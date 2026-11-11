"""Runs the unification pass's JavaScript suites under unittest, so the mutation
harness and `python -m unittest` see them as well as `run_tests.py`."""
from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SUITES = ("firmware_status_test.js", "remote_nav_test.js",
          "remote_admin_ui_test.js", "user_groups_test.js")


class UnificationFrontend(unittest.TestCase):
    pass


def _make(name):
    @unittest.skipUnless(shutil.which("node"), "node is not installed")
    def test(self):
        done = subprocess.run(["node", str(ROOT / "tests" / name)], cwd=str(ROOT),
                              capture_output=True, text=True, timeout=120)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
    return test


for _name in SUITES:
    setattr(UnificationFrontend, "test_" + _name.replace(".", "_"), _make(_name))


if __name__ == "__main__":
    unittest.main()
