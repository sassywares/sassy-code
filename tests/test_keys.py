#!/usr/bin/env python3
"""The keymap is the one table behind footers, the `?` menu and the README."""
from __future__ import annotations

import inspect
import re
import unittest
from pathlib import Path
from unittest import mock

from tests.panel_loader import ROOT, load_panel

panel = load_panel()


def readme_rows() -> list[tuple[str, str, str]]:
    """The README § Keys table as (scope, keys, action) rows."""
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    section = text.split("## Keys", 1)[1].split("\n## ", 1)[0]
    rows = []
    for line in section.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if cells == ["Scope", "Keys", "Action"] or all(
            set(cell) <= {"-", ":"} for cell in cells
        ):
            continue
        if len(cells) != 3:
            raise AssertionError(f"bad README key row: {line}")
        rows.append(tuple(cells))
    return rows


class ReadmeMirrorTests(unittest.TestCase):
    def test_readme_table_mirrors_keymap(self):
        expected = [
            (r.scope, r.keys, r.action) for r in panel.keymap.listed_rows()
        ]
        self.assertEqual(readme_rows(), expected)

    def test_unlisted_rows_are_not_in_the_readme(self):
        table = readme_rows()
        for row in panel.keymap.ROWS:
            if not row.listed:
                with self.subTest(row=row.keys):
                    self.assertNotIn(
                        (row.scope, row.keys, row.action), table
                    )

    def test_hidden_aliases_stay_in_the_table(self):
        by_scope = {}
        for row in panel.keymap.ROWS:
            by_scope.setdefault(row.scope, []).append(row)
        move = by_scope["List"][0]
        self.assertEqual(move.keys, "↑ ↓")
        for alias in ("j", "k"):
            self.assertIn(alias, move.also)
        # The display rule is uniform: every navigation row trims j/k to `also`.
        for scope in ("New box", "Move box", "Trash box", "Keys menu"):
            nav = by_scope[scope][0]
            with self.subTest(scope=scope):
                self.assertEqual(nav.keys, "↑ ↓")
                for alias in ("j", "k"):
                    self.assertIn(alias, nav.also)
        tier = next(r for r in by_scope["List"] if r.keys == "[ ]")
        for alias in ("h", "l", "←", "→"):
            self.assertIn(alias, tier.also)
        jump = next(r for r in by_scope["List"] if r.keys == "g G")
        self.assertFalse(jump.menu)
        self.assertFalse(jump.listed)

    def test_every_row_has_a_scope_and_a_purpose(self):
        for row in panel.keymap.ROWS:
            self.assertTrue(row.scope)
            self.assertTrue(row.keys)
            self.assertTrue(row.action)


class FooterTests(unittest.TestCase):
    def rows(self):
        s = panel.Session(
            path="", id="s", cwd="/p", created=0.0, last_ts=0.0, name="S"
        )
        g = panel.Group(cwd="/p", sessions=[s])
        goal = panel.Goal(id="g", name="G", created=0.0, sessions=["s"])
        g.goals = [goal]
        return s, g, goal

    def test_exact_d14_footers(self):
        s, g, goal = self.rows()
        self.assertEqual(
            panel.footer_items(("group", g, None)), ("⏎ toggle", "n new")
        )
        self.assertEqual(
            panel.footer_items(("session", g, s)),
            ("⏎ present", "R rename", "m move", "x done", "D delete"),
        )
        self.assertEqual(
            panel.footer_items(("goal", g, goal)),
            ("⏎ toggle", "n new", "R rename", "x done", "D delete"),
        )
        self.assertEqual(
            panel.footer_items(("slice", g, goal)),
            ("⏎ toggle", "n new", "R rename", "x done", "D delete"),
        )
        self.assertEqual(
            panel.footer_items(("more", g, (2, False))), ("⏎ more",)
        )
        self.assertEqual(
            panel.footer_items(("more", g, (0, True))), ("⏎ less",)
        )
        self.assertEqual(panel.footer_items(None), ("↑/↓ move", "? keys"))
        self.assertEqual(
            panel.footer_for_scope("Trash entry"), ("⏎ restore", "d purge")
        )

    def test_done_row_offers_undo(self):
        s, g, goal = self.rows()
        s.status = "done"
        goal.done = True
        self.assertIn("x undo", panel.footer_items(("session", g, s)))
        self.assertIn("x undo", panel.footer_items(("goal", g, goal)))
        self.assertNotIn("x done", panel.footer_items(("goal", g, goal)))

class MenuTests(unittest.TestCase):
    def assert_scroll_keeps_selection(self, width, predicate):
        """Press every scroll key to an end; the selection stays selectable."""
        lines, _selected = panel.help_lines(panel.help_start(width))
        for key in (
            ord("j"), panel.curses.KEY_DOWN,
            ord("k"), panel.curses.KEY_UP,
            ord("g"), ord("G"),
        ):
            state = panel.help_start(width)
            for _step in range(len(lines) + 1):
                state, _action = panel.help_box_key(state, key)
                self.assertTrue(predicate(state["index"], lines))

    def test_menu_sections_and_no_entity_actions(self):
        lines, selected = panel.help_lines({"index": 0})
        text = "\n".join(line for line, _attr, _kind in lines)
        for heading in ("Global", "Tmux", "Mouse"):
            self.assertIn(heading, text)
        self.assertNotIn("In boxes", text)
        for entity in ("⏎ present", "R rename", "m move", "D delete"):
            self.assertNotIn(entity, text)
        for box in ("close the box", "pick a target", "scroll the menu"):
            self.assertNotIn(box, text)
        self.assertEqual(lines[selected][2], "row")

    def test_menu_does_not_repeat_the_footer_navigation(self):
        self.assertNotIn("No row", {r.scope for r in panel.keymap.menu_rows()})
        # No global row carries a footer hint, and no entity row is in the menu.
        self.assertFalse(
            any(r.menu and r.footer for r in panel.keymap.ROWS)
        )

    def test_menu_scrolls_and_closes(self):
        state = panel.help_start()
        rows = state["rows"]
        state, action = panel.help_box_key(state, ord("G"))
        self.assertEqual(action, "")
        self.assertEqual(state["index"], rows[-1])
        state, action = panel.help_box_key(state, ord("g"))
        self.assertEqual(state["index"], rows[0])
        for key in (ord("j"), panel.curses.KEY_DOWN):
            state["index"] = rows[0]
            state, action = panel.help_box_key(state, key)
            self.assertEqual(action, "")
            self.assertEqual(state["index"], rows[1])
        for key in (ord("?"), 27, ord("q"), 3):
            _state, action = panel.help_box_key(panel.help_start(), key)
            self.assertEqual(action, "cancel")

    def test_narrow_menu_wraps_instead_of_clipping(self):
        width = 40
        lines, selected = panel.help_lines({"index": 0, "width": width})
        kinds = {kind for _text, _attr, kind in lines}
        self.assertIn("wrap", kinds)
        for text, _attr, _kind in lines:
            with self.subTest(text=text):
                self.assertLessEqual(
                    panel.core.display_width(text), width - 4
                )
        self.assertEqual(lines[selected][2], "row")

    def test_continuation_lines_are_never_selected(self):
        width = 40
        start = panel.help_start(width)
        lines, _selected = panel.help_lines(start)
        wraps = [i for i, (_t, _a, kind) in enumerate(lines) if kind == "wrap"]
        rows = set(start["rows"])
        self.assertTrue(wraps)
        self.assertFalse(rows & set(wraps))
        self.assert_scroll_keeps_selection(
            width, lambda index, lines: lines[index][2] == "row"
        )
        for index in wraps:
            state = panel.help_start(width)
            panel.help_mouse_pick(state, index)
            self.assertEqual(state["index"], start["rows"][0])

    def test_selection_never_rests_on_a_heading_or_rule(self):
        start = panel.help_start()
        rows = set(start["rows"])
        lines, _selected = panel.help_lines(start)
        unselectable = [
            i for i, (_text, _attr, kind) in enumerate(lines) if kind != "row"
        ]
        self.assertTrue(unselectable)  # the menu has headings and rules
        self.assert_scroll_keeps_selection(
            None, lambda index, lines: lines[index][2] == "row"
        )
        for index in unselectable:
            state = panel.help_start()
            panel.help_mouse_pick(state, index)
            self.assertEqual(state["index"], start["index"])


class HelpDispatchTests(unittest.TestCase):
    def test_question_mark_opens_the_menu(self):
        app = panel.main.App(mock.Mock(), "%1")
        with mock.patch.object(panel.help, "help_box") as box:
            self.assertTrue(app.dispatch_key(ord("?"), {}))
            box.assert_called_once_with(app.stdscr)


class DispatchCoverageTests(unittest.TestCase):
    """Every advertised key is handled, and every dispatch has a keymap row."""

    EXEMPT = {"Tmux", "Mouse"}
    OWNERS = {
        "New box": ("boxes", "new_box_key"),
        "Move box": ("boxes", "move_box_key"),
        "Trash box": ("boxes", "trash_box_key"),
        "Trash entry": ("boxes", "trash_box_key"),
        "Keys menu": ("help", "help_box_key"),
    }
    TOKENS = {
        "↑": ("curses.KEY_UP",),
        "↓": ("curses.KEY_DOWN",),
        "j": ('ord("j")',),
        "k": ('ord("k")',),
        "g": ('ord("g")',),
        "G": ('ord("G")',),
        "Home": ("curses.KEY_HOME",),
        "End": ("curses.KEY_END",),
        "[": ('ord("[")',),
        "]": ('ord("]")',),
        "h": ('ord("h")',),
        "l": ('ord("l")',),
        "←": ("curses.KEY_LEFT",),
        "→": ("curses.KEY_RIGHT",),
        "/": ('ord("/")',),
        ":": ('ord(":")',),
        "r": ('ord("r")',),
        "q": ('ord("q")',),
        "?": ('ord("?")',),
        "⏎": ("curses.KEY_ENTER",),
        "n": ('ord("n")',),
        "N": ("core.NEW_NO_PICKER_KEY",),
        "R": ('ord("R")',),
        "m": ('ord("m")',),
        "x": ('ord("x")',),
        "D": ('ord("D")',),
        "t": ('ord("t")',),
        "d": ('ord("d")',),
        "y": ('ord("y")',),
        "Tab": ("== 9",),
        "Esc": ("27",),
        "1–9": ('ord("1") <= ch <= ord("9")',),
        "Ctrl-C": ("KeyboardInterrupt", "3"),
    }

    def owner_source(self, row):
        owner = self.OWNERS.get(row.scope)
        if owner is None:
            return inspect.getsource(panel.main.App.dispatch_key)
        module, name = owner
        return inspect.getsource(getattr(getattr(panel, module), name))

    def test_every_advertised_token_is_handled_by_its_owner(self):
        for row in panel.keymap.ROWS:
            if row.scope in self.EXEMPT:
                continue
            tokens = re.split(r"\s+", f"{row.keys} {row.also}".strip())
            self.assertTrue(tokens, f"empty row {row.scope}")
            source = self.owner_source(row)
            for token in tokens:
                handlers = self.TOKENS.get(token)
                if handlers is None:
                    self.fail(
                        f"no checked handler for {token!r} in row "
                        f"{row.scope} {row.keys}"
                    )
                with self.subTest(scope=row.scope, keys=row.keys, token=token):
                    self.assertTrue(
                        any(handler in source for handler in handlers),
                        f"{row.scope} {row.keys} advertises {token!r}, but "
                        f"{self.OWNERS.get(row.scope, 'panel.main')} never "
                        "handles it",
                    )

    def test_dispatched_keys_have_a_keymap_row_or_alias(self):
        source = inspect.getsource(panel.main.App.dispatch_key)
        advertised = set()
        for row in panel.keymap.ROWS:
            advertised.update(
                re.split(r"\s+", f"{row.keys} {row.also}".strip())
            )
        key_tokens = {
            "KEY_UP": "↑",
            "KEY_DOWN": "↓",
            "KEY_HOME": "Home",
            "KEY_END": "End",
            "KEY_LEFT": "←",
            "KEY_RIGHT": "→",
            "KEY_ENTER": "⏎",
        }
        dispatched = set()
        for line in source.splitlines():
            match = re.match(
                r"\s*(?:el)?if ch\s+(?:in|==)\s+(.*):\s*$", line
            )
            if not match:
                continue
            condition = match.group(1)
            dispatched.update(re.findall(r'ord\("(.)"\)', condition))
            for name in re.findall(r"curses\.(KEY_[A-Z]+)", condition):
                token = key_tokens.get(name)
                if token is not None:
                    dispatched.add(token)
            if "core.NEW_NO_PICKER_KEY" in condition:
                dispatched.add("N")
        self.assertTrue(dispatched)
        for token in sorted(dispatched):
            with self.subTest(token=token):
                self.assertIn(token, advertised)


if __name__ == "__main__":
    unittest.main(verbosity=2)
