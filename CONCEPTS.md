# CONCEPTS.md

Vocabulary used across `sassy-pi`. Use these words in code, commits, and
discussions.

## The layout

**Control panel** — the narrow pane that lists sessions and dispatches actions.
*Avoid: dashboard, sidebar, browser.*

**Session viewer** — the wide pane that shows one session at a time. It is a
slot, not an identity: the pane occupying it changes when sessions are swapped.
*Avoid: target pane, right side.*

**Foreground session** — the session currently occupying the session viewer.
*Avoid: active pane, selected process.*

**Background session** — a live session hidden in the dedicated `sassy-pi-bg`
tmux session, one single-pane window per session. It keeps running when another
session is shown. *Avoid: parked session, suspended session.*

**Managed pane** — a viewer or background pane that sassy-pi controls. Managed
panes carry the `@sassy_session=<id>` pane option, which travels with the pane
and is the authoritative session identity after swaps and restarts.

**Project group** — the set of sessions that share one working directory,
rendered as a single collapsible heading.

## The domain

**Session store** — the on-disk collection of Pi conversations, grouped by
working directory, that the panel reads to build its list.

**Present** — putting a session in the session viewer by swapping its live pane
with the pane currently there. The displaced process moves to the background; it
is not killed or respawned. *Avoid: resume, open, launch.*

**Swap** — the tmux `swap-pane -dZ` operation that exchanges two panes while
their processes, scrollback, and working directories stay intact.

**Reap** — removing a hidden background pane and its window once its work is
done or its Pi process has exited. Reaping never touches the foreground session.

**Refresh grace** — the interval a hidden completed session must stay completed
before it is reaped, so a session file that lags the live process cannot cause a
just-switched-away turn to be killed. *Avoid: delay, timeout.*

**Reload** — the `r` action: typing `/reload` into every managed Pi pane so each
session discovers extensions, skills, templates, themes, keybindings, and
context files again, then refreshing the panel's session list. The Pi processes
stay alive; this is not a restart. It is a deliberate hard refresh and may
interrupt an in-flight turn. *Avoid: restart, respawn.*

**Placeholder** — a bare shell swapped into the session viewer while a live
session is preserved in the background, so the visible window can be removed
without destroying that session.

**Pending turn** — a session whose latest turn is unresolved: the last message
is from the user or a tool, the assistant is at `stopReason=toolUse`, or no
terminal assistant stop reason has been recorded yet.

**Completion** — the latest assistant message ending with `stop`, `error`, or
`aborted`. A hidden completed session is reaped after the refresh grace.

**External process** — a live Pi process for a session that is running outside
the managed viewer/background panes. The panel refuses to present that session
until the external process exits, rather than starting a duplicate.

**Status inference** — deriving a session's state from its last recorded message
and from live processes, rather than from a state Pi pushes.
*Avoid: health, state.*

**Collapse** — hiding a project group's sessions while keeping its heading
visible; the inverse is **expand**. *Avoid: fold/unfold, close/open.*
