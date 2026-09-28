"""Clearing the inventory must not quietly take equipment off a User.

An administrator pressing Clear Units is tidying up discovery. They are not,
usually, saying "remove the front display from every classroom that was
assigned it" -- but that is what it looked like afterwards, with nothing
saying it had happened.

So while Remote Access is on, a device assigned to at least one account is
kept and the administrator is told. With Remote Access off there is nobody to
protect: a standalone installation must not be made to argue with dormant
records, and the ordinary path is left exactly as it was.

The two halves are deliberately separate:

  * Clear Units keeps assigned devices. It is not the destructive override.
  * The override lives where permissions are, because clearing a device
    somebody was given is a decision about their permissions.

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
        return self.client.post("/api/clear_units", json=body or None)

    def cached(self):
        return sorted(omni_users._normalise_identity(u.get("mac"))
                      for u in (self.srv._load_cache() or []))


class RemoteAccessOffTests(ClearTestBase):
    """Nothing to protect, so nothing changes."""

    def test_everything_is_cleared(self):
        self.with_a_user()
        response = self.clear()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.cached(), [])

    def test_the_answer_says_protection_was_not_in_play(self):
        self.with_a_user()
        body = self.clear().get_json()
        self.assertEqual(body["protection"], "off")
        self.assertEqual(body["retained"], 0)

    def test_dormant_assignments_survive_the_clear(self):
        """The accounts are untouched: identity is stable and devices return."""
        self.with_a_user()
        self.clear()
        teacher = self.store.find_by_username("teacher")
        self.assertIn(ASSIGNED_SOURCE, teacher.permissions.sources)
        self.assertIn(ASSIGNED_DISPLAY, teacher.permissions.destinations)

    def test_turning_remote_access_on_afterwards_finds_them_again(self):
        """A rescan restores the devices and the assignment still matches.

        The permission names a hardware identity, so a device that comes back
        is the same device -- and a DIFFERENT unit at the same address is not
        silently adopted, because the address was never the identity.
        """
        self.with_a_user()
        self.clear()
        self.remote_access(True)
        # Rediscovered: same hardware, new address.
        moved = [dict(u, ip="192.0.2.99") for u in UNITS
                 if omni_users._normalise_identity(u["mac"]) == ASSIGNED_SOURCE]
        self.srv._save_cache(moved)
        assigned = self.store.assigned_devices()
        self.assertIn(ASSIGNED_SOURCE, assigned)
        # And the protection now applies to it at its new address.
        body = self.clear().get_json()
        self.assertEqual(body["retained"], 1)
        self.assertEqual(self.cached(), [ASSIGNED_SOURCE])


class RemoteAccessOnTests(ClearTestBase):
    """Assigned devices are kept, and the administrator is told."""

    def setUp(self):
        super().setUp()
        self.remote_access(True)

    def test_assigned_units_are_retained_and_the_rest_cleared(self):
        self.with_a_user()
        body = self.clear().get_json()
        self.assertEqual(sorted(self.cached()),
                         sorted([ASSIGNED_SOURCE, ASSIGNED_DISPLAY]))
        self.assertEqual(body["retained"], 2)
        self.assertEqual(body["cleared"], 2)

    def test_it_says_which_accounts_the_devices_belong_to(self):
        self.with_a_user()
        body = self.clear().get_json()
        self.assertEqual(body["retained_for"], ["teacher"])
        self.assertEqual(body["protection"], "on")

    def test_an_encoder_and_a_decoder_are_both_protected(self):
        """Both halves of an assignment, not just the display."""
        self.with_a_user()
        self.clear()
        kept = self.cached()
        self.assertIn(ASSIGNED_SOURCE, kept)
        self.assertIn(ASSIGNED_DISPLAY, kept)
        self.assertNotIn(SPARE_SOURCE, kept)
        self.assertNotIn(SPARE_DISPLAY, kept)

    def test_a_multiview_approval_protects_its_decoder(self):
        """Approving a preset names a decoder; clearing it takes the subject."""
        self.srv._save_cache([dict(u) for u in UNITS]
                             + [{"ip": "192.0.2.29", "mac": MULTIVIEW_DISPLAY,
                                 "hostname": "dec-mv", "model": "hw-omni-d4511",
                                 "role": "decoder", "type": "Decoder"}])
        self.with_a_user(multiviews=[omni_users.multiview_key(
            MULTIVIEW_DISPLAY, "quad")])
        self.clear()
        self.assertIn(MULTIVIEW_DISPLAY, self.cached())

    def test_a_disabled_account_still_protects_its_devices(self):
        """An account that is off is one an administrator can turn back on."""
        self.with_a_user()
        teacher = self.store.find_by_username("teacher")
        self.store.set_enabled(teacher.id, False)
        self.clear()
        self.assertIn(ASSIGNED_SOURCE, self.cached())

    def test_an_administrator_account_protects_nothing(self):
        """The role is the grant, so there are no per-device assignments."""
        self.store.create("principal", "principal-pass-1", omni_users.ROLE_ADMIN)
        body = self.clear().get_json()
        self.assertEqual(body["retained"], 0)
        self.assertEqual(self.cached(), [])

    def test_with_no_accounts_at_all_everything_is_cleared(self):
        body = self.clear().get_json()
        self.assertEqual(self.cached(), [])
        self.assertEqual(body["retained"], 0)

    def test_the_usb_inventory_is_still_forgotten(self):
        """Protection is about the device cache, not about the whole action."""
        self.with_a_user()
        body = self.clear().get_json()
        self.assertTrue(body["ok"])
        self.assertIn("usb_cleared", body)


class TheOverrideTests(ClearTestBase):
    """Explicit, and not what the ordinary button does."""

    def setUp(self):
        super().setUp()
        self.remote_access(True)
        self.with_a_user()

    def test_the_override_clears_everything(self):
        body = self.clear(override=True).get_json()
        self.assertEqual(self.cached(), [])
        self.assertEqual(body["protection"], "override")
        self.assertEqual(body["retained"], 0)

    def test_the_ordinary_clear_never_overrides(self):
        """Device Info's button must not be the destructive one."""
        page = (ROOT / "ui" / "index.html").read_text(encoding="utf-8")
        start = page.index("#clear_units_btn")
        handler = page[start:page.index("});", start)]
        self.assertNotIn("override", handler,
                         "Clear Units on Device Info sends the override")

    def _override_handler(self):
        """The click handler, not the render function that shares its name."""
        admin = (ROOT / "ui" / "admin" / "users.js").read_text(encoding="utf-8")
        start = admin.index("assignedClear.addEventListener")
        end = admin.index("el('alias_save')", start)
        body = admin[start:end]
        return "\n".join(line.split("//")[0] for line in body.split("\n"))

    def test_the_override_lives_where_permissions_are(self):
        handler = self._override_handler()
        self.assertIn("override: true", handler,
                      "Manage users does not offer the override")
        self.assertIn("/api/clear_units", handler,
                      "the override is a second implementation of clearing")

    def test_the_override_asks_first(self):
        handler = self._override_handler()
        self.assertIn("omniConfirm", handler,
                      "clearing assigned devices happens without asking")
        self.assertIn("danger", handler,
                      "a destructive action should be distinguished as one")

    def test_the_question_names_who_is_affected(self):
        self.assertIn("Accounts affected", self._override_handler(),
                      "the question does not say whose equipment this is")

    def test_removing_the_assignment_is_the_ordinary_way(self):
        """No override needed once nobody has the device."""
        teacher = self.store.find_by_username("teacher")
        self.store.set_permissions(teacher.id, Permissions(features=("av",)))
        body = self.clear().get_json()
        self.assertEqual(self.cached(), [])
        self.assertEqual(body["retained"], 0)


class FailClosedTests(ClearTestBase):
    """If what is assigned cannot be established, nothing is cleared."""

    def test_an_unreadable_store_refuses_rather_than_clears(self):
        self.remote_access(True)
        self.with_a_user()

        def explode():
            raise RuntimeError("the account store is unreadable")

        original = self.store.assigned_devices
        self.store.assigned_devices = explode
        self.addCleanup(lambda: setattr(self.store, "assigned_devices", original))
        response = self.clear()
        self.assertGreaterEqual(response.status_code, 500)
        self.assertNotEqual(self.cached(), [],
                            "the inventory was cleared without knowing what "
                            "was assigned")


if __name__ == "__main__":
    unittest.main()


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
