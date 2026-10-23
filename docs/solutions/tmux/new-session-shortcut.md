---
title: A tmux shortcut should ask the control panel, not duplicate it
category: tmux
tags: [keybindings, run-shell, send-keys, new-session, focus]
created: 2026-10-24
updated: 2026-10-24
---

## Problem

Starting a fresh session in the project you are already looking at meant
`prefix + ←` to focus the control panel, then `n`. The panel already knew how to
spawn, tag, swap in, and record a new session; only the focus round-trip was
missing. The obvious shortcuts are all wrong:

- a bare key would be stolen from Pi's prompt,
- a key bound in the `root` table would break normal typing,
- `select-pane -L \; send-keys n` types `n` into whatever pane happens to be on
  the left when the window is not a sassy-pi layout.

## Solution

The panel tags its own pane with `@sassy_panel=1` at startup. The launcher binds
one **free** prefix key (default `N`) to:

```sh
panel.py --new-session --pane #{pane_id}
```

`#{pane_id}` is the pane that was focused when the key fired. The new mode:

1. resolves that pane's window,
2. finds the pane in it tagged `@sassy_panel` whose foreground command is a
   Python interpreter (a stale tag on a shell is ignored),
3. sends it the literal key `n`, the same key the panel handles when focused.

`send-keys` writes to the pane's pty regardless of focus, and the curses loop
reads it, so the panel runs its normal `n` path: spawn in the highlighted
project, tag the pane, swap it into the viewer, write the current-session state.
The key never moves focus; the viewer stays in front of the user. A window with
no panel gets a `sassy-pi: no control panel in this window` message instead of
stray input.

## Choosing the key

Check the live prefix table before committing to a key:

```sh
tmux list-keys -T prefix -F '#{key_command}' N   # empty means free
```

`N` is free in tmux 3.8's defaults and in the user's server; `n` is taken
(`next-window`), as are `C-n`, `C-p`, and the other control keys. The installer
never overwrites a binding: it refuses (with a note) when the key is taken, and
rebinds only when the existing command already contains `--new-session`, so
relaunching refreshes the panel's path. `SASSY_PI_NEW_KEY` changes the key.

## Prevention

`tests/tmux-pane-keys.sh` asserts the key is installed on a cold start, is
idempotent, and leaves a user's binding untouched. The isolated tmux tests in
`tests/test_background.py` assert the panel of the focused window receives `n`
without focus moving, and that a tagged non-Python pane is rejected. When adding
a shortcut, reuse a panel action through `send-keys` rather than reimplementing
spawn or swap logic, and verify the key is free on the live server.
