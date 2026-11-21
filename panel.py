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
import tempfile
import unicodedata
import uuid
import zlib
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any, Callable

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
STATE_DIR = os.path.expanduser("~/.sassywares/sassy-code")
LEGACY_STATE_FILE = os.path.expanduser("~/.sassy-code-current")
CURRENT_STATE = "current"
GOALS_STATE = "goals.json"
SESSIONS_STATE = "sessions.json"
SESSION_LIMIT = 5  # top-level items shown per project before "… N more"
GOAL_SESSION_LIMIT = 3  # children shown per expanded goal before "… N more"
SLICE_SESSION_LIMIT = 3  # sessions shown per expanded slice before "… N more"
# D14 name columns, by container depth: a loose session at 1, a goal's name at
# 5, a slice's name at 8. A session's status mark starts at its container's
# name column, so one table keeps every indent aligned.
NAME_COL = {0: 1, 1: 5, 2: 8}
# The goal/slice arrow sits two columns before the name, in a loose session's
# harness-glyph column for a goal.
ARROW_COL = {depth: NAME_COL[depth] - 2 for depth in NAME_COL if depth}

BG_SESSION = "sassy-code-bg"
DEFAULT_HARNESS = "pi"
# Small, quiet marks that say which harness owns a session.
HARNESS = {
    "pi": {"label": "Pi", "glyph": "π"},
    "claude": {"label": "Claude Code", "glyph": "✳"},
}
HARNESS_KEYS = tuple(HARNESS)
# Internal panel key: `n` without the new-session box. `--new-session` sends it
# so the prefix shortcut never asks. Plain `N` is unbound in the panel and is a
# printable character, so `send-keys -l` delivers it as one byte.
NEW_NO_PICKER_KEY = "N"
# Mouse: a second click on the same row inside this window presents it (D20).
DOUBLE_CLICK_SECONDS = 0.4
# One wheel notch can arrive as more than one KEY_MOUSE event (a terminal's
# press then click, or a prolonged event). A same-direction event inside this
# window is the same notch, so it must not move the highlight again.
WHEEL_DEBOUNCE_SECONDS = 0.05
# A held drag survives the 2 s refresh tick; only a press older than this is
# dropped, so a button held still for a while is not silently cancelled.
DRAG_EXPIRE_SECONDS = 30.0
# ncurses names wheel buttons 4/5 by their press and click masks. Wheel up is
# almost always there; wheel down only on a build that exposes BUTTON5_*. On a
# 4-button build (this platform's system ncurses, macOS) the library drops a
# real wheel-down notch before the panel sees it, so MOUSE_WHEEL_DOWN is 0 and
# wheel down is inert rather than guessed from a modifier bit.
MOUSE_WHEEL_UP = (
    getattr(curses, "BUTTON4_PRESSED", 0)
    | getattr(curses, "BUTTON4_CLICKED", 0)
)
MOUSE_WHEEL_DOWN = (
    getattr(curses, "BUTTON5_PRESSED", 0)
    | getattr(curses, "BUTTON5_CLICKED", 0)
)
# Guard the left-button masks too: a build with KEY_MOUSE but no BUTTON1_*
# must not raise in mouse_kind().
MOUSE_B1_PRESSED = getattr(curses, "BUTTON1_PRESSED", 0)
MOUSE_B1_RELEASED = getattr(curses, "BUTTON1_RELEASED", 0)
MOUSE_B1_CLICKED = getattr(curses, "BUTTON1_CLICKED", 0)
MOUSE_B1_DOUBLE_CLICKED = getattr(curses, "BUTTON1_DOUBLE_CLICKED", 0)
MOUSE_B1_TRIPLE_CLICKED = getattr(curses, "BUTTON1_TRIPLE_CLICKED", 0)
# A curses build without mouse support simply never reports this key.
MOUSE_KEY = getattr(curses, "KEY_MOUSE", -1)
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
    "in_progress": ("●", 1),
    "needs_input": ("◐", 3),
    "idle": ("·", 4),
    "error": ("✗", 2),
    "done": ("✓", 6),
}
# In-memory needs-input attention marks, keyed by session id. Never persisted:
# a restart drops them (D6).
_ATTENTION: set[str] = set()

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
    # A Claude `AskUserQuestion` tool call with no matching result yet.
    awaiting_input: bool = False
    # Task/agent ids with a background run still open (Claude Code only).
    open_tasks: frozenset[str] = frozenset()
    # A queued input (e.g. a task notification) the main thread has not acted on.
    queued_input: bool = False

    @property
    def title(self) -> str:
        return self.name or self.preview or "(empty session)"

    @property
    def done(self) -> bool:
        """The done state is the merged status, never a second field."""
        return self.status == "done"

    def is_complete(self) -> bool:
        """True once the latest turn has a terminal assistant stop reason.

        Background bookkeeping (`open_tasks`, `queued_input`) belongs here,
        not in `pending_turn()`: a running background run must never let a
        hidden pane be reaped, but it does not keep the main thread's turn
        unresolved for status.
        """
        if self.open_tasks or self.queued_input:
            return False  # background work outlives the main thread's turn
        if self.last_role in ("user", "toolResult"):
            return False
        return self.last_stop_reason in TERMINAL_STOP_REASONS


@dataclass
class Goal:
    """A user-created folder inside one project; panel state only."""

    id: str
    name: str
    created: float
    done: bool = False
    collapsed: bool = False
    sessions: list[str] = field(default_factory=list)
    # Parent goal id. Missing/unknown means a top-level goal (A2): old files
    # load unchanged and a dangling link never makes a crash or a lost row.
    parent: str = ""


@dataclass
class Group:
    cwd: str
    sessions: list[Session] = field(default_factory=list)
    goals: list[Goal] = field(default_factory=list)

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
CLAUDE_TOOL_USE_ID_RE = re.compile(r"<tool-use-id>\s*([^<\s]+)\s*</tool-use-id>")
CLAUDE_NOTIFICATION_RE = re.compile(r"<task-notification>.*?</task-notification>", re.S)
# A TaskStop result names the background run it closed (group 1 = task id).
# The error form ("No task found with ID") also closes it: the task is not
# running any more. That form is anchored to the error body's start so text
# quoted elsewhere in a result body never closes a run.
CLAUDE_TASK_STOP_RE = re.compile(r"Successfully stopped task:\s*([\w-]+)")
CLAUDE_TASK_GONE_RE = re.compile(
    r"\A\s*<tool_use_error>\s*No task found with ID:\s*([\w-]+)")
# Background launch results, matched at the start of the result body (group 1
# = task id) and only for the tool_use that launched them. The Agent wording is
# confirmed on disk; the Bash wording follows Claude Code's documented text.
CLAUDE_TASK_OPEN_RES = {
    "agent": re.compile(r"\A\s*Async agent launched successfully.*?agentId:\s*([\w-]+)", re.S),
    "bash": re.compile(r"\A\s*(?:Command )?running in background with ID:\s*([\w-]+)", re.I),
    # SendMessage resuming a finished agent starts a new background run.
    "resume": re.compile(
        r'\A\s*\{.*?"message"\s*:\s*"Resuming agent.*?"resumedAgentId"\s*:\s*"([\w-]+)"', re.S),
}
# An agent that stopped while its own background work still runs sends an
# interim `<task-notification>` (summary "... is waiting on its own background
# work", note "... stopped with background work of its own still running ...");
# the same task id notifies again when it truly finishes. It closes nothing.
CLAUDE_INTERIM_NOTE_RE = re.compile(
    r"waiting on its own background work|stopped with background work of its own")
CLAUDE_NOTE_TEXT_RE = re.compile(
    r"<(summary|note)>(.*?)</\1>", re.S)
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
        elif b.get("name") == "SendMessage":
            launches[b.get("id")] = "resume"
        elif b.get("name") == "TaskStop":
            launches[b.get("id")] = "stop"
        elif b.get("name") == "Bash" and isinstance(args, dict) \
                and args.get("run_in_background"):
            launches[b.get("id")] = "bash"


def _claude_task_runs(
    content, launches: dict[str, str], pos: int,
    opened: dict[tuple[str, str], int],
    closed_runs: set[str], closed_tasks: dict[str, int],
    first_seen: dict[str, int],
    consumed: set[str], op: str | None = None,
) -> list[str]:
    """Fold the runs opened and closed by one entry (file position `pos`).

    A run is (launching tool_use id, task id), recorded with its position. A
    notification naming a `<tool-use-id>` closes that run wherever it sits; a
    non-interim notification also closes every earlier run of its `<task-id>`,
    because a `SendMessage` resume reports the resumed run's own tool-use id.
    A notification without a `<tool-use-id>` (a resumed process reporting
    `stopped`, or a plain `completed`) closes only runs of its `<task-id>`
    launched earlier than the notification's first copy: it is written several
    times (queue, then user entry), and a late copy must not close a run
    launched in between, so `first_seen` keeps the position where each
    notification's text first appears. Once an instance was consumed
    (dequeued, removed, or seen as a user entry; `consumed`), a later
    `enqueue` (`op`) of the same text is a new instance and restarts its
    position. A `TaskStop` result closes the run of the task id it names. An
    interim notification (`CLAUDE_INTERIM_NOTE_RE`) closes nothing.
    Returns the note texts seen, for the caller to mark consumed.
    """
    keys: list[str] = []
    if isinstance(content, list):
        for b in content:
            if isinstance(b, dict) and b.get("type") == "tool_result":
                tuid = b.get("tool_use_id")
                kind = launches.get(tuid)
                if kind:
                    body = b.get("content")
                    body = body if isinstance(body, str) else text_of(body)
                    if kind == "stop":
                        # A stopped run is closed; a later resume opens a fresh
                        # run of the same task id at a later position.
                        for tid in (CLAUDE_TASK_STOP_RE.findall(body)
                                    + CLAUDE_TASK_GONE_RE.findall(body)):
                            closed_tasks[tid] = max(closed_tasks.get(tid, -1), pos)
                        continue
                    for tid in CLAUDE_TASK_OPEN_RES[kind].findall(body):
                        opened.setdefault((tuid, tid), pos)
        blocks = [b.get("text", "") for b in content
                  if isinstance(b, dict) and b.get("type") == "text"]
    else:
        blocks = [content] if isinstance(content, str) else []
    for block in blocks:
        block = SYSTEM_REMINDER_RE.sub(" ", block)
        if not block.lstrip().startswith("<task-notification"):
            continue
        for note in CLAUDE_NOTIFICATION_RE.findall(block):
            if any(CLAUDE_INTERIM_NOTE_RE.search(m[1])
                   for m in CLAUDE_NOTE_TEXT_RE.findall(note)):
                continue
            run_ids = CLAUDE_TOOL_USE_ID_RE.findall(note)
            key = " ".join(note.split())
            keys.append(key)
            if op == "enqueue" and key in consumed:
                consumed.discard(key)
                first_seen[key] = pos
            at = first_seen.setdefault(key, pos)
            if run_ids:
                closed_runs.update(run_ids)
            # A resume reports a different tool-use id, so the later finish
            # closes the earlier runs of the same task id as well. Duplicate
            # copies of an older note keep its first position and close nothing
            # opened after it.
            for tid in CLAUDE_TASK_ID_RE.findall(note):
                closed_tasks[tid] = max(closed_tasks.get(tid, -1), at)
    return keys


def _queue_text(content) -> str:
    """Whitespace-normalised queue-operation content, for matching removes."""
    return " ".join(content.split()) if isinstance(content, str) else ""


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
    opened_all: dict[tuple[str, str], int] = {}
    closed_runs: set[str] = set()
    closed_tasks: dict[str, int] = {}
    first_seen: dict[str, int] = {}
    consumed: set[str] = set()
    # Enqueued, not yet dequeued, removed, or answered items, FIFO: the raw
    # content (whitespace-normalised) and its id-less note texts.
    queued: list[tuple[str, list[str]]] = []
    launches: dict[str, str] = {}
    # AskUserQuestion tool calls with no matching tool_result yet.
    open_questions: list[str] = []
    for pos, line in enumerate(lines):
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
            op = o.get("operation")
            keys = _claude_task_runs(o.get("content"), launches, pos,
                                     opened_all, closed_runs, closed_tasks,
                                     first_seen, consumed, op)
            if op == "enqueue":
                queued.append((_queue_text(o.get("content")), keys))
            else:
                # A remove names the item it drops (not always the oldest); a
                # dequeue, or a remove without content, takes the oldest.
                text = _queue_text(o.get("content"))
                taken: list[str] = []
                if not text and queued:
                    taken = queued.pop(0)[1]
                elif text:
                    for i, (qt, qk) in enumerate(queued):
                        if qt == text:
                            taken = queued.pop(i)[1]
                            break
                consumed.update(keys or taken)
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
            consumed.update(_claude_task_runs(
                content, launches, pos, opened_all, closed_runs, closed_tasks,
                first_seen, consumed))
            if isinstance(content, list):
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") != "tool_result":
                        continue
                    tid = str(block.get("tool_use_id") or "")
                    if tid in open_questions:
                        open_questions.remove(tid)
                    elif not tid and "" in open_questions:
                        open_questions.remove("")
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
            content = m.get("content")
            _claude_launches(content, launches)
            if isinstance(content, list):
                for block in content:
                    if (
                        isinstance(block, dict)
                        and block.get("type") == "tool_use"
                        and block.get("name") == "AskUserQuestion"
                    ):
                        open_questions.append(str(block.get("id") or ""))
            s.messages += 1
            # Heuristic bound on orphaned entries: some enqueued items are
            # never dequeued or removed on disk, so a new assistant turn
            # resets the queue. It can drop an item that is dequeued a few
            # ms after an end_turn; that is harmless because the window is
            # far shorter than the reap grace.
            queued.clear()
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
    s.open_tasks = frozenset(
        tid for (run, tid), at in opened_all.items()
        if run not in closed_runs and closed_tasks.get(tid, -1) <= at)
    s.queued_input = bool(queued)
    s.awaiting_input = bool(open_questions)
    s.last_error = last_is_error
    s.status = _infer_status(s)
    return s


def parse_session(path: str, harness: str = "pi") -> Session | None:
    if harness == "claude":
        return parse_claude_session(path)
    return parse_pi_session(path)


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
    return s.last_stop_reason not in TERMINAL_STOP_REASONS


def _infer_status(s: Session) -> str:
    """File-only base status: a failure, else idle without a live process."""
    return "error" if s.last_error else "idle"


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


def mark_attention(session_id: str) -> None:
    """Remember that a hidden session is waiting for the user (D6)."""
    _ATTENTION.add(session_id)


def clear_attention(session_id: str) -> None:
    _ATTENTION.discard(session_id)


def attention_active(session_id: str) -> bool:
    return session_id in _ATTENTION


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
        if p.session_name == BG_SESSION
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

    Sending the panel's no-picker `N` key keeps one spawn/present path and does not
    move focus, so the keyboard stays in the viewer.
    """
    panel_id = panel_pane_for(pane_id)
    if not panel_id:
        tmux("display-message", "sassy-code: no control panel in this window")
        return False
    return tmux_ok(tmux("send-keys", "-t", panel_id, "-l", NEW_NO_PICKER_KEY))


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


def _spawn_bg_window(cwd: str, command: str, tag: str) -> str:
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
    set_pane_tag(pane_id, tag)
    return pane_id


def installed_harnesses() -> list[str]:
    """Harness keys whose executable is on PATH, in `HARNESS` order."""
    return [key for key in HARNESS_KEYS if shutil.which(key)]


def default_harness() -> str:
    """Harness for a brand-new session when the project has no hint.

    `SASSY_CODE_NEW_HARNESS` wins when valid; otherwise use whichever harness
    is installed, so a Claude-only machine does not try to run `pi`.
    """
    value = os.environ.get("SASSY_CODE_NEW_HARNESS", "").strip().lower()
    if value in HARNESS:
        return value
    return next(iter(installed_harnesses()), DEFAULT_HARNESS)


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
        if not pane_is_live(pane, procs):
            # The agent already exited: remove the leftover shell/window too.
            kill_pane(pane.id)
            reap_state.pop(pane.id, None)
            continue
        if s is None or not s.is_complete():
            reap_state.pop(pane.id, None)
            continue
        # A hidden pane that finished its turn is waiting for the user; keep
        # that attention after the pane is reaped (D6). Errors keep their own
        # status and done marks outrank attention.
        if not s.last_error:
            mark_attention(s.id)
            if s.status != "done":
                s.status = "needs_input"
        first_seen = reap_state.get(pane.id)
        if first_seen is None:
            reap_state[pane.id] = now
        elif now - first_seen >= REAP_GRACE:
            kill_pane(pane.id)
            reap_state.pop(pane.id, None)
    for pane_id in list(reap_state):
        if pane_id not in hidden_ids:
            reap_state.pop(pane_id, None)


def state_path(name: str) -> str:
    return os.path.join(STATE_DIR, name)


def ensure_state_dir() -> None:
    os.makedirs(STATE_DIR, exist_ok=True)


def migrate_state() -> None:
    """Move the old current-session file into the state dir, once. New wins."""
    new = state_path(CURRENT_STATE)
    if os.path.exists(new) or not os.path.exists(LEGACY_STATE_FILE):
        return
    try:
        ensure_state_dir()
        shutil.move(LEGACY_STATE_FILE, new)
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
        cwd: [_goal_from_record(r) for r in records]
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

_GOALS_STORE = Store(
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


def _goal_from_record(r: dict) -> Goal:
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
    return read_store(_GOALS_STORE)


def load_goals() -> tuple[GoalIndex, bool]:
    """Goals by project cwd plus an unreadable flag, cached by mtime and size."""
    return load_store(_GOALS_STORE)


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
    return mutate_store(_GOALS_STORE, apply)


def _clean_goal_name(name: str, noun: str = "goal") -> str:
    name = " ".join(name.split())
    if not name:
        raise GoalError(f"{noun} needs a name")
    return name


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
                raise GoalError("slices cannot contain slices")
            # A collapsed or done parent would hide the node just created, so
            # reopen and expand it as starting a session there does.
            container["collapsed"] = False
            container["done"] = False
        _check_name_free(data, cwd, name, parent)
        goals.append(record)

    mutate_goals(apply)
    return _goal_from_record(record)


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
    raise GoalError("goal no longer exists")


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
                return NO_WRITE
        elif not owners and in_loose:
            return NO_WRITE
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

    return mutate_goals(apply) is not NO_WRITE


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
        raise GoalError(f"{noun} '{name}' already exists here")


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


def delete_goal(cwd: str, goal_id: str) -> None:
    """Remove a goal and its slices; every session becomes loose (D16)."""

    def apply(data: dict) -> None:
        records = data["projects"].get(cwd, [])
        _find_goal_in(records, goal_id)
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


def goal_rollup(
    g: Group, goal: Goal, members: list[Session] | None = None
) -> str:
    """The node's rollup marks: `✗` > `●` > `◐`, plus `✓` when done (D15)."""
    if members is None:
        members = goal_subtree_members(g, goal)
    statuses = {s.status for s in members}
    marks = []
    for status in ("error", "in_progress", "needs_input"):
        if status in statuses:
            marks.append(STATUS[status][0])
            break
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


def picker_start(hint: str | None, installed: list[str]) -> int:
    """Index of the preselected harness: the inferred one, else the default."""
    if hint in installed:
        return installed.index(hint)
    default = default_harness()
    return installed.index(default) if default in installed else 0


def new_box_start(
    row: tuple | None,
    installed: list[str],
    hint: str | None = None,
    sections: list[str] | None = None,
) -> dict:
    """Initial state for the two-section box; focus starts on Harnesses."""
    sections = list(sections) if sections is not None else new_box_sections(row)
    focus = (
        sections.index(NEW_BOX_HARNESSES)
        if NEW_BOX_HARNESSES in sections
        else 0
    )
    state = {
        "sections": sections,
        "section": focus,
        "harness": picker_start(hint, installed),
    }
    if NEW_BOX_GOAL in sections:
        action = new_box_goal_action(row)
        state["goal_action"] = action
        state["goal_label"] = (
            NEW_BOX_SLICE_ITEM if action == "slice" else NEW_BOX_GOAL_ITEM
        )
    return state


def new_box_key(state: dict, installed: list[str], ch) -> tuple[dict, str]:
    """Apply one key to the box; returns (new state, action).

    Actions are "" (stay), "cancel", "harness" or "goal". Ctrl-C cancels the
    box like Esc/q when it arrives as the int key 3; with ISIG on, curses
    raises KeyboardInterrupt before a key is read, which `new_box` catches.
    `↑`/`↓` (and `k`/`j`) cross sections: `↑` off the first harness returns to
    the Goal item and `↓` off it lands on the first harness. `Tab` still
    switches sections; `←`/`→` do nothing here.
    """
    state = dict(state)
    sections = state["sections"]
    if not sections:
        return state, "cancel"
    section = sections[state["section"]]
    if ch in (3, 27, ord("q")):
        return state, "cancel"
    if ch == 9:  # Tab still switches sections; left/right are inert here
        if len(sections) > 1:
            state["section"] = (state["section"] + 1) % len(sections)
        return state, ""
    if ch in (curses.KEY_UP, ord("k")):
        if section == NEW_BOX_HARNESSES and state["harness"] > 0:
            state["harness"] -= 1
        elif NEW_BOX_GOAL in sections:
            # Up off the first harness returns to the Goal item.
            state["section"] = sections.index(NEW_BOX_GOAL)
        return state, ""
    if ch in (curses.KEY_DOWN, ord("j")):
        if section == NEW_BOX_GOAL:
            # Down off the Goal item lands on the first harness.
            state["section"] = sections.index(NEW_BOX_HARNESSES)
            state["harness"] = 0
        elif state["harness"] < max(0, len(installed) - 1):
            state["harness"] += 1
        return state, ""
    if ch in (curses.KEY_ENTER, 10, 13):
        if section == NEW_BOX_HARNESSES:
            return (state, "harness" if installed else "")
        return state, "goal"
    if isinstance(ch, int) and ord("1") <= ch <= ord("9"):
        n = ch - ord("1")
        if section == NEW_BOX_HARNESSES:
            if n < len(installed):
                state["harness"] = n
                return state, "harness"
        elif n == 0:
            return state, "goal"
    return state, ""


def new_box_lines(state: dict, installed: list[str]) -> list[tuple[str, str, int]]:
    """One line per item: (text, section, index), with no section headings.

    A `divider` line separates the contextual item from the harnesses. The
    Goal label is `+ <label>`: the ASCII marker plus one space puts it in the
    harness index column and its text in the harness glyph column, the same
    columns as a harness row. A harness-only box is just its harness lines.
    """
    lines: list[tuple[str, str, int]] = []
    for n, section in enumerate(state["sections"]):
        if n:
            lines.append(("", PICKER_DIVIDER, -1))  # one rule between
        if section == NEW_BOX_GOAL:
            # `+ ` is two columns wide, so the label starts in the harness
            # glyph column and the marker in the harness index column.
            lines.append((f" {state['goal_label']}", NEW_BOX_GOAL, 0))
            continue
        for n, key in enumerate(installed):
            info = HARNESS[key]
            lines.append(
                (f" {n + 1} {info['glyph']} {info['label']}",
                 NEW_BOX_HARNESSES, n)
            )
    return lines


def new_box_create(row: tuple, choice: str, name: str) -> tuple:
    """Create the Goal item's goal or slice: (node, footer text, store failed).

    A store refusal (corrupt or unwritable) is flagged by its kind so the
    caller still starts the session loose with the warning; a bad name is not,
    so the caller can abort the whole action. Never matches on message text.
    """
    parent = slice_parent_for_row(row) if choice == "slice" else ""
    if choice == "slice" and not parent:
        return None, "highlight a goal to create a slice in", False
    try:
        node = (
            create_slice(row[1].cwd, parent, name)
            if choice == "slice"
            else create_goal(row[1].cwd, name)
        )
    except OSError as e:
        return None, _store_error_message(_GOALS_STORE, e), True
    except GoalError as e:
        return None, str(e), isinstance(e, GoalStoreError)
    noun = "slice" if choice == "slice" else "goal"
    return node, f"created {noun} '{node.name}'", False


_DERIVE_CONTAINER = object()


def new_box_flow(
    stdscr,
    row: tuple,
    installed: list[str],
    hint: str | None = None,
    container=_DERIVE_CONTAINER,
) -> tuple[str, str | None, list[str], bool] | None:
    """Run the box to a decision; (harness, container id, notices, created).

    None means the user cancelled at any step. *container* is the caller's
    already-computed target; when omitted it is derived here. A harness item
    starts with that target; the Goal item then asks for a harness and a
    name, creates the goal or slice and targets the new node. An empty
    harness in the result means the name was refused: show the notice and
    start nothing. *created* is True only when this run created a node.
    """
    if container is _DERIVE_CONTAINER:
        _, container = new_session_plan(row)
    picked = new_box(stdscr, row, installed, hint)
    if picked is None:
        return None
    if picked["action"] == "harness":
        return picked["harness"], container, [], False
    choice = picked["action"]
    picked = new_box(
        stdscr, row, installed, hint, sections=[NEW_BOX_HARNESSES]
    )
    if picked is None:
        return None
    harness = picked["harness"]
    noun = "slice" if choice == "slice" else "goal"
    name = prompt_line(stdscr, f"{noun} name: ", "")
    if name is None:
        return None
    node, text, store_failed = new_box_create(row, choice, name)
    if node is not None:
        return harness, node.id, [text], True
    if store_failed:
        return harness, None, [f"{text}; started loose"], False
    return "", None, [text], False


def new_box_pick(installed: list[str], index: int) -> dict | None:
    """The box's harness result for *index*, or None when none installed."""
    if not installed:
        return None
    return {"action": "harness", "harness": installed[index]}


def new_box_mouse_pick(state: dict, index: int, installed: list[str]):
    """Resolve a click on drawn box line *index*; _PICKER_STAY keeps the box.

    The Goal item and each harness are pickable; the divider is not.
    """
    lines = new_box_lines(state, installed)
    if not 0 <= index < len(lines):
        return _PICKER_STAY
    _text, section, number = lines[index]
    if section == PICKER_DIVIDER:
        return _PICKER_STAY
    if section == NEW_BOX_GOAL:
        return {"action": state["goal_action"]}
    if section == NEW_BOX_HARNESSES:
        return new_box_pick(installed, number)
    return _PICKER_STAY  # a section heading is not an item


_PICKER_STAY = object()
PICKER_DIVIDER = "divider"


def divider_text(width: int) -> str:
    """A picker's horizontal rule, edge to edge across *width* columns.

    It overwrites the box's side borders so the rule visibly joins them.
    """
    if width < 2:
        return "─" * max(0, width)
    return "├" + "─" * (width - 2) + "┤"


def run_picker(stdscr, state, title: str, lines_for, step, fallback,
               mouse_pick=None):
    """Draw a one-line-per-item box until *step* ends it; return its result.

    *lines_for(state)* returns `(lines, selected)`, where each line is
    `(text, attribute)` or `(text, attribute, kind)` and *selected* is the
    index to keep centred; *step* applies one key and returns `(state,
    result)`, with `_PICKER_STAY` to keep looping or the value to return (None
    cancels). A `PICKER_DIVIDER` line is a real rule drawn edge to edge at the
    box origin; it takes no part in the width, is never selected and is not
    pickable. If the box cannot be drawn (tiny terminal, or curses raises),
    return *fallback* without acting on a choice nobody saw. Ctrl-C anywhere
    cancels like Esc/q, whether curses reports it as key 3 or raises
    KeyboardInterrupt (ISIG on). Only a left click (or a press and its release)
    picks; the wheel and other buttons are ignored, so scrolling over a box
    never acts on the item under it.
    """
    pressed = False  # an outstanding left press, wherever it landed
    try:
        while True:
            lines, selected = lines_for(state)
            if not lines:
                return fallback  # nothing to draw: cancel like a tiny box
            h, w = stdscr.getmaxyx()  # re-read: the terminal may resize
            rows = min(len(lines), h - 2)
            content = [
                display_width(text)
                for text, _attr, *kind in lines
                if (kind[0] if kind else "") != PICKER_DIVIDER
            ]
            wanted = max(content or [0]) + 3
            width = min(w, wanted)
            if rows < 1 or width < 6:
                return fallback
            # A clamped box keeps one trailing blank column so a truncated
            # line never ends flush against the right border.
            inner = width - 2 - (1 if width < wanted else 0)
            top = max(0, min(selected - rows // 2, len(lines) - rows))
            box_y = max(0, (h - rows - 2) // 2)
            box_x = max(0, (w - width) // 2)
            try:
                win = curses.newwin(
                    rows + 2, width, box_y, box_x,
                )
                # Read keys from the box itself: stdscr.getch() would
                # implicitly repaint the stale panel over it after a resize.
                win.keypad(True)
                win.timeout(2000)
                win.erase()
                win.box()
                win.addnstr(0, 1, title, width - 2)
                for i in range(rows):
                    text, attr, *kind = lines[top + i]
                    if (kind[0] if kind else "") == PICKER_DIVIDER:
                        win.addnstr(i + 1, 0, divider_text(width), width,
                                    attr)
                        continue
                    win.addnstr(i + 1, 1, fit_cols(text, inner),
                                width - 2, attr)
                win.refresh()
            except curses.error:
                return fallback
            ch = win.getch()
            if ch in (-1, curses.KEY_RESIZE):
                continue  # a refresh tick or resize: redraw the box
            if ch == MOUSE_KEY:
                try:
                    _id, mx, my, _z, bstate = curses.getmouse()
                except (curses.error, AttributeError):
                    continue
                kind = mouse_kind(bstate)
                mouse_debug(bstate, mx, my, kind)
                if kind == "press":
                    pressed = True  # the release decides inside vs outside
                    continue
                if kind == "release":
                    if not pressed:
                        continue
                    pressed = False
                elif kind == "click":
                    pressed = False
                else:
                    continue  # a wheel notch or another button: ignore it
                inside, rel = picker_click(
                    rows, box_y, box_x, rows + 2, width, mx, my
                )
                if not inside:
                    return None  # a click outside either box cancels it
                if rel is not None and mouse_pick is not None:
                    result = mouse_pick(state, top + rel)
                    if result is not _PICKER_STAY:
                        return result
                continue
            state, result = step(state, ch)
            if result is not _PICKER_STAY:
                return result
    except KeyboardInterrupt:
        # Ctrl-C anywhere inside the box (draw or key handling) cancels it;
        # it must never escape to the main loop, where it would detach.
        return None
    finally:
        # A Ctrl-C landing during this repaint must stay contained too: the
        # box cancels (or returns its pick) and never detaches the client.
        # Repaint each step separately so one interrupt cannot skip the rest.
        for repaint in (stdscr.touchwin, stdscr.refresh):
            try:
                repaint()
            except (curses.error, KeyboardInterrupt):
                pass


def new_box(stdscr, row, installed: list[str], hint: str | None = None,
            sections: list[str] | None = None) -> dict | None:
    """Boxed Goal + Harnesses picker; a choice dict, or None on cancel.

    A harness choice is `{"action": "harness", "harness": key}`; the Goal item
    is `{"action": "goal"|"slice"}`. If the box cannot be drawn (tiny
    terminal, or curses raises), cancel rather than acting on a pick nobody
    saw. Ctrl-C cancels the box like Esc/q.
    """
    state = new_box_start(row, installed, hint, sections)

    def lines_for(state):
        lines = new_box_lines(state, installed)
        section = state["sections"][state["section"]]
        index = state["harness"] if section == NEW_BOX_HARNESSES else 0
        selected = next(
            (i for i, (_t, sec, idx) in enumerate(lines)
             if sec == section and idx == index),
            0,
        )
        rendered = []
        for text, sec, idx in lines:
            kind = PICKER_DIVIDER if sec == PICKER_DIVIDER else ""
            attr = 0
            if sec == section and idx == index:
                attr = curses.A_REVERSE
            elif sec == "heading":
                attr = curses.A_BOLD
            rendered.append((text, attr, kind))
        return rendered, selected

    def step(state, ch):
        state, action = new_box_key(state, installed, ch)
        if action == "cancel":
            return state, None
        if action == "harness":
            return state, new_box_pick(installed, state["harness"])
        if action == "goal":
            return state, {"action": state["goal_action"]}
        return state, _PICKER_STAY

    def mouse_pick(state, index):
        return new_box_mouse_pick(state, index, installed)

    return run_picker(
        stdscr, state, "new", lines_for, step, None, mouse_pick
    )


MOVE_LOOSE = "loose"
MOVE_GOAL = "goal"
MOVE_NEW = "new"
MOVE_LOOSE_ITEM = "Top level"
MOVE_NEW_ITEM = "+ New goal"


def move_targets(g: Group) -> list[tuple[str, Goal | None, int]]:
    """Move-box items: Top level, New goal, then the goal/slice tree (D18).

    Each item is (kind, goal or None, depth); Top level is depth 0, goals
    start at depth 1 and their slices at depth 2. File order is kept for each
    parent.
    """
    children, _ = goal_tree(g)
    out: list[tuple[str, Goal | None, int]] = [(MOVE_LOOSE, None, 0),
                                              (MOVE_NEW, None, 0)]

    def walk(node: Goal, depth: int) -> None:
        out.append((MOVE_GOAL, node, depth))
        for child in children.get(node.id, []):
            walk(child, depth + 1)

    for root in goal_roots(g, children):
        walk(root, 1)
    return out


def move_box_start(g: Group, session: Session) -> dict:
    """Initial state for the Move-to box, on the session's current container."""
    targets = move_targets(g)
    current = goal_of_session(g, session.id)
    index = 0  # Top level is the current container for a loose session
    if current is not None:
        index = next(
            (
                i
                for i, (kind, goal, _) in enumerate(targets)
                if kind == MOVE_GOAL and goal is not None
                and goal.id == current.id
            ),
            0,
        )
    return {
        "targets": targets,
        "index": index,
        "current": current.id if current is not None else None,
    }


def move_box_key(state: dict, ch) -> tuple[dict, str]:
    """Apply one key to the Move-to box; returns (state, action).

    Actions are "" (stay), "cancel" or "pick". Ctrl-C, Esc and q cancel.
    """
    state = dict(state)
    targets = state["targets"]
    if ch in (3, 27, ord("q")):
        return state, "cancel"
    if ch in (curses.KEY_UP, ord("k")):
        state["index"] = max(0, state["index"] - 1)
        return state, ""
    if ch in (curses.KEY_DOWN, ord("j")):
        state["index"] = min(len(targets) - 1, state["index"] + 1)
        return state, ""
    if ch in (curses.KEY_ENTER, 10, 13):
        return state, "pick"
    if isinstance(ch, int) and ord("1") <= ch <= ord("9"):
        # Digits follow the drawn rows, so the divider takes no number and a
        # digit on it is ignored rather than picking a hidden neighbour.
        lines = move_box_lines(state)
        line = ch - ord("1")
        if line < len(lines):
            _text, target, kind = lines[line]
            if target is not None and kind != PICKER_DIVIDER:
                state["index"] = target
                return state, "pick"
    return state, ""


def move_box_mark(state: dict, goal: Goal | None) -> str:
    """One marker per row: the current container's ✓, else ⌂ on Top level.

    A goal or slice that is not current gets a blank marker (never a done
    mark); the loose row always shows a glyph so the marker column is never
    empty there.
    """
    current = state.get("current")
    if goal is None:
        return "✓" if current is None else "⌂"
    return "✓" if goal.id == current else " "


def move_box_line_attr(state: dict, index: int) -> int:
    """Attributes for drawn line *index*: reverse when selected, dim when done."""
    _kind, goal, _ = state["targets"][index]
    attr = curses.A_REVERSE if index == state["index"] else 0
    if goal is not None and goal.done:
        attr |= curses.A_DIM
    return attr


def move_box_lines(state: dict) -> list[tuple[str, int | None, str]]:
    """Drawn lines: (text, target index or None, kind), tree in shared columns.

    Every line starts one column in from the left border, like the
    new-session box, so the marker never touches the frame. `⌂`, `+` and `✓`
    start in one marker column; `Top level`, every goal name and every slice
    connector share the next name column (a slice's name follows its
    connector), so slices read as children of their goal. A goal with no
    slices has no connector line. A
    divider line precedes the first goal and has no target index, so an empty
    project never ends with a rule that introduces nothing.
    """
    targets = state["targets"]
    body: list[tuple[str, int | None, str]] = []
    index = 0
    tree_started = False
    while index < len(targets):
        kind, goal, depth = targets[index]
        if kind == MOVE_LOOSE:
            body.append((f"{move_box_mark(state, None)} {MOVE_LOOSE_ITEM}",
                         index, ""))
            index += 1
            continue
        if kind == MOVE_NEW:
            body.append((MOVE_NEW_ITEM, index, ""))
            index += 1
            continue
        if not tree_started:
            body.append(("", None, PICKER_DIVIDER))
            tree_started = True
        body.append((f"{move_box_mark(state, goal)} {goal.name}", index, ""))
        # Slices are the goal's direct children, immediately after it.
        child = index + 1
        slices: list[int] = []
        while (child < len(targets)
               and targets[child][0] == MOVE_GOAL
               and targets[child][2] > depth):
            slices.append(child)
            child += 1
        for position, child_index in enumerate(slices):
            child_goal = targets[child_index][1]
            connector = "└" if position == len(slices) - 1 else "├"
            body.append((
                f"{move_box_mark(state, child_goal)} {connector} "
                f"{child_goal.name}",
                child_index,
                "",
            ))
        index = child
    return [
        (text if kind == PICKER_DIVIDER else " " + text, target, kind)
        for text, target, kind in body
    ]


def move_box(stdscr, g: Group, session: Session) -> tuple[str, Goal | None] | None:
    """Boxed Move-to picker; (kind, goal) or None on cancel (D18).

    Reuses the new-session box's shape: one line per item, a narrow frame, and
    j/k/arrows plus digits and Enter. Esc/q/Ctrl-C cancel, and so does a box
    that cannot be drawn, so a session never moves without a visible pick.
    """
    state = move_box_start(g, session)

    def lines_for(state):
        lines = move_box_lines(state)
        selected = next(
            (i for i, (_t, index, _k) in enumerate(lines)
             if index == state["index"]),
            0,
        )
        return [
            (text,
             0 if index is None else move_box_line_attr(state, index),
             kind)
            for text, index, kind in lines
        ], selected

    def step(state, ch):
        targets = state["targets"]
        state, action = move_box_key(state, ch)
        if action == "cancel":
            return state, None
        if action == "pick":
            kind, goal, _ = targets[state["index"]]
            return state, (kind, goal)
        return state, _PICKER_STAY

    def mouse_pick(state, index):
        return move_box_mouse_pick(state, index)

    return run_picker(
        stdscr, state, "Move to", lines_for, step, None, mouse_pick
    )


def move_box_mouse_pick(state: dict, index: int):
    """Resolve a click on drawn Move-to line *index*; _PICKER_STAY keeps it.

    The divider is drawn but is not a target: clicking it keeps the box open.
    """
    lines = move_box_lines(state)
    if not 0 <= index < len(lines):
        return _PICKER_STAY
    _text, target, kind = lines[index]
    if kind == PICKER_DIVIDER or target is None:
        return _PICKER_STAY
    kind, goal, _ = state["targets"][target]
    return kind, goal


def empty_goal_order(
    g: Group, children: dict[str, list[Goal]] | None = None
) -> list[Goal]:
    """The project's empty containers, oldest first (D16).

    Computed once per draw so each hint line does not rescan every goal. A goal
    holding slices is not empty even when no session sits directly in it.
    """
    if children is None:
        children = goal_tree(g)[0]
    return sorted(
        (
            x
            for x in g.goals
            if not goal_subtree_members(g, x, children)
            and not goal_children(g, x, children)
        ),
        key=lambda x: (x.created, x.id),
    )


def goal_hint(
    g: Group, goal: Goal, empties: list[Goal] | None = None
) -> str:
    """A sarcastic nudge, distinct among the project's empty goals and slices.

    *empties* is the project's empty-container order from `empty_goal_order()`;
    without it the order is built here. Each empty container starts at
    crc32(id) and walks forward to the first line no older empty container
    holds, so a node keeps its line until an older empty one gains a session.
    With more empty nodes than lines the walk wraps and lines repeat.
    """
    if empties is None:
        empties = empty_goal_order(g)
    n = len(GOAL_HINTS)
    taken: set[int] = set()
    for x in empties:
        i = zlib.crc32(x.id.encode()) % n
        if len(taken) < n:
            while i in taken:
                i = (i + 1) % n
        if x.id == goal.id:
            return GOAL_HINTS[i]
        taken.add(i)
    return GOAL_HINTS[zlib.crc32(goal.id.encode()) % n]


def slice_parent_for_row(row: tuple | None) -> str:
    """The top-level goal `:slice:create` should file a new slice under."""
    if not row:
        return ""
    kind, g, item = row
    if kind == "goal":
        return item.id
    if kind == "slice":
        return item.parent
    if kind == "session":
        goal = goal_of_session(g, item.id)
        if goal is None:
            return ""
        return goal.parent if goal_is_slice(g, goal) else goal.id
    return ""


def run_command(
    text: str,
    cwd: str | None,
    current: Goal | None = None,
    current_kind: str = "goal",
    slice_parent: str = "",
) -> tuple[str, Goal | None]:
    """Run a `:` command line; returns (footer message, node to select).

    *current* is the highlighted goal or slice; *current_kind* says which, so
    `goal:rename` and `slice:rename` only touch the matching node (A5).
    *slice_parent* lets `:slice:create` target the container of a session row.
    """
    parts = text.strip().lstrip(":").split(None, 1)
    if not parts:
        return "", None
    command, arg = parts[0], parts[1] if len(parts) > 1 else ""

    def fail(e: Exception) -> tuple[str, None]:
        return _store_error_message(_GOALS_STORE, e), None

    if command in ("goal:rename", "slice:rename"):
        noun = command.split(":", 1)[0]
        if cwd is None or current is None or current_kind != noun:
            return f"highlight a {noun} to rename it", None
        try:
            name = rename_goal(cwd, current.id, arg, noun)
        except (GoalError, OSError) as e:
            return fail(e)
        return f"renamed {noun} to '{name}'", replace(current, name=name)
    if command == "slice:create":
        parent = slice_parent
        if not parent and current is not None:
            parent = current.parent if current_kind == "slice" else current.id
        if cwd is None or not parent:
            return "highlight a goal to create a slice in", None
        try:
            node = create_slice(cwd, parent, arg)
        except (GoalError, OSError) as e:
            return fail(e)
        return f"created slice '{node.name}'", node
    if command != "goal:create":
        return f"unknown command: {command}", None
    if cwd is None:
        return "no project to put a goal in", None
    try:
        goal = create_goal(cwd, arg)
    except (GoalError, OSError) as e:
        return fail(e)
    return f"created goal '{goal.name}'", goal


def goal_members(g: Group, goal: Goal) -> list[Session]:
    """The goal's sessions that have a file, newest first (others hidden)."""
    by_id = {s.id: s for s in g.sessions}
    found = [by_id[i] for i in dict.fromkeys(goal.sessions) if i in by_id]
    return sorted(found, key=lambda s: s.last_ts, reverse=True)


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
        members = goal_subtree_members(g, goal, children)
    if members:
        return members[0].last_ts
    return max(
        (
            node.created
            for node in goal_subtree_goals(g, goal, children)
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
        goal_members(g, goal),
        [(child, None) for child in goal_children(g, goal, children)],
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
        if not pane_is_live(target_pane, procs):
            # The tagged pane is only a leftover shell. Replace it rather than
            # presenting a dead pane (hidden ones are normally reaped first).
            if target_id != viewer:
                kill_pane(target_id)
            target_id = None

    if target_id == viewer:
        set_pane_tag(viewer, s.id)
        _write_state(s.id)
        focus(viewer)
        clear_attention(s.id)
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
        clear_attention(s.id)
    return warning


def new_session(
    target: str,
    cwd: str,
    groups: list[Group] | None = None,
    reap_state: dict[str, float] | None = None,
    harness: str | None = None,
    goal_id: str | None = None,
) -> str | None:
    """Start a fresh agent in the viewer via the background swap path.

    With *goal_id* the new id is filed in that goal before spawning. If the
    goal store refuses, the session still starts loose and the reason is
    returned as the footer warning.
    """
    groups = load_groups() if groups is None else groups
    reap_state = {} if reap_state is None else reap_state
    harness = harness if harness in HARNESS else default_harness()
    panel_id, panes, viewer = visible_layout(preferred=target)
    if not panel_id or not panes:
        return "No tmux layout found for the control panel"
    if not viewer:
        return "No session viewer pane found"
    session_id = str(uuid.uuid4())
    goal_warning = None
    if goal_id:
        outside = "; started outside the goal"
        try:
            file_session_in_goal(cwd, goal_id, session_id)
        except GoalError as e:
            goal_warning = f"{e}{outside}"
        except OSError as e:
            goal_warning = f"could not save goal: {e.strerror or e}{outside}"
    new_pane = spawn_new_session_pane(cwd, session_id, harness)
    if not new_pane:
        return "Could not start a new session"
    warning = _present_pane(new_pane, viewer, reap_state)
    if warning:
        kill_pane(new_pane)
        return warning
    _write_state(session_id)
    return goal_warning


DETACH_FAILED = " Could not detach from tmux "


def detach_panel() -> str:
    """Detach every client attached to the panel's own tmux session.

    The one quit path. Nothing is killed or moved: the session, panel, viewer
    and agents keep running and `sassy-code` reattaches. Only the session the
    panel lives in is touched. Returns "" on success or when there is nothing
    to detach (no client attached), else a footer message (also when the
    panel's pane or tmux could not be queried). Callers handle "outside tmux"
    themselves, from $TMUX.
    """
    pane = panel_pane_id()
    if not pane:
        return DETACH_FAILED
    res = tmux("display-message", "-p", "-t", pane, "#{session_name}")
    if not tmux_ok(res):
        return DETACH_FAILED
    if not res.stdout.strip():
        return ""
    target = f"={res.stdout.strip().splitlines()[0]}"
    res = tmux("list-clients", "-t", target, "-F", "#{client_name}")
    if not tmux_ok(res):
        return DETACH_FAILED
    if not res.stdout.strip():
        return ""
    if tmux_ok(tmux("detach-client", "-s", target)):
        return ""
    return DETACH_FAILED


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
        target = pane_live_session(pane, sessions, children, tagged, procs)
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
    records, _ = load_sessions()
    for s in sessions.values():
        # A new turn supersedes any old needs-input attention; presenting the
        # session clears it too (D6).
        if pending_turn(s):
            clear_attention(s.id)
        s.status = session_status(
            s,
            live=s.id in live,
            done=session_done(s, records),
            attention=attention_active(s.id),
        )

    result = list(groups.values())
    for g in result:
        g.sessions.sort(key=lambda s: s.last_ts, reverse=True)
    result.sort(key=lambda g: g.last_ts, reverse=True)
    attach_goals(result)
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
    direct = goal_members(g, goal)
    sessions = direct if full else [s for s in direct if s.id in match_ids]
    children = []
    for child in goal_children(g, goal, children_map):
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
    limit: int = SESSION_LIMIT,
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
        match_ids = {s.id for s in match_sessions}
        member_ids = goal_member_ids(g)
        loose = [s for s in match_sessions if s.id not in member_ids]
        tree = trees.get(id(g)) if trees is not None else None
        if tree is None:
            tree = goal_tree(g)
            if trees is not None:
                trees[id(g)] = tree
        children, _ = tree
        plans = [
            plan
            for plan in (
                plan_goal(g, root, flt_l, match_ids, children_map=children)
                for root in goal_roots(g, children)
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
                g, plan, expanded, flt, GOAL_SESSION_LIMIT, "goal_more",
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
                g, child, expanded, flt, SLICE_SESSION_LIMIT, "slice_more",
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


FOOTER_ITEMS = (
    "↑/↓ move",
    "⏎ present",
    "n new",
    "/ filter",
    ": cmd",
    "r reload",
    "q quit",
)


GOAL_FOOTER_ITEMS = (
    "⏎ toggle",
    "n new",
    "R rename",
    "x done",
    "D delete",
)


SESSION_FOOTER_ITEMS = (
    *FOOTER_ITEMS[:-1],
    "m move",
    "x done",
    FOOTER_ITEMS[-1],
)


def footer_items(row: tuple | None) -> tuple[str, ...]:
    """Footer hints for the highlighted row's kind."""
    kind = row[0] if row else None
    if kind in ("goal", "slice"):
        items, done = GOAL_FOOTER_ITEMS, row[2].done
    elif kind == "session":
        items, done = SESSION_FOOTER_ITEMS, row[2].done
    elif kind == "group":
        return tuple("⏎ toggle" if i == "⏎ present" else i for i in FOOTER_ITEMS)
    else:
        return FOOTER_ITEMS
    if not done:
        return items
    return tuple("x undo" if i == "x done" else i for i in items)


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
    if kind == "goal":
        return ("goal", s.id, g.cwd)
    if kind == "slice":
        return ("slice", s.id, g.cwd)
    if kind == "goal_more":
        return ("goal_more", s[0].id, g.cwd)
    if kind == "slice_more":
        return ("slice_more", s[0].id, g.cwd)
    return ("group", g.cwd)


def hit_test(hits: dict[int, tuple | None], y: int):
    """The row drawn at screen line *y*, or None for a hint, gap or off-panel."""
    return hits.get(y)


def mouse_kind(bstate: int) -> str:
    """Classify a curses mouse bstate: wheel, press, release, click or "".

    Only the real button 4/5 masks count: a build without BUTTON5_* drops a
    wheel-down notch before it reaches us, so there is nothing to guess from
    and no modifier bit is ever read as a wheel. A bstate with no wheel or
    left-button bit (pointer motion, button 2/3) is "".
    """
    if MOUSE_WHEEL_UP and bstate & MOUSE_WHEEL_UP:
        return "wheel_up"
    if MOUSE_WHEEL_DOWN and bstate & MOUSE_WHEEL_DOWN:
        return "wheel_down"
    if bstate & (MOUSE_B1_PRESSED | MOUSE_B1_RELEASED):
        return "press" if bstate & MOUSE_B1_PRESSED else "release"
    if bstate & (
        MOUSE_B1_CLICKED
        | MOUSE_B1_DOUBLE_CLICKED
        | MOUSE_B1_TRIPLE_CLICKED
    ):
        return "click"
    return ""


def picker_click(
    rows: int, box_y: int, box_x: int, height: int, width: int,
    mx: int, my: int,
) -> tuple[bool, int | None]:
    """Hit-test a click in a picker box: (inside, item index or None).

    The index is relative to the box's first drawn line; a click on the frame
    is inside but picks nothing. A click outside the box cancels it.
    """
    inside = (
        box_y <= my < box_y + height
        and box_x <= mx < box_x + width
    )
    if not inside:
        return False, None
    rel = my - box_y - 1
    return True, rel if 0 <= rel < rows else None


def wheel_cursor(count: int, cursor: int, kind: str) -> int:
    """One wheel notch moves the highlight one row, clamped into range."""
    if count <= 0:
        return 0
    step = -1 if kind == "wheel_up" else 1
    return max(0, min(count - 1, cursor + step))


def wheel_notch(
    kind: str, now: float, last: tuple[str, float] | None
) -> tuple[bool, tuple[str, float]]:
    """Whether this wheel event is a fresh notch, and the new debounce state.

    A same-direction event inside WHEEL_DEBOUNCE_SECONDS is the same notch
    arriving twice (press then click, or a prolonged event); it updates the
    clock but moves nothing, so a stream of duplicates still counts as one.
    A direction change is always a fresh notch.
    """
    if (
        last is not None
        and kind == last[0]
        and now - last[1] < WHEEL_DEBOUNCE_SECONDS
    ):
        return False, (kind, now)
    return True, (kind, now)


def mouse_debug(bstate: int, x: int, y: int, kind: str) -> None:
    """Append one diagnostic line per KEY_MOUSE event when asked.

    SASSY_CODE_MOUSE_DEBUG names the file; unset means inert. The path is
    expanded and refused inside the repo, so the panel never picks a location
    itself, and any write error is swallowed so diagnostics never break it.
    """
    path = os.environ.get("SASSY_CODE_MOUSE_DEBUG")
    if not path:
        return
    try:
        target = os.path.realpath(os.path.expanduser(path))
        repo = os.path.dirname(os.path.realpath(__file__))
        if target == repo or target.startswith(repo + os.sep):
            return  # diagnostics never write inside the repo
        with open(target, "a", encoding="utf-8") as fh:
            fh.write(f"bstate=0x{bstate:x} x={x} y={y} kind={kind}\n")
    except (OSError, ValueError):
        pass


def mouse_press(hits, y: int):
    """The session row a press on screen line *y* starts a drag with."""
    row = hit_test(hits, y)
    return row if row is not None and row[0] == "session" else None


def drag_press(drag, hits, y: int):
    """Update a held drag from a left press; an outstanding drag keeps itself.

    A terminal may report pointer motion as a button press, so a press that
    arrives while a drag is in flight must never retarget it.
    """
    if drag is not None:
        return drag
    return mouse_press(hits, y)


def drag_expired(press_time: float, now: float) -> bool:
    """Whether a held drag's press is older than DRAG_EXPIRE_SECONDS."""
    return now - press_time > DRAG_EXPIRE_SECONDS


def drag_target(drag, hit):
    """Where a release on *hit* files the pressed *drag* row; None cancels.

    A goal or slice in the same project takes the session; the project
    heading makes it loose. Any other row, or another project, cancels (D20).
    """
    if drag is None or hit is None or hit[1].cwd != drag[1].cwd:
        return None
    if hit[0] in ("goal", "slice"):
        return (MOVE_GOAL, hit[2])
    if hit[0] == "group":
        return (MOVE_LOOSE, None)
    return None


def mouse_release(kind: str, drag, hit):
    """What a button release means: ("click", row), ("move", target) or None.

    Releasing on the pressed row is a plain click, so a terminal that reports
    press and release instead of a click still works. A release that ends a
    drag only moves onto a same-project goal, slice or heading; anything else
    cancels. Without a drag, a release on a non-session row is a click too,
    so press+release terminals can toggle headings and step `more` rows. A
    release on a session row with no press is ignored.
    """
    if drag is not None:
        if hit is not None and row_key(hit) == row_key(drag):
            return ("click", hit)
        target = drag_target(drag, hit)
        return ("move", target) if target is not None else None
    if hit is None:
        return None
    if kind == "click" or (kind == "release" and hit[0] != "session"):
        return ("click", hit)
    return None


def click_kind(
    row: tuple | None, now: float, last_key, last_time: float
) -> str | None:
    """What a left click on *row* means: present, toggle, tier, select or None.

    The second click on the same session row within DOUBLE_CLICK_SECONDS
    presents it (D20); a heading, goal or slice toggles, a `more` row steps a
    tier, and any other row only selects.
    """
    if row is None:
        return None
    if (
        row[0] == "session"
        and last_key == row_key(row)
        and now - last_time <= DOUBLE_CLICK_SECONDS
    ):
        return "present"
    if row[0] in ("group", "goal", "slice"):
        return "toggle"
    if row[0] in ("more", "goal_more", "slice_more"):
        return "tier"
    return "select"


def more_click_action(row: tuple) -> str:
    """Direction for a click on a `more` row: reveal, or shrink when all shown."""
    return "hide" if row[2][-1] else "show"


def cursor_for_key(rows, key) -> int:
    """Index of the row matching *key*, falling back within its group."""
    if key is None:
        return 0
    for i, item in enumerate(rows):
        if row_key(item) == key:
            return i
    if key[0] not in (
        "session", "more", "goal", "slice", "goal_more", "slice_more"
    ):
        return 0
    cwd = key[1] if key[0] == "more" else key[2]
    if key[0] == "session":
        # Behind a slice's or goal's "more" row, else collapsed away with it.
        group = next((item[1] for item in rows if item[1].cwd == cwd), None)
        goal = next(
            (
                gl
                for gl in (group.goals if group else [])
                if key[1] in gl.sessions
            ),
            None,
        )
        if goal is not None:
            by_id = {gl.id: gl for gl in group.goals}
            chain = [goal]
            parent = by_id.get(goal.parent)
            if parent is not None:
                chain.append(parent)
            for node in chain:
                for i, item in enumerate(rows):
                    if (
                        item[1].cwd == cwd
                        and item[0] in ("goal_more", "slice_more")
                        and item[2][0].id == node.id
                    ):
                        return i
                for i, item in enumerate(rows):
                    if (
                        item[1].cwd == cwd
                        and item[0] in ("goal", "slice")
                        and item[2].id == node.id
                    ):
                        return i
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


def session_depths(
    g: Group, parents: dict[str, str] | None = None
) -> dict[str, int]:
    """Session id -> container depth: 0 loose, 1 directly in a goal, 2 slice."""
    if parents is None:
        parents = _normalized_parents(
            g.goals, lambda gl: gl.id, lambda gl: gl.parent
        )
    depths: dict[str, int] = {}
    for goal in g.goals:
        depth = 2 if parents.get(goal.id) else 1
        for sid in goal.sessions:
            depths[sid] = max(depths.get(sid, 0), depth)
    return depths


def build_lines(
    items,
    w: int,
    collapsed: set[str],
    flt: str,
    labels: dict[str, str] | None = None,
    trees: dict[int, tuple[dict[str, list[Goal]], dict[str, str]]] | None = None,
):
    """Lay out items into single display lines (one line per item).

    Returns (lines, starts, heights) where each line is
    (text, kind, item_index) and starts/heights map an item index to its
    first line and line count. Project groups are separated by a blank gap.
    A goal name starts at column 5 and a slice name at column 8 (D14); a
    session's status mark starts in its container's name column. Rollup marks
    follow the count so a name never shifts.
    """
    if not labels:
        labels = canonical_group_names([g for _, g, _ in items])
    lines = []
    starts: dict[int, int] = {}
    heights: dict[int, int] = {}
    maps = {} if trees is None else trees
    depths: dict[int, dict[str, int]] = {}
    empty_orders: dict[int, list[Goal]] = {}

    def tree(g: Group) -> tuple[dict[str, list[Goal]], dict[str, str]]:
        """This group's adjacency map, shared for one draw (D15)."""
        if id(g) not in maps:
            maps[id(g)] = goal_tree(g)
        return maps[id(g)]

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
        elif kind in ("goal", "slice"):
            closed = goal_collapsed(s, flt)
            arrow = "▸" if closed else "▾"
            children_of, _ = tree(g)
            members = goal_subtree_members(g, s, children_of)
            count = len(members)
            suffix = rel_time(goal_age_ts(g, s, members, children_of))
            marks = []
            if closed:
                rollup = goal_rollup(g, s, members)
                if rollup:
                    marks.append(rollup)
            elif s.done:
                marks.append(STATUS["done"][0])
            mark_text = f" {' '.join(marks)}" if marks else ""
            # Goal arrow stays in the harness-glyph column of a loose session;
            # a slice arrow sits two columns before its name column (D14).
            depth = 2 if kind == "slice" else 1
            prefix = " " * ARROW_COL[depth] + f"{arrow} "
            hint_indent = " " * NAME_COL[depth]
            tail = f"  ({count}){mark_text}"
            tw = max(
                4,
                w - display_width(prefix) - display_width(tail)
                - display_width(suffix) - 2,
            )
            body = prefix + truncate_cols(s.name, tw) + tail
            pad = max(1, w - display_width(body) - display_width(suffix) - 1)
            lines.append((body + " " * pad + suffix, kind, idx))
            if (
                count == 0
                and not closed
                and not goal_children(g, s, children_of)
            ):
                if id(g) not in empty_orders:
                    empty_orders[id(g)] = empty_goal_order(g, children_of)
                hint = truncate_cols(
                    goal_hint(g, s, empty_orders[id(g)]),
                    max(4, w - NAME_COL[depth] - 3),
                )
                lines.append((hint_indent + hint, "hint", -1))
        elif kind in ("goal_more", "slice_more"):
            _container, hidden, all_shown = s
            indent = "       " if kind == "goal_more" else "          "
            text = (
                f"{indent}… show fewer"
                if all_shown
                else f"{indent}… {hidden} more"
            )
            lines.append((text, "more", idx))
        elif kind == "more":
            hidden, all_shown = s
            text = (
                "   … show fewer"
                if all_shown
                else f"   … {hidden} more"
            )
            lines.append((text, "more", idx))
        else:
            sym, _ = STATUS[s.status]
            suffix = rel_time(s.last_ts)
            glyph = HARNESS[s.harness]["glyph"]
            if id(g) not in depths:
                depths[id(g)] = session_depths(g, tree(g)[1])
            depth = depths[id(g)].get(s.id, 0)
            indent = " " * NAME_COL[depth]
            prefix = f"{indent}{sym} {glyph} "
            tw = max(4, w - display_width(prefix) - display_width(suffix) - 2)
            title = truncate_cols(re.sub(r"\s+", " ", s.title), tw)
            body = prefix + title
            pad = max(1, w - display_width(body) - display_width(suffix) - 1)
            lines.append((body + " " * pad + suffix, "session", idx))
        heights[idx] = len(lines) - starts[idx]
    return lines, starts, heights


def draw(
    stdscr, groups, flt, cursor, offset, collapsed, message="", expanded=None,
    notice="",
):
    stdscr.erase()
    h, w = stdscr.getmaxyx()

    def put(y: int, text: str, attr: int, pad: bool = True) -> None:
        if not 0 <= y < h or w < 2:
            return
        text = (fit_cols if pad else truncate_cols)(text, w - 1)
        try:
            stdscr.addnstr(y, 0, text, len(text), attr)
        except curses.error:
            pass  # a tiny or mid-resize terminal must never kill the panel

    title = f" Sassy Code — {sum(len(g.sessions) for g in groups)} sessions "
    put(0, title, curses.A_REVERSE | curses.A_BOLD)
    if flt:
        put(1, f" filter: {flt}", curses.A_DIM, pad=False)

    trees = {id(g): goal_tree(g) for g in groups}
    items = list(rows_for(groups, flt, collapsed, expanded, trees=trees))
    keys = footer_lines(footer_items(highlighted_row(items, cursor)), w)
    if notice:
        keys = footer_lines([notice.strip()], w) + keys
    footer_h = len(keys)
    list_top = 2 if flt else 1
    avail = max(1, h - list_top - footer_h)
    if items:
        cursor = min(max(cursor, 0), len(items) - 1)
    else:
        cursor = 0

    lines, starts, heights = build_lines(
        items, w, collapsed, flt, canonical_group_names(groups), trees
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

    hits: dict[int, tuple | None] = {}
    for i in range(avail):
        li = offset + i
        if li >= len(lines):
            break
        text, kind, idx = lines[li]
        y = i + list_top
        if kind in ("gap", "hint"):
            hits[y] = None
            if kind == "gap":
                continue
        else:
            hits[y] = items[idx]
        sel = idx == cursor
        attr = curses.A_REVERSE if sel else curses.A_NORMAL
        if kind == "hint":
            put(i + list_top, text, curses.A_DIM)
        elif kind in ("goal", "slice"):
            node = items[idx][2]
            put(i + list_top, text,
                attr | curses.A_BOLD | (curses.A_DIM if node.done else 0))
        elif kind == "group":
            put(i + list_top, text,
                attr | curses.A_BOLD | (0 if sel else curses.color_pair(5)))
        elif kind == "more":
            put(i + list_top, text, attr | (0 if sel else curses.A_DIM))
        else:
            s = items[idx][2]
            _, color = STATUS[s.status]
            put(i + list_top, text,
                attr | (0 if sel else curses.color_pair(color))
                | (curses.A_DIM if s.status == "done" else 0))

    footer = footer_lines([message.strip()], w)[:footer_h] if message else keys
    for i in range(footer_h):
        text = footer[i] if i < len(footer) else ""
        put(h - footer_h + i, text,
            curses.A_REVERSE | (curses.A_BOLD if message else 0))
    try:
        stdscr.refresh()
    except curses.error:
        pass
    return cursor, offset, hits


def apply_edit_key(text: str, ch) -> tuple[str, str | None]:
    """Apply one key to a prompt line; returns (text, "submit"/"cancel"/None)."""
    if ch in (curses.KEY_ENTER, 10, 13, "\n", "\r"):
        return text, "submit"
    if ch in (27, "\x1b"):
        return text, "cancel"
    if ch in (curses.KEY_BACKSPACE, 127, 8, "\x7f", "\b"):
        return text[:-1], None
    if ch in (21, "\x15"):
        return "", None
    # Cf (ZWJ, variation selectors) keeps emoji sequences intact.
    if isinstance(ch, str) and (
        ch.isprintable() or unicodedata.category(ch) == "Cf"
    ):
        return text + ch, None
    return text, None


def tail_by_width(text: str, room: int) -> str:
    """The right end of *text* that fits in *room* display columns."""
    shown = text
    while shown and display_width(shown) > room:
        shown = shown[1:]
    return shown


def prompt_line(stdscr, label: str, initial: str = ""):
    """Edit one line at the bottom; returns the text, or None on Esc."""
    curses.curs_set(1)
    text = initial
    try:
        while True:
            h, w = stdscr.getmaxyx()  # re-read: the terminal may resize
            line = f" {label}"
            room = max(1, w - 2 - display_width(line))
            shown = tail_by_width(text, room)
            try:
                stdscr.addnstr(h - 1, 0, fit_cols(line + shown, w - 1), w - 1,
                               curses.A_REVERSE)
                stdscr.move(h - 1, min(w - 2, display_width(line + shown)))
                stdscr.refresh()
            except curses.error:
                pass  # a mid-resize frame must never kill the panel
            try:
                ch = stdscr.get_wch()
            except (curses.error, KeyboardInterrupt) as e:
                if isinstance(e, KeyboardInterrupt):
                    return None
                continue  # timeout tick
            if ch == curses.KEY_RESIZE:
                continue
            text, action = apply_edit_key(text, ch)
            if action == "submit":
                return text
            if action == "cancel":
                return None
    finally:
        curses.curs_set(0)


def highlighted_row(rows: list[tuple], cursor: int) -> tuple | None:
    """The row under the cursor, or None when the cursor is out of range."""
    return rows[cursor] if 0 <= cursor < len(rows) else None


def apply_command(
    typed: str, row: tuple | None, groups: list[Group], collapsed: set[str]
) -> tuple[str, tuple | None]:
    """Run a `:` line against the highlighted row's project.

    Returns (footer message, row key to select). A created goal un-collapses
    its project and reloads the goals so the cursor can land on it.
    """
    cwd = row[1].cwd if row else None
    current_kind = row[0] if row and row[0] in ("goal", "slice") else "goal"
    current = row[2] if row and row[0] in ("goal", "slice") else None
    text, node = run_command(
        typed, cwd, current, current_kind, slice_parent_for_row(row)
    )
    message = f" {text} " if text else ""
    if node is None:
        return message, None
    collapsed.discard(cwd)
    attach_goals(groups)
    if row and row[0] in ("goal", "slice") and row[2].id == node.id:
        kind = row[0]
    else:
        kind = "slice" if node.parent else "goal"
    return message, (kind, node.id, cwd)


NO_GOAL_ROW = " highlight a goal or slice first "
NO_SESSION_ROW = " highlight a session first "


def goals_guard() -> str:
    """A footer warning when goals.json is unreadable, else ""."""
    return store_guard(_GOALS_STORE)


def _goal_row_refusal(row: tuple | None) -> str:
    """Why a goal/slice-only action on *row* is refused, else ""."""
    if not row or row[0] not in ("goal", "slice"):
        return NO_GOAL_ROW
    return goals_guard()


def row_noun(row: tuple) -> str:
    """`slice` for a slice row, else `goal` (D16)."""
    return "slice" if row[0] == "slice" else "goal"


def rename_prefill(row: tuple | None) -> tuple[str, str]:
    """(`:` line text, footer message) for `R`; empty text means refused."""
    refusal = _goal_row_refusal(row)
    if refusal:
        return "", refusal
    noun = row_noun(row)
    return f"{noun}:rename {row[2].name}", ""


def done_goal(groups: list[Group], row: tuple | None) -> tuple[str, tuple | None]:
    """`x`: toggle the highlighted goal or slice done; (message, key to select)."""
    if not row or row[0] not in ("goal", "slice"):
        return NO_GOAL_ROW, None
    _, g, goal = row
    try:
        done = toggle_goal_done(g.cwd, goal.id)
    except (GoalError, OSError) as e:
        return f" {_store_error_message(_GOALS_STORE, e)} ", None
    attach_goals(groups)
    state = "done" if done else "reopened"
    return f" {state}: '{goal.name}' ", (row[0], goal.id, g.cwd)


def done_session(row: tuple | None) -> tuple[str, tuple | None]:
    """`x` on a session: toggle its done mark; (message, key to select).

    The caller reloads groups, which recomputes the status from the store and
    the live processes, so this only writes the mark.
    """
    if not row or row[0] != "session":
        return NO_SESSION_ROW, None
    _, g, s = row
    try:
        done = toggle_session_done(s)
    except (SessionError, OSError) as e:
        return f" {_store_error_message(_SESSIONS_STORE, e)} ", None
    state = "done" if done else "reopened"
    return f" {state}: '{s.title}' ", ("session", s.id, g.cwd)


def move_session_row(
    groups: list[Group], row: tuple | None, target: tuple[str, Goal | None]
) -> tuple[str, tuple | None]:
    """`m` on a session: move it to *target*; (message, key to select).

    Only session rows move. The backend re-reads and writes goals.json once;
    a corrupt store refuses before anything changes.
    """
    if not row or row[0] != "session":
        return NO_SESSION_ROW, None
    _, g, s = row
    kind, goal = target
    target_id = goal.id if kind == MOVE_GOAL and goal is not None else None
    try:
        changed = move_session(g.cwd, s.id, target_id)
    except (GoalError, OSError) as e:
        return f" {_store_error_message(_GOALS_STORE, e)} ", None
    attach_goals(groups)
    key = ("session", s.id, g.cwd)
    if not changed:
        return f" already there: '{s.title}' ", key
    where = f"'{goal.name}'" if target_id and goal is not None else "loose"
    return f" moved '{s.title}' to {where} ", key


def move_to_new_goal(
    groups: list[Group], row: tuple | None, name: str
) -> tuple[str, tuple | None]:
    """`+ New goal`: create a top-level goal and move the session into it."""
    if not row or row[0] != "session":
        return NO_SESSION_ROW, None
    _, g, s = row
    try:
        goal = move_session_to_new_goal(g.cwd, s.id, name)
    except (GoalError, OSError) as e:
        return f" {_store_error_message(_GOALS_STORE, e)} ", None
    attach_goals(groups)
    return (
        f" moved '{s.title}' to new goal '{goal.name}' ",
        ("session", s.id, g.cwd),
    )


def delete_prompt(row: tuple | None) -> tuple[str, str]:
    """(confirm text, refusal message) for `D`; empty confirm means refused."""
    refusal = _goal_row_refusal(row)
    if refusal:
        return "", refusal
    noun = row_noun(row)
    return f" Delete {noun} '{row[2].name}'? y/n ", ""


def delete_goal_row(
    groups: list[Group], row: tuple
) -> tuple[str, tuple | None]:
    """Delete the highlighted goal or slice after a `y` (D16).

    A goal takes its slices with it and every session in the subtree becomes
    loose; a slice flattens only its own sessions. Selects the newest former
    member, else the parent goal, else the project heading.
    """
    _, g, goal = row
    members = goal_subtree_members(g, goal)
    parent = goal.parent if row[0] == "slice" else ""
    try:
        delete_goal(g.cwd, goal.id)
    except (GoalError, OSError) as e:
        return f" {_store_error_message(_GOALS_STORE, e)} ", None
    attach_goals(groups)
    if members:
        key = ("session", members[0].id, g.cwd)
    elif parent:
        key = ("goal", parent, g.cwd)
    else:
        key = ("group", g.cwd)
    noun = row_noun(row)
    return f" deleted {noun} '{goal.name}' ", key


def toggle_goal(groups: list[Group], g: Group, goal: Goal, value: bool) -> str:
    """Persist a goal's collapse state; returns an error message or "".

    Reloads the goals either way so rows reflect the file.
    """
    error = ""
    try:
        set_goal_collapsed(g.cwd, goal.id, value)
    except (GoalError, OSError) as e:
        error = f" {e} "
    attach_goals(groups)
    return error


def main_curses(stdscr, target: str):
    """Run the panel with mouse reporting, restoring the terminal afterwards.

    A terminal without mouse support (or a curses build without it) simply
    gets no mouse events; the keyboard path is unaffected.
    """
    old_mask = None
    try:
        _available, old_mask = curses.mousemask(curses.ALL_MOUSE_EVENTS)
    except (curses.error, AttributeError):
        old_mask = None
    try:
        _main_curses(stdscr, target)
    finally:
        if old_mask is not None:
            try:
                curses.mousemask(old_mask)
            except (curses.error, AttributeError):
                pass


def _main_curses(stdscr, target: str):
    if hasattr(curses, "set_escdelay"):
        curses.set_escdelay(25)  # Esc cancels at once, not after ~1s
    curses.curs_set(0)
    curses.start_color()
    curses.use_default_colors()
    # A true green (256-colour palette) keeps in-progress distinct from the
    # yellow needs-input mark; basic terminals fall back to COLOR_GREEN.
    green = (
        46 if getattr(curses, "COLORS", 0) >= 256 else curses.COLOR_GREEN
    )
    try:
        curses.init_pair(1, green, -1)            # in progress
    except curses.error:
        curses.init_pair(1, curses.COLOR_GREEN, -1)
    curses.init_pair(2, curses.COLOR_RED, -1)     # error
    curses.init_pair(3, curses.COLOR_YELLOW, -1)  # needs input
    curses.init_pair(4, curses.COLOR_BLUE, -1)    # idle
    curses.init_pair(5, curses.COLOR_CYAN, -1)    # group
    curses.init_pair(6, curses.COLOR_WHITE, -1)   # done (drawn dim)
    stdscr.timeout(2000)

    groups = load_groups()
    pane = panel_pane_id()
    set_panel_tag(pane)
    flt = ""
    cursor = offset = 0
    selected = None  # row_key() of the highlighted row, stable across sorts
    migrate_state()
    presented = _read_state()  # session the viewer was last asked to show
    if not presented:
        # No record yet: take the session already in the viewer as presented.
        _, panes, viewer = visible_layout(groups, target)
        presented = next((p.tag for p in panes if p.id == viewer), "")
    collapsed: set[str] = set()
    expanded: dict[str, int] = {}
    reap_state: dict[str, float] = {}
    message = ""
    message_until = 0.0
    drag = None  # session row held down for drag-to-move
    drag_since = 0.0  # when that press arrived, for the tick expiry
    wheel_last = None  # (direction, time) of the previous wheel event
    last_click_key = None
    last_click_time = 0.0

    def say(text: str, key=None):
        """Show *text* in the footer for a few seconds; select *key* if given."""
        nonlocal message, message_until, selected
        if text:
            message = text
            message_until = time.time() + 4
        if key is not None:
            selected = key

    def present(row):
        """Present *row* and report it; shared by Enter and a double-click."""
        nonlocal message, message_until, presented, groups
        warning = present_session(row, groups, target, reap_state)
        if warning:
            message = f" {warning} "
            message_until = time.time() + 4
        else:
            message = ""
            presented = row.id
        groups = load_groups()

    def file_move(row, result):
        """Show a move's footer message, un-collapse its project, reload."""
        nonlocal groups
        say(*result)
        collapsed.discard(row[1].cwd)
        groups = load_groups()

    def command_line(row, initial: str):
        """Prompt for a `:` line, run it, report it; returns the new key."""
        typed = prompt_line(stdscr, ":", initial)
        if typed is None:
            return None
        text, key = apply_command(typed, row, groups, collapsed)
        say(text, key)
        return key

    def quit_keys() -> bool:
        """Every quit key lands here: detach, and the panel keeps running.

        Returns True when the loop should end: outside tmux there is no
        client to detach, so q and Ctrl-C exit the panel instead. Further
        Ctrl-C taps while detaching are absorbed.
        """
        nonlocal message, message_until
        if not os.environ.get("TMUX"):
            return True
        for _ in range(3):
            try:
                failure = detach_panel()
            except KeyboardInterrupt:
                continue
            if failure:
                message = failure
                message_until = time.time() + 10
            break
        return False

    def fold_goal(g, goal, value):
        nonlocal message, message_until
        error = toggle_goal(groups, g, goal, value)
        if error:
            message = error
            message_until = time.time() + 4

    def set_tier(row, action: str):
        """`[` / `]` / Enter / click on *row*: one level, or flip a container.

        *action* is "show" (`]`: expand a collapsed container, else reveal one
        tier), "hide" (`[`: hide one tier, or collapse at the base tier), or
        "toggle" (Enter/click: flip a container, and on a `more` row reveal
        `… N more` or hide `… show fewer`). A session row acts on the container
        that holds it. Returns the row key to land on, or None when the row had
        nothing to show or hide.
        """
        kind, g, s = row
        if flt and action in ("show", "hide"):
            return None  # a filter ignores limits, so the keys own no state
        on_more = kind in ("more", "goal_more", "slice_more")
        # The container a row acts on, and its own row key. A `more` row acts
        # on the container it belongs to.
        if kind in ("group", "more"):
            key, container_row = g.cwd, ("group", g.cwd)
            container, folded = None, g.cwd in collapsed
        elif kind in ("goal", "slice"):
            key, container_row = s.id, (kind, s.id, g.cwd)
            container, folded = s, s.collapsed
        elif on_more:
            container = s[0]
            kind = goal_kind(g, container)
            key, container_row = container.id, (kind, container.id, g.cwd)
            folded = container.collapsed
        else:  # a session row acts on the container holding it
            goal = goal_of_session(g, s.id)
            if goal is None:
                key, container_row = g.cwd, ("group", g.cwd)
                container, folded = None, g.cwd in collapsed
            else:
                kind = goal_kind(g, goal)
                key, container_row = goal.id, (kind, goal.id, g.cwd)
                container, folded = goal, goal.collapsed

        def set_fold(value: bool) -> None:
            if container is None:
                (collapsed.add if value else collapsed.discard)(key)
            else:
                fold_goal(g, container, value)

        if on_more:
            if action == "toggle":
                action = "hide" if s[-1] else "show"
            if action == "show":
                return tier_reveal(
                    rows, groups, flt, collapsed, expanded, key, row_key(row)
                )
            if folded:
                return None  # already collapsed
            if tier_level(expanded, key) == 0:
                set_fold(True)  # the base tier's `[` collapses the container
                return container_row
            return tier_hide(
                groups, flt, collapsed, expanded, key, container_row
            )
        if action == "toggle":
            if folded:
                expanded.pop(key, None)  # expanding always shows the base tier
            set_fold(not folded)
            return container_row
        if action == "show":
            if folded:
                expanded.pop(key, None)  # expanding shows the base tier
                set_fold(False)
                return container_row
            return tier_reveal(
                rows, groups, flt, collapsed, expanded, key, container_row
            )
        if folded:
            return None  # already collapsed
        if tier_level(expanded, key) > 0:
            return tier_hide(
                groups, flt, collapsed, expanded, key, container_row
            )
        set_fold(True)  # at the base tier, `[` collapses
        return container_row

    while True:
        try:
            if message and time.time() >= message_until:
                message = ""
            # Re-derive the cursor from the selected row's identity, not its old
            # index: a session that starts working moves to the top of the list.
            unreadable = attach_goals(groups)
            rows = list(rows_for(groups, flt, collapsed, expanded))
            cursor = cursor_for_key(rows, selected)
            footer = (
                f" moving {drag[2].title}… " if drag is not None else message
            )
            result = draw(
                stdscr, groups, flt, cursor, offset, collapsed, footer, expanded,
                GOALS_UNREADABLE if unreadable else sessions_guard(),
            )
            if isinstance(result, tuple) and len(result) >= 3:
                cursor, offset, hits = result[:3]
            else:  # a test double may return the older (cursor, offset)
                cursor, offset = result[:2]
                hits = {}
            try:
                ch = stdscr.getch()
            except KeyboardInterrupt:
                if quit_keys():
                    return
                continue
            if ch == -1:
                if drag is not None and drag_expired(drag_since, time.time()):
                    drag = None  # only a press older than the limit expires
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
            row = highlighted_row(rows, cursor)

            if ch == MOUSE_KEY:
                try:
                    _id, _mx, my, _z, bstate = curses.getmouse()
                except (curses.error, AttributeError):
                    continue
                # Mouse traffic is a clock too: motion keeps arriving while a
                # drag is held, so the age bound must not wait for an idle tick.
                if drag is not None and drag_expired(drag_since, time.time()):
                    drag = None
                kind = mouse_kind(bstate)
                mouse_debug(bstate, _mx, my, kind)
                if kind in ("wheel_up", "wheel_down"):
                    fresh, wheel_last = wheel_notch(
                        kind, time.time(), wheel_last
                    )
                    if not fresh:
                        continue
                    cursor = wheel_cursor(len(rows), cursor, kind)
                    if rows:
                        selected = row_key(rows[cursor])
                    continue
                if kind == "press":
                    held, drag = drag, drag_press(drag, hits, my)
                    if held is None and drag is not None:
                        drag_since = time.time()
                    continue
                if kind in ("release", "click"):
                    held, drag = drag, None
                    outcome = mouse_release(kind, held, hit_test(hits, my))
                    if outcome is None:
                        continue
                    if outcome[0] == "move":
                        file_move(
                            held, move_session_row(groups, held, outcome[1])
                        )
                        continue
                    hit = outcome[1]
                    now = time.time()
                    action = click_kind(
                        hit, now, last_click_key, last_click_time
                    )
                    last_click_key = row_key(hit)
                    last_click_time = now
                    if action == "present":
                        present(hit[2])
                        last_click_key = None
                    elif action == "toggle":
                        selected = set_tier(hit, "toggle") or selected
                    elif action == "tier":
                        selected = (
                            set_tier(hit, more_click_action(hit)) or selected
                        )
                    else:
                        selected = row_key(hit)
                    continue
                # Motion, a zero bstate or button 2/3: never touch a held drag.
                continue
            drag = None  # any non-mouse key ends a held drag
            if ch in (curses.KEY_UP, ord("k")):
                cursor = max(0, cursor - 1)
            elif ch in (curses.KEY_DOWN, ord("j")):
                cursor = min(len(rows) - 1, cursor + 1)
            elif ch in (ord("g"), curses.KEY_HOME):
                cursor = 0
            elif ch in (ord("G"), curses.KEY_END):
                cursor = max(0, len(rows) - 1)
            elif ch == ord("q"):
                if quit_keys():
                    return
                continue
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
                typed = prompt_line(stdscr, "filter: ", "")
                if typed is not None:
                    flt = typed
                    cursor = 0
                    selected = None
                continue
            elif ch == ord(":"):
                if command_line(row, "") is not None:
                    # Land on the new goal: show its project and drop the filter.
                    flt = ""
                continue
            elif ch == ord("R"):
                text, warning = rename_prefill(row)
                if not text:
                    say(warning)
                    continue
                command_line(row, text)
                continue
            elif ch == ord("m"):
                if not row or row[0] != "session":
                    say(NO_SESSION_ROW)
                    continue
                drag = None  # the box owns the pointer; a held drag is stale
                try:
                    target = move_box(stdscr, row[1], row[2])
                except KeyboardInterrupt:
                    if quit_keys():
                        return
                    continue
                if target is None:
                    continue
                if target[0] == MOVE_NEW:
                    name = prompt_line(stdscr, "goal name: ", "")
                    if name is None:
                        continue
                    file_move(row, move_to_new_goal(groups, row, name))
                else:
                    file_move(row, move_session_row(groups, row, target))
                continue
            elif ch == ord("x"):
                if row and row[0] == "session":
                    say(*done_session(row))
                    groups = load_groups()
                else:
                    say(*done_goal(groups, row))
                continue
            elif ch == ord("D"):
                ask, warning = delete_prompt(row)
                if not ask:
                    say(warning)
                    continue
                draw(stdscr, groups, flt, cursor, offset, collapsed, ask, expanded)
                try:
                    answer = stdscr.getch()
                    while answer == -1:  # a refresh tick is not an answer
                        answer = stdscr.getch()
                except KeyboardInterrupt:
                    if quit_keys():
                        return
                    continue
                if answer != ord("y"):
                    continue
                say(*delete_goal_row(groups, row))
                continue
            elif ch in (ord("n"), ord(NEW_NO_PICKER_KEY)):
                if row:
                    harness, goal_id = new_session_plan(row)
                    installed = installed_harnesses() if ch == ord("n") else []
                    notices = []
                    if should_open_new_box(row, installed, ch):
                        drag = None  # the box owns the pointer; a drag is stale
                        try:
                            picked = new_box_flow(
                                stdscr, row, installed, harness, goal_id
                            )
                        except KeyboardInterrupt:
                            if quit_keys():
                                return
                            continue
                        if picked is None:
                            continue
                        harness, goal_id, notices, created = picked
                        if not harness:
                            say(f" {notices[0] if notices else ''} ")
                            continue
                        if created:
                            # Like `:`, show the new goal/slice at once.
                            collapsed.discard(row[1].cwd)
                            flt = ""
                    warning = new_session(
                        target, row[1].cwd, groups, reap_state, harness, goal_id
                    )
                    notices.extend(x for x in (warning,) if x)
                    if notices:
                        message = f" {'; '.join(notices)} "
                        message_until = time.time() + 4
                    groups = load_groups()
                    presented = _read_state()
            elif ch in (ord("["), curses.KEY_LEFT, ord("h")):
                if row:
                    selected = set_tier(row, "hide") or selected
                continue
            elif ch in (ord("]"), curses.KEY_RIGHT, ord("l")):
                if row:
                    selected = set_tier(row, "show") or selected
                continue
            elif ch in (curses.KEY_ENTER, 10, 13):
                if row:
                    kind, g, s = row
                    if kind != "session":
                        selected = set_tier(row, "toggle") or selected
                        continue
                    present(s)
            # Remember identity of the newly highlighted row for the next sort.
            if rows and 0 <= cursor < len(rows):
                selected = row_key(rows[cursor])
        except KeyboardInterrupt:
            if quit_keys():
                return


def list_lines(groups: list[Group]) -> list[str]:
    """Plain-text dump: projects, goals, slices nested, then sessions."""

    def row(s: Session, indent: str) -> str:
        return (f"{indent}{STATUS[s.status][0]} [{s.status:11}] "
                f"[{s.harness:6}] "
                f"{s.title[:60]:<60} {rel_time(s.last_ts):>4}  {s.id}")

    def node_lines(
        g: Group,
        goal: Goal,
        indent: str,
        children: dict[str, list[Goal]],
    ) -> list[str]:
        out = []
        for _, kind, item, _ in goal_child_items(g, goal, children):
            if kind == "session":
                out.append(row(item, indent))
                continue
            count = len(goal_subtree_members(g, item, children))
            mark = "✓" if item.done else "▾"
            out.append(f"{indent}{mark} {item.name}  ({count})")
            out.extend(node_lines(g, item, indent + "  ", children))
        return out

    labels = canonical_group_names(groups)
    out = []
    for g in groups:
        children, _ = goal_tree(g)
        out.append(labels.get(g.cwd, g.cwd))
        member_ids = goal_member_ids(g)
        goal_rows = [
            (
                root,
                goal_subtree_members(g, root, children),
                goal_age_ts(g, root, children=children),
            )
            for root in goal_roots(g, children)
        ]
        loose = [s for s in g.sessions if s.id not in member_ids]
        for _, kind, item, members in recency_top(loose, goal_rows):
            if kind == "session":
                out.append(row(item, "  "))
                continue
            count = len(members)
            mark = "✓" if item.done else "▾"
            out.append(f"  {mark} {item.name}  ({count})")
            out.extend(node_lines(g, item, "    ", children))
    return out


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
        for line in list_lines(load_groups()):
            print(line)
        return

    if not args.target:
        ap.error("--target is required unless --list is given")

    curses.wrapper(main_curses, args.target)


if __name__ == "__main__":
    main()
