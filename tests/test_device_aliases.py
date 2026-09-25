"""Friendly device names: they label, and they never authorise.

The whole risk in this feature is one sentence: if an alias could ever be
resolved back to a device, two accounts given the same label would reach each
other's equipment. So most of these tests are about what an alias *cannot* do.
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

import omni_aliases                                  # noqa: E402
import omni_gate                                     # noqa: E402
import omni_projection                               # noqa: E402
import omni_remote                                   # noqa: E402
import omni_users                                    # noqa: E402
from omni_aliases import AliasStore                  # noqa: E402
from omni_users import Permissions, UserStore        # noqa: E402

TEACHER_PC = "B8:98:B0:00:00:01"
DOC_CAMERA = "B8:98:B0:00:00:02"
LAB_CAMERA = "B8:98:B0:00:00:09"
FRONT_DISPLAY = "B8:98:B0:00:00:11"
LAB_DISPLAY = "B8:98:B0:00:00:19"

UNITS = [
    {"ip": "192.0.2.11", "mac": TEACHER_PC, "hostname": "at-omni-111-04553",
     "model": "hw-omni-e4111", "role": "encoder"},
    {"ip": "192.0.2.12", "mac": DOC_CAMERA, "hostname": "hw-omni-e4111-784fe",
     "model": "hw-omni-e4111", "role": "encoder"},
    {"ip": "192.0.2.19", "mac": LAB_CAMERA, "hostname": "hw-omni-e4111-lab",
     "model": "hw-omni-e4111", "role": "encoder"},
    {"ip": "192.0.2.21", "mac": FRONT_DISPLAY, "hostname": "hw-omni-d4111-7851dd",
     "model": "hw-omni-d4111", "role": "decoder"},
    {"ip": "192.0.2.29", "mac": LAB_DISPLAY, "hostname": "hw-omni-d4111-lab",
     "model": "hw-omni-d4111", "role": "decoder"},
]

MATRIX = {
    "encoders": [{"ip": u["ip"], "host": u["hostname"], "model": u["model"]}
                 for u in UNITS if u["role"] == "encoder"],
    "decoders": [{"ip": u["ip"], "host": u["hostname"], "model": u["model"],
                  "multiview_active": False} for u in UNITS if u["role"] == "decoder"],
    "routes": {"192.0.2.21": "192.0.2.11"},
}


class AliasStoreTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="omni-alias-"))
        self.store = AliasStore(self.dir / "device_aliases.json")

    def test_an_alias_is_stored_and_read_back(self):
        self.store.set_alias(TEACHER_PC, "Teacher PC")
        self.assertEqual(self.store.alias_for(TEACHER_PC), "Teacher PC")

    def test_an_alias_survives_a_restart(self):
        self.store.set_alias(TEACHER_PC, "Teacher PC")
        reopened = AliasStore(self.dir / "device_aliases.json")
        self.assertEqual(reopened.alias_for(TEACHER_PC), "Teacher PC")

    def test_an_alias_is_keyed_by_identity_not_by_address(self):
        """The device moves; the label goes with it."""
        self.store.set_alias(TEACHER_PC, "Teacher PC")
        moved = [dict(u) for u in UNITS]
        moved[0]["ip"] = "192.0.2.77"          # same device, new address
        payload = omni_projection.user_state(
            {"encoders": [{"ip": "192.0.2.77", "host": "at-omni-111-04553"}],
             "decoders": [], "routes": {}},
            moved, _admin(), self.store)
        self.assertEqual([s["name"] for s in payload["sources"]], ["Teacher PC"])

    def test_identity_spelling_does_not_change_the_lookup(self):
        self.store.set_alias("b8-98-b0-00-00-01", "Teacher PC")
        for spelling in (TEACHER_PC, "b8:98:b0:00:00:01", "B898B0000001"):
            with self.subTest(spelling=spelling):
                self.assertEqual(self.store.alias_for(spelling), "Teacher PC")

    def test_clearing_an_alias_removes_it(self):
        self.store.set_alias(TEACHER_PC, "Teacher PC")
        self.store.set_alias(TEACHER_PC, "")
        self.assertEqual(self.store.alias_for(TEACHER_PC), "")
        self.assertEqual(self.store.display_name(TEACHER_PC, "at-omni-111-04553"),
                         "at-omni-111-04553")

    def test_the_fallback_is_the_device_name(self):
        self.assertEqual(self.store.display_name(DOC_CAMERA, "hw-omni-e4111-784fe"),
                         "hw-omni-e4111-784fe")

    def test_a_nameless_device_still_gets_something_to_show(self):
        self.assertEqual(self.store.display_name(DOC_CAMERA, ""), "Unnamed device")

    def test_an_alias_is_trimmed_and_bounded(self):
        self.store.set_alias(TEACHER_PC, "   Teacher    PC   ")
        self.assertEqual(self.store.alias_for(TEACHER_PC), "Teacher PC")
        self.store.set_alias(DOC_CAMERA, "x" * 200)
        self.assertLessEqual(len(self.store.alias_for(DOC_CAMERA)),
                             omni_aliases.MAX_ALIAS_LENGTH)

    def test_control_characters_are_stripped(self):
        self.store.set_alias(TEACHER_PC, "Teacher\x00\x07 PC\n")
        self.assertEqual(self.store.alias_for(TEACHER_PC), "Teacher PC")

    def test_an_identity_that_is_not_one_is_refused(self):
        self.assertFalse(self.store.set_alias("", "Nothing")["ok"])

    def test_a_corrupt_file_degrades_to_no_aliases(self):
        path = self.dir / "broken.json"
        path.write_text("{ not json", encoding="utf-8")
        store = AliasStore(path)
        self.assertEqual(store.all(), {})
        self.assertTrue(path.with_suffix(".corrupt").exists())

    def test_duplicates_are_reported_rather_than_prevented(self):
        self.store.set_alias(FRONT_DISPLAY, "Front Display")
        self.store.set_alias(LAB_DISPLAY, "front display")
        duplicates = self.store.duplicates()
        self.assertIn("front display", duplicates)
        self.assertEqual(len(duplicates["front display"]), 2)


class AliasesAreNotIdentityTests(unittest.TestCase):
    """The property that matters. An alias may never decide access."""

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="omni-alias-"))
        self.aliases = AliasStore(self.dir / "device_aliases.json")
        self.users = UserStore(self.dir / "users.json")
        self.users.create("teacher", "teacher-password-1", omni_users.ROLE_USER,
                          Permissions(features=("av",),
                                      sources=(TEACHER_PC, DOC_CAMERA),
                                      destinations=(FRONT_DISPLAY,)))
        self.teacher = self.users.find_by_username("teacher")
        self.session = self.users.open_session(self.teacher.id)

    def decide(self, payload):
        env = {omni_remote.LISTENER_KEY: omni_remote.REMOTE,
               "HTTP_COOKIE": "omnisuite_session=" + self.session.token,
               "HTTP_X_OMNISUITE_CSRF": self.session.csrf_token}
        return omni_gate.evaluate("/api/user/route", "POST", env, payload,
                                  self.users)

    def test_renaming_a_device_does_not_change_permissions(self):
        before = self.decide({"source": DOC_CAMERA, "destination": FRONT_DISPLAY})
        self.assertTrue(before.allowed)
        self.aliases.set_alias(DOC_CAMERA, "Document Camera")
        self.aliases.set_alias(DOC_CAMERA, "Instructor Camera")
        after = self.decide({"source": DOC_CAMERA, "destination": FRONT_DISPLAY})
        self.assertTrue(after.allowed, "a label change moved an access boundary")

    def test_an_alias_cannot_be_used_in_place_of_an_identity(self):
        """The attack: ask for the device by the name you were shown."""
        self.aliases.set_alias(DOC_CAMERA, "Document Camera")
        decision = self.decide({"source": "Document Camera",
                                "destination": FRONT_DISPLAY})
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "source_not_permitted")

    def test_a_duplicate_alias_does_not_cross_an_authorization_boundary(self):
        """Two devices, one label, and only one of them permitted."""
        self.aliases.set_alias(FRONT_DISPLAY, "Front Display")
        self.aliases.set_alias(LAB_DISPLAY, "Front Display")
        permitted = self.decide({"source": DOC_CAMERA,
                                 "destination": FRONT_DISPLAY})
        self.assertTrue(permitted.allowed)
        forbidden = self.decide({"source": DOC_CAMERA,
                                 "destination": LAB_DISPLAY})
        self.assertFalse(forbidden.allowed,
                         "sharing a label must not share access")
        self.assertEqual(forbidden.reason, "destination_not_permitted")

    def test_naming_a_device_after_another_devices_identity_changes_nothing(self):
        """An alias that looks like a MAC is still only a label."""
        self.aliases.set_alias(LAB_DISPLAY, FRONT_DISPLAY)
        decision = self.decide({"source": DOC_CAMERA, "destination": LAB_DISPLAY})
        self.assertFalse(decision.allowed)

    def test_the_alias_module_offers_no_lookup_by_name(self):
        """One direction only, asserted against the API rather than the prose."""
        public = [name for name in dir(AliasStore) if not name.startswith("_")]
        self.assertEqual(sorted(public),
                         ["alias_for", "all", "display_name", "duplicates",
                          "forget", "set_alias", "set_many"],
                         "a name-to-identity lookup would make a label an "
                         "identifier; there must not be one")

    def test_the_gate_never_consults_the_alias_store(self):
        import ast
        tree = ast.parse((ROOT / "omni_gate.py").read_text(encoding="utf-8"))
        names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
        self.assertNotIn("omni_aliases", names)
        self.assertNotIn("aliases", names,
                         "authorization must not see a label at all")


class ProjectionUsesAliasesTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="omni-alias-"))
        self.aliases = AliasStore(self.dir / "device_aliases.json")
        self.users = UserStore(self.dir / "users.json")
        self.users.create("teacher", "teacher-password-1", omni_users.ROLE_USER,
                          Permissions(features=("av",),
                                      sources=(TEACHER_PC, DOC_CAMERA),
                                      destinations=(FRONT_DISPLAY,)))
        self.teacher = self.users.find_by_username("teacher")
        self.aliases.set_alias(TEACHER_PC, "Teacher PC")
        self.aliases.set_alias(DOC_CAMERA, "Document Camera")
        self.aliases.set_alias(FRONT_DISPLAY, "Front Display")

    def project(self, account=None):
        return omni_projection.user_state(MATRIX, UNITS,
                                          account or self.teacher, self.aliases)

    def test_a_user_sees_the_friendly_names(self):
        payload = self.project()
        self.assertEqual(sorted(s["name"] for s in payload["sources"]),
                         ["Document Camera", "Teacher PC"])
        self.assertEqual([d["name"] for d in payload["destinations"]],
                         ["Front Display"])

    def test_the_hostname_is_not_sent_once_an_alias_exists(self):
        blob = json.dumps(self.project())
        for hostname in ("at-omni-111-04553", "hw-omni-e4111-784fe",
                         "hw-omni-d4111-7851dd"):
            self.assertNotIn(hostname, blob,
                             "an engineering hostname reached a restricted user")

    def test_a_device_without_an_alias_falls_back_to_its_own_name(self):
        self.aliases.set_alias(DOC_CAMERA, "")
        names = sorted(s["name"] for s in self.project()["sources"])
        self.assertEqual(names, ["Teacher PC", "hw-omni-e4111-784fe"])

    def test_the_current_route_is_described_with_the_friendly_name(self):
        showing = self.project()["destinations"][0]["showing"]
        self.assertEqual(showing["mode"], "source")
        self.assertEqual(showing["source_name"], "Teacher PC")

    def test_aliases_add_no_field_to_the_projection(self):
        """The allowlist is unchanged: a label replaces a name, it does not
        add one."""
        payload = self.project()
        self.assertEqual(set(payload),
                         {"ok", "features", "role", "username",
                          "sources", "destinations"})
        for source in payload["sources"]:
            self.assertEqual(set(source), {"id", "name", "online"})
        for destination in payload["destinations"]:
            self.assertEqual(set(destination),
                             {"id", "name", "online", "showing"})

    def test_an_alias_never_leaks_a_device_the_account_may_not_see(self):
        self.aliases.set_alias(LAB_CAMERA, "Lab Camera")
        blob = json.dumps(self.project())
        self.assertNotIn("Lab Camera", blob)

    def test_no_alias_store_means_the_previous_behaviour(self):
        payload = omni_projection.user_state(MATRIX, UNITS, self.teacher)
        self.assertIn("at-omni-111-04553",
                      [s["name"] for s in payload["sources"]])


class NoDeviceIoForAliasesTests(unittest.TestCase):
    def test_the_alias_module_performs_no_device_io(self):
        import ast
        tree = ast.parse((ROOT / "omni_aliases.py").read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        for forbidden in ("socket", "requests", "websocket", "urllib",
                          "omni_matrix_logic"):
            self.assertNotIn(forbidden, imported,
                             "a label must never cost a device read")

    def test_nothing_writes_an_alias_to_a_device(self):
        """Asserted against the code, not the prose.

        The first version searched the whole file for "hostname" and failed on
        the docstring that explains hostnames are never touched. A test that
        cannot tell an explanation from an instruction constrains nothing --
        the same mistake the trust-signal test made in Phase A.
        """
        import ast
        tree = ast.parse((ROOT / "omni_aliases.py").read_text(encoding="utf-8"))

        docstrings = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef)):
                doc = ast.get_docstring(node, clean=False)
                if doc:
                    docstrings.add(doc)

        literals = {n.value for n in ast.walk(tree)
                    if isinstance(n, ast.Constant) and isinstance(n.value, str)
                    and n.value not in docstrings}
        called = {n.func.attr for n in ast.walk(tree)
                  if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
        called |= {n.func.id for n in ast.walk(tree)
                   if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}

        for forbidden in ("config_set", "hostname", "_ws_send_recv", "method"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, literals,
                                 f"{forbidden!r} appears as a value this module "
                                 "could send to a device")
                self.assertNotIn(forbidden, called,
                                 f"{forbidden} is called from the alias store")


def _admin():
    class _A:
        is_admin = True
        username = "admin"
        permissions = Permissions(features=("av", "usb", "multiview"))
    return _A()


if __name__ == "__main__":
    unittest.main()


class DuplicateLabelTests(unittest.TestCase):
    """Two rooms may each hold a "Front Display"; an operator must still choose.

    Duplicates are not prevented -- the site may genuinely have them -- so the
    presentation changes only where a label actually repeats inside what this
    account can see.
    """

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="omni-dupe-"))
        self.aliases = AliasStore(self.dir / "device_aliases.json")
        self.users = UserStore(self.dir / "users.json")
        self.users.create("teacher", "teacher-password-1", omni_users.ROLE_USER,
                          Permissions(features=("av",),
                                      sources=(TEACHER_PC, DOC_CAMERA),
                                      destinations=(FRONT_DISPLAY, LAB_DISPLAY)))
        self.teacher = self.users.find_by_username("teacher")

    def project(self):
        return omni_projection.user_state(MATRIX, UNITS, self.teacher,
                                          self.aliases)

    def test_a_unique_label_is_left_exactly_as_it_is(self):
        self.aliases.set_alias(FRONT_DISPLAY, "Front Display")
        names = [d["name"] for d in self.project()["destinations"]]
        self.assertIn("Front Display", names,
                      "a unique name must not be decorated")

    def test_a_repeated_label_gains_the_device_name(self):
        self.aliases.set_alias(FRONT_DISPLAY, "Front Display")
        self.aliases.set_alias(LAB_DISPLAY, "Front Display")
        names = sorted(d["name"] for d in self.project()["destinations"])
        self.assertEqual(len(names), 2)
        for name in names:
            self.assertTrue(name.startswith("Front Display \u2014 "),
                            f"{name!r} is still ambiguous")
        self.assertNotEqual(names[0], names[1])

    def test_the_differentiator_is_the_device_name_not_an_address(self):
        self.aliases.set_alias(FRONT_DISPLAY, "Front Display")
        self.aliases.set_alias(LAB_DISPLAY, "Front Display")
        payload = self.project()
        names = [d["name"] for d in payload["destinations"]]
        # The device's own hostname, which is this file's fixture naming.
        self.assertTrue(any("hw-omni-d4111-7851dd" in n for n in names))
        self.assertTrue(any("hw-omni-d4111-lab" in n for n in names))
        # And never an address. The identity in `id` is what the browser posts
        # back rather than a label, so the check is on the names themselves.
        blob = json.dumps(names)
        for unit in UNITS:
            self.assertNotIn(unit["ip"], blob,
                             "an address must never be used to disambiguate")
            self.assertNotIn(unit["mac"].lower(), blob.lower(),
                             "a MAC must never be used as a label")

    def test_case_and_spacing_still_count_as_the_same_label(self):
        self.aliases.set_alias(FRONT_DISPLAY, "Front Display")
        self.aliases.set_alias(LAB_DISPLAY, "  front display ")
        names = [d["name"] for d in self.project()["destinations"]]
        self.assertTrue(all("\u2014" in n for n in names),
                        "a label differing only by case is still ambiguous")

    def test_a_duplicate_the_account_cannot_see_changes_nothing(self):
        """Ambiguity is judged over this account's own view."""
        self.users.set_permissions(self.teacher.id, Permissions(
            features=("av",), sources=(TEACHER_PC,),
            destinations=(FRONT_DISPLAY,)))
        teacher = self.users.find_by_username("teacher")
        self.aliases.set_alias(FRONT_DISPLAY, "Front Display")
        self.aliases.set_alias(LAB_DISPLAY, "Front Display")   # not visible
        payload = omni_projection.user_state(MATRIX, UNITS, teacher, self.aliases)
        self.assertEqual([d["name"] for d in payload["destinations"]],
                         ["Front Display"],
                         "a duplicate in another room must not decorate this one")

    def test_an_alias_matching_its_own_hostname_is_not_doubled(self):
        """"dec-front — dec-front" would be worse than leaving it alone."""
        self.aliases.set_alias(FRONT_DISPLAY, "hw-omni-d4111-7851dd")
        self.aliases.set_alias(LAB_DISPLAY, "hw-omni-d4111-7851dd")
        names = sorted(d["name"] for d in self.project()["destinations"])
        # The one whose label already is its own device name keeps it; the
        # other gains its own, so the pair is distinguishable without a
        # tautology like "X \u2014 X".
        self.assertIn("hw-omni-d4111-7851dd", names)
        self.assertIn("hw-omni-d4111-7851dd \u2014 hw-omni-d4111-lab", names)
