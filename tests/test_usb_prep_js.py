"""Runs tests/usb_prep_test.js under unittest so the mutation harness sees it."""
from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class UsbPrepFrontend(unittest.TestCase):

    @unittest.skipUnless(shutil.which("node"), "node is not installed")
    def test_the_usb_matrix_is_painted_only_while_its_inventory_still_matches(self):
        done = subprocess.run(["node", str(ROOT / "tests" / "usb_prep_test.js")],
                              cwd=str(ROOT), capture_output=True, text=True, timeout=180)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)


if __name__ == "__main__":
    unittest.main()
