---
title: Keep switched-away sessions alive by swapping panes instead of respawning
category: tmux
tags: [tmux, swap-pane, pane-options, background, lifecycle]
created: 2026-10-17
updated: 2026-10-18
---

## Problem

The first version of the panel resumed a session with:

```bash
tmux respawn-pane -k -t <viewer> -c <cwd> "pi --session <id>; exec $SHELL"
```

That is fine for browsing, but it kills the process that was in the viewer. If
the user had dispatched a long Pi turn and then selected another session, the
in-flight turn was destroyed. The panel also had no way to switch back to the
same live process: each selection started a fresh one.

## Root cause

`respawn-pane -k` replaces the pane's process. It is a destructive operation by
design. The viewer was being treated as the session, when it is really only a
**slot**. Pane ids are stable per process, not per screen position: swapping two
panes moves their ids with the processes, so any cached "viewer pane id" becomes
wrong after the first swap.

## Solution

Use a dedicated background session, `sassy-code-bg`, with one hidden single-pane
window per live session. Give every managed pane an authoritative pane option:

```bash
tmux set-option -p -t <pane> @sassy_session <session-id>
```

To present a session, start it in a hidden background window only if it does not
already have a live pane, then swap it with the viewer:

```bash
tmux swap-pane -d -Z -s <background-pane> -t <viewer-pane>
tmux select-pane -t <background-pane>   # now in the viewer
```

`-d` keeps the active pane unchanged during the swap; `-Z` preserves zoom.
Pane options and processes travel with the swapped panes, so the displaced
working session is now hidden but still running, scrollback and cwd included.
The next selection swaps that same pane back instead of spawning a duplicate.

Re-derive the viewer after every swap from the panel's own tmux pane:

```bash
tmux display-message -p -t "$TMUX_PANE" '#{pane_id} #{window_id}'
tmux list-panes -a -F '#{pane_id} #{window_id} #{session_name} #{pane_pid} #{@sassy_session}'
```

The viewer is the other pane in the panel's visible window. Querying all panes
also makes tagged background panes adoptable after a panel restart. A pane whose
Pi command names a known session is adopted and tagged even if its option was
lost, so adoption is lazy rather than dependent on a prior panel process.

Completion is read from the session JSONL, never from the screen. A hidden pane
is safe to reap when its latest assistant message has `stopReason` `stop`,
`error`, or `aborted`; keep it while the last message is a user/tool result,
while the assistant is at `toolUse`, or when no terminal stop reason exists.
Liveness comes from the pane's own process tree (`#{pane_pid}` plus its
descendants), not a global process scan: the same session id can also belong to
an external process elsewhere. A hidden pane with no descendant `pi` is an
orphan shell and is removed immediately. Otherwise, always apply the refresh
grace before reaping a hidden completed pane, including the pane just displaced
by a swap: the JSONL can lag the process, so a file that still looks complete
must never cause an immediate kill.

Quitting moves nothing. `q` and Ctrl-C only detach the client from the
`sassy-code` session (`detach_panel()` runs `tmux detach-client -s` for the
panel's own session); the panel keeps running, so the viewer, any other window
and every agent stay exactly where they are, and running `sassy-code` again
reattaches. This replaced an earlier design that stashed live agents behind
placeholders and then killed the session: it kept finding ways to lose agents.
End the session for good with `tmux kill-session -t sassy-code`. The launcher's
trailing command only kills the panel pane, so a panel crash leaves the viewer,
and the rebuild path adds a window and keeps the old one.

## Prevention

`tests/test_background.py` starts an isolated `tmux -L` server with
`-f /dev/null` and a fake `pi` on PATH. It asserts that switching away leaves the
old pane and PID alive, switching back uses the same pane, completed hidden
panes are reaped after the grace period, orphan shells are removed when Pi
exits, external sessions (including `--session-id`) are refused, quit keys only detach (session, panel, viewer and agent pids survive, a second
attach sees the same panel, re-running `main` adds no second panel), and pane
options survive swaps. Never point these
tests at the user's tmux server.

When changing this code, inspect pane identity rather than variable names:

```bash
tmux list-panes -a -F '#{pane_id} #{window_id} #{session_name} #{@sassy_session}'
```

A missing option after a swap means the identity was cached rather than carried
by the pane; a new pane id for the same session means a duplicate was spawned.
