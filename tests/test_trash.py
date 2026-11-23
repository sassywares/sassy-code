#!/usr/bin/env python3
"""Session delete, the trash store, restore/purge and the busy preflight."""
from __future__ import annotations

import json
import os
import signal
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.panel_loader import load_panel

panel = load_panel()


def write_pi(path: Path, sid: str, *, role: str = "assistant",
             stop: str | None = "stop", cwd: str = "/p",
             parent: str = "") -> None:
    ts = "2026-10-23T04:26:42.000Z"
    lines = [
        {"type": "session", "id": sid, "cwd": cwd, "timestamp": ts},
        {"type": "message", "timestamp": ts,
         "message": {"role": role, "content": [
             {"type": "text", "text": "hello"}]}},
    ]
    if parent:
        lines[0]["parentSession"] = parent
    if role == "assistant":
        lines[-1]["message"]["stopReason"] = stop
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(o) + "\n" for o in lines))


def entry(sid: str, trashed: str, *, deleted_at: float,
          cwd: str = "/p", title: str = "", harness: str = "pi") -> dict:
    return {
        "session_id": sid,
        "harness": harness,
        "title": title or sid,
        "original_path": f"/p/{sid}.jsonl",
        "trashed_path": trashed,
        "cwd": cwd,
        "goal_id": "",
        "done": None,
        "deleted_at": deleted_at,
    }


class TrashCase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.root = Path(self.tmpdir.name)
        values = (
            ("STATE_DIR", str(self.root / "state")),
            ("_trash_cache", None),
            ("_sessions_cache", None),
            ("_goals_cache", None),
        )
        for name, value in values:
            patcher = mock.patch.object(panel.state, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def session(self, sid: str = "a", *, title: str = "Alpha",
                role: str = "assistant", stop: str | None = "stop",
                cwd: str = "/p", parent: str = "") -> panel.Session:
        path = self.root / f"{sid}.jsonl"
        write_pi(path, sid, role=role, stop=stop, cwd=cwd, parent=parent)
        s = panel.read_session(str(path))
        assert s is not None
        s.name = title
        return s

    def group(self, s: panel.Session) -> panel.Group:
        return panel.Group(cwd=s.cwd, sessions=[s], goals=[])

    def done_record(self, sid: str) -> dict:
        data = {"version": 1, "sessions": {sid: {"done": True, "done_at": 5}}}
        panel.state.write_store_atomic(panel.state._SESSIONS_STORE, data)
        return data


class TrashStoreTests(TrashCase):
    def test_store_round_trips_and_is_newest_first(self):
        panel.state.mutate_trash(lambda data: data["entries"].extend([
            entry("old", "/t/old.jsonl", deleted_at=10),
            entry("new", "/t/new.jsonl", deleted_at=20),
        ]))
        entries, unreadable = panel.load_entries()
        self.assertFalse(unreadable)
        self.assertEqual([e["session_id"] for e in entries], ["new", "old"])

    def test_corrupt_manifest_hides_entries_and_refuses_edits(self):
        index = Path(panel.state.STATE_DIR) / "trash" / "index.json"
        index.parent.mkdir(parents=True)
        index.write_text("{not json")
        entries, unreadable = panel.load_entries()
        self.assertEqual(entries, [])
        self.assertTrue(unreadable)
        self.assertEqual(panel.trash_guard().strip(), panel.TRASH_UNREADABLE)

    def test_seven_day_prune_removes_expired_entries_and_files(self):
        files = Path(panel.trash.files_dir())
        files.mkdir(parents=True)
        old = files / "old.jsonl"
        fresh = files / "fresh.jsonl"
        old.write_text("old")
        fresh.write_text("fresh")
        now = 1_000_000.0
        panel.state.mutate_trash(lambda data: data["entries"].extend([
            entry("old", str(old), deleted_at=now - panel.TRASH_RETENTION_SECONDS - 1),
            entry("fresh", str(fresh), deleted_at=now - 1),
        ]))
        self.assertEqual(panel.prune(now=now), 1)
        self.assertFalse(old.exists())
        self.assertTrue(fresh.exists())
        entries, _ = panel.load_entries()
        self.assertEqual([e["session_id"] for e in entries], ["fresh"])

    def test_corrupt_manifest_is_left_alone_by_prune(self):
        index = Path(panel.state.STATE_DIR) / "trash" / "index.json"
        index.parent.mkdir(parents=True)
        index.write_text("broken")
        self.assertEqual(panel.prune(now=1_000_000.0), 0)
        self.assertEqual(index.read_text(), "broken")


class DeleteRestoreTests(TrashCase):
    def test_delete_moves_the_transcript_and_all_records(self):
        s = self.session()
        original = Path(s.path)
        self.done_record(s.id)
        goal = panel.create_goal(s.cwd, "Goal")
        panel.file_session_in_goal(s.cwd, goal.id, s.id)
        data = panel.read_goals_file()
        g = panel.Group(
            cwd=s.cwd,
            sessions=[s],
            goals=[panel.goal_from_record(data["projects"][s.cwd][0])],
        )
        message = panel.delete_session(
            s, g, procs=[panel.Proc(1, 0, "init")], panes=[]
        )
        self.assertTrue(message[0])
        self.assertIn("deleted", message[1])
        self.assertFalse(original.exists())
        entries, unreadable = panel.load_entries()
        self.assertFalse(unreadable)
        self.assertEqual(len(entries), 1)
        self.assertTrue(Path(entries[0]["trashed_path"]).exists())
        self.assertNotIn(s.id, panel.state.read_sessions_file()["sessions"])
        goals = panel.read_goals_file()["projects"][s.cwd]
        self.assertNotIn(s.id, goals[0]["sessions"])

    def test_restore_brings_back_file_done_mark_and_goal(self):
        s = self.session()
        original = Path(s.path)
        self.done_record(s.id)
        goal = panel.create_goal(s.cwd, "Goal")
        panel.file_session_in_goal(s.cwd, goal.id, s.id)
        data = panel.read_goals_file()
        g = panel.Group(
            cwd=s.cwd,
            sessions=[s],
            goals=[panel.goal_from_record(data["projects"][s.cwd][0])],
        )
        panel.delete_session(s, g, procs=[panel.Proc(1, 0, "init")], panes=[])
        trashed, _ = panel.load_entries()
        ok, message = panel.restore_entry(trashed[0])
        self.assertTrue(ok)
        self.assertIn("restored", message)
        self.assertTrue(original.exists())
        self.assertFalse(Path(trashed[0]["trashed_path"]).exists())
        self.assertTrue(
            panel.state.read_sessions_file()["sessions"][s.id]["done"]
        )
        goals = panel.read_goals_file()["projects"][s.cwd]
        self.assertIn(s.id, goals[0]["sessions"])
        self.assertFalse(goals[0]["done"])
        self.assertFalse(goals[0]["collapsed"])
        self.assertEqual(panel.load_entries()[0], [])

    def test_restore_with_a_missing_container_comes_back_loose(self):
        cwd = "/p"
        parent = self.session("parent", title="Parent", cwd=cwd)
        parent_goal = panel.create_goal(cwd, "ParentGoal")
        panel.file_session_in_goal(cwd, parent_goal.id, parent.id)
        s = self.session(cwd=cwd, parent=parent.path)
        goal = panel.create_goal(cwd, "Goal")
        panel.file_session_in_goal(cwd, goal.id, s.id)
        data = panel.read_goals_file()
        g = panel.Group(
            cwd=s.cwd,
            sessions=[parent, s],
            goals=[
                panel.goal_from_record(r)
                for r in data["projects"][s.cwd]
            ],
        )
        panel.delete_session(s, g, procs=[panel.Proc(1, 0, "init")], panes=[])
        panel.delete_goal(cwd, goal.id)
        trashed, _ = panel.load_entries()
        ok, message = panel.restore_entry(trashed[0])
        self.assertTrue(ok)
        self.assertIn("restored", message)
        self.assertTrue(Path(s.path).exists())
        data = panel.read_goals_file()
        self.assertEqual(
            [r["id"] for r in data["projects"][cwd]], [parent_goal.id]
        )
        # Explicitly loose: the filed parentSession ancestor must not refile it.
        self.assertIn(s.id, data["loose"][cwd])
        goals = [panel.goal_from_record(r) for r in data["projects"][cwd]]
        g = panel.Group(cwd=cwd, sessions=[parent, s], goals=goals)
        attached = panel.inherit_goal_members(g, goals, set(data["loose"][cwd]))
        self.assertNotIn(
            s.id, [sid for gl in attached for sid in gl.sessions]
        )

    def test_restore_top_level_stays_loose_under_a_filed_ancestor(self):
        cwd = "/p"
        parent = self.session("parent", title="Parent", cwd=cwd)
        goal = panel.create_goal(cwd, "ParentGoal")
        panel.file_session_in_goal(cwd, goal.id, parent.id)
        s = self.session(cwd=cwd, parent=parent.path)
        g = panel.Group(cwd=cwd, sessions=[parent, s], goals=[])
        ok, _ = panel.delete_session(
            s, g, procs=[panel.Proc(1, 0, "init")], panes=[]
        )
        self.assertTrue(ok)
        trashed, _ = panel.load_entries()
        self.assertEqual(trashed[0]["goal_id"], "")
        self.assertFalse(trashed[0]["loose"])
        ok, _ = panel.restore_entry(trashed[0])
        self.assertTrue(ok)
        data = panel.read_goals_file()
        self.assertIn(s.id, data["loose"][cwd])
        goals = [panel.goal_from_record(r) for r in data["projects"][cwd]]
        g = panel.Group(cwd=cwd, sessions=[parent, s], goals=goals)
        attached = panel.inherit_goal_members(g, goals, set(data["loose"][cwd]))
        self.assertNotIn(
            s.id, [sid for gl in attached for sid in gl.sessions]
        )

    def test_restore_collision_keeps_the_trash_copy(self):
        s = self.session()
        panel.delete_session(
            s, self.group(s), procs=[panel.Proc(1, 0, "init")], panes=[]
        )
        trashed, _ = panel.load_entries()
        write_pi(Path(s.path), s.id)
        ok, message = panel.restore_entry(trashed[0])
        self.assertFalse(ok)
        self.assertIn("original file is back", message)
        self.assertTrue(Path(trashed[0]["trashed_path"]).exists())

    def test_purge_removes_entry_and_transcript(self):
        s = self.session()
        panel.delete_session(
            s, self.group(s), procs=[panel.Proc(1, 0, "init")], panes=[]
        )
        trashed, _ = panel.load_entries()
        ok, message = panel.purge_entry(trashed[0])
        self.assertTrue(ok)
        self.assertIn("purged", message)
        self.assertFalse(Path(trashed[0]["trashed_path"]).exists())
        self.assertEqual(panel.load_entries()[0], [])

    def test_corrupt_trash_refuses_before_moving(self):
        s = self.session()
        index = Path(panel.state.STATE_DIR) / "trash" / "index.json"
        index.parent.mkdir(parents=True)
        index.write_text("broken")
        message = panel.delete_session(
            s, self.group(s), procs=[panel.Proc(1, 0, "init")], panes=[]
        )
        self.assertFalse(message[0])
        self.assertIn("trash is unreadable", message[1])
        self.assertTrue(Path(s.path).exists())

    def test_corrupt_goals_refuses_before_moving(self):
        s = self.session()
        goals = Path(panel.state.STATE_DIR) / "goals.json"
        goals.parent.mkdir(parents=True, exist_ok=True)
        goals.write_text("broken")
        ok, message = panel.delete_session(
            s, self.group(s), procs=[panel.Proc(1, 0, "init")], panes=[]
        )
        self.assertFalse(ok)
        self.assertIn("goals.json is unreadable", message)
        self.assertTrue(Path(s.path).exists())
        self.assertEqual(panel.load_entries()[0], [])

    def test_corrupt_sessions_refuses_before_moving(self):
        s = self.session()
        sessions = Path(panel.state.STATE_DIR) / "sessions.json"
        sessions.parent.mkdir(parents=True, exist_ok=True)
        sessions.write_text("broken")
        ok, message = panel.delete_session(
            s, self.group(s), procs=[panel.Proc(1, 0, "init")], panes=[]
        )
        self.assertFalse(ok)
        self.assertIn("sessions.json is unreadable", message)
        self.assertTrue(Path(s.path).exists())
        self.assertEqual(panel.load_entries()[0], [])

    def test_sessions_write_failure_rolls_the_transcript_back(self):
        s = self.session()
        original = Path(s.path)
        self.done_record(s.id)
        before = panel.state.read_sessions_file()["sessions"]
        with mock.patch.object(
            panel.state, "restore_session_done",
            side_effect=panel.state.SessionError("could not save done marks"),
        ):
            ok, message = panel.delete_session(
                s, self.group(s), procs=[panel.Proc(1, 0, "init")], panes=[]
            )
        self.assertFalse(ok)
        self.assertIn("could not save done marks", message)
        self.assertTrue(original.exists())
        self.assertEqual(panel.load_entries()[0], [])
        self.assertEqual(
            panel.state.read_sessions_file()["sessions"], before
        )

    def test_goals_write_failure_rolls_the_transcript_back(self):
        s = self.session()
        original = Path(s.path)
        self.done_record(s.id)
        goal = panel.create_goal(s.cwd, "Goal")
        panel.file_session_in_goal(s.cwd, goal.id, s.id)
        data = panel.read_goals_file()
        g = panel.Group(
            cwd=s.cwd,
            sessions=[s],
            goals=[panel.goal_from_record(data["projects"][s.cwd][0])],
        )
        with mock.patch.object(
            panel.goals, "remove_session_membership",
            side_effect=panel.state.GoalStoreError("could not save goal"),
        ):
            ok, message = panel.delete_session(
                s, g, procs=[panel.Proc(1, 0, "init")], panes=[]
            )
        self.assertFalse(ok)
        self.assertIn("could not save goal", message)
        self.assertTrue(original.exists())
        self.assertEqual(panel.load_entries()[0], [])
        self.assertIn(s.id, panel.state.read_sessions_file()["sessions"])
        goals = panel.read_goals_file()["projects"][s.cwd]
        self.assertIn(s.id, goals[0]["sessions"])

    def test_current_write_failure_rolls_everything_back(self):
        s = self.session()
        original = Path(s.path)
        self.done_record(s.id)
        goal = panel.create_goal(s.cwd, "Goal")
        panel.file_session_in_goal(s.cwd, goal.id, s.id)
        data = panel.read_goals_file()
        g = panel.Group(
            cwd=s.cwd,
            sessions=[s],
            goals=[panel.goal_from_record(data["projects"][s.cwd][0])],
        )
        with mock.patch.object(panel.state, "write_current",
                               side_effect=OSError("no current")):
            message = panel.delete_session(
                s, g, presented=s.id, procs=[panel.Proc(1, 0, "init")],
                panes=[],
            )
        self.assertFalse(message[0])
        self.assertIn("nothing was deleted", message[1])
        self.assertTrue(original.exists())
        self.assertEqual(panel.load_entries()[0], [])
        self.assertIn(s.id, panel.state.read_sessions_file()["sessions"])
        goals = panel.read_goals_file()["projects"][s.cwd]
        self.assertIn(s.id, goals[0]["sessions"])


class PreflightTests(TrashCase):
    def test_done_marked_running_turn_refuses(self):
        s = self.session(role="user", stop=None)
        s.status = "done"
        self.assertEqual(
            panel.busy_reason(s, [], [panel.Proc(1, 0, "init")]),
            "a turn is in progress",
        )

    def test_open_background_work_refuses(self):
        s = self.session()
        s.queued_input = True
        self.assertEqual(
            panel.busy_reason(s, [], [panel.Proc(1, 0, "init")]),
            "background work is still open",
        )

    def test_external_agent_refuses(self):
        s = self.session()
        procs = [
            panel.Proc(1, 0, "init"),
            panel.Proc(10, 1, f"pi --session {s.id}"),
        ]
        self.assertEqual(
            panel.busy_reason(s, [], procs),
            "it is running outside sassy-code",
        )

    def test_empty_process_snapshot_is_unknown(self):
        s = self.session()
        self.assertEqual(panel.busy_reason(s, [], []), "liveness is unknown")

    def test_stop_sigterms_and_refuses_on_timeout(self):
        s = self.session()
        pane = panel.PaneInfo("%3", "@2", panel.core.BG_SESSION, 100, tag=s.id)
        procs = [
            panel.Proc(1, 0, "init"),
            panel.Proc(100, 1, "bash"),
            panel.Proc(10, 100, f"pi --session {s.id}"),
        ]
        with mock.patch.object(panel.trash._procs, "process_table",
                               return_value=procs), \
                mock.patch.object(panel.trash.os, "kill") as kill, \
                mock.patch.object(panel.trash, "STOP_TIMEOUT_SECONDS", 0):
            message = panel.stop_session(s, [pane], procs)
        self.assertIn("did not stop", message)
        kill.assert_called_once_with(10, signal.SIGTERM)

    def test_stop_returns_when_the_agent_exits(self):
        s = self.session()
        pane = panel.PaneInfo("%3", "@2", panel.core.BG_SESSION, 100, tag=s.id)
        procs = [
            panel.Proc(1, 0, "init"),
            panel.Proc(100, 1, "bash"),
            panel.Proc(10, 100, f"pi --session {s.id}"),
        ]
        with mock.patch.object(
            panel.trash._procs, "process_table",
            return_value=[panel.Proc(1, 0, "init")],
        ), mock.patch.object(panel.trash.os, "kill"):
            self.assertEqual(panel.stop_session(s, [pane], procs), "")

    def test_stop_refuses_a_process_outside_a_managed_pane(self):
        s = self.session()
        procs = [
            panel.Proc(1, 0, "init"),
            panel.Proc(10, 1, f"pi --session {s.id}"),
        ]
        with mock.patch.object(panel.trash.os, "kill") as kill:
            message = panel.stop_session(s, [], procs)
        self.assertIn("running outside", message)
        kill.assert_not_called()

    def test_presented_session_leaves_a_shell_and_clears_the_tag(self):
        s = self.session()
        pane = panel.PaneInfo("%2", "@1", "sassy-code", 100, tag=s.id)
        procs = [panel.Proc(1, 0, "init"), panel.Proc(100, 1, "bash")]
        panel.write_current(s.id)
        with mock.patch.object(panel.tmuxctl, "clear_pane_tag") as clear:
            message = panel.delete_session(
                s, self.group(s), presented=s.id, panes=[pane],
                procs=procs, viewer="%2",
            )
        self.assertTrue(message[0])
        self.assertIn("deleted", message[1])
        self.assertFalse(Path(s.path).exists())
        self.assertEqual(panel.state._read_state(), "")
        clear.assert_called_once_with("%2")

    def test_hidden_pane_is_killed_and_unreaped(self):
        s = self.session()
        pane = panel.PaneInfo("%3", "@2", panel.core.BG_SESSION, 100, tag=s.id)
        procs = [panel.Proc(1, 0, "init"), panel.Proc(100, 1, "bash")]
        reap = {"%3": 1.0}
        with mock.patch.object(panel.tmuxctl, "kill_pane") as kill:
            panel.delete_session(
                s, self.group(s), panes=[pane], procs=procs, reap_state=reap,
            )
        kill.assert_called_once_with("%3")
        self.assertNotIn("%3", reap)


class PickerTests(TrashCase):
    def entries(self) -> list[dict]:
        return [
            entry("a", "/t/a", deleted_at=10, cwd="/work/alpha", title="Alpha"),
            entry("b", "/t/b", deleted_at=20, cwd="/work/beta", title="Beta"),
        ]

    def test_filter_matches_title_project_and_harness(self):
        entries = self.entries()
        self.assertTrue(panel.entry_matches(entries[0], "alp"))
        self.assertTrue(panel.entry_matches(entries[1], "beta"))
        self.assertTrue(panel.entry_matches(entries[0], "Pi"))
        self.assertFalse(panel.entry_matches(entries[0], "claude"))

    def test_slash_filter_narrows_the_list_and_enter_restores(self):
        state = panel.trash_box_start(self.entries())
        state, _ = panel.trash_box_key(state, ord("/"))
        for ch in "beta":
            state, _ = panel.trash_box_key(state, ch)
        state, _ = panel.trash_box_key(state, panel.curses.KEY_ENTER)
        self.assertEqual([e["session_id"] for e in panel.trash_visible(state)],
                         ["b"])
        state, action = panel.trash_box_key(state, panel.curses.KEY_ENTER)
        self.assertEqual(action, "restore")

    def test_empty_list_says_so_and_escape_closes(self):
        state = panel.trash_box_start([])
        lines, _ = panel.trash_lines(state)
        self.assertIn("no deleted sessions", lines[0][0])
        self.assertEqual(panel.trash_box_key(state, 27)[1], "cancel")

    def test_d_asks_before_purging(self):
        state = panel.trash_box_start(self.entries())
        state, action = panel.trash_box_key(state, ord("d"))
        self.assertEqual(action, "")
        self.assertIsNotNone(state["confirm"])
        state, action = panel.trash_box_key(state, ord("y"))
        self.assertEqual(action, "purge")

    def test_rows_share_columns_and_a_long_title_takes_the_reclaimed_width(self):
        entries = [
            entry(
                "long", "/t/long", deleted_at=0, cwd="/work/alpha",
                title="A title much too long for a narrow panel " * 4,
                harness="claude",
            ),
            entry("short", "/t/short", deleted_at=0, cwd="/work/alpha",
                  title="Short"),
        ]
        # Dropping the project column gives its width to the title; every row
        # still fills the box exactly and only the text inside a column cuts.
        for width in (80, 50, 36, 30, 24):
            state = panel.trash_box_start(entries)
            state["width"] = width
            lines, _selected = panel.trash_lines(state)
            rows = [text for text, _attr, *kind in lines if not kind]
            headers = [
                text for text, _attr, *kind in lines
                if (kind[0] if kind else "") == panel.PICKER_HEADING
            ]
            with self.subTest(width=width):
                self.assertEqual(
                    len({panel.display_width(text) for text in rows}), 1
                )
                long_row = rows[0]
                self.assertEqual(panel.display_width(long_row), width - 3)
                self.assertIn("…", long_row)
                self.assertIn("Claude Code", long_row)
                self.assertTrue(long_row.endswith("?"))
                # The project is a header now, not a repeated row column.
                self.assertNotIn("alpha", long_row)
                self.assertEqual([text.strip() for text in headers],
                                 ["alpha"])

    def test_projects_get_a_header_and_newest_first_inside(self):
        entries = [
            entry("a-old", "/t/a-old", deleted_at=10, cwd="/work/beta",
                  title="Beta old"),
            entry("a-new", "/t/a-new", deleted_at=30, cwd="/work/alpha",
                  title="Alpha new"),
            entry("b-old", "/t/b-old", deleted_at=20, cwd="/work/alpha",
                  title="Alpha old"),
        ]
        state = panel.trash_box_start(entries)
        self.assertEqual(
            [e["session_id"] for e in panel.trash_visible(state)],
            ["a-new", "b-old", "a-old"],
        )
        lines, selected = panel.trash_lines(state)
        headers = [
            (i, text) for i, (text, _attr, *kind) in enumerate(lines)
            if (kind[0] if kind else "") == panel.PICKER_HEADING
        ]
        # One rule between groups, never above the first header.
        self.assertEqual([text.strip() for _i, text in headers],
                         ["alpha", "beta"])
        dividers = [
            i for i, (_text, _attr, *kind) in enumerate(lines)
            if (kind[0] if kind else "") == panel.PICKER_DIVIDER
        ]
        self.assertEqual(dividers, [headers[1][0] - 1])
        self.assertTrue(
            lines[headers[0][0] + 1][0].strip().startswith("Alpha new")
        )
        self.assertTrue(
            lines[headers[1][0] + 1][0].strip().startswith("Beta old")
        )
        self.assertTrue(lines[selected][0].strip().startswith("Alpha new"))
        self.assertNotEqual(
            (lines[selected][2][0] if len(lines[selected]) > 2 else ""),
            panel.PICKER_HEADING,
        )

    def test_mouse_selects_entries_but_never_a_header(self):
        entries = [
            entry("a", "/t/a", deleted_at=30, cwd="/work/alpha",
                  title="Alpha"),
            entry("b", "/t/b", deleted_at=20, cwd="/work/beta", title="Beta"),
        ]
        state = panel.trash_box_start(entries)
        lines, _selected, rows = panel.boxes._trash_layout(state)
        headers = [
            i for i, (_text, _attr, *kind) in enumerate(lines)
            if (kind[0] if kind else "") == panel.PICKER_HEADING
        ]
        self.assertEqual(len(headers), 2)
        for line in headers:
            panel.trash_box_mouse_pick(state, line)
            self.assertEqual(state["index"], 0)
        last_entry_line = max(rows)
        panel.trash_box_mouse_pick(state, last_entry_line)
        self.assertEqual(state["index"], rows[last_entry_line])
        self.assertEqual(state["index"], 1)

    def test_notice_and_filter_header_are_not_pickable(self):
        state = panel.trash_box_start(self.entries())
        state["filter"] = "a"  # draws the " filter: a" line above the groups
        lines, _selected, rows = panel.boxes._trash_layout(state)
        self.assertTrue(lines[0][0].startswith(" filter:"))
        panel.trash_box_mouse_pick(state, 0)
        self.assertEqual(state["index"], 0)
        self.assertNotIn(0, rows)

    def test_action_line_comes_from_the_keymap(self):
        state = panel.trash_box_start(self.entries())
        self.assertEqual(
            "  ".join(text for text, _attr in panel.trash_footer(state)),
            "⏎ restore  d purge",
        )

    def test_action_line_wraps_on_a_narrow_panel(self):
        scr = mock.Mock()
        scr.getmaxyx.return_value = (24, 20)
        state = panel.trash_box_start(self.entries())
        state["screen"] = scr
        lines = [text for text, _attr in panel.trash_footer(state)]
        self.assertGreater(len(lines), 1)
        self.assertEqual("  ".join(lines), "⏎ restore  d purge")
        self.assertFalse(any("…" in text for text in lines))
        win = mock.Mock()
        win.getch.side_effect = [27]  # Esc
        with mock.patch.object(panel.curses, "newwin", return_value=win):
            self.assertIsNone(panel.trash_box(scr, self.entries()))
        drawn = [call.args[2] for call in win.addnstr.call_args_list]
        footer = [t for t in drawn if "restore" in t or "purge" in t]
        self.assertEqual("  ".join(t.strip() for t in footer),
                         "⏎ restore  d purge")
        self.assertFalse(any("…" in t for t in footer))
        self.assertIn(panel.divider_text(20), drawn)  # a rule above the hints

    def test_box_draws_a_separator_above_the_pinned_action_line(self):
        scr = mock.Mock()
        scr.getmaxyx.return_value = (24, 40)
        win = mock.Mock()
        win.getch.side_effect = [27]  # Esc
        with mock.patch.object(panel.curses, "newwin", return_value=win):
            self.assertIsNone(panel.trash_box(scr, self.entries()))
        frame = [(call.args[0], call.args[2])
                 for call in win.addnstr.call_args_list]
        rule = next(
            (
                i for i, (_y, text) in enumerate(frame)
                if text == panel.divider_text(40)
                and i + 1 < len(frame) and "restore" in frame[i + 1][1]
            ),
            None,
        )
        self.assertIsNotNone(rule)
        # The hints follow immediately under the rule, not the list.
        self.assertEqual(frame[rule + 1][0], frame[rule][0] + 1)
        self.assertIn("restore", frame[rule + 1][1])

    def test_pinned_action_block_keeps_the_last_entry_visible(self):
        entries = [
            entry(f"e{i}", f"/t/e{i}", deleted_at=100 - i,
                  cwd="/work/alpha", title=f"Entry {i}")
            for i in range(6)
        ]
        scr = mock.Mock()
        scr.getmaxyx.return_value = (8, 40)
        win = mock.Mock()
        win.getch.side_effect = [panel.curses.KEY_DOWN] * 5 + [27]
        with mock.patch.object(panel.curses, "newwin", return_value=win):
            self.assertIsNone(panel.trash_box(scr, entries))
        drawn = [call.args[2] for call in win.addnstr.call_args_list]
        # The rule and hint take their own rows; the selected last entry is
        # still drawn above them rather than squeezed away.
        self.assertTrue(any("Entry 5" in text for text in drawn))
        self.assertIn(panel.divider_text(40), drawn)

    def test_click_on_the_separator_or_hints_does_nothing(self):
        scr = mock.Mock()
        scr.getmaxyx.return_value = (24, 40)
        win = mock.Mock()
        win.getch.side_effect = [panel.MOUSE_KEY, panel.MOUSE_KEY, 27]
        # 2 entries + 2 borders + 1 separator + 1 hint = height 6 at box_y 9.
        box_y = 9
        clicks = [
            (0, 5, box_y + 1 + 3, 0, panel.curses.BUTTON1_CLICKED),
            (0, 5, box_y + 1 + 4, 0, panel.curses.BUTTON1_CLICKED),
        ]
        with mock.patch.object(panel.curses, "newwin", return_value=win), \
                mock.patch.object(panel.curses, "getmouse",
                                  side_effect=clicks):
            self.assertIsNone(panel.trash_box(scr, self.entries()))

    def test_empty_and_empty_filter_states_have_no_header(self):
        state = panel.trash_box_start([])
        lines, _selected = panel.trash_lines(state)
        self.assertIn("no deleted sessions", lines[0][0])
        self.assertFalse(any(
            (kind[0] if kind else "") == panel.PICKER_HEADING
            for _text, _attr, *kind in lines
        ))
        state = panel.trash_box_start(self.entries())
        state["filter"] = "nothing-matches"
        lines, _selected = panel.trash_lines(state)
        self.assertTrue(any("no matches" in text for text, _attr in lines))
        self.assertFalse(any(
            (kind[0] if kind else "") == panel.PICKER_HEADING
            for _text, _attr, *kind in lines
        ))

    def test_empty_filter_result_says_so(self):
        state = panel.trash_box_start(self.entries())
        state["filter"] = "nothing-matches"
        lines, _selected = panel.trash_lines(state)
        self.assertTrue(any("no matches" in text for text, _attr in lines))


class TrashSectionRuleTests(TrashCase):
    """A rule separates project groups, with no blank gap (D28)."""

    def entries(self) -> list[dict]:
        return [
            entry("a", "/t/a", deleted_at=60, cwd="/work/alpha", title="Alpha"),
            entry("b", "/t/b", deleted_at=50, cwd="/work/beta", title="Beta"),
            entry("c", "/t/c", deleted_at=40, cwd="/work/alpha",
                  title="Alpha old"),
            entry("d", "/t/d", deleted_at=30, cwd="/work/gamma",
                  title="Gamma"),
        ]

    def heading_lines(self, lines) -> list[int]:
        return [
            i for i, (_text, _attr, *kind) in enumerate(lines)
            if (kind[0] if kind else "") == panel.PICKER_HEADING
        ]

    def divider_lines(self, lines) -> list[int]:
        return [
            i for i, (_text, _attr, *kind) in enumerate(lines)
            if (kind[0] if kind else "") == panel.PICKER_DIVIDER
        ]

    def gap_lines(self, lines) -> list[int]:
        return [
            i for i, (text, _attr, *kind) in enumerate(lines)
            if text == "" and not kind
        ]

    def test_one_rule_between_groups_and_none_before_the_first(self):
        state = panel.trash_box_start(self.entries())
        lines, _selected, rows = panel.boxes._trash_layout(state)
        headers = self.heading_lines(lines)
        dividers = self.divider_lines(lines)
        self.assertEqual(len(headers), 3)
        # Exactly one rule per boundary, each on the line above the header.
        self.assertEqual(dividers, [headers[1] - 1, headers[2] - 1])
        self.assertNotIn(0, dividers)
        self.assertNotIn(headers[0] - 1, dividers)
        for divider in dividers:
            self.assertNotIn(divider, rows)
            self.assertNotIn(divider, headers)
            self.assertNotIn(
                (lines[divider - 1][2][0] if len(lines[divider - 1]) > 2 else ""),
                (panel.PICKER_HEADING,),
            )
            self.assertEqual(
                (lines[divider + 1][2] if len(lines[divider + 1]) > 2 else ""),
                panel.PICKER_HEADING,
            )
        # The rule replaces the blank gap entirely: no empty line left over.
        self.assertEqual(self.gap_lines(lines), [])

    def test_empty_and_empty_filter_states_have_no_rule_or_gap(self):
        states = [panel.trash_box_start([])]
        filtered = panel.trash_box_start(self.entries())
        filtered["filter"] = "nothing-matches"
        states.append(filtered)
        for state in states:
            lines, _selected = panel.trash_lines(state)
            self.assertEqual(self.divider_lines(lines), [])
            self.assertEqual(self.gap_lines(lines), [])
            self.assertEqual(self.heading_lines(lines), [])

    def test_neither_keys_nor_mouse_can_select_a_rule(self):
        state = panel.trash_box_start(self.entries())
        lines, _selected, rows = panel.boxes._trash_layout(state)
        dividers = self.divider_lines(lines)
        self.assertTrue(dividers)
        for divider in dividers:
            before = state["index"]
            panel.trash_box_mouse_pick(state, divider)
            self.assertEqual(state["index"], before)
        visible = panel.trash_visible(state)
        for _ in range(len(visible) + 2):
            state, _action = panel.trash_box_key(state, panel.curses.KEY_DOWN)
            lines, selected, rows = panel.boxes._trash_layout(state)
            self.assertIn(selected, rows)
            self.assertNotIn(selected, self.divider_lines(lines))
            self.assertNotIn(
                (lines[selected][2][0] if len(lines[selected]) > 2 else ""),
                (panel.PICKER_HEADING, panel.PICKER_DIVIDER),
            )
            self.assertNotEqual(lines[selected][0].strip(), "")
        self.assertEqual(state["index"], len(visible) - 1)
        for _ in range(len(visible) + 2):
            state, _action = panel.trash_box_key(state, panel.curses.KEY_UP)
            lines, selected, _rows = panel.boxes._trash_layout(state)
            self.assertNotIn(selected, self.divider_lines(lines))
        self.assertEqual(state["index"], 0)

    def test_rules_and_rows_fit_a_narrow_panel(self):
        state = panel.trash_box_start(self.entries())
        visible = panel.trash_visible(state)
        for width in (20, 24):
            state["width"] = width
            lines, selected, rows = panel.boxes._trash_layout(state)
            headers = self.heading_lines(lines)
            dividers = self.divider_lines(lines)
            self.assertEqual(dividers, [headers[1] - 1, headers[2] - 1])
            self.assertEqual(
                panel.display_width(panel.divider_text(width)), width
            )
            self.assertEqual(self.gap_lines(lines), [])
            body = [
                text for text, _attr, *kind in lines
                if (kind[0] if kind else "") != panel.PICKER_DIVIDER
            ]
            self.assertEqual(
                max(panel.display_width(text) for text in body), width - 3
            )
            self.assertEqual(sorted(rows.values()), list(range(len(visible))))
            # The last entry is reachable by keys and clickable by mouse.
            for _ in range(len(visible) + 2):
                state, _action = panel.trash_box_key(
                    state, panel.curses.KEY_DOWN
                )
            lines, selected, rows = panel.boxes._trash_layout(state)
            self.assertEqual(state["index"], len(visible) - 1)
            self.assertEqual(rows[selected], len(visible) - 1)
            self.assertIn(visible[-1]["title"], lines[selected][0])
            self.assertNotIn(selected, self.divider_lines(lines))
            panel.trash_box_mouse_pick(state, selected)
            self.assertEqual(state["index"], len(visible) - 1)


if __name__ == "__main__":
    unittest.main()


class CascadeCase(TrashCase):
    """One project holding a goal, an optional slice and real transcripts."""

    PROCS = [panel.Proc(1, 0, "init")]

    def build(self, goal_ids=(), slice_ids=(), roles=None, with_slice=True):
        roles = roles or {}
        goal = panel.create_goal("/p", "Goal")
        slice_ = panel.create_slice("/p", goal.id, "Slice")
        sessions = []
        for sid, container in (
            [(sid, goal.id) for sid in goal_ids]
            + [(sid, slice_.id) for sid in slice_ids]
        ):
            role = roles.get(sid, "assistant")
            stop = None if role == "user" else "stop"
            s = self.session(sid, title=sid, role=role, stop=stop)
            panel.file_session_in_goal("/p", container, sid)
            sessions.append(s)
        if not with_slice:
            panel.delete_goal("/p", slice_.id)
        g = panel.Group(
            cwd="/p", sessions=sorted(sessions, key=lambda s: -s.last_ts)
        )
        panel.attach_goals([g])
        goal_obj = next(x for x in g.goals if x.id == goal.id)
        slice_obj = next(
            (x for x in g.goals if x.id == slice_.id), None
        )
        return g, goal_obj, slice_obj

    def mark_done(self, *ids):
        def apply(data):
            for sid in ids:
                data["sessions"][sid] = {"done": True, "done_at": 1}

        panel.state.mutate_sessions(apply)

    def files(self, *ids):
        return {sid: self.root / f"{sid}.jsonl" for sid in ids}

    def delete(self, g, goal, **kwargs):
        kwargs.setdefault("procs", self.PROCS)
        kwargs.setdefault("panes", [])
        return panel.delete_subtree(g, goal, **kwargs)


class CascadeTests(CascadeCase):
    def test_goal_cascade_trashes_every_subtree_session(self):
        g, goal, _ = self.build(["a", "b"], ["c"])
        self.mark_done("c")
        ok, text = self.delete(g, goal)
        self.assertTrue(ok)
        self.assertIn("deleted goal 'Goal' and 3 sessions", text)
        for path in self.files("a", "b", "c").values():
            self.assertFalse(path.exists())
        entries, unreadable = panel.load_entries()
        self.assertFalse(unreadable)
        self.assertEqual(
            sorted(e["session_id"] for e in entries), ["a", "b", "c"]
        )
        self.assertEqual(panel.read_goals_file()["projects"]["/p"], [])
        self.assertEqual(panel.state.read_sessions_file()["sessions"], {})

    def test_inherited_successor_is_part_of_the_subtree(self):
        parent = self.session("parent", title="parent")
        goal = panel.create_goal("/p", "Goal")
        panel.file_session_in_goal("/p", goal.id, parent.id)
        child = self.session("child", title="child", parent=parent.path)
        g = panel.Group(
            cwd="/p", sessions=sorted([parent, child], key=lambda s: -s.last_ts)
        )
        panel.attach_goals([g])
        goal_obj = next(x for x in g.goals if x.id == goal.id)
        self.assertIn(
            child.id,
            [x.id for x in panel.goal_subtree_members(g, goal_obj)],
        )
        self.assertEqual(
            panel.delete_prompt(("goal", g, goal_obj))[0],
            " Delete goal 'Goal' and 2 sessions? y/n ",
        )
        ok, text = self.delete(g, goal_obj)
        self.assertTrue(ok)
        self.assertIn("and 2 sessions", text)
        self.assertFalse(self.files("parent", "child")["parent"].exists())
        self.assertFalse(self.files("parent", "child")["child"].exists())
        self.assertEqual(len(panel.load_entries()[0]), 2)

    def test_restored_member_comes_back_loose_with_its_done_mark(self):
        g, goal, _ = self.build(["a"], ["c"])
        self.mark_done("c")
        self.assertTrue(self.delete(g, goal)[0])
        entries, _ = panel.load_entries()
        c_entry = next(e for e in entries if e["session_id"] == "c")
        ok, _ = panel.restore_entry(c_entry)
        self.assertTrue(ok)
        self.assertTrue(self.files("c")["c"].exists())
        self.assertEqual(panel.read_goals_file()["projects"]["/p"], [])
        self.assertIn("c", panel.read_goals_file()["loose"]["/p"])
        self.assertTrue(
            panel.state.read_sessions_file()["sessions"]["c"]["done"]
        )

    def test_slice_cascade_keeps_the_parent_goal(self):
        g, goal, slice_ = self.build(["d"], ["a", "b"])
        message, key = panel.delete_goal_row(
            [g], ("slice", g, slice_), procs=self.PROCS, panes=[],
        )
        self.assertIn("deleted slice 'Slice' and 2 sessions", message)
        self.assertEqual(key, ("goal", goal.id, "/p"))
        self.assertEqual(
            [r["name"] for r in panel.read_goals_file()["projects"]["/p"]],
            ["Goal"],
        )
        self.assertEqual(len(panel.load_entries()[0]), 2)
        self.assertTrue(self.files("d")["d"].exists())

    def test_goal_row_selects_the_project_heading(self):
        g, goal, _ = self.build(["a"], [], with_slice=False)
        message, key = panel.delete_goal_row(
            [g], ("goal", g, goal), procs=self.PROCS, panes=[]
        )
        self.assertIn("deleted goal 'Goal' and 1 session", message)
        self.assertEqual(key, ("group", "/p"))

    def test_prompt_counts_members_and_omits_zero(self):
        g, goal, slice_ = self.build(["a", "b"], ["c"])
        self.assertEqual(
            panel.delete_prompt(("goal", g, goal))[0],
            " Delete goal 'Goal' and 3 sessions? y/n ",
        )
        self.assertEqual(
            panel.delete_prompt(("slice", g, slice_))[0],
            " Delete slice 'Slice' and 1 session? y/n ",
        )
        empty = panel.Group(cwd="/p", sessions=[], goals=[])
        goal_obj = panel.create_goal("/p", "Empty")
        empty.goals = [goal_obj]
        self.assertEqual(
            panel.delete_prompt(("goal", empty, goal_obj))[0],
            " Delete goal 'Empty'? y/n ",
        )

    def test_one_busy_member_refuses_the_whole_cascade(self):
        g, goal, _ = self.build(["a", "b"], ["c"], roles={"b": "user"})
        before = panel.read_goals_file()
        ok, text = self.delete(g, goal)
        self.assertFalse(ok)
        self.assertIn("cannot delete goal 'Goal'", text)
        self.assertIn("'b'", text)
        self.assertIn("a turn is in progress", text)
        for path in self.files("a", "b", "c").values():
            self.assertTrue(path.exists())
        self.assertEqual(panel.load_entries()[0], [])
        self.assertEqual(panel.read_goals_file(), before)

    def test_external_member_refuses_the_whole_cascade(self):
        g, goal, _ = self.build(["a", "b"])
        procs = self.PROCS + [panel.Proc(10, 1, "pi --session a")]
        ok, text = self.delete(g, goal, procs=procs)
        self.assertFalse(ok)
        self.assertIn("'a'", text)
        self.assertIn("it is running outside sassy-code", text)
        for path in self.files("a", "b").values():
            self.assertTrue(path.exists())
        self.assertEqual(panel.load_entries()[0], [])

    def test_unknown_liveness_refuses_the_whole_cascade(self):
        g, goal, _ = self.build(["a"])
        ok, text = self.delete(g, goal, procs=[])
        self.assertFalse(ok)
        self.assertIn("liveness is unknown", text)
        self.assertTrue(self.files("a")["a"].exists())
        self.assertEqual(panel.load_entries()[0], [])

    def test_failed_move_rolls_back_nothing_trashed(self):
        g, goal, _ = self.build(["a", "b"])
        before = panel.read_goals_file()
        real_move = panel.trash.shutil.move
        calls = {"n": 0}

        def move(src, dst):
            calls["n"] += 1
            if calls["n"] == 2:
                raise OSError(28, "No space left on device")
            return real_move(src, dst)

        with mock.patch.object(panel.trash.shutil, "move", side_effect=move):
            ok, text = self.delete(g, goal)
        self.assertFalse(ok)
        self.assertIn("nothing was deleted", text)
        for path in self.files("a", "b").values():
            self.assertTrue(path.exists())
        self.assertEqual(panel.load_entries()[0], [])
        self.assertEqual(panel.read_goals_file(), before)

    def test_failed_sessions_write_rolls_the_moves_back(self):
        g, goal, _ = self.build(["a", "b"])
        self.mark_done("a")
        before = panel.read_goals_file()
        with mock.patch.object(
            panel.state, "clear_session_dones",
            side_effect=panel.SessionError("could not save session"),
        ):
            ok, text = self.delete(g, goal)
        self.assertFalse(ok)
        self.assertIn("could not save session", text)
        for path in self.files("a", "b").values():
            self.assertTrue(path.exists())
        self.assertEqual(panel.load_entries()[0], [])
        self.assertEqual(panel.read_goals_file(), before)
        self.assertTrue(
            panel.state.read_sessions_file()["sessions"]["a"]["done"]
        )

    def test_failed_goals_write_restores_done_marks_and_trash(self):
        g, goal, _ = self.build(["a"])
        self.mark_done("a")
        with mock.patch.object(
            panel.goals, "delete_goal_with_members",
            side_effect=panel.GoalError("could not save goal"),
        ):
            ok, text = self.delete(g, goal)
        self.assertFalse(ok)
        self.assertIn("could not save goal", text)
        self.assertTrue(self.files("a")["a"].exists())
        self.assertEqual(panel.load_entries()[0], [])
        self.assertTrue(
            panel.state.read_sessions_file()["sessions"]["a"]["done"]
        )
        self.assertEqual(len(panel.read_goals_file()["projects"]["/p"]), 2)

    def test_presented_member_clears_current_and_drops_the_tag(self):
        g, goal, _ = self.build(["a", "b"])
        panel.write_current("a")
        pane = panel.PaneInfo("%2", "@1", "sassy-code", 100, tag="a")
        with mock.patch.object(panel.tmuxctl, "clear_pane_tag") as clear:
            ok, text = self.delete(
                g, goal, presented="a", panes=[pane], viewer="%2",
            )
        self.assertTrue(ok)
        self.assertIn("deleted goal 'Goal' and 2 sessions", text)
        self.assertEqual(panel.state._read_state(), "")
        clear.assert_called_once_with("%2")

    def test_failed_goals_write_restores_current_after_clearing_it(self):
        g, goal, _ = self.build(["a"])
        panel.write_current("a")
        pane = panel.PaneInfo("%2", "@1", "sassy-code", 100, tag="a")
        with mock.patch.object(
            panel.goals, "delete_goal_with_members",
            side_effect=panel.GoalError("could not save goal"),
        ):
            ok, text = self.delete(
                g, goal, presented="a", panes=[pane], viewer="%2",
            )
        self.assertFalse(ok)
        self.assertIn("could not save goal", text)
        self.assertEqual(panel.state._read_state(), "a")
        self.assertTrue(self.files("a")["a"].exists())
        self.assertEqual(panel.load_entries()[0], [])

    def test_member_moved_to_another_container_is_not_trashed(self):
        g, goal, _ = self.build(["a", "b"])
        other = panel.create_goal("/p", "Other")
        panel.move_session("/p", "b", other.id)
        ok, text = self.delete(g, goal)
        self.assertTrue(ok)
        self.assertIn("deleted goal 'Goal' and 1 session", text)
        self.assertFalse(self.files("a")["a"].exists())
        self.assertTrue(self.files("b")["b"].exists())
        records = panel.read_goals_file()["projects"]["/p"]
        kept = next(r for r in records if r["id"] == other.id)
        self.assertEqual(kept["sessions"], ["b"])


class DeleteSelectionTests(TrashCase):
    """`D` on a session lands on its neighbour among the rows as drawn."""

    def project(self, cwd, *specs):
        sessions = []
        for rank, (sid, title) in enumerate(specs):
            s = self.session(sid, title=title, cwd=cwd)
            s.last_ts = 1000.0 - rank
            sessions.append(s)
        return panel.Group(cwd=cwd, sessions=sessions, goals=[])

    def drawn(self, groups, flt=""):
        return list(panel.rows_for(groups, flt, set()))

    def delete(self, groups, rows, sid, cwd="/p"):
        row = next(r for r in rows if panel.row_key(r) == ("session", sid, cwd))
        with mock.patch.object(
            panel.trash, "delete_session", return_value=(True, "deleted")
        ):
            message, key = panel.delete_session_row(groups, row, rows=rows)
        self.assertEqual(message, "deleted")
        return key

    def test_in_place_row_takes_the_deleted_seat(self):
        p = self.project("/p", ("a", "a"), ("b", "b"))
        q = self.project("/q", ("c", "c"))
        rows = self.drawn([p, q])
        key = self.delete([p, q], rows, "a")
        self.assertEqual(key, ("session", "b", "/p"))
        after = self.drawn([
            panel.Group(cwd="/p", sessions=[p.sessions[1]], goals=[]), q,
        ])
        self.assertEqual(panel.cursor_for_key(after, key), 1)

    def test_last_row_of_a_project_selects_the_row_above(self):
        p = self.project("/p", ("a", "a"), ("b", "b"))
        q = self.project("/q", ("c", "c"))
        rows = self.drawn([p, q])
        self.assertEqual(self.delete([p, q], rows, "b"), ("session", "a", "/p"))

    def test_empty_project_falls_back_to_its_heading(self):
        p = self.project("/p", ("a", "a"))
        q = self.project("/q", ("c", "c"))
        rows = self.drawn([p, q])
        self.assertEqual(self.delete([p, q], rows, "a"), ("group", "/p"))

    def test_tier_more_row_is_not_the_neighbour(self):
        specs = tuple((f"s{i}", f"s{i}") for i in range(1, 7))
        p = self.project("/p", *specs)
        rows = self.drawn([p])
        self.assertEqual(
            [row[0] for row in rows],
            ["group"] + ["session"] * panel.core.SESSION_LIMIT + ["more"],
        )
        key = self.delete([p], rows, "s5")
        self.assertEqual(key, ("session", "s4", "/p"))
        left = [s for s in p.sessions if s.id != "s5"]
        after = self.drawn([
            panel.Group(cwd="/p", sessions=left, goals=[]),
        ])
        index = panel.cursor_for_key(after, key)
        self.assertEqual(after[index][0], "session")
        self.assertEqual(panel.row_key(after[index]), ("session", "s4", "/p"))

    def test_neighbour_comes_from_the_filtered_rows_only(self):
        p = self.project(
            "/p", ("a", "keep-a"), ("x", "hidden"), ("b", "keep-b")
        )
        q = self.project("/q", ("c", "hidden-c"))
        rows = self.drawn([p, q], flt="keep")
        self.assertEqual(
            [panel.row_key(r) for r in rows],
            [("group", "/p"), ("session", "a", "/p"),
             ("session", "b", "/p")],
        )
        self.assertEqual(self.delete([p, q], rows, "a"), ("session", "b", "/p"))

    def test_D_wiring_lands_the_cursor_in_place(self):
        p = self.project("/p", ("a", "a"), ("b", "b"))
        app = panel.main.App(mock.Mock(), "%1")
        app.groups = [p]

        def echo_cursor(_stdscr, _groups, _flt, cursor, _offset, *_args):
            return (cursor, 0, {})

        with mock.patch.object(panel.view, "draw", side_effect=echo_cursor):
            app.draw()
        app.cursor = next(
            i for i, row in enumerate(app.rows)
            if panel.row_key(row) == ("session", "a", "/p")
        )
        app.selected = panel.row_key(app.rows[app.cursor])
        after = [panel.Group(cwd="/p", sessions=[p.sessions[1]], goals=[])]
        with mock.patch.object(
            panel.view, "draw", side_effect=echo_cursor
        ), mock.patch.object(
            panel.trash, "delete_session", return_value=(True, "deleted")
        ), mock.patch.object(
            panel.status, "load_groups", return_value=after
        ):
            app.stdscr.getch.return_value = ord("y")
            self.assertTrue(app.dispatch_key(ord("D"), {}))
        with mock.patch.object(panel.view, "draw", side_effect=echo_cursor):
            app.draw()
        self.assertEqual(
            panel.row_key(app.rows[app.cursor]), ("session", "b", "/p")
        )
        self.assertEqual(app.message.strip(), "deleted")
