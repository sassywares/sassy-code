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
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location("sassy_code_panel", ROOT / "panel.py")
panel = importlib.util.module_from_spec(SPEC)
sys.modules["sassy_code_panel"] = panel
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


class ClaudeParseTests(unittest.TestCase):
    SID = "11111111-2222-3333-4444-555555555555"

    def write(self, tmp: str, lines: list[dict]) -> str:
        path = Path(tmp) / f"{self.SID}.jsonl"
        path.write_text("".join(json.dumps(o) + "\n" for o in lines))
        return str(path)

    def entry(self, kind: str, ts: str, **extra) -> dict:
        base = {
            "type": kind,
            "sessionId": self.SID,
            "cwd": "/proj",
            "timestamp": ts,
        }
        base.update(extra)
        return base

    def test_claude_transcript_identity_preview_and_completion(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write(tmp, [
                self.entry(
                    "user", "2026-10-23T04:26:42.592Z",
                    origin={"kind": "human"},
                    message={"role": "user", "content": "fix the login bug"},
                ),
                self.entry(
                    "assistant", "2026-10-23T04:26:43.592Z",
                    message={
                        "role": "assistant", "stop_reason": "end_turn",
                        "content": [{"type": "text", "text": "done"}],
                    },
                ),
            ])
            s = panel.parse_session(path, "claude")
            self.assertIsNotNone(s)
            self.assertEqual(s.harness, "claude")
            self.assertEqual(s.id, self.SID)
            self.assertEqual(s.cwd, "/proj")
            self.assertEqual(s.preview, "fix the login bug")
            self.assertTrue(s.is_complete())
            self.assertEqual(
                s.created,
                datetime(
                    2026, 10, 23, 4, 26, 42, 592000, tzinfo=timezone.utc
                ).timestamp(),
            )

    def test_tool_use_and_tool_result_keep_the_turn_pending(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write(tmp, [
                self.entry(
                    "user", "2026-10-23T04:26:42.592Z",
                    origin={"kind": "human"},
                    message={"role": "user", "content": "run tests"},
                ),
                self.entry(
                    "assistant", "2026-10-23T04:26:43.592Z",
                    message={
                        "role": "assistant", "stop_reason": "tool_use",
                        "content": [{"type": "tool_use", "name": "Bash"}],
                    },
                ),
                self.entry(
                    "user", "2026-10-23T04:26:44.592Z",
                    message={
                        "role": "user", "content": [
                            {"type": "tool_result", "content": "ok"}
                        ],
                    },
                ),
            ])
            s = panel.parse_session(path, "claude")
            self.assertIsNotNone(s)
            self.assertEqual(s.last_role, "toolResult")
            self.assertFalse(s.is_complete())

    def test_api_error_is_an_error_and_sidechains_are_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write(tmp, [
                self.entry(
                    "user", "2026-10-23T04:26:42.592Z",
                    origin={"kind": "human"},
                    message={"role": "user", "content": "hello"},
                ),
                self.entry(
                    "assistant", "2026-10-23T04:26:43.592Z",
                    isApiErrorMessage=True, error="billing_error",
                    message={
                        "role": "assistant", "stop_reason": "stop_sequence",
                        "content": [{"type": "text", "text": "no access"}],
                    },
                ),
                # A subagent's newest turn must not change the main status.
                self.entry(
                    "assistant", "2026-10-23T04:26:44.592Z",
                    isSidechain=True, sessionId="99999999-8888-7777-6666-555555555555",
                    cwd="/subagent",
                    message={
                        "role": "assistant", "stop_reason": "tool_use",
                        "content": [{"type": "tool_use"}],
                    },
                ),
            ])
            s = panel.parse_session(path, "claude")
            self.assertIsNotNone(s)
            self.assertEqual(s.id, self.SID)
            self.assertEqual(s.cwd, "/proj")
            self.assertEqual(s.status, "error")
            self.assertTrue(s.is_complete())

    def test_meta_and_local_command_entries_are_not_previews(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write(tmp, [
                self.entry(
                    "user", "2026-10-23T04:26:42.592Z", isMeta=True,
                    message={"role": "user", "content": "system reminder"},
                ),
                self.entry(
                    "user", "2026-10-23T04:26:43.592Z",
                    origin={"kind": "human"},
                    message={"role": "user", "content": "real question"},
                ),
            ])
            s = panel.parse_session(path, "claude")
            self.assertIsNotNone(s)
            self.assertEqual(s.preview, "real question")


class CommandTests(unittest.TestCase):
    def test_harness_is_detected_for_native_and_npm_claude(self):
        self.assertEqual(
            panel.command_harness("claude --resume abc"), "claude"
        )
        self.assertEqual(
            panel.command_harness(
                "node /opt/homebrew/lib/node_modules/@anthropic-ai/"
                "claude-code/cli.js --resume abc"
            ),
            "claude",
        )
        self.assertEqual(panel.command_harness("pi --session abc"), "pi")

    def test_a_shell_wrapper_mentioning_claude_is_not_the_agent(self):
        command = "sh -c 'claude --resume abc; exec zsh'"
        self.assertIsNone(panel.command_harness(command))
        self.assertIsNone(
            panel.command_harness("grep claude-code /tmp/log")
        )
        self.assertIsNone(
            panel.command_harness("echo @anthropic-ai/claude-code")
        )

    def test_session_arguments_are_harness_specific(self):
        self.assertEqual(
            panel.session_id_from_command("pi --session abc", "pi"), "abc"
        )
        self.assertEqual(
            panel.session_id_from_command(
                "claude --resume abc", "claude"
            ),
            "abc",
        )
        self.assertEqual(
            panel.session_id_from_command(
                "claude -r abc", "claude"
            ),
            "abc",
        )
        self.assertEqual(
            panel.session_id_from_command(
                "claude --session-id=abc", "claude"
            ),
            "abc",
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


class CacheTests(unittest.TestCase):
    def test_unchanged_file_still_ages_from_active_to_idle(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = datetime(
                2026, 10, 23, 4, 26, 42, tzinfo=timezone.utc
            ).timestamp()
            path = Path(tmp) / "s.jsonl"
            path.write_text(
                json.dumps({
                    "type": "session",
                    "id": "cached01",
                    "cwd": "/proj",
                    "timestamp": "2026-10-23T04:26:42.000Z",
                }) + "\n" + json.dumps({
                    "type": "message",
                    "timestamp": "2026-10-23T04:26:42.000Z",
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "text", "text": "done"}],
                        "stopReason": "stop",
                    },
                }) + "\n"
            )
            with mock.patch.object(panel.time, "time", return_value=base + 10):
                first = panel.read_session(str(path), "pi")
            with mock.patch.object(panel.time, "time", return_value=base + 1000):
                second = panel.read_session(str(path), "pi")
            self.assertEqual(first.status, "active")
            self.assertEqual(second.status, "idle")
