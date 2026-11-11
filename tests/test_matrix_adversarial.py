"""The restricted User Matrix, attacked from the places a plausible build gives way.

tests/test_user_matrix.py holds the contract. This holds what happens when the
world moves under a request, when the caller lies, and when many callers and an
administrator act at once. The engine is stubbed through the shared routing
harness; no device is contacted.

What each class is for:

  * MovesUnderARoute      -- permission withdrawn while a group is being routed.
                             The group route re-asks per member, so the members
                             after the withdrawal must not be written.
  * LiesFromTheBrowser    -- ids, modes and interfaces that no page would send.
                             Refused, never a 500, never an engine call.
  * AdministrativeSurface -- a restricted session cannot reach the A/V Matrix
                             the administrator uses.
  * ManyAtOnce            -- routes, state reads and permission changes together.
                             Only declared statuses; and after the last
                             withdrawal nothing more reaches the engine.
"""
from __future__ import annotations

import copy
import os
import sys
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("OMNI_SMOKE", "1")

import omni_gate                                        # noqa: E402
import omni_groups                                      # noqa: E402
import omni_remote                                      # noqa: E402
import omni_users                                       # noqa: E402
from omni_users import Permissions                      # noqa: E402
from tests import test_user_matrix as matrix            # noqa: E402
from tests import test_user_routing as harness          # noqa: E402

SRC_A, SRC_B = matrix.SRC_A, matrix.SRC_B
SOLO = matrix.SOLO
ROOMS = (matrix.ROOM_1, matrix.ROOM_2, matrix.ROOM_3)


class MatrixBase(harness.UserRoutingTestBase):
    def setUp(self):
        super().setUp()
        self._patch_registered("load_cache",
                               lambda: copy.deepcopy(matrix.UNITS))
        self.groups = omni_groups.GroupStore(
            self.dir / "display_groups.json", matrix.canonical)
        self._patch_registered("groups", self.groups)
        self.group = self.groups.create("Front rooms", list(ROOMS))["group"]
        self.state_now = matrix.build_state(
            [("192.0.2.21", {}), ("192.0.2.41", {}), ("192.0.2.42", {}),
             ("192.0.2.43", {})])
        self._patch_registered("matrix_state",
                               lambda: copy.deepcopy(self.state_now))

    # -- helpers -------------------------------------------------------
    def make(self, name="mx", interfaces=("matrix",), **extra):
        grant = dict(features=("av",), sources=[SRC_A, SRC_B],
                     destinations=[SOLO], groups=[self.group["id"]],
                     interfaces=list(interfaces))
        grant.update(extra)
        self.store.create(name, name + "-password-1", omni_users.ROLE_USER,
                          Permissions(**grant))
        return self.store.find_by_username(name)

    def regrant(self, account, **change):
        held = self.store.find_by_username(account.username).permissions
        grant = dict(features=held.features, sources=held.sources,
                     destinations=held.destinations, groups=held.groups,
                     interfaces=held.interfaces)
        grant.update(change)
        result = self.store.set_permissions(account.id, Permissions(**grant))
        self.assertTrue(result["ok"], result)

    def route_body(self, **over):
        body = {"source": SRC_A, "destination": SOLO, "mode": "av",
                "interface": "matrix"}
        body.update(over)
        return body

    def group_body(self, **over):
        body = {"source": SRC_A, "group": self.group["id"], "mode": "av",
                "interface": "matrix"}
        body.update(over)
        return body

    def engine_on_call(self, hook):
        """Replace the stubbed engine with one that runs `hook(n)` per call."""
        from flask import jsonify, request as flask_request
        calls = self.routes

        def engine():
            body = flask_request.get_json(silent=True) or {}
            calls.append(dict(body))
            hook(len(calls))
            return jsonify({"ok": True, "status": "VERIFIED"}), 200

        self._patch_registered("route_view", engine)


class MovesUnderARoute(MatrixBase):
    """The group route is sequential and asks again before every member."""

    def _run(self, withdraw):
        account = self.make()
        session = self.session_for(account)
        self.engine_on_call(lambda n: withdraw(account) if n == 1 else None)
        reply = self.post("/api/user/route_group", self.group_body(), session)
        return reply, reply.get_json() or {}

    def _assert_stopped_after_first(self, reply, body):
        self.assertLess(reply.status_code, 500, body)
        self.assertEqual(len(self.routes), 1,
                         "a member after the withdrawal reached the engine")
        self.assertEqual(body.get("total"), 3)
        self.assertEqual(body.get("routed"), 1)
        self.assertFalse(body.get("ok"), "a half-done group reported success")
        results = {r["index"]: r["result"] for r in body["results"]}
        self.assertEqual([results[i] for i in sorted(results)][1:],
                         ["refused", "refused"])

    def test_the_matrix_taken_away_mid_run_stops_the_remaining_members(self):
        reply, body = self._run(
            lambda a: self.regrant(a, interfaces=["standard"]))
        self._assert_stopped_after_first(reply, body)

    def test_the_source_taken_away_mid_run_stops_the_remaining_members(self):
        reply, body = self._run(lambda a: self.regrant(a, sources=[SRC_B]))
        self._assert_stopped_after_first(reply, body)

    def test_the_group_taken_away_mid_run_stops_the_remaining_members(self):
        reply, body = self._run(lambda a: self.regrant(a, groups=[]))
        self._assert_stopped_after_first(reply, body)

    def test_the_av_feature_withdrawn_mid_run_stops_the_remaining_members(self):
        reply, body = self._run(lambda a: self.regrant(a, features=("usb",)))
        self._assert_stopped_after_first(reply, body)

    def test_an_account_disabled_mid_run_stops_the_remaining_members(self):
        reply, body = self._run(
            lambda a: self.store.set_enabled(a.id, False))
        self.assertEqual(len(self.routes), 1)
        self.assertNotEqual(body.get("ok"), True)
        self.assertLess(reply.status_code, 500)

    def test_an_account_deleted_mid_run_stops_the_remaining_members(self):
        reply, body = self._run(lambda a: self.store.delete(a.id))
        self.assertEqual(len(self.routes), 1)
        self.assertNotEqual(body.get("ok"), True)
        self.assertLess(reply.status_code, 500)

    def test_control_nothing_withdrawn_routes_every_member(self):
        reply, body = self._run(lambda a: None)
        self.assertEqual(reply.status_code, 200)
        self.assertEqual(len(self.routes), 3)
        self.assertTrue(body["ok"])
        self.assertEqual((body["routed"], body["total"]), (3, 3))

    def test_a_group_edited_between_render_and_click_routes_the_current_members(self):
        # The page drew three members; one is gone by the time of the click.
        account = self.make()
        self.groups.update(self.group["id"], members=list(ROOMS[:2]))
        reply = self.post("/api/user/route_group", self.group_body(),
                          self.session_for(account))
        self.assertEqual(reply.status_code, 200)
        self.assertEqual(reply.get_json()["total"], 2)
        self.assertEqual(len(self.routes), 2)

    def test_a_group_deleted_between_render_and_click_is_not_a_crash(self):
        account = self.make()
        session = self.session_for(account)
        self.groups.delete(self.group["id"])
        reply = self.post("/api/user/route_group", self.group_body(), session)
        self.assertIn(reply.status_code, (403, 404))
        self.assertEqual(self.routes, [])

    def test_a_group_deleted_leaves_the_matrix_without_its_row(self):
        account = self.make()
        session = self.session_for(account)
        self.groups.delete(self.group["id"])
        reply = self.get("/api/user/matrix_state", session)
        self.assertEqual(reply.status_code, 200)
        kinds = {(r["kind"], r["id"]) for r in reply.get_json()["rows"]}
        self.assertNotIn(("group", self.group["id"]), kinds)


class LiesFromTheBrowser(MatrixBase):
    HOSTILE_IDS = [
        "", " ", "\x00", "a" * 10000, "../../etc/passwd", "' OR 1=1 --",
        "<script>alert(1)</script>", "b8:98:b0:00:00:4", "B8-98-B0-00-00-41",
        "192.0.2.41", "0.0.0.0", "‮" + SOLO, "null", "undefined",
        "constructor", "__proto__", "%00", "b8:98:b0:00:00:41\n",
        None, 0, 1, True, False, [], {}, [SOLO], {"id": SOLO}, 3.14,
    ]

    def setUp(self):
        super().setUp()
        self.account = self.make("liar", ("matrix", "standard"))
        self.session = self.session_for(self.account)

    def _no_engine_and_no_crash(self, reply, what):
        self.assertLess(reply.status_code, 500, "%s -> %s %r" % (
            what, reply.status_code, reply.get_data()[:200]))
        return reply

    def test_hostile_destinations_never_reach_the_engine(self):
        for value in self.HOSTILE_IDS:
            with self.subTest(destination=repr(value)[:40]):
                reply = self.post("/api/user/route",
                                  self.route_body(destination=value),
                                  self.session)
                self._no_engine_and_no_crash(reply, "destination")
        self.assertEqual(self.routes, [])

    def test_hostile_sources_never_reach_the_engine(self):
        for value in self.HOSTILE_IDS:
            with self.subTest(source=repr(value)[:40]):
                reply = self.post("/api/user/route",
                                  self.route_body(source=value), self.session)
                self._no_engine_and_no_crash(reply, "source")
        self.assertEqual(self.routes, [])

    def test_hostile_groups_never_reach_the_engine(self):
        for value in self.HOSTILE_IDS:
            with self.subTest(group=repr(value)[:40]):
                reply = self.post("/api/user/route_group",
                                  self.group_body(group=value), self.session)
                self._no_engine_and_no_crash(reply, "group")
        self.assertEqual(self.routes, [])

    def test_a_mode_that_is_not_one_of_three_never_reaches_the_engine(self):
        # The server trims and lower-cases what it is given and reads "not
        # stated" as AV, so those reach the engine -- but only ever as one of
        # the three canonical words. Everything else is refused first.
        refused = ["video;drop", "video\x00", "both", "audio-video", "vid eo",
                   1, True, 3.5, "аudio"]
        normalised = {"": "av", None: "av", "AV": "av", " av": "av",
                      "Video ": "video", "AUDIO": "audio"}
        for value in refused:
            with self.subTest(refused=repr(value)):
                before = len(self.routes)
                reply = self.post("/api/user/route",
                                  self.route_body(mode=value), self.session)
                self._no_engine_and_no_crash(reply, "mode")
                self.assertEqual(reply.status_code, 400)
                self.assertEqual(len(self.routes), before)
        for value, canonical in normalised.items():
            with self.subTest(normalised=repr(value)):
                before = len(self.routes)
                reply = self.post("/api/user/route",
                                  self.route_body(mode=value), self.session)
                self.assertEqual(reply.status_code, 200)
                self.assertEqual(len(self.routes), before + 1)
        for body in self.routes:
            self.assertIn(body.get("mode"), ("av", "video", "audio"))

    def test_an_interface_that_is_not_one_of_two_is_refused_before_the_engine(self):
        refused = ["admin", "both", "standard,matrix", "mat rix", 1, True,
                   "matrix\x00", "нatrix"]
        for value in refused:
            with self.subTest(refused=repr(value)):
                before = len(self.routes)
                reply = self.post("/api/user/route",
                                  self.route_body(interface=value),
                                  self.session)
                self._no_engine_and_no_crash(reply, "interface")
                self.assertEqual(reply.status_code, 400)
                self.assertEqual(len(self.routes), before)

    def test_an_unstated_interface_is_the_standard_one_not_an_upgrade(self):
        # A matrix-only account that says nothing is treated as asking for the
        # Standard interface, which it does not hold.
        matrix_only = self.make("mxonly", ("matrix",))
        for value in (None, "", [], {}):
            with self.subTest(interface=repr(value)):
                reply = self.post(
                    "/api/user/route", self.route_body(interface=value),
                    self.session_for(matrix_only))
                self.assertEqual(reply.status_code, 403)
        self.assertEqual(self.routes, [])

    def test_a_valid_mode_from_the_wrong_interface_is_refused(self):
        standard = self.make("std", ("standard",))
        for mode in ("video", "audio"):
            with self.subTest(mode=mode):
                reply = self.post(
                    "/api/user/route",
                    self.route_body(mode=mode, interface="standard"),
                    self.session_for(standard))
                self.assertIn(reply.status_code, (400, 403))
        self.assertEqual(self.routes, [])

    def test_an_id_with_the_right_shape_but_not_granted_is_refused(self):
        reply = self.post(
            "/api/user/route",
            self.route_body(destination=harness.UNSEEN_DISPLAY), self.session)
        self.assertEqual(reply.status_code, 403)
        reply = self.post(
            "/api/user/route",
            self.route_body(source=harness.UNSEEN_SOURCE), self.session)
        self.assertEqual(reply.status_code, 403)
        self.assertEqual(self.routes, [])

    def test_a_display_id_is_not_a_group_and_a_group_id_is_not_a_display(self):
        reply = self.post("/api/user/route_group",
                          self.group_body(group=SOLO), self.session)
        self.assertIn(reply.status_code, (403, 404))
        reply = self.post("/api/user/route",
                          self.route_body(destination=self.group["id"]),
                          self.session)
        self.assertIn(reply.status_code, (403, 404))
        self.assertEqual(self.routes, [])

    def test_a_member_is_not_routable_by_id_unless_granted_individually(self):
        reply = self.post("/api/user/route",
                          self.route_body(destination=ROOMS[0]), self.session)
        self.assertEqual(reply.status_code, 403)
        self.assertEqual(self.routes, [])

    def test_the_matrix_state_never_names_an_address_or_identity_it_was_not_given(self):
        reply = self.get("/api/user/matrix_state", self.session)
        text = reply.get_data(as_text=True)
        for unit in matrix.UNITS:
            if unit["mac"] in (SRC_A, SRC_B, SOLO):
                continue
            self.assertNotIn(unit["mac"].lower(), text.lower())
        for unit in matrix.UNITS:
            self.assertNotIn(unit["ip"], text)

    def test_a_body_that_is_not_an_object_is_refused_everywhere(self):
        for path in ("/api/user/route", "/api/user/route_group"):
            for body in ([], "text", 5, None, [SOLO]):
                with self.subTest(path=path, body=repr(body)):
                    self.client.set_cookie(omni_gate.SESSION_COOKIE,
                                           self.session.token)
                    reply = self.client.post(
                        path, json=body,
                        environ_base={omni_remote.LISTENER_KEY:
                                      omni_remote.REMOTE},
                        headers={"X-OmniSuite-CSRF": self.session.csrf_token})
                    self._no_engine_and_no_crash(reply, path)
        self.assertEqual(self.routes, [])


class AdministrativeSurface(MatrixBase):
    """A restricted session is not a back door into the administrator's grid."""

    ADMIN_ROUTES = [
        ("POST", "/api/route", {"encoder": "192.0.2.11", "decoder": "192.0.2.21",
                                "mode": "av"}),
        ("GET", "/api/state", None),
        ("POST", "/api/unroute", {"decoder": "192.0.2.21"}),
        ("POST", "/api/clear", {}),
        ("POST", "/api/clear_units", {}),
        ("POST", "/api/remove_units", {"macs": [SOLO]}),
        ("GET", "/api/admin/users", None),
        ("GET", "/api/admin/groups", None),
        ("POST", "/api/remote_access", {"enabled": False}),
    ]

    def test_a_restricted_session_cannot_use_the_administrators_surface(self):
        session = self.session_for(self.make("both", ("matrix", "standard")))
        for method, path, body in self.ADMIN_ROUTES:
            with self.subTest(route="%s %s" % (method, path)):
                if method == "GET":
                    reply = self.get(path, session)
                else:
                    reply = self.post(path, body, session)
                self.assertIn(reply.status_code, (401, 403, 404),
                              "%s %s answered %s to a restricted session" % (
                                  method, path, reply.status_code))
        self.assertEqual(self.routes, [])

    def test_a_direct_access_session_is_no_more_privileged(self):
        account = self.make("tablet", ("matrix",))
        session = self.session_for(account, omni_users.BY_DIRECT_LINK)
        for method, path, body in self.ADMIN_ROUTES:
            with self.subTest(route="%s %s" % (method, path)):
                reply = (self.get(path, session) if method == "GET"
                         else self.post(path, body, session))
                self.assertIn(reply.status_code, (401, 403, 404))
        self.assertEqual(self.routes, [])


class ManyAtOnce(MatrixBase):
    """Routes, reads and withdrawals together. Only declared statuses."""

    ALLOWED_ROUTE = {200, 401, 403, 404, 409, 503}
    ALLOWED_READ = {200, 401, 403}

    def _client(self, session):
        client = self.srv.app.test_client()
        client.set_cookie(omni_gate.SESSION_COOKIE, session.token)
        return client

    def _post(self, client, session, path, body):
        return client.post(
            path, json=body,
            environ_base={omni_remote.LISTENER_KEY: omni_remote.REMOTE},
            headers={"X-OmniSuite-CSRF": session.csrf_token})

    def _get(self, client, path):
        return client.get(
            path, environ_base={omni_remote.LISTENER_KEY: omni_remote.REMOTE})

    def test_routes_reads_and_withdrawals_together_produce_no_server_error(self):
        account = self.make("busy", ("matrix", "standard"))
        session = self.session_for(account)
        stop = threading.Event()
        statuses = {"route": [], "group": [], "read": []}
        failures = []

        def routes():
            client = self._client(session)
            while not stop.is_set():
                try:
                    statuses["route"].append(self._post(
                        client, session, "/api/user/route",
                        self.route_body(mode="video")).status_code)
                    statuses["group"].append(self._post(
                        client, session, "/api/user/route_group",
                        self.group_body(mode="audio")).status_code)
                except Exception as exc:     # a raised error is a 500 to a user
                    failures.append(repr(exc))

        def reads():
            client = self._client(session)
            while not stop.is_set():
                try:
                    statuses["read"].append(self._get(
                        client, "/api/user/matrix_state").status_code)
                except Exception as exc:
                    failures.append(repr(exc))

        workers = [threading.Thread(target=routes) for _ in range(3)] + \
                  [threading.Thread(target=reads) for _ in range(2)]
        for worker in workers:
            worker.start()
        try:
            for turn in range(40):
                interfaces = (["matrix"], ["standard"], ["matrix", "standard"])[turn % 3]
                self.regrant(account, interfaces=interfaces,
                             sources=[SRC_A, SRC_B] if turn % 2 else [SRC_B])
        finally:
            stop.set()
            for worker in workers:
                worker.join(30)
        self.assertEqual(failures, [])
        for kind, allowed in (("route", self.ALLOWED_ROUTE),
                              ("group", self.ALLOWED_ROUTE),
                              ("read", self.ALLOWED_READ)):
            with self.subTest(kind=kind):
                self.assertTrue(statuses[kind], "no %s request ran" % kind)
                self.assertEqual(set(statuses[kind]) - allowed, set())

        # The last withdrawal is final: nothing more reaches the engine.
        self.regrant(account, interfaces=["standard"])
        before = len(self.routes)
        client = self._client(session)
        for _ in range(10):
            self.assertEqual(self._post(client, session, "/api/user/route",
                                        self.route_body()).status_code, 403)
            self.assertEqual(self._post(client, session, "/api/user/route_group",
                                        self.group_body()).status_code, 403)
        self.assertEqual(len(self.routes), before)


if __name__ == "__main__":
    unittest.main()
