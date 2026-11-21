---
title: Frame every picker with one loop and one column model
category: tui
tags: [curses, pickers, layout, keyboard, mouse, refactor]
created: 2026-11-21
---

## Problem

The new-session box and the Move-to box had each grown their own copy of the
same frame code: terminal resize handling, a minimum-size bail-out, Ctrl-C
containment, mouse hit-testing and repaint. They drifted in spacing and in
which keys cancelled, and a box that could not be drawn still risked acting on
an item the user never saw.

## Root cause

Each box did its own `newwin`/`box`/`getch` loop and its own line formatting.
Nothing forced the two to agree on what a selectable line is, where the marker
column sits, or where the label column starts, so the contextual Goal item in
the new-session box used different columns from the Move-to box's markers, and a
slice's connector did not line up with its goal name.

## Solution

One frame loop, `run_picker()` (`panel.py:2666`), and one line-kind contract
shared by both boxes:

- `lines_for(state)` returns `(text, attribute[, kind])`. A `PICKER_DIVIDER`
  line is a real rule drawn edge to edge at the box origin; it is excluded from
  the width calculation, never selected and never pickable.
- `step(state, ch)` applies one key and returns `_PICKER_STAY` to loop or the
  result to return; Ctrl-C cancels whether curses delivers key 3 or raises
  `KeyboardInterrupt` (ISIG on), and the repaint in `finally` is contained too.
- If the box cannot be drawn — no lines, a terminal too small for a frame, or a
  `curses.error` from `newwin`/`addnstr`/`refresh` — return `fallback`/`None`
  without reading a key. A pick nobody saw must never be applied.
- Mouse goes through `picker_click()`: a left click or an outstanding press
  whose release lands inside picks; a click outside cancels; the wheel and
  other buttons are ignored.

Both boxes then share the columns: one marker column (`+`, `⌂`, `✓`, or a
slice's `├`/`└` connector), and a name column where `Top level`, goal names and
slice names begin, with a slice name following its connector. `new_box_lines()`
(`panel.py:2532`) and `move_box_lines()` (`panel.py:2928`) only produce lines;
they do not draw.

## Applicability

Use this when adding a third picker or prompt: write `lines_for` and `step`, and
call `run_picker`; do not copy the frame loop. The column model applies to
tree-shaped lists; a flat confirmation prompt can use `prompt_line` instead.
`tests/test_moves.py` pins both bail-outs (no lines, `curses.error`, a 2×4
terminal) and the exact rendered columns; `tests/test_mouse.py` covers click
selection and outside-click cancel for both boxes.
