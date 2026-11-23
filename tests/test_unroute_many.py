"""Stop several displays and groups as one action.

The endpoint is `/api/user/unroute_many`, the Unroute half of the one-or-many selection Route
uses. What these hold:

  * every display is stopped once, however many ways it was named, and group membership is read
    when the request arrives -- never from what the page last knew;
  * no source is involved: stopping a display needs only the display;
  * authority is per resolved display (its own grant if named alone, a requested group's grant if
    reached through the group), and a display nobody asked for is never touched;
  * displays are independent: one failing neither stops nor undoes the others, and the answer is a
    truthful "N of M" with names and no address or identity;
  * authorisation is asked again on the far side of the lock.

Everything is synthetic; the unroute engine is stubbed and the hardware is fenced.
"""
from __future__ import annotations

import threading
import time
import unittest

import omni_access
from omni_users import Permissions
from tests import test_display_group_integrity as base
from tests import test_user_routing as harness

mac = base.mac


class UnrouteManyBase(base.GroupLifecycleBase):
    def setUp(self):
        super().setUp()
        self.room_a = self.make_group("Room A", [1, 2])
        self.room_b = self.make_group("Room B", [3, 4])

    def grant_to_teacher(self, destinations=(), groups=()):
        teacher = self.store.find_by_username("teacher")
        self.store.set_permissions(teacher.id, Permissions(
            features=("av",), sources=[harness.TEACHER_PC], destinations=list(destinations),
            groups=list(groups)))
        return self.session_for(self.store.find_by_username("teacher"))

    def stop(self, session, destinations=(), groups=(), **extra):
        self.unroutes.clear()
        body = {"destinations": list(destinations), "groups": list(groups)}
        body.update(extra)
        return self.post("/api/user/unroute_many", body, session)

    def addresses(self, numbers):
        return sorted(self.address_of(mac(n)) for n in numbers)

    def reached(self):
        return sorted(u["decoder"] for u in self.unroutes)


class UnrouteManyResolutionTests(UnrouteManyBase):
    def test_displays_and_groups_together_stop_each_display_once(self):
        session = self.grant_to_teacher(destinations=[mac(5), mac(1)], groups=[self.room_a])
        reply = self.stop(session, [mac(5), mac(1)], [self.room_a])
        self.assertEqual(reply.status_code, 200, reply.get_data(as_text=True))
        body = reply.get_json()
        # 1 named twice (alone and through Room A), 2 through the group, 5 alone.
        self.assertEqual((body["routed"], body["total"]), (3, 3))
        self.assertEqual(self.reached(), self.addresses([1, 2, 5]))

    def test_a_display_nobody_asked_for_is_never_touched(self):
        session = self.grant_to_teacher(groups=[self.room_a, self.room_b],
                                        destinations=[mac(5)])
        self.stop(session, groups=[self.room_a])
        self.assertEqual(self.reached(), self.addresses([1, 2]))
        for n in (3, 4, 5, 6):
            self.assertNotIn(self.address_of(mac(n)), self.reached())

    def test_membership_is_read_when_the_request_arrives(self):
        session = self.grant_to_teacher(groups=[self.room_a])
        self.assertEqual(self.stop(session, groups=[self.room_a]).get_json()["total"], 2)
        self.admin_post("/api/admin/groups/%s" % self.room_a,
                        {"action": "set_members", "members": [mac(2), mac(5), mac(6)]})
        reply = self.stop(session, groups=[self.room_a])
        self.assertEqual(reply.get_json()["total"], 3)
        self.assertEqual(self.reached(), self.addresses([2, 5, 6]))

    def test_it_needs_no_source(self):
        session = self.grant_to_teacher(destinations=[mac(1)])
        reply = self.stop(session, [mac(1)])
        self.assertEqual(reply.status_code, 200)
        self.assertEqual(self.reached(), self.addresses([1]))

    def test_results_name_displays_and_carry_no_address_or_identity(self):
        session = self.grant_to_teacher(groups=[self.room_a])
        text = self.stop(session, groups=[self.room_a]).get_data(as_text=True)
        for n in (1, 2):
            self.assertNotIn(self.address_of(mac(n)), text)
            self.assertNotIn(mac(n), text)


class UnrouteManyAuthorityTests(UnrouteManyBase):
    def test_a_display_named_without_its_own_grant_is_refused_whole(self):
        session = self.grant_to_teacher(groups=[self.room_a])
        reply = self.stop(session, [mac(5)], [self.room_a])
        self.assertEqual(reply.status_code, 403)
        self.assertEqual(self.unroutes, [], "a refused request must reach no device")

    def test_group_membership_does_not_grant_the_display_individually(self):
        session = self.grant_to_teacher(groups=[self.room_a])
        reply = self.stop(session, [mac(1)])
        self.assertEqual(reply.status_code, 403)
        self.assertEqual(self.unroutes, [])

    def test_a_group_the_account_does_not_hold_is_refused_whole(self):
        session = self.grant_to_teacher(groups=[self.room_a])
        reply = self.stop(session, groups=[self.room_a, self.room_b])
        self.assertEqual(reply.status_code, 403)
        self.assertEqual(self.unroutes, [])

    def test_an_account_without_the_av_feature_is_refused(self):
        teacher = self.store.find_by_username("teacher")
        self.store.set_permissions(teacher.id, Permissions(
            features=(), sources=[], destinations=[mac(1)], groups=[]))
        session = self.session_for(self.store.find_by_username("teacher"))
        self.assertEqual(self.stop(session, [mac(1)]).status_code, 403)
        self.assertEqual(self.unroutes, [])

    def test_the_far_side_of_the_lock_asks_again(self):
        """A permission withdrawn while a request waits stops the writes still to come."""
        session = self.grant_to_teacher(groups=[self.room_a])
        teacher = self.store.find_by_username("teacher")
        started = threading.Event()
        release = threading.Event()
        seen = self.unroutes

        def gated_unroute():
            from flask import request as flask_request, jsonify
            body = flask_request.get_json(silent=True) or {}
            started.set()
            release.wait(5)
            seen.append(dict(body))
            return jsonify({"ok": True, "status": "UNROUTED"}), 200

        self._patch_registered("unroute_view", gated_unroute)
        result = {}
        worker = threading.Thread(
            target=lambda: result.update(reply=self.stop(session, groups=[self.room_a])))
        worker.start()
        self.assertTrue(started.wait(5), "the first write never started")
        self.store.set_permissions(teacher.id, Permissions(
            features=("av",), sources=[harness.TEACHER_PC], destinations=[], groups=[]))
        release.set()
        worker.join(10)
        body = result["reply"].get_json()
        self.assertEqual(body["total"], 2)
        self.assertTrue([r for r in body["results"] if r["result"] == "refused"],
                        "a withdrawn grant must stop at least the writes not yet made")
        self.assertLess(body["routed"], body["total"])


class UnrouteManyFarSideTests(UnrouteManyBase):
    def test_a_grant_withdrawn_while_waiting_for_the_lock_stops_the_write(self):
        """Authorisation is asked again AFTER the wait for the per-display lock, not only before."""
        session = self.grant_to_teacher(groups=[self.room_a])
        teacher = self.store.find_by_username("teacher")
        waiting = threading.Event()
        release = threading.Event()

        class Locks:
            @staticmethod
            def decoder(identity):
                return "decoder:" + identity

            @staticmethod
            def hold(*resources, timeout=None):
                import contextlib
                waiting.set()
                release.wait(5)
                return contextlib.nullcontext()

        self._patch_registered("locks", Locks())
        result = {}
        worker = threading.Thread(
            target=lambda: result.update(reply=self.stop(session, groups=[self.room_a])))
        worker.start()
        self.assertTrue(waiting.wait(5), "no display reached its lock")
        self.store.set_permissions(teacher.id, Permissions(
            features=("av",), sources=[harness.TEACHER_PC], destinations=[], groups=[]))
        release.set()
        worker.join(10)
        body = result["reply"].get_json()
        self.assertEqual(self.unroutes, [], "a write was made after the permission was withdrawn")
        self.assertEqual(body["routed"], 0)
        self.assertTrue(all(r["result"] == "refused" for r in body["results"]), body)


class UnrouteManyOutcomeTests(UnrouteManyBase):
    def test_a_failed_display_does_not_stop_or_undo_the_others(self):
        session = self.grant_to_teacher(groups=[self.room_a, self.room_b])
        failing = {self.address_of(mac(2))}

        def unroute():
            from flask import request as flask_request, jsonify
            body = flask_request.get_json(silent=True) or {}
            self.unroutes.append(dict(body))
            if body.get("decoder") in failing:
                return jsonify({"ok": False, "status": "FAILED"}), 200
            return jsonify({"ok": True, "status": "UNROUTED"}), 200

        self._patch_registered("unroute_view", unroute)
        body = self.stop(session, groups=[self.room_a, self.room_b]).get_json()
        self.assertEqual((body["routed"], body["total"]), (3, 4))
        self.assertFalse(body["ok"], "a partial result is not a success")
        self.assertEqual(self.reached(), self.addresses([1, 2, 3, 4]),
                         "every independent display is attempted")
        self.assertEqual([r["ok"] for r in body["results"]].count(False), 1)

    def test_a_busy_display_is_reported_and_the_rest_still_stop(self):
        session = self.grant_to_teacher(groups=[self.room_a])
        busy_identity = mac(1)

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
        body = self.stop(session, groups=[self.room_a]).get_json()
        self.assertEqual((body["routed"], body["total"]), (1, 2))
        self.assertIn("busy", [r["result"] for r in body["results"]])
        self.assertEqual(self.reached(), self.addresses([2]))

    def test_the_displays_are_stopped_concurrently(self):
        session = self.grant_to_teacher(groups=[self.room_a, self.room_b])
        delay = 0.4

        def slow():
            from flask import request as flask_request, jsonify
            body = flask_request.get_json(silent=True) or {}
            time.sleep(delay)
            self.unroutes.append(dict(body))
            return jsonify({"ok": True, "status": "UNROUTED"}), 200

        self._patch_registered("unroute_view", slow)
        began = time.perf_counter()
        body = self.stop(session, groups=[self.room_a, self.room_b]).get_json()
        elapsed = time.perf_counter() - began
        self.assertEqual(body["routed"], 4)
        self.assertLess(elapsed, delay * 2.5, "stopped one at a time: %.2fs" % elapsed)

    def test_the_activity_log_records_one_entry_for_the_action(self):
        session = self.grant_to_teacher(groups=[self.room_a])
        self.stop(session, groups=[self.room_a])
        events = [e["event"] for e in self.store.recent_audit()] \
            if hasattr(self.store, "recent_audit") else \
            [e["event"] for e in self.store._audit]
        self.assertEqual(events.count("unroute_many"), 1)


class UnrouteManyRequestTests(UnrouteManyBase):
    def test_malformed_requests_are_400_and_reach_no_device(self):
        session = self.grant_to_teacher(groups=[self.room_a])
        for body in ({},
                     {"destinations": "nope"},
                     {"groups": [self.room_a, ""]},
                     {"destinations": [mac(1)] * 70}):
            reply = self.post("/api/user/unroute_many", body, session)
            self.assertEqual(reply.status_code, 400, str(body)[:60])
        self.assertEqual(self.unroutes, [])

    def test_an_unknown_group_is_404(self):
        session = self.grant_to_teacher(groups=["grp_missing"])
        self.assertEqual(self.stop(session, groups=["grp_missing"]).status_code, 404)

    def test_the_route_is_classified_in_the_gate_table(self):
        rule = omni_access.ROUTES["/api/user/unroute_many"]
        self.assertEqual(rule, (omni_access.Access.USER, omni_access.FEATURE_AV))

    def test_an_unauthenticated_request_is_refused(self):
        self.client.delete_cookie(harness.omni_gate.SESSION_COOKIE) if hasattr(
            self.client, "delete_cookie") else None
        reply = self.client.post(
            "/api/user/unroute_many", json={"destinations": [mac(1)]},
            environ_base={harness.omni_remote.LISTENER_KEY: harness.omni_remote.REMOTE})
        self.assertIn(reply.status_code, (401, 403))
        self.assertEqual(self.unroutes, [])


if __name__ == "__main__":
    unittest.main()
