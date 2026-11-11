"""Every page that can open Settings gets the one confirmation dialog.

Settings > Back up / Restore asks for a passphrase through a dialog field. The
page-local dialog in matrix.js had no field, so /matrix and /matrix/configure
showed a passphrase dialog with nowhere to type, and /matrix/usb had no dialog
at all. The shared module owns the field; these pages must load it, and
matrix.js must yield to it rather than replace it.
"""
import re
import unittest
from pathlib import Path

UI = Path(__file__).resolve().parent.parent / "ui"
PAGES = ("matrix/index.html", "matrix/configure.html", "matrix/usb.html")


def scripts(page):
    return re.findall(r'<script[^>]+src="([^"?]+)', (UI / page).read_text(encoding="utf-8"))


def code(path):
    text = (UI / path).read_text(encoding="utf-8")
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"^\s*//.*$", "", text, flags=re.M)


class MatrixPagesShareTheDialogTests(unittest.TestCase):
    def test_each_page_loads_the_shared_dialog_before_settings(self):
        for page in PAGES:
            with self.subTest(page=page):
                found = scripts(page)
                self.assertIn("/ui/confirm.js", found, page)
                self.assertLess(found.index("/ui/confirm.js"), found.index("/ui/settings.js"), page)

    def test_matrix_pages_load_it_before_matrix_js(self):
        for page in PAGES[:2]:
            with self.subTest(page=page):
                found = scripts(page)
                self.assertLess(found.index("/ui/confirm.js"), found.index("/ui/matrix/matrix.js"), page)

    def test_matrix_js_only_installs_its_own_when_none_exists(self):
        js = code("matrix/matrix.js")
        self.assertNotIn("function omniConfirm(", js, "a hoisted declaration would replace the shared dialog")
        self.assertRegex(js, r"if \(typeof window\.omniConfirm !== 'function'\) window\.omniConfirm = matrixOwnConfirm;")


if __name__ == "__main__":
    unittest.main()
