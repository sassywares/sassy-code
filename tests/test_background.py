#!/usr/bin/env python3
"""Background-session lifecycle tests against an isolated tmux server.

No test talks to the user's tmux server: every case starts its own `tmux -L`
server with `-f /dev/null` and a fake `pi` executable on PATH.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from tests.panel_loader import ROOT, load_panel

panel = load_panel()


SESSION_NAME = "sassy-code"  # the dedicated session the launcher owns


class BackgroundLifecycleTests(unittest.TestCase):
    counter = 0

    def setUp(self):
        BackgroundLifecycleTests.counter += 1
        self.tmp = Path(tempfile.mkdtemp(prefix="sassy-code-test-"))
        self.sessions = self.tmp / "sessions"
        (self.sessions / "project").mkdir(parents=True)
        self.claude_sessions = self.tmp / "claude-projects"
        self.claude_sessions.mkdir()
        self.fake_bin = self.tmp / "bin"
        self.fake_bin.mkdir()
        self.fake_pi = self.fake_bin / "pi"
        self.fake_pi.write_text(
            "#!/usr/bin/env bash\n"
            "sid=''\n"
            "while [ $# -gt 0 ]; do\n"
            "  case \"$1\" in\n"
            "    --session) sid=\"$2\"; shift 2 ;;\n"
            "    --session=*) sid=\"${1#*=}\"; shift ;;\n"
            "    --session-id) sid=\"$2\"; shift 2 ;;\n"
            "    --session-id=*) sid=\"${1#*=}\"; shift ;;\n"
            "    *) shift ;;\n"
            "  esac\n"
            "done\n"
            "trap 'exit 0' TERM INT HUP\n"
            "while :; do sleep 1; done\n"
        )
        self.fake_pi.chmod(0o755)

        # A Claude Code stand-in that understands the resume/new flags.
        self.fake_claude = self.fake_bin / "claude"
        self.fake_claude.write_text(
            "#!/usr/bin/env bash\n"
            "sid=''\n"
            "while [ $# -gt 0 ]; do\n"
            "  case \"$1\" in\n"
            "    --resume|-r) sid=\"$2\"; shift 2 ;;\n"
            "    --resume=*) sid=\"${1#*=}\"; shift ;;\n"
            "    --session-id) sid=\"$2\"; shift 2 ;;\n"
            "    --session-id=*) sid=\"${1#*=}\"; shift ;;\n"
            "    *) shift ;;\n"
            "  esac\n"
            "done\n"
            "trap 'exit 0' TERM INT HUP\n"
            "while :; do sleep 1; done\n"
        )
        self.fake_claude.chmod(0o755)

        self.old_env = {
            key: os.environ.get(key)
            for key in ("TMUX", "TMUX_PANE", "PATH")
        }
        self.procs = []
        self.label = f"sassy-test-{os.getpid()}-{self.counter}"
        self.start_server()
        self.panel_id = self.tmux(
            "list-panes", "-t", "vis", "-F", "#{pane_id}"
        ).splitlines()[0]
        self.original_viewer = self.tmux(
            "split-window", "-h", "-t", self.panel_id,
            "-P", "-F", "#{pane_id}",
        ).strip()
        socket = self.tmux("display", "-p", "#{socket_path}").strip()
        self.socket = socket
        os.environ["TMUX"] = socket
        os.environ["TMUX_PANE"] = self.panel_id
        os.environ["PATH"] = f"{self.fake_bin}:{self.old_env['PATH'] or ''}"
        self.tmux("set-environment", "-g", "PATH", os.environ["PATH"])
        panel.PI_SESSIONS_DIR = str(self.sessions)
        panel.CLAUDE_SESSIONS_DIR = str(self.claude_sessions)
        patcher = mock.patch.object(panel, "STATE_DIR", str(self.tmp / "state"))
        patcher.start()
        self.addCleanup(patcher.stop)
        panel.LEGACY_STATE_FILE = str(self.tmp / "legacy-current")
        panel.REAP_GRACE = 0.2

    def tearDown(self):
        for proc in self.procs:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
        self.tmux("kill-server")
        for key, value in self.old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -- helpers ---------------------------------------------------------

    def tmux_env(self):
        env = os.environ.copy()
        env.pop("TMUX", None)
        env.pop("TMUX_PANE", None)
        env["PATH"] = f"{self.fake_bin}:{env.get('PATH', '')}"
        return env

    def tmux(self, *args: str) -> str:
        return subprocess.run(
            ["tmux", "-L", self.label, *args],
            capture_output=True,
            text=True,
            check=True,
            env=self.tmux_env(),
        ).stdout

    def start_server(self):
        subprocess.run(
            ["tmux", "-L", self.label, "-f", "/dev/null",
             "new-session", "-d", "-s", "vis", "-n", "win"],
            check=True,
            env=self.tmux_env(),
        )

    def utc_iso(self, offset: float = 0.0) -> str:
        """Pi-style UTC timestamp, so ordering is explicit in seconds."""
        stamp = datetime.now(timezone.utc) + timedelta(seconds=offset)
        return stamp.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

    def write_session(
        self, sid: str, stop: str = "toolUse", cwd: str = "",
        parent: str = "", offset: float = 0.0,
    ):
        ts = self.utc_iso(offset)
        cwd = cwd or str(self.tmp)
        path = self.sessions / "project" / f"{sid}.jsonl"
        header = {"type": "session", "id": sid, "cwd": cwd, "timestamp": ts}
        if parent:
            header["parentSession"] = str(parent)
        lines = [
            header,
            {"type": "message", "timestamp": ts,
             "message": {"role": "user",
                         "content": [{"type": "text", "text": sid}]}},
            {"type": "message", "timestamp": ts,
             "message": {"role": "assistant",
                         "content": [{"type": "text", "text": "working"}],
                         "stopReason": stop}},
        ]
        path.write_text("".join(json.dumps(o) + "\n" for o in lines))
        return path

    def append_stop(self, sid: str, stop: str = "stop"):
        path = self.sessions / "project" / f"{sid}.jsonl"
        ts = self.utc_iso()
        entry = {"type": "message", "timestamp": ts,
                 "message": {"role": "assistant",
                             "content": [{"type": "text", "text": "done"}],
                             "stopReason": stop}}
        with path.open("a") as f:
            f.write(json.dumps(entry) + "\n")

    def write_claude_session(
        self, sid: str, stop: str = "tool_use", cwd: str = "",
        offset: float = 0.0, error: bool = False,
    ):
        ts = self.utc_iso(offset)
        cwd = cwd or str(self.tmp)
        project = self.claude_sessions / (
            "-" + cwd.strip("/").replace("/", "-")
        )
        project.mkdir(parents=True, exist_ok=True)
        path = project / f"{sid}.jsonl"
        common = {"cwd": cwd, "sessionId": sid, "timestamp": ts}
        user = dict(common, type="user", origin={"kind": "human"},
                    message={"role": "user", "content": sid})
        assistant = dict(
            common, type="assistant", isApiErrorMessage=error,
            message={"role": "assistant", "stop_reason": stop,
                     "content": [{"type": "text", "text": "working"}]},
        )
        path.write_text(
            "".join(json.dumps(o) + "\n" for o in (user, assistant))
        )
        return path

    def append_claude_stop(self, sid: str, stop: str = "end_turn"):
        path = next(self.claude_sessions.glob(f"*/{sid}.jsonl"))
        ts = self.utc_iso()
        entry = {
            "cwd": str(self.tmp), "sessionId": sid, "timestamp": ts,
            "type": "assistant",
            "message": {"role": "assistant", "stop_reason": stop,
                        "content": [{"type": "text", "text": "done"}]},
        }
        with path.open("a") as f:
            f.write(json.dumps(entry) + "\n")

    def group_session(self, sid: str):
        groups = panel.load_groups()
        return groups, panel.session_index(groups)[sid]

    def panes(self):
        panel_id = panel.panel_pane_id()
        return panel.layout_panes(panel.panel_window_id(panel_id))

    def visible_viewer(self):
        panel_id = panel.panel_pane_id()
        return panel.viewer_pane_id(
            panel_id, panel.panel_window_id(panel_id), self.panes()
        )

    def pane_by_id(self, pane_id: str):
        return next((p for p in self.panes() if p.id == pane_id), None)

    def present(self, sid: str, state: dict[str, float] | None = None):
        groups, s = self.group_session(sid)
        return panel.present_session(
            s, groups, self.original_viewer, {} if state is None else state
        )

    def hide(self, sid: str, next_sid: str):
        """Present sid, then next_sid; return sid's hidden pane and PID."""
        self.assertIsNone(self.present(sid))
        pane_id = panel.pane_map(self.panes())[sid]
        pid = self.pi_pid_in(pane_id)
        self.assertIsNotNone(pid)
        self.assertIsNone(self.present(next_sid))
        hidden = self.pane_by_id(pane_id)
        self.assertIsNotNone(hidden)
        self.assertEqual(hidden.session_name, panel.BG_SESSION)
        self.assertEqual(hidden.tag, sid)
        return pane_id, pid

    def reap(self, state: dict[str, float]):
        panel.reap_hidden(
            self.panes(), panel.load_groups(), panel.process_table(), state
        )

    def run_panel(self, *args: str):
        env = os.environ.copy()
        env["TMUX"] = self.socket
        return subprocess.run(
            [sys.executable, str(ROOT / "panel.py"), *args],
            capture_output=True,
            text=True,
            env=env,
            timeout=10,
        )

    def key_listener(self, marker: Path):
        code = (
            "import sys,tty\n"
            "tty.setraw(0)\n"
            "open(sys.argv[1], 'w').write(repr(sys.stdin.read(1)))\n"
        )
        return [sys.executable, "-c", code, str(marker)]

    def wait_for_file(self, path: Path, timeout: float = 3.0) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if path.exists() and path.read_text():
                return True
            time.sleep(0.02)
        return False

    def pi_pid_in(self, pane_id: str):
        return self.agent_pid_in(pane_id, "pi")

    def agent_pid_in(self, pane_id: str, harness: str):
        pane = self.pane_by_id(pane_id)
        if not pane:
            return None
        # The pane's root is a shell whose command text mentions the agent; pick
        # the actual descendant deterministically instead of a set-order match.
        agents = sorted(
            (
                proc
                for proc in panel.pane_processes(pane, panel.process_table())
                if panel.command_harness(proc.command) == harness
            ),
            key=lambda proc: proc.pid,
        )
        return agents[0].pid if agents else None

    # -- tests -----------------------------------------------------------

    def test_switching_preserves_working_process_and_switches_back(self):
        self.write_session("aaa", "toolUse")
        self.write_session("bbb", "toolUse")
        a_pane, a_pid = self.hide("aaa", "bbb")

        self.assertIsNone(self.present("aaa"))
        shown = self.pane_by_id(a_pane)
        self.assertIsNotNone(shown)
        self.assertNotEqual(shown.session_name, panel.BG_SESSION)
        self.assertEqual(shown.tag, "aaa")
        self.assertEqual(self.pi_pid_in(a_pane), a_pid)
        self.assertEqual(
            len([p for p in self.panes() if p.tag == "aaa"]), 1,
            "re-selecting a live session must not spawn a duplicate",
        )

    def test_completed_session_is_reaped_after_grace_when_switched_away(self):
        self.write_session("done", "stop")
        self.write_session("next", "toolUse")
        self.assertIsNone(self.present("done"))
        done_pane = panel.pane_map(self.panes())["done"]

        state = {}
        self.assertIsNone(self.present("next", state))
        self.assertIsNotNone(
            self.pane_by_id(done_pane),
            "a live displaced pane must not be killed from a stale completion",
        )
        time.sleep(panel.REAP_GRACE + 0.25)
        self.reap(state)
        self.assertIsNone(
            self.pane_by_id(done_pane),
            "a completed hidden session is reaped after the refresh grace",
        )

    def test_working_hidden_session_survives_until_completion(self):
        self.write_session("aaa", "toolUse")
        self.write_session("bbb", "toolUse")
        a_pane, _ = self.hide("aaa", "bbb")

        state = {}
        self.reap(state)
        time.sleep(0.05)
        self.reap(state)
        self.assertIsNotNone(
            self.pane_by_id(a_pane),
            "an unfinished hidden session must not be reaped",
        )

        self.append_stop("aaa", "stop")
        self.reap(state)
        self.assertIsNotNone(
            self.pane_by_id(a_pane),
            "a completed hidden session gets a refresh grace period",
        )
        self.assertIn(a_pane, state)
        time.sleep(panel.REAP_GRACE + 0.25)
        self.reap(state)
        self.assertIsNone(
            self.pane_by_id(a_pane),
            "a completed hidden session is reaped after the grace period",
        )

    def test_hidden_pi_exit_removes_orphan_shell(self):
        self.write_session("aaa", "toolUse")
        self.write_session("bbb", "toolUse")
        a_pane, _ = self.hide("aaa", "bbb")
        pid = self.pi_pid_in(a_pane)
        self.assertIsNotNone(pid)
        os.kill(pid, 15)
        deadline = time.time() + 3
        while self.pi_pid_in(a_pane) and time.time() < deadline:
            time.sleep(0.05)
        self.assertIsNone(self.pi_pid_in(a_pane))
        self.reap({})
        self.assertIsNone(self.pane_by_id(a_pane))

    def test_external_process_refused_without_duplicate(self):
        self.write_session("outside", "toolUse")
        env = os.environ.copy()
        env["PATH"] = f"{self.fake_bin}:{env.get('PATH', '')}"
        proc = subprocess.Popen(
            [str(self.fake_pi), "--session", "outside"],
            env=env,
            start_new_session=True,
        )
        self.procs.append(proc)
        time.sleep(0.2)

        before = self.visible_viewer()
        groups, s = self.group_session("outside")
        warning = panel.present_session(s, groups, self.original_viewer, {})
        self.assertIsNotNone(warning)
        self.assertIn("outside", warning.lower())
        self.assertEqual(self.visible_viewer(), before)
        self.assertFalse(any(p.tag == "outside" for p in self.panes()))
        self.assertTrue(
            any(
                proc.pid in pids
                for sid, pids in panel.running_session_processes().items()
                if sid == "outside"
            ),
            "the external process should remain the only running copy",
        )

    def test_pane_option_mapping_and_adoption_after_restart(self):
        self.write_session("aaa", "toolUse")
        self.write_session("bbb", "toolUse")
        a_pane, _ = self.hide("aaa", "bbb")
        self.assertEqual(
            panel.pane_map(self.panes())["aaa"], a_pane,
            "a hidden pane keeps its session option across swaps",
        )
        self.assertIsNone(self.present("aaa"))
        self.assertEqual(panel.pane_map(self.panes())["aaa"], a_pane)
        self.assertEqual(len([p for p in self.panes() if p.tag == "aaa"]), 1)

    def test_handoff_follows_successor_without_spawning_a_duplicate(self):
        self.write_session("old", "stop", offset=-60)
        self.assertIsNone(self.present("old"))
        old_pane = panel.pane_map(self.panes())["old"]
        old_pid = self.pi_pid_in(old_pane)

        # `/handoff` replaces the session inside the same Pi process.
        self.write_session(
            "new", "toolUse", offset=0,
            parent=self.sessions / "project" / "old.jsonl",
        )
        sessions = panel.session_index(panel.load_groups())
        self.assertEqual(sessions["new"].status, "running")
        self.assertNotEqual(sessions["old"].status, "running")

        # The successor is already in the viewer: Enter must focus it, not
        # start a second Pi for the same session.
        self.assertIsNone(self.present("new"))
        panes = [p for p in self.panes() if p.tag in ("old", "new")]
        self.assertEqual(len(panes), 1)
        self.assertEqual(panes[0].tag, "new")
        self.assertEqual(self.pi_pid_in(old_pane), old_pid)

        sessions = panel.session_index(panel.load_groups())
        self.assertEqual(sessions["new"].status, "running")
        self.assertNotEqual(sessions["old"].status, "running")
        # The highlight tracks the retagged viewer pane, not the replaced row.
        _, panes, viewer = panel.visible_layout(
            panel.load_groups(), self.original_viewer
        )
        key, presented = panel.follow_viewer_switch(
            ("session", "old", str(self.tmp)), "old", panes,
            panel.load_groups(), viewer,
        )
        self.assertEqual(key, ("session", "new", str(self.tmp)))
        self.assertEqual(presented, "new")

    def test_second_handoff_from_a_child_follows_the_newest_sibling(self):
        # The viewer's Pi was started with `root`; every handoff inside it may
        # record `root` as the parent, so its children are siblings, not a
        # chain. The pane must still move to the newest one.
        self.write_session("root", "stop", offset=-600)
        self.assertIsNone(self.present("root"))
        pane_id = panel.pane_map(self.panes())["root"]

        self.write_session(
            "first", "toolUse", offset=-300,
            parent=self.sessions / "project" / "root.jsonl",
        )
        panel.visible_layout(panel.load_groups(), self.original_viewer)
        self.assertEqual(panel.pane_map(self.panes())["first"], pane_id)

        self.write_session(
            "second", "toolUse", offset=0,
            parent=self.sessions / "project" / "root.jsonl",
        )
        groups = panel.load_groups()
        sessions = panel.session_index(groups)
        self.assertEqual(sessions["second"].status, "running")
        self.assertNotEqual(sessions["first"].status, "running")
        self.assertNotEqual(sessions["root"].status, "running")

        _, panes, viewer = panel.visible_layout(groups, self.original_viewer)
        self.assertEqual(panel.pane_map(panes)["second"], pane_id)
        key, presented = panel.follow_viewer_switch(
            ("session", "first", str(self.tmp)), "first", panes, groups, viewer,
        )
        self.assertEqual(key, ("session", "second", str(self.tmp)))
        self.assertEqual(presented, "second")

    def test_adoption_after_restart_follows_handoff_to_successor(self):
        self.write_session("old", "stop", offset=-60)
        self.assertIsNone(self.present("old"))
        pane_id = panel.pane_map(self.panes())["old"]
        self.tmux("set-option", "-p", "-u", "-t", pane_id,
                  panel.PANE_OPTION)

        self.write_session(
            "new", "toolUse", offset=0,
            parent=self.sessions / "project" / "old.jsonl",
        )
        groups = panel.load_groups()
        panel.visible_layout(groups, self.original_viewer)
        self.assertEqual(panel.pane_map(self.panes())["new"], pane_id)

    def test_reopening_a_replaced_session_starts_a_separate_pane(self):
        self.write_session("old", "stop", offset=-60)
        self.assertIsNone(self.present("old"))
        child_pane = panel.pane_map(self.panes())["old"]
        self.write_session(
            "new", "toolUse", offset=0,
            parent=self.sessions / "project" / "old.jsonl",
        )
        panel.visible_layout(panel.load_groups(), self.original_viewer)
        self.assertEqual(panel.pane_map(self.panes())["new"], child_pane)

        # The pane now runs the successor. Asking for the replaced session must
        # start it afresh instead of relabelling the live successor's pane.
        self.assertIsNone(self.present("old"))
        tags = panel.pane_map(self.panes())
        self.assertEqual(tags["new"], child_pane)
        self.assertNotEqual(tags.get("old"), child_pane)
        sessions = panel.session_index(panel.load_groups())
        self.assertEqual(sessions["old"].status, "running")
        self.assertEqual(sessions["new"].status, "running")

    def test_new_session_preserves_previous_working_session(self):
        self.write_session("old", "toolUse")
        self.assertIsNone(self.present("old"))
        old_pane = panel.pane_map(self.panes())["old"]
        old_pid = self.pi_pid_in(old_pane)

        groups = panel.load_groups()
        self.assertIsNone(
            panel.new_session(
                self.original_viewer, str(self.tmp), groups, {}
            )
        )
        hidden = self.pane_by_id(old_pane)
        self.assertIsNotNone(hidden)
        self.assertEqual(hidden.session_name, panel.BG_SESSION)
        self.assertEqual(self.pi_pid_in(old_pane), old_pid)

        viewer = self.visible_viewer()
        self.assertNotEqual(viewer, old_pane)
        tag = self.pane_by_id(viewer).tag
        self.assertTrue(tag and tag != "old", "new pane should be tagged")

    def test_claude_session_presents_with_resume_and_stays_running(self):
        sid = "11111111-2222-3333-4444-555555555555"
        self.write_claude_session(sid, "tool_use")
        groups, s = self.group_session(sid)
        self.assertEqual(s.harness, "claude")

        self.assertIsNone(self.present(sid))
        pane_id = panel.pane_map(self.panes())[sid]
        _, running = self.group_session(sid)
        self.assertEqual(running.status, "running")
        pid = self.agent_pid_in(pane_id, "claude")
        self.assertIsNotNone(pid)
        command = next(
            proc.command
            for proc in panel.pane_processes(
                self.pane_by_id(pane_id), panel.process_table()
            )
            if proc.pid == pid
        )
        self.assertIn("--resume", command)
        self.assertIn(sid, command)
        self.assertEqual(panel.pane_running_session(
            self.pane_by_id(pane_id), panel.process_table()
        ), sid)

    def test_new_session_can_start_a_claude_session(self):
        groups = panel.load_groups()
        self.assertIsNone(
            panel.new_session(
                self.original_viewer, str(self.tmp), groups, {}, "claude"
            )
        )
        viewer = self.visible_viewer()
        tag = self.pane_by_id(viewer).tag
        self.assertTrue(tag)
        pid = self.agent_pid_in(viewer, "claude")
        self.assertIsNotNone(pid, "a new Claude session should be spawned")
        command = next(
            proc.command
            for proc in panel.pane_processes(
                self.pane_by_id(viewer), panel.process_table()
            )
            if proc.pid == pid
        )
        self.assertIn("--session-id", command)
        self.assertIn(tag, command)

    def test_new_session_in_a_goal_nests_for_both_harnesses(self):
        goal = panel.create_goal(str(self.tmp), "phase work")
        for harness in ("pi", "claude"):
            groups = panel.load_groups()
            self.assertIsNone(
                panel.new_session(
                    self.original_viewer, str(self.tmp), groups, {},
                    harness, goal.id,
                )
            )
            tag = self.pane_by_id(self.visible_viewer()).tag
            ids = panel.load_goals()[0][str(self.tmp)][0].sessions
            self.assertIn(tag, ids)
            self.assertIsNotNone(self.agent_pid_in(self.visible_viewer(), harness))

    def goal_session_ids(self):
        try:
            data = json.loads((self.panel_state / "goals.json").read_text())
        except (OSError, ValueError):
            return []
        goals = data["projects"].get(str(self.tmp), [])
        return goals[0]["sessions"] if goals else []

    def wait_until(self, check, timeout: float = 8.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            value = check()
            if value:
                return value
            time.sleep(0.1)
        return check()

    def test_new_session_in_goal_via_n_and_the_shortcut_path(self):
        # The panel process resolves its state dir from HOME.
        self.panel_state = self.tmp / ".sassywares" / "sassy-code"
        patcher = mock.patch.object(panel, "STATE_DIR", str(self.panel_state))
        patcher.start()
        self.addCleanup(patcher.stop)
        panel._goals_cache = None
        self.addCleanup(setattr, panel, "_goals_cache", None)
        self.write_session("seed", "stop", offset=-60)
        win = self.tmux(
            "new-window", "-d", "-P", "-F", "#{window_id}",
            "-n", "e2e", "sleep", "60",
        ).strip()
        viewer = self.tmux(
            "list-panes", "-t", win, "-F", "#{pane_id}"
        ).splitlines()[0]
        panel_pane = self.tmux(
            "split-window", "-h", "-b", "-t", win,
            "-P", "-F", "#{pane_id}",
            "env", f"HOME={self.tmp}",
            f"PI_CODING_AGENT_SESSION_DIR={self.sessions}",
            f"SASSY_CODE_CLAUDE_SESSION_DIR={self.claude_sessions}",
            "SASSY_CODE_NEW_HARNESS=pi",
            sys.executable, str(ROOT / "panel.py"), "--target", viewer,
        ).strip()
        os.environ["TMUX_PANE"] = panel_pane

        def screen():
            return self.tmux("capture-pane", "-p", "-t", panel_pane)

        def keys(*args: str):
            self.tmux("send-keys", "-t", panel_pane, *args)

        self.assertTrue(
            self.wait_until(lambda: "seed" in screen()), screen()
        )
        # `:goal:create` lands the cursor on the new goal; `n` starts in it.
        keys(":")
        keys("-l", "goal:create Phase work")
        keys("Enter")
        goals_file = self.panel_state / "goals.json"
        self.assertTrue(
            self.wait_until(
                lambda: goals_file.exists() and "(0)" in screen()
            ),
            screen(),
        )
        time.sleep(0.5)  # let the cursor land on the goal row
        keys("-l", "n")  # a goal row asks which harness; Enter keeps Pi
        self.assertTrue(
            self.wait_until(lambda: "Claude Code" in screen()), screen()
        )
        keys("Enter")
        first = self.wait_until(self.goal_session_ids)
        self.assertEqual(len(first), 1, "n on the goal row must file the id")
        first_id = first[0]

        def agent_pane(sid):
            def find():
                pane = next((p for p in self.panes() if p.tag == sid), None)
                if pane and self.agent_pid_in(pane.id, "pi"):
                    return pane
            return self.wait_until(find)

        self.assertIsNotNone(agent_pane(first_id), "no fake agent running")

        # Once the session file exists it nests under the goal; step onto it.
        self.write_session(first_id, "toolUse", offset=-5)
        self.assertTrue(
            self.wait_until(lambda: first_id[:6] in screen(), 6), screen()
        )
        keys("Down")
        time.sleep(0.3)
        shown_viewer = next(
            p.id for p in self.panes()
            if p.tag == first_id and p.session_name != panel.BG_SESSION
        )

        # The N shortcut path: the panel receives `n` on a goal member.
        result = self.run_panel("--new-session", "--pane", shown_viewer)
        self.assertEqual(result.returncode, 0, result.stderr)
        ids = self.wait_until(
            lambda: len(self.goal_session_ids()) == 2
            and self.goal_session_ids()
        )
        self.assertEqual(ids[0], first_id)
        second_id = ids[1]
        self.assertIsNotNone(agent_pane(second_id), "no fake agent running")

        self.write_session(second_id, "toolUse", offset=-1)
        rows = list(panel.rows_for(panel.load_groups(), ""))
        kinds = [(k, getattr(x, "id", None)) for k, _g, x in rows]
        at = kinds.index(("goal", panel.load_goals()[0][str(self.tmp)][0].id))
        self.assertEqual(
            {sid for _k, sid in kinds[at + 1:at + 3]}, {first_id, second_id}
        )
        self.assertEqual(kinds[at + 3], ("session", "seed"))

    def test_harness_picker_on_project_heading_and_n_shortcut_skips_it(self):
        self.panel_state = self.tmp / ".sassywares" / "sassy-code"
        self.write_session("seed", "stop", offset=-60)
        win = self.tmux(
            "new-window", "-d", "-P", "-F", "#{window_id}",
            "-n", "pick", "sleep", "60",
        ).strip()
        viewer = self.tmux(
            "list-panes", "-t", win, "-F", "#{pane_id}"
        ).splitlines()[0]
        panel_pane = self.tmux(
            "split-window", "-h", "-b", "-t", win,
            "-P", "-F", "#{pane_id}",
            "env", f"HOME={self.tmp}",
            f"PI_CODING_AGENT_SESSION_DIR={self.sessions}",
            f"SASSY_CODE_CLAUDE_SESSION_DIR={self.claude_sessions}",
            "SASSY_CODE_NEW_HARNESS=pi",
            sys.executable, str(ROOT / "panel.py"), "--target", viewer,
        ).strip()
        os.environ["TMUX_PANE"] = panel_pane

        def screen():
            return self.tmux("capture-pane", "-p", "-t", panel_pane)

        def keys(*args: str):
            self.tmux("send-keys", "-t", panel_pane, *args)

        def tagged():
            return {p.tag for p in self.panes() if p.tag}

        def agent_for(harness, known):
            def find():
                for p in self.panes():
                    if p.tag and p.tag not in known and \
                            self.agent_pid_in(p.id, harness):
                        return p
            return self.wait_until(find)

        self.assertTrue(self.wait_until(lambda: "seed" in screen()), screen())
        self.assertEqual(tagged(), set())

        # `n` on the project heading opens the box, preselected on Pi.
        keys("-l", "n")
        self.assertTrue(
            self.wait_until(lambda: "Claude Code" in screen()), screen()
        )
        self.assertIn("┌", screen())
        self.assertIn("π Pi", screen())
        # Esc creates nothing.
        keys("Escape")
        self.assertTrue(
            self.wait_until(lambda: "Claude Code" not in screen(), 6), screen()
        )
        time.sleep(0.5)
        self.assertEqual(tagged(), set())

        # j + Enter picks the non-default harness.
        keys("-l", "n")
        self.assertTrue(
            self.wait_until(lambda: "Claude Code" in screen()), screen()
        )
        keys("-l", "j")
        keys("Enter")
        claude = agent_for("claude", set())
        self.assertIsNotNone(claude, screen())
        self.assertIsNone(self.agent_pid_in(claude.id, "pi"))
        self.assertNotIn("Claude Code", screen())

        # A digit picks directly.
        known = tagged()
        keys("-l", "n")
        self.assertTrue(
            self.wait_until(lambda: "Claude Code" in screen()), screen()
        )
        keys("-l", "1")
        self.assertIsNotNone(agent_for("pi", known), screen())

        # The prefix shortcut path never opens the picker: the project's
        # newest session (the pi one) decides the harness.
        known = tagged()
        result = self.run_panel(
            "--new-session", "--pane", self.visible_viewer()
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIsNotNone(agent_for("pi", known), screen())
        self.assertNotIn("Claude Code", screen())

    def test_goal_done_and_delete_keys(self):
        self.panel_state = self.tmp / ".sassywares" / "sassy-code"
        patcher = mock.patch.object(panel, "STATE_DIR", str(self.panel_state))
        patcher.start()
        self.addCleanup(patcher.stop)
        panel._goals_cache = None
        self.addCleanup(setattr, panel, "_goals_cache", None)
        member = self.write_session("member1", "stop", offset=-10)
        goal = panel.create_goal(str(self.tmp), "Phase work")
        panel.file_session_in_goal(str(self.tmp), goal.id, "member1")
        before = member.read_bytes()
        win = self.tmux(
            "new-window", "-d", "-P", "-F", "#{window_id}",
            "-n", "e2e", "sleep", "60",
        ).strip()
        viewer = self.tmux(
            "list-panes", "-t", win, "-F", "#{pane_id}"
        ).splitlines()[0]
        panel_pane = self.tmux(
            "split-window", "-h", "-b", "-t", win,
            "-P", "-F", "#{pane_id}",
            "env", f"HOME={self.tmp}",
            f"PI_CODING_AGENT_SESSION_DIR={self.sessions}",
            f"SASSY_CODE_CLAUDE_SESSION_DIR={self.claude_sessions}",
            "SASSY_CODE_NEW_HARNESS=pi",
            sys.executable, str(ROOT / "panel.py"), "--target", viewer,
        ).strip()

        def screen():
            return self.tmux("capture-pane", "-p", "-t", panel_pane)

        def keys(*args: str):
            self.tmux("send-keys", "-t", panel_pane, *args)

        def record():
            try:
                data = json.loads((self.panel_state / "goals.json").read_text())
            except (OSError, ValueError):
                return None
            goals = data["projects"].get(str(self.tmp), [])
            return goals[0] if goals else None

        def flags_are(done, collapsed):
            rec = record()
            return bool(rec) and (rec["done"], rec["collapsed"]) == (
                done, collapsed
            )

        self.assertTrue(
            self.wait_until(lambda: "Phase work" in screen()), screen()
        )
        keys("Down")  # onto the goal row, below the project heading
        keys("-l", "x")
        self.assertTrue(self.wait_until(lambda: flags_are(True, True)), screen())
        keys("-l", "x")
        self.assertTrue(
            self.wait_until(lambda: flags_are(False, False)), screen()
        )

        keys("-l", "D")
        self.assertTrue(
            self.wait_until(lambda: "Delete goal" in screen()), screen()
        )
        keys("-l", "n")
        self.assertTrue(
            self.wait_until(lambda: "Delete goal" not in screen()), screen()
        )
        self.assertIsNotNone(record())

        keys("-l", "D")
        self.assertTrue(
            self.wait_until(lambda: "Delete goal" in screen()), screen()
        )
        keys("-l", "y")
        self.assertTrue(self.wait_until(lambda: record() is None), screen())

        self.assertEqual(member.read_bytes(), before)
        rows = list(panel.rows_for(panel.load_groups(), ""))
        self.assertIn(
            ("session", "member1"),
            [(k, getattr(x, "id", None)) for k, _g, x in rows],
        )
        self.assertNotIn("goal", [k for k, _g, _x in rows])

    def test_external_session_id_process_refused_without_duplicate(self):
        sid = "outside-new"
        self.write_session(sid, "toolUse")
        env = os.environ.copy()
        env["PATH"] = f"{self.fake_bin}:{env.get('PATH', '')}"
        proc = subprocess.Popen(
            [str(self.fake_pi), "--session-id", sid],
            env=env,
            start_new_session=True,
        )
        self.procs.append(proc)
        time.sleep(0.2)

        running = panel.running_session_processes()
        self.assertIn(sid, running)
        self.assertIn(proc.pid, running[sid])

        groups, s = self.group_session(sid)
        self.assertEqual(s.status, "running")
        before = self.visible_viewer()
        warning = panel.present_session(s, groups, self.original_viewer, {})
        self.assertIsNotNone(warning)
        self.assertIn(sid, warning.lower())
        self.assertEqual(self.visible_viewer(), before)
        self.assertFalse(any(p.tag == sid for p in self.panes()))

    def test_new_session_shortcut_asks_the_panel_without_moving_focus(self):
        marker = self.tmp / "shortcut-key"
        win = self.tmux(
            "new-window", "-d", "-P", "-F", "#{window_id}",
            "-n", "shortcut", "sleep", "30",
        ).strip()
        viewer = self.tmux(
            "list-panes", "-t", win, "-F", "#{pane_id}"
        ).splitlines()[0]
        panel_pane = self.tmux(
            "split-window", "-h", "-b", "-t", win,
            "-P", "-F", "#{pane_id}", *self.key_listener(marker),
        ).strip()
        self.tmux(
            "set-option", "-p", "-t", panel_pane,
            panel.PANEL_OPTION, "1",
        )
        self.tmux("select-pane", "-t", viewer)

        result = self.run_panel("--new-session", "--pane", viewer)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.wait_for_file(marker), "panel never received `n`")
        self.assertEqual(marker.read_text(), repr("N"))
        active = [
            line.split()[0]
            for line in self.tmux(
                "list-panes", "-t", win, "-F", "#{pane_id} #{pane_active}"
            ).splitlines()
            if line.split()[1] == "1"
        ]
        self.assertEqual(active, [viewer], "the shortcut must not move focus")

    def test_new_session_shortcut_ignores_a_stale_panel_tag(self):
        win = self.tmux(
            "new-window", "-d", "-P", "-F", "#{window_id}",
            "-n", "stale", "sleep", "30",
        ).strip()
        pane = self.tmux(
            "list-panes", "-t", win, "-F", "#{pane_id}"
        ).splitlines()[0]
        # A tag on a non-Python pane is a leftover, not a control panel.
        self.tmux("set-option", "-p", "-t", pane, panel.PANEL_OPTION, "1")

        result = self.run_panel("--new-session", "--pane", pane)

        self.assertNotEqual(result.returncode, 0)

    # -- quit paths --------------------------------------------------------

    def dedicated_panel(self, goal: bool = False):
        """Run the real panel inside a session named `sassy-code`."""
        self.panel_state = self.tmp / ".sassywares" / "sassy-code"
        patcher = mock.patch.object(panel, "STATE_DIR", str(self.panel_state))
        patcher.start()
        self.addCleanup(patcher.stop)
        panel._goals_cache = None
        self.addCleanup(setattr, panel, "_goals_cache", None)
        self.write_session("seed", "stop", offset=-60)
        if goal:
            made = panel.create_goal(str(self.tmp), "Phase work")
            panel.file_session_in_goal(str(self.tmp), made.id, "seed")
        self.tmux(
            "new-session", "-d", "-s", SESSION_NAME, "-x", "120", "-y", "40",
            "-n", "main", "sleep", "120",
        )
        viewer = self.tmux(
            "list-panes", "-t", f"={SESSION_NAME}", "-F", "#{pane_id}"
        ).splitlines()[0]
        panel_pane = self.tmux(
            "split-window", "-h", "-b", "-t", viewer, "-P", "-F", "#{pane_id}",
            "env", f"HOME={self.tmp}", f"PATH={os.environ['PATH']}",
            f"PI_CODING_AGENT_SESSION_DIR={self.sessions}",
            f"SASSY_CODE_CLAUDE_SESSION_DIR={self.claude_sessions}",
            "SASSY_CODE_NEW_HARNESS=pi",
            sys.executable, str(ROOT / "panel.py"), "--target", viewer,
        ).strip()
        self.assertTrue(
            self.wait_until(
                lambda: "seed" in self.tmux("capture-pane", "-p", "-t", panel_pane)
                or "Phase work" in self.tmux("capture-pane", "-p", "-t", panel_pane)
            )
        )
        return viewer, panel_pane

    def agent_window(self, sid: str) -> str:
        """A tagged live Pi in an extra window of the dedicated session."""
        pane = self.tmux(
            "new-window", "-d", "-t", f"={SESSION_NAME}:",
            "-P", "-F", "#{pane_id}", "-n", f"agent-{sid}",
            f"pi --session-id {sid}; exec sh",
        ).strip()
        self.tmux("set-option", "-p", "-t", pane, panel.PANE_OPTION, sid)
        self.assertTrue(self.wait_until(lambda: self.pi_pid_in_any(pane)))
        return pane

    def pi_pid_in_any(self, pane_id: str):
        pane = next(
            (p for p in panel.parse_panes(self.tmux(
                "list-panes", "-a", "-F", panel.PANE_FORMAT
            )) if p.id == pane_id),
            None,
        )
        if not pane:
            return None
        agents = [
            p.pid for p in panel.pane_processes(pane, panel.process_table())
            if panel.command_harness(p.command) == "pi"
        ]
        return min(agents) if agents else None

    def session_names(self):
        return self.tmux("list-sessions", "-F", "#{session_name}").split()

    def attach_client(self):
        """A control-mode client attached to the dedicated session."""
        client = subprocess.Popen(
            ["tmux", "-L", self.label, "-C", "attach", "-t", f"={SESSION_NAME}"],
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, env=self.tmux_env(),
        )
        self.procs.append(client)
        self.assertTrue(self.wait_until(lambda: self.client_count() == 1))
        return client

    def client_count(self):
        out = self.tmux("list-clients", "-t", f"={SESSION_NAME}", "-F", "#{client_name}")
        return len(out.split())

    def pane_pid(self, pane_id: str) -> int:
        return int(self.tmux("display", "-p", "-t", pane_id, "#{pane_pid}"))

    def alive(self, pid: int) -> bool:
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False

    def test_every_quit_key_only_detaches(self):
        cases = {
            "q": [("-l", "q")],
            "ctrl-c": [("C-c",)],
            "D confirm then ctrl-c": [("Down",), ("-l", "D"), ("C-c",)],
            "picker then ctrl-c": [("-l", "n"), ("C-c",)],
        }
        for name, steps in cases.items():
            with self.subTest(name):
                if SESSION_NAME in self.session_names():
                    self.tmux("kill-session", "-t", f"={SESSION_NAME}")
                viewer, panel_pane = self.dedicated_panel(goal=name.startswith("D"))
                other = self.agent_window("elsewhere")
                agent = self.pi_pid_in_any(other)
                viewer_pid = self.pane_pid(viewer)
                panel_pid = self.pane_pid(panel_pane)
                client = self.attach_client()
                for step in steps:
                    self.tmux("send-keys", "-t", panel_pane, *step)
                    if step == ("-l", "D"):
                        self.assertTrue(self.wait_until(
                            lambda: "Delete goal" in self.tmux(
                                "capture-pane", "-p", "-t", panel_pane)
                        ))
                    if step == ("-l", "n"):
                        self.assertTrue(self.wait_until(
                            lambda: "Claude Code" in self.tmux(
                                "capture-pane", "-p", "-t", panel_pane)
                        ))
                self.assertTrue(
                    self.wait_until(lambda: self.client_count() == 0),
                    f"{name} did not detach the client",
                )
                self.assertTrue(self.wait_until(lambda: client.poll() is not None))
                time.sleep(0.5)
                # Nothing else changed: session, panel, viewer, agents.
                self.assertIn(SESSION_NAME, self.session_names())
                self.assertNotIn(panel.BG_SESSION, self.session_names())
                panes = self.tmux("list-panes", "-s", "-t", f"={SESSION_NAME}",
                                  "-F", "#{pane_id}").split()
                self.assertIn(panel_pane, panes)
                self.assertIn(viewer, panes)
                self.assertEqual(self.pane_pid(viewer), viewer_pid)
                self.assertEqual(self.pane_pid(panel_pane), panel_pid)
                self.assertTrue(self.alive(viewer_pid))
                self.assertTrue(self.alive(panel_pid))
                self.assertEqual(self.pi_pid_in_any(other), agent)
                self.assertTrue(self.alive(agent))
                self.assertIn(
                    "python",
                    self.tmux("display", "-p", "-t", panel_pane,
                              "#{pane_current_command}").lower(),
                )
                # The panel is still running: a second attach sees the same
                # panel, which detaches that client too.
                self.attach_client()
                self.assertEqual(self.pane_pid(panel_pane), panel_pid)
                self.tmux("send-keys", "-t", panel_pane, "-l", "q")
                self.assertTrue(
                    self.wait_until(lambda: self.client_count() == 0),
                    f"{name}: the panel stopped answering after the first detach",
                )

    def test_rerunning_main_after_detach_adds_no_second_panel(self):
        _, panel_pane = self.dedicated_panel()
        self.attach_client()
        self.tmux("send-keys", "-t", panel_pane, "-l", "q")
        self.assertTrue(self.wait_until(lambda: self.client_count() == 0))
        env = os.environ.copy()
        env["TMUX"] = self.socket
        env["TMUX_PANE"] = self.panel_id  # a pane in the "vis" session
        result = subprocess.run(
            [str(ROOT / "main")], env=env, capture_output=True, text=True,
            timeout=20, stdin=subprocess.DEVNULL,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        time.sleep(0.5)
        panes = self.tmux("list-panes", "-s", "-t", f"={SESSION_NAME}",
                          "-F", "#{pane_id}").split()
        self.assertEqual(len(panes), 2, panes)
        self.assertIn(panel_pane, panes)
        windows = self.tmux("list-windows", "-t", f"={SESSION_NAME}",
                            "-F", "#{window_id}").split()
        self.assertEqual(len(windows), 1)

    def test_detach_only_touches_the_panels_own_session(self):
        _, panel_pane = self.dedicated_panel()
        self.attach_client()
        other = subprocess.Popen(
            ["tmux", "-L", self.label, "-C", "attach", "-t", "vis"],
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, env=self.tmux_env(),
        )
        self.procs.append(other)
        self.assertTrue(self.wait_until(
            lambda: self.tmux("list-clients", "-t", "=vis", "-F", "#{client_name}").strip()
        ))
        self.tmux("send-keys", "-t", panel_pane, "-l", "q")
        self.assertTrue(self.wait_until(lambda: self.client_count() == 0))
        time.sleep(0.3)
        self.assertIsNone(other.poll())
        self.assertTrue(self.tmux("list-clients", "-t", "=vis", "-F", "#{client_name}").strip())

    def test_detach_with_no_client_is_a_harmless_noop(self):
        _, panel_pane = self.dedicated_panel()
        self.tmux("send-keys", "-t", panel_pane, "-l", "q")
        time.sleep(0.7)
        self.assertIn(SESSION_NAME, self.session_names())
        self.assertIn("python", self.tmux(
            "display", "-p", "-t", panel_pane, "#{pane_current_command}").lower())
        self.assertNotIn("Could not detach", self.tmux(
            "capture-pane", "-p", "-t", panel_pane))

    def test_failed_detach_shows_a_footer_message_and_keeps_running(self):
        with mock.patch.object(panel, "panel_pane_id", return_value="%0"), \
                mock.patch.object(panel, "tmux") as fake:
            fake.side_effect = [
                mock.Mock(returncode=0, stdout="s\n"),
                mock.Mock(returncode=0, stdout="client\n"),
                mock.Mock(returncode=1, stdout=""),
            ]
            self.assertEqual(panel.detach_panel(), panel.DETACH_FAILED)
            self.assertEqual(fake.call_args_list[-1].args, ("detach-client", "-s", "=s"))

    def test_failed_query_is_a_failure_but_empty_ok_is_not(self):
        ok = lambda out: mock.Mock(returncode=0, stdout=out)
        bad = mock.Mock(returncode=1, stdout="")
        cases = [
            ([bad], panel.DETACH_FAILED),
            ([None], panel.DETACH_FAILED),
            ([ok("s\n"), bad], panel.DETACH_FAILED),
            ([ok("s\n"), None], panel.DETACH_FAILED),
            ([ok("")], ""),
            ([ok("s\n"), ok("")], ""),
        ]
        for effects, want in cases:
            with mock.patch.object(panel, "panel_pane_id", return_value="%0"), \
                    mock.patch.object(panel, "tmux", side_effect=effects):
                self.assertEqual(panel.detach_panel(), want, effects)


if __name__ == "__main__":
    unittest.main(verbosity=2)
