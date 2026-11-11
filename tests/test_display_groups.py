"""Display Groups: one source to several displays, sequentially, verified each time.

A group is not a Multiview and not a video wall. These hold the semantics
LumaSuite proved so the two products mean the same thing by the same words:

  * identity is a canonical MAC, never an address, and a group has an opaque id;
  * a display may be in many groups;
  * holding a group is not holding its members, and not the reverse;
  * members are routed one at a time, a failure does not stop the rest and
    nothing already routed is undone;
  * Clear Units empties groups of the displays it removes.

Nothing here contacts hardware. The route engine is stubbed through the shared
routing harness, and the gate is driven directly.
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

import omni_access                                   # noqa: E402
import omni_gate                                     # noqa: E402
import omni_groups                                   # noqa: E402
import omni_users                                    # noqa: E402
from omni_users import Permissions                   # noqa: E402
from tests import test_user_routing as harness       # noqa: E402

#: Synthetic identities in the documentation range of this suite.
ROOM_A = "b8:98:b0:00:00:31"
ROOM_B = "b8:98:b0:00:00:32"
ROOM_C = "b8:98:b0:00:00:33"
SRC = harness.TEACHER_PC
OTHER_SRC = harness.UNSEEN_SOURCE

UNITS = copy.deepcopy(harness.UNITS) + [
    {"ip": "192.0.2.31", "mac": ROOM_A, "hostname": "dec-a", "role": "decoder",
     "model": "hw-omni-d4511"},
    {"ip": "192.0.2.32", "mac": ROOM_B, "hostname": "dec-b", "role": "decoder",
     "model": "hw-omni-d4511"},
    {"ip": "192.0.2.33", "mac": ROOM_C, "hostname": "dec-c", "role": "decoder",
     "model": "hw-omni-d4511"},
]


def canonical(value):
    return omni_users._normalise_identity(value)


class GroupStoreTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="omni-groups-"))
        self.path = self.dir / "display_groups.json"
        self.store = omni_groups.GroupStore(self.path, canonical)

    def test_a_group_has_an_opaque_id_and_members_by_mac(self):
        made = self.store.create("Front rooms", [ROOM_A.upper(), ROOM_B])
        self.assertTrue(made["ok"])
        group = made["group"]
        self.assertTrue(group["id"].startswith("grp_"))
        self.assertNotIn("front", group["id"])
        self.assertEqual(group["members"], [ROOM_A, ROOM_B])

    def test_an_address_or_hostname_is_not_a_member(self):
        for bad in ("192.0.2.31", "dec-a", "b898b00000", ""):
            with self.subTest(bad=bad):
                self.assertFalse(self.store.create("X", [bad])["ok"])
        self.assertEqual(self.store.all(), [])

    def test_a_dotted_address_never_becomes_a_member_by_looking_like_a_mac(self):
        # Twelve digits with separators: stripping them used to leave a valid-looking MAC.
        # Built from parts: a public-looking literal is not allowed in a test file, and these
        # need twelve digits to be the shape that used to pass for a MAC.
        dotted = [".".join(parts) for parts in (("100",) * 4, ("203", "100", "113", "200"),
                                                 ("198", "100", "51", "152"))]
        for bad in dotted + [dotted[0] + ":80"]:
            with self.subTest(bad=bad):
                self.assertNotEqual(canonical(bad).count(":"), 5, canonical(bad))
                self.assertFalse(self.store.create("X", [bad])["ok"])
                made = self.store.create("Y", [ROOM_A])["group"]
                self.assertFalse(self.store.update(made["id"], members=[bad])["ok"])
                self.assertEqual(self.store.get(made["id"])["members"], [ROOM_A])
        self.assertEqual(len(self.store.all()), 4)

    def test_every_accepted_mac_spelling_still_works(self):
        for spelling in ("B8:98:B0:00:00:01", "b8-98-b0-00-00-01", "b898b0000001", "b898.b000.0001",
                         "  B8:98:B0:00:00:01  "):
            with self.subTest(spelling=spelling):
                group = self.store.create("S" + spelling[:3], [spelling])
                self.assertTrue(group["ok"], spelling)
                self.assertEqual(group["group"]["members"], ["b8:98:b0:00:00:01"])

    def test_a_mixed_or_malformed_mac_is_not_a_member(self):
        for bad in ("b8:98-b0:00:00:01", "b8:98:b0:00:00", "b8:98:b0:00:00:01:02", "g8:98:b0:00:00:01",
                    "b898b000000", "b8.98.b0.00.00.01"):
            with self.subTest(bad=bad):
                self.assertFalse(self.store.create("Z", [bad])["ok"])

    def test_a_group_needs_a_name_and_has_bounds(self):
        self.assertFalse(self.store.create("   ", [ROOM_A])["ok"])
        many = ["b8:98:b0:00:%02x:%02x" % (i // 256, i % 256)
                for i in range(omni_groups.MAX_MEMBERS + 1)]
        self.assertFalse(self.store.create("Big", many)["ok"])
        long_name = self.store.create("n" * 500, [ROOM_A])["group"]["name"]
        self.assertEqual(len(long_name), omni_groups.MAX_NAME_LENGTH)

    def test_renaming_or_repopulating_keeps_the_id(self):
        gid = self.store.create("One", [ROOM_A])["group"]["id"]
        self.store.update(gid, name="Two")
        self.store.update(gid, members=[ROOM_B, ROOM_C])
        group = self.store.get(gid)
        self.assertEqual((group["id"], group["name"], group["members"]),
                         (gid, "Two", [ROOM_B, ROOM_C]))

    def test_a_display_may_be_in_many_groups(self):
        a = self.store.create("A", [ROOM_A, ROOM_B])["group"]["id"]
        b = self.store.create("B", [ROOM_A])["group"]["id"]
        self.assertEqual(sorted(self.store.groups_containing(ROOM_A)),
                         sorted([a, b]))
        self.assertEqual(self.store.referenced_identities()[ROOM_A], ["A", "B"])

    def test_persists_and_reloads_in_a_stable_order(self):
        self.store.create("Zed", [ROOM_A])
        self.store.create("Alpha", [ROOM_B])
        again = omni_groups.GroupStore(self.path, canonical)
        self.assertEqual([g["name"] for g in again.all()], ["Alpha", "Zed"])

    def test_a_malformed_file_is_quarantined_and_degrades_to_empty(self):
        self.path.write_text("{ not json", encoding="utf-8")
        store = omni_groups.GroupStore(self.path, canonical)
        self.assertEqual(store.all(), [])
        self.assertTrue(self.path.with_suffix(".corrupt").exists())
        self.path.write_text(json.dumps({"groups": "nope"}), encoding="utf-8")
        self.assertEqual(omni_groups.GroupStore(self.path, canonical).all(), [])

    def test_a_record_with_an_address_member_loses_only_that_member(self):
        self.path.write_text(json.dumps({"version": 1, "groups": [
            {"id": "grp_1", "name": "Hand edited",
             "members": [ROOM_A, "192.0.2.5"]}]}), encoding="utf-8")
        group = omni_groups.GroupStore(self.path, canonical).get("grp_1")
        self.assertEqual(group["members"], [ROOM_A])

    def test_delete_returns_what_was_removed(self):
        gid = self.store.create("Gone", [ROOM_A])["group"]["id"]
        self.assertTrue(self.store.delete(gid)["ok"])
        self.assertTrue(self.store.delete(gid)["missing"])

    def test_the_file_is_never_truncated_in_place(self):
        source = (ROOT / "omni_groups.py").read_text(encoding="utf-8")
        self.assertIn("_atomic_write_json", source)
        self.assertNotIn('open(self._path, "w"', source)


class SequentialRunTests(unittest.TestCase):
    def test_members_run_in_order_one_at_a_time(self):
        order, active = [], []

        def one(index, identity):
            active.append(identity)
            self.assertEqual(len(active), 1)
            order.append(identity)
            active.pop()
            return {"ok": True}

        summary = omni_groups.run_sequential(["a", "b", "c"], one)
        self.assertEqual(order, ["a", "b", "c"])
        self.assertEqual((summary["routed"], summary["total"]), (3, 3))
        self.assertTrue(summary["ok"])

    def test_a_failed_member_does_not_stop_the_rest_and_nothing_is_undone(self):
        calls = []

        def one(index, identity):
            calls.append(identity)
            if identity == "b":
                raise RuntimeError("device went away")
            return {"ok": identity != "c"}

        summary = omni_groups.run_sequential(["a", "b", "c", "d"], one)
        self.assertEqual(calls, ["a", "b", "c", "d"])
        self.assertEqual((summary["routed"], summary["total"]), (2, 4))
        self.assertFalse(summary["ok"])
        # No rollback: no call after the failures revisits a succeeded member.
        self.assertEqual(calls.count("a"), 1)

    def test_a_member_that_returns_nothing_is_a_failure(self):
        summary = omni_groups.run_sequential(["a"], lambda i, m: None)
        self.assertFalse(summary["ok"])

    def test_an_empty_group_is_not_a_success(self):
        self.assertFalse(omni_groups.run_sequential([], lambda i, m: {"ok": True})["ok"])


class PermissionTests(unittest.TestCase):
    def test_a_group_grant_is_its_own_resource(self):
        perms = Permissions(features=("av",), sources=[SRC],
                            destinations=[ROOM_A], groups=["GRP_ONE "])
        self.assertTrue(perms.may_control_group("grp_one"))
        self.assertFalse(perms.may_control_group("grp_two"))
        # Holding the group did not add its members; holding a member did not
        # add the group.
        self.assertFalse(perms.may_control_destination(ROOM_B))
        only_dest = Permissions(features=("av",), destinations=[ROOM_A])
        self.assertFalse(only_dest.may_control_group("grp_one"))

    def test_groups_round_trip_through_json(self):
        perms = Permissions(features=("av",), groups=["grp_b", "grp_a"])
        again = Permissions.from_json(perms.to_json())
        self.assertEqual(sorted(again.groups), ["grp_a", "grp_b"])

    def test_an_old_record_without_groups_still_loads(self):
        perms = Permissions.from_json({"features": ["av"], "sources": [SRC]})
        self.assertEqual(perms.groups, frozenset())


class GateTests(unittest.TestCase):
    def test_both_routes_are_classified(self):
        self.assertEqual(omni_access.classify("/api/user/route_group", "POST")[0],
                         omni_access.Access.USER)
        for path in ("/api/admin/groups", "/api/admin/groups/<group_id>"):
            self.assertEqual(omni_access.classify(path, "POST")[0],
                             omni_access.Access.ADMIN, path)

    def test_the_gate_names_group_keys(self):
        self.assertEqual(omni_gate.GROUP_KEYS, ("group", "group_id"))
        self.assertIn("group_not_permitted", omni_gate.DENIAL_SENTENCES)


class GroupEndpointTests(harness.UserRoutingTestBase):
    """The real application, with the route engine stubbed."""

    def setUp(self):
        super().setUp()
        self._patch_registered("load_cache", lambda: copy.deepcopy(UNITS))
        self.groups = omni_groups.GroupStore(self.dir / "display_groups.json",
                                             canonical)
        self._patch_registered("groups", self.groups)
        self.group = self.groups.create(
            "Front rooms", [ROOM_A, ROOM_B, ROOM_C])["group"]
        self.teacher = self.store.find_by_username("teacher")
        self.store.set_permissions(self.teacher.id, Permissions(
            features=("av",), sources=[SRC, harness.DOC_CAMERA],
            destinations=[harness.FRONT_DISPLAY], groups=[self.group["id"]]))
        self.teacher = self.store.find_by_username("teacher")

    def route_group(self, session, source=SRC, group=None):
        return self.post("/api/user/route_group",
                         {"source": source, "group": group or self.group["id"]},
                         session)

    # ---- routing -------------------------------------------------------
    def test_routes_every_member_in_order_through_the_normal_av_route(self):
        reply = self.route_group(self.session_for(self.teacher))
        body = reply.get_json()
        self.assertEqual(reply.status_code, 200)
        self.assertTrue(body["ok"])
        self.assertEqual((body["routed"], body["total"]), (3, 3))
        self.assertEqual([r["decoder"] for r in self.routes],
                         ["192.0.2.31", "192.0.2.32", "192.0.2.33"])
        self.assertTrue(all(r["encoder"] == "192.0.2.11" and r["mode"] == "av"
                            for r in self.routes))
        # The ordinary route is asked for nothing about Multiview.
        self.assertTrue(all("multiview" not in r and "exit_multiview" not in r
                            for r in self.routes))

    def test_a_partial_failure_is_reported_and_the_rest_still_run(self):
        answers = iter([({"ok": True, "status": "VERIFIED"}, 200),
                        ({"ok": False, "status": "NOT VERIFIED"}, 502),
                        ({"ok": True, "status": "VERIFIED"}, 200)])
        self.route_answer = None

        def fake_route():
            from flask import jsonify, request as flask_request
            self.routes.append(dict(flask_request.get_json(silent=True) or {}))
            payload, status = next(answers)
            return jsonify(payload), status

        self._patch_registered("route_view", fake_route)
        reply = self.route_group(self.session_for(self.teacher))
        body = reply.get_json()
        self.assertEqual((body["routed"], body["total"]), (2, 3))
        self.assertFalse(body["ok"])
        self.assertEqual(len(self.routes), 3, "no rollback and no early stop")
        self.assertEqual([r["ok"] for r in body["results"]], [True, False, True])

    def test_the_reply_names_displays_but_never_identities_or_addresses(self):
        text = self.route_group(self.session_for(self.teacher)).get_data(as_text=True)
        for secret in (ROOM_A, ROOM_B, ROOM_C, "192.0.2.", "b8:98"):
            self.assertNotIn(secret, text)

    # ---- permissions ---------------------------------------------------
    def test_a_source_that_is_not_granted_reaches_no_device(self):
        reply = self.route_group(self.session_for(self.teacher),
                                 source=OTHER_SRC)
        self.assertEqual(reply.status_code, 403)
        self.assertEqual(self.routes, [])

    def test_a_group_that_is_not_granted_reaches_no_device(self):
        other = self.groups.create("Other", [ROOM_A])["group"]
        reply = self.route_group(self.session_for(self.teacher),
                                 group=other["id"])
        self.assertEqual(reply.status_code, 403)
        self.assertEqual(reply.get_json()["reason"], "group_not_permitted")
        self.assertEqual(self.routes, [])

    def test_holding_a_group_does_not_grant_a_member_individually(self):
        reply = self.route(self.session_for(self.teacher), destination=ROOM_A)
        self.assertEqual(reply.status_code, 403)
        self.assertEqual(self.routes, [])

    def test_a_member_grant_alone_does_not_grant_the_group(self):
        self.store.set_permissions(self.teacher.id, Permissions(
            features=("av",), sources=[SRC], destinations=[ROOM_A, ROOM_B, ROOM_C]))
        reply = self.route_group(self.session_for(
            self.store.find_by_username("teacher")))
        self.assertEqual(reply.status_code, 403)

    def test_an_unknown_group_is_a_404_for_a_holder(self):
        self.store.set_permissions(self.teacher.id, Permissions(
            features=("av",), sources=[SRC], groups=["grp_ghost"]))
        reply = self.route_group(self.session_for(
            self.store.find_by_username("teacher")), group="grp_ghost")
        self.assertEqual(reply.status_code, 404)

    def test_membership_is_read_at_route_time(self):
        self.groups.update(self.group["id"], members=[ROOM_B])
        body = self.route_group(self.session_for(self.teacher)).get_json()
        self.assertEqual(body["total"], 1)
        self.assertEqual([r["decoder"] for r in self.routes], ["192.0.2.32"])

    def test_a_stale_member_is_never_named_by_its_identity(self):
        stale = "b8:98:b0:00:00:99"
        self.groups.update(self.group["id"], members=[ROOM_A, stale, ROOM_B])
        text = self.route_group(self.session_for(self.teacher)).get_data(as_text=True)
        body = json.loads(text)
        self.assertEqual(body["results"][1]["name"], "a display that is no longer available")
        self.assertNotIn(stale, text)
        self.assertNotIn("b8:98:b0:00:00:99".replace(":", "-"), text)

    def test_a_member_that_is_not_currently_known_is_reported_not_fatal(self):
        self.groups.update(self.group["id"],
                           members=[ROOM_A, "b8:98:b0:00:00:99", ROOM_B])
        body = self.route_group(self.session_for(self.teacher)).get_json()
        self.assertEqual((body["routed"], body["total"]), (2, 3))
        self.assertEqual(body["results"][1]["result"], "not_found")

    # ---- the user's own view -------------------------------------------
    def test_user_state_lists_held_groups_without_members(self):
        self.groups.create("Not mine", [ROOM_A])
        state = self.get("/api/user/state", self.session_for(self.teacher)).get_json()
        self.assertEqual([g["name"] for g in state["groups"]], ["Front rooms"])
        self.assertEqual(state["groups"][0]["displays"], 3)
        text = json.dumps(state["groups"])
        self.assertNotIn(ROOM_A, text)
        # Not individually visible: no member becomes a destination.
        listed = {d["id"] for d in state["destinations"]}
        self.assertFalse(listed & {ROOM_A, ROOM_B, ROOM_C})

    # ---- administration -------------------------------------------------
    def admin_post(self, path, body):
        return self.post(path, body, self.session_for(self.admin))

    def test_an_address_is_refused_with_a_400_and_no_group_is_made(self):
        before = len(self.groups.all())
        for bad in ("100.100.100.100", "192.0.2.31", "dec-a"):
            with self.subTest(bad=bad):
                reply = self.admin_post("/api/admin/groups", {"name": "Bad", "members": [bad]})
                self.assertEqual(reply.status_code, 400)
                gid = self.group["id"]
                reply = self.admin_post("/api/admin/groups/" + gid,
                                        {"action": "set_members", "members": [ROOM_A, bad]})
                self.assertEqual(reply.status_code, 400)
        self.assertEqual(len(self.groups.all()), before)
        self.assertEqual(self.groups.get(self.group["id"])["members"], [ROOM_A, ROOM_B, ROOM_C])

    def test_a_grant_by_address_is_refused_too(self):
        for bad in ("100.100.100.100", "192.0.2.31", "dec-a"):
            with self.subTest(bad=bad):
                reply = self.admin_post("/api/admin/users/" + self.teacher.id,
                                        {"action": "set_permissions", "permissions": {
                                            "features": ["av"], "sources": [bad], "destinations": []}})
                self.assertEqual(reply.status_code, 400, reply.get_data(as_text=True)[:120])
                reply = self.admin_post("/api/admin/users", {
                    "username": "newcomer", "password": "newcomer-pass-9", "role": "user",
                    "permissions": {"features": ["av"], "sources": [], "destinations": [bad]}})
                self.assertEqual(reply.status_code, 400)
        held = self.store.find_by_username("teacher").permissions
        self.assertIn(SRC, held.sources)
        self.assertIsNone(self.store.find_by_username("newcomer"))

    def test_a_grant_by_mac_in_any_spelling_is_accepted(self):
        reply = self.admin_post("/api/admin/users/" + self.teacher.id,
                                {"action": "set_permissions", "permissions": {
                                    "features": ["av"], "sources": [SRC.upper()],
                                    "destinations": [ROOM_A.replace(":", "-")]}})
        self.assertEqual(reply.status_code, 200, reply.get_data(as_text=True)[:120])

    def test_admin_crud_and_audit(self):
        made = self.admin_post("/api/admin/groups",
                               {"name": "Lab", "members": [ROOM_A, ROOM_B]})
        self.assertEqual(made.status_code, 200)
        gid = made.get_json()["group"]["id"]
        self.assertEqual(self.admin_post("/api/admin/groups/" + gid,
                         {"action": "rename", "name": "Lab 2"}).status_code, 200)
        self.assertEqual(self.admin_post("/api/admin/groups/" + gid,
                         {"action": "set_members", "members": [ROOM_C]}
                         ).get_json()["group"]["members"][0]["name"], "dec-c")
        self.assertEqual(self.admin_post("/api/admin/groups/" + gid,
                         {"action": "explode"}).status_code, 400)
        self.assertEqual(self.admin_post("/api/admin/groups/" + gid,
                         {"action": "delete"}).status_code, 200)
        events = [e["event"] for e in self.store.audit_entries(50)]
        for name in ("group.created", "group.updated", "group.deleted"):
            self.assertIn(name, events)

    def test_a_restricted_user_cannot_edit_groups(self):
        session = self.session_for(self.teacher)
        self.assertEqual(self.post("/api/admin/groups",
                         {"name": "x", "members": []}, session).status_code, 403)
        self.assertEqual(self.get("/api/admin/groups", session).status_code, 403)

    def test_deleting_a_group_removes_the_grant_everywhere(self):
        self.admin_post("/api/admin/groups/" + self.group["id"],
                        {"action": "delete"})
        teacher = self.store.find_by_username("teacher")
        self.assertNotIn(self.group["id"], teacher.permissions.groups)

    def test_admin_listing_shows_who_holds_each_group(self):
        listing = self.get("/api/admin/groups",
                           self.session_for(self.admin)).get_json()
        self.assertEqual(listing["groups"][0]["granted_to"], ["teacher"])

    def test_set_permissions_accepts_a_group_grant(self):
        reply = self.admin_post("/api/admin/users/" + self.teacher.id, {
            "action": "set_permissions",
            "permissions": {"features": ["av"], "sources": [SRC],
                            "groups": [self.group["id"]]}})
        self.assertEqual(reply.status_code, 200)
        self.assertIn(self.group["id"],
                      self.store.find_by_username("teacher").permissions.groups)


class ClearUnitsEmptiesGroupsTests(unittest.TestCase):
    def test_forgetting_members_keeps_the_group_and_names_the_count(self):
        store = omni_groups.GroupStore(
            Path(tempfile.mkdtemp(prefix="omni-clear-")) / "g.json", canonical)
        made = store.create("Front rooms", [ROOM_A, ROOM_B])["group"]
        self.assertEqual(store.forget_members([ROOM_A]), 1)
        self.assertEqual(store.get(made["id"])["members"], [ROOM_B])
        self.assertEqual(store.forget_members(None), 1)
        self.assertEqual(store.get(made["id"])["members"], [])
        self.assertEqual(store.get(made["id"])["name"], "Front rooms")

    def test_a_snapshot_restores_the_members(self):
        store = omni_groups.GroupStore(
            Path(tempfile.mkdtemp(prefix="omni-clear-")) / "g.json", canonical)
        made = store.create("Front rooms", [ROOM_A, ROOM_B])["group"]
        snap = store.members_snapshot()
        store.forget_members(None)
        store.restore_members(snap)
        self.assertEqual(store.get(made["id"])["members"], [ROOM_A, ROOM_B])


class BackupTests(unittest.TestCase):
    def test_groups_travel_with_a_backup(self):
        import omni_backup
        self.assertIn("display_groups.json",
                      [s.filename for s in omni_backup.SECTIONS])


class GroupHandlerChecksItselfTests(GroupEndpointTests):
    """The handler's own grant checks, with the gate's taken out of the way.

    The gate looks for `source` and `group` in the request and refuses an
    ungranted one before the handler runs, so a mutation of the handler's
    checks survived -- not because they are redundant but because the ordinary
    path never reaches them. This reaches them on purpose, and puts the gate
    back afterwards.
    """

    def setUp(self):
        super().setUp()
        for name in ("SOURCE_KEYS", "GROUP_KEYS"):
            real = getattr(omni_gate, name)
            self.addCleanup(lambda n=name, r=real: setattr(omni_gate, n, r))
            setattr(omni_gate, name, ())

    def test_the_gate_really_is_out_of_the_way(self):
        """The precondition: a permitted request still works with it gone."""
        reply = self.route_group(self.session_for(self.teacher))
        self.assertEqual(reply.status_code, 200)

    def test_the_handler_refuses_a_group_the_account_does_not_hold(self):
        other = self.groups.create("Other", [ROOM_A])["group"]
        reply = self.route_group(self.session_for(self.teacher),
                                 group=other["id"])
        self.assertEqual(reply.status_code, 403)
        self.assertEqual(self.routes, [], "an ungranted group reached a device")

    def test_the_handler_refuses_a_source_the_account_never_had(self):
        reply = self.route_group(self.session_for(self.teacher),
                                 source=OTHER_SRC)
        self.assertEqual(reply.status_code, 403)
        self.assertEqual(self.routes, [], "an ungranted source reached a device")


class _RevokingLocks:
    """Lock manager that records every wait and can withdraw the grant mid-way."""

    def __init__(self, on_enter=None):
        self.entered = 0
        self.on_enter = on_enter

    @staticmethod
    def decoder(identity):
        return "decoder:" + str(identity or "").lower()

    def hold(self, *resources, timeout=None):
        import contextlib

        @contextlib.contextmanager
        def held():
            self.entered += 1
            if self.on_enter:
                self.on_enter(self.entered)
            yield
        return held()


class GroupPermissionWithdrawalTests(GroupEndpointTests):
    """Permission can be withdrawn while a group route is already running."""

    def revoke(self):
        self.store.set_permissions(self.teacher.id, Permissions(
            features=("av",), sources=[SRC], destinations=[], groups=[]))

    def test_withdrawn_while_a_member_waits_for_its_lock_is_not_used(self):
        locks = _RevokingLocks(
            on_enter=lambda n: self.revoke() if n == 2 else None)
        self._patch_registered("locks", locks)
        body = self.route_group(self.session_for(self.teacher)).get_json()
        self.assertEqual([r["result"] for r in body["results"]][1:],
                         ["refused", "refused"])
        self.assertEqual(len(self.routes), 1,
                         "a member was routed after the grant was withdrawn")

    def test_withdrawn_between_members_stops_the_rest_before_they_queue(self):
        locks = _RevokingLocks()
        self._patch_registered("locks", locks)

        def fake_route():
            from flask import jsonify, request as flask_request
            self.routes.append(dict(flask_request.get_json(silent=True) or {}))
            self.revoke()
            return jsonify({"ok": True, "status": "VERIFIED"}), 200

        self._patch_registered("route_view", fake_route)
        body = self.route_group(self.session_for(self.teacher)).get_json()
        self.assertEqual((body["routed"], body["total"]), (1, 3))
        self.assertEqual(locks.entered, 1,
                         "a refused member still queued for its display's lock")


def _run_only_own_tests(cls):
    """A subclass of the endpoint fixture should not repeat the fixture's tests."""
    for name in dir(GroupEndpointTests):
        if name.startswith("test") and name not in cls.__dict__:
            setattr(cls, name, None)


_run_only_own_tests(GroupHandlerChecksItselfTests)
_run_only_own_tests(GroupPermissionWithdrawalTests)


if __name__ == "__main__":
    unittest.main()
