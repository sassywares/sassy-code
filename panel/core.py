"""Constants, records and text helpers shared by the panel.

The bottom of the import graph: `panel.core` imports nothing else from the
`panel` package.
"""
from __future__ import annotations

import curses
import os
import re
import time
import unicodedata
from dataclasses import dataclass, field
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
# Move-box target kinds: loose (top level), an existing goal/slice, a new goal.
MOVE_LOOSE = "loose"
MOVE_GOAL = "goal"
MOVE_NEW = "new"

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


def _infer_status(s: Session) -> str:
    """File-only base status: a failure, else idle without a live process."""
    return "error" if s.last_error else "idle"


def mark_attention(session_id: str) -> None:
    """Remember that a hidden session is waiting for the user (D6)."""
    _ATTENTION.add(session_id)


def clear_attention(session_id: str) -> None:
    _ATTENTION.discard(session_id)


def attention_active(session_id: str) -> bool:
    return session_id in _ATTENTION


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
