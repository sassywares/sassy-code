---
title: Give a narrow table one shrinking column and move repeats into a header
category: tui
tags: [curses, picker, layout, columns, grouping, narrow-panel]
created: 2026-11-23
---

## Context

The trash picker lists deleted sessions with their title, harness, project and
age. Four columns do not fit a panel-width box, and a naive layout truncated the
last column (the age) first or repeated the project on every row.

## Guidance

Two decisions keep every row readable at any width:

- **One flexible column, and it shrinks first.** `_trash_columns()` computes the
  age width and harness width from the actual entries, subtracts their gaps,
  gives the remainder to the title, and only then shrinks: `title` keeps a
  small floor (`min(4, max(1, budget // 3))`) and the harness takes what is
  left (`return title_floor, budget - title_floor, age_w, gap`). The age is
  right-aligned inside its own width, so it never disappears behind a long
  title. The title truncates with `…` via `core.fit_cols()`.
- **Move repeated data into a group header.** The project is not a per-row
  column: `_trash_group_key()` groups entries by case-folded label then full cwd,
  and `_trash_layout()` emits one bold header per group with exactly one
  `PICKER_DIVIDER` rule between groups (never before the first, none in the
  empty states). The title then inherits the width the dropped column freed.

Structural lines are not pickable and are not selection stops: the layout
returns a line→entry map that records only entry lines, and the header uses
`PICKER_HEADING`. Tests assert that neither a rule nor a heading can be
selected, hovered or clicked.

## Applicability

Use when a box must render a table in a terminal whose width the user controls.
It does not apply to the main session list: that is one display line per session
by rule (`AGENTS.md`), so it truncates the title and right-aligns the age rather
than adding columns.
