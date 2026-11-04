#!/usr/bin/env python3
"""Tests for the panel state directory and the legacy current-file migration."""
from __future__ import annotations

import tempfile
import unittest
from unittest import mock
from pathlib import Path

from tests.panel_loader import load_panel

panel = load_panel()


class StateDirTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.tmp = Path(self.tmpdir.name)
        for name, value in (
            ("STATE_DIR", str(self.tmp / ".sassywares" / "sassy-code")),
            ("LEGACY_STATE_FILE", str(self.tmp / ".sassy-code-current")),
        ):
            patcher = mock.patch.object(panel, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.new = Path(panel.STATE_DIR) / "current"
        self.legacy = Path(panel.LEGACY_STATE_FILE)

    def test_fresh_install_does_nothing(self):
        panel.migrate_state()
        self.assertEqual(panel._read_state(), "")
        self.assertFalse(Path(panel.STATE_DIR).exists())

    def test_write_creates_state_dir(self):
        panel._write_state("abc")
        self.assertEqual(self.new.read_text(), "abc")
        self.assertEqual(panel._read_state(), "abc")

    def test_migrates_legacy_file(self):
        self.legacy.write_text("old-id\n")
        panel.migrate_state()
        self.assertFalse(self.legacy.exists())
        self.assertEqual(panel._read_state(), "old-id")

    def test_new_file_wins_and_legacy_is_left_alone(self):
        self.new.parent.mkdir(parents=True)
        self.new.write_text("new-id")
        self.legacy.write_text("old-id")
        panel.migrate_state()
        self.assertEqual(panel._read_state(), "new-id")
        self.assertEqual(self.legacy.read_text(), "old-id")

    def test_failed_move_does_not_raise_and_keeps_legacy(self):
        self.legacy.write_text("old-id")
        with mock.patch.object(panel.shutil, "move", side_effect=OSError("boom")):
            panel.migrate_state()
        self.assertEqual(self.legacy.read_text(), "old-id")
        self.assertFalse(self.new.exists())


if __name__ == "__main__":
    unittest.main()
