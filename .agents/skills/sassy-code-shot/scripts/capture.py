"""Capture `assets/sassy-code.svg` from the demo store, deterministically.

Run from anywhere; it finds the repo itself. The panel is started on a
throwaway tmux server (`-L`, `-f /dev/null`) against a temporary session store
built from `fixtures/demo.py`, so:

  * no real session, title, goal or path is ever published;
  * nothing touches `~/.sassywares/sassy-code` or the real harness stores;
  * ages are relative, so an unchanged panel renders byte-identical output and
    the caller can skip the commit.

Nothing is written to the repo unless the image actually changed.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILL = HERE.parent
REPO = SKILL.parents[2]
FIXTURES = SKILL / "fixtures"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(FIXTURES))

import ansi2svg  # noqa: E402
import demo  # noqa: E402

SERVER = "sassy-shot"
SESSION = "sassy-code"
COLS = 150
ROWS = 52
# 36% of the width, so titles are readable: at the launcher's 22% default a
# shot this wide truncates nearly every row.
PANEL_W = 54
VIEWER_W = COLS - PANEL_W - 1  # tmux keeps one column for the pane divider
TITLE = "Panel hardening"  # a row that only exists once the panel has painted
# The row the shot highlights: the session the viewer pane is showing.
HIGHLIGHT = "swapping panes"
WINDOW_NAME = "sassy-code"  # the launcher's `-n`, shown in the status bar
STATUS_FG, STATUS_BG = ansi2svg.THEME[0], ansi2svg.THEME[2]


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _pi_transcript(s: dict, cwd: str, first: float, last: float) -> str:
    lines = [
        {
            "type": "session",
            "id": s["id"],
            "cwd": cwd,
            "timestamp": iso(first),
            "parentSession": "",
        }
    ]
    if s.get("name"):
        lines.append(
            {"type": "session_info", "name": s["name"], "timestamp": iso(first + 1)}
        )
    lines.append(
        {
            "type": "message",
            # A pending session's newest event *is* the unanswered user turn;
            # stamping it early would make the age belong to the turn before.
            "timestamp": iso(last if s["end"] == "pending" else first + 2),
            "message": {"role": "user", "content": s["preview"]},
        }
    )
    if s["end"] != "pending":
        reason = "error" if s["end"] == "error" else "endTurn"
        lines.append(
            {
                "type": "message",
                "timestamp": iso(last),
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "done."}],
                    "stopReason": reason,
                },
            }
        )
    return "\n".join(json.dumps(line) for line in lines) + "\n"


def _claude_transcript(s: dict, cwd: str, first: float, last: float) -> str:
    base = {"sessionId": s["id"], "cwd": cwd}
    lines = []
    if s.get("name"):
        lines.append({"type": "ai-title", **base, "timestamp": iso(first), "aiTitle": s["name"]})
    lines.append(
        {
            "type": "user",
            **base,
            "timestamp": iso(last if s["end"] == "pending" else first + 2),
            "message": {"role": "user", "content": s["preview"]},
        }
    )
    if s["end"] != "pending":
        entry = {
            "type": "assistant",
            **base,
            "timestamp": iso(last),
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "done."}],
                "stop_reason": "end_turn",
            },
        }
        if s["end"] == "error":
            entry["isApiErrorMessage"] = True
        lines.append(entry)
    return "\n".join(json.dumps(line) for line in lines) + "\n"


def materialize(root: Path) -> dict[str, str]:
    """Write the demo store under *root*; return the env that points at it."""
    now = time.time()
    sessions = demo.SESSIONS
    goal_records: dict[str, list[dict]] = {cwd: [] for cwd in demo.PROJECTS.values()}
    marks: dict[str, dict] = {}
    procs: list[str] = []
    current = ""

    for index, s in enumerate(sessions):
        cwd = demo.PROJECTS[s["project"]]
        last = now - s["age"] * 60
        first = last - s["span"] * 60
        if s["harness"] == "pi":
            path = root / "pi-sessions" / s["project"] / f"{s['id']}.jsonl"
            _write(path, _pi_transcript(s, cwd, first, last))
        else:
            encoded = "-" + cwd.strip("/").replace("/", "-")
            path = root / "claude-projects" / encoded / f"{s['id']}.jsonl"
            _write(path, _claude_transcript(s, cwd, first, last))
        if s.get("done"):
            marks[s["id"]] = {"done": True, "done_at": last + 60}
        if s.get("live"):
            pid = 90001 + index
            command = (
                f"pi --session {s['id']}"
                if s["harness"] == "pi"
                else f"claude --resume {s['id']}"
            )
            procs.append(f"{pid:>6} {1:>6} {command}")
        if s.get("current"):
            current = s["id"]

    for goal in demo.GOALS:
        goal_records[demo.PROJECTS[goal["project"]]].append(
            {
                "id": goal["id"],
                "name": goal["name"],
                "created": now - 86400,
                "done": False,
                "collapsed": goal.get("collapsed", False),
                "sessions": goal["sessions"],
                "parent": goal.get("parent", ""),
            }
        )

    state = root / "state"
    _write(
        state / "goals.json",
        json.dumps({"version": 1, "projects": goal_records, "loose": {}}, indent=1) + "\n",
    )
    _write(
        state / "sessions.json",
        json.dumps({"version": 1, "sessions": marks}, indent=1) + "\n",
    )
    _write(state / "current", current + "\n")
    _write(root / "procs.txt", "\n".join(procs) + "\n")

    return {
        "SASSY_CODE_STATE_DIR": str(state),
        "PI_CODING_AGENT_SESSION_DIR": str(root / "pi-sessions"),
        "SASSY_CODE_CLAUDE_SESSION_DIR": str(root / "claude-projects"),
        "SASSY_CODE_PROCS_FIXTURE": str(root / "procs.txt"),
    }


def tmux(*args: str, check: bool = True) -> str:
    result = subprocess.run(
        ["tmux", "-L", SERVER, "-f", "/dev/null", *args],
        capture_output=True,
        text=True,
    )
    if check and result.returncode != 0:
        raise SystemExit(f"tmux {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def _pane_env(env: dict[str, str], argv: list[str]) -> list[str]:
    return ["env", *[f"{k}={v}" for k, v in env.items()], *argv]


def build(env: dict[str, str]) -> tuple[str, str]:
    """Create the two-pane window; return (panel pane, viewer pane)."""
    tmux("kill-server", check=False)
    # `start-server` exits at once with no sessions unless told otherwise; the
    # options below have to land before the first pane spawns, because
    # `default-terminal` decides the TERM that pane (and curses) sees.
    tmux("start-server", ";", "set-option", "-g", "exit-empty", "off")
    for option, value in (
        ("default-terminal", "tmux-256color"),
        ("pane-border-status", "off"),
        ("focus-events", "off"),
        # Keep tmux's own status line, but never the clock in its right half:
        # the image has to come out byte-identical when nothing changed.
        ("status-right", ""),
        ("status-justify", "left"),
        # The launcher widens this so `status-left` shows the whole session
        # name; the shot has to honour the same width or it would fold the
        # window list into the name.
        ("status-left-length", str(len(SESSION) + 3)),
    ):
        tmux("set-option", "-g", option, value)

    viewer = tmux(
        "new-session", "-d", "-s", SESSION, "-n", WINDOW_NAME,
        "-x", str(COLS), "-y", str(ROWS),
        "-c", str(REPO), "-P", "-F", "#{pane_id}", "--",
        *_pane_env(
            env,
            [sys.executable, str(HERE / "viewer.py"), str(VIEWER_W), str(ROWS)],
        ),
    ).strip()
    panel = tmux(
        "split-window", "-h", "-b", "-l", str(PANEL_W), "-t", viewer,
        "-c", str(REPO), "-P", "-F", "#{pane_id}", "--",
        *_pane_env(env, [sys.executable, "-m", "panel", "--target", viewer]),
    ).strip()
    return panel, viewer


def wait_for_paint(panel: str, timeout: float = 15.0) -> str:
    deadline = time.time() + timeout
    previous = None
    while time.time() < deadline:
        text = tmux("capture-pane", "-p", "-t", panel)
        if TITLE in text and text == previous:
            return text
        previous = text
        time.sleep(0.2)
    raise SystemExit("the panel never painted; run with --keep to inspect it")


def _is_reversed(row: str) -> bool:
    return "\x1b[7m" in row or ";7m" in row


def select_session(panel: str, needle: str, presses: int = 16) -> None:
    """Move the highlight onto *needle*'s row.

    The panel opens with the highlight on the first row (a project heading).
    The shot reads better with the row the viewer is showing highlighted, which
    is also the state `present` leaves behind. Reverse video only exists in the
    `-e` capture, so that is how the row is recognised.
    """
    for _ in range(presses):
        text = tmux("capture-pane", "-p", "-e", "-t", panel)
        rows = text.split("\n")
        if any(needle in row and _is_reversed(row) for row in rows):
            return
        tmux("send-keys", "-t", panel, "Down")
        time.sleep(0.15)
    raise SystemExit(f"could not highlight a row containing {needle!r}")


def status_line() -> tuple[str, str, str]:
    """The status bar tmux draws, read from tmux's own formats.

    A pane capture never includes the status bar, so it is rebuilt here from
    `status-left`, the window list and `status-right`, honouring the same
    `status-left-length` the launcher widens for `sassy-code`.
    """
    def option(name: str) -> str:
        return tmux("show-options", "-gv", name).rstrip("\n")

    left = tmux(
        "display-message", "-p", "-t", SESSION, "#{E:status-left}"
    ).rstrip("\n")[: int(option("status-left-length"))]
    window = tmux(
        "display-message", "-p", "-t", SESSION, "#{E:window-status-format}"
    ).rstrip("\n")
    text = (left + window)[:COLS]
    return text.ljust(COLS), STATUS_FG, STATUS_BG


def capture(panel: str, viewer: str) -> str:
    """Render the two panes as one SVG, as the window actually looks."""
    return ansi2svg.render(
        [
            (tmux("capture-pane", "-p", "-e", "-t", panel), PANEL_W),
            (tmux("capture-pane", "-p", "-e", "-t", viewer), VIEWER_W),
        ],
        ROWS,
        title=WINDOW_NAME,
        status=status_line(),
    )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--out",
        default=str(REPO / "assets" / "sassy-code.svg"),
        help="where the SVG goes (default: assets/sassy-code.svg)",
    )
    ap.add_argument("--plain", action="store_true", help="print the screen and exit")
    ap.add_argument("--keep", action="store_true", help="leave the tmux server up")
    args = ap.parse_args()

    out = Path(args.out)
    root = Path(tempfile.mkdtemp(prefix="sassy-shot-"))
    env = materialize(root)
    try:
        panel, viewer = build(env)
        wait_for_paint(panel)
        if args.plain:
            sys.stdout.write(tmux("capture-pane", "-p", "-t", panel))
            sys.stdout.write("\n--- viewer ---\n")
            sys.stdout.write(tmux("capture-pane", "-p", "-t", viewer))
            return
        select_session(panel, HIGHLIGHT)
        svg = capture(panel, viewer)
        existing = out.read_text() if out.exists() else ""
        label = out.relative_to(REPO) if out.is_relative_to(REPO) else out
        if existing == svg:
            print(f"{label} is up to date")
            return
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(svg)
        print(f"wrote {label} ({len(svg)} bytes)")
    finally:
        if not args.keep:
            tmux("kill-server", check=False)
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    main()
