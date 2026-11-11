"""Mutations for the restore's units_cache.json snapshot and rollback (this release).

Each entry is (id, file, anchor, replacement, harm, suites). The anchor must occur
exactly once. Imported by mutate_security.py, so
`python tools/mutate_security.py restorecache.` runs exactly these.

Not mutated, deliberately: the fsync calls and the unique temp-file names inside
snapshot_cache. Their absence changes durability across a power cut and the
collision behaviour of two concurrent restores, neither of which a unit test can
observe; the same two properties of _atomic_copy are the project's existing rule
and are held by the atomicity suite that covers that function.
"""
SUITE = ["tests.test_restore_cache_snapshot"]
ORDER_TESTS = ["tests.test_restore_cache_snapshot.LockOrderTests.test_the_restore_takes_the_cache_lock_before_the_scan_lock",
               "tests.test_restore_cache_snapshot.LockOrderTests.test_every_restore_acquires_cache_then_scan_and_never_the_reverse"]
SERVER = "OmniMatrix_upgrade_server_v7_6y.py"
BACKUP = "omni_backup.py"

RESTORE_CACHE_MUTATIONS = [
    # -- the capture ------------------------------------------------------
    ("restorecache.no-cache-snapshot-is-taken", SERVER,
     "        cache_snapshot = omni_backup.snapshot_cache(CACHE, BACKUP_SNAPSHOT_DIR)\n",
     '        cache_snapshot = {"state": "valid", "sha256": "", "size": 0}\n',
     "a failed restore would have no copy of the inventory to come back to",
     SUITE),
    ("restorecache.snapshot-calls-everything-valid", BACKUP,
     "            state = CACHE_VALID\n        except (ValueError, UnicodeDecodeError):\n            state = CACHE_RAW\n",
     "            state = CACHE_VALID\n        except (ValueError, UnicodeDecodeError):\n            state = CACHE_VALID\n",
     "a malformed file would be reported as a good inventory",
     SUITE),
    ("restorecache.snapshot-leaves-the-previous-restores-copy", BACKUP,
     "    for stale in (CACHE_SNAPSHOT, CACHE_MANIFEST):\n        try:\n",
     "    for stale in ():\n        try:\n",
     "a rollback could resurrect a file that was not there when this restore began",
     SUITE),
    # -- the locks --------------------------------------------------------
    ("restorecache.restore-holds-no-cache-locks", SERVER,
     "    with _cache_io_lock, _scan_results_lock:\n        return _restore_locked_cache_held(payload, summary, data_dir)\n",
     "    if True:\n        return _restore_locked_cache_held(payload, summary, data_dir)\n",
     "a scan could write between the snapshot and a rollback, or be overwritten by it",
     SUITE),
    ("restorecache.restore-holds-only-the-cache-lock", SERVER,
     "    with _cache_io_lock, _scan_results_lock:\n        return _restore_locked_cache_held(payload, summary, data_dir)\n",
     "    with _cache_io_lock:\n        return _restore_locked_cache_held(payload, summary, data_dir)\n",
     "the snapshot and the loader's scan_results merge could be torn by a scan",
     SUITE),
    ("restorecache.restore-takes-the-locks-in-the-wrong-order", SERVER,
     "    with _cache_io_lock, _scan_results_lock:\n        return _restore_locked_cache_held(payload, summary, data_dir)\n",
     "    with _scan_results_lock, _cache_io_lock:\n        return _restore_locked_cache_held(payload, summary, data_dir)\n",
     "the inverse lock order is the deadlock the fixed order exists to prevent",
     # Only the order tests: a genuine inversion deadlocks the threads of the concurrency tests and
     # the harness would wait out its whole timeout (and crashes on it) instead of reporting a catch.
     ORDER_TESTS),
    # -- the rollback -----------------------------------------------------
    ("restorecache.failed-apply-does-not-roll-the-cache-back", SERVER,
     '        cache_report = _restore_cache_rollback(\n            cache_snapshot, "the restore failed partway")\n',
     '        cache_report = {"state": "valid", "rollback": "restored", "verified": True}\n',
     "a failed restore would leave the cache changed and say it had been put back",
     SUITE),
    ("restorecache.unverified-rollback-is-not-a-failure", SERVER,
     '        if not cache_report["verified"]:\n            failures.append(',
     '        if False:\n            failures.append(',
     "a cache that could not be put back would be reported as a clean rollback",
     SUITE),
    ("restorecache.reply-omits-the-cache-outcome", SERVER,
     '            "rolled_back": restored, "rollback_failures": failures,\n            "cache": cache_report}\n',
     '            "rolled_back": restored, "rollback_failures": failures}\n',
     "the operator is not told what became of the device inventory",
     SUITE),
    ("restorecache.helper-always-claims-verified", SERVER,
     '    return {"state": info.get("state"), "rollback": detail, "verified": verified}\n',
     '    return {"state": info.get("state"), "rollback": detail, "verified": True}\n',
     "a rollback that failed would be reported as proven",
     SUITE),
    ("restorecache.rollback-failure-is-not-logged-as-an-error", SERVER,
     '        log.error("[BACKUP] units_cache.json could NOT be put back to its "\n',
     '        log.debug("[BACKUP] units_cache.json could NOT be put back to its "\n',
     "a failed rollback would leave nothing in the log at a level anyone reads",
     SUITE),
    # -- the device-list step ----------------------------------------------
    ("restorecache.a-save-that-does-not-read-back-is-accepted", SERVER,
     "            if devices_saved is not False and not _restore_cache_reads_back(units):\n",
     "            if False:\n",
     "a save that reported success but left junk would be accepted as the inventory",
     SUITE),
    ("restorecache.reads-back-check-always-passes", SERVER,
     '        return json.loads(raw.decode("utf-8")) == json.loads(json.dumps(units))\n',
     "        return True\n",
     "the read-back would verify nothing",
     SUITE),
    ("restorecache.failed-save-does-not-prove-the-file", SERVER,
     '                cache_report = _restore_cache_rollback(\n                    cache_snapshot, "the device list could not be saved")\n',
     "                cache_report = None\n",
     "a save that failed after writing part of the file would leave that part",
     SUITE),
    ("restorecache.exception-in-the-device-step-leaves-the-cache", SERVER,
     "        if devices_saved is None:\n",
     "        if False:\n",
     "a failure after the cache changed would leave it changed",
     SUITE),
    # -- the rollback itself -----------------------------------------------
    ("restorecache.absent-is-not-removed", BACKUP,
     '            Path(cache_path).unlink()\n            if _read_cache_bytes(cache_path) is None:\n',
     '            if _read_cache_bytes(cache_path) is None:\n',
     "a file that did not exist would be left behind by a rollback",
     SUITE),
    ("restorecache.an-untrusted-copy-is-written", BACKUP,
     "        if saved_bytes is None or _digest(saved_bytes) != wanted:\n",
     "        if saved_bytes is None:\n",
     "a corrupted snapshot copy would be written over the file",
     SUITE),
    ("restorecache.unchanged-shortcut-ignores-the-content", BACKUP,
     "        if current is not None and _digest(current) == wanted:\n",
     "        if current is not None:\n",
     "a changed cache would be called unchanged and left",
     SUITE),
    ("restorecache.the-write-is-not-read-back", BACKUP,
     "        if after is None or _digest(after) != wanted:\n",
     "        if False:\n",
     "a rollback that wrote something else would be reported as proven",
     SUITE),
]
