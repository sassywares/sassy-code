---
title: Rename opens blank everywhere, and a blank argument refuses everywhere
category: tui
tags: [rename, command-line, validation, consistency]
created: 2026-11-23
---

## Problem

`R` behaved differently by row kind: on a goal or slice it prefilled the current
name, while on a session it opened an empty argument. One key, two conventions:
a goal rename started with text the user had to clear, and a session rename
started blank, so the same action looked like two different features.

## Root cause

`actions.rename_prefill()` built the `:` line from `row[2].name` for goals and
slices; the session branch (added later) returned `"session:rename "` with no
name. Three commands, two conventions, no shared rule. Validation had the same
shape problem in the other direction: a whitespace-only argument must not become
a rename, and each command had to refuse it rather than write an empty title.

## Solution

One convention for all three, enforced in `rename_prefill()`:

- A session row returns `"session:rename "`, a goal/slice returns
  `"goal:rename "` / `"slice:rename "` — never the current name. The cursor sits
  after the space, so the user types a fresh one. A long, preview-derived or
  absent name all behave the same.
- A project row keeps its existing refusal; `row_noun(row)` names the row kind.

Blank input refuses in the same place for all three, because both writers route
through `core.clean_name()`: `parsers.write_title()` raises `TitleError` for a
session, `goals.rename_goal()` raises `GoalError` for a goal or slice. No partial
write reaches the transcript or `goals.json`.

## Prevention

`tests/test_session_rename.py`:

- `test_prefill_stays_blank_with_a_real_title` and
  `test_goal_rename_prefill_is_blank_too` assert the prefill text, not just the
  resulting command.
- `test_empty_and_whitespace_titles_refuse` loops over `""`, `"   "` and
  `"\n\t "` and asserts the transcript bytes are unchanged.

`tests/test_goals.py::test_slice_rename_is_unique_within_parent` covers the
store-side rules (uniqueness and the same refusal path).
