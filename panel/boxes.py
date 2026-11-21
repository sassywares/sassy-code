"""The boxed pickers: one frame, the new-session box and the Move-to box.

`run_picker()` is the shared seam: one loop, one column model, one divider rule.
The new-session box draws a contextual Goal item above the harnesses; the
Move-to box draws `Top level`, `+ New goal` and the goal/slice tree. Ctrl-C
anywhere inside either box cancels it and never reaches the main loop, where it
would detach the client.
"""
from __future__ import annotations

import curses

from panel import actions as _actions, core, goals as _goals, mouse as _mouse
from panel import panes as _panes, state as _state
from panel.core import Goal, Group, Session


def picker_start(hint: str | None, installed: list[str]) -> int:
    """Index of the preselected harness: the inferred one, else the default."""
    if hint in installed:
        return installed.index(hint)
    default = _panes.default_harness()
    return installed.index(default) if default in installed else 0


def new_box_start(
    row: tuple | None,
    installed: list[str],
    hint: str | None = None,
    sections: list[str] | None = None,
) -> dict:
    """Initial state for the two-section box; focus starts on Harnesses."""
    sections = list(sections) if sections is not None else _goals.new_box_sections(row)
    focus = (
        sections.index(_goals.NEW_BOX_HARNESSES)
        if _goals.NEW_BOX_HARNESSES in sections
        else 0
    )
    state = {
        "sections": sections,
        "section": focus,
        "harness": picker_start(hint, installed),
    }
    if _goals.NEW_BOX_GOAL in sections:
        action = _goals.new_box_goal_action(row)
        state["goal_action"] = action
        state["goal_label"] = (
            _goals.NEW_BOX_SLICE_ITEM if action == "slice" else _goals.NEW_BOX_GOAL_ITEM
        )
    return state


def new_box_key(state: dict, installed: list[str], ch) -> tuple[dict, str]:
    """Apply one key to the box; returns (new state, action).

    Actions are "" (stay), "cancel", "harness" or "goal". Ctrl-C cancels the
    box like Esc/q when it arrives as the int key 3; with ISIG on, curses
    raises KeyboardInterrupt before a key is read, which `new_box` catches.
    `↑`/`↓` (and `k`/`j`) cross sections: `↑` off the first harness returns to
    the Goal item and `↓` off it lands on the first harness. `Tab` still
    switches sections; `←`/`→` do nothing here.
    """
    state = dict(state)
    sections = state["sections"]
    if not sections:
        return state, "cancel"
    section = sections[state["section"]]
    if ch in (3, 27, ord("q")):
        return state, "cancel"
    if ch == 9:  # Tab still switches sections; left/right are inert here
        if len(sections) > 1:
            state["section"] = (state["section"] + 1) % len(sections)
        return state, ""
    if ch in (curses.KEY_UP, ord("k")):
        if section == _goals.NEW_BOX_HARNESSES and state["harness"] > 0:
            state["harness"] -= 1
        elif _goals.NEW_BOX_GOAL in sections:
            # Up off the first harness returns to the Goal item.
            state["section"] = sections.index(_goals.NEW_BOX_GOAL)
        return state, ""
    if ch in (curses.KEY_DOWN, ord("j")):
        if section == _goals.NEW_BOX_GOAL:
            # Down off the Goal item lands on the first harness.
            state["section"] = sections.index(_goals.NEW_BOX_HARNESSES)
            state["harness"] = 0
        elif state["harness"] < max(0, len(installed) - 1):
            state["harness"] += 1
        return state, ""
    if ch in (curses.KEY_ENTER, 10, 13):
        if section == _goals.NEW_BOX_HARNESSES:
            return (state, "harness" if installed else "")
        return state, "goal"
    if isinstance(ch, int) and ord("1") <= ch <= ord("9"):
        n = ch - ord("1")
        if section == _goals.NEW_BOX_HARNESSES:
            if n < len(installed):
                state["harness"] = n
                return state, "harness"
        elif n == 0:
            return state, "goal"
    return state, ""


def new_box_lines(state: dict, installed: list[str]) -> list[tuple[str, str, int]]:
    """One line per item: (text, section, index), with no section headings.

    A `divider` line separates the contextual item from the harnesses. The
    Goal label is `+ <label>`: the ASCII marker plus one space puts it in the
    harness index column and its text in the harness glyph column, the same
    columns as a harness row. A harness-only box is just its harness lines.
    """
    lines: list[tuple[str, str, int]] = []
    for n, section in enumerate(state["sections"]):
        if n:
            lines.append(("", PICKER_DIVIDER, -1))  # one rule between
        if section == _goals.NEW_BOX_GOAL:
            # `+ ` is two columns wide, so the label starts in the harness
            # glyph column and the marker in the harness index column.
            lines.append((f" {state['goal_label']}", _goals.NEW_BOX_GOAL, 0))
            continue
        for n, key in enumerate(installed):
            info = core.HARNESS[key]
            lines.append(
                (f" {n + 1} {info['glyph']} {info['label']}",
                 _goals.NEW_BOX_HARNESSES, n)
            )
    return lines


def new_box_create(row: tuple, choice: str, name: str) -> tuple:
    """Create the Goal item's goal or slice: (node, footer text, store failed).

    A store refusal (corrupt or unwritable) is flagged by its kind so the
    caller still starts the session loose with the warning; a bad name is not,
    so the caller can abort the whole action. Never matches on message text.
    """
    parent = _actions.slice_parent_for_row(row) if choice == "slice" else ""
    if choice == "slice" and not parent:
        return None, "highlight a goal to create a slice in", False
    try:
        node = (
            _goals.create_slice(row[1].cwd, parent, name)
            if choice == "slice"
            else _goals.create_goal(row[1].cwd, name)
        )
    except OSError as e:
        return None, _state._store_error_message(_state.GOALS_STORE, e), True
    except _state.GoalError as e:
        return None, str(e), isinstance(e, _state.GoalStoreError)
    noun = "slice" if choice == "slice" else "goal"
    return node, f"created {noun} '{node.name}'", False


_DERIVE_CONTAINER = object()


def new_box_flow(
    stdscr,
    row: tuple,
    installed: list[str],
    hint: str | None = None,
    container=_DERIVE_CONTAINER,
) -> tuple[str, str | None, list[str], bool] | None:
    """Run the box to a decision; (harness, container id, notices, created).

    None means the user cancelled at any step. *container* is the caller's
    already-computed target; when omitted it is derived here. A harness item
    starts with that target; the Goal item then asks for a harness and a
    name, creates the goal or slice and targets the new node. An empty
    harness in the result means the name was refused: show the notice and
    start nothing. *created* is True only when this run created a node.
    """
    if container is _DERIVE_CONTAINER:
        _, container = _goals.new_session_plan(row)
    picked = new_box(stdscr, row, installed, hint)
    if picked is None:
        return None
    if picked["action"] == "harness":
        return picked["harness"], container, [], False
    choice = picked["action"]
    picked = new_box(
        stdscr, row, installed, hint, sections=[_goals.NEW_BOX_HARNESSES]
    )
    if picked is None:
        return None
    harness = picked["harness"]
    noun = "slice" if choice == "slice" else "goal"
    name = _actions.prompt_line(stdscr, f"{noun} name: ", "")
    if name is None:
        return None
    node, text, store_failed = new_box_create(row, choice, name)
    if node is not None:
        return harness, node.id, [text], True
    if store_failed:
        return harness, None, [f"{text}; started loose"], False
    return "", None, [text], False


def new_box_pick(installed: list[str], index: int) -> dict | None:
    """The box's harness result for *index*, or None when none installed."""
    if not installed:
        return None
    return {"action": "harness", "harness": installed[index]}


def new_box_mouse_pick(state: dict, index: int, installed: list[str]):
    """Resolve a click on drawn box line *index*; _PICKER_STAY keeps the box.

    The Goal item and each harness are pickable; the divider is not.
    """
    lines = new_box_lines(state, installed)
    if not 0 <= index < len(lines):
        return _PICKER_STAY
    _text, section, number = lines[index]
    if section == PICKER_DIVIDER:
        return _PICKER_STAY
    if section == _goals.NEW_BOX_GOAL:
        return {"action": state["goal_action"]}
    if section == _goals.NEW_BOX_HARNESSES:
        return new_box_pick(installed, number)
    return _PICKER_STAY  # a section heading is not an item


_PICKER_STAY = object()
PICKER_DIVIDER = "divider"


def divider_text(width: int) -> str:
    """A picker's horizontal rule, edge to edge across *width* columns.

    It overwrites the box's side borders so the rule visibly joins them.
    """
    if width < 2:
        return "─" * max(0, width)
    return "├" + "─" * (width - 2) + "┤"


def run_picker(stdscr, state, title: str, lines_for, step, fallback,
               mouse_pick=None):
    """Draw a one-line-per-item box until *step* ends it; return its result.

    *lines_for(state)* returns `(lines, selected)`, where each line is
    `(text, attribute)` or `(text, attribute, kind)` and *selected* is the
    index to keep centred; *step* applies one key and returns `(state,
    result)`, with `_PICKER_STAY` to keep looping or the value to return (None
    cancels). A `PICKER_DIVIDER` line is a real rule drawn edge to edge at the
    box origin; it takes no part in the width, is never selected and is not
    pickable. If the box cannot be drawn (tiny terminal, or curses raises),
    return *fallback* without acting on a choice nobody saw. Ctrl-C anywhere
    cancels like Esc/q, whether curses reports it as key 3 or raises
    KeyboardInterrupt (ISIG on). Only a left click (or a press and its release)
    picks; the wheel and other buttons are ignored, so scrolling over a box
    never acts on the item under it.
    """
    pressed = False  # an outstanding left press, wherever it landed
    try:
        while True:
            lines, selected = lines_for(state)
            if not lines:
                return fallback  # nothing to draw: cancel like a tiny box
            h, w = stdscr.getmaxyx()  # re-read: the terminal may resize
            rows = min(len(lines), h - 2)
            content = [
                core.display_width(text)
                for text, _attr, *kind in lines
                if (kind[0] if kind else "") != PICKER_DIVIDER
            ]
            wanted = max(content or [0]) + 3
            width = min(w, wanted)
            if rows < 1 or width < 6:
                return fallback
            # A clamped box keeps one trailing blank column so a truncated
            # line never ends flush against the right border.
            inner = width - 2 - (1 if width < wanted else 0)
            top = max(0, min(selected - rows // 2, len(lines) - rows))
            box_y = max(0, (h - rows - 2) // 2)
            box_x = max(0, (w - width) // 2)
            try:
                win = curses.newwin(
                    rows + 2, width, box_y, box_x,
                )
                # Read keys from the box itself: stdscr.getch() would
                # implicitly repaint the stale panel over it after a resize.
                win.keypad(True)
                win.timeout(2000)
                win.erase()
                win.box()
                win.addnstr(0, 1, title, width - 2)
                for i in range(rows):
                    text, attr, *kind = lines[top + i]
                    if (kind[0] if kind else "") == PICKER_DIVIDER:
                        win.addnstr(i + 1, 0, divider_text(width), width,
                                    attr)
                        continue
                    win.addnstr(i + 1, 1, core.fit_cols(text, inner),
                                width - 2, attr)
                win.refresh()
            except curses.error:
                return fallback
            ch = win.getch()
            if ch in (-1, curses.KEY_RESIZE):
                continue  # a refresh tick or resize: redraw the box
            if ch == core.MOUSE_KEY:
                try:
                    _id, mx, my, _z, bstate = curses.getmouse()
                except (curses.error, AttributeError):
                    continue
                kind = _mouse.mouse_kind(bstate)
                _mouse.mouse_debug(bstate, mx, my, kind)
                if kind == "press":
                    pressed = True  # the release decides inside vs outside
                    continue
                if kind == "release":
                    if not pressed:
                        continue
                    pressed = False
                elif kind == "click":
                    pressed = False
                else:
                    continue  # a wheel notch or another button: ignore it
                inside, rel = _mouse.picker_click(
                    rows, box_y, box_x, rows + 2, width, mx, my
                )
                if not inside:
                    return None  # a click outside either box cancels it
                if rel is not None and mouse_pick is not None:
                    result = mouse_pick(state, top + rel)
                    if result is not _PICKER_STAY:
                        return result
                continue
            state, result = step(state, ch)
            if result is not _PICKER_STAY:
                return result
    except KeyboardInterrupt:
        # Ctrl-C anywhere inside the box (draw or key handling) cancels it;
        # it must never escape to the main loop, where it would detach.
        return None
    finally:
        # A Ctrl-C landing during this repaint must stay contained too: the
        # box cancels (or returns its pick) and never detaches the client.
        # Repaint each step separately so one interrupt cannot skip the rest.
        for repaint in (stdscr.touchwin, stdscr.refresh):
            try:
                repaint()
            except (curses.error, KeyboardInterrupt):
                pass


def new_box(stdscr, row, installed: list[str], hint: str | None = None,
            sections: list[str] | None = None) -> dict | None:
    """Boxed Goal + Harnesses picker; a choice dict, or None on cancel.

    A harness choice is `{"action": "harness", "harness": key}`; the Goal item
    is `{"action": "goal"|"slice"}`. If the box cannot be drawn (tiny
    terminal, or curses raises), cancel rather than acting on a pick nobody
    saw. Ctrl-C cancels the box like Esc/q.
    """
    state = new_box_start(row, installed, hint, sections)

    def lines_for(state):
        lines = new_box_lines(state, installed)
        section = state["sections"][state["section"]]
        index = state["harness"] if section == _goals.NEW_BOX_HARNESSES else 0
        selected = next(
            (i for i, (_t, sec, idx) in enumerate(lines)
             if sec == section and idx == index),
            0,
        )
        rendered = []
        for text, sec, idx in lines:
            kind = PICKER_DIVIDER if sec == PICKER_DIVIDER else ""
            attr = 0
            if sec == section and idx == index:
                attr = curses.A_REVERSE
            elif sec == "heading":
                attr = curses.A_BOLD
            rendered.append((text, attr, kind))
        return rendered, selected

    def step(state, ch):
        state, action = new_box_key(state, installed, ch)
        if action == "cancel":
            return state, None
        if action == "harness":
            return state, new_box_pick(installed, state["harness"])
        if action == "goal":
            return state, {"action": state["goal_action"]}
        return state, _PICKER_STAY

    def mouse_pick(state, index):
        return new_box_mouse_pick(state, index, installed)

    return run_picker(
        stdscr, state, "new", lines_for, step, None, mouse_pick
    )


MOVE_LOOSE_ITEM = "Top level"
MOVE_NEW_ITEM = "+ New goal"


def move_targets(g: Group) -> list[tuple[str, Goal | None, int]]:
    """Move-box items: Top level, New goal, then the goal/slice tree (D18).

    Each item is (kind, goal or None, depth); Top level is depth 0, goals
    start at depth 1 and their slices at depth 2. File order is kept for each
    parent.
    """
    children, _ = _goals.goal_tree(g)
    out: list[tuple[str, Goal | None, int]] = [(core.MOVE_LOOSE, None, 0),
                                              (core.MOVE_NEW, None, 0)]

    def walk(node: Goal, depth: int) -> None:
        out.append((core.MOVE_GOAL, node, depth))
        for child in children.get(node.id, []):
            walk(child, depth + 1)

    for root in _goals.goal_roots(g, children):
        walk(root, 1)
    return out


def move_box_start(g: Group, session: Session) -> dict:
    """Initial state for the Move-to box, on the session's current container."""
    targets = move_targets(g)
    current = _goals.goal_of_session(g, session.id)
    index = 0  # Top level is the current container for a loose session
    if current is not None:
        index = next(
            (
                i
                for i, (kind, goal, _) in enumerate(targets)
                if kind == core.MOVE_GOAL and goal is not None
                and goal.id == current.id
            ),
            0,
        )
    return {
        "targets": targets,
        "index": index,
        "current": current.id if current is not None else None,
    }


def move_box_key(state: dict, ch) -> tuple[dict, str]:
    """Apply one key to the Move-to box; returns (state, action).

    Actions are "" (stay), "cancel" or "pick". Ctrl-C, Esc and q cancel.
    """
    state = dict(state)
    targets = state["targets"]
    if ch in (3, 27, ord("q")):
        return state, "cancel"
    if ch in (curses.KEY_UP, ord("k")):
        state["index"] = max(0, state["index"] - 1)
        return state, ""
    if ch in (curses.KEY_DOWN, ord("j")):
        state["index"] = min(len(targets) - 1, state["index"] + 1)
        return state, ""
    if ch in (curses.KEY_ENTER, 10, 13):
        return state, "pick"
    if isinstance(ch, int) and ord("1") <= ch <= ord("9"):
        # Digits follow the drawn rows, so the divider takes no number and a
        # digit on it is ignored rather than picking a hidden neighbour.
        lines = move_box_lines(state)
        line = ch - ord("1")
        if line < len(lines):
            _text, target, kind = lines[line]
            if target is not None and kind != PICKER_DIVIDER:
                state["index"] = target
                return state, "pick"
    return state, ""


def move_box_mark(state: dict, goal: Goal | None) -> str:
    """One marker per row: the current container's ✓, else ⌂ on Top level.

    A goal or slice that is not current gets a blank marker (never a done
    mark); the loose row always shows a glyph so the marker column is never
    empty there.
    """
    current = state.get("current")
    if goal is None:
        return "✓" if current is None else "⌂"
    return "✓" if goal.id == current else " "


def move_box_line_attr(state: dict, index: int) -> int:
    """Attributes for drawn line *index*: reverse when selected, dim when done."""
    _kind, goal, _ = state["targets"][index]
    attr = curses.A_REVERSE if index == state["index"] else 0
    if goal is not None and goal.done:
        attr |= curses.A_DIM
    return attr


def move_box_lines(state: dict) -> list[tuple[str, int | None, str]]:
    """Drawn lines: (text, target index or None, kind), tree in shared columns.

    Every line starts one column in from the left border, like the
    new-session box, so the marker never touches the frame. `⌂`, `+` and `✓`
    start in one marker column; `Top level`, every goal name and every slice
    connector share the next name column (a slice's name follows its
    connector), so slices read as children of their goal. A goal with no
    slices has no connector line. A
    divider line precedes the first goal and has no target index, so an empty
    project never ends with a rule that introduces nothing.
    """
    targets = state["targets"]
    body: list[tuple[str, int | None, str]] = []
    index = 0
    tree_started = False
    while index < len(targets):
        kind, goal, depth = targets[index]
        if kind == core.MOVE_LOOSE:
            body.append((f"{move_box_mark(state, None)} {MOVE_LOOSE_ITEM}",
                         index, ""))
            index += 1
            continue
        if kind == core.MOVE_NEW:
            body.append((MOVE_NEW_ITEM, index, ""))
            index += 1
            continue
        if not tree_started:
            body.append(("", None, PICKER_DIVIDER))
            tree_started = True
        body.append((f"{move_box_mark(state, goal)} {goal.name}", index, ""))
        # Slices are the goal's direct children, immediately after it.
        child = index + 1
        slices: list[int] = []
        while (child < len(targets)
               and targets[child][0] == core.MOVE_GOAL
               and targets[child][2] > depth):
            slices.append(child)
            child += 1
        for position, child_index in enumerate(slices):
            child_goal = targets[child_index][1]
            connector = "└" if position == len(slices) - 1 else "├"
            body.append((
                f"{move_box_mark(state, child_goal)} {connector} "
                f"{child_goal.name}",
                child_index,
                "",
            ))
        index = child
    return [
        (text if kind == PICKER_DIVIDER else " " + text, target, kind)
        for text, target, kind in body
    ]


def move_box(stdscr, g: Group, session: Session) -> tuple[str, Goal | None] | None:
    """Boxed Move-to picker; (kind, goal) or None on cancel (D18).

    Reuses the new-session box's shape: one line per item, a narrow frame, and
    j/k/arrows plus digits and Enter. Esc/q/Ctrl-C cancel, and so does a box
    that cannot be drawn, so a session never moves without a visible pick.
    """
    state = move_box_start(g, session)

    def lines_for(state):
        lines = move_box_lines(state)
        selected = next(
            (i for i, (_t, index, _k) in enumerate(lines)
             if index == state["index"]),
            0,
        )
        return [
            (text,
             0 if index is None else move_box_line_attr(state, index),
             kind)
            for text, index, kind in lines
        ], selected

    def step(state, ch):
        targets = state["targets"]
        state, action = move_box_key(state, ch)
        if action == "cancel":
            return state, None
        if action == "pick":
            kind, goal, _ = targets[state["index"]]
            return state, (kind, goal)
        return state, _PICKER_STAY

    def mouse_pick(state, index):
        return move_box_mouse_pick(state, index)

    return run_picker(
        stdscr, state, "Move to", lines_for, step, None, mouse_pick
    )


def move_box_mouse_pick(state: dict, index: int):
    """Resolve a click on drawn Move-to line *index*; _PICKER_STAY keeps it.

    The divider is drawn but is not a target: clicking it keeps the box open.
    """
    lines = move_box_lines(state)
    if not 0 <= index < len(lines):
        return _PICKER_STAY
    _text, target, kind = lines[index]
    if kind == PICKER_DIVIDER or target is None:
        return _PICKER_STAY
    kind, goal, _ = state["targets"][target]
    return kind, goal
