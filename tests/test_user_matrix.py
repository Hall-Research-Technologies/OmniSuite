"""The restricted User's routing interfaces: Standard, Matrix, and who may use which.

Three things are held here, and they are held independently because each one
was a place a plausible implementation goes wrong:

  * WHO may use WHICH interface is an administrator's decision stored on the
    account. An account that existed before there was a choice keeps the one it
    had, and an account may not be saved able to route with no way to do it.
  * WHAT the Matrix draws is built on the server from the account's own
    permitted sources, displays and groups. It is a projection, not the
    administrative grid with rows hidden: a source or display the account does
    not hold is absent, and so is the name of whatever it is showing.
  * WHETHER a route is allowed is decided on the server for every request, from
    the account as it is at that moment. The interface a request claims is
    checked against the account, so a page that lies gains nothing.

Nothing here contacts hardware. The route engine is stubbed through the shared
routing harness, and device state is synthetic.
"""
from __future__ import annotations

import copy
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
import omni_groups                                   # noqa: E402
import omni_projection                               # noqa: E402
import omni_users                                    # noqa: E402
from omni_users import Permissions                   # noqa: E402
from tests import test_user_routing as harness       # noqa: E402

# Synthetic identities and documentation-range addresses: nothing here may
# resemble a bench value.
SRC_A = harness.TEACHER_PC
SRC_B = harness.DOC_CAMERA
SRC_X = harness.UNSEEN_SOURCE
ROOM_1 = "b8:98:b0:00:00:41"
ROOM_2 = "b8:98:b0:00:00:42"
ROOM_3 = "b8:98:b0:00:00:43"
SOLO = harness.FRONT_DISPLAY
HIDDEN = harness.UNSEEN_DISPLAY

UNITS = copy.deepcopy(harness.UNITS) + [
    {"ip": "192.0.2.41", "mac": ROOM_1, "hostname": "dec-r1", "role": "decoder",
     "model": "hw-omni-d4511"},
    {"ip": "192.0.2.42", "mac": ROOM_2, "hostname": "dec-r2", "role": "decoder",
     "model": "hw-omni-d4511"},
    {"ip": "192.0.2.43", "mac": ROOM_3, "hostname": "dec-r3", "role": "decoder",
     "model": "hw-omni-d4511"},
]

#: Encoder -> (audio multicast, port), documentation range.
AUDIO = {"192.0.2.11": ("233.252.0.11", 5004), "192.0.2.12": ("233.252.0.12", 5004),
         "192.0.2.19": ("233.252.0.19", 5004)}


def decoder_row(ip, *, video_from=None, audio_from=None, online=True, multiview=False):
    row = {"ip": ip, "online": online, "multiview_active": multiview,
           "ip1_addr": "", "ip1_port": 0, "ip3_addr": "", "ip3_port": 0}
    if audio_from:
        addr, port = AUDIO[audio_from]
        row["ip3_addr"], row["ip3_port"] = addr, port
    return row, video_from


def build_state(decoders, encoders_online=None):
    """A synthetic /api/state. `decoders` is [(ip, kwargs)]."""
    encoders_online = encoders_online or {}
    encoders = [{"ip": ip, "online": encoders_online.get(ip, True),
                 "a_mcast": AUDIO[ip][0], "a_port": AUDIO[ip][1]} for ip in AUDIO]
    rows, routes = [], {}
    for ip, kwargs in decoders:
        row, video_from = decoder_row(ip, **kwargs)
        rows.append(row)
        if video_from:
            routes[ip] = video_from
    return {"ok": True, "encoders": encoders, "decoders": rows, "routes": routes}


def canonical(value):
    return omni_users._normalise_identity(value)


class _Account:
    def __init__(self, permissions, admin=False):
        self.permissions = permissions
        self.is_admin = admin
        self.role = "admin" if admin else "user"
        self.enabled = True
        self.username = "synthetic"


class InterfacePermissionTests(unittest.TestCase):
    def test_a_new_permission_set_defaults_to_standard_routing(self):
        self.assertEqual(Permissions(features=("av",)).interfaces,
                         frozenset({"standard"}))

    def test_an_account_stored_before_the_choice_existed_keeps_standard_routing(self):
        # The upgrade case: no `interfaces` key at all. It must not lose the
        # one interface it always had, and must not gain the new one.
        loaded = Permissions.from_json({"features": ["av"], "sources": [SRC_A]})
        self.assertEqual(loaded.interfaces, frozenset({"standard"}))
        self.assertTrue(loaded.has_interface("standard"))
        self.assertFalse(loaded.has_interface("matrix"))

    def test_an_explicit_empty_choice_is_kept_not_repaired(self):
        loaded = Permissions.from_json({"features": ["av"], "interfaces": []})
        self.assertEqual(loaded.interfaces, frozenset())

    def test_a_malformed_choice_is_treated_as_not_stated(self):
        for junk in ("matrix", 7, {"matrix": True}):
            with self.subTest(junk=junk):
                self.assertEqual(
                    Permissions.from_json({"features": ["av"], "interfaces": junk}).interfaces,
                    frozenset({"standard"}))

    def test_unknown_interface_names_are_dropped(self):
        perms = Permissions(features=("av",), interfaces=["MATRIX ", "drag", "", None])
        self.assertEqual(perms.interfaces, frozenset({"matrix"}))

    def test_the_choice_round_trips_through_json(self):
        perms = Permissions(features=("av",), interfaces=["matrix", "standard"])
        self.assertEqual(Permissions.from_json(perms.to_json()).interfaces,
                         frozenset({"matrix", "standard"}))
        self.assertEqual(perms.to_json()["interfaces"], ["matrix", "standard"])

    def test_a_routing_account_with_no_interface_is_refused(self):
        refusal = omni_users.interface_refusal(
            omni_users.ROLE_USER, Permissions(features=("av",), interfaces=[]))
        self.assertTrue(refusal)
        # Not routing-capable, an administrator, or a choice made: all fine.
        self.assertEqual(omni_users.interface_refusal(
            omni_users.ROLE_USER, Permissions(features=("usb",), interfaces=[])), "")
        self.assertEqual(omni_users.interface_refusal(
            omni_users.ROLE_ADMIN, Permissions(features=("av",), interfaces=[])), "")
        self.assertEqual(omni_users.interface_refusal(
            omni_users.ROLE_USER, Permissions(features=("av",), interfaces=["matrix"])), "")


class StoreInterfaceTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="omni-iface-"))
        self.store = omni_users.UserStore(self.dir / "users.json", self.dir / "audit.json")

    def test_create_refuses_a_routing_account_with_no_interface(self):
        result = self.store.create("nobody", "long-enough-pass", omni_users.ROLE_USER,
                                   Permissions(features=("av",), interfaces=[]))
        self.assertFalse(result["ok"])
        self.assertIn("routing interface", result["error"])
        self.assertIsNone(self.store.find_by_username("nobody"))

    def test_set_permissions_refuses_it_and_changes_nothing(self):
        made = self.store.create("sam", "long-enough-pass", omni_users.ROLE_USER,
                                 Permissions(features=("av",), sources=[SRC_A]))
        uid = made["user"]["id"]
        result = self.store.set_permissions(
            uid, Permissions(features=("av",), sources=[SRC_B], interfaces=[]))
        self.assertFalse(result["ok"])
        held = self.store.get(uid).permissions
        self.assertEqual(held.sources, frozenset({SRC_A}))
        self.assertEqual(held.interfaces, frozenset({"standard"}))

    def test_changing_the_interface_is_its_own_audited_event(self):
        made = self.store.create("sam", "long-enough-pass", omni_users.ROLE_USER,
                                 Permissions(features=("av",), sources=[SRC_A]))
        uid = made["user"]["id"]
        self.store.set_permissions(uid, Permissions(
            features=("av",), sources=[SRC_A], interfaces=["standard", "matrix"]))
        entries = [e for e in self.store.audit_entries(limit=50)
                   if e["event"] == "user.routing_interfaces_changed"]
        self.assertEqual(len(entries), 1)
        detail = entries[0]["detail"]
        self.assertEqual(detail["interfaces"], ["matrix", "standard"])
        self.assertEqual(detail["was"], ["standard"])
        self.assertEqual(detail["username"], "sam")
        # A change that leaves the interfaces alone does not claim to have
        # changed them.
        self.store.set_permissions(uid, Permissions(
            features=("av",), sources=[SRC_B], interfaces=["standard", "matrix"]))
        again = [e for e in self.store.audit_entries(limit=50)
                 if e["event"] == "user.routing_interfaces_changed"]
        self.assertEqual(len(again), 1)

    def test_the_choice_survives_a_restart(self):
        self.store.create("sam", "long-enough-pass", omni_users.ROLE_USER,
                          Permissions(features=("av",), interfaces=["matrix"]))
        again = omni_users.UserStore(self.dir / "users.json", self.dir / "audit.json")
        self.assertEqual(again.find_by_username("sam").permissions.interfaces,
                         frozenset({"matrix"}))

    def test_an_old_users_file_without_the_field_loads_as_standard(self):
        path = self.dir / "old.json"
        self.store.create("sam", "long-enough-pass", omni_users.ROLE_USER,
                          Permissions(features=("av",), sources=[SRC_A]))
        raw = json.loads((self.dir / "users.json").read_text(encoding="utf-8"))
        self.assertTrue(raw["users"], "the fixture wrote no account to age")
        for account in raw["users"]:
            self.assertIn("interfaces", account["permissions"])
            account["permissions"].pop("interfaces")
        path.write_text(json.dumps(raw), encoding="utf-8")
        old = omni_users.UserStore(path, self.dir / "audit2.json")
        self.assertEqual(old.find_by_username("sam").permissions.interfaces,
                         frozenset({"standard"}))


class MatrixProjectionTests(unittest.TestCase):
    """The grid, built for one account, as a pure function of synthetic state."""

    def setUp(self):
        self.groups = omni_groups.GroupStore(
            Path(tempfile.mkdtemp(prefix="omni-mxg-")) / "g.json", canonical)
        self.group = self.groups.create("Front rooms", [ROOM_1, ROOM_2, ROOM_3])["group"]

    def matrix(self, state, perms, groups=True, admin=False):
        return omni_projection.user_matrix(
            copy.deepcopy(state), copy.deepcopy(UNITS), _Account(perms, admin),
            None, self.groups if groups else None)

    def perms(self, **extra):
        base = dict(features=("av",), sources=[SRC_A, SRC_B],
                    destinations=[SOLO], groups=[self.group["id"]],
                    interfaces=["matrix"])
        base.update(extra)
        return Permissions(**base)

    def state(self, **by_ip):
        decoders = [("192.0.2.21", by_ip.get("solo", {})),
                    ("192.0.2.29", by_ip.get("hidden", {})),
                    ("192.0.2.41", by_ip.get("r1", {})),
                    ("192.0.2.42", by_ip.get("r2", {})),
                    ("192.0.2.43", by_ip.get("r3", {}))]
        return build_state(decoders, by_ip.get("encoders"))

    def rows(self, result):
        return {r["id"]: r for r in result["rows"]}

    def test_columns_and_rows_are_only_what_the_account_holds(self):
        result = self.matrix(self.state(), self.perms())
        ids = {s["id"] for s in result["sources"]}
        self.assertEqual(ids, {SRC_A, SRC_B})
        self.assertNotIn(SRC_X, ids)
        kinds = {(r["kind"], r["id"]) for r in result["rows"]}
        self.assertEqual(kinds, {("display", SOLO), ("group", self.group["id"])})

    def test_a_group_is_one_row_and_its_members_are_not_rows(self):
        result = self.matrix(self.state(), self.perms())
        for member in (ROOM_1, ROOM_2, ROOM_3):
            self.assertNotIn(member, self.rows(result))
        row = self.rows(result)[self.group["id"]]
        self.assertEqual((row["kind"], row["displays"]), ("group", 3))

    def test_holding_a_member_does_not_add_the_group_or_the_reverse(self):
        only_member = self.matrix(self.state(), self.perms(destinations=[ROOM_1], groups=[]))
        self.assertEqual({(r["kind"], r["id"]) for r in only_member["rows"]},
                         {("display", ROOM_1)})
        only_group = self.matrix(self.state(), self.perms(destinations=[]))
        self.assertEqual({(r["kind"], r["id"]) for r in only_group["rows"]},
                         {("group", self.group["id"])})

    def test_nothing_about_an_unpermitted_device_appears_anywhere(self):
        # The hidden display is showing the hidden source. Neither identity,
        # hostname nor address may appear in the grid this account receives.
        state = self.state(hidden={"video_from": "192.0.2.19", "audio_from": "192.0.2.19"},
                           solo={"video_from": "192.0.2.19"})
        text = json.dumps(self.matrix(state, self.perms()))
        for needle in (SRC_X, HIDDEN, "enc-other", "dec-other", "192.0.2.19", "192.0.2.29",
                       "233.252.0.19"):
            self.assertNotIn(needle, text)
        for needle in ("192.0.2.", "233.252."):
            self.assertNotIn(needle, text)

    def test_a_display_showing_a_source_outside_the_account_says_other_not_who(self):
        state = self.state(solo={"video_from": "192.0.2.19", "audio_from": "192.0.2.19"})
        row = self.rows(self.matrix(state, self.perms()))[SOLO]
        self.assertEqual(row["video"], {"mode": "other", "source_id": None})
        self.assertEqual(row["audio"], {"mode": "other", "source_id": None})

    def test_video_and_audio_are_separate_planes_so_breakaway_is_visible(self):
        state = self.state(solo={"video_from": "192.0.2.11", "audio_from": "192.0.2.12"})
        row = self.rows(self.matrix(state, self.perms()))[SOLO]
        self.assertEqual(row["video"], {"mode": "source", "source_id": SRC_A})
        self.assertEqual(row["audio"], {"mode": "source", "source_id": SRC_B})

    def test_a_compositing_display_has_no_video_crosspoint_but_keeps_its_audio(self):
        state = self.state(solo={"video_from": "192.0.2.11", "audio_from": "192.0.2.11",
                                 "multiview": True})
        row = self.rows(self.matrix(state, self.perms()))[SOLO]
        self.assertEqual(row["video"]["mode"], "multiview")
        self.assertEqual(row["audio"], {"mode": "source", "source_id": SRC_A})

    def test_nothing_routed_is_none(self):
        row = self.rows(self.matrix(self.state(), self.perms()))[SOLO]
        self.assertEqual(row["video"]["mode"], "none")
        self.assertEqual(row["audio"]["mode"], "none")

    def test_the_display_row_and_the_standard_page_agree_about_what_is_showing(self):
        # Derived once: the Matrix row's video plane must name the same source
        # the Standard page's `showing` names.
        state = self.state(solo={"video_from": "192.0.2.12"})
        standard = omni_projection.user_state(
            copy.deepcopy(state), copy.deepcopy(UNITS), _Account(self.perms()), None, self.groups)
        showing = [d for d in standard["destinations"] if d["id"] == SOLO][0]["showing"]
        row = self.rows(self.matrix(state, self.perms()))[SOLO]
        self.assertEqual(showing["source_id"], row["video"]["source_id"])

    # ------------------------------------------------------- group states
    def test_a_group_all_on_one_source_is_all(self):
        state = self.state(**{k: {"video_from": "192.0.2.11"} for k in ("r1", "r2", "r3")})
        video = self.rows(self.matrix(state, self.perms()))[self.group["id"]]["video"]
        self.assertEqual((video["state"], video["source_id"]), ("all", SRC_A))
        self.assertEqual(video["counts"]["sources"], {SRC_A: 3})

    def test_a_group_with_one_member_routed_is_partial_not_all(self):
        state = self.state(r1={"video_from": "192.0.2.11"})
        video = self.rows(self.matrix(state, self.perms()))[self.group["id"]]["video"]
        self.assertEqual(video["state"], "partial")
        self.assertIsNone(video["source_id"])
        self.assertEqual(video["counts"]["sources"], {SRC_A: 1})
        self.assertEqual(video["counts"]["none"], 2)

    def test_a_group_on_two_sources_is_mixed(self):
        state = self.state(r1={"video_from": "192.0.2.11"}, r2={"video_from": "192.0.2.12"},
                           r3={"video_from": "192.0.2.11"})
        video = self.rows(self.matrix(state, self.perms()))[self.group["id"]]["video"]
        self.assertEqual(video["state"], "mixed")
        self.assertEqual(video["counts"]["sources"], {SRC_A: 2, SRC_B: 1})

    def test_video_and_audio_of_one_group_are_judged_separately(self):
        state = self.state(**{k: {"video_from": "192.0.2.11"} for k in ("r1", "r2", "r3")},
                           )
        state["decoders"][2]["ip3_addr"], state["decoders"][2]["ip3_port"] = AUDIO["192.0.2.12"]
        row = self.rows(self.matrix(state, self.perms()))[self.group["id"]]
        self.assertEqual(row["video"]["state"], "all")
        self.assertEqual(row["audio"]["state"], "partial")

    def test_an_unanswering_member_stops_a_group_being_all(self):
        state = self.state(**{k: {"video_from": "192.0.2.11"} for k in ("r1", "r2")},
                           r3={"video_from": "192.0.2.11", "online": False})
        row = self.rows(self.matrix(state, self.perms()))[self.group["id"]]
        self.assertEqual(row["video"]["state"], "partial")
        self.assertTrue(row["online"], "two members still answer")
        self.assertEqual((row["video"]["online"], row["video"]["total"]), (2, 3))

    def test_a_group_with_no_member_answering_is_unavailable(self):
        state = self.state(**{k: {"online": False} for k in ("r1", "r2", "r3")})
        row = self.rows(self.matrix(state, self.perms()))[self.group["id"]]
        self.assertEqual(row["video"]["state"], "unavailable")
        self.assertEqual(row["audio"]["state"], "unavailable")
        self.assertFalse(row["online"])

    def test_a_group_member_that_is_not_in_the_inventory_counts_against_all(self):
        self.groups.update(self.group["id"], members=[ROOM_1, "b8:98:b0:00:00:77"])
        state = self.state(r1={"video_from": "192.0.2.11"})
        video = self.rows(self.matrix(state, self.perms()))[self.group["id"]]["video"]
        self.assertEqual(video["total"], 2)
        self.assertEqual(video["state"], "partial")

    def test_aggregate_of_no_online_members_does_not_divide_by_zero(self):
        self.assertEqual(omni_projection.aggregate_plane([], 0)["state"], "unavailable")

    def test_the_payload_is_an_allowlist_of_named_fields(self):
        # A projection builds from named fields, so a field nobody added here
        # cannot appear however the administrative model grows.
        state = self.state(solo={"video_from": "192.0.2.11", "audio_from": "192.0.2.12"},
                           r1={"video_from": "192.0.2.11"})
        result = self.matrix(state, self.perms())
        self.assertEqual(set(result), {"ok", "interfaces", "revision", "sources", "rows"})
        for source in result["sources"]:
            self.assertEqual(set(source), {"id", "name", "online"})
        for row in result["rows"]:
            if row["kind"] == "display":
                self.assertEqual(set(row), {"kind", "id", "name", "online", "video", "audio"})
                self.assertEqual(set(row["video"]), {"mode", "source_id"})
            else:
                self.assertEqual(set(row), {"kind", "id", "name", "displays", "online",
                                            "video", "audio"})
                self.assertEqual(set(row["video"]),
                                 {"state", "source_id", "counts", "online", "total"})
                self.assertEqual(set(row["video"]["counts"]),
                                 {"sources", "other", "none", "multiview"})

    # ----------------------------------------------------------- offline
    def test_offline_sources_and_displays_are_reported_offline_not_hidden(self):
        state = self.state(solo={"online": False}, encoders={"192.0.2.11": False})
        result = self.matrix(state, self.perms())
        self.assertFalse(self.rows(result)[SOLO]["online"])
        self.assertFalse([s for s in result["sources"] if s["id"] == SRC_A][0]["online"])

    # --------------------------------------------------------- ordering etc
    def test_rows_are_ordered_deterministically_displays_before_groups(self):
        perms = self.perms(destinations=[SOLO, ROOM_1])
        first = self.matrix(self.state(), perms)
        second = self.matrix(self.state(), perms)
        self.assertEqual(first["rows"], second["rows"])
        kinds = [r["kind"] for r in first["rows"]]
        self.assertEqual(kinds, sorted(kinds, key={"display": 0, "group": 1}.get))

    def test_an_administrator_sees_everything_in_the_same_shape(self):
        result = self.matrix(self.state(), Permissions(features=("av",)), admin=True)
        self.assertEqual({s["id"] for s in result["sources"]}, {SRC_A, SRC_B, SRC_X})
        self.assertEqual(result["interfaces"], ["matrix", "standard"])

    def test_no_groups_store_is_no_group_rows(self):
        result = self.matrix(self.state(), self.perms(), groups=False)
        self.assertEqual([r["kind"] for r in result["rows"]], ["display"])

    def test_the_revision_moves_when_only_the_interface_changes(self):
        a = omni_projection.permission_revision(_Account(self.perms()))
        b = omni_projection.permission_revision(
            _Account(self.perms(interfaces=["matrix", "standard"])))
        self.assertNotEqual(a, b)

    def test_the_state_and_session_summaries_carry_the_interfaces(self):
        account = _Account(self.perms(interfaces=["standard", "matrix"]))
        state = omni_projection.user_state(
            build_state([]), copy.deepcopy(UNITS), account, None, self.groups)
        self.assertEqual(state["interfaces"], ["matrix", "standard"])
        self.assertEqual(omni_projection.session_summary(account)["interfaces"],
                         ["matrix", "standard"])


class MatrixEndpointTests(harness.UserRoutingTestBase):
    """The real application, with the engines stubbed."""

    def setUp(self):
        super().setUp()
        self._patch_registered("load_cache", lambda: copy.deepcopy(UNITS))
        self.groups = omni_groups.GroupStore(self.dir / "display_groups.json", canonical)
        self._patch_registered("groups", self.groups)
        self.group = self.groups.create("Front rooms", [ROOM_1, ROOM_2, ROOM_3])["group"]
        self.state_now = build_state([("192.0.2.21", {}), ("192.0.2.41", {}),
                                      ("192.0.2.42", {}), ("192.0.2.43", {})])
        self._patch_registered("matrix_state", lambda: copy.deepcopy(self.state_now))

    def make(self, name, interfaces, **extra):
        grant = dict(features=("av",), sources=[SRC_A, SRC_B], destinations=[SOLO],
                     groups=[self.group["id"]], interfaces=interfaces)
        grant.update(extra)
        self.store.create(name, name + "-password-1", omni_users.ROLE_USER,
                          Permissions(**grant))
        return self.store.find_by_username(name)

    def regrant(self, account, **change):
        held = account.permissions
        grant = dict(features=held.features, sources=held.sources,
                     destinations=held.destinations, groups=held.groups,
                     interfaces=held.interfaces)
        grant.update(change)
        result = self.store.set_permissions(account.id, Permissions(**grant))
        self.assertTrue(result["ok"], result)

    def matrix_route(self, session, source=SRC_A, destination=SOLO, mode="av", **extra):
        body = {"source": source, "destination": destination, "mode": mode,
                "interface": "matrix"}
        body.update(extra)
        return self.post("/api/user/route", body, session)

    def matrix_group_route(self, session, source=SRC_A, mode="av", group=None, **extra):
        body = {"source": source, "group": group or self.group["id"], "mode": mode,
                "interface": "matrix"}
        body.update(extra)
        return self.post("/api/user/route_group", body, session)

    # ------------------------------------------------ who gets the grid
    def test_the_matrix_is_refused_to_an_account_that_was_not_given_it(self):
        std = self.make("std", ["standard"])
        reply = self.get("/api/user/matrix_state", self.session_for(std))
        self.assertEqual(reply.status_code, 403)
        self.assertEqual(reply.get_json()["reason"], "interface_not_permitted")
        self.assertIn("routing interface", reply.get_json()["error"])

    def test_the_matrix_is_served_to_matrix_only_and_to_both(self):
        for name, interfaces in (("mx", ["matrix"]), ("both", ["matrix", "standard"])):
            with self.subTest(name=name):
                reply = self.get("/api/user/matrix_state",
                                 self.session_for(self.make(name, interfaces)))
                self.assertEqual(reply.status_code, 200)
                body = reply.get_json()
                self.assertTrue(body["ok"])
                self.assertEqual({s["id"] for s in body["sources"]}, {SRC_A, SRC_B})
                self.assertEqual({(r["kind"], r["id"]) for r in body["rows"]},
                                 {("display", SOLO), ("group", self.group["id"])})

    def test_the_matrix_is_refused_without_the_av_feature(self):
        usb = self.make("usbonly", ["matrix"], features=("usb",))
        self.assertEqual(self.get("/api/user/matrix_state",
                                  self.session_for(usb)).status_code, 403)

    def test_the_matrix_route_is_classified_and_the_gate_reaches_it(self):
        self.assertEqual(omni_access.classify("/api/user/matrix_state", "GET"),
                         (omni_access.Access.USER, omni_access.FEATURE_AV))
        anonymous = self.client.get(
            "/api/user/matrix_state",
            environ_base={harness.omni_remote.LISTENER_KEY: harness.omni_remote.REMOTE})
        self.assertIn(anonymous.status_code, (401, 403))

    def test_the_matrix_body_carries_no_address(self):
        text = self.get("/api/user/matrix_state",
                        self.session_for(self.make("mx", ["matrix"]))).get_data(as_text=True)
        # Names are the operator's own vocabulary and belong here; addresses
        # are engineering detail and do not.
        for needle in ("192.0.2.", "233.252.", "enc-other", "dec-other"):
            self.assertNotIn(needle, text)

    def test_the_standard_state_names_the_interfaces_so_the_page_can_draw_tabs(self):
        body = self.get("/api/user/state", self.session_for(
            self.make("both", ["matrix", "standard"]))).get_json()
        self.assertEqual(body["interfaces"], ["matrix", "standard"])

    # ----------------------------------------- the claimed interface is checked
    def test_a_route_claiming_an_interface_the_account_lacks_is_refused(self):
        std = self.make("std", ["standard"])
        mx = self.make("mx", ["matrix"])
        refused = self.matrix_route(self.session_for(std))
        self.assertEqual((refused.status_code, refused.get_json()["reason"]),
                         (403, "interface_not_permitted"))
        also = self.post("/api/user/route", {"source": SRC_A, "destination": SOLO,
                                             "mode": "av", "interface": "standard"},
                         self.session_for(mx))
        self.assertEqual((also.status_code, also.get_json()["reason"]),
                         (403, "interface_not_permitted"))
        self.assertEqual(self.routes, [], "a refusal reached the engine")

    def test_the_default_interface_is_standard(self):
        std = self.make("std", ["standard"])
        reply = self.post("/api/user/route", {"source": SRC_A, "destination": SOLO},
                          self.session_for(std))
        self.assertEqual(reply.status_code, 200)
        self.assertEqual(len(self.routes), 1)
        mx = self.make("mx", ["matrix"])
        refused = self.post("/api/user/route", {"source": SRC_A, "destination": SOLO},
                            self.session_for(mx))
        self.assertEqual(refused.status_code, 403)

    def test_a_matrix_only_account_can_route_through_the_matrix(self):
        mx = self.make("mx", ["matrix"])
        for mode in ("av", "video", "audio"):
            with self.subTest(mode=mode):
                self.routes.clear()
                reply = self.matrix_route(self.session_for(mx), mode=mode)
                self.assertEqual(reply.status_code, 200, reply.get_json())
                self.assertEqual(len(self.routes), 1)
                self.assertEqual((self.routes[0]["mode"], self.routes[0]["encoder"],
                                  self.routes[0]["decoder"]),
                                 (mode, "192.0.2.11", "192.0.2.21"))

    def test_video_only_and_audio_only_are_not_a_standard_operation(self):
        std = self.make("std", ["standard"])
        for mode in ("video", "audio"):
            with self.subTest(mode=mode):
                reply = self.post("/api/user/route", {
                    "source": SRC_A, "destination": SOLO, "mode": mode,
                    "interface": "standard"}, self.session_for(std))
                self.assertEqual(reply.status_code, 400)
        self.assertEqual(self.routes, [])

    def test_a_mode_nobody_defined_never_reaches_the_engine(self):
        both = self.make("both", ["standard", "matrix"])
        for junk in ("av; drop", "AV2", "", 7, ["av"]):
            with self.subTest(junk=junk):
                reply = self.matrix_route(self.session_for(both), mode=junk)
                self.assertIn(reply.status_code, (200, 400))
                if junk in ("", ):
                    continue   # an empty mode means the default, av
                self.assertEqual(reply.status_code, 400)
        modes = {r["mode"] for r in self.routes}
        self.assertLessEqual(modes, {"av"})

    def test_an_unknown_interface_is_a_bad_request_not_a_server_error(self):
        both = self.make("both", ["standard", "matrix"])
        for junk in ("admin", "drag", 7, ["matrix"], {"a": 1}):
            with self.subTest(junk=junk):
                reply = self.post("/api/user/route", {
                    "source": SRC_A, "destination": SOLO, "mode": "av",
                    "interface": junk}, self.session_for(both))
                self.assertEqual(reply.status_code, 400)
        self.assertEqual(self.routes, [])

    def test_manipulated_ids_cannot_route_through_the_matrix(self):
        mx = self.make("mx", ["matrix"])
        session = self.session_for(mx)
        cases = [
            ({"source": SRC_X, "destination": SOLO}, "source_not_permitted"),
            ({"source": SRC_A, "destination": HIDDEN}, "destination_not_permitted"),
            # A member of a held group is not a display the account holds.
            ({"source": SRC_A, "destination": ROOM_1}, "destination_not_permitted"),
            # A group id in the display slot is not a display.
            ({"source": SRC_A, "destination": self.group["id"]}, None),
        ]
        for body, reason in cases:
            with self.subTest(body=body):
                reply = self.matrix_route(session, **body)
                self.assertIn(reply.status_code, (400, 403, 404))
                if reason:
                    self.assertEqual((reply.status_code, reply.get_json()["reason"]),
                                     (403, reason))
        self.assertEqual(self.routes, [])

    def test_a_group_the_account_does_not_hold_cannot_be_routed(self):
        other = self.groups.create("Other", [ROOM_1])["group"]
        mx = self.make("mx", ["matrix"])
        reply = self.matrix_group_route(self.session_for(mx), group=other["id"])
        self.assertEqual((reply.status_code, reply.get_json()["reason"]),
                         (403, "group_not_permitted"))
        self.assertEqual(self.routes, [])

    def test_a_display_id_is_not_a_group_and_a_group_id_is_not_a_display(self):
        mx = self.make("mx", ["matrix"])
        session = self.session_for(mx)
        # The destination TYPE is part of what is checked: a held display's
        # identity in the group slot is not a group the account holds.
        reply = self.matrix_group_route(session, group=SOLO)
        self.assertEqual((reply.status_code, reply.get_json()["reason"]),
                         (403, "group_not_permitted"))
        reply = self.matrix_route(session, destination=self.group["id"])
        self.assertIn(reply.status_code, (403, 404))
        self.assertEqual(self.routes, [])

    def test_malformed_ids_never_produce_a_server_error(self):
        both = self.make("both", ["standard", "matrix"])
        session = self.session_for(both)
        junk_values = [None, "", " ", 7, ["x"], {"a": 1}, "x" * 5000, "\x00", "b8:98",
                       "192.0.2.21", "../../etc", True]
        for value in junk_values:
            for path, field in (("/api/user/route", "destination"),
                                ("/api/user/route_group", "group")):
                with self.subTest(value=repr(value)[:30], path=path):
                    body = {"source": value if field == "destination" else SRC_A,
                            field: value, "interface": "matrix", "mode": "av"}
                    reply = self.post(path, body, session)
                    self.assertLess(reply.status_code, 500, reply.get_data(as_text=True))
        self.assertEqual(self.routes, [])

    def test_a_non_object_body_is_refused_not_a_crash(self):
        both = self.make("both", ["standard", "matrix"])
        session = self.session_for(both)
        for payload in ([], "text", 7, None):
            reply = self.client.post(
                "/api/user/route", data=json.dumps(payload),
                content_type="application/json",
                environ_base={harness.omni_remote.LISTENER_KEY: harness.omni_remote.REMOTE},
                headers={"X-OmniSuite-CSRF": session.csrf_token})
            self.assertLess(reply.status_code, 500)

    # --------------------------------------------------------- group route
    def test_a_matrix_group_route_uses_the_same_operation_as_standard(self):
        mx = self.make("mx", ["matrix"])
        reply = self.matrix_group_route(self.session_for(mx), mode="audio")
        body = reply.get_json()
        self.assertEqual((reply.status_code, body["routed"], body["total"]), (200, 3, 3))
        # Sequential, one display at a time, each through the ordinary engine,
        # carrying the mode chosen -- and no member named by address in the reply.
        self.assertEqual([r["decoder"] for r in self.routes],
                         ["192.0.2.41", "192.0.2.42", "192.0.2.43"])
        self.assertTrue(all(r["mode"] == "audio" and r["encoder"] == "192.0.2.11"
                            for r in self.routes))
        self.assertNotIn("192.0.2.", json.dumps(body))

    def test_a_partial_group_result_is_never_reported_as_full_success(self):
        answers = iter([({"ok": True, "status": "VERIFIED"}, 200),
                        ({"ok": False, "status": "NOT VERIFIED"}, 502),
                        ({"ok": True, "status": "VERIFIED"}, 200)])

        def fake_route():
            from flask import jsonify, request as flask_request
            self.routes.append(dict(flask_request.get_json(silent=True) or {}))
            payload, status = next(answers)
            return jsonify(payload), status

        self._patch_registered("route_view", fake_route)
        body = self.matrix_group_route(
            self.session_for(self.make("mx", ["matrix"]))).get_json()
        self.assertFalse(body["ok"])
        self.assertEqual((body["routed"], body["total"]), (2, 3))

    def test_a_standard_only_account_cannot_route_a_group_through_the_matrix(self):
        reply = self.matrix_group_route(self.session_for(self.make("std", ["standard"])))
        self.assertEqual((reply.status_code, reply.get_json()["reason"]),
                         (403, "interface_not_permitted"))
        self.assertEqual(self.routes, [])

    # ----------------------------------------------------------- the audit
    def audit_events(self):
        return [e for e in self.store.audit_entries(limit=200)]

    def test_a_matrix_route_is_recorded_as_one_entry_with_the_interface(self):
        self.matrix_route(self.session_for(self.make("mx", ["matrix"])), mode="video")
        entries = [e for e in self.audit_events() if e["event"] == "matrix.route"]
        self.assertEqual(len(entries), 1)
        detail = entries[0]["detail"]
        self.assertEqual((detail["interface"], detail["mode"]), ("matrix", "video"))
        self.assertEqual(detail["source"], SRC_A)
        self.assertFalse([e for e in self.audit_events() if e["event"] == "route"])

    def test_a_standard_route_keeps_its_existing_event_name(self):
        self.post("/api/user/route", {"source": SRC_A, "destination": SOLO, "mode": "av"},
                  self.session_for(self.make("std", ["standard"])))
        events = [e["event"] for e in self.audit_events()]
        self.assertIn("route", events)
        self.assertNotIn("matrix.route", events)

    def test_a_matrix_group_route_is_recorded_with_its_totals(self):
        self.matrix_group_route(self.session_for(self.make("mx", ["matrix"])))
        entries = [e for e in self.audit_events() if e["event"] == "matrix.route_group"]
        self.assertEqual(len(entries), 1)
        self.assertEqual((entries[0]["detail"]["routed"], entries[0]["detail"]["total"]),
                         (3, 3))

    def test_the_audit_never_carries_a_credential_or_a_token(self):
        session = self.session_for(self.make("mx", ["matrix"]))
        self.matrix_route(session)
        text = json.dumps(self.audit_events())
        for secret in (session.token, session.csrf_token, "mx-password-1"):
            self.assertNotIn(secret, text)

    # ----------------------------------------------- live permission changes
    def test_taking_the_matrix_away_refuses_the_next_request_without_a_sign_out(self):
        both = self.make("both", ["standard", "matrix"])
        session = self.session_for(both)
        self.assertEqual(self.matrix_route(session).status_code, 200)
        before = self.get("/api/user/revision", session).get_json()["revision"]
        self.regrant(both, interfaces=frozenset({"standard"}))
        self.assertEqual(self.get("/api/user/matrix_state", session).status_code, 403)
        refused = self.matrix_route(session)
        self.assertEqual((refused.status_code, refused.get_json()["reason"]),
                         (403, "interface_not_permitted"))
        # Standard is still there, and the session was not ended.
        self.assertEqual(self.post("/api/user/route", {
            "source": SRC_A, "destination": SOLO, "mode": "av",
            "interface": "standard"}, session).status_code, 200)
        after = self.get("/api/user/revision", session).get_json()["revision"]
        self.assertNotEqual(before, after, "an open page could not have noticed")

    def test_giving_the_matrix_starts_working_on_the_next_request(self):
        std = self.make("std", ["standard"])
        session = self.session_for(std)
        self.assertEqual(self.get("/api/user/matrix_state", session).status_code, 403)
        self.regrant(std, interfaces=frozenset({"standard", "matrix"}))
        self.assertEqual(self.get("/api/user/matrix_state", session).status_code, 200)
        self.assertEqual(self.matrix_route(session).status_code, 200)

    def test_removing_a_source_removes_its_column_and_its_route(self):
        mx = self.make("mx", ["matrix"])
        session = self.session_for(mx)
        self.regrant(mx, sources=frozenset({SRC_B}))
        body = self.get("/api/user/matrix_state", session).get_json()
        self.assertEqual([s["id"] for s in body["sources"]], [SRC_B])
        self.assertEqual(self.matrix_route(session, source=SRC_A).status_code, 403)

    def test_adding_a_display_adds_its_row(self):
        mx = self.make("mx", ["matrix"])
        session = self.session_for(mx)
        self.regrant(mx, destinations=frozenset({SOLO, ROOM_1}))
        rows = self.get("/api/user/matrix_state", session).get_json()["rows"]
        self.assertIn(("display", ROOM_1), {(r["kind"], r["id"]) for r in rows})

    def test_a_group_losing_a_member_changes_the_aggregate_and_the_revision(self):
        mx = self.make("mx", ["matrix"])
        session = self.session_for(mx)
        before = self.get("/api/user/revision", session).get_json()["revision"]
        self.groups.update(self.group["id"], members=[ROOM_1, ROOM_2])
        body = self.get("/api/user/matrix_state", session).get_json()
        row = [r for r in body["rows"] if r["kind"] == "group"][0]
        self.assertEqual(row["displays"], 2)
        self.assertNotEqual(before, self.get("/api/user/revision", session).get_json()["revision"])

    def test_a_role_change_ends_the_session(self):
        mx = self.make("mx", ["matrix"])
        session = self.session_for(mx)
        self.store.set_role(mx.id, omni_users.ROLE_ADMIN)
        self.assertEqual(self.get("/api/user/matrix_state", session).status_code, 401)

    def test_a_deleted_account_is_signed_out_of_the_matrix(self):
        mx = self.make("mx", ["matrix"])
        session = self.session_for(mx)
        self.store.delete(mx.id)
        self.assertEqual(self.get("/api/user/matrix_state", session).status_code, 401)
        self.assertEqual(self.matrix_route(session).status_code, 401)

    def test_a_disabled_account_is_signed_out_of_the_matrix(self):
        mx = self.make("mx", ["matrix"])
        session = self.session_for(mx)
        self.store.set_enabled(mx.id, False)
        self.assertEqual(self.get("/api/user/matrix_state", session).status_code, 401)

    # -------------------------------------------------------- direct access
    def test_a_direct_access_session_follows_the_accounts_interfaces(self):
        both = self.make("both", ["standard", "matrix"])
        session = self.session_for(both, omni_users.BY_DIRECT_LINK)
        self.assertEqual(self.get("/api/user/matrix_state", session).status_code, 200)
        self.regrant(both, interfaces=frozenset({"standard"}))
        # The same session, converged on the account as it now is.
        self.assertEqual(self.get("/api/user/matrix_state", session).status_code, 403)
        state = self.get("/api/user/state", session).get_json()
        self.assertEqual(state["interfaces"], ["standard"])

    def test_a_direct_access_session_for_matrix_only_cannot_use_standard_claims(self):
        mx = self.make("mx", ["matrix"])
        session = self.session_for(mx, omni_users.BY_DIRECT_LINK)
        reply = self.post("/api/user/route", {"source": SRC_A, "destination": SOLO,
                                              "mode": "av", "interface": "standard"}, session)
        self.assertEqual(reply.status_code, 403)
        self.assertEqual(self.matrix_route(session).status_code, 200)

    def test_an_account_with_no_interface_at_all_is_refused_everywhere(self):
        # Reachable only from a record edited by hand; fail closed.
        account = self.make("std", ["standard"])
        account.permissions = Permissions(features=("av",), sources=[SRC_A],
                                          destinations=[SOLO], interfaces=[])
        session = self.session_for(account)
        self.assertEqual(self.post("/api/user/unroute", {"destination": SOLO},
                                   session).status_code, 403)
        self.assertEqual(self.get("/api/user/source_preview?source=" + SRC_A,
                                  session).status_code, 403)
        self.assertEqual(self.matrix_route(session).status_code, 403)
        self.assertEqual(self.post("/api/user/route", {
            "source": SRC_A, "destination": SOLO, "interface": "standard"},
            session).status_code, 403)
        self.assertEqual(self.routes + self.unroutes, [])

    # ---------------------------------------------------- admin account API
    def test_an_old_client_that_omits_interfaces_does_not_reset_them(self):
        both = self.make("both", ["standard", "matrix"])
        admin = self.session_for(self.admin)
        reply = self.post("/api/admin/users/" + both.id, {
            "action": "set_permissions",
            "permissions": {"features": ["av"], "sources": [SRC_A],
                            "destinations": [SOLO], "groups": []}}, admin)
        self.assertEqual(reply.status_code, 200, reply.get_json())
        self.assertEqual(self.store.get(both.id).permissions.interfaces,
                         frozenset({"standard", "matrix"}))

    def test_the_admin_api_refuses_a_routing_account_with_no_interface(self):
        both = self.make("both", ["standard", "matrix"])
        admin = self.session_for(self.admin)
        reply = self.post("/api/admin/users/" + both.id, {
            "action": "set_permissions",
            "permissions": {"features": ["av"], "interfaces": []}}, admin)
        self.assertEqual(reply.status_code, 400)
        self.assertEqual(self.store.get(both.id).permissions.interfaces,
                         frozenset({"standard", "matrix"}))
        created = self.post("/api/admin/users", {
            "username": "empty", "password": "empty-password-1", "role": "user",
            "permissions": {"features": ["av"], "interfaces": []}}, admin)
        self.assertEqual(created.status_code, 400)

    def test_a_new_account_made_without_the_field_is_standard(self):
        admin = self.session_for(self.admin)
        created = self.post("/api/admin/users", {
            "username": "plain", "password": "plain-password-1", "role": "user",
            "permissions": {"features": ["av"], "sources": [SRC_A]}}, admin)
        self.assertEqual(created.status_code, 200)
        self.assertEqual(self.store.find_by_username("plain").permissions.interfaces,
                         frozenset({"standard"}))

    def test_a_restricted_account_cannot_change_its_own_interfaces(self):
        std = self.make("std", ["standard"])
        reply = self.post("/api/admin/users/" + std.id, {
            "action": "set_permissions",
            "permissions": {"features": ["av"], "interfaces": ["standard", "matrix"]}},
            self.session_for(std))
        self.assertEqual(reply.status_code, 403)
        self.assertEqual(self.store.get(std.id).permissions.interfaces,
                         frozenset({"standard"}))


if __name__ == "__main__":
    unittest.main()
