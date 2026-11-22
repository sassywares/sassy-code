---
name: sassy-code-shot
description: Regenerate the README screenshot (`assets/sassy-code.svg`) from a demo store and commit it when it changed. Use as the sassy-ship hooks.push after_push hook, before sassy-code-release, and when asked to refresh the README image or the demo fixture. Internal Sassywares tooling.
metadata:
  internal: true
---

# sassy-code-shot

Keeps `assets/sassy-code.svg` — the picture under the README title — current,
without ever publishing a real session and without churning git.

## Rules

- **Always a fixture, never a live panel.** Real titles, project paths and goals
  would be in public history forever; ages, row order and `… N more` counts are
  not reproducible; and the live path would write the user's own state. The
  capture builds a throwaway tmux server and a temp store instead.
- **Releases only.** The hook runs after every push, so it self-gates with the
  same rule `bin/release` uses: a consumer-facing change since the highest `v*`
  tag on origin, with the same `bin/release-ignore` patterns. Its own commit
  touches `assets/`, which is consumer-facing, so an ungated refresh would
  release itself.
- **Before the release.** `after_push: [sassy-code-shot, sassy-code-release]`,
  so the tag carries the image. Reversing the order strands the refresh one
  commit after the tag.
- **Byte-stable.** Ages are offsets from now, so an unchanged panel renders an
  identical file and nothing is committed. Never introduce "today", a PID, a
  hostname or dict iteration order into the output.
- **A screenshot never blocks a release.** Every failure warns and exits 0;
  `--strict` exists for a person watching, not for the hook.
- **Never edit the SVG by hand** and never commit a bitmap over it: it is text
  so a panel change reads as a diff.

## Steps

Run from anywhere; the scripts locate the repo themselves.

1. `scripts/ship.sh` in the default `ship` mode. It gates, renders into a temp
   file, and only on a real difference does it write `assets/sassy-code.svg`,
   commit `docs: refresh the README screenshot` (that path alone) and push the
   current branch. Report the one-line outcome it prints ("nothing to release",
   "is up to date", "refreshed …", or a warning).
2. On a warning, relay it verbatim and carry on: the release must still run.
   `bin/release` pushes main afterwards, which carries an unpushed shot commit.

| command | does |
|---|---|
| `scripts/ship.sh` | gate, render, commit and push on a real change |
| `scripts/ship.sh render` | rewrite `assets/sassy-code.svg`; no git |
| `scripts/ship.sh check` | exit 1 when the committed image is stale |
| `scripts/capture.py --plain` | print both panes as text (debugging) |
| `scripts/capture.py --keep` | leave the throwaway tmux server and store up |

`render` then `qlmanage -t -s 1400 -o /tmp assets/sassy-code.svg` renders a PNG
to eyeball (Quick Look scales wide SVGs oddly; the SVG geometry is authoritative).

## How it works

`capture.py` builds a throwaway tmux server (`-L sassy-shot -f /dev/null`,
150×52), materialises `fixtures/demo.py` into a temp store, and starts a
two-pane window: a 54-column left pane running `python3 -m panel --target
<viewer>` and a right pane running `viewer.py` (a mocked Pi transcript, plain
text so no network or model is involved). It moves the highlight onto the
session the viewer shows, then renders both panes from `capture-pane -p -e`
through `ansi2svg.py`.

The image is a 1440×890 macOS terminal window, not a floating wall of text:
`ansi2svg.render()` draws the titlebar (traffic lights, the window title) and
the tmux status bar around the two panes. A pane capture never includes the
status bar, so `status_line()` rebuilds it from tmux's own formats
(`#{E:status-left}`, `#{E:window-status-format}`), honouring the same
`status-left-length` the launcher widens, with `status-right` pinned empty so
no clock can reach the file. Colours 0–15 are the terminal's default palette
and dim text is a fade towards the background, so the shot looks like the app in
a fresh terminal rather than in a theme of its own.

The panel pane is 36% of the width, wider than the launcher's `22%` default:
at 150 columns a 33-column panel truncates nearly every title, and the shot's
job is to show what the rows say.

The store is redirected with four env knobs, which exist for this skill:
`SASSY_CODE_STATE_DIR`, `PI_CODING_AGENT_SESSION_DIR`,
`SASSY_CODE_CLAUDE_SESSION_DIR` and `SASSY_CODE_PROCS_FIXTURE`.
`SASSY_CODE_PROCS_FIXTURE` is the one that matters most: `● in progress` and
`◐ needs input` require a live agent process, which a fixture store cannot have
on its own.

## Changing the demo

Edit `fixtures/demo.py`, keep the cast honest to what the panel does, and
`render` to look at it. A good demo shows: both harness glyphs (`π`, `✳`), all
five status marks, a goal with slices, a collapsed container and an expanded one
with a `… N more` row, a `…`-truncated title, and one highlighted live session —
which is also the invariant no tier or filter is allowed to hide.
