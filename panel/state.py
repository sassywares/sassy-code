"""State directory and JSON stores: `goals.json`, `sessions.json`, `current`.

Every panel state file goes through this module: the state directory, the
one-time migration of the old `~/.sassy-code-current`, both JSON stores with
their validation, mtime/size caches and corrupt-file guards, and the session
done marks. A corrupt store hides its data, refuses edits and is never
overwritten.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
from dataclasses import dataclass
from typing import Any, Callable

from panel import core
from panel.core import (
    CURRENT_STATE,
    GOALS_STATE,
    SESSIONS_STATE,
    Goal,
    Session,
)


STATE_DIR = os.path.expanduser("~/.sassywares/sassy-code")


def state_path(name: str) -> str:
    return os.path.join(STATE_DIR, name)


def ensure_state_dir() -> None:
    os.makedirs(STATE_DIR, exist_ok=True)


def migrate_state() -> None:
    """Move the old current-session file into the state dir, once. New wins."""
    new = state_path(CURRENT_STATE)
    if os.path.exists(new) or not os.path.exists(core.LEGACY_STATE_FILE):
        return
    try:
        ensure_state_dir()
        shutil.move(core.LEGACY_STATE_FILE, new)
    except OSError:
        pass


def _read_state() -> str:
    try:
        with open(state_path(CURRENT_STATE)) as f:
            return f.read().strip()
    except OSError:
        return ""


def _write_state(session_id: str) -> None:
    try:
        ensure_state_dir()
        with open(state_path(CURRENT_STATE), "w") as f:
            f.write(session_id)
    except OSError:
        pass


class GoalError(Exception):
    """A goal action refused; the message is shown in the footer."""


class GoalStoreError(GoalError):
    """goals.json is corrupt or unreadable; the action was refused."""


class SessionError(Exception):
    """A session-state action refused; the message is shown in the footer."""


GOALS_UNREADABLE = "goals.json is unreadable: goals hidden, edits refused"
SESSIONS_UNREADABLE = (
    "sessions.json is unreadable: done marks hidden, edits refused"
)


def goal_from_record(r: dict) -> Goal:
    created = r.get("created")
    return Goal(
        id=r["id"],
        name=r["name"],
        created=float(created) if isinstance(created, (int, float)) else 0.0,
        done=bool(r.get("done")),
        collapsed=bool(r.get("collapsed")),
        sessions=[i for i in r.get("sessions", []) if isinstance(i, str)],
        parent=r.get("parent") if isinstance(r.get("parent"), str) else "",
    )


@dataclass(frozen=True)
class Store:
    """One JSON state file: its shape, its error class and its cache slot.

    Both stores read, cache, guard and write through the same helpers; only
    the file name, validator, error message and payload shape differ.
    """

    name: str
    filename: str
    root: str
    cache_var: str
    error: type[Exception]
    unreadable: str
    save_error: str
    valid: Callable[[Any], bool]
    convert: Callable[[dict], Any]
    empty: Callable[[], Any]


# (stamp, converted payload, unreadable) of the last file read, per store.
_sessions_cache: tuple | None = None
_goals_cache: tuple | None = None


def _valid_sessions(data) -> bool:
    if not isinstance(data, dict) or data.get("version") != 1:
        return False
    records = data.get("sessions")
    if not isinstance(records, dict):
        return False
    for sid, record in records.items():
        if not isinstance(sid, str) or not isinstance(record, dict):
            return False
        if not isinstance(record.get("done", False), bool):
            return False
        done_at = record.get("done_at")
        if done_at is not None and not isinstance(done_at, (int, float)):
            return False
    return True


def _valid_goals(data) -> bool:
    if not isinstance(data, dict) or data.get("version") != 1:
        return False
    projects = data.get("projects")
    if not isinstance(projects, dict):
        return False
    for goals in projects.values():
        if not isinstance(goals, list):
            return False
        for g in goals:
            if not (
                isinstance(g, dict)
                and isinstance(g.get("id"), str)
                and isinstance(g.get("name"), str)
                and isinstance(g.get("sessions", []), list)
            ):
                return False
    loose = data.get("loose", {})
    if not isinstance(loose, dict):
        return False
    for ids in loose.values():
        if not isinstance(ids, list) or not all(
            isinstance(i, str) for i in ids
        ):
            return False
    return True


class GoalIndex(dict):
    """Goals by project cwd, plus explicit-loose ids by cwd (A3).

    The mapping keeps `load_goals()`'s historical shape; the loose marks ride
    along on the same cached read so `attach_goals()` needs no second file
    access. Explicit loose membership wins over inherited membership.
    """

    loose: dict[str, set[str]]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.loose = {}


def _goal_records(data: dict) -> GoalIndex:
    index = GoalIndex({
        cwd: [goal_from_record(r) for r in records]
        for cwd, records in data["projects"].items()
    })
    index.loose = {
        cwd: set(ids) for cwd, ids in data.get("loose", {}).items()
    }
    return index


_SESSIONS_STORE = Store(
    name="sessions",
    filename=SESSIONS_STATE,
    root="sessions",
    cache_var="_sessions_cache",
    error=SessionError,
    unreadable=SESSIONS_UNREADABLE,
    save_error="could not save done marks",
    valid=_valid_sessions,
    convert=lambda data: data["sessions"],
    empty=dict,
)

GOALS_STORE = Store(
    name="goals",
    filename=GOALS_STATE,
    root="projects",
    cache_var="_goals_cache",
    error=GoalStoreError,
    unreadable=GOALS_UNREADABLE,
    save_error="could not save goal",
    valid=_valid_goals,
    convert=_goal_records,
    empty=GoalIndex,
)


def _empty_store(store: Store) -> dict:
    return {"version": 1, store.root: {}}


def read_store(store: Store) -> dict:
    """Parse a store file. Missing means empty; unreadable raises."""
    try:
        with open(state_path(store.filename)) as f:
            data = json.load(f)
    except FileNotFoundError:
        return _empty_store(store)
    except (OSError, ValueError):
        raise store.error(store.unreadable)
    if not store.valid(data):
        raise store.error(store.unreadable)
    return data


def load_store(store: Store) -> tuple[Any, bool]:
    """Converted payload plus an unreadable flag, cached by file stamp."""
    cache = globals()[store.cache_var]
    try:
        st = os.stat(state_path(store.filename))
        stamp = (st.st_mtime_ns, st.st_size)
    except OSError:
        stamp = None
    if cache is not None and cache[0] == stamp:
        return cache[1], cache[2]
    try:
        value, unreadable = store.convert(read_store(store)), False
    except store.error:
        value, unreadable = store.empty(), True
    globals()[store.cache_var] = (stamp, value, unreadable)
    return value, unreadable


def write_store_atomic(store: Store, data: dict) -> None:
    """Write a store file through a temp file in the state dir, then rename."""
    ensure_state_dir()
    fd, tmp = tempfile.mkstemp(
        dir=STATE_DIR, prefix=f".{store.name}-", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
        os.replace(tmp, state_path(store.filename))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# Returned by a `mutate_store` apply callback that changed nothing, so the
# store is neither written nor invalidated. Unique, not falsy: some callers
# legitimately return False.
NO_WRITE = object()


def mutate_store(store: Store, apply):
    """Re-read a store, let *apply* edit it, write atomically.

    A corrupt file is never overwritten. *apply* may raise the store's error
    to abort before anything is written, or return `NO_WRITE` to skip the
    write entirely. Returns whatever *apply* returns.
    """
    data = read_store(store)
    result = apply(data)
    if result is not NO_WRITE:
        write_store_atomic(store, data)
        globals()[store.cache_var] = None
    return result


def store_guard(store: Store) -> str:
    """A footer warning when a store is unreadable, else ""."""
    _, unreadable = load_store(store)
    return f" {store.unreadable} " if unreadable else ""


def _store_error_message(store: Store, e: Exception) -> str:
    if isinstance(e, OSError):
        return f"{store.save_error}: {e.strerror or e}"
    return str(e)


def read_sessions_file() -> dict:
    """Parse sessions.json. Missing means no marks; unreadable raises."""
    return read_store(_SESSIONS_STORE)


def load_sessions() -> tuple[dict[str, dict], bool]:
    """Done records by session id plus an unreadable flag, cached by file."""
    return load_store(_SESSIONS_STORE)


def mutate_sessions(apply):
    """Re-read sessions.json, let *apply* edit it, write atomically."""
    return mutate_store(_SESSIONS_STORE, apply)


def sessions_guard() -> str:
    """A footer warning when sessions.json is unreadable, else ""."""
    return store_guard(_SESSIONS_STORE)


def _session_revived(s: Session, record: dict | None) -> bool:
    """Whether *s*'s stored done mark is stale because of newer activity."""
    if not record or not record.get("done"):
        return False
    done_at = record.get("done_at")
    return isinstance(done_at, (int, float)) and s.last_ts > done_at


def session_done(s: Session, records: dict[str, dict]) -> bool:
    """Whether the stored done mark still applies to *s* (D8).

    Activity newer than the mark revives the session without writing to the
    store; `x` again marks it done anew.
    """
    record = records.get(s.id)
    return bool(record and record.get("done")) and not _session_revived(
        s, record
    )


def toggle_session_done(s: Session) -> bool:
    """Flip a session's done mark; returns the new effective value (D8)."""
    def apply(data: dict) -> bool:
        records = data["sessions"]
        if _session_revived(s, records.get(s.id)):
            records[s.id] = {"done": True, "done_at": time.time()}
            return True
        if session_done(s, records):
            records.pop(s.id, None)
            return False
        records[s.id] = {"done": True, "done_at": time.time()}
        return True

    return mutate_sessions(apply)
