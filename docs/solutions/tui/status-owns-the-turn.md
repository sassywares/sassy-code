---
title: Let the turn decide status and background work decide reaping
category: tui
tags: [status, claude-code, background-tasks, reaping, stop-reason]
created: 2026-11-21
---

## Problem

A live Claude Code session with a background run stayed on `●` in progress
forever. Its main thread had finished its turn, but an `Agent` or background
bash run it had launched was still going, so the row never became needs input
and the user saw work that was not happening.

## Root cause

`pending_turn()` consulted the background bookkeeping before the stop reason.
Any open `open_tasks` entry or a `queued_input` flag made the turn look
unresolved, even when the last assistant message was a clean `end_turn`. Claude
Code is explicit that these are different clocks: a background run can outlive
the main thread's turn, and the user is free to type while it does, so the turn
is over even though the task is not.

The behavior was confirmed by spawning a real Claude session with a background
run, not by inference from the transcript shape.

## Solution

`pending_turn()` (`panel.status`) reads only the turn itself: no messages means
not pending, an unanswered `AskUserQuestion` means blocked rather than working,
a trailing user/tool-result means in progress, and anything but a terminal stop
reason means in progress. `open_tasks` and `queued_input` are not mentioned.

That bookkeeping stays in `Session.is_complete()` (`panel.core`), whose job is
reaping, not status: it returns `False` while a background run or queued input
is outstanding, so a hidden pane is never reaped out from under live work. The
two questions get two different answers from two different functions.

## Prevention

`tests/test_claude_sessions.py` pins both sides:
`test_open_background_run_does_not_pin_a_finished_turn` asserts a live session
with an open task and an `end_turn` reads `needs_input` while `is_complete()`
stays `False`, and `test_open_tasks_block_completion_but_status_stays_idle`
asserts tasks alone never make a session active. When adding a new harness
signal, decide first whether it describes the turn (status) or the process
(reaping); do not let it leak across.
