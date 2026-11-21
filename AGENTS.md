# AGENTS.md

Guide for agents and contributors working in `sassy-code`.

## What this is

A two-pane tmux control panel for agent sessions: a narrow left **control
panel** that lists every Pi and Claude Code session grouped by project, and a
wide right **session viewer** that presents whichever session is selected.
Switching sessions swaps the old viewer process into a hidden background pane
instead of killing it.

## Layout

| Path | Role |
|---|---|
| `main` | Bash launcher. Switches to (or creates) the dedicated `sassy-code` session, builds the layout there, passes the viewer pane id to the panel. |
| `panel.py` | The curses control panel. Reads both harness session stores, manages viewer/background panes, renders the list, dispatches actions. |
| `VERSION` | Current release version; see Releasing. |
| `bin/` | `release` (vendored release script) and `release-ignore` (paths that do not count as consumer-facing). |
| `.sassywares/` | Sassy Skills config and README (project-agnostic metadata). |
| `.agents/skills/sassy-code-release/` | Project skill that runs `bin/release`. |
| `tests/` | Isolated tmux tests (`tmux -L`, `-f /dev/null`) and key-binding regressions. |
| `CONCEPTS.md` | Vocabulary used across the repo. Read before changing user-facing terms. |
| `docs/solutions/` | Durable learnings. Read the relevant entry before re-investigating a problem. |

## How to run

Requires `tmux`, `python3`, bash, and at least one of `pi` / `claude`. See
[README § Requirements](README.md#requirements) for install links.

```bash
./main                                      # or the `sassy-code` shell alias
./panel.py --list                            # plain-text dump, no TUI
```

The launcher self-locates via `BASH_SOURCE`, so it works from any path or symlink.

## Architecture in one paragraph

`panel.py` parses Pi's JSONL session files (`~/.pi/agent/sessions/`) and Claude
Code's transcripts (`~/.claude/projects/`), groups them by working directory,
infers one of five statuses and turn completion from each file's main-thread
messages plus a process-table check, and renders the result with `curses`.
`draw()` returns the drawn screen-line → row/item hit map, so a mouse event is
hit-tested against what is actually on screen. Timestamps are UTC
epochs; render them only at draw time. Every `Session` carries a `harness` key
(`pi` or `claude`) that chooses the resume command and the row glyph. Managed
panes are tagged with the tmux pane option `@sassy_session=<id>`. An in-process
switch (`/handoff`, `/fork`, `/clone`) leaves that tag and the process command
line on the old Pi session, so the panel follows the successor's
`parentSession` link, re-tags the pane, and moves the **in progress** marker to
the successor. On Enter (or a double-click) the panel adopts a tagged pane or
starts the session's own harness (`pi --session <id>` or `claude --resume <id>`)
in a hidden single-pane window in the dedicated `sassy-code-bg` tmux session,
then uses `swap-pane -dZ`
to put it in the viewer. The displaced viewer process is swapped into the
background, keeping its scrollback, cwd, and in-flight turn. The viewer slot is
re-derived from the panel's tmux window after every swap because pane ids travel
with processes. There is no harness API dependency and no server; the session
files are the contract. Panel state lives in `~/.sassywares/sassy-code/`: the
presented session (`current`), the goals (`goals.json`) and per-session done
marks (`sessions.json`). Needs-input attention is in memory only and is lost on
restart. The old
`~/.sassy-code-current` is moved there once on start, and the new file wins if
both exist. Parsed files are cached by path, mtime, and size because
Claude transcripts are large and the panel refreshes every two seconds.
The panel also tags its own pane with `@sassy_panel`; the launcher binds a free
prefix key (default `N`) to `panel.py --new-session --pane #{pane_id}`, which
finds that tag in the focused window and sends the panel the internal
`NEW_NO_PICKER_KEY` (`N`: `n` minus the new-session box). That starts a session
in the highlighted project without moving focus, and never duplicates the spawn
path. `n` opens a boxed picker with no section headings (`new_box()`,
`should_open_new_box()`): the contextual `+ New goal`/`+ New slice` item, then
`installed_harnesses()` in `HARNESS` order, preselected on the inferred harness.
A divider rule separates the item from the harnesses; the ASCII `+` and one
space put the marker in the harness index column and the label in the glyph
column, the same columns as a harness row. `j/k`/`↑`/`↓` move across both parts
(`↑` from the first harness returns to the contextual item, `↓` from it lands
on the first harness), `Tab` switches between them, `←`/`→` do nothing,
`1`–`9`/Enter picks, and `Esc`/`q`/Ctrl-C cancels. A harness item
starts immediately; the contextual item runs harness → name → create → start, via
`new_box_flow()`, which reuses `new_box_create()` and `prompt_line()`;
`new_session()` then files the id with `file_session_in_goal()`. Project and goal rows always open the box, slice rows
only with more than one harness, and session rows and `N` skip it. On a goal or
slice row, or a session inside one, the new id is filed into that container
before spawning (a done container reopens; an unreadable or unwritable store
still starts a loose session and warns); project headings and loose sessions
start loose. The inferred harness is the highlighted session's, else the
container's newest subtree member's, then the project's newest session's, then
`SASSY_CODE_NEW_HARNESS`, then whichever harness is installed.

Goals group sessions inside a project, and a goal may hold slices: two levels,
project → goal → slice → sessions. `goals.json` is `{"version": 1,
"projects": {<cwd>: [goal, ...]}, "loose": {<cwd>: [session_id, ...]}}`; each
record has an optional `parent` goal id (missing or unknown means top-level, so
old files load unchanged and a dangling link is never an error), read through a
cache keyed by mtime and size and written atomically (temp file in the state
dir, then rename, after re-reading the file). The optional `loose` map holds
explicit loose membership per project (A3) and may be missing. A missing file
means no goals; a corrupt or wrong-shape file hides goals, refuses edits, and
is never overwritten. `rows_for()` recurses
through the tree: a `goal` row followed by its direct sessions and `slice` rows,
each slice followed by its sessions, each container with its own tiered `more`
row. `goal_roots()`, `goal_children()`, `goal_subtree_goals()`,
`goal_subtree_members()` and `goal_age_ts()` normalize parent links and
are cycle-safe; `goal_members()` stays direct sessions only. A goal counts, ages
and ranks by its whole subtree; a collapsed goal or slice shows the rollup
`✗` > `●` > `◐`, plus `✓` when that node is done, and a collapsed project
heading shows the same worst-descendant mark after its count (an expanded
heading shows none, because its rows carry their own).
Empty goals and slices get a sarcastic `hint` line in `build_lines()`. A `hint`
is a line kind, not a row: it is unselectable. Empty containers in one project
never share a hint: `goal_hint()` takes them oldest first, each starting at
`crc32(id)` and walking to the first free line; a goal holding slices is not
empty. A goal row's arrow sits in the harness-glyph column of a session row, a
slice name starts at column 8, and a session's status mark starts in its
container's name column (D14). Rollup marks follow the count so a name never
shifts. Loose sessions, goals and slices interleave by recency at their level,
sinking done items at every level. `:` opens a vim-style command line
(`prompt_line`) whose commands run in the highlighted row's project;
`:goal:create <name>` makes a top-level goal and `:slice:create <name>` makes a
slice under the highlighted goal (or a slice's parent), then un-collapses the
project, clears the filter, and lands the cursor on it.
Goal- and slice-row keys call testable helpers, not inline code: `R`
(`rename_prefill`) opens `:` pre-filled `goal:rename <name>` or
`slice:rename <name>`, which `run_command` applies to the highlighted node
(empty or case-insensitive duplicate names under the same parent refused); `x`
(`done_goal`) toggles done, where `recency_entries` sinks done items below active
siblings at every level and done marks collapse (reopen expands); `D`
(`delete_prompt`, then `delete_goal_row` only on `y`) removes a slice and
flattens its sessions loose, or removes a goal with all its slices and flattens
every session in the subtree. All three go through `mutate_goals`, so a corrupt
store refuses them. `m` on a session row opens the Move-to box (`move_box()`,
`move_targets()`): `⌂ Top level` (loose) is first, `+ New goal`
(`move_to_new_goal()`) second, then a divider rule, then every goal and slice
as a tree. A marker column holds one glyph: `⌂` on the loose row, `✓` on the
container the session is in now (`move_box_mark()`, never a done mark);
`Top level` and goal names start in the next column,
and a slice's `├`/`└` connector sits in that name column with
the slice name after it; `move_box_line_attr()` dims done containers. `move_session_row()` applies it through
`move_session()`, which re-reads the store, removes the id from every container,
then files it in the target or records explicit loose membership in one atomic
write; a done target reopens and expands (a session already exactly there is a
no-op with no write). Other rows are refused with a footer warning. The
`goals.json` cache carries the explicit loose sets alongside the goal records.

The mouse is the same list, hit-tested: `main_curses()` enables
`curses.ALL_MOUSE_EVENTS` and restores the previous mask on exit, and `draw()`
returns a screen-line → row/item map that `hit_test()` consults (`None` for a
hint, gap, or off-panel line). `mouse_kind()` classifies a wheel notch, button
press, release, or click; a click selects, a second click on the same session
row inside `DOUBLE_CLICK_SECONDS` presents, a heading/goal/slice toggles, a
`more` row steps one tier, and the wheel moves the highlight one row. A press
on a session remembers it, and releasing on a same-project goal or slice calls
`move_session_row()` (the heading makes it loose, anywhere else cancels), with
` moving <title>… ` in the footer while the button is held. Only a left press
starts a drag (an outstanding drag keeps its session), motion and other
buttons are ignored, and the 2 s refresh tick no longer ends a held drag: it
survives until its press is older than `DRAG_EXPIRE_SECONDS`, or a non-mouse
key or either box opens. The age bound is checked on mouse traffic too, so a
stream of ignored motion cannot keep a stale drag alive. One wheel notch moves
exactly one row: `wheel_notch()` debounces a same-direction event inside
`WHEEL_DEBOUNCE_SECONDS`, because one notch can arrive as a press then a click,
or as a prolonged event. `mouse_kind()` reads only the real `BUTTON4_*` and
`BUTTON5_*` masks: wheel-up works on every build, and wheel-down is best-effort
because this platform's system ncurses (macOS, 4-button) drops a wheel-down
notch inside the library before the panel can see it, leaving only
`BUTTON4_PRESSED` and no `BUTTON5_*` constants. A mask wider than
`ALL_MOUSE_EVENTS` cannot recover it, and no modifier bit is ever treated as a
wheel, so `BUTTON_SHIFT`/`BUTTON_ALT`/`BUTTON_CTRL` events are unclassified.
`SASSY_CODE_MOUSE_DEBUG=<path>`
appends one `bstate`/x/y/classification line per `KEY_MOUSE` event for pinning
the real bit; unset, it is inert and never raises. A release on a
non-session row is a click, so a press+release terminal toggles headings and
steps `more` rows too. The `n` and
`m` boxes route mouse events through the same `run_picker()`, where a click on
an item picks and a click outside cancels; an outstanding left press is tracked
wherever it landed so its release decides inside or outside. Mouse support never
gates the keyboard: without mouse events the panel behaves exactly as before.

Session status comes from `session_status()`: the file-only base is `error`
when the last entry failed, else `idle`; `load_groups()` adds live processes,
done marks and attention with precedence
`error > done > in progress > needs input > idle`. `pending_turn()` reads only
`last_role` and `last_stop_reason` (terminal stop reasons stay in
`TERMINAL_STOP_REASONS`), so a live session with an unresolved turn is in
progress and a live session with a terminal turn is needs input; a live session
is never idle. Claude's background bookkeeping (`open_tasks`, `queued_input`)
does not feed status: a background run outliving its `end_turn` leaves the user
free to type, so that row is needs input. Only `is_complete()` (reaping) uses
those fields, so an open run with a live pane is still never reaped.
Claude's unanswered `AskUserQuestion` sets `awaiting_input` in
`parse_claude_session`, which reads as needs input at once.
`sessions.json` is `{"version": 1, "sessions": {<session id>: {"done": true,
"done_at": <epoch>}}}` and follows the same cache, atomic-write and corrupt-file
guard as `goals.json`; activity newer than `done_at` revives without a write.
`x` on a session calls `done_session`/`toggle_session_done`; a done session dims,
joins done goals in one recency-ordered sunk band, and shows `x undo` in the
footer. Session rows add `m move` to the footer. Sessions with no file are
ignored.

Hidden panes are reaped only when their latest assistant message has a terminal
stop reason (`stop`, `error`, `aborted` for Pi; `end_turn`, `stop_sequence`,
`max_tokens`, `refusal` for Claude Code) for the full grace period and the
session has no open background runs (Claude Code: launches and `SendMessage`
resumes announced in tool results, closed by `<task-notification>` matching
`<tool-use-id>` or, without one, an earlier `<task-id>` run — a later
non-interim notification carries the same `<task-id>` — or by a `TaskStop`
result naming the task id, including the "No task found with ID" error form;
interim "waiting on its own background work" notifications close nothing) and no input
enqueued and not yet dequeued, removed, or answered by a main-thread assistant
entry, or when no descendant agent process
is left (a run that only ever got an interim notification stays open until
then). A tagged pane with no agent descendant is a leftover shell, not a live
session; an empty process snapshot is unknown, not dead, and falls back to the
tag-only answer (`pane_is_live()`), so one failed `ps` cannot flip every live
session to idle or reap hidden panes. A hidden pane that completed its turn sets the in-memory attention mark
(`mark_attention`), which keeps its row on needs input after the pane is reaped
until the session is presented or starts a new turn; it never keeps a process
alive. Switching away never force-reaps from a file
read alone: the JSONL can lag the process. Visible panes are never auto-reaped.
Quitting only detaches. `q`, Ctrl-C, and Ctrl-C in the delete prompt all call
`detach_panel()`, which runs `tmux detach-client -s
=<session>` for the session the panel lives in (found from its own pane) and
nothing else: no other session or client is touched, nothing is killed or
moved, and the panel keeps running so a later `sassy-code` reattaches to the
same panel, viewer and agents. No attached client is a no-op. Outside tmux (`$TMUX` unset, e.g. `panel.py
--target ...` run by hand) there is no client, so `q` and Ctrl-C exit the panel
instead. Ctrl-C detaches in the main loop (wherever it lands, not only while
blocked on a key) and the delete prompt; inside the new-session box, the
Move-to box, and the `:` and `/` prompts it cancels instead. If the detach command or a tmux query fails the footer says "Could not
detach" and the panel keeps running. To end the session for good, run `tmux kill-session
-t sassy-code`. The launcher's trailing command kills only the panel pane, never
the window, so a panel crash leaves the viewer. `sassy-code-bg` is left alone. Sessions
already running outside managed panes are refused instead of duplicated.

On a project heading or goal row, `n` opens the new-session box
(`new_box()`, built from `installed_harnesses()` in `HARNESS` order). The box
always opens on those rows, even with one harness; a slice row opens it only
when more than one harness is installed. Session rows,
`goal_more`/`slice_more` rows and the `N` shortcut never open it;
`should_open_new_box()` is the decision. The Goal item is contextual: a new goal
on a project heading or loose session, a new slice on a goal or a session
already inside one, and nothing on a slice row.

## Direction

The Pi half of the panel logic can become a native Pi extension:
`SessionManager.listAll()` lists sessions and `ctx.switchSession(path)` resumes
one in place, at the cost of the side-by-side view. The two-pane layout
fundamentally requires tmux, since a Pi extension lives inside one Pi process's
TUI, and a combined Pi + Claude Code list has no single native home at all. See
[`docs/solutions/pi-extension/`](docs/solutions/pi-extension/).

## Testing

```bash
python3 -m unittest tests.test_background    # isolated tmux lifecycle tests
python3 -m unittest tests.test_goals tests.test_goal_lifecycle tests.test_state  # goals store, rows, state dir
python3 -m unittest tests.test_status  # five statuses, attention, sessions.json
python3 -m unittest tests.test_mouse tests.test_moves  # mouse hit map/dispatch, Move-to box
bash tests/tmux-pane-keys.sh                 # isolated key-binding regression
bash tests/tmux-layout.sh                    # isolated narrow-panel geometry
```

Tmux tests use an isolated server (`tmux -L`, `-f /dev/null`). Tmux mouse
tests are impractical (the plan records that decision), so `tests/test_mouse.py`
covers the hit map and dispatch and the P6 manual QA is the gate.

## Releasing

sassy-code is versioned: `VERSION` at the repo root holds the current version
(`0.1.0` to start). Releases go through sassy-ship (`commit.branch: current`):
after the approved push, the `hooks.push` `after_push` hook in
`.sassywares/config.yaml` runs the `sassy-code-release` project skill, which runs
the vendored `bin/release` script. The script comes from
[sassywares/sassy-release](https://github.com/sassywares/sassy-release); refresh
the vendored copy by comparing its `# sassy-release vN` header with the repo's
latest tag.

- Patch bumps are automatic: when consumer-facing files changed since the
  highest `v*` tag on origin, the script bumps `VERSION`, commits
  `chore: release v<version>`, pushes main, then creates and pushes the
  annotated tag `v<version>`.
- A version already above the highest tag (a hand-written minor or major bump,
  an interrupted run, the first release) is tagged as-is. Minor and major bumps
  are hand-edited in `VERSION`; a hand-written version must be >= the highest
  tag on origin, or the script stops.
- A prerelease (`X.Y.Z-beta.1`) is tagged as-is and never auto-bumped: once it
  is tagged, later consumer-facing changes make the script stop until the next
  version is hand-set.
- Not consumer-facing: the paths in `bin/release-ignore`, which repeats
  the script's defaults (`AGENTS.md`, `docs/`, `tests/`, `.sassywares/`,
  `.github/`, `.agents/`, `.claude/`, `scripts/`, `bin/release`,
  `bin/release-ignore`) plus `.gitignore`. Everything
  else counts, including `CONCEPTS.md` (user-facing vocabulary), `README.md`,
  `main`, `panel.py`, and `assets/` (the README's screenshot, so it stays
  consumer-facing).
- Recovery: run `bin/release` from a clean `main`. It is idempotent: it
  tags what is already bumped and pushes a missing tag.
- If any step fails, report the failure; never continue silently.
- Never release while `README.md` describes old behavior (see Docs upkeep).

## Docs upkeep

Any change to user-visible behavior, install steps, commands or keys,
configuration, environment variables, requirements, or outputs updates
`README.md` in the **same commit**. A stale README is a review finding, and a
release never goes out with the README describing old behavior.

| Change | README section |
|---|---|
| Key or footer binding, prompt, picker, `:` command | Quick start (the keys paragraph) |
| Mouse actions (click, double-click, wheel, drag-to-move, `Shift`-drag) | Quick start (the Mouse section) |
| Env var (`SASSY_CODE_WIDTH`, `SASSY_CODE_NEW_KEY`, `SASSY_CODE_NEW_HARNESS`, session-dir overrides) | Quick start bullets |
| New dependency, supported platform, harness | Requirements |
| Install, launcher, alias, layout behavior | Quick start, Optional: add an alias |
| Goals, list rendering, limits, glyphs | Intro and the keys paragraph in Quick start |
| Switching, swap, reaping, detach, state files | How it works, Background lifecycle |
| Session stores, status rules, parsing | Data source |
| New or moved file | Files |

- Update `CONCEPTS.md` when a user-facing term is added or renamed.
- Update this file when a rule or an architecture fact changes.
- **Version pins.** The README carries no literal version pins, and the release
  script never edits the README, so a pin would go stale on every release.

## Conventions

- **Mouse is a second way in, never a change to keys.** Mouse reporting is
  enabled by `main_curses()` and restored on exit; a terminal without it behaves
  exactly like the keyboard-only panel. Handle events through `hit_test()`,
  `mouse_kind()`, `click_kind()` and the drawn hit map, not by guessing row
  numbers. Keep the keyboard path identical, and leave `Shift`-drag to the
  terminal so it still selects text; do not interpret it in the panel.
- **Stdlib only.** `panel.py` relies solely on the Python standard library.
- **Single line per session.** Titles truncate with `…`; the age is right-aligned.
  Keep the panel narrow and scannable.
- **Harness marks are quiet.** Every session row is `status glyph title age`
  (`π` Pi, `✳` Claude Code). Do not add a second badge or column; the glyph is
  the harness indicator. `HARNESS` is the single source for label and glyph.
- **Harness behaviour is data, not branches.** Resume/new commands, session
  stores, and terminal stop reasons live in the small `HARNESS` / store
  helpers. Add a harness by adding an entry and a parser, not by scattering
  `if harness == ...` through the panel.
- **Pi-only actions stay Pi-only.** `r` (`/reload`) has no Claude Code
  equivalent; it targets managed Pi panes only. In-process session switches are
  a Pi concept; Claude transcripts have no session-level parent link.
- **Goal inheritance is derived.** `attach_goals()` adds a Pi session to the
  goal or slice of its nearest explicitly filed `parentSession` ancestor in the
  same project (any depth, cycle-safe, siblings included; a fork into another cwd or
  a lineage through one stays loose) via `inherit_goal_members()`; nothing is
  written to `goals.json`, explicit membership wins, loose lineages stay loose.
  An explicitly loose id is skipped, and the nearest explicit ancestor (a goal
  or loose) decides what a successor inherits, so a moved-out lineage stays
  loose. `/new` and `/clear` have no parent link and land loose. Membership is
  keyed by session id, which survives resume: `pi --session <id>` keeps the header id
  and `claude --resume <id>` reuses it (the panel never passes `--fork-session`).
- **Dense projects.** `rows_for()` limits at three levels, each keyed by a
  distinct container id: a project starts at `SESSION_LIMIT` top-level items (a
  goal counts as one, slices included) plus the project's `more` row; an
  expanded goal starts at `GOAL_SESSION_LIMIT` children (direct sessions and
  slices counted together) plus its `goal_more` row; an expanded slice starts at
  `SLICE_SESSION_LIMIT` sessions plus its `slice_more` row. Revealing steps a
  container's entry in the in-memory tier map up one level, where level 0 is the
  base and the cap is `base * (2**(level+1) - 1)` (5, 15, 35, 75… at project
  level; 3, 9, 21… at goal and slice level). `[`/`]` own the decision and move
  exactly one level per press: `]` expands a collapsed container (always at the
  base tier, resetting any stored level) and otherwise reveals the next tier,
  advancing past levels whose cap adds no row so the landed level always
  reveals something; `[` hides one tier above the base and otherwise collapses
  the container, and on a base-tier `more` row it falls through to collapsing
  the container it belongs to. A hide lands on the surviving `more` row, else
  on the container row. Both keys are no-ops under a filter, which ignores
  limits, so clearing it restores the stored tier state. A `more` row is
  emitted whenever something is hidden or the tier level is above zero, so it
  never appears at the base tier with nothing to reveal, and carries
  `(hidden, all_shown)` so it reads `… N more` until everything is shown and
  `… show fewer` after that. Activating it (Enter or click) reveals the next
  tier on `… N more` and hides one tier on `… show fewer`; `]` on a fully shown
  tier is a no-op, and `more_click_action()` routes a click through the same
  rule. Never hide an `in progress` session: a goal or
  slice holding one counts as in progress at its parent level, and a collapsed
  project heading or container shows its rollup mark after the count, so the
  name never shifts. A filter ignores all limits
  and expands goals and slices. Group headings use `canonical_group_names()`
  (home is `root`, otherwise the folder name), and one blank `gap` line
  separates projects.
- **Narrow panel, wide viewer.** The panel pane is the small one. If a change
  makes the panel wide, the layout is wrong (see `docs/solutions/tmux/`).
- **Actions are tmux commands from the panel.** The panel never blocks on the
  viewer pane; it swaps panes and focuses the viewer. Never `respawn-pane -k`
  a managed session: that kills in-flight work.
- **Pane tags are identity.** `@sassy_session=<id>` travels with the pane and is
  authoritative after swaps and panel restarts. Re-query the viewer slot after
  every swap; do not cache pane ids. A tag can lag an in-process switch (and a
  handoff from a handoff can make the successor a sibling, not a child), so
  resolve the live session with `pane_switch_target()`, which checks the tag
  *and* the pane's command line; never trust the command line alone. A tag
  alone is not liveness: `pane_is_live()` also requires an agent descendant in
  the pane, so a tagged leftover shell is not a live session. An empty process
  snapshot is unknown, not dead: it falls back to the tag-only answer rather
  than flipping every session to idle.
- **Shortcuts reuse panel actions.** Bindings run
  `panel.py --new-session --pane #{pane_id}`, which finds the `@sassy_panel`
  pane and `send-keys`-es the panel's no-picker `N` key (it skips the
  new-session box); do not reimplement spawn, swap,
  or state handling in a binding, and never overwrite a key the user bound.
- **Background work is lazy.** Only sessions opened through the panel get live
  panes. A working hidden pane survives; a completed hidden pane is reaped after
  the refresh grace. Remove leftover shells with no descendant agent.

## Knowledge store

- Vocabulary and naming: [`CONCEPTS.md`](CONCEPTS.md)
- Durable learnings and past bugs: [`docs/solutions/`](docs/solutions/)
- Toolchain: [`docs/solutions/tmux/background-pane-swap.md`](docs/solutions/tmux/background-pane-swap.md)
- Document new solved problems there rather than in code comments.
