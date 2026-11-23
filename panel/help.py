"""The `?` keys menu: every `menu` row from `panel/keymap.py` in one box.

The box uses the shared picker frame (`boxes.run_picker`), so it scrolls,
wraps and mouse-picks the same way the other boxes do. It lists only global,
tmux and mouse rows; entity actions stay in their entity footers and box keys
stay in the README table, so the menu never repeats either.

A long action wraps onto an indented continuation line instead of being
truncated at the panel edge. A continuation is drawn but never selectable: the
key handlers and the mouse pick only ever land on a `row` line, never on a
`wrap`, a heading or a rule.
"""
from __future__ import annotations

import curses

from panel import core, keymap as _keymap
from panel.boxes import PICKER_DIVIDER, _PICKER_STAY, run_picker


# Enough for the widest key label plus a few action columns; the real width
# comes from the screen in `help_box`.
DEFAULT_WIDTH = 80
MIN_WIDTH = 8  # one word plus room for the box frame

SECTIONS = (
    ("Global", ("List", "Trash")),
    ("Tmux", ("Tmux",)),
    ("Mouse", ("Mouse",)),
)


def _chunks(text: str, cols: int) -> list[str]:
    """Split *text* into lines of at most *cols* columns, on word boundaries."""
    cols = max(1, cols)
    chunks: list[str] = []
    rest = text
    while rest:
        if core.display_width(rest) <= cols:
            chunks.append(rest)
            break
        out = ""
        for ch in rest:
            if core.display_width(out + ch) > cols:
                break
            out += ch
        # One long word has to split; a single glyph wider than the column
        # still takes one cell.
        cut = max(out.rfind(" "), 0) or len(out) or 1
        chunks.append(rest[:cut].rstrip())
        rest = rest[cut:].lstrip()
    return chunks or [""]


def _wrap(prefix: str, action: str, width: int) -> list[tuple[str, int, str]]:
    """A row's (text, attr, kind) lines, wrapping its action when it is long."""
    prefix_width = core.display_width(prefix)
    if prefix_width >= width - 4:
        # The key label alone fills the panel: wrap the whole line, so a
        # narrow menu still shows every column instead of clipping one.
        indent = "  "
        chunks = _chunks((prefix + action).strip(), width - len(indent))
        lines = [(" " + chunks[0], 0, "row")]
        lines += [(indent + chunk, 0, "wrap") for chunk in chunks[1:]]
        return lines
    chunks = _chunks(action, width - prefix_width)
    lines = [(prefix + chunks[0], 0, "row")]
    pad = " " * prefix_width
    for chunk in chunks[1:]:
        lines.append((pad + chunk, 0, "wrap"))
    return lines


def _menu_lines(width: int | None = None) -> list[tuple[str, int, str]]:
    """Sections and their rows as (text, attr, kind), divider-nested."""
    width = DEFAULT_WIDTH if width is None else max(MIN_WIDTH, width - 4)
    rows = _keymap.menu_rows()
    key_width = max((core.display_width(r.keys) for r in rows), default=0)
    lines: list[tuple[str, int, str]] = []
    for title, scopes in SECTIONS:
        section = [r for r in rows if r.scope in scopes]
        if not section:
            continue
        if lines:
            lines.append(("", 0, PICKER_DIVIDER))
        lines.append((f" {title}", curses.A_BOLD, "heading"))
        for item in section:
            prefix = f" {core.fit_cols(item.keys, key_width)}  "
            lines.extend(_wrap(prefix, item.action, width))
    return lines


def _row_indices(lines: list[tuple[str, int, str]]) -> list[int]:
    """Selectable line indices: a wrap, heading or rule is never selectable."""
    return [i for i, (_text, _attr, kind) in enumerate(lines) if kind == "row"]


def _width(state: dict) -> int:
    """The live panel width, else the width the state was started with."""
    screen = state.get("screen")
    if screen is not None:
        return screen.getmaxyx()[1]
    width = state.get("width")
    return DEFAULT_WIDTH if width is None else width


def help_lines(state: dict) -> tuple[list[tuple[str, int, str]], int]:
    """Rendered menu lines and the selected index, for `run_picker`.

    The width is read from the screen on every draw, so a resize re-flows the
    wrap instead of keeping the width the menu opened at. The re-flow changes
    the row indices, so the selection is clamped onto a real row and both
    `rows` and `index` are stored back for the key and mouse handlers.
    """
    lines = _menu_lines(_width(state))
    rows = _row_indices(lines)
    index = int(state["index"])
    if index not in rows:  # the one bound: keep the selection on a row
        later = [i for i in rows if i > index]
        index = later[0] if later else (rows[-1] if rows else 0)
    state["rows"] = rows
    state["index"] = index
    rendered = [
        (text, curses.A_REVERSE if i == index else attr, kind)
        for i, (text, attr, kind) in enumerate(lines)
    ]
    return rendered, index


def help_start(width: int | None = None) -> dict:
    width = DEFAULT_WIDTH if width is None else width
    rows = _row_indices(_menu_lines(width))
    return {"index": rows[0] if rows else 0, "rows": rows, "width": width}


def help_box_key(state: dict, ch) -> tuple[dict, str]:
    """Apply one key to the menu; actions are "" (stay) or "cancel".

    `j`/`k`, the arrows and `g`/`G` move between rows; `?`, `Esc`, `q` and
    Ctrl-C close it. Enter is harmless because a menu row is documentation,
    not an action.
    """
    state = dict(state)
    if ch in (3, 27, ord("?"), ord("q")):
        return state, "cancel"
    rows = state["rows"]
    index = int(state["index"])
    if ch in (curses.KEY_UP, ord("k")):
        earlier = [i for i in rows if i < index]
        state["index"] = earlier[-1] if earlier else index
    elif ch in (curses.KEY_DOWN, ord("j")):
        later = [i for i in rows if i > index]
        state["index"] = later[0] if later else index
    elif ch in (ord("g"), curses.KEY_HOME):
        state["index"] = rows[0] if rows else index
    elif ch in (ord("G"), curses.KEY_END):
        state["index"] = rows[-1] if rows else index
    return state, ""


def help_mouse_pick(state: dict, index: int):
    """A click on a drawn row selects it; a wrap, heading or rule is not."""
    if index in state["rows"]:
        state["index"] = index
    return _PICKER_STAY


def help_box(stdscr) -> None:
    """Run the `?` menu until one of its close keys is pressed."""
    state = help_start(stdscr.getmaxyx()[1])
    state["screen"] = stdscr  # help_lines reads the live width from it

    def step(state, ch):
        state, action = help_box_key(state, ch)
        if action == "cancel":
            return state, None
        return state, _PICKER_STAY

    run_picker(
        stdscr, state, "keys", help_lines, step, None, help_mouse_pick
    )
