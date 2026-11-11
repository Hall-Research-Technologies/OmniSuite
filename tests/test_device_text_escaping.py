"""Runs tests/device_text_escaping_test.js under unittest, so the mutation harness and
`python -m unittest` see the same check `run_tests.py` runs as a JS suite."""
from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class DeviceTextIsNeverMarkup(unittest.TestCase):

    @unittest.skipUnless(shutil.which("node"), "node is not installed")
    def test_the_builders_neutralise_hostile_device_text(self):
        done = subprocess.run(["node", str(ROOT / "tests" / "device_text_escaping_test.js")],
                              cwd=str(ROOT), capture_output=True, text=True, timeout=120)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)


if __name__ == "__main__":
    unittest.main()
