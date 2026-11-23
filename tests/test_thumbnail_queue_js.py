"""Runs tests/thumbnail_queue_test.js under unittest so the mutation harness sees it."""
from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class ThumbnailQueueFrontend(unittest.TestCase):

    @unittest.skipUnless(shutil.which("node"), "node is not installed")
    def test_status_reads_are_queued_and_not_repeated(self):
        done = subprocess.run(["node", str(ROOT / "tests" / "thumbnail_queue_test.js")],
                              cwd=str(ROOT), capture_output=True, text=True, timeout=120)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("scenarios passed", done.stdout)


if __name__ == "__main__":
    unittest.main()
