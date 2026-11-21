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
and is the authoritative session identity after swaps and restarts. The tag is
identity, not **liveness**: a pane whose agent has exited is a leftover shell
still carrying the tag, and is not live. *Avoid: tagged pane, agent pane.*

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
yet. Background work the harness is still running does not make a turn pending;
it keeps the pane from being **reaped**, not the row in progress.

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

**Status inference** — deriving a session's five-state status from its transcript,
live processes and in-memory attention, rather than from a state a harness
pushes. `in progress` means a live process with an unresolved turn; `needs
input` means a live process whose turn is terminal (or has not started), a
pending Claude `AskUserQuestion`, or an attention mark on a hidden session that
finished; `idle` means no live process and no attention; `error` means the last
entry failed; `done` is a user mark. Precedence is
`error > done > in progress > needs input > idle`. A live session is never
idle. *Avoid: health, state, active, running.*

**Collapse** — hiding a project group's sessions while keeping its heading
visible; the inverse is **expand**. *Avoid: fold/unfold, close/open.*

**Project name** — the readable label for a group's working directory: `root`
for home, otherwise the folder's own name (`tap2-workspace`), grown with parent
folders only when two projects share a name (`a/app`, `b/app`). The full cwd
still keys grouping and collapse state. *Avoid: path, shortened path.*

**Session limit** — the base cap on top-level items drawn under one project
(`SESSION_LIMIT`, currently 5; a goal counts as one item even with slices and
sessions inside it) before a `… N more` row, whose N counts hidden items.
Revealing doubles the increment each tier (+10, +20, +40), lifting the cap 5,
15, 35, 75…; `]` expands a collapsed container at the base tier and then reveals
one tier per press, while `[` hides one tier and then collapses at the base
tier. Activating `… N more` reveals the next tier; the row becomes
`… show fewer` once everything is shown, staying visible so activating it (or
`[`) can shrink the list one tier. An **in progress** session (or a goal or
slice holding one) is never hidden behind the limit, even when it sits beyond
the current tier. *Avoid: pagination, truncation.*

**Goal limit** — the second and third base caps, on children drawn under an
expanded **goal** and sessions drawn under an expanded **slice** (both 3, from
`GOAL_SESSION_LIMIT` and `SLICE_SESSION_LIMIT`) before that container's own
`… N more` row. A goal's children are its direct sessions and its slices counted
together; a slice's are its sessions. Each tiers the same way independently of
the project's row (3, 9, 21…), one level per `[`/`]` press: `]` expands then
reveals, `[` hides then collapses. Activating its `… show fewer` row hides one
tier. An in-progress session is never hidden behind either limit.

**Goal** — a user-created, named container inside one project that groups the
sessions of one feature. Two levels: project → goal → **slice** → sessions.
A goal's count, age and sort position cover its whole subtree; its rollup mark
is the worst descendant state (`✗` > `●` > `◐`) when collapsed, with `✓` when
the goal itself is done. Goals expand and collapse like a project and persist in
`goals.json` under the state directory, with an optional `parent` (missing or
unknown means top-level). Panel state only: Pi and Claude Code never know about
goals. An empty goal shows a dim hint line that is not a cursor stop. *Avoid:
folder, epic, workspace.*

**Slice** — a named child of a **goal**, one more level (project → goal →
slice → sessions). It holds sessions directly, has its own three-session limit,
collapse state and done mark, and cannot contain another slice. Names are unique
among siblings under the same parent only, so two goals may each have a
`Phase 2`. Deleting a slice flattens its sessions loose; deleting its goal
removes the slice too and flattens every session in the subtree. An empty slice
gets the same dim hint as an empty goal. *Avoid: subgoal, phase, step, child
goal.*

**Done goal / done slice** — a goal or slice marked finished with `x`: it shows
`✓` after its count, is collapsed, and sorts below its active siblings (a goal
still counts as one item toward the **session limit**). `x` again reopens it in
recency order and expands it; starting a session in it with `n` also reopens and
expands it. Deleting it (`D`, confirmed) is separate. *Avoid: archived, closed.*

**Done session** — a session marked finished with `x`, stored by session id in
`sessions.json` under the state directory (not in the transcript). It shows
`✓`, dims the whole row, and joins done goals and slices in one sunk band ordered
by recency. New activity or `x` again revives it. A corrupt or wrong-shape
`sessions.json` hides the marks, warns in the footer, refuses edits, and is
never overwritten. *Avoid: archived, hidden, deleted.*

**Attention** — an in-memory, per-session mark that a hidden session finished
its turn and is waiting for the user. It keeps the row on `needs input` after
the pane is reaped, is cleared when the session is presented or starts a new
turn, and is lost on restart; it never keeps a process alive. *Avoid:
notification, unread.*

**New-session box** — the boxed picker `n` opens, with no section headings. It
holds the contextual `+ New goal` or `+ New slice` item (nothing on a slice
row), then one line per installed harness (glyph + label), preselected on the
inferred harness. A divider rule separates the contextual item from the
harnesses; the ASCII `+` plus one space sits in the harness index column and
the label in the glyph column. `j/k`/`↑`/`↓` move across both parts (`↑` from
the first harness returns to the contextual item, `↓` from it lands on the
first harness), `Tab` switches between them, `←`/`→` do nothing, `1`–`9`/`Enter`
pick, and `Esc`/`q`/`Ctrl-C` cancel. A harness item starts at once; the
contextual item runs harness → name → create → start. Project and goal rows
always show the box, slice rows only with more than one harness; session rows
and the `N` prefix shortcut skip it and use the inferred harness. *Avoid:
harness menu, dialog, new-session dialog.*

**Move** — re-filing a session with `m` into a goal or slice, or back to
**loose** (`⌂ Top level`). The picker lists `⌂ Top level` first (no goal, i.e.
loose), `+ New goal` second (creates a top-level goal and moves the session
into it), then a divider rule, then every goal and slice as a tree (`├`/`└`
connectors under the parent goal); the marker column holds `⌂` on the loose
row and a `✓` on the container the session is in now (never done), with the
words in the name column; done goals and slices are dimmed. A move
stays inside one project; only sessions move, not goals or slices. Moving into a done goal or
slice reopens and expands it, and the session keeps running. Explicit loose
membership is stored per project in `goals.json` and wins over inherited
membership: a Pi successor of a moved-out session stays loose rather than
rejoining its ancestor's goal. *Avoid: reassign, transfer.*

**Mouse** — the pointer path through the panel: a **click** selects a row; a
**double-click** (a second click on the same session row within about 0.4
seconds) **presents** it; a click on a project heading, **goal**, or **slice**
toggles it; a click on a `more` row steps one tier; the **wheel** moves the
highlight one row per notch, with duplicate events from one notch debounced; a
click on a hint or gap line does nothing. Wheel-up works on every curses
build; wheel-down needs a build with 5-button (`BUTTON5_*`) support and is
inert on the macOS system ncurses, which has only four buttons. No modifier
bit is ever read as a wheel notch.
A **drag-to-move** presses on a session, shows `moving <title>…` in the footer
while held, and releasing on a goal or slice in the same project **moves** the
session there, on that project's heading makes it **loose**, and anywhere else
cancels. The **new-session box** and **Move** box accept a click to pick and a
click outside to cancel. Hold `Shift` to let the terminal select text instead.
*Avoid: tap, scroll, drop.*

**Command line** — the `:` prompt at the bottom of the panel. The first token is
the command (`goal:create`, `goal:rename`, `slice:create`, `slice:rename`) and
the rest is its argument, so a name may contain `:`. Unlike `/`, which only filters, it changes state; refusals appear
as a footer message. *Avoid: palette, ex mode.*
