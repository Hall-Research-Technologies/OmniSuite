"""Adversarial tests for the authorization gate.

These do not drive a UI. Every one of them is the shape of an attack: a crafted
request, a stolen-looking session, a permission that was revoked a moment ago, a
device the account was never granted. The brief is explicit that hiding a tile
is not authorization, so nothing here presses a button.

The gate is evaluated directly through `omni_gate.evaluate`, which takes a rule,
a method, a WSGI environ, a body and a store, and returns a decision. That keeps
the tests about the policy rather than about Flask, and means a failure names
the rule that let something through.
"""
from __future__ import annotations

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
import omni_remote                                   # noqa: E402
import omni_users                                    # noqa: E402
from omni_access import Access                       # noqa: E402
from omni_users import Permissions, UserStore        # noqa: E402

TEACHER_SOURCES = ["B8:98:B0:07:85:C6", "b8-98-b0-07-85-e6"]
TEACHER_DISPLAYS = ["B8:98:B0:06:41:B3"]
SOMEONE_ELSES_DISPLAY = "B8:98:B0:99:99:99"


def remote_env(cookie=None, csrf=None):
    """A WSGI environ as the remote listener would produce it."""
    env = {omni_remote.LISTENER_KEY: omni_remote.REMOTE}
    if cookie:
        env["HTTP_COOKIE"] = f"{omni_gate.SESSION_COOKIE}={cookie}"
    if csrf:
        env["HTTP_" + omni_gate.CSRF_HEADER.upper().replace("-", "_")] = csrf
    return env


def local_env():
    return {omni_remote.LISTENER_KEY: omni_remote.LOCAL}


class GateTestBase(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="omni-users-"))
        self.store = UserStore(self.dir / "users.json", self.dir / "audit.json")
        self.store.create("admin", "admin-password-1", omni_users.ROLE_ADMIN)
        self.admin = self.store.find_by_username("admin")

        self.store.create("teacher", "teacher-password-1", omni_users.ROLE_USER,
                          Permissions(features=("av", "multiview"),
                                      sources=TEACHER_SOURCES,
                                      destinations=TEACHER_DISPLAYS))
        self.teacher = self.store.find_by_username("teacher")

        self.admin_session = self.store.open_session(self.admin.id)
        self.teacher_session = self.store.open_session(self.teacher.id)

    def decide(self, rule, method="GET", session=None, payload=None, csrf=True):
        env = remote_env(
            cookie=session.token if session else None,
            csrf=(session.csrf_token if (session and csrf) else None))
        return omni_gate.evaluate(rule, method, env, payload or {}, self.store)


class AnonymousRemoteAccessTests(GateTestBase):
    """Nothing that touches a device answers a request with no session."""

    def test_every_non_public_route_refuses_an_anonymous_remote_request(self):
        for rule, (access, _f) in omni_access.ROUTES.items():
            if access is Access.PUBLIC:
                continue
            with self.subTest(rule=rule):
                decision = omni_gate.evaluate(rule, "GET", remote_env(), {}, self.store)
                self.assertFalse(
                    decision.allowed,
                    f"{rule} answered an anonymous request from the network")
                self.assertIn(decision.status, (401, 404))

    def test_the_credential_endpoint_is_not_anonymously_readable(self):
        """The live finding that motivated this work.

        GET /api/config?include_password=1 returns both device passwords. It is
        safe today only because the application is loopback-only.
        """
        decision = omni_gate.evaluate("/api/config", "GET", remote_env(),
                                      {"include_password": "1"}, self.store)
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.status, 401)

    def test_the_host_filesystem_is_not_reachable_from_the_network(self):
        """Not even for an Admin: list_dir resolves any absolute path."""
        for session in (None, self.admin_session):
            with self.subTest(session="anonymous" if session is None else "admin"):
                decision = self.decide("/api/list_dir", "GET", session,
                                       {"path": "C:\\"})
                self.assertFalse(decision.allowed)
                self.assertEqual(decision.status, 404,
                                 "a host-only route should not even admit it exists")


class LocalOperatorTests(GateTestBase):
    """The local experience is untouched: no login, every route."""

    def test_the_local_listener_reaches_everything_without_a_session(self):
        for rule in list(omni_access.ROUTES)[:40]:
            with self.subTest(rule=rule):
                decision = omni_gate.evaluate(rule, "POST", local_env(), {}, self.store)
                self.assertTrue(decision.allowed,
                                f"{rule} refused the local operator")

    def test_an_in_process_request_is_local(self):
        """No tag means no socket: an in-process caller, which is local.

        Every listener the product opens is tagged where it is bound, so this
        is not a gap -- tests/test_remote_access.py holds that guarantee in
        place by failing if a bare app.run or make_server reappears.
        """
        decision = omni_gate.evaluate("/api/scan", "POST", {}, {}, self.store)
        self.assertTrue(decision.allowed)

    def test_a_listener_on_a_routable_address_does_not_confer_local_trust(self):
        """The case that matters: bound to the network, remote access off."""
        env = {omni_remote.LISTENER_KEY:
               omni_remote.tag_for_bind("10.48.55.10")}
        decision = omni_gate.evaluate("/api/scan", "POST", env, {}, self.store)
        self.assertFalse(decision.allowed,
                         "a network-facing listener must require a session")

    def test_a_forged_header_does_not_make_a_request_local(self):
        env = remote_env()
        env["HTTP_OMNI_LISTENER"] = omni_remote.LOCAL
        env["HTTP_OMNI.LISTENER"] = omni_remote.LOCAL
        env["HTTP_X_FORWARDED_FOR"] = "127.0.0.1"
        env["HTTP_X_REAL_IP"] = "127.0.0.1"
        env["REMOTE_ADDR"] = "127.0.0.1"
        decision = omni_gate.evaluate("/api/scan", "POST", env, {}, self.store)
        self.assertFalse(decision.allowed,
                         "spoofing loopback must not grant local trust")


class RoleTests(GateTestBase):
    """Admin and User reach different things."""

    def test_a_restricted_user_cannot_scan(self):
        decision = self.decide("/api/scan", "POST", self.teacher_session)
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "not_admin")

    def test_a_restricted_user_cannot_reach_settings_or_firmware(self):
        for rule in ("/api/config", "/api/upgrade", "/api/sync_passwords",
                     "/api/reboot", "/api/hostname", "/api/unit_config/import"):
            with self.subTest(rule=rule):
                decision = self.decide(rule, "POST", self.teacher_session)
                self.assertFalse(decision.allowed)

    def test_a_restricted_user_cannot_manage_users(self):
        """Privilege escalation, attempted directly against the table."""
        for rule in omni_access.ROUTES:
            if "user" in rule and rule.startswith("/api/"):
                with self.subTest(rule=rule):
                    decision = self.decide(rule, "POST", self.teacher_session)
                    self.assertFalse(decision.allowed)

    def test_an_admin_reaches_the_admin_surface(self):
        decision = self.decide("/api/scan", "POST", self.admin_session)
        self.assertTrue(decision.allowed)

    def test_an_admin_is_not_stopped_by_a_missing_capability(self):
        """Admins hold no capability grants; the role is the grant."""
        decision = self.decide("/api/route", "POST", self.admin_session,
                               {"encoder": "anything", "decoder": "anything"})
        self.assertTrue(decision.allowed)


class FeaturePermissionTests(GateTestBase):
    """A capability gates a whole class of routing."""

    def test_a_user_without_usb_cannot_reach_usb_routing(self):
        for rule in ("/api/usb_state", "/api/usb_route/pair",
                     "/api/usb_route/unpair", "/api/usb_route/resolve"):
            with self.subTest(rule=rule):
                decision = self.decide(rule, "POST", self.teacher_session,
                                       {"host_mac": TEACHER_SOURCES[0]})
                self.assertFalse(decision.allowed)
                self.assertEqual(decision.reason, "missing_feature:usb")

    def test_granting_usb_opens_only_usb(self):
        self.store.set_permissions(self.teacher.id, Permissions(
            features=("usb",), sources=TEACHER_SOURCES,
            destinations=TEACHER_DISPLAYS))
        session = self.store.open_session(self.teacher.id)
        allowed = self.decide("/api/usb_route/pair", "POST", session,
                              {"host_mac": TEACHER_SOURCES[0],
                               "device_mac": TEACHER_DISPLAYS[0]})
        self.assertTrue(allowed.allowed)
        denied = self.decide("/api/route", "POST", session,
                             {"encoder": TEACHER_SOURCES[0],
                              "decoder": TEACHER_DISPLAYS[0]})
        self.assertFalse(denied.allowed)
        self.assertEqual(denied.reason, "missing_feature:av")

    def test_multiview_administration_is_refused_to_an_operating_user(self):
        """The Multiview boundary: operate, do not administer."""
        for rule in ("/api/multiview/delete", "/api/multiview/copy",
                     "/api/multiview/apply", "/api/multiview/layouts/install",
                     "/api/multiview/groups/save"):
            with self.subTest(rule=rule):
                decision = self.decide(rule, "POST", self.teacher_session,
                                       {"ip": TEACHER_DISPLAYS[0]})
                self.assertFalse(decision.allowed,
                                 f"{rule} is administration, not operation")

    def test_multiview_show_and_switch_are_permitted_on_a_granted_display(self):
        for rule in ("/api/multiview/show", "/api/multiview/switch"):
            with self.subTest(rule=rule):
                decision = self.decide(rule, "POST", self.teacher_session,
                                       {"ip": TEACHER_DISPLAYS[0],
                                        "source": TEACHER_SOURCES[0]})
                self.assertTrue(decision.allowed, decision.reason)


class DevicePermissionTests(GateTestBase):
    """A capability is not a device grant. This is the check that cannot be skipped."""

    def test_a_permitted_route_is_allowed(self):
        decision = self.decide("/api/route", "POST", self.teacher_session,
                               {"encoder": TEACHER_SOURCES[0],
                                "decoder": TEACHER_DISPLAYS[0]})
        self.assertTrue(decision.allowed, decision.reason)

    def test_an_unauthorised_source_is_refused(self):
        decision = self.decide("/api/route", "POST", self.teacher_session,
                               {"encoder": "B8:98:B0:11:11:11",
                                "decoder": TEACHER_DISPLAYS[0]})
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "source_not_permitted")

    def test_an_unauthorised_destination_is_refused(self):
        """Routing into another classroom."""
        decision = self.decide("/api/route", "POST", self.teacher_session,
                               {"encoder": TEACHER_SOURCES[0],
                                "decoder": SOMEONE_ELSES_DISPLAY})
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "destination_not_permitted")

    def test_both_ends_are_checked_not_just_one(self):
        """A gate that checked only the destination would pass this."""
        decision = self.decide("/api/route", "POST", self.teacher_session,
                               {"encoder": "B8:98:B0:11:11:11",
                                "decoder": TEACHER_DISPLAYS[0]})
        self.assertFalse(decision.allowed)

    def test_a_list_of_destinations_is_checked_element_by_element(self):
        """One permitted decoder must not carry an unpermitted one in with it."""
        decision = self.decide("/api/multiview/show", "POST", self.teacher_session,
                               {"decoder": [TEACHER_DISPLAYS[0],
                                            SOMEONE_ELSES_DISPLAY]})
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "destination_not_permitted")

    def test_identity_spelling_does_not_decide_permission(self):
        """The same MAC in four spellings is the same device.

        A check that compared raw strings would deny a permitted display
        because a different code path spelled its MAC with dashes.
        """
        for spelling in ("B8:98:B0:06:41:B3", "b8:98:b0:06:41:b3",
                         "b8-98-b0-06-41-b3", "B898B00641B3"):
            with self.subTest(spelling=spelling):
                decision = self.decide("/api/route", "POST", self.teacher_session,
                                       {"encoder": TEACHER_SOURCES[0],
                                        "decoder": spelling})
                self.assertTrue(decision.allowed,
                                f"{spelling} is the permitted display")

    def test_an_empty_body_does_not_grant_a_device(self):
        """No named device is not the same as a permitted device.

        This passes the gate -- there is nothing to refuse -- and the handler
        is responsible for rejecting a request that names no device. Asserted so
        the division of responsibility is explicit rather than assumed.
        """
        decision = self.decide("/api/route", "POST", self.teacher_session, {})
        self.assertTrue(decision.allowed)


class SessionLifecycleTests(GateTestBase):
    def test_logout_ends_the_session_immediately(self):
        self.assertTrue(self.decide("/api/state", "GET", self.teacher_session).allowed)
        self.store.revoke(self.teacher_session.token)
        self.assertFalse(self.decide("/api/state", "GET", self.teacher_session).allowed)

    def test_disabling_an_account_ends_its_sessions_at_once(self):
        self.assertTrue(self.decide("/api/state", "GET", self.teacher_session).allowed)
        self.store.set_enabled(self.teacher.id, False)
        decision = self.decide("/api/state", "GET", self.teacher_session)
        self.assertFalse(decision.allowed,
                         "a disabled account must not survive in an open session")

    def test_a_disabled_account_is_refused_even_if_its_session_was_not_revoked(self):
        """The second line of defence, tested on its own.

        `set_enabled` revokes the account's sessions, so the test above passes
        even when the enabled-check inside resolve_session is deleted -- a
        mutation run proved exactly that. The two are independent protections
        and a surviving session must be refused on its own merits, because
        revocation is the thing most likely to be missed by a future code path
        that disables an account another way.
        """
        self.teacher.enabled = False          # disabled without revoking anything
        account, session = self.store.resolve_session(self.teacher_session.token)
        self.assertIsNone(account,
                          "a live session for a disabled account must not resolve")
        self.assertFalse(self.decide("/api/state", "GET", self.teacher_session).allowed)

    def test_a_deleted_account_is_refused_even_if_its_session_survives(self):
        """Same argument for deletion: the session must not outlive the account."""
        with self.store._lock:
            del self.store._accounts[self.teacher.id]
        account, _session = self.store.resolve_session(self.teacher_session.token)
        self.assertIsNone(account)
        self.assertFalse(self.decide("/api/state", "GET", self.teacher_session).allowed)

    def test_changing_a_password_ends_every_other_session(self):
        second = self.store.open_session(self.teacher.id)
        self.store.set_password(self.teacher.id, "a-new-password-1")
        for session in (self.teacher_session, second):
            self.assertFalse(self.decide("/api/state", "GET", session).allowed)

    def test_a_permission_change_takes_effect_without_a_new_login(self):
        allowed = self.decide("/api/route", "POST", self.teacher_session,
                              {"encoder": TEACHER_SOURCES[0],
                               "decoder": TEACHER_DISPLAYS[0]})
        self.assertTrue(allowed.allowed)
        self.store.set_permissions(self.teacher.id, Permissions(features=("av",)))
        after = self.decide("/api/route", "POST", self.teacher_session,
                            {"encoder": TEACHER_SOURCES[0],
                             "decoder": TEACHER_DISPLAYS[0]})
        self.assertFalse(after.allowed)

    def test_an_expired_session_is_refused(self):
        self.teacher_session.created_at -= omni_users.ABSOLUTE_TIMEOUT_SECONDS + 10
        self.assertFalse(self.decide("/api/state", "GET", self.teacher_session).allowed)

    def test_an_idle_session_is_refused(self):
        self.teacher_session.last_seen -= omni_users.IDLE_TIMEOUT_SECONDS + 10
        self.assertFalse(self.decide("/api/state", "GET", self.teacher_session).allowed)

    def test_a_made_up_token_is_refused(self):
        class Fake:
            token = "not-a-real-token"
            csrf_token = "not-a-real-csrf"
        self.assertFalse(self.decide("/api/state", "GET", Fake()).allowed)

    def test_logging_in_twice_issues_two_distinct_sessions(self):
        """A phone and a laptop are the ordinary case."""
        first = self.store.authenticate("teacher", "teacher-password-1")
        second = self.store.authenticate("teacher", "teacher-password-1")
        self.assertTrue(first["ok"] and second["ok"])
        self.assertNotEqual(first["token"], second["token"])
        self.assertNotEqual(first["csrf_token"], second["csrf_token"])


class CsrfTests(GateTestBase):
    def test_a_state_changing_request_without_a_csrf_token_is_refused(self):
        decision = self.decide("/api/route", "POST", self.teacher_session,
                               {"encoder": TEACHER_SOURCES[0],
                                "decoder": TEACHER_DISPLAYS[0]}, csrf=False)
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "csrf")

    def test_another_sessions_csrf_token_is_not_accepted(self):
        env = remote_env(cookie=self.teacher_session.token,
                         csrf=self.admin_session.csrf_token)
        decision = omni_gate.evaluate("/api/route", "POST", env,
                                      {"encoder": TEACHER_SOURCES[0],
                                       "decoder": TEACHER_DISPLAYS[0]},
                                      self.store)
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "csrf")

    def test_a_read_does_not_require_a_csrf_token(self):
        self.assertTrue(self.decide("/api/state", "GET", self.teacher_session,
                                    csrf=False).allowed)


class AuthenticationTests(GateTestBase):
    def test_a_wrong_password_is_refused_and_says_nothing_useful(self):
        result = self.store.authenticate("teacher", "wrong")
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "invalid username or password")

    def test_an_unknown_username_gives_the_same_answer_as_a_wrong_password(self):
        unknown = self.store.authenticate("nobody", "whatever")
        wrong = self.store.authenticate("teacher", "wrong")
        self.assertEqual(unknown["error"], wrong["error"])

    def test_a_disabled_account_cannot_log_in(self):
        self.store.set_enabled(self.teacher.id, False)
        self.assertFalse(self.store.authenticate("teacher", "teacher-password-1")["ok"])

    def test_repeated_failures_are_throttled(self):
        for _ in range(omni_users.MAX_FAILURES):
            self.store.authenticate("teacher", "wrong", source="10.0.0.9")
        result = self.store.authenticate("teacher", "teacher-password-1",
                                         source="10.0.0.9")
        self.assertFalse(result["ok"])
        self.assertTrue(result.get("throttled"),
                        "a correct password must not bypass the lockout")

    def test_throttling_is_per_source_as_well_as_per_username(self):
        for _ in range(omni_users.MAX_FAILURES):
            self.store.authenticate("teacher", "wrong", source="10.0.0.9")
        other = self.store.authenticate("teacher", "teacher-password-1",
                                        source="10.0.0.10")
        self.assertTrue(other["ok"], "an unrelated client must not be locked out")

    def test_a_short_password_is_refused_at_creation(self):
        self.assertFalse(self.store.create("weak", "short", omni_users.ROLE_USER)["ok"])


class CredentialLeakTests(GateTestBase):
    """No password reaches a response, a log, or the audit trail."""

    CANARY = "canary-must-never-appear-1"

    def test_a_password_never_appears_in_any_account_representation(self):
        self.store.create("victim", self.CANARY, omni_users.ROLE_USER)
        account = self.store.find_by_username("victim")
        for blob in (repr(account.public()), repr(self.store.list_accounts())):
            self.assertNotIn(self.CANARY, blob)
            self.assertNotIn("password_hash", blob,
                             "a hash must not be published either")

    def test_the_stored_hash_does_not_contain_the_password(self):
        self.store.create("victim", self.CANARY, omni_users.ROLE_USER)
        account = self.store.find_by_username("victim")
        self.assertNotIn(self.CANARY, account.password_hash)
        self.assertTrue(omni_users.verify_password(account.password_hash, self.CANARY))

    def test_the_audit_trail_redacts_every_secret_bearing_field(self):
        self.store.audit("test.event", actor=self.admin, detail={
            "password": self.CANARY, "pwd": self.CANARY,
            "fallback_password": self.CANARY, "token": self.CANARY,
            "csrf_token": self.CANARY, "password_hash": self.CANARY,
            "nested": {"new_password": self.CANARY},
            "listed": [{"secret": self.CANARY}],
            "harmless": "kept"})
        entries = repr(self.store.audit_entries())
        self.assertNotIn(self.CANARY, entries, "a credential reached the audit log")
        self.assertIn("kept", entries, "redaction must not eat ordinary detail")

    def test_the_users_file_on_disk_holds_no_plaintext_password(self):
        self.store.create("victim", self.CANARY, omni_users.ROLE_USER)
        on_disk = (self.dir / "users.json").read_text(encoding="utf-8")
        self.assertNotIn(self.CANARY, on_disk)

    def test_the_audit_file_on_disk_holds_no_plaintext_password(self):
        self.store.authenticate("teacher", self.CANARY, source="10.0.0.1")
        on_disk = (self.dir / "audit.json").read_text(encoding="utf-8")
        self.assertNotIn(self.CANARY, on_disk)


class PersistenceTests(GateTestBase):
    def test_accounts_survive_a_restart_and_sessions_do_not(self):
        token = self.teacher_session.token
        reopened = UserStore(self.dir / "users.json", self.dir / "audit.json")
        self.assertIsNotNone(reopened.find_by_username("teacher"))
        account, session = reopened.resolve_session(token)
        self.assertIsNone(account,
                          "sessions are in memory and must not survive a restart")

    def test_permissions_survive_a_restart(self):
        reopened = UserStore(self.dir / "users.json")
        teacher = reopened.find_by_username("teacher")
        self.assertTrue(teacher.permissions.has_feature("av"))
        self.assertTrue(teacher.permissions.may_control_destination(TEACHER_DISPLAYS[0]))
        self.assertFalse(teacher.permissions.may_control_destination(SOMEONE_ELSES_DISPLAY))

    def test_a_missing_store_is_empty_rather_than_permissive(self):
        empty = UserStore(self.dir / "does-not-exist.json")
        self.assertTrue(empty.is_empty)
        self.assertFalse(empty.has_admin)

    def test_a_corrupt_store_quarantines_and_degrades_to_empty(self):
        path = self.dir / "broken.json"
        path.write_text("{ not json", encoding="utf-8")
        store = UserStore(path)
        self.assertTrue(store.is_empty, "a corrupt store must not crash startup")
        self.assertTrue(path.with_suffix(".corrupt").exists())

    def test_the_last_administrator_cannot_be_removed(self):
        """Locking everyone out is not a state the product should reach."""
        self.assertFalse(self.store.set_enabled(self.admin.id, False)["ok"])
        self.assertFalse(self.store.delete(self.admin.id)["ok"])
        self.assertFalse(self.store.set_role(self.admin.id, omni_users.ROLE_USER)["ok"])


class RemoteDisabledTests(GateTestBase):
    def test_disabling_remote_access_revokes_every_session(self):
        self.assertEqual(self.store.active_session_count(), 2)
        self.store.revoke_every_session()
        self.assertEqual(self.store.active_session_count(), 0)
        self.assertFalse(self.decide("/api/state", "GET", self.teacher_session).allowed)

    def test_local_use_is_unaffected_by_revoking_remote_sessions(self):
        self.store.revoke_every_session()
        self.assertTrue(
            omni_gate.evaluate("/api/scan", "POST", local_env(), {}, self.store).allowed)


if __name__ == "__main__":
    unittest.main()
