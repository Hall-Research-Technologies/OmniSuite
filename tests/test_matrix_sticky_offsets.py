"""The A/V Matrix's Lock Headers pins at the scrollport edge, on both axes.

Measured in a headless browser (1024, 1440 and 1920 wide, light and dark): with
Lock Headers on, the corner cell had `top: auto`, so it scrolled away while the
header row stayed, and decoder labels travelled up through its position and
overpainted the column headers. Separately `.matrix-wrap` carries padding that a
sticky offset ignores, so the grid scrolled through a gutter above and left of the
pinned cells.

The behaviour was measured by hit-testing the corner's own centre while scrolled on
both axes; this reads the stylesheet, comments stripped, and says that is what it
does. The offset is a published custom property, not a literal, so it follows the
compact template.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def rules():
    text = (ROOT / "ui" / "matrix" / "matrix.css").read_text(encoding="utf-8")
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    found = {}
    for sel, body in re.findall(r"([^{}]+)\{([^{}]*)\}", text):
        found.setdefault(" ".join(sel.split()), []).append(body)
    return {sel: " ".join(bodies) for sel, bodies in found.items()}


HEAD = ".matrix.sticky-enabled th.enc-head"
ROW = ".matrix.sticky-enabled th.row-head"
CORNER = ".matrix.sticky-enabled tr:first-child th.row-head"
PIN = r"calc\(\s*-1\s*\*\s*var\(--mx-pad"


class StickyOffsetsFollowThePadding(unittest.TestCase):
    def test_the_rules_exist(self):
        found = rules()
        for selector in (HEAD, ROW, CORNER):
            self.assertIn(selector, found)

    def test_the_header_row_pins_at_the_padding_edge(self):
        self.assertRegex(rules()[HEAD], r"top\s*:\s*" + PIN)

    def test_the_label_column_pins_at_the_padding_edge(self):
        self.assertRegex(rules()[ROW], r"left\s*:\s*" + PIN)

    def test_the_corner_pins_on_the_vertical_axis_too(self):
        self.assertRegex(rules()[CORNER], r"top\s*:\s*" + PIN)

    def test_no_pinned_cell_uses_a_zero_offset(self):
        found = rules()
        for selector in (HEAD, ROW, CORNER):
            self.assertNotRegex(found[selector], r"(?<![-\w])(top|left)\s*:\s*0\s*[;}]",
                                "%s pins at 0 and ignores the container padding" % selector)

    def test_the_padding_is_published_for_both_templates(self):
        found = rules()
        self.assertRegex(found[".matrix-wrap"], r"--mx-pad\s*:\s*8px")
        self.assertRegex(found["body.compact .matrix-wrap"], r"--mx-pad\s*:\s*6px")

    def test_the_published_padding_is_the_padding_the_container_really_has(self):
        found = rules()
        self.assertRegex(found[".matrix-wrap"], r"(?<![-\w])padding\s*:\s*8px")
        self.assertRegex(found["body.compact .matrix-wrap"], r"(?<![-\w])padding\s*:\s*6px")


SELECT_ALL = ".matrix.sticky-enabled th.row-head.select-all-head"


class TheSelectAllCellDoesNotHideUnderTheLabel(unittest.TestCase):
    """Both corner cells pinned at one `left`, so the label covered the checkbox.

    Found by packaged acceptance: with Lock Headers on and the grid scrolled 45px or
    more to the right, the select-all box was under the "Decoders / Encoders" label.
    Only the label is a frozen column, so the select-all cell stops pinning sideways
    (and keeps its top pin). The fix assumes no width: a hard-coded offset is right in
    one layout and wrong in the others.
    """

    def test_the_select_all_cell_does_not_pin_sideways(self):
        found = rules()
        self.assertIn(SELECT_ALL, found)
        self.assertRegex(found[SELECT_ALL], r"left\s*:\s*auto")

    def test_no_offset_is_assumed(self):
        self.assertNotRegex(rules()[SELECT_ALL], r"\d\s*(px|rem|em|ch)")

    def test_it_is_only_the_select_all_cell_that_stops_pinning(self):
        js = (ROOT / "ui" / "matrix" / "matrix.js").read_text(encoding="utf-8")
        head = js[js.index('const head = `<tr><th class="row-head'):]
        head = head[:head.index("</th><th class=\"row-head\">")]
        self.assertIn("select-all-head", head)
        self.assertIn("group-master-checkbox", head)
        self.assertIn('<th class="row-head">Decoders', js,
                      "the label cell is a plain row-head: the frozen column")

    def test_the_usb_matrix_corner_keeps_its_pin(self):
        usb = (ROOT / "ui" / "matrix" / "usb.js").read_text(encoding="utf-8")
        self.assertNotIn("select-all-head", usb,
                         "its single corner cell is the frozen label there")

    def test_the_corner_keeps_its_top_pin_and_stacking(self):
        self.assertRegex(rules()[CORNER], r"z-index\s*:\s*11")


if __name__ == "__main__":
    unittest.main()
