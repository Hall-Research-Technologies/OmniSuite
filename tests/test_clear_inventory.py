"""Clear Units while Remote Access is on: assigned units stay, the rest go.

The decision is the server's. It is made from the account and group stores as
they are at that moment, under the same locks those stores use for their writes,
and it never reads anything the request says. These tests drive the real
endpoints through the real gate, with the device transport fenced.

What is covered (the matrix in the brief, item 32):

    Remote OFF  + all unassigned / assigned
    Remote ON   + all unassigned / all assigned / mixed
    assigned by a User (source and display), by a group, by Direct Access
    manipulated requests, and the other ways of removing a unit
    a concurrent assignment edit, a concurrent group edit, a scan write
    persistence across a restart
    the report the dialog and the Device names page both draw from

Nothing here contacts hardware.
"""
from __future__ import annotations

import copy
import json
import os
import sys
import threading
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("OMNI_SMOKE", "1")

import omni_groups                                   # noqa: E402
import omni_inventory                                # noqa: E402
import omni_users                                    # noqa: E402
from omni_users import Permissions                   # noqa: E402
from tests.test_acceptance_lifecycle import LifecycleBase   # noqa: E402
from tests.test_display_groups import (              # noqa: E402
    ROOM_A, ROOM_B, ROOM_C, SRC, UNITS, canonical)
from tests import test_user_routing as harness       # noqa: E402

CAMERA = harness.DOC_CAMERA
OTHER_SOURCE = harness.UNSEEN_SOURCE
FRONT = harness.FRONT_DISPLAY
OTHER_DISPLAY = harness.UNSEEN_DISPLAY
ALL = sorted(omni_users._normalise_identity(u["mac"]) for u in UNITS)


class InventoryBase(LifecycleBase):
    """The lifecycle harness, plus an isolated standalone-extender store.

    The teacher starts with the sources SRC and CAMERA and the display FRONT;
    the group "Front rooms" holds ROOM_A and ROOM_B. ROOM_C, OTHER_SOURCE and
    OTHER_DISPLAY are held by nothing.
    """

    def setUp(self):
        super().setUp()
        srv = self.srv
        prev_file = srv._usb_extenders.state_file
        prev_devices = dict(srv._usb_extenders._devices)

        def restore_usb():
            srv._usb_extenders.state_file = prev_file
            srv._usb_extenders._devices.clear()
            srv._usb_extenders._devices.update(prev_devices)

        self.addCleanup(restore_usb)
        srv._usb_extenders.state_file = self.dir / "usb_extenders.json"
        srv._usb_extenders._devices.clear()
        self.grant(sources=[SRC, CAMERA], destinations=[FRONT], groups=[])
        self.remote(True)

    def remote(self, on):
        patcher = mock.patch.object(self.srv, "remote_access_enabled",
                                    return_value=bool(on))
        patcher.start()
        self.addCleanup(patcher.stop)
        self._remote_patch = patcher

    def set_remote(self, on):
        self._remote_patch.stop()
        self.remote(on)

    def clear(self, **body):
        return self.admin_post("/api/clear_units", body)

    def report(self):
        reply = self.get("/api/admin/inventory/assignments",
                         self.session_for(self.admin))
        self.assertEqual(reply.status_code, 200, reply.get_data(as_text=True))
        return reply.get_json()

    def row(self, identity, report=None):
        report = report or self.report()
        return next(r for r in report["units"] if r["id"] == identity)

    def cached(self):
        return sorted(omni_users._normalise_identity(u.get("mac"))
                      for u in (self.srv._load_cache() or []))

    def stores(self):
        return (self.store.grants_snapshot(), self.groups.members_snapshot(),
                [a["username"] for a in self.store.list_accounts()],
                self.aliases.all())

    def assign_everything(self):
        self.grant(sources=[SRC, CAMERA, OTHER_SOURCE],
                   destinations=[FRONT, OTHER_DISPLAY, ROOM_C])

    def assign_nothing(self):
        self.grant(sources=[], destinations=[], groups=[])
        self.groups.update(self.group["id"], members=[])


# ---------------------------------------------------------------- the report
class AssignmentReportTests(InventoryBase):
    def test_every_kind_of_reference_is_found(self):
        report = self.report()
        self.assertEqual(self.row(SRC, report)["users"], ["teacher"])
        self.assertEqual(self.row(SRC, report)["summary"], "1 User")
        self.assertEqual(self.row(FRONT, report)["users"], ["teacher"])
        self.assertEqual(self.row(ROOM_A, report)["groups"], ["Front rooms"])
        self.assertEqual(self.row(ROOM_A, report)["summary"], "1 Group")
        self.assertEqual(self.row(ROOM_C, report)["summary"], "Unassigned")
        self.assertFalse(self.row(OTHER_SOURCE, report)["assigned"])

    def test_totals_say_how_many_are_protected(self):
        report = self.report()
        self.assertTrue(report["remote_access"])
        self.assertEqual(report["total"], len(UNITS))
        self.assertEqual(report["assigned"], 5)
        self.assertEqual(report["protected"], 5)
        self.assertEqual(report["clearable"], len(UNITS) - 5)

    def test_with_remote_access_off_nothing_is_protected_but_it_is_still_reported(self):
        self.set_remote(False)
        report = self.report()
        self.assertFalse(report["remote_access"])
        self.assertEqual(report["assigned"], 5)
        self.assertEqual(report["protected"], 0)
        self.assertEqual(report["clearable"], len(UNITS))

    def test_several_holders_are_counted_and_combined(self):
        self.store.create("assistant", "assistant-password-1", omni_users.ROLE_USER,
                          Permissions(features=("av",), sources=[SRC],
                                      destinations=[ROOM_A], groups=[]))
        report = self.report()
        self.assertEqual(self.row(SRC, report)["summary"], "2 Users")
        self.assertEqual(self.row(SRC, report)["users"], ["assistant", "teacher"])
        # A user AND a group on one unit.
        self.assertEqual(self.row(ROOM_A, report)["summary"], "1 User · 1 Group")

    def test_a_direct_access_account_is_marked_and_still_counts(self):
        self.store.enable_direct_access(self.teacher.id, actor=self.admin)
        row = self.row(SRC)
        self.assertEqual(row["direct_access"], ["teacher"])
        self.assertEqual(row["users"], ["teacher"])

    def test_a_disabled_account_still_holds_its_devices(self):
        self.store.set_enabled(self.teacher.id, False, actor=self.admin)
        self.assertTrue(self.row(SRC)["assigned"])

    def test_an_administrator_holds_nothing(self):
        self.assertFalse(any(
            "principal" in r["users"] for r in self.report()["units"]))

    def test_the_report_names_things_and_carries_no_secret_or_address(self):
        self.aliases.set_alias(SRC, "Lectern PC")
        raw = self.get("/api/admin/inventory/assignments",
                       self.session_for(self.admin)).get_data(as_text=True)
        body = json.loads(raw)
        self.assertEqual(self.row(SRC, body)["friendly_name"], "Lectern PC")
        self.assertEqual(self.row(SRC, body)["device_name"], "enc-teacher")
        allowed = {"id", "type", "name", "friendly_name", "device_name", "model",
                   "users", "direct_access", "groups", "count", "summary",
                   "assigned"}
        for row in body["units"]:
            self.assertEqual(set(row), allowed)
        for forbidden in ("192.0.2.", "password", "token", "csrf", "digest", "blob"):
            self.assertNotIn(forbidden, raw.lower(), forbidden)

    def test_it_is_for_administrators_only(self):
        teacher = self.session_for(self.teacher)
        self.assertEqual(self.get("/api/admin/inventory/assignments", teacher
                                  ).status_code, 403)
        anonymous = self.srv.app.test_client().get(
            "/api/admin/inventory/assignments",
            environ_base={harness.omni_remote.LISTENER_KEY: harness.omni_remote.REMOTE})
        self.assertIn(anonymous.status_code, (401, 403))

    def test_an_unreadable_store_is_an_error_not_an_empty_answer(self):
        with mock.patch.object(self.store, "grants_snapshot",
                               side_effect=RuntimeError("unreadable")):
            reply = self.get("/api/admin/inventory/assignments",
                             self.session_for(self.admin))
        self.assertEqual(reply.status_code, 503)
        self.assertFalse(reply.get_json()["ok"])

    def test_identity_is_the_hardware_address_not_the_ip(self):
        moved = copy.deepcopy(UNITS)
        for unit in moved:
            if unit["mac"] == SRC:
                unit["ip"] = "192.0.2.201"
        self.srv._save_cache(moved)
        self.assertTrue(self.row(SRC)["assigned"])

    def test_a_name_or_hostname_never_makes_a_unit_assigned(self):
        self.aliases.set_alias(OTHER_SOURCE, "enc-teacher")      # a lookalike
        renamed = copy.deepcopy(UNITS)
        for unit in renamed:
            if unit["mac"] == OTHER_SOURCE:
                unit["hostname"] = "enc-teacher"
        self.srv._save_cache(renamed)
        self.assertFalse(self.row(OTHER_SOURCE)["assigned"])

    def test_mac_spelling_does_not_matter(self):
        spelled = copy.deepcopy(UNITS)
        for unit in spelled:
            if unit["mac"] == SRC:
                unit["mac"] = SRC.upper().replace(":", "-")
        self.srv._save_cache(spelled)
        self.assertTrue(self.row(SRC)["assigned"])

    def test_a_usb_address_names_its_parent(self):
        usb = "b8:98:b0:00:0a:01"
        with_usb = copy.deepcopy(UNITS)
        for unit in with_usb:
            if unit["mac"] == OTHER_DISPLAY:
                unit["usb_mac"] = usb
        self.srv._save_cache(with_usb)
        self.grant(destinations=[FRONT, usb])
        self.assertTrue(self.row(OTHER_DISPLAY)["assigned"])
        self.assertEqual(self.clear().status_code, 200)
        self.assertIn(OTHER_DISPLAY, self.cached())

    def test_the_summary_follows_every_administrative_change(self):
        # create / modify / delete a user
        self.assertFalse(self.row(OTHER_SOURCE)["assigned"])
        made = self.store.create("visitor", "visitor-password-1", omni_users.ROLE_USER,
                                 Permissions(features=("av",), sources=[OTHER_SOURCE]))
        self.assertEqual(self.row(OTHER_SOURCE)["users"], ["visitor"])
        self.store.set_permissions(made["user"]["id"] if "user" in made else made["id"],
                                   Permissions(features=("av",), sources=[]))
        self.assertFalse(self.row(OTHER_SOURCE)["assigned"])
        self.store.set_permissions(self.store.find_by_username("visitor").id,
                                   Permissions(features=("av",), sources=[OTHER_SOURCE]))
        self.store.delete(self.store.find_by_username("visitor").id, actor=self.admin)
        self.assertFalse(self.row(OTHER_SOURCE)["assigned"])
        # group membership and group permission
        self.groups.update(self.group["id"], members=[ROOM_A, ROOM_B, ROOM_C])
        self.assertEqual(self.row(ROOM_C)["groups"], ["Front rooms"])
        self.groups.update(self.group["id"], members=[ROOM_A])
        self.assertFalse(self.row(ROOM_C)["assigned"])
        self.groups.delete(self.group["id"])
        self.assertFalse(self.row(ROOM_A)["assigned"])
        # a clear, and a restore of the stores
        self.clear()
        after = self.report()
        self.assertEqual(sorted(r["id"] for r in after["units"]), sorted([SRC, CAMERA, FRONT]))
        self.assertTrue(all(r["assigned"] for r in after["units"]))


# ------------------------------------------------------------ the clear itself
class ClearRemoteOffTests(InventoryBase):
    def test_all_unassigned_is_a_full_clear(self):
        self.set_remote(False)
        self.assign_nothing()
        body = self.clear().get_json()
        self.assertEqual((body["mode"], body["cleared"], body["kept"]), ("full", len(UNITS), 0))
        self.assertEqual(self.cached(), [])

    def test_assigned_units_are_cleared_and_their_assignments_removed(self):
        self.set_remote(False)
        body = self.clear().get_json()
        self.assertEqual(body["mode"], "full")
        self.assertEqual(self.cached(), [])
        teacher = self.store.find_by_username("teacher")
        self.assertEqual((set(teacher.permissions.sources),
                          set(teacher.permissions.destinations)), (set(), set()))
        self.assertEqual(self.groups.get(self.group["id"])["members"], [])


class ClearRemoteOnTests(InventoryBase):
    def test_all_unassigned_clears_everything(self):
        self.assign_nothing()
        body = self.clear().get_json()
        self.assertEqual((body["mode"], body["cleared"], body["kept"]),
                         ("unassigned", len(UNITS), 0))
        self.assertEqual(self.cached(), [])

    def test_all_assigned_clears_nothing(self):
        self.assign_everything()
        self.groups.update(self.group["id"], members=[ROOM_A, ROOM_B])
        before = self.stores()
        reply = self.clear()
        self.assertEqual(reply.status_code, 200)
        body = reply.get_json()
        self.assertEqual((body["cleared"], body["kept"]), (0, len(UNITS)))
        self.assertEqual(self.cached(), ALL)
        self.assertEqual(self.stores(), before)

    def test_a_mixed_inventory_keeps_exactly_the_assigned_units(self):
        """The brief's example: A to a user, B in a group, D and E unassigned."""
        self.aliases.set_alias(SRC, "Kept source")
        self.aliases.set_alias(OTHER_SOURCE, "Cleared source")
        before_grants, before_members, before_accounts, _ = self.stores()
        body = self.clear().get_json()
        self.assertEqual(body["mode"], "unassigned")
        self.assertEqual(sorted(self.cached()),
                         sorted([SRC, CAMERA, FRONT, ROOM_A, ROOM_B]))
        self.assertEqual((body["cleared"], body["kept"]), (3, 5))
        for gone in (OTHER_SOURCE, OTHER_DISPLAY, ROOM_C):
            self.assertNotIn(gone, self.cached())
        # Nothing Remote Access holds was touched.
        after_grants, after_members, after_accounts, after_names = self.stores()
        self.assertEqual(after_grants, before_grants)
        self.assertEqual(after_members, before_members)
        self.assertEqual(after_accounts, before_accounts)
        self.assertEqual(self.groups.get(self.group["id"])["members"], [ROOM_A, ROOM_B])
        # A friendly name goes with the unit it named, and only with it.
        self.assertEqual(after_names.get(SRC), "Kept source")
        self.assertNotIn(OTHER_SOURCE, after_names)
        self.assertEqual((body["accounts_updated"], body["groups_updated"]), (0, 0))

    def test_active_users_still_route_afterwards(self):
        session = self.session_for(self.teacher)
        self.clear()
        self.assertEqual(self.route(session).status_code, 200)
        state = self.state(session)
        self.assertEqual([d["id"] for d in state["destinations"]], [FRONT])
        self.assertEqual(sorted(s["id"] for s in state["sources"]), sorted([SRC, CAMERA]))

    def test_a_direct_access_user_is_unaffected(self):
        token = self.store.enable_direct_access(self.teacher.id, actor=self.admin)["token"]
        client, reply = self.link_login(token)
        self.assertEqual(reply.status_code, 200)
        self.clear()
        state = client.get("/api/user/state",
                           environ_base={harness.omni_remote.LISTENER_KEY:
                                         harness.omni_remote.REMOTE}).get_json()
        self.assertEqual([d["id"] for d in state["destinations"]], [FRONT])

    def test_a_rescan_returns_the_cleared_units_once_and_never_duplicates_a_kept_one(self):
        self.clear()
        self.srv._save_cache(copy.deepcopy(UNITS))
        self.assertEqual(self.cached(), ALL)
        self.assertEqual(len(self.cached()), len(set(self.cached())))
        # And the kept ones were never un-assigned along the way.
        self.assertTrue(self.row(SRC)["assigned"])
        self.assertFalse(self.row(OTHER_SOURCE)["assigned"])

    def test_a_unit_that_moved_address_is_still_protected(self):
        moved = copy.deepcopy(UNITS)
        for unit in moved:
            if unit["mac"] == SRC:
                unit["ip"] = "192.0.2.202"
        self.srv._save_cache(moved)
        self.clear()
        kept = {omni_users._normalise_identity(u["mac"]): u["ip"]
                for u in self.srv._load_cache()}
        self.assertEqual(kept.get(SRC), "192.0.2.202")

    def test_a_unit_with_no_hardware_address_is_not_protected_by_anything(self):
        odd = copy.deepcopy(UNITS) + [{"ip": "192.0.2.77", "hostname": "no-mac",
                                       "role": "encoder"}]
        self.srv._save_cache(odd)
        self.clear()
        self.assertNotIn("192.0.2.77", [u.get("ip") for u in self.srv._load_cache()])

    def test_scan_results_are_trimmed_with_the_cache(self):
        """scan_results is merged back in on every read: it must not resurrect."""
        scan = {"devices": copy.deepcopy(UNITS),
                "encoders": [u for u in copy.deepcopy(UNITS) if u["role"] == "encoder"],
                "decoders": [u for u in copy.deepcopy(UNITS) if u["role"] == "decoder"],
                "stats": {}}
        self.srv._save_scan_results(scan)
        self.clear()
        self.assertEqual(self.cached(), sorted([SRC, CAMERA, FRONT, ROOM_A, ROOM_B]))
        stored = self.srv._read_scan_results()
        for key in ("devices", "encoders", "decoders"):
            for row in stored[key]:
                self.assertNotIn(row["mac"], (OTHER_SOURCE, OTHER_DISPLAY, ROOM_C))
        self.assertEqual(stored["stats"]["devices_merged"], 5)

    def test_standalone_usb_extenders_follow_the_same_rule(self):
        keep_mac, drop_mac = "b8:98:b0:00:0b:01", "b8:98:b0:00:0b:02"
        self.srv._usb_extenders._devices[keep_mac.upper().replace(":", "")] = {"mac": keep_mac}
        self.srv._usb_extenders._devices[drop_mac.upper().replace(":", "")] = {"mac": drop_mac}
        self.grant(destinations=[FRONT, keep_mac])
        body = self.clear().get_json()
        self.assertEqual(body["usb_cleared"], 1)
        self.assertEqual(list(self.srv._usb_extenders._devices),
                         [keep_mac.upper().replace(":", "")])

    def test_it_is_recorded_as_clear_unassigned_units(self):
        self.clear()
        entry = next(e for e in self.store.audit_entries(50)
                     if e["event"] == "inventory.cleared_unassigned")
        self.assertEqual((entry["detail"]["devices"], entry["detail"]["kept"]), (3, 5))
        self.assertEqual(entry["actor"], "principal")
        self.assertNotIn("password", json.dumps(entry).lower())

    def test_a_full_clear_is_recorded_under_its_own_name(self):
        self.set_remote(False)
        self.clear()
        events = [e["event"] for e in self.store.audit_entries(50)]
        self.assertIn("inventory.cleared", events)
        self.assertNotIn("inventory.cleared_unassigned", events)

    def test_a_failing_store_clears_nothing(self):
        before = self.cached()
        with mock.patch.object(self.store, "list_accounts",
                               side_effect=RuntimeError("unreadable")):
            reply = self.clear()
        self.assertEqual(reply.status_code, 503)
        self.assertEqual(self.cached(), before)

    def test_a_failure_while_forgetting_names_leaves_no_hybrid(self):
        self.aliases.set_alias(OTHER_SOURCE, "Spare")
        before = self.stores()
        with mock.patch.object(self.aliases, "forget_many",
                               side_effect=OSError("disk full")):
            reply = self.clear()
        self.assertEqual(reply.status_code, 503)
        self.assertEqual(self.cached(), ALL)
        self.assertEqual(self.stores(), before)

    def test_it_is_administrative(self):
        reply = self.post("/api/clear_units", {}, self.session_for(self.teacher))
        self.assertEqual(reply.status_code, 403)
        self.assertEqual(self.cached(), ALL)


class ManipulatedRequestTests(InventoryBase):
    """Remote Access on: nothing a caller sends can remove an assigned unit."""

    BODIES = ({}, {"override": True}, {"force": True}, {"protect": False},
              {"remote_access": False}, {"scope": "all"}, {"mode": "full"},
              {"identities": [SRC]}, {"all": True}, {"exit_multiview": True},
              {"ips": ["192.0.2.11"]}, {"protected": 0}, [], "all", None)

    def test_no_body_changes_what_is_kept(self):
        for body in self.BODIES:
            with self.subTest(body=body):
                self.srv._save_cache(copy.deepcopy(UNITS))
                reply = self.admin_post("/api/clear_units", body)
                # An object is a well-formed request and the assignments still decide;
                # anything else is refused outright. Either way nothing assigned goes.
                expected = 200 if isinstance(body, dict) else 400
                self.assertEqual(reply.status_code, expected, reply.get_data(as_text=True))
                kept = sorted([SRC, CAMERA, FRONT, ROOM_A, ROOM_B])
                if isinstance(body, dict):
                    self.assertEqual(self.cached(), kept)
                else:
                    # Refused, so not even the unassigned units went.
                    self.assertEqual(self.cached(), sorted(
                        omni_users._normalise_identity(u.get("mac")) for u in UNITS))

    def test_query_string_and_headers_do_not_either(self):
        session = self.session_for(self.admin)
        self.client.set_cookie(harness.omni_gate.SESSION_COOKIE, session.token)
        reply = self.client.post(
            "/api/clear_units?override=1&force=1&remote_access=0", json={},
            environ_base={harness.omni_remote.LISTENER_KEY: harness.omni_remote.REMOTE},
            headers={"X-OmniSuite-CSRF": session.csrf_token, "X-Force": "1",
                     "X-Override": "1"})
        self.assertEqual(reply.status_code, 200)
        self.assertIn(SRC, self.cached())

    def test_no_preview_route_and_no_second_clear_route_exist(self):
        self.assertEqual(self.get("/api/clear_units/preview",
                                  self.session_for(self.admin)).status_code, 404)
        routes = sorted(r.rule for r in self.srv.app.url_map.iter_rules()
                        if "clear" in r.rule)
        # The standalone USB inventory has its own clear, by design: it is not
        # part of the OmniStream inventory Clear Units protects.
        self.assertEqual(routes, ["/api/clear", "/api/clear_units",
                                  "/api/usb_extenders/clear"])

    def test_remove_selected_keeps_an_assigned_unit_too(self):
        reply = self.admin_post("/api/remove_units",
                                {"ips": ["192.0.2.11", "192.0.2.19"]})
        body = reply.get_json()
        self.assertEqual(reply.status_code, 200)
        self.assertEqual((body["removed"], body["protected"]), (1, 1))
        self.assertIn(SRC, self.cached())
        self.assertNotIn(OTHER_SOURCE, self.cached())

    def test_remove_selected_is_unchanged_with_remote_access_off(self):
        self.set_remote(False)
        body = self.admin_post("/api/remove_units", {"ips": ["192.0.2.11"]}).get_json()
        self.assertEqual((body["removed"], body["protected"]), (1, 0))
        self.assertNotIn(SRC, self.cached())

    def test_remove_selected_refuses_when_assignments_cannot_be_read(self):
        with mock.patch.object(self.store, "grants_snapshot",
                               side_effect=RuntimeError("unreadable")):
            reply = self.admin_post("/api/remove_units", {"ips": ["192.0.2.19"]})
        self.assertEqual(reply.status_code, 503)
        self.assertIn(OTHER_SOURCE, self.cached())

    def test_the_older_clear_route_is_closed_while_remote_access_is_on(self):
        self.srv._save_scan_results({"devices": copy.deepcopy(UNITS), "stats": {}})
        reply = self.admin_post("/api/clear", {})
        self.assertEqual(reply.status_code, 409)
        self.assertTrue(self.srv.SCAN_RESULTS.exists())

    def test_the_older_clear_route_is_unchanged_with_remote_access_off(self):
        self.set_remote(False)
        self.srv._save_scan_results({"devices": copy.deepcopy(UNITS), "stats": {}})
        self.assertEqual(self.admin_post("/api/clear", {}).status_code, 200)
        self.assertFalse(self.srv.SCAN_RESULTS.exists())


class FullClearNeedsRemoteAccessOffTests(InventoryBase):
    def test_two_steps_and_no_shortcut(self):
        self.assertEqual(self.clear().get_json()["kept"], 5)          # step one: kept
        self.srv._save_cache(copy.deepcopy(UNITS))
        self.set_remote(False)                                          # the Settings step
        body = self.clear().get_json()
        self.assertEqual((body["mode"], body["kept"]), ("full", 0))
        self.assertEqual(self.cached(), [])
        teacher = self.store.find_by_username("teacher")
        self.assertEqual(set(teacher.permissions.sources), set())

    def test_the_client_has_no_way_to_ask_for_both(self):
        source = (ROOT / "ui" / "clear-units.js").read_text(encoding="utf-8")
        self.assertNotIn("/api/remote", source)


# ------------------------------------------------------------------ the races
class RaceTests(InventoryBase):
    """Classify-and-remove is one step with respect to the stores it reads."""

    def _interleave(self, attempt):
        """Run `attempt` on another thread from inside the decision.

        Returns (blocked_during_clear, thread). The real classifier still runs;
        the wrapper only starts the competing write at the moment of decision.
        """
        outcome = {}
        real = omni_inventory.classify

        def classify(units, assignments, protect):
            thread = threading.Thread(target=attempt, daemon=True)
            thread.start()
            thread.join(0.6)
            outcome["blocked"] = thread.is_alive()
            outcome["thread"] = thread
            return real(units, assignments, protect)

        with mock.patch.object(omni_inventory, "classify", classify):
            reply = self.clear()
        outcome["thread"].join(10)
        self.assertFalse(outcome["thread"].is_alive(), "the competing write never finished")
        return outcome["blocked"], reply

    def test_a_grant_cannot_land_between_the_decision_and_the_removal(self):
        def grant_spare():
            self.store.set_permissions(self.teacher.id, Permissions(
                features=("av",), sources=[SRC, CAMERA, OTHER_SOURCE],
                destinations=[FRONT], groups=[]))

        blocked, reply = self._interleave(grant_spare)
        self.assertEqual(reply.status_code, 200)
        self.assertTrue(blocked, "precondition: the grant was not held off during the "
                                 "decision, so this test measures nothing")
        # It applied afterwards, in full, and nothing else was lost.
        teacher = self.store.find_by_username("teacher")
        self.assertIn(OTHER_SOURCE, teacher.permissions.sources)
        self.assertEqual(set(teacher.permissions.destinations), {FRONT})

    def test_a_group_edit_cannot_land_between_the_decision_and_the_removal(self):
        def add_member():
            self.groups.update(self.group["id"], members=[ROOM_A, ROOM_B, ROOM_C])

        blocked, reply = self._interleave(add_member)
        self.assertEqual(reply.status_code, 200)
        self.assertTrue(blocked, "precondition: the group edit was not held off")
        self.assertEqual(self.groups.get(self.group["id"])["members"],
                         [ROOM_A, ROOM_B, ROOM_C])
        self.assertNotIn(ROOM_A, [] if ROOM_A in self.cached() else [ROOM_A],
                         "a unit the group held was cleared")

    def test_a_grant_made_before_the_decision_protects_the_unit(self):
        self.store.set_permissions(self.teacher.id, Permissions(
            features=("av",), sources=[SRC, CAMERA, OTHER_SOURCE],
            destinations=[FRONT], groups=[]))
        self.clear()
        self.assertIn(OTHER_SOURCE, self.cached())

    def test_a_scan_write_is_held_off_during_the_decision(self):
        written = []

        def scan_finishes():
            self.srv._save_cache(copy.deepcopy(UNITS))
            written.append(True)

        outcome = {}
        real = omni_inventory.classify

        def classify(units, assignments, protect):
            thread = threading.Thread(target=scan_finishes, daemon=True)
            thread.start()
            thread.join(0.6)
            outcome["blocked"] = thread.is_alive()
            outcome["thread"] = thread
            return real(units, assignments, protect)

        with mock.patch.object(omni_inventory, "classify", classify):
            self.clear()
        outcome["thread"].join(10)
        self.assertTrue(outcome["blocked"], "precondition: the scan write was not held off")
        self.assertEqual(written, [True])
        # The scan finished after the clear, so it is a new discovery: whole and parseable.
        self.assertEqual(self.cached(), ALL)

    def test_clears_and_scans_hammered_together_leave_readable_files(self):
        errors = []
        stop = threading.Event()

        def scanner():
            try:
                while not stop.is_set():
                    self.srv._save_cache(copy.deepcopy(UNITS))
                    self.srv._save_scan_results(
                        {"devices": copy.deepcopy(UNITS), "stats": {}})
            except Exception as exc:                          # pragma: no cover
                errors.append(exc)

        thread = threading.Thread(target=scanner, daemon=True)
        thread.start()
        try:
            for _ in range(15):
                self.assertEqual(self.clear().status_code, 200)
                for unit in self.srv._load_cache():
                    self.assertIn("mac", unit)
        finally:
            stop.set()
            thread.join(10)
        self.assertEqual(errors, [])
        json.loads(self.srv.CACHE.read_text(encoding="utf-8"))
        # The assigned units were present at the end of every round that checked.
        self.assertTrue({SRC, CAMERA, FRONT} <= set(self.cached()))

    def test_the_stores_are_always_taken_in_the_same_order(self):
        """Accounts, then groups. The other order is a deadlock waiting for a race."""
        source = (ROOT / "omni_inventory.py").read_text(encoding="utf-8")
        body = source[source.index("def hold_assignments("):]
        body = body[:body.index("\ndef ")] if "\ndef " in body else body
        code = "\n".join(l.split("#")[0] for l in body.split("\n"))
        self.assertLess(code.index("store._lock"), code.index("groups._lock"))


# ---------------------------------------------------------------- persistence
class PersistenceTests(InventoryBase):
    def test_what_was_kept_is_still_there_after_a_restart(self):
        self.clear()
        # A restart is every store reading its own file again.
        reopened_accounts = omni_users.UserStore(self.store._path, self.store._audit_path)
        reopened_groups = omni_groups.GroupStore(self.groups._path, canonical)
        teacher = reopened_accounts.find_by_username("teacher")
        self.assertEqual(set(teacher.permissions.sources), {SRC, CAMERA})
        self.assertEqual(set(teacher.permissions.destinations), {FRONT})
        self.assertEqual(sorted(g for g in reopened_groups.members_snapshot().values())[0],
                         [ROOM_A, ROOM_B])
        on_disk = json.loads(self.srv.CACHE.read_text(encoding="utf-8"))
        self.assertEqual(sorted(omni_users._normalise_identity(u["mac"]) for u in on_disk),
                         sorted([SRC, CAMERA, FRONT, ROOM_A, ROOM_B]))
        # And the decision after the restart is the same decision.
        report = omni_inventory.report(
            on_disk, omni_inventory.collect(reopened_accounts, reopened_groups),
            remote_access=True, display_name=lambda u: u["hostname"])
        self.assertEqual(report["clearable"], 0)


# ------------------------------------------------------------ module-level rules
class InventoryModuleTests(unittest.TestCase):
    def test_summary_text(self):
        self.assertEqual(omni_inventory.summary_text([], []), "Unassigned")
        self.assertEqual(omni_inventory.summary_text(["a"], []), "1 User")
        self.assertEqual(omni_inventory.summary_text(["a", "b"], ["g"]), "2 Users · 1 Group")
        self.assertEqual(omni_inventory.summary_text([], ["g", "h"]), "2 Groups")

    def test_classification_is_ordered_and_fails_open_only_for_remote_off(self):
        units = [{"mac": "b8:98:b0:00:00:01"}, {"mac": "b8:98:b0:00:00:02"}, {"hostname": "x"}]
        assignments = {"b8:98:b0:00:00:01": {"users": ["u"], "direct": [], "groups": []}}
        kept, clearable = omni_inventory.classify(units, assignments, True)
        self.assertEqual([u.get("mac") for u in kept], ["b8:98:b0:00:00:01"])
        self.assertEqual(len(clearable), 2)
        kept, clearable = omni_inventory.classify(units, assignments, False)
        self.assertEqual((len(kept), len(clearable)), (0, 3))

    def test_no_live_value_appears_in_the_module(self):
        source = (ROOT / "omni_inventory.py").read_text(encoding="utf-8")
        import re
        self.assertIsNone(re.search(r"192\.168\.\d+\.\d+", source))
        self.assertIsNone(re.search(r"\b[0-9a-f]{2}(:[0-9a-f]{2}){5}\b", source.lower()))


class UserGuideTests(unittest.TestCase):
    """The guide ships in the build, so it must say what this build does."""

    @classmethod
    def setUpClass(cls):
        import re
        text = (ROOT / "ui" / "user-guide.html").read_text(encoding="utf-8")
        start = text.index('id="clear-assigned"')
        end = text.index("<h3>Clear Units</h3>", start)
        cls.section = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", text[start:end]))
        cls.whole = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", text))

    def test_it_describes_both_behaviours_and_the_two_step_removal(self):
        for needle in ("While Remote Access is on: assigned units are kept",
                       "While Remote Access is off: everything is cleared",
                       "Cancel", "View Assignments", "Clear Unassigned Units",
                       "turn Remote Access off in Settings first",
                       "There is deliberately no single button that does both",
                       "nothing is cleared"):
            self.assertIn(needle, self.section)

    def test_it_says_what_restore_does_to_what_the_backup_lacks(self):
        self.assertIn("The backup is the authority for what it contains", self.whole)
        self.assertIn("a backup made before a kind of setting existed says nothing",
                      self.whole.lower())

    def test_the_device_names_page_is_described(self):
        self.assertIn("Assignments column says what is using each unit", self.whole)

    def test_it_is_written_for_an_operator(self):
        for internal in ("/api/", "omni_inventory", "endpoint", "classify", "inventory.cleared",
                         "REMOVED_WHEN_ABSENT", "thread", "mutex"):
            self.assertNotIn(internal, self.section)


if __name__ == "__main__":
    unittest.main()
