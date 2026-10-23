#!/usr/bin/env python3
"""Session parsing tests: UTC timestamps and in-process session switches."""
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location("sassy_panel", ROOT / "panel.py")
panel = importlib.util.module_from_spec(SPEC)
sys.modules["sassy_panel"] = panel
SPEC.loader.exec_module(panel)


def session(
    sid: str,
    ts: float,
    parent: str = "",
    created: float | None = None,
    last_role: str | None = None,
    stop_reason: str | None = None,
) -> panel.Session:
    s = panel.Session(
        path=f"/tmp/{sid}.jsonl",
        id=sid,
        cwd="/proj",
        created=ts if created is None else created,
        last_ts=ts,
        parent_path=parent,
    )
    s.last_role = last_role
    s.last_stop_reason = stop_reason
    return s


class TimestampTests(unittest.TestCase):
    def test_utc_timestamp_keeps_its_instant(self):
        expected = datetime(
            2026, 10, 23, 4, 26, 42, 592000, tzinfo=timezone.utc
        ).timestamp()
        self.assertAlmostEqual(
            panel.iso_to_epoch("2026-10-23T04:26:42.592Z"), expected, places=3
        )

    def test_naive_timestamp_is_read_as_utc(self):
        expected = datetime(
            2026, 10, 23, 4, 26, 42, 592000, tzinfo=timezone.utc
        ).timestamp()
        self.assertAlmostEqual(
            panel.iso_to_epoch("2026-10-23T04:26:42.592"), expected, places=3
        )

    def test_just_created_session_reads_as_now(self):
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        self.assertEqual(panel.rel_time(panel.iso_to_epoch(stamp)), "now")

    def test_empty_timestamp_is_unknown(self):
        self.assertEqual(panel.iso_to_epoch(""), 0.0)
        self.assertEqual(panel.rel_time(0.0), "?")


class ParseTests(unittest.TestCase):
    def test_parent_session_and_utc_activity_are_parsed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "child.jsonl"
            child_ts = "2026-10-23T04:26:42.592Z"
            path.write_text(
                json.dumps({
                    "type": "session",
                    "id": "child",
                    "cwd": "/proj",
                    "timestamp": child_ts,
                    "parentSession": "/tmp/parent.jsonl",
                }) + "\n"
                + json.dumps({
                    "type": "message",
                    "timestamp": child_ts,
                    "message": {
                        "role": "user",
                        "content": [{"type": "text", "text": "continue"}],
                    },
                }) + "\n"
            )
            s = panel.parse_session(str(path))
            self.assertIsNotNone(s)
            self.assertEqual(s.parent_path, "/tmp/parent.jsonl")
            self.assertEqual(
                s.last_ts,
                datetime(
                    2026, 10, 23, 4, 26, 42, 592000, tzinfo=timezone.utc
                ).timestamp(),
            )


class SwitchTests(unittest.TestCase):
    def test_handoff_successor_is_followed(self):
        old = session("old", 100.0)
        new = session("new", 200.0, parent=old.path)
        children = panel.session_children({"old": old, "new": new})
        self.assertIs(panel.switched_session(old, children), new)

    def test_chain_of_switches_follows_to_the_newest(self):
        a = session("a", 100.0)
        b = session("b", 200.0, parent=a.path)
        c = session("c", 300.0, parent=b.path)
        children = panel.session_children({"a": a, "b": b, "c": c})
        self.assertIs(panel.switched_session(a, children), c)

    def test_successor_without_new_activity_is_not_followed(self):
        old = session("old", 200.0)
        fork = session("fork", 100.0, parent=old.path)
        children = panel.session_children({"old": old, "fork": fork})
        self.assertIs(panel.switched_session(old, children), old)

    def test_fork_with_copied_history_is_followed(self):
        # `/fork` copies entries with their original timestamps; the fresh
        # header timestamp is what proves the switch happened in the pane.
        old = session("old", 200.0)
        fork = session(
            "fork", 100.0, parent=old.path, created=300.0,
            last_role="user",
        )
        children = panel.session_children({"old": old, "fork": fork})
        self.assertIs(panel.switched_session(old, children), fork)

    def test_completed_stale_branch_is_not_followed(self):
        old = session("old", 200.0)
        branch = session(
            "branch", 100.0, parent=old.path, created=300.0,
            last_role="assistant", stop_reason="stop",
        )
        children = panel.session_children({"old": old, "branch": branch})
        self.assertIs(panel.switched_session(old, children), old)

    def test_successor_held_by_another_pane_is_not_stolen(self):
        old = session("old", 100.0)
        new = session("new", 200.0, parent=old.path)
        children = panel.session_children({"old": old, "new": new})
        self.assertIs(panel.switched_session(old, children, {"new"}), old)

    def test_unrelated_sessions_are_not_followed(self):
        a = session("a", 100.0)
        b = session("b", 200.0)
        # A stale parentSession path that is not in the store is ignored.
        c = session("c", 300.0, parent="/tmp/unknown.jsonl")
        children = panel.session_children({"a": a, "b": b, "c": c})
        self.assertIs(panel.switched_session(a, children), a)
        self.assertIs(panel.switched_session(c, children), c)


if __name__ == "__main__":
    unittest.main(verbosity=2)
