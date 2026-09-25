"""Observation must not scale with the number of browsers.

A/V is derived from the host's cache and costs nothing. USB and Multiview are
not: reading USB state queries every endpoint, and a decoder's Multiview state
is three reads from that decoder. Measured before this existed, one client
fetching each surface five times cost 20 and 30 device calls and ten clients
cost 200 and 300 -- ten times, which is precisely what the architecture is
supposed to prevent.

These tests are about the mechanism that fixed it: a brief shared answer with
single-flight, and invalidation by whatever just changed the thing.
"""
from __future__ import annotations

import os
import sys
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("OMNI_SMOKE", "1")

from omni_statecache import StateCache             # noqa: E402


class SharedAnswerTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.lock = threading.Lock()

    def producer(self, value="v", delay=0.0):
        def produce():
            with self.lock:
                self.calls.append(time.time())
            if delay:
                time.sleep(delay)
            return value
        return produce

    def test_a_second_reader_inside_the_window_does_not_ask_again(self):
        cache = StateCache(ttl=5)
        for _ in range(10):
            self.assertEqual(cache.get("k", self.producer()), "v")
        self.assertEqual(len(self.calls), 1,
                         "ten reads must cost one device read")

    def test_ten_concurrent_readers_cost_one_read(self):
        """Single-flight, which a time-to-live alone does not give.

        Without it, ten browsers arriving together each find an empty cache and
        each start their own sweep of the equipment.
        """
        cache = StateCache(ttl=5)
        results = []
        produce = self.producer(delay=0.25)

        def worker():
            results.append(cache.get("k", produce))

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(len(results), 10)
        self.assertTrue(all(r == "v" for r in results))
        self.assertEqual(len(self.calls), 1,
                         "concurrent readers must share one read, not queue "
                         "ten of them")

    def test_the_answer_expires(self):
        cache = StateCache(ttl=0.1)
        cache.get("k", self.producer())
        time.sleep(0.2)
        cache.get("k", self.producer())
        self.assertEqual(len(self.calls), 2,
                         "a stale answer must not be served forever")

    def test_different_keys_do_not_share_an_answer(self):
        """One decoder's Multiview state is not another's."""
        cache = StateCache(ttl=5)
        cache.get("multiview:a", self.producer("a"))
        cache.get("multiview:b", self.producer("b"))
        self.assertEqual(len(self.calls), 2)

    def test_invalidation_makes_the_next_read_fresh(self):
        """An operation that changed the device resets the cadence."""
        cache = StateCache(ttl=60)
        cache.get("k", self.producer())
        cache.invalidate("k")
        cache.get("k", self.producer())
        self.assertEqual(len(self.calls), 2,
                         "after a write, the next read must see what happened")

    def test_invalidating_one_key_leaves_the_others(self):
        cache = StateCache(ttl=60)
        cache.get("a", self.producer())
        cache.get("b", self.producer())
        cache.invalidate("a")
        cache.get("a", self.producer())
        cache.get("b", self.producer())
        self.assertEqual(len(self.calls), 3)

    def test_a_failing_read_is_not_cached_and_does_not_strand_the_key(self):
        """A claimed key that is never released disables the surface for good.

        The project has hit this before: claiming a guard flag and returning
        early left it claimed for the life of the process.
        """
        cache = StateCache(ttl=5)

        def boom():
            raise RuntimeError("device did not answer")

        with self.assertRaises(RuntimeError):
            cache.get("k", boom)
        # The key must be usable immediately afterwards.
        self.assertEqual(cache.get("k", self.producer()), "v")
        self.assertEqual(len(self.calls), 1)

    def test_a_failure_does_not_block_a_waiting_reader_forever(self):
        cache = StateCache(ttl=5)
        started = threading.Event()

        def slow_failure():
            started.set()
            time.sleep(0.15)
            raise RuntimeError("device did not answer")

        def first():
            try:
                cache.get("k", slow_failure)
            except RuntimeError:
                pass

        thread = threading.Thread(target=first)
        thread.start()
        started.wait(timeout=5)
        # Arrives while the first read is in flight, and must still get an
        # answer rather than waiting on a reader that will never publish one.
        self.assertEqual(cache.get("k", self.producer()), "v")
        thread.join(timeout=5)


class WiringTests(unittest.TestCase):
    """The server reads the expensive surfaces through the cache."""

    def test_the_user_surfaces_go_through_the_shared_answer(self):
        import ast
        source = (ROOT / "OmniMatrix_upgrade_server_v7_6y.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        for name in ("_usb_state_snapshot", "_multiview_state_snapshot"):
            with self.subTest(function=name):
                function = next(n for n in ast.walk(tree)
                                if isinstance(n, ast.FunctionDef) and n.name == name)
                body = ast.dump(function)
                self.assertIn("_user_observation", body,
                              f"{name} must read through the shared answer, or "
                              "a second browser is a second sweep of the "
                              "equipment")

    def test_every_user_write_invalidates_what_it_changed(self):
        """A Show that left a cached picture would report the old one."""
        source = (ROOT / "omni_remote_api.py").read_text(encoding="utf-8")
        code = "\n".join(line.split("#", 1)[0] for line in source.splitlines())
        for endpoint in ("usb_route", "multiview_show", "multiview_switch"):
            with self.subTest(endpoint=endpoint):
                start = code.index("def api_user_" + endpoint)
                # To the end of that function, not a fixed number of
                # characters: a slice of 2500 made this assertion fail the day
                # the handler grew, which says nothing about invalidation.
                end = code.index("\n    def ", start + 10)
                block = code[start:end]
                self.assertIn("forget_observation", block,
                              f"api_user_{endpoint} changes device state and "
                              "must drop the shared answer it invalidated")



class StressTests(unittest.TestCase):
    """The cache is not correct merely because the simple measurement passes.

    Each of these is a named failure mode from the hardening brief. The ones
    that matter most are the poisoning cases: a cache that keeps serving a
    failed read, or that strands a key after an exception, disables a surface
    for the life of the process rather than for one request.
    """

    def setUp(self):
        self.calls = []
        self.lock = threading.Lock()

    def counting(self, value="v", delay=0.0, fail=False):
        def produce():
            with self.lock:
                self.calls.append(time.time())
            if delay:
                time.sleep(delay)
            if fail:
                raise RuntimeError("the device did not answer")
            return value
        return produce

    def test_many_simultaneous_cold_requests_cost_one_read(self):
        """Fifty browsers opening at once on a cold cache."""
        cache = StateCache(ttl=5)
        produce = self.counting(delay=0.2)
        results = []
        barrier = threading.Barrier(50, timeout=20)

        def worker():
            barrier.wait()
            results.append(cache.get("k", produce))

        threads = [threading.Thread(target=worker) for _ in range(50)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
        self.assertEqual(len(results), 50)
        self.assertEqual(len(self.calls), 1)

    def test_readers_crossing_the_expiry_boundary_do_not_stampede(self):
        """The moment an answer expires is when everyone arrives at once."""
        cache = StateCache(ttl=0.15)
        produce = self.counting(delay=0.05)
        cache.get("k", produce)
        time.sleep(0.2)                     # everything below is now stale

        barrier = threading.Barrier(20, timeout=20)

        def worker():
            barrier.wait()
            cache.get("k", produce)

        threads = [threading.Thread(target=worker) for _ in range(20)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
        # One for the warm-up, and one for the crowd.
        self.assertLessEqual(len(self.calls), 2,
                             "expiry let a stampede through")

    def test_a_writer_invalidating_during_a_read_is_not_lost(self):
        """The race that would serve a picture from before the write.

        A reader in flight publishes its answer after the writer invalidated.
        The next reader must not be served that answer as though it were
        current, because it describes the device before the change.
        """
        cache = StateCache(ttl=30)
        produced = threading.Event()
        proceed = threading.Event()

        def slow():
            produced.set()
            proceed.wait(timeout=10)
            return "before"

        reader = threading.Thread(target=lambda: cache.get("k", slow))
        reader.start()
        produced.wait(timeout=5)
        cache.invalidate("k")               # a writer changed the device
        proceed.set()
        reader.join(timeout=10)

        # Whatever the in-flight reader published, a fresh read must happen.
        value = cache.get("k", lambda: "after")
        self.assertEqual(value, "after",
                         "an answer from before the write was served after it")

    def test_a_failed_read_is_not_cached(self):
        cache = StateCache(ttl=30)
        with self.assertRaises(RuntimeError):
            cache.get("k", self.counting(fail=True))
        self.assertEqual(cache.get("k", self.counting("good")), "good")

    def test_repeated_failures_never_poison_the_key(self):
        cache = StateCache(ttl=30)
        for _ in range(20):
            with self.assertRaises(RuntimeError):
                cache.get("k", self.counting(fail=True))
        self.assertEqual(cache.get("k", self.counting("good")), "good",
                         "the key was permanently poisoned")

    def test_a_slow_read_does_not_block_a_different_key(self):
        cache = StateCache(ttl=5)
        slow_done = threading.Event()

        def slow():
            time.sleep(0.6)
            slow_done.set()
            return "slow"

        threading.Thread(target=lambda: cache.get("slow", slow)).start()
        time.sleep(0.05)
        started = time.time()
        cache.get("fast", lambda: "fast")
        self.assertLess(time.time() - started, 0.4,
                        "one slow device blocked an unrelated one")
        slow_done.wait(timeout=10)

    def test_concurrent_writers_invalidating_do_not_deadlock(self):
        cache = StateCache(ttl=1)
        stop = threading.Event()

        def reader():
            while not stop.is_set():
                cache.get("k", lambda: "v")

        def writer():
            while not stop.is_set():
                cache.invalidate("k")

        threads = ([threading.Thread(target=reader) for _ in range(6)]
                   + [threading.Thread(target=writer) for _ in range(4)])
        for thread in threads:
            thread.start()
        time.sleep(1.0)
        stop.set()
        for thread in threads:
            thread.join(timeout=10)
            self.assertFalse(thread.is_alive(), "a thread did not finish")

    def test_a_user_and_an_admin_watching_share_one_read(self):
        """Observation is keyed by what is observed, not by who is asking."""
        cache = StateCache(ttl=5)
        produce = self.counting()
        cache.get("multiview:192.0.2.1", produce)   # the user's browser
        cache.get("multiview:192.0.2.1", produce)   # the admin's page
        self.assertEqual(len(self.calls), 1)

    def test_invalidating_everything_is_safe_while_readers_are_active(self):
        """What a shutdown or a broad change does."""
        cache = StateCache(ttl=5)
        stop = threading.Event()
        errors = []

        def reader():
            try:
                while not stop.is_set():
                    cache.get("k", lambda: "v")
            except Exception as exc:       # pragma: no cover - failure path
                errors.append(exc)

        threads = [threading.Thread(target=reader) for _ in range(6)]
        for thread in threads:
            thread.start()
        for _ in range(50):
            cache.invalidate()
            time.sleep(0.005)
        stop.set()
        for thread in threads:
            thread.join(timeout=10)
        self.assertEqual(errors, [])

    def test_a_wholesale_invalidation_also_discards_an_in_flight_read(self):
        """The gap a per-key counter alone left open.

        A key that had never been invalidated individually had no counter
        entry, so dropping everything at once did not move it and the stale
        answer was stored. Confirmed by experiment before the epoch was added.
        """
        cache = StateCache(ttl=30)
        produced = threading.Event()
        proceed = threading.Event()

        def slow():
            produced.set()
            proceed.wait(timeout=10)
            return "before"

        reader = threading.Thread(target=lambda: cache.get("never_dropped", slow))
        reader.start()
        produced.wait(timeout=5)
        cache.invalidate()                  # everything, not this key by name
        proceed.set()
        reader.join(timeout=10)

        self.assertEqual(cache.get("never_dropped", lambda: "after"), "after")

    def test_a_discarded_answer_is_still_returned_to_whoever_asked(self):
        """It is that caller's best available answer; it is just not shared."""
        cache = StateCache(ttl=30)
        produced = threading.Event()
        proceed = threading.Event()
        result = []

        def slow():
            produced.set()
            proceed.wait(timeout=10)
            return "mine"

        reader = threading.Thread(
            target=lambda: result.append(cache.get("k", slow)))
        reader.start()
        produced.wait(timeout=5)
        cache.invalidate("k")
        proceed.set()
        reader.join(timeout=10)

        self.assertEqual(result, ["mine"])
        self.assertGreaterEqual(cache.stats()["discarded"], 1)


if __name__ == "__main__":
    unittest.main()
