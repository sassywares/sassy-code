---
title: Treat a tagged pane as live only while an agent still runs in it
category: tmux
tags: [panes, liveness, process-table, status, reaping]
created: 2026-11-21
---

## Problem

A managed pane whose agent had exited kept its session "live" forever. The pane
was left as a plain shell still carrying `@sassy_session=<id>`, so a finished
Claude Code session could never fall back to idle and a hidden leftover shell
was never reaped.

## Root cause

Liveness was read from the pane tag alone. The tag travels with the pane, but it
does not go away when the harness process exits: after `claude` or `pi` quits,
the pane shell remains and the option is still set. The live set, `reap_hidden`
and `present_session` each decided "live" from the tag, so all three were wrong
in the same way.

The obvious fix — require an agent in the pane's process subtree — has a trap:
`process_table()` returns `[]` on any `ps` failure or timeout
(`panel.py:832`). Read literally, an empty snapshot means "no agent anywhere",
which would flip every working session to idle and reap every hidden pane in
one bad refresh.

## Solution

One shared predicate, `pane_is_live()` (`panel.py:1321`), used by every caller
that needs the answer:

```python
def pane_is_live(pane, procs):
    if not procs:
        return bool(pane.tag)          # unknown, not dead
    return pane_has_agent(pane, procs)
```

`pane_has_agent()` walks the pane's descendants for a supported harness command.
The empty-snapshot case deliberately falls back to the tag: a failed snapshot is
*unknown*, and the conservative answer is the old tag-only one. All three call
sites consume this function — the live set via `pane_live_session()`
(`panel.py:1361`), `reap_hidden()` (`panel.py:1526`) and `present_session()`
(`panel.py:3299`, which replaces a leftover shell instead of presenting it).

## Prevention

`tests/test_status.py` covers both edges: an agentless tagged pane reads `idle`
while the same pane with an agent descendant reads `needs_input`, and
`test_empty_process_snapshot_is_unknown_not_dead` asserts the session stays live
when `process_table()` returns `[]`. Any new caller that asks "is this pane
live?" must go through `pane_is_live()`, never test the tag directly.
