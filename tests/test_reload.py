#!/usr/bin/env python3
"""Tests for external `/reload` dispatch and the wrapping footer."""
from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location("sassy_panel", ROOT / "panel.py")
panel = importlib.util.module_from_spec(SPEC)
sys.modules["sassy_panel"] = panel
SPEC.loader.exec_module(panel)


def proc(pid: int, ppid: int, command: str) -> panel.Proc:
    return panel.Proc(pid=pid, ppid=ppid, command=command)


def pane(pid: int, tag: str, pane_id: str) -> panel.PaneInfo:
    return panel.PaneInfo(
        id=pane_id,
        window_id="@1",
        session_name=panel.BG_SESSION,
        pid=pid,
        tag=tag,
    )


class ReloadTests(unittest.TestCase):
    def setUp(self):
        # pid 100 is a plain shell; 101 is its live Pi child.
        self.procs = [
            proc(100, 1, "bash"),
            proc(101, 100, "pi --session aaa"),
            proc(200, 1, "bash"),
            proc(300, 200, "pi --session bbb"),
        ]
        self.panes = [
            pane(100, "aaa", "%1"),   # tagged + live Pi -> reload
            pane(200, "", "%2"),      # live Pi but unmanaged -> skip
            pane(300, "bbb", "%3"),   # tagged + live Pi -> reload
            pane(999, "ccc", "%4"),   # tagged, Pi exited -> skip
        ]

    def test_reload_targets_only_tagged_live_pi_panes(self):
        sent = []

        def send(pane_id):
            sent.append(pane_id)
            return True

        count = panel.reload_managed_panes(
            [], panes=self.panes, procs=self.procs, send=send
        )
        self.assertEqual(count, 2)
        self.assertEqual(sent, ["%1", "%3"])

    def test_reload_counts_only_successful_sends(self):
        sent = []

        def send(pane_id):
            sent.append(pane_id)
            return pane_id == "%3"

        count = panel.reload_managed_panes(
            [], panes=self.panes, procs=self.procs, send=send
        )
        self.assertEqual(count, 1)
        self.assertEqual(sent, ["%1", "%3"])


class FooterTests(unittest.TestCase):
    def test_narrow_footer_wraps_without_dropping_bindings(self):
        lines = panel.footer_lines(panel.FOOTER_ITEMS, 26)
        self.assertGreater(len(lines), 1)
        for line in lines:
            self.assertLessEqual(len(line), 25)
        rendered = "  ".join(lines)
        for item in panel.FOOTER_ITEMS:
            self.assertIn(item, rendered)

    def test_wide_footer_is_one_line(self):
        lines = panel.footer_lines(panel.FOOTER_ITEMS, 200)
        self.assertEqual(len(lines), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
