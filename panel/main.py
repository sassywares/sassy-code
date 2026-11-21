"""Sassy Code session control panel.

Left pane: grouped list of all Pi and Claude Code sessions with status.
Right pane: the session currently selected, presented by swapping tmux panes.

Sessions that are switched away from stay alive in a dedicated background tmux
session (`sassy-code-bg`), one hidden single-pane window per live session.  The
panel never respawns a managed pane, so no in-flight agent process is ever
killed by switching.  This process runs in the left tmux pane and never blocks
on the viewer.
"""
from __future__ import annotations

import argparse
import curses
import os
import sys
import time

from panel import actions as _actions, boxes as _boxes, core, goals as _goals
from panel import mouse as _mouse, panes as _panes, rows as _rows
from panel import state as _state, status as _status, tmuxctl as _tmux
from panel import view as _view
from panel.core import Group, Goal, Session


class App:
    """The curses panel: its state, the event loop and one key's work.

    `setup()` builds the terminal and loads the first snapshot; `run()` is the
    loop, which `draw()`s once per pass, `refresh()`es on an idle tick and hands
    every key to `dispatch_key()`; `teardown()` leaves the terminal usable.
    """

    def __init__(self, stdscr, target: str):
        self.stdscr = stdscr
        self.target = target
        self.groups: list[Group] = []
        self.pane = ""
        self.flt = ""
        self.cursor = 0
        self.offset = 0
        self.selected = None  # row_key() of the highlighted row
        self.presented = ""  # session the viewer was last asked to show
        self.collapsed: set[str] = set()
        self.expanded: dict[str, int] = {}
        self.reap_state: dict[str, float] = {}
        self.message = ""
        self.message_until = 0.0
        self.drag = None  # session row held down for drag-to-move
        self.drag_since = 0.0  # when that press arrived, for the tick expiry
        self.wheel_last = None  # (direction, time) of the previous wheel event
        self.last_click_key = None
        self.last_click_time = 0.0
        self.rows: list[tuple] = []

    def setup(self) -> None:
        """Enter the curses screen mode and load the first snapshot."""
        stdscr = self.stdscr
        if hasattr(curses, "set_escdelay"):
            curses.set_escdelay(25)  # Esc cancels at once, not after ~1s
        curses.curs_set(0)
        curses.start_color()
        curses.use_default_colors()
        # A true green (256-colour palette) keeps in-progress distinct from the
        # yellow needs-input mark; basic terminals fall back to COLOR_GREEN.
        green = (
            46 if getattr(curses, "COLORS", 0) >= 256 else curses.COLOR_GREEN
        )
        try:
            curses.init_pair(1, green, -1)            # in progress
        except curses.error:
            curses.init_pair(1, curses.COLOR_GREEN, -1)
        curses.init_pair(2, curses.COLOR_RED, -1)     # error
        curses.init_pair(3, curses.COLOR_YELLOW, -1)  # needs input
        curses.init_pair(4, curses.COLOR_BLUE, -1)    # idle
        curses.init_pair(5, curses.COLOR_CYAN, -1)    # group
        curses.init_pair(6, curses.COLOR_WHITE, -1)   # done (drawn dim)
        stdscr.timeout(2000)

        self.groups = _status.load_groups()
        self.pane = _tmux.panel_pane_id()
        _tmux.set_panel_tag(self.pane)
        _state.migrate_state()
        self.presented = _state._read_state()
        if not self.presented:
            # No record yet: take the session already in the viewer as presented.
            _, panes, viewer = _panes.visible_layout(self.groups, self.target)
            self.presented = next((p.tag for p in panes if p.id == viewer), "")

    def teardown(self) -> None:
        """Restore the cursor; the mouse mask is the wrapper's to restore."""
        curses.curs_set(0)

    def run(self) -> None:
        """Run the panel until a quit key says stop (or Ctrl-C outside tmux)."""
        self.setup()
        try:
            while True:
                try:
                    hits = self.draw()
                    try:
                        ch = self.stdscr.getch()
                    except KeyboardInterrupt:
                        if self.quit_keys():
                            return
                        continue
                    if ch == -1:
                        self.refresh()
                        continue
                    if not self.dispatch_key(ch, hits):
                        return
                except KeyboardInterrupt:
                    if self.quit_keys():
                        return
        finally:
            self.teardown()

    def draw(self) -> dict:
        """Paint the list once; return the drawn screen-line -> row map."""
        if self.message and time.time() >= self.message_until:
            self.message = ""
        # Re-derive the cursor from the selected row's identity, not its old
        # index: a session that starts working moves to the top of the list.
        unreadable = _goals.attach_goals(self.groups)
        rows = list(
            _rows.rows_for(self.groups, self.flt, self.collapsed, self.expanded)
        )
        self.cursor = _mouse.cursor_for_key(rows, self.selected)
        footer = (
            f" moving {self.drag[2].title}… " if self.drag is not None
            else self.message
        )
        result = _view.draw(
            self.stdscr, self.groups, self.flt, self.cursor, self.offset,
            self.collapsed, footer, self.expanded,
            _state.GOALS_UNREADABLE if unreadable else _state.sessions_guard(),
        )
        hits = {}
        if isinstance(result, tuple) and len(result) >= 3:
            self.cursor, self.offset, hits = result[:3]
        else:  # a test double may return the older (cursor, offset)
            self.cursor, self.offset = result[:2]
        self.rows = rows
        return hits

    def refresh(self) -> None:
        """The idle tick: expire a stale drag, reload groups and hidden panes."""
        if self.drag is not None and _mouse.drag_expired(
            self.drag_since, time.time()
        ):
            self.drag = None  # only a press older than the limit expires
        self.groups = _status.load_groups()
        panes, viewer = _panes.refresh_hidden(
            self.groups, self.reap_state, self.target
        )
        # The highlight is whatever row the cursor is on, even before the
        # first key press; `selected` is only filled in below a key event.
        highlight = (
            _rows.row_key(self.rows[self.cursor])
            if self.rows and 0 <= self.cursor < len(self.rows)
            else self.selected
        )
        before = self.presented
        highlight, self.presented = _view.follow_viewer_switch(
            highlight, self.presented, panes, self.groups, viewer
        )
        self.selected = highlight
        if self.presented != before:
            _state._write_state(self.presented)

    def say(self, text: str, key=None) -> None:
        """Show *text* in the footer for a few seconds; select *key* if given."""
        if text:
            self.message = text
            self.message_until = time.time() + 4
        if key is not None:
            self.selected = key

    def present(self, row) -> None:
        """Present *row* and report it; shared by Enter and a double-click."""
        warning = _actions.present_session(
            row, self.groups, self.target, self.reap_state
        )
        if warning:
            self.message = f" {warning} "
            self.message_until = time.time() + 4
        else:
            self.message = ""
            self.presented = row.id
        self.groups = _status.load_groups()

    def file_move(self, row, result) -> None:
        """Show a move's footer message, un-collapse its project, reload."""
        self.say(*result)
        self.collapsed.discard(row[1].cwd)
        self.groups = _status.load_groups()

    def command_line(self, row, initial: str):
        """Prompt for a `:` line, run it, report it; returns the new key."""
        typed = _actions.prompt_line(self.stdscr, ":", initial)
        if typed is None:
            return None
        text, key = _actions.apply_command(
            typed, row, self.groups, self.collapsed
        )
        self.say(text, key)
        return key

    def quit_keys(self) -> bool:
        """Every quit key lands here: detach, and the panel keeps running.

        Returns True when the loop should end: outside tmux there is no
        client to detach, so q and Ctrl-C exit the panel instead. Further
        Ctrl-C taps while detaching are absorbed.
        """
        if not os.environ.get("TMUX"):
            return True
        for _ in range(3):
            try:
                failure = _actions.detach_panel()
            except KeyboardInterrupt:
                continue
            if failure:
                self.message = failure
                self.message_until = time.time() + 10
            break
        return False

    def fold_goal(self, g, goal, value) -> None:
        """Persist a container's fold, reporting any store error."""
        error = _actions.toggle_goal(self.groups, g, goal, value)
        if error:
            self.message = error
            self.message_until = time.time() + 4

    def set_tier(self, row, action: str):
        """`[` / `]` / Enter / click on *row*: one level, or flip a container.

        *action* is "show" (`]`: expand a collapsed container, else reveal one
        tier), "hide" (`[`: hide one tier, or collapse at the base tier), or
        "toggle" (Enter/click: flip a container, and on a `more` row reveal
        `… N more` or hide `… show fewer`). A session row acts on the container
        that holds it. Returns the row key to land on, or None when the row had
        nothing to show or hide.
        """
        kind, g, s = row
        if self.flt and action in ("show", "hide"):
            return None  # a filter ignores limits, so the keys own no state
        on_more = kind in ("more", "goal_more", "slice_more")
        # The container a row acts on, and its own row key. A `more` row acts
        # on the container it belongs to.
        if kind in ("group", "more"):
            key, container_row = g.cwd, ("group", g.cwd)
            container, folded = None, g.cwd in self.collapsed
        elif kind in ("goal", "slice"):
            key, container_row = s.id, (kind, s.id, g.cwd)
            container, folded = s, s.collapsed
        elif on_more:
            container = s[0]
            kind = _goals.goal_kind(g, container)
            key, container_row = container.id, (kind, container.id, g.cwd)
            folded = container.collapsed
        else:  # a session row acts on the container holding it
            goal = _goals.goal_of_session(g, s.id)
            if goal is None:
                key, container_row = g.cwd, ("group", g.cwd)
                container, folded = None, g.cwd in self.collapsed
            else:
                kind = _goals.goal_kind(g, goal)
                key, container_row = goal.id, (kind, goal.id, g.cwd)
                container, folded = goal, goal.collapsed

        def set_fold(value: bool) -> None:
            if container is None:
                (self.collapsed.add if value else self.collapsed.discard)(key)
            else:
                self.fold_goal(g, container, value)

        if on_more:
            if action == "toggle":
                action = "hide" if s[-1] else "show"
            if action == "show":
                return _rows.tier_reveal(
                    self.rows, self.groups, self.flt, self.collapsed,
                    self.expanded, key, _rows.row_key(row),
                )
            if folded:
                return None  # already collapsed
            if _rows.tier_level(self.expanded, key) == 0:
                set_fold(True)  # the base tier's `[` collapses the container
                return container_row
            return _rows.tier_hide(
                self.groups, self.flt, self.collapsed, self.expanded, key,
                container_row,
            )
        if action == "toggle":
            if folded:
                self.expanded.pop(key, None)  # expanding always shows the base
            set_fold(not folded)
            return container_row
        if action == "show":
            if folded:
                self.expanded.pop(key, None)  # expanding shows the base tier
                set_fold(False)
                return container_row
            return _rows.tier_reveal(
                self.rows, self.groups, self.flt, self.collapsed,
                self.expanded, key, container_row,
            )
        if folded:
            return None  # already collapsed
        if _rows.tier_level(self.expanded, key) > 0:
            return _rows.tier_hide(
                self.groups, self.flt, self.collapsed, self.expanded, key,
                container_row,
            )
        set_fold(True)  # at the base tier, `[` collapses
        return container_row

    def dispatch_key(self, ch, hits: dict) -> bool:
        """Apply one key (or mouse event); return False when the loop ends.

        `run()` draws before every dispatch, so `self.rows` is already the
        current row list.
        """
        stdscr = self.stdscr
        rows = self.rows
        row = _view.highlighted_row(rows, self.cursor)

        if ch == core.MOUSE_KEY:
            try:
                _id, _mx, my, _z, bstate = curses.getmouse()
            except (curses.error, AttributeError):
                return True
            # Mouse traffic is a clock too: motion keeps arriving while a
            # drag is held, so the age bound must not wait for an idle tick.
            if self.drag is not None and _mouse.drag_expired(
                self.drag_since, time.time()
            ):
                self.drag = None
            kind = _mouse.mouse_kind(bstate)
            _mouse.mouse_debug(bstate, _mx, my, kind)
            if kind in ("wheel_up", "wheel_down"):
                fresh, self.wheel_last = _mouse.wheel_notch(
                    kind, time.time(), self.wheel_last
                )
                if not fresh:
                    return True
                self.cursor = _mouse.wheel_cursor(len(rows), self.cursor, kind)
                if rows:
                    self.selected = _rows.row_key(rows[self.cursor])
                return True
            if kind == "press":
                held, self.drag = self.drag, _mouse.drag_press(
                    self.drag, hits, my
                )
                if held is None and self.drag is not None:
                    self.drag_since = time.time()
                return True
            if kind in ("release", "click"):
                held, self.drag = self.drag, None
                outcome = _mouse.mouse_release(
                    kind, held, _view.hit_test(hits, my)
                )
                if outcome is None:
                    return True
                if outcome[0] == "move":
                    self.file_move(
                        held,
                        _actions.move_session_row(
                            self.groups, held, outcome[1]
                        ),
                    )
                    return True
                hit = outcome[1]
                now = time.time()
                action = _mouse.click_kind(
                    hit, now, self.last_click_key, self.last_click_time
                )
                self.last_click_key = _rows.row_key(hit)
                self.last_click_time = now
                if action == "present":
                    self.present(hit[2])
                    self.last_click_key = None
                elif action == "toggle":
                    self.selected = self.set_tier(hit, "toggle") or self.selected
                elif action == "tier":
                    self.selected = (
                        self.set_tier(hit, _mouse.more_click_action(hit))
                        or self.selected
                    )
                else:
                    self.selected = _rows.row_key(hit)
                return True
            # Motion, a zero bstate or button 2/3: never touch a held drag.
            return True
        self.drag = None  # any non-mouse key ends a held drag
        if ch in (curses.KEY_UP, ord("k")):
            self.cursor = max(0, self.cursor - 1)
        elif ch in (curses.KEY_DOWN, ord("j")):
            self.cursor = min(len(rows) - 1, self.cursor + 1)
        elif ch in (ord("g"), curses.KEY_HOME):
            self.cursor = 0
        elif ch in (ord("G"), curses.KEY_END):
            self.cursor = max(0, len(rows) - 1)
        elif ch == ord("q"):
            if self.quit_keys():
                return False
            return True
        elif ch == ord("r"):
            self.groups = _status.load_groups()
            _panes.refresh_hidden(self.groups, self.reap_state, self.target)
            count = _panes.reload_managed_panes(self.groups, self.target)
            if count:
                noun = "session" if count == 1 else "sessions"
                self.say(f" Reloading {count} {noun} ")
            else:
                self.say(" No live sessions to reload ")
            return True
        elif ch == ord("/"):
            typed = _actions.prompt_line(stdscr, "filter: ", "")
            if typed is not None:
                self.flt = typed
                self.cursor = 0
                self.selected = None
            return True
        elif ch == ord(":"):
            if self.command_line(row, "") is not None:
                # Land on the new goal: show its project and drop the filter.
                self.flt = ""
            return True
        elif ch == ord("R"):
            text, warning = _actions.rename_prefill(row)
            if not text:
                self.say(warning)
                return True
            self.command_line(row, text)
            return True
        elif ch == ord("m"):
            if not row or row[0] != "session":
                self.say(_actions.NO_SESSION_ROW)
                return True
            self.drag = None  # the box owns the pointer; a held drag is stale
            # The pre-split loop rebound its pane target to the box's pick
            # here; the App keeps `self.target` intact for later refresh,
            # present and new-session calls. Do not restore the old rebind.
            try:
                picked = _boxes.move_box(stdscr, row[1], row[2])
            except KeyboardInterrupt:
                if self.quit_keys():
                    return False
                return True
            if picked is None:
                return True
            if picked[0] == core.MOVE_NEW:
                name = _actions.prompt_line(stdscr, "goal name: ", "")
                if name is None:
                    return True
                self.file_move(
                    row, _actions.move_to_new_goal(self.groups, row, name)
                )
            else:
                self.file_move(
                    row, _actions.move_session_row(self.groups, row, picked)
                )
            return True
        elif ch == ord("x"):
            if row and row[0] == "session":
                self.say(*_actions.done_session(row))
                self.groups = _status.load_groups()
            else:
                self.say(*_actions.done_goal(self.groups, row))
            return True
        elif ch == ord("D"):
            ask, warning = _actions.delete_prompt(row)
            if not ask:
                self.say(warning)
                return True
            _view.draw(
                stdscr, self.groups, self.flt, self.cursor, self.offset,
                self.collapsed, ask, self.expanded,
            )
            try:
                answer = stdscr.getch()
                while answer == -1:  # a refresh tick is not an answer
                    answer = stdscr.getch()
            except KeyboardInterrupt:
                if self.quit_keys():
                    return False
                return True
            if answer != ord("y"):
                return True
            self.say(*_actions.delete_goal_row(self.groups, row))
            return True
        elif ch in (ord("n"), ord(core.NEW_NO_PICKER_KEY)):
            if row:
                harness, goal_id = _goals.new_session_plan(row)
                installed = (
                    _panes.installed_harnesses() if ch == ord("n") else []
                )
                notices = []
                if _goals.should_open_new_box(row, installed, ch):
                    self.drag = None  # the box owns the pointer; drag is stale
                    try:
                        picked = _boxes.new_box_flow(
                            stdscr, row, installed, harness, goal_id
                        )
                    except KeyboardInterrupt:
                        if self.quit_keys():
                            return False
                        return True
                    if picked is None:
                        return True
                    harness, goal_id, notices, created = picked
                    if not harness:
                        self.say(f" {notices[0] if notices else ''} ")
                        return True
                    if created:
                        # Like `:`, show the new goal/slice at once.
                        self.collapsed.discard(row[1].cwd)
                        self.flt = ""
                warning = _actions.new_session(
                    self.target, row[1].cwd, self.groups, self.reap_state,
                    harness, goal_id,
                )
                notices.extend(x for x in (warning,) if x)
                if notices:
                    self.message = f" {'; '.join(notices)} "
                    self.message_until = time.time() + 4
                self.groups = _status.load_groups()
                self.presented = _state._read_state()
                # Falls through: like the original, `n` lands on the row that
                # was highlighted when the key arrived.
        elif ch in (ord("["), curses.KEY_LEFT, ord("h")):
            if row:
                self.selected = self.set_tier(row, "hide") or self.selected
            return True
        elif ch in (ord("]"), curses.KEY_RIGHT, ord("l")):
            if row:
                self.selected = self.set_tier(row, "show") or self.selected
            return True
        elif ch in (curses.KEY_ENTER, 10, 13):
            if row:
                kind, g, s = row
                if kind != "session":
                    self.selected = self.set_tier(row, "toggle") or self.selected
                    return True
                self.present(s)
        # Remember identity of the newly highlighted row for the next sort.
        if rows and 0 <= self.cursor < len(rows):
            self.selected = _rows.row_key(rows[self.cursor])
        return True


def main_curses(stdscr, target: str):
    """Run the panel with mouse reporting, restoring the terminal afterwards.

    A terminal without mouse support (or a curses build without it) simply
    gets no mouse events; the keyboard path is unaffected.
    """
    old_mask = None
    try:
        _available, old_mask = curses.mousemask(curses.ALL_MOUSE_EVENTS)
    except (curses.error, AttributeError):
        old_mask = None
    try:
        _main_curses(stdscr, target)
    finally:
        if old_mask is not None:
            try:
                curses.mousemask(old_mask)
            except (curses.error, AttributeError):
                pass


def _main_curses(stdscr, target: str):
    """Drive one `App` to completion; the seam the curses wrapper runs."""
    App(stdscr, target).run()


def list_lines(groups: list[Group]) -> list[str]:
    """Plain-text dump: projects, goals, slices nested, then sessions."""

    def row(s: Session, indent: str) -> str:
        return (f"{indent}{core.STATUS[s.status][0]} [{s.status:11}] "
                f"[{s.harness:6}] "
                f"{s.title[:60]:<60} {core.rel_time(s.last_ts):>4}  {s.id}")

    def node_lines(
        g: Group,
        goal: Goal,
        indent: str,
        children: dict[str, list[Goal]],
    ) -> list[str]:
        out = []
        for _, kind, item, _ in _rows.goal_child_items(g, goal, children):
            if kind == "session":
                out.append(row(item, indent))
                continue
            count = len(_goals.goal_subtree_members(g, item, children))
            mark = "✓" if item.done else "▾"
            out.append(f"{indent}{mark} {item.name}  ({count})")
            out.extend(node_lines(g, item, indent + "  ", children))
        return out

    labels = _rows.canonical_group_names(groups)
    out = []
    for g in groups:
        children, _ = _goals.goal_tree(g)
        out.append(labels.get(g.cwd, g.cwd))
        member_ids = _rows.goal_member_ids(g)
        goal_rows = [
            (
                root,
                _goals.goal_subtree_members(g, root, children),
                _rows.goal_age_ts(g, root, children=children),
            )
            for root in _goals.goal_roots(g, children)
        ]
        loose = [s for s in g.sessions if s.id not in member_ids]
        for _, kind, item, members in _rows.recency_top(loose, goal_rows):
            if kind == "session":
                out.append(row(item, "  "))
                continue
            count = len(members)
            mark = "✓" if item.done else "▾"
            out.append(f"  {mark} {item.name}  ({count})")
            out.extend(node_lines(g, item, "    ", children))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", required=False, help="tmux pane id to control")
    ap.add_argument("--list", action="store_true", help="print parsed list and exit")
    ap.add_argument(
        "--new-session",
        action="store_true",
        help="ask the panel to start a session in its selected project",
    )
    ap.add_argument(
        "--pane",
        default="",
        help="tmux pane whose window holds the control panel",
    )
    args = ap.parse_args()

    if args.new_session:
        pane = args.pane or os.environ.get("TMUX_PANE", "")
        if not pane:
            ap.error("--new-session requires --pane or TMUX_PANE")
        sys.exit(0 if _tmux.request_new_session(pane) else 1)

    if args.list:
        for line in list_lines(_status.load_groups()):
            print(line)
        return

    if not args.target:
        ap.error("--target is required unless --list is given")

    curses.wrapper(main_curses, args.target)


if __name__ == "__main__":
    main()
