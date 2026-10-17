#!/usr/bin/env python3
"""Pi session control panel.

Left pane: grouped list of all Pi sessions with status.
Right pane: the session currently selected, presented by swapping tmux panes.

Sessions that are switched away from stay alive in a dedicated background tmux
session (`sassy-pi-bg`), one hidden single-pane window per live session.  The
panel never respawns a managed pane, so no in-flight Pi process is ever killed
by switching.  This process runs in the left tmux pane and never blocks on the
viewer.
"""
from __future__ import annotations

import argparse
import curses
import json
import os
import re
import shlex
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field

SESSIONS_DIR = os.environ.get(
    "PI_CODING_AGENT_SESSION_DIR", os.path.expanduser("~/.pi/agent/sessions")
)
STATE_FILE = os.path.expanduser("~/.pi/agent/.sassy-pi-current")
ACTIVE_WINDOW = 300  # seconds -> "active"

BG_SESSION = "sassy-pi-bg"
PANE_OPTION = "@sassy_session"
REAP_GRACE = 2.0
TERMINAL_STOP_REASONS = {"stop", "error", "aborted"}

# status -> (symbol, color pair index)
STATUS = {
    "running": ("●", 1),
    "error": ("✗", 2),
    "active": ("◐", 3),
    "idle": ("·", 4),
}

PI_COMMAND_RE = re.compile(r"(?:^|[\s/])pi(?:\s|$)")
SESSION_ARG_RE = re.compile(r"--session(?:-id)?(?:=|\s+)(\S+)")
SHELL_PROCESS_NAMES = {"sh", "bash", "zsh", "dash", "ksh", "fish"}


@dataclass
class Session:
    path: str
    id: str
    cwd: str
    created: float
    last_ts: float
    name: str = ""
    preview: str = ""
    last_user: str = ""
    status: str = "idle"
    messages: int = 0
    last_role: str | None = None
    last_stop_reason: str | None = None

    @property
    def title(self) -> str:
        return self.name or self.preview or "(empty session)"

    def is_complete(self) -> bool:
        """True once the latest turn has a terminal assistant stop reason."""
        if self.last_role in ("user", "toolResult"):
            return False
        return self.last_stop_reason in TERMINAL_STOP_REASONS


@dataclass
class Group:
    cwd: str
    sessions: list[Session] = field(default_factory=list)

    @property
    def last_ts(self) -> float:
        return max((s.last_ts for s in self.sessions), default=0.0)


@dataclass(frozen=True)
class Proc:
    pid: int
    ppid: int
    command: str


@dataclass(frozen=True)
class PaneInfo:
    id: str
    window_id: str
    session_name: str
    pid: int
    tag: str = ""


def iso_to_epoch(ts: str) -> float:
    try:
        return time.mktime(time.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S"))
    except Exception:
        return 0.0


def text_of(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for b in content:
            if isinstance(b, dict) and b.get("type") == "text":
                parts.append(str(b.get("text", "")))
        return " ".join(parts)
    return ""


def clean_preview(txt: str) -> str:
    """Turn a user turn into a one-line preview, skipping skill injections."""
    txt = re.sub(r"\s+", " ", txt).strip()
    if txt.startswith("<skill"):
        # Prefer text the user typed after the injected skill body, if any.
        tail = re.sub(r"^.*?</skill>", "", txt, flags=re.S).strip()
        return tail or "(skill invocation)"
    return txt


def parse_session(path: str) -> Session | None:
    try:
        with open(path, "r", errors="replace") as f:
            lines = f.readlines()
    except OSError:
        return None
    if not lines:
        return None

    def load(line: str):
        try:
            return json.loads(line)
        except Exception:
            return None

    header = load(lines[0])
    if not header or header.get("type") != "session":
        return None

    s = Session(
        path=path,
        id=header.get("id", ""),
        cwd=header.get("cwd", ""),
        created=iso_to_epoch(header.get("timestamp", "")),
        last_ts=iso_to_epoch(header.get("timestamp", "")),
    )

    last_role = None
    last_is_error = False
    for line in lines[1:]:
        o = load(line)
        if not o:
            continue
        s.last_ts = max(s.last_ts, iso_to_epoch(o.get("timestamp", "")))
        t = o.get("type")
        if t == "session_info":
            s.name = o.get("name", "") or s.name
        elif t == "message":
            m = o.get("message", {})
            role = m.get("role")
            if role not in ("user", "assistant", "toolResult", "custom"):
                continue
            s.messages += 1
            last_role, last_is_error = role, False
            s.last_role = role
            if role == "user":
                txt = text_of(m.get("content")).strip()
                if txt:
                    s.last_user = txt
                    if not s.preview:
                        s.preview = clean_preview(txt)
            elif role == "assistant":
                s.last_stop_reason = m.get("stopReason")
                if s.last_stop_reason in ("error", "aborted"):
                    last_is_error = True
            elif role == "toolResult":
                if m.get("isError"):
                    last_is_error = True

    status = "idle"
    if last_is_error:
        status = "error"
    elif last_role == "user":
        status = "active"  # user spoke last -> awaiting/handling
    elif time.time() - s.last_ts < ACTIVE_WINDOW:
        status = "active"
    s.status = status
    return s


def process_table(timeout: float = 5) -> list[Proc]:
    """One bounded process snapshot, reused for status and pane liveness."""
    try:
        out = subprocess.run(
            ["ps", "-axo", "pid=,ppid=,command="],
            capture_output=True,
            text=True,
            timeout=timeout,
        ).stdout
    except Exception:
        return []
    procs = []
    for line in out.splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) < 2:
            continue
        try:
            pid, ppid = int(parts[0]), int(parts[1])
        except ValueError:
            continue
        command = parts[2] if len(parts) > 2 else ""
        procs.append(Proc(pid, ppid, command))
    return procs


def descendants(root_pids: set[int], procs: list[Proc]) -> set[int]:
    """Return the roots and every process below them in the process tree."""
    children: dict[int, list[int]] = {}
    for p in procs:
        children.setdefault(p.ppid, []).append(p.pid)
    result = set(root_pids)
    stack = list(root_pids)
    while stack:
        pid = stack.pop()
        for child in children.get(pid, []):
            if child not in result:
                result.add(child)
                stack.append(child)
    return result


def session_id_from_command(command: str) -> str | None:
    m = SESSION_ARG_RE.search(command)
    if not m:
        return None
    return m.group(1).strip("'\"").rstrip(";")


def command_is_pi(command: str) -> bool:
    """True when the command is an actual Pi process, not a shell wrapper.

    tmux starts a pane command through a shell, so the pane's root process
    command can contain `pi --session ...` even though the live Pi is a child.
    """
    try:
        tokens = shlex.split(command)
    except ValueError:
        tokens = command.split()
    if not tokens:
        return False
    for index, token in enumerate(tokens):
        if os.path.basename(token) != "pi":
            continue
        for shell_index in range(index):
            if os.path.basename(tokens[shell_index]) not in SHELL_PROCESS_NAMES:
                continue
            option = tokens[shell_index + 1]
            if option.startswith("-") and "c" in option[1:]:
                # The `pi ...` here is shell command text, not the executable.
                break
        else:
            return True
    return False


def running_session_processes(
    procs: list[Proc] | None = None,
) -> dict[str, set[int]]:
    """Map `--session`/`--session-id` process arguments to their PIDs."""
    procs = process_table() if procs is None else procs
    found: dict[str, set[int]] = {}
    for p in procs:
        if "--session" not in p.command:
            continue
        sid = session_id_from_command(p.command)
        if sid:
            found.setdefault(sid, set()).add(p.pid)
    return found


def running_session_ids(procs: list[Proc] | None = None) -> set[str]:
    """Best effort: sessions currently resumed with an explicit --session id."""
    return set(running_session_processes(procs))


def tmux(*args: str, timeout: float = 3):
    try:
        return subprocess.run(
            ["tmux", *args], capture_output=True, text=True, timeout=timeout
        )
    except Exception:
        return None


def tmux_ok(result) -> bool:
    return result is not None and result.returncode == 0


def panel_pane_id() -> str:
    target = os.environ.get("TMUX_PANE", "")
    args = ["display-message", "-p"]
    if target:
        args.extend(["-t", target])
    args.append("#{pane_id}")
    result = tmux(*args)
    if tmux_ok(result):
        return result.stdout.strip().splitlines()[0]
    return ""


def panel_window_id(panel_id: str) -> str:
    if not panel_id:
        return ""
    result = tmux("display-message", "-p", "-t", panel_id, "#{window_id}")
    if tmux_ok(result):
        return result.stdout.strip().splitlines()[0]
    return ""


def layout_panes(window_id: str = "") -> list[PaneInfo]:
    """Panes in the visible window plus every background-session pane."""
    fmt = (
        "#{pane_id}\t#{window_id}\t#{session_name}\t#{pane_pid}\t"
        "#{@sassy_session}"
    )
    result = tmux("list-panes", "-a", "-F", fmt)
    if not tmux_ok(result):
        return []
    panes = []
    for line in result.stdout.splitlines():
        parts = line.rstrip("\n").split("\t", 4)
        while len(parts) < 5:
            parts.append("")
        pane_id, win, name, pid, tag = parts
        if name != BG_SESSION and (not window_id or win != window_id):
            continue
        try:
            pane_pid = int(pid)
        except ValueError:
            pane_pid = 0
        panes.append(PaneInfo(pane_id, win, name, pane_pid, tag))
    return panes


def pane_map(panes: list[PaneInfo]) -> dict[str, str]:
    """Session id -> pane id from authoritative pane options."""
    return {p.tag: p.id for p in panes if p.tag}


def viewer_pane_id(
    panel_id: str, window_id: str, panes: list[PaneInfo], preferred: str = ""
) -> str:
    """The viewer is the other pane in the panel's visible window."""
    candidates = [
        p.id for p in panes if p.window_id == window_id and p.id != panel_id
    ]
    if preferred and preferred in candidates:
        return preferred
    return candidates[0] if candidates else ""


def set_pane_tag(pane_id: str, session_id: str) -> None:
    if pane_id and session_id:
        tmux("set-option", "-p", "-t", pane_id, PANE_OPTION, session_id)


def kill_pane(pane_id: str) -> None:
    tmux("kill-pane", "-t", pane_id)


def focus(target: str) -> None:
    """Hand keyboard focus to the viewer so the user can type immediately."""
    tmux("select-pane", "-t", target)


def swap_panes(source: str, target: str) -> bool:
    return tmux_ok(
        tmux("swap-pane", "-d", "-Z", "-s", source, "-t", target)
    )


def send_reload(pane_id: str) -> bool:
    """Type `/reload` into a Pi pane and submit it."""
    if not pane_id:
        return False
    if not tmux_ok(tmux("send-keys", "-t", pane_id, "-l", "/reload")):
        return False
    return tmux_ok(tmux("send-keys", "-t", pane_id, "Enter"))


def reload_managed_panes(
    groups: list[Group],
    preferred: str = "",
    procs: list[Proc] | None = None,
    panes: list[PaneInfo] | None = None,
    send=send_reload,
) -> int:
    """Send `/reload` to every managed pane that still runs Pi.

    This is deliberately unconditional about in-flight turns: reload is a
    user-requested hard refresh, so the panel does not second-guess it.
    """
    procs = process_table() if procs is None else procs
    if panes is None:
        _, panes, _ = visible_layout(groups, preferred, procs)
    count = 0
    for pane in panes:
        if not pane.tag or not pane_has_pi(pane, procs):
            continue
        if send(pane.id):
            count += 1
    return count


def _shell() -> str:
    return os.environ.get("SHELL", "/bin/bash")


def _pane_command(invoke: str) -> str:
    return f"{invoke}; exec {shlex.quote(_shell())}"


def _bg_window_name(tag: str = "") -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]", "", tag)[:24]
    return f"bg-{cleaned}" if cleaned else f"bg-{int(time.time() * 1000)}"


def _spawn_bg_window(cwd: str, command: str, tag: str = "") -> str:
    """Create one hidden single-pane window in the background session."""
    name = _bg_window_name(tag)
    if tmux_ok(tmux("has-session", "-t", f"={BG_SESSION}")):
        target = ["new-window", "-t", f"={BG_SESSION}:"]
    else:
        target = ["new-session", "-s", BG_SESSION]
    result = tmux(
        *target, "-d", "-P", "-F", "#{pane_id} #{window_id}",
        "-n", name, "-c", cwd, command,
        timeout=5,
    )
    if not tmux_ok(result) or not result.stdout.strip():
        return ""
    pane_id = result.stdout.strip().split()[0]
    if tag:
        set_pane_tag(pane_id, tag)
    return pane_id


def spawn_session_pane(s: Session) -> str:
    invoke = f"pi --session {shlex.quote(s.id)}"
    return _spawn_bg_window(s.cwd, _pane_command(invoke), tag=s.id)


def spawn_new_session_pane(cwd: str, session_id: str | None = None) -> str:
    # Pi can create a session under a caller-chosen id, so the new pane is
    # tagged and adoptable immediately instead of waiting for a file to appear.
    session_id = session_id or str(uuid.uuid4())
    invoke = f"pi --session-id {shlex.quote(session_id)}"
    return _spawn_bg_window(cwd, _pane_command(invoke), tag=session_id)


def spawn_placeholder(cwd: str | None = None) -> str:
    cwd = cwd or os.path.expanduser("~")
    return _spawn_bg_window(cwd, f"exec {shlex.quote(_shell())}")


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


def pane_processes(pane: PaneInfo, procs: list[Proc]) -> list[Proc]:
    """Every process in this pane's subtree, including the pane process."""
    if not pane.pid:
        return []
    by_pid = {p.pid: p for p in procs}
    return [by_pid[pid] for pid in descendants({pane.pid}, procs)
            if pid in by_pid]


def pane_has_pi(pane: PaneInfo, procs: list[Proc]) -> bool:
    return any(command_is_pi(p.command) for p in pane_processes(pane, procs))


def pane_running_session(
    pane: PaneInfo, procs: list[Proc]
) -> str | None:
    # Prefer an actual Pi process over the pane shell's command text.
    processes = sorted(
        pane_processes(pane, procs),
        key=lambda p: (not command_is_pi(p.command), p.pid),
    )
    for p in processes:
        sid = session_id_from_command(p.command)
        if sid:
            return sid
    return None


def managed_process_pids(panes: list[PaneInfo], procs: list[Proc]) -> set[int]:
    return descendants({p.pid for p in panes if p.pid}, procs)


def find_managed_pane(
    session_id: str,
    panes: list[PaneInfo],
    procs: list[Proc],
    panel_id: str,
) -> PaneInfo | None:
    """Find a pane already running the session, even before it is tagged."""
    for pane in panes:
        if pane.id == panel_id or not pane.pid:
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
    """A session is external if its Pi process lives outside managed panes."""
    running = running_session_processes(procs) if running is None else running
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
    """Tag panes running a known session, e.g. after a panel restart."""
    sessions = session_index(groups)
    tags = {p.tag for p in panes if p.tag}
    changed = False
    for pane in panes:
        if pane.id == panel_id or pane.tag:
            continue
        sid = pane_running_session(pane, procs)
        s = find_session(sessions, sid or "")
        if not s or s.id in tags:
            continue
        set_pane_tag(pane.id, s.id)
        tags.add(s.id)
        changed = True
    return layout_panes(window_id) if changed else panes


def visible_layout(
    groups: list[Group] | None = None,
    preferred: str = "",
    procs: list[Proc] | None = None,
) -> tuple[str, list[PaneInfo], str]:
    """Panel and viewer panes, adopting untagged panes when groups are given."""
    panel_id = panel_pane_id()
    window_id = panel_window_id(panel_id)
    panes = layout_panes(window_id)
    if groups is not None and panel_id and panes:
        procs = process_table() if procs is None else procs
        panes = adopt_running_panes(groups, panes, procs, panel_id, window_id)
    viewer = viewer_pane_id(panel_id, window_id, panes, preferred)
    return panel_id, panes, viewer


def refresh_hidden(
    groups: list[Group],
    reap_state: dict[str, float],
    preferred: str = "",
) -> list[PaneInfo]:
    """Adopt tagged panes and reap completed/exited background sessions."""
    procs = process_table()
    _, panes, _ = visible_layout(groups, preferred, procs)
    reap_hidden(panes, groups, procs, reap_state)
    return panes


def reap_hidden(
    panes: list[PaneInfo],
    groups: list[Group],
    procs: list[Proc],
    reap_state: dict[str, float],
) -> None:
    """Remove hidden panes with no Pi left, or finished with their turn."""
    sessions = session_index(groups)
    now = time.time()
    hidden_ids = set()
    for pane in panes:
        if pane.session_name != BG_SESSION:
            continue
        hidden_ids.add(pane.id)
        s = find_session(sessions, pane.tag)
        if not pane_has_pi(pane, procs):
            # The Pi already exited: remove the leftover shell/window too.
            kill_pane(pane.id)
            reap_state.pop(pane.id, None)
            continue
        if s is None or not s.is_complete():
            reap_state.pop(pane.id, None)
            continue
        first_seen = reap_state.get(pane.id)
        if first_seen is None:
            reap_state[pane.id] = now
        elif now - first_seen >= REAP_GRACE:
            kill_pane(pane.id)
            reap_state.pop(pane.id, None)
    for pane_id in list(reap_state):
        if pane_id not in hidden_ids:
            reap_state.pop(pane_id, None)


def _write_state(session_id: str) -> None:
    try:
        with open(STATE_FILE, "w") as f:
            f.write(session_id)
    except OSError:
        pass


def _present_pane(
    pane_id: str,
    viewer: str,
    reap_state: dict[str, float],
) -> str | None:
    """Bring a live hidden pane into the viewer by swapping, never respawning."""
    if not swap_panes(pane_id, viewer):
        return "Could not swap the session pane into the viewer"
    panel_id = panel_pane_id()
    window_id = panel_window_id(panel_id)
    after = layout_panes(window_id)
    # Start the normal reap grace for the displaced pane. The file may still
    # describe the previous turn, so never kill it just because it looks done.
    reap_hidden(after, load_groups(), process_table(), reap_state)
    focus(pane_id)
    return None


def present_session(
    s: Session,
    groups: list[Group] | None = None,
    preferred: str = "",
    reap_state: dict[str, float] | None = None,
) -> str | None:
    """Show a session in the viewer, keeping the displaced process alive."""
    groups = load_groups() if groups is None else groups
    reap_state = {} if reap_state is None else reap_state
    procs = process_table()
    panel_id, panes, viewer = visible_layout(groups, preferred, procs)
    if not panel_id or not panes:
        return "No tmux layout found for the control panel"
    if not viewer:
        return "No session viewer pane found"

    panes_by_id = {p.id: p for p in panes}
    tags = pane_map(panes)
    target_id = tags.get(s.id)
    if not target_id:
        pane = find_managed_pane(s.id, panes, procs, panel_id)
        if pane:
            target_id = pane.id
            set_pane_tag(pane.id, s.id)

    if target_id:
        target_pane = panes_by_id[target_id]
        if not pane_has_pi(target_pane, procs):
            # The tagged pane is only a leftover shell. Replace it rather than
            # presenting a dead pane (hidden ones are normally reaped first).
            if target_id != viewer:
                kill_pane(target_id)
            target_id = None

    if target_id == viewer:
        set_pane_tag(viewer, s.id)
        _write_state(s.id)
        focus(viewer)
        return None

    if not target_id and external_session_running(
        s.id, panes, procs, running_session_processes(procs)
    ):
        return f"{s.title[:36]} is already running outside sassy-pi"

    if target_id:
        warning = _present_pane(target_id, viewer, reap_state)
    else:
        new_pane = spawn_session_pane(s)
        if not new_pane:
            return "Could not start the session in the background"
        warning = _present_pane(new_pane, viewer, reap_state)
        if warning:
            kill_pane(new_pane)
            return warning

    if not warning:
        _write_state(s.id)
    return warning


def new_session(
    target: str,
    cwd: str,
    groups: list[Group] | None = None,
    reap_state: dict[str, float] | None = None,
) -> str | None:
    """Start a fresh Pi in the viewer via the background swap path."""
    groups = load_groups() if groups is None else groups
    reap_state = {} if reap_state is None else reap_state
    panel_id, panes, viewer = visible_layout(preferred=target)
    if not panel_id or not panes:
        return "No tmux layout found for the control panel"
    if not viewer:
        return "No session viewer pane found"
    session_id = str(uuid.uuid4())
    new_pane = spawn_new_session_pane(cwd, session_id)
    if not new_pane:
        return "Could not start a new session"
    warning = _present_pane(new_pane, viewer, reap_state)
    if warning:
        kill_pane(new_pane)
        return warning
    _write_state(session_id)
    return None


def preserve_active_on_quit(
    groups: list[Group] | None = None,
    preferred: str = "",
    reap_state: dict[str, float] | None = None,
) -> None:
    """Move a live viewer session into the background before quitting.

    The panel's cached groups can be seconds old, so the decision is made from
    a fresh process table and session store. A live Pi is authoritative: it is
    preserved even if the last recorded assistant message looks complete.
    """
    # Ignore the caller's cached groups; they may predate the current turn.
    groups = load_groups()
    reap_state = {} if reap_state is None else reap_state
    procs = process_table()
    panel_id, panes, viewer = visible_layout(groups, preferred, procs)
    if not panel_id or not panes or not viewer:
        return
    pane = next((p for p in panes if p.id == viewer), None)
    if not pane:
        return

    # Completed sessions with no live Pi do not need preserving. Conversely, a
    # live Pi must survive even when the JSONL still shows a terminal stop.
    if not pane_has_pi(pane, procs):
        return

    sessions = session_index(groups)
    running_id = pane_running_session(pane, procs)
    s = find_session(sessions, pane.tag or running_id or "")
    session_id = pane.tag or running_id or (s.id if s else "")
    if session_id:
        set_pane_tag(viewer, session_id)
    placeholder = spawn_placeholder(s.cwd if s else None)
    if not placeholder:
        return
    if not swap_panes(placeholder, viewer):
        kill_pane(placeholder)
        return
    # The working pane is now hidden in the background window; the temporary
    # placeholder shell takes its place in the visible window.
    focus(placeholder)


def load_groups() -> list[Group]:
    running = running_session_ids()
    groups: dict[str, Group] = {}
    try:
        dirs = [d for d in os.listdir(SESSIONS_DIR)
                if os.path.isdir(os.path.join(SESSIONS_DIR, d))]
    except OSError:
        return []
    for d in dirs:
        dpath = os.path.join(SESSIONS_DIR, d)
        for fn in os.listdir(dpath):
            if not fn.endswith(".jsonl"):
                continue
            s = parse_session(os.path.join(dpath, fn))
            if not s:
                continue
            if any(s.id.startswith(r) for r in running):
                s.status = "running"
            groups.setdefault(s.cwd, Group(s.cwd)).sessions.append(s)
    result = list(groups.values())
    for g in result:
        g.sessions.sort(key=lambda s: s.last_ts, reverse=True)
    result.sort(key=lambda g: g.last_ts, reverse=True)
    return result


def rel_time(ts: float) -> str:
    if not ts:
        return "?"
    d = time.time() - ts
    if d < 60:
        return "now"
    if d < 3600:
        return f"{int(d // 60)}m"
    if d < 86400:
        return f"{int(d // 3600)}h"
    return f"{int(d // 86400)}d"


def shorten_cwd(cwd: str) -> str:
    home = os.path.expanduser("~")
    return "~" + cwd[len(home):] if cwd.startswith(home) else cwd


def rows_for(groups: list[Group], flt: str, collapsed: set[str] | None = None):
    """Yield (kind, group, session) rows honoring filter and collapse state."""
    collapsed = collapsed or set()
    for g in groups:
        match_sessions = [
            s
            for s in g.sessions
            if not flt
            or flt.lower() in s.title.lower()
            or flt.lower() in s.id.lower()
            or flt.lower() in s.last_user.lower()
        ]
        gm = not flt or flt.lower() in g.cwd.lower()
        if not match_sessions and not gm:
            continue
        yield ("group", g, None)
        # A filter always expands groups so matches stay visible.
        if g.cwd in collapsed and not flt:
            continue
        for s in match_sessions:
            yield ("session", g, s)


def group_collapsed(g: Group, collapsed: set[str], flt: str) -> bool:
    return g.cwd in collapsed and not flt


FOOTER_ITEMS = (
    "↑/↓ move",
    "⏎ present",
    "n new",
    "/ filter",
    "r reload",
    "q quit",
)


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


def row_key(item):
    """Stable identity for a row, so selection survives re-sorting."""
    kind, g, s = item
    if kind == "session":
        return ("session", s.id, g.cwd)
    return ("group", g.cwd)


def cursor_for_key(rows, key) -> int:
    """Index of the row matching *key*, falling back to its group or top."""
    if key is None:
        return 0
    for i, item in enumerate(rows):
        if row_key(item) == key:
            return i
    if key[0] == "session":
        for i, item in enumerate(rows):
            if item[0] == "group" and item[1].cwd == key[2]:
                return i
    return 0


def build_lines(items, w, collapsed: set[str], flt: str):
    """Lay out items into single display lines (one line per item).

    Returns (lines, starts, heights) where each line is
    (text, kind, item_index) and starts/heights map an item index to its
    first line and line count.
    """
    lines = []
    starts: dict[int, int] = {}
    heights: dict[int, int] = {}
    for idx, (kind, g, s) in enumerate(items):
        starts[idx] = len(lines)
        if kind == "group":
            arrow = "▸" if group_collapsed(g, collapsed, flt) else "▾"
            label = f"{arrow} {shorten_cwd(g.cwd)}  ({len(g.sessions)})"
            if len(label) > w - 1:
                label = label[: max(1, w - 2)] + "…"
            lines.append((label, "group", idx))
        else:
            sym, _ = STATUS[s.status]
            suffix = rel_time(s.last_ts)
            prefix = f" {sym} "
            tw = max(4, w - len(prefix) - len(suffix) - 2)
            title = s.title
            if len(title) > tw:
                title = title[: max(1, tw - 1)] + "…"
            body = prefix + title
            pad = max(1, w - len(body) - len(suffix) - 1)
            lines.append((body + " " * pad + suffix, "session", idx))
        heights[idx] = len(lines) - starts[idx]
    return lines, starts, heights


def draw(stdscr, groups, flt, cursor, offset, collapsed, message=""):
    stdscr.erase()
    h, w = stdscr.getmaxyx()
    title = f" Pi sessions — {sum(len(g.sessions) for g in groups)} sessions "
    stdscr.addnstr(0, 0, title.ljust(w - 1), w - 1, curses.A_REVERSE | curses.A_BOLD)
    if flt:
        stdscr.addnstr(1, 0, f" filter: {flt}", w - 1, curses.A_DIM)

    keys = footer_lines(FOOTER_ITEMS, w)
    footer_h = len(keys)
    items = list(rows_for(groups, flt, collapsed))
    list_top = 2 if flt else 1
    avail = max(1, h - list_top - footer_h)
    if items:
        cursor = min(max(cursor, 0), len(items) - 1)
    else:
        cursor = 0

    lines, starts, heights = build_lines(items, w, collapsed, flt)

    # Keep the whole selected item (which may span several lines) visible.
    if items:
        start = starts[cursor]
        end = start + heights[cursor]
        if start < offset:
            offset = start
        if end > offset + avail:
            offset = end - avail
    offset = max(0, min(offset, max(0, len(lines) - avail)))

    for i in range(avail):
        li = offset + i
        if li >= len(lines):
            break
        text, kind, idx = lines[li]
        sel = idx == cursor
        attr = curses.A_REVERSE if sel else curses.A_NORMAL
        if kind == "group":
            stdscr.addnstr(i + list_top, 0, text.ljust(w - 1), w - 1,
                           attr | curses.A_BOLD
                           | (0 if sel else curses.color_pair(5)))
        else:
            s = items[idx][2]
            _, color = STATUS[s.status]
            stdscr.addnstr(i + list_top, 0, text.ljust(w - 1), w - 1,
                           attr | (0 if sel else curses.color_pair(color)))

    footer = footer_lines([message.strip()], w)[:footer_h] if message else keys
    for i in range(footer_h):
        text = footer[i] if i < len(footer) else ""
        stdscr.addnstr(
            h - footer_h + i, 0, text.ljust(w - 1), w - 1,
            curses.A_REVERSE | (curses.A_BOLD if message else 0),
        )
    stdscr.refresh()
    return cursor, offset


def prompt_filter(stdscr, current):
    h, w = stdscr.getmaxyx()
    curses.echo()
    curses.curs_set(1)
    stdscr.addnstr(h - 1, 0, " filter: ", w - 1, curses.A_REVERSE)
    stdscr.move(h - 1, 9)
    try:
        raw = stdscr.getstr(h - 1, 9, w - 10).decode("utf-8", "replace")
    except Exception:
        raw = current
    curses.noecho()
    curses.curs_set(0)
    return raw


def main_curses(stdscr, target: str):
    curses.curs_set(0)
    curses.start_color()
    curses.use_default_colors()
    curses.init_pair(1, curses.COLOR_GREEN, -1)   # running
    curses.init_pair(2, curses.COLOR_RED, -1)     # error
    curses.init_pair(3, curses.COLOR_YELLOW, -1)  # active
    curses.init_pair(4, curses.COLOR_BLUE, -1)    # idle
    curses.init_pair(5, curses.COLOR_CYAN, -1)    # group
    stdscr.timeout(2000)

    groups = load_groups()
    flt = ""
    cursor = offset = 0
    selected = None  # row_key() of the highlighted row, stable across sorts
    collapsed: set[str] = set()
    reap_state: dict[str, float] = {}
    message = ""
    message_until = 0.0
    while True:
        if message and time.time() >= message_until:
            message = ""
        # Re-derive the cursor from the selected row's identity, not its old
        # index: a session that starts working moves to the top of the list.
        rows = list(rows_for(groups, flt, collapsed))
        cursor = cursor_for_key(rows, selected)
        cursor, offset = draw(
            stdscr, groups, flt, cursor, offset, collapsed, message
        )
        try:
            ch = stdscr.getch()
        except KeyboardInterrupt:
            preserve_active_on_quit(groups, target, reap_state)
            break
        if ch == -1:
            groups = load_groups()
            refresh_hidden(groups, reap_state, target)
            continue
        rows = list(rows_for(groups, flt, collapsed))
        if ch in (curses.KEY_UP, ord("k")):
            cursor = max(0, cursor - 1)
        elif ch in (curses.KEY_DOWN, ord("j")):
            cursor = min(len(rows) - 1, cursor + 1)
        elif ch in (ord("g"), curses.KEY_HOME):
            cursor = 0
        elif ch in (ord("G"), curses.KEY_END):
            cursor = max(0, len(rows) - 1)
        elif ch == ord("q"):
            preserve_active_on_quit(groups, target, reap_state)
            break
        elif ch == ord("r"):
            groups = load_groups()
            refresh_hidden(groups, reap_state, target)
            count = reload_managed_panes(groups, target)
            if count:
                noun = "session" if count == 1 else "sessions"
                message = f" Reloading {count} {noun} "
            else:
                message = " No live sessions to reload "
            message_until = time.time() + 4
            continue
        elif ch == ord("/"):
            flt = prompt_filter(stdscr, flt)
            cursor = 0
            selected = None
            continue
        elif ch == ord("n"):
            if 0 <= cursor < len(rows):
                warning = new_session(
                    target, rows[cursor][1].cwd, groups, reap_state
                )
                if warning:
                    message = f" {warning} "
                    message_until = time.time() + 4
                groups = load_groups()
        elif ch in (curses.KEY_LEFT, ord("h")):
            if 0 <= cursor < len(rows) and rows[cursor][0] == "group":
                collapsed.add(rows[cursor][1].cwd)
        elif ch in (curses.KEY_RIGHT, ord("l")):
            if 0 <= cursor < len(rows) and rows[cursor][0] == "group":
                collapsed.discard(rows[cursor][1].cwd)
        elif ch in (curses.KEY_ENTER, 10, 13):
            if 0 <= cursor < len(rows):
                kind, g, s = rows[cursor]
                if kind == "group":
                    if g.cwd in collapsed:
                        collapsed.discard(g.cwd)
                    else:
                        collapsed.add(g.cwd)
                else:
                    warning = present_session(s, groups, target, reap_state)
                    if warning:
                        message = f" {warning} "
                        message_until = time.time() + 4
                    else:
                        message = ""
                    groups = load_groups()
        # Remember identity of the newly highlighted row for the next sort.
        if rows and 0 <= cursor < len(rows):
            selected = row_key(rows[cursor])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", required=False, help="tmux pane id to control")
    ap.add_argument("--list", action="store_true", help="print parsed list and exit")
    args = ap.parse_args()

    if args.list:
        for g in load_groups():
            print(shorten_cwd(g.cwd))
            for s in g.sessions:
                print(f"  {STATUS[s.status][0]} [{s.status:7}] "
                      f"{s.title[:60]:<60} {rel_time(s.last_ts):>4}  {s.id}")
        return

    if not args.target:
        ap.error("--target is required unless --list is given")

    curses.wrapper(main_curses, args.target)


if __name__ == "__main__":
    main()
