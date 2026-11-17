"""Save is save only; names are validated, never repaired; recovery is one attempt, reported.

Requirements, in terms of what the operator can observe on the (simulated) devices:

  * saving an edit over the Multiview that is on the display writes nothing to any
    device -- not the windows, not an encoder, not audio, not the output -- and the
    edit is shown to the page as saved-but-pending;
  * Show then applies the complete saved configuration, and the edit stops being
    pending;
  * an illegal friendly name is refused with 400, the rules and the offending
    characters, nothing is written, and a legal name is stored exactly as typed;
  * a Show reports its first look and its look after the one recovery separately.

The simulated devices come from test_multiview.py; nothing touches a socket.
"""
import copy
import unittest
from unittest import mock

try:
    from . import test_multiview as base
    from .test_multiview_hardware_state import (HardwareStateBase, SOURCES,
                                                 distinct_assignment)
except ImportError:                                  # loaded by path
    import test_multiview as base
    from test_multiview_hardware_state import HardwareStateBase, SOURCES, distinct_assignment

srv = base.srv
mv = base.mv

QUAD = {"top_left": SOURCES[0], "top_right": SOURCES[1],
        "bottom_left": SOURCES[2], "bottom_right": SOURCES[3]}


class SaveOverDisplayedTests(HardwareStateBase):
    def setUp(self):
        super().setUp()
        self.name = self.store("Live", "2x2", QUAD)
        self.show_ok(self.name)

    def mutating_writes(self):
        return [(ip, w[0], w[1]) for ip, d in self.devices.items() for w in d.writes
                if w[0] != "config_get"]

    def clear_writes(self):
        for d in self.devices.values():
            d.writes = []

    def view(self):
        body = self.client.get("/api/multiview/state?ip=" + base.DECODER_IP).get_json()
        return next(v for v in body["multiviews"] if v["name"] == self.name)

    def save_edit(self, layout, assignment, friendly="Live"):
        return self.save(layout=layout, name=friendly, assignments=assignment,
                         object_name=self.name, update_existing=True)

    def test_saving_an_edit_of_the_displayed_multiview_writes_nothing_anywhere(self):
        edited = {"top_left": SOURCES[3], "top_right": SOURCES[2],
                  "bottom_left": SOURCES[1], "bottom_right": SOURCES[0]}
        before = self.snapshot()
        self.clear_writes()
        response = self.save_edit("2x2", edited)
        body = response.get_json()
        self.assertEqual(response.status_code, 200, body)
        self.assertTrue(body["ok"], body)
        self.assertEqual(self.mutating_writes(), [], "a device was written by Save")
        self.assertEqual(self.snapshot(), before, "device state moved after Save")
        self.assertTrue(body["pending_show"])
        self.assertFalse(body["device_written"])

    def test_a_layout_change_saved_over_the_display_leaves_every_window_alone(self):
        before = self.snapshot()
        self.clear_writes()
        pair = {"left": SOURCES[1], "right": SOURCES[0]}
        body = self.save_edit("side-by-side", pair).get_json()
        self.assertTrue(body["ok"], body)
        self.assertEqual(self.mutating_writes(), [])
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(len(self.obj(self.name)["subframes"]), 4)

    def test_the_audio_input_is_untouched_by_save_and_set_by_show(self):
        edited = dict(QUAD, top_left=SOURCES[3])        # a different main window source
        audio_before = copy.deepcopy(self.pool_input("ip_input3"))
        self.save_edit("2x2", edited)
        self.assertEqual(self.pool_input("ip_input3"), audio_before)
        self.show_ok(self.name)
        self.assertEqual(self.pool_input("ip_input3")["multicast"]["address"],
                         self.s1_audio_destination(SOURCES[3]))

    def test_the_page_is_told_the_edit_is_saved_but_not_shown(self):
        self.save_edit("2x2", dict(QUAD, top_left=SOURCES[3]))
        view = self.view()
        self.assertTrue(view["pending_show"])
        self.assertEqual(dict((c, ip) for c, ip in view["pending"]["windows"])["top_left"],
                         SOURCES[3])
        # the device still says what it said
        sub = next(s for s in view["subframes"] if s["cell"] == "top_left")
        self.assertEqual(sub["source"]["ip"], SOURCES[0])

    def test_show_applies_the_complete_saved_configuration_and_ends_the_pending_state(self):
        pair = {"left": SOURCES[1], "right": SOURCES[0]}
        self.save_edit("side-by-side", pair, friendly="Pair view")
        status, body = self.show(self.name)
        self.assertEqual(status, 200, body)
        self.assertEqual(body["status"], "VERIFIED", body)
        self.assertFalse(body.get("already_shown"))
        self.assert_hardware_matches(self.name, "side-by-side", pair, strict_pool=False)
        self.assertFalse(self.view()["pending_show"])
        self.assertEqual(self.view()["layout"], "side-by-side")

    def test_two_edits_before_show_apply_only_the_last(self):
        self.save_edit("2x2", dict(QUAD, top_left=SOURCES[3]))
        last = {"left": SOURCES[2], "right": SOURCES[3]}
        self.save_edit("side-by-side", last)
        self.show_ok(self.name)
        self.assert_hardware_matches(self.name, "side-by-side", last, strict_pool=False)

    def test_a_failed_show_leaves_the_devices_as_they_were_and_the_edit_pending(self):
        self.save_edit("2x2", dict(QUAD, top_left=SOURCES[3]))
        before = self.snapshot()
        with mock.patch.object(srv, "_await_input_status",
                               lambda ip: ({"active": False}, False)):
            status, body = self.show(self.name)
        self.assertIn("NO ACTIVE VIDEO", body["status"])
        self.assertEqual(self.snapshot(), before)
        self.assertTrue(self.view()["pending_show"], "the edit was lost by a failed Show")

    def test_saving_an_inactive_multiview_still_writes_its_object_and_nothing_else(self):
        other = self.store("Other", "side-by-side", {"left": SOURCES[0], "right": SOURCES[1]})
        self.clear_writes()
        body = self.save(layout="side-by-side", name="Other", object_name=other,
                         update_existing=True,
                         assignments={"left": SOURCES[1], "right": SOURCES[0]}).get_json()
        self.assertTrue(body["ok"])
        self.assertNotIn("pending_show", body)
        self.assertTrue(any(w[1] == "multiview" or w[0] == "method"
                            for w in self.dec.writes))
        self.assertEqual([w for ip in SOURCES for w in self.devices[ip].writes
                          if w[0] != "config_get"], [])


class FriendlyNameTests(HardwareStateBase):
    def attempt(self, name, **extra):
        return self.save(layout="2x2", name=name, assignments={"top_left": SOURCES[0]},
                         **extra)

    def assert_refused_untouched(self, name):
        for d in self.devices.values():
            d.writes = []
        before = self.snapshot()
        response = self.attempt(name)
        body = response.get_json()
        self.assertEqual(response.status_code, 400, (name, body))
        self.assertFalse(body["ok"])
        self.assertEqual(body["classification"], "invalid")
        self.assertIn("1 to 48 characters", body["error"], "the rules were not stated")
        self.assertEqual([w for d in self.devices.values() for w in d.writes
                          if w[0] != "config_get"], [], "a refused name wrote something")
        self.assertEqual(self.snapshot(), before)
        return body

    def test_characters_outside_the_rules_are_refused_and_named(self):
        for bad in ("Bar/Grill", "Lobby <1>", 'Say "hi"', "Cost $5", "tab\there",
                    "emoji \U0001F600", "100% sure", "a;b", "back\\slash"):
            body = self.assert_refused_untouched(bad)
            self.assertIn("not allowed", body["error"], bad)

    def test_the_refusal_names_the_offending_character(self):
        body = self.assert_refused_untouched("Bar/Grill")
        self.assertIn("'/'", body["error"])

    def test_an_empty_or_symbol_only_name_is_refused(self):
        for blank in ("", "   "):
            body = self.assert_refused_untouched(blank)
            self.assertIn("Enter a name", body["error"], "a blank name must say it is missing")
        body = self.assert_refused_untouched("---")
        self.assertIn("no letter or number", body["error"])

    def test_an_overlong_name_is_refused(self):
        body = self.assert_refused_untouched("A" * 49)
        self.assertIn("49 characters", body["error"])

    def test_nothing_is_silently_sanitised_into_a_different_object(self):
        self.assert_refused_untouched("My/Name")
        self.assertEqual([o["name"] for o in self.dec.nodes["multiview"]
                          if o["name"].startswith("multiviewMy")], [])

    def test_a_legal_name_is_stored_exactly_as_typed(self):
        body = self.attempt("Sports Bar (main) - 1+3 & more.").get_json()
        self.assertTrue(body["ok"], body)
        self.assertEqual(body["plan"]["friendly_name"], "Sports Bar (main) - 1+3 & more.")

    def test_every_standard_layout_label_is_a_legal_name(self):
        for layout in mv.LAYOUT_ORDER:
            ok, why = mv.validate_friendly_name(mv.LAYOUTS[layout]["label"])
            self.assertTrue(ok, (layout, why))

    def test_the_validator_itself(self):
        self.assertEqual(mv.validate_friendly_name("  Quad  "), (True, ""))
        self.assertFalse(mv.validate_friendly_name(None)[0])
        self.assertFalse(mv.validate_friendly_name("a\x00b")[0])
        self.assertIn("control character", mv.validate_friendly_name("a\x00b")[1])

    def test_the_plan_endpoint_says_the_same_thing_so_the_page_can_show_it_live(self):
        response = self.client.post("/api/multiview/plan", json={
            "decoder": base.DECODER_IP, "layout": "2x2", "name": "Bad/Name",
            "assignments": {"top_left": SOURCES[0]}})
        plan = response.get_json()["plan"]
        self.assertFalse(plan["preset_ok"])
        self.assertTrue(any("not allowed" in e for e in plan["errors"]))

    def test_an_illegal_rename_of_an_existing_multiview_is_refused_too(self):
        name = self.store("Keep", "2x2", {"top_left": SOURCES[0]})
        response = self.save(layout="2x2", name="Keep/This", object_name=name,
                             update_existing=True, assignments={"top_left": SOURCES[0]})
        self.assertEqual(response.status_code, 400)


class LockVerificationTests(HardwareStateBase):
    def setUp(self):
        super().setUp()
        self.quad = self.store("Quad", "2x2", distinct_assignment("2x2"))
        for target in ("MULTIVIEW_RECOVERY_PAUSE", "MULTIVIEW_WINDOW_SETTLE"):
            patcher = mock.patch.object(srv, target, 0.0)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_a_clean_show_is_an_initial_pass(self):
        _, body = self.show(self.quad)
        v = body["lock_verification"]
        self.assertEqual(v["outcome"], "INITIAL PASS")
        self.assertTrue(v["initial_pass"])
        self.assertFalse(v["recovery_attempted"])
        self.assertIsNone(v["pass_after_recovery"])
        self.assertEqual((v["windows"], v["initial_locked"], v["final_locked"]), (4, 4, 4))

    def test_a_recovered_window_is_a_pass_after_recovery_not_an_initial_pass(self):
        cell = self.subframe(self.quad, "top_right")["name"]
        self.dec.miss_on_next_select = {(self.quad, cell)}
        _, body = self.show(self.quad)
        v = body["lock_verification"]
        self.assertEqual(body["status"], "VERIFIED")
        self.assertEqual(v["outcome"], "PASS AFTER RECOVERY")
        self.assertFalse(v["initial_pass"])
        self.assertEqual(v["initial_dark"], [cell])
        self.assertTrue(v["recovery_attempted"])
        self.assertTrue(v["pass_after_recovery"])
        self.assertEqual((v["initial_locked"], v["final_locked"]), (3, 4))

    def test_a_window_that_never_locks_is_reported_partial_after_exactly_one_recovery(self):
        self.dec.never_locks.update({"ip_input2", "ip_input4", "ip_input6", "ip_input8"})
        _, body = self.show(self.quad)
        v = body["lock_verification"]
        self.assertEqual(body["status"], "PARTIAL — WINDOW NOT LOCKED")
        self.assertEqual(v["outcome"], "NOT LOCKED AFTER RECOVERY")
        self.assertFalse(v["pass_after_recovery"])
        self.assertTrue(v["final_dark"])
        neutral = [p for p in self.dec.selection_log if p[1] in ("generator", "ip_input1")]
        self.assertEqual(len(neutral), 1, "recovery repeated")

    def test_no_neutral_input_means_no_attempt_and_says_so(self):
        video = self.dec.nodes["hdmi_output"][0]["video"]
        video["available_inputs"] = [i for i in video["available_inputs"]
                                     if i not in ("generator", "ip_input1")]
        cell = self.subframe(self.quad, "top_right")["name"]
        self.dec.miss_on_next_select = {(self.quad, cell)}
        _, body = self.show(self.quad)
        v = body["lock_verification"]
        self.assertFalse(v["recovery_attempted"])
        self.assertEqual(v["outcome"], "NOT LOCKED — NO RECOVERY POSSIBLE")


if __name__ == "__main__":
    unittest.main()
