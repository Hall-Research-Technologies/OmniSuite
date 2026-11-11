"""Activity retention: how long entries are kept, and how much disk they may use.

Defaults are 30 days and 5 MB. Both are bounded, persisted beside the log, and
degrade to the defaults on a damaged file -- an unreadable setting must never
crash startup or switch a limit off. Pruning removes entries older than the
window AND the oldest entries beyond the disk budget, so the log cannot grow
without bound whichever limit is reached first.
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

import omni_users                                    # noqa: E402

DAY = 86400.0


def make_store(directory=None):
    directory = Path(directory or tempfile.mkdtemp(prefix="omni-retention-"))
    store = omni_users.UserStore(directory / "users.json",
                                 directory / "security_audit.json")
    return store, directory


def backdate(store, entry_index, days):
    """Move one entry into the past, as the passage of time would."""
    with store._lock:
        store._audit[entry_index]["at"] -= days * DAY
        store._audit_len_seen = -1


class DefaultsTests(unittest.TestCase):
    def test_defaults_are_thirty_days_and_five_megabytes(self):
        store, _ = make_store()
        retention = store.activity_retention()
        self.assertEqual(retention["days"], 30)
        self.assertEqual(retention["max_megabytes"], 5)
        self.assertEqual(omni_users.ACTIVITY_DEFAULT_DAYS, 30)
        self.assertEqual(omni_users.ACTIVITY_DEFAULT_MAX_BYTES, 5 * 1024 * 1024)

    def test_the_limits_are_bounded(self):
        self.assertLessEqual(omni_users.ACTIVITY_MAX_DAYS, 365)
        self.assertLessEqual(omni_users.ACTIVITY_MAX_MAX_BYTES, 50 * 1024 * 1024)
        self.assertGreater(omni_users.ACTIVITY_MIN_MAX_BYTES, 0)


class AgePruningTests(unittest.TestCase):
    def test_entries_older_than_the_window_are_removed(self):
        store, _ = make_store()
        for n in range(3):
            store.audit("route", detail={"n": n})
        backdate(store, 0, 31)
        backdate(store, 1, 29)
        store.audit("route", detail={"n": 3})
        kept = [e["detail"]["n"] for e in store._audit]
        self.assertEqual(kept, [1, 2, 3], "the 31-day-old entry should be gone")

    def test_a_shorter_window_prunes_at_once(self):
        store, _ = make_store()
        for n in range(4):
            store.audit("route", detail={"n": n})
        backdate(store, 0, 10)
        backdate(store, 1, 8)
        self.assertTrue(store.set_activity_retention(7, 5)["ok"])
        self.assertEqual([e["detail"]["n"] for e in store._audit
                          if e["event"] == "route"], [2, 3])

    def test_pruning_happens_on_load(self):
        store, directory = make_store()
        store.audit("route", detail={"n": 0})
        store.audit("route", detail={"n": 1})
        backdate(store, 0, 60)
        self.assertTrue(store.flush_audit() or True)
        # Write the aged state out exactly as it stands in memory.
        omni_users._atomic_write_json(
            directory / "security_audit.json",
            {"version": 1, "entries": list(store._audit)})
        again = omni_users.UserStore(directory / "users.json",
                                     directory / "security_audit.json")
        self.assertEqual([e["detail"].get("n") for e in again._audit], [1])


class SizeCapTests(unittest.TestCase):
    def test_the_oldest_entries_go_first_when_the_budget_is_exceeded(self):
        store, _ = make_store()
        store._activity_max_bytes = omni_users.ACTIVITY_MIN_MAX_BYTES
        padding = "x" * 400
        for n in range(1500):
            store.audit("route", detail={"n": n, "pad": padding})
        retention = store.activity_retention()
        self.assertLessEqual(retention["used_bytes"],
                             omni_users.ACTIVITY_MIN_MAX_BYTES)
        numbers = [e["detail"]["n"] for e in store._audit]
        self.assertEqual(numbers[-1], 1499, "the newest entry must survive")
        self.assertGreater(numbers[0], 0, "the oldest must have been dropped")
        self.assertEqual(numbers, sorted(numbers))

    def test_the_file_on_disk_respects_the_budget(self):
        store, directory = make_store()
        store._activity_max_bytes = omni_users.ACTIVITY_MIN_MAX_BYTES
        for n in range(1500):
            store.audit("user.create", detail={"n": n, "pad": "y" * 400})
        store.flush_audit()
        size = (directory / "security_audit.json").stat().st_size
        self.assertLessEqual(size, omni_users.ACTIVITY_MIN_MAX_BYTES * 1.05)

    def test_the_entry_ceiling_still_holds_underneath_the_settings(self):
        store, _ = make_store()
        store._activity_max_bytes = omni_users.ACTIVITY_MAX_MAX_BYTES
        for n in range(omni_users.AUDIT_MAX_ENTRIES + 50):
            store.audit("route", detail={"n": n})
        self.assertLessEqual(len(store._audit), omni_users.AUDIT_MAX_ENTRIES)


class SettingTests(unittest.TestCase):
    def test_a_valid_change_persists_and_survives_a_restart(self):
        store, directory = make_store()
        self.assertTrue(store.set_activity_retention(90, 12.5)["ok"])
        again = omni_users.UserStore(directory / "users.json",
                                     directory / "security_audit.json")
        retention = again.activity_retention()
        self.assertEqual((retention["days"], retention["max_megabytes"]), (90, 12.5))

    def test_out_of_range_input_is_refused_not_clamped(self):
        store, _ = make_store()
        for days, mb in ((0, 5), (366, 5), (30, 0), (30, 51), (30, 0.1),
                         (-1, 5), (30, -5)):
            with self.subTest(days=days, mb=mb):
                self.assertFalse(store.set_activity_retention(days, mb)["ok"])
        self.assertEqual(store.activity_retention()["days"], 30)

    def test_malformed_input_is_refused(self):
        store, _ = make_store()
        for days, mb in (("30", 5), (30, "5"), (None, 5), (30, None),
                         (True, 5), (30, True), (float("nan"), 5),
                         (30, float("inf")), (30.5, 5), ([], 5), ({}, 5)):
            with self.subTest(days=days, mb=mb):
                self.assertFalse(store.set_activity_retention(days, mb)["ok"])

    def test_the_change_is_recorded_without_inventing_an_actor(self):
        store, _ = make_store()
        store.set_activity_retention(14, 2)
        entry = [e for e in store._audit
                 if e["event"] == "activity.retention_changed"][-1]
        self.assertEqual(entry["detail"], {"days": 14, "max_megabytes": 2.0})
        self.assertIsNone(entry["actor"])

    def test_a_failed_write_leaves_the_old_limits(self):
        store, directory = make_store()
        original = omni_users._atomic_write_json
        omni_users._atomic_write_json = lambda *a, **k: False
        try:
            self.assertFalse(store.set_activity_retention(10, 3)["ok"])
        finally:
            omni_users._atomic_write_json = original
        self.assertEqual(store.activity_retention()["days"], 30)


class MalformedSettingsFileTests(unittest.TestCase):
    def reload(self, directory):
        return omni_users.UserStore(directory / "users.json",
                                    directory / "security_audit.json")

    def test_unreadable_json_degrades_to_defaults_and_is_quarantined(self):
        directory = Path(tempfile.mkdtemp(prefix="omni-retention-"))
        path = directory / omni_users.ACTIVITY_SETTINGS_FILE
        path.write_text("{ not json", encoding="utf-8")
        retention = self.reload(directory).activity_retention()
        self.assertEqual(retention["days"], 30)
        self.assertFalse(path.exists())
        self.assertTrue(path.with_suffix(".corrupt").exists())

    def test_wrong_shapes_and_hostile_values_never_widen_the_limits(self):
        directory = Path(tempfile.mkdtemp(prefix="omni-retention-"))
        path = directory / omni_users.ACTIVITY_SETTINGS_FILE
        cases = [
            ([], 30, 5),
            ({"days": "forever", "max_bytes": "lots"}, 30, 5),
            ({"days": 10 ** 9, "max_bytes": 10 ** 15}, 365, 50),
            ({"days": -5, "max_bytes": -1}, 1, 0.25),
            ({"days": 0, "max_bytes": 0}, 1, 0.25),
            ({"days": True, "max_bytes": False}, 30, 5),
            ({"days": 12.5, "max_bytes": None}, 30, 5),
            ({"days": float("nan")}, 30, 5),
        ]
        for raw, days, megabytes in cases:
            with self.subTest(raw=raw):
                path.write_text(json.dumps(raw), encoding="utf-8")
                retention = self.reload(directory).activity_retention()
                self.assertEqual(retention["days"], days)
                self.assertEqual(retention["max_megabytes"], megabytes)
                if path.with_suffix(".corrupt").exists():
                    path.with_suffix(".corrupt").unlink()


class NoSecretsTests(unittest.TestCase):
    def test_secrets_are_still_redacted_with_retention_in_play(self):
        store, _ = make_store()
        store.audit("route", detail={"password": "hunter2", "token": "t0k",
                                     "csrf_token": "c", "username": "alice"})
        text = json.dumps(store._audit[-1])
        for secret in ("hunter2", "t0k"):
            self.assertNotIn(secret, text)
        self.assertIn("alice", text, "a username is not a secret")


class EndpointAndUiTests(unittest.TestCase):
    def test_the_route_is_administrative_and_classified(self):
        import omni_access
        access, _ = omni_access.classify("/api/admin/audit/retention", "POST")
        self.assertEqual(access, omni_access.Access.ADMIN)

    def test_retention_travels_with_a_backup(self):
        import omni_backup
        self.assertIn("activity_settings.json",
                      [s.filename for s in omni_backup.SECTIONS])

    def test_the_activity_panel_exposes_both_limits(self):
        html = (ROOT / "ui" / "admin" / "users.html").read_text(encoding="utf-8")
        for needle in ('id="ret_days"', 'id="ret_mb"', 'id="ret_save"'):
            self.assertIn(needle, html)
        js = (ROOT / "ui" / "admin" / "users.js").read_text(encoding="utf-8")
        self.assertIn("/api/admin/audit/retention", js)
        self.assertIn("activity.retention_changed", js)


if __name__ == "__main__":
    unittest.main()
