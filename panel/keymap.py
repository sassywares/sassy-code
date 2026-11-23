"""The panel's single key-binding table.

Each row is `(scope, keys, action, footer, menu, also, listed)`: *scope* is the
row kind the binding acts on, *keys* is how the menu and the README name it,
*action* says what it does, *footer* is the exact hint a footer shows for that
scope, *menu* marks the rows the `?` keys menu lists, *also* is every further
key the same row drives (shown nowhere but still parsed by the tests; the
display never renders it), and *listed* marks the rows the README `## Keys`
table mirrors.

The footers (`view.footer_items`), the keys menu (`help`) and the README
`## Keys` table are renderers of this table, never a second copy of it. Entity
actions keep `menu=False` because their footer already shows them; global,
tmux and mouse rows keep `footer=""` because the menu shows them. Box keys and
`g`/`G` keep working with `menu=False`, and `g`/`G` also `listed=False`, so a
hidden binding never leaves the table.
"""
from __future__ import annotations

from collections import namedtuple


Row = namedtuple(
    "Row", "scope keys action footer menu also listed",
    defaults=("", True, "", True),
)


ROWS: tuple[Row, ...] = (
    # Global bindings: no entity footer carries them (D1/D4).
    Row("List", "↑ ↓", "move the highlight", also="j k"),
    Row(
        "List", "g G", "jump to the first or last row", menu=False,
        also="Home End", listed=False,
    ),
    Row(
        "List", "[ ]",
        "hide or show one tier, or collapse a container",
        also="h l ← →",
    ),
    Row("List", "/", "filter sessions, goals and slices"),
    Row("List", ":", "open the command line"),
    Row(
        "List", "r",
        "reload every managed Pi session's skills; Claude Code has no "
        "equivalent",
    ),
    Row("List", "q", "detach the panel; sessions keep running"),
    Row("List", "Ctrl-C", "detach the panel"),
    Row("No row", "↑ ↓", "move the highlight", "↑/↓ move", False),
    Row("No row", "?", "open the keys menu", "? keys", False),
    # Entity actions live in the footer of the row they act on (D2/D14).
    Row("Project", "⏎", "collapse or expand the project", "⏎ toggle", False),
    Row("Project", "n", "start a session in the project", "n new", False),
    Row("Session", "⏎", "present the session", "⏎ present", False),
    Row("Session", "R", "rename the session", "R rename", False),
    Row(
        "Session", "m", "move the session to a goal or slice", "m move",
        False,
    ),
    Row(
        "Session", "x", "mark the session done, or undo it", "x done",
        False,
    ),
    Row(
        "Session", "D", "delete the session to the trash", "D delete",
        False,
    ),
    Row(
        "Session", "n",
        "start a session in this session's project", "", False,
    ),
    Row(
        "Goal/slice", "⏎", "collapse or expand the goal or slice",
        "⏎ toggle", False,
    ),
    Row(
        "Goal/slice", "n", "start a session in the goal or slice", "n new",
        False,
    ),
    Row("Goal/slice", "R", "rename the goal or slice", "R rename", False),
    Row(
        "Goal/slice", "x", "mark the goal or slice done, or undo it",
        "x done", False,
    ),
    Row(
        "Goal/slice", "D",
        "delete the goal or slice and its sessions to the trash",
        "D delete", False,
    ),
    Row(
        "More/less", "⏎", "reveal the next tier, or hide one", "⏎ more",
        False,
    ),
    Row("Trash", "t", "open the trash"),
    Row(
        "Delete prompt", "y", "confirm the delete; any other key cancels",
        menu=False,
    ),
    # Boxed pickers: their own loop owns these keys (menu=False, still wired).
    Row(
        "New box", "↑ ↓",
        "move between the Goal item and the harnesses", menu=False,
        also="j k",
    ),
    Row(
        "New box", "Tab",
        "switch between the Goal item and the harnesses", menu=False,
    ),
    Row("New box", "1–9 ⏎", "pick a harness or the Goal item", menu=False),
    Row("New box", "Esc q Ctrl-C", "close the box", menu=False),
    Row(
        "Move box", "↑ ↓", "move the highlight", menu=False, also="j k",
    ),
    Row("Move box", "1–9 ⏎", "pick a target", menu=False),
    Row("Move box", "Esc q Ctrl-C", "close the box", menu=False),
    Row(
        "Trash box", "↑ ↓", "move the highlight", menu=False, also="j k",
    ),
    Row(
        "Trash box", "/", "filter by title, project or harness", menu=False,
    ),
    Row(
        "Trash entry", "⏎", "restore the selected session",
        "⏎ restore", False,
    ),
    Row(
        "Trash entry", "d y",
        "purge the selected session after confirmation", "d purge", False,
    ),
    Row("Trash box", "Esc q Ctrl-C", "close the box", menu=False),
    Row("Keys menu", "↑ ↓", "scroll the menu", menu=False, also="j k"),
    Row(
        "Keys menu", "g G", "jump to the top or bottom", menu=False,
        also="Home End",
    ),
    Row("Keys menu", "? Esc q Ctrl-C", "close the menu", menu=False),
    # tmux and the terminal: documented, never panel dispatch.
    Row(
        "Tmux", "prefix + N",
        "start a session in the project under the highlight", also="N",
    ),
    Row("Tmux", "prefix + ← → ↑ ↓", "switch panes"),
    Row(
        "Tmux", "tmux kill-session -t sassy-code",
        "end the panel, viewer and hidden sessions",
    ),
    Row(
        "Mouse", "click",
        "select a session or toggle a project, goal or slice",
    ),
    Row("Mouse", "double-click", "present a session"),
    Row("Mouse", "wheel", "move the highlight one row"),
    Row(
        "Mouse", "drag a session",
        "move it to a goal, slice or heading in the same project",
    ),
    Row(
        "Mouse", "click in a box",
        "pick an item; a click outside cancels",
    ),
    Row(
        "Mouse", "Shift-drag",
        "select terminal text; the terminal handles it",
    ),
)


ROW_SCOPES = {
    "group": "Project",
    "session": "Session",
    "goal": "Goal/slice",
    "slice": "Goal/slice",
    "more": "More/less",
    "goal_more": "More/less",
    "slice_more": "More/less",
}


def scope_for_row(row: tuple | None) -> str | None:
    """The keymap scope a highlighted row uses; None is the no-row footer."""
    if row is None:
        return "No row"
    return ROW_SCOPES.get(row[0])


def footer_for_scope(scope: str | None) -> tuple[str, ...]:
    """The exact footer hints for *scope*, in table order.

    Boxes use this directly for the scope they own (the trash entry), while
    an entity footer reaches it through `footer_items()`.
    """
    return tuple(r.footer for r in ROWS if r.scope == scope and r.footer)


def footer_items(row: tuple | None) -> tuple[str, ...]:
    """The exact footer hints for the highlighted row's scope (D14).

    A done session, goal or slice shows `x undo`; a `more` row that is fully
    shown shows `⏎ less`. No global hint ever leaks into an entity footer.
    """
    scope = scope_for_row(row)
    items = footer_for_scope(scope)
    if row is None:
        return items
    kind = row[0]
    if kind in ("session", "goal", "slice") and row[2].done:
        items = tuple("x undo" if i == "x done" else i for i in items)
    if kind in ("more", "goal_more", "slice_more") and row[2][-1]:
        items = tuple("⏎ less" if i == "⏎ more" else i for i in items)
    return items


def menu_rows() -> list[Row]:
    """Rows the `?` menu renders, in table order."""
    return [r for r in ROWS if r.menu]


def listed_rows() -> list[Row]:
    """Rows the README `## Keys` table mirrors, in table order."""
    return [r for r in ROWS if r.listed]
