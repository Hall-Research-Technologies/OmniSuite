"""Remote User Multiview: assigned by an administrator, shown by the user, decided on the server.

The model under test:

  ADMIN enables the Multiview capability for an account
    -> decoders that can take Multiview are offered for assignment
    -> the saved layouts on them are listed
    -> the administrator assigns specific layouts (decoder identity + opaque layout id)
    -> the user sees and may show ONLY those layouts.

Everything here uses synthetic equipment (documentation address ranges, invented MACs) and a fake
catalogue and fake Show engine that record what they were asked. A refused request is proven by the
engine never having been called, which is the same as proving no device was written. Nothing reaches
a transport.
"""
from __future__ import annotations

import copy
import json
import os
import re
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("OMNI_SMOKE", "1")

from flask import Flask, jsonify                          # noqa: E402

import omni_access                                        # noqa: E402
import omni_aliases                                       # noqa: E402
import omni_gate                                          # noqa: E402
import omni_groups                                        # noqa: E402
import omni_locks                                         # noqa: E402
import omni_remote                                        # noqa: E402
import omni_remote_api                                    # noqa: E402
import omni_requests                                      # noqa: E402
import omni_tls                                           # noqa: E402
import omni_users                                         # noqa: E402
from omni_users import Permissions                        # noqa: E402

# Distinct synthetic values: nothing here is a lab address, MAC, hostname or layout id, and no two
# of them share a prefix, so an accidental aliasing between fields cannot pass.
DEC_A = "0a:1b:2c:d0:00:11"
DEC_B = "0a:1b:2c:d0:00:22"
DEC_C = "0a:1b:2c:d0:00:33"
SRC_1 = "0a:1b:2c:e0:00:01"
ADDR_A = "198.51.100.71"
ADDR_B = "198.51.100.72"
ADDR_C = "203.0.113.73"
ADDR_A_MOVED = "198.51.100.171"
L_A1 = "mv_a1a1a1a1a1a1a1a1"
L_A2 = "mv_a2a2a2a2a2a2a2a2"
L_B1 = "mv_b1b1b1b1b1b1b1b1"
L_C1 = "mv_c1c1c1c1c1c1c1c1"
KEY_A1 = DEC_A + "/" + L_A1
KEY_A2 = DEC_A + "/" + L_A2
KEY_B1 = DEC_B + "/" + L_B1
KEY_C1 = DEC_C + "/" + L_C1
IPV4 = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")


def fast_hashing(test):
    from werkzeug.security import generate_password_hash
    original = omni_users.hash_password
    omni_users.hash_password = lambda password: generate_password_hash(password, method="pbkdf2:sha256:1000")
    test.addCleanup(setattr, omni_users, "hash_password", original)


def layout(lid, friendly, shape="2x2", showing=False, showable=True, reason=""):
    return {"id": lid, "object": "multiview" + lid[3:9], "friendly_name": friendly,
            "name": friendly, "layout": shape, "layout_label": shape.upper(),
            "showing": showing, "showable": showable, "reason": reason}


def fixture_catalog():
    return [
        {"id": DEC_A, "hostname": "room-a-display", "status": "ok", "eligible": True, "note": "",
         "layouts": [layout(L_A1, "Quad view"), layout(L_A2, "Side by side", "side-by-side", showing=True)]},
        {"id": DEC_B, "hostname": "room-b-display", "status": "ok", "eligible": True, "note": "",
         "layouts": [layout(L_B1, "Picture in picture", "pip-top-left")]},
        {"id": DEC_C, "hostname": "room-c-display", "status": "ok", "eligible": True, "note": "",
         "layouts": [layout(L_C1, "Wall view")]},
    ]


def fixture_units(a_address=ADDR_A):
    return [{"ip": a_address, "mac": DEC_A.upper(), "hostname": "room-a-display", "model": "x-d4511", "role": "decoder"},
            {"ip": ADDR_B, "mac": DEC_B.upper(), "hostname": "room-b-display", "model": "x-d4511", "role": "decoder"},
            {"ip": ADDR_C, "mac": DEC_C.upper(), "hostname": "room-c-display", "model": "x-d4511", "role": "decoder"},
            {"ip": "198.51.100.90", "mac": SRC_1.upper(), "hostname": "camera-one", "model": "x-e4111", "role": "encoder"}]


class Harness(unittest.TestCase):
    def setUp(self):
        fast_hashing(self)
        self.dir = Path(tempfile.mkdtemp(prefix="omni-usermv-"))
        self.store = omni_users.UserStore(self.dir / "users.json", self.dir / "audit.json")
        self.aliases = omni_aliases.AliasStore(self.dir / "aliases.json")
        self.groups = omni_groups.GroupStore(self.dir / "groups.json", omni_users._normalise_identity)
        self.locks = omni_locks.ResourceLocks(timeout=5)
        self.catalog = fixture_catalog()
        self.units = fixture_units()
        self.shows = []
        self.catalog_reads = []
        # The window editor: what the decoder holds, the sources it is judged on, and what the
        # engine was asked. `design` is the layout's geometry; default is one window so the
        # older tests are unchanged.
        self.design = [{"cell": "main", "x": 0, "y": 0, "width": 1280, "height": 720}]
        self.window_reads = []
        self.window_answer = {"ok": True, "showing": False, "revision": "rev-1", "windows": []}
        self.source_rows = []
        self.composes = []
        self.compose_reply = lambda address, name, wanted, base: ({"ok": True, "status": "VERIFIED"}, 200)
        self.engine_reply = lambda address, name: ({"ok": True, "status": "VERIFIED"}, 200)

        app = Flask(__name__)
        self.app = app

        @app.errorhandler(omni_requests.BadRequestBody)
        def bad_body(error):
            # The same answer the application gives: a body that is not the shape
            # the endpoint needs is a 400, never a server error.
            return jsonify({"ok": False, "error": error.message}), 400

        listener = omni_remote.RemoteListener(app)

        def catalog(identities=None):
            self.catalog_reads.append(None if identities is None else sorted(identities))
            wanted = None if identities is None else {omni_users._normalise_identity(i) for i in identities}
            return copy.deepcopy([r for r in self.catalog if wanted is None or r["id"] in wanted])

        def geometry(shape):
            return {"width": 1920, "height": 1080, "windows": copy.deepcopy(self.design)}

        def windows(address, name):
            self.window_reads.append((address, name))
            return copy.deepcopy(self.window_answer)

        def compose(address, name, wanted, base):
            self.composes.append((address, name, dict(wanted), base))
            return self.compose_reply(address, name, wanted, base)

        def show(address, name):
            self.shows.append((address, name))
            return self.engine_reply(address, name)

        omni_gate.install(app, lambda: self.store, lambda: True)
        omni_remote_api.register(
            app, store=self.store, listener=listener, aliases=self.aliases, groups=self.groups,
            load_cache=lambda: copy.deepcopy(self.units), matrix_state=lambda: {},
            route_view=lambda: jsonify({}), remote_enabled=lambda: True,
            set_remote_state=lambda *a, **k: None, remote_config=lambda: {},
            usb_state=lambda: {}, usb_pair_view=lambda: jsonify({}), usb_unpair_view=lambda: jsonify({}),
            usb_icron_pair_view=lambda: jsonify({}), usb_icron_unpair_view=lambda: jsonify({}),
            multiview_catalog=catalog, multiview_geometry=geometry, multiview_show=show,
            multiview_windows=windows, multiview_sources=lambda: copy.deepcopy(self.source_rows),
            multiview_compose=compose,
            forget_observation=lambda key=None: None,
            locks=self.locks, tls=omni_tls, cert_paths=(self.dir / "c.pem", self.dir / "k.pem"),
            tls_context_for=lambda saved: (None, ""), certificate_state=lambda: {},
            source_preview=lambda address, fresh=False: (None, "", ""),
            credential_report=lambda: {}, unroute_view=lambda: jsonify({}),
            user_shell=lambda: "")
        self.grant(features=("multiview",), multiviews=(KEY_A1,))

    # ---- accounts and requests
    def grant(self, **fields):
        if self.store.find_by_username("tina") is None:
            self.store.create("tina", "tina-password-1", omni_users.ROLE_USER, Permissions(**fields))
        else:
            self.store.set_permissions(self.store.find_by_username("tina").id, Permissions(**fields))
        self.tina = self.store.find_by_username("tina")
        if not hasattr(self, "session"):
            self.session = self.store.open_session(self.tina.id)

    def admin_login(self):
        self.store.create("boss", "boss-password-1", omni_users.ROLE_ADMIN)
        self.boss = self.store.find_by_username("boss")
        self.boss_session = self.store.open_session(self.boss.id)
        return self.boss_session

    def req(self, method, path, session=None, payload=None):
        session = session or self.session
        client = self.app.test_client()
        client.set_cookie("omnisuite_session", session.token)
        kwargs = {"environ_base": {omni_remote.LISTENER_KEY: omni_remote.REMOTE},
                  "headers": {"X-OmniSuite-CSRF": session.csrf_token}}
        if method == "GET":
            return client.get(path, **kwargs)
        return client.post(path, json=payload, **kwargs)

    def state(self, session=None):
        return self.req("GET", "/api/user/multiview_state", session)

    def show(self, key, session=None, **extra):
        return self.req("POST", "/api/user/multiview_show", session, dict({"layout": key}, **extra))

    def listed(self, session=None):
        body = self.state(session).get_json()
        return sorted(l["id"] for d in body["displays"] for l in d["layouts"])


# =================================================================== the model
class PermissionModelTests(unittest.TestCase):
    def test_a_key_is_a_decoder_identity_and_an_opaque_layout_id(self):
        self.assertEqual(omni_users.multiview_key(DEC_A.upper(), L_A1), KEY_A1)
        self.assertEqual(omni_users.multiview_key(DEC_A.replace(":", "-"), L_A1), KEY_A1)

    def test_a_name_an_address_or_a_malformed_id_is_not_an_identity(self):
        for decoder, layout_id in (
                (DEC_A, "Quad view"), (DEC_A, "multiviewQuad"), (ADDR_A, L_A1),
                ("room-a-display", L_A1), (DEC_A, L_A1 + "\n"), (DEC_A, L_A1.upper()),
                (DEC_A, "mv_" + "a" * 15), (DEC_A, "mv_" + "a" * 17), (DEC_A, ""), ("", L_A1)):
            self.assertEqual(omni_users.multiview_key(decoder, layout_id), "", (decoder, layout_id))

    def test_a_trailing_newline_does_not_make_a_valid_id(self):
        # `$` would accept it; the id must be matched to the end of the string.
        self.assertFalse(omni_users.valid_multiview_id(L_A1 + "\n"))
        self.assertTrue(omni_users.valid_multiview_id(L_A1))

    def test_permissions_round_trip_and_hostile_values_are_dropped(self):
        perms = Permissions(features=("multiview", "av", "nonsense"),
                            multiviews=(KEY_A1, KEY_B1, DEC_A + "/multiview2x2", "x", {"decoder": DEC_C, "layout": L_C1},
                                        {"decoder": ADDR_A, "layout": L_A1}, None, 7))
        self.assertEqual(perms.features, frozenset({"multiview", "av"}))
        self.assertEqual(perms.multiviews, frozenset({KEY_A1, KEY_B1, KEY_C1}))
        again = Permissions.from_json(json.loads(json.dumps(perms.to_json())))
        self.assertEqual(again.multiviews, perms.multiviews)

    def test_an_assignment_that_named_a_layout_by_name_is_dropped_on_read(self):
        # The earlier build identified a layout by its object name; such a record
        # can no longer be tied to one saved layout, so it is not read.
        legacy = Permissions.from_json({"features": ["multiview"], "multiviews": [DEC_A + "/multiviewQuad"]})
        self.assertEqual(legacy.multiviews, frozenset())

    def test_operating_needs_the_exact_decoder_and_layout(self):
        perms = Permissions(features=("multiview",), multiviews=(KEY_A1,))
        self.assertTrue(perms.may_operate_multiview(DEC_A, L_A1))
        self.assertFalse(perms.may_operate_multiview(DEC_A, L_A2), "another layout on the same decoder")
        self.assertFalse(perms.may_operate_multiview(DEC_B, L_A1), "the same id on another decoder")
        self.assertFalse(perms.may_operate_multiview(ADDR_A, L_A1))

    def test_multiview_access_is_not_display_or_source_access(self):
        perms = Permissions(features=("multiview",), multiviews=(KEY_A1,))
        self.assertFalse(perms.may_control_destination(DEC_A))
        self.assertFalse(perms.may_use_source(SRC_1))
        other = Permissions(features=("av",), destinations=(DEC_A,), sources=(SRC_1,))
        self.assertFalse(other.may_operate_multiview(DEC_A, L_A1), "holding the display grants no layout")

    def test_clearing_a_decoder_is_blocked_by_an_assignment_naming_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = omni_users.UserStore(Path(tmp) / "u.json", Path(tmp) / "a.json")
            store.create("p", "p-password-123", omni_users.ROLE_USER, Permissions(features=("multiview",), multiviews=(KEY_A1,)))
            self.assertEqual(store.assigned_devices().get(DEC_A), ["p"])

    def test_forgetting_devices_or_a_group_keeps_the_assignments(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = omni_users.UserStore(Path(tmp) / "u.json", Path(tmp) / "a.json")
            account = store.create("p", "p-password-123", omni_users.ROLE_USER, Permissions(
                features=("multiview", "av"), destinations=(DEC_B,), groups=("grp_0123456789abcdef",), multiviews=(KEY_A1,)))
            store.forget_devices({DEC_B})
            store.forget_group("grp_0123456789abcdef")
            held = store.find_by_username("p").permissions
            self.assertEqual(held.multiviews, frozenset({KEY_A1}))
            self.assertEqual(held.destinations, frozenset())


class RouteTableTests(unittest.TestCase):
    def test_the_user_routes_need_the_capability_and_the_picker_is_admin_only(self):
        for path in ("/api/user/multiview_state", "/api/user/multiview_show"):
            self.assertEqual(omni_access.ROUTES[path], (omni_access.Access.USER, omni_access.FEATURE_MULTIVIEW), path)
        self.assertEqual(omni_access.ROUTES["/api/admin/multiviews"], (omni_access.Access.ADMIN, None))

    def test_the_page_cannot_be_given_a_way_to_edit_a_layout(self):
        user_routes = [p for p, (access, _f) in omni_access.ROUTES.items()
                       if access is omni_access.Access.USER and "multiview" in p]
        # A person may open an assigned layout, choose what feeds each window and show it. Nothing
        # that designs, renames, copies, installs or deletes a layout is on the user side.
        self.assertEqual(sorted(user_routes), ["/api/user/multiview_compose", "/api/user/multiview_layout",
                                               "/api/user/multiview_show", "/api/user/multiview_state"])
        for path in user_routes:
            for design in ("delete", "copy", "install", "layouts", "groups", "plan", "switch"):
                self.assertNotIn(design, path.rsplit("/", 1)[-1])
        for path in ("/api/multiview/apply", "/api/multiview/delete", "/api/multiview/switch",
                     "/api/multiview/show", "/api/multiview/state", "/api/multiview/groups/show"):
            access = omni_access.ROUTES.get(path, (None, None))[0]
            self.assertIn(access, (omni_access.Access.ADMIN, omni_access.Access.LOCAL_ONLY), path)


# ============================================================== what is listed
class StateTests(Harness):
    def test_only_the_assigned_layout_is_listed(self):
        self.assertEqual(self.listed(), [KEY_A1])

    def test_a_layout_on_the_same_decoder_that_is_not_assigned_is_absent(self):
        body = json.dumps(self.state().get_json())
        self.assertNotIn(L_A2, body)
        self.assertNotIn("Side by side", body)

    def test_the_capability_alone_lists_nothing(self):
        self.grant(features=("multiview",), multiviews=())
        self.assertEqual(self.state().get_json(), {"ok": True, "displays": []})

    def test_an_assignment_without_the_capability_is_refused(self):
        self.grant(features=("av",), multiviews=(KEY_A1,))
        response = self.state()
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json()["reason"], "missing_feature:multiview")

    def test_the_handler_refuses_a_missing_capability_when_the_gate_does_not(self):
        # A second line of defence the first never lets you reach: the route table
        # already demands the capability, so the handler's own check is only ever
        # exercised by taking the table's requirement out of the way for one request.
        self.grant(features=("av",), multiviews=(KEY_A1,))
        rule = "/api/user/multiview_state"
        held = omni_access.ROUTES[rule]
        self.assertEqual(held[1], omni_access.FEATURE_MULTIVIEW, "precondition: the gate demands it")
        omni_access.ROUTES[rule] = (omni_access.Access.AUTHENTICATED, None)
        try:
            response = self.state()
        finally:
            omni_access.ROUTES[rule] = held
        self.assertEqual(response.status_code, 403,
                         "the handler listed layouts for an account without the capability")
        self.assertEqual(response.get_json()["reason"], "missing_feature:multiview")

    def test_the_reply_carries_structure_and_state_and_no_address(self):
        text = json.dumps(self.state().get_json())
        item = self.state().get_json()["displays"][0]["layouts"][0]
        self.assertEqual(item["name"], "Quad view")
        self.assertIn("windows", item)
        self.assertTrue(item["available"])
        self.assertFalse(item["showing"])
        self.assertEqual(IPV4.findall(text), [])
        for forbidden in (ADDR_A, ADDR_B, SRC_1, "camera-one", "source"):
            self.assertNotIn(forbidden, text)

    def test_the_showing_marker_follows_the_decoder(self):
        self.grant(features=("multiview",), multiviews=(KEY_A2,))
        item = self.state().get_json()["displays"][0]["layouts"][0]
        self.assertTrue(item["showing"])

    def test_layouts_of_two_decoders_are_grouped_by_display(self):
        self.grant(features=("multiview",), multiviews=(KEY_A1, KEY_B1))
        displays = self.state().get_json()["displays"]
        self.assertEqual([d["id"] for d in displays], [DEC_A, DEC_B])
        self.assertEqual(sorted(l["id"] for d in displays for l in d["layouts"]), [KEY_A1, KEY_B1])

    def test_only_what_was_assigned_is_read(self):
        self.state()
        self.assertEqual(self.catalog_reads[-1], [DEC_A], "the whole site was read for one assignment")

    def test_a_decoder_without_multiview_is_absent_not_reported(self):
        self.catalog[0]["status"], self.catalog[0]["eligible"], self.catalog[0]["layouts"] = "unsupported", False, []
        self.assertEqual(self.state().get_json()["displays"], [])

    def test_a_decoder_that_does_not_answer_is_named_as_such(self):
        self.catalog[0]["status"], self.catalog[0]["eligible"], self.catalog[0]["layouts"] = "unreachable", False, []
        displays = self.state().get_json()["displays"]
        self.assertEqual(len(displays), 1)
        self.assertEqual(displays[0]["layouts"], [])
        self.assertIn("not answering", displays[0]["unavailable"])

    def test_a_layout_the_decoder_cannot_show_is_listed_unavailable_with_a_sentence(self):
        self.catalog[0]["layouts"][0].update({"showable": False, "reason": "Video Wall is enabled on 198.51.100.71."})
        item = self.state().get_json()["displays"][0]["layouts"][0]
        self.assertFalse(item["available"])
        self.assertTrue(item["reason"])

    def test_a_layout_deleted_from_the_decoder_disappears(self):
        self.catalog[0]["layouts"] = [self.catalog[0]["layouts"][1]]
        self.assertEqual(self.listed(), [])

    def test_the_preserved_assignment_is_live_again_when_the_same_layout_returns(self):
        saved = self.catalog[0]["layouts"]
        self.catalog[0]["layouts"] = []
        self.assertEqual(self.listed(), [])
        self.assertIn(KEY_A1, self.store.find_by_username("tina").permissions.multiviews, "the grant was discarded")
        self.catalog[0]["layouts"] = saved
        self.assertEqual(self.listed(), [KEY_A1])

    def test_a_layout_that_came_back_under_another_id_is_not_the_assigned_one(self):
        # Deleted and recreated: same friendly name, new identity. The old grant must not carry over.
        self.catalog[0]["layouts"][0]["id"] = "mv_f9f9f9f9f9f9f9f9"
        self.assertEqual(self.listed(), [])


# ================================================================= showing it
class ShowTests(Harness):
    def test_an_assigned_layout_is_shown_through_the_engine_with_server_resolved_arguments(self):
        response = self.show(KEY_A1)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"ok": True, "status": "VERIFIED"})
        self.assertEqual(self.shows, [(ADDR_A, "multiview" + L_A1[3:9])])

    def test_the_address_comes_from_the_current_record_when_the_decoder_has_moved(self):
        self.units = fixture_units(a_address=ADDR_A_MOVED)
        self.assertEqual(self.show(KEY_A1).status_code, 200)
        self.assertEqual(self.shows[-1][0], ADDR_A_MOVED)

    def test_a_layout_that_is_not_assigned_is_refused_and_nothing_is_called(self):
        for key in (KEY_A2, KEY_B1, KEY_C1):
            response = self.show(key)
            self.assertEqual(response.status_code, 403, key)
            self.assertEqual(response.get_json()["reason"], "multiview_not_assigned")
        self.assertEqual(self.shows, [])
        self.assertEqual(self.catalog_reads, [], "a refused request read the decoders")

    def test_the_same_layout_id_on_another_decoder_is_refused(self):
        self.assertEqual(self.show(DEC_B + "/" + L_A1).status_code, 403)
        self.assertEqual(self.shows, [])

    def test_malformed_keys_are_a_400_and_reach_nothing(self):
        for key in ("", "x", KEY_A1 + "\n", KEY_A1.upper(), DEC_A + "/Quad view",
                    ADDR_A + "/" + L_A1, "room-a-display/" + L_A1, DEC_A + "/", "/" + L_A1):
            response = self.show(key)
            self.assertIn(response.status_code, (400, 403), repr(key))
            self.assertNotEqual(response.status_code, 200, repr(key))
        self.assertEqual(self.shows, [])

    def test_a_non_string_key_is_refused_not_a_server_error(self):
        for value in (None, 7, ["a"], {"a": 1}, True):
            response = self.req("POST", "/api/user/multiview_show", None, {"layout": value})
            self.assertIn(response.status_code, (400, 403), repr(value))
        self.assertEqual(self.shows, [])

    def test_nothing_else_in_the_body_changes_what_happens(self):
        for extra in ({"name": "multiview" + L_A2[3:9]}, {"assignments": {"main": SRC_1}},
                      {"ok": True}, {"authorized": True, "is_admin": True, "role": "admin"}):
            response = self.show(KEY_A2, **extra)
            self.assertEqual(response.status_code, 403, extra)
        self.assertEqual(self.shows, [])

    def test_extra_fields_on_an_assigned_layout_are_ignored_not_obeyed(self):
        response = self.show(KEY_A1, name="multiview" + L_A2[3:9], assignments={"main": SRC_1})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.shows, [(ADDR_A, "multiview" + L_A1[3:9])], "the browser chose what was shown")

    def test_the_gate_still_refuses_a_body_naming_a_display_or_source_this_account_was_not_given(self):
        # Defence in depth, independent of the handler: even for an ASSIGNED layout,
        # a body that names a device the account has no grant for is refused.
        for extra in ({"decoder": DEC_B}, {"display": DEC_B}, {"source": SRC_1}):
            response = self.show(KEY_A1, **extra)
            self.assertEqual(response.status_code, 403, extra)
        self.assertEqual(self.shows, [])

    def test_surrounding_space_resolves_to_the_same_identity_and_widens_nothing(self):
        # The one shared normaliser folds space around a MAC; the key it produces is
        # the assigned key itself, so nothing is reachable that was not assigned.
        self.assertEqual(self.show(" " + KEY_A1).status_code, 200)
        self.assertEqual(self.show(" " + KEY_A2).status_code, 403)

    def test_the_capability_is_required(self):
        self.grant(features=("av",), multiviews=(KEY_A1,))
        self.assertEqual(self.show(KEY_A1).status_code, 403)
        self.assertEqual(self.shows, [])

    def test_holding_the_display_grants_no_layout(self):
        self.grant(features=("multiview", "av"), destinations=(DEC_A,), sources=(SRC_1,), multiviews=())
        self.assertEqual(self.show(KEY_A1).status_code, 403)
        self.assertEqual(self.shows, [])

    def test_holding_a_layout_grants_neither_the_display_nor_its_sources(self):
        self.assertEqual(self.show(KEY_A1).status_code, 200)
        route = self.req("POST", "/api/user/route", None, {"source": SRC_1, "destination": DEC_A})
        self.assertEqual(route.status_code, 403)
        sources = self.req("GET", "/api/user/matrix_state")
        self.assertEqual(sources.status_code, 403)

    def test_a_layout_containing_sources_the_user_cannot_route_may_still_be_shown(self):
        # Assigning the layout authorises that exact saved composition. The account
        # has no source grants at all, and the show goes ahead.
        self.assertEqual(self.tina.permissions.sources, frozenset())
        self.assertEqual(self.show(KEY_A1).status_code, 200)

    def test_assignment_revoked_between_two_requests_is_refused_on_the_second(self):
        self.assertEqual(self.show(KEY_A1).status_code, 200)
        self.grant(features=("multiview",), multiviews=())
        self.assertEqual(self.show(KEY_A1).status_code, 403)
        self.assertEqual(len(self.shows), 1)

    def test_assignment_revoked_while_waiting_for_the_lock_is_refused_before_the_engine(self):
        held = threading.Event()
        release = threading.Event()

        def hold():
            with self.locks.hold(self.locks.decoder(DEC_A)):
                held.set()
                release.wait(5)

        holder = threading.Thread(target=hold)
        holder.start()
        held.wait(5)
        results = []

        def attempt():
            results.append(self.show(KEY_A1).status_code)

        waiter = threading.Thread(target=attempt)
        waiter.start()
        time.sleep(0.3)
        self.grant(features=("multiview",), multiviews=())        # withdrawn during the wait
        release.set()
        waiter.join(10)
        holder.join(10)
        self.assertEqual(results, [403])
        self.assertEqual(self.shows, [], "the engine ran after the grant was withdrawn")

    def test_a_request_that_was_never_allowed_does_not_queue_for_the_lock(self):
        # Checked BEFORE the wait as well as after it, so a refusal does not sit in
        # the queue delaying a request that was allowed.
        held, release = threading.Event(), threading.Event()

        def hold():
            with self.locks.hold(self.locks.decoder(DEC_B)):
                held.set()
                release.wait(8)

        holder = threading.Thread(target=hold)
        holder.start()
        held.wait(5)
        try:
            started = time.time()
            response = self.show(KEY_B1)             # not assigned, and its display is busy
            elapsed = time.time() - started
        finally:
            release.set()
            holder.join(10)
        self.assertEqual(response.status_code, 403)
        self.assertLess(elapsed, 2.0, "a refused request waited for the display's lock")

    def test_a_disabled_account_is_refused_and_a_re_enabled_one_works_again(self):
        self.store.set_enabled(self.tina.id, False)
        self.assertIn(self.show(KEY_A1).status_code, (401, 403))
        self.assertEqual(self.shows, [])
        self.store.set_enabled(self.tina.id, True)
        session = self.store.open_session(self.tina.id)
        self.assertEqual(self.show(KEY_A1, session=session).status_code, 200)

    def test_a_deleted_account_is_refused_and_leaves_nothing_behind(self):
        self.store.delete(self.tina.id)
        self.assertIn(self.show(KEY_A1).status_code, (401, 403))
        self.assertEqual(self.shows, [])
        self.assertIsNone(self.store.find_by_username("tina"))

    def test_the_decoder_losing_multiview_refuses_and_keeps_the_assignment(self):
        self.catalog[0]["status"], self.catalog[0]["eligible"], self.catalog[0]["layouts"] = "unsupported", False, []
        response = self.show(KEY_A1)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.shows, [])
        self.assertIn(KEY_A1, self.store.find_by_username("tina").permissions.multiviews)

    def test_multiview_returning_makes_the_preserved_assignment_work_again(self):
        saved = copy.deepcopy(self.catalog[0])
        self.catalog[0]["status"], self.catalog[0]["eligible"], self.catalog[0]["layouts"] = "unsupported", False, []
        self.assertEqual(self.show(KEY_A1).status_code, 409)
        self.catalog[0] = saved
        self.assertEqual(self.show(KEY_A1).status_code, 200)
        self.assertEqual(len(self.shows), 1)

    def test_a_decoder_that_does_not_answer_is_a_502_not_a_refusal(self):
        self.catalog[0]["status"], self.catalog[0]["eligible"], self.catalog[0]["layouts"] = "unreachable", False, []
        response = self.show(KEY_A1)
        self.assertEqual(response.status_code, 502)
        self.assertEqual(self.shows, [])

    def test_a_deleted_layout_cannot_be_shown(self):
        self.catalog[0]["layouts"] = []
        response = self.show(KEY_A1)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.shows, [])

    def test_a_recreated_layout_does_not_inherit_the_old_assignment(self):
        self.catalog[0]["layouts"][0]["id"] = "mv_e5e5e5e5e5e5e5e5"
        self.assertEqual(self.show(KEY_A1).status_code, 404)
        self.assertEqual(self.show(DEC_A + "/mv_e5e5e5e5e5e5e5e5").status_code, 403)
        self.assertEqual(self.shows, [])

    def test_renaming_a_layout_does_not_end_the_assignment(self):
        self.catalog[0]["layouts"][0]["friendly_name"] = "Renamed"
        self.catalog[0]["layouts"][0]["name"] = "Renamed"
        self.assertEqual(self.show(KEY_A1).status_code, 200)
        self.assertEqual(self.state().get_json()["displays"][0]["layouts"][0]["name"], "Renamed")

    def test_a_layout_the_decoder_cannot_show_is_refused_with_a_scrubbed_sentence(self):
        self.catalog[0]["layouts"][0].update({"showable": False, "reason": "Video Wall is on at 198.51.100.71 (0a:1b:2c:d0:00:11)."})
        response = self.show(KEY_A1)
        self.assertEqual(response.status_code, 409)
        text = json.dumps(response.get_json())
        self.assertEqual(IPV4.findall(text), [])
        self.assertNotIn(DEC_A, text)
        self.assertEqual(self.shows, [])


class ResultTests(Harness):
    def test_a_failure_is_a_sentence_without_an_address_or_a_plan(self):
        self.engine_reply = lambda address, name: ({
            "ok": False, "status": "DEVICE UNREACHABLE",
            "error": "198.51.100.90 did not answer after 3 attempts. No device was changed.",
            "plan": {"windows": [{"source": {"ip": "198.51.100.90"}}]},
            "unreachable": [{"ip": "198.51.100.90", "hostname": "camera-one"}],
            "steps": [{"step": "Point ip_input2 at 233.252.0.9"}]}, 502)
        response = self.show(KEY_A1)
        self.assertEqual(response.status_code, 502)
        body = response.get_json()
        text = json.dumps(body)
        self.assertFalse(body["ok"])
        self.assertEqual(IPV4.findall(text), [])
        for leaked in ("camera-one", "plan", "steps", "ip_input"):
            self.assertNotIn(leaked, text)

    def test_only_the_named_fields_are_returned(self):
        self.engine_reply = lambda address, name: ({"ok": True, "status": "VERIFIED", "plan": {"x": 1},
                                                    "windows": [{"ip": "198.51.100.90"}], "audio": {"address": "233.252.0.1"}}, 200)
        body = self.show(KEY_A1).get_json()
        self.assertEqual(set(body), {"ok", "status"})

    def test_a_status_that_is_not_one_of_the_engines_words_is_not_passed_on(self):
        self.engine_reply = lambda address, name: ({"ok": False, "status": "see 198.51.100.90 for details",
                                                    "error": "no"}, 409)
        body = self.show(KEY_A1).get_json()
        self.assertEqual(body["status"], "FAILED")
        self.assertEqual(IPV4.findall(json.dumps(body)), [])

    def test_a_rolled_back_show_is_reported_as_a_failure_in_the_engines_words(self):
        self.engine_reply = lambda address, name: ({"ok": False, "status": "NO ACTIVE VIDEO — ROLLED BACK",
                                                    "error": "The window never showed its stream."}, 200)
        response = self.show(KEY_A1)
        self.assertEqual(response.status_code, 502)
        body = response.get_json()
        self.assertFalse(body["ok"])
        self.assertEqual(body["status"], "NO ACTIVE VIDEO — ROLLED BACK")

    def test_a_show_that_lit_all_but_one_window_says_so(self):
        self.engine_reply = lambda address, name: ({"ok": True, "status": "PARTIAL — WINDOW NOT LOCKED",
                                                    "unlocked_windows": [{"subframe": "main (1280x720)"}]}, 200)
        body = self.show(KEY_A1).get_json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["unlocked"], 1)
        self.assertIn("did not show a picture", body["message"])


# ================================================================= admin side
class AdminTests(Harness):
    def setUp(self):
        super().setUp()
        self.admin_login()

    def admin(self, method, path, payload=None):
        return self.req(method, path, self.boss_session, payload)

    def test_the_picker_is_for_administrators_only(self):
        self.assertEqual(self.req("GET", "/api/admin/multiviews").status_code, 403)
        self.assertEqual(self.admin("GET", "/api/admin/multiviews").status_code, 200)

    def test_the_picker_lists_decoders_and_their_layouts_by_name_with_opaque_keys(self):
        body = self.admin("GET", "/api/admin/multiviews").get_json()
        self.assertEqual([d["id"] for d in body["decoders"]], [DEC_A, DEC_B, DEC_C])
        first = body["decoders"][0]
        self.assertEqual(first["name"], "room-a-display")
        self.assertEqual([l["key"] for l in first["layouts"]], [KEY_A1, KEY_A2])
        self.assertEqual(IPV4.findall(json.dumps(body)), [])

    def test_an_ineligible_decoder_is_listed_but_flagged(self):
        self.catalog[1]["status"], self.catalog[1]["eligible"] = "unsupported", False
        body = self.admin("GET", "/api/admin/multiviews").get_json()
        row = next(d for d in body["decoders"] if d["id"] == DEC_B)
        self.assertFalse(row["eligible"])
        self.assertEqual(row["status"], "unsupported")

    def set_permissions(self, **fields):
        base = {"features": ["multiview"], "sources": [], "destinations": [], "groups": [], "multiviews": []}
        base.update(fields)
        return self.admin("POST", "/api/admin/users/" + self.tina.id,
                          {"action": "set_permissions", "permissions": base})

    def held(self):
        return sorted(self.store.find_by_username("tina").permissions.multiviews)

    def test_assigning_a_layout_is_saved_by_identity(self):
        response = self.set_permissions(multiviews=[KEY_A1, KEY_B1])
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(self.held(), [KEY_A1, KEY_B1])

    def test_a_layout_on_an_ineligible_decoder_cannot_be_newly_assigned(self):
        self.catalog[1]["status"], self.catalog[1]["eligible"] = "unsupported", False
        response = self.set_permissions(multiviews=[KEY_A1, KEY_B1])
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.held(), [KEY_A1], "a refused save changed the grant")

    def test_a_layout_that_does_not_exist_cannot_be_assigned(self):
        response = self.set_permissions(multiviews=[KEY_A1, DEC_A + "/mv_0000000000000000"])
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.held(), [KEY_A1])

    def test_a_decoder_never_seen_cannot_be_assigned(self):
        response = self.set_permissions(multiviews=[KEY_A1, "0a:1b:2c:d0:00:99/" + L_B1])
        self.assertEqual(response.status_code, 400)

    def test_an_existing_assignment_survives_an_unrelated_save_after_its_decoder_went_away(self):
        self.catalog[0]["status"], self.catalog[0]["eligible"], self.catalog[0]["layouts"] = "unsupported", False, []
        response = self.set_permissions(multiviews=[KEY_A1], features=["multiview", "av"], destinations=[DEC_B])
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(self.held(), [KEY_A1], "the preserved assignment was silently discarded")

    def test_an_assignment_to_an_unavailable_decoder_can_be_removed(self):
        self.catalog[0]["status"], self.catalog[0]["eligible"], self.catalog[0]["layouts"] = "unsupported", False, []
        self.assertEqual(self.set_permissions(multiviews=[]).status_code, 200)
        self.assertEqual(self.held(), [])

    def test_removing_an_assignment_takes_effect_on_the_users_next_request(self):
        self.assertEqual(self.listed(), [KEY_A1])
        self.set_permissions(multiviews=[])
        self.assertEqual(self.listed(), [])
        self.assertEqual(self.show(KEY_A1).status_code, 403)

    def test_adding_an_assignment_takes_effect_on_the_users_next_request_without_a_new_session(self):
        self.set_permissions(multiviews=[KEY_A1, KEY_B1])
        self.assertEqual(self.listed(), [KEY_A1, KEY_B1])
        self.assertEqual(self.show(KEY_B1).status_code, 200)

    def test_a_save_that_omits_assignments_does_not_pretend_to_keep_them(self):
        # The server replaces the permission set; a client must send it whole.
        response = self.admin("POST", "/api/admin/users/" + self.tina.id, {
            "action": "set_permissions",
            "permissions": {"features": ["multiview"], "sources": [], "destinations": [], "groups": []}})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.held(), [])

    def test_a_new_account_can_be_created_with_assignments_which_are_validated(self):
        ok = self.admin("POST", "/api/admin/users", {
            "username": "kim", "password": "kim-password-12", "role": "user",
            "permissions": {"features": ["multiview"], "multiviews": [KEY_B1]}})
        self.assertEqual(ok.status_code, 200, ok.get_json())
        bad = self.admin("POST", "/api/admin/users", {
            "username": "lee", "password": "lee-password-12", "role": "user",
            "permissions": {"features": ["multiview"], "multiviews": [DEC_B + "/mv_1111111111111111"]}})
        self.assertEqual(bad.status_code, 400)
        self.assertIsNone(self.store.find_by_username("lee"))

    def test_an_address_or_a_name_in_an_assignment_is_dropped_not_stored(self):
        self.set_permissions(multiviews=[KEY_A1, ADDR_A + "/" + L_A1, DEC_A + "/Quad view", "room-a-display/" + L_A1])
        self.assertEqual(self.held(), [KEY_A1])


class AdministratorTests(Harness):
    def test_the_administrators_own_view_lists_every_layout_and_shows_one(self):
        session = self.admin_login()
        listed = self.listed(session)
        self.assertEqual(sorted(listed), sorted([KEY_A1, KEY_A2, KEY_B1, KEY_C1]))


if __name__ == "__main__":
    unittest.main()
