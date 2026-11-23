"""tmux plumbing: queries, layout, pane tags, swap, focus, spawn and reload.

Every tmux command the panel runs goes through here. This module makes no
reaping or presentation decision and sends no reload on its own: pane identity,
liveness, adoption, reaping and the reload target list live in `panel.panes`.
Its pane queries (`panel_pane_for`, `managed_panes`, `viewer_pane_id`) do read
the tags that say which managed and viewer panes exist, but never whether one
is live.
"""
from __future__ import annotations

import os
import re
import shlex
import subprocess
import time

from panel import core
from panel.core import PaneInfo


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
    lines = result.stdout.strip().splitlines() if tmux_ok(result) else []
    return lines[0] if lines else ""


def panel_window_id(panel_id: str) -> str:
    if not panel_id:
        return ""
    result = tmux("display-message", "-p", "-t", panel_id, "#{window_id}")
    if tmux_ok(result):
        return result.stdout.strip().splitlines()[0]
    return ""


PANE_FORMAT = (
    "#{pane_id}\t#{window_id}\t#{session_name}\t#{pane_pid}\t"
    "#{@sassy_session}"
)


def parse_panes(output: str) -> list[PaneInfo]:
    panes = []
    for line in output.splitlines():
        parts = line.rstrip("\n").split("\t", 4)
        while len(parts) < 5:
            parts.append("")
        pane_id, win, name, pid, tag = parts
        try:
            pane_pid = int(pid)
        except ValueError:
            pane_pid = 0
        panes.append(PaneInfo(pane_id, win, name, pane_pid, tag))
    return panes


def layout_panes(window_id: str = "") -> list[PaneInfo]:
    """Panes in the visible window plus every background-session pane."""
    result = tmux("list-panes", "-a", "-F", PANE_FORMAT)
    if not tmux_ok(result):
        return []
    return [
        p for p in parse_panes(result.stdout)
        if p.session_name == core.BG_SESSION
        or not window_id or p.window_id == window_id
    ]


def pane_map(panes: list[PaneInfo]) -> dict[str, str]:
    """Session id -> pane id from authoritative pane options."""
    return {p.tag: p.id for p in panes if p.tag}


def managed_panes() -> list[PaneInfo]:
    """Panes the panel controls, not counting the control panel itself."""
    panel_id = panel_pane_id()
    if not panel_id:
        return []
    window_id = panel_window_id(panel_id)
    return [p for p in layout_panes(window_id) if p.id != panel_id]


def managed_pane_tags() -> set[str]:
    """Session tags of the panes this panel controls, without adopting any."""
    return {p.tag for p in managed_panes() if p.tag}


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
        tmux("set-option", "-p", "-t", pane_id, core.PANE_OPTION, session_id)


def clear_pane_tag(pane_id: str) -> None:
    """Drop a pane's session identity after its session is deleted."""
    if pane_id:
        tmux("set-option", "-p", "-u", "-t", pane_id, core.PANE_OPTION)


def set_panel_tag(pane_id: str) -> None:
    """Mark this pane as the control panel so external bindings can find it."""
    if pane_id:
        tmux("set-option", "-p", "-t", pane_id, core.PANEL_OPTION, "1")


def panel_pane_for(pane_id: str) -> str:
    """The control panel pane in *pane_id*'s window, if a live one exists.

    The panel tags its own pane (`PANEL_OPTION`). Requiring a Python process in
    front keeps a stale tag (a crashed panel's pane reused by a shell) from
    being typed into.
    """
    result = tmux("display-message", "-p", "-t", pane_id, "#{window_id}")
    if not tmux_ok(result):
        return ""
    window_id = result.stdout.strip().splitlines()
    if not window_id or not window_id[0]:
        return ""
    fmt = f"#{{pane_id}}\t#{{pane_current_command}}\t#{{{core.PANEL_OPTION}}}"
    result = tmux("list-panes", "-t", window_id[0], "-F", fmt)
    if not tmux_ok(result):
        return ""
    for line in result.stdout.splitlines():
        parts = line.split("\t")
        while len(parts) < 3:
            parts.append("")
        pane, command, tag = parts[:3]
        if tag.strip() and command.lower().startswith("python"):
            return pane
    return ""


def request_new_session(pane_id: str) -> bool:
    """Ask the panel in *pane_id*'s window to start a session in its project.

    Sending the panel's no-picker `N` key keeps one spawn/present path and does not
    move focus, so the keyboard stays in the viewer.
    """
    panel_id = panel_pane_for(pane_id)
    if not panel_id:
        tmux("display-message", "sassy-code: no control panel in this window")
        return False
    return tmux_ok(tmux("send-keys", "-t", panel_id, "-l", core.NEW_NO_PICKER_KEY))


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


def _shell() -> str:
    return os.environ.get("SHELL", "/bin/bash")


def _pane_command(invoke: str) -> str:
    return f"{invoke}; exec {shlex.quote(_shell())}"


def _bg_window_name(tag: str = "") -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]", "", tag)[:24]
    return f"bg-{cleaned}" if cleaned else f"bg-{int(time.time() * 1000)}"


def _spawn_bg_window(cwd: str, command: str, tag: str) -> str:
    """Create one hidden single-pane window in the background session."""
    cwd = cwd or os.path.expanduser("~")
    name = _bg_window_name(tag)
    if tmux_ok(tmux("has-session", "-t", f"={core.BG_SESSION}")):
        target = ["new-window", "-t", f"={core.BG_SESSION}:"]
    else:
        target = ["new-session", "-s", core.BG_SESSION]
    result = tmux(
        *target, "-d", "-P", "-F", "#{pane_id} #{window_id}",
        "-n", name, "-c", cwd, command,
        timeout=5,
    )
    if not tmux_ok(result) or not result.stdout.strip():
        return ""
    pane_id = result.stdout.strip().split()[0]
    set_pane_tag(pane_id, tag)
    return pane_id
