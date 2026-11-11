"""Inventory ingest keeps identity and address apart.

The route-time guard refuses an ambiguous record; these hold the stores that
could produce one. A unit whose MAC could not be read, a scan row left at an
address another device now holds, and a MAC written two ways must never leave
one hardware identity under two addresses, or one device wearing another's
details. Synthetic documentation-range values only; fixtures are deep-copied.
"""
from __future__ import annotations

import copy
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("OMNI_SMOKE", "1")

import OmniMatrix_upgrade_server_v7_6y as srv        # noqa: E402

X = "b8:98:b0:00:09:01"
Y = "b8:98:b0:00:09:02"


def unit(mac, ip, **extra):
    row = {"ip": ip, "mac": mac, "hostname": "h-" + ip.split(".")[-1],
           "role": "decoder"}
    row.update(extra)
    return row


class DedupeTests(unittest.TestCase):
    def test_one_device_written_two_ways_is_one_row(self):
        raw = [unit("B8-98-B0-00-09-01", "198.51.100.1"),
               unit("b8:98:b0:00:09:01", "198.51.100.2"),
               unit("", "198.51.100.3"), unit("", "198.51.100.4")]
        units, omitted = srv._dedupe_by_identity(copy.deepcopy(raw))
        self.assertEqual([u["ip"] for u in units],
                         ["198.51.100.1", "198.51.100.3", "198.51.100.4"])
        self.assertEqual(omitted, 1, "unidentified units are never merged with each other")


class InheritTests(unittest.TestCase):
    CACHED = unit(X, "198.51.100.1", timezone="UTC", linkspeed=1000,
                  details={"systeminfo": {"location": "Room 9"}})

    def test_a_unit_with_no_readable_mac_does_not_inherit_a_known_units_details(self):
        fresh = {"ip": "198.51.100.1", "mac": "", "role": "decoder"}
        srv._inherit_cached_fields(fresh, copy.deepcopy([self.CACHED]))
        for field in ("timezone", "linkspeed", "details"):
            self.assertNotIn(field, fresh)

    def test_the_same_identity_still_inherits_however_it_is_written(self):
        fresh = {"ip": "198.51.100.77", "mac": "B8-98-B0-00-09-01"}
        srv._inherit_cached_fields(fresh, copy.deepcopy([self.CACHED]))
        self.assertEqual(fresh["timezone"], "UTC")
        self.assertEqual(fresh["details"]["systeminfo"]["location"], "Room 9")

    def test_two_unidentified_records_at_one_address_may_still_share(self):
        cached = unit("", "198.51.100.5", timezone="UTC")
        fresh = {"ip": "198.51.100.5", "mac": ""}
        srv._inherit_cached_fields(fresh, [cached])
        self.assertEqual(fresh["timezone"], "UTC")

    def test_a_different_identity_at_the_same_address_inherits_nothing(self):
        fresh = {"ip": "198.51.100.1", "mac": Y}
        srv._inherit_cached_fields(fresh, copy.deepcopy([self.CACHED]))
        self.assertNotIn("timezone", fresh)

    def test_a_blank_mac_does_not_carry_the_old_identity_through_reconcile(self):
        merged = srv._reconcile_discovered_units(
            copy.deepcopy([self.CACHED]),
            [{"ip": "198.51.100.1", "mac": "", "role": "decoder"}])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["mac"], "")


class LoadMergeTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="omni-ingest-"))
        for name in ("CACHE", "SCAN_RESULTS"):
            original = getattr(srv, name)
            self.addCleanup(setattr, srv, name, original)
        srv.CACHE = self.dir / "units_cache.json"
        srv.SCAN_RESULTS = self.dir / "scan_results.json"

    def write(self, cache, scan):
        srv.CACHE.write_text(json.dumps(cache), encoding="utf-8")
        srv.SCAN_RESULTS.write_text(json.dumps({"devices": scan}), encoding="utf-8")

    def test_a_scan_row_for_another_device_does_not_lend_its_details(self):
        self.write([unit(X, "198.51.100.1")],
                   [unit(Y, "198.51.100.1", serialnumber="SN-Y", model="m-y")])
        rows = srv._load_cache()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["mac"], X)
        self.assertNotIn("serialnumber", rows[0])
        self.assertNotIn("model", rows[0])

    def test_one_identity_never_appears_at_two_addresses(self):
        self.write([unit(X, "198.51.100.1")],
                   [unit(Y, "198.51.100.1"), unit(X, "198.51.100.2"),
                    unit("B8-98-B0-00-09-01", "198.51.100.3")])
        rows = srv._load_cache()
        owners = [srv._device_identity(r) for r in rows if srv._device_identity(r)]
        self.assertEqual(len(owners), len(set(owners)), rows)
        self.assertEqual([r["ip"] for r in rows if r["mac"] == X], ["198.51.100.1"])

    def test_a_scan_file_that_carries_one_device_twice_loads_it_once(self):
        self.write([unit(Y, "198.51.100.1")],
                   [unit(X, "198.51.100.2"), unit("B8-98-B0-00-09-01", "198.51.100.3")])
        rows = srv._load_cache()
        self.assertEqual([r["ip"] for r in rows if srv._device_identity(r) == "b898b0000901"],
                         ["198.51.100.2"])

    def test_the_same_device_still_gains_scan_fields(self):
        self.write([unit(X, "198.51.100.1")],
                   [unit(X, "198.51.100.1", model="m-x", serialnumber="SN-X")])
        rows = srv._load_cache()
        self.assertEqual((rows[0]["model"], rows[0]["serialnumber"]), ("m-x", "SN-X"))

    def test_scan_only_devices_are_kept(self):
        self.write([unit(X, "198.51.100.1")], [unit(Y, "198.51.100.9")])
        self.assertEqual(sorted(r["mac"] for r in srv._load_cache()), [X, Y])


if __name__ == "__main__":
    unittest.main()
