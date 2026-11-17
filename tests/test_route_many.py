"""One source to several displays and groups, as one action.

The endpoint is `/api/user/route_many`. What these hold:

  * every display is routed once, however many ways it was named, and group
    membership is read when the request arrives;
  * authority is per resolved display -- its own grant if it was named alone, a
    requested group's grant if it was reached through the group -- and group
    membership never grants a display on its own;
  * displays are independent: one failing neither stops nor undoes the others, and
    the answer is a truthful "N of M" with names and no address or identity;
  * the displays are routed concurrently, each under its own per-display lock;
  * authorisation is asked again on the far side of the lock, so a permission
    withdrawn while the request waits stops the writes that have not happened yet.

Everything is synthetic: documentation-range addresses and MACs that share nothing
with any bench value. The route engine is stubbed and the hardware is fenced.
"""
from __future__ import annotations

import threading
import time
import unittest

import omni_access
import omni_groups
from omni_users import Permissions
from tests import test_display_group_integrity as base
from tests import test_user_routing as harness

SRC = harness.TEACHER_PC
mac = base.mac


class RouteManyBase(base.GroupLifecycleBase):
    def setUp(self):
        super().setUp()
        self.room_a = self.make_group("Room A", [1, 2])
        self.room_b = self.make_group("Room B", [3, 4])

    def grant_to_teacher(self, destinations=(), groups=()):
        teacher = self.store.find_by_username("teacher")
        self.store.set_permissions(teacher.id, Permissions(
            features=("av",), sources=[SRC], destinations=list(destinations),
            groups=list(groups)))
        return self.session_for(self.store.find_by_username("teacher"))

    def many(self, session, destinations=(), groups=(), **extra):
        self.routes.clear()
        body = {"source": SRC, "destinations": list(destinations),
                "groups": list(groups)}
        body.update(extra)
        return self.post("/api/user/route_many", body, session)

    def addresses(self, numbers):
        return sorted(self.address_of(mac(n)) for n in numbers)

    def reached(self):
        return sorted(r["decoder"] for r in self.routes)


class RouteManyResolutionTests(RouteManyBase):
    def test_displays_and_groups_together_route_each_display_once(self):
        session = self.grant_to_teacher(destinations=[mac(5), mac(1)], groups=[self.room_a])
        reply = self.many(session, [mac(5), mac(1)], [self.room_a])
        self.assertEqual(reply.status_code, 200, reply.get_data(as_text=True))
        body = reply.get_json()
        # 1 named twice (alone and through Room A), 2 through the group, 5 alone.
        self.assertEqual((body["routed"], body["total"]), (3, 3))
        self.assertEqual(self.reached(), self.addresses([1, 2, 5]))

    def test_two_groups_route_every_member_and_nobody_else(self):
        session = self.grant_to_teacher(groups=[self.room_a, self.room_b])
        reply = self.many(session, groups=[self.room_a, self.room_b])
        self.assertEqual((reply.get_json()["routed"], reply.get_json()["total"]), (4, 4))
        self.assertEqual(self.reached(), self.addresses([1, 2, 3, 4]))

    def test_membership_is_read_when_the_request_arrives(self):
        session = self.grant_to_teacher(groups=[self.room_a])
        self.assertEqual(self.many(session, groups=[self.room_a]).get_json()["total"], 2)
        self.admin_post("/api/admin/groups/%s" % self.room_a,
                        {"action": "set_members", "members": [mac(2), mac(5), mac(6)]})
        reply = self.many(session, groups=[self.room_a])
        self.assertEqual(reply.get_json()["total"], 3)
        self.assertEqual(self.reached(), self.addresses([2, 5, 6]))

    def test_a_display_reached_two_ways_is_one_route_not_two(self):
        session = self.grant_to_teacher(destinations=[mac(1)], groups=[self.room_a])
        self.many(session, [mac(1)], [self.room_a])
        self.assertEqual(self.reached().count(self.address_of(mac(1))), 1)

    def test_results_name_displays_and_carry_no_address_or_identity(self):
        session = self.grant_to_teacher(groups=[self.room_a])
        text = self.many(session, groups=[self.room_a]).get_data(as_text=True)
        for n in (1, 2):
            self.assertNotIn(self.address_of(mac(n)), text)
            self.assertNotIn(mac(n), text)
        self.assertNotIn("198.51.100.", text)


class RouteManyAuthorityTests(RouteManyBase):
    def test_a_display_named_without_its_own_grant_is_refused_whole(self):
        # The account holds Room A only. Display 5 is in no group it holds.
        session = self.grant_to_teacher(groups=[self.room_a])
        reply = self.many(session, [mac(5)], [self.room_a])
        self.assertEqual(reply.status_code, 403)
        self.assertEqual(self.routes, [], "a refused request must reach no device")

    def test_group_membership_does_not_grant_the_display_individually(self):
        # Display 1 is in Room A, which the account holds -- but it was named alone.
        session = self.grant_to_teacher(groups=[self.room_a])
        reply = self.many(session, [mac(1)])
        self.assertEqual(reply.status_code, 403,
                         "holding the group must not imply the display on its own")
        self.assertEqual(self.routes, [])

    def test_a_group_the_account_does_not_hold_is_refused_whole(self):
        session = self.grant_to_teacher(groups=[self.room_a])
        reply = self.many(session, groups=[self.room_a, self.room_b])
        self.assertEqual(reply.status_code, 403)
        self.assertEqual(self.routes, [])

    def test_a_source_the_account_may_not_use_is_refused(self):
        session = self.grant_to_teacher(groups=[self.room_a])
        reply = self.post("/api/user/route_many", {
            "source": harness.UNSEEN_SOURCE, "groups": [self.room_a]}, session)
        self.assertEqual(reply.status_code, 403)
        self.assertEqual(self.routes, [])

    def test_the_far_side_of_the_lock_asks_again(self):
        """A permission withdrawn while a request waits stops the writes still to come."""
        session = self.grant_to_teacher(groups=[self.room_a])
        teacher = self.store.find_by_username("teacher")
        started = threading.Event()
        release = threading.Event()
        inner = self.routes

        def gated_route():
            from flask import request as flask_request, jsonify
            body = flask_request.get_json(silent=True) or {}
            started.set()
            release.wait(5)
            inner.append(dict(body))
            return jsonify({"ok": True, "status": "VERIFIED"}), 200

        self._patch_registered("route_view", gated_route)
        result = {}

        def run():
            result["reply"] = self.many(session, groups=[self.room_a])

        worker = threading.Thread(target=run)
        worker.start()
        self.assertTrue(started.wait(5), "the first write never started")
        # Withdraw the group while the request is in flight.
        self.store.set_permissions(teacher.id, Permissions(
            features=("av",), sources=[SRC], destinations=[], groups=[]))
        release.set()
        worker.join(10)
        reply = result["reply"]
        self.assertEqual(reply.status_code, 200, reply.get_data(as_text=True))
        body = reply.get_json()
        # Both members were queued behind the gate together; each asks again after its
        # lock, and the withdrawal reached the one that had not yet written.
        self.assertEqual(body["total"], 2)
        refused = [r for r in body["results"] if r["result"] == "refused"]
        self.assertTrue(refused, "a withdrawn grant must stop at least the writes not yet made")
        self.assertLess(body["routed"], body["total"])


class RouteManyOutcomeTests(RouteManyBase):
    def test_a_failed_display_does_not_stop_or_undo_the_others(self):
        session = self.grant_to_teacher(groups=[self.room_a, self.room_b])
        failing = {self.address_of(mac(2))}

        def route():
            from flask import request as flask_request, jsonify
            body = flask_request.get_json(silent=True) or {}
            self.routes.append(dict(body))
            if body.get("decoder") in failing:
                return jsonify({"ok": False, "status": "FAILED"}), 200
            return jsonify({"ok": True, "status": "VERIFIED"}), 200

        self._patch_registered("route_view", route)
        reply = self.many(session, groups=[self.room_a, self.room_b])
        body = reply.get_json()
        self.assertEqual(reply.status_code, 200)
        self.assertEqual((body["routed"], body["total"]), (3, 4))
        self.assertFalse(body["ok"])
        self.assertEqual(self.reached(), self.addresses([1, 2, 3, 4]),
                         "every independent display is attempted")
        self.assertEqual([r["ok"] for r in body["results"]].count(False), 1)

    def test_a_busy_display_is_reported_and_the_rest_still_route(self):
        session = self.grant_to_teacher(groups=[self.room_a])
        busy_identity = mac(1)
        real = self.srv.omni_locks if hasattr(self.srv, "omni_locks") else None

        class Locks:
            @staticmethod
            def decoder(identity):
                return "decoder:" + identity

            @staticmethod
            def hold(*resources, timeout=None):
                import omni_locks
                if resources == ("decoder:" + busy_identity,):
                    raise omni_locks.ResourceBusy(resources[0])
                import contextlib
                return contextlib.nullcontext()

        self._patch_registered("locks", Locks())
        body = self.many(session, groups=[self.room_a]).get_json()
        self.assertEqual((body["routed"], body["total"]), (1, 2))
        self.assertEqual(sorted(r["result"] for r in body["results"])[0], "busy")
        self.assertEqual(self.reached(), self.addresses([2]))
        del real

    def test_the_displays_are_routed_concurrently(self):
        session = self.grant_to_teacher(groups=[self.room_a, self.room_b])
        delay = 0.4

        def slow():
            from flask import request as flask_request, jsonify
            body = flask_request.get_json(silent=True) or {}
            time.sleep(delay)
            self.routes.append(dict(body))
            return jsonify({"ok": True, "status": "VERIFIED"}), 200

        self._patch_registered("route_view", slow)
        began = time.perf_counter()
        body = self.many(session, groups=[self.room_a, self.room_b]).get_json()
        elapsed = time.perf_counter() - began
        self.assertEqual(body["routed"], 4)
        # Four 0.4s routes one after another would be 1.6s.
        self.assertLess(elapsed, delay * 2.5, "routed one at a time: %.2fs" % elapsed)

    def test_results_keep_the_order_the_displays_were_named_in(self):
        session = self.grant_to_teacher(destinations=[mac(6), mac(5)])
        body = self.many(session, [mac(6), mac(5)]).get_json()
        self.assertEqual([r["index"] for r in body["results"]], [0, 1])

    def test_the_activity_log_records_one_entry_for_the_action(self):
        session = self.grant_to_teacher(groups=[self.room_a])
        self.many(session, groups=[self.room_a])
        events = [e["event"] for e in self.store.recent_audit()] \
            if hasattr(self.store, "recent_audit") else \
            [e["event"] for e in self.store._audit]
        self.assertEqual(events.count("route_many"), 1)
        self.assertEqual(events.count("route"), 0)


class RouteManyRequestTests(RouteManyBase):
    def test_malformed_requests_are_400_and_reach_no_device(self):
        session = self.grant_to_teacher(groups=[self.room_a])
        for body in ({"source": SRC},
                     {"source": SRC, "destinations": "nope"},
                     {"source": SRC, "groups": [self.room_a, ""]},
                     {"source": SRC, "destinations": [mac(1)] * 70},
                     {"destinations": [mac(1)]}):
            reply = self.post("/api/user/route_many", body, session)
            self.assertEqual(reply.status_code, 400, str(body)[:60])
        self.assertEqual(self.routes, [])

    def test_an_unknown_group_is_404(self):
        session = self.grant_to_teacher(groups=["grp_missing"])
        reply = self.many(session, groups=["grp_missing"])
        self.assertEqual(reply.status_code, 404)

    def test_audio_only_is_a_matrix_operation(self):
        session = self.grant_to_teacher(groups=[self.room_a])
        reply = self.many(session, groups=[self.room_a], mode="audio")
        self.assertEqual(reply.status_code, 400)

    def test_video_only_is_what_audio_follows_video_off_sends(self):
        # The Standard page's "Audio follows video" switch: off sends a video-only route. It is
        # narrower than A/V and needs exactly the same grants.
        session = self.grant_to_teacher(groups=[self.room_a])
        reply = self.many(session, groups=[self.room_a], mode="video")
        self.assertEqual(reply.status_code, 200, reply.get_data(as_text=True))
        self.assertTrue(all(r.get("mode") == "video" for r in self.routes), self.routes)
        ungranted = self.many(session, [mac(5)], mode="video")
        self.assertEqual(ungranted.status_code, 403, "video-only widens no grant")

    def test_the_route_is_classified_in_the_gate_table(self):
        rule = omni_access.ROUTES["/api/user/route_many"]
        self.assertEqual(rule, (omni_access.Access.USER, omni_access.FEATURE_AV))

    def test_the_concurrent_runner_keeps_each_failure_its_own(self):
        def one(index, item):
            if item == "boom":
                raise RuntimeError("x")
            return {"ok": item != "no"}

        summary = omni_groups.run_concurrent(["yes", "boom", "no", "yes"], one, workers=3)
        self.assertEqual((summary["routed"], summary["total"]), (2, 4))
        self.assertEqual([r["index"] for r in summary["results"]], [0, 1, 2, 3])


if __name__ == "__main__":
    unittest.main()
