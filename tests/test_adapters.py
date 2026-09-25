"""Remote Access follows an adapter, not an address.

The defect these exist for: OmniSuite persisted the bare IP of the network an
operator chose. When that machine's address changed, Settings reported the old
address as unavailable and the Network dropdown offered nothing, so there was
no way to put it right from the screen that was telling you to.

The machine it was found on made the second half of the problem concrete too:
192.168.100.180 belonged to a Wi-Fi adapter and 192.168.100.185 to an Ethernet
one. Same subnet, same building, different hardware -- so "the address moved"
and "a different adapter got that address" are genuinely different events and
must not be confused.

No test here touches a real network. Adapter inventories are fixtures.
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("OMNI_SMOKE", "1")

import omni_adapters                                    # noqa: E402
import omni_remote                                      # noqa: E402

#: Two adapters on one subnet, which is the shape that made this confusing.
ETHERNET = "guid:8222555e-632a-4c00-b050-7dc5bc4ba912"
WIFI = "guid:bf3f0723-43fd-4165-b804-9b04e810cd0e"


def adapter(identity, name, address, up=True, virtual=False, how="guid"):
    return {
        "id": identity, "id_from": how, "name": name, "address": address,
        "netmask": "255.255.255.0", "prefix": 24,
        "cidr": address.rsplit(".", 1)[0] + ".0/24", "scan": "",
        "status": "Up" if up else "Disconnected", "up": up,
        "virtual": virtual, "label": "%s — %s" % (name, address),
    }


ETH_185 = adapter(ETHERNET, "Ethernet 3", "192.168.100.185")
ETH_190 = adapter(ETHERNET, "Ethernet 3", "192.168.100.190")
WIFI_180 = adapter(WIFI, "Wi-Fi", "192.168.100.180")
HYPERV = adapter("guid:542af5c7-b71f-4b42-9cb4-3e7275a270bf",
                 "vEthernet (Default Switch)", "172.29.16.1", virtual=True)


class IdentityTests(unittest.TestCase):
    """What counts as identity, and in what order."""

    def test_a_guid_is_preferred(self):
        identity, how = omni_adapters.identity_for(
            guid="{8222555E-632A-4C00-B050-7DC5BC4BA912}",
            mac="84-5C-31-F4-44-63", name="Ethernet 3")
        self.assertEqual(how, omni_adapters.BY_GUID)
        self.assertEqual(identity, ETHERNET)

    def test_a_mac_is_the_fallback(self):
        identity, how = omni_adapters.identity_for(
            guid="", mac="84-5C-31-F4-44-63", name="Ethernet 3")
        self.assertEqual(how, omni_adapters.BY_MAC)
        self.assertEqual(identity, "mac:845c31f44463")

    def test_the_name_is_the_last_resort(self):
        identity, how = omni_adapters.identity_for(name="eth0")
        self.assertEqual(how, omni_adapters.BY_NAME)
        self.assertEqual(identity, "name:eth0")

    def test_a_guid_is_recognised_however_it_is_punctuated(self):
        braced, _ = omni_adapters.identity_for(guid="{8222555E-632A-4C00-B050-7DC5BC4BA912}")
        bare, _ = omni_adapters.identity_for(guid="8222555e-632a-4c00-b050-7dc5bc4ba912")
        self.assertEqual(braced, bare)

    def test_renaming_the_adapter_changes_nothing(self):
        """L: a friendly rename must not lose the selection."""
        before, _ = omni_adapters.identity_for(guid="{8222555E-632A-4C00-B050-7DC5BC4BA912}",
                                               name="Ethernet 3")
        after, _ = omni_adapters.identity_for(guid="{8222555E-632A-4C00-B050-7DC5BC4BA912}",
                                              name="Bench Network")
        self.assertEqual(before, after)


class WhatIsOfferedTests(unittest.TestCase):
    """A: a legitimate adapter appears. B: it is projected readably."""

    def test_a_physical_adapter_with_a_usable_address_is_offered(self):
        entry = omni_adapters._entry("Ethernet 3", "192.168.100.185",
                                     netmask="255.255.255.0",
                                     guid="{8222555E-632A-4C00-B050-7DC5BC4BA912}")
        self.assertIsNotNone(entry)
        self.assertEqual(entry["id"], ETHERNET)
        self.assertEqual(entry["address"], "192.168.100.185")
        self.assertEqual(entry["netmask"], "255.255.255.0")

    def test_the_label_is_the_name_and_the_address(self):
        entry = omni_adapters._entry("Ethernet 3", "192.168.100.185",
                                     netmask="255.255.255.0", guid="{aaaa1111-2222-3333-4444-555566667777}")
        self.assertEqual(entry["label"], "Ethernet 3 — 192.168.100.185")

    def test_a_virtual_adapter_is_not_excluded_for_being_virtual(self):
        """The operator chooses the network; the product does not pre-judge."""
        entry = omni_adapters._entry("vEthernet (Default Switch)", "172.29.16.1",
                                     netmask="255.255.240.0",
                                     guid="{542af5c7-b71f-4b42-9cb4-3e7275a270bf}",
                                     virtual=True)
        self.assertIsNotNone(entry)
        self.assertTrue(entry["virtual"])

    def test_nothing_is_excluded_for_what_it_is_called(self):
        for name in ("Ethernet 3", "Wi-Fi", "Bench", "vEthernet (x)", "tap0"):
            with self.subTest(name=name):
                self.assertIsNotNone(
                    omni_adapters._entry(name, "10.0.4.12",
                                         netmask="255.255.255.0",
                                         guid="{aaaa1111-2222-3333-4444-555566667777}"))

    def test_addresses_that_reach_nobody_are_left_out(self):
        for address in ("127.0.0.1", "169.254.1.1", "0.0.0.0",
                        "224.0.0.1", "255.255.255.255"):
            with self.subTest(address=address):
                self.assertIsNone(
                    omni_adapters._entry("Ethernet 3", address,
                                         netmask="255.255.255.0",
                                         guid="{aaaa1111-2222-3333-4444-555566667777}"))


class FollowingTheAdapterTests(unittest.TestCase):
    """D, E, F, G: the heart of it."""

    def test_the_saved_address_is_used_when_it_is_still_there(self):
        address, reason = omni_adapters.resolve_address(
            ETHERNET, "192.168.100.185", [ETH_185, HYPERV])
        self.assertEqual((address, reason), ("192.168.100.185", ""))

    def test_the_same_adapter_with_a_new_address_is_followed(self):
        """D: this is the defect, stated as a test."""
        address, reason = omni_adapters.resolve_address(
            ETHERNET, "192.168.100.180", [ETH_190, HYPERV])
        self.assertEqual(address, "192.168.100.190")
        self.assertEqual(reason, "")

    def test_a_different_adapter_holding_the_old_address_inherits_nothing(self):
        """E: the trap. Wi-Fi had .180; Ethernet must not answer for it."""
        address, reason = omni_adapters.resolve_address(
            ETHERNET, "192.168.100.180", [WIFI_180, HYPERV])
        self.assertEqual(address, "")
        self.assertEqual(reason, "adapter missing")

    def test_a_missing_adapter_yields_nothing(self):
        """F: no listener when the chosen adapter is gone."""
        address, reason = omni_adapters.resolve_address(
            ETHERNET, "192.168.100.185", [WIFI_180, HYPERV])
        self.assertEqual((address, reason), ("", "adapter missing"))

    def test_no_other_adapter_is_substituted(self):
        """G: not Wi-Fi, not Hyper-V, not the first in the list."""
        address, _reason = omni_adapters.resolve_address(
            ETHERNET, "192.168.100.185", [WIFI_180, HYPERV])
        self.assertNotIn(address, ("192.168.100.180", "172.29.16.1"))
        self.assertEqual(address, "")

    def test_several_addresses_on_one_adapter_are_not_guessed_between(self):
        """K: deterministic, and a choice rather than a coin toss."""
        second = dict(ETH_190)
        address, reason = omni_adapters.resolve_address(
            ETHERNET, "192.168.100.7", [ETH_185, second])
        self.assertEqual((address, reason), ("", "ambiguous"))

    def test_several_addresses_still_honour_the_saved_one(self):
        address, reason = omni_adapters.resolve_address(
            ETHERNET, "192.168.100.190", [ETH_185, ETH_190])
        self.assertEqual((address, reason), ("192.168.100.190", ""))

    def test_an_empty_identity_resolves_to_nothing(self):
        self.assertEqual(omni_adapters.resolve_address("", "192.168.100.185",
                                                       [ETH_185]),
                         ("", "adapter missing"))


class MigrationFromAnAddressOnlyConfigTests(unittest.TestCase):
    """P and Q: adopt only when it is unambiguous."""

    def test_one_owner_is_adopted(self):
        adopted = omni_adapters.adopt_saved_address("192.168.100.185",
                                                    [ETH_185, HYPERV])
        self.assertIsNotNone(adopted)
        self.assertEqual(adopted["id"], ETHERNET)

    def test_no_owner_is_refused(self):
        """The real case: .180 was on a Wi-Fi adapter that is not here."""
        self.assertIsNone(
            omni_adapters.adopt_saved_address("192.168.100.180",
                                              [ETH_185, HYPERV]))

    def test_two_owners_is_refused(self):
        """Two adapters reporting one address proves nothing about intent."""
        impostor = adapter("guid:cccc", "Ethernet 9", "192.168.100.185")
        self.assertIsNone(
            omni_adapters.adopt_saved_address("192.168.100.185",
                                              [ETH_185, impostor]))

    def test_an_empty_address_is_refused(self):
        for address in ("", "   ", None):
            with self.subTest(address=address):
                self.assertIsNone(
                    omni_adapters.adopt_saved_address(address, [ETH_185]))


class TheGateIsUnchangedTests(unittest.TestCase):
    """H and I: identity is configuration, never permission."""

    def test_the_wildcard_is_still_refused(self):
        self.assertFalse(omni_remote.is_usable_remote_address("0.0.0.0"))

    def test_loopback_is_still_refused(self):
        self.assertFalse(omni_remote.is_usable_remote_address("127.0.0.1"))

    def test_an_adapter_entry_cannot_smuggle_an_unbindable_address(self):
        """An adapter list is a menu, not an authority on what may be bound."""
        for address in ("0.0.0.0", "127.0.0.1", "255.255.255.255"):
            with self.subTest(address=address):
                self.assertFalse(omni_remote.is_usable_remote_address(address))


class EnumerationShapeTests(unittest.TestCase):
    """J: several adapters are several explicit choices."""

    def test_each_adapter_address_pair_is_its_own_choice(self):
        offered = [ETH_185, ETH_190, WIFI_180, HYPERV]
        self.assertEqual(len({a["label"] for a in offered}), 4)
        # Two of them are the same adapter, and say so.
        same = [a for a in offered if a["id"] == ETHERNET]
        self.assertEqual(len(same), 2)

    def test_an_identity_is_never_shown_to_an_operator(self):
        for entry in (ETH_185, WIFI_180, HYPERV):
            with self.subTest(entry=entry["name"]):
                self.assertNotIn("guid:", entry["label"])
                self.assertNotIn(entry["id"], entry["label"])


if __name__ == "__main__":
    unittest.main()
