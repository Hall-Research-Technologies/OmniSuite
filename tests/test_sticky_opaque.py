"""A sticky or frozen table cell is painted opaque, in every table that has one.

`--surface-raised` and `--surface-elevated` are colour-mix() results, and a mix
whose percentages sum to less than 100 carries the shortfall as alpha. A sticky
cell painted with one of them directly lets the rows scrolling beneath it read
through. Device Info solved this by compositing the token over the opaque page
surface (`background-color: var(--surface)` plus a one-colour gradient); this
holds the Configure tables to the same rule, so a table cannot go back to the bare
token. The behaviour itself was measured in a browser (hide the cells beneath and
compare pixels); this reads the stylesheet, and says that is what it does.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SHEETS = ("ui/templates.css", "ui/device-table.css", "ui/matrix/usb-matrix.css")
TRANSLUCENT_TOKENS = ("--surface-raised", "--surface-elevated")


def rules(sheet):
    text = (ROOT / sheet).read_text(encoding="utf-8")
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.findall(r"([^{}]+)\{([^{}]*)\}", text)


def sticky_blocks():
    for sheet in SHEETS:
        for selector, body in rules(sheet):
            if re.search(r"position\s*:\s*sticky", body) and "background" in body:
                yield sheet, " ".join(selector.split()), body


class StickyCellsAreOpaque(unittest.TestCase):
    def test_there_are_sticky_rules_to_check(self):
        self.assertGreaterEqual(len(list(sticky_blocks())), 3)

    def test_a_translucent_token_is_never_the_whole_background(self):
        for sheet, selector, body in sticky_blocks():
            for token in TRANSLUCENT_TOKENS:
                if token not in body:
                    continue
                shorthand = re.search(r"(?<![-\w])background\s*:[^;]*" + token, body)
                self.assertIsNone(
                    shorthand, "%s: %s paints a sticky cell with %s directly" % (sheet, selector, token))
                self.assertRegex(
                    body, r"background-color\s*:\s*var\(--surface\)",
                    "%s: %s needs the opaque underlay beneath %s" % (sheet, selector, token))
                self.assertRegex(
                    body, r"background-image\s*:\s*linear-gradient\(\s*var\(" + token,
                    "%s: %s should layer %s as a gradient over the underlay" % (sheet, selector, token))

    def test_configure_frozen_columns_use_the_layered_form(self):
        frozen = [b for s, sel, b in sticky_blocks() if "sticky-identity" in sel]
        self.assertGreaterEqual(len(frozen), 1)


if __name__ == "__main__":
    unittest.main()
