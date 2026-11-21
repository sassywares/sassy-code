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
        self.assertEqual(more[2], (2, False))  # 7 items, 5 shown
        shown = list(panel.rows_for(groups, "", expanded={"/p": 1}))
        self.assertEqual(shown[-2][0], "goal")

    def test_done_goal_with_running_member_is_pulled_in(self):
        groups, (a,) = self.make(
            names=("A",), loose_ts=(1.0, 2.0, 3.0, 4.0, 5.0, 6.0),
            members={0: [("a1", 0.5)]},
        )
        self.assertEqual(groups[0].sessions[-1].id, "a1")
        panel.done_goal(groups, ("goal", groups[0], groups[0].goals[0]))
        groups[0].sessions[-1].status = "in_progress"
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
            self.assertIn("▸ ", text)
            self.assertIn("(1) ✓", text)
        groups[0].sessions[0].status = "in_progress"
        lines, _, _ = panel.build_lines(
            list(panel.rows_for(groups, "")), 40, set(), ""
        )
        text = next(t for t, k, _ in lines if k == "goal")
        self.assertIn("(1) ● ✓", text)

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


class LoopCase(LifecycleCase):
    """Shared main_curses pump for key-dispatch tests."""

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


class TierKeyTests(LoopCase):
    """`[` / `]` / Enter dispatch through the main loop (P1)."""

    def keys(self, groups, presses):
        """Run *presses*, then a SystemExit; return (cursor, folded, tiers)."""
        snapshots = []

        def draw(stdscr, groups, flt, cursor, offset, collapsed, message,
                 expanded, notice):
            snapshots.append((cursor, set(collapsed), dict(expanded)))
            return cursor, offset

        with self.assertRaises(SystemExit):
            self.run_loop(
                groups, presses + [SystemExit], draw,
                mock.Mock(return_value=""),
            )
        return snapshots

    def loose(self, count=10):
        groups, _ = self.make(
            names=(), loose_ts=tuple(1000.0 - i for i in range(count))
        )
        return groups

    def goals(self, name="A"):
        groups, _ = self.make(
            names=(name,),
            members={0: [(f"m{i}", 100.0 - i) for i in range(5)]},
        )
        return groups

    def test_enter_reveals_the_next_tier_and_lands_on_the_first_new_row(self):
        groups = self.loose()
        # snaps[0] is the base tier, snaps[1] the `more` row after G,
        # snaps[2] the reveal and its landed cursor.
        snaps = self.keys(groups, [ord("G"), 10])
        self.assertEqual(snaps[1], (6, set(), {}))
        self.assertEqual(snaps[2], (6, set(), {"/p": 1}))
        rows = list(panel.rows_for(groups, "", set(), {"/p": 1}))
        self.assertEqual(rows[6][2].id, "l995.0")

    def test_bracket_and_l_reveal_like_enter(self):
        for key in (10, ord("]"), ord("l")):
            with self.subTest(key=key):
                snaps = self.keys(self.loose(), [ord("G"), key])
                self.assertEqual(snaps[2], (6, set(), {"/p": 1}), key)

    def test_show_fewer_activation_hides_one_tier_and_lands_on_the_more_row(self):
        # Enter/click activation and `[`/`h` all step back one tier (P1 fix).
        for key in (10, ord("["), ord("h")):
            with self.subTest(key=key):
                snaps = self.keys(self.loose(), [ord("G"), 10, ord("G"), key])
                self.assertEqual(snaps[3], (11, set(), {"/p": 1}))
                self.assertEqual(snaps[4], (6, set(), {}))

    def test_reveal_on_show_fewer_is_a_no_op(self):
        for key in (ord("]"), ord("l")):
            with self.subTest(key=key):
                snaps = self.keys(self.loose(), [ord("G"), 10, ord("G"), key])
                self.assertEqual(snaps[3], (11, set(), {"/p": 1}))
                self.assertEqual(snaps[4], (11, set(), {"/p": 1}), key)

    def test_session_row_bracket_collapses_its_goal(self):
        groups = self.goals()
        snaps = self.keys(groups, [ord("j"), ord("j"), ord("[")])
        self.assertEqual(snaps[2], (2, set(), {}))  # session row
        self.assertEqual(snaps[3], (1, set(), {}))  # goal row
        self.assertTrue(groups[0].goals[0].collapsed)

    def test_h_on_a_session_row_collapses_its_goal(self):
        groups = self.goals()
        snaps = self.keys(groups, [ord("j"), ord("j"), ord("h")])
        self.assertEqual(snaps[2], (2, set(), {}))
        self.assertEqual(snaps[3], (1, set(), {}))
        self.assertTrue(groups[0].goals[0].collapsed)

    def test_session_row_reveal_lands_on_the_first_new_row(self):
        groups = self.goals()
        goal_id = groups[0].goals[0].id
        snaps = self.keys(groups, [ord("j"), ord("j"), ord("l")])
        self.assertEqual(snaps[2], (2, set(), {}))  # session row
        self.assertEqual(snaps[3], (5, set(), {goal_id: 1}))
        self.assertFalse(groups[0].goals[0].collapsed)

    def test_session_row_collapses_and_expands_its_project(self):
        groups = self.loose()
        snaps = self.keys(groups, [ord("j"), ord("["), ord("]")])
        self.assertEqual(snaps[2], (0, {"/p"}, {}))
        self.assertEqual(snaps[3], (0, set(), {}))

    def test_goal_more_row_tiers_like_a_project(self):
        groups = self.goals()
        snaps = self.keys(groups, [ord("j"), ord("G"), 10])
        self.assertEqual(snaps[2], (5, set(), {}))
        cursor, _, tiers = snaps[3]
        self.assertEqual(cursor, 5)
        rows = list(panel.rows_for(groups, "", set(), tiers))
        self.assertEqual(rows[cursor][2].id, "m3")

    def fresh_goals(self):
        """Empty the goal store so each level case starts from one goal."""
        self.file.parent.mkdir(parents=True, exist_ok=True)
        self.file.write_text('{"version": 1, "projects": {}}')
        panel._goals_cache = None

    def slice_group(self, count=5):
        groups, _ = self.make(
            names=(), loose_ts=tuple(100.0 - i for i in range(count))
        )
        goal = panel.create_goal("/p", "A")
        sl = panel.create_slice("/p", goal.id, "S")
        for s in groups[0].sessions:
            panel.file_session_in_goal("/p", sl.id, s.id)
        panel.attach_goals(groups)
        return groups, next(x for x in groups[0].goals if x.id == sl.id)

    def level_case(self, level):
        """(groups, containers own row, tier-map key, container row index)."""
        self.fresh_goals()
        if level == "project":
            groups = self.loose()
            key, tier = ("group", "/p"), "/p"
        elif level == "goal":
            groups = self.goals()
            goal = groups[0].goals[0]
            key, tier = ("goal", goal.id, "/p"), goal.id
        else:
            groups, sl = self.slice_group()
            key, tier = ("slice", sl.id, "/p"), sl.id
        rows = list(panel.rows_for(groups, "", set(), {}))
        return groups, key, tier, panel.cursor_for_key(rows, key)

    def rows(self, groups, tiers=None, collapsed=None):
        return list(
            panel.rows_for(groups, "", collapsed or set(), tiers or {})
        )

    def test_bracket_reveals_one_tier_from_each_container_row(self):
        for level in ("project", "goal", "slice"):
            with self.subTest(level=level):
                groups, _key, tier, index = self.level_case(level)
                more = panel.more_row_index(self.rows(groups), tier)
                self.assertIsNotNone(more)
                snaps = self.keys(groups, [ord("j")] * index + [ord("]")])
                self.assertEqual(snaps[-1], (more, set(), {tier: 1}))
                after = self.rows(groups, {tier: 1})
                self.assertEqual(after[more][0], "session")

    def test_expanding_a_collapsed_container_shows_the_base_tier(self):
        # Collapsed while tiered up, `]` and Enter both reopen at the base
        # tier instead of restoring the stale raised tier.
        for level in ("project", "goal", "slice"):
            for reopen in (ord("]"), 10):
                with self.subTest(level=level, reopen=reopen):
                    groups, _key, tier, index = self.level_case(level)
                    snaps = self.keys(
                        groups,
                        [ord("j")] * index
                        + [ord("]"), ord("g")] + [ord("j")] * index
                        + [10, reopen],
                    )
                    self.assertEqual(snaps[-1], (index, set(), {}))
                    if level == "project":
                        self.assertNotIn("/p", snaps[-1][1])
                    else:
                        node = next(x for x in groups[0].goals if x.id == tier)
                        self.assertFalse(node.collapsed)

    def test_bracket_on_a_base_tier_more_row_collapses_its_container(self):
        # The hide that removed the last tier lands on the `more` row; `[`
        # there must fall through to collapsing the container it belongs to.
        for level in ("project", "goal", "slice"):
            for key in (ord("["), ord("h"), panel.curses.KEY_LEFT):
                with self.subTest(level=level, key=key):
                    groups, _key, tier, index = self.level_case(level)
                    rows = self.rows(groups)
                    more = panel.more_row_index(rows, tier)
                    self.assertIsNotNone(more)
                    snaps = self.keys(
                        groups, [ord("j")] * more + [key]
                    )
                    self.assertEqual(snaps[-1][0], index)
                    self.assertEqual(snaps[-1][2], {})
                    if level == "project":
                        self.assertIn("/p", snaps[-1][1])
                    else:
                        node = next(
                            x for x in groups[0].goals if x.id == tier
                        )
                        self.assertTrue(node.collapsed)

    def test_reveal_skips_a_level_that_adds_nothing(self):
        # 10 running sessions are pulled through the base cap, so 15 of 20
        # are shown and the next cap (15) adds no row: `]` must land on the
        # level that actually reveals the last five, not burn a phantom tier.
        groups, _ = self.make(
            names=(), loose_ts=tuple(1000.0 - i for i in range(20))
        )
        for s in groups[0].sessions[5:15]:
            s.status = "in_progress"
        base = self.rows(groups)
        more = panel.more_row_index(base, "/p")
        self.assertEqual(panel.more_hidden(base[more]), 5)
        level1 = self.rows(groups, {"/p": 1})
        self.assertEqual(
            panel.more_hidden(level1[panel.more_row_index(level1, "/p")]),
            5,
        )  # the next tier reveals nothing
        snaps = self.keys(groups, [ord("j")] * more + [ord("]")])
        self.assertEqual(snaps[-1][2], {"/p": 2})
        shown = self.rows(groups, {"/p": 2})
        self.assertEqual(
            panel.more_hidden(shown[panel.more_row_index(shown, "/p")]), 0
        )
        base_ids = {r[2].id for r in base if r[0] == "session"}
        first_new = next(
            r[2].id for r in shown
            if r[0] == "session" and r[2].id not in base_ids
        )
        self.assertEqual(shown[snaps[-1][0]][2].id, first_new)

    def test_hide_falls_back_to_the_container_when_the_more_row_goes(self):
        # With every session running the base tier hides nothing, so dropping
        # the last raised tier removes the `more` row; land on the container,
        # not the project heading (or a sibling).
        for level in ("project", "goal", "slice"):
            with self.subTest(level=level):
                groups, key, tier, _index = self.level_case(level)
                for s in groups[0].sessions:
                    s.status = "in_progress"
                self.assertIsNone(
                    panel.more_row_index(self.rows(groups), tier)
                )
                expanded = {tier: 1}
                self.assertIsNotNone(
                    panel.more_row_index(self.rows(groups, expanded), tier)
                )
                landed = panel.tier_hide(
                    groups, "", set(), expanded, tier, key
                )
                self.assertEqual(landed, key)
                self.assertEqual(expanded, {})
                self.assertIsNone(
                    panel.more_row_index(self.rows(groups), tier)
                )

    def test_tier_keys_are_no_ops_under_a_filter(self):
        # A filter draws no `more` row and ignores tiers; `[`/`]` must not
        # mutate the stored level, so clearing the filter restores it.
        groups = self.loose()
        snaps = []

        def draw(stdscr, groups, flt, cursor, offset, collapsed, message,
                 expanded, notice):
            snaps.append((flt, dict(expanded)))
            return cursor, offset

        with mock.patch.object(panel, "prompt_line", return_value="l"):
            with self.assertRaises(SystemExit):
                self.run_loop(
                    groups,
                    [ord("]"), ord("/"), ord("["), ord("]"), SystemExit],
                    draw,
                    mock.Mock(return_value=""),
                )
        self.assertEqual(snaps[1][1], {"/p": 1})  # `]` raised the tier
        self.assertEqual(snaps[2][0], "l")  # the filter is active
        for _flt, tiers in snaps[2:]:
            self.assertEqual(tiers, {"/p": 1})

    def test_bracket_hides_one_tier_from_each_expanded_container_row(self):
        for level in ("project", "goal", "slice"):
            with self.subTest(level=level):
                groups, _key, tier, index = self.level_case(level)
                more = panel.more_row_index(self.rows(groups), tier)
                snaps = self.keys(
                    groups,
                    [ord("j")] * index + [ord("]"), ord("g")]
                    + [ord("j")] * index + [ord("[")],
                )
                self.assertEqual(snaps[-1], (more, set(), {}))

    def test_bracket_collapses_each_container_at_the_base_tier(self):
        for level in ("project", "goal", "slice"):
            with self.subTest(level=level):
                groups, _key, tier, index = self.level_case(level)
                snaps = self.keys(groups, [ord("j")] * index + [ord("[")])
                self.assertEqual(snaps[-1][0], index)
                self.assertEqual(snaps[-1][2], {})
                if level == "project":
                    self.assertIn("/p", snaps[-1][1])
                else:
                    node = next(x for x in groups[0].goals if x.id == tier)
                    self.assertTrue(node.collapsed)

    def test_bracket_is_a_no_op_at_the_extremes(self):
        for level in ("project", "goal", "slice"):
            with self.subTest(level=level, extreme="all shown"):
                groups, _key, tier, index = self.level_case(level)
                snaps = self.keys(
                    groups,
                    [ord("j")] * index
                    + [ord("]"), ord("G"), ord("]")],
                )
                self.assertEqual(snaps[-1][2], {tier: 1})
                self.assertEqual(snaps[-1][1], set())
            with self.subTest(level=level, extreme="collapsed"):
                groups, _key, tier, index = self.level_case(level)
                snaps = self.keys(
                    groups, [ord("j")] * index + [ord("["), ord("[")]
                )
                self.assertEqual(snaps[-1][2], {})
                self.assertEqual(snaps[-1][0], index)

    def test_show_fewer_activation_hides_one_tier_at_each_level(self):
        for level in ("project", "goal", "slice"):
            with self.subTest(level=level):
                groups, _key, tier, index = self.level_case(level)
                snaps = self.keys(
                    groups,
                    [ord("j")] * index + [ord("]"), ord("G"), 10],
                )
                self.assertEqual(snaps[-1][2], {})
                more = panel.more_row_index(self.rows(groups), tier)
                self.assertEqual(snaps[-1][0], more)


class CtrlCTests(LoopCase):
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

    def test_ctrl_c_inside_the_box_cancels_without_detaching(self):
        groups, _ = self.make(("A",))
        detach = mock.Mock(return_value="")
        new_session = mock.Mock(return_value=None)
        win = mock.Mock()
        win.getch.side_effect = KeyboardInterrupt  # ISIG: no key reaches us
        with mock.patch.object(panel.curses, "newwin", return_value=win), \
                mock.patch.object(
                    panel, "installed_harnesses", return_value=["pi", "claude"]
                ), \
                mock.patch.object(panel, "new_session", new_session):
            with self.assertRaises(SystemExit):
                self.run_loop(
                    groups, [ord("n"), SystemExit],
                    mock.Mock(side_effect=lambda *a: (a[3], a[4])), detach,
                )
        detach.assert_not_called()
        new_session.assert_not_called()

    def test_ctrl_c_inside_the_move_box_cancels_without_moving_or_detaching(self):
        groups, _ = self.make(("A",), members={0: [("m", 100.0)]})
        before = self.file.read_bytes()
        detach = mock.Mock(return_value="")
        move_row = mock.Mock()
        new_goal = mock.Mock()
        win = mock.Mock()
        win.getch.side_effect = KeyboardInterrupt  # ISIG: no key reaches us
        with mock.patch.object(panel.curses, "newwin", return_value=win), \
                mock.patch.object(panel, "move_session_row", move_row), \
                mock.patch.object(panel, "move_to_new_goal", new_goal):
            with self.assertRaises(SystemExit):
                # group, goal, member: j j lands on the session row.
                self.run_loop(
                    groups, [ord("j"), ord("j"), ord("m"), SystemExit],
                    mock.Mock(side_effect=lambda *a: (a[3], a[4])), detach,
                )
        detach.assert_not_called()
        move_row.assert_not_called()
        new_goal.assert_not_called()
        self.assertEqual(self.file.read_bytes(), before)

    def test_ctrl_c_during_box_draw_cancels_without_detaching(self):
        # ISIG can raise KeyboardInterrupt while the box draws, not only at
        # getch. It must cancel the box, not escape to the loop's detach.
        groups, _ = self.make(("A",))
        detach = mock.Mock(return_value="")
        new_session = mock.Mock(return_value=None)
        with mock.patch.object(
                    panel, "new_box_lines", side_effect=KeyboardInterrupt
                ), \
                mock.patch.object(
                    panel, "installed_harnesses", return_value=["pi", "claude"]
                ), \
                mock.patch.object(panel, "new_session", new_session):
            with self.assertRaises(SystemExit):
                self.run_loop(
                    groups, [ord("n"), SystemExit],
                    mock.Mock(side_effect=lambda *a: (a[3], a[4])), detach,
                )
        detach.assert_not_called()
        new_session.assert_not_called()


class NewBoxLoopTests(LoopCase):
    """`n` box outcomes through the main loop (P4)."""

    def run_n(self, groups, presses, box=None, prompts=None, draw=None):
        """Press *presses* then `n`; return (snapshots, new_session mock)."""
        snaps = []

        def snapshot(stdscr, groups, flt, cursor, offset, collapsed,
                     message, expanded, notice=None):
            snaps.append((flt, set(collapsed)))
            return cursor, offset

        new_session = mock.Mock(return_value=None)
        box = box or [
            {"action": "goal"},
            {"action": "harness", "harness": "pi"},
        ]
        prompts = prompts if prompts is not None else ["New goal"]
        context = [
            mock.patch.object(panel, "new_box", side_effect=box),
            mock.patch.object(panel, "prompt_line", side_effect=prompts),
            mock.patch.object(panel, "new_session", new_session),
            mock.patch.object(
                panel, "installed_harnesses", return_value=["pi", "claude"]
            ),
        ]
        for patcher in context:
            patcher.start()
            self.addCleanup(patcher.stop)
        with self.assertRaises(SystemExit):
            self.run_loop(
                groups, list(presses) + [SystemExit], draw or snapshot,
                mock.Mock(return_value=""),
            )
        return snaps, new_session

    def test_creating_a_goal_clears_the_project_collapse(self):
        groups, _ = self.make(("A",))
        snaps, new_session = self.run_n(groups, [ord("["), ord("n")])
        self.assertIn("/p", snaps[1][1])  # the project was collapsed
        self.assertNotIn("/p", snaps[-1][1])
        new_session.assert_called_once()
        harness, goal_id = new_session.call_args.args[4:6]
        self.assertEqual(harness, "pi")
        self.assertEqual(
            next(
                x for x in panel.load_goals()[0]["/p"]
                if x.name == "New goal"
            ).id,
            goal_id,
        )

    def test_creating_a_goal_clears_an_active_filter(self):
        groups, _ = self.make(("A",))
        snaps, new_session = self.run_n(
            groups, [ord("/"), ord("n")], prompts=["A", "New goal"]
        )
        self.assertEqual(snaps[1][0], "A")  # the filter was active
        self.assertEqual(snaps[-1][0], "")
        new_session.assert_called_once()

    def test_an_empty_name_starts_nothing(self):
        groups, _ = self.make(("A",))
        before = self.file.read_text()
        snaps, new_session = self.run_n(
            groups, [ord("n")], prompts=[""]
        )
        new_session.assert_not_called()
        self.assertEqual(self.file.read_text(), before)
        self.assertTrue(snaps)

    def test_a_duplicate_name_starts_nothing(self):
        groups, _ = self.make(("A",))
        panel.create_goal("/p", "New goal")
        before = self.file.read_text()
        snaps, new_session = self.run_n(
            groups, [ord("n")], prompts=["new GOAL"]
        )
        new_session.assert_not_called()
        self.assertEqual(self.file.read_text(), before)
        self.assertTrue(snaps)


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



class SliceLifecycleTests(GoalStoreCase):
    def setUp(self):
        super().setUp()
        sessions = [session("d", "/p", 100.0), session("a", "/p", 200.0),
                    session("b", "/p", 50.0)]
        self.groups = [panel.Group(
            "/p", sorted(sessions, key=lambda s: -s.last_ts)
        )]
        self.goal = panel.create_goal("/p", "Goal")
        self.slice = panel.create_slice("/p", self.goal.id, "Slice")
        panel.file_session_in_goal("/p", self.goal.id, "d")
        panel.file_session_in_goal("/p", self.slice.id, "a")
        panel.file_session_in_goal("/p", self.slice.id, "b")
        panel.attach_goals(self.groups)

    def row(self, node_id: str):
        return next(
            r for r in panel.rows_for(self.groups, "")
            if r[0] in ("goal", "slice") and r[2].id == node_id
        )

    def kinds(self):
        return [(k, getattr(x, "id", None))
                for k, _, x in panel.rows_for(self.groups, "")]

    def test_rename_prefill_uses_slice_command(self):
        self.assertEqual(
            panel.rename_prefill(self.row(self.slice.id)),
            ("slice:rename Slice", ""),
        )
        self.assertEqual(
            panel.rename_prefill(self.row(self.goal.id)),
            ("goal:rename Goal", ""),
        )

    def test_apply_slice_rename_and_refuse_duplicate_sibling(self):
        message, key = panel.apply_command(
            "slice:rename  Phase   two", self.row(self.slice.id),
            self.groups, set(),
        )
        self.assertIn("renamed slice", message)
        self.assertEqual(key, ("slice", self.slice.id, "/p"))
        self.assertEqual(self.groups[0].goals[1].name, "Phase two")
        other = panel.create_slice("/p", self.goal.id, "Other")
        panel.attach_goals(self.groups)
        before = self.file.read_text()
        message, key = panel.apply_command(
            "slice:rename Other", self.row(self.slice.id), self.groups, set()
        )
        self.assertIn("already exists", message)
        self.assertIsNone(key)
        self.assertEqual(self.file.read_text(), before)
        ids = [g.id for g in panel.load_goals()[0]["/p"]]
        self.assertIn(other.id, ids)

    def test_slice_create_reopens_a_collapsed_parent(self):
        panel.set_goal_collapsed("/p", self.goal.id, True)
        panel.attach_goals(self.groups)
        collapsed = {"/p"}
        message, key = panel.apply_command(
            "slice:create Phase two", self.row(self.goal.id), self.groups,
            collapsed,
        )
        self.assertIn("created slice 'Phase two'", message)
        self.assertNotIn("/p", collapsed)
        parent = next(x for x in self.groups[0].goals if x.id == self.goal.id)
        self.assertFalse(parent.collapsed)
        self.assertEqual(key[0], "slice")
        rows = list(panel.rows_for(self.groups, ""))
        landed = rows[panel.cursor_for_key(rows, key)]
        self.assertEqual((landed[0], landed[2].id), ("slice", key[1]))

    def test_slice_create_reopens_a_done_parent(self):
        panel.toggle_goal_done("/p", self.goal.id)
        panel.attach_goals(self.groups)
        message, key = panel.apply_command(
            "slice:create Phase two", self.row(self.goal.id), self.groups, set()
        )
        self.assertIn("created slice 'Phase two'", message)
        parent = next(x for x in self.groups[0].goals if x.id == self.goal.id)
        self.assertFalse(parent.done)
        self.assertFalse(parent.collapsed)
        self.assertEqual(key[0], "slice")
        rows = list(panel.rows_for(self.groups, ""))
        landed = rows[panel.cursor_for_key(rows, key)]
        self.assertEqual((landed[0], landed[2].id), ("slice", key[1]))

    def test_hidden_slice_session_lands_on_its_goal_more_row(self):
        for n in range(3):
            node = panel.create_slice("/p", self.goal.id, f"Extra {n}")
            sid = f"x{n}"
            self.groups[0].sessions.append(session(sid, "/p", 300.0 + n))
            panel.file_session_in_goal("/p", node.id, sid)
        panel.attach_goals(self.groups)
        rows = list(panel.rows_for(self.groups, ""))
        more = next(i for i, r in enumerate(rows) if r[0] == "goal_more")
        self.assertEqual(
            panel.row_key(rows[more]), ("goal_more", self.goal.id, "/p")
        )
        self.assertEqual(
            panel.cursor_for_key(rows, ("session", "a", "/p")), more
        )

    def test_session_hidden_inside_its_slice_lands_on_slice_more(self):
        node = panel.create_slice("/p", self.goal.id, "Big slice")
        for n in range(4):
            sid = f"y{n}"
            self.groups[0].sessions.append(session(sid, "/p", 400.0 + n))
            panel.file_session_in_goal("/p", node.id, sid)
        panel.attach_goals(self.groups)
        rows = list(panel.rows_for(self.groups, ""))
        more = next(i for i, r in enumerate(rows) if r[0] == "slice_more")
        self.assertEqual(panel.row_key(rows[more]), ("slice_more", node.id, "/p"))
        self.assertEqual(
            panel.cursor_for_key(rows, ("session", "y0", "/p")), more
        )

    def test_goal_rename_does_not_target_a_slice(self):
        before = self.file.read_text()
        message, key = panel.apply_command(
            "goal:rename nope", self.row(self.slice.id), self.groups, set()
        )
        self.assertIn("highlight a goal", message)
        self.assertIsNone(key)
        self.assertEqual(self.file.read_text(), before)

    def test_done_slice_sinks_among_its_siblings(self):
        before = self.kinds()
        self.assertLess(before.index(("slice", self.slice.id)),
                        before.index(("session", "d")))
        message, key = panel.done_goal(
            self.groups, self.row(self.slice.id)
        )
        self.assertIn("done", message)
        self.assertEqual(key, ("slice", self.slice.id, "/p"))
        after = self.kinds()
        self.assertLess(after.index(("session", "d")),
                        after.index(("slice", self.slice.id)))
        self.assertTrue(self.groups[0].goals[1].done)

    def test_delete_slice_flattens_only_its_sessions(self):
        self.assertEqual(
            panel.delete_prompt(self.row(self.slice.id))[0],
            " Delete slice 'Slice'? y/n ",
        )
        message, key = panel.delete_goal_row(
            self.groups, self.row(self.slice.id)
        )
        self.assertIn("deleted slice", message)
        self.assertEqual(key, ("session", "a", "/p"))
        self.assertEqual([x.name for x in self.groups[0].goals], ["Goal"])
        member_ids = panel.goal_member_ids(self.groups[0])
        self.assertIn("d", member_ids)
        self.assertNotIn("a", member_ids)
        self.assertNotIn("b", member_ids)
        self.assertEqual(len(self.groups[0].sessions), 3)

    def test_delete_goal_cascades_to_slices(self):
        message, key = panel.delete_goal_row(
            self.groups, self.row(self.goal.id)
        )
        self.assertIn("deleted goal", message)
        self.assertEqual(key, ("session", "a", "/p"))
        self.assertEqual(self.groups[0].goals, [])
        self.assertEqual(panel.goal_member_ids(self.groups[0]), set())
        self.assertEqual(len(self.groups[0].sessions), 3)

    def test_corrupt_store_refuses_slice_actions(self):
        self.file.write_text("{ not json")
        panel._goals_cache = None
        row = self.row(self.slice.id)
        self.assertEqual(panel.rename_prefill(row)[0], "")
        self.assertIn("unreadable", panel.rename_prefill(row)[1])
        self.assertEqual(panel.delete_prompt(row)[0], "")
        self.assertIn("unreadable", panel.delete_prompt(row)[1])
        for message in (
            panel.done_goal(self.groups, row)[0],
            panel.delete_goal_row(self.groups, row)[0],
            panel.apply_command("slice:rename x", row, self.groups, set())[0],
        ):
            self.assertIn("unreadable", message)
        message, key = panel.apply_command(
            "slice:create x", self.row(self.goal.id), self.groups, set()
        )
        self.assertIn("unreadable", message)
        self.assertIsNone(key)
        self.assertEqual(self.file.read_text(), "{ not json")

if __name__ == "__main__":
    unittest.main(verbosity=2)
