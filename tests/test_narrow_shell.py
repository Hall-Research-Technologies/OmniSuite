"""The administrator shell wraps at narrow widths instead of pushing its navigation off screen."""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CSS = (ROOT / "ui" / "templates.css").read_text(encoding="utf-8")
PAGES = ("ui/index.html", "ui/matrix/index.html", "ui/matrix/configure.html",
         "ui/matrix/usb.html", "ui/matrix/multiview.html", "ui/admin/users.html")


def narrow_block():
    css = re.sub(r"/\*.*?\*/", "", CSS, flags=re.S)
    match = re.search(r"@media\s*\(max-width:\s*1000px\)\s*\{(.*?)\n\}", css, flags=re.S)
    assert match, "no narrow-window block in templates.css"
    return match.group(1)


class NarrowShellTests(unittest.TestCase):
    def test_the_desktop_minimum_width_is_released_below_it(self):
        self.assertRegex(narrow_block(), r"body\s*\{\s*min-width:\s*0\s*!important")

    def test_header_controls_and_tabs_wrap(self):
        block = narrow_block()
        for selector in (r"\.header", r"\.header \.controls", r"\.header \.tabs"):
            self.assertRegex(block, selector + r"\s*\{[^}]*flex-wrap:\s*wrap")

    def test_every_administrator_page_loads_the_shared_stylesheet(self):
        for page in PAGES:
            text = (ROOT / page).read_text(encoding="utf-8")
            self.assertIn("/ui/templates.css", text, page)


if __name__ == "__main__":
    unittest.main()
