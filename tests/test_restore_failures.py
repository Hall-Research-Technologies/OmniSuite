"""A restore that fails at any stage leaves no hybrid -- in the files or in the process.

test_restore_atomicity covers the files: a write that fails, a rollback that cannot
finish, a file the restore created. This covers the other half and the stages that
were not injected before:

    removing a file the backup says is gone      -> rolled back, controlled answer
    the process failing to pick the restore up   -> files are the restore's, the
                                                    process says so, nobody stays in
    a refused or failed restore                  -> nobody is signed out for it
    after RESTART REQUIRED                       -> configuration writes answer 409

The last two are the ones that matter to a person: a restore that did not happen
must cost nobody their session, and a restore the process could not load must not
leave a way to write the old state over the new.

Real endpoints, throwaway data directory, network fenced.
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import omni_backup                                        # noqa: E402
import omni_users                                         # noqa: E402
from omni_users import Permissions                        # noqa: E402
from tests.test_restore_sessions import (                 # noqa: E402
    RestoreSessionBase, NEW_PASSWORD, drop_every_account)
from tests.test_display_groups import SRC                 # noqa: E402
from tests import test_user_routing as harness            # noqa: E402

FRONT = harness.FRONT_DISPLAY


class FailureBase(RestoreSessionBase):
    def setUp(self):
        super().setUp()
        self.snapshot_files = lambda: {
            p.name: p.read_bytes() for p in self.dir.iterdir() if p.is_file()
            and p.name not in ("audit.json",)}

    def failing(self, name, how="false"):
        real = omni_users._atomic_write_json

        def write(path, payload):
            if Path(path).name == name:
                if how == "raise":
                    raise OSError("the disk went away")
                return False
            return real(path, payload)
        return mock.patch.object(omni_users, "_atomic_write_json", write)


class AFailedRestoreCostsNobodyTheirSessionTests(FailureBase):
    def test_a_write_that_fails_signs_nobody_out(self):
        teacher, admin = self.session_for(self.teacher), self.session_for(self.admin)
        blob = self.backup_of(
            lambda o: o.set_password(o.find_by_username("teacher").id, NEW_PASSWORD))
        before = self.snapshot_files()
        with self.failing("users.json", how="raise"):
            reply = self.restore(blob)
        body = reply.get_json()
        self.assertEqual(reply.status_code, 500, body)
        self.assertEqual(body["status"], "FAILED — ROLLED BACK")
        self.assertEqual(self.snapshot_files(), before)
        self.assertTrue(self.alive(teacher), "a restore that did not happen signed someone out")
        self.assertTrue(self.alive(admin))
        # ...and the accounts the process holds are still the ones it had.
        self.assertTrue(self.store.authenticate("teacher", "teacher-password-1")["ok"])

    def test_a_refused_backup_signs_nobody_out(self):
        teacher = self.session_for(self.teacher)
        for blob in (b"", b"not a backup", b"\x00" * 4096):
            with self.subTest(size=len(blob)):
                reply = self.restore(blob)
                self.assertIn(reply.status_code, (400,), reply.get_data(as_text=True))
                self.assertTrue(reply.get_json()["error"])
                self.assertTrue(self.alive(teacher))

    def test_a_wrong_passphrase_signs_nobody_out_and_changes_nothing(self):
        import io
        teacher = self.session_for(self.teacher)
        before = self.snapshot_files()
        blob = self.backup_of(lambda o: None)
        reply = self.srv.app.test_client().post(
            "/api/backup/restore", content_type="multipart/form-data",
            data={"backup": (io.BytesIO(blob), "b.omnibak"),
                  "passphrase": "the wrong passphrase entirely"})
        self.assertEqual(reply.status_code, 400)
        self.assertEqual(self.snapshot_files(), before)
        self.assertTrue(self.alive(teacher))


class AFailedRestoreIsRecordedTests(FailureBase):
    def test_a_rolled_back_restore_is_one_failure_entry_and_no_success_entry(self):
        blob = self.backup_of(lambda o: None)
        with self.failing("users.json", how="raise"):
            self.restore(blob)
        events = [e for e in self.store.audit_entries(200)
                  if e["event"].startswith("backup.")]
        self.assertEqual([e["event"] for e in events], ["backup.restore_failed"])
        self.assertIs(events[0]["detail"]["rolled_back"], True)
        self.assertNotIn("passphrase", json.dumps(events[0]).lower())


class RemovingAFileFailsTests(FailureBase):
    def test_a_file_that_cannot_be_removed_rolls_everything_back(self):
        """The backup covered device names and found none: that file must go. If it
        cannot, nothing else may have been applied."""
        names = self.dir / "device_aliases.json"
        names.write_text(json.dumps({"aliases": {"b8:98:b0:00:00:11": "Front"}}),
                         encoding="utf-8")
        self.aliases._load()
        source = self.work / "nonames"
        source.mkdir()
        import shutil
        shutil.copy2(self.store._path, source / "users.json")
        payload = omni_backup.build_payload(source, [], app_version="V1.2.0")
        self.assertIn("device_names", payload["covered"])
        blob = omni_backup.seal(payload, "a-long-enough-backup-passphrase",
                                app_version="V1.2.0")
        before = self.snapshot_files()
        teacher = self.session_for(self.teacher)
        real_unlink = Path.unlink

        def refusing(self_, *a, **k):
            # The deployment's own copy, not the snapshot's: those are different
            # stages, and the snapshot has its own test below.
            if self_.name == "device_aliases.json" and self_.parent == self.dir:
                raise PermissionError("the file is locked")
            return real_unlink(self_, *a, **k)

        with mock.patch.object(Path, "unlink", refusing):
            reply = self.restore(blob)
        body = reply.get_json()
        self.assertEqual(reply.status_code, 500, body)
        self.assertIn("status", body, "the answer was not the restore's own: %r" % body)
        self.assertIn(body["status"], ("FAILED — ROLLED BACK",
                                       "FAILED — ROLLBACK INCOMPLETE"))
        self.assertFalse(body["ok"])
        self.assertTrue(body["error"])
        self.assertEqual(self.snapshot_files(), before,
                         "a half-applied restore was left behind")
        self.assertTrue(self.alive(teacher))


class TheWayBackCannotBeMadeTests(FailureBase):
    def test_no_snapshot_means_nothing_is_touched_and_nobody_is_signed_out(self):
        teacher = self.session_for(self.teacher)
        blob = self.backup_of(drop_every_account)
        before = self.snapshot_files()
        with mock.patch.object(omni_backup, "snapshot", side_effect=OSError("no space")):
            reply = self.restore(blob)
        body = reply.get_json()
        self.assertEqual(reply.status_code, 500, body)
        self.assertEqual(body["reason"], "no_snapshot")
        self.assertIn("nothing was changed", body["error"])
        self.assertEqual(self.snapshot_files(), before)
        self.assertTrue(self.alive(teacher))
        self.assertFalse(self.srv._restart_required)


class TheProcessCannotPickTheRestoreUpTests(FailureBase):
    def setUp(self):
        super().setUp()
        self.blob_same = self.backup_of(lambda o: None)

    def assert_restart_required(self, reply):
        self.assertEqual(reply.status_code, 200, reply.get_data(as_text=True))
        body = reply.get_json()
        self.assertEqual(body["status"], "RESTORED — RESTART REQUIRED")
        self.assertTrue(body["error"], "it said nothing about restarting")
        self.assertTrue(self.srv._restart_required)

    def test_a_failure_saving_the_device_cache_is_a_restart_not_a_lie(self):
        user, admin = self.session_for(self.teacher), self.session_for(self.admin)
        with mock.patch.object(self.srv, "_save_cache", side_effect=OSError("full")):
            reply = self.restore(self.blob_same)
        self.assert_restart_required(reply)
        self.assertFalse(self.alive(user))
        self.assertFalse(self.alive(admin))

    def test_a_failure_loading_the_activity_settings_is_the_same(self):
        with mock.patch.object(self.store, "_load_activity_settings",
                               side_effect=ValueError("bad")):
            reply = self.restore(self.blob_same)
        self.assert_restart_required(reply)

    def test_a_failure_merging_devices_is_the_same(self):
        with mock.patch.object(omni_backup, "merge_devices",
                               side_effect=RuntimeError("bad")):
            reply = self.restore(self.blob_same)
        self.assert_restart_required(reply)

    def test_until_it_restarts_no_configuration_write_can_undo_the_restore(self):
        with mock.patch.object(self.srv, "_save_cache", side_effect=OSError("full")):
            self.restore(self.blob_same)
        admin = self.session_for(self.admin)
        for path, body in (
                ("/api/admin/users", {"username": "late", "password": "late-password-1",
                                      "role": "user"}),
                ("/api/clear_units", {})):
            with self.subTest(path=path):
                reply = self.post(path, body, admin)
                self.assertIn(reply.status_code, (401, 403, 409), reply.get_data(as_text=True))
        self.assertIsNone(self.store.find_by_username("late"))

    def test_the_answer_is_a_message_and_never_a_stack_trace(self):
        with mock.patch.object(self.srv, "_save_cache", side_effect=OSError("C:\\secret\\path")):
            reply = self.restore(self.blob_same)
        raw = reply.get_data(as_text=True)
        self.assertNotIn("Traceback", raw)
        self.assertNotIn("secret", raw, "an internal path reached the operator")


class AllAccountsGoAndTheComputerCanStillBeRecoveredTests(FailureBase):
    def test_a_restore_that_removes_everyone_and_then_fails_to_reload_keeps_the_way_in(self):
        blob = self.backup_of(drop_every_account)
        with mock.patch.object(self.srv, "_save_cache", side_effect=OSError("full")):
            reply = self.restore(blob)
        self.assertEqual(reply.get_json()["status"], "RESTORED — RESTART REQUIRED")
        # Nobody is signed in, and the machine itself can still say who may.
        self.assertEqual(self.store.active_session_count(), 0)
        state = self.srv.app.test_client().get("/api/bootstrap/state").get_json()
        self.assertTrue(state["needs_admin"])


if __name__ == "__main__":
    unittest.main()
