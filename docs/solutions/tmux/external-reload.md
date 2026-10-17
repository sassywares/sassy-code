---
title: Trigger Pi's /reload from outside the process with tmux send-keys
category: tmux
tags: [pi, reload, send-keys, panel, keybinding]
created: 2026-02-14
updated: 2026-02-14
---

## Problem

After editing an extension, skill, template, theme, or context file, every open
Pi session must re-read its resources. The panel could refresh its session list,
but there was no way to apply resource changes to already-running sessions from
the control panel.

## Root cause: Pi has no external reload API

Pi 1.3.0 exposes `/reload` as an interactive TUI command only:

- `docs/slash-commands.md` lists it as a built-in TUI command.
- `docs/rpc-commands.md` states built-in TUI commands are not returned by
  `get_commands` and do not execute when sent through RPC `prompt`.
- The CLI has no `pi reload` subcommand, and `SIGTERM`/`SIGHUP` shut the process
  down instead of reloading.
- Extensions can call `ctx.reload()` from a command handler, but an already
  running interactive TUI still has no IPC channel to invoke that command.

## Solution

The panel writes the command into each managed pane's terminal, exactly as a
user would:

```python
tmux send-keys -t <pane> -l '/reload'
tmux send-keys -t <pane> Enter
```

`reload_managed_panes()` iterates the panes from `visible_layout()` and targets
only panes that are tagged with `@sassy_session` and still have a live
descendant `pi` process. Untagged panes, shells left after Pi exited, and the
control panel itself are skipped. The `r` key first refreshes the session list,
then calls this and reports how many sessions were asked to reload.

Reload deliberately ignores in-flight turns. It is a user-requested hard
refresh; guarding on session-file completion would make the key unpredictably do
nothing. The tradeoff is that `/reload` text can become steering input or
replace a draft if Pi is busy, which is accepted as part of the action.

## Prevention

`tests/test_reload.py` checks that only tagged panes with a live Pi are targeted,
that failed sends are not counted, and that the footer keeps all bindings
visible when wrapped. Keep `/reload` delivery through `send-keys`; do not
respawn or restart panes to apply resources.
