"""The two env knobs the screenshot fixture runs on.

`SASSY_CODE_STATE_DIR` moves the panel's state dir and
`SASSY_CODE_PROCS_FIXTURE` replaces the live `ps` snapshot, so a capture can
show `● in progress` without touching the user's state or needing a real agent.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.panel_loader import load_panel

ROOT = Path(__file__).resolve().parents[1]
panel = load_panel()


class StateDirEnvTest(unittest.TestCase):
    def test_the_env_moves_the_whole_state_dir(self):
        # Imported in a fresh interpreter: STATE_DIR is read once, at import.
        with tempfile.TemporaryDirectory() as tmp:
            out = subprocess.run(
                [sys.executable, "-c", "import panel.state as s; print(s.STATE_DIR)"],
                cwd=ROOT,
                capture_output=True,
                text=True,
                env={**os.environ, "SASSY_CODE_STATE_DIR": tmp},
            )
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(out.stdout.strip(), tmp)

    def test_no_env_keeps_the_home_default(self):
        env = {k: v for k, v in os.environ.items() if k != "SASSY_CODE_STATE_DIR"}
        out = subprocess.run(
            [sys.executable, "-c", "import panel.state as s; print(s.STATE_DIR)"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            env=env,
        )
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(
            out.stdout.strip(), os.path.expanduser("~/.sassywares/sassy-code")
        )


class ProcsFixtureTest(unittest.TestCase):
    def table(self, text: str) -> list:
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
            f.write(text)
            path = f.name
        self.addCleanup(os.unlink, path)
        with mock.patch.dict(os.environ, {"SASSY_CODE_PROCS_FIXTURE": path}):
            return panel.process_table()

    def test_the_file_replaces_the_live_snapshot(self):
        procs = self.table("  101      1 pi --session abc\n  102    101 claude --resume x\n")
        self.assertEqual([(p.pid, p.ppid) for p in procs], [(101, 1), (102, 101)])
        self.assertTrue(procs[0].command.startswith("pi --session"))

    def test_a_missing_file_is_an_empty_snapshot_not_a_live_one(self):
        # Unknown liveness, never "dead": the panel must not reap on this.
        with mock.patch.dict(
            os.environ, {"SASSY_CODE_PROCS_FIXTURE": "/nonexistent/procs.txt"}
        ):
            self.assertEqual(panel.process_table(), [])


if __name__ == "__main__":
    unittest.main()
