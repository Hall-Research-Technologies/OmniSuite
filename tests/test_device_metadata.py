"""Location and description: presentation, and never identity.

OmniStream reports both in `systeminfo.config`, which every probe already
fetches, so surfacing them costs no device read and writes nothing back. What
these tests hold is the boundary: they may change what a device is *called* on
screen and they may never change what it *is*.

Fixtures are synthetic throughout -- documentation addresses, invented MACs and
hostnames that share nothing with the bench -- because a rule proven against
one site's data is not proven.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("OMNI_SMOKE", "1")

import omni_projection                                  # noqa: E402
import omni_users                                       # noqa: E402
from omni_users import Permissions, UserStore           # noqa: E402

CAMERA = "0A:1B:2C:00:00:01"
LECTERN = "0A:1B:2C:00:00:02"
NORTH = "0A:1B:2C:00:00:11"
SOUTH = "0A:1B:2C:00:00:12"


def unit(mac, address, hostname, *, role="decoder", location="",
         description="", model="hw-omni-d4511"):
    """A cached unit, shaped the way the probe builds one."""
    return {
        "ip": address, "mac": mac, "hostname": hostname, "role": role,
        "model": model,
        "details": {"systeminfo": {"config": {
            "hostname": hostname, "location": location,
            "description": description, "model": model,
        }}},
    }


def matrix_state(units):
    return {
        "encoders": [{"ip": u["ip"], "host": u["hostname"], "model": u["model"]}
                     for u in units if u["role"] == "encoder"],
        "decoders": [{"ip": u["ip"], "host": u["hostname"], "model": u["model"]}
                     for u in units if u["role"] == "decoder"],
        "routes": {},
    }


class Aliases:
    """Just enough of the alias store for a projection."""

    def __init__(self, mapping=None):
        self._mapping = {omni_users._normalise_identity(k): v
                         for k, v in (mapping or {}).items()}

    def display_name(self, identity, fallback):
        return self._mapping.get(
            omni_users._normalise_identity(identity)) or fallback

    def alias_for(self, identity):
        return self._mapping.get(omni_users._normalise_identity(identity), "")


class Account:
    def __init__(self, permissions, admin=False):
        self.permissions = permissions
        self.is_admin = admin
        self.username = "teacher"


def user_with(sources=(), destinations=(), features=("av",)):
    return Account(Permissions(features=features, sources=sources,
                               destinations=destinations))


# ---------------------------------------------------------------- reading ---
class ReadingMetadataTests(unittest.TestCase):
    def test_location_comes_from_systeminfo(self):
        record = unit(NORTH, "198.51.100.11", "dec-a", location="Room 101")
        self.assertEqual(omni_projection.device_location(record), "Room 101")

    def test_a_promoted_top_level_value_wins(self):
        """A unit discovered after the field was promoted answers from itself."""
        record = unit(NORTH, "198.51.100.11", "dec-a", location="Room 101")
        record["location"] = "Room 202"
        self.assertEqual(omni_projection.device_location(record), "Room 202")

    def test_a_unit_cached_before_the_field_existed_still_answers(self):
        """No rescan may be required to pick this up."""
        record = unit(NORTH, "198.51.100.11", "dec-a", location="Room 101")
        record.pop("location", None)
        self.assertEqual(omni_projection.device_location(record), "Room 101")

    def test_missing_and_blank_degrade_to_nothing(self):
        for record in (unit(NORTH, "198.51.100.11", "dec-a"),
                       unit(NORTH, "198.51.100.11", "dec-a", location="   "),
                       {}, {"details": {}}, {"details": {"systeminfo": {}}}):
            with self.subTest(record=str(record)[:40]):
                self.assertEqual(omni_projection.device_location(record), "")
                self.assertEqual(omni_projection.device_description(record), "")

    def test_no_device_read_is_issued_to_obtain_them(self):
        """Both are read out of the record in hand, by construction."""
        import inspect
        source = inspect.getsource(omni_projection)
        body = source[source.index("def device_location"):
                      source.index("def distinguisher")]
        for forbidden in ("config_get", "ws_", "socket", "urlopen", "requests"):
            self.assertNotIn(forbidden, body)


class PrecedenceTests(unittest.TestCase):
    ROW = {"host": "dec-a", "model": "hw-omni-d4511"}

    def test_location_is_preferred(self):
        record = unit(NORTH, "198.51.100.11", "dec-a",
                      location="Room 101", description="Lecture capture")
        self.assertEqual(omni_projection.distinguisher(self.ROW, record),
                         "Room 101")

    def test_description_is_next(self):
        record = unit(NORTH, "198.51.100.11", "dec-a",
                      description="Lecture capture")
        self.assertEqual(omni_projection.distinguisher(self.ROW, record),
                         "Lecture capture")

    def test_the_hostname_is_the_fallback(self):
        record = unit(NORTH, "198.51.100.11", "dec-a")
        self.assertEqual(omni_projection.distinguisher(self.ROW, record), "dec-a")

    def test_neither_address_nor_mac_is_ever_used(self):
        record = unit(NORTH, "198.51.100.11", "dec-a")
        for produced in (omni_projection.distinguisher(self.ROW, record),
                         omni_projection.device_location(record),
                         omni_projection.device_description(record)):
            self.assertNotIn("198.51.100.11", produced)
            self.assertNotIn("0a", produced.lower().replace("dec-a", ""))


# --------------------------------------------------------- disambiguation ---
class DuplicateNameTests(unittest.TestCase):
    def setUp(self):
        self.units = [
            unit(NORTH, "198.51.100.11", "dec-north", location="Room 101"),
            unit(SOUTH, "198.51.100.12", "dec-south", location="Room 102"),
            unit(CAMERA, "198.51.100.21", "enc-camera", role="encoder",
                 location="Room 101", model="hw-omni-e4521"),
        ]
        self.aliases = Aliases({NORTH: "Front Display", SOUTH: "Front Display",
                                CAMERA: "Document Camera"})

    def _state(self, account):
        return omni_projection.user_state(
            matrix_state(self.units), self.units, account, self.aliases)

    def test_a_repeated_label_is_resolved_by_location(self):
        account = user_with(sources=[CAMERA], destinations=[NORTH, SOUTH])
        names = sorted(d["name"] for d in self._state(account)["destinations"])
        self.assertEqual(names,
                         ["Front Display — Room 101", "Front Display — Room 102"])

    def test_a_unique_label_is_left_exactly_as_typed(self):
        account = user_with(sources=[CAMERA], destinations=[NORTH])
        state = self._state(account)
        self.assertEqual([d["name"] for d in state["destinations"]],
                         ["Front Display"])
        self.assertEqual([s["name"] for s in state["sources"]],
                         ["Document Camera"])

    def test_ambiguity_is_judged_over_what_this_account_can_see(self):
        """A duplicate in a room the account cannot reach changes nothing."""
        account = user_with(sources=[CAMERA], destinations=[SOUTH])
        self.assertEqual([d["name"] for d in self._state(account)["destinations"]],
                         ["Front Display"])

    def test_description_resolves_a_repeat_when_there_is_no_location(self):
        self.units[0] = unit(NORTH, "198.51.100.11", "dec-north",
                             description="House left")
        self.units[1] = unit(SOUTH, "198.51.100.12", "dec-south",
                             description="House right")
        account = user_with(sources=[], destinations=[NORTH, SOUTH])
        names = sorted(d["name"] for d in self._state(account)["destinations"])
        self.assertEqual(names,
                         ["Front Display — House left", "Front Display — House right"])

    def test_a_mixture_still_distinguishes(self):
        """One display has a location and the other does not."""
        self.units[1] = unit(SOUTH, "198.51.100.12", "dec-south")
        account = user_with(sources=[], destinations=[NORTH, SOUTH])
        names = sorted(d["name"] for d in self._state(account)["destinations"])
        self.assertEqual(names,
                         ["Front Display — Room 101", "Front Display — dec-south"])

    def test_no_address_or_mac_reaches_a_user_name(self):
        account = user_with(sources=[CAMERA], destinations=[NORTH, SOUTH])
        blob = repr(self._state(account))
        for leak in ("198.51.100", "0A:1B:2C", "0a1b2c"):
            self.assertNotIn(leak, blob)

    def test_the_projection_gains_no_metadata_fields(self):
        """Location is used to build a name, not shipped as its own field.

        Sending it separately would widen what a restricted account receives
        for no benefit the name does not already give.
        """
        account = user_with(sources=[CAMERA], destinations=[NORTH, SOUTH])
        state = self._state(account)
        self.assertEqual(set(state["destinations"][0]),
                         {"id", "name", "online", "showing"})
        self.assertEqual(set(state["sources"][0]), {"id", "name", "online"})

    def test_metadata_never_travels_for_a_device_outside_the_grant(self):
        account = user_with(sources=[], destinations=[NORTH])
        blob = repr(self._state(account))
        self.assertNotIn("Room 102", blob)
        self.assertNotIn("dec-south", blob)


# ------------------------------------------------- presentation ≠ identity ---
class MetadataIsNotIdentityTests(unittest.TestCase):
    """Changing what a device says about itself must change nothing that binds.

    This is the whole reason location is safe to surface. If any of these
    fail, a site that relabels a room has silently re-permissioned it.
    """

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="omni-meta-"))
        self.store = UserStore(self.dir / "users.json", self.dir / "audit.json")
        self.store.create("teacher", "teacher-password-1", omni_users.ROLE_USER)
        self.account = self.store.find_by_username("teacher")
        self.store.set_permissions(self.account.id, Permissions(
            features=["av"], sources=[CAMERA], destinations=[NORTH]))
        self.account = self.store.find_by_username("teacher")

    def _permits(self):
        return (self.account.permissions.may_use_source(CAMERA),
                self.account.permissions.may_control_destination(NORTH))

    def test_a_permission_survives_the_room_being_renamed(self):
        before = self._permits()
        first = unit(NORTH, "198.51.100.11", "dec-north", location="Room 101")
        second = unit(NORTH, "198.51.100.11", "dec-north", location="Room 202")
        self.assertNotEqual(omni_projection.device_location(first),
                            omni_projection.device_location(second))
        self.assertEqual(self._permits(), before)
        self.assertEqual(self._permits(), (True, True))

    def test_a_location_string_is_not_a_permission(self):
        for pretender in ("Room 101", "Front Display", "Front Display — Room 101",
                          "dec-north", "", "   "):
            with self.subTest(pretender=pretender):
                self.assertFalse(
                    self.account.permissions.may_control_destination(pretender))
                self.assertFalse(
                    self.account.permissions.may_use_source(pretender))

    def test_a_disambiguated_name_is_not_an_identity(self):
        """The dash form must not normalise into something that permits."""
        label = "Front Display — Room 101"
        self.assertFalse(self.account.permissions.may_control_destination(label))
        self.assertFalse(self.account.permissions.may_use_source(label))
        # The normaliser lowercases any string it is given, so what matters is
        # not that it refuses one -- it is that what comes out can never equal
        # a stored identity, which is a normalised hardware address.
        self.assertNotIn(omni_users._normalise_identity(label),
                         self.account.permissions.destinations)
        self.assertNotIn(omni_users._normalise_identity(label),
                         self.account.permissions.sources)

    def test_an_alias_and_a_location_are_different_things(self):
        """One belongs to OmniSuite, the other to the equipment."""
        aliases = Aliases({NORTH: "Front Display"})
        record = unit(NORTH, "198.51.100.11", "dec-north", location="Room 101")
        self.assertEqual(aliases.alias_for(NORTH), "Front Display")
        self.assertEqual(omni_projection.device_location(record), "Room 101")
        self.assertNotEqual(aliases.alias_for(NORTH),
                            omni_projection.device_location(record))

    def test_multiview_approval_is_unaffected_by_location(self):
        key = omni_users.multiview_key(NORTH, "Lecture 2x2")
        self.store.set_permissions(self.account.id, Permissions(
            features=["multiview"], destinations=[NORTH], multiviews=[key]))
        account = self.store.find_by_username("teacher")
        self.assertTrue(account.permissions.may_operate_multiview(
            NORTH, "Lecture 2x2"))
        # The same approval, asked for under the display's location or its
        # disambiguated label, is not an approval at all.
        for pretender in ("Room 101", "Front Display — Room 101", "dec-north"):
            with self.subTest(pretender=pretender):
                self.assertFalse(account.permissions.may_operate_multiview(
                    pretender, "Lecture 2x2"))

    def test_routing_identity_is_still_the_hardware_identity(self):
        units = [unit(NORTH, "198.51.100.11", "dec-north", location="Room 101")]
        index = omni_projection._identity_by_address(units)
        self.assertEqual(index["198.51.100.11"],
                         omni_users._normalise_identity(NORTH))
        self.assertNotIn("Room 101", index)
        self.assertNotIn("Room 101", index.values())


if __name__ == "__main__":
    unittest.main()
