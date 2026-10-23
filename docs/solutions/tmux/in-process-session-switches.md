---
title: Follow in-process session switches so a pane keeps one identity
category: tmux
tags: [handoff, fork, clone, panes, session-store, status]
created: 2026-10-23
updated: 2026-10-23
---

## Problem

In a pane started with `pi --session <old>`, running `/handoff` (or `/fork`,
`/clone`) replaced the session without the panel noticing:

- The sidebar kept the green **running** marker on the old session and showed
  the new one as idle, even though the new session was the live one.
- Selecting the new session spawned a **second** `pi` for it in a background
  window and swapped that duplicate into the viewer, instead of switching to the
  pane that was already showing it.

## Root cause

The panel identified a pane's session from the process command line
(`--session`/`--session-id`) and the `@sassy_session` pane option. Both are
fixed when the pane starts. Pi's `/handoff`, `/fork`, and `/clone` call
`ctx.newSession()`/`createBranchedSession()` **inside the same process**: the
command line still names the session the pane was launched with, and the pane
option still carries the old id. Only the on-disk session changed.

The new session's header makes the switch observable:

```json
{"type":"session","id":"<new>","timestamp":"<switch time>","parentSession":"<old file>"}
```

## Solution

Parse `parentSession` into `Session.parent_path` and follow the link:

- `session_children()` maps a parent id to the sessions created from its file;
  a copied branch (`/fork`, `/clone`) keeps old entries but a fresh header.
- `switched_session()` walks forward while the successor was created after the
  current session's last activity, either has newer activity or an unfinished
  turn, and is not `claimed` by another pane or live process. Bounded to eight
  hops so a malformed store cannot loop.- `adopt_running_panes()` re-tags a pane with its successor, not just untagged
  panes. It refuses to steal a successor that another pane or process already
  holds, so a fork opened separately keeps its own identity.
- `load_groups()` marks the followed session as **running**, not the argv id.
  A managed pane's tag is authoritative; process command lines only count for
  Pi processes outside managed panes, so a pane's stale argv cannot mark the
  replaced session running too.
- `follow_viewer_switch()` moves the highlight (and the recorded current
  session) when the viewer pane's tag becomes a successor of the presented
  session. A highlight on any other row is left alone, so browsing is never
  interrupted, and a deliberately reopened parent keeps its own row.
- `present_session()` prefers the pane already in the viewer when a session has
  more than one tagged pane, so `Enter` focuses it instead of swapping a stale
  duplicate into view. `find_managed_pane()` skips tagged panes, so asking for
the session a pane *used* to run starts it in a new pane instead of relabelling
  the live successor.

The `created > last_ts` test matters: `/fork` from an earlier user message
copies entries with their original timestamps, so activity alone cannot reveal
the switch. A completed stale branch that was never live in this pane is not
followed, and a completed copy (`/clone`, branch-at-tip) starts being followed
once it records new activity. That keeps a live idle parent from being re-tagged
by a fork or clone that exited elsewhere.

## Prevention

`tests/test_sessions.py` covers handoff, chained switches, copied-history forks,
stale completed branches, and claimed successors. `tests/test_background.py`
covers the pane side: the status marker moves to the successor, the pane is
re-tagged (including after a restart with the option removed), and selecting the
successor keeps the same pane and PID instead of spawning a duplicate.

When changing identity logic, verify with:

```bash
tmux list-panes -a -F '#{pane_id} #{session_name} #{@sassy_session}'
```

Two panes for one session, or a tag that lags a `/handoff`, means identity was
read from the command line again instead of following the session store.
