"""Print the mocked session viewer pane: a plausible Pi transcript.

The right-hand pane of the screenshot has to look like a real session, but it
must not be one: a captured live session would publish whatever was on screen.
So it is drawn here, at the width and height the pane actually has, from the
conversation the selected demo session is about.

The layout mirrors Pi's: the transcript fills the pane, and the composer sits
on the last rows — the working directory and session title, the token line, the
mode and the model.
"""
from __future__ import annotations

import sys

RESET = "\x1b[0m"
DIM = "\x1b[2m"
BOLD = "\x1b[1m"
RED = "\x1b[31m"
GREEN = "\x1b[32m"
BLUE = "\x1b[34m"
CYAN = "\x1b[36m"
WHITE = "\x1b[37m"

W = 95  # overwritten from argv
H = 52


def paint(parts: list[tuple[str, str]], width: int) -> str:
    """One row from (style, text) parts, padded to *width* columns."""
    used = sum(len(body) for _, body in parts)
    if used > width:
        raise SystemExit(f"viewer row is {used} columns, pane is {width}")
    text = "".join(f"{style}{body}{RESET}" for style, body in parts)
    return text + " " * (width - used)


def row(parts: list[tuple[str, str]], right: tuple[str, str] | None = None) -> str:
    """A row, optionally with *right* (style, text) flush against the edge."""
    return paint(parts, W) if right is None else paint(
        [
            *parts,
            ("", " " * (W - sum(len(b) for _, b in parts) - len(right[1]))),
            right,
        ],
        W,
    )


def rule() -> str:
    return row([(DIM, "─" * W)])


def main() -> None:
    global W, H
    if len(sys.argv) > 1:
        W = int(sys.argv[1])
    if len(sys.argv) > 2:
        H = int(sys.argv[2])

    lines = [
        row([]),
        row([(WHITE + BOLD, "  the switch target: the tag or argv?")]),
        row([]),
        row([(DIM, "    Neither alone. The tag names the pane; argv lags an")]),
        row([(DIM, "    in-process /new, so resolving from argv resumes the")]),
        row([(DIM, "    predecessor and the session drops out of its goal.")]),
        row([]),
        row([(DIM, "    Resolve from the tag and the command line together, and")]),
        row([(DIM, "    leave the goal filing alone: it is keyed by session id,")]),
        row([(DIM, "    which survives the swap.")]),
        row([]),
        row(
            [(CYAN, "  ● "), ("", "panel/panes.py  "), (DIM, "pane_switch_target()")],
            (GREEN, "+18 "),
        ),
        row(
            [(CYAN, "  ● "), ("", "panel/goals.py  "), (DIM, "attach_goals()")],
            (GREEN, "+6 "),
        ),
        row([]),
        row(
            [(GREEN, "  ● "), ("", "bash  "), (DIM, "python3 -m unittest tests.test_goals"),
             ("", "  "), (GREEN, "ok")],
            (DIM, "3.42s"),
        ),
        row([]),
        row([(DIM, "    ran 61 tests; resume-after-swap was the failing case")]),
        row([(DIM, "    and it passes now.")]),
        row([]),
        row([(WHITE + BOLD, "  and a pane that was swapped twice?")]),
        row([]),
        row([(DIM, "    The tag wins again: it is rewritten on every swap, argv")]),
        row([(DIM, "    is only ever what the pane started with. The switch")]),
        row([(DIM, "    target stays tag-first, command line as the fallback for")]),
        row([(DIM, "    panes that predate the tag.")]),
        row([]),
        row(
            [(CYAN, "  ● "), ("", "panel/core.py  "), (DIM, "pane_is_live()")],
            (GREEN, "+3 "),
        ),
        row([]),
        row([(DIM, "    An empty process snapshot is unknown, not dead: fall back")]),
        row([(DIM, "    to the tag-only answer instead of reaping a live pane.")]),
        row([]),
        row([(WHITE + BOLD, "  and after a restart?")]),
        row([]),
        row([(DIM, "    Same answer. The tag lives on the pane, so a restarted")]),
        row([(DIM, "    viewer is re-tagged before the panel paints; only a pane")]),
        row([(DIM, "    that never had one falls through to argv.")]),
        row([]),
        row(
            [(GREEN, "  ● "), ("", "bash  "), (DIM, "brain/restart.sh")],
            (GREEN, "ok "),
        ),
        row(
            [(DIM, "      swapped 2 panes, re-tagged both, goal membership")],
        ),
        row([(DIM, "      unchanged for 4 sessions")]),
        row([]),
        row([(WHITE + BOLD, "  so what do I change?")]),
        row([]),
        row([(DIM, "    One helper, `pane_switch_target()`, already resolves from")]),
        row([(DIM, "    both. Put the rule in its docstring, point the two callers")]),
        row([(DIM, "    at it, and add the swap-twice case to the suite.")]),
        row([]),
        row(
            [(CYAN, "  ● "), ("", "panel/panes.py  "), (DIM, "pane_switch_target()")],
            (RED, "−9 "),
        ),
        row([]),
    ]

    composer = [
        rule(),
        row(
            [(BLUE, "  ~/workspace/sassy-code "), (DIM, "(main) · Panel hardening")],
            (DIM, "↑41k ↓8.2k CH88% $0.048 4.1%/1.0M  "),
        ),
        row(
            [(WHITE, "  ▶ work")],
            (DIM, "(deepseek) deepseek-flash · high  "),
        ),
    ]

    body = lines
    if len(body) + len(composer) > H:
        raise SystemExit("the transcript does not fit the pane")
    body = body + [row([])] * (H - len(composer) - len(body)) + composer

    out = []
    for y, line in enumerate(body):
        out.append(f"\x1b[{y + 1};1H{line}")
    sys.stdout.write("".join(out))
    sys.stdout.write(RESET)
    sys.stdout.flush()
    # Keep the pane alive: if this exits, tmux closes the pane and the layout
    # collapses. Teardown kills the whole server.
    import time

    while True:
        time.sleep(3600)


if __name__ == "__main__":
    main()
