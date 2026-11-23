"""Transcript parsers for Pi JSONL sessions and Claude Code transcripts."""
from __future__ import annotations

import json
import os
import re

from panel.core import (
    CLAUDE_SESSION_FILE_RE,
    HARNESS,
    Session,
    _infer_status,
    clean_name,
    clean_preview,
    iso_to_epoch,
    text_of,
)


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
        if t == HARNESS["pi"]["title"]["type"]:
            s.name = o.get(HARNESS["pi"]["title"]["field"], "") or s.name
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
        elif t == HARNESS["claude"]["title"]["type"]:
            custom = o.get(HARNESS["claude"]["title"]["field"], "") or custom
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


class TitleError(Exception):
    """A rename refused: no title, an unknown harness, or no readable file."""


def write_title(path: str, harness: str, title: str) -> str:
    """Append the harness's native title line to a transcript; return the title.

    The line shape is data (`core.HARNESS[harness]["title"]`), so one writer
    serves every harness. A title is metadata, not a message: a live or
    mid-turn session may be renamed. The file keeps one line per entry, so a
    leading newline is added only when the transcript does not end with one.
    """
    spec = HARNESS.get(harness, {}).get("title")
    if not spec:
        raise TitleError(f"unknown harness: {harness}")
    title = clean_name(title, "a session title", TitleError)
    try:
        with open(path, "rb") as f:
            size = f.seek(0, os.SEEK_END)
            if size:
                f.seek(-1, os.SEEK_END)
                last = f.read(1)
    except OSError as e:
        raise TitleError(
            f"cannot read the session transcript: {e.strerror or e}"
        ) from e
    prefix = "\n" if size and last != b"\n" else ""
    line = json.dumps({"type": spec["type"], spec["field"]: title},
                      ensure_ascii=False)
    try:
        with open(path, "a", encoding="utf-8", newline="\n") as f:
            f.write(prefix + line + "\n")
    except OSError as e:
        raise TitleError(
            f"cannot write the session title: {e.strerror or e}"
        ) from e
    return title
