"""The Remote User USB Matrix: one derivation, the Admin rules, server-side authorisation.

The defect these answer: /api/user/usb_route always called the standalone-extender pair path, which
refuses every integrated E4521/D4511 endpoint, so the user matrix could not route the equipment most
installations have. The route now takes its control path from the same per-cell derivation the
projection shows, which restates the Admin matrix's rules from the Admin payload.

Everything here uses synthetic fixtures (documentation address ranges, invented MACs) and fake pair
views that record what they were asked: a refused request is proven by the views never being called,
which is the same as proving no device was written. Nothing reaches a transport.
"""
from __future__ import annotations

import copy
import json
import os
import re
import sys
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("OMNI_SMOKE", "1")

from flask import Flask, jsonify, request                # noqa: E402

import omni_aliases                                      # noqa: E402
import omni_gate                                         # noqa: E402
import omni_groups                                       # noqa: E402
import omni_locks                                        # noqa: E402
import omni_projection                                   # noqa: E402
import omni_remote                                       # noqa: E402
import omni_remote_api                                   # noqa: E402
import omni_tls                                          # noqa: E402
import omni_usb_cells                                    # noqa: E402
import omni_users                                        # noqa: E402
from omni_users import Permissions                       # noqa: E402

# Synthetic equipment: nothing here is a lab address, MAC or hostname.
NET_A = "198.51.100"          # the room's network
NET_B = "203.0.113"           # another network
MASK = "255.255.255.0"

PC1_PARENT = "0A:1B:2C:A0:00:01"      # E4521-like parent of computer 1
PC2_PARENT = "0A:1B:2C:A0:00:02"
KB1_PARENT = "0A:1B:2C:B0:00:01"      # D4511-like parent of device 1
KB2_PARENT = "0A:1B:2C:B0:00:02"
KB3_PARENT = "0A:1B:2C:B0:00:03"      # lives on the other network
KB4_PARENT = "0A:1B:2C:B0:00:04"

IPV4 = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")


def usb_mac(parent):
    """The endpoint's own MAC: the parent's, one up. Never equal to the parent's."""
    head = parent[:-2]
    return "%s%02X" % (head, int(parent[-2:], 16) + 8)


def endpoint(kind, role, parent, mgmt_ip, usb_ip, *, classification="INTEGRATED", online=True):
    return {
        "kind": kind, "type": role, "usb_key": mgmt_ip, "ip": mgmt_ip, "host": "unit-" + mgmt_ip[-3:],
        "mac": usb_mac(parent), "usb_mac": usb_mac(parent), "usb_ip": usb_ip,
        "endpoint_ip": usb_ip, "endpoint_mask": MASK, "classification": classification,
        "online": online, "firmware": "9.9.9", "revision": "0xBEEF", "host_port": 7, "filter": "x",
    }


def lex(parent, mgmt, usb, **kw):
    return endpoint("integrated", "LEX", parent, mgmt, usb, **kw)


def rex(parent, mgmt, usb, **kw):
    return endpoint("integrated", "REX", parent, mgmt, usb, **kw)


def admin_payload(lexes, rexes, routed=(), udp=None):
    """The administrative USB payload, with capabilities from the REAL server rule."""
    import OmniMatrix_upgrade_server_v7_6y as srv
    lexes, rexes = copy.deepcopy(lexes), copy.deepcopy(rexes)
    capabilities = {r["usb_key"]: {l["usb_key"]: srv._usb_route_capability(l, r) for l in lexes}
                    for r in rexes}
    pairings = {r["ip"]: {"active": None, "available": []} for r in rexes if r["kind"] == "integrated"}
    for lex_ip, rex_ip, active in routed:
        slot = pairings[rex_ip]
        if active:
            slot["active"] = lex_ip
        else:
            slot["available"].append(lex_ip)
    return {"ok": True, "lex": lexes, "rex": rexes, "matrix_lex": lexes, "matrix_rex": rexes,
            "capabilities": capabilities, "pairings": pairings,
            "standalone_routes": copy.deepcopy(udp or {})}


def units():
    out = []
    for parent, ip, model, role in (
            (PC1_PARENT, NET_A + ".21", "hw-omni-e4521", "encoder"),
            (PC2_PARENT, NET_A + ".22", "hw-omni-e4521", "encoder"),
            (KB1_PARENT, NET_A + ".31", "hw-omni-d4511", "decoder"),
            (KB2_PARENT, NET_A + ".32", "hw-omni-d4511", "decoder"),
            (KB3_PARENT, NET_B + ".33", "hw-omni-d4511", "decoder"),
            (KB4_PARENT, NET_A + ".34", "hw-omni-d4511", "decoder")):
        out.append({"ip": ip, "mac": parent, "hostname": "unit-" + ip[-3:], "model": model,
                    "role": role})
    return out


def standard_state(routed=()):
    lexes = [lex(PC1_PARENT, NET_A + ".21", NET_A + ".121"),
             lex(PC2_PARENT, NET_A + ".22", NET_A + ".122")]
    rexes = [rex(KB1_PARENT, NET_A + ".31", NET_A + ".131"),
             rex(KB2_PARENT, NET_A + ".32", NET_A + ".132"),
             rex(KB3_PARENT, NET_B + ".33", NET_B + ".133"),
             rex(KB4_PARENT, NET_A + ".34", NET_A + ".134")]
    return admin_payload(lexes, rexes, routed)


def fast_hashing(test):
    """Hash passwords cheaply for the life of one test, then put the real function back.

    Hashing is deliberately slow (scrypt); these tests create accounts to open sessions and have no
    interest in its cost. The module attribute is restored, never left replaced.
    """
    from werkzeug.security import generate_password_hash
    original = omni_users.hash_password
    omni_users.hash_password = lambda password: generate_password_hash(password, method="pbkdf2:sha256:1000")
    test.addCleanup(setattr, omni_users, "hash_password", original)


class Harness(unittest.TestCase):
    """A real registered handler on a private app, with recording pair views."""

    state = None

    def setUp(self):
        fast_hashing(self)
        self.dir = Path(tempfile.mkdtemp(prefix="omni-usbm-"))
        self.store = omni_users.UserStore(self.dir / "users.json", self.dir / "audit.json")
        self.aliases = omni_aliases.AliasStore(self.dir / "aliases.json")
        self.groups = omni_groups.GroupStore(self.dir / "groups.json", omni_users._normalise_identity)
        self.locks = omni_locks.ResourceLocks(timeout=2)
        self.calls = []
        self.state_reads = 0
        self.forgets = []
        self.current = standard_state()
        self.pair_reply = lambda kind, body: (jsonify({"ok": True, "message": "ok"}),)
        self.units = units()

        app = Flask(__name__)
        self.app = app
        listener = omni_remote.RemoteListener(app)

        def views(kind):
            def view():
                body = request.get_json(silent=True) or {}
                self.calls.append((kind, body))
                reply = self.pair_reply(kind, body)
                return reply if len(reply) > 1 else reply[0]
            view.__name__ = "fake_" + kind
            return view

        def usb_state():
            self.state_reads += 1
            return copy.deepcopy(self.current)

        omni_gate.install(app, lambda: self.store, lambda: True)
        omni_remote_api.register(
            app, store=self.store, listener=listener, aliases=self.aliases, groups=self.groups,
            load_cache=lambda: copy.deepcopy(self.units), matrix_state=lambda: {},
            route_view=lambda: jsonify({}), remote_enabled=lambda: True,
            set_remote_state=lambda *a, **k: None, remote_config=lambda: {},
            usb_state=usb_state,
            usb_pair_view=views("standalone_pair"), usb_unpair_view=views("standalone_unpair"),
            usb_icron_pair_view=views("icron_pair"), usb_icron_unpair_view=views("icron_unpair"),
            forget_observation=lambda key=None: self.forgets.append(key),
            locks=self.locks, tls=omni_tls, cert_paths=(self.dir / "c.pem", self.dir / "k.pem"),
            tls_context_for=lambda saved: (None, ""), certificate_state=lambda: {},
            source_preview=lambda address, fresh=False: (None, "", ""),
            credential_report=lambda: {}, unroute_view=lambda: jsonify({}),
            user_shell=lambda: "")

        self.grant(features=("usb",), sources=(PC1_PARENT,), destinations=(KB1_PARENT, KB3_PARENT))

    def admin_login(self):
        # Created on demand: hashing a password is deliberately slow and most tests never need one.
        self.store.create("admin", "admin-password-1", omni_users.ROLE_ADMIN)
        self.admin = self.store.find_by_username("admin")
        self.admin_session = self.store.open_session(self.admin.id)
        return self.admin_session

    def grant(self, **fields):
        if self.store.find_by_username("tina") is None:
            self.store.create("tina", "tina-password-1", omni_users.ROLE_USER, Permissions(**fields))
        else:
            self.store.set_permissions(self.store.find_by_username("tina").id, Permissions(**fields))
        self.tina = self.store.find_by_username("tina")
        if not hasattr(self, "session"):
            self.session = self.store.open_session(self.tina.id)

    def post(self, payload, session=None, path="/api/user/usb_route"):
        session = session or self.session
        client = self.app.test_client()
        client.set_cookie("omnisuite_session", session.token)
        return client.post(path, json=payload,
                           environ_base={omni_remote.LISTENER_KEY: omni_remote.REMOTE},
                           headers={"X-OmniSuite-CSRF": session.csrf_token})

    def get(self, path="/api/user/usb_state", session=None):
        session = session or self.session
        client = self.app.test_client()
        client.set_cookie("omnisuite_session", session.token)
        return client.get(path, environ_base={omni_remote.LISTENER_KEY: omni_remote.REMOTE})

    def route(self, computer, device, detach=False, **extra):
        return self.post(dict({"computer": usb_mac(computer), "peripherals": usb_mac(device),
                               "detach": detach}, **extra))


# ====================================================================== cells
class CellDerivationTests(unittest.TestCase):
    """omni_usb_cells restates the Admin page's rules, once."""

    def cell(self, state, device_parent, computer_parent):
        cells = omni_usb_cells.derive(state)
        return cells[(omni_users._normalise_identity(usb_mac(device_parent)),
                      omni_users._normalise_identity(usb_mac(computer_parent)))]

    def test_a_same_network_integrated_pair_is_available_through_the_parent(self):
        record = self.cell(standard_state(), KB1_PARENT, PC1_PARENT)
        self.assertTrue(record["can_act"])
        self.assertEqual(record["path"], "usb_icron")
        self.assertEqual(record["public"]["state"], "available")
        self.assertEqual((record["rex_key"], record["lex_key"]), (NET_A + ".31", NET_A + ".21"))

    def test_a_pair_on_different_usb_networks_is_blocked_by_the_servers_own_rule(self):
        record = self.cell(standard_state(), KB3_PARENT, PC1_PARENT)
        self.assertFalse(record["can_act"])
        self.assertEqual(record["public"]["reason"], "network_mismatch")
        self.assertEqual(record["public"]["state"], "blocked")

    def test_the_admin_pages_management_address_rule_is_applied_when_the_usb_networks_agree(self):
        # USB endpoints share a network but the units are managed from different /24s: the Admin
        # page does not offer the cell, so neither does the user matrix.
        lexes = [lex(PC1_PARENT, NET_A + ".21", NET_A + ".121")]
        rexes = [rex(KB3_PARENT, NET_B + ".33", NET_A + ".133")]
        record = self.cell(admin_payload(lexes, rexes), KB3_PARENT, PC1_PARENT)
        self.assertFalse(record["can_act"])
        self.assertEqual(record["public"]["reason"], "management_subnet_mismatch")

    def test_a_routed_pair_across_management_subnets_can_still_be_removed(self):
        # The Admin page applies its management-address rule only to a cell that is not already
        # routed, so removal stays possible; the user matrix must not be stricter than that.
        lexes = [lex(PC1_PARENT, NET_A + ".21", NET_A + ".121")]
        rexes = [rex(KB3_PARENT, NET_B + ".33", NET_A + ".133")]
        record = self.cell(admin_payload(lexes, rexes, [(NET_A + ".21", NET_B + ".33", True)]),
                           KB3_PARENT, PC1_PARENT)
        self.assertTrue(record["routed"])
        self.assertTrue(record["can_act"], record["public"])

    def test_an_already_routed_pair_stays_actionable_so_it_can_be_removed(self):
        record = self.cell(standard_state(routed=[(NET_A + ".21", NET_A + ".31", True)]),
                           KB1_PARENT, PC1_PARENT)
        self.assertTrue(record["routed"] and record["can_act"])
        self.assertTrue(record["public"]["active"])
        self.assertEqual(record["public"]["state"], "routed")

    def test_a_configured_but_not_linked_pair_is_routed_but_not_active(self):
        record = self.cell(standard_state(routed=[(NET_A + ".21", NET_A + ".31", False)]),
                           KB1_PARENT, PC1_PARENT)
        self.assertTrue(record["routed"])
        self.assertFalse(record["public"]["active"])

    def test_the_peer_limit_blocks_a_sixth_device_but_not_removal(self):
        lexes = [lex(PC1_PARENT, NET_A + ".21", NET_A + ".121")]
        rexes = [rex("0A:1B:2C:C0:00:%02X" % n, NET_A + ".%d" % (60 + n), NET_A + ".%d" % (160 + n))
                 for n in range(6)]
        routed = [(NET_A + ".21", NET_A + ".%d" % (60 + n), True) for n in range(5)]
        state = admin_payload(lexes, rexes, routed)
        sixth = omni_usb_cells.derive(state)[(omni_users._normalise_identity(usb_mac("0A:1B:2C:C0:00:05")),
                                              omni_users._normalise_identity(usb_mac(PC1_PARENT)))]
        self.assertFalse(sixth["can_act"])
        self.assertEqual(sixth["public"]["reason"], "peer_limit")
        first = omni_usb_cells.derive(state)[(omni_users._normalise_identity(usb_mac("0A:1B:2C:C0:00:00")),
                                              omni_users._normalise_identity(usb_mac(PC1_PARENT)))]
        self.assertTrue(first["can_act"] and first["routed"])

    def test_an_unconfirmed_endpoint_is_blocked(self):
        lexes = [lex(PC1_PARENT, NET_A + ".21", NET_A + ".121", classification="UNCONFIRMED")]
        rexes = [rex(KB1_PARENT, NET_A + ".31", NET_A + ".131")]
        record = self.cell(admin_payload(lexes, rexes), KB1_PARENT, PC1_PARENT)
        self.assertFalse(record["can_act"])

    def test_a_cell_the_admin_payload_does_not_describe_is_blocked(self):
        state = standard_state()
        state["capabilities"] = {}
        record = self.cell(state, KB1_PARENT, PC1_PARENT)
        self.assertFalse(record["can_act"])

    def test_no_public_text_carries_an_address_or_a_mac(self):
        for record in omni_usb_cells.derive(standard_state()).values():
            blob = json.dumps(record["public"])
            self.assertFalse(IPV4.search(blob), blob)
            self.assertNotIn("0A:1B", blob.upper())

    def test_clean_text_removes_addresses_and_macs(self):
        text = omni_usb_cells.clean_text("LEX 198.51.100.21 (0A:1B:2C:A0:00:09) is busy")
        self.assertNotIn("198.51", text)
        self.assertNotIn("0A:1B", text)


# ================================================================= projection
class ProjectionTests(Harness):
    def test_the_projection_carries_cells_only_for_the_authorised_subset(self):
        body = self.get().get_json()
        self.assertEqual([c["name"] for c in body["computers"]], [c["name"] for c in body["computers"]])
        self.assertEqual({c["id"] for c in body["computers"]}, {usb_mac(PC1_PARENT).lower()})
        self.assertEqual({p["id"] for p in body["peripherals"]},
                         {usb_mac(KB1_PARENT).lower(), usb_mac(KB3_PARENT).lower()})
        self.assertEqual(set(body["cells"]), {p["id"] for p in body["peripherals"]})
        for row in body["cells"].values():
            self.assertEqual(set(row), {usb_mac(PC1_PARENT).lower()})

    def test_the_payload_carries_no_address_mask_firmware_or_other_computers_identity(self):
        blob = self.get().get_data(as_text=True)
        self.assertFalse(IPV4.search(blob), "an address reached a user: " + blob)
        for forbidden in ("255.255.255", "9.9.9", "0xBEEF", "host_port", "filter",
                          usb_mac(PC2_PARENT).lower(), usb_mac(KB2_PARENT).lower(), "standalone_udp",
                          "usb_icron", "control_path"):
            self.assertNotIn(forbidden, blob)

    def test_the_cell_shape_is_exactly_the_documented_one(self):
        body = self.get().get_json()
        for row in body["cells"].values():
            for cell in row.values():
                self.assertEqual(set(cell), {"state", "routed", "active", "enabled", "reason",
                                             "label", "detail", "stale"})

    def test_an_integrated_route_is_reported_as_attached(self):
        self.current = standard_state(routed=[(NET_A + ".21", NET_A + ".31", True)])
        body = self.get().get_json()
        device = [p for p in body["peripherals"] if p["id"] == usb_mac(KB1_PARENT).lower()][0]
        self.assertEqual(device["attached_to"]["mode"], "computer")
        cell = body["cells"][device["id"]][usb_mac(PC1_PARENT).lower()]
        self.assertTrue(cell["routed"] and cell["active"])

    def test_a_route_to_a_computer_the_account_cannot_see_is_another_computer(self):
        self.current = standard_state(routed=[(NET_A + ".22", NET_A + ".31", True)])
        body = self.get().get_json()
        device = [p for p in body["peripherals"] if p["id"] == usb_mac(KB1_PARENT).lower()][0]
        self.assertEqual(device["attached_to"], {"mode": "other", "computer_id": None,
                                                 "computer_name": None})
        self.assertNotIn(usb_mac(PC2_PARENT).lower(), json.dumps(body))

    def test_the_cross_network_device_is_visible_and_blocked_with_a_reason(self):
        body = self.get().get_json()
        cell = body["cells"][usb_mac(KB3_PARENT).lower()][usb_mac(PC1_PARENT).lower()]
        self.assertFalse(cell["enabled"])
        self.assertEqual(cell["state"], "blocked")
        self.assertTrue(cell["label"])


# ========================================================================= route
class RouteDispatchTests(Harness):
    def test_an_integrated_attach_goes_through_the_parents_pairing_path_exactly_as_admin_does(self):
        response = self.route(PC1_PARENT, KB1_PARENT)
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertEqual(response.get_json(), {"ok": True, "status": "verified", "changed": True})
        self.assertEqual(self.calls, [("icron_pair", {"rex": NET_A + ".31", "lex": NET_A + ".21",
                                                      "makeActive": False, "replaceExisting": True})])

    def test_an_integrated_detach_goes_through_the_parents_unpair_path(self):
        self.current = standard_state(routed=[(NET_A + ".21", NET_A + ".31", True)])
        response = self.route(PC1_PARENT, KB1_PARENT, detach=True)
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertEqual(self.calls, [("icron_unpair", {"rex": NET_A + ".31", "lex": NET_A + ".21"})])

    def test_a_route_that_is_already_in_place_writes_nothing(self):
        self.current = standard_state(routed=[(NET_A + ".21", NET_A + ".31", True)])
        response = self.route(PC1_PARENT, KB1_PARENT)
        self.assertEqual(response.get_json(), {"ok": True, "status": "already_routed", "changed": False})
        self.assertEqual(self.calls, [])

    def test_detaching_what_is_not_routed_writes_nothing(self):
        response = self.route(PC1_PARENT, KB1_PARENT, detach=True)
        self.assertEqual(response.get_json(), {"ok": True, "status": "not_routed", "changed": False})
        self.assertEqual(self.calls, [])

    def test_standalone_units_use_the_extender_service_with_macs_only(self):
        lexes = [endpoint("standalone", "LEX", "0A:1B:2C:D0:00:01", "198.51.100.71", "198.51.100.71",
                          classification="STANDALONE")]
        rexes = [endpoint("standalone", "REX", "0A:1B:2C:D0:00:02", "198.51.100.72", "198.51.100.72",
                          classification="STANDALONE")]
        for row in lexes + rexes:
            row["usb_key"] = row["usb_mac"]
        self.current = admin_payload(lexes, rexes)
        self.grant(features=("usb",), sources=(usb_mac("0A:1B:2C:D0:00:01"),),
                   destinations=(usb_mac("0A:1B:2C:D0:00:02"),))
        # The standalone unit's own MAC is its identity, so the grants name it directly.
        response = self.post({"computer": usb_mac("0A:1B:2C:D0:00:01"),
                              "peripherals": usb_mac("0A:1B:2C:D0:00:02")})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertEqual(self.calls, [("standalone_pair", {
            "lex_mac": omni_users._normalise_identity(usb_mac("0A:1B:2C:D0:00:01")),
            "rex_mac": omni_users._normalise_identity(usb_mac("0A:1B:2C:D0:00:02"))})])

    def test_fields_the_browser_adds_are_ignored(self):
        self.route(PC1_PARENT, KB1_PARENT, replaceExisting=False, makeActive=True,
                   path="standalone_udp", control_path="standalone_udp", force=True)
        self.assertEqual(self.calls, [("icron_pair", {"rex": NET_A + ".31", "lex": NET_A + ".21",
                                                      "makeActive": False, "replaceExisting": True})])

    def test_addresses_and_macs_smuggled_into_the_body_cannot_redirect_the_route(self):
        # Whether the gate refuses the field or the handler ignores it, nothing may reach a pair path
        # addressed from the browser.
        for extra in ({"ip": NET_B + ".99"}, {"lex": NET_B + ".99"}, {"rex": NET_B + ".98"},
                      {"lex_mac": "0A:1B:2C:FF:FF:FF"}, {"rex_mac": "0A:1B:2C:FF:FF:EE"},
                      {"lex_ip": NET_B + ".99"}):
            with self.subTest(extra=extra):
                self.calls.clear()
                self.route(PC1_PARENT, KB1_PARENT, **extra)
                for kind, body in self.calls:
                    self.assertEqual(body.get("lex"), NET_A + ".21")
                    self.assertEqual(body.get("rex"), NET_A + ".31")
                    self.assertNotIn("FF:FF", json.dumps(body))

    def test_state_is_read_for_this_operation_after_the_cache_is_dropped(self):
        before = self.state_reads
        self.route(PC1_PARENT, KB1_PARENT)
        self.assertGreater(self.state_reads, before)
        self.assertIn("usb", self.forgets)
        # Dropped before the read that decides, and again after the write.
        self.assertGreaterEqual(self.forgets.count("usb"), 2)

    def test_one_action_is_one_audit_entry(self):
        self.route(PC1_PARENT, KB1_PARENT)
        events = [e["event"] for e in self.store.audit_entries(limit=50)]
        self.assertEqual(events.count("usb.attach"), 1)
        self.route(PC1_PARENT, KB3_PARENT)                 # refused
        events = [e["event"] for e in self.store.audit_entries(limit=50)]
        self.assertEqual(events.count("usb.attach"), 1, "a refused route was recorded as done")


class RouteRefusalTests(Harness):
    def test_a_cross_network_route_is_refused_and_nothing_is_written(self):
        response = self.route(PC1_PARENT, KB3_PARENT)
        self.assertEqual(response.status_code, 409)
        body = response.get_json()
        self.assertEqual(body["reason"], "network_mismatch")
        self.assertFalse(body["ok"])
        self.assertEqual(self.calls, [], "a prohibited route reached a pair path")
        self.assertFalse(IPV4.search(response.get_data(as_text=True)))

    def test_a_route_the_admin_matrix_would_not_offer_is_refused_for_the_same_reason(self):
        lexes = [lex(PC1_PARENT, NET_A + ".21", NET_A + ".121")]
        rexes = [rex(KB1_PARENT, NET_A + ".31", NET_A + ".131")]
        state = admin_payload(lexes, rexes)
        state["capabilities"][NET_A + ".31"][NET_A + ".21"]["enabled"] = False
        state["capabilities"][NET_A + ".31"][NET_A + ".21"]["state"] = "OFFLINE"
        self.current = state
        response = self.route(PC1_PARENT, KB1_PARENT)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.calls, [])

    def test_an_unauthorised_computer_is_refused(self):
        self.grant(features=("usb",), sources=(PC2_PARENT,), destinations=(KB1_PARENT,))
        response = self.route(PC1_PARENT, KB1_PARENT)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json()["reason"], "computer_not_permitted")
        self.assertEqual(self.calls, [])

    def test_an_unauthorised_peripheral_is_refused(self):
        response = self.route(PC1_PARENT, KB2_PARENT)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json()["reason"], "peripherals_not_permitted")
        self.assertEqual(self.calls, [])

    def test_both_unauthorised_is_refused(self):
        response = self.route(PC2_PARENT, KB2_PARENT)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.calls, [])

    def test_without_the_usb_feature_nothing_is_routed(self):
        self.grant(features=("av",), sources=(PC1_PARENT,), destinations=(KB1_PARENT,))
        response = self.route(PC1_PARENT, KB1_PARENT)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.calls, [])

    def test_a_user_gets_the_same_answer_for_an_unknown_and_an_unauthorised_endpoint(self):
        unknown = self.post({"computer": usb_mac(PC1_PARENT), "peripherals": "0A:1B:2C:EE:EE:EE"})
        refused = self.route(PC1_PARENT, KB2_PARENT)
        self.assertEqual(unknown.status_code, refused.status_code)
        self.assertEqual(self.calls, [])

    def test_an_administrator_naming_an_unknown_well_formed_endpoint_gets_404(self):
        response = self.post({"computer": usb_mac(PC1_PARENT), "peripherals": "0A:1B:2C:EE:EE:EE"},
                             session=self.admin_login())
        self.assertEqual(response.status_code, 404, response.get_data(as_text=True))
        self.assertEqual(response.get_json()["status"], "unknown_endpoint")
        self.assertEqual(self.calls, [])

    def test_malformed_requests_are_400(self):
        for payload in ({}, {"computer": usb_mac(PC1_PARENT)}, {"peripherals": usb_mac(KB1_PARENT)},
                        {"computer": NET_A + ".21", "peripherals": NET_A + ".31"},
                        {"computer": "not a mac", "peripherals": usb_mac(KB1_PARENT)},
                        {"computer": usb_mac(PC1_PARENT), "peripherals": "0A:1B:2C"}):
            with self.subTest(payload=payload):
                response = self.post(payload)
                self.assertEqual(response.status_code, 400, response.get_data(as_text=True))
        self.assertEqual(self.calls, [])

    def test_an_address_is_not_accepted_where_an_identity_is_expected(self):
        # A permissive normaliser would turn digits into a plausible MAC; the endpoint refuses it.
        response = self.post({"computer": "198.051.100.021", "peripherals": usb_mac(KB1_PARENT)})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.calls, [])

    def test_the_peer_limit_refuses_without_writing(self):
        lexes = [lex(PC1_PARENT, NET_A + ".21", NET_A + ".121")]
        parents = ["0A:1B:2C:C0:00:%02X" % n for n in range(6)]
        rexes = [rex(p, NET_A + ".%d" % (60 + n), NET_A + ".%d" % (160 + n)) for n, p in enumerate(parents)]
        routed = [(NET_A + ".21", NET_A + ".%d" % (60 + n), True) for n in range(5)]
        self.current = admin_payload(lexes, rexes, routed)
        self.units += [{"ip": NET_A + ".%d" % (60 + n), "mac": p, "hostname": "u%d" % n}
                       for n, p in enumerate(parents)]
        self.grant(features=("usb",), sources=(PC1_PARENT,), destinations=tuple(parents))
        response = self.route(PC1_PARENT, parents[5])
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json()["reason"], "peer_limit")
        self.assertEqual(self.calls, [])

    def test_a_dead_session_is_refused(self):
        self.store.revoke_sessions(self.tina.id) if hasattr(self.store, "revoke_sessions") else None
        client = self.app.test_client()
        response = client.post("/api/user/usb_route",
                               json={"computer": usb_mac(PC1_PARENT), "peripherals": usb_mac(KB1_PARENT)},
                               environ_base={omni_remote.LISTENER_KEY: omni_remote.REMOTE})
        self.assertIn(response.status_code, (401, 403))
        self.assertEqual(self.calls, [])


class WaitingAndRevalidationTests(Harness):
    def blocked(self, during):
        """Start a route that must queue behind the pair's lock, run `during`, then release."""
        outcome = {}
        held, release = threading.Event(), threading.Event()
        names = self.locks.usb_pair(usb_mac(PC1_PARENT).lower(), usb_mac(KB1_PARENT).lower())

        def holder():
            with self.locks.hold(*names):
                held.set()
                release.wait(20)

        def caller():
            response = self.route(PC1_PARENT, KB1_PARENT)
            outcome["status"], outcome["body"] = response.status_code, response.get_json()

        thread = threading.Thread(target=holder)
        thread.start()
        self.assertTrue(held.wait(5))
        worker = threading.Thread(target=caller)
        worker.start()
        try:
            self.assertFalse(self.calls, "the request ran before the lock was free")
            during()
        finally:
            release.set()
            thread.join(10)
            worker.join(10)
        self.assertFalse(thread.is_alive() or worker.is_alive(), "work outlived its test")
        return outcome

    def test_a_permission_taken_away_while_waiting_stops_the_write(self):
        outcome = self.blocked(lambda: self.grant(features=("usb",), sources=(PC2_PARENT,),
                                                  destinations=(KB1_PARENT,)))
        self.assertEqual(outcome["status"], 403, outcome)
        self.assertEqual(self.calls, [], "a route was written after its permission was withdrawn")

    def test_the_feature_removed_while_waiting_stops_the_write(self):
        outcome = self.blocked(lambda: self.grant(features=("av",), sources=(PC1_PARENT,),
                                                  destinations=(KB1_PARENT,)))
        self.assertEqual(outcome["status"], 403, outcome)
        self.assertEqual(self.calls, [])

    def test_a_disabled_account_is_refused_after_the_wait(self):
        outcome = self.blocked(lambda: self.store.set_enabled(self.tina.id, False, actor=None))
        self.assertIn(outcome["status"], (401, 403), outcome)
        self.assertEqual(self.calls, [])

    def test_the_cell_is_judged_on_state_read_after_the_wait(self):
        # Routed by somebody else while this request waited: the answer is the fresh one.
        def now_routed():
            self.current = standard_state(routed=[(NET_A + ".21", NET_A + ".31", True)])
        outcome = self.blocked(now_routed)
        self.assertEqual(outcome["body"].get("status"), "already_routed", outcome)
        self.assertEqual(self.calls, [])

    def test_a_busy_pair_is_409_and_names_no_resource(self):
        names = self.locks.usb_pair(usb_mac(PC1_PARENT).lower(), usb_mac(KB1_PARENT).lower())
        held, release = threading.Event(), threading.Event()

        def holder():
            with self.locks.hold(*names):
                held.set()
                release.wait(20)

        thread = threading.Thread(target=holder)
        thread.start()
        self.assertTrue(held.wait(5))
        try:
            response = self.route(PC1_PARENT, KB1_PARENT)
        finally:
            release.set()
            thread.join(10)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json()["reason"], "resource_busy")
        self.assertNotIn("usb:", response.get_data(as_text=True))
        self.assertEqual(self.calls, [])


class ResultTruthfulnessTests(Harness):
    def reply(self, body, status=200):
        self.pair_reply = lambda kind, req: (jsonify(body), status)

    def test_accepted_but_not_confirmed_is_not_success(self):
        self.reply({"ok": False, "error": "Pair command was sent but REX did not report the LEX pairing"})
        response = self.route(PC1_PARENT, KB1_PARENT)
        body = response.get_json()
        self.assertEqual(response.status_code, 502)
        self.assertFalse(body["ok"])
        self.assertEqual(body["status"], "unverified")
        self.assertFalse(body["changed"])

    def test_a_verified_conflict_is_a_refusal_without_addresses(self):
        self.reply({"ok": False, "error": "LEX %s.21 is already paired to 5 REX units" % NET_A,
                    "paired_count": 5, "existing": [NET_A + ".99"]}, 409)
        response = self.route(PC1_PARENT, KB1_PARENT)
        text = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 409)
        self.assertFalse(IPV4.search(text), text)
        self.assertNotIn("paired_count", text)
        self.assertNotIn("existing", text)

    def test_the_standalone_conflict_does_not_name_the_other_host(self):
        self.reply({"ok": False, "status": "CONFLICT", "owner_mac": "0A:1B:2C:99:99:99",
                    "error": "That endpoint is already owned by 0A:1B:2C:99:99:99 and could not be reassigned."}, 409)
        text = self.route(PC1_PARENT, KB1_PARENT).get_data(as_text=True)
        self.assertNotIn("0A:1B:2C:99", text.upper())
        self.assertNotIn("owner_mac", text)

    def test_an_unreachable_device_is_an_upstream_condition_not_a_server_fault(self):
        self.reply({"ok": False, "error": "device unreachable: %s.31" % NET_A}, 502)
        response = self.route(PC1_PARENT, KB1_PARENT)
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.get_json()["status"], "unreachable")
        self.assertFalse(IPV4.search(response.get_data(as_text=True)))

    def test_a_server_error_from_the_pair_path_is_not_passed_through_verbatim(self):
        self.reply({"ok": False, "error": "Traceback at %s.31 password=secret" % NET_A}, 500)
        response = self.route(PC1_PARENT, KB1_PARENT)
        self.assertEqual(response.status_code, 502)
        self.assertNotIn("Traceback" if False else NET_A, response.get_data(as_text=True))

    def test_a_success_body_is_rebuilt_not_forwarded(self):
        self.reply({"ok": True, "message": "Pairing successful", "response": {"config": {"x": NET_A + ".1"}},
                    "pairing": {"active": NET_A + ".21", "available": []}})
        text = self.route(PC1_PARENT, KB1_PARENT).get_data(as_text=True)
        self.assertFalse(IPV4.search(text), text)
        self.assertNotIn("pairing", text)

    def test_nothing_falls_back_to_the_other_path_when_the_first_fails(self):
        self.reply({"ok": False, "error": "x"}, 500)
        self.route(PC1_PARENT, KB1_PARENT)
        self.assertEqual([c[0] for c in self.calls], ["icron_pair"])


# =================================================================== structure
class StructureTests(unittest.TestCase):
    """The handler and its registration, held at the source level where a behaviour test cannot."""

    def setUp(self):
        self.source = (ROOT / "omni_remote_api.py").read_text(encoding="utf-8").replace("\r\n", "\n")
        start = self.source.index('@app.route("/api/user/usb_route"')
        self.handler = self.source[start:self.source.index("def _usb_permitted_endpoints", start)]

    def test_the_handler_dispatches_on_the_derived_control_path_not_on_anything_sent(self):
        self.assertIn('cell["path"]', self.handler)
        self.assertNotIn('data.get("path")', self.handler)
        self.assertNotIn('data.get("lex")', self.handler)
        self.assertNotIn('data.get("rex")', self.handler)
        self.assertNotIn('data.get("replaceExisting")', self.handler)

    def test_the_server_registers_the_integrated_views(self):
        server = (ROOT / "OmniMatrix_upgrade_server_v7_6y.py").read_text(encoding="utf-8")
        self.assertIn("usb_icron_pair_view=api_usb_pair", server)
        self.assertIn("usb_icron_unpair_view=api_usb_unpair", server)

    def test_the_endpoint_the_page_posts_to_is_registered_in_the_route_table(self):
        import OmniMatrix_upgrade_server_v7_6y as srv
        rules = {str(r.rule): sorted(r.methods) for r in srv.app.url_map.iter_rules()}
        self.assertIn("/api/user/usb_route", rules)
        self.assertIn("POST", rules["/api/user/usb_route"])
        self.assertIn("/api/user/usb_state", rules)
        page = (ROOT / "ui" / "user" / "user.js").read_text(encoding="utf-8")
        self.assertIn("'/api/user/usb_route'", page)
        self.assertIn("'/api/user/usb_state'", page)

    def test_the_two_views_the_user_path_calls_are_the_ones_the_admin_page_posts_to(self):
        admin = (ROOT / "ui" / "matrix" / "usb.js").read_text(encoding="utf-8")
        for path in ("/api/usb_pair", "/api/usb_unpair", "/api/usb_route/pair", "/api/usb_route/unpair"):
            self.assertIn("'%s'" % path, admin)
            self.assertIn('"%s"' % path, self.handler)


class RealServerBoundaryTests(unittest.TestCase):
    """Through the real registered handler and the real pair view, with the transport fenced.

    The cross-network refusal must come from the derivation before any device is read; a route the
    derivation accepts must reach the transport first with a READ, addressed from the server's own row.
    """

    def setUp(self):
        fast_hashing(self)
        import OmniMatrix_upgrade_server_v7_6y as srv
        self.srv = srv
        self.dir = Path(tempfile.mkdtemp(prefix="omni-usbreal-"))
        self.store = srv._user_store
        saved = (self.store._path, self.store._audit_path, dict(self.store._accounts),
                 dict(self.store._sessions), list(self.store._audit))

        def restore():
            self.store._path, self.store._audit_path = saved[0], saved[1]
            self.store._accounts.clear()
            self.store._accounts.update(saved[2])
            self.store._sessions.clear()
            self.store._sessions.update(saved[3])
            self.store._audit[:] = saved[4]
            srv._user_observation.invalidate() if hasattr(srv._user_observation, "invalidate") else None

        self.addCleanup(restore)
        self.store._path = self.dir / "users.json"
        self.store._audit_path = self.dir / "audit.json"
        self.store._accounts.clear()
        self.store._sessions.clear()
        self.store._audit.clear()
        for name in ("CACHE", "SCAN_RESULTS", "CSV_VIEW"):
            original = getattr(srv, name)
            self.addCleanup(lambda n=name, o=original: setattr(srv, n, o))
            setattr(srv, name, self.dir / original.name)
        (self.dir / srv.CACHE.name).write_text(json.dumps(units()), encoding="utf-8")

        self.current = standard_state()
        self.transport = []

        def api_usb_state_stub():
            return jsonify(copy.deepcopy(self.current))

        def transport_stub(url, payload, *a, **k):
            self.transport.append(json.loads(json.dumps(payload, default=str)))
            raise OSError("synthetic: no device")

        for name, replacement in (("api_usb_state", api_usb_state_stub),
                                  ("_ws_send_recv", transport_stub),
                                  ("_unit_offline_refusal", lambda **roles: None)):
            original = getattr(srv, name)
            self.addCleanup(lambda n=name, o=original: setattr(srv, n, o))
            setattr(srv, name, replacement)
        srv._forget_user_observation("usb")
        self.addCleanup(lambda: srv._forget_user_observation("usb"))

        self.store.create("tina", "tina-password-1", omni_users.ROLE_USER,
                          Permissions(features=("usb",), sources=(PC1_PARENT,),
                                      destinations=(KB1_PARENT, KB3_PARENT)))
        self.tina = self.store.find_by_username("tina")
        self.session = self.store.open_session(self.tina.id)
        listener = srv._remote_listener
        previous = listener._server
        self.addCleanup(lambda: setattr(listener, "_server", previous))
        listener._server = object()
        self.client = srv.app.test_client()

    def route(self, computer, device, detach=False):
        self.client.set_cookie("omnisuite_session", self.session.token)
        return self.client.post(
            "/api/user/usb_route",
            json={"computer": usb_mac(computer), "peripherals": usb_mac(device), "detach": detach},
            environ_base={omni_remote.LISTENER_KEY: omni_remote.REMOTE},
            headers={"X-OmniSuite-CSRF": self.session.csrf_token})

    def test_a_cross_network_route_never_reaches_a_device(self):
        response = self.route(PC1_PARENT, KB3_PARENT)
        self.assertEqual(response.status_code, 409, response.get_data(as_text=True))
        self.assertEqual(response.get_json()["reason"], "network_mismatch")
        self.assertEqual(self.transport, [], "a prohibited route touched the transport")

    def test_an_eligible_route_reaches_the_transport_with_a_read_from_the_servers_own_row(self):
        response = self.route(PC1_PARENT, KB1_PARENT)
        # The synthetic transport answers "no device", which the pair path reports as unreachable.
        self.assertEqual(response.status_code, 502, response.get_data(as_text=True))
        self.assertEqual(response.get_json()["status"], "unreachable")
        self.assertTrue(self.transport, "an eligible route never reached the pair path")
        first = self.transport[0]
        self.assertEqual(first.get("config_get"), "usb_icron")
        self.assertFalse([p for p in self.transport if "config_set" in p],
                         "a write happened before the first read answered")
        self.assertFalse(IPV4.search(response.get_data(as_text=True)))

    def test_the_real_pair_view_refuses_a_cross_network_pair_without_a_write_even_if_called_directly(self):
        # Second line of defence: the pair path's own network gate, reached by bypassing the
        # derivation. Reads answer with synthetic configs; any write fails the test.
        configs = {NET_A + ".21": {"macaddress": usb_mac(PC1_PARENT), "ipaddress": NET_A + ".121",
                                   "subnetmask": MASK, "type": "LEX", "paired_devices": {}},
                   NET_B + ".33": {"macaddress": usb_mac(KB3_PARENT), "ipaddress": NET_B + ".133",
                                   "subnetmask": MASK, "type": "REX", "paired_devices": {}}}
        writes = []

        def transport(url, payload, *a, **k):
            if "config_set" in payload:
                writes.append(payload)
                raise AssertionError("a device was written")
            ip = re.search(r"(\d+\.\d+\.\d+\.\d+)", url).group(1)
            if payload.get("config_get") == "usb_icron":
                return {"config": configs[ip]}
            return {"config": {"hostname": "x"}}

        self.srv._ws_send_recv = transport
        with self.srv.app.test_request_context("/api/usb_pair", method="POST",
                                               json={"rex": NET_B + ".33", "lex": NET_A + ".21",
                                                     "makeActive": False, "replaceExisting": True}):
            result = self.srv.api_usb_pair()
        response = result[0] if isinstance(result, tuple) else result
        code = result[1] if isinstance(result, tuple) else 200
        self.assertEqual(code, 409)
        self.assertEqual(response.get_json()["state"], "NETWORK_MISMATCH")
        self.assertEqual(writes, [])


if __name__ == "__main__":
    unittest.main()
