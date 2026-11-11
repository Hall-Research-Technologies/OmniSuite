"""A restore captures units_cache.json first and puts it back, proven, if it fails.

An earlier build left units_cache.json out of the restore's pre-restore snapshot (a recorded
decision). This build captures its exact state -- present and valid, present but
malformed (raw bytes kept), or absent -- under the cache lock and then the
scan-results lock, and holds both until the last step that can change the file.
On a failure it puts the file back, reads it again and compares, and reports
truthfully when it could not.

What is asserted, in the order of the requirement:

  1  a successful restore snapshots the cache
  2  a failed restore after the cache changed restores the original bytes
  3  the previous device inventory comes back exactly
  4  an originally absent cache returns to absent (not to an empty list)
  5  a malformed pre-state is kept raw and restored raw; quarantine is intact
  6  a cache save failure during a restore is detected, and the file proven
  7  a rollback write failure is surfaced in the reply and the log
  8  a concurrent scan or poll cannot tear the pre-restore snapshot
  9  concurrent activity cannot overwrite the rollback
 10  the lock order stays cache then scan (statically and as acquired)
 11  a mixed workload including snapshot and rollback does not deadlock
 12  a backup export stays coherent after a rolled-back restore
 13  the established malformed-cache quarantine behaviour is intact

Private locks and a throwaway data directory; the network fence is the test
package's (tests/_fence.py); fixtures are deep-copied before server code sees them.
"""
from __future__ import annotations

import ast
import copy
import hashlib
import json
import shutil
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import omni_backup
import omni_matrix_logic as logic
from tests.test_backup import PASSPHRASE
from tests.test_cache_backup_consistency import (SERVER_SOURCE, bounded, generation,
                                                  private_locks)
from tests.test_restore_atomicity import Base as RestoreBase

ORIGINAL = [
    {"ip": "198.51.100.11", "mac": "b8:98:b0:00:00:11", "hostname": "", "pad": "q" * 80},
    {"ip": "198.51.100.21", "mac": "b8:98:b0:00:00:01", "hostname": "", "pad": "r" * 80},
]
FAIL_ON = "usb_associations.json"       # written late in a restore: earlier files are already replaced


class CacheRestoreBase(RestoreBase):

    def setUp(self):
        super().setUp()
        private_locks(self)
        self.cache = self.target / "units_cache.json"
        self.scan = self.target / "scan_results.json"
        for p in (mock.patch.object(self.srv, "CACHE", self.cache),
                  mock.patch.object(self.srv, "SCAN_RESULTS", self.scan),
                  mock.patch.object(logic, "_cache_file", self.cache)):
            p.start()
            self.addCleanup(p.stop)
        enc, dec = dict(logic._encoders), dict(logic._decoders)
        self.addCleanup(lambda: (logic._encoders.clear(), logic._encoders.update(enc),
                                 logic._decoders.clear(), logic._decoders.update(dec)))
        self.original_bytes = json.dumps(copy.deepcopy(ORIGINAL)).encode("utf-8")
        self.cache.write_bytes(self.original_bytes)
        self.before = self.state()

    # -- helpers ---------------------------------------------------------
    def snap_info(self):
        return json.loads((self.snapshot_dir / omni_backup.CACHE_MANIFEST).read_text("utf-8"))

    def change_cache(self, raw=None):
        """What some other managed-state change would do to the file, mid-restore."""
        data = raw if raw is not None else json.dumps(
            [{"ip": "198.51.100.200", "mac": "02:00:5e:00:00:c8", "hostname": "intruder"}]).encode()
        self.cache.write_bytes(data)

    def fail_after_changing_cache(self, raw=None):
        return self.failing_write(FAIL_ON, before=(FAIL_ON, lambda: self.change_cache(raw)))


class CaptureTests(unittest.TestCase):
    """snapshot_cache / rollback_cache on their own: three states, and proof."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="omni-cachesnap-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.cache = self.tmp / "units_cache.json"
        self.into = self.tmp / "snap"

    def test_the_three_states_are_told_apart(self):
        info = omni_backup.snapshot_cache(self.cache, self.into)
        self.assertEqual((info["state"], info["size"]), ("absent", 0))
        self.assertFalse((self.into / omni_backup.CACHE_SNAPSHOT).exists())
        self.cache.write_bytes(b'[{"ip": "x"}]')
        info = omni_backup.snapshot_cache(self.cache, self.into)
        self.assertEqual(info["state"], "valid")
        self.assertEqual(info["sha256"], hashlib.sha256(b'[{"ip": "x"}]').hexdigest())
        raw = b'[{"ip": "x"}, \xff\xfe garbage'
        self.cache.write_bytes(raw)
        info = omni_backup.snapshot_cache(self.cache, self.into)
        self.assertEqual(info["state"], "raw")
        self.assertEqual((self.into / omni_backup.CACHE_SNAPSHOT).read_bytes(), raw)

    def test_a_stale_snapshot_is_never_left_behind_for_an_absent_cache(self):
        self.cache.write_bytes(b"[]")
        omni_backup.snapshot_cache(self.cache, self.into)
        self.cache.unlink()
        omni_backup.snapshot_cache(self.cache, self.into)
        self.assertFalse((self.into / omni_backup.CACHE_SNAPSHOT).exists(),
                         "a rollback could resurrect a file that was not there")

    def test_rollback_restores_and_verifies_each_state(self):
        for label, raw in (("valid", b'[{"ip": "a"}]'), ("raw", b"\x00\x01 not json")):
            with self.subTest(label):
                self.cache.write_bytes(raw)
                info = omni_backup.snapshot_cache(self.cache, self.into)
                self.cache.write_bytes(b"[]")
                self.assertEqual(omni_backup.rollback_cache(self.into, self.cache, info),
                                 (True, "restored"))
                self.assertEqual(self.cache.read_bytes(), raw)
        self.cache.unlink()
        info = omni_backup.snapshot_cache(self.cache, self.into)
        self.cache.write_bytes(b"[]")
        self.assertEqual(omni_backup.rollback_cache(self.into, self.cache, info), (True, "removed"))
        self.assertFalse(self.cache.exists())
        self.assertEqual(omni_backup.rollback_cache(self.into, self.cache, info), (True, "unchanged"))

    def test_a_snapshot_copy_that_does_not_match_is_not_trusted(self):
        self.cache.write_bytes(b'[{"ip": "a"}]')
        info = omni_backup.snapshot_cache(self.cache, self.into)
        (self.into / omni_backup.CACHE_SNAPSHOT).write_bytes(b"[]")
        self.cache.write_bytes(b"[1]")
        verified, _detail = omni_backup.rollback_cache(self.into, self.cache, info)
        self.assertFalse(verified)
        self.assertEqual(self.cache.read_bytes(), b"[1]", "an untrusted copy was written over the file")
        (self.into / omni_backup.CACHE_SNAPSHOT).unlink()
        self.assertFalse(omni_backup.rollback_cache(self.into, self.cache, info)[0])

    def test_a_write_that_does_not_read_back_is_a_failure(self):
        self.cache.write_bytes(b'[{"ip": "a"}]')
        info = omni_backup.snapshot_cache(self.cache, self.into)
        self.cache.write_bytes(b"[1]")
        with mock.patch.object(omni_backup, "_atomic_copy",
                               lambda s, t: Path(t).write_bytes(b"[2]")):
            verified, detail = omni_backup.rollback_cache(self.into, self.cache, info)
        self.assertFalse(verified, detail)

    def test_garbage_info_is_a_failure_not_an_exception(self):
        for info in ({}, {"state": "bogus", "sha256": ""}, None):
            self.assertFalse(omni_backup.rollback_cache(self.into, self.cache, info)[0])


class SnapshotAndRollbackThroughTheEndpointTests(CacheRestoreBase):

    # 1 ------------------------------------------------------------------
    def test_a_successful_restore_snapshots_the_cache(self):
        response = self.restore()
        body = response.get_json()
        self.assertEqual(response.status_code, 200, body)
        info = self.snap_info()
        self.assertEqual(info["state"], "valid")
        self.assertEqual(info["sha256"], hashlib.sha256(self.original_bytes).hexdigest())
        self.assertEqual((self.snapshot_dir / omni_backup.CACHE_SNAPSHOT).read_bytes(),
                         self.original_bytes, "the snapshot is not the pre-restore file")
        self.assertTrue(body["devices"]["saved"])
        self.assertNotEqual(self.cache.read_bytes(), self.original_bytes,
                            "the restore did not merge the learned details")

    # 2 / 3 --------------------------------------------------------------
    def test_a_failed_restore_after_the_cache_changed_restores_the_original(self):
        with self.fail_after_changing_cache():
            response = self.restore()
        body = response.get_json()
        self.assertEqual(response.status_code, 500, body)
        self.assertEqual(body["status"], "FAILED — ROLLED BACK")
        self.assertEqual(self.cache.read_bytes(), self.original_bytes)
        self.assertEqual(body["cache"], {"state": "valid", "rollback": "restored", "verified": True})
        self.assertEqual(self.state(), self.before, "something other than the cache differs")

    def test_the_exact_previous_inventory_is_restored(self):
        with self.fail_after_changing_cache():
            self.restore()
        self.assertEqual(self.srv._load_cache(), ORIGINAL)
        self.assertEqual(json.loads(self.cache.read_text("utf-8")), ORIGINAL)

    def test_a_failure_that_changed_nothing_is_reported_as_unchanged(self):
        with self.failing_write(FAIL_ON):
            body = self.restore().get_json()
        self.assertEqual(body["cache"]["rollback"], "unchanged")
        self.assertTrue(body["cache"]["verified"])

    # 4 ------------------------------------------------------------------
    def test_an_originally_absent_cache_returns_to_absent(self):
        self.cache.unlink()
        self.before = self.state()
        with self.fail_after_changing_cache():
            body = self.restore().get_json()
        self.assertEqual(body["status"], "FAILED — ROLLED BACK")
        self.assertFalse(self.cache.exists(), "absent was converted into a file")
        self.assertEqual(body["cache"], {"state": "absent", "rollback": "removed", "verified": True})
        self.assertEqual(self.state(), self.before)

    # 5 ------------------------------------------------------------------
    RAW = b'[{"ip": "198.51.100.11", "mac": "b8:98:b0:00:00:11", "ho\xff\xfe'

    def test_a_malformed_pre_state_is_kept_raw_and_put_back_raw(self):
        self.cache.write_bytes(self.RAW)
        with self.fail_after_changing_cache(raw=b"[]"):
            body = self.restore().get_json()
        self.assertEqual(self.cache.read_bytes(), self.RAW)
        self.assertEqual(body["cache"], {"state": "raw", "rollback": "restored", "verified": True})
        self.assertEqual(self.snap_info()["state"], "raw")

    def test_a_successful_restore_of_a_malformed_cache_still_quarantines_it(self):
        self.cache.write_bytes(self.RAW)
        response = self.restore()
        self.assertEqual(response.status_code, 200, response.get_json())
        evidence = list(self.target.glob("units_cache.corrupt-*.json"))
        self.assertEqual(len(evidence), 1)
        self.assertEqual(evidence[0].read_bytes(), self.RAW)
        json.loads(self.cache.read_text("utf-8"))            # and what is there parses

    # 6 ------------------------------------------------------------------
    def test_a_save_that_reports_success_but_wrote_junk_is_detected_and_undone(self):
        def junk(units):
            self.cache.write_bytes(b'[{"ip": "198.5')
            return True
        with mock.patch.object(self.srv, "_save_cache", junk):
            response = self.restore()
        body = response.get_json()
        self.assertEqual(response.status_code, 200, body)
        self.assertFalse(body["devices"]["saved"])
        self.assertEqual(self.cache.read_bytes(), self.original_bytes)
        self.assertEqual(body["devices"]["cache"]["rollback"], "restored")

    def test_a_save_that_fails_after_writing_part_of_the_file_is_undone(self):
        def partial(units):
            self.cache.write_bytes(b"[]")
            return False
        with mock.patch.object(self.srv, "_save_cache", partial):
            body = self.restore().get_json()
        self.assertFalse(body["devices"]["saved"])
        self.assertEqual(self.cache.read_bytes(), self.original_bytes)

    def test_a_save_that_fails_cleanly_is_proven_unchanged(self):
        with mock.patch.object(self.srv, "_save_cache", lambda units: False):
            body = self.restore().get_json()
        self.assertFalse(body["devices"]["saved"])
        self.assertEqual(body["devices"]["cache"], {"state": "valid", "rollback": "unchanged",
                                                    "verified": True})

    def test_an_exception_after_the_files_are_written_puts_the_cache_back(self):
        def boom(payload, units):
            self.change_cache()
            raise RuntimeError("merge failed")
        with mock.patch.object(omni_backup, "merge_devices", boom):
            body = self.restore().get_json()
        self.assertEqual(body["status"], "RESTORED — RESTART REQUIRED")
        self.assertEqual(self.cache.read_bytes(), self.original_bytes)
        self.assertTrue(body["cache"]["verified"])

    # 7 ------------------------------------------------------------------
    def _break_cache_rollback(self):
        real = omni_backup._atomic_copy

        def copy_(source, target):
            if Path(target).name == "units_cache.json":
                raise OSError("the disk went away")
            return real(source, target)
        return mock.patch.object(omni_backup, "_atomic_copy", copy_)

    def test_a_rollback_write_failure_is_surfaced_not_ignored(self):
        with self._break_cache_rollback(), self.fail_after_changing_cache(), \
                self.assertLogs(self.srv.log, level="ERROR") as logs:
            response = self.restore()
        body = response.get_json()
        self.assertEqual(response.status_code, 500)
        self.assertEqual(body["status"], "FAILED — ROLLBACK INCOMPLETE")
        self.assertFalse(body["cache"]["verified"])
        self.assertTrue(any("units_cache.json" in f for f in body["rollback_failures"]), body)
        self.assertIn("units_cache.json", body["error"])
        self.assertEqual(body["recovery_snapshot"], str(self.snapshot_dir))
        self.assertTrue(self.srv._restart_required)
        self.assertIn("could NOT be put back", "\n".join(logs.output))
        self.assertTrue((self.snapshot_dir / omni_backup.CACHE_SNAPSHOT).exists(),
                        "the only complete copy was not preserved")

    def test_a_snapshot_that_cannot_be_taken_changes_nothing(self):
        with mock.patch.object(omni_backup, "snapshot_cache", side_effect=OSError("full")):
            response = self.restore()
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.get_json()["reason"], "no_snapshot")
        self.assertEqual(self.state(), self.before)

    # 12 -----------------------------------------------------------------
    def test_an_export_after_a_rolled_back_restore_is_coherent(self):
        with self.fail_after_changing_cache():
            self.restore()
        response = self.srv.app.test_client().post("/api/backup/export",
                                                   json={"passphrase": PASSPHRASE})
        self.assertEqual(response.status_code, 200)
        _, payload = omni_backup.unseal(response.data, PASSPHRASE)
        self.assertEqual(payload["devices"], omni_backup.collect_devices(ORIGINAL))

    def test_the_snapshot_files_are_not_part_of_a_backup(self):
        self.restore()
        blob = self.srv.app.test_client().post(
            "/api/backup/export", json={"passphrase": PASSPHRASE}).data
        _, payload = omni_backup.unseal(blob, PASSPHRASE)
        self.assertNotIn("units_cache.snapshot", json.dumps(payload))


class ConcurrencyTests(CacheRestoreBase):

    def _hold_in_apply(self, after_wait, writer_target):
        """Patch apply_payload so a writer starts while the restore is inside its transaction."""
        real = omni_backup.apply_payload
        box = {"writer_done": threading.Event(), "writer_finished_inside": None}

        def writer():
            writer_target()
            box["writer_done"].set()

        def apply(payload, data_dir, *, write_json):
            t = threading.Thread(target=writer, daemon=True)
            box["thread"] = t
            t.start()
            time.sleep(0.5)
            box["writer_finished_inside"] = box["writer_done"].is_set()
            after_wait()
            return real(payload, data_dir, write_json=write_json)
        return mock.patch.object(omni_backup, "apply_payload", apply), box

    # 9 ------------------------------------------------------------------
    def test_concurrent_activity_cannot_write_while_the_restore_owns_the_cache(self):
        def fail():
            self.change_cache()
            raise OSError("the disk went away")
        patched, box = self._hold_in_apply(
            fail, lambda: self.srv._save_cache(generation(7, 3)))
        with patched:
            body = bounded(self, self.restore).get_json()
        box["thread"].join(10)
        self.assertIs(box["writer_finished_inside"], False,
                      "a writer got into the cache between the snapshot and the rollback")
        self.assertEqual(body["cache"], {"state": "valid", "rollback": "restored", "verified": True})
        self.assertTrue(box["writer_done"].is_set(), "the writer was never let in afterwards")

    # 8 ------------------------------------------------------------------
    def test_a_racing_scan_cannot_tear_or_stale_the_pre_restore_snapshot(self):
        self.srv._save_cache(generation(0))
        stop, seen = threading.Event(), set()

        def scan():
            n = 1
            while not stop.is_set():
                self.srv._save_cache(generation(n))
                n += 1

        t = threading.Thread(target=scan, daemon=True)
        t.start()
        try:
            for _ in range(4):
                response = bounded(self, self.restore)
                self.assertIn(response.status_code, (200, 500), response.get_json())
                raw = (self.snapshot_dir / omni_backup.CACHE_SNAPSHOT).read_bytes()
                units = json.loads(raw.decode("utf-8"))      # torn would not parse
                self.assertEqual(len(units), 24)
                self.assertEqual(len({u["hostname"] for u in units}), 1, "mixed generations")
                self.assertEqual(hashlib.sha256(raw).hexdigest(), self.snap_info()["sha256"])
                seen.add(units[0]["hostname"])
        finally:
            stop.set()
            t.join(10)
        self.assertGreaterEqual(len(seen), 1)

    def test_the_snapshot_is_the_file_as_it_was_when_the_restore_began(self):
        """A writer that arrives during the restore is not in the snapshot."""
        patched, box = self._hold_in_apply(
            lambda: None, lambda: self.srv._save_cache(generation(9, 3)))
        with patched:
            bounded(self, self.restore)
        box["thread"].join(10)
        snap = json.loads((self.snapshot_dir / omni_backup.CACHE_SNAPSHOT).read_text("utf-8"))
        self.assertEqual(snap, ORIGINAL, "the writer's later save leaked into an earlier snapshot")


class LockOrderTests(CacheRestoreBase):

    # 10 (static) ---------------------------------------------------------
    def test_the_restore_takes_the_cache_lock_before_the_scan_lock(self):
        tree = ast.parse(SERVER_SOURCE.read_text(encoding="utf-8"))
        fn = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef) and n.name == "_restore_locked")
        withs = [n for n in ast.walk(fn) if isinstance(n, ast.With)]
        self.assertEqual(len(withs), 1)
        order = [i.context_expr.id for i in withs[0].items]
        self.assertEqual(order, ["_cache_io_lock", "_scan_results_lock"])

    def test_the_restore_is_entered_only_with_the_account_locks_already_held(self):
        """Account, name and group locks first, then the cache pair: the order Clear Units uses."""
        tree = ast.parse(SERVER_SOURCE.read_text(encoding="utf-8"))
        fn = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef) and n.name == "api_backup_restore")
        outer = [ast.unparse(i.context_expr) for w in ast.walk(fn)
                 if isinstance(w, ast.With) for i in w.items]
        self.assertEqual(outer, ["_user_store._lock", "_device_aliases._lock",
                                 "_display_groups._lock"])
        self.assertNotIn("_cache_io_lock", ast.unparse(fn))

    # 10 (dynamic) --------------------------------------------------------
    def test_every_restore_acquires_cache_then_scan_and_never_the_reverse(self):
        held = threading.local()
        events = []

        class Recording:
            def __init__(self, name):
                self.name, self._lock = name, threading.RLock()

            def _stack(self):
                if not hasattr(held, "stack"):
                    held.stack = []
                return held.stack

            def acquire(self, blocking=True, timeout=-1):
                stack = self._stack()
                events.append((self.name, tuple(stack)))
                got = self._lock.acquire(blocking, timeout)
                if got:
                    stack.append(self.name)
                return got

            def release(self):
                self._stack().remove(self.name)
                self._lock.release()

            def __enter__(self):
                self.acquire()
                return self

            def __exit__(self, *exc):
                self.release()

        cache_lock, scan_lock = Recording("cache"), Recording("scan")
        for target, name, value in ((self.srv, "_cache_io_lock", cache_lock),
                                    (logic, "_cache_write_lock", cache_lock),
                                    (self.srv, "_scan_results_lock", scan_lock),
                                    (logic, "_scan_results_lock", scan_lock)):
            p = mock.patch.object(target, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.restore()
        with self.fail_after_changing_cache():
            self.restore()
        self.assertTrue(any(name == "scan" for name, _ in events), "the scan lock was never taken")
        for name, already in events:
            if name == "scan":
                self.assertIn("cache", already, "scan lock acquired without the cache lock held")
            if name == "cache" and "cache" not in already:
                self.assertNotIn("scan", already, "cache lock acquired while holding the scan lock")

    # 11 -----------------------------------------------------------------
    def test_a_mixed_workload_with_snapshot_and_rollback_does_not_deadlock(self):
        """Restores (succeeding and failing with a rollback), snapshots, scans, polls and exports at once."""
        stop, errors, counts = threading.Event(), [], {}
        srv = self.srv
        outcomes = []
        own_snap = self.tmp / "own-snap"

        def loop(name, fn):
            def run():
                n = 0
                try:
                    while not stop.is_set():
                        fn(n)
                        n += 1
                except Exception as e:                       # pragma: no cover
                    errors.append((name, repr(e)))
                counts[name] = n
            return threading.Thread(target=run, name=name, daemon=True)

        def restore_ok(n):
            outcomes.append(self.restore().status_code)

        def restore_fail(n):
            with self.failing_write(FAIL_ON, before=(
                    FAIL_ON, self.change_cache if n % 2 else (lambda: None))):
                body = self.restore().get_json()
            if body and body.get("cache") and not body["cache"]["verified"]:
                errors.append(("rollback", "not verified"))

        def snapshot_and_rollback(n):
            with srv._cache_io_lock, srv._scan_results_lock:
                info = omni_backup.snapshot_cache(srv.CACHE, own_snap)
                omni_backup.rollback_cache(own_snap, srv.CACHE, info)

        def export(n):
            if n < 3:
                srv.app.test_client().post("/api/backup/export", json={"passphrase": PASSPHRASE})
            else:
                time.sleep(0.05)

        def logic_save(n):
            logic._encoders["198.51.100.1"] = {"ip": "198.51.100.1", "role": "encoder"}
            logic._save_cache()

        workers = [
            loop("restore_ok", restore_ok),
            loop("restore_fail", restore_fail),
            loop("snap_rollback", snapshot_and_rollback),
            loop("scan_pair", lambda n: (srv._save_cache(generation(n, 6)),
                                         srv._save_scan_results({"devices": generation(n, 6)}))),
            loop("load", lambda n: srv._load_cache()),
            loop("logic_load", lambda n: logic._load_cache()),
            loop("logic_save", logic_save),
            loop("export", export),
        ]
        with mock.patch("builtins.print"):
            for w in workers:
                w.start()
            time.sleep(4)
            stop.set()
            deadline = time.monotonic() + 40
            for w in workers:
                w.join(max(0.1, deadline - time.monotonic()))
        stuck = [w.name for w in workers if w.is_alive()]
        self.assertEqual(stuck, [], "deadlock: these threads never finished: %s" % stuck)
        self.assertEqual(errors, [])
        self.assertTrue(all(counts.get(w.name, 0) > 0 for w in workers), counts)
        self.assertTrue(outcomes and set(outcomes) <= {200, 409, 500}, outcomes)


class QuarantineStillWorksTests(CacheRestoreBase):
    # 13 -----------------------------------------------------------------
    def test_the_loader_still_quarantines_and_degrades_to_empty(self):
        self.cache.write_bytes(b'[{"ip": "198.')
        self.scan.unlink(missing_ok=True)          # the fixture's scan results would otherwise answer
        self.assertEqual(self.srv._load_cache(), [])
        self.assertFalse(self.cache.exists())
        self.assertEqual(len(list(self.target.glob("units_cache.corrupt-*.json"))), 1)

    def test_snapshotting_a_malformed_cache_does_not_quarantine_or_alter_it(self):
        self.cache.write_bytes(b"not json")
        omni_backup.snapshot_cache(self.cache, self.snapshot_dir)
        self.assertEqual(self.cache.read_bytes(), b"not json")
        self.assertEqual(list(self.target.glob("units_cache.corrupt-*.json")), [])


if __name__ == "__main__":
    unittest.main()
