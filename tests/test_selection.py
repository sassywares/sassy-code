#!/usr/bin/env python3
"""Selection identity tests: the highlight follows a session across re-sorts."""
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


def session(sid: str, cwd: str, ts: float) -> panel.Session:
    return panel.Session(
        path=f"/tmp/{sid}.jsonl",
        id=sid,
        cwd=cwd,
        created=ts,
        last_ts=ts,
    )


def rows(groups):
    return list(panel.rows_for(groups, "", set()))


def pane(pane_id: str, tag: str = "") -> panel.PaneInfo:
    return panel.PaneInfo(pane_id, "win", "vis", 4242, tag)


class SelectionTests(unittest.TestCase):
    def test_session_highlight_follows_resort(self):
        a = session("aaa", "/proj", 100.0)
        b = session("bbb", "/proj", 200.0)
        group = panel.Group("/proj", [b, a])
        before = rows([group])
        key = panel.row_key(before[1])  # b's row while b is first
        self.assertEqual(panel.cursor_for_key(before, key), 1)

        # a starts working and sorts to the top of the group.
        a.last_ts = 300.0
        group.sessions.sort(key=lambda s: s.last_ts, reverse=True)
        after = rows([group])
        self.assertEqual(panel.cursor_for_key(after, key), 2)

    def test_group_highlight_follows_resort(self):
        one = panel.Group("/one", [session("a", "/one", 100.0)])
        two = panel.Group("/two", [session("b", "/two", 200.0)])
        before = rows([two, one])
        key = panel.row_key(before[2])  # /one group row
        self.assertEqual(panel.cursor_for_key(before, key), 2)

        one.sessions[0].last_ts = 300.0
        after = rows([one, two])
        self.assertEqual(panel.cursor_for_key(after, key), 0)

    def test_missing_selection_falls_back_to_group_then_top(self):
        group = panel.Group("/proj", [session("aaa", "/proj", 100.0)])
        collapsed = list(panel.rows_for([group], "", {"/proj"}))
        key = ("session", "aaa", "/proj")
        # Session hidden by collapse: highlight its group header.
        self.assertEqual(panel.cursor_for_key(collapsed, key), 0)
        self.assertEqual(panel.cursor_for_key([], key), 0)


class HighlightFollowTests(unittest.TestCase):
    def switched(self):
        old = session("old", "/proj", 100.0)
        new = session("new", "/proj", 200.0)
        new.parent_path = old.path
        old.last_role = "user"
        new.last_role = "assistant"
        return [panel.Group("/proj", [new, old])]

    def test_highlight_moves_to_the_handoff_successor(self):
        groups = self.switched()
        key, presented = panel.follow_viewer_switch(
            ("session", "old", "/proj"), "old",
            [pane("%1"), pane("%2", "new")], groups, "%2",
        )
        self.assertEqual(key, ("session", "new", "/proj"))
        self.assertEqual(presented, "new")

    def test_highlight_follows_a_sibling_successor(self):
        # A handoff from a handoff can parent the session the Pi process was
        # started with, making the two children siblings rather than a chain.
        root = session("root", "/proj", 50.0)
        old = session("old", "/proj", 100.0)
        old.parent_path = root.path
        new = session("new", "/proj", 200.0)
        new.parent_path = root.path
        groups = [panel.Group("/proj", [new, old, root])]
        key, presented = panel.follow_viewer_switch(
            ("session", "old", "/proj"), "old",
            [pane("%2", "new")], groups, "%2",
        )
        self.assertEqual(key, ("session", "new", "/proj"))
        self.assertEqual(presented, "new")

    def test_highlight_stays_when_browsing_another_row(self):
        groups = self.switched()
        key, presented = panel.follow_viewer_switch(
            ("session", "other", "/proj"), "old",
            [pane("%2", "new")], groups, "%2",
        )
        self.assertEqual(key, ("session", "other", "/proj"))
        self.assertEqual(presented, "old")

    def test_highlight_stays_when_the_viewer_was_not_retagged(self):
        groups = self.switched()
        key, presented = panel.follow_viewer_switch(
            ("session", "old", "/proj"), "old",
            [pane("%2", "old")], groups, "%2",
        )
        self.assertEqual(key, ("session", "old", "/proj"))
        self.assertEqual(presented, "old")

    def test_highlight_stays_when_the_viewer_shows_an_unrelated_session(self):
        groups = self.switched()
        groups[0].sessions.append(session("elsewhere", "/proj", 50.0))
        key, presented = panel.follow_viewer_switch(
            ("session", "old", "/proj"), "old",
            [pane("%2", "elsewhere")], groups, "%2",
        )
        self.assertEqual(key, ("session", "old", "/proj"))
        self.assertEqual(presented, "old")


if __name__ == "__main__":
    unittest.main(verbosity=2)
