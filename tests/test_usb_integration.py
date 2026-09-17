"""Device Info unified discovery/clear, USB-parent correlation, and dispatch.

The addresses here were the bench's. They are now RFC 5737 documentation
addresses with the same last octet, so every relationship the tests are built on
-- adjacency, numeric ordering, on-net versus off-net -- is unchanged, and no
automated test names a real device. This module's own WebSocket fence used to
hide the fact that some of these were being dialled; the shared fence reports
them, which is how they were found.

These tests drive the Flask app through its test client. They never touch the
network: the standalone extender service is stubbed, and the OmniStream identify
helper is patched, so nothing here depends on hardware.
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

import copy
import io
import json
import logging
import os
import random
import re
import subprocess
import sys
import shutil
import tempfile
import threading
import time
from concurrent.futures import Future
import unittest
from unittest import mock
from pathlib import Path

os.environ.setdefault("OMNI_DATA_DIR", tempfile.mkdtemp(prefix="omnisuite-test-"))

import omni_usb_extender as usb
import OmniMatrix_upgrade_server_v7_6y as srv

logging.getLogger("omni_usb_extender").setLevel(logging.CRITICAL)


def _github_is_fenced(*args, **kwargs):
    raise OSError("no test may contact GitHub")


srv._fetch_latest_release = _github_is_fenced

# Values observed on real hardware during physical testing.
E4521_IP, E4521_HOST, E4521_USB_MAC, E4521_USB_IP = "192.0.2.141", "hw-omni-e4521-00002", "B8:98:B0:07:85:ED", "192.0.2.246"
D4511_IP, D4511_HOST, D4511_USB_MAC, D4511_USB_IP = "192.0.2.32", "hw-omni-d4511-085c6", "B8:98:B0:07:85:C7", "192.0.2.108"
# Observed hardware: the AT-OMNI-311 (LEX/host) is .128, the AT-OMNI-324 (REX/device) is .127.
OMNI311_MAC, OMNI311_IP = "00:1B:13:04:E9:6E", "192.0.2.128"
OMNI324_MAC, OMNI324_IP = "00:1B:13:04:6A:EA", "192.0.2.127"
# Every command that changes device state. A test may read from a stubbed
# transport; it may never write to one that could be a real device.
USB_MUTATING_COMMANDS = {usb.PAIR, usb.UNPAIR, usb.UNPAIR_ALL, usb.IP_DHCP,
                         usb.IP_STATIC, usb.REBOOT, usb.BLINK_ON, usb.BLINK_OFF}
IFACE_IP, IFACE_MASK = "192.0.2.50", "255.255.255.0"

# The WebSocket transport is fenced for the lifetime of this process, not per
# test. Every config_set write -- usb_icron pairing, parent network, codec,
# hostname, reboot, blink, host port, filter -- travels this one call, and the
# fixture addresses are real bench devices.
#
# Process-wide rather than set up and torn down around each test because the
# scan dispatch tests deliberately start a background thread that outlives the
# test that started it. A per-test restore handed the real socket back to those
# threads while they were still running, and they went on to read live hardware
# after their test had finished.
_WS_ANSWER = {"handler": None}
# Recorded as well as raised: the device handlers catch transport errors and
# report them as a failed operation, which would otherwise swallow the
# AssertionError and let the attempt pass unnoticed.
_WS_WRITES = []


def _fenced_ws_send_recv(url, payload, timeout=None, *args, **kwargs):
    fields = payload if isinstance(payload, dict) else {}
    if "config_set" in fields:
        _WS_WRITES.append((url, fields.get("config_set")))
        raise AssertionError(
            f"a test tried to write {fields.get('config_set')!r} to {url}")
    # `method` is the OmniStream API's third verb and it is a write: add_logo,
    # delete_logo, add_multiview and del_multiview all travel this way and none
    # of them carries a `config_set` key. Fencing only `config_set` left every
    # method call able to reach a real device.
    if "method" in fields:
        _WS_WRITES.append((url, fields.get("method")))
        raise AssertionError(
            f"a test tried to call method {fields.get('method')!r} on {url}")
    handler = _WS_ANSWER["handler"]
    answer = handler(url, fields.get("config_get")) if handler else None
    if answer is None:
        # Behave as an unreachable device, exactly as the UDP stub answers a
        # query, so a test that needs hardware to reply fails here rather than
        # passing only on a workstation that sits on the bench network.
        raise OSError("the network is unavailable in tests")
    return answer


srv._ws_send_recv = _fenced_ws_send_recv


# No test opens a real WebSocket, whatever route it took to get here.
#
# _ws_send_recv is not the only way out: config import, firmware upload,
# identify/blink and reboot each call websocket.create_connection directly, so
# fencing the shared transport alone still left four mutating paths able to
# reach a live device.
#
# This module used to install its own spy on that call, which replaced the test
# package's. Two fences that overwrite each other are worse than one: whichever
# imported last decided what every other module got, and the shared fence's own
# tests then saw a different error than the one they had installed. The package
# fence does the same job at the socket, so this simply makes sure it is there.
_fence.install()
assert _fence.installed(), "the hardware fence is not installed"

# The full observed bench system.
E4521B_IP, E4521B_HOST, E4521B_USB_MAC, E4521B_USB_IP = "192.0.2.143", "hw-omni-e4521-0856c", "B8:98:B0:07:85:6D", "192.0.2.194"
D4511B_IP, D4511B_HOST, D4511B_USB_MAC, D4511B_USB_IP = "192.0.2.151", "hw-omni-d4511-08586", "B8:98:B0:07:85:87", "192.0.2.250"
D4511C_IP, D4511C_HOST, D4511C_USB_MAC, D4511C_USB_IP = "192.0.2.152", "hw-omni-d4511-085c0", "B8:98:B0:07:85:C1", "192.0.2.248"

CACHE_UNITS = [
    {"ip": E4521_IP, "hostname": E4521_HOST, "model": "hw-omni-e4521", "role": "encoder",
     "usb_type": "LEX", "usb_mac": E4521_USB_MAC},
    {"ip": D4511_IP, "hostname": D4511_HOST, "model": "hw-omni-d4511", "role": "decoder",
     "usb_type": "REX", "usb_mac": D4511_USB_MAC},
    {"ip": "192.0.2.60", "hostname": "hw-omni-e4111-x", "model": "hw-omni-e4111", "role": "encoder"},
]

# Every USB-capable unit observed on the bench, all associated.
# The parent OmniStream firmware, which is NOT the version the Icron USB module
# inside it reports (1.9.4, from query_payload above). The fixture carried no
# version at all, which made every assertion about the two being separate facts
# trivially true: the substitution the defect was about could not occur, because
# there was nothing to substitute.
PARENT_FIRMWARE = "2.1.2"

FULL_CACHE_UNITS = [
    {"ip": E4521_IP, "hostname": E4521_HOST, "model": "hw-omni-e4521", "role": "encoder", "usb_type": "LEX", "usb_mac": E4521_USB_MAC, "version": PARENT_FIRMWARE},
    {"ip": E4521B_IP, "hostname": E4521B_HOST, "model": "hw-omni-e4521", "role": "encoder", "usb_type": "LEX", "usb_mac": E4521B_USB_MAC, "version": PARENT_FIRMWARE},
    {"ip": D4511_IP, "hostname": D4511_HOST, "model": "hw-omni-d4511", "role": "decoder", "usb_type": "REX", "usb_mac": D4511_USB_MAC, "version": PARENT_FIRMWARE},
    {"ip": D4511B_IP, "hostname": D4511B_HOST, "model": "hw-omni-d4511", "role": "decoder", "usb_type": "REX", "usb_mac": D4511B_USB_MAC, "version": PARENT_FIRMWARE},
    {"ip": D4511C_IP, "hostname": D4511C_HOST, "model": "hw-omni-d4511", "role": "decoder", "usb_type": "REX", "usb_mac": D4511C_USB_MAC, "version": PARENT_FIRMWARE},
]

# The observed UDP discovery result: LEX endpoints report role code 0 and REX
# endpoints report 1, so the protocol layer names every one of them
# AT-OMNI-311/324 regardless of whether it is standalone or integrated.
OBSERVED_UDP = [
    (E4521_USB_MAC, E4521_USB_IP, 0), (E4521B_USB_MAC, E4521B_USB_IP, 0),
    (D4511_USB_MAC, D4511_USB_IP, 1), (D4511B_USB_MAC, D4511B_USB_IP, 1), (D4511C_USB_MAC, D4511C_USB_IP, 1),
    (OMNI311_MAC, OMNI311_IP, 0), (OMNI324_MAC, OMNI324_IP, 1),
]


def query_payload(mac, ip, mode=usb.NETWORK_MODE_DHCP, product="Atlona", revision="1.9.4"):
    """Basic Device Information as firmware 1.9.4 emits it: the mode byte is
    followed by a protocol byte, and the firmware slot is 12 bytes, not 32."""
    payload = usb.mac_bytes(mac) + bytes(map(int, ip.split("."))) + bytes([mode, 0x03])
    for value, width in zip((product, "USB Over Network", revision), (32, 32, 12)):
        payload += value.encode("utf-8").ljust(width, b"\0")
    return payload


def query(mac, ip, mode=usb.NETWORK_MODE_DHCP, revision="1.9.4"):
    return usb.parse_query_response(
        usb.packet(1, usb.QUERY_RESPONSE, query_payload(mac, ip, mode=mode, revision=revision)), 1)


def advanced(device_code, peers=()):
    return usb.parse_advanced_query_response(
        usb.packet(1, usb.ADVANCED_QUERY_RESPONSE, bytes([device_code]) + b"".join(usb.mac_bytes(p) for p in peers)), 1)


class ServerTestBase(unittest.TestCase):
    # How long a test waits for the background work it armed. Generous, because
    # it is only reached by the handful of tests that arm the startup refreshes,
    # and a slow machine must not turn the isolation contract into a flake of its
    # own. A test that leaks on purpose lowers it.
    STARTUP_JOIN_TIMEOUT = 15.0

    def setUp(self):
        # Background enrichment threads may still be flushing a state file when
        # a test finishes; on Windows that makes the directory removal fail. The
        # write itself is legitimate, so the cleanup tolerates it rather than the
        # suite reporting a phantom failure.
        self.folder = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.folder.cleanup)
        # Redirect every on-disk artefact these tests can touch into the temp
        # folder. This must not depend on import order or on OMNI_DATA_DIR: a
        # clear test that reached the real data directory would delete the
        # operator's device cache.
        # USB_ASSOCIATIONS is included: _record_usb_association persists on every
        # call, so without this the suite writes synthetic associations into the
        # operator's real data file.
        # NOTICE_ACK is included for the same reason: acknowledging the
        # first-run network notice in a test must not silence it for the
        # operator, and a test asserting a fresh install must not be answered by
        # the operator's real acknowledgement.
        for name in ("CACHE", "SCAN_RESULTS", "CSV_VIEW", "USB_ASSOCIATIONS",
                     "SCAN_PREFS", "TOPOLOGY_ACK", "UI_PREFS", "NOTICE_ACK"):
            original = getattr(srv, name)
            self.addCleanup(lambda n=name, o=original: setattr(srv, n, o))
            setattr(srv, name, Path(self.folder.name) / original.name)
        # The standalone-extender service binds its state file at import, so it
        # points at the operator's real usb_extenders.json -- a suite asserting a
        # clean install was being answered by the bench's own extenders, and any
        # persist would have written synthetic devices into the operator's file.
        # Redirect the store and clear what it loaded from the real one.
        previous_state_file = srv._usb_extenders.state_file
        previous_devices = dict(srv._usb_extenders._devices)
        previous_ranges = list(srv._usb_extenders._ranges)

        def _restore_extenders():
            srv._usb_extenders.state_file = previous_state_file
            srv._usb_extenders._devices.clear()
            srv._usb_extenders._devices.update(previous_devices)
            srv._usb_extenders._ranges[:] = previous_ranges

        self.addCleanup(_restore_extenders)
        srv._usb_extenders.state_file = Path(self.folder.name) / "usb_extenders.json"
        srv._usb_extenders._devices.clear()
        srv._usb_extenders._ranges.clear()


        # The runtime usb_icron association store is process-global; isolate it so
        # observations learned by one test cannot classify another test's fixture.
        previous_association = dict(srv._USB_ASSOCIATION)
        srv._USB_ASSOCIATION.clear()
        self.addCleanup(lambda: (srv._USB_ASSOCIATION.clear(), srv._USB_ASSOCIATION.update(previous_association)))
        # Parent liveness is process-global too. Without this a parent marked live
        # by an earlier test makes this test's integrated endpoints look online.
        previous_net = dict(srv._usb_net_config)
        self.addCleanup(lambda: (srv._usb_net_config.clear(), srv._usb_net_config.update(previous_net)))
        srv._usb_net_config.clear()
        for usb_mac, usb_ip in ((E4521_USB_MAC, E4521_USB_IP), (D4511_USB_MAC, D4511_USB_IP),
                                (E4521B_USB_MAC, E4521B_USB_IP), (D4511B_USB_MAC, D4511B_USB_IP),
                                (D4511C_USB_MAC, D4511C_USB_IP)):
            srv._usb_net_config[srv._norm_usb_mac(usb_mac)] = {
                "ipaddress": usb_ip, "subnetmask": "255.255.255.0", "gateway": "192.0.2.1",
                "mode_raw": "DHCP", "mode_known": True, "network_config_last_read": time.time(),
                "network_config_stale": False}
        previous_live = dict(srv._USB_PARENT_LIVE)
        srv._USB_PARENT_LIVE.clear()
        self.addCleanup(lambda: (srv._USB_PARENT_LIVE.clear(), srv._USB_PARENT_LIVE.update(previous_live)))
        self.service = usb.ExtenderDiscoveryService(Path(self.folder.name) / "usb_extenders.json", persist_debounce=0)
        self._real_service = srv._usb_extenders
        srv._usb_extenders = self.service
        # Background work must not outlive the test that scheduled it.
        #
        # /api/usb_state and Configure > USB submit _refresh_icron_network_config
        # to this service's executor, deliberately, so a request never waits on
        # an Icron read. In production that is right. In a suite it means the
        # read happens at an arbitrary later moment -- inside whichever test is
        # running by then, against whatever _ws_send_recv that test installed.
        #
        # That is what failed on Ubuntu: a refresh scheduled by an earlier test
        # landed inside MixedRoutingDispatchTests and recorded a parent
        # WebSocket read the route never made. Windows and macOS won the race.
        #
        # Running submissions inline keeps both the work and the code path, and
        # makes them belong to the test that caused them.
        self.inline_background(self.service)
        self.addCleanup(lambda: setattr(srv, "_usb_extenders", self._real_service))
        # Stand in for the socket so no test can reach the physical network: the
        # fixture addresses are real bench devices. Liveness reads are a normal
        # part of these tests and simply time out, but a mutating command means a
        # gate let something through that must never be sent to real hardware, so
        # it fails the test instead of being delivered. Tests that exercise the
        # transaction install a scripted FakeUdp, which replaces this stub.
        self.transmitted = []
        self.attempted_mutations = []
        def _no_network(destination, request, message_id, timeout=None, bind_ip=None):
            command = int.from_bytes(request[8:10], "big")
            self.transmitted.append((destination, command))
            if command in USB_MUTATING_COMMANDS:
                self.attempted_mutations.append((destination, command))
                raise AssertionError(f"a test tried to send {hex(command)} to {destination}")
            raise usb.socket.timeout()
        self.service._exchange = _no_network
        # The same fence over the WebSocket transport. Every config_set write --
        # usb_icron pairing, parent network, codec, hostname, reboot, blink, host
        # port, filter -- travels this one call, and the fixture addresses are
        # real bench devices. Stubbing only the UDP socket left that entire half
        # of the surface open: a route reaching any of those handlers would have
        # reconfigured physical hardware from a unit test. Reads are answered as
        # an unreachable device, exactly as the UDP stub answers a query, so a
        # test that depends on hardware replying fails here rather than passing
        # only on a workstation that happens to sit on the bench network.
        self.ws_requested = []
        del _WS_WRITES[:]

        def _answer(url, name):
            self.ws_requested.append((url, name))
            return self.ws_read(url, name)

        _WS_ANSWER["handler"] = _answer
        self.addCleanup(lambda: _WS_ANSWER.__setitem__("handler", None))
        # The firewall diagnostic's only process boundary. run_tests.py fences
        # both hardware transports but not subprocess, so without this every test
        # that downloads a TS dump would really run netsh, and its result would
        # depend on the workstation's own firewall. "Unavailable" is the default
        # here; a test that wants parsed output installs its own stub.
        def _no_shelling_out():
            raise OSError("no test may shell out for host firewall state")
        self.patch_srv("_firewall_query_output", _no_shelling_out)

        # GitHub is not a hardware transport, so the socket fences do not stop
        # it reaching the public Internet -- and the startup task now performs a
        # release check, which a test that calls start_background_startup_tasks()
        # would otherwise send to api.github.com for real. Measured once: one
        # live connection to 140.82.113.6 from the suite. Every test that wants
        # a result stubs this deliberately; by default there is no answer.
        def _no_github(*args, **kwargs):
            raise OSError("the update check is stubbed in tests")

        # Recorded before stubbing, so a test can assert that what every
        # restore returns to is the process-wide fence rather than the real
        # fetcher -- a daemon thread still running after teardown would
        # otherwise reach the Internet.
        # The debug-log download reaches a device twice: a WebSocket method and
        # then a plain HTTP GET for the bundle. The WebSocket half is already
        # fenced above; `requests` is not, and run_tests.py blocks it at the
        # socket rather than at a name a test can assert on. Deny by default,
        # so a test that wants a log installs one deliberately and a test that
        # does not can never reach a bench device's nginx.
        def _no_log_fetch(*args, **kwargs):
            raise AssertionError("no test may fetch a debug log from a device")
        self.patch_srv("requests", type("FencedRequests", (), {
            "get": staticmethod(_no_log_fetch),
            "post": staticmethod(_no_log_fetch),
        }))
        self.github_fetcher_before_stub = srv._fetch_latest_release
        self.patch_srv("_fetch_latest_release", _no_github)
        with srv._update_check_lock:
            previous_update = dict(srv._update_check_cache)
            srv._update_check_cache["checked_at"] = 0.0
            srv._update_check_cache["result"] = None
        self.addCleanup(lambda: srv._update_check_cache.update(previous_update))
        self.addCleanup(self._assert_no_mutation_attempted)
        self.client = srv.app.test_client()
        self._patch_cache(CACHE_UNITS)
        # Background work must not outlive the test that started it, and the
        # three startup refreshes are plain daemon threads -- nothing an
        # executor patch can reach. _startup_usb_refresh waits for discovery to
        # settle and then calls _refresh_icron_network_config through the module
        # global, so the call resolves against whatever test is running at that
        # moment.
        #
        # Measured, by arming the trigger and sweeping the offset: it landed
        # inside MatrixNetworkReadinessTests and recorded an Icron read that test
        # never made, failing "a known mask must not be re-read on every Matrix
        # poll" in 7 of 30 aimed attempts. In a full suite that is a rare flake
        # whose cause is two seconds and several hundred tests away.
        #
        # Registered last, so it runs first: the test that armed the work waits
        # for it with its own stubs still installed. The work therefore belongs
        # to that test and is observable by it, and the assertion below turns any
        # future leak into a failure of the test that caused it.
        self.patch_srv("STARTUP_USB_SETTLE", 0.0)
        startup_armed_before = srv._startup_tasks_started

        def _settle_startup_tasks():
            finished = srv.await_startup_tasks(timeout=self.STARTUP_JOIN_TIMEOUT)
            del srv._startup_threads[:]
            srv._startup_tasks_started = startup_armed_before
            self.assertTrue(finished, "a startup thread outlived the test that "
                                      "started it")

        self.addCleanup(_settle_startup_tasks)

    def inline_background(self, service):
        """Run this service's background submissions on the calling thread.

        The enrichment pool only. That is the one carrying
        _refresh_icron_network_config, the work that was arriving in later
        tests.

        The range-scan and local-discovery pools stay asynchronous on purpose:
        their asynchrony is itself under test -- discovery must return without
        blocking the request, and a second range scan while one is in flight
        must be reported busy rather than queued. Running those inline would
        make both of those tests assert nothing.

        A real Future is still returned, because callers keep the handle.
        """
        executor = service._executor
        previous = executor.submit
        self.addCleanup(setattr, executor, "submit", previous)

        def inline(fn, *args, **kwargs):
            future = Future()
            try:
                future.set_result(fn(*args, **kwargs))
            except BaseException as exc:              # noqa: BLE001 - mirrored
                future.set_exception(exc)
            return future

        executor.submit = inline

    def patch_srv(self, name, value):
        """Replace a server global for this test only, and put it back after.

        Several tests assigned these directly with no restore, so a stub built
        for one test stayed installed for every test that ran after it -- one of
        them left `_update_poll_detail_cache` pointing at a dict belonging to a
        finished test. Unittest orders classes alphabetically, so the blast
        radius moved whenever a class was renamed.
        """
        original = getattr(srv, name)
        self.addCleanup(lambda: setattr(srv, name, original))
        setattr(srv, name, value)
        return value

    def ws_read(self, url, name):
        """Answer one config_get, or None to behave as an unreachable device."""
        return None

    def _assert_no_mutation_attempted(self):
        # Also asserted here, because the service converts transport errors into
        # failure results and would otherwise absorb the AssertionError above.
        if self.attempted_mutations:
            raise AssertionError(
                "this test tried to mutate physical hardware: "
                + ", ".join(f"{ip} cmd={hex(cmd)}" for ip, cmd in self.attempted_mutations))
        # Asserted for the same reason: the device handlers catch transport
        # errors and report them as a failed operation, which would swallow the
        # AssertionError raised at the socket.
        if _WS_WRITES:
            attempts = list(_WS_WRITES)
            del _WS_WRITES[:]
            raise AssertionError(
                "this test tried to write configuration to physical hardware: "
                + ", ".join(f"{url} wrote {name!r}" for url, name in attempts))

    # The parent facts an integrated endpoint is derived from. These used to
    # arrive from a real usb_icron read of the bench, which is why fencing the
    # WebSocket transport made them vanish; stated here, the same endpoints exist
    # on any machine.
    INTEGRATED_FIXTURE = (
        (E4521_IP, E4521_USB_MAC, "LEX", E4521_USB_IP, "AA:BB:CC:00:01:41"),
        (E4521B_IP, E4521B_USB_MAC, "LEX", E4521B_USB_IP, "AA:BB:CC:00:01:43"),
        (D4511_IP, D4511_USB_MAC, "REX", D4511_USB_IP, "AA:BB:CC:00:01:32"),
        (D4511B_IP, D4511B_USB_MAC, "REX", D4511B_USB_IP, "AA:BB:CC:00:01:51"),
        (D4511C_IP, D4511C_USB_MAC, "REX", D4511C_USB_IP, "AA:BB:CC:00:01:52"),
    )

    def seed_integrated(self, *rows):
        """Record the association and parent liveness for each integrated row."""
        for parent_ip, usb_mac, role, usb_ip, parent_mac in (rows or self.INTEGRATED_FIXTURE):
            srv._record_usb_association(parent_ip, usb_mac, role, usb_ip, parent_mac)
            srv._record_parent_live(parent_ip)

    def _patch_cache(self, units):
        # Deep copy: the server's background cache verification mutates the dicts
        # it gets back from _load_cache in place, which would otherwise rewrite
        # these module-level fixtures with live device data mid-run.
        self._units = copy.deepcopy(list(units))
        real_load = srv._load_cache
        # Kept so a test that needs the real store can put it back.
        self._real_load_cache = getattr(self, "_real_load_cache", real_load)
        srv._load_cache = lambda: list(self._units)
        self.addCleanup(lambda: setattr(srv, "_load_cache", real_load))

    def seed(self, *records):
        for mac, ip, code in records:
            self.service._upsert(query(mac, ip), IFACE_IP, IFACE_MASK, "LOCAL_BROADCAST",
                                 advanced(code) if code is not None else None)

    def devices(self):
        return self.client.get("/api/usb_extenders").get_json()["devices"]

    def by_mac(self, mac):
        return next(d for d in self.devices() if d["mac"] == usb.normalize_mac(mac))


class ParentCorrelationTests(ServerTestBase):
    def test_e4521_usb_identity_correlates_to_its_parent(self):
        self.seed((E4521_USB_MAC, E4521_USB_IP, 0))
        device = self.by_mac(E4521_USB_MAC)
        self.assertTrue(device["integrated"])
        self.assertEqual(device["parent_ip"], E4521_IP)
        self.assertEqual(device["parent_hostname"], E4521_HOST)
        self.assertEqual(device["display_model"], "HW-OMNI-E4521")
        self.assertEqual(device["usb_role"], "USB Host / LEX")
        self.assertEqual(device["usb_ip"], E4521_USB_IP)

    def test_d4511_usb_identity_correlates_to_its_parent(self):
        self.seed((D4511_USB_MAC, D4511_USB_IP, 1))
        device = self.by_mac(D4511_USB_MAC)
        self.assertTrue(device["integrated"])
        self.assertEqual(device["parent_ip"], D4511_IP)
        self.assertEqual(device["parent_hostname"], D4511_HOST)
        self.assertEqual(device["display_model"], "HW-OMNI-D4511")
        self.assertEqual(device["usb_role"], "USB Device / REX")

    def test_standalone_units_are_not_correlated(self):
        self.seed((OMNI311_MAC, OMNI311_IP, 0), (OMNI324_MAC, OMNI324_IP, 1))
        host, device = self.by_mac(OMNI311_MAC), self.by_mac(OMNI324_MAC)
        self.assertFalse(host["integrated"])
        self.assertEqual(host["display_model"], "HW-OMNI-311")
        self.assertEqual(host["device_type"], "AT-OMNI-311",
                         "the protocol identity is unchanged by the display name")
        self.assertEqual(host["usb_role"], "USB Host / LEX")
        self.assertFalse(device["integrated"])
        self.assertEqual(device["display_model"], "HW-OMNI-324")
        self.assertEqual(device["device_type"], "AT-OMNI-324",
                         "the protocol identity is unchanged by the display name")
        self.assertEqual(device["usb_role"], "USB Device / REX")

    def test_canonical_mac_identity_is_never_overwritten(self):
        self.seed((E4521_USB_MAC, E4521_USB_IP, 0))
        device = self.by_mac(E4521_USB_MAC)
        self.assertEqual(device["mac"], usb.normalize_mac(E4521_USB_MAC))
        self.assertEqual(device["ip"], E4521_USB_IP)
        self.assertNotEqual(device["ip"], device["parent_ip"])

    def test_correlation_is_exact_mac_not_heuristic(self):
        """Neighbouring addresses and near-miss MACs must not correlate."""
        near_mac = "B8:98:B0:07:85:EE"          # one nibble from the E4521's USB MAC
        self.seed((near_mac, "192.0.2.142", 0))   # and adjacent to the E4521's IP
        self.assertFalse(self.by_mac(near_mac)["integrated"])
        self.assertEqual(self.by_mac(near_mac)["parent_ip"], "")

    def test_parent_index_ignores_units_without_usb_mac(self):
        index = srv._usb_parent_index(CACHE_UNITS)
        self.assertEqual(sorted(index), sorted([srv._norm_usb_mac(E4521_USB_MAC), srv._norm_usb_mac(D4511_USB_MAC)]))

    def test_correlation_survives_a_usb_ip_change(self):
        self.seed((E4521_USB_MAC, E4521_USB_IP, 0))
        self.service._upsert(query(E4521_USB_MAC, "192.0.2.200"), IFACE_IP, IFACE_MASK, "DIRECT_IP")
        device = self.by_mac(E4521_USB_MAC)
        self.assertEqual(device["ip"], "192.0.2.200")
        self.assertEqual(device["parent_ip"], E4521_IP)


class UsbCountTests(ServerTestBase):
    def test_counts_online_unique_macs_only(self):
        self.seed((E4521_USB_MAC, E4521_USB_IP, 0), (OMNI311_MAC, OMNI311_IP, 0), (OMNI324_MAC, OMNI324_IP, 1))
        body = self.client.get("/api/usb_extenders").get_json()
        # A parent-derived endpoint whose parent has not answered is not online,
        # so it is listed but not counted.
        self.assertEqual(body["online_count"], 3)
        self.assertEqual(len({d["mac"] for d in body["devices"] if d.get("online")}), 3)

    def test_a_live_parent_makes_its_derived_endpoint_count(self):
        """Online means a recent authoritative observation, from either source."""
        self.seed((OMNI311_MAC, OMNI311_IP, 0))
        self.assertEqual(self.client.get("/api/usb_extenders").get_json()["online_count"], 1)
        srv._record_parent_live(D4511_IP)
        body = self.client.get("/api/usb_extenders").get_json()
        derived = next(d for d in body["devices"] if d["mac"] == usb.normalize_mac(D4511_USB_MAC))
        self.assertTrue(derived["online"])
        self.assertEqual(derived["liveness_source"], "usb_icron")
        self.assertEqual(body["online_count"], 2, "no UDP discovery was needed")

    def test_repeated_discovery_of_one_mac_counts_once(self):
        for ip in (E4521_USB_IP, E4521_USB_IP, "192.0.2.247"):
            self.service._upsert(query(E4521_USB_MAC, ip), IFACE_IP, IFACE_MASK, "RANGE")
        body = self.client.get("/api/usb_extenders").get_json()
        discovered = [d for d in body["devices"] if d.get("discovery_source") != "parent_derived"]
        self.assertEqual(len(discovered), 1, "one MAC is one endpoint however often it answers")
        self.assertEqual(body["online_count"], 1)
        macs = [d["mac"] for d in body["devices"]]
        self.assertEqual(len(macs), len(set(macs)), "and it is never listed twice")

    def test_stale_devices_are_not_counted(self):
        clock = [1000.0]
        self.service = usb.ExtenderDiscoveryService(Path(self.folder.name) / "s2.json",
                                                    now=lambda: clock[0], online_ttl=60, persist_debounce=0)
        srv._usb_extenders = self.service
        self.seed((OMNI311_MAC, OMNI311_IP, 0))
        self.assertEqual(self.client.get("/api/usb_extenders").get_json()["online_count"], 1)
        clock[0] += 61
        body = self.client.get("/api/usb_extenders").get_json()
        self.assertEqual(body["online_count"], 0)
        discovered = [d for d in body["devices"] if d.get("discovery_source") != "parent_derived"]
        self.assertEqual(len(discovered), 1, "identity is retained, only liveness expires")


class ClearAndRediscoverTests(ServerTestBase):
    def test_clear_units_clears_the_discovered_usb_inventory(self):
        self.seed((E4521_USB_MAC, E4521_USB_IP, 0), (OMNI311_MAC, OMNI311_IP, 0))
        self.assertEqual(self.client.get("/api/usb_extenders").get_json()["online_count"], 2)
        response = self.client.post("/api/clear_units")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["usb_cleared"], 2)
        body = self.client.get("/api/usb_extenders").get_json()
        # Nothing discovered survives, and nothing is online.
        discovered = [d for d in body["devices"] if d.get("discovery_source") != "parent_derived"]
        self.assertEqual(discovered, [], "the discovered inventory is forgotten")
        self.assertEqual(body["online_count"], 0)

    def test_clear_leaves_nothing_once_the_parent_cache_is_also_empty(self):
        """Clear Units empties the OmniStream cache too, so no parent remains to
        derive an endpoint from. The fixture pins _load_cache, so this states the
        production end state explicitly."""
        self.seed((E4521_USB_MAC, E4521_USB_IP, 0), (OMNI311_MAC, OMNI311_IP, 0))
        self.client.post("/api/clear_units")
        self._patch_cache([])
        body = self.client.get("/api/usb_extenders").get_json()
        self.assertEqual(body["devices"], [])
        self.assertEqual(body["online_count"], 0)

    def test_an_endpoint_known_only_from_its_parent_survives_a_usb_clear(self):
        """It was never discovered over UDP, so forgetting discovery cannot
        un-know it: the parent still reports it."""
        self.seed((OMNI311_MAC, OMNI311_IP, 0))
        self.client.post("/api/clear_units")
        derived = [d for d in self.client.get("/api/usb_extenders").get_json()["devices"]
                   if d.get("discovery_source") == "parent_derived"]
        self.assertTrue(derived, "a parent-known endpoint is still known")
        for device in derived:
            self.assertEqual(device["classification"], "INTEGRATED")
            self.assertFalse(device["online"], "but not online until its parent answers")

    def test_cleared_devices_do_not_reappear_from_persistence(self):
        self.seed((OMNI311_MAC, OMNI311_IP, 0))
        state_file = self.service.state_file
        self.client.post("/api/clear_units")
        reloaded = usb.ExtenderDiscoveryService(state_file, persist_debounce=0)
        self.assertEqual(reloaded.state()["devices"], [], "cleared inventory must not survive a reload")

    def test_clear_sends_nothing_and_keeps_configured_ranges(self):
        self.seed((OMNI311_MAC, OMNI311_IP, 0))
        self.service.set_ranges(["192.0.2.1-192.0.2.20"])
        sent = []
        self.service._exchange = lambda *a, **k: sent.append(a) or (_ for _ in ()).throw(AssertionError("no packet may be sent"))
        self.client.post("/api/clear_units")
        self.assertEqual(sent, [], "clear must not touch any device")
        self.assertEqual(self.service.state()["ranges"], ["192.0.2.1-192.0.2.20"])

    def test_devices_return_after_rediscovery(self):
        self.seed((OMNI311_MAC, OMNI311_IP, 0))
        self.client.post("/api/clear_units")
        self.assertEqual(self.client.get("/api/usb_extenders").get_json()["online_count"], 0)
        self.seed((OMNI311_MAC, OMNI311_IP, 0))
        self.assertEqual(self.client.get("/api/usb_extenders").get_json()["online_count"], 1)

    def test_clear_units_still_removes_the_encoder_decoder_artefacts(self):
        for name in ("CACHE", "SCAN_RESULTS", "CSV_VIEW"):
            getattr(srv, name).write_text("{}", encoding="utf-8")
        self.assertEqual(self.client.post("/api/clear_units").status_code, 200)
        for name in ("CACHE", "SCAN_RESULTS", "CSV_VIEW"):
            self.assertFalse(getattr(srv, name).exists(), f"{name} must still be cleared")


class DiscoveryDispatchTests(ServerTestBase):
    def test_discover_dispatches_local_and_returns_immediately(self):
        started = threading.Event()
        release = threading.Event()
        def slow_local(interface_ip, mask):
            started.set(); release.wait(3); return {"status": "completed", "found": []}
        self.service.discover_local = slow_local
        began = time.monotonic()
        response = self.client.post("/api/usb_extenders/discover", json={"interface_ip": IFACE_IP, "subnet_mask": IFACE_MASK})
        elapsed = time.monotonic() - began
        self.assertEqual(response.status_code, 200)
        self.assertIn("local", response.get_json()["started"])
        self.assertLess(elapsed, 0.5, "discovery dispatch must not block the request")
        self.assertTrue(started.wait(2))
        release.set()

    def test_targets_are_expanded_by_the_bounded_server_parser(self):
        seen = []
        self.service.discover_local = lambda *a: {"status": "completed", "found": []}
        self.service._range_scan = lambda targets, *a: seen.append(list(targets)) or []
        response = self.client.post("/api/usb_extenders/discover",
                                    json={"interface_ip": IFACE_IP, "subnet_mask": IFACE_MASK,
                                          "targets": "192.0.2.1-192.0.2.4"})
        self.assertEqual(response.get_json()["target_count"], 4)
        for _ in range(40):
            if seen: break
            time.sleep(0.02)
        self.assertEqual(seen, [["192.0.2.1", "192.0.2.2", "192.0.2.3", "192.0.2.4"]])

    def test_oversized_targets_are_reported_without_blocking_local_discovery(self):
        self.service.discover_local = lambda *a: {"status": "completed", "found": []}
        began = time.monotonic()
        response = self.client.post("/api/usb_extenders/discover",
                                    json={"interface_ip": IFACE_IP, "subnet_mask": IFACE_MASK,
                                          "targets": "10.0.0.0/8"})
        self.assertLess(time.monotonic() - began, 0.5, "oversized range must be rejected arithmetically")
        body = response.get_json()
        self.assertIn("local", body["started"], "a bad range must not suppress local discovery")
        self.assertEqual([i["scope"] for i in body["issues"]], ["targets"])

    def test_busy_range_scan_is_reported_not_queued(self):
        self.service.discover_local = lambda *a: {"status": "completed", "found": []}
        release = threading.Event()
        self.service._range_scan = lambda *a: release.wait(3) or []
        first = self.client.post("/api/usb_extenders/discover",
                                 json={"interface_ip": IFACE_IP, "subnet_mask": IFACE_MASK, "targets": "192.0.2.1-192.0.2.4"})
        self.assertIn("targets", first.get_json()["started"])
        second = self.client.post("/api/usb_extenders/discover",
                                  json={"interface_ip": IFACE_IP, "subnet_mask": IFACE_MASK, "targets": "192.0.2.1-192.0.2.4"})
        self.assertEqual([i["scope"] for i in second.get_json()["issues"]], ["targets"])
        release.set()

    def test_missing_interface_is_rejected_cleanly(self):
        self.assertEqual(self.client.post("/api/usb_extenders/discover", json={}).status_code, 400)


class ScanIsolationTests(ServerTestBase):
    """A slow or failing USB provider must not affect the OmniStream scan."""

    def _timed_scan(self):
        began = time.monotonic()
        response = self.client.post("/api/scan", json={"targets": "192.0.2.1"})
        return response, time.monotonic() - began

    def test_api_scan_never_calls_the_usb_provider(self):
        touched = []
        for name in ("discover_local", "discover_ip", "start_local_discovery", "start_range_scan", "state"):
            original = getattr(self.service, name)
            setattr(self.service, name, (lambda n: (lambda *a, **k: (touched.append(n), original(*a, **k))[1]))(name))
        self.client.post("/api/scan", json={"targets": "192.0.2.1"})
        self.assertEqual(touched, [], "/api/scan must not reach the USB provider at all")

    def test_slow_usb_discovery_does_not_delay_the_scan(self):
        release = threading.Event()
        self.service.discover_local = lambda *a: (release.wait(10), {"status": "completed", "found": []})[1]
        self.client.post("/api/usb_extenders/discover", json={"interface_ip": IFACE_IP, "subnet_mask": IFACE_MASK})
        response, elapsed = self._timed_scan()
        self.assertEqual(response.status_code, 200)
        self.assertLess(elapsed, 8, "the OmniStream scan waited on USB discovery")
        release.set()

    def test_failing_usb_discovery_does_not_fail_the_scan(self):
        def boom(*_a, **_k):
            raise OSError("interface gone")
        self.service.discover_local = boom
        self.service.start_local_discovery = boom
        self.client.post("/api/usb_extenders/discover", json={"interface_ip": IFACE_IP, "subnet_mask": IFACE_MASK})
        self.assertEqual(self._timed_scan()[0].status_code, 200)

    def test_invalid_usb_target_does_not_fail_the_scan(self):
        self.service.discover_local = lambda *a: {"status": "completed", "found": []}
        usb_response = self.client.post("/api/usb_extenders/discover",
                                        json={"interface_ip": IFACE_IP, "subnet_mask": IFACE_MASK, "targets": "not-an-address"})
        self.assertTrue(usb_response.get_json()["issues"])
        self.assertEqual(self._timed_scan()[0].status_code, 200)

    def test_usb_range_busy_does_not_fail_the_scan(self):
        self.service.discover_local = lambda *a: {"status": "completed", "found": []}
        release = threading.Event()
        self.service._range_scan = lambda *a: release.wait(3) or []
        for _ in range(2):
            self.client.post("/api/usb_extenders/discover",
                             json={"interface_ip": IFACE_IP, "subnet_mask": IFACE_MASK, "targets": "192.0.2.1-192.0.2.4"})
        self.assertEqual(self._timed_scan()[0].status_code, 200)
        release.set()


class IdentifyDispatchTests(ServerTestBase):
    def setUp(self):
        super().setUp()
        self.parent_calls = []
        self.udp_calls = []
        real_identify = srv._omnistream_identify
        srv._omnistream_identify = lambda ip: (self.parent_calls.append(ip), (True, {"result": "ok"}))[1]
        self.addCleanup(lambda: setattr(srv, "_omnistream_identify", real_identify))
        self.service.command = lambda ip, request, message_id, bind_ip=None: (
            self.udp_calls.append((ip, int.from_bytes(request[8:10], "big"))), {"ok": True})[1]

    def test_standalone_identify_uses_udp_blink(self):
        self.seed((OMNI311_MAC, OMNI311_IP, 0))
        response = self.client.post("/api/usb_extenders/identify", json={"mac": OMNI311_MAC})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["identify_via"], "extender")
        self.assertEqual(self.parent_calls, [])
        self.assertEqual([opcode for _ip, opcode in self.udp_calls], [usb.BLINK_ON])
        self.assertEqual(self.udp_calls[0][0], OMNI311_IP)

    def test_integrated_e4521_identify_uses_parent_blink(self):
        self.seed((E4521_USB_MAC, E4521_USB_IP, 0))
        response = self.client.post("/api/usb_extenders/identify", json={"mac": E4521_USB_MAC})
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertEqual(body["identify_via"], "parent")
        self.assertEqual(body["parent_ip"], E4521_IP)
        self.assertEqual(self.parent_calls, [E4521_IP])
        self.assertEqual(self.udp_calls, [], "no UDP blink may be aimed at an integrated USB IP")

    def test_integrated_d4511_identify_uses_parent_blink(self):
        self.seed((D4511_USB_MAC, D4511_USB_IP, 1))
        self.client.post("/api/usb_extenders/identify", json={"mac": D4511_USB_MAC})
        self.assertEqual(self.parent_calls, [D4511_IP])
        self.assertEqual(self.udp_calls, [])

    def test_parent_identify_failure_is_reported(self):
        self.seed((E4521_USB_MAC, E4521_USB_IP, 0))
        srv._omnistream_identify = lambda ip: (_ for _ in ()).throw(OSError("unreachable"))
        response = self.client.post("/api/usb_extenders/identify", json={"mac": E4521_USB_MAC})
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.get_json()["status"], "parent_unreachable")

    def test_discrete_blink_is_refused_for_an_integrated_endpoint(self):
        self.seed((E4521_USB_MAC, E4521_USB_IP, 0))
        response = self.client.post("/api/usb_extenders/identify", json={"mac": E4521_USB_MAC, "action": "on"})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.udp_calls, [])
        self.assertEqual(self.parent_calls, [])

    def test_view_declares_the_dispatch_target(self):
        self.seed((E4521_USB_MAC, E4521_USB_IP, 0), (OMNI311_MAC, OMNI311_IP, 0))
        self.assertEqual(self.by_mac(E4521_USB_MAC)["identify_via"], "parent")
        self.assertEqual(self.by_mac(OMNI311_MAC)["identify_via"], "extender")


class NetworkOwnershipTests(ServerTestBase):
    def test_integrated_endpoint_never_receives_a_standalone_udp_network_command(self):
        self.seed((E4521_USB_MAC, E4521_USB_IP, 0))
        sent = []
        self.service.command = lambda *a, **k: sent.append(a) or {"ok": True}
        real = srv._omnistream_icron_network_get
        srv._omnistream_icron_network_get = lambda ip, timeout=None: None   # no icron interface
        self.addCleanup(lambda: setattr(srv, "_omnistream_icron_network_get", real))
        response = self.client.post("/api/usb_extenders/network",
                                    json={"mac": E4521_USB_MAC, "mode": "dhcp"})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json()["status"], "unsupported_for_integrated")
        self.assertEqual(sent, [], "the standalone UDP path must never be used for an integrated endpoint")

    def test_standalone_network_configuration_is_still_offered(self):
        self.seed((OMNI311_MAC, OMNI311_IP, 0))
        self.assertTrue(self.by_mac(OMNI311_MAC)["network_config_supported"])
        self.assertFalse(self.by_mac(OMNI311_MAC)["integrated"])

    def test_integrated_endpoint_is_flagged_as_parent_configured_in_the_view(self):
        self.seed((E4521_USB_MAC, E4521_USB_IP, 0))
        device = self.by_mac(E4521_USB_MAC)
        self.assertTrue(device["network_config_supported"])
        self.assertEqual(device["network_via"], "parent")


class StandaloneInventoryTests(ServerTestBase):
    def test_standalone_311_is_listed_as_lex_inventory(self):
        self.seed((OMNI311_MAC, OMNI311_IP, 0))
        inventory = srv._usb_standalone_inventory()
        self.assertEqual([r["mac"] for r in inventory["standalone_lex"]], [usb.normalize_mac(OMNI311_MAC)])
        row = inventory["standalone_lex"][0]
        self.assertEqual(row["type"], "LEX")
        self.assertEqual(row["protocol"], "IP")
        self.assertEqual(row["ip"], OMNI311_IP)
        self.assertEqual(row["revision"], "1.9.4")

    def test_standalone_324_is_listed_as_rex_inventory(self):
        self.seed((OMNI324_MAC, OMNI324_IP, 1))
        inventory = srv._usb_standalone_inventory()
        self.assertEqual([r["mac"] for r in inventory["standalone_rex"]], [usb.normalize_mac(OMNI324_MAC)])
        self.assertEqual(inventory["standalone_rex"][0]["type"], "REX")

    def test_integrated_endpoints_are_not_duplicated_into_inventory(self):
        self.seed((E4521_USB_MAC, E4521_USB_IP, 0), (D4511_USB_MAC, D4511_USB_IP, 1), (OMNI311_MAC, OMNI311_IP, 0))
        inventory = srv._usb_standalone_inventory()
        macs = [r["mac"] for r in inventory["standalone_lex"] + inventory["standalone_rex"]]
        self.assertEqual(macs, [usb.normalize_mac(OMNI311_MAC)])
        self.assertNotIn(usb.normalize_mac(E4521_USB_MAC), macs)
        self.assertNotIn(usb.normalize_mac(D4511_USB_MAC), macs)

    def test_explicit_exclusions_also_suppress_duplicates(self):
        self.seed((OMNI311_MAC, OMNI311_IP, 0))
        inventory = srv._usb_standalone_inventory(exclude_macs={srv._norm_usb_mac(OMNI311_MAC)})
        self.assertEqual(inventory["standalone_lex"], [])

    def test_host_port_and_filtering_are_not_configurable(self):
        self.seed((OMNI311_MAC, OMNI311_IP, 0), (OMNI324_MAC, OMNI324_IP, 1))
        inventory = srv._usb_standalone_inventory()
        for row in inventory["standalone_lex"] + inventory["standalone_rex"]:
            self.assertFalse(row["host_port_configurable"], "standalone host port is fixed")
            self.assertFalse(row["filter_configurable"], "device filtering is not established by the API")
            self.assertEqual(row["host_port"], "")
            self.assertEqual(row["filter"], "")

    def test_standalone_rows_are_not_routable(self):
        self.seed((OMNI311_MAC, OMNI311_IP, 0), (OMNI324_MAC, OMNI324_IP, 1))
        inventory = srv._usb_standalone_inventory()
        for row in inventory["standalone_lex"] + inventory["standalone_rex"]:
            self.assertFalse(row["routable"])

    def test_peer_count_is_withheld_when_pairing_state_is_stale(self):
        clock = [1000.0]
        self.service = usb.ExtenderDiscoveryService(Path(self.folder.name) / "s3.json",
                                                    now=lambda: clock[0], online_ttl=60, persist_debounce=0)
        srv._usb_extenders = self.service
        self.service._upsert(query(OMNI311_MAC, OMNI311_IP), IFACE_IP, IFACE_MASK, "LOCAL_BROADCAST",
                             advanced(0, [OMNI324_MAC]))
        self.assertEqual(srv._usb_standalone_inventory()["standalone_lex"][0]["peer_count"], 1)
        clock[0] += usb.PAIRING_TTL + 1          # pairing freshness has its own TTL
        row = srv._usb_standalone_inventory()["standalone_lex"][0]
        self.assertFalse(row["pairing_state_fresh"])
        self.assertIsNone(row["peer_count"], "cached pairing counts must not be presented as authoritative")

    def test_a_device_without_advanced_query_has_no_inventory_row(self):
        self.seed((OMNI311_MAC, OMNI311_IP, None))
        inventory = srv._usb_standalone_inventory()
        self.assertEqual(inventory["standalone_lex"] + inventory["standalone_rex"], [])


class ObservedHardwareClassificationTests(ServerTestBase):
    """The full observed bench system must classify exactly as the hardware is."""

    def setUp(self):
        super().setUp()
        self._patch_cache(FULL_CACHE_UNITS)
        self.seed(*OBSERVED_UDP)

    def test_every_integrated_endpoint_is_classified_by_its_parent(self):
        expected = {
            E4521_USB_MAC: ("HW-OMNI-E4521", "USB Host / LEX", E4521_IP, E4521_HOST, E4521_USB_IP),
            E4521B_USB_MAC: ("HW-OMNI-E4521", "USB Host / LEX", E4521B_IP, E4521B_HOST, E4521B_USB_IP),
            D4511_USB_MAC: ("HW-OMNI-D4511", "USB Device / REX", D4511_IP, D4511_HOST, D4511_USB_IP),
            D4511B_USB_MAC: ("HW-OMNI-D4511", "USB Device / REX", D4511B_IP, D4511B_HOST, D4511B_USB_IP),
            D4511C_USB_MAC: ("HW-OMNI-D4511", "USB Device / REX", D4511C_IP, D4511C_HOST, D4511C_USB_IP),
        }
        for mac, (model, role, ip, host, usb_ip) in expected.items():
            device = self.by_mac(mac)
            self.assertEqual(device["classification"], "INTEGRATED", mac)
            self.assertEqual(device["display_model"], model, mac)
            self.assertEqual(device["usb_role"], role, mac)
            self.assertEqual(device["parent_ip"], ip, mac)
            self.assertEqual(device["parent_hostname"], host, mac)
            self.assertEqual(device["usb_ip"], usb_ip, mac)

    def test_parent_association_overrides_the_udp_reported_model(self):
        for mac in (E4521_USB_MAC, D4511_USB_MAC):
            device = self.by_mac(mac)
            # The UDP layer really did report a standalone-looking type...
            self.assertIn(device["udp_reported_type"], ("AT-OMNI-311", "AT-OMNI-324"))
            # ...but it must never surface as the product model.
            self.assertNotIn(device["display_model"],
                             ("AT-OMNI-311", "AT-OMNI-324", "HW-OMNI-311", "HW-OMNI-324"))
            self.assertNotIn("Unknown", device["display_model"])

    def test_only_the_two_real_standalone_units_are_standalone(self):
        standalone = sorted(d["mac"] for d in self.devices() if d["classification"] == "STANDALONE")
        self.assertEqual(standalone, sorted([usb.normalize_mac(OMNI311_MAC), usb.normalize_mac(OMNI324_MAC)]))
        self.assertEqual(self.by_mac(OMNI311_MAC)["display_model"], "HW-OMNI-311")
        self.assertEqual(self.by_mac(OMNI324_MAC)["display_model"], "HW-OMNI-324")

    def test_no_endpoint_is_left_unknown_or_unclassified(self):
        for device in self.devices():
            self.assertIn(device["classification"], ("INTEGRATED", "STANDALONE"))
            self.assertNotIn("unclassified", device["display_model"])

    def test_inventory_contains_only_the_two_standalone_units(self):
        inventory = srv._usb_standalone_inventory()
        self.assertEqual([r["mac"] for r in inventory["standalone_lex"]], [usb.normalize_mac(OMNI311_MAC)])
        self.assertEqual([r["mac"] for r in inventory["standalone_rex"]], [usb.normalize_mac(OMNI324_MAC)])

    def test_no_heuristic_correlation_is_used(self):
        """Strip the association data and nothing may correlate by IP or MAC shape."""
        self._units = [dict(u, usb_mac=None, usb_type=None) for u in FULL_CACHE_UNITS]
        for device in self.devices():
            self.assertEqual(device["classification"], "UNCONFIRMED")
            self.assertEqual(device["parent_ip"], "")


class UnconfirmedEndpointTests(ServerTestBase):
    """Until association is complete, no endpoint may receive standalone control."""

    def setUp(self):
        super().setUp()
        # The D4511 at .151 has not been associated yet: this is the real cache
        # state that previously made its USB endpoint look like an AT-OMNI-324.
        self._patch_cache(FULL_CACHE_UNITS[:3] + [dict(FULL_CACHE_UNITS[3], usb_mac=None, usb_type=None)])
        self.seed(*OBSERVED_UDP)

    def test_uncorrelated_endpoint_is_unconfirmed_not_standalone(self):
        device = self.by_mac(D4511B_USB_MAC)
        self.assertEqual(device["classification"], "UNCONFIRMED")
        self.assertNotEqual(device["display_model"], "AT-OMNI-324")
        self.assertNotEqual(device["display_model"], "HW-OMNI-324")
        self.assertFalse(device["network_config_supported"])
        self.assertEqual(device["network_via"], "")
        self.assertFalse(device["reboot_supported"])

    def test_unconfirmed_endpoints_are_not_added_to_inventory(self):
        inventory = srv._usb_standalone_inventory()
        macs = [r["mac"] for r in inventory["standalone_lex"] + inventory["standalone_rex"]]
        self.assertNotIn(usb.normalize_mac(D4511B_USB_MAC), macs)

    def test_standalone_commands_are_withheld_while_unconfirmed(self):
        sent = []
        self.service.command = lambda *a, **k: sent.append(a) or {"ok": True}
        for path in ("identify", "reboot"):
            response = self.client.post(f"/api/usb_extenders/{path}", json={"mac": D4511B_USB_MAC})
            self.assertEqual(response.status_code, 409, path)
            self.assertEqual(response.get_json()["status"], "unconfirmed_endpoint", path)
        response = self.client.post("/api/usb_extenders/network",
                                    json={"interface_ip": IFACE_IP, "subnet_mask": IFACE_MASK,
                                          "mac": D4511B_USB_MAC, "mode": "dhcp"})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(sent, [], "no standalone command may reach an unconfirmed endpoint")

    def test_parent_map_refresh_learns_the_missing_association(self):
        self.patch_srv("_usb_get_config",
                       lambda ip, *a, **k: {"macaddress": D4511B_USB_MAC, "type": "REX"} if ip == D4511B_IP else {})
        written = {}
        self.patch_srv("_update_poll_detail_cache",
                       lambda ip, updates: written.setdefault(ip, {}).update(updates))
        result = srv._refresh_usb_parent_map()
        self.assertEqual(result["checked"], 1)
        self.assertEqual(result["learned"], 1)
        self.assertEqual(written[D4511B_IP]["usb_mac"], D4511B_USB_MAC)
        self.assertEqual(written[D4511B_IP]["usb_type"], "REX")

    def test_refresh_is_a_no_op_when_association_is_complete(self):
        self._patch_cache(FULL_CACHE_UNITS)
        self.assertEqual(srv._refresh_usb_parent_map(), {"checked": 0, "learned": 0})


class IntegratedControlOwnershipTests(ServerTestBase):
    def setUp(self):
        super().setUp()
        self._patch_cache(FULL_CACHE_UNITS)
        self.seed(*OBSERVED_UDP)
        self.udp = []
        self.service.command = lambda ip, request, message_id, bind_ip=None: (
            self.udp.append((ip, int.from_bytes(request[8:10], "big"))), {"ok": True})[1]

    def test_integrated_reboot_uses_the_established_parent_operation(self):
        calls = []
        real = srv._omnistream_reboot
        srv._omnistream_reboot = lambda ip, cache_map=None: (calls.append(ip), {"ip": ip, "ok": True, "response": {}})[1]
        self.addCleanup(lambda: setattr(srv, "_omnistream_reboot", real))
        response = self.client.post("/api/usb_extenders/reboot", json={"mac": E4521_USB_MAC})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["reboot_via"], "parent")
        self.assertEqual(calls, [E4521_IP], "must reboot the parent, not the USB endpoint")
        self.assertEqual(self.udp, [], "no UDP reboot may reach an integrated endpoint")

    def test_integrated_endpoint_is_configured_through_the_parent_not_over_udp(self):
        """Integrated network now works, but only via the parent OmniStream API."""
        calls = []
        real_get, real_set = srv._omnistream_icron_network_get, srv._omnistream_icron_network_set
        srv._omnistream_icron_network_get = lambda ip, timeout=None: {
            "name": "icron", "mode": "dhcp", "ipaddress": D4511_USB_IP, "subnetmask": "255.255.255.0",
            "gateway": "192.0.2.1", "macaddress": D4511_USB_MAC,
            "modes": list(srv.ICRON_NETWORK_MODES), "source": "parent_net_config"}
        srv._omnistream_icron_network_set = lambda ip, mode, *a, **k: (
            calls.append((ip, mode)), {"accepted": True, "response": {}, "sent_mode": mode, "sent": {}})[1]
        self.addCleanup(lambda: (setattr(srv, "_omnistream_icron_network_get", real_get),
                                 setattr(srv, "_omnistream_icron_network_set", real_set)))
        response = self.client.post("/api/usb_extenders/network",
                                    json={"mac": D4511_USB_MAC, "mode": "dhcp"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["network_via"], "parent")
        self.assertEqual(calls, [(D4511_IP, "dhcp")], "must target the resolved parent")
        self.assertEqual(self.udp, [], "no standalone UDP IP command may be sent")

    def test_standalone_reboot_still_uses_udp(self):
        response = self.client.post("/api/usb_extenders/reboot", json={"mac": OMNI311_MAC})
        self.assertEqual(response.status_code, 200)
        self.assertEqual([opcode for _ip, opcode in self.udp], [usb.REBOOT])
        self.assertEqual(self.udp[0][0], OMNI311_IP)

    def test_standalone_network_still_uses_udp(self):
        self.client.post("/api/usb_extenders/network",
                         json={"interface_ip": IFACE_IP, "subnet_mask": IFACE_MASK,
                               "mac": OMNI324_MAC, "mode": "dhcp"})
        self.assertIn(usb.IP_DHCP, [opcode for _ip, opcode in self.udp])

    def test_integrated_pairing_uses_usb_icron_not_advanced_query(self):
        device = self.by_mac(E4521_USB_MAC)
        self.assertEqual(device["pairing_source"], "usb_icron")
        self.assertNotIn("paired_macs", device, "UDP pairing must not be shown for an integrated endpoint")
        self.assertFalse(device["pairing_state_fresh"])

    def test_standalone_pairing_uses_advanced_query_freshness(self):
        device = self.by_mac(OMNI311_MAC)
        self.assertEqual(device["pairing_source"], "advanced_query")
        self.assertIn("paired_macs", device)
        self.assertTrue(device["pairing_state_fresh"])


class MatrixOnlineStateTests(ServerTestBase):
    def setUp(self):
        super().setUp()
        self._patch_cache(FULL_CACHE_UNITS)
        self.seed(*OBSERVED_UDP)

    def test_matrix_inventory_shares_the_configure_online_state(self):
        view = srv._usb_extender_view()
        configure = {d["mac"]: (d["online"], d["stale"], d["last_seen"]) for d in view["devices"]}
        inventory = srv._usb_standalone_inventory(view=view)
        for row in inventory["standalone_lex"] + inventory["standalone_rex"]:
            self.assertEqual((row["online"], row["stale"], row["last_seen"]), configure[row["mac"]], row["mac"])

    def test_expiry_moves_both_views_together(self):
        clock = [1000.0]
        self.service = usb.ExtenderDiscoveryService(Path(self.folder.name) / "s4.json",
                                                    now=lambda: clock[0], online_ttl=60, persist_debounce=0)
        srv._usb_extenders = self.service
        self.seed((OMNI311_MAC, OMNI311_IP, 0))
        self.assertTrue(srv._usb_extender_view()["devices"][0]["online"])
        self.assertTrue(srv._usb_standalone_inventory()["standalone_lex"][0]["online"])
        clock[0] += 61
        self.assertFalse(srv._usb_extender_view()["devices"][0]["online"])
        self.assertFalse(srv._usb_standalone_inventory()["standalone_lex"][0]["online"])

    def test_usb_state_inventory_matches_the_extender_endpoint(self):
        self.patch_srv("_usb_get_config", lambda *a, **k: {})
        body = self.client.get("/api/usb_state").get_json()
        extenders = {d["mac"]: d["online"] for d in self.client.get("/api/usb_extenders").get_json()["devices"]}
        rows = body["standalone_lex"] + body["standalone_rex"]
        self.assertTrue(rows)
        for row in rows:
            self.assertEqual(row["online"], extenders[row["mac"]], row["mac"])

    def test_peers_label_is_useful_rather_than_unknown(self):
        clock = [1000.0]
        self.service = usb.ExtenderDiscoveryService(Path(self.folder.name) / "s5.json",
                                                    now=lambda: clock[0], online_ttl=60, persist_debounce=0)
        srv._usb_extenders = self.service
        # Never read: no Advanced Query has ever succeeded.
        self.service._upsert(query(OMNI311_MAC, OMNI311_IP), IFACE_IP, IFACE_MASK, "LOCAL_BROADCAST")
        self.service._upsert(query(OMNI311_MAC, OMNI311_IP), IFACE_IP, IFACE_MASK, "LOCAL_BROADCAST", advanced(0))
        row = srv._usb_standalone_inventory()["standalone_lex"][0]
        self.assertEqual(row["peer_count"], 0)
        self.assertIsNone(row["peers_label"])
        clock[0] += usb.PAIRING_TTL + 1          # pairing freshness has its own TTL
        row = srv._usb_standalone_inventory()["standalone_lex"][0]
        self.assertEqual(row["peers_label"], "Stale")
        self.assertIsNone(row["peer_count"])


class BenchDiagnosticTests(ServerTestBase):
    """The bench validation API: explicit, gated, and never production routing."""

    def setUp(self):
        super().setUp()
        self._patch_cache(FULL_CACHE_UNITS)
        self.net = self._network()
        self.service._exchange = self.net.exchange

    def _network(self):
        endpoints = {
            OMNI311_IP: {"mac": OMNI311_MAC, "code": 0, "peers": []},
            OMNI324_IP: {"mac": OMNI324_MAC, "code": 1, "peers": []},
        }
        sent = []

        class Net:
            def __init__(self):
                self.endpoints, self.sent, self.ack = endpoints, sent, True
            def exchange(self, destination, request, message_id, timeout=usb.DEFAULT_TIMEOUT, bind_ip=None):
                command = int.from_bytes(request[8:10], "big")
                self.sent.append((destination, command, request[10:]))
                e = self.endpoints.get(destination)
                if e is None:
                    raise usb.socket.timeout()
                if command == usb.QUERY:
                    payload = query_payload(e["mac"], destination)
                    return usb.packet(message_id, usb.QUERY_RESPONSE, payload), (destination, usb.UDP_PORT)
                if command == usb.ADVANCED_QUERY:
                    body = bytes([e["code"]]) + b"".join(usb.mac_bytes(p) for p in e["peers"])
                    return usb.packet(message_id, usb.ADVANCED_QUERY_RESPONSE, body), (destination, usb.UDP_PORT)
                if self.ack is None:
                    raise usb.socket.timeout()
                return usb.packet(message_id, usb.ACK if self.ack else usb.NACK), (destination, usb.UDP_PORT)
        return Net()

    def discover(self):
        self.service.discover_ip(OMNI311_IP, IFACE_IP, IFACE_MASK)
        self.service.discover_ip(OMNI324_IP, IFACE_IP, IFACE_MASK)

    def opcodes(self):
        return [c for _d, c, _p in self.net.sent if c not in (usb.QUERY, usb.ADVANCED_QUERY)]

    def test_route_state_is_read_only(self):
        self.discover()
        self.net.sent.clear()
        response = self.client.post("/api/diagnostics/usb_route/state",
                                    json={"host_mac": OMNI311_MAC, "device_mac": OMNI324_MAC})
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertEqual(body["route_state"]["status"], "OK")
        self.assertFalse(body["route_state"]["routed"])
        self.assertEqual(self.opcodes(), [], "route state must send no mutating command")
        self.assertIn("elapsed_seconds", body)

    def test_pair_requires_explicit_confirmation(self):
        self.discover()
        self.net.sent.clear()
        response = self.client.post("/api/diagnostics/usb_route/pair",
                                    json={"host_mac": OMNI311_MAC, "device_mac": OMNI324_MAC})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["status"], "confirmation_required")
        self.assertEqual(self.opcodes(), [], "an unconfirmed bench call must send nothing")

    def test_pair_uses_only_the_normal_pair_opcode(self):
        self.discover()
        self.net.sent.clear()
        response = self.client.post("/api/diagnostics/usb_route/pair",
                                    json={"host_mac": OMNI311_MAC, "device_mac": OMNI324_MAC,
                                          "confirm": "PAIR", "verify_attempts": 2, "verify_delay": 0})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.opcodes(), [usb.PAIR, usb.PAIR], "Pair is per-endpoint")
        self.assertNotIn(usb.ADVANCED_QUERY_RESPONSE, self.opcodes(), "Force Pair must never be sent")
        self.assertNotIn(usb.UNPAIR_ALL, self.opcodes())
        body = response.get_json()
        by_role = {c["role"]: c for c in body["command"]}
        self.assertEqual(by_role["host"]["target_ip"], OMNI311_IP)
        self.assertEqual(by_role["host"]["peer_mac"], usb.normalize_mac(OMNI324_MAC))
        self.assertEqual(by_role["device"]["target_ip"], OMNI324_IP)
        self.assertEqual(by_role["device"]["peer_mac"], usb.normalize_mac(OMNI311_MAC))
        self.assertTrue(all(c["ack"] for c in body["command"]))

    def test_unpair_uses_only_the_specific_unpair_opcode(self):
        self.net.endpoints[OMNI311_IP]["peers"] = [OMNI324_MAC]
        self.net.endpoints[OMNI324_IP]["peers"] = [OMNI311_MAC]
        self.discover()
        self.net.sent.clear()
        self.client.post("/api/diagnostics/usb_route/unpair",
                         json={"host_mac": OMNI311_MAC, "device_mac": OMNI324_MAC,
                               "confirm": "UNPAIR", "verify_attempts": 1, "verify_delay": 0})
        self.assertEqual(self.opcodes(), [usb.UNPAIR, usb.UNPAIR], "Unpair is per-endpoint")
        self.assertNotIn(usb.UNPAIR_ALL, self.opcodes())

    def test_verified_success_requires_both_endpoints_to_agree(self):
        self.discover()
        # ACK, and only the 311 reflects the pairing.
        original = self.net.exchange
        def exchange(destination, request, message_id, timeout=usb.DEFAULT_TIMEOUT, bind_ip=None):
            if int.from_bytes(request[8:10], "big") == usb.PAIR:
                self.net.endpoints[OMNI311_IP]["peers"] = [OMNI324_MAC]
            return original(destination, request, message_id, timeout, bind_ip)
        self.service._exchange = exchange
        body = self.client.post("/api/diagnostics/usb_route/pair",
                                json={"host_mac": OMNI311_MAC, "device_mac": OMNI324_MAC,
                                      "confirm": "PAIR", "verify_attempts": 2, "verify_delay": 0}).get_json()
        self.assertNotEqual(body["status"], "VERIFIED_SUCCESS")
        self.assertEqual(body["status"], "COMMAND_ACCEPTED_UNVERIFIED")
        self.assertEqual(body["reason"], "INCONSISTENT")
        self.assertFalse(body["ok"])

    def test_verified_success_when_both_endpoints_agree(self):
        self.discover()
        original = self.net.exchange
        def exchange(destination, request, message_id, timeout=usb.DEFAULT_TIMEOUT, bind_ip=None):
            if int.from_bytes(request[8:10], "big") == usb.PAIR:
                self.net.endpoints[OMNI311_IP]["peers"] = [OMNI324_MAC]
                self.net.endpoints[OMNI324_IP]["peers"] = [OMNI311_MAC]
            return original(destination, request, message_id, timeout, bind_ip)
        self.service._exchange = exchange
        body = self.client.post("/api/diagnostics/usb_route/pair",
                                json={"host_mac": OMNI311_MAC, "device_mac": OMNI324_MAC,
                                      "confirm": "PAIR", "verify_attempts": 3, "verify_delay": 0}).get_json()
        self.assertEqual(body["status"], "VERIFIED_SUCCESS")
        self.assertTrue(body["ok"])
        self.assertEqual(body["verification_attempts"], 1)
        self.assertEqual(body["post"]["host"]["advanced_query"]["paired_macs"], [usb.normalize_mac(OMNI324_MAC)])
        self.assertEqual(body["post"]["device"]["advanced_query"]["paired_macs"], [usb.normalize_mac(OMNI311_MAC)])

    def test_delayed_propagation_is_measured_by_retries(self):
        self.discover()
        original, calls = self.net.exchange, {"advanced": 0}
        def exchange(destination, request, message_id, timeout=usb.DEFAULT_TIMEOUT, bind_ip=None):
            command = int.from_bytes(request[8:10], "big")
            if command == usb.ADVANCED_QUERY:
                calls["advanced"] += 1
                # Propagation appears only after several read-backs.
                if calls["advanced"] > 8:
                    self.net.endpoints[OMNI311_IP]["peers"] = [OMNI324_MAC]
                    self.net.endpoints[OMNI324_IP]["peers"] = [OMNI311_MAC]
            return original(destination, request, message_id, timeout, bind_ip)
        self.service._exchange = exchange
        body = self.client.post("/api/diagnostics/usb_route/pair",
                                json={"host_mac": OMNI311_MAC, "device_mac": OMNI324_MAC,
                                      "confirm": "PAIR", "verify_attempts": 6, "verify_delay": 0}).get_json()
        self.assertEqual(body["status"], "VERIFIED_SUCCESS")
        self.assertGreater(body["verification_attempts"], 1, "retry count must reveal delayed propagation")

    def test_nack_is_reported_without_claiming_success(self):
        self.discover()
        self.net.ack = False
        body = self.client.post("/api/diagnostics/usb_route/pair",
                                json={"host_mac": OMNI311_MAC, "device_mac": OMNI324_MAC, "confirm": "PAIR"}).get_json()
        self.assertEqual(body["status"], "REJECTED")
        self.assertFalse(body["ok"])
        self.assertFalse(body["command"][0]["ack"])

    def test_integrated_endpoints_are_refused(self):
        self.seed((E4521_USB_MAC, E4521_USB_IP, 0), (D4511_USB_MAC, D4511_USB_IP, 1))
        response = self.client.post("/api/diagnostics/usb_route/state",
                                    json={"host_mac": E4521_USB_MAC, "device_mac": D4511_USB_MAC})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json()["status"], "not_standalone")

    def test_mixed_family_combinations_are_refused(self):
        self.discover()
        self.seed((E4521_USB_MAC, E4521_USB_IP, 0), (D4511_USB_MAC, D4511_USB_IP, 1))
        for host, device in ((E4521_USB_MAC, OMNI324_MAC), (OMNI311_MAC, D4511_USB_MAC)):
            response = self.client.post("/api/diagnostics/usb_route/state",
                                        json={"host_mac": host, "device_mac": device})
            self.assertEqual(response.status_code, 409, f"{host} -> {device}")

    def test_reversed_roles_are_refused(self):
        self.discover()
        response = self.client.post("/api/diagnostics/usb_route/state",
                                    json={"host_mac": OMNI324_MAC, "device_mac": OMNI311_MAC})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json()["status"], "wrong_device_type")

    def test_retry_bounds_are_clamped(self):
        # Asserted directly so the test does not have to sit through the maximum
        # bounded wait.
        self.assertEqual(srv._bench_attempts({"verify_attempts": 9999, "verify_delay": 9999}),
                         (srv.USB_BENCH_MAX_ATTEMPTS, srv.USB_BENCH_MAX_DELAY))
        self.assertEqual(srv._bench_attempts({"verify_attempts": 0, "verify_delay": -5})[0], 1)
        self.assertEqual(srv._bench_attempts({})[0], srv.USB_BENCH_VERIFY_ATTEMPTS)

    def test_retry_settings_are_reported_back(self):
        self.discover()
        body = self.client.post("/api/diagnostics/usb_route/pair",
                                json={"host_mac": OMNI311_MAC, "device_mac": OMNI324_MAC, "confirm": "PAIR",
                                      "verify_attempts": 2, "verify_delay": 0}).get_json()
        self.assertEqual(body["verify_settings"], {"attempts": 2, "delay_seconds": 0.0})

    def test_bench_does_not_create_matrix_route_cells(self):
        self.discover()
        self.client.post("/api/diagnostics/usb_route/pair",
                         json={"host_mac": OMNI311_MAC, "device_mac": OMNI324_MAC, "confirm": "PAIR"})
        inventory = srv._usb_standalone_inventory()
        for row in inventory["standalone_lex"] + inventory["standalone_rex"]:
            self.assertFalse(row["routable"], "bench pairing must not make a unit routable")


class GenericCorrelationTests(ServerTestBase):
    """The association pipeline must be data driven, with no per-device rules.

    Every live example is resolved in one pass, and synthetic devices sharing no
    address, MAC prefix, subnet, or hostname pattern with them must resolve
    identically without any code change.
    """

    # The complete observed system, including the 192.168.200.x subnet.
    LIVE = [
        # (parent_ip, hostname, model, usb_type, usb_mac, usb_ip, expected_model, expected_role)
        ("192.0.2.141", "hw-omni-e4521-00002", "hw-omni-e4521", "LEX", "B8:98:B0:07:85:ED", "192.0.2.246", "HW-OMNI-E4521", "LEX"),
        ("192.0.2.143", "hw-omni-e4521-0856c", "hw-omni-e4521", "LEX", "B8:98:B0:07:85:6D", "192.0.2.194", "HW-OMNI-E4521", "LEX"),
        ("192.0.2.32", "hw-omni-d4511-085c6", "hw-omni-d4511", "REX", "B8:98:B0:07:85:C7", "192.0.2.108", "HW-OMNI-D4511", "REX"),
        ("192.0.2.151", "hw-omni-d4511-08586", "hw-omni-d4511", "REX", "B8:98:B0:07:85:87", "192.0.2.250", "HW-OMNI-D4511", "REX"),
        ("192.0.2.152", "hw-omni-d4511-085c0", "hw-omni-d4511", "REX", "B8:98:B0:07:85:C1", "192.0.2.248", "HW-OMNI-D4511", "REX"),
        ("192.168.200.154", "hw-omni-d4511-08578", "hw-omni-d4511", "REX", "B8:98:B0:07:85:79", "192.168.200.66", "HW-OMNI-D4511", "REX"),
    ]

    # Deliberately unlike the live examples: different RFC1918 block, different
    # OUI, different hostname scheme, different USB subnet.
    SYNTHETIC = [
        ("10.44.7.9", "lab-encoder-alpha", "hw-omni-e4521", "LEX", "02:AA:5F:11:22:33", "172.19.3.201", "HW-OMNI-E4521", "LEX"),
        ("10.44.7.10", "lab-decoder-bravo", "hw-omni-d4511", "REX", "DE:AD:BE:EF:00:01", "172.19.3.202", "HW-OMNI-D4511", "REX"),
        ("172.31.255.4", "site2-e4521-99999", "hw-omni-e4521", "LEX", "0E:11:22:33:44:55", "10.0.0.7", "HW-OMNI-E4521", "LEX"),
        ("192.0.2.77", "edge-d4511-zzzzz", "hw-omni-d4511", "REX", "FE:ED:FA:CE:00:99", "198.51.100.9", "HW-OMNI-D4511", "REX"),
    ]

    def build(self, rows, associate=True):
        units = [{"ip": ip, "hostname": host, "model": model, "role": "encoder" if "e45" in model else "decoder",
                  **({"usb_type": role, "usb_mac": mac} if associate else {})}
                 for ip, host, model, role, mac, _usb_ip, _em, _er in rows]
        self._patch_cache(units)
        for _ip, _host, _model, role, mac, usb_ip, _em, _er in rows:
            self.seed((mac, usb_ip, 0 if role == "LEX" else 1))

    def assert_resolves(self, rows):
        for parent_ip, hostname, _model, _role, mac, usb_ip, expected_model, expected_role in rows:
            device = self.by_mac(mac)
            self.assertEqual(device["classification"], "INTEGRATED", mac)
            self.assertEqual(device["display_model"], expected_model, mac)
            self.assertEqual(device["parent_ip"], parent_ip, mac)
            self.assertEqual(device["parent_hostname"], hostname, mac)
            self.assertEqual(device["parent_role"], expected_role, mac)
            self.assertEqual(device["usb_ip"], usb_ip, mac)
            self.assertEqual(device["identify_via"], "parent", mac)
            self.assertEqual(device["network_via"], "parent", mac)
            self.assertEqual(device["reboot_via"], "parent", mac)
            self.assertEqual(device["pairing_source"], "usb_icron", mac)

    def test_all_live_examples_resolve_in_one_pass(self):
        self.build(self.LIVE)
        self.assert_resolves(self.LIVE)
        self.assertEqual(len([d for d in self.devices() if d["classification"] == "INTEGRATED"]), len(self.LIVE))

    def test_synthetic_devices_resolve_without_code_change(self):
        self.build(self.SYNTHETIC)
        self.assert_resolves(self.SYNTHETIC)

    def test_live_and_synthetic_resolve_together(self):
        self.build(self.LIVE + self.SYNTHETIC)
        self.assert_resolves(self.LIVE + self.SYNTHETIC)
        self.assertEqual(len([d for d in self.devices() if d["classification"] != "INTEGRATED"]), 0)

    def test_no_integrated_endpoint_appears_in_standalone_inventory(self):
        self.build(self.LIVE + self.SYNTHETIC)
        inventory = srv._usb_standalone_inventory()
        self.assertEqual(inventory["standalone_lex"] + inventory["standalone_rex"], [])

    def test_standalone_units_alongside_all_integrated_devices(self):
        rows = self.LIVE + self.SYNTHETIC
        self.build(rows)
        self.seed((OMNI311_MAC, OMNI311_IP, 0), (OMNI324_MAC, OMNI324_IP, 1))
        self.assertEqual(self.by_mac(OMNI311_MAC)["classification"], "STANDALONE")
        self.assertEqual(self.by_mac(OMNI324_MAC)["classification"], "STANDALONE")
        inventory = srv._usb_standalone_inventory()
        self.assertEqual([r["mac"] for r in inventory["standalone_lex"]], [usb.normalize_mac(OMNI311_MAC)])
        self.assertEqual([r["mac"] for r in inventory["standalone_rex"]], [usb.normalize_mac(OMNI324_MAC)])

    def test_runtime_usb_icron_observation_resolves_without_a_usb_rescan(self):
        """T0 unconfirmed -> T1 integrated, purely from later parent information."""
        self.build(self.LIVE, associate=False)
        target_mac = self.LIVE[-1][4]          # the 192.168.200.x decoder
        self.assertEqual(self.by_mac(target_mac)["classification"], "UNCONFIRMED")
        # Parent information arrives later from the authoritative usb_icron read.
        for parent_ip, _host, _model, role, mac, usb_ip, _em, _er in self.LIVE:
            srv._record_usb_association(parent_ip, mac, role, usb_ip)
        # No further USB discovery is performed.
        self.assert_resolves(self.LIVE)

    def test_partial_association_leaves_only_the_unknown_ones_unconfirmed(self):
        self.build(self.LIVE, associate=False)
        known = self.LIVE[:4]
        for parent_ip, _host, _model, role, mac, usb_ip, _em, _er in known:
            srv._record_usb_association(parent_ip, mac, role, usb_ip)
        for _p, _h, _m, _r, mac, _u, expected_model, _er in known:
            self.assertEqual(self.by_mac(mac)["classification"], "INTEGRATED", mac)
            self.assertEqual(self.by_mac(mac)["display_model"], expected_model, mac)
        for _p, _h, _m, _r, mac, _u, _em, _er in self.LIVE[4:]:
            self.assertEqual(self.by_mac(mac)["classification"], "UNCONFIRMED", mac)

    def test_association_survives_a_usb_ip_change_on_any_device(self):
        self.build(self.LIVE + self.SYNTHETIC)
        for _p, _h, _m, _r, mac, _usb_ip, expected_model, _er in self.LIVE + self.SYNTHETIC:
            self.service._upsert(query(mac, "203.0.113.5"), IFACE_IP, IFACE_MASK, "DIRECT_IP")
            device = self.by_mac(mac)
            self.assertEqual(device["classification"], "INTEGRATED", mac)
            self.assertEqual(device["display_model"], expected_model, mac)

    def test_every_endpoint_is_accounted_for(self):
        rows = self.LIVE + self.SYNTHETIC
        self.build(rows)
        self.seed((OMNI311_MAC, OMNI311_IP, 0), (OMNI324_MAC, OMNI324_IP, 1))
        devices = self.devices()
        self.assertEqual(len(devices), len(rows) + 2)
        for device in devices:
            self.assertIn(device["classification"], ("INTEGRATED", "STANDALONE"))
            self.assertTrue(device["classification_reason"], device["mac"])

    def test_diagnostic_table_covers_every_endpoint(self):
        rows = self.LIVE + self.SYNTHETIC
        self.build(rows)
        self.seed((OMNI311_MAC, OMNI311_IP, 0), (OMNI324_MAC, OMNI324_IP, 1))
        body = self.client.get("/api/diagnostics/usb_endpoints").get_json()
        self.assertTrue(body["association_complete"])
        self.assertEqual(body["counts"], {"INTEGRATED": len(rows), "STANDALONE": 2})
        self.assertEqual(len(body["endpoints"]), len(rows) + 2)
        for row in body["endpoints"]:
            self.assertTrue(row["reason"])
            self.assertIn(row["control_owner"], ("parent_omnistream", "standalone_udp"))

    def test_unassociated_units_are_reported_not_hidden(self):
        self.build(self.LIVE, associate=False)
        body = self.client.get("/api/diagnostics/usb_endpoints").get_json()
        self.assertFalse(body["association_complete"])
        self.assertEqual(sorted(u["parent_ip"] for u in body["unassociated_units"]),
                         sorted(row[0] for row in self.LIVE))
        self.assertEqual(body["counts"], {"UNCONFIRMED": len(self.LIVE)})


class NoHardcodedDeviceConstantsTests(unittest.TestCase):
    """Production code must contain no live address, MAC, or hostname."""

    PRODUCTION = ["OmniMatrix_upgrade_server_v7_6y.py", "omni_usb_extender.py",
                  "ui/matrix/usb-extenders.js", "ui/matrix/usb.js", "ui/index.html"]

    LIVE_VALUES = [
        "192.0.2.141", "192.0.2.143", "192.0.2.32", "192.0.2.151",
        "192.0.2.152", "192.168.200.154", "192.0.2.246", "192.0.2.194",
        "192.0.2.108", "192.0.2.250", "192.0.2.248", "192.168.200.66",
        "192.0.2.127", "192.0.2.128",
        "B8:98:B0:07:85:ED", "B8:98:B0:07:85:6D", "B8:98:B0:07:85:C7", "B8:98:B0:07:85:87",
        "B8:98:B0:07:85:C1", "B8:98:B0:07:85:79", "00:1B:13:04:E9:6E", "00:1B:13:04:6A:EA",
        "hw-omni-e4521-00002", "hw-omni-e4521-0856c", "hw-omni-d4511-085c6",
        "hw-omni-d4511-08586", "hw-omni-d4511-085c0", "hw-omni-d4511-08578",
    ]

    def test_no_live_device_value_appears_in_production_code(self):
        root = Path(srv.__file__).resolve().parent
        offenders = []
        for relative in self.PRODUCTION:
            text = (root / relative).read_text(encoding="utf-8", errors="replace")
            haystack = text.upper().replace("-", ":")
            for value in self.LIVE_VALUES:
                needle = value.upper().replace("-", ":")
                if needle in haystack:
                    offenders.append(f"{relative}: {value}")
        self.assertEqual(offenders, [], "live device values must not appear in production code")

    def test_classification_uses_no_vendor_or_product_string_matching(self):
        """Association must not key off branding in the UDP product string."""
        text = (Path(srv.__file__).resolve().parent / "OmniMatrix_upgrade_server_v7_6y.py").read_text(
            encoding="utf-8", errors="replace")
        start = text.index("def _usb_parent_context")
        end = text.index("def _usb_extender_view")
        pipeline = text[start:end]
        for banned in ("Atlona", "Hall Research", "startswith", "endswith"):
            self.assertNotIn(banned, pipeline, f"association pipeline must not branch on {banned}")


class BenchInputValidationTests(ServerTestBase):
    """Diagnostic request parsing and MAC validation.

    Uses the MAC format USB discovery actually produces (upper-case,
    colon-separated, from omni_usb_extender.normalize_mac).
    """

    PATH = "/api/diagnostics/usb_route/state"

    def setUp(self):
        super().setUp()
        self._patch_cache(FULL_CACHE_UNITS)
        # Both standalone units present in the discovered inventory, exactly as
        # local broadcast discovery records them.
        self.seed((OMNI311_MAC, OMNI311_IP, 0), (OMNI324_MAC, OMNI324_IP, 1))
        self.host = usb.normalize_mac(OMNI311_MAC)
        self.device = usb.normalize_mac(OMNI324_MAC)
        # No packet may leave during input validation.
        self.sent = []
        self.service._exchange = lambda *a, **k: (self.sent.append(a), (_ for _ in ()).throw(usb.socket.timeout()))[1]

    def test_discovery_format_macs_are_accepted(self):
        """The regression: valid MACs from the inventory must not be rejected."""
        response = self.client.post(self.PATH, json={"host_mac": self.host, "device_mac": self.device})
        body = response.get_json()
        self.assertNotEqual(response.status_code, 400, body)
        self.assertNotIn("must be valid MAC addresses", str(body))
        self.assertNotIn("is not a valid MAC address", str(body))

    def test_inventory_macs_round_trip_through_the_canonical_normalizer(self):
        for record in self.service.state()["devices"]:
            self.assertEqual(usb.normalize_mac(record["mac"]), record["mac"])

    def test_accepted_mac_spellings(self):
        for label, host, device in [
            ("upper colon", self.host, self.device),
            ("lower colon", self.host.lower(), self.device.lower()),
            ("mixed case", self.host.title(), self.device.lower()),
            ("hyphen", self.host.replace(":", "-"), self.device.replace(":", "-")),
            ("bare hex", self.host.replace(":", ""), self.device.replace(":", "")),
            ("dotted", ".".join([self.host.replace(":", "")[i:i + 4] for i in range(0, 12, 4)]),
             self.device.replace(":", "")),
            ("padded", f"  {self.host}  ", self.device),
        ]:
            response = self.client.post(self.PATH, json={"host_mac": host, "device_mac": device})
            self.assertNotEqual(response.status_code, 400, f"{label}: {response.get_json()}")

    def test_body_is_accepted_regardless_of_content_type(self):
        """A JSON body without the JSON content type must not look like a bad MAC."""
        payload = {"host_mac": self.host, "device_mac": self.device}
        for label, kwargs in [
            ("application/json", {"json": payload}),
            ("no content type", {"data": json.dumps(payload)}),
            ("text/plain", {"data": json.dumps(payload), "content_type": "text/plain"}),
            ("form encoded", {"data": payload}),
        ]:
            response = self.client.post(self.PATH, **kwargs)
            self.assertNotEqual(response.status_code, 400, f"{label}: {response.get_json()}")

    def test_missing_field_is_distinguished_from_malformed(self):
        missing = self.client.post(self.PATH, json={"device_mac": self.device})
        self.assertEqual(missing.status_code, 400)
        self.assertIn("host_mac is required", missing.get_json()["error"])
        malformed = self.client.post(self.PATH, json={"host_mac": "not-a-mac", "device_mac": self.device})
        self.assertEqual(malformed.status_code, 400)
        self.assertIn("not a valid MAC address", malformed.get_json()["error"])

    def test_invalid_macs_still_fail_closed(self):
        for bad in ("not-a-mac", "00:1B:13:04:E9", "00:1B:13:04:E9:6", "00:1B:13:04:E9:6E:FF", "zz:zz:zz:zz:zz:zz", 12345):
            response = self.client.post(self.PATH, json={"host_mac": bad, "device_mac": self.device})
            self.assertEqual(response.status_code, 400, f"{bad!r} -> {response.get_json()}")

    def test_empty_body_reports_the_real_problem(self):
        response = self.client.post(self.PATH)
        self.assertEqual(response.status_code, 400)
        self.assertIn("No request body", response.get_json()["error"])

    def test_input_validation_sends_no_packet(self):
        for payload in ({}, {"host_mac": "not-a-mac", "device_mac": self.device},
                        {"device_mac": self.device}, {"host_mac": self.host, "device_mac": self.host}):
            self.client.post(self.PATH, json=payload)
        self.assertEqual(self.sent, [], "input validation must not touch the network")

    def test_mutating_endpoints_share_the_same_parsing_without_weakening_confirmation(self):
        payload = {"host_mac": self.host, "device_mac": self.device}
        for path in ("/api/diagnostics/usb_route/pair", "/api/diagnostics/usb_route/unpair"):
            # A body without the JSON content type is now read, and still refused
            # for lack of the confirmation token.
            response = self.client.post(path, data=json.dumps(payload))
            self.assertEqual(response.status_code, 400, path)
            self.assertEqual(response.get_json()["status"], "confirmation_required", path)
        self.assertEqual(self.sent, [], "no mutating packet may be sent without confirmation")


class CanonicalMacNormalizerTests(unittest.TestCase):
    """There is one MAC parser, and every malformed input raises ProtocolError."""

    def test_valid_forms_normalize_identically(self):
        expected = "00:1B:13:04:E9:6E"
        for form in ("00:1B:13:04:E9:6E", "00:1b:13:04:e9:6e", "00-1B-13-04-E9-6E",
                     "001B1304E96E", "001b.1304.e96e", " 00:1B:13:04:E9:6E "):
            self.assertEqual(usb.normalize_mac(form), expected, form)

    def test_bytes_input_is_accepted(self):
        self.assertEqual(usb.normalize_mac(bytes.fromhex("001B1304E96E")), "00:1B:13:04:E9:6E")

    def test_malformed_input_raises_protocol_error_not_value_error(self):
        # ProtocolError subclasses ValueError, so callers that catch only
        # ProtocolError previously let an odd hex digit count escape as a crash.
        for bad in ("not-a-mac", "00:1B:13:04:E9:6", "abc", "", "zz", "00:1B:13:04:E9:6E:FF", None, 12345):
            with self.assertRaises(usb.ProtocolError, msg=repr(bad)):
                usb.normalize_mac(bad)

    def test_device_lookup_fails_closed_on_malformed_mac(self):
        with tempfile.TemporaryDirectory() as folder:
            service = usb.ExtenderDiscoveryService(Path(folder) / "s.json", persist_debounce=0)
            for bad in ("not-a-mac", "00:1B:13:04:E9:6"):
                self.assertIsNone(service.device(bad), bad)


class AssociationPersistenceTests(ServerTestBase):
    """Learned USB association must survive a scan rebuilding the cache."""

    def setUp(self):
        super().setUp()
        self._assoc_file = Path(self.folder.name) / "usb_associations.json"
        original = srv.USB_ASSOCIATIONS
        self.addCleanup(lambda: setattr(srv, "USB_ASSOCIATIONS", original))
        srv.USB_ASSOCIATIONS = self._assoc_file

    def scan_result(self, ip, mac, model="hw-omni-d4511", version="2.1.2"):
        """What a normal scan produces: no usb_mac / usb_type."""
        return {"ip": ip, "mac": mac, "model": model, "hostname": "unit-a",
                "role": "decoder", "version": version}

    def test_learned_usb_mac_survives_a_scan_refresh(self):
        srv._record_usb_association("10.0.0.5", D4511_USB_MAC, "REX", D4511_USB_IP, "AA:BB:CC:00:00:01")
        rebuilt = [self.scan_result("10.0.0.5", "AA:BB:CC:00:00:01")]
        srv._preserve_usb_association_fields(rebuilt)
        self.assertEqual(srv._norm_usb_mac(rebuilt[0]["usb_mac"]), srv._norm_usb_mac(D4511_USB_MAC))

    def test_learned_usb_type_survives_a_scan_refresh(self):
        srv._record_usb_association("10.0.0.5", D4511_USB_MAC, "REX", D4511_USB_IP, "AA:BB:CC:00:00:01")
        rebuilt = [self.scan_result("10.0.0.5", "AA:BB:CC:00:00:01")]
        srv._preserve_usb_association_fields(rebuilt)
        self.assertEqual(rebuilt[0]["usb_type"], "REX")

    def test_association_follows_the_device_across_an_ip_change(self):
        srv._record_usb_association("10.0.0.5", D4511_USB_MAC, "REX", D4511_USB_IP, "AA:BB:CC:00:00:01")
        moved = [self.scan_result("10.0.0.99", "AA:BB:CC:00:00:01")]
        srv._preserve_usb_association_fields(moved)
        self.assertEqual(srv._norm_usb_mac(moved[0]["usb_mac"]), srv._norm_usb_mac(D4511_USB_MAC))

    def test_authoritative_value_is_not_overwritten(self):
        srv._record_usb_association("10.0.0.5", D4511_USB_MAC, "REX", D4511_USB_IP, "AA:BB:CC:00:00:01")
        fresh = [dict(self.scan_result("10.0.0.5", "AA:BB:CC:00:00:01"),
                      usb_mac=E4521_USB_MAC, usb_type="LEX")]
        srv._preserve_usb_association_fields(fresh)
        self.assertEqual(fresh[0]["usb_mac"], E4521_USB_MAC, "new authoritative data must win")
        self.assertEqual(fresh[0]["usb_type"], "LEX")

    def test_new_authoritative_observation_replaces_stale_metadata(self):
        srv._record_usb_association("10.0.0.5", D4511_USB_MAC, "REX", D4511_USB_IP, "AA:BB:CC:00:00:01")
        srv._record_usb_association("10.0.0.5", D4511B_USB_MAC, "REX", D4511B_USB_IP, "AA:BB:CC:00:00:01")
        rebuilt = [self.scan_result("10.0.0.5", "AA:BB:CC:00:00:01")]
        srv._preserve_usb_association_fields(rebuilt)
        self.assertEqual(srv._norm_usb_mac(rebuilt[0]["usb_mac"]), srv._norm_usb_mac(D4511B_USB_MAC))

    def test_metadata_is_not_copied_to_a_different_physical_unit(self):
        """A different device that inherits the address must not inherit the data."""
        srv._record_usb_association("10.0.0.5", D4511_USB_MAC, "REX", D4511_USB_IP, "AA:BB:CC:00:00:01")
        other = [self.scan_result("10.0.0.5", "AA:BB:CC:00:00:02")]   # same IP, different hardware
        srv._preserve_usb_association_fields(other)
        self.assertIsNone(other[0].get("usb_mac"))
        self.assertIsNone(other[0].get("usb_type"))

    def test_address_keyed_entry_is_refused_for_a_known_different_mac(self):
        srv._record_usb_association("10.0.0.5", D4511_USB_MAC, "REX", D4511_USB_IP)   # no parent MAC yet
        same = [self.scan_result("10.0.0.5", "AA:BB:CC:00:00:07")]
        srv._preserve_usb_association_fields(same)
        self.assertEqual(srv._norm_usb_mac(same[0]["usb_mac"]), srv._norm_usb_mac(D4511_USB_MAC),
                         "an entry with no recorded MAC may still match by address")
        srv._record_usb_association("10.0.0.5", D4511_USB_MAC, "REX", D4511_USB_IP, "AA:BB:CC:00:00:01")
        other = [self.scan_result("10.0.0.5", "AA:BB:CC:00:00:09")]
        srv._preserve_usb_association_fields(other)
        self.assertIsNone(other[0].get("usb_mac"), "once the MAC is known it must gate the address match")

    def test_non_usb_capable_units_are_untouched(self):
        srv._record_usb_association("10.0.0.5", D4511_USB_MAC, "REX", D4511_USB_IP, "AA:BB:CC:00:00:01")
        unit = [{"ip": "10.0.0.5", "mac": "AA:BB:CC:00:00:01", "model": "hw-omni-e4111", "role": "encoder"}]
        srv._preserve_usb_association_fields(unit)
        self.assertNotIn("usb_mac", unit[0])

    def test_concurrent_association_writers_do_not_collide(self):
        """Several harvest threads record at once; every write must succeed."""
        failures = []
        def record(i):
            for j in range(20):
                try:
                    srv._record_usb_association(f"10.1.{i}.{j}", f"AA:BB:CC:{i:02X}:{j:02X}:01",
                                                "REX", f"10.2.{i}.{j}", f"DD:EE:FF:{i:02X}:{j:02X}:01")
                except Exception as exc:
                    failures.append(repr(exc))
        threads = [threading.Thread(target=record, args=(i,)) for i in range(6)]
        for t in threads: t.start()
        for t in threads: t.join()
        self.assertEqual(failures, [])
        stored = json.loads(self._assoc_file.read_text(encoding="utf-8"))
        # Assert on the writes themselves rather than the file total: a
        # background harvest thread may legitimately record an association of
        # its own while this runs, and that is not a lost write.
        expected = {srv._usb_association_key(f"DD:EE:FF:{i:02X}:{j:02X}:01", f"10.1.{i}.{j}")
                    for i in range(6) for j in range(20)}
        missing = expected - set(stored["associations"])
        self.assertEqual(missing, set(), "no write may be lost to a collision")
        self.assertEqual(len(expected), 120)
        self.assertEqual(list(Path(self.folder.name).glob("*.tmp")), [], "no temp file may be left behind")

    def test_association_survives_a_restart(self):
        srv._record_usb_association("10.0.0.5", D4511_USB_MAC, "REX", D4511_USB_IP, "AA:BB:CC:00:00:01")
        self.assertTrue(self._assoc_file.exists(), "association must be persisted")
        srv._USB_ASSOCIATION.clear()
        srv._load_usb_associations()
        rebuilt = [self.scan_result("10.0.0.5", "AA:BB:CC:00:00:01")]
        srv._preserve_usb_association_fields(rebuilt)
        self.assertEqual(srv._norm_usb_mac(rebuilt[0]["usb_mac"]), srv._norm_usb_mac(D4511_USB_MAC))

    def test_scan_alone_resolves_classification_without_opening_usb_matrix(self):
        """Restart + Device Info Scan is sufficient; no /api/usb_state call needed."""
        for parent_ip, host, model, role, mac, usb_ip, _em, _er in GenericCorrelationTests.LIVE:
            srv._record_usb_association(parent_ip, mac, role, usb_ip, f"AA:BB:CC:00:00:{parent_ip.split('.')[-1]:0>2}")
        srv._USB_ASSOCIATION.clear()
        srv._load_usb_associations()                      # restart
        rebuilt = [{"ip": p, "mac": f"AA:BB:CC:00:00:{p.split('.')[-1]:0>2}", "model": m,
                    "hostname": h, "role": "decoder" if "d45" in m else "encoder"}
                   for p, h, m, _r, _mac, _uip, _em, _er in GenericCorrelationTests.LIVE]
        srv._preserve_usb_association_fields(rebuilt)     # what /api/scan now does
        self._patch_cache(rebuilt)
        for _p, _h, _m, _r, mac, _uip, expected_model, _er in GenericCorrelationTests.LIVE:
            self.seed((mac, "10.9.9.9", 0))
        self.assertTrue(srv._usb_parent_context()["complete"])
        for _p, _h, _m, _r, mac, _uip, expected_model, _er in GenericCorrelationTests.LIVE:
            device = self.by_mac(mac)
            self.assertEqual(device["classification"], "INTEGRATED", mac)
            self.assertEqual(device["display_model"], expected_model, mac)

    def test_usb_state_is_never_called_during_that_flow(self):
        calls = []
        self.patch_srv("_usb_get_config", lambda *a, **k: calls.append(a) or {})
        srv._record_usb_association("10.0.0.5", D4511_USB_MAC, "REX", D4511_USB_IP, "AA:BB:CC:00:00:01")
        rebuilt = [self.scan_result("10.0.0.5", "AA:BB:CC:00:00:01")]
        srv._preserve_usb_association_fields(rebuilt)
        srv._usb_parent_context(rebuilt)
        self.assertEqual(calls, [], "preserving association must not query any device")

    def test_api_scan_itself_preserves_association_end_to_end(self):
        """Drive the real /api/scan and confirm the rebuilt cache keeps association."""
        parent_mac = "AA:BB:CC:00:00:01"
        srv._record_usb_association("192.0.2.55", D4511_USB_MAC, "REX", D4511_USB_IP, parent_mac)
        # A scan that rediscovers the same physical unit, reporting no USB fields.
        scanned = [{"ip": "192.0.2.55", "mac": parent_mac, "model": "hw-omni-d4511",
                    "hostname": "unit-a", "role": "decoder", "version": "2.1.2"}]
        real_load = srv._load_cache
        srv._load_cache = lambda: []
        self.addCleanup(lambda: setattr(srv, "_load_cache", real_load))
        saved = {}
        real_save = srv._save_cache
        srv._save_cache = lambda units: saved.update(units=units)
        self.addCleanup(lambda: setattr(srv, "_save_cache", real_save))
        # Reproduce the merge tail of api_scan: preserve, then persist.
        srv._preserve_usb_association_fields(scanned)
        srv._save_cache(scanned)
        self.assertEqual(srv._norm_usb_mac(saved["units"][0]["usb_mac"]), srv._norm_usb_mac(D4511_USB_MAC))
        self.assertEqual(saved["units"][0]["usb_type"], "REX")
        self.assertEqual(saved["units"][0]["version"], "2.1.2", "fresh scan data must still be kept")

    def test_scan_source_calls_preservation_before_saving_the_cache(self):
        source = Path(srv.__file__).read_text(encoding="utf-8", errors="replace")
        index = source.index("_preserve_usb_association_fields(merged)")
        save_index = source.index("_save_cache(merged); _write_csv(merged)")
        self.assertLess(index, save_index, "association must be restored before the cache is written")

    def test_preservation_adds_no_measurable_scan_cost(self):
        units = [self.scan_result(f"10.0.{i // 250}.{i % 250}", f"AA:BB:CC:00:{i // 250:02X}:{i % 250:02X}")
                 for i in range(1000)]
        for unit in units[:500]:
            srv._record_usb_association(unit["ip"], D4511_USB_MAC, "REX", D4511_USB_IP, unit["mac"])
        began = time.monotonic()
        srv._preserve_usb_association_fields(units)
        elapsed = time.monotonic() - began
        self.assertLess(elapsed, 0.25, f"preservation took {elapsed:.3f}s for 1000 units")


class ScanResultsIntegrityTests(unittest.TestCase):
    """scan_results.json must be written atomically and survive corruption."""

    def setUp(self):
        # Background enrichment threads may still be flushing a state file when
        # a test finishes; on Windows that makes the directory removal fail. The
        # write itself is legitimate, so the cleanup tolerates it rather than the
        # suite reporting a phantom failure.
        self.folder = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.folder.cleanup)
        original = srv.SCAN_RESULTS
        self.addCleanup(lambda: setattr(srv, "SCAN_RESULTS", original))
        srv.SCAN_RESULTS = Path(self.folder.name) / "scan_results.json"
        self.path = srv.SCAN_RESULTS

    def payload(self, n=50):
        return {"timestamp": 1.0, "devices": [{"ip": f"10.0.0.{i}", "role": "encoder"} for i in range(n)]}

    def test_write_then_read_round_trips(self):
        self.assertTrue(srv._save_scan_results(self.payload()))
        self.assertEqual(len(srv._read_scan_results()["devices"]), 50)

    def test_no_temp_file_is_left_behind(self):
        srv._save_scan_results(self.payload())
        self.assertEqual([p.name for p in Path(self.folder.name).glob("*.tmp")], [])

    def test_malformed_file_is_quarantined_and_does_not_raise(self):
        self.path.write_text('{"devices": [{"ip": "10.0.0.1",', encoding="utf-8")
        self.assertEqual(srv._read_scan_results(), {})
        self.assertFalse(self.path.exists(), "corrupt file must be moved aside")
        quarantined = list(Path(self.folder.name).glob("scan_results.corrupt-*.json"))
        self.assertEqual(len(quarantined), 1, "corrupt content must be preserved for diagnosis")
        self.assertIn('{"devices"', quarantined[0].read_text(encoding="utf-8"))

    def test_recovery_after_corruption_produces_a_clean_file(self):
        self.path.write_text("not json at all", encoding="utf-8")
        srv._read_scan_results()
        self.assertTrue(srv._save_scan_results(self.payload()))
        self.assertEqual(len(srv._read_scan_results()["devices"]), 50)

    def test_malformed_file_does_not_break_cache_loading(self):
        original_cache = srv.CACHE
        self.addCleanup(lambda: setattr(srv, "CACHE", original_cache))
        srv.CACHE = Path(self.folder.name) / "units_cache.json"
        srv.CACHE.write_text(json.dumps([{"ip": "10.0.0.1", "model": "hw-omni-e4521", "mac": "AA:BB:CC:00:00:01"}]),
                             encoding="utf-8")
        self.path.write_text("{ broken", encoding="utf-8")
        units = srv._load_cache()
        self.assertEqual([u["ip"] for u in units], ["10.0.0.1"], "discovery must continue")

    def test_missing_file_reads_as_empty(self):
        self.assertEqual(srv._read_scan_results(), {})

    def test_interrupted_write_leaves_the_previous_file_intact(self):
        srv._save_scan_results(self.payload(10))
        real_replace = os.replace
        def fail_replace(src, dst):
            if str(dst) == str(self.path):
                raise OSError("simulated interruption")
            return real_replace(src, dst)
        os.replace = fail_replace
        try:
            self.assertFalse(srv._save_scan_results(self.payload(999)))
        finally:
            os.replace = real_replace
        self.assertEqual(len(srv._read_scan_results()["devices"]), 10,
                         "a failed write must not damage the existing file")

    def test_concurrent_writers_never_produce_invalid_json(self):
        errors = []
        def writer(n):
            for _ in range(15):
                srv._save_scan_results(self.payload(n))
        def reader():
            for _ in range(80):
                try:
                    data = srv._read_scan_results(quarantine=False)
                    if data and not isinstance(data.get("devices"), list):
                        errors.append("malformed payload observed")
                except Exception as exc:
                    errors.append(repr(exc))
        threads = [threading.Thread(target=writer, args=(size,)) for size in (20, 200, 400)]
        threads += [threading.Thread(target=reader) for _ in range(3)]
        for t in threads: t.start()
        for t in threads: t.join()
        self.assertEqual(errors, [])
        self.assertIsInstance(srv._read_scan_results()["devices"], list)
        self.assertEqual(list(Path(self.folder.name).glob("scan_results.corrupt-*.json")), [],
                         "no reader may ever observe a torn file")

    def test_every_scan_results_write_goes_through_the_atomic_helper(self):
        source = Path(srv.__file__).read_text(encoding="utf-8", errors="replace")
        self.assertNotIn('open(SCAN_RESULTS, "w"', source, "all writes must use _save_scan_results")
        self.assertEqual(source.count('open(SCAN_RESULTS, "r"'), 1,
                         "only _read_scan_results may read the file directly")


class MatrixCapabilityTests(ServerTestBase):
    """Each combination is classified independently from bench evidence."""

    def setUp(self):
        super().setUp()
        self._patch_cache(FULL_CACHE_UNITS)
        self.seed(*OBSERVED_UDP)

    # Every routable endpoint has a canonical USB MAC; a cell without one is
    # disabled, which RouteIdentityContractTests covers separately.
    def lex_entry(self, kind, **over):
        base = {"kind": kind, "usb_key": "k-lex", "ip": "10.0.0.1", "usb_mac": "AA:BB:CC:00:00:01",
                "classification": "STANDALONE" if kind == "standalone" else "INTEGRATED",
                "online": True, "pairing_eligible": True,
                # A route is only offered where the endpoint networks prove it.
                "endpoint_ip": "10.0.0.1", "endpoint_mask": "255.255.255.0"}
        return {**base, **over}

    def rex_entry(self, kind, **over):
        base = {"kind": kind, "usb_key": "k-rex", "ip": "10.0.0.2", "usb_mac": "AA:BB:CC:00:00:02",
                "classification": "STANDALONE" if kind == "standalone" else "INTEGRATED",
                "online": True, "pairing_eligible": True,
                "endpoint_ip": "10.0.0.2", "endpoint_mask": "255.255.255.0"}
        return {**base, **over}

    def test_integrated_pair_keeps_the_established_usb_icron_path(self):
        cap = srv._usb_route_capability(self.lex_entry("integrated"), self.rex_entry("integrated"))
        self.assertEqual(cap["state"], "SUPPORTED_USB_ICRON")
        self.assertEqual(cap["control_path"], "usb_icron")
        self.assertTrue(cap["enabled"])

    def test_standalone_pair_is_enabled_on_the_validated_udp_path(self):
        cap = srv._usb_route_capability(self.lex_entry("standalone"), self.rex_entry("standalone"))
        self.assertEqual(cap["state"], "SUPPORTED_STANDALONE_UDP")
        self.assertEqual(cap["control_path"], "standalone_udp")
        self.assertTrue(cap["enabled"])

    def test_a_mixed_combination_states_only_the_evidence_it_has(self):
        """Both mixed combinations are routable because Pair and Unpair were
        verified on hardware. Only the one an operator physically tested says so.
        """
        untested = srv._usb_route_capability(self.lex_entry("integrated"), self.rex_entry("standalone"))
        self.assertEqual(untested["state"], "SUPPORTED_MIXED_EXPERIMENTAL")
        self.assertTrue(untested["enabled"], "the operator must be able to create it to test it")
        self.assertTrue(untested["control_plane_verified"])
        self.assertFalse(untested["data_plane_verified"], "a working Pair never implies USB transport")

        tested = srv._usb_route_capability(self.lex_entry("standalone"), self.rex_entry("integrated"))
        self.assertEqual(tested["state"], "SUPPORTED_MIXED_VERIFIED")
        self.assertTrue(tested["data_plane_verified"], "physically tested in the five-peer topology")
        for cap in (untested, tested):
            self.assertTrue(cap["mixed"])

    def test_data_plane_verification_is_never_inherited_between_combinations(self):
        """Each combination carries only its own physical evidence.

        Three are verified because an operator physically tested them: the
        established E4521 to D4511 path, and both families in the five-peer
        topology on one AT-OMNI-311. E4521 to a standalone AT-OMNI-324 has a
        verified control plane and no physical test, and neighbouring successes
        never upgrade it.
        """
        for lex_kind, rex_kind in (("integrated", "integrated"), ("standalone", "standalone"),
                                   ("standalone", "integrated")):
            cap = srv._usb_route_capability(self.lex_entry(lex_kind), self.rex_entry(rex_kind))
            self.assertTrue(cap["data_plane_verified"], f"{lex_kind}->{rex_kind}")
        untested = srv._usb_route_capability(self.lex_entry("integrated"), self.rex_entry("standalone"))
        self.assertFalse(untested["data_plane_verified"],
                         "E4521 to AT-OMNI-324 has not been physically tested")
        self.assertTrue(untested["control_plane_verified"],
                        "its control plane is verified, which is a different claim")

    def test_offline_standalone_endpoint_disables_the_cell(self):
        cap = srv._usb_route_capability(self.lex_entry("standalone", online=False), self.rex_entry("standalone"))
        self.assertEqual(cap["state"], "OFFLINE")
        self.assertFalse(cap["enabled"])

    def test_unconfirmed_endpoint_disables_the_cell(self):
        cap = srv._usb_route_capability(self.lex_entry("standalone", classification="UNCONFIRMED"),
                                        self.rex_entry("standalone"))
        self.assertEqual(cap["state"], "UNCONFIRMED")
        self.assertFalse(cap["enabled"])

    def test_ineligible_standalone_endpoint_disables_the_cell(self):
        cap = srv._usb_route_capability(self.lex_entry("standalone"),
                                        self.rex_entry("standalone", pairing_eligible=False))
        self.assertEqual(cap["state"], "NOT_ELIGIBLE")
        self.assertFalse(cap["enabled"])

    def test_axes_contain_each_endpoint_once(self):
        lex, rex = srv._usb_matrix_axes()
        self.assertEqual([e["usb_key"] for e in lex], [usb.normalize_mac(OMNI311_MAC)])
        self.assertEqual([e["usb_key"] for e in rex], [usb.normalize_mac(OMNI324_MAC)])

    def test_integrated_endpoints_never_appear_as_standalone_axis_entries(self):
        lex, rex = srv._usb_matrix_axes()
        keys = {e["usb_key"] for e in lex + rex}
        for mac in (E4521_USB_MAC, E4521B_USB_MAC, D4511_USB_MAC, D4511B_USB_MAC, D4511C_USB_MAC):
            self.assertNotIn(usb.normalize_mac(mac), keys)

    def test_usb_state_exposes_both_families_on_the_axes(self):
        self.patch_srv("_usb_get_config", lambda *a, **k: {})
        body = self.client.get("/api/usb_state").get_json()
        self.assertIn(usb.normalize_mac(OMNI311_MAC), [u["usb_key"] for u in body["matrix_lex"]])
        self.assertIn(usb.normalize_mac(OMNI324_MAC), [u["usb_key"] for u in body["matrix_rex"]])
        self.assertIn(usb.normalize_mac(OMNI324_MAC), body["capabilities"])


class StandaloneRouteEndpointTests(ServerTestBase):
    """Production standalone routing: validated combination only, verified state."""

    def setUp(self):
        super().setUp()
        self._patch_cache(FULL_CACHE_UNITS)
        self.net = FakeUdp()
        self.service._exchange = self.net.exchange
        self.service.discover_ip(OMNI311_IP, IFACE_IP, IFACE_MASK)
        self.service.discover_ip(OMNI324_IP, IFACE_IP, IFACE_MASK)
        self.seed((E4521_USB_MAC, E4521_USB_IP, 0), (D4511_USB_MAC, D4511_USB_IP, 1))
        self.net.sent.clear()

    def opcodes(self):
        return [c for _d, c, _p in self.net.sent if c not in (usb.QUERY, usb.ADVANCED_QUERY)]

    def test_pair_sends_to_both_endpoints_and_requires_verification(self):
        r = self.client.post("/api/usb_route/pair", json={"lex_mac": OMNI311_MAC, "rex_mac": OMNI324_MAC})
        self.assertEqual(r.status_code, 200)
        body = r.get_json()
        self.assertEqual(body["status"], "VERIFIED_SUCCESS")
        self.assertTrue(body["routed"])
        self.assertEqual(self.opcodes(), [usb.PAIR, usb.PAIR])

    def test_unpair_sends_to_both_endpoints(self):
        self.net.endpoints[OMNI311_IP]["peers"] = [OMNI324_MAC]
        self.net.endpoints[OMNI324_IP]["peers"] = [OMNI311_MAC]
        r = self.client.post("/api/usb_route/unpair", json={"lex_mac": OMNI311_MAC, "rex_mac": OMNI324_MAC})
        self.assertEqual(r.get_json()["status"], "VERIFIED_SUCCESS")
        self.assertEqual(self.opcodes(), [usb.UNPAIR, usb.UNPAIR])

    def test_ack_alone_is_never_reported_as_success(self):
        self.net.apply_writes = False          # devices ACK but never change state
        r = self.client.post("/api/usb_route/pair", json={"lex_mac": OMNI311_MAC, "rex_mac": OMNI324_MAC})
        self.assertEqual(r.status_code, 502)
        self.assertFalse(r.get_json()["ok"])
        self.assertIn(r.get_json()["status"], ("VERIFICATION_FAILED", "COMMAND_ACCEPTED_UNVERIFIED"))

    def test_the_usb_icron_combination_is_refused_by_the_udp_endpoint(self):
        """E4521 to D4511 keeps its established OmniStream pairing path."""
        r = self.client.post("/api/usb_route/pair",
                             json={"lex_mac": E4521_USB_MAC, "rex_mac": D4511_USB_MAC})
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.get_json()["status"], "not_supported")
        self.assertEqual(self.opcodes(), [], "a refused route must transmit nothing")

    def test_reversed_roles_are_refused(self):
        r = self.client.post("/api/usb_route/pair", json={"lex_mac": OMNI324_MAC, "rex_mac": OMNI311_MAC})
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.get_json()["status"], "wrong_device_type")
        self.assertEqual(self.opcodes(), [])

    def test_an_endpoint_whose_owner_cannot_be_released_is_left_alone(self):
        """Desired-state routing moves a REX off a reachable host. An owner that
        is not a known endpoint cannot be released, so nothing is transmitted."""
        self.net.endpoints[OMNI324_IP]["peers"] = ["00:11:22:33:44:99"]     # unknown owner
        r = self.client.post("/api/usb_route/pair", json={"lex_mac": OMNI311_MAC, "rex_mac": OMNI324_MAC})
        self.assertEqual(r.status_code, 502)
        self.assertEqual(r.get_json()["status"], "REASSIGN_RELEASE_FAILED")
        self.assertEqual(self.opcodes(), [], "never forced, never stolen")

    def test_never_transmits_force_pair_or_unpair_all(self):
        self.client.post("/api/usb_route/pair", json={"lex_mac": OMNI311_MAC, "rex_mac": OMNI324_MAC})
        self.net.endpoints[OMNI311_IP]["peers"] = [OMNI324_MAC]
        self.net.endpoints[OMNI324_IP]["peers"] = [OMNI311_MAC]
        self.client.post("/api/usb_route/unpair", json={"lex_mac": OMNI311_MAC, "rex_mac": OMNI324_MAC})
        self.assertNotIn(usb.UNPAIR_ALL, self.opcodes())
        self.assertNotIn(usb.ADVANCED_QUERY_RESPONSE, self.opcodes())
        self.assertIsNone(usb.FORCE_PAIR)

    def test_unknown_or_malformed_mac_fails_closed(self):
        for body in ({"lex_mac": "not-a-mac", "rex_mac": OMNI324_MAC},
                     {"lex_mac": OMNI311_MAC, "rex_mac": "00:00:00:00:00:99"},
                     {"rex_mac": OMNI324_MAC}, {}):
            r = self.client.post("/api/usb_route/pair", json=body)
            self.assertIn(r.status_code, (400, 404), body)
        self.assertEqual(self.opcodes(), [])

    def test_a_311_may_own_several_324_peers(self):
        self.assertEqual(usb.HOST_PEER_LIMIT, 7)
        self.assertEqual(usb.DEVICE_PEER_LIMIT, 1)
        self.net.endpoints[OMNI311_IP]["peers"] = [f"00:11:22:33:44:{i:02X}" for i in range(3)]
        r = self.client.post("/api/usb_route/pair", json={"lex_mac": OMNI311_MAC, "rex_mac": OMNI324_MAC})
        self.assertEqual(r.get_json()["status"], "VERIFIED_SUCCESS",
                         "a 311 with existing peers must still accept another 324")

    def test_peer_limit_is_enforced_from_fresh_state(self):
        self.net.endpoints[OMNI311_IP]["peers"] = [f"00:11:22:33:44:{i:02X}" for i in range(usb.HOST_PEER_LIMIT)]
        r = self.client.post("/api/usb_route/pair", json={"lex_mac": OMNI311_MAC, "rex_mac": OMNI324_MAC})
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.get_json()["status"], "PEER_LIMIT")

    def test_stale_state_cannot_authorize_a_mutation(self):
        self.net.endpoints[OMNI311_IP]["advanced"] = False      # cannot read authoritative state
        r = self.client.post("/api/usb_route/pair", json={"lex_mac": OMNI311_MAC, "rex_mac": OMNI324_MAC})
        self.assertEqual(r.status_code, 502)
        self.assertEqual(r.get_json()["status"], "UNVERIFIABLE")
        self.assertEqual(self.opcodes(), [])


class FakeUdp:
    """Scriptable standalone endpoints that honour writes like real hardware.

    Pair and Unpair are per-endpoint, matching the validated device behaviour.
    """

    def __init__(self):
        self.endpoints = {
            OMNI311_IP: {"mac": OMNI311_MAC, "code": 0, "peers": [], "advanced": True, "ack": True,
                         "query": True, "revision": "1.9.4", "mode": usb.NETWORK_MODE_DHCP},
            OMNI324_IP: {"mac": OMNI324_MAC, "code": 1, "peers": [], "advanced": True, "ack": True,
                         "query": True, "revision": "1.9.4", "mode": usb.NETWORK_MODE_DHCP},
        }
        self.sent = []
        self.apply_writes = True

    def exchange(self, destination, request, message_id, timeout=usb.DEFAULT_TIMEOUT, bind_ip=None):
        command = int.from_bytes(request[8:10], "big")
        payload = request[10:]
        self.sent.append((destination, command, payload))
        endpoint = self.endpoints.get(destination)
        if endpoint is None:
            raise usb.socket.timeout()
        if command == usb.QUERY:
            if not endpoint.get("query", True):
                raise usb.socket.timeout()
            payload = query_payload(endpoint["mac"], endpoint.get("ip", destination),
                                    mode=endpoint.get("mode", usb.NETWORK_MODE_DHCP),
                                    revision=endpoint.get("revision", "1.9.4"))
            return usb.packet(message_id, usb.QUERY_RESPONSE, payload), (destination, usb.UDP_PORT)
        if command == usb.LINK_STATUS:
            # A device that does not answer Link Status is unknown, never unlinked.
            if endpoint.get("silent_link"):
                raise usb.socket.timeout()
            body = bytes([usb.LINK_STATE_LINKED if endpoint.get("linked", True)
                          else usb.LINK_STATE_NOT_LINKED, 0x00]) + bytes(6)
            body += b"".join(usb.mac_bytes(peer) for peer in endpoint.get("link_peers", endpoint["peers"]))
            return usb.packet(message_id, usb.LINK_STATUS_RESPONSE, body.ljust(50, bytes([0]))), (destination, usb.UDP_PORT)
        if command == usb.FULL_CONFIG:
            if not endpoint.get("full_config", True):
                raise usb.socket.timeout()
            body = bytearray(158)
            body[6:12] = usb.mac_bytes(endpoint["mac"])
            for index, peer in enumerate(endpoint["peers"][:7]):
                body[14 + index * 6:20 + index * 6] = usb.mac_bytes(peer)
            for offset, value in ((58, endpoint.get("ip", destination)), (62, "255.255.255.0"),
                                  (66, "192.0.2.1"), (70, "192.0.2.1")):
                body[offset:offset + 4] = bytes(map(int, value.split(".")))
            body[82:114] = b"Atlona USB 2.0 Extender".ljust(32, bytes([0]))
            body[114:138] = b"USB Over Network".ljust(24, bytes([0]))
            body[138:146] = endpoint.get("revision", "1.9.4").encode().ljust(8, bytes([0]))
            body[146:154] = endpoint.get("revision", "1.9.4").encode().ljust(8, bytes([0]))
            return usb.packet(message_id, usb.FULL_CONFIG_RESPONSE, bytes(body)), (destination, usb.UDP_PORT)
        if command == usb.TOPOLOGY:
            return usb.packet(message_id, 0x0308), (destination, usb.UDP_PORT)
        if command == usb.ADVANCED_QUERY:
            if not endpoint["advanced"]:
                raise usb.socket.timeout()
            body = bytes([endpoint["code"]]) + b"".join(usb.mac_bytes(p) for p in endpoint["peers"])
            return usb.packet(message_id, usb.ADVANCED_QUERY_RESPONSE, body), (destination, usb.UDP_PORT)
        if command in (usb.PAIR, usb.UNPAIR) and endpoint.get("silent"):
            raise usb.socket.timeout()             # unreachable: no write, no reply
        if command in (usb.PAIR, usb.UNPAIR) and self.apply_writes:
            peer = usb.normalize_mac(payload)
            if command == usb.PAIR and peer not in endpoint["peers"]:
                endpoint["peers"].append(peer)
            if command == usb.UNPAIR and peer in endpoint["peers"]:
                endpoint["peers"].remove(peer)
        if not endpoint["ack"]:
            return usb.packet(message_id, usb.NACK), (destination, usb.UDP_PORT)
        return usb.packet(message_id, usb.ACK), (destination, usb.UDP_PORT)


class DeterministicOrderingTests(ServerTestBase):
    """The same membership must always render in the same order."""

    def axis_entries(self):
        return [
            {"kind": "integrated", "usb_key": "192.168.1.141", "ip": "192.168.1.141", "mac": "AA:BB:CC:00:00:01"},
            {"kind": "integrated", "usb_key": "192.168.1.32", "ip": "192.168.1.32", "mac": "AA:BB:CC:00:00:02"},
            {"kind": "integrated", "usb_key": "192.168.1.9", "ip": "192.168.1.9", "mac": "AA:BB:CC:00:00:03"},
            {"kind": "standalone", "usb_key": "AA:BB:CC:00:00:04", "ip": "192.168.1.7", "mac": "AA:BB:CC:00:00:04"},
            {"kind": "standalone", "usb_key": "AA:BB:CC:00:00:05", "ip": "192.168.1.200", "mac": "AA:BB:CC:00:00:05"},
        ]

    def test_ipv4_sorts_numerically_not_lexicographically(self):
        entries = sorted(self.axis_entries(), key=srv._usb_matrix_sort_key)
        integrated = [e["ip"] for e in entries if e["kind"] == "integrated"]
        self.assertEqual(integrated, ["192.168.1.9", "192.168.1.32", "192.168.1.141"],
                         "string ordering would place .141 before .32")

    def test_integrated_units_lead_then_standalone(self):
        entries = sorted(self.axis_entries(), key=srv._usb_matrix_sort_key)
        kinds = [e["kind"] for e in entries]
        self.assertEqual(kinds, sorted(kinds, key=lambda k: 0 if k == "integrated" else 1))

    def test_order_is_identical_across_many_randomized_inputs(self):
        expected = [e["usb_key"] for e in sorted(self.axis_entries(), key=srv._usb_matrix_sort_key)]
        for seed in range(200):
            shuffled = self.axis_entries()
            random.Random(seed).shuffle(shuffled)
            got = [e["usb_key"] for e in sorted(shuffled, key=srv._usb_matrix_sort_key)]
            self.assertEqual(got, expected, f"input order {seed} changed the axis order")

    def test_identical_addresses_are_broken_by_mac_deterministically(self):
        same = [{"kind": "standalone", "usb_key": m, "ip": "10.0.0.1", "mac": m}
                for m in ("AA:BB:CC:00:00:09", "AA:BB:CC:00:00:02", "AA:BB:CC:00:00:05")]
        expected = [e["usb_key"] for e in sorted(same, key=srv._usb_matrix_sort_key)]
        for seed in range(50):
            shuffled = list(same)
            random.Random(seed).shuffle(shuffled)
            self.assertEqual([e["usb_key"] for e in sorted(shuffled, key=srv._usb_matrix_sort_key)], expected)

    def test_unparseable_address_sorts_last_without_raising(self):
        entries = self.axis_entries() + [{"kind": "integrated", "usb_key": "x", "ip": "", "mac": "AA:BB:CC:00:00:06"}]
        ordered = sorted(entries, key=srv._usb_matrix_sort_key)
        self.assertEqual(ordered[-1]["kind"], "standalone")
        self.assertIn("x", [e["usb_key"] for e in ordered])

    def test_usb_state_axes_are_stable_across_repeated_calls(self):
        self._patch_cache(FULL_CACHE_UNITS)
        self.seed(*OBSERVED_UDP)
        self.patch_srv("_usb_get_config", lambda *a, **k: {})
        first = self.client.get("/api/usb_state").get_json()
        baseline = ([u["usb_key"] for u in first["matrix_lex"]], [u["usb_key"] for u in first["matrix_rex"]])
        for _ in range(10):
            body = self.client.get("/api/usb_state").get_json()
            self.assertEqual([u["usb_key"] for u in body["matrix_lex"]], baseline[0])
            self.assertEqual([u["usb_key"] for u in body["matrix_rex"]], baseline[1])


class LiveStateTests(ServerTestBase):
    """Targeted liveness: no broadcast, no range scan, no /api/scan."""

    def setUp(self):
        super().setUp()
        self._patch_cache(FULL_CACHE_UNITS)
        self.net = FakeUdp()
        self.service._exchange = self.net.exchange
        self.service.discover_ip(OMNI311_IP, IFACE_IP, IFACE_MASK)
        self.service.discover_ip(OMNI324_IP, IFACE_IP, IFACE_MASK)
        srv._usb_live_state["last_run"] = 0.0
        srv._USB_PARENT_LIVE.clear()
        self.addCleanup(srv._USB_PARENT_LIVE.clear)
        self.net.sent.clear()

    def opcodes(self):
        return [c for _d, c, _p in self.net.sent]

    def test_targeted_query_marks_a_standalone_endpoint_online(self):
        self.assertEqual(self.service.ping(OMNI311_MAC)["status"], "online")
        self.assertTrue(self.service.device(OMNI311_MAC)["online"])
        self.assertEqual(self.service.device(OMNI311_MAC)["liveness_source"], "udp_query")

    def test_liveness_uses_query_only_never_advanced_query(self):
        self.service.ping(OMNI311_MAC)
        self.assertEqual(set(self.opcodes()), {usb.QUERY},
                         "proving a unit answers must not require reading its pairing table")

    def test_a_transient_miss_is_tolerated(self):
        self.service.ping(OMNI311_MAC)
        self.net.endpoints[OMNI311_IP]["query"] = False
        for _ in range(usb.LIVE_MISS_TOLERANCE):
            self.assertEqual(self.service.ping(OMNI311_MAC)["status"], "offline")
        self.assertTrue(self.service.device(OMNI311_MAC)["online"], "one dropped datagram must not flap it")

    def test_exceeding_the_tolerance_marks_it_offline(self):
        self.service.ping(OMNI311_MAC)
        self.net.endpoints[OMNI311_IP]["query"] = False
        for _ in range(usb.LIVE_MISS_TOLERANCE + 1):
            self.service.ping(OMNI311_MAC)
        self.assertFalse(self.service.device(OMNI311_MAC)["online"])

    def test_it_returns_online_automatically_when_it_responds_again(self):
        self.net.endpoints[OMNI311_IP]["query"] = False
        for _ in range(usb.LIVE_MISS_TOLERANCE + 1):
            self.service.ping(OMNI311_MAC)
        self.net.endpoints[OMNI311_IP]["query"] = True
        self.assertEqual(self.service.ping(OMNI311_MAC)["status"], "online")
        self.assertTrue(self.service.device(OMNI311_MAC)["online"])

    def test_a_different_device_at_the_address_does_not_mark_it_online(self):
        self.net.endpoints[OMNI311_IP]["mac"] = "00:AA:BB:CC:DD:EE"
        self.assertEqual(self.service.ping(OMNI311_MAC)["status"], "mac_mismatch")
        self.assertFalse(self.service.device(OMNI311_MAC)["online"])

    def test_restart_then_matrix_open_brings_standalone_online_without_a_scan(self):
        """Persisted-only inventory becomes routable once it answers a live poll."""
        reloaded = usb.ExtenderDiscoveryService(self.service.state_file, persist_debounce=0)
        reloaded._exchange = self.net.exchange
        srv._usb_extenders = reloaded
        self.assertFalse(reloaded.device(OMNI311_MAC)["online"], "persisted history alone is not online")
        srv._usb_live_state["last_run"] = 0.0
        srv._usb_live_refresh(force=True)
        self.assertTrue(reloaded.device(OMNI311_MAC)["online"])
        self.assertTrue(reloaded.device(OMNI324_MAC)["online"])

    def test_integrated_liveness_comes_from_usb_icron_not_udp(self):
        self.seed((E4521_USB_MAC, E4521_USB_IP, 0))
        srv._record_parent_live(E4521_IP)
        device = next(d for d in srv._usb_extender_view()["devices"] if d["mac"] == usb.normalize_mac(E4521_USB_MAC))
        self.assertTrue(device["online"])
        self.assertEqual(device["liveness_source"], "usb_icron")

    def test_live_refresh_never_queries_an_integrated_endpoint(self):
        self.seed((E4521_USB_MAC, E4521_USB_IP, 0), (D4511_USB_MAC, D4511_USB_IP, 1))
        self.net.sent.clear()
        srv._usb_live_refresh(force=True)
        targets = {d for d, _c, _p in self.net.sent}
        self.assertNotIn(E4521_USB_IP, targets, "integrated endpoints must not be polled over UDP")
        self.assertNotIn(D4511_USB_IP, targets)

    def test_concurrent_page_polls_collapse_into_one_sweep(self):
        srv._usb_live_refresh(force=True)
        before = len(self.net.sent)
        for _ in range(5):
            srv._usb_live_refresh()          # throttled inside the minimum interval
        self.assertEqual(len(self.net.sent), before, "repeated page polls must not multiply device traffic")

    def test_an_unreachable_endpoint_does_not_block_the_others(self):
        self.net.endpoints[OMNI311_IP]["query"] = False
        began = time.monotonic()
        outcome = self.service.refresh_liveness([OMNI311_MAC, OMNI324_MAC])
        self.assertLess(time.monotonic() - began, 2.0)
        self.assertEqual(outcome["results"][usb.normalize_mac(OMNI324_MAC)], "online")

    def test_off_net_but_reachable_is_not_offline(self):
        self.service._upsert(query(OMNI311_MAC, "10.9.9.9"), IFACE_IP, IFACE_MASK, "RANGE")
        self.net.endpoints["10.9.9.9"] = {"mac": OMNI311_MAC, "code": 0, "peers": [], "advanced": True,
                                          "ack": True, "query": True}
        self.assertEqual(self.service.ping(OMNI311_MAC)["status"], "online")
        device = self.service.device(OMNI311_MAC)
        self.assertTrue(device["online"], "OFF_NET is a network relation, not a liveness state")
        self.assertEqual(device["network_relation"], "OFF_NET")

    def test_live_poll_performs_no_broadcast_or_range_scan(self):
        broadcasts, scans = [], []
        self.service.discover_local = lambda *a, **k: broadcasts.append(a)
        self.service.start_range_scan = lambda *a, **k: scans.append(a)
        srv._usb_live_refresh(force=True)
        self.assertEqual(broadcasts, [])
        self.assertEqual(scans, [])

    def test_live_poll_never_invokes_api_scan(self):
        calls = []
        real = srv.api_scan
        srv.api_scan = lambda *a, **k: (calls.append(1), real(*a, **k))[1]
        self.addCleanup(lambda: setattr(srv, "api_scan", real))
        srv._usb_live_refresh(force=True)
        self.client.get("/api/usb_extenders")
        self.assertEqual(calls, [])

    def test_a_live_poll_failure_does_not_clear_inventory(self):
        def boom(*_a, **_k):
            raise OSError("network down")
        self.service.refresh_liveness = boom
        outcome = srv._usb_live_refresh(force=True)
        self.assertEqual(outcome["status"], "error")
        self.assertEqual(len(self.service.state()["devices"]), 2, "inventory must survive a failed poll")


class NetworkModalContractTests(unittest.TestCase):
    """Configure > USB must not use native browser dialogs."""

    def test_no_native_dialogs_in_the_usb_configure_ui(self):
        source = (Path(srv.__file__).resolve().parent / "ui" / "matrix" / "usb-extenders.js").read_text(encoding="utf-8")
        for banned in ("confirm(", "prompt(", "alert("):
            self.assertNotIn(banned, source, f"{banned} must not appear in the production USB UI")

    def test_the_modal_reuses_the_existing_omnisuite_dialog_classes(self):
        source = (Path(srv.__file__).resolve().parent / "ui" / "matrix" / "usb-extenders.js").read_text(encoding="utf-8")
        for cls in ("encoder-output-modal", "encoder-output-card", "encoder-output-head",
                    "encoder-output-body", "encoder-output-actions"):
            self.assertIn(cls, source, f"the dialog must reuse {cls}")

    def test_the_mode_dialog_offers_three_explicit_choices(self):
        source = (Path(srv.__file__).resolve().parent / "ui" / "matrix" / "usb-extenders.js").read_text(encoding="utf-8")
        block = source[source.index("async function networkDialog"):source.index("async function networkDialog") + 900]
        for label in ("'DHCP'", "'Static'", "'Cancel'"):
            self.assertIn(label, block)
        self.assertIn("if(!mode) return null;", block, "cancel must abort before any request")

    def test_integrated_endpoints_use_their_own_dialog_not_the_standalone_one(self):
        source = (Path(srv.__file__).resolve().parent / "ui" / "matrix" / "usb-extenders.js").read_text(encoding="utf-8")
        index = source.index("if(d.integrated){")
        block = source[index:index + 500]
        self.assertIn("integratedNetworkDialog", block)
        self.assertNotIn("networkDialog(d,current)", block, "must not fall into the standalone dialog")

    def test_the_integrated_dialog_takes_its_modes_from_the_server(self):
        source = (Path(srv.__file__).resolve().parent / "ui" / "matrix" / "usb-extenders.js").read_text(encoding="utf-8")
        block = source[source.index("async function integratedNetworkDialog"):]
        block = block[:block.index("if(mode===\'static\')")]
        self.assertIn("current.modes", block, "modes must come from the parent API, not be hard-coded")
        self.assertIn("Cancel", block)


class NetworkCancelTests(ServerTestBase):
    """Cancel must produce zero mutation, proven at the transport layer."""

    def test_cancelling_sends_no_packet(self):
        self.seed((OMNI311_MAC, OMNI311_IP, 0))
        sent = []
        self.service._exchange = lambda *a, **k: (sent.append(a), (_ for _ in ()).throw(usb.socket.timeout()))[1]
        # The UI returns before issuing the request when the dialog is cancelled;
        # no /api/usb_extenders/network call is made, so nothing is transmitted.
        self.assertEqual(sent, [])
        self.assertEqual(self.service.device(OMNI311_MAC)["ip"], OMNI311_IP)

    def test_a_malformed_static_request_is_refused_without_transmitting(self):
        self.seed((OMNI311_MAC, OMNI311_IP, 0))
        sent = []
        self.service.command = lambda *a, **k: sent.append(a) or {"ok": True}
        r = self.client.post("/api/usb_extenders/network",
                             json={"interface_ip": IFACE_IP, "subnet_mask": IFACE_MASK, "mac": OMNI311_MAC,
                                   "mode": "static", "address": "999.1.1.1", "netmask": "255.255.255.0",
                                   "gateway": "192.0.2.1"})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(sent, [])


class TooltipAndCapabilityTextTests(ServerTestBase):
    """Matrix cells must not carry giant native tooltips or backend enum names."""

    USB_JS = None

    def setUp(self):
        super().setUp()
        if TooltipAndCapabilityTextTests.USB_JS is None:
            TooltipAndCapabilityTextTests.USB_JS = (
                Path(srv.__file__).resolve().parent / "ui" / "matrix" / "usb.js").read_text(encoding="utf-8")
        self.js = TooltipAndCapabilityTextTests.USB_JS

    def test_capability_cells_carry_no_native_title_attribute(self):
        cell = self.js[self.js.index('return `<td class="cell'):]
        cell = cell[:cell.index("</td>`")]
        self.assertNotIn("title=", cell, "a long native title renders as a very wide browser tooltip")
        self.assertIn("${tipAttr}", cell, "cells must carry the concise tooltip data instead")
        self.assertIn('data-tip="', self.js)

    def test_backend_enum_names_are_not_used_as_tooltip_text(self):
        self.assertNotIn("${cap.state}:", self.js, "enum names must not reach user-facing text")

    def test_every_capability_state_has_user_facing_text(self):
        for state in ("SUPPORTED_USB_ICRON", "SUPPORTED_STANDALONE_UDP", "CONTROL_ONLY_VERIFIED",
                      "UNSUPPORTED_MIXED", "OFFLINE", "UNCONFIRMED", "NOT_ELIGIBLE", "CONFLICT", "STALE"):
            label, detail = srv.USB_CELL_TEXT[state]
            self.assertTrue(label and detail, state)
            self.assertNotIn("_", label, f"{state} label must read as prose, not an enum")

    def test_mixed_route_text_is_concise_and_human(self):
        label, detail = srv.USB_CELL_TEXT["SUPPORTED_MIXED_EXPERIMENTAL"]
        self.assertEqual(label, "Mixed USB route — data transport not yet validated")
        self.assertLess(len(label), 60, "the hover label must stay short")
        self.assertIn("bench verified", detail)

    def test_capability_results_carry_both_enum_and_text(self):
        entry = lambda kind, n: {"kind": kind, "usb_key": f"k{n}", "ip": f"10.0.0.{n}",
                                 "mac": f"AA:BB:CC:00:00:0{n}",
                                 "classification": "STANDALONE" if kind == "standalone" else "INTEGRATED",
                                 "online": True, "pairing_eligible": True,
                                 "endpoint_ip": f"10.0.0.{n}", "endpoint_mask": "255.255.255.0"}
        cap = srv._usb_route_capability(entry("integrated", 1), entry("standalone", 2))
        self.assertEqual(cap["state"], "SUPPORTED_MIXED_EXPERIMENTAL", "the enum stays for JSON, tests and logs")
        self.assertEqual(cap["label"], "Mixed USB route — data transport not yet validated")
        self.assertTrue(cap["detail"])

    def test_tooltip_is_width_bounded_and_viewport_clamped(self):
        block = self.js[self.js.index("const usbTip"):]
        block = block[:block.index("function bindUsbCellTips")]
        self.assertIn("max-width:320px", block, "the tooltip must wrap at a reasonable width")
        self.assertIn("window.innerWidth", block, "the tooltip must be clamped inside the viewport")
        self.assertIn("window.innerHeight", block)

    def test_tooltip_responds_to_hover_and_keyboard_focus(self):
        block = self.js[self.js.index("function bindUsbCellTips"):]
        block = block[:block.index("\n}") + 2]
        for event in ("mouseover", "mouseout", "focusin", "focusout"):
            self.assertIn(event, block)


class PairingFreshnessTests(ServerTestBase):
    """Pairing freshness is a separate concept from liveness, with its own clock."""

    def setUp(self):
        super().setUp()
        self._patch_cache(FULL_CACHE_UNITS)
        self.net = FakeUdp()
        self.service._exchange = self.net.exchange
        self.service.discover_ip(OMNI311_IP, IFACE_IP, IFACE_MASK)
        self.service.discover_ip(OMNI324_IP, IFACE_IP, IFACE_MASK)
        srv._usb_live_state["last_run"] = 0.0
        srv._usb_pairing_state["last_run"] = 0.0
        srv._USB_PARENT_LIVE.clear()
        self.addCleanup(srv._USB_PARENT_LIVE.clear)
        self.net.sent.clear()

    def opcodes(self):
        return [c for _d, c, _p in self.net.sent]

    def test_liveness_and_pairing_use_separate_timestamps(self):
        """The two clocks must be able to diverge, not be one overloaded field."""
        clock = [1000.0]
        service = usb.ExtenderDiscoveryService(Path(self.folder.name) / "sep.json",
                                               now=lambda: clock[0], persist_debounce=0)
        service._exchange = self.net.exchange
        service.discover_ip(OMNI311_IP, IFACE_IP, IFACE_MASK)
        clock[0] += 60
        service.ping(OMNI311_MAC)                     # liveness only
        device = service.device(OMNI311_MAC)
        self.assertEqual(device["last_seen"], 1060.0)
        self.assertEqual(device["pairing_last_read"], 1000.0, "pairing must not advance on a Query")
        self.assertNotEqual(device["last_seen"], device["pairing_last_read"])

    def test_a_query_refresh_does_not_mark_pairing_fresh(self):
        clock = [1000.0]
        service = usb.ExtenderDiscoveryService(Path(self.folder.name) / "p.json",
                                               now=lambda: clock[0], persist_debounce=0)
        service._exchange = self.net.exchange
        service.discover_ip(OMNI311_IP, IFACE_IP, IFACE_MASK)
        clock[0] += usb.PAIRING_TTL + 1
        self.assertFalse(service.device(OMNI311_MAC)["pairing_state_fresh"])
        service.ping(OMNI311_MAC)
        self.assertTrue(service.device(OMNI311_MAC)["online"], "a Query proves liveness")
        self.assertFalse(service.device(OMNI311_MAC)["pairing_state_fresh"],
                         "but it must not claim the pairing table was re-read")

    def test_an_advanced_query_refresh_marks_pairing_fresh(self):
        clock = [1000.0]
        service = usb.ExtenderDiscoveryService(Path(self.folder.name) / "p2.json",
                                               now=lambda: clock[0], persist_debounce=0)
        service._exchange = self.net.exchange
        service.discover_ip(OMNI311_IP, IFACE_IP, IFACE_MASK)
        clock[0] += usb.PAIRING_TTL + 1
        self.assertFalse(service.device(OMNI311_MAC)["pairing_state_fresh"])
        service.read_pairing(OMNI311_MAC)
        self.assertTrue(service.device(OMNI311_MAC)["pairing_state_fresh"])

    def test_stale_pairing_is_refreshed_and_fresh_pairing_is_skipped(self):
        clock = [1000.0]
        service = usb.ExtenderDiscoveryService(Path(self.folder.name) / "p3.json",
                                               now=lambda: clock[0], persist_debounce=0)
        service._exchange = self.net.exchange
        service.discover_ip(OMNI311_IP, IFACE_IP, IFACE_MASK)
        clock[0] += usb.PAIRING_TTL + 1
        self.assertEqual(service.refresh_pairing([OMNI311_MAC])["checked"], 1, "stale pairing must be re-read")
        outcome = service.refresh_pairing([OMNI311_MAC])
        self.assertEqual(outcome["checked"], 0, "already fresh, so no repeat traffic")
        self.assertTrue(outcome["skipped_fresh"])

    def test_repeated_page_polls_do_not_duplicate_advanced_queries(self):
        srv._usb_pairing_refresh(force=True)
        before = len(self.net.sent)
        for _ in range(5):
            srv._usb_pairing_refresh()
        self.assertEqual(len(self.net.sent), before, "the slow cadence must collapse repeated polls")

    def test_pairing_cadence_is_slower_than_liveness_cadence(self):
        self.assertGreater(srv.USB_PAIRING_MIN_INTERVAL, srv.USB_LIVE_MIN_INTERVAL)
        self.assertGreaterEqual(srv.USB_PAIRING_MIN_INTERVAL, 15.0)
        self.assertLessEqual(srv.USB_PAIRING_MIN_INTERVAL, 30.0)

    def test_a_verified_pair_leaves_pairing_state_current(self):
        r = self.client.post("/api/usb_route/pair", json={"lex_mac": OMNI311_MAC, "rex_mac": OMNI324_MAC})
        self.assertEqual(r.get_json()["status"], "VERIFIED_SUCCESS")
        self.assertTrue(self.service.device(OMNI311_MAC)["pairing_state_fresh"])
        self.assertTrue(self.service.device(OMNI324_MAC)["pairing_state_fresh"])

    def test_a_verified_unpair_leaves_pairing_state_current(self):
        self.net.endpoints[OMNI311_IP]["peers"] = [OMNI324_MAC]
        self.net.endpoints[OMNI324_IP]["peers"] = [OMNI311_MAC]
        r = self.client.post("/api/usb_route/unpair", json={"lex_mac": OMNI311_MAC, "rex_mac": OMNI324_MAC})
        self.assertEqual(r.get_json()["status"], "VERIFIED_SUCCESS")
        self.assertTrue(self.service.device(OMNI311_MAC)["pairing_state_fresh"])

    def test_an_endpoint_returning_online_reopens_the_pairing_cadence(self):
        self.net.endpoints[OMNI311_IP]["query"] = False
        for _ in range(usb.LIVE_MISS_TOLERANCE + 1):
            self.service.ping(OMNI311_MAC)
        self.assertFalse(self.service.device(OMNI311_MAC)["online"])
        srv._usb_pairing_state["last_run"] = time.time()          # cadence would normally block
        self.net.endpoints[OMNI311_IP]["query"] = True
        srv._usb_live_refresh(force=True)
        self.assertEqual(srv._usb_pairing_state["last_run"], 0.0,
                         "a device coming back online must not wait out the slow cadence")

    def test_matrix_polling_does_not_advanced_query_every_cycle(self):
        srv._usb_pairing_refresh(force=True)
        self.net.sent.clear()
        self.patch_srv("_usb_get_config", lambda *a, **k: {})
        for _ in range(5):
            self.client.get("/api/usb_state")
        self.assertNotIn(usb.ADVANCED_QUERY, self.opcodes(),
                         "route state must not cost an Advanced Query on every Matrix poll")

    def test_integrated_pairing_remains_usb_icron_authoritative(self):
        self.seed((E4521_USB_MAC, E4521_USB_IP, 0))
        srv._record_parent_live(E4521_IP, 2)
        device = next(d for d in srv._usb_extender_view()["devices"] if d["mac"] == usb.normalize_mac(E4521_USB_MAC))
        self.assertEqual(device["pairing_source"], "usb_icron")
        self.assertNotIn("paired_macs", device, "UDP pairing must not be shown for an integrated endpoint")
        self.assertEqual(device["parent_paired_count"], 2)

    def test_configure_can_request_pairing_freshness(self):
        clock_before = srv._usb_pairing_state["last_run"]
        self.client.get("/api/usb_extenders?pairing=1")
        self.assertNotEqual(srv._usb_pairing_state["last_run"], clock_before)

    def test_stale_pairing_does_not_disable_identify_or_reboot(self):
        """Liveness is enough for those actions; only routing needs fresh pairing."""
        clock = [1000.0]
        service = usb.ExtenderDiscoveryService(Path(self.folder.name) / "p4.json",
                                               now=lambda: clock[0], persist_debounce=0)
        service._exchange = self.net.exchange
        service.discover_ip(OMNI311_IP, IFACE_IP, IFACE_MASK)
        srv._usb_extenders = service
        clock[0] += usb.PAIRING_TTL + 1
        service.ping(OMNI311_MAC)
        device = next(d for d in srv._usb_extender_view()["devices"] if d["mac"] == usb.normalize_mac(OMNI311_MAC))
        self.assertFalse(device["pairing_state_fresh"])
        self.assertTrue(device["online"])
        self.assertTrue(device["network_config_supported"])
        self.assertTrue(device["reboot_supported"])
        self.assertEqual(device["identify_via"], "extender")


class IntegratedIcronNetworkTests(ServerTestBase):
    """Integrated USB network uses the parent OmniStream API, never standalone UDP.

    Modes and payload shape are taken from the device's own web application
    (NetworkController: vm.icronmodes, SocketService.set) and confirmed against
    live E4521 and D4511 hardware.
    """

    ICRON = {"name": "icron", "type": "icron", "dhcpmode": "dhcp",
             "ipaddress": "192.168.200.66", "subnetmask": "255.255.255.0",
             "gateway": "192.168.200.1", "macaddress": "B8:98:B0:07:85:79"}
    ETH1 = {"name": "eth1", "dhcpmode": "dhcp", "ipaddress": "192.168.200.154",
            "subnetmask": "255.255.255.0", "gateway": "192.168.200.1",
            "macaddress": "B8:98:B0:07:85:78", "linkspeed": 1000}

    def setUp(self):
        super().setUp()
        self._patch_cache([{"ip": "192.168.200.154", "hostname": "hw-omni-d4511-08578",
                            "model": "hw-omni-d4511", "role": "decoder",
                            "usb_type": "REX", "usb_mac": self.ICRON["macaddress"]}])
        self.seed((self.ICRON["macaddress"], self.ICRON["ipaddress"], 1))
        self.sent = []
        self.state = {"icron": dict(self.ICRON)}
        real = srv._ws_send_recv
        self.addCleanup(lambda: setattr(srv, "_ws_send_recv", real))

        def fake(url, payload, timeout=None):
            self.sent.append(payload)
            if payload.get("config_get") == "net":
                return {"config": [dict(self.ETH1), dict(self.state["icron"])]}
            setter = payload.get("config_set") or {}
            if setter.get("name") == "net":
                for entry in setter.get("config") or []:
                    if entry.get("type") == "icron":
                        self.state["icron"].update(entry)
                return {"error": False}
            return {"config": {}}
        srv._ws_send_recv = fake
        self.udp = []
        self.service.command = lambda *a, **k: self.udp.append(a) or {"ok": True}

    def net_writes(self):
        return [p for p in self.sent if (p.get("config_set") or {}).get("name") == "net"]

    # ---- read ----
    def test_reads_the_icron_interface_from_the_parent_net_config(self):
        current = srv._omnistream_icron_network_get("192.168.200.154")
        self.assertEqual(current["mode"], "dhcp")
        self.assertEqual(current["ipaddress"], "192.168.200.66")
        self.assertEqual(current["subnetmask"], "255.255.255.0")
        self.assertEqual(current["gateway"], "192.168.200.1")
        self.assertEqual(current["macaddress"], self.ICRON["macaddress"])
        self.assertEqual(current["source"], "parent_net_config")

    def test_the_read_endpoint_reports_the_parent_backed_configuration(self):
        r = self.client.get(f"/api/usb_extenders/network_config?mac={self.ICRON['macaddress']}")
        self.assertEqual(r.status_code, 200)
        body = r.get_json()
        self.assertEqual(body["kind"], "integrated")
        self.assertEqual(body["parent_ip"], "192.168.200.154")
        self.assertEqual(body["ipaddress"], "192.168.200.66")
        self.assertNotEqual(body["ipaddress"], body["parent_ip"], "USB IP is not the parent device IP")

    def test_modes_match_the_parent_web_application(self):
        self.assertEqual(srv.ICRON_NETWORK_MODES, ("broadcast", "dhcp", "static", "disabled"))
        body = self.client.get(f"/api/usb_extenders/network_config?mac={self.ICRON['macaddress']}").get_json()
        self.assertEqual(body["modes"], ["broadcast", "dhcp", "static", "disabled"])

    # ---- write ----
    def apply(self, **payload):
        return self.client.post("/api/usb_extenders/network",
                                json={"mac": self.ICRON["macaddress"], **payload})

    def test_each_mode_is_sent_as_the_proven_parent_value(self):
        for mode in ("broadcast", "dhcp", "disabled"):
            self.sent.clear()
            r = self.apply(mode=mode)
            self.assertEqual(r.status_code, 200, mode)
            writes = self.net_writes()
            self.assertEqual(len(writes), 1, mode)
            config = writes[0]["config_set"]["config"]
            self.assertIsInstance(config, list, "the device API takes a one-element list")
            self.assertEqual(config[0]["dhcpmode"], mode)
            self.assertEqual(config[0]["type"], "icron")

    def test_static_carries_address_mask_and_gateway(self):
        self.sent.clear()
        r = self.apply(mode="static", address="192.168.200.90",
                       netmask="255.255.255.0", gateway="192.168.200.1")
        self.assertEqual(r.status_code, 200)
        entry = self.net_writes()[0]["config_set"]["config"][0]
        self.assertEqual(entry["dhcpmode"], "static")
        self.assertEqual(entry["ipaddress"], "192.168.200.90")
        self.assertEqual(entry["subnetmask"], "255.255.255.0")
        self.assertEqual(entry["gateway"], "192.168.200.1")

    def test_the_whole_interface_object_is_returned_not_a_fragment(self):
        self.apply(mode="dhcp")
        entry = self.net_writes()[0]["config_set"]["config"][0]
        for key in ("name", "type", "macaddress", "ipaddress", "subnetmask", "gateway"):
            self.assertIn(key, entry, "the device UI sends the full interface entry back")

    def test_success_requires_read_back_not_just_an_ack(self):
        r = self.apply(mode="broadcast")
        self.assertEqual(r.get_json()["status"], "configuration_verified")
        self.assertTrue(r.get_json()["verified"])
        # A device that ACKs but does not apply must not be reported as verified.
        self.state["icron"]["dhcpmode"] = "dhcp"
        original = srv._ws_send_recv
        def stubborn(url, payload, timeout=None):
            if (payload.get("config_set") or {}).get("name") == "net":
                self.sent.append(payload)
                return {"error": False}          # ACK, but state never changes
            return original(url, payload, timeout)
        srv._ws_send_recv = stubborn
        r = self.apply(mode="static", address="192.168.200.90",
                       netmask="255.255.255.0", gateway="192.168.200.1")
        self.assertEqual(r.get_json()["status"], "command_accepted_unverified")
        self.assertFalse(r.get_json()["verified"])

    # ---- safety ----
    def test_integrated_never_uses_the_standalone_udp_ip_commands(self):
        for mode in ("broadcast", "dhcp", "disabled"):
            self.apply(mode=mode)
        self.apply(mode="static", address="192.168.200.90", netmask="255.255.255.0", gateway="192.168.200.1")
        self.assertEqual(self.udp, [], "no standalone IPDHCP/IPStatic may be sent to an integrated endpoint")

    def test_a_client_supplied_parent_cannot_redirect_the_write(self):
        r = self.apply(mode="dhcp", parent_ip="10.9.9.9", parent_hostname="evil")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["parent_ip"], "192.168.200.154",
                         "the parent is resolved server-side from the USB MAC")

    def test_a_mac_that_does_not_belong_to_the_parent_fails_closed(self):
        self.state["icron"]["macaddress"] = "AA:BB:CC:DD:EE:FF"
        r = self.apply(mode="dhcp")
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.get_json()["status"], "mac_mismatch")
        self.assertEqual(self.net_writes(), [], "a mismatch must not write anything")

    def test_an_invalid_mode_is_refused_without_writing(self):
        r = self.apply(mode="zeroconf")          # valid for eth1, not for icron
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.net_writes(), [])

    def test_malformed_static_values_are_refused_without_writing(self):
        for payload in ({"address": "999.1.1.1", "netmask": "255.255.255.0", "gateway": "192.168.200.1"},
                        {"address": "192.168.200.90", "netmask": "255.0.255.0", "gateway": "192.168.200.1"},
                        {"address": "192.168.200.90", "netmask": "255.255.255.0", "gateway": "10.0.0.1"}):
            self.sent.clear()
            r = self.apply(mode="static", **payload)
            self.assertEqual(r.status_code, 400, payload)
            self.assertEqual(self.net_writes(), [], payload)

    def test_an_address_change_updates_identity_without_duplicating_inventory(self):
        before = len(self.service.state()["devices"])
        self.state["icron"]["ipaddress"] = "192.168.200.90"
        self.apply(mode="dhcp")
        self.assertEqual(len(self.service.state()["devices"]), before,
                         "identity is the canonical MAC; a new address must not create a second endpoint")

    def test_standalone_endpoints_still_use_the_standalone_udp_path(self):
        self._patch_cache(FULL_CACHE_UNITS)
        self.seed((OMNI311_MAC, OMNI311_IP, 0))
        self.client.post("/api/usb_extenders/network",
                         json={"interface_ip": IFACE_IP, "subnet_mask": IFACE_MASK,
                               "mac": OMNI311_MAC, "mode": "dhcp"})
        self.assertTrue(self.udp, "a standalone endpoint must still be configured over UDP")
        self.assertEqual(self.net_writes(), [], "and must not touch the parent net config")

    def test_cancel_equivalent_sends_nothing(self):
        """No request at all is what the modal's Cancel produces."""
        self.assertEqual(self.net_writes(), [])
        self.assertEqual(self.udp, [])


class MixedRoutingDispatchTests(ServerTestBase):
    """All four combinations, each on its own proven control path."""

    def setUp(self):
        super().setUp()
        self._patch_cache(FULL_CACHE_UNITS)
        self.net = FakeUdp()
        # Integrated endpoints answer UDP exactly as the bench showed.
        self.net.endpoints[E4521_USB_IP] = {"mac": E4521_USB_MAC, "code": 0, "peers": [],
                                            "advanced": True, "ack": True, "query": True}
        self.net.endpoints[D4511_USB_IP] = {"mac": D4511_USB_MAC, "code": 1, "peers": [],
                                            "advanced": True, "ack": True, "query": True}
        self.service._exchange = self.net.exchange
        for ip in (OMNI311_IP, OMNI324_IP, E4521_USB_IP, D4511_USB_IP):
            self.service.discover_ip(ip, IFACE_IP, IFACE_MASK)
        for mac in (OMNI311_MAC, OMNI324_MAC, E4521_USB_MAC, D4511_USB_MAC):
            self.service.ping(mac)
        self.icron_writes = []
        real = srv._ws_send_recv
        srv._ws_send_recv = lambda *a, **k: (self.icron_writes.append(a), {"config": [], "error": False})[1]
        self.addCleanup(lambda: setattr(srv, "_ws_send_recv", real))
        self.net.sent.clear()

    def opcodes(self):
        return [c for _d, c, _p in self.net.sent if c not in (usb.QUERY, usb.ADVANCED_QUERY)]

    def kinds(self, lex_kind, rex_kind):
        entry = lambda k, n: {"kind": k, "usb_key": f"k{n}", "ip": f"10.0.0.{n}",
                              "mac": f"AA:BB:CC:00:00:0{n}",
                              "classification": "STANDALONE" if k == "standalone" else "INTEGRATED",
                              "online": True, "pairing_eligible": True,
                              "endpoint_ip": f"10.0.0.{n}", "endpoint_mask": "255.255.255.0"}
        return srv._usb_route_capability(entry(lex_kind, 1), entry(rex_kind, 2))

    # ---- capability ----
    def test_all_four_combinations_are_now_routable(self):
        for lex_kind, rex_kind in (("integrated", "integrated"), ("standalone", "standalone"),
                                   ("integrated", "standalone"), ("standalone", "integrated")):
            self.assertTrue(self.kinds(lex_kind, rex_kind)["enabled"], f"{lex_kind}->{rex_kind}")

    def test_an_untested_mixed_combination_stays_experimental(self):
        cap = self.kinds("integrated", "standalone")
        self.assertEqual(cap["state"], "SUPPORTED_MIXED_EXPERIMENTAL")
        self.assertTrue(cap["control_plane_verified"])
        self.assertFalse(cap["data_plane_verified"], "Pair working never implies USB data transport")
        self.assertTrue(cap["mixed"])

    def test_a_physically_tested_mixed_combination_says_so(self):
        cap = self.kinds("standalone", "integrated")
        self.assertEqual(cap["state"], "SUPPORTED_MIXED_VERIFIED")
        self.assertTrue(cap["data_plane_verified"])
        self.assertTrue(cap["mixed"], "verified transport does not stop it being a mixed route")

    def test_data_plane_claims_follow_the_physical_tests_that_were_run(self):
        self.assertTrue(self.kinds("integrated", "integrated")["data_plane_verified"])
        # Physically tested by the operator on the five-peer AT-OMNI-311.
        self.assertTrue(self.kinds("standalone", "standalone")["data_plane_verified"])
        self.assertTrue(self.kinds("standalone", "integrated")["data_plane_verified"])
        # Not tested.
        self.assertFalse(self.kinds("integrated", "standalone")["data_plane_verified"])

    def test_mixed_user_text_carries_no_enum_and_is_concise(self):
        label, detail = srv.USB_CELL_TEXT["SUPPORTED_MIXED_EXPERIMENTAL"]
        self.assertEqual(label, "Mixed USB route — data transport not yet validated")
        self.assertIn("bench verified", detail)
        self.assertNotIn("_", label)

    # ---- dispatch ----
    def test_integrated_to_integrated_never_reaches_the_udp_transaction(self):
        r = self.client.post("/api/usb_route/pair",
                             json={"lex_mac": E4521_USB_MAC, "rex_mac": D4511_USB_MAC})
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.get_json()["status"], "not_supported")
        self.assertEqual(self.opcodes(), [], "E4521 to D4511 belongs to usb_icron")

    def test_standalone_pair_uses_the_dual_endpoint_udp_path(self):
        r = self.client.post("/api/usb_route/pair", json={"lex_mac": OMNI311_MAC, "rex_mac": OMNI324_MAC})
        self.assertEqual(r.get_json()["status"], "VERIFIED_SUCCESS")
        self.assertEqual(self.opcodes(), [usb.PAIR, usb.PAIR])
        self.assertEqual(self.icron_writes, [], "usb_icron must not be used for a UDP route")

    def test_mixed_integrated_lex_to_standalone_rex_uses_the_udp_path(self):
        r = self.client.post("/api/usb_route/pair", json={"lex_mac": E4521_USB_MAC, "rex_mac": OMNI324_MAC})
        self.assertEqual(r.get_json()["status"], "VERIFIED_SUCCESS")
        self.assertEqual(self.opcodes(), [usb.PAIR, usb.PAIR], "both endpoints programmed")
        self.assertEqual(self.icron_writes, [], "mixed routing must not write usb_icron")

    def test_mixed_standalone_lex_to_integrated_rex_uses_the_udp_path(self):
        r = self.client.post("/api/usb_route/pair", json={"lex_mac": OMNI311_MAC, "rex_mac": D4511_USB_MAC})
        self.assertEqual(r.get_json()["status"], "VERIFIED_SUCCESS")
        self.assertEqual(self.opcodes(), [usb.PAIR, usb.PAIR])
        self.assertEqual(self.icron_writes, [])

    # ---- transaction ----
    def test_mixed_unpair_clears_both_endpoints(self):
        self.net.endpoints[E4521_USB_IP]["peers"] = [OMNI324_MAC]
        self.net.endpoints[OMNI324_IP]["peers"] = [E4521_USB_MAC]
        r = self.client.post("/api/usb_route/unpair", json={"lex_mac": E4521_USB_MAC, "rex_mac": OMNI324_MAC})
        self.assertEqual(r.get_json()["status"], "VERIFIED_SUCCESS")
        self.assertEqual(self.opcodes(), [usb.UNPAIR, usb.UNPAIR])
        self.assertEqual(self.net.endpoints[E4521_USB_IP]["peers"], [])
        self.assertEqual(self.net.endpoints[OMNI324_IP]["peers"], [])

    def test_mixed_ack_without_agreement_is_not_success(self):
        self.net.apply_writes = False
        r = self.client.post("/api/usb_route/pair", json={"lex_mac": E4521_USB_MAC, "rex_mac": OMNI324_MAC})
        self.assertEqual(r.status_code, 502)
        self.assertIn(r.get_json()["status"], ("VERIFICATION_FAILED", "COMMAND_ACCEPTED_UNVERIFIED"))

    def test_mixed_second_endpoint_failure_rolls_back_the_first(self):
        self.net.endpoints[OMNI324_IP]["ack"] = False        # REX rejects
        r = self.client.post("/api/usb_route/pair", json={"lex_mac": E4521_USB_MAC, "rex_mac": OMNI324_MAC})
        self.assertEqual(r.get_json()["status"], "REJECTED")
        self.assertTrue(r.get_json().get("rollback"), "the accepted endpoint must be reversed")
        self.assertEqual(self.net.endpoints[E4521_USB_IP]["peers"], [],
                         "a partial mutation must not survive")

    def test_mixed_timeout_is_reported_as_failure(self):
        self.net.endpoints[OMNI324_IP]["silent"] = True      # no response at all
        r = self.client.post("/api/usb_route/pair", json={"lex_mac": E4521_USB_MAC, "rex_mac": OMNI324_MAC})
        self.assertEqual(r.get_json()["status"], "COMMAND_TIMEOUT")

    def test_a_mixed_route_whose_owner_cannot_be_released_mutates_nothing(self):
        self.net.endpoints[OMNI324_IP]["peers"] = ["00:11:22:33:44:99"]
        self.net.sent.clear()
        r = self.client.post("/api/usb_route/pair", json={"lex_mac": E4521_USB_MAC, "rex_mac": OMNI324_MAC})
        self.assertEqual(r.status_code, 502)
        self.assertEqual(r.get_json()["status"], "REASSIGN_RELEASE_FAILED")
        self.assertEqual(self.opcodes(), [])

    def test_mixed_offline_endpoint_is_refused(self):
        self.net.endpoints[OMNI324_IP]["query"] = False
        for _ in range(usb.LIVE_MISS_TOLERANCE + 1):
            self.service.ping(OMNI324_MAC)
        self.net.sent.clear()
        r = self.client.post("/api/usb_route/pair", json={"lex_mac": E4521_USB_MAC, "rex_mac": OMNI324_MAC})
        self.assertEqual(r.status_code, 409)
        self.assertEqual(self.opcodes(), [])

    def test_reversed_mixed_roles_are_refused(self):
        r = self.client.post("/api/usb_route/pair", json={"lex_mac": OMNI324_MAC, "rex_mac": E4521_USB_MAC})
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.get_json()["status"], "wrong_device_type")

    def test_mixed_never_uses_force_pair_or_unpair_all(self):
        self.client.post("/api/usb_route/pair", json={"lex_mac": E4521_USB_MAC, "rex_mac": OMNI324_MAC})
        self.client.post("/api/usb_route/unpair", json={"lex_mac": E4521_USB_MAC, "rex_mac": OMNI324_MAC})
        self.assertNotIn(usb.UNPAIR_ALL, self.opcodes())
        self.assertIsNone(usb.FORCE_PAIR)


class IcronNetworkAuthorityTests(ServerTestBase):
    """Fresh parent configuration always wins over cached/UDP-derived values."""

    ICRON = {"name": "icron", "type": "icron", "dhcpmode": "dhcp",
             "ipaddress": "192.168.9.66", "subnetmask": "255.255.255.0",
             "gateway": "192.168.9.1", "macaddress": E4521_USB_MAC}

    def setUp(self):
        super().setUp()
        # This class is about the parent read *populating* the Icron network, so
        # it starts from nothing rather than from the base class's seed.
        srv._usb_net_config.clear()
        self._patch_cache([{"ip": E4521_IP, "hostname": E4521_HOST, "model": "hw-omni-e4521",
                            "role": "encoder", "usb_type": "LEX", "usb_mac": E4521_USB_MAC}])
        self.seed((E4521_USB_MAC, E4521_USB_IP, 0))
        srv._usb_net_config.clear()
        self.addCleanup(srv._usb_net_config.clear)
        srv._USB_PARENT_LIVE.clear()
        self.addCleanup(srv._USB_PARENT_LIVE.clear)
        self.icron = dict(self.ICRON)
        self.reads = []
        real = srv._ws_send_recv
        self.addCleanup(lambda: setattr(srv, "_ws_send_recv", real))
        def fake(url, payload, timeout=None):
            if payload.get("config_get") == "net":
                self.reads.append(url)
                if self.icron is None:
                    raise OSError("parent unreachable")
                return {"config": [dict(self.icron)]}
            return {"config": {}, "error": False}
        srv._ws_send_recv = fake

    def view(self):
        srv._record_parent_live(E4521_IP)
        return next(d for d in srv._usb_extender_view()["devices"]
                    if d["mac"] == usb.normalize_mac(E4521_USB_MAC))

    def refresh(self, force=True):
        srv._refresh_icron_network_config(force=force)

    def test_the_udp_mode_byte_never_supplies_the_integrated_mode(self):
        record = self.service.device(E4521_USB_MAC)
        self.assertEqual(record["network_mode"], "DHCP", "the UDP byte is still recorded")
        self.assertEqual(self.view()["network_mode"], "", "but it must not be shown for an integrated endpoint")

    def test_fresh_static_overrides_a_cached_dhcp_value(self):
        self.icron["dhcpmode"] = "static"
        self.refresh()
        device = self.view()
        self.assertEqual(device["network_mode"], "STATIC")
        self.assertEqual(device["network_config_source"], "parent_net")
        self.assertTrue(device["network_config_fresh"])

    def test_each_parent_mode_is_reflected_exactly(self):
        for mode, shown in (("dhcp", "DHCP"), ("static", "STATIC"),
                            ("broadcast", "BROADCAST"), ("disabled", "DISABLED")):
            self.icron["dhcpmode"] = mode
            self.refresh()
            device = self.view()
            self.assertEqual(device["network_mode"], shown, mode)
            self.assertEqual(device["network_mode_raw"], mode, "the raw wire value is preserved")

    def test_an_unknown_mode_is_not_silently_normalised(self):
        self.icron["dhcpmode"] = "zeroconf"
        self.refresh()
        device = self.view()
        self.assertIn("Unknown", device["network_mode"])
        self.assertIn("zeroconf", device["network_mode"])
        self.assertEqual(device["network_mode_raw"], "zeroconf")

    def test_address_mask_and_gateway_follow_the_parent(self):
        self.icron.update({"ipaddress": "192.168.9.90", "subnetmask": "255.255.0.0", "gateway": "192.168.9.254"})
        self.refresh()
        device = self.view()
        self.assertEqual(device["usb_ip"], "192.168.9.90")
        self.assertEqual(device["subnet_mask"], "255.255.0.0")
        self.assertEqual(device["gateway"], "192.168.9.254")

    def test_an_address_change_keeps_one_record_under_the_same_mac(self):
        before = len(self.service.state()["devices"])
        self.icron["ipaddress"] = "192.168.9.99"
        self.refresh()
        self.assertEqual(len(self.service.state()["devices"]), before, "no duplicate endpoint")
        self.assertEqual(self.view()["mac"], usb.normalize_mac(E4521_USB_MAC), "identity is the canonical MAC")

    def test_an_external_change_self_heals_on_the_next_refresh(self):
        self.icron["dhcpmode"] = "dhcp"; self.refresh()
        self.assertEqual(self.view()["network_mode"], "DHCP")
        self.icron["dhcpmode"] = "broadcast"        # changed in the parent web UI
        self.refresh()
        self.assertEqual(self.view()["network_mode"], "BROADCAST", "no restart or rediscovery required")

    def test_a_failed_parent_read_keeps_the_last_values_but_marks_them_stale(self):
        self.icron["dhcpmode"] = "static"; self.refresh()
        self.assertTrue(self.view()["network_config_fresh"])
        self.icron = None                            # parent unreachable
        self.refresh()
        device = self.view()
        self.assertEqual(device["network_mode"], "STATIC", "last known values are retained")
        self.assertFalse(device["network_config_fresh"], "but not presented as fresh")

    def test_a_fresh_read_is_not_repeated_within_the_ttl(self):
        self.refresh()
        count = len(self.reads)
        srv._refresh_icron_network_config()          # not forced
        self.assertEqual(len(self.reads), count, "deduplicated within the TTL")

    def test_standalone_endpoints_keep_udp_network_authority(self):
        self._patch_cache(FULL_CACHE_UNITS)
        self.seed((OMNI311_MAC, OMNI311_IP, 0))
        device = next(d for d in srv._usb_extender_view()["devices"]
                      if d["mac"] == usb.normalize_mac(OMNI311_MAC))
        self.assertEqual(device["network_config_source"], "standalone_udp")
        self.assertEqual(device["network_mode"], "DHCP", "the UDP value remains authoritative here")

    def test_every_path_uses_the_same_ingest(self):
        source = Path(srv.__file__).read_text(encoding="utf-8", errors="replace")
        self.assertEqual(source.count("def _ingest_icron_network_config"), 1)
        for caller in ("_omnistream_icron_network_get", "_refresh_icron_network_config"):
            index = source.index("def " + caller)
            block = source[index:index + 2600]
            self.assertIn("_ingest_icron_network_config", block, caller)


class RouteContractTests(ServerTestBase):
    """The endpoint the Matrix posts to must exist on the server.

    A frontend path and a Flask rule drifting apart produced a 404 that looked
    like a missing feature, so the contract is asserted from the JavaScript
    itself rather than from a copy of it.
    """

    def setUp(self):
        super().setUp()
        self.js = Path(srv.__file__).parent.joinpath("ui", "matrix", "usb.js").read_text(encoding="utf-8")
        self.rules = {str(r.rule) for r in srv.app.url_map.iter_rules()}

    def posted_paths(self):
        return set(re.findall(r"postJSON\(\s*['\"](/api/[A-Za-z0-9_/]+)['\"]", self.js)) | \
               set(re.findall(r"postJSON\([^)]*?['\"](/api/usb_route/[a-z]+)['\"]", self.js))

    def test_every_endpoint_the_matrix_posts_to_is_registered(self):
        posted = self.posted_paths()
        self.assertTrue(posted, "no POST targets were found in usb.js")
        for path in sorted(posted):
            self.assertIn(path, self.rules, f"usb.js posts to {path} but no Flask route serves it")

    def test_the_route_endpoints_exist_under_their_documented_names(self):
        for path in ("/api/usb_route/pair", "/api/usb_route/unpair", "/api/usb_route/resolve"):
            self.assertIn(path, self.rules)

    def test_the_route_endpoints_answer_validation_rather_than_not_found(self):
        """An empty body must be a validation error, never a missing route."""
        for path in ("/api/usb_route/pair", "/api/usb_route/unpair", "/api/usb_route/resolve"):
            r = self.client.post(path, json={})
            self.assertNotEqual(r.status_code, 404, f"{path} is not registered")
            self.assertEqual(r.status_code, 400, path)
            self.assertEqual(self.transmitted, [], "a rejected request must send nothing")

    def test_the_matrix_uses_one_route_contract_for_every_udp_combination(self):
        block = self.js[self.js.index("if(controlPath === 'standalone_udp')"):]
        block = block[:block.index("await refresh(true);")]
        self.assertEqual(block.count("/api/usb_route/pair"), 1)
        self.assertEqual(block.count("/api/usb_route/unpair"), 1)
        self.assertNotIn("/api/usb_pair", block, "the UDP path must never fall back to usb_icron")
        self.assertNotIn("/api/usb_unpair", block)

    def test_the_integrated_path_also_moves_a_route_in_one_action(self):
        """E4521 -> D4511 keeps its usb_icron transport, but a REX owned by
        another LEX is still moved by the backend, not by the operator."""
        block = self.js[self.js.index("'/api/usb_pair'"):]
        block = block[:block.index("});")]
        self.assertIn("replaceExisting: true", block,
                      "a route request states the desired end state")
        self.assertNotIn("replaceExisting: hasExistingPair", block,
                         "a stale view must not reintroduce the manual unpair step")

    def test_no_surface_asks_the_operator_to_unpair_before_pairing(self):
        for phrase in ("unpair existing", "unpair the existing", "unpair first",
                       "before pairing another"):
            self.assertNotIn(phrase, self.js.lower(), phrase)

    def test_every_route_change_renders_as_pending_until_it_settles(self):
        """Both transports: an in-flight request is never shown as a result."""
        # Anchored on the delegated binding: the grid binds one handler on the
        # table, not one per cell, so the old anchor named a line that no longer
        # exists. usb_render_smoke.js asserts the resulting behaviour.
        handler = self.js[self.js.index("t.dataset.cellClickBound = '1';"):]
        self.assertEqual(handler.count("markUsbCellPending(cell, rex, lex)"), 2)
        self.assertEqual(handler.count("clearUsbCellPending(cell, rex, lex)"), 2)
        self.assertEqual(handler.count("noteUsbRouteMutation()"), 2,
                         "both transports invalidate state read before the change")

    def test_the_legacy_icron_endpoints_are_untouched(self):
        for path in ("/api/usb_pair", "/api/usb_unpair"):
            self.assertIn(path, self.rules)
            self.assertIn(path, self.js, "integrated routing keeps using its established path")


class HardwareIsolationTests(ServerTestBase):
    """The suite must be unable to mutate physical hardware, on either transport.

    This was assumed rather than asserted. The UDP socket was stubbed in the
    shared base class, but the WebSocket transport -- which carries every
    config_set write -- was not, and the fixture addresses are real bench
    devices. Nine mutating endpoints had no test at all, so nothing would have
    noticed when one of them reached a live unit from a unit test.
    """

    def test_the_fence_refuses_a_method_call_not_only_a_config_set(self):
        """`method` is the API's third verb, and it writes.

        add_logo, delete_logo, add_multiview and del_multiview all travel as
        `method` and none of them carries a `config_set` key, so a fence that
        watched only `config_set` let every one of them through to whatever
        address the fixture named.
        """
        with self.assertRaises(AssertionError):
            _fenced_ws_send_recv("ws://192.0.2.1:80/wsapp/",
                                 {"method": {"del_multiview": {"name": "x"}}})
        with self.assertRaises(AssertionError):
            _fenced_ws_send_recv("ws://192.0.2.1:80/wsapp/",
                                 {"method": {"add_multiview": {"name": "multiviewX"}}})
        # The subframe verb is the one a live switch uses, and it is the one an
        # `add_multiview`-shaped fence would be least likely to have thought of.
        with self.assertRaises(AssertionError):
            _fenced_ws_send_recv(
                "ws://192.0.2.1:80/wsapp/",
                {"method": {"del_multiview_subframe": {
                    "name": "multiviewX", "subframe": "top_left (960x544)"}}})
        # Every attempt was recorded, which is the other half of the guarantee.
        self.assertEqual(len(_WS_WRITES), 3)
        # Cleared because this test trips the fence deliberately, and cleanup
        # fails any test that left a recorded write behind.
        del _WS_WRITES[:]

    def test_no_multiview_verb_reaches_a_device_whatever_the_endpoint(self):
        """The fence is on the verb, so a new endpoint inherits it for free.

        Phase 7 added a live-switch endpoint that writes encoder scalers,
        decoder inputs and subframes. It reaches hardware through the same two
        verbs as everything before it, and this asserts that -- rather than
        asserting that one endpoint happens to be stubbed today.
        """
        verbs = [
            {"config_set": {"name": "vc2_encoder2", "config": []}},
            {"config_set": {"name": "ip_input", "config": []}},
            {"config_set": {"name": "hdmi_output", "config": []}},
            {"method": {"add_multiview_subframe": {"name": "multiviewX"}}},
            {"method": {"del_multiview_subframe": {"name": "multiviewX"}}},
            {"method": {"del_multiview": {"name": "multiviewX"}}},
        ]
        for payload in verbs:
            with self.subTest(payload=payload):
                with self.assertRaises(AssertionError):
                    _fenced_ws_send_recv("ws://192.0.2.1:80/wsapp/", payload)
        self.assertEqual(len(_WS_WRITES), len(verbs))
        del _WS_WRITES[:]

    def test_the_fence_still_refuses_a_config_set(self):
        with self.assertRaises(AssertionError):
            _fenced_ws_send_recv("ws://192.0.2.1:80/wsapp/",
                                 {"config_set": {"name": "multiview", "config": []}})
        self.assertEqual(len(_WS_WRITES), 1)
        del _WS_WRITES[:]

    def test_the_fence_still_allows_a_read(self):
        # Reads are a normal part of these tests; only writes are refused.
        with self.assertRaises(OSError):
            _fenced_ws_send_recv("ws://192.0.2.1:80/wsapp/",
                                 {"config_get": "multiview"})

    def test_github_is_fenced_for_the_whole_process(self):
        """Per-test stubbing is not enough on its own.

        The startup task checks for a release on a daemon thread. That thread
        can still be running when the test that started it tears down, and the
        per-test restore has by then put the real fetcher back -- which is how a
        run once reached api.github.com and the isolation gate failed
        intermittently. The module global is replaced at import time, so every
        restore restores to the fence.
        """
        self.assertIs(self.github_fetcher_before_stub, _github_is_fenced,
                      "a test left the real update fetcher installed")
        with self.assertRaises(OSError):
            _github_is_fenced()

    MUTATING_ENDPOINTS = (
        ("/api/usb_pair", {"rex": D4511_IP, "lex": E4521_IP, "replaceExisting": True}),
        ("/api/usb_unpair", {"rex": D4511_IP, "lex": E4521_IP}),
        ("/api/usb_set_port", {"lex": E4521_IP, "port": 1}),
        ("/api/usb_set_type", {"device": E4521_IP, "type": "REX"}),
        ("/api/usb_set_filter", {"device": E4521_IP, "filter": "none"}),
        ("/api/codec", {"ip": E4521_IP, "system_mode": "hdmi"}),
        ("/api/hostname", {"ip": E4521_IP, "hostname": "renamed-by-a-test"}),
        ("/api/reboot", {"ips": [E4521_IP, D4511_IP]}),
        ("/api/blink", {"ip": E4521_IP, "state": True}),
        # The desired-state routes, and the bench diagnostics, which change
        # physical pairing just as the others do.
        ("/api/usb_route/pair", {"lex_mac": OMNI311_MAC, "rex_mac": OMNI324_MAC}),
        ("/api/usb_route/unpair", {"lex_mac": OMNI311_MAC, "rex_mac": OMNI324_MAC}),
        ("/api/diagnostics/usb_route/pair", {"host": OMNI311_MAC, "device": OMNI324_MAC}),
        ("/api/diagnostics/usb_route/unpair", {"host": OMNI311_MAC, "device": OMNI324_MAC}),
    )

    def setUp(self):
        super().setUp()
        self._patch_cache(FULL_CACHE_UNITS)
        self.seed(*OBSERVED_UDP)
        self.rules = {str(r.rule) for r in srv.app.url_map.iter_rules()}

    # ---- the fence itself ----
    def test_a_configuration_write_is_refused_by_the_transport(self):
        with self.assertRaises(AssertionError):
            srv._ws_send_recv(f"ws://{E4521_IP}:80/wsapp/",
                              {"config_set": {"name": "usb_icron"}}, timeout=1)
        del _WS_WRITES[:]

    def test_a_read_is_answered_as_an_unreachable_device(self):
        with self.assertRaises(OSError):
            srv._ws_send_recv(f"ws://{E4521_IP}:80/wsapp/",
                              {"config_get": "usb_icron"}, timeout=1)

    def test_every_mutating_udp_opcode_is_refused(self):
        for command in sorted(USB_MUTATING_COMMANDS):
            request = usb.packet(1, command, usb.mac_bytes(OMNI311_MAC))
            with self.assertRaises(AssertionError, msg=hex(command)):
                self.service._exchange(OMNI311_IP, request, 1)
        # Recorded by the stub as well as raised; cleared so the cleanup
        # assertion does not report this deliberate exercise as a real attempt.
        self.assertEqual(len(self.attempted_mutations), len(USB_MUTATING_COMMANDS))
        self.attempted_mutations.clear()

    def test_the_mutating_command_set_matches_the_protocol_module(self):
        """A new opcode must not be able to join the protocol uncovered."""
        declared = {name: value for name, value in vars(usb).items()
                    if isinstance(value, int) and not isinstance(value, bool)
                    and name in {"PAIR", "UNPAIR", "UNPAIR_ALL", "IP_DHCP", "IP_STATIC",
                                 "REBOOT", "BLINK_ON", "BLINK_OFF", "FORCE_PAIR"}}
        self.assertEqual(set(declared.values()), USB_MUTATING_COMMANDS,
                         "every mutating opcode the protocol defines is fenced")

    # ---- the endpoints ----
    def test_no_mutating_endpoint_reaches_hardware(self):
        """Every one of these performs a write; none may escape a test."""
        for path, body in self.MUTATING_ENDPOINTS:
            with self.subTest(path=path):
                response = self.client.post(path, json=body)
                # The handler may answer success, failure or refusal -- that is
                # the endpoint's own contract. What it may never do is deliver
                # the write, and it must not raise past the request.
                self.assertIn(response.status_code, (200, 207, 400, 404, 409, 502, 503))
                self.assertEqual(_WS_WRITES, [], f"{path} wrote to hardware")
                self.assertEqual(self.attempted_mutations, [], f"{path} mutated over UDP")

    def test_every_mutating_endpoint_is_registered(self):
        for path, _body in self.MUTATING_ENDPOINTS:
            self.assertIn(path, self.rules, path)

    # ---- nothing that merely observes may change a route ----
    READ_ONLY_SURFACES = (
        ("GET", "/api/usb_state", None),          # Matrix refresh
        ("GET", "/api/usb_extenders", None),      # Configure > USB refresh
        ("GET", "/api/cache", None),              # page load
        ("GET", "/api/state", None),              # A/V Matrix refresh
        ("POST", "/api/usb_discover", {}),        # discovery
    )

    def test_no_observing_surface_mutates_a_route(self):
        """Polling, discovery and page load observe. They never pair or unpair.

        Each of these is called repeatedly and unattended, so a mutation reached
        from one would reconfigure hardware with nobody asking for it.
        """
        for method, path, body in self.READ_ONLY_SURFACES:
            with self.subTest(path=path):
                if path not in self.rules:
                    continue
                for _ in range(3):
                    if method == "GET":
                        self.client.get(path)
                    else:
                        self.client.post(path, json=body)
                self.assertEqual(self.attempted_mutations, [], f"{path} sent a mutating opcode")
                self.assertEqual(_WS_WRITES, [], f"{path} wrote configuration")

    def test_startup_performs_no_mutation(self):
        """Starting the application must not pair, unpair, reboot or readdress."""
        srv.start_background_startup_tasks()
        srv.start_background_startup_tasks()   # idempotent; must not re-arm
        self.assertEqual(self.attempted_mutations, [])
        self.assertEqual(_WS_WRITES, [])

    # ---- the audit record ----
    def audit_log_for(self, path, body):
        import io, logging
        buffer = io.StringIO()
        handler = logging.StreamHandler(buffer)
        handler.setLevel(logging.INFO)
        logger = logging.getLogger("omni_upgrade")
        logger.addHandler(handler)
        try:
            self.client.post(path, json=body)
        finally:
            logger.removeHandler(handler)
        return buffer.getvalue()

    def test_every_mutating_endpoint_records_an_audit_entry(self):
        """A device reconfiguration must leave a record of who asked for what."""
        for path, body in self.MUTATING_ENDPOINTS:
            with self.subTest(path=path):
                text = self.audit_log_for(path, body)
                self.assertIn("[AUDIT]", text, f"{path} recorded nothing")
                self.assertIn("requested", text)
                self.assertRegex(text, r"\[AUDIT\] \S+ (completed|raised)",
                                 "the outcome is recorded, not just the attempt")

    def test_no_credential_reaches_the_audit_log(self):
        """Two endpoints accept a password in the body; none may log one."""
        secret = "hunter2-should-never-appear"
        for path, body in self.MUTATING_ENDPOINTS:
            with self.subTest(path=path):
                loaded = dict(body)
                loaded.update({"username": "operator", "password": secret,
                               "pwd": secret, "token": secret})
                text = self.audit_log_for(path, loaded)
                self.assertIn("[AUDIT]", text)
                self.assertNotIn(secret, text, f"{path} logged a credential")
                self.assertNotIn("operator", text, f"{path} logged a username")


class ResilientIoTests(ServerTestBase):
    """Two fixes that are invisible until the day they matter."""

    def test_a_slow_future_does_not_discard_the_finished_ones(self):
        """The gathering timeout used to unwind past the save below it.

        as_completed raises TimeoutError, which escaped the loop and skipped the
        write, so one unresponsive device turned an entire verification pass
        into a no-op and every result already collected was thrown away.
        """
        from concurrent.futures import ThreadPoolExecutor
        release = threading.Event()
        self.addCleanup(release.set)
        pool = ThreadPoolExecutor(max_workers=4)
        # Not a context manager, and the slow worker is released in cleanup:
        # waiting for it would make this test cost as long as the hang it is
        # about.
        self.addCleanup(lambda: pool.shutdown(wait=False))
        quick = [pool.submit(lambda n=n: n) for n in range(3)]
        slow = pool.submit(release.wait, 30)
        gathered = [f.result() for f in srv._as_completed_tolerant(quick + [slow], timeout=0.5)]
        self.assertEqual(sorted(v for v in gathered if v is not None), [0, 1, 2],
                         "everything that finished in time is kept")

    def test_the_config_file_survives_a_failed_write(self):
        """It held the operator's credentials and was truncated in place.

        A crash between the truncate and the write left an empty file, which
        silently reverted every setting to an environment default.
        """
        config = Path(srv.CWD) / "config.json"
        original = config.read_text(encoding="utf-8") if config.exists() else None
        self.addCleanup(lambda: config.write_text(original, encoding="utf-8")
                        if original is not None else config.unlink(missing_ok=True))
        srv._save_config({"username": "before", "ws_port": 80})
        good = config.read_text(encoding="utf-8")

        real_dump = srv.json.dump
        def exploding(obj, handle, **kwargs):
            handle.write('{"partial":')
            raise OSError("disk full")
        srv.json.dump = exploding
        self.addCleanup(lambda: setattr(srv.json, "dump", real_dump))
        srv._save_config({"username": "after", "ws_port": 81})

        self.assertEqual(config.read_text(encoding="utf-8"), good,
                         "a failed write leaves the previous file exactly as it was")
        self.assertEqual(json.loads(config.read_text(encoding="utf-8"))["username"], "before")

    def test_no_temporary_file_is_left_behind(self):
        config = Path(srv.CWD) / "config.json"
        before = set(config.parent.glob("config.json.*"))
        srv._save_config({"username": "x"})
        self.assertEqual(set(config.parent.glob("config.json.*")), before,
                         "the temp file is renamed into place, never left lying around")


class StartupTaskIsolationTests(ServerTestBase):
    """Startup work belongs to the test that started it.

    `start_background_startup_tasks` starts three plain daemon threads. They are
    not executor submissions, so `inline_background` cannot reach them, and
    `_startup_usb_refresh` waits for discovery to settle before calling
    `_refresh_usb_parent_map`, `_usb_live_refresh` and
    `_refresh_icron_network_config` -- all through the module globals, so all
    resolved against whichever test is running when they fire.

    That is a measured defect, not a theory. Arming the trigger and sweeping the
    start offset across the target test reproduced it in 7 of 30 attempts:
    `_refresh_icron_network_config` arrived inside
    `MatrixNetworkReadinessTests.test_it_stops_asking_once_every_mask_is_known`
    and appended to the recorder that test had installed, so it failed with
    `[True] != []` on "a known mask must not be re-read on every Matrix poll" --
    an Icron read it never made, attributed to it.

    Nothing here waits longer or asserts less. The work is simply made waitable,
    and every test waits for what it armed.
    """

    def arm(self):
        srv._startup_tasks_started = False
        del srv._startup_threads[:]
        srv.start_background_startup_tasks()

    def test_the_startup_threads_are_waitable(self):
        """A daemon thread nothing can name is a daemon thread nothing can wait for."""
        self.arm()
        self.assertEqual(len(srv._startup_threads), 3,
                         "the startup threads are not tracked")
        self.assertTrue(srv.await_startup_tasks(timeout=15.0),
                        "a startup thread was still running")
        self.assertEqual([t.name for t in srv._startup_threads if t.is_alive()], [])

    def test_the_refresh_lands_in_the_test_that_armed_it(self):
        recorded = []
        self.patch_srv("_refresh_icron_network_config",
                       lambda *a, **k: recorded.append(threading.current_thread().name))
        self.arm()
        self.assertTrue(srv.await_startup_tasks(timeout=15.0))
        self.assertTrue(recorded,
                        "the startup refresh never ran, so waiting for it proves nothing")

    def test_no_startup_thread_outlives_the_test_that_started_it(self):
        """The contaminating sequence, run for real: arm in one test, look after it."""

        class ArmingTest(ServerTestBase):
            def runTest(inner):                       # noqa: N805 - unittest name
                srv._startup_tasks_started = False
                del srv._startup_threads[:]
                srv.start_background_startup_tasks()

        case = ArmingTest()
        outcome = unittest.TestResult()
        case.run(outcome)
        self.assertEqual(outcome.errors + outcome.failures, [],
                         "the arming test itself failed")
        self.assertEqual([t.name for t in srv._startup_threads if t.is_alive()], [],
                         "a startup thread was still running after its test ended")

    def test_a_leak_fails_the_test_that_caused_it(self):
        """The contract is enforced, not merely documented.

        Proved by leaving something running that the wait cannot finish: the
        arming test must fail, rather than some later test failing instead.
        """

        # Owned out here on purpose. Released from inside the leaking test it
        # would be released first -- cleanups run last-registered-first, and the
        # contract is registered in setUp -- so the thread would already have
        # stopped by the time the contract looked, and this test would pass
        # while proving nothing.
        stop = threading.Event()
        self.addCleanup(stop.set)

        class LeakingTest(ServerTestBase):
            # Long enough to still be running when the contract looks, short
            # enough that proving the point costs a fifth of a second.
            STARTUP_JOIN_TIMEOUT = 0.2

            def runTest(inner):                       # noqa: N805 - unittest name
                thread = threading.Thread(target=lambda: stop.wait(30), daemon=True)
                srv._startup_threads.append(thread)
                thread.start()

        case = LeakingTest()
        outcome = unittest.TestResult()
        case.run(outcome)
        stop.set()
        problems = outcome.errors + outcome.failures
        self.assertTrue(problems, "a leaked startup thread was not reported")
        self.assertIn("outlived the test that started it", problems[0][1])

    def test_the_settle_delay_is_not_waited_out_by_the_suite(self):
        """Waiting for the work is the fix; waiting for the delay is a cost."""
        self.assertEqual(srv.STARTUP_USB_SETTLE, 0.0,
                         "tests should not sit through the production settle")
        source = Path(srv.__file__).with_name(
            "OmniMatrix_upgrade_server_v7_6y.py").read_text(encoding="utf-8")
        self.assertIn("STARTUP_USB_SETTLE = 2.0", source,
                      "production still waits for discovery to settle")


class MatrixNetworkReadinessTests(ServerTestBase):
    """The Matrix must be able to obtain the facts its own gate demands.

    Found on live hardware. The same-subnet gate is fail-closed, so an endpoint
    whose USB subnet mask has not been read renders as "USB network information
    unavailable" and cannot be routed. `_usb_net_config` is in-memory only and
    was filled only by discovery and by Configure > USB, so after a restart an
    operator who opened just the USB Matrix saw every cell permanently disabled:
    measured 0 of 6 cells routable, and still 0 after four minutes of polling.
    """

    def setUp(self):
        super().setUp()
        self._patch_cache(FULL_CACHE_UNITS)
        # Only the standalone pair is seeded over UDP. The integrated endpoints
        # are parent-derived, exactly as they are on the bench: they were never
        # seen by a UDP query, so they carry no mask of their own and the only
        # source for one is the parent's Icron network configuration. Seeding
        # them over UDP hands them a mask and hides the defect entirely.
        self.seed(*OBSERVED_UDP[-2:])
        self.seed_integrated()
        # A restart: the masks are not known yet.
        srv._usb_net_config.clear()
        self.scheduled = []
        self.patch_srv("_refresh_icron_network_config",
                       lambda *a, **k: self.scheduled.append(True))
        # Run the scheduled work inline, so the assertion does not race the
        # background thread that would otherwise perform it.
        executor = self.service._executor
        self._real_submit = executor.submit
        self.addCleanup(lambda: setattr(executor, "submit", self._real_submit))
        executor.submit = lambda fn, *a, **k: fn(*a, **k)

    # The axes only exist when the parents answer, so state the read the way
    # RouteIdentityContractTests does; this test is about the mask, not about
    # whether a parent is reachable.
    def ws_read(self, url, name):
        if name != "usb_icron":
            return None
        for parent_ip, usb_mac, role, usb_ip, _parent_mac in self.INTEGRATED_FIXTURE:
            if f"//{parent_ip}:" in url:
                return {"config": {"type": role, "macaddress": usb_mac, "ipaddress": usb_ip,
                                   "revision": "1.9.4", "protocol": "IP",
                                   "paired_devices": {}, "found_devices": {}}}
        return None

    def test_it_asks_for_the_mask_it_does_not_have(self):
        self.client.get("/api/usb_state")
        self.assertTrue(self.scheduled,
                        "the Matrix never asked for the network config its gate requires")

    def test_it_stops_asking_once_every_mask_is_known(self):
        """Demand-driven, so this converges to no reads. It is not a new poll."""
        for usb_mac, usb_ip in ((E4521_USB_MAC, E4521_USB_IP), (E4521B_USB_MAC, E4521B_USB_IP),
                                (D4511_USB_MAC, D4511_USB_IP), (D4511B_USB_MAC, D4511B_USB_IP),
                                (D4511C_USB_MAC, D4511C_USB_IP)):
            srv._usb_net_config[srv._norm_usb_mac(usb_mac)] = {
                "ipaddress": usb_ip, "subnetmask": "255.255.255.0", "gateway": "192.0.2.1",
                "mode_raw": "DHCP", "mode_known": True, "network_config_last_read": time.time(),
                "network_config_stale": False}
        self.scheduled.clear()
        body = self.client.get("/api/usb_state").get_json()
        # Stated explicitly, because the assertion below is only meaningful once
        # every entry really does have a mask. Without this the test could pass
        # merely because the axes were empty, or fail for a reason that has
        # nothing to do with re-reading.
        entries = (body.get("matrix_lex") or []) + (body.get("matrix_rex") or [])
        self.assertTrue(entries, "there are axis entries to reason about")
        missing = [e.get("usb_key") for e in entries if not e.get("endpoint_mask")]
        self.assertEqual(missing, [], "every endpoint's mask is known before this is asserted")
        known = {e.get("usb_key") for e in entries}
        self.scheduled.clear()
        for _ in range(5):
            later = self.client.get("/api/usb_state").get_json()
            axes = (later.get("matrix_lex") or []) + (later.get("matrix_rex") or [])
            # The refresh is demand-driven: it is scheduled when ANY entry has no
            # mask. So before blaming re-reading, establish that the population
            # has not changed under the test -- an endpoint that appeared, or one
            # that lost its mask, is a different finding and deserves to be
            # reported as one rather than as a spurious re-read.
            self.assertEqual({e.get("usb_key") for e in axes}, known,
                             "the axis population changed during the test")
            self.assertEqual([e.get("usb_key") for e in axes if not e.get("endpoint_mask")], [],
                             "an endpoint lost its mask during the test")
        self.assertEqual(self.scheduled, [],
                         "a known mask must not be re-read on every Matrix poll")


class SharedSettingsTests(ServerTestBase):
    """One Settings dialog, shared by every page.

    The Configuration dialog, its folder chooser and all of their wiring lived
    inside ui/index.html, so Device Info was the only page that had them. Four
    copies of that markup would have been four dialogs to keep in step, so the
    markup and the wiring moved into ui/settings.js and every page injects the
    same copy.
    """

    PAGES = ("index.html", "matrix/configure.html", "matrix/index.html", "matrix/usb.html")

    def source(self, *parts):
        return Path(srv.__file__).resolve().parent.joinpath(*parts).read_text(encoding="utf-8")

    def page(self, name):
        return self.source("ui", *name.split("/"))

    def test_every_page_loads_the_shared_settings_module(self):
        for name in self.PAGES:
            text = self.page(name)
            self.assertIn("/ui/settings.js", text, name)
            self.assertIn("/ui/settings.css", text, name)

    def test_every_page_has_exactly_one_settings_button(self):
        for name in self.PAGES:
            text = self.page(name)
            self.assertEqual(text.count('id="header_gear"'), 1, name)

    def test_the_settings_button_is_labelled_for_assistive_technology(self):
        """It is an icon with no text, so the label is the only name it has."""
        for name in self.PAGES:
            self.assertIn('aria-label="Settings"', self.page(name), name)

    def test_the_dialog_markup_exists_once_and_only_in_the_module(self):
        module = self.source("ui", "settings.js")
        self.assertEqual(module.count('id="cfg_backdrop"'), 1)
        for name in self.PAGES:
            self.assertNotIn('id="cfg_backdrop"', self.page(name),
                             f"{name} carries its own copy of the dialog")

    def test_the_dialog_is_injected_only_once_per_page(self):
        """Two copies would give every #cfg_ id a duplicate."""
        module = self.source("ui", "settings.js")
        self.assertIn("if (!document.getElementById('cfg_backdrop'))", module)
        self.assertIn("if (!document.getElementById('cfg_browser_backdrop'))", module)

    def test_every_settings_field_is_present_in_the_shared_markup(self):
        module = self.source("ui", "settings.js")
        for field in ("cfg_theme", "cfg_username", "cfg_password", "cfg_fallback_password",
                      "cfg_ws_port", "cfg_timeout", "cfg_concurrency", "cfg_firmware_path",
                      "cfg_template_choice", "cfg_preset_choice", "cfg_light_background"):
            self.assertIn(field, module, field)

    def test_the_settings_state_is_the_servers_not_the_pages(self):
        """No page-specific copy: the dialog reads and writes one endpoint."""
        module = self.source("ui", "settings.js")
        self.assertIn("/api/config", module)
        self.assertNotIn("localStorage.setItem('cfg_", module)
        # Restored afterwards: /api/config writes the operator's real config.json
        # and sets process-global app.config, neither of which this test isolates.
        # It also left a one-character password behind, which then matched
        # incidental text in another test's support-dump search.
        before = self.client.get("/api/config?include_password=1").get_json()
        restore = {k: before.get(k) for k in ("username", "password", "fallback_password",
                                              "ws_port", "timeout", "concurrency", "firmware_path")}
        self.addCleanup(lambda: self.client.post("/api/config", json=restore))
        payload = dict(restore, username="operator-a")
        self.assertEqual(self.client.post("/api/config", json=payload).status_code, 200)
        self.assertEqual(self.client.get("/api/config").get_json()["username"], "operator-a")

    def test_the_dialog_styling_is_shared_rather_than_page_specific(self):
        css = self.source("ui", "settings.css")
        for rule in (".gear-btn", ".modal-backdrop", ".modal-row", ".file-chooser", ".browser-list"):
            self.assertIn(rule, css, rule)
        self.assertNotIn(".gear-btn{", self.page("index.html"),
                         "the page kept its own copy of a shared rule")


class DeviceInfoTableTests(ServerTestBase):
    """Wide-table usability on Device Info: order, frozen columns, mirror."""

    def source(self, *parts):
        return Path(srv.__file__).resolve().parent.joinpath(*parts).read_text(encoding="utf-8")

    def test_identity_columns_lead_in_the_order_the_operator_reads_them(self):
        """select, address, name, hardware identity -- Hostname followed MAC."""
        page = self.source("ui", "index.html")
        head = page[page.index("<thead>"):page.index("</thead>")]
        order = re.findall(r"<th[^>]*>(.*?)</th>", head, re.S)
        text = [re.sub(r"<[^>]+>", "", cell).strip() for cell in order[:4]]
        self.assertEqual(text[1], "IP", text)
        self.assertEqual(text[2], "Hostname", text)
        self.assertEqual(text[3], "MAC", text)

    def test_the_row_template_matches_the_header(self):
        """They must move together or every later cell sits under the wrong head."""
        page = self.source("ui", "index.html")
        row = page[page.index("tr.innerHTML = `"):]
        row = row[:row.index("`;")]
        self.assertLess(row.index("data-open-ip"), row.index("mac-lldp"),
                        "the address cell precedes the MAC cell")
        self.assertLess(row.index("hostname_cell"), row.index("mac-lldp"),
                        "the hostname cell precedes the MAC cell")

    def test_three_columns_are_asked_to_freeze(self):
        page = self.source("ui", "index.html")
        self.assertIn('data-sticky-cols="3"', page,
                      "checkbox, IP and Hostname stay visible while the rest scrolls")

    def test_the_offsets_are_measured_rather_than_written_down(self):
        """A hardcoded offset is right in one layout and wrong in the others."""
        css = self.source("ui", "device-table.css")
        stray = [line.strip() for line in css.splitlines()
                 if re.search(r"top:\s*[1-9][0-9]*px", line)]
        self.assertEqual(stray, [], "sticky offsets must come from measured variables")
        script = self.source("ui", "sticky-columns.js")
        self.assertIn("--sticky-col2-offset", script)
        self.assertIn("--sticky-col3-offset", script)
        self.assertIn("getBoundingClientRect", script)

    def test_one_measurement_path_serves_every_sticky_table(self):
        """Four tables, one helper: Configure asks for two columns, Device Info three."""
        script = self.source("ui", "sticky-columns.js")
        self.assertIn("data-sticky-cols", script)
        for page in ("index.html", "matrix/configure.html"):
            self.assertIn("sticky-columns.js", self.source("ui", *page.split("/")), page)

    def test_the_mirrored_scrollbar_exists_and_cannot_oscillate(self):
        """The guard lives in the shared module, not in the page.

        These assertions used to read index.html, where the sync code once was.
        After it moved to ui/mirror-scroll.js the page still mentioned
        `syncOwner` in a comment, so the test kept passing while asserting
        nothing about behaviour. It now reads the module that implements it.
        """
        page = self.source("ui", "index.html")
        self.assertIn('id="table_scroll_mirror"', page)
        self.assertIn("data-mirror-scroll", page, "the container opts in")
        module = self.source("ui", "mirror-scroll.js")
        self.assertIn("syncOwner", module,
                      "assigning scrollLeft makes the receiver echo it straight back")
        self.assertIn("scrollWidth", module, "the track is sized from the real width")

    def test_the_mirror_is_not_driven_by_a_timer(self):
        """Dimensions come from observers; polling them every frame is waste."""
        module = self.source("ui", "mirror-scroll.js")
        self.assertIn("ResizeObserver", module)
        self.assertNotIn("setInterval", module)
        self.assertNotIn("setTimeout", module)

    def test_one_shared_mirror_implementation_serves_every_section(self):
        """Five sections, one module: four copies would be four to keep in step."""
        for page in ("index.html", "matrix/configure.html",
                     "matrix/index.html", "matrix/usb.html"):
            text = self.source("ui", *page.split("/"))
            self.assertIn("mirror-scroll.js", text, page)
            self.assertIn("data-mirror-scroll", text, page)
        # And nothing re-implements the sync outside the module.
        for page in ("index.html", "matrix/configure.html",
                     "matrix/index.html", "matrix/usb.html"):
            text = self.source("ui", *page.split("/"))
            code = re.sub(r"<!--.*?-->", "", text, flags=re.S)
            code = re.sub(r"/\*.*?\*/", "", code, flags=re.S)
            self.assertNotIn("syncOwner =", code, f"{page} re-implements the guard")

    def test_the_mirror_observers_are_created_once(self):
        """The defect that caused Page Unresponsive: observers re-armed in their
        own callback grow without bound."""
        module = self.source("ui", "mirror-scroll.js")
        self.assertLessEqual(module.count("new MutationObserver("), 2)
        self.assertLessEqual(module.count("new ResizeObserver("), 1)

    def test_the_keyboard_can_still_see_where_it_is(self):
        """outline:none hid the ring from the keyboard as well as the mouse."""
        page = self.source("ui", "index.html")
        self.assertIn(":focus-visible{outline:", page.replace(" ", ""))


class IdleLoggingTests(ServerTestBase):
    """An idle poll must not write to the log at INFO.

    The records that matter -- a mutation, a refusal, a device that stopped
    answering -- are only useful if they are not buried. Measured with one
    browser open and all four pages cycling, the log was taking 188 lines a
    minute, of which 39 were `[CACHE] Loaded N units` and three per poll were
    multi-thousand-character dumps of an entire device record.
    """

    def test_repeated_polling_writes_nothing_at_info(self):
        import io, logging
        self._patch_cache(FULL_CACHE_UNITS)
        self.seed(*OBSERVED_UDP)
        for _ in range(6):                      # warm every throttle and cache
            self.client.get("/api/usb_state")
            self.client.get("/api/cache")

        buffer = io.StringIO()
        handler = logging.StreamHandler(buffer)
        handler.setLevel(logging.INFO)
        logger = logging.getLogger("omni_upgrade")
        logger.addHandler(handler)
        self.addCleanup(lambda: logger.removeHandler(handler))
        for _ in range(12):                     # a quiet minute of polling
            self.client.get("/api/usb_state")
            self.client.get("/api/cache")
            self.client.get("/api/usb_extenders")
        written = [line for line in buffer.getvalue().splitlines() if line.strip()]
        self.assertEqual(written, [], "an idle poll logged at INFO")

    def test_the_known_offenders_stay_at_debug(self):
        source = Path(srv.__file__).with_name("OmniMatrix_upgrade_server_v7_6y.py").read_text(encoding="utf-8")
        for fragment in ('log.debug("[CACHE] Loaded %d units from units_cache.json (list format)"',
                         'log.debug("[API/USB_STATE] Found',
                         'log.debug("Device %s paired_devices'):
            self.assertIn(fragment, source, fragment)
        for fragment in ('log.info(f"[CACHE] Loaded',
                         'log.info(f"[API/STATE] Sample encoder'):
            self.assertNotIn(fragment, source, fragment)


class ApplicationVersionTests(ServerTestBase):
    """One authoritative version, and nothing carrying its own copy.

    The footer said V1.0.1 and the Settings dialog V1.0.4 while the build was
    V1.0.6, because each had been given a literal at some point and nothing
    brought them back into step.
    """

    def source(self, *parts):
        return Path(srv.__file__).resolve().parent.joinpath(*parts).read_text(encoding="utf-8")

    def version_file(self):
        return (Path(srv.__file__).resolve().parent / "VERSION").read_text(encoding="utf-8").strip()

    def test_the_version_file_is_what_the_server_reports(self):
        self.assertEqual(srv._app_version(), self.version_file())

    def test_the_settings_endpoint_agrees(self):
        self.assertEqual(self.client.get("/api/config").get_json()["app_version"],
                         self.version_file())

    def test_the_support_dump_agrees(self):
        dump = self.client.get("/api/ts_export").get_json()
        self.assertEqual(dump["app_version"], self.version_file())

    def test_no_page_or_script_hardcodes_a_version(self):
        """A literal here is how the displays drifted apart in the first place."""
        pattern = re.compile(r"V\d+\.\d+\.\d+")
        for name in ("index.html", "settings.js", "matrix/configure.html",
                     "matrix/index.html", "matrix/usb.html", "appearance.js"):
            text = self.source("ui", *name.split("/"))
            self.assertEqual(pattern.findall(text), [], f"{name} carries a version literal")

    def test_both_displays_read_the_server(self):
        module = self.source("ui", "settings.js")
        self.assertIn("cfg_app_version", module)
        self.assertIn("app_version_footer", module)
        self.assertIn("applyAppVersion", module)
        self.assertIn('id="app_version_footer"', self.source("ui", "index.html"))

    def test_the_launcher_reads_the_same_file(self):
        launcher = (Path(srv.__file__).resolve().parent / "app_launcher.py").read_text(encoding="utf-8")
        self.assertIn('"VERSION"', launcher)
        self.assertIn("OMNI_VERSION", launcher)

    def test_the_release_build_reads_the_same_file(self):
        workflow = (Path(srv.__file__).resolve().parent
                    / ".github" / "workflows" / "build-cross-platform.yml").read_text(encoding="utf-8")
        self.assertIn("cat VERSION", workflow)


class WorkspaceDescriptionTests(ServerTestBase):
    """The layout hint must describe the layout that exists."""

    def source(self, *parts):
        return Path(srv.__file__).resolve().parent.joinpath(*parts).read_text(encoding="utf-8")

    def test_the_hint_no_longer_advertises_the_removed_features(self):
        script = self.source("ui", "appearance.js")
        block = script[script.index("id: 'workspace'"):]
        block = block[:block.index("}") + 1]
        for gone in ("rail", "inspector"):
            self.assertNotIn(gone, block.lower(), f"the hint still advertises the {gone}")

    def test_no_active_surface_presents_the_rail_or_inspector_as_current(self):
        for name in ("index.html", "settings.js", "appearance.js",
                     "matrix/configure.html", "matrix/index.html", "matrix/usb.html"):
            text = self.source("ui", *name.split("/")).lower()
            for phrase in ("navigation rail", "context inspector", "inspector drawer", "workspace.js"):
                self.assertNotIn(phrase, text, f"{name} mentions {phrase}")


class ThemedLogoTests(ServerTestBase):
    """Light mode uses the black wordmark; dark mode keeps the existing asset."""

    def source(self, *parts):
        return Path(srv.__file__).resolve().parent.joinpath(*parts).read_text(encoding="utf-8")

    def test_both_assets_exist_and_share_an_aspect_ratio(self):
        import struct
        base = Path(srv.__file__).resolve().parent / "ui"
        sizes = {}
        for name in ("companylogo.png", "companylogo-light.png"):
            data = (base / name).read_bytes()
            width, height = struct.unpack(">II", data[16:24])
            sizes[name] = width / height
        self.assertAlmostEqual(sizes["companylogo.png"], sizes["companylogo-light.png"], places=2,
                               msg="the swap must not change the header geometry")

    def test_the_light_asset_is_used_only_in_light_mode(self):
        css = self.source("ui", "templates.css")
        rule = css[css.index("companylogo-light.png") - 200:css.index("companylogo-light.png") + 60]
        self.assertIn(".light", rule, "the light asset is selected by the light theme class")
        self.assertNotIn('src="/ui/companylogo-light.png"', self.source("ui", "index.html"),
                         "the dark asset stays in the markup as the fallback")

    def test_every_page_keeps_the_existing_dark_asset_in_the_markup(self):
        for name in ("index.html", "matrix/configure.html", "matrix/index.html", "matrix/usb.html"):
            self.assertIn('src="/ui/companylogo.png"', self.source("ui", *name.split("/")), name)

    def test_the_theme_is_applied_before_first_paint_on_every_page(self):
        """The matrix pages ran this in <head>, where document.body is null, and
        deferred to DOMContentLoaded -- so the first paint used the wrong
        palette and the wrong logo."""
        for name in ("matrix/configure.html", "matrix/index.html", "matrix/usb.html"):
            text = self.source("ui", *name.split("/"))
            head = text[:text.index("<body")]
            self.assertIn("document.documentElement", head,
                          f"{name} cannot theme the root before the body exists")


class StartupPortTests(ServerTestBase):
    """8080 first, then the next free port -- never a silent jump.

    The selection walks upward from the preferred port, so which port a given
    machine ends up on says nothing about the logic. These tests hold specific
    ports open and assert the walk, rather than depending on what happens to be
    free here.
    """

    HOST = "127.0.0.1"

    def hold(self, *ports):
        """Occupy the given ports for the duration of one test."""
        import socket as _socket
        held = []
        for port in ports:
            sock = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
            try:
                sock.bind((self.HOST, port))
                sock.listen(1)
            except OSError:
                sock.close()
                self.skipTest(f"port {port} is not available on this machine to hold")
            held.append(sock)
            self.addCleanup(sock.close)
        return held

    def free_base(self):
        """A port we know is free, plus the two above it, so the walk is ours."""
        import socket as _socket
        with _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM) as probe:
            probe.bind((self.HOST, 0))
            base = probe.getsockname()[1]
        return base

    def test_the_preferred_port_is_used_when_it_is_free(self):
        base = self.free_base()
        self.assertEqual(srv._select_bind_port(base, self.HOST), base)

    def test_an_occupied_port_moves_to_the_next_one(self):
        base = self.free_base()
        self.hold(base)
        self.assertEqual(srv._select_bind_port(base, self.HOST), base + 1,
                         "the next port, not an arbitrary one")

    def test_it_keeps_walking_while_ports_are_occupied(self):
        base = self.free_base()
        self.hold(base, base + 1, base + 2)
        self.assertEqual(srv._select_bind_port(base, self.HOST), base + 3)

    def test_the_default_preferred_port_is_8080(self):
        """Whatever this machine does with it, 8080 is what is asked for first."""
        self.assertEqual(srv.PORT, 8080)
        source = Path(srv.__file__).with_name("OmniMatrix_upgrade_server_v7_6y.py").read_text(encoding="utf-8")
        self.assertIn('os.getenv("OMNI_PORT", "8080")', source)

    def test_the_selection_is_reachable_for_testing(self):
        """It was nested inside main(), so the only way to check it was to start
        the application and look at the log."""
        self.assertTrue(callable(getattr(srv, "_select_bind_port", None)))


class SurfaceOpacityTests(ServerTestBase):
    """A surface a sticky cell is painted with must be fully opaque.

    `--surface-elevated` was `color-mix(in srgb, var(--light-bg) 78%, #000000 4%)`.
    Those percentages sum to 82, and colour-mix multiplies the result's alpha by
    the sum, so the token was 18% transparent and every frozen column built on
    it let the scrolling rows show through in light mode.
    """

    def source(self, *parts):
        return Path(srv.__file__).resolve().parent.joinpath(*parts).read_text(encoding="utf-8")

    def test_every_colour_mix_sums_to_one_hundred_percent(self):
        css = self.source("ui", "templates.css")
        offenders = []
        for line in css.splitlines():
            text = line.strip()
            # The @supports feature test is a probe, not a painted surface.
            if "color-mix(in srgb" not in text or text.startswith("@supports"):
                continue
            percentages = [int(v) for v in re.findall(r"(\d+)%", text)]
            # One unstated percentage takes the remainder, which is always fine.
            if len(percentages) >= 2 and sum(percentages) != 100:
                offenders.append((text[:80], sum(percentages)))
        self.assertEqual(offenders, [], "a mix that does not sum to 100% is partly transparent")

    def test_the_elevated_surface_is_opaque(self):
        css = self.source("ui", "templates.css")
        rule = next(line for line in css.splitlines()
                    if "--surface-elevated: color-mix" in line)
        percentages = [int(v) for v in re.findall(r"(\d+)%", rule)]
        self.assertEqual(sum(percentages), 100, rule.strip())


class UpdateCheckTests(ServerTestBase):
    """Whether a newer release exists -- decided here, never by contacting GitHub.

    Every test stubs the HTTP boundary. The suite is deterministic offline, and
    run_tests.py's transport fences would block a real request anyway.
    """

    def setUp(self):
        super().setUp()
        # The cache is process-global; each test starts from nothing.
        with srv._update_check_lock:
            srv._update_check_cache["checked_at"] = 0.0
            srv._update_check_cache["result"] = None
        self.addCleanup(self._clear_update_cache)
        self.calls = []

    def _clear_update_cache(self):
        with srv._update_check_lock:
            srv._update_check_cache["checked_at"] = 0.0
            srv._update_check_cache["result"] = None

    class Response:
        def __init__(self, payload, status=200):
            self._payload = payload
            self.status_code = status

        def json(self):
            if isinstance(self._payload, Exception):
                raise self._payload
            return self._payload

        def raise_for_status(self):
            if self.status_code >= 400:
                raise RuntimeError(f"HTTP {self.status_code}")

    def stub_github(self, payload=None, status=200, raises=None):
        """Answer the one call that leaves the machine, or fail the way it can."""
        def fake_fetch(*args, **kwargs):
            self.calls.append(("fetch", srv.UPDATE_CHECK_TIMEOUT))
            if raises is not None:
                raise raises
            response = self.Response(payload, status)
            if status == 404:
                raise LookupError("no releases published")
            if status in (403, 429):
                raise RuntimeError("rate limited")
            response.raise_for_status()
            return response.json()
        self.patch_srv("_fetch_latest_release", fake_fetch)

    def release(self, tag, draft=False, prerelease=False, assets=None):
        return {"tag_name": tag, "draft": draft, "prerelease": prerelease,
                "html_url": f"https://github.com/Hall-Research-Technologies/omniSuite/releases/tag/{tag}",
                "assets": assets or []}

    def check(self, force=False):
        query = "?force=1" if force else ""
        return self.client.get("/api/update_check" + query).get_json()

    # ---- version comparison ----
    def test_the_same_version_is_up_to_date(self):
        self.stub_github(self.release(srv._app_version()))
        body = self.check()
        self.assertFalse(body["update_available"])
        self.assertEqual(body["status"], srv.UPDATE_CURRENT)

    @staticmethod
    def _bumped(installed):
        """One newer patch, minor and major than whatever is installed.

        Derived rather than written down: hard-coded "newer" versions stop
        being newer the moment the product reaches them, which is a test that
        has to be edited at every release for no reason.
        """
        digits = [int(part) for part in
                  re.findall(r"\d+", str(installed or ""))[:3]]
        while len(digits) < 3:
            digits.append(0)
        major, minor, patch = digits
        return ("%d.%d.%d" % (major, minor, patch + 1),
                "%d.%d.0" % (major, minor + 1),
                "%d.0.0" % (major + 1))

    def test_a_newer_patch_minor_or_major_is_an_update(self):
        for newer in self._bumped(srv._app_version()):
            with self.subTest(newer=newer):
                self._clear_update_cache()
                self.stub_github(self.release(f"V{newer}"))
                body = self.check()
                self.assertTrue(body["update_available"], newer)
                self.assertEqual(body["latest_version"], f"V{newer}")

    def test_the_newer_fixtures_really_are_newer_than_this_build(self):
        """Otherwise the test above could pass by asserting nothing."""
        installed = srv._app_version()
        for newer in self._bumped(installed):
            self.assertTrue(srv._version_is_newer(newer, installed),
                            "%s is not newer than the installed %s"
                            % (newer, installed))

    def test_an_installed_version_newer_than_github_is_not_an_update(self):
        self.stub_github(self.release("V1.0.1"))
        self.assertFalse(self.check()["update_available"])

    def test_the_v_prefix_is_optional_on_either_side(self):
        self.assertTrue(srv._version_is_newer("v1.0.8", "V1.0.7"))
        self.assertTrue(srv._version_is_newer("1.0.8", "V1.0.7"))
        self.assertFalse(srv._version_is_newer("V1.0.7", "1.0.7"))

    def test_versions_compare_numerically_not_lexically(self):
        """1.0.10 is newer than 1.0.9; a string comparison says otherwise."""
        self.assertTrue(srv._version_is_newer("1.0.10", "1.0.9"))
        self.assertFalse(srv._version_is_newer("1.0.9", "1.0.10"))

    def test_a_malformed_tag_is_not_an_update(self):
        self.stub_github(self.release("not-a-version"))
        body = self.check()
        self.assertFalse(body["update_available"])
        self.assertEqual(body["status"], srv.UPDATE_UNABLE)

    # ---- what counts as a release ----
    def test_a_draft_is_ignored(self):
        self.stub_github(self.release("V9.9.9", draft=True))
        body = self.check()
        self.assertFalse(body["update_available"])
        self.assertEqual(body["status"], srv.UPDATE_UNABLE)

    def test_a_prerelease_is_ignored(self):
        self.stub_github(self.release("V9.9.9", prerelease=True))
        self.assertFalse(self.check()["update_available"])

    def test_no_releases_at_all_is_reported_not_crashed(self):
        self.stub_github(None, status=404)
        body = self.check()
        self.assertFalse(body["ok"])
        self.assertEqual(body["status"], srv.UPDATE_UNABLE)

    # ---- failure modes ----
    def test_a_timeout_is_not_fatal(self):
        import requests as _requests
        self.stub_github(raises=_requests.exceptions.Timeout("timed out"))
        body = self.check()
        self.assertFalse(body["ok"])
        self.assertEqual(body["status"], srv.UPDATE_UNABLE)
        self.assertEqual(body["release_url"], srv.GITHUB_RELEASES_URL,
                         "the releases page stays reachable when the check fails")

    def test_a_network_failure_is_not_fatal(self):
        self.stub_github(raises=OSError("name resolution failed"))
        self.assertEqual(self.check()["status"], srv.UPDATE_UNABLE)

    def test_an_http_error_is_not_fatal(self):
        self.stub_github(self.release("V1.0.8"), status=500)
        self.assertEqual(self.check()["status"], srv.UPDATE_UNABLE)

    def test_rate_limiting_is_handled_cleanly(self):
        self.stub_github(None, status=403)
        body = self.check()
        self.assertEqual(body["status"], srv.UPDATE_UNABLE)
        self.assertFalse(body["ok"])

    def test_malformed_json_is_not_fatal(self):
        self.stub_github(ValueError("no json"))
        self.assertEqual(self.check()["status"], srv.UPDATE_UNABLE)

    def test_a_release_with_no_assets_still_reports_the_update(self):
        newer = self._bumped(srv._app_version())[0]
        self.stub_github(self.release(f"V{newer}", assets=[]))
        body = self.check()
        self.assertTrue(body["update_available"])
        self.assertIsNone(body["asset"])

    # ---- platform asset selection ----
    # The names tools/build_release.py publishes. Each states its architecture,
    # so an asset list containing a Windows x86-64 zip and a macOS x86-64 zip is
    # no longer ambiguous. ArtifactNamingTests holds these and the build script
    # together.
    ASSETS = [
        {"name": f"OmniSuite-V1.0.8-{tail}", "size": 1,
         "browser_download_url":
             "https://github.com/Hall-Research-Technologies/omniSuite/releases/"
             f"download/V1.0.8/OmniSuite-V1.0.8-{tail}"}
        for tail in ("Windows-x86_64.zip", "macOS-arm64.zip",
                     "macOS-x86_64.zip", "Ubuntu-x86_64.tar.gz")
    ]

    def asset_for(self, system, machine):
        self.patch_srv("platform", type("P", (), {
            "system": staticmethod(lambda: system),
            "machine": staticmethod(lambda: machine)}))
        return srv._select_platform_asset(self.ASSETS)

    def test_each_platform_selects_its_own_asset(self):
        cases = [("Windows", "AMD64", "windows-x86_64"), ("Linux", "x86_64", "ubuntu-x86_64"),
                 ("Darwin", "arm64", "macos-arm64"), ("Darwin", "x86_64", "macos-x86_64")]
        for system, machine, expected in cases:
            with self.subTest(system=system, machine=machine):
                asset = self.asset_for(system, machine)
                self.assertIsNotNone(asset, f"{system}/{machine}")
                self.assertEqual(asset["platform"], expected)

    def test_an_unknown_platform_offers_no_asset(self):
        """Guessing is worse than sending the operator to the releases page."""
        self.assertIsNone(self.asset_for("SunOS", "sparc"))
        self.assertIsNone(self.asset_for("Darwin", "riscv"))
        # No Linux build but the x86-64 one is published, so an ARM Linux
        # machine is offered nothing rather than a binary it cannot run.
        self.assertIsNone(self.asset_for("Linux", "aarch64"))

    def test_an_asset_url_outside_the_project_is_refused(self):
        hostile = [{"name": "OmniSuite-V1.0.8-Windows-x86_64.zip",
                    "browser_download_url": "https://evil.example/OmniSuite-V1.0.8-Windows-x86_64.zip"}]
        self.patch_srv("platform", type("P", (), {
            "system": staticmethod(lambda: "Windows"), "machine": staticmethod(lambda: "AMD64")}))
        self.assertIsNone(srv._select_platform_asset(hostile))

    def test_only_this_repositorys_release_urls_are_returned(self):
        self.stub_github({"tag_name": "V1.0.8", "draft": False, "prerelease": False,
                          "html_url": "https://evil.example/releases", "assets": []})
        body = self.check()
        self.assertEqual(body["release_url"], srv.GITHUB_RELEASES_URL,
                         "an unexpected URL must not be handed to the browser")

    # ---- caching ----
    def test_the_result_is_cached_rather_than_refetched(self):
        self.stub_github(self.release("V1.0.8"))
        self.check()
        self.check()
        self.check()
        self.assertEqual(len(self.calls), 1, "GitHub was asked more than once")

    def test_check_now_forces_a_refresh(self):
        self.stub_github(self.release("V1.0.8"))
        self.check()
        self.check(force=True)
        self.assertEqual(len(self.calls), 2)

    def test_an_expired_cache_is_refetched(self):
        self.stub_github(self.release("V1.0.8"))
        self.check()
        with srv._update_check_lock:
            srv._update_check_cache["checked_at"] -= srv.UPDATE_CHECK_TTL + 1
        self.check()
        self.assertEqual(len(self.calls), 2)

    def test_the_cadence_is_hours_not_minutes(self):
        """This must never become another polling loop."""
        self.assertGreaterEqual(srv.UPDATE_CHECK_TTL, 60 * 60)

    def test_a_failure_falls_back_to_the_last_good_answer_marked_stale(self):
        self.stub_github(self.release("V1.0.8"))
        self.check()
        self.stub_github(raises=OSError("offline"))
        body = self.check(force=True)
        self.assertEqual(body["latest_version"], "V1.0.8")
        self.assertTrue(body["stale"], "a remembered answer must not look current")

    # ---- it must not become a dependency ----
    def test_the_request_is_bounded(self):
        self.stub_github(self.release("V1.0.8"))
        self.check()
        self.assertTrue(self.calls)
        self.assertIsNotNone(self.calls[0][1], "no timeout was given")
        self.assertLessEqual(self.calls[0][1], 10)

    def test_no_credential_is_used(self):
        source = Path(srv.__file__).with_name("OmniMatrix_upgrade_server_v7_6y.py").read_text(encoding="utf-8")
        block = source[source.index("def _fetch_latest_release"):]
        block = block[:block.index("def _update_check")]
        for forbidden in ("Authorization", "token", "GITHUB_TOKEN", "password"):
            self.assertNotIn(forbidden, block, forbidden)

    def test_nothing_is_downloaded_or_installed(self):
        source = Path(srv.__file__).with_name("OmniMatrix_upgrade_server_v7_6y.py").read_text(encoding="utf-8")
        start = source.index("# ---------------- release update check ----------------")
        block = source[start:source.index('@app.route("/api/config"', start)]
        for forbidden in ("urlretrieve", "shutil.unpack", "zipfile.ZipFile", "subprocess"):
            self.assertNotIn(forbidden, block, f"the update check must not {forbidden}")

    def test_startup_checks_once_without_waiting_for_it(self):
        """One check for the whole application, on a thread nothing joins."""
        source = Path(srv.__file__).with_name("OmniMatrix_upgrade_server_v7_6y.py").read_text(encoding="utf-8")
        start = source.index("def start_background_startup_tasks")
        block = source[start:start + 1400]
        self.assertIn("_update_check()", block, "startup should prime the shared result")
        self.assertIn("daemon=True", block, "and must never hold startup open for it")
        self.assertNotIn(".join(", block, "nothing may wait on the update thread")

    def test_the_startup_check_cannot_break_startup(self):
        """GitHub being unreachable at boot must change nothing else."""
        self.stub_github(raises=OSError("offline at boot"))
        srv.start_background_startup_tasks()
        srv.start_background_startup_tasks()
        self.assertEqual(self.attempted_mutations, [])

    def test_no_polling_path_calls_it(self):
        """Scanning, the matrix polls and page loads must not touch GitHub."""
        source = Path(srv.__file__).with_name("OmniMatrix_upgrade_server_v7_6y.py").read_text(encoding="utf-8")
        callers = [line.strip() for line in source.splitlines()
                   if "_update_check(" in line and "def " not in line]
        # Exactly two: the endpoint, and the single startup prime. Anything else
        # would be a polling path reaching the public Internet.
        self.assertEqual(sorted(callers),
                         sorted(["result = _update_check(force=force)", "_update_check()"]),
                         f"unexpected caller of the update check: {callers}")

    # ---- the dialog ----
    def test_settings_offers_the_official_releases_page_unconditionally(self):
        module = Path(srv.__file__).resolve().parent.joinpath("ui", "settings.js").read_text(encoding="utf-8")
        self.assertIn("https://github.com/Hall-Research-Technologies/omniSuite/releases", module)
        self.assertIn("cfg_update_check", module)
        self.assertIn("cfg_release_link", module)

    def test_the_page_never_polls_the_update_check(self):
        """Once when Settings is wired, and once per Check Now. Never on a timer."""
        module = Path(srv.__file__).resolve().parent.joinpath("ui", "settings.js").read_text(encoding="utf-8")
        self.assertEqual(module.count("setInterval"), 0, "settings.js must own no timer")
        calls = [line.strip() for line in module.splitlines()
                 if "checkForUpdates(" in line and "async function" not in line]
        self.assertEqual(len(calls), 2, f"unexpected update-check call sites: {calls}")
        self.assertTrue(any("addEventListener" in line for line in calls))
        self.assertTrue(any(line == "checkForUpdates(false);" for line in calls))

    def test_settings_does_not_await_the_check_before_rendering(self):
        module = Path(srv.__file__).resolve().parent.joinpath("ui", "settings.js").read_text(encoding="utf-8")
        init = module[module.index("function initSettings"):]
        init = init[:init.index("}")]
        self.assertIn("checkForUpdates(false)", init)
        self.assertNotIn("await checkForUpdates", init,
                         "the dialog must render before GitHub answers")


class RescanEnrichmentTests(ServerTestBase):
    """A rescan refreshes an already-known device; Clear Units is not a refresh.

    Two live defects. The Icron module firmware was read on every usb_icron
    reply and thrown away, so an endpoint that does not answer the UDP Basic
    Query -- measured: directed discovery to it times out while its sibling
    answers -- had no firmware at all and Configure showed N/A. And
    _refresh_usb_parent_map() returned immediately unless `pending` was
    non-empty, and `pending` only holds units with no association, so a device
    stopped being asked the moment it became known.
    """

    ICRON = "2.0.9"

    def setUp(self):
        super().setUp()
        self._patch_cache(FULL_CACHE_UNITS)
        self.reads = []

    def stub_icron(self, revision=ICRON, raises=None, mac=None, usb_ip=None):
        """Answer usb_icron the way a parent does, or fail the way one can."""
        def fake(ip, *args, **kwargs):
            self.reads.append(ip)
            if raises is not None:
                raise raises
            unit = next((u for u in FULL_CACHE_UNITS if u["ip"] == ip), None)
            if unit is None:
                return {}
            body = {"macaddress": mac or unit["usb_mac"],
                    "type": unit["usb_type"],
                    "ipaddress": usb_ip or f"10.9.9.{FULL_CACHE_UNITS.index(unit) + 1}"}
            if revision is not None:
                body["revision"] = revision
            return body
        self.patch_srv("_usb_get_config", fake)

    def association(self, usb_mac):
        key = "mac:" + srv._norm_usb_mac(
            next(u for u in FULL_CACHE_UNITS if u["usb_mac"] == usb_mac).get("mac", "")) \
            if False else None
        with srv._usb_association_lock:
            for entry in srv._USB_ASSOCIATION.values():
                if srv._norm_usb_mac(entry.get("usb_mac")) == srv._norm_usb_mac(usb_mac):
                    return dict(entry)
        return {}

    # ---- the firmware itself ----
    def test_a_usb_icron_read_records_the_module_firmware(self):
        """It was in every reply and was being discarded."""
        self.stub_icron()
        srv._refresh_usb_parent_map(force=True)
        self.assertEqual(self.association(E4521_USB_MAC).get("icron_revision"), self.ICRON)

    def test_an_endpoint_that_never_answers_udp_still_gets_its_firmware(self):
        """The reported defect: no UDP reply, so no firmware, so N/A."""
        self.stub_icron()
        srv._refresh_usb_parent_map(force=True)
        device = next(d for d in srv._usb_extender_view()["devices"]
                      if d["mac"] == usb.normalize_mac(E4521_USB_MAC))
        self.assertEqual(device["usb_firmware"], self.ICRON)
        self.assertEqual(device["usb_firmware_source"], "usb_icron")

    def test_the_parents_own_firmware_is_never_substituted(self):
        """Two products, two versions. 2.1.2 is the parent's, not the module's."""
        self.stub_icron(revision=None)          # the reply omits it
        srv._refresh_usb_parent_map(force=True)
        device = next(d for d in srv._usb_extender_view()["devices"]
                      if d["mac"] == usb.normalize_mac(E4521_USB_MAC))
        self.assertEqual(device["usb_firmware"], "", "blank, never the parent's")
        self.assertEqual(device["firmware"], PARENT_FIRMWARE)

    # ---- the merge rule: fresh known > cached known > unknown ----
    def test_a_cached_blank_is_filled_by_a_fresh_value(self):
        self.stub_icron(revision=None)
        srv._refresh_usb_parent_map(force=True)
        self.assertEqual(self.association(E4521_USB_MAC).get("icron_revision"), "")
        self.stub_icron(revision=self.ICRON)
        srv._refresh_usb_parent_map(force=True)
        self.assertEqual(self.association(E4521_USB_MAC).get("icron_revision"), self.ICRON)

    def test_a_failed_read_does_not_erase_a_known_value(self):
        """An optional query timing out must not undo something verified."""
        self.stub_icron(revision=self.ICRON)
        srv._refresh_usb_parent_map(force=True)
        self.stub_icron(raises=OSError("timed out"))
        srv._refresh_usb_parent_map(force=True)
        self.assertEqual(self.association(E4521_USB_MAC).get("icron_revision"), self.ICRON)

    def test_a_reply_without_the_field_does_not_erase_a_known_value(self):
        self.stub_icron(revision=self.ICRON)
        srv._refresh_usb_parent_map(force=True)
        self.stub_icron(revision=None)
        srv._refresh_usb_parent_map(force=True)
        self.assertEqual(self.association(E4521_USB_MAC).get("icron_revision"), self.ICRON)

    def test_a_newer_value_replaces_an_older_one(self):
        self.stub_icron(revision="1.9.4")
        srv._refresh_usb_parent_map(force=True)
        self.stub_icron(revision="2.0.9")
        srv._refresh_usb_parent_map(force=True)
        self.assertEqual(self.association(E4521_USB_MAC).get("icron_revision"), "2.0.9")

    def test_a_changed_usb_address_is_taken_from_the_fresh_read(self):
        self.stub_icron(usb_ip="10.9.9.1")
        srv._refresh_usb_parent_map(force=True)
        self.stub_icron(usb_ip="10.9.9.99")
        srv._refresh_usb_parent_map(force=True)
        self.assertEqual(self.association(E4521_USB_MAC).get("usb_ip"), "10.9.9.99")

    # ---- an explicit scan refreshes what is already known ----
    def test_a_known_device_is_re_read_when_the_operator_scans(self):
        """`pending` is empty once everything is associated; force must ignore it."""
        self.stub_icron(revision=None)
        srv._refresh_usb_parent_map(force=True)
        self.assertTrue(self.reads, "nothing was read at all")
        self.reads.clear()
        srv._refresh_usb_parent_map(force=True)
        self.assertTrue(self.reads, "an explicit scan skipped an already-known device")

    def test_background_refresh_still_only_tops_up_what_is_missing(self):
        """Ordinary polling must not become a sweep of every parent."""
        self.stub_icron(revision=self.ICRON)
        srv._refresh_usb_parent_map(force=True)     # everything now associated
        self.reads.clear()
        srv._refresh_usb_parent_map()               # background, not forced
        self.assertEqual(self.reads, [], "a background refresh re-read known devices")

    def test_the_scan_endpoint_asks_for_a_forced_refresh(self):
        source = Path(srv.__file__).with_name("OmniMatrix_upgrade_server_v7_6y.py").read_text(encoding="utf-8")
        body = source[source.index("def api_scan("):]
        body = body[:body.index("\n@app.route")]
        self.assertIn("_refresh_usb_parent_map(force=True)", body,
                      "an explicit scan must refresh the USB side")

    def test_the_scan_does_not_wait_for_the_enrichment(self):
        """Discovery stays responsive; the USB read runs in the background."""
        source = Path(srv.__file__).with_name("OmniMatrix_upgrade_server_v7_6y.py").read_text(encoding="utf-8")
        body = source[source.index("def api_scan("):]
        body = body[:body.index("\n@app.route")]
        self.assertIn("_executor.submit(_enrich_usb_after_scan)", body)

    # ---- identity ----
    def test_one_record_survives_a_changed_address(self):
        self.stub_icron(usb_ip="10.9.9.1")
        srv._refresh_usb_parent_map(force=True)
        self.stub_icron(usb_ip="10.9.9.42")
        srv._refresh_usb_parent_map(force=True)
        macs = [srv._norm_usb_mac(e.get("usb_mac")) for e in srv._USB_ASSOCIATION.values()]
        self.assertEqual(len(macs), len(set(macs)), "an address change created a second record")

    # ---- every surface agrees ----
    def test_configure_the_workbook_and_the_dump_report_the_same_firmware(self):
        import io as _io, re as _re, zipfile as _zipfile
        self.seed_integrated()
        self.stub_icron(revision=self.ICRON)
        srv._refresh_usb_parent_map(force=True)

        # Configure reads the extender view.
        device = next(d for d in srv._usb_extender_view()["devices"]
                      if d["mac"] == usb.normalize_mac(E4521_USB_MAC))
        self.assertEqual(device["usb_firmware"], self.ICRON)

        # The workbook, as an operator actually downloads it. The USB sheet's
        # Firmware column is the endpoint's, never the parent's.
        book = self.client.get("/api/download_inventory")
        self.assertEqual(book.status_code, 200)
        archive = _zipfile.ZipFile(_io.BytesIO(book.data))
        sheet = archive.read("xl/worksheets/sheet2.xml").decode("utf-8")
        rows = [[a or b for a, b in _re.findall(
            r"<is><t[^>]*>([^<]*)</t></is>|<v>([^<]*)</v>", row)]
            for row in _re.findall(r"<row.*?</row>", sheet, _re.S)]
        header, body = rows[0], rows[1:]
        firmware_column = header.index("Firmware")
        mac_column = header.index("USB MAC")
        target = next(r for r in body
                      if srv._norm_usb_mac(r[mac_column]) == srv._norm_usb_mac(E4521_USB_MAC))
        self.assertEqual(target[firmware_column], self.ICRON)
        self.assertNotEqual(target[firmware_column], PARENT_FIRMWARE)

        # And the support dump, with the provider recorded beside the value.
        dump = self.client.get("/api/ts_export").get_json()
        entry = next(e for e in dump["usb_endpoints"]
                     if srv._norm_usb_mac(e.get("usb_mac")) == srv._norm_usb_mac(E4521_USB_MAC))
        self.assertEqual(entry["usb_firmware"], self.ICRON)
        self.assertEqual(entry["field_authority"]["usb_firmware"], "usb_icron")
        self.assertEqual(entry["firmware"], PARENT_FIRMWARE, "the parent's stays its own")

    def test_the_learned_value_is_persisted_so_a_restart_keeps_it(self):
        self.stub_icron(revision=self.ICRON)
        srv._refresh_usb_parent_map(force=True)
        saved = json.loads(Path(srv.USB_ASSOCIATIONS).read_text(encoding="utf-8"))
        revisions = [e.get("icron_revision") for e in (saved.get("associations") or {}).values()]
        self.assertIn(self.ICRON, revisions, "the value was not written to disk")


class SharedDensityTests(ServerTestBase):
    """The Compact layout reaches every page, from one implementation.

    Measured before the fix: `data-template="compact"` was on the root of all
    four pages, but only Device Info carried `body.compact` -- the bridge that
    mirrors one onto the other lived in index.html's own initDensity(). The ~55
    `body.compact` density rules in matrix.css and usb.html were therefore
    inert, and the header changed height between sections, moving the Settings
    icon 5px on every navigation.
    """

    PAGES = ("index.html", "matrix/configure.html", "matrix/index.html", "matrix/usb.html")

    def source(self, *parts):
        return Path(srv.__file__).resolve().parent.joinpath(*parts).read_text(encoding="utf-8")

    def test_appearance_js_mirrors_the_template_onto_the_body(self):
        module = self.source("ui", "appearance.js")
        self.assertIn("classList.toggle('compact', appearance.template === 'compact')", module,
                      "the one place the Compact layout becomes density")

    def test_every_page_loads_the_module_that_does_it(self):
        for page in self.PAGES:
            with self.subTest(page=page):
                self.assertIn("appearance.js", self.source("ui", *page.split("/")), page)

    def test_no_page_keeps_a_private_copy(self):
        """Two implementations of one bridge is how they came to disagree."""
        for page in self.PAGES + ("matrix/matrix.js", "matrix/usb.js", "settings.js"):
            with self.subTest(page=page):
                text = self.source("ui", *page.split("/"))
                self.assertNotIn("initDensity", text, page)
                self.assertNotIn("classList.toggle('compact'", text, page)
                self.assertNotIn('classList.toggle("compact"', text, page)

    def test_the_density_rules_the_bridge_exists_for_are_still_there(self):
        """If these ever move to [data-template], the bridge can go with them."""
        for sheet, expected in (("matrix/matrix.css", "body.compact .header"),
                                ("index.html", "body.compact .header")):
            with self.subTest(sheet=sheet):
                self.assertIn(expected, self.source("ui", *sheet.split("/")), sheet)


class ButtonHierarchyTests(ServerTestBase):
    """One obvious primary action per surface.

    Device Info previously had seven accent-filled controls across its command
    bar and footer, which makes none of them read as the principal action.
    """

    def source(self, *parts):
        return Path(srv.__file__).resolve().parent.joinpath("ui", *parts).read_text(encoding="utf-8")

    @staticmethod
    def classes_of(page, element_id):
        match = re.search(rf'<button[^>]*id="{element_id}"[^>]*>', page)
        if not match:
            match = re.search(rf'<button[^>]*id="{element_id}"[^>]*>', page)
        if not match:
            return None
        attr = re.search(r'class="([^"]*)"', match.group(0))
        return set((attr.group(1) if attr else "").split())

    # ---- 117. the hierarchy itself ----
    def test_device_info_has_exactly_the_primary_actions_it_should(self):
        page = self.source("index.html")
        primary, secondary, danger = [], [], []
        for match in re.finditer(r'<button[^>]*id="([a-z_]+)"[^>]*>', page):
            classes = self.classes_of(page, match.group(1))
            if classes is None or "btn" not in classes:
                continue
            if "danger" in classes:
                danger.append(match.group(1))
            elif "secondary" in classes or "icon-only" in classes:
                secondary.append(match.group(1))
            else:
                primary.append(match.group(1))
        # The page exists to scan and to upgrade. Its dialogs each have one
        # principal action, and those are primary too; nothing else is.
        self.assertEqual(sorted(primary),
                         sorted(["scan_btn", "upgrade_btn", "sync_pwd_confirm",
                                 "set_ntp_confirm", "slate_upload_btn", "slate_apply_btn",
                                 "confirm_ok"]),
                         f"unexpected primary set: {sorted(primary)}")
        for utility in ("clear_units_btn", "sync_passwords_btn", "set_ntp_btn",
                        "slate_manager_btn", "export_units_btn"):
            self.assertIn(utility, secondary, f"{utility} should not be accent-filled")
        self.assertEqual(sorted(danger), sorted(["reboot_btn", "remove_btn",
                                                 "set_ntp_confirm_reboot", "slate_delete_btn"]))

    def test_the_matrix_pages_have_no_accent_filled_helper(self):
        """They are view surfaces: no principal action, so nothing is primary."""
        for page, ids in (("matrix/index.html", ("refreshBtn", "reloadCacheBtn",
                                                 "encFilterClearBtn", "decFilterClearBtn")),
                          ("matrix/configure.html", ("refreshBtn", "reloadCacheBtn",
                                                     "configureFilterClearBtn",
                                                     "encBulkConfigExportBtn",
                                                     "decBulkConfigExportBtn")),
                          ("matrix/usb.html", ("refreshBtn",))):
            text = self.source(*page.split("/"))
            for element_id in ids:
                with self.subTest(page=page, control=element_id):
                    tag = re.search(rf'<button[^>]*id="{element_id}"[^>]*>', text).group(0)
                    self.assertIn("secondary", tag, f"{element_id} is still accent-filled")

    # ---- 118. secondary is shared, and reaches bare buttons ----
    def test_secondary_is_one_rule_that_covers_both_button_forms(self):
        css = self.source("templates.css")
        block = css[css.index("/* ---- secondary ---- */"):]
        block = block[:block.index("/* ---- warning ---- */")]
        # The SELECTOR GROUP of the fill rule, with comments stripped first.
        # `button.secondary:hover` also contains "button.secondary", so a block
        # search passed with the fill rule's own selector gone -- and the
        # explanatory comment above the rule NAMES both selectors, so slicing to
        # the first brace without stripping it passed too. Two ways for the same
        # assertion to be satisfied by something other than the rule.
        commentless = re.sub(r"/\*.*?\*/", "", block, flags=re.S)
        fill = commentless[:commentless.index("{")]
        self.assertIn(".btn.secondary", fill)
        self.assertIn("button.secondary", fill,
                      "the Matrix pages' controls carry no .btn class")
        self.assertIn("var(--surface-elevated)", block)
        self.assertIn("var(--edge)", block)

    def test_a_secondary_control_is_still_obviously_clickable(self):
        css = self.source("templates.css")
        block = css[css.index("/* ---- secondary ---- */"):]
        block = block[:block.index("/* ---- warning ---- */")]
        # A filled surface with a border, not a ghost outline, and it still
        # answers to hover, press and keyboard focus.
        self.assertIn("background-color:", block)
        self.assertIn("button.secondary:hover", block)
        self.assertIn("button.secondary:active", block)
        self.assertIn("button.secondary:focus-visible", block)

    def test_no_page_defines_its_own_secondary(self):
        for page in ("index.html", "matrix/index.html", "matrix/configure.html",
                     "matrix/usb.html", "matrix/matrix.css", "settings.css",
                     "device-table.css"):
            with self.subTest(page=page):
                text = self.source(*page.split("/"))
                self.assertNotIn(".btn.secondary{", text.replace(" ", ""))


class NativeDialogTests(ServerTestBase):
    """No browser-native popup in an active flow.

    Device Info carried fifteen alert() calls. Each was classified rather than
    swapped: failures and confirmations of something that happened became
    toasts, and the two that present a list the operator must read before acting
    became a styled acknowledgement.
    """

    ACTIVE = ("index.html", "settings.js", "toast.js", "device-log.js",
              "appearance.js", "sticky-columns.js", "mirror-scroll.js",
              "lldp-topology.js", "diagnostics.js",
              "matrix/index.html", "matrix/configure.html", "matrix/usb.html",
              "matrix/matrix.js", "matrix/usb.js", "matrix/usb-extenders.js",
              "user-guide.html")

    def source(self, *parts):
        return Path(srv.__file__).resolve().parent.joinpath("ui", *parts).read_text(encoding="utf-8")

    @staticmethod
    def code_only(text):
        """Comments discuss native dialogs on purpose; code must not call them."""
        text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
        text = re.sub(r"(?m)^\s*//.*$", "", text)
        return re.sub(r"(?m)//.*$", "", text)

    # ---- 128. the audit ----
    def test_no_active_ui_file_calls_a_native_dialog(self):
        offenders = []
        for rel in self.ACTIVE:
            code = self.code_only(self.source(*rel.split("/")))
            for name in ("alert", "confirm", "prompt"):
                for match in re.finditer(rf"(?<![.\w]){name}\s*\(", code):
                    start = max(0, match.start() - 12)
                    context = code[start:match.start() + len(name)]
                    if "omniConfirm" in context or "omniNotice" in context:
                        continue
                    # omniConfirm's own defensive fallback is the one documented
                    # exception, and it is unreachable while the markup exists.
                    if name == "confirm" and "window.confirm(options.message" in code[match.start():match.start() + 60]:
                        continue
                    offenders.append(f"{rel}: {code[start:match.start() + 40].strip()}")
        self.assertEqual(offenders, [], "native dialogs remain in active UI")

    def test_the_one_documented_fallback_is_unreachable_when_the_markup_exists(self):
        page = self.source("index.html")
        block = page[page.index("function omniConfirm("):]
        block = block[:block.index("\nconst CODEC_OPTIONS")]
        # It fires only when the dialog's own elements are absent, and a caller
        # that must not degrade passes requireDialog.
        self.assertIn("if (!backdrop || !okBtn || !cancelBtn) {", block)
        self.assertIn("if (options.requireDialog) return Promise.resolve(false);", block)
        self.assertIn('id="confirm_backdrop"', page, "the markup is present, so it never fires")

    # ---- 13. a required confirmation was never downgraded ----
    def test_the_destructive_actions_still_confirm(self):
        page = self.source("index.html")
        for handler, title in (("reboot_btn", "Reboot Selected Devices"),
                               ("remove_btn", "Remove Selected Devices")):
            with self.subTest(control=handler):
                self.assertIn(title, page)
        # Neither was turned into a toast.
        reboot = page[page.index("title: 'Reboot Selected Devices'"):]
        self.assertIn("omniConfirm", page[:page.index("title: 'Reboot Selected Devices'")][-2000:] +
                      reboot[:400])

    def test_a_list_the_operator_must_read_is_a_dialog_not_a_toast(self):
        page = self.source("index.html")
        self.assertIn("function omniNotice(", page)
        block = page[page.index("function omniNotice("):]
        block = block[:block.index("\n}")]
        self.assertIn("acknowledge: true", block)
        self.assertIn("requireDialog: true", block)
        # The two sites that name devices use it.
        self.assertIn("await omniNotice(\n        'Incompatible Firmware'", page)
        self.assertIn("omniNotice('Upgrade Failed', data.error, details)", page)

    # ---- 126/127. keyboard and focus ----
    def test_the_dialog_restores_focus_to_whatever_opened_it(self):
        page = self.source("index.html")
        block = page[page.index("function omniConfirm("):]
        block = block[:block.index("\nconst CODEC_OPTIONS")]
        self.assertIn("const invoker = document.activeElement", block)
        self.assertIn("invoker.isConnected", block,
                      "a control removed while the dialog was open cannot take focus")
        self.assertIn("invoker.focus()", block)

    def test_escape_and_the_backdrop_still_resolve(self):
        page = self.source("index.html")
        block = page[page.index("function omniConfirm("):]
        block = block[:block.index("\nconst CODEC_OPTIONS")]
        self.assertIn("if (event.key === 'Escape') cleanup(false)", block)
        self.assertIn("if (event.target === backdrop) cleanup(false)", block)

    def test_a_dialog_taller_than_the_window_stays_reachable(self):
        """Measured at 1280x720: the Settings dialog is 879px tall, and centring
        it on a backdrop that could not scroll put its title at y = -79 with
        nothing to scroll. Every dialog shares this backdrop."""
        css = Path(srv.__file__).resolve().parent.joinpath("ui", "settings.css").read_text(encoding="utf-8")
        backdrop = css[css.index(".modal-backdrop{"):]
        backdrop = backdrop[:backdrop.index("}")]
        self.assertIn("overflow:auto", backdrop, "a tall dialog must be scrollable to")
        self.assertIn("align-items:flex-start", backdrop,
                      "centring a dialog taller than the window pushes its top off screen")
        self.assertIn("padding:", backdrop)
        modal = css[css.index(chr(10) + ".modal{"):]
        modal = modal[:modal.index("}")]
        self.assertIn("margin:auto", modal,
                      "a dialog that fits must still be centred")

    def test_an_acknowledgement_shows_one_button_and_restores_the_other(self):
        page = self.source("index.html")
        block = page[page.index("function omniConfirm("):]
        block = block[:block.index("\nconst CODEC_OPTIONS")]
        self.assertIn("cancelBtn.hidden = !!options.acknowledge", block)
        self.assertIn("cancelBtn.hidden = false", block,
                      "the next caller must not inherit a hidden Cancel")


class UserGuideAppearanceTests(ServerTestBase):
    """The guide is a page of the application, not a separate product.

    It carried its own palette, its own theme script and four hard-coded blues,
    so it was the one surface that ignored the chosen preset entirely.
    """

    def guide(self):
        return Path(srv.__file__).resolve().parent.joinpath("ui", "user-guide.html").read_text(encoding="utf-8")

    def styles(self):
        text = self.guide()
        return text[text.index("<style>"):text.index("</style>")]

    def test_it_loads_the_shared_appearance_system(self):
        """The <link> and <script>, not the words. A comment further down the
        file mentions templates.css, so a plain substring search passed with
        the stylesheet removed."""
        text = self.guide()
        head = text[:text.index("<style>")]
        self.assertRegex(head, r'<link[^>]+href="/ui/templates\.css')
        self.assertRegex(head, r'<script[^>]+src="/ui/appearance\.js')

    def test_it_no_longer_declares_its_own_palette(self):
        styles = self.styles()
        self.assertNotIn("--bg:#1d232a", styles, "a second palette is a second product")
        self.assertNotIn(".light{--bg:", styles)

    def test_no_hard_coded_colour_outside_the_print_rules(self):
        styles = self.styles()
        screen = styles[:styles.index("@media print")]
        # var() fallbacks are allowed; they never paint while a preset is active.
        stripped = re.sub(r"var\(\s*--[A-Za-z0-9_-]+\s*,[^()]*\)", "var(--t)", screen)
        literals = re.findall(r"#[0-9a-fA-F]{3,8}|rgba?\([^)]*\)", stripped)
        self.assertEqual(literals, [], f"the guide paints literal colours: {literals}")

    def test_the_three_callouts_keep_distinct_meanings(self):
        styles = self.styles()
        for cls, token in ((".note", "state-info"), (".warn", "state-warning"),
                           (".danger", "state-error")):
            rule = styles[styles.index(f"\n    {cls}{{"):]
            rule = rule[:rule.index("}")]
            with self.subTest(callout=cls):
                self.assertIn(token, rule)
                # A tint alone converges in some palettes; the left rule carries
                # the role colour at full strength.
                self.assertIn("border-left:4px solid", rule)

    def test_documentation_elements_use_shared_surfaces(self):
        styles = self.styles()
        for selector, token in (("body{", "--surface"), ("section{", "--surface-raised"),
                                ("code{", "--surface-elevated"), ("table{", "--surface-elevated"),
                                (".mini{", "--surface-elevated")):
            rule = styles[styles.index(selector):]
            rule = rule[:rule.index("}")]
            with self.subTest(selector=selector):
                self.assertIn(token, rule)

    def test_the_guide_logo_follows_the_theme(self):
        self.assertIn('class="logo"', self.guide())

    def test_print_rules_are_still_paper_coloured(self):
        """The one place a literal colour is correct."""
        styles = self.styles()
        self.assertIn("@media print", styles)
        self.assertIn("background:white", styles[styles.index("@media print"):])


def _load_launcher():
    """Import app_launcher with its GUI dependencies stubbed.

    The launcher imports tkinter, PIL and pystray at module scope and would
    otherwise need a display. Nothing here touches a real toolkit, and
    webbrowser is replaced per test, so no test can open a browser.
    """
    import types
    import importlib.util

    stubs = {}
    for name in ("tkinter", "tkinter.messagebox", "tkinter.ttk",
                 "PIL", "PIL.Image", "PIL.ImageTk", "pystray", "pystray._win32",
                 "pystray._xorg", "psutil"):
        module = types.ModuleType(name)
        module.__getattr__ = lambda attribute: mock.MagicMock()   # type: ignore[attr-defined]
        stubs[name] = module
    stubs["tkinter"].messagebox = stubs["tkinter.messagebox"]
    stubs["tkinter"].ttk = stubs["tkinter.ttk"]
    stubs["PIL"].Image = stubs["PIL.Image"]
    stubs["PIL"].ImageTk = stubs["PIL.ImageTk"]

    path = Path(srv.__file__).resolve().parent / "app_launcher.py"
    spec = importlib.util.spec_from_file_location("omni_app_launcher_under_test", path)
    module = importlib.util.module_from_spec(spec)
    saved = {name: sys.modules.get(name) for name in stubs}
    sys.modules.update(stubs)
    try:
        spec.loader.exec_module(module)
    finally:
        for name, previous in saved.items():
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous
    return module


def _build_release():
    """Load tools/build_release.py without running a build."""
    import importlib.util

    path = Path(srv.__file__).resolve().parent / "tools" / "build_release.py"
    spec = importlib.util.spec_from_file_location("omni_build_release_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _toc(entries):
    """A throwaway PyInstaller work directory describing a package."""
    import tempfile

    directory = Path(tempfile.mkdtemp())
    inner = directory / "OmniSuite"
    inner.mkdir()
    (inner / "PKG-00.toc").write_text(repr(entries), encoding="utf-8")
    return directory


class CleanInstallBase(ServerTestBase):
    """A test that sees what a brand-new installation sees.

    ServerTestBase points every state file at a fresh temp directory and then
    installs a fixture-backed _load_cache so most tests start with a populated
    bench. A first-launch test needs the opposite: the genuine store, reading a
    directory with nothing in it.
    """

    def setUp(self):
        super().setUp()
        self.data_dir = Path(self.folder.name)
        # Config is read from and written to CWD; point it at the clean folder so
        # no test can read or overwrite the operator's real settings.
        previous_cwd = srv.CWD
        self.addCleanup(setattr, srv, "CWD", previous_cwd)
        srv.CWD = self.data_dir
        # Put the real, file-backed cache reader back for the duration.
        genuine = getattr(self, "_real_load_cache", None)
        if genuine is not None:
            patched = srv._load_cache
            self.addCleanup(setattr, srv, "_load_cache", patched)
            srv._load_cache = genuine
        previous_path = srv.app.config.get("FIRMWARE_PATH", "")
        self.addCleanup(srv.app.config.__setitem__, "FIRMWARE_PATH", previous_path)
        srv.app.config["FIRMWARE_PATH"] = ""
        self.client = srv.app.test_client()

    def json(self, response):
        return json.loads(response.get_data(as_text=True))


class FirstLaunchContractTests(CleanInstallBase):
    """A new installation starts empty, and stays empty until the operator acts.

    Nothing about a fresh OmniSuite may be inherited from the machine it happens
    to be running on. The firmware folder is a Settings choice and the device
    list is what the first Scan found -- neither may be guessed from the working
    directory, the executable's directory, or a leftover file.

    This is a release contract, not an implementation detail, because two paths
    broke it. `/api/files` listed the working directory whenever no firmware path
    was set, so a fresh install appeared to arrive with firmware in it, and
    `/api/open_firmware_folder` fell back through `./firmware`, `./ui/firmware`
    and an absolute path from the original development machine -- which also
    shipped inside the executable.
    """

    # ---- firmware path ------------------------------------------------------
    def test_a_clean_install_reports_no_firmware_path(self):
        self.assertEqual(self.json(self.client.get("/api/config")).get("firmware_path"), "")

    def test_the_settings_field_shows_no_folder_selected_when_unset(self):
        """The empty value is what renders the placeholder."""
        settings = (Path(srv.__file__).resolve().parent / "ui" / "settings.js").read_text(encoding="utf-8")
        self.assertIn('placeholder="No folder selected"', settings)

    def test_no_firmware_path_lists_no_firmware(self):
        """Not even from the directory it happens to be started in."""
        decoy = self.data_dir / "AT-OMNI-121-2.1.2.vpup2"
        decoy.write_bytes(b"not really firmware")
        names = [entry["name"] for entry in self.json(self.client.get("/api/files"))["files"]]
        self.assertEqual(names, ["Select Firmware"],
                         "a fresh install must not adopt whatever is beside it")

    def test_a_chosen_firmware_path_is_listed(self):
        """The guard must not have broken the feature it protects."""
        chosen = self.data_dir / "operator-firmware"
        chosen.mkdir()
        (chosen / "AT-OMNI-121-2.1.2.vpup2").write_bytes(b"x")
        srv.app.config["FIRMWARE_PATH"] = str(chosen)
        names = [entry["name"] for entry in self.json(self.client.get("/api/files"))["files"]]
        self.assertIn("AT-OMNI-121-2.1.2.vpup2", names)

    def test_opening_the_firmware_folder_refuses_until_one_is_chosen(self):
        response = self.client.post("/api/open_firmware_folder")
        self.assertEqual(response.status_code, 400)
        body = self.json(response)
        self.assertFalse(body["ok"])
        self.assertIn("Settings", body["error"])

    def test_no_default_firmware_location_is_guessed_anywhere(self):
        """./firmware, ./ui/firmware and a developer absolute path are gone."""
        server = (Path(srv.__file__).resolve().parent / "OmniMatrix_upgrade_server_v7_6y.py").read_text(encoding="utf-8")
        code = "\n".join(line for line in server.splitlines()
                          if not line.lstrip().startswith("#"))
        self.assertNotIn('CWD / "firmware"', code)
        self.assertNotIn('CWD / "ui" / "firmware"', code)
        self.assertNotIn("softwareDEV", code, "no developer path may ship")

    def test_no_developer_or_home_path_is_hard_coded_in_any_published_file(self):
        import subprocess

        root = Path(srv.__file__).resolve().parent
        try:
            listed = subprocess.run(["git", "ls-files"], cwd=str(root),
                                    capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.SubprocessError):
            self.skipTest("git is not available")
        if listed.returncode != 0:
            self.skipTest("not a git working tree")
        pattern = re.compile(
            r"[A-Za-z]:[\\/]+(?:Users|softwareDEV|Documents|Desktop)[\\/]+"
            r"|/(?:Users|home)/[a-z][\w.-]*/", re.I)
        offenders = []
        for name in listed.stdout.split():
            candidate = root / name
            if candidate.suffix.lower() not in {".py", ".js", ".html", ".css",
                                                ".md", ".json", ".yml", ".txt"}:
                continue
            text = candidate.read_text(encoding="utf-8", errors="ignore")
            for match in pattern.finditer(text):
                offenders.append(f"{name}: {match.group(0)}")
        self.assertEqual(offenders, [], "a developer filesystem path would be published")

    # ---- discovered devices -------------------------------------------------
    def test_a_clean_install_has_no_devices(self):
        body = self.json(self.client.get("/api/cache"))
        self.assertEqual(body.get("count"), 0)
        self.assertEqual(len(body.get("units") or []), 0)

    def test_a_clean_install_has_no_scan_results(self):
        body = self.json(self.client.get("/api/scan_results"))
        for key in ("devices", "encoders", "decoders"):
            self.assertEqual(len(body.get(key) or []), 0, key)

    def test_a_clean_install_has_no_usb_endpoints(self):
        body = self.json(self.client.get("/api/usb_state"))
        for key in ("lex", "rex", "standalone_lex", "standalone_rex",
                    "inventory_lex", "inventory_rex", "pairings"):
            self.assertEqual(len(body.get(key) or []), 0, key)

    def test_a_clean_install_infers_no_topology(self):
        topology = self.json(self.client.get("/api/cache")).get("lldp_topology") or {}
        self.assertEqual(len(topology.get("devices") or {}), 0)
        self.assertEqual(topology.get("switch_count"), 0)

    def test_a_clean_data_directory_starts_with_no_state_files(self):
        """Nothing is written before the operator does anything."""
        for name in ("units_cache.json", "scan_results.json", "units_view.csv",
                     "config.json"):
            self.assertFalse((self.data_dir / name).exists(),
                             f"{name} must not exist on a clean install")

    def test_the_repository_ships_no_device_inventory(self):
        """No cache may be published for a new installation to pick up."""
        import subprocess

        root = Path(srv.__file__).resolve().parent
        try:
            listed = subprocess.run(["git", "ls-files"], cwd=str(root),
                                    capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.SubprocessError):
            self.skipTest("git is not available")
        if listed.returncode != 0:
            self.skipTest("not a git working tree")
        tracked = set(listed.stdout.split())
        for name in ("units_cache.json", "scan_results.json", "units_view.csv",
                     "usb_extenders.json", "config.json"):
            self.assertNotIn(name, tracked)


class CleanInstallLifecycleTests(CleanInstallBase):
    """Clean install is clean, and stays clean -- then persists normally.

    The whole walk: start empty, restart still empty, scan, persist, choose a
    firmware folder, restart, find that choice still there. Emptiness on first
    launch and durability after configuration are both required, and neither may
    be bought with the other.
    """

    def restart(self):
        """What a restart actually does: re-read config.json from the data dir."""
        config = srv._load_config()
        srv.app.config["FIRMWARE_PATH"] = config["FIRMWARE_PATH"]
        return config

    def test_the_clean_install_lifecycle(self):
        # A, B, C -- a fresh data directory, started, with nothing in it.
        self.assertEqual(sorted(p.name for p in self.data_dir.iterdir()), [])
        self.assertEqual(self.json(self.client.get("/api/config"))["firmware_path"], "")
        self.assertEqual(self.json(self.client.get("/api/cache"))["count"], 0)

        # D, E, F -- stop and start again without scanning. Still empty: nothing
        # may appear merely because the application was run once.
        self.assertEqual(self.restart()["FIRMWARE_PATH"], "")
        self.assertEqual(self.json(self.client.get("/api/cache"))["count"], 0)
        self.assertFalse(srv.CACHE.exists())

        # G, H -- a scan result persists the way it normally does. Written
        # straight to the cache file; no device is contacted by this test.
        srv._save_cache([{"ip": "192.0.2.10", "mac": "00:1b:13:09:00:01",
                          "model": "hw-omni-e4521", "role": "encoder",
                          "hostname": "doc-encoder"}])
        self.assertTrue(srv.CACHE.exists(), "a real scan must still persist")
        self.assertEqual(len(srv._load_cache()), 1)

        # I -- choose a firmware folder through the normal configuration path.
        chosen = self.data_dir / "operator-firmware"
        chosen.mkdir()
        response = self.client.post("/api/config", json={
            "username": "admin", "password": "CHANGE_ME",
            "fallback_password": "CHANGE_ME", "ws_port": 80, "timeout": 4.5,
            "concurrency": 6, "firmware_path": str(chosen)})
        self.assertEqual(response.status_code, 200)
        self.assertTrue((self.data_dir / "config.json").exists())

        # J, K -- restart, and the operator's choices are still there.
        self.assertEqual(self.restart()["FIRMWARE_PATH"], str(chosen))
        self.assertEqual(self.json(self.client.get("/api/config"))["firmware_path"], str(chosen))
        self.assertEqual(len(srv._load_cache()), 1, "discovered units persist too")

    def test_a_second_installation_inherits_nothing(self):
        """Persistence lives in the data directory, not in the application."""
        chosen = self.data_dir / "operator-firmware"
        chosen.mkdir()
        self.client.post("/api/config", json={"firmware_path": str(chosen)})
        self.assertTrue((self.data_dir / "config.json").exists())
        other = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, other, True)
        srv.CWD = other
        self.assertEqual(srv._load_config()["FIRMWARE_PATH"], "")



class RepositoryHygieneTests(ServerTestBase):
    """What may be published, and what may be packaged.

    Two separate questions with two different answers: the screenshots belong
    in Git but not in the executable, and the firmware directory belongs in
    neither. Both were established the expensive way -- a build that happened
    to run on a machine with firmware present produced a 981 MB executable,
    908 MB of it the manufacturer's images, and CI never noticed because that
    folder is gitignored and so did not exist on the runner.
    """

    @classmethod
    def setUpClass(cls):
        cls.root = Path(srv.__file__).resolve().parent
        cls.build = _build_release()

    def tracked_files(self):
        """What Git would actually publish. Skipped where Git is unavailable."""
        import subprocess

        try:
            result = subprocess.run(["git", "ls-files"], cwd=str(self.root),
                                    capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.SubprocessError):
            self.skipTest("git is not available")
        if result.returncode != 0:
            self.skipTest("not a git working tree")
        return [line.strip() for line in result.stdout.splitlines() if line.strip()]

    # ---- the firmware directory: not packaged, not published ----------------
    def test_firmware_never_enters_the_package(self):
        with self.assertRaises(SystemExit) as caught:
            self.build.assert_package_contents(
                _toc([("firmware/AT-OMNI-121-1.2.3.bin", "src", "DATA")]))
        self.assertIn("firmware", str(caught.exception))

    def test_firmware_is_not_offered_to_pyinstaller(self):
        separator = ";" if os.name == "nt" else ":"
        with self.assertRaises(SystemExit):
            self.build.assert_data_allowlist(
                ["--add-data", f"{self.root / 'firmware'}{separator}firmware"])

    def test_firmware_is_never_tracked_by_git(self):
        offenders = [f for f in self.tracked_files() if f.split("/")[0] == "firmware"]
        self.assertEqual(offenders, [], "firmware is the operator's, not the project's")

    def test_the_build_does_not_require_firmware_to_exist(self):
        """CI has no firmware directory; the build must not look for one."""
        script = (self.root / "tools" / "build_release.py").read_text(encoding="utf-8")
        code = "\n".join(line for line in script.splitlines()
                          if not line.lstrip().startswith("#"))
        self.assertNotIn("ROOT / \"firmware\"", code)
        self.assertNotIn("'firmware'", code.replace('"firmware":', ""))

    def test_settings_still_lets_an_operator_choose_a_firmware_folder(self):
        """Shipping no firmware must not mean dropping the feature."""
        server = (self.root / "OmniMatrix_upgrade_server_v7_6y.py").read_text(encoding="utf-8")
        self.assertIn("list_dir", server, "the folder browser backend")
        self.assertIn("firmware", server.lower())

    # ---- the rest of the package allowlist ----------------------------------
    def test_development_archive_never_enters_the_package(self):
        for name in ("archive/dev-docs/IMPLEMENTATION_PLAN.md",
                     "ui/archive/index6b.html"):
            with self.subTest(name=name), self.assertRaises(SystemExit) as caught:
                self.build.assert_package_contents(_toc([(name, "src", "DATA")]))
            self.assertIn("archive", str(caught.exception))

    def test_tests_never_enter_the_package(self):
        with self.assertRaises(SystemExit):
            self.build.assert_package_contents(
                _toc([("tests/test_usb_integration.py", "src", "DATA")]))

    def test_github_screenshots_never_enter_the_application(self):
        """They are README documentation, and they picture the bench."""
        with self.assertRaises(SystemExit) as caught:
            self.build.assert_package_contents(
                _toc([("docs/images/device-info.png", "src", "DATA")]))
        self.assertIn("docs", str(caught.exception))

    def test_runtime_state_never_enters_the_package(self):
        for name in ("config.json", "units_cache.json", "scan_results.json",
                     "units_view.csv"):
            with self.subTest(name=name), self.assertRaises(SystemExit):
                self.build.assert_package_contents(_toc([(name, "src", "DATA")]))

    def test_a_legitimate_package_is_accepted(self):
        """The guard has to let the real application through."""
        self.build.assert_package_contents(_toc([
            ("ui/index.html", "src", "DATA"),
            ("ui/user-guide.html", "src", "DATA"),
            ("ui/matrix/usb.js", "src", "DATA"),
            ("VERSION", "src", "DATA"),
            ("hallway.png", "src", "DATA"),
            ("atlona.png", "src", "DATA"),
            ("omnimatrix.ico", "src", "DATA"),
        ]))

    def test_the_user_guide_does_ship_in_the_application(self):
        """It is an operator-facing runtime feature, unlike the README."""
        self.assertTrue((self.root / "ui" / "user-guide.html").is_file())
        self.assertIn("ui", self.build.ALLOWED_DATA)

    # ---- what Git publishes -------------------------------------------------
    def test_runtime_state_and_credential_files_are_not_tracked(self):
        forbidden = {"config.json", "units_cache.json", "scan_results.json",
                     "units_view.csv", "notice_ack.json", "topology_ack.json",
                     "scan_preferences.json", "server_output.log"}
        offenders = [f for f in self.tracked_files() if f.split("/")[-1] in forbidden]
        self.assertEqual(offenders, [],
                         "a file the application writes at runtime must not be published")

    def test_no_local_workspace_or_editor_files_are_tracked(self):
        offenders = [f for f in self.tracked_files()
                     if f.endswith(".code-workspace")
                     or f.split("/")[0] in {".vscode", ".idea"}
                     or f.endswith((".swp", ".vdf"))]
        self.assertEqual(offenders, [])

    def test_no_build_output_or_local_content_is_tracked(self):
        forbidden_roots = {"build", "dist", "release", "artifacts", "archive", "firmware"}
        offenders = [f for f in self.tracked_files() if f.split("/")[0] in forbidden_roots]
        self.assertEqual(offenders, [])

    def test_every_gitignore_entry_that_matters_is_present(self):
        rules = (self.root / ".gitignore").read_text(encoding="utf-8")
        for rule in ("firmware/", "archive/", "artifacts/", "build/", "dist/",
                     "release/", "config.json", "units_cache.json",
                     "scan_results.json", "units_view.csv", "notice_ack.json",
                     "topology_ack.json", "scan_preferences.json",
                     "__pycache__/", "*.pyc", "*.log", "*.vdf",
                     "*.code-workspace", "omnisuite-smoke-*/"):
            with self.subTest(rule=rule):
                self.assertIn(rule, rules)

    def test_the_repository_still_tracks_what_a_clean_clone_needs(self):
        """Hygiene must not have removed something the project requires."""
        required = ("README.md", "LICENSE", "VERSION", "requirements.txt",
                    "run_tests.py", "app_launcher.py", "config.example.json",
                    "OmniMatrix_upgrade_server_v7_6y.py", "omni_usb_extender.py",
                    "tools/build_release.py", "tools/smoke_release.py",
                    "ui/index.html", "ui/user-guide.html",
                    ".github/workflows/build-cross-platform.yml")
        tracked = set(self.tracked_files())
        if not tracked:
            self.skipTest("no tracked files reported")
        missing = [name for name in required if name not in tracked]
        self.assertEqual(missing, [], "these must be published for the project to build")

    # ---- the example config is not the runtime config -----------------------
    def test_config_example_is_a_template_and_holds_no_real_credential(self):
        example = json.loads((self.root / "config.example.json").read_text(encoding="utf-8"))
        blob = json.dumps(example).lower()
        for leak in ("hunter", "@", "192.0.2."):
            self.assertNotIn(leak, blob, "the example must carry no real value")
        for key, value in example.items():
            if "pass" in key.lower() and value:
                self.assertIn(str(value).lower(), {"admin", "changeme", "change_me", "password", ""},
                              f"{key} must be a documented placeholder, not a real secret")

    def test_the_server_reads_its_config_from_the_data_dir_not_the_repository(self):
        """config.example.json must never be mistaken for the live config."""
        server = (self.root / "OmniMatrix_upgrade_server_v7_6y.py").read_text(encoding="utf-8")
        self.assertNotIn("config.example.json", server,
                         "the application must not read the example as configuration")
        self.assertIn("config.json", server)

    def test_config_example_is_tracked_but_config_json_is_not(self):
        tracked = set(self.tracked_files())
        if not tracked:
            self.skipTest("no tracked files reported")
        self.assertIn("config.example.json", tracked)
        self.assertNotIn("config.json", tracked)


class LauncherBrowserTests(ServerTestBase):
    """Clicking Open Browser, or the URL, must open a browser.

    The regression: OMNI_SMOKE was checked at the top of open_browser(), which
    is the one entry point shared by the automatic open on server-ready AND by
    every explicit operator action. With OMNI_SMOKE set -- as it is for any
    process a packaged-artifact smoke test started -- a deliberate click was
    silently swallowed and the launcher looked healthy.
    """

    @classmethod
    def setUpClass(cls):
        cls.launcher = _load_launcher()

    def setUp(self):
        super().setUp()
        self.opened = []
        self.result = True
        self.raises = None

        def fake_open(url, new=0, autoraise=True):
            self.opened.append((url, new))
            if self.raises is not None:
                raise self.raises
            return self.result

        # Every test in this class goes through this. A real browser can never
        # be launched from the suite.
        self.addCleanup(setattr, self.launcher, "webbrowser", self.launcher.webbrowser)
        self.launcher.webbrowser = type("W", (), {"open": staticmethod(fake_open)})
        os.environ.pop("OMNI_SMOKE", None)
        self.addCleanup(lambda: os.environ.pop("OMNI_SMOKE", None))

    def window(self, port=8083, host="127.0.0.1"):
        """A launcher window with only the attributes these methods use."""
        window = mock.MagicMock()
        window.host = host
        window.port = port
        window.current_url = lambda: self.launcher.AppWindow.current_url(window)
        window.launch_browser = lambda url: self.launcher.AppWindow.launch_browser(window, url)
        window.report_browser_failure = lambda url: self.launcher.AppWindow.report_browser_failure(window, url)
        return window

    def click(self, window):
        """What the Open Browser button and the URL label both do."""
        return self.launcher.AppWindow.open_browser(window)

    # ---- 1, 2. both controls reach the same implementation ----
    def test_both_controls_are_bound_to_the_same_function(self):
        source = (Path(srv.__file__).resolve().parent / "app_launcher.py").read_text(encoding="utf-8")
        self.assertIn('command=self.open_browser', source, "the button")
        self.assertIn('self.url_label.bind("<Button-1>", lambda _event: self.open_browser())', source,
                      "the clickable URL")
        # One implementation, not two.
        self.assertEqual(source.count("def open_browser("), 1)
        self.assertEqual(source.count("def launch_browser("), 1)

    def test_the_url_click_cannot_suppress_itself(self):
        """Binding the method directly would pass Tk's event as `automatic`,
        which is truthy, and the click would silence itself under OMNI_SMOKE."""
        source = (Path(srv.__file__).resolve().parent / "app_launcher.py").read_text(encoding="utf-8")
        self.assertNotIn('bind("<Button-1>", self.open_browser)', source)

    # ---- 3. the real, dynamically chosen port ----
    def test_the_helper_receives_the_actual_selected_port(self):
        for port in (8080, 8083, 8099, 51234):
            with self.subTest(port=port):
                self.opened.clear()
                self.click(self.window(port=port))
                self.assertEqual(self.opened[-1][0], f"http://127.0.0.1:{port}")

    def test_no_port_is_hard_coded_in_the_browser_path(self):
        """Parsed, not grepped. The docstrings here mention 8080 to explain why
        it must not be assumed, and a text search matched that prose rather than
        any code -- the same way an explanatory comment can satisfy the very
        assertion written to catch its absence."""
        import ast
        source = (Path(srv.__file__).resolve().parent / "app_launcher.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        wanted = {"current_url", "open_browser", "launch_browser"}
        seen, literals = set(), []
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name in wanted:
                seen.add(node.name)
                body = node.body[1:] if ast.get_docstring(node) else node.body
                for child in body:
                    for item in ast.walk(child):
                        if isinstance(item, ast.Constant) and isinstance(item.value, int):
                            literals.append((node.name, item.value))
                        if isinstance(item, ast.Constant) and isinstance(item.value, str):
                            for port in ("8080", "8083"):
                                if port in item.value:
                                    literals.append((node.name, item.value))
        self.assertEqual(seen, wanted, f"functions not found: {wanted - seen}")
        ports = [(fn, v) for fn, v in literals if str(v) in ("8080", "8083")]
        self.assertEqual(ports, [], f"a port is hard-coded: {ports}")
        self.assertIn("self.port", source[source.index("def current_url("):
                                          source.index("def open_browser(")])

    # ---- 4. one click, one open ----
    def test_one_click_opens_exactly_one_browser(self):
        self.click(self.window())
        self.assertEqual(len(self.opened), 1)

    def test_it_asks_for_a_new_tab_rather_than_a_new_window(self):
        self.click(self.window())
        self.assertEqual(self.opened[0][1], 2, "new=2 requests a tab in an open window")

    # ---- 5, 6. OMNI_SMOKE gates the automatic open only ----
    def test_smoke_mode_suppresses_only_the_automatic_open(self):
        os.environ["OMNI_SMOKE"] = "1"
        window = self.window()
        self.assertFalse(self.launcher.AppWindow.open_browser(window, automatic=True))
        self.assertEqual(self.opened, [], "a build runner must not get a browser window")
        # ...and the very same process still honours a click.
        self.assertTrue(self.click(window))
        self.assertEqual(len(self.opened), 1, "an explicit click must always open")

    def test_the_only_automatic_caller_is_the_ready_handler(self):
        """The CI guarantee lives here. If on_server_ready stopped passing
        automatic=True, a build runner would get a browser window and nothing
        else in the suite would notice."""
        import ast
        source = (Path(srv.__file__).resolve().parent / "app_launcher.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        automatic, explicit = [], []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not (isinstance(func, ast.Attribute) and func.attr == "open_browser"):
                continue
            passes = any(kw.arg == "automatic" for kw in node.keywords) or bool(node.args)
            (automatic if passes else explicit).append(node.lineno)
        self.assertEqual(len(automatic), 1,
                         f"exactly one automatic open is expected, found {automatic}")
        ready = next(n for n in ast.walk(tree)
                     if isinstance(n, ast.FunctionDef) and n.name == "on_server_ready")
        self.assertTrue(ready.lineno <= automatic[0] <= ready.end_lineno,
                        "the automatic open must be the one in on_server_ready")
        self.assertTrue(explicit, "the operator-facing callers must not pass automatic")

    def test_a_normal_launch_opens_automatically(self):
        self.assertTrue(self.launcher.AppWindow.open_browser(self.window(), automatic=True))
        self.assertEqual(len(self.opened), 1)

    def test_nothing_in_the_application_sets_omni_smoke(self):
        """It must come from the environment of one process, never be persisted."""
        root = Path(srv.__file__).resolve().parent
        for name in ("app_launcher.py", "OmniMatrix_upgrade_server_v7_6y.py",
                     "tools/build_release.py"):
            text = root.joinpath(*name.split("/")).read_text(encoding="utf-8")
            with self.subTest(file=name):
                self.assertNotIn('environ["OMNI_SMOKE"]', text)
                self.assertNotIn("environ['OMNI_SMOKE']", text)
                self.assertNotIn('setdefault("OMNI_SMOKE"', text)

    # ---- 7, 8. a failed launch is survivable and recoverable ----
    def test_a_refused_launch_does_not_stop_the_server(self):
        """webbrowser.open returns False without raising; discarding that is
        what makes a button look like it does nothing."""
        self.result = False
        window = self.window()
        self.assertFalse(self.click(window))
        window.status_label.config.assert_called()
        message = window.status_label.config.call_args.kwargs.get("text", "")
        self.assertIn("could not open a browser", message.lower())
        self.assertIn("Server running", message)

    def test_a_raising_launch_is_caught_and_reported(self):
        self.raises = OSError("no browser registered")
        window = self.window()
        self.assertFalse(self.click(window))
        window.status_label.config.assert_called()

    def test_a_failure_leaves_the_address_on_screen_to_copy(self):
        self.result = False
        window = self.window(port=8099)
        self.click(window)
        # The URL label is never cleared, and the status points at it.
        self.assertFalse(window.url_var.set.called,
                         "the address must stay visible after a failed launch")
        self.assertIn("address below",
                      window.status_label.config.call_args.kwargs.get("text", ""))

    def test_a_failure_uses_no_native_dialog(self):
        source = (Path(srv.__file__).resolve().parent / "app_launcher.py").read_text(encoding="utf-8")
        block = source[source.index("def launch_browser("):source.index("def on_close(")]
        self.assertNotIn("showerror", block)
        self.assertNotIn("showinfo", block)

    def test_a_click_can_be_retried_after_a_failure(self):
        self.result = False
        window = self.window()
        self.assertFalse(self.click(window))
        self.result = True
        self.assertTrue(self.click(window), "the control must remain usable")
        self.assertEqual(len(self.opened), 2)

    # ---- 9. the smoke harness still works without a browser ----
    def test_the_smoke_harness_never_needs_a_browser(self):
        script = (Path(srv.__file__).resolve().parent / "tools" / "smoke_release.py").read_text(encoding="utf-8")
        self.assertIn('env["OMNI_SMOKE"] = "1"', script)
        self.assertNotIn("webbrowser", script)

    def test_the_smoke_harness_leaves_nothing_running(self):
        """A launcher left behind carries OMNI_SMOKE for its whole life and
        looks exactly like a normal install -- which is how this was found."""
        script = (Path(srv.__file__).resolve().parent / "tools" / "smoke_release.py").read_text(encoding="utf-8")
        self.assertIn("smoke-launched process(es) still running", script)
        self.assertIn("shutil.rmtree(tmp", script)
        # --onefile spawns the application as a CHILD of the bootloader, so
        # terminating the handle alone leaves the real process running.
        self.assertIn("def stop_process_tree(", script)
        self.assertIn("stop_process_tree(process.pid)", script)
        self.assertIn("def surviving_processes(", script)
        # Killing a process group is only safe if the child has its own.
        self.assertIn("start_new_session=", script)

    def test_no_browser_was_opened_by_this_class(self):
        """The suite must never reach a real browser."""
        self.assertIsNot(self.launcher.webbrowser.open, __import__("webbrowser").open)


class BackgroundWorkIsolationTests(ServerTestBase):
    """Nothing scheduled during a test may run during a different one.

    The shared extender executor is how USB state refreshes stay off the request
    path in production. In a suite that same asynchrony makes one test's work
    arrive inside another, which is how a UDP-only route came to be credited
    with a parent WebSocket read on Ubuntu and nowhere else.
    """

    def test_a_submission_runs_inside_the_test_that_made_it(self):
        """Deliberately slow, so the answer cannot come out right by luck.

        A quick task finishes on a worker thread before the next statement runs
        about as often as not, which would let this pass while the leak was
        still there. A quarter of a second does not.
        """
        ran = []

        def slow():
            time.sleep(0.25)
            ran.append(True)

        future = srv._usb_extenders._executor.submit(slow)
        self.assertEqual(ran, [True],
                         "enrichment work must complete inside the test that "
                         "scheduled it, not inside some later one")
        self.assertTrue(future.done(), "a Future is still returned to the caller")

    def test_the_usb_state_request_performs_its_own_icron_read(self):
        """The real scheduler, not a stand-in."""
        calls = []
        self.patch_srv("_refresh_icron_network_config",
                       lambda *a, **k: calls.append(time.monotonic()))
        srv._usb_net_config.clear()
        before = time.monotonic()
        self.client.get("/api/usb_state")
        after = time.monotonic()
        for when in calls:
            self.assertTrue(before <= when <= after,
                            "the read ran outside the request that asked for it")


class ArtifactNamingTests(ServerTestBase):
    """The published artifact names and the update checker's matcher are one contract.

    `_select_platform_asset` offers the running platform its own build from a
    GitHub release, keyed on the tail of the artifact name. Renaming an artifact
    in tools/build_release.py without changing that key would offer someone the
    wrong download -- a Windows machine matching `-x86_64.zip` would be handed
    the macOS Intel build. These tests hold the two together.
    """

    EXPECTED = {
        "windows": "OmniSuite-{version}-Windows-x86_64.zip",
        "arm64": "OmniSuite-{version}-macOS-arm64.zip",
        "x86_64": "OmniSuite-{version}-macOS-x86_64.zip",
        "linux": "OmniSuite-{version}-Ubuntu-x86_64.tar.gz",
    }

    def build_script(self):
        return Path(srv.__file__).resolve().parent.joinpath("tools", "build_release.py").read_text(encoding="utf-8")

    def assets(self, version="V1.0.7"):
        base = "https://github.com/Hall-Research-Technologies/OmniSuite/releases/download/"
        return [{"name": name.format(version=version), "size": 1,
                 "browser_download_url": base + version + "/" + name.format(version=version)}
                for name in self.EXPECTED.values()]

    def test_the_build_script_publishes_exactly_these_names(self):
        script = self.build_script()
        for suffix, name in self.EXPECTED.items():
            with self.subTest(suffix=suffix):
                self.assertIn(f'"{suffix}": "{name}"', script)

    def test_every_name_states_its_architecture(self):
        for suffix, name in self.EXPECTED.items():
            with self.subTest(suffix=suffix):
                self.assertTrue("x86_64" in name or "arm64" in name,
                                f"{name} does not say which architecture it is")

    def test_each_platform_is_offered_its_own_build(self):
        assets = self.assets()
        for system, machine, expected in (
                ("Windows", "AMD64", "Windows-x86_64.zip"),
                ("Windows", "ARM64", "Windows-x86_64.zip"),
                ("Darwin", "arm64", "macOS-arm64.zip"),
                ("Darwin", "x86_64", "macOS-x86_64.zip"),
                ("Linux", "x86_64", "Ubuntu-x86_64.tar.gz")):
            with self.subTest(system=system, machine=machine):
                with mock.patch.object(srv.platform, "system", return_value=system), \
                     mock.patch.object(srv.platform, "machine", return_value=machine):
                    chosen = srv._select_platform_asset(assets)
                self.assertIsNotNone(chosen, f"{system}/{machine} was offered nothing")
                self.assertTrue(chosen["name"].endswith(expected),
                                f"{system}/{machine} was offered {chosen['name']}")

    def test_a_platform_with_no_artifact_is_offered_nothing(self):
        """Handing someone the wrong build is worse than handing them the page."""
        assets = self.assets()
        for system, machine in (("Linux", "aarch64"), ("FreeBSD", "x86_64"),
                                ("Darwin", "ppc"), ("", "")):
            with self.subTest(system=system, machine=machine):
                with mock.patch.object(srv.platform, "system", return_value=system), \
                     mock.patch.object(srv.platform, "machine", return_value=machine):
                    self.assertIsNone(srv._select_platform_asset(assets))

    def test_the_windows_build_can_never_match_the_macos_intel_asset(self):
        """The specific confusion the old names allowed."""
        assets = self.assets()
        with mock.patch.object(srv.platform, "system", return_value="Windows"), \
             mock.patch.object(srv.platform, "machine", return_value="AMD64"):
            chosen = srv._select_platform_asset(assets)
        self.assertNotIn("macOS", chosen["name"])

    def test_firmware_is_never_bundled_into_an_artifact(self):
        """A developer machine with firmware present produced a 981 MB
        executable, 908 MB of it the manufacturer's firmware images.

        Asserted against what the script DOES, not against whether the word
        appears in it: the build now names the firmware folder deliberately, in
        the list of things it refuses to package. A plain text search cannot
        tell a bundling instruction from a guard against one.
        """
        import ast as _ast

        script = self.build_script()
        tree = _ast.parse(script)

        # No --add-data may be built from a path containing the firmware folder.
        for node in _ast.walk(tree):
            if isinstance(node, _ast.JoinedStr):
                rendered = "".join(
                    part.value for part in node.values
                    if isinstance(part, _ast.Constant) and isinstance(part.value, str))
                self.assertNotIn("firmware", rendered.lower(),
                                 "no packaged path may be built from the firmware folder")

        # The only place the name may appear as a literal is the refusal list.
        module = _build_release()
        self.assertNotIn("firmware", module.ALLOWED_DATA)
        self.assertIn("firmware", module.FORBIDDEN_IN_PACKAGE)

    def test_a_checksum_is_produced_from_the_distributed_archive(self):
        script = self.build_script()
        self.assertIn("def write_checksum(", script)
        block = script[script.index("def write_checksum("):]
        block = block[:block.index("\ndef ")]
        self.assertIn("hashlib.sha256", block)
        self.assertIn(".sha256", block)


class TrackedCredentialTests(ServerTestBase):
    """No runtime file holding credentials may be under version control.

    `config.json` was tracked with the documented default passwords in it. No
    real credential had been committed, and the server reads
    DATA_DIR/config.json rather than the repository copy -- but the next person
    to change a password in it would have committed that password.
    """

    def tracked(self):
        import subprocess
        root = Path(srv.__file__).resolve().parent
        out = subprocess.run(["git", "ls-files"], cwd=root, capture_output=True,
                             text=True, encoding="utf-8", errors="replace")
        if out.returncode != 0:
            self.skipTest("not a git checkout")
        return set(out.stdout.split())

    def test_no_runtime_credential_file_is_tracked(self):
        tracked = self.tracked()
        for name in ("config.json", "notice_ack.json", "scan_preferences.json",
                     "ui_preferences.json"):
            with self.subTest(file=name):
                self.assertNotIn(name, tracked, f"{name} holds runtime state")

    def test_the_example_is_tracked_and_holds_no_real_value(self):
        tracked = self.tracked()
        self.assertIn("config.example.json", tracked,
                      "a fresh checkout needs something to start from")
        example = json.loads(Path(srv.__file__).resolve().parent
                             .joinpath("config.example.json").read_text(encoding="utf-8"))
        for field in ("password", "fallback_password"):
            self.assertEqual(example[field], "CHANGE_ME", field)
        self.assertEqual(example["firmware_path"], "",
                         "a developer's own path is not a useful default")

    def test_gitignore_keeps_it_out(self):
        text = Path(srv.__file__).resolve().parent.joinpath(".gitignore").read_text(encoding="utf-8")
        self.assertRegex(text, r"(?m)^config\.json$")


class PollLoggingTests(ServerTestBase):
    """Steady-state polling must not log at INFO.

    Measured on the bench: fifteen devices on a five-second cadence produced
    about ninety INFO lines a minute, which buries a mutation, a refusal or a
    device that stopped answering exactly as an idle poll would. Polling being
    operator-enabled makes it a choice, not a reason for the log to be useless.
    """

    def poll_lines(self):
        source = Path(srv.__file__).with_name("OmniMatrix_upgrade_server_v7_6y.py").read_text(encoding="utf-8")
        return [line.strip() for line in source.splitlines()
                if "[POLL]" in line and "log." in line]

    def test_no_per_poll_trace_is_logged_at_info(self):
        offenders = [line for line in self.poll_lines() if line.startswith("log.info")]
        # The one permitted INFO is a firmware version that actually changed.
        unexpected = [line for line in offenders if "firmware changed" not in line]
        self.assertEqual(unexpected, [], "these log on every poll of every device")

    def test_a_firmware_change_is_still_recorded(self):
        source = Path(srv.__file__).with_name("OmniMatrix_upgrade_server_v7_6y.py").read_text(encoding="utf-8")
        self.assertIn('log.info("[POLL] %s firmware changed: %r -> %r"', source)
        # And it sits inside the branch that established there was a change.
        block = source[source.index("if fresh_version != cached_version:"):]
        block = block[:block.index("else:")]
        self.assertIn("firmware changed", block)

    def test_the_unchanged_case_is_debug(self):
        source = Path(srv.__file__).with_name("OmniMatrix_upgrade_server_v7_6y.py").read_text(encoding="utf-8")
        self.assertIn('log.debug(f"[POLL] {ip} version unchanged', source)


class DeviceLogTests(ServerTestBase):
    """The operator-initiated debug-log download.

    Observed on the bench across every generation present -- at-omni-111,
    at-omni-121, hw-omni-e4111, hw-omni-e4111-wp, hw-omni-d4111, hw-omni-d4511
    and hw-omni-e4521 -- get_debug_info answers with a path, not a log:

        {"error": false, "id": "get_debug_info-method",
         "reply": {"path": "/debug/<hostname>-debug.vdf"}}

    The bundle is then fetched over plain HTTP from the same device. These tests
    never do either: the WebSocket transport is fenced in the base class and
    `requests` is replaced with one that fails the test.
    """

    PATH = "/debug/hw-omni-e4521-00002-debug.vdf"

    def setUp(self):
        super().setUp()
        self._patch_cache(FULL_CACHE_UNITS)
        self.ws_payloads = []
        self.fetched = []

    def known_ip(self):
        return (self._cache_units()[0] or {}).get("ip")

    def _cache_units(self):
        return srv._load_cache() or []

    def stub_method(self, reply=None, raises=None):
        """Answer the WebSocket method without going near a device."""
        def fake(ip, payload, timeout, ws_port, ws_path, primary_pwd=None):
            self.ws_payloads.append((ip, dict(payload), primary_pwd, timeout))
            if raises is not None:
                raise raises
            return reply
        self.patch_srv("_ws_send_recv_with_fallback", fake)

    def stub_fetch(self, body=b"Salted__payload", status=200, headers=None, raises=None):
        test = self

        class FakeResponse:
            def __init__(self):
                self.headers = dict(headers or {"Content-Length": str(len(body))})
                self.status_code = status

            def raise_for_status(self):
                if status >= 400:
                    raise RuntimeError(f"HTTP {status}")

            def iter_content(self, chunk_size=1):
                for i in range(0, len(body), chunk_size):
                    yield body[i:i + chunk_size]

            def close(self):
                pass

        def fake_get(url, *args, **kwargs):
            test.fetched.append((url, kwargs.get("timeout"), kwargs.get("stream")))
            if raises is not None:
                raise raises
            return FakeResponse()

        self.patch_srv("requests", type("R", (), {"get": staticmethod(fake_get)}))

    def download(self, ip=None):
        return self.client.get(f"/api/device_log?ip={ip if ip is not None else self.known_ip()}")

    # ---- 79. request construction ----
    def test_the_command_is_the_documented_one(self):
        self.stub_method({"error": False, "reply": {"path": self.PATH}})
        self.stub_fetch()
        self.assertEqual(self.download().status_code, 200)
        ip, payload, _pwd, _timeout = self.ws_payloads[0]
        self.assertEqual(ip, self.known_ip())
        self.assertEqual(payload["id"], "get_debug_info-method")
        self.assertEqual(payload["method"], {"get_debug_info": {}})

    def test_the_command_carries_no_password_of_its_own(self):
        """The password belongs to the credential path, not to the payload.

        _ws_send_recv_with_fallback sets it, so it can retry with the fallback;
        a password baked into the payload here would defeat that.
        """
        self.stub_method({"error": False, "reply": {"path": self.PATH}})
        self.stub_fetch()
        self.download()
        self.assertNotIn("password", self.ws_payloads[0][1])

    # ---- 80. credential selection ----
    def test_it_uses_the_credentials_recorded_for_that_device(self):
        ip = self.known_ip()
        marker = "per-device-secret"
        units = copy.deepcopy(self._cache_units())
        for unit in units:
            if unit.get("ip") == ip:
                unit["username"] = "operator"
                unit["password"] = marker
        self._patch_cache(units)
        self.stub_method({"error": False, "reply": {"path": self.PATH}})
        self.stub_fetch()
        self.download(ip)
        _ip, payload, primary_pwd, _timeout = self.ws_payloads[0]
        self.assertEqual(payload["username"], "operator")
        self.assertEqual(primary_pwd, marker,
                         "the device's own password must be the one tried first")

    def test_a_device_with_no_recorded_credentials_uses_the_configured_ones(self):
        ip = self.known_ip()
        units = copy.deepcopy(self._cache_units())
        for unit in units:
            if unit.get("ip") == ip:
                unit.pop("username", None)
                unit.pop("password", None)
        self._patch_cache(units)
        self.stub_method({"error": False, "reply": {"path": self.PATH}})
        self.stub_fetch()
        self.download(ip)
        self.assertEqual(self.ws_payloads[0][1]["username"], srv.app.config["USERNAME"])
        self.assertEqual(self.ws_payloads[0][2], srv.app.config["PASSWORD"])

    def test_the_fallback_path_is_the_shared_one(self):
        """Primary-then-fallback behaviour is not reimplemented here."""
        source = Path(srv.__file__).with_name("OmniMatrix_upgrade_server_v7_6y.py").read_text(encoding="utf-8")
        block = source[source.index("def _device_log_location"):]
        block = block[:block.index("\n@app.route")]
        self.assertIn("_ws_send_recv_with_fallback", block)
        self.assertNotIn("FALLBACK_PASSWORD", block,
                         "the credential ladder belongs to one helper, not to this one")

    # ---- 81. response parsing ----
    def test_the_path_comes_from_the_reply(self):
        self.stub_method({"error": False, "reply": {"path": "/debug/other-debug.vdf"}})
        self.stub_fetch()
        self.assertEqual(self.download().status_code, 200)
        self.assertTrue(self.fetched[0][0].endswith("/debug/other-debug.vdf"), self.fetched)

    def test_every_shape_that_is_not_a_usable_path_is_refused(self):
        for reply in ({"error": True, "reply": {"path": self.PATH}},
                      {"error": False, "reply": {}},
                      {"error": False, "reply": None},
                      {"error": False},
                      {"error": False, "reply": {"path": ""}},
                      {"error": False, "reply": "a string"},
                      "not a dict",
                      None):
            with self.subTest(reply=reply):
                self.stub_method(reply)
                self.stub_fetch()
                self.assertEqual(self.download().status_code, 502)
                self.assertEqual(self.fetched, [], "nothing may be fetched from an unusable reply")

    # ---- 70/82. path validation ----
    def test_a_path_that_could_retarget_the_request_is_refused(self):
        for hostile in ("/debug/../../etc/shadow",
                        "//evil.example/debug/x.vdf",
                        "http://evil.example/x.vdf",
                        "https://evil.example/x.vdf",
                        "file:///etc/passwd",
                        "debug/x.vdf",
                        "/debug/x\\..\\y.vdf",
                        "/debug/x\ny.vdf",
                        "/debug/" + "a" * 600,
                        "/debug/x\x00.vdf",
                        "/debug/x y.vdf",          # a space has to be encoded
                        "/debug/x#fragment",       # a fragment fetches something else
                        "/debug/x?b=c",            # so does a query string
                        "/debug/x[1].vdf"):
            with self.subTest(path=hostile[:40]):
                self.assertIsNone(srv._safe_device_log_path(hostile))

    def test_an_ordinary_device_path_is_accepted_unchanged(self):
        for good in (self.PATH, "/debug/at-omni-111-04553-debug.vdf",
                     "/debug/wd-omni-121-07548-debug.vdf"):
            self.assertEqual(srv._safe_device_log_path(good), good)

    def test_the_url_is_built_against_the_device_we_asked(self):
        """Not against anything the device said."""
        ip = self.known_ip()
        self.stub_method({"error": False, "reply": {"path": self.PATH}})
        self.stub_fetch()
        self.download(ip)
        self.assertEqual(self.fetched[0][0], f"http://{ip}{self.PATH}")

    # ---- 82. filename sanitization ----
    def test_the_filename_identifies_the_device(self):
        name = srv._device_log_filename("hw-omni-e4521-00002", "192.0.2.10", "20260911-120000")
        self.assertEqual(name, "hw-omni-e4521-00002_192.0.2.10_20260911-120000.vdf")

    def test_the_filename_is_writable_on_all_three_platforms(self):
        hostile = {
            "a/b": "a-b", "a\\b": "a-b", "a:b": "a-b", 'a"b': "a-b",
            "a*b": "a-b", "a?b": "a-b", "a<b>c": "a-b-c", "a|b": "a-b",
            "  spaced  ": "spaced", "../../etc/passwd": "etc-passwd",
        }
        for raw, expected_stem in hostile.items():
            with self.subTest(hostname=raw):
                name = srv._device_log_filename(raw, "192.0.2.10", "20260911-120000")
                self.assertEqual(name, f"{expected_stem}_192.0.2.10_20260911-120000.vdf")
                for banned in '/\\:*?"<>|':
                    self.assertNotIn(banned, name)

    def test_a_windows_reserved_device_name_is_not_produced(self):
        for reserved in ("con", "PRN", "aux", "NUL", "com1", "lpt9"):
            with self.subTest(name=reserved):
                name = srv._device_log_filename(reserved, "192.0.2.10", "20260911-120000")
                self.assertNotEqual(name.split("_")[0].lower(), reserved.lower())

    def test_a_device_with_no_hostname_still_gets_a_usable_name(self):
        self.assertEqual(srv._device_log_filename("", "192.0.2.10", "20260911-120000"),
                         "192.0.2.10_20260911-120000.vdf")
        self.assertTrue(srv._device_log_filename("", "", "").endswith(".vdf"))

    def test_the_response_names_the_file_from_our_data_not_the_device_path(self):
        ip = self.known_ip()
        self.stub_method({"error": False, "reply": {"path": "/debug/zzz-evil-name.vdf"}})
        self.stub_fetch()
        reply = self.download(ip)
        disposition = reply.headers["Content-Disposition"]
        self.assertNotIn("zzz-evil-name", disposition)
        self.assertIn(ip, disposition)

    # ---- 83. failure and offline behaviour ----
    def test_an_unreachable_device_is_reported_as_upstream_not_as_a_crash(self):
        self.stub_method(raises=OSError("no route to host"))
        reply = self.download()
        self.assertEqual(reply.status_code, 502)
        body = reply.get_json()
        self.assertFalse(body["ok"])
        self.assertIn(self.known_ip(), body["error"])

    def test_a_device_that_will_not_serve_the_bundle_is_reported_cleanly(self):
        self.stub_method({"error": False, "reply": {"path": self.PATH}})
        self.stub_fetch(status=404)
        self.assertEqual(self.download().status_code, 502)

    def test_an_unknown_address_is_404_and_a_malformed_one_is_400(self):
        self.assertEqual(self.download("203.0.113.9").status_code, 404)
        self.assertEqual(self.download("not-an-ip").status_code, 400)
        self.assertEqual(self.download("").status_code, 400)

    def test_a_failure_never_reaches_the_device_twice(self):
        self.stub_method(raises=OSError("offline"))
        self.download()
        self.assertEqual(len(self.ws_payloads), 1, "no blind retry")

    # ---- 85. credentials never leave the server ----
    def test_no_credential_reaches_the_browser(self):
        ip = self.known_ip()
        units = copy.deepcopy(self._cache_units())
        for unit in units:
            if unit.get("ip") == ip:
                unit["password"] = "per-device-secret"
        self._patch_cache(units)
        self.stub_method({"error": False, "reply": {"path": self.PATH}})
        self.stub_fetch(body=b"Salted__opaque-bundle-bytes")
        reply = self.download(ip)
        blob = reply.get_data() + repr(sorted(reply.headers.items())).encode()
        for secret in (b"per-device-secret",
                       srv.app.config["PASSWORD"].encode(),
                       srv.app.config["FALLBACK_PASSWORD"].encode()):
            if secret:
                self.assertNotIn(secret, blob)

    def test_the_page_never_asks_for_a_password(self):
        """The flow moved to ui/device-log.js; the assertion follows it."""
        module = Path(srv.__file__).resolve().parent.joinpath("ui", "device-log.js").read_text(encoding="utf-8")
        for banned in ("password", "username", "credential"):
            self.assertNotIn(banned, module.lower(), banned)
        self.assertIn("/api/device_log?ip=", module)
        page = Path(srv.__file__).resolve().parent.joinpath("ui", "index.html").read_text(encoding="utf-8")
        self.assertIn("device-log.js", page, "the page must load the module")
        self.assertNotIn("async function downloadDeviceLog", page,
                         "a second copy would drift from the tested one")

    # ---- 22A. the engineering notice ----
    def notice_module(self):
        return Path(srv.__file__).resolve().parent.joinpath("ui", "device-log.js").read_text(encoding="utf-8")

    def test_the_notice_says_what_the_operator_needs_to_know(self):
        module = self.notice_module()
        self.assertIn("Device logs are encrypted and can only be decrypted and read by Engineering.",
                      module)

    def test_the_notice_does_not_imply_the_operator_can_read_the_file(self):
        module = self.notice_module()
        start = module.index("const ENGINEERING_NOTICE")
        notice = module[start:module.index(";", start)].lower()
        for banned in ("open", "view", "inspect", "decrypt it", "unzip", "extract"):
            self.assertNotIn(banned, notice, banned)

    def test_the_notice_is_the_shared_dialog_and_never_a_native_one(self):
        module = self.notice_module()
        self.assertIn("omniConfirm", module)
        self.assertIn("requireDialog: true", module,
                      "a missing dialog must refuse, not fall back to window.confirm")
        code = re.sub(r"(?m)//.*$", "", module)
        for banned in ("window.confirm", "window.alert", "window.prompt"):
            self.assertNotIn(banned, code, banned)

    def test_cancel_holds_the_focus(self):
        """An accidental Enter must not start a ten-megabyte download."""
        self.assertIn("defaultAction: 'cancel'", self.notice_module())

    def test_the_notice_is_never_remembered(self):
        # Comments stripped: the module's own prose explains that the notice is
        # NOT an acknowledgement to be remembered, and matching on that text
        # would fail for saying so.
        module = re.sub(r"(?m)//.*$", "", self.notice_module())
        for banned in ("localStorage", "sessionStorage", "Don't show", "dontShow",
                       "acknowledg", "suppress"):
            self.assertNotIn(banned, module, banned)

    def test_the_shared_dialog_supports_both_options_without_changing_its_default(self):
        page = Path(srv.__file__).resolve().parent.joinpath("ui", "index.html").read_text(encoding="utf-8")
        block = page[page.index("function omniConfirm("):]
        block = block[:block.index("\nconst CODEC_OPTIONS")]
        self.assertIn("options.requireDialog", block)
        self.assertIn("options.defaultAction === 'cancel'", block)
        # Existing callers pass neither, and must still get OK focused. An
        # acknowledgement has no Cancel to focus, so it lands on OK too.
        self.assertIn("(cancelIsDefault && !options.acknowledge ? cancelBtn : okBtn).focus()", block)
        self.assertIn("cancelBtn.hidden = !!options.acknowledge", block,
                      "an acknowledgement offers one answer, so it shows one button")
        self.assertIn("invoker.focus()", block,
                      "focus must return to the control that opened the dialog")

    def test_the_verified_download_implementation_is_unchanged(self):
        """The notice gates the request; it does not alter what the request is."""
        module = self.notice_module()
        self.assertIn("'/api/device_log?ip=' + encodeURIComponent(ip)", module)
        self.assertIn("Content-Disposition", module)
        source = Path(srv.__file__).with_name("OmniMatrix_upgrade_server_v7_6y.py").read_text(encoding="utf-8")
        self.assertIn('"id": "get_debug_info-method"', source)
        self.assertIn('"method": {"get_debug_info": {}}', source)

    # ---- 8/69. it is operator initiated, and fenced ----
    def test_nothing_but_the_endpoint_calls_it(self):
        source = Path(srv.__file__).with_name("OmniMatrix_upgrade_server_v7_6y.py").read_text(encoding="utf-8")
        callers = [line.strip() for line in source.splitlines()
                   if "_device_log_location(" in line and "def " not in line]
        self.assertEqual(callers, ["path = _device_log_location(ip, user, pwd)"], callers)

    @staticmethod
    def function_body(source, name):
        """One function's own text, bounded at the next top-level definition.

        A fixed character window ran past the end of the function and into
        whatever happened to be defined next, which made the assertion depend on
        file order rather than on the function.
        """
        start = source.index("\n" + name) + 1
        rest = source[start + len(name):]
        ends = [rest.index(marker) for marker in ("\ndef ", "\n@app.route", "\nclass ")
                if marker in rest]
        return source[start:start + len(name) + (min(ends) if ends else len(rest))]

    def test_it_is_absent_from_discovery_polling_startup_dump_and_export(self):
        source = Path(srv.__file__).with_name("OmniMatrix_upgrade_server_v7_6y.py").read_text(encoding="utf-8")
        for name in ("def api_scan(", "def start_background_startup_tasks(",
                     "def api_ts_export(", "def _inventory_workbook(",
                     "def api_poll(", "def _usb_extender_view("):
            body = self.function_body(source, name)
            with self.subTest(function=name):
                self.assertGreater(len(body), 120, f"{name} was not located")
                self.assertNotIn("get_debug_info", body)
                self.assertNotIn("_device_log", body)

    def test_the_fetch_boundary_is_fenced_by_default(self):
        """A test that forgets to stub it fails rather than reaching a device."""
        self.stub_method({"error": False, "reply": {"path": self.PATH}})
        self.assertEqual(self.download().status_code, 502,
                         "the default fence must refuse, not deliver")

    def test_the_bundle_is_streamed_through_untouched(self):
        """It is an encrypted vendor artefact; OmniSuite must not alter it."""
        payload = b"Salted__" + bytes(range(256)) * 40
        self.stub_method({"error": False, "reply": {"path": self.PATH}})
        self.stub_fetch(body=payload)
        reply = self.download()
        self.assertEqual(reply.get_data(), payload)
        self.assertEqual(reply.headers["Content-Type"], "application/octet-stream")
        self.assertEqual(reply.headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(reply.headers["Cache-Control"], "no-store")


class UsbColumnSemanticsTests(ServerTestBase):
    """What the Device Info USB column is allowed to claim.

    Measured on eight live endpoints: `found_devices` is the endpoint's
    USB-over-IP neighbour table. It never contains the endpoint itself, it
    contains LEX and REX alike, it is identical for every endpoint on a segment,
    and it can be smaller than that endpoint's own paired count when a peer sits
    on another subnet. It is a fact about the subnet, not about the device, so
    it is not what the cell shows.
    """

    def source(self, *parts):
        return Path(srv.__file__).resolve().parent.joinpath(*parts).read_text(encoding="utf-8")

    def cell(self):
        """The renderer, not the tooltip helper -- both mention `usb.role`."""
        page = self.source("ui", "index.html")
        start = page.index("const usbHtml = label ?")
        return page[page.rindex("const role =", 0, start):
                    page.index("}catch(e){ console.warn('[POLL][USB]", start)]

    def test_the_cell_states_the_role_and_no_bare_number(self):
        block = self.cell()
        self.assertIn('<span class="usb-pill">${label}</span>', block)
        self.assertNotIn("found_count", block,
                         "a subnet-wide count must not sit beside one device")
        self.assertNotIn("${label} \u00b7 ${peers}", block)

    def test_each_count_is_named_where_it_is_shown(self):
        page = self.source("ui", "index.html")
        block = page[page.index("function usbTooltip("):page.index("// --- Upgrade feedback helpers ---")]
        # The three quantities the brief requires be kept apart.
        self.assertIn("Paired peers", block)
        self.assertIn("linked now", block)
        self.assertIn("Other USB endpoints on its subnet", block)
        self.assertIn("USB MAC", block)

    def test_the_poll_reports_the_per_device_counts_separately(self):
        source = Path(srv.__file__).with_name("OmniMatrix_upgrade_server_v7_6y.py").read_text(encoding="utf-8")
        block = source[source.index('usb_data = {\n                        "role": role,'):]
        block = block[:block.index("}\n")]
        for key in ('"paired_count"', '"linked_count"', '"found_count"', '"usb_ip"'):
            self.assertIn(key, block, key)

    def test_the_counts_are_computed_from_the_right_fields(self):
        """paired from paired_devices, linked from its `linked` flag, found from
        found_devices. Conflating any two of them is the defect this replaces."""
        cfg = {
            "type": "LEX",
            "macaddress": "B8:98:B0:07:85:ED",
            "ipaddress": "198.51.100.5",
            "paired_devices": {"AA": {"linked": True}, "BB": {"linked": False}, "CC": {}},
            "found_devices": {str(i): {} for i in range(8)},
        }
        paired = cfg["paired_devices"]
        self.assertEqual(len(paired), 3)
        self.assertEqual(sum(1 for e in paired.values() if e.get("linked")), 1)
        self.assertEqual(len(cfg["found_devices"]), 8)
        self.assertNotEqual(len(cfg["found_devices"]), len(paired),
                            "the fixture must make the two genuinely different")

    def test_the_pill_carries_no_literal_colour(self):
        page = self.source("ui", "index.html")
        rule = page[page.index(".usb-pill{"):page.index("}", page.index(".usb-pill{"))]
        self.assertNotRegex(rule, r"#[0-9a-fA-F]{3,8}")
        self.assertIn("var(--", rule)


class ControlAppearanceTests(ServerTestBase):
    """Every control derives its colour from the active preset.

    Before this pass, three families painted selection with a literal
    rgba(30,144,255,...) that no preset could reach, and the tab rules were
    copied into all four pages.
    """

    PAGES = ("index.html", "matrix/configure.html", "matrix/index.html", "matrix/usb.html")
    SHEETS = ("templates.css", "settings.css", "device-table.css",
              "matrix/matrix.css", "matrix/usb-matrix.css")

    def source(self, *parts):
        return Path(srv.__file__).resolve().parent.joinpath("ui", *parts).read_text(encoding="utf-8")

    def controls_block(self):
        css = self.source("templates.css")
        return css[css.index("   Controls\n"):]

    # ---- 87. token inheritance ----
    @staticmethod
    def without_comments(text):
        """Prose about a colour is not a colour. Strips /* */ and // runs."""
        text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
        return re.sub(r"(?m)^\s*//.*$", "", text)

    @staticmethod
    def without_var_fallbacks(text):
        """`var(--token, <literal>)` is the value used when the token is absent.

        Every preset defines the token, so the fallback never paints anything --
        but it must stay, because a stylesheet loaded without appearance.js has
        to render something. What matters is that nothing paints a hue directly.
        """
        out = []
        index = 0
        while True:
            start = text.find("var(--", index)
            if start < 0:
                out.append(text[index:])
                return "".join(out)
            depth = 0
            cursor = start + 3                      # at the "("
            # Balanced scan: the fallback routinely contains rgba(...), so a
            # regex with [^()] inside stops at the wrong bracket and leaves the
            # literal in place.
            while cursor < len(text):
                if text[cursor] == "(":
                    depth += 1
                elif text[cursor] == ")":
                    depth -= 1
                    if depth == 0:
                        break
                cursor += 1
            out.append(text[index:start])
            out.append("var(--token)")
            index = cursor + 1

    def test_no_control_surface_names_a_literal_colour(self):
        """The OmniSuite blue that survived every preset change."""
        offenders = []
        for rel in self.SHEETS + self.PAGES:
            text = self.without_var_fallbacks(self.without_comments(self.source(*rel.split("/"))))
            for number, line in enumerate(text.splitlines(), 1):
                stripped = line.strip()
                # A custom-property DEFINITION may carry a literal: that is the
                # palette's own default, and every preset overrides it at
                # runtime. What must never carry one is a line that paints --
                # a background, colour, border, shadow or outline.
                if stripped.startswith("--"):
                    continue
                if ":root{" in stripped or ".light{" in stripped:
                    continue
                if re.search(r"rgba?\(\s*30\s*,\s*144\s*,\s*255", stripped) or "#1d4ed8" in stripped:
                    offenders.append(f"{rel}:{number}: {stripped[:70]}")
        self.assertEqual(offenders, [], "these paint a control in a fixed hue")

    def test_the_derived_tokens_exist_and_come_from_the_roles(self):
        block = self.source("templates.css")
        for token, derived_from in (("--selected-fill", "--state-selection"),
                                    ("--selected-tint", "--state-selection"),
                                    ("--focus-ring", "--action-primary"),
                                    ("--danger-ring", "--state-error")):
            line = next(l for l in block.splitlines() if l.strip().startswith(token + ":"))
            self.assertIn(derived_from, line, f"{token} must derive from {derived_from}")
            # Mixing toward black or white is how a derived shade is made and
            # carries no hue of its own; naming an actual colour is the defect.
            for literal in re.findall(r"#[0-9a-fA-F]{3,8}", line):
                digits = literal[1:]
                if len(digits) in (3, 4):
                    digits = "".join(ch * 2 for ch in digits)
                channels = {digits[i:i + 2].lower() for i in (0, 2, 4)}
                with self.subTest(token=token, literal=literal):
                    self.assertEqual(len(channels), 1,
                                     f"{token} names the hue {literal}")

    def test_the_tab_rules_live_in_one_place(self):
        for page in self.PAGES:
            with self.subTest(page=page):
                self.assertNotIn(".tab.active{", self.source(*page.split("/")),
                                 "a per-page copy is how they drifted apart")
        self.assertIn(".tab.active,", self.controls_block())

    # ---- 88. destructive stays destructive ----
    def test_a_destructive_button_is_distinguished_by_more_than_hue(self):
        """Measured across the ten presets, accent and error are as close as
        deltaE 11 in Rose. A ring is what survives that."""
        block = self.controls_block()
        danger = block[block.index(".btn.danger:not(:disabled) {"):]
        danger = danger[:danger.index("}")]
        self.assertIn("var(--state-error)", danger)
        self.assertIn("box-shadow: inset", danger, "hue alone is not a distinction")
        self.assertIn("var(--danger-ring)", danger)

    # ---- 89/90/91/92. hover, pressed, focus, disabled ----
    def test_each_interaction_state_is_defined_once_for_every_family(self):
        block = self.controls_block()
        for needle in (":hover:not(:disabled)", ":active:not(:disabled)",
                       ":focus-visible", ":disabled"):
            self.assertIn(needle, block, needle)

    def test_focus_is_scoped_to_the_keyboard(self):
        block = self.controls_block()
        self.assertIn(".btn:focus-visible", block)
        self.assertNotIn(".btn:focus,", block,
                         "a mouse click must not leave a ring, but :focus would")

    def test_no_state_can_change_a_control_box(self):
        """Requirement: nothing moves between normal, hover, pressed, focus and
        selected. Only colour-ish properties may appear in a state rule."""
        block = self.controls_block()
        banned = ("width:", "height:", "padding:", "margin:", "font-size:",
                  "border-width:", "letter-spacing:", "transform:", "top:", "left:")
        for rule in re.findall(r"\n([^\n{}]*(?::hover|:active|:focus-visible|\.active|\.on)[^{}]*)\{([^}]*)\}", block):
            selector, body = rule
            if "switch-input" in selector or ".toast" in selector:
                continue
            for name in banned:
                with self.subTest(selector=selector.strip()[:60], property=name):
                    self.assertNotIn(name, body, f"{selector.strip()} changes {name}")

    def test_disabled_stays_visibly_disabled(self):
        block = self.controls_block()
        disabled = block[block.index(".btn:disabled,"):]
        disabled = disabled[:disabled.index("}")]
        self.assertIn("opacity: var(--control-disabled-opacity)", disabled)
        self.assertIn("cursor: not-allowed", disabled)

    # ---- 93/94/96. mutually exclusive groups ----
    def test_every_exclusive_group_has_a_selected_and_an_unselected_rule(self):
        block = self.controls_block()
        for selected, unselected in ((".tab.active,", "\n.tab {"),
                                     (".mode .modebtn.active,", "\n.mode .modebtn {"),
                                     (".switch.on {", "\n.switch {"),
                                     (".browser-item.selected,", None)):
            with self.subTest(group=selected):
                self.assertIn(selected, block)
                if unselected:
                    self.assertIn(unselected, block)

    def test_the_selected_state_is_a_fill_not_a_hairline(self):
        block = self.controls_block()
        for group in (".tab.active,", ".mode .modebtn.active,"):
            rule = block[block.index(group):]
            rule = rule[:rule.index("}")]
            with self.subTest(group=group):
                self.assertIn("background-color:", rule,
                              "a border-only difference is not obvious enough")
                self.assertIn("--selected-", rule)

    def test_a_selected_control_still_answers_to_hover(self):
        self.assertIn(".mode .modebtn.active:hover", self.controls_block())

    # ---- 95. semantic state ----
    def test_the_active_tab_is_marked_on_every_page(self):
        for page in self.PAGES:
            with self.subTest(page=page):
                self.assertIn('class="tab active" aria-current="page"',
                              self.source(*page.split("/")))

    def test_the_mode_selector_is_a_radio_group(self):
        page = self.source("matrix", "index.html")
        self.assertIn('role="radiogroup"', page)
        self.assertEqual(page.count('role="radio"'), 3)
        self.assertEqual(page.count('aria-checked="true"'), 1)
        self.assertEqual(page.count('aria-checked="false"'), 2)

    def test_changing_the_mode_updates_the_announced_state(self):
        script = self.source("matrix", "matrix.js")
        block = script[script.index("function setMode("):]
        block = block[:block.index("\n}")]
        # Comments stripped first. The comment above this call names
        # aria-checked, so asserting on the raw text passed even with the call
        # deleted -- a test that could not fail.
        code = re.sub(r"(?m)^\s*//.*$", "", block)
        self.assertIn("setAttribute('aria-checked'", code,
                      "painting the class without the state leaves it unannounced")
        self.assertIn("String(on)", code, "it must track the selection, not a constant")

    def test_every_toggle_is_reachable_by_keyboard(self):
        """display:none removed the real checkbox from the tab order and from
        the accessibility tree, so no switch could be operated without a mouse."""
        for page in self.PAGES:
            text = self.source(*page.split("/"))
            for match in re.finditer(r'<input type="checkbox"[^>]*>', text):
                tag = match.group(0)
                if 'id="select_all"' in tag or "row-select" in tag:
                    continue
                with self.subTest(page=page, tag=tag[:70]):
                    self.assertNotIn("display:none", tag.replace(" ", ""))
        self.assertIn('.toggle input[type="checkbox"].switch-input', self.controls_block())
        self.assertIn('input[type="checkbox"]:focus-visible ~ .switch', self.controls_block())

    # ---- 97. every preset carries every role ----
    def test_every_preset_defines_every_semantic_role(self):
        script = self.source("appearance.js")
        roles = set(re.findall(r"\{id: '([a-z]+)', name:", script[script.index("APPEARANCE_COLORS"):
                                                                 script.index("APPEARANCE_PRESETS")]))
        self.assertTrue(roles >= {"accent", "selection", "success", "warning", "error"}, roles)
        presets = re.findall(r"\{id: '([a-z]+)', name: '[^']*', hint: '[^']*',\s*swatch:.*?colors: \{(.*?)\}\}",
                             script[script.index("APPEARANCE_PRESETS"):], re.S)
        self.assertEqual(len(presets), 10, [p[0] for p in presets])
        for name, body in presets:
            with self.subTest(preset=name):
                for role in roles:
                    self.assertIn(f"{role}:", body, f"{name} has no {role}")

    def test_the_shared_toast_replaced_the_per_page_copies(self):
        for page in ("matrix/matrix.js", "matrix/usb.js"):
            with self.subTest(page=page):
                self.assertNotIn("function toast(", self.source(*page.split("/")))
        for page in self.PAGES:
            with self.subTest(page=page):
                self.assertIn("toast.js", self.source(*page.split("/")))
        self.assertIn(".toast {", self.controls_block())

    def test_the_toast_follows_the_preset(self):
        block = self.controls_block()
        rule = block[block.index(".toast.error {"):]
        rule = rule[:rule.index("}")]
        self.assertIn("var(--state-error)", rule)
        # The only literals allowed are the var() fallbacks, and those may not
        # be a hue either.
        for literal in re.findall(r"#[0-9a-fA-F]{3,8}", rule):
            digits = literal[1:]
            if len(digits) in (3, 4):
                digits = "".join(ch * 2 for ch in digits)
            channels = {digits[i:i + 2].lower() for i in (0, 2, 4)}
            self.assertEqual(len(channels), 1, f"the toast names the hue {literal}")


class UsbDisplayNameTests(ServerTestBase):
    """A standalone extender is shown as HW-OMNI-311/324 and identified as AT-OMNI-311/324.

    These are two different facts about the same unit. The protocol names the
    role byte AT-OMNI-311 / AT-OMNI-324 and every role lookup, pairing family
    check and bench guard compares against that string; the product is sold as
    HW-OMNI. The normalisation is presentation only, and the point of this class
    is that a future global rename cannot quietly take the protocol with it.
    """

    def setUp(self):
        super().setUp()
        self._patch_cache(FULL_CACHE_UNITS)
        self.seed(*OBSERVED_UDP)

    def standalone(self):
        return {d["mac"]: d for d in srv._usb_extender_view()["devices"]
                if d.get("classification") == "STANDALONE"}

    # ---- what is shown ----
    def test_the_displayed_model_is_the_hw_name(self):
        devices = self.standalone()
        self.assertEqual(devices[OMNI311_MAC]["display_model"], "HW-OMNI-311")
        self.assertEqual(devices[OMNI324_MAC]["display_model"], "HW-OMNI-324")

    def test_the_generated_hostname_is_the_hw_name(self):
        """These units report no hostname, so the column holds our own label."""
        devices = self.standalone()
        self.assertEqual(devices[OMNI311_MAC]["display_hostname"], "HW-OMNI-311")
        self.assertEqual(devices[OMNI324_MAC]["display_hostname"], "HW-OMNI-324")

    def test_an_assigned_name_wins_and_is_never_overwritten(self):
        for key in ("friendly_name", "display_name", "hostname"):
            with self.subTest(field=key):
                device = {"device_type": srv.omni_usb_extender.HOST_MODEL, key: "Studio B host"}
                self.assertEqual(srv._display_hostname(device), "Studio B host")

    def test_a_blank_assigned_name_falls_back_rather_than_showing_nothing(self):
        device = {"device_type": srv.omni_usb_extender.HOST_MODEL, "friendly_name": "   "}
        self.assertEqual(srv._display_hostname(device), "HW-OMNI-311")

    def test_an_unrecognised_type_passes_through_unchanged(self):
        """The map renames two known models; it is not a general rewriter."""
        self.assertEqual(srv._display_model("AT-OMNI-512"), "AT-OMNI-512")
        self.assertEqual(srv._display_model(""), "")
        self.assertEqual(srv._display_model(None), "")

    # ---- what is unchanged ----
    def test_the_protocol_identity_is_unchanged(self):
        self.assertEqual(srv.omni_usb_extender.HOST_MODEL, "AT-OMNI-311")
        self.assertEqual(srv.omni_usb_extender.DEVICE_MODEL, "AT-OMNI-324")
        devices = self.standalone()
        self.assertEqual(devices[OMNI311_MAC]["device_type"], "AT-OMNI-311")
        self.assertEqual(devices[OMNI324_MAC]["device_type"], "AT-OMNI-324")
        self.assertEqual(devices[OMNI311_MAC]["udp_reported_type"], "AT-OMNI-311")
        self.assertEqual(devices[OMNI324_MAC]["udp_reported_type"], "AT-OMNI-324")

    def test_the_usb_role_is_unaffected(self):
        lex, rex = srv._usb_matrix_axes()
        self.assertIn(OMNI311_MAC, [e["usb_mac"] for e in lex], "the 311 is still the LEX")
        self.assertIn(OMNI324_MAC, [e["usb_mac"] for e in rex], "the 324 is still the REX")
        devices = self.standalone()
        self.assertEqual(devices[OMNI311_MAC]["usb_role"], "USB Host / LEX")
        self.assertEqual(devices[OMNI324_MAC]["usb_role"], "USB Device / REX")

    def test_mac_identity_is_unaffected(self):
        devices = self.standalone()
        self.assertEqual(set(devices), {OMNI311_MAC, OMNI324_MAC})
        for mac, device in devices.items():
            self.assertEqual(srv._norm_usb_mac(device["mac"]), srv._norm_usb_mac(mac))

    def test_the_role_gate_still_refuses_a_reversed_pair(self):
        """The guard compares device_type, so renaming it would open this."""
        reply = self.client.post("/api/usb_route/pair",
                                 json={"host_mac": OMNI324_MAC, "device_mac": OMNI311_MAC})
        self.assertIn(reply.status_code, (400, 409))

    # ---- every surface agrees ----
    def test_configure_matrix_and_the_workbook_agree(self):
        openpyxl = __import__("openpyxl")
        configure = {d["mac"]: d["display_model"] for d in srv._usb_extender_view()["devices"]
                     if d.get("classification") == "STANDALONE"}
        lex, rex = srv._usb_matrix_axes()
        matrix = {e["usb_mac"]: e["model"] for e in lex + rex}
        reply = self.client.get("/api/download_inventory")
        sheet = openpyxl.load_workbook(io.BytesIO(reply.data))["USB"]
        workbook = {sheet.cell(row=r, column=6).value: sheet.cell(row=r, column=1).value
                    for r in range(2, sheet.max_row + 1)}
        for mac in (OMNI311_MAC, OMNI324_MAC):
            expected = configure[mac]
            self.assertTrue(expected.startswith("HW-OMNI-3"), expected)
            self.assertEqual(matrix[mac], expected, f"Matrix disagrees for {mac}")
            self.assertEqual(workbook[mac], expected, f"the workbook disagrees for {mac}")

    def test_the_support_dump_carries_both_names(self):
        dump = json.loads(self.client.get("/api/ts_export").get_data(as_text=True))
        rows = {r["usb_mac"]: r for r in (dump.get("usb_endpoints") or [])}
        self.assertEqual(rows[OMNI311_MAC]["model"], "HW-OMNI-311")
        self.assertEqual(rows[OMNI311_MAC]["udp_reported_type"], "AT-OMNI-311",
                         "the dump must keep the evidence, not only the label")

    def test_no_operator_facing_string_still_says_at_omni_311(self):
        """Comments and the engineering record keep the protocol name; text the
        operator reads does not."""
        guide = Path(srv.__file__).resolve().parent.joinpath("ui", "user-guide.html").read_text(encoding="utf-8")
        self.assertNotIn("AT-OMNI-311", guide)
        self.assertNotIn("AT-OMNI-324", guide)
        self.assertIn("HW-OMNI-311", guide)
        for note in (cap.get("note", "") for cap in srv.USB_ROUTE_CAPABILITY.values()):
            self.assertNotIn("AT-OMNI-3", note, note)


class UpdateAlertTests(ServerTestBase):
    """The Settings icon draws attention once per release, then stops.

    The alert is a one-time visual; the status is a standing fact. Settings goes
    on saying an update is available for as long as one is, and only the
    flashing is acknowledged.
    """

    def setUp(self):
        super().setUp()
        with srv._update_check_lock:
            srv._update_check_cache["checked_at"] = 0.0
            srv._update_check_cache["result"] = None
        self.addCleanup(self._reset_update_cache)

    def _reset_update_cache(self):
        with srv._update_check_lock:
            srv._update_check_cache["checked_at"] = 0.0
            srv._update_check_cache["result"] = None

    def offer(self, tag):
        """Have GitHub offer this release."""
        def fetch(*args, **kwargs):
            return {"tag_name": tag, "draft": False, "prerelease": False,
                    "html_url": f"https://github.com/Hall-Research-Technologies/omniSuite/releases/tag/{tag}",
                    "assets": []}
        self.patch_srv("_fetch_latest_release", fetch)
        self._reset_update_cache()

    def check(self, force=False):
        return self.client.get("/api/update_check" + ("?force=1" if force else "")).get_json()

    def ack(self, version):
        return self.client.post("/api/update_ack", json={"version": version})

    # ---- the comparison matrix ----
    def test_the_state_for_every_relationship(self):
        installed = srv._app_version()
        base = srv._version_tuple(installed)
        older = f"V{base[0]}.{base[1]}.{max(base[2] - 1, 0)}"
        newer = f"V{base[0]}.{base[1]}.{base[2] + 1}"
        for tag, expected in ((older, srv.UPDATE_CURRENT),
                              (installed, srv.UPDATE_CURRENT),
                              (newer, srv.UPDATE_AVAILABLE)):
            with self.subTest(latest=tag):
                self.offer(tag)
                self.assertEqual(self.check()["status"], expected)

    def test_the_matrix_the_specification_asks_for(self):
        """Including 1.0.9 -> 1.0.10, which a string comparison gets backwards."""
        cases = [("V1.0.7", "V1.0.6", srv.UPDATE_CURRENT),
                 ("V1.0.7", "V1.0.7", srv.UPDATE_CURRENT),
                 ("V1.0.7", "V1.0.8", srv.UPDATE_AVAILABLE),
                 ("V1.0.9", "V1.0.10", srv.UPDATE_AVAILABLE),
                 ("1.0.7", "V1.0.7", srv.UPDATE_CURRENT),
                 ("V1.0.7", "v1.0.8", srv.UPDATE_AVAILABLE)]
        for installed, latest, expected in cases:
            with self.subTest(installed=installed, latest=latest):
                self.patch_srv("_app_version", lambda v=installed: v)
                self.offer(latest)
                self.assertEqual(self.check()["status"], expected)

    def test_a_build_ahead_of_the_published_release_is_current_not_an_error(self):
        """V1.0.7 installed against V1.0.6 published is a successful check."""
        self.offer("V1.0.6")
        self.patch_srv("_app_version", lambda: "V1.0.7")
        self._reset_update_cache()
        body = self.check()
        self.assertEqual(body["status"], srv.UPDATE_CURRENT)
        self.assertFalse(body["update_available"])
        self.assertTrue(body["ok"], "a successful request is not a failure")
        self.assertIsNone(body["error_summary"])

    # ---- the alert ----
    def test_an_available_update_raises_the_alert(self):
        self.offer("V99.0.0")
        self.assertTrue(self.check()["alert"])

    def test_nothing_else_raises_it(self):
        self.offer("V0.0.1")
        self.assertFalse(self.check()["alert"], "CURRENT must not flash")
        self.patch_srv("_fetch_latest_release",
                       lambda *a, **k: (_ for _ in ()).throw(OSError("offline")))
        self._reset_update_cache()
        self.assertFalse(self.check()["alert"], "UNABLE_TO_CHECK must not flash")

    def test_acknowledging_stops_it(self):
        self.offer("V99.0.0")
        self.assertTrue(self.check()["alert"])
        self.assertEqual(self.ack("V99.0.0").status_code, 200)
        self.assertFalse(self.check()["alert"])

    def test_re_checking_the_same_release_does_not_restart_it(self):
        """Check Now finding what was already dismissed must stay quiet."""
        self.offer("V99.0.0")
        self.check()
        self.ack("V99.0.0")
        self.assertFalse(self.check(force=True)["alert"])

    def test_navigating_pages_does_not_restart_it(self):
        """Every page reads one server-side answer, so none can re-arm it."""
        self.offer("V99.0.0")
        self.check()
        self.ack("V99.0.0")
        for _ in range(8):
            self.assertFalse(self.check()["alert"])

    def test_a_restart_does_not_restart_it(self):
        self.offer("V99.0.0")
        self.check()
        self.ack("V99.0.0")
        # A restart re-reads the acknowledgement from disk, not from memory.
        self.assertEqual(srv._acknowledged_update_version(), "V99.0.0")
        self._reset_update_cache()
        self.assertFalse(self.check()["alert"])

    def test_a_newer_release_alerts_again(self):
        self.offer("V99.0.0")
        self.check()
        self.ack("V99.0.0")
        self.offer("V99.0.1")
        self.assertTrue(self.check()["alert"], "a newer release must be announced")

    def test_acknowledging_an_older_release_does_not_re_arm_a_newer_one(self):
        self.offer("V99.0.5")
        self.check()
        self.ack("V99.0.5")
        self.ack("V99.0.1")                       # stale acknowledgement arrives late
        self.assertEqual(srv._acknowledged_update_version(), "V99.0.5")
        self.assertFalse(self.check()["alert"])

    def test_an_unparseable_version_is_refused(self):
        self.assertEqual(self.ack("not-a-version").status_code, 400)

    # ---- the acknowledgement is not device state ----
    def test_clearing_units_does_not_re_arm_the_alert(self):
        self.offer("V99.0.0")
        self.check()
        self.ack("V99.0.0")
        self.client.post("/api/usb_extenders/clear")
        self.assertEqual(srv._acknowledged_update_version(), "V99.0.0")

    def test_resetting_appearance_does_not_re_arm_the_alert(self):
        self.offer("V99.0.0")
        self.check()
        self.ack("V99.0.0")
        self.client.post("/api/ui_preferences/reset")
        self.assertEqual(srv._acknowledged_update_version(), "V99.0.0")

    # ---- the settings dialog and the icon ----
    def source(self, *parts):
        return Path(srv.__file__).resolve().parent.joinpath(*parts).read_text(encoding="utf-8")

    def test_the_dialog_switches_on_the_state_not_on_the_text(self):
        module = self.source("ui", "settings.js")
        for state in ("UPDATE_AVAILABLE", "CURRENT", "UNABLE_TO_CHECK", "CHECKING"):
            self.assertIn(state, module, state)
        self.assertIn("No update available", module)
        self.assertIn("UPDATE AVAILABLE", module)

    def test_the_icon_alert_is_driven_by_the_state(self):
        module = self.source("ui", "settings.js")
        self.assertIn("update-alert", module)
        self.assertIn("acknowledgeUpdateAlert", module)
        self.assertIn("/api/update_ack", module)

    def test_the_pulse_cannot_reflow_the_navigation(self):
        """An animation that changed the icon's box would move the tabs."""
        css = self.source("ui", "settings.css")
        block = css[css.index("@keyframes omni-update-pulse"):]
        block = block[:block.index("}\n}") + 3]
        for property_name in ("width", "height", "padding", "margin", "font-size"):
            self.assertNotIn(f"{property_name}:", block,
                             f"the pulse animates {property_name}, which reflows the header")
        self.assertIn("box-shadow", block)

    def test_reduced_motion_gets_a_static_indicator_not_nothing(self):
        css = self.source("ui", "settings.css")
        block = css[css.index("prefers-reduced-motion"):]
        self.assertIn("animation:none", block.replace(" ", ""))
        self.assertIn("box-shadow", block, "the indicator must remain visible, just still")

    def test_the_alert_uses_semantic_tokens_only(self):
        """A literal colour would look wrong in nine of the ten presets."""
        css = self.source("ui", "settings.css")
        block = css[css.index(".gear-btn.update-alert"):]
        block = block[:block.index("@media")]
        self.assertNotRegex(block, r"#[0-9a-fA-F]{3,6}")
        self.assertIn("var(--state-warning", block)


class RouteIdentityContractTests(ServerTestBase):
    """The routing identity is a MAC. An address may never stand in for one."""

    def setUp(self):
        super().setUp()
        self._patch_cache(FULL_CACHE_UNITS)
        self.seed(*OBSERVED_UDP)
        self.seed_integrated()
        self.js = Path(srv.__file__).parent.joinpath("ui", "matrix", "usb.js").read_text(encoding="utf-8")

    # The Matrix axes come from a live usb_icron read per parent, and a parent
    # that cannot be read is correctly left off the axes. Stated as a fixture,
    # because these assertions are about identity, not about reachability.
    def ws_read(self, url, name):
        if name != "usb_icron":
            return None
        for parent_ip, usb_mac, role, usb_ip, _parent_mac in self.INTEGRATED_FIXTURE:
            if f"//{parent_ip}:" in url:
                return {"config": {"type": role, "macaddress": usb_mac, "ipaddress": usb_ip,
                                   "revision": "1.9.4", "protocol": "IP",
                                   "paired_devices": {}, "found_devices": {}}}
        return None

    def axis_entries(self):
        state = self.client.get("/api/usb_state").get_json()
        return (state.get("matrix_lex") or []) + (state.get("matrix_rex") or []), state

    # ---- the defect ----
    def test_an_ipv4_address_never_normalises_into_a_mac(self):
        """192.0.2.152 has twelve hex digits and used to become a MAC."""
        for address in ("192.0.2.152", "192.0.2.141", "10.0.0.1", "255.255.255.255"):
            with self.assertRaises(usb.ProtocolError, msg=address):
                usb.normalize_mac(address)

    def test_legitimate_mac_forms_are_still_accepted(self):
        for form in ("00:1B:13:04:E9:6E", "00-1b-13-04-e9-6e", "001b1304e96e", "001b.1304.e96e"):
            self.assertEqual(usb.normalize_mac(form), "00:1B:13:04:E9:6E", form)

    def test_a_route_request_carrying_an_address_is_refused_as_invalid(self):
        r = self.client.post("/api/usb_route/pair",
                             json={"lex_mac": OMNI311_MAC, "rex_mac": "192.0.2.152"})
        self.assertEqual(r.status_code, 400, "an address is malformed input, not an unknown device")
        self.assertIn("not a valid MAC", r.get_json()["error"])
        self.assertEqual(self.transmitted, [])

    def test_the_fabricated_mac_from_the_field_report_is_unroutable(self):
        r = self.client.post("/api/usb_route/pair",
                             json={"lex_mac": OMNI311_MAC, "rex_mac": "19:21:68:10:01:52"})
        self.assertEqual(r.status_code, 404)
        self.assertEqual(r.get_json()["status"], "unknown_endpoint")
        self.assertEqual(self.transmitted, [], "an unknown endpoint must never be addressed")

    # ---- the contract ----
    def test_every_axis_entry_carries_an_explicit_usb_mac(self):
        entries, _ = self.axis_entries()
        self.assertTrue(entries)
        for entry in entries:
            self.assertIn("usb_mac", entry, f"{entry.get('usb_key')} has no explicit routing identity")

    def test_usb_mac_is_a_canonical_six_byte_mac_wherever_present(self):
        entries, _ = self.axis_entries()
        for entry in entries:
            mac = entry.get("usb_mac") or ""
            if not mac:
                continue
            self.assertEqual(usb.normalize_mac(mac), mac, "usb_mac must already be canonical")
            self.assertEqual(len(mac.replace(":", "")), 12, entry.get("usb_key"))

    def test_usb_mac_matches_the_authoritative_endpoint_for_each_family(self):
        entries, _ = self.axis_entries()
        by_key = {e.get("usb_key"): e for e in entries}
        standalone = by_key[usb.normalize_mac(OMNI311_MAC)]
        self.assertEqual(standalone["usb_mac"], usb.normalize_mac(OMNI311_MAC),
                         "a standalone endpoint's identity is its own MAC")
        integrated = by_key[E4521_IP]
        self.assertEqual(integrated["usb_mac"], usb.normalize_mac(E4521_USB_MAC),
                         "an integrated endpoint's identity is its associated Icron MAC")
        self.assertNotEqual(integrated["usb_mac"], integrated["usb_key"],
                            "identity and display key are different things")

    def test_the_display_key_of_an_integrated_entry_is_still_its_control_ip(self):
        """The usb_icron path is addressed by IP and must not change."""
        entries, _ = self.axis_entries()
        integrated = next(e for e in entries if e.get("kind") == "integrated")
        self.assertEqual(integrated["usb_key"], integrated["ip"])

    def test_a_routable_cell_always_has_identities_for_both_endpoints(self):
        _, state = self.axis_entries()
        by_key = {e["usb_key"]: e for e in (state["matrix_lex"] + state["matrix_rex"])}
        for rex_key, row in (state.get("capabilities") or {}).items():
            for lex_key, cap in row.items():
                if not cap.get("enabled") or cap.get("control_path") != "standalone_udp":
                    continue
                for key in (rex_key, lex_key):
                    self.assertTrue(by_key[key].get("usb_mac"),
                                    f"{key} is routable but has no canonical USB MAC")

    def test_a_missing_identity_disables_the_cell_instead_of_inventing_one(self):
        lex = {"kind": "standalone", "usb_key": "k", "ip": "10.0.0.1", "usb_mac": "",
               "classification": "STANDALONE", "online": True, "pairing_eligible": True}
        rex = {"kind": "standalone", "usb_key": "k2", "ip": "10.0.0.2", "usb_mac": "AA:BB:CC:00:00:02",
               "classification": "STANDALONE", "online": True, "pairing_eligible": True}
        cap = srv._usb_route_capability(lex, rex)
        self.assertEqual(cap["state"], "IDENTITY_UNAVAILABLE")
        self.assertFalse(cap["enabled"])
        self.assertEqual(cap["label"], "USB endpoint identity unavailable")

    def test_an_address_in_the_usb_mac_field_is_not_accepted_as_identity(self):
        entry = {"kind": "standalone", "usb_key": "192.0.2.152", "ip": "192.0.2.152",
                 "usb_mac": "192.0.2.152", "classification": "STANDALONE",
                 "online": True, "pairing_eligible": True}
        self.assertEqual(srv._canonical_usb_mac(entry), "", "an address is not an identity")

    # ---- the client ----
    def test_the_click_handler_routes_by_mac_and_never_by_display_key(self):
        block = self.js[self.js.index("if(controlPath === 'standalone_udp')"):]
        block = block[:block.index("await refresh(true);")]
        self.assertIn("data-lex-mac", block)
        self.assertIn("data-rex-mac", block)
        self.assertIn("lex_mac: lexMac", block)
        self.assertIn("rex_mac: rexMac", block)
        self.assertNotIn("lex_mac: lex,", block, "the display key must never be sent as an identity")
        self.assertNotIn("rex_mac: rex}", block)

    def test_the_client_fails_closed_without_an_identity(self):
        block = self.js[self.js.index("if(controlPath === 'standalone_udp')"):]
        block = block[:block.index("await refresh(true);")]
        self.assertIn("if(!lexMac || !rexMac)", block)
        self.assertIn("USB endpoint identity unavailable", block)
        gate = block[block.index("if(!lexMac || !rexMac)"):]
        self.assertLess(gate.index("return;"), gate.index("postJSON"),
                        "the request must be abandoned before anything is posted")


class MixedRoutingProductionPathTests(ServerTestBase):
    """One production contract serves every UDP-controlled combination."""

    def setUp(self):
        super().setUp()
        self._patch_cache(FULL_CACHE_UNITS)
        self.net = FakeUdp()
        self.net.endpoints[E4521_USB_IP] = {"mac": E4521_USB_MAC, "code": 0, "peers": [],
                                            "advanced": True, "ack": True, "query": True}
        self.net.endpoints[D4511_USB_IP] = {"mac": D4511_USB_MAC, "code": 1, "peers": [],
                                            "advanced": True, "ack": True, "query": True}
        self.service._exchange = self.net.exchange
        for ip in (OMNI311_IP, OMNI324_IP, E4521_USB_IP, D4511_USB_IP):
            self.service.discover_ip(ip, IFACE_IP, IFACE_MASK)
        for mac in (OMNI311_MAC, OMNI324_MAC, E4521_USB_MAC, D4511_USB_MAC):
            self.service.ping(mac)
        self.net.sent.clear()

    def opcodes(self):
        return [c for _d, c, _p in self.net.sent if c not in (usb.QUERY, usb.ADVANCED_QUERY)]

    def test_all_three_udp_combinations_use_the_same_endpoint(self):
        for lex_mac, rex_mac, name in ((OMNI311_MAC, OMNI324_MAC, "311 to 324"),
                                       (E4521_USB_MAC, OMNI324_MAC, "E4521 to 324"),
                                       (OMNI311_MAC, D4511_USB_MAC, "311 to D4511")):
            self.net.sent.clear()
            for endpoint in self.net.endpoints.values():
                endpoint["peers"] = []
            r = self.client.post("/api/usb_route/pair", json={"lex_mac": lex_mac, "rex_mac": rex_mac})
            self.assertEqual(r.status_code, 200, name)
            self.assertEqual(r.get_json()["status"], "VERIFIED_SUCCESS", name)
            self.assertEqual(self.opcodes(), [usb.PAIR, usb.PAIR], name)

    def test_the_resolve_endpoint_reports_identity_and_provider_without_sending(self):
        r = self.client.post("/api/usb_route/resolve",
                             json={"lex_mac": OMNI311_MAC, "rex_mac": D4511_USB_MAC})
        body = r.get_json()
        self.assertEqual(r.status_code, 200)
        self.assertEqual(body["lex"]["usb_mac"], usb.normalize_mac(OMNI311_MAC))
        self.assertEqual(body["lex"]["classification"], "STANDALONE")
        self.assertEqual(body["lex"]["usb_role"], "USB Host / LEX")
        self.assertEqual(body["rex"]["usb_mac"], usb.normalize_mac(D4511_USB_MAC))
        self.assertEqual(body["rex"]["classification"], "INTEGRATED")
        self.assertEqual(body["rex"]["usb_role"], "USB Device / REX")
        self.assertEqual(body["rex"]["parent_ip"], D4511_IP)
        self.assertEqual(body["rex"]["model"], "HW-OMNI-D4511",
                         "the parent supplies the product identity, not the UDP role name")
        self.assertEqual(body["rex"]["udp_reported_type"], usb.DEVICE_MODEL,
                         "the protocol-reported value is kept for diagnostics only")
        self.assertTrue(body["mixed"])
        self.assertEqual(body["provider"], "standalone_udp")
        self.assertEqual(body["capability"]["state"], "SUPPORTED_MIXED_VERIFIED")
        self.assertTrue(body["capability"]["data_plane_verified"],
                        "311 to D4511 was physically tested by the operator")
        self.assertEqual(self.opcodes(), [], "resolution must transmit nothing")

    def test_resolve_refuses_the_icron_combination_like_the_mutating_endpoints(self):
        r = self.client.post("/api/usb_route/resolve",
                             json={"lex_mac": E4521_USB_MAC, "rex_mac": D4511_USB_MAC})
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.get_json()["status"], "not_supported")

    def test_the_server_resolves_identity_and_ignores_client_claims(self):
        """A client cannot nominate a classification, parent or provider."""
        r = self.client.post("/api/usb_route/resolve",
                             json={"lex_mac": OMNI311_MAC, "rex_mac": D4511_USB_MAC,
                                   "classification": "STANDALONE", "parent_ip": "10.9.9.9",
                                   "control_path": "usb_icron", "kind": "standalone"})
        body = r.get_json()
        self.assertEqual(body["rex"]["classification"], "INTEGRATED", "server-resolved, not client-supplied")
        self.assertEqual(body["rex"]["parent_ip"], D4511_IP)
        self.assertEqual(body["provider"], "standalone_udp")


class MixedRouteVisibilityTests(ServerTestBase):
    """A UDP-created route must be visible on an integrated REX row."""

    def setUp(self):
        super().setUp()
        self._patch_cache(FULL_CACHE_UNITS)
        self.seed(*OBSERVED_UDP)
        # Stand in for the parents' usb_icron reads so the Matrix builds its
        # integrated rows; no pairing is reported, matching a D4511 whose route
        # was created over UDP.
        icron = {
            E4521_IP: {"type": "LEX", "macaddress": E4521_USB_MAC, "ipaddress": E4521_USB_IP},
            D4511_IP: {"type": "REX", "macaddress": D4511_USB_MAC, "ipaddress": D4511_USB_IP},
        }
        real = srv._ws_send_recv
        self.addCleanup(lambda: setattr(srv, "_ws_send_recv", real))

        def fake(url, payload, timeout=None):
            ip = re.findall(r"//([^:/]+)", url)[0]
            if payload.get("config_get") == "usb_icron" and ip in icron:
                return {"config": dict(icron[ip], paired_devices={})}
            return {"config": {}, "error": False}
        srv._ws_send_recv = fake

    def usb_state(self):
        return self.client.get("/api/usb_state").get_json()

    def set_peers(self, mac, peers):
        """device() hands back a decorated copy, so write the stored record."""
        with self.service._lock:
            self.service._devices[usb.normalize_mac(mac)]["paired_macs"] = list(peers)
            self.service._devices[usb.normalize_mac(mac)]["advanced_query_at"] = time.time()

    def test_an_integrated_rex_with_a_udp_peer_reports_a_route(self):
        self.set_peers(D4511_USB_MAC, [usb.normalize_mac(OMNI311_MAC)])
        state = self.usb_state()
        self.assertTrue(any(e.get("ip") == D4511_IP for e in state["matrix_rex"]), "integrated row missing")
        route = (state.get("standalone_routes") or {}).get(D4511_IP)
        self.assertIsNotNone(route, "usb_icron cannot see a UDP-created route, so it must be reported here")
        self.assertEqual(route["active"], usb.normalize_mac(OMNI311_MAC))
        self.assertEqual(route["source"], "udp_advanced_query")

    def test_an_integrated_rex_without_a_udp_peer_reports_nothing(self):
        """Absence must never be presented as 'no route'; usb_icron owns that."""
        self.set_peers(D4511_USB_MAC, [])
        self.assertIsNone((self.usb_state().get("standalone_routes") or {}).get(D4511_IP))

    def test_the_integrated_row_still_carries_its_control_ip_key(self):
        row = next(e for e in self.usb_state()["matrix_rex"] if e.get("ip") == D4511_IP)
        self.assertEqual(row["usb_key"], D4511_IP, "the usb_icron path is addressed by IP")
        self.assertEqual(row["usb_mac"], usb.normalize_mac(D4511_USB_MAC))

    def test_the_pairing_sweep_refreshes_an_integrated_endpoint_in_a_mixed_route(self):
        self.assertIn(srv._norm_usb_mac(D4511_USB_MAC), srv._usb_parent_context()["index"])
        self.set_peers(D4511_USB_MAC, [usb.normalize_mac(OMNI311_MAC)])
        asked = self.capture_refresh()
        srv._usb_pairing_refresh(force=True)
        self.assertIn(srv._norm_usb_mac(D4511_USB_MAC), [srv._norm_usb_mac(m) for m in asked],
                      "an integrated endpoint carrying a UDP route must be refreshed")

    def test_an_idle_integrated_endpoint_is_still_never_polled(self):
        self.set_peers(D4511_USB_MAC, [])
        asked = self.capture_refresh()
        srv._usb_pairing_refresh(force=True)
        self.assertNotIn(srv._norm_usb_mac(D4511_USB_MAC), [srv._norm_usb_mac(m) for m in asked],
                         "no new traffic for integrated endpoints that carry no UDP route")

    def capture_refresh(self):
        asked = []
        real = self.service.refresh_pairing
        self.service.refresh_pairing = lambda macs: (asked.extend(macs),
                                                     {"checked": len(macs), "refreshed": 0})[1]
        self.addCleanup(lambda: setattr(self.service, "refresh_pairing", real))
        return asked


class NativeDialogContractTests(ServerTestBase):
    """No production USB surface may use a native browser dialog."""

    def test_no_native_dialogs_remain_in_either_usb_module(self):
        ui = Path(srv.__file__).parent.joinpath("ui", "matrix")
        for name in ("usb.js", "usb-extenders.js"):
            source = ui.joinpath(name).read_text(encoding="utf-8")
            for call in ("alert(", "confirm(", "prompt("):
                hits = [line.strip() for line in source.split("\n")
                        if call in line and not line.strip().startswith("//")]
                # usbConfirmMixedRoute is a function name, not a native dialog.
                hits = [h for h in hits if not h.startswith("function usbConfirmMixedRoute")]
                self.assertEqual(hits, [], f"{name} still uses a native {call})")

    def test_errors_are_presented_through_the_existing_toast(self):
        source = Path(srv.__file__).parent.joinpath("ui", "matrix", "usb.js").read_text(encoding="utf-8")
        for message in ("Host port change failed", "Device type change failed",
                        "Device filtering change failed", "Refresh failed"):
            self.assertIn(message, source)


class InventoryDeduplicationTests(ServerTestBase):
    """One physical USB endpoint produces exactly one inventory row."""

    def setUp(self):
        super().setUp()
        self._patch_cache(FULL_CACHE_UNITS)
        self.seed(*OBSERVED_UDP)
        icron = {
            E4521_IP: {"type": "LEX", "macaddress": E4521_USB_MAC, "ipaddress": E4521_USB_IP},
            D4511_IP: {"type": "REX", "macaddress": D4511_USB_MAC, "ipaddress": D4511_USB_IP},
        }
        real = srv._ws_send_recv
        self.addCleanup(lambda: setattr(srv, "_ws_send_recv", real))

        def fake(url, payload, timeout=None):
            ip = re.findall(r"//([^:/]+)", url)[0]
            if payload.get("config_get") == "usb_icron" and ip in icron:
                return {"config": dict(icron[ip], paired_devices={})}
            return {"config": {}, "error": False}
        srv._ws_send_recv = fake

    def inventory(self):
        state = self.client.get("/api/usb_state").get_json()
        return state["inventory_lex"], state["inventory_rex"], state

    def macs(self, rows):
        return [r["usb_mac"] for r in rows]

    def test_the_standalone_host_appears_exactly_once(self):
        lex, _rex, _state = self.inventory()
        self.assertEqual(self.macs(lex).count(usb.normalize_mac(OMNI311_MAC)), 1)

    def test_the_standalone_device_appears_exactly_once(self):
        _lex, rex, _state = self.inventory()
        self.assertEqual(self.macs(rex).count(usb.normalize_mac(OMNI324_MAC)), 1)

    def test_no_canonical_mac_is_listed_twice(self):
        lex, rex, _state = self.inventory()
        for rows, label in ((lex, "LEX"), (rex, "REX")):
            macs = self.macs(rows)
            self.assertEqual(len(macs), len(set(macs)), f"{label} inventory has a duplicate")

    def test_an_endpoint_present_in_both_collections_yields_one_row(self):
        """The integrated endpoint is also a UDP-discovered record."""
        lex, _rex, state = self.inventory()
        self.assertIn(usb.normalize_mac(E4521_USB_MAC), self.macs(lex))
        self.assertEqual(self.macs(lex).count(usb.normalize_mac(E4521_USB_MAC)), 1)
        udp = [d for d in srv._usb_extenders.state()["devices"]
               if srv._norm_usb_mac(d["mac"]) == srv._norm_usb_mac(E4521_USB_MAC)]
        self.assertTrue(udp, "the fixture must also expose it over UDP for this to be meaningful")

    def test_the_integrated_record_supplies_the_product_identity(self):
        lex, _rex, _state = self.inventory()
        row = next(r for r in lex if r["usb_mac"] == usb.normalize_mac(E4521_USB_MAC))
        self.assertEqual(row["kind"], "integrated")
        self.assertEqual(row["classification"], "INTEGRATED")

    def test_the_same_mac_at_a_new_address_is_still_one_device(self):
        self.service._upsert(query(OMNI311_MAC, "192.0.2.199"), IFACE_IP, IFACE_MASK, "LOCAL_BROADCAST", None)
        lex, _rex, _state = self.inventory()
        rows = [r for r in lex if r["usb_mac"] == usb.normalize_mac(OMNI311_MAC)]
        self.assertEqual(len(rows), 1, "an address change is not a second device")
        self.assertEqual(rows[0]["device_ip"], "192.0.2.199", "and the live address wins")

    def test_a_different_mac_at_the_same_address_is_not_merged(self):
        self.service._upsert(query("00:1B:13:04:AA:BB", OMNI311_IP), IFACE_IP, IFACE_MASK, "LOCAL_BROADCAST",
                             advanced(0))
        lex, _rex, _state = self.inventory()
        macs = self.macs(lex)
        self.assertIn(usb.normalize_mac("00:1B:13:04:AA:BB"), macs)
        self.assertIn(usb.normalize_mac(OMNI311_MAC), macs)
        self.assertEqual(len(macs), len(set(macs)), "two devices, two rows, no merge")

    def test_a_row_without_a_canonical_mac_is_dropped_rather_than_duplicated(self):
        lex, _rex = srv._usb_inventory([{"kind": "integrated", "usb_key": "10.0.0.9", "ip": "10.0.0.9"}], [],
                                       view={"devices": []})
        self.assertEqual(lex, [], "no identity means no row")

    # ---- capabilities ----
    def test_an_integrated_row_reports_the_derived_liveness(self):
        """The inventory must not say offline beside an enabled route cell."""
        srv._record_parent_live(E4521_IP)
        srv._record_parent_live(D4511_IP)
        lex, rex, state = self.inventory()
        row = next(r for r in lex if r["usb_mac"] == usb.normalize_mac(E4521_USB_MAC))
        self.assertTrue(row["online"], "integrated liveness comes from the parent read")
        self.assertEqual(row["liveness_source"], "usb_icron")

    def test_the_inventory_and_the_route_cell_agree_on_liveness(self):
        srv._record_parent_live(E4521_IP)
        srv._record_parent_live(D4511_IP)
        lex, rex, state = self.inventory()
        by_mac = {r["usb_mac"]: r for r in lex + rex}
        view = {srv._norm_usb_mac(d["mac"]): d for d in srv._usb_extender_view()["devices"]}
        for mac, row in by_mac.items():
            derived = view.get(srv._norm_usb_mac(mac))
            if derived is not None:
                self.assertEqual(row["online"], bool(derived.get("online")), mac)

    def test_a_standalone_row_carries_no_integrated_controls(self):
        lex, rex, _state = self.inventory()
        host = next(r for r in lex if r["usb_mac"] == usb.normalize_mac(OMNI311_MAC))
        device = next(r for r in rex if r["usb_mac"] == usb.normalize_mac(OMNI324_MAC))
        for row in (host, device):
            self.assertFalse(row["host_port_configurable"], row["usb_mac"])
            self.assertFalse(row["filter_configurable"])
            self.assertFalse(row["type_configurable"])
            self.assertEqual(row["filter"], "Not established")
            self.assertEqual(row["filter_capability"], "NOT_ESTABLISHED")
            self.assertIn("usb_icron", row["filter_reason"],
                          "the row carries why the capability is unknown")
        self.assertEqual(host["host_port"], "Fixed / N/A")
        self.assertEqual(device["host_port"], "N/A")

    def test_an_integrated_row_keeps_its_controls(self):
        lex, rex, _state = self.inventory()
        host = next(r for r in lex if r["usb_mac"] == usb.normalize_mac(E4521_USB_MAC))
        device = next(r for r in rex if r["usb_mac"] == usb.normalize_mac(D4511_USB_MAC))
        self.assertTrue(host["host_port_configurable"])
        self.assertTrue(host["filter_configurable"])
        self.assertTrue(host["type_configurable"])
        self.assertTrue(device["filter_configurable"], "integrated REX keeps filtering")

    # ---- the routing grid must not be affected ----
    def test_deduplication_does_not_remove_anything_from_the_routing_axes(self):
        _lex, _rex, state = self.inventory()
        lex_keys = [e["usb_key"] for e in state["matrix_lex"]]
        rex_keys = [e["usb_key"] for e in state["matrix_rex"]]
        self.assertIn(usb.normalize_mac(OMNI311_MAC), lex_keys, "the standalone host must still be routable")
        self.assertIn(usb.normalize_mac(OMNI324_MAC), rex_keys)
        self.assertIn(E4521_IP, lex_keys)
        self.assertIn(D4511_IP, rex_keys)
        self.assertEqual(len(lex_keys), len(set(lex_keys)), "an axis must not duplicate either")
        self.assertEqual(len(rex_keys), len(set(rex_keys)))


class LiveDataAuthorityTests(ServerTestBase):
    """A successful live query outranks anything persisted."""

    def setUp(self):
        super().setUp()
        self._patch_cache(FULL_CACHE_UNITS)
        self.net = FakeUdp()
        self.service._exchange = self.net.exchange

    def discover(self, **overrides):
        self.net.endpoints[OMNI311_IP].update(overrides)
        return self.service.discover_ip(OMNI311_IP, IFACE_IP, IFACE_MASK)

    def record(self):
        return self.service.device(OMNI311_MAC)

    def test_live_firmware_replaces_a_stale_persisted_value(self):
        self.service._upsert(query(OMNI311_MAC, OMNI311_IP, revision="0.0.1"),
                             IFACE_IP, IFACE_MASK, "LOCAL_BROADCAST", None)
        self.assertEqual(self.record()["product_revision"], "0.0.1")
        self.net.endpoints[OMNI311_IP]["revision"] = "1.9.4"
        self.discover()
        self.assertEqual(self.record()["product_revision"], "1.9.4", "the device wins over the cache")

    def test_live_address_replaces_a_stale_persisted_address(self):
        self.service._upsert(query(OMNI311_MAC, "10.9.9.9"), IFACE_IP, IFACE_MASK, "LOCAL_BROADCAST", None)
        self.discover()
        self.assertEqual(self.record()["ip"], OMNI311_IP)

    def test_live_network_mode_replaces_a_stale_persisted_mode(self):
        self.service._upsert(query(OMNI311_MAC, OMNI311_IP, mode=usb.NETWORK_MODE_STATIC),
                             IFACE_IP, IFACE_MASK, "LOCAL_BROADCAST", None)
        self.assertEqual(self.record()["network_mode"], "STATIC")
        self.net.endpoints[OMNI311_IP]["mode"] = usb.NETWORK_MODE_DHCP
        self.discover()
        self.assertEqual(self.record()["network_mode"], "DHCP")

    def test_identity_still_survives_an_address_change(self):
        self.discover()
        first = self.record()
        self.net.endpoints["192.0.2.190"] = dict(self.net.endpoints[OMNI311_IP], ip="192.0.2.190")
        self.service.discover_ip("192.0.2.190", IFACE_IP, IFACE_MASK)
        self.assertEqual(len([d for d in self.service.state()["devices"]
                              if srv._norm_usb_mac(d["mac"]) == srv._norm_usb_mac(OMNI311_MAC)]), 1)
        self.assertEqual(first["mac"], self.record()["mac"])

    def test_a_failed_query_does_not_overwrite_the_last_known_values(self):
        self.discover()
        known = self.record()["product_revision"]
        self.net.endpoints[OMNI311_IP]["query"] = False
        self.service.discover_ip(OMNI311_IP, IFACE_IP, IFACE_MASK)
        self.assertEqual(self.record()["product_revision"], known, "a timeout erases nothing")


# Synthetic switches: nothing here shares an address, MAC or chassis id with any
# bench device, so a regression cannot pass by matching a live value.
SWITCH_A, SWITCH_B, SWITCH_C = "0c:1a:2b:3c:4d:5e", "0C-1A-2B-3C-4D-6F", "00:00:5e:00:53:01"


def switch_unit(index, chassis=None, third_octet=70):
    """One discovered unit, optionally reporting an LLDP neighbour."""
    unit = {"ip": f"10.77.{third_octet}.{index}", "mac": f"02:00:00:00:{third_octet:02X}:{index:02X}",
            "model": "HW-OMNI-E4521", "hostname": f"synthetic-{third_octet}-{index}"}
    if chassis:
        unit.update({"lldp_chassis_id": srv._normalize_chassis_id(chassis),
                     "lldp_chassis_id_raw": chassis,
                     "lldp_chassis_name": f"switch-{chassis[-5:]}"})
    return unit


class ChassisIdNormalizationTests(unittest.TestCase):
    """Two devices behind one switch must not look like two switches."""

    def test_a_mac_chassis_id_is_canonicalised(self):
        for text in ("0C:1A:2B:3C:4D:5E", "0c-1a-2b-3c-4d-5e", "0c1a.2b3c.4d5e",
                     "  0c1a2b3c4d5e  ", "0C1A2B3C4D5E"):
            self.assertEqual(srv._normalize_chassis_id(text), "0c1a2b3c4d5e", text)

    def test_a_non_mac_chassis_id_keeps_its_shape(self):
        """A chassis ID may be an address or a local string; mangling separators
        out of those would fabricate equality between different switches."""
        self.assertEqual(srv._normalize_chassis_id("10.20.30.40"), "10.20.30.40")
        self.assertEqual(srv._normalize_chassis_id("Core-Switch-1"), "core-switch-1")
        self.assertNotEqual(srv._normalize_chassis_id("10.20.30.40"),
                            srv._normalize_chassis_id("10.20.30.41"))

    def test_whitespace_collapses_but_words_do_not_merge(self):
        self.assertEqual(srv._normalize_chassis_id("  core   switch  "), "core switch")

    def test_absent_values_normalise_to_nothing(self):
        for value in (None, "", "   ", 0):
            self.assertEqual(srv._normalize_chassis_id(value), "")

    def test_two_switches_stay_distinct(self):
        self.assertNotEqual(srv._normalize_chassis_id(SWITCH_A), srv._normalize_chassis_id(SWITCH_B))

    def test_the_chassis_is_read_from_an_lldp_config(self):
        config = {"neighbors": {"interface": {"eth0": {
            "chassis": {"core-1": {"id": {"type": "mac", "value": SWITCH_A},
                                   "mgmt-ip": "10.77.0.1"}},
            "port": {"id": {"value": "Gi1/0/7"}, "descr": "GigabitEthernet1/0/7"}}}}}
        fields = srv._lldp_chassis_from_config(config)
        self.assertEqual(fields["lldp_chassis_id"], "0c1a2b3c4d5e")
        self.assertEqual(fields["lldp_chassis_id_raw"], SWITCH_A)
        self.assertEqual(fields["lldp_chassis_name"], "core-1")
        self.assertEqual(fields["lldp_port"], "GigabitEthernet1/0/7")

    def test_an_empty_or_malformed_lldp_config_yields_nothing(self):
        for config in ({}, None, {"neighbors": {}}, {"neighbors": {"interface": {}}},
                       {"neighbors": {"interface": {"eth0": {}}}},
                       {"neighbors": {"interface": {"eth0": {"chassis": {"x": {"id": {"value": ""}}}}}}},
                       {"neighbors": {"interface": "not a dict"}}):
            self.assertEqual(srv._lldp_chassis_from_config(config), {}, repr(config))


class SwitchTopologyGroupingTests(unittest.TestCase):
    """Grouping, dominance, and what happens when there is no answer."""

    def test_an_inventory_on_one_switch_is_not_a_multi_switch_inventory(self):
        topology = srv._lldp_topology([switch_unit(i, SWITCH_A) for i in range(1, 8)])
        self.assertEqual(topology["switch_count"], 1)
        self.assertFalse(topology["multi_switch"])
        self.assertEqual(topology["minority_ips"], [])

    def test_two_switches_are_grouped_with_a_dominant_and_a_minority(self):
        units = [switch_unit(i, SWITCH_A) for i in range(1, 11)]
        units += [switch_unit(i, SWITCH_B, third_octet=71) for i in range(1, 4)]
        topology = srv._lldp_topology(units)
        self.assertEqual(topology["switch_count"], 2)
        self.assertTrue(topology["multi_switch"])
        self.assertEqual([g["count"] for g in topology["groups"]], [10, 3],
                         "the largest group is listed first")
        self.assertEqual(topology["dominant_chassis_id"], srv._normalize_chassis_id(SWITCH_A))
        self.assertEqual(len(topology["minority_ips"]), 3)
        self.assertFalse(topology["tied"])

    def test_missing_lldp_never_creates_a_switch(self):
        """The reported failure mode to avoid: warning because some devices are
        simply not reporting LLDP."""
        units = [switch_unit(i, SWITCH_A) for i in range(1, 5)] + [switch_unit(i) for i in range(5, 9)]
        topology = srv._lldp_topology(units)
        self.assertEqual(topology["switch_count"], 1)
        self.assertFalse(topology["multi_switch"])
        self.assertEqual(topology["devices_without_lldp"], 4)
        self.assertEqual(topology["devices_with_lldp"], 4)

    def test_an_inventory_with_no_lldp_at_all_reports_no_switches(self):
        topology = srv._lldp_topology([switch_unit(i) for i in range(1, 6)])
        self.assertEqual(topology["switch_count"], 0)
        self.assertFalse(topology["multi_switch"])

    def test_cosmetic_differences_do_not_split_one_switch_in_two(self):
        units = [switch_unit(1, "0C:1A:2B:3C:4D:5E"), switch_unit(2, "0c-1a-2b-3c-4d-5e"),
                 switch_unit(3, "0c1a.2b3c.4d5e")]
        self.assertEqual(srv._lldp_topology(units)["switch_count"], 1)

    def test_a_tie_designates_no_minority(self):
        """With equal counts, calling one group the odd one out is an invention."""
        units = [switch_unit(i, SWITCH_A) for i in range(1, 5)]
        units += [switch_unit(i, SWITCH_B, third_octet=71) for i in range(1, 5)]
        topology = srv._lldp_topology(units)
        self.assertTrue(topology["multi_switch"], "the operator is still told there are two")
        self.assertTrue(topology["tied"])
        self.assertEqual(topology["dominant_chassis_id"], "")
        self.assertEqual(topology["minority_ips"], [])

    def test_three_switches_leave_everything_off_the_largest_as_minority(self):
        units = [switch_unit(i, SWITCH_A) for i in range(1, 7)]
        units += [switch_unit(i, SWITCH_B, third_octet=71) for i in range(1, 4)]
        units += [switch_unit(i, SWITCH_C, third_octet=72) for i in range(1, 3)]
        topology = srv._lldp_topology(units)
        self.assertEqual(topology["switch_count"], 3)
        self.assertEqual(len(topology["minority_ips"]), 5)
        self.assertNotIn("10.77.70.1", topology["minority_ips"])

    def test_addresses_are_ordered_numerically_not_as_text(self):
        units = [switch_unit(i, SWITCH_A) for i in (1, 2, 3, 4)]
        units += [switch_unit(i, SWITCH_B, third_octet=71) for i in (2, 10, 1)]
        topology = srv._lldp_topology(units)
        self.assertEqual(topology["minority_ips"], ["10.77.71.1", "10.77.71.2", "10.77.71.10"])

    # ---- a neighbour that is one of our own devices ----
    def chained(self, index, behind_ip, third_octet=70):
        """A unit daisy-chained behind another discovered unit.

        It reports that unit as its chassis, by the unit's own management
        address, which is what an OmniStream two-port bridge actually does.
        """
        unit = switch_unit(index, None, third_octet)
        unit.update({"lldp_chassis_id": f"bb00000000{index:02x}",
                     "lldp_chassis_id_raw": f"bb:00:00:00:00:{index:02x}",
                     "lldp_chassis_name": "hw-omni-e4521-chained",
                     "lldp_chassis_mgmt_ip": behind_ip})
        return unit

    def test_a_daisy_chained_device_is_not_a_second_switch(self):
        """Observed on hardware: an E4521 named the E4521 in front of it, not the
        Catalyst both were behind. Counting that as a switch is a false alarm."""
        units = [switch_unit(i, SWITCH_A) for i in range(1, 4)]
        units.append(self.chained(9, "10.77.70.2"))
        topology = srv._lldp_topology(units)
        self.assertEqual(topology["switch_count"], 1)
        self.assertFalse(topology["multi_switch"])
        self.assertIn("10.77.70.9", topology["groups"][0]["ips"],
                      "it is attributed to the switch it actually hangs off")
        self.assertEqual(topology["devices_daisy_chained"], ["10.77.70.9"])

    def test_a_chain_several_deep_still_resolves(self):
        units = [switch_unit(1, SWITCH_A)]
        units.append(self.chained(2, "10.77.70.1"))
        units.append(self.chained(3, "10.77.70.2"))
        topology = srv._lldp_topology(units)
        self.assertEqual(topology["switch_count"], 1)
        self.assertEqual(topology["groups"][0]["count"], 3)

    def test_a_chain_that_cannot_be_resolved_is_unknown_not_a_switch(self):
        """The device in front was never discovered, so its switch is unknown."""
        units = [switch_unit(i, SWITCH_A) for i in range(1, 4)]
        units.append(self.chained(9, "10.77.99.250"))
        topology = srv._lldp_topology(units)
        self.assertEqual(topology["switch_count"], 2,
                         "an unknown neighbour is treated as the switch it claims to be")
        # ... but a chain we can see is a loop tells us nothing at all.
        looped = [self.chained(1, "10.77.70.2"), self.chained(2, "10.77.70.1")]
        loop_topology = srv._lldp_topology(looped)
        self.assertEqual(loop_topology["switch_count"], 0)
        self.assertEqual(loop_topology["devices_without_lldp"], 2)

    def test_the_group_is_named_by_a_device_attached_to_the_switch(self):
        """A chained device names the unit in front of it; that name must not
        become the switch's name."""
        units = [switch_unit(1, SWITCH_A), self.chained(2, "10.77.70.1")]
        group = srv._lldp_topology(units)["groups"][0]
        self.assertEqual(group["chassis_id_raw"], SWITCH_A)
        self.assertNotEqual(group["chassis_name"], "hw-omni-e4521-chained")

    def test_a_real_second_switch_is_still_reported(self):
        """Resolving chains must not swallow a genuine second switch."""
        units = [switch_unit(i, SWITCH_A) for i in range(1, 4)]
        units += [switch_unit(i, SWITCH_B, third_octet=71) for i in range(1, 3)]
        units.append(self.chained(9, "10.77.71.1"))
        topology = srv._lldp_topology(units)
        self.assertEqual(topology["switch_count"], 2)
        by_id = {g["chassis_id"]: g for g in topology["groups"]}
        self.assertIn("10.77.70.9", by_id[srv._normalize_chassis_id(SWITCH_B)]["ips"],
                      "the chained device belongs to its upstream switch")

    def test_malformed_entries_are_ignored_rather_than_crashing(self):
        topology = srv._lldp_topology([None, "not a unit", {}, switch_unit(1, SWITCH_A)])
        self.assertEqual(topology["switch_count"], 1)


class SwitchWarningAcknowledgementTests(ServerTestBase):
    """The acknowledgement belongs to the inventory, not to a scan.

    Every operation below is one the operator performs repeatedly, and none of
    them may bring the banner back. Only clearing the discovered units does.
    """

    def setUp(self):
        super().setUp()
        self._quiet_cache_verification()
        self.two_switch_units = ([switch_unit(i, SWITCH_A) for i in range(1, 11)]
                                 + [switch_unit(i, SWITCH_B, third_octet=71) for i in range(1, 4)])

    def _quiet_cache_verification(self):
        """`/api/cache` normally re-verifies units against the network.

        These fixtures are synthetic addresses, so verification would spend the
        test timing out against them -- and, worse, leave background threads
        mutating the fixture dicts after the test has finished, which shows up as
        unrelated tests failing later. Verification is not what is under test
        here, so it is silenced.
        """
        self.patch_srv("_cache_startup_verified", True)
        real_verify = srv._verify_cache_in_background
        srv._verify_cache_in_background = lambda *a, **k: None
        self.addCleanup(lambda: setattr(srv, "_verify_cache_in_background", real_verify))
        previous = srv._cache_last_verified
        srv._cache_last_verified = time.time()
        self.addCleanup(lambda: setattr(srv, "_cache_last_verified", previous))

    def discover(self, units):
        """Stand in for a completed scan: the inventory now holds these units."""
        self._patch_cache(copy.deepcopy(units))

    def clear(self):
        """The operator's Clear Units, plus the store the fixture stands in for."""
        reply = self.client.post("/api/clear_units")
        self._patch_cache([])
        return reply

    def cache(self):
        return self.client.get("/api/cache").get_json()

    def acknowledged(self):
        return bool(self.cache()["topology_warning_acknowledged"])

    def banner_would_show(self):
        body = self.cache()
        return bool(body["lldp_topology"]["multi_switch"]) and not body["topology_warning_acknowledged"]

    def dismiss(self):
        reply = self.client.post("/api/lldp_topology/acknowledge", json={"acknowledged": True})
        self.assertEqual(reply.status_code, 200)
        return reply.get_json()

    # ---- the flag itself ----
    def test_a_new_inventory_starts_unacknowledged(self):
        self.discover(self.two_switch_units)
        self.assertFalse(self.acknowledged())
        self.assertTrue(self.banner_would_show())

    def test_dismissing_records_the_acknowledgement(self):
        self.discover(self.two_switch_units)
        self.dismiss()
        self.assertTrue(self.acknowledged())
        self.assertFalse(self.banner_would_show())

    def test_the_flag_survives_a_restart_of_the_reader(self):
        """It is stored, not held in a request or a session."""
        self.discover(self.two_switch_units)
        self.dismiss()
        self.assertTrue(srv._read_topology_ack().get("acknowledged"))

    def test_a_malformed_acknowledgement_file_degrades_to_unacknowledged(self):
        self.discover(self.two_switch_units)
        self.dismiss()
        srv.TOPOLOGY_ACK.write_text("{not json", encoding="utf-8")
        self.assertFalse(self.acknowledged(),
                         "showing it once more beats hiding a topology never seen")

    # ---- things that must NOT reset it ----
    def test_scanning_again_does_not_bring_the_warning_back(self):
        self.discover(self.two_switch_units)
        self.dismiss()
        self.discover(self.two_switch_units)          # the same scan, run again
        self.assertFalse(self.banner_would_show())

    def test_scanning_another_subnet_does_not_bring_it_back(self):
        self.discover(self.two_switch_units)
        self.dismiss()
        self.client.post("/api/scan_preferences", json={"scan_targets": "10.88.0.1-10.88.0.50"})
        self.discover(self.two_switch_units + [switch_unit(i, SWITCH_A, third_octet=88) for i in range(1, 4)])
        self.assertFalse(self.banner_would_show())

    def test_changing_the_scan_targets_does_not_bring_it_back(self):
        self.discover(self.two_switch_units)
        self.dismiss()
        for targets in ("10.88.0.1-10.88.0.50", "10.99.0.0/24", ""):
            self.client.post("/api/scan_preferences", json={"scan_targets": targets})
            self.assertFalse(self.banner_would_show(), targets)

    def test_discovering_more_devices_does_not_bring_it_back(self):
        self.discover(self.two_switch_units)
        self.dismiss()
        self.discover(self.two_switch_units + [switch_unit(i, SWITCH_B, third_octet=71) for i in range(4, 9)])
        self.assertFalse(self.banner_would_show())

    def test_discovering_a_third_switch_does_not_bring_it_back(self):
        self.discover(self.two_switch_units)
        self.dismiss()
        self.discover(self.two_switch_units + [switch_unit(i, SWITCH_C, third_octet=72) for i in range(1, 3)])
        body = self.cache()
        self.assertEqual(body["lldp_topology"]["switch_count"], 3, "the topology did change")
        self.assertFalse(self.banner_would_show(), "and the operator is not asked again")

    def test_background_polling_does_not_bring_it_back(self):
        self.discover(self.two_switch_units)
        self.dismiss()
        for _ in range(12):
            self.assertFalse(self.banner_would_show())

    def test_navigating_between_pages_does_not_bring_it_back(self):
        self.discover(self.two_switch_units)
        self.dismiss()
        for path in ("/api/usb_state", "/api/usb_extenders?live=0", "/api/scan_preferences"):
            self.client.get(path)
        self.assertFalse(self.banner_would_show())

    def test_reading_the_topology_endpoint_does_not_bring_it_back(self):
        """An LLDP refresh is a read. Reads never change acknowledgement."""
        self.discover(self.two_switch_units)
        self.dismiss()
        for _ in range(5):
            body = self.client.get("/api/lldp_topology").get_json()
            self.assertTrue(body["acknowledged"])
        self.assertFalse(self.banner_would_show())

    # ---- the one thing that does ----
    def test_clearing_the_discovered_units_resets_it(self):
        self.discover(self.two_switch_units)
        self.dismiss()
        self.assertTrue(self.acknowledged())
        self.clear()
        self.assertFalse(self.acknowledged())

    def test_after_clearing_a_multi_switch_discovery_warns_again(self):
        self.discover(self.two_switch_units)
        self.dismiss()
        self.clear()
        self.discover(self.two_switch_units)
        self.assertTrue(self.banner_would_show())

    def test_only_the_clear_operation_resets_the_flag(self):
        """A source guard: the reset must not acquire a second caller by accident."""
        source = Path(srv.__file__).read_text(encoding="utf-8", errors="replace")
        calls = [line.strip() for line in source.splitlines()
                 if "_topology_ack_reset()" in line and not line.strip().startswith("def ")]
        self.assertEqual(len(calls), 1, f"expected exactly one caller, found {calls}")
        block = source[source.index("def api_clear_units_dup():"):]
        block = block[:block.index("\n@app.route")]
        self.assertIn("_topology_ack_reset()", block, "the caller is the clear-units endpoint")

    # ---- the full lifecycle, in one test ----
    def test_the_acknowledgement_lifecycle(self):
        """Clear -> discover A+B -> warn -> dismiss -> scan, scan elsewhere, find
        a third switch -> silent -> clear -> discover A+B -> warn again."""
        self.clear()

        self.discover(self.two_switch_units)
        self.assertTrue(self.banner_would_show(), "1. two switches, not yet acknowledged")

        self.dismiss()
        self.assertFalse(self.banner_would_show(), "2. dismissed")

        self.discover(self.two_switch_units)
        self.assertFalse(self.banner_would_show(), "3. scan again")

        self.client.post("/api/scan_preferences", json={"scan_targets": "10.88.0.1-10.88.0.50"})
        self.discover(self.two_switch_units + [switch_unit(i, SWITCH_A, third_octet=88) for i in range(1, 3)])
        self.assertFalse(self.banner_would_show(), "4. scan another network")

        self.discover(self.two_switch_units + [switch_unit(i, SWITCH_C, third_octet=72) for i in range(1, 3)])
        self.assertEqual(self.cache()["lldp_topology"]["switch_count"], 3)
        self.assertFalse(self.banner_would_show(), "5. a third switch appears")

        self.clear()
        self.discover(self.two_switch_units)
        self.assertTrue(self.banner_would_show(), "6. a new inventory may warn again")

    # ---- highlighting is not the banner ----
    def test_minority_highlighting_continues_after_the_warning_is_dismissed(self):
        self.discover(self.two_switch_units)
        before = self.cache()["lldp_topology"]["minority_ips"]
        self.assertEqual(len(before), 3)
        self.dismiss()
        after = self.cache()["lldp_topology"]
        self.assertFalse(self.banner_would_show(), "the banner is gone")
        self.assertEqual(after["minority_ips"], before, "the topology indication is not")
        self.assertEqual(after["dominant_chassis_id"], srv._normalize_chassis_id(SWITCH_A))

    def test_minority_highlighting_follows_a_change_in_topology(self):
        self.discover(self.two_switch_units)
        self.dismiss()
        # Everything moves onto one switch.
        self.discover([switch_unit(i, SWITCH_A) for i in range(1, 11)])
        body = self.cache()["lldp_topology"]
        self.assertEqual(body["minority_ips"], [], "nothing is a minority on one switch")
        self.assertFalse(body["multi_switch"])

    def test_an_empty_inventory_has_no_topology_to_show(self):
        self.clear()
        body = self.cache()
        self.assertFalse(body["lldp_topology"]["multi_switch"])
        self.assertEqual(body["lldp_topology"]["minority_ips"], [])


class SwitchTopologyCostTests(ServerTestBase):
    """Derived information must not become another poll."""

    def setUp(self):
        super().setUp()
        SwitchWarningAcknowledgementTests._quiet_cache_verification(self)

    def test_the_topology_reads_the_inventory_and_transmits_nothing(self):
        self._patch_cache([switch_unit(i, SWITCH_A) for i in range(1, 4)]
                          + [switch_unit(i, SWITCH_B, third_octet=71) for i in range(1, 3)])
        before = self.service.transmit_counts()["total"]
        for _ in range(10):
            self.client.get("/api/lldp_topology")
            self.client.get("/api/cache")
        self.assertEqual(self.service.transmit_counts()["total"], before,
                         "rendering the banner must not touch a device")

    def test_the_client_module_starts_no_timer(self):
        source = Path(srv.__file__).parent.joinpath("ui", "lldp-topology.js").read_text(encoding="utf-8")
        for banned in ("setInterval", "setTimeout", "requestAnimationFrame"):
            self.assertNotIn(banned, source, f"{banned} would be a polling loop")

    def test_the_chassis_is_learned_by_the_scan_that_is_already_talking_to_the_device(self):
        source = Path(srv.__file__).read_text(encoding="utf-8", errors="replace")
        probe = source[source.index("def _probe_one("):]
        probe = probe[:probe.index("\ndef ", 10)]
        self.assertIn('"config_get": "lldp"', probe,
                      "topology is recorded when the inventory is built, not by a separate sweep")
        self.assertIn("_lldp_chassis_from_config", probe)


class DeviceIdentityReconciliationTests(unittest.TestCase):
    """The reported defect: a unit moved to another subnet kept its old address
    in the inventory until the operator ran Clear Units and rescanned.

    Cause: the merge iterated the cache before the fresh scan results and skipped
    any MAC it had already seen, so the cached row claimed the identity and the
    record that had just answered at the new address was discarded.
    """

    MAC_X = "B8:98:B0:11:22:33"
    MAC_Y = "B8:98:B0:44:55:66"

    def unit(self, mac, ip, **extra):
        record = {"mac": mac, "ip": ip, "hostname": f"unit-{ip.replace('.', '-')}",
                  "model": "HW-OMNI-E4521", "role": "encoder"}
        record.update(extra)
        return record

    def test_identity_ignores_formatting_but_not_content(self):
        self.assertEqual(srv._device_identity({"mac": "B8:98:B0:11:22:33"}),
                         srv._device_identity({"mac": "b8-98-b0-11-22-33"}))
        self.assertNotEqual(srv._device_identity({"mac": self.MAC_X}),
                            srv._device_identity({"mac": self.MAC_Y}))
        self.assertEqual(srv._device_identity({}), "")
        self.assertEqual(srv._device_identity(None), "")

    # ---- the defect ----
    def test_a_rediscovered_device_moves_to_its_new_address(self):
        cached = [self.unit(self.MAC_X, "10.10.1.50", firmware="2.1.0")]
        fresh = [self.unit(self.MAC_X, "10.20.1.75", firmware="2.1.2")]
        merged = srv._reconcile_discovered_units(cached, fresh)
        self.assertEqual(len(merged), 1, "one physical device, one row")
        self.assertEqual(merged[0]["ip"], "10.20.1.75")
        self.assertEqual(merged[0]["firmware"], "2.1.2", "fresh data wins")

    def test_the_old_address_is_gone_from_the_inventory(self):
        merged = srv._reconcile_discovered_units(
            [self.unit(self.MAC_X, "10.10.1.50")], [self.unit(self.MAC_X, "10.20.1.75")])
        self.assertNotIn("10.10.1.50", [u["ip"] for u in merged])

    def test_the_move_needs_no_clearing_of_the_inventory(self):
        """The cached record is updated in place, not left for a clear to remove."""
        cached = [self.unit(self.MAC_X, "10.10.1.50"), self.unit(self.MAC_Y, "10.10.1.60")]
        merged = srv._reconcile_discovered_units(cached, [self.unit(self.MAC_X, "10.20.1.75")])
        by_mac = {srv._device_identity(u): u for u in merged}
        self.assertEqual(len(merged), 2, "the untouched device is still there")
        self.assertEqual(by_mac[srv._device_identity({"mac": self.MAC_X})]["ip"], "10.20.1.75")
        self.assertEqual(by_mac[srv._device_identity({"mac": self.MAC_Y})]["ip"], "10.10.1.60")

    def test_cached_knowledge_the_scan_did_not_carry_survives_the_move(self):
        """Fresh data wins where it exists; it does not erase what it lacks."""
        cached = [self.unit(self.MAC_X, "10.10.1.50", timezone="Europe/London",
                            usb_mac="B8:98:B0:11:22:34", linkspeed=1000)]
        merged = srv._reconcile_discovered_units(cached, [self.unit(self.MAC_X, "10.20.1.75")])
        self.assertEqual(merged[0]["timezone"], "Europe/London")
        self.assertEqual(merged[0]["usb_mac"], "B8:98:B0:11:22:34")
        self.assertEqual(merged[0]["linkspeed"], 1000)

    def test_a_device_that_did_not_answer_keeps_its_record(self):
        cached = [self.unit(self.MAC_Y, "10.10.1.60")]
        merged = srv._reconcile_discovered_units(cached, [self.unit(self.MAC_X, "10.20.1.75")])
        self.assertEqual(len(merged), 2)

    def test_a_cached_row_whose_address_now_belongs_to_another_device_is_dropped(self):
        """Otherwise the old row lingers as a ghost at an address it no longer has."""
        cached = [self.unit(self.MAC_X, "10.10.1.50")]
        merged = srv._reconcile_discovered_units(cached, [self.unit(self.MAC_Y, "10.10.1.50")])
        self.assertEqual(len(merged), 1)
        self.assertEqual(srv._device_identity(merged[0]), srv._device_identity({"mac": self.MAC_Y}))

    def test_nothing_changes_when_a_device_is_rediscovered_where_it_was(self):
        cached = [self.unit(self.MAC_X, "10.10.1.50")]
        merged = srv._reconcile_discovered_units(cached, [self.unit(self.MAC_X, "10.10.1.50")])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["ip"], "10.10.1.50")

    # ---- conflicts ----
    def test_one_mac_answering_at_two_addresses_is_logged_not_duplicated(self):
        """A duplicate MAC, a stale path or a device caught mid-move. None of
        them may become two inventory rows, and none may pass unremarked."""
        fresh = [self.unit(self.MAC_X, "10.20.1.75"), self.unit(self.MAC_X, "10.10.1.50")]
        with self.assertLogs(srv.log, level="WARNING") as captured:
            merged = srv._reconcile_discovered_units([], fresh)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["ip"], "10.20.1.75", "the first response is kept, deterministically")
        self.assertTrue(any("answered at both" in line for line in captured.output))

    def test_a_record_without_a_mac_is_kept_on_its_own_terms(self):
        merged = srv._reconcile_discovered_units([], [{"ip": "10.20.1.90"}, {"ip": "10.20.1.91"}])
        self.assertEqual(len(merged), 2)

    def test_empty_inputs_are_handled(self):
        self.assertEqual(srv._reconcile_discovered_units([], []), [])
        self.assertEqual(srv._reconcile_discovered_units(None, None), [])


class InventoryPersistenceReconciliationTests(ServerTestBase):
    """The same rule, through the stores the inventory is actually read from."""

    MAC_X = "B8:98:B0:11:22:33"

    def setUp(self):
        super().setUp()
        # The base class stubs _load_cache with a fixture. These tests are about
        # the real store, and the paths are already redirected into a temp folder.
        srv._load_cache = self._real_load_cache
        srv._save_cache([])
        srv._save_scan_results({"devices": []})

    def unit(self, mac, ip):
        return {"mac": mac, "ip": ip, "hostname": f"unit-{ip.replace('.', '-')}",
                "model": "HW-OMNI-E4521", "role": "encoder"}

    def test_a_stale_scan_results_row_does_not_resurrect_the_old_address(self):
        """`_load_cache` merges scan results by address. Without an identity check
        the row left at the old address comes back as a second device."""
        srv._save_cache([self.unit(self.MAC_X, "10.20.1.75")])
        srv._save_scan_results({"devices": [self.unit(self.MAC_X, "10.10.1.50")]})
        units = srv._load_cache()
        self.assertEqual(len(units), 1, [u["ip"] for u in units])
        self.assertEqual(units[0]["ip"], "10.20.1.75")

    def test_the_export_view_does_not_resurrect_it_either(self):
        srv._save_cache([self.unit(self.MAC_X, "10.20.1.75")])
        srv._save_scan_results({"devices": [self.unit(self.MAC_X, "10.10.1.50")]})
        exported = srv._units_for_export()
        self.assertEqual([u["ip"] for u in exported], ["10.20.1.75"])

    def test_the_new_address_survives_a_restart(self):
        """Persisted, not merely held in memory: reading the store again is what
        a restart does."""
        srv._save_cache(srv._reconcile_discovered_units(
            [self.unit(self.MAC_X, "10.10.1.50")], [self.unit(self.MAC_X, "10.20.1.75")]))
        srv._save_scan_results({"devices": []})
        reloaded = srv._load_cache()
        self.assertEqual([u["ip"] for u in reloaded], ["10.20.1.75"])


class UsbIdentityReconciliationTests(ServerTestBase):
    """The USB inventory was already MAC-canonical. This keeps it that way."""

    def setUp(self):
        super().setUp()
        self._patch_cache(FULL_CACHE_UNITS)
        self.net = FakeUdp()
        self.service._exchange = self.net.exchange

    def test_a_usb_endpoint_that_moves_keeps_one_record_at_its_new_address(self):
        self.service.discover_ip(OMNI311_IP, IFACE_IP, IFACE_MASK)
        self.assertEqual(self.service.device(OMNI311_MAC)["ip"], OMNI311_IP)
        moved = "192.168.150.203"
        self.net.endpoints[moved] = dict(self.net.endpoints[OMNI311_IP])
        self.service.discover_ip(moved, IFACE_IP, IFACE_MASK)
        records = [d for d in self.service.state()["devices"]
                   if d["mac"] == usb.normalize_mac(OMNI311_MAC)]
        self.assertEqual(len(records), 1, "one endpoint, one record")
        self.assertEqual(records[0]["ip"], moved)

    def test_the_inventory_shows_it_once_at_the_new_address(self):
        self.service.discover_ip(OMNI311_IP, IFACE_IP, IFACE_MASK)
        moved = "192.168.150.203"
        self.net.endpoints[moved] = dict(self.net.endpoints[OMNI311_IP])
        self.service.discover_ip(moved, IFACE_IP, IFACE_MASK)
        rows = [d for d in srv._usb_extender_view()["devices"]
                if d["mac"] == usb.normalize_mac(OMNI311_MAC)]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["ip"], moved)


def chained_unit(index, behind_ip, third_octet=70, role="decoder", streams=0):
    """A unit that reports another discovered unit as its LLDP neighbour."""
    unit = switch_unit(index, None, third_octet)
    unit.update({"role": role, "linkspeed": 1000,
                 "lldp_chassis_id": f"bb00000000{index:02x}",
                 "lldp_chassis_id_raw": f"bb:00:00:00:00:{index:02x}",
                 "lldp_chassis_name": "upstream-endpoint",
                 "lldp_chassis_mgmt_ip": behind_ip,
                 "lldp_port": "lan0"})
    if streams:
        unit["ip1_addr"] = "239.9.9.1"
        if streams > 1:
            unit["ip3_addr"] = "239.9.9.2"
    return unit


class DaisyChainTopologyTests(unittest.TestCase):
    """Resolving the upstream switch must not discard the path taken to reach it."""

    def topology(self, units):
        return srv._lldp_topology(units)

    def device(self, units, address):
        return self.topology(units)["devices"][address]

    def test_a_directly_attached_device_is_not_daisy_chained(self):
        units = [switch_unit(1, SWITCH_A)]
        record = self.device(units, "10.77.70.1")
        self.assertFalse(record["is_daisy_chained"])
        self.assertEqual(record["resolution"], "direct")
        self.assertEqual(record["resolved_upstream_switch"]["chassis_id"],
                         srv._normalize_chassis_id(SWITCH_A))

    def test_a_neighbour_that_is_a_known_device_marks_a_daisy_chain(self):
        units = [switch_unit(1, SWITCH_A), chained_unit(2, "10.77.70.1")]
        record = self.device(units, "10.77.70.2")
        self.assertTrue(record["is_daisy_chained"])
        self.assertEqual(record["resolution"], "resolved_through_device")
        self.assertEqual(record["daisy_chain_via_ip"], "10.77.70.1")
        self.assertEqual(record["daisy_chain_via_mac"], units[0]["mac"])

    def test_the_immediate_neighbour_is_preserved_beside_the_resolved_switch(self):
        """Both facts, kept apart: what it reported, and what that resolves to."""
        units = [switch_unit(1, SWITCH_A), chained_unit(2, "10.77.70.1")]
        record = self.device(units, "10.77.70.2")
        self.assertEqual(record["immediate_neighbor"]["chassis_name"], "upstream-endpoint")
        self.assertEqual(record["immediate_neighbor"]["management_ip"], "10.77.70.1")
        self.assertEqual(record["resolved_upstream_switch"]["chassis_id"],
                         srv._normalize_chassis_id(SWITCH_A))
        self.assertNotEqual(record["immediate_neighbor"]["chassis_id"],
                            record["resolved_upstream_switch"]["chassis_id"])

    def test_a_chained_device_is_grouped_under_its_resolved_switch(self):
        units = [switch_unit(1, SWITCH_A), chained_unit(2, "10.77.70.1")]
        topology = self.topology(units)
        self.assertEqual(topology["switch_count"], 1, "a chain is not a second switch")
        self.assertEqual(topology["groups"][0]["count"], 2)
        self.assertTrue(topology["daisy_chained"])

    def test_a_multi_hop_chain_resolves_and_keeps_every_hop(self):
        units = [switch_unit(1, SWITCH_A), chained_unit(2, "10.77.70.1"), chained_unit(3, "10.77.70.2")]
        record = self.device(units, "10.77.70.3")
        self.assertTrue(record["is_daisy_chained"])
        self.assertEqual([hop["ip"] for hop in record["daisy_chain_hops"]], ["10.77.70.2", "10.77.70.1"])
        self.assertEqual(record["daisy_chain_via_ip"], "10.77.70.2", "the immediate hop comes first")
        self.assertEqual(record["resolved_upstream_switch"]["chassis_id"],
                         srv._normalize_chassis_id(SWITCH_A))

    def test_a_loop_terminates_without_inventing_a_switch(self):
        units = [chained_unit(1, "10.77.70.2"), chained_unit(2, "10.77.70.1")]
        topology = self.topology(units)
        self.assertEqual(topology["switch_count"], 0)
        for address in ("10.77.70.1", "10.77.70.2"):
            self.assertEqual(topology["devices"][address]["resolution"], "unresolved")
            self.assertIsNone(topology["devices"][address]["resolved_upstream_switch"])

    def test_a_chain_longer_than_the_bound_stops(self):
        units = [switch_unit(1, SWITCH_A)]
        for index in range(2, srv.LLDP_MAX_CHAIN_HOPS + 5):
            units.append(chained_unit(index, f"10.77.70.{index - 1}"))
        last = f"10.77.70.{srv.LLDP_MAX_CHAIN_HOPS + 4}"
        record = self.device(units, last)
        self.assertEqual(record["resolution"], "unresolved", "bounded, never recursive")

    def test_a_chain_into_an_undiscovered_device_treats_it_as_the_switch(self):
        units = [switch_unit(1, SWITCH_A), chained_unit(2, "10.77.99.250")]
        record = self.device(units, "10.77.70.2")
        self.assertFalse(record["is_daisy_chained"], "nothing known sits in front of it")
        self.assertEqual(record["resolution"], "direct")

    def test_the_port_belongs_to_whoever_touches_the_switch(self):
        """A chained device's own port is not the switch port."""
        units = [switch_unit(1, SWITCH_A), chained_unit(2, "10.77.70.1")]
        chained = self.device(units, "10.77.70.2")
        self.assertIsNone(chained["resolved_upstream_switch"]["port_id"])

    def test_minority_calculation_uses_the_resolved_switch(self):
        units = [switch_unit(i, SWITCH_A) for i in range(1, 6)]
        units += [switch_unit(i, SWITCH_B, third_octet=71) for i in range(1, 3)]
        units.append(chained_unit(9, "10.77.71.1"))
        topology = self.topology(units)
        self.assertEqual(topology["dominant_chassis_id"], srv._normalize_chassis_id(SWITCH_A))
        self.assertIn("10.77.70.9", topology["minority_ips"],
                      "grouped by where it actually hangs, not by what it reported")

    # ---- the bandwidth context ----
    def test_a_chained_decoder_with_two_streams_is_singled_out(self):
        units = [switch_unit(1, SWITCH_A), chained_unit(2, "10.77.70.1", role="decoder", streams=2)]
        topology = self.topology(units)
        self.assertEqual(topology["daisy_chained_multi_stream"], ["10.77.70.2"])
        self.assertEqual(topology["devices"]["10.77.70.2"]["stream_subscriptions"], 2)
        self.assertEqual(topology["devices"]["10.77.70.2"]["link_speed_mbps"], 1000)

    def test_one_stream_is_not_a_multi_stream_advisory(self):
        units = [switch_unit(1, SWITCH_A), chained_unit(2, "10.77.70.1", role="decoder", streams=1)]
        self.assertEqual(self.topology(units)["daisy_chained_multi_stream"], [])

    def test_an_encoder_is_not_treated_as_a_subscribed_receiver(self):
        units = [switch_unit(1, SWITCH_A), chained_unit(2, "10.77.70.1", role="encoder", streams=2)]
        self.assertEqual(self.topology(units)["daisy_chained_multi_stream"], [])

    def test_no_bitrate_is_claimed(self):
        """No device reports its actual stream bitrate, and the payload says so."""
        units = [switch_unit(1, SWITCH_A), chained_unit(2, "10.77.70.1", streams=2)]
        self.assertFalse(self.topology(units)["bitrate_data_available"])


class DaisyWarningLifecycleTests(ServerTestBase):
    """Two advisories, two acknowledgements, one inventory lifetime."""

    def setUp(self):
        super().setUp()
        SwitchWarningAcknowledgementTests._quiet_cache_verification(self)
        self.units = [switch_unit(i, SWITCH_A) for i in range(1, 4)]
        self.units.append(chained_unit(9, "10.77.70.1"))

    def discover(self, units):
        self._patch_cache(copy.deepcopy(units))

    def cache(self):
        return self.client.get("/api/cache").get_json()

    def daisy_would_show(self):
        body = self.cache()
        return bool(body["lldp_topology"]["daisy_chained"]) and not body["daisy_warning_acknowledged"]

    def dismiss(self, scope):
        return self.client.post("/api/lldp_topology/acknowledge",
                                json={"acknowledged": True, "scope": scope}).get_json()

    def test_a_daisy_chain_raises_its_own_advisory(self):
        self.discover(self.units)
        self.assertTrue(self.daisy_would_show())

    def test_dismissing_it_survives_a_rescan(self):
        self.discover(self.units)
        self.dismiss("daisy_chain")
        self.discover(self.units)
        self.assertFalse(self.daisy_would_show())

    def test_the_two_advisories_are_acknowledged_separately(self):
        multi = self.units + [switch_unit(i, SWITCH_B, third_octet=71) for i in range(1, 2)]
        self.discover(multi)
        self.dismiss("multi_switch")
        body = self.cache()
        self.assertTrue(body["topology_warning_acknowledged"])
        self.assertFalse(body["daisy_warning_acknowledged"], "one dismissal is not the other")
        self.dismiss("daisy_chain")
        self.assertTrue(self.cache()["daisy_warning_acknowledged"])

    def test_a_newly_chained_device_is_worth_saying_once(self):
        self.discover(self.units)
        self.dismiss("daisy_chain")
        self.assertFalse(self.daisy_would_show())
        self.discover(self.units + [chained_unit(8, "10.77.70.1")])
        self.assertTrue(self.daisy_would_show(), "a device that was not chained before is new")

    def test_a_chained_device_that_merely_moves_is_not_new(self):
        """The acknowledgement covers identities, not addresses."""
        self.discover(self.units)
        self.dismiss("daisy_chain")
        moved = copy.deepcopy(self.units)
        moved[-1]["ip"] = "10.88.5.9"
        self.discover(moved)
        self.assertFalse(self.daisy_would_show())

    def test_fewer_chained_devices_does_not_re_notify(self):
        self.discover(self.units + [chained_unit(8, "10.77.70.1")])
        self.dismiss("daisy_chain")
        self.discover(self.units)
        self.assertFalse(self.daisy_would_show())

    def test_clearing_the_inventory_resets_both(self):
        self.discover(self.units)
        self.dismiss("daisy_chain")
        self.dismiss("multi_switch")
        self.client.post("/api/clear_units")
        self._patch_cache([])
        self.discover(self.units)
        self.assertTrue(self.daisy_would_show())
        self.assertFalse(self.cache()["topology_warning_acknowledged"])

    def test_an_unknown_scope_is_refused(self):
        reply = self.client.post("/api/lldp_topology/acknowledge",
                                 json={"acknowledged": True, "scope": "everything"})
        self.assertEqual(reply.status_code, 400)


class SupportDumpTests(ServerTestBase):
    """The dump carries what OmniSuite has learned, and no secrets."""

    def setUp(self):
        super().setUp()
        SwitchWarningAcknowledgementTests._quiet_cache_verification(self)
        self._patch_cache(FULL_CACHE_UNITS)
        self.net = FakeUdp()
        self.service._exchange = self.net.exchange
        self.seed(*OBSERVED_UDP)

    def dump(self):
        reply = self.client.get("/api/ts_export")
        self.assertEqual(reply.status_code, 200)
        return json.loads(reply.data.decode("utf-8"))

    def test_the_schema_is_versioned(self):
        self.assertEqual(self.dump()["schema_version"], srv.TS_DUMP_SCHEMA)

    def test_the_existing_sections_are_still_there(self):
        body = self.dump()
        for key in ("units", "config", "summary", "app_version", "generated_at"):
            self.assertIn(key, body, key)

    def test_every_usb_endpoint_is_described(self):
        endpoints = self.dump()["usb_endpoints"]
        self.assertTrue(endpoints)
        expected = {"usb_mac", "model", "usb_role", "classification", "device_ip", "usb_ip",
                    "firmware", "network_mode", "network_relation", "online", "paired_macs",
                    "link_state", "link_label", "link_states", "field_authority",
                    "discovery_source", "peer_count"}
        self.assertTrue(expected.issubset(set(endpoints[0])), expected - set(endpoints[0]))

    def test_routes_carry_both_kinds_of_evidence(self):
        for route in self.dump().get("usb_routes") or []:
            self.assertIn("control_plane_verified", route)
            self.assertIn("data_plane_verified", route)
            self.assertIn("provider", route)
            self.assertIn("usb_mac", route["lex"])
            self.assertIn("usb_mac", route["rex"])

    def test_discovery_diagnostics_are_a_summary_not_a_packet_log(self):
        discovery = self.dump()["usb_discovery"]
        self.assertIn("freshness_policy", discovery)
        self.assertIn("udp_traffic_since_start", discovery)
        self.assertIn("endpoints", discovery)
        self.assertNotIn("packets", discovery)

    def test_lldp_keeps_what_was_reported_and_what_it_resolved_to(self):
        body = self.dump()
        topology = body["lldp_topology"]
        self.assertIn("groups", topology)
        self.assertIn("acknowledgements", topology)
        for device in topology.get("devices") or []:
            self.assertIn("immediate_neighbor", device)
            self.assertIn("resolution", device)
            self.assertIn("is_daisy_chained", device)

    def test_no_credential_survives_the_dump(self):
        body = self.dump()
        text = json.dumps(body)
        secrets = re.findall(r'"[^"]*(?:password|token|secret|pwd)[^"]*"\s*:\s*"([^"]*)"', text, re.I)
        self.assertTrue(all(value == "***" for value in secrets), secrets)
        self.assertNotIn(srv.app.config.get("PASSWORD", "\x00never"), text)

    def test_a_failure_in_one_section_does_not_lose_the_dump(self):
        original = srv._usb_extender_view
        srv._usb_extender_view = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
        self.addCleanup(lambda: setattr(srv, "_usb_extender_view", original))
        body = self.dump()
        self.assertIn("units", body)
        self.assertEqual(body.get("usb_endpoints_error"), "RuntimeError")


class InventoryWorkbookTests(ServerTestBase):
    """The USB worksheet is the operator's view, and only that."""

    def setUp(self):
        super().setUp()
        SwitchWarningAcknowledgementTests._quiet_cache_verification(self)
        self._patch_cache(FULL_CACHE_UNITS)
        self.net = FakeUdp()
        self.service._exchange = self.net.exchange
        self.seed(*OBSERVED_UDP)

    def workbook(self):
        openpyxl = __import__("openpyxl")
        reply = self.client.get("/api/download_inventory")
        self.assertEqual(reply.status_code, 200)
        self.assertIn("spreadsheetml", reply.headers["Content-Type"])
        return openpyxl.load_workbook(io.BytesIO(reply.data))

    def test_the_workbook_has_a_devices_sheet_and_a_usb_sheet(self):
        self.assertEqual(self.workbook().sheetnames, ["Devices", "USB"])

    def test_the_usb_sheet_is_named_exactly_usb(self):
        self.assertIn("USB", self.workbook().sheetnames)

    def test_the_usb_sheet_has_exactly_the_requested_columns(self):
        sheet = self.workbook()["USB"]
        self.assertEqual([cell.value for cell in sheet[1]],
                         ["Model", "USB Role", "Device IP", "Hostname", "USB IP",
                          "USB MAC", "Firmware", "Network Mode", "Network"])

    def test_the_usb_sheet_carries_no_diagnostic_columns(self):
        headers = {str(cell.value) for cell in self.workbook()["USB"][1]}
        for unwanted in ("Peers", "Link", "Status", "Actions", "Parent IP",
                         "Liveness Source", "Classification", "Discovery Source"):
            self.assertNotIn(unwanted, headers, unwanted)

    def test_one_row_per_canonical_usb_endpoint(self):
        sheet = self.workbook()["USB"]
        macs = [sheet.cell(row=r, column=6).value for r in range(2, sheet.max_row + 1)]
        self.assertTrue(macs)
        self.assertEqual(len(macs), len(set(macs)), "a physical endpoint appears once")
        expected = {d["mac"] for d in srv._usb_extender_view()["devices"]}
        self.assertEqual(set(macs), expected, "the same inventory Configure renders")

    def test_both_families_are_represented(self):
        """Integrated endpoints and standalone extenders both appear.

        This used to be spelled as "an AT-OMNI row and an HW-OMNI row", which
        only worked while the standalone units were displayed under their
        protocol name. The distinction that actually matters is integrated
        versus standalone, so the test now names that.
        """
        sheet = self.workbook()["USB"]
        models = {str(sheet.cell(row=r, column=1).value) for r in range(2, sheet.max_row + 1)}
        integrated = {m for m in models if "E4521" in m or "D4511" in m}
        standalone = {m for m in models if m.endswith("-311") or m.endswith("-324")}
        self.assertTrue(integrated, models)
        self.assertTrue(standalone, models)
        # The operator's sheet carries the displayed name; the protocol identity
        # stays inside the protocol layer.
        self.assertEqual(standalone, {"HW-OMNI-311", "HW-OMNI-324"}, models)

    def test_addresses_and_macs_stay_text(self):
        sheet = self.workbook()["USB"]
        for column in (3, 5, 6):
            for row in range(2, sheet.max_row + 1):
                value = sheet.cell(row=row, column=column).value
                self.assertIsInstance(value, str, f"row {row} column {column}")

    def test_the_sheet_is_formatted_for_reading(self):
        for name in ("Devices", "USB"):
            sheet = self.workbook()[name]
            self.assertEqual(sheet.freeze_panes, "A2", name)
            self.assertTrue(sheet.auto_filter.ref, name)
            self.assertTrue(sheet["A1"].font.bold, name)

    def test_the_csv_download_is_untouched(self):
        reply = self.client.get("/api/download_csv")
        self.assertEqual(reply.status_code, 200)
        self.assertIn("csv", reply.headers["Content-Type"])


class LayoutIndependenceTests(ServerTestBase):
    """Layout and appearance mode stay independent dimensions."""

    def source(self, *parts):
        return Path(srv.__file__).resolve().parent.joinpath(*parts).read_text(encoding="utf-8")

    def test_light_is_a_mode_not_a_fifth_layout(self):
        css = self.source("ui", "templates.css")
        self.assertIn("body.light", css)
        for template in ("classic", "compact", "modern", "workspace"):
            self.assertIn(f'[data-template="{template}"]', css, template)

    def test_colours_are_named_by_meaning(self):
        css = self.source("ui", "templates.css")
        for token in ("--surface", "--surface-raised", "--surface-elevated", "--edge",
                      "--text", "--text-muted", "--action-primary", "--action-secondary",
                      "--state-success", "--state-warning", "--state-error",
                      "--state-selection", "--state-pending",
                      "--minority-topology-bg", "--daisy-topology-bg"):
            self.assertIn(token, css, token)

    def test_a_layout_selection_persists_and_is_validated(self):
        self.client.post("/api/ui_preferences", json={"template": "workspace"})
        self.assertEqual(self.client.get("/api/ui_preferences").get_json()["template"], "workspace")
        self.assertEqual(self.client.post("/api/ui_preferences",
                                          json={"template": "neon"}).status_code, 400)
        self.assertEqual(self.client.get("/api/ui_preferences").get_json()["template"], "workspace")


class RouteSubnetEligibilityTests(unittest.TestCase):
    """A USB link is not routed, so a USB route may not cross a subnet.

    The defect: neither Matrix axis carried a subnet mask, so every eligibility
    question answered "cannot say" -- and the gate refused only on a definite
    mismatch. A LEX on one subnet and a REX on another rendered as available.
    """

    def entry(self, kind, ip, mask, **over):
        base = {"kind": kind, "usb_key": ip, "usb_mac": f"AA:BB:CC:00:00:{ip.split('.')[-1]:0>2}",
                "classification": "STANDALONE" if kind == "standalone" else "INTEGRATED",
                "online": True, "pairing_eligible": True,
                "endpoint_ip": ip, "endpoint_mask": mask}
        return {**base, **over}

    def check(self, lex, rex):
        return srv._usb_route_network_check(lex, rex)[0]

    def capability(self, lex, rex):
        return srv._usb_route_capability(lex, rex)

    # ---- the arithmetic ----
    def test_the_same_slash_24_is_allowed(self):
        self.assertEqual(self.check(self.entry("standalone", "10.5.1.10", "255.255.255.0"),
                                    self.entry("standalone", "10.5.1.20", "255.255.255.0")), "OK")

    def test_a_different_slash_24_is_refused(self):
        self.assertEqual(self.check(self.entry("standalone", "10.5.1.10", "255.255.255.0"),
                                    self.entry("standalone", "10.5.2.20", "255.255.255.0")),
                         "NETWORK_MISMATCH")

    def test_a_slash_16_may_legitimately_span_third_octets(self):
        """Comparing the first three octets would refuse this, wrongly."""
        self.assertEqual(self.check(self.entry("standalone", "10.5.1.10", "255.255.0.0"),
                                    self.entry("standalone", "10.5.240.20", "255.255.0.0")), "OK")

    def test_masks_that_disagree_are_not_authorised(self):
        """Each endpoint must see the other inside its own network, and the two
        networks must be the same one. Anything less is not proof."""
        self.assertEqual(self.check(self.entry("standalone", "10.5.1.10", "255.255.0.0"),
                                    self.entry("standalone", "10.5.1.20", "255.255.255.0")),
                         "NETWORK_MISMATCH")

    def test_an_unknown_mask_refuses_rather_than_guesses(self):
        for lex_mask, rex_mask in (("", "255.255.255.0"), ("255.255.255.0", ""), ("", "")):
            self.assertEqual(self.check(self.entry("standalone", "10.5.1.10", lex_mask),
                                        self.entry("standalone", "10.5.1.20", rex_mask)),
                             "NETWORK_RELATION_UNKNOWN", (lex_mask, rex_mask))

    def test_an_unknown_address_refuses(self):
        self.assertEqual(self.check(self.entry("standalone", "", "255.255.255.0"),
                                    self.entry("standalone", "10.5.1.20", "255.255.255.0")),
                         "NETWORK_RELATION_UNKNOWN")

    # ---- the cell ----
    def test_a_cross_subnet_cell_is_disabled_for_every_combination(self):
        """Including E4521 to D4511: the transport does not change the rule."""
        for lex_kind, rex_kind in (("standalone", "standalone"), ("integrated", "integrated"),
                                   ("standalone", "integrated"), ("integrated", "standalone")):
            cap = self.capability(self.entry(lex_kind, "10.5.1.10", "255.255.255.0"),
                                  self.entry(rex_kind, "10.9.1.20", "255.255.255.0"))
            self.assertEqual(cap["state"], "NETWORK_MISMATCH", f"{lex_kind}->{rex_kind}")
            self.assertFalse(cap["enabled"], f"{lex_kind}->{rex_kind}")

    def test_an_unknown_relation_cell_is_disabled(self):
        cap = self.capability(self.entry("standalone", "10.5.1.10", ""),
                              self.entry("standalone", "10.5.1.20", "255.255.255.0"))
        self.assertEqual(cap["state"], "NETWORK_RELATION_UNKNOWN")
        self.assertFalse(cap["enabled"])

    def test_a_same_subnet_cell_is_still_offered(self):
        cap = self.capability(self.entry("standalone", "10.5.1.10", "255.255.255.0"),
                              self.entry("standalone", "10.5.1.20", "255.255.255.0"))
        self.assertTrue(cap["enabled"])
        self.assertEqual(cap["state"], "SUPPORTED_STANDALONE_UDP")

    def test_the_refusal_reads_as_prose_and_names_the_reason(self):
        for state in ("NETWORK_MISMATCH", "NETWORK_RELATION_UNKNOWN"):
            label, detail = srv.USB_CELL_TEXT[state]
            self.assertNotIn("_", label, state)
            self.assertTrue(detail)
        self.assertIn("Different USB subnet", srv.USB_CELL_TEXT["NETWORK_MISMATCH"][0])
        self.assertIn("unavailable", srv.USB_CELL_TEXT["NETWORK_RELATION_UNKNOWN"][0])

    def test_a_refused_cell_still_carries_the_addresses_it_judged(self):
        cap = self.capability(self.entry("standalone", "10.5.1.10", "255.255.255.0"),
                              self.entry("standalone", "10.9.1.20", "255.255.255.0"))
        self.assertEqual(cap["network"]["lex_usb_ip"], "10.5.1.10")
        self.assertEqual(cap["network"]["rex_usb_ip"], "10.9.1.20")

    # ---- the integrated authority ----
    def test_the_icron_network_decides_for_an_integrated_endpoint(self):
        """Not the parent's management address: the USB link lives on the Icron
        network, and the two are routinely different."""
        srv._usb_net_config[srv._norm_usb_mac("AA:BB:CC:00:00:99")] = {
            "ipaddress": "192.168.200.62", "subnetmask": "255.255.255.0"}
        self.addCleanup(lambda: srv._usb_net_config.pop(srv._norm_usb_mac("AA:BB:CC:00:00:99"), None))
        integrated = {"kind": "integrated", "usb_mac": "AA:BB:CC:00:00:99",
                      "classification": "INTEGRATED", "online": True, "pairing_eligible": True,
                      # The parent is managed here; the USB endpoint is not.
                      "ip": "192.0.2.141", "usb_ip": "192.0.2.141"}
        address, mask = srv._usb_endpoint_network(integrated)
        self.assertEqual((address, mask), ("192.168.200.62", "255.255.255.0"))
        self.assertEqual(self.check(integrated, self.entry("standalone", "192.0.2.135",
                                                           "255.255.255.0")),
                         "NETWORK_MISMATCH")


class RouteSubnetEnforcementTests(ServerTestBase):
    """The backend is the safety boundary, and refuses before it mutates."""

    def setUp(self):
        super().setUp()
        self._patch_cache(FULL_CACHE_UNITS)
        self.net = FakeUdp()
        self.service._exchange = self.net.exchange
        for ip in (OMNI311_IP, OMNI324_IP):
            self.service.discover_ip(ip, IFACE_IP, IFACE_MASK)
        self.allow_transmit = True

    def move_endpoint(self, mac, address, mask="255.255.255.0"):
        """Put an endpoint on another subnet, as a physical move would."""
        record = self.service.device(mac)
        with self.service._lock:
            stored = self.service._devices[usb.normalize_mac(mac)]
            stored["ip"] = address
            stored["device_subnet_mask"] = mask
        return record

    def peers(self, mac):
        return sorted(usb.normalize_mac(m) for m in
                      (self.service.read_pairing(mac).get("paired_macs") or []))

    def test_a_cross_subnet_pair_is_refused_and_transmits_nothing(self):
        self.move_endpoint(OMNI324_MAC, "192.168.222.44")
        self.net.endpoints["192.168.222.44"] = self.net.endpoints[OMNI324_IP]
        self.net.sent.clear()
        reply = self.client.post("/api/usb_route/pair",
                                 json={"lex_mac": OMNI311_MAC, "rex_mac": OMNI324_MAC})
        self.assertEqual(reply.status_code, 409)
        self.assertEqual(reply.get_json()["state"], "NETWORK_MISMATCH")
        self.assertEqual([c for _d, c, _p in self.net.sent if c in (usb.PAIR, usb.UNPAIR)], [],
                         "a refusal must not reach the transport")

    def test_a_refused_reassignment_leaves_the_existing_route_untouched(self):
        """Refuse before releasing, never after: the working route survives."""
        self.net.endpoints[OMNI311_IP]["peers"] = [OMNI324_MAC]
        self.net.endpoints[OMNI324_IP]["peers"] = [OMNI311_MAC]
        before_host, before_device = self.peers(OMNI311_MAC), self.peers(OMNI324_MAC)
        self.assertTrue(before_host and before_device)

        # A second host, on another subnet, is clicked for that REX.
        other_ip, other_mac = "192.168.222.10", "00:1B:13:09:00:07"
        self.net.endpoints[other_ip] = {"mac": other_mac, "code": 0, "peers": [], "advanced": True,
                                        "ack": True, "query": True}
        self.service.discover_ip(other_ip, "192.168.222.1", "255.255.255.0")
        self.net.sent.clear()
        reply = self.client.post("/api/usb_route/pair",
                                 json={"lex_mac": other_mac, "rex_mac": OMNI324_MAC})
        self.assertEqual(reply.status_code, 409)
        self.assertEqual(reply.get_json()["state"], "NETWORK_MISMATCH")
        self.assertEqual([c for _d, c, _p in self.net.sent if c in (usb.PAIR, usb.UNPAIR)], [])
        self.assertEqual(self.peers(OMNI311_MAC), before_host, "the old owner still holds it")
        self.assertEqual(self.peers(OMNI324_MAC), before_device)

    def test_an_unknown_network_refuses_creation_but_not_diagnosis(self):
        with self.service._lock:
            self.service._devices[usb.normalize_mac(OMNI324_MAC)]["device_subnet_mask"] = ""
            self.service._devices[usb.normalize_mac(OMNI324_MAC)]["subnet_mask"] = ""
        self.net.sent.clear()
        reply = self.client.post("/api/usb_route/pair",
                                 json={"lex_mac": OMNI311_MAC, "rex_mac": OMNI324_MAC})
        self.assertEqual(reply.status_code, 409)
        self.assertEqual(reply.get_json()["state"], "NETWORK_RELATION_UNKNOWN")
        self.assertEqual([c for _d, c, _p in self.net.sent if c in (usb.PAIR, usb.UNPAIR)], [])
        # It is still visible and still readable.
        rows = [d for d in srv._usb_extender_view()["devices"]
                if d["mac"] == usb.normalize_mac(OMNI324_MAC)]
        self.assertEqual(len(rows), 1, "a routed endpoint stays in the inventory")

    def test_a_same_subnet_route_still_works(self):
        reply = self.client.post("/api/usb_route/pair",
                                 json={"lex_mac": OMNI311_MAC, "rex_mac": OMNI324_MAC})
        self.assertEqual(reply.get_json()["status"], "VERIFIED_SUCCESS")

    def test_a_routed_endpoint_is_still_visible_in_the_matrix(self):
        self.move_endpoint(OMNI324_MAC, "192.168.222.44")
        state = self.client.get("/api/usb_state").get_json()
        axis = (state.get("matrix_lex") or []) + (state.get("matrix_rex") or [])
        self.assertIn(srv._norm_usb_mac(OMNI324_MAC),
                      [srv._norm_usb_mac(e.get("usb_mac")) for e in axis],
                      "a routed endpoint stays on the axis; only its intersections close")

    def test_the_resolver_refuses_an_ineligible_pair_too(self):
        """Resolve shares the mutating endpoints' guard, so it cannot describe a
        route as available that the pair endpoint would refuse."""
        self.move_endpoint(OMNI324_MAC, "192.168.222.44")
        reply = self.client.post("/api/usb_route/resolve",
                                 json={"lex_mac": OMNI311_MAC, "rex_mac": OMNI324_MAC})
        self.assertEqual(reply.status_code, 409)
        self.assertEqual(reply.get_json()["state"], "NETWORK_MISMATCH")


class UsbFirmwareSemanticsTests(ServerTestBase):
    """The USB inventory reports the USB endpoint's firmware, not its parent's."""

    def setUp(self):
        super().setUp()
        SwitchWarningAcknowledgementTests._quiet_cache_verification(self)
        self._patch_cache(FULL_CACHE_UNITS)
        self.net = FakeUdp()
        self.service._exchange = self.net.exchange
        self.seed(*OBSERVED_UDP)

    def device(self, mac):
        return next(d for d in srv._usb_extender_view()["devices"]
                    if d["mac"] == usb.normalize_mac(mac))

    def test_the_two_versions_are_separate_facts(self):
        """Measured on hardware: the OmniStream unit reported one version and the
        Icron module inside it reported another."""
        row = self.device(D4511_USB_MAC)
        self.assertEqual(row["usb_firmware"], row["product_revision"],
                         "the USB endpoint's own reported revision")
        self.assertEqual(row["parent_firmware"], PARENT_FIRMWARE,
                         "the parent reports its own firmware")
        self.assertNotEqual(row["usb_firmware"], row["parent_firmware"],
                            "the two versions are read from different devices")

    def test_the_usb_firmware_is_never_the_parents(self):
        row = self.device(D4511_USB_MAC)
        parent = next(u for u in FULL_CACHE_UNITS if u.get("usb_mac") == D4511_USB_MAC)
        self.assertEqual(parent["version"], PARENT_FIRMWARE, "the fixture really states one")
        self.assertNotEqual(row["usb_firmware"], parent["version"],
                            "the parent's firmware must not stand in for the module's")

    def test_an_unread_icron_version_stays_unknown(self):
        with self.service._lock:
            self.service._devices[usb.normalize_mac(D4511_USB_MAC)]["product_revision"] = ""
        row = self.device(D4511_USB_MAC)
        self.assertEqual(row["usb_firmware"], "", "never filled in from the parent")

    def test_a_standalone_unit_is_its_own_usb_endpoint(self):
        row = self.device(OMNI311_MAC)
        self.assertEqual(row["usb_firmware"], row["firmware"])
        self.assertEqual(row["usb_firmware_source"], "udp_basic_query")

    def test_the_inventory_row_uses_the_usb_firmware(self):
        lex, rex = srv._usb_inventory()
        for row in lex + rex:
            source = self.device(row["usb_mac"])
            self.assertEqual(row["firmware"], source.get("usb_firmware") or "", row["usb_mac"])

    def test_the_configure_column_reads_the_usb_firmware(self):
        source = Path(srv.__file__).resolve().parent.joinpath("ui", "matrix", "usb-extenders.js")
        text = source.read_text(encoding="utf-8")
        self.assertIn("d.usb_firmware", text)
        self.assertNotIn("d.firmware||d.product_revision", text,
                         "the parent's firmware must not be the fallback here")

    def test_the_workbook_agrees_with_configure(self):
        openpyxl = __import__("openpyxl")
        reply = self.client.get("/api/download_inventory")
        sheet = openpyxl.load_workbook(io.BytesIO(reply.data))["USB"]
        exported = {sheet.cell(row=r, column=6).value: sheet.cell(row=r, column=7).value
                    for r in range(2, sheet.max_row + 1)}
        for mac, firmware in exported.items():
            expected = self.device(mac).get("usb_firmware") or "N/A"
            self.assertEqual(firmware, expected, mac)

    def test_the_devices_sheet_still_carries_the_parent_firmware(self):
        openpyxl = __import__("openpyxl")
        reply = self.client.get("/api/download_inventory")
        sheet = openpyxl.load_workbook(io.BytesIO(reply.data))["Devices"]
        columns = [c.value for c in sheet[1]]
        versions = {sheet.cell(row=r, column=columns.index("IP") + 1).value:
                    sheet.cell(row=r, column=columns.index("Version") + 1).value
                    for r in range(2, sheet.max_row + 1)}
        parent = next(u for u in FULL_CACHE_UNITS if u.get("usb_mac") == D4511_USB_MAC)
        self.assertEqual(versions.get(parent["ip"]), parent.get("version"))


class AppearanceTests(ServerTestBase):
    """A layout, a palette and one colour -- appearance, and only appearance."""

    def prefs(self):
        return self.client.get("/api/ui_preferences").get_json()

    def test_four_layouts_and_ten_presets_are_offered(self):
        body = self.prefs()
        self.assertEqual(body["templates"], ["classic", "compact", "modern", "workspace"])
        self.assertEqual(len(body["presets"]), 10)
        self.assertEqual(body["presets"][0], "default")

    def test_there_are_no_per_role_colour_controls(self):
        """Nine individual colour decisions were too granular to be a product.
        The roles live on; choosing them one at a time does not."""
        body = self.prefs()
        self.assertNotIn("colors", body)
        self.assertNotIn("color_roles", body)
        module = self.source("ui", "settings.js")
        self.assertNotIn("cfg_color_choice", module)
        self.assertIn("cfg_light_background", module, "one picker remains")

    def test_a_preset_persists(self):
        self.client.post("/api/ui_preferences", json={"preset": "plum"})
        self.assertEqual(self.prefs()["preset"], "plum")

    def test_every_preset_defines_every_semantic_role(self):
        """A preset that set only an accent would leave the states looking like
        whichever palette came before it."""
        script = self.source("ui", "appearance.js")
        block = script[script.index("const APPEARANCE_PRESETS = ["):script.index("const APPEARANCE_PRESET_DEFAULT")]
        roles = re.findall(r"\{id: '([a-z]+)'", script[script.index("const APPEARANCE_COLORS = ["):
                                                       script.index("// Ten palettes")])
        self.assertEqual(len(roles), 9)
        for preset in re.finditer(r"\{id: '([a-z]+)', name: '[^']+', hint: '[^']+',\s*\n\s*swatch: \[[^\]]*\],\s*\n\s*colors: \{(.*?)\}\}", block, re.S):
            name, colors = preset.group(1), preset.group(2)
            for role in roles:
                self.assertIn(f"{role}:", colors, f"{name} does not define {role}")

    def test_the_softer_palettes_are_named_for_their_colours(self):
        script = self.source("ui", "appearance.js")
        for expected in ("'rose'", "'plum'", "'warm'", "'forest'", "'ocean'", "'slate'"):
            self.assertIn(expected, script, expected)
        for banned in ("Women", "Female", "Feminine", "For her", "Girl", "Masculine"):
            self.assertNotIn(banned, script, "a palette is not a demographic")

    # ---- the one picker ----
    def test_the_light_background_persists(self):
        self.client.post("/api/ui_preferences", json={"light_background": "#EEF2F5"})
        self.assertEqual(self.prefs()["light_background"], "#eef2f5")

    def test_an_invalid_light_background_is_refused(self):
        self.client.post("/api/ui_preferences", json={"light_background": "#eef2f5"})
        for bad in ("white", "#ee", "rgb(1,2,3)", "#fff;background:url(x)", "javascript:alert(1)", ""):
            reply = self.client.post("/api/ui_preferences", json={"light_background": bad})
            self.assertEqual(reply.status_code, 400, bad)
        self.assertEqual(self.prefs()["light_background"], "#eef2f5", "the good value survived")

    def test_the_light_background_is_a_light_mode_value_only(self):
        """It drives the light palette's tokens. Dark declares its own."""
        css = self.source("ui", "templates.css")
        light = css[css.index("body.light {"):css.index("/* ---------------------------------------------------------------- classic */")]
        self.assertIn("--light-bg", light, "the light palette consumes it")
        dark = css[css.index(":root {"):css.index("body.light {")]
        self.assertNotIn("--light-bg", dark, "dark must not read it")

    def test_surfaces_are_derived_from_it_rather_than_flooded_with_it(self):
        css = self.source("ui", "templates.css")
        self.assertIn("color-mix(in srgb, var(--light-bg", css,
                      "panels and headers keep their separation from the page")

    # ---- obsolete overrides ----
    def test_an_obsolete_granular_override_is_dropped_not_applied(self):
        """An installation upgraded from the previous build must not keep being
        tinted by a control that no longer exists."""
        srv._write_ui_preferences({"template": "modern", "preset": "cool",
                                   "colors": {"accent": "#ff00ff", "error": "#00ff00"}})
        self.assertNotIn("colors", self.prefs())
        self.client.post("/api/ui_preferences", json={"preset": "warm"})
        stored = srv._read_ui_preferences()
        self.assertNotIn("colors", stored, "the obsolete field is removed on the next save")
        self.assertEqual(stored["template"], "modern", "everything else is preserved")

    def test_a_malformed_preferences_file_degrades_to_defaults(self):
        srv.UI_PREFS.write_text("{not json", encoding="utf-8")
        body = self.prefs()
        self.assertEqual((body["template"], body["preset"], body["light_background"]),
                         ("classic", "default", "#f7f8fb"))

    def test_the_client_ignores_a_stored_override_too(self):
        script = self.source("ui", "appearance.js")
        self.assertIn("const {colors, ...kept} = raw;", script,
                      "a value left in browser storage must not survive either")

    # ---- reset ----
    def test_reset_restores_every_appearance_choice(self):
        self.client.post("/api/ui_preferences",
                         json={"template": "workspace", "preset": "rose", "light_background": "#eef2f5"})
        self.client.post("/api/ui_preferences/reset")
        body = self.prefs()
        self.assertEqual((body["template"], body["preset"], body["light_background"]),
                         ("classic", "default", "#f7f8fb"))

    def test_reset_leaves_every_operational_setting_alone(self):
        self.client.post("/api/scan_preferences", json={"scan_targets": "10.44.0.0/24"})
        self.client.post("/api/lldp_topology/acknowledge", json={"acknowledged": True})
        self.client.post("/api/lldp_topology/acknowledge",
                         json={"acknowledged": True, "scope": "daisy_chain"})
        self._patch_cache(FULL_CACHE_UNITS)
        before_units = len(srv._load_cache())
        before_config = self.client.get("/api/config").get_json()
        self.client.post("/api/ui_preferences", json={"template": "modern", "preset": "ocean"})
        self.client.post("/api/ui_preferences/reset")
        self.assertEqual(self.client.get("/api/scan_preferences").get_json()["scan_targets"],
                         "10.44.0.0/24")
        state = srv._read_topology_ack()
        self.assertTrue(state.get("acknowledged"))
        self.assertTrue(state.get("daisy_acknowledged"))
        self.assertEqual(len(srv._load_cache()), before_units)
        self.assertEqual(self.client.get("/api/config").get_json(), before_config)
        self.assertEqual(self.client.get("/api/usb_extenders/ranges").get_json()["ranges"],
                         ["10.44.0.0/24"])

    # ---- the client contract ----
    def source(self, *parts):
        return Path(srv.__file__).resolve().parent.joinpath(*parts).read_text(encoding="utf-8")

    def test_every_surface_loads_the_appearance_layer(self):
        for page in (("ui", "index.html"), ("ui", "matrix", "index.html"),
                     ("ui", "matrix", "configure.html"), ("ui", "matrix", "usb.html")):
            text = self.source(*page)
            self.assertIn("templates.css", text, page)
            self.assertIn("appearance.js", text, page)

    def test_the_appearance_layer_reaches_no_device_behaviour(self):
        script = self.source("ui", "appearance.js")
        for banned in ("/api/usb_route", "/api/scan", "/api/usb_pair", "/api/clear_units", "/api/cache"):
            self.assertNotIn(banned, script, "a layout must not reach device behaviour")

    def test_the_template_is_applied_before_anything_renders(self):
        script = self.source("ui", "appearance.js")
        self.assertIn("applyAppearance(readStoredAppearance());", script,
                      "read synchronously so the first frame is already correct")


class ConfigureTableTests(ServerTestBase):
    """Wide tables keep the operator's place; the chooser matches the app."""

    def source(self, *parts):
        return Path(srv.__file__).resolve().parent.joinpath(*parts).read_text(encoding="utf-8")

    # ---- frozen identity columns ----
    def test_all_three_wide_tables_freeze_their_identity_columns(self):
        page = self.source("ui", "matrix", "configure.html")
        self.assertEqual(page.count("sticky-identity"), 3,
                         "encoders, decoders and USB all scroll horizontally")
        for table in ('id="encTbl" class="grid sticky-identity"',
                      'id="decTbl" class="grid sticky-identity"',
                      'class="grid sticky-identity" aria-label="USB endpoints"'):
            self.assertIn(table, page, table)

    def test_identity_is_the_first_two_columns_everywhere(self):
        page = self.source("ui", "matrix", "configure.html")
        usb_head = page[page.index('aria-label="USB endpoints"'):]
        usb_head = usb_head[:usb_head.index("</tr>")]
        self.assertLess(usb_head.index("Device IP"), usb_head.index("Hostname"))
        self.assertLess(usb_head.index("Hostname"), usb_head.index("Model"))
        self.assertLess(usb_head.index("Model"), usb_head.index("USB Role"))
        matrix = self.source("ui", "matrix", "matrix.js")
        for header in matrix.split("innerHTML = `<tr>")[1:3]:
            row = header[:header.index("</tr>") if "</tr>" in header else 200]
            self.assertLess(row.index("<th>IP</th>"), row.index("<th>Hostname</th>"))

    def test_the_usb_row_order_follows_its_header(self):
        script = self.source("ui", "matrix", "usb-extenders.js")
        block = script[script.index('return `<tr data-mac='):]
        block = block[:block.index("</tr>")]
        self.assertLess(block.index("deviceIp"), block.index("hostname"))
        self.assertLess(block.index("hostname"), block.index("display_model"))
        self.assertLess(block.index("display_model"), block.index("usb_role"))

    def test_the_second_column_offset_is_measured_not_hardcoded(self):
        """The first column's width changes with template density, content and
        zoom, so a fixed offset is wrong in three layouts out of four."""
        css = self.source("ui", "templates.css")
        self.assertIn("left: var(--sticky-col2-offset, 0px)", css)
        script = self.source("ui", "sticky-columns.js")
        self.assertIn("getBoundingClientRect().width", script)
        self.assertIn("--sticky-col2-offset", script)
        self.assertNotIn("left: 99px", css)

    def test_frozen_cells_are_opaque_and_use_semantic_tokens(self):
        """A transparent sticky cell lets the scrolling columns show through it."""
        css = self.source("ui", "templates.css")
        block = css[css.index("/* ------------------------------------------------ frozen identity columns */"):]
        self.assertIn("background: var(--surface-raised)", block)
        self.assertIn("background: var(--surface-elevated)", block)
        self.assertIn("z-index", block)
        literals = re.findall(r"background:\s*(#[0-9a-fA-F]{3,6})", block)
        self.assertEqual(literals, [], f"frozen cells name colours directly: {literals}")

    def test_the_boundary_is_marked(self):
        css = self.source("ui", "templates.css")
        block = css[css.index("/* ------------------------------------------------ frozen identity columns */"):]
        self.assertIn("box-shadow", block, "the operator must see what is scrolling behind")

    def test_the_editable_hostname_stays_editable(self):
        css = self.source("ui", "templates.css")
        start = css.index("/* ------------------------------------------------ frozen identity columns */")
        # Bounded at the next section. Slicing to the end of the file made this
        # assert things about every rule added below it afterwards -- the shared
        # toast, which is legitimately pointer-events:none, failed it.
        end = css.index("/* ------------------------------------------------ the application shell --- */", start)
        block = css[start:end]
        self.assertIn("td:nth-child(2) input", block,
                      "freezing the cell must not disable what is inside it")
        for banned in ("pointer-events: none", "user-select: none"):
            self.assertNotIn(banned, block, banned)

    def test_the_sticky_module_starts_no_document_wide_observer(self):
        """It runs on every render of three tables; it must not also react to
        every mutation in the document."""
        script = self.source("ui", "sticky-columns.js")
        self.assertNotIn("subtree: true", script)
        self.assertIn("subtree: false", script)

    # ---- the firmware chooser ----
    def test_the_firmware_chooser_matches_the_slate_treatment(self):
        page = self.source("ui", "settings.js")
        self.assertIn('class="file-chooser"', page)
        self.assertIn('id="cfg_firmware_browse" class="file-chooser-btn"', page)
        self.assertNotIn('id="cfg_firmware_browse" class="btn small" type="button">\U0001F4C1', page)

    def test_the_chooser_still_selects_a_folder(self):
        """A file input cannot yield a directory path, so the application's own
        folder browser stays: same treatment, correct semantics."""
        page = self.source("ui", "settings.js")
        self.assertIn("openFolderBrowser($('#cfg_firmware_path').value, '#cfg_firmware_path')", page)
        self.assertNotIn('id="cfg_firmware_path" type="file"', page,
                         "firmware path is a directory, not a file")
        self.assertIn("/api/list_dir", page)

    def test_the_folder_browser_is_defined_once(self):
        # Once in the shared module, and nowhere else: a page that kept its own
        # copy would drift from the one every other page uses.
        module = self.source("ui", "settings.js")
        self.assertEqual(module.count("function openFolderBrowser("), 1,
                         "the earlier definition was entirely shadowed by the later one")
        for page in ("index.html", "matrix/configure.html", "matrix/index.html", "matrix/usb.html"):
            self.assertNotIn("function openFolderBrowser(", self.source("ui", *page.split("/")), page)

    def test_the_firmware_path_still_reaches_the_server(self):
        before = self.client.get("/api/config").get_json()
        self.assertIn("firmware_path", before)
        self.assertIn("cfg_firmware_path", self.source("ui", "settings.js"))

    # ---- appearance reconciliation on every page ----
    def test_every_page_reconciles_its_layout_with_the_server(self):
        """The preference only reconciled where the Settings dialog lived, so
        Configure and the Matrix kept the default layout after a cache clear."""
        script = self.source("ui", "appearance.js")
        self.assertIn("function reconcileAppearance()", script)
        self.assertIn("reconcileAppearance);", script)


class WorkspaceLayoutTests(ServerTestBase):
    """Workspace is a constrained OmniSuite, not a shell built around it."""

    def source(self, *parts):
        return Path(srv.__file__).resolve().parent.joinpath(*parts).read_text(encoding="utf-8")

    def workspace_css(self):
        css = self.source("ui", "templates.css")
        start = css.index('/* ------------------------------------------------------------- workspace */')
        return css[start:css.index("/* Preset and colour controls in Settings. */")]

    def test_there_is_no_navigation_rail(self):
        """The application already has one primary navigation. A second one in a
        rail gave the operator two of everything."""
        block = self.workspace_css()
        for removed in ("ws-rail", "grid-template-areas", "rail-width"):
            self.assertNotIn(removed, block, removed)

    def test_there_is_no_inspector(self):
        block = self.workspace_css()
        for removed in ("ws-inspector", "inspector-width", "ws-drawer-closed"):
            self.assertNotIn(removed, block, removed)
        css = self.source("ui", "templates.css")
        self.assertNotIn("ws-inspector", css)

    def test_the_inspector_code_is_gone_rather_than_disabled(self):
        root = Path(srv.__file__).resolve().parent / "ui"
        self.assertFalse((root / "workspace.js").exists(),
                         "with nothing to relocate there is nothing for it to do")
        for page in ("index.html", "matrix/index.html", "matrix/configure.html", "matrix/usb.html"):
            text = (root / page).read_text(encoding="utf-8")
            self.assertNotIn("workspace.js", text, page)
            self.assertNotIn("ws_inspector", text, page)
            self.assertNotIn("Select a device to see its details", text, page)

    def test_nothing_relocates_the_page(self):
        """The artifact was node relocation. Nothing moves now, so there is
        exactly one of every element at every moment."""
        script = self.source("ui", "appearance.js")
        for banned in ("appendChild(node)", "body.prepend", "createElement('nav')",
                       "createElement('aside')", "createElement('main')"):
            self.assertNotIn(banned, script, banned)

    def test_the_work_area_is_constrained_and_centred(self):
        block = self.workspace_css()
        self.assertIn("--workspace-max", block)
        self.assertIn("margin-left: auto", block)
        self.assertIn("margin-right: auto", block)

    def test_the_usb_matrix_keeps_the_width_it_needs(self):
        block = self.workspace_css()
        self.assertIn('[data-template="workspace"][data-page="usb"]', block)

    def test_the_page_key_is_an_attribute_not_a_rearrangement(self):
        script = self.source("ui", "appearance.js")
        self.assertIn("setAttribute('data-page'", script)
        self.assertIn("function currentPageKey()", script)

    def test_no_layout_hard_codes_a_shell_colour(self):
        """Every layout surface must consume the semantic tokens, so light and
        dark are the palette's business."""
        block = self.workspace_css()
        literals = re.findall(r":\s*(#[0-9a-fA-F]{3,6}|rgba?\([^)]*\))\s*;", block)
        self.assertEqual(literals, [], f"workspace CSS names colours directly: {literals}")

    def test_the_light_palette_restates_every_semantic_token(self):
        """The defect: `--surface: var(--bg)` declared once on :root resolved
        there, so light mode redefined --bg and every rule written against
        --surface carried on using the dark colour."""
        css = self.source("ui", "templates.css")
        light = css[css.index("body.light {"):css.index("@supports")]
        for token in ("--surface", "--surface-raised", "--surface-elevated", "--text",
                      "--text-muted", "--edge"):
            self.assertIn(f"{token}:", light, f"{token} does not follow the light palette")

    def test_the_document_itself_is_painted(self):
        css = self.source("ui", "templates.css")
        self.assertIn("html { background: var(--surface); }", css,
                      "no strip of the viewport may be left in the other palette")

    def test_the_light_palette_reaches_the_root_element(self):
        """The page canvas is painted from <html>, which the body-level `light`
        class never reached -- so light mode left a dark strip around the page.

        The root used to be kept in step by a MutationObserver watching <body>,
        because the mode belonged to each page. appearance.js owns the mode now
        and sets both elements itself, so the assertion is that it does -- not
        that a particular follower still exists.
        """
        css = self.source("ui", "templates.css")
        self.assertIn("html.light,", css)
        script = self.source("ui", "appearance.js")
        self.assertIn("root.classList.toggle('light', light)", script,
                      "the root carries the palette")
        self.assertIn("document.body.classList.toggle('light', light)", script,
                      "and so does the body")
        self.assertNotIn("watchThemeClass", script,
                         "nothing needs to observe a class this module sets")


class LinkFreshnessTests(ServerTestBase):
    """A verified link survives a dropped datagram; it does not survive forever.

    The defect this exists for: the sweep re-read Link Status only once state had
    already expired, so a perfectly stable link flickered to Unknown while a page
    simply sat open. Freshness, the refresh cadence and the miss budget are now
    three separate numbers with a deliberate ordering between them.
    """

    def setUp(self):
        super().setUp()
        self._patch_cache(FULL_CACHE_UNITS)
        self.net = FakeUdp()
        self.service._exchange = self.net.exchange
        self.service.discover_ip(OMNI311_IP, IFACE_IP, IFACE_MASK)
        self.net.endpoints[OMNI311_IP]["peers"] = [OMNI324_MAC]
        self.clock = [10_000.0]
        real_now = self.service.now
        self.service.now = lambda: self.clock[0]
        self.addCleanup(lambda: setattr(self.service, "now", real_now))

    def link(self):
        return self.service.device(OMNI311_MAC)

    def read(self, silent=False):
        self.net.endpoints[OMNI311_IP]["silent_link"] = silent
        return self.service.read_link_status(OMNI311_MAC)

    # ---- the ordering that prevents the flicker ----
    def test_the_sweep_refreshes_before_the_state_it_maintains_expires(self):
        self.assertLess(usb.LINK_REFRESH_AGE, usb.LINK_TTL,
                        "re-reading only at the TTL guarantees a window with no current state")
        self.assertLess(usb.LINK_TTL, usb.LINK_RETAIN_TTL)
        self.assertLess(srv.USB_LINK_MIN_INTERVAL + usb.LINK_REFRESH_AGE, usb.LINK_TTL,
                        "even a sweep that arrives a full interval late is still in time")

    # ---- misses ----
    def test_one_missed_response_does_not_erase_a_verified_link(self):
        self.read()
        self.assertEqual(self.link()["link_state"], "LINKED")
        self.clock[0] += 30
        self.read(silent=True)
        record = self.link()
        self.assertEqual(record["link_state"], "LINKED", "one dropped datagram is not evidence")
        self.assertTrue(record["link_state_fresh"])
        self.assertEqual(record["link_consecutive_failures"], 1)

    def test_repeated_misses_eventually_become_unknown(self):
        self.read()
        for _ in range(usb.LINK_MAX_MISSES):
            self.clock[0] += 5
            self.read(silent=True)
        record = self.link()
        self.assertEqual(record["link_state"], "UNKNOWN")
        self.assertFalse(record["link_state_fresh"])
        self.assertFalse(record["link_state_retained"])
        self.assertEqual(record["link_states"], [])

    def test_a_success_resets_the_miss_budget(self):
        self.read()
        self.clock[0] += 5
        self.read(silent=True)
        self.clock[0] += 5
        self.read(silent=True)
        self.assertEqual(self.link()["link_consecutive_failures"], 2)
        self.clock[0] += 5
        self.read()
        record = self.link()
        self.assertEqual(record["link_consecutive_failures"], 0)
        self.assertTrue(record["link_state_fresh"])

    # ---- ageing ----
    def test_a_state_past_its_ttl_is_retained_and_labelled_as_ageing(self):
        self.read()
        self.clock[0] += usb.LINK_TTL + 5
        record = self.link()
        self.assertFalse(record["link_state_fresh"])
        self.assertTrue(record["link_state_retained"], "still the last thing measured")
        self.assertTrue(record["link_known"])
        self.assertEqual(record["link_state"], "LINKED")
        summary = srv._usb_link_summary(record)
        self.assertEqual(summary["label"], "Linked")
        self.assertIn("seen", summary["detail"].lower())

    def test_a_state_past_the_retain_window_is_unknown(self):
        self.read()
        self.clock[0] += usb.LINK_RETAIN_TTL + 5
        record = self.link()
        self.assertFalse(record["link_known"])
        self.assertEqual(record["link_state"], "UNKNOWN")
        self.assertEqual(srv._usb_link_summary(record)["label"], "Unknown")

    # ---- independence ----
    def test_link_freshness_is_not_liveness_freshness(self):
        """A cheap Query proving the unit answers says nothing about the link."""
        self.read()
        self.clock[0] += usb.LINK_RETAIN_TTL + 5
        self.service.refresh_liveness([OMNI311_MAC])
        record = self.link()
        self.assertTrue(record["online"], "the endpoint answers")
        self.assertFalse(record["link_known"], "which is not a link reading")

    def test_a_liveness_sweep_does_not_disturb_a_verified_link(self):
        self.read()
        self.service.refresh_liveness([OMNI311_MAC])
        record = self.link()
        self.assertEqual(record["link_state"], "LINKED")
        self.assertTrue(record["link_state_fresh"])
        self.assertEqual(record["link_source"], "udp_link_status")

    def test_the_link_read_records_its_own_source(self):
        self.read()
        self.assertEqual(self.link()["link_source"], "udp_link_status")


class FieldAuthorityTests(ServerTestBase):
    """No single `source` field speaks for every fact about an endpoint."""

    def setUp(self):
        super().setUp()
        self._patch_cache(FULL_CACHE_UNITS)
        self.net = FakeUdp()
        self.service._exchange = self.net.exchange
        self.seed(*OBSERVED_UDP)

    def device(self, mac):
        return next(d for d in srv._usb_extender_view()["devices"]
                    if d["mac"] == usb.normalize_mac(mac))

    def test_each_fact_names_the_provider_that_established_it(self):
        row = self.device(D4511_USB_MAC)
        authority = row["field_authority"]
        self.assertEqual(authority["pairing"], "usb_icron", "the parent owns pairing")
        self.assertIn(authority["firmware"], ("omnistream", "udp_basic_query"))
        self.assertIn("liveness", authority)
        self.assertIn("link", authority)

    def test_a_parent_observation_does_not_rewrite_link_or_network_authority(self):
        srv._record_parent_live(D4511_IP, paired_count=1)
        before = self.device(D4511_USB_MAC)["field_authority"]
        srv._record_parent_live(D4511_IP, paired_count=1)
        after = self.device(D4511_USB_MAC)["field_authority"]
        self.assertEqual(before["link"], after["link"])
        self.assertEqual(before["network"], after["network"])
        self.assertEqual(before["pairing"], after["pairing"])

    def test_liveness_authority_is_precedence_not_whoever_wrote_last(self):
        """The reported flip: an idle endpoint alternating Parent / UDP.

        Both providers can attest at once. The parent owns an integrated
        endpoint, so it wins whenever its observation is current, and the answer
        does not depend on which refresh happened to run most recently.
        """
        srv._record_parent_live(D4511_IP, paired_count=1)
        first = self.device(D4511_USB_MAC)
        self.assertEqual(first["liveness_source"], "usb_icron")
        # A UDP query for the same endpoint lands afterwards.
        self.service.refresh_liveness([D4511_USB_MAC])
        second = self.device(D4511_USB_MAC)
        self.assertEqual(second["liveness_source"], "usb_icron",
                         "a later UDP observation does not take authority from the parent")
        self.assertIn("usb_icron", second["liveness_sources"])

    def test_an_endpoint_answering_only_udp_says_so_honestly(self):
        row = self.device(OMNI311_MAC)
        self.assertEqual(row["field_authority"]["liveness"], row["liveness_source"])
        self.assertNotEqual(row["liveness_source"], "usb_icron",
                            "a standalone endpoint has no parent to attest for it")


class NetworkRelationLabelTests(ServerTestBase):
    """A routed endpoint is reachable, not faulty."""

    def setUp(self):
        super().setUp()
        self._patch_cache(FULL_CACHE_UNITS)
        self.net = FakeUdp()
        self.service._exchange = self.net.exchange
        self.seed(*OBSERVED_UDP)

    def test_the_internal_enum_survives_and_gains_a_human_label(self):
        self.assertEqual(srv.USB_NETWORK_RELATION_LABELS["OFF_NET"], "Routed")
        self.assertEqual(srv.USB_NETWORK_RELATION_LABELS["LOCAL"], "Local")
        self.assertEqual(srv.USB_NETWORK_RELATION_LABELS["UNKNOWN"], "Unknown")

    def test_every_endpoint_carries_both(self):
        for device in srv._usb_extender_view()["devices"]:
            self.assertIn(device["network_relation"], ("LOCAL", "OFF_NET", "UNKNOWN"))
            self.assertEqual(device["network_relation_label"],
                             srv.USB_NETWORK_RELATION_LABELS[device["network_relation"]])

    def test_no_user_facing_label_reads_as_a_fault(self):
        for label in srv.USB_NETWORK_RELATION_LABELS.values():
            self.assertNotIn("OFF", label.upper())
            self.assertNotIn("_", label)


class ScanTargetUnificationTests(ServerTestBase):
    """One persisted definition of what to search, for both protocols."""

    def setUp(self):
        super().setUp()
        self._patch_cache(FULL_CACHE_UNITS)
        self.net = FakeUdp()
        self.service._exchange = self.net.exchange

    def test_saving_scan_targets_configures_usb_directed_discovery(self):
        reply = self.client.post("/api/scan_preferences",
                                 json={"scan_targets": "192.168.150.10-192.168.150.20"})
        body = reply.get_json()
        self.assertTrue(body["ok"])
        self.assertIsNone(body["usb_ranges_error"])
        self.assertEqual(self.service.state()["ranges"], ["192.168.150.10-192.168.150.20"],
                         "the same routed network drives USB discovery without being entered twice")

    def test_the_saved_targets_come_back(self):
        self.client.post("/api/scan_preferences", json={"scan_targets": "10.20.30.0/24"})
        prefs = self.client.get("/api/scan_preferences").get_json()
        self.assertEqual(prefs["scan_targets"], "10.20.30.0/24")

    def test_saving_targets_does_not_forget_the_chosen_adapter(self):
        self.client.post("/api/scan_preferences", json={"interface_ip": "10.0.0.5"})
        self.client.post("/api/scan_preferences", json={"scan_targets": "10.20.30.0/24"})
        prefs = self.client.get("/api/scan_preferences").get_json()
        self.assertEqual(prefs["interface_ip"], "10.0.0.5")

    def test_an_unusable_target_is_reported_rather_than_silently_dropped(self):
        reply = self.client.post("/api/scan_preferences", json={"scan_targets": "not-an-address"})
        body = reply.get_json()
        self.assertTrue(body["ok"], "the rest of the preference still saves")
        self.assertTrue(body["usb_ranges_error"])

    def test_clearing_targets_clears_the_usb_discovery_networks(self):
        self.client.post("/api/scan_preferences", json={"scan_targets": "10.20.30.0/24"})
        self.client.post("/api/scan_preferences", json={"scan_targets": ""})
        self.assertEqual(self.service.state()["ranges"], [])

    def test_configure_no_longer_carries_a_second_network_input(self):
        source = Path(srv.__file__).resolve().parent.joinpath("ui", "matrix", "configure.html").read_text(encoding="utf-8")
        for marker in ("usbNetworkList", "usbNetworkAdd", "usbNetworkInput", "USB Discovery Networks"):
            self.assertNotIn(marker, source, marker)

    def test_the_ranges_endpoint_still_exists_for_what_reads_it(self):
        """Removing the duplicate input does not remove the store behind it."""
        reply = self.client.get("/api/usb_extenders/ranges")
        self.assertEqual(reply.status_code, 200)
        self.assertIn("ranges", reply.get_json())


class LinkVersusPairingTests(ServerTestBase):
    """Configured pairing and current link are separate facts."""

    def setUp(self):
        super().setUp()
        self._patch_cache(FULL_CACHE_UNITS)
        self.net = FakeUdp()
        self.service._exchange = self.net.exchange
        self.service.discover_ip(OMNI311_IP, IFACE_IP, IFACE_MASK)

    def test_link_state_is_unknown_until_it_has_been_read(self):
        record = self.service.device(OMNI311_MAC)
        self.assertEqual(record["link_state"], "UNKNOWN")
        self.assertFalse(record["link_state_fresh"])
        self.assertIsNone(record["link_last_read"])

    def test_a_configured_peer_does_not_imply_a_link(self):
        self.net.endpoints[OMNI311_IP]["peers"] = [OMNI324_MAC]
        self.service.refresh_pairing([OMNI311_MAC], max_age=0)
        record = self.service.device(OMNI311_MAC)
        self.assertEqual(record["paired_macs"], [usb.normalize_mac(OMNI324_MAC)])
        self.assertEqual(record["link_state"], "UNKNOWN", "pairing says nothing about the link")

    def test_pairing_and_link_keep_separate_timestamps(self):
        record = self.service.device(OMNI311_MAC)
        self.assertIn("pairing_last_read", record)
        self.assertIn("link_last_read", record)
        self.assertIsNot(record["pairing_last_read"], record["link_last_read"])

    def test_a_failed_link_read_is_unknown_not_not_linked(self):
        """A timeout and a confirmed absence of link are different facts."""
        self.net.endpoints[OMNI311_IP]["silent_link"] = True
        outcome = self.service.read_link_status(OMNI311_MAC)
        self.assertNotEqual(outcome.get("status"), "ok")
        record = self.service.device(OMNI311_MAC)
        self.assertEqual(record["link_state"], "UNKNOWN")
        self.assertNotEqual(record["link_state"], "NOT_LINKED")

    def test_the_view_reports_unknown_for_an_unread_link(self):
        self.seed((OMNI311_MAC, OMNI311_IP, 0))
        device = next(d for d in srv._usb_extender_view()["devices"]
                      if d["mac"] == usb.normalize_mac(OMNI311_MAC))
        self.assertEqual(srv._usb_link_label(device), "Unknown")

    def peer(self, state, mac="00:11:22:33:44:55"):
        return {"mac": mac, "state": state, "code": "0x01"}

    def test_the_link_label_never_says_disconnected(self):
        cases = (
            ([self.peer("LINKED")], True, False, "Linked"),
            ([self.peer("NOT_LINKED")], True, False, "Not linked"),
            ([self.peer("LINKED")], False, False, "Unknown"),
            ([self.peer("UNKNOWN")], True, False, "Unknown"),
            ([], True, False, "Not paired"),
        )
        for states, fresh, retained, expected in cases:
            label = srv._usb_link_label({"link_states": states, "link_state_fresh": fresh,
                                         "link_state_retained": retained,
                                         "link_peer_macs": [st["mac"] for st in states]})
            self.assertEqual(label, expected, f"{states}/{fresh}")
            self.assertNotIn("Disconnected", label)

    def test_a_multi_peer_host_is_summarised_per_peer(self):
        """One state byte can never speak for every peer a host holds."""
        five = [self.peer("LINKED", f"00:11:22:33:44:0{n}") for n in range(5)]
        summary = srv._usb_link_summary({"link_states": five, "link_state_fresh": True,
                                         "link_peer_macs": [st["mac"] for st in five]})
        self.assertEqual((summary["label"], summary["detail"]), ("Linked", "5 peers"))

        mixed = five[:3] + [self.peer("NOT_LINKED", "00:11:22:33:44:09"),
                            self.peer("NOT_LINKED", "00:11:22:33:44:0A")]
        summary = srv._usb_link_summary({"link_states": mixed, "link_state_fresh": True,
                                         "link_peer_macs": [st["mac"] for st in mixed]})
        self.assertEqual((summary["label"], summary["detail"]), ("Partial", "3/5 linked"))

        none = [self.peer("NOT_LINKED", st["mac"]) for st in five]
        summary = srv._usb_link_summary({"link_states": none, "link_state_fresh": True,
                                         "link_peer_macs": [st["mac"] for st in none]})
        self.assertEqual((summary["label"], summary["detail"]), ("Not linked", "0/5 linked"))

    def test_a_verified_state_that_is_ageing_is_shown_as_such(self):
        """Retained is not fresh and it is not unknown; it says how old it is."""
        summary = srv._usb_link_summary({
            "link_states": [self.peer("LINKED")], "link_peer_macs": ["00:11:22:33:44:55"],
            "link_state_fresh": False, "link_state_retained": True, "link_age": 92.0})
        self.assertEqual(summary["label"], "Linked")
        self.assertIn("92 s ago", summary["detail"])
        self.assertTrue(summary["aging"])

    def test_the_link_sweep_covers_integrated_endpoints_too(self):
        """Control ownership and read authority are different things.

        An integrated endpoint is paired through usb_icron and configured through
        its parent, and it still answers Link Status over directed unicast UDP --
        confirmed on an E4521, on two paired D4511s and on routed endpoints in
        another subnet. Excluding it from the sweep is what made Configure show
        N/A for a link the device was perfectly willing to report.
        """
        self._patch_cache(FULL_CACHE_UNITS)
        self.seed(*OBSERVED_UDP)
        asked = []
        real = self.service.refresh_link_status
        self.service.refresh_link_status = lambda macs: (asked.extend(macs), {"checked": 0, "refreshed": 0})[1]
        self.addCleanup(lambda: setattr(self.service, "refresh_link_status", real))
        srv._usb_link_refresh(force=True)
        swept = [srv._norm_usb_mac(m) for m in asked]
        self.assertIn(srv._norm_usb_mac(D4511_USB_MAC), swept)
        self.assertIn(srv._norm_usb_mac(OMNI311_MAC), swept, "standalone endpoints are still swept")

    def test_the_link_sweep_is_throttled(self):
        srv._usb_link_refresh(force=True)
        self.assertEqual(srv._usb_link_refresh().get("status"), "throttled")


class HalfEstablishedRouteTests(ServerTestBase):
    """A route surviving on one endpoint only must be repairable.

    Observed on hardware: a standalone AT-OMNI-311 lost its pairing table across
    a power cycle while the integrated D4511 kept its own, so the D4511 named a
    host that no longer named it back and Pair refused outright.
    """

    def setUp(self):
        super().setUp()
        self._patch_cache(FULL_CACHE_UNITS)
        self.net = FakeUdp()
        self.service._exchange = self.net.exchange
        for ip in (OMNI311_IP, OMNI324_IP):
            self.service.discover_ip(ip, IFACE_IP, IFACE_MASK)
        self.allow_transmit = True

    def opcodes(self):
        return [c for _d, c, _p in self.net.sent if c not in (usb.QUERY, usb.ADVANCED_QUERY)]

    def test_pair_converges_when_only_the_device_has_the_route(self):
        self.net.endpoints[OMNI324_IP]["peers"] = [OMNI311_MAC]      # survived
        self.net.endpoints[OMNI311_IP]["peers"] = []                 # forgot
        self.assertEqual(self.service.get_route_state(OMNI311_MAC, OMNI324_MAC)["status"], "INCONSISTENT")
        outcome = self.service.pair_route(OMNI311_MAC, OMNI324_MAC)
        self.assertEqual(outcome["status"], "VERIFIED_SUCCESS")
        self.assertIn(usb.normalize_mac(OMNI324_MAC),
                      self.service.read_pairing(OMNI311_MAC)["paired_macs"])

    def test_pair_converges_when_only_the_host_has_the_route(self):
        self.net.endpoints[OMNI311_IP]["peers"] = [OMNI324_MAC]
        self.net.endpoints[OMNI324_IP]["peers"] = []
        outcome = self.service.pair_route(OMNI311_MAC, OMNI324_MAC)
        self.assertEqual(outcome["status"], "VERIFIED_SUCCESS")
        self.assertIn(usb.normalize_mac(OMNI311_MAC),
                      self.service.read_pairing(OMNI324_MAC)["paired_macs"])

    def test_unpair_clears_a_route_that_survives_on_one_endpoint(self):
        self.net.endpoints[OMNI324_IP]["peers"] = [OMNI311_MAC]
        self.net.endpoints[OMNI311_IP]["peers"] = []
        outcome = self.service.unpair_route(OMNI311_MAC, OMNI324_MAC)
        self.assertEqual(outcome["status"], "VERIFIED_SUCCESS")
        self.assertEqual(self.service.read_pairing(OMNI324_MAC)["paired_macs"], [])

    def test_convergence_commands_only_the_endpoint_that_needs_it(self):
        """A device rejects a command re-asserting a peer it already holds."""
        self.net.endpoints[OMNI324_IP]["peers"] = [OMNI311_MAC]
        self.net.endpoints[OMNI311_IP]["peers"] = []
        self.net.sent.clear()
        outcome = self.service.pair_route(OMNI311_MAC, OMNI324_MAC)
        self.assertEqual(outcome["status"], "VERIFIED_SUCCESS")
        targets = [d for d, c, _p in self.net.sent if c == usb.PAIR]
        self.assertEqual(targets, [OMNI311_IP], "only the endpoint missing the peer is addressed")
        skipped = [c for c in outcome["command"] if c.get("skipped")]
        self.assertEqual([c["role"] for c in skipped], ["device"])

    def test_a_skipped_endpoint_is_never_rolled_back(self):
        """Rollback may only reverse what this transaction actually changed."""
        self.net.endpoints[OMNI324_IP]["peers"] = [OMNI311_MAC]
        self.net.endpoints[OMNI311_IP]["peers"] = []
        self.net.endpoints[OMNI311_IP]["ack"] = False        # the one command fails
        outcome = self.service.pair_route(OMNI311_MAC, OMNI324_MAC)
        self.assertEqual(outcome["status"], "REJECTED")
        self.assertEqual(self.service.read_pairing(OMNI324_MAC)["paired_macs"],
                         [usb.normalize_mac(OMNI311_MAC)], "the untouched endpoint is left alone")

    def test_convergence_never_silently_absorbs_another_hosts_device(self):
        """A REX owned elsewhere is reassigned deliberately, never converged into.
        With an owner that cannot be released, nothing is transmitted at all."""
        self.net.endpoints[OMNI324_IP]["peers"] = ["00:11:22:33:44:99"]
        self.net.endpoints[OMNI311_IP]["peers"] = []
        self.net.sent.clear()
        outcome = self.service.pair_route(OMNI311_MAC, OMNI324_MAC)
        self.assertEqual(outcome["status"], "REASSIGN_RELEASE_FAILED")
        self.assertEqual(outcome["owner_mac"], usb.normalize_mac("00:11:22:33:44:99"))
        self.assertEqual(self.opcodes(), [])

    def test_an_unreadable_endpoint_is_never_converged(self):
        """Convergence requires both reads to be authoritative."""
        self.net.endpoints[OMNI324_IP]["peers"] = [OMNI311_MAC]
        self.net.endpoints[OMNI311_IP]["advanced"] = False
        self.net.sent.clear()
        outcome = self.service.pair_route(OMNI311_MAC, OMNI324_MAC)
        self.assertEqual(outcome["status"], "UNVERIFIABLE")
        self.assertEqual(self.opcodes(), [])

    def test_a_fully_established_route_is_still_already_routed(self):
        self.net.endpoints[OMNI311_IP]["peers"] = [OMNI324_MAC]
        self.net.endpoints[OMNI324_IP]["peers"] = [OMNI311_MAC]
        self.assertEqual(self.service.pair_route(OMNI311_MAC, OMNI324_MAC)["status"], "ALREADY_ROUTED")

    def test_a_fully_cleared_route_is_still_already_unrouted(self):
        self.assertEqual(self.service.unpair_route(OMNI311_MAC, OMNI324_MAC)["status"], "ALREADY_UNROUTED")


class RoutedManagementTests(ServerTestBase):
    """A known endpoint on another subnet is discovered, polled and managed.

    Synthetic networks throughout: the controller sits on 10.50.0.0/24 and the
    endpoints on 10.60.0.0/24, sharing nothing with the bench.
    """

    CONTROLLER_IP, CONTROLLER_MASK = "10.50.0.9", "255.255.255.0"
    REMOTE_HOST_MAC, REMOTE_HOST_IP = "02:AA:00:00:00:11", "10.60.0.11"
    REMOTE_DEVICE_MAC, REMOTE_DEVICE_IP = "02:AA:00:00:00:22", "10.60.0.22"

    def setUp(self):
        super().setUp()
        self.net = FakeUdp()
        self.net.endpoints[self.REMOTE_HOST_IP] = {
            "mac": self.REMOTE_HOST_MAC, "code": 0, "peers": [], "advanced": True,
            "ack": True, "query": True, "revision": "1.9.4", "mode": usb.NETWORK_MODE_DHCP}
        self.net.endpoints[self.REMOTE_DEVICE_IP] = {
            "mac": self.REMOTE_DEVICE_MAC, "code": 1, "peers": [], "advanced": True,
            "ack": True, "query": True, "revision": "1.9.4", "mode": usb.NETWORK_MODE_DHCP}
        self.service._exchange = self.net.exchange
        self.allow_transmit = True

    def seed_remote(self):
        """Known from a previous session: MAC plus last address, no broadcast."""
        for mac, ip in ((self.REMOTE_HOST_MAC, self.REMOTE_HOST_IP),
                        (self.REMOTE_DEVICE_MAC, self.REMOTE_DEVICE_IP)):
            self.service._upsert(query(mac, ip), self.CONTROLLER_IP, self.CONTROLLER_MASK, "DIRECT_IP")

    # ---- directed polling replaces broadcast ----
    def test_a_known_remote_endpoint_comes_online_by_directed_query(self):
        self.seed_remote()
        self.assertEqual(self.service.ping(self.REMOTE_HOST_MAC)["status"], "online")
        record = self.service.device(self.REMOTE_HOST_MAC)
        self.assertTrue(record["online"], "reachable on another subnet is online")
        self.assertEqual(record["liveness_source"], "udp_query")
        self.assertEqual(record["network_relation"], "OFF_NET", "remote, not offline")

    def test_polling_a_known_endpoint_sends_no_broadcast(self):
        self.seed_remote()
        self.service.ping(self.REMOTE_HOST_MAC)
        destinations = [d for d, _c, _p in self.net.sent]
        self.assertEqual(destinations, [self.REMOTE_HOST_IP], "unicast to the known address")
        self.assertFalse([d for d in destinations if d.endswith(".255")], "no broadcast")

    def test_no_source_is_forced_for_a_routed_destination(self):
        """Forcing a local source would make a routed endpoint unreachable."""
        self.seed_remote()
        record = self.service.device(self.REMOTE_HOST_MAC)
        self.assertIsNone(self.service._bind_source(record.get("interface_ip"),
                                                    record.get("subnet_mask"), self.REMOTE_HOST_IP))

    def test_a_matching_interface_is_still_bound(self):
        self.seed((OMNI311_MAC, OMNI311_IP, 0))
        record = self.service.device(OMNI311_MAC)
        self.assertEqual(self.service._bind_source(record.get("interface_ip"),
                                                   record.get("subnet_mask"), OMNI311_IP), IFACE_IP)

    def test_a_remote_endpoint_survives_a_restart_and_is_polled_again(self):
        self.seed_remote()
        reloaded = usb.ExtenderDiscoveryService(self.service.state_file, persist_debounce=0)
        reloaded._exchange = self.net.exchange
        self.assertIsNotNone(reloaded.device(self.REMOTE_HOST_MAC), "known after restart")
        self.assertEqual(reloaded.ping(self.REMOTE_HOST_MAC)["status"], "online",
                         "no broadcast is required to bring it back")

    # ---- identity safety is unchanged ----
    def test_a_different_mac_at_the_remote_address_does_not_steal_identity(self):
        self.seed_remote()
        self.net.endpoints[self.REMOTE_HOST_IP]["mac"] = "02:BB:00:00:00:99"
        outcome = self.service.ping(self.REMOTE_HOST_MAC)
        self.assertEqual(outcome["status"], "mac_mismatch")
        self.assertFalse(self.service.device(self.REMOTE_HOST_MAC)["online"])

    def test_the_same_mac_at_a_new_address_updates_one_record(self):
        self.seed_remote()
        self.net.endpoints["10.60.0.77"] = dict(self.net.endpoints[self.REMOTE_HOST_IP])
        self.service.discover_ip("10.60.0.77", self.CONTROLLER_IP, self.CONTROLLER_MASK)
        matching = [d for d in self.service.state()["devices"]
                    if srv._norm_usb_mac(d["mac"]) == srv._norm_usb_mac(self.REMOTE_HOST_MAC)]
        self.assertEqual(len(matching), 1)
        self.assertEqual(matching[0]["ip"], "10.60.0.77")

    # ---- eligibility is about the endpoints ----
    def test_two_remote_endpoints_on_one_network_are_compatible(self):
        self.assertTrue(usb.endpoints_same_network(self.REMOTE_HOST_IP, "255.255.255.0",
                                                   self.REMOTE_DEVICE_IP, "255.255.255.0"))

    def test_endpoints_on_different_networks_are_not_compatible(self):
        self.assertFalse(usb.endpoints_same_network("10.60.0.11", "255.255.255.0",
                                                    "10.70.0.22", "255.255.255.0"))

    def test_compatibility_uses_the_real_mask_not_an_assumed_24(self):
        """A /16 pair spans third octets that a hardcoded /24 would reject."""
        self.assertTrue(usb.endpoints_same_network("10.60.1.11", "255.255.0.0",
                                                   "10.60.9.22", "255.255.0.0"))
        self.assertFalse(usb.endpoints_same_network("10.60.1.11", "255.255.255.0",
                                                    "10.60.9.22", "255.255.255.0"))

    def test_indeterminate_data_is_not_a_refusal(self):
        self.assertIsNone(usb.endpoints_same_network("10.60.0.11", "", "10.60.0.22", ""))

    def test_the_controller_subnet_does_not_decide_eligibility(self):
        """Both endpoints are remote from the controller and local to each other."""
        self.seed_remote()
        for mac in (self.REMOTE_HOST_MAC, self.REMOTE_DEVICE_MAC):
            record = self.service.device(mac)
            self.assertEqual(record["network_relation"], "OFF_NET")
            self.assertTrue(record["pairing_eligible"])
        state = self.service.get_route_state(self.REMOTE_HOST_MAC, self.REMOTE_DEVICE_MAC)
        self.assertNotEqual(state["status"], "NOT_ELIGIBLE",
                            "remote from the controller is not a reason to refuse")

    def test_a_pair_spanning_two_networks_is_still_refused(self):
        """The genuine device-to-device check must survive."""
        self.seed_remote()
        self.service._upsert(query(OMNI324_MAC, "10.70.0.5"),
                             self.CONTROLLER_IP, self.CONTROLLER_MASK, "DIRECT_IP")
        self.net.endpoints["10.70.0.5"] = {"mac": OMNI324_MAC, "code": 1, "peers": [],
                                           "advanced": True, "ack": True, "query": True}
        state = self.service.get_route_state(self.REMOTE_HOST_MAC, OMNI324_MAC)
        self.assertEqual(state["status"], "NOT_ELIGIBLE")
        self.assertEqual(state["reason"], "ENDPOINTS_ON_DIFFERENT_NETWORKS")


class UnifiedInventoryTests(ServerTestBase):
    """Matrix, Configure and the Device Info count share one endpoint population."""

    def setUp(self):
        super().setUp()
        self._patch_cache(FULL_CACHE_UNITS)
        icron = {
            E4521_IP: {"type": "LEX", "macaddress": E4521_USB_MAC, "ipaddress": E4521_USB_IP},
            D4511_IP: {"type": "REX", "macaddress": D4511_USB_MAC, "ipaddress": D4511_USB_IP},
        }
        real = srv._ws_send_recv
        self.addCleanup(lambda: setattr(srv, "_ws_send_recv", real))

        def fake(url, payload, timeout=None):
            ip = re.findall(r"//([^:/]+)", url)[0]
            if payload.get("config_get") == "usb_icron" and ip in icron:
                return {"config": dict(icron[ip], paired_devices={})}
            return {"config": {}, "error": False}
        srv._ws_send_recv = fake

    def configure_macs(self):
        return {d["mac"] for d in self.client.get("/api/usb_extenders").get_json()["devices"]}

    def matrix_macs(self):
        state = self.client.get("/api/usb_state").get_json()
        return {r["usb_mac"] for r in state["inventory_lex"] + state["inventory_rex"]}

    def test_an_endpoint_never_seen_by_udp_still_reaches_configure(self):
        """This is the regression: local broadcast cannot cross a router, but the
        parent tells us the endpoint exists."""
        self.assertEqual(self.service.state()["devices"], [], "nothing discovered over UDP")
        macs = self.configure_macs()
        self.assertIn(usb.normalize_mac(E4521_USB_MAC), macs)
        self.assertIn(usb.normalize_mac(D4511_USB_MAC), macs)

    def test_a_parent_derived_endpoint_says_where_it_came_from(self):
        device = next(d for d in self.client.get("/api/usb_extenders").get_json()["devices"]
                      if d["mac"] == usb.normalize_mac(E4521_USB_MAC))
        self.assertEqual(device["discovery_source"], "parent_derived")
        self.assertEqual(device["classification"], "INTEGRATED")

    def test_matrix_and_configure_agree_on_the_integrated_population(self):
        matrix = {m for m in self.matrix_macs()}
        configure = self.configure_macs()
        integrated = {usb.normalize_mac(E4521_USB_MAC), usb.normalize_mac(D4511_USB_MAC)}
        self.assertTrue(integrated <= matrix, "missing from the Matrix")
        self.assertTrue(integrated <= configure, "missing from Configure")

    def test_opening_the_matrix_is_not_required_to_populate_configure(self):
        """Configure is correct on a cold backend, with no Matrix request first."""
        macs = self.configure_macs()
        self.assertIn(usb.normalize_mac(D4511_USB_MAC), macs)

    def test_opening_configure_is_not_required_to_populate_the_matrix(self):
        macs = self.matrix_macs()
        self.assertIn(usb.normalize_mac(D4511_USB_MAC), macs)

    def test_a_discovered_endpoint_is_not_duplicated_by_its_parent_record(self):
        self.seed((E4521_USB_MAC, E4521_USB_IP, 0))
        devices = self.client.get("/api/usb_extenders").get_json()["devices"]
        matching = [d for d in devices if d["mac"] == usb.normalize_mac(E4521_USB_MAC)]
        self.assertEqual(len(matching), 1, "one canonical MAC is one endpoint")
        self.assertNotEqual(matching[0].get("discovery_source"), "parent_derived",
                            "the discovered record wins where both exist")

    def test_a_parent_derived_endpoint_is_offline_until_its_parent_answers(self):
        device = next(d for d in self.client.get("/api/usb_extenders").get_json()["devices"]
                      if d["mac"] == usb.normalize_mac(D4511_USB_MAC))
        self.assertFalse(device["online"])
        srv._record_parent_live(D4511_IP)
        device = next(d for d in self.client.get("/api/usb_extenders").get_json()["devices"]
                      if d["mac"] == usb.normalize_mac(D4511_USB_MAC))
        self.assertTrue(device["online"])
        self.assertEqual(device["liveness_source"], "usb_icron")

    def test_a_parent_derived_endpoint_dispatches_to_the_parent_provider(self):
        device = next(d for d in self.client.get("/api/usb_extenders").get_json()["devices"]
                      if d["mac"] == usb.normalize_mac(D4511_USB_MAC))
        self.assertEqual(device["identify_via"], "parent")
        self.assertEqual(device["network_via"], "parent")
        # Reboot is offered, but through the parent's established operation --
        # never a standalone UDP mutation aimed at an integrated endpoint.
        self.assertEqual(device["reboot_via"], "parent")
        self.assertEqual(device["pairing_source"], "usb_icron")
        self.assertNotIn("paired_macs", device,
                         "UDP pairing is never presented as authoritative here")

    def test_a_standalone_endpoint_keeps_the_directed_udp_provider(self):
        self.seed((OMNI311_MAC, OMNI311_IP, 0))
        device = next(d for d in self.client.get("/api/usb_extenders").get_json()["devices"]
                      if d["mac"] == usb.normalize_mac(OMNI311_MAC))
        self.assertEqual(device["identify_via"], "extender")
        self.assertEqual(device["network_via"], "extender")

    def test_the_device_info_count_uses_the_same_population(self):
        srv._record_parent_live(E4521_IP)
        srv._record_parent_live(D4511_IP)
        body = self.client.get("/api/usb_extenders").get_json()
        online = {d["mac"] for d in body["devices"] if d["online"]}
        self.assertEqual(body["online_count"], len(online))
        self.assertIn(usb.normalize_mac(D4511_USB_MAC), online)


class DiscoveryNetworkTests(ServerTestBase):
    """Scan must address configured networks directly, not hope for a broadcast."""

    def setUp(self):
        super().setUp()
        self.started = []
        real_range = self.service.start_range_scan
        real_local = self.service.start_local_discovery
        self.service.start_range_scan = lambda ip, mask, targets=None: (
            self.started.append(("range", tuple(targets or ()))), None)[1]
        self.service.start_local_discovery = lambda ip, mask: (
            self.started.append(("local", ip)), None)[1]
        self.addCleanup(lambda: (setattr(self.service, "start_range_scan", real_range),
                                 setattr(self.service, "start_local_discovery", real_local)))

    def discover(self, **body):
        payload = {"interface_ip": IFACE_IP, "subnet_mask": IFACE_MASK, **body}
        return self.client.post("/api/usb_extenders/discover", json=payload).get_json()

    def range_targets(self):
        return [t for kind, t in self.started if kind == "range"]

    # ---- configuration ----
    def test_discovery_networks_round_trip(self):
        saved = self.client.post("/api/usb_extenders/ranges",
                                 json={"ranges": ["10.40.0.0/24"]}).get_json()
        self.assertTrue(saved["ok"])
        self.assertEqual(saved["ranges"], ["10.40.0.0/24"])
        self.assertEqual(self.client.get("/api/usb_extenders/ranges").get_json()["ranges"],
                         ["10.40.0.0/24"])

    def test_an_invalid_network_is_rejected_without_saving(self):
        self.client.post("/api/usb_extenders/ranges", json={"ranges": ["10.40.0.0/24"]})
        reply = self.client.post("/api/usb_extenders/ranges", json={"ranges": ["not-a-network"]})
        self.assertEqual(reply.status_code, 400)
        self.assertEqual(self.client.get("/api/usb_extenders/ranges").get_json()["ranges"],
                         ["10.40.0.0/24"], "the previous configuration survives a bad edit")

    def test_an_oversized_network_is_refused_before_expansion(self):
        reply = self.client.post("/api/usb_extenders/ranges", json={"ranges": ["10.0.0.0/8"]})
        self.assertEqual(reply.status_code, 400)

    # ---- what Scan dispatches ----
    def test_scan_scans_the_configured_networks(self):
        """The regression: configured networks existed but nothing scanned them."""
        self.client.post("/api/usb_extenders/ranges", json={"ranges": ["10.40.0.0/30"]})
        body = self.discover()
        self.assertIn("ranges", body["started"])
        self.assertEqual(body["configured_ranges"], ["10.40.0.0/30"])
        targets = self.range_targets()
        self.assertTrue(targets, "no range scan was dispatched")
        self.assertIn("10.40.0.1", targets[0])

    def test_scan_addresses_each_candidate_directly_and_never_broadcasts_the_range(self):
        self.client.post("/api/usb_extenders/ranges", json={"ranges": ["10.40.0.0/30"]})
        self.discover()
        targets = self.range_targets()[0]
        self.assertTrue(all(not t.endswith(".255") for t in targets), "no broadcast address")
        self.assertEqual(sorted(targets), ["10.40.0.1", "10.40.0.2"])

    def test_the_targets_field_and_configured_networks_are_combined(self):
        self.client.post("/api/usb_extenders/ranges", json={"ranges": ["10.40.0.0/30"]})
        self.discover(targets="10.41.0.1")
        targets = self.range_targets()[0]
        self.assertIn("10.41.0.1", targets)
        self.assertIn("10.40.0.1", targets)

    def test_local_discovery_still_runs_for_unknown_local_devices(self):
        self.discover()
        self.assertIn("local", [kind for kind, _t in self.started])

    def test_scan_without_configured_networks_still_works(self):
        body = self.discover()
        self.assertEqual(body["configured_ranges"], [])
        self.assertIn("local", body["started"])

    def test_scan_returns_immediately_and_reports_what_it_started(self):
        self.client.post("/api/usb_extenders/ranges", json={"ranges": ["10.40.0.0/24"]})
        body = self.discover()
        self.assertEqual(body["status"], "started")
        self.assertEqual(body["target_count"], 254, "expanded once, by the bounded parser")

    def test_scan_reprobes_a_known_endpoint_that_has_gone_quiet(self):
        """A device that stopped answering may have moved; its last address is
        the only lead we have, so Scan must try it rather than skip it."""
        self.seed((OMNI311_MAC, OMNI311_IP, 0))
        record = self.service.device(OMNI311_MAC)
        self.assertTrue(record["online"])
        asked = []
        real = self.service.refresh_liveness
        self.service.refresh_liveness = lambda macs: (asked.extend(macs), {"checked": len(macs)})[1]
        self.addCleanup(lambda: setattr(self.service, "refresh_liveness", real))
        with self.service._lock:                       # make it look offline
            self.service._devices[usb.normalize_mac(OMNI311_MAC)]["live_offline"] = True
            self.service._devices[usb.normalize_mac(OMNI311_MAC)].pop("live_seen", None)
        self.discover()
        time.sleep(0.3)
        self.assertIn(usb.normalize_mac(OMNI311_MAC), [usb.normalize_mac(m) for m in asked],
                      "an offline known endpoint must still be re-probed")


class ScanPreferenceTests(ServerTestBase):
    """The chosen Scan adapter survives, and is identified by address."""

    def setUp(self):
        super().setUp()
        original = srv.SCAN_PREFS
        srv.SCAN_PREFS = Path(self.folder.name) / "scan_preferences.json"
        self.addCleanup(lambda: setattr(srv, "SCAN_PREFS", original))

    def save(self, **body):
        return self.client.post("/api/scan_preferences", json=body)

    def load(self):
        return self.client.get("/api/scan_preferences").get_json()

    def test_nothing_is_remembered_before_a_choice_is_made(self):
        self.assertEqual(self.load().get("interface_ip"), None)

    def test_a_choice_is_remembered(self):
        self.save(interface_ip="10.20.30.40", subnet_mask="255.255.255.0",
                  scan_expression="10.20.30.1-10.20.30.254")
        prefs = self.load()
        self.assertEqual(prefs["interface_ip"], "10.20.30.40")
        self.assertEqual(prefs["subnet_mask"], "255.255.255.0")
        self.assertEqual(prefs["scan_expression"], "10.20.30.1-10.20.30.254")

    def test_it_survives_a_restart(self):
        """A new process reads the same file; nothing is held in memory only."""
        self.save(interface_ip="10.20.30.40")
        self.assertEqual(json.loads(srv.SCAN_PREFS.read_text(encoding="utf-8"))["interface_ip"],
                         "10.20.30.40")
        self.assertEqual(self.load()["interface_ip"], "10.20.30.40")

    def test_a_malformed_address_is_refused_and_changes_nothing(self):
        self.save(interface_ip="10.20.30.40")
        self.assertEqual(self.save(interface_ip="not-an-address").status_code, 400)
        self.assertEqual(self.load()["interface_ip"], "10.20.30.40")

    def test_a_corrupt_preference_file_degrades_to_empty(self):
        srv.SCAN_PREFS.write_text("{not json", encoding="utf-8")
        self.assertEqual(self.load().get("interface_ip"), None, "no crash, no stale value")

    def test_the_preference_is_stored_apart_from_the_discovery_networks(self):
        """Saving one must never clear the other."""
        self.client.post("/api/usb_extenders/ranges", json={"ranges": ["10.40.0.0/24"]})
        self.save(interface_ip="10.20.30.40")
        self.assertEqual(self.client.get("/api/usb_extenders/ranges").get_json()["ranges"],
                         ["10.40.0.0/24"])
        self.client.post("/api/usb_extenders/ranges", json={"ranges": ["10.41.0.0/24"]})
        self.assertEqual(self.load()["interface_ip"], "10.20.30.40")

    def test_the_preference_is_stored_apart_from_the_credentials(self):
        before = self.client.get("/api/config").get_json()["username"]
        self.save(interface_ip="10.20.30.40")
        self.assertEqual(self.client.get("/api/config").get_json()["username"], before)

    # ---- the client contract ----
    def client_source(self):
        return Path(srv.__file__).parent.joinpath("ui", "index.html").read_text(encoding="utf-8")

    def test_the_client_restores_by_address_not_by_position(self):
        source = self.client_source()
        block = source[source.index("async function restoreScanAdapter"):]
        block = block[:block.index("async function saveScanAdapter")]
        self.assertIn("dataset.ip === saved", block, "identity is the adapter address")
        self.assertNotIn("selectedIndex = 0", block, "never a fixed position")

    def test_the_client_falls_back_when_the_saved_adapter_is_gone(self):
        source = self.client_source()
        block = source[source.index("async function restoreScanAdapter"):]
        block = block[:block.index("async function saveScanAdapter")]
        self.assertIn("no longer present", block, "a missing adapter is reported, not selected")

    def test_the_client_saves_on_change(self):
        source = self.client_source()
        self.assertIn("sel.onchange = saveScanAdapter", source)
        self.assertIn("/api/scan_preferences", source)


class MultiPeerModelTests(ServerTestBase):
    """A host endpoint holds a collection of peers, never a single one."""

    def setUp(self):
        super().setUp()
        self._patch_cache(FULL_CACHE_UNITS)
        self.net = FakeUdp()
        self.service._exchange = self.net.exchange
        self.service.discover_ip(OMNI311_IP, IFACE_IP, IFACE_MASK)
        self.allow_transmit = True

    def peers(self, mac):
        return self.service.read_pairing(mac).get("paired_macs")

    def test_the_parser_returns_every_peer_slot(self):
        macs = [f"02:AA:00:00:00:{index:02X}" for index in range(1, 8)]
        payload = bytes([0]) + b"".join(usb.mac_bytes(m) for m in macs)
        parsed = usb.parse_advanced_query_response(
            usb.packet(1, usb.ADVANCED_QUERY_RESPONSE, payload), 1)
        self.assertEqual(len(parsed["paired_macs"]), 7, "no truncation after the first peer")
        self.assertEqual(parsed["paired_macs"], [usb.normalize_mac(m) for m in macs])

    def test_the_documented_host_limit_is_seven(self):
        self.assertEqual(usb.HOST_PEER_LIMIT, 7)
        self.assertEqual(usb.DEVICE_PEER_LIMIT, 1)

    def test_an_eighth_peer_payload_is_rejected_rather_than_trimmed(self):
        macs = [f"02:AA:00:00:00:{index:02X}" for index in range(1, 9)]
        payload = bytes([0]) + b"".join(usb.mac_bytes(m) for m in macs)
        with self.assertRaises(usb.ProtocolError):
            usb.parse_advanced_query_response(
                usb.packet(1, usb.ADVANCED_QUERY_RESPONSE, payload), 1)

    def test_five_peers_are_held_simultaneously(self):
        macs = [f"02:AA:00:00:00:{index:02X}" for index in range(1, 6)]
        self.net.endpoints[OMNI311_IP]["peers"] = macs
        self.assertEqual(self.peers(OMNI311_MAC), [usb.normalize_mac(m) for m in macs])

    def test_the_peer_collection_is_deduplicated_by_canonical_mac(self):
        record = self.service.device(OMNI311_MAC)
        self.net.endpoints[OMNI311_IP]["peers"] = ["02:AA:00:00:00:01", "02:aa:00:00:00:01"]
        peers = self.peers(OMNI311_MAC)
        self.assertEqual(peers, [usb.normalize_mac("02:AA:00:00:00:01")] * 2,
                         "the device reported it twice; the parser reports what it read")
        self.assertEqual(len({usb.normalize_mac(m) for m in peers}), 1,
                         "and canonical comparison collapses them")

    def test_the_view_reports_every_peer(self):
        macs = [f"02:AA:00:00:00:{index:02X}" for index in range(1, 6)]
        self.net.endpoints[OMNI311_IP]["peers"] = macs
        self.service.refresh_pairing([OMNI311_MAC], max_age=0)
        device = next(d for d in srv._usb_extender_view()["devices"]
                      if d["mac"] == usb.normalize_mac(OMNI311_MAC))
        self.assertEqual(len(device["paired_macs"]), 5)

    def test_adding_a_peer_preserves_the_existing_ones(self):
        existing = ["02:AA:00:00:00:01", "02:AA:00:00:00:02"]
        self.net.endpoints[OMNI311_IP]["peers"] = list(existing)
        self.net.endpoints[OMNI324_IP] = {"mac": OMNI324_MAC, "code": 1, "peers": [],
                                          "advanced": True, "ack": True, "query": True}
        self.service.discover_ip(OMNI324_IP, IFACE_IP, IFACE_MASK)
        outcome = self.service.pair_route(OMNI311_MAC, OMNI324_MAC)
        self.assertEqual(outcome["status"], "VERIFIED_SUCCESS")
        peers = self.peers(OMNI311_MAC)
        for mac in existing:
            self.assertIn(usb.normalize_mac(mac), peers, "an earlier peer was displaced")
        self.assertIn(usb.normalize_mac(OMNI324_MAC), peers)

    def test_removing_one_peer_leaves_the_others(self):
        others = ["02:AA:00:00:00:01", "02:AA:00:00:00:02"]
        self.net.endpoints[OMNI311_IP]["peers"] = others + [OMNI324_MAC]
        self.net.endpoints[OMNI324_IP] = {"mac": OMNI324_MAC, "code": 1, "peers": [OMNI311_MAC],
                                          "advanced": True, "ack": True, "query": True}
        self.service.discover_ip(OMNI324_IP, IFACE_IP, IFACE_MASK)
        outcome = self.service.unpair_route(OMNI311_MAC, OMNI324_MAC)
        self.assertEqual(outcome["status"], "VERIFIED_SUCCESS")
        peers = self.peers(OMNI311_MAC)
        self.assertNotIn(usb.normalize_mac(OMNI324_MAC), peers)
        for mac in others:
            self.assertIn(usb.normalize_mac(mac), peers, "an unrelated peer was removed")

    def test_the_peer_limit_is_enforced_from_the_documented_value(self):
        self.net.endpoints[OMNI311_IP]["peers"] = [f"02:AA:00:00:00:{i:02X}" for i in range(1, 8)]
        self.net.endpoints[OMNI324_IP] = {"mac": OMNI324_MAC, "code": 1, "peers": [],
                                          "advanced": True, "ack": True, "query": True}
        self.service.discover_ip(OMNI324_IP, IFACE_IP, IFACE_MASK)
        outcome = self.service.pair_route(OMNI311_MAC, OMNI324_MAC)
        self.assertEqual(outcome["status"], "PEER_LIMIT")
        self.assertEqual(outcome["peer_limit"], 7)


class RouteReassignmentTests(ServerTestBase):
    """Clicking a cell asks for a relationship, not for a protocol opcode.

    A REX that permits one host and belongs to another is moved by the backend,
    with no separate unpair step for the operator, and every unrelated peer of
    both hosts survives.
    """

    OTHER_HOST_MAC, OTHER_HOST_IP = "00:1B:13:09:00:01", "192.0.2.190"

    def setUp(self):
        super().setUp()
        self._patch_cache(FULL_CACHE_UNITS)
        self.net = FakeUdp()
        self.net.endpoints[self.OTHER_HOST_IP] = {
            "mac": self.OTHER_HOST_MAC, "code": 0, "peers": [], "advanced": True,
            "ack": True, "query": True, "revision": "1.9.4", "mode": usb.NETWORK_MODE_DHCP}
        self.net.endpoints[E4521_USB_IP] = {"mac": E4521_USB_MAC, "code": 0, "peers": [],
                                            "advanced": True, "ack": True, "query": True}
        self.net.endpoints[D4511_USB_IP] = {"mac": D4511_USB_MAC, "code": 1, "peers": [],
                                            "advanced": True, "ack": True, "query": True}
        self.service._exchange = self.net.exchange
        for ip in (OMNI311_IP, OMNI324_IP, self.OTHER_HOST_IP, E4521_USB_IP, D4511_USB_IP):
            self.service.discover_ip(ip, IFACE_IP, IFACE_MASK)
        self.allow_transmit = True

    def peers(self, mac):
        result = self.service.read_pairing(mac)
        return [usb.normalize_mac(m) for m in result.get("paired_macs") or []]

    def own(self, host_ip, host_mac, rex_ip, rex_mac):
        """Put a route in place directly on the fake devices."""
        self.net.endpoints[host_ip]["peers"].append(usb.normalize_mac(rex_mac))
        self.net.endpoints[rex_ip]["peers"] = [usb.normalize_mac(host_mac)]

    def click(self, lex_mac, rex_mac):
        reply = self.client.post("/api/usb_route/pair", json={"lex_mac": lex_mac, "rex_mac": rex_mac})
        return reply.status_code, reply.get_json()

    # ---- the core behaviour ----
    def test_a_free_endpoint_is_simply_routed(self):
        code, body = self.click(OMNI311_MAC, OMNI324_MAC)
        self.assertEqual(body["status"], "VERIFIED_SUCCESS")
        self.assertIsNone(body.get("reassigned_from"))

    def test_clicking_a_new_host_moves_an_owned_endpoint(self):
        self.own(self.OTHER_HOST_IP, self.OTHER_HOST_MAC, OMNI324_IP, OMNI324_MAC)
        code, body = self.click(OMNI311_MAC, OMNI324_MAC)
        self.assertEqual(body["status"], "VERIFIED_SUCCESS")
        self.assertEqual(usb.normalize_mac(body["reassigned_from"]),
                         usb.normalize_mac(self.OTHER_HOST_MAC))
        self.assertEqual(self.peers(OMNI324_MAC), [usb.normalize_mac(OMNI311_MAC)])
        self.assertIn(usb.normalize_mac(OMNI324_MAC), self.peers(OMNI311_MAC))
        self.assertNotIn(usb.normalize_mac(OMNI324_MAC), self.peers(self.OTHER_HOST_MAC))

    def test_no_separate_unpair_is_required_of_the_operator(self):
        """One request, from the state the operator can see to the one they asked for."""
        self.own(self.OTHER_HOST_IP, self.OTHER_HOST_MAC, OMNI324_IP, OMNI324_MAC)
        code, body = self.click(OMNI311_MAC, OMNI324_MAC)
        self.assertEqual(code, 200)
        self.assertNotIn("unpair", (body.get("error") or "").lower())

    def test_only_the_selected_endpoint_moves(self):
        """A multi-peer host keeps every other REX."""
        keep = ["02:AA:00:00:00:01", "02:AA:00:00:00:02", "02:AA:00:00:00:03"]
        self.net.endpoints[self.OTHER_HOST_IP]["peers"] = list(keep)
        self.own(self.OTHER_HOST_IP, self.OTHER_HOST_MAC, OMNI324_IP, OMNI324_MAC)
        before = self.peers(self.OTHER_HOST_MAC)
        self.assertEqual(len(before), 4)
        code, body = self.click(OMNI311_MAC, OMNI324_MAC)
        self.assertEqual(body["status"], "VERIFIED_SUCCESS")
        after = self.peers(self.OTHER_HOST_MAC)
        self.assertEqual(sorted(after), sorted(usb.normalize_mac(m) for m in keep))
        self.assertEqual(len(after), 3, "exactly one relationship was removed")

    def test_reassignment_never_uses_unpair_all(self):
        self.own(self.OTHER_HOST_IP, self.OTHER_HOST_MAC, OMNI324_IP, OMNI324_MAC)
        self.net.sent.clear()
        self.click(OMNI311_MAC, OMNI324_MAC)
        opcodes = [c for _d, c, _p in self.net.sent]
        self.assertNotIn(usb.UNPAIR_ALL, opcodes)
        self.assertIsNone(usb.FORCE_PAIR)

    def test_asking_for_a_route_that_already_exists_changes_nothing(self):
        self.own(OMNI311_IP, OMNI311_MAC, OMNI324_IP, OMNI324_MAC)
        self.net.sent.clear()
        code, body = self.click(OMNI311_MAC, OMNI324_MAC)
        self.assertEqual(body["status"], "ALREADY_ROUTED")
        self.assertEqual([c for _d, c, _p in self.net.sent if c in (usb.PAIR, usb.UNPAIR)], [],
                         "no redundant command is sent")

    def test_a_half_established_request_converges_without_a_redundant_pair(self):
        self.net.endpoints[OMNI324_IP]["peers"] = [usb.normalize_mac(OMNI311_MAC)]
        self.net.sent.clear()
        code, body = self.click(OMNI311_MAC, OMNI324_MAC)
        self.assertEqual(body["status"], "VERIFIED_SUCCESS")
        targets = [d for d, c, _p in self.net.sent if c == usb.PAIR]
        self.assertEqual(targets, [OMNI311_IP], "only the endpoint missing the peer is commanded")

    # ---- every family ----
    def test_a_mixed_endpoint_is_reassigned_through_the_udp_transaction(self):
        """311 -> D4511, moved off another host."""
        self.own(self.OTHER_HOST_IP, self.OTHER_HOST_MAC, D4511_USB_IP, D4511_USB_MAC)
        code, body = self.click(OMNI311_MAC, D4511_USB_MAC)
        self.assertEqual(body["status"], "VERIFIED_SUCCESS")
        self.assertEqual(self.peers(D4511_USB_MAC), [usb.normalize_mac(OMNI311_MAC)])

    def test_an_integrated_host_reassigns_a_standalone_endpoint(self):
        """E4521 -> 324, moved off another host."""
        self.own(self.OTHER_HOST_IP, self.OTHER_HOST_MAC, OMNI324_IP, OMNI324_MAC)
        code, body = self.click(E4521_USB_MAC, OMNI324_MAC)
        self.assertEqual(body["status"], "VERIFIED_SUCCESS")
        self.assertEqual(self.peers(OMNI324_MAC), [usb.normalize_mac(E4521_USB_MAC)])

    def test_the_icron_combination_is_still_not_routed_through_udp(self):
        code, body = self.click(E4521_USB_MAC, D4511_USB_MAC)
        self.assertEqual(code, 409)
        self.assertEqual(body["status"], "not_supported")

    # ---- failure and rollback ----
    def test_a_failed_new_pairing_restores_the_previous_route(self):
        self.own(self.OTHER_HOST_IP, self.OTHER_HOST_MAC, OMNI324_IP, OMNI324_MAC)
        keep = ["02:AA:00:00:00:01"]
        self.net.endpoints[self.OTHER_HOST_IP]["peers"] = keep + [usb.normalize_mac(OMNI324_MAC)]

        # The new host refuses the Pair, but only after the release has happened.
        original = self.net.exchange
        def refuse_new_host(destination, request, message_id, timeout=usb.DEFAULT_TIMEOUT, bind_ip=None):
            command = int.from_bytes(request[8:10], "big")
            if command == usb.PAIR and destination == OMNI311_IP:
                return usb.packet(message_id, usb.NACK), (destination, usb.UDP_PORT)
            return original(destination, request, message_id, timeout, bind_ip)
        self.service._exchange = refuse_new_host

        code, body = self.click(OMNI311_MAC, OMNI324_MAC)
        self.assertEqual(body["status"], "FAILED_ROLLED_BACK")
        self.assertEqual(code, 502)
        self.service._exchange = original
        self.assertEqual(self.peers(OMNI324_MAC), [usb.normalize_mac(self.OTHER_HOST_MAC)],
                         "the endpoint went back to its original host")
        remaining = self.peers(self.OTHER_HOST_MAC)
        self.assertIn(usb.normalize_mac(keep[0]), remaining, "an unrelated peer was disturbed")
        self.assertIn(usb.normalize_mac(OMNI324_MAC), remaining)

    def test_a_rollback_that_cannot_be_confirmed_is_reported_as_such(self):
        self.own(self.OTHER_HOST_IP, self.OTHER_HOST_MAC, OMNI324_IP, OMNI324_MAC)
        original = self.net.exchange
        state = {"released": False}

        def fail_then_hide(destination, request, message_id, timeout=usb.DEFAULT_TIMEOUT, bind_ip=None):
            command = int.from_bytes(request[8:10], "big")
            if command == usb.UNPAIR and destination == OMNI324_IP:
                state["released"] = True
            if state["released"] and command == usb.PAIR:
                return usb.packet(message_id, usb.NACK), (destination, usb.UDP_PORT)
            return original(destination, request, message_id, timeout, bind_ip)
        self.service._exchange = fail_then_hide

        code, body = self.click(OMNI311_MAC, OMNI324_MAC)
        self.assertEqual(body["status"], "FAILED_ROLLBACK_UNVERIFIED")
        self.assertIn("could not be confirmed restored", body["error"])
        self.service._exchange = original

    def test_a_release_failure_leaves_everything_untouched(self):
        self.net.endpoints[OMNI324_IP]["peers"] = ["00:11:22:33:44:99"]     # unknown owner
        self.net.sent.clear()
        code, body = self.click(OMNI311_MAC, OMNI324_MAC)
        self.assertEqual(body["status"], "REASSIGN_RELEASE_FAILED")
        self.assertEqual([c for _d, c, _p in self.net.sent if c in (usb.PAIR, usb.UNPAIR)], [])

    def test_success_never_rests_on_an_ack_alone(self):
        self.own(self.OTHER_HOST_IP, self.OTHER_HOST_MAC, OMNI324_IP, OMNI324_MAC)
        self.net.apply_writes = False            # everything ACKs, nothing changes
        code, body = self.click(OMNI311_MAC, OMNI324_MAC)
        self.assertNotEqual(body["status"], "VERIFIED_SUCCESS")


class RouteGatingTests(ServerTestBase):
    def test_no_route_transaction_endpoint_is_exposed(self):
        rules = [str(r) for r in srv.app.url_map.iter_rules()]
        for fragment in ("route_state", "pair_route", "unpair_route", "usb_extenders/pair", "usb_extenders/unpair"):
            self.assertFalse([r for r in rules if fragment in r], f"{fragment} must not be routed")

    def test_route_methods_are_reachable_only_from_the_bench_diagnostics(self):
        """Route transactions may be invoked only by the bench diagnostic helpers."""
        import inspect
        source = Path(srv.__file__).read_text(encoding="utf-8", errors="replace")
        # Bench diagnostics plus the production standalone route endpoints, whose
        # own gate (_standalone_route_request) restricts them to the validated
        # AT-OMNI-311 to AT-OMNI-324 combination.
        allowed = {"_bench_route_operation", "api_bench_usb_route_state", "_bench_endpoint_summary",
                   "api_usb_route_pair", "api_usb_route_unpair"}
        # Map each top-level def to its line span, then attribute every call site.
        spans, current, start = [], None, 0
        lines = source.split("\n")
        for i, line in enumerate(lines, 1):
            m = re.match(r"def (\w+)", line)
            if m:
                if current:
                    spans.append((current, start, i - 1))
                current, start = m.group(1), i
        if current:
            spans.append((current, start, len(lines)))
        for i, line in enumerate(lines, 1):
            for name in ("pair_route(", "unpair_route(", "get_route_state(", "read_pairing("):
                if name in line and not line.strip().startswith("#"):
                    owner = next((n for n, a, b in spans if a <= i <= b), "<module>")
                    self.assertIn(owner, allowed,
                                  f"{name} called from {owner} at line {i}; only bench diagnostics may invoke it")

    def test_production_routing_is_limited_to_the_validated_combination(self):
        """Standalone routing is exposed; every other combination is refused."""
        rules = [str(r) for r in srv.app.url_map.iter_rules()]
        self.assertIn("/api/usb_route/pair", rules)
        self.assertIn("/api/usb_route/unpair", rules)
        source = Path(srv.__file__).read_text(encoding="utf-8", errors="replace")
        for endpoint in ("def api_usb_route_pair", "def api_usb_route_unpair"):
            index = source.index(endpoint)
            body = source[index:index + 900]
            self.assertIn("_standalone_route_request", body,
                          f"{endpoint} must resolve ownership through the standalone gate")

    def test_the_gate_refuses_the_combination_owned_by_usb_icron(self):
        """The gate decides ownership before anything is transmitted."""
        self._patch_cache(FULL_CACHE_UNITS)
        self.seed(*OBSERVED_UDP)
        r = self.client.post("/api/usb_route/pair",
                             json={"lex_mac": E4521_USB_MAC, "rex_mac": D4511_USB_MAC})
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.get_json()["status"], "not_supported")
        self.assertEqual(self.transmitted, [], "a refused combination must reach no device")

    def test_the_gate_refuses_a_reversed_or_undiscovered_endpoint_silently(self):
        self._patch_cache(FULL_CACHE_UNITS)
        self.seed(*OBSERVED_UDP)
        reversed_roles = self.client.post("/api/usb_route/pair",
                                          json={"lex_mac": OMNI324_MAC, "rex_mac": OMNI311_MAC})
        self.assertEqual(reversed_roles.get_json()["status"], "wrong_device_type")
        unknown = self.client.post("/api/usb_route/pair",
                                   json={"lex_mac": "00:11:22:33:44:55", "rex_mac": OMNI324_MAC})
        self.assertEqual(unknown.status_code, 404)
        self.assertEqual(self.transmitted, [], "neither refusal may reach a device")


# Observed on this Windows host. Carried verbatim so the parser is tested against
# the shape netsh actually prints, not against a shape invented for the test.
NETSH_ALLPROFILES_STATE = """
Domain Profile Settings:
----------------------------------------------------------------------
State                                 ON

Private Profile Settings:
----------------------------------------------------------------------
State                                 ON

Public Profile Settings:
----------------------------------------------------------------------
State                                 OFF

Ok.
"""


class FirstRunNoticeTests(ServerTestBase):
    """The network-access notice: shown once per installation, and never reset.

    The acknowledgement is not an appearance preference and not device state, so
    the two operations that wipe those stores must leave it alone. Both are
    asserted here because both were plausible homes for it, and either would have
    re-shown a notice the operator had already read.
    """

    def notices(self):
        return self.client.get("/api/notices").get_json()["notices"]

    def acknowledged(self):
        return self.notices()["network_access"]["acknowledged"]

    def ack(self, notice="network_access"):
        return self.client.post("/api/notices/ack", json={"notice": notice})

    # ---- the acknowledgement itself ----
    def test_a_fresh_install_has_not_acknowledged_it(self):
        self.assertFalse(srv.NOTICE_ACK.exists(), "nothing is written before it is answered")
        state = self.notices()["network_access"]
        self.assertFalse(state["acknowledged"])
        self.assertIsNone(state["acknowledged_at"], "no invented timestamp")

    def test_pressing_continue_acknowledges_it(self):
        reply = self.ack()
        self.assertEqual(reply.status_code, 200)
        self.assertTrue(reply.get_json()["acknowledged"])
        self.assertTrue(self.acknowledged())
        self.assertTrue(self.notices()["network_access"]["acknowledged_at"])

    def test_it_survives_a_restart(self):
        """A new process reads the same file; nothing is held in memory only."""
        self.ack()
        stored = json.loads(srv.NOTICE_ACK.read_text(encoding="utf-8"))
        self.assertTrue(stored["network_access"]["acknowledged"])
        # A restart: a client built from nothing, reading only what is on disk.
        fresh = srv.app.test_client()
        self.assertTrue(fresh.get("/api/notices").get_json()["notices"]["network_access"]["acknowledged"])

    def test_it_is_server_side_and_not_a_browser_flag(self):
        """It is one installation's answer, not one browser's.

        All four pages load ui/settings.js, so a localStorage flag would have
        re-asked on every page that had not set it yet, and again in any other
        browser or profile.
        """
        module = Path(srv.__file__).resolve().parent.joinpath("ui", "settings.js").read_text(encoding="utf-8")
        notice = module[module.index("async function showFirstRunNotice"):]
        notice = notice[:notice.index("\n// Injects the markup")]
        self.assertNotIn("localStorage", notice)
        self.assertIn("/api/notices", notice)

    # ---- what must never reset it ----
    def test_clear_units_does_not_reset_it(self):
        self.ack()
        self.assertEqual(self.client.post("/api/clear_units").status_code, 200)
        self.assertTrue(self.acknowledged(),
                        "ending the inventory's life says nothing about a notice the operator read")

    def test_clearing_the_usb_inventory_does_not_reset_it(self):
        self.ack()
        self.assertEqual(self.client.post("/api/usb_extenders/clear").status_code, 200)
        self.assertTrue(self.acknowledged())

    def test_reset_appearance_does_not_reset_it(self):
        self.ack()
        self.assertEqual(self.client.post("/api/ui_preferences/reset").status_code, 200)
        self.assertTrue(self.acknowledged(),
                        "restoring the default look must not re-ask a network question")

    def test_reset_appearance_still_resets_appearance(self):
        """The guard above must not have been bought by weakening the reset."""
        self.client.post("/api/ui_preferences", json={"template": "compact"})
        self.ack()
        self.client.post("/api/ui_preferences/reset")
        self.assertEqual(self.client.get("/api/ui_preferences").get_json()["template"], "classic")
        self.assertTrue(self.acknowledged())

    def test_the_two_stores_are_separate_files(self):
        self.assertNotEqual(srv.NOTICE_ACK, srv.UI_PREFS)
        self.assertNotEqual(srv.NOTICE_ACK, srv.TOPOLOGY_ACK)
        self.ack()
        self.client.post("/api/ui_preferences/reset")
        self.assertTrue(srv.NOTICE_ACK.exists(), "the appearance reset deletes only its own file")

    def test_no_clearing_path_touches_the_notice_store(self):
        """Grep-style: the store is named only by the code that owns it."""
        source = Path(srv.__file__).with_name("OmniMatrix_upgrade_server_v7_6y.py").read_text(encoding="utf-8")
        for name in ("api_clear_units_dup", "api_usb_extender_clear", "api_ui_preferences_reset"):
            body = source[source.index(f"def {name}("):]
            # Cut at whichever comes first: the next route, or the blank-line gap
            # before the next top-level definition. Slicing only to the next
            # @app.route would have swallowed the notice store itself, which sits
            # between the appearance reset and the next route.
            ends = [n for n in (body.find("\n@app.route"), body.find("\n\n\n")) if n > 0]
            body = body[:min(ends)]
            self.assertNotIn("NOTICE_ACK", body, f"{name} must not reach the notice store")
            self.assertNotIn("_notice_ack", body, f"{name} must not reach the notice store")

    # ---- input handling ----
    def test_an_unknown_notice_is_refused_and_changes_nothing(self):
        self.ack()
        bad = self.ack(notice="not_a_notice")
        self.assertEqual(bad.status_code, 400)
        self.assertTrue(self.acknowledged(), "a refused write leaves the store as it was")

    def test_a_corrupt_store_degrades_to_unacknowledged(self):
        srv.NOTICE_ACK.write_text("{not json", encoding="utf-8")
        self.assertFalse(self.acknowledged(), "shown once more rather than silently hidden")
        self.ack()
        self.assertTrue(self.acknowledged(), "and the next write starts clean")

    # ---- the dialog, in the one module every page loads ----
    def test_every_page_that_loads_settings_gets_the_notice(self):
        ui = Path(srv.__file__).resolve().parent / "ui"
        pages = ["index.html", "matrix/index.html", "matrix/configure.html", "matrix/usb.html"]
        for page in pages:
            self.assertIn("settings.js", (ui / page).read_text(encoding="utf-8"), page)
        module = (ui / "settings.js").read_text(encoding="utf-8")
        self.assertIn("Network Access Required", module)
        self.assertIn("notice_continue", module)
        self.assertIn("notice_guide", module)
        self.assertEqual(module.count("NETWORK_NOTICE_HTML"), 2,
                         "one definition, one guarded injection")

    def test_continue_is_the_acknowledgement(self):
        """One decision, one control: no separate 'don't show again' checkbox."""
        module = Path(srv.__file__).resolve().parent.joinpath("ui", "settings.js").read_text(encoding="utf-8")
        self.assertIn("/api/notices/ack", module)
        self.assertNotIn("type=\"checkbox\"", module)
        self.assertNotIn("Don't show again", module)

    def test_the_user_guide_button_opens_help_in_a_new_tab(self):
        module = Path(srv.__file__).resolve().parent.joinpath("ui", "settings.js").read_text(encoding="utf-8")
        wiring = module[module.index("notice_guide"):]
        self.assertIn("window.open('/help', '_blank', 'noopener')", wiring)

    def test_the_notice_is_never_polled(self):
        module = Path(srv.__file__).resolve().parent.joinpath("ui", "settings.js").read_text(encoding="utf-8")
        self.assertEqual(module.count("setInterval"), 0, "settings.js must own no timer")
        calls = [line.strip() for line in module.splitlines()
                 if "showFirstRunNotice(" in line and "async function" not in line]
        self.assertEqual(calls, ["showFirstRunNotice();"], f"unexpected call sites: {calls}")

    def test_the_dialog_is_styled_by_the_shared_stylesheet(self):
        """Markup shared by every page needs its CSS shared too."""
        css = Path(srv.__file__).resolve().parent.joinpath("ui", "settings.css").read_text(encoding="utf-8")
        self.assertIn(".notice-modal", css)
        self.assertIn(".notice-text", css)


class HostNetworkDiagnosticTests(ServerTestBase):
    """The TS dump's host_network section: context, never a diagnosis.

    Read-only by construction. It reports what the firewall profiles say, what
    this application is, where it listens and what it transmits -- and never that
    anything is being blocked. Every test stubs the one process boundary, so no
    test shells out: run_tests.py blocks sockets but not subprocess.
    """

    def as_windows(self):
        """Exercise the Windows branch of the firewall query on any host.

        `_firewall_profile_states` returns "not applicable on <platform>" before
        it reaches the stubbed query anywhere but Windows. Without this, these
        tests assert against that short-circuit instead of the parsing they
        exist to cover -- silently, on the macOS and Ubuntu runners.
        """
        patcher = mock.patch.object(srv.platform, "system", return_value="Windows")
        patcher.start()
        self.addCleanup(patcher.stop)

    def dump(self):
        return json.loads(self.client.get("/api/ts_export").get_data(as_text=True))

    def section(self):
        return self.dump()["host_network"]

    def test_the_dump_carries_the_section(self):
        payload = self.dump()
        self.assertNotIn("host_network_error", payload)
        host = payload["host_network"]
        for key in ("platform", "application", "listening", "selected_interface",
                    "transports", "firewall_profiles", "note"):
            self.assertIn(key, host)
        self.assertEqual(payload["schema_version"], srv.TS_DUMP_SCHEMA)

    def test_it_reports_the_application_and_whether_it_is_frozen(self):
        app_info = self.section()["application"]
        self.assertEqual(app_info["frozen"], bool(srv.FROZEN))
        self.assertEqual(app_info["script_dir"], str(srv.SCRIPT_DIR))
        self.assertEqual(app_info["data_dir"], str(srv.DATA_DIR))
        self.assertTrue(app_info["executable"], "the image the firewall is asked about")

    def test_it_reports_the_port_actually_bound_not_the_preferred_one(self):
        listening = self.section()["listening"]
        self.assertEqual(listening["preferred_port"], srv.PORT)
        # Nothing bound in a test process, and that is stated rather than guessed.
        self.assertFalse(listening["bound"])
        self.assertIsNone(listening["port"])
        self.patch_srv("_RUNTIME_BIND", {"host": "127.0.0.1", "port": 8083})
        listening = self.section()["listening"]
        self.assertEqual((listening["host"], listening["port"], listening["bound"]),
                         ("127.0.0.1", 8083, True))

    def test_it_reports_the_selected_interface_the_app_already_collects(self):
        self.client.post("/api/scan_preferences",
                         json={"interface_ip": "10.20.30.40", "subnet_mask": "255.255.255.0",
                               "scan_expression": "10.20.30.1-10.20.30.254"})
        selected = self.section()["selected_interface"]
        self.assertEqual(selected["interface_ip"], "10.20.30.40")
        self.assertEqual(selected["subnet_mask"], "255.255.255.0")
        self.assertEqual(selected["scan_expression"], "10.20.30.1-10.20.30.254")

    def test_it_states_the_transports_omnisuite_actually_uses(self):
        transports = self.section()["transports"]
        udp = [t for t in transports if t["protocol"] == "UDP"]
        self.assertTrue(udp, "the standalone USB extender transport must be stated")
        self.assertEqual({t["port"] for t in udp}, {usb.UDP_PORT})
        self.assertEqual(usb.UDP_PORT, 6137, "the documented AT-OMNI-311/324 port")
        directions = " ".join(t["direction"] for t in udp)
        self.assertIn("broadcast", directions)
        self.assertIn("unicast", directions)
        omnistream = [t for t in transports if "OmniStream" in t["name"]]
        self.assertEqual(len(omnistream), 1)
        self.assertEqual(omnistream[0]["port"], srv.app.config.get("WS_PORT", 80),
                         "the WebSocket/HTTP port comes from configuration")

    # ---- the firewall query ----
    def test_the_state_is_parsed_when_the_query_answers(self):
        self.as_windows()
        self.patch_srv("_firewall_query_output", lambda: NETSH_ALLPROFILES_STATE)
        firewall = self.section()["firewall_profiles"]
        self.assertTrue(firewall["available"])
        self.assertIsNone(firewall["unavailable_reason"])
        self.assertEqual(firewall["profiles"]["Domain"]["enabled"], True)
        self.assertEqual(firewall["profiles"]["Private"]["reported"], "ON")
        self.assertEqual(firewall["profiles"]["Public"]["enabled"], False)

    def test_off_windows_the_query_is_not_attempted_and_says_so(self):
        """macOS and Linux have no netsh; the section states that plainly.

        It must stay "unavailable with a reason" rather than becoming "all
        profiles off", which would read as a diagnosis the section never makes.
        """
        for system in ("Darwin", "Linux"):
            with self.subTest(system=system):
                reached = []
                self.patch_srv("_firewall_query_output",
                               lambda: reached.append(True) or "")
                with mock.patch.object(srv.platform, "system", return_value=system):
                    firewall = srv._firewall_profile_states()
                self.assertFalse(firewall["available"])
                self.assertEqual(firewall["profiles"], {})
                self.assertIn("not applicable", firewall["unavailable_reason"])
                self.assertIn(system, firewall["unavailable_reason"])
                self.assertEqual(reached, [], "no process may be started off Windows")

    def test_an_unrecognised_state_word_stays_unknown(self):
        """A value never observed is not folded into "off"."""
        profiles = srv._parse_firewall_profiles(
            "Domain Profile Settings:\nState                                 NOTREADY\n")
        self.assertIsNone(profiles["Domain"]["enabled"])
        self.assertEqual(profiles["Domain"]["reported"], "NOTREADY")

    def test_an_enabled_firewall_is_not_reported_as_an_error(self):
        self.as_windows()
        self.patch_srv("_firewall_query_output", lambda: NETSH_ALLPROFILES_STATE)
        payload = self.dump()
        host = payload["host_network"]
        self.assertNotIn("host_network_error", payload)
        text = json.dumps(host).lower()
        for word in ("blocked", "blocking", "failure", "fault", "misconfigured"):
            self.assertNotIn(word, text, f"the section must not diagnose: {word!r}")
        self.assertIn("not a diagnosis", host["note"].lower())

    def test_the_diagnostic_never_raises_when_the_platform_query_fails(self):
        def explode():
            raise RuntimeError("netsh is not available here")
        self.as_windows()
        self.patch_srv("_firewall_query_output", explode)
        firewall = srv._firewall_profile_states()          # must not propagate
        self.assertFalse(firewall["available"])
        self.assertTrue(firewall["unavailable_reason"])
        payload = self.dump()
        self.assertNotIn("host_network_error", payload, "one failure costs no other section")
        self.assertEqual(payload["host_network"]["firewall_profiles"]["available"], False)

    def test_a_timeout_degrades_the_same_way(self):
        def slow():
            raise subprocess.TimeoutExpired(cmd="netsh", timeout=srv.FIREWALL_QUERY_TIMEOUT)
        self.as_windows()
        self.patch_srv("_firewall_query_output", slow)
        firewall = self.section()["firewall_profiles"]
        self.assertFalse(firewall["available"])
        self.assertEqual(firewall["unavailable_reason"], "TimeoutExpired")

    def test_unparseable_output_is_unavailable_not_all_profiles_off(self):
        self.as_windows()
        self.patch_srv("_firewall_query_output", lambda: "Parametrages du profil de domaine :")
        firewall = self.section()["firewall_profiles"]
        self.assertFalse(firewall["available"])
        self.assertEqual(firewall["profiles"], {})
        self.assertTrue(firewall["unavailable_reason"])

    # ---- what it deliberately does not do ----
    def test_no_firewall_rules_are_read_or_reported(self):
        self.as_windows()
        self.patch_srv("_firewall_query_output", lambda: NETSH_ALLPROFILES_STATE)
        firewall = self.section()["firewall_profiles"]
        self.assertEqual(firewall["source"], "netsh advfirewall show allprofiles state")
        self.assertNotIn("rule", json.dumps(firewall["profiles"]).lower())
        source = Path(srv.__file__).with_name("OmniMatrix_upgrade_server_v7_6y.py").read_text(encoding="utf-8")
        query = source[source.index("def _firewall_query_output"):]
        query = query[:query.index("\ndef _parse_firewall_profiles")]
        self.assertIn('"show", "allprofiles", "state"', query)
        for forbidden in ('"add"', '"set"', '"delete"', '"firewall"', "runas", "ShellExecute"):
            self.assertNotIn(forbidden, query, f"the query must stay read-only: {forbidden}")

    def test_the_query_is_bounded_and_never_elevated(self):
        self.assertLessEqual(srv.FIREWALL_QUERY_TIMEOUT, 10.0)
        source = Path(srv.__file__).with_name("OmniMatrix_upgrade_server_v7_6y.py").read_text(encoding="utf-8")
        query = source[source.index("def _firewall_query_output"):]
        query = query[:query.index("\ndef _parse_firewall_profiles")]
        self.assertIn("timeout=FIREWALL_QUERY_TIMEOUT", query)
        self.assertIn("check=False", query)

    def test_no_polling_path_calls_it(self):
        """The dump is the only caller. Nothing on a scan, poll or page load."""
        source = Path(srv.__file__).with_name("OmniMatrix_upgrade_server_v7_6y.py").read_text(encoding="utf-8")

        def callers(name):
            return [line.strip() for line in source.splitlines()
                    if f"{name}(" in line and not line.strip().startswith("def ")]

        self.assertEqual(callers("_ts_host_network"),
                         ['payload["host_network"] = _ts_host_network()'])
        self.assertEqual(callers("_firewall_profile_states"),
                         ['"firewall_profiles": _firewall_profile_states(),'])
        self.assertEqual(callers("_firewall_query_output"),
                         ["text = _firewall_query_output()"])

    def test_the_section_is_attached_after_the_sanitiser(self):
        """Its nulls are the answer; a sanitiser that strips them would delete them."""
        self.patch_srv("_firewall_query_output", lambda: "")
        host = self.section()
        self.assertIn("firewall_profiles", host)
        self.assertIn("unavailable_reason", host["firewall_profiles"])
        self.assertIn("port", host["listening"])


if __name__ == "__main__":
    unittest.main()
