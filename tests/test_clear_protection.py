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

    def test_the_ordinary_clear_is_never_the_destructive_one(self):
        """Device Info's button may now offer the override. It may not BE it.

        This used to assert that the word `override` appeared nowhere in the
        handler, on the reasoning that removing a device somebody was given is
        a decision about their permissions and belongs where permissions are.
        That path still exists and is unchanged; what has been added beside it
        is an explicit, default-off choice in the confirmation, because an
        administrator who has just been shown the list is exactly the person
        in a position to decide.

        So the property is not "the word is absent" -- it is that the safe
        answer is what happens unless somebody deliberately asked otherwise.
        """
        page = (ROOT / "ui" / "index.html").read_text(encoding="utf-8")
        start = page.index("#clear_units_btn")
        handler = page[start:page.index("\n});", start)]
        code = "\n".join(line.split("//")[0] for line in handler.split("\n"))

        # It starts false and only a tick box changes it.
        self.assertIn("let override = false;", code)
        self.assertIn("onValue: (v) => { override = v; }", code)
        # The request carries nothing unless it was asked for.
        self.assertIn("JSON.stringify(override ? {override: true} : {})", code)
        # And the dialog's own default is Cancel.
        self.assertIn("defaultAction: 'cancel'", code)

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



class ThePreviewChangesNothingTests(ClearTestBase):
    """The confirmation has to be built from facts, before anything is touched.

    A page that decided what to warn about from what it happened to remember
    would warn about the wrong thing exactly when it mattered, so it asks --
    and asking must be free of consequences.
    """

    def preview(self):
        return self.client.get("/api/clear_units/preview")

    def test_it_is_a_read_and_leaves_the_inventory_alone(self):
        before = self.cached()
        self.remote_access(True)
        self.with_a_user()
        response = self.preview()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.cached(), before,
                         "asking what a clear would do cleared something")

    def test_it_says_whether_remote_access_is_on(self):
        """The page confirms on this, not on whether anything is assigned."""
        self.remote_access(False)
        self.assertFalse(self.preview().get_json()["remote_access"])
        self.remote_access(True)
        self.assertTrue(self.preview().get_json()["remote_access"])

    def test_it_names_the_units_and_the_people(self):
        self.remote_access(True)
        self.with_a_user()
        body = self.preview().get_json()
        self.assertEqual(body["assigned_count"], 2)
        names = {row["name"] for row in body["assigned"]}
        self.assertTrue(names, "nothing was named")
        for row in body["assigned"]:
            with self.subTest(unit=row["name"]):
                self.assertEqual(row["users"], ["teacher"])
                # A name a person can act on, not an identity.
                self.assertNotIn(":", row["name"],
                                 "a raw MAC reached the confirmation")

    def test_a_friendly_name_is_used_where_one_is_set(self):
        self.remote_access(True)
        self.with_a_user()
        self.srv._device_aliases.set_alias(ASSIGNED_DISPLAY, "Hall Display")
        self.addCleanup(self.srv._device_aliases.set_alias,
                        ASSIGNED_DISPLAY, "")
        names = {row["name"] for row in self.preview().get_json()["assigned"]}
        self.assertIn("Hall Display", names,
                      "the operator's own name for the device was not used")

    def test_it_is_sorted_so_the_list_does_not_move_about(self):
        self.remote_access(True)
        self.with_a_user()
        rows = self.preview().get_json()["assigned"]
        self.assertEqual([r["name"] for r in rows],
                         sorted((r["name"] for r in rows), key=str.lower))

    def test_nothing_assigned_is_still_worth_confirming(self):
        """Clearing changes what every User's controls are built from."""
        self.remote_access(True)
        body = self.preview().get_json()
        self.assertTrue(body["remote_access"])
        self.assertEqual(body["assigned_count"], 0)
        self.assertGreater(body["would_clear"], 0,
                           "the page cannot say how much it would remove")

    def test_it_fails_closed_when_assignments_cannot_be_read(self):
        """An operator can look again; they cannot un-clear."""
        self.remote_access(True)
        broken = lambda: (_ for _ in ()).throw(OSError("the store is gone"))
        original = self.store.assigned_devices
        self.store.assigned_devices = broken
        self.addCleanup(lambda: setattr(self.store, "assigned_devices",
                                        original))
        response = self.preview()
        self.assertEqual(response.status_code, 503)
        body = response.get_json()
        self.assertFalse(body["ok"])
        self.assertIn("could not read", body["error"].lower())
        # And it does not report an empty list, which would read as "nothing
        # is assigned" and invite exactly the clear it cannot vouch for.
        self.assertNotIn("assigned", body)

    def test_it_is_administrative(self):
        access, _feature = omni_access.classify("/api/clear_units/preview")
        self.assertIs(access, omni_access.Access.ADMIN,
                      "it names User accounts")


class TheOverrideKeepsThePermissionsTests(ClearTestBase):
    """Removing a unit from the inventory is not removing somebody's grant.

    The override takes the equipment out of the discovered list, which is what
    an administrator asked for. What it must never do is quietly rewrite the
    account, because then rediscovering the same physical unit would not bring
    the assignment back and nobody would know why.
    """

    def granted(self):
        account = self.store.find_by_username("teacher")
        return (sorted(account.permissions.sources),
                sorted(account.permissions.destinations))

    def test_an_override_clear_does_not_touch_the_account(self):
        self.remote_access(True)
        self.with_a_user()
        before = self.granted()
        response = self.clear(override=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.cached(), [], "the override kept something")
        self.assertEqual(self.granted(), before,
                         "clearing a unit rewrote somebody's permissions")

    def test_the_same_equipment_reconnects_when_it_is_found_again(self):
        self.remote_access(True)
        self.with_a_user()
        self.clear(override=True)
        self.assertEqual(self.cached(), [])

        # A later scan finds the same physical units, at whatever addresses.
        rediscovered = [dict(u) for u in UNITS]
        for unit in rediscovered:
            unit["ip"] = "198.51.100." + unit["ip"].rsplit(".", 1)[-1]
        self.srv._save_cache(rediscovered)

        body = self.client.get("/api/clear_units/preview").get_json()
        self.assertEqual(body["assigned_count"], 2,
                         "the assignments did not reconnect to the equipment")
        for row in body["assigned"]:
            self.assertEqual(row["users"], ["teacher"])

    def test_a_different_unit_at_an_old_address_inherits_nothing(self):
        """Identity, not address -- the rule the whole design rests on."""
        self.remote_access(True)
        self.with_a_user()
        self.clear(override=True)
        stranger = [{"ip": u["ip"], "mac": "b8:98:b0:00:00:ff",
                     "hostname": "someone-elses-kit"} for u in UNITS]
        self.srv._save_cache(stranger)
        body = self.client.get("/api/clear_units/preview").get_json()
        self.assertEqual(body["assigned_count"], 0,
                         "a different device inherited an assignment by "
                         "taking an old address")



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
