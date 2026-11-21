#!/usr/bin/env bash
# Regression test: the launcher never splits the current window; it builds the
# dedicated sassy-code session with a narrow leftmost panel, and does not add
# a second panel when re-run.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LABEL="sassy-code-layout-test-$$"
TMP=""
COLD_DIR=""

cleanup() {
  tmux -L "$LABEL" kill-server 2>/dev/null || true
  if [ -n "$COLD_DIR" ]; then
    env -u TMUX TMUX_TMPDIR="$COLD_DIR/tmux" tmux kill-server 2>/dev/null || true
  fi
  for dir in "$TMP" "$COLD_DIR"; do
    if [ -n "$dir" ]; then
      rm -rf "$dir"
    fi
  done
}
trap cleanup EXIT INT TERM

fail() {
  printf 'FAIL: %s\n' "$*" >&2
  exit 1
}

TMP="$(mktemp -d)"
mkdir -p "$TMP/sessions" "$TMP/claude"

env -u TMUX tmux -L "$LABEL" -f /dev/null \
  new-session -d -s user -n win -x 160 -y 40
VIEWER="$(tmux -L "$LABEL" list-panes -t user -F '#{pane_id}' | head -1)"
SOCKET="$(tmux -L "$LABEL" display -p '#{socket_path}')"

run_main() {
  env TMUX="$SOCKET" TMUX_PANE="$1" \
    PI_CODING_AGENT_SESSION_DIR="$TMP/sessions" \
    SASSY_CODE_CLAUDE_SESSION_DIR="$TMP/claude" \
    TERM="${TERM:-xterm-256color}" \
    "$ROOT/main"
}

t() { tmux -L "$LABEL" "$@"; }

# Count live panels (tagged panes running Python) in the dedicated session.
live_panels() {
  t list-panes -s -t =sassy-code \
    -F '#{?@sassy_panel,#{pane_current_command},}' 2>/dev/null \
    | grep -ci python || true
}

wait_panels() {
  for _ in $(seq 1 80); do
    [ "$(live_panels)" -ge "$1" ] && return 0
    sleep 0.1
  done
  return 1
}

# Scenario 1: run from inside another tmux session. The user's window must
# not be split; the dedicated session is created with a narrow leftmost panel.
run_main "$VIEWER"
wait_panels 1 || fail "no live panel in the dedicated session"

[ "$(t list-panes -t user | wc -l | tr -d ' ')" -eq 1 ] \
  || fail "the user's window was split"

# Scenario 1a: the status line shows the whole session name, then the window
# list. tmux's default status-left-length (10) clips "sassy-code" and butts the
# window list against it, so the launcher must widen it for this session. The
# launcher leaves status-left itself unset, so a user's own format survives.
footer_left="$(t show-options -t "=sassy-code:" -v status-left 2>/dev/null || true)"
[ -z "$footer_left" ] \
  || fail "launcher set status-left itself, overriding a user's format: '$footer_left'"
footer_len="$(t show-options -t "=sassy-code:" -v status-left-length 2>/dev/null || true)"
[ -n "$footer_len" ] || footer_len="$(t show-options -gv status-left-length)"
footer_rendered="$(t display-message -p -t "=sassy-code:" '#{E:status-left}')"
case "$footer_rendered" in
  *sassy-code*) ;;
  *) fail "status-left does not show the session name: '$footer_rendered'" ;;
esac
[ "${#footer_rendered}" -le "$footer_len" ] \
  || fail "status-left clips the session name: '$footer_rendered' is ${#footer_rendered} cols, status-left-length is $footer_len"
case "$footer_rendered" in
  *' ') ;;
  *) fail "status-left runs into the window list: '$footer_rendered'" ;;
esac
printf 'PASS: the status line shows the whole session name before the window list\n'

panes="$(t list-panes -t =sassy-code -F '#{pane_id} #{pane_width} left=#{pane_left} cmd=#{pane_current_command}')"
left="$(printf '%s\n' "$panes" | awk '$3 == "left=0" {print; exit}')"
right="$(printf '%s\n' "$panes" | awk '$3 != "left=0" {print; exit}')"
[ -n "$left" ] || fail "no leftmost panel pane found: $panes"
[ -n "$right" ] || fail "no viewer pane found: $panes"
left_width="$(printf '%s\n' "$left" | sed -E 's/.* ([0-9]+) left=.*/\1/')"
right_width="$(printf '%s\n' "$right" | sed -E 's/.* ([0-9]+) left=.*/\1/')"
[ "$left_width" -lt "$right_width" ] \
  || fail "panel is not narrow: left=$left_width right=$right_width"
case "$left" in
  *cmd=*python*|*cmd=*Python*) ;;
  *) fail "panel process did not start in the left pane: $left" ;;
esac
printf 'PASS: from another session, the dedicated session gets a narrow leftmost panel and the window is not split\n'

# Scenario 1b: running again, from the user session and from inside the
# dedicated session, never adds a second panel or window.
PANEL_PANE="$(printf '%s\n' "$left" | awk '{print $1}')"
run_main "$VIEWER"
run_main "$PANEL_PANE"
sleep 0.5
[ "$(live_panels)" -eq 1 ] || fail "a second panel appeared: $(live_panels)"
[ "$(t list-windows -t =sassy-code | wc -l | tr -d ' ')" -eq 1 ] \
  || fail "a second window appeared in the dedicated session"
[ "$(t list-panes -t user | wc -l | tr -d ' ')" -eq 1 ] \
  || fail "the user's window was split on re-run"
printf 'PASS: re-running adds no second panel\n'

# Scenario 1c: a session left without a live panel (crash) is rebuilt.
OLD_VIEWER="$(t list-panes -t =sassy-code -F '#{pane_id} #{pane_left}' \
  | awk '$2 != 0 {print $1; exit}')"
[ -n "$OLD_VIEWER" ] || fail "no old viewer pane found"
t kill-pane -t "$PANEL_PANE"
[ "$(live_panels)" -eq 0 ] || fail "panel should be gone"
run_main "$VIEWER"
wait_panels 1 || fail "panel-less session was not rebuilt"
printf 'PASS: a panel-less dedicated session is rebuilt\n'

# Rebuild adds a window and keeps the old panel-less one, with its pane (and
# any agent in it) untouched.
[ "$(t list-windows -t =sassy-code | wc -l | tr -d ' ')" -eq 2 ] \
  || fail "rebuild should add a window and keep the old one"
t list-panes -s -t =sassy-code -F '#{pane_id}' | grep -qx "$OLD_VIEWER" \
  || fail "the old window's pane was killed by the rebuild"
printf 'PASS: rebuild keeps the old panel-less window and its panes\n'

# Scenario 1d: two immediate runs give exactly one panel, even though the
# panel only marks itself once curses starts.
t kill-session -t =sassy-code
run_main "$VIEWER"
run_main "$VIEWER"
wait_panels 1 || fail "no panel after two immediate runs"
sleep 0.5
[ "$(live_panels)" -eq 1 ] || fail "two immediate runs gave $(live_panels) panels"
[ "$(t list-windows -t =sassy-code | wc -l | tr -d ' ')" -eq 1 ] \
  || fail "two immediate runs gave more than one window"
printf 'PASS: two immediate runs give exactly one panel\n'

# Scenario 1e: a mark that never became a running panel ages out, so the
# session is rebuilt instead of being stranded.
t kill-session -t =sassy-code
t new-session -d -s sassy-code -n stale sleep 300
STALE="$(t list-panes -t =sassy-code -F '#{pane_id}' | head -1)"
t set-option -p -t "$STALE" @sassy_panel "$(( $(date +%s) - 120 ))"
run_main "$VIEWER"
wait_panels 1 || fail "a stale panel mark stranded the session"
printf 'PASS: a stale panel mark counts as dead and the session is rebuilt\n'

# Scenario 1f: a zero-padded mark like 09 (bad octal) and a future mark count
# as dead; the launcher rebuilds instead of exiting with "value too great".
for mark in 09 08 "$(( $(date +%s) + 3600 ))"; do
  t kill-session -t =sassy-code
  t new-session -d -s sassy-code -n odd sleep 300
  ODD="$(t list-panes -t =sassy-code -F '#{pane_id}' | head -1)"
  t set-option -p -t "$ODD" @sassy_panel "$mark"
  run_main "$VIEWER" || fail "main failed on panel mark $mark"
  wait_panels 1 || fail "panel mark $mark stranded the session"
done
printf 'PASS: odd panel marks (09, future) count as dead and never crash the launcher\n'

# Scenario 2: cold start with no server. `tmux attach` cannot run without a
# terminal, but the launcher creates the layout first; inspect that layout.
COLD_DIR="$(mktemp -d)"
mkdir -p "$COLD_DIR/sessions" "$COLD_DIR/claude" "$COLD_DIR/home" "$COLD_DIR/config"
cold_tmux() {
  env -u TMUX TMUX_TMPDIR="$COLD_DIR/tmux" tmux "$@"
}

env -u TMUX TMUX_TMPDIR="$COLD_DIR/tmux" \
  HOME="$COLD_DIR/home" XDG_CONFIG_HOME="$COLD_DIR/config" \
  PI_CODING_AGENT_SESSION_DIR="$COLD_DIR/sessions" \
  SASSY_CODE_CLAUDE_SESSION_DIR="$COLD_DIR/claude" \
  TERM="${TERM:-xterm-256color}" \
  "$ROOT/main" </dev/null >/dev/null 2>&1 || true

cold_left=""
cold_right=""
for _ in $(seq 1 50); do
  panes="$(cold_tmux list-panes -t sassy-code \
    -F '#{pane_id} #{pane_width} left=#{pane_left} cmd=#{pane_current_command}' \
    2>/dev/null || true)"
  count="$(printf '%s\n' "$panes" | grep -c . || true)"
  if [ "$count" -eq 2 ]; then
    cold_left="$(printf '%s\n' "$panes" | awk '$3 == "left=0" {print; exit}')"
    cold_right="$(printf '%s\n' "$panes" | awk '$3 != "left=0" {print; exit}')"
    if [ -n "$cold_left" ] && [ -n "$cold_right" ]; then
      case "$cold_left" in
        *cmd=*python*|*cmd=*Python*) break ;;
      esac
    fi
  fi
  sleep 0.1
done

[ -n "$cold_left" ] || fail "cold start: no leftmost panel pane: $panes"
[ -n "$cold_right" ] || fail "cold start: no viewer pane: $panes"

cold_left_width="$(printf '%s\n' "$cold_left" | sed -E 's/.* ([0-9]+) left=.*/\1/')"
cold_right_width="$(printf '%s\n' "$cold_right" | sed -E 's/.* ([0-9]+) left=.*/\1/')"
[ "$cold_left_width" -lt "$cold_right_width" ] \
  || fail "cold start: panel is not narrow: left=$cold_left_width right=$cold_right_width"

case "$cold_left" in
  *cmd=*python*|*cmd=*Python*) ;;
  *) fail "cold start: panel process did not start in the left pane: $cold_left" ;;
esac

printf 'PASS: cold start creates a narrow left panel beside the wide viewer\n'

# The truly cold first launch must already show the whole name: the session is
# created before the option is set, so a first run never clips.
cold_len="$(cold_tmux show-options -t "=sassy-code:" -v status-left-length 2>/dev/null || true)"
[ -n "$cold_len" ] \
  || fail "cold start: status-left-length is unset, so the name clips to the default 10"
cold_rendered="$(cold_tmux display-message -p -t "=sassy-code:" '#{E:status-left}')"
case "$cold_rendered" in
  *sassy-code*) ;;
  *) fail "cold start: status-left does not show the session name: '$cold_rendered'" ;;
esac
[ "${#cold_rendered}" -le "$cold_len" ] \
  || fail "cold start: status-left clips the session name: '$cold_rendered' is ${#cold_rendered} cols, status-left-length is $cold_len"
printf 'PASS: cold start shows the whole session name on the first launch\n'

# Scenario 3: a panel that fails to start keeps its pane open with the exit
# status, instead of vanishing and leaving an empty viewer.
FAIL_DIR="$TMP/failing"
mkdir -p "$FAIL_DIR/panel"
cp "$ROOT/main" "$FAIL_DIR/main"
: > "$FAIL_DIR/panel/__init__.py"
# Print the package that actually ran, so the scenario proves it was the fake
# one and not the repo's package reached through PYTHONPATH.
printf 'import panel, sys\nprint("fake-panel:" + panel.__file__)\nsys.exit(3)\n' \
  > "$FAIL_DIR/panel/__main__.py"
# A decoy package in the launcher's working directory: `python3 -m panel` puts
# the *caller's* cwd ahead of PYTHONPATH, so a launcher that does not `cd` into
# its own dir would run this one instead of the fake package above.
DECOY_DIR="$TMP/decoy"
mkdir -p "$DECOY_DIR/panel"
: > "$DECOY_DIR/panel/__init__.py"
printf 'import panel, sys\nprint("decoy-panel:" + panel.__file__)\nsys.exit(9)\n' \
  > "$DECOY_DIR/panel/__main__.py"
t kill-session -t =sassy-code 2>/dev/null || true
# Run the fake launcher from the decoy dir: the panel must come from the
# launcher's own `DIR` (the `cd` in PANEL_CMD), never from the caller's cwd.
(
  cd "$DECOY_DIR"
  env TMUX="$SOCKET" TMUX_PANE="$VIEWER" TERM="${TERM:-xterm-256color}" \
    "$FAIL_DIR/main"
)
fail_pane=""
for _ in $(seq 1 50); do
  fail_pane="$(t list-panes -t =sassy-code -F '#{pane_id} #{pane_left}' \
    | awk '$2 == 0 {print $1; exit}')"
  if [ -n "$fail_pane" ] \
    && t capture-pane -pJ -t "$fail_pane" | grep -q 'panel exited with status 3'; then
    break
  fi
  sleep 0.1
done
# The marker (with its own `panel.__file__`) only the fake package prints, so
# this proves `python3 -m panel` resolved the launcher's own package, not one
# reachable from the caller's cwd, and not the repo's package via PYTHONPATH.
t capture-pane -pJ -t "$fail_pane" | grep -Fq 'decoy-panel:' \
  && fail "failed-panel scenario ran the decoy package from the caller's cwd"
t capture-pane -pJ -t "$fail_pane" | grep -Fq 'fake-panel:' \
  || fail "failed-panel scenario ran a package other than the fake one"
t capture-pane -pJ -t "$fail_pane" | grep -q 'panel exited with status 3' \
  || fail "failed panel did not show its exit status"
t send-keys -t "$fail_pane" Enter
for _ in $(seq 1 30); do
  t list-panes -t =sassy-code -F '#{pane_id}' | grep -qx "$fail_pane" || break
  sleep 0.1
done
! t list-panes -t =sassy-code -F '#{pane_id}' | grep -qx "$fail_pane" \
  || fail "failed panel pane did not close after Enter"
printf 'PASS: a failed panel shows its exit status and closes on Enter\n'
