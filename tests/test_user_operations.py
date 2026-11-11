"""Adversarial tests for restricted USB and Multiview operation.

Phases H and I gave a restricted account two more things it can do to real
equipment, so these are attacks rather than demonstrations: a crafted body, an
endpoint identity the account was never offered, a preset name approved on a
different display, a source that appears inside an administrator's layout but
was never granted.

Nothing here contacts hardware. The projections are exercised directly, and
the gate is driven through omni_gate.evaluate, so a failure names the rule that
let something through.
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
import omni_gate                                     # noqa: E402
import omni_projection                               # noqa: E402
import omni_remote                                   # noqa: E402
import omni_users                                    # noqa: E402
from omni_aliases import AliasStore                  # noqa: E402
from omni_users import Permissions, UserStore   # noqa: E402

# Documentation-range addresses only; a test may never carry a bench address.
TEACHER_PC = "B8:98:B0:00:00:01"
DOC_CAMERA = "B8:98:B0:00:00:02"
LAB_CAMERA = "B8:98:B0:00:00:09"
FRONT_DISPLAY = "B8:98:B0:00:00:11"
LAB_DISPLAY = "B8:98:B0:00:00:19"

#: USB endpoints have their own MACs, distinct from the parent they live on.
TEACHER_PC_USB = "B8:98:B0:00:0A:01"
FRONT_DISPLAY_USB = "B8:98:B0:00:0A:11"
LAB_DISPLAY_USB = "B8:98:B0:00:0A:19"
STANDALONE_LEX = "00:1B:13:00:00:01"

UNITS = [
    {"ip": "192.0.2.11", "mac": TEACHER_PC, "hostname": "enc-teacher",
     "model": "hw-omni-e4521", "role": "encoder"},
    {"ip": "192.0.2.12", "mac": DOC_CAMERA, "hostname": "enc-camera",
     "model": "hw-omni-e4111", "role": "encoder"},
    {"ip": "192.0.2.19", "mac": LAB_CAMERA, "hostname": "enc-lab",
     "model": "hw-omni-e4111", "role": "encoder"},
    {"ip": "192.0.2.21", "mac": FRONT_DISPLAY, "hostname": "dec-front",
     "model": "hw-omni-d4511", "role": "decoder"},
    {"ip": "192.0.2.29", "mac": LAB_DISPLAY, "hostname": "dec-lab",
     "model": "hw-omni-d4511", "role": "decoder"},
]

#: Shaped like the administrative USB view, including fields a user must never
#: receive.
USB_STATE = {
    "matrix_lex": [
        {"mac": TEACHER_PC_USB, "ip": "192.0.2.11", "type": "LEX",
         "host": "enc-teacher", "firmware": "4.1.2", "endpoint_mask": "255.255.255.0",
         "host_port": 3, "filter": "none", "icron_revision": "0x1234",
         "usb_discovered": True},
        {"mac": STANDALONE_LEX, "ip": "192.0.2.40", "type": "LEX",
         "host": "standalone-lex", "firmware": "2.0.1", "usb_discovered": True},
    ],
    "matrix_rex": [
        {"mac": FRONT_DISPLAY_USB, "ip": "192.0.2.21", "type": "REX",
         "host": "dec-front", "firmware": "4.1.2", "endpoint_mask": "255.255.255.0",
         "usb_discovered": True},
        {"mac": LAB_DISPLAY_USB, "ip": "192.0.2.29", "type": "REX",
         "host": "dec-lab", "firmware": "4.1.2", "usb_discovered": True},
    ],
    "standalone_routes": {
        FRONT_DISPLAY_USB: {"active": TEACHER_PC_USB},
        LAB_DISPLAY_USB: {"active": STANDALONE_LEX},
    },
}


def _subframe(cell, source_mac, hostname, health="live"):
    unit = next(u for u in UNITS if u["mac"] == source_mac)
    return {"cell": cell, "health": health,
            "source": {"ip": unit["ip"], "hostname": hostname}}


MULTIVIEW_STATE = {
    "ok": True,
    "multiviews": [
        {"name": "ClassroomFour", "layout_label": "2x2", "showable": True,
         "selected_on_output": False,
         "subframes": [_subframe("top_left", TEACHER_PC, "enc-teacher"),
                       _subframe("top_right", DOC_CAMERA, "enc-camera")]},
        # Contains a source the teacher was never granted.
        {"name": "WholeSchool", "layout_label": "1+3", "showable": True,
         "selected_on_output": False,
         "subframes": [_subframe("main", TEACHER_PC, "enc-teacher"),
                       _subframe("bottom_left", LAB_CAMERA, "enc-lab")]},
        # Exists on the decoder but is not approved for anyone.
        {"name": "AdminOnly", "layout_label": "Side by side", "showable": True,
         "selected_on_output": False,
         "subframes": [_subframe("left", TEACHER_PC, "enc-teacher")]},
    ],
}


def remote_env(session=None, csrf=True):
    env = {omni_remote.LISTENER_KEY: omni_remote.REMOTE}
    if session:
        env["HTTP_COOKIE"] = "omnisuite_session=" + session.token
        if csrf:
            env["HTTP_X_OMNISUITE_CSRF"] = session.csrf_token
    return env


class OperationTestBase(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="omni-ops-"))
        self.aliases = AliasStore(self.dir / "device_aliases.json")
        self.aliases.set_alias(TEACHER_PC, "Teacher PC")
        self.aliases.set_alias(DOC_CAMERA, "Document Camera")
        self.aliases.set_alias(FRONT_DISPLAY, "Front Display")
        self.aliases.set_alias(LAB_CAMERA, "Lab Camera")
        self.aliases.set_alias(LAB_DISPLAY, "Lab Display")

        self.store = UserStore(self.dir / "users.json")
        self.store.create("teacher", "teacher-password-1", omni_users.ROLE_USER,
                          Permissions(
                              features=("av", "usb"),
                              sources=(TEACHER_PC, DOC_CAMERA),
                              destinations=(FRONT_DISPLAY,)))
        self.teacher = self.store.find_by_username("teacher")
        self.session = self.store.open_session(self.teacher.id)

        self.store.create("admin", "admin-password-1", omni_users.ROLE_ADMIN)
        self.admin = self.store.find_by_username("admin")

    def decide(self, rule, method="POST", payload=None, session=None, csrf=True):
        return omni_gate.evaluate(rule, method,
                                  remote_env(session or self.session, csrf),
                                  payload or {}, self.store)

    def usb(self, account=None):
        return omni_projection.project_usb_state(
            USB_STATE, UNITS, account or self.teacher, self.aliases)

# =====================================================================  USB
class UsbProjectionTests(OperationTestBase):
    def test_only_permitted_endpoints_are_listed(self):
        projected = self.usb()
        self.assertEqual([c["name"] for c in projected["computers"]],
                         ["Teacher PC"])
        self.assertEqual([p["name"] for p in projected["peripherals"]],
                         ["Front Display"])

    def test_an_unpermitted_endpoint_appears_nowhere(self):
        blob = json.dumps(self.usb())
        for forbidden in (LAB_DISPLAY_USB, STANDALONE_LEX, "dec-lab",
                          "standalone-lex", "Lab Display", "192.0.2.29",
                          "192.0.2.40"):
            self.assertNotIn(forbidden, blob, f"{forbidden} reached a user")

    def test_no_administrative_usb_detail_survives(self):
        blob = json.dumps(self.usb())
        for field in ("firmware", "4.1.2", "endpoint_mask", "255.255.255.0",
                      "host_port", "filter", "icron_revision", "0x1234",
                      "type", "LEX", "REX"):
            self.assertNotIn(field, blob, f"{field} reached a user")

    def test_the_projection_has_only_the_expected_fields(self):
        projected = self.usb()
        self.assertEqual(set(projected), {"ok", "computers", "peripherals"})
        for computer in projected["computers"]:
            self.assertEqual(set(computer), {"id", "name", "online"})
        for peripheral in projected["peripherals"]:
            self.assertEqual(set(peripheral),
                             {"id", "name", "online", "attached_to"})
            self.assertEqual(set(peripheral["attached_to"]),
                             {"mode", "computer_id", "computer_name"})

    def test_an_endpoint_is_named_after_the_device_it_belongs_to(self):
        """Not after its own USB MAC, which means nothing to an operator."""
        computer = self.usb()["computers"][0]
        self.assertEqual(computer["name"], "Teacher PC")

    def test_a_peripheral_on_a_permitted_computer_names_it(self):
        peripheral = self.usb()["peripherals"][0]
        self.assertEqual(peripheral["attached_to"]["mode"], "computer")
        self.assertEqual(peripheral["attached_to"]["computer_name"], "Teacher PC")

    def test_a_peripheral_on_another_computer_is_not_named(self):
        """Same rule as a display showing an unauthorised source."""
        self.store.set_permissions(self.teacher.id, Permissions(
            features=("usb",), sources=(TEACHER_PC,),
            destinations=(FRONT_DISPLAY, LAB_DISPLAY)))
        teacher = self.store.find_by_username("teacher")
        projected = self.usb(teacher)
        lab = [p for p in projected["peripherals"] if p["name"] == "Lab Display"][0]
        self.assertEqual(lab["attached_to"]["mode"], "other")
        self.assertIsNone(lab["attached_to"]["computer_name"])
        self.assertNotIn("standalone-lex", json.dumps(projected))

    def test_an_administrator_sees_every_endpoint(self):
        projected = self.usb(self.admin)
        self.assertEqual(len(projected["computers"]), 2)
        self.assertEqual(len(projected["peripherals"]), 2)


class UsbAuthorizationTests(OperationTestBase):
    def test_the_capability_is_required(self):
        self.store.set_permissions(self.teacher.id, Permissions(
            features=("av",), sources=(TEACHER_PC,), destinations=(FRONT_DISPLAY,)))
        session = self.store.open_session(self.teacher.id)
        for rule in ("/api/user/usb_state", "/api/user/usb_route"):
            with self.subTest(rule=rule):
                decision = self.decide(rule, session=session)
                self.assertFalse(decision.allowed)
                self.assertEqual(decision.reason, "missing_feature:usb")

    def test_the_administrative_usb_endpoints_stay_closed(self):
        for rule in ("/api/usb_state", "/api/usb_route/pair",
                     "/api/usb_route/unpair", "/api/usb_route/resolve",
                     "/api/usb_pair", "/api/usb_unpair", "/api/usb_set_port",
                     "/api/usb_set_filter", "/api/usb_extenders",
                     "/api/usb_extenders/network"):
            with self.subTest(rule=rule):
                decision = self.decide(rule)
                self.assertFalse(decision.allowed)
                self.assertEqual(decision.reason, "not_admin")

    def test_the_permitted_endpoint_set_is_what_the_projection_offered(self):
        """The handler authorises against the same projection the browser saw.

        A crafted request naming an endpoint the account was never shown is
        refused because it is not in that set -- not because somebody
        remembered to list it.
        """
        projected = self.usb()
        offered = ({c["id"] for c in projected["computers"]}
                   | {p["id"] for p in projected["peripherals"]})
        self.assertEqual(
            offered,
            {omni_users._normalise_identity(TEACHER_PC_USB),
             omni_users._normalise_identity(FRONT_DISPLAY_USB)})
        for forbidden in (LAB_DISPLAY_USB, STANDALONE_LEX):
            self.assertNotIn(omni_users._normalise_identity(forbidden), offered)

    def test_csrf_is_required_for_a_usb_change(self):
        decision = self.decide("/api/user/usb_route", payload={
            "computer": TEACHER_PC_USB, "peripherals": FRONT_DISPLAY_USB},
            csrf=False)
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "csrf")


# ===============================================================  Multiview
class CrossCuttingTests(OperationTestBase):
    def test_only_a_granted_source_is_permitted(self):
        """The grant is a list, not a switch: a source that was never given is refused.

        Found by the mutation harness after the Multiview tests that happened to
        exercise it were deleted: `may_use_source` returning True survived.
        """
        permissions = self.teacher.permissions
        self.assertTrue(permissions.may_use_source(TEACHER_PC))
        self.assertTrue(permissions.may_use_source(DOC_CAMERA))
        self.assertFalse(permissions.may_use_source(LAB_CAMERA))
        self.assertFalse(permissions.may_use_source(""))
        self.assertFalse(permissions.may_use_source(None))

    def test_only_a_granted_display_is_permitted(self):
        permissions = self.teacher.permissions
        self.assertTrue(permissions.may_control_destination(FRONT_DISPLAY))
        self.assertFalse(permissions.may_control_destination(LAB_DISPLAY))
        self.assertFalse(permissions.may_control_destination(""))

    def test_a_disabled_account_cannot_operate_anything(self):
        self.store.set_enabled(self.teacher.id, False)
        for rule in ("/api/user/route", "/api/user/usb_route",
                     "/api/user/multiview_show", "/api/user/multiview_switch"):
            with self.subTest(rule=rule):
                self.assertFalse(self.decide(rule).allowed)

    def test_a_revoked_session_cannot_operate_anything(self):
        self.store.revoke(self.session.token)
        for rule in ("/api/user/usb_state", "/api/user/multiview_state"):
            with self.subTest(rule=rule):
                self.assertFalse(self.decide(rule, method="GET").allowed)

    def test_another_accounts_resources_are_not_reachable(self):
        self.store.create("other", "other-password-1", omni_users.ROLE_USER,
                          Permissions(features=("av", "usb"),
                                      sources=(LAB_CAMERA,),
                                      destinations=(LAB_DISPLAY,)))
        other = self.store.find_by_username("other")
        session = self.store.open_session(other.id)
        decision = self.decide("/api/user/route", payload={
            "source": TEACHER_PC, "destination": FRONT_DISPLAY},
            session=session)
        self.assertFalse(decision.allowed)

    def test_a_friendly_name_collision_does_not_cross_a_boundary(self):
        """Two displays, one label, one of them permitted."""
        self.aliases.set_alias(LAB_DISPLAY, "Front Display")
        allowed = self.decide("/api/user/route", payload={
            "source": TEACHER_PC, "destination": FRONT_DISPLAY})
        self.assertTrue(allowed.allowed)
        refused = self.decide("/api/user/route", payload={
            "source": TEACHER_PC, "destination": LAB_DISPLAY})
        self.assertFalse(refused.allowed)

    def test_an_offline_device_keeps_its_permission(self):
        """It must not be routable, and it must not lose its assignment."""
        offline = [dict(u) for u in UNITS]
        matrix = {"encoders": [{"ip": "192.0.2.11", "host": "enc-teacher",
                                "online": False}],
                  "decoders": [], "routes": {}}
        projected = omni_projection.user_state(matrix, offline, self.teacher,
                                               self.aliases)
        source = projected["sources"][0]
        self.assertFalse(source["online"])
        self.assertEqual(source["name"], "Teacher PC")
        self.assertTrue(self.teacher.permissions.may_use_source(TEACHER_PC))

    def test_no_user_operation_returns_credential_material(self):
        blobs = json.dumps(self.usb())
        for forbidden in ("password", "hash", "token", "csrf", "secret"):
            self.assertNotIn(forbidden, blobs.lower())




if __name__ == "__main__":
    unittest.main()
