"""Unit tests for the README screenshot renderer.

The renderer lives with the `sassy-code-shot` skill rather than in `panel/`,
so it is loaded by path. Only the SVG text is asserted: the image *is* text,
which is the reason a change to the panel shows up as a readable diff.
"""
from __future__ import annotations

import importlib.util
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
RENDERER = ROOT / ".agents/skills/sassy-code-shot/scripts/ansi2svg.py"


def _load():
    spec = importlib.util.spec_from_file_location("ansi2svg", RENDERER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ansi2svg = _load()


def texts(svg: str) -> list[tuple[str, dict]]:
    """Every <text> in *svg* with its attributes and body."""
    import re

    out = []
    for match in re.finditer(r"<text ([^>]*)>(.*?)</text>", svg, re.S):
        attrs = dict(re.findall(r'([A-Za-z:]+)="([^"]*)"', match.group(1)))
        out.append((match.group(2), attrs))
    return out


def rect_fills(svg: str) -> list[str]:
    import re

    return re.findall(r'<rect[^>]*fill="([^"]+)"', svg)


class ParseTest(unittest.TestCase):
    def test_padding_is_not_part_of_a_text_run(self):
        # `textLength` stretches a run to its nominal width, so trailing
        # padding inside the run would smear the text across the pane.
        svg = ansi2svg.render([("\x1b[32mab\x1b[0m      ", 8)], 1)
        (body, attrs), = texts(svg)
        self.assertEqual(body, "ab")
        self.assertAlmostEqual(float(attrs["textLength"]), 2 * ansi2svg.CELL_W)

    def test_a_wide_glyph_occupies_two_cells(self):
        svg = ansi2svg.render([("日x", 3)], 1)
        (body, attrs), = texts(svg)
        self.assertEqual(body, "日x")
        self.assertAlmostEqual(float(attrs["textLength"]), 3 * ansi2svg.CELL_W)

    def test_xterm_escapes_are_dropped(self):
        rows = ansi2svg.parse("\x1b(B\x1b[?25lab", 4)
        self.assertEqual("".join(glyph for glyph, _ in rows[0]), "ab  ")

    def test_256_colour_indexes_the_xterm_cube(self):
        rows = ansi2svg.parse("\x1b[38;5;196mx", 1)
        self.assertEqual(rows[0][0][1].fg, 196)
        self.assertEqual(ansi2svg._hex(196, "#000000"), "#ff0000")


class ColourTest(unittest.TestCase):
    def test_bold_brightens_a_theme_colour_instead_of_doubling_weight(self):
        svg = ansi2svg.render([("\x1b[1;31mx", 1)], 1)
        (_, attrs), = texts(svg)
        self.assertEqual(attrs["fill"], ansi2svg.THEME[9])
        self.assertNotIn("font-weight", attrs)

    def test_reverse_resolves_after_defaults(self):
        # Default foreground on default background is not a palette swap: the
        # reversed row has to become dark text on the light default foreground.
        svg = ansi2svg.render([("\x1b[1;7m x ", 3)], 1)
        (body, attrs), = texts(svg)
        self.assertEqual(body.strip(), "x")
        self.assertEqual(attrs["fill"], ansi2svg.DEFAULT_BG)
        self.assertIn(ansi2svg.DEFAULT_FG, rect_fills(svg))

    def test_dim_moves_the_foreground_towards_the_background(self):
        svg = ansi2svg.render([("\x1b[2mxx", 2)], 1)
        (_, attrs), = texts(svg)
        self.assertNotEqual(attrs["fill"], ansi2svg.DEFAULT_FG)
        self.assertRegex(attrs["fill"], r"^#[0-9a-f]{6}$")


class RenderTest(unittest.TestCase):
    def test_panes_are_separated_by_one_divider_column(self):
        svg = ansi2svg.render([("a", 2), ("b", 2)], 2)
        # 5 columns: 2 + one divider + 2, at CELL_W each.
        self.assertIn(f'width="{5 * ansi2svg.CELL_W:.0f}"', svg)
        dividers = [attrs for body, attrs in texts(svg) if "│" in body]
        self.assertEqual({a["fill"] for a in dividers}, {ansi2svg.DIVIDER_FG})
        self.assertEqual(len(dividers), 2)  # one per row

    def test_a_short_screen_is_padded_to_the_window_height(self):
        svg = ansi2svg.render([("a", 1)], 3)
        self.assertIn(f'height="{3 * ansi2svg.CELL_H:.0f}"', svg)  # 3 rows
        self.assertEqual(len(ansi2svg.parse("a", 1)), 1)

    def test_the_escape_free_text_survives_escaping(self):
        svg = ansi2svg.render([("a<b>&c", 6)], 1)
        (body, _), = texts(svg)
        self.assertEqual(body, "a&lt;b&gt;&amp;c")


    def test_the_window_adds_a_titlebar_and_a_status_bar(self):
        svg = ansi2svg.render([("a", 1)], 2, title="sassy-code")
        height = 2 * ansi2svg.CELL_H + ansi2svg.TITLEBAR_H
        self.assertIn(f'height="{height:.0f}"', svg)
        self.assertIn(">sassy-code</text>", svg)
        self.assertEqual(svg.count("<circle"), len(ansi2svg.DOTS))

        svg = ansi2svg.render(
            [("a", 1)], 2, status=("[sassy-code] 0:sassy-code*", "#1d1f21", "#2e8b58")
        )
        height = 2 * ansi2svg.CELL_H + ansi2svg.STATUSBAR_H
        self.assertIn(f'height="{height:.0f}"', svg)
        # The bar is painted edge to edge, not just under its text.
        self.assertIn(f'height="{ansi2svg.STATUSBAR_H:.1f}" fill="#2e8b58"', svg)
        # Every glyph is its own <text>; the bar starts at the left edge.
        self.assertIn(">[</text>", svg)


if __name__ == "__main__":
    unittest.main()
