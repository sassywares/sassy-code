# Goals: group a project's sessions by feature

**QA:** manual

## Intent

Work on a large feature runs in phases, usually one session per phase, and a
project's flat session list makes those phases hard to follow. Goals add one
level of user-created, folder-like grouping inside a project: a goal holds the
sessions of one feature, expands and collapses, can be renamed, marked done, or
deleted, and new sessions started inside it land in it. Goals are panel state
only; Pi and Claude Code never know about them. Both harnesses are first-class.

## Decisions

- D1: Goals are one level deep: project → goal → sessions. (user-approved, over nested goals: the narrow panel loses title width per level)
- D2: Goals live only in the panel; Pi and Claude Code never know about them. (user-directed)
- D3: All panel state moves to `~/.sassywares/sassy-code/`, holding the current-session file and `goals.json`. (user-directed, over a loose `~/.sassy-code-goals.json`: one home for panel state)
- D4: On first start, if `~/.sassy-code-current` exists and the new file does not, move it into the new directory. (user-approved, over ignoring it or reading both forever)
- D5: `:` opens a command line; `/` stays a pure filter. (user-approved, over overloading `/`: filter text beginning with "goal" would collide)
- D6: `:goal:create <name>` creates a goal in the highlighted row's project. (user-directed, over `:goal:new` and bare `:goal`)
- D7: A highlighted goal row gets a contextual footer. (user-directed)
- D8: Delete removes the goal and flattens its sessions back to loose; session files are never touched. (user-directed)
- D9: Done marks a goal with a check. (user-directed)
- D10: `R` opens the `:` line pre-filled with `goal:rename <old name>`, applying to the highlighted goal; `:goal:rename` ships now. (user-directed, over bare `:rename`: one namespace for goal commands)
- D11: Goal-row footer: `⏎ toggle  n new  R rename  x done  D delete`. (user-approved; `n new` added by user)
- D12: Enter toggles, `h`/`l` collapse/expand goals, exactly like projects. (from code: `panel.py:1987-2010`)
- D13: `D` asks `Delete goal '<name>'? y/n` in the footer; only `y` deletes. (user-approved, over immediate delete: grouping is unrecoverable)
- D14: `x` marks done: check, collapse, sink below active goals; `x` again reopens. (user-approved, over check-only and hiding)
- D15: Goals and loose sessions interleave by recency (a goal ranks by its newest session); done goals sink to the project's bottom; goal rows show count and age of newest session. (user-directed, over goals-first: most recent work on top)
- D16: Sessions inside a goal are ordered newest first. (user-approved, over phase order and creation order)
- D17: A Pi `/handoff`, `/fork`, `/clone` successor joins its parent's goal, resolved at load time from `parentSession` (siblings included), not only on pane re-tag. (user-approved, over `n`-only membership: a handoff is the next phase)
- D18: `n` / `N` on a goal row or a session inside a goal starts the session in that goal; on a project heading or loose session it starts loose. (user-directed)
- D19: Starting a session in a done goal reopens it. (user-approved, over staying done or starting loose)
- D20: A project shows its 5 newest top-level items (a goal counts as one); an expanded goal shows its 3 newest sessions plus its own `… N more` row; running sessions are always pulled in past both limits; a collapsed goal with a running session shows `●` on its row. (user-directed: 3 per goal)
- D21: Goal collapse state persists in `goals.json`; new goals start expanded; project collapse stays in-memory. (user-approved, over persisting projects too or nothing)
- D22: An empty goal shows `(0)`, takes its age and rank from creation, and has one dim, unselectable sarcastic hint line mentioning `n`. (user-directed)
- D23: The hint is chosen stably from the goal's id. (user-approved, over per-start and rotating: no flicker on the 2s refresh)
- D24: `--list` prints goals as nested headings, done ones checked. (user-approved)
- D25: `/` matches goal names as well as sessions; a name match shows all its sessions; a filter expands goals (done included) and ignores limits. (user-approved, over sessions-only)
- D26: `n` in the panel on a project heading or goal row opens a boxed harness picker built from `HARNESS` (glyph + label per line), preselected with the inferred harness, `j`/`k`/`1`–`9`/Enter to pick, Esc/`q` to cancel, skipped when only one harness is installed. Inference on a goal row: goal's newest session → project's newest → `SASSY_CODE_NEW_HARNESS` → installed. (user-directed, over a fixed rule: more harnesses are coming)
- D27: Nothing else opens the picker: `n` on a session row and the `N` prefix shortcut (any row) use the inferred harness directly. (user-directed)
- D28: A session started by Pi `/new` or Claude Code `/clear` stays loose; documented as a known limit. (user-approved, over a pane heuristic: no link means guessing; new phases use `n` / `N`)
- D29: sassy-code only runs in its own dedicated `sassy-code` tmux session, never inside another session. Run from inside tmux, it switches the client to that session (creating it if needed) rather than splitting the current window. (user-directed at QA, over splitting the current window: re-running nested a second panel)
- D30: `q` and Ctrl-C only detach the client from the `sassy-code` session (`tmux detach-client`). The session, panel, viewer and agents keep running, and running `sassy-code` again reattaches. Nothing is killed or moved on quit; ending the session for good is the user's own `tmux kill-session -t sassy-code`. (user-directed at QA, over ending the session: the first rework's kill path kept finding ways to lose agents)
- D31: The empty-goal hint pool grows to about 12 lines, and no two empty goals in a project show the same line: the newer goal takes the next free line. A goal keeps its line across refreshes and restarts; a newer goal's line can change only when an older empty goal gets its first session. Refines D23. (user-approved at QA, over a larger pool alone)
- D32: Reopening a done goal with `x` also expands it. Refines D14. (user-directed at QA)
- D33: Enter on a project heading reads `⏎ toggle` in the footer. (user-directed at QA)
- D34: A highlighted goal row's footer shows only the goal actions, hiding move/filter/cmd/reload/quit. Refines D11. (user-directed at QA)
- D35: A goal row's arrow sits in the same column as a session row's harness glyph, so goal names align with loose session titles; nested sessions keep their indentation. (user-directed at QA)

## Constraints

- Stdlib only; one line per row; harness glyph stays the only harness mark; narrow panel (AGENTS.md conventions).
- Harness behaviour stays data in `HARNESS`; the picker reads it, no `if harness ==` branches.
- `N` keeps reusing the panel's own action via `send-keys`; no spawn logic in the binding (`docs/solutions/tmux/new-session-shortcut.md`).
- Never `respawn-pane -k`; goal actions never kill or touch session processes or files.
- Behaviour change for everyone: `n` on a project heading now opens the picker (user-directed, D26).
- Non-goals: N1 moving sessions into or between goals; N2 nested goals; N3 harness awareness of goals; N4 persisting project collapse; N5 sharing goals across machines.

## Deferred

- F1: `:goal:delete` and `:goal:done` as typed commands. (user-deferred — later)
- F2: Confirm `pi --session <id>` and `claude --resume <id>` keep writing the same session id, so membership survives resume. If either does not, stop and return to the user. (implementation-time — P4)
- F3: Exact hint copy (seeded from: "n. it's not going to plan itself.", "press n. or keep admiring the empty folder.", "a goal with zero sessions. bold strategy. n.", "n starts a session. revolutionary, I know.") and the internal key `N` sends to skip the picker. (implementation-time)

## Assumptions

- A1: Empty name, unknown command, or `:goal:rename` with no goal highlighted → footer message, no change. (user-delegated)
- A2: Duplicate goal name in the same project (case-insensitive) is refused; across projects is fine. (user-delegated)
- A3: Long goal names truncate with `…`; `:` allowed inside a name. (user-delegated)
- A4: A goal's session id with no session file is hidden, never pruned (both harnesses may write the file only after the first message). (user-delegated)
- A5: Concurrent panels: each change re-reads `goals.json`, applies, writes via temp file + rename; the panel reloads it when its mtime changes. (user-delegated)
- A6: A corrupt `goals.json` is never overwritten: goals hidden, footer warns, goal actions refused. (user-delegated)
- A7: A project with no remaining sessions disappears; its goals stay in the file, unseen. (user-delegated)
- A8: The cursor lands on a newly created goal; goals get their own `row_key` identity so the cursor follows them across resorts. (user-delegated)
- A9: The `:` prompt generalises `prompt_filter` (`panel.py:1864`) to take a label and pre-filled text. (user-delegated)
- A10: `goals.json` shape: `{"version": 1, "projects": {"<cwd>": [{"id", "name", "created", "done", "collapsed", "sessions": [ids]}]}}`; UUID goal ids; no file means today's behaviour. (user-delegated)
- A11: No feature flag; README, CONCEPTS.md, AGENTS.md updated alongside the phases that change behaviour. (user-delegated)
- A12: Tests cover the store, migration and corrupt file; `rows_for` with goals; successor inheritance; the picker; `--list`; the tmux key regression. (user-delegated)

## Phase tracking

| Phase | Goal | Status |
|-------|------|--------|
| P1 | Panel state lives in `~/.sassywares/sassy-code/` | In progress |
| P2 | Create a goal and see it in the panel | Blocked |
| P3 | Start sessions inside a goal | In progress |
| P4 | Pi successors stay in their goal | In progress |
| P5 | Busy projects stay readable with goals | In progress |
| P6 | Rename, finish, and delete goals | In progress |
| P7 | Pick the harness for a new session | In progress |

## Phases

### P1 — State directory

**Goal:** The panel reads and writes its state under `~/.sassywares/sassy-code/`, moving the old current-session file once.

**Why this slice:** Every later phase writes `goals.json` here; landing the directory and migration alone keeps that change verifiable on its own.

**Work (vertical):**
- Backend: replace `STATE_FILE` (`panel.py:40`) with a state-directory helper; `_read_state` / `_write_state` (`panel.py:1241-1254`) use `~/.sassywares/sassy-code/current`; one-shot move of `~/.sassy-code-current` when the new file is absent (D3, D4).
- Tests/config: unit tests for fresh install, migration, and "both exist" (new wins, old left alone); AGENTS.md notes the state directory.

**Done when:** After upgrading, the panel reopens on the previously presented session and the old file is gone from `~`.

#### QA — P1 (Manual)

| ID | Check | Mode | Status | Notes |
|----|-------|------|--------|-------|
| P1-Q1 | With `~/.sassy-code-current` present from the old version, start `sassy-code`: the panel highlights the same session it was presenting before, `~/.sassywares/sassy-code/current` exists, and `~/.sassy-code-current` is gone. | Manual | Done | User approved 2026-10-30. |
| P1-Q2 | Present a different session, end the session with `tmux kill-session -t sassy-code` and run `sassy-code` again: the panel reopens on that session. | Manual | Done | User approved 2026-10-30. |
| P1-Q3 | Remove `~/.sassywares` entirely and start: the panel starts normally with no errors. | Manual | Done | User approved 2026-10-30. |

### P2 — Create and see goals

**Goal:** `:goal:create <name>` makes an empty, expanded goal in the highlighted project that renders, toggles, persists, and lists.

**Why this slice:** The thinnest end-to-end goal: store → command → row → persisted state, before any session belongs to one.

**Work (vertical):**
- Backend: `goals.json` store per A10 with atomic read-modify-write and mtime reload (A5), corrupt-file guard (A6); goal identity in `row_key` / cursor follow (A8).
- Frontend: `:` command line from a generalised `prompt_filter` (A9, D5); `:goal:create` with A1–A3 messages (D6); `goal` row kind in `rows_for` / `build_lines` with `▾/▸`, name, `(0)`, creation age, interleaved by recency (D15, D22); stable sarcastic hint line, dim and unselectable (D22, D23, F3); Enter/`h`/`l` toggle with persisted collapse, new goals expanded (D12, D21); `: cmd` in the default footer; `/` matches goal names (D25).
- `--list`: goals as nested headings (D24).
- Tests/config: store, corrupt-file, and `rows_for` goal-row tests; CONCEPTS.md defines goal and command line.

**Done when:** A goal created with `:goal:create` appears at the top of its project with a hint, collapses and expands, survives a restart in the same state, and shows in `--list`.

#### QA — P2 (Manual)

| ID | Check | Mode | Status | Notes |
|----|-------|------|--------|-------|
| P2-Q1 | Highlight any row in a project, type `:goal:create Auth rework`, Enter: a row `▾ Auth rework  (0)` appears near the top of that project with the cursor on it, and one dim sarcastic line under it mentions `n`. | Manual | Done | User approved 2026-10-30. |
| P2-Q2 | Wait ~10s: the hint text does not change. Create a second goal: it may show a different hint. | Manual | Pending | Failed 2026-10-30: every goal created in one panel session shows the same hint; new panel sessions show a different one. Rework. |
| P2-Q3 | Press Enter, then `l`, then `h` on the goal: it collapses, expands, collapses. End the session with `tmux kill-session -t sassy-code` and run `sassy-code` again: it is still collapsed. | Manual | Done | User confirmed the toggle and persistence work. The q issue was resolved by D30. |
| P2-Q4 | Try `:goal:create` with no name, `:goal:create auth REWORK` in the same project, and `:nonsense`: each shows a footer message and changes nothing. | Manual | Done | User approved 2026-10-30. |
| P2-Q5 | `/auth` filters to the goal; `/` with an empty value restores the full list. | Manual | Done | User approved 2026-10-30. |
| P2-Q6 | `./panel.py --list` shows the goal nested under its project. | Manual | Done | User approved 2026-10-30. |
| P2-Q7 | Put `{` into `~/.sassywares/sassy-code/goals.json` and start: the panel runs, warns in the footer, shows no goals, and the file is unchanged afterwards. | Manual | Done | User approved 2026-10-30. |
| P2-Q8 | Create three goals in one project and leave them empty: each shows a different hint. Restart the panel: each keeps its hint. | Manual | Pending | |
| P2-Q9 | Each goal arrow lines up with the harness glyph on loose session rows, goal names line up with loose session titles, and sessions nested in a goal keep their indentation. | Manual | Pending | |
| P2-Q10 | From another tmux session, run `sassy-code`: you switch to the dedicated `sassy-code` session and your window is not split. Run it again from inside that session: no second panel appears. | Manual | Pending | |
| P2-Q11 | Press `q` with a live agent in the viewer: you are detached from `sassy-code`, and `tmux ls` still shows the session. Run `sassy-code` again: you reattach to the same panel and viewer, the agent still running, with no second panel. | Manual | Pending | |

### P3 — Sessions in goals

**Goal:** `n` / `N` on a goal row or a session inside a goal starts a session that is filed into and shown inside that goal, for Pi and Claude Code.

**Why this slice:** The core value: phases started from a goal land in it.

**Work (vertical):**
- Backend: `new_session` records the panel-generated id (`panel.py:1363`) in the goal before spawn (D18); missing-file ids are hidden, never pruned (A4); `n` on a done goal reopens it (D19); harness inference on goal rows: goal's newest → project's newest → env → installed (D26 inference, no picker yet).
- Frontend: sessions indented under their goal, newest first (D16); goal row count and newest-session age (D15); goal-row footer with `⏎ toggle  n new` (D11, rest added in P6).
- Tests/config: `rows_for` nesting and ordering tests; tmux key regression covering `:goal:create` → `n` in goal → session row inside the goal; spawn tests for both harnesses.

**Done when:** Sessions started with `n` or `N` from a goal (or from a session inside it) appear nested in that goal, for both harnesses, and those started elsewhere stay loose.

#### QA — P3 (Manual)

| ID | Check | Mode | Status | Notes |
|----|-------|------|--------|-------|
| P3-Q1 | Highlight an empty goal, press `n`: a session starts in the viewer, and within a refresh or after the first message it appears indented under the goal; the hint disappears and the count becomes `(1)`. | Manual | Done | User approved 2026-10-30. |
| P3-Q2 | Repeat P3-Q1 in a project whose newest session is Claude Code and in one whose newest is Pi: the goal's first session uses that harness (`✳` / `π`). | Manual | Done | User approved 2026-10-30. |
| P3-Q3 | Highlight a session inside the goal, focus the viewer, press `prefix + N`: a new session joins the same goal and focus stays in the viewer. | Manual | Done | User approved 2026-10-30. |
| P3-Q4 | Highlight a loose session or the project heading, press `n`: the new session is loose. | Manual | Done | User approved 2026-10-30. |
| P3-Q5 | With two sessions in a goal, the most recently active is on top inside the goal, and the goal sits among loose sessions by its newest activity. | Manual | Done | User approved 2026-10-30. |
| P3-Q6 | End the session with `tmux kill-session -t sassy-code` and run `sassy-code` again: goal membership is unchanged. | Manual | Done | User approved 2026-10-30. |

### P4 — Successors and resume keep membership

**Goal:** Pi in-process successors join their parent's goal, and resuming a goal session in either harness keeps it in the goal.

**Why this slice:** Phase work hands off between sessions; without this a goal silently leaks its next phase.

**Work (vertical):**
- Backend: at load time, a Pi session whose `parentSession` resolves to a goal member joins that goal, following sibling successors (D17; reuse `session_children()` / `switched_session()` lineage); verify F2 for `pi --session` and `claude --resume`, and stop for the user if an id changes.
- Docs: README known limit for `/new` and `/clear` (D28).
- Tests/config: inheritance tests including the sibling case (mirroring `tests/test_background.py::test_second_handoff_from_a_child_follows_the_newest_sibling`).

**Done when:** `/handoff`, `/fork`, `/clone` from a goal Pi session land in the goal, and reopening goal sessions of either harness keeps them there.

#### QA — P4 (Manual)

| ID | Check | Mode | Status | Notes |
|----|-------|------|--------|-------|
| P4-Q1 | In a Pi session inside a goal, run `/handoff`: the successor row appears inside the same goal and carries the running marker. | Manual | Done | User approved 2026-10-30. |
| P4-Q2 | From that successor run `/handoff` again: the new session also appears inside the goal. | Manual | Done | User approved 2026-10-30. |
| P4-Q3 | End the session with `tmux kill-session -t sassy-code`, run `/fork` in a goal Pi session outside it, run `sassy-code` again: the fork appears inside the goal. | Manual | Done | User approved 2026-10-30. |
| P4-Q4 | Present a Claude Code goal session after it was reaped (resume path): it stays in the goal. Same for a Pi goal session. | Manual | Done | User approved 2026-10-30. |
| P4-Q5 | In a goal session, run Pi `/new` (or Claude `/clear`): the fresh session appears loose, as documented. | Manual | Done | User approved 2026-10-30. |

### P5 — Dense projects

**Goal:** Limits, running visibility, and filtering stay correct when projects contain goals.

**Why this slice:** Goals add a second level to the existing `SESSION_LIMIT` rules; isolating it keeps the regression surface testable.

**Work (vertical):**
- Frontend: project limit of 5 top-level items with goals counting as one; per-goal limit of 3 with its own `… N more` row; running sessions pulled in past both; `●` on a collapsed goal holding a running session; filter expands goals and ignores limits (D20, D25).
- Tests/config: `rows_for` tests for both limits, running pull-through, collapsed rollup, and filter expansion; AGENTS.md "Dense projects" rule updated.

**Done when:** A busy project shows at most 5 top-level items and 3 sessions per expanded goal, never hides a running session, and the filter reveals everything that matches.

#### QA — P5 (Manual)

| ID | Check | Mode | Status | Notes |
|----|-------|------|--------|-------|
| P5-Q1 | In a goal with 5+ sessions, only the 3 newest show plus `… N more`; Enter on it shows all and becomes `… show fewer`. | Manual | Done | User approved 2026-10-30. |
| P5-Q2 | In a project with 6+ top-level items (goals + loose), only 5 show plus the project's `more` row. | Manual | Done | User approved 2026-10-30. |
| P5-Q3 | Start a turn in an old goal session beyond the 3 shown: it appears in the goal while running. Collapse the goal: its row shows `●`. | Manual | Done | User approved 2026-10-30. |
| P5-Q4 | Filter for a session title hidden behind a goal's `more` row: it shows, with its goal expanded. | Manual | Done | User approved 2026-10-30. |

### P6 — Goal lifecycle

**Goal:** Rename, done, and delete work from the goal-row footer.

**Why this slice:** Completes the goal journey once goals hold real sessions.

**Work (vertical):**
- Frontend: full goal-row footer `⏎ toggle  n new  R rename  x done  D delete` (D11); `R` opens `:` pre-filled `goal:rename <old>` and `:goal:rename` applies to the highlighted goal (D10, A1, A2); `x` toggles done with check, collapse, sink below active goals (D14); `D` with footer `y/n` confirm, flattening sessions to loose (D8, D13); `--list` shows done checks (D24).
- Tests/config: rename/done/delete store and `rows_for` tests; key regression for `x` and `D` → `y`; README goal section and footer keys.

**Done when:** A goal can be renamed, marked done and reopened, and deleted with confirmation, with its sessions intact afterwards.

#### QA — P6 (Manual)

| ID | Check | Mode | Status | Notes |
|----|-------|------|--------|-------|
| P6-Q1 | Highlight a goal: the footer shows `⏎ toggle  n new  R rename  x done  D delete`. Highlight a session: the normal footer returns. | Manual | Done | User approved 2026-10-30. |
| P6-Q2 | Press `R`: the bottom line shows `goal:rename <old name>`; edit it and Enter: the goal is renamed and the cursor stays on it. | Manual | Done | User approved 2026-10-30. |
| P6-Q3 | Press `x`: the goal gets `✓`, collapses, and moves below active goals; `x` again reopens and expands it, in recency order. | Manual | Pending | Failed 2026-10-30: the goal should expand when reopened with x. Rework. |
| P6-Q4 | On a done goal press `n`: the new session lands in it and the goal is no longer done. | Manual | Done | User approved 2026-10-30. |
| P6-Q5 | Press `D`, then any key other than `y`: nothing changes. Press `D` then `y`: the goal is gone and its sessions appear loose in the project, still openable. | Manual | Done | User approved 2026-10-30. |
| P6-Q6 | Highlight a project heading: the footer shows `⏎ toggle`. Highlight a goal: the footer shows only `⏎ toggle  n new  R rename  x done  D delete`. | Manual | Pending | |

### P7 — Harness picker

**Goal:** `n` on a project heading or goal row asks which harness to use; everything else uses the inferred harness.

**Why this slice:** Separate from goals proper, and touches the shared `n` path and the `N` binding.

**Work (vertical):**
- Frontend: boxed `curses.newwin` picker built from `HARNESS`, preselected with the inferred harness, `j`/`k`/arrows/`1`–`9`/Enter, Esc/`q` cancels, skipped with one installed harness (D26).
- Backend: `--new-session` sends the internal skip-picker key so `N` never opens it; `n` on session rows unchanged (D27, F3); `docs/solutions/tmux/new-session-shortcut.md` updated.
- Tests/config: tmux key regression for picker pick, cancel, and `N` skipping; README `n` behaviour.

**Done when:** The picker appears only for `n` in the panel on project headings and goal rows, and both harnesses can be chosen from it.

#### QA — P7 (Manual)

| ID | Check | Mode | Status | Notes |
|----|-------|------|--------|-------|
| P7-Q1 | Highlight a project heading, press `n`: a boxed picker lists Pi and Claude Code with glyphs, preselected on the project's newest harness; Enter starts that one. | Manual | Done | User approved 2026-10-30. |
| P7-Q2 | Same on a goal row, choose the other harness with `j` + Enter (or its number): the new session uses it and lands in the goal. | Manual | Done | User approved 2026-10-30. |
| P7-Q3 | Open the picker and press Esc: nothing is created. | Manual | Done | User approved 2026-10-30. |
| P7-Q4 | Highlight a session row and press `n`; then with a goal row highlighted, focus the viewer and press `prefix + N`: neither opens the picker, both start with the inferred harness. | Manual | Done | User approved 2026-10-30. |
| P7-Q5 | With only one harness on `PATH`, `n` on a project heading starts immediately with no picker. | Manual | Done | User approved 2026-10-30. |
