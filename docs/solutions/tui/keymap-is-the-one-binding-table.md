---
title: One keymap table is the only source of bindings and their displays
category: tui
tags: [curses, keys, keymap, footers, readme, tests]
created: 2026-11-23
---

## Context

Footers, the `?` keys menu and the README `## Keys` table all describe the same
bindings from different places. Before this, the footer tuples in `view.py` and
the dispatcher in `main.py` drifted: a key could work with no hint, a hint could
name a key that no longer existed, and trimming a crowded display meant deleting
a binding.

## Guidance

`panel/keymap.py` holds one `ROWS` tuple of
`Row(scope, keys, action, footer, menu, also, listed)`. Everything else is a
renderer, never a second list:

- `keymap.footer_items()` → `view.footer_items()` draws exact per-scope hints
  (`⏎ present  R rename  m move  x done  D delete`).
- `keymap.menu_rows()` → `help` draws the `?` menu.
- `keymap.listed_rows()` → the README table; `tests/test_keys.py` parses the
  README section and asserts the same rows.
- `also` holds every hidden alias (`j`/`k`, `h`/`l`/`←`/`→`, `Home`/`End`); the
  display never renders it, but the binding stays.
- `menu=False` hides a row from `?`; `listed=False` hides it from the README
  table. `g`/`G` use both and keep working. Box keys keep `menu=False` and stay
  in the README table.

Two directions must both hold, and both are tested by parsing source rather than
by review:

- `test_every_advertised_token_is_handled_by_its_owner` checks each advertised
  token against the module that must handle it (`OWNERS`).
- `test_dispatched_keys_have_a_keymap_row_or_alias` reads
  `App.dispatch_key`'s `if ch in (...)` conditions, maps `curses.KEY_*` and
  `core.NEW_NO_PICKER_KEY` to their display tokens, and fails when a dispatched
  key has no row or `also` alias. That test, not a reviewer, is what keeps the
  table honest as the dispatcher grows.

## Applicability

Applies whenever several surfaces describe one set of actions: make one data
table and render every surface from it. Trimming a display is a flag on a row,
never a removed binding. If a key only makes sense inside a box, it still gets a
row (`menu=False`) so the coverage test has something to check.
