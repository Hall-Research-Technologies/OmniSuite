"""Runs tests/matrix_lifecycle_test.js under unittest so the mutation harness sees it."""
from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class MatrixLifecycleFrontend(unittest.TestCase):

    @unittest.skipUnless(shutil.which("node"), "node is not installed")
    def test_structure_state_delegation_and_prepared_snapshot(self):
        done = subprocess.run(["node", str(ROOT / "tests" / "matrix_lifecycle_test.js")],
                              cwd=str(ROOT), capture_output=True, text=True, timeout=120)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)


if __name__ == "__main__":
    unittest.main()
