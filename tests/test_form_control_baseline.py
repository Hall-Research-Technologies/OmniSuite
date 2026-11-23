"""Form controls take their colours from the theme on every page.

The defect: in Dark, the Settings dialog on the three Matrix pages (and filter boxes on
them) painted WHITE text boxes, selects and password fields. The control rules lived only
in ui/index.html's inline <style> and in ui/remote.css, so a page that loaded neither fell
back to the browser's own painting -- a white box with black text in the middle of a dark
card, differing in height from the same dialog on Device Info.

Measured in a real browser before the fix: every `input[type=text|password|number]` and every
`select` on /matrix, /matrix/configure, /matrix/usb and /matrix/multiview reported
`rgb(255, 255, 255)` with Dark selected, and after it `rgb(38, 47, 57)` on all of them. The
probe over every control on every page is kept with the evidence for this work; a unit test
cannot compute styles, so this one pins what the probe depends on:

  * one baseline, in the stylesheet every page loads, written in the semantic tokens the
    presets set (never a literal colour);
  * wrapped in :where() so it has no specificity of its own and every page-specific rule
    still wins -- the baseline decides only what nothing else decided;
  * covering every kind of control that paints: text, password, number, search, email, url,
    file, select and textarea -- and leaving checkboxes, radios, range, colour and buttons to
    their own treatment;
  * the dialog's size and shape for those controls in settings.css, so the dialog is the same
    height on every page.
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
UI = ROOT / "ui"


def css_without_comments(path: Path) -> str:
    return re.sub(r"/\*.*?\*/", "", path.read_text(encoding="utf-8"), flags=re.S)


def baseline_block() -> str:
    """The rule with :where(select, textarea, input...) and nothing else."""
    css = css_without_comments(UI / "templates.css")
    start = css.index(":where(select, textarea,")
    return css[start: css.index("}", start) + 1]


class BaselineTests(unittest.TestCase):
    def test_the_baseline_paints_from_tokens_and_never_from_a_literal(self):
        block = baseline_block()
        for declaration in ("background-color: var(--surface-elevated)",
                            "color: var(--text)", "border: 1px solid var(--edge)"):
            self.assertIn(declaration, block)
        self.assertNotRegex(block, r"#[0-9a-fA-F]{3,8}\b", "a literal colour in the baseline")
        self.assertNotRegex(block, r"\brgba?\(|\bhsla?\(", "a literal colour in the baseline")
        self.assertNotRegex(block, r"\b(white|black)\b", "a colour keyword in the baseline")

    def test_it_has_no_specificity_of_its_own(self):
        """A page-specific rule must still win, or the baseline would repaint every page."""
        block = baseline_block()
        selector = block[: block.index("{")]
        self.assertTrue(selector.strip().startswith(":where("))
        # Nothing outside :where() in the selector list.
        outside = re.sub(r":where\((?:[^()]|\([^()]*\))*\)", "", selector)
        self.assertNotRegex(outside, r"[A-Za-z.#\[]", "something outside :where() adds specificity: " + outside)

    def test_every_kind_of_control_that_paints_is_covered(self):
        block = baseline_block()
        self.assertIn("select", block)
        self.assertIn("textarea", block)
        self.assertRegex(block, r"input:not\(\[type=checkbox\]\)")
        # What is excluded is only what has its own treatment, so text, password, number,
        # search, email, url and file all fall under the rule.
        excluded = set(re.findall(r":not\(\[type=(\w+)\]\)", block))
        self.assertEqual(excluded, {"checkbox", "radio", "range", "color", "button", "submit",
                                    "reset", "image", "hidden"})
        for painted in ("text", "password", "number", "search", "email", "url", "file", "tel", "date"):
            self.assertNotIn(painted, excluded)

    def test_the_dark_native_control_scheme_follows_the_page(self):
        css = css_without_comments(UI / "templates.css")
        self.assertIn("color-scheme: dark", baseline_block())
        self.assertRegex(css, r":where\(\.light\) :where\(select, textarea, input\)\s*\{\s*color-scheme: light")

    def test_every_page_with_a_control_loads_the_stylesheet_that_carries_it(self):
        pages = sorted(UI.rglob("*.html"))
        self.assertGreaterEqual(len(pages), 8)
        checked = 0
        for page in pages:
            html = page.read_text(encoding="utf-8")
            # A page that builds its own controls from script still needs the sheet, so the
            # test is "any control or any script that injects the Settings dialog".
            has_controls = (re.search(r"<(input|select|textarea)\b", html)
                            or "settings.js" in html)
            if not has_controls:
                continue
            checked += 1
            self.assertRegex(html, r'href="/ui/templates\.css(\?[^"]*)?"',
                             f"{page.relative_to(UI)} has controls but does not load templates.css")
        self.assertGreaterEqual(checked, 8)

    def test_the_settings_dialog_has_one_size_on_every_page(self):
        css = css_without_comments(UI / "settings.css")
        match = re.search(r"\.modal select,\.modal input\[type=text\],\.modal input\[type=number\],"
                          r"\.modal input\[type=password\]\{([^}]*)\}", css)
        self.assertTrue(match, "the dialog's controls have no shared size rule")
        for declaration in ("font-size:14px", "padding:6px 8px", "border-radius:6px"):
            self.assertIn(declaration, match.group(1))

    def test_the_settings_dialog_looks_the_same_on_the_administration_pages(self):
        """Measured on /admin/users: remote.css's borrowed-dialog block painted every button in
        the Settings dialog in the accent fill (all ten presets, Choose Folder, Check Now...) and its
        text at the page's 15px, so the dialog was 1696px tall there against 1484px on Device Info.
        The block is for the page's own dialogs; the Settings dialog (`.cfg-modal`) has one look."""
        remote = css_without_comments(UI / "remote.css")
        start = remote.index(".modal:not(.cfg-modal) input[type=\"text\"]")
        block = remote[start: remote.index(".user-table")]
        self.assertNotRegex(re.sub(r"\.modal:not\(\.cfg-modal\)", "", block), r"\.modal\b",
                            "a `.modal` selector in the borrowed-dialog block does not exclude the Settings dialog")
        self.assertGreaterEqual(block.count(".modal:not(.cfg-modal)"), 10)
        settings_js = (UI / "settings.js").read_text(encoding="utf-8")
        self.assertIn('<div class="modal cfg-modal">', settings_js)
        css = css_without_comments(UI / "settings.css")
        self.assertRegex(css, r"\.cfg-modal \.btn\{[^}]*padding:3px 8px;border-radius:8px")
        self.assertRegex(css, r"\.cfg-modal \.btn:not\(\.secondary\)\{background:var\(--action-primary")
        self.assertRegex(css, r"\.cfg-modal \.small\{font-size:12px")
        self.assertRegex(css, r"\.cfg-modal input,\.cfg-modal select,\.cfg-modal \.btn\{box-sizing:border-box\}")

    def test_pages_that_loaded_neither_old_source_are_among_those_covered(self):
        """The three Matrix pages and Multiview are the ones that painted white."""
        for name in ("matrix/index.html", "matrix/configure.html", "matrix/usb.html", "matrix/multiview.html"):
            html = (UI / name).read_text(encoding="utf-8")
            self.assertNotIn("input[type=text]", html, name + " styles its own controls now")
            self.assertRegex(html, r'/ui/templates\.css', name)
            self.assertRegex(html, r'/ui/settings\.css', name)


if __name__ == "__main__":
    unittest.main()
