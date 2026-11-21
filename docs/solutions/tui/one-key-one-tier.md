---
title: Give the tier step and the fold one key, and land on what the label says
category: tui
tags: [curses, tiers, collapse, more-row, keys]
created: 2026-11-21
---

## Problem

Expanding a long project or goal felt broken: `]` did nothing once the container
was already expanded, `[` collapsed the whole thing instead of hiding one tier,
and activating the `… show fewer` row was a no-op that left the list unchanged.
Users had to press `[` twice (or collapse and re-expand) to see the rows they
had just revealed.

## Root cause

Two independent axes of state were wired to separate actions:

- Tier level lived in `expanded` and could only be raised from a `more` row's
  activation.
- Collapse lived on the container (`collapsed` or `Goal.collapsed`) and `[`/`]`
  toggled that flag on a container or session row.

So on a container row, `]` only expanded (already expanded ⇒ nothing) and `[`
only collapsed (throwing away the visible tier). The `more` row had the
opposite gap: its activation always ran "reveal", even when its own label said
`… show fewer`, so the one row that could shrink the list could not.

## Solution

`App.set_tier()` (`panel.main`) treats both keys as one level per
press, on any row that owns a container:

- `show` (`]`/`l`/`→`): expand a collapsed container at the base tier (dropping
  any stored level); otherwise `tier_reveal()`.
- `hide` (`[`/`h`/`←`): at a raised tier `tier_hide()`; at the base tier there
  is no tier left to hide, so collapse the container.
- `toggle` (Enter/click): flip a container; on a `more` row follow its label.
  `more_click_action(row)` (`panel.mouse`) returns `"hide"` when the row is
  already `… show fewer`, `"show"` otherwise.

Two supporting rules make the single step feel right:

- `tier_reveal()` (`panel.rows`) advances the stored level until the hidden
  count actually drops. In-progress sessions are pulled past the cap, so a tier
  whose cap adds no new row would otherwise consume a press and look inert; it
  skips those levels instead, then lands on the first newly added row.
- With a filter active, `set_tier` refuses both keys. A filter ignores limits
  and expands everything, so mutating the tier there would change state the
  user cannot see; clearing the filter restores the stored tiers untouched.

## Prevention

`tests/test_goal_lifecycle.py` covers the key equivalence and the bug directly:
`]`, `l`, Enter and click reveal the same next tier; `[`, `h` and Enter on
`… show fewer` hide one tier and land on the surviving `more` row; reveal on
`… show fewer` is a no-op; and a session row's `[` collapses its container.
`tests/test_mouse.py` pins `more_click_action` to the row's label. When adding a
new row kind, route it through `set_tier` rather than introducing a second
collapse key.

## Related

The tier caps and `more` row rendering are described in
[`compact-sidebar-groups.md`](../tui/compact-sidebar-groups.md).
