"""Row actions and the panels outside the list.

The new-session and viewer paths (`present_session`, `new_session`,
`detach_panel`), the `:` command line and prompt editing, and every row action
(`x` done, `m` move, `D` delete, `R` rename) live here. Each one returns a
footer message plus the row key to land on, so the app layer only shows text.
"""
from __future__ import annotations

import curses
import unicodedata
import uuid
from dataclasses import replace

from panel import core, goals as _goals, panes as _panes, procs as _procs
from panel import parsers as _parsers, rows as _rows
from panel import state as _state, status as _status, tmuxctl as _tmux
from panel import trash as _trash
from panel.core import Group, Goal, Session


def slice_parent_for_row(row: tuple | None) -> str:
    """The top-level goal `:slice:create` should file a new slice under."""
    if not row:
        return ""
    kind, g, item = row
    if kind == "goal":
        return item.id
    if kind == "slice":
        return item.parent
    if kind == "session":
        goal = _goals.goal_of_session(g, item.id)
        if goal is None:
            return ""
        return goal.parent if _goals.goal_is_slice(g, goal) else goal.id
    return ""


def run_command(
    text: str,
    cwd: str | None,
    current: Goal | None = None,
    current_kind: str = "goal",
    slice_parent: str = "",
    session: Session | None = None,
) -> tuple[str, Goal | Session | None]:
    """Run a `:` command line; returns (footer message, node to select).

    *current* is the highlighted goal or slice; *current_kind* says which, so
    `goal:rename` and `slice:rename` only touch the matching node (A5).
    *slice_parent* lets `:slice:create` target the container of a session row;
    *session* is the highlighted session for `session:rename`.
    """
    parts = text.strip().lstrip(":").split(None, 1)
    if not parts:
        return "", None
    command, arg = parts[0], parts[1] if len(parts) > 1 else ""

    def fail(e: Exception) -> tuple[str, None]:
        return _state._store_error_message(_state.GOALS_STORE, e), None

    if command == "session:rename":
        if session is None:
            return "highlight a session to rename it", None
        try:
            name = _parsers.write_title(session.path, session.harness, arg)
        except _parsers.TitleError as e:
            return str(e), None
        return f"renamed session to '{name}'", session
    if command in ("goal:rename", "slice:rename"):
        noun = command.split(":", 1)[0]
        if cwd is None or current is None or current_kind != noun:
            return f"highlight a {noun} to rename it", None
        try:
            name = _goals.rename_goal(cwd, current.id, arg, noun)
        except (_state.GoalError, OSError) as e:
            return fail(e)
        return f"renamed {noun} to '{name}'", replace(current, name=name)
    if command == "slice:create":
        parent = slice_parent
        if not parent and current is not None:
            parent = current.parent if current_kind == "slice" else current.id
        if cwd is None or not parent:
            return "highlight a goal to create a slice in", None
        try:
            node = _goals.create_slice(cwd, parent, arg)
        except (_state.GoalError, OSError) as e:
            return fail(e)
        return f"created slice '{node.name}'", node
    if command != "goal:create":
        return f"unknown command: {command}", None
    if cwd is None:
        return "no project to put a goal in", None
    try:
        goal = _goals.create_goal(cwd, arg)
    except (_state.GoalError, OSError) as e:
        return fail(e)
    return f"created goal '{goal.name}'", goal


def _present_pane(
    pane_id: str,
    viewer: str,
    reap_state: dict[str, float],
) -> str | None:
    """Bring a live hidden pane into the viewer by swapping, never respawning."""
    if not _tmux.swap_panes(pane_id, viewer):
        return "Could not swap the session pane into the viewer"
    panel_id = _tmux.panel_pane_id()
    window_id = _tmux.panel_window_id(panel_id)
    after = _tmux.layout_panes(window_id)
    # Start the normal reap grace for the displaced pane. The file may still
    # describe the previous turn, so never kill it just because it looks done.
    _panes.reap_hidden(after, _status.load_groups(), _procs.process_table(), reap_state)
    _tmux.focus(pane_id)
    return None


def present_session(
    s: Session,
    groups: list[Group] | None = None,
    preferred: str = "",
    reap_state: dict[str, float] | None = None,
) -> str | None:
    """Show a session in the viewer, keeping the displaced process alive."""
    groups = _status.load_groups() if groups is None else groups
    reap_state = {} if reap_state is None else reap_state
    procs = _procs.process_table()
    panel_id, panes, viewer = _panes.visible_layout(groups, preferred, procs)
    if not panel_id or not panes:
        return "No tmux layout found for the control panel"
    if not viewer:
        return "No session viewer pane found"

    panes_by_id = {p.id: p for p in panes}
    # A session can have more than one tagged pane if a duplicate was spawned by
    # an older panel. Prefer the pane already showing it so Enter only focuses
    # the live session instead of swapping a duplicate into the viewer.
    claimed = [p for p in panes if p.tag == s.id and p.id != panel_id]
    if claimed:
        target_id = next((p.id for p in claimed if p.id == viewer), "")
        if not target_id:
            target_id = next(
                (p.id for p in claimed if p.session_name != core.BG_SESSION),
                claimed[0].id,
            )
    else:
        target_id = ""
    if not target_id:
        pane = _panes.find_managed_pane(s.id, panes, procs, panel_id)
        if pane:
            target_id = pane.id
            _tmux.set_pane_tag(pane.id, s.id)

    if target_id:
        target_pane = panes_by_id[target_id]
        if not _panes.pane_is_live(target_pane, procs):
            # The tagged pane is only a leftover shell. Replace it rather than
            # presenting a dead pane (hidden ones are normally reaped first).
            if target_id != viewer:
                _tmux.kill_pane(target_id)
            target_id = None

    if target_id == viewer:
        _tmux.set_pane_tag(viewer, s.id)
        _state._write_state(s.id)
        _tmux.focus(viewer)
        core.clear_attention(s.id)
        return None

    if not target_id and _panes.external_session_running(
        s.id, panes, procs, _procs.running_session_processes(procs)
    ):
        return f"{s.title[:36]} is already running outside sassy-code"

    if target_id:
        warning = _present_pane(target_id, viewer, reap_state)
    else:
        new_pane = _panes.spawn_session_pane(s)
        if not new_pane:
            return "Could not start the session in the background"
        warning = _present_pane(new_pane, viewer, reap_state)
        if warning:
            _tmux.kill_pane(new_pane)
            return warning

    if not warning:
        _state._write_state(s.id)
        core.clear_attention(s.id)
    return warning


def new_session(
    target: str,
    cwd: str,
    groups: list[Group] | None = None,
    reap_state: dict[str, float] | None = None,
    harness: str | None = None,
    goal_id: str | None = None,
) -> str | None:
    """Start a fresh agent in the viewer via the background swap path.

    With *goal_id* the new id is filed in that goal before spawning. If the
    goal store refuses, the session still starts loose and the reason is
    returned as the footer warning.
    """
    groups = _status.load_groups() if groups is None else groups
    reap_state = {} if reap_state is None else reap_state
    harness = harness if harness in core.HARNESS else _panes.default_harness()
    panel_id, panes, viewer = _panes.visible_layout(preferred=target)
    if not panel_id or not panes:
        return "No tmux layout found for the control panel"
    if not viewer:
        return "No session viewer pane found"
    session_id = str(uuid.uuid4())
    goal_warning = None
    if goal_id:
        outside = "; started outside the goal"
        try:
            _goals.file_session_in_goal(cwd, goal_id, session_id)
        except _state.GoalError as e:
            goal_warning = f"{e}{outside}"
        except OSError as e:
            goal_warning = f"could not save goal: {e.strerror or e}{outside}"
    new_pane = _panes.spawn_new_session_pane(cwd, session_id, harness)
    if not new_pane:
        return "Could not start a new session"
    warning = _present_pane(new_pane, viewer, reap_state)
    if warning:
        _tmux.kill_pane(new_pane)
        return warning
    _state._write_state(session_id)
    return goal_warning


DETACH_FAILED = " Could not detach from tmux "


def detach_panel() -> str:
    """Detach every client attached to the panel's own tmux session.

    The one quit path. Nothing is killed or moved: the session, panel, viewer
    and agents keep running and `sassy-code` reattaches. Only the session the
    panel lives in is touched. Returns "" on success or when there is nothing
    to detach (no client attached), else a footer message (also when the
    panel's pane or tmux could not be queried). Callers handle "outside tmux"
    themselves, from $TMUX.
    """
    pane = _tmux.panel_pane_id()
    if not pane:
        return DETACH_FAILED
    res = _tmux.tmux("display-message", "-p", "-t", pane, "#{session_name}")
    if not _tmux.tmux_ok(res):
        return DETACH_FAILED
    if not res.stdout.strip():
        return ""
    target = f"={res.stdout.strip().splitlines()[0]}"
    res = _tmux.tmux("list-clients", "-t", target, "-F", "#{client_name}")
    if not _tmux.tmux_ok(res):
        return DETACH_FAILED
    if not res.stdout.strip():
        return ""
    if _tmux.tmux_ok(_tmux.tmux("detach-client", "-s", target)):
        return ""
    return DETACH_FAILED


def apply_edit_key(text: str, ch) -> tuple[str, str | None]:
    """Apply one key to a prompt line; returns (text, "submit"/"cancel"/None)."""
    if ch in (curses.KEY_ENTER, 10, 13, "\n", "\r"):
        return text, "submit"
    if ch in (27, "\x1b"):
        return text, "cancel"
    if ch in (curses.KEY_BACKSPACE, 127, 8, "\x7f", "\b"):
        return text[:-1], None
    if ch in (21, "\x15"):
        return "", None
    # Cf (ZWJ, variation selectors) keeps emoji sequences intact.
    if isinstance(ch, str) and (
        ch.isprintable() or unicodedata.category(ch) == "Cf"
    ):
        return text + ch, None
    return text, None


def tail_by_width(text: str, room: int) -> str:
    """The right end of *text* that fits in *room* display columns."""
    shown = text
    while shown and core.display_width(shown) > room:
        shown = shown[1:]
    return shown


def prompt_line(stdscr, label: str, initial: str = ""):
    """Edit one line at the bottom; returns the text, or None on Esc."""
    curses.curs_set(1)
    text = initial
    try:
        while True:
            h, w = stdscr.getmaxyx()  # re-read: the terminal may resize
            line = f" {label}"
            room = max(1, w - 2 - core.display_width(line))
            shown = tail_by_width(text, room)
            try:
                stdscr.addnstr(h - 1, 0, core.fit_cols(line + shown, w - 1), w - 1,
                               curses.A_REVERSE)
                stdscr.move(h - 1, min(w - 2, core.display_width(line + shown)))
                stdscr.refresh()
            except curses.error:
                pass  # a mid-resize frame must never kill the panel
            try:
                ch = stdscr.get_wch()
            except (curses.error, KeyboardInterrupt) as e:
                if isinstance(e, KeyboardInterrupt):
                    return None
                continue  # timeout tick
            if ch == curses.KEY_RESIZE:
                continue
            text, action = apply_edit_key(text, ch)
            if action == "submit":
                return text
            if action == "cancel":
                return None
    finally:
        curses.curs_set(0)


def apply_command(
    typed: str, row: tuple | None, groups: list[Group], collapsed: set[str]
) -> tuple[str, tuple | None]:
    """Run a `:` line against the highlighted row's project.

    Returns (footer message, row key to select). A created goal un-collapses
    its project and reloads the goals so the cursor can land on it.
    """
    cwd = row[1].cwd if row else None
    current_kind = row[0] if row and row[0] in ("goal", "slice") else "goal"
    current = row[2] if row and row[0] in ("goal", "slice") else None
    session = row[2] if row and row[0] == "session" else None
    text, node = run_command(
        typed, cwd, current, current_kind, slice_parent_for_row(row), session
    )
    message = f" {text} " if text else ""
    if isinstance(node, Session):
        # A renamed transcript keeps its row; nothing in goals.json moved.
        return message, ("session", node.id, node.cwd)
    if node is None:
        return message, None
    collapsed.discard(cwd)
    _goals.attach_goals(groups)
    if row and row[0] in ("goal", "slice") and row[2].id == node.id:
        kind = row[0]
    else:
        kind = "slice" if node.parent else "goal"
    return message, (kind, node.id, cwd)


NO_GOAL_ROW = " highlight a goal or slice first "
NO_SESSION_ROW = " highlight a session first "


def goals_guard() -> str:
    """A footer warning when goals.json is unreadable, else ""."""
    return _state.store_guard(_state.GOALS_STORE)


def _goal_row_refusal(row: tuple | None) -> str:
    """Why a goal/slice-only action on *row* is refused, else ""."""
    if not row or row[0] not in ("goal", "slice"):
        return NO_GOAL_ROW
    return goals_guard()


def row_noun(row: tuple) -> str:
    """`slice` for a slice row, else `goal` (D16)."""
    return "slice" if row[0] == "slice" else "goal"


def rename_prefill(row: tuple | None) -> tuple[str, str]:
    """(`:` line text, footer message) for `R`; empty text means refused.

    No rename carries the current name over: a session, goal or slice row all
    open on an empty argument, so the user types a fresh one (D26).
    """
    if row and row[0] == "session":
        return "session:rename ", ""
    refusal = _goal_row_refusal(row)
    if refusal:
        return "", refusal
    return f"{row_noun(row)}:rename ", ""


def done_goal(groups: list[Group], row: tuple | None) -> tuple[str, tuple | None]:
    """`x`: toggle the highlighted goal or slice done; (message, key to select)."""
    if not row or row[0] not in ("goal", "slice"):
        return NO_GOAL_ROW, None
    _, g, goal = row
    try:
        done = _goals.toggle_goal_done(g.cwd, goal.id)
    except (_state.GoalError, OSError) as e:
        return f" {_state._store_error_message(_state.GOALS_STORE, e)} ", None
    _goals.attach_goals(groups)
    state = "done" if done else "reopened"
    return f" {state}: '{goal.name}' ", (row[0], goal.id, g.cwd)


def done_session(row: tuple | None) -> tuple[str, tuple | None]:
    """`x` on a session: toggle its done mark; (message, key to select).

    The caller reloads groups, which recomputes the status from the store and
    the live processes, so this only writes the mark.
    """
    if not row or row[0] != "session":
        return NO_SESSION_ROW, None
    _, g, s = row
    try:
        done = _state.toggle_session_done(s)
    except (_state.SessionError, OSError) as e:
        return f" {_state._store_error_message(_state._SESSIONS_STORE, e)} ", None
    state = "done" if done else "reopened"
    return f" {state}: '{s.title}' ", ("session", s.id, g.cwd)


def move_session_row(
    groups: list[Group], row: tuple | None, target: tuple[str, Goal | None]
) -> tuple[str, tuple | None]:
    """`m` on a session: move it to *target*; (message, key to select).

    Only session rows move. The backend re-reads and writes goals.json once;
    a corrupt store refuses before anything changes.
    """
    if not row or row[0] != "session":
        return NO_SESSION_ROW, None
    _, g, s = row
    kind, goal = target
    target_id = goal.id if kind == core.MOVE_GOAL and goal is not None else None
    try:
        changed = _goals.move_session(g.cwd, s.id, target_id)
    except (_state.GoalError, OSError) as e:
        return f" {_state._store_error_message(_state.GOALS_STORE, e)} ", None
    _goals.attach_goals(groups)
    key = ("session", s.id, g.cwd)
    if not changed:
        return f" already there: '{s.title}' ", key
    where = f"'{goal.name}'" if target_id and goal is not None else "loose"
    return f" moved '{s.title}' to {where} ", key


def move_to_new_goal(
    groups: list[Group], row: tuple | None, name: str
) -> tuple[str, tuple | None]:
    """`+ New goal`: create a top-level goal and move the session into it."""
    if not row or row[0] != "session":
        return NO_SESSION_ROW, None
    _, g, s = row
    try:
        goal = _goals.move_session_to_new_goal(g.cwd, s.id, name)
    except (_state.GoalError, OSError) as e:
        return f" {_state._store_error_message(_state.GOALS_STORE, e)} ", None
    _goals.attach_goals(groups)
    return (
        f" moved '{s.title}' to new goal '{goal.name}' ",
        ("session", s.id, g.cwd),
    )


def delete_prompt(row: tuple | None) -> tuple[str, str]:
    """(confirm text, refusal message) for `D`; empty confirm means refused.

    A cascade prompt names how many sessions the goal or slice takes with it;
    at zero the clause is dropped. An unreadable store refuses before the
    prompt, because the transaction could not preserve the filing or marks.
    """
    if row and row[0] == "session":
        refusal = (
            goals_guard()
            or _state.sessions_guard()
            or _state.trash_guard()
        )
        if refusal:
            return "", refusal
        return f" Delete session '{row[2].title}'? y/n ", ""
    refusal = _goal_row_refusal(row)
    if refusal:
        return "", refusal
    unreadable = _state.sessions_guard() or _state.trash_guard()
    if unreadable:
        return "", unreadable
    _, g, goal = row
    members = _goals.goal_subtree_members(g, goal)
    noun = row_noun(row)
    if members:
        count = len(members)
        plural = "" if count == 1 else "s"
        return (
            f" Delete {noun} '{goal.name}' and {count} session{plural}? y/n ",
            "",
        )
    return f" Delete {noun} '{goal.name}'? y/n ", ""


def delete_session_row(
    groups: list[Group],
    row: tuple | None,
    presented: str = "",
    reap_state: dict[str, float] | None = None,
    viewer: str = "",
    rows: list | None = None,
) -> tuple[str, tuple | None]:
    """`D` on a session: preflight, stop, move to trash; (message, key).

    The preflight lives in `panel.trash`, so the same rule protects the P2
    cascade. A refusal names the reason and moves nothing. The follow-up key
    is the neighbour among the rows as drawn, so a filter is honored.
    """
    if not row or row[0] != "session":
        return NO_SESSION_ROW, None
    _, g, s = row
    ok, text = _trash.delete_session(
        s, g, presented=presented, viewer=viewer, reap_state=reap_state
    )
    if not ok:
        return text, None
    _goals.attach_goals(groups)
    return text, _rows.neighbour_after_delete(rows or [], row)


def delete_goal_row(
    groups: list[Group],
    row: tuple,
    presented: str = "",
    reap_state: dict[str, float] | None = None,
    viewer: str = "",
    panes: list | None = None,
    procs: list | None = None,
) -> tuple[str, tuple | None]:
    """Delete the highlighted goal or slice and its whole subtree (P2).

    Every member session goes to the trash in one transaction and the
    container records are removed after; a refused or failed transaction
    leaves the list unchanged. Selects the parent goal for a slice, else the
    project heading, because the members are gone.
    """
    _, g, goal = row
    parent_id = goal.parent if row[0] == "slice" else ""
    ok, text = _trash.delete_subtree(
        g, goal, presented=presented, viewer=viewer, reap_state=reap_state,
        panes=panes, procs=procs,
    )
    if not ok:
        return text, None
    _goals.attach_goals(groups)
    parent = (
        next((x for x in g.goals if x.id == parent_id), None)
        if parent_id else None
    )
    if parent is not None:
        kind = "slice" if _goals.goal_is_slice(g, parent) else "goal"
        key = (kind, parent.id, g.cwd)
    else:
        key = ("group", g.cwd)
    return text, key


def toggle_goal(groups: list[Group], g: Group, goal: Goal, value: bool) -> str:
    """Persist a goal's collapse state; returns an error message or "".

    Reloads the goals either way so rows reflect the file.
    """
    error = ""
    try:
        _goals.set_goal_collapsed(g.cwd, goal.id, value)
    except (_state.GoalError, OSError) as e:
        error = f" {e} "
    _goals.attach_goals(groups)
    return error
