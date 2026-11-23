"""The user USB grid is styled like the Admin grid, declaration by declaration.

matrix.css cannot be loaded into the restricted page (it styles the body and every administrative
control), so ui/matrix/usb-grid.css restates the grid's geometry under .usb-grid-area. A restatement is
only safe if something compares it with the original; otherwise the two drift the first time somebody
changes a size on the Admin page. This reads both stylesheets and requires every geometry rule of the
Admin grid to be present, with the same values, in the user's.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read(*parts):
    return (ROOT.joinpath(*parts)).read_text(encoding="utf-8").replace("\r\n", "\n")


def rules(css):
    """{normalised selector: {property: value}}, comments removed, at-rules ignored."""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    out = {}
    for selector, body in re.findall(r"(?m)^\s*([^@{}\n][^{}]*?)\s*\{([^{}]*)\}", css):
        decls = {}
        for part in body.split(";"):
            if ":" in part:
                key, value = part.split(":", 1)
                decls[key.strip()] = re.sub(r"\s*,\s*", ",", re.sub(r"\s+", " ", value.strip())).lower()
        # A list of selectors is one rule per selector, so a restatement may group them differently.
        for part in selector.split(","):
            out.setdefault(re.sub(r"\s+", " ", part.strip()), {}).update(decls)
    return out


ADMIN = rules(read("ui", "matrix", "matrix.css"))
PAGE = rules(read("ui", "matrix", "usb.html")[read("ui", "matrix", "usb.html").index("<style>"):
                                               read("ui", "matrix", "usb.html").index("</style>")])
USER = rules(read("ui", "matrix", "usb-grid.css"))
SCOPE = ".usb-grid-area "

# (Admin selector, user selector). The Admin geometry, as matrix.css and usb.html state it.
GEOMETRY = [
    (ADMIN, ".matrix-wrap"), (ADMIN, ".matrix"), (ADMIN, ".matrix th"), (ADMIN, ".matrix td"),
    (ADMIN, ".matrix th.enc-head"), (ADMIN, ".matrix th.row-head"), (ADMIN, ".col-header"),
    (ADMIN, ".col-header .enc-host"), (ADMIN, ".cell"), (ADMIN, ".radio-wrap"), (ADMIN, ".radio-wrap input"),
    (ADMIN, ".radio-wrap input:checked"), (ADMIN, ".radio-wrap .dot"),
    (PAGE, ".lex-label"), (PAGE, ".rex-label"), (PAGE, "td.cell.pending"), (PAGE, "td.cell.pending .dot"),
]


def scoped(selector):
    return SCOPE + selector.strip()


class GridParityTests(unittest.TestCase):
    def test_every_admin_geometry_rule_is_restated_with_the_same_values(self):
        for source, selector in GEOMETRY:
            with self.subTest(selector=selector):
                admin = source.get(selector)
                self.assertIsNotNone(admin, "the Admin rule %r moved; update this comparison" % selector)
                user = USER.get(scoped(selector))
                self.assertIsNotNone(user, "usb-grid.css does not restate %r" % selector)
                for prop, value in admin.items():
                    self.assertEqual(user.get(prop), value,
                                     "%s { %s } differs: admin %r, user %r" % (selector, prop, value, user.get(prop)))

    def test_hover_and_keyboard_focus_use_the_same_tokens_in_both(self):
        admin_hover = ADMIN[".cell:not(.offline-blocked):not(.codec-blocked) .radio-wrap input:hover"]
        user_hover = USER[SCOPE + "td.cell:not(.disabled) .radio-wrap input:hover"]
        self.assertEqual(user_hover["border-color"], admin_hover["border-color"])
        self.assertEqual(USER[SCOPE + ".radio-wrap input:focus-visible"]["outline"].split(" var(")[0],
                         ADMIN[".radio-wrap input:focus-visible"]["outline"].split(" var(")[0])
        self.assertIn("var(--focus-ring", USER[SCOPE + ".radio-wrap input:focus-visible"]["outline"])

    def test_the_inner_dot_is_blue_for_usb_in_both(self):
        self.assertEqual(PAGE["#usbMatrix .radio-wrap .dot.audio-on"]["background"], "var(--video)")
        self.assertEqual(USER[SCOPE + "#usbMatrix .radio-wrap .dot.audio-on"]["background"], "var(--video)")

    def test_the_two_tokens_the_admin_page_gets_from_matrix_css_are_declared_with_its_values(self):
        root = ADMIN[":root"]
        user = USER[".usb-grid-area"]
        for token in ("--table", "--ring", "--video"):
            self.assertEqual(user[token], root[token], token)
        self.assertEqual(USER["html.light .usb-grid-area"]["--table"],
                         ADMIN[".light"]["--table"])

    def test_the_frozen_column_and_pinned_header_come_from_the_admin_file_not_a_copy(self):
        html = read("ui", "user", "index.html")
        self.assertIn("/ui/matrix/usb-matrix.css", html)
        self.assertNotIn("sticky", read("ui", "matrix", "usb-grid.css").replace("Keyboard focus", ""),
                         "usb-matrix.css owns stickiness; restating it here would be a second copy")

    def test_the_scroll_reserve_matches_the_admin_page(self):
        admin = rules(read("ui", "matrix", "usb-matrix.css"))[".matrix-area .matrix-wrap"]
        self.assertEqual(USER[SCOPE + ".matrix-wrap"]["scroll-padding-left"], admin["scroll-padding-left"])

    def test_every_colour_in_the_user_grid_is_a_token(self):
        text = re.sub(r"/\*.*?\*/", "", read("ui", "matrix", "usb-grid.css"), flags=re.S)
        for match in re.finditer(r"(?<![\w-])#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})\b", text):
            line = text[:match.start()].count("\n")
            context = text.splitlines()[line]
            # The two tokens the Admin page declares with literals, and the fallbacks inside var(...).
            self.assertTrue(context.strip().startswith(("--", "background: var", "border-color: var"))
                            or "var(" in context or "--" in context,
                            "a literal colour outside a token declaration: " + context)

    def test_tokens_used_are_defined_here_or_by_the_shells_own_stylesheets(self):
        used = set(re.findall(r"var\((--[\w-]+)", read("ui", "matrix", "usb-grid.css")))
        defined = set(re.findall(r"(--[\w-]+)\s*:", read("ui", "matrix", "usb-grid.css")))
        defined |= set(re.findall(r"(--[\w-]+)\s*:", read("ui", "remote.css")))
        defined |= set(re.findall(r"(--[\w-]+)\s*:", read("ui", "templates.css")))
        optional = {"--usb-frozen-col-w", "--danger"}       # both have fallbacks at the point of use
        missing = sorted(t for t in used - defined - optional)
        self.assertEqual(missing, [], "tokens used but defined nowhere the user page loads: %s" % missing)

    def test_hidden_wins_inside_the_grid_area(self):
        self.assertIn(".usb-grid-area [hidden] { display: none !important; }",
                      read("ui", "matrix", "usb-grid.css"))


if __name__ == "__main__":
    unittest.main()
