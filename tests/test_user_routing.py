"""User routing, source previews and signing out — the things hands-on testing found.

The reported defect was that restricted-user routing was "hit or miss", with
`409 (CONFLICT)` in the console. Everybody's first assumption, including the
brief's, was lock contention. Measured on the bench it was not: the refusal
came back in 0.37s carrying `status: MULTIVIEW ACTIVE`, where a lock timeout
costs 45 seconds. The A/V engine refuses to take a display off a composition
unless the caller has said so, and the user endpoint never passed that answer
through -- so a display that happened to be compositing refused every route and
every other display worked. That is exactly what hit-or-miss looks like from
the outside.

These hold the fix and the fences around it:

  * the two 409s are told apart by a machine-readable reason, so neither the
    page nor a future reader has to guess from how long it took;
  * the operator's answer travels with the request, and without it nothing
    reaches a device;
  * a preview is authorised against the account at the moment it is asked for,
    addressed by stable identity so no device address reaches a restricted
    client, and its absence never makes a source unroutable.

Nothing here contacts hardware. The route engine and the preview producer are
stubbed, and the gate is driven directly.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("OMNI_SMOKE", "1")

import omni_gate                                     # noqa: E402
import omni_locks                                    # noqa: E402
import omni_remote                                   # noqa: E402
import omni_users                                    # noqa: E402
from omni_users import Permissions                   # noqa: E402

#: Documentation-range identities only; a test may never carry a bench value.
TEACHER_PC = "b8:98:b0:00:00:01"
DOC_CAMERA = "b8:98:b0:00:00:02"
UNSEEN_SOURCE = "b8:98:b0:00:00:09"
FRONT_DISPLAY = "b8:98:b0:00:00:11"
UNSEEN_DISPLAY = "b8:98:b0:00:00:19"

UNITS = [
    {"ip": "192.0.2.11", "mac": TEACHER_PC, "hostname": "enc-teacher",
     "model": "hw-omni-e4521", "role": "encoder"},
    {"ip": "192.0.2.12", "mac": DOC_CAMERA, "hostname": "enc-camera",
     "model": "hw-omni-e4111", "role": "encoder"},
    {"ip": "192.0.2.19", "mac": UNSEEN_SOURCE, "hostname": "enc-other",
     "model": "hw-omni-e4111", "role": "encoder"},
    {"ip": "192.0.2.21", "mac": FRONT_DISPLAY, "hostname": "dec-front",
     "model": "hw-omni-d4511", "role": "decoder"},
    {"ip": "192.0.2.29", "mac": UNSEEN_DISPLAY, "hostname": "dec-other",
     "model": "hw-omni-d4511", "role": "decoder"},
]

#: A PNG header, so a test can tell an image from a refusal without inventing
#: a format.
PIXEL = (b"\x89PNG\r\n\x1a\n" + b"\x00" * 24)


class UserRoutingTestBase(unittest.TestCase):
    """The real application, reached remotely, with the devices stubbed out."""

    def setUp(self):
        import OmniMatrix_upgrade_server_v7_6y as srv
        self.srv = srv

        self.dir = Path(tempfile.mkdtemp(prefix="omni-routing-"))
        self.store = srv._user_store
        saved = (self.store._path, self.store._audit_path,
                 dict(self.store._accounts), dict(self.store._sessions),
                 list(self.store._audit), dict(self.store._failures))

        def restore():
            self.store._path, self.store._audit_path = saved[0], saved[1]
            for held, original in ((self.store._accounts, saved[2]),
                                   (self.store._sessions, saved[3]),
                                   (self.store._failures, saved[5])):
                held.clear()
                held.update(original)
            self.store._audit[:] = saved[4]

        self.addCleanup(restore)
        self.store._path = self.dir / "users.json"
        self.store._audit_path = self.dir / "audit.json"
        for held in (self.store._accounts, self.store._sessions,
                     self.store._failures):
            held.clear()
        self.store._audit.clear()

        listener = srv._remote_listener
        previous = listener._server
        self.addCleanup(lambda: setattr(listener, "_server", previous))
        listener._server = object()

        # The device cache the endpoint resolves identities against. Deep
        # copied, because background work mutates cached device dicts in place.
        #
        # Patched through the closure, not the module attribute: register()
        # took `load_cache` as an argument, so the views hold the object they
        # were given and reassigning `srv._load_cache` changes nothing they
        # can see. That is exactly the trap AGENTS.md records, and it showed up
        # here as every route answering 404 "not currently known".

        # The verified route engine, stubbed. Every call is recorded, so a test
        # can assert what reached it -- and that nothing did when a refusal was
        # supposed to come first.
        self.routes = []
        self.route_answer = ({"ok": True, "status": "VERIFIED"}, 200)

        def fake_route():
            from flask import request as flask_request, jsonify
            body = flask_request.get_json(silent=True) or {}
            self.routes.append(dict(body))
            answer = self.route_answer
            payload, status = answer if isinstance(answer, tuple) else (answer, 200)
            return jsonify(payload), status

        # The unroute engine, stubbed the same way. It is the same shape of
        # dependency as the route engine -- authorise here, delegate there --
        # so it is fenced here too, and a refusal that still reached it is a
        # test failure rather than a write to a decoder.
        self.unroutes = []
        self.unroute_answer = ({"ok": True, "status": "UNROUTED",
                                "cleared": ["ip_input1", "ip_input3"]}, 200)

        def fake_unroute():
            from flask import request as flask_request, jsonify
            body = flask_request.get_json(silent=True) or {}
            self.unroutes.append(dict(body))
            answer = self.unroute_answer
            payload, status = answer if isinstance(answer, tuple) else (answer, 200)
            return jsonify(payload), status

        self._patch_registered("load_cache", lambda: [dict(u) for u in UNITS])
        self._patch_registered("route_view", fake_route)
        self._patch_registered("unroute_view", fake_unroute)

        self.previews = []
        self.preview_freshness = []
        self.preview_answer = (PIXEL, "image/png", "")
        self._patch_registered("source_preview", self._fake_preview)

        self.client = srv.app.test_client()
        self.store.create("principal", "principal-pass-1", omni_users.ROLE_ADMIN)
        self.store.create("teacher", "teacher-password-1", omni_users.ROLE_USER,
                          Permissions(features=("av",),
                                      sources=[TEACHER_PC, DOC_CAMERA],
                                      destinations=[FRONT_DISPLAY]))
        self.admin = self.store.find_by_username("principal")
        self.teacher = self.store.find_by_username("teacher")

    def _fake_preview(self, address, fresh=False):
        # `fresh` is recorded as well as accepted: a page that has just opened
        # asks the encoder to be re-read rather than answered from the cache,
        # and that is behaviour worth being able to assert.
        self.previews.append(address)
        self.preview_freshness.append(bool(fresh))
        return self.preview_answer

    def _patch_registered(self, name, replacement):
        """Replace a callable the API closed over when it registered.

        `register()` takes these as arguments, so they live in the view
        functions' closures rather than as module attributes. Reaching into
        the closure cell is the only way to substitute one, and it is restored
        on cleanup.
        """
        import omni_remote_api
        for view in self.srv.app.view_functions.values():
            closure = getattr(view, "__closure__", None) or ()
            names = getattr(view, "__code__", None)
            names = names.co_freevars if names else ()
            for index, freevar in enumerate(names):
                if freevar != name:
                    continue
                cell = closure[index]
                original = cell.cell_contents
                cell.cell_contents = replacement
                self.addCleanup(
                    lambda c=cell, o=original: setattr(c, "cell_contents", o))

    # -- helpers -------------------------------------------------------
    def session_for(self, account, method=omni_users.BY_PASSWORD):
        return self.store.open_session(account.id, method)

    def post(self, path, body, session):
        self.client.set_cookie(omni_gate.SESSION_COOKIE, session.token)
        return self.client.post(
            path, json=body,
            environ_base={omni_remote.LISTENER_KEY: omni_remote.REMOTE},
            headers={"X-OmniSuite-CSRF": session.csrf_token})

    def get(self, path, session):
        self.client.set_cookie(omni_gate.SESSION_COOKIE, session.token)
        return self.client.get(
            path, environ_base={omni_remote.LISTENER_KEY: omni_remote.REMOTE})

    def route(self, session, source=TEACHER_PC, destination=FRONT_DISPLAY,
              **extra):
        body = {"source": source, "destination": destination, "mode": "av"}
        body.update(extra)
        return self.post("/api/user/route", body, session)


class ANormalRouteMeansSingleViewTests(UserRoutingTestBase):
    """Routing a source to a compositing display just works.

    The first version of this fix made the operator say so: the engine refused
    and each surface asked a question to obtain the answer. That was one
    question too many -- putting a source on a display IS the instruction, and
    nobody choosing a camera is thinking about what the display was doing
    first. So the question is gone and the engine leaves the Multiview itself.

    What is NOT gone is the transaction around it, which is the reason this is
    a change to the default rather than a deleted check.
    """

    def test_a_route_to_a_compositing_display_is_not_refused(self):
        response = self.route(self.session_for(self.teacher))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["ok"])

    def test_the_request_carries_no_answer_because_none_is_needed(self):
        """Nothing has to be asked, so nothing has to be sent."""
        self.route(self.session_for(self.teacher))
        self.assertEqual(len(self.routes), 1)
        self.assertNotIn("exit_multiview", self.routes[0],
                         "a normal route should not need to say it is normal")

    def test_a_stale_flag_from_an_older_page_is_harmless(self):
        """A browser holding a cached page still works."""
        response = self.route(self.session_for(self.teacher),
                              exit_multiview=True)
        self.assertEqual(response.status_code, 200)

    def test_a_busy_display_is_still_its_own_answer(self):
        """The refusal that remains still names itself."""
        self._patch_registered("locks", _BusyLocks())
        busy = self.route(self.session_for(self.teacher))
        self.assertEqual(busy.status_code, 409)
        self.assertEqual(busy.get_json()["reason"], "resource_busy")

    def test_a_busy_refusal_reaches_no_device(self):
        self._patch_registered("locks", _BusyLocks())
        self.route(self.session_for(self.teacher))
        self.assertEqual(self.routes, [],
                         "a request refused for the lock must not reach the "
                         "route engine")


class _BusyLocks:
    """A lock manager whose every acquisition times out."""

    @staticmethod
    def decoder(identity):
        return "decoder:" + str(identity or "").lower()

    @staticmethod
    def usb_pair(a, b):
        return ("usb:" + str(a), "usb:" + str(b))

    def hold(self, *resources, timeout=None):
        raise omni_locks.ResourceBusy(resources[0] if resources else "resource")


class OneClickOneRouteTests(UserRoutingTestBase):
    """A deliberate action produces one device operation, not two."""

    def test_one_request_reaches_the_engine_once(self):
        self.route(self.session_for(self.teacher))
        self.assertEqual(len(self.routes), 1)

    def test_the_same_route_twice_is_two_deliberate_operations(self):
        """Repeating is allowed -- last successful operation wins."""
        session = self.session_for(self.teacher)
        self.assertEqual(self.route(session).status_code, 200)
        self.assertEqual(self.route(session).status_code, 200)
        self.assertEqual(len(self.routes), 2)

    def test_sequential_sources_to_one_display_all_arrive(self):
        session = self.session_for(self.teacher)
        for source in (TEACHER_PC, DOC_CAMERA, TEACHER_PC):
            self.assertEqual(self.route(session, source=source).status_code, 200)
        self.assertEqual([r["encoder"] for r in self.routes],
                         ["192.0.2.11", "192.0.2.12", "192.0.2.11"])

    def test_the_display_is_held_for_the_duration_of_one_route(self):
        """Serialised on the display, and released afterwards."""
        held = []
        real = self.srv._resource_locks

        def watch():
            held.append(sorted(real.busy()))

        self.route_answer = ({"ok": True, "status": "VERIFIED"}, 200)
        original = self.route_answer

        def observing_route():
            from flask import request as flask_request, jsonify
            self.routes.append(dict(flask_request.get_json(silent=True) or {}))
            watch()
            return jsonify(original[0]), original[1]

        self._patch_registered("route_view", observing_route)
        self.route(self.session_for(self.teacher))
        self.assertEqual(held, [["decoder:" + FRONT_DISPLAY]],
                         "the display should be held while its route runs")
        self.assertEqual(real.busy(), [],
                         "and released the moment the operation finished")

    def test_the_lock_is_released_when_the_engine_fails(self):
        self.route_answer = ({"ok": False, "status": "FAILED"}, 200)
        self.route(self.session_for(self.teacher))
        self.assertEqual(self.srv._resource_locks.busy(), [])

    def test_the_lock_is_released_when_the_engine_raises(self):
        """Flask turns the exception into a 500; the display must still be free."""
        def explode():
            raise RuntimeError("the engine fell over")
        self._patch_registered("route_view", explode)
        self.client.set_cookie(omni_gate.SESSION_COOKIE,
                               self.session_for(self.teacher).token)
        try:
            self.route(self.session_for(self.teacher))
        except RuntimeError:
            pass          # propagated by the test client rather than caught
        self.assertEqual(self.srv._resource_locks.busy(), [],
                         "an exception must not strand a display")

    def test_unrelated_displays_do_not_serialise_against_each_other(self):
        """Room 101 must not wait on Room 102."""
        locks = self.srv._resource_locks
        with locks.hold(locks.decoder(UNSEEN_DISPLAY)):
            response = self.route(self.session_for(self.teacher))
        self.assertEqual(response.status_code, 200)


class AuthorisationStillAppliesTests(UserRoutingTestBase):
    """Nothing about the fix relaxes who may route what."""

    def test_a_source_the_account_never_had_is_refused(self):
        response = self.route(self.session_for(self.teacher),
                              source=UNSEEN_SOURCE)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.routes, [])

    def test_a_display_the_account_never_had_is_refused(self):
        response = self.route(self.session_for(self.teacher),
                              destination=UNSEEN_DISPLAY)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.routes, [])

    def test_a_permission_removed_while_the_page_was_open_is_refused(self):
        session = self.session_for(self.teacher)
        self.store.set_permissions(
            self.teacher.id, Permissions(features=("av",), sources=[],
                                         destinations=[FRONT_DISPLAY]),
            actor=self.admin)
        response = self.route(session)
        self.assertIn(response.status_code, (401, 403))
        self.assertEqual(self.routes, [])

    def test_a_disabled_account_cannot_route(self):
        session = self.session_for(self.teacher)
        self.store.set_enabled(self.teacher.id, False, actor=self.admin)
        response = self.route(session)
        self.assertEqual(response.status_code, 401)
        self.assertEqual(self.routes, [])

    def test_exit_multiview_is_not_a_way_past_authorisation(self):
        """The answer says what may happen to a display, not which display."""
        response = self.route(self.session_for(self.teacher),
                              destination=UNSEEN_DISPLAY, exit_multiview=True)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.routes, [])


class BothKindsOfSessionRouteIdenticallyTests(UserRoutingTestBase):
    """How somebody signed in must not change what routing does."""

    def test_a_direct_link_session_routes(self):
        self.store.enable_direct_access(self.teacher.id, actor=self.admin)
        session = self.session_for(self.teacher, omni_users.BY_DIRECT_LINK)
        self.assertEqual(self.route(session).status_code, 200)

    def test_both_sessions_produce_the_same_device_operation(self):
        self.store.enable_direct_access(self.teacher.id, actor=self.admin)
        self.route(self.session_for(self.teacher))
        self.route(self.session_for(self.teacher, omni_users.BY_DIRECT_LINK))
        self.assertEqual(len(self.routes), 2)
        self.assertEqual(self.routes[0], self.routes[1])

    def test_a_direct_link_session_is_refused_an_unpermitted_display(self):
        self.store.enable_direct_access(self.teacher.id, actor=self.admin)
        session = self.session_for(self.teacher, omni_users.BY_DIRECT_LINK)
        self.assertEqual(self.route(session, destination=UNSEEN_DISPLAY)
                         .status_code, 403)


class SourcePreviewTests(UserRoutingTestBase):
    """A picture of a permitted source, and of nothing else."""

    def preview(self, session, source=TEACHER_PC):
        return self.get("/api/user/source_preview?source=" + source, session)

    def test_a_permitted_source_returns_an_image(self):
        response = self.preview(self.session_for(self.teacher))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.headers["Content-Type"].startswith("image/"))
        self.assertEqual(response.get_data(), PIXEL)

    def test_a_source_the_account_may_not_use_has_no_preview(self):
        """Not because the card was hidden. Because the server said no."""
        response = self.preview(self.session_for(self.teacher), UNSEEN_SOURCE)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.previews, [],
                         "an unauthorised preview must not reach a device")

    def test_a_direct_link_session_is_held_to_the_same_rule(self):
        self.store.enable_direct_access(self.teacher.id, actor=self.admin)
        session = self.session_for(self.teacher, omni_users.BY_DIRECT_LINK)
        self.assertEqual(self.preview(session).status_code, 200)
        self.assertEqual(self.preview(session, UNSEEN_SOURCE).status_code, 403)

    def test_a_permission_removed_takes_the_preview_with_it(self):
        session = self.session_for(self.teacher)
        self.store.set_permissions(
            self.teacher.id, Permissions(features=("av",), sources=[],
                                         destinations=[FRONT_DISPLAY]),
            actor=self.admin)
        self.assertIn(self.preview(session).status_code, (401, 403))

    def test_no_device_address_is_ever_in_a_preview_response(self):
        """The endpoint is addressed by identity for exactly this reason."""
        session = self.session_for(self.teacher)
        for source in (TEACHER_PC, UNSEEN_SOURCE):
            with self.subTest(source=source):
                # Bytes, because a success is a picture and decoding one as
                # text raises before the assertion is ever reached.
                body = self.preview(session, source).get_data()
                for address in (b"192.0.2.11", b"192.0.2.12", b"192.0.2.19"):
                    self.assertNotIn(address, body)
                self.assertNotIn(b"192.0.2", body)

    def test_nothing_about_the_device_is_in_a_refusal(self):
        session = self.session_for(self.teacher)
        for reason in ("preview_unavailable", "preview_disabled"):
            with self.subTest(reason=reason):
                self.preview_answer = (None, "", reason)
                body = self.preview(session).get_data(as_text=True).lower()
                for leaked in ("password", "serial", "mac", "hw-omni",
                               "firmware", "enc-teacher",
                               # The address above all: this endpoint is
                               # addressed by identity precisely so a
                               # restricted client never learns one.
                               "192.0.2.11", "192.0.2"):
                    self.assertNotIn(leaked, body)

    def test_a_disabled_thumbnail_is_said_rather_than_shown(self):
        """The device answers 200 with a placeholder either way, so the
        application must not pass one off as a picture."""
        self.preview_answer = (None, "", "preview_disabled")
        response = self.preview(self.session_for(self.teacher))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["reason"], "preview_disabled")
        self.assertFalse(response.get_json()["ok"])

    def test_an_unavailable_preview_is_not_an_error_status(self):
        """A failed request per source without a thumbnail is noise an
        operator would have to learn to ignore."""
        self.preview_answer = (None, "", "preview_unavailable")
        response = self.preview(self.session_for(self.teacher))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["reason"], "preview_unavailable")

    def test_a_source_with_no_preview_is_still_routable(self):
        """Preview availability never decides whether a source may be used."""
        self.preview_answer = (None, "", "preview_disabled")
        self.preview(self.session_for(self.teacher))
        self.assertEqual(self.route(self.session_for(self.teacher))
                         .status_code, 200)

    def test_asking_for_a_preview_routes_nothing(self):
        self.preview(self.session_for(self.teacher))
        self.assertEqual(self.routes, [])

    def test_a_preview_is_not_stored_by_a_browser(self):
        response = self.preview(self.session_for(self.teacher))
        self.assertIn("no-store", response.headers["Cache-Control"])
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")

    def test_an_unknown_source_is_refused_without_reaching_a_device(self):
        response = self.preview(self.session_for(self.teacher),
                                "b8:98:b0:ff:ff:ff")
        self.assertIn(response.status_code, (403, 404))
        self.assertEqual(self.previews, [])

    def test_the_identity_is_normalised_before_it_is_used(self):
        session = self.session_for(self.teacher)
        for spelling in (TEACHER_PC, TEACHER_PC.upper(),
                         TEACHER_PC.replace(":", "-")):
            with self.subTest(spelling=spelling):
                self.assertEqual(self.preview(session, spelling).status_code,
                                 200)


class SignOutTests(UserRoutingTestBase):
    """Ending a session, and ending only that one."""

    def test_signing_out_ends_the_session(self):
        session = self.session_for(self.teacher)
        self.assertEqual(self.post("/api/auth/logout", {}, session).status_code,
                         200)
        self.assertEqual(self.store.resolve_session(session.token), (None, None))

    def test_another_session_of_the_same_account_survives(self):
        one = self.session_for(self.teacher)
        two = self.session_for(self.teacher)
        self.post("/api/auth/logout", {}, one)
        account, _s = self.store.resolve_session(two.token)
        self.assertIsNotNone(account, "signing out one browser must not sign "
                                      "out the other")

    def test_an_administrator_signing_out_leaves_a_user_alone(self):
        admin_session = self.session_for(self.admin)
        teacher_session = self.session_for(self.teacher)
        self.post("/api/auth/logout", {}, admin_session)
        self.assertIsNotNone(
            self.store.resolve_session(teacher_session.token)[0])

    def test_a_protected_api_is_refused_afterwards(self):
        session = self.session_for(self.teacher)
        self.post("/api/auth/logout", {}, session)
        self.assertEqual(self.get("/api/user/state", session).status_code, 401)

    def test_routing_is_refused_afterwards(self):
        session = self.session_for(self.teacher)
        self.post("/api/auth/logout", {}, session)
        self.assertEqual(self.route(session).status_code, 401)
        self.assertEqual(self.routes, [])

    def test_a_direct_link_session_signs_out_like_any_other(self):
        self.store.enable_direct_access(self.teacher.id, actor=self.admin)
        session = self.session_for(self.teacher, omni_users.BY_DIRECT_LINK)
        self.post("/api/auth/logout", {}, session)
        self.assertEqual(self.store.resolve_session(session.token), (None, None))

    def test_signing_out_of_a_direct_session_leaves_the_link_working(self):
        """Signing out ends a session. It does not withdraw the link."""
        token = self.store.enable_direct_access(
            self.teacher.id, actor=self.admin)["token"]
        session = self.session_for(self.teacher, omni_users.BY_DIRECT_LINK)
        self.post("/api/auth/logout", {}, session)
        self.assertIsNotNone(self.store.resolve_direct_token(token))
        # And it can open a new one.
        self.client.delete_cookie(omni_gate.SESSION_COOKIE)
        response = self.client.get(
            "/access/" + token,
            environ_base={omni_remote.LISTENER_KEY: omni_remote.REMOTE,
                          "HTTP_SEC_FETCH_MODE": "navigate",
                          "HTTP_ACCEPT": "text/html"})
        # The panel is served at the link's own URL rather than redirected to,
        # so the link stays bookmarkable; a fresh session cookie is what the
        # new session runs on.
        self.assertEqual(response.status_code, 200)
        self.assertIn(omni_gate.SESSION_COOKIE,
                      response.headers.get("Set-Cookie", ""))

    def test_the_session_endpoint_says_who_is_signed_in(self):
        """What the injected Sign out control decides from."""
        body = self.get("/api/auth/session",
                        self.session_for(self.teacher)).get_json()
        self.assertEqual(body["username"], "teacher")
        self.assertFalse(body["local"])

    def test_a_local_request_reports_no_account_to_sign_out_of(self):
        response = self.client.get(
            "/api/auth/session",
            environ_base={omni_remote.LISTENER_KEY: omni_remote.LOCAL})
        body = response.get_json()
        self.assertTrue(body["local"])
        self.assertIsNone(body["username"])


if __name__ == "__main__":
    unittest.main()


class PreviewBehindTheGateTests(UserRoutingTestBase):
    """The handler's own checks, reached deliberately.

    The gate refuses an unpermitted `source` before these are ever run, which
    is right and is why a mutation of them survived the first time: a check the
    ordinary path never reaches is not redundant, it is untested. An
    administrator's session passes the gate and skips `_revalidate`, so it
    reaches the handler body -- which is how these get exercised at all.
    """

    def preview(self, session, source):
        return self.get("/api/user/source_preview?source=" + source, session)

    def test_an_identity_with_no_address_is_refused_without_leaking_one(self):
        response = self.preview(self.session_for(self.admin),
                                "b8:98:b0:ff:ff:ff")
        self.assertEqual(response.status_code, 404)
        body = response.get_data()
        self.assertEqual(response.get_json()["reason"], "preview_unavailable")
        for address in (b"192.0.2.11", b"192.0.2.12", b"192.0.2.19", b"192.0.2"):
            self.assertNotIn(address, body)
        self.assertEqual(self.previews, [],
                         "nothing should have been asked of a device")

    def test_a_missing_source_argument_is_a_bad_request(self):
        response = self.get("/api/user/source_preview", self.session_for(self.admin))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.previews, [])

    def test_the_address_is_resolved_server_side_and_never_supplied(self):
        """The endpoint takes an identity; a caller cannot aim it at a device."""
        self.preview(self.session_for(self.admin), TEACHER_PC)
        self.assertEqual(self.previews, ["192.0.2.11"])
        # And an address in the query string is not an identity, so it resolves
        # to nothing rather than being used.
        self.previews.clear()
        response = self.preview(self.session_for(self.admin), "192.0.2.11")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.previews, [])


class ThePreviewProducerTests(unittest.TestCase):
    """The device half, with the device stubbed.

    Separate from the endpoint tests because those stub this out entirely --
    so without these, the rule that a disabled thumbnail is never passed off as
    a picture had no test at all, and a mutation of it survived.
    """

    def setUp(self):
        import OmniMatrix_upgrade_server_v7_6y as srv
        self.srv = srv
        srv._preview_images.invalidate()
        srv._preview_enabled.invalidate()
        self.addCleanup(srv._preview_images.invalidate)
        self.addCleanup(srv._preview_enabled.invalidate)

        self.fetched = []
        self.enabled = True
        self.answer = (b"\x89PNG\r\n\x1a\n", "image/jpeg", 200)

        real_enabled = srv._thumbnail_enabled
        self.addCleanup(lambda: setattr(srv, "_thumbnail_enabled", real_enabled))
        srv._thumbnail_enabled = lambda address: self.enabled

        real_open = srv.urllib.request.urlopen
        self.addCleanup(
            lambda: setattr(srv.urllib.request, "urlopen", real_open))
        srv.urllib.request.urlopen = self._fake_open

    def _fake_open(self, url, timeout=None, **kwargs):
        self.fetched.append(url)
        body, kind, status = self.answer
        if body is None:
            raise OSError("the encoder did not answer")
        return _FakeResponse(body, kind, status)

    def test_an_enabled_encoder_yields_a_picture(self):
        body, kind, reason = self.srv.source_preview("192.0.2.11")
        self.assertEqual(reason, "")
        self.assertTrue(kind.startswith("image/"))
        self.assertEqual(body, b"\x89PNG\r\n\x1a\n")

    def test_the_url_is_built_from_the_trusted_address(self):
        self.srv.source_preview("192.0.2.11")
        self.assertEqual(self.fetched,
                         ["http://192.0.2.11/thumbnail/thumbnail1.jpg"])

    def test_a_disabled_encoder_is_never_shown_as_a_picture(self):
        """The device answers 200 with a placeholder either way."""
        self.enabled = False
        body, _kind, reason = self.srv.source_preview("192.0.2.11")
        self.assertIsNone(body)
        self.assertEqual(reason, "preview_disabled")
        self.assertEqual(self.fetched, [],
                         "a disabled encoder must not be fetched from at all")

    def test_an_unreachable_encoder_is_unavailable_rather_than_disabled(self):
        self.answer = (None, "", 0)
        body, _kind, reason = self.srv.source_preview("192.0.2.11")
        self.assertIsNone(body)
        self.assertEqual(reason, "preview_unavailable")

    def test_something_that_is_not_an_image_is_not_passed_through(self):
        self.answer = (b"<html>login</html>", "text/html", 200)
        body, _kind, reason = self.srv.source_preview("192.0.2.11")
        self.assertIsNone(body)
        self.assertEqual(reason, "preview_unavailable")

    def test_an_oversized_body_is_refused(self):
        self.answer = (b"x" * (self.srv.PREVIEW_MAX_BYTES + 10), "image/jpeg", 200)
        body, _kind, reason = self.srv.source_preview("192.0.2.11")
        self.assertIsNone(body)
        self.assertEqual(reason, "preview_unavailable")

    def test_an_empty_address_asks_nothing(self):
        for address in ("", "   ", None):
            with self.subTest(address=address):
                body, _kind, reason = self.srv.source_preview(address)
                self.assertIsNone(body)
                self.assertEqual(reason, "preview_unavailable")
        self.assertEqual(self.fetched, [])

    def test_many_clients_cost_one_device_fetch(self):
        """The whole point of the cache: browsers must not multiply traffic."""
        for _ in range(25):
            self.srv.source_preview("192.0.2.11")
        self.assertEqual(len(self.fetched), 1,
                         "25 clients produced %d device fetches"
                         % len(self.fetched))

    def test_two_sources_are_two_fetches(self):
        self.srv.source_preview("192.0.2.11")
        self.srv.source_preview("192.0.2.12")
        self.assertEqual(len(self.fetched), 2)


class _FakeResponse:
    def __init__(self, body, kind, status):
        self._body = body
        self.status = status
        self.headers = {"Content-Type": kind}

    def read(self, limit=None):
        return self._body[:limit] if limit else self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class ThePreviewChecksItselfTests(UserRoutingTestBase):
    """The handler's own grant check, with the gate's taken out of the way.

    The gate inspects `source` on this route and refuses an unpermitted one
    before the handler runs, so a mutation of the handler's check survived:
    not because the check is redundant, but because the ordinary path never
    reaches it. A second line of defence that nothing can reach is untested,
    so this reaches it on purpose.

    The gate is not weakened for the application -- only for the span of the
    test, and it is put back.
    """

    def setUp(self):
        super().setUp()
        # The gate finds a device identity in the request by looking for these
        # keys. Emptied here, it admits the request and the handler is the
        # only thing standing between the account and the device.
        real = omni_gate.SOURCE_KEYS
        self.addCleanup(lambda: setattr(omni_gate, "SOURCE_KEYS", real))
        omni_gate.SOURCE_KEYS = ()

    def test_the_gate_really_was_what_refused_it_before(self):
        """The precondition, asserted: without this the test proves nothing."""
        response = self.get(
            "/api/user/source_preview?source=" + UNSEEN_SOURCE,
            self.session_for(self.teacher))
        self.assertNotEqual(
            response.status_code, 200,
            "the handler admitted a source the account never had")

    def test_the_handler_refuses_a_source_the_account_never_had(self):
        response = self.get(
            "/api/user/source_preview?source=" + UNSEEN_SOURCE,
            self.session_for(self.teacher))
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.previews, [],
                         "an unauthorised preview reached a device")

    def test_a_permitted_source_is_still_served(self):
        response = self.get(
            "/api/user/source_preview?source=" + TEACHER_PC,
            self.session_for(self.teacher))
        self.assertEqual(response.status_code, 200)


class TheEngineAnswerIsProjectedTests(UserRoutingTestBase):
    """The A/V engine answers with the decoder's own record; a user must not.

    The engine is written for the administrative surface, so a successful route
    comes back carrying `decoder.ip`, the multicast groups it just wrote and the
    input names it wrote them to. Forwarding that put a device address into a
    restricted browser -- the one thing the projection exists to prevent -- and
    it did so on the success path, which is the path nobody looks at.
    """

    ENGINE_BODY = {
        "ok": True,
        "status": "VERIFIED",
        "planes": {"video": "verified", "audio": "verified", "ok": True},
        "decoder": {
            "ip": "192.0.2.21",
            "ip1_addr": "239.70.1.2", "ip1_port": 1000,
            "ip3_addr": "239.80.1.2", "ip3_port": 1000,
            "video_input": "ip_input1",
            "multiview_active": False,
        },
        "multiview_exit": {"multiview": "multiview2x2", "ok": True,
                           "released": ["ip_input2", "ip_input4"],
                           "steps": ["moved output", "released inputs"]},
    }

    def test_no_device_address_survives_into_a_route_answer(self):
        self.route_answer = (dict(self.ENGINE_BODY), 200)
        response = self.route(self.session_for(self.teacher))
        self.assertEqual(response.status_code, 200)
        raw = response.get_data(as_text=True)
        for leaked in ("192.0.2", "239.70", "239.80", "ip_input",
                       "video_input"):
            with self.subTest(leaked=leaked):
                self.assertNotIn(leaked, raw,
                                 "the engine's device record reached a "
                                 "restricted client")

    def test_the_operator_facing_result_is_still_there(self):
        """Reducing the answer must not reduce what the page can say."""
        self.route_answer = (dict(self.ENGINE_BODY), 200)
        body = self.route(self.session_for(self.teacher)).get_json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["status"], "VERIFIED")
        self.assertEqual(body["planes"]["audio"], "verified")
        # Summarised, not forwarded: that it left a Multiview is a fact about
        # the operation; which inputs were released is not.
        self.assertEqual(body["multiview_exit"], {"left": True})

    def test_a_failure_keeps_its_reason_and_loses_its_addresses(self):
        self.route_answer = ({
            "ok": False, "status": "FAILED", "reason": "plane_not_verified",
            "error": "The display did not confirm the audio subscription "
                     "after it was set.",
            "planes": {"video": "verified", "audio": "failed", "ok": False},
            "decoder": {"ip": "192.0.2.21", "ip1_addr": "239.70.1.2"},
        }, 200)
        response = self.route(self.session_for(self.teacher))
        body = response.get_json()
        self.assertFalse(body["ok"])
        self.assertEqual(body["reason"], "plane_not_verified")
        self.assertIn("audio", body["error"])
        self.assertNotIn("192.0.2", response.get_data(as_text=True))

    def test_an_unknown_engine_field_is_dropped_rather_than_forwarded(self):
        """Built from a fixed list, not filtered by removing known fields.

        A reducer that strips what somebody remembered lets the next field the
        engine gains through by default. This one has to be widened on purpose.
        """
        self.route_answer = ({"ok": True, "status": "VERIFIED",
                              "something_new": {"ip": "192.0.2.21"}}, 200)
        body = self.route(self.session_for(self.teacher)).get_json()
        self.assertNotIn("something_new", body)


class UnrouteTests(UserRoutingTestBase):
    """Stopping a display: authorised like a route, and projected like one."""

    def unroute(self, destination, session=None):
        return self.post("/api/user/unroute", {"destination": destination},
                         session)

    def test_a_permitted_display_is_handed_to_the_verified_engine(self):
        response = self.unroute(FRONT_DISPLAY, self.session_for(self.teacher))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(self.unroutes), 1,
                         "the engine was not reached exactly once")
        self.assertEqual(self.unroutes[0]["decoder"], "192.0.2.21",
                         "the identity was not resolved to its address")

    def test_a_display_this_account_does_not_hold_is_refused(self):
        response = self.unroute(UNSEEN_DISPLAY, self.session_for(self.teacher))
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json()["reason"],
                         "destination_not_permitted")
        self.assertEqual(self.unroutes, [],
                         "a refused request still reached the hardware")

    def test_the_decoder_s_own_input_names_are_not_reported(self):
        """`cleared` names ip_input1 and ip_input3. That is device internals.

        Caught by driving the real endpoint and searching the response, not by
        reading the reducer: the names had been added to its allowlist without
        anybody noticing what they contained. The operator learns what happened
        from the status and the sentence.
        """
        self.unroute_answer = ({"ok": True, "status": "UNROUTED",
                                "cleared": ["ip_input1", "ip_input3"],
                                "failed": []}, 200)
        response = self.unroute(FRONT_DISPLAY, self.session_for(self.teacher))
        raw = response.get_data(as_text=True)
        self.assertNotIn("ip_input", raw,
                         "a decoder input name reached a restricted client")
        body = response.get_json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["status"], "UNROUTED",
                         "removing the detail removed the answer too")

    def test_the_answer_carries_no_device_address(self):
        self.unroute_answer = ({"ok": True, "status": "UNROUTED",
                                "cleared": ["ip_input1", "ip_input3"],
                                "decoder": {"ip": "192.0.2.21",
                                            "ip1_addr": ""}}, 200)
        response = self.unroute(FRONT_DISPLAY, self.session_for(self.teacher))
        body = response.get_json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["status"], "UNROUTED")
        self.assertNotIn("192.0.2", response.get_data(as_text=True))

    def test_an_account_without_av_cannot_stop_a_display(self):
        self.store.set_permissions(
            self.teacher.id,
            Permissions(features=("usb",), sources=[TEACHER_PC],
                        destinations=[FRONT_DISPLAY]),
            actor=self.admin)
        response = self.unroute(FRONT_DISPLAY, self.session_for(self.teacher))
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json()["reason"], "missing_feature:av")
        self.assertEqual(self.unroutes, [])

    def test_a_display_that_is_not_known_is_a_404_not_a_400(self):
        """Well-formed, and about a device this host cannot currently place."""
        self.store.set_permissions(
            self.teacher.id,
            Permissions(features=("av",), sources=[TEACHER_PC],
                        destinations=[FRONT_DISPLAY, "b8:98:b0:00:00:99"]),
            actor=self.admin)
        response = self.unroute("b8:98:b0:00:00:99",
                                self.session_for(self.teacher))
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.unroutes, [])

    def test_a_missing_destination_is_a_400(self):
        response = self.post("/api/user/unroute", {},
                             self.session_for(self.teacher))
        self.assertEqual(response.status_code, 400)


class ARefusalSaysWhichRefusalItIsTests(UserRoutingTestBase):
    """403 had eight origins and one message, so it diagnosed nothing.

    "A User granted Multiview gets 403" was unanswerable from the response: the
    reason went to the audit log, which the person holding the tablet cannot
    read. Each refusal now names itself in a stable token, with a sentence
    beside it for the operator.
    """

    def test_a_source_refusal_and_a_destination_refusal_differ(self):
        session = self.session_for(self.teacher)
        source = self.route(session, source=UNSEEN_SOURCE)
        destination = self.route(session, destination=UNSEEN_DISPLAY)
        self.assertEqual(source.status_code, 403)
        self.assertEqual(destination.status_code, 403)
        self.assertEqual(source.get_json()["reason"], "source_not_permitted")
        self.assertEqual(destination.get_json()["reason"],
                         "destination_not_permitted")

    def test_every_reason_carries_a_sentence_rather_than_a_token(self):
        response = self.route(self.session_for(self.teacher), source=UNSEEN_SOURCE)
        error = response.get_json()["error"]
        self.assertNotEqual(error, response.get_json()["reason"],
                            "the internal name reached the operator")
        self.assertIn("access", error.lower())

    def test_a_missing_feature_is_distinguishable_from_a_missing_grant(self):
        self.store.set_permissions(
            self.teacher.id,
            Permissions(features=(), sources=[TEACHER_PC],
                        destinations=[FRONT_DISPLAY]),
            actor=self.admin)
        response = self.route(self.session_for(self.teacher))
        self.assertEqual(response.get_json()["reason"], "missing_feature:av")


class TheHandlerChecksAgainBehindTheGateTests(UserRoutingTestBase):
    """The endpoint's own authorisation, reached on purpose.

    The gate inspects `destination` in the request body and refuses before the
    handler runs, so the handler's identical check is never reached by the
    ordinary path. That does not make it redundant -- it makes it untested,
    which is worse, because it is the check that still applies if the body ever
    stops carrying a key the gate recognises.

    So the first line is taken out of the way for the span of one test, and the
    precondition is asserted: if emptying the gate's key list stopped working,
    this test would say so rather than quietly measuring the gate again.
    """

    def setUp(self):
        super().setUp()
        self.gate_keys = omni_gate.DESTINATION_KEYS
        self.addCleanup(
            lambda: setattr(omni_gate, "DESTINATION_KEYS", self.gate_keys))

    def blind_the_gate(self):
        """Make the gate unable to see the destination in the body."""
        omni_gate.DESTINATION_KEYS = ()

    def test_the_gate_really_is_what_refuses_first(self):
        """The precondition, asserted rather than assumed."""
        response = self.post("/api/user/unroute",
                             {"destination": UNSEEN_DISPLAY},
                             self.session_for(self.teacher))
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json()["reason"],
                         "destination_not_permitted")

    def test_unroute_refuses_an_unheld_display_on_its_own(self):
        self.blind_the_gate()
        response = self.post("/api/user/unroute",
                             {"destination": UNSEEN_DISPLAY},
                             self.session_for(self.teacher))
        self.assertEqual(response.status_code, 403,
                         "the handler let a display through that the gate "
                         "could no longer see")
        self.assertEqual(response.get_json()["reason"],
                         "destination_not_permitted")
        self.assertEqual(self.unroutes, [],
                         "a refused unroute still reached the hardware")

    def test_route_refuses_an_unheld_display_on_its_own(self):
        self.blind_the_gate()
        response = self.route(self.session_for(self.teacher),
                              destination=UNSEEN_DISPLAY)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json()["reason"],
                         "destination_not_permitted")
        self.assertEqual(self.routes, [])


class AMultiviewRefusalNamesItselfTests(UserRoutingTestBase):
    """The 403 that started this, and the only one the gate cannot make.

    Preset approval is not visible in the request body, so these refusals come
    from the endpoint rather than the gate -- which makes them the ones whose
    reason the ordinary path actually observes. "A User granted Multiview gets
    403" was unanswerable precisely because this was the generic message.
    """

    def setUp(self):
        super().setUp()
        self.store.set_permissions(
            self.teacher.id,
            Permissions(features=("av", "multiview"),
                        sources=[TEACHER_PC, DOC_CAMERA],
                        destinations=[FRONT_DISPLAY],
                        multiviews=[FRONT_DISPLAY + "/approved"]),
            actor=self.admin)
        self.teacher = self.store.find_by_username("teacher")

        self.mv_state = {
            "ok": True,
            "multiviews": [
                {"name": "approved", "layout_label": "2x2",
                 "subframes": [{"cell": "top_left",
                                "source": {"ip": "192.0.2.11"}}]},
                {"name": "unapproved", "layout_label": "2x2",
                 "subframes": [{"cell": "top_left",
                                "source": {"ip": "192.0.2.11"}}]},
            ],
        }
        self._patch_registered("multiview_state", lambda _ip: self.mv_state)

    def show(self, preset):
        return self.post("/api/user/multiview_show",
                         {"display": FRONT_DISPLAY, "preset": preset},
                         self.session_for(self.teacher))

    def test_an_unapproved_preset_says_so(self):
        response = self.show("unapproved")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json()["reason"],
                         "multiview_not_approved")

    def test_a_preset_holding_an_unpermitted_source_says_which_problem_it_is(self):
        """The bench reproduction, in a test.

        The account holds the display and the preset; one of the preset's
        windows is fed by a source it was never given. That is a different
        problem from the preset not being approved, and the operator can only
        act on it if the two are told apart.
        """
        self.mv_state["multiviews"][0]["subframes"] = [
            {"cell": "top_left", "source": {"ip": "192.0.2.19"}},
        ]
        response = self.show("approved")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json()["reason"],
                         "multiview_content_not_permitted")
        self.assertIn("source", response.get_json()["error"].lower())

    def test_an_approved_preset_of_permitted_sources_is_not_refused(self):
        """The other half: the refusals must not be refusing everything."""
        response = self.show("approved")
        self.assertNotEqual(response.status_code, 403,
                            "a fully permitted preset was refused")

    def test_a_source_that_cannot_be_identified_fails_closed(self):
        """An address this host cannot place is not 'no source'.

        An empty window is ordinary and carries no source at all. A window that
        names an address resolving to no known device is a different thing, and
        treating it as nothing to authorise let an undiscovered or re-addressed
        encoder through unchecked.
        """
        self.mv_state["multiviews"][0]["subframes"] = [
            {"cell": "top_left", "source": {"ip": "198.18.0.7"}},
        ]
        response = self.show("approved")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json()["reason"],
                         "multiview_content_not_permitted")

    def test_an_empty_window_is_not_treated_as_an_unknown_source(self):
        """The decoder represents an unassigned window as `input: ""`.

        Failing closed on an unidentifiable source must not fail closed on a
        window that legitimately has none, or half the saved layouts on a
        decoder become unreachable.
        """
        self.mv_state["multiviews"][0]["subframes"] = [
            {"cell": "top_left", "source": {"ip": ""}},
            {"cell": "top_right", "source": {}},
        ]
        response = self.show("approved")
        self.assertNotEqual(response.status_code, 403,
                            "an empty window was read as an unknown source")


class UnrouteIsHeldToTheSameContractAsRouteTests(UserRoutingTestBase):
    """Unroute takes the same lock and reports the same refusals as a route.

    It reaches the same hardware in the same way, so "somebody else is
    changing this display" has to mean the same thing and be told apart from a
    composition in the same way. Written out rather than assumed from the two
    handlers looking alike: they are separate code and can drift.
    """

    def unroute(self, session=None, destination=FRONT_DISPLAY):
        return self.post("/api/user/unroute", {"destination": destination},
                         session or self.session_for(self.teacher))

    def test_a_busy_display_says_it_is_busy_rather_than_compositing(self):
        self._patch_registered("locks", _BusyLocks())
        busy = self.unroute()
        self.assertEqual(busy.status_code, 409)
        self.assertEqual(busy.get_json()["reason"], "resource_busy",
                         "a contended lock was reported as something else")

    def test_a_busy_refusal_reaches_no_device(self):
        self._patch_registered("locks", _BusyLocks())
        self.unroute()
        self.assertEqual(self.unroutes, [],
                         "a request refused for the lock still reached the "
                         "unroute engine")

    def test_the_display_is_the_resource_that_is_held(self):
        """The lock is per display, so two displays do not queue on each other.

        Asserted by holding the display's own lock and watching the request
        refuse: a handler that locked nothing, or locked something else, would
        sail through.
        """
        held = []

        class _Recording:
            @staticmethod
            def decoder(identity):
                return "decoder:" + str(identity or "").lower()

            @staticmethod
            def usb_pair(a, b):
                return ("usb:" + str(a), "usb:" + str(b))

            def hold(self, *resources, timeout=None):
                held.extend(resources)
                raise omni_locks.ResourceBusy(
                    resources[0] if resources else "resource")

        self._patch_registered("locks", _Recording())
        self.unroute()
        self.assertEqual(held, ["decoder:" + FRONT_DISPLAY],
                         "unroute did not serialise on the display it changes")


class UnrouteLeavesAMultiviewProperlyTests(unittest.TestCase):
    """Unroute goes through the verified teardown, not around it.

    A display showing a Multiview has to be taken out of it before it can be
    told to subscribe to nothing, and that teardown is the one that moves the
    output first and releases only the inputs OmniSuite owns. Skipping it would
    leave the output pointed at an object nothing is feeding.

    This drives the engine endpoint itself rather than the restricted wrapper,
    because the teardown is the engine's job.
    """

    def setUp(self):
        import OmniMatrix_upgrade_server_v7_6y as srv
        self.srv = srv
        self.calls = []

        self.saved = {name: getattr(srv, name) for name in
                      ("_exit_active_multiview", "_decoder_state",
                       "_load_cache", "_save_cache")}

        def restore():
            for name, value in self.saved.items():
                setattr(srv, name, value)

        self.addCleanup(restore)

        srv._decoder_state = lambda ip: (
            {"video_input": "multiview2x2"}, None)
        srv._load_cache = lambda: [
            {"ip": "192.0.2.21", "mac": "b8:98:b0:00:00:11",
             "role": "decoder", "model": "hw-omni-d4511"}]
        srv._save_cache = lambda units: None

        def fake_exit(decoder, name, state):
            self.calls.append((decoder, name))
            return True, ["moved output"], ["ip_input2"], ""

        srv._exit_active_multiview = fake_exit
        self.client = srv.app.test_client()

    def test_unrouting_a_compositing_display_uses_the_verified_teardown(self):
        self.client.post("/api/unroute", json={"decoder": "192.0.2.21"})
        self.assertEqual(self.calls, [("192.0.2.21", "multiview2x2")],
                         "unroute did not take the display out of its "
                         "Multiview through the verified teardown")

    def test_a_teardown_that_fails_stops_the_unroute(self):
        """Nothing is cleared while the output still points at the Multiview."""
        self.srv._exit_active_multiview = (
            lambda d, n, s: (False, [], [], "the output would not move"))
        response = self.client.post("/api/unroute",
                                    json={"decoder": "192.0.2.21"})
        body = response.get_json()
        self.assertFalse(body["ok"])
        self.assertEqual(body["status"], "FAILED — MULTIVIEW NOT EXITED")

    def test_a_display_showing_nothing_needs_no_teardown(self):
        self.srv._decoder_state = lambda ip: ({"video_input": "ip_input1"},
                                              None)
        self.client.post("/api/unroute", json={"decoder": "192.0.2.21"})
        self.assertEqual(self.calls, [],
                         "a display that was not compositing was put through "
                         "a Multiview teardown anyway")
