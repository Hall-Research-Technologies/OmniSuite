"""What a restore does to the people who are signed in.

A restore replaces the account table. Sessions live in memory beside it, keyed by
account id, so a restore that swaps the table and leaves the sessions alone
decides who stays signed in by accident: an account the backup does not carry
is already refused (the session is resolved against the table on every request),
but an account the backup carries *differently* -- a different password, a link
that has been regenerated or turned off -- kept the session its old credential
opened.

    REMOVED account          the session ends          (the table no longer has it)
    CHANGED password         password sessions end     (the credential that opened them is gone)
    CHANGED or OFF link      link sessions end         (same, for the link)
    CHANGED role             every session ends
    CHANGED permissions      every session ends        (it is no longer the account that signed in)
    UNCHANGED account        the session stays         (a restore is not a sign-out of the room),
                             an administrator's included

A restore that cannot be sure what the process now holds ends every session.

The local operator keeps a way in whatever the backup held, because a request
from the OmniSuite computer is trusted by the socket it arrived on.

Real endpoints, throwaway data directory, network fenced.
"""
from __future__ import annotations

import copy
import io
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import omni_backup                                        # noqa: E402
import omni_users                                         # noqa: E402
from omni_users import Permissions                        # noqa: E402
from tests.test_acceptance_lifecycle import LifecycleBase, REMOTE   # noqa: E402
from tests.test_backup import PASSPHRASE, UNITS           # noqa: E402
from tests.test_display_groups import SRC                 # noqa: E402
from tests import test_user_routing as harness            # noqa: E402

FRONT = harness.FRONT_DISPLAY
NEW_PASSWORD = "a-different-password-9"


def drop_every_account(other):
    """A backup that holds no accounts at all.

    Written through the store's own table, because `delete` refuses to remove the
    last administrator -- which is right for a person at a keyboard and irrelevant
    to what a backup file can contain.
    """
    other._accounts.clear()


class RestoreSessionBase(LifecycleBase):
    """The lifecycle harness with the restore endpoint pointed at its directory."""

    def setUp(self):
        super().setUp()
        srv = self.srv
        saved = (srv._backup_data_dir, srv.BACKUP_SNAPSHOT_DIR,
                 self.aliases._path, srv._restart_required)

        def undo():
            srv._backup_data_dir, srv.BACKUP_SNAPSHOT_DIR = saved[0], saved[1]
            self.aliases._path = saved[2]
            srv._restart_required = saved[3]
            srv._restore_in_progress = False

        self.addCleanup(undo)
        srv._backup_data_dir = lambda: self.dir
        srv.BACKUP_SNAPSHOT_DIR = self.dir / "snapshot"
        self.aliases._path = self.dir / "device_aliases.json"
        self.work = Path(tempfile.mkdtemp(prefix="omni-restore-sessions-"))
        self.addCleanup(shutil.rmtree, self.work, ignore_errors=True)
        # What the application will write to disk now, so a backup of "the same
        # accounts" starts from the real thing.
        self.store._persist()

    # ---------------------------------------------------------- the backup
    def backup_of(self, mutate):
        """A sealed backup of the CURRENT accounts, after `mutate(other_store)`.

        The mutation runs on a second store over a copy of the file, so the live
        store is untouched until the restore replaces it.
        """
        source = self.work / "source"
        if source.exists():
            shutil.rmtree(source)
        source.mkdir(parents=True)
        shutil.copy2(self.store._path, source / "users.json")
        other = omni_users.UserStore(source / "users.json", source / "audit.json")
        mutate(other)
        other._persist()
        payload = omni_backup.build_payload(source, copy.deepcopy(UNITS),
                                            app_version="V1.2.0")
        return omni_backup.seal(payload, PASSPHRASE, app_version="V1.2.0")

    def restore(self, blob):
        """The restore, from the OmniSuite computer itself (no sign-in needed)."""
        client = self.srv.app.test_client()
        return client.post(
            "/api/backup/restore", content_type="multipart/form-data",
            data={"backup": (io.BytesIO(blob), "b.omnibak"),
                  "passphrase": PASSPHRASE})

    def restore_ok(self, blob):
        reply = self.restore(blob)
        self.assertEqual(reply.status_code, 200, reply.get_data(as_text=True))
        return reply.get_json()

    def alive(self, session):
        return self.store.resolve_session(session.token)[0] is not None

    def teacher_in(self, other):
        return other.find_by_username("teacher")


# --------------------------------------------------------------------------
class AccountsTheBackupDoesNotCarryTests(RestoreSessionBase):
    def test_a_removed_account_loses_its_password_session(self):
        session = self.session_for(self.teacher)
        self.restore_ok(self.backup_of(
            lambda o: o.delete(o.find_by_username("teacher").id)))
        self.assertFalse(self.alive(session))
        self.assertEqual(self.get("/api/user/state", session).status_code, 401)

    def test_a_removed_account_loses_its_direct_access_session_and_link(self):
        token = self.store.enable_direct_access(
            self.teacher.id, actor=self.admin)["token"]
        client, reply = self.link_login(token)
        self.assertEqual(reply.status_code, 200)
        self.assertEqual(client.get("/api/user/state", environ_base=REMOTE
                                    ).status_code, 200)
        self.restore_ok(self.backup_of(
            lambda o: o.delete(o.find_by_username("teacher").id)))
        self.assertEqual(client.get("/api/user/state", environ_base=REMOTE
                                    ).status_code, 401)
        _again, fresh = self.link_login(token)
        self.assertNotEqual(fresh.status_code, 200, "the old link still opens")

    def test_a_backup_with_no_accounts_ends_every_session(self):
        user, admin = self.session_for(self.teacher), self.session_for(self.admin)
        blob = self.backup_of(drop_every_account)
        self.restore_ok(blob)
        self.assertFalse(self.alive(user))
        self.assertFalse(self.alive(admin))
        self.assertEqual(self.store.active_session_count(), 0)


class AccountsTheBackupCarriesDifferentlyTests(RestoreSessionBase):
    def test_a_changed_password_ends_the_password_session(self):
        session = self.session_for(self.teacher)
        self.restore_ok(self.backup_of(
            lambda o: o.set_password(o.find_by_username("teacher").id, NEW_PASSWORD)))
        self.assertFalse(self.alive(session),
                         "the session the old password opened survived a restore "
                         "that replaced that password")

    def test_a_changed_password_does_not_end_the_link_session(self):
        """Different credentials, different lifecycle: the password is not the link."""
        token = self.store.enable_direct_access(
            self.teacher.id, actor=self.admin)["token"]
        client, _ = self.link_login(token)
        self.restore_ok(self.backup_of(
            lambda o: o.set_password(o.find_by_username("teacher").id, NEW_PASSWORD)))
        self.assertEqual(client.get("/api/user/state", environ_base=REMOTE
                                    ).status_code, 200)

    def test_a_regenerated_link_ends_the_old_link_session(self):
        token = self.store.enable_direct_access(
            self.teacher.id, actor=self.admin)["token"]
        client, _ = self.link_login(token)
        self.restore_ok(self.backup_of(
            lambda o: o.regenerate_direct_access(o.find_by_username("teacher").id)))
        self.assertEqual(client.get("/api/user/state", environ_base=REMOTE
                                    ).status_code, 401,
                         "a session opened by a link that the restore replaced "
                         "is still signed in")
        _again, fresh = self.link_login(token)
        self.assertNotEqual(fresh.status_code, 200)

    def test_a_link_the_backup_has_turned_off_ends_the_link_session(self):
        token = self.store.enable_direct_access(
            self.teacher.id, actor=self.admin)["token"]
        client, _ = self.link_login(token)
        self.restore_ok(self.backup_of(
            lambda o: o.disable_direct_access(o.find_by_username("teacher").id)))
        self.assertEqual(client.get("/api/user/state", environ_base=REMOTE
                                    ).status_code, 401)

    def test_a_disabled_account_in_the_backup_is_signed_out(self):
        session = self.session_for(self.teacher)
        self.restore_ok(self.backup_of(
            lambda o: o.set_enabled(o.find_by_username("teacher").id, False)))
        self.assertFalse(self.alive(session))

    def test_a_promotion_in_the_backup_ends_every_session_of_that_account(self):
        user = self.session_for(self.teacher)
        link = self.store.enable_direct_access(self.teacher.id, actor=self.admin)
        client, _ = self.link_login(link["token"])
        self.restore_ok(self.backup_of(
            lambda o: o.set_role(o.find_by_username("teacher").id,
                                 omni_users.ROLE_ADMIN)))
        self.assertFalse(self.alive(user))
        self.assertEqual(client.get("/api/user/state", environ_base=REMOTE
                                    ).status_code, 401)

    def test_an_unchanged_account_is_not_signed_out(self):
        teacher, admin = self.session_for(self.teacher), self.session_for(self.admin)
        self.restore_ok(self.backup_of(lambda o: None))
        self.assertTrue(self.alive(teacher),
                        "a restore that changed nothing about an account signed it out")
        self.assertTrue(self.alive(admin))

    def test_only_the_changed_account_loses_its_session(self):
        self.store.create("assistant", "assistant-password-1", omni_users.ROLE_USER,
                          Permissions(features=("av",), sources=[SRC],
                                      destinations=[FRONT]))
        assistant = self.store.find_by_username("assistant")
        a, t = self.session_for(assistant), self.session_for(self.teacher)
        self.store._persist()
        self.restore_ok(self.backup_of(
            lambda o: o.set_password(o.find_by_username("teacher").id, NEW_PASSWORD)))
        self.assertTrue(self.alive(a))
        self.assertFalse(self.alive(t))


class WhatAnOpenSessionIsGivenAfterwardsTests(RestoreSessionBase):
    """A restore that changes what an account may do ends its sessions, and the
    refusal is the server's. Authorisation is also read from the account on every
    request, so even a session that somehow survived would be refused; the second
    test reaches that line of defence on purpose by putting the session back."""

    def test_reduced_destinations_end_the_session_and_nothing_reaches_the_engine(self):
        session = self.session_for(self.teacher)
        self.assertEqual(self.route(session).status_code, 200)
        self.restore_ok(self.backup_of(lambda o: o.set_permissions(
            o.find_by_username("teacher").id,
            Permissions(features=("av",), sources=[SRC], destinations=[]))))
        self.assertFalse(self.alive(session),
                         "a reduced account kept the session it signed in with")
        calls = len(self.routes)
        self.assertEqual(self.route(session).status_code, 401)
        self.assertEqual(len(self.routes), calls, "a refused route reached the engine")

    def test_a_reduction_is_enforced_per_request_even_if_a_session_survives(self):
        session = self.session_for(self.teacher)
        before = dict(self.store._sessions)
        self.restore_ok(self.backup_of(lambda o: o.set_permissions(
            o.find_by_username("teacher").id,
            Permissions(features=("av",), sources=[SRC], destinations=[]))))
        self.store._sessions.update(before)
        self.assertTrue(self.alive(session))
        calls = len(self.routes)
        self.assertEqual(self.route(session).status_code, 403)
        self.assertEqual(len(self.routes), calls, "a refused route reached the engine")
        self.assertEqual(self.state(session)["destinations"], [])

    def test_a_removed_matrix_interface_takes_effect_on_the_next_request(self):
        self.store.set_permissions(self.teacher.id, Permissions(
            features=("av",), sources=[SRC], destinations=[FRONT],
            interfaces=["standard", "matrix"]))
        self.store._persist()
        session = self.session_for(self.store.find_by_username("teacher"))
        self.assertEqual(self.get("/api/user/matrix_state", session).status_code, 200)
        before = dict(self.store._sessions)
        self.restore_ok(self.backup_of(lambda o: o.set_permissions(
            o.find_by_username("teacher").id,
            Permissions(features=("av",), sources=[SRC], destinations=[FRONT],
                        interfaces=["standard"]))))
        self.assertFalse(self.alive(session), "a changed interface set kept the session")
        self.store._sessions.update(before)      # the per-request check, reached on purpose
        self.assertEqual(self.get("/api/user/matrix_state", session).status_code, 403)

    def test_the_revision_an_open_page_polls_moves_after_a_reducing_restore(self):
        session = self.session_for(self.teacher)
        before = self.get("/api/user/revision", session).get_json()["revision"]
        self.restore_ok(self.backup_of(lambda o: o.set_permissions(
            o.find_by_username("teacher").id,
            Permissions(features=("av",), sources=[], destinations=[]))))
        # The page's own poll is refused now, which is how an open page finds out.
        self.assertEqual(self.get("/api/user/revision", session).status_code, 401)
        fresh = self.session_for(self.store.find_by_username("teacher"))
        after = self.get("/api/user/revision", fresh).get_json()["revision"]
        self.assertNotEqual(before, after)


class EachPermissionFieldIsPartOfTheComparisonTests(RestoreSessionBase):
    """One field at a time, from a known grant, so a field the comparison forgot
    cannot hide behind another one that differs anyway."""

    BASE = {"features": ("av",), "sources": [SRC], "destinations": [FRONT],
            "groups": [], "interfaces": ["standard"]}
    ONE_FIELD_DIFFERENT = {
        "features": (),
        "sources": [],
        "destinations": [],
        "groups": ["some-group"],
        "interfaces": ["standard", "matrix"],
    }

    def check(self, field):
        self.store.set_permissions(self.teacher.id, Permissions(**self.BASE))
        self.store._persist()
        teacher = self.store.find_by_username("teacher")
        session = self.session_for(teacher)
        changed = dict(self.BASE, **{field: self.ONE_FIELD_DIFFERENT[field]})
        self.restore_ok(self.backup_of(lambda o: o.set_permissions(
            o.find_by_username("teacher").id, Permissions(**changed))))
        self.assertFalse(self.alive(session),
                         "a restore that changed only %s left the session open" % field)

    def test_features(self):
        self.check("features")

    def test_sources(self):
        self.check("sources")

    def test_destinations(self):
        self.check("destinations")

    def test_groups(self):
        self.check("groups")

    def test_interfaces(self):
        self.check("interfaces")

    def test_the_same_grant_changes_nothing(self):
        self.store.set_permissions(self.teacher.id, Permissions(**self.BASE))
        self.store._persist()
        session = self.session_for(self.store.find_by_username("teacher"))
        self.restore_ok(self.backup_of(lambda o: o.set_permissions(
            o.find_by_username("teacher").id, Permissions(**self.BASE))))
        self.assertTrue(self.alive(session))


class AdministratorSessionAcrossARestoreTests(RestoreSessionBase):
    """A signed-in administrator keeps their session across a restore only if that
    exact account is security-equivalent afterwards. Anything else ends it.

    The comparison is the server's, made against the table the session was opened
    against; the page's idea of who is signed in plays no part.
    """

    def admin_session(self):
        session = self.session_for(self.admin)
        self.assertEqual(self.get("/api/admin/users", session).status_code, 200,
                         "precondition: the administrator session works")
        return session

    def admin_in(self, other):
        return other.find_by_username(self.admin.username)

    def assertRevoked(self, session, what):
        self.assertFalse(self.alive(session), what)
        self.assertEqual(self.get("/api/admin/users", session).status_code, 401, what)

    def test_an_unchanged_administrator_stays_signed_in_and_still_administers(self):
        session = self.admin_session()
        self.restore_ok(self.backup_of(lambda o: None))
        self.assertTrue(self.alive(session))
        self.assertEqual(self.get("/api/admin/users", session).status_code, 200)

    def test_an_unchanged_administrator_survives_a_restore_that_changes_someone_else(self):
        session = self.admin_session()
        self.restore_ok(self.backup_of(
            lambda o: o.set_password(o.find_by_username("teacher").id, NEW_PASSWORD)))
        self.assertTrue(self.alive(session))
        self.assertEqual(self.get("/api/admin/users", session).status_code, 200)

    def test_a_password_changed_by_the_restore_revokes_the_administrator(self):
        session = self.admin_session()
        self.restore_ok(self.backup_of(
            lambda o: o.set_password(self.admin_in(o).id, NEW_PASSWORD)))
        self.assertRevoked(session, "an administrator whose password the restore replaced")

    def test_a_role_changed_by_the_restore_revokes_the_administrator(self):
        session = self.admin_session()

        def demote(o):
            # Another administrator must remain, or the store refuses the demotion
            # (right for a person at a keyboard, irrelevant to what a file can hold).
            o.create("second-admin", "second-admin-password-1", omni_users.ROLE_ADMIN)
            o.set_role(self.admin_in(o).id, omni_users.ROLE_USER)

        self.restore_ok(self.backup_of(demote))
        self.assertRevoked(session, "an administrator the restore demoted")

    def test_an_account_the_backup_does_not_carry_revokes_the_administrator(self):
        session = self.admin_session()
        self.restore_ok(self.backup_of(drop_every_account))
        self.assertRevoked(session, "an administrator the backup removed")

    def test_an_account_recreated_with_a_different_identity_revokes_the_administrator(self):
        session = self.admin_session()

        def recreate(o):
            name = self.admin.username
            o._accounts.pop(self.admin_in(o).id)
            o.create(name, "recreated-password-1", omni_users.ROLE_ADMIN)

        self.restore_ok(self.backup_of(recreate))
        self.assertRevoked(session, "an administrator whose account was recreated")

    def test_an_account_the_backup_disabled_revokes_the_administrator(self):
        session = self.admin_session()

        def disable(o):
            o.create("second-admin", "second-admin-password-1", omni_users.ROLE_ADMIN)
            o.set_enabled(self.admin_in(o).id, False)

        self.restore_ok(self.backup_of(disable))
        self.assertRevoked(session, "an administrator the backup disabled")

    def test_changed_permissions_revoke_the_administrator(self):
        session = self.admin_session()
        self.restore_ok(self.backup_of(lambda o: setattr(
            self.admin_in(o), "permissions",
            Permissions(features=("av",), sources=[SRC], destinations=[FRONT]))))
        self.assertRevoked(session, "an administrator whose authorisation the restore changed")

    def test_only_the_changed_account_is_counted_as_ended(self):
        admin = self.admin_session()
        teacher = self.session_for(self.teacher)
        with mock.patch.object(omni_users.UserStore, "reload_from_disk",
                               autospec=True,
                               side_effect=omni_users.UserStore.reload_from_disk) as spy:
            self.restore_ok(self.backup_of(
                lambda o: o.set_password(o.find_by_username("teacher").id,
                                         NEW_PASSWORD)))
        self.assertTrue(self.alive(admin))
        self.assertFalse(self.alive(teacher))
        self.assertEqual(spy.call_count, 1)

    def test_the_comparison_is_the_servers_not_the_requests(self):
        # No field of the restore request can say "keep this administrator signed in".
        session = self.admin_session()
        client = self.srv.app.test_client()
        blob = self.backup_of(
            lambda o: o.set_password(self.admin_in(o).id, NEW_PASSWORD))
        reply = client.post(
            "/api/backup/restore?keep_sessions=1", content_type="multipart/form-data",
            data={"backup": (io.BytesIO(blob), "b.omnibak"), "passphrase": PASSPHRASE,
                  "keep_sessions": "1", "keep_admin": "true"},
            headers={"X-Keep-Sessions": "1"})
        self.assertEqual(reply.status_code, 200, reply.get_data(as_text=True))
        self.assertRevoked(session, "a request field kept an administrator signed in")


class TheLocalOperatorAlwaysHasAWayInTests(RestoreSessionBase):
    def _local(self, method, path, **kw):
        # No listener marker: the request is the OmniSuite computer's own.
        return getattr(self.srv.app.test_client(), method)(path, **kw)

    def test_a_backup_with_no_accounts_leaves_the_computer_able_to_create_an_administrator(self):
        blob = self.backup_of(drop_every_account)
        self.restore_ok(blob)
        state = self._local("get", "/api/bootstrap/state").get_json()
        self.assertTrue(state["needs_admin"])
        made = self._local("post", "/api/bootstrap/admin", json={
            "username": "recovery", "password": "recovery-password-1",
            "confirm": "recovery-password-1"})
        self.assertEqual(made.status_code, 200, made.get_data(as_text=True))
        self.assertTrue(self.store.authenticate("recovery", "recovery-password-1")["ok"])

    def test_the_same_request_from_the_network_is_refused(self):
        blob = self.backup_of(drop_every_account)
        self.restore_ok(blob)
        remote = self.srv.app.test_client().post(
            "/api/bootstrap/admin", environ_base=REMOTE, json={
                "username": "intruder", "password": "intruder-password-1",
                "confirm": "intruder-password-1"})
        self.assertIn(remote.status_code, (401, 403, 404))
        self.assertIsNone(self.store.find_by_username("intruder"))

    def test_an_administrator_from_the_backup_can_be_given_a_new_password_here(self):
        """Nobody knows the restored administrator's password: reset it locally."""
        self.restore_ok(self.backup_of(lambda o: o.set_password(
            o.find_by_username("principal").id, NEW_PASSWORD)))
        admin = self.store.find_by_username("principal")
        reset = self._local("post", "/api/admin/users/" + admin.id, json={
            "action": "set_password", "password": "set-here-password-1"})
        self.assertEqual(reset.status_code, 200, reset.get_data(as_text=True))
        self.assertTrue(self.store.authenticate("principal", "set-here-password-1")["ok"])

    def test_the_results_screen_names_the_administrators_that_came_with_it(self):
        body = self.restore_ok(self.backup_of(lambda o: None))
        self.assertIn("principal", body["admin_recovery"]["administrators"])
        self.assertIn("came from the backup", body["admin_recovery"]["note"])

    def test_an_accounts_empty_backup_does_not_claim_administrators_came_with_it(self):
        body = self.restore_ok(self.backup_of(drop_every_account))
        recovery = body["admin_recovery"]
        self.assertEqual(recovery["administrators"], [])
        self.assertNotIn("came from the backup", recovery["note"],
                         "a note about administrators that do not exist")
        self.assertIn("no administrator", recovery["note"])
        self.assertIn("Create a new administrator", recovery["note"])


class AReloadThatFailsEndsEverySessionTests(RestoreSessionBase):
    def test_when_the_process_cannot_pick_the_restore_up_nobody_stays_signed_in(self):
        user, admin = self.session_for(self.teacher), self.session_for(self.admin)
        blob = self.backup_of(lambda o: None)
        with mock.patch.object(self.srv, "_load_config", side_effect=RuntimeError("x")):
            reply = self.restore(blob)
        self.assertEqual(reply.status_code, 200, reply.get_data(as_text=True))
        self.assertEqual(reply.get_json()["status"], "RESTORED — RESTART REQUIRED")
        self.assertFalse(self.alive(user), "a half-loaded restore kept a session")
        self.assertFalse(self.alive(admin))


class ARestoreIsOneAuditEntryTests(RestoreSessionBase):
    def test_the_restore_is_recorded_once_with_what_it_did_to_sessions(self):
        self.session_for(self.teacher)
        self.restore_ok(self.backup_of(
            lambda o: o.set_password(o.find_by_username("teacher").id, NEW_PASSWORD)))
        events = [e for e in self.store.audit_entries(200)
                  if e["event"] == "backup.restored"]
        self.assertEqual(len(events), 1, "one operator action, one entry")
        detail = events[0].get("detail") or {}
        self.assertEqual(detail.get("sessions_ended"), 1)
        self.assertNotIn("passphrase", json.dumps(events[0]).lower())

    def test_a_refused_restore_is_not_recorded_as_a_restore(self):
        reply = self.restore(b"not a backup")
        self.assertEqual(reply.status_code, 400)
        self.assertFalse([e for e in self.store.audit_entries(200)
                          if e["event"] == "backup.restored"])


class WhatTheConfirmationIsToldTests(RestoreSessionBase):
    """The inspect step names the accounts a restore would remove or replace."""

    def inspect(self, blob):
        reply = self.srv.app.test_client().post(
            "/api/backup/inspect", content_type="multipart/form-data",
            data={"backup": (io.BytesIO(blob), "b.omnibak"),
                  "passphrase": PASSPHRASE})
        self.assertEqual(reply.status_code, 200, reply.get_data(as_text=True))
        return reply.get_json()

    def test_a_backup_without_one_account_names_it_as_removed(self):
        impact = self.inspect(self.backup_of(
            lambda o: o._accounts.pop(o.find_by_username("teacher").id)
        ))["account_impact"]
        self.assertTrue(impact["applies"])
        self.assertEqual(impact["removed"], ["teacher"])
        self.assertEqual(impact["replaced"], [])
        self.assertEqual(impact["current"], 2)
        self.assertEqual(impact["in_backup"], 1)

    def test_a_changed_password_is_named_as_replaced(self):
        impact = self.inspect(self.backup_of(
            lambda o: o.set_password(o.find_by_username("teacher").id, NEW_PASSWORD)
        ))["account_impact"]
        self.assertEqual(impact["replaced"], ["teacher"])
        self.assertEqual(impact["removed"], [])

    def test_a_permission_difference_is_named_as_replaced_because_it_ends_the_session(self):
        impact = self.inspect(self.backup_of(lambda o: o.set_permissions(
            o.find_by_username("teacher").id,
            Permissions(features=("av",), sources=[SRC], destinations=[]))
        ))["account_impact"]
        self.assertEqual(impact["replaced"], ["teacher"])
        self.assertEqual(impact["removed"], [])

    def test_a_backup_of_the_same_accounts_changes_nothing(self):
        impact = self.inspect(self.backup_of(lambda o: None))["account_impact"]
        self.assertEqual((impact["removed"], impact["replaced"], impact["added"]),
                         ([], [], []))

    def test_a_backup_with_no_accounts_says_so(self):
        impact = self.inspect(self.backup_of(drop_every_account))["account_impact"]
        self.assertEqual(impact["in_backup"], 0)
        self.assertEqual(sorted(impact["removed"]), ["principal", "teacher"])

    def test_a_backup_that_did_not_cover_accounts_applies_to_none(self):
        def uncovered(payload):
            payload["covered"] = [c for c in payload["covered"] if c != "accounts"]
            payload["sections"].pop("accounts", None)

        source = self.work / "uncovered"
        source.mkdir()
        shutil.copy2(self.store._path, source / "users.json")
        payload = omni_backup.build_payload(source, copy.deepcopy(UNITS))
        uncovered(payload)
        blob = omni_backup.seal(payload, PASSPHRASE, app_version="V1.2.0")
        impact = self.inspect(blob)["account_impact"]
        self.assertFalse(impact["applies"])
        # ...and the restore agrees with what it was told.
        teacher = self.session_for(self.teacher)
        self.restore_ok(blob)
        self.assertTrue(self.alive(teacher))
        self.assertIsNotNone(self.store.find_by_username("teacher"))

    def test_what_it_says_is_what_the_restore_then_does(self):
        blob = self.backup_of(lambda o: o._accounts.pop(o.find_by_username("teacher").id))
        told = self.inspect(blob)["account_impact"]["removed"]
        self.restore_ok(blob)
        now = {a["username"] for a in self.store.list_accounts()}
        self.assertTrue(set(told).isdisjoint(now), "it said removed; it was not")

    def test_it_names_accounts_and_nothing_a_credential_could_come_from(self):
        raw = json.dumps(self.inspect(self.backup_of(
            lambda o: o.set_password(o.find_by_username("teacher").id, NEW_PASSWORD)
        ))["account_impact"])
        for forbidden in ("scrypt", "password_hash", "digest", "blob", "token"):
            self.assertNotIn(forbidden, raw.lower())

    def test_inspecting_changes_nothing(self):
        session = self.session_for(self.teacher)
        before = (self.store._path.read_bytes(),
                  sorted(a["username"] for a in self.store.list_accounts()))
        self.inspect(self.backup_of(drop_every_account))
        self.assertTrue(self.alive(session))
        self.assertEqual(before, (self.store._path.read_bytes(),
                                  sorted(a["username"] for a in self.store.list_accounts())))


class TheImpactArithmeticTests(unittest.TestCase):
    """omni_backup.account_impact on synthetic tables: ids match, names are shown."""

    def impact(self, current, incoming, **payload):
        payload.setdefault("sections", {"accounts": {"users": []}})
        payload.setdefault("covered", ["accounts"])
        return omni_backup.account_impact(payload, current, incoming)

    def test_accounts_are_matched_by_id_not_by_name(self):
        current = {"id-1": ("alice", ("h", "user", True, ""))}
        incoming = {"id-2": ("alice", ("h", "user", True, ""))}
        got = self.impact(current, incoming)
        self.assertEqual(got["removed"], ["alice"])
        self.assertEqual(got["added"], ["alice"])

    def test_each_part_of_the_fingerprint_counts_as_a_replacement(self):
        base = ("hash", "user", True, "link")
        for index, changed in enumerate(("other", "admin", False, "")):
            fp = list(base)
            fp[index] = changed
            with self.subTest(part=index):
                got = self.impact({"i": ("a", base)}, {"i": ("a", tuple(fp))})
                self.assertEqual(got["replaced"], ["a"])

    def test_a_section_the_backup_never_looked_at_does_not_apply(self):
        got = self.impact({"i": ("a", ())}, {}, sections={}, covered=["device_names"])
        self.assertFalse(got["applies"])
        self.assertEqual(got["removed"], [])

    def test_a_missing_coverage_list_with_no_accounts_does_not_apply(self):
        got = omni_backup.account_impact({"sections": {}}, {"i": ("a", ())}, {})
        self.assertFalse(got["applies"])

    def test_a_covered_and_absent_section_removes_everyone(self):
        got = self.impact({"i": ("a", ()), "j": ("b", ())}, {}, sections={},
                          covered=["accounts"])
        self.assertTrue(got["applies"])
        self.assertEqual(got["removed"], ["a", "b"])

    def test_names_come_back_sorted_so_the_sentence_is_stable(self):
        current = {"1": ("zed", ()), "2": ("amy", ()), "3": ("mo", ())}
        self.assertEqual(self.impact(current, {})["removed"], ["amy", "mo", "zed"])


class TheExportIsRecordedTests(RestoreSessionBase):
    def test_a_backup_made_is_one_entry_with_counts_and_no_secret(self):
        reply = self.srv.app.test_client().post(
            "/api/backup/export", json={"passphrase": PASSPHRASE})
        self.assertEqual(reply.status_code, 200, reply.get_data()[:200])
        events = [e for e in self.store.audit_entries(200)
                  if e["event"] == "backup.exported"]
        self.assertEqual(len(events), 1)
        raw = json.dumps(events[0])
        self.assertNotIn(PASSPHRASE, raw)
        self.assertIn("sections", events[0]["detail"])

    def test_a_refused_export_is_not_recorded_as_one_made(self):
        reply = self.srv.app.test_client().post(
            "/api/backup/export", json={"passphrase": "x"})
        self.assertEqual(reply.status_code, 400)
        self.assertFalse([e for e in self.store.audit_entries(200)
                          if e["event"] == "backup.exported"])


if __name__ == "__main__":
    unittest.main()
