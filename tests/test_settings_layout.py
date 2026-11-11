"""Settings follows the family order, and nothing that was there is lost.

General / Device Communication, Firmware, Appearance, Remote Access, Backup and
Restore, Application Information -- the same order LumaSuite uses, so an operator
who knows one product finds the other. Reordering moved every control; this holds
that none went missing, because a control that vanished from a dialog nobody
re-reads would not be noticed until somebody needed it.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SETTINGS = (ROOT / "ui" / "settings.js").read_text(encoding="utf-8")
MODAL = SETTINGS[SETTINGS.index("const SETTINGS_MODAL_HTML"):
                 SETTINGS.index("const SETTINGS_BROWSER_HTML")]


def headings():
    return re.findall(r"<h4>([^<]+)</h4>", MODAL)


class OrderTests(unittest.TestCase):
    def test_the_sections_are_in_the_family_order(self):
        self.assertEqual(headings(), [
            "Device Communication", "Firmware", "Appearance",
            "Local Application Port", "Remote Access",
            "Backup and Restore", "Application Information"])

    def test_every_control_is_still_there(self):
        for control in ("cfg_username", "cfg_password", "cfg_fallback_password",
                        "cfg_ws_port", "cfg_timeout", "cfg_concurrency",
                        "cfg_firmware_path", "cfg_firmware_browse", "cfg_theme",
                        "cfg_template_choice", "cfg_preset_choice",
                        "cfg_light_background", "cfg_remote_adapter",
                        "cfg_remote_transport", "cfg_remote_toggle",
                        "cfg_backup_export", "cfg_backup_restore",
                        "cfg_app_version", "cfg_update_check", "cfg_fw_check",
                        "cfg_reset_ui", "cfg_save", "cfg_close", "cfg_user_guide"):
            with self.subTest(control=control):
                self.assertIn('id="%s"' % control, MODAL)

    def test_each_control_lives_in_its_own_section(self):
        parts = re.split(r"<h4>", MODAL)[1:]
        owner = {}
        for part in parts:
            name = part.split("</h4>")[0]
            for found in re.findall(r'id="(cfg_[a-z_]+)"', part):
                owner[found] = name
        self.assertEqual(owner["cfg_theme"], "Appearance")
        self.assertEqual(owner["cfg_firmware_path"], "Firmware")
        self.assertEqual(owner["cfg_ws_port"], "Device Communication")
        self.assertEqual(owner["cfg_app_version"], "Application Information")
        self.assertEqual(owner["cfg_backup_export"], "Backup and Restore")
        self.assertEqual(owner["cfg_remote_toggle"], "Remote Access")
        # The local port is its own section, never inside Remote Access: it is
        # about the address OmniSuite answers on at this computer.
        self.assertEqual(owner["cfg_localport_fixed"], "Local Application Port")

    def test_no_id_is_duplicated(self):
        ids = re.findall(r'\bid="([^"]+)"', MODAL)
        self.assertEqual(len(ids), len(set(ids)))

    def test_the_old_name_for_backup_is_gone(self):
        self.assertNotIn("Configuration Backup", MODAL)


if __name__ == "__main__":
    unittest.main()
