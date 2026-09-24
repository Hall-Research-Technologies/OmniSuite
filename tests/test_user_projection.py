"""What a restricted account is allowed to know.

The Phase D gate decides what an account may *do*. These tests are about what
it may *see*, which is a separate question and the one this checkpoint exists
to answer: a teacher authorised for one classroom must not receive an inventory
of the building merely because their page needs to draw two lists.

The central assertion is structural rather than a list of forbidden words. The
projection is built from named fields, so the test that matters is "no field
appears in the output that the projection did not deliberately put there" --
which keeps holding as the administrative model grows.
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

import omni_projection                               # noqa: E402
import omni_users                                    # noqa: E402
from omni_users import Permissions, UserStore        # noqa: E402

TEACHER_PC = "B8:98:B0:00:00:01"
DOC_CAMERA = "B8:98:B0:00:00:02"
OTHER_ROOM_SOURCE = "B8:98:B0:00:00:09"
FRONT_DISPLAY = "B8:98:B0:00:00:11"
OTHER_ROOM_DISPLAY = "B8:98:B0:00:00:19"

#: Addresses are from the documentation range: a test must never carry a bench
#: address, as a target or otherwise.
UNITS = [
    {"ip": "192.0.2.11", "mac": TEACHER_PC, "hostname": "teacher-pc",
     "model": "hw-omni-e4111", "role": "encoder"},
    {"ip": "192.0.2.12", "mac": DOC_CAMERA, "hostname": "doc-camera",
     "model": "hw-omni-e4111", "role": "encoder"},
    {"ip": "192.0.2.19", "mac": OTHER_ROOM_SOURCE, "hostname": "lab-camera",
     "model": "hw-omni-e4111", "role": "encoder"},
    {"ip": "192.0.2.21", "mac": FRONT_DISPLAY, "hostname": "front-display",
     "model": "hw-omni-d4111", "role": "decoder"},
    {"ip": "192.0.2.29", "mac": OTHER_ROOM_DISPLAY, "hostname": "lab-display",
     "model": "hw-omni-d4111", "role": "decoder"},
]

#: A matrix payload shaped like the real one, including the administrative
#: fields a decoder row actually carries.
MATRIX = {
    "encoders": [
        {"ip": "192.0.2.11", "host": "teacher-pc", "model": "hw-omni-e4111",
         "serial": "SER-0001", "fw": "2.1.2",
         "v_mcast": "239.0.2.11", "v_port": 1000, "a_mcast": "239.0.2.111",
         "edid": "default", "hdcp_support_version": "1.4/2.2"},
        {"ip": "192.0.2.12", "host": "doc-camera", "model": "hw-omni-e4111",
         "serial": "SER-0002", "fw": "2.1.2",
         "v_mcast": "239.0.2.12", "v_port": 1000},
        {"ip": "192.0.2.19", "host": "lab-camera", "model": "hw-omni-e4111",
         "serial": "SER-0009", "fw": "2.1.2", "v_mcast": "239.0.2.19"},
    ],
    "decoders": [
        {"ip": "192.0.2.21", "host": "front-display", "model": "hw-omni-d4111",
         "serial": "SER-0011", "fw": "2.1.2", "video_input": "ip_input1",
         "ip1_addr": "239.0.2.11", "ip1_port": 1000,
         "video_wall_enabled": False, "video_wall_grid_width": 1,
         "fast_switching_enabled": True, "sap_input_enabled": False,
         "multiview_active": False, "multiview_name": None},
        {"ip": "192.0.2.29", "host": "lab-display", "model": "hw-omni-d4111",
         "serial": "SER-0019", "fw": "2.1.2", "video_input": "ip_input1",
         "ip1_addr": "239.0.2.19", "multiview_active": False},
    ],
    # front-display is showing the teacher PC; lab-display the lab camera.
    "routes": {"192.0.2.21": "192.0.2.11", "192.0.2.29": "192.0.2.19"},
}

#: Every field a restricted account may receive. Anything else appearing in the
#: payload is a leak, whether or not anybody thought to forbid it by name.
ALLOWED_TOP = {"ok", "features", "role", "username", "sources", "destinations"}
ALLOWED_SOURCE = {"id", "name", "online"}
ALLOWED_DESTINATION = {"id", "name", "online", "showing"}
ALLOWED_SHOWING = {"mode", "source_id", "source_name"}


class ProjectionTestBase(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="omni-proj-"))
        self.store = UserStore(self.dir / "users.json")
        self.store.create("teacher", "teacher-password-1", omni_users.ROLE_USER,
                          Permissions(features=("av",),
                                      sources=(TEACHER_PC, DOC_CAMERA),
                                      destinations=(FRONT_DISPLAY,)))
        self.teacher = self.store.find_by_username("teacher")
        self.store.create("admin", "admin-password-1", omni_users.ROLE_ADMIN)
        self.admin = self.store.find_by_username("admin")

    def project(self, account=None):
        return omni_projection.user_state(MATRIX, UNITS, account or self.teacher)


class VisibilityTests(ProjectionTestBase):
    def test_a_teacher_sees_only_their_own_sources(self):
        names = [s["name"] for s in self.project()["sources"]]
        self.assertEqual(sorted(names), ["doc-camera", "teacher-pc"])
        self.assertNotIn("lab-camera", names,
                         "another room's source must not be listed")

    def test_a_teacher_sees_only_their_own_displays(self):
        names = [d["name"] for d in self.project()["destinations"]]
        self.assertEqual(names, ["front-display"])
        self.assertNotIn("lab-display", names)

    def test_an_unauthorised_device_appears_nowhere_in_the_payload(self):
        """Not merely absent from the lists -- absent from the whole response."""
        blob = json.dumps(self.project())
        for forbidden in ("lab-camera", "lab-display", OTHER_ROOM_SOURCE,
                          OTHER_ROOM_DISPLAY, "192.0.2.19", "192.0.2.29"):
            self.assertNotIn(forbidden, blob,
                             f"{forbidden} reached a restricted account")

    def test_an_administrator_sees_everything(self):
        payload = self.project(self.admin)
        self.assertEqual(len(payload["sources"]), 3)
        self.assertEqual(len(payload["destinations"]), 2)


class FieldAllowlistTests(ProjectionTestBase):
    """The structural assertion: only named fields come out.

    A deny list of forbidden words would pass the day somebody adds a
    fifty-eighth field to a decoder row. This fails.
    """

    def test_the_payload_has_only_the_expected_top_level_fields(self):
        self.assertEqual(set(self.project()), ALLOWED_TOP)

    def test_a_source_has_only_the_expected_fields(self):
        for source in self.project()["sources"]:
            self.assertEqual(set(source), ALLOWED_SOURCE)

    def test_a_destination_has_only_the_expected_fields(self):
        for destination in self.project()["destinations"]:
            self.assertEqual(set(destination), ALLOWED_DESTINATION)
            self.assertEqual(set(destination["showing"]), ALLOWED_SHOWING)

    def test_no_administrative_detail_survives_the_projection(self):
        """The engineering fields a decoder row actually carries."""
        blob = json.dumps(self.project())
        for field in ("serial", "SER-0001", "SER-0011", "fw", "2.1.2",
                      "v_mcast", "239.0.2.11", "ip1_addr", "video_wall",
                      "fast_switching", "sap_input", "edid", "hdcp",
                      "video_input", "ip_input1", "model", "hw-omni-e4111"):
            self.assertNotIn(field, blob, f"{field} reached a restricted account")

    def test_no_device_address_is_disclosed(self):
        blob = json.dumps(self.project())
        for unit in UNITS:
            self.assertNotIn(unit["ip"], blob,
                             "a restricted account is given identities, not "
                             "addresses")


class ShowingTests(ProjectionTestBase):
    def test_a_permitted_source_on_a_permitted_display_is_named(self):
        front = self.project()["destinations"][0]
        self.assertEqual(front["showing"]["mode"], "source")
        self.assertEqual(front["showing"]["source_name"], "teacher-pc")

    def test_an_unpermitted_source_is_reported_without_being_named(self):
        """A display showing somebody else's camera says so, and no more.

        Naming it would disclose a device outside the account's world through
        the one field that has to describe what is on screen.
        """
        matrix = json.loads(json.dumps(MATRIX))
        matrix["routes"]["192.0.2.21"] = "192.0.2.19"     # the lab camera
        payload = omni_projection.user_state(matrix, UNITS, self.teacher)
        showing = payload["destinations"][0]["showing"]
        self.assertEqual(showing["mode"], "other")
        self.assertIsNone(showing["source_name"])
        self.assertIsNone(showing["source_id"])
        self.assertNotIn("lab-camera", json.dumps(payload))

    def test_an_unrouted_display_reports_nothing_shown(self):
        matrix = json.loads(json.dumps(MATRIX))
        matrix["routes"] = {}
        payload = omni_projection.user_state(matrix, UNITS, self.teacher)
        self.assertEqual(payload["destinations"][0]["showing"]["mode"], "none")

    def test_a_compositing_display_is_reported_as_multiview(self):
        matrix = json.loads(json.dumps(MATRIX))
        matrix["decoders"][0]["multiview_active"] = True
        matrix["decoders"][0]["multiview_name"] = "multiview2x2"
        payload = omni_projection.user_state(matrix, UNITS, self.teacher)
        self.assertEqual(payload["destinations"][0]["showing"]["mode"], "multiview")


class PermissionDisclosureTests(ProjectionTestBase):
    def test_only_this_accounts_grants_are_returned(self):
        payload = self.project()
        self.assertEqual(payload["features"], ["av"])
        self.assertEqual(payload["role"], "user")
        self.assertEqual(payload["username"], "teacher")

    def test_no_other_account_is_mentioned(self):
        blob = json.dumps(self.project())
        self.assertNotIn("admin", blob,
                         "the projection must not disclose other accounts")

    def test_no_credential_material_is_ever_returned(self):
        blob = json.dumps(self.project()) + json.dumps(self.project(self.admin))
        for forbidden in ("password", "password_hash", "scrypt", "token",
                          "csrf", "secret"):
            self.assertNotIn(forbidden, blob.lower())

    def test_the_session_summary_carries_no_permission_detail(self):
        summary = omni_projection.session_summary(self.teacher)
        self.assertEqual(set(summary), {"ok", "username", "role", "features"})
        self.assertNotIn("sources", json.dumps(summary))


class IdentityTests(ProjectionTestBase):
    def test_devices_are_identified_by_the_same_identity_permissions_use(self):
        ids = {s["id"] for s in self.project()["sources"]}
        self.assertEqual(ids, {omni_users._normalise_identity(TEACHER_PC),
                               omni_users._normalise_identity(DOC_CAMERA)})

    def test_a_device_with_no_stable_identity_is_omitted(self):
        """A permission naming it could not be written, so it is not offered."""
        units = UNITS + [{"ip": "192.0.2.50", "mac": "", "hostname": "mystery",
                          "role": "encoder"}]
        matrix = json.loads(json.dumps(MATRIX))
        matrix["encoders"].append({"ip": "192.0.2.50", "host": "mystery"})
        payload = omni_projection.user_state(matrix, units, self.admin)
        self.assertNotIn("mystery", json.dumps(payload))

    def test_permission_spelling_does_not_change_what_is_visible(self):
        self.store.set_permissions(self.teacher.id, Permissions(
            features=("av",),
            sources=("b8-98-b0-00-00-01",),          # dashes
            destinations=("b898b0000011",)))         # unseparated
        payload = self.project()
        self.assertEqual([s["name"] for s in payload["sources"]], ["teacher-pc"])
        self.assertEqual([d["name"] for d in payload["destinations"]],
                         ["front-display"])


class OfflineTests(ProjectionTestBase):
    def test_a_permitted_device_that_is_offline_is_still_listed(self):
        """Hiding it would say the assignment had been taken away."""
        matrix = json.loads(json.dumps(MATRIX))
        matrix["encoders"][0]["online"] = False
        payload = omni_projection.user_state(matrix, UNITS, self.teacher)
        teacher_pc = [s for s in payload["sources"] if s["name"] == "teacher-pc"]
        self.assertEqual(len(teacher_pc), 1)
        self.assertFalse(teacher_pc[0]["online"])


class NoDeviceIoTests(unittest.TestCase):
    """The projection reads what the host already has and nothing else."""

    def test_the_projection_module_performs_no_device_io(self):
        import ast
        tree = ast.parse((ROOT / "omni_projection.py").read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        for forbidden in ("socket", "requests", "websocket", "urllib",
                          "http", "omni_matrix_logic"):
            self.assertNotIn(forbidden, imported,
                             "the projection must never read a device; ten "
                             "browsers would become ten times the traffic")


if __name__ == "__main__":
    unittest.main()
