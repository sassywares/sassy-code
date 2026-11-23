# AGENTS.md

Guide for agents and contributors: the rules that must not break. The system is
in [`docs/architecture.md`](docs/architecture.md); the words are in
[`CONCEPTS.md`](CONCEPTS.md).

## Layout

| Path | Role |
|---|---|
| `main` | Bash launcher. Builds the `sassy-code` session and layout, starts the panel with the viewer pane id, owns the tmux key bindings. |
| `panel/` | The stdlib-only curses package. `__init__.py` is the read-only facade; `__main__.py` is the `-m` shim; `main.py` holds `App` and `main()`; `keymap.py` is the one binding table and `help.py` the `?` menu. |
| `docs/architecture.md` | The one system picture: how it works. |
| `docs/solutions/` | Durable learnings and past bugs. Read the relevant entry before re-investigating a problem. |
| `CONCEPTS.md` | Vocabulary. Read before adding or renaming a user-facing term. |
| `README.md` | User-facing install, keys, mouse and data source. |
| `VERSION` | Current release version; see Releasing. |
| `bin/` | `release` (vendored release script) and `release-ignore`. |
| `.sassywares/` | Sassy Skills config and README (project-agnostic metadata). |
| `.agents/skills/sassy-code-release/` | Project skill that runs `bin/release`. |
| `.agents/skills/sassy-code-shot/` | Project skill that regenerates `assets/sassy-code.svg` from a demo store and commits it when it changed. |
| `assets/sassy-code.svg` | The README screenshot. Text, not a bitmap: a panel change shows up as a readable diff. Never edit by hand. |
| `tests/` | Isolated tmux tests (`tmux -L`, `-f /dev/null`), unit tests and key-binding regressions. |

## How to run

Requires `tmux`, `python3`, bash and at least one of `pi` / `claude` (see
[README § Requirements](README.md#requirements)); `main` self-locates.

```bash
./main                                      # or the `sassy-code` shell alias
python3 -m panel --list                     # plain-text dump, no TUI
```

## Architecture rules

The module map and import graph are in
[`docs/architecture.md`](docs/architecture.md#modules). Enforce them:

- **Import direction is one-way.** The graph in architecture is the contract:
  no cycles, nothing imports upward, and only `__main__.py` imports `main`.
- **The facade is for reads only.** `panel/__init__.py` re-exports every non-app
  module, but ownership does not move: patch a mutable name at its owner
  (`panel.state.STATE_DIR`, `panel.procs.process_table`) and read it through the
  owner at the use site (`core.X`, `_state.X`), never in a default argument.
  App-layer names never enter the facade, and an `__all__` in an owner module
  would break the stdlib pass-through tests read (`panel.curses`, `panel.os`).

## Rules that must not break

- **Mouse is a second way in, never a change to keys.** Reporting is enabled by
  `main_curses()` and restored on exit; without it the panel behaves exactly as
  before. Go through `hit_test()`, `mouse_kind()`, `click_kind()` and the drawn
  hit map, never by guessing row numbers; leave `Shift`-drag to the terminal.
- **Never hide an in-progress session.** No tier, filter, collapse or rollup may
  hide a live `in progress` row; a container holding one counts as in progress.
- **Dense projects are tiered, not truncated.** Limits come from the tier map
  and `[`/`]` move exactly one level per press; the formula and `more`-row rules
  are in [`docs/architecture.md`](docs/architecture.md#tiers).
- **Pane tags are identity, not liveness.** `@sassy_session=<id>` is
  authoritative after swaps and restarts, so re-query the viewer slot and never
  cache pane ids. Liveness also needs an agent descendant (`pane_is_live()`),
  and an empty process snapshot is unknown, not dead: fall back to the tag-only
  answer. Resolve a switched pane from tag *and* command line
  (`pane_switch_target()`), never the command line alone.
- **Actions are tmux commands from the panel.** Never block on the viewer pane;
  swap panes and focus the viewer. **Never `respawn-pane -k`** a managed
  session: that kills in-flight work.
- **Shortcuts reuse panel actions.** Bindings run
  `python3 -m panel --new-session --pane #{pane_id}`, which finds the
  `@sassy_panel` pane and sends its no-picker `N` key; never reimplement spawn,
  swap or state handling in a binding, and never overwrite a user's key.
- **A binding is a keymap row.** Footers, the `?` menu and the README mirror
  `panel/keymap.py`; change the table, the renderers (`view.footer_items`,
  `help`) and the README `## Keys` table in one commit. Entity footers carry
  only the highlighted row's actions, never a global hint. A key the menu or
  the table hides keeps working and keeps its row (`also` for a hidden alias,
  `menu=False`/`listed=False` for a hidden row); no binding exists only in the
  dispatcher.
- **Background work is lazy.** Only sessions opened through the panel get live
  panes; a working hidden pane survives, a completed one is reaped after the
  refresh grace, and a leftover shell with no agent descendant is removed.
  Visible panes are never auto-reaped.
- **Quitting only detaches.** `q`, Ctrl-C and Ctrl-C in the delete prompt call
  `detach_panel()`, which detaches the client from the panel's session and
  touches nothing else; inside the boxes and `:`/`/` prompts Ctrl-C cancels
  instead. To end it for good: `tmux kill-session -t sassy-code`.
- **Goal inheritance is derived.** `attach_goals()` files a Pi session under the
  goal or slice of its nearest explicitly filed `parentSession` ancestor in the
  same project (any depth, cycle-safe); nothing is written to `goals.json`,
  explicit membership wins, and loose lineages stay loose. `/new` and `/clear`
  land loose; membership is keyed by session id, which survives resume.
- **Stdlib only.** `panel/` relies solely on the Python standard library.
- **Single line per session.** Titles truncate with `…`; the age is
  right-aligned.
- **The session status line names the session in full.** The launcher widens
  `status-left-length` for `sassy-code` so tmux's `status-left` shows the whole
  name before the window list; never leave it at the default clip. The length is
  the bare name plus its two brackets and trailing space; the launcher leaves
  `status-left` unset but pins that length, so a user's longer custom format is
  not widened for them.
- **Narrow panel, wide viewer.** The panel pane is the small one; a change that
  makes the panel wide means the layout is wrong (see `docs/solutions/tmux/`).
- **Harness marks are quiet.** Every session row is `status glyph title age`
  (`π` Pi, `✳` Claude Code); no second badge or column.
- **Harness behaviour is data, not branches.** A new harness is one
  `core.HARNESS` entry (label, glyph, and the native title line type and field
  a rename writes), one parser in `parsers`, and its resume/new commands in
  the `panes` helpers; never scatter `if harness == …` through the panel.
- **Pi-only actions stay Pi-only.** `r` (`/reload`) targets managed Pi panes
  only; Claude Code has no equivalent. Claude transcripts have no session-level
  parent link, so in-process switches are a Pi concept.
- **The README shot is generated from a fixture, never from your sessions.**
  `sassy-code-shot` builds a throwaway tmux server and a temp store, so no real
  title, project path or goal is ever published, and the output is
  byte-reproducible (ages are relative offsets), which is what lets the hook
  skip the commit when nothing changed. It runs on releases only, gated like
  `bin/release` (consumer-facing change since the highest `v*` tag on origin),
  because its own `assets/` commit would otherwise release itself. It runs
  *before* `bin/release` so the tag carries the image.

## Testing

```bash
python3 -m unittest discover -s tests       # every test module
python3 -m unittest tests.test_background   # isolated tmux lifecycle
bash tests/tmux-pane-keys.sh                # key-binding regression
bash tests/tmux-layout.sh                   # narrow-panel geometry, failing panel
bash tests/shot-gate.sh                     # screenshot release gate, throwaway origin
```

The discover run covers the store, row, mouse and move suites plus
`test_sessions`, `test_claude_sessions`, `test_selection`, `test_reload`,
`test_shot_render` (the screenshot renderer, loaded by path from the skill) and
`test_shot_env` (the two env knobs that fixture needs).
Tmux tests use an isolated server (`tmux -L`, `-f /dev/null`). Tmux mouse tests
are impractical, so `tests/test_mouse.py` covers the hit map and dispatch.

## Releasing

`VERSION` holds the current version. Releases go through sassy-ship
(`commit.branch: current`): its `hooks.push` `after_push` hook runs the
`sassy-code-release` skill, which runs the vendored `bin/release` (refresh it
against [sassywares/sassy-release](https://github.com/sassywares/sassy-release)).

- Consumer-facing files changed since the highest `v*` tag on origin → bump
  `VERSION`, commit `chore: release v<version>`, push main, create and push the
  annotated tag. A hand-written minor/major above that tag is tagged as-is and
  must be >= it, or the script stops; a prerelease is tagged as-is, never
  auto-bumped.
- Not consumer-facing: the paths in `bin/release-ignore`; everything else
  counts, including `CONCEPTS.md`, `README.md`, `main`, `panel/` and `assets/`.
- Recovery: run `bin/release` from a clean `main` (idempotent). Report any
  failure and never continue silently. Never release while `README.md`
  describes old behaviour.

## Docs upkeep

A user-visible change updates `README.md` in the **same commit**; a stale
README is a review finding.

| Change | Document |
|---|---|
| A keybinding, mouse gesture, keys-menu row or footer hint | `panel/keymap.py`, its renderer (`view.footer_items`, `help`) and `README.md` § Keys in one commit |
| A key, mouse gesture, prompt, picker, `:` command, env var, dependency, layout, goal/limit/glyph/status mark or session store | `README.md` |
| The README screenshot, its demo fixture or renderer | `.agents/skills/sassy-code-shot/` and [`docs/solutions/tooling/readme-screenshot.md`](docs/solutions/tooling/readme-screenshot.md) |
| Process model, module map, data flow, reaping, state files | `docs/architecture.md` |
| A rule or a must-not-break invariant | `AGENTS.md` |
| A user-facing term added or renamed | `CONCEPTS.md` |
| New or moved file | the Layout table here, and `README.md` § Files |
| A solved problem and its why | `docs/solutions/` |

Cite symbols, not line numbers: line anchors drift. Never put a literal version
number in the README; the release script never edits it, so a pin goes stale.

## Knowledge store

- Vocabulary: [`CONCEPTS.md`](CONCEPTS.md) · learnings and past bugs:
  [`docs/solutions/`](docs/solutions/), including the tmux toolchain in
  [`docs/solutions/tmux/background-pane-swap.md`](docs/solutions/tmux/background-pane-swap.md).
- Direction: the Pi half could be a native Pi extension, at the cost of the
  side-by-side view, which fundamentally needs tmux — see
  [`docs/solutions/pi-extension/`](docs/solutions/pi-extension/).
- Document new solved problems there rather than in code comments.
