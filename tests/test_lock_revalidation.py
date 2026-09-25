"""Authorisation on the far side of the lock wait.

A lock is a wait. Everything decided before it describes a world that may have
moved on -- an administrator can disable an account, take a display away or
change a permission while a request sits in the queue -- and the far side of
that wait is the last moment before a device is written to.

These drive the real registered handlers, not the gate in isolation, because
the ordering of "check, audit, wait, write" is a property of the handler.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("OMNI_SMOKE", "1")

import omni_locks                                       # noqa: E402
import omni_remote                                      # noqa: E402
import omni_users                                       # noqa: E402
from omni_users import Permissions                      # noqa: E402

CAMERA = "0A:1B:2C:00:00:01"
DISPLAY = "0A:1B:2C:00:00:11"
OTHER_DISPLAY = "0A:1B:2C:00:00:12"

UNITS = [
    {"ip": "198.51.100.21", "mac": CAMERA, "hostname": "enc-camera",
     "role": "encoder", "model": "hw-omni-e4521"},
    {"ip": "198.51.100.11", "mac": DISPLAY, "hostname": "dec-front",
     "role": "decoder", "model": "hw-omni-d4511"},
    {"ip": "198.51.100.12", "mac": OTHER_DISPLAY, "hostname": "dec-back",
     "role": "decoder", "model": "hw-omni-d4511"},
]


class LockRevalidationBase(unittest.TestCase):
    def setUp(self):
        import OmniMatrix_upgrade_server_v7_6y as srv
        self.srv = srv
        self.dir = Path(tempfile.mkdtemp(prefix="omni-relock-"))

        # The API captured the store when it registered its routes, so the
        # module attribute cannot be substituted. Redirect this one's files.
        self.store = srv._user_store
        saved = (self.store._path, self.store._audit_path,
                 dict(self.store._accounts), dict(self.store._sessions),
                 list(self.store._audit))

        def restore():
            self.store._path, self.store._audit_path = saved[0], saved[1]
            self.store._accounts.clear()
            self.store._accounts.update(saved[2])
            self.store._sessions.clear()
            self.store._sessions.update(saved[3])
            self.store._audit[:] = saved[4]

        self.addCleanup(restore)
        self.store._path = self.dir / "users.json"
        self.store._audit_path = self.dir / "audit.json"
        self.store._accounts.clear()
        self.store._sessions.clear()
        self.store._audit.clear()

        # Synthetic devices, so identity resolution succeeds without the
        # cache on this machine taking part.
        #
        # The handler captured `load_cache` when it registered its routes, so
        # substituting the module attribute would change nothing it can see.
        # `_load_cache` reads the CACHE path at call time, so the file is what
        # gets redirected -- and the operator's real cache is left alone.
        for name in ("CACHE", "SCAN_RESULTS", "CSV_VIEW"):
            original = getattr(srv, name)
            self.addCleanup(lambda n=name, o=original: setattr(srv, n, o))
            setattr(srv, name, self.dir / original.name)
        (self.dir / srv.CACHE.name).write_text(json.dumps(UNITS),
                                               encoding="utf-8")

        self.store.create("teacher", "teacher-password-1", omni_users.ROLE_USER,
                          Permissions(features=("av", "usb", "multiview"),
                                      sources=(CAMERA,),
                                      destinations=(DISPLAY,)))
        self.teacher = self.store.find_by_username("teacher")
        self.session = self.store.open_session(self.teacher.id)

        # The gate refuses every remote request while remote access is off,
        # which is right and is not what these tests are about. `running` is
        # "the listener has a server", so a placeholder says it is up without
        # opening a socket.
        listener = srv._remote_listener
        previous_server = listener._server
        self.addCleanup(lambda: setattr(listener, "_server", previous_server))
        listener._server = object()

        self.client = srv.app.test_client()

    def remote(self, path, payload):
        # The cookie goes in the client's jar: setting HTTP_COOKIE through
        # environ_base is silently discarded, because the client rebuilds that
        # header from the jar on every request.
        self.client.set_cookie("omnisuite_session", self.session.token)
        return self.client.post(
            path, json=payload,
            environ_base={omni_remote.LISTENER_KEY: omni_remote.REMOTE},
            headers={"X-OmniSuite-CSRF": self.session.csrf_token})

    def while_blocked(self, path, payload, during):
        """Issue a request that must queue behind a held lock.

        The lock is taken here, the request is started, `during` runs while it
        is waiting, and only then is the lock released. The worker is joined
        before the test ends: work that outlives its test lands inside
        somebody else's.
        """
        outcome = {}
        started = threading.Event()
        release = threading.Event()
        held = threading.Event()

        def holder():
            with self.srv._resource_locks.hold(
                    self.srv._resource_locks.decoder(DISPLAY)):
                held.set()
                release.wait(30)

        def caller():
            started.set()
            response = self.remote(path, payload)
            outcome["status"] = response.status_code
            outcome["body"] = response.get_json() or {}

        holder_thread = threading.Thread(target=holder)
        holder_thread.start()
        self.assertTrue(held.wait(10), "the lock was never taken")

        worker = threading.Thread(target=caller)
        worker.start()
        self.assertTrue(started.wait(10))
        # Long enough for the request to reach the lock and queue on it.
        worker.join(timeout=1.5)
        if not worker.is_alive():
            # Name the precondition that broke rather than the behaviour under
            # test: a request refused before it reached the lock says nothing
            # about revalidation, and reads like a failure of the feature.
            release.set()
            holder_thread.join(timeout=10)
            self.fail("the request finished without queueing on the held lock "
                      "(HTTP %s, %r) -- it never reached the wait, so this "
                      "test proves nothing"
                      % (outcome.get("status"), outcome.get("body")))

        during()

        release.set()
        holder_thread.join(timeout=30)
        worker.join(timeout=60)
        self.assertFalse(worker.is_alive(), "the request never completed")
        return outcome


class PermissionRevokedWhileWaitingTests(LockRevalidationBase):
    def test_a_display_taken_away_during_the_wait_is_not_routed(self):
        def revoke():
            # Directly on the account, so the session survives and what is
            # being tested is the permission check rather than sign-out.
            self.teacher.permissions = Permissions(
                features=("av",), sources=(CAMERA,),
                destinations=(OTHER_DISPLAY,))

        outcome = self.while_blocked(
            "/api/user/route", {"source": CAMERA, "destination": DISPLAY},
            revoke)
        self.assertEqual(outcome["status"], 403)

    def test_a_capability_withdrawn_during_the_wait_is_not_routed(self):
        def revoke():
            self.teacher.permissions = Permissions(
                features=("usb",), sources=(CAMERA,), destinations=(DISPLAY,))

        outcome = self.while_blocked(
            "/api/user/route", {"source": CAMERA, "destination": DISPLAY},
            revoke)
        self.assertEqual(outcome["status"], 403)

    def test_a_source_taken_away_during_the_wait_is_not_routed(self):
        def revoke():
            self.teacher.permissions = Permissions(
                features=("av",), sources=(), destinations=(DISPLAY,))

        outcome = self.while_blocked(
            "/api/user/route", {"source": CAMERA, "destination": DISPLAY},
            revoke)
        self.assertEqual(outcome["status"], 403)

    def test_an_account_disabled_during_the_wait_is_signed_out(self):
        def disable():
            self.store.set_enabled(self.teacher.id, False)

        outcome = self.while_blocked(
            "/api/user/route", {"source": CAMERA, "destination": DISPLAY},
            disable)
        self.assertEqual(outcome["status"], 401)

    def test_a_password_change_during_the_wait_signs_the_request_out(self):
        def reset():
            self.store.set_password(self.teacher.id, "a-different-password-1")

        outcome = self.while_blocked(
            "/api/user/route", {"source": CAMERA, "destination": DISPLAY},
            reset)
        self.assertEqual(outcome["status"], 401)

    def test_nothing_revoked_means_the_request_is_not_refused(self):
        """The control. Without it the tests above pass for the wrong reason.

        The device write itself cannot succeed here -- the hardware fence
        blocks every non-loopback transport -- so this asserts only that the
        request was not turned away by authorisation.
        """
        outcome = self.while_blocked(
            "/api/user/route", {"source": CAMERA, "destination": DISPLAY},
            lambda: None)
        self.assertNotIn(outcome["status"], (401, 403))

    def test_a_refusal_after_the_wait_is_recorded_as_a_refusal(self):
        def revoke():
            self.teacher.permissions = Permissions(
                features=("av",), sources=(CAMERA,), destinations=())

        self.while_blocked("/api/user/route",
                           {"source": CAMERA, "destination": DISPLAY}, revoke)
        events = [e["event"] for e in self.store.audit_entries(50)]
        self.assertIn("access.denied", events)

    def test_a_route_that_never_happened_is_not_recorded_as_one(self):
        """The audit entry moved inside the lock for this reason."""
        def revoke():
            self.teacher.permissions = Permissions(
                features=("av",), sources=(CAMERA,), destinations=())

        self.while_blocked("/api/user/route",
                           {"source": CAMERA, "destination": DISPLAY}, revoke)
        events = [e["event"] for e in self.store.audit_entries(50)]
        self.assertNotIn("route", events)


class MultiviewRefusesBeforeTakingALockTests(LockRevalidationBase):
    """A display that cannot be reached costs no lock and no wait.

    The end-to-end revalidation for Multiview is not testable here: its gate
    reads the decoder's own Multiview state first, a fenced test cannot reach a
    decoder, and the request is refused before it ever queues. That path is
    covered instead by `EveryOperationRevalidatesTests`, which holds that the
    recheck is inside the lock, and by the `relock.multiview-no-recheck`
    mutation, which removes it and must be caught.

    What is testable, and worth testing, is that none of this takes a lock on
    the way to failing -- so a display nobody can reach cannot be used to block
    one that somebody can.
    """

    def setUp(self):
        super().setUp()
        key = omni_users.multiview_key(DISPLAY, "ClassroomFour")
        self.teacher.permissions = Permissions(
            features=("multiview", "av"), sources=(CAMERA,),
            destinations=(DISPLAY,), multiviews=(key,))

    def test_an_unreachable_display_is_reported_as_unavailable(self):
        response = self.remote("/api/user/multiview_show",
                               {"display": DISPLAY, "preset": "ClassroomFour"})
        self.assertEqual(response.status_code, 502)
        self.assertIn("unavailable", (response.get_json() or {}).get("error", ""))

    def test_and_it_holds_no_lock_afterwards(self):
        self.remote("/api/user/multiview_show",
                    {"display": DISPLAY, "preset": "ClassroomFour"})
        self.remote("/api/user/multiview_switch",
                    {"display": DISPLAY, "preset": "ClassroomFour",
                     "window": "top_left", "source": CAMERA})
        self.assertEqual(self.srv._resource_locks.busy(), [])

    def test_an_unapproved_preset_is_refused_without_reaching_the_device(self):
        """The approval check comes first, so an unapproved preset is a 403."""
        self.teacher.permissions = Permissions(
            features=("multiview",), sources=(CAMERA,),
            destinations=(DISPLAY,), multiviews=())
        response = self.remote("/api/user/multiview_show",
                               {"display": DISPLAY, "preset": "ClassroomFour"})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.srv._resource_locks.busy(), [])


class EveryOperationRevalidatesTests(unittest.TestCase):
    """A structural check, so a fifth operation cannot be added without one."""

    OPERATIONS = ("api_user_route", "api_user_usb_route",
                  "api_user_multiview_show", "api_user_multiview_switch")

    def test_each_user_operation_rechecks_inside_its_lock(self):
        source = (ROOT / "omni_remote_api.py").read_text(encoding="utf-8")
        for name in self.OPERATIONS:
            with self.subTest(operation=name):
                start = source.index("def %s(" % name)
                end = source.index("\n    def ", start + 10)
                body = source[start:end]
                self.assertIn("locks.hold(", body)
                held = body[body.index("locks.hold("):]
                self.assertTrue(
                    "_revalidate(" in held or "_revalidate_multiview(" in held,
                    "%s writes to a device after a wait without asking again "
                    "whether the caller is still allowed" % name)

    def test_the_audit_entry_is_written_inside_the_lock(self):
        """Recording an operation before waiting records one that may not happen."""
        source = (ROOT / "omni_remote_api.py").read_text(encoding="utf-8")
        for name in self.OPERATIONS:
            with self.subTest(operation=name):
                start = source.index("def %s(" % name)
                end = source.index("\n    def ", start + 10)
                body = source[start:end]
                before, held = body.split("locks.hold(", 1)
                self.assertNotIn("store.audit(", before,
                                 "%s records the event before the wait" % name)
                self.assertIn("store.audit(", held)


if __name__ == "__main__":
    unittest.main()
