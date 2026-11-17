"""A window the decoder missed is brought back once, and the operator is told.

Measured on the lab decoders: after some transitions a window stays black while
every field is correct and its packets arrive. The decoder never allocated a
channel for it. No write to that window's input repairs it; leaving the composite
for a neutral input and selecting it again does, provided the display rests there
for about a second (7 of 7 with a one second pause, 0 of 3 with none).

The simulated decoder models exactly that and nothing more (see
`FakeDevice.miss_on_next_select`); every address is an RFC 5737 documentation
address and nothing touches a socket.
"""
import unittest
from unittest import mock

try:
    from . import test_multiview as base
    from .test_multiview_hardware_state import HardwareStateBase, SOURCES, distinct_assignment
except ImportError:                                  # loaded by path
    import test_multiview as base
    from test_multiview_hardware_state import HardwareStateBase, SOURCES, distinct_assignment

srv = base.srv


class RecoveryTests(HardwareStateBase):
    def setUp(self):
        super().setUp()
        self.quad = self.store("Quad", "2x2", distinct_assignment("2x2"))
        self.pip = self.store("Presenter", "pip-bottom-right",
                              {"main": SOURCES[1], "bottom_right": SOURCES[0]})
        for target in ("MULTIVIEW_RECOVERY_PAUSE", "MULTIVIEW_WINDOW_SETTLE"):
            patcher = mock.patch.object(srv, target, 0.0)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.show_ok(self.pip)                      # a composite is on the display first
        self.dec.selection_log.clear()

    def dark_cell(self, name, cell="top_right"):
        return (name, self.subframe(name, cell)["name"])

    def neutral_selections(self):
        return [pair for pair in self.dec.selection_log
                if pair[1] in ("generator", "ip_input1")]

    # ---- the measured behaviour ---------------------------------------------

    def test_a_missed_window_is_recovered_and_reported_as_verified(self):
        self.dec.miss_on_next_select = {self.dark_cell(self.quad)}
        status, body = self.show(self.quad)
        self.assertEqual(status, 200, body)
        self.assertEqual(body["status"], "VERIFIED", body)
        self.assertEqual(body["unlocked_windows"], [])
        self.assertTrue(body["recovery"]["attempted"])
        self.assertTrue(body["recovery"]["cleared"])
        self.assertEqual(body["recovery"]["dark_before"], [self.subframe(self.quad, "top_right")["name"]])

    def test_the_display_rests_on_ip_input1_and_comes_back(self):
        self.dec.miss_on_next_select = {self.dark_cell(self.quad)}
        self.show_ok(self.quad)
        selections = [new for _, new in self.dec.selection_log]
        self.assertEqual(selections, [self.quad, "ip_input1", self.quad],
                         "the display did not leave and re-select exactly once")
        self.assertEqual(self.dec.nodes["hdmi_output"][0]["video"]["input"], self.quad)

    def test_a_clean_show_attempts_no_recovery_and_never_leaves_the_composite(self):
        status, body = self.show(self.quad)
        self.assertEqual(body["status"], "VERIFIED")
        self.assertIsNone(body["recovery"])
        self.assertEqual(self.neutral_selections(), [])

    def test_recovery_is_one_attempt_never_a_loop(self):
        # A decoder that never locks the window: the second look is still dark.
        self.dec.never_locks.add(self.subframe(self.quad, "top_right")["input"] or "ip_input2")
        status, body = self.show(self.quad)
        self.assertEqual(status, 200, body)
        self.assertEqual(len(self.neutral_selections()), 1,
                         "recovery repeated itself instead of reporting")
        self.assertEqual(self.dec.nodes["hdmi_output"][0]["video"]["input"], self.quad,
                         "the picture was removed because one window stayed dark")

    def test_a_window_that_stays_dark_is_still_named_after_the_attempt(self):
        window = self.subframe(self.quad, "top_right")["name"]
        self.dec.never_locks.update({"ip_input2", "ip_input4", "ip_input6", "ip_input8"})
        status, body = self.show(self.quad)
        self.assertEqual(body["status"], "VERIFIED — WINDOW NOT LOCKED")
        self.assertFalse(body["recovery"]["cleared"])
        self.assertTrue(body["recovery"]["attempted"])
        self.assertTrue(body["unlocked_windows"])
        self.assertIsNotNone(body["diagnostics"])

    def test_recovery_touches_the_display_and_nothing_else(self):
        self.dec.miss_on_next_select = {self.dark_cell(self.quad)}
        self.show_ok(self.quad)
        last_select = max(i for i, w in enumerate(self.dec.writes)
                          if w[1] == "hdmi_output"
                          and w[2][0].get("video", {}).get("input") == self.quad)
        # Everything the recovery wrote is a display selection, and it is the tail.
        tail = self.dec.writes[last_select - 1:]
        self.assertTrue(all(w[1] == "hdmi_output" for w in tail), tail)
        self.assertEqual([w[2][0]["video"]["input"] for w in tail], ["ip_input1", self.quad])

    # ---- where the neutral input comes from ---------------------------------

    def test_without_ip_input1_the_decoders_own_generator_is_the_fallback(self):
        video = self.dec.nodes["hdmi_output"][0]["video"]
        video["available_inputs"] = [i for i in video["available_inputs"] if i != "ip_input1"]
        self.dec.miss_on_next_select = {self.dark_cell(self.quad)}
        status, body = self.show(self.quad)
        self.assertEqual(body["recovery"]["neutral_input"], "generator")
        self.assertEqual(body["status"], "VERIFIED", body)

    def test_a_decoder_with_no_neutral_input_is_not_touched(self):
        video = self.dec.nodes["hdmi_output"][0]["video"]
        video["available_inputs"] = [i for i in video["available_inputs"]
                                     if i not in ("generator", "ip_input1")]
        self.dec.miss_on_next_select = {self.dark_cell(self.quad)}
        status, body = self.show(self.quad)
        self.assertEqual(status, 200, body)
        self.assertFalse(body["recovery"]["attempted"])
        self.assertEqual(body["status"], "VERIFIED — WINDOW NOT LOCKED")
        self.assertEqual(self.neutral_selections(), [])

    # ---- failure of the recovery itself ---------------------------------------

    def test_a_neutral_selection_the_device_ignored_is_reported_and_the_picture_kept(self):
        self.dec.miss_on_next_select = {self.dark_cell(self.quad)}
        real = self.dec._set
        state = {"swallowed": False}

        def ignore_neutral(request):
            entries = request.get("config") or []
            if (request.get("name") == "hdmi_output" and not state["swallowed"] and any(
                    (e.get("video") or {}).get("input") == "ip_input1" for e in entries)):
                state["swallowed"] = True
                return {"error": False}                 # accepted, answered success, ignored
            return real(request)
        self.dec._set = ignore_neutral
        status, body = self.show(self.quad)
        self.assertEqual(status, 200, body)
        self.assertTrue(state["swallowed"], "the neutral selection was never attempted")
        self.assertIn("error", body["recovery"])
        self.assertEqual(self.dec.nodes["hdmi_output"][0]["video"]["input"], self.quad)

    def test_a_neutral_selection_that_cannot_be_confirmed_is_still_undone(self):
        """The device took the selection but the read-back never answered: the
        recovery cannot tell, so it must still put the Multiview back."""
        self.dec.miss_on_next_select = {self.dark_cell(self.quad)}
        real = self.dec.handle

        def silent_while_on_neutral(payload):
            if (payload.get("config_get") == "hdmi_output" and self.dec.selection_log
                    and self.dec.selection_log[-1][1] == "ip_input1"):
                raise OSError("the device did not answer")
            return real(payload)
        self.dec.handle = silent_while_on_neutral
        status, body = self.show(self.quad)
        self.dec.handle = real
        self.assertEqual(status, 200, body)
        self.assertIn("error", body["recovery"])
        self.assertEqual(self.dec.nodes["hdmi_output"][0]["video"]["input"], self.quad,
                         "an unconfirmed neutral selection left the display on the neutral input")

    def test_the_way_back_is_retried_so_the_display_is_never_left_on_the_neutral_input(self):
        self.dec.miss_on_next_select = {self.dark_cell(self.quad)}
        real = self.dec._set
        state = {"refused": 0}

        def refuse_first_return(request):
            entries = request.get("config") or []
            left = any(s[1] == "ip_input1" for s in self.dec.selection_log)
            if (request.get("name") == "hdmi_output" and left and state["refused"] == 0 and any(
                    (e.get("video") or {}).get("input") == self.quad for e in entries)):
                state["refused"] += 1
                return {"error": True, "error_message": "device busy"}
            return real(request)
        self.dec._set = refuse_first_return
        status, body = self.show(self.quad)
        self.assertEqual(state["refused"], 1)
        self.assertEqual(self.dec.nodes["hdmi_output"][0]["video"]["input"], self.quad,
                         "a refused return left the display on the neutral input")
        self.assertTrue(body["recovery"]["cleared"], body["recovery"])

    def test_a_refused_neutral_selection_leaves_the_multiview_on_the_display(self):
        self.dec.miss_on_next_select = {self.dark_cell(self.quad)}
        real = self.dec._set

        def refuse_generator(request):
            entries = request.get("config") or []
            if request.get("name") == "hdmi_output" and any(
                    (e.get("video") or {}).get("input") == "ip_input1" for e in entries):
                return {"error": True, "error_message": "device busy"}
            return real(request)
        self.dec._set = refuse_generator
        status, body = self.show(self.quad)
        self.assertEqual(status, 200, body)
        self.assertIn("error", body["recovery"])
        self.assertTrue(body["ok"])
        self.assertEqual(self.dec.nodes["hdmi_output"][0]["video"]["input"], self.quad,
                         "a failed recovery left the display on the wrong thing")


class RecoveryConstantTests(unittest.TestCase):
    def test_the_shipped_pause_is_at_least_the_measured_second(self):
        source = open(srv.__file__, encoding="utf-8").read()
        import re
        found = re.search(r"^MULTIVIEW_RECOVERY_PAUSE\s*=\s*([0-9.]+)", source, re.M)
        self.assertIsNotNone(found)
        self.assertGreaterEqual(float(found.group(1)), 1.0)


if __name__ == "__main__":
    unittest.main()
