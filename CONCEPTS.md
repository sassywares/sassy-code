# CONCEPTS.md

Vocabulary used across `sassy-pi`. Use these words in code, commits, and
discussions.

## The layout

**Control panel** — the narrow pane that lists sessions and dispatches actions.
*Avoid: dashboard, sidebar, browser.*

**Session viewer** — the wide pane that hosts exactly one resumed session at a
time. *Avoid: target pane, right side.*

**Project group** — the set of sessions that share one working directory,
rendered as a single collapsible heading.

## The domain

**Session store** — the on-disk collection of Pi conversations, grouped by
working directory, that the panel reads to build its list.

**Resume** — replacing the session viewer's process with a Pi invocation bound
to a chosen session, so its conversation continues in place.

**Status inference** — deriving a session's state from its last recorded message
and from live processes, rather than from a state Pi pushes.
*Avoid: health, state.*

**Collapse** — hiding a project group's sessions while keeping its heading
visible; the inverse is **expand**. *Avoid: fold/unfold, close/open.*
