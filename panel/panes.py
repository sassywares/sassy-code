"""Pane identity and lifecycle: session index, liveness, adoption and reaping.

A pane's `@sassy_session` tag is identity, but a tag alone is not liveness: a
pane is live only while an agent still runs in it, and an empty process
snapshot is unknown, not dead, so it falls back to the tag-only answer. This
module also owns the harness data and the spawn helpers that put a session in a
background pane.
"""
from __future__ import annotations

import os
import shlex
import shutil
import time
import uuid

from panel import core, tmuxctl as _tmux
from panel import procs as _procs
from panel.core import Group, PaneInfo, Proc, Session


def installed_harnesses() -> list[str]:
    """Harness keys whose executable is on PATH, in `HARNESS` order."""
    return [key for key in core.HARNESS_KEYS if shutil.which(key)]


def default_harness() -> str:
    """Harness for a brand-new session when the project has no hint.

    `SASSY_CODE_NEW_HARNESS` wins when valid; otherwise use whichever harness
    is installed, so a Claude-only machine does not try to run `pi`.
    """
    value = os.environ.get("SASSY_CODE_NEW_HARNESS", "").strip().lower()
    if value in core.HARNESS:
        return value
    return next(iter(installed_harnesses()), core.DEFAULT_HARNESS)


def session_invoke(s: Session) -> str:
    """The command that resumes *s* in a fresh pane."""
    if s.harness == "claude":
        return f"claude --resume {shlex.quote(s.id)}"
    return f"pi --session {shlex.quote(s.id)}"


def spawn_session_pane(s: Session) -> str:
    command = _tmux._pane_command(session_invoke(s))
    return _tmux._spawn_bg_window(s.cwd, command, tag=s.id)


def spawn_new_session_pane(
    cwd: str, session_id: str | None = None, harness: str = core.DEFAULT_HARNESS
) -> str:
    # Both harnesses can create a session under a caller-chosen id, so the new
    # pane is tagged and adoptable immediately instead of waiting for a file.
    session_id = session_id or str(uuid.uuid4())
    if harness == "claude":
        invoke = f"claude --session-id {shlex.quote(session_id)}"
    else:
        invoke = f"pi --session-id {shlex.quote(session_id)}"
    return _tmux._spawn_bg_window(cwd, _tmux._pane_command(invoke), tag=session_id)


def session_index(groups: list[Group]) -> dict[str, Session]:
    return {s.id: s for g in groups for s in g.sessions}


def find_session(
    sessions: dict[str, Session], key: str
) -> Session | None:
    if not key:
        return None
    if key in sessions:
        return sessions[key]
    for sid, s in sessions.items():
        if sid.startswith(key) or key.startswith(sid):
            return s
    return None


def session_children(
    sessions: dict[str, Session],
) -> dict[str, list[Session]]:
    """Map a session id to sessions switched to from it inside one Pi process."""
    by_path = {s.path: s for s in sessions.values() if s.path}
    children: dict[str, list[Session]] = {}
    for s in sessions.values():
        parent = by_path.get(s.parent_path) if s.parent_path else None
        if parent is not None and parent.id != s.id:
            children.setdefault(parent.id, []).append(s)
    return children


def switched_session(
    s: Session,
    children: dict[str, list[Session]],
    claimed: set[str] | frozenset[str] = frozenset(),
) -> Session:
    """Follow an in-process session switch to the session a pane now runs.

    `/handoff`, `/fork`, and `/clone` replace the session inside the same Pi
    process, so the pane's command line keeps naming the session it was started
    with. The successor records the old file as its `parentSession`. Follow that
    link when the successor was created after the current session's last
    activity, carries newer activity or an unfinished turn, and is not held by
    another pane or process. Copied history keeps old timestamps, so the header
    timestamp (`created`) decides whether the switch happened here.
    """
    current = s
    seen = {current.id}
    for _ in range(8):
        candidates = [
            child
            for child in children.get(current.id, ())
            if child.id not in seen
            and child.id not in claimed
            and child.created > current.last_ts
            and (child.last_ts > current.last_ts or not child.is_complete())
        ]
        if not candidates:
            break
        current = max(candidates, key=lambda child: (child.created, child.last_ts))
        seen.add(current.id)
    return current


def pane_processes(pane: PaneInfo, procs: list[Proc]) -> list[Proc]:
    """Every process in this pane's subtree, including the pane process."""
    if not pane.pid:
        return []
    by_pid = {p.pid: p for p in procs}
    return [by_pid[pid] for pid in _procs.descendants({pane.pid}, procs)
            if pid in by_pid]


def pane_has_agent(pane: PaneInfo, procs: list[Proc]) -> bool:
    """True when a supported harness is alive anywhere in the pane."""
    return any(
        _procs.command_harness(p.command) for p in pane_processes(pane, procs)
    )


def pane_is_live(pane: PaneInfo, procs: list[Proc]) -> bool:
    """True when the pane still runs an agent, or the snapshot cannot say.

    A tag alone is not liveness: when the agent exits, the pane is left as a
    plain shell still carrying `@sassy_session`. But `process_table()` returns
    [] on any `ps` failure or timeout, and treating that as "every process
    exited" would flip every live session to idle and reap every hidden pane at
    once. An empty snapshot is *unknown*, so fall back to the tag-only answer.
    """
    if not procs:
        return bool(pane.tag)
    return pane_has_agent(pane, procs)


def pane_has_pi(pane: PaneInfo, procs: list[Proc]) -> bool:
    return any(
        _procs.command_is_pi(p.command) for p in pane_processes(pane, procs)
    )


def pane_running_session(
    pane: PaneInfo, procs: list[Proc]
) -> str | None:
    # Prefer an actual agent process over the pane shell's command text.
    processes = sorted(
        pane_processes(pane, procs),
        key=lambda p: (not _procs.command_harness(p.command), p.pid),
    )
    for p in processes:
        harness = _procs.command_harness(p.command)
        if not harness:
            continue
        sid = _procs.session_id_from_command(p.command, harness)
        if sid:
            return sid
    return None


def managed_process_pids(panes: list[PaneInfo], procs: list[Proc]) -> set[int]:
    return _procs.descendants({p.pid for p in panes if p.pid}, procs)


def pane_live_session(
    pane: PaneInfo,
    sessions: dict[str, Session],
    children: dict[str, list[Session]],
    claimed: set[str] | frozenset[str],
    procs: list[Proc],
) -> Session | None:
    """The session this pane actually runs, or None when it is only a shell.

    A tag alone is not liveness: when an agent exits, the pane is left as a
    plain shell still carrying `@sassy_session`. Gate on an agent process in
    the pane (`pane_is_live`), the same rule `reap_hidden` and
    `present_session` use, so a dead tagged pane never keeps its session live
    (an empty process snapshot is unknown, not dead).
    """
    if not pane_is_live(pane, procs):
        return None
    return pane_switch_target(pane, sessions, children, claimed, procs)


def pane_switch_target(
    pane: PaneInfo,
    sessions: dict[str, Session],
    children: dict[str, list[Session]],
    claimed: set[str] | frozenset[str],
    procs: list[Proc],
) -> Session | None:
    """The session this pane runs now, following in-process switches.

    A pane's tag is authoritative, but it can lag the process in two ways:
    adoption may not have run yet, and some switches record the session the Pi
    process was *started* with as `parentSession` instead of the one it replaces
    (so a handoff child can be a sibling of the previous child, not its child).
    Follow both the tag and the pane's command line and keep the newest session
    reached, ignoring sessions another pane or process holds.
    """
    candidates = []
    for sid in {pane.tag, pane_running_session(pane, procs)}:
        s = find_session(sessions, sid or "")
        if s is None:
            continue
        candidates.append(
            switched_session(s, children, claimed - {pane.tag or "", sid})
        )
    if not candidates:
        return None
    return max(candidates, key=lambda s: (s.created, s.last_ts))


def find_managed_pane(
    session_id: str,
    panes: list[PaneInfo],
    procs: list[Proc],
    panel_id: str,
) -> PaneInfo | None:
    """Find a pane already running the session, even before it is tagged."""
    for pane in panes:
        if pane.id == panel_id or not pane.pid or pane.tag:
            # A tagged pane already belongs to another session; never relabel
            # it from its (possibly stale) command line.
            continue
        sid = pane_running_session(pane, procs)
        if sid and (sid == session_id or session_id.startswith(sid)):
            return pane
    return None


def external_session_running(
    session_id: str,
    panes: list[PaneInfo],
    procs: list[Proc],
    running: dict[str, set[int]] | None = None,
) -> bool:
    """A session is external if its agent process lives outside managed panes."""
    running = _procs.running_session_processes(procs) if running is None else running
    managed = managed_process_pids(panes, procs)
    for sid, pids in running.items():
        if sid == session_id or session_id.startswith(sid):
            if any(pid not in managed for pid in pids):
                return True
    return False


def adopt_running_panes(
    groups: list[Group],
    panes: list[PaneInfo],
    procs: list[Proc],
    panel_id: str,
    window_id: str,
) -> list[PaneInfo]:
    """Tag panes with the session they now run, after restarts and handoffs.

    A pane's tag can lag the process: `/handoff` replaces the session inside
    the same Pi process without changing its command line, and a handoff from a
    handoff can record the original session as `parentSession`, making the new
    session a sibling of the previous child. `pane_switch_target()` resolves
    both, and the pane keeps one identity.
    """
    sessions = session_index(groups)
    children = session_children(sessions)
    claimed = {p.tag for p in panes if p.tag}
    claimed |= set(_procs.running_session_processes(procs))
    changed = False
    for pane in panes:
        if pane.id == panel_id:
            continue
        target = pane_switch_target(pane, sessions, children, claimed, procs)
        if target is None:
            continue
        if target.id == pane.tag:
            continue
        if target.id in claimed:
            # Another pane or process already holds the successor (for example
            # a fork that was opened separately). Do not steal its identity.
            continue
        claimed.discard(pane.tag or "")
        claimed.add(target.id)
        _tmux.set_pane_tag(pane.id, target.id)
        changed = True
    return _tmux.layout_panes(window_id) if changed else panes


def visible_layout(
    groups: list[Group] | None = None,
    preferred: str = "",
    procs: list[Proc] | None = None,
) -> tuple[str, list[PaneInfo], str]:
    """Panel and viewer panes, adopting untagged panes when groups are given."""
    panel_id = _tmux.panel_pane_id()
    window_id = _tmux.panel_window_id(panel_id)
    panes = _tmux.layout_panes(window_id)
    if groups is not None and panel_id and panes:
        procs = _procs.process_table() if procs is None else procs
        panes = adopt_running_panes(groups, panes, procs, panel_id, window_id)
    viewer = _tmux.viewer_pane_id(panel_id, window_id, panes, preferred)
    return panel_id, panes, viewer


def refresh_hidden(
    groups: list[Group],
    reap_state: dict[str, float],
    preferred: str = "",
) -> tuple[list[PaneInfo], str]:
    """Adopt tagged panes and reap completed/exited background sessions."""
    procs = _procs.process_table()
    _, panes, viewer = visible_layout(groups, preferred, procs)
    reap_hidden(panes, groups, procs, reap_state)
    return panes, viewer


def reap_hidden(
    panes: list[PaneInfo],
    groups: list[Group],
    procs: list[Proc],
    reap_state: dict[str, float],
) -> None:
    """Remove hidden panes with no agent left, or finished with their turn."""
    sessions = session_index(groups)
    now = time.time()
    hidden_ids = set()
    for pane in panes:
        if pane.session_name != core.BG_SESSION:
            continue
        hidden_ids.add(pane.id)
        s = find_session(sessions, pane.tag)
        if not pane_is_live(pane, procs):
            # The agent already exited: remove the leftover shell/window too.
            _tmux.kill_pane(pane.id)
            reap_state.pop(pane.id, None)
            continue
        if s is None or not s.is_complete():
            reap_state.pop(pane.id, None)
            continue
        # A hidden pane that finished its turn is waiting for the user; keep
        # that attention after the pane is reaped (D6). The mark set lives in
        # `core`; errors keep their own status and done marks outrank attention.
        if not s.last_error:
            core.mark_attention(s.id)
            if s.status != "done":
                s.status = "needs_input"
        first_seen = reap_state.get(pane.id)
        if first_seen is None:
            reap_state[pane.id] = now
        elif now - first_seen >= core.REAP_GRACE:
            _tmux.kill_pane(pane.id)
            reap_state.pop(pane.id, None)
    for pane_id in list(reap_state):
        if pane_id not in hidden_ids:
            reap_state.pop(pane_id, None)


def reload_managed_panes(
    groups: list[Group],
    preferred: str = "",
    procs: list[Proc] | None = None,
    panes: list[PaneInfo] | None = None,
    send=_tmux.send_reload,
) -> int:
    """Send `/reload` to every managed pane that still runs Pi.

    This is deliberately unconditional about in-flight turns: reload is a
    user-requested hard refresh, so the panel does not second-guess it.
    """
    procs = _procs.process_table() if procs is None else procs
    if panes is None:
        _, panes, _ = visible_layout(groups, preferred, procs)
    count = 0
    for pane in panes:
        if not pane.tag or not pane_has_pi(pane, procs):
            continue
        if send(pane.id):
            count += 1
    return count
