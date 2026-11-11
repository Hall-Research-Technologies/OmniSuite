"""A saved Multiview group refers to displays by hardware identity, never by address.

A member recorded with a MAC used to fall back to its stored address when the MAC
was not in the inventory, and the layout install went straight to the stored
address. Either way a different unit that had taken the address was written to as
though it were in the group. Synthetic documentation-range values only.
"""
from __future__ import annotations

import copy
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("OMNI_SMOKE", "1")

import OmniMatrix_upgrade_server_v7_6y as srv        # noqa: E402

X = "b8:98:b0:00:0a:01"
Y = "b8:98:b0:00:0a:02"
Z = "b8:98:b0:00:0a:03"


def unit(mac, ip):
    return {"ip": ip, "mac": mac, "hostname": "h-" + ip.split(".")[-1],
            "role": "decoder"}


class ResolveMemberTests(unittest.TestCase):
    def test_a_member_with_an_identity_is_found_by_it_however_it_is_written(self):
        units = [unit(Y, "198.51.100.2"), unit("B8-98-B0-00-0A-01", "198.51.100.9")]
        found = srv._resolve_group_member({"mac": X, "ip": "198.51.100.1"}, units)
        self.assertEqual(found["ip"], "198.51.100.9")

    def test_a_missing_identity_is_not_resolved_through_its_old_address(self):
        units = [unit(Y, "198.51.100.1")]       # a different unit holds the address
        self.assertIsNone(
            srv._resolve_group_member({"mac": X, "ip": "198.51.100.1"}, units))

    def test_an_old_record_with_no_identity_still_resolves_by_address(self):
        units = [unit(Y, "198.51.100.1")]
        self.assertEqual(
            srv._resolve_group_member({"ip": "198.51.100.1"}, units)["mac"], Y)


class LibraryInstallTests(unittest.TestCase):
    def setUp(self):
        self.cache = [unit(Y, "198.51.100.1"), unit(Z, "198.51.100.3"),
                      unit(X, "198.51.100.77")]
        self.installed = []
        original = (srv._load_cache, srv._install_standard_layouts)
        self.addCleanup(lambda: (setattr(srv, "_load_cache", original[0]),
                                 setattr(srv, "_install_standard_layouts", original[1])))
        srv._load_cache = lambda: copy.deepcopy(self.cache)

        def fake_install(ip):
            self.installed.append(ip)
            return {"installed": [], "existing": [], "conflicts": [], "skipped": []}, ""

        srv._install_standard_layouts = fake_install
        saved = dict(srv._MULTIVIEW_GROUPS)
        self.addCleanup(lambda: (srv._MULTIVIEW_GROUPS.clear(),
                                 srv._MULTIVIEW_GROUPS.update(saved)))
        srv._MULTIVIEW_GROUPS["g1"] = {"id": "g1", "name": "Room", "members": [
            # X moved to .77; its stored address now belongs to Y.
            {"mac": X, "ip": "198.51.100.1", "hostname": "x"},
            {"mac": Z, "ip": "198.51.100.3", "hostname": "z"},
            {"mac": "b8:98:b0:00:0a:09", "ip": "198.51.100.5", "hostname": "gone"}]}
        self.client = srv.app.test_client()

    def test_install_goes_to_each_member_where_it_is_now_and_to_nobody_else(self):
        self.client.post("/api/multiview/layouts/install", json={"group": "g1"})
        self.assertEqual(sorted(self.installed), ["198.51.100.3", "198.51.100.77"])
        self.assertNotIn("198.51.100.1", self.installed)
        self.assertNotIn("198.51.100.5", self.installed)

    def test_a_member_that_is_not_discovered_is_reported_not_substituted(self):
        body = self.client.post("/api/multiview/layouts/install",
                                json={"group": "g1"}).get_json()
        text = str(body)
        self.assertIn("gone", text)
        self.assertIn("Not currently discovered", text)


if __name__ == "__main__":
    unittest.main()
