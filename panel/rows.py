"""List rows: recency ordering, group names, tiers and both limits.

A project, an expanded goal and an expanded slice each page their children with
the same ladder: the base limit, then caps of `base * (2**(level+1) - 1)`. The
tier map is in-memory; a `more` row carries the hidden count and whether
everything is shown, and an in-progress session is never hidden at any level.
Rows keep a stable `row_key()` so selection survives a re-sort.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass

from panel import core, goals as _goals
from panel.core import Goal, Group, Session


def goal_member_ids(g: Group) -> set[str]:
    """Ids of every session claimed by one of the group's goals."""
    return {i for gl in g.goals for i in gl.sessions}


def goal_age_ts(
    g: Group,
    goal: Goal,
    members: list[Session] | None = None,
    children: dict[str, list[Goal]] | None = None,
) -> float:
    """Newest activity in the goal's whole subtree, else its creation time."""
    if members is None:
        members = _goals.goal_subtree_members(g, goal, children)
    if members:
        return members[0].last_ts
    return max(
        (
            node.created
            for node in _goals.goal_subtree_goals(g, goal, children)
        ),
        default=goal.created,
    )


def recency_entries(entries: list[tuple]) -> list[tuple]:
    """Sort (timestamp, kind, item, extra) entries, done sunk at this level."""
    return sorted(entries, key=lambda t: (t[2].done, -t[0]))


def child_entries(
    g: Group,
    sessions: list[Session],
    children,
    children_map: dict[str, list[Goal]] | None = None,
) -> list[tuple]:
    """Recency entries for direct sessions and child slices (D15).

    *children* pairs each child Goal with the payload the caller needs: None
    for `--list`, its GoalPlan for rows. *children_map* is the project's
    adjacency map, so every slice reuses one refresh's tree. Each entry is
    (timestamp, kind, item, extra); a slice ranks and ages by its subtree.
    """
    entries = [(s.last_ts, "session", s, None) for s in sessions]
    entries += [
        (goal_age_ts(g, child, children=children_map), "slice", child, extra)
        for child, extra in children
    ]
    return recency_entries(entries)


def goal_child_items(
    g: Group,
    goal: Goal,
    children: dict[str, list[Goal]] | None = None,
) -> list[tuple]:
    """Direct sessions and child slices, interleaved by recency (D15).

    Done children sink below active ones at this level (A7).
    """
    return child_entries(
        g,
        _goals.goal_members(g, goal),
        [(child, None) for child in _goals.goal_children(g, goal, children)],
        children,
    )


def recency_top(
    sessions: list[Session], goal_rows: list[tuple[Goal, list[Session], float]]
) -> list[tuple]:
    """Loose sessions and goals interleaved by recency, newest first.

    A goal ranks by its newest session, else by creation. Entries are
    (timestamp, kind, item, goal members or None).
    """
    top = [(s.last_ts, "session", s, None) for s in sessions]
    top += [(age, "goal", gl, members) for gl, members, age in goal_rows]
    return recency_entries(top)


def group_name(cwd: str, home: str) -> str:
    """The readable name of a working directory: `root` for home, else its own
    folder name. Only the *display* name; grouping still keys on the full cwd."""
    path = os.path.normpath(cwd) if cwd else cwd
    if path == home:
        return "root"
    return os.path.basename(path) or path or "?"


def canonical_group_names(
    groups: list[Group], home: str | None = None
) -> dict[str, str]:
    """Readable, stable names for every group's working directory.

    Home becomes `root`, a project shows just its folder name, and two folders
    with the same name grow parent components until they are distinguishable
    (`a/app`, `b/app`). Names depend only on the set of cwds, so they do not
    change when sessions re-sort.
    """
    home = os.path.normpath(os.path.expanduser("~")) if home is None else home
    cwds: list[str] = []
    seen: set[str] = set()
    for g in groups:
        cwd = os.path.normpath(g.cwd) if g.cwd else g.cwd
        if cwd not in seen:
            seen.add(cwd)
            cwds.append(cwd)
    parts = {
        cwd: [p for p in re.split(r"[\\/]+", cwd) if p] for cwd in cwds
    }
    labels: dict[str, str] = {}
    used: set[str] = set()
    if home in parts:
        labels[home] = "root"
        used.add("root")
    buckets: dict[str, list[str]] = {}
    for cwd in cwds:
        if cwd in labels:
            continue
        base = group_name(cwd, home)
        buckets.setdefault(base, []).append(cwd)
    for base, paths in buckets.items():
        max_depth = max(len(parts[cwd]) for cwd in paths)
        # A folder literally named `root` must not shadow home's label.
        depth = 2 if base in used else 1
        while depth < max_depth:
            names = {
                "/".join(parts[cwd][-depth:]) if parts[cwd] else cwd
                for cwd in paths
            }
            if len(names) == len(paths):
                break
            depth += 1
        for cwd in paths:
            labels[cwd] = (
                "/".join(parts[cwd][-depth:]) if parts[cwd] else cwd
            )
    return labels


def tier_level(tiers: dict[str, int] | None, key: str) -> int:
    """Current tier level for a container; 0 is the base limit."""
    return max(0, (tiers or {}).get(key, 0))


def tier_cap(base: int, level: int) -> int:
    """Items shown at *level*: the base, then doubling steps from there."""
    return base * (2 ** (max(0, level) + 1) - 1)


def tier_up(tiers: dict[str, int], key: str) -> int:
    """Reveal the next tier for *key*; returns the new level."""
    level = tier_level(tiers, key) + 1
    tiers[key] = level
    return level


def tier_down(tiers: dict[str, int], key: str) -> int:
    """Hide one tier for *key*; level 0 drops the entry. Returns the level."""
    level = tier_level(tiers, key) - 1
    if level > 0:
        tiers[key] = level
    else:
        tiers.pop(key, None)
        level = 0
    return level


def more_row_index(rows: list[tuple], key: str) -> int | None:
    """Index of the `more` row belonging to container *key*, if drawn."""
    for i, item in enumerate(rows):
        if item[0] in ("more", "goal_more", "slice_more") and (
            more_key(item[0], item[1], item[2]) == key
        ):
            return i
    return None


def more_hidden(row: tuple) -> int:
    """Hidden count carried by a `more`, `goal_more` or `slice_more` row."""
    return row[2][0] if row[0] == "more" else row[2][1]


def first_added_key(
    before: list[tuple], after: list[tuple], more_index: int
) -> tuple | None:
    """Key of the first row a reveal added, which takes the old `more` slot."""
    if not 0 <= more_index < len(before):
        return None
    if before[more_index][0] not in ("more", "goal_more", "slice_more"):
        return None
    return row_key(after[more_index]) if more_index < len(after) else None


def tier_reveal(
    rows: list[tuple],
    groups: list[Group],
    flt: str,
    collapsed: set[str],
    expanded: dict[str, int],
    key: str,
    fallback: tuple,
) -> tuple | None:
    """Reveal a level that actually adds rows; land on the first of them.

    The stored level advances until the container's hidden count drops (or
    nothing is hidden), so a tier whose cap adds no row -- several running
    sessions already pulled through it -- is skipped rather than left behind.
    Returns *fallback* when nothing took the old `more` slot, and None when
    everything is already shown.
    """
    index = more_row_index(rows, key)
    if index is None or more_hidden(rows[index]) == 0:
        return None
    old_hidden = more_hidden(rows[index])
    after = rows
    while True:
        tier_up(expanded, key)
        after = list(rows_for(groups, flt, collapsed, expanded))
        current = more_row_index(after, key)
        hidden = 0 if current is None else more_hidden(after[current])
        if hidden < old_hidden or current is None:
            break
    return first_added_key(rows, after, index) or fallback


def tier_hide(
    groups: list[Group],
    flt: str,
    collapsed: set[str],
    expanded: dict[str, int],
    key: str,
    container_row: tuple,
) -> tuple:
    """Hide one tier; land on the surviving `more` row, else the container."""
    tier_down(expanded, key)
    after = list(rows_for(groups, flt, collapsed, expanded))
    index = more_row_index(after, key)
    return row_key(after[index]) if index is not None else container_row


@dataclass
class GoalPlan:
    """A goal/slice with the filtered children that should be drawn under it."""

    goal: Goal
    full: bool
    sessions: list[Session]
    children: list["GoalPlan"]


def plan_goal(
    g: Group,
    goal: Goal,
    flt_l: str,
    match_ids: set[str],
    force_full: bool = False,
    children_map: dict[str, list[Goal]] | None = None,
) -> GoalPlan | None:
    """Filtered subtree plan; None when neither the node nor a child matches.

    A name match (or an empty filter) shows the whole subtree; otherwise only
    matching sessions and the slices that contain them survive. *children_map*
    is the group's adjacency map, shared for one refresh (D15).
    """
    full = force_full or not flt_l or flt_l in goal.name.lower()
    direct = _goals.goal_members(g, goal)
    sessions = direct if full else [s for s in direct if s.id in match_ids]
    children = []
    for child in _goals.goal_children(g, goal, children_map):
        plan = plan_goal(
            g, child, flt_l, match_ids, full, children_map
        )
        if plan is not None:
            children.append(plan)
    if not full and not sessions and not children:
        return None
    return GoalPlan(goal, full, sessions, children)


def rows_for(
    groups: list[Group],
    flt: str,
    collapsed: set[str] | None = None,
    expanded: dict[str, int] | None = None,
    limit: int | None = None,
    trees: dict[int, tuple[dict[str, list[Goal]], dict[str, str]]] | None = None,
):
    """Yield (kind, group, payload) rows honoring filter, collapse and limits.

    A project shows at most *limit* top-level items (a loose session, or a goal
    counting as one), then more in doubling tiers from the base: 5, 15, 35, 75.
    An expanded goal lists its direct sessions and slices the same way from
    GOAL_SESSION_LIMIT; an expanded slice lists its sessions from
    SLICE_SESSION_LIMIT. Each container gets its own `more` row whenever
    something is hidden or its tier level is above zero; the payload carries
    (hidden count, all shown). Those rows stay once expanded so the same row
    shrinks the list again, and are absent at the base tier when nothing is
    hidden. Running sessions are never hidden at any level, and a goal or slice
    holding one counts as running. A filter shows every match and expands every
    container; a collapsed project, goal or slice shows only its heading.
    """
    collapsed = collapsed or set()
    expanded = expanded or {}
    limit = core.SESSION_LIMIT if limit is None else limit
    flt_l = flt.lower()
    for g in groups:
        match_sessions = [
            s
            for s in g.sessions
            if not flt
            or flt_l in s.title.lower()
            or flt_l in s.id.lower()
            or flt_l in s.last_user.lower()
            or flt_l in s.harness
            or flt_l in core.HARNESS[s.harness]["label"].lower()
        ]
        match_ids = {s.id for s in match_sessions}
        member_ids = goal_member_ids(g)
        loose = [s for s in match_sessions if s.id not in member_ids]
        tree = trees.get(id(g)) if trees is not None else None
        if tree is None:
            tree = _goals.goal_tree(g)
            if trees is not None:
                trees[id(g)] = tree
        children, _ = tree
        plans = [
            plan
            for plan in (
                plan_goal(g, root, flt_l, match_ids, children_map=children)
                for root in _goals.goal_roots(g, children)
            )
            if plan is not None
        ]
        gm = not flt or flt_l in g.cwd.lower()
        if not match_sessions and not gm and not plans:
            continue
        yield ("group", g, None)
        # A filter always expands groups so matches stay visible.
        if g.cwd in collapsed and not flt:
            continue
        top = [(s.last_ts, "session", s, None) for s in loose]
        top += [
            (goal_age_ts(g, plan.goal, children=children), "goal", plan.goal,
             plan)
            for plan in plans
        ]
        top = recency_entries(top)
        shown, hidden, level = tier_page(top, g.cwd, limit, expanded, flt)
        for _, kind, item, plan in shown:
            if kind == "session":
                yield ("session", g, item)
                continue
            yield ("goal", g, item)
            if goal_collapsed(item, flt):
                continue
            yield from goal_child_rows(
                g, plan, expanded, flt, core.GOAL_SESSION_LIMIT, "goal_more",
                children,
            )
        if not flt and (hidden > 0 or level > 0):
            # Kept above the base tier so the same row shrinks the list.
            yield ("more", g, (hidden, hidden == 0))


def is_running(s: Session) -> bool:
    return s.status == "in_progress"


def plan_running(plan: GoalPlan) -> bool:
    return any(map(is_running, plan.sessions)) or any(
        plan_running(child) for child in plan.children
    )


def entry_running(entry: tuple) -> bool:
    """Whether a recency entry is, or holds, a running session."""
    _, kind, item, extra = entry
    if kind == "session":
        return is_running(item)
    if isinstance(extra, GoalPlan):
        return plan_running(extra)
    return any(map(is_running, extra or []))


def limit_keep(items: list, limit: int, running) -> list:
    """The first *limit* items plus any later one *running* says to keep."""
    return [x for i, x in enumerate(items) if i < limit or running(x)]


def tier_page(
    entries: list[tuple],
    key: str,
    base: int,
    expanded: dict[str, int],
    flt: str,
) -> tuple[list[tuple], int, int]:
    """(shown entries, hidden count, tier level) for one container (D15).

    A filter ignores the limits; otherwise the container's tier cap applies and
    running entries are pulled through it.
    """
    level = tier_level(expanded, key)
    shown = entries if flt else limit_keep(
        entries, tier_cap(base, level), entry_running
    )
    return shown, len(entries) - len(shown), level


def goal_child_rows(
    g: Group,
    plan: GoalPlan,
    expanded: dict[str, int],
    flt: str,
    base_limit: int,
    more_kind: str,
    children: dict[str, list[Goal]] | None = None,
) -> list[tuple]:
    """Rows under one expanded goal or slice, in its own tier (D15)."""
    entries = child_entries(
        g,
        plan.sessions,
        [(child.goal, child) for child in plan.children],
        children,
    )
    shown, hidden, level = tier_page(
        entries, plan.goal.id, base_limit, expanded, flt
    )
    out: list[tuple] = []
    for _, kind, item, child in shown:
        if kind == "session":
            out.append(("session", g, item))
            continue
        out.append(("slice", g, item))
        if goal_collapsed(item, flt):
            continue
        out.extend(
            goal_child_rows(
                g, child, expanded, flt, core.SLICE_SESSION_LIMIT, "slice_more",
                children,
            )
        )
    if not flt and (hidden > 0 or level > 0):
        # Kept above the base tier so the same row shrinks the list.
        out.append((more_kind, g, (plan.goal, hidden, hidden == 0)))
    return out


def more_key(kind: str, g: Group, s) -> str:
    """Tier-map key of a `more` (project cwd) or goal/slice `more` row."""
    return g.cwd if kind == "more" else s[0].id


def goal_collapsed(goal: Goal, flt: str) -> bool:
    return goal.collapsed and not flt


def group_collapsed(g: Group, collapsed: set[str], flt: str) -> bool:
    return g.cwd in collapsed and not flt


def row_index(rows, key) -> int | None:
    """Index of the drawn row matching *key*, or None when it is not drawn."""
    for i, item in enumerate(rows):
        if row_key(item) == key:
            return i
    return None


def neighbour_after_delete(rows, item) -> tuple:
    """The key to select once *item*'s row is removed from *rows*.

    The row below takes its place when it is still in the same project,
    otherwise the row above; a project left with nothing selects its heading.
    Only rows actually drawn are considered, so a filter or a tier is honored.
    A tier row (`more`, `goal_more`, `slice_more`) is skipped: deleting a
    session lowers the hidden count, so that row may not be drawn next time.
    """
    cwd = item[1].cwd
    index = row_index(rows, row_key(item))
    if index is None:
        return ("group", cwd)
    for i in (index + 1, index - 1):
        if (
            0 <= i < len(rows)
            and rows[i][1].cwd == cwd
            and rows[i][0] not in ("more", "goal_more", "slice_more")
        ):
            return row_key(rows[i])
    return ("group", cwd)


def row_key(item):
    """Stable identity for a row, so selection survives re-sorting."""
    kind, g, s = item
    if kind == "session":
        return ("session", s.id, g.cwd)
    if kind == "more":
        return ("more", g.cwd)
    if kind == "goal":
        return ("goal", s.id, g.cwd)
    if kind == "slice":
        return ("slice", s.id, g.cwd)
    if kind == "goal_more":
        return ("goal_more", s[0].id, g.cwd)
    if kind == "slice_more":
        return ("slice_more", s[0].id, g.cwd)
    return ("group", g.cwd)
