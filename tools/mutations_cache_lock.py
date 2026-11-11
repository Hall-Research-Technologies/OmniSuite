"""Mutations for the shared cache lock: units_cache.json and scan_results.json are
read and replaced under one lock across the server and the matrix logic module.

Each entry is (id, file, anchor, replacement, harm, suites). The anchor must occur
exactly once. Imported by mutate_security.py, so
`python tools/mutate_security.py cachelock.` runs exactly these.
"""
LOCKS = ["tests.test_cache_file_sharing", "tests.test_clear_protection"]
SERVER = "OmniMatrix_upgrade_server_v7_6y.py"
LOGIC = "omni_matrix_logic.py"

CACHE_LOCK_MUTATIONS = [
    ("cachelock.logic-loader-takes-no-lock", LOGIC,
     "    with _cache_write_lock, _scan_results_lock:\n        _load_cache_locked()\n",
     "    _load_cache_locked()\n",
     "the loader would open units_cache.json while it is being replaced: [Errno 13] on Windows",
     LOCKS),
    ("cachelock.logic-loader-skips-the-scan-lock", LOGIC,
     "    with _cache_write_lock, _scan_results_lock:\n        _load_cache_locked()\n",
     "    with _cache_write_lock:\n        _load_cache_locked()\n",
     "the loader's scan_results merge would race the scan_results replace",
     LOCKS),
    ("cachelock.logic-saver-takes-no-lock", LOGIC,
     "    with _cache_write_lock:\n        _save_cache_locked()\n",
     "    _save_cache_locked()\n",
     "the logic saver would replace the file under a reader",
     LOCKS),
    ("cachelock.server-binds-a-private-cache-lock", SERVER,
     '_cache_io_lock = (getattr(omni_matrix_logic, "_cache_write_lock", None)\n                  or threading.RLock())\n',
     "_cache_io_lock = threading.RLock()\n",
     "the two modules would be back on two locks for one file",
     LOCKS),
    ("cachelock.server-binds-a-private-scan-lock", SERVER,
     '_scan_results_lock = (getattr(omni_matrix_logic, "_scan_results_lock", None)\n                      or threading.RLock())\n',
     "_scan_results_lock = threading.RLock()\n",
     "the scan_results merge in the logic module would be unsynchronised with the server's writer",
     LOCKS),
    ("cachelock.server-saver-takes-no-lock", SERVER,
     "        with _cache_io_lock:\n            tmp_path = _atomic_tmp_path(CACHE)\n",
     "        if True:\n            tmp_path = _atomic_tmp_path(CACHE)\n",
     "the server's replace would race every reader",
     LOCKS),
    ("cachelock.server-saver-uses-a-shared-temp-name", SERVER,
     "            tmp_path = _atomic_tmp_path(CACHE)\n",
     '            tmp_path = CACHE.with_suffix(CACHE.suffix + ".tmp")\n',
     "two writers outside the lock would truncate one temp file",
     LOCKS),
    ("cachelock.server-saver-skips-fsync", SERVER,
     "                f.flush()\n                os.fsync(f.fileno())\n            os.replace(tmp_path, CACHE)\n",
     "                f.flush()\n            os.replace(tmp_path, CACHE)\n",
     "a crash could publish an empty or partial cache",
     LOCKS),
    ("cachelock.server-saver-hides-the-failure", SERVER,
     '        log.warning("save cache failed: %s", e)\n',
     "        pass\n",
     "a failed save would leave no trace",
     LOCKS),
    ("cachelock.server-saver-reports-success-on-failure", SERVER,
     "                tmp_path.unlink(missing_ok=True)\n        except Exception:\n            pass\n        return False\n",
     "                tmp_path.unlink(missing_ok=True)\n        except Exception:\n            pass\n        return True\n",
     "callers could not tell a failed save from a good one",
     LOCKS),
    ("cachelock.server-saver-leaves-its-temp-file", SERVER,
     "            if tmp_path is not None:\n                tmp_path.unlink(missing_ok=True)\n",
     "            pass\n",
     "a failed save would litter the data folder",
     LOCKS),
]
