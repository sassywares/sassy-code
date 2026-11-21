---
title: Resolve the repo root from the package's parent, never from __file__ alone
category: python
tags: [paths, __file__, module-split, guards, tests]
created: 2026-11-22
---

## Problem

`SASSY_CODE_MOUSE_DEBUG` promises a diagnostics file and refuses any path inside
the repo, so the panel never seeds files into its own tree. After the module
split the guard silently stopped refusing part of the repo: a path anywhere
under the repo root but outside `panel/` was accepted and written.

## Root cause

The guard computed the repo as the directory of the module:
`os.path.dirname(os.path.realpath(__file__))`. When `panel.py` was one file at
the root that was the repo. `git mv` into a package changed `__file__` from
`<repo>/panel.py` to `<repo>/panel/mouse.py`, so the same expression moved the
"repo" down one level, and every other path in the repo became fair game.

The existing test did not catch it because it derived the same directory the
same way — from the module under test — so it re-based along with the guard and
kept asserting a narrower claim than it read as. A test that shares a path
derivation with the code cannot police that derivation.

## Solution

Resolve the repo from the package's parent (`panel/mouse.py`):

```python
# `__file__` lives in the package, so the repo root is its parent.
repo = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
```

The rule generalises: a guard that means *the repository* must walk up from the
package, not assume the module sits at the root. If the module ever moves again,
that assumption is the thing to re-check first.

## Prevention

`MouseDebugTests` in `tests/test_mouse.py` derives the repo root from the test
tree (`ROOT` in `tests/panel_loader.py`), independently of the panel module,
and asserts a repo-root-relative diagnostics path is not created. Keep path
fixtures in tests derived from the test file or an explicit root, never from the
module they are testing.
