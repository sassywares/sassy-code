#!/usr/bin/env python3
"""P6 mouse tests: the drawn hit map, click dispatch, drag-to-move and pickers."""
from __future__ import annotations

import curses
import json
import os
import tempfile
import time
import unittest
from unittest import mock

from tests.goal_case import GoalStoreCase, panel, session
from tests.panel_loader import ROOT


class FakeScreen:
    """A curses screen double: records the last text drawn on each line."""

    def __init__(self, h: int = 24, w: int = 80):
        self.h = h
        self.w = w
        self.drawn: dict[int, str] = {}

    def erase(self):
        self.drawn = {}

    def getmaxyx(self):
        return self.h, self.w

    def addnstr(self, y, x, text, n, attr=0):
        self.drawn[y] = text

    def refresh(self):
        pass

    def touchwin(self):
        pass


class FakeWindow:
    """A picker window double that replays queued keys and mouse clicks."""

    def __init__(self, rows, width, y, x, queue, mouse):
        self.rows = rows
        self.width = width
        self.y = y
        self.x = x
        self.queue = queue
        self.mouse = mouse

    def keypad(self, flag):
        pass

    def timeout(self, ms):
        pass

    def erase(self):
        pass

    def box(self):
        pass

    def addnstr(self, *args, **kwargs):
        pass

    def refresh(self):
        pass

    def _mouse(self, mx, my, bstate):
        self.mouse.append((mx, my, bstate))
        return panel.MOUSE_KEY

    def getch(self):
        event = self.queue.pop(0)
        bstate = (
            event[2] if len(event) > 2 else curses.BUTTON1_CLICKED
        )
        if event[0] == "mouse_line":
            return self._mouse(self.x + 1, self.y + 1 + event[1], bstate)
        if event[0] == "mouse_frame":
            return self._mouse(self.x, self.y, bstate)
        if event[0] == "mouse_outside":
            return self._mouse(
                self.x + self.width, self.y + self.rows + 2, bstate
            )
        return event[1]


class MouseCase(unittest.TestCase):
    """Patch the only curses call that needs a live terminal during draw."""

    def setUp(self):
        patcher = mock.patch.object(
            curses, "color_pair", return_value=0
        )
        patcher.start()
        self.addCleanup(patcher.stop)


class StoreMouseCase(GoalStoreCase, MouseCase):
    """Goal-store temp dir plus the draw-time curses patch."""

    def setUp(self):
        GoalStoreCase.setUp(self)
        MouseCase.setUp(self)


def rows_for(groups, **kwargs):
    return list(panel.rows_for(groups, "", set(), **kwargs))


def sorted_group(cwd: str, count: int, start: float = 100.0) -> panel.Group:
    sessions = [
        session(f"{cwd}-{i}", cwd, start + i) for i in range(count)
    ]
    sessions.sort(key=lambda s: s.last_ts, reverse=True)
    return panel.Group(cwd, sessions)


class MouseMaskTests(unittest.TestCase):
    """main_curses enables mouse reporting and restores the old mask."""

    def run_main(self, old_mask: int, failure: BaseException | None = None):
        calls = []

        def mousemask(mask):
            calls.append(mask)
            return curses.ALL_MOUSE_EVENTS, old_mask

        run = mock.Mock(side_effect=failure)
        with mock.patch.object(
            curses, "mousemask", side_effect=mousemask
        ), mock.patch.object(panel.main, "_main_curses", run):
            if failure is not None:
                with self.assertRaises(type(failure)):
                    panel.main.main_curses(object(), "/p")
            else:
                panel.main.main_curses(object(), "/p")
        run.assert_called_once()
        return calls

    def test_enable_and_restore_pair(self):
        calls = self.run_main(0x2A)
        self.assertEqual(
            calls, [curses.ALL_MOUSE_EVENTS, 0x2A]
        )

    def test_previous_mask_is_restored_after_an_exception(self):
        calls = self.run_main(0x2A, RuntimeError("boom"))
        self.assertEqual(
            calls, [curses.ALL_MOUSE_EVENTS, 0x2A]
        )


class HitMapTests(MouseCase):
    def test_plain_draw_maps_every_visible_row_and_off_panel(self):
        g = sorted_group("/p", 3)
        screen = FakeScreen(20, 60)
        rows = rows_for([g])
        _cursor, _offset, hits = panel.draw(
            screen, [g], "", 0, 0, set()
        )
        for i, row in enumerate(rows):
            self.assertEqual(hits[1 + i], row)
        self.assertIsNone(panel.hit_test(hits, 100))
        self.assertIsNone(panel.hit_test(hits, 0))

    def test_scrolled_window_maps_rows_below_the_visible_area(self):
        g = sorted_group("/p", 25)
        screen = FakeScreen(12, 60)
        rows = rows_for([g], expanded={"/p": 4})
        _cursor, offset, hits = panel.draw(
            screen, [g], "", 20, 0, set(), expanded={"/p": 4}
        )
        self.assertGreater(offset, 0)
        # list_top is 1 without a filter; every visible line maps to its row.
        for i in range(10):
            y = 1 + i
            if y not in hits:
                break
            self.assertEqual(
                panel.row_key(hits[y]), panel.row_key(rows[offset + i])
            )
        beyond = rows[offset + 1]
        self.assertEqual(panel.hit_test(hits, 2), beyond)

    def test_project_more_row_maps_and_its_direction_flips(self):
        g = sorted_group("/p", 7)
        screen = FakeScreen(20, 60)
        _cursor, _offset, hits = panel.draw(screen, [g], "", 0, 0, set())
        more_y = next(
            y for y, item in hits.items()
            if item is not None and item[0] == "more"
        )
        more = hits[more_y]
        self.assertIs(panel.hit_test(hits, more_y), more)
        self.assertEqual(panel.more_click_action(more), "show")

        _cursor, _offset, hits = panel.draw(
            screen, [g], "", 0, 0, set(), expanded={"/p": 1}
        )
        shown_y = next(
            y for y, item in hits.items()
            if item is not None and item[0] == "more"
        )
        shown = hits[shown_y]
        self.assertTrue(shown[2][-1])
        self.assertEqual(panel.more_click_action(shown), "hide")

    def test_hint_and_gap_lines_are_unselectable(self):
        one = panel.Group(
            "/one",
            [session("a", "/one", 100.0)],
            goals=[panel.Goal("g1", "Empty", 50.0)],
        )
        two = panel.Group("/two", [session("b", "/two", 200.0)])
        screen = FakeScreen(20, 60)
        _cursor, _offset, hits = panel.draw(
            screen, [one, two], "", 0, 0, set()
        )
        rows = rows_for([one, two])
        labels = panel.canonical_group_names([one, two])
        lines, _starts, _heights = panel.build_lines(
            rows, 60, set(), "", labels
        )
        unselectable = [
            1 + i for i, (_text, kind, _idx) in enumerate(lines)
            if kind in ("hint", "gap")
        ]
        self.assertTrue(unselectable)
        for y in unselectable:
            self.assertIsNone(panel.hit_test(hits, y))

    def test_row_kind_changes_between_two_draws(self):
        one = sorted_group("/one", 3)
        two = sorted_group("/two", 1, start=500.0)
        screen = FakeScreen(20, 60)
        _cursor, _offset, before = panel.draw(
            screen, [one, two], "", 0, 0, set()
        )
        _cursor, _offset, after = panel.draw(
            screen, [one, two], "", 0, 0, {"/one"}
        )
        self.assertEqual(before[3][0], "session")
        self.assertEqual(after[3][0], "group")
        self.assertNotEqual(
            panel.row_key(panel.hit_test(before, 3)),
            panel.row_key(panel.hit_test(after, 3)),
        )


class GoalHitMapTests(StoreMouseCase):
    def test_goal_more_and_slice_more_rows_are_keyed_in_the_hit_map(self):
        sessions = [
            session(f"g{i}", "/p", 100.0 + i) for i in range(5)
        ] + [
            session(f"s{i}", "/p", 1000.0 + i) for i in range(5)
        ]
        g = panel.Group("/p", sessions)
        goal = panel.create_goal("/p", "Goal")
        slice_ = panel.create_slice("/p", goal.id, "Slice")
        for s in sessions[:5]:
            panel.file_session_in_goal("/p", goal.id, s.id)
        for s in sessions[5:]:
            panel.file_session_in_goal("/p", slice_.id, s.id)
        panel.attach_goals([g])
        screen = FakeScreen(30, 70)
        _cursor, _offset, hits = panel.draw(screen, [g], "", 0, 0, set())
        kinds = {
            item[0] for item in hits.values() if item is not None
        }
        self.assertIn("goal_more", kinds)
        self.assertIn("slice_more", kinds)


class ClickDispatchTests(MouseCase):
    def setUp(self):
        super().setUp()
        self.g = panel.Group("/p", [session("a", "/p", 100.0)])
        self.goal = panel.Goal("g1", "Goal", 50.0)
        self.slice = panel.Goal("s1", "Slice", 60.0, parent="g1")
        self.session_row = ("session", self.g, self.g.sessions[0])

    def test_single_click_selects(self):
        self.assertEqual(
            panel.click_kind(self.session_row, 10.0, None, 0.0), "select"
        )
        other = ("session", self.g, session("b", "/p", 90.0))
        self.assertEqual(
            panel.click_kind(
                other, 10.0, panel.row_key(self.session_row), 9.9
            ),
            "select",
        )

    def test_second_click_within_the_window_presents(self):
        key = panel.row_key(self.session_row)
        first = 0.0
        window = panel.core.DOUBLE_CLICK_SECONDS
        self.assertEqual(
            panel.click_kind(
                self.session_row, first + window / 2, key, first
            ),
            "present",
        )
        self.assertEqual(
            panel.click_kind(self.session_row, first + window, key, first),
            "present",
        )
        self.assertEqual(
            panel.click_kind(
                self.session_row, first + window + 0.0001, key, first
            ),
            "select",
        )
        self.assertEqual(
            panel.click_kind(self.session_row, first + window + 1.0, key,
                             first),
            "select",
        )

    def test_heading_goal_and_slice_clicks_toggle(self):
        for row in (
            ("group", self.g, None),
            ("goal", self.g, self.goal),
            ("slice", self.g, self.slice),
        ):
            self.assertEqual(panel.click_kind(row, 10.0, None, 0.0), "toggle")

    def test_more_click_moves_one_tier_and_direction_is_payload(self):
        for kind in ("more", "goal_more", "slice_more"):
            payload = (
                (4, False) if kind == "more"
                else (self.goal, 4, False)
            )
            row = (kind, self.g, payload)
            self.assertEqual(panel.click_kind(row, 10.0, None, 0.0), "tier")
            self.assertEqual(panel.more_click_action(row), "show")
            payload = (
                (0, True) if kind == "more"
                else (self.goal, 0, True)
            )
            self.assertEqual(
                panel.more_click_action((kind, self.g, payload)), "hide"
            )

    def test_clicking_nothing_does_nothing(self):
        self.assertIsNone(panel.click_kind(None, 10.0, None, 0.0))


class WheelTests(MouseCase):
    def test_wheel_masks_classify_both_directions(self):
        for name in ("BUTTON4_PRESSED", "BUTTON4_CLICKED"):
            if hasattr(curses, name):
                self.assertTrue(panel.core.MOUSE_WHEEL_UP & getattr(
                    curses, name
                ))
        self.assertEqual(
            panel.mouse_kind(panel.core.MOUSE_WHEEL_UP), "wheel_up"
        )
        if panel.core.MOUSE_WHEEL_DOWN:
            self.assertEqual(
                panel.mouse_kind(panel.core.MOUSE_WHEEL_DOWN), "wheel_down"
            )

    def test_shift_and_alt_clicks_are_not_wheel_notches(self):
        modifiers = (
            curses.BUTTON_SHIFT,
            curses.BUTTON_ALT,
            curses.BUTTON_SHIFT | curses.BUTTON_ALT,
        )
        buttons = (
            curses.BUTTON1_PRESSED,
            curses.BUTTON1_RELEASED,
            curses.BUTTON1_CLICKED,
            curses.BUTTON1_DOUBLE_CLICKED,
        )
        for modifier in modifiers:
            for button in buttons:
                self.assertNotIn(
                    panel.mouse_kind(button | modifier),
                    ("wheel_up", "wheel_down"),
                )

    def test_button_two_release_is_not_a_wheel_notch(self):
        self.assertNotIn(
            panel.mouse_kind(curses.BUTTON2_RELEASED),
            ("wheel_up", "wheel_down"),
        )
        self.assertNotIn(
            panel.mouse_kind(curses.BUTTON2_RELEASED
                             | curses.BUTTON1_RELEASED),
            ("wheel_up", "wheel_down"),
        )

    def test_bare_modifier_bits_are_not_wheel_notches(self):
        for bstate in (
            curses.BUTTON_SHIFT,
            curses.BUTTON_ALT,
            curses.BUTTON_CTRL,
            curses.BUTTON_SHIFT | curses.BUTTON_ALT,
        ):
            self.assertNotIn(
                panel.mouse_kind(bstate), ("wheel_up", "wheel_down")
            )

    def test_a_build_with_button5_uses_its_real_masks(self):
        # A stand-in for a BUTTON5_PRESSED|CLICKED mask, whatever bits the
        # build actually uses.
        real = 1 << 30
        with mock.patch.multiple(panel.core, MOUSE_WHEEL_DOWN=real):
            self.assertEqual(panel.mouse_kind(real), "wheel_down")
            self.assertEqual(
                panel.mouse_kind(real | curses.BUTTON1_PRESSED),
                "wheel_down",
            )
        with mock.patch.multiple(panel.core, MOUSE_WHEEL_DOWN=0):
            self.assertEqual(panel.mouse_kind(real), "")

    def test_motion_and_middle_or_right_presses_are_unclassified(self):
        self.assertEqual(panel.mouse_kind(0), "")
        for bstate in (
            curses.BUTTON2_PRESSED,
            curses.BUTTON3_PRESSED,
            curses.BUTTON2_RELEASED,
            curses.BUTTON3_CLICKED,
        ):
            self.assertEqual(panel.mouse_kind(bstate), "")

    def test_button_press_release_click_classification(self):
        self.assertEqual(
            panel.mouse_kind(curses.BUTTON1_PRESSED), "press"
        )
        self.assertEqual(
            panel.mouse_kind(curses.BUTTON1_RELEASED), "release"
        )
        self.assertEqual(
            panel.mouse_kind(curses.BUTTON1_CLICKED), "click"
        )
        self.assertEqual(panel.mouse_kind(0), "")

    def test_wheel_notches_move_the_highlight_one_row_and_clamp(self):
        self.assertEqual(panel.wheel_cursor(5, 2, "wheel_up"), 1)
        self.assertEqual(panel.wheel_cursor(5, 2, "wheel_down"), 3)
        self.assertEqual(panel.wheel_cursor(5, 0, "wheel_up"), 0)
        self.assertEqual(panel.wheel_cursor(5, 4, "wheel_down"), 4)
        self.assertEqual(panel.wheel_cursor(0, 0, "wheel_down"), 0)

    def test_a_duplicated_notch_moves_exactly_one_row(self):
        for kind in ("wheel_up", "wheel_down"):
            cursor = 2
            last = None
            # A press then click, then a prolonged repeat of the same notch.
            for now in (10.0, 10.001, 10.049):
                fresh, last = panel.wheel_notch(kind, now, last)
                if fresh:
                    cursor = panel.wheel_cursor(5, cursor, kind)
            expected = 1 if kind == "wheel_up" else 3
            self.assertEqual(cursor, expected)
            # A real gap is a new notch, and a direction change is too.
            fresh, last = panel.wheel_notch(kind, 10.1, last)
            self.assertTrue(fresh)
            cursor = panel.wheel_cursor(5, cursor, kind)
            self.assertEqual(cursor, expected + (-1 if kind == "wheel_up" else 1))
            other = "wheel_down" if kind == "wheel_up" else "wheel_up"
            fresh, last = panel.wheel_notch(other, 10.11, last)
            self.assertTrue(fresh)

    def test_press_and_click_bits_in_one_bstate_are_one_notch(self):
        both = (
            curses.BUTTON4_PRESSED | curses.BUTTON4_CLICKED
        )
        self.assertEqual(panel.mouse_kind(both), "wheel_up")
        self.assertEqual(panel.wheel_cursor(5, 2, panel.mouse_kind(both)), 1)


class MouseDebugTests(unittest.TestCase):
    def test_debug_appends_one_line_per_event_when_set(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "mouse.log")
            with mock.patch.dict(
                os.environ, {"SASSY_CODE_MOUSE_DEBUG": path}
            ):
                panel.mouse_debug(0x2000000, 3, 4, "wheel_down")
                panel.mouse_debug(5, 6, 7, "")
            with open(path, encoding="utf-8") as fh:
                self.assertEqual(
                    fh.read(),
                    "bstate=0x2000000 x=3 y=4 kind=wheel_down\n"
                    "bstate=0x5 x=6 y=7 kind=\n",
                )

    def test_debug_is_inert_when_unset_and_never_raises(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "mouse.log")
            with mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop("SASSY_CODE_MOUSE_DEBUG", None)
                panel.mouse_debug(1, 2, 3, "wheel_down")
            self.assertFalse(os.path.exists(path))
            with mock.patch.dict(
                os.environ,
                {"SASSY_CODE_MOUSE_DEBUG": os.path.join(d, "missing", "x.log")},
            ):
                panel.mouse_debug(1, 2, 3, "wheel_down")  # no directory, no raise
            # The guard's "repo" is the repo root, i.e. the package's parent,
            # not the package directory itself.
            inside = os.path.join(
                str(ROOT), "mouse-debug-should-not-exist.log"
            )
            with mock.patch.dict(
                os.environ, {"SASSY_CODE_MOUSE_DEBUG": inside}
            ):
                panel.mouse_debug(1, 2, 3, "wheel_down")
            self.assertFalse(os.path.exists(inside))


class DragMoveTests(StoreMouseCase):
    def setUp(self):
        super().setUp()
        self.a = session("a", "/p", 300.0, title="Alpha")
        self.b = session("b", "/p", 200.0, title="Beta")
        self.c = session("c", "/p", 100.0, title="Gamma")
        self.g = panel.Group("/p", [self.a, self.b, self.c])
        self.goal = panel.create_goal("/p", "Goal")
        self.slice = panel.create_slice("/p", self.goal.id, "Slice")
        panel.attach_goals([self.g])
        self.drag = ("session", self.g, self.a)

    def node(self, goal_id):
        return next(x for x in self.g.goals if x.id == goal_id)

    def test_press_only_starts_a_drag_on_a_session_row(self):
        screen = FakeScreen(30, 80)
        _cursor, _offset, hits = panel.draw(
            screen, [self.g], "", 0, 0, set(), expanded={"/p": 1}
        )
        session_y = next(
            y for y, item in hits.items()
            if item is not None
            and item[0] == "session" and item[2].id == "a"
        )
        group_y = next(
            y for y, item in hits.items()
            if item is not None and item[0] == "group"
        )
        goal_y = next(
            y for y, item in hits.items()
            if item is not None and item[0] == "goal"
        )
        self.assertEqual(panel.mouse_press(hits, session_y), self.drag)
        self.assertIsNone(panel.mouse_press(hits, group_y))
        self.assertIsNone(panel.mouse_press(hits, goal_y))
        for y, item in hits.items():
            if item is None:
                self.assertIsNone(panel.mouse_press(hits, y))

    def test_a_press_while_a_drag_is_outstanding_keeps_its_session(self):
        other = ("session", self.g, self.b)
        hits = {7: other}
        self.assertIs(panel.drag_press(self.drag, hits, 7), self.drag)
        self.assertEqual(
            panel.drag_press(None, hits, 7), other
        )
        # A press on a non-session row never becomes a drag either.
        self.assertIsNone(
            panel.drag_press(None, {7: ("group", self.g, None)}, 7)
        )

    def test_a_held_drag_expires_only_after_the_stated_age(self):
        limit = panel.core.DRAG_EXPIRE_SECONDS
        self.assertFalse(panel.drag_expired(100.0, 100.0))
        self.assertFalse(panel.drag_expired(100.0, 100.0 + limit))
        self.assertTrue(panel.drag_expired(100.0, 100.0 + limit + 0.001))
        self.assertFalse(panel.drag_expired(100.0, 100.0 + limit - 0.001))

    def test_mouse_traffic_enforces_drag_expiry_without_an_idle_tick(self):
        events = [panel.MOUSE_KEY] * 3 + [ord("q")]
        mouse = [
            (0, 1, curses.BUTTON1_PRESSED),
            (0, 1, 0),  # ignored pointer motion, not an idle -1 tick
            (0, 2, curses.BUTTON1_RELEASED),  # the stale release
        ]
        hits = {
            1: ("session", self.g, self.a),
            2: ("goal", self.g, self.goal),
        }
        now = [100.0]

        class LoopScreen(FakeScreen):
            def timeout(self, ms):
                pass

            def getch(self):
                return events.pop(0)

        def advancing_time():
            now[0] += 1.0
            return now[0]

        def getmouse():
            _mx, my, bstate = mouse.pop(0)
            return 0, _mx, my, 0, bstate

        with mock.patch.multiple(
            panel.status,
            load_groups=lambda: [self.g],
        ), mock.patch.multiple(
            panel.view,
            draw=lambda *args, **kwargs: (0, 0, hits),
        ), mock.patch.multiple(
            panel.tmuxctl,
            panel_pane_id=lambda: "",
            set_panel_tag=lambda pane: None,
        ), mock.patch.object(
            panel.core, "DRAG_EXPIRE_SECONDS", 0.5
        ), mock.patch.object(
            panel.goals, "attach_goals", lambda groups: False
        ), mock.patch.object(
            panel.state, "migrate_state", lambda: None
        ), mock.patch.object(
            panel.state, "_read_state", lambda: "a"
        ), mock.patch.multiple(
            curses,
            curs_set=lambda *args: None,
            start_color=lambda: None,
            use_default_colors=lambda: None,
            init_pair=lambda *args: None,
            set_escdelay=lambda *args: None,
            getmouse=getmouse,
        ), mock.patch.object(
            time, "time", side_effect=advancing_time
        ), mock.patch.dict(
            "os.environ", {"TMUX": ""}
        ), mock.patch.object(
            panel.actions, "move_session_row", return_value=(None, None)
        ) as move:
            panel.main._main_curses(LoopScreen(20, 80), "/p")

        move.assert_not_called()

    def test_release_on_goal_and_slice_uses_the_move_path(self):
        for goal_id, kind in ((self.goal.id, "goal"), (self.slice.id, "slice")):
            target_node = self.node(goal_id)
            hit = (kind, self.g, target_node)
            outcome = panel.mouse_release("release", self.drag, hit)
            self.assertEqual(
                outcome, ("move", (panel.MOVE_GOAL, target_node))
            )
            panel.move_session_row([self.g], self.drag, outcome[1])
            panel.attach_goals([self.g])
            self.assertIn(
                "a",
                {s.id for s in panel.goal_members(
                    self.g, self.node(goal_id)
                )},
            )
            panel.move_session("/p", "a", None)
            panel.attach_goals([self.g])

    def test_release_on_the_heading_makes_the_session_loose(self):
        panel.file_session_in_goal("/p", self.goal.id, "a")
        panel.attach_goals([self.g])
        heading = ("group", self.g, None)
        outcome = panel.mouse_release("release", self.drag, heading)
        self.assertEqual(outcome, ("move", (panel.MOVE_LOOSE, None)))
        panel.move_session_row([self.g], self.drag, outcome[1])
        panel.attach_goals([self.g])
        self.assertIsNone(panel.goal_of_session(self.g, "a"))
        stored = json.loads(self.file.read_text())
        self.assertIn("a", stored["loose"]["/p"])

    def test_release_on_another_session_a_hint_or_a_gap_cancels(self):
        before = self.file.read_bytes()
        other_project = panel.Group("/q", [session("z", "/q", 10.0)])
        for hit in (
            ("session", self.g, self.b),
            ("group", other_project, None),
            None,
        ):
            self.assertIsNone(
                panel.mouse_release("release", self.drag, hit)
            )
        self.assertEqual(self.file.read_bytes(), before)

    def test_release_on_the_pressed_row_is_a_click(self):
        self.assertEqual(
            panel.mouse_release("release", self.drag, self.drag),
            ("click", self.drag),
        )
        self.assertIsNone(panel.mouse_release("release", None, self.drag))

    def test_release_on_a_non_session_row_behaves_like_a_click(self):
        rows = (
            ("group", self.g, None),
            ("goal", self.g, self.goal),
            ("slice", self.g, self.slice),
            ("more", self.g, (4, False)),
            ("goal_more", self.g, (self.goal, 4, False)),
            ("slice_more", self.g, (self.slice, 4, False)),
        )
        for row in rows:
            self.assertEqual(
                panel.mouse_release("release", None, row), ("click", row)
            )
            self.assertEqual(
                panel.mouse_release("click", None, row), ("click", row)
            )
        self.assertIsNone(panel.mouse_release("release", None, None))

    def test_drag_footer_shows_the_held_session_title(self):
        screen = FakeScreen(20, 80)
        panel.draw(
            screen, [self.g], "", 0, 0, set(),
            f" moving {self.a.title}… ",
        )
        self.assertTrue(
            any("moving Alpha…" in text for text in screen.drawn.values())
        )


class TierClickLoopTests(StoreMouseCase):
    """A click on `… show fewer` hides one tier, like its key (P1)."""

    def test_click_on_show_fewer_hides_one_tier(self):
        sessions = [session(f"s{i}", "/p", 100.0 - i) for i in range(10)]
        g = panel.Group("/p", sessions)
        events = [ord("]"), panel.MOUSE_KEY, SystemExit]
        mouse = [(10, 12, curses.BUTTON1_CLICKED)]
        snaps = []

        class LoopScreen(FakeScreen):
            def timeout(self, ms):
                pass

            def getch(self):
                event = events.pop(0)
                if isinstance(event, type):
                    raise event
                return event

        real_draw = panel.view.draw

        def draw(stdscr, groups, flt, cursor, offset, collapsed, message,
                 expanded, notice):
            snaps.append((cursor, dict(expanded)))
            _c, _o, hits = real_draw(
                stdscr, groups, flt, cursor, offset, collapsed, message,
                expanded, notice,
            )
            return cursor, offset, hits

        def getmouse():
            mx, my, bstate = mouse.pop(0)
            return 0, mx, my, 0, bstate

        with mock.patch.multiple(
            panel.status,
            load_groups=lambda: [g],
        ), mock.patch.multiple(
            panel.view,
            draw=draw,
        ), mock.patch.multiple(
            panel.tmuxctl,
            panel_pane_id=lambda: "",
            set_panel_tag=lambda pane: None,
        ), mock.patch.object(
            panel.goals, "attach_goals", lambda groups: False
        ), mock.patch.object(
            panel.state, "migrate_state", lambda: None
        ), mock.patch.object(
            panel.state, "_read_state", lambda: "s0"
        ), mock.patch.multiple(
            curses,
            curs_set=lambda *args: None,
            start_color=lambda: None,
            use_default_colors=lambda: None,
            init_pair=lambda *args: None,
            set_escdelay=lambda *args: None,
            getmouse=getmouse,
        ), mock.patch.dict("os.environ", {"TMUX": ""}):
            with self.assertRaises(SystemExit):
                panel.main._main_curses(LoopScreen(20, 80), "/p")

        self.assertEqual(snaps[1][1], {"/p": 1})  # `]` revealed
        self.assertEqual(snaps[-1][1], {})  # the click hid one tier


class PickerClickTests(StoreMouseCase):
    def setUp(self):
        super().setUp()
        self.g = panel.Group("/p", [session("a", "/p", 300.0, title="Alpha")])
        self.goal = panel.create_goal("/p", "Goal")
        self.slice = panel.create_slice("/p", self.goal.id, "Slice")
        panel.attach_goals([self.g])

    def run_picker(self, call, events, size=(24, 80)):
        screen = FakeScreen(*size)
        queue = list(events)
        mouse = []

        def newwin(rows, width, y, x):
            return FakeWindow(rows, width, y, x, queue, mouse)

        def getmouse():
            mx, my, bstate = mouse.pop(0)
            return 0, mx, my, 0, bstate

        with mock.patch.object(
            curses, "newwin", side_effect=newwin
        ), mock.patch.object(
            curses, "getmouse", side_effect=getmouse
        ):
            return call(screen)

    def new_box(self, events):
        return self.run_picker(
            lambda scr: panel.new_box(
                scr, ("group", self.g, None), ["pi", "claude"], "pi"
            ),
            events,
        )

    def move_box(self, events):
        return self.run_picker(
            lambda scr: panel.move_box(scr, self.g, self.g.sessions[0]),
            events,
        )

    def test_click_picks_an_item_in_the_new_session_box(self):
        # Lines: 0 the Goal item, 1 the divider, 2 the first harness,
        # 3 the second.
        self.assertEqual(
            self.new_box([("mouse_line", 3)]),  # the second harness
            {"action": "harness", "harness": "claude"},
        )

    def test_click_picks_an_item_in_the_move_box(self):
        state = panel.move_box_start(self.g, self.g.sessions[0])
        drawn = panel.move_box_lines(state)
        index = next(
            i for i, (_text, target, kind) in enumerate(drawn)
            if kind != panel.PICKER_DIVIDER
            and target is not None
            and state["targets"][target][0] == panel.MOVE_GOAL
        )
        result = self.move_box([("mouse_line", index)])
        self.assertEqual(result[0], panel.MOVE_GOAL)
        self.assertEqual(result[1].id, self.goal.id)

    def test_click_on_the_move_box_divider_does_not_pick(self):
        self.assertIsNone(
            self.move_box([("mouse_line", 2), ("key", ord("q"))])
        )

    def test_click_on_the_new_box_divider_does_not_pick(self):
        self.assertIsNone(
            self.new_box([("mouse_line", 1), ("key", ord("q"))])
        )

    def test_press_then_release_on_an_item_picks_it(self):
        # Line 3 is the second harness, below the divider.
        pressed = [("mouse_line", 3, curses.BUTTON1_PRESSED)]
        released = [("mouse_line", 3, curses.BUTTON1_RELEASED)]
        self.assertEqual(
            self.new_box(pressed + released),
            {"action": "harness", "harness": "claude"},
        )

    def test_click_outside_either_box_cancels(self):
        self.assertIsNone(self.new_box([("mouse_outside",)]))
        self.assertIsNone(self.move_box([("mouse_outside",)]))

    def test_outside_press_then_outside_release_cancels_either_box(self):
        events = [
            ("mouse_outside", 0, curses.BUTTON1_PRESSED),
            ("mouse_outside", 0, curses.BUTTON1_RELEASED),
        ]
        self.assertIsNone(self.new_box(events))
        self.assertIsNone(self.move_box(events))

    def test_click_on_a_frame_does_not_pick(self):
        # The frame is inside the box but not a pickable item; the box stays
        # open, so the next event has to cancel it.
        self.assertIsNone(
            self.new_box([("mouse_frame",), ("key", ord("q"))])
        )
        self.assertIsNone(
            self.move_box([("mouse_frame",), ("key", ord("q"))])
        )

    def test_wheel_and_other_buttons_do_not_pick_in_either_box(self):
        ignored = (
            curses.BUTTON4_CLICKED,  # a wheel notch
            curses.BUTTON3_CLICKED,  # right button
            curses.BUTTON2_CLICKED,  # middle button
        )
        for bstate in ignored:
            with self.subTest(bstate=bstate, box="new"):
                self.assertIsNone(self.new_box([
                    ("mouse_line", 2, bstate), ("key", ord("q"))
                ]))
            with self.subTest(bstate=bstate, box="move"):
                self.assertIsNone(self.move_box([
                    ("mouse_line", 1, bstate), ("key", ord("q"))
                ]))

    def test_keyboard_digit_still_picks_after_an_ignored_mouse_event(self):
        self.assertEqual(
            self.new_box([
                ("mouse_line", 2, curses.BUTTON4_CLICKED),
                ("key", ord("2")),
            ]),
            {"action": "harness", "harness": "claude"},
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
