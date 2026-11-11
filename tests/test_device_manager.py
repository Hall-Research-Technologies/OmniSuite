"""Device Manager: the name, uptime, and the AT-OMNI-311/324 USB extenders.

Four things, each measured on live OmniStream hardware before it was written down:

  * the page is the Device Manager (it was the Upgrade Manager), though firmware
    *actions* are still called upgrades;
  * uptime is `systeminfo.config.uptime`, an integer number of seconds that
    advanced one second per second on every encoder and decoder model, and it is
    shown from that and from nowhere else;
  * a standalone AT-OMNI-311/324 appears on Device Info, once, with only what its
    own protocol established;
  * and it can never be a firmware target -- refused by the server, not merely
    left without a button.

Hardware fenced: every transport is replaced and nothing here reaches a device.
"""
from __future__ import annotations

import copy
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("OMNI_SMOKE", "1")

import OmniMatrix_upgrade_server_v7_6y as srv              # noqa: E402

ENC = {"ip": "192.0.2.11", "mac": "b8:98:b0:00:00:01", "hostname": "enc-a",
       "model": "hw-omni-e4521", "role": "encoder", "type": "encoder",
       "details": {"systeminfo": {"config": {"uptime": 6391980}}}}
DEC = {"ip": "192.0.2.21", "mac": "b8:98:b0:00:00:11", "hostname": "dec-a",
       "model": "hw-omni-d4511", "role": "decoder", "type": "decoder",
       "details": {"systeminfo": {"config": {"uptime": 180}}}}


def extender(ip, model, classification="STANDALONE", **extra):
    base = {"ip": ip, "mac": "00:1B:13:04:00:%02X" % (int(ip.split(".")[-1]) % 256),
            "display_model": model, "device_type": model.replace("HW-", "AT-"),
            "classification": classification, "firmware": "1.9.4", "online": True,
            "integrated": classification == "INTEGRATED"}
    base.update(extra)
    return base


class Base(unittest.TestCase):
    def setUp(self):
        self.units = [copy.deepcopy(ENC), copy.deepcopy(DEC)]
        self.extenders = []
        self.uploads = []
        self.dir = Path(tempfile.mkdtemp(prefix="omni-devmgr-"))
        (self.dir / "fw.bin").write_bytes(b"not firmware")

        def upload(ip, path, **kwargs):
            self.uploads.append(ip)
            return {"ok": False, "stage": "upload", "error": "stub"}

        saved_fw = srv.app.config.get("FIRMWARE_PATH")
        saved_verified = srv._cache_startup_verified

        def restore():
            srv.app.config["FIRMWARE_PATH"] = saved_fw
            srv._cache_startup_verified = saved_verified
        self.addCleanup(restore)
        srv.app.config["FIRMWARE_PATH"] = str(self.dir)
        srv._cache_startup_verified = True            # no startup sweep of the units
        for patcher in (
                mock.patch.object(srv, "_load_cache", side_effect=lambda *a, **k: copy.deepcopy(self.units)),
                mock.patch.object(srv, "_verify_cache_in_background"),
                mock.patch.object(srv, "_usb_extender_view",
                                  side_effect=lambda *a, **k: {"devices": copy.deepcopy(self.extenders)}),
                mock.patch.object(srv, "_http_upload_file", side_effect=upload),
                mock.patch.object(srv, "_ws_send_recv", side_effect=AssertionError("a device was contacted"))):
            patcher.start()
            self.addCleanup(patcher.stop)
        # A GET of /api/cache starts a background verification sweep that reads the
        # transport through module globals when it fires. Wait for it while these
        # stubs are still installed, so it cannot land in another test's recorder.
        self.addCleanup(lambda: self.assertTrue(
            srv.await_startup_tasks(timeout=15.0),
            "a verification sweep started by this test was still running at teardown"))
        self.client = srv.app.test_client()

    def cache(self):
        response = self.client.get("/api/cache")
        self.assertEqual(response.status_code, 200)
        return response.get_json()

    def upgrade(self, targets):
        return self.client.post("/api/upgrade", json={"file": "fw.bin", "targets": targets})


class TheNameTests(unittest.TestCase):
    def read(self, relative):
        return (ROOT / relative).read_text(encoding="utf-8")

    def test_the_page_is_the_device_manager(self):
        html = self.read("ui/index.html")
        self.assertIn("<title>OmniSuite Device Manager</title>", html)
        self.assertIn("<h1>OmniSuite Device Manager</h1>", html)

    def test_no_live_page_still_calls_it_the_upgrade_manager(self):
        # The one intended mention: the guide tells operators the old name once, so
        # someone who knew it as the Upgrade Manager can find it.
        for path in (ROOT / "ui").rglob("*"):
            if path.name == "user-guide.html":
                continue
            if path.suffix in (".html", ".js", ".css"):
                text = path.read_text(encoding="utf-8", errors="replace")
                self.assertNotRegex(text, r"(?i)upgrade manager", str(path.relative_to(ROOT)))

    def test_firmware_actions_keep_the_word_upgrade(self):
        html = self.read("ui/index.html")
        self.assertIn("Upgrade Selected", html)

    def test_the_guide_names_the_device_manager_and_the_old_name_once(self):
        guide = self.read("ui/user-guide.html")
        self.assertIn("OmniSuite Device Manager", guide)
        self.assertEqual(len(re.findall(r"(?i)upgrade manager", guide)), 1)


class UptimeParsingTests(unittest.TestCase):
    def test_a_device_reading_is_taken_in_seconds(self):
        self.assertEqual(srv._uptime_seconds(ENC), 6391980)
        self.assertEqual(srv._uptime_seconds(DEC), 180)

    def test_zero_is_a_reading_and_not_unknown(self):
        self.assertEqual(srv._uptime_seconds({"details": {"systeminfo": {"config": {"uptime": 0}}}}), 0)

    def test_a_float_is_truncated(self):
        self.assertEqual(srv._uptime_seconds({"uptime": 59.9}), 59)

    def test_anything_else_is_unknown_never_zero(self):
        for bad in (None, "", "123", "abc", True, False, -1, float("nan"), float("inf"),
                    10 ** 12, [], {}, {"a": 1}):
            with self.subTest(bad=bad):
                self.assertIsNone(srv._uptime_seconds({"uptime": bad}))
                self.assertIsNone(srv._uptime_seconds(
                    {"details": {"systeminfo": {"config": {"uptime": bad}}}}))

    def test_a_unit_with_no_systeminfo_has_no_uptime(self):
        for unit in ({}, None, {"details": None}, {"details": {"systeminfo": None}},
                     {"details": {"systeminfo": {"config": None}}}, "garbage"):
            with self.subTest(unit=unit):
                self.assertIsNone(srv._uptime_seconds(unit))

    def test_nothing_but_the_device_is_a_source(self):
        """Not discovery time, not cache age, not when this process started."""
        text = (ROOT / "OmniMatrix_upgrade_server_v7_6y.py").read_text(encoding="utf-8")
        start = text.index("def _uptime_seconds")
        body = text[start:text.index("def _standalone_extender_rows")]
        for forbidden in ("time.time", "monotonic", "first_seen", "last_seen", "discovered_at"):
            self.assertNotIn(forbidden, body)


class UptimeOnDeviceInfoTests(Base):
    def test_encoder_and_decoder_uptime_ride_the_cache_response(self):
        by_ip = {u["ip"]: u for u in self.cache()["units"]}
        self.assertEqual(by_ip["192.0.2.11"]["uptime_seconds"], 6391980)
        self.assertEqual(by_ip["192.0.2.21"]["uptime_seconds"], 180)

    def test_a_unit_that_reported_none_carries_null(self):
        self.units.append({"ip": "192.0.2.31", "mac": "b8:98:b0:00:00:21", "model": "hw-omni-d4111",
                           "role": "decoder", "details": {"systeminfo": {"config": {"uptime": "soon"}}}})
        by_ip = {u["ip"]: u for u in self.cache()["units"]}
        self.assertIsNone(by_ip["192.0.2.31"]["uptime_seconds"])

    def test_the_cache_is_not_given_a_second_uptime_to_go_stale(self):
        self.cache()
        for unit in self.units:
            self.assertNotIn("uptime_seconds", unit)


class StandaloneExtenderTests(Base):
    def rows(self):
        return self.cache()["standalone_extenders"]

    def test_a_standalone_324_and_311_appear(self):
        self.extenders = [extender("192.0.2.138", "HW-OMNI-324"), extender("192.0.2.139", "HW-OMNI-311")]
        rows = self.rows()
        self.assertEqual([(r["ip"], r["model"], r["type"], r["version"]) for r in rows],
                         [("192.0.2.138", "HW-OMNI-324", "USB Extender", "1.9.4"),
                          ("192.0.2.139", "HW-OMNI-311", "USB Extender", "1.9.4")])

    def test_an_integrated_or_unconfirmed_endpoint_is_never_a_second_row(self):
        self.extenders = [extender("192.0.2.140", "HW-OMNI-324", "INTEGRATED"),
                          extender("192.0.2.141", "HW-OMNI-324", "UNCONFIRMED"),
                          extender("192.0.2.142", "HW-OMNI-324", "")]
        self.assertEqual(self.rows(), [])

    def test_a_mixed_inventory_keeps_units_and_extenders_apart(self):
        self.extenders = [extender("192.0.2.138", "HW-OMNI-324"), extender("192.0.2.140", "HW-OMNI-324", "INTEGRATED")]
        body = self.cache()
        self.assertEqual({u["ip"] for u in body["units"]}, {"192.0.2.11", "192.0.2.21"})
        self.assertEqual([r["ip"] for r in body["standalone_extenders"]], ["192.0.2.138"])

    def test_missing_optional_fields_are_empty_not_invented(self):
        self.extenders = [{"ip": "192.0.2.150", "classification": "STANDALONE"}]
        row = self.rows()[0]
        self.assertEqual((row["mac"], row["model"], row["version"], row["online"]), ("", "", "", False))
        self.assertNotIn("hostname", row)
        self.assertNotIn("uptime_seconds", row)
        self.assertNotIn("timezone", row)

    def test_an_unknown_model_is_shown_as_the_protocol_named_it(self):
        self.extenders = [extender("192.0.2.151", "HW-OMNI-999")]
        row = self.rows()[0]
        self.assertEqual(row["model"], "HW-OMNI-999")
        self.assertTrue(row["firmware_supported"], "an unknown model is not claimed unsupported by name")

    def test_a_record_with_no_address_is_not_a_row(self):
        self.extenders = [{"classification": "STANDALONE", "display_model": "HW-OMNI-324"}]
        self.assertEqual(self.rows(), [])

    def test_the_rows_are_ordered_numerically_not_as_text(self):
        self.extenders = [extender("192.0.2.100", "HW-OMNI-324"), extender("192.0.2.20", "HW-OMNI-311"),
                          extender("192.0.2.3", "HW-OMNI-324")]
        self.assertEqual([r["ip"] for r in self.rows()], ["192.0.2.3", "192.0.2.20", "192.0.2.100"])

    def test_the_rows_carry_no_credential_or_device_detail(self):
        self.extenders = [extender("192.0.2.138", "HW-OMNI-324", password="secret", dhcp_server="x")]
        self.assertEqual(set(self.rows()[0]),
                         {"ip", "mac", "model", "version", "type", "online", "firmware_supported"})

    def test_every_324_and_311_is_flagged_as_having_no_firmware_support(self):
        self.extenders = [extender("192.0.2.138", "HW-OMNI-324"), extender("192.0.2.139", "AT-OMNI-311")]
        self.assertEqual([r["firmware_supported"] for r in self.rows()], [False, False])

    def test_an_extender_failure_does_not_take_the_inventory_with_it(self):
        with mock.patch.object(srv, "_usb_extender_view", side_effect=RuntimeError("boom")):
            body = self.cache()
        self.assertEqual(body["standalone_extenders"], [])
        self.assertEqual(len(body["units"]), 2)


class NoFirmwareForExtendersTests(Base):
    def refused(self, response, expected):
        self.assertEqual(response.status_code, 400, response.get_data(as_text=True))
        body = response.get_json()
        self.assertEqual(body["reason"], "firmware_not_supported")
        self.assertEqual(sorted(body["unsupported_devices"]), sorted(expected))
        self.assertEqual(self.uploads, [], "a refusal must not start an upload")

    def test_a_standalone_324_is_refused(self):
        self.extenders = [extender("192.0.2.138", "HW-OMNI-324")]
        self.refused(self.upgrade(["192.0.2.138"]), ["192.0.2.138"])

    def test_a_standalone_311_is_refused(self):
        self.extenders = [extender("192.0.2.139", "HW-OMNI-311")]
        self.refused(self.upgrade(["192.0.2.139"]), ["192.0.2.139"])

    def test_an_unclassified_extender_address_is_refused_too(self):
        self.extenders = [extender("192.0.2.141", "HW-OMNI-324", "UNCONFIRMED")]
        self.refused(self.upgrade(["192.0.2.141"]), ["192.0.2.141"])

    def test_an_integrated_endpoints_own_address_is_refused(self):
        self.extenders = [extender("192.0.2.140", "HW-OMNI-324", "INTEGRATED", usb_ip="192.0.2.240")]
        self.refused(self.upgrade(["192.0.2.140"]), ["192.0.2.140"])

    def test_a_cached_unit_whose_model_is_311_or_324_is_refused(self):
        for model in ("at-omni-311", "HW-OMNI-324", "at-omni-324-wp"):
            with self.subTest(model=model):
                self.units = [{"ip": "192.0.2.77", "mac": "b8:98:b0:00:00:77", "model": model, "role": "extender"}]
                self.refused(self.upgrade(["192.0.2.77"]), ["192.0.2.77"])

    def test_one_unsupported_target_refuses_the_whole_request(self):
        self.extenders = [extender("192.0.2.138", "HW-OMNI-324")]
        self.refused(self.upgrade(["192.0.2.11", "192.0.2.138"]), ["192.0.2.138"])

    def test_a_model_that_merely_contains_the_digits_is_not_caught(self):
        self.units = [{"ip": "192.0.2.78", "mac": "b8:98:b0:00:00:78", "model": "hw-omni-d4311x", "role": "decoder"},
                      {"ip": "192.0.2.79", "mac": "b8:98:b0:00:00:79", "model": "hw-omni-13241", "role": "decoder"}]
        response = self.upgrade(["192.0.2.78", "192.0.2.79"])
        self.assertNotEqual((response.get_json() or {}).get("reason"), "firmware_not_supported")

    def test_targets_that_are_not_a_list_are_a_400_not_a_crash(self):
        for bad in ("192.0.2.138", {"a": 1}, 5):
            with self.subTest(bad=bad):
                self.assertEqual(self.upgrade(bad).status_code, 400)

    def test_a_supported_unit_is_still_upgraded_as_before(self):
        self.extenders = [extender("192.0.2.138", "HW-OMNI-324")]
        response = self.upgrade(["192.0.2.11"])
        self.assertNotEqual((response.get_json() or {}).get("reason"), "firmware_not_supported")
        self.assertEqual(self.uploads, ["192.0.2.11"], "the ordinary path no longer reaches the upload")

    def test_an_extender_inventory_that_cannot_be_read_does_not_block_ordinary_upgrades(self):
        with mock.patch.object(srv, "_usb_extender_view", side_effect=RuntimeError("boom")):
            self.upgrade(["192.0.2.11"])
        self.assertEqual(self.uploads, ["192.0.2.11"])


class ModelPredicateTests(unittest.TestCase):
    def test_whole_numbers_between_separators_only(self):
        yes = ("AT-OMNI-311", "HW-OMNI-324", "at-omni-324-wp", "311", "omni 324 ext")
        no = ("", None, "hw-omni-e4521", "AT-OMNI-3110", "HW-OMNI-1324", "hw-omni-d4311", "at-omni-111")
        for model in yes:
            self.assertTrue(srv._firmware_unsupported_model(model), model)
        for model in no:
            self.assertFalse(srv._firmware_unsupported_model(model), model)


class UptimeCellLayoutTests(unittest.TestCase):
    def test_uptime_value_never_wraps(self):
        """73d 23h 33m is one token: wrapped, it made every row three lines tall."""
        html = (Path(srv.__file__).parent / "ui" / "index.html").read_text(encoding="utf-8")
        self.assertRegex(html, r"\.uptime_cell\s*\{\s*white-space\s*:\s*nowrap")


class RemoteAccessShellTests(unittest.TestCase):
    """The page keeps the shell every other page has: same title shape, same header geometry."""

    def test_remote_access_page_names_itself_and_matches_the_shell_header(self):
        root = Path(srv.__file__).parent / "ui"
        html = (root / "admin" / "users.html").read_text(encoding="utf-8")
        css = (root / "remote.css").read_text(encoding="utf-8")
        self.assertIn("<h1>OmniSuite Remote Access</h1>", html)
        self.assertNotIn("<h1>OmniSuite Device Manager</h1>", html)
        self.assertRegex(css, r"\.remote-header \.logo \{ height: 48px")
        self.assertRegex(css, r"\.remote-header h1 \{[^}]*font-size: 22px")


if __name__ == "__main__":
    unittest.main()
