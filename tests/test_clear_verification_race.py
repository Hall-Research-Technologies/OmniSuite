"""A cache verification sweep that began before a clear cannot hand back what was cleared.

Found by packaged acceptance: Clear Units returned 200 and emptied the inventory,
and about three seconds later ``units_cache.json`` reappeared holding every unit
that had just been cleared, which the next scan then merged back in.

The writer was established by tracing every write to the cache file (see
``TracedWritesTests``): the only writer running on another thread at that moment
is the verification sweep that ``GET /api/cache`` starts. It reads the whole cache
when it begins, asks each unit a question that takes seconds, and then wrote the
whole snapshot back. Anything removed in between -- Clear Units or Remove
Selected -- came back with it, and so did any change a scan or poll made.

The guard orders that write against the clears it races: the sweep remembers
which life of the inventory it belongs to, and under the same two locks it writes
under it either discards (the inventory was cleared) or applies only the fields
it learned onto the units that still exist.

Nothing here reaches a device: the transport is replaced and the hardware fence
stays installed. The sweep is held mid-flight by an event and the test waits for
the work itself, never for a timer.
"""
from __future__ import annotations

import copy
import json
import sys
import threading
import traceback
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import omni_users                                    # noqa: E402
from tests.test_clear_inventory import (             # noqa: E402
    ALL, CAMERA, FRONT, InventoryBase, OTHER_DISPLAY, OTHER_SOURCE)
from tests.test_display_groups import ROOM_A, ROOM_B, ROOM_C, SRC, UNITS  # noqa: E402

KEPT = sorted([SRC, CAMERA, FRONT, ROOM_A, ROOM_B])
CLEARED = sorted(set(ALL) - set(KEPT))
LEARNED_VERSION = "9.9.9-sweep"


def _norm(unit):
    return omni_users._normalise_identity(unit.get("mac"))


class SweepBase(InventoryBase):
    """A verification sweep that can be held in the middle of its device reads."""

    def setUp(self):
        super().setUp()
        srv = self.srv
        self.entered = threading.Event()
        self.release = threading.Event()
        self.writes = []                 # every write to the cache file

        def device(url, payload, timeout=0, *args, **kwargs):
            self.entered.set()
            self.release.wait(10)
            ip = str(url).split("//")[1].split(":")[0].split("/")[0]
            return {"config": {"hostname": "learned-" + ip.replace(".", "-"),
                               "firmwareversion": LEARNED_VERSION}, "error": None}

        real_save = srv._save_cache

        def traced_save(units):
            self.writes.append({
                "thread": threading.current_thread().name,
                "generation": srv._inventory_generation_now(),
                "units": len(units),
                "stack": [f.name for f in traceback.extract_stack()[:-1]][-6:]})
            return real_save(units)

        saved = (srv._cache_verification_in_progress, srv._cache_last_verified,
                 srv._cache_startup_verified)

        def restore():
            (srv._cache_verification_in_progress, srv._cache_last_verified,
             srv._cache_startup_verified) = saved

        for patcher in (mock.patch.object(srv, "_ws_send_recv", side_effect=device),
                        mock.patch.object(srv, "_save_cache", side_effect=traced_save)):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(restore)
        srv._cache_verification_in_progress = False
        srv._cache_last_verified = 0
        srv._cache_startup_verified = True        # skip the synchronous first-request pass

        def finish_the_work():
            # Registered last, so it runs first: the sweep belongs to this test
            # and is finished while this test's stubs are still installed.
            self.release.set()
            srv.await_startup_tasks(timeout=15.0)
        self.addCleanup(finish_the_work)

    def start_sweep(self):
        reply = self.get("/api/cache", self.session_for(self.admin))
        self.assertEqual(reply.status_code, 200, reply.get_data(as_text=True))
        self.assertTrue(self.entered.wait(5),
                        "precondition: GET /api/cache did not start a sweep, so there is "
                        "nothing in flight for the clear to race")
        return reply

    def finish_sweep(self):
        self.release.set()
        self.assertTrue(self.srv.await_startup_tasks(timeout=15.0),
                        "the sweep was still running after await_startup_tasks()")

    def file_units(self):
        path = self.srv.CACHE
        if not path.exists():
            return []
        return sorted(_norm(u) for u in json.loads(path.read_text(encoding="utf-8")))

    def served(self):
        reply = self.get("/api/cache", self.session_for(self.admin))
        self.assertEqual(reply.status_code, 200)
        body = reply.get_json()
        units = body if isinstance(body, list) else (body.get("units") or [])
        return sorted(_norm(u) for u in units)

    def sweep_writes(self):
        return [w for w in self.writes if w["thread"] != "MainThread"
                and "do_verify" in w["stack"]]


class TracedWritesTests(SweepBase):
    """The evidence: which code writes the cache after a clear, and from which thread."""

    def test_the_only_write_after_the_clear_is_the_sweep_on_its_own_thread(self):
        self.set_remote(False)
        self.start_sweep()
        reply = self.clear()
        self.assertEqual(reply.status_code, 200)
        self.assertEqual(self.cached(), [], "precondition: the clear emptied the inventory")
        after_clear = len(self.writes)
        self.finish_sweep()
        late = self.writes[after_clear:]
        # Whatever the sweep did, it must not have written a cache after the clear.
        self.assertEqual(late, [],
                         "writes after the clear: " + json.dumps(late, indent=1))


class ClearDuringSweepTests(SweepBase):
    def test_a_full_clear_during_a_sweep_stays_cleared(self):
        self.set_remote(False)
        self.start_sweep()
        self.assertEqual(self.clear().status_code, 200)
        self.assertEqual(self.cached(), [])
        self.finish_sweep()
        self.assertEqual(self.cached(), [], "the sweep handed the cleared units back")
        self.assertFalse(self.srv.CACHE.exists(),
                         "units_cache.json reappeared after Clear Units")
        self.assertEqual(self.served(), [], "cleared units are served again")

    def test_a_partial_clear_keeps_what_was_kept_and_nothing_more(self):
        self.start_sweep()
        self.assertEqual(self.clear().status_code, 200)
        self.assertEqual(self.cached(), KEPT)
        self.finish_sweep()
        self.assertEqual(self.cached(), KEPT)
        self.assertEqual(set(self.file_units()) & set(CLEARED), set())

    def test_the_discard_is_logged_once_and_not_as_an_error(self):
        self.set_remote(False)
        self.start_sweep()
        self.clear()
        with self.assertLogs(self.srv.log, level="DEBUG") as logs:
            self.finish_sweep()
        said = [r for r in logs.records if "VERIFY" in r.getMessage()
                and "cleared" in r.getMessage()]
        self.assertEqual(len(said), 1, [r.getMessage() for r in logs.records])
        self.assertLess(said[0].levelno, 30, "a discarded stale sweep is not a warning")

    def test_a_clear_then_rescan_is_not_overwritten_by_the_old_sweep(self):
        """A newer discovery wins: the old sweep's result describes a world that is gone."""
        self.set_remote(False)
        self.start_sweep()
        self.clear()
        newer = copy.deepcopy(UNITS[:2])
        for unit in newer:
            unit["hostname"] = "found-after-the-clear"
        self.srv._save_cache(newer)
        self.finish_sweep()
        rows = self.srv._load_cache()
        self.assertEqual(sorted(_norm(u) for u in rows), sorted(_norm(u) for u in newer))
        for row in rows:
            self.assertEqual(row["hostname"], "found-after-the-clear")
            self.assertNotEqual(row.get("firmwareversion"), LEARNED_VERSION,
                                "the old sweep's reading overwrote a newer discovery")


class RemoveSelectedDuringSweepTests(SweepBase):
    def test_only_the_removed_units_stay_removed_and_the_others_stay(self):
        self.set_remote(False)
        victims = [u["ip"] for u in UNITS if _norm(u) in (OTHER_SOURCE, OTHER_DISPLAY)]
        self.assertEqual(len(victims), 2, "precondition: both fixtures are in the inventory")
        self.start_sweep()
        reply = self.admin_post("/api/remove_units", {"ips": victims})
        self.assertEqual(reply.status_code, 200, reply.get_data(as_text=True))
        self.assertEqual(reply.get_json()["removed"], 2)
        self.finish_sweep()
        remaining = sorted(set(ALL) - {OTHER_SOURCE, OTHER_DISPLAY})
        self.assertEqual(self.cached(), remaining)
        self.assertEqual(self.file_units(), remaining,
                         "a removed unit was written back to the cache file")
        self.assertEqual(self.served(), remaining)

    def test_the_sweep_still_records_what_it_learned_about_the_units_that_remain(self):
        self.set_remote(False)
        victim = next(u["ip"] for u in UNITS if _norm(u) == OTHER_SOURCE)
        self.start_sweep()
        self.admin_post("/api/remove_units", {"ips": [victim]})
        self.finish_sweep()
        rows = self.srv._load_cache()
        self.assertTrue(rows)
        self.assertEqual({r.get("firmwareversion") for r in rows}, {LEARNED_VERSION},
                         "removing one unit made the sweep discard everything it learned")


class OrdinarySweepTests(SweepBase):
    """No clear happened: verification does what it always did."""

    def test_a_sweep_with_nothing_cleared_updates_every_unit(self):
        self.start_sweep()
        self.finish_sweep()
        rows = self.srv._load_cache()
        self.assertEqual(sorted(_norm(u) for u in rows), ALL)
        for row in rows:
            self.assertEqual(row.get("firmwareversion"), LEARNED_VERSION)
            self.assertEqual(row.get("version"), LEARNED_VERSION)
            self.assertTrue(row["hostname"].startswith("learned-"))
        self.assertEqual(self.file_units(), ALL)
        self.assertEqual(len(self.sweep_writes()), 1)

    def test_nested_details_the_sweep_learns_are_kept(self):
        units = copy.deepcopy(UNITS)
        for unit in units:
            unit["details"] = {"systeminfo": {"config": {"hostname": "old", "firmwareversion": "0"}}}
        self.srv._save_cache(units)
        self.writes.clear()
        self.start_sweep()
        self.finish_sweep()
        for row in self.srv._load_cache():
            cfg = row["details"]["systeminfo"]["config"]
            self.assertEqual(cfg["firmwareversion"], LEARNED_VERSION)
            self.assertTrue(cfg["hostname"].startswith("learned-"))

    def test_a_field_changed_elsewhere_during_the_sweep_is_not_reverted_by_it(self):
        """A refresh must not erase additive metadata learned outside it."""
        self.start_sweep()
        rows = self.srv._load_cache()
        for row in rows:
            row["notes_from_elsewhere"] = "kept"
            row["model"] = "model-changed-elsewhere"
        self.srv._save_cache(rows)
        self.finish_sweep()
        for row in self.srv._load_cache():
            self.assertEqual(row.get("notes_from_elsewhere"), "kept")
            self.assertEqual(row.get("model"), "model-changed-elsewhere",
                             "the sweep reverted a field another writer changed")
            self.assertEqual(row.get("firmwareversion"), LEARNED_VERSION)


class SnapshotApplyTests(SweepBase):
    """The shared write: every background holder of a snapshot goes through it."""

    def test_a_stale_generation_writes_nothing(self):
        before = copy.deepcopy(self.srv._load_cache())
        snapshot = copy.deepcopy(before)
        baseline = copy.deepcopy(before)
        for row in snapshot:
            row["firmwareversion"] = "stale"
        generation = self.srv._inventory_generation_now()
        self.srv._bump_inventory_generation()
        self.writes.clear()
        self.assertFalse(self.srv._apply_snapshot_to_cache(snapshot, baseline, generation))
        self.assertEqual(self.writes, [])

    def test_a_current_generation_applies_only_changed_fields_to_existing_units(self):
        before = copy.deepcopy(self.srv._load_cache())
        baseline = copy.deepcopy(before)
        snapshot = copy.deepcopy(before)
        snapshot[0]["firmwareversion"] = "new"
        generation = self.srv._inventory_generation_now()
        self.assertTrue(self.srv._apply_snapshot_to_cache(snapshot, baseline, generation))
        rows = {_norm(u): u for u in self.srv._load_cache()}
        self.assertEqual(rows[_norm(snapshot[0])]["firmwareversion"], "new")

    def test_nothing_changed_means_no_write(self):
        baseline = copy.deepcopy(self.srv._load_cache())
        self.writes.clear()
        self.assertFalse(self.srv._apply_snapshot_to_cache(
            copy.deepcopy(baseline), baseline, self.srv._inventory_generation_now()))
        self.assertEqual(self.writes, [])


class StartupPassTests(SweepBase):
    """GET /api/cache's first-request pass also holds a snapshot across device reads."""

    def test_a_clear_during_the_first_request_pass_stays_cleared(self):
        self.set_remote(False)
        self.srv._cache_startup_verified = False
        started = threading.Event()
        outcome = {}

        def request():
            started.set()
            outcome["reply"] = self.get("/api/cache", self.session_for(self.admin))

        thread = threading.Thread(target=request, name="first-request", daemon=True)
        thread.start()
        self.assertTrue(started.wait(5))
        self.assertTrue(self.entered.wait(5),
                        "precondition: the first-request pass never reached a device read")
        self.assertEqual(self.clear().status_code, 200)
        self.release.set()
        thread.join(15)
        self.assertFalse(thread.is_alive())
        self.srv.await_startup_tasks(timeout=15.0)
        self.assertEqual(self.cached(), [])
        self.assertFalse(self.srv.CACHE.exists())


class WriterContractTests(unittest.TestCase):
    """Structure: a background snapshot is never written back whole."""

    def test_the_sweep_does_not_call_save_cache_with_its_snapshot(self):
        source = (ROOT / "OmniMatrix_upgrade_server_v7_6y.py").read_text(encoding="utf-8")
        start = source.index("def _verify_cache_in_background")
        end = source.index("# ---------------- basic routes", start)
        body = "\n".join(line.split("#")[0] for line in source[start:end].split("\n"))
        self.assertNotIn("_save_cache(cached_units)", body)
        self.assertIn("_apply_snapshot_to_cache(", body)
        self.assertIn("_inventory_generation_now()", body)

    def test_the_apply_helper_decides_under_the_two_locks_in_the_scan_order(self):
        source = (ROOT / "OmniMatrix_upgrade_server_v7_6y.py").read_text(encoding="utf-8")
        start = source.index("def _apply_snapshot_to_cache")
        end = source.index("\ndef ", start + 10)
        body = "\n".join(line.split("#")[0] for line in source[start:end].split("\n"))
        lock = body.index("with _cache_io_lock, _scan_results_lock:")
        self.assertLess(lock, body.index("_inventory_generation_now() != generation"))
        self.assertLess(body.index("_inventory_generation_now() != generation"),
                        body.index("_save_cache("))


if __name__ == "__main__":
    unittest.main()
