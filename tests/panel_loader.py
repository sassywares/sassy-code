"""Shared loader: import panel.py as a fresh module for a test file."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_panel():
    spec = importlib.util.spec_from_file_location(
        "sassy_code_panel", ROOT / "panel.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["sassy_code_panel"] = module
    spec.loader.exec_module(module)
    return module
