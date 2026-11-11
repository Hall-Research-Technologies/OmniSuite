"""Display Group integrity: a group route reaches its members and nobody else.

Routing to a destination outside the selected group is a release blocker. These
tests drive the real application (route engine stubbed, hardware fenced) through
the whole life of two groups and assert, at every step, which addresses reached
the route engine -- never which ones the page thought it was sending.

Everything is synthetic: documentation-range addresses and MACs that share
nothing with any bench value. Fixtures are deep-copied because background work
mutates cached device dicts in place.
"""
from __future__ import annotations

import copy
import logging
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("OMNI_SMOKE", "1")

import omni_aliases                                  # noqa: E402
import omni_groups                                   # noqa: E402
import omni_users                                    # noqa: E402
from omni_users import Permissions                   # noqa: E402
from tests import test_user_routing as harness       # noqa: E402

SRC = harness.TEACHER_PC


def mac(n):
    return "b8:98:b0:00:07:%02x" % n


def ip(n):
    return "198.51.100.%d" % n


def decoder(n, address=None, host=None):
    return {"ip": address or ip(n), "mac": mac(n),
            "hostname": host or "dec-%d" % n, "role": "decoder",
            "model": "hw-omni-d4511"}


def canonical(value):
    return omni_users._normalise_identity(value)


class GroupLifecycleBase(harness.UserRoutingTestBase):
    def setUp(self):
        super().setUp()
        # Six decoders: 1-4 are group members at some point, 5 and 6 never are.
        self.cache = copy.deepcopy(harness.UNITS) + [decoder(n) for n in range(1, 7)]
        self._patch_registered("load_cache", lambda: copy.deepcopy(self.cache))
        self.group_path = self.dir / "display_groups.json"
        self.alias_store = omni_aliases.AliasStore(self.dir / "aliases.json")
        self._patch_registered("aliases", self.alias_store)
        self.use_store(omni_groups.GroupStore(self.group_path, canonical))
        self.admin_session = self.session_for(self.admin)

    def use_store(self, store):
        self.groups = store
        self._patch_registered("groups", store)

    # ---- helpers --------------------------------------------------------
    def admin_post(self, path, body):
        return self.post(path, body, self.admin_session)

    def make_group(self, name, members):
        reply = self.admin_post("/api/admin/groups",
                                {"name": name, "members": [mac(n) for n in members]})
        self.assertEqual(reply.status_code, 200, reply.get_data(as_text=True))
        return reply.get_json()["group"]["id"]

    def grant(self, *group_ids):
        teacher = self.store.find_by_username("teacher")
        self.store.set_permissions(teacher.id, Permissions(
            features=("av",), sources=[SRC], groups=list(group_ids)))
        return self.store.find_by_username("teacher")

    def route_group(self, group_id, source=SRC):
        teacher = self.store.find_by_username("teacher")
        self.routes.clear()
        return self.post("/api/user/route_group",
                         {"source": source, "group": group_id},
                         self.session_for(teacher))

    def targeted(self):
        return [r["decoder"] for r in self.routes]

    def membership(self, group_id):
        listing = self.get("/api/admin/groups", self.admin_session).get_json()
        for group in listing["groups"]:
            if group["id"] == group_id:
                return [m["id"] for m in group["members"]]
        return None

    def address_of(self, identity):
        return next(u["ip"] for u in self.cache if u.get("mac") == identity)

    def assert_only(self, reply, members):
        """Every intended member attempted, nobody else, in order."""
        self.assertEqual(reply.status_code, 200, reply.get_data(as_text=True))
        self.assertEqual(self.targeted(),
                         [self.address_of(mac(n)) for n in members])
        body = reply.get_json()
        self.assertEqual((body["routed"], body["total"]),
                         (len(members), len(members)))

    def rediscover(self, fresh):
        """Fold a scan into the cache with the application's own reconcile."""
        self.cache = self.srv._reconcile_discovered_units(
            copy.deepcopy(self.cache), copy.deepcopy(fresh))


class GroupLifecycleTests(GroupLifecycleBase):
    def test_the_whole_life_of_two_groups(self):
        a = self.make_group("Room A", [1, 2])
        b = self.make_group("Room B", [3, 4])
        self.grant(a, b)
        self.assertNotEqual(a, b)
        self.assertEqual(self.membership(a), [mac(1), mac(2)])
        self.assertEqual(self.membership(b), [mac(3), mac(4)])

        # route A, then B: each reaches its own members and nobody else.
        self.assert_only(self.route_group(a), [1, 2])
        self.assert_only(self.route_group(b), [3, 4])

        # edit A: membership replaced, not appended to.
        self.assertEqual(self.admin_post("/api/admin/groups/" + a, {
            "action": "set_members", "members": [mac(1), mac(5)]}).status_code, 200)
        self.assertEqual(self.membership(a), [mac(1), mac(5)])
        self.assert_only(self.route_group(a), [1, 5])
        self.assertEqual(self.membership(b), [mac(3), mac(4)],
                         "B untouched by editing A")

        # rename the group and a display: identity, not name, decides.
        self.admin_post("/api/admin/groups/" + a, {"action": "rename", "name": "Hall"})
        self.alias_store.set_alias(mac(5), "Renamed Display")
        for unit in self.cache:
            if unit.get("mac") == mac(5):
                unit["hostname"] = "dec-renamed"
        self.assert_only(self.route_group(a), [1, 5])

        # rescan: decoder 1 moves, and decoder 6 -- never in any group -- takes
        # the address decoder 1 just left. An address-keyed group would now
        # route decoder 6.
        old_one = self.address_of(mac(1))
        self.rediscover([decoder(1, address=ip(101)), decoder(6, address=old_one)])
        self.assertEqual(self.address_of(mac(1)), ip(101))
        self.assert_only(self.route_group(a), [1, 5])
        self.assertEqual(self.targeted()[0], ip(101))
        self.assertNotIn(old_one, self.targeted())
        self.assertEqual(self.membership(a), [mac(1), mac(5)])

        # restart: a fresh store from the same file.
        self.use_store(omni_groups.GroupStore(self.group_path, canonical))
        self.assert_only(self.route_group(a), [1, 5])
        self.assert_only(self.route_group(b), [3, 4])

        # delete A: its id resolves to nothing and nothing is routed.
        self.assertEqual(self.admin_post("/api/admin/groups/" + a,
                                         {"action": "delete"}).status_code, 200)
        self.assertIsNone(self.membership(a))
        gone = self.route_group(a)
        self.assertIn(gone.status_code, (403, 404))
        self.assertEqual(self.routes, [])

        # recreate A with the same name and different members: a new identity,
        # no inherited grant, no inherited members.
        a2 = self.make_group("Hall", [2])
        self.assertNotEqual(a2, a)
        self.assertEqual(self.route_group(a2).status_code, 403,
                         "the grant is not inherited")
        self.assertEqual(self.routes, [])
        self.grant(b, a2)
        self.assert_only(self.route_group(a2), [2])
        self.assertEqual(self.route_group(a).status_code, 403)
        self.assertEqual(self.routes, [])
        self.assert_only(self.route_group(b), [3, 4])

    def test_failures_are_truthful_and_never_widen_the_set(self):
        a = self.make_group("Room A", [1, 2, 3])
        self.grant(a)
        bad = self.address_of(mac(2))

        def fake_route():
            from flask import jsonify, request as flask_request
            body = dict(flask_request.get_json(silent=True) or {})
            self.routes.append(body)
            if body["decoder"] == bad:
                return jsonify({"ok": False, "status": "NOT VERIFIED"}), 502
            return jsonify({"ok": True, "status": "VERIFIED"}), 200

        self._patch_registered("route_view", fake_route)
        body = self.route_group(a).get_json()
        self.assertEqual([r["ok"] for r in body["results"]], [True, False, True])
        self.assertEqual((body["routed"], body["total"]), (2, 3))
        self.assertFalse(body["ok"])
        self.assertEqual(len(self.routes), 3)
        self.assertEqual(sorted(self.targeted()),
                         sorted(self.address_of(mac(n)) for n in (1, 2, 3)))

    def test_a_member_that_left_the_inventory_is_reported_not_substituted(self):
        a = self.make_group("Room A", [1, 2])
        self.grant(a)
        self.cache = [u for u in self.cache if u.get("mac") != mac(2)]
        body = self.route_group(a).get_json()
        self.assertEqual(self.targeted(), [ip(1)])
        self.assertEqual(body["results"][1]["result"], "not_found")
        self.assertFalse(body["ok"])

    def test_clearing_members_does_not_leave_stale_ones_to_resolve(self):
        a = self.make_group("Room A", [1, 2])
        self.grant(a)
        self.groups.forget_members([mac(1)])
        self.assertEqual(self.membership(a), [mac(2)])
        self.assert_only(self.route_group(a), [2])

    def test_a_display_in_two_groups_is_routed_by_whichever_is_chosen(self):
        a = self.make_group("Room A", [1, 2])
        b = self.make_group("Room B", [2, 3])
        self.grant(a, b)
        self.assert_only(self.route_group(a), [1, 2])
        self.assert_only(self.route_group(b), [2, 3])

    def test_a_group_id_is_never_reused_after_delete_and_recreate(self):
        seen = set()
        for _ in range(25):
            gid = self.make_group("Same", [1])
            self.assertNotIn(gid, seen)
            seen.add(gid)
            self.admin_post("/api/admin/groups/" + gid, {"action": "delete"})

    def test_the_request_names_a_group_by_id_only(self):
        a = self.make_group("Room A", [1, 2])
        b = self.make_group("Room B", [3, 4])
        self.grant(a, b)
        teacher = self.store.find_by_username("teacher")
        # Extra fields a stale or hostile page might send are not a way to
        # widen the target set.
        self.routes.clear()
        reply = self.post("/api/user/route_group", {
            "source": SRC, "group": a, "members": [mac(5), mac(6)],
            "destinations": [ip(5)]}, self.session_for(teacher))
        self.assertEqual(reply.status_code, 200)
        self.assertEqual(self.targeted(), [ip(1), ip(2)])
        # A key that names a destination is refused by the gate outright.
        self.routes.clear()
        reply = self.post("/api/user/route_group", {
            "source": SRC, "group": a, "destination": mac(6),
            "decoder": ip(6)}, self.session_for(teacher))
        self.assertGreaterEqual(reply.status_code, 400)
        self.assertEqual(self.routes, [])

    def test_the_intended_identities_are_logged_before_the_route_and_audited(self):
        a = self.make_group("Room A", [1, 2])
        self.grant(a)
        order = []

        def fake_route():
            from flask import jsonify, request as flask_request
            order.append("route")
            self.routes.append(dict(flask_request.get_json(silent=True) or {}))
            return jsonify({"ok": True, "status": "VERIFIED"}), 200

        self._patch_registered("route_view", fake_route)
        marks = []

        class Mark(logging.Handler):
            def emit(self, record):
                if record.getMessage().startswith("[GROUP-ROUTE]"):
                    order.append("log")
                    marks.append(record)

        handler = Mark()
        logger = logging.getLogger("omni_remote_api")
        previous = logger.level
        logger.setLevel(logging.INFO)
        logger.addHandler(handler)
        try:
            self.route_group(a)
        finally:
            logger.removeHandler(handler)
            logger.setLevel(previous)
        self.assertEqual(order[0], "log", "intent is logged before any member routes")
        self.assertEqual(len(marks), 1)
        self.assertEqual(marks[0].levelno, logging.INFO)
        text = marks[0].getMessage()
        for identity in (mac(1), mac(2), SRC, a):
            self.assertIn(identity, text)
        for outsider in (mac(3), mac(5), "password", "csrf"):
            self.assertNotIn(outsider, text)
        entry = next(e for e in self.store.audit_entries(20)
                     if e["event"] == "route_group")
        self.assertEqual(entry["detail"]["intended"], [mac(1), mac(2)])


class AmbiguousRecordTests(GroupLifecycleBase):
    """The last step before a write must not guess between two records."""

    def test_an_address_claimed_by_two_identities_routes_neither(self):
        a = self.make_group("Room A", [1, 2])
        self.grant(a)
        # A stale row for decoder 1 sits at the address decoder 6 holds.
        self.cache = [u for u in self.cache
                      if not (u["mac"] == mac(1) and u["ip"] == ip(1))]
        self.cache.append(decoder(1, address=ip(6)))
        body = self.route_group(a).get_json()
        self.assertEqual(self.targeted(), [ip(2)], "the unambiguous member only")
        self.assertEqual(body["results"][0]["result"], "ambiguous")
        self.assertNotIn(ip(6), self.targeted())

    def test_one_identity_recorded_at_two_addresses_is_refused(self):
        a = self.make_group("Room A", [1, 2])
        self.grant(a)
        self.cache.append(decoder(1, address=ip(201)))
        body = self.route_group(a).get_json()
        self.assertEqual(self.targeted(), [ip(2)])
        self.assertEqual(body["results"][0]["result"], "ambiguous")
        self.assertFalse(body["ok"])

    def test_an_ambiguous_source_routes_nothing(self):
        a = self.make_group("Room A", [1, 2])
        self.grant(a)
        self.cache.append(dict(next(u for u in self.cache if u["mac"] == SRC),
                               ip="198.51.100.250"))
        body = self.route_group(a).get_json()
        self.assertEqual(self.routes, [])
        self.assertEqual([r["result"] for r in body["results"]], ["ambiguous"] * 2)

    def test_a_single_display_route_refuses_the_same_ambiguity(self):
        teacher = self.store.find_by_username("teacher")
        self.store.set_permissions(teacher.id, Permissions(
            features=("av",), sources=[SRC], destinations=[mac(1)]))
        self.cache = [u for u in self.cache
                      if not (u["mac"] == mac(1) and u["ip"] == ip(1))]
        self.cache.append(decoder(1, address=ip(6)))
        self.routes.clear()
        reply = self.route(self.session_for(self.store.find_by_username("teacher")),
                           destination=mac(1))
        self.assertEqual(reply.status_code, 409)
        self.assertEqual(reply.get_json()["reason"], "ambiguous_device")
        self.assertEqual(self.routes, [])

    def test_stop_refuses_the_same_ambiguity(self):
        teacher = self.store.find_by_username("teacher")
        self.store.set_permissions(teacher.id, Permissions(
            features=("av",), sources=[SRC], destinations=[mac(1)]))
        self.cache = [u for u in self.cache
                      if not (u["mac"] == mac(1) and u["ip"] == ip(1))]
        self.cache.append(decoder(1, address=ip(6)))
        self.unroutes.clear()
        reply = self.post("/api/user/unroute", {"destination": mac(1)},
                          self.session_for(self.store.find_by_username("teacher")))
        self.assertEqual(reply.status_code, 409)
        self.assertEqual(self.unroutes, [])

    def test_an_unambiguous_cache_is_unchanged(self):
        a = self.make_group("Room A", [1, 2])
        self.grant(a)
        self.assert_only(self.route_group(a), [1, 2])


if __name__ == "__main__":
    unittest.main()
