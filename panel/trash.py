"""Recoverable session deletion: the busy preflight, the stop and the trash.

Deleting a session is deliberately soft: the transcript moves under
`<state>/trash/files/` and an entry in `trash/index.json` records where it came
from, its goal, its done mark and when it was deleted. A goal or slice delete is
the same flow over `goal_subtree_members()`, run as one transaction so a refused
or failed cascade rolls back and reports instead of silently leaving part of a
subtree behind. Nothing in this module ever sends SIGKILL: a live managed agent
gets SIGTERM and a short wait, and a delete that cannot finish refuses before the
transcript moves.
"""
from __future__ import annotations

import os
import re
import shutil
import signal
import time

from panel import core, goals as _goals, panes as _panes, procs as _procs
from panel import state as _state, status as _status, tmuxctl as _tmux
from panel.core import Group, PaneInfo, Proc, Session


TRASH_RETENTION_SECONDS = 7 * 24 * 60 * 60
STOP_TIMEOUT_SECONDS = 3.0
STOP_POLL_SECONDS = 0.05


def trash_root() -> str:
    return os.path.join(_state.STATE_DIR, "trash")


def files_dir() -> str:
    return os.path.join(trash_root(), "files")


def _safe_name(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]", "_", value or "")[:80]
    return cleaned or "session"


def _unique_trash_path(session_id: str, now: float) -> str:
    """A file name under `files/` that never overwrites an older trashed copy."""
    base = _safe_name(session_id)
    candidate = os.path.join(files_dir(), f"{base}.jsonl")
    if not os.path.exists(candidate):
        return candidate
    n = 1
    while True:
        suffix = f"-{int(now)}" if n == 1 else f"-{int(now)}-{n}"
        candidate = os.path.join(files_dir(), f"{base}{suffix}.jsonl")
        if not os.path.exists(candidate):
            return candidate
        n += 1


def _stores_unreadable() -> str:
    """A footer message when a store a delete writes is unreadable, else ""."""
    try:
        _goals.read_goals_file()
    except _state.GoalStoreError as e:
        return str(e)
    try:
        _state.read_sessions_file()
    except _state.SessionError as e:
        return str(e)
    try:
        _state.read_trash_file()
    except _state.TrashError as e:
        return str(e)
    return ""


def load_entries() -> tuple[list[dict], bool]:
    """Trash entries newest first, plus an unreadable flag."""
    entries, unreadable = _state.load_trash()
    ordered = sorted(
        entries, key=lambda e: float(e.get("deleted_at", 0)), reverse=True
    )
    return ordered, unreadable


def _remove_entry(entry: dict, entries: list[dict]) -> list[dict]:
    trashed = entry.get("trashed_path", "")
    return [e for e in entries if e.get("trashed_path") != trashed]


def _drop_entry(entry: dict) -> bool:
    """Remove one entry from the index; False when it was already absent."""
    def apply(data: dict) -> object:
        before = len(data["entries"])
        data["entries"] = _remove_entry(entry, data["entries"])
        return True if len(data["entries"]) != before else _state.NO_WRITE

    return _state.mutate_trash(apply) is not _state.NO_WRITE


def _unlink(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


def prune(now: float | None = None) -> int:
    """Purge entries older than seven days; a corrupt index is left alone."""
    now = time.time() if now is None else now
    cutoff = now - TRASH_RETENTION_SECONDS

    def apply(data: dict) -> object:
        kept, expired = [], []
        for e in data["entries"]:
            if float(e.get("deleted_at", 0)) < cutoff:
                expired.append(e)
            else:
                kept.append(e)
        if not expired:
            return _state.NO_WRITE
        data["entries"] = kept
        return expired

    try:
        expired = _state.mutate_trash(apply)
    except (_state.TrashError, OSError):
        return 0
    if expired is _state.NO_WRITE:
        return 0
    for entry in expired:
        _unlink(str(entry.get("trashed_path", "")))
    return len(expired)


def _same_session(session_id: str, candidate: str) -> bool:
    return bool(candidate) and (
        candidate == session_id or session_id.startswith(candidate)
    )


def session_pids(session_id: str, procs: list[Proc]) -> set[int]:
    running = _procs.running_session_processes(procs)
    return {
        pid
        for sid, pids in running.items()
        if _same_session(session_id, sid)
        for pid in pids
    }


def _pane_runs(pane: PaneInfo, session: Session, procs: list[Proc]) -> bool:
    if pane.tag and _same_session(session.id, pane.tag):
        return True
    sid = _panes.pane_running_session(pane, procs)
    return bool(sid and _same_session(session.id, sid))


def session_panes(
    session: Session, panes: list[PaneInfo], procs: list[Proc]
) -> list[PaneInfo]:
    return [p for p in panes if _pane_runs(p, session, procs)]


def transcript_readable(session: Session) -> bool:
    return _status.read_session(session.path, session.harness) is not None


def _external_pids(
    session: Session, panes: list[PaneInfo], procs: list[Proc]
) -> set[int]:
    """The session's agent pids that are not inside a managed pane."""
    pids = session_pids(session.id, procs)
    managed = _panes.managed_process_pids(panes, procs)
    return {pid for pid in pids if pid not in managed}


def busy_reason(
    session: Session,
    panes: list[PaneInfo] | None = None,
    procs: list[Proc] | None = None,
) -> str:
    """Why deleting *session* is unsafe, or "" (D18).

    The transcript is the authority, not the row's badge: an unresolved turn,
    open background work, an unreadable file, an unknown process snapshot or
    an agent outside managed panes all refuse.
    """
    if not transcript_readable(session):
        return "the transcript is unreadable"
    if _status.pending_turn(session):
        return "a turn is in progress"
    if session.open_tasks or session.queued_input:
        return "background work is still open"
    procs = _procs.process_table() if procs is None else procs
    if not procs:
        return "liveness is unknown"
    panes = _tmux.managed_panes() if panes is None else panes
    pids = session_pids(session.id, procs)
    if _external_pids(session, panes, procs):
        return "it is running outside sassy-code"
    matching = session_panes(session, panes, procs)
    if any(_panes.pane_is_live(p, procs) for p in matching) and not pids:
        return "liveness is unknown"
    return ""


def stop_session(
    session: Session,
    panes: list[PaneInfo] | None = None,
    procs: list[Proc] | None = None,
    timeout: float | None = None,
) -> str:
    """SIGTERM the session's managed agents and wait briefly for them to exit.

    Returns "" once they are gone, or a footer message on a timeout. Never
    signals a shell, tmux or a process outside a managed pane.
    """
    procs = _procs.process_table() if procs is None else procs
    panes = _tmux.managed_panes() if panes is None else panes
    if _external_pids(session, panes, procs):
        return f" '{session.title}' is running outside sassy-code; nothing was deleted "
    pids = session_pids(session.id, procs)
    if not pids:
        return ""
    for pid in sorted(pids):
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            continue
        except OSError as e:
            return (
                f" could not stop '{session.title}': {e.strerror or e} "
            )
    limit = STOP_TIMEOUT_SECONDS if timeout is None else timeout
    deadline = time.monotonic() + limit
    while True:
        remaining = session_pids(session.id, _procs.process_table())
        if not remaining:
            return ""
        if time.monotonic() >= deadline:
            seconds = f"{limit:g}"
            return (
                f" '{session.title}' did not stop within {seconds}s; "
                "nothing was deleted "
            )
        time.sleep(STOP_POLL_SECONDS)


def cleanup_panes(
    session: Session,
    panes: list[PaneInfo],
    procs: list[Proc],
    viewer: str = "",
    reap_state: dict[str, float] | None = None,
) -> None:
    """After a successful delete: drop hidden panes, keep a viewer as a shell."""
    for pane in session_panes(session, panes, procs):
        if pane.id == viewer:
            _tmux.clear_pane_tag(pane.id)
            continue
        if pane.session_name == core.BG_SESSION:
            _tmux.kill_pane(pane.id)
            if reap_state is not None:
                reap_state.pop(pane.id, None)


def delete_session(
    session: Session,
    group: Group,
    presented: str = "",
    panes: list[PaneInfo] | None = None,
    procs: list[Proc] | None = None,
    viewer: str = "",
    reap_state: dict[str, float] | None = None,
    now: float | None = None,
) -> tuple[bool, str]:
    """Delete one session: preflight, stop, move, then update all records.

    Refuses before moving anything when goals.json, sessions.json or the trash
    index is unreadable, or when the session is busy/external/unknown. A store
    or `current` failure after the move puts the transcript back.
    """
    now = time.time() if now is None else now
    unreadable = _stores_unreadable()
    if unreadable:
        return False, unreadable
    goals_data = _goals.read_goals_file()
    procs = _procs.process_table() if procs is None else procs
    panes = _tmux.managed_panes() if panes is None else panes
    reason = busy_reason(session, panes, procs)
    if reason:
        return False, f"cannot delete '{session.title}': {reason}"
    failure = stop_session(session, panes, procs)
    if failure:
        return False, failure.strip()

    membership = _goals.membership_from(goals_data, group.cwd, session.id)
    sessions_before = _state.read_sessions_file()["sessions"]
    done = sessions_before.get(session.id)
    original = session.path
    trashed = _unique_trash_path(session.id, now)
    entry = _tree_entry(
        session, group, sessions_before, trashed, now, goals_data
    )
    try:
        os.makedirs(files_dir(), exist_ok=True)
        shutil.move(original, trashed)
    except OSError as e:
        return False, f"could not move '{session.title}' to trash: {e.strerror or e}"

    def move_back() -> bool:
        try:
            os.makedirs(os.path.dirname(original) or ".", exist_ok=True)
            shutil.move(trashed, original)
            return True
        except OSError:
            return False

    try:
        _append_entries([entry])
    except (_state.TrashError, OSError) as e:
        move_back()
        return False, _state._store_error_message(_state.TRASH_STORE, e)

    try:
        _state.restore_session_done(session.id, None)
    except (_state.SessionError, OSError) as e:
        if move_back():
            _drop_entries([entry])
        return False, _state._store_error_message(_state._SESSIONS_STORE, e)

    try:
        _goals.remove_session_membership(group.cwd, session.id)
    except (_state.GoalError, OSError) as e:
        try:
            _state.restore_session_done(session.id, done)
        except Exception:
            pass
        if move_back():
            _drop_entries([entry])
        return False, _state._store_error_message(_state.GOALS_STORE, e)

    if presented and presented == session.id:
        try:
            _state.write_current("")
        except OSError as e:
            _restore_records(session, group.cwd, membership, done)
            if move_back():
                _drop_entries([entry])
            return False, (
                f"could not clear current: {e.strerror or e}; "
                "nothing was deleted"
            )

    cleanup_panes(session, panes, procs, viewer, reap_state)
    core.clear_attention(session.id)
    return True, f"deleted '{session.title}'; press t to restore"


def _append_entries(entries: list[dict]) -> None:
    """Append manifest entries in one atomic trash write."""
    if not entries:
        return

    def apply(data: dict) -> None:
        data["entries"].extend(entries)

    _state.mutate_trash(apply)


def _drop_entries(entries: list[dict]) -> None:
    """Best-effort removal of entries whose transactions were rolled back."""
    for entry in entries:
        try:
            _drop_entry(entry)
        except Exception:
            pass


def _rollback_note(failed: list[tuple[str, str]]) -> str:
    """The rollback outcome a failed transaction's footer reports."""
    if not failed:
        return "nothing was deleted"
    count = len(failed)
    files = "1 file" if count == 1 else f"{count} files"
    return f"{files} could not be moved back (still in the trash)"


def _index_leftovers(
    entries: list[dict], failed: list[tuple[str, str]]
) -> None:
    """Keep transcripts that could not move back reachable from the trash."""
    if not failed:
        return
    by_path = {e["original_path"]: e for e in entries}
    leftovers = [
        by_path[original] for original, _ in failed if original in by_path
    ]
    try:
        _append_entries(leftovers)
    except (_state.TrashError, OSError):
        pass


def _entries_moved_back(
    entries: list[dict], failed: list[tuple[str, str]]
) -> list[dict]:
    """Entries whose files were restored, so their index rows must go."""
    left = {original for original, _ in failed}
    return [e for e in entries if e["original_path"] not in left]


def _rollback_moves(
    moved: list[tuple[str, str]],
) -> list[tuple[str, str]]:
    """Move every transcript in *moved* back; return those still trashed."""
    failed: list[tuple[str, str]] = []
    for original, trashed in reversed(moved):
        try:
            os.makedirs(os.path.dirname(original) or ".", exist_ok=True)
            shutil.move(trashed, original)
        except OSError:
            failed.append((original, trashed))
    return failed


def _fresh_group(group: Group, goals_data: dict) -> Group:
    """*group* with its goals recomputed from a fresh goals.json parse.

    The session rows stay as refreshed; only membership is re-derived, so a
    session another writer moved out of the doomed subtree is not trashed and
    its new container is never stripped.
    """
    records = goals_data["projects"].get(group.cwd, [])
    goals = [_state.goal_from_record(r) for r in records]
    loose = set(goals_data.get("loose", {}).get(group.cwd, []))
    fresh = Group(cwd=group.cwd, sessions=list(group.sessions), goals=[])
    fresh.goals = _goals.inherit_goal_members(fresh, goals, loose)
    return fresh


def _tree_entry(
    session: Session,
    group: Group,
    sessions_before: dict,
    trashed: str,
    now: float,
    goals_data: dict,
) -> dict:
    goal = _goals.goal_of_session(group, session.id)
    membership = _goals.membership_from(goals_data, group.cwd, session.id)
    done = sessions_before.get(session.id)
    return {
        "session_id": session.id,
        "harness": session.harness,
        "title": session.title,
        "original_path": session.path,
        "trashed_path": trashed,
        "cwd": group.cwd,
        "goal_id": goal.id if goal is not None else "",
        "loose": bool(membership.get("loose")),
        "done": dict(done) if isinstance(done, dict) else None,
        "deleted_at": now,
    }


def delete_subtree(
    group: Group,
    goal,
    presented: str = "",
    panes: list[PaneInfo] | None = None,
    procs: list[Proc] | None = None,
    viewer: str = "",
    reap_state: dict[str, float] | None = None,
    now: float | None = None,
) -> tuple[bool, str]:
    """Delete a goal/slice and every session in its subtree as one transaction.

    The whole subtree is re-derived from a fresh goals.json read and preflighted
    with `busy_reason()` before the prompt's `y` can move anything; one unsafe
    member refuses all and names it. Every manifest entry is computed before the
    first move, then every transcript is moved, then the trash index, done marks
    and goal records are rewritten. Any failure after the first move rolls the
    files back, restores the records and reports; a file that cannot move back
    is named in the footer and stays in the trash, never silently lost.
    """
    now = time.time() if now is None else now
    unreadable = _stores_unreadable()
    if unreadable:
        return False, unreadable
    goals_data = _goals.read_goals_file()
    fresh = _fresh_group(group, goals_data)
    fresh_goal = next((x for x in fresh.goals if x.id == goal.id), None)
    if fresh_goal is None:
        return False, "goal no longer exists"
    goal = fresh_goal
    noun = "slice" if _goals.goal_is_slice(fresh, goal) else "goal"
    procs = _procs.process_table() if procs is None else procs
    panes = _tmux.managed_panes() if panes is None else panes
    members = _goals.goal_subtree_members(fresh, goal)
    for session in members:
        reason = busy_reason(session, panes, procs)
        if reason:
            return False, (
                f"cannot delete {noun} '{goal.name}': "
                f"'{session.title}' {reason}"
            )
    for session in members:
        failure = stop_session(session, panes, procs)
        if failure:
            return False, failure.strip()

    sessions_before = _state.read_sessions_file()["sessions"]
    ids = [s.id for s in members]
    try:
        os.makedirs(files_dir(), exist_ok=True)
        entries = [
            _tree_entry(
                session, fresh, sessions_before,
                _unique_trash_path(session.id, now), now, goals_data,
            )
            for session in members
        ]
    except OSError as e:
        return False, (
            f"could not move the subtree to trash: {e.strerror or e}; "
            "nothing was deleted"
        )

    moved: list[tuple[str, str]] = []
    try:
        for entry in entries:
            shutil.move(entry["original_path"], entry["trashed_path"])
            moved.append(
                (entry["original_path"], entry["trashed_path"])
            )
    except OSError as e:
        failed = _rollback_moves(moved)
        _index_leftovers(entries, failed)
        return False, (
            f"could not move the subtree to trash: {e.strerror or e}; "
            f"{_rollback_note(failed)}"
        )

    clear_current = bool(presented) and presented in ids
    if clear_current:
        try:
            _state.write_current("")
        except OSError as e:
            failed = _rollback_moves(moved)
            _index_leftovers(entries, failed)
            return False, (
                f"could not clear current: {e.strerror or e}; "
                f"{_rollback_note(failed)}"
            )

    try:
        _append_entries(entries)
    except (_state.TrashError, OSError) as e:
        failed = _rollback_moves(moved)
        _index_leftovers(entries, failed)
        if clear_current:
            _restore_current(presented)
        return False, (
            f"{_state._store_error_message(_state.TRASH_STORE, e)}; "
            f"{_rollback_note(failed)}"
        )

    try:
        removed_dones = _state.clear_session_dones(ids)
    except (_state.SessionError, OSError) as e:
        failed = _rollback_moves(moved)
        _drop_entries(_entries_moved_back(entries, failed))
        if clear_current:
            _restore_current(presented)
        return False, (
            f"{_state._store_error_message(_state._SESSIONS_STORE, e)}; "
            f"{_rollback_note(failed)}"
        )

    try:
        _goals.delete_goal_with_members(group.cwd, goal.id, ids)
    except (_state.GoalError, OSError) as e:
        try:
            _state.restore_session_dones(removed_dones)
        except (_state.SessionError, OSError):
            pass
        failed = _rollback_moves(moved)
        _drop_entries(_entries_moved_back(entries, failed))
        if clear_current:
            _restore_current(presented)
        return False, (
            f"{_state._store_error_message(_state.GOALS_STORE, e)}; "
            f"{_rollback_note(failed)}"
        )

    for session in members:
        cleanup_panes(session, panes, procs, viewer, reap_state)
        core.clear_attention(session.id)
    count = len(members)
    plural = "s" if count != 1 else ""
    suffix = f' and {count} session{plural}' if count else ""
    return True, f"deleted {noun} '{goal.name}'{suffix}; press t to restore"


def _restore_current(session_id: str) -> None:
    try:
        _state.write_current(session_id)
    except OSError:
        pass


def _restore_records(
    session: Session, cwd: str, membership: dict, done: dict | None
) -> None:
    try:
        _state.restore_session_done(session.id, done)
    except Exception:
        pass
    try:
        _goals.restore_session_membership(cwd, session.id, membership)
    except Exception:
        pass


def restore_entry(entry: dict) -> tuple[bool, str]:
    """Move one trashed transcript back, then restore its marks and filing."""
    original = str(entry.get("original_path", ""))
    trashed = str(entry.get("trashed_path", ""))
    title = entry.get("title") or entry.get("session_id", "session")
    if not original or not trashed:
        return False, "cannot restore: the trash entry is incomplete"
    if os.path.exists(original):
        return False, f"cannot restore '{title}': the original file is back"
    if not os.path.exists(trashed):
        return False, f"cannot restore '{title}': the trash copy is missing"
    try:
        _goals.read_goals_file()
        _state.read_sessions_file()
        _state.read_trash_file()
    except (_state.GoalError, _state.SessionError, _state.TrashError) as e:
        return False, str(e)
    session_id = str(entry.get("session_id", ""))
    cwd = str(entry.get("cwd", ""))
    goal_id = str(entry.get("goal_id", ""))
    done = entry.get("done")
    done = dict(done) if isinstance(done, dict) else None
    sessions_before = _state.read_sessions_file()["sessions"].get(session_id)
    try:
        os.makedirs(os.path.dirname(original) or ".", exist_ok=True)
        shutil.move(trashed, original)
    except OSError as e:
        return False, f"could not restore '{title}': {e.strerror or e}"

    def back_to_trash() -> bool:
        try:
            shutil.move(original, trashed)
            return True
        except OSError:
            return False

    try:
        _drop_entry(entry)
    except (_state.TrashError, OSError) as e:
        back_to_trash()
        return False, _state._store_error_message(_state.TRASH_STORE, e)

    try:
        _state.restore_session_done(session_id, done)
    except (_state.SessionError, OSError) as e:
        if back_to_trash():
            try:
                _append_entries([entry])
            except Exception:
                pass
        return False, _state._store_error_message(_state._SESSIONS_STORE, e)

    try:
        _goals.file_session_on_restore(cwd, session_id, goal_id)
    except (_state.GoalError, OSError) as e:
        try:
            _state.restore_session_done(session_id, sessions_before)
        except Exception:
            pass
        if back_to_trash():
            try:
                _append_entries([entry])
            except Exception:
                pass
        return False, _state._store_error_message(_state.GOALS_STORE, e)

    return True, f"restored '{title}'"


def purge_entry(entry: dict) -> tuple[bool, str]:
    """Remove one entry and its transcript for good; stays in the trash box."""
    title = entry.get("title") or entry.get("session_id", "session")
    try:
        _drop_entry(entry)
    except (_state.TrashError, OSError) as e:
        return False, _state._store_error_message(_state.TRASH_STORE, e)
    _unlink(str(entry.get("trashed_path", "")))
    return True, f"purged '{title}'"


# Kept as small pure helpers so the picker can be tested without curses.
def _harness_label(entry: dict) -> str:
    """The display label for an entry's harness, falling back to its id."""
    harness = str(entry.get("harness", ""))
    return core.HARNESS.get(harness, {"label": harness})["label"]


def entry_matches(entry: dict, flt: str) -> bool:
    """A trash filter over title, project and harness, case-insensitively."""
    needle = flt.strip().lower()
    if not needle:
        return True
    project = os.path.basename(str(entry.get("cwd", "")).rstrip("/"))
    fields = (
        str(entry.get("title", "")),
        str(entry.get("cwd", "")),
        project,
        str(entry.get("harness", "")),
        _harness_label(entry),
    )
    return any(needle in field.lower() for field in fields)


def filtered_entries(entries: list[dict], flt: str) -> list[dict]:
    return [e for e in entries if entry_matches(e, flt)]
