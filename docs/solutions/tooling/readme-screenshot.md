---
title: Screenshot the README from a fixture, never from the live panel
category: tooling
tags: [screenshot, fixture, tmux, release, determinism]
created: 2026-11-22
updated: 2026-11-22
---

## Problem

The README's picture — the two-pane cockpit — was a hand-uploaded
user-attachment, so it drifted: the panel gained the `n`/`m`/`x` keys, tiered
`… N more` rows and goal slices, while the image still showed the first layout.
Regenerating it by hand from a real session is worse than stale: it publishes
the user's project names, titles and goals into public git history, and it is
not reproducible, so every run produces a different image and a commit.

## Root cause

A screenshot of a *live* panel is a snapshot of the machine, not of the code:
session titles, working directories, ages, row order, `… N more` counts and the
current selection all come from the user's disk and clock. There is nothing to
diff and nothing to trust.

## Solution

`.agents/skills/sassy-code-shot/` renders the image from a demo store:

1. `fixtures/demo.py` describes projects, sessions, goals and slices as plain
   data, with ages as **offsets in minutes from now** — that one choice makes
   an unchanged panel render byte-identical output, which is what lets the hook
   skip the commit entirely.
2. `capture.py` creates a throwaway tmux server (`-L sassy-shot`,
   `-f /dev/null`, pinned `150x52`) and materialises the fixture into a temp
   store, reached through `SASSY_CODE_STATE_DIR`,
   `PI_CODING_AGENT_SESSION_DIR`, `SASSY_CODE_CLAUDE_SESSION_DIR` and
   `SASSY_CODE_PROCS_FIXTURE`.
3. `ansi2svg.py` turns `capture-pane -p -e` into a text SVG: no browser, no
   image library, nothing but the standard library. Every run carries an
   explicit `textLength`, so alignment survives whatever font the reader has.
   It also draws the window around the panes — titlebar and tmux status bar —
   so the README shows the app as it looks on a desktop rather than as a bare
   screen. Those are the two things a pane capture cannot contain.

The last env var earns its place: `● in progress` and `◐ needs input` are
derived from a live agent process in `procs.process_table()`, so a store-only
fixture can never show the panel's most important state. The file just replaces
the `ps` snapshot.

### Why the hook gates itself

`hooks.push` runs on every push, but the shot is a *release* artifact: it must be
inside the tag. So `after_push: [sassy-code-shot, sassy-code-release]`, and the
shot gates on the same rule `bin/release` uses — a consumer-facing change since
the highest `v*` tag on origin, with the same `bin/release-ignore` patterns.
Without the gate, the shot's own commit under `assets/` (consumer-facing) would
make every push cut a release of its own.

## Traps worth remembering

- `capture-pane -e` writes reverse video as real colours, so the highlight must
  be detected from the SGR codes, not from the plain text.
- Reverse video of default colours is *not* an index swap: swapping the palette
  indexes leaves `fg == bg == default`, and the highlight disappears. Resolve
  the two hex colours first, then swap them (`Style.colours`).
- A run's trailing padding must be trimmed before its `textLength` is computed
  with `lengthAdjust="spacingAndGlyphs"`, or the padding is stretched and the
  text smears across the pane.
- The divider's colour is a hex string, not a palette index; a numeric-only
  `_hex()` silently drops it (a test now pins this).
- `tmux start-server` exits at once unless `exit-empty` is off, and
  `default-terminal` has to be set before the first pane spawns — curses reads
  `TERM` once.
- A pane capture has no status bar, so `status_line()` rebuilds it from tmux's
  own formats (`#{E:status-left}`, `#{E:window-status-format}`) and the guard
  that matters is `status-right`: leave tmux's default and the clock lands in
  the image, breaking byte-stability. `status-left-length` matters too — the
  launcher widens it for `sassy-code`, and the default 10 clips the name into
  the window list.
- Palette 0–15 is the terminal's default style, not the xterm defaults: the
  image has to look like the app in a fresh terminal. Dim is a fade towards the
  background (`DIM_KEEP`), the way a terminal renders `A_DIM`, not a fixed grey.
- The panel starts with the highlight on the first row (a project heading).
  `select_session()` walks it onto the presented session with `Down`, checking
  for reverse video, so the shot reads the way `present` leaves the panel.

## Tests

- `python3 -m unittest tests.test_shot_render` — the renderer, loaded by path
  from the skill (trailing padding, reverse, wide glyphs, xterm 256, divider).
- `bash tests/shot-gate.sh` — the real `ship.sh` against a throwaway bare
  origin: docs-only and fixture-only changes are refused, a `panel/` change
  opens the gate, a reproducible capture commits nothing, and a real change
  commits the SVG alone and is idempotent on a second run.
