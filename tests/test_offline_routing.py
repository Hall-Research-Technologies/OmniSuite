"""A unit that is not answering is not routable, and the server is the one that says so.

Offline units stay visible -- hiding one tells the operator their equipment was taken away rather
than that it is not answering -- but they cannot be chosen as a source or a destination, and the
disabled control is never the only thing standing in the way. These hold:

  * what the liveness record is and is not: fed by reads that already happen, believed for a
    bounded time, re-probed once when stale, and never a timer or a thread;
  * the engine refuses a route to or from a unit that is not answering, before anything is read or
    written, with a 409 that names the role and carries no address in `error`;
  * an unknown unit is not an offline one: only a unit seen not to answer is refused;
  * the screens' state carries what is known, and a unit that was recorded offline and came back is
    not left disabled;
  * a restricted account gets the sentence and never the address; a group reports the member that
    did not take the route instead of pretending the room changed;
  * the USB pairing entry point for the integrated endpoints is held to the same rule.

Nothing here reaches a network: the probe is the test's, and the WebSocket transport is fenced.
"""
import copy
import threading
from concurrent.futures import ThreadPoolExecutor

try:
    from . import test_usb_integration as base
    from . import test_user_routing as harness
    from ._wsrecord import RecordingWebSocket
except ImportError:                                   # loaded by path, no package
    import test_usb_integration as base
    import test_user_routing as harness
    from _wsrecord import RecordingWebSocket

import omni_groups
import omni_liveness
import omni_projection
import omni_users
from omni_users import Permissions

srv = base.srv

ENC_IP, DEC_IP = "192.0.2.11", "192.0.2.21"
ENC = {"ip": ENC_IP, "mac": "b8:98:b0:00:00:01", "hostname": "enc-one", "model": "HW-OMNI-E4111",
       "role": "encoder", "system_mode": "AVoIP"}
DEC = {"ip": DEC_IP, "mac": "b8:98:b0:00:00:11", "hostname": "dec-one", "model": "HW-OMNI-D4111",
       "role": "decoder", "system_mode": "AVoIP"}


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


# ----------------------------------------------------------------------------- the record itself
class LivenessRecordTests(base.unittest.TestCase):

    def make(self, offline=()):
        self.clock = Clock()
        self.probed = []

        def probe(ip):
            self.probed.append(ip)
            return ip not in offline

        return omni_liveness.UnitLiveness(probe, now=self.clock)

    def test_nothing_observed_is_unknown_not_offline(self):
        live = self.make()
        self.assertIsNone(live.known("192.0.2.5"))

    def test_recording_asks_nothing(self):
        live = self.make()
        live.record("192.0.2.5", False, "verify")
        self.assertIs(live.known("192.0.2.5"), False)
        self.assertEqual(self.probed, [])

    def test_a_fresh_online_observation_is_believed_without_a_probe(self):
        live = self.make()
        live.record("192.0.2.5", True, "poll")
        self.clock.t += omni_liveness.FRESH_ONLINE_SECONDS - 1
        self.assertTrue(live.check("192.0.2.5"))
        self.assertEqual(self.probed, [])

    def test_a_stale_observation_is_asked_again_once_and_the_answer_kept(self):
        live = self.make()
        live.record("192.0.2.5", True, "poll")
        self.clock.t += omni_liveness.FRESH_ONLINE_SECONDS + 1
        self.assertTrue(live.check("192.0.2.5"))
        self.assertTrue(live.check("192.0.2.5"))
        self.assertEqual(self.probed, ["192.0.2.5"], "the second operation used the first one's answer")

    def test_not_answering_is_believed_for_less_time_than_answering(self):
        self.assertLess(omni_liveness.FRESH_OFFLINE_SECONDS, omni_liveness.FRESH_ONLINE_SECONDS)
        live = self.make(offline={"192.0.2.5"})
        live.record("192.0.2.5", False, "verify")
        self.clock.t += omni_liveness.FRESH_OFFLINE_SECONDS + 1
        self.assertFalse(live.check("192.0.2.5"))
        self.assertEqual(self.probed, ["192.0.2.5"], "a unit that has just been plugged in is looked at again")

    def test_a_probe_that_raises_is_not_answering(self):
        def boom(ip):
            raise OSError("no route")
        live = omni_liveness.UnitLiveness(boom, now=Clock())
        self.assertFalse(live.check("192.0.2.5"))

    def test_an_empty_address_is_never_a_live_unit(self):
        live = self.make()
        self.assertFalse(live.check(""))
        self.assertFalse(live.check(None))
        live.record("", True)
        self.assertEqual(live.snapshot(), {})

    def test_many_units_are_probed_together_once_each(self):
        gate = threading.Barrier(3, timeout=5)
        seen = []

        def probe(ip):
            seen.append(ip)
            gate.wait()                      # only passes if all three are being probed at once
            return True
        live = omni_liveness.UnitLiveness(probe, now=Clock())
        answers = live.check_many(["192.0.2.1", "192.0.2.2", "192.0.2.3", "192.0.2.1"])
        self.assertEqual(answers, {"192.0.2.1": True, "192.0.2.2": True, "192.0.2.3": True})
        self.assertEqual(sorted(seen), ["192.0.2.1", "192.0.2.2", "192.0.2.3"], "a repeated address costs one probe")

    def test_refresh_offline_touches_only_units_recorded_offline_and_stale(self):
        live = self.make()
        live.record("192.0.2.1", True, "poll")
        live.record("192.0.2.2", False, "verify")
        live.record("192.0.2.3", False, "verify")
        self.clock.t += 5
        live.record("192.0.2.4", False, "verify")          # recorded just now
        self.clock.t += 6
        looked = live.refresh_offline(min_age=10.0)
        self.assertEqual(sorted(looked), ["192.0.2.2", "192.0.2.3"])
        self.assertEqual(sorted(self.probed), ["192.0.2.2", "192.0.2.3"], "nothing that answers is asked again")
        self.assertIs(live.known("192.0.2.2"), True, "a unit that came back stops showing as offline")

    def test_with_every_unit_answering_a_refresh_does_nothing_at_all(self):
        live = self.make()
        live.record("192.0.2.1", True, "poll")
        self.clock.t += 600
        self.assertEqual(live.refresh_offline(), [])
        self.assertEqual(self.probed, [])

    def test_forget_drops_one_unit_or_all_of_them(self):
        live = self.make()
        live.record("192.0.2.1", True)
        live.record("192.0.2.2", False)
        live.forget("192.0.2.1")
        self.assertIsNone(live.known("192.0.2.1"))
        self.assertIs(live.known("192.0.2.2"), False)
        live.forget()
        self.assertEqual(live.snapshot(), {})

    def test_it_has_no_timer_and_starts_no_thread_of_its_own(self):
        with open(omni_liveness.__file__, encoding="utf-8") as handle:
            source = handle.read()
        for forbidden in ("threading.Timer", "threading.Thread(", "setInterval", "while True", "sched."):
            self.assertNotIn(forbidden, source)
        before = threading.active_count()
        live = self.make()
        live.record("192.0.2.1", False)
        live.check("192.0.2.2")
        self.assertEqual(threading.active_count(), before)

    def test_the_real_probe_is_answered_by_the_test_package_not_a_socket(self):
        """The fence installs the answer; with it removed the transport fence still refuses the connect."""
        self.assertTrue(omni_liveness.tcp_probe("192.0.2.77"))
        previous = omni_liveness._probe_override
        omni_liveness.set_probe_override(None)
        try:
            self.assertFalse(omni_liveness.tcp_probe("192.0.2.77", timeout=0.2),
                             "an unanswered connect is a unit that is not answering")
        finally:
            omni_liveness.set_probe_override(previous)


# ------------------------------------------------------------------------------ the engine's gate
class EngineGateTests(RecordingWebSocket, base.ServerTestBase):

    def setUp(self):
        super().setUp()
        self.install_recording_ws()
        self._patch_cache([ENC, DEC])
        self.clock = Clock()
        self.probed = []
        self.offline = set()

        def probe(ip):
            self.probed.append(ip)
            return ip not in self.offline

        self.live = omni_liveness.UnitLiveness(probe, now=self.clock)
        previous = srv._liveness
        srv._liveness = self.live
        self.addCleanup(lambda: setattr(srv, "_liveness", previous))

    def route(self, **extra):
        body = {"encoder": ENC_IP, "decoder": DEC_IP, "mode": "av"}
        body.update(extra)
        return self.client.post("/api/route", json=body)

    def assertNothingWasSent(self):
        self.assertEqual(self.ws_requested, [], "refused before any device was read")
        self._assert_no_mutation_attempted()

    def test_a_source_that_is_not_answering_is_refused_by_name_before_anything_is_sent(self):
        self.offline = {ENC_IP}
        reply = self.route()
        body = reply.get_json()
        self.assertEqual(reply.status_code, 409)
        self.assertEqual(body["reason"], "unit_offline")
        self.assertEqual(body["status"], "UNIT OFFLINE")
        self.assertFalse(body["ok"])
        self.assertIn("source", body["error"])
        self.assertIn("nothing was changed", body["error"])
        self.assertEqual(body["offline"], [{"role": "source", "ip": ENC_IP}])
        self.assertNothingWasSent()

    def test_a_display_that_is_not_answering_is_refused_by_name(self):
        self.offline = {DEC_IP}
        body = self.route().get_json()
        self.assertEqual(body["offline"], [{"role": "display", "ip": DEC_IP}])
        self.assertIn("display", body["error"])
        self.assertNothingWasSent()

    def test_both_are_named_when_neither_answers(self):
        self.offline = {ENC_IP, DEC_IP}
        body = self.route().get_json()
        self.assertTrue(body["error"].startswith("Neither the source nor the display"))
        self.assertEqual(sorted(o["role"] for o in body["offline"]), ["display", "source"])

    def test_the_sentence_carries_no_address_because_a_restricted_account_is_shown_it(self):
        self.offline = {ENC_IP, DEC_IP}
        body = self.route().get_json()
        for address in (ENC_IP, DEC_IP, "192.0.2."):
            self.assertNotIn(address, body["error"])
            self.assertNotIn(address, body["status"])

    def test_the_refusal_comes_before_every_other_check(self):
        """A codec mismatch on a unit that is not answering is still a unit that is not answering."""
        self._patch_cache([ENC, dict(DEC, system_mode="Other")])
        self.offline = {DEC_IP}
        self.assertEqual(self.route().status_code, 409)

    def test_a_unit_that_answers_is_not_held_up_by_the_gate(self):
        self._patch_cache([ENC, dict(DEC, system_mode="Other")])
        reply = self.route()
        self.assertEqual(reply.status_code, 200)
        self.assertIn("Codec mismatch", reply.get_json()["error"], "it reached the engine's own checks")
        self.assertNothingWasSent()

    def test_a_unit_nothing_has_looked_at_is_probed_once_and_not_assumed_offline(self):
        self.assertIsNone(self.live.known(ENC_IP))
        self._patch_cache([ENC, dict(DEC, system_mode="Other")])
        self.assertEqual(self.route().status_code, 200)
        self.assertEqual(sorted(self.probed), sorted([DEC_IP, ENC_IP]))
        self.route()
        self.assertEqual(sorted(self.probed), sorted([DEC_IP, ENC_IP]), "the second route inside the window asked nobody")

    def test_a_probe_that_cannot_run_is_a_unit_that_is_not_answering(self):
        def boom(ip):
            raise OSError("no route")
        srv._liveness = omni_liveness.UnitLiveness(boom, now=self.clock)
        self.assertEqual(self.route().status_code, 409)

    def test_missing_fields_and_unknown_units_keep_their_own_answers(self):
        self.assertEqual(self.client.post("/api/route", json={"decoder": DEC_IP}).status_code, 400)
        unknown = self.client.post("/api/route", json={"encoder": "192.0.2.99", "decoder": DEC_IP})
        self.assertIn("not found", unknown.get_json()["error"])
        self.assertEqual(self.probed, [], "an unknown unit is not probed on its way to being refused")

    def test_a_route_to_a_unit_that_answered_a_moment_ago_does_not_probe(self):
        self._patch_cache([ENC, dict(DEC, system_mode="Other")])
        self.live.record(ENC_IP, True, "poll")
        self.live.record(DEC_IP, True, "poll")
        self.route()
        self.assertEqual(self.probed, [])

    # -- what the screens are told ---------------------------------------------------------------
    def state(self):
        body = self.client.get("/api/state").get_json()
        return ({e["ip"]: e for e in body["encoders"]}, {d["ip"]: d for d in body["decoders"]})

    def test_state_says_what_is_known_and_is_silent_otherwise(self):
        self.live.record(DEC_IP, False, "verify")
        encoders, decoders = self.state()
        self.assertIs(decoders[DEC_IP]["online"], False)
        self.assertNotIn("online", encoders[ENC_IP], "no observation is not 'online'")

    def test_a_unit_that_came_back_does_not_stay_disabled(self):
        self.live.record(DEC_IP, False, "verify")
        self.clock.t += 11
        _encoders, decoders = self.state()
        self.assertIs(decoders[DEC_IP]["online"], True)
        self.assertEqual(self.probed, [DEC_IP], "only the unit that had gone quiet was looked at")

    def test_a_unit_that_is_still_down_is_not_looked_at_on_every_read(self):
        self.offline = {DEC_IP}
        self.live.record(DEC_IP, False, "verify")
        self.clock.t += 11
        self.state()
        self.state()
        self.state()
        self.assertEqual(self.probed, [DEC_IP], "ten seconds apart at most, however often the page reads")

    def test_clearing_the_inventory_forgets_what_was_known(self):
        self.live.record(DEC_IP, False, "verify")
        self.assertEqual(self.client.post("/api/clear_units", json={}).status_code, 200)
        self.assertIsNone(self.live.known(DEC_IP))

    def test_removing_a_unit_forgets_it_and_only_it(self):
        self.live.record(DEC_IP, False, "verify")
        self.live.record(ENC_IP, False, "verify")
        self.client.post("/api/remove_units", json={"ips": [DEC_IP]})
        self.assertIsNone(self.live.known(DEC_IP))
        self.assertIs(self.live.known(ENC_IP), False)

    # -- the verification sweep already reads every cached unit ----------------------------------
    def test_the_sweep_records_what_it_found_without_asking_anyone_else(self):
        def answer(url, name):
            return {"config": {"hostname": "dec-one", "firmwareversion": "2.1.2"}} if DEC_IP in url else None
        self.ws_read = answer
        srv._verify_cache_in_background()
        srv.await_startup_tasks()
        self.assertIs(self.live.known(DEC_IP), True, "it answered")
        self.assertIs(self.live.known(ENC_IP), False, "it did not, after every attempt")
        self.assertEqual(self.probed, [], "recording asked no unit anything")


# ------------------------------------------------------------------------------- USB pairing
class UsbPairGateTests(RecordingWebSocket, base.ServerTestBase):

    def setUp(self):
        super().setUp()
        self.install_recording_ws()
        self.offline = set()
        self.live = omni_liveness.UnitLiveness(lambda ip: ip not in self.offline, now=Clock())
        previous = srv._liveness
        srv._liveness = self.live
        self.addCleanup(lambda: setattr(srv, "_liveness", previous))

    def pair(self):
        return self.client.post("/api/usb_pair", json={"lex": base.E4521_IP, "rex": base.D4511_IP})

    def test_a_parent_that_is_not_answering_refuses_the_pairing_before_any_read(self):
        self.offline = {base.D4511_IP}
        reply = self.pair()
        body = reply.get_json()
        self.assertEqual(reply.status_code, 409)
        self.assertEqual(body["reason"], "unit_offline")
        self.assertIn("USB device", body["error"])
        self.assertEqual(body["offline"], [{"role": "usb_device", "ip": base.D4511_IP}])
        self.assertEqual(self.ws_requested, [], "nothing was read, so nothing was released")
        self._assert_no_mutation_attempted()

    def test_both_parents_are_named_when_neither_answers(self):
        self.offline = {base.E4521_IP, base.D4511_IP}
        body = self.pair().get_json()
        self.assertTrue(body["error"].startswith("Neither the USB host nor the USB device"))

    def test_parents_that_answer_reach_the_existing_transaction_unchanged(self):
        reply = self.pair()
        self.assertNotEqual(reply.status_code, 409, "the gate did not refuse a pairing between units that answer")
        self.assertTrue(self.ws_requested, "the existing transaction began with its own reads")
        self._assert_no_mutation_attempted()


# --------------------------------------------------------------------- a restricted account
LIVE_A = "b8:98:b0:00:00:31"
LIVE_B = "b8:98:b0:00:00:32"
LIVE_C = "b8:98:b0:00:00:33"
GROUP_UNITS = copy.deepcopy(harness.UNITS) + [
    {"ip": "192.0.2.31", "mac": LIVE_A, "hostname": "dec-a", "role": "decoder", "model": "hw-omni-d4511"},
    {"ip": "192.0.2.32", "mac": LIVE_B, "hostname": "dec-b", "role": "decoder", "model": "hw-omni-d4511"},
    {"ip": "192.0.2.33", "mac": LIVE_C, "hostname": "dec-c", "role": "decoder", "model": "hw-omni-d4511"},
]


class RestrictedAccountTests(harness.UserRoutingTestBase):

    def setUp(self):
        super().setUp()
        self._patch_registered("load_cache", lambda: copy.deepcopy(GROUP_UNITS))
        self.groups = omni_groups.GroupStore(self.dir / "display_groups.json", omni_users._normalise_identity)
        self._patch_registered("groups", self.groups)
        self.group = self.groups.create("Front rooms", [LIVE_A, LIVE_B, LIVE_C])["group"]
        self.store.set_permissions(self.teacher.id, Permissions(
            features=("av",), sources=[harness.TEACHER_PC],
            destinations=[harness.FRONT_DISPLAY], groups=[self.group["id"]]))
        self.teacher = self.store.find_by_username("teacher")
        self.offline_ips = set()

        def engine_with_liveness():
            from flask import jsonify, request as flask_request
            body = flask_request.get_json(silent=True) or {}
            self.routes.append(dict(body))
            down = {role: ip for role, ip in (("source", body.get("encoder")), ("display", body.get("decoder")))
                    if ip in self.offline_ips}
            if down:
                # The engine's own refusal, produced by the engine's own function.
                live = omni_liveness.UnitLiveness(lambda ip: ip not in self.offline_ips, now=Clock())
                previous = self.srv._liveness
                self.srv._liveness = live
                try:
                    return self.srv._unit_offline_refusal(**{r: i for r, i in
                                                             (("source", body["encoder"]), ("display", body["decoder"]))})
                finally:
                    self.srv._liveness = previous
            return jsonify({"ok": True, "status": "VERIFIED"}), 200

        self._patch_registered("route_view", engine_with_liveness)

    def test_a_restricted_account_is_told_which_end_is_not_answering_and_shown_no_address(self):
        self.offline_ips = {"192.0.2.11"}
        reply = self.route(self.session_for(self.teacher))
        body = reply.get_json()
        self.assertEqual(reply.status_code, 409)
        self.assertEqual(body["reason"], "unit_offline")
        self.assertIn("source is not answering", body["error"])
        self.assertNotIn("offline", body, "the administrative detail is not forwarded")
        text = reply.get_data(as_text=True)
        for secret in ("192.0.2.", harness.TEACHER_PC, "b8:98"):
            self.assertNotIn(secret, text)

    def test_the_page_is_told_the_unit_is_offline_and_may_not_choose_it(self):
        self.assertFalse(omni_projection.project_source({"online": False}, {}, harness.TEACHER_PC)["online"])
        self.assertFalse(omni_projection.project_destination(
            {"online": False}, {}, harness.FRONT_DISPLAY, {"mode": "none"})["online"])
        self.assertTrue(omni_projection.project_source({}, {}, harness.TEACHER_PC)["online"],
                        "no observation is not an offline unit")

    def test_a_group_reports_the_member_that_did_not_take_the_route_and_routes_the_rest(self):
        self.offline_ips = {"192.0.2.32"}
        reply = self.post("/api/user/route_group",
                          {"source": harness.TEACHER_PC, "group": self.group["id"]},
                          self.session_for(self.teacher))
        body = reply.get_json()
        self.assertEqual((body["routed"], body["total"]), (2, 3), "never reported as the whole room changing")
        self.assertFalse(body["ok"])
        failed = [r for r in body["results"] if not r["ok"]]
        self.assertEqual(len(failed), 1)
        self.assertEqual(failed[0]["name"], "dec-b")
        self.assertEqual(failed[0]["result"], "unit offline")
        self.assertEqual(len(self.routes), 3, "the other members were still routed")
        text = reply.get_data(as_text=True)
        for secret in ("192.0.2.", LIVE_A, LIVE_B, "b8:98"):
            self.assertNotIn(secret, text)

    def test_a_group_with_every_member_offline_routes_nobody(self):
        self.offline_ips = {"192.0.2.31", "192.0.2.32", "192.0.2.33"}
        body = self.post("/api/user/route_group",
                         {"source": harness.TEACHER_PC, "group": self.group["id"]},
                         self.session_for(self.teacher)).get_json()
        self.assertEqual(body["routed"], 0)
        self.assertFalse(body["ok"])


# ------------------------------------------------------------------------------ the matrix page
class MatrixPageTests(base.unittest.TestCase):
    """The page's own rules, read from its source with comments removed. The behaviour is driven in
    tests/matrix_offline_test.js; this holds the wiring that test cannot see."""

    def source(self):
        import re
        from pathlib import Path
        text = (Path(__file__).resolve().parent.parent / "ui" / "matrix" / "matrix.js").read_text(encoding="utf-8")
        return re.sub(r"^\s*//.*$", "", re.sub(r"/\*[\s\S]*?\*/", "", text), flags=re.M)

    def test_no_decision_to_offer_a_route_is_made_without_asking_whether_the_unit_answers(self):
        text = self.source()
        self.assertIn("unitIsOffline(encoderUnit) || unitIsOffline(decoderUnit)", text, "a click that gets past the control is refused")
        self.assertIn("!unitIsOffline(targetUnit)", text, "a group selection drops units that are not answering")
        self.assertIn("offline-blocked", text)


if __name__ == "__main__":
    base.unittest.main()
