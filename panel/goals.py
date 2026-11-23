"""Goals and slices: records, membership, mutations and the goal tree.

`goals.json` is read through `panel.state`, which owns the file, its cache and
the corrupt-file guard. This module adds the goal-specific behaviour: attach
and inherit derived membership, create/rename/done/delete/move, file a new
session into a container, and answer tree, rollup and new-session questions.
Nothing here writes except through `panel.state.mutate_store`.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import replace

from panel import state as _state
from panel.core import STATUS, clean_name


GOAL_HINTS = (
    "n. it's not going to plan itself.",
    "press n. or keep admiring the empty folder.",
    "a goal with zero sessions. bold strategy. n.",
    "n starts a session. revolutionary, I know.",
    "empty again. n would fix that.",
    "still nothing here. n is right there.",
    "ambition without sessions. try n.",
    "this goal is vibes only. n adds substance.",
    "zero sessions, infinite potential. n.",
    "a folder of dreams. press n to make it work.",
    "n. the goal won't start itself either.",
    "bold of you to plan this much and press n so little.",
)


def _normalized_parents(items, id_of, parent_of) -> dict[str, str]:
    """Parent links with missing, self and cyclic links turned into roots.

    A parent that is not one of *items* (a deleted goal, an id from another
    project) is ignored rather than dangling; a cycle is cut at one node so
    every tree walk terminates.
    """
    ids = {id_of(x) for x in items}
    parent: dict[str, str] = {}
    for x in items:
        node, p = id_of(x), parent_of(x)
        parent[node] = p if isinstance(p, str) and p in ids and p != node else ""
    for node in list(parent):
        seen: set[str] = set()
        current = node
        while current and current not in seen:
            seen.add(current)
            current = parent.get(current, "")
        if current:
            parent[current] = ""
    return parent


def _children_by_parent(items, id_of, parent_of):
    """(children map keyed by parent id, normalized parent map)."""
    parent = _normalized_parents(items, id_of, parent_of)
    children: dict[str, list] = {}
    for x in items:
        children.setdefault(parent[id_of(x)], []).append(x)
    return children, parent


def _record_parent(record: dict) -> str:
    value = record.get("parent")
    return value if isinstance(value, str) else ""


def read_goals_file() -> dict:
    """Parse goals.json. Missing means no goals; unreadable raises GoalError."""
    return _state.read_store(_state.GOALS_STORE)


def load_goals() -> tuple[GoalIndex, bool]:
    """Goals by project cwd plus an unreadable flag, cached by mtime and size."""
    return _state.load_store(_state.GOALS_STORE)


def attach_goals(groups: list[Group]) -> bool:
    """Set each group's goals from goals.json; True when the file is unreadable.

    A project with no sessions has no group, so its goals stay in the file
    unseen.
    """
    goals, unreadable = load_goals()
    for g in groups:
        g.goals = inherit_goal_members(
            g, goals.get(g.cwd, []), goals.loose.get(g.cwd, set())
        )
    return unreadable


def inherit_goal_members(
    g: Group, goals: list[Goal], loose: set[str] | None = None
) -> list[Goal]:
    """Goals with Pi successors added to their parent's goal.

    Derived on every attach, never written; the cached Goal objects are copied,
    not mutated, so deleting a goal drops its derived members with it. Explicit
    membership wins: an explicitly loose session stays loose, and the nearest
    explicit ancestor (a goal or loose) decides what a successor inherits (A3).
    """
    if not goals:
        return goals
    loose_ids = set(loose or ())
    owner: dict[str, int] = {}
    for n, gl in enumerate(goals):
        for sid in gl.sessions:
            owner.setdefault(sid, n)
    by_path = {s.path: s for s in g.sessions if s.path}
    inherited: dict[int, list[str]] = {}
    for s in g.sessions:
        if s.id in owner or s.id in loose_ids:
            continue
        current, seen = s, {s.id}
        while True:
            parent = by_path.get(current.parent_path)
            if parent is None or parent.id in seen:
                break
            if parent.id in owner:
                inherited.setdefault(owner[parent.id], []).append(s.id)
                break
            if parent.id in loose_ids:
                break  # the nearest explicit membership is loose
            seen.add(parent.id)
            current = parent
    if not inherited:
        return goals
    return [
        replace(gl, sessions=gl.sessions + inherited[n]) if n in inherited else gl
        for n, gl in enumerate(goals)
    ]


def mutate_goals(apply):
    """Re-read goals.json, let *apply* edit it, write atomically."""
    return _state.mutate_store(_state.GOALS_STORE, apply)


def _clean_goal_name(name: str, noun: str = "goal") -> str:
    return clean_name(name, noun, _state.GoalError)


def _record_is_slice(records: list[dict], record: dict) -> bool:
    """True when *record* hangs under a real parent in this project."""
    parent = _record_parent(record)
    return bool(parent) and parent != record["id"] and any(
        other["id"] == parent for other in records
    )


def _create_node(cwd: str, parent: str, name: str, noun: str) -> Goal:
    """Create a top-level goal (empty *parent*) or a slice under *parent*."""
    name = _clean_goal_name(name, noun)
    record = {
        "id": str(uuid.uuid4()),
        "name": name,
        "created": time.time(),
        "done": False,
        "collapsed": False,
        "sessions": [],
        "parent": parent,
    }

    def apply(data: dict) -> None:
        goals = data["projects"].setdefault(cwd, [])
        if parent:
            container = _find_goal_in(goals, parent)
            if _record_is_slice(goals, container):
                raise _state.GoalError("slices cannot contain slices")
            # A collapsed or done parent would hide the node just created, so
            # reopen and expand it as starting a session there does.
            container["collapsed"] = False
            container["done"] = False
        _check_name_free(data, cwd, name, parent)
        goals.append(record)

    mutate_goals(apply)
    return _state.goal_from_record(record)


def create_goal(cwd: str, name: str, parent: str = "") -> Goal:
    """Create a goal. *parent* is empty for top-level; slices use create_slice."""
    return _create_node(cwd, parent, name, "goal")


def create_slice(cwd: str, parent: str, name: str) -> Goal:
    """Create a slice under the top-level goal *parent* (D13, D16)."""
    return _create_node(cwd, parent, name, "slice")


def _find_goal_in(goals: list[dict], goal_id: str) -> dict:
    for g in goals:
        if g["id"] == goal_id:
            return g
    raise _state.GoalError("goal no longer exists")


def _find_goal(data: dict, cwd: str, goal_id: str) -> dict:
    return _find_goal_in(data["projects"].get(cwd, []), goal_id)


def set_goal_collapsed(cwd: str, goal_id: str, collapsed: bool) -> None:
    def apply(data: dict) -> None:
        _find_goal(data, cwd, goal_id)["collapsed"] = collapsed

    mutate_goals(apply)


def file_session_in_goal(cwd: str, goal_id: str, session_id: str) -> None:
    """Record *session_id* in a goal, reopening it when it was done (D19).

    Filed before the session file exists; ids with no file stay hidden.
    """
    def apply(data: dict) -> None:
        g = _find_goal(data, cwd, goal_id)
        sessions = g.setdefault("sessions", [])
        if session_id not in sessions:
            sessions.append(session_id)
        g["done"] = False
        g["collapsed"] = False

    mutate_goals(apply)


def _loose_ids(data: dict, cwd: str) -> list[str]:
    """Explicit-loose ids for one project; `_valid_goals` guarantees the shape."""
    return data.get("loose", {}).get(cwd, [])


def _remove_loose(data: dict, cwd: str, session_id: str) -> None:
    """Drop an explicit-loose mark for one project (A3)."""
    ids = _loose_ids(data, cwd)
    if session_id not in ids:
        return
    loose_map = data["loose"]
    remaining = [i for i in ids if i != session_id]
    if remaining:
        loose_map[cwd] = remaining
        return
    loose_map.pop(cwd, None)
    if not loose_map:
        data.pop("loose", None)


def _add_loose(data: dict, cwd: str, session_id: str) -> None:
    """Record explicit loose membership for one project (A3)."""
    loose_map = data.setdefault("loose", {})
    ids = loose_map.setdefault(cwd, [])
    if session_id not in ids:
        ids.append(session_id)


def move_session(
    cwd: str, session_id: str, target_goal_id: str | None
) -> bool:
    """Move a session into a goal/slice, or to explicit loose (D18, D19).

    Atomically removes the id from every goal and slice in the project, then
    files it in the target (reopening and expanding a done target) or records
    explicit loose membership for that project. Re-reads the store first, so a
    write made since the last load is never lost. Returns whether anything was
    written; a session already exactly in the target is a no-op.
    """
    def apply(data: dict) -> bool:
        goals = data["projects"].get(cwd, [])
        target = _find_goal_in(goals, target_goal_id) if target_goal_id else None
        in_loose = session_id in _loose_ids(data, cwd)
        owners = [g for g in goals if session_id in g.get("sessions", [])]
        if target is not None:
            if len(owners) == 1 and owners[0] is target and not in_loose:
                return _state.NO_WRITE
        elif not owners and in_loose:
            return _state.NO_WRITE
        for owner in owners:
            owner["sessions"] = [
                i for i in owner.get("sessions", []) if i != session_id
            ]
        _remove_loose(data, cwd, session_id)
        if target is not None:
            sessions = target.setdefault("sessions", [])
            if session_id not in sessions:
                sessions.append(session_id)
            target["done"] = False
            target["collapsed"] = False
        else:
            _add_loose(data, cwd, session_id)
        return True

    return mutate_goals(apply) is not _state.NO_WRITE


def move_session_to_new_goal(
    cwd: str, session_id: str, name: str
) -> Goal:
    """Create a top-level goal, then move *session_id* into it (D18)."""
    goal = create_goal(cwd, name)
    move_session(cwd, session_id, goal.id)
    return goal


def slice_name_taken(
    data: dict, cwd: str, parent: str, name: str, skip_id: str = ""
) -> bool:
    """Whether *name* is used by a sibling under *parent* (D16).

    Names are unique within the parent only; the same name in another goal or
    slice, or at the top level, is allowed. An old record without `parent` is
    a top-level sibling.
    """
    records = data["projects"].get(cwd, [])
    parents = _normalized_parents(
        records, lambda r: r["id"], _record_parent
    )
    return any(
        parents.get(g["id"], "") == parent
        and g["name"].lower() == name.lower()
        and g["id"] != skip_id
        for g in records
    )


def goal_name_taken(
    data: dict, cwd: str, name: str, skip_id: str = ""
) -> bool:
    """Whether *name* is used by another top-level goal in the project."""
    return slice_name_taken(data, cwd, "", name, skip_id)


def _check_name_free(
    data: dict, cwd: str, name: str, parent: str = "", skip_id: str = ""
) -> None:
    if slice_name_taken(data, cwd, parent, name, skip_id):
        noun = "slice" if parent else "goal"
        raise _state.GoalError(f"{noun} '{name}' already exists here")


def rename_goal(
    cwd: str, goal_id: str, name: str, noun: str = "goal"
) -> str:
    """Rename a goal or slice; returns the stored name (A1, A5)."""
    name = _clean_goal_name(name, noun)

    def apply(data: dict) -> None:
        record = _find_goal(data, cwd, goal_id)
        records = data["projects"][cwd]
        parents = _normalized_parents(
            records, lambda r: r["id"], _record_parent
        )
        _check_name_free(
            data, cwd, name, parents.get(goal_id, ""), goal_id
        )
        record["name"] = name

    mutate_goals(apply)
    return name


def toggle_goal_done(cwd: str, goal_id: str) -> bool:
    """Flip a goal's done flag; returns the new value (D15).

    Marking done collapses it; reopening expands it.
    """
    def apply(data: dict) -> bool:
        record = _find_goal(data, cwd, goal_id)
        record["done"] = not record.get("done")
        if record["done"]:
            record["collapsed"] = True
        else:
            record["collapsed"] = False
        return record["done"]

    return mutate_goals(apply)


def _doomed_goal_ids(records: list[dict], goal_id: str) -> set[str]:
    """*goal_id* plus every descendant slice id, cycle-safe."""
    children, _ = _children_by_parent(
        records, lambda r: r["id"], _record_parent
    )
    doomed, stack = set(), [goal_id]
    while stack:
        node = stack.pop()
        if node in doomed:
            continue
        doomed.add(node)
        stack.extend(x["id"] for x in children.get(node, []))
    return doomed


def delete_goal(cwd: str, goal_id: str) -> None:
    """Remove a goal and its slices, leaving their sessions loose (D16)."""
    return delete_goal_with_members(cwd, goal_id, [])


def delete_goal_with_members(
    cwd: str, goal_id: str, session_ids: list[str]
) -> None:
    """One write: unfiled *session_ids*, then remove the goal subtree (P2)."""

    def apply(data: dict) -> None:
        records = data["projects"].get(cwd, [])
        _find_goal_in(records, goal_id)
        for session_id in session_ids:
            _strip_membership(data, cwd, session_id)
        doomed = _doomed_goal_ids(records, goal_id)
        data["projects"][cwd] = [
            r for r in records if r["id"] not in doomed
        ]

    mutate_goals(apply)


def goal_of_session(g: Group, session_id: str) -> Goal | None:
    return next((gl for gl in g.goals if session_id in gl.sessions), None)


def goal_tree(
    g: Group,
) -> tuple[dict[str, list[Goal]], dict[str, str]]:
    """(children by parent, normalized parent links) for one refresh (D15)."""
    return _children_by_parent(
        g.goals, lambda gl: gl.id, lambda gl: gl.parent
    )


def goal_roots(
    g: Group, children: dict[str, list[Goal]] | None = None
) -> list[Goal]:
    """Top-level goals: no parent, or a parent that is not in this project."""
    if children is None:
        children = goal_tree(g)[0]
    return children.get("", [])


def goal_children(
    g: Group,
    goal: Goal,
    children: dict[str, list[Goal]] | None = None,
) -> list[Goal]:
    """The goal's direct slices, in file order."""
    if children is None:
        children = goal_tree(g)[0]
    return children.get(goal.id, [])


def goal_is_slice(
    g: Group, goal: Goal, parents: dict[str, str] | None = None
) -> bool:
    """True when *goal* hangs under a real parent in this project (D13)."""
    if parents is None:
        parents = _normalized_parents(
            g.goals, lambda gl: gl.id, lambda gl: gl.parent
        )
    return bool(parents.get(goal.id))


def goal_kind(g: Group, goal: Goal) -> str:
    return "slice" if goal_is_slice(g, goal) else "goal"


def goal_subtree_goals(
    g: Group,
    goal: Goal,
    children: dict[str, list[Goal]] | None = None,
) -> list[Goal]:
    """*goal* plus every descendant slice, cycle-safe."""
    if children is None:
        children = goal_tree(g)[0]
    out: list[Goal] = []
    seen: set[str] = set()
    stack = [goal]
    while stack:
        node = stack.pop()
        if node.id in seen:
            continue
        seen.add(node.id)
        out.append(node)
        stack.extend(children.get(node.id, []))
    return out


def goal_subtree_members(
    g: Group,
    goal: Goal,
    children: dict[str, list[Goal]] | None = None,
) -> list[Session]:
    """Every session in *goal* and its slices, newest first, deduped."""
    found: dict[str, Session] = {}
    for node in goal_subtree_goals(g, goal, children):
        for session_ in goal_members(g, node):
            found.setdefault(session_.id, session_)
    return sorted(found.values(), key=lambda s: s.last_ts, reverse=True)


def worst_mark(sessions) -> str:
    """The worst status mark among *sessions*: `✗` > `●` > `◐` (D15)."""
    statuses = {s.status for s in sessions}
    for status in ("error", "in_progress", "needs_input"):
        if status in statuses:
            return STATUS[status][0]
    return ""


def goal_rollup(
    g: Group, goal: Goal, members: list[Session] | None = None
) -> str:
    """The node's rollup marks: `✗` > `●` > `◐`, plus `✓` when done (D15)."""
    if members is None:
        members = goal_subtree_members(g, goal)
    marks = []
    worst = worst_mark(members)
    if worst:
        marks.append(worst)
    if goal.done:
        marks.append(STATUS["done"][0])
    return " ".join(marks)


def new_session_plan(row: tuple) -> tuple[str | None, str | None]:
    """(harness hint, goal or slice id) for `n` on *row*.

    A goal or slice row, or a session inside one, files the new session there
    (D13); anything else stays loose. A session row reuses its own harness;
    a container row uses its newest subtree member's, then the project's newest
    session.
    """
    kind, g, item = row
    if kind == "goal_more":
        kind, item = "goal", item[0]
    elif kind == "slice_more":
        kind, item = "slice", item[0]
    if kind == "session":
        goal = goal_of_session(g, item.id)
        return item.harness, goal.id if goal else None
    newest = g.sessions[0].harness if g.sessions else None
    if kind in ("goal", "slice"):
        members = goal_subtree_members(g, item)
        return (members[0].harness if members else newest), item.id
    return newest, None


NEW_BOX_GOAL = "goal"
NEW_BOX_HARNESSES = "harnesses"
NEW_BOX_GOAL_ITEM = "+ New goal"
NEW_BOX_SLICE_ITEM = "+ New slice"


def new_box_goal_action(row: tuple | None) -> str | None:
    """`goal`, `slice`, or None for the box's contextual Goal item (D13).

    A project heading, a loose session and a session inside a goal offer a new
    goal; a goal row or a session already in a goal or slice offers a new
    slice. A slice row offers nothing, so its box shows only harnesses.
    """
    if not row:
        return None
    kind, g, item = row
    if kind in ("slice", "slice_more"):
        return None
    if kind == "session":
        return "slice" if goal_of_session(g, item.id) else "goal"
    if kind in ("goal", "goal_more"):
        return "slice"
    return "goal"


def new_box_sections(row: tuple | None) -> list[str]:
    """The sections the box shows for *row*, in display order."""
    sections = []
    if new_box_goal_action(row):
        sections.append(NEW_BOX_GOAL)
    sections.append(NEW_BOX_HARNESSES)
    return sections


def should_open_new_box(
    row: tuple | None, installed: list[str], key=ord("n")
) -> bool:
    """Whether *key* on *row* opens the Goal/Harnesses box (D17).

    Project and goal rows always open it, even with one harness, because the
    Goal section lives there. A slice row opens it only when there is a real
    harness choice; session rows and the `N` shortcut skip it entirely.
    """
    if not row or key != ord("n"):
        return False
    kind = row[0]
    if kind in ("group", "goal"):
        return True
    return kind == "slice" and len(installed) > 1


def goal_members(g: Group, goal: Goal) -> list[Session]:
    """The goal's sessions that have a file, newest first (others hidden)."""
    by_id = {s.id: s for s in g.sessions}
    found = [by_id[i] for i in dict.fromkeys(goal.sessions) if i in by_id]
    return sorted(found, key=lambda s: s.last_ts, reverse=True)


def membership_from(data: dict, cwd: str, session_id: str) -> dict:
    """Where a session is filed explicitly in parsed *data*.

    Used to roll a delete back exactly; inherited membership is derived and
    needs no snapshot. A caller that already read goals.json passes the parse
    so a multi-session delete does not re-read it per member.
    """
    goals = data["projects"].get(cwd, [])
    return {
        "goals": [
            g["id"] for g in goals
            if session_id in g.get("sessions", [])
        ],
        "loose": session_id in _loose_ids(data, cwd),
    }


def session_membership(cwd: str, session_id: str) -> dict:
    """Where a session is filed explicitly: (goal ids, explicit-loose flag)."""
    return membership_from(read_goals_file(), cwd, session_id)


def _strip_membership(data: dict, cwd: str, session_id: str) -> bool:
    """Drop a session from every goal/slice and the loose list; wrote anything?"""
    changed = False
    for g in data["projects"].get(cwd, []):
        sessions = g.get("sessions", [])
        remaining = [i for i in sessions if i != session_id]
        if len(remaining) != len(sessions):
            g["sessions"] = remaining
            changed = True
    if session_id in _loose_ids(data, cwd):
        _remove_loose(data, cwd, session_id)
        changed = True
    return changed


def remove_session_membership(cwd: str, session_id: str):
    """Drop a session from every goal/slice and the loose list in a project."""
    def apply(data: dict) -> object:
        return (
            True if _strip_membership(data, cwd, session_id)
            else _state.NO_WRITE
        )

    return mutate_goals(apply)


def restore_session_membership(cwd: str, session_id: str, snap: dict) -> None:
    """Put a session back exactly where *snap* recorded it (delete rollback)."""
    def apply(data: dict) -> None:
        _strip_membership(data, cwd, session_id)
        by_id = {g["id"]: g for g in data["projects"].get(cwd, [])}
        for goal_id in snap.get("goals", []):
            record = by_id.get(goal_id)
            if record is not None:
                record.setdefault("sessions", []).append(session_id)
        if snap.get("loose"):
            _add_loose(data, cwd, session_id)

    mutate_goals(apply)


def file_session_on_restore(cwd: str, session_id: str, goal_id: str) -> None:
    """Restore a trashed session to *goal_id*, or mark it explicitly loose (D16).

    An existing container is reopened and expanded so the restored row is
    visible. When the recorded container is gone, or the session was at top
    level before deletion, it comes back explicitly loose: a restore never
    lets `inherit_goal_members()` refile it under a parent's container.
    """
    def apply(data: dict) -> None:
        _strip_membership(data, cwd, session_id)
        goals = data["projects"].get(cwd, [])
        record = next((g for g in goals if goal_id and g["id"] == goal_id), None)
        if record is None:
            _add_loose(data, cwd, session_id)
            return
        sessions = record.setdefault("sessions", [])
        if session_id not in sessions:
            sessions.append(session_id)
        record["done"] = False
        record["collapsed"] = False
        # A slice can sit under a collapsed goal; expand the chain so the
        # restored row is actually visible when the main filter shows it.
        by_id = {g["id"]: g for g in goals}
        seen = set()
        parent = record.get("parent", "")
        while parent and parent not in seen and parent in by_id:
            seen.add(parent)
            ancestor = by_id[parent]
            ancestor["collapsed"] = False
            parent = ancestor.get("parent", "")

    mutate_goals(apply)
