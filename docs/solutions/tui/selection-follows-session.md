---
title: Track the highlighted row by identity so it survives re-sorting
category: tui
tags: [curses, sorting, selection, cursor]
created: 2026-02-14
updated: 2026-10-23
---

## Problem

With several sessions listed, highlighting one and waiting for it to start
working moved it to the top of its group, but the reverse-video highlight stayed
on row #2. The panel was now highlighting whichever session had taken that row
index, not the session the user had chosen.

## Root cause

`main_curses` kept only `cursor`, an integer index into the flattened rows. Every
refresh (`ch == -1` after the 2 s timeout, `r`, `n`, `Enter`) reloads groups and
sorts sessions and groups by `last_ts` descending. A sort is a permutation, so
the same index points at a different row afterwards.

## Solution

Store the highlighted row's identity instead of its index:

- `row_key(item)` returns `("session", id, cwd)` or `("group", cwd)`.
- At the top of every loop iteration, `cursor_for_key(rows, selected)` converts
  that key back into an index against the freshly sorted rows.
- After a key event changes the cursor, the new `row_key(rows[cursor])` is
  stored in `selected`.
- `cursor_for_key` falls back to the containing group header when the session is
  hidden by a collapse or filter, and to row 0 when it no longer exists.

Reloads can therefore reorder the list freely; the highlight follows the logical
row. Filter changes reset `selected` to `None` so a new filter starts at the top.

## Prevention

`tests/test_selection.py` exercises `row_key`/`cursor_for_key` directly by
re-sorting synthetic groups, including group-level rows, collapse fallback, and
the empty-list case. `tests/test_background.py` continues to cover pane
lifecycle. Keep cursor state as an identity, never as an index, when adding new
sort keys.

## Related

A session can also change identity in place (`/handoff`, `/fork`, `/clone`),
rather than just moving to another row. `follow_viewer_switch()` then moves the
highlight to the successor of the presented session. The successor test uses the
shared handoff lineage, not a direct parent link, because a handoff from a
handoff can parent the session the Pi process was started with (making the new
session a sibling of the previous child). The idle refresh passes the row under
the cursor as the highlight anchor, so the follow also works before the first
key press. See
[`../tmux/in-process-session-switches.md`](../tmux/in-process-session-switches.md).
