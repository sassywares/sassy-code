# Panel rework: expansion, statuses, slices, moves, mouse

**QA:** manual

## Intent

Rework the control panel's everyday ergonomics: long lists expand in tiers
instead of jumping to the bottom, session statuses are accurate and meaningful
(in progress / needs input / idle / error / done), goals can contain slices,
sessions can be moved between goals and out to loose, the panel responds to the
mouse, and `n` opens one box that resolves both the harness and the target goal.
The panel stays stdlib-only, one line per row, and narrow.

## Decisions

**Expansion**

- D1: Reveals are tiered so each tier adds twice the previous increment from the
  base limit — the caps are 5, 15, 35, 75… (`base * (2**(level+1) - 1)`); the
  row shows `… N more` (remaining), and `… show fewer` hides one tier.
  (user-directed, over a fixed 5: a 100-session project reaches the end in five
  presses)
- D2: After a reveal the cursor lands on the first newly revealed row; a hide
  lands on the `more` row; a collapse lands on the container row. The row
  persists as `… show fewer` when everything is shown. (user-approved)
- D3: `[` / `]` are the universal detail pair and move exactly one level per
  press — project heading, goal, slice, `more` row, and a session row acts on
  its container. `]` expands a collapsed container at the base tier, then
  reveals the next tier; `[` hides one tier, then collapses the container at
  the base tier. `h`/`l` and arrows stay aliases. (user-approved)

**Statuses**

- D4: Five states: in progress, needs input, idle, error, done. (user-directed)
- D5: Detection stays file + process only, no harness sidecar: in progress =
  live agent process and an unresolved turn; needs input = live and the turn is
  terminal (or no turn yet); idle = no live process; error = last entry failed;
  done = user-marked. A Claude `AskUserQuestion` tool call with no result counts
  as needs input immediately. (user-approved, over a Pi extension + Claude hooks
  sidecar: one detection path, no harness setup)
- D6: An in-memory attention marker keeps a hidden session that completes on
  needs input until it is presented or a new turn starts; no backfill, cleared
  on restart. (user-approved, over keeping completed panes alive: no process
  leak, no change to the lazy background lifecycle)
- D7: Idle = no live process and no attention flag; a live session is never
  idle. A freshly spawned session with no messages is needs input.
  (from D5/D6)
- D8: A per-session done mark is toggled with `x`, sinks below active items,
  dims the row, and is revived by new activity or `x` again; it lives in
  `sessions.json` keyed by session id. (user-approved, over hiding done
  sessions: the list stays honest)
- D9: Precedence is `error > done > in progress > needs input > idle`.
  (user-approved, over done-first: a failure can never be silently archived)
- D10: `●` in progress (a truer green), `◐` needs input (yellow), `·` idle
  (blue), `✗` error (red), `✓` done (dim); done sessions and done goals dim the
  whole row. (user-directed: keep the `◐` shape; user-approved palette)

**Goals and `n`**

- D11: `n` opens a two-section box — **Goal** and **Harnesses**. A harness item
  starts immediately; Goal runs: pick harness → type name → create the goal →
  start a session in it. (user-directed)
- D12: The Goal section is `+ New goal` only; existing goals are targeted by
  highlighting them and pressing `n`. (user-approved)
- D13: Two levels: project → goal → slice. `n` on a goal + Goal creates a slice;
  `n` on a slice shows only Harnesses. (user-directed, "call it slice")
- D14: Child indentation: goal name at column 5, slice name at column 8, a
  session's status aligns to its container's name, and rollup marks move after
  the count so a name never shifts. (user-approved)
- D15: Goal-tree semantics: a goal counts, ages and ranks by its whole subtree;
  limits are 5 (project), 3 (goal children), 3 (slice sessions), all tiered;
  rollup is `✗` > `●` > `◐`, plus `✓` when the node is done; done is per node;
  the filter expands slices; `--list` nests. (user-approved)
- D16: Slice lifecycle: `R` works; names are unique within the parent; `x` as a
  goal; `D` flattens its sessions loose; deleting a goal removes its slices and
  flattens all their sessions; `:goal:create` stays top-level, new
  `:slice:create`; an empty slice gets the dim hint. (user-approved)
- D17: With one harness installed the box still opens on project and goal rows;
  slice rows and `N` go straight through. (user-approved)

**Move**

- D18: `m` on a session opens a "Move to" box — `⌂ Top level` (loose) first,
  `+ New goal` (creates a top-level goal, then moves) second, then a divider
  rule, then every goal/slice as a tree drawn with `├`/`└` connectors under its
  parent goal; a `✓` marks the container the session is in now (not done) and
  done containers are dimmed. Mouse drag arrives with P6. (user-directed:
  chose to include New goal)
- D19: Moving into a done goal/slice reopens it; order stays recency; explicit
  loose sticks and is inherited by Pi successors; running sessions are movable;
  only session rows; no cross-project target. (user-approved)

**Mouse**

- D20: Click selects; double-click presents; a project heading or goal/slice
  toggles; a `more` row moves one tier; the wheel moves the highlight;
  press-on-session then release-on-target drags; pickers are clickable;
  `Shift`-drag is needed for terminal text selection. (user-approved)

## Constraints

- Supersedes `docs/plans/goals.md` D1/N2 (one level) and its N1 (no moving
  sessions into or between goals).
- No cross-project moves; no moving goals or slices themselves; no harness
  sidecar; no unlimited nesting.
- Stdlib only; one line per row; narrow panel; never `respawn-pane -k`; session
  files are never modified by panel actions.
- Project collapse stays in memory; goal/slice collapse persists; tier state
  does not.
- No feature flag. README, CONCEPTS.md and AGENTS.md change in the same phase as
  the behaviour they describe.

## Deferred

- F1: A Pi extension + Claude Code hooks sidecar for permission prompts and
  extension dialogs. (future)
- F2: Moving goals or slices between goals. (future)
- F3: Persisting tier and project expand state. (not now)
- F4: Exact store shapes for slice `parent`, explicit-loose membership, and
  `sessions.json`. (implementation-time — P3/P5)

## Assumptions

- A1: A corrupt `sessions.json` or membership store warns in the footer, refuses
  edits, and is never overwritten, mirroring `goals.json`.
- A2: `goals.json` gains an optional `parent` on goal records; missing means
  top-level; old files load unchanged with no version bump.
- A3: Explicit loose membership is stored per project and wins over inherited
  membership.
- A4: Slice collapse persists; tier expansion state does not.
- A5: `R` on a slice pre-fills `slice:rename`; `goal:rename` stays for goals.
- A6: Attention markers are lost on restart; done sessions with missing files are
  ignored.
- A7: Done sessions and done goals share one sunk band, ordered by recency.
- A8: README, CONCEPTS.md and AGENTS.md are updated in the phases that change
  each behaviour.

## Phase tracking

| Phase | Goal | Status |
|-------|------|--------|
| P1 | Long lists reveal in tiers without jumping to the bottom | Done |
| P2 | Five accurate session statuses, including per-session done | Done |
| P3 | Goals contain slices, with correct nesting and alignment | Done |
| P4 | `n` resolves harness and goal in one box | Done |
| P5 | Sessions move between goals, slices, and loose | Done |
| P6 | The panel and session rows respond to the mouse | Done |

## Phases

### P1 — Tiered expansion

**Goal:** A project or goal with more items than the base limit reveals them in
doubling tiers, never dumps the cursor at the bottom, and moves exactly one
level per `[`/`]` press — expand/reveal with `]`, hide/collapse with `[`.

**Why this slice:** It is the smallest end-to-end change to `rows_for`,
`build_lines`, the cursor, and the keys, and every later phase that adds rows
(slices, attention) inherits the mechanics.

**Work (vertical):**
- Backend: replace `expanded: set[str]` with a tier map (container key → tier
  level). Add tier arithmetic (`base * (2**(level+1) - 1)`), and use it in
  `rows_for` (`panel.py:2187`) for project top-level items and goal sessions.
  Keep `limit_keep` pulling running sessions through the current tier. The
  `more` row is emitted whenever something is hidden or the container's tier
  level is above zero, and its payload carries both the hidden count and whether
  everything is shown.
- Frontend: `build_lines` (`panel.py:2438`) labels `… N more` vs `… show fewer`
  from that payload. `main_curses` (`panel.py:2782`) maps Enter and click
  activation of a `more` row by its label — `… N more` reveals the next tier,
  `… show fewer` hides one tier — and maps `[`/`]` on any row to one level:
  `]` expands a collapsed project/goal/slice (base tier) or reveals the next
  tier, `[` hides one tier above the base or collapses the container. After a
  reveal, set `selected` to the first row the tier added; after a hide, to the
  `more` row; after collapsing a container from a child, to the container row.
  A reveal that would hide nothing is a no-op, and the `… show fewer` row
  remains so the list can be shrunk.
- Tests/config: extend `tests/test_goals.py` row tests (tier boundaries,
  persistent `more` row, running pulled through) and add a cursor-landing test;
  extend `tests/tmux-pane-keys.sh` for `[`/`]`.
- Docs: README expansion paragraph and key list; CONCEPTS "Session limit" and
  "Goal limit".

**Done when:** A project with 100 sessions reveals 5, then 15, then 35, then 75,
then all, with the cursor on the first new row each time; `[` removes one tier;
the `more` row never disappears; goals behave identically.

#### QA — P1 (Manual)

| ID | Check | Mode | Status | Notes |
|----|-------|------|--------|-------|
| P1-Q1 | In a project with 12+ items, press Enter on `… N more`: the next tier appears and the cursor is on the first newly revealed row, not on `… show fewer`. | Manual | Done | User ran the QA pass and confirmed pass. |
| P1-Q2 | Press `[` on `… show fewer` (or activate it with Enter/click): the list returns exactly one tier and the cursor is on the `more` row. Repeat to the base tier, then `[` again collapses the container. | Manual | Done | User ran the QA pass and confirmed pass. |
| P1-Q3 | Press `]` on an expanded container row: exactly one tier is revealed and the cursor lands on the first new row; `[` reverses one tier, then collapses at the base. A session row acts on its container the same way. | Manual | Done | User ran the QA pass and confirmed pass. |
| P1-Q4 | A goal with 5+ sessions reveals in tiers from 3 and `[` reverses; the goal's `more` row also stays as `… show fewer` at the end. | Manual | Done | User ran the QA pass and confirmed pass. |
| P1-Q5 | Start a turn in a session hidden behind a tier: it appears in the list while running and is never hidden. | Manual | Done | User ran the QA pass and confirmed pass. |
| P1-Q6 | With a filter active, every matching row shows and tiers are ignored; clearing the filter restores the tier state. | Manual | Done | User ran the QA pass and confirmed pass. |

### P2 — Five statuses and session done

**Goal:** Rows show in progress, needs input, idle, error, or done from the
process and the transcript, with no stale yellow, and `x` archives a session.

**Why this slice:** Status decides the glyph, colour and sort band that P3
(slices) and P5 (moves) render; landing it before the tree keeps the visual
regression surface small.

**Work (vertical):**
- Backend: split status inference into a file-derived base
  (`panel.py:638`) and the live/attention decision in `load_groups`
  (`panel.py:2069`). Add `pending_turn()` from `last_role` and
  `last_stop_reason` only; `open_tasks`/`queued_input` stay in `is_complete()`
  for reaping and never feed status. Detect a Claude `AskUserQuestion` tool call with
  no result in `parse_claude_session` (`panel.py:493`). Add the in-memory
  attention map, populated in `reap_hidden` (`panel.py:1324`) when a hidden
  pane is complete, cleared when the session is presented or a new turn starts.
  Add `sessions.json` read/write with a corrupt-file guard (A1) and
  `toggle_session_done`.
- Frontend: `STATUS` (`panel.py:73`) becomes the five states; `init_pair`
  (`panel.py:2786`) adds a 256-colour true green for in progress with a
  `COLOR_GREEN` fallback, keeps `◐` yellow, and dims done rows. `x` on a session
  row toggles done; the footer gains `x done`/`x undo`. The sort key sinks done
  sessions with done goals (A7). `--list` (`panel.py:3036`) prints the new names.
- Tests/config: status tests for live/complete/no-message/error/done/attention
  transitions, precedence, and stale-yellow regression; store tests for
  `sessions.json` and corrupt files.
- Docs: README status table and keys; CONCEPTS "Status inference", "Done".

**Done when:** A session that finished a turn shows `◐` and a session that is
working shows a visibly distinct green `●`; a session that ended on a tool result
long ago shows `·`; a failed session shows `✗`; `x` archives and `x` again
revives; restart keeps done marks and clears attention.

#### QA — P2 (Manual)

| ID | Check | Mode | Status | Notes |
|----|-------|------|--------|-------|
| P2-Q1 | Send a message to a live session: it shows `●` green while working, then `◐` yellow when the turn finishes. | Manual | Done | User ran the QA pass and confirmed pass. |
| P2-Q2 | Switch away from a working session and let it finish: within a refresh it shows `◐`; present it, then switch away again and let it finish: it is flagged again. | Manual | Done | Failed on the user's first run: a Claude session that finished its turn while a background run outlived it stayed `●`. Root-caused against a live Claude 2.1.330 session (spawned probe) and narrowed `pending_turn()` to the turn only; background bookkeeping stays in `is_complete()`. User re-tested: pass. |
| P2-Q3 | A session whose last entry is a tool result from hours ago shows `·`, not yellow. | Manual | Done | User ran the QA pass and confirmed pass. |
| P2-Q4 | Make a tool fail: the session shows `✗` red; send a new message and let it succeed: the error clears. | Manual | Done | User ran the QA pass and confirmed pass. |
| P2-Q5 | Press `x` on a session: it shows `✓`, dims, and sinks below active items; `x` again revives it; a new turn also revives it. | Manual | Done | User ran the QA pass and confirmed pass. |
| P2-Q6 | Archive a session, then make it fail: it shows `✗`, not `✓`. | Manual | Done | User ran the QA pass and confirmed pass. |
| P2-Q7 | The green in-progress `●` and the yellow `◐` are clearly different on this terminal; idle is blue `·`, error red `✗`. | Manual | Done | User ran the QA pass and confirmed pass. |
| P2-Q8 | `./panel.py --list` shows the new status words, and done sessions carry a check. | Manual | Done | User ran the QA pass and confirmed pass. |
| P2-Q9 | Restart the panel: done marks survive; background needs-input flags are gone. | Manual | Done | User ran the QA pass and confirmed pass. |

### P3 — Goals contain slices

**Goal:** A goal can hold slices; counts, ages, rollups, limits, indentation,
rename/done/delete, filter and `--list` work at both levels.

**Why this slice:** It is the tree the `n` box and the move picker target; it
lands the data shape and the rendering together so nesting is verifiable before
either interaction is added.

**Work (vertical):**
- Backend: add an optional `parent` to `Goal` / the `goals.json` record
  (`panel.py:127`, `panel.py:1450`); `attach_goals` and `inherit_goal_members`
  build a flat list with parent links and ignore a missing parent as top-level.
  Add tree helpers: roots, children, subtree members, subtree age, rollup
  status. `goal_members` keeps direct sessions; a new helper returns a goal's
  child items (direct sessions + slices) interleaved by recency. Extend
  `new_session_plan` (`panel.py:1671`) so a slice is a valid target.
  `create_goal` gains a parent argument; add `create_slice`,
  `slice_name_taken`, `:slice:create` and `slice:rename`; make goal delete
  remove child slices and flatten every session loose.
- Frontend: `rows_for` (`panel.py:2187`) recurses one more level; `build_lines`
  (`panel.py:2438`) indents goal names to column 5 and slice names to column 8,
  aligns a child session's status to its container's name, and moves rollup
  marks after the count (D14). Done goals/slices dim the whole row. Extend the
  filter to slices and the footer actions (`R`, `x`, `D`) to slice rows.
  `goal_hint` (`panel.py:1776`) serves slices too.
- Tests/config: store tests for `parent` and old-file compatibility; row tests
  for nesting, counts, rollups, tier limits and filter; delete-cascade tests;
  column-alignment tests; `--list` nesting.
- Docs: README goals/slices section and keys; CONCEPTS "Goal", new "Slice";
  AGENTS.md dense-projects rule.

**Done when:** `:slice:create` under a goal renders an indented slice, sessions
under a goal and a slice start their status under the container name, a goal's
count and age include its slices, and deleting a goal removes its slices while
keeping every session openable.

#### QA — P3 (Manual)

| ID | Check | Mode | Status | Notes |
|----|-------|------|--------|-------|
| P3-Q1 | `:slice:create Phase 2` under a goal: a slice row appears indented, its name at column 8, and an empty slice shows the dim hint. | Manual | Done | User ran the QA pass and confirmed pass. |
| P3-Q2 | Sessions directly in a goal start their status in the same column as the goal name; sessions in a slice align with the slice name. | Manual | Done | User ran the QA pass and confirmed pass. |
| P3-Q3 | A goal with 5 direct sessions and 3 slices shows 3 children plus `… N more`, tiered; the project's top level still counts the goal as one item. | Manual | Done | User ran the QA pass and confirmed pass. |
| P3-Q4 | Collapse a goal: its slices and sessions hide; its row shows the worst descendant status (`✗`, else `●`, else `◐`) and a `✓` if it is done. | Manual | Done | User ran the QA pass and confirmed pass. |
| P3-Q5 | `x` on a slice dims and sinks it among its siblings; `x` again revives it. A done goal dims its whole row. | Manual | Done | User ran the QA pass and confirmed pass. |
| P3-Q6 | `D` a slice: its sessions become loose. `D` a goal with slices: the slices disappear and every session is loose and openable. | Manual | Done | User ran the QA pass and confirmed pass. |
| P3-Q7 | `R` renames a slice; a duplicate name in the same parent is refused; the same name in another goal is accepted. | Manual | Done | User ran the QA pass and confirmed pass. |
| P3-Q8 | `/slice-name` filters to the slice with its goal expanded; `/session-title` expands the session's slice and goal. | Manual | Done | User ran the QA pass and confirmed pass. |
| P3-Q9 | `./panel.py --list` nests slices under goals; an old `goals.json` without `parent` still loads as top-level goals. | Manual | Done | User ran the QA pass and confirmed pass. |

### P4 — One `n` box for harness and goal

**Goal:** `n` opens a two-section box that starts a session with the chosen
harness and, when Goal is chosen, creates the goal or slice and starts the
session inside it.

**Why this slice:** It completes the new-goal journey (D11–D13) on top of the
slice tree, without touching the move picker.

**Work (vertical):**
- Frontend: replace `harness_picker` (`panel.py:1725`) with a two-section box:
  `+ New goal`/`+ New slice` (contextual) and one line per installed harness.
  A divider rule separates the sections, `j`/`k`/`↑`/`↓` cross both of them,
  `Tab` switches sections, `←`/`→` do nothing, `1`–`9`/Enter picks, `Esc`/`q`
  cancels. A harness row is ` <n> <glyph> <label>`; the Goal item is ` + <label>`
  so its marker sits in the index column and its label in the glyph column. Choosing Goal then shows the harness list and a name prompt, creates
  the goal/slice, files the generated id, and starts the session with the chosen
  harness. `should_pick_harness` (`panel.py:1691`) becomes
  `should_open_new_box`: project/goal rows always, slice rows only with more than
  one harness, session rows never. `N` keeps skipping the box.
- Backend: `new_session` (`panel.py:1957`) accepts a goal or slice id and the
  chosen harness; the existing `file_session_in_goal` path is reused. A refused
  store still starts the session loose with a footer warning.
- Tests/config: picker key tests for both sections, one-harness behaviour,
  cancel at each step, `N` skipping, and a tmux regression for `n` → Goal →
  harness → name.
- Docs: README `n` section and key list; CONCEPTS "Harness picker" becomes the
  new box.

**Done when:** `n` on a project heading offers Goal and Harnesses; a harness
starts a loose session immediately; Goal → harness → name creates the goal and
starts the session in it; a goal row creates a slice; one harness still opens
the box on project/goal rows; Esc creates nothing.

#### QA — P4 (Manual)

| ID | Check | Mode | Status | Notes |
|----|-------|------|--------|-------|
| P4-Q1 | `n` on a project heading shows Goal and Harnesses; Enter on a harness starts a loose session immediately. | Manual | Done | User ran the QA pass and confirmed pass. |
| P4-Q2 | `n` on a project heading → Goal → harness → name: the goal is created and a session with that harness starts inside it. | Manual | Done | User ran the QA pass and confirmed pass. |
| P4-Q3 | `n` on a goal → Goal creates a slice under it; `n` on a goal → harness starts a session in the goal. | Manual | Done | User ran the QA pass and confirmed pass. |
| P4-Q4 | `n` on a slice shows only Harnesses; choosing one starts a session in the slice. | Manual | Done | User ran the QA pass and confirmed pass. |
| P4-Q5 | With one harness installed, project and goal rows still show the box; a slice row starts immediately. | Manual | Done | User ran the QA pass and confirmed pass. |
| P4-Q6 | `N` from the viewer skips the box and uses the inferred harness; `n` on a session row also starts directly. | Manual | Done | User ran the QA pass and confirmed pass. |
| P4-Q7 | Esc at the section, the harness step, or the name prompt creates nothing. | Manual | Done | User ran the QA pass and confirmed pass. |

### P5 — Move sessions

**Goal:** `m` moves a session between goals and slices and back to loose, with
`+ New goal` available, and the membership sticks for successors.

**Why this slice:** It is the only write path into the tree added by P3, and the
mouse drag in P6 reuses it.

**Work (vertical):**
- Backend: add `move_session(cwd, session_id, target_goal_id | None)` that
  atomically removes the id from every goal/slice in the project and either adds
  it to the target or records explicit loose (A3). Extend
  `inherit_goal_members` (`panel.py:1499`) to skip explicit-loose ids and to
  treat explicit membership (goal or loose) as the inherited thing. A target
  that is done is reopened and expanded. A no-op target returns without writing.
- Frontend: `m` on a session opens a "Move to" box listing `⌂ Top level`
  (loose) first, `+ New goal` (creates a top-level goal and moves) second, then
  a divider rule, then the goal/slice tree with `├`/`└` connectors under each
  parent goal; a `✓` marks the container the
  session is in now and done containers are dimmed. `m` on any other row shows a footer warning. The footer gains `m move`.
- Tests/config: move in/out/direct/slice, done-target reopen, loose-sticks-for-
  successor, corrupt-store refusal, no-op, and concurrent re-read.
- Docs: README move keys and known limits; CONCEPTS "Move".

**Done when:** A session moves into a goal, into a slice, from a slice to its
parent, and back to loose; a done target reopens; a moved-out Pi session's
successor stays loose; a running session keeps running across the move.

#### QA — P5 (Manual)

| ID | Check | Mode | Status | Notes |
|----|-------|------|--------|-------|
| P5-Q1 | `m` on a loose session → pick a goal: it appears in that goal; `m` again → `⌂ Top level`: it appears loose. | Manual | Done | User ran the QA pass and confirmed pass. |
| P5-Q2 | Move a session from a slice to its parent goal, then into another slice: the row follows each time. | Manual | Done | User ran the QA pass and confirmed pass. |
| P5-Q3 | Move a session into a done goal: the goal reopens, expands, and holds the session. | Manual | Done | User ran the QA pass and confirmed pass. |
| P5-Q4 | Move a Pi session out of a goal whose parent is in it; run `/fork` on the moved-out session: the fork stays loose. | Manual | Done | User ran the QA pass and confirmed pass. |
| P5-Q5 | `m` on a goal, slice, or heading shows a footer warning and changes nothing. | Manual | Done | User ran the QA pass and confirmed pass. |
| P5-Q6 | `+ New goal`: type a name; a top-level goal is created and the session lands in it. | Manual | Done | User ran the QA pass and confirmed pass. |
| P5-Q7 | Move a running session: its row changes group while the viewer keeps running. | Manual | Done | User ran the QA pass and confirmed pass. |

### P6 — Mouse

**Goal:** The panel and its rows respond to clicks, double-clicks, the wheel,
and drag-to-move.

**Why this slice:** It needs the row hit-map from P1, the tree from P3, and the
move path from P5; adding it last keeps the keyboard path stable throughout.

**Work (vertical):**
- Frontend: enable `curses.mousemask` and handle `curses.KEY_MOUSE` in
  `main_curses` (`panel.py:2782`). `draw` (`panel.py:2528`) returns the drawn
  line → item map so a click can be hit-tested. Single click selects; a second
  click on the same row within ~400 ms presents; a heading/goal/slice toggles; a
  `more` row moves one tier; a hint or gap does nothing. Wheel up/down moves the
  highlight. Press on a session remembers the id; release on a goal/slice moves
  it, on the heading makes it loose, elsewhere cancels; the footer shows
  `moving <title>…` while the button is down. The `n` and `m` boxes accept a
  click to pick and a click outside to cancel.
- Tests/config: unit tests for the line → item hit map and for click dispatch;
  tmux mouse tests are impractical, so the manual QA below is the gate.
- Docs: README mouse section with the `Shift`-drag note; CONCEPTS "Mouse".

**Done when:** Click, double-click, wheel, drag-to-goal, drag-to-loose, and
picker clicks all work, the keyboard path is unchanged, and `Shift`-drag still
selects terminal text.

#### QA — P6 (Manual)

| ID | Check | Mode | Status | Notes |
|----|-------|------|--------|-------|
| P6-Q1 | Click a session row: it highlights; double-click: it is presented in the viewer. | Manual | Done | User ran the QA pass and confirmed pass. |
| P6-Q2 | Click a project heading, a goal, and a slice: each toggles; click a `more` row: one tier changes. | Manual | Done | User ran the QA pass and confirmed pass. |
| P6-Q3 | Wheel up/down moves the highlight one row at a time. | Manual | Done | Wheel-up moves exactly one row per notch (a 50 ms same-direction debounce fixed the two-row jump). Wheel-down is inert on macOS system ncurses, which speaks a 4-button mouse protocol (`BUTTON4_PRESSED = 0x80000`, no `BUTTON5_*`), so the library drops the notch before the panel sees it; the panel's capture log shows no event at all. User accepted this and asked to ignore wheel-down for now. |
| P6-Q4 | Drag a session onto a goal/slice: it moves; drag onto the project heading: it becomes loose; release on another session or a gap: nothing changes. | Manual | Done | User ran the QA pass and confirmed pass. |
| P6-Q5 | Click an item in the `n` box and in the `m` box: it is picked; click outside either box: it cancels. | Manual | Done | User ran the QA pass and confirmed pass. |
| P6-Q6 | `Shift`-drag selects terminal text normally; without `Shift`, a drag moves a session. | Manual | Done | User ran the QA pass and confirmed pass. |
| P6-Q7 | Keyboard-only operation after mouse support is enabled is unchanged. | Manual | Done | User ran the QA pass and confirmed pass. |
