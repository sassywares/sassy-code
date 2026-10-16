# AGENTS.md

Guide for agents and contributors working in `sassy-pi`.

## What this is

A two-pane tmux control panel for Pi sessions: a narrow left **control panel**
that lists every session grouped by project, and a wide right **session viewer**
that resumes whichever session is selected.

## Layout

| Path | Role |
|---|---|
| `sassy-pi` | Bash launcher. Builds the tmux layout, passes the viewer pane id to the panel. |
| `panel.py` | The curses control panel. Reads the Pi session store, renders the list, dispatches actions. |
| `CONCEPTS.md` | Vocabulary used across the repo. Read before changing user-facing terms. |
| `docs/solutions/` | Durable learnings. Read the relevant entry before re-investigating a problem. |

## How to run

Requires `tmux`, `python3`, `pi`, and bash. See
[README § Requirements](README.md#requirements) for install links.

```bash
./sassy-pi                 # or the `sassy-pi` shell alias
./panel.py --list          # plain-text dump of parsed sessions, no TUI
```

The launcher self-locates via `BASH_SOURCE`, so it works from any path or symlink.

## Architecture in one paragraph

`panel.py` parses Pi's JSONL session files directly (the session store under
`~/.pi/agent/sessions/`), groups them by working directory, infers a status from
each file's last message plus a process-table check, and renders the result with
`curses`. On Enter it asks tmux to `respawn-pane` the viewer pane into
`pi --session <id>` in the session's own cwd, then moves focus to that pane.
There is no Pi API dependency and no server; the session files are the contract.

## Conventions

- **Stdlib only.** `panel.py` relies solely on the Python standard library.
- **Single line per session.** Titles truncate with `…`; the age is right-aligned.
  Keep the panel narrow and scannable.
- **Narrow panel, wide viewer.** The panel pane is the small one. If a change
  makes the panel wide, the layout is wrong (see `docs/solutions/tmux/`).
- **Actions are tmux commands from the panel.** The panel never blocks on the
  viewer pane; it only respawns and focuses it.

## Knowledge store

- Vocabulary and naming: [`CONCEPTS.md`](CONCEPTS.md)
- Durable learnings and past bugs: [`docs/solutions/`](docs/solutions/)
- Document new solved problems there rather than in code comments.

## Direction

The panel logic can become a native Pi extension: `SessionManager.listAll()`
lists sessions and `ctx.switchSession(path)` resumes one in place, at the cost of
the side-by-side view. The two-pane layout fundamentally requires tmux, since a
Pi extension lives inside one Pi process's TUI. See
[`docs/solutions/pi-extension/`](docs/solutions/pi-extension/).
