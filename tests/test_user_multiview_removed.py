"""Multiview is not a restricted-account capability: the code is gone, the data still loads.

The administrator's Multiview is untouched. What was removed is the ability to
give a User or a Direct Access link Multiview: the feature, the per-layout
approvals, the three user endpoints, the user page's Multiview tab and the
account editor's controls.

A users.json written before that still holds a "multiview" feature and a
"multiviews" approval list. Those accounts must load, keep everything else they
had, and have the obsolete fields dropped on the next save -- never an error, and
never a feature quietly kept alive from a record nobody can see in the editor.
"""
from __future__ import annotations

import json
import os
import re
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


def stripped(text):
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"(?m)^\s*//.*$", "", text)


class ThePermissionModelHasNoMultiview(unittest.TestCase):
    def test_the_constants_and_helpers_are_gone(self):
        for name in ("FEATURE_MULTIVIEW", "USER_MULTIVIEW_ENABLED"):
            self.assertFalse(hasattr(omni_access, name), name)
        for name in ("multiview_key", "_split_multiview", "MULTIVIEW_KEY_SEPARATOR"):
            self.assertFalse(hasattr(omni_users, name), name)
        self.assertFalse(hasattr(Permissions(), "multiviews"))
        self.assertFalse(hasattr(Permissions(), "may_operate_multiview"))

    def test_a_restricted_account_can_hold_av_and_usb_only(self):
        self.assertEqual(omni_access.FEATURES, ("av", "usb"))
        self.assertEqual(omni_access.user_features(), ["av", "usb"])

    def test_a_request_that_still_names_multiview_is_read_as_if_it_did_not(self):
        perms = Permissions(features=("av", "multiview"), sources=(SOURCE,),
                            destinations=(DECODER,), groups=(GROUP,))
        self.assertEqual(perms.features, frozenset({"av"}))
        self.assertFalse(perms.has_feature("multiview"))
        self.assertEqual(sorted(perms.to_json()),
                         ["destinations", "features", "groups", "interfaces",
                          "sources"])


class OldStoredAccountsStillLoad(unittest.TestCase):
    """An account written when Multiview could be granted, loaded now."""

    OLD_ACCOUNT_PERMISSIONS = {
        "features": ["av", "usb", "multiview"],
        "sources": [SOURCE],
        "destinations": [DECODER],
        "multiviews": [DECODER + "/Quad", DECODER + "/Whole"],
        "groups": [GROUP],
    }

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "users.json"
        # Create through the store, then rewrite the record the way an older
        # build left it, so the password hash is real and the file is genuine.
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

    def test_it_loads_and_keeps_everything_that_still_exists(self):
        self.assertIsNotNone(self.teacher, "the account must still load")
        p = self.teacher.permissions
        self.assertEqual(p.features, frozenset({"av", "usb"}))
        self.assertEqual(p.sources, frozenset({SOURCE}))
        self.assertEqual(p.destinations, frozenset({DECODER}))
        self.assertEqual(p.groups, frozenset({GROUP}))
        self.assertTrue(self.teacher.enabled)

    def test_it_still_signs_in(self):
        account = self.store.authenticate("teacher", "teacher-password-1")
        self.assertIsNotNone(account)

    def test_the_obsolete_fields_are_dropped_on_the_next_save(self):
        self.store.set_permissions(self.teacher.id, self.teacher.permissions)
        text = self.path.read_text(encoding="utf-8")
        self.assertNotIn("multiviews", text)
        self.assertNotIn('"multiview"', text)
        self.assertIn(SOURCE, text)
        reloaded = UserStore(self.path).find_by_username("teacher")
        self.assertEqual(reloaded.permissions.features, frozenset({"av", "usb"}))

    def test_a_record_with_garbage_in_the_obsolete_fields_is_not_an_error(self):
        for hostile in (None, "x", 5, {"a": 1}, [None, 3, {"k": "v"}, "no/slash", "/"]):
            perms = Permissions.from_json({"features": ["av", "multiview"], "multiviews": hostile,
                                           "sources": [SOURCE]})
            self.assertEqual(perms.features, frozenset({"av"}))
            self.assertEqual(perms.sources, frozenset({SOURCE}))

    def test_clearing_units_no_longer_counts_an_approval_as_an_assignment(self):
        assigned = self.store.assigned_devices()
        self.assertEqual(assigned.get(DECODER), ["teacher"], "held as a display")
        self.assertEqual(set(assigned), {DECODER, SOURCE})


class TheRoutesAreGone(unittest.TestCase):
    REMOVED = ("/api/user/multiview_state", "/api/user/multiview_show",
               "/api/user/multiview_switch", "/api/admin/multiviews")
    ADMIN_STAYS = ("/matrix/multiview", "/api/multiview/state", "/api/multiview/show",
                   "/api/multiview/switch", "/api/multiview/plan", "/api/multiview/layouts",
                   "/api/multiview/apply", "/api/multiview/groups/show")

    def test_the_access_table_no_longer_lists_them(self):
        for rule in self.REMOVED:
            self.assertNotIn(rule, omni_access.ROUTES, rule)

    def test_the_application_no_longer_serves_them(self):
        import OmniMatrix_upgrade_server_v7_6y as server
        served = {rule.rule for rule in server.app.url_map.iter_rules()}
        for rule in self.REMOVED:
            self.assertNotIn(rule, served, rule)

    def test_administrator_multiview_is_untouched(self):
        import OmniMatrix_upgrade_server_v7_6y as server
        served = {rule.rule for rule in server.app.url_map.iter_rules()}
        for rule in self.ADMIN_STAYS:
            access, capability = omni_access.ROUTES[rule]
            self.assertEqual(access, omni_access.Access.ADMIN, rule)
            self.assertIsNone(capability, rule)
            self.assertIn(rule, served, rule)


class TheInterfaceOffersNoUserMultiview(unittest.TestCase):
    def test_the_account_editor_has_no_multiview_controls(self):
        html = stripped((ROOT / "ui" / "admin" / "users.html").read_text(encoding="utf-8"))
        js = stripped((ROOT / "ui" / "admin" / "users.js").read_text(encoding="utf-8"))
        for needle in ('value="multiview"', "mv_approval_section", "mv_display_pick",
                       "Multiview layouts they may show"):
            self.assertNotIn(needle, html)
        for needle in ("approvedMultiviews", "/api/admin/multiviews", "multiviews:"):
            self.assertNotIn(needle, js)

    def test_the_user_page_has_no_multiview_tab_panel_or_handler(self):
        html = stripped((ROOT / "ui" / "user" / "index.html").read_text(encoding="utf-8"))
        js = stripped((ROOT / "ui" / "user" / "user.js").read_text(encoding="utf-8"))
        for needle in ("panel_multiview", "mv_sheet", "mv_layout", "mv_presets", "mv_displays"):
            self.assertNotIn(needle, html)
        for needle in ("/api/user/multiview", "loadMultiviewPresets", "showPreset",
                       "switchWindow", "closeSourceSheet", "mvDisplay", "mvPreset",
                       "REMEMBERED_MV_DISPLAY"):
            self.assertNotIn(needle, js)
        self.assertNotRegex(js, r"\[\s*['\"]multiview['\"]\s*,")
        # What a display is *showing* is information, not a capability, and stays.
        self.assertIn("Showing a Multiview layout", js)

    def test_the_stylesheet_carries_none_of_the_removed_windows(self):
        css = stripped((ROOT / "ui" / "remote.css").read_text(encoding="utf-8"))
        self.assertNotIn(".mv-layout", css)
        self.assertNotIn(".mv-window", css)

    def test_the_server_does_not_wire_user_multiview_into_the_remote_api(self):
        source = (ROOT / "OmniMatrix_upgrade_server_v7_6y.py").read_text(encoding="utf-8")
        self.assertNotIn("multiview_show_view=", source)
        self.assertNotIn("multiview_switch_view=", source)
        self.assertNotIn("_multiview_state_snapshot", source)


if __name__ == "__main__":
    unittest.main()
