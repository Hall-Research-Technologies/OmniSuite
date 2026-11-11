"""Runs the Clear Units front-end checks under unittest so the mutation harness sees them.

Three scripts, each executed rather than read:

  clear_units_test.js      the confirmation dialog and View Assignments
  remote_admin_ui_test.js  the assignment summary on the Device names page
  settings_remote_test.js  the protected-inventory line in Settings

remote_admin_ui_test.js covers the whole administration page, and other changes
to that page are not this file's subject, so only the checks named here are
required to pass from it.
"""
from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

DEVICE_NAMES_CHECKS = (
    "each device shows what refers to it, and Unassigned when nothing does",
    "the summary expands to the names, marks Direct Access, and collapses",
    "opening the tab re-reads the assignments and repaints in place",
    "an unreadable answer leaves a dash, never \"Unassigned\"",
    "the audit list labels the two clear events in English",
)


def node(script):
    return subprocess.run(["node", str(ROOT / "tests" / script)], cwd=str(ROOT),
                          capture_output=True, text=True, timeout=180)


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class ClearUnitsFrontend(unittest.TestCase):
    def test_the_confirmation_and_the_assignments_dialog(self):
        done = node("clear_units_test.js")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        # Node exits 0 when a scenario never settles and nothing is left to run,
        # so a zero status alone is not a pass: the summary has to be there.
        self.assertIn("all passed", done.stdout, done.stdout + done.stderr)

    def test_the_device_names_page_shows_what_refers_to_each_device(self):
        done = node("remote_admin_ui_test.js")
        lines = done.stdout.splitlines()
        for name in DEVICE_NAMES_CHECKS:
            ran = [l for l in lines if name in l]
            self.assertTrue(ran, "the check never ran: " + name)
            self.assertTrue(all(l.startswith("ok") for l in ran),
                            "\n".join(ran) + "\n" + done.stderr)

    def test_settings_says_assigned_devices_are_protected_while_remote_access_is_on(self):
        done = node("settings_remote_test.js")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)


if __name__ == "__main__":
    unittest.main()
