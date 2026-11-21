#!/usr/bin/env python3
"""P5 move-session store, box and row tests."""
from __future__ import annotations

import curses
import json
import os
import unicodedata
import unittest
from unittest import mock

from tests.goal_case import GoalStoreCase, panel, session


class MoveCase(GoalStoreCase):
    def setUp(self):
        super().setUp()
        self.a = session("a", "/p", 400.0)
        self.b = session("b", "/p", 300.0)
        self.c = session("c", "/p", 200.0)
        self.group = panel.Group("/p", [self.a, self.b, self.c])
        self.goal = panel.create_goal("/p", "Goal")
        self.other = panel.create_goal("/p", "Other")
        self.slice = panel.create_slice("/p", self.goal.id, "Slice")
        self.other_slice = panel.create_slice(
            "/p", self.other.id, "Other slice"
        )
        panel.attach_goals([self.group])

    def members(self, goal_id):
        goal = next(x for x in self.group.goals if x.id == goal_id)
        return {s.id for s in panel.goal_members(self.group, goal)}

    def node(self, goal_id):
        return next(x for x in self.group.goals if x.id == goal_id)

    def loose_ids(self):
        return json.loads(self.file.read_text()).get("loose", {}).get("/p", [])


class MoveStoreTests(MoveCase):
    def test_move_in_and_out_to_explicit_loose(self):
        self.assertTrue(panel.move_session("/p", "a", self.goal.id))
        panel.attach_goals([self.group])
        self.assertEqual(self.members(self.goal.id), {"a"})
        self.assertTrue(panel.move_session("/p", "a", None))
        panel.attach_goals([self.group])
        self.assertEqual(self.members(self.goal.id), set())
        self.assertEqual(self.loose_ids(), ["a"])
        self.assertIsNone(panel.goal_of_session(self.group, "a"))

    def test_move_between_direct_goals(self):
        panel.file_session_in_goal("/p", self.goal.id, "a")
        panel.attach_goals([self.group])
        panel.move_session("/p", "a", self.other.id)
        panel.attach_goals([self.group])
        self.assertEqual(self.members(self.goal.id), set())
        self.assertEqual(self.members(self.other.id), {"a"})

    def test_move_into_a_slice(self):
        panel.move_session("/p", "a", self.slice.id)
        panel.attach_goals([self.group])
        self.assertEqual(self.members(self.slice.id), {"a"})
        self.assertEqual(self.members(self.goal.id), set())

    def test_move_from_slice_to_parent_goal(self):
        panel.file_session_in_goal("/p", self.slice.id, "a")
        panel.attach_goals([self.group])
        panel.move_session("/p", "a", self.goal.id)
        panel.attach_goals([self.group])
        self.assertEqual(self.members(self.slice.id), set())
        self.assertEqual(self.members(self.goal.id), {"a"})

    def test_move_from_slice_to_another_slice(self):
        panel.file_session_in_goal("/p", self.slice.id, "a")
        panel.attach_goals([self.group])
        panel.move_session("/p", "a", self.other_slice.id)
        panel.attach_goals([self.group])
        self.assertEqual(self.members(self.slice.id), set())
        self.assertEqual(self.members(self.other_slice.id), {"a"})

    def test_done_target_reopens_and_expands(self):
        panel.toggle_goal_done("/p", self.slice.id)
        panel.attach_goals([self.group])
        self.assertTrue(self.node(self.slice.id).done)
        self.assertTrue(self.node(self.slice.id).collapsed)
        panel.move_session("/p", "a", self.slice.id)
        panel.attach_goals([self.group])
        node = self.node(self.slice.id)
        self.assertFalse(node.done)
        self.assertFalse(node.collapsed)
        self.assertEqual(self.members(self.slice.id), {"a"})

    def test_moving_a_loose_session_into_a_goal_clears_its_loose_mark(self):
        panel.move_session("/p", "a", None)
        self.assertEqual(self.loose_ids(), ["a"])
        panel.move_session("/p", "a", self.goal.id)
        stored = json.loads(self.file.read_text())
        self.assertNotIn("a", stored.get("loose", {}).get("/p", []))
        self.assertNotIn("/p", stored.get("loose", {}))
        self.assertNotIn("loose", stored)

    def test_corrupt_store_refuses_and_leaves_file_byte_identical(self):
        self.file.write_text("{ not json")
        before = self.file.read_bytes()
        panel.state._goals_cache = None
        with self.assertRaises(panel.GoalStoreError):
            panel.move_session("/p", "a", self.goal.id)
        self.assertEqual(self.file.read_bytes(), before)

    def test_no_op_writes_nothing(self):
        panel.move_session("/p", "a", self.goal.id)
        before = self.file.read_bytes()
        stamp = os.stat(self.file).st_mtime_ns
        self.assertFalse(panel.move_session("/p", "a", self.goal.id))
        self.assertEqual(self.file.read_bytes(), before)
        self.assertEqual(os.stat(self.file).st_mtime_ns, stamp)

    def test_no_op_explicit_loose_writes_nothing(self):
        panel.move_session("/p", "a", None)
        before = self.file.read_bytes()
        stamp = os.stat(self.file).st_mtime_ns
        self.assertFalse(panel.move_session("/p", "a", None))
        self.assertEqual(self.file.read_bytes(), before)
        self.assertEqual(os.stat(self.file).st_mtime_ns, stamp)

    def test_goal_record_without_sessions_validates_and_accepts_a_move(self):
        data = {
            "version": 1,
            "projects": {"/p": [
                {"id": "bare", "name": "Bare", "created": 1.0},
            ]},
        }
        self.file.write_text(json.dumps(data))
        panel.state._goals_cache = None
        goals, unreadable = panel.load_goals()
        self.assertFalse(unreadable)
        self.assertEqual(goals["/p"][0].sessions, [])
        self.assertTrue(panel.move_session("/p", "a", "bare"))
        stored = json.loads(self.file.read_text())["projects"]["/p"][0]
        self.assertEqual(stored["sessions"], ["a"])

    def test_move_rereads_the_store_first(self):
        panel.load_goals()  # cache the file before the other writer lands
        data = json.loads(self.file.read_text())
        data["projects"]["/p"].append({
            "id": "theirs", "name": "Theirs", "created": 1,
            "done": False, "collapsed": False, "sessions": [],
        })
        self.file.write_text(json.dumps(data))
        panel.move_session("/p", "a", self.goal.id)
        records = json.loads(self.file.read_text())["projects"]["/p"]
        names = {r["name"] for r in records}
        self.assertEqual(names, {"Goal", "Other", "Slice", "Other slice",
                                 "Theirs"})
        stored = next(r for r in records if r["id"] == self.goal.id)
        self.assertIn("a", stored["sessions"])

    def test_moving_out_of_a_slice_clears_old_container(self):
        panel.file_session_in_goal("/p", self.slice.id, "a")
        panel.file_session_in_goal("/p", self.other.id, "a")
        panel.attach_goals([self.group])
        panel.move_session("/p", "a", self.slice.id)
        panel.attach_goals([self.group])
        self.assertEqual(self.members(self.slice.id), {"a"})
        self.assertEqual(self.members(self.other.id), set())


class MoveRowTests(MoveCase):
    def test_only_session_rows_move(self):
        before = self.file.read_bytes()
        rows = (
            ("group", self.group, None),
            ("goal", self.group, self.goal),
            ("slice", self.group, self.slice),
            ("more", self.group, (2, False)),
            ("goal_more", self.group, (self.goal, 2, False)),
            ("slice_more", self.group, (self.slice, 2, False)),
        )
        for row in rows:
            with self.subTest(kind=row[0]):
                message, key = panel.move_session_row(
                    [self.group], row, (panel.MOVE_LOOSE, None)
                )
                self.assertEqual(message, panel.NO_SESSION_ROW)
                self.assertIsNone(key)
        self.assertEqual(self.file.read_bytes(), before)

    def test_session_row_move_returns_its_key(self):
        row = ("session", self.group, self.a)
        message, key = panel.move_session_row(
            [self.group], row, (panel.MOVE_GOAL, self.goal)
        )
        self.assertIn("moved", message)
        self.assertEqual(key, ("session", "a", "/p"))
        self.assertIn("a", self.members(self.goal.id))

    def test_vanished_target_refuses_with_a_message_and_no_write(self):
        before = self.file.read_bytes()
        stamp = os.stat(self.file).st_mtime_ns
        gone = panel.Goal("gone", "Gone", 0.0)
        message, key = panel.move_session_row(
            [self.group], ("session", self.group, self.a),
            (panel.MOVE_GOAL, gone),
        )
        self.assertIn("goal no longer exists", message)
        self.assertIsNone(key)
        self.assertEqual(self.file.read_bytes(), before)
        self.assertEqual(os.stat(self.file).st_mtime_ns, stamp)

    def test_store_error_refuses_with_a_message_and_no_write(self):
        self.file.write_text("{ not json")
        before = self.file.read_bytes()
        stamp = os.stat(self.file).st_mtime_ns
        message, key = panel.move_session_row(
            [self.group], ("session", self.group, self.a),
            (panel.MOVE_GOAL, self.goal),
        )
        self.assertIn(panel.GOALS_UNREADABLE, message)
        self.assertIsNone(key)
        self.assertEqual(self.file.read_bytes(), before)
        self.assertEqual(os.stat(self.file).st_mtime_ns, stamp)

    def test_second_move_reports_already_there_and_writes_nothing(self):
        row = ("session", self.group, self.a)
        panel.move_session_row(
            [self.group], row, (panel.MOVE_GOAL, self.goal)
        )
        before = self.file.read_bytes()
        stamp = os.stat(self.file).st_mtime_ns
        message, key = panel.move_session_row(
            [self.group], row, (panel.MOVE_GOAL, self.goal)
        )
        self.assertIn("already there", message)
        self.assertEqual(key, ("session", "a", "/p"))
        self.assertEqual(self.file.read_bytes(), before)
        self.assertEqual(os.stat(self.file).st_mtime_ns, stamp)

    def test_new_goal_creates_top_level_and_moves(self):
        row = ("session", self.group, self.a)
        message, key = panel.move_to_new_goal(
            [self.group], row, "  Phase   two "
        )
        self.assertIn("new goal 'Phase two'", message)
        self.assertEqual(key, ("session", "a", "/p"))
        records = panel.load_goals()[0]["/p"]
        created = next(x for x in records if x.name == "Phase two")
        self.assertEqual(created.parent, "")
        self.assertIn("a", self.members(created.id))

    def test_new_goal_refuses_an_empty_name_without_writing(self):
        before = self.file.read_bytes()
        message, key = panel.move_to_new_goal(
            [self.group], ("session", self.group, self.a), "   "
        )
        self.assertIn("needs a name", message)
        self.assertIsNone(key)
        self.assertEqual(self.file.read_bytes(), before)

    def test_footer_offers_move_on_sessions_only(self):
        row = ("session", self.group, self.a)
        self.assertIn("m move", panel.footer_items(row))
        self.assertNotIn(
            "m move", panel.footer_items(("goal", self.group, self.goal))
        )
        self.assertNotIn(
            "m move", panel.footer_items(("group", self.group, None))
        )


class MoveKeyTests(MoveCase):
    """`m` dispatch keeps the panel's pane target (P6 F1)."""

    def test_move_pick_keeps_the_panel_target(self):
        app = panel.main.App(mock.Mock(), "%2")
        app.groups = [self.group]
        with mock.patch.object(panel.view, "draw", return_value=(0, 0, {})):
            app.draw()  # the loop always draws before a key arrives
        app.cursor = next(
            i for i, row in enumerate(app.rows)
            if row[0] == "session" and row[2].id == "a"
        )
        with mock.patch.object(
            panel.boxes, "move_box",
            return_value=(panel.MOVE_GOAL, self.goal),
        ), mock.patch.object(
            panel.status, "load_groups", return_value=[self.group]
        ):
            self.assertTrue(app.dispatch_key(ord("m"), {}))
        self.assertEqual(app.target, "%2")
        self.assertIn("a", self.members(self.goal.id))


class MoveBoxTests(MoveCase):
    def test_targets_list_top_level_new_goal_then_the_tree(self):
        targets = panel.move_targets(self.group)
        self.assertEqual(targets[0][:2], (panel.MOVE_LOOSE, None))
        self.assertEqual(targets[1][:2], (panel.MOVE_NEW, None))
        middle = [
            (kind, goal.id if goal else None)
            for kind, goal, _ in targets[2:]
        ]
        self.assertEqual(middle, [
            (panel.MOVE_GOAL, self.goal.id),
            (panel.MOVE_GOAL, self.slice.id),
            (panel.MOVE_GOAL, self.other.id),
            (panel.MOVE_GOAL, self.other_slice.id),
        ])

    def test_lines_share_marker_and_name_columns_and_start_on_current(self):
        state = panel.move_box_start(self.group, self.a)
        lines = panel.move_box_lines(state)
        self.assertEqual([text for text, _i, _k in lines], [
            " ✓ Top level",
            " + New goal",
            "",
            "   Goal",
            "   └ Slice",
            "   Other",
            "   └ Other slice",
        ])
        self.assertEqual(lines[2][2], panel.PICKER_DIVIDER)
        self.assertIsNone(lines[2][1])
        # One leading blank column keeps every row off the left border.
        self.assertTrue(all(text == "" or text[0] == " " for text in [
            line[0] for line in lines
        ]))
        # `⌂`, `+` and `✓` share the marker column; the words after them share
        # the name column.
        self.assertEqual(lines[0][0][1], "✓")
        self.assertEqual(lines[1][0][1], "+")
        self.assertEqual(lines[4][0][3], "└")
        self.assertEqual(lines[6][0][3], "└")
        self.assertEqual(lines[0][0][3], "T")
        self.assertEqual(lines[1][0][3], "N")
        # Every goal name and every slice connector share the name column.
        self.assertEqual(lines[3][0][3], "G")
        self.assertEqual(lines[5][0][3], "O")
        self.assertEqual(
            next(text for text, index, _kind in lines
                 if index == state["index"]),
            " ✓ Top level",
        )

    def test_lines_mark_the_current_slice_not_its_goal(self):
        panel.file_session_in_goal("/p", self.slice.id, "a")
        panel.attach_goals([self.group])
        state = panel.move_box_start(self.group, self.a)
        lines = [text for text, _i, _k in panel.move_box_lines(state)]
        self.assertEqual(lines, [
            " ⌂ Top level",
            " + New goal",
            "",
            "   Goal",
            " ✓ └ Slice",
            "   Other",
            "   └ Other slice",
        ])
        self.assertEqual(
            next(text for text, index, _kind
                 in panel.move_box_lines(state)
                 if index == state["index"]),
            " ✓ └ Slice",
        )

    def test_lines_mark_the_current_goal_and_share_the_marker_column(self):
        panel.file_session_in_goal("/p", self.goal.id, "a")
        panel.attach_goals([self.group])
        state = panel.move_box_start(self.group, self.a)
        lines = [text for text, _i, _k in panel.move_box_lines(state)]
        self.assertEqual(lines, [
            " ⌂ Top level",
            " + New goal",
            "",
            " ✓ Goal",
            "   └ Slice",
            "   Other",
            "   └ Other slice",
        ])
        # `⌂`, `+` and `✓` all sit in the same display column, followed by a
        # blank column, with the words starting in the same name column.
        def width(text):
            return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 0
                       if unicodedata.combining(ch) else 1 for ch in text)

        marker_columns = {}
        for text, glyph in ((lines[0], "⌂"), (lines[1], "+"),
                            (lines[3], "✓")):
            self.assertEqual(text[1], glyph)
            self.assertEqual(text[2], " ")
            marker_columns[glyph] = width(text[:1])
        self.assertEqual(marker_columns["⌂"], marker_columns["+"])
        self.assertEqual(marker_columns["⌂"], marker_columns["✓"])
        self.assertEqual(width(lines[0][:3]), width(lines[1][:3]))
        self.assertEqual(width(lines[0][:3]), width(lines[3][:3]))
        self.assertEqual((lines[0][3], lines[1][3], lines[3][3]),
                         ("T", "N", "G"))

    def test_done_goals_and_slices_are_dimmed_not_checked(self):
        panel.toggle_goal_done("/p", self.slice.id)
        panel.attach_goals([self.group])
        state = panel.move_box_start(self.group, self.a)
        lines = [text for text, _i, _k in panel.move_box_lines(state)]
        self.assertEqual(lines[4], "   └ Slice")
        slice_index = next(
            i for i, (kind, goal, _) in enumerate(state["targets"])
            if goal is not None and goal.id == self.slice.id
        )
        attr = panel.move_box_line_attr(state, slice_index)
        self.assertTrue(attr & curses.A_DIM)
        self.assertNotIn("✓", lines[4])

    def test_keys_move_pick_and_cancel(self):
        state = panel.move_box_start(self.group, self.a)
        self.assertEqual(state["index"], 0)  # Top level, the current container
        state, action = panel.move_box_key(state, ord("j"))
        self.assertEqual((state["index"], action), (1, ""))
        state, action = panel.move_box_key(state, ord("k"))
        self.assertEqual((state["index"], action), (0, ""))
        state, action = panel.move_box_key(state, 10)
        self.assertEqual(action, "pick")
        self.assertEqual(state["targets"][state["index"]][0],
                         panel.MOVE_LOOSE)
        _, action = panel.move_box_key(state, ord("q"))
        self.assertEqual(action, "cancel")
        _, action = panel.move_box_key(state, 27)
        self.assertEqual(action, "cancel")

    def test_arrow_keys_skip_the_divider(self):
        state = panel.move_box_start(self.group, self.a)
        state, action = panel.move_box_key(state, ord("j"))  # to New goal
        self.assertEqual((state["index"], action), (1, ""))
        state, action = panel.move_box_key(state, ord("j"))
        self.assertEqual((state["index"], action), (2, ""))
        lines = panel.move_box_lines(state)
        self.assertEqual(lines[2][2], panel.PICKER_DIVIDER)
        selected = next(
            i for i, (_t, index, _kind) in enumerate(lines)
            if index == state["index"]
        )
        self.assertEqual(selected, 3)  # the goal after the rule

    def test_digits_pick_by_drawn_line(self):
        state = panel.move_box_start(self.group, self.a)
        state, action = panel.move_box_key(state, ord("1"))
        self.assertEqual((action, state["index"]), ("pick", 0))
        self.assertEqual(state["targets"][0][0], panel.MOVE_LOOSE)
        # `3` lands on the divider: no selection, no pick.
        state, action = panel.move_box_key(state, ord("3"))
        self.assertEqual((action, state["index"]), ("", 0))
        state, action = panel.move_box_key(state, ord("4"))
        self.assertEqual((action, state["index"]), ("pick", 2))
        self.assertEqual(state["targets"][2][1].id, self.goal.id)

    def test_click_indices_follow_the_new_line_order(self):
        state = panel.move_box_start(self.group, self.a)
        self.assertEqual(panel.move_box_mouse_pick(state, 0),
                         (panel.MOVE_LOOSE, None))
        self.assertEqual(panel.move_box_mouse_pick(state, 1),
                         (panel.MOVE_NEW, None))
        # The divider is not pickable; the box stays open.
        self.assertIs(panel.move_box_mouse_pick(state, 2),
                      panel._PICKER_STAY)
        self.assertEqual(panel.move_box_mouse_pick(state, 3),
                         (panel.MOVE_GOAL, self.goal))
        self.assertEqual(panel.move_box_mouse_pick(state, 4),
                         (panel.MOVE_GOAL, self.slice))

    def test_box_cancels_when_there_are_no_lines(self):
        scr = mock.Mock()
        scr.getmaxyx.return_value = (24, 30)
        with mock.patch.object(curses, "newwin") as nw:
            result = panel.run_picker(
                scr, {}, "move", lambda state: ([], 0),
                lambda state, ch: (state, None), None,
            )
        self.assertIsNone(result)
        nw.assert_not_called()

    def test_box_cancels_when_it_cannot_draw(self):
        scr = mock.Mock()
        scr.getmaxyx.return_value = (24, 30)
        with mock.patch.object(curses, "newwin", side_effect=curses.error):
            result = panel.move_box(scr, self.group, self.a)
        self.assertIsNone(result)
        scr.getch.assert_not_called()
        scr.getmaxyx.return_value = (2, 4)  # too small for any box
        with mock.patch.object(curses, "newwin") as nw:
            result = panel.move_box(scr, self.group, self.a)
        self.assertIsNone(result)
        nw.assert_not_called()


class MoveBoxRenderTests(GoalStoreCase):
    """The exact Move-to box, indented like the new-session box."""

    def setUp(self):
        super().setUp()
        self.a = session("a", "/p", 400.0)
        self.group = panel.Group("/p", [self.a])
        self.goal_a = panel.create_goal("/p", "Goal A")
        self.slice_a1 = panel.create_slice("/p", self.goal_a.id, "Slice A1")
        self.slice_a2 = panel.create_slice("/p", self.goal_a.id, "Slice A2")
        self.goal_b = panel.create_goal("/p", "Goal B")
        self.slice_b1 = panel.create_slice("/p", self.goal_b.id, "Slice B1")
        panel.attach_goals([self.group])

    def lines(self):
        state = panel.move_box_start(self.group, self.a)
        return state, [
            text for text, _index, _kind in panel.move_box_lines(state)
        ]

    def drawn(self, size=(24, 40)):
        scr = mock.Mock()
        scr.getmaxyx.return_value = size
        win = mock.Mock()
        win.getch.side_effect = [27]
        with mock.patch.object(curses, "newwin", return_value=win) as nw:
            panel.move_box(scr, self.group, self.a)
        calls = win.addnstr.call_args_list[1:]  # call 0 is the title row
        return [c.args[2] for c in calls], nw.call_args.args[1]

    def test_exact_rendering(self):
        state, lines = self.lines()
        self.assertEqual(lines, [
            " ✓ Top level",
            " + New goal",
            "",
            "   Goal A",
            "   ├ Slice A1",
            "   └ Slice A2",
            "   Goal B",
            "   └ Slice B1",
        ])
        kinds = [kind for _t, _i, kind in panel.move_box_lines(state)]
        self.assertEqual(kinds[2], panel.PICKER_DIVIDER)
        self.assertEqual(kinds.count(panel.PICKER_DIVIDER), 1)

    def test_exact_rendered_lines_include_the_divider_rule(self):
        lines, width = self.drawn()
        rule = panel.divider_text(width)
        self.assertEqual(panel.display_width(rule), width)
        self.assertEqual([line.rstrip() for line in lines], [
            " ✓ Top level",
            " + New goal",
            rule,
            "   Goal A",
            "   ├ Slice A1",
            "   └ Slice A2",
            "   Goal B",
            "   └ Slice B1",
        ])
        self.assertEqual(rule[0], "├")
        self.assertEqual(rule[-1], "┤")
        self.assertEqual(rule[1:-1], "─" * (width - 2))

    def test_rendering_when_the_session_is_in_a_goal(self):
        panel.file_session_in_goal("/p", self.goal_a.id, "a")
        panel.attach_goals([self.group])
        state, lines = self.lines()
        self.assertEqual(lines, [
            " ⌂ Top level",
            " + New goal",
            "",
            " ✓ Goal A",
            "   ├ Slice A1",
            "   └ Slice A2",
            "   Goal B",
            "   └ Slice B1",
        ])
        self.assertEqual(
            next(text for text, index, _kind
                 in panel.move_box_lines(state)
                 if index == state["index"]),
            " ✓ Goal A",
        )

    def test_rendering_when_the_session_is_in_a_slice(self):
        panel.file_session_in_goal("/p", self.slice_a2.id, "a")
        panel.attach_goals([self.group])
        state, lines = self.lines()
        self.assertEqual(lines, [
            " ⌂ Top level",
            " + New goal",
            "",
            "   Goal A",
            "   ├ Slice A1",
            " ✓ └ Slice A2",
            "   Goal B",
            "   └ Slice B1",
        ])
        self.assertEqual(
            next(text for text, index, _kind
                 in panel.move_box_lines(state)
                 if index == state["index"]),
            " ✓ └ Slice A2",
        )

    def test_a_done_goal_and_slice_are_dimmed(self):
        panel.toggle_goal_done("/p", self.goal_b.id)
        panel.toggle_goal_done("/p", self.slice_a2.id)
        panel.attach_goals([self.group])
        state, lines = self.lines()
        self.assertEqual(lines[6], "   Goal B")
        self.assertEqual(lines[7], "   └ Slice B1")
        indices = {
            goal.id: i
            for i, (_kind, goal, _depth) in enumerate(state["targets"])
            if goal is not None
        }
        self.assertTrue(
            panel.move_box_line_attr(state, indices[self.goal_b.id])
            & curses.A_DIM
        )
        self.assertTrue(
            panel.move_box_line_attr(state, indices[self.slice_a2.id])
            & curses.A_DIM
        )
        self.assertFalse(
            panel.move_box_line_attr(state, indices[self.goal_a.id])
            & curses.A_DIM
        )
        self.assertNotIn("✓", lines[6])
        self.assertNotIn("✓", lines[7])

    def test_a_goal_with_no_slices_gets_no_connector_line(self):
        panel.create_goal("/p", "Goal C")
        panel.attach_goals([self.group])
        _state, lines = self.lines()
        self.assertEqual(lines, [
            " ✓ Top level",
            " + New goal",
            "",
            "   Goal A",
            "   ├ Slice A1",
            "   └ Slice A2",
            "   Goal B",
            "   └ Slice B1",
            "   Goal C",
        ])
        self.assertNotIn("├", lines[-1])
        self.assertNotIn("└", lines[-1])

    def test_empty_project_has_no_divider(self):
        empty = panel.Group("/empty", [session("b", "/empty", 401.0)])
        state = panel.move_box_start(empty, empty.sessions[0])
        lines = panel.move_box_lines(state)
        self.assertEqual([text for text, _i, _k in lines],
                         [" ✓ Top level", " + New goal"])
        self.assertNotIn(panel.PICKER_DIVIDER,
                         [kind for _t, _i, kind in lines])

    def test_a_clamped_box_keeps_one_trailing_padding_column(self):
        panel.create_goal("/p", "A goal name far too long for the box")
        panel.attach_goals([self.group])
        width = 24
        lines, box_width = self.drawn(size=(24, width))
        self.assertEqual(box_width, width)
        rule = panel.divider_text(box_width)
        for line in lines:
            if line == rule:
                self.assertEqual(panel.display_width(line), box_width)
                continue
            # The interior is width - 2, so width - 3 leaves one blank
            # column against the right border even for a truncated label.
            self.assertLessEqual(panel.display_width(line), box_width - 3)
        self.assertTrue(any("…" in line for line in lines))
        short = next(line for line in lines if "+" in line)
        self.assertEqual(panel.display_width(short), box_width - 3)
        self.assertEqual(short[-1], " ")

    def test_no_ellipsis_at_ordinary_widths(self):
        _state, lines = self.lines()
        for cols in (24, 30, 34, 40):
            with self.subTest(cols=cols):
                width = min(
                    cols,
                    max(panel.display_width(text) for text in lines
                        if text) + 3,
                )
                inner = width - 2
                for text in lines:
                    if not text:
                        continue
                    fitted = panel.fit_cols(text, inner).rstrip()
                    self.assertNotIn("…", fitted)
                    self.assertEqual(fitted, text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
