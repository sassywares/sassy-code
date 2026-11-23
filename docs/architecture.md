# Architecture

How sassy-code works. Vocabulary: [`CONCEPTS.md`](../CONCEPTS.md). Rules:
[`AGENTS.md`](../AGENTS.md). Reasons: [`docs/solutions/`](solutions/).

## Process model

`main` is a bash launcher. In the dedicated `sassy-code` tmux session it creates
the session (or a new window when it has no live panel), splits horizontally
with `-b -l $WIDTH` so the **control panel** is the narrow left pane and the
**session viewer** the wide right one, tags the panel pane
`@sassy_panel=<start time>`, and sends it `PANEL_CMD --target <viewer pane id>`.
Only the panel pane is killed when the panel exits; any non-zero exit except 130
(Ctrl-C, a normal way out) prints `sassy-code: panel exited with status N. Press
Enter to close.` and waits for Enter. `q` only detaches.

`PANEL_CMD` is `cd "$DIR" && env PYTHONPATH="$DIR" python3 -m panel`. The `cd`
is load-bearing: `python3 -m` puts the caller's cwd on `sys.path` ahead of
`PYTHONPATH`, so a project with its own `panel/` package would be imported. The
panel always runs with the repo as its cwd and never reads it; every spawned
session gets an explicit cwd. `ensure_tmux_config` disables the repeatable
default prefix+arrow bindings and binds a free prefix key (default `N`) to
`--new-session --pane #{pane_id}`, never overwriting a user binding. There is
no harness API and no server: one stdlib `curses` process reads session files,
asks tmux for pane state, and runs tmux commands.

## Panes, tags and liveness

A **managed pane** (the viewer or a background pane) carries
`@sassy_session=<session id>`, which travels with the pane through swaps and
panel restarts and is the authoritative identity. The panel finds its own pane
from `$TMUX_PANE` and re-derives the viewer slot from its window after every
swap, because pane ids travel with processes; no pane id is cached.

Identity is not liveness: a tag survives the agent process, so a pane whose `pi`
or `claude` exited is a leftover shell still carrying it. Liveness needs a
descendant agent process (`pane_is_live`, `pane_has_agent`), except that an
empty process snapshot is *unknown*, not dead, and falls back to the tag-only
answer — one failed `ps` must not flip every live session to idle or reap
hidden panes.

An in-process switch (`/handoff`, `/fork`, `/clone`) replaces the Pi session
inside the same process, so the tag and command line keep naming the old
session. The panel follows the successor in the new file's `parentSession`
header, re-tags the pane, and moves the **in progress** marker to it.
`pane_switch_target()` resolves the live session from the tag *and* the command
line, because a handoff from a handoff can make the successor a sibling rather
than a child. Claude transcripts have no session-level parent link, so each
resumed Claude session is its own identity.

`Enter` (or a double-click) presents a session: it adopts an existing tagged
pane (the one in the viewer wins when there are several), or starts
`pi --session <id>` / `claude --resume <id>` in a hidden single-pane window of
`sassy-code-bg`, then swaps it into the viewer with `swap-pane -dZ`. The
displaced process keeps its scrollback, cwd and in-flight turn; nothing is
respawned or killed to switch, and a session already running outside managed
panes is refused rather than duplicated.

## Modules

`panel/__init__.py` re-exports every non-app module for reads; it never imports
`panel.main`, because a package `__init__` that imports its `__main__` module
makes `python3 -m panel` execute it twice. App-layer names live in `main.py`,
reached as `panel.main.<name>`.

| Module | Lines | Contents |
|---|---|---|
| `__init__.py` | 40 | Facade: wildcard re-exports of every non-app module, plus a few leading-underscore names tests read. Reads only. |
| `__main__.py` | 3 | `from panel.main import main; raise SystemExit(main())`. |
| `main.py` | 741 | `App` (`setup`, `run`, `dispatch_key`, `refresh`, `draw`, `teardown`, `set_tier`), `main_curses`/`_main_curses` (the curses wrapper seam and event loop), `list_lines`, `main()` and its argparse. |
| `core.py` | 345 | Constants (stores, state filenames, limits, columns, `STATUS`, `HARNESS`, mouse masks, `MOVE_*`, `REAP_GRACE`, `LEGACY_STATE_FILE`), the five dataclasses, time/text/width helpers, `rel_time`, `_infer_status`, and in-memory attention (`mark_attention`, `clear_attention`, `attention_active`). |
| `keymap.py` | 221 | The one binding table (`Row(scope, keys, action, footer, menu, also, listed)`, `ROWS`), `scope_for_row`, `footer_for_scope`, `footer_items`, `menu_rows`, `listed_rows`. `also` holds a hidden alias and a `menu=False`/`listed=False` row stays wired. |
| `parsers.py` | 488 | Claude transcript helpers and task-run tracking, `parse_pi_session`, `parse_claude_session`, `parse_session`, and `write_title` (the native title append a rename writes). |
| `procs.py` | 170 | `process_table`, `descendants`, command detection, `running_session_processes`. |
| `state.py` | 505 | `STATE_DIR`, the legacy state-file migration, read/write `current`, and the JSON stores (`goals.json`, `sessions.json`, `trash/index.json`) with validation, atomic writes, mtime+size caches, corrupt-file guards and done marks. |
| `goals.py` | 698 | Goal records and loading, `parentSession` inheritance, create/rename/done/delete/move/filing, tree queries and rollup, hints, `new_session_plan`, `new_box_goal_action`, `new_box_sections`, `should_open_new_box`, and delete/restore snapshots of goal membership. |
| `tmuxctl.py` | 230 | Pure tmux plumbing: `tmux()`, pane queries, `layout_panes`, `managed_panes`, `viewer_pane_id`, `panel_pane_for`, tags, swap, focus, `send_reload`, `_spawn_bg_window`. No policy, no liveness decision. |
| `panes.py` | 397 | Harness data and spawn helpers, session index/switch, liveness (`pane_has_agent`, `pane_is_live`, `pane_switch_target`), adoption, `visible_layout`, `refresh_hidden`, `reap_hidden`, `reload_managed_panes`. |
| `status.py` | 177 | `pending_turn`, `session_status`, `session_files`/`read_session` with the parse cache, `load_groups`. |
| `trash.py` | 729 | The delete preflight (`busy_reason`), the `SIGTERM`-only stop, trash load/prune/purge, `delete_session`, the `delete_subtree` cascade transaction, `restore_entry`, pane cleanup and the picker filter. |
| `rows.py` | 496 | Recency, `canonical_group_names`, the tier map and limits, `GoalPlan`/`plan_goal`, `rows_for`, `row_key`. |
| `mouse.py` | 245 | `mouse_kind`, `click_kind`, drag/press/release helpers, `wheel_notch`, `more_click_action`, `cursor_for_key`, `mouse_debug`. |
| `view.py` | 422 | `footer_items` (delegating to `keymap`), `footer_lines`, `hit_test`, `goal_hint`, `session_depths`, `follow_viewer_switch`, `highlighted_row`, `build_lines`, `draw`. |
| `help.py` | 173 | The `?` keys menu: `help_lines`, `help_box_key`, `help_mouse_pick` and `help_box`, drawn through `boxes.run_picker`; long actions wrap onto non-selectable continuation lines. |
| `boxes.py` | 923 | `run_picker` (with an optional pinned footer), the new-session box, the Move-to box and the project-grouped Trash box with its aligned rows and key handlers. |
| `actions.py` | 571 | `present_session`, `new_session`, `detach_panel`, `run_command`, `prompt_line`, and every goal/session row action, including delete/restore/purge. |

Import direction, bottom-up; no cycles, nothing imports upward:
`core` and `keymap` import nothing internal; `parsers`, `procs`, `tmuxctl` and
`state` import only `core`; `goals` adds `state`; `status` adds `panes`,
`tmuxctl`, `parsers`, `procs`, `state` and `goals`; `trash` adds `status`;
`rows` adds `goals`; `view` adds `keymap`; `mouse`, `boxes`, `help` and
`actions` may import any of them (`boxes` and `actions` call `trash`, never the
reverse; `boxes` calls `actions`, never the reverse; `boxes` and `help` call
`keymap` for their rendered rows); `main` may import any module, and
`__main__.py` is the only module that imports `main`.

## Data flow

`status.load_groups()` is the entry point for a refresh:

1. `parsers.parse_session` reads each session file into a `Session` (id, cwd,
   title, harness, timestamps, last role/stop reason, token counts, Claude
   bookkeeping), cached by path, mtime and size because Claude transcripts are
   large and the refresh runs every two seconds. Timestamps are UTC epochs,
   rendered only at draw time. A rename appends one native title line with
   `parsers.write_title`; the line type and field come from
   `core.HARNESS[harness]["title"]`, so the writer has no harness branch. The
   append is metadata, not a message, so a live or mid-turn session may be
   renamed; the size change invalidates the parse cache and the next refresh
   reads the new title. There is no local title copy.
2. `procs.process_table()` and `panes` resolve live panes;
   `status.session_status()` merges the file-derived base with the live process,
   the done mark and attention; `load_groups()` sorts sessions into `Group`s by
   working directory and attaches goal membership.
3. `rows.rows_for()` turns groups and goals into the visible row tree with
   collapse state and tier limits; `view.draw()` paints it and returns a
   screen-line → row/item hit map that `hit_test()` consults, so the mouse is
   tested against what is actually on screen.
4. `main.App` runs the loop: dispatch a key or mouse event, or rebuild and
   redraw on the 2 s refresh tick.

Status precedence is `error > done > in progress > needs input > idle`. `error`
is a failed last entry; `done` is the `x` mark in `sessions.json`; `in progress`
is a live process with an unresolved turn (`pending_turn()` reads only the
turn); `needs input` is a live process whose turn is terminal or has not
started, an unanswered Claude `AskUserQuestion`, or an attention mark on a
finished hidden session; `idle` is neither live nor marked. `is_complete()` is
the other question, for reaping only: false while background work or queued input
is outstanding (`open_tasks`, `queued_input`), not status.

## Tiers

A project, an expanded goal and an expanded slice each page their children with
`SESSION_LIMIT` (5) top-level items per project, `GOAL_SESSION_LIMIT` (3)
children per expanded goal (sessions and slices together) and
`SLICE_SESSION_LIMIT` (3) sessions per expanded slice. The cap at level `L` is
`base * (2**(L+1) - 1)` — 5, 15, 35, 75… at project level, 3, 9, 21… at goal
and slice level. Levels live in an in-memory tier map keyed by container id;
level 0 is the base. `]` expands a collapsed container at the base tier
(resetting any stored level), otherwise reveals one tier; `[` hides one tier,
then collapses at the base tier. Both move exactly one level per press and are
no-ops under a filter, which ignores limits; clearing the filter restores the
stored tiers. `tier_reveal()` advances past levels whose cap adds no row, so a
press always lands on rows that appeared.

A `more` row appears whenever something is hidden or the level is above zero,
and carries `(hidden, all_shown)`: `… N more` until everything is shown, `… show
fewer` after. Activating it reveals one tier on the former and hides one on the
latter (`more_click_action()`). An in-progress session is never hidden: a goal
or slice holding one counts as in progress at its parent level, and a collapsed
container shows its rollup mark (worst descendant `✗` > `●` > `◐`, plus `✓`
when the node is done). A collapsed project shows the same mark after its count.

## Mouse

`main_curses()` enables `curses.ALL_MOUSE_EVENTS` and restores the previous mask
on exit; the keyboard path never depends on it. `draw()` returns the screen-line
→ row/item map that `hit_test()` consults (`None` for a hint, gap or off-panel
line), and `mouse_kind()` classifies an event as a wheel notch, press, release
or click. A second click on the same session row inside `DOUBLE_CLICK_SECONDS`
(0.4 s) presents; a drag left press survives the refresh tick until its press is
older than `DRAG_EXPIRE_SECONDS` (30 s), a non-mouse key, or a box opens.

One notch moves exactly one row: a terminal may report one notch as a press and
a click, or as a prolonged event, so `wheel_notch()` debounces a same-direction
event inside `WHEEL_DEBOUNCE_SECONDS` (0.05 s), while a direction change is
always fresh. `mouse_kind()` reads only the real `BUTTON4_*` and `BUTTON5_*`
masks: wheel-up works everywhere, wheel-down is best-effort because macOS's
system ncurses has four buttons and drops a wheel-down notch inside the library.
`SASSY_CODE_MOUSE_DEBUG=<path>` logs one classification line per event; unset,
it is inert. The `n` and `m` boxes route mouse events through the same
`run_picker()`, where a click on an item picks and a click outside cancels.

## State files

Panel state lives in `~/.sassywares/sassy-code/`: `current` holds the presented
session id, so a restart re-presents it. `SASSY_CODE_STATE_DIR` moves the whole
directory, which is how the README screenshot runs a demo store in a temp dir.

- `goals.json` — `{"version": 1, "projects": {<cwd>: [goal, ...]}, "loose":
  {<cwd>: [session_id, ...]}}`. A goal's optional `parent` that is missing or
  unknown means top-level, so old files load unchanged. The `loose` map records
  explicit loose membership and may be missing.
- `sessions.json` — `{"version": 1, "sessions": {<id>: {"done": true,
  "done_at": <epoch>}}}`; activity newer than `done_at` revives a session
  without a write.
- `trash/index.json` — `{"version": 1, "entries": [entry, ...]}`, one entry per
  deleted session: `session_id`, `harness`, `title`, `original_path`,
  `trashed_path`, `cwd`, `goal_id`, `loose`, `done`, `deleted_at`. Transcripts
  live under `trash/files/`; a collision gets a timestamped name. Entries older
  than seven days are pruned on start.

All stores are cached by mtime and size and written atomically: re-read, write
a temp file in the state dir, rename. A missing file means no goals, no done
marks or no trash; a corrupt or wrong-shape file hides that data, refuses
edits, warns in the footer and is never overwritten. The old
`~/.sassy-code-current` migrates in once on start, and the new file wins if
both exist. Attention marks are in memory only and lost on restart.

## Delete and trash

`D` on a session runs one preflight before anything moves. The transcript must
be readable and show no unresolved turn (`pending_turn()`), no open background
work or queued input, and the process snapshot must not be empty. An agent in
the session's managed panes is safe; an agent running under another session id
or outside managed panes refuses as external. The predicate and the stop live in
`panel.trash`, and the goal/slice cascade reuses both: it preflights every
explicit and inherited session in the subtree, and one unsafe member refuses the
whole delete before anything moves. A managed agent gets `SIGTERM` and up to three
seconds; a timeout refuses with nothing deleted, and the panel never sends
`SIGKILL` or signals a shell or tmux.

After the stop, the transcript moves to `trash/files/`, the manifest entry is
written, and the session's done record and explicit goal/loose membership are
removed. If writing `current` or a store fails after the move, the transcript
moves back and the records are rolled back. A presented session leaves its pane
alive as a shell and clears the pane's `@sassy_session` tag; a hidden background
pane is killed and dropped from the reap state. On success the highlight goes to
the row that took the deleted row's place: the row below, else the row above,
else the project heading. The neighbour is taken from the rows as drawn, so a
filter or a tier is honored, and a tier row (`more`, `goal_more`, `slice_more`)
is skipped because deleting a session lowers the hidden count and that row may
not be drawn next time.

`delete_subtree()` is the container cascade, one transaction over
`goal_subtree_members()`. It re-derives the member set from a fresh
`goals.json` parse before the first move, so a session another writer moved out
of the subtree is left alone and recorded entries match what is actually moved.
Every manifest entry is computed before the first transcript moves, so a store
read cannot fail halfway. It stops the whole subtree first, then moves every
transcript, then rewrites the trash index, the done marks and the goal records;
each store write is atomic and happens once. A stop failure aborts before any
move and leaves already-stopped sessions stopped. A move or write failure after
the first file moved rolls every transcript back, drops the entries whose files
returned, restores the done marks and leaves the goal records unchanged. When a
transcript cannot be moved back, the footer says so and that file stays in the
trash (indexed whenever the index is writable) instead of disappearing. On
success it selects the parent goal (for a slice) or the project heading.

`t` opens the Trash picker. Entries are grouped by project, deterministically
ordered by project name and newest first inside a group, with a non-selectable
project header line starting each group and a `PICKER_DIVIDER` rule between
groups (never above the first header and none in the empty or empty-filter
states). Each row is one aligned display line with title, harness and
right-aligned age columns; the title takes the width the dropped project column
freed and is truncated with `…` first, so the harness and age survive a narrow
panel. The box pins an action line read from the keymap's `Trash entry` rows
(`⏎ restore  d purge`) below a separator rule: the panel footer behind a modal
box is stale and may be covered, so the box owns its own visible actions. Neither
the header nor the rule nor the hints are selectable or pickable. `Enter` restores: the
transcript moves back to its original path, the done record returns, and
`file_session_on_restore()` files it in its recorded goal or slice, reopening
and expanding that container. A missing container leaves the session loose. If
the original path is occupied or the trash copy is missing, restore refuses and
leaves the entry alone. `d` then `y` purges the entry and transcript for good;
purge also runs automatically for entries older than seven days at startup.

## Reaping

Only hidden panes are reaped, and only when the session is finished for real;
visible panes are never auto-reaped, and switching away never force-reaps from a
file read alone. Five independent gates:

- the latest assistant message carries a terminal stop reason (Pi `stop`,
  `error`, `aborted`; Claude `end_turn`, `stop_sequence`, `max_tokens`,
  `refusal`);
- that turn lasted the full `REAP_GRACE` (2 s), so a file that lags the process
  cannot kill a just-switched-away turn;
- no open background work: Claude Code launches and `SendMessage` resumes,
  closed by a matching `<task-notification>` or `TaskStop` result, including the
  "No task found with ID" error form — interim notifications close nothing;
- no input enqueued and not yet dequeued, removed or answered;
- no descendant agent process (a leftover shell, removed regardless); a run that
  only ever got an interim notification stays open until that check.

A hidden pane that completed its turn sets the in-memory attention mark, which
keeps its row on needs input after the pane is gone until the session is
presented or starts a new turn. Attention never keeps a process alive.

`q`, Ctrl-C and Ctrl-C in the delete prompt call `detach_panel()`, which runs
`tmux detach-client` for the panel's session and nothing else: panel, viewer and
agents keep running, so `sassy-code` reattaches to the same state. Outside tmux
there is no client, so they exit the panel instead.
