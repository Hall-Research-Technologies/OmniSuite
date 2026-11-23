"""The Admin A/V Matrix draws its control states from the tokens the appearance presets set.

A literal colour in a hover, selected, pending or failed rule is invisible to the preset system and
survives every change to it; `filter: brightness()` lightens a light control and a dark one alike, so
one rule reads as hover in one palette and washed out in the other. These hold the convergence with
LumaSuite's matrix (which shares the same table CSS) so it does not drift back.

Comments are stripped before anything is asserted, and each rule is read only up to its own closing
brace, so an explanatory comment naming a selector cannot satisfy an assertion about the rule.
"""
import re
import unittest
from pathlib import Path

UI = Path(__file__).resolve().parent.parent / "ui"
LITERAL = re.compile(r"#[0-9a-fA-F]{3,8}\b|\brgba?\(|\bhsla?\(")


def css():
    text = (UI / "matrix" / "matrix.css").read_text(encoding="utf-8")
    return re.sub(r"/\*.*?\*/", "", text, flags=re.S)


def rule(selector):
    """The declaration block of the first rule whose selector list is exactly `selector`."""
    for chunk in css().split("}"):
        head, brace, body = chunk.rpartition("{")
        if brace and head.rsplit("{", 1)[-1].strip().split(";")[-1].strip() == selector:
            return body
        if brace and head.strip() == selector:
            return body
    raise AssertionError("no rule for %s" % selector)


class MatrixStateTokenTests(unittest.TestCase):
    def test_hover_is_a_veil_never_a_brightness_filter(self):
        self.assertNotIn("brightness(", css())
        self.assertIn("var(--control-hover-veil)", rule(".gear-btn:hover"))

    def test_state_rules_carry_no_literal_colour(self):
        for selector in (".gear-btn:hover", ".divider", ".failed-write", ".pending-write",
                         ".matrix-hostname-edit.invalid", ".radio-wrap input:checked"):
            self.assertIsNone(LITERAL.search(rule(selector)), "%s uses a literal colour" % selector)

    def test_failure_and_pending_use_distinct_semantic_tokens(self):
        self.assertIn("var(--state-error-text)", rule(".failed-write"))
        self.assertIn("var(--action-primary)", rule(".pending-write"))

    def test_a_routing_cell_has_hover_and_a_keyboard_focus_ring(self):
        hover = rule(".cell:not(.offline-blocked):not(.codec-blocked) .radio-wrap input:hover")
        self.assertIn("border-color", hover)
        focus = rule(".radio-wrap input:focus-visible")
        self.assertIn("var(--focus-ring)", focus)
        self.assertNotIn("outline: none", focus)
        self.assertNotIn("outline:none", focus)

    def test_every_color_mix_sums_to_one_hundred(self):
        text, seen = css(), 0
        for m in re.finditer(r"color-mix\(", text):
            depth, i = 1, m.end()
            while depth:                                   # balance the parentheses: var(...) is nested
                depth += {"(": 1, ")": -1}.get(text[i], 0)
                i += 1
            parts = [p.strip() for p in re.split(r",(?![^()]*\))", text[m.end():i - 1])][1:]   # drop "in srgb"
            stated = [float(x) for p in parts for x in re.findall(r"([\d.]+)%\s*$", p)]
            seen += 1
            if len(stated) == len(parts):
                self.assertAlmostEqual(sum(stated), 100.0, msg=text[m.start():i])
            else:                                         # one share is implicit and takes the remainder
                self.assertLess(sum(stated), 100.0, text[m.start():i])
                self.assertTrue(all(0 < v < 100 for v in stated), text[m.start():i])
        self.assertGreater(seen, 0)

    def test_every_matrix_page_asks_for_the_same_stylesheet_version(self):
        versions = set()
        for page in ("index.html", "configure.html", "usb.html", "multiview.html"):
            text = (UI / "matrix" / page).read_text(encoding="utf-8")
            versions.update(re.findall(r"/ui/matrix/matrix\.css\?v=([\w-]+)", text))
        self.assertEqual(len(versions), 1, versions)


if __name__ == "__main__":
    unittest.main()
