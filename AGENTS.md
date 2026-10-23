# AGENTS.md

Guide for agents and contributors working in `sassy-pi`.

## What this is

A two-pane tmux control panel for Pi sessions: a narrow left **control panel**
that lists every session grouped by project, and a wide right **session viewer**
that presents whichever session is selected. Switching sessions swaps the old
viewer process into a hidden background pane instead of killing it.

## Layout

| Path | Role |
|---|---|
| `main` | Bash launcher. Builds the tmux layout, passes the viewer pane id to the panel. |
| `panel.py` | The curses control panel. Reads the Pi session store, manages viewer/background panes, renders the list, dispatches actions. |
| `tests/` | Isolated tmux tests (`tmux -L`, `-f /dev/null`) and key-binding regressions. |
| `CONCEPTS.md` | Vocabulary used across the repo. Read before changing user-facing terms. |
| `docs/solutions/` | Durable learnings. Read the relevant entry before re-investigating a problem. |

## How to run

Requires `tmux`, `python3`, `pi`, and bash. See
[README § Requirements](README.md#requirements) for install links.

```bash
./main                                      # or the `sassy-pi` shell alias
./panel.py --list                            # plain-text dump, no TUI
python3 -m unittest tests.test_background    # isolated tmux lifecycle tests
bash tests/tmux-pane-keys.sh                 # isolated key-binding regression
bash tests/tmux-layout.sh                    # isolated narrow-panel geometry
```

The launcher self-locates via `BASH_SOURCE`, so it works from any path or symlink.

## Architecture in one paragraph

`panel.py` parses Pi's JSONL session files directly (the session store under
`~/.pi/agent/sessions/`), groups them by working directory, infers a status and
turn completion from each file's last message plus a process-table check, and
renders the result with `curses`. Timestamps are UTC epochs; render them only at
draw time. Managed panes are tagged with the tmux pane option
`@sassy_session=<id>`. An in-process switch (`/handoff`, `/fork`, `/clone`)
leaves that tag and the process command line on the old session, so the panel
follows the successor's `parentSession` link, re-tags the pane, and moves the
**running** marker to the successor. On Enter the panel adopts a tagged pane or starts
`pi --session <id>` in a hidden single-pane window in the dedicated
`sassy-pi-bg` tmux session, then uses `swap-pane -dZ` to put it in the viewer.
The displaced viewer process is swapped into the background, keeping its
scrollback, cwd, and in-flight turn. The viewer slot is re-derived from the
panel's tmux window after every swap because pane ids travel with processes.
There is no Pi API dependency and no server; the session files are the contract.

Hidden panes are reaped only when their latest assistant message has a terminal
stop reason (`stop`, `error`, `aborted`) for the full grace period, or when no
descendant `pi` process is left. Switching away never force-reaps from a file
read alone: the JSONL can lag the process. Visible panes are never auto-reaped.
On quit a viewer with a live `pi` process is swapped into the background behind a
placeholder shell so it survives the panel, even if its session file looks
complete. Sessions already running outside managed panes are refused instead of
duplicated.

## Conventions

- **Stdlib only.** `panel.py` relies solely on the Python standard library.
- **Single line per session.** Titles truncate with `…`; the age is right-aligned.
  Keep the panel narrow and scannable.
- **Dense projects.** `rows_for()` shows at most `SESSION_LIMIT` sessions per
  project plus a `more` row; never hide a `running` session. Group headings use
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
- **Background work is lazy.** Only sessions opened through the panel get live
  panes. A working hidden pane survives; a completed hidden pane is reaped after
  the refresh grace. Remove leftover shells with no descendant `pi`.

## Knowledge store

- Vocabulary and naming: [`CONCEPTS.md`](CONCEPTS.md)
- Durable learnings and past bugs: [`docs/solutions/`](docs/solutions/)
- Toolchain: [`docs/solutions/tmux/background-pane-swap.md`](docs/solutions/tmux/background-pane-swap.md)
- Document new solved problems there rather than in code comments.

## Direction

The panel logic can become a native Pi extension: `SessionManager.listAll()`
lists sessions and `ctx.switchSession(path)` resumes one in place, at the cost of
the side-by-side view. The two-pane layout fundamentally requires tmux, since a
Pi extension lives inside one Pi process's TUI. See
[`docs/solutions/pi-extension/`](docs/solutions/pi-extension/).
