#!/usr/bin/env python3
"""Sassy Code session control panel.

Left pane: grouped list of all Pi and Claude Code sessions with status.
Right pane: the session currently selected, presented by swapping tmux panes.

Sessions that are switched away from stay alive in a dedicated background tmux
session (`sassy-code-bg`), one hidden single-pane window per live session.  The
panel never respawns a managed pane, so no in-flight agent process is ever
killed by switching.  This process runs in the left tmux pane and never blocks
on the viewer.
"""
from __future__ import annotations

import argparse
import curses
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
import unicodedata
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone

PI_SESSIONS_DIR = os.environ.get(
    "PI_CODING_AGENT_SESSION_DIR", os.path.expanduser("~/.pi/agent/sessions")
)
CLAUDE_SESSIONS_DIR = os.environ.get(
    "SASSY_CODE_CLAUDE_SESSION_DIR",
    os.path.join(
        os.environ.get("CLAUDE_CONFIG_DIR", os.path.expanduser("~/.claude")),
        "projects",
    ),
)
STATE_FILE = os.path.expanduser("~/.sassy-code-current")
ACTIVE_WINDOW = 300  # seconds -> "active"
SESSION_LIMIT = 5  # sessions shown per project before "… N more"

BG_SESSION = "sassy-code-bg"
DEFAULT_HARNESS = "pi"
# Small, quiet marks that say which harness owns a session.
HARNESS = {
    "pi": {"label": "Pi", "glyph": "π"},
    "claude": {"label": "Claude Code", "glyph": "✳"},
}
HARNESS_KEYS = tuple(HARNESS)
PANE_OPTION = "@sassy_session"
PANEL_OPTION = "@sassy_panel"
REAP_GRACE = 2.0
TERMINAL_STOP_REASONS = {
    # Pi
    "stop", "error", "aborted",
    # Claude Code (Anthropic stop reasons)
    "end_turn", "stop_sequence", "max_tokens", "refusal",
}

# status -> (symbol, color pair index)
STATUS = {
    "running": ("●", 1),
    "error": ("✗", 2),
    "active": ("◐", 3),
    "idle": ("·", 4),
}

PI_SESSION_ARG_RE = re.compile(r"--session(?:-id)?(?:=|\s+)(\S+)")
CLAUDE_SESSION_ARG_RE = re.compile(
    r"(?:--resume|--session-id|-r)(?:=|\s+)(\S+)"
)
CLAUDE_SESSION_FILE_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\.jsonl$"
)
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
    parent_path: str = ""
    harness: str = "pi"
    status: str = "idle"
    messages: int = 0
    last_role: str | None = None
    last_stop_reason: str | None = None
    last_error: bool = False
    # Background task ids announced but not yet closed (Claude Code only).
    open_tasks: frozenset[str] = frozenset()

    @property
    def title(self) -> str:
        return self.name or self.preview or "(empty session)"

    def is_complete(self) -> bool:
        """True once the latest turn has a terminal assistant stop reason."""
        if self.open_tasks:
            return False  # background work outlives the main thread's turn
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
    """Parse Pi's ISO-8601 timestamps as UTC.

    Pi writes UTC (`...Z`). Parsing them as local time shifted every age by the
    machine's UTC offset, which made a just-created session look hours old.
    """
    if not ts:
        return 0.0
    value = ts.strip()
    if value[-1:] in ("Z", "z"):
        value = value[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return 0.0
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


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
    """Turn a user turn into a one-line preview, skipping injected context."""
    txt = re.sub(r"\s+", " ", txt).strip()
    if txt.startswith("<skill"):
        # Prefer text the user typed after the injected skill body, if any.
        tail = re.sub(r"^.*?</skill>", "", txt, flags=re.S).strip()
        return tail or "(skill invocation)"
    if txt.startswith("<local-command-"):
        return ""
    # Claude Code records slash commands as tagged prompt text.
    txt = re.sub(r"</?command-(?:name|args|message)>?", " ", txt)
    return re.sub(r"\s+", " ", txt).strip()


def char_width(ch: str) -> int:
    """Terminal columns for one character: wide = 2, combining/zero-width = 0."""
    if unicodedata.combining(ch) or unicodedata.category(ch) in ("Mn", "Me", "Cf"):
        return 0
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


def display_width(text: str) -> int:
    """Columns for a string; U+FE0F widens the previous character to emoji."""
    total, prev = 0, 0
    for c in text:
        if c == "\ufe0f":
            if prev == 1:
                total += 1
                prev = 2
            continue
        prev = char_width(c)
        total += prev
    return total


def truncate_cols(text: str, cols: int) -> str:
    """Fit text into `cols` terminal columns, ending with `…` when cut."""
    if display_width(text) <= cols:
        return text
    if cols <= 0:
        return ""
    out = ""
    for ch in text:
        if display_width(out + ch) > cols - 1:
            break
        out += ch
    return out + "…"


def fit_cols(text: str, cols: int) -> str:
    """Truncate then right-pad with spaces to exactly `cols` columns."""
    text = truncate_cols(text, cols)
    return text + " " * max(0, cols - display_width(text))


# Harness-injected prompt wrappers that are never a human title.
CLAUDE_NON_PROMPT_PREFIXES = (
    "<task-notification", "<system-reminder", "<agent-message",
    "<local-command-", "<command-stdout", "Caveat:",
)
CLAUDE_TASK_ID_RE = re.compile(r"<task-id>\s*([^<\s]+)\s*</task-id>")
# Background launch results, matched at the start of the result body (group 1
# = task id) and only for the tool_use that launched them. The Agent wording is
# confirmed on disk; the Bash wording follows Claude Code's documented text.
CLAUDE_TASK_OPEN_RES = {
    "agent": re.compile(r"\A\s*Async agent launched successfully.*?agentId:\s*([\w-]+)", re.S),
    "bash": re.compile(r"\A\s*(?:Command )?running in background with ID:\s*([\w-]+)", re.I),
}
SYSTEM_REMINDER_RE = re.compile(r"<system-reminder>.*?</system-reminder>", re.S)


def claude_prompt_title(txt: str) -> str:
    """One-line title from a Claude prompt; "" when it is only harness text."""
    txt = SYSTEM_REMINDER_RE.sub(" ", txt).strip()
    if txt.startswith(CLAUDE_NON_PROMPT_PREFIXES):
        return ""
    m = re.search(r"<command-args>(.*?)</command-args>", txt, re.S)
    if m:
        txt = m.group(1)
    elif txt.startswith("<command-"):
        return ""  # bare slash command: a later prompt supplies the title
    # Pasted bodies are what the user typed about: keep the text, drop the tags.
    txt = re.sub(r"</?pasted_content[^>]*>", " ", txt)
    return clean_preview(txt)


def _claude_launches(content, launches: dict[str, str]) -> None:
    """Remember tool_use ids that start background work (id -> result kind)."""
    if not isinstance(content, list):
        return
    for b in content:
        if not isinstance(b, dict) or b.get("type") != "tool_use":
            continue
        args = b.get("input")
        if b.get("name") in ("Agent", "Task"):
            launches[b.get("id")] = "agent"
        elif b.get("name") == "Bash" and isinstance(args, dict) \
                and args.get("run_in_background"):
            launches[b.get("id")] = "bash"


def _claude_task_ids(content, launches: dict[str, str]) -> tuple[set[str], set[str]]:
    """(opened, closed) background task ids announced by one entry."""
    opened: set[str] = set()
    closed: set[str] = set()
    if isinstance(content, list):
        for b in content:
            if isinstance(b, dict) and b.get("type") == "tool_result":
                kind = launches.get(b.get("tool_use_id"))
                if kind:
                    body = b.get("content")
                    body = body if isinstance(body, str) else text_of(body)
                    opened.update(CLAUDE_TASK_OPEN_RES[kind].findall(body))
        blocks = [b.get("text", "") for b in content
                  if isinstance(b, dict) and b.get("type") == "text"]
    else:
        blocks = [content] if isinstance(content, str) else []
    for block in blocks:
        block = SYSTEM_REMINDER_RE.sub(" ", block)
        if block.lstrip().startswith("<task-notification"):
            closed.update(CLAUDE_TASK_ID_RE.findall(block))
    return opened, closed


def _load(line: str):
    try:
        return json.loads(line)
    except Exception:
        return None


def _read_lines(path: str) -> list[str] | None:
    try:
        with open(path, "r", errors="replace") as f:
            return f.readlines()
    except OSError:
        return None


def parse_pi_session(path: str) -> Session | None:
    lines = _read_lines(path)
    if not lines:
        return None

    header = _load(lines[0])
    if not header or header.get("type") != "session":
        return None

    s = Session(
        path=path,
        id=header.get("id", ""),
        cwd=header.get("cwd", ""),
        created=iso_to_epoch(header.get("timestamp", "")),
        last_ts=iso_to_epoch(header.get("timestamp", "")),
        parent_path=header.get("parentSession", "") or "",
        harness="pi",
    )

    last_role = None
    last_is_error = False
    for line in lines[1:]:
        o = _load(line)
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

    s.last_error = last_is_error
    s.status = _infer_status(s)
    return s


def _claude_content_types(content) -> set[str]:
    if not isinstance(content, list):
        return set()
    return {
        b.get("type", "")
        for b in content
        if isinstance(b, dict)
    }


def _claude_is_human_prompt(entry: dict, content) -> bool:
    """True when a top-level `user` entry is a turn, not tool output/meta.

    Claude Code marks injected context with `isMeta` and echoes slash commands
    as `<command-...>` / `<local-command-...>` text. Anything else that is not
    a `tool_result` is treated as a user turn, which errs on the side of
    "still working" so a live pane is never reaped from a misread.
    """
    if entry.get("isMeta"):
        return False
    text = text_of(content).strip()
    if not text:
        return bool(content) and not isinstance(content, str)
    return not text.startswith(("<local-command-", "<command-"))


def _claude_cwd_from_dir(path: str) -> str:
    """Best-effort cwd from an encoded project directory name.

    Claude Code encodes a cwd by replacing separators and punctuation with
    `-`, which is lossy. Real session files carry `cwd` on nearly every entry;
    this is only the fallback for a file that does not.
    """
    name = os.path.basename(os.path.dirname(path))
    if not name.startswith("-"):
        return ""
    return "/" + name.strip("-").replace("-", "/")


def parse_claude_session(path: str) -> Session | None:
    """Parse a Claude Code transcript (`~/.claude/projects/<cwd>/<uuid>.jsonl`).

    Claude Code has no header line: the first entry that carries `sessionId`,
    `cwd`, or `timestamp` supplies the identity. Subagent sidechains live in
    the same file, so they are skipped for preview and status inference; only
    the main thread decides what the session is doing.
    """
    lines = _read_lines(path)
    if not lines:
        return None

    file_id = os.path.basename(path)[: -len(".jsonl")]
    s = Session(
        path=path,
        id=file_id if CLAUDE_SESSION_FILE_RE.match(os.path.basename(path)) else "",
        cwd="",
        created=0.0,
        last_ts=0.0,
        harness="claude",
    )

    last_role = None
    last_is_error = False
    summary = custom = ai = ""
    opened_all: set[str] = set()
    closed_all: set[str] = set()
    launches: dict[str, str] = {}
    for line in lines:
        o = _load(line)
        if not o:
            continue
        ts = iso_to_epoch(o.get("timestamp", ""))
        if ts:
            s.last_ts = max(s.last_ts, ts)
            if not s.created:
                s.created = ts
        if o.get("type") == "queue-operation":
            # Notifications are also queued while the turn is still running.
            closed_all |= _claude_task_ids(o.get("content"), launches)[1]
            continue
        if o.get("isSidechain"):
            continue
        # Identity comes from the main thread only: a subagent entry may carry
        # a different session id and must not rename the parent session.
        if o.get("sessionId"):
            s.id = str(o["sessionId"])
        if o.get("cwd") and not s.cwd:
            s.cwd = str(o["cwd"])  # launch cwd: later `cd`s must not regroup

        t = o.get("type")
        if t == "summary":
            summary = o.get("summary", "") or summary
        elif t == "custom-title":
            custom = o.get("customTitle", "") or custom
        elif t == "ai-title":
            ai = o.get("aiTitle", "") or ai
        elif t == "user":
            m = o.get("message", {})
            content = m.get("content")
            opened, closed = _claude_task_ids(content, launches)
            opened_all |= opened
            closed_all |= closed
            if "tool_result" in _claude_content_types(content):
                s.messages += 1
                last_role, last_is_error = "toolResult", False
                s.last_role, s.last_stop_reason = last_role, None
            elif _claude_is_human_prompt(o, content):
                txt = text_of(content).strip()
                s.messages += 1
                last_role, last_is_error = "user", False
                s.last_role, s.last_stop_reason = last_role, None
                if txt:
                    s.last_user = txt
                    if not s.preview:
                        s.preview = claude_prompt_title(txt)
            elif not s.preview and not o.get("isMeta"):
                # Slash-command echoes are not turns but carry a title.
                txt = text_of(content).strip()
                if txt.startswith("<command-"):
                    s.preview = claude_prompt_title(txt)
        elif t == "assistant":
            m = o.get("message", {})
            _claude_launches(m.get("content"), launches)
            s.messages += 1
            last_role = "assistant"
            s.last_role = last_role
            stop = m.get("stop_reason")
            if o.get("isApiErrorMessage") or o.get("error"):
                last_is_error = True
                s.last_stop_reason = "error"
            else:
                last_is_error = False
                s.last_stop_reason = stop

    if not s.id:
        # A transcript without `sessionId` is not an addressable session.
        return None
    if not s.cwd:
        s.cwd = _claude_cwd_from_dir(path)
    if not s.created:
        s.created = s.last_ts
    # A user rename beats the generated title, which beats the legacy summary.
    s.name = next(
        (clean_preview(c) for c in (custom, ai, summary) if c.strip()), ""
    )
    s.open_tasks = frozenset(opened_all - closed_all)
    s.last_error = last_is_error
    s.status = _infer_status(s)
    return s


def parse_session(path: str, harness: str = "pi") -> Session | None:
    if harness == "claude":
        return parse_claude_session(path)
    return parse_pi_session(path)


def _infer_status(s: Session) -> str:
    if s.last_error:
        return "error"
    if s.last_role in ("user", "toolResult"):
        return "active"  # user or tool spoke last -> awaiting/handling
    if s.last_stop_reason == "tool_use":
        return "active"  # Claude is mid-tool-use
    if time.time() - s.last_ts < ACTIVE_WINDOW:
        return "active"
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
        # The active window is clock-relative, so an unchanged file can still
        # age from active to idle between refreshes.
        s.status = _infer_status(s)
        return s
    s = parse_session(path, harness)
    if s is None:
        return None
    _PARSE_CACHE[key] = (st.st_mtime_ns, st.st_size, s)
    return replace(s)


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
        tmux("set-option", "-p", "-t", pane_id, PANE_OPTION, session_id)


def set_panel_tag(pane_id: str) -> None:
    """Mark this pane as the control panel so external bindings can find it."""
    if pane_id:
        tmux("set-option", "-p", "-t", pane_id, PANEL_OPTION, "1")


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
    fmt = f"#{{pane_id}}\t#{{pane_current_command}}\t#{{{PANEL_OPTION}}}"
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

    Sending the panel's own `n` key keeps one spawn/present path and does not
    move focus, so the keyboard stays in the viewer.
    """
    panel_id = panel_pane_for(pane_id)
    if not panel_id:
        tmux("display-message", "sassy-code: no control panel in this window")
        return False
    return tmux_ok(tmux("send-keys", "-t", panel_id, "-l", "n"))


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
    cwd = cwd or os.path.expanduser("~")
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


def default_harness() -> str:
    """Harness for a brand-new session when the project has no hint.

    `SASSY_CODE_NEW_HARNESS` wins when valid; otherwise use whichever harness
    is installed, so a Claude-only machine does not try to run `pi`.
    """
    value = os.environ.get("SASSY_CODE_NEW_HARNESS", "").strip().lower()
    if value in HARNESS:
        return value
    for candidate in HARNESS_KEYS:
        if shutil.which(candidate):
            return candidate
    return DEFAULT_HARNESS


def session_invoke(s: Session) -> str:
    """The command that resumes *s* in a fresh pane."""
    if s.harness == "claude":
        return f"claude --resume {shlex.quote(s.id)}"
    return f"pi --session {shlex.quote(s.id)}"


def spawn_session_pane(s: Session) -> str:
    return _spawn_bg_window(s.cwd, _pane_command(session_invoke(s)), tag=s.id)


def spawn_new_session_pane(
    cwd: str, session_id: str | None = None, harness: str = DEFAULT_HARNESS
) -> str:
    # Both harnesses can create a session under a caller-chosen id, so the new
    # pane is tagged and adoptable immediately instead of waiting for a file.
    session_id = session_id or str(uuid.uuid4())
    if harness == "claude":
        invoke = f"claude --session-id {shlex.quote(session_id)}"
    else:
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
    return [by_pid[pid] for pid in descendants({pane.pid}, procs)
            if pid in by_pid]


def pane_has_agent(pane: PaneInfo, procs: list[Proc]) -> bool:
    """True when a supported harness is alive anywhere in the pane."""
    return any(
        command_harness(p.command) for p in pane_processes(pane, procs)
    )


def pane_has_pi(pane: PaneInfo, procs: list[Proc]) -> bool:
    return any(command_is_pi(p.command) for p in pane_processes(pane, procs))


def pane_running_session(
    pane: PaneInfo, procs: list[Proc]
) -> str | None:
    # Prefer an actual agent process over the pane shell's command text.
    processes = sorted(
        pane_processes(pane, procs),
        key=lambda p: (not command_harness(p.command), p.pid),
    )
    for p in processes:
        harness = command_harness(p.command)
        if not harness:
            continue
        sid = session_id_from_command(p.command, harness)
        if sid:
            return sid
    return None


def managed_process_pids(panes: list[PaneInfo], procs: list[Proc]) -> set[int]:
    return descendants({p.pid for p in panes if p.pid}, procs)


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
    claimed |= set(running_session_processes(procs))
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
        set_pane_tag(pane.id, target.id)
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
) -> tuple[list[PaneInfo], str]:
    """Adopt tagged panes and reap completed/exited background sessions."""
    procs = process_table()
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
        if pane.session_name != BG_SESSION:
            continue
        hidden_ids.add(pane.id)
        s = find_session(sessions, pane.tag)
        if not pane_has_agent(pane, procs):
            # The agent already exited: remove the leftover shell/window too.
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


def _read_state() -> str:
    try:
        with open(STATE_FILE) as f:
            return f.read().strip()
    except OSError:
        return ""


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
    # A session can have more than one tagged pane if a duplicate was spawned by
    # an older panel. Prefer the pane already showing it so Enter only focuses
    # the live session instead of swapping a duplicate into the viewer.
    claimed = [p for p in panes if p.tag == s.id and p.id != panel_id]
    if claimed:
        target_id = next((p.id for p in claimed if p.id == viewer), "")
        if not target_id:
            target_id = next(
                (p.id for p in claimed if p.session_name != BG_SESSION),
                claimed[0].id,
            )
    else:
        target_id = ""
    if not target_id:
        pane = find_managed_pane(s.id, panes, procs, panel_id)
        if pane:
            target_id = pane.id
            set_pane_tag(pane.id, s.id)

    if target_id:
        target_pane = panes_by_id[target_id]
        if not pane_has_agent(target_pane, procs):
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
        return f"{s.title[:36]} is already running outside sassy-code"

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
    harness: str | None = None,
) -> str | None:
    """Start a fresh agent in the viewer via the background swap path."""
    groups = load_groups() if groups is None else groups
    reap_state = {} if reap_state is None else reap_state
    harness = harness if harness in HARNESS else default_harness()
    panel_id, panes, viewer = visible_layout(preferred=target)
    if not panel_id or not panes:
        return "No tmux layout found for the control panel"
    if not viewer:
        return "No session viewer pane found"
    session_id = str(uuid.uuid4())
    new_pane = spawn_new_session_pane(cwd, session_id, harness)
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
    a fresh process table and session store. A live agent is authoritative: it
    is preserved even if the last recorded assistant message looks complete.
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

    # Completed sessions with no live agent do not need preserving. Conversely,
    # a live agent must survive even when the JSONL still shows a terminal stop.
    if not pane_has_agent(pane, procs):
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
            if harness == "claude" and not CLAUDE_SESSION_FILE_RE.match(
                filename
            ):
                # Claude keeps subagent transcripts beside sessions; only a
                # UUID-named file is a resumable session.
                continue
            files.append((os.path.join(dpath, filename), harness))
    return files


def session_files() -> list[tuple[str, str]]:
    return _store_files(PI_SESSIONS_DIR, "pi") + _store_files(
        CLAUDE_SESSIONS_DIR, "claude"
    )


def load_groups() -> list[Group]:
    procs = process_table()
    panes = managed_panes()
    running = running_session_processes(procs)
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
    children = session_children(sessions)
    live = set()
    for pane in panes:
        target = pane_switch_target(pane, sessions, children, tagged, procs)
        if target is not None:
            live.add(target.id)
    # Command lines only identify sessions whose agent runs outside managed
    # panes, so a pane's stale argv cannot double-count as still running.
    managed = managed_process_pids(panes, procs)
    external = {
        sid: {pid for pid in pids if pid not in managed}
        for sid, pids in running.items()
    }
    external = {sid: pids for sid, pids in external.items() if pids}
    claimed = tagged | set(external)
    for sid in external:
        s = sessions.get(sid) or find_session(sessions, sid)
        if s is not None:
            live.add(switched_session(s, children, claimed - {sid}).id)
    for s in sessions.values():
        if s.id in live:
            s.status = "running"

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


def rows_for(
    groups: list[Group],
    flt: str,
    collapsed: set[str] | None = None,
    expanded: set[str] | None = None,
    limit: int = SESSION_LIMIT,
):
    """Yield (kind, group, payload) rows honoring filter, collapse and limit.

    A project shows at most *limit* sessions, plus a `more` row to reveal the
    rest. Live sessions stay visible even when they fall past the limit. A
    filter shows every match, and a collapsed project shows only its heading.
    """
    collapsed = collapsed or set()
    expanded = expanded or set()
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
            or flt_l in HARNESS[s.harness]["label"].lower()
        ]
        gm = not flt or flt_l in g.cwd.lower()
        if not match_sessions and not gm:
            continue
        yield ("group", g, None)
        # A filter always expands groups so matches stay visible.
        if g.cwd in collapsed and not flt:
            continue
        limited = (
            not flt and g.cwd not in expanded and len(match_sessions) > limit
        )
        if limited:
            shown = match_sessions[:limit]
            shown_ids = {s.id for s in shown}
            live = [
                s
                for s in match_sessions[limit:]
                if s.status == "running" and s.id not in shown_ids
            ]
            if live:
                shown = sorted(
                    shown + live, key=lambda s: s.last_ts, reverse=True
                )
            hidden = len(match_sessions) - len(shown)
        else:
            shown = match_sessions
            hidden = 0
        for s in shown:
            yield ("session", g, s)
        if not flt and len(match_sessions) > limit:
            # Kept when expanded, so the same row collapses the list again.
            yield ("more", g, hidden)


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
    if kind == "more":
        return ("more", g.cwd)
    return ("group", g.cwd)


def cursor_for_key(rows, key) -> int:
    """Index of the row matching *key*, falling back within its group."""
    if key is None:
        return 0
    for i, item in enumerate(rows):
        if row_key(item) == key:
            return i
    if key[0] in ("session", "more"):
        cwd = key[1] if key[0] == "more" else key[2]
        # A session pushed behind the per-project limit lives under "more".
        for i, item in enumerate(rows):
            if item[0] == "more" and item[1].cwd == cwd:
                return i
        for i, item in enumerate(rows):
            if item[0] == "group" and item[1].cwd == cwd:
                return i
    return 0


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
    sessions = session_index(groups)
    current = sessions.get(presented)
    target = sessions.get(tag)
    if current is None or target is None:
        return selected, presented
    if target.created <= current.created:
        return selected, presented
    if session_root(current, sessions) != session_root(target, sessions):
        return selected, presented
    return ("session", target.id, target.cwd), target.id


def build_lines(
    items,
    w: int,
    collapsed: set[str],
    flt: str,
    expanded: set[str] | None = None,
    labels: dict[str, str] | None = None,
):
    """Lay out items into single display lines (one line per item).

    Returns (lines, starts, heights) where each line is
    (text, kind, item_index) and starts/heights map an item index to its
    first line and line count. Project groups are separated by a blank gap.
    """
    expanded = expanded or set()
    if not labels:
        labels = canonical_group_names([g for _, g, _ in items])
    lines = []
    starts: dict[int, int] = {}
    heights: dict[int, int] = {}
    for idx, (kind, g, s) in enumerate(items):
        if kind == "group" and idx > 0:
            lines.append(("", "gap", -1))
        starts[idx] = len(lines)
        if kind == "group":
            arrow = "▸" if group_collapsed(g, collapsed, flt) else "▾"
            name = labels.get(g.cwd, g.cwd)
            label = f"{arrow} {name}  ({len(g.sessions)})"
            if len(label) > w - 1:
                label = label[: max(1, w - 2)] + "…"
            lines.append((label, "group", idx))
        elif kind == "more":
            text = (
                "   … show fewer"
                if g.cwd in expanded
                else f"   … {s or 0} more"
            )
            lines.append((text, "more", idx))
        else:
            sym, _ = STATUS[s.status]
            suffix = rel_time(s.last_ts)
            glyph = HARNESS[s.harness]["glyph"]
            prefix = f" {sym} {glyph} "
            tw = max(4, w - display_width(prefix) - display_width(suffix) - 2)
            title = truncate_cols(re.sub(r"\s+", " ", s.title), tw)
            body = prefix + title
            pad = max(1, w - display_width(body) - display_width(suffix) - 1)
            lines.append((body + " " * pad + suffix, "session", idx))
        heights[idx] = len(lines) - starts[idx]
    return lines, starts, heights


def draw(
    stdscr, groups, flt, cursor, offset, collapsed, message="", expanded=None
):
    stdscr.erase()
    h, w = stdscr.getmaxyx()

    def put(y: int, text: str, attr: int, pad: bool = True) -> None:
        text = (fit_cols if pad else truncate_cols)(text, w - 1)
        stdscr.addnstr(y, 0, text, len(text), attr)

    title = f" Sassy Code — {sum(len(g.sessions) for g in groups)} sessions "
    put(0, title, curses.A_REVERSE | curses.A_BOLD)
    if flt:
        put(1, f" filter: {flt}", curses.A_DIM, pad=False)

    keys = footer_lines(FOOTER_ITEMS, w)
    footer_h = len(keys)
    items = list(rows_for(groups, flt, collapsed, expanded))
    list_top = 2 if flt else 1
    avail = max(1, h - list_top - footer_h)
    if items:
        cursor = min(max(cursor, 0), len(items) - 1)
    else:
        cursor = 0

    lines, starts, heights = build_lines(
        items, w, collapsed, flt, expanded, canonical_group_names(groups)
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

    for i in range(avail):
        li = offset + i
        if li >= len(lines):
            break
        text, kind, idx = lines[li]
        if kind == "gap":
            continue
        sel = idx == cursor
        attr = curses.A_REVERSE if sel else curses.A_NORMAL
        if kind == "group":
            put(i + list_top, text,
                attr | curses.A_BOLD | (0 if sel else curses.color_pair(5)))
        elif kind == "more":
            put(i + list_top, text, attr | (0 if sel else curses.A_DIM))
        else:
            s = items[idx][2]
            _, color = STATUS[s.status]
            put(i + list_top, text,
                attr | (0 if sel else curses.color_pair(color)))

    footer = footer_lines([message.strip()], w)[:footer_h] if message else keys
    for i in range(footer_h):
        text = footer[i] if i < len(footer) else ""
        put(h - footer_h + i, text,
            curses.A_REVERSE | (curses.A_BOLD if message else 0))
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
    set_panel_tag(panel_pane_id())
    flt = ""
    cursor = offset = 0
    selected = None  # row_key() of the highlighted row, stable across sorts
    presented = _read_state()  # session the viewer was last asked to show
    if not presented:
        # No record yet: take the session already in the viewer as presented.
        _, panes, viewer = visible_layout(groups, target)
        presented = next((p.tag for p in panes if p.id == viewer), "")
    collapsed: set[str] = set()
    expanded: set[str] = set()
    reap_state: dict[str, float] = {}
    message = ""
    message_until = 0.0
    while True:
        if message and time.time() >= message_until:
            message = ""
        # Re-derive the cursor from the selected row's identity, not its old
        # index: a session that starts working moves to the top of the list.
        rows = list(rows_for(groups, flt, collapsed, expanded))
        cursor = cursor_for_key(rows, selected)
        cursor, offset = draw(
            stdscr, groups, flt, cursor, offset, collapsed, message, expanded
        )
        try:
            ch = stdscr.getch()
        except KeyboardInterrupt:
            preserve_active_on_quit(groups, target, reap_state)
            break
        if ch == -1:
            groups = load_groups()
            panes, viewer = refresh_hidden(groups, reap_state, target)
            # The highlight is whatever row the cursor is on, even before the
            # first key press; `selected` is only filled in below a key event.
            highlight = (
                row_key(rows[cursor])
                if rows and 0 <= cursor < len(rows)
                else selected
            )
            before = presented
            highlight, presented = follow_viewer_switch(
                highlight, presented, panes, groups, viewer
            )
            selected = highlight
            if presented != before:
                _write_state(presented)
            continue
        rows = list(rows_for(groups, flt, collapsed, expanded))
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
                kind, g, selected_session = rows[cursor]
                # New sessions continue the habit of the project: reuse the
                # harness of the highlighted session (or its newest sibling).
                harness = (
                    selected_session.harness
                    if kind == "session" and selected_session
                    else (g.sessions[0].harness if g.sessions else None)
                )
                warning = new_session(
                    target, g.cwd, groups, reap_state, harness
                )
                if warning:
                    message = f" {warning} "
                    message_until = time.time() + 4
                groups = load_groups()
                presented = _read_state()
        elif ch in (curses.KEY_LEFT, ord("h")):
            if 0 <= cursor < len(rows):
                kind, g, _ = rows[cursor]
                if kind == "group":
                    collapsed.add(g.cwd)
                elif kind == "more":
                    expanded.discard(g.cwd)
        elif ch in (curses.KEY_RIGHT, ord("l")):
            if 0 <= cursor < len(rows):
                kind, g, _ = rows[cursor]
                if kind == "group":
                    collapsed.discard(g.cwd)
                elif kind == "more":
                    expanded.add(g.cwd)
        elif ch in (curses.KEY_ENTER, 10, 13):
            if 0 <= cursor < len(rows):
                kind, g, s = rows[cursor]
                if kind == "group":
                    if g.cwd in collapsed:
                        collapsed.discard(g.cwd)
                    else:
                        collapsed.add(g.cwd)
                elif kind == "more":
                    if g.cwd in expanded:
                        expanded.discard(g.cwd)
                    else:
                        expanded.add(g.cwd)
                else:
                    warning = present_session(s, groups, target, reap_state)
                    if warning:
                        message = f" {warning} "
                        message_until = time.time() + 4
                    else:
                        message = ""
                        presented = s.id
                    groups = load_groups()
        # Remember identity of the newly highlighted row for the next sort.
        if rows and 0 <= cursor < len(rows):
            selected = row_key(rows[cursor])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", required=False, help="tmux pane id to control")
    ap.add_argument("--list", action="store_true", help="print parsed list and exit")
    ap.add_argument(
        "--new-session",
        action="store_true",
        help="ask the panel to start a session in its selected project",
    )
    ap.add_argument(
        "--pane",
        default="",
        help="tmux pane whose window holds the control panel",
    )
    args = ap.parse_args()

    if args.new_session:
        pane = args.pane or os.environ.get("TMUX_PANE", "")
        if not pane:
            ap.error("--new-session requires --pane or TMUX_PANE")
        sys.exit(0 if request_new_session(pane) else 1)

    if args.list:
        groups = load_groups()
        labels = canonical_group_names(groups)
        for g in groups:
            print(labels.get(g.cwd, g.cwd))
            for s in g.sessions:
                print(f"  {STATUS[s.status][0]} [{s.status:7}] "
                      f"[{s.harness:6}] "
                      f"{s.title[:60]:<60} {rel_time(s.last_ts):>4}  {s.id}")
        return

    if not args.target:
        ap.error("--target is required unless --list is given")

    curses.wrapper(main_curses, args.target)


if __name__ == "__main__":
    main()
