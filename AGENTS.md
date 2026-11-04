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
| `tests/` | Isolated tmux tests (`tmux -L`, `-f /dev/null`) and key-binding regressions. |
| `CONCEPTS.md` | Vocabulary used across the repo. Read before changing user-facing terms. |
| `docs/solutions/` | Durable learnings. Read the relevant entry before re-investigating a problem. |

## How to run

Requires `tmux`, `python3`, bash, and at least one of `pi` / `claude`. See
[README § Requirements](README.md#requirements) for install links.

```bash
./main                                      # or the `sassy-code` shell alias
./panel.py --list                            # plain-text dump, no TUI
python3 -m unittest tests.test_background    # isolated tmux lifecycle tests
python3 -m unittest tests.test_goals tests.test_goal_lifecycle tests.test_state  # goals store, rows, state dir
bash tests/tmux-pane-keys.sh                 # isolated key-binding regression
bash tests/tmux-layout.sh                    # isolated narrow-panel geometry
```

The launcher self-locates via `BASH_SOURCE`, so it works from any path or symlink.

## Architecture in one paragraph

`panel.py` parses Pi's JSONL session files (`~/.pi/agent/sessions/`) and Claude
Code's transcripts (`~/.claude/projects/`), groups them by working directory,
infers a status and turn completion from each file's main-thread messages plus a
process-table check, and renders the result with `curses`. Timestamps are UTC
epochs; render them only at draw time. Every `Session` carries a `harness` key
(`pi` or `claude`) that chooses the resume command and the row glyph. Managed
panes are tagged with the tmux pane option `@sassy_session=<id>`. An in-process
switch (`/handoff`, `/fork`, `/clone`) leaves that tag and the process command
line on the old Pi session, so the panel follows the successor's
`parentSession` link, re-tags the pane, and moves the **running** marker to the
successor. On Enter the panel adopts a tagged pane or starts the session's own
harness (`pi --session <id>` or `claude --resume <id>`) in a hidden single-pane
window in the dedicated `sassy-code-bg` tmux session, then uses `swap-pane -dZ`
to put it in the viewer. The displaced viewer process is swapped into the
background, keeping its scrollback, cwd, and in-flight turn. The viewer slot is
re-derived from the panel's tmux window after every swap because pane ids travel
with processes. There is no harness API dependency and no server; the session
files are the contract. Panel state lives in `~/.sassywares/sassy-code/`: the
presented session (`current`) and the goals (`goals.json`). The old
`~/.sassy-code-current` is moved there once on start, and the new file wins if
both exist. Parsed files are cached by path, mtime, and size because
Claude transcripts are large and the panel refreshes every two seconds.
The panel also tags its own pane with `@sassy_panel`; the launcher binds a free
prefix key (default `N`) to `panel.py --new-session --pane #{pane_id}`, which
finds that tag in the focused window and sends the panel the internal
`NEW_NO_PICKER_KEY` (`N`: `n` minus the harness picker). That starts a session in the highlighted project without moving focus, and never
duplicates the spawn path. On a goal row, or a session inside a goal, `n` (and
`N`) files the panel-generated id into that goal before spawning (a done goal
reopens; an unreadable store still starts a loose session and warns); project
headings and loose sessions start loose. The new session uses the harness of
the highlighted session. On a goal row it is the goal's newest member's
harness, then the project's newest session's, then `SASSY_CODE_NEW_HARNESS`,
then whichever harness is installed.

Goals group sessions inside a project. `goals.json` is `{"version": 1,
"projects": {<cwd>: [goal, ...]}}`, read through a cache keyed by mtime and
size and written atomically (temp file in the state dir, then rename, after
re-reading the file). A missing file means no goals; a corrupt or wrong-shape
file hides goals, refuses edits, and is never overwritten. `rows_for()` emits a
`goal` row (selectable; Enter/`h`/`l` persist its collapse state) followed by
its member sessions, and an empty, expanded goal gets a sarcastic `hint` line
in `build_lines()`. A `hint` is a line kind, not a row: it is unselectable.
Empty goals in one project never share a hint: `goal_hint()` takes them oldest
first, each starting at `crc32(id)` and walking to the first free line.
A goal row's arrow sits in the harness-glyph column of a session row.
Loose sessions and goals interleave by recency, and a goal ranks by its newest
member. `:` opens a vim-style command line (`prompt_line`) whose commands run
in the highlighted row's project; `:goal:create <name>` makes a goal, then
un-collapses the project, clears the filter, and lands the cursor on it.
Goal-row keys call testable helpers, not inline code: `R` (`rename_prefill`)
opens `:` pre-filled `goal:rename <name>`, which `run_command` applies to the
highlighted goal (empty or case-insensitive duplicate names refused); `x`
(`done_goal`) toggles done, where `recency_top` sinks done goals below all active
items and done marks collapse (reopen expands); `D` (`delete_prompt`,
then `delete_goal_row` only on `y`) removes the record and flattens its sessions
loose. All three go through `mutate_goals`, so a corrupt store refuses them.

Hidden panes are reaped only when their latest assistant message has a terminal
stop reason (`stop`, `error`, `aborted` for Pi; `end_turn`, `stop_sequence`,
`max_tokens`, `refusal` for Claude Code) for the full grace period and the
session has no open background tasks (Claude Code: announced in tool results,
closed by `<task-notification>` task ids), or when no descendant agent process
is left. Switching away never force-reaps from a file
read alone: the JSONL can lag the process. Visible panes are never auto-reaped.
Quitting only detaches. `q`, Ctrl-C, Ctrl-C in the delete prompt and Ctrl-C in
the harness picker all call `detach_panel()`, which runs `tmux detach-client -s
=<session>` for the session the panel lives in (found from its own pane) and
nothing else: no other session or client is touched, nothing is killed or
moved, and the panel keeps running so a later `sassy-code` reattaches to the
same panel, viewer and agents. No attached client is a no-op. Outside tmux (`$TMUX` unset, e.g. `panel.py
--target ...` run by hand) there is no client, so `q` and Ctrl-C exit the panel
instead. Ctrl-C detaches in the main loop (wherever it lands, not only while
blocked on a key), the delete prompt and the harness picker; inside the `:` and
`/` prompts it cancels the prompt. If the detach command or a tmux query fails the footer says "Could not
detach" and the panel keeps running. To end the session for good, run `tmux kill-session
-t sassy-code`. The launcher's trailing command kills only the panel pane, never
the window, so a panel crash leaves the viewer. `sassy-code-bg` is left alone. Sessions
already running outside managed panes are refused instead of duplicated.

On a project heading or goal row, `n` first opens a boxed harness picker
(`harness_picker()`, built from `installed_harnesses()` in `HARNESS` order,
preselected on the inferred harness, skipped with one harness installed;
`should_pick_harness()` is the decision). Session rows, `goal_more` rows and the
`N` shortcut never open it.

## Conventions

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
  goal of its nearest explicitly filed `parentSession` ancestor in the same
  project (any depth, cycle-safe, siblings included; a fork into another cwd or
  a lineage through one stays loose) via `inherit_goal_members()`; nothing is
  written to `goals.json`, explicit membership wins, loose lineages stay loose.
  `/new` and `/clear` have no parent link and land loose. Membership is keyed
  by session id, which survives resume: `pi --session <id>` keeps the header id
  and `claude --resume <id>` reuses it (the panel never passes `--fork-session`).
- **Dense projects.** `rows_for()` limits at two levels: at most
  `SESSION_LIMIT` top-level items per project (a goal counts as one) plus the
  project's `more` row, and at most `GOAL_SESSION_LIMIT` sessions per expanded
  goal plus that goal's own `goal_more` row (expanded state keyed by goal id).
  Never hide a `running` session: a goal holding one counts as running at
  project level, and a collapsed goal shows `●` after its arrow. A filter
  ignores both limits and expands goals. Group headings use
  `canonical_group_names()` (home is `root`, otherwise the folder name), and one
  blank `gap` line separates projects.
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
  *and* the pane's command line; never trust the command line alone.
- **Shortcuts reuse panel actions.** Bindings run
  `panel.py --new-session --pane #{pane_id}`, which finds the `@sassy_panel`
  pane and `send-keys`-es the panel's no-picker `N` key; do not reimplement spawn, swap,
  or state handling in a binding, and never overwrite a key the user bound.
- **Background work is lazy.** Only sessions opened through the panel get live
  panes. A working hidden pane survives; a completed hidden pane is reaped after
  the refresh grace. Remove leftover shells with no descendant agent.

## Knowledge store

- Vocabulary and naming: [`CONCEPTS.md`](CONCEPTS.md)
- Durable learnings and past bugs: [`docs/solutions/`](docs/solutions/)
- Toolchain: [`docs/solutions/tmux/background-pane-swap.md`](docs/solutions/tmux/background-pane-swap.md)
- Document new solved problems there rather than in code comments.

## Direction

The Pi half of the panel logic can become a native Pi extension:
`SessionManager.listAll()` lists sessions and `ctx.switchSession(path)` resumes
one in place, at the cost of the side-by-side view. The two-pane layout
fundamentally requires tmux, since a Pi extension lives inside one Pi process's
TUI, and a combined Pi + Claude Code list has no single native home at all. See
[`docs/solutions/pi-extension/`](docs/solutions/pi-extension/).
