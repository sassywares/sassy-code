#!/usr/bin/env bash
# Keep `assets/sassy-code.svg` current — on releases, not on every push.
#
#   ship.sh [ship|render|check] [--strict]
#
#   ship    the default, for the `after_push` hook: refresh the shot, then
#           commit and push it only when the image changed AND a release is
#           due, so the tag that follows carries the new shot and the shot
#           never manufactures a release of its own.
#   render  rewrite `assets/sassy-code.svg` if the panel changed; no git.
#   check   report whether the committed image is stale (exit 1); no writes.
#
# A screenshot must never block a release: every failure warns and exits 0
# unless `--strict` is passed.
#
# Ordering matters. The hook runs before `bin/release`, because the image has
# to be inside the tag; the release script then commits over a clean tree.
set -uo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
root="$(git -C "$here" rev-parse --show-toplevel 2>/dev/null)" || {
  echo "sassy-code-shot: not inside a git repository" >&2
  exit 1
}
cd "$root"

mode="ship"
strict=0
for arg in "$@"; do
  case "$arg" in
    ship | render | check) mode="$arg" ;;
    --strict) strict=1 ;;
    *) echo "sassy-code-shot: unknown argument: $arg" >&2; exit 1 ;;
  esac
done

shot="assets/sassy-code.svg"
say() { echo "sassy-code-shot: $*"; }
warn() { # never block the release unless asked to
  echo "sassy-code-shot: $*" >&2
  [ "$strict" = 1 ] && exit 1
  exit 0
}

# ---- consumer-facing change detection --------------------------------------
# Mirrors `bin/release`: the same `bin/release-ignore` (or its defaults), the
# same match rules, the same "highest v* tag on origin" boundary. A change the
# release script would not release must not refresh the shot.
ignore_patterns() {
  if [ -f bin/release-ignore ]; then
    while IFS= read -r line || [ -n "$line" ]; do
      line="${line%%#*}"
      line="${line#"${line%%[![:space:]]*}"}"
      line="${line%"${line##*[![:space:]]}"}"
      [ -n "$line" ] || continue
      printf '%s\n' "${line#/}"
    done < bin/release-ignore
  else
    printf '%s\n' AGENTS.md docs/ tests/ .sassywares/ .github/ .agents/ \
      .claude/ scripts/ bin/release bin/release-ignore
  fi
}

ignores=()
while IFS= read -r pat; do ignores+=("$pat"); done < <(ignore_patterns)

is_ignored() {
  local path="$1" pat
  for pat in ${ignores[@]+"${ignores[@]}"}; do
    case "$pat" in
      */) [[ "$path" == "$pat"* ]] && return 0 ;;
      *[*?[]*) [[ "$path" == $pat ]] && return 0 ;;
      *) [ "$path" = "$pat" ] && return 0
         [[ "$path" == "$pat"/* ]] && return 0 ;;
    esac
  done
  return 1
}

release_due() { # exit 0 when a release is due
  local tags tag changed line
  tags="$(git ls-remote --tags origin 2>/dev/null)" ||
    warn "could not reach origin; skipping the shot (a release cannot run offline either)"
  tag="$(printf '%s\n' "$tags" \
    | awk -F 'refs/tags/' 'NF > 1 { print $2 }' \
    | grep -E '^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(-[0-9A-Za-z.-]+)?$' \
    | sort -V | tail -1)"
  [ -n "$tag" ] || return 0   # nothing released yet: every change counts
  changed="$(git -c core.quotePath=false diff --name-only --no-renames "$tag" HEAD)" ||
    warn "could not diff $tag..HEAD; skipping the shot"
  while IFS= read -r line; do
    [ -n "$line" ] || continue
    is_ignored "$line" || return 0
  done <<< "$changed"
  return 1
}

# ---- render once, compare once ---------------------------------------------
tmp="$(mktemp -t sassy-shot.XXXXXX)"
trap 'rm -f "$tmp"' EXIT

[ "$mode" = ship ] && { release_due || { say "nothing to release; shot skipped"; exit 0; }; }

if [ "$mode" = ship ] && [ -n "$(git status --porcelain --untracked-files=normal)" ]; then
  # The release script refuses a dirty tree anyway; never commit into someone
  # else's work in progress.
  warn "working tree is dirty; leaving $shot alone"
fi

python3 "$here/capture.py" --out "$tmp" >/dev/null ||
  warn "capture failed; $shot left untouched"

if cmp -s "$tmp" "$shot"; then
  say "$shot is up to date"
  exit 0
fi

case "$mode" in
  check)
    say "$shot is stale; run this skill in render mode"
    exit 1
    ;;
  render)
    cp "$tmp" "$shot" || warn "could not write $shot"
    say "rewrote $shot"
    ;;
  ship)
    cp "$tmp" "$shot" || warn "could not write $shot"
    git add -- "$shot" || warn "could not stage $shot"
    git commit -q -m "docs: refresh the README screenshot" -- "$shot" ||
      warn "could not commit $shot"
    branch="$(git rev-parse --abbrev-ref HEAD)"
    # The release's own push of main carries the commit if this one fails, so
    # a failed push is not worth blocking the release over.
    git push origin "$branch" ||
      warn "committed $shot but could not push $branch; the release push will carry it"
    say "refreshed $shot on $branch"
    ;;
esac
