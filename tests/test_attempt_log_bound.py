"""The per-thread read-attempt table cannot grow without bound.

The server starts a thread for every request, and `_note_attempt` kept a list
for each thread's identifier forever. Measured over a run of local route calls
(tracemalloc diff, 800 routes): the growth was attributed to this function and
nothing else. The table is a per-operation diagnostic, so ended threads are
forgotten and the table is capped.

No device is contacted; threads call the note function directly.
"""
from __future__ import annotations

import os
import sys
import threading
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("OMNI_SMOKE", "1")

import OmniMatrix_upgrade_server_v7_6y as srv           # noqa: E402


CAP = 64          # the documented bound; fixed here so a changed constant cannot hide itself


class AttemptTableBoundTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict(srv._MULTIVIEW_ATTEMPTS, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_threads(self, count, per_thread=1):
        def work():
            for n in range(per_thread):
                srv._note_attempt("192.0.2.1", "multiview", n + 1, "ok")
        for _ in range(count):
            t = threading.Thread(target=work)
            t.start()
            t.join()

    def test_the_cap_is_the_documented_one(self):
        self.assertEqual(srv._MULTIVIEW_ATTEMPT_THREAD_CAP, CAP)

    def test_many_ended_threads_leave_a_bounded_table(self):
        self.run_threads(CAP * 6)
        self.assertLessEqual(len(srv._MULTIVIEW_ATTEMPTS), CAP)

    def test_ended_threads_are_forgotten_before_live_ones(self):
        self.run_threads(CAP)                    # fills the table with dead threads
        srv._note_attempt("192.0.2.2", "multiview", 1, "ok")   # this thread is alive
        mine = threading.get_ident()
        self.assertIn(mine, srv._MULTIVIEW_ATTEMPTS)
        self.assertEqual(len(srv._MULTIVIEW_ATTEMPTS[mine]), 1)
        self.assertLess(len(srv._MULTIVIEW_ATTEMPTS), CAP)

    def test_a_threads_own_entries_stay_bounded(self):
        for n in range(500):
            srv._note_attempt("192.0.2.1", "multiview", 1, "ok")
        self.assertLessEqual(len(srv._attempt_log()), 200)

    def test_the_log_a_thread_reads_is_its_own(self):
        srv._note_attempt("192.0.2.3", "multiview", 2, "ok")
        seen = []
        t = threading.Thread(target=lambda: seen.append(srv._attempt_log()))
        t.start(); t.join()
        self.assertEqual(seen, [[]])
        self.assertEqual(len(srv._retried_reads()), 1)

    def test_more_live_threads_than_the_cap_still_cannot_exceed_it(self):
        release = threading.Event()
        started = []

        def work():
            srv._note_attempt("192.0.2.1", "multiview", 1, "ok")
            started.append(1)
            release.wait(10)
        threads = [threading.Thread(target=work) for _ in range(CAP + 20)]
        for t in threads:
            t.start()
        try:
            for _ in range(200):
                if len(started) == len(threads):
                    break
                threading.Event().wait(0.05)
            self.assertLessEqual(len(srv._MULTIVIEW_ATTEMPTS), CAP)
        finally:
            release.set()
            for t in threads:
                t.join()


if __name__ == "__main__":
    unittest.main()
