"""Runs the restricted User page's JS suites under unittest so the mutation harness sees them."""
from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class UserPageFrontend(unittest.TestCase):

    @unittest.skipUnless(shutil.which("node"), "node is not installed")
    def test_routing_and_picking(self):
        done = subprocess.run(["node", str(ROOT / "tests" / "user_routing_test.js")],
                              cwd=str(ROOT), capture_output=True, text=True, timeout=180)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)

    @unittest.skipUnless(shutil.which("node"), "node is not installed")
    def test_shell(self):
        done = subprocess.run(["node", str(ROOT / "tests" / "user_shell_test.js")],
                              cwd=str(ROOT), capture_output=True, text=True, timeout=180)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)

    @unittest.skipUnless(shutil.which("node"), "node is not installed")
    def test_groups_offline_and_revision(self):
        done = subprocess.run(["node", str(ROOT / "tests" / "user_groups_test.js")],
                              cwd=str(ROOT), capture_output=True, text=True, timeout=180)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)

    @unittest.skipUnless(shutil.which("node"), "node is not installed")
    def test_matrix_and_drag_and_drop(self):
        done = subprocess.run(["node", str(ROOT / "tests" / "user_matrix_test.js")],
                              cwd=str(ROOT), capture_output=True, text=True, timeout=180)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)

    @unittest.skipUnless(shutil.which("node"), "node is not installed")
    def test_selected_state_is_filled(self):
        done = subprocess.run(["node", str(ROOT / "tests" / "user_selected_state_test.js")],
                              cwd=str(ROOT), capture_output=True, text=True, timeout=180)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)


if __name__ == "__main__":
    unittest.main()
