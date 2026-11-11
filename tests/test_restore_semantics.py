"""What a restore does to a record the backup does not carry.

A backup is authoritative for what it covers. A section it carries replaces the
installation's copy wholesale, so any record the backup does not hold goes. A
whole managed section the backup COVERED but that had no file when it was made
means "there was none", and restoring leaves none. A backup that never looked at
a section (older than the section, or one that does not say what it covered)
says nothing about it, and the installation's copy is left alone.

Machine and environment settings are never removed by a restore, and the files a
computer regenerates are not touched at all.

    REPLACED BY RESTORE   every section the backup carries
    REMOVED BY RESTORE    accounts, device names, display groups, notices,
                          appearance, topology notices -- when covered and absent
    PRESERVED LOCALLY     everything else that is absent from the backup:
                          Multiview layouts and groups, USB associations,
                          activity retention, device credentials, scan
                          preferences, Remote Access settings, USB extender
                          ranges (held in memory by the running process, or
                          describing this computer)
    REGENERATED           scan results, the CSV view, firmware cache, certificate
                          and key, the activity log -- never exported, never
                          written by a restore

The restore stays one transaction: a failure puts back every file, including a
file the restore removed.

Real endpoint, throwaway data directory, network fenced.
"""
from __future__ import annotations

import json
import shutil
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import omni_backup                                        # noqa: E402
import omni_inventory                                     # noqa: E402
import omni_users                                         # noqa: E402
from tests.test_backup import (CAMERA, FRONT, PASSPHRASE, UNITS,   # noqa: E402
                               a_deployment)
from tests.test_restore_atomicity import Base            # noqa: E402

GROUPS_FILE = {"version": 1, "groups": [
    {"id": "grp_existing", "name": "Existing group", "members": [FRONT],
     "created_at": 1.0, "updated_at": 1.0}]}


def sealed(source_dir, mutate=None):
    payload = omni_backup.build_payload(source_dir, UNITS, app_version="V1.2.0")
    if mutate:
        mutate(payload)
    return omni_backup.seal(payload, PASSPHRASE, app_version="V1.2.0")


class RestoreSemanticsBase(Base):
    def setUp(self):
        super().setUp()
        groups = self.srv._display_groups
        saved = groups._path
        self.addCleanup(lambda: (setattr(groups, "_path", saved), groups._load()))
        groups._path = self.target / "display_groups.json"
        (self.target / "display_groups.json").write_text(
            json.dumps(GROUPS_FILE), encoding="utf-8")
        groups._load()
        self.before = self.state()

    def use(self, blob):
        self.blob = blob

    def restore_ok(self):
        reply = self.restore()
        self.assertEqual(reply.status_code, 200, reply.get_data(as_text=True))
        return reply.get_json()


class ARecordTheBackupDoesNotCarryTests(RestoreSemanticsBase):
    def test_a_covered_managed_section_that_was_empty_is_removed(self):
        self.use(sealed(self.tmp / "source"))                 # no display_groups.json
        body = self.restore_ok()
        self.assertIn("display_groups.json", body["applied"]["removed"])
        self.assertFalse((self.target / "display_groups.json").exists())
        self.assertEqual(self.srv._display_groups.all(), [],
                         "the running process still holds the removed groups")

    def test_the_same_goes_for_device_names(self):
        shutil.rmtree(self.tmp / "source")
        source = a_deployment(self.tmp / "source")
        (source / "device_aliases.json").unlink()
        self.use(sealed(source))
        body = self.restore_ok()
        self.assertIn("device_aliases.json", body["applied"]["removed"])
        self.assertEqual(self.srv._device_aliases.all(), {})

    def test_a_record_inside_a_carried_section_is_removed_too(self):
        """Replaced wholesale: the backup's list, and nothing the target added."""
        shutil.rmtree(self.tmp / "source")
        source = a_deployment(self.tmp / "source", aliases={CAMERA: "Camera"})
        self.use(sealed(source))
        self.restore_ok()
        self.assertEqual(self.srv._device_aliases.all(), {CAMERA: "Camera"})

    def test_machine_and_in_memory_settings_are_preserved_when_absent(self):
        shutil.rmtree(self.tmp / "source")
        source = a_deployment(self.tmp / "source")
        keep_names = ("multiview_groups.json", "usb_associations.json",
                      "usb_extenders.json", "scan_preferences.json", "config.json",
                      "remote_access.json", "multiview_meta.json")
        marker = {}
        for name in keep_names:
            (source / name).unlink()
            marker[name] = (self.target / name).read_bytes()
        self.use(sealed(source))
        body = self.restore_ok()
        for name in keep_names:
            self.assertTrue((self.target / name).exists(), name)
            self.assertEqual((self.target / name).read_bytes(), marker[name], name)
            self.assertNotIn(name, body["applied"]["removed"])

    def test_a_backup_that_does_not_say_what_it_covered_removes_nothing(self):
        self.use(sealed(self.tmp / "source", lambda p: p.pop("covered")))
        body = self.restore_ok()
        self.assertEqual(body["applied"]["removed"], [])
        self.assertTrue((self.target / "display_groups.json").exists())
        self.assertEqual(len(self.srv._display_groups.all()), 1)

    def test_what_a_computer_regenerates_is_never_touched(self):
        for name in omni_backup.REGENERATED:
            (self.target / name).write_bytes(b"local " + name.encode())
        regenerated = {n: (self.target / n).read_bytes() for n in omni_backup.REGENERATED}
        self.use(sealed(self.tmp / "source"))
        self.restore_ok()
        for name, content in regenerated.items():
            self.assertEqual((self.target / name).read_bytes(), content, name)

    def test_the_removal_set_is_exactly_the_documented_one(self):
        self.assertEqual(sorted(omni_backup.REMOVED_WHEN_ABSENT), sorted([
            "accounts", "device_names", "display_groups", "notices", "appearance",
            "topology_notices"]))
        names = {s.name for s in omni_backup.SECTIONS}
        self.assertTrue(omni_backup.REMOVED_WHEN_ABSENT <= names)

    def test_a_backup_records_every_section_it_looked_at(self):
        payload = omni_backup.build_payload(self.tmp / "source", UNITS)
        self.assertEqual(payload["covered"], [s.name for s in omni_backup.SECTIONS])
        self.assertNotIn("display_groups", payload["sections"])

    def test_a_damaged_coverage_list_is_refused_before_anything_changes(self):
        for bad in ("everything", [1, 2], {"a": 1}):
            with self.subTest(bad=bad):
                self.use(sealed(self.tmp / "source",
                                lambda p, b=bad: p.__setitem__("covered", b)))
                reply = self.restore()
                self.assertEqual(reply.status_code, 400)
                self.assertEqual(self.state(), self.before)


class TheRestoreIsStillOneTransactionTests(RestoreSemanticsBase):
    def test_a_failure_after_a_removal_puts_the_removed_file_back(self):
        self.use(sealed(self.tmp / "source"))
        with self.failing_write("usb_associations.json"):
            reply = self.restore()
        body = reply.get_json()
        self.assertEqual(reply.status_code, 500, body)
        self.assertEqual(body["status"], "FAILED — ROLLED BACK")
        self.assertEqual(self.state(), self.before,
                         "a hybrid: the removal was kept although the restore failed")
        self.assertTrue((self.target / "display_groups.json").exists())

    def test_a_failure_before_the_removal_leaves_the_file_alone(self):
        self.use(sealed(self.tmp / "source"))
        with self.failing_write("users.json"):
            reply = self.restore()
        self.assertEqual(reply.status_code, 500)
        self.assertEqual(self.state(), self.before)


class RoutingInterfaceMigrationTests(RestoreSemanticsBase):
    """The Standard / Matrix choice rides in the accounts file and migrates safely."""

    def _backup_with(self, interfaces):
        shutil.rmtree(self.tmp / "source")
        source = a_deployment(self.tmp / "source")
        users = json.loads((source / "users.json").read_text(encoding="utf-8"))
        for entry in users["users"]:
            if entry["role"] == "user":
                if interfaces is None:
                    entry["permissions"].pop("interfaces", None)
                else:
                    entry["permissions"]["interfaces"] = interfaces
        (source / "users.json").write_text(json.dumps(users), encoding="utf-8")
        self.use(sealed(source))

    def teacher(self):
        return self.srv._user_store.find_by_username("teacher")

    def test_an_older_backup_keeps_standard_routing_and_gains_no_matrix(self):
        self._backup_with(None)
        self.restore_ok()
        interfaces = self.teacher().permissions.interfaces
        self.assertEqual(set(interfaces), {omni_users.INTERFACE_STANDARD})

    def test_the_choice_survives_a_backup_and_restore(self):
        for chosen in (["matrix"], ["matrix", "standard"], ["standard"]):
            with self.subTest(chosen=chosen):
                self._backup_with(chosen)
                self.restore_ok()
                self.assertEqual(set(self.teacher().permissions.interfaces), set(chosen))

    def test_an_explicit_choice_of_neither_is_kept_as_neither(self):
        self._backup_with([])
        self.restore_ok()
        self.assertEqual(set(self.teacher().permissions.interfaces), set())

    def test_the_account_file_on_disk_carries_the_field(self):
        self._backup_with(["matrix"])
        self.restore_ok()
        on_disk = json.loads((self.target / "users.json").read_text(encoding="utf-8"))
        teacher = next(u for u in on_disk["users"] if u["username"] == "teacher")
        self.assertEqual(teacher["permissions"]["interfaces"], ["matrix"])


class AssignmentsFollowARestoreTests(RestoreSemanticsBase):
    def test_what_is_protected_is_what_the_restored_accounts_hold(self):
        before = omni_inventory.collect(self.srv._user_store, self.srv._display_groups)
        self.assertNotIn(CAMERA, before)
        self.use(sealed(self.tmp / "source"))
        self.restore_ok()
        after = omni_inventory.collect(self.srv._user_store, self.srv._display_groups)
        self.assertEqual(after[CAMERA]["users"], ["teacher"])
        self.assertEqual(after[FRONT]["users"], ["teacher"])
        self.assertEqual(after[FRONT]["groups"], [],
                         "the removed group must stop protecting its members")


if __name__ == "__main__":
    unittest.main()
