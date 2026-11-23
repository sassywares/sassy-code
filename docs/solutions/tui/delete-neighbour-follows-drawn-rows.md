---
title: Take the post-delete neighbour from the rows as drawn, never the raw list
category: tui
tags: [curses, selection, tiers, filter, delete]
created: 2026-11-23
---

## Problem

After `D` deleted one session, the highlight sometimes landed on the project
heading even though other sessions were still visible, and a filtered or tiered
list could move the highlight onto a `… N more` row that the delete then
removed.

## Root cause

The neighbour was computed from the session objects, not from the lines the
panel draws. `rows.rows_for()` drops filtered sessions, caps each container at
its tier, pulls in-progress sessions through the cap and inserts
`more`/`goal_more`/`slice_more` rows, so the drawn sequence is not the raw
list. Picking the next raw session could select something not drawn — the
fallback was the project heading. Selecting a tier row was worse: deleting a
session lowers the hidden count, so that row can vanish on the very next draw.

## Solution

`rows.neighbour_after_delete(rows, item)` works only on the drawn rows:

- `rows.row_index(rows, row_key(item))` finds the deleted row by identity, so
  re-sorting cannot shift the answer.
- It tries the row below, then the row above, accepting only a row in the same
  project (`item[1].cwd`).
- It skips `more`, `goal_more` and `slice_more` outright.
- With no such neighbour it returns `("group", cwd)`, the project heading.

`actions.delete_session_row()` hands the caller that key, so the highlight
lands on a row that will still be drawn. The rule is written up in
`docs/architecture.md` and `README.md` § Delete.

## Prevention

`tests/test_trash.py`:

- `test_tier_more_row_is_not_the_neighbour` deletes the last visible session,
  asserts the neighbour is the previous session and, after re-drawing without
  it, that the stored key still resolves to a session — not the `more` row.
- `test_neighbour_comes_from_the_filtered_rows_only` builds a list with a
  filter and a hidden session between two matches.
- `test_empty_project_falls_back_to_its_heading` and
  `test_D_wiring_lands_the_cursor_in_place` cover the heading and the real
  dispatch path.
