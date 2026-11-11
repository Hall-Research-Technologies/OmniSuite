"""Looking again at silent units must not make a Matrix read wait, or multiply.

Measured before this change, with the connect to a silent unit replaced by one
that sleeps the 0.6 s it costs:

    16 silent units, one reader     first read 1,204 ms, every ten seconds
    24 silent units, one reader     first read 1,806 ms
    12 pages open at once           192 probes issued for 16 units

The look-again belongs to the backend, not to whoever happened to read next, and
only one batch should be out at a time.
"""
import copy
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("OMNI_SMOKE", "1")

import omni_liveness                                          # noqa: E402
import OmniMatrix_upgrade_server_v7_6y as srv                 # noqa: E402

N_ENC, N_DEC = 4, 16
UNITS = (
    [{"ip": "192.0.2.%d" % (1 + i), "mac": "b8:98:b0:10:00:%02x" % i,
      "hostname": "enc-%02d" % i, "role": "encoder", "model": "hw-omni-e4521"}
     for i in range(N_ENC)]
    + [{"ip": "192.0.2.%d" % (101 + i), "mac": "b8:98:b0:20:00:%02x" % i,
        "hostname": "dec-%02d" % i, "role": "decoder", "model": "hw-omni-d4511"}
       for i in range(N_DEC)])
DECODER_IPS = [u["ip"] for u in UNITS if u["role"] == "decoder"]


class Base(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.folder.cleanup)
        for name in ("CACHE", "SCAN_RESULTS", "CSV_VIEW"):
            patcher = mock.patch.object(
                srv, name, Path(self.folder.name) / getattr(srv, name).name)
            patcher.start()
            self.addCleanup(patcher.stop)
        srv.CACHE.write_text(json.dumps(copy.deepcopy(UNITS)), encoding="utf-8")

        self.live = srv._liveness
        self.addCleanup(self.live.forget)
        self.addCleanup(srv.await_startup_tasks, 20)
        previous = omni_liveness._probe_override
        self.addCleanup(omni_liveness.set_probe_override, previous)

        self.probes = []
        self.lock = threading.Lock()
        self.release = threading.Event()
        self.release.set()                 # probes answer at once unless a test says not
        self.answer = False

        def probe(ip):
            with self.lock:
                self.probes.append(ip)
            self.release.wait(8)
            return self.answer

        omni_liveness.set_probe_override(probe)
        self.client = srv.app.test_client()

    def seed_silent(self, ips, age=30.0):
        self.live.forget()
        now = self.live._now()
        for ip in ips:
            self.live._seen[ip] = (False, now - age, "seed")

    def read(self):
        started = time.perf_counter()
        reply = self.client.get("/api/state")
        self.assertEqual(reply.status_code, 200)
        return reply.get_json(), time.perf_counter() - started


class TheReadDoesNotWait(Base):
    def test_a_read_returns_while_the_probes_are_still_out(self):
        self.seed_silent(DECODER_IPS)
        self.release.clear()                       # every probe now hangs
        body, elapsed = self.read()
        self.assertLess(elapsed, 2.0,
                        "the read waited for the probes (%.1f s)" % elapsed)
        decoders = {d["ip"]: d for d in body["decoders"]}
        for ip in DECODER_IPS:
            self.assertIs(decoders[ip].get("online"), False)
        self.release.set()

    def test_what_the_batch_learned_is_shown_by_the_next_read(self):
        self.seed_silent(DECODER_IPS[:3])
        self.answer = True                         # they have come back
        self.read()
        self.assertTrue(srv.await_startup_tasks(20))
        body, _ = self.read()
        decoders = {d["ip"]: d for d in body["decoders"]}
        for ip in DECODER_IPS[:3]:
            self.assertIs(decoders[ip].get("online"), True)

    def test_nothing_recorded_offline_starts_no_work(self):
        self.live.forget()
        before = threading.active_count()
        self.read()
        self.assertEqual(self.probes, [])
        self.assertEqual([t for t in threading.enumerate()
                          if t.name == "offline-refresh"], [])
        self.assertLessEqual(threading.active_count(), before + 1)


class OneBatchAtATime(Base):
    def test_many_readers_do_not_multiply_the_probes(self):
        self.seed_silent(DECODER_IPS)
        self.release.clear()
        gate = threading.Barrier(8)
        done = []

        def reader():
            client = srv.app.test_client()
            gate.wait(10)
            done.append(client.get("/api/state").status_code)

        threads = [threading.Thread(target=reader) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(20)
        self.assertEqual(done, [200] * 8)
        # Wait until the one batch has reached every unit it is going to.
        deadline = time.monotonic() + 10
        while len(self.probes) < len(DECODER_IPS) and time.monotonic() < deadline:
            time.sleep(0.05)
        self.release.set()
        self.assertTrue(srv.await_startup_tasks(20))
        self.assertEqual(sorted(self.probes), sorted(DECODER_IPS),
                         "each silent unit should be looked at once, not once "
                         "per reader")

    def test_the_claim_is_released_when_the_batch_ends(self):
        self.seed_silent(DECODER_IPS[:2])
        self.read()
        self.assertTrue(srv.await_startup_tasks(20))
        self.assertIsNotNone(self.live.begin_refresh(min_age=0.0))
        # The claim just taken is held until its work runs; run it.
        self.live._refresh_gate.release()

    def test_a_probe_that_raises_does_not_leave_the_claim_held(self):
        self.seed_silent(DECODER_IPS[:2])

        def boom(ip):
            raise RuntimeError("synthetic")

        omni_liveness.set_probe_override(boom)
        self.read()
        self.assertTrue(srv.await_startup_tasks(20))
        # check() treats a raising probe as "not answering", so the batch ends
        # normally and the next one can start.
        work = self.live.begin_refresh(min_age=0.0)
        self.assertIsNotNone(work, "the claim was left held")
        work()

    def test_a_machine_that_cannot_start_a_thread_still_looks_and_lets_go(self):
        self.seed_silent(DECODER_IPS[:2])          # and they stay silent

        class CannotRegister(list):
            def append(self, item):
                raise RuntimeError("can't start new thread")

        with mock.patch.object(srv, "_startup_threads", CannotRegister()):
            self.read()
        # Done where it stood, and the claim is not held.
        self.assertEqual(sorted(self.probes), sorted(DECODER_IPS[:2]))
        work = self.live.begin_refresh(min_age=0.0)
        self.assertIsNotNone(work, "the claim was left held")
        work()

    def test_a_claimed_batch_blocks_a_second_claim_until_it_finishes(self):
        self.seed_silent(DECODER_IPS[:2])
        first = self.live.begin_refresh()
        self.assertIsNotNone(first)
        self.assertIsNone(self.live.begin_refresh(),
                          "a second batch was started over the same units")
        first()
        self.assertIsNotNone(self.live.begin_refresh(min_age=0.0))
        self.live._refresh_gate.release()

    def test_the_synchronous_refresh_is_single_flight_too(self):
        self.seed_silent(DECODER_IPS[:4])
        self.release.clear()
        results = []
        first = threading.Thread(
            target=lambda: results.append(self.live.refresh_offline()))
        first.start()
        deadline = time.monotonic() + 5
        while not self.probes and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertEqual(self.live.refresh_offline(), [],
                         "a second caller started a batch over the first")
        self.release.set()
        first.join(20)
        self.assertEqual(len(results[0]), 4)


class WhatItMustNotChange(Base):
    def test_the_thread_is_registered_so_a_caller_can_wait_for_it(self):
        self.seed_silent(DECODER_IPS[:2])
        self.release.clear()
        self.read()
        named = [t for t in srv._startup_threads if t.name == "offline-refresh"]
        self.assertTrue(named, "the background look-again is not registered")
        self.release.set()
        self.assertTrue(srv.await_startup_tasks(20))

    def test_the_liveness_module_still_starts_no_thread_of_its_own(self):
        source = Path(omni_liveness.__file__).read_text(encoding="utf-8")
        for forbidden in ("threading.Timer", "threading.Thread(", "setInterval",
                          "while True"):
            self.assertNotIn(forbidden, source)

    def test_a_unit_that_was_seen_a_moment_ago_is_not_looked_at_again(self):
        self.seed_silent(DECODER_IPS[:2], age=2.0)
        self.read()
        self.assertTrue(srv.await_startup_tasks(20))
        self.assertEqual(self.probes, [])


if __name__ == "__main__":
    unittest.main()
