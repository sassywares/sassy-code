---
title: Size the panel pane with `-b` so the control panel gets the narrow side
category: tmux
tags: [tmux, layout, split-window]
created: 2026-10-17
updated: 2026-10-17
---

## Problem

The control panel kept rendering at roughly three quarters of the window no
matter how small `SASSY_CODE_WIDTH` was set. Lowering the percentage from 38% to
30% to 22% changed nothing visible, so it looked as if the width setting was
broken.

## Root cause

`tmux split-window -h -l <size>` applies `<size>` to the **new** pane and gives
the remainder to the existing one. The launcher created the new pane on the
right (the default for `-h`) while the control panel ran in the original,
untouched pane. The percentage was therefore shrinking the *session viewer*,
not the panel.

```
# panel runs here (gets remainder ~78%)          # new pane (gets 22%)
[session-viewer / control-panel]        [            unused          ]
```

The fix is a one-flag change: `-b` creates the new pane **before** the current
one (left, for `-h`), so the sized pane is the panel and the remainder goes to
the viewer.

## Solution

Create the panel pane with `-b` and let the pre-existing pane be the session
viewer:

```bash
# main:56 and main:66
PANEL_PANE="$(tmux split-window -h -b -l "$WIDTH" -P -F '#{pane_id}')"
```

Verified at 160 columns: panel `35x40` at `left=0`, viewer `124x40` at
`left=36`.

## Prevention

Check the resulting geometry, not the intent of the flag, whenever a tmux layout
is built. This command makes the sizes explicit and catches the swapped case
immediately:

```bash
tmux list-panes -t "$WIN" -F '#{pane_id} #{pane_width}x#{pane_height} left=#{pane_left}'
```

Assert that the panel pane is the narrow one and sits at `left=0`; a wide panel
at `left=0` means `-b` was dropped or inverted.
