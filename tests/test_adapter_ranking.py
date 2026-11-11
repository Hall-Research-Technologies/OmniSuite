"""The adapter OmniSuite starts on is a physical one.

Observed: with nothing chosen yet, OmniSuite offered a Hyper-V virtual switch
ahead of the computer's physical Ethernet port. The default now ranks adapters:
active physical Ethernet, then Wi-Fi, then any other physical adapter, then
virtual ones -- and a virtual adapter is never placed above a usable physical
one. Loopback, link-local and disconnected adapters are never candidates.

This is only the starting point. A selection the operator saved is not touched,
and neither is the order of the list.

Everything is synthetic: names are the ones operating systems use, addresses are
RFC 5737 documentation addresses.
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("OMNI_SMOKE", "1")

import omni_adapters                                 # noqa: E402


def adapter(name, address, **extra):
    entry = {"name": name, "address": address, "up": True, "id": "name:" + name.lower()}
    entry.update(extra)
    return entry


class KindTests(unittest.TestCase):
    CASES = [
        # Windows, with what the OS reports
        (dict(name="Ethernet", media="802.3", hardware=True), "ethernet"),
        (dict(name="Ethernet 3", description="Realtek PCIe GbE Family Controller"),
         "ethernet"),
        (dict(name="Wi-Fi", media="Native 802.11", hardware=True), "wifi"),
        (dict(name="Wireless Network Connection"), "wifi"),
        (dict(name="vEthernet (Default Switch)"), "virtual"),
        (dict(name="vEthernet (External)", description="Hyper-V Virtual Ethernet Adapter"),
         "virtual"),
        (dict(name="Ethernet 2", description="VMware Virtual Ethernet Adapter for VMnet8"),
         "virtual"),
        (dict(name="Ethernet 4", description="VirtualBox Host-Only Ethernet Adapter"),
         "virtual"),
        (dict(name="Local Area Connection* 1",
              description="Microsoft Wi-Fi Direct Virtual Adapter"), "virtual"),
        (dict(name="OpenVPN TAP", description="TAP-Windows Adapter V9"), "virtual"),
        (dict(name="Tailscale"), "virtual"),
        (dict(name="WireGuard Tunnel"), "virtual"),
        (dict(name="Ethernet", virtual=True), "virtual"),
        (dict(name="Ethernet", hardware=False), "virtual"),
        (dict(name="Loopback Pseudo-Interface 1"), "virtual"),
        (dict(name="Bluetooth Network Connection"), "virtual"),
        (dict(name="vEthernet (WSL)"), "virtual"),
        # Linux
        (dict(name="eth0"), "ethernet"),
        (dict(name="enp3s0"), "ethernet"),
        (dict(name="eno1"), "ethernet"),
        (dict(name="wlp2s0"), "wifi"),
        (dict(name="wlan0"), "wifi"),
        (dict(name="docker0"), "virtual"),
        (dict(name="br-1a2b3c4d"), "virtual"),
        (dict(name="virbr0"), "virtual"),
        (dict(name="veth12ab"), "virtual"),
        (dict(name="tun0"), "virtual"),
        (dict(name="lo"), "virtual"),
        # macOS: en0 may be Ethernet or Wi-Fi, and is not guessed at
        (dict(name="en0"), "physical"),
        (dict(name="en5"), "physical"),
        (dict(name="utun3"), "virtual"),
        (dict(name="awdl0"), "virtual"),
        (dict(name="bridge0"), "virtual"),
        (dict(name="llw0"), "virtual"),
    ]

    def test_each_adapter_is_classified_from_what_is_known_about_it(self):
        for fields, expected in self.CASES:
            with self.subTest(**{k: v for k, v in fields.items() if k == "name"}):
                self.assertEqual(omni_adapters.adapter_kind(fields), expected)

    def test_an_unknown_adapter_is_physical_not_virtual(self):
        self.assertEqual(omni_adapters.adapter_kind({"name": "Acme NIC 9000"}),
                         "physical")

    def test_missing_facts_never_raise(self):
        for junk in (None, {}, {"name": None}, {"name": 5, "description": []}):
            with self.subTest(junk=junk):
                self.assertIn(omni_adapters.adapter_kind(junk),
                              ("ethernet", "wifi", "physical", "virtual"))


class RankingTests(unittest.TestCase):
    def test_hyper_v_never_beats_a_physical_ethernet_port(self):
        found = [adapter("vEthernet (Default Switch)", "198.51.100.1"),
                 adapter("Ethernet", "203.0.113.20", media="802.3", hardware=True)]
        best = omni_adapters.preferred_adapter(found)
        self.assertEqual(best["name"], "Ethernet")
        # even when the virtual one sorts first by name, which it does here
        self.assertEqual(sorted(a["name"] for a in found)[0], "Ethernet")

    def test_it_holds_when_the_virtual_adapter_is_listed_first(self):
        found = [adapter("A virtual switch", "198.51.100.1", virtual=True),
                 adapter("Zed Ethernet", "203.0.113.20", media="802.3")]
        self.assertEqual(omni_adapters.preferred_adapter(found)["name"],
                         "Zed Ethernet")

    def test_the_full_order(self):
        found = [adapter("tun0", "198.51.100.9"),
                 adapter("en7", "198.51.100.8"),
                 adapter("Wi-Fi", "198.51.100.7", media="Native 802.11"),
                 adapter("Ethernet", "198.51.100.6", media="802.3")]
        self.assertEqual([a["name"] for a in omni_adapters.rank_adapters(found)],
                         ["Ethernet", "Wi-Fi", "en7", "tun0"])

    def test_wifi_beats_a_vpn_and_loses_to_ethernet(self):
        found = [adapter("WireGuard", "198.51.100.5"),
                 adapter("Wi-Fi", "198.51.100.4", media="Native 802.11")]
        self.assertEqual(omni_adapters.preferred_adapter(found)["name"], "Wi-Fi")
        found.append(adapter("Ethernet", "198.51.100.3", media="802.3"))
        self.assertEqual(omni_adapters.preferred_adapter(found)["name"], "Ethernet")

    def test_the_order_within_a_class_is_stable(self):
        found = [adapter("Ethernet 2", "198.51.100.12", media="802.3"),
                 adapter("Ethernet", "198.51.100.11", media="802.3")]
        self.assertEqual(omni_adapters.preferred_adapter(found)["name"], "Ethernet 2")

    def test_only_virtual_adapters_still_yields_a_choice(self):
        found = [adapter("vEthernet (Default Switch)", "198.51.100.1")]
        self.assertEqual(omni_adapters.preferred_adapter(found)["name"],
                         "vEthernet (Default Switch)")

    def test_nothing_usable_yields_none(self):
        self.assertIsNone(omni_adapters.preferred_adapter([]))
        self.assertIsNone(omni_adapters.preferred_adapter(
            [adapter("Ethernet", "198.51.100.2", up=False)]))
        for bad in ("127.0.0.1", "169.254.7.7", "0.0.0.0", "224.0.0.1", "nope", ""):
            with self.subTest(address=bad):
                self.assertIsNone(omni_adapters.preferred_adapter(
                    [adapter("Ethernet", bad)]))

    def test_annotate_marks_exactly_one_and_changes_no_order(self):
        found = [adapter("B", "198.51.100.2"), adapter("A", "198.51.100.1")]
        marked = omni_adapters.annotate(found)
        self.assertEqual([a["name"] for a in marked], ["B", "A"])
        self.assertEqual(sum(1 for a in marked if a["preferred"]), 1)
        self.assertNotIn("preferred", found[0], "the input must not be mutated")


class EnumerationTests(unittest.TestCase):
    """The enumeration the product uses carries the preference."""

    def rows(self):
        raw = [
            omni_adapters._entry("vEthernet (Default Switch)", "198.51.100.1",
                                 prefix=24, guid="{aaaaaaaa-0000-0000-0000-000000000001}",
                                 status="Up", virtual=True,
                                 description="Hyper-V Virtual Ethernet Adapter",
                                 hardware=False),
            omni_adapters._entry("Ethernet", "203.0.113.20", prefix=24,
                                 guid="{aaaaaaaa-0000-0000-0000-000000000002}",
                                 status="Up", media="802.3", hardware=True),
            omni_adapters._entry("Wi-Fi", "192.0.2.77", prefix=24,
                                 guid="{aaaaaaaa-0000-0000-0000-000000000003}",
                                 status="Disconnected", media="Native 802.11",
                                 hardware=True),
        ]
        return [r for r in raw if r]

    def test_enumerate_adapters_prefers_the_physical_port(self):
        with mock.patch.object(omni_adapters, "_windows_powershell",
                               return_value=self.rows()), \
                mock.patch.object(omni_adapters.sys, "platform", "win32"):
            found = omni_adapters.enumerate_adapters()
        self.assertEqual([a["name"] for a in found],
                         ["Ethernet", "vEthernet (Default Switch)"],
                         "a disconnected adapter is not listed")
        preferred = [a["name"] for a in found if a["preferred"]]
        self.assertEqual(preferred, ["Ethernet"])

    def test_existing_fields_are_unchanged(self):
        row = self.rows()[1]
        for field in ("id", "id_from", "name", "address", "netmask", "prefix",
                      "cidr", "scan", "status", "up", "virtual", "label"):
            self.assertIn(field, row)


class LegacyScanListTests(unittest.TestCase):
    def test_the_device_info_list_carries_the_preference(self):
        import OmniMatrix_upgrade_server_v7_6y as srv
        rows = [{"name": "vEthernet (Default Switch)", "ip": "198.51.100.1",
                 "scan": "198.51.100.1-254"},
                {"name": "Ethernet", "ip": "203.0.113.20", "scan": "203.0.113.1-254"}]
        known = [adapter("vEthernet (Default Switch)", "198.51.100.1", virtual=True),
                 adapter("Ethernet", "203.0.113.20", media="802.3")]
        with mock.patch.object(omni_adapters, "enumerate_adapters",
                               return_value=omni_adapters.annotate(known)):
            marked = srv._mark_preferred_adapter(rows)
        self.assertEqual([r["name"] for r in marked],
                         ["vEthernet (Default Switch)", "Ethernet"],
                         "the list order is left alone")
        self.assertEqual([r["preferred"] for r in marked], [False, True])

    def test_an_unplaceable_row_carries_no_preference_and_nothing_breaks(self):
        import OmniMatrix_upgrade_server_v7_6y as srv
        rows = [{"name": "Mystery", "ip": "192.0.2.200", "scan": "x"}]
        with mock.patch.object(omni_adapters, "enumerate_adapters", return_value=[]):
            marked = srv._mark_preferred_adapter(rows)
        self.assertFalse(marked[0]["preferred"])

    def test_a_failing_enumeration_returns_the_list_untouched(self):
        import OmniMatrix_upgrade_server_v7_6y as srv
        rows = [{"name": "Ethernet", "ip": "203.0.113.20", "scan": "x"}]
        with mock.patch.object(omni_adapters, "enumerate_adapters",
                               side_effect=OSError("no powershell")):
            self.assertEqual(srv._mark_preferred_adapter(rows), rows)


class SavedSelectionIsNeverOverriddenTests(unittest.TestCase):
    """The pages use the preference only when nothing was chosen."""

    @classmethod
    def setUpClass(cls):
        cls.index = (ROOT / "ui" / "index.html").read_text(encoding="utf-8")
        cls.settings = (ROOT / "ui" / "settings.js").read_text(encoding="utf-8")

    def test_device_info_tries_the_saved_adapter_first(self):
        start = self.index.index("async function restoreScanAdapter")
        body = self.index[start:self.index.index("\n}\n", start)]
        self.assertLess(body.index("saved ? options.find"),
                        body.index("dataset.preferred"))
        # The preference lives only on the branch where there was no match.
        self.assertIn("} else {", body.split("dataset.preferred")[0][-400:])

    def test_remote_access_tries_what_was_chosen_and_configured_first(self):
        start = self.settings.index("function fillAdapterList")
        body = self.settings[start:self.settings.index("async function loadRemoteAccess", start)]
        self.assertLess(body.index("state.configured_adapter"),
                        body.index("dataset.preferred === '1'"))
        self.assertLess(body.index("[previous"), body.index("dataset.preferred === '1'"))


if __name__ == "__main__":
    unittest.main()
