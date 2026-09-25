"""Authentication and audit, attacked rather than demonstrated.

No claim is made here about resistance to sophisticated timing analysis. What
is asserted is that the obvious differences are absent: an unknown username and
a wrong password produce the same answer, and both do the work of a
verification.
"""
from __future__ import annotations

import os
import sys
import time
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("OMNI_SMOKE", "1")

import omni_users                                    # noqa: E402
from omni_users import UserStore                     # noqa: E402


class LoginHardeningTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="omni-login-"))
        self.store = UserStore(self.dir / "users.json", self.dir / "audit.json")
        self.store.create("teacher", "teacher-password-1", omni_users.ROLE_USER)
        self.teacher = self.store.find_by_username("teacher")

    # ---- enumeration -------------------------------------------------
    def test_an_unknown_account_and_a_wrong_password_answer_identically(self):
        unknown = self.store.authenticate("nobody-here", "whatever")
        wrong = self.store.authenticate("teacher", "wrong-password")
        self.assertEqual(unknown["error"], wrong["error"])
        self.assertEqual(set(unknown), set(wrong))

    def test_a_disabled_account_does_not_announce_that_it_exists(self):
        self.store.set_enabled(self.teacher.id, False)
        disabled = self.store.authenticate("teacher", "teacher-password-1")
        unknown = self.store.authenticate("nobody-here", "teacher-password-1")
        self.assertEqual(disabled["error"], unknown["error"])

    def test_an_unknown_account_still_costs_a_verification(self):
        """So a name that does not exist is not obviously faster.

        Asserted against the code rather than by timing: a measurement here
        would be flaky on a loaded machine and would prove less than the code
        path does.
        """
        source = (ROOT / "omni_users.py").read_text(encoding="utf-8")
        body = source[source.index("def authenticate"):]
        body = body[:body.index("\n    def ")]
        self.assertIn("hash_password(secrets.token_urlsafe", body,
                      "an unknown username must still do the work")

    # ---- throttling --------------------------------------------------
    def test_failures_lock_the_pair_and_a_correct_password_does_not_open_it(self):
        for _ in range(omni_users.MAX_FAILURES):
            self.store.authenticate("teacher", "wrong", source="10.0.0.9")
        result = self.store.authenticate("teacher", "teacher-password-1",
                                         source="10.0.0.9")
        self.assertFalse(result["ok"])
        self.assertTrue(result.get("throttled"))

    def test_throttling_is_per_source_so_one_client_cannot_lock_everyone_out(self):
        for _ in range(omni_users.MAX_FAILURES * 2):
            self.store.authenticate("teacher", "wrong", source="10.0.0.9")
        self.assertTrue(self.store.authenticate(
            "teacher", "teacher-password-1", source="10.0.0.10")["ok"])

    def test_throttling_is_per_account_so_one_does_not_lock_another(self):
        self.store.create("other", "other-password-1", omni_users.ROLE_USER)
        for _ in range(omni_users.MAX_FAILURES * 2):
            self.store.authenticate("teacher", "wrong", source="10.0.0.9")
        self.assertTrue(self.store.authenticate(
            "other", "other-password-1", source="10.0.0.9")["ok"])

    def test_a_throttled_attempt_is_recorded(self):
        for _ in range(omni_users.MAX_FAILURES + 1):
            self.store.authenticate("teacher", "wrong", source="10.0.0.9")
        events = [e["event"] for e in self.store.audit_entries(50)]
        self.assertIn("login.throttled", events)
        self.assertIn("login.failure", events)

    def test_failed_logins_cannot_grow_the_log_without_bound(self):
        """An attacker can produce these at will; the disk must not fill."""
        for index in range(omni_users.AUDIT_MAX_ENTRIES + 200):
            self.store.audit("login.failure", detail={"n": index})
        self.assertLessEqual(len(self.store.audit_entries(10 ** 6)),
                             omni_users.AUDIT_MAX_ENTRIES)

    # ---- sessions ----------------------------------------------------
    def test_signing_in_always_mints_a_new_token(self):
        """Session fixation: a token can be neither chosen nor reused."""
        tokens = set()
        for _ in range(5):
            result = self.store.authenticate("teacher", "teacher-password-1")
            tokens.add(result["token"])
            tokens.add(result["csrf_token"])
        self.assertEqual(len(tokens), 10)

    def test_a_token_is_long_and_opaque(self):
        result = self.store.authenticate("teacher", "teacher-password-1")
        self.assertGreaterEqual(len(result["token"]), 32)
        self.assertNotIn("teacher", result["token"])

    def test_a_malformed_cookie_is_simply_not_a_session(self):
        for rubbish in ("", "   ", "not-a-token", "a" * 500,
                        "../../etc/passwd", "<script>", "\x00\x01"):
            with self.subTest(cookie=rubbish[:20]):
                account, _session = self.store.resolve_session(rubbish)
                self.assertIsNone(account)

    def test_changing_a_password_ends_every_session_for_that_account(self):
        first = self.store.authenticate("teacher", "teacher-password-1")["token"]
        second = self.store.authenticate("teacher", "teacher-password-1")["token"]
        self.store.set_password(self.teacher.id, "a-different-password-1")
        for token in (first, second):
            self.assertIsNone(self.store.resolve_session(token)[0])

    def test_a_password_change_does_not_touch_another_account(self):
        self.store.create("other", "other-password-1", omni_users.ROLE_USER)
        kept = self.store.authenticate("other", "other-password-1")["token"]
        self.store.set_password(self.teacher.id, "a-different-password-1")
        self.assertIsNotNone(self.store.resolve_session(kept)[0])

    def test_sessions_are_not_written_to_disk(self):
        """A restart signs everyone out, and nothing on disk says otherwise."""
        self.store.authenticate("teacher", "teacher-password-1")
        on_disk = (self.dir / "users.json").read_text(encoding="utf-8")
        self.assertNotIn("token", on_disk.lower())
        self.assertNotIn("session", on_disk.lower())


class AuditRedactionTests(unittest.TestCase):
    """Nothing secret is recorded, whatever a caller puts in a request."""

    CANARY = "canary-must-never-appear-2"

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="omni-audit-"))
        self.store = UserStore(self.dir / "users.json", self.dir / "audit.json")

    def test_every_secret_bearing_field_is_stripped_before_recording(self):
        self.store.audit("test", detail={
            "password": self.CANARY, "pwd": self.CANARY,
            "new_password": self.CANARY, "fallback_password": self.CANARY,
            "token": self.CANARY, "csrf_token": self.CANARY,
            "session": self.CANARY, "secret": self.CANARY,
            "password_hash": self.CANARY,
            # username is deliberately NOT a secret: the Activity view names
            # the actor, and redacting it would make the log unreadable.
            "nested": {"old_password": self.CANARY},
            "listed": [{"pwd": self.CANARY}],
            "kept": "ordinary detail"})
        self.store.flush_audit()
        entries = repr(self.store.audit_entries(10))
        self.assertNotIn(self.CANARY, entries)
        self.assertIn("kept", entries)
        self.assertNotIn(self.CANARY,
                         (self.dir / "audit.json").read_text(encoding="utf-8"))

    def test_a_sign_in_attempt_never_records_the_password(self):
        self.store.create("victim", self.CANARY, omni_users.ROLE_USER)
        self.store.authenticate("victim", self.CANARY, source="10.0.0.1")
        self.store.authenticate("victim", "wrong", source="10.0.0.1")
        self.store.flush_audit()
        for blob in (repr(self.store.audit_entries(50)),
                     (self.dir / "audit.json").read_text(encoding="utf-8"),
                     (self.dir / "users.json").read_text(encoding="utf-8")):
            self.assertNotIn(self.CANARY, blob)

    def test_the_log_survives_a_restart(self):
        self.store.audit("login.success", detail={"username": "someone"})
        self.store.flush_audit()
        reopened = UserStore(self.dir / "users.json", self.dir / "audit.json")
        self.assertTrue(reopened.audit_entries(10),
                        "the activity view must not be blank after a restart")

    def test_a_corrupt_log_costs_the_history_and_nothing_else(self):
        (self.dir / "audit.json").write_text("{ broken", encoding="utf-8")
        reopened = UserStore(self.dir / "users.json", self.dir / "audit.json")
        self.assertEqual(reopened.audit_entries(10), [])
        reopened.audit("login.success")
        self.assertTrue(reopened.audit_entries(10))

    def test_filtering_happens_here_rather_than_in_the_browser(self):
        for index in range(20):
            self.store.audit("login.success", detail={"n": index})
        for index in range(5):
            self.store.audit("access.denied", detail={"n": index})
        denied = self.store.audit_entries(100, event="access.denied")
        self.assertEqual(len(denied), 5)
        self.assertTrue(all(e["event"] == "access.denied" for e in denied))

    def test_entries_come_back_newest_first(self):
        self.store.audit("first")
        self.store.audit("second")
        entries = self.store.audit_entries(10)
        self.assertEqual(entries[0]["event"], "second")


class SessionLifetimeTests(unittest.TestCase):
    """Expiry and the second line of defence behind a disabled account.

    These exist because a mutation harness removed each of them and the suite
    carried on passing.
    """

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="omni-session-"))
        self.store = UserStore(self.dir / "users.json", self.dir / "audit.json")
        self.store.create("teacher", "teacher-password-1", omni_users.ROLE_USER)
        self.teacher = self.store.find_by_username("teacher")
        self.token = self.store.authenticate(
            "teacher", "teacher-password-1")["token"]

    def _session(self):
        return self.store._sessions[self.token]

    def test_an_idle_session_stops_working(self):
        self._session().last_seen -= omni_users.IDLE_TIMEOUT_SECONDS + 1
        self.assertIsNone(self.store.resolve_session(self.token)[0])

    def test_a_session_just_inside_the_idle_window_still_works(self):
        self._session().last_seen -= omni_users.IDLE_TIMEOUT_SECONDS - 60
        self.assertIsNotNone(self.store.resolve_session(self.token)[0])

    def test_a_busy_session_still_expires_eventually(self):
        """The absolute limit, which activity must not be able to extend."""
        session = self._session()
        session.created_at -= omni_users.ABSOLUTE_TIMEOUT_SECONDS + 1
        session.last_seen = time.time()          # in use this very second
        self.assertIsNone(self.store.resolve_session(self.token)[0])

    def test_an_expired_session_is_discarded_rather_than_kept(self):
        self._session().created_at -= omni_users.ABSOLUTE_TIMEOUT_SECONDS + 1
        self.store.resolve_session(self.token)
        self.assertNotIn(self.token, self.store._sessions)

    def test_a_disabled_account_is_refused_even_if_its_session_survived(self):
        """The second line of defence, tested on its own.

        `set_enabled` revokes every session in the same critical section, so in
        the ordinary path this check is never what refuses. That is exactly why
        it needs its own test: a future path that sets `enabled` without
        revoking would otherwise leave an open page fully authorised.
        """
        self.teacher.enabled = False             # deliberately not set_enabled
        self.assertIsNone(self.store.resolve_session(self.token)[0])
        self.assertNotIn(self.token, self.store._sessions)


class TransportSecurityTests(unittest.TestCase):
    """The Secure cookie, and plaintext needing to be asked for."""

    def setUp(self):
        import OmniMatrix_upgrade_server_v7_6y as srv
        self.srv = srv
        self.dir = Path(tempfile.mkdtemp(prefix="omni-transport-"))
        # The API captured this object when it registered its routes, so
        # substituting the module attribute would leave the handler talking to
        # the real store. Redirect this one's files instead, and put back
        # everything it held.
        self.store = srv._user_store
        saved = (self.store._path, self.store._audit_path,
                 dict(self.store._accounts), dict(self.store._sessions),
                 list(self.store._audit))

        def restore():
            (self.store._path, self.store._audit_path) = saved[0], saved[1]
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
        self.store.create("principal", "principal-pass-1", omni_users.ROLE_ADMIN)

    def _login(self, base_url):
        client = self.srv.app.test_client()
        response = client.post("/api/auth/login", json={
            "username": "principal", "password": "principal-pass-1"},
            base_url=base_url)
        self.assertEqual(response.status_code, 200)
        return " ".join(response.headers.get_all("Set-Cookie"))

    def test_the_cookie_is_secure_over_tls(self):
        cookie = self._login("https://omnisuite.invalid/")
        self.assertIn("Secure", cookie)
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Lax", cookie)

    def test_the_cookie_is_not_marked_secure_over_plaintext(self):
        """Not a weakening: a Secure cookie over http is simply never sent,
        which would sign the operator out of their own bench."""
        cookie = self._login("http://omnisuite.invalid/")
        self.assertNotIn("Secure", cookie)
        self.assertIn("HttpOnly", cookie)

    def test_the_flag_follows_the_transport_rather_than_a_setting(self):
        self.assertNotEqual(
            "Secure" in self._login("https://omnisuite.invalid/"),
            "Secure" in self._login("http://omnisuite.invalid/"))

    def test_plaintext_remote_access_must_be_asked_for(self):
        """Omitting a field must not be a way to reach the unencrypted path."""
        client = self.srv.app.test_client()
        response = client.post("/api/remote_access", json={
            "enabled": True, "address": "198.18.7.9", "port": 8443,
            "transport": "http"})
        self.assertEqual(response.status_code, 400)
        self.assertTrue(response.get_json().get("needs_confirmation"))

    def test_the_acknowledgement_is_specific_to_plaintext(self):
        """A truthy value of some other name must not do instead."""
        client = self.srv.app.test_client()
        response = client.post("/api/remote_access", json={
            "enabled": True, "address": "198.18.7.9", "port": 8443,
            "transport": "http", "confirm": True, "accept": True,
            "insecure": True})
        self.assertEqual(response.status_code, 400)
        self.assertTrue(response.get_json().get("needs_confirmation"))


if __name__ == "__main__":
    unittest.main()
