#!/usr/bin/env python3
"""Background-session lifecycle tests against an isolated tmux server.

No test talks to the user's tmux server: every case starts its own `tmux -L`
server with `-f /dev/null` and a fake `pi` executable on PATH.
"""
from __future__ import annotations

import importlib.util
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

ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location("sassy_code_panel", ROOT / "panel.py")
panel = importlib.util.module_from_spec(SPEC)
sys.modules["sassy_code_panel"] = panel
SPEC.loader.exec_module(panel)


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
        panel.STATE_FILE = str(self.tmp / "current")
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

    def test_quit_and_reopen_preserves_pending_session(self):
        self.write_session("pending", "toolUse")
        self.assertIsNone(self.present("pending"))
        pane_id = panel.pane_map(self.panes())["pending"]
        pid = self.pi_pid_in(pane_id)

        groups, _ = self.group_session("pending")
        panel.preserve_active_on_quit(groups, self.original_viewer, {})
        hidden = self.pane_by_id(pane_id)
        self.assertIsNotNone(hidden)
        self.assertEqual(hidden.session_name, panel.BG_SESSION)
        self.assertEqual(self.pi_pid_in(pane_id), pid)

        viewer = self.visible_viewer()
        self.assertIsNotNone(viewer)
        groups, s = self.group_session("pending")
        self.assertIsNone(panel.present_session(s, groups, viewer, {}))
        self.assertEqual(panel.pane_map(self.panes())["pending"], pane_id)
        self.assertEqual(self.pi_pid_in(pane_id), pid)
        self.assertEqual(len([p for p in self.panes() if p.tag == "pending"]), 1)

    def test_cold_start_quit_preserves_pending_viewer_without_cached_groups(self):
        self.write_session("cold", "toolUse")
        self.assertIsNone(self.present("cold"))
        pane_id = panel.pane_map(self.panes())["cold"]
        pid = self.pi_pid_in(pane_id)

        # Simulate a quit before the panel ever refreshed its cached list.
        panel.preserve_active_on_quit([], self.original_viewer, {})

        hidden = self.pane_by_id(pane_id)
        self.assertIsNotNone(hidden)
        self.assertEqual(hidden.session_name, panel.BG_SESSION)
        self.assertEqual(self.pi_pid_in(pane_id), pid)

    def test_quit_preserves_live_viewer_with_stale_complete_file(self):
        self.write_session("stale", "stop")
        self.assertIsNone(self.present("stale"))
        pane_id = panel.pane_map(self.panes())["stale"]
        pid = self.pi_pid_in(pane_id)
        self.assertIsNotNone(pid)

        groups, stale = self.group_session("stale")
        self.assertTrue(
            stale.is_complete(),
            "the session file still looks complete from the previous turn",
        )
        panel.preserve_active_on_quit(groups, self.original_viewer, {})

        hidden = self.pane_by_id(pane_id)
        self.assertIsNotNone(
            hidden,
            "a live viewer must be preserved even when the file looks complete",
        )
        self.assertEqual(hidden.session_name, panel.BG_SESSION)
        self.assertEqual(self.pi_pid_in(pane_id), pid)

    def test_quit_does_not_preserve_completed_viewer_without_live_pi(self):
        self.write_session("finished", "stop")
        self.assertIsNone(self.present("finished"))
        pane_id = panel.pane_map(self.panes())["finished"]
        pid = self.pi_pid_in(pane_id)
        self.assertIsNotNone(pid)
        os.kill(pid, 15)
        deadline = time.time() + 3
        while self.pi_pid_in(pane_id) and time.time() < deadline:
            time.sleep(0.05)
        self.assertIsNone(self.pi_pid_in(pane_id))

        groups, _ = self.group_session("finished")
        panel.preserve_active_on_quit(groups, self.original_viewer, {})

        self.assertEqual(self.visible_viewer(), pane_id)
        shown = self.pane_by_id(pane_id)
        self.assertIsNotNone(shown)
        self.assertNotEqual(shown.session_name, panel.BG_SESSION)
        self.assertFalse(
            any(p.tag == "finished" and p.session_name == panel.BG_SESSION
                for p in self.panes()),
            "a completed viewer with no live Pi needs no preservation",
        )

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
        self.assertEqual(marker.read_text(), repr("n"))
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
