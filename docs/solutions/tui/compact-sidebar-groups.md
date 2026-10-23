---
title: Keep one project's sessions from swallowing the sidebar
category: tui
tags: [curses, sidebar, layout, session-limit, paths]
created: 2026-10-24
updated: 2026-10-24
---

## Problem

The control panel rendered every session in the store. One busy project (71
sessions) pushed every other project below the fold, and project headings showed
long literal paths (`~/Documents/Workspace/Sassywares/tap2-workspace`), which
truncated at the panel width before the session count was readable. Adjacent
projects also ran together because headings and session rows looked alike.

## Root cause

There was no per-project display limit: `rows_for()` yielded all matching
sessions for every group. The heading used `shorten_cwd()`, which only replaced
the home prefix with `~`, so a project still competed with its own path for the
narrow panel's width. Layout emitted one line per item with no visual separator
between groups.

## Solution

- `rows_for(groups, flt, collapsed, expanded)` yields at most `SESSION_LIMIT`
  (5) sessions per project, then a `("more", g, hidden)` row. The heading keeps
  the full session count: `▾ root  (22)`.
- Sessions with `status == "running"` are appended even when they sort past the
  limit, so live work is never hidden. The hidden count adjusts to the rows
  actually drawn.
- `expanded` is a set of cwds. The `more` row toggles on `Enter`, `→`/`l`
  expands, `←`/`h` collapses, and the row stays in place (relabelled
  `… show fewer`) so the cursor does not jump when the list grows.
- The filter always shows every match, and a collapsed project shows only its
  heading, so neither state hides search results behind the limit.
- `canonical_group_names()` builds display labels from the *set* of cwds, not
  their order: home is `root`, a project is its folder name
  (`tap2-workspace`), and same-named folders grow parent components until they
  are unique (`a/app`, `b/app`). A folder literally named `root` cannot shadow
  home's label.
- `build_lines()` inserts one `("", "gap", -1)` line before every group after
  the first. `draw()` skips gap lines, and the cursor still indexes items, not
  lines.

`cursor_for_key()` falls back from a hidden session to its project's `more` row
before the heading, so a session that slips behind the limit keeps its
neighbourhood highlighted instead of jumping to the top.

## Prevention

`tests/test_selection.py` covers the limit, the `more`/`show fewer` toggle, live
sessions past the limit, filter and collapse interaction, canonical-name
uniqueness (including `root`), and the single gap between projects. When adding
a new row kind, handle it in `row_key()`, `cursor_for_key()`, `build_lines()` and
`draw()` — the four places that switch on `kind`.
