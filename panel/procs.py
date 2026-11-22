"""Process-table snapshots and agent command detection."""
from __future__ import annotations

import os
import shlex
import subprocess

from panel.core import (
    CLAUDE_SESSION_ARG_RE,
    PI_SESSION_ARG_RE,
    SHELL_PROCESS_NAMES,
    Proc,
)


PS_ARGS = ["ps", "-axo", "pid=,ppid=,command="]


def process_table(timeout: float = 5) -> list[Proc]:
    """One bounded process snapshot, reused for status and pane liveness.

    `SASSY_CODE_PROCS_FIXTURE` names a file holding a `ps` snapshot in the same
    format and replaces the live one. Liveness (and with it `in progress` and
    `needs input`) otherwise depends on an agent process existing, so a demo
    store cannot show the working states without it.
    """
    fixture = os.environ.get("SASSY_CODE_PROCS_FIXTURE")
    if fixture:
        try:
            with open(fixture) as f:
                out = f.read()
        except OSError:
            return []
    else:
        try:
            out = subprocess.run(
                PS_ARGS,
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


def _tokens(command: str) -> list[str]:
    try:
        return shlex.split(command)
    except ValueError:
        return command.split()


def _is_shell_command_text(tokens: list[str], index: int) -> bool:
    """True when tokens[index] is inside a shell's `-c` command string."""
    for shell_index in range(index):
        if os.path.basename(tokens[shell_index]) not in SHELL_PROCESS_NAMES:
            continue
        for option in tokens[shell_index + 1:index]:
            if option.startswith("-") and "c" in option[1:]:
                return True
            if not option.startswith("-"):
                break
    return False


def _runs_program(command: str, names: set[str]) -> bool:
    """True when the command actually executes one of *names*.

    tmux starts a pane command through a shell, so the pane's root process
    command can contain `pi --session ...` or `claude --resume ...` even though
    the live agent is a child. Shell `-c` text is not the executable.
    """
    tokens = _tokens(command)
    for index, token in enumerate(tokens):
        if os.path.basename(token) not in names:
            continue
        if not _is_shell_command_text(tokens, index):
            return True
    return False


def command_is_pi(command: str) -> bool:
    return _runs_program(command, {"pi"})


def command_is_claude(command: str) -> bool:
    # Native installs run `claude`; npm installs run
    # `node .../@anthropic-ai/claude-code/cli.js`. A script or grep that merely
    # mentions that path is not the agent, so the hint only counts when the
    # executable is a JavaScript runtime.
    if _runs_program(command, {"claude"}):
        return True
    tokens = _tokens(command)
    for index, token in enumerate(tokens):
        if os.path.basename(token) not in {"node", "bun", "deno"}:
            continue
        if _is_shell_command_text(tokens, index):
            continue
        if any(
            "claude-code" in arg or "@anthropic-ai/claude" in arg
            for arg in tokens[index + 1:]
        ):
            return True
    return False


def command_harness(command: str) -> str | None:
    """Which supported harness a process command belongs to, if any."""
    if command_is_pi(command):
        return "pi"
    if command_is_claude(command):
        return "claude"
    return None


def session_id_from_command(
    command: str, harness: str | None = None
) -> str | None:
    harness = harness or command_harness(command) or "pi"
    pattern = (
        CLAUDE_SESSION_ARG_RE if harness == "claude" else PI_SESSION_ARG_RE
    )
    match = pattern.search(command)
    if not match:
        return None
    return match.group(1).strip("'\"").rstrip(";")


def running_session_processes(
    procs: list[Proc] | None = None,
) -> dict[str, set[int]]:
    """Map a resumed session argument to the PIDs running it."""
    procs = process_table() if procs is None else procs
    found: dict[str, set[int]] = {}
    for p in procs:
        harness = command_harness(p.command)
        if not harness:
            continue
        sid = session_id_from_command(p.command, harness)
        if sid:
            found.setdefault(sid, set()).add(p.pid)
    return found
