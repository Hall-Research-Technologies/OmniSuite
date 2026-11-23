"""A Remote User chooses what feeds each window of an assigned Multiview layout.

The model under test:

  ADMIN assigns a saved layout (decoder identity + opaque layout id) and gives the account sources
    -> the user OPENS the layout and sees its windows, what is in each, and the sources they were given
    -> they choose a source per window (or empty one); choices are pending until Show
    -> Show sends {layout, base revision, changes}; the server decides, then hands the whole composition
       to the same Show engine the administrator's page uses.

Every refusal is proven by the engine never having been called, which is the same as proving no
device was written. Synthetic equipment only (documentation ranges, invented MACs); nothing reaches a
transport. Engine behaviour itself -- same-size reuse, the size conflict, replace, stale revision
against real devices -- is in test_user_multiview_compose.py.
"""
from __future__ import annotations

import copy
import re
import threading
import time
import unittest

try:
    from . import test_user_multiview as base
except ImportError:                                           # loaded by path
    import test_user_multiview as base

import omni_access
import omni_gate
from omni_users import Permissions

SRC_2 = "0a:1b:2c:e0:00:02"          # given to the account, ready
SRC_3 = "0a:1b:2c:e0:00:03"          # given, needs configuration
SRC_4 = "0a:1b:2c:e0:00:04"          # given, can never feed a window
SRC_5 = "0a:1b:2c:e0:00:05"          # exists, NOT given to the account
SRC_6 = "0a:1b:2c:e0:00:06"          # given, but not in the inventory
ADDR_S1, ADDR_S2, ADDR_S3, ADDR_S4, ADDR_S5 = (
    "198.51.100.90", "198.51.100.91", "203.0.113.93", "203.0.113.94", "203.0.113.95")
IPV4 = base.IPV4
KEY = base.KEY_A1


def units():
    rows = base.fixture_units()
    rows += [
        {"ip": ADDR_S2, "mac": SRC_2.upper(), "hostname": "camera-two", "model": "x-e4111", "role": "encoder"},
        {"ip": ADDR_S3, "mac": SRC_3.upper(), "hostname": "camera-three", "model": "x-e4111", "role": "encoder"},
        {"ip": ADDR_S4, "mac": SRC_4.upper(), "hostname": "camera-four", "model": "x-e4111", "role": "encoder"},
        {"ip": ADDR_S5, "mac": SRC_5.upper(), "hostname": "camera-five", "model": "x-e4111", "role": "encoder"},
    ]
    return rows


def source_rows():
    def row(mac, host, status, reason=""):
        return {"mac": mac, "hostname": host, "status": status, "reason": reason}
    return [row(base.SRC_1, "camera-one", "ready"),
            row(SRC_2, "camera-two", "ready"),
            row(SRC_3, "camera-three", "configuration_required", "Multicast configuration required"),
            row(SRC_4, "camera-four", "ineligible", "not an encoder"),
            row(SRC_5, "camera-five", "ready")]


def window(cell, mac="", ip="", origin="empty", health=""):
    return {"cell": cell, "source_mac": mac, "source_ip": ip, "origin": origin, "health": health,
            "input_active": origin == "live"}


class ChoiceBase(base.Harness):
    DESIGN = [{"cell": "main", "x": 0, "y": 0, "width": 1280, "height": 720},
              {"cell": "a", "x": 1280, "y": 0, "width": 640, "height": 360},
              {"cell": "b", "x": 1280, "y": 360, "width": 640, "height": 360}]

    def setUp(self):
        super().setUp()
        self.units = units()
        self.source_rows = source_rows()
        self.design = copy.deepcopy(self.DESIGN)
        self.window_answer = {"ok": True, "showing": True, "revision": "rev-1", "windows": [
            window("main", base.SRC_1, ADDR_S1, "live", "live"),
            window("a"),
            window("b", SRC_5, ADDR_S5, "live", "live")]}
        self.grant(features=("multiview",), multiviews=(KEY,),
                   sources=(base.SRC_1, SRC_2, SRC_3, SRC_4, SRC_6))

    def layout(self, key=KEY, session=None):
        return self.req("GET", "/api/user/multiview_layout?layout=" + key, session)

    def apply(self, changes=None, key=KEY, base_revision="rev-1", session=None, **extra):
        body = {"layout": key, "base": base_revision, "changes": {} if changes is None else changes}
        body.update(extra)
        return self.req("POST", "/api/user/multiview_compose", session, body)


# ================================================================ opening a layout
class OpenTests(ChoiceBase):
    def test_the_windows_the_state_and_the_sources_this_account_was_given(self):
        response = self.layout()
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        body = response.get_json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["layout"]["id"], KEY)
        self.assertEqual(body["layout"]["name"], "Quad view")
        self.assertTrue(body["layout"]["showing"])
        self.assertTrue(body["layout"]["editable"])
        self.assertEqual(body["revision"], "rev-1")
        self.assertEqual([w["cell"] for w in body["windows"]], ["main", "a", "b"])
        self.assertEqual([w["label"] for w in body["windows"]], ["Window 1", "Window 2", "Window 3"])
        self.assertEqual([(w["width"], w["height"]) for w in body["windows"]],
                         [(1280, 720), (640, 360), (640, 360)])
        main, a, b = body["windows"]
        self.assertEqual(main["source"], {"id": base.SRC_1, "name": "camera-one"})
        self.assertIsNone(a["source"])
        self.assertEqual(main["origin"], "live")

    def test_a_source_the_account_was_not_given_is_never_named(self):
        body = self.layout().get_json()
        window_b = next(w for w in body["windows"] if w["cell"] == "b")
        self.assertEqual(window_b["source"], {"id": None, "name": "Another source", "restricted": True})
        text = self.layout().get_data(as_text=True)
        for forbidden in (SRC_5, "camera-five", ADDR_S5):
            self.assertNotIn(forbidden, text)
        self.assertNotIn(SRC_5, [s["id"] for s in body["sources"]])

    def test_only_the_account_s_own_sources_are_offered_with_their_state(self):
        body = self.layout().get_json()
        offered = {s["id"]: s for s in body["sources"]}
        self.assertEqual(sorted(offered), sorted([base.SRC_1, SRC_2, SRC_3]))
        self.assertTrue(offered[base.SRC_1]["selectable"])
        self.assertFalse(offered[SRC_3]["selectable"])
        self.assertIn("configuration", offered[SRC_3]["reason"].lower())
        self.assertEqual(body["hidden_sources"], 1, "the one that can never feed a window is counted, not listed")
        self.assertNotIn("camera-four", self.layout().get_data(as_text=True))

    def test_the_reply_carries_no_device_address_and_no_internal_field(self):
        text = self.layout().get_data(as_text=True)
        self.assertEqual(IPV4.findall(text), [])
        for internal in ("source_ip", "source_mac", "input_active", base.ADDR_A):
            self.assertNotIn(internal, text)

    def test_an_unassigned_layout_is_refused_and_the_decoder_is_not_read(self):
        for key in (base.KEY_A2, base.KEY_B1):
            response = self.layout(key)
            self.assertEqual(response.status_code, 403, key)
            self.assertEqual(response.get_json()["reason"], "multiview_not_assigned")
        self.assertEqual(self.window_reads, [])

    def test_the_capability_is_required(self):
        self.grant(features=("av",), multiviews=(KEY,))
        self.assertEqual(self.layout().status_code, 403)
        self.assertEqual(self.window_reads, [])

    def test_a_malformed_key_is_a_400(self):
        for key in ("", "nonsense", base.DEC_A + "/", "/" + base.L_A1, base.ADDR_A + "/" + base.L_A1):
            self.assertEqual(self.layout(key).status_code, 400, key)
        self.assertEqual(self.window_reads, [])

    def test_a_display_that_does_not_answer_is_a_502_and_one_that_lost_multiview_a_409(self):
        self.catalog[0]["status"] = "unreachable"
        self.assertEqual(self.layout().status_code, 502)
        self.catalog[0]["status"] = "unsupported"
        self.assertEqual(self.layout().status_code, 409)

    def test_a_layout_that_cannot_be_shown_can_still_be_looked_at_with_the_reason(self):
        self.catalog[0]["layouts"][0]["showable"] = False
        self.catalog[0]["layouts"][0]["reason"] = "Video Wall is on for 198.51.100.71."
        body = self.layout().get_json()
        self.assertFalse(body["layout"]["available"])
        self.assertIn("Video Wall", body["layout"]["reason"])
        self.assertEqual(IPV4.findall(body["layout"]["reason"]), [])

    def test_a_layout_with_no_known_design_is_not_editable(self):
        self.design = []
        body = self.layout().get_json()
        self.assertFalse(body["layout"]["editable"])
        self.assertTrue(body["layout"]["editable_reason"])
        self.assertEqual(body["windows"], [])

    def test_a_layout_the_decoder_no_longer_holds_is_a_404(self):
        self.window_answer = {"ok": False, "missing": True, "error": "gone"}
        self.assertEqual(self.layout().status_code, 404)

    def test_an_unreadable_decoder_is_a_502(self):
        self.window_answer = {"ok": False, "error": "198.51.100.71 did not answer"}
        response = self.layout()
        self.assertEqual(response.status_code, 502)
        self.assertEqual(IPV4.findall(response.get_data(as_text=True)), [])

    def test_an_administrator_may_use_every_source(self):
        session = self.admin_login()
        body = self.layout(session=session).get_json()
        self.assertIn(SRC_5, [s["id"] for s in body["sources"]])


# ============================================================== applying choices
class ApplyTests(ChoiceBase):
    def test_a_choice_reaches_the_engine_as_server_resolved_arguments(self):
        response = self.apply({"a": SRC_2, "main": ""}, op_id="op-1")
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        body = response.get_json()
        self.assertEqual((body["ok"], body["status"], body["op_id"]), (True, "VERIFIED", "op-1"))
        self.assertEqual(self.composes, [(base.ADDR_A, "multiview" + base.L_A1[3:9],
                                          {"a": ADDR_S2, "main": ""}, "rev-1")])

    def test_no_change_is_allowed_and_is_just_a_show(self):
        self.assertEqual(self.apply({}).status_code, 200)
        self.assertEqual(self.composes[0][2], {})

    def test_surrounding_space_resolves_to_the_same_identity_and_widens_nothing(self):
        # The shared normaliser folds space around a MAC. What it produces is checked against the
        # same grants, so a padded identity is the identity and an ungiven one stays ungiven.
        self.assertEqual(self.apply({"a": " " + SRC_2 + chr(10)}).status_code, 200)
        self.assertEqual(self.composes[0][2], {"a": ADDR_S2})
        self.assertEqual(self.apply({"b": SRC_5 + chr(10)}).status_code, 403)
        self.assertEqual(len(self.composes), 1)

    def test_the_reply_is_built_from_named_fields_and_scrubbed(self):
        self.compose_reply = lambda *a: ({"ok": False, "status": "FAILED — ROLLED BACK",
            "error": "198.51.100.90 did not answer (0a:1b:2c:e0:00:01)", "plan": {"x": ADDR_S1}}, 200)
        response = self.apply({"a": SRC_2})
        text = response.get_data(as_text=True)
        self.assertEqual(IPV4.findall(text), [])
        self.assertNotIn("0a:1b:2c:e0:00:01", text)
        self.assertNotIn("plan", response.get_json())
        self.assertFalse(response.get_json()["ok"])

    def test_a_show_that_lit_all_but_one_window_says_so(self):
        self.compose_reply = lambda *a: ({"ok": True, "status": "PARTIAL — WINDOW NOT LOCKED",
                                          "unlocked_windows": [{"w": 1}]}, 200)
        body = self.apply({"a": SRC_2}).get_json()
        self.assertTrue(body["ok"])
        self.assertIn("did not show a picture", body["message"])

    def test_a_stale_revision_is_reported_as_changed_and_not_as_a_failure(self):
        self.compose_reply = lambda *a: ({"ok": False, "reason": "stale", "status": "CHANGED",
                                          "error": "This layout changed while you were choosing."}, 409)
        response = self.apply({"a": SRC_2}, base_revision="old")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json()["reason"], "stale")

    def test_an_engine_refusal_is_a_409_with_the_sentence(self):
        self.compose_reply = lambda *a: ({"ok": False, "status": "conflict",
            "error": "camera-one is needed at two sizes (1280x720 and 640x360)."}, 409)
        response = self.apply({"a": base.SRC_1})
        self.assertEqual(response.status_code, 409)
        self.assertIn("two sizes", response.get_json()["error"])

    def test_a_display_that_did_not_answer_is_not_a_refusal(self):
        self.compose_reply = lambda *a: ({"ok": False, "error": "did not answer"}, 502)
        self.assertEqual(self.apply({"a": SRC_2}).status_code, 502)


# ================================================== what a direct call must not do
class RefusalTests(ChoiceBase):
    def test_an_unassigned_layout_or_another_decoder_is_refused_before_the_engine(self):
        for key in (base.KEY_A2, base.KEY_B1, base.KEY_C1):
            response = self.apply({"a": SRC_2}, key=key)
            self.assertEqual(response.status_code, 403, key)
        self.assertEqual(self.composes, [])

    def test_a_source_the_account_was_not_given_is_refused_by_the_gate(self):
        response = self.apply({"a": SRC_5})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json()["reason"], "source_not_permitted")
        self.assertEqual(self.composes, [])

    def test_the_handler_refuses_an_ungiven_source_when_the_gate_does_not(self):
        # A second line of defence the first never lets a request reach: take the first out
        # of the way for this one test and assert the precondition so a failure says which
        # line it was measuring.
        original = omni_gate.SOURCE_MAP_KEYS
        omni_gate.SOURCE_MAP_KEYS = ()
        self.addCleanup(setattr, omni_gate, "SOURCE_MAP_KEYS", original)
        response = self.apply({"a": SRC_5})
        self.assertEqual(response.status_code, 403, "the handler let an ungiven source through")
        self.assertEqual(response.get_json()["reason"], "source_not_permitted")
        self.assertEqual(self.composes, [])

    def test_one_ungiven_source_among_given_ones_refuses_the_whole_request(self):
        response = self.apply({"a": SRC_2, "b": SRC_5})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.composes, [])

    def test_clearing_a_window_that_holds_a_source_the_account_was_not_given_is_allowed(self):
        response = self.apply({"b": ""})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.composes[0][2], {"b": ""})

    def test_design_fields_are_a_permission_refusal_and_reach_nothing(self):
        for field, value in (("windows", [{"cell": "x"}]), ("geometry", {}), ("canvas", "3840x2160"),
                             ("name", "Renamed"), ("rename", "x"), ("delete", True), ("create", True),
                             ("layout_definition", {}), ("width", 10), ("size", "1x1"), ("subframes", [])):
            response = self.apply({"a": SRC_2}, **{field: value})
            self.assertEqual(response.status_code, 403, field)
            self.assertEqual(response.get_json()["reason"], "layout_edit_not_permitted", field)
        self.assertEqual(self.composes, [])
        self.assertEqual(self.catalog_reads, [], "a refused design change read the decoder")

    def test_any_other_field_is_refused_not_ignored(self):
        response = self.apply({"a": SRC_2}, nonsense=1)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["reason"], "unsupported_field")
        self.assertEqual(self.composes, [])

    def test_a_window_the_layout_does_not_have_is_refused(self):
        response = self.apply({"zzz": SRC_2})
        self.assertEqual((response.status_code, response.get_json()["reason"]), (400, "bad_window"))
        self.assertEqual(self.composes, [])

    def test_malformed_requests_are_400s_that_reach_nothing(self):
        bad = [
            {"layout": KEY},                                              # no base
            {"layout": KEY, "base": "", "changes": {}},
            {"layout": KEY, "base": 5, "changes": {}},
            {"layout": KEY, "base": "x" * 65, "changes": {}},
            {"layout": KEY, "base": "r", "changes": []},
            {"layout": KEY, "base": "r", "changes": "main"},
            {"layout": KEY, "base": "r", "changes": {"a": 5}},
            {"layout": KEY, "base": "r", "changes": {"a": None}},
            {"layout": KEY, "base": "r", "changes": {"A B": SRC_2}},
            {"layout": KEY, "base": "r", "changes": {"a": ADDR_S2}},         # an address is not an identity
            {"layout": KEY, "base": "r", "changes": {"a": "camera-two"}},    # nor is a name
            {"layout": KEY, "base": "r", "changes": {"a": SRC_2[:-1]}},
            {"layout": KEY, "base": "r", "changes": {"c%d" % i: "" for i in range(17)}},
            {"layout": 5, "base": "r", "changes": {}},
            {"layout": KEY + "\n", "base": "r", "changes": {}},
        ]
        for payload in bad:
            response = self.req("POST", "/api/user/multiview_compose", None, payload)
            self.assertIn(response.status_code, (400, 403), payload)
        self.assertEqual(self.composes, [])
        self.assertEqual(self.catalog_reads, [])

    def test_a_non_object_body_is_a_400(self):
        for payload in ([], "x", 5):
            response = self.req("POST", "/api/user/multiview_compose", None, payload)
            self.assertEqual(response.status_code, 400)

    def test_a_given_source_that_is_not_in_the_inventory_is_a_404(self):
        response = self.apply({"a": SRC_6})
        self.assertEqual((response.status_code, response.get_json()["reason"]), (404, "source_not_found"))
        self.assertEqual(self.composes, [])

    def test_a_source_that_is_not_ready_is_refused_with_its_reason(self):
        response = self.apply({"a": SRC_3})
        self.assertEqual((response.status_code, response.get_json()["reason"]), (409, "source_not_ready"))
        self.assertIn("configuration", response.get_json()["error"].lower())
        response = self.apply({"a": SRC_4})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.composes, [])

    def test_a_layout_that_cannot_be_shown_is_refused(self):
        self.catalog[0]["layouts"][0]["showable"] = False
        self.catalog[0]["layouts"][0]["reason"] = "Video Wall is on."
        response = self.apply({"a": SRC_2})
        self.assertEqual((response.status_code, response.get_json()["reason"]), (409, "not_showable"))
        self.assertEqual(self.composes, [])

    def test_a_layout_with_no_known_design_is_refused(self):
        self.design = []
        response = self.apply({})
        self.assertEqual((response.status_code, response.get_json()["reason"]), (409, "layout_not_editable"))
        self.assertEqual(self.composes, [])

    def test_the_gate_refuses_a_body_naming_a_display_the_account_was_not_given(self):
        for extra in ({"decoder": base.DEC_B}, {"display": base.DEC_B}):
            self.assertEqual(self.apply({"a": SRC_2}, **extra).status_code, 403, extra)
        self.assertEqual(self.composes, [])

    def test_an_unauthenticated_request_reaches_nothing(self):
        client = self.app.test_client()
        response = client.post("/api/user/multiview_compose", json={"layout": KEY, "base": "r", "changes": {}},
                               environ_base={base.omni_remote.LISTENER_KEY: base.omni_remote.REMOTE})
        self.assertIn(response.status_code, (401, 403))
        self.assertEqual(self.composes, [])

    def test_the_csrf_token_is_required(self):
        client = self.app.test_client()
        client.set_cookie("omnisuite_session", self.session.token)
        response = client.post("/api/user/multiview_compose", json={"layout": KEY, "base": "r", "changes": {}},
                               environ_base={base.omni_remote.LISTENER_KEY: base.omni_remote.REMOTE})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.composes, [])

    def test_the_routes_are_classified_for_a_user_with_the_capability(self):
        for path in ("/api/user/multiview_layout", "/api/user/multiview_compose"):
            self.assertEqual(omni_access.ROUTES[path], (omni_access.Access.USER, omni_access.FEATURE_MULTIVIEW))

    def test_no_live_value_is_in_production_code(self):
        # The address of a lab unit must never be a literal in the code that handles requests.
        import pathlib
        root = pathlib.Path(base.ROOT)
        for name in ("omni_remote_api.py", "omni_gate.py", "omni_access.py"):
            text = (root / name).read_text(encoding="utf-8")
            self.assertFalse(re.search(r"\b192\.168\.(100|150|200|250)\.\d+\b", text), name)


# ===================================================== revocation, in every position
class RevocationTests(ChoiceBase):
    def test_withdrawing_the_layout_between_two_requests_refuses_the_second(self):
        self.assertEqual(self.apply({"a": SRC_2}).status_code, 200)
        self.grant(features=("multiview",), multiviews=(), sources=(base.SRC_1, SRC_2))
        self.assertEqual(self.apply({"a": SRC_2}).status_code, 403)
        self.assertEqual(self.layout().status_code, 403)
        self.assertEqual(len(self.composes), 1)

    def test_withdrawing_a_source_refuses_that_source_on_the_next_request_only(self):
        self.assertEqual(self.apply({"a": SRC_2}).status_code, 200)
        self.grant(features=("multiview",), multiviews=(KEY,), sources=(base.SRC_1,))
        self.assertEqual(self.apply({"a": SRC_2}).status_code, 403)
        self.assertEqual(self.apply({"a": base.SRC_1}).status_code, 200)
        self.assertNotIn(SRC_2, [s["id"] for s in self.layout().get_json()["sources"]])

    def test_withdrawing_the_capability_refuses_everything(self):
        self.grant(features=("av",), multiviews=(KEY,), sources=(base.SRC_1, SRC_2))
        self.assertEqual(self.apply({"a": SRC_2}).status_code, 403)
        self.assertEqual(self.layout().status_code, 403)

    def test_a_disabled_account_is_refused_and_a_re_enabled_one_works_again(self):
        self.store.set_enabled(self.tina.id, False)
        self.assertIn(self.apply({"a": SRC_2}).status_code, (401, 403))
        self.assertEqual(self.composes, [])
        self.store.set_enabled(self.tina.id, True)
        self.session = self.store.open_session(self.store.find_by_username("tina").id)
        self.assertEqual(self.apply({"a": SRC_2}).status_code, 200)

    def _wait_for_lock(self, revoke):
        held, release, results = threading.Event(), threading.Event(), []

        def hold():
            with self.locks.hold(self.locks.decoder(base.DEC_A)):
                held.set()
                release.wait(5)

        holder = threading.Thread(target=hold)
        holder.start()
        held.wait(5)
        waiter = threading.Thread(target=lambda: results.append(self.apply({"a": SRC_2}).status_code))
        waiter.start()
        time.sleep(0.3)
        revoke()
        release.set()
        waiter.join(10)
        holder.join(10)
        return results

    def test_a_source_withdrawn_while_waiting_for_the_lock_never_reaches_the_engine(self):
        results = self._wait_for_lock(lambda: self.grant(
            features=("multiview",), multiviews=(KEY,), sources=(base.SRC_1,)))
        self.assertEqual(results, [403])
        self.assertEqual(self.composes, [], "the engine ran after the source was withdrawn")

    def test_the_layout_withdrawn_while_waiting_for_the_lock_never_reaches_the_engine(self):
        results = self._wait_for_lock(lambda: self.grant(
            features=("multiview",), multiviews=(), sources=(base.SRC_1, SRC_2)))
        self.assertEqual(results, [403])
        self.assertEqual(self.composes, [])

    def test_the_capability_withdrawn_while_waiting_never_reaches_the_engine(self):
        results = self._wait_for_lock(lambda: self.grant(
            features=("av",), multiviews=(KEY,), sources=(base.SRC_1, SRC_2)))
        self.assertEqual(results, [403])
        self.assertEqual(self.composes, [])

    def test_a_request_that_was_never_allowed_does_not_queue_for_the_lock(self):
        held, release, finished = threading.Event(), threading.Event(), []

        def hold():
            with self.locks.hold(self.locks.decoder(base.DEC_A)):
                held.set()
                release.wait(5)

        holder = threading.Thread(target=hold)
        holder.start()
        held.wait(5)
        started = time.time()
        finished.append(self.apply({"a": SRC_5}).status_code)
        elapsed = time.time() - started
        release.set()
        holder.join(10)
        self.assertEqual(finished, [403])
        self.assertLess(elapsed, 2.0, "a refused request waited for the display's lock")

    def test_the_lock_is_the_displays_and_is_released_after_the_operation(self):
        self.assertEqual(self.apply({"a": SRC_2}).status_code, 200)
        with self.locks.hold(self.locks.decoder(base.DEC_A)):
            pass

    def test_a_busy_display_is_a_409_naming_nothing_internal(self):
        original = self.locks._timeout
        held, release, results = threading.Event(), threading.Event(), []
        self.locks._timeout = 0.3

        def hold():
            with self.locks.hold(self.locks.decoder(base.DEC_A)):
                held.set()
                release.wait(5)

        holder = threading.Thread(target=hold)
        holder.start()
        held.wait(5)
        results.append(self.apply({"a": SRC_2}))
        release.set()
        holder.join(10)
        self.locks._timeout = original
        self.assertEqual(results[0].status_code, 409)
        self.assertEqual(results[0].get_json()["reason"], "resource_busy")
        self.assertEqual(self.composes, [])


# ====================================== the lines of defence the first line never lets a request reach
class SecondLineTests(ChoiceBase):
    """Each check is tested with the check in front of it taken out of the way, and the
    precondition asserted so a failure says which of the two lines it was measuring."""

    def _without_gate_source_check(self):
        original = omni_gate.SOURCE_MAP_KEYS
        self.assertEqual(original, ("changes",), "precondition: the gate checks the map")
        omni_gate.SOURCE_MAP_KEYS = ()
        self.addCleanup(setattr, omni_gate, "SOURCE_MAP_KEYS", original)

    def test_the_gate_alone_refuses_an_ungiven_source_in_the_map(self):
        # The gate's decision, asked directly, so it cannot be confused with the handler's.
        decision = omni_gate.evaluate(
            "/api/user/multiview_compose", "POST",
            {base.omni_remote.LISTENER_KEY: base.omni_remote.REMOTE,
             "HTTP_X_OMNISUITE_CSRF": self.session.csrf_token,
             "HTTP_COOKIE": "omnisuite_session=" + self.session.token},
            {"layout": KEY, "base": "r", "changes": {"a": SRC_5}}, self.store)
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "source_not_permitted")
        decision = omni_gate.evaluate(
            "/api/user/multiview_compose", "POST",
            {base.omni_remote.LISTENER_KEY: base.omni_remote.REMOTE,
             "HTTP_X_OMNISUITE_CSRF": self.session.csrf_token,
             "HTTP_COOKIE": "omnisuite_session=" + self.session.token},
            {"layout": KEY, "base": "r", "changes": {"a": SRC_2}}, self.store)
        self.assertTrue(decision.allowed, "a given source must pass the gate")

    def test_the_handler_refuses_an_address_or_a_name_or_trailing_text_as_a_source(self):
        self._without_gate_source_check()
        for value in (ADDR_S2, "camera-two", SRC_2 + "zz", SRC_2[:-1]):
            response = self.apply({"a": value})
            self.assertEqual((response.status_code, response.get_json()["reason"]), (400, "bad_request"), value)
        self.assertEqual(self.composes, [])

    def test_a_given_identity_that_is_not_a_multiview_source_is_refused(self):
        # An account may be given a unit that is in the inventory but is not an encoder row the
        # Multiview list knows (here: a decoder). It has an address, so only the source-list check
        # stops it reaching the engine.
        self.grant(features=("multiview",), multiviews=(KEY,), sources=(base.SRC_1, base.DEC_B))
        response = self.apply({"a": base.DEC_B})
        self.assertEqual((response.status_code, response.get_json()["reason"]), (404, "source_not_found"))
        self.assertEqual(self.composes, [])

    def test_the_layout_that_was_assigned_is_the_one_that_is_read_and_composed(self):
        # Two layouts on one decoder, the SECOND assigned: nothing may answer for the first.
        self.grant(features=("multiview",), multiviews=(base.KEY_A2,), sources=(base.SRC_1, SRC_2))
        body = self.layout(base.KEY_A2).get_json()
        self.assertEqual(body["layout"]["name"], "Side by side")
        self.assertEqual(self.apply({"a": SRC_2}, key=base.KEY_A2).status_code, 200)
        self.assertEqual(self.composes[-1][1], "multiview" + base.L_A2[3:9])
        self.assertEqual(self.window_reads[-1][1], "multiview" + base.L_A2[3:9])

    def test_the_handler_refuses_a_missing_capability_when_the_gate_does_not(self):
        self.grant(features=("av",), multiviews=(KEY,), sources=(base.SRC_1,))
        for rule, call in (("/api/user/multiview_layout", self.layout),
                           ("/api/user/multiview_compose", lambda: self.apply({}))):
            held = omni_access.ROUTES[rule]
            self.assertEqual(held[1], omni_access.FEATURE_MULTIVIEW, "precondition: the gate demands it")
            omni_access.ROUTES[rule] = (omni_access.Access.AUTHENTICATED, None)
            try:
                response = call()
            finally:
                omni_access.ROUTES[rule] = held
            self.assertEqual(response.status_code, 403, rule)
            self.assertEqual(response.get_json()["reason"], "missing_feature:multiview", rule)
        self.assertEqual(self.composes, [])
        self.assertEqual(self.window_reads, [])

    def test_a_request_the_handler_refuses_before_the_wait_does_not_queue_for_the_lock(self):
        # An UNASSIGNED layout on the busy display: the gate has no opinion about layouts, so the
        # handler's check before the wait is the only thing that can refuse it quickly.
        self.grant(features=("multiview",), multiviews=(KEY,), sources=(base.SRC_1, SRC_2))
        held, release = threading.Event(), threading.Event()

        def hold():
            with self.locks.hold(self.locks.decoder(base.DEC_A)):
                held.set()
                release.wait(8)

        holder = threading.Thread(target=hold)
        holder.start()
        held.wait(5)
        try:
            started = time.time()
            response = self.apply({"a": SRC_2}, key=base.KEY_A2)
            elapsed = time.time() - started
        finally:
            release.set()
            holder.join(10)
        self.assertEqual(response.status_code, 403)
        self.assertLess(elapsed, 2.0, "a refused request waited for the display's lock")

    def test_an_ungiven_source_is_refused_before_the_wait_when_the_gate_is_out_of_the_way(self):
        self._without_gate_source_check()
        held, release = threading.Event(), threading.Event()

        def hold():
            with self.locks.hold(self.locks.decoder(base.DEC_A)):
                held.set()
                release.wait(8)

        holder = threading.Thread(target=hold)
        holder.start()
        held.wait(5)
        try:
            started = time.time()
            response = self.apply({"a": SRC_5})
            elapsed = time.time() - started
        finally:
            release.set()
            holder.join(10)
        self.assertEqual((response.status_code, response.get_json()["reason"]), (403, "source_not_permitted"))
        self.assertLess(elapsed, 2.0, "the handler's own check waited for the lock")
        self.assertEqual(self.composes, [])



class AuditTests(ChoiceBase):
    def test_one_operator_action_is_one_entry_naming_fields_not_the_body(self):
        self.apply({"a": SRC_2})
        entries = [e for e in self.store.audit_entries() if e.get("event") == "multiview.compose"] \
            if hasattr(self.store, "audit_entries") else None
        if entries is None:
            self.skipTest("this store exposes no read of the audit log")
        self.assertEqual(len(entries), 1)
        detail = entries[0]["detail"]
        self.assertEqual(sorted(detail), ["display", "display_label", "layout", "windows_changed"])


class AdminUseTests(ChoiceBase):
    def test_an_administrator_may_choose_any_source(self):
        session = self.admin_login()
        response = self.apply({"a": SRC_5}, session=session)
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertEqual(self.composes[0][2], {"a": ADDR_S5})


if __name__ == "__main__":
    unittest.main()
