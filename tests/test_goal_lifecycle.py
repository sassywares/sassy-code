#!/usr/bin/env python3
"""Goal rename, done and delete tests."""
from __future__ import annotations

import unittest
from unittest import mock

from tests.goal_case import GoalStoreCase, panel, session


class LifecycleCase(GoalStoreCase):
    def make(self, names=("A", "B"), loose_ts=(), members=None):
        """Groups with one goal per name; goal i holds members[i] = [(id, ts)]."""
        members = members or {}
        sessions = [session(f"l{t}", "/p", t) for t in loose_ts]
        goals = []
        for n, name in enumerate(names):
            goals.append(panel.create_goal("/p", name))
            for sid, ts in members.get(n, []):
                sessions.append(session(sid, "/p", ts))
                panel.file_session_in_goal("/p", goals[n].id, sid)
        sessions.sort(key=lambda s: -s.last_ts)
        groups = [panel.Group("/p", sessions)]
        panel.attach_goals(groups)
        return groups, goals

    def row(self, groups, goal):
        return next(
            r for r in panel.rows_for(groups, "")
            if r[0] == "goal" and r[2].id == goal.id
        )

    def tops(self, groups):
        return [
            ("G:" + x.name if k == "goal" else x.id)
            for k, _, x in panel.rows_for(groups, "", limit=99)
            if k in ("goal", "session") and not (
                k == "session" and x.id in panel.goal_member_ids(groups[0])
            )
        ]


class RenameTests(LifecycleCase):
    def test_prefill_and_footer(self):
        groups, (a, b) = self.make()
        self.assertEqual(
            panel.rename_prefill(self.row(groups, a)), ("goal:rename A", "")
        )
        items = panel.footer_items(self.row(groups, a))
        self.assertEqual(
            panel.footer_lines(items, 80),
            ["⏎ toggle  n new  R rename  x done  D delete"],
        )

    def test_prefill_refused_off_goal_rows(self):
        groups, _ = self.make(loose_ts=(5.0,))
        text, msg = panel.rename_prefill(("session", groups[0], groups[0].sessions[0]))
        self.assertEqual(text, "")
        self.assertIn("highlight a goal", msg)
        self.assertEqual(panel.rename_prefill(None)[0], "")

    def test_apply_renames_and_keeps_cursor_on_goal(self):
        groups, (a, b) = self.make()
        msg, key = panel.apply_command(
            "goal:rename  Shiny   new", self.row(groups, a), groups, set()
        )
        self.assertIn("renamed", msg)
        self.assertEqual(key, ("goal", a.id, "/p"))
        self.assertEqual(groups[0].goals[0].name, "Shiny new")
        rows = list(panel.rows_for(groups, ""))
        self.assertEqual(rows[panel.cursor_for_key(rows, key)][0], "goal")

    def test_same_name_different_case_for_itself_is_allowed(self):
        groups, (a, b) = self.make()
        msg, key = panel.apply_command(
            "goal:rename a", self.row(groups, a), groups, set()
        )
        self.assertIsNotNone(key)
        self.assertEqual(groups[0].goals[0].name, "a")

    def test_refusals(self):
        groups, (a, b) = self.make(loose_ts=(5.0,))
        before = self.file.read_text()
        for typed, row, expect in (
            ("goal:rename x", ("session", groups[0], groups[0].sessions[0]),
             "highlight a goal"),
            ("goal:rename x", None, "highlight a goal"),
            ("goal:rename   ", self.row(groups, a), "needs a name"),
            ("goal:rename b", self.row(groups, a), "already exists"),
        ):
            msg, key = panel.apply_command(typed, row, groups, set())
            self.assertIn(expect, msg)
            self.assertIsNone(key)
        self.assertEqual(self.file.read_text(), before)

    def test_duplicate_in_other_project_is_fine(self):
        panel.create_goal("/q", "Zed")
        groups, (a, _) = self.make()
        panel.rename_goal("/p", a.id, "zed")


class DoneTests(LifecycleCase):
    def test_done_marks_collapses_and_sinks_below_loose(self):
        groups, (a, b) = self.make(
            loose_ts=(100.0,), members={0: [("a1", 500.0)], 1: [("b1", 50.0)]}
        )
        self.assertEqual(self.tops(groups), ["G:A", "l100.0", "G:B"])
        msg, key = panel.done_goal(groups, self.row(groups, a))
        self.assertIn("done", msg)
        self.assertEqual(key, ("goal", a.id, "/p"))
        self.assertTrue(groups[0].goals[0].done)
        self.assertTrue(groups[0].goals[0].collapsed)
        self.assertEqual(self.tops(groups), ["l100.0", "G:B", "G:A"])
        rows = [r[0] for r in panel.rows_for(groups, "")]
        self.assertNotIn("a1", [getattr(r[2], "id", "") for r in panel.rows_for(groups, "")])
        self.assertTrue(rows)

    def test_reopen_restores_recency_and_expands(self):
        groups, (a, b) = self.make(
            loose_ts=(100.0,), members={0: [("a1", 500.0)], 1: [("b1", 50.0)]}
        )
        panel.done_goal(groups, self.row(groups, a))
        msg, _ = panel.done_goal(groups, self.row(groups, a))
        self.assertIn("reopened", msg)
        self.assertFalse(groups[0].goals[0].done)
        self.assertFalse(groups[0].goals[0].collapsed)
        self.assertEqual(self.tops(groups), ["G:A", "l100.0", "G:B"])

    def test_persists_across_reload(self):
        groups, (a, _) = self.make()
        panel.done_goal(groups, self.row(groups, a))
        panel._goals_cache = None
        goals, bad = panel.load_goals()
        self.assertFalse(bad)
        self.assertTrue(goals["/p"][0].done)

    def test_done_goal_behind_limit_still_counts(self):
        groups, (a,) = self.make(
            names=("A",), loose_ts=(1.0, 2.0, 3.0, 4.0, 5.0, 6.0)
        )
        panel.done_goal(groups, self.row(groups, a))
        rows = list(panel.rows_for(groups, ""))
        self.assertNotIn("goal", [r[0] for r in rows])
        more = next(r for r in rows if r[0] == "more")
        self.assertEqual(more[2], 2)  # 7 items, 5 shown
        shown = list(panel.rows_for(groups, "", expanded={"/p"}))
        self.assertEqual(shown[-2][0], "goal")

    def test_done_goal_with_running_member_is_pulled_in(self):
        groups, (a,) = self.make(
            names=("A",), loose_ts=(1.0, 2.0, 3.0, 4.0, 5.0, 6.0),
            members={0: [("a1", 0.5)]},
        )
        self.assertEqual(groups[0].sessions[-1].id, "a1")
        panel.done_goal(groups, ("goal", groups[0], groups[0].goals[0]))
        groups[0].sessions[-1].status = "running"
        self.assertIn("goal", [r[0] for r in panel.rows_for(groups, "")])

    def test_build_lines_check_placement_and_truncation(self):
        groups, (a,) = self.make(
            names=("A very long goal name indeed",), members={0: [("a1", 10.0)]}
        )
        panel.done_goal(groups, self.row(groups, a))
        for w in (40, 30, 26):
            lines, _, _ = panel.build_lines(
                list(panel.rows_for(groups, "")), w, set(), ""
            )
            text = next(t for t, k, _ in lines if k == "goal")
            self.assertLessEqual(panel.display_width(text), w - 1)
            self.assertIn("▸ ✓ ", text)
        groups[0].sessions[0].status = "running"
        lines, _, _ = panel.build_lines(
            list(panel.rows_for(groups, "")), 40, set(), ""
        )
        text = next(t for t, k, _ in lines if k == "goal")
        self.assertIn("▸ ● ✓ ", text)

    def test_list_shows_check(self):
        groups, (a, _) = self.make(members={0: [("a1", 10.0)]})
        panel.done_goal(groups, self.row(groups, a))
        self.assertRegex("\n".join(panel.list_lines(groups)), r"(?m)^  ✓ A  \(1\)$")

    def test_non_goal_row_refused(self):
        groups, _ = self.make(loose_ts=(5.0,))
        msg, key = panel.done_goal(groups, ("session", groups[0], groups[0].sessions[0]))
        self.assertIn("highlight a goal", msg)
        self.assertIsNone(key)


class DeleteTests(LifecycleCase):
    def test_prompt_text(self):
        groups, (a, _) = self.make()
        self.assertEqual(
            panel.delete_prompt(self.row(groups, a))[0], " Delete goal 'A'? y/n "
        )
        self.assertEqual(panel.delete_prompt(None)[0], "")

    def test_delete_flattens_sessions_and_selects_newest_member(self):
        groups, (a, b) = self.make(members={0: [("a1", 10.0), ("a2", 20.0)]})
        msg, key = panel.delete_goal_row(groups, self.row(groups, a))
        self.assertIn("deleted", msg)
        self.assertEqual(key, ("session", "a2", "/p"))
        self.assertEqual([g.name for g in groups[0].goals], ["B"])
        kinds = [(r[0], getattr(r[2], "id", None)) for r in panel.rows_for(groups, "")]
        self.assertIn(("session", "a1"), kinds)
        self.assertIn(("session", "a2"), kinds)
        self.assertEqual(len(groups[0].sessions), 2)
        rows = list(panel.rows_for(groups, ""))
        self.assertEqual(rows[panel.cursor_for_key(rows, key)][2].id, "a2")

    def test_empty_goal_selects_project_heading(self):
        groups, (a, _) = self.make()
        _, key = panel.delete_goal_row(groups, self.row(groups, a))
        self.assertEqual(key, ("group", "/p"))

    def test_vanished_goal_reports(self):
        groups, (a, _) = self.make()
        row = self.row(groups, a)
        panel.delete_goal("/p", a.id)
        msg, key = panel.delete_goal_row(groups, row)
        self.assertIn("no longer exists", msg)
        self.assertIsNone(key)


class CtrlCTests(LifecycleCase):
    def setUp(self):
        super().setUp()
        env = mock.patch.dict(panel.os.environ, {"TMUX": "/x,1,0"})
        env.start()
        self.addCleanup(env.stop)

    def test_ctrl_c_in_delete_confirm_detaches_and_keeps_running(self):
        groups, goals = self.make(("A",), members={0: [("m", 100.0)]})
        # D, Ctrl-C in the confirm, Ctrl-C in the main loop, then stop the
        # (otherwise endless) loop to prove it kept running after both.
        keys = iter([ord("D"), KeyboardInterrupt, KeyboardInterrupt, SystemExit])

        def getch():
            key = next(keys)
            if isinstance(key, type):
                raise key
            return key

        scr = mock.Mock()
        scr.getch.side_effect = getch
        scr.getmaxyx.return_value = (24, 40)
        detach = mock.Mock(return_value="")
        with mock.patch.object(panel.curses, "curs_set"), \
                mock.patch.object(panel.curses, "start_color"), \
                mock.patch.object(panel.curses, "use_default_colors"), \
                mock.patch.object(panel.curses, "init_pair"), \
                mock.patch.object(panel, "load_groups", return_value=groups), \
                mock.patch.object(panel, "set_panel_tag"), \
                mock.patch.object(panel, "panel_pane_id", return_value="%1"), \
                mock.patch.object(panel, "migrate_state"), \
                mock.patch.object(panel, "_read_state", return_value="x"), \
                mock.patch.object(panel, "draw", return_value=(1, 0)) as draw, \
                mock.patch.object(panel, "detach_panel", detach), \
                mock.patch.object(panel, "tmux") as tmux:
            with self.assertRaises(SystemExit):
                panel.main_curses(scr, "%2")
        self.assertTrue(
            any("Delete goal" in str(c.args[6]) for c in draw.call_args_list)
        )
        self.assertEqual(detach.call_count, 2)
        tmux.assert_not_called()
        self.assertTrue(self.file.exists())  # nothing was deleted

    def run_loop(self, groups, keys, draw, detach, pane="%1"):
        keys = iter(keys)

        def getch():
            key = next(keys)
            if isinstance(key, type):
                raise key
            return key

        scr = mock.Mock()
        scr.getch.side_effect = getch
        scr.getmaxyx.return_value = (24, 40)
        with mock.patch.object(panel.curses, "curs_set"), \
                mock.patch.object(panel.curses, "start_color"), \
                mock.patch.object(panel.curses, "use_default_colors"), \
                mock.patch.object(panel.curses, "init_pair"), \
                mock.patch.object(panel, "load_groups", return_value=groups), \
                mock.patch.object(panel, "set_panel_tag"), \
                mock.patch.object(panel, "panel_pane_id", return_value=pane), \
                mock.patch.object(panel, "migrate_state"), \
                mock.patch.object(panel, "_read_state", return_value="x"), \
                mock.patch.object(panel, "draw", draw), \
                mock.patch.object(panel, "detach_panel", detach):
            return panel.main_curses(scr, "%2")

    def test_ctrl_c_during_draw_detaches_and_keeps_running(self):
        groups, _ = self.make(("A",))
        draw = mock.Mock(side_effect=[KeyboardInterrupt, (0, 0), (0, 0)])
        detach = mock.Mock(return_value="")
        with self.assertRaises(SystemExit):
            self.run_loop(groups, [SystemExit], draw, detach)
        self.assertEqual(detach.call_count, 1)

    def test_ctrl_c_inside_detach_still_detaches(self):
        groups, _ = self.make(("A",))
        detach = mock.Mock(side_effect=[KeyboardInterrupt, ""])
        with self.assertRaises(SystemExit):
            self.run_loop(
                groups, [ord("q"), SystemExit], mock.Mock(return_value=(0, 0)),
                detach,
            )
        self.assertEqual(detach.call_count, 2)

    def test_q_and_ctrl_c_exit_outside_tmux(self):
        groups, _ = self.make(("A",))
        for key in (ord("q"), KeyboardInterrupt):
            detach = mock.Mock(return_value="")
            with mock.patch.dict(panel.os.environ, {}, clear=False):
                panel.os.environ.pop("TMUX", None)
                # A tmux server may be running: its pane must not matter.
                self.run_loop(
                    groups, [key], mock.Mock(return_value=(0, 0)), detach,
                    pane="%0",
                )
            detach.assert_not_called()

    def test_outside_tmux_runs_no_detach_command(self):
        groups, _ = self.make(("A",))
        with mock.patch.dict(panel.os.environ, {}, clear=False), \
                mock.patch.object(panel, "tmux") as tmux:
            panel.os.environ.pop("TMUX", None)
            self.run_loop(
                groups, [ord("q")], mock.Mock(return_value=(0, 0)),
                panel.detach_panel, pane="%0",
            )
        for call in tmux.call_args_list:
            self.assertNotIn("detach-client", call.args)

    def test_failed_pane_read_inside_tmux_keeps_running_then_recovers(self):
        groups, _ = self.make(("A",))
        draw = mock.Mock(return_value=(0, 0))
        with mock.patch.dict(panel.os.environ, {"TMUX": "/x,1,0"}), \
                mock.patch.object(
                    panel, "panel_pane_id", side_effect=["%1", "", "%1"]
                ), \
                mock.patch.object(panel, "tmux") as tmux:
            tmux.return_value = mock.Mock(returncode=0, stdout="s\n")
            keys = iter([ord("q"), ord("q"), SystemExit])

            def getch():
                key = next(keys)
                if isinstance(key, type):
                    raise key
                return key

            scr = mock.Mock()
            scr.getch.side_effect = getch
            scr.getmaxyx.return_value = (24, 40)
            with mock.patch.object(panel.curses, "curs_set"), \
                    mock.patch.object(panel.curses, "start_color"), \
                    mock.patch.object(panel.curses, "use_default_colors"), \
                    mock.patch.object(panel.curses, "init_pair"), \
                    mock.patch.object(panel, "load_groups", return_value=groups), \
                    mock.patch.object(panel, "set_panel_tag"), \
                    mock.patch.object(panel, "migrate_state"), \
                    mock.patch.object(panel, "_read_state", return_value="x"), \
                    mock.patch.object(panel, "draw", draw):
                with self.assertRaises(SystemExit):
                    panel.main_curses(scr, "%2")
        self.assertEqual(draw.call_args_list[1].args[6], panel.DETACH_FAILED)
        self.assertTrue(
            any("detach-client" in c.args for c in tmux.call_args_list)
        )

    def test_ctrl_c_during_present_and_new_session_detaches(self):
        groups, _ = self.make(("A",), members={0: [("m", 100.0)]})
        for key, target in (
            (10, "present_session"), (ord("n"), "new_session"),
        ):
            detach = mock.Mock(return_value="")
            # group, goal, member: j j lands on the session row.
            keys = [ord("j"), ord("j"), key, SystemExit]
            with mock.patch.dict(panel.os.environ, {"TMUX": "/x,1,0"}), \
                    mock.patch.object(panel, target, side_effect=KeyboardInterrupt), \
                    mock.patch.object(panel, "installed_harnesses", return_value=["pi"]):
                with self.assertRaises(SystemExit):
                    self.run_loop(
                        groups, keys,
                        mock.Mock(side_effect=lambda *a: (a[3], a[4])), detach,
                    )
            self.assertEqual(detach.call_count, 1, target)


class TinyScreenTests(LifecycleCase):
    def test_draw_survives_a_tiny_terminal(self):
        groups, _ = self.make(("A",), members={0: [("m", 100.0)]})
        for size in ((1, 1), (2, 3), (3, 10), (24, 1)):
            scr = mock.Mock()
            scr.getmaxyx.return_value = size

            def strict(y, x, text, n, attr=0, size=size):
                if not 0 <= y < size[0]:
                    raise panel.curses.error("off screen")

            scr.addnstr.side_effect = strict
            with mock.patch.object(panel.curses, "color_pair", return_value=0):
                panel.draw(scr, groups, "", 0, 0, set())


class CorruptTests(LifecycleCase):
    def test_all_actions_refuse_and_never_write(self):
        groups, (a, _) = self.make()
        row = self.row(groups, a)
        self.file.write_text("{ not json")
        panel._goals_cache = None
        self.assertEqual(panel.rename_prefill(row)[0], "")
        self.assertIn("unreadable", panel.rename_prefill(row)[1])
        self.assertEqual(panel.delete_prompt(row)[0], "")
        self.assertIn("unreadable", panel.delete_prompt(row)[1])
        for msg in (
            panel.done_goal(groups, row)[0],
            panel.delete_goal_row(groups, row)[0],
            panel.apply_command("goal:rename x", row, groups, set())[0],
        ):
            self.assertIn("unreadable", msg)
        self.assertEqual(self.file.read_text(), "{ not json")


if __name__ == "__main__":
    unittest.main(verbosity=2)
