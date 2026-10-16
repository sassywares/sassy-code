# sassy-pi

A two-pane tmux control panel for Pi sessions.

```
┌──────────────────────┬──────────────────────────────────────────┐
│  Pi sessions — 34    │                                          │
│  ▾ ~  (4)            │                                          │
│   · Is there a way …│        resumed pi session lives here      │
│   · Let's enrich …   │        (press Enter -> focus jumps here)  │
│  ▾ ~/work/tap2 (25)  │                                          │
│   ✗ color picker …   │                                          │
└──────────────────────┴──────────────────────────────────────────┘
        control panel                    active session
```

Session titles are truncated to a single line with an ellipsis; the age is
right-aligned.

## Usage

```bash
sassy-pi
```

- Run outside tmux: creates a `sassy-pi` session with the two panes.
- Run inside tmux: splits the current window and adds the panel to the left.
- `SASSY_PI_WIDTH=25%` overrides the left pane width (default `22%`).

Keys in the left pane: `↑/↓` or `j/k` move, `g/G` jump, `Enter` resumes a
session (and focuses the right pane) or collapses/expands a directory, `←/→`
or `h/l` collapse/expand a directory, `n` new session in the selected project,
`r` refresh, `/` filter (which auto-expands groups), `q` quit. Switch back to
the panel with your tmux prefix + arrow.

## How it works

`sassy-pi` (bash) creates the layout and passes the right pane's tmux pane id
to `panel.py`. `panel.py` (Python, stdlib only) reads Pi's session store,
renders the list with `curses`, and on Enter runs:

```bash
tmux respawn-pane -k -t <right-pane> -c <session-cwd> "pi --session <id>; exec $SHELL"
tmux select-pane -t <right-pane>
```

## Data source

Sessions live under `~/.pi/agent/sessions/--<encoded-cwd>--/<ts>_<id>.jsonl`.
Each file is JSONL:

- first line: `{"type":"session", "id", "cwd", "timestamp", ...}`
- `{"type":"session_info", "name"}` — set by `/name`
- `{"type":"message", "message": {"role", "content", "stopReason", ...}}`

Status is inferred from the last message:

| status | rule |
|---|---|
| running | a live `pi --session <id>` process is in the process table |
| error | last entry is a failed `toolResult` or assistant `error`/`aborted` |
| active | user spoke last, or modified within 5 minutes |
| idle | otherwise |

## Files

- `sassy-pi` — tmux launcher / layout.
- `panel.py` — the curses control panel (`--list` prints a plain-text dump).
- `AGENTS.md` — repository guide for agents and contributors.
- `CONCEPTS.md` — vocabulary used in this project.
- `docs/solutions/` — durable learnings from building this.
