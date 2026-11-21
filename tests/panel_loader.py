"""Shared loader: import the `panel` package fresh for a test file."""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ROOT_PATH = str(ROOT)


def load_panel():
    """Return a fresh `panel` facade, with `panel.main` loaded alongside it.

    The facade deliberately omits `panel.main` (D10), so app-layer names
    (`App`, `main_curses`, `list_lines`) are reached as `panel.main.<name>`;
    importing it here only makes that attribute exist.
    """
    if ROOT_PATH not in sys.path:
        sys.path.insert(0, ROOT_PATH)
    for name in [n for n in sys.modules if n == "panel" or n.startswith("panel.")]:
        del sys.modules[name]
    module = importlib.import_module("panel")
    importlib.import_module("panel.main")
    return module
