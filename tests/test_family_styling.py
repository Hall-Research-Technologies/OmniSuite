"""OmniSuite and LumaSuite are members of one product family.

Not two themes that look alike: one set of semantic roles, one Default palette,
one navigation geometry. These hold the parts of that which live in the
stylesheets and the preset table, so a later change that quietly forks them from
the sibling product fails here.

Source assertions strip comments first -- an explanatory sentence naming the very
selector under test would otherwise satisfy the check with the rule gone.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read(*parts):
    return (ROOT.joinpath(*parts)).read_text(encoding="utf-8")


def strip_css(text):
    return re.sub(r"/\*.*?\*/", "", text, flags=re.S)


def block(css, selector):
    """The declarations of the first rule whose selector list contains `selector`."""
    for match in re.finditer(r"([^{}]+)\{([^{}]*)\}", css):
        selectors = [s.strip() for s in match.group(1).split(",")]
        if selector in selectors:
            return match.group(2)
    return ""


TEMPLATES = strip_css(read("ui", "templates.css"))
REMOTE = strip_css(read("ui", "remote.css"))
MATRIX = strip_css(read("ui", "matrix", "matrix.css"))
APPEARANCE = read("ui", "appearance.js")


class ButtonRoleTests(unittest.TestCase):
    ROLES = {
        ".btn.primary:not(:disabled)": ("--action-primary", "--action-primary-text"),
        ".btn.secondary": ("--surface-elevated", "--text", "--edge"),
        ".btn.success:not(:disabled)": ("--state-success", "--on-success"),
        ".btn.warn": ("--state-warning", "--on-warning"),
        ".btn.danger:not(:disabled)": ("--state-error", "--on-error", "--danger-ring"),
    }

    def test_every_role_is_defined_from_semantic_tokens(self):
        for selector, tokens in self.ROLES.items():
            body = block(TEMPLATES, selector)
            with self.subTest(selector=selector):
                self.assertTrue(body, "the %s rule is missing" % selector)
                for token in tokens:
                    self.assertIn("var(%s" % token, body)

    def test_a_role_never_names_a_literal_colour(self):
        for selector in self.ROLES:
            body = block(TEMPLATES, selector)
            # A literal is allowed only as the fallback inside var(), as the
            # existing rules do for --on-error and --on-warning.
            stripped = re.sub(r"var\([^)]*\)", "", body)
            stripped = re.sub(r"color-mix\([^;]*\)", "", stripped)
            with self.subTest(selector=selector):
                self.assertNotRegex(stripped, r"#[0-9a-fA-F]{3,8}\b|rgba?\(")

    def test_destructive_is_distinguished_by_form_as_well_as_hue(self):
        body = block(TEMPLATES, ".btn.danger:not(:disabled)")
        self.assertIn("box-shadow", body)
        self.assertIn("--danger-ring", body)

    def test_disabled_is_muted_for_every_role(self):
        css = TEMPLATES
        start = css.index(".btn:disabled")
        rule = css[start:css.index("}", start)]
        for needle in ("--control-disabled-opacity", "cursor: not-allowed",
                       "background-image: none", "box-shadow: none"):
            self.assertIn(needle, rule)

    def test_hover_is_a_veil_and_never_brightness(self):
        self.assertNotRegex(TEMPLATES, r"filter:\s*brightness")
        self.assertIn("--control-hover-veil", TEMPLATES)
        self.assertIn("--control-press-veil", TEMPLATES)

    def test_keyboard_focus_is_visible_not_removed(self):
        self.assertIn(".btn:focus-visible", TEMPLATES)
        self.assertNotRegex(TEMPLATES, r":focus\s*\{[^}]*outline:\s*(none|0)")

    def test_no_state_changes_a_controls_box(self):
        for selector in (".btn:hover:not(:disabled):not([aria-disabled=\"true\"])",
                         ".btn:active:not(:disabled)"):
            body = block(TEMPLATES, selector)
            for forbidden in ("width", "height", "padding", "border-width",
                              "font-size", "margin", "position"):
                with self.subTest(selector=selector, prop=forbidden):
                    self.assertNotRegex(body, r"(^|;|\s)%s\s*:" % forbidden)


class DefaultPresetTests(unittest.TestCase):
    """Luma's Default preset is #1e90ff accent/selection, #4ade80 success,
    #eab308 warning, #e5534b error, #38bdf8 information, #4a5561 surface. This is
    the same palette; it differs only by the two Omni-only roles."""

    @classmethod
    def setUpClass(cls):
        cls.preset = APPEARANCE[APPEARANCE.index("id: 'default'"):]
        cls.preset = cls.preset[:cls.preset.index("{id: 'slate'")]

    def test_the_default_is_hallway_blue(self):
        self.assertIn("hint: 'Hallway blue'", self.preset)
        self.assertNotIn("OmniSuite blue", APPEARANCE)

    def test_the_default_palette_matches_the_family(self):
        expected = {"accent": "#1e90ff", "selection": "#1e90ff", "success": "#4ade80",
                    "warning": "#eab308", "error": "#e5534b", "info": "#38bdf8",
                    "surface": "#4a5561"}
        for role, value in expected.items():
            with self.subTest(role=role):
                self.assertRegex(self.preset, r"%s:\s*'%s'" % (role, value))

    def test_the_base_tokens_use_the_same_accent(self):
        for sheet in (TEMPLATES,):
            self.assertIn("#1e90ff", sheet)
        self.assertIn("--action-primary: var(--accent, #1e90ff)", TEMPLATES)

    def test_every_preset_is_still_offered(self):
        ids = re.findall(r"\{id: '([a-z]+)', name: '[A-Za-z ]+', hint:", APPEARANCE)
        self.assertGreaterEqual(len(ids), 10)
        self.assertIn("default", ids)

    def test_light_mode_restates_the_semantic_tokens(self):
        light = TEMPLATES[TEMPLATES.index("html.light"):]
        for token in ("--surface:", "--text:", "--state-success:", "--state-warning:",
                      "--state-error:", "--control-hover-veil:", "--danger-ring:"):
            with self.subTest(token=token):
                self.assertIn(token, light)


class NavigationGeometryTests(unittest.TestCase):
    """Every page's tab carries the same measurements."""

    PAGE_RULE = ("padding:8px 16px", "font-size:14px", "font-weight:500",
                 "border-radius:6px 6px 0 0")

    def squash(self, text):
        return re.sub(r"\s+", "", text)

    def test_the_inline_page_rule_is_the_reference(self):
        index = self.squash(read("ui", "index.html"))
        for part in self.PAGE_RULE:
            self.assertIn(self.squash(part), index)

    def test_the_matrix_pages_carry_the_same_rule(self):
        for page in ("index.html", "configure.html", "usb.html", "multiview.html"):
            text = self.squash(read("ui", "matrix", page))
            with self.subTest(page=page):
                if ".tab{" not in text:
                    self.skipTest("this page takes the tab rule from a stylesheet")
                for part in self.PAGE_RULE:
                    self.assertIn(self.squash(part), text)

    def test_the_remote_access_page_uses_the_same_measurements(self):
        body = self.squash(block(REMOTE, ".remote-header .tabs .tab"))
        for part in self.PAGE_RULE:
            self.assertIn(self.squash(part), body)

    def test_the_gear_is_the_same_square_everywhere(self):
        for text in (read("ui", "settings.css"), read("ui", "matrix", "matrix.css")):
            self.assertIn("width:36px", self.squash(strip_css(text)))
        self.assertIn("width:36px", self.squash(REMOTE))

    def test_selected_and_hover_come_from_the_shared_stylesheet(self):
        body = block(TEMPLATES, ".tab.active")
        self.assertIn("--selected-tint-strong", body)
        self.assertIn("--selected-edge", body)
        self.assertIn("--text", body)
        self.assertNotRegex(block(TEMPLATES, ".tab.active"), r"#[0-9a-f]{3,6}")

    def test_navigation_is_text_with_no_decorative_images(self):
        for page in (read("ui", "index.html"), read("ui", "admin", "users.html")):
            tabs = re.search(r'<div class="tabs"[^>]*>(.*?)</div>', page, re.S).group(1)
            self.assertNotRegex(tabs, r"<img|<svg|&#x?[0-9a-f]+;|emoji")


class MatrixCenteringTests(unittest.TestCase):
    """Centred when it fits, scrolling when it does not."""

    def test_the_matrix_is_centred_with_auto_margins_not_a_flex_trap(self):
        body = block(MATRIX, ".matrix-wrap")
        squashed = re.sub(r"\s+", "", body)
        self.assertIn("width:max-content", squashed)
        self.assertIn("margin-left:auto", squashed)
        self.assertIn("margin-right:auto", squashed)
        # The cap includes the padding and border, so the left and right gaps are
        # equal when the matrix is wide enough to need it (measured: 30px each at
        # a 1500px viewport, where it was 28 and 14 before).
        self.assertIn("box-sizing:border-box", squashed)
        # Capped, and scrolls inside the cap -- so a large matrix is never cut off.
        self.assertIn("max-width:96vw", squashed)
        self.assertIn("overflow:auto", squashed)

    def test_the_parent_does_not_justify_content_to_the_centre(self):
        """`justify-content: center` on an overflowing flex parent clips the
        left edge with no way to scroll to it. Auto margins do not."""
        for selector in (".matrix-area", ".configure-area"):
            self.assertNotIn("justify-content", block(MATRIX, selector))

    def test_the_filters_align_with_the_matrix(self):
        body = re.sub(r"\s+", "", block(MATRIX, ".matrix-filter-row"))
        self.assertIn("margin:18pxauto8px", body)
        self.assertIn("justify-content:center", body)
        self.assertIn("max-width:96vw", body)


if __name__ == "__main__":
    unittest.main()
