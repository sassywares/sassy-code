# CONCEPTS.md

Vocabulary used across `sassy-code`. Use these words in code, commits and
discussions; mechanics live in [`docs/architecture.md`](docs/architecture.md).

## The layout

**Control panel** — the narrow pane that lists sessions and dispatches actions.
*Avoid: dashboard, sidebar, browser.*

**Session viewer** — the wide pane showing one session. It is a slot, not an
identity: the pane occupying it changes when sessions are swapped. *Avoid:
target pane, right side.*

**Foreground session** — the session in the viewer. **Background session** — a
live session hidden in the dedicated `sassy-code-bg` tmux session, one
single-pane window each; it keeps running while another session is shown.
*Avoid: active/parked/suspended.*

**Managed pane** — a viewer or background pane, carrying `@sassy_session=<id>`:
identity, not **liveness** — the tag outlives the agent. *Avoid: tagged pane.*

**Panel tag** — `@sassy_panel`, which the control panel puts on its own pane so
a launcher shortcut can find it without trusting pane positions.

**Footer** — the panel's own bottom line (hints, prompts, refusals). An entity
footer shows only the highlighted **project**, **session** or goal/slice
actions, plus `⏎ more`/`⏎ less` on a tier row; global, tmux and mouse bindings
live in the **keys menu**, and the box keys stay in the README table. Never the
**status line**: the bar tmux draws for the session (at the bottom by default),
of which `status-left` is only the left field. The launcher pins nothing there
except the length of `status-left`, so the session name is not clipped.

**Keys menu** — the `?` box listing the global, tmux and mouse bindings,
rendered from `panel/keymap.py` in Global, Tmux and Mouse sections; a long
action wraps instead of clipping. It is a supplement, not a duplicate: entity
actions stay in their footers, and the box keys stay in the README table. A
hidden alias (`j`/`k`, `g`/`G`/`Home`/`End`, `h`/`l`/`←`/`→`) still works
without being listed. *Avoid: help screen, cheatsheet, shortcuts.*

**Project group** — the sessions sharing one working directory, rendered as one
collapsible heading. Its **project name** is `root` for home, else the folder's
own name (`tap2-workspace`), grown with parent folders when two share it
(`a/app`); the full cwd keys grouping and collapse. *Avoid: path, shortened
path.*

## The domain

**Session store** — the on-disk conversations, grouped by working directory,
that the panel reads. One per harness: Pi's `~/.pi/agent/sessions/` and Claude
Code's `~/.claude/projects/`.

**Harness** — the agent runtime that owns a session and its transcript format:
**Pi** or **Claude Code**, each with its own resume command and terminal stop
reasons. *Avoid: tool, agent type, provider.*

**Harness mark** — the glyph before a session title: `π` Pi, `✳` Claude Code.
It identifies the harness without a second column. *Avoid: icon, logo, badge.*

**Present** — put a session in the viewer with a **swap** (`swap-pane -dZ`),
which exchanges two panes and leaves their processes, scrollback and working
directories intact. The displaced process moves to the background; an
already-presented session is focused, not started again. A session already
running outside managed panes is an **external process** and is refused until it
exits. *Avoid: resume, open, launch.*

**Reap** — remove a hidden background pane and window once its work is done or
its agent exited; never the foreground session. The **refresh grace** is how
long a hidden completed session must stay completed first, so a lagging file
cannot kill a just-switched-away turn. *Avoid: delay, timeout.*

**Reload** — the `r` action: typing `/reload` into every managed **Pi** pane so
it re-reads extensions, skills, templates, themes, keybindings and context
files. Claude Code has no equivalent; processes stay alive, but an in-flight turn
may be interrupted. *Avoid: restart, respawn.*

**Pending turn** — a session whose latest turn is unresolved: the last message
is from the user or a tool, or the assistant is still working. The turn is
**complete** when the latest assistant message carries a terminal stop reason.
Background work does not make a turn pending; it only blocks **reaping**. How
the states are inferred:
[`docs/architecture.md`](docs/architecture.md#data-flow).

**In-process switch** — Pi replacing the session inside the same process
(`/handoff`, `/fork`, `/clone`), so the pane still names the old session. The
**successor** is the session it now runs, identified by the old file recorded as
`parentSession` plus a later header timestamp, never by the command line alone.
Two successors may be **siblings** of one root rather than a chain; the panel
follows the newest unclaimed session in that **handoff lineage**. *Avoid: reopen,
relaunch.*

**Status inference** — deriving one of five states from the transcript, live
processes and in-memory attention, rather than from a harness-pushed state; a
live session is never idle. *Avoid: health, state, active, running.*

**Tier limit** — the base cap on children at each level, revealed one tier at a
time with its own `… N more` row. The caps and the reveal ladder are in
[`docs/architecture.md`](docs/architecture.md#tiers). *Avoid: pagination,
truncation.*

**Goal** — a named container inside one project grouping the sessions of one
feature. Two levels: project → goal → **slice** → sessions. Its count, age and
rank cover the whole subtree; collapsed, it shows the worst descendant mark
(`✗` > `●` > `◐`), plus `✓` when it is done. Persists in `goals.json`; harnesses
never know about goals. *Avoid: folder, epic, workspace.*

**Slice** — a named child of a **goal**, holding sessions directly, with its own
limit, collapse state and done mark; it cannot contain another slice. Names are
unique among siblings under the same parent only. Deleting a slice trashes its
sessions and removes only the slice; deleting its goal removes the slice too and
trashes the whole subtree. *Avoid: subgoal, phase, step, child goal.*

**Collapse** — hide a group's or container's children but keep its row visible;
the inverse is **expand**. *Avoid: fold/unfold, close/open.*

**Done goal / done slice / done session** — marked with `x`: `✓`, sunk below
active siblings, and for containers collapsed. A done session is dimmed and
stored by id in `sessions.json` (never in the transcript). New activity, another
`x`, or starting a session in a done container revives it. *Avoid: archived,
hidden, deleted.*

**Delete** — `D` on a session stops its managed agent, moves the transcript to
the **trash**, and removes its done mark and goal membership. `D` on a goal or
slice is a **cascade**: every explicit and inherited session in the subtree goes
to the trash and then the containers are removed, all-or-nothing, so one busy,
external or unknown member refuses the whole delete. A session delete refuses the
same way, and the stop is `SIGTERM` only, never `SIGKILL`. *Avoid: remove,
destroy, archive.*

**Trash** — the recoverable store behind `t`: `trash/index.json` plus
`trash/files/`, holding the original path, title, harness, project, goal, done
mark and deletion time. The picker groups entries by project under a project
header, with a divider rule between groups (never above the first), newest
first inside each group, with aligned title, harness and age columns and a
rule-separated action line for restore and purge. **Restore** moves the transcript back and
reapplies those records; **purge** drops the entry and transcript for good;
entries older than seven days are purged on start. A corrupt index hides the
entries and refuses edits, like the other state stores. *Avoid: recycle bin,
graveyard, archive.*

**Attention** — an in-memory per-session mark that a hidden session finished its
turn. It keeps the row on `needs input` after the pane is reaped, clears when
presented or when a new turn starts, is lost on restart, and never keeps a
process alive. *Avoid: notification, unread.*

**New-session box** — the boxed picker `n` opens: the contextual `+ New goal` /
`+ New slice` item, then one line per installed harness. A harness item starts
at once; the contextual item asks for harness, then name. *Avoid: harness menu,
dialog.*

**Move** — re-filing a session with `m` into a goal or slice, or back to
**loose** (`⌂ Top level`). Moves stay inside one project; only sessions move. The
session keeps running, and a done target reopens and expands. Explicit loose
membership in `goals.json` wins over inherited membership. *Avoid: reassign,
transfer.*

**Mouse** — hit-tested clicks select; a second click **presents**; a
press-and-release **moves**. Gestures: [`README.md`](README.md#mouse).

**Command line** — the `:` prompt. The first token is the command
(`goal:create`, `goal:rename`, `slice:create`, `slice:rename`,
`session:rename`) and the rest its argument, so a name may contain `:`. Unlike
`/`, which only filters, it changes state; refusals appear in the footer.
*Avoid: palette, ex mode.*

## The repository

**README shot** — the image at the top of the README, rendered from a demo
fixture rather than a live panel: it publishes nothing real and re-renders
byte-identical, so it is refreshed only when a release is due. *Avoid:
screenshot, capture, hero image.*
