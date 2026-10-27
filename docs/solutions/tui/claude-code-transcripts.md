---
title: Merge Claude Code transcripts into the Pi session list behind a harness field
category: tui
tags: [claude-code, pi, harness, session-store, transcripts, parsing]
created: 2026-10-25
updated: 2026-10-25
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
- Subagent work is in the same file with `isSidechain: true` (separate
  `agent-*.jsonl` files also exist beside sessions). A sidechain entry may name
  a different session, so identity must be read from the main thread only.
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

Only UUID-named Claude files are sessions. `agent-*.jsonl` subagent transcripts
are skipped, and sidechains are excluded from preview/status. Identity fields
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
  resumed Claude session is its own row.
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

`tests/test_sessions.py::ClaudeParseTests` covers identity/preview/completion,
pending tool turns, API errors with sidechains, and meta/local-command
filtering. `CommandTests` covers harness detection and per-harness session
arguments. `tests/test_background.py` presents a Claude session with
`--resume`, verifies it stays running, and starts a new Claude session.
