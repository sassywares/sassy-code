# CONCEPTS.md

Vocabulary used across `sassy-code`. Use these words in code, commits, and
discussions.

## The layout

**Control panel** — the narrow pane that lists sessions and dispatches actions.
*Avoid: dashboard, sidebar, browser.*

**Session viewer** — the wide pane that shows one session at a time. It is a
slot, not an identity: the pane occupying it changes when sessions are swapped.
*Avoid: target pane, right side.*

**Foreground session** — the session currently occupying the session viewer.
*Avoid: active pane, selected process.*

**Background session** — a live session hidden in the dedicated `sassy-code-bg`
tmux session, one single-pane window per session. It keeps running when another
session is shown. *Avoid: parked session, suspended session.*

**Managed pane** — a viewer or background pane that sassy-code controls. Managed
panes carry the `@sassy_session=<id>` pane option, which travels with the pane
and is the authoritative session identity after swaps and restarts.

**Panel tag** — the `@sassy_panel=1` pane option the control panel puts on its
own pane, so a launcher shortcut can find the panel of the current window
without trusting pane positions. Only a pane whose foreground process is Python
counts as a live panel.

**Project group** — the set of sessions that share one working directory,
rendered as a single collapsible heading.

## The domain

**Session store** — the on-disk collection of conversations, grouped by working
directory, that the panel reads to build its list. There is one store per
harness: Pi's `~/.pi/agent/sessions/` and Claude Code's `~/.claude/projects/`.

**Harness** — the agent runtime that owns a session and its transcript format.
Supported harnesses are **Pi** and **Claude Code**; each has its own resume
command (`pi --session`, `claude --resume`) and terminal stop reasons. *Avoid:
tool, agent type, provider.*

**Harness mark** — the small glyph drawn before a session title: `π` for Pi and
`✳` for Claude Code. It identifies the harness without adding a second column.
*Avoid: icon, logo, badge.*

**Present** — putting a session in the session viewer by swapping its live pane
with the pane currently there. The displaced process moves to the background; it
is not killed or respawned. A session that is already presented is focused, not
started again. *Avoid: resume, open, launch.*

**Swap** — the tmux `swap-pane -dZ` operation that exchanges two panes while
their processes, scrollback, and working directories stay intact.

**Reap** — removing a hidden background pane and its window once its work is
done (no open background tasks) or its agent process has exited. Reaping never touches the foreground session.

**Refresh grace** — the interval a hidden completed session must stay completed
before it is reaped, so a session file that lags the live process cannot cause a
just-switched-away turn to be killed. *Avoid: delay, timeout.*

**Reload** — the `r` action: typing `/reload` into every managed **Pi** pane so
each session discovers extensions, skills, templates, themes, keybindings, and
context files again, then refreshing the panel's session list. Claude Code has
no equivalent, so its panes are left alone. The Pi processes stay alive; this is
not a restart. It is a deliberate hard refresh and may interrupt an in-flight
turn. *Avoid: restart, respawn.*

**Pending turn** — a session whose latest turn is unresolved: the last message
is from the user or a tool, the assistant is at `stopReason=toolUse` /
`stop_reason=tool_use`, or no terminal assistant stop reason has been recorded
yet.

**Completion** — the latest assistant message ending with a terminal stop
reason: `stop`, `error`, or `aborted` for Pi; `end_turn`, `stop_sequence`,
`max_tokens`, or `refusal` for Claude Code. A hidden completed session is reaped
after the refresh grace.

**External process** — a live agent process for a session that is running
outside the managed viewer/background panes. The panel refuses to present that
session until the external process exits, rather than starting a duplicate.

**In-process switch** — Pi replacing the session inside the same process
(`/handoff`, `/fork`, `/clone`), so the pane's command line and pane option
still name the session it was started with. The successor's header records the
old session file as `parentSession`; the panel follows that link and re-tags the
pane with the successor. *Avoid: reopen, relaunch.*

**Successor** — the session a pane now runs after an in-process switch. It is
identified by `parentSession` plus a header timestamp later than the previous
session's last activity, never by the pane's command line alone. A handoff from
a handoff can record the session the Pi process was *started* with as
`parentSession`, so two successors may be **siblings** of the same root rather
than a parent/child chain; the panel follows the newest unclaimed session in
that shared **handoff lineage**.

**Status inference** — deriving a session's state from its last recorded message
and from live processes, rather than from a state Pi pushes.
*Avoid: health, state.*

**Collapse** — hiding a project group's sessions while keeping its heading
visible; the inverse is **expand**. *Avoid: fold/unfold, close/open.*

**Project name** — the readable label for a group's working directory: `root`
for home, otherwise the folder's own name (`tap2-workspace`), grown with parent
folders only when two projects share a name (`a/app`, `b/app`). The full cwd
still keys grouping and collapse state. *Avoid: path, shortened path.*

**Session limit** — the cap on top-level items drawn under one project
(`SESSION_LIMIT`, currently 5; a goal counts as one item) before a `… N more`
row, whose N counts hidden items. Expanding reveals the rest and relabels that
row `… show fewer`; a **running** session (or a goal holding one) is never
hidden behind the limit. *Avoid: pagination, truncation.*

**Goal limit** — the second cap, on sessions drawn under one expanded goal
(`GOAL_SESSION_LIMIT`, currently 3) before the goal's own `… N more` row, whose
N counts hidden sessions (not items). It expands and collapses independently of
the project's row, and a running session is never hidden behind it either.

**Goal** — a user-created, named folder inside one project that groups the
sessions of one feature. One level deep (project → goal → sessions), expands
and collapses like a project, and persists in `goals.json` under the state
directory. Panel state only: Pi and Claude Code never know about goals. An empty
goal shows a dim hint line that is not a cursor stop. *Avoid: folder, epic,
workspace.*

**Done goal** — a goal marked finished with `x`: it shows `✓` after its arrow,
is collapsed, and sorts below the project's active goals and loose sessions
(still one item toward the **session limit**). `x` again reopens it in recency
order and expands it; starting a session in it with `n` also reopens and
expands it. Deleting a goal (`D`, confirmed) is separate: its sessions become
loose. *Avoid: archived, closed.*

**Harness picker** — the small boxed list `n` opens on a project heading or
goal row when more than one harness is installed: one line per installed harness
(glyph + label), preselected on the inferred harness; `j`/`k`/arrows move,
`1`–`9`/`Enter` pick, `Esc`/`q` cancel. Session rows and the `N` prefix shortcut
skip it and use the inferred harness. *Avoid: harness menu, dialog.*

**Command line** — the `:` prompt at the bottom of the panel. The first token is
the command (`goal:create`, `goal:rename`) and the rest is its argument, so a goal name may
contain `:`. Unlike `/`, which only filters, it changes state; refusals appear
as a footer message. *Avoid: palette, ex mode.*
