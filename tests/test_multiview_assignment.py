"""Window assignment on the server: the size rule, one transaction, one writer.

Release blockers F and G. A source cannot feed two windows of different sizes
(one Encoder 2 has one scaler), so moving it to a window of another size means
taking it out of the first. The page does that as ONE request -- `clear_cells`
on the switch -- because two requests would leave a half-applied display when the
second failed. And the whole switch (read, plan, write, verify) holds the
Multiview lock, so two switches that read the same state cannot each plan from a
world the other was about to change.

All of this runs against the simulated devices in test_multiview.py; nothing
here can reach hardware.
"""
import copy
import threading
import unittest
from unittest import mock

import omni_multiview as mv
import OmniMatrix_upgrade_server_v7_6y as srv
from tests.test_multiview import (  # noqa: F401  (the base builds the fake bench)
    MultiviewTestBase, DECODER_IP, ENCODER_IP, ENCODER2_IP, NEW_WALLPLATE_IP)


class AssignmentTransactionTests(MultiviewTestBase):

    def _pip_shown(self, assignments):
        self.decoder()
        body = self.client.post("/api/multiview/apply", json={
            "decoder": DECODER_IP, "layout": "pip-bottom-right",
            "canvas": mv.ACTIVE_CANVAS, "name": "Inset",
            "assignments": assignments}).get_json()
        self.assertTrue(body["ok"], body)
        name = body["plan"]["object_name"]
        shown = self.client.post("/api/multiview/show",
                                 json={"decoder": DECODER_IP, "name": name})
        self.assertTrue(shown.get_json()["ok"], shown.get_json())
        return name

    def _switch(self, name, cell, source, **extra):
        payload = {"decoder": DECODER_IP, "name": name, "cell": cell,
                   "source": source}
        payload.update(extra)
        return self.client.post("/api/multiview/switch", json=payload)

    def _inputs(self, name):
        obj = next(o for o in self.devices[DECODER_IP].nodes["multiview"]
                   if str(o.get("name") or "") == name)
        return {str(s.get("name") or "").split(" (")[0]: s.get("input")
                for s in obj["subframes"]}

    def test_a_source_in_two_sizes_is_refused_and_nothing_is_written(self):
        self.encoder(ENCODER_IP)
        name = self._pip_shown({"main": ENCODER_IP})
        decoder = self.devices[DECODER_IP]
        before = self._inputs(name)
        mark = len(decoder.writes)
        response = self._switch(name, "bottom_right", ENCODER_IP)
        self.assertEqual(response.status_code, 409, response.get_json())
        self.assertFalse(response.get_json()["ok"])
        self.assertEqual(self._inputs(name), before,
                         "a refused switch changed the decoder")
        self.assertEqual([w for w in decoder.writes[mark:]
                          if w[0] not in ("config_get",)], [],
                         "a refused switch still wrote to the decoder")

    def test_moving_it_is_one_request_that_empties_the_old_window(self):
        self.encoder(ENCODER_IP)
        name = self._pip_shown({"main": ENCODER_IP})
        response = self._switch(name, "bottom_right", ENCODER_IP,
                                clear_cells=["main"], op_id="op-7")
        body = response.get_json()
        self.assertEqual(response.status_code, 200, body)
        self.assertTrue(body["ok"], body)
        by_cell = self._inputs(name)
        self.assertEqual(by_cell.get("main"), "",
                         "the old window still holds the source: %s" % by_cell)
        self.assertTrue(by_cell.get("bottom_right"),
                        "the new window did not take it: %s" % by_cell)
        self.assertEqual(body["op_id"], "op-7", "the operation id was not echoed")
        # A window emptied on purpose is not a window that went dark.
        self.assertEqual(body["regressed_windows"], [], body["regressed_windows"])
        # And the stored preset agrees, so a refresh shows the same thing.
        device = {d.get("ip"): d for d in srv._load_cache()}.get(DECODER_IP, {})
        saved, _record = srv._saved_assignments(device, name)
        self.assertEqual(saved, {"bottom_right": ENCODER_IP}, saved)

    def test_the_refusal_names_its_operation_too(self):
        self.encoder(ENCODER_IP)
        name = self._pip_shown({"main": ENCODER_IP})
        body = self._switch(name, "bottom_right", ENCODER_IP,
                            op_id="op-9").get_json()
        self.assertEqual(body.get("op_id"), "op-9", body)

    def test_clear_cells_must_be_a_list(self):
        self.encoder(ENCODER_IP)
        name = self._pip_shown({"main": ENCODER_IP})
        response = self._switch(name, "bottom_right", ENCODER_IP,
                                clear_cells="main")
        self.assertEqual(response.status_code, 400, response.get_json())

    def test_a_clear_that_is_not_needed_changes_nothing_it_was_not_asked_to(self):
        """Emptying a window that is not the source's own removes only that one."""
        self.encoder(ENCODER_IP)
        self.encoder(ENCODER2_IP)
        name = self._pip_shown({"main": ENCODER_IP, "bottom_right": ENCODER2_IP})
        response = self._switch(name, "bottom_right", ENCODER_IP,
                                clear_cells=["main"])
        self.assertTrue(response.get_json()["ok"], response.get_json())
        by_cell = self._inputs(name)
        self.assertEqual(by_cell.get("main"), "")
        self.assertTrue(by_cell.get("bottom_right"))

    def test_the_whole_switch_holds_the_multiview_lock(self):
        """Read, plan, write and verify are one critical section.

        Two switches that each read the display before either wrote would plan
        from a world the other was about to change, and the later write would
        silently undo the earlier one. The decoder read is the first step, so it
        is the one to look at.
        """
        self.encoder(ENCODER_IP)
        name = self._pip_shown({"main": ENCODER_IP})
        owners = []
        real = srv._decoder_state

        def watching(ip, *args, **kwargs):
            owners.append(srv._multiview_apply_lock._is_owned())
            return real(ip, *args, **kwargs)

        srv._decoder_state = watching
        try:
            self._switch(name, "bottom_right", ENCODER_IP, clear_cells=["main"])
        finally:
            srv._decoder_state = real
        self.assertTrue(owners, "the switch never read the decoder")
        self.assertTrue(all(owners),
                        "the decoder was read outside the Multiview lock")

    def test_concurrent_switches_do_not_interleave(self):
        self.encoder(ENCODER_IP)
        self.encoder(ENCODER2_IP)
        name = self._pip_shown({"main": ENCODER_IP})
        inside = []
        depth = [0]
        real = srv._decoder_state
        guard = threading.Lock()

        def watching(ip, *args, **kwargs):
            with guard:
                depth[0] += 1
                inside.append(depth[0])
            try:
                return real(ip, *args, **kwargs)
            finally:
                with guard:
                    depth[0] -= 1

        srv._decoder_state = watching
        results = []

        def go(cell, source, clear):
            client = srv.app.test_client()
            results.append(client.post("/api/multiview/switch", json={
                "decoder": DECODER_IP, "name": name, "cell": cell,
                "source": source, "clear_cells": clear}).status_code)

        try:
            threads = [threading.Thread(target=go, args=("bottom_right", ENCODER2_IP, [])),
                       threading.Thread(target=go, args=("bottom_right", "", []))]
            for t in threads:
                t.start()
            for t in threads:
                t.join(30)
        finally:
            srv._decoder_state = real
        self.assertEqual(len(results), 2, "a switch never finished")
        self.assertLessEqual(max(inside), 1,
                             "two switches read the decoder at the same time")



class SharedInputClearTests(MultiviewTestBase):
    """Clearing one of two windows that share an input.

    Measured on the bench decoder: unbinding the second window while the first
    stays bound leaves the first reading inactive. Releasing the sibling first,
    then the window being emptied, then binding the sibling again lands it live.
    The simulated decoder cannot reproduce the device quirk, so what is held here
    is the order of writes the application makes, plus the pure rule that decides
    when it applies.
    """

    def _shown_pair(self):
        self.encoder(ENCODER_IP)
        self.decoder()
        body = self.client.post("/api/multiview/apply", json={
            "decoder": DECODER_IP, "layout": "2x2", "canvas": mv.ACTIVE_CANVAS,
            "name": "Pair", "assignments": {"top_left": ENCODER_IP,
                                            "top_right": ENCODER_IP}}).get_json()
        self.assertTrue(body["ok"], body)
        name = body["plan"]["object_name"]
        shown = self.client.post("/api/multiview/show",
                                 json={"decoder": DECODER_IP, "name": name})
        self.assertTrue(shown.get_json()["ok"], shown.get_json())
        return name

    def _subframe_writes(self, decoder, mark):
        out = []
        for kind, node, payload in decoder.writes[mark:]:
            if kind == "config_set" and node == "multiview":
                for entry in payload:
                    for sub in entry.get("subframes") or []:
                        out.append((str(sub.get("name")).split(" (")[0], sub.get("input")))
        return out

    def test_the_sibling_is_released_first_and_bound_again_last(self):
        name = self._shown_pair()
        decoder = self.devices[DECODER_IP]
        bound = {s["name"].split(" (")[0]: s["input"]
                 for o in decoder.nodes["multiview"] if o["name"] == name
                 for s in o["subframes"]}
        shared = bound["top_left"]
        self.assertTrue(shared and shared == bound["top_right"], bound)
        mark = len(decoder.writes)
        body = self.client.post("/api/multiview/switch", json={
            "decoder": DECODER_IP, "name": name, "cell": "top_left",
            "source": ""}).get_json()
        self.assertTrue(body["ok"], body)
        writes = self._subframe_writes(decoder, mark)
        self.assertEqual(writes, [("top_right", ""), ("top_left", ""),
                                  ("top_right", shared)],
                         "the transition is not release sibling, empty window, "
                         "bind sibling: %s" % writes)

    def test_the_end_state_is_the_requested_one(self):
        name = self._shown_pair()
        self.client.post("/api/multiview/switch", json={
            "decoder": DECODER_IP, "name": name, "cell": "top_left", "source": ""})
        decoder = self.devices[DECODER_IP]
        by_cell = {s["name"].split(" (")[0]: s["input"]
                   for o in decoder.nodes["multiview"] if o["name"] == name
                   for s in o["subframes"]}
        self.assertEqual(by_cell["top_left"], "")
        self.assertTrue(by_cell["top_right"], by_cell)

    def test_a_window_alone_on_its_input_gets_no_extra_writes(self):
        self.encoder(ENCODER_IP)
        self.encoder(ENCODER2_IP)
        self.decoder()
        body = self.client.post("/api/multiview/apply", json={
            "decoder": DECODER_IP, "layout": "2x2", "canvas": mv.ACTIVE_CANVAS,
            "name": "Solo", "assignments": {"top_left": ENCODER_IP,
                                            "top_right": ENCODER2_IP}}).get_json()
        name = body["plan"]["object_name"]
        self.client.post("/api/multiview/show",
                         json={"decoder": DECODER_IP, "name": name})
        decoder = self.devices[DECODER_IP]
        mark = len(decoder.writes)
        self.client.post("/api/multiview/switch", json={
            "decoder": DECODER_IP, "name": name, "cell": "top_left", "source": ""})
        writes = self._subframe_writes(decoder, mark)
        self.assertEqual(writes, [("top_left", "")], writes)

    def test_a_pair_emptied_together_goes_one_window_at_a_time(self):
        """The Replace that broke the next refill: both windows of a shared pair
        emptied in ONE write. Measured on the bench decoder, that leaves the first
        window unable to light up again, although every write verifies."""
        name = self._shown_pair()
        decoder = self.devices[DECODER_IP]
        shared = next(s["input"] for o in decoder.nodes["multiview"]
                      if o["name"] == name for s in o["subframes"]
                      if s["name"].startswith("top_left"))
        mark = len(decoder.writes)
        body = self.client.post("/api/multiview/switch", json={
            "decoder": DECODER_IP, "name": name, "cell": "top_left",
            "source": "", "clear_cells": ["top_right"]}).get_json()
        self.assertTrue(body["ok"], body)
        writes = self._subframe_writes(decoder, mark)
        self.assertEqual(writes, [("top_right", ""), ("top_left", ""),
                                  ("top_right", shared), ("top_right", "")], writes)
        sets = [w for w in decoder.writes[mark:] if w[0] == "config_set" and w[1] == "multiview"]
        for _kind, _node, payload in sets:
            emptied = [sub["name"] for entry in payload for sub in entry.get("subframes") or []
                       if sub.get("input") == ""]
            self.assertLessEqual(len(emptied), 1,
                                 "two windows were emptied in one write: %s" % emptied)

    def test_replace_that_empties_a_pair_is_ordered_and_then_binds_the_new_window(self):
        self.encoder(ENCODER_IP)
        self.encoder(ENCODER2_IP)
        self.decoder()
        body = self.client.post("/api/multiview/apply", json={
            "decoder": DECODER_IP, "layout": "1+3-horizontal-bottom",
            "canvas": mv.ACTIVE_CANVAS, "name": "Rep",
            "assignments": {"main": ENCODER2_IP, "bottom_left": ENCODER_IP,
                            "bottom_middle": ENCODER_IP}}).get_json()
        self.assertTrue(body["ok"], body)
        name = body["plan"]["object_name"]
        self.client.post("/api/multiview/show", json={"decoder": DECODER_IP, "name": name})
        decoder = self.devices[DECODER_IP]
        mark = len(decoder.writes)
        body = self.client.post("/api/multiview/switch", json={
            "decoder": DECODER_IP, "name": name, "cell": "main", "source": ENCODER_IP,
            "clear_cells": ["bottom_left", "bottom_middle"]}).get_json()
        self.assertTrue(body["ok"], body)
        writes = self._subframe_writes(decoder, mark)
        emptied = [w for w in writes if w[1] == ""]
        self.assertEqual([w[0] for w in emptied][:2], ["bottom_middle", "bottom_left"],
                         writes)
        main_at = next(i for i, w in enumerate(writes) if w[0] == "main" and w[1])
        self.assertTrue(all(i < main_at for i, w in enumerate(writes) if w[1] == ""),
                        "the new window was bound before the old ones were emptied: %s" % writes)
        by_cell = {sub["name"].split(" (")[0]: sub["input"]
                   for o in decoder.nodes["multiview"] if o["name"] == name
                   for sub in o["subframes"]}
        self.assertEqual((by_cell["bottom_left"], by_cell["bottom_middle"]), ("", ""))
        self.assertTrue(by_cell["main"], by_cell)

    def test_the_rule_names_the_inputs_that_need_ordering(self):
        target = {"subframes": [{"name": "a (1x1)", "input": "ip_input2"},
                                {"name": "b (1x1)", "input": "ip_input2"},
                                {"name": "c (1x1)", "input": "ip_input4"}]}
        plan = {"windows": [{"label": "a (1x1)", "ip_input": None},
                            {"label": "b (1x1)", "ip_input": {"ip_input": "ip_input2"}},
                            {"label": "c (1x1)", "ip_input": {"ip_input": "ip_input4"}}]}
        self.assertEqual(srv._shared_input_groups(target, plan),
                         {"ip_input2": (["a (1x1)"], ["b (1x1)"])})
        plan["windows"][1]["ip_input"] = None          # both emptied: still needs an order
        self.assertEqual(srv._shared_input_groups(target, plan),
                         {"ip_input2": (["a (1x1)", "b (1x1)"], [])})
        plan["windows"][0]["ip_input"] = {"ip_input": "ip_input2"}   # nothing emptied from a shared input
        plan["windows"][1]["ip_input"] = {"ip_input": "ip_input2"}
        self.assertEqual(srv._shared_input_groups(target, plan), {})
        lone = {"windows": [{"label": "c (1x1)", "ip_input": None}]}
        self.assertEqual(srv._shared_input_groups(target, lone), {})   # alone on its input: plain clear

    def test_a_sibling_the_plan_moves_elsewhere_is_not_released_and_bound_back(self):
        """Only a window that STAYS on the input needs to be protected. One the plan
        moves to another input is rewritten by the ordinary change anyway; releasing
        it and binding it back to the old input first is two extra writes and a
        glitch on a display somebody is watching."""
        target = {"subframes": [{"name": "a (1x1)", "input": "ip_input2"},
                                {"name": "b (1x1)", "input": "ip_input2"}]}
        plan = {"windows": [{"label": "a (1x1)", "ip_input": None},
                            {"label": "b (1x1)", "ip_input": {"ip_input": "ip_input4"}}]}
        self.assertEqual(srv._shared_input_groups(target, plan), {},
                         "a window that is leaving the input was treated as one that stays")
        plan["windows"][1]["ip_input"] = None          # leaving by being emptied is not staying either
        self.assertEqual(srv._shared_input_groups(target, plan),
                         {"ip_input2": (["a (1x1)", "b (1x1)"], [])})


class OwnershipAfterSwitchTests(MultiviewTestBase):
    """A live switch can move a stream to another pool input. The input is only
    OmniSuite's while its record names the stream the input carries, so the switch
    must keep the record current -- Show already does. Left stale, the next Show
    of anything else refused with "carrying a stream OmniSuite did not configure"
    about an input OmniSuite had configured seconds earlier (seen on the bench
    after a run of live switches)."""

    def _record(self, name):
        device = {d.get("ip"): d for d in srv._load_cache()}.get(DECODER_IP, {})
        return (srv._multiview_meta_for(device) or {}).get(name) or {}

    def _shown(self, assignments, layout="2x2", label="Own"):
        self.decoder()
        body = self.client.post("/api/multiview/apply", json={
            "decoder": DECODER_IP, "layout": layout, "canvas": mv.ACTIVE_CANVAS,
            "name": label, "assignments": assignments}).get_json()
        self.assertTrue(body["ok"], body)
        name = body["plan"]["object_name"]
        shown = self.client.post("/api/multiview/show",
                                 json={"decoder": DECODER_IP, "name": name})
        self.assertTrue(shown.get_json()["ok"], shown.get_json())
        return name

    def test_a_stream_the_switch_puts_on_a_new_input_is_recorded_as_owned(self):
        self.encoder(ENCODER_IP)
        self.encoder(ENCODER2_IP)
        name = self._shown({"top_left": ENCODER_IP})
        before = dict(self._record(name).get("allocated_inputs") or {})
        body = self.client.post("/api/multiview/switch", json={
            "decoder": DECODER_IP, "name": name, "cell": "top_right",
            "source": ENCODER2_IP}).get_json()
        self.assertTrue(body["ok"], body)
        held = self._record(name).get("allocated_inputs") or {}
        new_input = next(w["ip_input"]["ip_input"] for w in body["plan"]["windows"]
                         if w["cell"] == "top_right")
        self.assertNotIn(new_input, before, "precondition: the stream already had an input")
        self.assertEqual(held.get(new_input),
                         next(w["multicast"]["address"] for w in body["plan"]["windows"]
                              if w["cell"] == "top_right"),
                         "the input the switch configured is not recorded: %s" % held)

    def test_an_input_the_switch_released_is_no_longer_claimed(self):
        self.encoder(ENCODER_IP)
        self.encoder(ENCODER2_IP)
        name = self._shown({"top_left": ENCODER_IP, "top_right": ENCODER2_IP})
        held = dict(self._record(name).get("allocated_inputs") or {})
        self.assertEqual(len(held), 2, held)
        body = self.client.post("/api/multiview/switch", json={
            "decoder": DECODER_IP, "name": name, "cell": "top_right",
            "source": ""}).get_json()
        self.assertTrue(body["ok"], body)
        for released in body.get("released") or []:
            self.assertNotIn(released, self._record(name).get("allocated_inputs") or {},
                             "a released input is still recorded as owned")


class StaleOwnershipTests(unittest.TestCase):
    """An input OmniSuite configured for the displayed Multiview must be released
    when the next one does not need it, whatever other saved Multiviews still say.

    Saved Multiviews keep whatever allocation they last had; entries are never
    removed once a stream moves to another input. One such stale entry used to veto
    ownership that the displayed Multiview's own record proved, so the input stayed
    on, carrying a stream nothing referenced, and every later change that needed it
    was refused as "not configured by OmniSuite"."""

    ADDR = {"ip_input2": "239.1.0.2", "ip_input4": "239.1.0.4", "ip_input6": "239.1.0.6"}

    def _state(self, shown):
        return {
            "multiview": [{"name": "A", "subframes": [{"name": "w (1x1)", "input": "ip_input2"}]},
                          {"name": "B", "subframes": [{"name": "w (1x1)", "input": "ip_input4"}]}],
            "hdmi_output": {"video": {"input": shown}, "audio": {"input": "ip_input3"}},
            "ip_input": [{"name": n, "enabled": True, "port": 1000,
                          "multicast": {"address": a}} for n, a in self.ADDR.items()]
                        + [{"name": "ip_input3", "enabled": True, "port": 1000,
                            "multicast": {"address": "239.9.9.9"}}],
        }

    def _release(self, meta, shown):
        real = srv._multiview_meta_for
        srv._multiview_meta_for = lambda device: copy.deepcopy(meta)
        try:
            return srv._releasable_pool_inputs("192.0.2.10", {}, self._state(shown), {"ip_input2"})
        finally:
            srv._multiview_meta_for = real

    def test_a_stale_entry_in_a_saved_multiview_does_not_veto_the_displayed_ones_ownership(self):
        meta = {"A": {"allocated_inputs": {"ip_input2": self.ADDR["ip_input2"],
                                           "ip_input6": self.ADDR["ip_input6"]}},
                # Saved, not displayed, and its record names a stream ip_input6 no longer carries.
                "B": {"allocated_inputs": {"ip_input6": "239.7.7.7"}}}
        release, kept = self._release(meta, shown="A")
        self.assertIn("ip_input6", release,
                      "an input the displayed Multiview configured was kept: %s" % (kept,))

    def test_the_displayed_multiviews_own_stale_record_still_vetoes(self):
        meta = {"A": {"allocated_inputs": {"ip_input2": self.ADDR["ip_input2"],
                                           "ip_input6": "239.7.7.7"}},
                "B": {"allocated_inputs": {}}}
        release, kept = self._release(meta, shown="A")
        self.assertNotIn("ip_input6", release,
                         "an input something repointed under the displayed Multiview was released")
        self.assertIn(("ip_input6", "OmniSuite did not configure it"), kept)


class HealDarkWindowTests(MultiviewTestBase):
    """A window bound alone on an input that reads inactive although its stream is
    arriving (the broken state a pair emptied in one write leaves behind) is healed
    by pairing it with an empty window of the same size and releasing the pair in
    the proven order. The simulated decoder models the measured behaviour: such a
    window is dark while alone and lights once something shares its input."""

    def _shown_with_empty_windows(self):
        self.encoder(ENCODER_IP)
        self.encoder(ENCODER2_IP)
        self.decoder()
        body = self.client.post("/api/multiview/apply", json={
            "decoder": DECODER_IP, "layout": "2x2", "canvas": mv.ACTIVE_CANVAS,
            "name": "Heal", "assignments": {"bottom_right": ENCODER2_IP}}).get_json()
        self.assertTrue(body["ok"], body)
        name = body["plan"]["object_name"]
        shown = self.client.post("/api/multiview/show", json={"decoder": DECODER_IP, "name": name})
        self.assertTrue(shown.get_json()["ok"], shown.get_json())
        return name

    def _break_alone(self, decoder, label_prefix="top_left", heals_when_paired=True):
        real = decoder._apply_window_activity
        state = {"healed": False}

        def apply(multiviews):
            real(multiviews)
            for obj in multiviews:
                subs = obj.get("subframes") or []
                for sub in subs:
                    if not str(sub.get("name", "")).startswith(label_prefix) or not sub.get("input"):
                        continue
                    paired = any(o is not sub and o.get("input") == sub.get("input") for o in subs)
                    if paired and heals_when_paired:
                        state["healed"] = True
                    lit = paired or state["healed"]
                    sub["video"]["input"] = {"active": bool(lit)}
                    sub["video"]["output"] = {"active": bool(lit)}

        decoder._apply_window_activity = apply
        return real

    def _quick(self):
        """A moment for a window, not ten seconds (the default is fixed when the
        function is defined, so patching the constant would change nothing)."""
        real = srv._await_window_lock
        return mock.patch.object(
            srv, "_await_window_lock",
            lambda ip, name, timeout=0.01, interval=0.01: real(ip, name, 0.01, 0.01))

    def _switch_in(self, name, cell="top_left"):
        with self._quick():
            return self.client.post("/api/multiview/switch", json={
                "decoder": DECODER_IP, "name": name, "cell": cell,
                "source": ENCODER_IP}).get_json()

    def test_a_dark_window_is_healed_through_an_empty_one_and_reported_as_healed(self):
        name = self._shown_with_empty_windows()
        decoder = self.devices[DECODER_IP]
        real = self._break_alone(decoder)
        mark = len(decoder.writes)
        body = self._switch_in(name)
        decoder._apply_window_activity = real
        self.assertTrue(body["ok"], body)
        self.assertEqual(body["healing"]["healed"], ["top_left (960x544)"], body["healing"])
        by_cell = {s["name"].split(" (")[0]: s["input"]
                   for o in decoder.nodes["multiview"] if o["name"] == name for s in o["subframes"]}
        self.assertTrue(by_cell["top_left"], "the healed window is not bound: %s" % by_cell)
        self.assertEqual((by_cell["top_right"], by_cell["bottom_left"]), ("", ""),
                         "the helper window was left bound: %s" % by_cell)

    def test_the_helper_is_put_back_empty_when_pairing_does_not_light_the_window(self):
        name = self._shown_with_empty_windows()
        decoder = self.devices[DECODER_IP]
        real = self._break_alone(decoder, heals_when_paired=False)
        body = self._switch_in(name)
        decoder._apply_window_activity = real
        by_cell = {s["name"].split(" (")[0]: s["input"]
                   for o in decoder.nodes["multiview"] if o["name"] == name for s in o["subframes"]}
        self.assertEqual((by_cell["top_right"], by_cell["bottom_left"]), ("", ""),
                         "a helper window was left showing the stream: %s" % by_cell)
        self.assertFalse(body["ok"], "a window that never lit was reported as a success")

    def test_nothing_is_borrowed_when_no_empty_window_of_the_same_size_exists(self):
        self.encoder(ENCODER_IP)
        self.encoder(ENCODER2_IP)
        self.decoder()
        body = self.client.post("/api/multiview/apply", json={
            "decoder": DECODER_IP, "layout": "pip-bottom-right", "canvas": mv.ACTIVE_CANVAS,
            "name": "Full", "assignments": {"main": ENCODER2_IP}}).get_json()
        name = body["plan"]["object_name"]
        self.client.post("/api/multiview/show", json={"decoder": DECODER_IP, "name": name})
        decoder = self.devices[DECODER_IP]
        real = self._break_alone(decoder, label_prefix="bottom_right", heals_when_paired=True)
        with self._quick():
            body = self.client.post("/api/multiview/switch", json={
                "decoder": DECODER_IP, "name": name, "cell": "bottom_right",
                "source": ENCODER_IP}).get_json()
        decoder._apply_window_activity = real
        heal = body.get("healing") or {}
        self.assertEqual(heal.get("healed"), [], heal)
        self.assertIn("no empty window of the same size",
                      " ".join(a["result"] for a in heal.get("attempted", [])), heal)


class HealRulesTests(unittest.TestCase):
    """The conditions under which a window may be borrowed, held without a device."""

    @staticmethod
    def _object(subframes):
        return [{"name": "mv", "subframes": [{"name": n, "input": i} for n, i in subframes]}]

    def _run(self, subframes, dark, lit_after_pair=True):
        writes = []
        state = {"obj": self._object(subframes)}

        def config(ip, node, **kwargs):
            return copy.deepcopy(state["obj"])

        def put(ip, node, config_):
            for entry in config_["subframes"]:
                for sub in state["obj"][0]["subframes"]:
                    if sub["name"] == entry["name"]:
                        sub["input"] = entry["input"]
            writes.append([(e["name"].split(" (")[0], e["input"]) for e in config_["subframes"]])
            return True, ""

        def lock(ip, name, timeout=0, interval=0):
            wins = [{"subframe": s["name"], "ip_input": s["input"],
                     "active": lit_after_pair or s["name"] != dark["subframe"]}
                    for s in state["obj"][0]["subframes"] if s["input"]]
            return wins, [w for w in wins if not w["active"]]

        with mock.patch.object(srv, "_mv_config", config),                 mock.patch.object(srv, "_mv_set", put),                 mock.patch.object(srv, "_await_window_lock", lock):
            report = srv._heal_dark_windows("192.0.2.10", "mv", [dark])
        return report, writes, state["obj"][0]["subframes"]

    DARK = {"subframe": "a (640x360)", "ip_input": "ip_input4", "active": False}

    def test_a_window_that_shares_its_input_is_not_healed_by_borrowing(self):
        report, writes, _ = self._run([("a (640x360)", "ip_input4"), ("b (640x360)", "ip_input4"),
                                       ("c (640x360)", "")], self.DARK)
        self.assertEqual(writes, [], "a window that shares its input was rewritten")
        self.assertIn("shares its input", report["attempted"][0]["result"])

    def test_a_window_of_another_size_is_never_borrowed(self):
        report, writes, _ = self._run([("a (640x360)", "ip_input4"), ("big (1280x720)", "")], self.DARK)
        self.assertEqual(writes, [], "an empty window of a different size was bound")
        self.assertIn("no empty window of the same size", report["attempted"][0]["result"])

    def test_the_order_is_pair_then_release_the_dark_window_first(self):
        report, writes, subs = self._run([("a (640x360)", "ip_input4"), ("b (640x360)", "")], self.DARK)
        self.assertEqual(writes, [[("b", "ip_input4")], [("a", "")], [("b", "")], [("a", "ip_input4")]], writes)
        self.assertEqual(report["healed"], ["a (640x360)"])
        self.assertEqual({s["name"].split(" ")[0]: s["input"] for s in subs}, {"a": "ip_input4", "b": ""})

    def test_the_helper_is_emptied_even_when_the_look_at_the_window_fails(self):
        state = {"obj": self._object([("a (640x360)", "ip_input4"), ("b (640x360)", "")])}

        def config(ip, node, **kwargs):
            return copy.deepcopy(state["obj"])

        def put(ip, node, config_):
            for entry in config_["subframes"]:
                for sub in state["obj"][0]["subframes"]:
                    if sub["name"] == entry["name"]:
                        sub["input"] = entry["input"]
            return True, ""

        def lock(ip, name, timeout=0, interval=0):
            raise RuntimeError("the decoder stopped answering")

        with mock.patch.object(srv, "_mv_config", config), mock.patch.object(srv, "_mv_set", put),                 mock.patch.object(srv, "_await_window_lock", lock):
            with self.assertRaises(RuntimeError):
                srv._heal_dark_windows("192.0.2.10", "mv", [self.DARK])
        self.assertEqual(state["obj"][0]["subframes"][1]["input"], "",
                         "a failure left the helper window showing the stream")


class InactiveCanvasIsTheSavedRecordTests(MultiviewTestBase):
    """An inactive Multiview's canvas is a form: its windows are what was saved.

    The pool inputs its subframes name belong to whichever Multiview was shown
    last, and may since have been repointed at other streams. Resolving windows
    from them painted another Multiview's sources onto this one -- a preset saved
    as main=A, top_left=B showed main=B, top_left=A while another was on the
    display -- and Show then applied what was saved, so the picture changed under
    the operator when they pressed Show.
    """

    def _save(self, label, assignments, layout="2x2"):
        body = self.client.post("/api/multiview/apply", json={
            "decoder": DECODER_IP, "layout": layout, "canvas": mv.ACTIVE_CANVAS,
            "name": label, "assignments": assignments}).get_json()
        self.assertTrue(body["ok"], body)
        return body["plan"]["object_name"]

    def _show(self, name):
        body = self.client.post("/api/multiview/show",
                                json={"decoder": DECODER_IP, "name": name}).get_json()
        self.assertTrue(body["ok"], body)

    def _windows(self, name):
        state = self.client.get("/api/multiview/state?ip=%s" % DECODER_IP).get_json()
        view = next(v for v in state["multiviews"] if v["name"] == name)
        return view, {s["cell"]: (s["source"] or {}).get("ip") for s in view["subframes"]
                      if s["source"]}

    def _two_presets(self):
        self.encoder(ENCODER_IP)
        self.encoder(ENCODER2_IP)
        self.encoder(NEW_WALLPLATE_IP)
        self.decoder()
        a = self._save("Alpha", {"top_left": ENCODER_IP, "top_right": ENCODER2_IP})
        # Bravo needs a stream Alpha does not use, so showing it repoints the
        # pool input that Alpha's top_right is bound to.
        b = self._save("Bravo", {"top_left": NEW_WALLPLATE_IP, "top_right": ENCODER_IP})
        return a, b

    def test_an_inactive_preset_shows_its_saved_assignment_not_the_pool(self):
        a, b = self._two_presets()
        self._show(a)
        self._show(b)          # repoints the pool inputs under Alpha's subframes
        view, windows = self._windows(a)
        live = {s["cell"]: s["device_source_ip"] for s in view["subframes"] if s["device_source_ip"]}
        self.assertNotEqual(live.get("top_right"), ENCODER2_IP,
                            "precondition: showing Bravo did not repoint Alpha's input, "
                            "so this test could not have failed")
        view, windows = self._windows(a)
        self.assertFalse(view["selected_on_output"])
        self.assertEqual(windows, {"top_left": ENCODER_IP, "top_right": ENCODER2_IP},
                         "the inactive preset shows another Multiview's sources: %s" % windows)
        for sub in view["subframes"]:
            if sub["source"]:
                self.assertEqual(sub["source_basis"], "saved", sub)

    def test_the_canvas_before_show_is_what_show_applies(self):
        a, b = self._two_presets()
        self._show(a)
        self._show(b)
        _view, before = self._windows(a)
        self._show(a)
        decoder = self.devices[DECODER_IP]
        by_addr = {}
        for ip in (ENCODER_IP, ENCODER2_IP):
            session = next(x for x in self.devices[ip].nodes["sessions"] if x["name"] == "session2")
            by_addr[session["video"]["stream"]["destination_address"]] = ip
        inputs = {i["name"]: (i.get("multicast") or {}).get("address")
                  for i in decoder.nodes["ip_input"]}
        shown = {}
        for o in decoder.nodes["multiview"]:
            if o["name"] == a:
                for sub in o["subframes"]:
                    if sub.get("input"):
                        shown[sub["name"].split(" (")[0]] = by_addr.get(inputs.get(sub["input"]))
        self.assertEqual(shown, before, "Show applied something other than the canvas had shown")

    def test_the_displayed_one_keeps_its_live_subscriptions(self):
        a, b = self._two_presets()
        self._show(a)
        self._show(b)
        view, windows = self._windows(b)
        self.assertTrue(view["selected_on_output"])
        self.assertEqual(windows, {"top_left": NEW_WALLPLATE_IP, "top_right": ENCODER_IP})
        for sub in view["subframes"]:
            if sub["source"]:
                self.assertEqual(sub["source_basis"], "device", sub)

    def test_the_device_is_consulted_only_when_there_is_no_record(self):
        a, b = self._two_presets()
        self._show(a)
        decoder = self.devices[DECODER_IP]
        template = next(o for o in decoder.nodes["multiview"] if o["name"] == a)
        external = copy.deepcopy(template)
        external["name"] = "multiviewMadeOnTheDecoder"
        decoder.nodes["multiview"].append(external)
        view, windows = self._windows("multiviewMadeOnTheDecoder")
        self.assertFalse(view["selected_on_output"])
        self.assertEqual(windows, {"top_left": ENCODER_IP, "top_right": ENCODER2_IP}, windows)
        for sub in view["subframes"]:
            if sub["source"]:
                self.assertEqual(sub["source_basis"], "device", sub)

    def test_a_record_with_an_empty_window_does_not_borrow_one_from_the_pool(self):
        a, b = self._two_presets()
        self._show(a)
        device = {d.get("ip"): d for d in srv._load_cache()}.get(DECODER_IP, {})
        record = copy.deepcopy(srv._multiview_meta_for(device)[a])
        record["windows"] = [w for w in record["windows"] if w["cell"] == "top_left"]
        srv._record_multiview_meta(device, a, record)       # top_right saved as empty
        self._show(b)
        _view, windows = self._windows(a)
        self.assertEqual(windows, {"top_left": ENCODER_IP},
                         "an empty saved window was filled from the decoder: %s" % windows)

    def test_copy_carries_the_saved_assignment_of_an_inactive_preset(self):
        a, b = self._two_presets()
        self._show(a)
        self._show(b)
        definition, error = srv._saved_definition(DECODER_IP, a)
        self.assertEqual(error, "")
        self.assertEqual(definition["assignments"],
                         {"top_left": ENCODER_IP, "top_right": ENCODER2_IP},
                         "Copy would have carried another Multiview's sources")

class ClientRuleTests(unittest.TestCase):
    """What the page does is pinned in tests/multiview_ui_test.js; these hold the
    parts of that contract that live in shared files."""

    def test_the_shared_dialog_can_make_cancel_the_default(self):
        import pathlib
        source = (pathlib.Path(__file__).resolve().parent.parent / "ui" / "confirm.js"
                  ).read_text(encoding="utf-8")
        self.assertIn("cancelDefault", source)
        self.assertRegex(source,
                         r"\(options\.danger \|\| options\.cancelDefault\) \? cancelBtn : okBtn")

    def test_the_page_never_uses_a_native_dialog_for_the_replace_question(self):
        import pathlib
        import re
        source = (pathlib.Path(__file__).resolve().parent.parent / "ui" / "matrix"
                  / "multiview.js").read_text(encoding="utf-8")
        code = re.sub(r"//.*", "", source)
        start = code.index("async function confirmReplace")
        end = code.index("async function assign(")
        body = code[start:end]
        self.assertNotRegex(body, r"(?<![\w.])(confirm|alert|prompt)\(")
        self.assertIn("window.omniConfirm", body)
        self.assertIn("cancelDefault: true", body)


if __name__ == "__main__":
    unittest.main()
