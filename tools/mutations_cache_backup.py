"""Mutations for the cache lock usage added in this release: the loader's two-lock read, the
restore's single critical section for the device list, quarantine of a malformed
units_cache.json, and the fixed lock order.

Each entry is (id, file, anchor, replacement, harm, suites). The anchor must occur
exactly once. Imported by mutate_security.py, so
`python tools/mutate_security.py cachebackup.` runs exactly these.
"""
SUITE = ["tests.test_cache_backup_consistency"]
SERVER = "OmniMatrix_upgrade_server_v7_6y.py"

CACHE_BACKUP_MUTATIONS = [
    ("cachebackup.restore-merge-takes-no-lock", SERVER,
     "        with _cache_io_lock:\n            units, devices_matched, devices_unmatched = omni_backup.merge_devices(\n",
     "        if True:\n            units, devices_matched, devices_unmatched = omni_backup.merge_devices(\n",
     "a restore would overwrite a save that landed between its load and its save",
     SUITE),
    ("cachebackup.restore-hides-a-failed-device-save", SERVER,
     '"saved": devices_saved is not False}',
     '"saved": True}',
     "a restore would report the device list written when it was not",
     SUITE),
    ("cachebackup.loader-takes-only-the-cache-lock", SERVER,
     "        with _cache_io_lock, _scan_results_lock:\n            cache_units = []\n",
     "        with _cache_io_lock:\n            cache_units = []\n",
     "a backup could read a cache and a scan_results from different generations",
     SUITE),
    ("cachebackup.loader-locks-in-the-wrong-order", SERVER,
     "        with _cache_io_lock, _scan_results_lock:\n            cache_units = []\n",
     "        with _scan_results_lock, _cache_io_lock:\n            cache_units = []\n",
     "the inverse lock order is the deadlock the fixed order exists to prevent",
     SUITE),
    ("cachebackup.generation-bump-locks-in-the-wrong-order", SERVER,
     "    with _cache_io_lock, _scan_results_lock:\n        _inventory_generation += 1\n",
     "    with _scan_results_lock, _cache_io_lock:\n        _inventory_generation += 1\n",
     "the inverse lock order is the deadlock the fixed order exists to prevent",
     SUITE),
    ("cachebackup.malformed-cache-is-not-quarantined", SERVER,
     "                    _quarantine_cache_file(e)\n                    d = None\n",
     "                    d = None\n",
     "a malformed units_cache.json would be re-read and fail on every call",
     SUITE),
    ("cachebackup.quarantine-destroys-the-evidence", SERVER,
     "        os.replace(CACHE, target)\n",
     "        CACHE.unlink()\n",
     "the only evidence of how the file got that way would be thrown away",
     SUITE),
    ("cachebackup.loader-accepts-a-wrong-shape", SERVER,
     'isinstance(d.get("units"), list) and d["units"]',
     '"units" in d and d["units"]',
     "a units value that is not a list would be returned as the device list",
     SUITE),
    ("cachebackup.poll-status-reads-the-cache-unlocked", "omni_matrix_logic.py",
     "                with _cache_write_lock:\n                    with open(_cache_file, \"r\", encoding=\"utf-8\") as f:\n                        cached_units = json.load(f)\n",
     "                if True:\n                    with open(_cache_file, \"r\", encoding=\"utf-8\") as f:\n                        cached_units = json.load(f)\n",
     "the status poll would open units_cache.json while it is being replaced",
     SUITE),
    ("cachebackup.clear-deletes-scan-results-unlocked", SERVER,
     "        with _cache_io_lock, _scan_results_lock:\n            if SCAN_RESULTS.exists():\n                SCAN_RESULTS.unlink()\n",
     "        if True:\n            if SCAN_RESULTS.exists():\n                SCAN_RESULTS.unlink()\n",
     "Clear would delete scan_results.json under a reader",
     SUITE),
]
