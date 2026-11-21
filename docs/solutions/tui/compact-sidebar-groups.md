---
title: Keep one project's sessions from swallowing the sidebar
category: tui
tags: [curses, sidebar, layout, session-limit, paths]
created: 2026-10-24
updated: 2026-11-02
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

- `rows_for(groups, flt, collapsed, expanded)` yields a project's top-level
  items in *tiers*: `SESSION_LIMIT` (5) at level 0, then doubling caps from
  there (`base * (2**(level + 1) - 1)` → 15, 35, 75…) before a `more` row. A goal
  lists its sessions the same way from `GOAL_SESSION_LIMIT` (3 → 9, 21…). The
  heading keeps the full session count: `▾ root  (22)`.
- `expanded` maps a container key to its tier level: a project cwd for a
  project `more` row, a goal id for a `goal_more` row, and a slice id for a
  `slice_more` row. Goal children page from `GOAL_SESSION_LIMIT` (3 → 9, 21…) and
  slice sessions from `SLICE_SESSION_LIMIT` (the same 3 → 9, 21…).
  `tier_up()` / `tier_down()` change one level, and level 0 drops the entry.
- Sessions with `status == "in_progress"` (and a goal holding one) are
  appended even when they sort past the current tier, so live work is never
  hidden. The hidden count adjusts to the rows actually drawn. The panel has
  five statuses — in progress, needs input, idle, error, done — merged with
  precedence `error > done > in progress > needs input > idle`; only an
  in-progress session is pulled through a tier.
- A `more` row carries `(hidden, all_shown)` (a `goal_more` row prefixes its
  goal). It is emitted whenever something is hidden or the tier level is above
  zero, so it never appears at the base tier with nothing to reveal, and it
  stays in place once expanded, relabelled `… show fewer`, so the cursor does
  not jump when the list grows.
- `[` and `]` are the universal pair and move exactly one level per press in
  both directions, on the container row, its `more` row, or a session row
  (which acts on its container): `]` expands a collapsed container at the base
  tier, then reveals one tier per press; `[` hides one tier above the base,
  then collapses the container at the base tier. `Enter`/click activation of a
  `more` row follows the label: `… N more` reveals the next tier, `… show fewer`
  hides one tier (`more_click_action()` supplies the same direction to a
  click). A reveal lands on the first newly revealed row, a hide lands on the
  `more` row, and a collapse lands on the container row. `h`/`l` and the arrow
  keys are aliases. Revealing when everything is already shown (`all_shown`)
  is a safe no-op, so no phantom tier is created.
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

`tests/test_selection.py` covers the limit, the `more`/`show fewer` label, live
sessions past the limit, filter and collapse interaction, canonical-name
uniqueness (including `root`), and the single gap between projects.
`tests/test_goals.py` covers tier boundaries and payloads, and
`tests/test_goal_lifecycle.py` drives the `[`/`]`/`h`/`l`/`Enter` dispatch
through `main_curses`. When adding a new row kind, handle it in `row_key()`,
`cursor_for_key()`, `build_lines()` and `draw()` — the four places that switch
on `kind` — and in `set_tier()`, which owns the reveal/hide/collapse behaviour.
