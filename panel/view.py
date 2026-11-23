"""Drawing the panel: footer, list layout and the screen-line hit map.

`build_lines()` turns rows into single display lines and `draw()` paints them,
returning the drawn screen-line -> row/item map the mouse hit-tests against.
Goal and slice names start in fixed columns, a session's status mark starts in
its container's name column, and a collapsed container rolls its worst
descendant up after the count so a name never shifts.
"""
from __future__ import annotations

import curses
import re
import zlib

from panel import core, goals as _goals, keymap as _keymap
from panel import panes as _panes, rows as _rows
from panel.core import Goal, Group, PaneInfo, Session


def footer_items(row: tuple | None) -> tuple[str, ...]:
    """The exact footer hints for the highlighted row's keymap scope (D14)."""
    return _keymap.footer_items(row)


def footer_lines(items, w: int) -> list[str]:
    """Wrap footer items into as many lines as the panel width needs."""
    width = max(1, w - 1)
    lines: list[str] = []
    line = ""
    for item in items:
        candidate = item if not line else f"{line}  {item}"
        if len(candidate) <= width or not line:
            line = candidate
        else:
            lines.append(line)
            line = item
    if line:
        lines.append(line)
    return lines


def hit_test(hits: dict[int, tuple | None], y: int):
    """The row drawn at screen line *y*, or None for a hint, gap or off-panel."""
    return hits.get(y)


def empty_goal_order(
    g: Group, children: dict[str, list[Goal]] | None = None
) -> list[Goal]:
    """The project's empty containers, oldest first (D16).

    Computed once per draw so each hint line does not rescan every goal. A goal
    holding slices is not empty even when no session sits directly in it.
    """
    if children is None:
        children = _goals.goal_tree(g)[0]
    return sorted(
        (
            x
            for x in g.goals
            if not _goals.goal_subtree_members(g, x, children)
            and not _goals.goal_children(g, x, children)
        ),
        key=lambda x: (x.created, x.id),
    )


def goal_hint(
    g: Group, goal: Goal, empties: list[Goal] | None = None
) -> str:
    """A sarcastic nudge, distinct among the project's empty goals and slices.

    *empties* is the project's empty-container order from `empty_goal_order()`;
    without it the order is built here. Each empty container starts at
    crc32(id) and walks forward to the first line no older empty container
    holds, so a node keeps its line until an older empty one gains a session.
    With more empty nodes than lines the walk wraps and lines repeat.
    """
    if empties is None:
        empties = empty_goal_order(g)
    n = len(_goals.GOAL_HINTS)
    taken: set[int] = set()
    for x in empties:
        i = zlib.crc32(x.id.encode()) % n
        if len(taken) < n:
            while i in taken:
                i = (i + 1) % n
        if x.id == goal.id:
            return _goals.GOAL_HINTS[i]
        taken.add(i)
    return _goals.GOAL_HINTS[zlib.crc32(goal.id.encode()) % n]


def session_root(s: Session, sessions: dict[str, Session]) -> str:
    """Id of the top-most ancestor recorded for a session.

    Handoff children may parent the session the Pi process was started with
    rather than their immediate predecessor, so siblings can share a root.
    """
    by_path = {x.path: x for x in sessions.values() if x.path}
    current = s
    seen = {s.id}
    for _ in range(8):
        parent = by_path.get(current.parent_path) if current.parent_path else None
        if parent is None or parent.id in seen:
            break
        current = parent
        seen.add(current.id)
    return current.id


def follow_viewer_switch(
    selected,
    presented: str,
    panes: list[PaneInfo],
    groups: list[Group],
    viewer: str,
):
    """Move the highlight to the session the viewer's pane now runs.

    `/handoff`, `/fork`, and `/clone` replace the session inside the viewer's
    Pi process, so the pane is re-tagged with the successor while the panel's
    recorded current session still names the replaced one. Only follow when the
    highlight is on that presented session, so browsing another row is never
    interrupted. The successor must be newer and share the presented session's
    handoff lineage: a handoff from a handoff can parent the original session,
    which makes the new session a sibling rather than a child.
    """
    if not presented or not selected or selected[0] != "session":
        return selected, presented
    if selected[1] != presented:
        return selected, presented
    tag = next((p.tag for p in panes if p.id == viewer), "")
    if not tag or tag == presented:
        return selected, presented
    sessions = _panes.session_index(groups)
    current = sessions.get(presented)
    target = sessions.get(tag)
    if current is None or target is None:
        return selected, presented
    if target.created <= current.created:
        return selected, presented
    if session_root(current, sessions) != session_root(target, sessions):
        return selected, presented
    return ("session", target.id, target.cwd), target.id


def session_depths(
    g: Group, parents: dict[str, str] | None = None
) -> dict[str, int]:
    """Session id -> container depth: 0 loose, 1 directly in a goal, 2 slice."""
    if parents is None:
        parents = _goals._normalized_parents(
            g.goals, lambda gl: gl.id, lambda gl: gl.parent
        )
    depths: dict[str, int] = {}
    for goal in g.goals:
        depth = 2 if parents.get(goal.id) else 1
        for sid in goal.sessions:
            depths[sid] = max(depths.get(sid, 0), depth)
    return depths


def highlighted_row(rows: list[tuple], cursor: int) -> tuple | None:
    """The row under the cursor, or None when the cursor is out of range."""
    return rows[cursor] if 0 <= cursor < len(rows) else None


def build_lines(
    items,
    w: int,
    collapsed: set[str],
    flt: str,
    labels: dict[str, str] | None = None,
    trees: dict[int, tuple[dict[str, list[Goal]], dict[str, str]]] | None = None,
):
    """Lay out items into single display lines (one line per item).

    Returns (lines, starts, heights) where each line is
    (text, kind, item_index) and starts/heights map an item index to its
    first line and line count. Project groups are separated by a blank gap.
    A goal name starts at column 5 and a slice name at column 8 (D14); a
    session's status mark starts in its container's name column. Rollup marks
    follow the count so a name never shifts.
    """
    if not labels:
        labels = _rows.canonical_group_names([g for _, g, _ in items])
    lines = []
    starts: dict[int, int] = {}
    heights: dict[int, int] = {}
    maps = {} if trees is None else trees
    depths: dict[int, dict[str, int]] = {}
    empty_orders: dict[int, list[Goal]] = {}

    def tree(g: Group) -> tuple[dict[str, list[Goal]], dict[str, str]]:
        """This group's adjacency map, shared for one draw (D15)."""
        if id(g) not in maps:
            maps[id(g)] = _goals.goal_tree(g)
        return maps[id(g)]

    for idx, (kind, g, s) in enumerate(items):
        if kind == "group" and idx > 0:
            lines.append(("", "gap", -1))
        starts[idx] = len(lines)
        if kind == "group":
            closed = _rows.group_collapsed(g, collapsed, flt)
            arrow = "▸" if closed else "▾"
            name = labels.get(g.cwd, g.cwd)
            # A collapsed heading rolls up its worst descendant, after the
            # count, so neither the name nor the count ever shifts. Expanded,
            # the children carry their own marks and the heading is unchanged.
            worst = _goals.worst_mark(g.sessions) if closed else ""
            mark_text = f" {worst}" if worst else ""
            if not mark_text:
                label = f"{arrow} {name}  ({len(g.sessions)})"
                if len(label) > w - 1:
                    label = label[: max(1, w - 2)] + "…"
            else:
                count_text = f"  ({len(g.sessions)})"
                prefix = f"{arrow} "
                available = max(1, w - 1)
                # Degrade name -> count -> mark so the mark is the last thing
                # to go, and the arrow only goes when not even the mark plus
                # arrow fits.
                mark = mark_text.strip()
                room = available - core.display_width(
                    prefix
                ) - core.display_width(count_text + mark_text)
                if room >= 1:
                    label = (
                        prefix + core.truncate_cols(name, room) + count_text
                        + mark_text
                    )
                elif available - core.display_width(
                    prefix
                ) - core.display_width(mark_text) >= 1:
                    room = (
                        available - core.display_width(prefix)
                        - core.display_width(mark_text)
                    )
                    label = prefix + core.truncate_cols(name, room) + mark_text
                elif available >= core.display_width(prefix + mark):
                    label = prefix + mark
                else:
                    label = core.truncate_cols(mark, available)
            lines.append((label, "group", idx))
        elif kind in ("goal", "slice"):
            closed = _rows.goal_collapsed(s, flt)
            arrow = "▸" if closed else "▾"
            children_of, _ = tree(g)
            members = _goals.goal_subtree_members(g, s, children_of)
            count = len(members)
            suffix = core.rel_time(_rows.goal_age_ts(g, s, members, children_of))
            marks = []
            if closed:
                rollup = _goals.goal_rollup(g, s, members)
                if rollup:
                    marks.append(rollup)
            elif s.done:
                marks.append(core.STATUS["done"][0])
            mark_text = f" {' '.join(marks)}" if marks else ""
            # Goal arrow stays in the harness-glyph column of a loose session;
            # a slice arrow sits two columns before its name column (D14).
            depth = 2 if kind == "slice" else 1
            prefix = " " * core.ARROW_COL[depth] + f"{arrow} "
            hint_indent = " " * core.NAME_COL[depth]
            tail = f"  ({count}){mark_text}"
            tw = max(
                4,
                w - core.display_width(prefix) - core.display_width(tail)
                - core.display_width(suffix) - 2,
            )
            body = prefix + core.truncate_cols(s.name, tw) + tail
            pad = max(
                1, w - core.display_width(body)
                - core.display_width(suffix) - 1
            )
            lines.append((body + " " * pad + suffix, kind, idx))
            if (
                count == 0
                and not closed
                and not _goals.goal_children(g, s, children_of)
            ):
                if id(g) not in empty_orders:
                    empty_orders[id(g)] = empty_goal_order(g, children_of)
                hint = core.truncate_cols(
                    goal_hint(g, s, empty_orders[id(g)]),
                    max(4, w - core.NAME_COL[depth] - 3),
                )
                lines.append((hint_indent + hint, "hint", -1))
        elif kind in ("goal_more", "slice_more"):
            _container, hidden, all_shown = s
            indent = "       " if kind == "goal_more" else "          "
            text = (
                f"{indent}… show fewer"
                if all_shown
                else f"{indent}… {hidden} more"
            )
            lines.append((text, "more", idx))
        elif kind == "more":
            hidden, all_shown = s
            text = (
                "   … show fewer"
                if all_shown
                else f"   … {hidden} more"
            )
            lines.append((text, "more", idx))
        else:
            sym, _ = core.STATUS[s.status]
            suffix = core.rel_time(s.last_ts)
            glyph = core.HARNESS[s.harness]["glyph"]
            if id(g) not in depths:
                depths[id(g)] = session_depths(g, tree(g)[1])
            depth = depths[id(g)].get(s.id, 0)
            indent = " " * core.NAME_COL[depth]
            prefix = f"{indent}{sym} {glyph} "
            tw = max(
                4, w - core.display_width(prefix)
                - core.display_width(suffix) - 2
            )
            title = core.truncate_cols(re.sub(r"\s+", " ", s.title), tw)
            body = prefix + title
            pad = max(
                1, w - core.display_width(body)
                - core.display_width(suffix) - 1
            )
            lines.append((body + " " * pad + suffix, "session", idx))
        heights[idx] = len(lines) - starts[idx]
    return lines, starts, heights


def draw(
    stdscr, groups, flt, cursor, offset, collapsed, message="", expanded=None,
    notice="",
):
    stdscr.erase()
    h, w = stdscr.getmaxyx()

    def put(y: int, text: str, attr: int, pad: bool = True) -> None:
        if not 0 <= y < h or w < 2:
            return
        text = (core.fit_cols if pad else core.truncate_cols)(text, w - 1)
        try:
            stdscr.addnstr(y, 0, text, len(text), attr)
        except curses.error:
            pass  # a tiny or mid-resize terminal must never kill the panel

    title = f" Sassy Code — {sum(len(g.sessions) for g in groups)} sessions "
    put(0, title, curses.A_REVERSE | curses.A_BOLD)
    if flt:
        put(1, f" filter: {flt}", curses.A_DIM, pad=False)

    trees = {id(g): _goals.goal_tree(g) for g in groups}
    items = list(_rows.rows_for(groups, flt, collapsed, expanded, trees=trees))
    keys = footer_lines(footer_items(highlighted_row(items, cursor)), w)
    if notice:
        keys = footer_lines([notice.strip()], w) + keys
    footer_h = len(keys)
    list_top = 2 if flt else 1
    avail = max(1, h - list_top - footer_h)
    if items:
        cursor = min(max(cursor, 0), len(items) - 1)
    else:
        cursor = 0

    lines, starts, heights = build_lines(
        items, w, collapsed, flt, _rows.canonical_group_names(groups), trees
    )

    # Keep the whole selected item (which may span several lines) visible.
    if items:
        start = starts[cursor]
        end = start + heights[cursor]
        if start < offset:
            offset = start
        if end > offset + avail:
            offset = end - avail
    offset = max(0, min(offset, max(0, len(lines) - avail)))

    hits: dict[int, tuple | None] = {}
    for i in range(avail):
        li = offset + i
        if li >= len(lines):
            break
        text, kind, idx = lines[li]
        y = i + list_top
        if kind in ("gap", "hint"):
            hits[y] = None
            if kind == "gap":
                continue
        else:
            hits[y] = items[idx]
        sel = idx == cursor
        attr = curses.A_REVERSE if sel else curses.A_NORMAL
        if kind == "hint":
            put(i + list_top, text, curses.A_DIM)
        elif kind in ("goal", "slice"):
            node = items[idx][2]
            put(i + list_top, text,
                attr | curses.A_BOLD | (curses.A_DIM if node.done else 0))
        elif kind == "group":
            put(i + list_top, text,
                attr | curses.A_BOLD | (0 if sel else curses.color_pair(5)))
        elif kind == "more":
            put(i + list_top, text, attr | (0 if sel else curses.A_DIM))
        else:
            s = items[idx][2]
            _, color = core.STATUS[s.status]
            put(i + list_top, text,
                attr | (0 if sel else curses.color_pair(color))
                | (curses.A_DIM if s.status == "done" else 0))

    footer = footer_lines([message.strip()], w)[:footer_h] if message else keys
    for i in range(footer_h):
        text = footer[i] if i < len(footer) else ""
        put(h - footer_h + i, text,
            curses.A_REVERSE | (curses.A_BOLD if message else 0))
    try:
        stdscr.refresh()
    except curses.error:
        pass
    return cursor, offset, hits
