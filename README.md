# Sassy Code

**A lean, fast tmux control panel for Pi and Claude Code sessions — switch sessions without losing work.**

Sassy Code gives you a two-pane cockpit: a fast session list on the left and the
live agent session on the right. Switching keeps in-flight work alive in
background tmux panes. Sessions from both harnesses share one list, grouped by
working directory and marked with a small glyph: `π` for Pi, `✳` for Claude Code.

<img width="1536" height="988" alt="image" src="https://github.com/user-attachments/assets/5d8ef5c1-28f2-48ec-a2c8-950c501a8864" />

Titles truncate to one line with an ellipsis; the age is right-aligned. Projects
are labelled with the folder's own name — `root` for home, `tap2-workspace`
rather than the whole path — with a blank line between them. A project shows its
five most recent items (a goal counts as one) plus `… N more`; each reveal
doubles the increment (+10, +20, +40), lifting the cap 5, 15, 35, 75, then all.
`]` expands a collapsed container at the base tier and otherwise reveals one
tier; `[` hides one tier and then collapses. Activating `… N more` reveals and
`… show fewer` hides one, skipping tiers whose cap adds no row. Goals tier from
3 children (sessions and slices together), slices from 3 sessions. No limit hides
a live in-progress session; a collapsed container shows its worst descendant
mark (`✗` > `●` > `◐`) after its count.

## Table of contents

- [Requirements](#requirements)
- [Quick start](#quick-start)
- [Mouse](#mouse)
- [Optional: add an alias](#optional-add-an-alias)
- [How it works](#how-it-works)
  - [Background lifecycle](#background-lifecycle)
- [Data source](#data-source)
- [Files](#files)

## Requirements

| Tool | Why it is needed | Install |
|---|---|---|
| [tmux](https://github.com/tmux/tmux/wiki/Installing) | creates the two panes and keeps background sessions alive | `brew install tmux` / `sudo apt install tmux` / [other platforms](https://github.com/tmux/tmux/wiki/Installing) |
| [Python 3](https://www.python.org/downloads/) | runs `panel/` (standard library only) | `brew install python` / `sudo apt install python3` / [python.org](https://python.org/downloads/) |
| [Pi](https://pi.dev) and/or [Claude Code](https://claude.com/claude-code) | the sessions this panel manages | `curl -fsSL https://pi.dev/install.sh \| sh` / `npm install -g @anthropic-ai/claude-code` |
| bash | runs the `main` launcher | preinstalled on macOS and most Linux distributions |

At least one harness must be installed; the panel shows an empty list for a store
it cannot find. Runs on macOS and Linux (including WSL), and needs no Python
packages beyond the standard library.

## Quick start

Clone the repo and run the launcher:

```bash
git clone https://github.com/sassywares/sassy-code.git
cd sassy-code
./main
```

If the file is not executable, run `chmod +x main` or `bash main`.

- Run outside tmux: creates the dedicated `sassy-code` session and attaches.
  Its status line shows the whole session name before the window list; the
  launcher widens tmux's `status-left-length`, which would otherwise clip it.
- Run inside tmux: switches your client to `sassy-code`, creating it first when
  missing. Your current window is never split, and running it again inside
  `sassy-code` does not add a second panel. A session left without a live panel
  (after a crash, say) is rebuilt by adding a window; the old window and any
  agent in it are kept. The launcher marks the panel pane before it starts, so
  two quick launches still give one panel.
- `SASSY_CODE_WIDTH=25% ./main` overrides the left pane width (default `22%`).
- `SASSY_CODE_NEW_KEY=C-n ./main` chooses the prefix key for new sessions
  (default `N`; a key that is already bound is never taken).
- `SASSY_CODE_NEW_HARNESS=claude ./main` makes `n` start Claude Code when the
  highlighted project has no session to infer a harness from; otherwise the
  panel picks whichever harness is installed.
- `SASSY_CODE_MOUSE_DEBUG=/tmp/sassywares/scratch/mouse.log ./main` appends one
  line per mouse event (`bstate` in hex, x, y, and how it was classified), for
  pinning an unknown wheel bit. Unset, it writes nothing.
- `SASSY_CODE_TMUX_CONFIG_ONLY=1 ./main` applies the tmux options and key
  bindings and exits, without building the layout or starting the panel.
- `PI_CODING_AGENT_SESSION_DIR` points the panel at a non-default Pi session
  store. For Claude Code, `SASSY_CODE_CLAUDE_SESSION_DIR` is the `projects`
  directory itself; `CLAUDE_CONFIG_DIR` is the config directory, and the panel
  appends `projects` to it.

Keys in the left pane: `↑/↓` or `j/k` move, `g/G` jump to the top/bottom,
`Enter` (or a double-click) presents a session and focuses the right pane, or
collapses/expands a directory, goal or slice, or steps a `… N more` row forward
and a `… show fewer` row back. `[`/`]` are one level per press in both
directions; `←/→` and `h/l` are aliases. On a session row they act on its
container, and while a filter is active neither key changes the stored tier.

`n` starts a new session in the selected project, opening one boxed picker with
no section headings: the contextual `+ New goal` item on a project heading or
loose session, `+ New slice` on a goal row or a session inside one (a slice row
offers none), then one line per installed harness (`π Pi`, `✳ Claude Code`),
preselected on the inferred harness, with the `+` in the harness index column.
`j/k`/`↑`/`↓` move (`↑` from the first harness returns to the contextual item),
`Tab` switches between item and harnesses, `1`–`9` or `Enter` picks, and
`Esc`/`q`/`Ctrl-C` cancels. A harness item starts at once, loose or inside the
highlighted container; the Goal item asks for a harness and a name, creates the
container, and starts inside it. Project and goal rows always open the box; a
slice row only with more than one harness, and session rows and `prefix + N`
skip it and use the inferred harness.

`m` on a session opens the **Move to** box: `⌂ Top level` (loose) first,
`+ New goal` second, then a divider rule, then every goal of that project with
its slices as a tree (`├` a sibling, `└` the last child). A marker column holds
one glyph: `⌂` on `Top level` and `✓` on the container the session is in now
(never a done mark); words start in the name column, and a slice's connector
sits there too, so names never shift. Done goals and slices are dimmed.
`j/k`/arrows move, `1`–`9` or `Enter` picks, `Esc`/`q`/`Ctrl-C` cancels. A done
target reopens and expands, and a running session keeps running. On any other
row `m` shows a footer warning.

`r` reloads every managed Pi session (sends `/reload` and refreshes the list),
`/` filters (auto-expanding groups, goals and slices; it also matches the
harness name and goal/slice names), `x` marks a session done (`✓`, dimmed and
sunk below active rows; the footer then reads `x undo`) and `x` again revives
it, `:` opens the command line, and `q` quits (detaching only; the session keeps
running). `:goal:create <name>` makes an empty top-level goal in the highlighted
row's project, and `:slice:create <name>` makes a slice under the highlighted
goal (or under a highlighted slice's parent); both refuse empty or
case-insensitive duplicate names under the same parent.

On a goal or slice row the footer reads `⏎ toggle  n new  R rename  x done  D
delete` (or `x undo` once done): `Enter` toggles it, `R` opens the `:` line
pre-filled with `goal:rename <name>` or `slice:rename <name>`, `x` marks it done
(collapsed, sunk below active siblings; `x` again reopens and expands), and `D`
asks `Delete goal '<name>'? y/n` or `Delete slice '<name>'? y/n` in the footer;
only `y` deletes. Deleting a slice flattens its sessions; deleting a goal removes
its slices and flattens the whole subtree. Sessions are never touched in the
store: they become loose and stay openable. With an unreadable `goals.json` all
three refuse with a footer warning. Done session marks live in
`~/.sassywares/sassy-code/sessions.json`, keyed by session id; an unreadable
file hides them and refuses edits without being overwritten. New activity or `x`
revives. `--list` nests slices under goals and shows done rows with `✓`.

A Pi `/handoff`, `/fork` or `/clone` from a goal or slice session stays in that
container when it lands in the same project. Known limits: a fork into another
working directory stays loose, a lineage passing through another directory
breaks, and a session started with Pi `/new` or Claude Code `/clear` inside a
goal session has no link to it and lands loose; start new phases with `n` /
`prefix + N`. Moves stay inside one project, containers cannot be moved, and a
moved-out Pi session's successors stay loose. The highlight follows the session,
not the row, as activity re-sorts the list.

From anywhere in the window, `prefix + N` asks the panel to run its own `n`
action in the project under the highlight without moving focus, so the keyboard
stays in the viewer (it sends the internal key `N`, skipping the new-session
box). The shortcut is installed only when that key is free;
`SASSY_CODE_NEW_KEY` chooses another. Switch back to the panel with your tmux
prefix + arrow. The launcher removes repeat from tmux's default repeatable
prefix+arrow `select-pane` bindings so the next arrow reaches the panel;
bindings with a different command are left alone, and a user binding
byte-identical to tmux's default is indistinguishable and will be normalized.

## Mouse

The panel turns on terminal mouse reporting while it runs; a terminal without
mouse support simply gets no mouse events, and the keyboard path is unchanged.

- Click a session row to select it. Click the same row again within about 0.4
  seconds to present it in the viewer.
- Click a project heading, a goal, or a slice to collapse or expand it.
- Click a `… N more` row to reveal the next tier, or `… show fewer` to hide one.
- Scroll the wheel to move the highlight; one notch moves exactly one row, and
  duplicate events from one notch are debounced. Wheel-up works on every
  Python/curses build; wheel-down needs 5-button support (`BUTTON5_*`), and the
  macOS system ncurses has only four buttons, so wheel-down is inert there —
  best-effort, never emulated.
- Press on a session row, then release on a goal or slice in the same project to
  move the session there. Release on that project's heading to make it loose.
  While the button is down the footer reads `moving <title>…`; releasing
  anywhere else — another session, a hint line, the gap between projects, or
  another project — cancels the move.
- Click a goal or harness item in the `n` box, or a target in the `m` box, to
  pick it; click outside the box to cancel. A click on the divider in the `n`
  box does nothing.

Hold `Shift` while dragging to select terminal text as usual: the terminal
handles `Shift`-drag itself, so the panel never sees it. Without `Shift`, a drag
that starts on a session row is a move.

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

Then just run `sassy-code`, or symlink it onto your `PATH` instead:
`ln -s "$PWD/main" ~/.local/bin/sassy-code`.

## How it works

`main` (bash) creates the narrow control panel and the viewer slot, then passes
the viewer pane id to `panel/` (Python, stdlib only, non-blocking), which reads
both harness stores, renders the list with `curses`, and drives tmux.

On `Enter`, the panel adopts an existing managed pane for the session if one
exists (when there are several, the one already in the viewer wins, so `Enter`
focuses the live session rather than swapping in a stale copy). Otherwise it
starts the harness for that session (`pi --session <id>` or `claude --resume
<id>`) in a new hidden single-pane window in the `sassy-code-bg` session, then
runs `tmux swap-pane -dZ` between that pane and the viewer and focuses the
viewer. Nothing is respawned or killed to switch. The displaced viewer process
is swapped into the background with its process, scrollback and cwd intact, and
re-selecting a live session swaps the same pane back instead of duplicating it.

For Pi, `/handoff`, `/fork` and `/clone` replace the session inside the same Pi
process; when the switched session is in the viewer, the highlight and recorded
current session follow the successor on the next refresh, and browsing another
row is never interrupted.

### Background lifecycle

A hidden pane stays alive while its last turn is unresolved: the last message is
a user turn or tool result, the assistant is still at `stopReason=toolUse` /
`stop_reason=tool_use`, or no terminal stop reason has been recorded. Once the
latest assistant message ends with a terminal reason (`stop`, `error`, `aborted`
for Pi; `end_turn`, `stop_sequence`, `max_tokens`, `refusal` for Claude Code),
the panel reaps the hidden pane after a short refresh grace period. Switching
away always starts that grace period instead of killing from a file-only
completion; the JSONL can lag the process. A Claude Code session with open
background tasks (announced in tool results, closed by a `<task-notification>`)
is not reaped even after a terminal reason. The visible session is never
auto-reaped.

If a hidden agent exits on its own, the panel detects that the pane has no live
descendant agent process and removes the leftover shell and window. If a session
is already running outside managed panes, `Enter` is refused with a warning
until that external process exits.

On `q` (and `Ctrl-C`) nothing is moved or killed: your client just detaches from
the `sassy-code` tmux session. The panel, viewer and every agent keep running,
and `sassy-code` reattaches to them. End the session for good with
`tmux kill-session -t sassy-code`.

You can inspect managed panes with:

```bash
tmux list-panes -a -F '#{pane_id} #{session_name} #{@sassy_session}'
```

The background session is disposable: `tmux kill-session -t sassy-code-bg`
stops any hidden sessions and the panel recreates it lazily on the next switch.

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
`CLAUDE_CONFIG_DIR`; the leading `/` of the cwd becomes `-`). These files have
no header: the first entry carrying `sessionId`, `cwd`, and `timestamp` supplies
the identity. `user` and `assistant` entries wrap an Anthropic message
(`stop_reason` in snake_case), and `summary` / `last-prompt` entries supply a
title or prompt reminder. Entries with `isSidechain: true` belong to subagents
and are ignored for status and preview.

Status is inferred from the transcript and live processes (timestamps are UTC,
rendered in the viewer's local zone):

| status | mark |
|---|---|
| in progress | `●` green |
| needs input | `◐` yellow |
| idle | `·` blue |
| error | `✗` red |
| done | `✓` dim |

How each status is inferred, and the precedence between them, is in
[`docs/architecture.md`](docs/architecture.md#data-flow). A hidden session that
finishes a turn gets an in-memory attention mark, cleared when it is presented
or starts a new turn and lost on restart. Attention never keeps a pane process
alive. Transcripts are cached by path, mtime and size, so the refresh stays
cheap.

## Files

- `main` — tmux launcher and layout.
- `panel/` — the curses control panel package.
- `tests/` — isolated tmux lifecycle, key-binding and unit tests.
- `docs/` — architecture, durable learnings, plans.
- `bin/` — the vendored release script and its ignore list.
