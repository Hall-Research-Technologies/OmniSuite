"""Accounts written by earlier builds still load, and a user can show but never edit.

An earlier build identified a restricted account's Multiview approvals by the layout's
name. Names change and can be reused, so an approval is now the decoder's hardware
identity plus the layout's opaque id (see test_user_multiview.py). A users.json that
still holds the old form must load, keep everything else the account had, and shed the
old approvals on the next save -- never an error, and never an approval kept alive from
a record nobody can tie to one saved layout.

What the user page is deliberately NOT given is also held here: the three ways of
reshaping a layout (switching a window, applying, deleting) stay administrative.
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

import omni_access                                   # noqa: E402
import omni_users                                    # noqa: E402
from omni_users import Permissions, UserStore        # noqa: E402

DECODER = "b8:98:b0:00:00:41"
SOURCE = "b8:98:b0:00:00:01"
GROUP = "grp_0123456789abcdef"
LAYOUT = "mv_0123456789abcdef"


class OldStoredAccountsStillLoad(unittest.TestCase):
    OLD_ACCOUNT_PERMISSIONS = {
        "features": ["av", "usb", "multiview"],
        "sources": [SOURCE],
        "destinations": [DECODER],
        "multiviews": [DECODER + "/Quad", DECODER + "/Whole", DECODER + "/" + LAYOUT],
        "groups": [GROUP],
    }

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "users.json"
        store = UserStore(self.path)
        store.create("teacher", "teacher-password-1", omni_users.ROLE_USER,
                     Permissions(features=("av",), sources=(SOURCE,)))
        document = json.loads(self.path.read_text(encoding="utf-8"))
        accounts = document["users"] if isinstance(document, dict) else document
        accounts = accounts.values() if isinstance(accounts, dict) else accounts
        for record in accounts:
            if record.get("username") == "teacher":
                record["permissions"] = dict(self.OLD_ACCOUNT_PERMISSIONS)
        self.path.write_text(json.dumps(document), encoding="utf-8")
        self.store = UserStore(self.path)
        self.teacher = self.store.find_by_username("teacher")

    def tearDown(self):
        self.tmp.cleanup()

    def test_it_loads_and_keeps_everything_else_the_account_had(self):
        self.assertIsNotNone(self.teacher, "the account must still load")
        p = self.teacher.permissions
        self.assertEqual(p.features, frozenset({"av", "usb", "multiview"}))
        self.assertEqual(p.sources, frozenset({SOURCE}))
        self.assertEqual(p.destinations, frozenset({DECODER}))
        self.assertEqual(p.groups, frozenset({GROUP}))
        self.assertTrue(self.teacher.enabled)

    def test_only_the_approval_that_names_a_layout_by_identity_survives(self):
        self.assertEqual(self.teacher.permissions.multiviews, frozenset({DECODER + "/" + LAYOUT}))

    def test_it_still_signs_in(self):
        self.assertIsNotNone(self.store.authenticate("teacher", "teacher-password-1"))

    def test_the_name_based_approvals_are_gone_after_the_next_save(self):
        self.store.set_permissions(self.teacher.id, self.teacher.permissions)
        text = self.path.read_text(encoding="utf-8")
        self.assertNotIn("/Quad", text)
        self.assertNotIn("/Whole", text)
        self.assertIn(DECODER + "/" + LAYOUT, text)

    def test_a_record_with_garbage_in_the_approvals_is_not_an_error(self):
        for hostile in (None, "x", 5, {"a": 1}, [None, 3, {"k": "v"}, "no/slash", "/", "a/b/c"]):
            perms = Permissions.from_json({"features": ["av", "multiview"], "multiviews": hostile,
                                           "sources": [SOURCE]})
            self.assertEqual(perms.features, frozenset({"av", "multiview"}))
            self.assertEqual(perms.sources, frozenset({SOURCE}))
            self.assertEqual(perms.multiviews, frozenset())


class UsersCanShowButNeverEdit(unittest.TestCase):
    USER_MULTIVIEW = ("/api/user/multiview_state", "/api/user/multiview_show")
    NEVER_FOR_A_USER = ("/api/user/multiview_switch", "/api/user/multiview_apply",
                        "/api/user/multiview_delete", "/api/user/multiview_save")
    ADMIN_STAYS = ("/matrix/multiview", "/api/multiview/state", "/api/multiview/show",
                   "/api/multiview/switch", "/api/multiview/plan", "/api/multiview/layouts",
                   "/api/multiview/apply", "/api/multiview/groups/show", "/api/admin/multiviews")

    def test_the_user_routes_exist_and_nothing_that_edits_does(self):
        for rule in self.USER_MULTIVIEW:
            self.assertEqual(omni_access.ROUTES[rule],
                             (omni_access.Access.USER, omni_access.FEATURE_MULTIVIEW), rule)
        for rule in self.NEVER_FOR_A_USER:
            self.assertNotIn(rule, omni_access.ROUTES, rule)

    def test_the_application_serves_exactly_the_two_user_routes(self):
        import OmniMatrix_upgrade_server_v7_6y as server
        served = {rule.rule for rule in server.app.url_map.iter_rules()}
        for rule in self.USER_MULTIVIEW:
            self.assertIn(rule, served, rule)
        for rule in self.NEVER_FOR_A_USER:
            self.assertNotIn(rule, served, rule)

    def test_every_administrative_multiview_route_stays_administrative(self):
        import OmniMatrix_upgrade_server_v7_6y as server
        served = {rule.rule for rule in server.app.url_map.iter_rules()}
        for rule in self.ADMIN_STAYS:
            access, capability = omni_access.ROUTES[rule]
            self.assertEqual(access, omni_access.Access.ADMIN, rule)
            self.assertIsNone(capability, rule)
            self.assertIn(rule, served, rule)

    def test_the_server_wires_the_show_engine_and_no_window_switching_into_the_remote_api(self):
        source = (ROOT / "OmniMatrix_upgrade_server_v7_6y.py").read_text(encoding="utf-8")
        self.assertIn("multiview_show=_show_multiview_on", source)
        self.assertNotIn("multiview_switch_view=", source)
        self.assertNotIn("multiview_switch=", source)


if __name__ == "__main__":
    unittest.main()
