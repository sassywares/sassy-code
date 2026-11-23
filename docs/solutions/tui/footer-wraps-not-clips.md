---
title: A pinned footer line must wrap, not clip
category: tui
tags: [curses, footer, picker, truncation, narrow-panel]
created: 2026-11-23
---

## Problem

On a narrow panel the trash box's pinned action line lost its last hint: the
user saw `⏎ restore` (or a line ending in `…`) and never learned that `d` purges.
The panel footer already wrapped, so the same hints behaved differently inside a
box and outside it.

## Root cause

A pinned footer was treated as one display line: the box measured it once and
drew it with a single `addnstr`, which stops at the box width. The main panel
had already solved this with `view.footer_lines(items, w)`, but `boxes` did not
use it, so a box footer could never occupy more than one line no matter how many
actions it named.

## Solution

`boxes.run_picker()` takes `footer_for(state) -> list[(text, attr)]` instead of a
single string. It counts the returned lines into the box height
(`h - 2 - len(footer) - separator`), draws a `PICKER_DIVIDER` rule above them,
and draws each line with `addnstr`, never joining them into one.

`boxes.trash_footer()` builds its list with the same wrapper the panel uses:

```python
items = _keymap.footer_for_scope("Trash entry")
return [
    (line, curses.A_DIM)
    for line in _view.footer_lines(items, max(1, _trash_width(state) - 2))
]
```

So a narrow panel gets two short lines and every hint stays readable; a wide one
still gets one line. The footer is also read from the keymap (`Trash entry`
scope), not typed into the box.

## Prevention

`tests/test_trash.py::test_action_line_wraps_on_a_narrow_panel` shrinks the
screen to 20 columns, asserts the footer returns more than one line, that the
joined text is exactly `⏎ restore  d purge`, and that no drawn line contains
`…`. It also asserts the separator rule is drawn above the hints.
`test_action_line_comes_from_the_keymap` pins the text to the keymap rows.
