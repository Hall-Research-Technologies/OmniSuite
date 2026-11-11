"""units_cache.json and scan_results.json: one lock across both modules.

Found by a long soak: 225 failed cache reads ([Errno 13] Permission
denied) and 3,007 failed cache saves ([WinError 5] Access is denied on the
os.replace). The server's reader and writer held one lock; the matrix logic
module's loader opened the same file holding none, and had its own separate
lock for its writes. On Windows a file that is open cannot be replaced, and a
file that is being replaced cannot be opened, so each side failed the other.

The deterministic tests hold the lock from one module and require the other
module's access to wait. The stress test drives both modules at once and
requires that nothing fails.

Nothing here contacts a device. Fixtures are synthetic.
"""
from __future__ import annotations

import copy
import json
import logging
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

import omni_matrix_logic as logic                     # noqa: E402
import OmniMatrix_upgrade_server_v7_6y as srv         # noqa: E402

UNITS = [{"ip": "198.51.100.%d" % n, "mac": "02:00:5e:00:%02x:%02x" % (n // 256, n % 256),
          "hostname": "synthetic-%d" % n,
          "role": "encoder" if n % 2 else "decoder",
          "type": "Encoder" if n % 2 else "Decoder", "pad": "x" * 400}
         for n in range(1, 40)]


class CacheFixture(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.folder.cleanup)
        self.here = Path(self.folder.name)
        self.cache = self.here / "units_cache.json"
        self.scan = self.here / "scan_results.json"
        patches = [
            mock.patch.object(srv, "CACHE", self.cache),
            mock.patch.object(srv, "SCAN_RESULTS", self.scan),
            mock.patch.object(logic, "_cache_file", self.cache),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        # logic's tables are process-global; put them back as they were.
        enc, dec = dict(logic._encoders), dict(logic._decoders)
        self.addCleanup(lambda: (logic._encoders.clear(), logic._encoders.update(enc),
                                 logic._decoders.clear(), logic._decoders.update(dec)))
        self.cache.write_text(json.dumps(copy.deepcopy(UNITS)), encoding="utf-8")
        self.scan.write_text(json.dumps({"devices": copy.deepcopy(UNITS)}), encoding="utf-8")


class OneLockAcrossBothModulesTests(CacheFixture):
    def test_the_cache_lock_is_one_object(self):
        self.assertIs(srv._cache_io_lock, logic._cache_write_lock)

    def test_the_scan_results_lock_is_one_object(self):
        self.assertIs(srv._scan_results_lock, getattr(logic, "_scan_results_lock", None))

    def _blocked_while_held(self, lock, action):
        """Hold `lock` here, run `action` on a thread, and report whether the
        action waited for it. The thread is always joined before returning."""
        done = threading.Event()
        errors = []

        def run():
            try:
                action()
            except Exception as exc:                  # pragma: no cover
                errors.append(exc)
            finally:
                done.set()

        with lock:
            t = threading.Thread(target=run, daemon=True)
            t.start()
            waited = not done.wait(0.4)
        t.join(10)
        self.assertTrue(done.is_set(), "the action never finished after release")
        self.assertEqual(errors, [])
        return waited

    def test_the_logic_loader_waits_for_the_server_cache_lock(self):
        self.assertTrue(self._blocked_while_held(srv._cache_io_lock, logic._load_cache),
                        "the logic loader opened units_cache.json without the shared lock")

    def test_the_logic_loader_waits_for_the_server_scan_results_lock(self):
        self.assertTrue(self._blocked_while_held(srv._scan_results_lock, logic._load_cache),
                        "the logic loader read scan_results.json without its lock")

    def test_the_logic_saver_waits_for_the_server_cache_lock(self):
        logic._encoders["198.51.100.1"] = dict(UNITS[0])
        self.assertTrue(self._blocked_while_held(srv._cache_io_lock, logic._save_cache),
                        "the logic saver replaced the file without the shared lock")

    def test_the_server_saver_waits_for_the_logic_cache_lock(self):
        self.assertTrue(self._blocked_while_held(
            logic._cache_write_lock, lambda: srv._save_cache(copy.deepcopy(UNITS))))

    def test_the_server_loader_waits_for_the_logic_cache_lock(self):
        self.assertTrue(self._blocked_while_held(logic._cache_write_lock, srv._load_cache))

    def test_the_scan_results_writer_waits_for_the_logic_scan_lock(self):
        self.assertTrue(self._blocked_while_held(
            getattr(logic, "_scan_results_lock", threading.RLock()),
            lambda: srv._save_scan_results({"devices": copy.deepcopy(UNITS)})))


class _Records(logging.Handler):
    def __init__(self):
        super().__init__(logging.DEBUG)
        self.messages = []

    def emit(self, record):
        self.messages.append(record.getMessage())


class ConcurrentAccessTests(CacheFixture):
    def test_readers_and_writers_of_both_modules_never_fail(self):
        records = _Records()
        logger = logging.getLogger("omni_upgrade")
        logger.addHandler(records)
        self.addCleanup(logger.removeHandler, records)
        printed = []
        escaped = []
        stop = time.time() + 2.5

        def loop(fn):
            while time.time() < stop:
                try:
                    fn()
                except Exception as exc:
                    escaped.append(repr(exc))

        workers = ([logic._load_cache] * 3 + [srv._load_cache] * 2
                   + [lambda: srv._save_cache(copy.deepcopy(UNITS))] * 2
                   + [logic._save_cache]
                   + [lambda: srv._save_scan_results({"devices": copy.deepcopy(UNITS)})])
        with mock.patch("builtins.print", lambda *a, **k: printed.append(" ".join(map(str, a)))):
            threads = [threading.Thread(target=loop, args=(w,)) for w in workers]
            for t in threads:
                t.start()
            for t in threads:
                t.join(30)
        failures = [m for m in records.messages + printed
                    if "failed" in m.lower() or "merge skipped" in m]
        self.assertEqual(escaped, [])
        self.assertEqual(failures[:3], [], "%d cache access failures; first shown" % len(failures))


class SaveFailureIsReportedTests(CacheFixture):
    def test_a_failed_replace_is_logged_returned_and_leaves_no_temp_file(self):
        before = self.cache.read_bytes()
        with self.assertLogs("omni_upgrade", level="WARNING") as captured, \
                mock.patch.object(os, "replace", side_effect=PermissionError(13, "Access is denied")):
            ok = srv._save_cache([{"ip": "198.51.100.200"}])
        self.assertIs(ok, False)
        self.assertTrue(any("save cache failed" in m for m in captured.output))
        self.assertEqual(self.cache.read_bytes(), before, "the old file must be untouched")
        self.assertEqual([p.name for p in self.here.iterdir() if p.name.endswith(".tmp")], [])

    def test_a_successful_save_returns_true_and_uses_a_unique_temp_name(self):
        seen = []
        real = os.replace

        def spy(src, dst):
            seen.append(Path(src).name)
            return real(src, dst)

        with mock.patch.object(os, "replace", spy):
            self.assertIs(srv._save_cache([{"ip": "198.51.100.201"}]), True)
        self.assertEqual(len(seen), 1)
        self.assertNotEqual(seen[0], "units_cache.json.tmp", "a shared temp name lets writers collide")
        self.assertIn(str(os.getpid()), seen[0])
        self.assertEqual(json.loads(self.cache.read_text(encoding="utf-8")), [{"ip": "198.51.100.201"}])

    def test_the_replacement_is_fsynced_before_it_is_published(self):
        order = []
        real_fsync, real_replace = os.fsync, os.replace
        with mock.patch.object(os, "fsync", lambda fd: (order.append("fsync"), real_fsync(fd))[1]),                 mock.patch.object(os, "replace", lambda a, b: (order.append("replace"), real_replace(a, b))[1]):
            self.assertIs(srv._save_cache([{"ip": "198.51.100.202"}]), True)
        self.assertEqual(order, ["fsync", "replace"])


if __name__ == "__main__":
    unittest.main()
