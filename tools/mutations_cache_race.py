"""Mutations for the cache verification race: a sweep that began before a clear
cannot hand back what was cleared.

Each entry is (id, file, anchor, replacement, harm, suites). The anchor must occur
exactly once; the harness reports one that does not, and does not count it caught.
Imported by mutate_security.py and appended to MUTATIONS, so
`python tools/mutate_security.py cacherace.` runs exactly these.
"""
RACE = ["tests.test_clear_verification_race"]
SERVER = "OmniMatrix_upgrade_server_v7_6y.py"

CACHE_RACE_MUTATIONS = [
    ("cacherace.ignores-the-generation", SERVER,
     "        if _inventory_generation_now() != generation:\n",
     "        if False:\n",
     "a sweep that began before Clear Units would write its stale result and bring the cleared units back",
     RACE),
    ("cacherace.check-outside-the-locks", SERVER,
     "    with _cache_io_lock, _scan_results_lock:\n        if _inventory_generation_now() != generation:",
     "    if True:\n        if _inventory_generation_now() != generation:",
     "the generation check and the write would no longer be ordered against a clear",
     RACE),
    ("cacherace.sweep-writes-its-whole-snapshot", SERVER,
     "                    _apply_snapshot_to_cache(cached_units, baseline_units,\n                                             sweep_generation, \"VERIFY\")",
     "                    _save_cache(cached_units)",
     "the verification sweep would overwrite a clear, a Remove Selected and every concurrent update",
     RACE),
    ("cacherace.sweep-generation-never-matches", SERVER,
     "    sweep_generation = _inventory_generation_now()\n",
     "    sweep_generation = -1\n",
     "an ordinary verification would be discarded and no unit would ever learn its firmware",
     RACE),
    ("cacherace.first-request-writes-its-snapshot", SERVER,
     "            _apply_snapshot_to_cache(units, baseline_units, request_generation, \"API/CACHE\")",
     "            _save_cache(units)",
     "the first-request verification would bring cleared units back",
     RACE),
    ("cacherace.applies-unchanged-fields", SERVER,
     "            if name in base and base[name] == value:\n                continue\n",
     "            if False:\n                continue\n",
     "a sweep would revert a field another writer changed while it was running",
     RACE),
    ("cacherace.matches-by-position-not-identity", SERVER,
     "            current = by_key.get(key)\n            if current is not None:",
     "            current = current_units[0] if current_units else None\n            if current is not None:",
     "a sweep would write one device's readings onto another",
     RACE),
]
