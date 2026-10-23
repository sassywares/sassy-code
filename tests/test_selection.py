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


class SidebarLayoutTests(unittest.TestCase):
    def group(self, cwd: str, count: int, live: tuple[int, ...] = ()) -> panel.Group:
        sessions = [
            session(f"{cwd}-{i}", cwd, 100.0 + i) for i in range(count)
        ]
        sessions.sort(key=lambda s: s.last_ts, reverse=True)
        for i in live:
            sessions[i].status = "running"
        return panel.Group(cwd, sessions)

    def test_group_limits_sessions_and_offers_more(self):
        g = self.group("/proj", 7)
        items = list(panel.rows_for([g], "", set(), set()))
        self.assertEqual(
            [item[0] for item in items],
            ["group"] + ["session"] * panel.SESSION_LIMIT + ["more"],
        )
        self.assertEqual(items[-1][2], 7 - panel.SESSION_LIMIT)  # hidden count

    def test_more_row_reveals_everything_and_collapses_again(self):
        g = self.group("/proj", 7)
        items = list(panel.rows_for([g], "", set(), {"/proj"}))
        self.assertEqual(
            [item[0] for item in items], ["group"] + ["session"] * 7 + ["more"]
        )
        lines, _, _ = panel.build_lines(
            items, 40, set(), "", {"/proj"}, {"/proj": "proj"}
        )
        self.assertEqual(lines[-1][0].strip(), "… show fewer")

    def test_limit_does_not_hide_a_live_session(self):
        g = self.group("/proj", 7, live=(6,))
        items = list(panel.rows_for([g], "", set(), set()))
        shown = [item[2] for item in items if item[0] == "session"]
        self.assertIn(g.sessions[6], shown)
        self.assertEqual(len(shown), panel.SESSION_LIMIT + 1)
        self.assertEqual(items[-1][2], 7 - len(shown))

    def test_filter_shows_every_match(self):
        # Half the titles match the filter; the limit must not hide them.
        g = self.group("/proj", 8)
        for i, s in enumerate(g.sessions):
            s.preview = f"match {i}"
        items = list(panel.rows_for([g], "match", set(), set()))
        self.assertEqual(
            [item[0] for item in items], ["group"] + ["session"] * 8
        )

    def test_collapsed_group_shows_neither_sessions_nor_more(self):
        g = self.group("/proj", 7)
        items = list(panel.rows_for([g], "", {"/proj"}, set()))
        self.assertEqual([item[0] for item in items], ["group"])

    def test_cursor_falls_back_to_the_more_row(self):
        g = self.group("/proj", 7)
        items = list(panel.rows_for([g], "", set(), set()))
        hidden = g.sessions[-1]
        self.assertEqual(
            items[panel.cursor_for_key(
                items, ("session", hidden.id, "/proj")
            )][0],
            "more",
        )

    def test_groups_are_separated_by_one_blank_gap(self):
        one = self.group("/one", 2)
        two = self.group("/two", 1)
        items = list(panel.rows_for([one, two], "", set(), set()))
        lines, starts, heights = panel.build_lines(
            items, 40, set(), "", set(), {}
        )
        preview = [(line[0], line[1]) for line in lines]
        self.assertEqual([
            text for text, kind in preview if kind == "gap"
        ], [""])
        gap = next(i for i, (_, kind) in enumerate(preview) if kind == "gap")
        self.assertEqual(preview[0][1], "group")
        self.assertEqual(preview[gap + 1][1], "group")
        self.assertEqual(starts[3], gap + 1)  # second group starts after gap
        self.assertEqual(heights[3], 1)

    def test_single_group_has_no_gap(self):
        items = list(panel.rows_for([self.group("/one", 2)], "", set(), set()))
        lines, _, _ = panel.build_lines(items, 40, set(), "", set(), {})
        self.assertTrue(all(kind != "gap" for _, kind, _ in lines))

    def test_group_names_are_readable_and_unique(self):
        home = "/home/u"
        paths = [
            home,
            f"{home}/Documents/Workspace/tap2-workspace",
            "/srv/a/app",
            "/srv/b/app",
            "/srv/x/root",
        ]
        labels = panel.canonical_group_names(
            [panel.Group(p) for p in paths], home=home
        )
        self.assertEqual(labels[home], "root")
        self.assertEqual(labels[paths[1]], "tap2-workspace")
        self.assertEqual(labels["/srv/a/app"], "a/app")
        self.assertEqual(labels["/srv/b/app"], "b/app")
        self.assertEqual(labels["/srv/x/root"], "x/root")
        self.assertEqual(len(set(labels.values())), len(paths))


if __name__ == "__main__":
    unittest.main(verbosity=2)
