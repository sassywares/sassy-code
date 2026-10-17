---
title: Rebind tmux's repeatable prefix arrows so arrows reach the panel after a pane switch
category: tmux
tags: [tmux, bind-key, key-repeat, input]
created: 2026-10-17
updated: 2026-10-17
---

## Problem

After moving focus back to the control panel with `prefix + ←`, the first few
arrow presses did nothing. `j`/`k` moved the selection immediately, and pressing
any unbound key made the arrows start working again. The panel itself was
healthy — the keys never arrived.

## Root cause

tmux's default prefix bindings for the arrow keys are **repeatable**:
`bind-key -r -T prefix Up select-pane -U` and the same for `Down`, `Left`,
`Right`. `-r` tells tmux to keep the prefix key table active for `repeat-time`
(500 ms by default) after the binding fires, so a following key is interpreted as
another prefix command.

Switching panes therefore leaves tmux in repeat mode. An arrow pressed inside
that window is consumed by tmux as a second `select-pane` and never reaches the
control panel. After the window expires — or after an unbound key cancels repeat
mode and is delivered — arrows behave normally. That is why the symptom looked
like intermittent curses input loss while `j`/`k` worked.

## Solution

`main` rebinds the four default pane-switch arrows **without** `-r`, so no repeat
window follows a pane switch:

```bash
# main:22-27
for key in Up Down Left Right; do
  dir="${key:0:1}"   # Up→U, Down→D, Left→L, Right→R
  binding="$(tmux list-keys -T prefix -F '#{key_repeat} #{key_command}' "$key" 2>/dev/null || true)"
  if [ "$binding" = "1 select-pane -$dir" ]; then
    tmux bind-key -T prefix "$key" select-pane "-$dir" 2>/dev/null || true
  fi
done
```

The guard matters: only a binding that is both repeatable and exactly
`select-pane -<dir>` is rewritten, so a user's custom prefix-arrow binding is
left alone. A user binding that is byte-identical to tmux's default cannot be
told apart and will be normalized (README:69).

Configuration is applied by `ensure_tmux_config` (main:31) only once a tmux
server exists, on both the in-tmux path (main:54) and before `attach` on a fresh
server (main:70). The cold-start path needs `start-server \; set-option -g
exit-empty off` (main:36-48) because `start-server` exits immediately when there
are no sessions, which would discard the new bindings.

## Prevention

`tests/tmux-pane-keys.sh` asserts the resulting binding state rather than the
intent of the code. It checks each arrow reports `0 select-pane -<dir>` via
`#{key_repeat} #{key_command}`, so a stray `-r` fails the test. It covers both
regressions that matter:

- **Cold start** — no server, session, or user config; the launcher must still
  apply the bindings (scenario 1, tests/tmux-pane-keys.sh:58).
- **Custom bindings** — a repeatable, non-default prefix binding must survive
  unchanged (scenario 2, tests/tmux-pane-keys.sh:83).

When debugging keys that vanish only right after a pane switch, inspect the
binding before suspecting the TUI: `tmux list-keys -T prefix Up` prints the
command, and a leading `-r` is the tell.
