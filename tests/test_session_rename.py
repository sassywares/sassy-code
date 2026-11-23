#!/usr/bin/env python3
"""Session rename: one native title line, read back on the next parse (P3)."""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from tests.goal_case import GoalStoreCase, panel

CLAUDE_SID = "11111111-2222-3333-4444-555555555555"


def line(obj: dict) -> str:
    return json.dumps(obj) + "\n"


def pi_lines(sid: str = "pi-1", cwd: str = "/p") -> str:
    return line({
        "type": "session", "id": sid, "cwd": cwd,
        "timestamp": "2026-10-23T04:26:00.000Z",
    }) + line({
        "type": "message", "timestamp": "2026-10-23T04:26:01.000Z",
        "message": {"role": "user", "content": "start"},
    })


def claude_lines(cwd: str = "/p") -> str:
    return line({
        "type": "user", "sessionId": CLAUDE_SID, "cwd": cwd,
        "timestamp": "2026-10-23T04:26:00.000Z",
        "message": {"role": "user", "content": "start"},
    })


class SessionRenameCase(GoalStoreCase):
    def setUp(self):
        super().setUp()
        self.root = Path(self.tmpdir.name)
        panel.status._PARSE_CACHE.clear()
        self.addCleanup(panel.status._PARSE_CACHE.clear)

    def write(self, name: str, text: str) -> str:
        path = self.root / name
        path.write_text(text, encoding="utf-8")
        return str(path)

    def raw_lines(self, path: str) -> list[str]:
        return Path(path).read_text(encoding="utf-8").splitlines()

    def test_pi_writes_one_session_info_line_and_reads_it_back(self):
        path = self.write("pi.jsonl", pi_lines())
        title = panel.write_title(path, "pi", "  New   title ")
        self.assertEqual(title, "New title")
        entries = [json.loads(x) for x in self.raw_lines(path)]
        self.assertEqual(entries[-1], {"type": "session_info", "name": "New title"})
        self.assertEqual(sum(e.get("type") == "session_info" for e in entries), 1)
        self.assertEqual(panel.parse_session(path, "pi").name, "New title")

    def test_claude_writes_one_custom_title_line_and_reads_it_back(self):
        path = self.write("claude.jsonl", claude_lines())
        panel.write_title(path, "claude", "Claude name")
        entries = [json.loads(x) for x in self.raw_lines(path)]
        self.assertEqual(
            entries[-1], {"type": "custom-title", "customTitle": "Claude name"}
        )
        self.assertEqual(sum(e.get("type") == "custom-title" for e in entries), 1)
        self.assertEqual(panel.parse_session(path, "claude").name, "Claude name")

    def test_leading_newline_only_when_the_file_lacks_one(self):
        ended = self.write("ended.jsonl", pi_lines())
        panel.write_title(ended, "pi", "one")
        broken = self.write("broken.jsonl", pi_lines().rstrip("\n"))
        panel.write_title(broken, "pi", "two")
        self.assertEqual(Path(ended).read_bytes().count(b"session_info"), 1)
        self.assertNotIn(b"}\n\n{", Path(ended).read_bytes())
        self.assertIn(b'}\n{"type": "session_info"', Path(broken).read_bytes())
        self.assertNotIn(b"}\n\n{", Path(broken).read_bytes())
        self.assertEqual(len(self.raw_lines(broken)), 3)

    def test_live_mid_turn_session_rename_invalidates_the_parse_cache(self):
        path = self.write("live.jsonl", claude_lines())
        before = panel.read_session(path, "claude")
        self.assertTrue(panel.pending_turn(before))
        panel.write_title(path, "claude", "Mid turn")
        after = panel.read_session(path, "claude")
        self.assertEqual(after.name, "Mid turn")
        self.assertTrue(panel.pending_turn(after))

    def test_empty_and_whitespace_titles_refuse(self):
        path = self.write("pi.jsonl", pi_lines())
        before = Path(path).read_bytes()
        for title in ("", "   ", "\n\t "):
            with self.assertRaises(panel.TitleError):
                panel.write_title(path, "pi", title)
        self.assertEqual(Path(path).read_bytes(), before)

    def test_missing_or_unknown_refuses(self):
        with self.assertRaises(panel.TitleError):
            panel.write_title(str(self.root / "nope.jsonl"), "pi", "x")
        path = self.write("pi.jsonl", pi_lines())
        with self.assertRaises(panel.TitleError):
            panel.write_title(path, "other", "x")

    def test_command_renames_and_keeps_the_session_row(self):
        path = self.write("pi.jsonl", pi_lines())
        s = panel.parse_session(path, "pi")
        g = panel.Group("/p", [s])
        row = ("session", g, s)
        self.assertEqual(panel.rename_prefill(row), ("session:rename ", ""))
        msg, key = panel.apply_command(
            "session:rename  New   name ", row, [g], set()
        )
        self.assertIn("renamed session to 'New name'", msg)
        self.assertEqual(key, ("session", "pi-1", "/p"))
        self.assertEqual(panel.parse_session(path, "pi").name, "New name")
        self.assertFalse((self.root / "state" / "goals.json").exists())

    def test_prefill_stays_blank_with_a_real_title(self):
        path = self.write(
            "titled.jsonl",
            pi_lines() + line({"type": "session_info", "name": "A real title"}),
        )
        s = panel.parse_session(path, "pi")
        g = panel.Group("/p", [s])
        self.assertEqual(s.name, "A real title")
        self.assertEqual(
            panel.rename_prefill(("session", g, s)), ("session:rename ", "")
        )

    def test_command_refuses_empty_name_without_writing(self):
        path = self.write("pi.jsonl", pi_lines())
        s = panel.parse_session(path, "pi")
        g = panel.Group("/p", [s])
        before = Path(path).read_bytes()
        for typed in ("session:rename", "session:rename    "):
            msg, key = panel.apply_command(typed, ("session", g, s), [g], set())
            self.assertIn("needs a name", msg)
            self.assertIsNone(key)
        self.assertEqual(Path(path).read_bytes(), before)

    def test_command_needs_a_session_row(self):
        msg, key = panel.apply_command("session:rename x", None, [], set())
        self.assertIn("highlight a session", msg)
        self.assertIsNone(key)

    def test_goal_rename_prefill_is_blank_too(self):
        goal = panel.Goal(id="g1", name="Goal A", created=1.0)
        g = panel.Group("/p", [], [goal])
        self.assertEqual(
            panel.rename_prefill(("goal", g, goal)), ("goal:rename ", "")
        )
        self.assertEqual(
            panel.rename_prefill(("group", g, None)), ("", panel.NO_GOAL_ROW)
        )


if __name__ == "__main__":
    unittest.main()
