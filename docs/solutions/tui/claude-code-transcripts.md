---
title: Merge Claude Code transcripts into the Pi session list behind a harness field
category: tui
tags: [claude-code, pi, harness, session-store, transcripts, parsing]
created: 2026-10-25
updated: 2026-11-10
---

## Problem

The panel knew exactly one session format: Pi's JSONL files under
`~/.pi/agent/sessions/--<encoded-cwd>--/`. Adding Claude Code meant listing both
harnesses in one sidebar, showing which harness owned each row, and resuming a
session with the right command (`claude --resume <id>` instead of
`pi --session <id>`). The two formats disagree on almost everything.

## What Claude Code stores

- `~/.claude/projects/<encoded-cwd>/<uuid>.jsonl` (honours
  `CLAUDE_CONFIG_DIR`). The cwd's leading `/` becomes `-`; the encoding is
  lossy, so it is only a fallback when no entry carries `cwd`.
- No header line. Identity comes from the first entry with `sessionId`, `cwd`,
  and `timestamp`. The filename is a UUID and is a safe fallback id.
- `user` and `assistant` entries wrap an Anthropic message. Assistant
  `stop_reason` is snake_case (`tool_use`, `end_turn`, `stop_sequence`,
  `max_tokens`, `refusal`).
- `summary` entries carry a generated title; `last-prompt` entries echo the
  latest prompt; `system`, `mode`, `permission-mode`, `cost-state`,
  `attachment`, `file-history-snapshot`, and `atis-latch` entries carry no
  conversation.
- Injected context is `isMeta: true`. Slash commands are echoed as
  `<command-name>…</command-name>` or `<local-command-…>` text.
- Subagent work is not a session. Sidechain entries (`isSidechain: true`) may
  appear in the session file, and each subagent also has its own transcript
  nested under the session: `<encoded-cwd>/<session-uuid>/subagents/agent-*.jsonl`
  plus an `agent-*.meta.json` (`agentType`, `spawnDepth`, ...). They are never
  beside the sessions, so `_store_files()` (direct children only) never lists
  them. A sidechain entry may name a different session, so identity must be
  read from the main thread only.
- `ai-title` entries (`aiTitle`, at most about 40 characters) are the generated
  title; `custom-title` entries (`customTitle`) are a user rename. Title order:
  `custom-title`, `ai-title`, legacy `summary`, then the first real prompt
  whitespace-normalised and not capped by the parser (the panel truncates to
  its width at draw time). Prompt fallback strips harness wrappers: pure
  `<task-notification>`, `<system-reminder>`, `<agent-message>`,
  `<local-command-*>` and `Caveat:` messages are skipped (the next prompt is
  used), `<pasted_content>` keeps its inner text without the tags, and a slash
  command shows its `<command-args>` text.
- `cwd` is the launch cwd: the first main-thread entry carrying one. Later
  entries follow `cd`s (a session started in `knit` and then working in
  `knit/fe` filed itself under an `fe` group when the latest `cwd` won).
- API failures set `isApiErrorMessage` / `error` / `apiErrorStatus`, sometimes
  alongside a normal-looking `stop_reason`.

## Solution

A `Session.harness` field (`"pi"` or `"claude"`) selects:

- the parser (`parse_pi_session` / `parse_claude_session` via `parse_session`),
- the store (`PI_SESSIONS_DIR` + `CLAUDE_SESSIONS_DIR` merged by
  `session_files()`),
- the resume command (`pi --session <id>` / `claude --resume <id>`) and the
  new-session command (`pi --session-id` / `claude --session-id`),
- the row glyph (`π` / `✳`), from the single `HARNESS` table.

A new session continues the project's habit: the highlighted session's harness,
then the project's newest session's harness, then
`SASSY_CODE_NEW_HARNESS`, then whichever harness is installed. This avoids
starting `pi` on a Claude-only machine.

Status keeps its single source of truth (`_infer_status`): tool errors and API
errors win, then a user or `tool_result` turn, then `tool_use`, then the
5-minute active window. Claude's `end_turn` / `stop_sequence` / `max_tokens` /
`refusal` were added to `TERMINAL_STOP_REASONS` so hidden panes are reaped on
the same grace period as Pi's `stop` / `error` / `aborted`.

Only UUID-named Claude files are sessions. Subagent transcripts are never
enumerated, and sidechains are excluded from preview/status. Identity fields
are taken from the main thread even if a sidechain appears first.

Claude transcripts are frequently megabytes long and the panel refreshes every
two seconds, so `read_session()` caches by path, `mtime_ns`, and size. Callers
get a copy, and `_infer_status()` is re-run on that copy because the active
window is clock-relative: an unchanged file still ages from active to idle.

`/reload` stays Pi-only: Claude Code has no equivalent action, so
`reload_managed_panes()` checks `pane_has_pi()` before sending anything.

## Consequences

- Claude Code has no session-level parent link, so the `/handoff` successor
  following that keeps a Pi pane's identity accurate does not apply. Each
  resumed Claude session is its own row. `claude --resume <id>` reuses the id
  unless `--fork-session` is passed (the panel never does), and
  `pi --session <id>` keeps the header id, so id-keyed goal membership survives resume.
- `claude --resume` requires the cwd to still exist; the panel uses the parsed
  `cwd`, falling back to the encoded project directory. Resuming a deleted
  project will fail in Claude, not in the panel.
- A Claude session started with `--session-id` may not have a transcript until
  its first message; the pane is still tagged and shown in the viewer, and the
  row appears on the next refresh.
- External-process detection works the same for both harnesses because
  `command_harness()` recognises `claude` and `claude-code`/`@anthropic-ai/claude`
  paths; a shell wrapper that merely mentions `claude` is not the agent.

## Prevention

`tests/test_claude_sessions.py` covers titles, launch cwd, background tasks, and
display width. `tests/test_sessions.py::ClaudeParseTests` covers identity/preview/completion,
pending tool turns, API errors with sidechains, and meta/local-command
filtering. `CommandTests` covers harness detection and per-harness session
arguments. `tests/test_background.py` presents a Claude session with
`--resume`, verifies it stays running, and starts a new Claude session.

## Background tasks keep a hidden pane alive

Subagents run inside the `claude` process; there is no child `claude`. When the
main thread launches background work and then ends its turn with `end_turn`,
`is_complete()` was True, so `reap_hidden()` killed the hidden pane after
`REAP_GRACE` and the running subagents died with it. Evidence:
`00c23e16-...jsonl` has two Agent `tool_result`s ("Async agent launched
successfully ... agentId: aeca7e04c433750b3"), then an assistant `end_turn`,
then a user `<task-notification>` listing several `<task-id>` lines with
`<status>stopped</status>`.

The rule, parsed from the main transcript only (`Session.open_tasks`):

- Only a `tool_result` whose `tool_use_id` belongs to a background launch
  opens a task, matched at the start of the result body so a Read/Grep of
  these docs cannot open a phantom one. Launches are main-thread `Agent`/`Task`
  tool_uses (`Async agent launched successfully ... agentId: <id>`, confirmed
  on disk) and `Bash` tool_uses with `input.run_in_background` true
  (`Command running in background with ID: <id>`). No background-Bash
  transcript was on disk, so that wording is Claude Code's documented text,
  unverified. A `SendMessage` tool_use whose result is JSON saying
  `"message":"Resuming agent ..."` with a `resumedAgentId` also opens a run:
  resuming a finished agent starts it again in the background. Monitor and
  Workflow have no examples and are not matched yet.
- Tasks are tracked as runs keyed by the launching tool_use id (the Agent call,
  or the SendMessage call for a resumed run), so a finished agent can be open
  again after a resume. A `<task-notification>` carrying `<tool-use-id>` closes
  only that run, wherever it sits in the file. One without it
  (`stopped` from a resumed process, and also plain `completed` ones from
  current Claude Code) closes only runs of its `<task-id>` launched before the
  notification's first copy in the file; the notification is written several
  times, so its position is where its text first appears, and a run resumed
  after that stays open. Once a copy was consumed (dequeued, removed, or seen as
  a user entry), a later `enqueue` of identical text is a new instance (a second
  crash emits the same `stopped`) and restarts that position. One notification
  can carry several ids.
- An interim notification closes nothing. An agent that stops while its own
  background work still runs notifies with `<summary>Agent "X" is waiting on its
  own background work</summary>` and a `<note>` saying it "stopped with
  background work of its own still running" (on disk, with or without
  `<tool-use-id>`); the same task id notifies again, with a `finished` summary
  and no `<tool-use-id>`, when it truly ends. `CLAUDE_INTERIM_NOTE_RE` matches
  either phrase, but only inside `<summary>` or `<note>` (a final notification
  whose `<result>` quotes the phrase still closes its run), and such notes are
  skipped. A final notification does not always follow: a run that only ever got
  an interim one (e.g. the session ended while the agent's own work was still
  running) stays open. That errs toward keeping the hidden pane, which is
  reaped once its agent process exits (the no-agent-process path).
- The notification is often only queued (a `queue-operation` `enqueue`, then
  `remove` or `dequeue`; a `remove` with content drops the queued item with that content, which is not always the oldest, while a `dequeue` or content-less `remove` takes the oldest) and never written as a user entry, which is why queue
  content closes runs too. Duplicate copies are harmless.
- Open runs are computed once as `opened - closed`. A `<task-notification>` is
  detected per text block after stripping `<system-reminder>` blocks.
  `open_tasks` is the set of ids of runs still open.
- `Session.queued_input` is True while input is enqueued and not yet dequeued,
  removed, or answered by a main-thread assistant entry (a FIFO: `enqueue`
  appends; a `remove` with content drops the first queued item whose content
  matches, and is a no-op if none does; a `dequeue` or content-less `remove`
  drops the oldest; an assistant entry clears the queue; queued input means the
  queue is non-empty).
  The main thread has input (often the completion hand-back) it is about to act
  on, so `is_complete()` stays False.
- While any run is open or input is queued, `is_complete()` is False (never
  reaped from file state). Open runs never expire. A resumed Claude emits a
  `stopped` notification for runs of the dead process, which closes them. Open
  runs do not make a session `active`: a live session already shows `running`
  (process table / managed pane), and a dead one with stale open runs reads
  `idle` instead of `active` forever. The "no agent process in the pane" reap
  path is unchanged.
- Slash-command echoes (`<command-...>`) are not turns, so `/model` after an
  `end_turn` leaves the session complete; a command with args still supplies
  the title, a bare one does not.

A notification counts as a user turn for status but is never used as a title.

## Display width

Rows are fitted by terminal columns, not code points (`display_width`,
`truncate_cols`, `fit_cols`): East Asian wide/fullwidth characters are 2
columns, combining and zero-width characters 0. `draw()` passes only
column-fitted strings to `addnstr`.
