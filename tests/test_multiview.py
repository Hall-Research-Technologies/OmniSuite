"""Multiview: layout geometry, resource planning, and the verified apply.

Nothing here touches hardware. Every address is in the RFC 5737 documentation
ranges, which are not routed, and the device is a simulator that reproduces the
three OmniStream behaviours the real transaction has to survive:

  * `config_set` merges by object name; it never replaces the collection.
  * named objects are created and deleted only through `method`.
  * an invalid write is accepted and answered `error: false`.

That last one is the whole reason the transaction reads everything back, so the
simulator can be told to swallow a write silently and the tests prove the
read-back notices.
"""
# The hardware fence, installed before the application is imported. This module
# can be loaded as part of the `tests` package, or by path with no package at
# all (run_tests.py does that), so it is reached both ways.
try:
    from . import _fence
except ImportError:  # loaded without its package
    import os as _os
    import sys as _sys
    _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
    import _fence
_fence.install()

import collections
import contextlib
import copy
import pathlib
import re
import json
import os
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("OMNI_DATA_DIR", tempfile.mkdtemp(prefix="omnisuite-mvtest-"))

import omni_multiview as mv
import OmniMatrix_upgrade_server_v7_6y as srv

# RFC 5737 documentation addresses. A real bench address must never appear here
# as a reachable target: these tests drive a simulator, and if the patch ever
# failed the address it fell through to could not reach a device.
DECODER_IP = "192.0.2.10"
DECODER2_IP = "192.0.2.11"      # a second decoder, for copy and for groups
ENCODER_IP = "192.0.2.20"
ENCODER2_IP = "192.0.2.21"
OLD_WALLPLATE_IP = "192.0.2.22"      # AT-OMNI-111-WP, excluded
NEW_WALLPLATE_IP = "192.0.2.24"      # HW-OMNI-E4111-WP, eligible
PLAIN_111_IP = "192.0.2.23"

S1_VIDEO = "233.252.0.11"
S1_AUDIO = "233.252.0.12"
S2_VIDEO = "233.252.0.21"


def _devices():
    return [
        {"ip": DECODER_IP, "mac": "00:00:5E:00:53:10", "role": "decoder",
         "type": "Decoder", "model": "hw-omni-d4511", "hostname": "dec-test-01"},
        {"ip": DECODER2_IP, "mac": "00:00:5E:00:53:11", "role": "decoder",
         "type": "Decoder", "model": "hw-omni-d4511", "hostname": "dec-test-02"},
        {"ip": ENCODER_IP, "mac": "00:00:5E:00:53:20", "role": "encoder",
         "type": "Encoder", "model": "hw-omni-e4111", "hostname": "enc-test-01",
         "codec": "VCx",
         "session1_video_mcast": S1_VIDEO, "session1_video_port": 1000,
         "session2_video_mcast": S2_VIDEO, "session2_video_port": 1000},
        {"ip": ENCODER2_IP, "mac": "00:00:5E:00:53:21", "role": "encoder",
         "type": "Encoder", "model": "hw-omni-e4521", "hostname": "enc-test-02",
         "codec": "VCx",
         "session1_video_mcast": "233.252.0.121", "session1_video_port": 1000,
         "session1_audio_mcast": "233.252.0.171",
         "session2_video_mcast": "233.252.0.121", "session2_video_port": 1000},
        {"ip": OLD_WALLPLATE_IP, "mac": "00:00:5E:00:53:22", "role": "encoder",
         "type": "Encoder", "model": "AT-OMNI-111-WP", "hostname": "old-wp-01",
         "codec": "VCx", "session2_video_mcast": "233.252.0.122",
         "session2_video_port": 1000},
        # A 4xxx wall plate. It is a wall plate and it IS an eligible source --
        # the pair of them is what keeps the rule from drifting back to a
        # suffix match.
        {"ip": NEW_WALLPLATE_IP, "mac": "00:00:5E:00:53:24", "role": "encoder",
         "type": "Encoder", "model": "HW-OMNI-E4111-WP", "hostname": "new-wp-01",
         "codec": "VCx",
         "session1_video_mcast": "233.252.0.124", "session1_video_port": 1000,
         "session1_audio_mcast": "233.252.0.174",
         "session2_video_mcast": "233.252.0.124", "session2_video_port": 1000},
        {"ip": PLAIN_111_IP, "mac": "00:00:5E:00:53:23", "role": "encoder",
         "type": "Encoder", "model": "at-omni-111", "hostname": "enc-111-01",
         "codec": "VCx", "session1_video_mcast": "233.252.0.123",
         "session1_audio_mcast": "233.252.0.173",
         "session2_video_mcast": "233.252.0.123", "session2_video_port": 1000},
    ]


def _ip_inputs(count=32):
    inputs = []
    for index in range(1, count + 1):
        inputs.append({"name": f"ip_input{index}", "enabled": False,
                       "interface": "eth1", "port": 1000,
                       "multicast": {"address": ""}, "status": {"packets": 0}})
    # The bench convention, which is a site convention and not an API rule:
    # 1 = Session 1 video, 3 = Session 1 audio, 5 = aux.
    inputs[0].update({"enabled": True, "multicast": {"address": S1_VIDEO}})
    inputs[2].update({"enabled": True, "multicast": {"address": S1_AUDIO}})
    return inputs


def _hdmi_output(wall=False, fast_switching=False, resolution="3840x2160",
                 input_active=True):
    return [{
        "name": "hdmi_output1",
        "video": {"input": "ip_input1",
                  "available_inputs": ["generator", "ip_input1", "ip_input3"],
                  # The decoder's own "Input status": whether the selected input
                  # is actually producing a picture. It is the only check in the
                  # whole transaction that is not a read-back of our own write.
                  "status": {"active": input_active, "framerate": 60,
                             "colorspace": "YUV",
                             "resolution": {"width": 1920, "height": 1080}},
                  # `resolution` is the configured output; `status.resolution` is
                  # what the attached display actually negotiated. They are
                  # different things and the device reports both.
                  "output": {"resolution": resolution,
                             "wall": {"enabled": wall},
                             "fsm": {"enabled": fast_switching, "timeout": 5},
                             "status": {"active": True, "width": 3840, "height": 2160}}},
        "audio": {"input": "ip_input3", "available_inputs": ["generator", "ip_input1", "ip_input3"]},
        "aux": {"input": "ip_input5", "available_inputs": ["ip_input1", "ip_input3"]},
        "sap_input": {"enabled": True, "session": "session1@enc-test-01",
                      "available_sessions": ["session1@enc-test-01"]},
    }]


def _vc2(encoder2_scaler=None, encoder2_input="hdmi_input1", encoder1_bitrate=700,
         encoder2_bitrate=150, thumbnail=None):
    # Measured on the bench: the thumbnail generator belongs to Encoder 1 and
    # every unit ships it enabled at 320x180. `vc2_encoder2` carries no
    # thumbnail fields at all, which is why preview is an Encoder-1 reading.
    return [
        {"name": "vc2_encoder1", "bitrate": encoder1_bitrate, "input": "hdmi_input1",
         "scaler": {"enable": False, "width": 0, "height": 0},
         "thumbnail": {"enable": True, "width": 320, "height": 180}
                      if thumbnail is None else thumbnail},
        {"name": "vc2_encoder2", "bitrate": encoder2_bitrate, "input": encoder2_input,
         "scaler": encoder2_scaler or {"enable": True, "width": 1920, "height": 1080}},
    ]


def _sessions(session2_enabled=False, session2_sap=True):
    return [
        {"name": "session1",
         "video": {"encoder": "vc2_encoder1",
                   "stream": {"enabled": True, "destination_address": S1_VIDEO,
                              "destination_port": 1000,
                              "destination_generate_applied": True,
                              "destination_generate_default": "233.252.0.0/16:1000"}},
         "audio": {"stream": {"enabled": True, "destination_address": S1_AUDIO,
                              "destination_port": 1000}},
         # Session 1 announces itself; that is normal and is left alone.
         "sap": {"enabled": True, "name": "session1"}},
        {"name": "session2",
         "video": {"encoder": "vc2_encoder2",
                   "stream": {"enabled": session2_enabled,
                              "destination_address": S2_VIDEO,
                              "destination_port": 1000,
                              "destination_generate_applied": True,
                              "destination_generate_default": "233.252.0.0/16:1000"}},
         "audio": {"stream": {"enabled": False, "destination_address": "",
                              "destination_port": 5004}},
         # Session 2 must NOT announce itself, or a decoder with SAP enabled
         # adopts it underneath OmniSuite. The fixture ships it enabled because
         # that is how the hardware ships.
         "sap": {"enabled": session2_sap, "name": "session2"}},
    ]


class FakeDevice:
    """An OmniStream device with the merge, method and silent-accept semantics."""

    def __init__(self, nodes, unreachable=False):
        self.nodes = copy.deepcopy(nodes)
        self.unreachable = unreachable
        self.writes = []
        # Reads are recorded as well as writes, because "how much does opening
        # this page cost" is a question about reads, and the lazy Multiview
        # architecture is a claim that can only be checked by counting them.
        self.reads = []
        # name -> how many more writes to accept and then ignore. This is the
        # device behaviour that makes read-back verification mandatory.
        self.swallow = {}
        self.refuse = {}
        # node -> how many writes succeed before it starts refusing. This is how
        # a rollback is made to fail after the forward write already landed.
        self.refuse_after = {}
        self._write_counts = {}

    def handle(self, payload):
        if self.unreachable:
            raise OSError("the device did not answer")
        if "config_get" in payload:
            node = payload["config_get"]
            self.reads.append(node)
            if node not in self.nodes:
                return {"error": True, "error_message": "Config node not found"}
            config = copy.deepcopy(self.nodes[node])
            if node == "multiview":
                self._apply_window_activity(config)
            return {"error": False, "config": config}
        if "config_set" in payload:
            return self._set(payload["config_set"])
        if "method" in payload:
            return self._method(payload["method"])
        return {"error": True, "error_message": "Protocol error"}

    def _apply_window_activity(self, multiviews):
        """A window shows its stream when its input is enabled and carrying one.

        The device derives this; modelling it is what lets a test tell a window
        that is black from one that is live, which is the whole point of the
        per-window check in the show transaction.
        """
        live = {str(i.get("name") or "") for i in (self.nodes.get("ip_input") or [])
                if i.get("enabled") and (i.get("multicast") or {}).get("address")}
        for obj in multiviews:
            for subframe in obj.get("subframes") or []:
                active = str(subframe.get("input") or "") in live
                subframe.setdefault("video", {})
                subframe["video"]["input"] = {"active": active}
                subframe["video"]["output"] = {"active": active}

    def _set(self, request):
        node = request.get("name")
        entries = request.get("config") or []
        self.writes.append(("config_set", node, copy.deepcopy(entries)))
        if node in self.refuse:
            return {"error": True, "error_message": self.refuse[node]}
        seen = self._write_counts.get(node, 0)
        self._write_counts[node] = seen + 1
        if node in self.refuse_after and seen >= self.refuse_after[node]:
            return {"error": True, "error_message": "device busy"}
        existing = self.nodes.setdefault(node, [])
        for entry in entries:
            name = entry.get("name")
            if self.swallow.get(name, 0) > 0:
                # Accepted, answered success, and then ignored -- exactly what
                # the real decoder does with an invalid value.
                self.swallow[name] -= 1
                continue
            target = next((o for o in existing if o.get("name") == name), None)
            if target is None:
                existing.append(copy.deepcopy(entry))
            else:
                _merge(target, entry)
        return {"error": False}

    def _method(self, request):
        for name, options in request.items():
            self.writes.append(("method", name, copy.deepcopy(options)))
            if name in self.refuse:
                return {"error": True, "error_message": self.refuse[name]}
            if name == "add_multiview":
                objects = self.nodes.setdefault("multiview", [])
                if any(o.get("name") == options.get("name") for o in objects):
                    return {"error": True,
                            "error_message": "Multiview %s already exists" % options.get("name")}
                if self.swallow.get(options.get("name"), 0) > 0:
                    self.swallow[options["name"]] -= 1
                    return {"error": False}
                objects.append(_materialise_multiview(options))
                return {"error": False}
            if name == "add_multiview_subframe":
                target = next((o for o in self.nodes.setdefault("multiview", [])
                               if o.get("name") == options.get("multiview")), None)
                if target is None:
                    return {"error": True, "error_message": "Multiview not found"}
                if len(target.get("subframes") or []) >= 4:
                    return {"error": True,
                            "error_message": "Multiview is limited to 4 subframes"}
                target.setdefault("subframes", []).append({
                    "name": options.get("name"), "x": options.get("x", 0),
                    "y": options.get("y", 0),
                    "anchor": options.get("anchor", "top left"),
                    "input": options.get("input", ""),
                    "priority": options.get("priority", 1),
                    "video": {"input": {"active": False}, "output": {"active": False}}})
                return {"error": False}
            if name == "del_multiview_subframe":
                target = next((o for o in self.nodes.setdefault("multiview", [])
                               if o.get("name") == options.get("multiview")), None)
                if target is None:
                    return {"error": True, "error_message": "Multiview not found"}
                target["subframes"] = [s for s in (target.get("subframes") or [])
                                       if s.get("name") != options.get("name")]
                return {"error": False}
            if name == "del_multiview":
                objects = self.nodes.setdefault("multiview", [])
                self.nodes["multiview"] = [
                    o for o in objects if o.get("name") != options.get("name")]
                return {"error": False}
        return {"error": True, "error_message": "Method not found"}


def _merge(target, update):
    for key, value in update.items():
        if key == "subframes":
            existing = target.setdefault("subframes", [])
            for wanted in value:
                found = next((s for s in existing
                              if s.get("name") == wanted.get("name")), None)
                if found is None:
                    existing.append(copy.deepcopy(wanted))
                else:
                    _merge(found, wanted)
            continue
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _merge(target[key], value)
        else:
            target[key] = copy.deepcopy(value)


def _materialise_multiview(options):
    """What the device stores: geometry, plus read-only status it adds itself."""
    stored = {"name": options.get("name"),
              "width": options.get("width"), "height": options.get("height"),
              "background": {"color": {"red": 0, "green": 0, "blue": 0}},
              "slice_info": {"width": {"min": 32, "max": 32},
                             "height": {"min": 8, "max": 8}},
              "subframes": []}
    for subframe in options.get("subframes") or []:
        stored["subframes"].append({
            "name": subframe.get("name"), "x": subframe.get("x", 0),
            "y": subframe.get("y", 0), "anchor": subframe.get("anchor", "top left"),
            "input": subframe.get("input", ""), "priority": subframe.get("priority", 1),
            "video": {"input": {"active": False}, "output": {"active": False}},
        })
    # `layout` and `resolution` are deliberately dropped: the real device does
    # not persist them, which is why OmniSuite has to store the layout itself.
    return stored


class MultiviewTestBase(unittest.TestCase):
    """Routes device traffic to simulators; nothing reaches a socket."""

    def setUp(self):
        self.devices = {}
        self.client = srv.app.test_client()
        patcher = mock.patch.object(srv, "_ws_send_recv_with_fallback",
                                    self._transport)
        patcher.start()
        self.addCleanup(patcher.stop)
        # The source preflight is a second network path: a raw TCP connect, not a
        # WebSocket. run_tests.py blocks it at the socket, so it is fenced, but it
        # would then report every simulated device as unreachable. Answer it from
        # the devices this test registered, which is also how an unreachable
        # source is modelled -- simply by not registering one.
        preflight = mock.patch.object(srv, "_tcp_probe", self._preflight)
        preflight.start()
        self.addCleanup(preflight.stop)
        self.preflights = []
        cache = mock.patch.object(srv, "_load_cache", _devices)
        cache.start()
        self.addCleanup(cache.stop)
        # Capability and metadata are process-wide caches; a test must not
        # inherit another test's answers.
        with srv._multiview_capability_lock:
            srv._MULTIVIEW_CAPABILITY.clear()
        self.addCleanup(srv._MULTIVIEW_CAPABILITY.clear)
        with srv._multiview_meta_lock:
            srv._MULTIVIEW_META.clear()
        self.addCleanup(srv._MULTIVIEW_META.clear)
        saver = mock.patch.object(srv, "_save_multiview_meta", lambda: None)
        saver.start()
        self.addCleanup(saver.stop)

    def _preflight(self, ip, ports, timeout=0.4):
        self.preflights.append(ip)
        device = self.devices.get(ip)
        return device is not None and not device.unreachable

    def _transport(self, ip, payload, timeout, ws_port, ws_path, primary_pwd=None):
        device = self.devices.get(ip)
        if device is None:
            raise OSError("no such device in this test")
        return device.handle(payload)

    def decoder(self, multiviews=None, ip_inputs=None, hdmi=None,
                ip=DECODER_IP):
        device = FakeDevice({
            "multiview": multiviews if multiviews is not None else [],
            "ip_input": ip_inputs if ip_inputs is not None else _ip_inputs(),
            "hdmi_output": hdmi if hdmi is not None else _hdmi_output(),
        })
        self.devices[ip] = device
        return device

    def encoder(self, ip=ENCODER_IP, vc2=None, sessions=None):
        # Each encoder generates its own destinations, so two of them never
        # advertise the same address. A fixture that shares one hides the case
        # where distinct sources need distinct decoder inputs.
        if sessions is None:
            sessions = _sessions()
            if ip != ENCODER_IP:
                suffix = int(str(ip).rsplit(".", 1)[-1])
                for entry in sessions:
                    for kind, base in (("video", 100), ("audio", 150)):
                        stream = (entry.get(kind) or {}).get("stream") or {}
                        if stream.get("destination_address"):
                            stream["destination_address"] = "233.252.0.%d" % (base + suffix)
        device = FakeDevice({
            "vc2": vc2 if vc2 is not None else _vc2(),
            "sessions": sessions,
            # The shape a real encoder returns: resolution under `video`, with
            # `active` saying whether a source is connected at all.
            "hdmi_input": [{"name": "hdmi_input1",
                            "video": {"active": True,
                                      "resolution": {"width": 3840, "height": 2160}}}],
        })
        self.devices[ip] = device
        return device


    # ---- Phase 6: saving stores, recalling configures ---------------------

    def save(self, **overrides):
        """Store a Multiview. Writes the object and nothing else."""
        payload = {"decoder": DECODER_IP, "layout": "2x2",
                   "canvas": mv.ACTIVE_CANVAS, "name": "2x2",
                   "assignments": {"top_left": ENCODER_IP}}
        payload.update(overrides)
        return self.client.post("/api/multiview/apply", json=payload)

    def recall(self, name):
        """Show a saved Multiview, which is what prepares every resource."""
        return self.client.post("/api/multiview/show",
                                json={"decoder": DECODER_IP, "name": name})

    def offer(self, name):
        """Let the decoder offer the object as a video input, as it does."""
        decoder = self.devices[DECODER_IP]
        available = decoder.nodes["hdmi_output"][0]["video"]["available_inputs"]
        if name not in available:
            available.append(name)
        return name

    def commission(self, **overrides):
        """Save then recall: the full path from nothing to on-screen.

        Most behaviour that used to be asserted against Apply now belongs to
        recall, because a saved Multiview deliberately claims no resources.
        """
        body = self.save(**overrides).get_json()
        assert body.get("ok"), body
        name = body["plan"]["object_name"]
        self.offer(name)
        return name, self.recall(name).get_json()


# ==========================================================================
# Layout geometry
# ==========================================================================

class LayoutGeometryTests(unittest.TestCase):

    def test_exactly_the_eleven_layouts_are_exposed(self):
        self.assertEqual(len(mv.LAYOUT_ORDER), 11)
        self.assertEqual(set(mv.LAYOUT_ORDER), set(mv.LAYOUTS))
        self.assertNotIn("custom", mv.LAYOUTS)

    def test_only_1080p_is_offered(self):
        self.assertEqual(mv.EXPOSED_CANVASES, ("1920x1080",))
        self.assertEqual(mv.ACTIVE_CANVAS, "1920x1080")
        # The 4K and 1440 geometry stays in the table so the capability can be
        # brought back as a flag rather than a redesign. Being in the table is
        # not being reachable -- see ActiveCanvasOnlyTests.
        self.assertEqual({p["id"] for p in mv.CANVAS_PRESETS},
                         {"1920x1080", "3840x2160", "2560x1440"})
        for preset in mv.CANVAS_PRESETS:
            if not preset["exposed"]:
                self.assertTrue(preset.get("note"), preset["id"])

    def test_every_window_of_every_layout_fits_encoder_two(self):
        # This is what makes the 1080p-only, Encoder-2-only architecture
        # possible at all: if one window of one layout needed a size Encoder 2
        # cannot produce, that layout would have to be withdrawn.
        for name in mv.LAYOUT_ORDER:
            for window in mv.compute_windows(name, 1920, 1080)["windows"]:
                size = "%dx%d" % (window["width"], window["height"])
                self.assertIn(size, mv.ENCODER2_FORMATS,
                              f"{name}/{window['cell']} needs {size}")

    def test_the_dormant_1440_canvas_is_unexposed_for_a_reason(self):
        missing = []
        preset = mv.canvas_preset("2560x1440")
        for name in mv.LAYOUT_ORDER:
            for window in mv.compute_windows(name, preset["width"],
                                             preset["height"])["windows"]:
                encoder = mv.encoder_for_window_size(window["width"], window["height"])
                if not mv.scaler_format_supported(window["width"], window["height"], encoder):
                    missing.append((name, window["cell"],
                                    f"{window['width']}x{window['height']}"))
        self.assertEqual(len(missing), 6)
        self.assertTrue(all(size == "896x480" for _n, _c, size in missing))

    def test_catalog_covers_every_layout_at_the_active_canvas_only(self):
        catalog = mv.layout_geometry_catalog()
        self.assertEqual(len(catalog), 11)
        for entry in catalog:
            self.assertEqual(list(entry["canvases"]), [mv.ACTIVE_CANVAS])
            for window in entry["canvases"][mv.ACTIVE_CANVAS]["windows"]:
                self.assertEqual(window["encoder"], 2)
                self.assertTrue(window["scaler_supported"])


# ==========================================================================
# Encoder selection
# ==========================================================================

class EncoderSelectionTests(unittest.TestCase):

    def test_every_multiview_window_uses_encoder_two(self):
        # The active rule, and it does not consult the size at all: Encoder 1
        # carries the primary stream and Multiview never retunes it.
        for width, height in [(1920, 1080), (1280, 720), (960, 544), (640, 360),
                              (480, 272), (3840, 2160), (2560, 1440)]:
            self.assertEqual(mv.required_encoder(width, height), 2,
                             f"{width}x{height}")
        self.assertEqual(mv.MULTIVIEW_ENCODER_INDEX, 2)

    def test_the_dormant_size_rule_still_describes_the_4k_case(self):
        # Kept so the 4K path can be revived; nothing in the active workflow
        # calls it -- ActiveCanvasOnlyTests asserts that separately.
        for width, height in [(3840, 2160), (2880, 1584), (2560, 1440), (1920, 1104)]:
            self.assertEqual(mv.encoder_for_window_size(width, height), 1,
                             f"{width}x{height}")
        for width, height in [(1920, 1080), (1280, 720), (480, 272)]:
            self.assertEqual(mv.encoder_for_window_size(width, height), 2,
                             f"{width}x{height}")

    def test_encoder_two_has_no_4k_formats_and_no_passthrough(self):
        for fmt in mv.SCALER_4K_FORMATS:
            self.assertNotIn(fmt, mv.ENCODER2_FORMATS)
        self.assertNotIn("disable", mv.ENCODER2_FORMATS)

    def test_the_dormant_rule_agrees_with_the_scaler_tables(self):
        # If the rule ever picked an encoder whose table lacks the format, the
        # plan would be unbuildable. Assert the two cannot drift.
        for fmt in mv.ENCODER1_FORMATS:
            width, height = (int(v) for v in fmt.split("x"))
            encoder = mv.encoder_for_window_size(width, height)
            self.assertTrue(mv.scaler_format_supported(width, height, encoder), fmt)

    def test_a_1080p_canvas_never_needs_encoder_one(self):
        # The measured advantage of the 1080p canvas, and the reason it is the
        # only one: it disturbs no native feed.
        for name in mv.LAYOUT_ORDER:
            for window in mv.compute_windows(name, 1920, 1080)["windows"]:
                self.assertEqual(
                    mv.encoder_for_window_size(window["width"], window["height"]), 2,
                    f"{name}/{window['cell']}")

    def test_a_4k_canvas_would_need_encoder_one_for_nine_layouts(self):
        needing = {name for name in mv.LAYOUT_ORDER
                   for window in mv.compute_windows(name, 3840, 2160)["windows"]
                   if mv.encoder_for_window_size(window["width"], window["height"]) == 1}
        # Every layout except 2x2 and side-by-side has a main window larger
        # than 1920x1080 at a 4K canvas. That is why 4K is not offered: nine of
        # the eleven layouts would retune the source's primary stream.
        self.assertEqual(needing, set(mv.LAYOUT_ORDER) - {"2x2", "side-by-side"})
        self.assertEqual(len(needing), 9)


class LayoutInferenceTests(unittest.TestCase):

    def _subframes(self, layout, canvas):
        preset = mv.canvas_preset(canvas)
        geometry = mv.compute_windows(layout, preset["width"], preset["height"])
        return geometry, [{"name": mv.subframe_label(w["cell"], w["width"], w["height"]),
                           "x": w["x"], "y": w["y"], "anchor": w["anchor"]}
                          for w in geometry["windows"]]

    def test_every_generated_layout_is_recovered_from_geometry(self):
        for canvas in mv.EXPOSED_CANVASES:
            for layout in mv.LAYOUT_ORDER:
                geometry, subframes = self._subframes(layout, canvas)
                found, found_canvas = mv.infer_layout(
                    geometry["canvas"]["width"], geometry["canvas"]["height"], subframes)
                self.assertEqual(found, layout, f"{layout}/{canvas}")
                self.assertEqual(found_canvas, canvas, f"{layout}/{canvas}")

    def test_unknown_geometry_is_not_given_a_layout_name(self):
        found, _canvas = mv.infer_layout(3840, 2160, [
            {"name": "a", "x": 100, "y": 8, "anchor": "top left"},
            {"name": "b", "x": 2000, "y": 16, "anchor": "top left"}])
        self.assertIsNone(found)

    def test_stored_layout_is_used_only_when_the_hardware_agrees(self):
        geometry, subframes = self._subframes("2x2", "3840x2160")
        result = mv.reconcile_layout({"layout": "2x2"}, 3840, 2160, subframes)
        self.assertEqual(result["layout"], "2x2")
        self.assertEqual(result["source"], "stored")

    def test_a_stale_stored_layout_is_corrected_not_repeated(self):
        # Someone edited the geometry elsewhere. Reporting the stored name would
        # be a lie the operator could act on.
        _geometry, subframes = self._subframes("4-split", "3840x2160")
        result = mv.reconcile_layout({"layout": "2x2"}, 3840, 2112, subframes)
        self.assertEqual(result["layout"], "4-split")
        self.assertEqual(result["source"], "inferred")
        self.assertTrue(result["diverged"])

    def test_unrecognisable_geometry_reports_custom(self):
        result = mv.reconcile_layout({"layout": "2x2"}, 1024, 768, [
            {"name": "x", "x": 0, "y": 0, "anchor": "top left"}])
        self.assertIsNone(result["layout"])
        self.assertEqual(result["source"], "custom")
        self.assertTrue(result["diverged"])

    def test_subframe_labels_round_trip(self):
        label = mv.subframe_label("top_left", 1920, 1080)
        self.assertEqual(label, "top_left (1920x1080)")
        self.assertEqual(mv.parse_subframe_label(label), ("top_left", 1920, 1080))
        self.assertEqual(mv.parse_subframe_label("subframe1"), ("subframe1", None, None))


# ==========================================================================
# Naming
# ==========================================================================

class NamingTests(unittest.TestCase):

    def test_generated_names_satisfy_the_device_rules(self):
        for layout in mv.LAYOUT_ORDER:
            name = mv.device_object_name(mv.LAYOUTS[layout]["label"])
            self.assertTrue(name.startswith("multiview"), name)
            self.assertNotEqual(name, "multiview")
            valid, message = mv.validate_object_name(name)
            self.assertTrue(valid, message)

    def test_the_prompt_style_name_would_have_been_refused(self):
        # The device refuses anything not prefixed "multiview"; this is the
        # check that stops OmniSuite sending one.
        valid, message = mv.validate_object_name("OmniSuite-Test-2x2")
        self.assertFalse(valid)
        self.assertIn("multiview", message)
        self.assertFalse(mv.validate_object_name("multiview")[0])

    def test_names_avoid_colliding_with_what_is_already_there(self):
        existing = {"multiview2x2", "multiview2x22"}
        self.assertEqual(mv.device_object_name("2x2", existing), "multiview2x23")

    def test_punctuation_is_stripped_rather_than_sent(self):
        self.assertEqual(mv.device_object_name("PiP Bottom Right"),
                         "multiviewPiPBottomRight")
        self.assertEqual(mv.device_object_name("1+3 Vertical (left)"),
                         "multiview13Verticalleft")


# ==========================================================================
# A/V Matrix awareness of an active Multiview
# ==========================================================================

class MatrixMultiviewAwarenessTests(MultiviewTestBase):
    """The matrix must not draw a crosspoint that describes nothing.

    A decoder compositing a Multiview is not showing any single encoder, but its
    `ip_input1` still holds whatever it last watched -- so the matrix would
    happily tick a video route for a picture nobody is looking at. Audio is
    different: Multiview audio genuinely does follow one source over its normal
    path, so an audio crosspoint on that row can be perfectly true.
    """

    # ---- the rule itself --------------------------------------------------
    def test_an_ip_input_is_not_a_multiview(self):
        for value in ("ip_input1", "ip_input2", "ip_input8", "ip_input32"):
            self.assertIsNone(mv.active_multiview_name(value), value)

    def test_a_multiview_object_name_is(self):
        self.assertEqual(mv.active_multiview_name("multiviewSidebySide"),
                         "multiviewSidebySide")

    def test_nothing_selected_is_not_a_multiview(self):
        for value in ("", "   ", None):
            self.assertIsNone(mv.active_multiview_name(value), repr(value))

    # ---- what the matrix is told ------------------------------------------
    def _matrix_decoder(self, devices):
        with mock.patch.object(srv, "_load_cache", lambda: devices):
            body = self.client.get("/api/state").get_json()
        return next(d for d in body["decoders"] if d["ip"] == DECODER_IP)

    def _devices_with(self, video_input):
        devices = [dict(d) for d in _devices()]
        for device in devices:
            if device.get("ip") == DECODER_IP:
                device["video_input"] = video_input
                device["ip1_addr"] = S2_VIDEO      # a stale but plausible route
                device["ip1_port"] = 1000
        return devices

    def test_a_decoder_showing_a_multiview_is_reported_as_such(self):
        entry = self._matrix_decoder(self._devices_with("multiviewLive"))
        self.assertTrue(entry["multiview_active"])
        self.assertEqual(entry["multiview_name"], "multiviewLive")

    def test_a_decoder_on_an_ordinary_input_is_not(self):
        entry = self._matrix_decoder(self._devices_with("ip_input1"))
        self.assertFalse(entry["multiview_active"])
        self.assertIsNone(entry["multiview_name"])

    def test_saved_but_inactive_multiviews_do_not_make_a_decoder_multiview(self):
        """Existence proves nothing; only the current output selection does."""
        self.decoder(multiviews=[
            _multiview_object("multiviewSaved", 1920, 1088),
            _multiview_object("multiviewAlsoSaved", 1920, 1088)])
        entry = self._matrix_decoder(self._devices_with("ip_input1"))
        self.assertFalse(entry["multiview_active"],
                         "saved objects were treated as an active Multiview")

    def test_learning_this_costs_the_scan_nothing(self):
        """It is derived from a field the scan already collects."""
        devices = self._devices_with("multiviewLive")
        encoder = self.encoder(ENCODER_IP)
        self.decoder()
        with mock.patch.object(srv, "_load_cache", lambda: devices):
            self.client.get("/api/state")
        self.assertEqual(encoder.reads, [], "the matrix state read an encoder")
        self.assertEqual(self.devices[DECODER_IP].reads, [],
                         "the matrix state read the decoder")

    # ---- routing to it -----------------------------------------------------
    def _route(self, **extra):
        payload = {"decoder": DECODER_IP, "encoder": ENCODER_IP, "mode": "av"}
        payload.update(extra)
        return self.client.post("/api/route", json=payload)

    def test_routing_to_a_multiview_decoder_is_refused_without_consent(self):
        """The warning happens in the page; this is the server's half of it."""
        decoder = self.decoder(
            multiviews=[_multiview_object("multiviewLive", 1920, 1088)])
        decoder.nodes["hdmi_output"][0]["video"]["input"] = "multiviewLive"
        self.encoder(ENCODER_IP)
        before = copy.deepcopy(decoder.nodes)
        response = self._route()
        self.assertEqual(response.status_code, 409)
        body = response.get_json()
        self.assertEqual(body["status"], "MULTIVIEW ACTIVE")
        self.assertEqual(body["multiview"], "multiviewLive")
        self.assertEqual(decoder.nodes, before,
                         "a refused route still changed the decoder")

    def test_cancelling_writes_nothing_at_all(self):
        """Cancel never reaches the server, so nothing is written anywhere."""
        decoder = self.decoder(
            multiviews=[_multiview_object("multiviewLive", 1920, 1088)])
        decoder.nodes["hdmi_output"][0]["video"]["input"] = "multiviewLive"
        encoder = self.encoder(ENCODER_IP)
        decoder.writes, encoder.writes = [], []
        # The page returns before issuing the request; assert the shape that
        # makes that possible -- the request is what carries the consent.
        self.assertEqual(decoder.writes, [])
        self.assertEqual(encoder.writes, [])

    def test_consenting_leaves_multiview_and_keeps_the_saved_object(self):
        decoder = self.decoder(
            multiviews=[_multiview_object("multiviewLive", 1920, 1088)])
        decoder.nodes["hdmi_output"][0]["video"]["input"] = "multiviewLive"
        decoder.nodes["hdmi_output"][0]["video"]["available_inputs"] = [
            "ip_input1", "multiviewLive"]
        self.encoder(ENCODER_IP)
        response = self._route(exit_multiview=True)
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertIn("multiview_exit", body, body)
        self.assertTrue(body["multiview_exit"]["ok"], body["multiview_exit"])
        # Whether the route itself then succeeded or not, the one thing that
        # must never happen is the saved Multiview being removed.
        self.assertIn("multiviewLive",
                      [str(o.get("name") or "") for o in decoder.nodes["multiview"]],
                      "exiting Multiview deleted the saved object")

    def _decoder_composing(self, windows=("ip_input2", "ip_input4")):
        """A decoder actually showing a Multiview that OmniSuite configured.

        The pool inputs have to be genuinely owned and genuinely carrying
        something, or "release what it was using" has nothing to release and a
        test of it cannot fail. That is precisely how the release test below
        used to pass with the release loop removed.
        """
        subframes = [{"name": "subframe%d" % (index + 1), "input": name}
                     for index, name in enumerate(windows)]
        inputs = _ip_inputs()
        for name in windows:
            entry = next(i for i in inputs if i["name"] == name)
            entry.update({"enabled": True,
                          "multicast": {"address": "233.252.0.%d"
                                        % (40 + int(name[len("ip_input"):]))}})
        decoder = self.decoder(
            multiviews=[_multiview_object("multiviewLive", 1920, 1088, subframes)],
            ip_inputs=inputs)
        decoder.nodes["hdmi_output"][0]["video"]["input"] = "multiviewLive"
        decoder.nodes["hdmi_output"][0]["video"]["available_inputs"] = [
            "ip_input1", "multiviewLive"]
        # OmniSuite only reconfigures pool inputs it can prove it configured.
        srv._record_multiview_meta(
            {"ip": DECODER_IP, "mac": "00:00:5E:00:53:10",
             "hostname": "dec-test-01"},
            "multiviewLive",
            {"layout": "side_by_side",
             "windows": [{"source_ip": ENCODER_IP, "encoder_index": 2,
                          "input": name} for name in windows]})
        return decoder

    def test_leaving_multiview_releases_the_windows_it_was_using(self):
        decoder = self._decoder_composing()
        self.encoder(ENCODER_IP)
        carrying = [i["name"] for i in decoder.nodes["ip_input"]
                    if i["name"] in mv.WINDOW_IP_INPUTS and i.get("enabled")]
        self.assertTrue(carrying, "the fixture does not model a running Multiview")
        body = self._route(exit_multiview=True).get_json()
        self.assertEqual(sorted(body["multiview_exit"]["released"]),
                         sorted(carrying), body["multiview_exit"])
        # Nothing in the reserved pool should still be carrying a stream for a
        # Multiview that is no longer on the display.
        for entry in decoder.nodes["ip_input"]:
            if entry["name"] in mv.WINDOW_IP_INPUTS:
                self.assertFalse(
                    entry.get("enabled"),
                    "%s was left carrying a Multiview stream" % entry["name"])

    def test_an_input_outside_the_pool_is_never_released(self):
        """ip_input1 and ip_input3 are the ordinary subscriptions, not ours."""
        decoder = self._decoder_composing()
        self.encoder(ENCODER_IP)
        self._route(exit_multiview=True)
        for name in ("ip_input1", "ip_input3"):
            entry = next(i for i in decoder.nodes["ip_input"]
                         if i["name"] == name)
            self.assertTrue(entry.get("enabled"),
                            "%s was released, and Multiview did not own it" % name)

    @contextlib.contextmanager
    def _route_transport_that_works(self, decoder):
        """Stand in for the two things the simulator does not model.

        The simulator speaks `config_get`, `config_set` and `method`; it has no
        route transport and no decoder-input summary. Without these the route
        always fails, so every assertion about a SUCCESSFUL route sat behind a
        branch that never ran -- which is how the payload assertions below came
        to be unfalsifiable.
        """
        def inputs(ip, *args, **kwargs):
            hdmi = decoder.nodes["hdmi_output"][0]
            return {"video_input": (hdmi.get("video") or {}).get("input"),
                    "audio_input": (hdmi.get("audio") or {}).get("input"),
                    "ip1_addr": S1_VIDEO, "ip1_port": 1000}

        with mock.patch.object(srv.omni_matrix_logic, "set_route",
                               lambda *a, **k: True), \
                mock.patch.object(srv, "_ws_get_decoder_inputs", inputs):
            yield

    def test_the_response_carries_the_state_the_matrix_should_redraw(self):
        """§11: no refresh, no rescan, no navigation to become truthful."""
        decoder = self._decoder_composing()
        self.encoder(ENCODER_IP)
        with self._route_transport_that_works(decoder):
            body = self._route(exit_multiview=True).get_json()
        self.assertTrue(body["ok"], body)
        payload = body["decoder"]
        self.assertIn("multiview_active", payload,
                      "the route response cannot update the MV indicator")
        self.assertFalse(payload["multiview_active"],
                         "the matrix would still show MULTIVIEW after leaving it")
        self.assertIsNone(payload["multiview_name"], payload)
        self.assertFalse(str(payload["video_input"]).startswith("multiview"),
                         payload)

    def test_a_route_that_fails_puts_the_multiview_back(self):
        """The simulator has no route transport, so the route genuinely fails.

        That makes this the real rollback case rather than a simulated one.
        """
        self._decoder_composing()
        self.encoder(ENCODER_IP)
        body = self._route(exit_multiview=True).get_json()
        self.assertFalse(body["ok"], body)
        self.assertIn("multiview_exit", body, body)
        self.assertIn("ROLLED BACK", body["status"], body["status"])
        self.assertTrue(body["multiview_exit"]["rollback"]["restored"],
                        body["multiview_exit"]["rollback"])

    def test_no_credential_is_returned_by_a_route(self):
        decoder = self.decoder(
            multiviews=[_multiview_object("multiviewLive", 1920, 1088)])
        decoder.nodes["hdmi_output"][0]["video"]["input"] = "multiviewLive"
        decoder.nodes["hdmi_output"][0]["video"]["available_inputs"] = [
            "ip_input1", "multiviewLive"]
        self.encoder(ENCODER_IP)
        raw = self._route(exit_multiview=True).get_data(as_text=True).lower()
        # The generic route error legitimately says "password mismatch" as
        # prose. What must never appear is a credential VALUE being handed
        # back, so the patterns are the ones that carry one.
        for pattern in ('"password"', '"passwd"', 'used_password', '"secret"',
                        '"token"', 'password=', 'password:'):
            self.assertNotIn(pattern, raw, raw[:300])


# ==========================================================================
# No Multiview-facing response may carry a credential
# ==========================================================================

class MultiviewCredentialSweepTests(MultiviewTestBase):
    """Sweep every Multiview-facing endpoint, looking for the actual secret.

    The scattered per-endpoint checks all searched for the WORD "password",
    which a fixture carrying no credential can never produce -- the test would
    pass whether or not the code leaked. So the devices here carry a distinctive
    passphrase and the configured fallback is another, and the assertion is that
    neither VALUE appears in any response. Searching for the value rather than
    the key also survives someone renaming the field.

    The list of endpoints is taken from the application's own URL map, so an
    endpoint added later is swept without anyone remembering to add it here.
    """

    SENTINEL = "Sentinel-Device-Passphrase-9471"
    FALLBACK = "Sentinel-Fallback-Passphrase-3382"

    def _endpoints(self):
        paths = set()
        for rule in srv.app.url_map.iter_rules():
            path = str(rule)
            if "<" in path:                     # nothing Multiview-facing is
                continue                        # parameterised
            if path.startswith("/api/multiview") or path in ("/api/route",
                                                             "/api/state"):
                methods = rule.methods & {"GET", "POST"}
                if methods:
                    paths.add((path, "GET" if "GET" in methods else "POST"))
        return sorted(paths)

    def _sweep(self):
        self.decoder(multiviews=[_multiview_object("multiviewLive", 1920, 1088)])
        self.encoder(ENCODER_IP)
        devices = [dict(d) for d in _devices()]
        for device in devices:
            device["username"] = "sentinel-user"
            device["password"] = self.SENTINEL
        responses = {}
        payload = {"decoder": DECODER_IP, "encoder": ENCODER_IP, "mode": "av",
                   "name": "multiviewLive", "ip": DECODER_IP}
        with mock.patch.object(srv, "_load_cache", lambda: devices),                 mock.patch.dict(srv.app.config, {"PASSWORD": self.FALLBACK}):
            for path, method in self._endpoints():
                if method == "GET":
                    response = self.client.get(path + "?ip=%s" % DECODER_IP)
                else:
                    response = self.client.post(path, json=payload)
                responses[path] = response.get_data(as_text=True)
        return responses

    def test_every_multiview_endpoint_is_swept(self):
        """A new endpoint must not be able to slip past this file unnoticed."""
        swept = {path for path, _method in self._endpoints()}
        self.assertIn("/api/multiview/state", swept)
        self.assertIn("/api/multiview/show", swept)
        self.assertIn("/api/route", swept)
        multiview = [str(r) for r in srv.app.url_map.iter_rules()
                     if str(r).startswith("/api/multiview") and "<" not in str(r)]
        self.assertEqual(sorted(swept & set(multiview)), sorted(multiview),
                         "a Multiview endpoint is not covered by the sweep")

    def test_no_response_carries_the_device_passphrase(self):
        for path, body in self._sweep().items():
            self.assertNotIn(self.SENTINEL, body, path)

    def test_no_response_carries_the_configured_fallback(self):
        for path, body in self._sweep().items():
            self.assertNotIn(self.FALLBACK, body, path)

    def test_no_response_carries_the_username_either(self):
        for path, body in self._sweep().items():
            self.assertNotIn("sentinel-user", body, path)

    def test_the_sweep_would_notice_a_leak(self):
        """The sweep is only worth having if a leak actually reaches a response.

        Without this, a change that stopped the endpoints from being reached at
        all -- or a fixture that never carried the passphrase -- would leave
        every assertion above passing on empty responses.
        """
        leaky = dict(self._sweep())
        leaky["/api/multiview/state"] += self.SENTINEL
        with self.assertRaises(AssertionError):
            for path, body in leaky.items():
                self.assertNotIn(self.SENTINEL, body, path)
        # ...and the responses really were substantive, not empty.
        self.assertTrue(all(body.strip() for body in self._sweep().values()),
                        "an endpoint returned nothing, so it proved nothing")


# ==========================================================================
# Copying a Multiview to another decoder
# ==========================================================================

class CopyMultiviewTests(MultiviewTestBase):
    """A venue with six identical screens should not be six manual rebuilds.

    Copying moves the DEFINITION -- layout, and which source is in which window.
    It deliberately carries no resources: ip_input numbers, scaler sizes and
    bitrates belong to one decoder at one moment, and the planner works them out
    again at Show. That is also why a copy does not need anything to be free
    right now, and why it changes no display.
    """

    def setUp(self):
        super().setUp()
        self.source = self.decoder()
        self.target = self.decoder(ip=DECODER2_IP)
        for ip in (ENCODER_IP, ENCODER2_IP):
            self.encoder(ip)

    def _saved(self, **overrides):
        body = self.save(**overrides).get_json()
        self.assertTrue(body.get("ok"), body)
        return body["plan"]["object_name"]

    def _copy(self, name, **extra):
        payload = {"source_decoder": DECODER_IP, "target_decoder": DECODER2_IP,
                   "name": name}
        payload.update(extra)
        return self.client.post("/api/multiview/copy", json=payload)

    def _names(self, device):
        return [str(o.get("name") or "") for o in device.nodes["multiview"]]

    def test_the_definition_arrives_on_the_target(self):
        name = self._saved(layout="side-by-side",
                           assignments={"left": ENCODER_IP, "right": ENCODER2_IP})
        body = self._copy(name).get_json()
        self.assertTrue(body.get("ok"), body)
        self.assertIn(name, self._names(self.target))

    def test_the_source_decoder_is_not_touched(self):
        name = self._saved()
        before = copy.deepcopy(self.source.nodes)
        self.source.writes = []
        self._copy(name)
        self.assertEqual(self.source.nodes, before,
                         "copying changed the decoder it copied FROM")
        self.assertEqual([w for w in self.source.writes if w[0] != "config_get"],
                         [], self.source.writes)

    def test_neither_display_changes(self):
        name = self._saved()
        before_source = self.source.nodes["hdmi_output"][0]["video"].get("input")
        before_target = self.target.nodes["hdmi_output"][0]["video"].get("input")
        body = self._copy(name).get_json()
        self.assertTrue(body.get("ok"), body)
        self.assertFalse(body["shown"], "a copy reported itself as shown")
        self.assertEqual(self.source.nodes["hdmi_output"][0]["video"].get("input"),
                         before_source)
        self.assertEqual(self.target.nodes["hdmi_output"][0]["video"].get("input"),
                         before_target)

    def test_the_copy_reserves_nothing_on_the_target(self):
        """Saving claims no resources, which is what makes copying safe."""
        name = self._saved()
        self._copy(name)
        for entry in self.target.nodes["ip_input"]:
            if entry["name"] in mv.WINDOW_IP_INPUTS:
                self.assertFalse(entry.get("enabled"),
                                 "%s was configured by a copy" % entry["name"])

    def test_both_presets_remain_independently_manageable(self):
        name = self._saved()
        self._copy(name)
        # Deleting it from the target leaves the original alone.
        self.client.post("/api/multiview/delete",
                         json={"decoder": DECODER2_IP, "name": name})
        self.assertIn(name, self._names(self.source))

    def test_an_existing_name_is_never_silently_overwritten(self):
        name = self._saved()
        self._copy(name)
        before = copy.deepcopy(self.target.nodes["multiview"])
        response = self._copy(name)
        self.assertEqual(response.status_code, 409)
        body = response.get_json()
        self.assertEqual(body["status"], "NAME IN USE")
        self.assertTrue(body["suggested_name"])
        self.assertEqual(self.target.nodes["multiview"], before,
                         "a refused copy still changed the target")

    def test_the_operator_can_ask_for_a_replacement(self):
        name = self._saved()
        self._copy(name)
        body = self._copy(name, on_conflict="replace").get_json()
        self.assertTrue(body.get("ok"), body)
        self.assertEqual(self._names(self.target).count(name), 1)

    def test_the_operator_can_ask_for_a_new_name(self):
        name = self._saved()
        self._copy(name)
        body = self._copy(name, on_conflict="rename").get_json()
        self.assertTrue(body.get("ok"), body)
        names = self._names(self.target)
        self.assertIn(name, names)
        self.assertEqual(len(names), 2, names)

    def test_copying_onto_itself_is_refused(self):
        name = self._saved()
        response = self._copy(name, target_decoder=DECODER_IP)
        self.assertEqual(response.status_code, 400)

    def test_an_interlocked_target_is_refused_with_the_reason(self):
        """§7: the preflight speaks about the target, not about the planner."""
        name = self._saved()
        self.decoder(ip=DECODER2_IP, hdmi=_hdmi_output(wall=True))
        response = self._copy(name)
        self.assertEqual(response.status_code, 409)
        body = response.get_json()
        self.assertEqual(body["status"], "INCOMPATIBLE")
        self.assertTrue(any("Video Wall" in p for p in body["problems"]),
                        body["problems"])

    def test_an_offline_source_is_a_warning_and_the_window_is_kept(self):
        """§7: a source that is offline today is still the right source."""
        name = self._saved(layout="side-by-side",
                           assignments={"left": ENCODER_IP, "right": ENCODER2_IP})
        del self.devices[ENCODER2_IP]              # stops answering
        body = self._copy(name).get_json()
        self.assertTrue(body.get("ok"), body)
        self.assertTrue(body["warnings"], "the offline source was not mentioned")
        target = next(o for o in self.target.nodes["multiview"]
                      if o.get("name") == name)
        self.assertEqual(len(target.get("subframes") or []), 2,
                         "the offline source's window was dropped")

    def test_no_credential_is_returned(self):
        name = self._saved()
        raw = self._copy(name).get_data(as_text=True).lower()
        for pattern in ('"password"', 'used_password', '"secret"', '"token"',
                        'password=', 'password:'):
            self.assertNotIn(pattern, raw)


# ==========================================================================
# Synchronized decoder groups
# ==========================================================================

class DecoderGroupTests(MultiviewTestBase):
    """Several decoders that are meant to show the same thing.

    The reason this is a first-class idea rather than a convenience: the
    encoders are shared. Window geometry decides what a source's Encoder 2 must
    scale to, and one Encoder 2 produces one size -- so a group has to be judged
    as a whole before anything is written, not configured decoder by decoder
    until the conflict turns up with half the room already changed.
    """

    def setUp(self):
        super().setUp()
        self.first = self.decoder()
        self.second = self.decoder(ip=DECODER2_IP)
        for ip in (ENCODER_IP, ENCODER2_IP):
            self.encoder(ip)
        srv._MULTIVIEW_GROUPS.clear()
        self.addCleanup(srv._MULTIVIEW_GROUPS.clear)
        saver = mock.patch.object(srv, "_save_multiview_groups", lambda: None)
        saver.start()
        self.addCleanup(saver.stop)

    # ---- membership --------------------------------------------------------
    def _group(self, name="Sports Bar", members=None):
        response = self.client.post("/api/multiview/groups/save", json={
            "name": name,
            "members": members if members is not None else [DECODER_IP, DECODER2_IP]})
        body = response.get_json()
        self.assertTrue(body.get("ok"), body)
        return body["group"]["id"]

    def test_a_group_is_created_with_its_members(self):
        group_id = self._group()
        body = self.client.get("/api/multiview/groups").get_json()
        group = next(g for g in body["groups"] if g["id"] == group_id)
        self.assertEqual(group["name"], "Sports Bar")
        self.assertEqual({m["ip"] for m in group["members"]},
                         {DECODER_IP, DECODER2_IP})

    def test_creating_a_group_writes_to_no_device(self):
        self.first.writes, self.second.writes = [], []
        self._group()
        self.assertEqual([w for w in self.first.writes if w[0] != "config_get"], [])
        self.assertEqual([w for w in self.second.writes if w[0] != "config_get"], [])

    def test_a_group_survives_a_restart(self):
        """§8: membership is not a browser selection."""
        group_id = self._group()
        saved = json.loads(json.dumps(
            {"groups": {k: v for k, v in srv._MULTIVIEW_GROUPS.items()}}))
        srv._MULTIVIEW_GROUPS.clear()
        srv._MULTIVIEW_GROUPS.update(saved["groups"])
        body = self.client.get("/api/multiview/groups").get_json()
        self.assertIn(group_id, [g["id"] for g in body["groups"]])

    def test_a_group_stores_no_credential(self):
        self._group()
        raw = json.dumps(srv._MULTIVIEW_GROUPS).lower()
        for word in ("password", "passwd", "secret", "token", "credential"):
            self.assertNotIn(word, raw)

    def test_members_are_remembered_by_mac_not_only_address(self):
        """An address can be reassigned; a group must not follow it blindly."""
        self._group()
        stored = next(iter(srv._MULTIVIEW_GROUPS.values()))
        self.assertTrue(all(m.get("mac") for m in stored["members"]),
                        stored["members"])

    def test_two_groups_cannot_share_a_name(self):
        self._group()
        response = self.client.post("/api/multiview/groups/save",
                                    json={"name": "Sports Bar",
                                          "members": [DECODER_IP]})
        self.assertEqual(response.status_code, 409)

    def test_renaming_and_removing_members(self):
        group_id = self._group()
        body = self.client.post("/api/multiview/groups/save", json={
            "id": group_id, "name": "Main Bar", "members": [DECODER_IP],
        }).get_json()
        self.assertTrue(body["ok"], body)
        self.assertEqual(body["group"]["name"], "Main Bar")
        self.assertEqual([m["ip"] for m in body["group"]["members"]], [DECODER_IP])

    def test_deleting_a_group_leaves_every_decoder_alone(self):
        group_id = self._group()
        before = copy.deepcopy(self.first.nodes)
        body = self.client.post("/api/multiview/groups/delete",
                                json={"id": group_id}).get_json()
        self.assertTrue(body["ok"], body)
        self.assertEqual(self.first.nodes, before)
        self.assertEqual(self.client.get("/api/multiview/groups")
                         .get_json()["groups"], [])

    # ---- copy to the group -------------------------------------------------
    def _saved_on_first(self, **overrides):
        body = self.save(**overrides).get_json()
        self.assertTrue(body.get("ok"), body)
        return body["plan"]["object_name"]

    def test_copying_to_a_group_saves_on_every_member_and_shows_nothing(self):
        """§10: COPY/SAVE TO GROUP is not SHOW ON GROUP."""
        group_id = self._group()
        name = self._saved_on_first()
        before = self.second.nodes["hdmi_output"][0]["video"].get("input")
        body = self.client.post("/api/multiview/groups/copy", json={
            "group": group_id, "source_decoder": DECODER_IP, "name": name,
        }).get_json()
        self.assertTrue(body.get("ok"), body)
        self.assertFalse(body["shown"])
        self.assertIn(name, [str(o.get("name") or "")
                             for o in self.second.nodes["multiview"]])
        self.assertEqual(self.second.nodes["hdmi_output"][0]["video"].get("input"),
                         before, "a copy to the group changed a display")

    # ---- the conflict this whole idea exists for ---------------------------
    def test_one_encoder_cannot_be_asked_for_two_window_sizes(self):
        """§12/§23: refuse the group before anything is written.

        The same source in a big window on one decoder and a small window on
        another is not a layout that can exist: there is one Encoder 2 behind
        that source and it produces one picture size.
        """
        group_id = self._group()
        # A 2x2 on the first decoder: every window 960x544.
        equal = self._saved_on_first(layout="2x2", name="Equal",
                                     assignments={"top_left": ENCODER_IP,
                                                  "top_right": ENCODER2_IP})
        # The second decoder already holds a layout where ENCODER_IP is the big
        # window, so the group is being asked for two sizes of the same source.
        srv._MULTIVIEW_GROUPS[group_id]["multiview"] = None
        plans = self._group_plan(group_id, equal)
        self.assertTrue(plans["ok"], plans)     # identical layouts agree

        conflicting = self._conflicting_plans()
        self.assertTrue(conflicting, "no conflict was produced to test")
        self.assertIn("one picture size", conflicting[0]["detail"])

    def _group_plan(self, group_id, name):
        return self.client.post("/api/multiview/groups/plan", json={
            "group": group_id, "source_decoder": DECODER_IP, "name": name,
        }).get_json()

    def _conflicting_plans(self):
        """Two plans that want one source at two sizes, judged by the server."""
        member_a = {"ip": DECODER_IP, "hostname": "dec-test-01"}
        member_b = {"ip": DECODER2_IP, "hostname": "dec-test-02"}
        plan_a = {"windows": [{"cell": "main", "label": "main (1280x720)",
                               "scaler_format": "1280x720",
                               "source": {"ip": ENCODER_IP, "hostname": "enc-1"}}]}
        plan_b = {"windows": [{"cell": "top_left", "label": "top_left (640x360)",
                               "scaler_format": "640x360",
                               "source": {"ip": ENCODER_IP, "hostname": "enc-1"}}]}
        return srv._scaler_conflicts([(member_a, plan_a), (member_b, plan_b)])

    def test_the_conflict_names_the_source_and_both_decoders(self):
        conflicts = self._conflicting_plans()
        self.assertEqual(len(conflicts), 1, conflicts)
        detail = conflicts[0]["detail"]
        self.assertIn("enc-1", detail)
        self.assertIn("dec-test-01", detail)
        self.assertIn("dec-test-02", detail)
        self.assertIn("1280x720", detail)
        self.assertIn("640x360", detail)

    def test_the_same_size_everywhere_is_not_a_conflict(self):
        member_a = {"ip": DECODER_IP, "hostname": "dec-test-01"}
        member_b = {"ip": DECODER2_IP, "hostname": "dec-test-02"}
        window = {"cell": "top_left", "label": "top_left (960x544)",
                  "scaler_format": "960x544",
                  "source": {"ip": ENCODER_IP, "hostname": "enc-1"}}
        self.assertEqual(
            srv._scaler_conflicts([(member_a, {"windows": [window]}),
                                   (member_b, {"windows": [copy.deepcopy(window)]})]),
            [])

    def test_a_refused_group_writes_nothing(self):
        """§12: refuse BEFORE the first mutation."""
        group_id = self._group()
        name = self._saved_on_first()
        self.first.writes, self.second.writes = [], []
        with mock.patch.object(srv, "_scaler_conflicts",
                               lambda plans: [{"source": "enc-1",
                                               "detail": "conflict"}]):
            response = self.client.post("/api/multiview/groups/show", json={
                "group": group_id, "source_decoder": DECODER_IP, "name": name})
        self.assertEqual(response.status_code, 409)
        body = response.get_json()
        self.assertEqual(body["status"], "REFUSED")
        self.assertEqual(body["writes"], 0)
        for device in (self.first, self.second):
            self.assertEqual([w for w in device.writes if w[0] != "config_get"],
                             [], "a refused group operation wrote to a device")

    def test_an_offline_member_refuses_the_group_before_any_write(self):
        group_id = self._group()
        name = self._saved_on_first()
        del self.devices[DECODER2_IP]
        self.first.writes = []
        response = self.client.post("/api/multiview/groups/show", json={
            "group": group_id, "source_decoder": DECODER_IP, "name": name})
        self.assertEqual(response.status_code, 409)
        self.assertEqual([w for w in self.first.writes if w[0] != "config_get"], [])

    # ---- drift -------------------------------------------------------------
    def test_a_group_reports_itself_synchronized_or_drifted(self):
        """§17: read on demand, never on a timer."""
        group_id = self._group()
        srv._MULTIVIEW_GROUPS[group_id]["multiview"] = {"name": "multiviewLive"}
        for device in (self.first, self.second):
            device.nodes["hdmi_output"][0]["video"]["input"] = "multiviewLive"
        body = self.client.get("/api/multiview/groups/state?group=%s"
                               % group_id).get_json()
        self.assertEqual(body["state"], "SYNCHRONIZED")

        # One member routed away from the A/V Matrix (§18).
        self.second.nodes["hdmi_output"][0]["video"]["input"] = "ip_input1"
        body = self.client.get("/api/multiview/groups/state?group=%s"
                               % group_id).get_json()
        self.assertEqual(body["state"], "DRIFTED")
        drifted = [m for m in body["members"] if m["state"] == "DRIFTED"]
        self.assertEqual([m["ip"] for m in drifted], [DECODER2_IP])

    def test_an_offline_member_is_offline_not_drifted(self):
        group_id = self._group()
        srv._MULTIVIEW_GROUPS[group_id]["multiview"] = {"name": "multiviewLive"}
        self.first.nodes["hdmi_output"][0]["video"]["input"] = "multiviewLive"
        del self.devices[DECODER2_IP]
        body = self.client.get("/api/multiview/groups/state?group=%s"
                               % group_id).get_json()
        states = {m["ip"]: m["state"] for m in body["members"]}
        self.assertEqual(states[DECODER2_IP], "OFFLINE")

    def test_drift_is_reported_not_corrected(self):
        """§17: OmniSuite does not know the operator did not mean it.

        The drifted decoder really does hold the Multiview and really could be
        put back -- otherwise an attempt to correct it would fail for its own
        reasons and this would pass without proving anything.
        """
        group_id = self._group()
        srv._MULTIVIEW_GROUPS[group_id]["multiview"] = {"name": "multiviewLive"}
        self.second.nodes["multiview"] = [
            _multiview_object("multiviewLive", 1920, 1088,
                              [{"name": "left (960x544)", "input": "ip_input2"}])]
        self.second.nodes["hdmi_output"][0]["video"]["available_inputs"] = [
            "ip_input1", "multiviewLive"]
        self.second.nodes["hdmi_output"][0]["video"]["input"] = "ip_input1"
        srv._record_multiview_meta(
            {"ip": DECODER2_IP, "mac": "00:00:5E:00:53:11",
             "hostname": "dec-test-02"},
            "multiviewLive",
            {"layout": "side-by-side",
             "windows": [{"source_ip": ENCODER_IP, "encoder_index": 2,
                          "cell": "left", "input": "ip_input2"}]})
        self.second.writes = []
        self.client.get("/api/multiview/groups/state?group=%s" % group_id)
        self.assertEqual([w for w in self.second.writes if w[0] != "config_get"],
                         [], "reading group state changed a decoder")
        self.assertEqual(
            self.second.nodes["hdmi_output"][0]["video"]["input"], "ip_input1")


# ==========================================================================
# Show on Group
# ==========================================================================

class ShowOnGroupTests(MultiviewTestBase):
    """Six screens are six devices on a network, not one atomic thing.

    So the group is planned in full and refused in full, applied member by
    member with each one verified, and on a failure part way through every
    member already changed is put back. That is best effort and is reported as
    what it is -- never as success with half the room on the old layout.
    """

    def setUp(self):
        super().setUp()
        self.first = self.decoder()
        self.second = self.decoder(ip=DECODER2_IP)
        for ip in (ENCODER_IP, ENCODER2_IP):
            self.encoder(ip)
        srv._MULTIVIEW_GROUPS.clear()
        self.addCleanup(srv._MULTIVIEW_GROUPS.clear)
        saver = mock.patch.object(srv, "_save_multiview_groups", lambda: None)
        saver.start()
        self.addCleanup(saver.stop)
        body = self.client.post("/api/multiview/groups/save", json={
            "name": "Sports Bar", "members": [DECODER_IP, DECODER2_IP]}).get_json()
        self.group = body["group"]["id"]
        saved = self.save().get_json()
        self.name = saved["plan"]["object_name"]
        # Both decoders must offer the object as a video input, as a real one
        # does once it exists.
        for ip in (DECODER_IP, DECODER2_IP):
            self.client.post("/api/multiview/groups/copy", json={
                "group": self.group, "source_decoder": DECODER_IP,
                "name": self.name})
            device = self.devices[ip]
            available = device.nodes["hdmi_output"][0]["video"]["available_inputs"]
            if self.name not in available:
                available.append(self.name)

    def _show(self):
        return self.client.post("/api/multiview/groups/show", json={
            "group": self.group, "source_decoder": DECODER_IP, "name": self.name})

    def _display(self, ip):
        return self.devices[ip].nodes["hdmi_output"][0]["video"].get("input")

    def test_every_member_ends_up_showing_it(self):
        body = self._show().get_json()
        self.assertTrue(body.get("ok"), body)
        self.assertEqual(body["status"], "VERIFIED")
        for ip in (DECODER_IP, DECODER2_IP):
            self.assertEqual(self._display(ip), self.name,
                             "%s is not showing the group Multiview" % ip)

    def test_the_group_remembers_what_it_is_showing(self):
        self._show()
        record = srv._MULTIVIEW_GROUPS[self.group]
        self.assertEqual((record.get("multiview") or {}).get("name"), self.name)

    def test_a_shared_source_is_charged_once(self):
        """§13: the decoders subscribe to one Encoder 2 stream, not one each."""
        body = self._show().get_json()
        shared = {entry["source_ip"]: entry for entry in body["shared_sources"]}
        self.assertIn(ENCODER_IP, shared)
        # One stream, one charge -- not doubled because two decoders watch it.
        self.assertLessEqual(shared[ENCODER_IP]["bitrate"], 900)

    def test_each_decoder_maps_the_stream_into_its_own_pool(self):
        """§13: the same multicast, each decoder's own ip_input."""
        self._show()
        addresses = []
        for ip in (DECODER_IP, DECODER2_IP):
            entries = [e for e in self.devices[ip].nodes["ip_input"]
                       if e["name"] in mv.WINDOW_IP_INPUTS and e.get("enabled")]
            self.assertTrue(entries, "%s mapped no stream" % ip)
            addresses.append({(e.get("multicast") or {}).get("address")
                              for e in entries})
        self.assertEqual(addresses[0], addresses[1],
                         "the group members subscribed to different streams")

    def test_each_decoder_sets_its_own_audio_input(self):
        """§14: audio is per decoder, and not assumed to be the same number."""
        self._show()
        for ip in (DECODER_IP, DECODER2_IP):
            audio = self.devices[ip].nodes["hdmi_output"][0].get("audio") or {}
            self.assertTrue(audio.get("input"),
                            "%s was left with no audio input" % ip)

    # ---- the failure path --------------------------------------------------
    def _break_second_decoder_show(self):
        """Let the first member succeed and the second fail at Show."""
        real = srv._show_multiview_on
        calls = {"n": 0}

        def flaky(ip, name):
            calls["n"] += 1
            if ip == DECODER2_IP:
                return {"ok": False, "status": "FAILED",
                        "error": "the decoder stopped answering"}, 200
            return real(ip, name)
        return mock.patch.object(srv, "_show_multiview_on", flaky), calls

    def test_a_failure_part_way_through_puts_the_group_back(self):
        before = {ip: self._display(ip) for ip in (DECODER_IP, DECODER2_IP)}
        patcher, _calls = self._break_second_decoder_show()
        with patcher:
            body = self._show().get_json()
        self.assertFalse(body["ok"], body)
        self.assertIn("GROUP", body["status"])
        self.assertEqual(self._display(DECODER_IP), before[DECODER_IP],
                         "the first decoder was left on the new layout")

    def test_the_failure_says_which_decoder_and_which_stage(self):
        patcher, _calls = self._break_second_decoder_show()
        with patcher:
            body = self._show().get_json()
        failure = body["failures"][-1]
        self.assertEqual(failure["decoder"], DECODER2_IP)
        self.assertEqual(failure["stage"], "show")
        self.assertIn("stopped answering", failure["error"])

    def test_an_incomplete_rollback_is_never_reported_as_rolled_back(self):
        patcher, _calls = self._break_second_decoder_show()
        with patcher, mock.patch.object(srv, "_restore_member_display",
                                        lambda snapshot: (False, "no answer")):
            body = self._show().get_json()
        self.assertEqual(body["status"], "FAILED — GROUP ROLLBACK INCOMPLETE")
        self.assertTrue(any(not entry["restored"] for entry in body["rollback"]))

    def test_a_failed_group_show_does_not_record_itself_as_the_group_view(self):
        patcher, _calls = self._break_second_decoder_show()
        with patcher:
            self._show()
        record = srv._MULTIVIEW_GROUPS[self.group]
        self.assertIsNone((record.get("multiview") or {}).get("name"),
                          "a failed group show was remembered as the group state")

    def test_no_credential_is_returned_by_a_group_show(self):
        raw = self._show().get_data(as_text=True).lower()
        for pattern in ('"password"', 'used_password', '"secret"', '"token"',
                        'password=', 'password:'):
            self.assertNotIn(pattern, raw)


# ==========================================================================
# The User Guide is a release artefact
# ==========================================================================

class UserGuideTests(unittest.TestCase):
    """The Settings-page User Guide ships with the build and matches it.

    Structural only. Headings, resources and navigation are asserted; prose is
    not, so the guide can be edited freely without these failing for no reason.
    """

    ROOT = pathlib.Path(__file__).resolve().parents[1]
    GUIDE = ROOT / "ui" / "user-guide.html"

    def setUp(self):
        self.client = srv.app.test_client()
        self.body = self.GUIDE.read_text(encoding="utf-8")

    # ---- it exists and is reachable --------------------------------------
    def test_the_guide_file_ships_with_the_application(self):
        self.assertTrue(self.GUIDE.exists(), "ui/user-guide.html is missing")
        self.assertGreater(len(self.body), 20000, "the guide looks truncated")

    def test_settings_offers_the_guide_on_every_page(self):
        """Settings is the only way most operators will find it."""
        settings = (self.ROOT / "ui" / "settings.js").read_text(encoding="utf-8")
        self.assertIn("cfg_user_guide", settings,
                      "Settings no longer has a User Guide control")
        self.assertIn("'/help'", settings,
                      "the Settings control no longer points at the guide")

    def test_the_server_serves_it(self):
        response = self.client.get("/help")
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/html", response.headers["Content-Type"])
        self.assertIn("OmniSuite User Guide", response.get_data(as_text=True))

    def test_the_served_guide_states_the_running_version(self):
        """A release artefact must not claim a version it did not ship with."""
        served = self.client.get("/help").get_data(as_text=True)
        self.assertNotIn("{{OMNI_VERSION}}", served,
                         "the version placeholder was served unsubstituted")
        self.assertIn(srv._app_version(), served,
                      "the guide does not say which build it describes")

    def test_the_version_is_not_hand_typed_into_the_document(self):
        """The file itself carries a token, so a version bump cannot orphan it."""
        self.assertIn("{{OMNI_VERSION}}", self.body,
                      "the guide hard-codes a version again")

    # ---- packaging --------------------------------------------------------
    def test_packaging_carries_the_guide(self):
        """`ui/` is bundled wholesale, so the guide has to be inside it."""
        import tools.build_release as build
        self.assertIn("ui", build.ALLOWED_DATA,
                      "ui/ is no longer bundled, so the guide would not ship")
        self.assertTrue(
            self.GUIDE.is_relative_to(self.ROOT / "ui"),
            "the guide is outside ui/ and would not be packaged")
        # And nothing forbids it by name or by the directory it sits in.
        self.assertNotIn("user-guide.html", build.FORBIDDEN_FILES)
        self.assertNotIn("ui", build.FORBIDDEN_IN_PACKAGE)

    # ---- it represents the application that exists ------------------------
    def test_every_page_of_the_application_has_a_section(self):
        for feature in ("Device Info", "Configure", "A/V Matrix", "USB Matrix",
                        "Multiview", "Settings", "Appearance"):
            self.assertIn(feature, self.body, "the guide never mentions " + feature)

    def test_multiview_has_its_own_section_and_is_navigable(self):
        self.assertIn('<section id="multiview">', self.body,
                      "Multiview has no section of its own")
        self.assertIn('href="#multiview"', self.body,
                      "Multiview is not in the table of contents")
        self.assertIn('href="/matrix/multiview"', self.body,
                      "the guide cannot navigate to the Multiview page")

    def test_the_multiview_section_covers_what_an_operator_has_to_know(self):
        """Headings, not sentences: the subjects that must be addressed."""
        section = self.body[self.body.index('<section id="multiview">'):
                            self.body.index('<section id="usb">')]
        for subject in ("layout", "Save", "Show on Display", "LIVE",
                        "Audio", "Bitrate", "preview", "Preview disabled",
                        "Preview unavailable", "Configuration required",
                        "Video Wall", "Fast Switching", "1920x1080",
                        "Delete"):
            self.assertIn(subject, section,
                          "the Multiview section does not cover %r" % subject)

    def test_the_multiview_section_states_the_bitrate_policy_in_force(self):
        """If the policy changes, this is what notices the guide did not."""
        section = self.body[self.body.index('<section id="multiview">'):
                            self.body.index('<section id="usb">')]
        self.assertIn("%d Mb/s" % mv.EQUAL_WINDOW_TARGET, section)
        self.assertIn("%d Mb/s" % mv.MAIN_WINDOW_TARGET, section)
        self.assertIn("%d Mb/s" % mv.SMALL_WINDOW_TARGET, section)
        self.assertIn("%d Mb/s" % mv.SOURCE_VIDEO_BUDGET, section)

    def test_every_layout_the_application_offers_is_described(self):
        section = self.body[self.body.index('<section id="multiview">'):
                            self.body.index('<section id="usb">')]
        for family in ("Side-by-Side", "2x2", "Picture-in-Picture", "1+3",
                       "4-Split"):
            self.assertIn(family, section,
                          "the guide does not describe the %s layouts" % family)

    def test_the_sections_are_numbered_without_gaps_or_repeats(self):
        numbers = [int(m) for m in re.findall(r"<h2>(\d+)\. ", self.body)]
        self.assertEqual(numbers, list(range(1, len(numbers) + 1)),
                         "section numbering is wrong: %s" % numbers)
        # Each section must appear as a heading and in the contents. Counting
        # occurrences was too strict: a cross-reference from one section to
        # another is a third mention and a perfectly good thing to have.
        contents = self.body[self.body.index('<div class="toc">'):
                             self.body.index("</div>",
                                             self.body.index('<div class="toc">'))]
        for number in numbers:
            self.assertIn(">%d. " % number, contents,
                          "section %d is missing from the contents" % number)
            self.assertIn("<h2>%d. " % number, self.body,
                          "section %d has no heading" % number)

    # ---- and it is safe to publish ----------------------------------------
    def test_the_guide_contains_no_credential(self):
        lowered = self.body.lower()
        for word in ("password:", "passwd", "secret", "api key", "apikey",
                     "token=", "bearer "):
            self.assertNotIn(word, lowered,
                             "the guide may contain a credential: %r" % word)

    def test_the_guide_exposes_no_implementation_detail(self):
        """It explains how to use the application, not how it is built."""
        section = self.body[self.body.index('<section id="multiview">'):
                            self.body.index('<section id="usb">')]
        for leak in ("/api/", "config_set", "config_get", "wsapp",
                     "vc2_encoder", "def ", "Phase 7", "Phase 8",
                     "pytest", "localStorage"):
            self.assertNotIn(leak, section,
                             "implementation detail in the guide: %r" % leak)


# ==========================================================================
# Write-minimal reconciliation
# ==========================================================================

class WriteMinimalTests(MultiviewTestBase):
    """A field the device already holds must not be written again.

    Encoder configuration is shared state. Encoder 1 may be feeding decoders
    that have nothing to do with Multiview, and a `config_set` is an instruction
    the device acts on whether or not the value changed -- measured on the
    bench, writing an encoder's Session 1 video stream blacks every decoder
    watching it for about half a second, even when the value written is the one
    it already had.

    So the transaction reads, plans, diffs, and writes the difference.
    """

    def setUp(self):
        super().setUp()
        self.dec = self.decoder()
        for ip in (ENCODER_IP, ENCODER2_IP, NEW_WALLPLATE_IP, PLAIN_111_IP):
            self.encoder(ip)

    def _commissioned(self, **overrides):
        name, body = self.commission(**overrides)
        self.assertTrue(body["ok"], body)
        return name

    def _clear_writes(self):
        for device in self.devices.values():
            device.writes = []

    def _writes(self, ip):
        return self.devices[ip].writes

    # ---- the whole transaction -------------------------------------------
    def test_showing_an_already_prepared_multiview_writes_nothing(self):
        name = self._commissioned()
        self._clear_writes()
        body = self.recall(name).get_json()
        self.assertTrue(body["ok"], body)
        for ip, device in self.devices.items():
            self.assertEqual(device.writes, [], "%s was written to: %s"
                             % (ip, device.writes))
        self.assertEqual(body["writes"]["writes_performed"], 0)

    @contextlib.contextmanager
    def _settles_before_the_write(self, settle):
        """Let a device reach the wanted state after planning, before writing.

        `_read_nodes` is the read the diff makes; running `settle` immediately
        before it delegates is exactly the case of a device that has come good
        on its own since the plan was computed.
        """
        original = srv._read_nodes
        state = {"settled": False}

        def read(mutations):
            if not state["settled"]:
                state["settled"] = True
                settle()
            return original(mutations)

        with mock.patch.object(srv, "_read_nodes", read):
            yield state

    def test_a_field_that_comes_good_after_planning_is_not_written(self):
        """The window the diff exists for.

        The planner sees a wrong bitrate and plans to correct it. By the time
        the transaction is applied the encoder already holds the wanted value,
        so writing it would disturb every decoder watching that encoder for
        nothing.
        """
        name = self._commissioned()
        encoder = self._encoder(ENCODER_IP, "vc2_encoder2")
        wanted = encoder["bitrate"]
        encoder["bitrate"] = 42                     # drift, so a write is planned
        self._clear_writes()

        def settle():
            self._encoder(ENCODER_IP, "vc2_encoder2")["bitrate"] = wanted

        with self._settles_before_the_write(settle) as state:
            body = self.recall(name).get_json()
        self.assertTrue(state["settled"], "the diff never read the devices")
        self.assertTrue(body["ok"], body)
        self.assertEqual(
            [w for w in self._writes(ENCODER_IP) if w[0] == "config_set"], [],
            "a field the encoder already held was written anyway")
        self.assertTrue(body["writes"]["skipped"],
                        "the transaction reported nothing as skipped")
        self.assertEqual(self._encoder(ENCODER_IP, "vc2_encoder2")["bitrate"],
                         wanted)

    def test_the_same_window_is_closed_on_a_live_switch(self):
        """The switch path has its own copy of this, and its own call site."""
        name, _body = self.commission(layout="side-by-side", assignments={
            "left": ENCODER_IP, "right": ENCODER2_IP})
        target = self.devices[DECODER_IP]
        entry = next(i for i in target.nodes["ip_input"]
                     if i["name"] == "ip_input4")
        self._clear_writes()

        def settle():
            # Whatever the switch is about to point ip_input4 at, it is already
            # there: the wall plate stream it plans to write.
            entry.update({"enabled": True, "port": 1000,
                          "multicast": {"address": "233.252.0.124"}})

        with self._settles_before_the_write(settle) as state:
            response = self.client.post("/api/multiview/switch", json={
                "decoder": DECODER_IP, "name": name, "cell": "right",
                "source": NEW_WALLPLATE_IP})
        body = response.get_json()
        self.assertTrue(state["settled"], "the diff never read the devices")
        self.assertTrue(body["ok"], body)
        written = [w for w in self._writes(DECODER_IP)
                   if w[0] == "config_set" and w[1] == "ip_input"
                   and any((e or {}).get("name") == "ip_input4"
                           for e in (w[2] if isinstance(w[2], list) else [w[2]]))]
        self.assertEqual(written, [],
                         "ip_input4 already carried the wanted stream and was "
                         "written anyway")

    def test_the_transaction_reports_what_it_found_and_what_it_wrote(self):
        """§16: a diagnostic has to be able to answer this for any transaction."""
        name = self._commissioned()
        body = self.recall(name).get_json()
        writes = body["writes"]
        for key in ("planned_fields", "already_correct", "writes_required",
                    "writes_performed"):
            self.assertIn(key, writes)
        self.assertLessEqual(writes["already_correct"], writes["planned_fields"])

    # ---- one field at a time ---------------------------------------------
    def _encoder(self, ip, name):
        return next(e for e in self.devices[ip].nodes["vc2"] if e["name"] == name)

    def _session(self, ip, name):
        return next(s for s in self.devices[ip].nodes["sessions"]
                    if s["name"] == name)

    def _recall_after(self, name, change):
        """Apply `change` to the bench, recall, and return what was written."""
        self._clear_writes()
        change()
        self._clear_writes()
        body = self.recall(name).get_json()
        self.assertTrue(body["ok"], body)
        return body

    def test_an_already_correct_encoder_2_bitrate_is_not_rewritten(self):
        name = self._commissioned()
        encoder = self._encoder(ENCODER_IP, "vc2_encoder2")
        settled = encoder["bitrate"]
        self._clear_writes()
        self.recall(name)
        self.assertEqual(self._writes(ENCODER_IP), [])
        self.assertEqual(encoder["bitrate"], settled)

    def test_a_wrong_encoder_2_bitrate_is_written_and_nothing_else_is(self):
        name = self._commissioned()
        encoder = self._encoder(ENCODER_IP, "vc2_encoder2")
        wanted = encoder["bitrate"]
        body = self._recall_after(name, lambda: encoder.update({"bitrate": 42}))
        writes = self._writes(ENCODER_IP)
        self.assertEqual(len(writes), 1, "more than the bitrate was written: %s"
                         % writes)
        self.assertEqual(encoder["bitrate"], wanted)
        # And no other device was touched at all.
        self.assertEqual(self._writes(ENCODER2_IP), [])
        self.assertEqual(self.devices[DECODER_IP].writes, [])

    def test_an_already_correct_encoder_2_input_is_not_rewritten(self):
        name = self._commissioned()
        self._clear_writes()
        self.recall(name)
        self.assertEqual(self._writes(ENCODER_IP), [])

    def test_a_cleared_encoder_2_input_is_written_back(self):
        name = self._commissioned()
        encoder = self._encoder(ENCODER_IP, "vc2_encoder2")
        self._recall_after(name, lambda: encoder.update({"input": ""}))
        self.assertEqual(encoder["input"], "hdmi_input1")
        self.assertTrue(self._writes(ENCODER_IP))

    def test_an_already_correct_scaler_is_not_rewritten(self):
        name = self._commissioned()
        self._clear_writes()
        self.recall(name)
        self.assertEqual(self._writes(ENCODER_IP), [])

    def test_a_wrong_scaler_is_written(self):
        name = self._commissioned()
        encoder = self._encoder(ENCODER_IP, "vc2_encoder2")
        self._recall_after(
            name, lambda: encoder["scaler"].update({"width": 111, "height": 222}))
        self.assertNotEqual(encoder["scaler"]["width"], 111)
        self.assertTrue(self._writes(ENCODER_IP))

    def test_an_already_enabled_session_2_is_not_rewritten(self):
        name = self._commissioned()
        self._clear_writes()
        self.recall(name)
        writes = [w for w in self._writes(ENCODER_IP) if w[1] == "sessions"]
        self.assertEqual(writes, [])

    def test_an_already_disabled_sap_is_not_rewritten(self):
        name = self._commissioned()
        session = self._session(ENCODER_IP, "session2")
        self.assertFalse(session["sap"]["enabled"], "the fixture is not settled")
        self._clear_writes()
        self.recall(name)
        self.assertEqual([w for w in self._writes(ENCODER_IP)
                          if w[1] == "sessions"], [])

    def test_an_already_correct_decoder_input_is_not_disabled_and_re_enabled(self):
        """The pair that makes naive diffing dangerous.

        The planner emits "disable ip_inputN while its source changes" followed
        by "point ip_inputN at X and enable it". Judged separately, the disable
        looks necessary (the input IS enabled) and the enable looks redundant --
        which would leave the input switched off with nothing to switch it back
        on. They are judged together, as the state they jointly ask for.
        """
        name = self._commissioned()
        inputs = {i["name"]: copy.deepcopy(i)
                  for i in self.dec.nodes["ip_input"]}
        self._clear_writes()
        body = self.recall(name).get_json()
        self.assertTrue(body["ok"], body)
        self.assertEqual(self.dec.writes, [], "the decoder was rewritten")
        for name_, before in inputs.items():
            now = next(i for i in self.dec.nodes["ip_input"] if i["name"] == name_)
            self.assertEqual(now, before, "%s changed" % name_)

    def test_an_audio_input_already_on_the_right_source_is_not_rewritten(self):
        name = self._commissioned()
        hdmi = self.dec.nodes["hdmi_output"][0]
        audio_input = hdmi["audio"]["input"]
        before = copy.deepcopy(next(i for i in self.dec.nodes["ip_input"]
                                    if i["name"] == audio_input))
        self._clear_writes()
        self.recall(name)
        now = next(i for i in self.dec.nodes["ip_input"] if i["name"] == audio_input)
        self.assertEqual(now, before, "the audio subscription was rewritten")

    # ---- bandwidth is still calculated, just not always written ----------
    def test_the_planner_still_computes_bandwidth_when_nothing_is_written(self):
        """Calculating is not writing, and skipping writes must not skip safety."""
        name = self._commissioned(layout="2x2", assignments={
            "top_left": ENCODER_IP, "top_right": ENCODER2_IP,
            "bottom_left": NEW_WALLPLATE_IP, "bottom_right": PLAIN_111_IP})
        self._clear_writes()
        body = self.recall(name).get_json()
        self.assertTrue(body["ok"], body)
        bandwidth = body["plan"]["bandwidth"]
        self.assertGreater(bandwidth["decoder_aggregate"], 0,
                           "bandwidth was not recalculated")
        self.assertLessEqual(bandwidth["decoder_aggregate"], 900)
        self.assertEqual(bandwidth["budget_source"], 900)
        self.assertTrue(bandwidth["allocations"], "no per-source allocation")
        self.assertEqual(body["writes"]["writes_performed"], 0,
                         "it recalculated AND rewrote")

    def test_a_budget_violation_is_still_refused_when_writes_would_be_zero(self):
        """Write-minimal must not become a way round the bandwidth rules."""
        for ip in (ENCODER_IP, ENCODER2_IP):
            encoder = self._encoder(ip, "vc2_encoder1")
            encoder["bitrate"] = 900          # leaves nothing for Encoder 2
        body = self.save(layout="side-by-side", name="Tight", assignments={
            "left": ENCODER_IP, "right": ENCODER2_IP}).get_json()
        plan = body.get("plan") or {}
        allocations = (plan.get("bandwidth") or {}).get("allocations") or []
        self.assertTrue(allocations)
        for entry in allocations:
            # Encoder 1 at 900 leaves nothing, so the source is refused rather
            # than being quietly trimmed to make room.
            self.assertIsNone(entry["bitrate"], entry)
            self.assertIn("less than the 20 Mb/s", entry["error"])
            self.assertIsNone(entry["encoder1_target"])
        self.assertFalse(plan["ok"], "a source with no headroom was accepted")

    # ---- and a live switch obeys the same rule ---------------------------
    def test_a_switch_to_the_source_already_there_writes_nothing(self):
        name = self._commissioned()
        self.assertTrue(self.recall(name).get_json()["ok"])
        self._clear_writes()
        body = self.client.post("/api/multiview/switch", json={
            "decoder": DECODER_IP, "name": name,
            "cell": "top_left", "source": ENCODER_IP}).get_json()
        self.assertTrue(body["ok"], body)
        self.assertEqual(body["writes"]["writes_performed"], 0,
                         "switching to the source already there wrote: %s"
                         % body["steps"])
        for ip, device in self.devices.items():
            self.assertEqual(device.writes, [], "%s was written to" % ip)


# ==========================================================================
# Showable verdict
# ==========================================================================

def _multiview_object(name, width, height, subframes=None):
    """A Multiview object as a decoder reports it."""
    return {"name": name, "width": width, "height": height,
            "subframes": subframes if subframes is not None else []}


class ShowableTests(MultiviewTestBase):
    """The 409 the operator actually hit, and why the page should pre-empt it.

    `multiviewOmniSuiteTest` on the bench is a 3840x2160 object left by an
    earlier release. It is listed, Show was offered for it, and the only
    feedback was a console 409. The refusal is correct; offering the action was
    not.
    """

    def test_a_1080p_multiview_is_showable(self):
        self.decoder(multiviews=[_multiview_object("multiview2x2", 1920, 1088)])
        body = self.client.get(
            f"/api/multiview/state?ip={DECODER_IP}").get_json()
        view = body["multiviews"][0]
        self.assertTrue(view["showable"])
        self.assertEqual(view["not_showable_reason"], "")

    def test_a_4k_multiview_is_reported_as_not_showable_with_the_reason(self):
        self.decoder(multiviews=[_multiview_object("multiviewOld", 3840, 2160)])
        body = self.client.get(
            f"/api/multiview/state?ip={DECODER_IP}").get_json()
        view = body["multiviews"][0]
        self.assertFalse(view["showable"])
        self.assertIn("3840x2160", view["not_showable_reason"])
        self.assertIn("1920x1080", view["not_showable_reason"])

    def test_the_verdict_matches_what_show_actually_does(self):
        """One rule, reported in two places; they must not drift apart."""
        decoder = self.decoder(
            multiviews=[_multiview_object("multiviewOld", 3840, 2160)])
        available = decoder.nodes["hdmi_output"][0]["video"]["available_inputs"]
        available.append("multiviewOld")
        state = self.client.get(
            f"/api/multiview/state?ip={DECODER_IP}").get_json()
        self.assertFalse(state["multiviews"][0]["showable"])
        response = self.recall("multiviewOld")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json()["status"], "REFUSED")

    def test_showing_an_already_active_multiview_is_not_a_conflict(self):
        """Idempotency: it was already true, and it stays true."""
        for ip in (ENCODER_IP, ENCODER2_IP):
            self.encoder(ip)
        self.decoder()
        name, body = self.commission()
        self.assertTrue(body["ok"], body)
        for _ in range(3):
            again = self.recall(name)
            self.assertEqual(again.status_code, 200)
            self.assertTrue(again.get_json()["ok"])
            self.assertEqual(again.get_json()["status"], "VERIFIED")


# ==========================================================================
# Source preview
# ==========================================================================

class SourcePreviewTests(MultiviewTestBase):
    """Hovering a source shows what is on it. That is the whole feature.

    It exists between "which of these four encoders is the podium camera" and
    having to route one into a window to find out. Everything here is about it
    staying an aid: it reads, it never writes, and it never decides whether a
    source can be used.
    """

    def _preview(self, ip):
        return self.client.get("/api/multiview/preview?ip=%s" % ip)

    def test_an_encoder_generating_a_thumbnail_can_be_previewed(self):
        self.encoder(ENCODER_IP)
        body = self._preview(ENCODER_IP).get_json()
        self.assertTrue(body["ok"], body)
        self.assertEqual(body["status"], "available")
        self.assertEqual(body["url"],
                         "http://%s/thumbnail/thumbnail1.jpg" % ENCODER_IP)
        self.assertEqual((body["width"], body["height"]), (320, 180))

    def test_a_disabled_thumbnail_is_reported_as_disabled_not_missing(self):
        """The distinction the browser cannot make for itself.

        Measured: with `thumbnail.enable` false the encoder still answers
        `GET /thumbnail/thumbnail1.jpg` with HTTP 200 and a placeholder JPEG, so
        an <img> load succeeds either way. Only the configuration says which it
        is, which is why this endpoint exists at all.
        """
        self.encoder(ENCODER_IP, vc2=_vc2(thumbnail={"enable": False,
                                                     "width": 320, "height": 180}))
        body = self._preview(ENCODER_IP).get_json()
        self.assertEqual(body["status"], "disabled")
        self.assertNotIn("url", body, "a disabled preview offered an image URL")
        self.assertIn("turned off", body["reason"])

    def test_an_encoder_with_no_thumbnail_support_is_unavailable(self):
        self.encoder(ENCODER_IP, vc2=_vc2(thumbnail={}))
        body = self._preview(ENCODER_IP).get_json()
        self.assertEqual(body["status"], "unavailable")
        self.assertNotIn("url", body)

    def test_an_encoder_that_does_not_answer_is_unavailable(self):
        # Registered nowhere, so the transport has nothing to talk to.
        body = self._preview(ENCODER_IP).get_json()
        self.assertEqual(body["status"], "unavailable")
        self.assertIn("did not answer", body["reason"])

    def test_the_excluded_model_is_never_previewed(self):
        self.encoder(OLD_WALLPLATE_IP)
        body = self._preview(OLD_WALLPLATE_IP).get_json()
        self.assertEqual(body["status"], "unavailable")
        self.assertNotIn("url", body)

    def test_a_decoder_has_no_preview(self):
        self.decoder()
        response = self._preview(DECODER_IP)
        self.assertEqual(response.status_code, 400)
        self.assertIn("encoder", response.get_json()["reason"])

    def test_an_undiscovered_device_is_refused(self):
        self.assertEqual(self._preview("192.0.2.99").status_code, 404)

    def test_asking_about_a_preview_writes_nothing(self):
        """The one rule the feature must never break."""
        encoder = self.encoder(ENCODER_IP)
        self.decoder()
        before = copy.deepcopy(encoder.nodes)
        for _ in range(5):
            self._preview(ENCODER_IP)
        self.assertEqual(encoder.writes, [], "a preview wrote to the encoder")
        self.assertEqual(encoder.nodes, before, "a preview changed the encoder")

    def test_a_preview_never_returns_a_credential(self):
        """Anything in this response can end up in a log or a capture."""
        self.encoder(ENCODER_IP)
        raw = self._preview(ENCODER_IP).get_data(as_text=True).lower()
        for forbidden in ("password", "passwd", "credential", "secret", "token"):
            self.assertNotIn(forbidden, raw, raw)

    def test_the_preview_url_carries_no_credential_either(self):
        self.encoder(ENCODER_IP)
        url = self._preview(ENCODER_IP).get_json()["url"]
        self.assertNotIn("@", url, "credentials were put in the URL")
        self.assertNotIn("?", url, "something was put in the query string")

    def test_a_source_needing_configuration_can_still_be_previewed(self):
        """Preview availability and routing eligibility are separate questions.

        This is the case where it helps most: seeing what is on an encoder is
        how an operator decides whether it is the one worth configuring.
        """
        devices = _devices()
        for device in devices:
            if device["ip"] == ENCODER_IP:
                device["session2_video_mcast"] = ""
        with mock.patch.object(srv, "_load_cache", lambda: devices):
            self.encoder(ENCODER_IP)
            preview = self._preview(ENCODER_IP).get_json()
            sources = self.client.get("/api/multiview/sources").get_json()
        self.assertEqual(preview["status"], "available")
        entry = next(s for s in sources["sources"] if s["ip"] == ENCODER_IP)
        self.assertEqual(entry["status"], mv.SOURCE_CONFIGURATION_REQUIRED)

    def test_a_failed_preview_does_not_change_eligibility(self):
        """The other direction, and the one that would actually hurt."""
        self.encoder(ENCODER_IP, vc2=_vc2(thumbnail={"enable": False}))
        self.assertEqual(self._preview(ENCODER_IP).get_json()["status"],
                         "disabled")
        sources = self.client.get("/api/multiview/sources").get_json()
        entry = next(s for s in sources["sources"] if s["ip"] == ENCODER_IP)
        self.assertEqual(entry["status"], mv.SOURCE_READY,
                         "a source became unusable because its preview is off")

    def test_listing_sources_still_asks_no_encoder_anything(self):
        """The preview must not have quietly made the source list expensive."""
        encoder = self.encoder(ENCODER_IP)
        self.client.get("/api/multiview/sources")
        self.assertEqual(encoder.reads, [],
                         "the source list read a device: %s" % encoder.reads)

    def test_a_preview_reads_one_node_from_one_device(self):
        encoder = self.encoder(ENCODER_IP)
        other = self.encoder(ENCODER2_IP)
        self._preview(ENCODER_IP)
        self.assertEqual(encoder.reads, ["vc2"], encoder.reads)
        self.assertEqual(other.reads, [], "it read an encoder nobody asked about")


# ==========================================================================
# Source eligibility
# ==========================================================================

class SourceEligibilityTests(unittest.TestCase):
    """Exactly one model is excluded, matched as a whole identity.

    This is not a wall-plate rule. An earlier `endswith("-WP")` version excluded
    HW-OMNI-E4111-WP, which is a 4xxx wall plate and a perfectly good Multiview
    source, so the three cases below are asserted explicitly and by name.
    """

    def test_the_old_wall_plate_is_the_one_excluded_model(self):
        for model in ["AT-OMNI-111-WP", "at-omni-111-wp", "AT-Omni-111-WP",
                      "AT OMNI 111 WP", "at_omni_111_wp"]:
            eligible, reason = mv.is_eligible_source(
                {"role": "encoder", "model": model})
            self.assertFalse(eligible, model)
            self.assertIn("AT-OMNI-111-WP", reason)

    def test_the_4xxx_wall_plate_is_an_eligible_source(self):
        # The correction this phase exists for.
        for model in ["HW-OMNI-E4111-WP", "hw-omni-e4111-wp", "HW-OMNI-E4521-WP"]:
            eligible, reason = mv.is_eligible_source(
                {"role": "encoder", "model": model})
            self.assertTrue(eligible, f"{model} was excluded: {reason}")

    def test_the_plain_at_omni_111_stays_eligible(self):
        for model in ["AT-OMNI-111", "at-omni-111"]:
            self.assertTrue(mv.is_eligible_source(
                {"role": "encoder", "model": model})[0], model)

    def test_no_generic_suffix_or_substring_rule_is_used(self):
        # A suffix rule would take these; a "111" substring rule would take the
        # plain model above.
        for model in ["HW-OMNI-E4111-WP", "AT-OMNI-121-WP", "AT-OMNI-111-WP-X",
                      "at-omni-1111-wp", "AT-OMNI-112-WP"]:
            self.assertTrue(mv.is_eligible_source(
                {"role": "encoder", "model": model})[0],
                f"{model} was excluded by an over-broad rule")

    def test_the_exclusion_list_names_exactly_one_model(self):
        self.assertEqual(mv.EXCLUDED_SOURCE_MODELS, frozenset({"at-omni-111-wp"}))

    def test_model_identity_is_normalised_not_compared_raw(self):
        self.assertEqual(mv.normalize_model("  HW__OMNI--E4111_WP "),
                         "hw-omni-e4111-wp")
        self.assertEqual(mv.normalize_model(None), "")

    def test_eligibility_still_requires_an_encoder(self):
        self.assertFalse(mv.is_eligible_source(
            {"role": "decoder", "model": "hw-omni-d4511"})[0])
        self.assertFalse(mv.is_eligible_source({})[0])

    def test_capability_is_read_from_the_device_answer(self):
        self.assertEqual(mv.multiview_capable({"error": False, "config": []}),
                         (True, ""))
        supported, _message = mv.multiview_capable(
            {"error": True, "error_message": "Config node not found"})
        self.assertIs(supported, False)
        # An unreachable device is unknown, never "unsupported".
        supported, _message = mv.multiview_capable({"__unreachable__": True})
        self.assertIsNone(supported)


# ==========================================================================
# ip_input allocation
# ==========================================================================

class IpInputPoolTests(unittest.TestCase):
    """Four Multiview video slots, handed to distinct streams in first-use order."""

    def _requirements(self, *streams):
        return [{"key": "w%d" % i, "index": i, "address": address, "port": port}
                for i, (address, port) in enumerate(streams)]

    def _allocate(self, *streams, **kwargs):
        inputs = kwargs.pop("ip_inputs", None) or _ip_inputs()
        return mv.allocate_window_inputs(
            self._requirements(*streams), inputs, _hdmi_output()[0], **kwargs)

    A = ("233.252.0.41", 1000)
    B = ("233.252.0.42", 1000)
    C = ("233.252.0.43", 1000)
    D = ("233.252.0.44", 1000)

    def _chosen(self, assignments, count):
        return [assignments["w%d" % i]["ip_input"] for i in range(count)]

    def test_roles_are_discovered_not_assumed(self):
        classified = {item["name"]: item for item in mv.classify_ip_inputs(
            _ip_inputs(), _hdmi_output()[0])}
        self.assertIn("HDMI video", classified["ip_input1"]["roles"])
        self.assertIn("HDMI audio", classified["ip_input3"]["roles"])
        self.assertIn("Aux", classified["ip_input5"]["roles"])
        self.assertEqual(classified["ip_input2"]["roles"], [])

    def test_the_pool_is_the_four_even_inputs_in_order(self):
        self.assertEqual(mv.WINDOW_IP_INPUTS,
                         ("ip_input2", "ip_input4", "ip_input6", "ip_input8"))
        for index, name in enumerate(mv.WINDOW_IP_INPUTS):
            self.assertEqual(mv.window_ip_input(index), name)
        self.assertIsNone(mv.window_ip_input(4))

    # ---- allocation is by stream, not by window ---------------------------

    def test_four_distinct_streams_take_the_four_slots_in_order(self):
        assignments, errors, collisions = self._allocate(self.A, self.B, self.C, self.D)
        self.assertEqual((errors, collisions), ([], []))
        self.assertEqual(self._chosen(assignments, 4),
                         ["ip_input2", "ip_input4", "ip_input6", "ip_input8"])

    def test_a_repeated_stream_consumes_no_second_slot(self):
        # The worked example: W1=A W2=A W3=B W4=C -> A:2 B:4 C:6, and ip_input8
        # is never touched.
        assignments, errors, _c = self._allocate(self.A, self.A, self.B, self.C)
        self.assertEqual(errors, [])
        self.assertEqual(self._chosen(assignments, 4),
                         ["ip_input2", "ip_input2", "ip_input4", "ip_input6"])
        self.assertNotIn("ip_input8", self._chosen(assignments, 4))

    def test_four_identical_windows_are_one_stream_on_one_input(self):
        assignments, errors, _c = self._allocate(self.A, self.A, self.A, self.A)
        self.assertEqual(errors, [])
        self.assertEqual(set(self._chosen(assignments, 4)), {"ip_input2"})
        for key in ("w1", "w2", "w3"):
            self.assertEqual(assignments[key]["shared_with"], "ip_input2")

    def test_two_duplicate_pairs_are_two_streams_on_two_inputs(self):
        assignments, errors, _c = self._allocate(self.A, self.A, self.B, self.B)
        self.assertEqual(errors, [])
        self.assertEqual(self._chosen(assignments, 4),
                         ["ip_input2", "ip_input2", "ip_input4", "ip_input4"])

    def test_an_interleaved_repeat_still_resolves_to_two_inputs(self):
        assignments, errors, _c = self._allocate(self.A, self.B, self.A, self.B)
        self.assertEqual(errors, [])
        self.assertEqual(self._chosen(assignments, 4),
                         ["ip_input2", "ip_input4", "ip_input2", "ip_input4"])

    def test_allocation_is_deterministic(self):
        first = self._allocate(self.A, self.A, self.B, self.C)[0]
        second = self._allocate(self.A, self.A, self.B, self.C)[0]
        self.assertEqual(first, second)

    # ---- stream identity ---------------------------------------------------

    def test_identity_is_the_address_and_port_not_the_hostname(self):
        self.assertEqual(mv.stream_identity("233.252.0.41", 1000),
                         mv.stream_identity("233.252.0.41", "1000"))
        self.assertNotEqual(mv.stream_identity("233.252.0.41", 1000),
                            mv.stream_identity("233.252.0.41", 1004))
        self.assertNotEqual(mv.stream_identity("233.252.0.41", 1000),
                            mv.stream_identity("233.252.0.42", 1000))
        self.assertIsNone(mv.stream_identity("", 1000))

    def test_one_source_on_two_ports_is_two_streams(self):
        # Same encoder, different destinations: the decoder opens two things, so
        # they need two inputs however alike the sources look.
        assignments, errors, _c = self._allocate(("233.252.0.41", 1000),
                                                 ("233.252.0.41", 1004))
        self.assertEqual(errors, [])
        self.assertEqual(self._chosen(assignments, 2), ["ip_input2", "ip_input4"])

    # ---- what the decoder already has -------------------------------------

    def test_the_odd_inputs_are_never_used_for_window_video(self):
        assignments, _errors, _c = self._allocate(self.A, self.B, self.C, self.D)
        for name in ("ip_input1", "ip_input3", "ip_input5", "ip_input7"):
            self.assertNotIn(name, self._chosen(assignments, 4))

    def test_an_input_already_on_the_wanted_stream_is_used_as_it_stands(self):
        inputs = _ip_inputs()
        inputs[1].update({"enabled": True, "multicast": {"address": self.A[0]},
                          "port": 1000})
        assignments, errors, _c = self._allocate(self.A, ip_inputs=inputs)
        self.assertEqual(errors, [])
        self.assertEqual(assignments["w0"]["ip_input"], "ip_input2")
        self.assertTrue(assignments["w0"]["reused"])

    def test_a_stream_open_outside_the_pool_is_shared_never_duplicated(self):
        # The decoder refuses to open one address and port on two inputs, so the
        # input already carrying it is referenced rather than a pool slot taken.
        inputs = _ip_inputs()
        inputs[8].update({"enabled": True, "multicast": {"address": self.A[0]},
                          "port": 1000})
        assignments, errors, _c = self._allocate(self.A, self.B, ip_inputs=inputs)
        self.assertEqual(errors, [])
        self.assertEqual(assignments["w0"]["ip_input"], "ip_input9")
        self.assertTrue(assignments["w0"]["outside_pool"])
        # And the next distinct stream still starts at the first pool slot.
        self.assertEqual(assignments["w1"]["ip_input"], "ip_input2")

    def test_a_pool_input_carrying_an_hdmi_role_is_refused(self):
        hdmi = _hdmi_output()
        hdmi[0]["audio"]["input"] = "ip_input2"
        assignments, errors, collisions = mv.allocate_window_inputs(
            self._requirements(self.A), _ip_inputs(), hdmi[0])
        self.assertEqual(assignments, {})
        self.assertIn("HDMI audio", collisions[0]["reason"])
        self.assertTrue(errors)

    def test_a_pool_input_carrying_an_unknown_live_stream_is_refused(self):
        inputs = _ip_inputs()
        inputs[1].update({"enabled": True, "multicast": {"address": "233.252.0.99"},
                          "port": 1000})
        assignments, errors, collisions = self._allocate(self.A, ip_inputs=inputs)
        self.assertEqual(assignments, {})
        self.assertEqual(collisions[0]["ip_input"], "ip_input2")
        self.assertIn("233.252.0.99", collisions[0]["reason"])
        self.assertTrue(errors)

    def test_a_disabled_pool_input_with_a_stale_address_is_reusable(self):
        # Disabled means nothing is open on it; the address it remembers is
        # history, and reserving the pool would be meaningless otherwise.
        inputs = _ip_inputs()
        inputs[1].update({"enabled": False, "multicast": {"address": "233.252.0.99"},
                          "port": 1000})
        assignments, errors, collisions = self._allocate(self.A, ip_inputs=inputs)
        self.assertEqual((errors, collisions), ([], []))
        self.assertEqual(assignments["w0"]["ip_input"], "ip_input2")

    # ---- §18: a saved Multiview reserves nothing --------------------------

    def test_another_saved_multiviews_reference_is_not_a_collision(self):
        # A decoder holds many saved Multiviews and only one is active. If every
        # saved reference reserved an input, the first saved layout would lock
        # the pool against all the others.
        others = [{"name": "multiviewSomeoneElse",
                   "subframes": [{"input": "ip_input2"}]}]
        assignments, errors, collisions = mv.allocate_window_inputs(
            self._requirements(self.A), _ip_inputs(), _hdmi_output()[0], others)
        self.assertEqual((errors, collisions), ([], []))
        self.assertEqual(assignments["w0"]["ip_input"], "ip_input2")

    def test_an_input_another_multiview_has_live_is_still_refused(self):
        # A reference is not a reservation, but a live stream OmniSuite did not
        # configure still is.
        inputs = _ip_inputs()
        inputs[1].update({"enabled": True, "multicast": {"address": "233.252.0.99"},
                          "port": 1000})
        others = [{"name": "multiviewSomeoneElse",
                   "subframes": [{"input": "ip_input2"}]}]
        assignments, errors, collisions = mv.allocate_window_inputs(
            self._requirements(self.A), inputs, _hdmi_output()[0], others)
        self.assertEqual(assignments, {})
        self.assertTrue(errors)
        self.assertIn("233.252.0.99", collisions[0]["reason"])

    def test_an_input_the_active_configuration_owns_is_reconfigurable(self):
        inputs = _ip_inputs()
        inputs[1].update({"enabled": True, "multicast": {"address": "233.252.0.99"},
                          "port": 1000})
        assignments, errors, collisions = mv.allocate_window_inputs(
            self._requirements(self.A), inputs, _hdmi_output()[0],
            owned={"ip_input2"})
        self.assertEqual((errors, collisions), ([], []))
        self.assertEqual(assignments["w0"]["ip_input"], "ip_input2")

    # ---- limits ------------------------------------------------------------

    def test_a_fifth_distinct_stream_has_nowhere_to_go(self):
        assignments, errors, _c = self._allocate(
            self.A, self.B, self.C, self.D, ("233.252.0.45", 1000))
        self.assertEqual(len(assignments), 4)
        self.assertNotIn("w4", assignments)
        self.assertIn("more than the 4 reserved Multiview inputs", errors[0])

    def test_a_decoder_without_a_pool_input_says_so(self):
        assignments, errors, _c = self._allocate(
            self.A, self.B, self.C, self.D, ip_inputs=_ip_inputs(count=5))
        self.assertIn("ip_input6", errors[0])
        self.assertNotIn("w2", assignments)

    def test_the_decoders_own_input_count_is_respected(self):
        # 32 on 4xxx, 16 on the 2.0-era decoder: the count comes from the device.
        classified = mv.classify_ip_inputs(_ip_inputs(count=16), _hdmi_output()[0])
        self.assertEqual(len(classified), 16)

    # ---- two streams must never land on one input -------------------------

    def test_two_streams_never_share_an_input_during_a_transition(self):
        # Found live, recalling Side-by-Side after a quad: the incoming layout
        # wanted a stream that the outgoing one had on the very input the new
        # first window was about to take, so both windows ended on ip_input2 and
        # the Multiview showed one source twice.
        inputs = _ip_inputs()
        inputs[1].update({"enabled": True, "multicast": {"address": self.A[0]},
                          "port": 1000})
        inputs[3].update({"enabled": True, "multicast": {"address": self.B[0]},
                          "port": 1000})
        assignments, errors, _c = self._allocate(
            self.B, self.A, ip_inputs=inputs, owned={"ip_input2", "ip_input4"})
        self.assertEqual(errors, [])
        chosen = self._chosen(assignments, 2)
        self.assertEqual(len(set(chosen)), 2,
                         "two different streams were put on one input: %s" % chosen)
        self.assertEqual(assignments["w0"]["ip_input"], "ip_input4")
        self.assertEqual(assignments["w1"]["ip_input"], "ip_input2")

    def test_every_distinct_stream_gets_a_distinct_input_always(self):
        # The property, stated once: across every arrangement of up to four
        # streams and any starting decoder state, distinct streams never share.
        import itertools
        streams = [self.A, self.B, self.C, self.D]
        for existing in itertools.permutations(streams, 3):
            inputs = _ip_inputs()
            for slot, stream in zip((1, 3, 5), existing):
                inputs[slot].update({"enabled": True,
                                     "multicast": {"address": stream[0]},
                                     "port": 1000})
            for wanted in itertools.permutations(streams, 4):
                assignments, errors, _c = self._allocate(
                    *wanted, ip_inputs=inputs, owned=set(mv.WINDOW_IP_INPUTS))
                self.assertEqual(errors, [], (existing, wanted))
                chosen = self._chosen(assignments, 4)
                self.assertEqual(len(set(chosen)), 4,
                                 "%s -> %s put two streams on one input"
                                 % (list(wanted), chosen))

    # ---- the invariant that a live defect broke ---------------------------

    def test_everything_the_allocator_takes_the_reclaimer_can_give_back(self):
        assignments, errors, _c = self._allocate(self.A, self.B)
        self.assertEqual(errors, [])
        taken = {a["ip_input"]: a["address"] for a in assignments.values()}
        spare, kept = mv.reclaimable_inputs(
            taken, set(), [], _hdmi_output()[0], _ip_inputs())
        self.assertEqual(sorted(spare), sorted(taken),
                         "the allocator took %s but the reclaimer kept %s"
                         % (sorted(taken), kept))


# ==========================================================================
# Shared scaler ownership
# ==========================================================================

class ScalerOwnershipTests(unittest.TestCase):

    def test_a_correct_scaler_needs_no_write(self):
        result = mv.classify_scaler({"enable": True, "width": 1920, "height": 1080},
                                    1920, 1080, 2)
        self.assertEqual(result["status"], mv.SCALER_SAFE)
        self.assertFalse(result["change_required"])

    def test_encoder_one_passing_through_at_the_right_size_is_safe(self):
        result = mv.classify_scaler({"enable": False}, 3840, 2160, 1,
                                    input_resolution="3840x2160")
        self.assertEqual(result["status"], mv.SCALER_SAFE)
        self.assertTrue(result["passthrough"])

    def test_a_change_with_no_known_consumer_is_reported_as_unproven(self):
        # OmniSuite cannot see a Multiview built in the device's own web UI, so
        # "no known consumer" must not be reported as "safe".
        result = mv.classify_scaler({"enable": True, "width": 1920, "height": 1080},
                                    960, 528, 2)
        self.assertEqual(result["status"], mv.SCALER_UNKNOWN_EXTERNAL)
        self.assertTrue(result["change_required"])

    def test_a_known_consumer_wanting_the_same_size_is_only_a_change(self):
        result = mv.classify_scaler({"enable": True, "width": 1920, "height": 1080},
                                    960, 528, 2,
                                    other_consumers=[{"scaler_format": "960x528"}])
        self.assertEqual(result["status"], mv.SCALER_CHANGE)

    def test_a_known_consumer_wanting_another_size_is_a_conflict(self):
        result = mv.classify_scaler({"enable": True, "width": 1920, "height": 1080},
                                    960, 528, 2,
                                    other_consumers=[
                                        {"scaler_format": "1280x720",
                                         "decoder": "dec-b", "multiview": "multiviewB"}])
        self.assertEqual(result["status"], mv.SCALER_CONFLICT)
        self.assertEqual(len(result["conflicts"]), 1)


# ==========================================================================
# The planner
# ==========================================================================

class PlannerTests(unittest.TestCase):

    def _state(self, **overrides):
        state = {"ip": DECODER_IP, "model": "hw-omni-d4511", "multiview": [],
                 "ip_input": _ip_inputs(), "hdmi_output": _hdmi_output()[0]}
        state.update(overrides)
        return state

    def _encoders(self, **overrides):
        encoders = {ENCODER_IP: {
            "device": {"ip": ENCODER_IP, "hostname": "enc-test-01",
                       "model": "hw-omni-e4111"},
            "vc2": _vc2(), "sessions": _sessions(),
            "input_resolution": "3840x2160", "reachable": True}}
        encoders.update(overrides)
        return encoders

    def _desired(self, **overrides):
        desired = {"layout": "pip-bottom-right", "canvas": "1920x1080",
                   "assignments": {"bottom_right": ENCODER_IP},
                   "name": "PiP Bottom Right"}
        desired.update(overrides)
        return desired

    def test_a_plan_is_produced_with_geometry_and_resources(self):
        plan = mv.plan_multiview(self._desired(), self._state(), self._encoders())
        self.assertTrue(plan["ok"], plan["errors"])
        window = next(w for w in plan["windows"] if w["cell"] == "bottom_right")
        self.assertEqual(window["encoder_index"], 2)
        self.assertEqual(window["session"], "session2")
        self.assertEqual(window["scaler_format"], "640x360")
        # Slots go to distinct streams, not to window numbers: the main window
        # has no source here, so the inset is the first stream and takes slot 1.
        self.assertEqual(window["ip_input"]["ip_input"], "ip_input2")

    def test_the_multicast_address_comes_from_the_device_never_invented(self):
        plan = mv.plan_multiview(self._desired(), self._state(), self._encoders())
        window = next(w for w in plan["windows"] if w["cell"] == "bottom_right")
        self.assertEqual(window["multicast"]["address"], S2_VIDEO)
        self.assertTrue(window["multicast"]["generated"])

    def test_a_session_with_no_destination_is_an_error_not_a_generated_address(self):
        sessions = _sessions()
        sessions[1]["video"]["stream"]["destination_address"] = ""
        plan = mv.plan_multiview(
            self._desired(), self._state(),
            self._encoders(**{ENCODER_IP: {
                "device": {"ip": ENCODER_IP, "hostname": "enc-test-01"},
                "vc2": _vc2(), "sessions": sessions, "reachable": True}}))
        self.assertFalse(plan["ok"])
        self.assertTrue(any("no stream destination" in e for e in plan["errors"]))

    def test_saving_writes_the_object_and_nothing_else(self):
        # A decoder holds many saved Multiviews and only one is active, so a
        # saved configuration cannot claim an encoder or a decoder input.
        plan = mv.plan_multiview(self._desired(), self._state(), self._encoders())
        self.assertEqual([m["stage"] for m in plan["mutations"]],
                         [mv.STAGE_MULTIVIEW])
        for mutation in plan["mutations"]:
            self.assertEqual(mutation["node"], "multiview")

    def test_recall_carries_every_resource_change_in_order(self):
        plan = mv.plan_multiview(self._desired(), self._state(), self._encoders())
        stages = [m["stage"] for m in plan["activation"]]
        self.assertLess(stages.index(mv.STAGE_ENCODER_SCALER),
                        stages.index(mv.STAGE_SESSION))
        self.assertLess(stages.index(mv.STAGE_SESSION),
                        stages.index(mv.STAGE_IP_INPUT))
        self.assertEqual([m for m in plan["activation"]
                          if m["node"] == "hdmi_output"], [],
                         "recall planning must not pre-empt the display writes")

    def test_encoder_two_input_is_settled_before_anything_that_depends_on_it(self):
        encoders = self._encoders()
        encoders[ENCODER_IP]["vc2"] = _vc2(encoder2_input="")
        plan = mv.plan_multiview(self._desired(), self._state(), encoders)
        stages = [m["stage"] for m in plan["activation"]]
        self.assertIn(mv.STAGE_ENCODER_INPUT, stages)
        for later in (mv.STAGE_ENCODER_SCALER, mv.STAGE_SESSION, mv.STAGE_IP_INPUT):
            self.assertLess(stages.index(mv.STAGE_ENCODER_INPUT), stages.index(later),
                            later)

    def test_the_layout_name_is_never_sent_to_the_device(self):
        plan = mv.plan_multiview(self._desired(), self._state(), self._encoders())
        multiview = next(m for m in plan["mutations"] if m["stage"] == mv.STAGE_MULTIVIEW)
        self.assertNotIn("layout", multiview["config"])
        self.assertNotIn("resolution", multiview["config"])
        self.assertEqual(multiview["method"], "add_multiview")

    def test_both_windows_of_a_pip_come_from_encoder_two(self):
        plan = mv.plan_multiview(
            self._desired(assignments={"main": ENCODER_IP, "bottom_right": ENCODER2_IP}),
            self._state(),
            self._encoders(**{ENCODER2_IP: {
                "device": {"ip": ENCODER2_IP, "hostname": "enc-test-02"},
                "vc2": _vc2(), "sessions": _sessions(), "reachable": True}}))
        self.assertTrue(plan["ok"], plan["errors"] + plan["conflicts"])
        for window in plan["windows"]:
            self.assertEqual(window["encoder_index"], 2, window["cell"])
            self.assertEqual(window["session"], "session2", window["cell"])

    def test_recall_never_rescales_encoder_one(self):
        plan = mv.plan_multiview(
            self._desired(assignments={"main": ENCODER_IP, "bottom_right": ENCODER2_IP}),
            self._state(),
            self._encoders(**{ENCODER2_IP: {
                "device": {"ip": ENCODER2_IP, "hostname": "enc-test-02"},
                "vc2": _vc2(), "sessions": _sessions(), "reachable": True}}))
        scaler_writes = [m for m in plan["activation"]
                         if m["stage"] == mv.STAGE_ENCODER_SCALER]
        self.assertTrue(scaler_writes)
        for write in scaler_writes:
            self.assertEqual(write["target"], "vc2_encoder2",
                             "Multiview rescaled Encoder 1")

    def test_a_shared_scaler_change_produces_a_warning(self):
        plan = mv.plan_multiview(self._desired(), self._state(), self._encoders())
        self.assertTrue(any("re-scaled" in w for w in plan["warnings"]))
        scaler_writes = [m for m in plan["activation"]
                         if m["stage"] == mv.STAGE_ENCODER_SCALER]
        self.assertTrue(scaler_writes)
        self.assertTrue(all(m.get("shared") for m in scaler_writes))

    def test_a_known_conflict_blocks_the_plan(self):
        known = [{"decoder_ip": "192.0.2.99", "decoder_hostname": "dec-b",
                  "object_name": "multiviewOther",
                  "windows": [{"source_ip": ENCODER_IP, "encoder_index": 2,
                               "scaler_format": "1920x1080"}]}]
        plan = mv.plan_multiview(self._desired(), self._state(), self._encoders(), known)
        self.assertTrue(plan["conflicts"])
        self.assertIn("multiviewOther", plan["conflicts"][0])

    def test_a_plan_with_no_sources_is_refused(self):
        plan = mv.plan_multiview(self._desired(assignments={}),
                                 self._state(), self._encoders())
        self.assertFalse(plan["ok"])

    def test_an_unreachable_encoder_is_reported_not_guessed(self):
        plan = mv.plan_multiview(self._desired(), self._state(), {})
        self.assertFalse(plan["ok"])
        self.assertTrue(any("did not answer" in e for e in plan["errors"]))

    def test_a_snapped_canvas_is_reported_to_the_operator(self):
        plan = mv.plan_multiview(
            self._desired(layout="4-split", assignments={"main": ENCODER_IP}),
            self._state(), self._encoders())
        self.assertTrue(plan["snapped"])
        self.assertEqual(plan["canvas"], {"width": 1920, "height": 1088})
        self.assertTrue(any("snapped" in w for w in plan["warnings"]))
        # A snapped canvas still drives the display at the preset, because the
        # decoder has no 1920x1088 output mode.
        self.assertEqual(plan["output_resolution"], "1920x1080")

    def test_an_existing_name_requires_an_explicit_update(self):
        state = self._state(multiview=[{"name": "multiviewPiPBottomRight",
                                        "subframes": []}])
        plan = mv.plan_multiview(
            self._desired(object_name="multiviewPiPBottomRight"), state,
            self._encoders())
        self.assertFalse(plan["ok"])
        self.assertTrue(any("already has a Multiview" in e for e in plan["errors"]))

    def test_updating_an_existing_object_uses_config_set_not_add(self):
        state = self._state(multiview=[{"name": "multiviewPiPBottomRight",
                                        "subframes": []}])
        plan = mv.plan_multiview(
            self._desired(object_name="multiviewPiPBottomRight", update_existing=True),
            state, self._encoders())
        self.assertTrue(plan["ok"], plan["errors"])
        multiview = next(m for m in plan["mutations"] if m["stage"] == mv.STAGE_MULTIVIEW)
        self.assertIsNone(multiview["method"])

    def test_saving_writes_nothing_at_all_to_the_hdmi_output(self):
        # Not the selected input, not the resolution, not the audio. Everything
        # the operator can see or hear belongs to Show.
        plan = mv.plan_multiview(self._desired(), self._state(), self._encoders())
        self.assertEqual([m for m in plan["mutations"] if m["node"] == "hdmi_output"], [])
        self.assertEqual([m for m in plan["activation"] if m["node"] == "hdmi_output"], [])

    def test_the_main_window_and_its_audio_source_are_named(self):
        plan = mv.plan_multiview(
            self._desired(assignments={"main": ENCODER_IP,
                                       "bottom_right": ENCODER_IP}),
            self._state(), self._encoders())
        self.assertEqual(plan["main_window"], "main")
        self.assertTrue(plan["audio"]["available"])
        self.assertEqual(plan["audio"]["source_ip"], ENCODER_IP)
        self.assertEqual(plan["audio"]["session"], "session1")
        self.assertEqual(plan["audio"]["address"], S1_AUDIO)

    def test_nothing_is_planned_when_the_hardware_already_matches(self):
        # Session enabled and not announcing, scaler already right, Encoder 2
        # already fed, input already bound and enabled, multiview present.
        geometry = mv.compute_windows("pip-bottom-right", 1920, 1080)
        window = next(w for w in geometry["windows"] if w["cell"] == "bottom_right")
        main = next(w for w in geometry["windows"] if w["cell"] == "main")
        inputs = _ip_inputs()
        inputs[1].update({"enabled": True, "multicast": {"address": S2_VIDEO},
                          "port": 1000})
        state = self._state(ip_input=inputs, multiview=[{
            "name": "multiviewPiPBottomRight", "width": 1920, "height": 1080,
            "subframes": [
                {"name": mv.subframe_label("main", main["width"], main["height"]),
                 "x": main["x"], "y": main["y"], "anchor": main["anchor"],
                 "input": "", "priority": 1},
                {"name": mv.subframe_label("bottom_right", window["width"], window["height"]),
                 "x": window["x"], "y": window["y"], "anchor": window["anchor"],
                 "input": "ip_input2", "priority": 2}]}])
        encoders = self._encoders()
        encoders[ENCODER_IP]["vc2"][1]["scaler"] = {"enable": True, "width": 640,
                                                    "height": 360}
        # A PiP inset is a small window, so its target is 150 -- and Encoder 1
        # at 700 leaves 200, which is more than enough. The device already
        # holds the value the policy asks for, so nothing is planned.
        encoders[ENCODER_IP]["vc2"][1]["bitrate"] = mv.SMALL_WINDOW_TARGET
        encoders[ENCODER_IP]["sessions"] = _sessions(session2_enabled=True,
                                                     session2_sap=False)
        plan = mv.plan_multiview(
            self._desired(object_name="multiviewPiPBottomRight", update_existing=True),
            state, encoders, claimed_inputs={"ip_input4"})
        self.assertTrue(plan["ok"], plan["errors"])
        self.assertEqual(plan["activation"], [],
                         [m["description"] for m in plan["activation"]])


# ==========================================================================
# The active canvas is the only one that can be reached
# ==========================================================================

class ActiveCanvasOnlyTests(unittest.TestCase):
    """The 4K and 1440 paths are dormant, not merely hidden."""

    def _state(self):
        return {"ip": DECODER_IP, "model": "hw-omni-d4511", "multiview": [],
                "ip_input": _ip_inputs(), "hdmi_output": _hdmi_output()[0]}

    def _encoders(self):
        return {ENCODER_IP: {"device": {"ip": ENCODER_IP, "hostname": "enc-test-01"},
                             "vc2": _vc2(), "sessions": _sessions(),
                             "reachable": True}}

    def _plan(self, canvas):
        return mv.plan_multiview(
            {"layout": "2x2", "canvas": canvas, "name": "X",
             "assignments": {"top_left": ENCODER_IP}},
            self._state(), self._encoders())

    def test_the_planner_refuses_every_canvas_but_the_active_one(self):
        for canvas in ("3840x2160", "2560x1440", "1280x720", "", None):
            plan = self._plan(canvas) if canvas else self._plan("3840x2160")
            if canvas in ("", None):
                continue
            self.assertFalse(plan["ok"], canvas)
            self.assertTrue(any("1920x1080" in e for e in plan["errors"]), canvas)

    def test_a_refused_canvas_produces_no_mutations_whatsoever(self):
        plan = self._plan("3840x2160")
        self.assertEqual(plan["mutations"], [])
        self.assertEqual(plan["activation"], [])
        self.assertEqual(plan["snapshot"], [])
        self.assertEqual(plan["windows"], [])

    def test_the_active_canvas_is_accepted(self):
        plan = self._plan("1920x1080")
        self.assertTrue(plan["ok"], plan["errors"])

    def test_an_absent_canvas_defaults_to_the_active_one(self):
        plan = mv.plan_multiview(
            {"layout": "2x2", "name": "X", "assignments": {"top_left": ENCODER_IP}},
            self._state(), self._encoders())
        self.assertTrue(plan["ok"], plan["errors"])
        self.assertEqual(plan["canvas_id"], mv.ACTIVE_CANVAS)

    def test_the_catalog_the_ui_consumes_offers_one_canvas(self):
        for entry in mv.layout_geometry_catalog():
            self.assertEqual(list(entry["canvases"]), [mv.ACTIVE_CANVAS])

    def test_no_active_plan_can_reach_a_4k_scaler_format(self):
        # The strongest form of "dormant": run every layout the product offers
        # and assert not one window asks for a size only Encoder 1 can make.
        for layout in mv.LAYOUT_ORDER:
            cells = [c[0] for c in mv.LAYOUTS[layout]["cells"]]
            plan = mv.plan_multiview(
                {"layout": layout, "canvas": mv.ACTIVE_CANVAS, "name": "X",
                 "assignments": {cell: ENCODER_IP for cell in cells[:1]}},
                self._state(), self._encoders())
            for window in plan["windows"]:
                self.assertNotIn(window["scaler_format"], mv.SCALER_4K_FORMATS,
                                 f"{layout}/{window['cell']}")
                self.assertIn(window["scaler_format"], mv.ENCODER2_FORMATS,
                              f"{layout}/{window['cell']}")


# ==========================================================================
# Device semantics the transaction depends on
# ==========================================================================

class DeviceSemanticsTests(MultiviewTestBase):
    """The simulator must behave the way the real device was observed to."""

    def test_config_set_merges_and_does_not_replace_the_collection(self):
        device = self.decoder(multiviews=[
            {"name": "multiviewA", "width": 1920, "height": 1080, "subframes": []},
            {"name": "multiviewB", "width": 3840, "height": 2160, "subframes": []}])
        srv._mv_set(DECODER_IP, "multiview", {"name": "multiviewA", "width": 2560})
        names = [o["name"] for o in device.nodes["multiview"]]
        self.assertEqual(names, ["multiviewA", "multiviewB"])
        self.assertEqual(device.nodes["multiview"][0]["width"], 2560)
        self.assertEqual(device.nodes["multiview"][1]["width"], 3840)

    def test_only_del_multiview_removes_an_object(self):
        device = self.decoder(multiviews=[
            {"name": "multiviewA", "width": 1920, "height": 1080, "subframes": []}])
        # The two mechanisms the real device accepts and then ignores.
        srv._mv_set(DECODER_IP, "multiview", {"name": "multiviewA", "delete": True})
        self.assertEqual(len(device.nodes["multiview"]), 1)
        ok, _message = srv._mv_method(DECODER_IP, "del_multiview", {"name": "multiviewA"})
        self.assertTrue(ok)
        self.assertEqual(device.nodes["multiview"], [])

    def test_the_device_does_not_store_a_layout_field(self):
        self.decoder()
        srv._mv_method(DECODER_IP, "add_multiview",
                       {"name": "multiviewX", "width": 1920, "height": 1080,
                        "layout": "2x2", "resolution": "1920x1080", "subframes": []})
        stored = self.devices[DECODER_IP].nodes["multiview"][0]
        self.assertNotIn("layout", stored)
        self.assertNotIn("resolution", stored)


# ==========================================================================
# Capability
# ==========================================================================

class CapabilityTests(MultiviewTestBase):

    def test_a_decoder_that_answers_is_capable_and_the_answer_is_cached(self):
        device = self.decoder()
        supported, _reason, cached = srv._multiview_capability(DECODER_IP)
        self.assertTrue(supported)
        self.assertFalse(cached)
        reads = len([w for w in device.writes])
        supported, _reason, cached = srv._multiview_capability(DECODER_IP)
        self.assertTrue(supported)
        self.assertTrue(cached, "the second call must not ask the device again")
        self.assertEqual(len(device.writes), reads)

    def test_a_device_without_the_node_is_definitely_not_capable(self):
        self.devices[ENCODER_IP] = FakeDevice({"vc2": _vc2(), "sessions": _sessions()})
        supported, reason, _cached = srv._multiview_capability(ENCODER_IP)
        self.assertIs(supported, False)
        self.assertIn("not found", reason)

    def test_an_unreachable_device_is_not_cached_as_unsupported(self):
        self.devices[DECODER_IP] = FakeDevice({}, unreachable=True)
        supported, _reason, _cached = srv._multiview_capability(DECODER_IP)
        self.assertIsNone(supported)
        with srv._multiview_capability_lock:
            self.assertNotIn(DECODER_IP, srv._MULTIVIEW_CAPABILITY)

    def test_the_decoder_list_does_not_probe_unless_asked(self):
        device = self.decoder()
        response = self.client.get("/api/multiview/decoders")
        body = response.get_json()
        self.assertEqual(body["probed"], 0)
        self.assertEqual(device.writes, [])
        entry = next(d for d in body["decoders"] if d["ip"] == DECODER_IP)
        self.assertIsNone(entry["multiview_supported"])

    def test_probing_answers_the_capability_once(self):
        self.decoder()
        body = self.client.get("/api/multiview/decoders?probe=1").get_json()
        self.assertEqual(body["probed"], 1)
        self.assertTrue(next(d for d in body["decoders"]
                             if d["ip"] == DECODER_IP)["multiview_supported"])
        # Second call is answered from cache.
        body = self.client.get("/api/multiview/decoders?probe=1").get_json()
        self.assertEqual(body["probed"], 0)


# ==========================================================================
# Endpoints
# ==========================================================================

class EndpointTests(MultiviewTestBase):

    def test_the_layout_catalog_is_the_one_the_ui_draws_from(self):
        body = self.client.get("/api/multiview/layouts").get_json()
        self.assertEqual(len(body["layouts"]), 11)
        self.assertEqual([c["id"] for c in body["canvases"]], [mv.ACTIVE_CANVAS])
        self.assertEqual(body["canvas"], mv.ACTIVE_CANVAS)
        self.assertEqual(body["window_ip_inputs"], list(mv.WINDOW_IP_INPUTS))
        self.assertNotIn("2560x1440", [c["id"] for c in body["canvases"]])
        # Same numbers the planner writes.
        four_split = next(e for e in body["layouts"] if e["id"] == "4-split")
        main = next(w for w in four_split["canvases"][mv.ACTIVE_CANVAS]["windows"]
                    if w["cell"] == "main")
        self.assertEqual([main["width"], main["height"]], [1440, 816])
        self.assertEqual(main["encoder"], 2)
        self.assertEqual(body["main_windows"]["4-split"], "main")
        self.assertEqual(body["main_windows"]["2x2"], "top_left")

    def test_sources_exclude_only_the_old_wall_plate(self):
        for ip in (ENCODER_IP, ENCODER2_IP, OLD_WALLPLATE_IP, NEW_WALLPLATE_IP,
                   PLAIN_111_IP):
            self.encoder(ip)
        body = self.client.get("/api/multiview/sources").get_json()
        addresses = {s["ip"] for s in body["sources"]}
        self.assertIn(ENCODER_IP, addresses)
        self.assertIn(PLAIN_111_IP, addresses, "AT-OMNI-111 must stay eligible")
        self.assertIn(NEW_WALLPLATE_IP, addresses,
                      "HW-OMNI-E4111-WP must be an eligible source")
        self.assertNotIn(OLD_WALLPLATE_IP, addresses)
        excluded = {s["ip"]: s for s in body["excluded"]}
        self.assertEqual(list(excluded), [OLD_WALLPLATE_IP])
        self.assertIn("AT-OMNI-111-WP", excluded[OLD_WALLPLATE_IP]["reason"])

    def test_a_source_tile_carries_the_session_multicast_the_scan_recorded(self):
        for ip in (ENCODER_IP, ENCODER2_IP, NEW_WALLPLATE_IP, PLAIN_111_IP):
            self.encoder(ip)
        # `ipN_addr` is the decoder-side field. Reading it here left every tile
        # reporting "not set" while the addresses were sitting in the cache.
        body = self.client.get("/api/multiview/sources").get_json()
        encoder = next(s for s in body["sources"] if s["ip"] == ENCODER_IP)
        self.assertEqual(encoder["session1"], S1_VIDEO)
        self.assertEqual(encoder["session2"], S2_VIDEO)
        self.assertEqual(encoder["session1_port"], 1000)

    def test_the_source_list_contacts_no_device(self):
        device = self.decoder()
        encoder = self.encoder()
        self.client.get("/api/multiview/sources")
        self.assertEqual(device.writes, [])
        self.assertEqual(encoder.writes, [])

    def test_state_reads_an_existing_multiview_and_names_its_layout(self):
        geometry = mv.compute_windows("2x2", 1920, 1080)
        subframes = [{"name": mv.subframe_label(w["cell"], w["width"], w["height"]),
                      "x": w["x"], "y": w["y"], "anchor": w["anchor"],
                      "input": "ip_input2" if w["cell"] == "top_left" else "",
                      "priority": w["priority"],
                      "video": {"input": {"active": True}, "output": {"active": True}}}
                     for w in geometry["windows"]]
        inputs = _ip_inputs()
        inputs[1].update({"enabled": True, "multicast": {"address": S2_VIDEO}})
        self.decoder(multiviews=[{"name": "multiviewRead", "width": 1920,
                                  "height": 1088, "subframes": subframes}],
                     ip_inputs=inputs)
        body = self.client.get(f"/api/multiview/state?ip={DECODER_IP}").get_json()
        self.assertTrue(body["ok"])
        view = body["multiviews"][0]
        self.assertEqual(view["name"], "multiviewRead")
        self.assertEqual(view["layout"], "2x2")
        self.assertEqual(view["layout_source"], "inferred")
        top_left = next(s for s in view["subframes"] if s["cell"] == "top_left")
        self.assertEqual(top_left["ip_input"], "ip_input2")
        self.assertEqual(top_left["multicast"], S2_VIDEO)
        # The source is resolved back to the encoder that advertises that stream.
        self.assertEqual(top_left["source"]["ip"], ENCODER_IP)
        self.assertTrue(top_left["output_active"])

    def test_a_window_source_resolves_to_an_encoder_not_another_decoder(self):
        # A decoder records the multicast it listens to in the same field an
        # encoder uses for the stream it sends. Searching every device resolved
        # a window's source to a second decoder subscribed to the same stream.
        # Listed first on purpose. With it last, the encoder is found before it
        # whether or not the guard exists, and the test passes either way.
        devices = [
            {"ip": "192.0.2.19", "mac": "00:00:5E:00:53:19", "role": "decoder",
             "type": "Decoder", "model": "hw-omni-d4511", "hostname": "dec-listener",
             "ip1_addr": S2_VIDEO}] + _devices()
        with mock.patch.object(srv, "_load_cache", lambda: devices):
            inputs = _ip_inputs()
            inputs[1].update({"enabled": True, "multicast": {"address": S2_VIDEO}})
            self.decoder(multiviews=[{
                "name": "multiviewRead", "width": 1920, "height": 1080,
                "subframes": [{"name": "top_left (960x544)", "x": 0, "y": 0,
                               "anchor": "top left", "input": "ip_input2",
                               "priority": 1,
                               "video": {"input": {"active": True},
                                         "output": {"active": True}}}]}],
                ip_inputs=inputs)
            body = self.client.get(
                f"/api/multiview/state?ip={DECODER_IP}").get_json()
        source = body["multiviews"][0]["subframes"][0]["source"]
        self.assertEqual(source["ip"], ENCODER_IP)
        self.assertEqual(source["hostname"], "enc-test-01")

    def test_state_reports_the_output_and_sap_condition(self):
        self.decoder()
        body = self.client.get(f"/api/multiview/state?ip={DECODER_IP}").get_json()
        self.assertEqual(body["hdmi_output"]["video_input"], "ip_input1")
        self.assertEqual(body["hdmi_output"]["audio_input"], "ip_input3")
        self.assertTrue(body["hdmi_output"]["sap_enabled"])

    def test_state_on_an_incapable_device_says_so_without_failing(self):
        self.devices[DECODER_IP] = FakeDevice({"ip_input": _ip_inputs()})
        response = self.client.get(f"/api/multiview/state?ip={DECODER_IP}")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.get_json()["ok"])

    def test_selecting_a_decoder_costs_three_reads(self):
        device = self.decoder()
        reads = []
        original = device.handle

        def counting(payload):
            if "config_get" in payload:
                reads.append(payload["config_get"])
            return original(payload)

        device.handle = counting
        self.client.get(f"/api/multiview/state?ip={DECODER_IP}")
        self.assertEqual(reads, ["multiview", "hdmi_output", "ip_input"])

    def test_plan_mutates_nothing(self):
        decoder = self.decoder()
        encoder = self.encoder()
        response = self.client.post("/api/multiview/plan", json={
            "decoder": DECODER_IP, "layout": "2x2", "canvas": "1920x1080",
            "assignments": {"top_left": ENCODER_IP}, "select_on_output": True})
        self.assertTrue(response.get_json()["ok"])
        for device in (decoder, encoder):
            self.assertEqual([w for w in device.writes if w[0] != "config_get"], [])


# ==========================================================================
# The apply transaction
# ==========================================================================

class ApplyTransactionTests(MultiviewTestBase):

    def _apply(self, **overrides):
        payload = {"decoder": DECODER_IP, "layout": "2x2", "canvas": "1920x1080",
                   "name": "2x2", "assignments": {"top_left": ENCODER_IP}}
        payload.update(overrides)
        return self.client.post("/api/multiview/apply", json=payload)

    def test_a_clean_save_stores_the_object_and_touches_nothing_else(self):
        decoder = self.decoder()
        encoder = self.encoder()
        body = self._apply().get_json()
        self.assertTrue(body["ok"], body)
        self.assertEqual(body["status"], "VERIFIED")
        self.assertTrue(all(step["verified"] for step in body["verified"]))
        # Saving claims no resource: two saved Multiviews would otherwise have
        # to make their encoder requirements coexist, which is not possible.
        self.assertEqual([w for w in encoder.writes if w[0] != "config_get"], [],
                         "saving wrote to a source")
        self.assertEqual([i["name"] for i in decoder.nodes["ip_input"]
                          if i["enabled"]], ["ip_input1", "ip_input3"])

        # The decoder holds the multiview, with device-legal geometry.
        stored = decoder.nodes["multiview"][0]
        self.assertTrue(stored["name"].startswith("multiview"))
        self.assertEqual([stored["width"], stored["height"]], [1920, 1088])
        assigned = [s for s in stored["subframes"] if s["input"]]
        self.assertEqual(len(assigned), 1)
        self.assertEqual(assigned[0]["input"], "ip_input2")


        # And the display is exactly as it was: saving changes nothing anyone
        # can see or hear.
        output = decoder.nodes["hdmi_output"][0]
        self.assertEqual(output["video"]["input"], "ip_input1")
        self.assertTrue(output["sap_input"]["enabled"])
        self.assertEqual(output["video"]["output"]["resolution"], "3840x2160")
        self.assertEqual(output["audio"]["input"], "ip_input3")

    def test_the_layout_is_persisted_by_omnisuite_because_the_device_drops_it(self):
        self.decoder()
        self.encoder()
        body = self._apply().get_json()
        name = body["plan"]["object_name"]
        stored = self.devices[DECODER_IP].nodes["multiview"][0]
        self.assertNotIn("layout", stored)
        recorded = srv._multiview_meta_for({"ip": DECODER_IP, "mac": "00:00:5E:00:53:10"})
        self.assertEqual(recorded[name]["layout"], "2x2")
        self.assertEqual(recorded[name]["canvas"], "1920x1088")

    def test_metadata_is_keyed_by_hardware_identity_not_address(self):
        self.decoder()
        self.encoder()
        self._apply()
        with srv._multiview_meta_lock:
            keys = list(srv._MULTIVIEW_META)
        self.assertEqual(keys, ["mac:00005e005310"])

    def test_a_silently_ignored_write_is_caught_by_readback(self):
        # The decoder's defining hazard: it answers error:false and does nothing.
        decoder = self.decoder()
        self.encoder()
        decoder.swallow["multiview2x2"] = 1
        body = self._apply().get_json()
        self.assertFalse(body["ok"])
        self.assertIn("FAILED", body["status"])
        self.assertTrue(any("accepted the write but does not hold it" in f["error"]
                            for f in body["failures"]))

    def test_a_failed_apply_rolls_back_and_says_so(self):
        decoder = self.decoder()
        encoder = self.encoder()
        before_output = copy.deepcopy(decoder.nodes["hdmi_output"][0])
        before_scaler = copy.deepcopy(
            next(e for e in encoder.nodes["vc2"] if e["name"] == "vc2_encoder2")["scaler"])
        # Fail at the last stage, after several changes have already landed.
        decoder.swallow["multiview2x2"] = 99
        body = self._apply().get_json()
        self.assertFalse(body["ok"])
        self.assertEqual(body["status"], "FAILED — ROLLED BACK")
        self.assertTrue(all(entry["verified"] for entry in body["rollback"]),
                        body["rollback"])

        # The encoder is back where it started and the multiview was removed.
        after_scaler = next(e for e in encoder.nodes["vc2"]
                            if e["name"] == "vc2_encoder2")["scaler"]
        self.assertEqual(after_scaler, before_scaler)
        self.assertEqual(decoder.nodes["multiview"], [])
        self.assertEqual(decoder.nodes["ip_input"][1]["enabled"], False)
        self.assertEqual(decoder.nodes["hdmi_output"][0]["video"]["input"],
                         before_output["video"]["input"])

    def test_an_unverifiable_rollback_is_reported_as_incomplete(self):
        # A layout change prunes a subframe, the object write then fails, and
        # re-adding the subframe is refused -- so the bench is left part-way.
        decoder = self.decoder()
        self.encoder()
        name = self._apply().get_json()["plan"]["object_name"]
        # The object write is accepted and ignored, and the restore that would
        # put it back is then refused outright.
        decoder.swallow[name] = 1
        decoder.refuse_after["multiview"] = 1
        body = self._apply(layout="side-by-side",
                           assignments={"left": ENCODER_IP},
                           object_name=name, update_existing=True).get_json()
        decoder.refuse_after.pop("multiview", None)
        self.assertFalse(body["ok"], body)
        self.assertIn("ROLLBACK INCOMPLETE", body["status"])
        self.assertFalse(all(entry["verified"] for entry in body["rollback"]))

    def test_a_mid_stage_failure_stops_the_remaining_stages(self):
        decoder = self.decoder()
        self.encoder()
        decoder.refuse["add_multiview"] = "device busy"
        body = self._apply().get_json()
        decoder.refuse.pop("add_multiview", None)
        self.assertFalse(body["ok"])
        self.assertEqual(decoder.nodes["multiview"], [])
        self.assertFalse(decoder.nodes["ip_input"][1]["enabled"])

    def test_applying_twice_is_verified_and_changes_nothing_the_second_time(self):
        self.decoder()
        self.encoder()
        first = self._apply().get_json()
        self.assertTrue(first["ok"])
        name = first["plan"]["object_name"]
        second = self._apply(object_name=name, update_existing=True).get_json()
        self.assertTrue(second["ok"], second)
        self.assertEqual(second["status"], "VERIFIED")
        non_multiview = [d for d in second["applied"] if "multiview" not in d.lower()]
        self.assertEqual(non_multiview, [], non_multiview)

    def test_a_conflicting_scaler_is_refused_rather_than_retuned(self):
        decoder = self.decoder()
        encoder = self.encoder()
        srv._record_multiview_meta(
            {"ip": "192.0.2.99", "mac": "00:00:5E:00:53:99", "hostname": "dec-b"},
            "multiviewOther",
            {"layout": "2x2", "windows": [{"source_ip": ENCODER_IP, "encoder_index": 2,
                                           "scaler_format": "1280x720"}]})
        response = self._apply()
        self.assertEqual(response.status_code, 409)
        body = response.get_json()
        self.assertEqual(body["status"], "conflict")
        # Nothing was written anywhere.
        self.assertEqual([w for w in encoder.writes if w[0] != "config_get"], [])
        self.assertEqual(decoder.nodes["multiview"], [])

    def test_an_invalid_plan_is_refused_before_any_write(self):
        decoder = self.decoder()
        self.encoder()
        response = self._apply(canvas="2560x1440")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(decoder.nodes["multiview"], [])

    def test_a_4k_canvas_is_refused_by_the_endpoint_before_any_write(self):
        decoder = self.decoder()
        encoder = self.encoder()
        response = self._apply(canvas="3840x2160")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(decoder.nodes["multiview"], [])
        self.assertEqual([w for w in encoder.writes if w[0] != "config_get"], [])

    def test_encoder_one_is_never_written_at_all(self):
        self.decoder()
        encoder = self.encoder()
        self.encoder(ENCODER2_IP)
        body = self._apply(layout="pip-bottom-right", name="PiP Bottom Right",
                           assignments={"main": ENCODER_IP,
                                        "bottom_right": ENCODER2_IP}).get_json()
        self.assertTrue(body["ok"], body)
        self.assertEqual([m for m in body["plan"]["mutations"]
                          if m["target"] == "vc2_encoder1"], [])
        encoder1 = next(e for e in encoder.nodes["vc2"] if e["name"] == "vc2_encoder1")
        self.assertEqual(encoder1["scaler"], {"enable": False, "width": 0, "height": 0})
        self.assertEqual(encoder1["bitrate"], 700)
        self.assertEqual(encoder1["input"], "hdmi_input1")

    def test_one_source_in_two_differently_sized_windows_is_refused(self):
        # One encoder, one scaler, two sizes. At a 4K canvas this was legal --
        # the main window went to Encoder 1 -- and with every window on Encoder 2
        # it is the conflict that has to be caught.
        decoder = self.decoder()
        encoder = self.encoder()
        response = self._apply(layout="pip-bottom-right", name="PiP Bottom Right",
                               assignments={"main": ENCODER_IP,
                                            "bottom_right": ENCODER_IP})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(decoder.nodes["multiview"], [])
        self.assertEqual([w for w in encoder.writes if w[0] != "config_get"], [])

    def test_a_pip_from_two_sources_gives_each_its_own_encoder_two(self):
        decoder = self.decoder()
        self.encoder()
        self.encoder(ENCODER2_IP)
        body = self._apply(layout="pip-bottom-right", name="PiP Bottom Right",
                           assignments={"main": ENCODER_IP,
                                        "bottom_right": ENCODER2_IP}).get_json()
        self.assertTrue(body["ok"], body)
        windows = {w["cell"]: w for w in body["plan"]["windows"]}
        self.assertEqual(windows["main"]["encoder_index"], 2)
        self.assertEqual(windows["bottom_right"]["encoder_index"], 2)
        self.assertEqual(windows["main"]["ip_input"]["ip_input"], "ip_input2")
        self.assertEqual(windows["bottom_right"]["ip_input"]["ip_input"], "ip_input4")
        self.assertEqual(len(decoder.nodes["multiview"][0]["subframes"]), 2)



# ==========================================================================
# Encoder 2's physical input
# ==========================================================================

class EncoderInputTests(MultiviewTestBase):
    """Session 2 configured and Encoder 2 set to Not used produces no video.

    Observed on the bench: two of four sources shipped with
    `vc2_encoder2.input = ""`. Everything downstream can then be configured,
    verified and reported correct while nothing is ever encoded.
    """

    def _apply(self, **overrides):
        """Save, then recall. Every resource decision belongs to recall now, so
        a test about encoder or input state has to go all the way."""
        saved = self.save(**overrides).get_json()
        if not saved.get("ok"):
            return saved
        name = saved["plan"]["object_name"]
        self.offer(name)
        body = self.recall(name).get_json()
        body.setdefault("failures", [s for s in (body.get("steps") or [])
                                     if not s.get("verified")])
        body.setdefault("verified", body.get("steps") or [])
        body["object_name"] = name
        return body

    def _encoder2(self, encoder):
        return next(e for e in encoder.nodes["vc2"] if e["name"] == "vc2_encoder2")

    # --- classification -----------------------------------------------------

    def test_not_used_is_recognised_and_told_what_to_follow(self):
        answer = mv.classify_encoder_input("hdmi_input1", "")
        self.assertEqual(answer["status"], mv.ENCODER_INPUT_NOT_USED)
        self.assertEqual(answer["wanted"], "hdmi_input1")
        self.assertTrue(answer["change_required"])

    def test_the_right_input_is_left_alone(self):
        answer = mv.classify_encoder_input("hdmi_input1", "hdmi_input1")
        self.assertEqual(answer["status"], mv.ENCODER_INPUT_OK)
        self.assertFalse(answer["change_required"])

    def test_a_different_input_is_a_conflict_not_an_overwrite(self):
        answer = mv.classify_encoder_input("hdmi_input1", "hdmi_input2")
        self.assertEqual(answer["status"], mv.ENCODER_INPUT_CONFLICT)
        self.assertFalse(answer["change_required"])
        self.assertIn("cannot prove that input is free", answer["reason"])

    def test_an_encoder_one_with_no_input_leaves_nothing_to_follow(self):
        answer = mv.classify_encoder_input("", "")
        self.assertEqual(answer["status"], mv.ENCODER_INPUT_UNAVAILABLE)
        self.assertFalse(answer["change_required"])

    # --- the transaction ----------------------------------------------------

    def test_an_unused_encoder_two_is_pointed_at_encoder_ones_input(self):
        self.decoder()
        encoder = self.encoder(vc2=_vc2(encoder2_input=""))
        body = self._apply()
        self.assertTrue(body["ok"], body)
        self.assertEqual(self._encoder2(encoder)["input"], "hdmi_input1")
        step = next(s for s in body["steps"] if s["stage"] == mv.STAGE_ENCODER_INPUT)
        self.assertTrue(step["verified"])

    def test_the_operator_is_told_before_it_happens(self):
        self.decoder()
        self.encoder(vc2=_vc2(encoder2_input=""))
        plan = self.client.post("/api/multiview/plan", json={
            "decoder": DECODER_IP, "layout": "2x2", "canvas": mv.ACTIVE_CANVAS,
            "name": "2x2", "assignments": {"top_left": ENCODER_IP},
        }).get_json()["plan"]
        self.assertTrue(any("Not used" in w for w in plan["warnings"]), plan["warnings"])

    def test_an_encoder_two_already_correct_is_not_rewritten(self):
        self.decoder()
        encoder = self.encoder(vc2=_vc2(encoder2_input="hdmi_input1"))
        body = self._apply()
        self.assertTrue(body["ok"], body)
        self.assertEqual([m for m in body["plan"]["activation"]
                          if m["stage"] == mv.STAGE_ENCODER_INPUT], [])
        input_writes = [w for w in encoder.writes if w[0] == "config_set"
                        and "input" in (w[2][0] if w[2] else {})]
        self.assertEqual(input_writes, [])

    def test_an_encoder_two_on_another_input_is_refused_not_overwritten(self):
        # The conflict is a recall-time refusal now: saving stores a
        # description, and only recall has to make the encoder produce video.
        decoder = self.decoder()
        encoder = self.encoder(vc2=_vc2(encoder2_input="hdmi_input2"))
        body = self._apply()
        self.assertFalse(body["ok"], body)
        self.assertIn("already encoding hdmi_input2", body["error"])
        self.assertEqual(self._encoder2(encoder)["input"], "hdmi_input2")
        self.assertEqual([w for w in encoder.writes if w[0] != "config_get"], [])
        # The display was never touched either.
        self.assertEqual(decoder.nodes["hdmi_output"][0]["video"]["input"], "ip_input1")

    def test_a_silently_ignored_input_write_is_caught_by_readback(self):
        # The device's defining hazard, on the field this phase exists for.
        self.decoder()
        encoder = self.encoder(vc2=_vc2(encoder2_input=""))
        # The input write is the first thing sent to vc2_encoder2.
        encoder.swallow["vc2_encoder2"] = 1
        body = self._apply()
        self.assertFalse(body["ok"], body)
        self.assertTrue(any(f.get("error") for f in body["failures"]), body["failures"])

    def test_a_later_failure_puts_the_input_back_to_not_used(self):
        decoder = self.decoder()
        encoder = self.encoder(vc2=_vc2(encoder2_input=""))
        decoder.refuse["ip_input"] = "device busy"
        body = self._apply()
        decoder.refuse.pop("ip_input", None)
        self.assertFalse(body["ok"], body)
        self.assertIn("ROLLED BACK", body["status"])
        self.assertEqual(self._encoder2(encoder)["input"], "",
                         "Encoder 2's input was left pointing somewhere on rollback")

    def test_the_input_is_configured_before_the_session_that_depends_on_it(self):
        self.decoder()
        encoder = self.encoder(vc2=_vc2(encoder2_input=""))
        self._apply()
        nodes = [w[1] for w in encoder.writes if w[0] == "config_set"]
        self.assertLess(nodes.index("vc2"), nodes.index("sessions"))


# ==========================================================================
# Session 2
# ==========================================================================

class SessionTwoTests(MultiviewTestBase):
    """Every window's video comes from Session 2, which announces nothing."""

    def _apply(self, **overrides):
        """Save, then recall. Every resource decision belongs to recall now, so
        a test about encoder or input state has to go all the way."""
        saved = self.save(**overrides).get_json()
        if not saved.get("ok"):
            return saved
        name = saved["plan"]["object_name"]
        self.offer(name)
        body = self.recall(name).get_json()
        body.setdefault("failures", [s for s in (body.get("steps") or [])
                                     if not s.get("verified")])
        body.setdefault("verified", body.get("steps") or [])
        body["object_name"] = name
        return body

    def _session(self, encoder, name="session2"):
        return next(s for s in encoder.nodes["sessions"] if s["name"] == name)

    def test_session_two_is_assigned_to_encoder_two_and_enabled(self):
        self.decoder()
        encoder = self.encoder()
        self.assertTrue(self._apply()["ok"])
        session2 = self._session(encoder)
        self.assertEqual(session2["video"]["encoder"], "vc2_encoder2")
        self.assertTrue(session2["video"]["stream"]["enabled"])

    def test_a_session_pointed_at_the_wrong_encoder_is_corrected(self):
        self.decoder()
        sessions = _sessions()
        sessions[1]["video"]["encoder"] = "vc2_encoder1"
        encoder = self.encoder(sessions=sessions)
        self.assertTrue(self._apply()["ok"])
        self.assertEqual(self._session(encoder)["video"]["encoder"], "vc2_encoder2")

    def test_session_two_sap_announcement_is_turned_off(self):
        # A decoder with SAP enabled discovers an announced session and repoints
        # its own inputs at it, underneath whatever configured them.
        self.decoder()
        encoder = self.encoder()
        self.assertTrue(self._session(encoder)["sap"]["enabled"], "fixture")
        body = self._apply()
        self.assertTrue(body["ok"], body)
        self.assertFalse(self._session(encoder)["sap"]["enabled"])
        step = next(s for s in body["steps"] if s["stage"] == mv.STAGE_SESSION_SAP)
        self.assertTrue(step["verified"])

    def test_session_one_keeps_announcing_itself(self):
        self.decoder()
        encoder = self.encoder()
        self._apply()
        self.assertTrue(self._session(encoder, "session1")["sap"]["enabled"],
                        "Session 1's announcement is normal and is left alone")

    def test_an_already_silent_session_two_is_not_rewritten(self):
        self.decoder()
        encoder = self.encoder(sessions=_sessions(session2_sap=False))
        body = self._apply()
        self.assertTrue(body["ok"], body)
        self.assertEqual([m for m in body["plan"]["activation"]
                          if m["stage"] == mv.STAGE_SESSION_SAP], [])

    def test_a_failed_apply_restores_the_announcement(self):
        decoder = self.decoder()
        encoder = self.encoder()
        decoder.refuse["ip_input"] = "device busy"
        body = self._apply()
        decoder.refuse.pop("ip_input", None)
        self.assertFalse(body["ok"])
        self.assertTrue(self._session(encoder)["sap"]["enabled"],
                        "the SAP announcement was not restored")

    def test_a_silently_ignored_sap_write_is_caught_by_readback(self):
        self.decoder()
        encoder = self.encoder()
        # Let the scaler, bitrate and session writes land; swallow the SAP one.
        seen = {"n": 0}
        real = encoder._set

        def swallow_sap(request):
            config = (request.get("config") or [{}])[0]
            # Only the forward write. Swallowing the rollback too would make
            # this a test about a broken rollback rather than about the
            # read-back noticing.
            if "sap" in config and not seen["n"]:
                seen["n"] += 1
                encoder.writes.append(("config_set", request.get("name"), [config]))
                return {"error": False}
            return real(request)

        encoder._set = swallow_sap
        body = self._apply()
        encoder._set = real
        self.assertEqual(seen["n"], 1)
        self.assertFalse(body["ok"], body)
        self.assertTrue(any(f.get("error") for f in body["failures"]))

    def test_session_one_video_is_never_touched(self):
        self.decoder()
        encoder = self.encoder()
        before = copy.deepcopy(self._session(encoder, "session1"))
        self.assertTrue(self._apply()["ok"])
        self.assertEqual(self._session(encoder, "session1"), before)


# ==========================================================================
# Bandwidth
# ==========================================================================

class BandwidthModelTests(unittest.TestCase):
    """Explicit layout targets, capped by what each source can spare.

    The policy is stated rather than derived:

        equal-sized windows   200 Mb/s
        the largest window    300 Mb/s
        the smaller windows   150 Mb/s

        actual = min(target, 900 - current Encoder 1 bitrate)

    Encoder 1 is read and never written. Phase 7B measured that changing it
    blacks every decoder watching that source for about half a second, which is
    a far worse outcome than a Multiview window running at 150 instead of 200.
    """

    def _stream(self, ip, target, encoder1=100):
        return {"source_ip": ip, "target": target, "encoder1_bitrate": encoder1}

    def test_the_budgets_and_the_device_limits_are_what_hardware_reported(self):
        self.assertEqual(mv.SOURCE_VIDEO_BUDGET, 900)
        self.assertEqual(mv.DECODER_MULTIVIEW_BUDGET, 900)
        # Measured, not assumed: vc2_encoder2 accepted 20 and 900 and refused
        # 10 and 901 with "Invalid bitrate".
        self.assertEqual(mv.ENCODER2_MIN_BITRATE, 20)
        self.assertEqual(mv.ENCODER2_MAX_BITRATE, 900)

    def test_the_stated_targets(self):
        self.assertEqual(mv.EQUAL_WINDOW_TARGET, 200)
        self.assertEqual(mv.MAIN_WINDOW_TARGET, 300)
        self.assertEqual(mv.SMALL_WINDOW_TARGET, 150)

    # ---- roles come from geometry, never from cell names ------------------
    def test_windows_of_one_size_are_all_equal(self):
        sizes = [(960, 544)] * 4
        self.assertEqual(mv.window_roles(sizes), ["equal"] * 4)
        self.assertEqual(mv.window_targets(sizes), [200] * 4)

    def test_the_largest_window_is_the_main_one_and_the_rest_are_small(self):
        sizes = [(1280, 720), (640, 360), (640, 360), (640, 360)]
        self.assertEqual(mv.window_roles(sizes),
                         ["main", "small", "small", "small"])
        self.assertEqual(mv.window_targets(sizes), [300, 150, 150, 150])

    def test_the_main_window_is_found_wherever_it_appears_in_the_order(self):
        sizes = [(640, 360), (640, 360), (1920, 1080)]
        self.assertEqual(mv.window_roles(sizes), ["small", "small", "main"])

    def test_every_layout_gives_equal_windows_equal_targets(self):
        """§8, across all eleven, from the real geometry."""
        for layout in mv.LAYOUT_ORDER:
            windows = mv.compute_windows(layout, 1920, 1080)["windows"]
            sizes = [(w["width"], w["height"]) for w in windows]
            targets = mv.window_targets(sizes)
            by_size = {}
            for size, target in zip(sizes, targets):
                by_size.setdefault(size, set()).add(target)
            for size, found in by_size.items():
                self.assertEqual(len(found), 1,
                                 "%s: %sx%s got targets %s"
                                 % (layout, size[0], size[1], sorted(found)))

    def test_no_small_window_ever_out_targets_the_main_one(self):
        """§9: a small inset must never be given more than the main window."""
        for layout in mv.LAYOUT_ORDER:
            windows = mv.compute_windows(layout, 1920, 1080)["windows"]
            sizes = [(w["width"], w["height"]) for w in windows]
            roles = mv.window_roles(sizes)
            targets = mv.window_targets(sizes)
            main = [t for r, t in zip(roles, targets) if r == "main"]
            small = [t for r, t in zip(roles, targets) if r == "small"]
            if main and small:
                self.assertGreater(min(main), max(small), layout)

    def test_the_published_schema_is_what_the_layouts_produce(self):
        """The table in the documentation, asserted against the planner."""
        expected = {
            "side-by-side": [200, 200],
            "2x2": [200, 200, 200, 200],
            "pip-top-left": [300, 150],
            "pip-top-right": [300, 150],
            "pip-bottom-left": [300, 150],
            "pip-bottom-right": [300, 150],
            "1+3-horizontal-bottom": [300, 150, 150, 150],
            "1+3-horizontal-top": [300, 150, 150, 150],
            "1+3-vertical-right": [300, 150, 150, 150],
            "1+3-vertical-left": [300, 150, 150, 150],
            "4-split": [300, 150, 150, 150],
        }
        self.assertEqual(set(expected), set(mv.LAYOUT_ORDER))
        for layout, targets in expected.items():
            windows = mv.compute_windows(layout, 1920, 1080)["windows"]
            self.assertEqual(
                mv.window_targets([(w["width"], w["height"]) for w in windows]),
                targets, layout)

    # ---- headroom ---------------------------------------------------------
    def test_a_source_with_room_gets_its_full_target(self):
        allocation = mv.allocate_bitrates([self._stream("a", 200, 700)])[0]
        self.assertEqual(allocation["bitrate"], 200)
        self.assertEqual(allocation["headroom"], 200)
        self.assertEqual(allocation["capped_by"], "")

    def test_a_source_without_room_gets_what_it_can_spare(self):
        """§7's worked example, and the one that used to read as a reduction."""
        allocation = mv.allocate_bitrates([self._stream("a", 200, 750)])[0]
        self.assertEqual(allocation["target"], 200)
        self.assertEqual(allocation["bitrate"], 150)
        self.assertIn("Encoder 1 is at 750", allocation["capped_by"])
        self.assertIsNone(allocation["encoder1_target"],
                          "Encoder 1 was made a target of the plan")

    def test_encoder_one_is_never_reduced_at_any_bitrate(self):
        """§13, swept across the whole range rather than sampled."""
        for encoder1 in range(0, 901, 10):
            for target in (mv.SMALL_WINDOW_TARGET, mv.EQUAL_WINDOW_TARGET,
                           mv.MAIN_WINDOW_TARGET):
                allocation = mv.allocate_bitrates(
                    [self._stream("a", target, encoder1)])[0]
                self.assertIsNone(allocation["encoder1_target"],
                                  "E1=%s target=%s" % (encoder1, target))
                if allocation["bitrate"] is not None:
                    self.assertLessEqual(encoder1 + allocation["bitrate"],
                                         mv.SOURCE_VIDEO_BUDGET,
                                         "E1=%s" % encoder1)

    def test_a_source_with_no_usable_headroom_is_refused_not_trimmed(self):
        """Encoder 2 cannot run below 20 Mb/s, and Encoder 1 is still not touched."""
        allocation = mv.allocate_bitrates([self._stream("a", 200, 895)])[0]
        self.assertIsNone(allocation["bitrate"])
        self.assertIsNone(allocation["encoder1_target"])
        self.assertIn("less than the 20 Mb/s", allocation["error"])

    def test_an_unknown_encoder_one_bitrate_is_not_treated_as_zero(self):
        allocation = mv.allocate_bitrates(
            [{"source_ip": "a", "target": 200, "encoder1_bitrate": None}])[0]
        self.assertIsNone(allocation["headroom"])
        self.assertIsNone(allocation["encoder1_target"])
        self.assertEqual(allocation["bitrate"], 200)

    # ---- both budgets ------------------------------------------------------
    def test_no_layout_can_ask_the_decoder_for_more_than_it_has(self):
        for layout in mv.LAYOUT_ORDER:
            windows = mv.compute_windows(layout, 1920, 1080)["windows"]
            targets = mv.window_targets(
                [(w["width"], w["height"]) for w in windows])
            allocations = mv.allocate_bitrates(
                [self._stream("s%d" % i, t) for i, t in enumerate(targets)])
            self.assertLessEqual(mv.decoder_aggregate(allocations),
                                 mv.DECODER_MULTIVIEW_BUDGET, layout)

    def test_the_targets_leave_the_decoder_budget_with_room_to_spare(self):
        """§12: 900 is a ceiling, not something to consume."""
        worst = max(
            sum(mv.window_targets([(w["width"], w["height"]) for w in
                                   mv.compute_windows(layout, 1920, 1080)["windows"]]))
            for layout in mv.LAYOUT_ORDER)
        self.assertEqual(worst, 800, "2x2 is the heaviest layout")
        self.assertLess(worst, mv.DECODER_MULTIVIEW_BUDGET)


class BandwidthPlanTests(MultiviewTestBase):
    """The budgets as the planner and the transaction apply them."""

    def _plan(self, layout="2x2", assignments=None):
        cells = [c[0] for c in mv.LAYOUTS[layout]["cells"]]
        assignments = assignments or {cells[0]: ENCODER_IP}
        return self.client.post("/api/multiview/plan", json={
            "decoder": DECODER_IP, "layout": layout, "canvas": mv.ACTIVE_CANVAS,
            "name": layout, "assignments": assignments}).get_json()["plan"]

    def test_one_source_in_four_windows_is_one_stream_at_the_decoder(self):
        self.decoder()
        self.encoder()
        plan = self._plan(assignments={c: ENCODER_IP for c in
                                       ("top_left", "top_right", "bottom_left",
                                        "bottom_right")})
        self.assertEqual(plan["bandwidth"]["unique_streams"], 1)
        self.assertEqual(plan["bandwidth"]["decoder_aggregate"], 200)
        self.assertEqual(len(plan["bandwidth"]["allocations"]), 1)

    def test_four_sources_are_four_streams_inside_the_decoder_budget(self):
        self.decoder()
        for ip in (ENCODER_IP, ENCODER2_IP, NEW_WALLPLATE_IP, PLAIN_111_IP):
            self.encoder(ip)
        plan = self._plan(assignments=dict(zip(
            ("top_left", "top_right", "bottom_left", "bottom_right"),
            (ENCODER_IP, ENCODER2_IP, NEW_WALLPLATE_IP, PLAIN_111_IP))))
        self.assertTrue(plan["ok"], plan["errors"])
        self.assertEqual(plan["bandwidth"]["unique_streams"], 4)
        self.assertLessEqual(plan["bandwidth"]["decoder_aggregate"],
                             mv.DECODER_MULTIVIEW_BUDGET)

    def test_the_bitrate_is_written_to_encoder_two_only(self):
        self.decoder()
        self.encoder()
        plan = self._plan()
        writes = [m for m in plan["activation"] if m["stage"] == mv.STAGE_ENCODER_BITRATE]
        self.assertTrue(writes)
        for write in writes:
            self.assertEqual(write["target"], "vc2_encoder2")

    def test_a_source_with_almost_no_headroom_keeps_its_encoder_one(self):
        """Phase 7C: Encoder 1 is never reduced to widen a Multiview window.

        This test used to assert the opposite -- that Encoder 1 was cut from 850
        to 750 so the window could have its 150. Phase 7B measured what that
        costs: every decoder watching that source blacks out for about half a
        second. A 50 Mb/s Multiview window is the better trade.
        """
        self.decoder()
        encoder = self.encoder(ENCODER_IP)
        first = next(e for e in encoder.nodes["vc2"] if e["name"] == "vc2_encoder1")
        first["bitrate"] = 850
        body = self.save(layout="2x2", name="Tight",
                         assignments={"top_left": ENCODER_IP}).get_json()
        self.assertTrue(body["ok"], body)
        allocation = body["plan"]["bandwidth"]["allocations"][0]
        self.assertEqual(allocation["encoder1_bitrate"], 850)
        self.assertIsNone(allocation["encoder1_target"])
        self.assertEqual(allocation["target"], mv.EQUAL_WINDOW_TARGET)
        self.assertEqual(allocation["bitrate"], 50)
        # And nothing in the plan writes Encoder 1.
        for mutation in body["plan"]["activation"]:
            self.assertNotEqual(mutation.get("target"), "vc2_encoder1",
                                "the plan writes Encoder 1: %s" % mutation)
        self.assertEqual(first["bitrate"], 850, "Encoder 1 was changed")

    def test_encoder_one_is_restored_when_a_later_step_fails(self):
        decoder = self.decoder()
        encoder = self.encoder(vc2=_vc2(encoder1_bitrate=850))
        decoder.refuse["ip_input"] = "device busy"
        name = self.save().get_json()["plan"]["object_name"]
        self.offer(name)
        body = self.recall(name).get_json()
        decoder.refuse.pop("ip_input", None)
        self.assertFalse(body["ok"], body)
        encoder1 = next(e for e in encoder.nodes["vc2"] if e["name"] == "vc2_encoder1")
        self.assertEqual(encoder1["bitrate"], 850)

    def test_encoder_ones_scaler_is_untouched_even_when_its_bitrate_is_not(self):
        self.decoder()
        encoder = self.encoder(vc2=_vc2(encoder1_bitrate=850))
        before = copy.deepcopy(
            next(e for e in encoder.nodes["vc2"] if e["name"] == "vc2_encoder1")["scaler"])
        self.commission()
        after = next(e for e in encoder.nodes["vc2"]
                     if e["name"] == "vc2_encoder1")["scaler"]
        self.assertEqual(after, before)

    def test_every_layout_stays_inside_both_budgets_through_the_planner(self):
        self.decoder()
        sources = [ENCODER_IP, ENCODER2_IP, NEW_WALLPLATE_IP, PLAIN_111_IP]
        for ip in sources:
            self.encoder(ip)
        for layout in mv.LAYOUT_ORDER:
            self.decoder()          # a fresh decoder per layout
            cells = [c[0] for c in mv.LAYOUTS[layout]["cells"]]
            plan = self._plan(layout, dict(zip(cells, sources)))
            self.assertTrue(plan["ok"], (layout, plan["errors"]))
            self.assertLessEqual(plan["bandwidth"]["decoder_aggregate"],
                                 mv.DECODER_MULTIVIEW_BUDGET, layout)
            for allocation in plan["bandwidth"]["allocations"]:
                encoder1 = (allocation["encoder1_target"]
                            if allocation["encoder1_target"] is not None
                            else allocation["encoder1_bitrate"])
                self.assertLessEqual(encoder1 + allocation["bitrate"],
                                     mv.SOURCE_VIDEO_BUDGET, layout)

# ==========================================================================
# Delete
# ==========================================================================

class DeleteTests(MultiviewTestBase):

    def _existing(self, on_output=False):
        hdmi = _hdmi_output()
        hdmi[0]["video"]["available_inputs"].append("multiviewGone")
        if on_output:
            hdmi[0]["video"]["input"] = "multiviewGone"
        return self.decoder(
            multiviews=[{"name": "multiviewGone", "width": 1920, "height": 1080,
                         "subframes": []}], hdmi=hdmi)

    def test_delete_uses_the_method_and_verifies_absence(self):
        device = self._existing()
        body = self.client.post("/api/multiview/delete", json={
            "decoder": DECODER_IP, "name": "multiviewGone"}).get_json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["status"], "VERIFIED")
        self.assertEqual(device.nodes["multiview"], [])
        methods = [w for w in device.writes if w[0] == "method"]
        self.assertEqual([m[1] for m in methods], ["del_multiview"])

    def test_the_output_is_moved_off_the_object_before_it_is_deleted(self):
        device = self._existing(on_output=True)
        body = self.client.post("/api/multiview/delete", json={
            "decoder": DECODER_IP, "name": "multiviewGone"}).get_json()
        self.assertTrue(body["ok"], body)
        self.assertEqual(device.nodes["hdmi_output"][0]["video"]["input"], "ip_input1")
        self.assertEqual(device.nodes["multiview"], [])
        # Order matters: the output must move first.
        order = [w for w in device.writes if w[0] in ("config_set", "method")]
        self.assertEqual(order[0][1], "hdmi_output")
        self.assertEqual(order[-1][1], "del_multiview")

    def test_a_failed_output_move_aborts_the_delete(self):
        device = self._existing(on_output=True)
        device.swallow["hdmi_output1"] = 99
        body = self.client.post("/api/multiview/delete", json={
            "decoder": DECODER_IP, "name": "multiviewGone"}).get_json()
        self.assertFalse(body["ok"])
        self.assertEqual(len(device.nodes["multiview"]), 1)

    def test_deleting_forgets_the_metadata_but_leaves_encoder_streams(self):
        device = self._existing()
        encoder = self.encoder()
        srv._record_multiview_meta(
            {"ip": DECODER_IP, "mac": "00:00:5E:00:53:10"}, "multiviewGone",
            {"layout": "2x2", "windows": []})
        self.client.post("/api/multiview/delete",
                         json={"decoder": DECODER_IP, "name": "multiviewGone"})
        self.assertEqual(
            srv._multiview_meta_for({"ip": DECODER_IP, "mac": "00:00:5E:00:53:10"}), {})
        # Conservative cleanup: shared encoder resources are not dismantled.
        self.assertEqual([w for w in encoder.writes if w[0] != "config_get"], [])

    def test_deleting_something_that_is_not_there_is_a_404(self):
        self._existing()
        response = self.client.post("/api/multiview/delete", json={
            "decoder": DECODER_IP, "name": "multiviewMissing"})
        self.assertEqual(response.status_code, 404)


# ==========================================================================
# Persistence
# ==========================================================================

class MetadataPersistenceTests(MultiviewTestBase):

    @contextlib.contextmanager
    def meta_file(self, contents):
        """Point the metadata store at a real temporary file."""
        import pathlib
        directory = tempfile.mkdtemp(prefix="omnisuite-meta-")
        path = pathlib.Path(directory) / "multiview_meta.json"
        path.write_text(contents, encoding="utf-8")
        with mock.patch.object(srv, "MULTIVIEW_META", path):
            yield path

    def test_metadata_survives_a_restart(self):
        record = {"layout": "4-split", "friendly_name": "4 Split",
                  "canvas": "3840x2112", "windows": []}
        srv._record_multiview_meta(
            {"ip": DECODER_IP, "mac": "00:00:5E:00:53:10"}, "multiviewSplit", record)
        with srv._multiview_meta_lock:
            saved = json.dumps({"decoders": dict(srv._MULTIVIEW_META)})
            srv._MULTIVIEW_META.clear()
        self.assertEqual(
            srv._multiview_meta_for({"ip": DECODER_IP, "mac": "00:00:5E:00:53:10"}), {})

        with self.meta_file(saved):
            srv._load_multiview_meta()
        restored = srv._multiview_meta_for({"ip": DECODER_IP, "mac": "00:00:5E:00:53:10"})
        self.assertEqual(restored["multiviewSplit"]["layout"], "4-split")

    def test_a_malformed_metadata_file_degrades_to_empty(self):
        with self.meta_file("{not json"):
            srv._load_multiview_meta()      # must not raise
        self.assertEqual(srv._multiview_meta_for({"ip": DECODER_IP}), {})

    def test_hardware_geometry_wins_over_stored_metadata(self):
        # The operator edited the multiview in the device's own web UI. The
        # stored layout name is now a lie, and the page must not repeat it.
        geometry = mv.compute_windows("4-split", 3840, 2160)
        subframes = [{"name": mv.subframe_label(w["cell"], w["width"], w["height"]),
                      "x": w["x"], "y": w["y"], "anchor": w["anchor"],
                      "input": "", "priority": w["priority"],
                      "video": {"input": {"active": False}, "output": {"active": False}}}
                     for w in geometry["windows"]]
        self.decoder(multiviews=[{"name": "multiviewDrifted", "width": 3840,
                                  "height": 2112, "subframes": subframes}])
        srv._record_multiview_meta(
            {"ip": DECODER_IP, "mac": "00:00:5E:00:53:10"}, "multiviewDrifted",
            {"layout": "2x2", "windows": []})
        body = self.client.get(f"/api/multiview/state?ip={DECODER_IP}").get_json()
        view = body["multiviews"][0]
        self.assertEqual(view["layout"], "4-split")
        self.assertEqual(view["layout_source"], "inferred")
        self.assertTrue(view["layout_diverged"])
        self.assertEqual(view["stored_layout"], "2x2")


# ==========================================================================
# Isolation
# ==========================================================================

class HardwareIsolationTests(unittest.TestCase):
    """The fence, asserted rather than assumed."""

    def test_no_bench_address_appears_in_this_suite(self):
        # Comments and docstrings are stripped first. The rule this asserts has
        # to name the address it forbids, and an assertion that reads its own
        # explanation passes with the offending code still present.
        import io, pathlib, tokenize
        source = pathlib.Path(__file__).read_text(encoding="utf-8")
        code = []
        for token in tokenize.generate_tokens(io.StringIO(source).readline):
            if token.type == tokenize.COMMENT:
                continue
            code.append(token.string)
        # Assembled from parts so the needle is not itself a literal in
        # this file, which is the only reason the check can fail loudly.
        bench_prefix = "192.168." + "100."
        self.assertNotIn(bench_prefix, " ".join(code))
        # The RFC 5737 range these tests do use is not routed.
        self.assertIn("192.0.2.", " ".join(code))

    # The transport fence itself is asserted in test_usb_integration, where it is
    # defined. Importing that module from here re-executed it -- run_tests loads
    # each suite without registering it in sys.modules -- which rebound the fence
    # to a second copy holding different state, and four of its own tests failed.

    def test_multiview_writes_reach_the_transport_where_the_fence_lives(self):
        # If a future change gave the Multiview code its own socket, the fence
        # would not see it. Assert both write helpers go through the shared one.
        import inspect
        for helper in (srv._mv_set, srv._mv_method):
            source = inspect.getsource(helper)
            self.assertIn("_ws_send_recv_with_fallback", source)
            self.assertNotIn("create_connection", source)


class StageOrderingTests(unittest.TestCase):
    """Ordering only bites with more than one window, so it is tested there.

    With a single window the natural append order already happens to be correct,
    which made an earlier version of this pass with the sort removed entirely.
    """

    def _plan(self, assignments):
        encoders = {}
        for ip in set(assignments.values()):
            encoders[ip] = {"device": {"ip": ip, "hostname": "enc-" + ip},
                            "vc2": _vc2(), "sessions": _sessions(),
                            "input_resolution": "3840x2160", "reachable": True}
        return mv.plan_multiview(
            {"layout": "2x2", "canvas": "1920x1080", "assignments": assignments,
             "name": "2x2", "select_on_output": True},
            {"ip": DECODER_IP, "multiview": [], "ip_input": _ip_inputs(),
             "hdmi_output": _hdmi_output()[0]},
            encoders)

    def test_every_stage_is_grouped_and_in_the_declared_order(self):
        plan = self._plan({"top_left": ENCODER_IP, "top_right": ENCODER2_IP,
                           "bottom_left": "192.0.2.24"})
        for key in ("mutations", "activation"):
            stages = [m["stage"] for m in plan[key]]
            positions = [mv.STAGE_ORDER.index(stage) for stage in stages]
            self.assertEqual(positions, sorted(positions),
                             "%s ran out of order: %s" % (key, stages))

    def test_no_input_is_enabled_before_its_encoder_is_ready(self):
        # The failure this prevents: window two's encoder is still being
        # re-scaled while window one's input is already live on screen.
        plan = self._plan({"top_left": ENCODER_IP, "top_right": ENCODER2_IP})
        stages = [m["stage"] for m in plan["activation"]]
        last_encoder_stage = max(
            index for index, stage in enumerate(stages)
            if stage in (mv.STAGE_ENCODER_SCALER, mv.STAGE_ENCODER_BITRATE,
                         mv.STAGE_SESSION))
        first_ip_input = min(index for index, stage in enumerate(stages)
                             if stage == mv.STAGE_IP_INPUT)
        self.assertLess(last_encoder_stage, first_ip_input)

    def test_the_object_is_saved_before_any_resource_is_prepared(self):
        # The two sets are executed by different operations -- Save writes the
        # object, Recall prepares the resources -- so the guarantee is that
        # neither set reaches into the other's territory.
        plan = self._plan({"top_left": ENCODER_IP, "top_right": ENCODER2_IP})
        self.assertEqual({m["stage"] for m in plan["mutations"]},
                         {mv.STAGE_MULTIVIEW})
        self.assertNotIn(mv.STAGE_MULTIVIEW,
                         {m["stage"] for m in plan["activation"]})


class CanvasRoundingDirectionTests(unittest.TestCase):
    """Both rounding directions, pinned independently.

    At 3840 every layout granularity divides evenly, so the width rule's
    direction is unobservable on the exposed canvases. It is pinned here through
    the internal 2560 path, which does need rounding, so the rule cannot be
    changed silently before that canvas is enabled.
    """

    def test_height_rounds_down_at_2160_and_up_below_it(self):
        self.assertEqual(mv.compute_canvas("4-split", 3840, 2160)[1], 2112)
        self.assertEqual(mv.compute_canvas("4-split", 1920, 1080)[1], 1088)

    def test_width_rounds_up_below_3840(self):
        # 2560 does not divide by the 1+3-horizontal granularity (6 x 32 = 192),
        # so this is where the width rule is observable at all.
        self.assertEqual(mv.compute_canvas("1+3-horizontal-top", 2560, 1440)[0], 2688)
        self.assertEqual(mv.compute_canvas("pip-top-left", 2560, 1440)[0], 2592)

    def test_width_rounds_down_at_3840_so_a_4k_panel_is_never_exceeded(self):
        for name in mv.LAYOUT_ORDER:
            width, _height = mv.compute_canvas(name, 3840, 2160)
            self.assertLessEqual(width, 3840, name)

    def test_the_rounding_rule_itself_rounds_down_only_at_the_cap(self):
        # 224 is chosen because it does NOT divide 3840; every real layout
        # granularity does, which is exactly why this has to be asserted against
        # the rule rather than through a layout.
        self.assertEqual(mv.snap_dimension(3840, 224, 3840), 3808)
        self.assertEqual(mv.snap_dimension(3776, 224, 3840), 3808)
        self.assertEqual(mv.snap_dimension(2160, 64, 2160), 2112)
        self.assertEqual(mv.snap_dimension(1080, 64, 2160), 1088)
        # At the cap it must never round up past it.
        for multiple in (64, 96, 128, 192, 224, 256):
            self.assertLessEqual(mv.snap_dimension(3840, multiple, 3840), 3840)


class SubframePruneTests(MultiviewTestBase):
    """Changing the layout of an existing Multiview must remove the old windows.

    `config_set` merges by subframe name. Without an explicit removal the
    previous layout's windows stay on the canvas, and because the device caps a
    Multiview at four subframes, adding four new names on top of four old ones is
    refused outright.
    """

    def _with_2x2(self):
        geometry = mv.compute_windows("2x2", 1920, 1080)
        subframes = [{"name": mv.subframe_label(w["cell"], w["width"], w["height"]),
                      "x": w["x"], "y": w["y"], "anchor": w["anchor"],
                      "input": "ip_input2" if w["cell"] == "top_left" else "",
                      "priority": w["priority"],
                      "video": {"input": {"active": False}, "output": {"active": False}}}
                     for w in geometry["windows"]]
        inputs = _ip_inputs()
        inputs[1].update({"enabled": True, "multicast": {"address": S2_VIDEO},
                          "port": 1000})
        hdmi = _hdmi_output()
        hdmi[0]["video"]["input"] = "multiviewEdit"
        hdmi[0]["sap_input"]["enabled"] = False
        return self.decoder(
            multiviews=[{"name": "multiviewEdit", "width": 1920, "height": 1088,
                         "slice_info": {"width": {"min": 32}, "height": {"min": 8}},
                         "subframes": subframes}],
            ip_inputs=inputs, hdmi=hdmi)

    def _switch_to_side_by_side(self):
        return self.client.post("/api/multiview/apply", json={
            "decoder": DECODER_IP, "layout": "side-by-side", "canvas": "1920x1080",
            "object_name": "multiviewEdit", "update_existing": True,
            "assignments": {"left": ENCODER_IP}, "select_on_output": True})

    def test_the_plan_removes_every_window_the_new_layout_does_not_have(self):
        self._with_2x2()
        self.encoder()
        body = self.client.post("/api/multiview/plan", json={
            "decoder": DECODER_IP, "layout": "side-by-side", "canvas": "1920x1080",
            "object_name": "multiviewEdit", "update_existing": True,
            "assignments": {"left": ENCODER_IP}}).get_json()
        prunes = [m for m in body["plan"]["mutations"]
                  if m["stage"] == mv.STAGE_MULTIVIEW_PRUNE]
        self.assertEqual(len(prunes), 4, [m["description"] for m in prunes])
        self.assertTrue(all(m["method"] == "del_multiview_subframe" for m in prunes))

    def test_pruning_happens_before_the_new_subframes_are_written(self):
        self._with_2x2()
        self.encoder()
        body = self.client.post("/api/multiview/plan", json={
            "decoder": DECODER_IP, "layout": "side-by-side", "canvas": "1920x1080",
            "object_name": "multiviewEdit", "update_existing": True,
            "assignments": {"left": ENCODER_IP}}).get_json()
        stages = [m["stage"] for m in body["plan"]["mutations"]]
        self.assertLess(max(i for i, s in enumerate(stages)
                            if s == mv.STAGE_MULTIVIEW_PRUNE),
                        stages.index(mv.STAGE_MULTIVIEW))

    def test_after_the_switch_only_the_new_layouts_windows_remain(self):
        decoder = self._with_2x2()
        self.encoder()
        body = self._switch_to_side_by_side().get_json()
        self.assertTrue(body["ok"], body)
        self.assertEqual(body["status"], "VERIFIED")
        stored = next(o for o in decoder.nodes["multiview"]
                      if o["name"] == "multiviewEdit")
        names = sorted(s["name"] for s in stored["subframes"])
        self.assertEqual(names, ["left (960x544)", "right (960x544)"])
        # The device's own limit, which a merge without pruning would breach.
        self.assertLessEqual(len(stored["subframes"]), mv.MAX_SUBFRAMES)

    def test_a_layout_that_keeps_its_windows_prunes_nothing(self):
        self._with_2x2()
        self.encoder()
        body = self.client.post("/api/multiview/plan", json={
            "decoder": DECODER_IP, "layout": "2x2", "canvas": "1920x1080",
            "object_name": "multiviewEdit", "update_existing": True,
            "assignments": {"top_left": ENCODER_IP}}).get_json()
        self.assertEqual([m for m in body["plan"]["mutations"]
                          if m["stage"] == mv.STAGE_MULTIVIEW_PRUNE], [])

    def test_a_failed_switch_puts_the_removed_windows_back(self):
        decoder = self._with_2x2()
        self.encoder()
        before = sorted(s["name"] for s in decoder.nodes["multiview"][0]["subframes"])
        # Fail once, after the prune has already removed the old windows. One
        # write is swallowed, not all of them: a device that keeps ignoring
        # writes cannot be rolled back either, and that is reported separately
        # (see the ROLLBACK INCOMPLETE case).
        decoder.swallow["multiviewEdit"] = 1
        body = self._switch_to_side_by_side().get_json()
        self.assertFalse(body["ok"])
        self.assertEqual(body["status"], "FAILED — ROLLED BACK")
        after = sorted(s["name"] for s in decoder.nodes["multiview"][0]["subframes"])
        self.assertEqual(after, before, "the removed windows were not restored")

    def test_a_pruned_subframe_is_verified_by_absence(self):
        # If the device accepted the removal and ignored it, the transaction has
        # to notice: there are no fields left to compare, only absence.
        decoder = self._with_2x2()
        self.encoder()
        original = decoder._method

        def ignore_removals(request):
            if "del_multiview_subframe" in request:
                decoder.writes.append(("method", "del_multiview_subframe",
                                       dict(request["del_multiview_subframe"])))
                return {"error": False}          # accepted, and quietly ignored
            return original(request)

        decoder._method = ignore_removals
        body = self._switch_to_side_by_side().get_json()
        self.assertFalse(body["ok"])
        self.assertTrue(any("still present" in f["error"] for f in body["failures"]),
                        body["failures"])


class ScanPathTests(unittest.TestCase):
    """Multiview must stay off the critical discovery path.

    Discovery is performance-sensitive and shared by every page. A capability
    probe per decoder per scan would be a request per device that almost no scan
    needs, so the rule is that the scan asks for nothing Multiview-related and
    the page asks lazily.
    """

    def _scan_source(self):
        import inspect
        return inspect.getsource(srv.api_scan)

    def test_the_scan_endpoint_requests_no_multiview_node(self):
        source = self._scan_source()
        for name in ("multiview", "_multiview_capability", "vc2", "del_multiview"):
            self.assertNotIn(name, source,
                             f"the scan path references {name!r}")

    def test_the_scan_endpoint_does_not_reach_the_multiview_helpers(self):
        import inspect
        source = self._scan_source()
        for helper in ("_mv_get", "_mv_set", "_mv_method", "_decoder_state",
                       "_gather_encoder_states"):
            self.assertNotIn(helper, source, f"the scan path calls {helper}")

    def test_multiview_helpers_are_only_reachable_from_multiview_endpoints(self):
        import inspect
        # Every caller of the capability probe must be a Multiview endpoint.
        module = inspect.getsource(srv)
        callers = [line.strip() for line in module.splitlines()
                   if "_multiview_capability(" in line
                   and not line.strip().startswith("def ")]
        self.assertTrue(callers)
        self.assertLessEqual(len(callers), 3, callers)


class CapabilityCadenceTests(MultiviewTestBase):

    def test_repeatedly_opening_the_page_costs_the_devices_nothing(self):
        device = self.decoder()
        reads = []
        original = device.handle

        def counting(payload):
            if "config_get" in payload:
                reads.append(payload["config_get"])
            return original(payload)

        device.handle = counting
        for _ in range(5):
            self.client.get("/api/multiview/decoders?probe=1")
        # One probe for the one decoder in the fixture, however many times the
        # page is opened.
        self.assertEqual(reads, ["multiview"])

    def test_the_layout_catalog_reaches_no_device(self):
        device = self.decoder()
        encoder = self.encoder()
        for _ in range(3):
            self.client.get("/api/multiview/layouts")
        self.assertEqual(device.writes, [])
        self.assertEqual(encoder.writes, [])


class PackagingTests(unittest.TestCase):
    """The new module has to reach the built executable.

    PyInstaller follows a plain top-level import, which is how
    `omni_matrix_logic` and `omni_usb_extender` are already collected -- neither
    is a hidden import. A lazy or conditional import here would build cleanly and
    fail only when someone opened the packaged app.
    """

    def test_the_server_imports_the_multiview_module_at_top_level(self):
        import ast
        import pathlib
        source = pathlib.Path("OmniMatrix_upgrade_server_v7_6y.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        top_level = [node for node in tree.body if isinstance(node, (ast.Import, ast.ImportFrom))]
        imported = set()
        for node in top_level:
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif node.module:
                imported.add(node.module)
        self.assertIn("omni_multiview", imported)
        # The precedent this relies on.
        self.assertIn("omni_usb_extender", imported)

    def test_the_page_assets_live_under_ui_which_is_packaged_wholesale(self):
        import pathlib
        for asset in ("ui/matrix/multiview.html", "ui/matrix/multiview.js",
                      "ui/matrix/multiview.css", "ui/confirm.js"):
            self.assertTrue(pathlib.Path(asset).exists(), asset)

    def test_the_page_references_only_assets_that_exist(self):
        import pathlib
        import re
        html = pathlib.Path("ui/matrix/multiview.html").read_text(encoding="utf-8")
        for match in re.finditer(r'(?:src|href)="/ui/([^"?]+)', html):
            asset = pathlib.Path("ui") / match.group(1)
            self.assertTrue(asset.exists(), f"{asset} is referenced but missing")


class MultipleMultiviewTests(MultiviewTestBase):
    """A decoder holds many saved Multiviews. Only one is ever on the output.

    This replaces the Phase 5 one-per-canvas rule outright. A saved Multiview is
    a description, so two of them wanting the same Encoder 2 at different sizes
    is ordinary rather than a conflict -- the difference is resolved at recall,
    against the state that exists then.
    """

    LAYOUTS = [("Quad Unique", "2x2",
                {"top_left": ENCODER_IP, "top_right": ENCODER2_IP,
                 "bottom_left": NEW_WALLPLATE_IP, "bottom_right": PLAIN_111_IP}),
               ("Quad Duplicate", "2x2",
                {"top_left": ENCODER_IP, "top_right": ENCODER_IP,
                 "bottom_left": ENCODER2_IP, "bottom_right": NEW_WALLPLATE_IP}),
               ("Side by Side", "side-by-side",
                {"left": NEW_WALLPLATE_IP, "right": ENCODER_IP}),
               ("Presenter PiP", "pip-bottom-right",
                {"main": ENCODER2_IP, "bottom_right": NEW_WALLPLATE_IP})]

    def _sources(self):
        for ip in (ENCODER_IP, ENCODER2_IP, NEW_WALLPLATE_IP, PLAIN_111_IP):
            self.encoder(ip)

    def _save_all(self):
        self.decoder()
        self._sources()
        names = []
        for friendly, layout, assignments in self.LAYOUTS:
            body = self.save(layout=layout, name=friendly,
                             assignments=assignments).get_json()
            self.assertTrue(body["ok"], (friendly, body))
            names.append(body["plan"]["object_name"])
        return names

    def _state(self):
        return self.client.get(f"/api/multiview/state?ip={DECODER_IP}").get_json()

    def test_four_multiviews_coexist_on_one_decoder(self):
        names = self._save_all()
        self.assertEqual(len(set(names)), 4)
        on_device = sorted(o["name"] for o in self.devices[DECODER_IP].nodes["multiview"])
        self.assertEqual(on_device, sorted(names))

    def test_saving_the_fourth_does_not_disturb_the_first_three(self):
        self.decoder()
        self._sources()
        first = self.save(layout="2x2", name="Quad Unique",
                          assignments=self.LAYOUTS[0][2]).get_json()["plan"]
        before = copy.deepcopy(next(
            o for o in self.devices[DECODER_IP].nodes["multiview"]
            if o["name"] == first["object_name"]))
        for friendly, layout, assignments in self.LAYOUTS[1:]:
            self.assertTrue(self.save(layout=layout, name=friendly,
                                      assignments=assignments).get_json()["ok"])
        after = next(o for o in self.devices[DECODER_IP].nodes["multiview"]
                     if o["name"] == first["object_name"])
        self.assertEqual(after, before, "an unrelated Multiview was rewritten")

    def test_new_remains_available_however_many_exist(self):
        self._save_all()
        body = self._state()
        self.assertTrue(body["can_create"],
                        "New Multiview was withdrawn because others exist")
        self.assertTrue(body["canvases"][0]["available"])

    def test_the_dropdown_lists_every_saved_multiview(self):
        names = self._save_all()
        body = self._state()
        self.assertEqual(sorted(v["name"] for v in body["multiviews"]), sorted(names))
        for view in body["multiviews"]:
            self.assertTrue(view["layout"], view["name"])
            self.assertTrue(view["layout_label"], view["name"])

    def test_an_inactive_saved_multiview_reserves_no_live_input(self):
        # The Phase 5 gate refused a pool input another Multiview referenced.
        # With many saved objects that would let the first one lock the pool.
        names = self._save_all()
        decoder = self.devices[DECODER_IP]
        enabled = [i["name"] for i in decoder.nodes["ip_input"] if i["enabled"]]
        self.assertEqual(enabled, ["ip_input1", "ip_input3"],
                         "saving enabled a decoder input")
        # And every one of them can still be recalled.
        for name in names:
            self.offer(name)
            body = self.recall(name).get_json()
            self.assertTrue(body["ok"], (name, body))

    def test_saving_one_does_not_disturb_the_active_display(self):
        names = self._save_all()
        self.offer(names[0])
        self.assertTrue(self.recall(names[0]).get_json()["ok"])
        decoder = self.devices[DECODER_IP]
        before = copy.deepcopy(decoder.nodes["hdmi_output"][0])
        self.assertTrue(self.save(layout="4-split", name="Another",
                                  assignments={"main": ENCODER_IP}).get_json()["ok"])
        self.assertEqual(decoder.nodes["hdmi_output"][0], before,
                         "saving a new Multiview changed the display")

    def test_editing_one_changes_only_that_object(self):
        names = self._save_all()
        decoder = self.devices[DECODER_IP]
        others = {o["name"]: copy.deepcopy(o) for o in decoder.nodes["multiview"]
                  if o["name"] != names[0]}
        body = self.save(layout="side-by-side", name="Quad Unique",
                         assignments={"left": ENCODER_IP, "right": ENCODER2_IP},
                         object_name=names[0], update_existing=True).get_json()
        self.assertTrue(body["ok"], body)
        for name, before in others.items():
            after = next(o for o in decoder.nodes["multiview"] if o["name"] == name)
            self.assertEqual(after, before, "%s was rebuilt by an unrelated edit" % name)
        edited = next(o for o in decoder.nodes["multiview"] if o["name"] == names[0])
        self.assertEqual(sorted(s["name"] for s in edited["subframes"]),
                         ["left (960x544)", "right (960x544)"])

    def test_deleting_one_leaves_the_others_intact(self):
        names = self._save_all()
        decoder = self.devices[DECODER_IP]
        body = self.client.post("/api/multiview/delete", json={
            "decoder": DECODER_IP, "name": names[1]}).get_json()
        self.assertTrue(body["ok"], body)
        self.assertFalse(body["was_active"])
        self.assertEqual(sorted(o["name"] for o in decoder.nodes["multiview"]),
                         sorted(n for n in names if n != names[1]))

    def test_deleting_an_inactive_one_releases_nothing(self):
        names = self._save_all()
        self.offer(names[0])
        self.assertTrue(self.recall(names[0]).get_json()["ok"])
        decoder = self.devices[DECODER_IP]
        before = [i["name"] for i in decoder.nodes["ip_input"] if i["enabled"]]
        display = copy.deepcopy(decoder.nodes["hdmi_output"][0])
        body = self.client.post("/api/multiview/delete", json={
            "decoder": DECODER_IP, "name": names[2]}).get_json()
        self.assertTrue(body["ok"], body)
        self.assertTrue(body["reclamation"]["skipped"])
        self.assertEqual([i["name"] for i in decoder.nodes["ip_input"] if i["enabled"]],
                         before, "deleting an inactive Multiview released an input")
        self.assertEqual(decoder.nodes["hdmi_output"][0], display,
                         "deleting an inactive Multiview disturbed the display")

    def test_deleting_the_active_one_moves_the_output_first_then_releases(self):
        names = self._save_all()
        self.offer(names[0])
        self.assertTrue(self.recall(names[0]).get_json()["ok"])
        decoder = self.devices[DECODER_IP]
        self.assertEqual(decoder.nodes["hdmi_output"][0]["video"]["input"], names[0])
        body = self.client.post("/api/multiview/delete", json={
            "decoder": DECODER_IP, "name": names[0]}).get_json()
        self.assertTrue(body["ok"], body)
        self.assertTrue(body["was_active"])
        self.assertNotEqual(decoder.nodes["hdmi_output"][0]["video"]["input"], names[0])
        self.assertTrue(all(r["verified"] for r in body["reclamation"]["reclaimed"]),
                        body["reclamation"])
        pool = [i["name"] for i in decoder.nodes["ip_input"]
                if i["enabled"] and i["name"] in mv.WINDOW_IP_INPUTS]
        self.assertEqual(pool, [], "pool inputs were left enabled after the active "
                                   "Multiview was deleted")

    def test_an_external_multiview_is_neither_listed_as_ours_nor_overwritten(self):
        external = {"name": "multiviewSomeoneElse", "width": 3840, "height": 2160,
                    "subframes": [{"name": "subframe1", "x": 0, "y": 0,
                                   "anchor": "top left", "input": "ip_input1",
                                   "priority": 1,
                                   "video": {"input": {"active": False},
                                             "output": {"active": False}}}]}
        decoder = self.decoder(multiviews=[external])
        self._sources()
        self.assertTrue(self.save().get_json()["ok"])
        still = next(o for o in decoder.nodes["multiview"]
                     if o["name"] == "multiviewSomeoneElse")
        self.assertEqual(still, external)
        body = self._state()
        view = next(v for v in body["multiviews"] if v["name"] == "multiviewSomeoneElse")
        self.assertIsNone(view["layout"])
        self.assertEqual(view["layout_source"], "custom")

    def test_stale_metadata_for_a_removed_object_is_not_listed(self):
        srv._record_multiview_meta(
            {"ip": DECODER_IP, "mac": "00:00:5E:00:53:10"}, "multiviewGoneAway",
            {"layout": "2x2", "friendly_name": "gone", "windows": []})
        self.decoder(multiviews=[])
        self.assertEqual(self._state()["multiviews"], [])


class CanvasAvailabilityUnitTests(unittest.TestCase):

    def test_availability_lists_only_exposed_canvases(self):
        entries = mv.canvas_availability([])
        self.assertEqual([e["id"] for e in entries], list(mv.EXPOSED_CANVASES))
        self.assertNotIn("2560x1440", [e["id"] for e in entries])

    def test_a_managed_entry_marks_its_canvas_taken(self):
        entries = {e["id"]: e for e in mv.canvas_availability(
            [{"object_name": "multiviewA", "friendly_name": "2x2",
              "canvas": "1920x1080"}])}
        self.assertFalse(entries["1920x1080"]["available"])
        self.assertEqual(entries["1920x1080"]["used_by_label"], "2x2")
        # The one exposed canvas is the whole list, so taking it is the limit.
        self.assertEqual(list(entries), [mv.ACTIVE_CANVAS])

    def test_can_create_is_false_only_when_every_canvas_is_taken(self):
        self.assertTrue(mv.can_create_multiview([]))
        self.assertTrue(mv.can_create_multiview(
            [{"object_name": "a", "canvas": "3840x2160"}]))
        self.assertFalse(mv.can_create_multiview(
            [{"object_name": "a", "canvas": "3840x2160"},
             {"object_name": "b", "canvas": "1920x1080"}]))

    def test_an_unknown_canvas_in_metadata_is_ignored(self):
        self.assertTrue(mv.can_create_multiview(
            [{"object_name": "a", "canvas": "2560x1440"}]))


class ShowOnDisplayTests(MultiviewTestBase):
    """Saving and showing are separate operations.

    Saving configures the Multiview and everything feeding it. Showing is the one
    moment the picture and the sound change, so everything display-side lives
    there: the output resolution, SAP, the selection and the audio.
    """

    def _save(self, **overrides):
        payload = {"decoder": DECODER_IP, "layout": "2x2",
                   "canvas": mv.ACTIVE_CANVAS, "name": "2x2",
                   "assignments": {"top_left": ENCODER_IP}}
        payload.update(overrides)
        return self.client.post("/api/multiview/apply", json=payload)

    def _ready(self, **overrides):
        """Save a Multiview and let the decoder offer it, as the device does."""
        decoder = self.devices.get(DECODER_IP) or self.decoder()
        if ENCODER_IP not in self.devices:
            self.encoder()
        name = self._save(**overrides).get_json()["plan"]["object_name"]
        decoder.nodes["hdmi_output"][0]["video"]["available_inputs"].append(name)
        return decoder, name

    def _show(self, name):
        return self.client.post("/api/multiview/show", json={
            "decoder": DECODER_IP, "name": name}).get_json()

    def test_saving_does_not_change_what_is_on_the_display(self):
        decoder = self.decoder()
        self.encoder()
        body = self._save().get_json()
        self.assertTrue(body["ok"], body)
        output = decoder.nodes["hdmi_output"][0]
        self.assertEqual(output["video"]["input"], "ip_input1")
        self.assertEqual(output["video"]["output"]["resolution"], "3840x2160")
        self.assertEqual(output["audio"]["input"], "ip_input3")
        self.assertTrue(output["sap_input"]["enabled"],
                        "saving must not disturb automatic source selection")
        # And the plan said so, rather than doing it quietly.
        self.assertEqual([m for m in body["plan"]["mutations"]
                          if m["node"] == "hdmi_output"], [])

    def test_showing_sets_the_output_to_1080p_disables_sap_and_selects(self):
        decoder, name = self._ready()
        body = self._show(name)
        self.assertTrue(body["ok"], body)
        self.assertEqual(body["status"], "VERIFIED")
        self.assertTrue(all(step["verified"] for step in body["steps"]))
        output = decoder.nodes["hdmi_output"][0]
        self.assertEqual(output["video"]["input"], name)
        self.assertEqual(output["video"]["output"]["resolution"], "1920x1080")
        self.assertFalse(output["sap_input"]["enabled"])
        self.assertEqual(body["output_resolution"], "1920x1080")

    def test_the_resolution_and_sap_are_settled_before_the_selection(self):
        decoder, name = self._ready()
        self._show(name)
        writes = [w for w in decoder.writes if w[0] == "config_set"
                  and w[1] == "hdmi_output"]
        stages = [list(w[2][0].keys()) for w in writes]
        order = [f for entry in stages for f in entry if f != "name"]
        # First write carries the output resolution, SAP comes down before the
        # selection, and the selection is last.
        self.assertEqual(order[0], "video")
        self.assertIn("sap_input", order)
        self.assertLess(order.index("sap_input"), len(order) - 1)
        self.assertEqual(order[-1], "video")

    def test_an_output_already_at_the_right_resolution_is_not_rewritten(self):
        self.decoder(hdmi=_hdmi_output(resolution="1920x1080"))
        self.encoder()
        _decoder, name = self._ready()
        resolution_writes = [
            s for s in self._show(name)["steps"]
            if s["step"].startswith("Set the display output")]
        self.assertEqual(resolution_writes, [])

    def test_showing_something_already_shown_changes_nothing(self):
        decoder, name = self._ready()
        self._show(name)
        before = len(decoder.writes)
        body = self._show(name)
        self.assertTrue(body["ok"], body)
        self.assertTrue(body["already_shown"])
        self.assertEqual([w for w in decoder.writes[before:] if w[0] != "config_get"], [])

    def test_a_multiview_the_decoder_does_not_offer_yet_is_refused(self):
        decoder = self.decoder(multiviews=[
            {"name": "multiviewNotReady", "width": 1920, "height": 1080,
             "subframes": []}])
        response = self.client.post("/api/multiview/show", json={
            "decoder": DECODER_IP, "name": "multiviewNotReady"})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(decoder.nodes["hdmi_output"][0]["video"]["input"], "ip_input1")

    def test_showing_an_unknown_name_is_a_404(self):
        self.decoder()
        response = self.client.post("/api/multiview/show", json={
            "decoder": DECODER_IP, "name": "multiviewNope"})
        self.assertEqual(response.status_code, 404)

    def test_state_reports_which_multiview_is_shown(self):
        _decoder, name = self._ready()
        body = self.client.get(f"/api/multiview/state?ip={DECODER_IP}").get_json()
        self.assertFalse(body["multiviews"][0]["selected_on_output"])
        self._show(name)
        body = self.client.get(f"/api/multiview/state?ip={DECODER_IP}").get_json()
        self.assertTrue(body["multiviews"][0]["selected_on_output"])

    # --- rollback -----------------------------------------------------------

    def test_a_refused_write_fails_and_rolls_everything_back(self):
        decoder, name = self._ready()
        before = copy.deepcopy(decoder.nodes["hdmi_output"][0])
        decoder.refuse["hdmi_output"] = "device busy"
        body = self._show(name)
        decoder.refuse.pop("hdmi_output", None)
        self.assertFalse(body["ok"])
        self.assertIn("FAILED", body["status"])
        self.assertEqual(decoder.nodes["hdmi_output"][0]["video"]["output"]["resolution"],
                         before["video"]["output"]["resolution"])
        self.assertEqual(decoder.nodes["hdmi_output"][0]["video"]["input"], "ip_input1")

    def test_a_silently_ignored_write_is_caught_by_readback(self):
        # The decoder is known to accept a write and not hold it.
        decoder, name = self._ready()
        decoder.swallow["hdmi_output1"] = 1
        body = self._show(name)
        self.assertFalse(body["ok"])
        self.assertTrue(any("does not hold it" in (s.get("error") or "")
                            or s.get("error") for s in body["steps"]), body["steps"])

    def test_a_later_failure_restores_the_resolution_and_sap(self):
        decoder, name = self._ready()
        original = copy.deepcopy(decoder.nodes["hdmi_output"][0])
        seen = {"n": 0}
        real = decoder._set

        def fail_on_the_selection(request):
            seen["n"] += 1
            if seen["n"] == 3:          # resolution, sap, then the selection
                decoder.writes.append(("config_set", request.get("name"),
                                       request.get("config")))
                return {"error": False}          # accepted and ignored
            return real(request)

        decoder._set = fail_on_the_selection
        body = self._show(name)
        decoder._set = real
        self.assertFalse(body["ok"])
        self.assertEqual(body["status"], "FAILED — ROLLED BACK")
        after = decoder.nodes["hdmi_output"][0]
        self.assertEqual(after["video"]["output"]["resolution"],
                         original["video"]["output"]["resolution"],
                         "the previous output resolution was not restored")
        self.assertTrue(after["sap_input"]["enabled"], "SAP was not restored")
        self.assertEqual(after["video"]["input"], "ip_input1")

    def test_showing_reports_the_negotiated_resolution_separately(self):
        # The setting is ours; what the sink negotiates depends on its EDID and
        # is reported, never used as a pass condition.
        _decoder, name = self._ready()
        body = self._show(name)
        self.assertIn("negotiated", body)
        self.assertIn("output_resolution", body)

    def test_a_dormant_4k_multiview_cannot_be_shown(self):
        # An object from an earlier release, or from the device's own web UI.
        decoder = self.decoder(multiviews=[
            {"name": "multiviewLegacy4K", "width": 3840, "height": 2112,
             "subframes": []}])
        decoder.nodes["hdmi_output"][0]["video"]["available_inputs"].append(
            "multiviewLegacy4K")
        response = self.client.post("/api/multiview/show", json={
            "decoder": DECODER_IP, "name": "multiviewLegacy4K"})
        self.assertEqual(response.status_code, 409)
        self.assertIn("1920x1080 Multiviews only", response.get_json()["error"])
        self.assertEqual(decoder.nodes["hdmi_output"][0]["video"]["input"], "ip_input1")
        self.assertEqual(
            decoder.nodes["hdmi_output"][0]["video"]["output"]["resolution"],
            "3840x2160")


class MainWindowAudioTests(MultiviewTestBase):
    """Multiview carries no audio, so the display follows the main window."""

    def _ready(self, layout="2x2", assignments=None):
        decoder = self.decoder()
        self.encoder(ENCODER_IP)
        self.encoder(ENCODER2_IP)
        cells = [c[0] for c in mv.LAYOUTS[layout]["cells"]]
        assignments = assignments or {cells[0]: ENCODER_IP}
        name = self.client.post("/api/multiview/apply", json={
            "decoder": DECODER_IP, "layout": layout, "canvas": mv.ACTIVE_CANVAS,
            "name": layout, "assignments": assignments,
        }).get_json()["plan"]["object_name"]
        decoder.nodes["hdmi_output"][0]["video"]["available_inputs"].append(name)
        return decoder, name

    def _show(self, name):
        return self.client.post("/api/multiview/show", json={
            "decoder": DECODER_IP, "name": name}).get_json()

    def test_the_main_window_of_an_equal_layout_is_window_one(self):
        self.assertEqual(mv.main_window_cell("2x2"), "top_left")
        self.assertEqual(mv.main_window_cell("side-by-side"), "left")

    def test_the_main_window_of_every_other_layout_is_the_large_one(self):
        for layout in mv.LAYOUT_ORDER:
            if layout in ("2x2", "side-by-side"):
                continue
            self.assertEqual(mv.main_window_cell(layout), "main", layout)

    def test_the_main_window_is_the_largest_window_of_its_layout(self):
        for layout in mv.LAYOUT_ORDER:
            windows = mv.compute_windows(layout, 1920, 1080)["windows"]
            main = next(w for w in windows
                        if w["cell"] == mv.main_window_cell(layout))
            self.assertEqual(main["width"] * main["height"],
                             max(w["width"] * w["height"] for w in windows), layout)

    def test_audio_follows_the_main_window_over_session_one(self):
        decoder, name = self._ready()
        body = self._show(name)
        self.assertTrue(body["ok"], body)
        self.assertTrue(body["audio"]["followed"], body["audio"])
        self.assertEqual(body["audio"]["session"], "session1")
        self.assertEqual(body["audio"]["source_ip"], ENCODER_IP)
        # The decoder's audio input now carries that source's Session 1 audio.
        audio_input = decoder.nodes["hdmi_output"][0]["audio"]["input"]
        entry = next(i for i in decoder.nodes["ip_input"] if i["name"] == audio_input)
        self.assertEqual(entry["multicast"]["address"], S1_AUDIO)
        self.assertTrue(entry["enabled"])

    def test_no_second_audio_stream_is_created_on_session_two(self):
        _decoder, name = self._ready()
        self._show(name)
        encoder = self.devices[ENCODER_IP]
        session2 = next(s for s in encoder.nodes["sessions"] if s["name"] == "session2")
        self.assertFalse(session2["audio"]["stream"]["enabled"])
        self.assertEqual(session2["audio"]["stream"]["destination_address"], "")

    def test_the_audio_input_is_never_taken_from_the_window_pool(self):
        decoder, name = self._ready()
        self._show(name)
        self.assertNotIn(decoder.nodes["hdmi_output"][0]["audio"]["input"],
                         mv.WINDOW_IP_INPUTS)

    def test_an_input_already_carrying_the_audio_is_used_as_it_stands(self):
        decoder, name = self._ready()
        # ip_input3 already carries ENCODER_IP's Session 1 audio in the fixture.
        before = copy.deepcopy(
            next(i for i in decoder.nodes["ip_input"] if i["name"] == "ip_input3"))
        body = self._show(name)
        self.assertTrue(body["ok"], body)
        self.assertFalse(body["audio"]["write_ip_input"])
        after = next(i for i in decoder.nodes["ip_input"] if i["name"] == "ip_input3")
        self.assertEqual(after, before)

    def test_a_different_main_source_repoints_the_display_audio(self):
        decoder, name = self._ready(assignments={"top_left": ENCODER2_IP})
        body = self._show(name)
        self.assertTrue(body["ok"], body)
        self.assertEqual(body["audio"]["source_ip"], ENCODER2_IP)
        audio_input = decoder.nodes["hdmi_output"][0]["audio"]["input"]
        entry = next(i for i in decoder.nodes["ip_input"] if i["name"] == audio_input)
        self.assertEqual(entry["multicast"]["address"], "233.252.0.171")

    def test_the_aux_input_is_never_repointed_for_audio(self):
        decoder, name = self._ready(assignments={"top_left": ENCODER2_IP})
        decoder.nodes["hdmi_output"][0]["audio"]["input"] = "ip_input5"   # also aux
        body = self._show(name)
        self.assertTrue(body["ok"], body)
        self.assertFalse(body["audio"]["followed"])
        self.assertIn("something else as well", body["audio"]["reason"])
        entry = next(i for i in decoder.nodes["ip_input"] if i["name"] == "ip_input5")
        self.assertEqual(entry["multicast"]["address"], "")

    def test_audio_that_cannot_be_placed_is_reported_not_guessed(self):
        decoder, name = self._ready(assignments={"top_left": ENCODER2_IP})
        decoder.nodes["hdmi_output"][0]["audio"]["input"] = "generator"
        body = self._show(name)
        self.assertTrue(body["ok"], body)
        self.assertFalse(body["audio"]["followed"])
        self.assertIn("generator", body["audio"]["reason"])
        self.assertEqual(decoder.nodes["hdmi_output"][0]["audio"]["input"], "generator")

    def test_a_source_not_transmitting_session_one_audio_is_reported(self):
        decoder, name = self._ready()
        encoder = self.devices[ENCODER_IP]
        session1 = next(s for s in encoder.nodes["sessions"] if s["name"] == "session1")
        session1["audio"]["stream"]["enabled"] = False
        body = self._show(name)
        self.assertTrue(body["ok"], body)
        self.assertFalse(body["audio"]["followed"])
        self.assertIn("not being transmitted", body["audio"]["reason"])

    def test_a_failed_show_puts_the_audio_input_back(self):
        decoder, name = self._ready(assignments={"top_left": ENCODER2_IP})
        before = copy.deepcopy(decoder.nodes["hdmi_output"][0]["audio"])
        before_input = copy.deepcopy(
            next(i for i in decoder.nodes["ip_input"] if i["name"] == "ip_input3"))
        seen = {"n": 0}
        real = decoder._set

        def fail_at_the_end(request):
            seen["n"] += 1
            # resolution, SAP, the audio input, then the selection. The audio
            # *selection* is not a fifth write: ip_input3 is already the chosen
            # audio input, so only what it carries had to change.
            if seen["n"] == 4:
                decoder.writes.append(("config_set", request.get("name"),
                                       request.get("config")))
                return {"error": False}
            return real(request)

        decoder._set = fail_at_the_end
        body = self._show(name)
        decoder._set = real
        self.assertFalse(body["ok"], body)
        self.assertEqual(decoder.nodes["hdmi_output"][0]["audio"], before)
        self.assertEqual(
            next(i for i in decoder.nodes["ip_input"] if i["name"] == "ip_input3"),
            before_input)


class DecoderInterlockTests(MultiviewTestBase):
    """Video Wall and Fast Switching. Neither is ever turned off automatically."""

    def _apply(self):
        return self.client.post("/api/multiview/apply", json={
            "decoder": DECODER_IP, "layout": "2x2", "canvas": mv.ACTIVE_CANVAS,
            "name": "2x2", "assignments": {"top_left": ENCODER_IP}})

    # --- video wall ---------------------------------------------------------

    def test_video_wall_blocks_saving_and_writes_nothing(self):
        decoder = self.decoder(hdmi=_hdmi_output(wall=True))
        encoder = self.encoder()
        response = self._apply()
        self.assertEqual(response.status_code, 400)
        body = response.get_json()
        self.assertIn("Video Wall", body["error"])
        self.assertEqual(body["plan"]["mutations"], [])
        self.assertEqual(decoder.nodes["multiview"], [])
        self.assertEqual([w for w in encoder.writes if w[0] != "config_get"], [])

    def test_video_wall_blocks_showing_and_writes_nothing(self):
        decoder = self.decoder(multiviews=[
            {"name": "multiviewHD", "width": 1920, "height": 1088, "subframes": []}],
            hdmi=_hdmi_output(wall=True))
        decoder.nodes["hdmi_output"][0]["video"]["available_inputs"].append("multiviewHD")
        before = len(decoder.writes)
        response = self.client.post("/api/multiview/show", json={
            "decoder": DECODER_IP, "name": "multiviewHD"})
        self.assertEqual(response.status_code, 409)
        self.assertIn("Video Wall", response.get_json()["error"])
        self.assertEqual([w for w in decoder.writes[before:] if w[0] != "config_get"], [])

    def test_video_wall_is_never_disabled_automatically(self):
        decoder = self.decoder(hdmi=_hdmi_output(wall=True))
        self.encoder()
        self._apply()
        self.assertTrue(
            decoder.nodes["hdmi_output"][0]["video"]["output"]["wall"]["enabled"])

    def test_the_page_is_told_before_it_offers_anything(self):
        self.decoder(hdmi=_hdmi_output(wall=True))
        body = self.client.get(f"/api/multiview/state?ip={DECODER_IP}").get_json()
        self.assertTrue(body["interlocks"])
        self.assertEqual(body["interlocks"][0]["interlock"], "video_wall")
        self.assertTrue(body["hdmi_output"]["video_wall"])

    # --- fast switching -----------------------------------------------------

    def test_a_4xxx_decoder_is_not_blocked_by_fast_switching(self):
        # Live evidence: the bench D4511 composites a Multiview with Fast
        # Switching on.
        self.decoder(hdmi=_hdmi_output(fast_switching=True))
        self.encoder()
        response = self._apply()
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertTrue(response.get_json()["ok"])

    def test_a_1xx_decoder_is_blocked_by_fast_switching(self):
        # Model-only coverage: no 1xx decoder is on the bench, so this is
        # asserted against the family rule rather than against hardware.
        devices = _devices()
        devices[0]["model"] = "at-omni-121"
        with mock.patch.object(srv, "_load_cache", lambda: devices):
            self.decoder(hdmi=_hdmi_output(fast_switching=True))
            self.encoder()
            response = self._apply()
            self.assertEqual(response.status_code, 400)
            self.assertIn("Fast Switching", response.get_json()["error"])
            self.assertIn("1xx decoder", response.get_json()["error"])

    def test_fast_switching_off_blocks_neither_family(self):
        for model in ("hw-omni-d4511", "at-omni-121"):
            devices = _devices()
            devices[0]["model"] = model
            with mock.patch.object(srv, "_load_cache", lambda d=devices: d):
                self.devices.clear()
                self.decoder(hdmi=_hdmi_output(fast_switching=False))
                self.encoder()
                self.assertEqual(self._apply().status_code, 200, model)

    def test_an_unrecognised_family_fails_closed_while_it_is_on(self):
        devices = _devices()
        devices[0]["model"] = "hw-omni-dXXXX"
        with mock.patch.object(srv, "_load_cache", lambda: devices):
            self.decoder(hdmi=_hdmi_output(fast_switching=True))
            self.encoder()
            response = self._apply()
            self.assertEqual(response.status_code, 400)
            self.assertIn("cannot establish this decoder's family",
                          response.get_json()["error"])

    def test_fast_switching_is_never_disabled_automatically(self):
        self.decoder(hdmi=_hdmi_output(fast_switching=True))
        self.encoder()
        self._apply()
        self.assertTrue(
            self.devices[DECODER_IP].nodes["hdmi_output"][0]["video"]["output"]["fsm"]["enabled"])

    # --- the family rule itself --------------------------------------------

    def test_the_family_comes_from_the_model_and_nothing_else(self):
        self.assertEqual(mv.decoder_family("hw-omni-d4511"), "4xxx")
        self.assertEqual(mv.decoder_family("AT-OMNI-D4511"), "4xxx")
        self.assertEqual(mv.decoder_family("hw-omni-d4111"), "4xxx")
        self.assertEqual(mv.decoder_family("at-omni-121"), "1xx")
        self.assertEqual(mv.decoder_family("AT-OMNI-141"), "1xx")
        self.assertIsNone(mv.decoder_family(""))
        self.assertIsNone(mv.decoder_family("something-else"))
        # Not an address, not a hostname.
        self.assertIsNone(mv.decoder_family("198.51.100.32"))
        self.assertIsNone(mv.decoder_family("hw-omni-d4511-085c6"))


class InputStatusTests(MultiviewTestBase):
    """Every write holding is not the same as a picture existing."""

    def _ready(self, input_active=True):
        decoder = self.decoder(hdmi=_hdmi_output(input_active=input_active))
        self.encoder()
        name = self.client.post("/api/multiview/apply", json={
            "decoder": DECODER_IP, "layout": "2x2", "canvas": mv.ACTIVE_CANVAS,
            "name": "2x2", "assignments": {"top_left": ENCODER_IP},
        }).get_json()["plan"]["object_name"]
        decoder.nodes["hdmi_output"][0]["video"]["available_inputs"].append(name)
        return decoder, name

    def _show(self, name):
        return self.client.post("/api/multiview/show", json={
            "decoder": DECODER_IP, "name": name}).get_json()

    def test_an_active_input_status_is_part_of_a_verified_show(self):
        _decoder, name = self._ready(input_active=True)
        body = self._show(name)
        self.assertTrue(body["ok"], body)
        self.assertTrue(body["input_status"]["active"])
        self.assertEqual(body["input_status"]["label"], "1920x1080")

    def test_no_active_video_fails_the_operation_however_clean_the_writes(self):
        _decoder, name = self._ready(input_active=False)
        with mock.patch.object(srv, "MULTIVIEW_INPUT_SETTLE", 0.0):
            body = self._show(name)
        self.assertFalse(body["ok"], body)
        self.assertIn("NO ACTIVE VIDEO", body["status"])
        self.assertEqual(body["input_status"]["label"], "No active video")
        # Every write it made was accepted and verified: that is the point.
        self.assertTrue(all(step["verified"] for step in body["steps"]), body["steps"])

    def test_no_active_video_captures_the_whole_chain_before_rolling_back(self):
        _decoder, name = self._ready(input_active=False)
        with mock.patch.object(srv, "MULTIVIEW_INPUT_SETTLE", 0.0):
            body = self._show(name)
        diagnostics = body["diagnostics"]
        self.assertEqual(diagnostics["decoder"]["video_input"], name)
        self.assertEqual(diagnostics["decoder"]["output_resolution"], "1920x1080")
        self.assertIn("pool", diagnostics["decoder"])
        self.assertEqual(sorted(diagnostics["decoder"]["pool"]),
                         sorted(mv.WINDOW_IP_INPUTS))
        window = next(w for w in diagnostics["windows"] if w["ip_input"])
        self.assertEqual(window["ip_input"], "ip_input2")
        self.assertTrue(window["ip_input_enabled"])
        self.assertEqual(window["source"]["encoder2_input"], "hdmi_input1")
        self.assertTrue(window["source"]["session2_enabled"])
        self.assertFalse(window["source"]["session2_sap"])

    def test_no_active_video_rolls_the_display_back(self):
        decoder, name = self._ready(input_active=False)
        with mock.patch.object(srv, "MULTIVIEW_INPUT_SETTLE", 0.0):
            body = self._show(name)
        self.assertIn("ROLLED BACK", body["status"])
        output = decoder.nodes["hdmi_output"][0]
        self.assertEqual(output["video"]["input"], "ip_input1")
        self.assertEqual(output["video"]["output"]["resolution"], "3840x2160")
        self.assertTrue(output["sap_input"]["enabled"])

    def test_the_settle_wait_is_bounded_and_only_runs_after_a_show(self):
        import inspect
        source = inspect.getsource(srv._await_input_status)
        self.assertIn("deadline", source)
        self.assertLessEqual(srv.MULTIVIEW_INPUT_SETTLE, 10.0)


class UnreachableSourceTests(MultiviewTestBase):
    """A source that does not answer must fail fast and say so.

    Measured on the bench before this: one unreachable source made a plan take
    **27.5 seconds** -- three config nodes, each retried with the fallback
    password, six WebSocket timeouts in series -- during which the page looked
    idle and Save was disabled with no explanation. That is what made the Save
    button appear to be missing.
    """

    def _plan(self, assignments):
        return self.client.post("/api/multiview/plan", json={
            "decoder": DECODER_IP, "layout": "2x2", "canvas": mv.ACTIVE_CANVAS,
            "name": "2x2", "assignments": assignments}).get_json()

    def test_an_unreachable_source_is_preflighted_not_waited_on(self):
        self.decoder()
        # ENCODER_IP is deliberately not registered, so it does not answer.
        body = self._plan({"top_left": ENCODER_IP})
        self.assertFalse(body["plan"]["ok"])
        self.assertTrue(any("did not answer" in e for e in body["plan"]["errors"]))
        # The preflight was asked, and no WebSocket read followed it.
        self.assertIn(ENCODER_IP, self.preflights)

    def test_a_failed_preflight_asks_the_device_nothing_further(self):
        self.decoder()
        dead = FakeDevice({"vc2": _vc2(), "sessions": _sessions()}, unreachable=True)
        self.devices[ENCODER_IP] = dead
        self._plan({"top_left": ENCODER_IP})
        # Six timeouts is what this replaces: once the preflight fails there is
        # nothing to be gained by asking three more questions.
        self.assertEqual(dead.writes, [])

    def test_one_unreachable_source_does_not_invalidate_the_others(self):
        self.decoder()
        self.encoder(ENCODER2_IP)
        body = self._plan({"top_left": ENCODER_IP, "top_right": ENCODER2_IP})
        plan = body["plan"]
        windows = {w["cell"]: w for w in plan["windows"]}
        self.assertEqual(windows["top_left"]["source"]["error"], "unreachable")
        # The reachable one is still fully planned.
        self.assertIsNotNone(windows["top_right"]["multicast"])
        self.assertIsNotNone(windows["top_right"]["ip_input"])
        self.assertEqual(windows["top_right"]["encoder_index"], 2)

    def test_the_failing_source_is_named_so_the_ui_can_mark_it(self):
        self.decoder()
        self.encoder(ENCODER2_IP)
        plan = self._plan({"top_left": ENCODER_IP,
                           "top_right": ENCODER2_IP})["plan"]
        failing = [w["source"]["ip"] for w in plan["windows"]
                   if w.get("source") and w["source"].get("error")]
        self.assertEqual(failing, [ENCODER_IP])

    def test_every_assigned_source_is_preflighted_exactly_once(self):
        self.decoder()
        self.encoder(ENCODER_IP)
        self.encoder(ENCODER2_IP)
        self.preflights.clear()
        self._plan({"top_left": ENCODER_IP, "top_right": ENCODER_IP,
                    "bottom_left": ENCODER2_IP})
        # Two distinct addresses, however many windows use them.
        self.assertEqual(sorted(self.preflights), sorted([ENCODER_IP, ENCODER2_IP]))

    def test_a_device_that_answers_tcp_but_not_the_api_is_still_absent(self):
        # Something is listening on the port; it is not an OmniStream encoder.
        self.decoder()
        device = FakeDevice({})                       # no vc2, no sessions
        self.devices[ENCODER_IP] = device
        reads = []
        original = device.handle
        device.handle = lambda payload: (
            reads.append(payload.get("config_get")) or original(payload))

        plan = self._plan({"top_left": ENCODER_IP})["plan"]
        self.assertFalse(plan["ok"])
        self.assertTrue(any("did not answer" in e for e in plan["errors"]))
        # Asserted as a count, not just an outcome: without the early exit the
        # answer is the same but the device is asked three times instead of once,
        # and it is the extra questions that cost the seconds.
        self.assertEqual(reads, ["vc2"])

    def test_sources_are_read_concurrently(self):
        # Four sources used to be read one after another. The pool is what keeps
        # one slow encoder from delaying the other three.
        import inspect
        source = inspect.getsource(srv._gather_encoder_states)
        self.assertIn("ThreadPoolExecutor", source)

    def test_an_unreachable_source_never_reaches_the_websocket_transport(self):
        self.decoder()
        calls = []
        original = srv._ws_send_recv_with_fallback

        def counting(ip, *args, **kwargs):
            calls.append(ip)
            return original(ip, *args, **kwargs)

        with mock.patch.object(srv, "_ws_send_recv_with_fallback", counting):
            self._plan({"top_left": ENCODER_IP})
        self.assertNotIn(ENCODER_IP, calls)


class IntraPlanScalerConflictTests(MultiviewTestBase):
    """One source cannot feed two windows that need different sizes.

    A scaler belongs to the encoder, so two windows drawing on the same encoder
    get one size between them. Asking for two is two writes to one field: the
    second overwrites the first and the losing window sits silently at the wrong
    resolution, with the device reporting no error at all.
    """

    def _plan(self, layout, canvas, assignments):
        return self.client.post("/api/multiview/plan", json={
            "decoder": DECODER_IP, "layout": layout, "canvas": canvas,
            "name": "probe", "assignments": assignments}).get_json()["plan"]

    def test_one_source_in_two_differently_sized_windows_is_a_conflict(self):
        self.decoder()
        self.encoder()
        # At 1080p a PiP main is 1920x1080 and its inset 640x360 -- both on
        # Encoder 2, so one source cannot serve both.
        plan = self._plan("pip-bottom-right", "1920x1080",
                          {"main": ENCODER_IP, "bottom_right": ENCODER_IP})
        self.assertEqual(len(plan["conflicts"]), 1, plan["conflicts"])
        conflict = plan["conflicts"][0]
        self.assertIn(ENCODER_IP, conflict)
        self.assertIn("1920x1080", conflict)
        self.assertIn("640x360", conflict)
        self.assertIn("Encoder 2", conflict)

    def test_such_a_plan_is_refused_rather_than_applied(self):
        decoder = self.decoder()
        encoder = self.encoder()
        response = self.client.post("/api/multiview/apply", json={
            "decoder": DECODER_IP, "layout": "pip-bottom-right",
            "canvas": "1920x1080", "name": "probe",
            "assignments": {"main": ENCODER_IP, "bottom_right": ENCODER_IP}})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(decoder.nodes["multiview"], [])
        self.assertEqual([w for w in encoder.writes if w[0] != "config_get"], [])

    def test_two_sources_in_differently_sized_windows_are_fine(self):
        self.decoder()
        self.encoder(ENCODER_IP)
        self.encoder(ENCODER2_IP)
        plan = self._plan("pip-bottom-right", "1920x1080",
                          {"main": ENCODER_IP, "bottom_right": ENCODER2_IP})
        self.assertEqual(plan["conflicts"], [])

    def test_the_escape_from_the_conflict_is_a_second_source(self):
        # With every window on Encoder 2 there is no second encoder to fall back
        # on, so a differently-sized window needs a different source. That is
        # what the conflict message tells the operator to do.
        self.decoder()
        self.encoder(ENCODER_IP)
        self.encoder(ENCODER2_IP)
        plan = self._plan("pip-bottom-right", mv.ACTIVE_CANVAS,
                          {"main": ENCODER_IP, "bottom_right": ENCODER2_IP})
        self.assertEqual(plan["conflicts"], [])
        windows = {w["cell"]: w for w in plan["windows"]}
        self.assertEqual(windows["main"]["encoder_index"], 2)
        self.assertEqual(windows["bottom_right"]["encoder_index"], 2)
        self.assertNotEqual(windows["main"]["source"]["ip"],
                            windows["bottom_right"]["source"]["ip"])

    def test_one_source_in_equally_sized_windows_is_fine(self):
        # A 2x2 has four identical windows; one source can serve all of them.
        self.decoder()
        self.encoder()
        plan = self._plan("2x2", "1920x1080",
                          {"top_left": ENCODER_IP, "top_right": ENCODER_IP,
                           "bottom_left": ENCODER_IP, "bottom_right": ENCODER_IP})
        self.assertEqual(plan["conflicts"], [])
        scaler_writes = [m for m in plan["activation"]
                         if m["stage"] == mv.STAGE_ENCODER_SCALER]
        self.assertEqual(len(scaler_writes), 1, "one size, one write")
        # And one stream, on one input, counted once.
        self.assertEqual(plan["bandwidth"]["unique_streams"], 1)
        self.assertEqual(len({(w["ip_input"] or {}).get("ip_input")
                              for w in plan["windows"]}), 1)

    def test_the_conflict_names_both_windows_so_it_can_be_acted_on(self):
        self.decoder()
        self.encoder()
        plan = self._plan("pip-bottom-right", "1920x1080",
                          {"main": ENCODER_IP, "bottom_right": ENCODER_IP})
        conflict = plan["conflicts"][0]
        self.assertIn("main", conflict)
        self.assertIn("bottom_right", conflict)
        self.assertIn("Use a different source", conflict)

    def test_an_unreachable_source_does_not_produce_a_spurious_conflict(self):
        # Its windows have no scaler requirement to conflict over.
        self.decoder()
        plan = self._plan("pip-bottom-right", "1920x1080",
                          {"main": ENCODER_IP, "bottom_right": ENCODER_IP})
        self.assertEqual(plan["conflicts"], [])
        self.assertFalse(plan["ok"])


class OutputResolutionMappingTests(unittest.TestCase):
    """A Multiview canvas decides the decoder's output resolution."""

    def test_the_active_canvas_maps_to_its_preset(self):
        self.assertEqual(mv.output_resolution_for_canvas(1920, 1080), "1920x1080")

    def test_a_snapped_canvas_still_maps_to_its_preset(self):
        # Most layouts snap the canvas; the output must not follow the snapped
        # height to a resolution the decoder does not offer.
        for width, height in [(1920, 1088), (1920, 1104)]:
            self.assertEqual(mv.output_resolution_for_canvas(width, height),
                             "1920x1080")

    def test_an_unexposed_canvas_maps_to_nothing(self):
        # Better to leave the output alone than to guess -- and this is what
        # makes a dormant 4K object unshowable through the production path
        # rather than silently driving the display to 4K.
        self.assertIsNone(mv.output_resolution_for_canvas(3840, 2160))
        self.assertIsNone(mv.output_resolution_for_canvas(3840, 2112))
        self.assertIsNone(mv.output_resolution_for_canvas(2560, 1440))
        self.assertIsNone(mv.output_resolution_for_canvas(0, 0))
        self.assertIsNone(mv.output_resolution_for_canvas(None, None))

    def test_every_mapped_value_is_one_the_decoder_offers(self):
        for preset in mv.CANVAS_PRESETS:
            if not preset["exposed"]:
                continue
            value = mv.output_resolution_for_canvas(preset["width"], preset["height"])
            self.assertIn(value, mv.DECODER_OUTPUT_RESOLUTIONS, preset["id"])

    def test_input_and_auto_are_never_produced(self):
        # The canvas decides the output, not the incoming signal.
        for preset in mv.CANVAS_PRESETS:
            value = mv.output_resolution_for_canvas(preset["width"], preset["height"])
            self.assertNotIn(value, ("input", "auto"))

    def test_the_server_drives_the_display_from_the_active_canvas(self):
        self.assertEqual(srv.MULTIVIEW_OUTPUT_RESOLUTION, mv.ACTIVE_CANVAS)
        self.assertEqual(srv.MULTIVIEW_OUTPUT_RESOLUTION, "1920x1080")


class ReclaimableInputsTests(unittest.TestCase):
    """Ownership has to be proven before anything is disabled."""

    def _inputs(self, **overrides):
        inputs = [{"name": "ip_input%d" % i, "enabled": False, "port": 1000,
                   "multicast": {"address": ""}} for i in range(1, 9)]
        by_name = {i["name"]: i for i in inputs}
        for name, address in overrides.items():
            by_name[name].update({"enabled": True,
                                  "multicast": {"address": address}})
        return inputs

    HDMI = {"video": {"input": "ip_input1"}, "audio": {"input": "ip_input3"},
            "aux": {"input": "ip_input5"}}

    def test_an_input_we_claimed_and_no_longer_need_is_reclaimable(self):
        spare, kept = mv.reclaimable_inputs(
            {"ip_input4": "A"}, set(), [], self.HDMI, self._inputs(ip_input4="A"))
        self.assertEqual(spare, ["ip_input4"])
        self.assertEqual(kept, [])

    def test_an_input_still_required_is_kept(self):
        spare, kept = mv.reclaimable_inputs(
            {"ip_input4": "A"}, {"ip_input4"}, [], self.HDMI,
            self._inputs(ip_input4="A"))
        self.assertEqual(spare, [])
        self.assertIn("still required", kept[0][1])

    def test_the_hdmi_video_audio_and_aux_inputs_are_never_reclaimed(self):
        # Even if metadata somehow claims them.
        spare, kept = mv.reclaimable_inputs(
            {"ip_input1": "V", "ip_input3": "A", "ip_input5": "X"}, set(), [],
            self.HDMI, self._inputs(ip_input1="V", ip_input3="A", ip_input5="X"))
        self.assertEqual(spare, [])
        self.assertEqual(len(kept), 3)
        self.assertTrue(all("it is the" in reason for _n, reason in kept))

    def test_an_input_another_multiview_uses_is_kept(self):
        others = [{"name": "multiviewOther",
                   "subframes": [{"input": "ip_input4"}]}]
        spare, kept = mv.reclaimable_inputs(
            {"ip_input4": "A"}, set(), others, self.HDMI,
            self._inputs(ip_input4="A"))
        self.assertEqual(spare, [])
        self.assertIn("another Multiview", kept[0][1])

    def test_an_input_repointed_since_we_claimed_it_is_kept(self):
        # Somebody else took it over. The claim no longer describes reality.
        spare, kept = mv.reclaimable_inputs(
            {"ip_input4": "A"}, set(), [], self.HDMI,
            self._inputs(ip_input4="SOMETHING-ELSE"))
        self.assertEqual(spare, [])
        self.assertIn("now carries", kept[0][1])

    def test_only_pool_inputs_can_ever_be_claimed(self):
        # Ownership rests on the pool being reserved: a window only ever lands
        # on ip_input 2/4/6/8, and a pool input carrying anything OmniSuite
        # cannot account for is refused at plan time rather than taken. So an
        # input outside the pool can never appear in a claim.
        assignments, errors, _collisions = mv.allocate_window_inputs(
            [{"key": "w%d" % i, "index": i, "address": "233.252.0.%d" % (41 + i),
              "port": 1000} for i in range(4)],
            _ip_inputs(), _hdmi_output()[0])
        self.assertEqual(errors, [])
        for assignment in assignments.values():
            self.assertIn(assignment["ip_input"], mv.WINDOW_IP_INPUTS)

    def test_an_input_missing_from_the_decoder_is_kept(self):
        spare, kept = mv.reclaimable_inputs(
            {"ip_input99": "A"}, set(), [], self.HDMI, self._inputs())
        self.assertEqual(spare, [])
        self.assertIn("not present", kept[0][1])

    def test_nothing_claimed_means_nothing_touched(self):
        spare, kept = mv.reclaimable_inputs({}, set(), [], self.HDMI, self._inputs())
        self.assertEqual((spare, kept), ([], []))


class RecallTransitionTests(MultiviewTestBase):
    """Changing which Multiview is shown is a transition, not a replay.

    Nothing an earlier recall left behind is assumed to have survived: every
    source is read again, the requirement is recomputed, and the pool inputs the
    new configuration does not need are released. This is what makes two saved
    Multiviews with incompatible Encoder-2 requirements ordinary rather than a
    conflict.
    """

    SOURCES = (ENCODER_IP, ENCODER2_IP, NEW_WALLPLATE_IP, PLAIN_111_IP)

    def setUp(self):
        super().setUp()
        self.dec = self.decoder()
        for ip in self.SOURCES:
            self.encoder(ip)

    def _enabled(self, names=None):
        pool = names or mv.WINDOW_IP_INPUTS
        return sorted(i["name"] for i in self.dec.nodes["ip_input"]
                      if i["enabled"] and i["name"] in pool)

    def _store(self, friendly, layout, assignments):
        body = self.save(layout=layout, name=friendly,
                         assignments=assignments).get_json()
        self.assertTrue(body["ok"], body)
        return self.offer(body["plan"]["object_name"])

    def _quad_unique(self):
        return self._store("Quad Unique", "2x2", dict(zip(
            ("top_left", "top_right", "bottom_left", "bottom_right"), self.SOURCES)))

    def _side_by_side(self):
        return self._store("Side by Side", "side-by-side",
                           {"left": ENCODER_IP, "right": ENCODER2_IP})

    def _quad_duplicate(self):
        return self._store("Quad Duplicate", "2x2",
                           {"top_left": ENCODER_IP, "top_right": ENCODER_IP,
                            "bottom_left": ENCODER2_IP,
                            "bottom_right": NEW_WALLPLATE_IP})

    # ---- the transition itself ---------------------------------------------

    def test_recall_enables_exactly_the_inputs_the_streams_need(self):
        name = self._quad_unique()
        self.assertEqual(self._enabled(), [])
        body = self.recall(name).get_json()
        self.assertTrue(body["ok"], body)
        self.assertEqual(self._enabled(),
                         ["ip_input2", "ip_input4", "ip_input6", "ip_input8"])

    def test_moving_to_a_smaller_layout_releases_what_it_no_longer_needs(self):
        quad = self._quad_unique()
        pair = self._side_by_side()
        self.assertTrue(self.recall(quad).get_json()["ok"])
        self.assertEqual(len(self._enabled()), 4)

        body = self.recall(pair).get_json()
        self.assertTrue(body["ok"], body)
        self.assertEqual(self._enabled(), ["ip_input2", "ip_input4"])
        self.assertEqual(sorted(body["released"]), ["ip_input6", "ip_input8"])

    def test_a_duplicate_layout_needs_fewer_inputs_than_it_has_windows(self):
        quad = self._quad_unique()
        dup = self._quad_duplicate()
        self.assertTrue(self.recall(quad).get_json()["ok"])
        body = self.recall(dup).get_json()
        self.assertTrue(body["ok"], body)
        # Three distinct streams across four windows.
        self.assertEqual(self._enabled(), ["ip_input2", "ip_input4", "ip_input6"])
        self.assertEqual(body["released"], ["ip_input8"])
        self.assertEqual(body["plan"]["bandwidth"]["unique_streams"], 3)
        self.assertEqual(body["plan"]["bandwidth"]["window_count"], 4)

    def test_the_decoders_own_inputs_are_never_released(self):
        quad = self._quad_unique()
        pair = self._side_by_side()
        self.recall(quad)
        self.recall(pair)
        enabled = [i["name"] for i in self.dec.nodes["ip_input"] if i["enabled"]]
        self.assertIn("ip_input1", enabled, "the HDMI video input was released")
        self.assertIn("ip_input3", enabled, "the HDMI audio input was released")

    def test_recall_replans_the_encoder_rather_than_trusting_the_last_one(self):
        # Side-by-Side wants 960x544; the PiP inset wants 640x360 from the same
        # encoder. Recalling one after the other must retune it, not assume.
        pair = self._side_by_side()
        pip = self._store("Presenter PiP", "pip-bottom-right",
                          {"main": ENCODER2_IP, "bottom_right": ENCODER_IP})
        encoder = self.devices[ENCODER_IP]

        self.assertTrue(self.recall(pair).get_json()["ok"])
        scaler = next(e for e in encoder.nodes["vc2"] if e["name"] == "vc2_encoder2")["scaler"]
        self.assertEqual([scaler["width"], scaler["height"]], [960, 544])

        self.assertTrue(self.recall(pip).get_json()["ok"])
        scaler = next(e for e in encoder.nodes["vc2"] if e["name"] == "vc2_encoder2")["scaler"]
        self.assertEqual([scaler["width"], scaler["height"]], [640, 360],
                         "recall replayed the previous size instead of replanning")

    def test_two_saved_multiviews_wanting_different_sizes_is_not_a_conflict(self):
        self._side_by_side()
        pip = self._store("Presenter PiP", "pip-bottom-right",
                          {"main": ENCODER2_IP, "bottom_right": ENCODER_IP})
        body = self.recall(pip).get_json()
        self.assertTrue(body["ok"], body)
        self.assertEqual(body["plan"]["conflicts"], [])

    def test_recalling_the_same_one_twice_changes_nothing_the_second_time(self):
        name = self._quad_unique()
        self.assertTrue(self.recall(name).get_json()["ok"])
        before = len(self.dec.writes)
        body = self.recall(name).get_json()
        self.assertTrue(body["ok"], body)
        self.assertTrue(body["already_shown"])
        self.assertEqual([w for w in self.dec.writes[before:] if w[0] != "config_get"], [])

    def test_a_failed_recall_puts_the_previous_configuration_back(self):
        quad = self._quad_unique()
        # A PiP needs 640x360 from an encoder the quad left at 960x544, so this
        # transition really does have to write a scaler -- which is the write
        # the encoder then refuses.
        pip = self._store("Presenter PiP", "pip-bottom-right",
                          {"main": ENCODER2_IP, "bottom_right": ENCODER_IP})
        self.assertTrue(self.recall(quad).get_json()["ok"])
        before_inputs = self._enabled()
        before_output = copy.deepcopy(self.dec.nodes["hdmi_output"][0])
        before_scaler = copy.deepcopy(next(
            e for e in self.devices[ENCODER_IP].nodes["vc2"]
            if e["name"] == "vc2_encoder2")["scaler"])

        self.devices[ENCODER_IP].refuse["vc2"] = "device busy"
        body = self.recall(pip).get_json()
        self.devices[ENCODER_IP].refuse.pop("vc2", None)
        self.assertFalse(body["ok"], body)
        self.assertIn("ROLLED BACK", body["status"])
        self.assertEqual(self._enabled(), before_inputs)
        self.assertEqual(self.dec.nodes["hdmi_output"][0]["video"]["input"],
                         before_output["video"]["input"])
        after_scaler = next(e for e in self.devices[ENCODER_IP].nodes["vc2"]
                            if e["name"] == "vc2_encoder2")["scaler"]
        self.assertEqual(after_scaler, before_scaler)

    def test_repeated_transitions_do_not_leak_inputs(self):
        quad = self._quad_unique()
        pair = self._side_by_side()
        dup = self._quad_duplicate()
        counts = []
        for _ in range(4):
            for name, expected in ((quad, 4), (pair, 2), (dup, 3)):
                self.assertTrue(self.recall(name).get_json()["ok"], name)
                counts.append(len(self._enabled()))
        self.assertEqual(counts, [4, 2, 3] * 4,
                         "enabled pool inputs drifted across transitions: %s" % counts)

    def test_an_inactive_saved_multiview_does_not_block_the_pool(self):
        # All four saved, only one recalled: the other three reference pool
        # inputs in their metadata and none of that reserves anything.
        self._quad_unique()
        self._quad_duplicate()
        pair = self._side_by_side()
        body = self.recall(pair).get_json()
        self.assertTrue(body["ok"], body)
        self.assertEqual(self._enabled(), ["ip_input2", "ip_input4"])


# ==========================================================================
# Every window, not just the composite
# ==========================================================================

class WindowLockTests(MultiviewTestBase):
    """The decoder's Input status can read active while one window sits black."""

    def _ready(self):
        self.decoder()
        self.encoder(ENCODER_IP)
        self.encoder(ENCODER2_IP)
        body = self.save(layout="side-by-side", name="Pair",
                         assignments={"left": ENCODER_IP,
                                      "right": ENCODER2_IP}).get_json()
        self.assertTrue(body["ok"], body)
        return self.offer(body["plan"]["object_name"])

    def test_a_show_with_every_window_locked_is_plainly_verified(self):
        name = self._ready()
        body = self.recall(name).get_json()
        self.assertTrue(body["ok"], body)
        self.assertEqual(body["status"], "VERIFIED")
        self.assertEqual(body["unlocked_windows"], [])
        self.assertEqual(len(body["windows"]), 2)
        self.assertTrue(all(w["active"] for w in body["windows"]))

    def test_a_window_that_never_locks_is_reported_not_hidden(self):
        name = self._ready()
        decoder = self.devices[DECODER_IP]
        real = decoder._apply_window_activity

        def one_window_black(multiviews):
            real(multiviews)
            for obj in multiviews:
                for subframe in obj.get("subframes") or []:
                    if subframe.get("name", "").startswith("right"):
                        subframe["video"]["input"] = {"active": False}
                        subframe["video"]["output"] = {"active": False}

        decoder._apply_window_activity = one_window_black
        with mock.patch.object(srv, "MULTIVIEW_WINDOW_SETTLE", 0.0):
            body = self.recall(name).get_json()
        decoder._apply_window_activity = real

        # The rest of the picture is live, so the operation is not undone --
        # but the condition is stated rather than swallowed.
        self.assertTrue(body["ok"], body)
        self.assertEqual(body["status"], "VERIFIED — WINDOW NOT LOCKED")
        self.assertEqual([w["subframe"] for w in body["unlocked_windows"]],
                         ["right (960x544)"])
        self.assertIsNotNone(body["diagnostics"])
        self.assertEqual(decoder.nodes["hdmi_output"][0]["video"]["input"], name,
                         "a late window rolled back a working display")

    def test_a_locked_show_carries_no_diagnostics(self):
        name = self._ready()
        body = self.recall(name).get_json()
        self.assertIsNone(body["diagnostics"])

    def test_the_window_wait_is_bounded(self):
        self.assertLessEqual(srv.MULTIVIEW_WINDOW_SETTLE, 30.0)
        import inspect
        self.assertIn("deadline", inspect.getsource(srv._await_window_lock))


# ==========================================================================
# Bandwidth counts streams, not windows
# ==========================================================================

class UniqueStreamBandwidthTests(MultiviewTestBase):

    def _plan(self, assignments, layout="2x2"):
        return self.client.post("/api/multiview/plan", json={
            "decoder": DECODER_IP, "layout": layout, "canvas": mv.ACTIVE_CANVAS,
            "name": layout, "assignments": assignments}).get_json()["plan"]

    def setUp(self):
        super().setUp()
        self.decoder()
        for ip in (ENCODER_IP, ENCODER2_IP, NEW_WALLPLATE_IP, PLAIN_111_IP):
            self.encoder(ip)

    CELLS = ("top_left", "top_right", "bottom_left", "bottom_right")

    def test_four_identical_windows_are_charged_once(self):
        plan = self._plan({c: ENCODER_IP for c in self.CELLS})
        self.assertEqual(plan["bandwidth"]["unique_streams"], 1)
        self.assertEqual(plan["bandwidth"]["window_count"], 4)
        only = plan["bandwidth"]["allocations"][0]
        self.assertEqual(plan["bandwidth"]["decoder_aggregate"], only["bitrate"])
        self.assertEqual(len(plan["unique_streams"]), 1)

    def test_two_duplicate_pairs_are_charged_twice(self):
        plan = self._plan(dict(zip(self.CELLS,
                                   [ENCODER_IP, ENCODER_IP, ENCODER2_IP, ENCODER2_IP])))
        self.assertEqual(plan["bandwidth"]["unique_streams"], 2)
        self.assertEqual(plan["bandwidth"]["window_count"], 4)
        total = sum(a["bitrate"] for a in plan["bandwidth"]["allocations"])
        self.assertEqual(plan["bandwidth"]["decoder_aggregate"], total)

    def test_one_source_in_several_windows_encodes_once(self):
        plan = self._plan({c: ENCODER_IP for c in self.CELLS})
        self.assertEqual(len(plan["bandwidth"]["allocations"]), 1,
                         "one source was charged for more than one encode")
        writes = [m for m in plan["activation"]
                  if m["stage"] == mv.STAGE_ENCODER_BITRATE]
        self.assertLessEqual(len(writes), 1)

    def test_a_repeated_source_takes_the_bitrate_of_its_largest_window(self):
        # The PiP main window is 1920x1080 and the inset 640x360; the encode has
        # to serve the larger of them.
        plan = self._plan({"main": ENCODER_IP, "bottom_right": ENCODER2_IP},
                          layout="pip-bottom-right")
        main = next(w for w in plan["windows"] if w["cell"] == "main")
        inset = next(w for w in plan["windows"] if w["cell"] == "bottom_right")
        self.assertGreater(main["area"], inset["area"])
        self.assertGreaterEqual(main["bitrate"], inset["bitrate"])

    def test_two_sources_sharing_one_destination_are_charged_once(self):
        # A misconfiguration OmniSuite already warns about: two encoders given
        # the same session destination. The decoder opens one stream and
        # receives it once, so charging it twice would refuse plans that fit.
        clash = "233.252.0.21"
        sessions = _sessions()
        sessions[1]["video"]["stream"]["destination_address"] = clash
        self.encoder(ENCODER2_IP, sessions=sessions)
        plan = self._plan({"top_left": ENCODER_IP, "top_right": ENCODER2_IP})
        addresses = {w["multicast"]["address"] for w in plan["windows"]
                     if w.get("multicast")}
        self.assertEqual(addresses, {clash}, "the fixture did not produce a clash")
        self.assertEqual(plan["bandwidth"]["unique_streams"], 1)
        charged = plan["bandwidth"]["decoder_aggregate"]
        each = [a["bitrate"] for a in plan["bandwidth"]["allocations"]]
        self.assertEqual(charged, max(each),
                         "one incoming stream was charged %s for %s"
                         % (charged, each))
        self.assertTrue(any("both resolve to" in w for w in plan["warnings"]),
                        plan["warnings"])

    def test_the_aggregate_never_exceeds_the_decoder_budget(self):
        for assignments in (
                {c: ENCODER_IP for c in self.CELLS},
                dict(zip(self.CELLS, [ENCODER_IP, ENCODER_IP, ENCODER2_IP, ENCODER2_IP])),
                dict(zip(self.CELLS, [ENCODER_IP, ENCODER2_IP, NEW_WALLPLATE_IP,
                                      PLAIN_111_IP]))):
            plan = self._plan(assignments)
            self.assertLessEqual(plan["bandwidth"]["decoder_aggregate"],
                                 mv.DECODER_MULTIVIEW_BUDGET)


# ==========================================================================
# One source, one scaler, two window sizes
# ==========================================================================

class RepeatedSourceSizeTests(MultiviewTestBase):
    """A single encode cannot be two sizes, so the assignment is refused.

    Measured on hardware: the decoder does not reject it. Packets flow, both
    subframes report active, and the decoder reports active video -- while the
    picture is wrong, because a subframe has no size of its own and takes the
    resolution arriving on its input. The inset would render full size over the
    main window. The device reports nothing that distinguishes the two, so the
    refusal has to come from here.
    """

    def _plan(self, assignments):
        return self.client.post("/api/multiview/plan", json={
            "decoder": DECODER_IP, "layout": "pip-bottom-right",
            "canvas": mv.ACTIVE_CANVAS, "name": "PiP",
            "assignments": assignments}).get_json()["plan"]

    def test_one_source_in_two_differently_sized_windows_is_refused(self):
        self.decoder()
        self.encoder()
        plan = self._plan({"main": ENCODER_IP, "bottom_right": ENCODER_IP})
        self.assertEqual(len(plan["conflicts"]), 1, plan["conflicts"])
        conflict = plan["conflicts"][0]
        self.assertIn("1920x1080", conflict)
        self.assertIn("640x360", conflict)
        self.assertIn("main", conflict)
        self.assertIn("bottom_right", conflict)

    def test_the_refusal_reaches_no_device(self):
        decoder = self.decoder()
        encoder = self.encoder()
        response = self.client.post("/api/multiview/apply", json={
            "decoder": DECODER_IP, "layout": "pip-bottom-right",
            "canvas": mv.ACTIVE_CANVAS, "name": "PiP",
            "assignments": {"main": ENCODER_IP, "bottom_right": ENCODER_IP}})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(decoder.nodes["multiview"], [])
        self.assertEqual([w for w in encoder.writes if w[0] != "config_get"], [])

    def test_the_same_source_in_two_equally_sized_windows_is_fine(self):
        self.decoder()
        self.encoder()
        plan = self.client.post("/api/multiview/plan", json={
            "decoder": DECODER_IP, "layout": "2x2", "canvas": mv.ACTIVE_CANVAS,
            "name": "2x2", "assignments": {"top_left": ENCODER_IP,
                                           "top_right": ENCODER_IP},
        }).get_json()["plan"]
        self.assertEqual(plan["conflicts"], [])
        self.assertTrue(plan["ok"], plan["errors"])


class StreamWindowLimitTests(MultiviewTestBase):
    """One decoder input drives at most two windows.

    Measured: with three or four subframes on one input, exactly two showed and
    the rest stayed black -- no error, no status, the stream arriving the whole
    time, and the decoder's composite Input status reading active because the
    other windows carried it.
    """

    CELLS = ("top_left", "top_right", "bottom_left", "bottom_right")

    def _plan(self, assignments, layout="2x2"):
        return self.client.post("/api/multiview/plan", json={
            "decoder": DECODER_IP, "layout": layout, "canvas": mv.ACTIVE_CANVAS,
            "name": layout, "assignments": assignments}).get_json()["plan"]

    def setUp(self):
        super().setUp()
        self.decoder()
        for ip in (ENCODER_IP, ENCODER2_IP, NEW_WALLPLATE_IP):
            self.encoder(ip)

    def test_the_limit_is_two_and_it_was_measured(self):
        self.assertEqual(mv.MAX_WINDOWS_PER_STREAM, 2)

    def test_one_stream_in_two_windows_is_allowed(self):
        plan = self._plan({"top_left": ENCODER_IP, "top_right": ENCODER_IP,
                           "bottom_left": ENCODER2_IP,
                           "bottom_right": NEW_WALLPLATE_IP})
        self.assertTrue(plan["ok"], plan["errors"])

    def test_one_stream_in_three_windows_is_refused(self):
        plan = self._plan({"top_left": ENCODER_IP, "top_right": ENCODER_IP,
                           "bottom_left": ENCODER_IP,
                           "bottom_right": ENCODER2_IP})
        self.assertFalse(plan["ok"])
        message = " ".join(plan["errors"])
        self.assertIn("assigned to 3 windows", message)
        self.assertIn("drives at most 2", message)
        self.assertIn("stay black", message)

    def test_one_stream_in_four_windows_is_refused(self):
        plan = self._plan({c: ENCODER_IP for c in self.CELLS})
        self.assertFalse(plan["ok"])
        self.assertIn("assigned to 4 windows", " ".join(plan["errors"]))

    def test_the_refusal_names_the_windows_that_would_be_black(self):
        plan = self._plan({c: ENCODER_IP for c in self.CELLS})
        message = " ".join(plan["errors"])
        for cell in self.CELLS:
            self.assertIn(cell, message)

    def test_the_refusal_reaches_no_device(self):
        decoder = self.devices[DECODER_IP]
        encoder = self.devices[ENCODER_IP]
        response = self.client.post("/api/multiview/apply", json={
            "decoder": DECODER_IP, "layout": "2x2", "canvas": mv.ACTIVE_CANVAS,
            "name": "2x2", "assignments": {c: ENCODER_IP for c in self.CELLS}})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(decoder.nodes["multiview"], [])
        self.assertEqual([w for w in encoder.writes if w[0] != "config_get"], [])

    def test_two_different_sources_twice_each_is_allowed(self):
        plan = self._plan(dict(zip(self.CELLS,
                                   [ENCODER_IP, ENCODER_IP, ENCODER2_IP, ENCODER2_IP])))
        self.assertTrue(plan["ok"], plan["errors"])
        self.assertEqual(plan["bandwidth"]["unique_streams"], 2)

    def test_the_limit_counts_streams_not_hostnames(self):
        # Two sessions of one encoder would be two streams and so two budgets;
        # Multiview only uses Session 2, so this is stated by the identity the
        # check uses rather than by which device the windows name.
        import inspect
        source = inspect.getsource(mv.check_stream_window_limit)
        self.assertIn("stream_identity", source)

    def test_two_sources_at_two_sizes_is_fine(self):
        self.decoder()
        self.encoder(ENCODER_IP)
        self.encoder(ENCODER2_IP)
        plan = self._plan({"main": ENCODER_IP, "bottom_right": ENCODER2_IP})
        self.assertEqual(plan["conflicts"], [])
        self.assertTrue(plan["ok"], plan["errors"])


# ==========================================================================
# The audio insertion point
# ==========================================================================

class AudioInsertionTests(MultiviewTestBase):
    """Audio is inserted on the input the display's audio already uses."""

    def _commission(self, main, other):
        self.decoder()
        self.encoder(main)
        self.encoder(other)
        body = self.save(layout="side-by-side", name="Pair",
                         assignments={"left": main, "right": other}).get_json()
        self.assertTrue(body["ok"], body)
        name = self.offer(body["plan"]["object_name"])
        return name, self.recall(name).get_json()

    def _audio_address(self, ip):
        encoder = self.devices[ip]
        session1 = next(s for s in encoder.nodes["sessions"] if s["name"] == "session1")
        return session1["audio"]["stream"]["destination_address"]

    def test_the_audio_input_is_the_one_the_display_already_uses(self):
        _name, body = self._commission(ENCODER_IP, ENCODER2_IP)
        self.assertTrue(body["ok"], body)
        decoder = self.devices[DECODER_IP]
        # The bench convention puts it on ip_input3; the rule is "whatever the
        # display's audio already is", which is what makes it portable.
        self.assertEqual(decoder.nodes["hdmi_output"][0]["audio"]["input"], "ip_input3")
        self.assertEqual(body["audio"]["ip_input"], "ip_input3")

    def test_the_audio_follows_the_main_window_source(self):
        _name, body = self._commission(ENCODER2_IP, ENCODER_IP)
        self.assertTrue(body["ok"], body)
        decoder = self.devices[DECODER_IP]
        entry = next(i for i in decoder.nodes["ip_input"] if i["name"] == "ip_input3")
        self.assertEqual(entry["multicast"]["address"], self._audio_address(ENCODER2_IP))
        self.assertTrue(entry["enabled"])

    def test_recalling_another_multiview_moves_the_audio_with_it(self):
        # The failure this prevents: audio stuck on the previously shown
        # Multiview's source.
        self.decoder()
        self.encoder(ENCODER_IP)
        self.encoder(ENCODER2_IP)
        first = self.offer(self.save(
            layout="side-by-side", name="First",
            assignments={"left": ENCODER_IP, "right": ENCODER2_IP}
        ).get_json()["plan"]["object_name"])
        second = self.offer(self.save(
            layout="side-by-side", name="Second",
            assignments={"left": ENCODER2_IP, "right": ENCODER_IP}
        ).get_json()["plan"]["object_name"])

        self.assertTrue(self.recall(first).get_json()["ok"])
        decoder = self.devices[DECODER_IP]
        entry = next(i for i in decoder.nodes["ip_input"] if i["name"] == "ip_input3")
        self.assertEqual(entry["multicast"]["address"], self._audio_address(ENCODER_IP))

        self.assertTrue(self.recall(second).get_json()["ok"])
        entry = next(i for i in decoder.nodes["ip_input"] if i["name"] == "ip_input3")
        self.assertEqual(entry["multicast"]["address"], self._audio_address(ENCODER2_IP),
                         "audio stayed on the previous Multiview's source")

    def test_session_two_audio_is_never_used(self):
        _name, body = self._commission(ENCODER_IP, ENCODER2_IP)
        self.assertEqual(body["audio"]["session"], "session1")
        for ip in (ENCODER_IP, ENCODER2_IP):
            session2 = next(s for s in self.devices[ip].nodes["sessions"]
                            if s["name"] == "session2")
            self.assertFalse(session2["audio"]["stream"]["enabled"])

    def test_the_audio_input_is_never_one_of_the_window_pool(self):
        _name, body = self._commission(ENCODER_IP, ENCODER2_IP)
        self.assertNotIn(body["audio"]["ip_input"], mv.WINDOW_IP_INPUTS)


# ==========================================================================
# Recall shows what the decoder is subscribed to
# ==========================================================================

class LiveSubscriptionTests(MultiviewTestBase):
    """After a recall the canvas is the decoder's subscriptions, not a memory."""

    def _commission(self, assignments=None, layout="side-by-side"):
        self.decoder()
        for ip in (ENCODER_IP, ENCODER2_IP):
            self.encoder(ip)
        assignments = assignments or {"left": ENCODER_IP, "right": ENCODER2_IP}
        body = self.save(layout=layout, name="Pair",
                         assignments=assignments).get_json()
        self.assertTrue(body["ok"], body)
        name = self.offer(body["plan"]["object_name"])
        self.assertTrue(self.recall(name).get_json()["ok"])
        return name

    def _windows(self, name):
        body = self.client.get(f"/api/multiview/state?ip={DECODER_IP}").get_json()
        view = next(v for v in body["multiviews"] if v["name"] == name)
        return {w["cell"]: w for w in view["subframes"]}

    def test_each_window_names_the_encoder_its_input_is_listening_to(self):
        name = self._commission()
        windows = self._windows(name)
        self.assertEqual(windows["left"]["source"]["ip"], ENCODER_IP)
        self.assertEqual(windows["right"]["source"]["ip"], ENCODER2_IP)
        for window in windows.values():
            self.assertEqual(window["source_origin"], "subscription")
            self.assertTrue(window["subscribed"])
            self.assertEqual(window["health"], "live")

    def test_the_window_carries_the_stream_it_is_actually_open_on(self):
        name = self._commission()
        windows = self._windows(name)
        self.assertEqual(windows["left"]["multicast"], S2_VIDEO)
        self.assertEqual(windows["left"]["multicast_port"], 1000)
        self.assertEqual(windows["left"]["stream"], "%s:1000" % S2_VIDEO)
        self.assertIn(windows["left"]["ip_input"], mv.WINDOW_IP_INPUTS)

    def test_a_subscription_matching_nothing_discovered_is_shown_not_erased(self):
        # Something is plainly playing in that window; refusing to draw it is
        # worse than admitting the source is unrecognised.
        name = self._commission()
        decoder = self.devices[DECODER_IP]
        entry = next(i for i in decoder.nodes["ip_input"] if i["name"] == "ip_input2")
        entry["multicast"]["address"] = "233.252.0.199"
        windows = self._windows(name)
        left = windows["left"]
        self.assertIsNone(left["source"])
        self.assertEqual(left["source_origin"], "unknown")
        self.assertEqual(left["multicast"], "233.252.0.199")
        self.assertTrue(left["subscribed"])

    def test_a_window_whose_input_carries_nothing_falls_back_to_the_preset(self):
        name = self._commission()
        decoder = self.devices[DECODER_IP]
        entry = next(i for i in decoder.nodes["ip_input"] if i["name"] == "ip_input2")
        entry.update({"enabled": False, "multicast": {"address": ""}})
        left = self._windows(name)["left"]
        self.assertEqual(left["source_origin"], "saved")
        self.assertEqual(left["source"]["ip"], ENCODER_IP)
        self.assertFalse(left["subscribed"])
        self.assertEqual(left["health"], "not subscribed")

    def test_a_subscription_that_disagrees_with_the_preset_is_flagged(self):
        name = self._commission()
        decoder = self.devices[DECODER_IP]
        # ip_input2 is repointed at the OTHER encoder's session 2 behind us.
        other = self.devices[ENCODER2_IP]
        session2 = next(s for s in other.nodes["sessions"] if s["name"] == "session2")
        entry = next(i for i in decoder.nodes["ip_input"] if i["name"] == "ip_input2")
        entry["multicast"]["address"] = session2["video"]["stream"]["destination_address"]
        left = self._windows(name)["left"]
        self.assertEqual(left["source"]["ip"], ENCODER2_IP)
        self.assertEqual(left["saved_source_ip"], ENCODER_IP)
        self.assertTrue(left["diverged"])

    def test_resolution_pairs_the_address_with_the_port(self):
        # An address alone can belong to two sessions on different ports.
        name = self._commission()
        decoder = self.devices[DECODER_IP]
        entry = next(i for i in decoder.nodes["ip_input"] if i["name"] == "ip_input2")
        entry["port"] = 1004
        left = self._windows(name)["left"]
        self.assertIsNone(left["source"], "a different port resolved to the same source")
        self.assertEqual(left["source_origin"], "unknown")


# ==========================================================================
# The active canvas is a switching surface
# ==========================================================================

class LiveSwitchTests(MultiviewTestBase):

    def _commission(self, layout="side-by-side", assignments=None):
        self.decoder()
        for ip in (ENCODER_IP, ENCODER2_IP, NEW_WALLPLATE_IP, PLAIN_111_IP):
            self.encoder(ip)
        assignments = assignments or {"left": ENCODER_IP, "right": ENCODER2_IP}
        body = self.save(layout=layout, name="Pair",
                         assignments=assignments).get_json()
        self.assertTrue(body["ok"], body)
        name = self.offer(body["plan"]["object_name"])
        self.assertTrue(self.recall(name).get_json()["ok"])
        return name

    def _switch(self, name, cell, source):
        return self.client.post("/api/multiview/switch", json={
            "decoder": DECODER_IP, "name": name, "cell": cell, "source": source})

    def _saved(self, name):
        record = srv._multiview_meta_for(
            {"ip": DECODER_IP, "mac": "00:00:5E:00:53:10"}).get(name) or {}
        return {w["cell"]: w["source_ip"] for w in (record.get("windows") or [])}

    def test_a_live_switch_changes_only_the_window_it_was_asked_to(self):
        name = self._commission()
        decoder = self.devices[DECODER_IP]
        before = {i["name"]: copy.deepcopy(i) for i in decoder.nodes["ip_input"]}
        body = self._switch(name, "left", NEW_WALLPLATE_IP).get_json()
        self.assertTrue(body["ok"], body)
        self.assertEqual(body["status"], "VERIFIED")
        # The untouched window's input is byte-identical.
        right = self._windows(name)["right"]["ip_input"]
        self.assertEqual(decoder.nodes["ip_input"][
            [i["name"] for i in decoder.nodes["ip_input"]].index(right)],
            before[right])

    def _windows(self, name):
        body = self.client.get(f"/api/multiview/state?ip={DECODER_IP}").get_json()
        view = next(v for v in body["multiviews"] if v["name"] == name)
        return {w["cell"]: w for w in view["subframes"]}

    def test_the_switched_window_is_subscribed_to_the_new_source(self):
        name = self._commission()
        self.assertTrue(self._switch(name, "left", NEW_WALLPLATE_IP).get_json()["ok"])
        left = self._windows(name)["left"]
        self.assertEqual(left["source"]["ip"], NEW_WALLPLATE_IP)
        self.assertEqual(left["source_origin"], "subscription")
        self.assertTrue(left["input_active"])

    def test_the_other_windows_stay_locked(self):
        name = self._commission()
        body = self._switch(name, "left", NEW_WALLPLATE_IP).get_json()
        self.assertEqual(body["regressed_windows"], [])
        self.assertEqual(body["unlocked_windows"], [])
        self.assertTrue(all(w["active"] for w in body["windows"]))

    def test_a_successful_switch_becomes_the_saved_preset(self):
        name = self._commission()
        self.assertEqual(self._saved(name)["left"], ENCODER_IP)
        body = self._switch(name, "left", NEW_WALLPLATE_IP).get_json()
        self.assertTrue(body["ok"], body)
        self.assertTrue(body["saved"])
        self.assertEqual(self._saved(name)["left"], NEW_WALLPLATE_IP,
                         "the display and the preset disagree")

    def test_the_change_survives_recalling_something_else_and_coming_back(self):
        name = self._commission()
        other = self.offer(self.save(
            layout="2x2", name="Quad",
            assignments={"top_left": ENCODER2_IP}).get_json()["plan"]["object_name"])
        self.assertTrue(self._switch(name, "left", NEW_WALLPLATE_IP).get_json()["ok"])
        self.assertTrue(self.recall(other).get_json()["ok"])
        self.assertTrue(self.recall(name).get_json()["ok"])
        self.assertEqual(self._windows(name)["left"]["source"]["ip"], NEW_WALLPLATE_IP)

    def test_switching_an_inactive_multiview_is_refused(self):
        name = self._commission()
        other = self.offer(self.save(
            layout="2x2", name="Quad",
            assignments={"top_left": ENCODER2_IP}).get_json()["plan"]["object_name"])
        response = self._switch(other, "top_left", ENCODER_IP)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json()["status"], "NOT ACTIVE")

    def test_a_failed_switch_rolls_back_and_leaves_the_preset_alone(self):
        name = self._commission()
        decoder = self.devices[DECODER_IP]
        before = copy.deepcopy(decoder.nodes["ip_input"])
        decoder.refuse["ip_input"] = "device busy"
        body = self._switch(name, "left", NEW_WALLPLATE_IP).get_json()
        decoder.refuse.pop("ip_input", None)
        self.assertFalse(body["ok"], body)
        self.assertIn("ROLLED BACK", body["status"])
        self.assertEqual(decoder.nodes["ip_input"], before)
        self.assertEqual(self._saved(name)["left"], ENCODER_IP,
                         "a failed switch was saved")
        self.assertEqual(body["restored_source"], ENCODER_IP)

    def test_a_failure_part_way_through_undoes_the_writes_already_made(self):
        """The rollback has to have something to undo for this to mean anything.

        The other rollback test refuses the first write in the transaction, so
        nothing had been applied and an empty rollback looked identical to a
        correct one. Here the encoder and the decoder input are written
        successfully and the *rebind* is refused, so a real rollback has to put
        several devices back.
        """
        name = self._commission()
        decoder = self.devices[DECODER_IP]
        source = self.devices[ENCODER2_IP]
        before_inputs = copy.deepcopy(decoder.nodes["ip_input"])
        before_encoder = copy.deepcopy(source.nodes["vc2"])
        before_sessions = copy.deepcopy(source.nodes["sessions"])
        before_object = copy.deepcopy(decoder.nodes["multiview"])

        # Switching `left` onto the stream `right` already carries makes the two
        # windows share one input, so the subframe genuinely has to be rebound --
        # which is the write this test needs to have refused, after the encoder
        # and the decoder input have already been changed.
        decoder.refuse["multiview"] = "device busy"
        body = self._switch(name, "left", ENCODER2_IP).get_json()
        decoder.refuse.pop("multiview", None)

        self.assertFalse(body["ok"], body)
        self.assertIn("ROLLED BACK", body["status"])
        self.assertTrue(body["rollback"], "nothing was rolled back at all")
        self.assertTrue(all(entry["verified"] for entry in body["rollback"]),
                        body["rollback"])
        # Every device is back where it started, not just the decoder.
        self.assertEqual(decoder.nodes["ip_input"], before_inputs)
        self.assertEqual(source.nodes["vc2"], before_encoder)
        self.assertEqual(source.nodes["sessions"], before_sessions)
        self.assertEqual(decoder.nodes["multiview"], before_object)
        self.assertEqual(self._saved(name)["left"], ENCODER_IP)

    def test_a_device_that_cannot_be_read_is_not_assumed_to_be_configured(self):
        """The "already applied?" filter must fail towards doing the work.

        A switch skips writes the devices already hold. A device that could not
        be read must therefore count as holding nothing -- treating silence as
        "already correct" would skip every write to it and then report success
        on a device that was never touched.

        Asserted on the predicate rather than through the endpoint, because a
        device that cannot be read fails while the plan is being built, long
        before this filter runs. The guard is what keeps that true if the order
        ever changes.
        """
        self.decoder()
        mutation = {"device": DECODER_IP, "node": "ip_input",
                    "target": "ip_input2",
                    "config": {"name": "ip_input2", "enabled": False}}
        # It answers, and already holds this: nothing to do.
        self.assertTrue(srv._already_applied(mutation))

        def unreadable(ip, node):
            raise OSError("the device is not answering")

        with mock.patch.object(srv, "_mv_config", unreadable):
            self.assertFalse(srv._already_applied(mutation),
                             "silence was read as agreement")

    def test_a_method_call_is_never_skipped_as_already_applied(self):
        """A create or a delete has no "already written" reading to infer."""
        self.decoder()
        self.assertFalse(srv._already_applied({
            "device": DECODER_IP, "node": "multiview", "target": "anything",
            "method": "add_multiview_subframe", "config": {}}))

    def test_a_switch_may_use_a_pool_input_another_saved_multiview_mentions(self):
        """A saved Multiview is a description, not a reservation.

        The switch passes the inputs other managed Multiviews reference to the
        allocator so it knows they are OmniSuite's to reuse. Without that, a
        decoder holding a second saved layout could refuse a switch because some
        *inactive* object mentions the input it wants -- which is exactly the
        claim-based model Phase 6 removed.
        """
        name = self._commission(layout="2x2", assignments={
            "top_left": ENCODER_IP, "top_right": ENCODER2_IP,
            "bottom_left": NEW_WALLPLATE_IP, "bottom_right": PLAIN_111_IP})
        # A second saved Multiview that names the whole pool, and is not shown.
        other = self.save(layout="2x2", name="Shadow", assignments={
            "top_left": PLAIN_111_IP, "top_right": NEW_WALLPLATE_IP,
            "bottom_left": ENCODER2_IP, "bottom_right": ENCODER_IP,
        }).get_json()
        self.assertTrue(other["ok"], other)
        self.offer(other["plan"]["object_name"])

        body = self._switch(name, "bottom_right", ENCODER2_IP).get_json()
        self.assertTrue(body["ok"], body)
        self.assertEqual(body["status"], "VERIFIED")
        windows = self._windows(name)
        self.assertEqual(windows["bottom_right"]["source"]["ip"], ENCODER2_IP)
        self.assertTrue(all(w["input_active"] for w in windows.values()))
        # And the other object was not touched by any of it.
        self.assertIn(other["plan"]["object_name"],
                      [str(o.get("name") or "")
                       for o in self.devices[DECODER_IP].nodes["multiview"]])

    def test_a_window_that_never_locks_is_rolled_back(self):
        name = self._commission()
        decoder = self.devices[DECODER_IP]
        real = decoder._apply_window_activity

        def left_stays_black(multiviews):
            real(multiviews)
            for obj in multiviews:
                for subframe in obj.get("subframes") or []:
                    if subframe.get("name", "").startswith("left"):
                        subframe["video"]["input"] = {"active": False}
                        subframe["video"]["output"] = {"active": False}

        decoder._apply_window_activity = left_stays_black
        with mock.patch.object(srv, "MULTIVIEW_WINDOW_SETTLE", 0.0):
            body = self._switch(name, "left", NEW_WALLPLATE_IP).get_json()
        decoder._apply_window_activity = real
        self.assertFalse(body["ok"], body)
        self.assertIn("NO ACTIVE VIDEO", body["status"])
        self.assertEqual(self._saved(name)["left"], ENCODER_IP)
        self.assertIsNotNone(body["diagnostics"])

    def test_switching_the_main_window_moves_the_audio(self):
        name = self._commission()
        decoder = self.devices[DECODER_IP]
        wallplate = self.devices[NEW_WALLPLATE_IP]
        wanted = next(s for s in wallplate.nodes["sessions"]
                      if s["name"] == "session1")["audio"]["stream"]["destination_address"]
        body = self._switch(name, "left", NEW_WALLPLATE_IP).get_json()
        self.assertTrue(body["ok"], body)
        self.assertEqual(body["audio"]["source_ip"], NEW_WALLPLATE_IP)
        audio_input = decoder.nodes["hdmi_output"][0]["audio"]["input"]
        entry = next(i for i in decoder.nodes["ip_input"] if i["name"] == audio_input)
        self.assertEqual(entry["multicast"]["address"], wanted)

    def test_switching_a_secondary_window_leaves_the_audio_alone(self):
        name = self._commission()
        decoder = self.devices[DECODER_IP]
        before = copy.deepcopy(decoder.nodes["hdmi_output"][0]["audio"])
        audio_entry = copy.deepcopy(
            next(i for i in decoder.nodes["ip_input"] if i["name"] == "ip_input3"))
        self.assertTrue(self._switch(name, "right", NEW_WALLPLATE_IP).get_json()["ok"])
        self.assertEqual(decoder.nodes["hdmi_output"][0]["audio"], before)
        self.assertEqual(
            next(i for i in decoder.nodes["ip_input"] if i["name"] == "ip_input3"),
            audio_entry)

    def test_switching_onto_a_source_already_shown_shares_its_input(self):
        name = self._commission()
        body = self._switch(name, "right", ENCODER_IP).get_json()
        self.assertTrue(body["ok"], body)
        windows = self._windows(name)
        self.assertEqual(windows["left"]["ip_input"], windows["right"]["ip_input"])
        decoder = self.devices[DECODER_IP]
        enabled = [i["name"] for i in decoder.nodes["ip_input"]
                   if i["enabled"] and i["name"] in mv.WINDOW_IP_INPUTS]
        self.assertEqual(len(enabled), 1, "a second input was opened for one stream")

    def test_a_switch_that_would_break_the_two_window_limit_is_refused(self):
        name = self._commission(
            layout="2x2",
            assignments={"top_left": ENCODER_IP, "top_right": ENCODER_IP,
                         "bottom_left": ENCODER2_IP, "bottom_right": PLAIN_111_IP})
        decoder = self.devices[DECODER_IP]
        before = copy.deepcopy(decoder.nodes["ip_input"])
        response = self._switch(name, "bottom_left", ENCODER_IP)
        self.assertEqual(response.status_code, 400)
        self.assertIn("drives at most 2", response.get_json()["error"])
        self.assertEqual(decoder.nodes["ip_input"], before)

    def test_a_switch_onto_an_unknown_window_is_refused(self):
        name = self._commission()
        response = self._switch(name, "nowhere", ENCODER_IP)
        self.assertEqual(response.status_code, 400)

    # ---- what the live matrix found ------------------------------------
    def _four_split(self):
        """One large window and three small ones, each on its own stream."""
        return self._commission(layout="4-split", assignments={
            "main": ENCODER_IP, "top_right": ENCODER2_IP,
            "middle_right": NEW_WALLPLATE_IP, "bottom_right": PLAIN_111_IP})

    def _pool(self):
        return {i["name"]: (i["enabled"], (i.get("multicast") or {}).get("address"))
                for i in self.devices[DECODER_IP].nodes["ip_input"]
                if i["name"] in mv.WINDOW_IP_INPUTS}

    def test_a_switch_that_unshares_a_stream_prepares_the_window_it_moves(self):
        """The one switch in 36 that failed on the bench, and why.

        Taking a source off a window can un-share a stream, and the re-plan then
        moves the *other* window onto a free pool input. The endpoint used to
        apply only the changed source's mutations and the changed window's
        input, so the input the other window was moved to was never enabled or
        pointed anywhere -- and that window went black while the switch reported
        success.
        """
        name = self._four_split()
        # top_right and bottom_right now share one stream on one input.
        self.assertTrue(self._switch(name, "bottom_right",
                                     ENCODER2_IP).get_json()["ok"])
        windows = self._windows(name)
        self.assertEqual(windows["bottom_right"]["ip_input"],
                         windows["top_right"]["ip_input"])

        # Moving top_right away un-shares it, so bottom_right has to move.
        body = self._switch(name, "top_right", PLAIN_111_IP).get_json()
        self.assertTrue(body["ok"], body)
        self.assertEqual(body["status"], "VERIFIED")

        windows = self._windows(name)
        self.assertNotEqual(windows["bottom_right"]["ip_input"],
                            windows["top_right"]["ip_input"])
        for cell, window in windows.items():
            self.assertTrue(window["input_active"],
                            "%s went dark: %s" % (cell, window))
        moved = windows["bottom_right"]["ip_input"]
        enabled, address = self._pool()[moved]
        self.assertTrue(enabled, "%s was left disabled under a bound window" % moved)
        self.assertEqual(address, windows["bottom_right"]["multicast"])

    def test_un_sharing_a_stream_leaves_the_other_window_on_its_own_input(self):
        """Allocation order must not evict a stream from the input it is on.

        The allocator preferred an input already carrying the wanted stream, but
        it only looked when it reached that window -- by which time an earlier
        window's stream could have taken that very slot from the free pool. So
        un-sharing moved the window that had not changed. It kept its picture,
        but nothing asked for it to move, and on the bench it was the difference
        between a switch that touched one window and one that touched two.
        """
        name = self._four_split()
        self.assertTrue(self._switch(name, "bottom_right",
                                     ENCODER2_IP).get_json()["ok"])
        before = {c: w["ip_input"] for c, w in self._windows(name).items()}
        self.assertEqual(before["bottom_right"], before["top_right"])

        body = self._switch(name, "top_right", PLAIN_111_IP).get_json()
        self.assertTrue(body["ok"], body)
        after = {c: w["ip_input"] for c, w in self._windows(name).items()}
        moved = {c: (before[c], after[c]) for c in before
                 if c != "top_right" and before[c] != after[c]}
        self.assertEqual(moved, {}, "windows nobody asked about were moved")
        self.assertNotEqual(after["top_right"], after["bottom_right"])
        self.assertTrue(all(w["input_active"]
                            for w in self._windows(name).values()))

    def test_a_fresh_allocation_still_hands_out_slots_in_first_use_order(self):
        """The other half: with nothing live, the documented order is unchanged."""
        name = self._commission(layout="2x2", assignments={
            "top_left": ENCODER_IP, "top_right": ENCODER2_IP,
            "bottom_left": NEW_WALLPLATE_IP, "bottom_right": PLAIN_111_IP})
        self.assertEqual(
            {c: w["ip_input"] for c, w in self._windows(name).items()},
            {"top_left": "ip_input2", "top_right": "ip_input4",
             "bottom_left": "ip_input6", "bottom_right": "ip_input8"})

    def test_a_switch_applies_only_what_the_devices_do_not_already_hold(self):
        """The filter is "is this already true", so it stays minimal."""
        name = self._four_split()
        body = self._switch(name, "top_right", PLAIN_111_IP).get_json()
        self.assertTrue(body["ok"], body)
        touched = " ".join(step["step"] for step in body["steps"])
        # middle_right was not involved and its stream did not move, so nothing
        # should have been written for it.
        self.assertNotIn("ip_input6", touched, touched)
        self.assertNotIn(NEW_WALLPLATE_IP, touched, touched)

    def _config_sets(self, ip):
        """Every field a device was actually asked to change."""
        fields = []
        for verb, node, entries in self.devices[ip].writes:
            if verb != "config_set":
                continue
            for entry in entries if isinstance(entries, list) else [entries]:
                for key, value in (entry or {}).items():
                    if key == "name":
                        continue
                    fields.append(("%s.%s.%s" % (node, entry.get("name"), key),
                                   value))
        return fields

    def test_a_switch_writes_no_field_the_device_already_holds(self):
        """The real write-minimality assertion for the switch path.

        The transaction is planned in full and then diffed against what the
        devices hold; what survives the diff is what may be written. Counting
        the writes is the only way to notice the diff being skipped -- the step
        descriptions look the same either way.
        """
        name = self._commission()
        for device in self.devices.values():
            device.writes = []
        body = self._switch(name, "right", NEW_WALLPLATE_IP).get_json()
        self.assertTrue(body["ok"], body)
        plan = body["writes"]
        self.assertGreater(plan["already_correct"], 0,
                           "nothing was already correct, so this proves nothing")
        # One mutation is one config_set or one method call, which is the unit
        # the transaction reports. Counting fields instead compares nine against
        # six and calls correct behaviour a defect.
        written = sum(len(device.writes) for device in self.devices.values())
        self.assertEqual(
            written, plan["writes_required"],
            "the switch made %d device calls against %d planned: fields the "
            "devices already held were written anyway"
            % (written, plan["writes_required"]))
        self.assertEqual(plan["writes_performed"], plan["writes_required"], plan)

    def test_a_switch_prepares_the_source_it_moved_a_window_onto(self):
        """A switch is not decoder-only work.

        The window has to be pointed at the new stream AND the encoder behind it
        has to be producing one. Nothing else in this file fails if the encoder
        half of the transaction stops being applied.
        """
        # Side-by-side uses two of the four commissioned encoders, so the wall
        # plate is a source that has NOT already been prepared. Switching onto
        # one that is already streaming writes nothing, correctly, and would
        # prove nothing here.
        name = self._commission()
        for device in self.devices.values():
            device.writes = []
        body = self._switch(name, "right", NEW_WALLPLATE_IP).get_json()
        self.assertTrue(body["ok"], body)
        encoder_fields = [field for field, _value
                          in self._config_sets(NEW_WALLPLATE_IP)]
        self.assertTrue(
            any(f.startswith("vc2.vc2_encoder2") for f in encoder_fields),
            "the switch configured nothing on the source encoder: %s"
            % encoder_fields)
        self.assertTrue(
            any(f.startswith("sessions.session2") for f in encoder_fields),
            "the switch never started the source stream: %s" % encoder_fields)

    def test_a_pool_input_omnisuite_did_not_configure_is_left_alone(self):
        """The restrictive half of ownership.

        A saved Multiview referencing an input is a description, not a claim --
        that is the permissive test above. This is the other direction: an input
        that is enabled and carrying something OmniSuite never set up is not
        OmniSuite's to release, whatever any saved object says about it.
        """
        # Side-by-side occupies two of the four pool inputs, so there is a spare
        # one for somebody else to be using. A 4-split occupies all four, which
        # made the "stranger" below one of this Multiview own windows.
        name = self._commission()
        decoder = self.devices[DECODER_IP]
        # Something else entirely is using a pool input, and the only object
        # that mentions it is one OmniSuite did not create.
        stranger = "ip_input8"
        for entry in decoder.nodes["ip_input"]:
            if entry["name"] == stranger:
                entry.update({"enabled": True, "port": 1000,
                              "multicast": {"address": "233.252.0.199"}})
        decoder.nodes["multiview"].append(_multiview_object(
            "someoneElsesLayout", 1920, 1088,
            [{"name": "subframe1", "input": stranger}]))
        decoder.writes = []
        body = self._switch(name, "right", NEW_WALLPLATE_IP).get_json()
        self.assertTrue(body["ok"], body)
        touched = [field for field, _value in self._config_sets(DECODER_IP)
                   if ".%s." % stranger in field or field.endswith(stranger)]
        self.assertEqual(touched, [],
                         "%s was reconfigured, and OmniSuite did not put it "
                         "there: %s" % (stranger, touched))
        entry = next(i for i in decoder.nodes["ip_input"]
                     if i["name"] == stranger)
        self.assertTrue(entry["enabled"],
                        "%s was released, and it was not OmniSuite's" % stranger)
        self.assertEqual((entry.get("multicast") or {}).get("address"),
                         "233.252.0.199",
                         "%s was repointed at something else" % stranger)

    def test_a_switch_that_darkens_another_window_is_undone(self):
        """A window the operator did not ask about is not acceptable collateral.

        This used to return ok with "VERIFIED - WINDOW NOT LOCKED" and record the
        new preset, leaving the operator with the window they asked for and one
        fewer than they had.
        """
        name = self._commission()
        decoder = self.devices[DECODER_IP]
        real = decoder._apply_window_activity
        started = []

        def right_goes_dark(multiviews):
            real(multiviews)
            # Only once the switch has begun writing, so the window is active
            # beforehand and this is a regression the switch caused rather than
            # a condition it inherited.
            if not started:
                return
            for obj in multiviews:
                for subframe in obj.get("subframes") or []:
                    if subframe.get("name", "").startswith("right"):
                        subframe["video"]["input"] = {"active": False}

        before = copy.deepcopy(decoder.nodes["ip_input"])
        decoder._apply_window_activity = right_goes_dark
        real_set = srv._mv_set

        def note_and_set(*args, **kwargs):
            started.append(True)
            return real_set(*args, **kwargs)

        with mock.patch.object(srv, "MULTIVIEW_WINDOW_SETTLE", 0.0),                 mock.patch.object(srv, "_mv_set", note_and_set):
            body = self._switch(name, "left", NEW_WALLPLATE_IP).get_json()
        decoder._apply_window_activity = real

        self.assertFalse(body["ok"], body)
        self.assertIn("NO ACTIVE VIDEO", body["status"])
        self.assertIn("right (960x544)", body["regressed_windows"])
        self.assertEqual(self._saved(name)["left"], ENCODER_IP,
                         "a switch that darkened another window was saved")
        self.assertEqual(decoder.nodes["ip_input"], before)

    def test_a_rollback_is_not_failed_by_the_stream_status_it_cannot_control(self):
        """`video.hdcp` is a property of the picture, not of our write.

        The restore captured the whole subframe list, read-only status included,
        and compared it back. A rollback taken while a window was dark recorded
        `hdcp: none`; once the picture returned the device reported `2.2`, and a
        restore that had worked exactly reported itself incomplete. Measured on
        the bench twice, identically, against a decoder that had in fact been
        restored.

        The rebind has to succeed here and the window has to fail to light,
        because that is the path where the restored snapshot contains subframes
        at all.
        """
        name = self._commission()
        decoder = self.devices[DECODER_IP]
        real = decoder._apply_window_activity
        started = []

        def dark_but_protected(multiviews):
            real(multiviews)
            for entry in multiviews:
                for subframe in entry.get("subframes") or []:
                    # HDCP only appears once the transaction is under way, so
                    # the snapshot records `none` and the restore reads `2.2` --
                    # which is the whole point. Reporting it from the start would
                    # capture and compare the same value and prove nothing.
                    # Present throughout, so the snapshot captures it -- and
                    # it CHANGES once the transaction is under way, which is the
                    # whole point. A field that is absent when the snapshot is
                    # taken is never compared, and proves nothing.
                    subframe.setdefault("video", {})["hdcp"] = (
                        "2.2" if started else "none")
                    if subframe.get("name", "").startswith("left"):
                        subframe["video"]["input"] = {"active": False}
                        subframe["video"]["output"] = {"active": False}

        decoder._apply_window_activity = dark_but_protected
        real_snapshot = srv._snapshot_fields

        def snapshot_then_change(entries):
            # The snapshot is the boundary: everything it captured is what the
            # rollback will compare against, so HDCP appears immediately after
            # it and not before.
            captured = real_snapshot(entries)
            started.append(True)
            return captured

        with mock.patch.object(srv, "MULTIVIEW_WINDOW_SETTLE", 0.0),                 mock.patch.object(srv, "_snapshot_fields", snapshot_then_change):
            body = self._switch(name, "left", ENCODER2_IP).get_json()
        decoder._apply_window_activity = real

        self.assertFalse(body["ok"], body)
        self.assertIn("NO ACTIVE VIDEO", body["status"])
        self.assertTrue(body["rollback"], "nothing was rolled back at all")
        self.assertTrue(any(entry["step"].startswith("Bind")
                            for entry in body["rollback"]),
                        "the subframe rebind was not among the restores: %s"
                        % body["rollback"])
        self.assertNotIn("ROLLBACK INCOMPLETE", body["status"], body["rollback"])
        self.assertTrue(all(entry["verified"] for entry in body["rollback"]),
                        body["rollback"])

    def test_switching_is_refused_while_video_wall_is_on(self):
        name = self._commission()
        decoder = self.devices[DECODER_IP]
        decoder.nodes["hdmi_output"][0]["video"]["output"]["wall"]["enabled"] = True
        response = self._switch(name, "left", NEW_WALLPLATE_IP)
        self.assertEqual(response.status_code, 409)
        self.assertIn("Video Wall", response.get_json()["error"])


# ==========================================================================
# Recalling after the world moved
# ==========================================================================

class RecallAfterDriftTests(MultiviewTestBase):
    """A saved Multiview says what was wanted. Recall has to make it true now.

    Between saving and showing, anything can have changed: an encoder can be
    readdressed, repurposed or switched off, the decoder's inputs can be
    repointed by another operator or another product, and the Multiview object
    itself can be edited outside OmniSuite. A recall that replayed the stored
    plan would produce a display that disagrees with every one of those, and
    would do it silently.

    So recall reads the live state and reconciles against it. What the preset
    supplies is the *intent* -- which source, in which window -- and everything
    else is recomputed. These tests move the world after the save and assert on
    what actually reaches the devices.
    """

    def setUp(self):
        super().setUp()
        self.dec = self.decoder()
        for ip in (ENCODER_IP, ENCODER2_IP, NEW_WALLPLATE_IP, PLAIN_111_IP):
            self.encoder(ip)

    def _store(self, layout="side-by-side", assignments=None, name="Pair"):
        assignments = assignments or {"left": ENCODER_IP, "right": ENCODER2_IP}
        body = self.save(layout=layout, name=name,
                         assignments=assignments).get_json()
        self.assertTrue(body["ok"], body)
        return self.offer(body["plan"]["object_name"])

    def _session2_address(self, ip):
        return next(s for s in self.devices[ip].nodes["sessions"]
                    if s["name"] == "session2")["video"]["stream"][
                        "destination_address"]

    def _readdress(self, ip, address):
        session = next(s for s in self.devices[ip].nodes["sessions"]
                       if s["name"] == "session2")
        session["video"]["stream"]["destination_address"] = address

    def _inputs(self):
        return {i["name"]: i for i in self.dec.nodes["ip_input"]}

    def _windows(self, name):
        body = self.client.get(f"/api/multiview/state?ip={DECODER_IP}").get_json()
        view = next(v for v in body["multiviews"] if v["name"] == name)
        return {w["cell"]: w for w in view["subframes"]}

    # ---- the source moved ------------------------------------------------
    def test_a_source_readdressed_after_the_save_is_recalled_at_its_new_address(self):
        """The stored address is a record of the past, not an instruction.

        Re-addressing an encoder is an ordinary thing to do between building a
        preset and showing it, and a recall that subscribed to the address the
        preset remembered would produce a permanently black window while
        reporting success. Recall reads each source's session live, so the
        window follows the device.
        """
        name = self._store()
        self._readdress(ENCODER_IP, "233.252.0.201")
        body = self.recall(name).get_json()
        self.assertTrue(body["ok"], body)
        left = self._windows(name)["left"]
        self.assertEqual(left["multicast"], "233.252.0.201")
        self.assertTrue(left["subscribed"])
        self.assertTrue(left["input_active"])

    def test_a_readdressed_source_is_shown_as_unknown_until_the_cache_catches_up(self):
        """Measured, and worth stating plainly rather than papering over.

        Recall reads sources live, but *naming* the encoder behind a window is
        answered from the discovery cache, because reading every encoder when
        the page opens is exactly the cost the lazy architecture exists to
        avoid. So between re-addressing a source and the next scan, the window
        is correctly subscribed and correctly drawn, and the page says it does
        not know which encoder it is -- rather than naming the wrong one.

        What is never lost is the intent: `saved_source_ip` still says which
        source the preset asked for.
        """
        name = self._store()
        self._readdress(ENCODER_IP, "233.252.0.201")
        self.assertTrue(self.recall(name).get_json()["ok"])
        left = self._windows(name)["left"]
        self.assertIsNone(left["source"])
        self.assertEqual(left["source_origin"], "unknown")
        self.assertEqual(left["saved_source_ip"], ENCODER_IP)
        self.assertEqual(left["health"], "live")

        # And once discovery has seen the new address, it names it again --
        # nothing else has to happen, and no recall is needed.
        refreshed = [dict(d) for d in _devices()]
        for device in refreshed:
            if device.get("ip") == ENCODER_IP:
                device["session2_video_mcast"] = "233.252.0.201"
        with mock.patch.object(srv, "_load_cache", lambda: refreshed):
            left = self._windows(name)["left"]
        self.assertEqual(left["source"]["ip"], ENCODER_IP)
        self.assertEqual(left["source_origin"], "subscription")

    def test_two_sources_that_swapped_addresses_are_not_crossed_over(self):
        """Identity is the device, not the address it happened to hold."""
        name = self._store()
        first, second = (self._session2_address(ENCODER_IP),
                         self._session2_address(ENCODER2_IP))
        self._readdress(ENCODER_IP, second)
        self._readdress(ENCODER2_IP, first)
        self.assertTrue(self.recall(name).get_json()["ok"])
        windows = self._windows(name)
        self.assertEqual(windows["left"]["multicast"], second)
        self.assertEqual(windows["right"]["multicast"], first)
        self.assertNotEqual(windows["left"]["ip_input"],
                            windows["right"]["ip_input"])

    def test_a_source_that_went_offline_is_reported_rather_than_shown_black(self):
        name = self._store()
        self.devices[ENCODER_IP].unreachable = True
        body = self.recall(name).get_json()
        self.assertFalse(body["ok"], body)
        self.assertIn(ENCODER_IP, json.dumps(body))
        # Nothing half-applied: the other source is not left mid-preparation.
        self.assertTrue(all(not i["enabled"] or i["name"] not in mv.WINDOW_IP_INPUTS
                            or i["multicast"]["address"]
                            for i in self.dec.nodes["ip_input"]))

    def test_an_encoder_repurposed_after_the_save_is_prepared_again(self):
        """Encoder 2 is shared, and another product may have taken it back."""
        name = self._store()
        self.assertTrue(self.recall(name).get_json()["ok"])
        encoder = next(e for e in self.devices[ENCODER_IP].nodes["vc2"]
                       if e["name"] == "vc2_encoder2")
        encoder["input"] = ""
        encoder["scaler"]["width"] = 640
        encoder["scaler"]["height"] = 360
        body = self.recall(name).get_json()
        self.assertTrue(body["ok"], body)
        self.assertEqual(encoder["input"], "hdmi_input1")
        window = next(w for w in body["plan"]["windows"] if w["cell"] == "left")
        self.assertEqual("%sx%s" % (encoder["scaler"]["width"],
                                    encoder["scaler"]["height"]),
                         window["scaler_format"])

    def test_a_session_switched_off_after_the_save_is_switched_back_on(self):
        name = self._store()
        self.assertTrue(self.recall(name).get_json()["ok"])
        session = next(s for s in self.devices[ENCODER_IP].nodes["sessions"]
                       if s["name"] == "session2")
        session["video"]["stream"]["enabled"] = False
        self.assertTrue(self.recall(name).get_json()["ok"])
        self.assertTrue(session["video"]["stream"]["enabled"])

    # ---- the decoder moved -----------------------------------------------
    def test_a_pool_input_repointed_by_someone_else_is_taken_back(self):
        name = self._store()
        self.assertTrue(self.recall(name).get_json()["ok"])
        used = self._windows(name)["left"]["ip_input"]
        self._inputs()[used]["multicast"]["address"] = "233.252.0.240"
        body = self.recall(name).get_json()
        self.assertTrue(body["ok"], body)
        self.assertEqual(self._windows(name)["left"]["multicast"],
                         self._session2_address(ENCODER_IP))

    def test_a_pool_input_disabled_by_someone_else_is_re_enabled(self):
        name = self._store()
        self.assertTrue(self.recall(name).get_json()["ok"])
        used = self._windows(name)["left"]["ip_input"]
        self._inputs()[used]["enabled"] = False
        self.assertTrue(self.recall(name).get_json()["ok"])
        self.assertTrue(self._inputs()[used]["enabled"])

    def test_the_audio_input_moved_by_someone_else_is_put_back(self):
        name = self._store()
        self.assertTrue(self.recall(name).get_json()["ok"])
        hdmi = self.dec.nodes["hdmi_output"][0]
        wanted = next(s for s in self.devices[ENCODER_IP].nodes["sessions"]
                      if s["name"] == "session1")["audio"]["stream"][
                          "destination_address"]
        hdmi["audio"]["input"] = "ip_input5"
        body = self.recall(name).get_json()
        self.assertTrue(body["ok"], body)
        entry = self._inputs()[hdmi["audio"]["input"]]
        self.assertEqual(entry["multicast"]["address"], wanted)

    def test_a_subframe_deleted_outside_omnisuite_is_rebuilt_by_recall(self):
        name = self._store()
        self.assertTrue(self.recall(name).get_json()["ok"])
        obj = next(o for o in self.dec.nodes["multiview"] if o["name"] == name)
        removed = obj["subframes"].pop()
        self.assertTrue(self.recall(name).get_json()["ok"])
        obj = next(o for o in self.dec.nodes["multiview"] if o["name"] == name)
        self.assertIn(removed["name"], [s["name"] for s in obj["subframes"]])

    def _add_stray(self, name):
        obj = next(o for o in self.dec.nodes["multiview"] if o["name"] == name)
        obj["subframes"].append({"name": "stray (320x180)", "input": "ip_input6",
                                 "x": 0, "y": 0, "anchor": "top left",
                                 "priority": 9,
                                 "video": {"input": {"active": False},
                                           "output": {"active": False}}})
        return "stray (320x180)"

    def _subframe_names(self, name):
        obj = next(o for o in self.dec.nodes["multiview"] if o["name"] == name)
        return [s["name"] for s in obj["subframes"]]

    def test_a_stray_subframe_added_outside_omnisuite_survives_recall_and_is_reported(self):
        """Geometry belongs to Save. Recall configures resources and reports.

        A window someone added on the device is still a window, and silently
        deleting it during a recall would be OmniSuite overruling a change it
        did not make. Recall leaves it alone -- and does not pretend it is not
        there: it comes back as a window with no stream that never locked, so
        the page shows it and the log names it.
        """
        name = self._store()
        self.assertTrue(self.recall(name).get_json()["ok"])
        stray = self._add_stray(name)
        with mock.patch.object(srv, "MULTIVIEW_WINDOW_SETTLE", 0.0):
            body = self.recall(name).get_json()
        self.assertTrue(body["ok"], body)
        self.assertIn(stray, self._subframe_names(name))
        self.assertIn(stray, [w["subframe"] for w in body["unlocked_windows"]])
        self.assertIn("stray", self._windows(name))

    def test_saving_over_it_is_what_prunes_a_stray_subframe(self):
        """The other half of the same rule, so neither can drift alone."""
        name = self._store()
        self.assertTrue(self.recall(name).get_json()["ok"])
        stray = self._add_stray(name)
        body = self.save(layout="side-by-side", name="Pair", object_name=name,
                         update_existing=True,
                         assignments={"left": ENCODER_IP,
                                      "right": ENCODER2_IP}).get_json()
        self.assertTrue(body["ok"], body)
        self.assertEqual(body["plan"]["object_name"], name)
        self.assertNotIn(stray, self._subframe_names(name))

    # ---- what the page shows afterwards -----------------------------------
    def test_recall_leaves_every_window_resolvable_to_its_live_subscription(self):
        """§17's actual product requirement, stated as one assertion.

        After a recall the page must be able to say, for every window, which
        encoder it is watching -- derived from the decoder's subscriptions
        rather than from the record that was saved.
        """
        name = self._store(layout="2x2", assignments={
            "top_left": ENCODER_IP, "top_right": ENCODER2_IP,
            "bottom_left": NEW_WALLPLATE_IP, "bottom_right": PLAIN_111_IP})
        self.assertTrue(self.recall(name).get_json()["ok"])
        windows = self._windows(name)
        self.assertEqual(len(windows), 4)
        for cell, window in windows.items():
            self.assertEqual(window["source_origin"], "subscription", cell)
            self.assertTrue(window["subscribed"], cell)
            self.assertIsNotNone(window["source"], cell)
            self.assertFalse(window["diverged"], cell)

    def test_a_window_the_decoder_is_not_receiving_is_not_claimed_as_live(self):
        name = self._store()
        self.assertTrue(self.recall(name).get_json()["ok"])
        used = self._windows(name)["left"]["ip_input"]
        self._inputs()[used]["enabled"] = False
        left = self._windows(name)["left"]
        self.assertFalse(left["subscribed"])
        self.assertEqual(left["health"], "not subscribed")
        self.assertEqual(left["saved_source_ip"], ENCODER_IP,
                         "the record of what was wanted was lost as well")

    def test_drift_is_reported_when_the_display_disagrees_with_the_preset(self):
        """Someone switched a window from the front panel. Say so."""
        name = self._store()
        self.assertTrue(self.recall(name).get_json()["ok"])
        used = self._windows(name)["left"]["ip_input"]
        self._inputs()[used]["multicast"]["address"] = self._session2_address(
            NEW_WALLPLATE_IP)
        left = self._windows(name)["left"]
        self.assertEqual(left["source"]["ip"], NEW_WALLPLATE_IP)
        self.assertEqual(left["saved_source_ip"], ENCODER_IP)
        self.assertTrue(left["diverged"],
                        "a window showing something else was reported as matching")


# ==========================================================================
# Source eligibility
# ==========================================================================

class SourceEligibilityTests(MultiviewTestBase):

    def _sources(self):
        body = self.client.get("/api/multiview/sources").get_json()
        return ({s["ip"]: s for s in body["sources"]},
                {s["ip"]: s for s in body["excluded"]})

    def test_a_reachable_configured_encoder_is_ready(self):
        self.encoder(ENCODER_IP)
        sources, _excluded = self._sources()
        self.assertEqual(sources[ENCODER_IP]["status"], mv.SOURCE_READY)
        self.assertEqual(sources[ENCODER_IP]["status_label"], "Ready")
        self.assertEqual(sources[ENCODER_IP]["reason"], "")

    def test_an_offline_encoder_is_not_offered(self):
        # Registered nowhere, so the preflight does not answer for it.
        sources, excluded = self._sources()
        self.assertNotIn(ENCODER_IP, sources)
        self.assertEqual(excluded[ENCODER_IP]["status"], mv.SOURCE_INELIGIBLE)
        self.assertEqual(excluded[ENCODER_IP]["reason"], "Offline")

    def test_an_encoder_with_no_multicast_needs_configuration_not_exclusion(self):
        devices = _devices()
        for device in devices:
            if device["ip"] == ENCODER_IP:
                device["session2_video_mcast"] = ""
        with mock.patch.object(srv, "_load_cache", lambda: devices):
            self.encoder(ENCODER_IP)
            sources, excluded = self._sources()
        self.assertIn(ENCODER_IP, sources, "it was hidden instead of explained")
        entry = sources[ENCODER_IP]
        self.assertEqual(entry["status"], mv.SOURCE_CONFIGURATION_REQUIRED)
        self.assertEqual(entry["reason"], "Multicast configuration required")
        self.assertIn("generates its own default", entry["detail"])
        self.assertEqual(entry["action"], "configure_multicast")

    def test_an_encoder_without_the_codec_is_ineligible(self):
        devices = _devices()
        for device in devices:
            if device["ip"] == ENCODER_IP:
                device["codec"] = ""
        with mock.patch.object(srv, "_load_cache", lambda: devices):
            self.encoder(ENCODER_IP)
            sources, excluded = self._sources()
        self.assertNotIn(ENCODER_IP, sources)
        self.assertEqual(excluded[ENCODER_IP]["reason"], "No supported encoder")

    def test_the_excluded_wall_plate_is_still_excluded_by_identity(self):
        for ip in (OLD_WALLPLATE_IP, NEW_WALLPLATE_IP, PLAIN_111_IP):
            self.encoder(ip)
        sources, excluded = self._sources()
        self.assertIn("AT-OMNI-111-WP", excluded[OLD_WALLPLATE_IP]["reason"])
        self.assertIn(NEW_WALLPLATE_IP, sources, "HW-OMNI-E4111-WP must stay eligible")
        self.assertIn(PLAIN_111_IP, sources, "AT-OMNI-111 must stay eligible")

    def test_ready_sources_are_listed_before_ones_needing_work(self):
        devices = _devices()
        for device in devices:
            if device["ip"] == ENCODER_IP:
                device["session2_video_mcast"] = ""
        with mock.patch.object(srv, "_load_cache", lambda: devices):
            self.encoder(ENCODER_IP)
            self.encoder(ENCODER2_IP)
            body = self.client.get("/api/multiview/sources").get_json()
        statuses = [s["status"] for s in body["sources"]]
        self.assertEqual(statuses, sorted(statuses, key=lambda s: s != mv.SOURCE_READY))
        self.assertEqual(body["ready"], 1)

    def test_listing_sources_reads_no_device_configuration(self):
        encoder = self.encoder(ENCODER_IP)
        self.client.get("/api/multiview/sources")
        self.assertEqual(encoder.writes, [],
                         "the source list talked to a device")

    def test_an_offline_decoder_is_not_offered(self):
        devices = _devices() + [
            {"ip": "192.0.2.19", "mac": "00:00:5E:00:53:19", "role": "decoder",
             "type": "Decoder", "model": "hw-omni-d4511", "hostname": "dec-gone"}]
        with mock.patch.object(srv, "_load_cache", lambda: devices):
            self.decoder()
            self.decoder(ip=DECODER2_IP)     # answers, so it is not the hidden one
            body = self.client.get("/api/multiview/decoders?probe=1").get_json()
        offered = {d["ip"] for d in body["decoders"]}
        self.assertIn(DECODER_IP, offered)
        self.assertIn(DECODER2_IP, offered)
        # Registered nowhere, so it does not answer the preflight.
        self.assertNotIn("192.0.2.19", offered)
        self.assertEqual(body["hidden"], 1)

    def test_a_decoder_barred_by_an_interlock_is_still_listed(self):
        # The operator can turn Video Wall off, and would never guess that was
        # the problem from an empty list.
        self.decoder(hdmi=_hdmi_output(wall=True))
        body = self.client.get("/api/multiview/decoders?probe=1").get_json()
        entry = next(d for d in body["decoders"] if d["ip"] == DECODER_IP)
        self.assertEqual(entry["reachable"], True)
        state = self.client.get(f"/api/multiview/state?ip={DECODER_IP}").get_json()
        self.assertTrue(state["interlocks"])
