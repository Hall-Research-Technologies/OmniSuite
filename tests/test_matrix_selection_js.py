"""Runs tests/matrix_selection_test.js under unittest so the mutation harness sees it."""
from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class MatrixSelectionFrontend(unittest.TestCase):

    @unittest.skipUnless(shutil.which("node"), "node is not installed")
    def test_selection_is_held_by_identity_and_resolved_at_click_time(self):
        done = subprocess.run(["node", str(ROOT / "tests" / "matrix_selection_test.js")],
                              cwd=str(ROOT), capture_output=True, text=True, timeout=120)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("all checks passed", done.stdout)


if __name__ == "__main__":
    unittest.main()
