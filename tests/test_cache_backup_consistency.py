"""Backup, restore and the cache files: one synchronisation strategy.

units_cache.json and scan_results.json are read and replaced under two locks,
always taken in the order cache then scan results, by the server, the matrix
logic module, the backup export and the restore. These tests hold that to
account from the outside:

  * a backup taken while writers replace both files is always parseable and
    internally consistent (never a mixture of two generations);
  * a restore's read-merge-write of the device list is one critical section, so a
    save that races it is not silently overwritten;
  * a malformed units_cache.json is quarantined and degrades to empty, in both
    modules, and a backup of such an installation still succeeds;
  * replacement is atomic: an interrupted write leaves the old file whole and no
    temporary file behind;
  * the lock order is fixed (checked statically over the server source, and by a
    mixed workload under a watchdog that would expose a deadlock).

Nothing here touches a device or leaves loopback. Fixtures are synthetic and are
deep-copied before server code sees them.
"""
from __future__ import annotations

import ast
import copy
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("OMNI_SMOKE", "1")

import omni_backup                                        # noqa: E402
import omni_matrix_logic as logic                         # noqa: E402
import OmniMatrix_upgrade_server_v7_6y as srv             # noqa: E402
from tests.test_backup import PASSPHRASE                  # noqa: E402
from tests.test_restore_atomicity import Base as RestoreBase  # noqa: E402

SERVER_SOURCE = ROOT / "OmniMatrix_upgrade_server_v7_6y.py"


def generation(n, count=24):
    """A device list in which every unit carries the same generation tag."""
    return [{"ip": "198.51.100.%d" % (i + 1),
             "mac": "02:00:5e:00:00:%02x" % (i + 1),
             "hostname": "gen-%d" % n, "model": "hw-omni-e4111",
             "role": "encoder" if i % 2 else "decoder",
             "password": "synthetic", "pad": "x" * 300} for i in range(count)]


def private_locks(case):
    """Private cache and scan-results locks for one test, installed in every module that holds them.

    The same identity relationship as production (one object per file, shared by
    both modules), but a deadlock this suite provokes -- or a mutation provokes --
    strands only this test's threads and never the process-wide locks that every
    later test needs."""
    cache_lock, scan_lock = threading.RLock(), threading.RLock()
    for target, name, value in ((srv, "_cache_io_lock", cache_lock),
                                (logic, "_cache_write_lock", cache_lock),
                                (srv, "_scan_results_lock", scan_lock),
                                (logic, "_scan_results_lock", scan_lock)):
        patch = mock.patch.object(target, name, value)
        patch.start()
        case.addCleanup(patch.stop)


def bounded(case, fn, seconds=40):
    """Run fn on a thread and fail the test, rather than hang the run, if it does not finish."""
    box = {}

    def run():
        try:
            box["value"] = fn()
        except BaseException as e:                       # pragma: no cover
            box["error"] = e

    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(seconds)
    if t.is_alive():
        case.fail("did not finish within %d s: a deadlock between the cache and scan-results locks" % seconds)
    if "error" in box:
        raise box["error"]
    return box.get("value")


class CacheDir(unittest.TestCase):
    """A throwaway data directory the server and the logic module both use."""

    def setUp(self):
        private_locks(self)
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)
        self.here = Path(self.tmp.name)
        self.cache = self.here / "units_cache.json"
        self.scan = self.here / "scan_results.json"
        for p in (mock.patch.object(srv, "CACHE", self.cache),
                  mock.patch.object(srv, "SCAN_RESULTS", self.scan),
                  mock.patch.object(logic, "_cache_file", self.cache)):
            p.start()
            self.addCleanup(p.stop)
        enc, dec = dict(logic._encoders), dict(logic._decoders)
        self.addCleanup(lambda: (logic._encoders.clear(), logic._encoders.update(enc),
                                 logic._decoders.clear(), logic._decoders.update(dec)))

    def write_pair(self, n):
        """What a scan does: both files, under both locks, in the fixed order."""
        units = generation(n)
        with srv._cache_io_lock, srv._scan_results_lock:
            self.assertTrue(srv._save_cache(copy.deepcopy(units)))
            self.assertTrue(srv._save_scan_results({"devices": copy.deepcopy(units)}))


class BackupSnapshotIsConsistentTests(CacheDir):
    """A backup under concurrent writes is never torn."""

    def test_every_export_is_parseable_and_one_generation(self):
        self.write_pair(0)
        stop = threading.Event()
        errors = []

        def writer():
            n = 1
            try:
                while not stop.is_set():
                    self.write_pair(n)
                    n += 1
            except Exception as e:                       # pragma: no cover
                errors.append(e)

        t = threading.Thread(target=writer, daemon=True)
        t.start()
        client = srv.app.test_client()
        seen = set()
        try:
            for _ in range(10):
                response = bounded(self, lambda: client.post(
                    "/api/backup/export", json={"passphrase": PASSPHRASE}))
                self.assertEqual(response.status_code, 200, response.data[:200])
                header, payload = omni_backup.unseal(response.data, PASSPHRASE)
                devices = payload["devices"]
                self.assertEqual(len(devices), 24, "a backup lost units")
                tags = {d.get("hostname") for d in devices}
                self.assertEqual(len(tags), 1, "a backup mixed generations: %s" % tags)
                seen |= tags
        finally:
            stop.set()
            t.join(10)
        self.assertEqual(errors, [])
        self.assertGreater(len(seen), 1, "the writer never ran during the exports; "
                                         "this test did not measure a race")

    def test_the_loader_holds_both_locks_for_the_whole_read(self):
        """A writer that needs either lock cannot slip between the two reads."""
        self.write_pair(0)
        entered, release = threading.Event(), threading.Event()
        real = srv._read_scan_results

        def slow(*a, **k):
            entered.set()
            release.wait(5)
            return real(*a, **k)

        got = {}
        blocked = []

        def intruder():
            srv._save_cache(generation(9))
            blocked.append("done")

        with mock.patch.object(srv, "_read_scan_results", slow):
            t = threading.Thread(target=lambda: got.update(units=srv._load_cache()),
                                 daemon=True)
            t.start()
            self.assertTrue(entered.wait(5))
            w = threading.Thread(target=intruder, daemon=True)
            w.start()
            time.sleep(0.3)
            self.assertEqual(blocked, [], "a cache writer entered the middle of a load")
            release.set()
            t.join(10)
            w.join(10)
        self.assertEqual(len({u["hostname"] for u in got["units"]}), 1)

    def test_a_scan_results_writer_waits_for_a_load_in_progress(self):
        """The load holds the scan lock across the whole merge, not per read."""
        self.write_pair(0)
        entered, release = threading.Event(), threading.Event()
        real = srv._read_scan_results
        blocked = []

        def slow(*a, **k):
            entered.set()
            release.wait(5)
            return real(*a, **k)

        def intruder():
            srv._save_scan_results({"devices": generation(9, 3)})
            blocked.append("done")

        with mock.patch.object(srv, "_read_scan_results", slow):
            t = threading.Thread(target=srv._load_cache, daemon=True)
            t.start()
            self.assertTrue(entered.wait(5))
            # The read itself is under the scan lock; what this checks is that
            # the lock is not released and retaken inside the load, so no
            # writer interleaves between the cache read and the scan read.
            w = threading.Thread(target=intruder, daemon=True)
            w.start()
            time.sleep(0.3)
            self.assertEqual(blocked, [])
            release.set()
            t.join(10)
            w.join(10)
        self.assertEqual(blocked, ["done"])


class RestoreDeviceMergeIsOneCriticalSectionTests(RestoreBase):

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
        self.cache.write_text(json.dumps(copy.deepcopy([
            {"ip": "198.51.100.11", "mac": "b8:98:b0:00:00:11", "hostname": ""},
            {"ip": "198.51.100.21", "mac": "b8:98:b0:00:00:01", "hostname": ""}])),
            encoding="utf-8")
        self.before = self.state()

    def test_a_save_that_races_the_merge_is_not_overwritten(self):
        marker = {"ip": "198.51.100.77", "mac": "b8:98:b0:00:00:77", "hostname": "late"}
        real_merge = omni_backup.merge_devices
        started = {}

        def racing_writer():
            # A poll or a scan: read-modify-write of the device list.
            with self.srv._cache_io_lock:
                units = self.srv._load_cache() or []
                units.append(dict(marker))
                self.srv._save_cache(units)

        def merge(payload, units):
            t = threading.Thread(target=racing_writer, daemon=True)
            started["thread"] = t
            t.start()
            t.join(0.6)          # completes here, between the load and the save
            return real_merge(payload, units)

        with mock.patch.object(omni_backup, "merge_devices", merge):
            response = self.restore()
        started["thread"].join(10)
        body = response.get_json()
        self.assertEqual(response.status_code, 200, body)
        self.assertTrue(body["devices"].get("saved", True))
        final = json.loads(self.cache.read_text(encoding="utf-8"))
        ips = {u.get("ip") for u in final}
        self.assertIn("198.51.100.77", ips,
                      "the restore overwrote a save that landed during its merge")
        restored = {u["mac"]: u for u in final if u.get("mac")}
        self.assertEqual(restored["b8:98:b0:00:00:11"].get("hostname"), "dec-front",
                         "the restore did not merge the learned details")

    def test_a_failed_device_save_is_reported_not_hidden(self):
        with mock.patch.object(self.srv, "_save_cache", lambda units: False):
            response = self.restore()
        body = response.get_json()
        self.assertEqual(response.status_code, 200, body)
        self.assertFalse(body["devices"]["saved"])

    def test_the_restore_still_puts_back_every_file_on_failure(self):
        """The cache work must not disturb the existing rollback guarantee."""
        import omni_users
        real = omni_users._atomic_write_json

        def write(path, payload):
            if Path(path).name == "usb_associations.json":
                return False
            return real(path, payload)

        with mock.patch.object(omni_users, "_atomic_write_json", write):
            response = self.restore()
        self.assertEqual(response.get_json()["status"], "FAILED — ROLLED BACK")
        self.assertEqual(self.state(), self.before)


class MalformedCacheDegradesToEmptyTests(CacheDir):

    BAD = {
        "truncated": b'[{"ip": "198.51.100.1", "mac": "02:00:5e:00:00:01", "ho',
        "empty": b"",
        "binary": b"\xff\xfe\x00\x01\x02garbage\x80",
        "trailing": json.dumps(generation(1, 3)).encode() + b'{"x": 1',
    }

    def _evidence(self):
        return list(self.here.glob("units_cache.corrupt-*.json"))

    def test_the_server_loader_quarantines_and_returns_empty(self):
        for name, raw in self.BAD.items():
            with self.subTest(name):
                for old in self._evidence():
                    old.unlink()
                self.cache.write_bytes(raw)
                self.assertEqual(srv._load_cache(), [])
                self.assertFalse(self.cache.exists(), "the bad file was left in place")
                self.assertEqual(len(self._evidence()), 1,
                                 "the bad copy was not kept as evidence")
                # And the next save starts clean.
                self.assertTrue(srv._save_cache(generation(2, 2)))
                self.assertEqual(len(srv._load_cache()), 2)
                self.cache.unlink()

    def test_the_scan_results_are_used_when_the_cache_is_quarantined(self):
        self.cache.write_bytes(self.BAD["truncated"])
        srv._save_scan_results({"devices": generation(3, 4)})
        self.assertEqual(len(srv._load_cache()), 4)

    def test_the_logic_loader_quarantines_too(self):
        for name, raw in self.BAD.items():
            with self.subTest(name):
                for old in self._evidence():
                    old.unlink()
                self.cache.write_bytes(raw)
                with mock.patch("builtins.print"):
                    logic._load_cache()
                self.assertFalse(self.cache.exists())
                self.assertEqual(len(self._evidence()), 1)

    def test_a_malformed_scan_results_file_is_quarantined(self):
        self.scan.write_bytes(self.BAD["truncated"])
        self.assertEqual(srv._read_scan_results(), {})
        self.assertEqual(len(list(self.here.glob("scan_results.corrupt-*.json"))), 1)

    def test_wrong_shape_is_not_a_crash(self):
        for raw in (b'"a string"', b"123", b"null", b'{"units": "x"}'):
            self.cache.write_bytes(raw)
            self.assertEqual(srv._load_cache(), [])

    def test_a_backup_of_an_installation_with_a_malformed_cache_still_succeeds(self):
        self.cache.write_bytes(self.BAD["truncated"])
        srv._save_scan_results({"devices": generation(4, 5)})
        response = srv.app.test_client().post("/api/backup/export",
                                              json={"passphrase": PASSPHRASE})
        self.assertEqual(response.status_code, 200)
        _, payload = omni_backup.unseal(response.data, PASSPHRASE)
        self.assertEqual(len(payload["devices"]), 5)


class AtomicReplacementTests(CacheDir):

    def test_an_interrupted_write_leaves_the_old_file_whole_and_no_temp(self):
        self.write_pair(1)
        before = self.cache.read_bytes()

        def dying(obj, fp, *a, **k):
            fp.write('[{"ip": "198.5')       # half a document, then the process "dies"
            raise OSError("interrupted")

        with mock.patch.object(srv.json, "dump", dying):
            self.assertFalse(srv._save_cache(generation(2)))
        self.assertEqual(self.cache.read_bytes(), before)
        self.assertEqual(list(self.here.glob("*.tmp")), [])

    def test_a_reader_never_sees_a_partial_document(self):
        self.write_pair(0)
        stop = threading.Event()
        problems = []

        def writer():
            n = 1
            while not stop.is_set():
                srv._save_cache(generation(n))
                n += 1

        t = threading.Thread(target=writer, daemon=True)
        t.start()
        reads = 0
        deadline = time.monotonic() + 1.5
        try:
            while time.monotonic() < deadline:
                units = srv._load_cache()
                reads += 1
                if len(units) != 24 or len({u["hostname"] for u in units}) != 1:
                    problems.append(len(units))
        finally:
            stop.set()
            t.join(10)
        self.assertGreater(reads, 20)
        self.assertEqual(problems, [])


class EveryRemainingPathUsesTheSharedLockTests(CacheDir):
    """The last two paths found outside the shared lock by reading every user of the files."""

    def _waits(self, lock, action):
        done, errors = threading.Event(), []

        def run():
            try:
                action()
            except Exception as e:                       # pragma: no cover
                errors.append(e)
            finally:
                done.set()

        with lock:
            t = threading.Thread(target=run, daemon=True)
            t.start()
            waited = not done.wait(0.4)
        t.join(20)
        self.assertTrue(done.is_set())
        self.assertEqual(errors, [])
        return waited

    def test_the_poll_status_fallback_read_waits_for_the_cache_lock(self):
        self.cache.write_text(json.dumps(generation(1, 3)), encoding="utf-8")
        with mock.patch("sys.stderr"):
            self.assertTrue(self._waits(
                srv._cache_io_lock, lambda: logic.poll_unit_status("198.51.100.250")),
                "poll_unit_status opened units_cache.json without the shared lock")

    def test_clear_removes_scan_results_under_the_two_locks(self):
        self.write_pair(1)
        with mock.patch.object(srv, "remote_access_enabled", lambda: False):
            for lock in (srv._cache_io_lock, srv._scan_results_lock):
                self.write_pair(1)
                self.assertTrue(self._waits(lock, lambda: srv.app.test_client().post(
                    "/api/clear", json={})),
                    "/api/clear deleted scan_results.json without the shared locks")


class LockOrderTests(CacheDir):

    def test_one_lock_object_per_file_shared_by_every_module(self):
        self.assertIs(srv._cache_io_lock, logic._cache_write_lock)
        self.assertIs(srv._scan_results_lock, logic._scan_results_lock)

    def test_no_server_code_takes_the_scan_lock_before_the_cache_lock(self):
        """Static: the order is cache, then scan results, everywhere."""
        tree = ast.parse(SERVER_SOURCE.read_text(encoding="utf-8"))
        takes_cache = {"_load_cache", "_save_cache", "_apply_snapshot_to_cache",
                       "_bump_inventory_generation"}

        def names_in(node):
            return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}

        def calls_in(node):
            return {n.func.id for n in ast.walk(node)
                    if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}

        offenders = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.With):
                continue
            order = []
            for item in node.items:
                hit = names_in(item.context_expr) & {"_cache_io_lock", "_scan_results_lock"}
                if hit:
                    order.append(next(iter(hit)))
            if "_scan_results_lock" in order and "_cache_io_lock" in order:
                if order.index("_scan_results_lock") < order.index("_cache_io_lock"):
                    offenders.append((node.lineno, "scan before cache in one with"))
            if order and order[0] == "_scan_results_lock" and "_cache_io_lock" not in order:
                body = ast.Module(body=node.body, type_ignores=[])
                if "_cache_io_lock" in names_in(body):
                    offenders.append((node.lineno, "cache lock taken inside a scan-lock block"))
                if takes_cache & calls_in(body):
                    offenders.append((node.lineno, "a cache-locking call inside a scan-lock block"))
        self.assertEqual(offenders, [])

    def test_a_mixed_workload_does_not_deadlock(self):
        """Every operation that touches the files, at once, under a watchdog.

        The locks are private to the test (see private_locks), so a deadlock
        here strands these threads and nothing else."""
        self.write_pair(0)
        stop = threading.Event()
        errors = []
        counts = {}

        def loop(name, fn):
            def run():
                n = 0
                try:
                    while not stop.is_set():
                        fn(n)
                        n += 1
                except Exception as e:                   # pragma: no cover
                    errors.append((name, repr(e)))
                counts[name] = n
            return threading.Thread(target=run, name=name, daemon=True)

        def apply_snap(n):
            snap = generation(n)
            base = copy.deepcopy(snap)
            snap[0]["hostname"] = "learned-%d" % n
            srv._apply_snapshot_to_cache(snap, base, srv._inventory_generation_now(),
                                         label="TEST")

        def restore_merge(n):
            with srv._cache_io_lock, srv._scan_results_lock:
                units, _, _ = omni_backup.merge_devices(
                    {"devices": [{"mac": "02:00:5e:00:00:01", "hostname": "x"}]},
                    srv._load_cache() or [])
                srv._save_cache(units)

        def logic_save(n):
            logic._encoders["198.51.100.1"] = {"ip": "198.51.100.1", "role": "encoder"}
            logic._save_cache()

        def export(n):
            if n < 3:
                srv.app.test_client().post("/api/backup/export",
                                           json={"passphrase": PASSPHRASE})
            else:
                time.sleep(0.05)

        workers = [
            loop("pair", lambda n: self.write_pair(n)),
            loop("load", lambda n: srv._load_cache()),
            loop("scan_only", lambda n: srv._save_scan_results({"devices": generation(n, 6)})),
            loop("snapshot", apply_snap),
            loop("restore_merge", restore_merge),
            loop("logic_load", lambda n: logic._load_cache()),
            loop("logic_save", logic_save),
            loop("export", export),
        ]
        with mock.patch("builtins.print"):
            for w in workers:
                w.start()
            time.sleep(2.5)
            stop.set()
            deadline = time.monotonic() + 25
            for w in workers:
                w.join(max(0.1, deadline - time.monotonic()))
        stuck = [w.name for w in workers if w.is_alive()]
        self.assertEqual(stuck, [], "deadlock: these threads never finished: %s" % stuck)
        self.assertEqual(errors, [])
        self.assertTrue(all(counts.get(w.name, 0) > 0 for w in workers), counts)


if __name__ == "__main__":
    unittest.main()
