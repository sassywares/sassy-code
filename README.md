# Sassy Pi

**A lean, fast tmux control panel for Pi sessions — switch sessions without losing work.**

Sassy Pi gives you a two-pane cockpit: a fast session list on the left and the
live Pi session on the right. Switching keeps in-flight work alive in background
tmux panes.

![sassy-pi: the session control panel on the left and the resumed session on the right](assets/sassy-pi.png)

Session titles are truncated to a single line with an ellipsis; the age is
right-aligned. Projects are grouped by working directory and labelled with the
folder's own name — `root` for home, `tap2-workspace` instead of the whole path
— with a blank line between projects. A project shows its five most recent
sessions, then `… N more`; that row toggles to `… show fewer` once expanded. A
live (running) session is never hidden behind the limit.

## Table of contents

- [Requirements](#requirements)
- [Quick start](#quick-start)
- [Optional: add an alias](#optional-add-an-alias)
- [How it works](#how-it-works)
  - [Background lifecycle](#background-lifecycle)
- [Data source](#data-source)
- [Files](#files)

## Requirements

| Tool | Why it is needed | Install |
|---|---|---|
| [tmux](https://github.com/tmux/tmux/wiki/Installing) | creates the two panes and keeps background sessions alive | `brew install tmux` / `sudo apt install tmux` / [other platforms](https://github.com/tmux/tmux/wiki/Installing) |
| [Python 3](https://www.python.org/downloads/) | runs `panel.py` (standard library only) | `brew install python` / `sudo apt install python3` / [python.org](https://www.python.org/downloads/) |
| [Pi](https://pi.dev) | the sessions this panel manages | `curl -fsSL https://pi.dev/install.sh \| sh` |
| bash | runs the `main` launcher | preinstalled on macOS and most Linux distributions |

Runs on macOS and Linux (including WSL). The panel renders with Python's
standard-library `curses`, so no extra Python packages are required.

## Quick start

Clone the repo and run the launcher:

```bash
git clone https://github.com/sassywares/sassy-pi.git
cd sassy-pi
./main
```

If the file is not executable, either `chmod +x main` or run it as `bash main`.

- Run outside tmux: creates a `sassy-pi` session with the two panes.
- Run inside tmux: splits the current window and adds the panel to the left.
- `SASSY_PI_WIDTH=25% ./main` overrides the left pane width (default `22%`).
- `SASSY_PI_NEW_KEY=C-n ./main` chooses the prefix key for new sessions
  (default `N`; a key that is already bound is never taken).

## Optional: add an alias

Run `sassy-pi` from anywhere by adding an alias to your shell config. Run this
from inside the cloned repo so `$PWD` resolves to it.

zsh (`~/.zshrc`):

```sh
echo "alias sassy-pi='$PWD/main'" >> ~/.zshrc && source ~/.zshrc
```

bash (`~/.bashrc`, or `~/.bash_profile` on macOS):

```sh
echo "alias sassy-pi='$PWD/main'" >> ~/.bashrc && source ~/.bashrc
```

Then just run `sassy-pi`. If you prefer a command over an alias, symlink it onto
your `PATH` instead:

```sh
ln -s "$PWD/main" ~/.local/bin/sassy-pi
```

Keys in the left pane: `↑/↓` or `j/k` move, `g/G` jump, `Enter` presents a
session (and focuses the right pane), collapses/expands a directory, or toggles
`… N more`/`… show fewer`, `←/→`
or `h/l` collapse/expand a directory or a project's session limit, `n` new
session in the selected project,
`r` reload every managed session (sends `/reload` to each live Pi pane and
refreshes the session list), `/` filter (which auto-expands groups), `q` quit.
The highlight is attached to the session, not the row, so it follows a session
as new activity re-sorts the list. The footer wraps to show every binding at the
default narrow panel width. Quitting moves a
viewer whose Pi is still running into the background before the panel exits.
From anywhere in the window, `prefix + N` asks the panel to run its own `n`
action — a new session in the project under the panel highlight — without
moving focus, so the keyboard stays in the viewer. The shortcut is installed
only when that key is free; set `SASSY_PI_NEW_KEY` to choose another.
Switch back to the panel with your tmux prefix + arrow. The launcher removes
repeat from tmux's default repeatable prefix+arrow `select-pane` bindings so the
next arrow reaches the panel; bindings with a different command are left alone. A
user binding byte-identical to tmux's default is indistinguishable and will
be normalized.

## How it works

`main` (bash) creates the narrow control panel and the viewer slot, then passes
the viewer pane id to `panel.py`. `panel.py` (Python, stdlib only, non-blocking)
reads Pi's session store, renders the list with `curses`, and drives tmux.

Every managed pane carries a pane option, `@sassy_session=<id>`, which is the
authoritative identity even when panes move. The panel finds its own pane from
the tmux environment and re-derives the viewer as the other pane in the visible
window after every swap; pane ids travel with processes, so no cached viewer id
is trusted.

On `Enter`, the panel:

1. Adopts an existing tagged pane for the session, if one exists. When a
   session has more than one tagged pane, the one already in the viewer wins,
   so `Enter` focuses the live session instead of swapping in a stale copy.
2. Otherwise starts `pi --session <id>` in a new hidden single-pane window in
   the `sassy-pi-bg` session.
3. Runs `tmux swap-pane -dZ` between that pane and the viewer, then focuses the
   viewer.

Nothing is respawned or killed to switch. The displaced viewer process is
swapped into the background session with its process, scrollback, and cwd
intact. Re-selecting a live session swaps the same pane back instead of starting
a duplicate.

`/handoff`, `/fork`, and `/clone` replace the session inside the same Pi process,
so the pane's command line and `@sassy_session` option keep naming the old
session. The panel follows the successor recorded in the new file's
`parentSession` header, re-tags the pane with it, and moves the **running**
marker to that session. A second handoff can record the session the process was
started with as its parent, making the two successors siblings; the panel
resolves the pane from both its tag and its command line and keeps the newest
session in that lineage. When the switched session is the one in the viewer, the
panel highlight (and the recorded current session) moves to the successor on
the next refresh; browsing another row is never interrupted. A just-created
session therefore reads `now` and is recognised as already open.

### Background lifecycle

A hidden pane stays alive while its last turn is unresolved: the last message is
a user turn or tool result, the assistant is still at `stopReason=toolUse`, or no
terminal assistant stop reason has been recorded. Once the latest assistant
message ends with `stop`, `error`, or `aborted`, the panel reaps the hidden pane
after a short refresh grace period. Switching away always starts that grace
period instead of killing from a file-only completion; the JSONL can lag the Pi
process. The visible session is never auto-reaped.

If a hidden Pi exits on its own, the panel detects that the pane has no live
descendant `pi` process and removes the leftover shell and window. If a session
is already running outside managed panes, `Enter` is refused with a warning
until that external process exits.

On `q` (and `Ctrl-C`), a viewer with a live Pi process is swapped into the
background session behind a temporary placeholder shell, even if the session
file still looks complete. It survives after the panel exits for as long as the
tmux server and `sassy-pi-bg` session live. A completed viewer with no live Pi
does not need preserving.

You can inspect managed panes with:

```bash
tmux list-panes -a -F '#{pane_id} #{session_name} #{@sassy_session}'
```

The background session is disposable: `tmux kill-session -t sassy-pi-bg` stops
any hidden sessions and the panel will recreate it lazily on the next switch.

## Data source

Sessions live under `~/.pi/agent/sessions/--<encoded-cwd>--/<ts>_<id>.jsonl`.
Each file is JSONL:

- first line: `{"type":"session", "id", "cwd", "timestamp", ...}`
  (`parentSession` points at the file this session was handed off, forked, or
  cloned from)
- `{"type":"session_info", "name"}` — set by `/name`
- `{"type":"message", "message": {"role", "content", "stopReason", ...}}`

Status is inferred from the last message (timestamps are UTC, rendered in the
viewer's local zone):

| status | rule |
|---|---|
| running | a live `pi --session`/`--session-id` process runs this session, including a `/handoff`/`/fork` successor of the session it was started with, or the newest session reachable from a managed pane's tag and command line |
| error | last entry is a failed `toolResult` or assistant `error`/`aborted` |
| active | user spoke last, or modified within 5 minutes |
| idle | otherwise |

## Files

- `main` — tmux launcher / layout.
- `panel.py` — the curses control panel (`--list` prints a plain-text dump;
  `--new-session --pane <id>` is the shortcut entry point).
- `tests/` — isolated tmux lifecycle and key-binding regression tests.
- `AGENTS.md` — repository guide for agents and contributors.
- `CONCEPTS.md` — vocabulary used in this project.
- `docs/solutions/` — durable learnings from building this.
- `assets/` — screenshots used in this README.
