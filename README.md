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
items (a goal counts as one), then `… N more`; revealing doubles the increment
each tier (+10, +20, +40), so the cap climbs 5, 15, 35, 75, then all. `]`
reveals one level: it expands a collapsed project, goal or slice at the base
tier, then reveals the next tier. `[` goes back one level the same way: it hides
one tier, then collapses the container once it is back at the base tier.
Activating a `… N more` row reveals the next tier, and activating
`… show fewer` hides one. A reveal skips any tier its cap leaves unchanged, so
it always lands on rows that appear. The row shows the remaining count, and
becomes `… show fewer` once everything is shown. It exists at the base tier
only while something is hidden; once you step past the base tier it stays in
place, so the list can be shrunk again. Expanding a collapsed container always
returns to the base tier. An expanded goal tiers the same way from its three
most recent children — direct sessions and slices counted together — and an
expanded slice tiers its three most recent sessions. A live in-progress session
is never hidden behind any of the limits, even when it sits beyond the current
tier, and a collapsed goal or slice shows its rollup mark when a descendant
holds one. A collapsed project heading rolls up the same worst descendant status
(`✗` > `●` > `◐`) after its count; an expanded heading shows no mark, because
its rows carry their own.

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

- Run outside tmux: creates the dedicated `sassy-code` session (two panes) and attaches.
- Run inside tmux: switches your client to the `sassy-code` session, creating it
  first when missing. Your current window is never split, and running it again
  from inside `sassy-code` does not add a second panel. A `sassy-code` session
  left without a live panel (for example after a crash) is rebuilt by adding a
  new window; the old panel-less window is kept, and any agent in it keeps running. The launcher marks the panel pane before it starts, so
  running it twice in quick succession still gives one panel (a mark older than
  ten seconds with no Python behind it counts as dead).
- `SASSY_CODE_WIDTH=25% ./main` overrides the left pane width (default `22%`).
- `SASSY_CODE_NEW_KEY=C-n ./main` chooses the prefix key for new sessions
  (default `N`; a key that is already bound is never taken).
- `SASSY_CODE_NEW_HARNESS=claude ./main` makes `n` start Claude Code when the
  highlighted project has no session to infer a harness from. Without the
  override, the panel picks whichever harness is installed.
- `SASSY_CODE_MOUSE_DEBUG=/tmp/sassywares/scratch/mouse.log ./main` appends one
  line per mouse event (`bstate` in hex, x, y, and how the panel classified it)
  to that file, for pinning an unknown wheel bit. Unset, it writes nothing.
- `PI_CODING_AGENT_SESSION_DIR` points the panel at a non-default Pi session
  store. For Claude Code, `SASSY_CODE_CLAUDE_SESSION_DIR` is the `projects`
  directory itself; `CLAUDE_CONFIG_DIR` is the config directory, and the panel
  appends `projects` to it.

Keys in the left pane: `↑/↓` or `j/k` move, `g/G` jump, `Enter` or a
double-click presents a session (and focuses the right pane), collapses/expands
a directory, goal, or slice, or reveals the next tier on a project's, goal's,
or slice's `… N more` row (and `… show fewer` hides one tier). `[`/`]` are the
detail pair, one level per press in both directions: `]` expands a collapsed
project, goal or slice at the base tier, then reveals the next tier; `[` hides
one tier, then collapses the container at the base tier. On a session row they
act on its container; on a `more` row they step one tier down or up, and `[` at
the base tier collapses the container. Expanding always shows the base tier.
While a filter is active neither key changes the stored tier.
`←/→` or `h/l` stay aliases for the same one-level `[`/`]` behaviour, `n` new
session in the selected project. It opens one boxed picker with no section
headings: the contextual `+ New goal` item appears on a project heading or
loose session and `+ New slice` on a goal row or a session inside one (a slice
row offers no contextual item), then one line per installed harness (`π Pi`,
`✳ Claude Code`), preselected on the inferred harness. A divider rule separates
the contextual item from the harnesses; the `+` sits in the harness index
column and the label in the glyph column, so the item lines up with a harness
row. `j/k`/`↑`/`↓` move — `↑` from the first harness returns to the
contextual item and `↓` from it lands on the first harness — `Tab` switches
between the item and the harnesses, `←`/`→` do nothing, `1`–`9` or `Enter`
pick, and `Esc`/`q`/`Ctrl-C` cancel without creating anything. A harness item
starts at
once — loose, or inside the highlighted goal or slice. The Goal item then asks
for a harness and a name, creates the goal or slice, and starts the session
inside it. Project and goal rows open the box even with one harness installed;
slice rows open it only when more than one harness is installed. Session rows
and `prefix + N` skip the box and use the inferred harness. A slice row starts
the session directly inside that slice and never offers a nested slice.
`m` on a session opens the **Move to** box: `⌂ Top level` (no goal, i.e. loose)
first, `+ New goal` (creates a top-level goal and moves the session into it)
second, then a divider rule, then every goal of that project with its slices
drawn as a tree under it — `├` for a sibling, `└` for the last child, and no
connector line under a goal with no slices. The marker column holds one
glyph: `⌂` on `Top level`, the words starting in the name column like every
goal name; a `✓` marks the container the session is in now (never done), so
it is `✓ Top level` for a loose session. A goal name starts in the next
column, and a slice's connector sits in that same column with the slice name
after it, so names never shift. Done goals and slices are dimmed. Labels are
shown in full at normal widths; a narrow terminal or a very long name
truncates with `…`.
`j/k`/arrows move, `1`–`9` or `Enter` pick, and `Esc`/`q`/`Ctrl-C` cancel.
Moving into a done goal or slice reopens and expands it; a running session
keeps running across the move. On
any other row `m` shows a footer warning and changes nothing. Both pickers also
accept a click to pick an item, and a click outside either box cancels. The
mouse mirrors the list actions — select, present, expand, reveal, and
drag-to-move — as described in [Mouse](#mouse).
`r` reload every managed Pi session (sends `/reload` to each live Pi pane and
refreshes the session list), `/` filter (which auto-expands groups, goals and
slices and also matches the harness name plus goal and slice names), `x` marks
a session done (`✓`, dimmed and sunk below active rows; the footer then reads
`x undo`) and `x` again revives it, `:` command line, `q` quit
(this only detaches your client; the session keeps running).
`:goal:create <name>` makes an empty top-level goal in the highlighted row's
project, and `:slice:create <name>` makes a slice under the highlighted goal
(or under the parent of a highlighted slice); both refuse empty or
case-insensitive duplicate names, where a duplicate means the same name under
the same parent. `Enter` toggles a goal or slice, while `[`/`]` and `h`/`l`
step one level at a time — collapsing at the base tier and hiding a tier above
it — and the state persists in `~/.sassywares/sassy-code/goals.json`.
Goal- and slice-row keys (the footer then reads `⏎ toggle  n new  R rename  x
done  D delete`, or `x undo` once done): `R` opens the `:` line pre-filled with
`goal:rename <name>` for a goal or `slice:rename <name>` for a slice (empty or
duplicate sibling names are refused), `x` marks the goal or slice done (`✓`,
collapsed, sunk below its active siblings) and `x` again reopens and expands it,
and `D` asks `Delete goal '<name>'? y/n` or `Delete slice '<name>'? y/n` in the
footer; only `y` deletes. Deleting a slice flattens only its own sessions;
deleting a goal removes its slices and flattens every session in the whole
subtree. Sessions are never touched in the store: they become loose and stay
openable. With an unreadable `goals.json` all three refuse with a footer
warning. Done session marks live in
`~/.sassywares/sassy-code/sessions.json`, keyed by session id; an unreadable file
hides the marks and refuses edits without being overwritten. New activity or
`x` again revives a done session. `--list` nests slices under goals and shows
done rows with `✓`. A Pi `/handoff`, `/fork`, or `/clone` from a goal or slice
session stays in that container when it lands in the same project. Known limits: a fork into another working
directory belongs to that project and stays loose, a lineage passing through
another directory breaks (lineage resolves within one project), and a session started with Pi `/new` or Claude Code `/clear` inside a
goal session has no link to it and lands loose; start new phases with `n` /
`prefix + N` instead. Moves stay inside one project, and goals or slices
themselves cannot be moved. A moved-out Pi session's `/handoff`, `/fork`, or
`/clone` successors stay loose with it.
The highlight is attached to the session, not the row, so it follows a session
as new activity re-sorts the list. The footer wraps to show every binding at the
default narrow panel width. Quitting (`q` or `Ctrl-C`) only detaches your client from the
`sassy-code` tmux session. The session, panel, viewer and agents keep running;
run `sassy-code` again to reattach. To end it for good, run
`tmux kill-session -t sassy-code`.
From anywhere in the window, `prefix + N` asks the panel to run its own `n`
action — a new session in the project under the panel highlight — without
moving focus, so the keyboard stays in the viewer (it sends the panel the
internal key `N`, `n` without the new-session box, so it never asks which
harness or target). The shortcut is installed
only when that key is free; set `SASSY_CODE_NEW_KEY` to choose another.
Switch back to the panel with your tmux prefix + arrow. The launcher removes
repeat from tmux's default repeatable prefix+arrow `select-pane` bindings so the
next arrow reaches the panel; bindings with a different command are left alone. A
user binding byte-identical to tmux's default is indistinguishable and will
be normalized.

## Mouse

The panel turns on terminal mouse reporting while it runs; a terminal without
mouse support simply gets no mouse events, and the keyboard path is unchanged.

- Click a session row to select it. Click the same row again within about 0.4
  seconds to present it in the viewer; a single click never presents.
- Click a project heading, a goal, or a slice to collapse or expand it.
- Click a `… N more` row to reveal the next tier, or `… show fewer` to hide one
  tier.
- Scroll the wheel to move the highlight; one notch moves exactly one row, and
  duplicate events from one notch (press then click) are debounced. Wheel-up
  works on every Python/curses build. Wheel-down needs a curses build with
  5-button support (`BUTTON5_*`); the macOS system ncurses has only four
  buttons and drops a wheel-down notch before the panel sees it, so wheel-down
  is inert there — best-effort, never emulated from another event.
- Press on a session row, then release on a goal or slice in the same project to
  move the session there. Release on that project's heading to make it loose.
  While the button is down the footer reads `moving <title>…`; releasing
  anywhere else — another session, a hint line, the gap between projects, or
  another project — cancels the move.
- Click a goal or harness item in the `n` box, or a target in the `m` box, to
  pick it; click outside the box to cancel. A click on the divider in the `n`
  box does nothing.
- Clicking a hint line or the gap between projects does nothing.

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

Then just run `sassy-code`. If you prefer a command over an alias, symlink it onto
your `PATH` instead:

```sh
ln -s "$PWD/main" ~/.local/bin/sassy-code
```

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
`parentSession` header, re-tags the pane with it, and moves the **in
progress** marker to that session. A second handoff can record the session the process was
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
file-only completion; the JSONL can lag the agent process. A Claude Code
session with open background tasks (announced in tool results, closed by a
`<task-notification>`) is not reaped even after a terminal reason, so resumed
agents keep running. The visible session is never auto-reaped.

If a hidden agent exits on its own, the panel detects that the pane has no live
descendant agent process and removes the leftover shell and window. If a session
is already running outside managed panes, `Enter` is refused with a warning
until that external process exits.

On `q` (and `Ctrl-C`) nothing is moved or killed: your client just detaches
from the `sassy-code` tmux session. The panel, viewer and every agent keep
running, and `sassy-code` reattaches to them. End the session for good with
`tmux kill-session -t sassy-code`.

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

Status is inferred from the transcript and live processes (timestamps are UTC,
rendered in the viewer's local zone). Precedence is
`error > done > in progress > needs input > idle`:

| status | mark | rule |
|---|---|---|
| in progress | `●` green | a live `pi --session` / `claude --resume` process has an unresolved turn: a user or tool result is being handled, or the last assistant turn has no terminal stop reason (a background run outliving an `end_turn` does not count; it only blocks reaping) |
| needs input | `◐` yellow | a live process has finished its turn (or has no messages yet), a Claude `AskUserQuestion` has no answer, or a hidden session finished and kept an in-memory attention mark until presented |
| idle | `·` blue | no live process and no attention mark |
| error | `✗` red | last entry is a failed `toolResult`, an assistant `error`/`aborted`, or a Claude API error |
| done | `✓` dim | marked with `x` in `sessions.json`; revived by new activity or `x` again |

A hidden session that finishes a turn gets an in-memory attention mark, cleared
when it is presented or starts a new turn and lost on restart. Attention never
keeps a pane process alive.

Parsed transcripts are cached by path, mtime, and size, because Claude
transcripts are frequently large and the panel refreshes every two seconds.

## Files

- `main` — tmux launcher / layout.
- `panel.py` — the curses control panel (`--list` prints a plain-text dump;
  `--new-session --pane <id>` is the shortcut entry point).
- `VERSION` — the current release version.
- `bin/` — the vendored release script and its ignore list.
- `.sassywares/` — Sassy Skills config and README.
- `tests/` — isolated tmux lifecycle and key-binding regression tests.
- `AGENTS.md` — repository guide for agents and contributors.
- `CONCEPTS.md` — vocabulary used in this project.
- `docs/solutions/` — durable learnings from building this.
- `assets/` — screenshots used in this README.
