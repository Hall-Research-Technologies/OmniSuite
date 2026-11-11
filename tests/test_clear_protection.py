"""Clear Units clears the discovered inventory.

With Remote Access OFF it is a full clear: the discovered units go, and so does
every assignment, group membership and friendly name that pointed at them, so
the controls a restricted User sees are never built from equipment the
application no longer knows. A later scan makes the equipment available for NEW
assignment; nothing is silently re-granted.

With Remote Access ON it is assignment aware and keeps what a User or a group
refers to. That behaviour is tested in test_clear_inventory; this file holds the
full-clear behaviour, so every test here that clears says Remote Access is off.

The cascade across accounts, groups and names is exercised end to end in
test_acceptance_lifecycle. This file holds the inventory-level behaviour: the
files, the USB inventory, the failure reporting, and the cache writers.

Nothing here contacts hardware.
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

import omni_access                                   # noqa: E402
import omni_remote                                   # noqa: E402
import omni_users                                    # noqa: E402
from omni_users import Permissions                   # noqa: E402

ASSIGNED_SOURCE = "b8:98:b0:00:00:01"
ASSIGNED_DISPLAY = "b8:98:b0:00:00:11"
SPARE_SOURCE = "b8:98:b0:00:00:02"
SPARE_DISPLAY = "b8:98:b0:00:00:12"
MULTIVIEW_DISPLAY = "b8:98:b0:00:00:19"

UNITS = [
    {"ip": "192.0.2.11", "mac": ASSIGNED_SOURCE, "hostname": "enc-assigned",
     "model": "hw-omni-e4521", "role": "encoder", "type": "Encoder"},
    {"ip": "192.0.2.12", "mac": SPARE_SOURCE, "hostname": "enc-spare",
     "model": "hw-omni-e4111", "role": "encoder", "type": "Encoder"},
    {"ip": "192.0.2.21", "mac": ASSIGNED_DISPLAY, "hostname": "dec-assigned",
     "model": "hw-omni-d4511", "role": "decoder", "type": "Decoder"},
    {"ip": "192.0.2.22", "mac": SPARE_DISPLAY, "hostname": "dec-spare",
     "model": "hw-omni-d4511", "role": "decoder", "type": "Decoder"},
]


class ClearTestBase(unittest.TestCase):
    def setUp(self):
        import OmniMatrix_upgrade_server_v7_6y as srv
        self.srv = srv
        self.folder = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.folder.cleanup)
        here = Path(self.folder.name)

        for name in ("CACHE", "SCAN_RESULTS", "CSV_VIEW", "USB_ASSOCIATIONS",
                     "SCAN_PREFS", "TOPOLOGY_ACK", "UI_PREFS", "NOTICE_ACK"):
            original = getattr(srv, name)
            self.addCleanup(lambda n=name, o=original: setattr(srv, n, o))
            setattr(srv, name, here / original.name)

        # The standalone extender store binds its file at import.
        previous_file = srv._usb_extenders.state_file
        previous_devices = dict(srv._usb_extenders._devices)
        self.addCleanup(lambda: (
            setattr(srv._usb_extenders, "state_file", previous_file),
            srv._usb_extenders._devices.clear(),
            srv._usb_extenders._devices.update(previous_devices)))
        srv._usb_extenders.state_file = here / "usb_extenders.json"
        srv._usb_extenders._devices.clear()

        self.store = srv._user_store
        saved = (self.store._path, self.store._audit_path,
                 dict(self.store._accounts), dict(self.store._sessions),
                 list(self.store._audit))

        def restore():
            self.store._path, self.store._audit_path = saved[0], saved[1]
            self.store._accounts.clear()
            self.store._accounts.update(saved[2])
            self.store._sessions.clear()
            self.store._sessions.update(saved[3])
            self.store._audit[:] = saved[4]

        self.addCleanup(restore)
        self.store._path = here / "users.json"
        self.store._audit_path = here / "audit.json"
        self.store._accounts.clear()
        self.store._sessions.clear()
        self.store._audit.clear()

        listener = srv._remote_listener
        previous = listener._server
        self.addCleanup(lambda: setattr(listener, "_server", previous))
        listener._server = None                      # Remote Access off by default

        srv._save_cache([dict(u) for u in UNITS])
        self.client = srv.app.test_client()

    # -- helpers -------------------------------------------------------
    def remote_access(self, on):
        self.srv._remote_listener._server = object() if on else None

    def with_a_user(self, **permissions):
        defaults = {"features": ("av",), "sources": [ASSIGNED_SOURCE],
                    "destinations": [ASSIGNED_DISPLAY]}
        defaults.update(permissions)
        self.store.create("principal", "principal-pass-1", omni_users.ROLE_ADMIN)
        self.store.create("teacher", "teacher-password-1", omni_users.ROLE_USER,
                          Permissions(**defaults))

    def clear(self, **body):
        return self.client.post("/api/clear_units", json=body)

    def cached(self):
        return sorted(omni_users._normalise_identity(u.get("mac"))
                      for u in (self.srv._load_cache() or []))


class ClearRemovesTheInventoryTests(ClearTestBase):
    def test_everything_is_cleared_when_remote_access_is_off(self):
        self.remote_access(False)
        self.with_a_user()
        response = self.clear()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.cached(), [])
        self.assertEqual(response.get_json()["mode"], "full")

    def test_everything_is_cleared_when_remote_access_is_on_and_nothing_is_assigned(self):
        self.remote_access(True)
        response = self.clear()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.cached(), [])

    def test_assignments_do_not_keep_a_device_when_remote_access_is_off(self):
        self.remote_access(False)
        self.with_a_user()
        body = self.clear().get_json()
        self.assertEqual(self.cached(), [])
        self.assertEqual(body["cleared"], len(UNITS))
        self.assertEqual(body["accounts_updated"], 1)
        self.assertNotIn("retained", body)

    def test_the_accounts_themselves_survive(self):
        self.remote_access(False)
        self.with_a_user()
        self.clear()
        teacher = self.store.find_by_username("teacher")
        self.assertIsNotNone(teacher)
        self.assertEqual(set(teacher.permissions.sources), set())
        self.assertEqual(set(teacher.permissions.features), {"av"})

    def test_the_usb_inventory_is_still_forgotten(self):
        import omni_usb_extender
        called = []
        original = self.srv._usb_extenders.clear_devices
        self.srv._usb_extenders.clear_devices = (
            lambda keep=None: called.append(1) or 0)
        self.addCleanup(lambda: setattr(self.srv._usb_extenders, "clear_devices",
                                        original))
        self.clear()
        self.assertTrue(called)

    def test_an_unreadable_account_store_refuses_rather_than_clears(self):
        self.remote_access(True)
        self.with_a_user()

        def explode(identities=None):
            raise RuntimeError("the account store is unreadable")

        original = self.store.forget_devices
        self.store.forget_devices = explode
        self.addCleanup(lambda: setattr(self.store, "forget_devices", original))
        response = self.clear()
        self.assertEqual(response.status_code, 503)
        self.assertNotEqual(self.cached(), [],
                            "the inventory was cleared although the accounts "
                            "could not be updated")

    def test_the_preview_workflow_is_gone(self):
        self.assertEqual(self.client.get("/api/clear_units/preview").status_code, 404)
        source = (ROOT / "OmniMatrix_upgrade_server_v7_6y.py").read_text(
            encoding="utf-8")
        self.assertNotIn("_protected_assignments", source)
        self.assertNotIn('data.get("override")', source)


class AFailedRemovalIsReportedTests(ClearTestBase):
    """A file that could not be removed brings devices back on the next read.

    These used to share one `except Exception: pass`, so a locked file took
    the rest of the clear down with it and said nothing -- and `scan_results`
    is merged back in by the very next read, which means devices the operator
    was told had been cleared reappear immediately.
    """

    def test_a_file_that_cannot_be_removed_is_named(self):
        # The path object is replaced, never `Path.unlink` on the class: a
        # method patched on the class outlives this test whatever the restore
        # does with a bound one, and the blast radius is every test that runs
        # afterwards. setUp already restores this attribute.
        self.srv.CACHE = _RefusesToBeRemoved(self.srv.CACHE)
        body = self.clear().get_json()
        self.assertIn("units_cache.json", body["unremoved"])

    def test_one_failure_does_not_stop_the_others(self):
        """They are removed separately, so a lock on one is not a whole outage."""
        source = (ROOT / "OmniMatrix_upgrade_server_v7_6y.py").read_text(
            encoding="utf-8")
        start = source.index("def api_clear_units_dup")
        body = source[start:source.index("\n@app.route", start)]
        code = "\n".join(l.split("#")[0] for l in body.split("\n"))
        self.assertIn("for path in (CACHE, SCAN_RESULTS, CSV_VIEW)", code,
                      "the files are not removed independently")
        self.assertNotIn("except Exception: pass", code,
                         "a failed removal is swallowed again")

    def test_an_ordinary_clear_reports_nothing_unremoved(self):
        body = self.clear().get_json()
        self.assertEqual(body["unremoved"], [])


class _RefusesToBeRemoved:
    """A path that exists and cannot be unlinked, standing in for a locked file."""

    def __init__(self, real):
        self._real = real
        self.name = real.name

    def exists(self):
        return True

    def unlink(self, *_a, **_k):
        raise OSError(13, "Permission denied")

    def __fspath__(self):
        return str(self._real)

    def __str__(self):
        return str(self._real)



class TheUnitCacheSurvivesTwoWritersTests(unittest.TestCase):
    """Found on the bench: units_cache.json was a complete 16-unit document
    followed by 98 bytes of a longer previous one.

    The cause was two writers -- this module's and the server's -- sharing one
    temp filename, `units_cache.json.tmp`, with only one of them holding a
    lock. The second truncated the shared temp, wrote less, and whichever
    os.replace ran last published the mixture. A directly-run
    `python OmniMatrix_upgrade_server_v7_6y.py` alongside the launcher is all
    it took.
    """

    def setUp(self):
        import omni_matrix_logic as logic
        self.logic = logic
        self.folder = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.folder.cleanup)
        self.here = Path(self.folder.name)

        saved_file = logic._cache_file
        saved_enc = dict(logic._encoders)
        saved_dec = dict(logic._decoders)
        self.addCleanup(lambda: (
            setattr(logic, "_cache_file", saved_file),
            logic._encoders.clear(), logic._encoders.update(saved_enc),
            logic._decoders.clear(), logic._decoders.update(saved_dec)))
        logic._cache_file = self.here / "units_cache.json"
        logic._encoders.clear()
        logic._decoders.clear()

    def test_each_write_uses_its_own_temp_file(self):
        """A shared temp name is what let two writers collide."""
        source = (ROOT / "omni_matrix_logic.py").read_text(encoding="utf-8")
        block = source[source.index("def _save_cache_locked"):]
        block = block[:block.index("\ndef ")]
        self.assertIn("os.getpid()", block,
                      "the temp file is not unique to this process")
        self.assertIn("threading.get_ident()", block,
                      "the temp file is not unique to this thread")

    def test_the_whole_read_modify_write_is_one_critical_section(self):
        """Locking only the write loses one of two concurrent changes."""
        source = (ROOT / "omni_matrix_logic.py").read_text(encoding="utf-8")
        wrapper = source[source.index("def _save_cache():"):]
        wrapper = wrapper[:wrapper.index("def _save_cache_locked")]
        self.assertIn("with _cache_write_lock:", wrapper)
        self.assertIn("_save_cache_locked()", wrapper)
        # And the inner function does the reading as well as the writing, so
        # the lock above really does span both.
        inner = source[source.index("def _save_cache_locked"):]
        inner = inner[:inner.index("\ndef ")]
        self.assertIn("json.load", inner)
        self.assertIn("os.replace", inner)

    def test_concurrent_writers_leave_a_readable_file(self):
        """Driven, not asserted about: eight threads, all writing."""
        import threading as _threading
        self.logic._encoders.update({
            "198.51.100.%d" % n: {"ip": "198.51.100.%d" % n,
                                  "mac": "b8:98:b0:00:00:%02x" % n,
                                  "hostname": "enc-%d" % n}
            for n in range(1, 30)})
        errors = []

        def hammer():
            try:
                for _ in range(12):
                    self.logic._save_cache()
            except Exception as exc:                 # pragma: no cover
                errors.append(exc)

        threads = [_threading.Thread(target=hammer) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(60)
        self.assertEqual(errors, [])

        raw = (self.here / "units_cache.json").read_text(encoding="utf-8")
        data = json.loads(raw)                       # the assertion that matters
        self.assertIsInstance(data, list)
        self.assertEqual(len(data), 29)
        # And no temp files left behind.
        leftovers = [p.name for p in self.here.iterdir() if ".tmp" in p.name]
        self.assertEqual(leftovers, [])

    def test_a_malformed_cache_is_moved_aside_once(self):
        """Re-reading a file that cannot be parsed will not start parsing it.

        The bench logged the same failure five times during one startup,
        because every caller re-read the same broken file.
        """
        broken = self.here / "units_cache.json"
        broken.write_text('[{"ip": "198.51.100.1"}]\n  "left over"\n',
                          encoding="utf-8")
        # `_load_cache` populates the module's own device tables rather than
        # returning anything, so that is what is inspected.
        self.logic._load_cache()
        self.assertEqual(dict(self.logic._encoders), {},
                         "a malformed cache was used anyway")
        self.assertEqual(dict(self.logic._decoders), {})
        self.assertFalse(broken.exists(),
                         "the malformed file was left where it would fail again")
        spoiled = [p.name for p in self.here.iterdir() if "corrupt" in p.name]
        self.assertEqual(len(spoiled), 1, spoiled)
        # Kept, not deleted: it is the only evidence of how it got that way.
        self.assertIn("left over",
                      (self.here / spoiled[0]).read_text(encoding="utf-8"))

    def test_a_good_cache_is_not_quarantined(self):
        good = self.here / "units_cache.json"
        good.write_text(json.dumps([{"ip": "198.51.100.1",
                                     "mac": "b8:98:b0:00:00:01",
                                     "role": "encoder",
                                     "type": "Encoder"}]),
                        encoding="utf-8")
        self.logic._load_cache()
        self.assertEqual(len(self.logic._encoders)
                         + len(self.logic._decoders), 1)
        self.assertTrue(good.exists())
        self.assertEqual([p.name for p in self.here.iterdir()
                          if "corrupt" in p.name], [])

    def test_the_exact_bench_corruption_is_recognised(self):
        """A complete document followed by the tail of a longer one."""
        good = json.dumps([{"ip": "198.51.100.%d" % n} for n in range(1, 17)],
                          indent=2)
        tail = '\n      "2.2"\n    ],\n    "firmwareversion": "2.1.2"\n  }\n]'
        (self.here / "units_cache.json").write_text(good + tail,
                                                    encoding="utf-8")
        self.logic._load_cache()
        self.assertEqual(dict(self.logic._encoders), {})
        self.assertEqual(len([p for p in self.here.iterdir()
                              if "corrupt" in p.name]), 1)
