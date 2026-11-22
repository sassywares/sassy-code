"""Render `tmux capture-pane -p -e` output to one SVG image.

Standard library only, on purpose: the repo ships no rendering dependency, and
the image is text, so a change to the panel shows up as a readable diff instead
of a new binary blob in history.

`render()` takes one ANSI screen per pane and stacks them side by side with a
divider, which is what the `sassy-code` window looks like: the control panel on
the left, the session viewer on the right.
"""
from __future__ import annotations

import re
import unicodedata

# Geometry. Every run is emitted with an explicit `textLength`, so alignment
# never depends on the reader having the same monospace font we do. These cells
# reproduce a 1440x900 macOS terminal window at 14px.
CELL_W = 9.6
CELL_H = 16.0
FONT_SIZE = 14.0

# ANSI 0-15 is the palette the shot was taken in (Ghostty's default style, so
# the README looks like the app in a fresh terminal) rather than the xterm
# defaults, which are washed out on a dark background. 16-255 use the standard
# xterm cube, which terminals do not theme.
THEME = [
    "#1d1f21", "#cc6666", "#2e8b58", "#f0c674",
    "#81a2be", "#b294bb", "#8abeb7", "#c5c8c6",
    "#666666", "#d54e53", "#34c759", "#e7c547",
    "#7aa6da", "#c397d8", "#70c0b1", "#eaeaea",
]
DEFAULT_FG = "#ffffff"
DEFAULT_BG = "#282c34"
DIVIDER_FG = "#3a3f4b"
# Ghostty fades dim text towards the background instead of using a colour.
DIM_KEEP = 0.46

# The window around the panes. Drawn, not captured: the image then reads as the
# app running on a desktop, which is what a README hero shot is for.
TITLEBAR_H = 30.0
STATUSBAR_H = 28.0
WINDOW_RADIUS = 12.0
TITLE_TEXT = "#8b93a1"
TITLE_SIZE = 13.0
TITLE_SEAM = "#3a3f4b"
DOTS = ((22.0, "#ff5f57"), (44.0, "#febc2e"), (66.0, "#28c840"))
DOT_R = 5.5


def _cube() -> list[str]:
    levels = [0, 95, 135, 175, 215, 255]
    out = list(THEME)
    for r in levels:
        for g in levels:
            for b in levels:
                out.append(f"#{r:02x}{g:02x}{b:02x}")
    for i in range(24):
        v = 8 + i * 10
        out.append(f"#{v:02x}{v:02x}{v:02x}")
    return out


PALETTE = _cube()

SGR_RE = re.compile(r"\x1b\[([0-9;]*)m")
# Any other escape tmux may emit (charset switches, cursor moves): dropped.
OTHER_ESC_RE = re.compile(r"\x1b(?:\[[0-9;?]*[@-~]|\([A-Za-z0-9]|[@-Z\\-_])")


class Style:
    __slots__ = ("fg", "bg", "bold", "dim", "underline", "reverse")

    def __init__(self) -> None:
        self.fg = -1
        self.bg = -1
        self.bold = False
        self.dim = False
        self.underline = False
        self.reverse = False

    def copy(self) -> "Style":
        other = Style()
        for name in self.__slots__:
            setattr(other, name, getattr(self, name))
        return other

    def sgr(self, params: list[int]) -> None:
        i = 0
        while i < len(params):
            p = params[i]
            if p == 0:
                self.__init__()
            elif p == 1:
                self.bold = True
            elif p == 2:
                self.dim = True
            elif p == 4:
                self.underline = True
            elif p == 7:
                self.reverse = True
            elif p == 22:
                self.bold = self.dim = False
            elif p == 24:
                self.underline = False
            elif p == 27:
                self.reverse = False
            elif 30 <= p <= 37:
                self.fg = p - 30
            elif p == 39:
                self.fg = -1
            elif 40 <= p <= 47:
                self.bg = p - 40
            elif p == 49:
                self.bg = -1
            elif 90 <= p <= 97:
                self.fg = p - 90 + 8
            elif 100 <= p <= 107:
                self.bg = p - 100 + 8
            elif p in (38, 48):
                target = "fg" if p == 38 else "bg"
                if i + 2 < len(params) and params[i + 1] == 5:
                    setattr(self, target, params[i + 2])
                    i += 2
                elif i + 4 < len(params) and params[i + 1] == 2:
                    setattr(
                        self, target,
                        (params[i + 2], params[i + 3], params[i + 4]),
                    )
                    i += 4
            i += 1

    def key(self) -> tuple:
        """Style with attributes folded into colours, for run comparison."""
        fg = self.fg
        bold = self.bold
        if bold and isinstance(fg, int) and 0 <= fg < 8:
            fg += 8
            bold = False  # the bright colour already carries the weight
        return (fg, self.bg, bold, self.dim, self.underline)

    def colours(self) -> tuple[str, str, bool, bool]:
        """(fg, bg, bold, underline) as hex, with dim and reverse applied.

        Reverse has to be resolved *after* the colours: default foreground and
        default background are not palette indexes, so swapping the indexes
        would turn a reversed row into a no-op.
        """
        fg, bg, bold, dim, underline = self.key()
        fg_hex = _hex(fg, DEFAULT_FG)
        bg_hex = _hex(bg, DEFAULT_BG)
        if dim:
            fg_hex = _blend(fg_hex, bg_hex, DIM_KEEP)
        if self.reverse:
            fg_hex, bg_hex = bg_hex, fg_hex
        return fg_hex, bg_hex, bold, underline


def _hex(value, default: str) -> str:
    if isinstance(value, str):  # the divider's own colour, e.g. DIVIDER_FG
        return value
    if isinstance(value, tuple):
        return "#%02x%02x%02x" % value
    if isinstance(value, int) and 0 <= value < len(PALETTE):
        return PALETTE[value]
    return default


def _blend(a: str, b: str, keep: float) -> str:
    pa = [int(a[i:i + 2], 16) for i in (1, 3, 5)]
    pb = [int(b[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(
        f"{int(x * keep + y * (1 - keep)):02x}" for x, y in zip(pa, pb)
    )


def _width(ch: str) -> int:
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


def parse(text: str, width: int) -> list[list[tuple[str, Style]]]:
    """ANSI text -> rows of (glyph, style) cells, padded to *width*."""
    rows: list[list[tuple[str, Style]]] = []
    for line in text.split("\n"):
        cells: list[tuple[str, Style]] = []
        style = Style()
        i = 0
        while i < len(line):
            m = SGR_RE.match(line, i)
            if m:
                params = [int(p) if p else 0 for p in m.group(1).split(";")]
                style.sgr(params)
                i = m.end()
                continue
            m = OTHER_ESC_RE.match(line, i)
            if m:
                i = m.end()
                continue
            ch = line[i]
            cells.append((ch, style.copy()))
            if _width(ch) == 2:
                # A continuation cell: no glyph, but it still occupies a column.
                cells.append(("", style.copy()))
            i += 1
        blank = (" ", Style())
        while len(cells) < width:
            cells.append((blank[0], blank[1].copy()))
        rows.append(cells[:width])
    if not rows:
        rows = [[(" ", Style()) for _ in range(width)]]
    return rows


def _run_key(style: Style) -> tuple:
    return style.colours()


def _text(chars: list[str]) -> str:
    return (
        "".join(chars)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _draw_row(row: list[tuple[str, Style]], y: float, out: list[str]) -> None:
    x = 0
    while x < len(row):
        key = _run_key(row[x][1])
        start = x
        chars: list[str] = []
        last_used = start
        while x < len(row) and _run_key(row[x][1]) == key:
            chars.append(row[x][0])
            if row[x][0].strip() or row[x][0] == "":
                # A continuation cell belongs to the wide glyph before it.
                last_used = x
            x += 1
        fg, bg, bold, underline = key
        span = x - start
        if bg != DEFAULT_BG:
            out.append(
                f'<rect x="{start * CELL_W:.1f}" y="{y:.1f}" '
                f'width="{span * CELL_W:.1f}" height="{CELL_H:.1f}" fill="{bg}"/>'
            )
        # Trim the run's padding before measuring it: `textLength` stretches the
        # glyphs across the run, so padding inside the run would smear the text
        # over the whole pane instead of leaving it where the terminal put it.
        text = "".join(chars).rstrip()
        if not text:
            continue
        weight = ' font-weight="bold"' if bold else ""
        deco = ' text-decoration="underline"' if underline else ""
        out.append(
            f'<text x="{start * CELL_W:.1f}" y="{y + CELL_H * 0.76:.1f}" '
            f'fill="{fg}" textLength="{(last_used - start + 1) * CELL_W:.1f}" '
            f'lengthAdjust="spacingAndGlyphs"{weight}{deco} '
            f'xml:space="preserve">{_text(text)}</text>'
        )


def _divider(height: int) -> list[list[tuple[str, Style]]]:
    style = Style()
    style.fg = DIVIDER_FG
    return [[("│", style) for _ in range(1)] for _ in range(height)]


def _status_row(text: str, width: int, fg: str, bg: str) -> list[tuple[str, Style]]:
    style = Style()
    style.fg = fg
    style.bg = bg
    row = [(ch, style.copy()) for ch in text[:width]]
    while len(row) < width:
        row.append((" ", style.copy()))
    return row


def render(
    panes: list[tuple[str, int]],
    height: int,
    *,
    title: str | None = None,
    status: tuple[str, str, str] | None = None,
) -> str:
    """One SVG for *panes* (ANSI text, column width), side by side.

    `title` draws the terminal window's titlebar (traffic lights plus the
    window title); `status` draws the tmux status bar as one more row, given as
    `(text, foreground, background)`. Without them the image is the bare screen.
    """
    grid: list[list[tuple[str, Style]]] = [[] for _ in range(height)]
    for index, (text, width) in enumerate(panes):
        if index:
            for y, cell in enumerate(_divider(height)):
                grid[y].extend(cell)
        rows = parse(text, width)
        for y in range(height):
            row = rows[y] if y < len(rows) else [(" ", Style()) for _ in range(width)]
            grid[y].extend(row)
    cols = max(len(r) for r in grid)
    for row in grid:
        while len(row) < cols:
            row.append((" ", Style()))

    w = cols * CELL_W
    screen_h = height * CELL_H
    top = TITLEBAR_H if title else 0.0
    bottom = STATUSBAR_H if status else 0.0
    h = top + screen_h + bottom
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w:.0f}" '
        f'height="{h:.0f}" viewBox="0 0 {w:.0f} {h:.0f}" font-size="{FONT_SIZE:.0f}" '
        'font-family="SFMono-Regular, Menlo, Consolas, \'DejaVu Sans Mono\', '
        'monospace">',
        f'<rect width="{w:.0f}" height="{h:.0f}" rx="{WINDOW_RADIUS:.0f}" '
        f'fill="{DEFAULT_BG}"/>',
    ]
    if title:
        out.append(
            f'<rect x="0" y="{top - 1:.1f}" width="{w:.0f}" height="1" '
            f'fill="{TITLE_SEAM}"/>'
        )
        for cx, colour in DOTS:
            out.append(
                f'<circle cx="{cx:.1f}" cy="{top / 2:.1f}" r="{DOT_R}" '
                f'fill="{colour}"/>'
            )
        out.append(
            f'<text x="{w / 2:.1f}" y="{top / 2 + TITLE_SIZE * 0.36:.1f}" '
            f'fill="{TITLE_TEXT}" font-size="{TITLE_SIZE:.0f}" '
            f'text-anchor="middle">{_text(list(title))}</text>'
        )
    out.append(f'<g transform="translate(0,{top:.1f})">')
    for y, row in enumerate(grid):
        _draw_row(row, y * CELL_H, out)
    out.append("</g>")
    if status:
        text, fg, bg = status
        out.append(
            f'<rect x="0" y="{top + screen_h:.1f}" width="{w:.0f}" '
            f'height="{STATUSBAR_H:.1f}" fill="{bg}"/>'
        )
        out.append(f'<g transform="translate(0,{top + screen_h + (STATUSBAR_H - CELL_H) / 2:.1f})">')
        _draw_row(_status_row(text, cols, fg, bg), 0.0, out)
        out.append("</g>")
    out.append("</svg>")
    return "\n".join(out) + "\n"
