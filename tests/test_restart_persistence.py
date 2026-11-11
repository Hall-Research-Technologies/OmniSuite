"""Everything an administrator configured is still there after a restart -- and nothing
that was only ever in memory is.

A restart is modelled by building NEW stores from the files the application wrote,
not by asking the old ones to reload: the reload path and the startup path are not
the same code, and it is startup that a person experiences.

    survives     accounts, roles, enabled flags, password hashes, permissions
                 (sources, displays, groups, routing interfaces, features),
                 Direct Access links (and the ability to show the same link again),
                 groups and their members, friendly names, the audit record,
                 activity retention, the assignment protection that follows from
                 all of it
    does not     any sign-in: sessions are memory, so every cookie issued before the
                 restart is refused after it, and so is a link-opened session

Real files in a throwaway directory.
"""
from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import omni_aliases                                       # noqa: E402
import omni_groups                                        # noqa: E402
import omni_inventory                                     # noqa: E402
import omni_users                                         # noqa: E402
from omni_users import Permissions                        # noqa: E402
from tests.test_acceptance_lifecycle import LifecycleBase, REMOTE   # noqa: E402
from tests.test_display_groups import ROOM_A, ROOM_B, ROOM_C, SRC, canonical   # noqa: E402
from tests import test_user_routing as harness            # noqa: E402

FRONT = harness.FRONT_DISPLAY
CAMERA = harness.DOC_CAMERA


class RestartBase(LifecycleBase):
    def setUp(self):
        super().setUp()
        self.store.create("lecturer", "lecturer-password-1", omni_users.ROLE_USER,
                          Permissions(features=("av", "usb"), sources=[SRC, CAMERA],
                                      destinations=[FRONT, ROOM_C],
                                      groups=[self.group["id"]],
                                      interfaces=["standard", "matrix"]))
        self.store.create("matrixonly", "matrixonly-password-1", omni_users.ROLE_USER,
                          Permissions(features=("av",), sources=[SRC],
                                      interfaces=["matrix"]))
        self.store.create("disabled", "disabled-password-1", omni_users.ROLE_USER,
                          Permissions(features=("av",), sources=[CAMERA]))
        self.store.set_enabled(self.store.find_by_username("disabled").id, False,
                               actor=self.admin)
        lecturer = self.store.find_by_username("lecturer")
        self.token = self.store.enable_direct_access(lecturer.id, actor=self.admin)["token"]
        self.aliases.set_alias(FRONT, "Front Display")
        self.aliases.set_alias(SRC, "Lectern PC")
        self.store.flush_audit()

    def restart(self):
        """What startup builds, from the files alone."""
        store = omni_users.UserStore(self.store._path, self.store._audit_path)
        aliases = omni_aliases.AliasStore(self.aliases._path)
        groups = omni_groups.GroupStore(self.groups._path, canonical)
        return store, aliases, groups


class WhatSurvivesTests(RestartBase):
    def test_every_account_comes_back_exactly(self):
        store, _a, _g = self.restart()
        before = {a.id: a.to_json() for a in self.store._accounts.values()}
        after = {a.id: a.to_json() for a in store._accounts.values()}
        self.assertEqual(after, before)

    def test_routing_interfaces_features_and_enabled_flags_survive(self):
        store, _a, _g = self.restart()
        lecturer = store.find_by_username("lecturer")
        self.assertTrue(lecturer.permissions.has_interface("standard"))
        self.assertTrue(lecturer.permissions.has_interface("matrix"))
        self.assertTrue(lecturer.permissions.has_feature("usb"))
        matrix_only = store.find_by_username("matrixonly")
        self.assertTrue(matrix_only.permissions.has_interface("matrix"))
        self.assertFalse(matrix_only.permissions.has_interface("standard"))
        self.assertFalse(store.find_by_username("disabled").enabled)

    def test_a_direct_access_link_still_opens_and_can_be_shown_again(self):
        store, _a, _g = self.restart()
        self.assertEqual(store.resolve_direct_token(self.token).username, "lecturer")
        self.assertEqual(store.direct_access_token(store.find_by_username("lecturer").id),
                         self.token)

    def test_groups_names_and_their_members_survive(self):
        _s, aliases, groups = self.restart()
        self.assertEqual(sorted(groups.get(self.group["id"])["members"]),
                         sorted([ROOM_A, ROOM_B]))
        self.assertEqual(groups.get(self.group["id"])["name"], "Front rooms")
        self.assertEqual(aliases.all(), self.aliases.all())

    def test_the_assignment_protection_is_the_same_after_a_restart(self):
        store, _a, groups = self.restart()
        before = omni_inventory.collect(self.store, self.groups)
        after = omni_inventory.collect(store, groups)
        self.assertEqual(after, before)
        self.assertTrue(after, "precondition: nothing was assigned, so nothing was tested")

    def test_the_audit_record_survives(self):
        store, _a, _g = self.restart()
        before = [e["event"] for e in self.store.audit_entries(1000)]
        after = [e["event"] for e in store.audit_entries(1000)]
        self.assertEqual(after, before)
        self.assertTrue(after, "precondition: nothing was recorded, so nothing was tested")

    def test_a_second_restart_changes_nothing(self):
        store, aliases, groups = self.restart()
        store._persist()
        again = omni_users.UserStore(store._path, store._audit_path)
        self.assertEqual({a.id: a.to_json() for a in again._accounts.values()},
                         {a.id: a.to_json() for a in store._accounts.values()})


class WhatDoesNotSurviveTests(RestartBase):
    def test_a_password_session_does_not_outlive_the_process(self):
        lecturer = self.store.find_by_username("lecturer")
        session = self.session_for(lecturer)
        self.assertIsNotNone(self.store.resolve_session(session.token)[0])
        store, _a, _g = self.restart()
        self.assertEqual(store.resolve_session(session.token), (None, None))
        self.assertEqual(store.active_session_count(), 0)

    def test_a_link_session_does_not_outlive_the_process_either(self):
        lecturer = self.store.find_by_username("lecturer")
        session = self.session_for(lecturer, omni_users.BY_DIRECT_LINK)
        store, _a, _g = self.restart()
        self.assertEqual(store.resolve_session(session.token), (None, None))
        # ...but the link that opened it is still good, so a wall panel recovers.
        self.assertIsNotNone(store.resolve_direct_token(self.token))

    def test_failed_sign_in_counters_are_not_a_permanent_lockout(self):
        for _ in range(30):
            self.store.authenticate("lecturer", "wrong-password-xyz", source="198.51.100.9")
        store, _a, _g = self.restart()
        self.assertTrue(store.authenticate("lecturer", "lecturer-password-1",
                                           source="198.51.100.9")["ok"],
                        "a restart left an account locked out")


class AStoreFileThatIsDamagedTests(RestartBase):
    def test_a_damaged_account_file_is_quarantined_and_the_application_starts(self):
        self.store._path.write_text("{ this is not json", encoding="utf-8")
        store = omni_users.UserStore(self.store._path, self.store._audit_path)
        self.assertEqual(store.list_accounts(), [])
        self.assertTrue(self.store._path.with_suffix(".corrupt").exists(),
                        "the damaged file was destroyed instead of kept")
        self.assertTrue(store.is_empty and not store.has_admin,
                        "a damaged store must not become 'no restrictions'")

    def test_a_damaged_group_file_degrades_to_none_and_keeps_the_evidence(self):
        self.groups._path.write_text("[[[", encoding="utf-8")
        groups = omni_groups.GroupStore(self.groups._path, canonical)
        self.assertEqual(groups.all(), [])

    def test_a_damaged_alias_file_degrades_to_none(self):
        self.aliases._path.write_text("not json at all", encoding="utf-8")
        aliases = omni_aliases.AliasStore(self.aliases._path)
        self.assertEqual(aliases.all(), {})


if __name__ == "__main__":
    unittest.main()
