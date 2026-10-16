#!/usr/bin/env python3
"""Pi session control panel.

Left pane: grouped list of all Pi sessions with status.
Right pane: the session currently selected, resumed via tmux.

This process runs in the left tmux pane. It never blocks on the right pane.
"""
from __future__ import annotations

import argparse
import curses
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field

SESSIONS_DIR = os.environ.get(
    "PI_CODING_AGENT_SESSION_DIR", os.path.expanduser("~/.pi/agent/sessions")
)
STATE_FILE = os.path.expanduser("~/.pi/agent/.sassy-pi-current")
ACTIVE_WINDOW = 300  # seconds -> "active"

# status -> (symbol, color pair index)
STATUS = {
    "running": ("●", 1),
    "error": ("✗", 2),
    "active": ("◐", 3),
    "idle": ("·", 4),
}


@dataclass
class Session:
    path: str
    id: str
    cwd: str
    created: float
    last_ts: float
    name: str = ""
    preview: str = ""
    last_user: str = ""
    status: str = "idle"
    messages: int = 0

    @property
    def title(self) -> str:
        return self.name or self.preview or "(empty session)"


@dataclass
class Group:
    cwd: str
    sessions: list[Session] = field(default_factory=list)

    @property
    def last_ts(self) -> float:
        return max((s.last_ts for s in self.sessions), default=0.0)


def iso_to_epoch(ts: str) -> float:
    try:
        return time.mktime(time.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S"))
    except Exception:
        return 0.0


def text_of(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for b in content:
            if isinstance(b, dict) and b.get("type") == "text":
                parts.append(str(b.get("text", "")))
        return " ".join(parts)
    return ""


def clean_preview(txt: str) -> str:
    """Turn a user turn into a one-line preview, skipping skill injections."""
    txt = re.sub(r"\s+", " ", txt).strip()
    if txt.startswith("<skill"):
        # Prefer text the user typed after the injected skill body, if any.
        tail = re.sub(r"^.*?</skill>", "", txt, flags=re.S).strip()
        return tail or "(skill invocation)"
    return txt


def parse_session(path: str) -> Session | None:
    try:
        with open(path, "r", errors="replace") as f:
            lines = f.readlines()
    except OSError:
        return None
    if not lines:
        return None

    def load(line: str):
        try:
            return json.loads(line)
        except Exception:
            return None

    header = load(lines[0])
    if not header or header.get("type") != "session":
        return None

    s = Session(
        path=path,
        id=header.get("id", ""),
        cwd=header.get("cwd", ""),
        created=iso_to_epoch(header.get("timestamp", "")),
        last_ts=iso_to_epoch(header.get("timestamp", "")),
    )

    last_role = None
    last_is_error = False
    for line in lines[1:]:
        o = load(line)
        if not o:
            continue
        s.last_ts = max(s.last_ts, iso_to_epoch(o.get("timestamp", "")))
        t = o.get("type")
        if t == "session_info":
            s.name = o.get("name", "") or s.name
        elif t == "message":
            m = o.get("message", {})
            role = m.get("role")
            if role not in ("user", "assistant", "toolResult", "custom"):
                continue
            s.messages += 1
            last_role, last_is_error = role, False
            if role == "user":
                txt = text_of(m.get("content")).strip()
                if txt:
                    s.last_user = txt
                    if not s.preview:
                        s.preview = clean_preview(txt)
            elif role == "assistant":
                if m.get("stopReason") in ("error", "aborted"):
                    last_is_error = True
            elif role == "toolResult":
                if m.get("isError"):
                    last_is_error = True

    status = "idle"
    if last_is_error:
        status = "error"
    elif last_role == "user":
        status = "active"  # user spoke last -> awaiting/handling
    elif time.time() - s.last_ts < ACTIVE_WINDOW:
        status = "active"
    s.status = status
    return s


def running_session_ids() -> set[str]:
    """Best effort: sessions currently resumed with an explicit --session id."""
    try:
        out = subprocess.run(
            ["ps", "-axo", "command"], capture_output=True, text=True, timeout=5
        ).stdout
    except Exception:
        return set()
    ids = set()
    for line in out.splitlines():
        if "--session" not in line:
            continue
        m = re.search(r"--session[= ](\S+)", line)
        if m:
            ids.add(m.group(1))
    return ids


def load_groups() -> list[Group]:
    running = running_session_ids()
    groups: dict[str, Group] = {}
    try:
        dirs = [d for d in os.listdir(SESSIONS_DIR)
                if os.path.isdir(os.path.join(SESSIONS_DIR, d))]
    except OSError:
        return []
    for d in dirs:
        dpath = os.path.join(SESSIONS_DIR, d)
        for fn in os.listdir(dpath):
            if not fn.endswith(".jsonl"):
                continue
            s = parse_session(os.path.join(dpath, fn))
            if not s:
                continue
            if s.id in running or any(s.id.startswith(r) for r in running):
                s.status = "running"
            groups.setdefault(s.cwd, Group(s.cwd)).sessions.append(s)
    result = list(groups.values())
    for g in result:
        g.sessions.sort(key=lambda s: s.last_ts, reverse=True)
    result.sort(key=lambda g: g.last_ts, reverse=True)
    return result


def rel_time(ts: float) -> str:
    if not ts:
        return "?"
    d = time.time() - ts
    if d < 60:
        return "now"
    if d < 3600:
        return f"{int(d // 60)}m"
    if d < 86400:
        return f"{int(d // 3600)}h"
    return f"{int(d // 86400)}d"


def shorten_cwd(cwd: str) -> str:
    home = os.path.expanduser("~")
    return "~" + cwd[len(home):] if cwd.startswith(home) else cwd


def resume(target: str, s: Session) -> None:
    cmd = f"pi --session {s.id}; exec {os.environ.get('SHELL', '/bin/bash')}"
    subprocess.run(
        ["tmux", "respawn-pane", "-k", "-t", target, "-c", s.cwd, cmd],
        check=False,
    )
    with open(STATE_FILE, "w") as f:
        f.write(s.id)


def new_session(target: str, cwd: str) -> None:
    cmd = f"pi; exec {os.environ.get('SHELL', '/bin/bash')}"
    subprocess.run(
        ["tmux", "respawn-pane", "-k", "-t", target, "-c", cwd, cmd], check=False
    )
    with open(STATE_FILE, "w") as f:
        f.write("")


def focus(target: str) -> None:
    """Hand keyboard focus to the right pane so the user can type immediately."""
    subprocess.run(["tmux", "select-pane", "-t", target], check=False)


def rows_for(groups: list[Group], flt: str, collapsed: set[str] | None = None):
    """Yield (kind, group, session) rows honoring filter and collapse state."""
    collapsed = collapsed or set()
    for g in groups:
        match_sessions = [
            s
            for s in g.sessions
            if not flt
            or flt.lower() in s.title.lower()
            or flt.lower() in s.id.lower()
            or flt.lower() in s.last_user.lower()
        ]
        gm = not flt or flt.lower() in g.cwd.lower()
        if not match_sessions and not gm:
            continue
        yield ("group", g, None)
        # A filter always expands groups so matches stay visible.
        if g.cwd in collapsed and not flt:
            continue
        for s in match_sessions:
            yield ("session", g, s)


def group_collapsed(g: Group, collapsed: set[str], flt: str) -> bool:
    return g.cwd in collapsed and not flt


def build_lines(items, w, collapsed: set[str], flt: str):
    """Lay out items into single display lines (one line per item).

    Returns (lines, starts, heights) where each line is
    (text, kind, item_index) and starts/heights map an item index to its
    first line and line count.
    """
    lines = []
    starts: dict[int, int] = {}
    heights: dict[int, int] = {}
    for idx, (kind, g, s) in enumerate(items):
        starts[idx] = len(lines)
        if kind == "group":
            arrow = "▸" if group_collapsed(g, collapsed, flt) else "▾"
            label = f"{arrow} {shorten_cwd(g.cwd)}  ({len(g.sessions)})"
            if len(label) > w - 1:
                label = label[: max(1, w - 2)] + "…"
            lines.append((label, "group", idx))
        else:
            sym, _ = STATUS[s.status]
            suffix = rel_time(s.last_ts)
            prefix = f" {sym} "
            tw = max(4, w - len(prefix) - len(suffix) - 2)
            title = s.title
            if len(title) > tw:
                title = title[: max(1, tw - 1)] + "…"
            body = prefix + title
            pad = max(1, w - len(body) - len(suffix) - 1)
            lines.append((body + " " * pad + suffix, "session", idx))
        heights[idx] = len(lines) - starts[idx]
    return lines, starts, heights


def draw(stdscr, groups, flt, cursor, offset, collapsed):
    stdscr.erase()
    h, w = stdscr.getmaxyx()
    title = f" Pi sessions — {sum(len(g.sessions) for g in groups)} sessions "
    stdscr.addnstr(0, 0, title.ljust(w - 1), w - 1, curses.A_REVERSE | curses.A_BOLD)
    if flt:
        stdscr.addnstr(1, 0, f" filter: {flt}", w - 1, curses.A_DIM)

    items = list(rows_for(groups, flt, collapsed))
    list_top = 2 if flt else 1
    avail = max(1, h - list_top - 1)
    if items:
        cursor = min(max(cursor, 0), len(items) - 1)
    else:
        cursor = 0

    lines, starts, heights = build_lines(items, w, collapsed, flt)

    # Keep the whole selected item (which may span several lines) visible.
    if items:
        start = starts[cursor]
        end = start + heights[cursor]
        if start < offset:
            offset = start
        if end > offset + avail:
            offset = end - avail
    offset = max(0, min(offset, max(0, len(lines) - avail)))

    for i in range(avail):
        li = offset + i
        if li >= len(lines):
            break
        text, kind, idx = lines[li]
        sel = idx == cursor
        attr = curses.A_REVERSE if sel else curses.A_NORMAL
        if kind == "group":
            stdscr.addnstr(i + list_top, 0, text.ljust(w - 1), w - 1,
                           attr | curses.A_BOLD
                           | (0 if sel else curses.color_pair(5)))
        else:
            s = items[idx][2]
            _, color = STATUS[s.status]
            stdscr.addnstr(i + list_top, 0, text.ljust(w - 1), w - 1,
                           attr | (0 if sel else curses.color_pair(color)))

    footer = " ↑/↓ move  ⏎ open  n new  r  /  q "
    stdscr.addnstr(h - 1, 0, footer.ljust(w - 1), w - 1, curses.A_REVERSE)
    stdscr.refresh()
    return cursor, offset


def prompt_filter(stdscr, current):
    h, w = stdscr.getmaxyx()
    curses.echo()
    curses.curs_set(1)
    stdscr.addnstr(h - 1, 0, " filter: ", w - 1, curses.A_REVERSE)
    stdscr.move(h - 1, 9)
    try:
        raw = stdscr.getstr(h - 1, 9, w - 10).decode("utf-8", "replace")
    except Exception:
        raw = current
    curses.noecho()
    curses.curs_set(0)
    return raw


def main_curses(stdscr, target: str):
    curses.curs_set(0)
    curses.start_color()
    curses.use_default_colors()
    curses.init_pair(1, curses.COLOR_GREEN, -1)   # running
    curses.init_pair(2, curses.COLOR_RED, -1)     # error
    curses.init_pair(3, curses.COLOR_YELLOW, -1)  # active
    curses.init_pair(4, curses.COLOR_BLUE, -1)    # idle
    curses.init_pair(5, curses.COLOR_CYAN, -1)    # group
    stdscr.timeout(2000)

    groups = load_groups()
    flt = ""
    cursor = offset = 0
    collapsed: set[str] = set()
    while True:
        cursor, offset = draw(stdscr, groups, flt, cursor, offset, collapsed)
        try:
            ch = stdscr.getch()
        except KeyboardInterrupt:
            break
        if ch == -1:
            groups = load_groups()
            continue
        rows = list(rows_for(groups, flt, collapsed))
        if ch in (curses.KEY_UP, ord("k")):
            cursor = max(0, cursor - 1)
        elif ch in (curses.KEY_DOWN, ord("j")):
            cursor = min(len(rows) - 1, cursor + 1)
        elif ch in (ord("g"), curses.KEY_HOME):
            cursor = 0
        elif ch in (ord("G"), curses.KEY_END):
            cursor = max(0, len(rows) - 1)
        elif ch == ord("q"):
            break
        elif ch == ord("r"):
            groups = load_groups()
        elif ch == ord("/"):
            flt = prompt_filter(stdscr, flt)
            cursor = 0
        elif ch == ord("n"):
            if 0 <= cursor < len(rows):
                new_session(target, rows[cursor][1].cwd)
                focus(target)
        elif ch in (curses.KEY_LEFT, ord("h")):
            if 0 <= cursor < len(rows) and rows[cursor][0] == "group":
                collapsed.add(rows[cursor][1].cwd)
        elif ch in (curses.KEY_RIGHT, ord("l")):
            if 0 <= cursor < len(rows) and rows[cursor][0] == "group":
                collapsed.discard(rows[cursor][1].cwd)
        elif ch in (curses.KEY_ENTER, 10, 13):
            if 0 <= cursor < len(rows):
                kind, g, s = rows[cursor]
                if kind == "group":
                    if g.cwd in collapsed:
                        collapsed.discard(g.cwd)
                    else:
                        collapsed.add(g.cwd)
                else:
                    resume(target, s)
                    focus(target)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", required=False, help="tmux pane id to control")
    ap.add_argument("--list", action="store_true", help="print parsed list and exit")
    args = ap.parse_args()

    if args.list:
        for g in load_groups():
            print(shorten_cwd(g.cwd))
            for s in g.sessions:
                print(f"  {STATUS[s.status][0]} [{s.status:7}] "
                      f"{s.title[:60]:<60} {rel_time(s.last_ts):>4}  {s.id}")
        return

    if not args.target:
        ap.error("--target is required unless --list is given")

    curses.wrapper(main_curses, args.target)


if __name__ == "__main__":
    main()
