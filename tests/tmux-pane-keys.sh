#!/usr/bin/env bash
# Regression test: the launcher disables repeat on tmux's default prefix+arrow
# pane-switch bindings, including on a cold start, without touching custom
# prefix bindings.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LABEL="sassy-pi-test-$$"
SOCKET_PATH=""
COLD_DIR=""
COLD_HOME=""
COLD_CONFIG=""

cleanup() {
  if [ -n "$SOCKET_PATH" ]; then
    tmux -S "$SOCKET_PATH" kill-server 2>/dev/null || true
  fi
  if [ -n "$COLD_DIR" ]; then
    # Ignore an inherited TMUX so this only targets the cold-start server.
    env -u TMUX TMUX_TMPDIR="$COLD_DIR" tmux kill-server 2>/dev/null || true
  fi
  tmux -L "$LABEL" kill-server 2>/dev/null || true
  local dir
  for dir in "$COLD_DIR" "$COLD_HOME" "$COLD_CONFIG"; do
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

assert_pane_keys() {
  local key dir binding
  for key in Up Down Left Right; do
    dir="${key:0:1}"   # tmux pane directions: Up→U, Down→D, Left→L, Right→R
    binding="$(tmux list-keys -T prefix -F '#{key_repeat} #{key_command}' "$key")" \
      || fail "$key is not bound"
    [ "$binding" = "0 select-pane -$dir" ] \
      || fail "$key is not a non-repeatable select-pane -$dir: $binding"
  done
}

assert_extended_keys() {
  local value
  value="$(tmux show-options -gv extended-keys 2>/dev/null)" \
    || fail "extended-keys is not set"
  [ "$value" = "on" ] || fail "extended-keys is not on: $value"
  value="$(tmux show-options -gv extended-keys-format 2>/dev/null)" \
    || fail "extended-keys-format is not set"
  [ "$value" = "csi-u" ] || fail "extended-keys-format is not csi-u: $value"
}

# Scenario 1: cold start with no tmux server or session yet, and no user
# configuration that could redefine the prefix arrows.
COLD_DIR="$(mktemp -d)"
COLD_HOME="$(mktemp -d)"
COLD_CONFIG="$(mktemp -d)"
(
  unset TMUX
  export TMUX_TMPDIR="$COLD_DIR"
  export HOME="$COLD_HOME"
  export XDG_CONFIG_HOME="$COLD_CONFIG"
  if tmux list-sessions >/dev/null 2>&1; then
    fail "expected no tmux server before cold start"
  fi
  SASSY_PI_TMUX_CONFIG_ONLY=1 "$ROOT/main" \
    || fail "launcher failed in cold-start config-only mode"
  assert_extended_keys
  assert_pane_keys
)
env -u TMUX TMUX_TMPDIR="$COLD_DIR" tmux kill-server 2>/dev/null || true
rm -rf "$COLD_DIR" "$COLD_HOME" "$COLD_CONFIG"
COLD_DIR=""
COLD_HOME=""
COLD_CONFIG=""
printf 'PASS: cold start configures extended keys and non-repeatable prefix arrows\n'

# Scenario 2: an isolated server with a custom prefix binding.
tmux -L "$LABEL" -f /dev/null new-session -d -s sassy-pi-test
SOCKET_PATH="$(tmux -L "$LABEL" display -p '#{socket_path}')"
export TMUX="$SOCKET_PATH"

# A repeatable, non-default binding that must survive configuration.
tmux bind-key -r -T prefix C-g display-message custom-binding
CUSTOM_BEFORE="$(tmux list-keys -T prefix C-g)"

SASSY_PI_TMUX_CONFIG_ONLY=1 "$ROOT/main" \
  || fail "launcher failed in config-only mode"

assert_pane_keys

CUSTOM_AFTER="$(tmux list-keys -T prefix C-g)"
[ "$CUSTOM_AFTER" = "$CUSTOM_BEFORE" ] \
  || fail "custom binding changed: $CUSTOM_AFTER"
case "$CUSTOM_AFTER" in
  *" -r "*"display-message custom-binding") ;;
  *) fail "custom binding not preserved: $CUSTOM_AFTER" ;;
esac

printf 'PASS: prefix arrows are non-repeatable select-pane; custom binding preserved\n'
