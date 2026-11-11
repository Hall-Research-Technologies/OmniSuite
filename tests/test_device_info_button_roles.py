"""Each Device Info control carries the role its purpose earns, and icon-only ones are named.

Primary is the action the page exists for (Scan, Upgrade); destructive is red and is
reserved for what empties or removes something; everything else is a neutral secondary.
An icon-only control carries an accessible name -- a tooltip is not one.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PAGE = (ROOT / "ui" / "index.html").read_text(encoding="utf-8")


def button(element_id):
    match = re.search(r'<button[^>]*\bid="%s"[^>]*>' % re.escape(element_id), PAGE)
    assert match, element_id + " is missing from Device Info"
    return match.group(0)


def classes(element_id):
    found = re.search(r'class="([^"]*)"', button(element_id))
    return set(found.group(1).split()) if found else set()


class RolesTests(unittest.TestCase):
    def test_the_actions_the_page_exists_for_are_primary(self):
        for element_id in ("scan_btn", "upgrade_btn"):
            self.assertEqual(classes(element_id) & {"secondary", "danger", "warn"}, set(), element_id)

    def test_destructive_controls_are_red(self):
        for element_id in ("clear_units_btn", "reboot_btn", "remove_btn"):
            self.assertIn("danger", classes(element_id), element_id)

    def test_utilities_are_secondary(self):
        for element_id in ("sync_passwords_btn", "set_ntp_btn", "slate_manager_btn", "export_units_btn",
                           "refresh_files_btn", "open_firmware_folder_btn"):
            self.assertIn("secondary", classes(element_id), element_id)

    def test_nothing_that_empties_the_inventory_is_neutral_or_primary(self):
        self.assertNotIn("secondary", classes("clear_units_btn"))


class NamesTests(unittest.TestCase):
    def test_icon_only_controls_have_an_accessible_name(self):
        for element_id in ("refresh_files_btn", "open_firmware_folder_btn"):
            self.assertIn("icon-only", classes(element_id))
            self.assertRegex(button(element_id), r'aria-label="[^"]+"', element_id)


class FooterTests(unittest.TestCase):
    def test_the_footer_bar_keeps_clear_of_the_fixed_version_text(self):
        css = re.sub(r"/\*.*?\*/", "", PAGE, flags=re.S)
        self.assertRegex(css, r"\.version-footer\{position:fixed;right:\d+px")
        reserved = re.search(r"\.footer-bar\{padding-right:(\d+)px\}", css)
        self.assertIsNotNone(reserved, "the footer bar must reserve room for the version text")
        self.assertGreaterEqual(int(reserved.group(1)), 140)


if __name__ == "__main__":
    unittest.main()
