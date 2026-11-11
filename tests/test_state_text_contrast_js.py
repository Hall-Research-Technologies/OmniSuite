"""Runs tests/state_text_contrast_test.js under unittest so the mutation harness sees it."""
from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class StateTextContrastFrontend(unittest.TestCase):

    @unittest.skipUnless(shutil.which("node"), "node is not installed")
    def test_state_text_is_readable_in_every_preset(self):
        done = subprocess.run(["node", str(ROOT / "tests" / "state_text_contrast_test.js")],
                              cwd=str(ROOT), capture_output=True, text=True, timeout=120)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)


if __name__ == "__main__":
    unittest.main()
