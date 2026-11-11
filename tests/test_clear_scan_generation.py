"""A scan that began before Clear Units cannot hand back what was cleared.

Clear Units moves the inventory's "generation" on, under the cache and
scan-results locks. A scan records the generation when it starts and, under the
same two locks, refuses to write if it has moved. These tests drive the real
/api/scan with the network stubbed out (the probe is replaced; nothing leaves
the process) and run the real Clear Units from inside the scan, which is the
only way to be certain the clear happened while the scan was in flight.

Also recorded here, because it is the other half of the same race: a grant that
names a unit which is not in the inventory is ACCEPTED. Grants are by stable
identity and can precede discovery, which is also why they survive a clear. A
unit that comes back through a scan is then protected on the next clear.
"""
from __future__ import annotations

import copy
import sys
import threading
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import omni_users                                    # noqa: E402
from tests.test_clear_inventory import (             # noqa: E402
    ALL, CAMERA, FRONT, InventoryBase, OTHER_DISPLAY, OTHER_SOURCE)
from tests.test_display_groups import ROOM_A, ROOM_B, ROOM_C, SRC, UNITS  # noqa: E402

KEPT = sorted([SRC, CAMERA, FRONT, ROOM_A, ROOM_B])
CLEARED = sorted(set(ALL) - set(KEPT))
TARGETS = ",".join(u["ip"] for u in UNITS)


class ScanBase(InventoryBase):
    def setUp(self):
        super().setUp()
        by_ip = {u["ip"]: u for u in UNITS}
        self.probe_log = []

        def probe(address, *args, **kwargs):
            self.probe_log.append(address)
            self.during_scan(address)
            unit = by_ip.get(address)
            return copy.deepcopy(unit) if unit else None

        self.during_scan = lambda address: None
        for target, value in (("_probe_one", probe),
                              ("_tcp_probe_outcome",
                               lambda *a, **k: "open")):
            patcher = mock.patch.object(self.srv, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def scan(self):
        reply = self.admin_post("/api/scan", {"targets": TARGETS})
        self.assertEqual(reply.status_code, 200, reply.get_data(as_text=True))
        return reply.get_json()

    def scan_rows(self):
        return sorted(omni_users._normalise_identity(u.get("mac"))
                      for u in (self.srv._read_scan_results().get("devices") or []))

    def clear_during_scan(self):
        """Arrange for exactly one Clear Units to happen while the scan runs."""
        fired = []
        lock = threading.Lock()

        def hook(address):
            with lock:
                if fired:
                    return
                fired.append(address)
            result = self.clear()
            fired.append(result.status_code)
            fired.append(result.get_json())

        self.during_scan = hook
        return fired


class ScanStartedBeforeAClearTests(ScanBase):
    def test_control_without_a_clear_the_scan_adds_everything(self):
        """The scan stub works and does repopulate: this is what the rest guard against."""
        self.clear()
        self.assertEqual(self.cached(), KEPT, "precondition: the clear removed nothing")
        body = self.scan()
        self.assertFalse(body.get("discarded"))
        self.assertEqual(self.cached(), ALL)

    def test_a_scan_that_finishes_after_a_clear_does_not_bring_cleared_units_back(self):
        fired = self.clear_during_scan()
        body = self.scan()
        self.assertEqual(fired[1:2], [200], f"the clear did not run during the scan: {fired}")
        self.assertGreater(fired[2]["cleared"], 0,
                           "precondition: the clear removed nothing, so this measures nothing")
        self.assertTrue(body["discarded"])
        self.assertEqual(body["reason"], "inventory_cleared_during_scan")
        self.assertEqual(self.cached(), KEPT)
        self.assertEqual(set(self.scan_rows()) & set(CLEARED), set(),
                         "a cleared unit was written back to scan_results.json")
        # The reply shows what is really held, never the discarded scan.
        self.assertEqual(sorted(omni_users._normalise_identity(u["mac"])
                                for u in body["units"]), KEPT)
        self.assertEqual(body["added"], 0)

    def test_the_discarded_scan_leaves_the_csv_and_matrix_state_alone(self):
        self.clear_during_scan()
        with mock.patch.object(self.srv, "_write_csv") as write_csv:
            self.scan()
        # The clear writes the view once, with what it kept. The scan must not
        # add a write of its own carrying the units it found.
        for call in write_csv.call_args_list:
            written = {omni_users._normalise_identity(u.get("mac")) for u in call.args[0]}
            self.assertEqual(written & set(CLEARED), set(),
                             "the discarded scan wrote the cleared units to the view")

    def test_the_same_holds_for_a_full_clear_with_remote_access_off(self):
        self.set_remote(False)
        fired = self.clear_during_scan()
        body = self.scan()
        self.assertEqual(fired[1], 200)
        self.assertTrue(body["discarded"])
        self.assertEqual(self.cached(), [])

    def test_a_clear_that_removed_nothing_does_not_discard_the_scan(self):
        self.assign_everything()
        self.grant(sources=[SRC, CAMERA, OTHER_SOURCE],
                   destinations=[FRONT, OTHER_DISPLAY, ROOM_C])
        self.groups.update(self.group["id"], members=[ROOM_A, ROOM_B, ROOM_C])
        fired = self.clear_during_scan()
        body = self.scan()
        self.assertEqual(fired[1], 200)
        self.assertEqual(fired[2]["cleared"], 0,
                         "precondition: this clear was meant to remove nothing")
        self.assertFalse(body.get("discarded"))
        self.assertEqual(self.cached(), ALL)

    def test_a_scan_started_after_the_clear_is_a_new_discovery(self):
        self.clear()
        self.assertEqual(self.cached(), KEPT)
        body = self.scan()
        self.assertFalse(body.get("discarded"))
        self.assertEqual(self.cached(), ALL, "units removed by a clear must return on a rescan")

    def test_a_clear_after_the_scan_finished_classifies_what_the_scan_found(self):
        self.scan()
        self.assertEqual(self.cached(), ALL)
        reply = self.clear()
        self.assertEqual(reply.status_code, 200)
        self.assertEqual(self.cached(), KEPT)

    def test_the_generation_only_moves_on_a_clear_that_removed_something(self):
        before = self.srv._inventory_generation_now()
        self.clear()
        after_first = self.srv._inventory_generation_now()
        self.assertEqual(after_first, before + 1)
        self.clear()                          # nothing left to clear
        self.assertEqual(self.srv._inventory_generation_now(), after_first)

    def test_a_refused_clear_does_not_discard_a_scan(self):
        """A 503 changed nothing, so the scan in flight is still current."""
        before = self.srv._inventory_generation_now()
        with mock.patch.object(self.srv, "_cascade_clear", side_effect=OSError("disk")):
            reply = self.clear()
        self.assertEqual(reply.status_code, 503)
        self.assertEqual(self.srv._inventory_generation_now(), before)

    def test_scan_and_clear_take_the_two_locks_in_the_same_order(self):
        source = (ROOT / "OmniMatrix_upgrade_server_v7_6y.py").read_text(encoding="utf-8")
        code = "\n".join(l.split("#")[0] for l in source.split("\n"))
        pair = "with _cache_io_lock, _scan_results_lock:"
        self.assertGreaterEqual(code.count(pair), 3,
                                "scan write, clear and the generation bump all hold both locks")
        self.assertNotIn("with _scan_results_lock, _cache_io_lock:", code)

    def test_the_scans_generation_check_and_its_write_are_under_the_pair(self):
        """The count above passes while any three uses remain, so it cannot say
        WHICH ones. The scan's own check-then-write is the one a clear races:
        the line that opens its block must be the pair, and nothing between the
        check and the save may release it."""
        source = (ROOT / "OmniMatrix_upgrade_server_v7_6y.py").read_text(encoding="utf-8")
        code = [l.split("#")[0].rstrip() for l in source.split(chr(10))]
        checks = [i for i, l in enumerate(code)
                  if l.strip() == "if _inventory_generation_now() != scan_generation:"]
        self.assertEqual(len(checks), 1, "precondition: one scan generation check")
        at = checks[0]
        opener = code[at - 1]
        self.assertEqual(opener.strip(), "with _cache_io_lock, _scan_results_lock:",
                         "the scan's generation check must open the locked block")
        indent = len(opener) - len(opener.lstrip())
        saves = [i for i in range(at, min(at + 40, len(code)))
                 if "_save_cache(merged)" in code[i]]
        self.assertTrue(saves, "precondition: the scan saves its merge below the check")
        for i in range(at, saves[0] + 1):
            line = code[i]
            if line.strip():
                self.assertGreater(len(line) - len(line.lstrip()), indent,
                                   "the check and the save must both sit inside the block")


class PageWiringTests(unittest.TestCase):
    """The page must not say 'found' about a scan whose result was not added."""

    def test_the_scan_status_says_so_when_the_result_was_discarded(self):
        page = (ROOT / "ui" / "index.html").read_text(encoding="utf-8")
        start = page.index("await fetch('/api/scan'")
        body = page[start:page.index("loadScanResults()", start)]
        code = "\n".join(line.split("//")[0] for line in body.split("\n"))
        discarded = code.index("data.discarded")
        self.assertLess(discarded, code.index("found`"),
                        "the discarded branch must be decided before the 'found' wording")
        self.assertIn("were cleared while this scan ran", code)
        self.assertIn("Scan again", code)


class GrantsNamingAnUndiscoveredUnitTests(ScanBase):
    """Chosen behaviour: accepted by identity, kept, and protective once discovered."""

    def grant_destination(self, identity):
        reply = self.admin_post(f"/api/admin/users/{self.teacher.id}", {
            "action": "set_permissions",
            "permissions": {"features": ["av"], "sources": [SRC, CAMERA],
                            "destinations": [FRONT, identity], "groups": []}})
        return reply

    def test_a_grant_for_a_unit_the_clear_just_removed_is_accepted_and_kept(self):
        self.clear()
        self.assertNotIn(OTHER_DISPLAY, self.cached())
        reply = self.grant_destination(OTHER_DISPLAY)
        self.assertEqual(reply.status_code, 200, reply.get_data(as_text=True))
        teacher = self.store.find_by_username("teacher")
        self.assertIn(OTHER_DISPLAY, teacher.permissions.destinations)
        # It names nothing in the inventory, so it protects nothing yet and
        # produces no row: the report speaks about discovered units only.
        report = self.report()
        self.assertNotIn(OTHER_DISPLAY, [r["id"] for r in report["units"]])

    def test_the_unit_is_protected_as_soon_as_a_scan_finds_it_again(self):
        self.clear()
        self.grant_destination(OTHER_DISPLAY)
        self.scan()
        self.assertIn(OTHER_DISPLAY, self.cached())
        self.clear()
        self.assertIn(OTHER_DISPLAY, self.cached(),
                      "a unit someone holds a grant for was cleared after rediscovery")

    def test_a_group_naming_an_undiscovered_unit_keeps_it_and_protects_it_later(self):
        self.clear()
        result = self.groups.update(self.group["id"], members=[ROOM_A, ROOM_B, ROOM_C])
        self.assertTrue(result["ok"])
        self.scan()
        self.clear()
        self.assertIn(ROOM_C, self.cached())

    def test_grants_and_group_members_survive_a_clear_untouched(self):
        before = self.stores()
        self.clear()
        self.assertEqual(self.stores()[:3], before[:3])


class ClearAgainstOtherActivityTests(ScanBase):
    def test_a_state_refresh_during_repeated_clear_and_rescan_never_fails(self):
        errors = []
        stop = threading.Event()

        def reader():
            try:
                while not stop.is_set():
                    for unit in self.srv._load_cache() or []:
                        assert unit.get("mac")
                    self.srv._read_scan_results()
            except Exception as exc:                          # pragma: no cover
                errors.append(repr(exc))

        thread = threading.Thread(target=reader, daemon=True)
        thread.start()
        try:
            for _ in range(8):
                self.assertEqual(self.clear().status_code, 200)
                self.assertFalse(self.scan().get("discarded"))
        finally:
            stop.set()
            thread.join(10)
        self.assertEqual(errors, [])
        self.assertEqual(self.cached(), ALL)

    def test_a_permission_edit_during_a_scan_that_is_then_cleared_is_not_lost(self):
        """Assignment added while the scan is running protects the unit at the clear."""
        added = []

        def grant_during_scan(address):
            if added:
                return
            added.append(address)
            self.store.set_permissions(self.teacher.id, omni_users.Permissions(
                features=("av",), sources=[SRC, CAMERA, OTHER_SOURCE],
                destinations=[FRONT], groups=[]))

        self.during_scan = grant_during_scan
        self.scan()
        self.assertTrue(added)
        self.clear()
        self.assertIn(OTHER_SOURCE, self.cached())


if __name__ == "__main__":
    unittest.main()
