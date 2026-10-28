#!/usr/bin/env python3
"""Claude Code transcript tests: titles, launch cwd, background tasks, widths."""
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location("sassy_code_panel", ROOT / "panel.py")
panel = importlib.util.module_from_spec(SPEC)
sys.modules["sassy_code_panel"] = panel
SPEC.loader.exec_module(panel)

SID = "11111111-2222-3333-4444-555555555555"
NOTE = (
    "<task-notification>\n<task-id>{ids}</task-id>\n"
    "<status>stopped</status>\n<summary>done</summary>\n</task-notification>"
)


def entry(kind: str, n: int, **extra) -> dict:
    base = {
        "type": kind, "sessionId": SID, "cwd": "/proj",
        "timestamp": f"2026-10-23T04:26:{n:02d}.000Z",
    }
    base.update(extra)
    return base


def user(n: int, text, **extra) -> dict:
    return entry("user", n, message={"role": "user", "content": text}, **extra)


def assistant(n: int, stop: str = "end_turn") -> dict:
    return entry("assistant", n, message={
        "role": "assistant", "stop_reason": stop,
        "content": [{"type": "text", "text": "ok"}],
    })


def tool_result(n: int, text: str) -> dict:
    return entry("user", n, message={"role": "user", "content": [
        {"type": "tool_result", "content": [{"type": "text", "text": text}]}
    ]})


def tool_use(n: int, name: str, tid: str, **inp) -> dict:
    return entry("assistant", n, message={
        "role": "assistant", "stop_reason": "tool_use",
        "content": [{"type": "tool_use", "id": tid, "name": name, "input": inp}],
    })


def result_for(n: int, tid: str, text: str) -> dict:
    return entry("user", n, message={"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": tid,
         "content": [{"type": "text", "text": text}]}
    ]})


def launched(n: int, agent_id: str) -> list[dict]:
    """Agent tool_use plus its async launch result (two entries)."""
    tid = f"toolu_{agent_id}"
    return [
        tool_use(n, "Agent", tid),
        result_for(
            n,
            tid,
            "Async agent launched successfully. (internal metadata)\n"
            f"agentId: {agent_id} (internal ID)\nThe agent is working.",
        ),
    ]


class ClaudeTestCase(unittest.TestCase):
    def parse(self, lines: list[dict]) -> panel.Session:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / f"{SID}.jsonl"
            path.write_text("".join(json.dumps(o) + "\n" for o in lines))
            s = panel.parse_session(str(path), "claude")
        self.assertIsNotNone(s)
        return s


class TitleTests(ClaudeTestCase):
    def test_ai_title_beats_prompt_and_latest_wins(self):
        s = self.parse([
            user(1, "fix the login bug"),
            {"type": "ai-title", "aiTitle": "First", "sessionId": SID},
            {"type": "ai-title", "aiTitle": "Login  bug\nfix", "sessionId": SID},
        ])
        self.assertEqual(s.title, "Login bug fix")

    def test_custom_title_beats_ai_title(self):
        s = self.parse([
            user(1, "hello"),
            {"type": "custom-title", "customTitle": "My rename", "sessionId": SID},
            {"type": "ai-title", "aiTitle": "Generated", "sessionId": SID},
        ])
        self.assertEqual(s.title, "My rename")

    def test_task_notification_never_becomes_the_title(self):
        s = self.parse([
            user(1, NOTE.format(ids="abc")),
            user(2, "real prompt here"),
        ])
        self.assertEqual(s.title, "real prompt here")
        s = self.parse([user(1, NOTE.format(ids="abc"))])
        self.assertEqual(s.title, "(empty session)")

    def test_system_reminder_and_local_command_are_skipped(self):
        s = self.parse([
            user(1, "<system-reminder>ctx</system-reminder>"),
            user(2, "<local-command-stdout>out</local-command-stdout>"),
            user(3, "Caveat: injected"),
            user(4, "actual ask"),
        ])
        self.assertEqual(s.title, "actual ask")

    def test_embedded_system_reminder_is_stripped(self):
        s = self.parse([user(
            1, "<system-reminder>ctx</system-reminder>\nplease refactor"
        )])
        self.assertEqual(s.title, "please refactor")

    def test_pasted_content_keeps_inner_text_without_tags(self):
        s = self.parse([user(
            1, '<pasted_content id="1">stack trace line</pasted_content> why?'
        )])
        self.assertEqual(s.title, "stack trace line why?")

    def test_bare_slash_command_yields_no_title_until_next_prompt(self):
        cmd = "<command-name>/model</command-name>\n<command-args></command-args>"
        s = self.parse([user(1, cmd)])
        self.assertEqual(s.title, "(empty session)")
        s = self.parse([user(1, cmd), user(2, "real ask")])
        self.assertEqual(s.title, "real ask")

    def test_slash_command_uses_args_text(self):
        s = self.parse([user(
            1,
            "<command-message>sassy-plan</command-message>\n"
            "<command-name>/sassy-plan</command-name>\n"
            "<command-args>We need to\nredo it</command-args>",
        )])
        self.assertEqual(s.title, "We need to redo it")

    def test_long_prompt_title_is_not_capped_by_parser(self):
        s = self.parse([user(1, "word " * 30)])
        self.assertEqual(s.title, ("word " * 30).strip())
        self.assertGreater(panel.display_width(s.title), 40)


class LocalCommandTests(ClaudeTestCase):
    def test_model_command_after_end_turn_stays_complete(self):
        s = self.parse([
            user(1, "hi"), assistant(2),
            user(3, "Caveat: local command", isMeta=True),
            user(4, "<command-name>/model</command-name>\n"
                 "<command-message>model</command-message>\n<command-args></command-args>"),
            user(5, "<local-command-stdout>Set model</local-command-stdout>"),
        ])
        self.assertTrue(s.is_complete())
        self.assertEqual(s.last_role, "assistant")
        self.assertNotEqual(s.status, "active")


class CwdTests(ClaudeTestCase):
    def test_cwd_is_launch_cwd(self):
        a = user(1, "go", cwd="/proj/knit")
        b = assistant(2)
        b["cwd"] = "/proj/knit/fe"
        c = user(3, "more", cwd="/proj/knit/fe")
        side = user(0, "x", cwd="/elsewhere", isSidechain=True)
        s = self.parse([side, a, b, c])
        self.assertEqual(s.cwd, "/proj/knit")


class BackgroundTaskTests(ClaudeTestCase):
    def test_open_tasks_block_completion_but_status_stays_idle(self):
        s = self.parse([
            user(1, "run scouts"), *launched(2, "aaa111"), *launched(3, "bbb222"),
            assistant(4),
        ])
        self.assertEqual(s.open_tasks, {"aaa111", "bbb222"})
        self.assertFalse(s.is_complete())
        self.assertEqual(s.status, "idle")  # not running: tasks alone are not active

    def test_one_notification_with_several_ids_closes_them(self):
        s = self.parse([
            user(1, "run scouts"), *launched(2, "aaa111"), *launched(3, "bbb222"),
            assistant(4),
            user(5, NOTE.format(ids="aaa111</task-id>\n<task-id>bbb222")),
        ])
        self.assertEqual(s.open_tasks, frozenset())

    def test_partial_close_and_notification_counts_as_user_turn(self):
        s = self.parse([
            user(1, "go"), *launched(2, "aaa111"), *launched(3, "bbb222"),
            assistant(4), user(5, NOTE.format(ids="aaa111")),
        ])
        self.assertEqual(s.open_tasks, {"bbb222"})
        self.assertEqual(s.last_role, "user")
        self.assertFalse(s.is_complete())

    def test_closed_tasks_restore_normal_completion(self):
        s = self.parse([
            user(1, "go"), *launched(2, "aaa111"), assistant(3),
            user(4, NOTE.format(ids="aaa111")), assistant(5),
        ])
        self.assertEqual(s.open_tasks, frozenset())
        self.assertTrue(s.is_complete())

    def test_queued_notification_closes_task(self):
        s = self.parse([
            user(1, "go"), *launched(2, "aaa111"), assistant(3),
            {"type": "queue-operation", "operation": "enqueue",
             "content": NOTE.format(ids="aaa111")},
            assistant(5),
        ])
        self.assertEqual(s.open_tasks, frozenset())

    def test_background_bash_announcement(self):
        s = self.parse([
            user(1, "go"),
            tool_use(2, "Bash", "tb", command="sleep 9", run_in_background=True),
            result_for(2, "tb", "Command running in background with ID: bx9z1"),
            assistant(3),
        ])
        self.assertEqual(s.open_tasks, {"bx9z1"})

    def test_foreground_bash_and_read_results_open_nothing(self):
        text = "Command running in background with ID: bx9z1"
        s = self.parse([
            user(1, "go"),
            tool_use(2, "Read", "tr", file_path="/docs.md"),
            result_for(2, "tr", text),
            tool_use(3, "Bash", "tf", command="grep x"),
            result_for(3, "tf", text),
            tool_use(4, "Agent", "ta"),
            result_for(4, "ta", "see: Async agent launched successfully. agentId: zz"),
            assistant(5),
        ])
        self.assertEqual(s.open_tasks, frozenset())
        self.assertTrue(s.is_complete())

    def test_notification_inside_system_reminder_does_not_close(self):
        s = self.parse([
            user(1, "go"), *launched(2, "aaa111"), assistant(3),
            user(4, "<system-reminder>x</system-reminder>\n"
                 + NOTE.format(ids="aaa111")),
        ])
        self.assertEqual(s.open_tasks, frozenset())

    def test_close_before_open_is_order_independent(self):
        s = self.parse([
            user(1, "go"), user(2, NOTE.format(ids="aaa111")),
            *launched(3, "aaa111"), assistant(4),
        ])
        self.assertEqual(s.open_tasks, frozenset())

    def test_tasks_do_not_expire_but_dead_session_is_not_active(self):
        s = self.parse([user(1, "go"), *launched(2, "aaa111"), assistant(3)])
        self.assertEqual(s.open_tasks, {"aaa111"})
        self.assertFalse(s.is_complete())  # never reaped from file state
        self.assertEqual(panel._infer_status(s), "idle")  # years old, not running


class WidthTests(unittest.TestCase):
    def test_display_width(self):
        self.assertEqual(panel.display_width("abc"), 3)
        self.assertEqual(panel.display_width("漢字"), 4)
        self.assertEqual(panel.display_width("😀"), 2)
        self.assertEqual(panel.display_width("é"), 1)  # combining mark
        self.assertEqual(panel.display_width("a​b"), 2)  # zero-width space

    def test_truncate_cols_never_exceeds_and_marks_cut(self):
        for text in ("漢字漢字漢字", "😀😀😀😀😀", "plain ascii text"):
            for cols in range(1, 9):
                out = panel.truncate_cols(text, cols)
                self.assertLessEqual(panel.display_width(out), cols)
        self.assertEqual(panel.truncate_cols("漢字漢字", 5), "漢字…")
        self.assertEqual(panel.truncate_cols("short", 10), "short")

    def test_fit_cols_pads_to_exact_width(self):
        self.assertEqual(panel.display_width(panel.fit_cols("漢a", 7)), 7)

    def test_row_with_wide_title_fits_pane(self):
        s = panel.Session(
            path="/x", id="i", cwd="/p", created=1.0, last_ts=1.0,
            name="漢字😀" * 20, harness="claude",
        )
        g = panel.Group(cwd="/p", sessions=[s])
        items = list(panel.rows_for([g], "", set(), None))
        lines, _, _ = panel.build_lines(items, 30, set(), "", None, {"/p": "p"})
        for text, kind, _ in lines:
            if kind == "session":
                self.assertLessEqual(panel.display_width(text), 29)


if __name__ == "__main__":
    unittest.main()
