"""One operator action, one audit entry.

Every account-management action used to be recorded twice: the store knew what
changed and to whom but not who did it, and the API knew who but named the
event after the verb in the request body and identified the account by an
opaque id. Two half-entries where one whole one was wanted, and an Activity
view that read as though everything had happened twice.

The store keeps the event and now receives the actor. These tests hold that,
and hold that an internal caller still produces a record -- because removing
coverage for a legitimate path would be a worse defect than the duplicate.
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

import omni_remote                                      # noqa: E402
import omni_users                                       # noqa: E402
from omni_users import Permissions, UserStore           # noqa: E402

ACCOUNT_EVENTS = ("user.create", "user.enabled", "user.disabled",
                  "user.deleted", "user.role_changed",
                  "user.permissions_changed", "user.password_changed",
                  "bootstrap.admin_created")


class OneActionOneEntryTests(unittest.TestCase):
    """Driven through the HTTP surface, because that is where the duplicate was."""

    def setUp(self):
        import OmniMatrix_upgrade_server_v7_6y as srv
        self.srv = srv
        self.dir = Path(tempfile.mkdtemp(prefix="omni-audit-one-"))
        # The API captured this object when it registered its routes, so the
        # module attribute cannot be substituted. Redirect this one's files and
        # put back everything it held.
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


        # The gate refuses every remote request while remote access is off,
        # which is right and is not what these tests are about. `running` is
        # "the listener has a server", so a placeholder says it is up without
        # opening a socket.
        listener = srv._remote_listener
        previous_server = listener._server
        self.addCleanup(lambda: setattr(listener, "_server", previous_server))
        listener._server = object()

        self.client = srv.app.test_client()
        # Local: creating the first administrator is local-only, and that is
        # deliberate. It therefore records no actor, because there is no
        # account behind a local request.
        self.client.post("/api/bootstrap/admin", json={
            "username": "principal", "password": "principal-pass-1",
            "confirm": "principal-pass-1"})
        self.principal = self.store.find_by_username("principal")
        self.session = self.store.open_session(self.principal.id)

    def admin(self, path, payload):
        """An administrator acting from a browser on the network.

        Everything after bootstrap goes through here, because an actor only
        exists when a request carries a session.
        """
        # The cookie goes in the client's jar: setting HTTP_COOKIE through
        # environ_base is silently discarded, because the client rebuilds that
        # header from the jar on every request.
        self.client.set_cookie("omnisuite_session", self.session.token)
        return self.client.post(
            path, json=payload,
            environ_base={omni_remote.LISTENER_KEY: omni_remote.REMOTE},
            headers={"X-OmniSuite-CSRF": self.session.csrf_token})

    def _account_entries(self):
        return [e for e in self.store.audit_entries(500)
                if e["event"].startswith(("user.", "bootstrap."))]

    def _since(self, before):
        return self._account_entries()[:len(self._account_entries()) - before]

    def _create_teacher(self):
        response = self.admin("/api/admin/users", {
            "username": "teacher", "password": "teacher-password-1",
            "role": "user"})
        self.assertEqual(response.status_code, 200)
        return self.store.find_by_username("teacher")

    def _act(self, user_id, body):
        before = len(self._account_entries())
        response = self.admin("/api/admin/users/" + user_id, body)
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        return self._account_entries()[:len(self._account_entries()) - before]

    # ---- one entry each ---------------------------------------------
    def test_bootstrap_records_the_specific_event_and_not_both(self):
        entries = self._account_entries()
        self.assertEqual([e["event"] for e in entries], ["bootstrap.admin_created"])

    def test_the_local_operator_is_recorded_as_having_no_account(self):
        """Not a gap: a local request is trusted by its socket, not a sign-in.

        Writing an actor there would be inventing one.
        """
        entry = self._account_entries()[0]
        self.assertIsNone(entry["actor"])
        self.assertIsNone(entry["actor_id"])
        self.assertEqual(entry["detail"]["username"], "principal")

    def test_creating_an_account_records_one_entry(self):
        before = len(self._account_entries())
        self._create_teacher()
        new = self._account_entries()[:len(self._account_entries()) - before]
        self.assertEqual([e["event"] for e in new], ["user.create"])

    def test_disabling_records_one_entry_that_says_disabled(self):
        teacher = self._create_teacher()
        new = self._act(teacher.id, {"action": "set_enabled", "enabled": False})
        self.assertEqual([e["event"] for e in new], ["user.disabled"])

    def test_enabling_records_one_entry_that_says_enabled(self):
        teacher = self._create_teacher()
        self._act(teacher.id, {"action": "set_enabled", "enabled": False})
        new = self._act(teacher.id, {"action": "set_enabled", "enabled": True})
        self.assertEqual([e["event"] for e in new], ["user.enabled"])

    def test_a_role_change_records_one_entry(self):
        teacher = self._create_teacher()
        new = self._act(teacher.id, {"action": "set_role", "role": "admin"})
        self.assertEqual([e["event"] for e in new], ["user.role_changed"])

    def test_a_password_reset_records_one_entry(self):
        teacher = self._create_teacher()
        new = self._act(teacher.id, {"action": "set_password",
                                     "password": "a-new-password-1"})
        self.assertEqual([e["event"] for e in new], ["user.password_changed"])

    def test_a_permission_change_records_one_entry(self):
        teacher = self._create_teacher()
        new = self._act(teacher.id, {"action": "set_permissions",
                                     "permissions": {"features": ["av"]}})
        self.assertEqual([e["event"] for e in new], ["user.permissions_changed"])

    def test_deleting_records_one_entry(self):
        teacher = self._create_teacher()
        new = self._act(teacher.id, {"action": "delete"})
        self.assertEqual([e["event"] for e in new], ["user.deleted"])

    def test_no_event_is_named_after_the_verb_in_the_request(self):
        """"set_enabled" is what the API was asked to do, not what happened."""
        teacher = self._create_teacher()
        for body in ({"action": "set_enabled", "enabled": False},
                     {"action": "set_role", "role": "admin"},
                     {"action": "set_password", "password": "another-pass-1"},
                     {"action": "set_permissions", "permissions": {}}):
            self.admin("/api/admin/users/" + teacher.id, body)
        events = {e["event"] for e in self._account_entries()}
        for verb in ("user.set_enabled", "user.set_role", "user.set_password",
                     "user.set_permissions", "user.delete"):
            self.assertNotIn(verb, events)

    # ---- the entry answers the five questions ------------------------
    def test_the_single_entry_says_who_what_which_and_when(self):
        teacher = self._create_teacher()
        entry = self._act(teacher.id,
                          {"action": "set_enabled", "enabled": False})[0]
        self.assertEqual(entry["event"], "user.disabled")          # what
        self.assertEqual(entry["actor"], "principal")              # who
        self.assertEqual(entry["detail"]["username"], "teacher")   # which
        self.assertIsInstance(entry["at"], float)                  # when
        self.assertIsNotNone(entry["actor_id"])

    def test_creating_records_the_administrator_who_did_it(self):
        before = len(self._account_entries())
        self._create_teacher()
        entry = self._account_entries()[:len(self._account_entries()) - before][0]
        self.assertEqual(entry["actor"], "principal")
        self.assertEqual(entry["detail"]["username"], "teacher")
        self.assertEqual(entry["detail"]["role"], "user")

    def test_a_refused_action_records_nothing(self):
        """Result: only what happened is recorded."""
        principal = self.store.find_by_username("principal")
        before = len(self._account_entries())
        response = self.admin("/api/admin/users/" + principal.id,
                              {"action": "set_enabled", "enabled": False})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(len(self._account_entries()), before)

    def test_an_unknown_action_records_nothing(self):
        teacher = self._create_teacher()
        before = len(self._account_entries())
        response = self.admin("/api/admin/users/" + teacher.id,
                              {"action": "make_coffee"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(len(self._account_entries()), before)

    def test_no_password_reaches_the_log(self):
        canary = "canary-account-audit-3"
        self.admin("/api/admin/users", {
            "username": "victim", "password": canary, "role": "user"})
        victim = self.store.find_by_username("victim")
        self.admin("/api/admin/users/" + victim.id,
                   {"action": "set_password", "password": canary})
        self.store.flush_audit()
        self.assertNotIn(canary, json.dumps(self.store.audit_entries(500)))
        self.assertNotIn(canary, (self.dir / "audit.json").read_text(encoding="utf-8"))


class InternalCallersStillRecordTests(unittest.TestCase):
    """Coverage must not be lost for a path that does not go through the API."""

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="omni-audit-internal-"))
        self.store = UserStore(self.dir / "users.json", self.dir / "audit.json")

    def test_a_direct_store_call_is_still_recorded_with_no_actor(self):
        self.store.create("teacher", "teacher-password-1", omni_users.ROLE_USER)
        teacher = self.store.find_by_username("teacher")
        self.store.set_enabled(teacher.id, False)
        events = [(e["event"], e["actor"]) for e in self.store.audit_entries(50)]
        self.assertIn(("user.disabled", None), events)
        self.assertIn(("user.create", None), events)

    def test_every_account_change_still_produces_exactly_one_record(self):
        # A second administrator, so promoting the teacher does not make them
        # the only one -- OmniSuite refuses to disable the last administrator,
        # and that refusal would look here like a missing audit entry.
        self.store.create("keeper", "keeper-password-1", omni_users.ROLE_ADMIN)
        self.store.create("teacher", "teacher-password-1", omni_users.ROLE_USER)
        teacher = self.store.find_by_username("teacher")
        self.store.set_role(teacher.id, omni_users.ROLE_ADMIN)
        self.store.set_permissions(teacher.id, Permissions(features=["av"]))
        self.store.set_password(teacher.id, "another-password-1")
        self.store.set_enabled(teacher.id, False)
        # Only the teacher's own entries: the second administrator above was
        # also created, and counting both would make user.create look doubled.
        events = [e["event"] for e in self.store.audit_entries(50)
                  if (e["detail"] or {}).get("username") == "teacher"]
        for event in ("user.create", "user.role_changed",
                      "user.permissions_changed", "user.password_changed",
                      "user.disabled"):
            with self.subTest(event=event):
                self.assertEqual(events.count(event), 1)

    def test_suppression_is_explicit_and_only_bootstrap_uses_it(self):
        self.store.create("first", "first-password-1", omni_users.ROLE_ADMIN,
                          audit=False)
        self.assertEqual(self.store.audit_entries(50), [])
        source = (ROOT / "omni_remote_api.py").read_text(encoding="utf-8")
        self.assertEqual(source.count("audit=False"), 1,
                         "only the bootstrap path may suppress the store event")


class ActivityLabelsFollowTheEventsTests(unittest.TestCase):
    """The events that no longer exist must not linger in the label map."""

    def test_the_retired_verb_events_are_gone_from_the_ui(self):
        labels = (ROOT / "ui" / "admin" / "users.js").read_text(encoding="utf-8")
        start = labels.index("const EVENT_LABELS = {")
        block = labels[start:labels.index("};", start)]
        for retired in ("user.set_enabled", "user.set_role", "user.set_password",
                        "user.set_permissions", "user.delete"):
            with self.subTest(retired=retired):
                self.assertNotIn("'%s'" % retired, block)

    def test_every_surviving_account_event_still_has_a_label(self):
        labels = (ROOT / "ui" / "admin" / "users.js").read_text(encoding="utf-8")
        start = labels.index("const EVENT_LABELS = {")
        block = labels[start:labels.index("};", start)]
        for event in ACCOUNT_EVENTS:
            with self.subTest(event=event):
                self.assertIn("'%s'" % event, block)


if __name__ == "__main__":
    unittest.main()
