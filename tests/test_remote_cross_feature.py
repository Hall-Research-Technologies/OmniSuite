"""Features built by different hands, held together.

Clear Units protection, the routing interfaces, the Matrix projection, the
permission revision and Direct Access were each tested where they were made.
What they promise *together* is checked here: a clear that keeps an assigned
unit leaves that account's Matrix exactly as it was and its revision unmoved, a
full clear (Remote Access off, the deliberate second step) moves both, and a
Direct Access session sees the same account the password session does.

Nothing contacts hardware: the device state handed to the projection is
synthetic and the stores are the isolated ones the lifecycle harness builds.
"""
from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import omni_projection                               # noqa: E402
import omni_users                                    # noqa: E402
from omni_users import Permissions                   # noqa: E402
from tests.test_clear_inventory import (             # noqa: E402
    CAMERA, FRONT, InventoryBase)
from tests.test_display_groups import ROOM_A, ROOM_B, SRC  # noqa: E402


def device_state(units):
    """A synthetic /api/state for whichever units are in the inventory."""
    encoders = [{"ip": u["ip"], "online": True} for u in units if u.get("role") == "encoder"]
    decoders = [{"ip": u["ip"], "online": True, "multiview_active": False,
                 "ip1_addr": "", "ip1_port": 0, "ip3_addr": "", "ip3_port": 0}
                for u in units if u.get("role") == "decoder"]
    return {"ok": True, "encoders": encoders, "decoders": decoders, "routes": {}}


class ClearAndMatrixTests(InventoryBase):
    def setUp(self):
        super().setUp()
        self.give(["standard", "matrix"])

    def give(self, interfaces):
        result = self.store.set_permissions(self.teacher.id, Permissions(
            features=("av",), sources=[SRC, CAMERA], destinations=[FRONT],
            groups=[self.group["id"]], interfaces=interfaces))
        self.assertTrue(result["ok"], result)

    def account(self):
        return self.store.find_by_username("teacher")

    def matrix(self):
        units = copy.deepcopy(self.srv._load_cache() or [])
        return omni_projection.user_matrix(
            device_state(units), units, self.account(), None, self.groups)

    def shape(self, result):
        return ([s["id"] for s in result["sources"]],
                [(r["kind"], r["id"]) for r in result["rows"]])

    def revision(self):
        return omni_projection.permission_revision(self.account(), self.groups, self.aliases)

    def test_a_remote_access_clear_leaves_the_accounts_matrix_as_it_was(self):
        before = self.shape(self.matrix())
        revision = self.revision()
        reply = self.clear()
        self.assertEqual(reply.status_code, 200)
        self.assertGreater(reply.get_json()["cleared"], 0,
                           "precondition: nothing was cleared, so nothing was tested")
        self.assertEqual(self.shape(self.matrix()), before)
        self.assertEqual(self.revision(), revision,
                         "a clear that only removes unassigned units moved an account's revision")
        # And the rows really are live, not merely listed.
        rows = {r["id"]: r for r in self.matrix()["rows"]}
        self.assertTrue(rows[FRONT].get("online", True))

    def test_the_matrix_never_names_a_unit_the_account_does_not_hold(self):
        self.clear()
        sources, rows = self.shape(self.matrix())
        self.assertEqual(set(sources), {SRC, CAMERA})
        self.assertEqual({i for _k, i in rows if _k == "display"}, {FRONT})
        # A grouped decoder is reachable only through its group row.
        self.assertNotIn(ROOM_A, {i for _k, i in rows})
        self.assertNotIn(ROOM_B, {i for _k, i in rows})

    def test_a_full_clear_with_remote_access_off_moves_the_revision(self):
        self.set_remote(False)
        revision = self.revision()
        self.assertEqual(self.clear().status_code, 200)
        self.assertNotEqual(self.revision(), revision,
                            "the grants were cascaded away, so open pages must be told")

    def test_the_revision_follows_every_part_of_an_assignment(self):
        base = self.revision()
        seen = {base}
        edits = {
            "source": lambda: self.store.set_permissions(self.teacher.id, Permissions(
                features=("av",), sources=[SRC], destinations=[FRONT],
                groups=[self.group["id"]], interfaces=["standard", "matrix"])),
            "destination": lambda: self.store.set_permissions(self.teacher.id, Permissions(
                features=("av",), sources=[SRC], destinations=[],
                groups=[self.group["id"]], interfaces=["standard", "matrix"])),
            "interface": lambda: self.store.set_permissions(self.teacher.id, Permissions(
                features=("av",), sources=[SRC], destinations=[],
                groups=[self.group["id"]], interfaces=["standard"])),
            "group": lambda: self.store.set_permissions(self.teacher.id, Permissions(
                features=("av",), sources=[SRC], destinations=[], groups=[],
                interfaces=["standard"])),
        }
        for name, edit in edits.items():
            edit()
            now = self.revision()
            self.assertNotIn(now, seen, f"changing the {name} grant did not move the revision")
            seen.add(now)

    def test_a_group_membership_edit_moves_the_revision_of_a_holder(self):
        revision = self.revision()
        self.groups.update(self.group["id"], members=[ROOM_A])
        self.assertNotEqual(self.revision(), revision)

    def test_a_direct_access_session_sees_the_same_account_through_a_clear(self):
        session = self.session_for(self.account(), omni_users.BY_DIRECT_LINK)
        before = self.get("/api/user/revision", session).get_json()["revision"]
        self.assertEqual(self.clear().status_code, 200)
        reply = self.get("/api/user/revision", session)
        self.assertEqual(reply.status_code, 200, "an assigned account lost its link session in a clear")
        self.assertEqual(reply.get_json()["revision"], before)

    def test_a_direct_access_session_converges_when_the_interface_is_removed(self):
        session = self.session_for(self.account(), omni_users.BY_DIRECT_LINK)
        before = self.get("/api/user/revision", session).get_json()["revision"]
        self.give(["standard"])
        after = self.get("/api/user/revision", session).get_json()["revision"]
        self.assertNotEqual(after, before)
        self.assertEqual(self.get("/api/user/matrix_state", session).status_code, 403)


if __name__ == "__main__":
    unittest.main()
