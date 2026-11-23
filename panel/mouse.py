"""Mouse classification, click meaning, drag-to-move and wheel debounce.

Only a left press starts a drag; motion and other buttons are ignored, and a
wheel notch is debounced so one notch moves exactly one row. `SASSY_CODE_MOUSE_DEBUG`
appends one diagnostic line per event and never lets a write error escape, and
it refuses any path inside the repo.
"""
from __future__ import annotations

import os

from panel import core, rows as _rows, view as _view


def mouse_kind(bstate: int) -> str:
    """Classify a curses mouse bstate: wheel, press, release, click or "".

    Only the real button 4/5 masks count: a build without BUTTON5_* drops a
    wheel-down notch before it reaches us, so there is nothing to guess from
    and no modifier bit is ever read as a wheel. A bstate with no wheel or
    left-button bit (pointer motion, button 2/3) is "".
    """
    if core.MOUSE_WHEEL_UP and bstate & core.MOUSE_WHEEL_UP:
        return "wheel_up"
    if core.MOUSE_WHEEL_DOWN and bstate & core.MOUSE_WHEEL_DOWN:
        return "wheel_down"
    if bstate & (core.MOUSE_B1_PRESSED | core.MOUSE_B1_RELEASED):
        return "press" if bstate & core.MOUSE_B1_PRESSED else "release"
    if bstate & (
        core.MOUSE_B1_CLICKED
        | core.MOUSE_B1_DOUBLE_CLICKED
        | core.MOUSE_B1_TRIPLE_CLICKED
    ):
        return "click"
    return ""


def picker_click(
    rows: int, box_y: int, box_x: int, height: int, width: int,
    mx: int, my: int,
) -> tuple[bool, int | None]:
    """Hit-test a click in a picker box: (inside, item index or None).

    The index is relative to the box's first drawn line; a click on the frame
    is inside but picks nothing. A click outside the box cancels it.
    """
    inside = (
        box_y <= my < box_y + height
        and box_x <= mx < box_x + width
    )
    if not inside:
        return False, None
    rel = my - box_y - 1
    return True, rel if 0 <= rel < rows else None


def wheel_cursor(count: int, cursor: int, kind: str) -> int:
    """One wheel notch moves the highlight one row, clamped into range."""
    if count <= 0:
        return 0
    step = -1 if kind == "wheel_up" else 1
    return max(0, min(count - 1, cursor + step))


def wheel_notch(
    kind: str, now: float, last: tuple[str, float] | None
) -> tuple[bool, tuple[str, float]]:
    """Whether this wheel event is a fresh notch, and the new debounce state.

    A same-direction event inside WHEEL_DEBOUNCE_SECONDS is the same notch
    arriving twice (press then click, or a prolonged event); it updates the
    clock but moves nothing, so a stream of duplicates still counts as one.
    A direction change is always a fresh notch.
    """
    if (
        last is not None
        and kind == last[0]
        and now - last[1] < core.WHEEL_DEBOUNCE_SECONDS
    ):
        return False, (kind, now)
    return True, (kind, now)


def mouse_debug(bstate: int, x: int, y: int, kind: str) -> None:
    """Append one diagnostic line per KEY_MOUSE event when asked.

    SASSY_CODE_MOUSE_DEBUG names the file; unset means inert. The path is
    expanded and refused inside the repo, so the panel never picks a location
    itself, and any write error is swallowed so diagnostics never break it.
    """
    path = os.environ.get("SASSY_CODE_MOUSE_DEBUG")
    if not path:
        return
    try:
        target = os.path.realpath(os.path.expanduser(path))
        # `__file__` lives in the package, so the repo root is its parent.
        repo = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
        if target == repo or target.startswith(repo + os.sep):
            return  # diagnostics never write inside the repo
        with open(target, "a", encoding="utf-8") as fh:
            fh.write(f"bstate=0x{bstate:x} x={x} y={y} kind={kind}\n")
    except (OSError, ValueError):
        pass


def mouse_press(hits, y: int):
    """The session row a press on screen line *y* starts a drag with."""
    row = _view.hit_test(hits, y)
    return row if row is not None and row[0] == "session" else None


def drag_press(drag, hits, y: int):
    """Update a held drag from a left press; an outstanding drag keeps itself.

    A terminal may report pointer motion as a button press, so a press that
    arrives while a drag is in flight must never retarget it.
    """
    if drag is not None:
        return drag
    return mouse_press(hits, y)


def drag_expired(press_time: float, now: float) -> bool:
    """Whether a held drag's press is older than DRAG_EXPIRE_SECONDS."""
    return now - press_time > core.DRAG_EXPIRE_SECONDS


def drag_target(drag, hit):
    """Where a release on *hit* files the pressed *drag* row; None cancels.

    A goal or slice in the same project takes the session; the project
    heading makes it loose. Any other row, or another project, cancels (D20).
    """
    if drag is None or hit is None or hit[1].cwd != drag[1].cwd:
        return None
    if hit[0] in ("goal", "slice"):
        return (core.MOVE_GOAL, hit[2])
    if hit[0] == "group":
        return (core.MOVE_LOOSE, None)
    return None


def mouse_release(kind: str, drag, hit):
    """What a button release means: ("click", row), ("move", target) or None.

    Releasing on the pressed row is a plain click, so a terminal that reports
    press and release instead of a click still works. A release that ends a
    drag only moves onto a same-project goal, slice or heading; anything else
    cancels. Without a drag, a release on a non-session row is a click too,
    so press+release terminals can toggle headings and step `more` rows. A
    release on a session row with no press is ignored.
    """
    if drag is not None:
        if hit is not None and _rows.row_key(hit) == _rows.row_key(drag):
            return ("click", hit)
        target = drag_target(drag, hit)
        return ("move", target) if target is not None else None
    if hit is None:
        return None
    if kind == "click" or (kind == "release" and hit[0] != "session"):
        return ("click", hit)
    return None


def click_kind(
    row: tuple | None, now: float, last_key, last_time: float
) -> str | None:
    """What a left click on *row* means: present, toggle, tier, select or None.

    The second click on the same session row within DOUBLE_CLICK_SECONDS
    presents it (D20); a heading, goal or slice toggles, a `more` row steps a
    tier, and any other row only selects.
    """
    if row is None:
        return None
    if (
        row[0] == "session"
        and last_key == _rows.row_key(row)
        and now - last_time <= core.DOUBLE_CLICK_SECONDS
    ):
        return "present"
    if row[0] in ("group", "goal", "slice"):
        return "toggle"
    if row[0] in ("more", "goal_more", "slice_more"):
        return "tier"
    return "select"


def more_click_action(row: tuple) -> str:
    """Direction for a click on a `more` row: reveal, or shrink when all shown."""
    return "hide" if row[2][-1] else "show"


def cursor_for_key(rows, key) -> int:
    """Index of the row matching *key*, falling back within its group."""
    if key is None:
        return 0
    index = _rows.row_index(rows, key)
    if index is not None:
        return index
    if key[0] not in (
        "session", "more", "goal", "slice", "goal_more", "slice_more"
    ):
        return 0
    cwd = key[1] if key[0] == "more" else key[2]
    if key[0] == "session":
        # Behind a slice's or goal's "more" row, else collapsed away with it.
        group = next((item[1] for item in rows if item[1].cwd == cwd), None)
        goal = next(
            (
                gl
                for gl in (group.goals if group else [])
                if key[1] in gl.sessions
            ),
            None,
        )
        if goal is not None:
            by_id = {gl.id: gl for gl in group.goals}
            chain = [goal]
            parent = by_id.get(goal.parent)
            if parent is not None:
                chain.append(parent)
            for node in chain:
                for i, item in enumerate(rows):
                    if (
                        item[1].cwd == cwd
                        and item[0] in ("goal_more", "slice_more")
                        and item[2][0].id == node.id
                    ):
                        return i
                for i, item in enumerate(rows):
                    if (
                        item[1].cwd == cwd
                        and item[0] in ("goal", "slice")
                        and item[2].id == node.id
                    ):
                        return i
    # A session pushed behind the per-project limit lives under "more".
    for i, item in enumerate(rows):
        if item[0] == "more" and item[1].cwd == cwd:
            return i
    for i, item in enumerate(rows):
        if item[0] == "group" and item[1].cwd == cwd:
            return i
    return 0
