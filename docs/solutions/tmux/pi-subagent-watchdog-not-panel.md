---
title: Pi subagent "idle for 120000ms" kills come from the extension watchdog, not the panel
category: tmux
tags: [pi, subagent, watchdog, hidden-pane, sassy-pi-extensions]
created: 2026-11-11
---

## Problem

Pi subagent runs failed with `Subagent idle for 120000ms; terminated by
watchdog.` (in `~/.pi/agent/subagent-runs.jsonl` and the parent tool result).
Because sessions are swapped into the hidden `sassy-code-bg` pane, the panel and
session switching looked like the cause. They are not.

## Root cause

The `sassy-pi-extensions` subagent extension re-armed its idle watchdog only on
child stdout. A tool call that prints nothing for more than 120s (for example
`bunx vitest run ... | tail -30`, which buffers until exit) was killed as
"idle". Reproduced in a visible pane that was never switched, using `sleep 130`.

## Solution

Fixed in sassy-pi-extensions v0.4.3 (commit `ea725df`): the watchdog pauses while
tool calls are in flight, and a separate `PI_SUBAGENT_TOOL_TIMEOUT_MS` ceiling
(default 30 minutes) bounds a stuck tool. Upgrade the extension; nothing changes
in the panel.

## Prevention

Ruled out during the investigation, so do not re-investigate:

- tmux does not throttle or stop draining hidden panes; a hidden pane's writer
  kept full rate.
- A raw `swap-pane` into `sassy-code-bg` does not break a blocking Pi subagent.

Triage rule: reproduce in a visible, never-switched pane first. If it still
dies at 120s of silence, it is the extension. The Claude-side hidden-pane issue
(resumed agents reaped) is a different, real panel bug, covered in
[`../tui/claude-code-transcripts.md`](../tui/claude-code-transcripts.md).
