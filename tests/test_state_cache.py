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
                block = code[start:start + 2500]
                self.assertIn("forget_observation", block,
                              f"api_user_{endpoint} changes device state and "
                              "must drop the shared answer it invalidated")


if __name__ == "__main__":
    unittest.main()
