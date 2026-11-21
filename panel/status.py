"""Session status and the grouped session list.

`pending_turn()` lets the unresolved turn decide status and leaves background
bookkeeping to reaping; `session_status()` merges the file-derived base with the
live process, the done mark and needs-input attention in one precedence:
error > done > in progress > needs input > idle. `load_groups()` walks both
session stores, resolves live panes, and attaches goal membership.
"""
from __future__ import annotations

import os
from dataclasses import replace

from panel import core, goals as _goals, panes as _panes, parsers as _parsers
from panel import procs as _procs, state as _state, tmuxctl as _tmux
from panel.core import Group, Session


def pending_turn(s: Session) -> bool:
    """True while the transcript shows a turn the agent has not finished.

    Only the turn decides this: a user or tool result is being processed, or
    the latest assistant turn has no terminal stop reason. Background
    bookkeeping (`open_tasks`, `queued_input`) is deliberately ignored here;
    a background run outliving its `end_turn` leaves the session waiting on
    the user, and only `is_complete()` (reaping) cares about it. A fresh
    session with no messages is not pending: it is waiting for its first
    input.
    """
    if s.messages == 0:
        return False
    if s.awaiting_input:
        return False  # Claude is blocked on its question, not working
    if s.last_role in ("user", "toolResult"):
        return True
    return s.last_stop_reason not in core.TERMINAL_STOP_REASONS


def session_status(
    s: Session, live: bool, done: bool, attention: bool
) -> str:
    """Merge file state, live processes, done marks and attention (D9).

    Precedence is error > done > in progress > needs input > idle. A live
    session is never idle: it is in progress while a turn is unresolved and
    needs input once that turn is terminal (or before its first message).
    """
    if s.last_error:
        return "error"
    if done:
        return "done"
    if live:
        return "needs_input" if not pending_turn(s) else "in_progress"
    if attention:
        return "needs_input"
    return "idle"


_PARSE_CACHE: dict[tuple[str, str], tuple[int, int, Session]] = {}


def read_session(path: str, harness: str = "pi") -> Session | None:
    """Parse a transcript, reusing the last result while the file is unchanged.

    Claude Code transcripts are frequently megabytes long, and the panel
    refreshes every couple of seconds, so unchanged files must not be re-read.
    Callers get a copy: status is recomputed per refresh.
    """
    try:
        st = os.stat(path)
    except OSError:
        return None
    key = (path, harness)
    cached = _PARSE_CACHE.get(key)
    if cached and cached[0] == st.st_mtime_ns and cached[1] == st.st_size:
        s = replace(cached[2])
        # Status is not stored on the parsed copy: it is the clock-free
        # file-derived base here, and `load_groups` merges the live process,
        # done mark and attention on every refresh.
        s.status = core._infer_status(s)
        return s
    s = _parsers.parse_session(path, harness)
    if s is None:
        return None
    _PARSE_CACHE[key] = (st.st_mtime_ns, st.st_size, s)
    return replace(s)


def _store_files(directory: str, harness: str) -> list[tuple[str, str]]:
    """Transcript files in one harness store, paired with their harness."""
    try:
        project_dirs = os.listdir(directory)
    except OSError:
        return []
    files = []
    for name in project_dirs:
        dpath = os.path.join(directory, name)
        if not os.path.isdir(dpath):
            continue
        try:
            names = os.listdir(dpath)
        except OSError:
            continue
        for filename in names:
            if not filename.endswith(".jsonl"):
                continue
            if harness == "claude" and not core.CLAUDE_SESSION_FILE_RE.match(
                filename
            ):
                # Claude keeps subagent transcripts beside sessions; only a
                # UUID-named file is a resumable session.
                continue
            files.append((os.path.join(dpath, filename), harness))
    return files


def session_files() -> list[tuple[str, str]]:
    return _store_files(core.PI_SESSIONS_DIR, "pi") + _store_files(
        core.CLAUDE_SESSIONS_DIR, "claude"
    )


def load_groups() -> list[Group]:
    procs = _procs.process_table()
    panes = _tmux.managed_panes()
    running = _procs.running_session_processes(procs)
    tagged = {p.tag for p in panes if p.tag}
    groups: dict[str, Group] = {}
    sessions: dict[str, Session] = {}
    for path, harness in session_files():
        s = read_session(path, harness)
        if not s:
            continue
        sessions.setdefault(s.id, s)
        groups.setdefault(s.cwd, Group(s.cwd)).sessions.append(s)

    # A managed pane's tag is the authority on the session it runs; adoption
    # keeps it current. Resolve each pane the same way adoption does, so a tag
    # that lags an in-process switch still marks the successor as running.
    children = _panes.session_children(sessions)
    live = set()
    for pane in panes:
        target = _panes.pane_live_session(pane, sessions, children, tagged, procs)
        if target is not None:
            live.add(target.id)
    # Command lines only identify sessions whose agent runs outside managed
    # panes, so a pane's stale argv cannot double-count as still running.
    managed = _panes.managed_process_pids(panes, procs)
    external = {
        sid: {pid for pid in pids if pid not in managed}
        for sid, pids in running.items()
    }
    external = {sid: pids for sid, pids in external.items() if pids}
    claimed = tagged | set(external)
    for sid in external:
        s = sessions.get(sid) or _panes.find_session(sessions, sid)
        if s is not None:
            live.add(_panes.switched_session(s, children, claimed - {sid}).id)
    records, _ = _state.load_sessions()
    for s in sessions.values():
        # A new turn supersedes any old needs-input attention; presenting the
        # session clears it too (D6).
        if pending_turn(s):
            core.clear_attention(s.id)
        s.status = session_status(
            s,
            live=s.id in live,
            done=_state.session_done(s, records),
            attention=core.attention_active(s.id),
        )

    result = list(groups.values())
    for g in result:
        g.sessions.sort(key=lambda s: s.last_ts, reverse=True)
    result.sort(key=lambda g: g.last_ts, reverse=True)
    _goals.attach_goals(result)
    return result
