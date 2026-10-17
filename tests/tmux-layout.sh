#!/usr/bin/env bash
# Regression test: the in-tmux launcher keeps the panel narrow and leftmost
# while running the panel in its own pane beside the original viewer pane.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LABEL="sassy-pi-layout-test-$$"
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
mkdir -p "$TMP/sessions"

env -u TMUX tmux -L "$LABEL" -f /dev/null \
  new-session -d -s user -n win -x 160 -y 40
VIEWER="$(tmux -L "$LABEL" list-panes -t user -F '#{pane_id}' | head -1)"
SOCKET="$(tmux -L "$LABEL" display -p '#{socket_path}')"

env TMUX="$SOCKET" TMUX_PANE="$VIEWER" \
  PI_CODING_AGENT_SESSION_DIR="$TMP/sessions" \
  TERM="${TERM:-xterm-256color}" \
  "$ROOT/main"

# The panel starts asynchronously in the new pane; give it a moment to exec.
left=""
right=""
for _ in $(seq 1 50); do
  panes="$(tmux -L "$LABEL" list-panes -t user \
    -F '#{pane_id} #{pane_width} left=#{pane_left} cmd=#{pane_current_command}')"
  count="$(printf '%s\n' "$panes" | wc -l | tr -d ' ')"
  if [ "$count" -eq 2 ]; then
    left="$(printf '%s\n' "$panes" | awk '$3 == "left=0" {print; exit}')"
    right="$(printf '%s\n' "$panes" | awk '$3 != "left=0" {print; exit}')"
    if [ -n "$left" ] && [ -n "$right" ]; then
      case "$left" in
        *cmd=*python*|*cmd=*Python*) break ;;
      esac
    fi
  fi
  sleep 0.1
done

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

printf 'PASS: in-tmux panel is narrow, leftmost, and beside the viewer\n'

# Scenario 2: cold start with no server. `tmux attach` cannot run without a
# terminal, but the launcher creates the layout first; inspect that layout.
COLD_DIR="$(mktemp -d)"
mkdir -p "$COLD_DIR/sessions" "$COLD_DIR/home" "$COLD_DIR/config"
cold_tmux() {
  env -u TMUX TMUX_TMPDIR="$COLD_DIR/tmux" tmux "$@"
}

env -u TMUX TMUX_TMPDIR="$COLD_DIR/tmux" \
  HOME="$COLD_DIR/home" XDG_CONFIG_HOME="$COLD_DIR/config" \
  PI_CODING_AGENT_SESSION_DIR="$COLD_DIR/sessions" \
  TERM="${TERM:-xterm-256color}" \
  "$ROOT/main" </dev/null >/dev/null 2>&1 || true

cold_left=""
cold_right=""
for _ in $(seq 1 50); do
  panes="$(cold_tmux list-panes -t sassy-pi \
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
