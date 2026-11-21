# panel.py module split and documentation split

**QA:** manual

## Intent

`sassy-code` is one 5,328-line `panel.py` (260 top-level definitions, a 464-line
`_main_curses` full of closures) plus three prose files that re-tell the same
facts — 208 of `AGENTS.md`'s 419 lines are a single "Architecture in one
paragraph", and eleven topics exist in three or four places. Every future change
pays for both. This work splits the panel into a `panel/` package of 14 focused
modules behind a thin facade, then re-cuts `AGENTS.md`, `README.md`,
`CONCEPTS.md` and a new `docs/architecture.md` so each fact has exactly one
home. No behaviour changes: the same tmux layout, keys, glyphs, files on disk
and vocabulary.

## Decisions

- D1: Both halves ship; the module split leads and the documentation work rides
  along. (user-directed, over docs-only: the split is what makes every future
  change expensive.)
- D2: The panel becomes the package `panel/` with `panel/__init__.py` as a plain
  re-export facade. (user-directed, over flat sibling modules at the repo root
  and over no facade at all.) Supersedes the earlier "`panel.py` stays the
  runnable entry": `panel/` and `panel.py` cannot both be the entry, because
  `import panel` resolves to the package while `python3 panel.py` would execute
  the file as `__main__` — two modules with one name.
- D3: Test patch sites are retargeted to the owning module
  (`panel.state.STATE_DIR`, `mock.patch.object(panel.procs, "process_table")`,
  and the stdlib handle-throughs to the real modules: `curses.wrapper`,
  `os.replace`, `shutil.which`, `time.time`). The facade keeps **reads** free
  through plain re-exports. (user-directed, over a delegating module class that
  forwards `setattr` — that hides which module owns a name, which is the thing
  this work removes — and over a hybrid that forwards only the ~12 mutable
  globals.) Evidence: 120 `mock.patch.object(panel, …)` sites, 26 on
  `panel.curses`, 6 each on `panel.os`/`panel.shutil`/`panel.time`, 6
  `mock.patch.dict`, 2 `mock.patch.multiple`, over ~80 distinct names.
- D4: `tests/panel_loader.py` gains `sys.path.insert(0, ROOT)` and purges every
  `panel*` entry from `sys.modules` before importing, preserving today's
  fresh-globals contract (`tests/goal_case.py:31` depends on it). The four
  inlined copies of the loader (`test_claude_sessions.py:13`,
  `test_reload.py:11`, `test_selection.py:11`, `test_sessions.py:15`) collapse
  into it. (user-directed, over resetting each mutable global in fixtures and
  over making the package stateless.)
- D5: 14 modules, flat package, ~600-line ceiling. (user-directed, over 8
  merged modules — `ui` alone would be ~2,000 lines — over 18 finer ones, and
  over namespaced subpackages with longer patch targets.)
- D6: `_main_curses` becomes an `App` class with `setup`, `run`, `dispatch_key`,
  `refresh`, `draw`, `teardown`; the nested `set_tier` becomes a method.
  (user-directed, over an explicit state dict threaded through free functions
  and over leaving it as one function.)
- D7: `main` stays bash and is not merged into Python. (user-directed, over a
  Python launcher: today `main` still builds the layout and shows `panel exited
  with status N` when the panel is broken, and `SASSY_CODE_TMUX_CONFIG_ONLY`
  stays free of an interpreter start.)
- D8: `panel.py` is deleted; its residue becomes `panel/main.py` holding `App`,
  `list_lines` and `main`. There is no `app.py`. (user-directed, over a
  `__main__.py` shim plus `app.py`, and over folding the app into
  `__main__.py` — that would execute the module twice under `-m panel`.)
- D9: `panel/__main__.py` is a 3-line shim (`from panel.main import main;
  raise SystemExit(main())`), so both `python3 -m panel` and
  `python3 -m panel.main` work. `main:6` uses the short form. (user-directed.)
- D10: `panel/__init__.py` must never import `panel.main`, and app-layer names
  are patched at `panel.main.*`. (user-approved; verified: a package
  `__init__` importing its `__main__` module makes `-m pkg` emit
  `RuntimeWarning: 'pkg.__main__' found in sys.modules … prior to execution` and
  runs the file twice, giving two copies of every module-level name and two
  `App` classes.)
- D11: Documentation shape A — `AGENTS.md` ~150 lines of rules and pointers,
  `docs/architecture.md` ~180 lines as the one system picture, `CONCEPTS.md`
  ~120 lines of glossary, `README.md` ~300 user-facing lines. (user-directed,
  over five `docs/architecture/*.md` files and over in-place cuts.)
- D12: One question per document, and a fact lives with the question it
  answers: README = what will I see and press; `docs/architecture.md` = how does
  it work; `CONCEPTS.md` = what do we call it; `docs/solutions/` = why is it
  this way; `AGENTS.md` = what must I not break. Duplication is deleted, not
  moved. (user-approved.) The five non-mechanical calls:
  - Status precedence → architecture owns the precedence rule and the
    `is_complete()` boundary; README keeps the five-row mark table;
    `docs/solutions/tui/status-owns-the-turn.md` keeps the why.
  - Tiers → README keeps the ladder and `[`/`]` behaviour in ~6 lines;
    architecture owns the cap formula, the tier map and the key decision logic;
    AGENTS keeps one rule: never hide an in-progress session.
  - Mouse → README owns the gestures; architecture owns the hit map, debounce
    and the four-button wheel; CONCEPTS drops to two lines; AGENTS keeps *mouse
    never gates the keyboard*.
  - File inventory → AGENTS' Layout table owns it; README's Files section drops
    to five lines naming `main`, `panel/`, `tests/`, `docs/`, `bin/`.
  - Pane tag / liveness → CONCEPTS keeps the tag in two lines; architecture
    owns identity-vs-liveness and the empty-`ps` fallback; README stops
    describing `@sassy_session`; `tagged-pane-is-not-liveness.md` keeps the why.
- D13: AGENTS' Testing block gains the four modules it omits
  (`test_sessions`, `test_claude_sessions`, `test_selection`, `test_reload`), and
  every `panel.py:NNNN` / `main:NNNN` citation in `docs/solutions/` is rewritten
  to symbol names (`panel.tmuxctl.tmux()`, `build_layout`) because 13 of 16
  checked anchors have already drifted 5–42 lines. (user-approved.)
- D14: `SASSY_CODE_TMUX_CONFIG_ONLY` (`main:57`) is documented in README as a
  user environment variable. (user-directed, over declaring it test-only in
  AGENTS and over deleting it: it is shipped behaviour, and deleting it during a
  docs refactor is scope creep.)
- D15: Seven phases, one commit each, one push at the end, so exactly one
  automatic patch bump. Loaders import `panel.main` during P1–P6 while `main.py`
  re-exports each name as it moves out; P6 switches the loaders to the facade.
  (user-directed, over 2 commits, over 1 commit, and over one commit per
  module.)
- D16: Done-when is the six-check set in "Constraints" below.
  (user-approved.)
- D17: `prompt.py` is called `actions.py` and also absorbs `present_session`,
  `new_session` and `detach_panel` (`panel.py:3250-3420`), which the 15-file map
  left homeless. (user-approved.)
- D18: Progress is gated on the whole suite, both tmux scripts and a golden
  `--list` dump at every phase. (from code: `AGENTS.md:256-269`,
  `tests/tmux-layout.sh`, `tests/tmux-pane-keys.sh`.)

## Constraints

**Non-goals.** No behaviour change and no new features. No rename of any
user-facing key, glyph or term — the CONCEPTS vocabulary survives intact. No
test-assertion changes beyond patch targets, loader imports and the
`panel.py` → `-m panel` command form. No rewrite of `docs/solutions/` content
(citations only). No new dependency: the package stays standard-library only
(`AGENTS.md:340`). No changes to `docs/plans/`, `bin/` or `.sassywares/config.yaml`.

**Import direction.** `core` imports nothing internal; `parsers`, `procs`,
`tmuxctl`, `state` import only `core`; `goals`, `status`, `rows`, `panes` add
those; `view`, `mouse`, `boxes`, `actions` may import any of them; `main`
imports everything. No cycles, and nothing imports upward.

**Names that cannot change.** `.sassywares/config.yaml` sets
`artifacts.concepts: CONCEPTS.md`, so the glossary keeps its filename. `main`
keeps its name and its no-argument behaviour. `./main --new-session --pane`
remains the tmux shortcut, because `tests/tmux-pane-keys.sh:67` matches on
`*--new-session*--pane*`.

**Done-when (all phases).**

1. `python3 -m unittest discover -s tests` green — all 12 modules.
2. `bash tests/tmux-layout.sh` and `bash tests/tmux-pane-keys.sh` green,
   including the failing-panel scenario.
3. `python3 -m panel --list` byte-identical to the dump captured in P1.
4. `./main` from outside tmux attaches to a fresh narrow-panel/wide-viewer
   layout; `q` detaches and `sassy-code` reattaches to the same panel.
5. prefix+`N` starts a session in the highlighted project without moving focus.
6. Enter presents a session and switching away leaves it running; the `n` box,
   the `m` box, the `:` prompt, and a mouse click/wheel/drag each still work.

**Risks.** The launcher, the prefix shortcut and the pane swap are the three
seams the split endangers, and they are covered by done-when 4–6. A broken
`panel/main.py` must still leave a built layout — `tests/tmux-layout.sh`
scenario 3 is that check. `curses` handles in tests are the subtle one: today
`mock.patch.object(panel.curses, "wrapper")` patches the `curses` module object
itself, so retargeting to a bare `curses` import is behaviourally identical and
must not become a per-module alias.

### Target layout

| File | ~Lines | Contents |
|---|---|---|
| `main` | 150 | bash launcher, unchanged except `PANEL_CMD` (line 6) |
| `panel/__init__.py` | 45 | facade: plain re-exports; never imports `panel.main` |
| `panel/__main__.py` | 3 | `from panel.main import main; raise SystemExit(main())` |
| `panel/main.py` | 600 | `App`, `list_lines`, `main()`, `__main__` guard |
| `panel/core.py` | 200 | constants, `Session`/`Goal`/`Group`/`Proc`/`PaneInfo`, `iso_to_epoch`, text/width helpers, `rel_time`, `NAME_COL`/`ARROW_COL`, store errors |
| `panel/parsers.py` | 500 | Claude regexes and task-run tracking, `parse_pi_session`, `parse_claude_session`, `parse_session` |
| `panel/procs.py` | 150 | `process_table`, `descendants`, command detection, `running_session_processes` |
| `panel/tmuxctl.py` | 240 | `tmux()`/`tmux_ok`, pane queries, layout, tags, swap, focus, spawn, reload — no policy |
| `panel/panes.py` | 350 | session index/switch, liveness, adopt, refresh, reap |
| `panel/state.py` | 340 | state dir and `current`, `Store` validity, `goals.json`/`sessions.json` I/O, done marks, caches, guards |
| `panel/goals.py` | 600 | attach/inherit, create/rename/done/delete/move/filing, tree/rollup, hints, name checks, `new_session_plan` |
| `panel/status.py` | 260 | `pending_turn`, `session_status`, attention, `load_groups`, `session_files` |
| `panel/rows.py` | 500 | recency, canonical names, tiers, `plan_goal`, `rows_for`, limits |
| `panel/view.py` | 450 | footer, `row_key`, `hit_test`, `build_lines`, `draw`, `session_depths` |
| `panel/mouse.py` | 250 | `mouse_kind`, click, drag, wheel, debug |
| `panel/boxes.py` | 450 | `run_picker`, `divider_text`, new-session box, Move-to box |
| `panel/actions.py` | 450 | `prompt_line`, `run_command`, `present_session`, `new_session`, `detach_panel`, `done_session`, `done_goal`, `delete_goal_row`, `move_session_row`, `move_to_new_goal` |

## Deferred

- F1: `dogfood.enabled` in `.sassywares/config.yaml` — user-deferred; until it is
  true every QA row here is `Manual`.
- F2: Re-splitting `panel/goals.py` (~600) or `panel/boxes.py` (~450) —
  implementation-time: only if one crosses the ceiling once the real code is in
  it.
- F3: The `panel.main.main()` naming (`module.main.module_function`) —
  user-deferred, cosmetic.

## Assumptions

- A1: The migration mechanics in D15 hold — loaders can import `panel.main`
  while `main.py` still re-exports moved names, without the facade being
  involved. (user-delegated by accepting the phase count; if the re-export dance
  proves noisy it becomes implementation-time and `main.py` keeps the
  re-exports until P6 either way.)

## Phase tracking

| Phase | Goal | Status |
|-------|------|--------|
| P1 | `panel/` package exists, `panel.py` gone, everything still runs | Done |
| P2 | Data layer out: `core`, `parsers`, `procs` | Done |
| P3 | Persistence out: `state`, `goals` | Done |
| P4 | tmux and pane lifecycle out: `tmuxctl`, `panes` | Done |
| P5 | List and presentation out: `status`, `rows`, `view`, `mouse` | Done |
| P6 | Interaction and app out: `boxes`, `actions`, `App` class, facade switch | Done |
| P7 | Documentation re-cut: AGENTS, architecture, CONCEPTS, README | Done |

## Phases

### P1 — Scaffolding: the package exists and nothing else changes

**Goal:** `panel.py` becomes `panel/` and every existing invocation and test
still works.

**Why this slice:** It proves the invocation change (`python3 -m panel`), the
facade rule and the loader switch in isolation, before any code moves between
modules.

**Work (vertical):**

- Package: create `panel/` with `__main__.py`
  (`from panel.main import main; raise SystemExit(main())`) and an empty
  `__init__.py` (the facade has nothing to re-export yet — `main.py` holds
  everything, and D10 forbids importing it). `git mv panel.py panel/main.py` and
  keep its `if __name__ == "__main__": main()`.
- Launcher: `main:6` becomes
  `PANEL_CMD="$(printf 'env PYTHONPATH=%q python3 -m panel' "$DIR")"`. Nothing
  else in `main` changes; `main:48` and `main:127` keep reusing `$PANEL_CMD`.
- Tests: `tests/panel_loader.py` gains `sys.path.insert(0, ROOT)`, drops
  `panel*` from `sys.modules`, and returns
  `importlib.import_module("panel.main")`; the four inlined loaders
  (`test_claude_sessions.py:13`, `test_reload.py:11`, `test_selection.py:11`,
  `test_sessions.py:15`) call it instead. `test_background.py`'s six
  `[sys.executable, str(ROOT / "panel.py"), …]` sites become
  `[sys.executable, "-m", "panel", …]` with `env["PYTHONPATH"] = str(ROOT)`.
  `tests/tmux-layout.sh:205`'s fake panel becomes a fake package: create
  `$FAIL_DIR/panel/__init__.py` and `$FAIL_DIR/panel/__main__.py` containing
  `import sys; sys.exit(3)`.
- Baseline: capture `python3 -m panel --list` to a file for P7's diff.

**Done when:** the suite, both tmux scripts and `./main` behave exactly as
before, and `panel.py` no longer exists.

#### QA — P1 (Manual)

| ID | Check | Mode | Status | Notes |
|----|-------|------|--------|-------|
| P1-Q1 | `python3 -m unittest discover -s tests` passes with all 12 modules. | Manual | Done | User confirmed at the gate ("All approved, everything, QA as well"). |
| P1-Q2 | `bash tests/tmux-layout.sh` and `bash tests/tmux-pane-keys.sh` pass, including the failing-panel scenario that shows `panel exited with status 3`. | Manual | Done | User confirmed at the gate ("All approved, everything, QA as well"). |
| P1-Q3 | `python3 -m panel --list` prints the same list as before the move. | Manual | Done | Save the output as the golden file. |
| P1-Q4 | From outside tmux, `./main` attaches to the usual narrow-panel/wide-viewer layout, and `q` then `./main` returns to the same panel. | Manual | Done | User confirmed at the gate ("All approved, everything, QA as well"). |

### P2 — Data layer out

**Goal:** `core`, `parsers` and `procs` hold every constant, dataclass, width
helper, transcript parser and process inspection helper.

**Why this slice:** These three have no dependency on tmux, state or the UI, so
they can leave `main.py` first and prove the facade re-export mechanism on
code nothing else calls into yet.

**Work (vertical):**

- Backend: move `panel.py:33-224` (constants and the five dataclasses),
  `225-347` (time/text/width helpers) and `rel_time` into `panel/core.py`;
  `348-737` (Claude helpers and both parsers) into `panel/parsers.py`;
  `832-971` into `panel/procs.py`. `Status`/`HARNESS`-style constant blocks that
  are read by tests stay in `core`.
- Imports: `main.py` imports them and re-exports each moved name
  (`from panel.core import Session, Goal, Group, Proc, PaneInfo, …`) so
  `panel.main.<name>` keeps resolving for the tests.
- Tests: retarget patch sites whose owner moved —
  `process_table` (`panel.procs`), `running_session_processes`
  (`panel.procs`), `PI_SESSIONS_DIR`/`CLAUDE_SESSIONS_DIR` reads (`panel.core`).

**Done when:** those three modules hold the code, `main.py` re-exports it, and
nothing else has moved.

#### QA — P2 (Manual)

| ID | Check | Mode | Status | Notes |
|----|-------|------|--------|-------|
| P2-Q1 | `python3 -m unittest discover -s tests` passes. | Manual | Done | User confirmed at the gate ("All approved, everything, QA as well"). |
| P2-Q2 | `bash tests/tmux-layout.sh` and `bash tests/tmux-pane-keys.sh` pass. | Manual | Done | User confirmed at the gate ("All approved, everything, QA as well"). |
| P2-Q3 | `python3 -m panel --list` matches the P1 golden dump. | Manual | Done | User confirmed at the gate ("All approved, everything, QA as well"). |

### P3 — Persistence out

**Goal:** `state` and `goals` own the state directory, both JSON stores, their
validation, caches and every goal mutation.

**Why this slice:** The store is the biggest test surface (goal and state tests
patch `STATE_DIR`, `_goals_cache`, `_sessions_cache`), so isolating it proves the
patch-retarget rule on the heaviest consumer.

**Work (vertical):**

- Backend: move `1552-1588` (state path, migrate, read/write `current`),
  `1589-1889` (errors, `Store`, validity, both store files, done marks, guards,
  caches) into `panel/state.py`; `1890-2452` (goal records, load, inherit,
  mutations, tree, rollup, `new_session_plan`, `new_box_goal_action`,
  `new_box_sections`, `should_open_new_box`) into `panel/goals.py`.
  `panel/core.py` keeps the constants the tests read.
- Imports: re-export from `main.py` as in P2.
- Tests: retarget `STATE_DIR`, `_goals_cache`, `_sessions_cache`,
  `_read_state`, `_write_state`, `migrate_state`, `load_goals`, `mutate_goals`
  and the store guards to `panel.state` / `panel.goals`; `panel.os.*` and
  `panel.shutil.*` become `os.*` and `shutil.*`.

**Done when:** the store and goal code live in their modules, with the same
files on disk and the same corrupt-file behaviour.

#### QA — P3 (Manual)

| ID | Check | Mode | Status | Notes |
|----|-------|------|--------|-------|
| P3-Q1 | `python3 -m unittest discover -s tests` passes, including the goal, move and state modules. | Manual | Done | User confirmed at the gate ("All approved, everything, QA as well"). |
| P3-Q2 | `python3 -m panel --list` matches the P1 golden dump. | Manual | Done | User confirmed at the gate ("All approved, everything, QA as well"). |
| P3-Q3 | With a temporary `goals.json`, `:goal:create Demo` creates a goal and `x` marks it done, then `x` again reopens it. | Manual | Done | Uses the real panel against a scratch state dir. |

### P4 — tmux and pane lifecycle out

**Goal:** `tmuxctl` is pure tmux plumbing and `panes` owns pane identity,
liveness, adoption and reaping.

**Why this slice:** This is the seam the panel's correctness rests on, and it is
where the "tag is identity, not liveness" rule lives; splitting it before the UI
means a broken import shows up as a refused session, not a blank screen.

**Work (vertical):**

- Backend: move `972-1200` into `panel/tmuxctl.py` (including `_spawn_bg_window`
  and `_bg_window_name`); `1242-1551` into `panel/panes.py`, plus the harness
  data from `1201-1241` that spawns and resumes sessions
  (`installed_harnesses`, `default_harness`, `session_invoke`,
  `spawn_session_pane`, `spawn_new_session_pane`) — `installed_harnesses` is
  read by tests, so it stays in whichever of the two owns spawning.
- Imports: re-export from `main.py`.
- Tests: retarget `tmux`, `panel_pane_id`, `managed_panes`, `set_panel_tag`,
  `set_pane_tag`, `kill_pane`, `focus`, `pane_has_agent`, `visible_layout`,
  `installed_harnesses`, `default_harness`, `running_session_processes`;
  `panel.curses.*` patches become `curses.*`.

**Done when:** the tmux calls are in one module with no policy, pane lifecycle
in another, and no test reaches into `main.py` for either.

#### QA — P4 (Manual)

| ID | Check | Mode | Status | Notes |
|----|-------|------|--------|-------|
| P4-Q1 | `python3 -m unittest discover -s tests` passes, including `test_background` and `test_reload`. | Manual | Done | User confirmed at the gate ("All approved, everything, QA as well"). |
| P4-Q2 | `bash tests/tmux-layout.sh` and `bash tests/tmux-pane-keys.sh` pass. | Manual | Done | User confirmed at the gate ("All approved, everything, QA as well"). |
| P4-Q3 | Enter a session, switch to another, switch back: the first keeps running in a hidden pane and comes back with its scrollback. | Manual | Done | User confirmed at the gate ("All approved, everything, QA as well"). |

### P5 — List and presentation out

**Goal:** `status`, `rows`, `view` and `mouse` build the list, decide each
status, and draw and hit-test the panel.

**Why this slice:** With these out, `main.py` is left holding only the boxes,
the actions and the curses loop, so the `App` conversion in P6 happens against a
small file.

**Work (vertical):**

- Backend: `pending_turn`, `session_status`, attention, `load_groups` and
  `session_files` (`738-831`, `3451-3507`, `3417-3450`) into
  `panel/status.py`; recency, canonical names, tiers, `plan_goal`, `rows_for`
  and the limit/page helpers (`3162-3249`, `3508-3928`) into `panel/rows.py`;
  footer, `row_key`, `hit_test`, `build_lines`, `draw` and `session_depths`
  (`3929-3984`, `4217-4533`) into `panel/view.py`; `3985-4216` into
  `panel/mouse.py`.
- Imports: re-export from `main.py`.
- Tests: retarget `load_groups`, `session_files`, `draw`, `_ATTENTION`,
  `MOUSE_WHEEL_UP`/`MOUSE_WHEEL_DOWN` (`panel.mouse`), `FOOTER_ITEMS`,
  `SESSION_FOOTER_ITEMS`, `NAME_COL`, `STATUS`, `HARNESS` and the other constant
  reads to `panel.core`/`panel.rows`/`panel.view` as their new owners.
  `panel.time.*` becomes `time.*`.

**Done when:** the list, the statuses and the drawing come from four modules and
the screen is unchanged.

#### QA — P5 (Manual)

| ID | Check | Mode | Status | Notes |
|----|-------|------|--------|-------|
| P5-Q1 | `python3 -m unittest discover -s tests` passes, including the mouse and selection modules. | Manual | Done | User confirmed at the gate ("All approved, everything, QA as well"). |
| P5-Q2 | `python3 -m panel --list` matches the P1 golden dump. | Manual | Done | User confirmed at the gate ("All approved, everything, QA as well"). |
| P5-Q3 | Rows still read `●`/`◐`/`·`/`✗`/`✓`, goals and slices indent as before, and `]`/`[` step exactly one tier. | Manual | Done | User confirmed at the gate ("All approved, everything, QA as well"). |
| P5-Q4 | A click selects, a second click presents, the wheel moves one row per notch, and a heading click toggles. | Manual | Done | User confirmed at the gate ("All approved, everything, QA as well"). |

### P6 — Interaction and app out

**Goal:** `boxes` and `actions` hold the pickers and the row actions,
`_main_curses` becomes the `App` class in `panel/main.py`, and the loaders switch
to the facade.

**Why this slice:** It is the last code phase, so it carries the only test
changes that touch app-layer names — and it is the one place where a
mechanical move becomes a real rewrite (`App`).

**Work (vertical):**

- Backend: `2453-3045` (picker frame, new-session box, Move-to box) into
  `panel/boxes.py`; `3046-3161` and `3250-3420` (`empty_goal_order`,
  `goal_hint`, `slice_parent_for_row`, `run_command`, `_present_pane`,
  `present_session`, `new_session`, `detach_panel`) plus the row actions
  (`apply_edit_key`, `highlighted_row`, `apply_command`, `goals_guard`,
  `_goal_row_refusal`, `row_noun`, `rename_prefill`, `done_goal`, `done_session`,
  `move_session_row`, `move_to_new_goal`, `delete_prompt`, `delete_goal_row`,
  `toggle_goal`, `tail_by_width` — `4594-4778`, `4534-4593`) into
  `panel/actions.py`.
- Frontend: convert `main_curses`/`_main_curses` (`4779-5242`) into
  `class App` in `panel/main.py` with `setup`, `run`, `dispatch_key`, `refresh`,
  `draw`, `teardown`; `set_tier` becomes a method. Keep a module-level
  `_main_curses` name only if the curses wrapper needs it; otherwise retarget
  the single test site.
- Facade: `panel/__init__.py` re-exports every public name from every module
  **except** `panel.main` (`App`, `_main_curses`, `list_lines`, `main`).
- Tests: loaders switch from `import panel.main` to `import panel`;
  `mock.patch.object(panel, "_main_curses")` becomes
  `mock.patch.object(panel.main, "_main_curses")` (or `App.run`), and
  `panel.__file__` reads adjust to the package location.

**Done when:** `main.py` holds only `App`, `list_lines`, `main`, everything else
is imported from its module, and every test imports the facade.

#### QA — P6 (Manual)

| ID | Check | Mode | Status | Notes |
|----|-------|------|--------|-------|
| P6-Q1 | `python3 -m unittest discover -s tests` passes with the loaders on the facade. | Manual | Done | User confirmed at the gate ("All approved, everything, QA as well"). |
| P6-Q2 | `bash tests/tmux-layout.sh` and `bash tests/tmux-pane-keys.sh` pass, including the failing-panel scenario. | Manual | Done | User confirmed at the gate ("All approved, everything, QA as well"). |
| P6-Q3 | `n` on a project heading opens the new-session box; `↑`/`↓`/`Tab` move, Enter starts the harness, `Esc` cancels. | Manual | Done | User confirmed at the gate ("All approved, everything, QA as well"). |
| P6-Q4 | `m` on a session opens the Move-to box; picking `⌂ Top level` and a goal both move it, and dragging a session onto a goal does the same. | Manual | Done | User confirmed at the gate ("All approved, everything, QA as well"). |
| P6-Q5 | `:` opens the command line, `:goal:create Demo` lands the cursor on the new goal, and `Esc` cancels. | Manual | Done | User confirmed at the gate ("All approved, everything, QA as well"). |
| P6-Q6 | `q` detaches without killing anything, and `./main` returns to the same panel and viewer. | Manual | Done | User confirmed at the gate ("All approved, everything, QA as well"). |

### P7 — Documentation re-cut

**Goal:** each fact lives in exactly one document.

**Why this slice:** The docs describe the old single file; re-cutting them after
the module map is real means the architecture text is written once, against the
finished tree.

**Work (vertical):**

- Docs: write `docs/architecture.md` (~180 lines) from `AGENTS.md:39-246`,
  deduplicated against README and `docs/solutions/` per D12; cut `AGENTS.md` to
  ~150 lines of layout table, how to run, import-direction rules, module map (one
  line each), testing (with the four missing modules), releasing, docs upkeep and
  knowledge store; trim `CONCEPTS.md` to ~120 lines; trim `README.md` to ~300
  lines and document `SASSY_CODE_TMUX_CONFIG_ONLY` in its env-var bullets.
- Docs upkeep: update the AGENTS table's file/row names for `panel/`,
  `docs/architecture.md` and the new owners.
- Citations: rewrite `panel.py:NNNN`/`main:NNNN` references in `docs/solutions/`
  to symbol names.
- Verification: `python3 -m panel --list` still matches the P1 golden dump.

**Done when:** no fact appears in two of the four prose files, AGENTS.md is
~150 lines, and every claim in README matches the code.

#### QA — P7 (Manual)

| ID | Check | Mode | Status | Notes |
|----|-------|------|--------|-------|
| P7-Q1 | `AGENTS.md` is ~150 lines and contains no architecture prose beyond pointers; `docs/architecture.md` exists and covers process model, data flow, status precedence, tiers and mouse. | Manual | Done | User confirmed at the gate ("All approved, everything, QA as well"). |
| P7-Q2 | Reading README alone, a new user can install, run, and use every key and mouse action without opening another file — and the `SASSY_CODE_TMUX_CONFIG_ONLY` bullet is there. | Manual | Done | User confirmed at the gate ("All approved, everything, QA as well"). |
| P7-Q3 | No fact appears in two of `README.md`, `AGENTS.md`, `CONCEPTS.md`, `docs/architecture.md`; each `docs/solutions/` citation names a symbol that exists in the new tree. | Manual | Done | User confirmed at the gate ("All approved, everything, QA as well"). |
| P7-Q4 | `python3 -m unittest discover -s tests` passes and `python3 -m panel --list` matches the P1 golden dump. | Manual | Done | User confirmed at the gate ("All approved, everything, QA as well"). |
