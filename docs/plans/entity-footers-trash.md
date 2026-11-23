# Entity footers, a keys menu, and recoverable deletion

**QA:** manual

## Intent

The footer currently mixes the highlighted row's actions with global hints
(`/`, `:`, `r`, `q`, navigation), so it never says what the selected entity can
actually do. This work makes every footer entity-scoped, moves the global and
otherwise-hidden bindings into a `?` keys menu, and mirrors the whole keymap in
the README from one code table. It also closes the missing session actions:
rename writes the harness's own title, and delete stops a safe session, moves its
transcript to a durable trash with a filtered restore/purge picker, and cascades
from a deleted goal or slice. One keymap table drives the footers, the menu and
the README, so the three cannot drift.

## Decisions

- D1: Footers are entity-scoped; every global hint leaves them. (user-directed)
- D2: Footer actions: Project `⏎ toggle  n new`; Session `⏎ present  R rename  m move  x done  D delete`; Goal/slice `⏎ toggle  n new  R rename  x done  D delete`; more/less `⏎ more`/`⏎ less`. (user-directed)
- D3: A global help menu exists. (user-directed)
- D4: `[`/`]`, `h`/`l`/`←`/`→`, `g`/`G`, `/`, `:`, `r`, `q` and `Ctrl-C` are real bindings with no footer hint today. D27 hides some of them from the display without removing the binding. (from code: `panel/main.py` `dispatch_key()`, `panel/view.py` `FOOTER_ITEMS`; amended by D27)
- D5: Harness titles are Pi `session_info.name` and Claude `custom-title.customTitle`, both already parsed. (from code: `panel/parsers.py` `parse_pi_session()` and the Claude title branch)
- D6: Projects are derived from session `cwd`; there is no stored project record and no project row to act on beyond the heading. (from code: `panel/rows.py` grouping and `panel/core.py` `Group`)
- D7: No project done and no archive state; projects stay auto-discovered. (user-directed, over adding a project store)
- D8: Session rename writes the harness's native title; there is no local title copy. `R` on a session opens the `:` line on `session:rename` with an empty argument, per D26. (user-directed)
- D9: Session delete is soft: the transcript moves to `~/.sassywares/sassy-code/trash/` with a manifest entry; `t` opens the trash, `Enter` restores, `d` purges, `Esc` closes; entries older than 7 days are purged silently at panel startup; delete is refused for busy or external sessions (D11/D18 define busy); the session confirm is `Delete session '<title>'? y/n`; deletion removes it from Pi/Claude too. (user-directed)
- D10: `D` on a goal/slice trashes the whole subtree (explicit and inherited, done or not), removes the container records, and is all-or-nothing; if any member is busy, external or unknown, the whole delete refuses. Restore brings a member back loose with its done mark. (user-directed)
- D11: A turn in progress refuses deletion. A completed, idle live session is stopped with SIGTERM, waited on for about 3 seconds, and the delete aborts if it survives; never SIGKILL. (user-directed)
- D12: The `?` menu is a supplement, not a duplicate: global bindings, tmux bindings and mouse gestures, in Global, Tmux and Mouse sections. Entity actions stay out because their footers show them, and the box keys stay out because the README table documents them (D27). It is scrollable and closed by `?`/`Esc`/`q`, and a long action wraps onto a continuation line instead of clipping. (user-directed; amended by D27)
- D13: `panel/keymap.py` is the single binding table (`scope, keys, action, footer, menu, also, listed`). Footers render `footer` rows, `?` renders `menu` rows, README § Keys mirrors every `listed` row, and `tests/test_keys.py` enforces parity. A hidden alias lives in `also` and a hidden row keeps `menu=False`/`listed=False`, so no binding exists only in the dispatcher. `AGENTS.md` gains the rule and a Docs-upkeep row. (user-directed; amended by D27)
- D14: Exact footers: Project `⏎ toggle  n new`; Session `⏎ present  R rename  m move  x done  D delete` (done: `x undo`); Goal/slice `⏎ toggle  n new  R rename  x done  D delete` (done: `x undo`); `… N more` `⏎ more`; `… show fewer` `⏎ less`; no row `↑/↓ move  ? keys`. (user-directed)
- D15: Rename is allowed during a turn and while live; the append is metadata, not a message. (user-directed)
- D16: Restore puts the transcript at its original path, restores the done mark, and re-files it in its original goal/slice if that container still exists, reopening and expanding it; otherwise it comes back loose. It refuses if the original file is back, selects the row when visible, and never auto-presents. Trash `d` purges after `y/n`; the 7-day startup purge is silent. (user-directed)
- D17: Deleting the presented session leaves the viewer as a shell, clears the pane's `@sassy_session` tag and the recorded `current`, and does not auto-present anything. The cascade prompt reads `Delete goal '<name>' and N sessions? y/n`, with no session clause at zero. (user-directed)
- D18: Busy means the transcript, not the badge: refuse when `pending_turn()` is true, `open_tasks` or `queued_input` is non-empty, the transcript is unreadable, liveness is unknown, or the session runs in a pane the panel does not manage. A done-marked session with a live turn is refused. (user-directed; from code: `panel/status.py` `pending_turn()`, `panel/core.py` `Session.is_complete()`, `panel/panes.py` `pane_is_live()`, `panel/procs.py` snapshot)
- D19: The trash is a plain list of deleted sessions with its own `/` filter over title, project and harness; the main list filter is untouched by a restore. `Enter` restores and closes; `d` purges and keeps the box open; an empty list says so. (user-directed)
- D20: A cascade delete stops the whole subtree before moving any transcript; a stop failure aborts with nothing trashed (already-stopped sessions stay stopped). A later move or record-write failure rolls the moved files back and leaves the records unchanged, so it never trashes a partial subtree. (user-approved in the read-back, from D10)
- D21: `assets/sassy-code.svg` is regenerated from the demo fixture in this work because the footers change; it is never hand-edited, and `tests/shot-gate.sh` plus `ship.sh check` must pass. (from project rules and `tests/shot-gate.sh`)
- D22: Every QA row is Manual: `dogfood.enabled` is not set, so no `Auto` row can exist. (from `.sassywares/config.yaml` and the QA rules in the plan format)
- D23: `n` stays live on a session row (it starts in that session's project/container), but only the project and goal/slice footers show it, per the session footer list; the README documents it. (user-approved in the read-back)
- D24: `x`, `R`, `m` and `D` on a project heading keep refusing with the existing footer messages; the project footer shows only toggle and new. (from code: `panel/actions.py` guards; user-approved in the read-back)
- D25: After a single-session delete the highlight goes to the row below, else the row above, else the project heading. The neighbour is taken from the rows as drawn (filter and tier honored), never auto-presents, and skips tier rows (`more`, `goal_more`, `slice_more`) because a delete lowers the hidden count and can remove that row. (from the review finding on `rows.neighbour_after_delete`; README § Delete and `docs/architecture.md` document it)
- D26: No rename pre-fills a name, so a session, goal or slice all open the same way: `R` pre-fills an empty `session:rename `, `goal:rename ` or `slice:rename ` (the cursor after it) and never carries the current name over, so a long, preview-derived or absent name all behave the same: the user types a fresh one. A blank or whitespace-only argument still refuses in the footer for all three, and the project-row refusal is unchanged. This supersedes D8's pre-fill wording and the earlier goal/slice pre-fill. (user change request)
- D27: The keys *display* is trimmed, never the dispatch: move rows show `↑ ↓` only (`j`/`k` stay in `also`), `[`/`]` hide the `h`/`l`/`←`/`→` aliases (kept in `also`), `g`/`G` stay a working but unlisted row (`menu=False`, `listed=False`), the "In boxes" section leaves the `?` menu (box rows keep working and stay in the README table, `menu=False`), `r`'s action text names Pi-only reload of a session's skills, and a menu action longer than the panel wraps onto a non-selectable continuation line rather than clipping or ellipsizing. README prose names the hidden aliases. Footers are unchanged, so the screenshot is not re-rendered. (user change request)
- D28: The trash picker is a project-grouped list, not a free-form table. Each entry is one display line with three aligned columns — title, harness, and a right-aligned age — sized to the live panel width; the title takes the width the dropped project column freed, truncates with `…`, and shrinks before the other columns, so harness and age stay visible on a narrow panel. Entries are grouped by project label (case-folded, then full cwd as the tie-break), newest first inside a group, and each group starts with a bold, non-selectable project header line, with the shared `PICKER_DIVIDER` rule between groups (exactly one per boundary, never before the first header, none in the empty or empty-filter states) and no blank gap line; neither the rule nor the header is selectable or pickable and the mouse uses the drawn line-to-entry map, with the rule taking its own drawn row like any other non-row line. The box pins its own action line below a separator rule, read from the new `Trash entry` keymap rows, so it always reads `⏎ restore  d purge`; the panel footer is stale behind the modal box and may be covered, so the box owns the visible actions. The main footer strings are unchanged, so the screenshot is not re-rendered. (user change request; implements T1–T4, delegates the pixels under A1)

## Constraints

- Never hide an in-progress session; a cascade holding one refuses rather than hiding or skipping it.
- Never `respawn-pane -k`; the only stop is an explicit delete, and only SIGTERM, never SIGKILL. Visible panes are still never auto-reaped.
- Liveness needs an agent descendant; an empty process snapshot is unknown, not dead, and unknown liveness refuses a delete.
- Stdlib only; the import graph stays one-way; `panel/__init__.py` remains a read-only facade.
- Footer items wrap at the panel width; a session stays one display line with `…` truncation and a right-aligned age.
- Mouse behaviour is unchanged; `Shift`-drag remains the terminal's.
- The keymap table is the source of truth; the footers, `?` menu, README table and test change together. A user-visible change updates README in the same commit.
- Trash is the only copy after a delete; a failed move or record write must not lose a transcript.
- Pi-only actions stay Pi-only (`r`); rename works for both harnesses.
- No project done/archive, no project rename/move/delete. No goal/slice trash or restore: containers are hard-deleted, only sessions are recoverable.
- No configurable retention and no "empty all"; no trash for deletions made outside the panel; no auto-present; no stopping external sessions; no new CLI; no undo stack beyond `t`.

## Deferred

- F1: The trash manifest schema, the exact module split (trash store, stop helper, box) and the retention constant are implementation-time. (implementation-time — P1)
- F2: The exact refusal wording for busy/external/unknown, the trash entry layout and the filter matching details. (implementation-time — P1/P2)
- F3: The release, version bump and tag; the release skill owns them after this work. (user-deferred — release)
- F4: A configurable retention window or an "empty trash" command. (user-deferred — out of scope)
- F5: A dogfood recipe and `Auto` QA rows; dogfood is not enabled. (user-deferred — later)

## Assumptions

- A1: The user delegated the trash box's visual details and the exact refusal copy to the agent; D19 fixes the interaction, not the pixels. (user-delegated)
- A2: The user delegated the keymap row shape and the menu section order to the agent, provided every binding appears once in the README table. (user-delegated)
- A3: The user delegated the plan's phase boundaries; the behaviour above is not split differently. (user-delegated)

## Phase tracking

| Phase | Goal | Status |
|-------|------|--------|
| P1 | A session delete is safe and recoverable from a filtered trash | Done |
| P2 | Deleting a goal or slice cascades through its sessions | Done |
| P3 | `R` renames a session by writing the harness title | Done |
| P4 | One keymap drives the footers, the `?` menu and the README | Done |

## Phases

### P1 — A session delete is safe and recoverable from a filtered trash

**Goal:** Deleting a session stops it only when safe, moves its transcript to a
durable trash, and lets the user filter, restore or purge it.

**Why this slice:** It is the first end-to-end destructive flow: `D` → busy
preflight → optional SIGTERM → manifest and file move → `t` → restore/purge,
with the viewer and docs updated in the same phase.

**Review:** logic

**Work (vertical):**
- **State:** add a trash store under `SASSY_CODE_STATE_DIR/trash/` (`index.json`
  plus `files/`) with atomic writes, a corrupt-file guard that hides entries and
  refuses edits, and a 7-day startup prune. A manifest entry carries the session
  id, harness, title, original path, trashed path, cwd, goal id, done mark and
  deleted time. Reuse the `Store`/`mutate_store` seam in `panel/state.py`; add
  the helpers to remove/restore the done mark and goal membership. Refuse
  before moving when `goals.json` or `sessions.json` is unreadable, because the
  manifest could not preserve the filing or the done mark.
- **Lifecycle:** add the busy predicate from D18 (reuse `status.pending_turn()`,
  `Session.open_tasks`, `Session.queued_input`, the transcript read and the live
  process/pane snapshot). Add a stop helper that SIGTERMs the agent process in a
  managed pane, polls about 3 seconds and refuses to continue on timeout; never
  the shell or tmux. For a hidden pane, remove the empty pane after exit; for
  the viewer, leave the shell, clear the tag and `current`.
- **Actions/UI:** `D` on a session asks the y/n prompt, runs the preflight and
  moves the transcript; refusals name the reason. `t` opens a plain list through
  the shared picker (`panel/boxes.py`), newest first, `j/k`/arrows, `/` filters
  title/project/harness, `Enter` restores and closes, `d` purges after y/n and
  stays open, `Esc` closes, empty says so. Restore is exact where possible and
  selects the row when the main filter shows it. If a store or `current` write
  fails after the transcript moved, move it back and report.
- **Footer (intermediate):** add `D delete` to the session footer and `t trash`
  to the global footer in the existing tuple style; P4 replaces both with the
  keymap.
- **Docs:** README gains delete/trash/restore/purge and the viewer-shell rule;
  `CONCEPTS.md` gains **Trash** and **Delete**; `docs/architecture.md` gains the
  trash state file, the stop path and the restore flow.
- **Tests:** trash store round-trips, corrupt manifest, 7-day prune, session
  delete/restore/purge, busy and done-marked-running refusals, SIGTERM timeout,
  external/unknown refusal, collision, presented-session shell, picker filter.

**Done when:** A non-busy session can be deleted, found in `t`, filtered,
restored to its original place (or loose when the container is gone) and purged;
a busy or external session refuses before anything moves; no path uses SIGKILL;
README, CONCEPTS and architecture describe the new state.

#### QA — P1 (Manual)

| ID | Check | Mode | Status | Notes |
|----|-------|------|--------|-------|
| P1-Q1 | Delete an idle, non-presented session, confirm in the footer, find it in `t`, press `Enter`, and see it back in its project and goal with its done mark. | Manual | Done | User confirmed at the gate; no failure reported. Setup: a goal containing one idle session. After restore, `t` no longer lists it. |
| P1-Q2 | Delete the session shown in the viewer; the viewer returns to a shell, the panel does not present anything else, and the session is in `t`. | Manual | Done | User confirmed at the gate; no failure reported. Setup: present a session, then `D` it. Restore afterwards. |
| P1-Q3 | Start a long turn, press `D`, and see a refusal with nothing in `t`; wait for the turn to finish and delete again. | Manual | Done | User confirmed at the gate; no failure reported. The second delete should SIGTERM the idle agent and the row should disappear. |
| P1-Q4 | Mark a running session done with `x`, then press `D`: it still refuses. | Manual | Done | User confirmed at the gate; no failure reported. Proves the badge does not hide a live turn. |
| P1-Q5 | In `t`, confirm each project group starts with a project header separated from the group above by exactly one divider rule that cannot be selected or clicked (no rule before the first header and no blank gap line), groups are newest first inside, that title, harness and age line up in columns without repeating the project per row, and that a long title is cut with `…` without hiding the harness or age. Confirm a click on a header or a rule selects nothing and `j`/`k` skip both. Then type a filter that matches the title, then the project, then the harness; purge one entry with `d` and `y` and confirm it cannot be restored. | Manual | Done | User confirmed at the gate; no failure reported. Setup: at least two deleted sessions from different projects and one long title. Resize the panel narrow (20–24 columns) and confirm the columns and rule still fit, the title gives way first, and the last entry is reachable by keys and by click. |
| P1-Q8 | While `t` is open, confirm a separator rule sits between the list and the box's action line, which says `⏎ restore  d purge`; a click on the rule or the hints does nothing. Press `Enter` to restore and confirm the box closes, then reopen and purge with `d` then `y` to confirm the box stays open. | Manual | Done | User confirmed at the gate; no failure reported. The line comes from `panel/keymap.py`'s `Trash entry` rows, not a hardcoded string, and wraps rather than clipping on a narrow panel. |
| P1-Q6 | Set an entry's deleted timestamp in `trash/index.json` to eight days ago, restart the panel, and confirm the entry and its file are gone. | Manual | Done | User confirmed at the gate; no failure reported. Use a temp `SASSY_CODE_STATE_DIR`; never edit real state by hand. |
| P1-Q7 | Point `SASSY_CODE_STATE_DIR` at a temp dir, delete a session, and confirm the transcript moved under `trash/files/` while the harness store no longer lists it. | Manual | Done | User confirmed at the gate; no failure reported. Restore it before switching back. |

### P2 — Deleting a goal or slice cascades through its sessions

**Goal:** `D` on a goal or slice checks the whole subtree, stops and trashes all
its sessions, removes the containers, and restores members loose.

**Why this slice:** It is the second layer of the same delete flow, reusing P1's
trash and preflight; the only new work is the subtree transaction and its
rollback.

**Review:** logic

**Work (vertical):**
- **Goals:** use `goals.goal_subtree_members()` to collect explicit and
  inherited sessions (done included). Refuse before prompting when
  `goals.json` or `sessions.json` is unreadable, then preflight every member
  with P1's busy predicate; one busy, external or unknown member refuses the
  whole delete with a message naming it.
- **Transaction:** prompt `Delete goal '<name>' and N sessions? y/n` (or
  `Delete slice ...`; no clause at zero). Stop all, move all transcripts, then
  remove the goal/slice records. A stop failure aborts before any move (the
  sessions already stopped stay stopped). A move or write failure after some
  files moved rolls those moves back, writes nothing, and reports; never leave a
  partial trash.
- **Selection/restore:** after success select the parent goal or the project
  heading (the members are gone). P1's restore already lands a member loose when
  its goal id is missing; verify that path and keep the done mark.
- **Docs:** README's goal/slice delete paragraph becomes the cascade, including
  the all-or-nothing rule and loose restore; `CONCEPTS.md` corrects "deleting a
  slice flattens its sessions loose" and "deleting a goal removes the slice
  too"; architecture gains the transaction and rollback.
- **Tests:** goal and slice cascade, count in the prompt, zero count, one busy
  member refuses all, external/unknown member refuses all, rollback after a
  failed move/write, restored member loose with done mark.

**Done when:** Deleting a goal with a slice and sessions moves every subtree
session to the trash, removes the containers, restores members loose, and
refuses the whole operation if any member is not safe.

#### QA — P2 (Manual)

| ID | Check | Mode | Status | Notes |
|----|-------|------|--------|-------|
| P2-Q1 | Create a goal with two sessions and a slice with one, press `D`, see `and 3 sessions`, confirm, and find all three in `t` while the containers are gone. | Manual | Done | User confirmed at the gate; no failure reported. Restore one: it should return loose in the project with its done mark. |
| P2-Q2 | Put one subtree session in a long turn, press `D` on the goal: the whole delete refuses and nothing is in `t`. | Manual | Done | User confirmed at the gate; no failure reported. Same for an external session if you have one. |
| P2-Q3 | Delete an empty goal: the prompt has no session clause and the goal disappears. | Manual | Done | User confirmed at the gate; no failure reported. Setup: a goal with no sessions and no slices. |
| P2-Q4 | Make the trash directory read-only, delete a goal with sessions, and confirm it aborts with the sessions, goal and slice still present. | Manual | Done | User confirmed at the gate; no failure reported. Use a temp state dir; restore permissions afterwards. |

### P3 — `R` renames a session by writing the harness title

**Goal:** `R` on a session appends the harness's native title line, and the panel
reads the new title back on the next refresh.

**Why this slice:** It is the remaining session action and is independent of the
trash; it only needs the writer, the command and the hint.

**Review:** logic

**Work (vertical):**
- **Harness data:** extend `core.HARNESS` (or an adjacent title map) with the
  native line type and field for Pi and Claude, so no `if harness ==` branch is
  added. Pi writes `{"type": "session_info", "name": <title>}`; Claude writes
  `{"type": "custom-title", "customTitle": <title>}`.
- **Writer:** append one JSON line with a trailing newline, adding a leading
  newline only if the file does not end with one; refuse a missing or unreadable
  transcript; refuse an empty or whitespace-only title; allow a live or
  in-progress session (D15). The existing parse cache invalidates on size/mtime,
  so the next refresh shows the title.
- **Command/action:** `actions.rename_prefill()` returns `session:rename `,
  `goal:rename ` or `slice:rename ` with an empty name (D26); the `:` parser
  accepts `session:rename <name>` and refuses an empty name for all three.
  `R` on a project or the wrong row refuses as today.
  Add `R rename` to the session footer in the existing style; P4 finalizes it.
- **Docs:** README documents `R` and `:session:rename <name>`, the native-title
  write and the mid-turn allowance; `CONCEPTS.md` adds `session:rename` to the
  command-line list; architecture notes the writer.
- **Tests:** Pi and Claude line shapes, live write, read-back, empty/whitespace
  refusal for session, goal and slice, missing file, `R` prefill blank on a
  session, goal and slice, command parsing.

**Done when:** Renaming a Pi and a Claude session (idle and mid-turn) updates the
row on refresh, leaves a single valid title line, refuses an empty name, and does
not touch any other session state.

#### QA — P3 (Manual)

| ID | Check | Mode | Status | Notes |
|----|-------|------|--------|-------|
| P3-Q1 | Press `R` on an idle Pi session (the `:` line opens on a blank `session:rename ` — the current title is not pre-filled), type a new name, and see the row rename on refresh. | Manual | Done | User confirmed at the gate; no failure reported. Confirm the transcript gained one `session_info` line. Then press `R` on a goal and a slice: both open blank too (`goal:rename ` / `slice:rename `). |
| P3-Q2 | Rename a Claude session the same way and see the row update; confirm one `custom-title` line. | Manual | Done | User confirmed at the gate; no failure reported. Setup: a Claude session in the list. |
| P3-Q3 | Start a turn, press `R` during it, and see the new title appear without stopping the turn. | Manual | Done | User confirmed at the gate; no failure reported. The agent should finish normally. |
| P3-Q4 | Submit `:session:rename`, `:goal:rename` and `:slice:rename` with an empty name and with only spaces: all refuse with a footer message and nothing changes. | Manual | Done | User confirmed at the gate; no failure reported. Also try `R` on a project row; it refuses. An interrupted (`Esc`) rename writes nothing. |

### P4 — One keymap drives the footers, the `?` menu and the README

**Goal:** Replace the ad-hoc footer tuples with `panel/keymap.py`, add the `?`
supplement, document every binding in the README, and refresh the screenshot.

**Why this slice:** The actions now exist; this is the surface that makes them
discoverable and keeps the menu, footers and README from drifting.

**Review:** logic

**Work (vertical):**
- **Keymap:** add `panel/keymap.py` with rows `scope, keys, action, footer,
  menu, also, listed` covering every binding from D4 and the boxes/tmux/mouse
  rows, with `also` keeping a display-hidden alias (D27). Rewrite
  `view.footer_items()` to select the highlighted row's `footer` rows, including
  `x undo`, `⏎ more`/`⏎ less`, and the no-row `↑/↓ move  ? keys`; remove the old
  `FOOTER_ITEMS`, `GOAL_FOOTER_ITEMS` and `SESSION_FOOTER_ITEMS`.
- **Menu:** add `panel/help.py` with the `?` box, drawn through the shared
  picker frame: scrollable with `j/k`, arrows and `g/G`, sections for
  Global, Tmux and Mouse, closed by `?`/`Esc`/`q`. It renders only `menu` rows,
  so entity actions stay in their footers and box keys stay in the README
  table (D27). A long action wraps onto an indented continuation line that no
  key or click can select, so the menu never clips at a narrow width.
- **README:** add a `## Keys` table with one row per `listed` keymap row,
  grouped by scope, including the menu, trash, box, tmux and mouse rows, plus a
  prose line naming the display-hidden aliases; remove the stale prose
  duplicates and update the TOC. The table is the checked mirror, not a second
  source.
- **Docs/rules:** `AGENTS.md` gains the must-not-break rule ("a binding is a
  keymap row; change the table, the renderers and the README table in one
  commit") and a Docs-upkeep row for keybindings/mouse/menu. `CONCEPTS.md` adds
  **Keys menu** and updates **Footer**; `docs/architecture.md` adds the keymap
  and help modules to the module map/import graph.
- **Tests:** add `tests/test_keys.py` parsing the README table and comparing it
  to `keymap` (scope, keys, action), plus a dispatch-coverage assertion and
  footer/menu snapshots. Update `tests/test_goals.py::test_footer_by_row_kind`
  and any footer assertions in `test_sessions`, `test_selection` and the tmux
  key regression.
- **Screenshot:** run `.agents/skills/sassy-code-shot/scripts/ship.sh render`,
  commit `assets/sassy-code.svg` with the change, and verify
  `ship.sh check`/`tests/shot-gate.sh`. Never edit the SVG by hand.

**Done when:** The project, session, goal/slice, more/less and no-row footers are
exactly the D14 strings with no globals; `?` opens the supplement and lists the
global/tmux/mouse bindings, wrapping long actions and omitting the box keys and
`g`/`G` (which keep working); the README table matches the keymap's
`listed` rows and the test is green; AGENTS, CONCEPTS and architecture are
updated; and the screenshot check passes without a re-render.

#### QA — P4 (Manual)

| ID | Check | Mode | Status | Notes |
|----|-------|------|--------|-------|
| P4-Q1 | Highlight a project, a session, a done session, a goal, a done goal, a more row and a show-fewer row; each footer matches D14, carries no `/`, `:`, `r` or `q`, and is unchanged by the trimmed display. | Manual | Done | User confirmed at the gate; no failure reported. Use a demo store if the real list lacks a done row. |
| P4-Q2 | Press `?`, scroll to the bottom and back, and close with `?`, `Esc` and `q`; Global, Tmux and Mouse are listed with `[`/`]` and `t`, there is no "In boxes" section and no `g`/`G`, `j`/`k` or box key, and a long action wraps onto a continuation line on a narrow panel. | Manual | Done | User confirmed at the gate; no failure reported. The hidden keys still work (`j`/`k`, `g`/`G`, `h`/`l`, the box keys); the menu must not repeat the entity footers. |
| P4-Q3 | Compare the README § Keys table with the menu and footers; every listed binding appears in the table, the prose names the display-hidden aliases, and `python3 -m unittest tests.test_keys` passes. | Manual | Done | User confirmed at the gate; no failure reported. Change one keymap row temporarily and watch the test fail, then restore it. |
| P4-Q4 | Open an empty list (filter with no matches), confirm the footer reads `↑/↓ move  ? keys`; do the same on a hint line or a project rule. | Manual | Done | User confirmed at the gate; no failure reported. |
| P4-Q5 | Run `bash tests/shot-gate.sh` and `ship.sh check`; both pass, and the shot is not re-rendered because no footer string changed. | Manual | Done | User confirmed at the gate; no failure reported. |
| P4-Q6 | Read `AGENTS.md`, `CONCEPTS.md` and `docs/architecture.md`: the keymap rule, Docs-upkeep row, Keys menu/Footer terms and the new modules are present and consistent. | Manual | Done | User confirmed at the gate; no failure reported. |
