"""A request that starts a verification sweep must not leave it running past the test.

`GET /api/cache` launches `_verify_cache_in_background()` when the cache is stale
(initially always). That worker is a daemon thread that reads `_ws_send_recv`
through the module global when it fires, so a sweep begun by one test
and still running during the next one lands its connection attempts in whatever
recorder that next test has installed. It was seen once as
`test_a_list_with_one_stranger_is_refused_whole` reporting two attempts to
198.51.100.7 -- the address `test_remote_credential_filter` leaves in the cache
-- when the suite shared a machine with another run.

Production code that starts a thread has to record it where a caller can wait on
it (`_startup_threads` / `await_startup_tasks()`); this one did not. These tests
hold the worker mid-sweep with an event, so the crossing is reproduced
deterministically rather than by timing.
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

KNOWN = "198.51.100.7"
UNIT = {"ip": KNOWN, "mac": "B8:98:B0:00:00:01", "model": "AT-OMNI-111",
        "hostname": "unit-a", "password": "Synthetic-Device-Pw-0003"}


class VerificationSweepIsAwaitableTests(unittest.TestCase):

    def setUp(self):
        import OmniMatrix_upgrade_server_v7_6y as srv
        self.srv = srv
        self.release = threading.Event()
        self.calls = []
        self.entered = threading.Event()

        def hold(url, payload, timeout=0, *a, **k):
            self.calls.append(url)
            self.entered.set()
            self.release.wait(10)
            return {"config": {}, "error": None}

        saved = (srv._cache_verification_in_progress, srv._cache_last_verified,
                 srv._cache_startup_verified)

        def restore():
            self.release.set()
            srv.await_startup_tasks(timeout=15.0)
            (srv._cache_verification_in_progress, srv._cache_last_verified,
             srv._cache_startup_verified) = saved

        # Registered before the patchers so it runs after them is NOT wanted:
        # cleanups run last-in first-out, and this must run while the stubs are
        # still installed, so it is added after them below.
        for patcher in (
                mock.patch.object(srv, "_load_cache", return_value=[dict(UNIT)]),
                mock.patch.object(srv, "_ws_send_recv", side_effect=hold)):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(restore)
        srv._cache_verification_in_progress = False
        srv._cache_last_verified = 0
        srv._cache_startup_verified = True      # skip the synchronous first-request pass

    def _start_sweep(self):
        response = self.srv.app.test_client().get("/api/cache")
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertTrue(self.entered.wait(5),
                        "precondition: the request did not start a verification sweep "
                        "(no connection attempt was made), so there is nothing to isolate")

    def test_the_sweep_a_request_starts_is_registered_for_waiting(self):
        self._start_sweep()
        alive = [t for t in self.srv._startup_threads if t.is_alive()]
        self.assertTrue(alive,
                        "the verification worker is running but is not in _startup_threads, "
                        "so await_startup_tasks() cannot let it finish before the next test")

    def test_awaiting_lets_the_sweep_finish_inside_this_test(self):
        self._start_sweep()
        self.release.set()
        self.assertTrue(self.srv.await_startup_tasks(timeout=10.0),
                        "the sweep was still running after await_startup_tasks()")
        self.assertFalse(self.srv._cache_verification_in_progress,
                         "the sweep finished but left its in-progress claim set")

    def test_finished_workers_are_not_kept_forever(self):
        """The registry is for waiting, not history: a sweep every five minutes must not grow it."""
        self.release.set()
        before = len(self.srv._startup_threads)
        for _ in range(5):
            self.srv._cache_verification_in_progress = False
            self.srv._cache_last_verified = 0
            self.entered.clear()
            self._start_sweep()
            self.assertTrue(self.srv.await_startup_tasks(timeout=10.0))
        self.assertLessEqual(len(self.srv._startup_threads), before + 1,
                             "finished verification workers accumulate in _startup_threads")

class AwaitFollowsThreadsThatStartThreadsTests(unittest.TestCase):

    def test_a_worker_registered_after_the_first_pass_is_still_awaited(self):
        import OmniMatrix_upgrade_server_v7_6y as srv
        gate = threading.Event()
        late = threading.Thread(target=lambda: gate.wait(10), daemon=True)

        register = threading.Event()

        def first():
            # registers a second worker part-way through the wait, as the startup
            # verification thread does with the cache sweep
            register.wait(10)
            srv._startup_threads.append(late)
            late.start()

        head = threading.Thread(target=first, daemon=True)
        saved = list(srv._startup_threads)
        srv._startup_threads[:] = [head]
        self.addCleanup(lambda: (gate.set(), srv._startup_threads.__setitem__(slice(None), saved)))
        head.start()
        threading.Timer(0.2, register.set).start()   # after await has taken its first look
        threading.Timer(0.5, gate.set).start()
        self.assertTrue(srv.await_startup_tasks(timeout=5.0),
                        "await returned while a worker registered during the wait was still alive")
        self.assertFalse(late.is_alive())


if __name__ == "__main__":
    unittest.main()
