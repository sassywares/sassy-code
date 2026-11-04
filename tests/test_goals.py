#!/usr/bin/env python3
"""Goal store, command line, row model, filter and --list tests."""
from __future__ import annotations

import errno
import json
import re
import time
import unittest
from pathlib import Path
from unittest import mock

from tests.goal_case import GoalStoreCase, panel, session

class StoreTests(GoalStoreCase):
    def test_missing_file_means_no_goals(self):
        self.assertEqual(panel.load_goals(), ({}, False))

    def test_create_round_trip(self):
        before = time.time()
        goal = panel.create_goal("/p", "  Auth   rework ")
        data = json.loads(self.file.read_text())
        self.assertEqual(data["version"], 1)
        rec = data["projects"]["/p"][0]
        self.assertEqual(rec["name"], "Auth rework")
        self.assertEqual(rec["id"], goal.id)
        self.assertEqual(len(rec["id"]), 36)
        self.assertGreaterEqual(rec["created"], before)
        self.assertEqual(
            (rec["done"], rec["collapsed"], rec["sessions"]), (False, False, [])
        )
        goals, bad = panel.load_goals()
        self.assertFalse(bad)
        self.assertEqual(goals["/p"][0].name, "Auth rework")

    def test_write_is_atomic_and_leaves_no_temp_files(self):
        with mock.patch.object(panel.os, "replace", side_effect=OSError("x")):
            with self.assertRaises(OSError):
                panel.create_goal("/p", "a")
        self.assertFalse(self.file.exists())
        self.assertEqual(list(Path(panel.STATE_DIR).iterdir()), [])
        with mock.patch.object(panel.os, "replace", wraps=panel.os.replace) as r:
            panel.create_goal("/p", "a")
        self.assertEqual(Path(r.call_args[0][1]), self.file)

    def test_mutation_rereads_the_file(self):
        panel.create_goal("/p", "one")
        data = json.loads(self.file.read_text())
        data["projects"]["/p"].append(
            {"id": "other", "name": "theirs", "created": 1, "done": False,
             "collapsed": False, "sessions": []}
        )
        self.file.write_text(json.dumps(data))
        panel.create_goal("/p", "two")  # another panel wrote in between
        names = [g["name"] for g in json.loads(self.file.read_text())["projects"]["/p"]]
        self.assertEqual(names, ["one", "theirs", "two"])

    def test_reloads_when_mtime_changes(self):
        panel.create_goal("/p", "one")
        self.assertEqual(len(panel.load_goals()[0]["/p"]), 1)
        data = json.loads(self.file.read_text())
        data["projects"]["/p"][0]["collapsed"] = True
        self.file.write_text(json.dumps(data, indent=4))
        self.assertTrue(panel.load_goals()[0]["/p"][0].collapsed)

    def test_corrupt_file_is_never_overwritten(self):
        self.file.parent.mkdir(parents=True)
        self.file.write_text("{")
        goals, bad = panel.load_goals()
        self.assertEqual((goals, bad), ({}, True))
        with self.assertRaises(panel.GoalError):
            panel.create_goal("/p", "x")
        self.assertEqual(self.file.read_text(), "{")
        text, goal = panel.run_command("goal:create x", "/p")
        self.assertEqual(text, panel.GOALS_UNREADABLE)
        self.assertIsNone(goal)
        self.assertEqual(self.file.read_text(), "{")

    def test_wrong_shape_counts_as_corrupt(self):
        self.file.parent.mkdir(parents=True)
        self.file.write_text('{"version": 2, "projects": {}}')
        self.assertTrue(panel.load_goals()[1])

    def test_set_collapsed_persists(self):
        goal = panel.create_goal("/p", "a")
        panel.set_goal_collapsed("/p", goal.id, True)
        self.assertTrue(panel.load_goals()[0]["/p"][0].collapsed)


class CommandTests(GoalStoreCase):
    def test_mutators_on_missing_project_raise_goal_error(self):
        self.file.parent.mkdir(parents=True, exist_ok=True)
        self.file.write_text(json.dumps({"version": 1, "projects": {}}))
        with self.assertRaises(panel.GoalError):
            panel.delete_goal("/p", "gone")
        with self.assertRaises(panel.GoalError):
            panel.rename_goal("/p", "gone", "x")
        with self.assertRaises(panel.GoalError):
            panel.toggle_goal_done("/p", "gone")
        goal = panel.Goal(id="gone", name="g", created=0.0)
        group = panel.Group(cwd="/p")
        text, key = panel.delete_goal_row([group], ("goal", group, goal))
        self.assertIn("no longer exists", text)
        self.assertIsNone(key)

    def test_create_keeps_colons_in_the_name(self):
        text, goal = panel.run_command("goal:create API: v2 rewrite", "/p")
        self.assertEqual(goal.name, "API: v2 rewrite")
        self.assertIn("created", text)

    def test_empty_name(self):
        for line in ("goal:create", "goal:create   "):
            text, goal = panel.run_command(line, "/p")
            self.assertEqual(text, "goal needs a name")
            self.assertIsNone(goal)
        self.assertFalse(self.file.exists())

    def test_duplicate_in_project_is_refused_case_insensitively(self):
        panel.run_command("goal:create Auth rework", "/p")
        text, goal = panel.run_command("goal:create auth REWORK", "/p")
        self.assertIn("already exists", text)
        self.assertIsNone(goal)
        self.assertEqual(len(panel.load_goals()[0]["/p"]), 1)
        text, goal = panel.run_command("goal:create auth rework", "/q")
        self.assertIsNotNone(goal)

    def test_unknown_command(self):
        text, goal = panel.run_command("nonsense stuff", "/p")
        self.assertEqual(text, "unknown command: nonsense")
        self.assertIsNone(goal)

    def test_no_project(self):
        text, goal = panel.run_command("goal:create x", None)
        self.assertTrue(text)
        self.assertIsNone(goal)

    def test_edit_keys(self):
        t = ""
        for ch in "ab":
            t, _ = panel.apply_edit_key(t, ch)
        self.assertEqual(t, "ab")
        self.assertEqual(panel.apply_edit_key(t, 127), ("a", None))
        self.assertEqual(panel.apply_edit_key(t, "\n"), ("ab", "submit"))
        self.assertEqual(panel.apply_edit_key(t, 27), ("ab", "cancel"))


class WiringTests(GoalStoreCase):
    def groups(self):
        groups = [panel.Group("/p", [session("a", "/p", 100.0)])]
        panel.attach_goals(groups)
        return groups

    def test_highlighted_row_is_bounds_checked(self):
        rows = [("group", None, None)]
        self.assertEqual(panel.highlighted_row(rows, 0), rows[0])
        self.assertIsNone(panel.highlighted_row(rows, 1))
        self.assertIsNone(panel.highlighted_row(rows, -1))
        self.assertIsNone(panel.highlighted_row([], 0))

    def test_command_creates_goal_in_highlighted_project(self):
        groups = self.groups()
        row = ("session", groups[0], groups[0].sessions[0])
        collapsed = {"/p", "/other"}
        message, key = panel.apply_command(
            "goal:create ship it", row, groups, collapsed
        )
        self.assertIn("created goal 'ship it'", message)
        self.assertEqual(collapsed, {"/other"})
        self.assertEqual(key[0], "goal")
        self.assertEqual(key[2], "/p")
        self.assertEqual(groups[0].goals[0].id, key[1])  # goals reloaded

    def test_command_without_a_project_selects_nothing(self):
        collapsed = {"/p"}
        message, key = panel.apply_command("goal:create x", None, [], collapsed)
        self.assertTrue(message)
        self.assertIsNone(key)
        self.assertEqual(collapsed, {"/p"})

    def test_failed_command_keeps_state(self):
        groups = self.groups()
        row = ("group", groups[0], None)
        message, key = panel.apply_command("nope", row, groups, {"/p"})
        self.assertIn("unknown command", message)
        self.assertIsNone(key)

    def test_toggle_goal_persists_collapse(self):
        groups = self.groups()
        gl = panel.create_goal("/p", "a")
        panel.attach_goals(groups)
        for value in (True, False, True):
            self.assertEqual(panel.toggle_goal(groups, groups[0], gl, value), "")
            self.assertEqual(panel.load_goals()[0]["/p"][0].collapsed, value)
            self.assertEqual(groups[0].goals[0].collapsed, value)

    def test_toggle_goal_reports_a_vanished_goal(self):
        groups = self.groups()
        gl = panel.create_goal("/p", "a")
        self.file.write_text('{"version": 1, "projects": {"/p": []}}')
        error = panel.toggle_goal(groups, groups[0], gl, True)
        self.assertIn("no longer exists", error)


class PromptTests(unittest.TestCase):
    def test_zwj_is_accepted(self):
        t = ""
        for ch in "\U0001F468\u200d\U0001F469":
            t, _ = panel.apply_edit_key(t, ch)
        self.assertEqual(t, "\U0001F468\u200d\U0001F469")

    def test_tail_trims_by_display_width(self):
        self.assertEqual(panel.tail_by_width("abcdef", 3), "def")
        self.assertEqual(panel.tail_by_width("漢字漢字", 5), "漢字")
        self.assertEqual(panel.tail_by_width("ab", 10), "ab")

    def test_prompt_survives_resize_and_draw_errors(self):
        import curses
        scr = mock.Mock()
        scr.getmaxyx.return_value = (5, 20)
        scr.addnstr.side_effect = curses.error
        scr.get_wch.side_effect = [curses.KEY_RESIZE, "x", "\n"]
        with mock.patch.object(panel.curses, "curs_set"):
            self.assertEqual(panel.prompt_line(scr, "f: "), "x")
        self.assertGreaterEqual(scr.getmaxyx.call_count, 3)


def goal(gid: str, name: str, created: float, sessions=(), collapsed=False):
    return panel.Goal(gid, name, created, False, collapsed, list(sessions))


class RowTests(unittest.TestCase):
    def group(self):
        a = session("a", "/p", 100.0)
        b = session("b", "/p", 300.0)
        c = session("c", "/p", 200.0)
        g = panel.Group("/p", [b, c, a])
        g.goals = [goal("g1", "Auth rework", 50.0, ["a", "c"]),
                   goal("g2", "Empty one", 250.0)]
        return g

    def kinds(self, g, flt="", **kw):
        return [
            (k, getattr(x, "id", None) if k != "more" else None)
            for k, _, x in panel.rows_for([g], flt, **kw)
        ]

    def test_interleave_by_recency(self):
        # b(300) loose, g2 created 250, g1 ranks by c(200); a is inside g1.
        self.assertEqual(
            self.kinds(self.group()),
            [("group", None), ("session", "b"), ("goal", "g2"),
             ("goal", "g1"), ("session", "c"), ("session", "a")],
        )

    def test_collapsed_goal_hides_its_sessions(self):
        g = self.group()
        g.goals[0].collapsed = True
        ids = [i for _, i in self.kinds(g)]
        self.assertNotIn("a", ids)
        self.assertIn("g1", ids)

    def test_row_key_follows_goal(self):
        g = self.group()
        rows = list(panel.rows_for([g], ""))
        key = panel.row_key(rows[3])
        self.assertEqual(key, ("goal", "g1", "/p"))
        g.goals[0].sessions = []
        g.goals[0].created = 999.0
        after = list(panel.rows_for([g], ""))
        self.assertEqual(panel.row_key(after[panel.cursor_for_key(after, key)]), key)
        self.assertEqual(panel.cursor_for_key(after, ("goal", "gone", "/p")), 0)

    def test_empty_goal_hint_is_stable_unselectable_and_mentions_n(self):
        g = self.group()
        items = list(panel.rows_for([g], ""))
        lines, starts, heights = panel.build_lines(items, 60, set(), "")
        hints = [(t, k, i) for t, k, i in lines if k == "hint"]
        self.assertEqual(len(hints), 1)
        text, _, idx = hints[0]
        self.assertEqual(idx, -1)  # never a cursor stop
        self.assertEqual(text.strip(), panel.goal_hint(g, g.goals[1]))
        goal_idx = [i for i, it in enumerate(items) if it[0] == "goal"][0]
        self.assertEqual(panel.goal_hint(g, g.goals[1]), panel.goal_hint(g, g.goals[1]))
        for h in panel.GOAL_HINTS:
            self.assertRegex(h, r"\bn\b")
        self.assertEqual(sum(1 for it in items if it[0] == "goal"), 2)
        self.assertGreaterEqual(heights[goal_idx], 1)

    def test_goal_line_layout(self):
        g = self.group()
        items = list(panel.rows_for([g], ""))
        lines, _, _ = panel.build_lines(items, 30, set(), "")
        goal_lines = [t for t, k, _ in lines if k == "goal"]
        self.assertTrue(goal_lines[0].startswith("   ▾ Empty one  (0)"))
        self.assertTrue(goal_lines[1].startswith("   ▾ Auth rework  (2)"))
        for t in goal_lines:
            self.assertLessEqual(panel.display_width(t), 29)
        nested = [t for t, k, _ in lines if k == "session"]
        self.assertTrue(nested[1].startswith("    "))  # inside the goal
        self.assertFalse(nested[0].startswith("  "))

    def test_goal_arrow_shares_the_harness_glyph_column(self):
        g = self.group()
        lines, _, _ = panel.build_lines(list(panel.rows_for([g], "")), 40, set(), "")
        loose = next(t for t, k, _ in lines if k == "session" and not t.startswith("  "))
        goal_line = next(t for t, k, _ in lines if k == "goal")
        glyph_col = loose.index(panel.HARNESS["pi"]["glyph"])
        self.assertEqual(goal_line.index("▾"), glyph_col)
        # name starts where a loose session's title starts
        self.assertEqual(goal_line.index("Empty"), glyph_col + 2)

    def test_empty_goal_hints_are_distinct_stable_and_age_ordered(self):
        g = panel.Group("/p", [])
        g.goals = [goal(f"id-{i}", f"G{i}", 10.0 + i) for i in range(len(panel.GOAL_HINTS))]
        hints = [panel.goal_hint(g, x) for x in g.goals]
        self.assertEqual(len(set(hints)), len(panel.GOAL_HINTS))
        self.assertEqual(hints, [panel.goal_hint(g, x) for x in g.goals])
        # a newer goal never changes an older goal's line
        older = hints[:3]
        g.goals.append(goal("newest", "N", 999.0))
        self.assertEqual(older, [panel.goal_hint(g, x) for x in g.goals[:3]])
        # more empty goals than lines wraps instead of failing
        self.assertIn(panel.goal_hint(g, g.goals[-1]), panel.GOAL_HINTS)
        self.assertGreaterEqual(len(panel.GOAL_HINTS), 12)

    def test_collapsed_goal_shows_arrow_and_no_hint(self):
        g = self.group()
        g.goals[1].collapsed = True
        lines, _, _ = panel.build_lines(list(panel.rows_for([g], "")), 40, set(), "")
        self.assertIn("▸ Empty one", [t for t, k, _ in lines if k == "goal"][0])
        self.assertFalse([1 for _, k, _ in lines if k == "hint"])

    def test_filter_matches_goal_name_and_expands(self):
        g = self.group()
        g.goals[0].collapsed = True
        got = self.kinds(g, "auth")
        self.assertEqual(
            got,
            [("group", None), ("goal", "g1"), ("session", "c"), ("session", "a")],
        )

    def test_filter_on_session_shows_only_its_goal_match(self):
        g = self.group()
        g.sessions[2].name = "zed task"  # session a
        got = self.kinds(g, "zed")
        self.assertIn(("goal", "g1"), got)
        self.assertIn(("session", "a"), got)
        self.assertNotIn(("session", "c"), got)
        self.assertNotIn(("goal", "g2"), got)

    def test_filter_without_matches_hides_project(self):
        self.assertEqual(self.kinds(self.group(), "zzz"), [])

    def test_goal_sessions_without_files_are_hidden(self):
        g = self.group()
        g.goals[1].sessions = ["missing"]
        self.assertEqual(len(panel.goal_members(g, g.goals[1])), 0)

    def test_list_lines_nest_goals(self):
        g = self.group()
        g.goals[0].done = True
        out = panel.list_lines([g])
        joined = "\n".join(out)
        self.assertRegex(joined, r"(?m)^  ▾ Empty one  \(0\)$")
        self.assertRegex(joined, r"(?m)^  ✓ Auth rework  \(2\)$")
        self.assertRegex(joined, r"(?m)^    .*  c$")
        self.assertRegex(joined, r"(?m)^  \S.*  b$")


class NewInGoalTests(GoalStoreCase):
    def setUp(self):
        super().setUp()
        self.pi = session("p1", "/p", 100.0)
        self.cl = session("c1", "/p", 300.0)
        self.cl.harness = "claude"
        self.loose = session("l1", "/p", 200.0)
        self.group = panel.Group("/p", [self.cl, self.loose, self.pi])
        self.goal = panel.create_goal("/p", "g")
        panel.mutate_goals(
            lambda d: d["projects"]["/p"][0]["sessions"].extend(["p1"])
        )
        panel.attach_goals([self.group])

    def test_plan_for_each_row_kind(self):
        g = self.group
        gid = self.goal.id
        table = (
            (("session", g, self.pi), ("pi", gid)),
            (("session", g, self.loose), ("pi", None)),
            (("group", g, None), ("claude", None)),
            # Goal row: its newest member's harness beats the project's.
            (("goal", g, g.goals[0]), ("pi", gid)),
        )
        for row, expected in table:
            with self.subTest(kind=row[0]):
                self.assertEqual(panel.new_session_plan(row), expected)

    def test_empty_goal_falls_back_to_project_newest(self):
        empty = panel.create_goal("/p", "empty")
        panel.attach_goals([self.group])
        gl = next(x for x in self.group.goals if x.id == empty.id)
        self.assertEqual(
            panel.new_session_plan(("goal", self.group, gl)),
            ("claude", empty.id),
        )

    def run_new(self, harness, goal_id, order):
        def spawn(cwd, sid, h):
            stored = panel.load_goals()[0]["/p"][0].sessions
            order.append((sid in stored, h))
            return "%9"

        with mock.patch.object(
            panel, "visible_layout", return_value=("%1", [object()], "%2")
        ), mock.patch.object(
            panel, "spawn_new_session_pane", side_effect=spawn
        ), mock.patch.object(
            panel, "_present_pane", return_value=None
        ), mock.patch.object(panel, "_write_state"):
            return panel.new_session(
                "%1", "/p", [self.group], {}, harness, goal_id
            )

    def test_id_is_filed_before_spawn_for_both_harnesses(self):
        for harness in ("pi", "claude"):
            order = []
            self.assertIsNone(self.run_new(harness, self.goal.id, order))
            self.assertEqual(order, [(True, harness)])

    def test_loose_start_leaves_goals_alone(self):
        before = self.file.read_text()
        self.assertIsNone(self.run_new("pi", None, []))
        self.assertEqual(self.file.read_text(), before)

    def test_starting_in_a_done_goal_reopens_it(self):
        def done(d):
            rec = d["projects"]["/p"][0]
            rec["done"], rec["collapsed"] = True, True

        panel.mutate_goals(done)
        self.assertIsNone(self.run_new("pi", self.goal.id, []))
        gl = panel.load_goals()[0]["/p"][0]
        self.assertEqual((gl.done, gl.collapsed), (False, False))

    def test_unreadable_store_still_spawns_with_warning(self):
        self.file.write_text("{ nope")
        panel._goals_cache = None
        spawned = []
        with mock.patch.object(
            panel, "visible_layout", return_value=("%1", [object()], "%2")
        ), mock.patch.object(
            panel, "spawn_new_session_pane",
            side_effect=lambda *a: spawned.append(a) or "%9",
        ), mock.patch.object(
            panel, "_present_pane", return_value=None
        ), mock.patch.object(panel, "_write_state"):
            warning = panel.new_session(
                "%1", "/p", [self.group], {}, "pi", self.goal.id
            )
        self.assertEqual(len(spawned), 1)
        self.assertIn("started outside the goal", warning)
        self.assertEqual(self.file.read_text(), "{ nope")  # never overwritten

    def test_vanished_goal_spawns_loose_with_warning(self):
        before = self.file.read_text()
        order = []
        warning = self.run_new("pi", "gone", order)
        self.assertEqual(
            warning, "goal no longer exists; started outside the goal"
        )
        self.assertEqual(order, [(False, "pi")])
        self.assertEqual(self.file.read_text(), before)

    def test_unwritable_store_spawns_loose_with_warning(self):
        order = []
        with mock.patch.object(
            panel, "file_session_in_goal",
            side_effect=OSError(errno.ENOSPC, "No space left on device"),
        ):
            warning = self.run_new("pi", self.goal.id, order)
        self.assertEqual(
            warning,
            "could not save goal: No space left on device; "
            "started outside the goal",
        )
        self.assertEqual(order, [(False, "pi")])

    def test_goal_record_without_sessions_key_is_filed(self):
        def strip(d):
            del d["projects"]["/p"][0]["sessions"]

        panel.mutate_goals(strip)
        self.assertIsNone(self.run_new("pi", self.goal.id, []))
        stored = json.loads(self.file.read_text())["projects"]["/p"][0]
        self.assertEqual(len(stored["sessions"]), 1)


class NestingTests(unittest.TestCase):
    def group(self):
        a, b, c = (session(i, "/p", t) for i, t in
                   (("a", 100.0), ("b", 300.0), ("c", 200.0)))
        g = panel.Group("/p", [b, c, a])
        g.goals = [goal("g1", "G", 50.0, ["a", "b", "c", "ghost"])]
        return g

    def test_members_nest_newest_first_and_ghosts_hidden(self):
        rows = list(panel.rows_for([self.group()], ""))
        self.assertEqual(
            [(k, getattr(x, "id", None)) for k, _, x in rows],
            [("group", None), ("goal", "g1"), ("session", "b"),
             ("session", "c"), ("session", "a")],
        )

    def test_goal_line_shows_count_and_newest_age(self):
        g = self.group()
        lines, _, _ = panel.build_lines(
            list(panel.rows_for([g], "")), 40, set(), ""
        )
        goal_line = next(t for t, k, _ in lines if k == "goal")
        self.assertIn("(3)", goal_line)
        self.assertTrue(goal_line.rstrip().endswith(panel.rel_time(300.0)))

    def test_cursor_falls_back_to_the_collapsed_goal(self):
        g = self.group()
        key = ("session", "c", "/p")
        g.goals[0].collapsed = True
        rows = list(panel.rows_for([g], ""))
        self.assertEqual(rows[panel.cursor_for_key(rows, key)][0], "goal")

    def test_footer_by_row_kind(self):
        g = self.group()
        goal_items = panel.footer_items(("goal", g, g.goals[0]))
        self.assertIn("⏎ toggle", goal_items)
        self.assertNotIn("⏎ present", goal_items)
        self.assertEqual(
            panel.footer_items(("session", g, g.sessions[0])),
            panel.FOOTER_ITEMS,
        )
        self.assertEqual(panel.footer_items(None), panel.FOOTER_ITEMS)
        self.assertEqual(
            goal_items,
            ("⏎ toggle", "n new", "R rename", "x done", "D delete"),
        )
        self.assertEqual(
            panel.footer_items(("goal_more", g, g.goals[0])),
            panel.FOOTER_ITEMS,
        )
        group_items = panel.footer_items(("group", g, None))
        self.assertEqual(
            group_items,
            tuple("⏎ toggle" if i == "⏎ present" else i for i in panel.FOOTER_ITEMS),
        )
        self.assertEqual(group_items[0], "↑/↓ move")
        self.assertIn("⏎ toggle", group_items)
        self.assertNotIn("⏎ present", group_items)
        line = panel.footer_lines(goal_items, 80)[0]
        self.assertIn("⏎ toggle  n new", line)


class InheritanceTests(GoalStoreCase):
    """D17: Pi successors join their parent's goal at load time."""

    def setUp(self):
        super().setUp()
        self.root = Path(self.tmpdir.name)
        self.pi = self.root / "pi"
        (self.pi / "project").mkdir(parents=True)
        for name, value in (
            ("PI_SESSIONS_DIR", str(self.pi)),
            ("CLAUDE_SESSIONS_DIR", str(self.root / "no-claude")),
        ):
            patcher = mock.patch.object(panel, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = mock.patch.object(panel, "process_table", return_value=[])
        patcher.start()
        self.addCleanup(patcher.stop)
        self.cwd = "/work/proj"
        self.n = 0

    def write(self, sid, parent=None):
        self.n += 1
        ts = time.strftime("%Y-%m-%dT%H:%M:%S.000Z",
                           time.gmtime(time.time() - 1000 + self.n))
        header = {"type": "session", "id": sid, "cwd": self.cwd,
                  "timestamp": ts}
        if parent:
            header["parentSession"] = str(self.pi / "project" / f"{parent}.jsonl")
        lines = [header,
                 {"type": "message", "timestamp": ts,
                  "message": {"role": "user",
                              "content": [{"type": "text", "text": sid}]}},
                 {"type": "message", "timestamp": ts,
                  "message": {"role": "assistant",
                              "content": [{"type": "text", "text": "ok"}],
                              "stopReason": "stop"}}]
        (self.pi / "project" / f"{sid}.jsonl").write_text(
            "".join(json.dumps(o) + "\n" for o in lines))

    def file_goal(self, name, *ids):
        goal = panel.create_goal(self.cwd, name)
        for sid in ids:
            panel.file_session_in_goal(self.cwd, goal.id, sid)
        return goal

    def group(self):
        (g,) = panel.load_groups()
        return g

    def members(self, g, goal_id):
        goal = next(x for x in g.goals if x.id == goal_id)
        return {s.id for s in panel.goal_members(g, goal)}

    def test_child_joins_parents_goal_without_writing(self):
        self.write("root")
        goal = self.file_goal("Auth", "root")
        self.write("child", parent="root")
        before = self.file.read_text()
        g = self.group()
        self.assertEqual(self.members(g, goal.id), {"root", "child"})
        self.assertEqual(self.file.read_text(), before)
        self.assertEqual(panel.goal_of_session(g, "child").id, goal.id)
        self.assertNotIn("child", panel.load_goals()[0][self.cwd][0].sessions)

    def test_sibling_successors_both_join(self):
        # A handoff from a handoff records the original as parent.
        self.write("root")
        goal = self.file_goal("Auth", "root")
        self.write("first", parent="root")
        self.write("second", parent="root")
        self.assertEqual(self.members(self.group(), goal.id),
                         {"root", "first", "second"})

    def test_multi_hop_chain_joins(self):
        self.write("a")
        goal = self.file_goal("Auth", "a")
        self.write("b", parent="a")
        self.write("c", parent="b")
        self.write("d", parent="c")
        self.assertEqual(self.members(self.group(), goal.id),
                         {"a", "b", "c", "d"})

    def test_explicit_membership_wins(self):
        self.write("root")
        one = self.file_goal("One", "root")
        self.write("child", parent="root")
        two = self.file_goal("Two", "child")
        g = self.group()
        self.assertEqual(self.members(g, one.id), {"root"})
        self.assertEqual(self.members(g, two.id), {"child"})

    def test_grandchild_follows_nearest_explicit_ancestor(self):
        self.write("root")
        one = self.file_goal("One", "root")
        self.write("mid", parent="root")
        two = self.file_goal("Two", "mid")
        self.write("leaf", parent="mid")
        g = self.group()
        self.assertEqual(self.members(g, one.id), {"root"})
        self.assertEqual(self.members(g, two.id), {"mid", "leaf"})

    def test_loose_parent_keeps_successor_loose(self):
        self.write("root")
        goal = self.file_goal("Auth")
        self.write("child", parent="root")
        g = self.group()
        self.assertEqual(self.members(g, goal.id), set())
        self.assertIsNone(panel.goal_of_session(g, "child"))

    def test_missing_parent_file_stays_loose(self):
        goal = self.file_goal("Auth", "gone")
        self.write("orphan", parent="gone")
        g = self.group()
        self.assertEqual(self.members(g, goal.id), set())

    def test_cycle_terminates(self):
        self.write("x", parent="y")
        self.write("y", parent="x")
        self.write("z", parent="x")
        goal = self.file_goal("Auth", "x")
        g = self.group()
        self.assertEqual(self.members(g, goal.id), {"x", "y", "z"})
        self.write("p", parent="q")
        self.write("q", parent="p")
        self.file_goal("Other")
        g = self.group()
        self.assertEqual(len(g.sessions), 5)
        self.assertEqual(self.members(g, goal.id), {"x", "y", "z"})

    def test_deep_chain_keeps_the_tip(self):
        self.write("s0")
        goal = self.file_goal("Auth", "s0")
        for n in range(1, 12):
            self.write(f"s{n}", parent=f"s{n - 1}")
        g = self.group()
        self.assertEqual(self.members(g, goal.id), {f"s{n}" for n in range(12)})

    def test_deleting_the_goal_flattens_successors(self):
        self.write("root")
        goal = self.file_goal("Auth", "root")
        self.write("child", parent="root")

        def drop(data):
            data["projects"][self.cwd] = [
                r for r in data["projects"][self.cwd] if r["id"] != goal.id]

        panel.mutate_goals(drop)
        g = self.group()
        self.assertEqual(g.goals, [])
        self.assertEqual(panel.goal_member_ids(g), set())

    def test_rows_and_list_show_successor_inside_goal(self):
        self.write("root")
        goal = self.file_goal("Auth", "root")
        self.write("child", parent="root")
        g = self.group()
        self.assertEqual(panel.goal_member_ids(g), {"root", "child"})
        rows = list(panel.rows_for([g], ""))
        kinds = [(k, getattr(x, "id", None)) for k, _, x in rows
                 if k in ("goal", "session")]
        self.assertEqual(kinds[0], ("goal", goal.id))
        self.assertEqual({i for k, i in kinds if k == "session"},
                         {"root", "child"})
        joined = "\n".join(panel.list_lines([g]))
        self.assertRegex(joined, r"(?m)^  ▾ Auth  \(2\)$")
        self.assertRegex(joined, r"(?m)^    .*  child$")
        self.assertEqual(len(list(panel.rows_for([g], "Auth"))), len(rows))

    def test_cached_goals_are_not_mutated(self):
        self.write("root")
        self.file_goal("Auth", "root")
        self.write("child", parent="root")
        self.group()
        self.assertEqual(panel.load_goals()[0][self.cwd][0].sessions, ["root"])


class DenseTests(unittest.TestCase):
    """Two-level limits (D20) and the filter override (D25)."""

    def sess(self, sid, ts, running=False):
        s = session(sid, "/p", ts)
        if running:
            s.status = "running"
        return s

    def group(self, sessions, goals=()):
        g = panel.Group("/p", sorted(sessions, key=lambda s: -s.last_ts))
        g.goals = list(goals)
        return g

    def ids(self, g, flt="", **kw):
        out = []
        for k, _, x in panel.rows_for([g], flt, **kw):
            if k == "session":
                out.append(x.id)
            elif k == "goal":
                out.append("G:" + x.id)
            elif k in ("more", "goal_more"):
                out.append(k)
        return out

    def test_project_limit_counts_goal_as_one(self):
        loose = [self.sess(f"l{i}", 1000 - i) for i in range(4)]
        inside = [self.sess(f"m{i}", 10 + i) for i in range(4)]
        gl = goal("g", "G", 1.0, [s.id for s in inside])
        g = self.group(loose + inside, [gl])
        # 4 loose + 1 goal = 5 items: no project more row.
        self.assertNotIn("more", self.ids(g))
        g = self.group(loose + inside + [self.sess("x", 500)], [gl])
        # 6 items: oldest top-level item (the goal) hidden behind "more".
        ids = self.ids(g)
        self.assertEqual(ids[-1], "more")
        self.assertNotIn("G:g", ids)

    def test_running_loose_pulled_past_project_limit(self):
        loose = [self.sess(f"l{i}", 1000 - i) for i in range(7)]
        loose[6].status = "running"
        ids = self.ids(self.group(loose))
        self.assertIn("l6", ids)
        self.assertNotIn("l5", ids)
        rows = list(panel.rows_for([self.group(loose)], ""))
        self.assertEqual(rows[-1][2], 1)

    def test_goal_with_running_member_pulled_past_project_limit(self):
        loose = [self.sess(f"l{i}", 1000 - i) for i in range(6)]
        m = [self.sess("m0", 5, running=True), self.sess("m1", 4)]
        gl = goal("g", "G", 1.0, ["m0", "m1"])
        ids = self.ids(self.group(loose + m, [gl]))
        self.assertEqual(ids.count("G:g"), 1)
        self.assertIn("m0", ids)
        self.assertNotIn("l5", ids)

    def test_goal_limit_and_more_row(self):
        m = [self.sess(f"m{i}", 100 - i) for i in range(5)]
        gl = goal("g", "G", 1.0, [s.id for s in m])
        g = self.group(m, [gl])
        self.assertEqual(
            self.ids(g), ["G:g", "m0", "m1", "m2", "goal_more"]
        )
        rows = list(panel.rows_for([g], ""))
        self.assertEqual(rows[-1][2], (gl, 2))
        full = self.ids(g, expanded={"g"})
        self.assertEqual(full, ["G:g", "m0", "m1", "m2", "m3", "m4", "goal_more"])

    def test_running_pulled_past_goal_limit_in_order(self):
        m = [self.sess(f"m{i}", 100 - i) for i in range(6)]
        m[4].status = "running"
        gl = goal("g", "G", 1.0, [s.id for s in m])
        ids = self.ids(self.group(m, [gl]))
        self.assertEqual(ids, ["G:g", "m0", "m1", "m2", "m4", "goal_more"])
        rows = list(panel.rows_for([self.group(m, [gl])], ""))
        self.assertEqual(rows[-1][2][1], 2)

    def test_filter_ignores_both_limits_and_expands_goals(self):
        loose = [self.sess(f"l{i}", 1000 - i) for i in range(7)]
        m = [self.sess(f"m{i}", 100 - i) for i in range(5)]
        gl = goal("g", "Work", 1.0, [s.id for s in m], collapsed=True)
        g = self.group(loose + m, [gl])
        ids = self.ids(g, "Work")
        self.assertEqual(ids, ["G:g"] + [f"m{i}" for i in range(5)])
        ids = self.ids(g, "m4")
        self.assertEqual(ids, ["G:g", "m4"])
        self.assertIn("l6", self.ids(g, "l"))
        self.assertNotIn("more", self.ids(g, "l"))

    def lines(self, g, **kw):
        items = list(panel.rows_for([g], ""))
        return panel.build_lines(items, 30, set(), "", **kw)[0]

    def test_collapsed_goal_rolls_up_running_mark(self):
        m = [self.sess("m0", 5, running=True), self.sess("m1", 4)]
        gl = goal("g", "Name", 1.0, ["m0", "m1"], collapsed=True)
        text = self.lines(self.group(m, [gl]))[1][0]
        self.assertTrue(text.startswith("   ▸ ● Name"), text)
        self.assertLessEqual(panel.display_width(text), 29)
        gl.collapsed = False
        text = self.lines(self.group(m, [gl]))[1][0]
        self.assertTrue(text.startswith("   ▾ Name"), text)
        gl.collapsed = True
        m[0].status = "idle"
        text = self.lines(self.group(m, [gl]))[1][0]
        self.assertNotIn("●", text)

    def test_goal_more_lines_toggle_label(self):
        m = [self.sess(f"m{i}", 100 - i) for i in range(5)]
        gl = goal("g", "G", 1.0, [s.id for s in m])
        g = self.group(m, [gl])
        self.assertTrue(self.lines(g)[-1][0].strip().startswith("… 2 more"))
        self.assertTrue(
            self.lines(g, expanded={"g"})[-1][0].strip().startswith("… show fewer")
        )

    def test_goal_more_key_and_cursor_follow(self):
        m = [self.sess(f"m{i}", 100 - i) for i in range(5)]
        gl = goal("g", "G", 1.0, [s.id for s in m])
        g = self.group(m, [gl])
        rows = list(panel.rows_for([g], ""))
        key = panel.row_key(rows[-1])
        self.assertEqual(key, ("goal_more", "g", "/p"))
        # Survives a re-sort that adds a newer loose session above.
        g2 = self.group(m + [self.sess("n", 900)], [gl])
        rows2 = list(panel.rows_for([g2], ""))
        self.assertEqual(rows2[panel.cursor_for_key(rows2, key)][0], "goal_more")
        # A session hidden behind the goal's more row lands on it.
        hidden = ("session", "m4", "/p")
        self.assertEqual(rows[panel.cursor_for_key(rows, hidden)][0], "goal_more")
        # Once the goal is collapsed there is no more row: land on the goal.
        gl.collapsed = True
        rows3 = list(panel.rows_for([g], ""))
        self.assertEqual(rows3[panel.cursor_for_key(rows3, hidden)][0], "goal")
        self.assertEqual(panel.new_session_plan(rows[-1])[1], "g")


if __name__ == "__main__":
    unittest.main(verbosity=2)


class HarnessPickerTests(unittest.TestCase):
    BOTH = ["pi", "claude"]

    def row(self, kind):
        g = panel.Group(cwd="/p", sessions=[session("a", "/p", 1.0)])
        return (kind, g, None)

    def test_decision_by_row_kind(self):
        for kind, expected in (
            ("group", True), ("goal", True), ("session", False),
            ("goal_more", False), ("more", False),
        ):
            self.assertEqual(
                panel.should_pick_harness(self.row(kind), self.BOTH),
                expected, kind,
            )
        self.assertFalse(panel.should_pick_harness(None, self.BOTH))

    def test_single_harness_skips(self):
        self.assertFalse(panel.should_pick_harness(self.row("group"), ["pi"]))
        self.assertFalse(panel.should_pick_harness(self.row("group"), []))

    def test_preselection_matches_inference(self):
        self.assertEqual(panel.picker_start("claude", self.BOTH), 1)
        self.assertEqual(panel.picker_start("pi", self.BOTH), 0)
        with mock.patch.object(panel, "default_harness", return_value="claude"):
            self.assertEqual(panel.picker_start(None, self.BOTH), 1)
        self.assertEqual(panel.picker_start("claude", ["pi"]), 0)

    def test_installed_harnesses_follow_harness_order(self):
        with mock.patch.object(
            panel.shutil, "which", side_effect=lambda k: k == "claude"
        ):
            self.assertEqual(panel.installed_harnesses(), ["claude"])
        with mock.patch.object(panel.shutil, "which", return_value="/x"):
            self.assertEqual(panel.installed_harnesses(), list(panel.HARNESS))

    def test_key_handling(self):
        import curses
        key = panel.picker_key
        self.assertEqual(key(0, 2, ord("j")), (1, ""))
        self.assertEqual(key(1, 2, curses.KEY_DOWN), (1, ""))
        self.assertEqual(key(1, 2, ord("k")), (0, ""))
        self.assertEqual(key(0, 2, curses.KEY_UP), (0, ""))
        self.assertEqual(key(1, 2, 10), (1, "pick"))
        self.assertEqual(key(0, 2, ord("2")), (1, "pick"))
        self.assertEqual(key(0, 2, ord("3"))[1], "")
        self.assertEqual(key(0, 2, 27)[1], "cancel")
        self.assertEqual(key(0, 2, ord("q"))[1], "cancel")
        self.assertEqual(key(0, 2, ord("x")), (0, ""))

    def run_picker(self, keys, size=(24, 30), index=0):
        import curses
        scr = mock.Mock()
        scr.getmaxyx.return_value = size
        win = mock.Mock()
        win.getch.side_effect = keys
        with mock.patch.object(panel.curses, "newwin", return_value=win) as nw:
            result = panel.harness_picker(scr, self.BOTH, index)
        return result, scr, win, nw

    def test_picker_move_and_enter(self):
        result, _, win, _ = self.run_picker([ord("j"), 10])
        self.assertEqual(result, "claude")
        drawn = " ".join(c.args[2] for c in win.addnstr.call_args_list)
        self.assertIn("π Pi", drawn)
        self.assertIn("✳ Claude Code", drawn)

    def test_picker_digit_pick_and_enter_on_preselected(self):
        self.assertEqual(self.run_picker([ord("2")])[0], "claude")
        self.assertEqual(self.run_picker([ord("1")], index=1)[0], "pi")
        self.assertEqual(self.run_picker([10], index=1)[0], "claude")

    def test_picker_cancel(self):
        self.assertIsNone(self.run_picker([27])[0])
        self.assertIsNone(self.run_picker([ord("j"), ord("q")])[0])

    def test_picker_survives_resize_and_ticks(self):
        import curses
        result, scr, _, nw = self.run_picker([curses.KEY_RESIZE, -1, 10])
        self.assertEqual(result, "pi")
        self.assertEqual(nw.call_count, 3)
        scr.getch.assert_not_called()  # keys come from the box, not stdscr

    def test_picker_ctrl_c_propagates_and_restores(self):
        scr = mock.Mock()
        scr.getmaxyx.return_value = (24, 30)
        win = mock.Mock()
        win.getch.side_effect = KeyboardInterrupt
        with mock.patch.object(panel.curses, "newwin", return_value=win):
            with self.assertRaises(KeyboardInterrupt):
                panel.harness_picker(scr, self.BOTH, 0)
        scr.touchwin.assert_called()

    def test_picker_falls_back_when_it_cannot_draw(self):
        import curses
        scr = mock.Mock()
        scr.getmaxyx.return_value = (24, 30)
        with mock.patch.object(panel.curses, "newwin", side_effect=curses.error):
            self.assertEqual(panel.harness_picker(scr, self.BOTH, 1), "claude")
        scr.getch.assert_not_called()
        scr.getmaxyx.return_value = (2, 4)  # too small for any box
        with mock.patch.object(panel.curses, "newwin") as nw:
            self.assertEqual(panel.harness_picker(scr, self.BOTH, 0), "pi")
        nw.assert_not_called()
