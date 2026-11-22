#!/usr/bin/env bash
# The screenshot hook's release gate.
#
# `assets/sassy-code.svg` may only be refreshed when `bin/release` would tag
# something, otherwise the shot's own commit becomes a consumer-facing change
# and every push cuts a release. This test drives the real `ship.sh` against a
# throwaway origin and asserts the gate:
#
#   docs/ only              -> "nothing to release", no commit
#   .agents/ fixture only   -> refused, even though it would move pixels
#   panel/ change           -> gate opens, and an unchanged fixture commits
#                              nothing (the capture is byte-reproducible)
#   fixture + panel change  -> the shot really changes and is committed alone
set -euo pipefail

repo="$(cd "$(dirname "$0")/.." && pwd)"
work="$(mktemp -d -t sassy-shot-gate.XXXXXX)"
trap 'rm -rf "$work"' EXIT

git init -q --bare "$work/origin.git"
git init -q -b main "$work/work"
cd "$work/work"
git remote add origin "$work/origin.git"
rsync -a --exclude .git "$repo/" ./
export GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@t
export GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@t
git add -A
git commit -qm "base"
git push -q origin main
git tag v0.1.3
git push -q origin v0.1.3

shot="./.agents/skills/sassy-code-shot/scripts/ship.sh"
demo=".agents/skills/sassy-code-shot/fixtures/demo.py"

fail() { echo "FAIL: $*" >&2; exit 1; }
head_rev() { git rev-parse HEAD; }
# A visible name change: the panel lists project headings in full.
move_pixels() { python3 -c "import sys,pathlib;p=pathlib.Path(sys.argv[1]);p.write_text(p.read_text().replace('web-shop','web-store'))" "$demo"; }

# 1. A docs-only change is not consumer-facing: the shot is not even rendered.
echo "note" >> docs/architecture.md
git commit -qam "docs change"
before="$(head_rev)"
out="$($shot 2>&1)" || fail "ship.sh failed on a docs change: $out"
[[ "$out" == *"nothing to release"* ]] || fail "docs change: $out"
[[ "$(head_rev)" == "$before" ]] || fail "docs change: ship.sh committed"

# 2. A fixture-only change is ignored too, even though it would move pixels.
move_pixels
git commit -qam "fixture change"
before="$(head_rev)"
out="$($shot 2>&1)" || fail "ship.sh failed on a fixture change: $out"
[[ "$out" == *"nothing to release"* ]] || fail "fixture change: $out"
[[ "$(head_rev)" == "$before" ]] || fail "fixture change: ship.sh committed"
git checkout -q HEAD~1 -- "$demo"
git commit -qam "revert fixture change"

# 3. A panel change opens the gate; the capture is reproducible, so the image
#    on disk is byte-identical and nothing is committed.
echo "# consumer-facing" >> panel/state.py
git commit -qam "panel change"
before="$(head_rev)"
out="$($shot 2>&1)" || fail "ship.sh failed on a panel change: $out"
[[ "$out" != *"nothing to release"* ]] || fail "panel change: gate stayed shut: $out"
[[ "$out" == *"is up to date"* ]] || fail "panel change: capture was not reproducible: $out"
[[ "$(head_rev)" == "$before" ]] || fail "panel change: ship.sh committed an unchanged shot"

# 4. With the panel changed and the fixture moved, the shot differs: `check`
#    reports it, one run commits the image alone, and a rerun is a no-op.
move_pixels
git commit -qam "fixture change"
$shot check >/dev/null 2>&1 && fail "check: a stale shot exited 0"
before="$(head_rev)"
out="$($shot 2>&1)" || fail "ship.sh failed with a changed fixture: $out"
[[ "$(head_rev)" != "$before" ]] || fail "changed fixture: ship.sh did not commit the shot"
git log -1 --format=%s | grep -qx "docs: refresh the README screenshot" ||
  fail "changed fixture: unexpected commit subject: $(git log -1 --format=%s)"
git show --name-only --format= HEAD | grep -qx "assets/sassy-code.svg" ||
  fail "changed fixture: the commit touched more than the shot"
$shot check >/dev/null 2>&1 || fail "check: the shot is fresh after the commit"
before="$(head_rev)"
out="$($shot 2>&1)" || fail "a second run failed: $out"
[[ "$out" == *"is up to date"* ]] || fail "second run: $out"
[[ "$(head_rev)" == "$before" ]] || fail "second run: committed again"

echo "shot-gate: ok"
