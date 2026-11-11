"""The account editor keeps security-related settings together, in a logical order.

Role first (it decides what the rest of the form means), then the name, password and
Direct Access, then what the account may use:
features, sources, displays, display groups. Direct Access sits directly under the
credentials rather than after the device lists, because it is a way of signing in.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HTML = (ROOT / "ui" / "admin" / "users.html").read_text(encoding="utf-8")
EDITOR = HTML[HTML.index('id="editor_backdrop"'):HTML.index('id="group_backdrop"')]


def position(marker):
    found = EDITOR.find(marker)
    assert found >= 0, marker + " is missing from the editor"
    return found


class EditorOrderTests(unittest.TestCase):
    def test_the_sections_run_in_the_family_order(self):
        order = [position(m) for m in (
            'id="f_role"', 'id="f_username"', 'id="f_password"',
            'id="direct_section"', 'class="f-feature"', 'id="src_list"',
            'id="dst_list"', 'id="grp_list"')]
        self.assertEqual(order, sorted(order))

    def test_direct_access_is_the_first_thing_after_the_credentials(self):
        user_only = EDITOR[position('id="user_only"'):]
        first_heading = re.search(r"<h4>([^<]+)</h4>", user_only).group(1)
        self.assertEqual(first_heading, "Direct access")

    def test_the_direct_access_controls_are_all_still_there(self):
        for control in ("f_direct", "direct_link", "direct_copy", "direct_regen",
                        "direct_detail", "direct_status"):
            self.assertIn('id="%s"' % control, EDITOR)

    def test_the_warning_is_the_family_wording(self):
        text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", EDITOR))
        self.assertIn("Anyone with this link can use the controls assigned to this "
                      "account. Treat the link like a password.", text)

    def test_there_is_no_multiview_control_left(self):
        self.assertNotRegex(EDITOR.lower(), r"multiview")

    def test_display_groups_are_a_permission_alongside_sources_and_displays(self):
        for heading in ("Sources they may route", "Displays they may control",
                        "Display groups they may route"):
            self.assertIn(heading, EDITOR)


if __name__ == "__main__":
    unittest.main()
