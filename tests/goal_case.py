"""Shared fixtures for the goal tests: one panel module, a session builder and
a base case that points the goal store at a temp dir."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.panel_loader import load_panel

panel = load_panel()


def session(sid: str, cwd: str, ts: float, title: str = "") -> panel.Session:
    return panel.Session(
        path=f"/tmp/{sid}.jsonl", id=sid, cwd=cwd, created=ts, last_ts=ts,
        name=title or sid,
    )


class GoalStoreCase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        for name, value in (
            ("STATE_DIR", str(Path(self.tmpdir.name) / "state")),
            ("_goals_cache", None),
        ):
            patcher = mock.patch.object(panel, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.file = Path(panel.STATE_DIR) / "goals.json"
