---
title: Patch the owner and read it at call time, or the patch never lands
category: python
tags: [testing, mocking, module-split, facade, defaults, globals]
created: 2026-11-22
---

## Context

The panel was one module, so a test could patch anything as
`mock.patch.object(panel, "process_table", ...)` and every caller saw it: there
was one module namespace. After the split, `panel/__init__.py` re-exports names
that are *defined* in `panel.core`, `panel.procs`, `panel.panes` and the rest.
A re-export is a second binding, not a move, and it is the reader that decides
which binding matters.

## Guidance

A monkeypatch only works when the code under test reads the patched name at call
time, from the module that owns it. The split broke that in three ways at once;
each produced a green-looking test that exercised the real thing.

- **Patch the owner.** `mock.patch.object(panel, "migrate_state")` rebinds the
  facade copy, which nothing calls. The real `state.migrate_state()` then runs
  and moves the developer's `~/.sassy-code-current`; the same mistake with
  `set_panel_tag` writes `@sassy_panel` onto the developer's live pane. Patch
  the definition site: `panel.state.STATE_DIR`, `panel.procs.process_table`,
  `panel.tmuxctl.tmux`. `panel/__init__.py` states this; the docstring is the
  contract, not a hint.
- **The reader must read the owner at the use site.** A module that does
  `from panel.state import STATE_DIR` freezes a copy at import and cannot follow
  a patched owner. Read `_state.STATE_DIR` / `core.SESSION_LIMIT` in the body.
  The exception is a pure function imported for its behaviour; data and mutable
  caches are never safe to copy.
- **Never capture a patched constant in a default argument.**
  `def rows_for(..., limit=SESSION_LIMIT)` and `send=send_reload` bind at import,
  so patching the owner is silently ignored and the test that appears to drive
  the option is vacuous. Late-bind (`limit=None`, then
  `core.SESSION_LIMIT if limit is None else limit`) as `rows_for()` does.
- **One owner per mutable global.** A set written from two modules makes every
  write site unretargetable: the patch lands on one name and the other write
  still moves the unpatched set. `_ATTENTION` lives in `panel/core.py` with its
  accessors, and `panes.reap_hidden()` and `status` call `core.mark_attention()`
  / `core.attention_active()` rather than touching the set.
- **Keep the facade's incidental pass-throughs.** Tests read `panel.curses` and
  `panel.os` only because a wildcard re-export carried the owner's stdlib
  imports into the package namespace. An `__all__` in any owner module would
  remove those patch points without a single test failing loudly.

The sweep after any split must cover more than `patch.object`: `patch.multiple`,
`patch.dict`, bare `panel.X = ...` rebinds, and string targets passed to
`mock.patch` all name a module.

## Applicability

This applies to any re-exporting package, façade, or `__init__` that flattens
submodules, and to refactors that move a function between modules. It does not
apply to a module-private helper defined and used in one module — patch it where
it lives and read it directly. When a test must exercise behaviour rather than a
seam, prefer patching the owner's data (`STATE_DIR` to a temp dir) over patching
the function that reads it.
