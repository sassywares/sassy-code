#!/usr/bin/env python3
"""Five session statuses, needs-input attention and the sessions.json store."""
from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from tests.panel_loader import load_panel

panel = load_panel()


def session(
    sid: str = "s",
    ts: float = 100.0,
    *,
    messages: int = 1,
    role: str | None = "assistant",
    stop: str | None = "stop",
    error: bool = False,
) -> panel.Session:
    s = panel.Session(
        path=f"/tmp/{sid}.jsonl",
        id=sid,
        cwd="/p",
        created=ts,
        last_ts=ts,
        messages=messages,
        last_role=role,
        last_stop_reason=stop,
    )
    s.last_error = error
    return s


def write_pi(path: Path, sid: str, *, role: str, stop: str | None,
             cwd: str = "/p", ts: str = "2026-10-23T04:26:42.000Z") -> None:
    lines = [
        {"type": "session", "id": sid, "cwd": cwd, "timestamp": ts},
        {"type": "message", "timestamp": ts,
         "message": {"role": role, "content": [
             {"type": "text", "text": "hello"}]}},
    ]
    if role == "assistant":
        lines[-1]["message"]["stopReason"] = stop
    path.write_text("".join(json.dumps(o) + "\n" for o in lines))


class StatusTests(unittest.TestCase):
    def setUp(self):
        panel.core._ATTENTION.clear()
        self.addCleanup(panel.core._ATTENTION.clear)

    def test_terminal_file_without_a_process_is_idle(self):
        s = session()
        self.assertFalse(panel.pending_turn(s))
        self.assertEqual(panel._infer_status(s), "idle")
        self.assertEqual(panel.session_status(s, False, False, False), "idle")

    def test_live_terminal_turn_needs_input(self):
        s = session()
        self.assertEqual(
            panel.session_status(s, True, False, False), "needs_input"
        )

    def test_live_fresh_session_with_no_messages_needs_input(self):
        s = session(messages=0, role=None, stop=None)
        self.assertFalse(panel.pending_turn(s))
        self.assertEqual(
            panel.session_status(s, True, False, False), "needs_input"
        )

    def test_stale_tool_result_is_idle_not_yellow(self):
        # The old inference kept toolResult "active" forever; without a live
        # process it must be idle no matter how old the result is.
        s = session(ts=time.time() - 86400, role="toolResult", stop=None)
        self.assertTrue(panel.pending_turn(s))
        self.assertEqual(
            panel.session_status(s, False, False, False), "idle"
        )

    def test_presenting_clears_attention_without_backfill(self):
        # A6: presenting clears the mark, and the same file read must not
        # recreate it -- that would leave the row sticky yellow.
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "s.jsonl"
            write_pi(path, "s", role="assistant", stop="stop")
            s = panel.read_session(str(path), "pi")
            self.assertIsNotNone(s)
            panel.mark_attention("s")
            pane = panel.PaneInfo(
                id="%2", window_id="@1", session_name="viewer",
                pid=1, tag="s",
            )
            groups = [panel.Group(cwd="/p", sessions=[s])]
            with mock.patch.object(panel.panes, "visible_layout",
                                   return_value=("%1", [pane], "%2")), \
                    mock.patch.object(panel.procs, "process_table",
                                      return_value=[]), \
                    mock.patch.object(panel.panes, "pane_has_agent",
                                      return_value=True), \
                    mock.patch.object(panel.tmuxctl, "set_pane_tag"), \
                    mock.patch.object(panel.tmuxctl, "focus"), \
                    mock.patch.object(panel.state, "_write_state"):
                self.assertIsNone(panel.present_session(s, groups))
            self.assertFalse(panel.attention_active("s"))
            with mock.patch.object(panel.status, "session_files",
                                   return_value=[(str(path), "pi")]), \
                    mock.patch.object(panel.procs, "process_table",
                                      return_value=[]), \
                    mock.patch.object(panel.tmuxctl, "managed_panes",
                                      return_value=[]), \
                    mock.patch.object(panel.procs, "running_session_processes",
                                      return_value={}), \
                    mock.patch.object(panel.state, "STATE_DIR",
                                      str(Path(tmp) / "state")), \
                    mock.patch.object(panel.state, "_sessions_cache", None), \
                    mock.patch.object(panel.state, "_goals_cache", None):
                groups = panel.load_groups()
            got = panel.session_index(groups)["s"]
            self.assertFalse(panel.attention_active("s"))
            self.assertEqual(got.status, "idle")

    def test_agentless_tagged_pane_is_not_live(self):
        # A pane whose agent exited leaves a plain shell still carrying
        # @sassy_session. A tag alone must not keep the session in progress.
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "s.jsonl"
            write_pi(path, "s", role="assistant", stop="stop")
            pane = panel.PaneInfo(
                id="%2", window_id="@1", session_name="viewer",
                pid=100, tag="s",
            )
            shell = [panel.Proc(pid=100, ppid=1, command="zsh")]
            agent = shell + [
                panel.Proc(pid=101, ppid=100, command="pi --session s")
            ]

            def status_for(procs):
                with mock.patch.object(
                        panel.status, "session_files",
                        return_value=[(str(path), "pi")]), \
                        mock.patch.object(
                            panel.procs, "process_table", return_value=procs), \
                        mock.patch.object(
                            panel.tmuxctl, "managed_panes", return_value=[pane]), \
                        mock.patch.object(
                            panel.state, "STATE_DIR", str(Path(tmp) / "state")), \
                        mock.patch.object(panel.state, "_sessions_cache", None), \
                        mock.patch.object(panel.state, "_goals_cache", None):
                    return panel.session_index(panel.load_groups())["s"].status

            self.assertEqual(status_for(shell), "idle")
            self.assertEqual(status_for(agent), "needs_input")

    def test_empty_process_snapshot_is_unknown_not_dead(self):
        # process_table() returns [] on any ps failure or timeout. That must not
        # declare every live session dead or reap its pane.
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "s.jsonl"
            write_pi(path, "s", role="user", stop=None)
            pane = panel.PaneInfo(
                id="%2", window_id="@1", session_name="viewer",
                pid=100, tag="s",
            )
            with mock.patch.object(panel.status, "session_files",
                                   return_value=[(str(path), "pi")]), \
                    mock.patch.object(panel.procs, "process_table", return_value=[]), \
                    mock.patch.object(panel.tmuxctl, "managed_panes",
                                      return_value=[pane]), \
                    mock.patch.object(panel.state, "STATE_DIR",
                                      str(Path(tmp) / "state")), \
                    mock.patch.object(panel.state, "_sessions_cache", None), \
                    mock.patch.object(panel.state, "_goals_cache", None):
                groups = panel.load_groups()
            self.assertEqual(panel.session_index(groups)["s"].status,
                             "in_progress")

            hidden = panel.PaneInfo(
                id="%3", window_id="@1", session_name=panel.BG_SESSION,
                pid=100, tag="s",
            )
            with mock.patch.object(panel.tmuxctl, "kill_pane") as kill:
                panel.reap_hidden([hidden], groups, [], {})
            kill.assert_not_called()

    def test_attention_keeps_a_dead_session_waiting(self):
        s = session()
        panel.mark_attention(s.id)
        self.assertEqual(
            panel.session_status(s, False, False, True), "needs_input"
        )
        panel.clear_attention(s.id)
        self.assertFalse(panel.attention_active(s.id))
        self.assertEqual(
            panel.session_status(s, False, False, False), "idle"
        )

    def test_precedence_error_done_progress_needs_input_idle(self):
        pending = session(role="user", stop=None)
        failed = session(role="user", stop=None, error=True)
        cases = [
            (pending, False, True, True, "done"),
            (pending, True, False, False, "in_progress"),
            (pending, True, True, False, "done"),
            (pending, True, True, True, "done"),
            (failed, True, True, True, "error"),
        ]
        for s, live, done, attention, expected in cases:
            with self.subTest(live=live, done=done, attention=attention,
                              error=s.last_error):
                self.assertEqual(
                    panel.session_status(s, live, done, attention), expected
                )
        dead = session()
        self.assertEqual(
            panel.session_status(dead, False, False, True), "needs_input"
        )
        self.assertEqual(
            panel.session_status(dead, False, False, False), "idle"
        )

    def test_reap_marks_a_completed_hidden_session(self):
        s = session()
        g = panel.Group(cwd="/p", sessions=[s])
        pane = panel.PaneInfo(
            id="%1", window_id="@1", session_name=panel.BG_SESSION,
            pid=123, tag=s.id,
        )
        with mock.patch.object(panel.panes, "pane_has_agent", return_value=True), \
                mock.patch.object(panel.tmuxctl, "kill_pane"):
            panel.reap_hidden([pane], [g], [], {})
        self.assertTrue(panel.attention_active(s.id))
        self.assertEqual(s.status, "needs_input")

    def test_new_turn_clears_attention_in_load_groups(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "s.jsonl"
            write_pi(path, "s", role="user", stop=None)
            panel.mark_attention("s")
            with mock.patch.object(panel.status, "session_files",
                                   return_value=[(str(path), "pi")]), \
                    mock.patch.object(panel.procs, "process_table", return_value=[]), \
                    mock.patch.object(panel.tmuxctl, "managed_panes", return_value=[]), \
                    mock.patch.object(panel.procs, "running_session_processes",
                                      return_value={}), \
                    mock.patch.object(panel.state, "STATE_DIR",
                                      str(Path(tmp) / "state")), \
                    mock.patch.object(panel.state, "_sessions_cache", None), \
                    mock.patch.object(panel.state, "_goals_cache", None):
                groups = panel.load_groups()
            s = panel.session_index(groups)["s"]
            self.assertFalse(panel.attention_active("s"))
            self.assertEqual(s.status, "idle")


class AskUserQuestionTests(unittest.TestCase):
    SID = "11111111-2222-3333-4444-555555555555"

    def parse(self, lines):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / f"{self.SID}.jsonl"
            path.write_text("".join(json.dumps(o) + "\n" for o in lines))
            return panel.parse_session(str(path), "claude")

    def entry(self, kind, n, **extra):
        data = {
            "type": kind, "sessionId": self.SID, "cwd": "/p",
            "timestamp": f"2026-10-23T04:26:{n:02d}.000Z",
        }
        data.update(extra)
        return data

    def question(self, n=2):
        return self.entry("assistant", n, message={
            "role": "assistant", "stop_reason": "tool_use",
            "content": [{
                "type": "tool_use", "id": "q1",
                "name": "AskUserQuestion",
                "input": {"questions": []},
            }],
        })

    def test_question_without_a_result_needs_input(self):
        s = self.parse([
            self.entry("user", 1, message={
                "role": "user", "content": "pick one"}),
            self.question(),
        ])
        self.assertTrue(s.awaiting_input)
        self.assertEqual(
            panel.session_status(s, True, False, False), "needs_input"
        )

    def test_answered_question_returns_to_progress(self):
        s = self.parse([
            self.entry("user", 1, message={
                "role": "user", "content": "pick one"}),
            self.question(),
            self.entry("user", 3, message={
                "role": "user", "content": [{
                    "type": "tool_result", "tool_use_id": "q1",
                    "content": "A",
                }],
            }),
        ])
        self.assertFalse(s.awaiting_input)
        self.assertEqual(
            panel.session_status(s, True, False, False), "in_progress"
        )


class SessionStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.tmp = Path(self.tmpdir.name)
        for name, value in (
            ("STATE_DIR", str(self.tmp / "state")),
            ("_sessions_cache", None),
        ):
            patcher = mock.patch.object(panel.state, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.file = Path(panel.state.STATE_DIR) / panel.SESSIONS_STATE

    def test_toggle_round_trip_keyed_by_session_id(self):
        a = session("a", 100.0)
        b = session("b", 200.0)
        self.assertTrue(panel.toggle_session_done(a))
        self.assertTrue(panel.toggle_session_done(b))
        data = json.loads(self.file.read_text())
        self.assertEqual(data["version"], 1)
        self.assertEqual(set(data["sessions"]), {"a", "b"})
        self.assertTrue(data["sessions"]["a"]["done"])
        records, bad = panel.load_sessions()
        self.assertFalse(bad)
        self.assertTrue(panel.session_done(a, records))
        self.assertTrue(panel.session_done(b, records))
        self.assertFalse(panel.toggle_session_done(a))
        self.assertNotIn("a", json.loads(self.file.read_text())["sessions"])

    def test_new_activity_revives_but_x_marks_it_again(self):
        s = session("a", 100.0)
        self.assertTrue(panel.toggle_session_done(s))
        records, _ = panel.load_sessions()
        self.assertTrue(panel.session_done(s, records))
        s.last_ts = records["a"]["done_at"] + 0.000001
        self.assertFalse(panel.session_done(s, records))
        self.assertTrue(panel.toggle_session_done(s))
        self.assertTrue(panel.session_done(s, panel.load_sessions()[0]))

    def test_corrupt_file_is_never_overwritten_and_refuses_edits(self):
        self.file.parent.mkdir(parents=True)
        self.file.write_text("{")
        records, bad = panel.load_sessions()
        self.assertEqual((records, bad), ({}, True))
        with self.assertRaises(panel.SessionError):
            panel.toggle_session_done(session())
        self.assertEqual(self.file.read_text(), "{")
        self.assertIn("unreadable", panel.state.sessions_guard())

    def test_x_on_a_session_toggles_the_mark_and_the_footer_label(self):
        s = session("a", 100.0)
        g = panel.Group(cwd="/p", sessions=[s])
        row = ("session", g, s)
        self.assertIn("x done", panel.footer_items(row))
        msg, key = panel.done_session(row)
        self.assertIn("done:", msg)
        self.assertEqual(key, ("session", "a", "/p"))
        self.assertTrue(panel.session_done(s, panel.load_sessions()[0]))
        s.status = "done"  # what the caller's load_groups recomputes
        self.assertIn("x undo", panel.footer_items(row))
        msg, _ = panel.done_session(row)
        self.assertIn("reopened", msg)
        self.assertFalse(panel.session_done(s, panel.load_sessions()[0]))
        s.status = "idle"
        self.assertIn("x done", panel.footer_items(row))

    def test_load_groups_applies_done_and_lists_the_new_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "s.jsonl"
            write_pi(path, "s", role="assistant", stop="stop")
            s = panel.read_session(str(path), "pi")
            self.assertTrue(panel.toggle_session_done(s))
            with mock.patch.object(panel.status, "session_files",
                                   return_value=[(str(path), "pi")]), \
                    mock.patch.object(panel.procs, "process_table", return_value=[]), \
                    mock.patch.object(panel.tmuxctl, "managed_panes", return_value=[]), \
                    mock.patch.object(panel.procs, "running_session_processes",
                                      return_value={}), \
                    mock.patch.object(panel.state, "_goals_cache", None):
                groups = panel.load_groups()
            got = panel.session_index(groups)["s"]
            self.assertEqual(got.status, "done")
            self.assertTrue(got.done)
            text = "\n".join(panel.main.list_lines(groups))
            self.assertIn("[done", text)
            self.assertIn("✓", text)

    def test_wrong_shape_counts_as_corrupt(self):
        self.file.parent.mkdir(parents=True)
        self.file.write_text('{"version": 2, "sessions": {}}')
        self.assertTrue(panel.load_sessions()[1])
        with self.assertRaises(panel.SessionError):
            panel.toggle_session_done(session())

    def test_done_session_dims_and_sinks_with_done_goals(self):
        old = session("old", 100.0)
        fresh = session("fresh", 300.0)
        done = session("done", 200.0)
        done.status = "done"
        goal = panel.Goal(id="g", name="G", created=50.0, done=True)
        rows = panel.recency_top(
            [old, fresh, done], [(goal, [], 250.0)]
        )
        self.assertEqual(
            [r[2].id for r in rows],
            ["fresh", "old", "g", "done"],
        )
        lines, _, _ = panel.build_lines(
            list(panel.rows_for([panel.Group(
                cwd="/p", sessions=[done],
                goals=[panel.Goal(id="g", name="G", created=50.0,
                                  done=True)]),
            ], "")), 40, set(), ""
        )
        text = "\n".join(t for t, _, _ in lines)
        self.assertIn("✓", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
