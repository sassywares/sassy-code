"""Facade for the `panel` package: every module's public names in one place.

Tests (and any one-off script) read the panel through this module, so the
namespace they see is the one the single-file panel used to have. Ownership
does not move: a name is patched at the module that owns it
(`panel.state.STATE_DIR`, `panel.procs.process_table`) and a name a module
reads from another is reached through that module (`goals.load_goals()`), so a
re-export here is for reading only.

`panel.main` is deliberately absent (D10): a package `__init__` that imports
its `__main__` module makes `python3 -m panel` warn and execute it twice. The
app-layer names (`App`, `_main_curses`, `main_curses`, `list_lines`, `main`) are
imported from `panel.main` by whoever needs them.
"""
from __future__ import annotations

# The wildcard imports also pass through each owner's incidental stdlib
# imports (`panel.curses`, `panel.os`, `panel.time`, ...), which tests read.
# An `__all__` in any owner module would remove that pass-through.
from panel.core import *  # noqa: F401,F403
from panel.parsers import *  # noqa: F401,F403
from panel.procs import *  # noqa: F401,F403
from panel.tmuxctl import *  # noqa: F401,F403
from panel.panes import *  # noqa: F401,F403
from panel.state import *  # noqa: F401,F403
from panel.goals import *  # noqa: F401,F403
from panel.status import *  # noqa: F401,F403
from panel.rows import *  # noqa: F401,F403
from panel.view import *  # noqa: F401,F403
from panel.mouse import *  # noqa: F401,F403
from panel.boxes import *  # noqa: F401,F403
from panel.actions import *  # noqa: F401,F403


# Leading-underscore names tests read, kept out of the wildcard on purpose.
from panel.boxes import _DERIVE_CONTAINER, _PICKER_STAY  # noqa: F401
from panel.core import _infer_status  # noqa: F401
