"""A restore must leave the old configuration or the new one, never a mixture.

Reading the restore path turned up five ways to end up with a hybrid, each
reproduced here against the real endpoint before it was fixed:

  1. A write that fails by returning False was counted as written. The restore
     went on, reported RESTORED, and left some files restored and others not.
  2. A file the restore created was never removed by the rollback, which only
     put back the files that existed beforehand.
  3. The rollback copied over live files in place, so an interruption in the
     middle of it truncated the very file it was recovering.
  4. Nothing stopped a normal write landing in the middle. An account store keeps
     its accounts in memory and rewrites the whole file on any change, so a
     change made between "users.json restored" and "reload from disk" wrote the
     old accounts straight back over the restored ones.
  5. If reloading in place failed after the files were applied, the process still
     held the old state, and the next ordinary save wrote it over the restore.

Real endpoint, throwaway data directory, network fenced.
"""
from __future__ import annotations

import copy
import io
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("OMNI_SMOKE", "1")

import omni_backup                                        # noqa: E402
import omni_users                                         # noqa: E402
from tests.test_backup import (PASSPHRASE, UNITS, a_deployment)  # noqa: E402


def a_single_account(name):
    return {"version": 1, "users": [
        {"id": "existing-1", "username": name,
         "password_hash": "scrypt:32768:8:1$zzz$yyy", "role": "admin",
         "enabled": True, "permissions": {"features": [], "sources": [],
                                          "destinations": [], "multiviews": []},
         "created_at": 9.0, "password_changed_at": 9.0,
         "direct_access": {"enabled": False, "digest": "", "blob": "",
                           "protection": "", "created_at": 0}}]}


class Base(unittest.TestCase):

    def setUp(self):
        import OmniMatrix_upgrade_server_v7_6y as srv
        self.srv = srv
        self.tmp = Path(tempfile.mkdtemp(prefix="omni-restore-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

        source = a_deployment(self.tmp / "source")
        payload = omni_backup.build_payload(source, UNITS, app_version="V1.2.0")
        self.blob = omni_backup.seal(payload, PASSPHRASE, app_version="V1.2.0")

        self.target = a_deployment(self.tmp / "target",
                                   aliases={"b8:98:b0:00:00:11": "Existing Name"})
        (self.target / "users.json").write_text(
            json.dumps(a_single_account("existing-admin")), encoding="utf-8")
        self.snapshot_dir = self.tmp / "snapshot"

        store, aliases = srv._user_store, srv._device_aliases
        saved = (store._path, store._audit_path, aliases._path,
                 srv._backup_data_dir, srv.BACKUP_SNAPSHOT_DIR)
        self.accounts_before = dict(store._accounts)
        self.sessions_before = dict(store._sessions)

        def restore():
            store._path, store._audit_path, aliases._path = saved[0], saved[1], saved[2]
            srv._backup_data_dir, srv.BACKUP_SNAPSHOT_DIR = saved[3], saved[4]
            store._accounts.clear()
            store._accounts.update(self.accounts_before)
            store._sessions.clear()
            store._sessions.update(self.sessions_before)
            for name in ("_restore_in_progress", "_restart_required"):
                if hasattr(srv, name):
                    setattr(srv, name, False)

        self.addCleanup(restore)
        store._path = self.target / "users.json"
        store._audit_path = self.tmp / "audit.json"
        aliases._path = self.target / "device_aliases.json"
        srv._backup_data_dir = lambda: self.target
        srv.BACKUP_SNAPSHOT_DIR = self.snapshot_dir
        store._load()
        aliases._load()

        self.before = self.state()

    def state(self):
        return {p.name: p.read_bytes() for p in self.target.iterdir() if p.is_file()}

    def restore(self, **extra):
        client = self.srv.app.test_client()
        data = {"backup": (io.BytesIO(self.blob), "b.omnibackup"),
                "passphrase": PASSPHRASE}
        return client.post("/api/backup/restore", data=data,
                           content_type="multipart/form-data")

    def failing_write(self, name, real=omni_users._atomic_write_json, how="false",
                      before=None):
        def write(path, payload):
            if before and Path(path).name == before[0]:
                before[1]()
            if Path(path).name == name:
                if how == "raise":
                    raise OSError("the disk went away")
                return False
            return real(path, payload)
        return mock.patch.object(omni_users, "_atomic_write_json", write)


class AFailedWriteIsAFailedRestoreTests(Base):

    def test_a_write_that_returns_false_rolls_everything_back(self):
        with self.failing_write("usb_associations.json"):
            response = self.restore()
        body = response.get_json()
        self.assertEqual(response.status_code, 500, body)
        self.assertEqual(body["status"], "FAILED — ROLLED BACK")
        self.assertEqual(self.state(), self.before,
                         "some files were restored and some were not")

    def test_a_write_that_raises_rolls_everything_back(self):
        with self.failing_write("usb_associations.json", how="raise"):
            response = self.restore()
        self.assertEqual(response.get_json()["status"], "FAILED — ROLLED BACK")
        self.assertEqual(self.state(), self.before)

    def test_a_file_the_restore_created_is_removed_by_the_rollback(self):
        (self.target / "multiview_groups.json").unlink()
        self.before = self.state()
        with self.failing_write("usb_associations.json"):
            response = self.restore()
        self.assertEqual(response.get_json()["status"], "FAILED — ROLLED BACK")
        self.assertFalse((self.target / "multiview_groups.json").exists(),
                         "a half-applied restore left a file that was never there")
        self.assertEqual(self.state(), self.before)

    def test_a_stale_snapshot_from_an_earlier_restore_is_never_resurrected(self):
        """The snapshot folder is reused. What it held last time must not come back."""
        self.snapshot_dir.mkdir()
        (self.snapshot_dir / "multiview_groups.json").write_text(
            '{"groups": {"stale": []}}', encoding="utf-8")
        (self.target / "multiview_groups.json").unlink()
        self.before = self.state()
        with self.failing_write("usb_associations.json"):
            self.restore()
        self.assertFalse((self.target / "multiview_groups.json").exists())
        self.assertEqual(self.state(), self.before)
        # The folder is also what a person recovers from by hand, so it must hold
        # this restore's files and nothing an earlier one left.
        self.assertFalse((self.snapshot_dir / "multiview_groups.json").exists(),
                         "the snapshot folder still holds an earlier restore's file")


class ARollbackThatFailsIsSaidLoudlyTests(Base):

    def test_it_is_reported_as_incomplete_and_the_evidence_is_kept(self):
        def broken(src, dst):
            raise OSError("the volume is read-only")

        with self.failing_write("usb_associations.json"), \
                mock.patch.object(omni_backup, "_atomic_copy", broken, create=True):
            response = self.restore()
        body = response.get_json()
        self.assertEqual(response.status_code, 500)
        self.assertEqual(body["status"], "FAILED — ROLLBACK INCOMPLETE", body)
        self.assertTrue(body["rollback_failures"])
        self.assertFalse(body.get("ok"))
        self.assertTrue(body.get("recovery_snapshot"),
                        "the operator is not told where the way back is")
        self.assertTrue((Path(body["recovery_snapshot"]) / "users.json").exists(),
                        "the snapshot was discarded while the rollback was incomplete")

    def test_the_rollback_never_truncates_a_live_file_in_place(self):
        """An interrupted copy must leave the old file or the new one, whole."""
        seen = []
        real = omni_backup._atomic_copy if hasattr(omni_backup, "_atomic_copy") else None
        self.assertIsNotNone(real, "the rollback has no atomic copy")

        def watching(src, dst):
            seen.append(Path(dst).name)
            return real(src, dst)

        with self.failing_write("usb_associations.json"), \
                mock.patch.object(omni_backup, "_atomic_copy", watching):
            self.restore()
        self.assertIn("users.json", seen)


class ANormalWriteCannotLandInTheMiddleTests(Base):

    def test_an_account_change_during_the_restore_does_not_undo_it(self):
        errors = []
        ready = threading.Event()

        def concurrent_change():
            try:
                self.srv._user_store.create(
                    "latecomer", "latecomer-pass-1", omni_users.ROLE_USER)
            except Exception as exc:                      # pragma: no cover
                errors.append(exc)

        thread = threading.Thread(target=concurrent_change, daemon=True)

        def start_it():
            thread.start()
            thread.join(0.5)          # long enough to have written, if it can

        with self.failing_write("never.json", before=("device_aliases.json", start_it)):
            response = self.restore()
        thread.join(5)
        self.assertEqual(response.get_json()["status"], "RESTORED", response.get_json())
        self.assertFalse(errors, errors)
        names = {a["username"] for a in
                 json.loads((self.target / "users.json").read_text())["users"]}
        self.assertIn("teacher", names, "the restored accounts were overwritten")
        self.assertNotIn("existing-admin", names,
                         "the old accounts were written back over the restore")

    def test_an_alias_change_during_the_restore_does_not_undo_it(self):
        thread = threading.Thread(
            target=lambda: self.srv._device_aliases.set_alias(
                "b8:98:b0:00:00:99", "Late Name"), daemon=True)

        def start_it():
            thread.start()
            thread.join(0.5)

        # The first file written after the aliases is the multiview section.
        with self.failing_write("never.json", before=("multiview_meta.json", start_it)):
            response = self.restore()
        thread.join(5)
        self.assertEqual(response.get_json()["status"], "RESTORED")
        aliases = json.loads((self.target / "device_aliases.json").read_text())
        self.assertNotIn("Existing Name", json.dumps(aliases),
                         "the previous names were written back over the restore")
        self.assertIn("Hall Display", json.dumps(aliases))

    def test_other_writes_are_refused_while_it_runs(self):
        seen = {}

        def poke():
            client = self.srv.app.test_client()
            seen["response"] = client.post(
                "/api/scan_preferences", json={"scan_expression": "198.51.100.1"})

        with self.failing_write("never.json", before=("device_aliases.json", poke)):
            self.restore()
        response = seen["response"]
        self.assertEqual(response.status_code, 409, response.get_data(as_text=True))
        self.assertEqual(response.get_json()["reason"], "restore_in_progress")

    def test_reads_still_work_while_it_runs(self):
        seen = {}

        def poke():
            seen["response"] = self.srv.app.test_client().get("/api/version")

        with self.failing_write("never.json", before=("device_aliases.json", poke)):
            self.restore()
        self.assertNotEqual(seen["response"].status_code, 409)

    def test_a_second_restore_cannot_start_inside_the_first(self):
        seen = {}

        def again():
            seen["response"] = self.restore()

        with self.failing_write("never.json", before=("device_aliases.json", again)):
            first = self.restore()
        self.assertEqual(seen["response"].status_code, 409)
        self.assertEqual(seen["response"].get_json()["reason"], "restore_in_progress")
        self.assertEqual(first.get_json()["status"], "RESTORED")

    def test_the_lock_is_released_after_a_failure(self):
        with self.failing_write("usb_associations.json"):
            self.restore()
        response = self.srv.app.test_client().post(
            "/api/scan_preferences", json={"scan_expression": "198.51.100.1"})
        self.assertNotEqual(response.status_code, 409,
                            "a failed restore left the application refusing writes")
        again = self.restore()
        self.assertEqual(again.get_json()["status"], "RESTORED")


class AGroupChangeCannotLandInTheMiddleTests(Base):
    """Display Groups are a store the restore replaces, so they are held too.

    Like the accounts and names, the group store keeps its contents in memory and
    rewrites the whole file on a change. A group edited between "display_groups.json
    restored" and "reloaded from disk" would write the old groups straight back
    over the restored ones.
    """

    def setUp(self):
        super().setUp()
        import omni_groups
        from tests.test_backup import FRONT
        groups = self.srv._display_groups
        saved = (groups._path, dict(groups._groups))

        def put_back():
            groups._path = saved[0]
            groups._groups = saved[1]

        self.addCleanup(put_back)
        canonical = omni_users._normalise_identity

        # The backup being restored carries one group...
        source = a_deployment(self.tmp / "source_with_groups")
        omni_groups.GroupStore(source / "display_groups.json",
                               canonical).create("Restored", [FRONT])
        payload = omni_backup.build_payload(source, UNITS, app_version="V1.2.0")
        self.blob = omni_backup.seal(payload, PASSPHRASE, app_version="V1.2.0")

        # ...and the installation being restored over has a different one.
        groups._path = self.target / "display_groups.json"
        groups._groups = {}
        groups.create("Old", [FRONT])

    def names_on_disk(self):
        data = json.loads((self.target / "display_groups.json").read_text())
        return {g["name"] for g in data["groups"]}

    def test_a_group_change_during_the_restore_does_not_undo_it(self):
        thread = threading.Thread(
            target=lambda: self.srv._display_groups.create("Late", []),
            daemon=True)

        def start_it():
            thread.start()
            thread.join(0.5)          # long enough to have written, if it can

        # display_groups.json is written before multiview_meta.json.
        with self.failing_write("never.json", before=("multiview_meta.json", start_it)):
            response = self.restore()
        thread.join(5)
        self.assertEqual(response.get_json()["status"], "RESTORED", response.get_json())
        names = self.names_on_disk()
        self.assertIn("Restored", names, "the restored groups were overwritten")
        self.assertNotIn("Old", names, "the old groups were written back over the restore")


class ALoadThatFailsAfterTheFilesAreAppliedTests(Base):

    def test_further_writes_are_refused_until_a_restart(self):
        with mock.patch.object(self.srv._device_aliases, "_load",
                               side_effect=RuntimeError("could not read")):
            response = self.restore()
        self.assertEqual(response.get_json()["status"], "RESTORED — RESTART REQUIRED")
        refused = self.srv.app.test_client().post(
            "/api/scan_preferences", json={"scan_expression": "198.51.100.1"})
        self.assertEqual(refused.status_code, 409)
        self.assertEqual(refused.get_json()["reason"], "restart_required")
        # Reading, and signing in, still work: it is the saves that would overwrite.
        self.assertNotEqual(self.srv.app.test_client().get("/api/version").status_code, 409)

    def test_the_restored_files_are_left_exactly_as_restored(self):
        with mock.patch.object(self.srv._device_aliases, "_load",
                               side_effect=RuntimeError("could not read")):
            self.restore()
        self.assertNotEqual(self.state(), self.before)
        aliases = json.loads((self.target / "device_aliases.json").read_text())
        self.assertIn("Hall Display", json.dumps(aliases))


if __name__ == "__main__":
    unittest.main()
