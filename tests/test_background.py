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
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location("sassy_panel", ROOT / "panel.py")
panel = importlib.util.module_from_spec(SPEC)
sys.modules["sassy_panel"] = panel
SPEC.loader.exec_module(panel)


class BackgroundLifecycleTests(unittest.TestCase):
    counter = 0

    def setUp(self):
        BackgroundLifecycleTests.counter += 1
        self.tmp = Path(tempfile.mkdtemp(prefix="sassy-pi-test-"))
        self.sessions = self.tmp / "sessions"
        (self.sessions / "project").mkdir(parents=True)
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
        os.environ["TMUX"] = socket
        os.environ["TMUX_PANE"] = self.panel_id
        os.environ["PATH"] = f"{self.fake_bin}:{self.old_env['PATH'] or ''}"
        self.tmux("set-environment", "-g", "PATH", os.environ["PATH"])
        panel.SESSIONS_DIR = str(self.sessions)
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

    def write_session(self, sid: str, stop: str = "toolUse", cwd: str = ""):
        ts = time.strftime("%Y-%m-%dT%H:%M:%S")
        cwd = cwd or str(self.tmp)
        path = self.sessions / "project" / f"{sid}.jsonl"
        lines = [
            {"type": "session", "id": sid, "cwd": cwd, "timestamp": ts},
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
        ts = time.strftime("%Y-%m-%dT%H:%M:%S")
        entry = {"type": "message", "timestamp": ts,
                 "message": {"role": "assistant",
                             "content": [{"type": "text", "text": "done"}],
                             "stopReason": stop}}
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

    def pi_pid_in(self, pane_id: str):
        pane = self.pane_by_id(pane_id)
        if not pane:
            return None
        # The pane's root is a shell whose command text mentions `pi`; pick the
        # actual Pi descendant deterministically instead of a set-order match.
        pi_procs = sorted(
            (
                proc
                for proc in panel.pane_processes(pane, panel.process_table())
                if panel.command_is_pi(proc.command)
            ),
            key=lambda proc: proc.pid,
        )
        return pi_procs[0].pid if pi_procs else None

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


if __name__ == "__main__":
    unittest.main(verbosity=2)
