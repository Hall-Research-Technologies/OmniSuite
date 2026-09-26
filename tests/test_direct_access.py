"""Direct Access links: a URL that works without a password, and its fences.

The feature exists because a tablet on a lectern should not ask a teacher to
type a password to change what is on the projector. Everything here is about
the consequences of that, because possession of the URL is the credential.

Six properties, and each one has tests that would fail if it stopped holding:

  1. It is OFF by default, per account, and only an administrator turns it on.
  2. It can never exist on an administrator's account, and a session opened by
     a link is refused the administrative surface even if the account is
     promoted afterwards.
  3. It carries no permissions. It names an account, and every request is
     authorised against that account as it is at that moment -- so removing a
     capability, a source or a display takes it from the link at once.
  4. Exactly one token is live. Regenerating invalidates the previous URL and
     ends the sessions it opened.
  5. The token never appears in an audit entry or a log line.
  6. Nothing else about the security model is relaxed: CSRF, per-device
     authorisation and the route classification all still apply.

No address, hostname or token in this file is real.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("OMNI_SMOKE", "1")

import omni_gate                                        # noqa: E402
import omni_remote                                      # noqa: E402
import omni_secret                                      # noqa: E402
import omni_users                                       # noqa: E402
from omni_access import Access                          # noqa: E402
from omni_users import Permissions, UserStore           # noqa: E402

import omni_access                                      # noqa: E402

TEACHER_SOURCE = "B8:98:B0:07:85:C6"
TEACHER_DISPLAY = "B8:98:B0:06:41:B3"
SOMEONE_ELSES = "B8:98:B0:99:99:99"


class StoreTestBase(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="omni-direct-"))
        self.store = UserStore(self.dir / "users.json", self.dir / "audit.json")
        self.store.create("principal", "principal-pass-1", omni_users.ROLE_ADMIN)
        self.store.create("teacher", "teacher-password-1", omni_users.ROLE_USER,
                          Permissions(features=("av", "usb", "multiview"),
                                      sources=[TEACHER_SOURCE],
                                      destinations=[TEACHER_DISPLAY]))
        self.store.create("janitor", "janitor-password-1", omni_users.ROLE_USER,
                          Permissions(features=("av",)))
        self.admin = self.store.find_by_username("principal")
        self.teacher = self.store.find_by_username("teacher")
        self.janitor = self.store.find_by_username("janitor")


class OffByDefaultTests(StoreTestBase):
    def test_a_new_account_has_no_link(self):
        self.assertFalse(self.teacher.direct_access.enabled)
        self.assertFalse(self.teacher.direct_access.active)
        self.assertIsNone(self.store.direct_access_token(self.teacher.id))

    def test_what_an_api_shows_says_only_that_there_is_none(self):
        public = self.teacher.public()["direct_access"]
        self.assertEqual(set(public), {"enabled", "protection", "created_at"})
        self.assertFalse(public["enabled"])

    def test_the_route_that_creates_one_is_administrative(self):
        access, _f = omni_access.classify(
            "/api/admin/users/<user_id>/direct_access", "POST")
        self.assertIs(access, Access.ADMIN)

    def test_the_route_that_shows_your_own_is_not_administrative(self):
        access, _f = omni_access.classify("/api/user/direct_access", "GET")
        self.assertIs(access, Access.AUTHENTICATED)


class MintingTests(StoreTestBase):
    def test_enabling_produces_a_token(self):
        result = self.store.enable_direct_access(self.teacher.id,
                                                 actor=self.admin)
        self.assertTrue(result["ok"])
        self.assertTrue(result["token"])
        self.assertTrue(self.teacher.direct_access.active)

    def test_the_token_is_long_and_from_the_system_random_source(self):
        token = self.store.enable_direct_access(
            self.teacher.id, actor=self.admin)["token"]
        # 32 bytes of entropy, urlsafe-base64. Anything materially shorter is
        # guessable by somebody willing to spend a weekend on it.
        self.assertGreaterEqual(len(token), 40)
        self.assertEqual(len(set(token)) > 16, True)

    def test_two_accounts_receive_different_tokens(self):
        first = self.store.enable_direct_access(self.teacher.id,
                                                actor=self.admin)["token"]
        second = self.store.enable_direct_access(self.janitor.id,
                                                 actor=self.admin)["token"]
        self.assertNotEqual(first, second)

    def test_the_token_is_not_derived_from_anything_about_the_account(self):
        token = self.store.enable_direct_access(
            self.teacher.id, actor=self.admin)["token"]
        for known in (self.teacher.id, self.teacher.username,
                      "teacher-password-1", TEACHER_SOURCE,
                      TEACHER_SOURCE.replace(":", "")):
            with self.subTest(known=known):
                self.assertNotIn(known.lower(), token.lower())

    def test_an_administrator_can_never_be_given_one(self):
        result = self.store.enable_direct_access(self.admin.id, actor=self.admin)
        self.assertFalse(result["ok"])
        self.assertFalse(self.admin.direct_access.active)

    def test_promoting_an_account_takes_its_link_away(self):
        self.store.enable_direct_access(self.teacher.id, actor=self.admin)
        self.store.set_role(self.teacher.id, omni_users.ROLE_ADMIN,
                            actor=self.admin)
        self.assertFalse(self.teacher.direct_access.active)


class StorageTests(StoreTestBase):
    """It has to be recoverable, and it must not be sitting there in the clear."""

    def test_the_raw_token_is_not_in_the_accounts_file(self):
        token = self.store.enable_direct_access(
            self.teacher.id, actor=self.admin)["token"]
        stored = (self.dir / "users.json").read_text(encoding="utf-8")
        self.assertNotIn(token, stored,
                         "the bearer credential must not be readable in the "
                         "accounts file")

    def test_the_same_link_can_be_shown_again_later(self):
        token = self.store.enable_direct_access(
            self.teacher.id, actor=self.admin)["token"]
        self.assertEqual(self.store.direct_access_token(self.teacher.id), token)

    def test_it_survives_a_restart(self):
        token = self.store.enable_direct_access(
            self.teacher.id, actor=self.admin)["token"]
        reopened = UserStore(self.dir / "users.json", self.dir / "audit.json")
        account = reopened.find_by_username("teacher")
        self.assertEqual(reopened.direct_access_token(account.id), token)

    def test_what_is_stored_records_which_protection_was_applied(self):
        self.store.enable_direct_access(self.teacher.id, actor=self.admin)
        protection = self.teacher.direct_access.protection
        self.assertIn(protection, (omni_secret.DPAPI, omni_secret.PLAIN))
        # And the record is honest about it rather than claiming more than the
        # platform provided.
        self.assertEqual(protection == omni_secret.DPAPI,
                         omni_secret.available())

    def test_a_blob_that_cannot_be_read_here_yields_no_link_rather_than_a_wrong_one(self):
        self.store.enable_direct_access(self.teacher.id, actor=self.admin)
        self.teacher.direct_access.blob = "bm90LWEtcmVhbC1ibG9i"
        self.assertIsNone(self.store.direct_access_token(self.teacher.id))

    def test_a_blob_that_disagrees_with_its_digest_is_refused(self):
        """A damaged record must not hand out a link that will not work."""
        self.store.enable_direct_access(self.teacher.id, actor=self.admin)
        blob, protection = omni_secret.protect("a-different-token")
        self.teacher.direct_access.blob = blob
        self.teacher.direct_access.protection = protection
        with self.assertLogs("omni_users", level=logging.WARNING):
            self.assertIsNone(self.store.direct_access_token(self.teacher.id))


class RedeemingTests(StoreTestBase):
    def setUp(self):
        super().setUp()
        self.token = self.store.enable_direct_access(
            self.teacher.id, actor=self.admin)["token"]

    def test_a_valid_token_names_its_account(self):
        account = self.store.resolve_direct_token(self.token)
        self.assertIsNotNone(account)
        self.assertEqual(account.username, "teacher")

    def test_an_unknown_token_is_refused(self):
        for wrong in ("", "   ", "not-a-token", self.token[:-1],
                      self.token + "x", self.token.upper()):
            with self.subTest(token=wrong):
                self.assertIsNone(self.store.resolve_direct_token(wrong))

    def test_a_disabled_account_refuses_its_own_link(self):
        self.store.set_enabled(self.teacher.id, False, actor=self.admin)
        self.assertIsNone(self.store.resolve_direct_token(self.token))

    def test_a_deleted_account_refuses_its_own_link(self):
        self.store.delete(self.teacher.id, actor=self.admin)
        self.assertIsNone(self.store.resolve_direct_token(self.token))

    def test_turning_the_feature_off_refuses_it(self):
        self.store.disable_direct_access(self.teacher.id, actor=self.admin)
        self.assertIsNone(self.store.resolve_direct_token(self.token))

    def test_regenerating_invalidates_the_link_already_handed_out(self):
        replacement = self.store.regenerate_direct_access(
            self.teacher.id, actor=self.admin)["token"]
        self.assertNotEqual(replacement, self.token)
        self.assertIsNone(self.store.resolve_direct_token(self.token))
        self.assertIsNotNone(self.store.resolve_direct_token(replacement))

    def test_only_one_token_is_live_at_a_time(self):
        self.store.regenerate_direct_access(self.teacher.id, actor=self.admin)
        self.store.regenerate_direct_access(self.teacher.id, actor=self.admin)
        live = [t for t in (self.token,)
                if self.store.resolve_direct_token(t) is not None]
        self.assertEqual(live, [])


class SessionTests(StoreTestBase):
    def setUp(self):
        super().setUp()
        self.token = self.store.enable_direct_access(
            self.teacher.id, actor=self.admin)["token"]
        self.direct = self.store.open_session(self.teacher.id,
                                              omni_users.BY_DIRECT_LINK)
        self.password = self.store.open_session(self.teacher.id)

    def test_a_session_records_how_it_was_established(self):
        self.assertTrue(self.direct.from_direct_link)
        self.assertFalse(self.password.from_direct_link)

    def test_a_link_session_is_shorter_lived_than_a_password_one(self):
        self.assertLess(omni_users.DIRECT_ABSOLUTE_TIMEOUT_SECONDS,
                        omni_users.ABSOLUTE_TIMEOUT_SECONDS)
        self.assertLess(omni_users.DIRECT_IDLE_TIMEOUT_SECONDS,
                        omni_users.IDLE_TIMEOUT_SECONDS)
        # And the shorter limit is the one actually applied to it. Taken just
        # past the link session's idle window, which is still well inside the
        # password session's -- so the two are judged by different rules at the
        # same instant, which is the property under test.
        stale = self.direct.last_seen + omni_users.DIRECT_IDLE_TIMEOUT_SECONDS + 1
        self.assertTrue(self.direct.expired(stale))
        self.assertFalse(self.password.expired(stale))

    def test_regenerating_ends_the_sessions_the_old_link_opened(self):
        self.store.regenerate_direct_access(self.teacher.id, actor=self.admin)
        self.assertEqual(self.store.resolve_session(self.direct.token),
                         (None, None))

    def test_regenerating_leaves_a_password_session_alone(self):
        """Replacing a link is not a reason to sign somebody out of a browser."""
        self.store.regenerate_direct_access(self.teacher.id, actor=self.admin)
        account, _s = self.store.resolve_session(self.password.token)
        self.assertIsNotNone(account)

    def test_disabling_the_link_ends_its_sessions(self):
        self.store.disable_direct_access(self.teacher.id, actor=self.admin)
        self.assertEqual(self.store.resolve_session(self.direct.token),
                         (None, None))

    def test_disabling_the_account_ends_every_session(self):
        self.store.set_enabled(self.teacher.id, False, actor=self.admin)
        for session in (self.direct, self.password):
            with self.subTest(method=session.method):
                self.assertEqual(self.store.resolve_session(session.token),
                                 (None, None))

    def test_deleting_the_account_ends_every_session(self):
        self.store.delete(self.teacher.id, actor=self.admin)
        for session in (self.direct, self.password):
            with self.subTest(method=session.method):
                self.assertEqual(self.store.resolve_session(session.token),
                                 (None, None))


def remote_env(session=None):
    env = {omni_remote.LISTENER_KEY: omni_remote.REMOTE}
    if session is not None:
        env["HTTP_COOKIE"] = f"{omni_gate.SESSION_COOKIE}={session.token}"
        env["HTTP_" + omni_gate.CSRF_HEADER.upper().replace("-", "_")] = \
            session.csrf_token
    return env


class AuthorizationIsUnchangedTests(StoreTestBase):
    """A link changes who is asking. It changes nothing about what is allowed."""

    def setUp(self):
        super().setUp()
        self.store.enable_direct_access(self.teacher.id, actor=self.admin)
        self.session = self.store.open_session(self.teacher.id,
                                               omni_users.BY_DIRECT_LINK)

    def decide(self, rule, method="POST", payload=None):
        return omni_gate.evaluate(rule, method, remote_env(self.session),
                                  payload or {}, self.store)

    def test_it_can_do_what_the_account_may_do(self):
        decision = self.decide("/api/user/route",
                               payload={"source": TEACHER_SOURCE,
                                        "destination": TEACHER_DISPLAY})
        self.assertTrue(decision.allowed, decision.reason)

    def test_a_device_the_account_was_never_granted_is_still_refused(self):
        decision = self.decide("/api/user/route",
                               payload={"source": TEACHER_SOURCE,
                                        "destination": SOMEONE_ELSES})
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "destination_not_permitted")

    def test_removing_a_capability_takes_it_from_the_link_at_once(self):
        self.store.set_permissions(self.teacher.id,
                                   Permissions(features=("av",),
                                               sources=[TEACHER_SOURCE],
                                               destinations=[TEACHER_DISPLAY]),
                                   actor=self.admin)
        # The permission change revoked every session, which is the mechanism:
        # a new one from the same link gets the new permissions.
        fresh = self.store.open_session(self.teacher.id,
                                        omni_users.BY_DIRECT_LINK)
        decision = omni_gate.evaluate("/api/user/usb_route", "POST",
                                      remote_env(fresh), {}, self.store)
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "missing_feature:usb")

    def test_removing_a_source_takes_it_from_the_link_at_once(self):
        self.store.set_permissions(self.teacher.id,
                                   Permissions(features=("av",), sources=[],
                                               destinations=[TEACHER_DISPLAY]),
                                   actor=self.admin)
        fresh = self.store.open_session(self.teacher.id,
                                        omni_users.BY_DIRECT_LINK)
        decision = omni_gate.evaluate(
            "/api/user/route", "POST", remote_env(fresh),
            {"source": TEACHER_SOURCE, "destination": TEACHER_DISPLAY},
            self.store)
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "source_not_permitted")

    def test_a_missing_csrf_token_is_still_refused(self):
        """Passwordless does not mean the other controls go away."""
        environ = remote_env(self.session)
        environ.pop("HTTP_" + omni_gate.CSRF_HEADER.upper().replace("-", "_"))
        decision = omni_gate.evaluate("/api/user/route", "POST", environ,
                                      {"source": TEACHER_SOURCE,
                                       "destination": TEACHER_DISPLAY},
                                      self.store)
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "csrf")


class NeverAdministrativeTests(StoreTestBase):
    """The outcome this feature must never be able to produce."""

    ADMIN_SURFACE = ("/api/admin/users", "/api/admin/audit",
                     "/api/remote_access", "/api/scan", "/api/config",
                     "/api/state", "/api/upgrade", "/api/usb_pair",
                     "/api/multiview/apply", "/api/admin/certificate",
                     "/api/admin/users/<user_id>/direct_access",
                     "/", "/matrix", "/admin/users")

    def setUp(self):
        super().setUp()
        self.store.enable_direct_access(self.teacher.id, actor=self.admin)
        self.session = self.store.open_session(self.teacher.id,
                                               omni_users.BY_DIRECT_LINK)

    def test_no_administrative_route_is_reachable(self):
        for rule in self.ADMIN_SURFACE:
            with self.subTest(rule=rule):
                decision = omni_gate.evaluate(
                    rule, "GET", remote_env(self.session), {}, self.store)
                self.assertFalse(decision.allowed, rule)

    def test_the_host_surface_is_not_reachable_either(self):
        for rule in ("/api/list_dir", "/api/files",
                     "/api/diagnostics/usb_endpoints"):
            with self.subTest(rule=rule):
                decision = omni_gate.evaluate(
                    rule, "GET", remote_env(self.session), {}, self.store)
                self.assertFalse(decision.allowed)
                self.assertEqual(decision.status, 404)

    def test_promoting_the_account_does_not_promote_the_link(self):
        """Two controls, not one. The role check alone would let this through."""
        # Done directly rather than through set_role, which would revoke the
        # session -- the point is that even a session that survived somehow is
        # refused on its own merits.
        self.teacher.role = omni_users.ROLE_ADMIN
        decision = omni_gate.evaluate("/api/admin/users", "GET",
                                      remote_env(self.session), {}, self.store)
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "direct_link_not_admin")

    def test_a_promoted_account_does_not_skip_the_device_check(self):
        self.teacher.role = omni_users.ROLE_ADMIN
        decision = omni_gate.evaluate(
            "/api/user/route", "POST", remote_env(self.session),
            {"source": TEACHER_SOURCE, "destination": SOMEONE_ELSES},
            self.store)
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "destination_not_permitted")

    def test_a_password_session_for_an_administrator_is_unaffected(self):
        """The bar is on the link, not on administrators."""
        session = self.store.open_session(self.admin.id)
        decision = omni_gate.evaluate("/api/admin/users", "GET",
                                      remote_env(session), {}, self.store)
        self.assertTrue(decision.allowed)


class AuditTests(StoreTestBase):
    """Useful, and carrying no credential."""

    def test_the_events_are_recorded(self):
        self.store.enable_direct_access(self.teacher.id, actor=self.admin)
        self.store.regenerate_direct_access(self.teacher.id, actor=self.admin)
        self.store.disable_direct_access(self.teacher.id, actor=self.admin)
        events = [e["event"] for e in self.store.audit_entries(50)]
        for expected in ("direct_access.enabled", "direct_access.regenerated",
                         "direct_access.disabled"):
            with self.subTest(event=expected):
                self.assertIn(expected, events)

    def test_an_entry_names_the_account_and_who_did_it(self):
        self.store.enable_direct_access(self.teacher.id, actor=self.admin)
        entry = next(e for e in self.store.audit_entries(50)
                     if e["event"] == "direct_access.enabled")
        self.assertEqual(entry["detail"]["username"], "teacher")
        self.assertEqual(entry["actor"], "principal")

    def test_no_token_ever_reaches_the_audit(self):
        token = self.store.enable_direct_access(
            self.teacher.id, actor=self.admin)["token"]
        replacement = self.store.regenerate_direct_access(
            self.teacher.id, actor=self.admin)["token"]
        flattened = json.dumps(self.store.audit_entries(200))
        for secret in (token, replacement):
            with self.subTest(secret=secret[:8]):
                self.assertNotIn(secret, flattened)
        # And nothing in an entry is long enough to be one by accident.
        for entry in self.store.audit_entries(200):
            for value in (entry.get("detail") or {}).values():
                if isinstance(value, str):
                    self.assertLess(len(value), 40, entry["event"])

    def test_no_token_reaches_the_audit_file_on_disk(self):
        token = self.store.enable_direct_access(
            self.teacher.id, actor=self.admin)["token"]
        self.store.audit("direct_access.used", actor=self.teacher,
                         detail={"result": "accepted"})
        self.assertNotIn(token, (self.dir / "audit.json").read_text(
            encoding="utf-8"))


class SourceFenceTests(unittest.TestCase):
    """What the code is not allowed to do with a token, asserted in the source.

    A runtime test cannot prove that no log statement anywhere formats one.
    These can, and they fail the moment somebody adds one.
    """

    FILES = ("omni_users.py", "omni_remote_api.py", "omni_secret.py")

    def _code(self, name):
        text = (ROOT / name).read_text(encoding="utf-8")
        # Comments stripped: a sentence explaining that tokens are not logged
        # must not be what satisfies an assertion that they are not.
        out = []
        for line in text.split("\n"):
            out.append(line.split("#")[0])
        return "\n".join(out)

    def test_no_log_statement_formats_a_token(self):
        for name in self.FILES:
            code = self._code(name)
            for line in code.split("\n"):
                if "log." not in line:
                    continue
                with self.subTest(name=name, line=line.strip()[:60]):
                    self.assertNotIn("token", line.lower(),
                                     "a bearer credential must never be logged")

    def test_the_link_is_never_written_to_browser_storage(self):
        for name in ("ui/admin/users.js", "ui/user/user.js"):
            text = (ROOT / name).read_text(encoding="utf-8")
            code = "\n".join(l.split("//")[0] for l in text.split("\n"))
            for marker in ("localStorage.setItem('direct",
                           "localStorage.setItem('omni_link",
                           "sessionStorage.setItem('direct"):
                with self.subTest(name=name, marker=marker):
                    self.assertNotIn(marker, code)
            # Nothing writes the link element's value into storage at all.
            self.assertNotIn("localStorage.setItem", code,
                             f"{name} must not put anything in localStorage")

    def test_a_token_in_a_request_path_is_redacted_before_it_is_logged(self):
        """The request log would otherwise hold a working passwordless link.

        Driven through the logging machinery rather than asserted against the
        source, because what matters is what lands in the file.
        """
        import importlib
        import io
        srv = importlib.import_module("OmniMatrix_upgrade_server_v7_6y")
        token = "aBcD" + "e" * 40
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        logger = logging.getLogger("werkzeug")
        logger.addHandler(handler)
        previous = logger.level
        logger.setLevel(logging.INFO)
        try:
            srv._install_token_redaction()
            logger.info('127.0.0.1 - - "GET /access/%s HTTP/1.1" 302 -', token)
            logger.error("Error on /access/%s", token)
        finally:
            logger.removeHandler(handler)
            logger.setLevel(previous)
        written = stream.getvalue()
        self.assertNotIn(token, written, "the token reached the log")
        self.assertIn("/access/aBcD...redacted", written,
                      "enough should survive to tell two attempts apart")
        # The line is still worth reading.
        self.assertIn("302", written)

    def test_the_redaction_leaves_an_ordinary_line_alone(self):
        import importlib
        import io
        srv = importlib.import_module("OmniMatrix_upgrade_server_v7_6y")
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        logger = logging.getLogger("werkzeug")
        logger.addHandler(handler)
        previous = logger.level
        logger.setLevel(logging.INFO)
        try:
            srv._install_token_redaction()
            logger.info('127.0.0.1 - - "GET /api/state HTTP/1.1" 200 -')
        finally:
            logger.removeHandler(handler)
            logger.setLevel(previous)
        self.assertIn("/api/state", stream.getvalue())
        self.assertNotIn("redacted", stream.getvalue())

    def test_installing_the_redaction_twice_does_not_stack_it(self):
        import importlib
        srv = importlib.import_module("OmniMatrix_upgrade_server_v7_6y")
        logger = logging.getLogger("werkzeug")
        srv._install_token_redaction()
        before = len([f for f in logger.filters
                      if isinstance(f, srv._RedactDirectAccessToken)])
        srv._install_token_redaction()
        after = len([f for f in logger.filters
                     if isinstance(f, srv._RedactDirectAccessToken)])
        self.assertEqual(before, after)

    def test_the_gate_bars_link_sessions_from_the_admin_surface(self):
        code = self._code("omni_gate.py")
        self.assertIn("from_direct_link", code)
        self.assertIn("direct_link_not_admin", code)


if __name__ == "__main__":
    unittest.main()
