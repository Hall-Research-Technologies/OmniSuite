"""Runs the restore confirmation's front-end checks under unittest, so the mutation
harness can see them.

    restore_impact_test.js    the sentences a restore owes the accounts it affects
    settings_backup_test.js   the backup and restore flow, and the dialogs it uses
    restore_confirm_focus_test.js   the Restore confirmation opens with Cancel focused

Each is executed rather than read, and a zero status is not a pass on its own: Node
exits 0 when a scenario never settles, so the summary line has to be there too.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def node(script):
    return subprocess.run(["node", str(ROOT / "tests" / script)], cwd=str(ROOT),
                          capture_output=True, text=True, timeout=180)


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class RestoreFrontend(unittest.TestCase):
    def passed(self, script):
        done = node(script)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        match = re.search(r"(\d+)/(\d+) checks passed", done.stdout)
        self.assertIsNotNone(match, "no summary line: " + done.stdout + done.stderr)
        self.assertEqual(match.group(1), match.group(2), done.stdout)
        self.assertGreater(int(match.group(2)), 0)

    def test_the_account_impact_sentences(self):
        self.passed("restore_impact_test.js")

    def test_the_backup_and_restore_flow(self):
        self.passed("settings_backup_test.js")

    def test_the_restore_confirmation_opens_on_cancel(self):
        self.passed("restore_confirm_focus_test.js")


if __name__ == "__main__":
    unittest.main()
