"""Firmware availability: what Hall Research publishes, compared with what is installed.

Nothing here reaches Hall Research. Every fetch is a stub, the test package sets
OMNI_FIRMWARE_CHECK=0 so the application never arms the real one, and the
checker holds no network source until something supplies one.

What these hold, in the brief's own terms:

  * a check on launch and then every seven days, in the background;
  * a bounded request, an offline Internet that costs nothing, and the last good
    answer kept (marked stale) when a later check fails;
  * versions compared numerically, and an unreadable one is Unknown, never
    "update";
  * the model is mapped to its own product page and nowhere else;
  * availability is not upgrade capability: a model OmniSuite cannot upgrade can
    still have a status, and a status never makes it an upgrade target;
  * nothing is ever downloaded or installed.
"""
from __future__ import annotations

import copy
import json
import os
import re
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("OMNI_SMOKE", "1")

import omni_firmware as fw                           # noqa: E402


def payload(*versions, date="2026-01-01", extra=None):
    """A product page the way Hall Research's JSON shapes it."""
    entries = [{"version": [{"type": "paragraph", "text": v, "spans": []}],
                "release_date": date,
                "firmware_link": {"link_type": "Web",
                                  "url": "https://example.invalid/fw.zip"}}
               for v in versions]
    body = {"data": {"firmware": entries}}
    body["data"].update(extra or {})
    return body


class Clock:
    def __init__(self, start=1_000_000.0):
        self.t = start

    def __call__(self):
        return self.t


class FakeTimer:
    """Records what would have been scheduled; never starts a thread."""
    made = []

    def __init__(self, interval, function):
        self.interval, self.function = interval, function
        self.daemon = False
        self.started = self.cancelled = False
        FakeTimer.made.append(self)

    def start(self):
        self.started = True

    def cancel(self):
        self.cancelled = True


def make_checker(fetch=None, clock=None, directory=None):
    directory = Path(directory or tempfile.mkdtemp(prefix="omni-fw-"))
    FakeTimer.made = []
    checker = fw.FirmwareChecker(directory / "firmware_cache.json", fetch=fetch,
                                 now=clock or Clock(), timer_factory=FakeTimer)
    return checker, directory


# ---------------------------------------------------------------- versions ---
class VersionTests(unittest.TestCase):
    def test_numeric_not_string_order(self):
        self.assertEqual(fw.compare("2.0.9", "2.0.10"), -1)
        self.assertEqual(fw.compare("1.0.10", "1.0.9"), 1)
        self.assertEqual(fw.compare("2.1.2", "2.1.2"), 0)

    def test_the_formats_seen_on_real_equipment(self):
        cases = [
            ("V1.0.2.3", "1.0.2", 1),      # a 4th component is ahead, not behind
            ("v1.0.2.6", "1.0.2", 1),
            ("2.0.3b", "2.0.3", 1),        # a suffix is ahead of none
            ("2.0.3b", "2.0.4", -1),
            ("1.0.2", "1.0.2.0", 0),       # zero padding
            ("  2.0.0 ", "2.0.0", 0),      # stray whitespace
            ("1.2.7.1", "1.2.7", 1),
        ]
        for installed, published, expected in cases:
            with self.subTest(installed=installed, published=published):
                self.assertEqual(fw.compare(installed, published), expected)

    def test_unreadable_is_none_never_an_update(self):
        for junk in ("garbage", "", None, 5, "1..2", "1.2.x", "v", "1.2.3-beta",
                     ["2.0.0"]):
            with self.subTest(junk=junk):
                self.assertIsNone(fw.parse_version(junk))
                self.assertIsNone(fw.compare(junk, "2.0.0"))
                self.assertIsNone(fw.compare("2.0.0", junk))

    def test_highest_version_does_not_trust_the_order(self):
        self.assertEqual(fw.highest_version(["1.0.1", "1.0.3", "1.0.2"]), "1.0.3")
        self.assertEqual(fw.highest_version(["2.0.9", " 2.0.10"]), "2.0.10")
        self.assertEqual(fw.highest_version(["junk", "1.0.0"]), "1.0.0")
        self.assertIsNone(fw.highest_version(["junk", ""]))
        self.assertIsNone(fw.highest_version([]))


class PayloadTests(unittest.TestCase):
    def test_picks_the_highest_not_the_first(self):
        found = fw.latest_from_payload(payload("1.0.1", "1.0.3", "1.0.2"))
        self.assertEqual(found["latest"], "1.0.3")

    def test_strips_whitespace_and_tolerates_a_missing_or_malformed_date(self):
        self.assertEqual(fw.latest_from_payload(payload(" 2.0.0"))["latest"], "2.0.0")
        self.assertIsNone(fw.latest_from_payload(payload("2.0.0", date=None))["release_date"])
        self.assertEqual(fw.latest_from_payload(payload("2.0.0", date="0024-08-21"))["latest"],
                         "2.0.0")

    def test_no_firmware_is_not_an_error(self):
        for body in ({"data": {"firmware": []}}, {"data": {}}, {"data": None},
                     {}, [], "x", {"data": {"firmware": "nope"}},
                     {"data": {"firmware": [None, 3, {"version": "x"}]}}):
            with self.subTest(body=body):
                self.assertIsNone(fw.latest_from_payload(body)["latest"])

    def test_the_download_link_is_never_read(self):
        found = fw.latest_from_payload(payload("1.0.0"))
        self.assertEqual(set(found), {"latest", "release_date"})
        source = (ROOT / "omni_firmware.py").read_text(encoding="utf-8")
        code = re.sub(r'""".*?"""', "", source, flags=re.S)
        code = "\n".join(l.split("#")[0] for l in code.split("\n"))
        self.assertNotIn("firmware_link", code)
        self.assertNotIn("subprocess", code)
        self.assertNotIn("shutil", code)
        self.assertNotIn("urlretrieve", code)


# ------------------------------------------------------------------ mapping ---
class ModelMappingTests(unittest.TestCase):
    def test_each_model_maps_to_its_own_page(self):
        for model, slug in (("HW-OMNI-D4511", "hw-omni-d4511"),
                            ("hw-omni-e4521", "hw-omni-e4521"),
                            ("HW-OMNI-E4111-WP", "hw-omni-e4111-wp"),
                            ("HW-OMNI-E4111", "hw-omni-e4111"),
                            ("AT-OMNI-111", "at-omni-111"),
                            ("AT-OMNI-111-WP", "at-omni-111-wp"),
                            ("  at-omni-121 ", "at-omni-121")):
            with self.subTest(model=model):
                self.assertEqual(fw.slug_for(model), slug)

    def test_a_model_is_matched_whole_never_as_a_prefix(self):
        # Hall Research has no HW-OMNI-D4111-WP page; a prefix rule would have
        # reported the D4111's firmware against it.
        self.assertIsNone(fw.slug_for("HW-OMNI-D4111-WP"))
        self.assertIsNone(fw.slug_for("hw-omni-e41110"))
        self.assertIsNone(fw.slug_for(""))
        self.assertIsNone(fw.slug_for(None))

    def test_both_names_of_the_usb_extenders_reach_the_same_page(self):
        for model in ("HW-OMNI-324", "AT-OMNI-324"):
            self.assertEqual(fw.slug_for(model), "at-omni-324")
        for model in ("HW-OMNI-311", "AT-OMNI-311"):
            self.assertEqual(fw.slug_for(model), "at-omni-311")

    def test_every_slug_is_acceptable_to_the_url_guard(self):
        for slug in set(fw.MODEL_SLUGS.values()):
            self.assertTrue(fw.valid_product_url(slug), slug)


# ------------------------------------------------------------------ the URL ---
class UrlGuardTests(unittest.TestCase):
    def test_only_the_expected_host_and_path(self):
        self.assertEqual(fw.valid_product_url("hw-omni-d4511"),
                         "https://hallresearch.com/api/products/hw-omni-d4511")
        for bad in ("", None, "../x", "a/b", "A", "x" * 80, "x.y", "a b",
                    "x@evil.example", "x\\y", "-x", "//evil.example/x"):
            with self.subTest(slug=bad):
                self.assertEqual(fw.valid_product_url(bad), "")

    def test_fetch_product_refuses_a_bad_slug_without_a_request(self):
        opener = mock.Mock()
        with self.assertRaises(fw.FirmwareSourceError):
            fw.fetch_product("../etc", opener=opener)
        opener.open.assert_not_called()


class FetchProductTests(unittest.TestCase):
    """fetch_product through a stub opener, so no socket is ever opened."""

    class Response:
        def __init__(self, data):
            self.data = data

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self, limit=-1):
            return self.data if limit < 0 else self.data[:limit]

    def opener(self, result):
        opener = mock.Mock()
        if isinstance(result, Exception):
            opener.open.side_effect = result
        else:
            opener.open.return_value = self.Response(result)
        return opener

    def test_a_good_page_is_parsed(self):
        data = json.dumps(payload("2.1.2")).encode()
        self.assertEqual(fw.latest_from_payload(
            fw.fetch_product("hw-omni-d4511", self.opener(data)))["latest"], "2.1.2")

    def test_the_request_is_bounded_in_time(self):
        opener = self.opener(json.dumps(payload("1.0.0")).encode())
        fw.fetch_product("hw-omni-d4511", opener)
        self.assertEqual(opener.open.call_args.kwargs["timeout"], fw.REQUEST_TIMEOUT)
        self.assertLessEqual(fw.REQUEST_TIMEOUT, 10)

    def test_a_404_is_a_missing_page_not_an_outage(self):
        err = urllib.error.HTTPError("u", 404, "nf", {}, None)
        with self.assertRaises(fw.FirmwareSourceError) as caught:
            fw.fetch_product("hw-omni-d4511", self.opener(err))
        self.assertTrue(caught.exception.missing)
        self.assertFalse(caught.exception.transport)

    def test_unreachable_and_server_errors_are_transport_failures(self):
        for error in (urllib.error.URLError("no route"), TimeoutError(),
                      ConnectionResetError(),
                      urllib.error.HTTPError("u", 503, "x", {}, None)):
            with self.subTest(error=type(error).__name__):
                with self.assertRaises(fw.FirmwareSourceError) as caught:
                    fw.fetch_product("hw-omni-d4511", self.opener(error))
                self.assertTrue(caught.exception.transport)

    def test_an_oversize_or_non_json_body_is_refused(self):
        big = b"{" + b" " * (fw.MAX_RESPONSE_BYTES + 10) + b"}"
        for body in (big, b"<html>nope</html>", b"[1,2]", b"\xff\xfe", b""):
            with self.subTest(size=len(body)):
                with self.assertRaises(fw.FirmwareSourceError):
                    fw.fetch_product("hw-omni-d4511", self.opener(body))

    def test_redirects_are_not_followed(self):
        handler = fw._NoRedirects()
        self.assertIsNone(handler.redirect_request(None, None, 302, "x", {}, "http://evil.example/"))


# ---------------------------------------------------------------- the checker ---
class CheckerTests(unittest.TestCase):
    def pages(self, _table=None, **by_slug):
        table = _table if _table is not None else by_slug

        def fetch(slug):
            self.calls.append(slug)
            value = table.get(slug, payload("9.9.9"))
            if isinstance(value, Exception):
                raise value
            return value
        return fetch

    def setUp(self):
        self.calls = []

    def test_no_network_source_means_no_request(self):
        checker, _ = make_checker(fetch=None)
        result = checker.check()
        self.assertFalse(result["ran"])
        self.assertFalse(checker.network_enabled)
        self.assertEqual(self.calls, [])

    def test_the_application_holds_no_source_at_import(self):
        import OmniMatrix_upgrade_server_v7_6y as srv
        self.assertEqual(os.environ.get("OMNI_FIRMWARE_CHECK"), "0")
        self.assertFalse(srv._firmware_checker.network_enabled)

    def test_a_check_records_the_catalogue_and_the_time(self):
        clock = Clock()
        checker, _ = make_checker(self.pages(**{"hw-omni-d4511": payload("2.1.2")}),
                                  clock)
        result = checker.check(["hw-omni-d4511"])
        self.assertTrue(result["ok"])
        snap = checker.snapshot()
        self.assertEqual(snap["products"]["hw-omni-d4511"]["latest"], "2.1.2")
        self.assertEqual(snap["last_success"], clock.t)
        self.assertEqual(snap["state"], "ready")
        self.assertFalse(snap["stale"])

    def test_up_to_date_available_and_unknown(self):
        checker, _ = make_checker(self.pages(**{"hw-omni-d4511": payload("2.1.2")}))
        checker.check(["hw-omni-d4511"])
        self.assertEqual(checker.describe("HW-OMNI-D4511", "2.1.2")["label"], "Up to Date")
        self.assertEqual(checker.describe("HW-OMNI-D4511", "2.1.3")["label"], "Up to Date")
        available = checker.describe("HW-OMNI-D4511", "2.0.9")
        self.assertEqual((available["label"], available["latest"]), ("Available", "2.1.2"))
        unreadable = checker.describe("HW-OMNI-D4511", "garbage")
        self.assertEqual(unreadable["label"], "Unknown")
        self.assertTrue(unreadable["reason"])
        self.assertEqual(checker.describe("HW-OMNI-D4511", None)["label"], "Unknown")

    def test_never_na_for_a_model_that_has_a_status(self):
        checker, _ = make_checker(self.pages(**{"hw-omni-d4511": payload("2.1.2")}))
        checker.check(["hw-omni-d4511"])
        for model, version in (("HW-OMNI-D4511", "2.1.2"), ("nonsense", "1.0"),
                               ("HW-OMNI-324", "2.0.9")):
            entry = checker.describe(model, version)
            self.assertNotIn("N/A", json.dumps(entry))
            self.assertIn(entry["label"], ("Up to Date", "Available", "Unknown"))

    def test_before_any_check_everything_is_unknown_and_says_so(self):
        checker, _ = make_checker(None)
        entry = checker.describe("HW-OMNI-D4511", "2.1.2")
        self.assertEqual(entry["label"], "Unknown")
        self.assertEqual(entry["reason"], fw.REASON_NOT_CHECKED)
        self.assertEqual(checker.snapshot()["state"], "pending")

    def test_usb_extenders_have_a_status_and_it_is_unknown_with_the_reason(self):
        pages = {"at-omni-324": {"data": {"firmware": []}},
                 "at-omni-311": {"data": {"firmware": []}}}
        checker, _ = make_checker(self.pages(**pages))
        checker.check(["at-omni-324", "at-omni-311"])
        for model in ("HW-OMNI-324", "AT-OMNI-324", "HW-OMNI-311"):
            entry = checker.describe(model, "2.0.9")
            self.assertEqual(entry["label"], "Unknown")
            self.assertEqual(entry["reason"], fw.REASON_NO_FIRMWARE)
            self.assertIsNone(entry["latest"], "another product's version is not borrowed")

    def test_an_unmapped_model_says_there_is_no_page(self):
        checker, _ = make_checker(self.pages())
        checker.check(["hw-omni-d4511"])
        self.assertEqual(checker.describe("HW-OMNI-D4111-WP", "2.1.2")["reason"],
                         fw.REASON_NO_PAGE)

    # ---- failure keeps the last good answer ---------------------------
    def test_a_failed_check_keeps_the_last_good_result_and_marks_it_stale(self):
        clock = Clock()
        results = {"hw-omni-d4511": payload("2.1.2")}
        checker, _ = make_checker(self.pages(results), clock)
        checker.check(["hw-omni-d4511"])
        good_time = clock.t

        clock.t += 8 * 86400
        results["hw-omni-d4511"] = fw.FirmwareSourceError("down", transport=True)
        outcome = checker.check(["hw-omni-d4511"])
        self.assertFalse(outcome["ok"])
        snap = checker.snapshot()
        self.assertEqual(snap["products"]["hw-omni-d4511"]["latest"], "2.1.2")
        self.assertEqual(snap["last_success"], good_time, "the success time is not moved")
        self.assertTrue(snap["stale"])
        # ...and a known status never became Unknown because of it.
        entry = checker.describe("HW-OMNI-D4511", "2.0.0")
        self.assertEqual(entry["label"], "Available")
        self.assertTrue(entry["stale"])

    def test_an_unreachable_source_stops_after_the_first_request(self):
        down = fw.FirmwareSourceError("unreachable", transport=True)
        checker, _ = make_checker(lambda slug: self.calls.append(slug) or (_ for _ in ()).throw(down))
        checker.check()
        self.assertEqual(len(self.calls), 1,
                         "if Hall Research cannot be reached, the rest is only traffic")

    def test_a_missing_page_is_recorded_and_the_sweep_continues(self):
        missing = fw.FirmwareSourceError("nf", missing=True)
        checker, _ = make_checker(self.pages(**{"hw-omni-d4111": missing}))
        outcome = checker.check(["hw-omni-d4111", "hw-omni-d4511"])
        self.assertTrue(outcome["ok"])
        self.assertEqual(self.calls, ["hw-omni-d4111", "hw-omni-d4511"])
        self.assertFalse(checker.snapshot()["products"]["hw-omni-d4111"]["published"])

    def test_a_stub_that_raises_something_else_is_not_fatal(self):
        def broken(slug):
            raise ValueError("boom")
        checker, _ = make_checker(broken)
        outcome = checker.check(["hw-omni-d4511"])
        self.assertEqual((outcome["ran"], outcome["ok"]), (True, False))

    def test_a_second_check_while_one_runs_is_refused_not_stacked(self):
        import threading
        gate, entered = threading.Event(), threading.Event()

        def slow(slug):
            entered.set()
            gate.wait(5)
            return payload("1.0.0")

        checker, _ = make_checker(slow)
        thread = threading.Thread(target=checker.check, args=(["hw-omni-d4511"],))
        thread.start()
        self.assertTrue(entered.wait(5))
        self.assertEqual(checker.check()["reason"], "already_running")
        self.assertEqual(checker.check_async()["reason"], "already_running")
        self.assertTrue(checker.snapshot()["running"])
        gate.set()
        thread.join(5)
        self.assertFalse(checker.snapshot()["running"])

    def test_check_async_runs_in_the_background_and_can_be_waited_for(self):
        checker, _ = make_checker(self.pages(**{"hw-omni-d4511": payload("2.1.2")}))
        self.assertTrue(checker.check_async()["started"])
        self.assertTrue(checker.wait(10))
        self.assertEqual(checker.snapshot()["state"], "ready")

    # ---- persistence ----------------------------------------------------
    def test_the_catalogue_survives_a_restart(self):
        clock = Clock()
        checker, directory = make_checker(self.pages(**{"hw-omni-d4511": payload("2.1.2")}), clock)
        checker.check(["hw-omni-d4511"])
        again = fw.FirmwareChecker(directory / "firmware_cache.json", now=clock)
        self.assertEqual(again.describe("HW-OMNI-D4511", "2.0.0")["label"], "Available")
        self.assertEqual(again.snapshot()["last_success"], clock.t)

    def test_a_malformed_cache_is_quarantined_and_starts_empty(self):
        directory = Path(tempfile.mkdtemp(prefix="omni-fw-"))
        path = directory / "firmware_cache.json"
        for text in ("{ nope", json.dumps([]), json.dumps({"products": "x"})):
            with self.subTest(text=text[:12]):
                path.write_text(text, encoding="utf-8")
                checker = fw.FirmwareChecker(path)
                self.assertEqual(checker.snapshot()["products"], {})
                self.assertTrue(path.with_suffix(".corrupt").exists())
                path.with_suffix(".corrupt").unlink()

    def test_hostile_cache_entries_cannot_inject_a_slug_or_a_version(self):
        directory = Path(tempfile.mkdtemp(prefix="omni-fw-"))
        path = directory / "firmware_cache.json"
        path.write_text(json.dumps({"products": {
            "../../etc": {"latest": "9.9.9", "published": True},
            "hw-omni-d4511": {"latest": "<script>", "published": True,
                              "fetched_at": "yesterday"},
            "hw-omni-e4521": {"latest": "2.0.0", "published": True,
                              "fetched_at": float("nan")},
            "at-omni-111": "not a record"}, "last_success": -5,
            "last_attempt": True}), encoding="utf-8")
        checker = fw.FirmwareChecker(path)
        products = checker.snapshot()["products"]
        self.assertNotIn("../../etc", products)
        self.assertNotIn("at-omni-111", products)
        self.assertIsNone(products["hw-omni-d4511"]["latest"])
        self.assertEqual(products["hw-omni-d4511"]["fetched_at"], 0.0)
        self.assertIsNone(checker.snapshot()["last_success"])

    def test_the_file_is_written_atomically(self):
        source = (ROOT / "omni_firmware.py").read_text(encoding="utf-8")
        self.assertIn("_atomic_write_json", source)


# ----------------------------------------------------------------- scheduling ---
class SchedulingTests(unittest.TestCase):
    def ok(self, slug):
        return payload("2.1.2")

    def test_the_launch_check_is_shortly_after_start(self):
        checker, _ = make_checker(self.ok)
        checker.arm(first=True)
        timer = FakeTimer.made[-1]
        self.assertEqual(timer.interval, fw.LAUNCH_DELAY_SECONDS)
        self.assertTrue(timer.started and timer.daemon)

    def test_after_a_success_the_next_check_is_seven_days_out(self):
        clock = Clock()
        checker, _ = make_checker(self.ok, clock)
        checker.check(["hw-omni-d4511"])
        checker.arm()
        self.assertEqual(FakeTimer.made[-1].interval, 7 * 86400)
        clock.t += 3 * 86400
        self.assertEqual(checker.seconds_until_next(), 4 * 86400)

    def test_the_seven_day_window_is_the_brief_s(self):
        self.assertEqual(fw.CHECK_INTERVAL_SECONDS, 7 * 24 * 3600)

    def test_after_a_failure_the_retry_is_bounded_not_immediate(self):
        clock = Clock()
        down = fw.FirmwareSourceError("down", transport=True)
        checker, _ = make_checker(lambda s: (_ for _ in ()).throw(down), clock)
        checker.check()
        self.assertEqual(checker.seconds_until_next(), fw.RETRY_AFTER_FAILURE_SECONDS)
        self.assertGreaterEqual(fw.RETRY_AFTER_FAILURE_SECONDS, 3600)

    def test_a_restart_loop_is_not_a_request_per_launch(self):
        clock = Clock()
        checker, directory = make_checker(self.ok, clock)
        checker.check(["hw-omni-d4511"])
        clock.t += 60                                   # restarted a minute later
        again = fw.FirmwareChecker(directory / "firmware_cache.json", fetch=self.ok,
                                   now=clock, timer_factory=FakeTimer)
        self.assertGreaterEqual(again.seconds_until_next(first=True),
                                fw.MIN_GAP_BETWEEN_ATTEMPTS - 60)

    def test_firing_checks_and_schedules_the_next(self):
        calls = []
        checker, _ = make_checker(lambda s: calls.append(s) or payload("1.0.0"))
        checker.arm(first=True)
        before = len(FakeTimer.made)
        FakeTimer.made[-1].function()
        self.assertTrue(calls, "the scheduled check did not run")
        self.assertEqual(len(FakeTimer.made), before + 1, "the next one was not armed")

    def test_arming_replaces_a_pending_timer(self):
        checker, _ = make_checker(self.ok)
        checker.arm(first=True)
        first = FakeTimer.made[-1]
        checker.arm()
        self.assertTrue(first.cancelled)

    def test_stopping_cancels_and_nothing_rearms(self):
        checker, _ = make_checker(self.ok)
        checker.arm(first=True)
        timer = FakeTimer.made[-1]
        checker.stop()
        self.assertTrue(timer.cancelled)
        count = len(FakeTimer.made)
        checker.arm()
        self.assertEqual(len(FakeTimer.made), count)

    def test_without_a_source_nothing_is_armed(self):
        checker, _ = make_checker(None)
        checker.arm(first=True)
        self.assertEqual(FakeTimer.made, [])

    def test_scheduling_never_uses_a_blocking_sleep(self):
        source = (ROOT / "omni_firmware.py").read_text(encoding="utf-8")
        self.assertNotIn("time.sleep", source)

    def test_startup_arms_it_off_the_startup_threads_and_honours_the_guard(self):
        server = (ROOT / "OmniMatrix_upgrade_server_v7_6y.py").read_text(encoding="utf-8")
        start = server.index("def start_background_startup_tasks")
        body = server[start:server.index("_startup_tasks_started = False", start)]
        self.assertIn('os.environ.get("OMNI_FIRMWARE_CHECK") != "0"', body)
        self.assertIn("_firmware_checker.arm(first=True)", body)
        # The three existing threads are unchanged.
        self.assertEqual(body.count("threading.Thread("), 1)


# -------------------------------------------------------------------- the API ---
class StatusApiTests(unittest.TestCase):
    def setUp(self):
        import OmniMatrix_upgrade_server_v7_6y as srv
        self.srv = srv
        self.checker, _ = make_checker(
            lambda slug: payload("2.1.2") if slug != "at-omni-324"
            else {"data": {"firmware": []}})
        patcher = mock.patch.object(srv, "_firmware_checker", self.checker)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.units = [
            {"ip": "192.0.2.10", "mac": "b8:98:b0:00:00:51", "model": "hw-omni-d4511",
             "version": "2.1.2"},
            {"ip": "192.0.2.11", "mac": "b8:98:b0:00:00:52", "model": "hw-omni-e4521",
             "version": "2.0.0"},
            {"ip": "192.0.2.12", "mac": "b8:98:b0:00:00:53", "model": "hw-omni-d4111-wp",
             "version": "2.1.2"},
        ]
        patcher = mock.patch.object(srv, "_load_cache",
                                    lambda: copy.deepcopy(self.units))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.extenders = [{"ip": "192.0.2.20", "mac": "f8:22:85:00:00:51",
                           "model": "HW-OMNI-324", "version": "2.0.9"}]
        patcher = mock.patch.object(srv, "_standalone_extender_rows",
                                    lambda: copy.deepcopy(self.extenders))
        patcher.start()
        self.addCleanup(patcher.stop)
        # The upgrade guard asks the extender inventory which addresses are
        # extenders; say that this one is.
        patcher = mock.patch.object(srv, "_extender_addresses",
                                    lambda: {"192.0.2.20"})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = srv.app.test_client()

    def test_the_status_reads_the_catalogue_and_touches_no_network(self):
        self.checker.check()
        body = self.client.get("/api/firmware_status").get_json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["units"]["192.0.2.10"]["label"], "Up to Date")
        self.assertEqual(body["units"]["192.0.2.11"]["label"], "Available")
        self.assertEqual(body["units"]["192.0.2.12"]["label"], "Unknown")
        self.assertEqual(body["units"]["192.0.2.10"]["model"], "hw-omni-d4511")
        self.assertEqual(body["check"]["state"], "ready")

    def test_opening_settings_issues_no_request_at_all(self):
        """Counted, not assumed: the read must not reach the fetcher or start a check."""
        self.checker.check()
        calls = []
        real = self.checker._fetch
        self.checker._fetch = lambda *a, **k: calls.append(a) or real(*a, **k)
        started = []
        original = self.checker.check_async
        self.checker.check_async = lambda *a, **k: started.append(1) or original(*a, **k)
        self.addCleanup(lambda: setattr(self.checker, "check_async", original))
        for _ in range(3):
            self.assertEqual(self.client.get("/api/firmware_status").status_code, 200)
        self.assertEqual(calls, [], "reading the summary reached the network")
        self.assertEqual(started, [], "reading the summary started a check")

    def test_a_usb_extender_has_a_status_and_is_still_not_an_upgrade_target(self):
        self.checker.check()
        body = self.client.get("/api/firmware_status").get_json()
        entry = body["extenders"]["192.0.2.20"]
        self.assertEqual(entry["label"], "Unknown")
        self.assertEqual(entry["reason"], fw.REASON_NO_FIRMWARE)
        # Availability is not capability: the upgrade guard is unmoved.
        refused = self.client.post("/api/upgrade", json={
            "file": "fw.vpup2", "targets": ["192.0.2.20"]})
        self.assertEqual(refused.status_code, 400)
        self.assertEqual(refused.get_json()["reason"], "firmware_not_supported")

    def test_a_published_version_never_makes_a_model_upgradeable(self):
        """Even an extender the source *did* publish firmware for stays refused."""
        self.checker._catalog["at-omni-324"] = {
            "latest": "9.9.9", "release_date": None, "published": True,
            "fetched_at": 1.0}
        entry = self.client.get("/api/firmware_status").get_json()["extenders"]["192.0.2.20"]
        self.assertEqual(entry["label"], "Available")
        refused = self.client.post("/api/upgrade", json={
            "file": "fw.vpup2", "targets": ["192.0.2.20"]})
        self.assertEqual(refused.status_code, 400)

    def test_the_manual_check_is_refused_when_no_source_is_enabled(self):
        self.checker._fetch = None
        reply = self.client.post("/api/firmware_status/check", json={})
        self.assertEqual(reply.status_code, 409)

    def test_the_manual_check_answers_at_once_and_runs_in_the_background(self):
        reply = self.client.post("/api/firmware_status/check", json={})
        self.assertEqual(reply.status_code, 202)
        self.assertTrue(reply.get_json()["started"])
        self.assertTrue(self.checker.wait(10))
        body = self.client.get("/api/firmware_status").get_json()
        self.assertEqual(body["check"]["state"], "ready")

    def test_both_routes_are_administrative(self):
        import omni_access
        for rule in ("/api/firmware_status", "/api/firmware_status/check"):
            self.assertEqual(omni_access.classify(rule)[0], omni_access.Access.ADMIN)


class UiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.settings = (ROOT / "ui" / "settings.js").read_text(encoding="utf-8")
        cls.index = (ROOT / "ui" / "index.html").read_text(encoding="utf-8")

    def test_settings_has_a_firmware_section_with_a_manual_check(self):
        for needle in ('<h4>Firmware</h4>', 'id="cfg_fw_status"', 'id="cfg_fw_check"'):
            self.assertIn(needle, self.settings)

    def test_device_info_loads_the_module_and_decorates_after_the_rows(self):
        self.assertIn("/ui/firmware-status.js", self.index)
        self.assertIn("applyToTable", self.index)


if __name__ == "__main__":
    unittest.main()
