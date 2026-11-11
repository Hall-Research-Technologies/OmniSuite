"""The operating system is asked about network adapters at most once per window.

Measured before this existed: every GET /api/remote_access started a PowerShell
child (about 2.3 s, about 175 MB while it ran) and every GET /api/adapters
started one PowerShell and two ipconfig processes. Device Info asks for the
adapter list each time it opens.

These tests pin what remembering the answer is allowed to mean: it is brief,
shared by a burst, forgotten on request, never made of a failure, and never
used to decide something.
"""
import copy
import re
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import omni_adapters

ROOT = Path(__file__).resolve().parent.parent


def _row(name, address, up=True, hardware=True, desc="", media="802.3"):
    return {"id": "guid:" + name.lower(), "id_from": "guid", "name": name,
            "address": address, "netmask": "255.255.255.0", "prefix": 24,
            "cidr": "", "scan": "", "status": "Up" if up else "Disconnected",
            "up": up, "virtual": not hardware, "description": desc,
            "media": media, "hardware": hardware,
            "label": "%s — %s" % (name, address)}


class CacheCase(unittest.TestCase):
    """Turns the cache on for one test and always puts it back."""

    TTL = 30.0

    def setUp(self):
        self.scans = []
        self.rows = [_row("Ethernet", "198.18.0.10"),
                     _row("Wi-Fi", "198.18.1.10", media="802.11")]
        patcher = mock.patch.object(omni_adapters, "_scan_system",
                                    side_effect=self._scan)
        patcher.start()
        self.addCleanup(patcher.stop)
        ttl = mock.patch.object(omni_adapters, "CACHE_TTL", self.TTL)
        ttl.start()
        self.addCleanup(ttl.stop)
        omni_adapters.invalidate_cache()
        self.addCleanup(omni_adapters.invalidate_cache)

    def _scan(self):
        self.scans.append(time.monotonic())
        return copy.deepcopy(self.rows)


class RememberedBriefly(CacheCase):
    def test_a_burst_of_callers_scans_once(self):
        for _ in range(8):
            omni_adapters.enumerate_adapters()
        self.assertEqual(len(self.scans), 1)

    def test_include_down_shares_the_same_enumeration(self):
        self.rows.append(_row("Idle", "198.18.2.10", up=False))
        up = omni_adapters.enumerate_adapters()
        every = omni_adapters.enumerate_adapters(include_down=True)
        self.assertEqual(len(self.scans), 1)
        self.assertEqual({a["name"] for a in up}, {"Ethernet", "Wi-Fi"})
        self.assertEqual({a["name"] for a in every},
                         {"Ethernet", "Wi-Fi", "Idle"})

    def test_it_is_forgotten_after_the_window(self):
        omni_adapters.enumerate_adapters()
        with mock.patch.object(omni_adapters.time, "monotonic",
                               return_value=time.monotonic() + self.TTL + 1):
            omni_adapters.enumerate_adapters()
        self.assertEqual(len(self.scans), 2)

    def test_an_adapter_that_goes_away_is_seen_after_the_window(self):
        before = omni_adapters.enumerate_adapters()
        self.assertIn("Wi-Fi", [a["name"] for a in before])
        self.rows = [self.rows[0]]
        still = omni_adapters.enumerate_adapters()
        self.assertIn("Wi-Fi", [a["name"] for a in still])   # inside the window
        with mock.patch.object(omni_adapters.time, "monotonic",
                               return_value=time.monotonic() + self.TTL + 1):
            gone = omni_adapters.enumerate_adapters()
        self.assertEqual([a["name"] for a in gone], ["Ethernet"])
        # A saved selection of the vanished adapter is "missing", not served
        # from memory.
        self.assertEqual(
            omni_adapters.resolve_address("guid:wi-fi", "198.18.1.10", gone),
            ("", "adapter missing"))

    def test_the_lifetime_is_short_and_a_module_constant(self):
        # No hidden permanent state: a plain number a reader can see and a
        # test can set, and not so long that a changed network goes unseen.
        self.assertTrue(0 < type(self).TTL <= 60)
        source = (ROOT / "omni_adapters.py").read_text(encoding="utf-8")
        match = re.search(r"^CACHE_TTL = ([0-9.]+)$", source, re.M)
        self.assertIsNotNone(match)
        self.assertTrue(0 < float(match.group(1)) <= 60)


class ForgottenOnRequest(CacheCase):
    def test_invalidate_makes_the_next_call_ask_again(self):
        omni_adapters.enumerate_adapters()
        omni_adapters.invalidate_cache()
        omni_adapters.enumerate_adapters()
        self.assertEqual(len(self.scans), 2)

    def test_a_scan_that_overlaps_an_invalidation_is_not_remembered(self):
        # The enumeration that was already running describes a moment before
        # the caller who invalidated asked.
        started, release = threading.Event(), threading.Event()
        calls = []

        def slow_scan():
            calls.append(1)
            if len(calls) == 1:
                started.set()
                release.wait(5)
            return copy.deepcopy(self.rows)

        with mock.patch.object(omni_adapters, "_scan_system",
                               side_effect=slow_scan):
            first = threading.Thread(target=omni_adapters.enumerate_adapters)
            first.start()
            self.assertTrue(started.wait(5))
            omni_adapters.invalidate_cache()
            release.set()
            first.join(5)
            omni_adapters.enumerate_adapters()
        self.assertEqual(len(calls), 2)


class NeverMadeOfAFailure(CacheCase):
    def test_an_empty_answer_is_not_remembered(self):
        self.rows = []
        self.assertEqual(omni_adapters.enumerate_adapters(), [])
        self.rows = [_row("Ethernet", "198.18.0.10")]
        self.assertEqual([a["name"] for a in omni_adapters.enumerate_adapters()],
                         ["Ethernet"])
        self.assertEqual(len(self.scans), 2)

    def test_callers_get_copies(self):
        first = omni_adapters.enumerate_adapters()
        first[0]["address"] = "203.0.113.99"
        first.clear()
        second = omni_adapters.enumerate_adapters()
        self.assertEqual(len(second), 2)
        self.assertNotIn("203.0.113.99", [a["address"] for a in second])


class SharedByAConcurrentBurst(CacheCase):
    def test_threads_arriving_together_share_one_enumeration(self):
        gate = threading.Barrier(8)
        results = []

        def slow_scan():
            time.sleep(0.15)
            self.scans.append(1)
            return copy.deepcopy(self.rows)

        def caller():
            gate.wait(5)
            results.append(omni_adapters.enumerate_adapters())

        with mock.patch.object(omni_adapters, "_scan_system",
                               side_effect=slow_scan):
            threads = [threading.Thread(target=caller) for _ in range(8)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(10)
        self.assertEqual(len(results), 8)
        self.assertEqual(len(self.scans), 1)
        for result in results:
            self.assertEqual([a["name"] for a in result], ["Ethernet", "Wi-Fi"])


class WhatItMustNotChange(CacheCase):
    def test_the_physical_adapter_is_still_preferred_from_memory(self):
        self.rows = [
            _row("vEthernet (Default Switch)", "198.18.5.1", hardware=False,
                 desc="Hyper-V Virtual Ethernet Adapter"),
            _row("Ethernet", "198.18.0.10"),
        ]
        omni_adapters.enumerate_adapters()
        again = omni_adapters.enumerate_adapters()
        self.assertEqual(len(self.scans), 1)
        preferred = [a for a in again if a["preferred"]]
        self.assertEqual([a["name"] for a in preferred], ["Ethernet"])

    def test_the_default_for_a_suite_is_off(self):
        # tests/__init__.py turns the cache off for every other test; this
        # class turned it on for itself, so read the source of the package.
        text = (ROOT / "tests" / "__init__.py").read_text(encoding="utf-8")
        self.assertIn("omni_adapters.CACHE_TTL = 0.0", text)


class DecisionsAreNotAnsweredFromMemory(unittest.TestCase):
    """Source-level: the paths that act on the list drop the memory first."""

    @staticmethod
    def _code(path):
        text = (ROOT / path).read_text(encoding="utf-8")
        return "\n".join(line for line in text.splitlines()
                         if not line.lstrip().startswith("#"))

    def test_turning_remote_access_on_invalidates_first(self):
        body = self._code("omni_remote_api.py")
        start = body.index("def api_remote_access_set")
        end = body.index("def ", start + 10)
        handler = body[start:end]
        self.assertIn("omni_adapters.invalidate_cache()", handler)
        self.assertLess(handler.index("omni_adapters.invalidate_cache()"),
                        handler.index("omni_adapters.enumerate_adapters()"))

    def test_restoring_remote_access_invalidates_first(self):
        body = self._code("OmniMatrix_upgrade_server_v7_6y.py")
        start = body.index("def _remote_address_for")
        end = body.index("\ndef ", start + 10)
        function = body[start:end]
        self.assertLess(function.index("omni_adapters.invalidate_cache()"),
                        function.index("omni_adapters.enumerate_adapters()"))

    def test_refresh_is_an_explicit_request_on_both_reads(self):
        remote = self._code("omni_remote_api.py")
        start = remote.index("def api_remote_access_state")
        end = remote.index("@app.route", start)
        self.assertIn('request.args.get("refresh")', remote[start:end])
        server = self._code("OmniMatrix_upgrade_server_v7_6y.py")
        start = server.index("def api_adapters")
        end = server.index("\ndef ", start + 10)
        self.assertIn('request.args.get("refresh")', server[start:end])


if __name__ == "__main__":
    unittest.main()
