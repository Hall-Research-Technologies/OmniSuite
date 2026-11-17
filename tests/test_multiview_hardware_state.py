"""Multiview: what the devices HOLD after an operation, not what the API answered.

A 200 with the wrong scaler is a failure, so every test here reads the simulated
devices after the operation and compares the configuration values with what the
selected layout and source assignment require. The simulator is the one in
test_multiview.py (merge-by-name, method-only create/delete, silently ignored
invalid writes); nothing touches a socket and every address is an RFC 5737
documentation address.

What is covered, in terms of the thing an operator asked for:

  * each of the eleven layouts at 1920x1080: Encoder 2 scaler, Encoder 2 input,
    Encoder 1 untouched, Session 2, decoder pool inputs, the source in the right
    window, bitrates, output resolution and audio;
  * rotating sources through the windows, with no stale source left behind;
  * A -> B -> A transitions where Encoder 2 changes every time;
  * a repeated Show writes nothing;
  * failure paths report the failure with a structured reason and leave the
    devices as they were;
  * save over the active object, then Show again;
  * create -> show -> modify -> show -> delete;
  * a source encoder that only accepts the fallback password.
"""
import copy
import unittest
from unittest import mock

try:
    from . import test_multiview as base
except ImportError:                                  # loaded by path
    import test_multiview as base

mv = base.mv
srv = base.srv

SOURCES = (base.ENCODER_IP, base.ENCODER2_IP, base.NEW_WALLPLATE_IP, base.PLAIN_111_IP)
POOL = mv.WINDOW_IP_INPUTS
SIZES_BY_LAYOUT = {name: [(w["width"], w["height"]) for w in
                          mv.compute_windows(name, 1920, 1080)["windows"]]
                   for name in mv.LAYOUT_ORDER}


def cells(layout):
    return mv.compute_windows(layout, 1920, 1080)["windows"]


def distinct_assignment(layout, sources=SOURCES, offset=0):
    """A different source in every window: legal in every layout, whatever the sizes."""
    windows = cells(layout)
    return {w["cell"]: sources[(i + offset) % len(sources)] for i, w in enumerate(windows)}


def expected_targets(layout):
    """The Encoder 2 target per cell, from the policy: equal 200, main 300, small 150."""
    sizes = SIZES_BY_LAYOUT[layout]
    distinct = set(sizes)
    if len(distinct) == 1:
        return {w["cell"]: 200 for w in cells(layout)}
    largest = max(distinct, key=lambda s: s[0] * s[1])
    return {w["cell"]: 300 if (w["width"], w["height"]) == largest else 150
            for w in cells(layout)}


class HardwareStateBase(base.MultiviewTestBase):
    """A decoder, four encoders that can feed Multiview, and readers that go to the devices."""

    ENCODER1_BITRATE = 500            # headroom 400, so no target is cut by the 900 ceiling

    def setUp(self):
        super().setUp()
        self.dec = self.decoder()
        for ip in SOURCES:
            self.encoder(ip, vc2=base._vc2(encoder1_bitrate=self.ENCODER1_BITRATE))

    # ---- readers: straight from the simulated devices -------------------------

    def e1(self, ip):
        return next(e for e in self.devices[ip].nodes["vc2"] if e["name"] == "vc2_encoder1")

    def e2(self, ip):
        return next(e for e in self.devices[ip].nodes["vc2"] if e["name"] == "vc2_encoder2")

    def session(self, ip, name="session2"):
        return next(s for s in self.devices[ip].nodes["sessions"] if s["name"] == name)

    def s2_destination(self, ip):
        return self.session(ip)["video"]["stream"]["destination_address"]

    def s1_audio_destination(self, ip):
        return self.session(ip, "session1")["audio"]["stream"]["destination_address"]

    def scaler(self, ip):
        s = self.e2(ip)["scaler"]
        return (s.get("width"), s.get("height")) if s.get("enable") else None

    def pool_input(self, name):
        return next(i for i in self.dec.nodes["ip_input"] if i["name"] == name)

    def obj(self, name):
        return next(o for o in self.dec.nodes["multiview"] if o["name"] == name)

    def subframe(self, name, cell):
        return next(s for s in self.obj(name)["subframes"]
                    if s["name"].split(" (")[0] == cell)

    def vc2_writes(self, ip, encoder):
        return [w for w in self.devices[ip].writes
                if w[0] == "config_set" and w[1] == "vc2"
                and any(e.get("name") == encoder for e in w[2])]

    def all_writes(self):
        """Every write that reached any device, as (ip, kind, node)."""
        return [(ip, w[0], w[1]) for ip, d in self.devices.items() for w in d.writes]

    def snapshot(self):
        """Every device's configuration, with the read-only status noise removed."""
        def clean(value):
            if isinstance(value, dict):
                return {k: clean(v) for k, v in value.items()
                        if k not in ("status", "packets", "active")}
            if isinstance(value, list):
                return [clean(v) for v in value]
            return value
        return {ip: clean(copy.deepcopy(d.nodes)) for ip, d in self.devices.items()}

    # ---- the operator's actions ---------------------------------------------

    def store(self, friendly, layout, assignment, **extra):
        body = self.save(layout=layout, name=friendly, assignments=assignment, **extra).get_json()
        self.assertTrue(body["ok"], body)
        return self.offer(body["plan"]["object_name"])

    def show(self, name):
        response = self.recall(name)
        return response.status_code, response.get_json()

    def show_ok(self, name):
        status, body = self.show(name)
        self.assertEqual(status, 200, body)
        self.assertTrue(body["ok"], body)
        self.assertEqual(body["status"], "VERIFIED", body)
        return body

    # ---- the assertion that matters -------------------------------------------

    def assert_hardware_matches(self, name, layout, assignment, strict_pool=True):
        """Every layer agrees: preset, encoders, decoder inputs, window position, output."""
        targets = expected_targets(layout)
        by_cell = {w["cell"]: w for w in cells(layout)}
        assigned = {cell: ip for cell, ip in assignment.items() if ip}
        pool_use = {}
        for cell, ip in assigned.items():
            window = by_cell[cell]
            size = (window["width"], window["height"])
            # --- Encoder 2 carries the window, Encoder 1 is left alone
            self.assertEqual(self.scaler(ip), size,
                             "%s/%s: Encoder 2 scaler on %s" % (layout, cell, ip))
            self.assertEqual(self.e2(ip)["input"], self.e1(ip)["input"],
                             "%s/%s: Encoder 2 is not fed from Encoder 1's input" % (layout, cell))
            self.assertTrue(self.e2(ip)["input"], "%s/%s: Encoder 2 is Not used" % (layout, cell))
            self.assertEqual(self.vc2_writes(ip, "vc2_encoder1"), [],
                             "%s: Encoder 1 on %s was written" % (layout, ip))
            self.assertEqual(self.e1(ip)["bitrate"], self.ENCODER1_BITRATE)
            self.assertFalse(self.e1(ip)["scaler"]["enable"])
            # --- bitrate policy: target, capped by what Encoder 1 leaves of 900
            want = min(targets[cell], 900 - self.e1(ip)["bitrate"])
            self.assertEqual(self.e2(ip)["bitrate"], want,
                             "%s/%s: Encoder 2 bitrate on %s" % (layout, cell, ip))
            # --- Session 2 transmits and does not announce itself
            stream = self.session(ip)["video"]["stream"]
            self.assertTrue(stream["enabled"], "%s: Session 2 on %s not transmitting" % (layout, ip))
            self.assertFalse(self.session(ip)["sap"]["enabled"],
                             "%s: Session 2 on %s announces via SAP" % (layout, ip))
            self.assertEqual(self.session(ip)["video"]["encoder"], "vc2_encoder2")
            # --- the window is wired to that source, in that position
            sub = self.subframe(name, cell)
            self.assertIn(sub["input"], POOL, "%s/%s: not a reserved pool input" % (layout, cell))
            inp = self.pool_input(sub["input"])
            self.assertTrue(inp["enabled"])
            self.assertEqual(inp["multicast"]["address"], self.s2_destination(ip),
                             "%s/%s: window carries a stream that is not %s's Session 2"
                             % (layout, cell, ip))
            self.assertEqual(inp["port"], 1000)
            pool_use.setdefault(inp["name"], set()).add(ip)
        # one input, one stream
        for input_name, owners in pool_use.items():
            self.assertEqual(len(owners), 1, "%s carries two sources: %s" % (input_name, owners))
        # nothing enabled in the pool that no window needs
        needed = {self.subframe(name, c)["input"] for c in assigned}
        enabled = {i["name"] for i in self.dec.nodes["ip_input"]
                   if i["enabled"] and i["name"] in POOL}
        if strict_pool:
            self.assertEqual(enabled, needed,
                             "%s: pool inputs left enabled that nothing uses" % layout)
        else:
            self.assertTrue(needed <= enabled, "%s: a needed pool input is off" % layout)
        # empty windows stay empty and consume nothing
        for cell in by_cell:
            if cell not in assigned:
                self.assertEqual(self.subframe(name, cell)["input"], "")
        # --- output
        hdmi = self.dec.nodes["hdmi_output"][0]
        self.assertEqual(hdmi["video"]["input"], name)
        self.assertEqual(hdmi["video"]["output"]["resolution"], "1920x1080")
        self.assertFalse(hdmi["sap_input"]["enabled"], "SAP input still on")
        # --- audio follows the main window's Session 1 audio, over the audio input as it was
        main = mv.main_window_cell(layout)
        self.assertEqual(hdmi["audio"]["input"], "ip_input3")
        if assigned.get(main):
            audio = self.pool_input("ip_input3")
            self.assertEqual(audio["multicast"]["address"],
                             self.s1_audio_destination(assigned[main]),
                             "%s: audio is not the main window's Session 1 audio" % layout)
            self.assertTrue(audio["enabled"])
        # --- every window's object geometry
        for cell, window in by_cell.items():
            sub = self.subframe(name, cell)
            self.assertEqual((sub["x"], sub["y"]), (window["x"], window["y"]), cell)


def _per_layout(layout):
    def test(self):
        assignment = distinct_assignment(layout)
        name = self.store("Layout %s" % layout.replace("+", " plus "), layout, assignment)
        self.show_ok(name)
        self.assert_hardware_matches(name, layout, assignment)
    test.__name__ = "test_%s_leaves_the_devices_configured_for_it" % \
        layout.replace("+", "_").replace("-", "_")
    return test


class EveryLayoutTests(HardwareStateBase):
    """The eleven layouts, each from a clean start, each judged from the devices."""


for _layout in mv.LAYOUT_ORDER:
    _t = _per_layout(_layout)
    setattr(EveryLayoutTests, _t.__name__, _t)


def hardware_state(case):
    """The parts of the devices a Multiview operation is responsible for, by value."""
    state = {}
    for ip in SOURCES:
        if ip not in case.devices:
            continue
        state[ip] = {"e2": {k: copy.deepcopy(case.e2(ip).get(k))
                            for k in ("input", "bitrate", "scaler")},
                     "e1": copy.deepcopy({k: case.e1(ip).get(k)
                                          for k in ("input", "bitrate", "scaler")}),
                     "s2": {"enabled": case.session(ip)["video"]["stream"]["enabled"],
                            "sap": case.session(ip)["sap"]["enabled"],
                            "dest": case.s2_destination(ip)}}
    hdmi = case.dec.nodes["hdmi_output"][0]
    state["decoder"] = {
        "video": hdmi["video"]["input"], "audio": hdmi["audio"]["input"],
        "resolution": hdmi["video"]["output"]["resolution"],
        "sap_input": hdmi["sap_input"]["enabled"],
        "inputs": {i["name"]: (i["enabled"], i["multicast"]["address"], i["port"])
                   for i in case.dec.nodes["ip_input"]},
        # Only the object on the display. An inactive one keeps whatever pool inputs
        # it was last wired to, which another Multiview may since have taken; Show
        # rewires it, so its stale pointers are not state anything depends on.
        "objects": {o["name"]: sorted((s["name"], s.get("input")) for s in o["subframes"])
                    for o in case.dec.nodes["multiview"] if o["name"] == hdmi["video"]["input"]}}
    return state


def writes_only(case):
    return [w for w in case.all_writes() if w[1] != "config_get"]


class TransitionTests(HardwareStateBase):
    """Moving between saved Multiviews: Encoder 2 changes every time, nothing is inherited."""

    def setUp(self):
        super().setUp()
        self.quad_assign = distinct_assignment("2x2")
        self.quad = self.store("Quad", "2x2", self.quad_assign)
        self.pip_assign = {"main": SOURCES[1], "bottom_right": SOURCES[0]}
        self.pip = self.store("Presenter", "pip-bottom-right", self.pip_assign)
        self.pair_assign = {"left": SOURCES[2], "right": SOURCES[3]}
        self.pair = self.store("Pair", "side-by-side", self.pair_assign)

    def test_required_to_required_with_different_parameters(self):
        self.show_ok(self.quad)
        self.assertEqual(self.scaler(SOURCES[0]), (960, 544))
        self.assertEqual(self.scaler(SOURCES[1]), (960, 544))
        self.show_ok(self.pip)
        # Both sources are re-scaled, in opposite directions, from what the quad left
        self.assertEqual(self.scaler(SOURCES[1]), (1920, 1080))
        self.assertEqual(self.scaler(SOURCES[0]), (640, 360))
        self.assert_hardware_matches(self.pip, "pip-bottom-right", self.pip_assign)

    def test_back_to_the_first_layout_inherits_nothing_from_the_second(self):
        self.show_ok(self.quad)
        first = hardware_state(self)
        self.show_ok(self.pip)
        self.assertNotEqual(hardware_state(self), first)
        self.show_ok(self.quad)
        again = hardware_state(self)
        # A -> B -> A lands exactly where A -> A would have: same scalers, bitrates,
        # inputs, Session 2 state, pool inputs and subframe wiring.
        self.assertEqual(again["decoder"]["inputs"], first["decoder"]["inputs"])
        self.assertEqual(again, first)
        self.assert_hardware_matches(self.quad, "2x2", self.quad_assign)

    def test_full_size_to_scaled_and_back(self):
        """1920x1080 is 'no scaling needed'; 960x544 is a real scale. Both directions."""
        self.assertEqual(self.scaler(SOURCES[2]), (1920, 1080))     # the fixture's starting point
        self.show_ok(self.pair)
        self.assertEqual(self.scaler(SOURCES[2]), (960, 544))
        self.show_ok(self.store("Full", "pip-top-left",
                                {"main": SOURCES[2], "top_left": SOURCES[3]}))
        self.assertEqual(self.scaler(SOURCES[2]), (1920, 1080))
        self.assertEqual(self.scaler(SOURCES[3]), (640, 360))

    def test_a_source_not_in_the_new_layout_is_not_touched(self):
        self.show_ok(self.quad)
        before = {ip: copy.deepcopy((self.e2(ip), self.session(ip))) for ip in SOURCES[2:]}
        for ip in SOURCES:
            self.devices[ip].writes.clear()
        self.show_ok(self.pip)                     # uses only SOURCES[0] and SOURCES[1]
        for ip in SOURCES[2:]:
            writes = [w for w in self.devices[ip].writes if w[0] != "config_get"]
            self.assertEqual(writes, [], "%s was written by a layout that does not use it" % ip)
            self.assertEqual((self.e2(ip), self.session(ip)), before[ip])

    def test_the_pool_inputs_the_new_layout_does_not_need_are_released(self):
        self.show_ok(self.quad)
        self.assertEqual(sum(1 for i in POOL if self.pool_input(i)["enabled"]), 4)
        self.show_ok(self.pip)
        self.assertEqual(sorted(i for i in POOL if self.pool_input(i)["enabled"]),
                         sorted(self.subframe(self.pip, c)["input"] for c in self.pip_assign))

    def test_a_long_sequence_never_leaves_stale_state(self):
        steps = [(self.quad, "2x2", self.quad_assign),
                 (self.pip, "pip-bottom-right", self.pip_assign),
                 (self.pair, "side-by-side", self.pair_assign),
                 (self.quad, "2x2", self.quad_assign),
                 (self.pair, "side-by-side", self.pair_assign),
                 (self.pip, "pip-bottom-right", self.pip_assign)] * 3
        for name, layout, assignment in steps:
            self.show_ok(name)
            self.assert_hardware_matches(name, layout, assignment)

    def test_repeating_a_show_writes_nothing_at_all(self):
        self.show_ok(self.pip)
        for device in self.devices.values():
            device.writes.clear()
        status, body = self.show(self.pip)
        self.assertEqual(status, 200, body)
        self.assertTrue(body["ok"])
        self.assertTrue(body.get("already_shown"), body)
        self.assertEqual(writes_only(self), [], "a repeated Show wrote to a device")
        self.assertEqual((body.get("writes") or {}).get("writes_performed"), 0)
        self.assert_hardware_matches(self.pip, "pip-bottom-right", self.pip_assign)

    def test_showing_a_different_preset_is_not_mistaken_for_a_repeat(self):
        self.show_ok(self.pip)
        status, body = self.show(self.quad)
        self.assertFalse(body.get("already_shown"), body)
        self.assertEqual(self.dec.nodes["hdmi_output"][0]["video"]["input"], self.quad)


class SourceAssignmentTests(HardwareStateBase):
    """The source the operator put in a window is the one that ends up in it."""

    def resave(self, name, layout, assignment):
        body = self.save(layout=layout, name=name, assignments=assignment,
                         object_name=name, update_existing=True).get_json()
        self.assertTrue(body["ok"], body)
        return body

    def test_rotating_three_sources_through_the_windows(self):
        cells3 = ("top_left", "top_right", "bottom_left")
        first = dict(zip(cells3, SOURCES[:3]))
        name = self.store("Rotor", "2x2", first)
        for turn in range(6):
            rotated = {c: SOURCES[(i + turn) % 3] for i, c in enumerate(cells3)}
            if turn:
                self.resave(name, "2x2", rotated)
            self.show_ok(name)
            self.assert_hardware_matches(name, "2x2", rotated)
            for cell, ip in rotated.items():
                addr = self.pool_input(self.subframe(name, cell)["input"])["multicast"]["address"]
                self.assertEqual(addr, self.s2_destination(ip), "turn %d %s" % (turn, cell))

    def test_every_permutation_of_a_main_window_and_two_small_ones(self):
        import itertools
        layout = "1+3-horizontal-bottom"
        main = mv.main_window_cell(layout)
        smalls = [c["cell"] for c in cells(layout) if c["cell"] != main][:2]
        name = None
        for turn, perm in enumerate(itertools.permutations(SOURCES[:3])):
            assignment = dict(zip([main] + smalls, perm))
            if name is None:
                name = self.store("Permute", layout, assignment)
            else:
                self.resave(name, layout, assignment)
            self.show_ok(name)
            self.assert_hardware_matches(name, layout, assignment)
            self.assertEqual(self.scaler(perm[0]), (1280, 720), turn)
            self.assertEqual(self.scaler(perm[1]), (640, 360), turn)
            self.assertEqual(self.scaler(perm[2]), (640, 360), turn)

    def test_equal_size_windows_may_share_one_source_and_one_input(self):
        assignment = {"top_left": SOURCES[0], "top_right": SOURCES[0],
                      "bottom_left": SOURCES[1], "bottom_right": SOURCES[2]}
        name = self.store("Shared", "2x2", assignment)
        self.show_ok(name)
        self.assert_hardware_matches(name, "2x2", assignment)
        self.assertEqual(self.subframe(name, "top_left")["input"],
                         self.subframe(name, "top_right")["input"])
        self.assertEqual(sum(1 for i in POOL if self.pool_input(i)["enabled"]), 3)

    def test_one_source_in_windows_of_two_sizes_is_refused_before_any_write(self):
        before = hardware_state(self)
        response = self.save(layout="pip-bottom-right", name="Greedy",
                             assignments={"main": SOURCES[0], "bottom_right": SOURCES[0]})
        self.assertEqual(response.status_code, 409)
        body = response.get_json()
        self.assertFalse(body["ok"])
        self.assertEqual(body["classification"], "conflict")
        self.assertIn("1920x1080", body["error"])
        self.assertIn("640x360", body["error"])
        self.assertEqual(writes_only(self), [])
        self.assertEqual(hardware_state(self), before)

    def test_one_stream_in_three_windows_is_refused_naming_the_windows(self):
        assignment = {"top_left": SOURCES[0], "top_right": SOURCES[0],
                      "bottom_left": SOURCES[0], "bottom_right": SOURCES[1]}
        response = self.save(layout="2x2", name="Triple", assignments=assignment)
        body = response.get_json()
        self.assertIn(response.status_code, (400, 409), body)
        self.assertFalse(body["ok"])
        for cell in ("top_left", "top_right", "bottom_left"):
            self.assertIn(cell, body["error"])
        self.assertEqual(writes_only(self), [])

    def test_an_empty_window_consumes_nothing(self):
        assignment = {"main": SOURCES[1]}
        name = self.store("Solo", "pip-top-left", assignment)
        self.show_ok(name)
        self.assert_hardware_matches(name, "pip-top-left", assignment)
        self.assertEqual(self.scaler(SOURCES[0]), (1920, 1080))     # untouched fixture value
        self.assertEqual(self.vc2_writes(SOURCES[0], "vc2_encoder2"), [])
        self.assertFalse(self.session(SOURCES[0])["video"]["stream"]["enabled"])


class FailureTests(HardwareStateBase):
    """A failure is reported as one, with a structured reason, and leaves nothing half-done."""

    def setUp(self):
        super().setUp()
        self.assign = distinct_assignment("2x2")
        self.quad = self.store("Quad", "2x2", self.assign)
        self.pip_assign = {"main": SOURCES[1], "bottom_right": SOURCES[0]}
        self.pip = self.store("Presenter", "pip-bottom-right", self.pip_assign)

    def test_an_unreachable_source_is_503_and_changes_nothing(self):
        self.show_ok(self.quad)
        mark = len(writes_only(self))
        del self.devices[SOURCES[1]]
        status, body = self.show(self.pip)
        self.assertEqual(status, 503, body)
        self.assertFalse(body["ok"])
        self.assertEqual(body["classification"], "device_unreachable")
        self.assertEqual(body["status"], "DEVICE UNREACHABLE")
        self.assertIn("enc-test-02", body["error"])
        self.assertIn("No device was changed", body["error"])
        self.assertEqual(writes_only(self)[mark:], [])

    def test_a_source_with_no_session_2_destination_is_400_and_changes_nothing(self):
        self.session(SOURCES[1])["video"]["stream"]["destination_address"] = ""
        mark = len(writes_only(self))
        status, body = self.show(self.pip)
        self.assertEqual(status, 400, body)
        self.assertEqual(body["classification"], "invalid")
        self.assertIn("no stream destination", body["error"])
        self.assertIn("enc-test-02", body["error"])
        self.assertEqual(writes_only(self)[mark:], [])
        self.assertEqual(self.scaler(SOURCES[1]), (1920, 1080))       # not re-scaled

    def test_an_object_that_is_not_on_the_decoder_is_404(self):
        mark = len(writes_only(self))
        status, body = self.show("multiviewNeverExisted")
        self.assertEqual(status, 404)
        self.assertEqual(body["error"], "multiviewNeverExisted is not on this decoder")
        self.assertEqual(writes_only(self)[mark:], [])

    def test_an_object_with_no_record_is_409_and_nothing_is_guessed(self):
        self.dec.nodes["multiview"].append(
            {"name": "multiviewMadeElsewhere", "width": 1920, "height": 1080,
             "subframes": [{"name": "main (1920x1080)", "x": 0, "y": 0, "input": "ip_input2"}]})
        mark = len(writes_only(self))
        status, body = self.show("multiviewMadeElsewhere")
        self.assertEqual(status, 409, body)
        self.assertIn("no record of what feeds", body["error"])
        self.assertEqual(writes_only(self)[mark:], [])

    def test_a_refused_write_at_any_stage_rolls_every_device_back(self):
        """Whichever device refuses, whichever node, every device is where it started."""
        refusals = [(SOURCES[0], "vc2"), (SOURCES[1], "vc2"),
                    (base.DECODER_IP, "ip_input"), (base.DECODER_IP, "hdmi_output")]
        for ip, node in refusals:
            with self.subTest(device=ip, node=node):
                self.show_ok(self.quad)
                before = hardware_state(self)
                self.devices[ip].refuse[node] = "device busy"
                status, body = self.show(self.pip)
                self.devices[ip].refuse.pop(node, None)
                self.assertFalse(body["ok"], body)
                self.assertIn("ROLLED BACK", body["status"], body)
                self.assertTrue(all(step["verified"] for step in body["rollback"]),
                                body["rollback"])
                self.assertEqual(hardware_state(self), before,
                                 "%s refusing %s left the devices part-way" % (ip, node))
                self.assertEqual(self.dec.nodes["hdmi_output"][0]["video"]["input"], self.quad)

    def test_a_refused_session_write_on_a_first_show_rolls_back_the_encoders_before_it(self):
        before = hardware_state(self)
        self.devices[SOURCES[3]].refuse["sessions"] = "device busy"
        status, body = self.show(self.quad)
        self.devices[SOURCES[3]].refuse.pop("sessions", None)
        self.assertFalse(body["ok"], body)
        self.assertIn("ROLLED BACK", body["status"])
        self.assertEqual(hardware_state(self), before)
        for ip in SOURCES:
            self.assertEqual(self.scaler(ip), (1920, 1080), "%s kept a scaler from the failed show" % ip)
            self.assertFalse(self.session(ip)["video"]["stream"]["enabled"])

    def test_a_write_the_device_swallows_is_caught_and_rolled_back(self):
        self.show_ok(self.quad)
        before = hardware_state(self)
        self.devices[SOURCES[0]].swallow["vc2_encoder2"] = 99     # accepted, answered ok, ignored
        status, body = self.show(self.pip)
        self.devices[SOURCES[0]].swallow.clear()
        self.assertFalse(body["ok"], body)
        self.assertIn("ROLLED BACK", body["status"])
        self.assertEqual(hardware_state(self), before)

    def test_a_decoder_whose_input_never_goes_active_is_a_failure_and_is_rolled_back(self):
        self.show_ok(self.quad)
        before = hardware_state(self)
        self.dec.nodes["hdmi_output"][0]["video"]["status"]["active"] = False
        status, body = self.show(self.pip)
        self.dec.nodes["hdmi_output"][0]["video"]["status"]["active"] = True
        self.assertFalse(body["ok"], body)
        self.assertEqual(body["status"].replace("—", "-").upper().split(" - ")[0],
                         "NO ACTIVE VIDEO", body["status"])
        self.assertIn("ROLLED BACK", body["status"])
        self.assertEqual(hardware_state(self), before)

    def test_a_window_that_never_locks_is_named_and_the_picture_is_kept(self):
        name = self.quad
        self.show_ok(name)
        self.show_ok(self.pip)
        dark = self.subframe(name, "top_right")["input"]
        self.dec.never_locks.add(dark)
        status, body = self.show(name)
        self.assertEqual(status, 200, body)
        self.assertTrue(body["ok"])
        self.assertTrue(body.get("unlocked_windows"), "a dark window was not reported")
        self.assertEqual(self.dec.nodes["hdmi_output"][0]["video"]["input"], name,
                         "the live windows were rolled back to punish one dark one")
        self.assertEqual(self.scaler(SOURCES[1]), (960, 544))


class BitrateCapTests(HardwareStateBase):
    """What reaches Encoder 2 is min(layout target, 900 - Encoder 1); Encoder 1 is never lowered."""

    def run_layout(self, e1_bitrate, layout, assignment):
        for ip in SOURCES:
            self.e1(ip)["bitrate"] = e1_bitrate
        name = self.store("Cap %s %s" % (layout, e1_bitrate), layout, assignment)
        return name, self.show(name)

    def test_the_target_is_cut_to_what_encoder_1_leaves(self):
        assignment = {"main": SOURCES[0], "top_left": SOURCES[1]}
        for e1, main_want, small_want in ((500, 300, 150), (700, 200, 150), (750, 150, 150),
                                          (800, 100, 100), (850, 50, 50)):
            with self.subTest(encoder1=e1):
                name, (status, body) = self.run_layout(e1, "pip-top-left", assignment)
                self.assertEqual(status, 200, body)
                self.assertEqual(self.e2(SOURCES[0])["bitrate"], main_want)
                self.assertEqual(self.e2(SOURCES[1])["bitrate"], small_want)
                # Encoder 1 is read, never written
                self.assertEqual(self.e1(SOURCES[0])["bitrate"], e1)
                self.assertEqual(self.vc2_writes(SOURCES[0], "vc2_encoder1"), [])

    def test_a_source_with_under_the_minimum_spare_is_refused_by_name_and_untouched(self):
        for ip in SOURCES:
            self.e1(ip)["bitrate"] = 890                     # 10 Mb/s spare, minimum is 20
        name = self.store("Starved", "side-by-side", {"left": SOURCES[0], "right": SOURCES[1]})
        before = hardware_state(self)
        status, body = self.show(name)
        self.assertIn(status, (400, 409), body)
        self.assertFalse(body["ok"])
        self.assertIn(SOURCES[0], body["error"])
        self.assertIn("leaving 10 of its 900 Mb/s budget", body["error"])
        self.assertIn("less than the 20 Mb/s Encoder 2 needs", body["error"])
        self.assertEqual(writes_only(self)[-1:], [(base.DECODER_IP, "method", "add_multiview")])
        self.assertEqual(hardware_state(self), before)
        self.assertEqual(self.e1(SOURCES[0])["bitrate"], 890, "Encoder 1 was trimmed to make room")

    def test_equal_sources_in_equal_windows_get_the_same_bitrate(self):
        name = self.store("Even", "2x2", distinct_assignment("2x2"))
        self.show_ok(name)
        self.assertEqual({self.e2(ip)["bitrate"] for ip in SOURCES}, {200})


class EditWhileActiveTests(HardwareStateBase):
    """Saving over the Multiview that is on the display, then showing it again."""

    def test_save_over_the_active_object_then_show_again_ends_on_the_new_assignment(self):
        first = {"top_left": SOURCES[0], "top_right": SOURCES[1],
                 "bottom_left": SOURCES[2], "bottom_right": SOURCES[3]}
        name = self.store("Live", "2x2", first)
        self.show_ok(name)
        self.assert_hardware_matches(name, "2x2", first)

        edited = {"top_left": SOURCES[3], "top_right": SOURCES[2],
                  "bottom_left": SOURCES[1], "bottom_right": SOURCES[0]}
        saved = self.save(layout="2x2", name="Live", assignments=edited,
                          object_name=name, update_existing=True).get_json()
        self.assertTrue(saved["ok"], saved)
        # Saving describes; it does not configure. Encoder 2 and Session 2 are as they were.
        for ip in SOURCES:
            self.assertEqual(self.scaler(ip), (960, 544))
        # The edit is not reported as already shown: the stored description has changed.
        status, body = self.show(name)
        self.assertEqual(status, 200, body)
        self.assertEqual(body["status"], "VERIFIED")
        self.assertFalse(body.get("already_shown"), "an edited preset was reported as already on screen")
        self.assert_hardware_matches(name, "2x2", edited)

    def test_changing_the_layout_of_the_active_object_drops_windows_the_new_layout_lacks(self):
        quad = {"top_left": SOURCES[0], "top_right": SOURCES[1],
                "bottom_left": SOURCES[2], "bottom_right": SOURCES[3]}
        name = self.store("Reshape", "2x2", quad)
        self.show_ok(name)
        pair = {"left": SOURCES[1], "right": SOURCES[0]}
        saved = self.save(layout="side-by-side", name="Reshape", assignments=pair,
                          object_name=name, update_existing=True).get_json()
        self.assertTrue(saved["ok"], saved)
        # Save is save only: the object on the display still has its four windows.
        self.assertEqual(len(self.obj(name)["subframes"]), 4,
                         "saving over the displayed Multiview rewrote its live windows")
        self.show_ok(name)
        self.assertEqual(sorted(s["name"].split(" (")[0] for s in self.obj(name)["subframes"]),
                         ["left", "right"], "windows of the old layout were left behind")
        self.assert_hardware_matches(name, "side-by-side", pair, strict_pool=False)

    def test_the_pool_inputs_of_windows_an_edit_removed_are_released(self):
        """Saving a smaller layout over the active Multiview prunes its subframes,
        which are what used to prove OmniSuite configured the inputs under them.
        The proof is now recorded before the prune, so Show hands back the inputs
        the removed windows used."""
        quad = {"top_left": SOURCES[0], "top_right": SOURCES[1],
                "bottom_left": SOURCES[2], "bottom_right": SOURCES[3]}
        name = self.store("Reshape", "2x2", quad)
        self.show_ok(name)
        self.save(layout="side-by-side", name="Reshape",
                  assignments={"left": SOURCES[1], "right": SOURCES[0]},
                  object_name=name, update_existing=True)
        self.show_ok(name)
        self.assertEqual(sum(1 for i in POOL if self.pool_input(i)["enabled"]), 2)

    def test_the_canvas_of_an_existing_object_cannot_be_moved(self):
        name = self.store("Fixed", "2x2", {"top_left": SOURCES[0]})
        response = self.save(layout="2x2", name="Fixed", canvas="3840x2160",
                             assignments={"top_left": SOURCES[0]}, object_name=name,
                             update_existing=True)
        self.assertIn(response.status_code, (400, 409))
        self.assertFalse(response.get_json()["ok"])
        self.assertEqual((self.obj(name)["width"], self.obj(name)["height"]), (1920, 1088))


class PoolOwnershipTests(HardwareStateBase):
    """Which reserved decoder inputs a Show may hand back after an edit shrank a layout.

    Release needs three things to be true together: OmniSuite put the input there
    (a record made before the edit pruned the subframes that proved it), the
    displayed configuration no longer needs it, and it still carries the stream
    OmniSuite gave it.
    """

    QUAD = {"top_left": SOURCES[0], "top_right": SOURCES[1],
            "bottom_left": SOURCES[2], "bottom_right": SOURCES[3]}
    PAIR = {"left": SOURCES[1], "right": SOURCES[0]}
    FOREIGN = "239.9.9.9"

    def shrink(self):
        name = self.store("Reshape", "2x2", self.QUAD)
        self.show_ok(name)
        self.assertEqual(sorted(i for i in POOL if self.pool_input(i)["enabled"]), list(POOL))
        saved = self.save(layout="side-by-side", name="Reshape", assignments=self.PAIR,
                          object_name=name, update_existing=True).get_json()
        self.assertTrue(saved["ok"], saved)
        return name

    def enabled(self):
        return sorted(i for i in POOL if self.pool_input(i)["enabled"])

    def test_exactly_the_inputs_the_removed_windows_used_are_released(self):
        name = self.shrink()
        body = self.show_ok(name)
        self.assertEqual(len(self.enabled()), 2, self.enabled())
        self.assertEqual(len(body["released"]), 2, body["released"])
        self.assertEqual(sorted(body["released"] + self.enabled()), list(POOL))

    def test_an_input_repointed_since_is_not_ours_and_is_left_alone(self):
        name = self.shrink()
        target = max(self.enabled())
        self.pool_input(target)["multicast"]["address"] = self.FOREIGN
        body = self.show_ok(name)
        self.assertIn(target, self.enabled(), "an input carrying another stream was released")
        self.assertEqual(self.pool_input(target)["multicast"]["address"], self.FOREIGN)
        self.assertNotIn(target, body["released"])
        self.assertIn(target, [k["ip_input"] for k in body["kept"]])

    def test_an_input_the_operator_enabled_that_omnisuite_never_configured_is_left_alone(self):
        name = self.store("Pair", "side-by-side", self.PAIR)
        self.show_ok(name)
        spare = [i for i in POOL if not self.pool_input(i)["enabled"]][0]
        self.pool_input(spare).update({"enabled": True})
        self.pool_input(spare)["multicast"]["address"] = self.FOREIGN
        again = self.store("Pair", "side-by-side", {"left": SOURCES[0], "right": SOURCES[1]},
                           object_name=name, update_existing=True)
        self.show_ok(again)
        self.assertIn(spare, self.enabled())
        self.assertEqual(self.pool_input(spare)["multicast"]["address"], self.FOREIGN)

    def test_a_multiview_shown_before_ownership_was_recorded_is_still_released_after_an_edit(self):
        """Proof taken at Save, from the subframes, covers an object an older build
        showed and so never recorded anything."""
        name = self.store("Reshape", "2x2", self.QUAD)
        self.show_ok(name)
        for entry in srv._MULTIVIEW_META.values():
            ((entry.get("multiviews") or {}).get(name) or {}).pop("allocated_inputs", None)
        self.save(layout="side-by-side", name="Reshape", assignments=self.PAIR,
                  object_name=name, update_existing=True)
        self.show_ok(name)
        self.assertEqual(len(self.enabled()), 2, self.enabled())

    def test_ownership_survives_two_saves_with_no_show_between_them(self):
        name = self.shrink()
        self.save(layout="side-by-side", name="Reshape",
                  assignments={"left": SOURCES[0], "right": SOURCES[1]},
                  object_name=name, update_existing=True)
        self.show_ok(name)
        self.assertEqual(len(self.enabled()), 2, self.enabled())

    def test_a_refused_release_fails_the_show_and_leaves_the_devices_as_they_were(self):
        name = self.shrink()
        before = hardware_state(self)
        self.dec.refuse["ip_input"] = "device busy"
        status, body = self.show(name)
        self.dec.refuse.pop("ip_input", None)
        self.assertFalse(body["ok"], body)
        self.assertEqual(hardware_state(self), before)
        self.assertEqual(self.enabled(), list(POOL), "a refused Show released an input")

    def test_a_release_that_is_rolled_back_comes_back(self):
        """The release itself is verified and held; it is the picture that fails."""
        name = self.shrink()
        before = hardware_state(self)
        self.dec.nodes["hdmi_output"][0]["video"]["status"]["active"] = False
        status, body = self.show(name)
        self.dec.nodes["hdmi_output"][0]["video"]["status"]["active"] = True
        self.assertFalse(body["ok"], body)
        self.assertIn("ROLLED BACK", body["status"])
        self.assertEqual(self.enabled(), list(POOL), "the released inputs were not put back")
        self.assertEqual(hardware_state(self), before)

    def test_a_released_input_the_operator_takes_over_afterwards_is_not_released_again(self):
        name = self.shrink()
        body = self.show_ok(name)
        taken = body["released"][0]
        self.pool_input(taken)["enabled"] = True
        # Same address it used to carry: only the record could tell it from ours.
        status, again = self.show(name)
        self.assertEqual(status, 200, again)
        self.assertIn(taken, self.enabled(), "a released input was reclaimed from the operator")

    def test_the_record_carries_only_names_and_addresses(self):
        name = self.shrink()
        self.show_ok(name)
        held = {}
        for entry in srv._MULTIVIEW_META.values():
            held.update(((entry.get("multiviews") or {}).get(name) or {}).get("allocated_inputs") or {})
        self.assertTrue(held, "nothing was recorded")
        for key, address in held.items():
            self.assertIn(key, POOL)
            self.assertRegex(address, r"^[0-9.]+$")


class LifecycleTests(HardwareStateBase):
    """The path a user takes: create, assign, save, show, modify, show, switch, delete."""

    def test_create_show_modify_show_delete(self):
        assignment = {"main": SOURCES[2], "bottom_right": SOURCES[3]}
        name = self.store("Temporary", "pip-bottom-right", assignment)
        self.assertEqual(self.dec.nodes["hdmi_output"][0]["video"]["input"], "ip_input1",
                         "saving changed the display")
        self.assertEqual(writes_only(self)[-1][2], "add_multiview")
        for ip in SOURCES:
            self.assertEqual(self.vc2_writes(ip, "vc2_encoder2"), [], "saving configured Encoder 2")

        self.show_ok(name)
        self.assert_hardware_matches(name, "pip-bottom-right", assignment)

        modified = {"main": SOURCES[3], "bottom_right": SOURCES[2]}
        saved = self.save(layout="pip-bottom-right", name="Temporary", assignments=modified,
                          object_name=name, update_existing=True).get_json()
        self.assertTrue(saved["ok"], saved)
        self.show_ok(name)
        self.assert_hardware_matches(name, "pip-bottom-right", modified)
        self.assertEqual(self.scaler(SOURCES[3]), (1920, 1080))
        self.assertEqual(self.scaler(SOURCES[2]), (640, 360))

        response = self.client.post("/api/multiview/delete",
                                    json={"decoder": base.DECODER_IP, "name": name})
        body = response.get_json()
        self.assertEqual(response.status_code, 200, body)
        self.assertEqual(body["status"], "VERIFIED")
        self.assertEqual([o["name"] for o in self.dec.nodes["multiview"]], [])
        hdmi = self.dec.nodes["hdmi_output"][0]
        self.assertNotEqual(hdmi["video"]["input"], name, "the output still points at a deleted object")
        self.assertEqual(sum(1 for i in POOL if self.pool_input(i)["enabled"]), 0,
                         "the pool inputs the deleted Multiview owned were not released")
        # shared encoder streams are deliberately left configured
        self.assertTrue(self.session(SOURCES[2])["video"]["stream"]["enabled"])
        self.assertEqual(self.scaler(SOURCES[3]), (1920, 1080))

    def test_several_temporary_presets_come_and_go_without_leaving_anything_behind(self):
        names = []
        for turn, layout in enumerate(("2x2", "side-by-side", "pip-top-right", "4-split")):
            assignment = distinct_assignment(layout, offset=turn)
            name = self.store("Temp %d" % turn, layout, assignment)
            names.append(name)
            self.show_ok(name)
            self.assert_hardware_matches(name, layout, assignment)
        for name in names:
            body = self.client.post("/api/multiview/delete",
                                    json={"decoder": base.DECODER_IP, "name": name}).get_json()
            self.assertTrue(body["ok"], body)
        self.assertEqual(self.dec.nodes["multiview"], [])
        self.assertEqual(sum(1 for i in POOL if self.pool_input(i)["enabled"]), 0)

    def test_filling_and_changing_one_window_on_the_live_display(self):
        assignment = {"top_left": SOURCES[0], "top_right": SOURCES[1]}
        name = self.store("Live edit", "2x2", assignment)
        self.show_ok(name)
        for ip in SOURCES:
            self.devices[ip].writes.clear()
        response = self.client.post("/api/multiview/switch", json={
            "decoder": base.DECODER_IP, "name": name, "cell": "bottom_left", "source": SOURCES[2]})
        body = response.get_json()
        self.assertEqual(response.status_code, 200, body)
        self.assertTrue(body["ok"], body)
        now = dict(assignment, bottom_left=SOURCES[2])
        self.assert_hardware_matches(name, "2x2", now)
        # the two windows nobody touched caused no write at all
        for ip in SOURCES[:2]:
            self.assertEqual([w for w in self.devices[ip].writes if w[0] != "config_get"], [],
                             "%s was written by a switch of another window" % ip)
        # a verified switch becomes the saved preset
        again = self.show_ok(name)
        self.assertTrue(again.get("already_shown"), "the preset was left describing the old picture")

    def test_replacing_a_source_on_the_live_display_retargets_that_window_only(self):
        assignment = {"top_left": SOURCES[0], "top_right": SOURCES[1], "bottom_left": SOURCES[2]}
        name = self.store("Swap", "2x2", assignment)
        self.show_ok(name)
        response = self.client.post("/api/multiview/switch", json={
            "decoder": base.DECODER_IP, "name": name, "cell": "top_right", "source": SOURCES[3]})
        self.assertTrue(response.get_json()["ok"], response.get_json())
        now = dict(assignment, top_right=SOURCES[3])
        self.assert_hardware_matches(name, "2x2", now)
        self.assertEqual(self.subframe(name, "top_left")["input"],
                         self.subframe(name, "top_left")["input"])

    def test_a_switch_aimed_at_an_inactive_object_is_refused_and_changes_nothing(self):
        live = self.store("Live", "2x2", {"top_left": SOURCES[0]})
        idle = self.store("Idle", "side-by-side", {"left": SOURCES[1]})
        self.show_ok(live)
        before = hardware_state(self)
        response = self.client.post("/api/multiview/switch", json={
            "decoder": base.DECODER_IP, "name": idle, "cell": "right", "source": SOURCES[2]})
        self.assertIn(response.status_code, (400, 409), response.get_json())
        self.assertFalse(response.get_json()["ok"])
        self.assertEqual(hardware_state(self), before)


class OtherDecoderTests(HardwareStateBase):
    """A scaler is shared with every decoder showing the encoder; only a displayed Multiview counts."""

    OTHER = base.DECODER2_IP

    def other_decoder(self, showing):
        hdmi = base._hdmi_output()
        hdmi[0]["video"]["input"] = "multiviewOther" if showing else "ip_input1"
        other = self.decoder(ip=self.OTHER, hdmi=hdmi)
        mv_object = {"name": "multiviewOther", "width": 1920, "height": 1080, "subframes": []}
        other.nodes["multiview"].append(mv_object)
        srv._record_multiview_meta(
            {"ip": self.OTHER, "mac": "00:00:5E:00:53:11", "hostname": "dec-test-02"},
            "multiviewOther",
            {"layout": "pip-top-left", "windows": [
                {"source_ip": SOURCES[0], "encoder_index": 2, "scaler_format": "1920x1080"}]})
        self.addCleanup(lambda: srv._forget_multiview_meta(
            {"ip": self.OTHER, "mac": "00:00:5E:00:53:11"}, "multiviewOther"))
        return other

    def test_a_stored_multiview_elsewhere_does_not_block_and_the_scaler_lands_exactly(self):
        """The reported HTTP 409: Show refused because another decoder merely STORED a layout."""
        self.other_decoder(showing=False)
        assignment = distinct_assignment("2x2")
        name = self.store("Mine", "2x2", assignment)
        self.show_ok(name)
        self.assertEqual(self.scaler(SOURCES[0]), (960, 544))
        self.assert_hardware_matches(name, "2x2", assignment)

    def test_a_multiview_on_another_decoders_display_blocks_and_writes_nothing(self):
        self.other_decoder(showing=True)
        name = self.store("Mine", "2x2", distinct_assignment("2x2"))
        for ip in SOURCES:
            self.devices[ip].writes.clear()
        status, body = self.show(name)
        self.assertEqual(status, 409, body)
        self.assertEqual(body["classification"], "conflict")
        self.assertIn("dec-test-02", body["error"])
        self.assertIn("multiviewOther", body["error"])
        for ip in SOURCES:
            self.assertEqual([w for w in self.devices[ip].writes if w[0] != "config_get"], [])
            self.assertEqual(self.scaler(ip), (1920, 1080), "%s was re-scaled under another display" % ip)
        self.assertEqual(self.dec.nodes["hdmi_output"][0]["video"]["input"], "ip_input1")

    def test_the_other_decoder_stops_being_a_consumer_the_moment_it_stops_showing(self):
        other = self.other_decoder(showing=True)
        name = self.store("Mine", "2x2", distinct_assignment("2x2"))
        self.assertEqual(self.show(name)[0], 409)
        other.nodes["hdmi_output"][0]["video"]["input"] = "ip_input1"
        self.show_ok(name)
        self.assertEqual(self.scaler(SOURCES[0]), (960, 544))

    def test_an_unreachable_other_decoder_is_not_a_consumer(self):
        other = self.other_decoder(showing=True)
        other.unreachable = True
        name = self.store("Mine", "2x2", distinct_assignment("2x2"))
        self.show_ok(name)


class MixedCredentialTests(HardwareStateBase):
    """A source encoder that only answers to the fallback password still works end to end."""

    PRIMARY = "primary-test-secret"
    FALLBACK = "fallback-test-secret"

    def setUp(self):
        real_with_fallback = srv._ws_send_recv_with_fallback
        super().setUp()
        # the base class stands in for the whole credential layer; this suite is about it
        patch = mock.patch.object(srv, "_ws_send_recv_with_fallback", real_with_fallback)
        patch.start()
        self.addCleanup(patch.stop)
        previous = {k: srv.app.config.get(k) for k in ("PASSWORD", "FALLBACK_PASSWORD")}
        self.addCleanup(lambda: srv.app.config.update(previous))
        srv.app.config.update(PASSWORD=self.PRIMARY, FALLBACK_PASSWORD=self.FALLBACK)
        self.accepts = {ip: self.PRIMARY for ip in (base.DECODER_IP,) + SOURCES}
        self.accepts[SOURCES[1]] = self.FALLBACK            # one encoder is on the vendor default
        self.accepts[SOURCES[3]] = self.FALLBACK
        self.offers = []

        def raw(url, payload, timeout=None, *a, **k):
            address = url.split("//", 1)[1].split(":", 1)[0]
            device = self.devices.get(address)
            if device is None:
                raise OSError("no such device in this test")
            write = "config_set" in payload or "method" in payload
            accepted = self.accepts.get(address) == payload.get("password")
            self.offers.append((address, "write" if write else "read", accepted))
            if not accepted:
                return {"id": payload.get("id"), "error": "Invalid username/password"}
            return device.handle(payload)

        for attribute, value in (("_ws_send_recv_raw", raw),
                                 ("_ws_send_recv", srv._ws_send_recv_credentialed)):
            original = getattr(srv, attribute)
            self.addCleanup(lambda a=attribute, o=original: setattr(srv, a, o))
            setattr(srv, attribute, value)
        with srv._working_cred_lock:
            saved = (dict(srv._working_cred_by_identity), dict(srv._working_cred_identity_by_ip),
                     set(srv._working_cred_suspect), srv._working_cred_warmed)
            srv._working_cred_by_identity.clear()
            srv._working_cred_identity_by_ip.clear()
            srv._working_cred_suspect.clear()
        srv._working_cred_warmed = True

        def restore():
            with srv._working_cred_lock:
                srv._working_cred_by_identity.clear()
                srv._working_cred_by_identity.update(saved[0])
                srv._working_cred_identity_by_ip.clear()
                srv._working_cred_identity_by_ip.update(saved[1])
                srv._working_cred_suspect.clear()
                srv._working_cred_suspect.update(saved[2])
            srv._working_cred_warmed = saved[3]
        self.addCleanup(restore)

    def test_show_configures_every_encoder_whatever_password_it_answers_to(self):
        assignment = distinct_assignment("2x2")
        name = self.store("Mixed", "2x2", assignment)
        self.show_ok(name)
        self.assert_hardware_matches(name, "2x2", assignment)
        for ip in (SOURCES[1], SOURCES[3]):
            self.assertEqual(self.scaler(ip), (960, 544), "%s never received its scaler" % ip)

    def test_a_write_is_never_presented_with_a_password_the_device_does_not_accept(self):
        name = self.store("Mixed", "2x2", distinct_assignment("2x2"))
        self.show_ok(name)
        refused_writes = [o for o in self.offers if o[1] == "write" and not o[2]]
        self.assertEqual(refused_writes, [],
                         "a write went to a device with the wrong password: %s" % refused_writes)

    def test_the_credential_found_by_the_first_read_serves_the_rest_of_the_operation(self):
        name = self.store("Mixed", "2x2", distinct_assignment("2x2"))
        self.show_ok(name)
        mark = len(self.offers)
        self.show_ok(self.store("Pair", "side-by-side", {"left": SOURCES[1], "right": SOURCES[3]}))
        later = self.offers[mark:]
        refused = [o for o in later if not o[2]]
        # after the credential is learned a unit is not offered the wrong one first
        self.assertEqual(refused, [], "re-tried the primary on a unit already known to need the fallback")

    def test_no_password_appears_in_the_response(self):
        name = self.store("Mixed", "2x2", distinct_assignment("2x2"))
        status, body = self.show(name)
        import json as _json
        text = _json.dumps(body)
        self.assertNotIn(self.PRIMARY, text)
        self.assertNotIn(self.FALLBACK, text)


class EncoderTwoInputTests(HardwareStateBase):
    """Encoder 2 has to be fed before anything downstream of it means anything."""

    def pair(self):
        return self.store("Pair", "side-by-side", {"left": SOURCES[0], "right": SOURCES[1]})

    def test_encoder_2_set_to_not_used_is_pointed_at_encoder_1s_input(self):
        for ip in SOURCES[:2]:
            self.e2(ip)["input"] = ""
            self.e1(ip)["input"] = "hdmi_input2"
        self.show_ok(self.pair())
        for ip in SOURCES[:2]:
            self.assertEqual(self.e2(ip)["input"], "hdmi_input2",
                             "%s: Encoder 2 was left producing the slate" % ip)
            self.assertEqual(self.e1(ip)["input"], "hdmi_input2")
            self.assertEqual(self.vc2_writes(ip, "vc2_encoder1"), [])

    def test_an_encoder_2_already_on_the_right_input_is_not_written_for_it(self):
        self.show_ok(self.pair())
        for ip in SOURCES[:2]:
            written = [w for w in self.vc2_writes(ip, "vc2_encoder2")
                       if any("input" in e for e in w[2])]
            self.assertEqual(written, [], "%s: the input was rewritten although correct" % ip)

    def test_an_encoder_2_on_a_different_active_input_is_a_conflict_and_is_left_alone(self):
        name = self.pair()
        self.e2(SOURCES[0])["input"] = "hdmi_input2"
        before = hardware_state(self)
        status, body = self.show(name)
        self.assertEqual(status, 409, body)
        self.assertEqual(body["classification"], "conflict")
        self.assertIn("hdmi_input2", body["error"])
        self.assertIn("hdmi_input1", body["error"])
        self.assertEqual(writes_only(self)[-1:], [(base.DECODER_IP, "method", "add_multiview")])
        self.assertEqual(hardware_state(self), before)

    def test_an_encoder_with_no_input_at_all_is_refused_rather_than_guessed(self):
        name = self.pair()
        self.e1(SOURCES[1])["input"] = ""
        self.e2(SOURCES[1])["input"] = ""
        status, body = self.show(name)
        self.assertEqual(status, 400, body)
        self.assertEqual(body["classification"], "invalid")
        self.assertIn("Encoder 1 has no input either", body["error"])
        self.assertEqual(self.e2(SOURCES[1])["input"], "")
        self.assertEqual(self.vc2_writes(SOURCES[1], "vc2_encoder2"), [])


if __name__ == "__main__":
    unittest.main()
