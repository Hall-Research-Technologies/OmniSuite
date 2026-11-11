"""Account, permission and link lifecycle, exercised through the real application.

Every case here was written against a defect class found in acceptance testing:
an administrator changes an account and a session that is already open keeps
acting on what it was given at sign-in. The properties:

  * The server authorises from the account as it is at THAT request, so a
    removed grant is refused immediately and an added one appears at the next
    read -- with no sign-out, restart or new password.
  * A group's membership is read when it is used, never copied into a grant.
  * Regenerating a Direct Access link kills the old one and opens the new one.
  * A password change ends password sessions and leaves the link alone.
  * Deleting or disabling an account ends everything it could reach.
  * Clear Units removes the devices AND every reference to them, atomically.

Nothing here contacts hardware; the route engine is stubbed.
"""
from __future__ import annotations

import copy
import re
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("OMNI_SMOKE", "1")

import omni_gate                                     # noqa: E402
import omni_groups                                   # noqa: E402
import omni_remote                                   # noqa: E402
import omni_users                                    # noqa: E402
from omni_users import Permissions                   # noqa: E402
from tests import test_user_routing as harness       # noqa: E402
from tests.test_display_groups import (              # noqa: E402
    ROOM_A, ROOM_B, ROOM_C, SRC, UNITS, canonical)

REMOTE = {omni_remote.LISTENER_KEY: omni_remote.REMOTE}
FRONT = harness.FRONT_DISPLAY


class LifecycleBase(harness.UserRoutingTestBase):
    def setUp(self):
        super().setUp()
        import OmniMatrix_upgrade_server_v7_6y as srv
        self._patch_registered("load_cache", lambda: copy.deepcopy(UNITS))
        self.groups = omni_groups.GroupStore(self.dir / "display_groups.json",
                                             canonical)
        self._patch_registered("groups", self.groups)
        self.group = self.groups.create("Front rooms", [ROOM_A, ROOM_B])["group"]
        # The module-level stores the Clear Units path uses.
        original = srv._display_groups
        self.addCleanup(lambda o=original: setattr(srv, "_display_groups", o))
        srv._display_groups = self.groups
        self.aliases = srv._device_aliases
        saved = self.aliases.all()
        self.addCleanup(lambda: self.aliases.restore_all(saved))
        self.aliases.restore_all({})
        for name in ("CACHE", "SCAN_RESULTS", "CSV_VIEW"):
            original = getattr(srv, name)
            self.addCleanup(lambda n=name, o=original: setattr(srv, n, o))
            setattr(srv, name, self.dir / original.name)
        srv._save_cache(copy.deepcopy(UNITS))
        self.teacher = self.store.find_by_username("teacher")

    def grant(self, **kw):
        base = {"features": ("av",), "sources": [SRC],
                "destinations": [FRONT], "groups": []}
        base.update(kw)
        self.store.set_permissions(self.teacher.id, Permissions(**base))
        self.teacher = self.store.find_by_username("teacher")

    def state(self, session):
        reply = self.get("/api/user/state", session)
        self.assertEqual(reply.status_code, 200, reply.get_data(as_text=True))
        return reply.get_json()

    def admin_post(self, path, body):
        return self.post(path, body, self.session_for(self.admin))

    def link_login(self, token):
        client = self.srv.app.test_client()
        return client, client.get("/access/" + token, environ_base=REMOTE)


class LivePermissionTests(LifecycleBase):
    def test_a_session_that_was_open_sees_a_changed_grant(self):
        session = self.session_for(self.teacher)
        self.grant(destinations=[FRONT])
        self.assertEqual([d["id"] for d in self.state(session)["destinations"]],
                         [FRONT])
        # The administrator removes the display and adds a group.
        self.grant(destinations=[], groups=[self.group["id"]])
        after = self.state(session)             # the SAME session, still valid
        self.assertEqual(after["destinations"], [])
        self.assertEqual([g["id"] for g in after["groups"]], [self.group["id"]])

    def test_a_removed_display_is_refused_server_side_with_the_old_session(self):
        session = self.session_for(self.teacher)
        self.assertEqual(self.route(session).status_code, 200)
        self.grant(destinations=[])
        calls = len(self.routes)
        reply = self.route(session)
        self.assertEqual(reply.status_code, 403)
        self.assertEqual(len(self.routes), calls, "nothing reached the engine")

    def test_the_same_holds_for_a_direct_access_session(self):
        token = self.store.enable_direct_access(
            self.teacher.id, actor=self.admin)["token"]
        client, reply = self.link_login(token)
        self.assertEqual(reply.status_code, 200)
        self.assertEqual(client.get("/api/user/state", environ_base=REMOTE
                                    ).get_json()["destinations"][0]["id"], FRONT)
        self.grant(destinations=[])
        # Still signed in, and now seeing the new truth.
        after = client.get("/api/user/state", environ_base=REMOTE)
        self.assertEqual(after.status_code, 200)
        self.assertEqual(after.get_json()["destinations"], [])
        csrf = client.get("/api/auth/session", environ_base=REMOTE).get_json()
        again = client.post(
            "/api/user/route", environ_base=REMOTE,
            json={"source": SRC, "destination": FRONT, "mode": "av"},
            headers={"X-OmniSuite-CSRF": csrf.get("csrf_token") or ""})
        self.assertEqual(again.status_code, 403)
        self.assertEqual(self.routes, [])

    def test_a_permission_change_does_not_sign_anyone_out(self):
        session = self.session_for(self.teacher)
        self.grant(sources=[])
        self.assertIsNotNone(self.store.resolve_session(session.token)[0])

    def test_the_revision_changes_when_the_grants_do_and_only_then(self):
        session = self.session_for(self.teacher)
        rev = lambda: self.get("/api/user/revision", session).get_json()["revision"]
        first = rev()
        self.assertEqual(first, self.state(session)["revision"])
        self.assertEqual(first, rev())
        self.grant(destinations=[])
        self.assertNotEqual(first, rev())

    def test_each_part_of_the_grant_moves_the_revision_on_its_own(self):
        """One field at a time, so none can hide behind another changing too."""
        session = self.session_for(self.teacher)
        rev = lambda: self.get("/api/user/revision", session).get_json()["revision"]
        base = {"features": ("av",), "sources": [SRC], "destinations": [FRONT],
                "groups": []}
        self.grant(**base)
        seen = {rev()}
        for change in ({"destinations": []},
                       {"destinations": [FRONT, ROOM_A]},
                       {"sources": []},
                       {"sources": [SRC, ROOM_B]},
                       {"features": ("av", "usb")},
                       {"groups": [self.group["id"]]}):
            self.grant(**base)
            before = rev()
            self.grant(**dict(base, **change))
            after = rev()
            with self.subTest(change=sorted(change)):
                self.assertNotEqual(before, after)
                self.assertNotIn(after, seen - {before}, "digests must differ per state")
            seen.add(after)

    def test_the_revision_follows_group_membership_and_names(self):
        self.grant(groups=[self.group["id"]])
        session = self.session_for(self.teacher)
        rev = lambda: self.get("/api/user/revision", session).get_json()["revision"]
        a = rev()
        self.groups.update(self.group["id"], members=[ROOM_B, ROOM_C])
        b = rev()
        self.assertNotEqual(a, b)
        self.aliases.set_alias(FRONT, "Lectern")
        self.assertNotEqual(b, rev())

    def test_the_revision_carries_no_grant_and_needs_a_session(self):
        session = self.session_for(self.teacher)
        text = self.get("/api/user/revision", session).get_data(as_text=True)
        for secret in (SRC, FRONT, self.group["id"]):
            self.assertNotIn(secret, text)
        anonymous = self.srv.app.test_client().get(
            "/api/user/revision", environ_base=REMOTE)
        self.assertIn(anonymous.status_code, (401, 403))


class SimultaneousClientsTests(LifecycleBase):
    """Three independent cookie jars at once: Admin, User and Direct Access."""

    def test_both_restricted_clients_converge_when_the_admin_edits_over_http(self):
        token = self.store.enable_direct_access(
            self.teacher.id, actor=self.admin)["token"]
        direct, opened = self.link_login(token)
        self.assertEqual(opened.status_code, 200)
        user_session = self.session_for(self.teacher)
        user = self.srv.app.test_client()
        user.set_cookie(omni_gate.SESSION_COOKIE, user_session.token)
        admin = self.srv.app.test_client()
        admin_session = self.session_for(self.admin)
        admin.set_cookie(omni_gate.SESSION_COOKIE, admin_session.token)

        def view(client):
            return client.get("/api/user/state", environ_base=REMOTE).get_json()

        for client in (user, direct):
            self.assertEqual([d["id"] for d in view(client)["destinations"]], [FRONT])
        reply = admin.post(
            "/api/admin/users/" + self.teacher.id, environ_base=REMOTE,
            json={"action": "set_permissions", "permissions": {
                "features": ["av"], "sources": [SRC], "destinations": [],
                "groups": [self.group["id"]]}},
            headers={"X-OmniSuite-CSRF": admin_session.csrf_token})
        self.assertEqual(reply.status_code, 200)
        for client in (user, direct):
            seen = view(client)
            self.assertEqual(seen["destinations"], [])
            self.assertEqual([g["id"] for g in seen["groups"]], [self.group["id"]])
        # And the admin client was not disturbed by editing someone else.
        self.assertEqual(admin.get("/api/admin/users", environ_base=REMOTE
                                   ).status_code, 200)


class GroupResolvesLiveTests(LifecycleBase):
    def test_membership_edits_apply_without_recreating_the_user(self):
        self.grant(destinations=[], groups=[self.group["id"]])
        session = self.session_for(self.teacher)
        body = {"source": SRC, "group": self.group["id"]}
        self.assertEqual(self.post("/api/user/route_group", body, session
                                   ).get_json()["routed"], 2)
        self.assertEqual(sorted(r["decoder"] for r in self.routes),
                         ["192.0.2.31", "192.0.2.32"])
        # Group edited: A out, C in.
        self.admin_post("/api/admin/groups/" + self.group["id"],
                        {"action": "set_members", "members": [ROOM_B, ROOM_C]})
        self.routes.clear()
        self.post("/api/user/route_group", body, session)
        self.assertEqual(sorted(r["decoder"] for r in self.routes),
                         ["192.0.2.32", "192.0.2.33"])

    def test_holding_a_group_gives_no_individual_control_of_members(self):
        self.grant(destinations=[], groups=[self.group["id"]])
        session = self.session_for(self.teacher)
        self.assertEqual(self.state(session)["destinations"], [])
        self.assertEqual(self.route(session, destination=ROOM_A).status_code, 403)

    def test_removing_the_group_grant_refuses_the_next_group_route(self):
        self.grant(groups=[self.group["id"]])
        session = self.session_for(self.teacher)
        self.grant(groups=[])
        reply = self.post("/api/user/route_group",
                          {"source": SRC, "group": self.group["id"]}, session)
        self.assertEqual(reply.status_code, 403)
        self.assertEqual(self.routes, [])


class PasswordAndRoleTests(LifecycleBase):
    def login(self, username, password):
        client = self.srv.app.test_client()
        return client.post("/api/auth/login", json={"username": username,
                           "password": password}, environ_base=REMOTE)

    def test_old_password_fails_new_succeeds_and_sessions_end(self):
        self.assertEqual(self.login("teacher", "teacher-password-1").status_code, 200)
        session = self.session_for(self.teacher)
        reply = self.admin_post("/api/admin/users/" + self.teacher.id, {
            "action": "set_password", "password": "a-brand-new-pass-2"})
        self.assertEqual(reply.status_code, 200)
        self.assertIn(self.login("teacher", "teacher-password-1").status_code,
                      (400, 401, 403))
        self.assertEqual(self.login("teacher", "a-brand-new-pass-2").status_code, 200)
        self.assertEqual(self.get("/api/user/state", session).status_code, 401)

    def test_a_password_change_leaves_the_direct_link_alone(self):
        token = self.store.enable_direct_access(
            self.teacher.id, actor=self.admin)["token"]
        client, _ = self.link_login(token)
        self.admin_post("/api/admin/users/" + self.teacher.id, {
            "action": "set_password", "password": "a-brand-new-pass-2"})
        self.assertEqual(self.store.direct_access_token(self.teacher.id), token)
        self.assertEqual(self.link_login(token)[1].status_code, 200)

    def test_a_password_change_ends_password_sessions_but_not_a_link_session(self):
        token = self.store.enable_direct_access(
            self.teacher.id, actor=self.admin)["token"]
        link_client, _ = self.link_login(token)
        password_session = self.session_for(self.teacher)
        self.assertEqual(link_client.get("/api/user/state",
                                         environ_base=REMOTE).status_code, 200)
        self.admin_post("/api/admin/users/" + self.teacher.id, {
            "action": "set_password", "password": "a-brand-new-pass-2"})
        self.assertEqual(self.get("/api/user/state", password_session).status_code, 401,
                         "the password session must end")
        self.assertEqual(link_client.get("/api/user/state",
                                         environ_base=REMOTE).status_code, 200,
                         "a session the link opened was never authenticated by the password")

    def test_a_role_change_ends_link_sessions_too(self):
        token = self.store.enable_direct_access(
            self.teacher.id, actor=self.admin)["token"]
        link_client, _ = self.link_login(token)
        self.admin_post("/api/admin/users/" + self.teacher.id,
                        {"action": "set_role", "role": "admin"})
        self.assertEqual(link_client.get("/api/user/state",
                                         environ_base=REMOTE).status_code, 401)

    def test_user_to_admin_ends_the_session_and_drops_the_link(self):
        token = self.store.enable_direct_access(
            self.teacher.id, actor=self.admin)["token"]
        session = self.session_for(self.teacher)
        self.admin_post("/api/admin/users/" + self.teacher.id,
                        {"action": "set_role", "role": "admin"})
        self.assertEqual(self.get("/api/user/state", session).status_code, 401)
        self.assertEqual(self.link_login(token)[1].status_code, 404)

    def test_admin_to_user_leaves_no_admin_access(self):
        self.store.create("second", "second-admin-pass-1", omni_users.ROLE_ADMIN)
        second = self.store.find_by_username("second")
        session = self.session_for(second)
        self.assertEqual(self.get("/api/admin/users", session).status_code, 200)
        self.admin_post("/api/admin/users/" + second.id,
                        {"action": "set_role", "role": "user"})
        self.assertIn(self.get("/api/admin/users", session).status_code, (401, 403))

    def test_the_last_administrator_cannot_be_demoted_or_deleted(self):
        for body in ({"action": "set_role", "role": "user"}, {"action": "delete"},
                     {"action": "set_enabled", "enabled": False}):
            self.assertEqual(self.admin_post(
                "/api/admin/users/" + self.admin.id, body).status_code, 400)


class DirectAccessLifecycleTests(LifecycleBase):
    def direct(self, action):
        return self.admin_post("/api/admin/users/%s/direct_access" % self.teacher.id,
                               {"action": action})

    def test_the_whole_lifecycle(self):
        self.assertEqual(self.link_login("not-a-real-token")[1].status_code, 404)
        a = self.direct("enable").get_json()
        token_a = self.store.direct_access_token(self.teacher.id)
        self.assertTrue(token_a)
        self.assertTrue(a["ok"])
        client_a, opened = self.link_login(token_a)
        self.assertEqual(opened.status_code, 200)
        # Regenerate: A fails at once, its session ends, B opens.
        b = self.direct("regenerate").get_json()
        token_b = self.store.direct_access_token(self.teacher.id)
        self.assertNotEqual(token_a, token_b)
        self.assertTrue(b["ok"])
        self.assertEqual(self.link_login(token_a)[1].status_code, 404)
        self.assertEqual(client_a.get("/api/user/state",
                                      environ_base=REMOTE).status_code, 401)
        client_b, opened = self.link_login(token_b)
        self.assertEqual(opened.status_code, 200)
        # B carries only the account's CURRENT permissions.
        self.grant(destinations=[])
        self.assertEqual(client_b.get("/api/user/state", environ_base=REMOTE
                                      ).get_json()["destinations"], [])
        # The copy-link read returns the live token.
        shown = self.get("/api/admin/users/%s/direct_access" % self.teacher.id,
                         self.session_for(self.admin)).get_json()
        self.assertTrue(shown["recoverable"])
        # Disable, then re-enable: only a NEW link works.
        self.direct("disable")
        self.assertEqual(self.link_login(token_b)[1].status_code, 404)
        self.direct("enable")
        token_c = self.store.direct_access_token(self.teacher.id)
        self.assertNotIn(token_c, (token_a, token_b))
        self.assertEqual(self.link_login(token_c)[1].status_code, 200)
        self.assertEqual(self.link_login(token_b)[1].status_code, 404)

    def test_regenerating_when_off_is_refused_not_a_500(self):
        self.assertEqual(self.direct("regenerate").status_code, 400)

    def test_a_link_never_resurrects_a_deleted_account(self):
        self.direct("enable")
        token = self.store.direct_access_token(self.teacher.id)
        self.admin_post("/api/admin/users/" + self.teacher.id,
                        {"action": "set_enabled", "enabled": False})
        self.assertEqual(self.link_login(token)[1].status_code, 404)
        self.admin_post("/api/admin/users/" + self.teacher.id, {"action": "delete"})
        self.assertEqual(self.link_login(token)[1].status_code, 404)

    def test_an_administrator_cannot_hold_a_link(self):
        reply = self.admin_post("/api/admin/users/%s/direct_access" % self.admin.id,
                                {"action": "enable"})
        self.assertEqual(reply.status_code, 400)


class DeleteAccountTests(LifecycleBase):
    def test_everything_the_account_could_reach_is_gone(self):
        self.grant(groups=[self.group["id"]])
        self.store.enable_direct_access(self.teacher.id, actor=self.admin)
        token = self.store.direct_access_token(self.teacher.id)
        password_session = self.session_for(self.teacher)
        client, _ = self.link_login(token)
        self.admin_post("/api/admin/users/" + self.teacher.id, {"action": "delete"})
        self.assertEqual(self.get("/api/user/state", password_session).status_code, 401)
        self.assertEqual(client.get("/api/user/state", environ_base=REMOTE).status_code, 401)
        self.assertEqual(self.link_login(token)[1].status_code, 404)
        listing = self.get("/api/admin/users", self.session_for(self.admin)).get_json()
        self.assertNotIn("teacher", [u["username"] for u in listing["users"]])
        self.assertIsNone(self.store.get(self.teacher.id))


class AccountValidationTests(LifecycleBase):
    def create(self, **kw):
        body = {"username": "new-user", "password": "a-long-enough-pass",
                "role": "user", "permissions": {}}
        body.update(kw)
        return self.admin_post("/api/admin/users", body)

    def test_create_validation_and_read(self):
        self.assertEqual(self.create().status_code, 200)
        self.assertEqual(self.create().status_code, 400, "duplicate")
        self.assertEqual(self.create(username="").status_code, 400)
        self.assertEqual(self.create(username="short", password="short").status_code, 400)
        for bad in ("tab\there", "line\nbreak", "nul\x00name", "x" * 65):
            self.assertEqual(self.create(username=bad).status_code, 400, repr(bad))
        self.assertEqual(self.create(username="x" * 64).status_code, 200)
        self.assertEqual(self.create(username="bad-role", role="root").status_code, 400)
        listing = self.get("/api/admin/users", self.session_for(self.admin)).get_json()
        self.assertIn("new-user", [u["username"] for u in listing["users"]])
        text = str(listing)
        self.assertNotIn("password_hash", text)

    def test_an_unknown_account_is_refused_not_a_500(self):
        for action in ("set_enabled", "set_role", "set_password", "set_permissions",
                       "delete"):
            reply = self.admin_post("/api/admin/users/nobody",
                                    {"action": action, "role": "user",
                                     "password": "long-enough-pass", "enabled": True})
            self.assertEqual(reply.status_code, 400, action)
        self.assertEqual(self.admin_post("/api/admin/users/nobody",
                                         {"action": "bogus"}).status_code, 400)


class FriendlyNameTests(LifecycleBase):
    def test_set_change_remove_and_never_authorisation(self):
        session = self.session_for(self.teacher)
        name = lambda: self.state(session)["destinations"][0]["name"]
        fallback = name()
        self.admin_post("/api/admin/aliases", {"aliases": {FRONT: "Lectern"}})
        self.assertEqual(name(), "Lectern")
        self.admin_post("/api/admin/aliases", {"aliases": {FRONT: "Podium"}})
        self.assertEqual(name(), "Podium")
        self.admin_post("/api/admin/aliases", {"aliases": {FRONT: ""}})
        self.assertEqual(name(), fallback)
        # A label is not an identity: routing by it reaches nothing.
        self.aliases.set_alias(ROOM_A, "Lectern")
        self.assertIn(self.route(session, destination="Lectern").status_code,
                      (400, 403))
        self.assertEqual(self.routes, [])


class ClearUnitsCascadeTests(LifecycleBase):
    """The full clear: Remote Access off, so nothing is protected.

    With Remote Access on, Clear Units is assignment aware and keeps what a
    User or group refers to; that is test_clear_inventory's subject. The server
    decides from the listener, so these tests say the listener is off.
    """

    def setUp(self):
        super().setUp()
        self.grant(destinations=[FRONT], groups=[self.group["id"]])
        self.aliases.set_alias(FRONT, "Lectern")
        self.aliases.set_alias(ROOM_A, "Room A")
        self.token = self.store.enable_direct_access(
            self.teacher.id, actor=self.admin)["token"]
        patcher = mock.patch.object(self.srv, "remote_access_enabled",
                                    return_value=False)
        patcher.start()
        self.addCleanup(patcher.stop)

    def clear(self):
        return self.admin_post("/api/clear_units", {})

    def test_every_store_agrees_afterwards(self):
        session = self.session_for(self.teacher)
        client, _ = self.link_login(self.token)
        reply = self.clear()
        self.assertEqual(reply.status_code, 200, reply.get_data(as_text=True))
        self.assertEqual(self.srv._load_cache() or [], [])
        teacher = self.store.find_by_username("teacher")
        self.assertEqual((set(teacher.permissions.sources),
                          set(teacher.permissions.destinations)), (set(), set()))
        self.assertEqual(self.groups.get(self.group["id"])["members"], [])
        self.assertEqual(self.aliases.all(), {})
        for visible in (self.state(session),
                        client.get("/api/user/state", environ_base=REMOTE).get_json()):
            self.assertEqual((visible["sources"], visible["destinations"]), ([], []))
        self.assertEqual(self.route(session).status_code, 403)
        self.assertEqual(self.routes, [])

    def test_the_old_group_route_reaches_nothing(self):
        session = self.session_for(self.teacher)
        self.clear()
        self.post("/api/user/route_group",
                  {"source": SRC, "group": self.group["id"]}, session)
        self.assertEqual(self.routes, [])

    def test_a_rescan_makes_equipment_available_for_new_assignment_only(self):
        self.clear()
        self.srv._save_cache(copy.deepcopy(UNITS))
        session = self.session_for(self.store.find_by_username("teacher"))
        self.assertEqual(self.state(session)["destinations"], [], "not restored")
        self.grant(destinations=[FRONT])
        self.assertEqual(len(self.state(session)["destinations"]), 1)

    def test_a_failure_partway_leaves_no_hybrid(self):
        before_grants = self.store.grants_snapshot()
        before_members = self.groups.members_snapshot()
        before_names = self.aliases.all()

        def boom(identities=None):
            raise OSError("disk full")
        original = self.groups.forget_members
        self.groups.forget_members = boom
        self.addCleanup(lambda: setattr(self.groups, "forget_members", original))
        reply = self.clear()
        self.assertEqual(reply.status_code, 503)
        self.assertEqual(self.store.grants_snapshot(), before_grants)
        self.assertEqual(self.groups.members_snapshot(), before_members)
        self.assertEqual(self.aliases.all(), before_names)
        self.assertEqual(len(self.srv._load_cache() or []), len(UNITS),
                         "the inventory is only cleared after the cascade held")

    def test_it_is_audited_and_the_preview_route_is_gone(self):
        self.clear()
        events = [e["event"] for e in self.store.audit_entries(50)]
        self.assertIn("inventory.cleared", events)
        self.assertEqual(self.get("/api/clear_units/preview",
                                  self.session_for(self.admin)).status_code, 404)

    def test_it_is_administrative(self):
        reply = self.post("/api/clear_units", {}, self.session_for(self.teacher))
        self.assertEqual(reply.status_code, 403)
        self.assertEqual(len(self.srv._load_cache() or []), len(UNITS))


class NavigationIsServedWithThePageTests(LifecycleBase):
    def test_the_hint_is_in_the_first_bytes_when_remote_access_is_on(self):
        session = self.session_for(self.admin)
        self.client.set_cookie(omni_gate.SESSION_COOKIE, session.token)
        html = self.client.get("/", environ_base=REMOTE).get_data(as_text=True)
        self.assertIn('<meta name="omni-remote-access" content="1">', html)
        self.assertIn('name="omni-signed-in" content="principal"', html)
        self.assertLess(html.index("omni-remote-access"), html.index("adminnav.js"))

    def test_the_hint_says_off_when_it_is_off(self):
        self.srv._remote_listener._server = None
        html = self.client.get("/").get_data(as_text=True)
        self.assertIn('name="omni-remote-access" content="0"', html[:600])


class NoStaleAuthorisationStateTests(LifecycleBase):
    """Session/cache audit: nothing a browser or the server holds can grant."""

    def test_api_answers_are_never_cacheable(self):
        session = self.session_for(self.teacher)
        for path in ("/api/user/state", "/api/user/revision", "/api/auth/session"):
            reply = self.get(path, session)
            self.assertIn("no-store", reply.headers.get("Cache-Control", ""), path)

    def test_a_session_holds_no_copy_of_the_permissions(self):
        session = self.session_for(self.teacher)
        self.assertFalse(any(hasattr(session, name) for name in
                             ("permissions", "sources", "destinations", "groups")))

    def test_the_account_is_re_read_for_every_request(self):
        session = self.session_for(self.teacher)
        first, _ = self.store.resolve_session(session.token)
        self.grant(sources=[])
        second, _ = self.store.resolve_session(session.token)
        self.assertEqual(set(second.permissions.sources), set())
        self.assertEqual(second.id, first.id)

    def test_no_page_script_stores_a_grant_in_the_browser(self):
        banned = ("permissions", "grants", "sources", "destinations", "groups")
        for path in list((ROOT / "ui").rglob("*.js")):
            text = path.read_text(encoding="utf-8")
            for line in text.splitlines():
                if "localStorage.setItem" in line or "sessionStorage.setItem" in line:
                    for word in banned:
                        self.assertNotIn(
                            "'%s'" % word, line,
                            "%s stores %s in the browser" % (path.name, word))

    def test_the_user_page_checks_a_revision_without_a_hot_timer(self):
        text = (ROOT / "ui" / "user" / "user.js").read_text(encoding="utf-8")
        self.assertIn("/api/user/revision", text)
        self.assertRegex(text, r"setInterval\(checkRevision, (\d+)\)")
        interval = int(re.search(r"setInterval\(checkRevision, (\d+)\)", text).group(1))
        self.assertGreaterEqual(interval, 30000)
        self.assertIn("document.hidden", text)


if __name__ == "__main__":
    unittest.main()
