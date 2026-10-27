# Sassy Code

**A lean, fast tmux control panel for Pi and Claude Code sessions — switch sessions without losing work.**

Sassy Code gives you a two-pane cockpit: a fast session list on the left and the
live agent session on the right. Switching keeps in-flight work alive in
background tmux panes. Sessions from both harnesses share one list, grouped by
working directory and marked with a small glyph: `π` for Pi, `✳` for Claude Code.

<img width="1536" height="988" alt="image" src="https://github.com/user-attachments/assets/5d8ef5c1-28f2-48ec-a2c8-950c501a8864" />

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
| [Python 3](https://www.python.org/downloads/) | runs `panel.py` (standard library only) | `brew install python` / `sudo apt install python3` / [python.org](https://python.org/downloads/) |
| [Pi](https://pi.dev) and/or [Claude Code](https://claude.com/claude-code) | the sessions this panel manages | `curl -fsSL https://pi.dev/install.sh \| sh` / `npm install -g @anthropic-ai/claude-code` |
| bash | runs the `main` launcher | preinstalled on macOS and most Linux distributions |

At least one harness must be installed; the panel simply shows an empty list for
a store it cannot find. Runs on macOS and Linux (including WSL). The panel
renders with Python's standard-library `curses`, so no extra Python packages are
required.

## Quick start

Clone the repo and run the launcher:

```bash
git clone https://github.com/sassywares/sassy-code.git
cd sassy-code
./main
```

If the file is not executable, either `chmod +x main` or run it as `bash main`.

- Run outside tmux: creates a `sassy-code` session with the two panes.
- Run inside tmux: splits the current window and adds the panel to the left.
- `SASSY_CODE_WIDTH=25% ./main` overrides the left pane width (default `22%`).
- `SASSY_CODE_NEW_KEY=C-n ./main` chooses the prefix key for new sessions
  (default `N`; a key that is already bound is never taken).
- `SASSY_CODE_NEW_HARNESS=claude ./main` makes `n` start Claude Code when the
  highlighted project has no session to infer a harness from. Without the
  override, the panel picks whichever harness is installed.

## Optional: add an alias

Run `sassy-code` from anywhere by adding an alias to your shell config. Run this
from inside the cloned repo so `$PWD` resolves to it.

zsh (`~/.zshrc`):

```sh
echo "alias sassy-code='$PWD/main'" >> ~/.zshrc && source ~/.zshrc
```

bash (`~/.bashrc`, or `~/.bash_profile` on macOS):

```sh
echo "alias sassy-code='$PWD/main'" >> ~/.bashrc && source ~/.bashrc
```

Then just run `sassy-code`. If you prefer a command over an alias, symlink it onto
your `PATH` instead:

```sh
ln -s "$PWD/main" ~/.local/bin/sassy-code
```

Keys in the left pane: `↑/↓` or `j/k` move, `g/G` jump, `Enter` presents a
session (and focuses the right pane), collapses/expands a directory, or toggles
`… N more`/`… show fewer`, `←/→`
or `h/l` collapse/expand a directory or a project's session limit, `n` new
session in the selected project (using the harness of the highlighted session),
`r` reload every managed Pi session (sends `/reload` to each live Pi pane and
refreshes the session list), `/` filter (which auto-expands groups and also
matches the harness name), `q` quit.
The highlight is attached to the session, not the row, so it follows a session
as new activity re-sorts the list. The footer wraps to show every binding at the
default narrow panel width. Quitting moves a
viewer whose agent is still running into the background before the panel exits.
From anywhere in the window, `prefix + N` asks the panel to run its own `n`
action — a new session in the project under the panel highlight — without
moving focus, so the keyboard stays in the viewer. The shortcut is installed
only when that key is free; set `SASSY_CODE_NEW_KEY` to choose another.
Switch back to the panel with your tmux prefix + arrow. The launcher removes
repeat from tmux's default repeatable prefix+arrow `select-pane` bindings so the
next arrow reaches the panel; bindings with a different command are left alone. A
user binding byte-identical to tmux's default is indistinguishable and will
be normalized.

## How it works

`main` (bash) creates the narrow control panel and the viewer slot, then passes
the viewer pane id to `panel.py`. `panel.py` (Python, stdlib only, non-blocking)
reads both harness stores, renders the list with `curses`, and drives tmux.

Every managed pane carries a pane option, `@sassy_session=<id>`, which is the
authoritative identity even when panes move. The panel finds its own pane from
the tmux environment and re-derives the viewer as the other pane in the visible
window after every swap; pane ids travel with processes, so no cached viewer id
is trusted.

On `Enter`, the panel:

1. Adopts an existing tagged pane for the session, if one exists. When a
   session has more than one tagged pane, the one already in the viewer wins,
   so `Enter` focuses the live session instead of swapping in a stale copy.
2. Otherwise starts the harness for that session (`pi --session <id>` or
   `claude --resume <id>`) in a new hidden single-pane window in the
   `sassy-code-bg` session.
3. Runs `tmux swap-pane -dZ` between that pane and the viewer, then focuses the
   viewer.

Nothing is respawned or killed to switch. The displaced viewer process is
swapped into the background session with its process, scrollback, and cwd
intact. Re-selecting a live session swaps the same pane back instead of starting
a duplicate.

For Pi, `/handoff`, `/fork`, and `/clone` replace the session inside the same Pi
process, so the pane's command line and `@sassy_session` option keep naming the
old session. The panel follows the successor recorded in the new file's
`parentSession` header, re-tags the pane with it, and moves the **running**
marker to that session. A second handoff can record the session the process was
started with as its parent, making the two successors siblings; the panel
resolves the pane from both its tag and its command line and keeps the newest
session in that lineage. When the switched session is the one in the viewer, the
panel highlight (and the recorded current session) moves to the successor on
the next refresh; browsing another row is never interrupted. A just-created
session therefore reads `now` and is recognised as already open.

Claude Code transcripts do not have a session-level parent link, so the panel
treats each resumed Claude session as its own identity. Subagent sidechains
inside a transcript are ignored when inferring what the main session is doing.

### Background lifecycle

A hidden pane stays alive while its last turn is unresolved: the last message is
a user turn or tool result, the assistant is still at `stopReason=toolUse` /
`stop_reason=tool_use`, or no terminal assistant stop reason has been recorded.
Once the latest assistant message ends with a terminal reason (`stop`, `error`,
`aborted` for Pi; `end_turn`, `stop_sequence`, `max_tokens`, `refusal` for
Claude Code), the panel reaps the hidden pane after a short refresh grace
period. Switching away always starts that grace period instead of killing from a
file-only completion; the JSONL can lag the agent process. The visible session
is never auto-reaped.

If a hidden agent exits on its own, the panel detects that the pane has no live
descendant agent process and removes the leftover shell and window. If a session
is already running outside managed panes, `Enter` is refused with a warning
until that external process exits.

On `q` (and `Ctrl-C`), a viewer with a live agent process is swapped into the
background session behind a temporary placeholder shell, even if the session
file still looks complete. It survives after the panel exits for as long as the
tmux server and `sassy-code-bg` session live. A completed viewer with no live
agent does not need preserving.

You can inspect managed panes with:

```bash
tmux list-panes -a -F '#{pane_id} #{session_name} #{@sassy_session}'
```

The background session is disposable: `tmux kill-session -t sassy-code-bg`
stops any hidden sessions and the panel will recreate it lazily on the next
switch.

## Data source

The panel reads two on-disk stores and merges them into one list.

**Pi** — `~/.pi/agent/sessions/--<encoded-cwd>--/<ts>_<id>.jsonl`. Each file is
JSONL:

- first line: `{"type":"session", "id", "cwd", "timestamp", ...}`
  (`parentSession` points at the file this session was handed off, forked, or
  cloned from)
- `{"type":"session_info", "name"}` — set by `/name`
- `{"type":"message", "message": {"role", "content", "stopReason", ...}}`

**Claude Code** — `~/.claude/projects/<encoded-cwd>/<uuid>.jsonl` (honours
`CLAUDE_CONFIG_DIR`; the leading `/` of the cwd becomes `-`). These files have no header: the first entry carrying
`sessionId`, `cwd`, and `timestamp` supplies the identity. `user` and
`assistant` entries wrap an Anthropic message (`stop_reason` in snake_case), and
`summary` / `last-prompt` entries supply a title or a prompt reminder. Entries
with `isSidechain: true` belong to subagents and are ignored for status and
preview.

Status is inferred from the last message (timestamps are UTC, rendered in the
viewer's local zone):

| status | rule |
|---|---|
| running | a live `pi --session` / `claude --resume` process runs this session, including a `/handoff`/`/fork` successor of the Pi session it was started with, or the newest session reachable from a managed pane's tag and command line |
| error | last entry is a failed `toolResult`, an assistant `error`/`aborted`, or a Claude API error |
| active | user spoke last, Claude is mid-`tool_use`, or modified within 5 minutes |
| idle | otherwise |

Parsed transcripts are cached by path, mtime, and size, because Claude
transcripts are frequently large and the panel refreshes every two seconds.

## Files

- `main` — tmux launcher / layout.
- `panel.py` — the curses control panel (`--list` prints a plain-text dump;
  `--new-session --pane <id>` is the shortcut entry point).
- `tests/` — isolated tmux lifecycle and key-binding regression tests.
- `AGENTS.md` — repository guide for agents and contributors.
- `CONCEPTS.md` — vocabulary used in this project.
- `docs/solutions/` — durable learnings from building this.
- `assets/` — screenshots used in this README.
