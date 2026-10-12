"""Scan transport diagnostics: a scan the operating system refused is not an empty scan.

On macOS a scan of the adapter's own subnet was reported as "0 found" within a
moment. Every connection error was swallowed, so a machine that refused every
connection looked exactly like a network with nothing on it.

These tests drive the real /api/scan with the connection primitive replaced, so
nothing here reaches a network. Addresses are from RFC 5737 documentation space.
Every test that replaces the primitive does so through mock.patch and restores it.
"""
import errno
import os
import socket
import sys
import unittest
from unittest import mock

try:
    from . import test_usb_integration as base
except ImportError:                                   # loaded by path, no package
    import test_usb_integration as base

srv = base.srv

RANGE = "192.0.2.1-20"          # 20 addresses: above the verdict minimum
LOCAL_BLOCKS = ("EPERM", "EACCES", "EHOSTUNREACH", "ENETUNREACH", "EMFILE")


def _raising(code):
    def connect(address, timeout=None, *args, **kwargs):
        raise OSError(code, os.strerror(code) if code < 10000 else "socket error")
    return connect


class ScanBlockedLocallyTests(base.ServerTestBase):
    """Immediate connection failures are reported as a failed scan, with a reason."""

    def _scan(self, targets, connector):
        with mock.patch.object(srv.socket, "create_connection", side_effect=connector):
            return self.client.post("/api/scan", json={"targets": targets})

    def test_each_immediate_local_failure_is_an_explicit_failure(self):
        for name in LOCAL_BLOCKS:
            with self.subTest(errno=name):
                response = self._scan(RANGE, _raising(getattr(errno, name)))
                body = response.get_json()
                # 503: an upstream condition, not a fault in this application.
                self.assertEqual(response.status_code, 503, body)
                self.assertIs(body["ok"], False)
                self.assertEqual(body["reason"], "scan_blocked")
                self.assertEqual(body["tested"], 20)
                self.assertEqual(body["reachable"], 0)
                self.assertEqual(body["diagnostics"]["probe_outcomes"], {name: 20},
                                 "one count per address, not per port")
                self.assertIn(name, body["error"])
                self.assertIn("not the same as finding no devices", body["error"])

    def test_a_refused_scan_leaves_the_cache_and_results_untouched(self):
        self._scan(RANGE, _raising(errno.EPERM))
        self.assertFalse(srv.CACHE.exists(), "a refused scan must not persist anything")
        self.assertFalse(srv.SCAN_RESULTS.exists())

    def test_permission_failures_name_the_macos_privacy_setting(self):
        body = self._scan(RANGE, _raising(errno.EPERM)).get_json()
        self.assertIn("Local Network", body["error"])

    def test_descriptor_exhaustion_names_the_limit_and_is_not_raised(self):
        class Resource:
            @staticmethod
            def getrlimit(which):
                return (256, 10240)

            @staticmethod
            def setrlimit(*args):
                raise AssertionError("the limit must only be inspected, never raised")

            RLIMIT_NOFILE = 8

        with mock.patch.dict(sys.modules, {"resource": Resource}):
            body = self._scan(RANGE, _raising(errno.EMFILE)).get_json()
        self.assertEqual(body["diagnostics"]["fd_soft_limit"], 256)
        self.assertEqual(body["diagnostics"]["fd_hard_limit"], 10240)
        self.assertIn("file descriptors", body["error"])
        self.assertIn("limit 256", body["error"])


class ScanOrdinaryResultsAreNotFailuresTests(base.ServerTestBase):
    """The verdict must not turn a quiet or small network into an error."""

    def _scan(self, targets, connector):
        with mock.patch.object(srv.socket, "create_connection", side_effect=connector):
            return self.client.post("/api/scan", json={"targets": targets})

    def test_timeouts_are_an_ordinary_empty_scan(self):
        def timeout(address, timeout=None, *a, **k):
            raise socket.timeout("timed out")
        response = self._scan(RANGE, timeout)
        body = response.get_json()
        self.assertEqual(response.status_code, 200, body)
        self.assertTrue(body["ok"])
        self.assertEqual(body["reachable"], 0)
        self.assertEqual(body["diagnostics"]["probe_outcomes"], {"timeout": 20})

    def test_refusals_mean_the_network_answered(self):
        response = self._scan(RANGE, _raising(errno.ECONNREFUSED))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["diagnostics"]["probe_outcomes"],
                         {"refused": 20})

    def test_a_single_unreachable_address_is_not_a_verdict(self):
        # One typed address that does not answer says nothing about this machine.
        response = self._scan("192.0.2.1", _raising(errno.EHOSTUNREACH))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["ok"])

    def test_a_delayed_no_route_is_a_quiet_host_not_a_local_refusal(self):
        # A real unanswered ARP takes time. Only an instant "no route" is local.
        self.assertEqual(srv._classify_tcp_error(OSError(errno.EHOSTUNREACH, "x"), 0.01),
                         "EHOSTUNREACH")
        self.assertEqual(srv._classify_tcp_error(OSError(errno.EHOSTUNREACH, "x"), 3.0),
                         "unreachable_delayed")
        stats = srv._ScanProbeStats()
        for _ in range(20):
            stats.record("unreachable_delayed")
        self.assertIsNone(stats.verdict())

    def test_one_device_answering_cancels_the_verdict(self):
        answering = "192.0.2.7"

        def connector(address, timeout=None, *a, **k):
            if address[0] == answering:
                return mock.MagicMock()           # usable as a context manager
            raise OSError(errno.EPERM, "Operation not permitted")

        unit = {"ip": answering, "mac": "02:00:00:00:00:07", "role": "encoder",
                "model": "synthetic", "version": "0", "hostname": "synthetic-1"}
        with mock.patch.object(srv, "_probe_one", return_value=dict(unit)):
            response = self._scan(RANGE, connector)
        body = response.get_json()
        self.assertEqual(response.status_code, 200, body)
        self.assertEqual(body["reachable"], 1)
        self.assertEqual(body["diagnostics"]["probe_outcomes"]["open"], 1)
        self.assertEqual(body["diagnostics"]["probe_outcomes"]["EPERM"], 19)


class ScanProbeBehaviourIsPreservedTests(base.ServerTestBase):
    """The diagnostics observe; they must not change what is attempted."""

    def test_ports_order_and_timeout_are_unchanged(self):
        calls = []

        def connector(address, timeout=None, *a, **k):
            calls.append((address, timeout))
            raise socket.timeout("timed out")

        with mock.patch.object(srv.socket, "create_connection", side_effect=connector):
            self.client.post("/api/scan", json={"targets": "192.0.2.1"})
        ws_port = srv.app.config["WS_PORT"]
        self.assertEqual(calls, [(("192.0.2.1", ws_port), 0.4),
                                 (("192.0.2.1", 80), 0.4),
                                 (("192.0.2.1", 443), 0.4)])

    def test_the_original_probe_is_untouched_for_every_other_caller(self):
        with mock.patch.object(srv.socket, "create_connection",
                               side_effect=_raising(errno.EPERM)):
            self.assertIs(srv._tcp_probe("192.0.2.1", [80], timeout=0.4), False)

    def test_windows_spellings_are_normalised(self):
        with mock.patch.dict(errno.errorcode, {10013: "WSAEACCES"}):
            self.assertEqual(srv._tcp_errno_label(OSError(10013, "denied")), "EACCES")

    def test_an_exception_without_an_errno_is_named_not_dropped(self):
        self.assertEqual(srv._classify_tcp_error(RuntimeError("x"), 0.0),
                         "other:RuntimeError")

    def test_descriptor_diagnostics_never_raise_without_the_resource_module(self):
        with mock.patch.dict(sys.modules, {"resource": None}):      # as on Windows
            self.assertIsInstance(srv._fd_diagnostics(), dict)


class ScanContextTests(base.ServerTestBase):
    """Enough context in one response to say why a scan found nothing."""

    ADAPTER = {"name": "en0", "ip": "192.0.2.5", "netmask": "255.255.255.0",
               "cidr": "192.0.2.0/24"}

    def test_diagnostics_carry_counts_timing_duration_and_adapters(self):
        with mock.patch.object(srv, "_adapters_psutil", return_value=[dict(self.ADAPTER)]):
            with mock.patch.object(srv.socket, "create_connection",
                                   side_effect=_raising(errno.EHOSTUNREACH)):
                body = self.client.post("/api/scan",
                                        json={"targets": "192.0.2.1-20"}).get_json()
        d = body["diagnostics"]
        self.assertEqual(d["target_count"], 20)
        self.assertEqual((d["first_target"], d["last_target"]), ("192.0.2.1", "192.0.2.20"))
        self.assertTrue(d["first_target_on_attached_subnet"])
        self.assertEqual(d["adapters"][0]["name"], "en0")
        self.assertIn("duration_s", d)
        self.assertEqual(set(d["probe_timing_ms"]), {"EHOSTUNREACH"})
        timing = d["probe_timing_ms"]["EHOSTUNREACH"]
        self.assertLessEqual(timing["min"], timing["max"])

    def test_an_off_subnet_target_is_marked_as_routed(self):
        with mock.patch.object(srv, "_adapters_psutil", return_value=[dict(self.ADAPTER)]):
            ctx = srv._scan_context(["198.51.100.1", "198.51.100.2"])
        self.assertIs(ctx["first_target_on_attached_subnet"], False)

    def test_context_never_raises_when_the_platform_cannot_answer(self):
        with mock.patch.object(srv, "_adapters_psutil", side_effect=RuntimeError("no")):
            ctx = srv._scan_context(["192.0.2.1"])
        self.assertEqual(ctx["adapters_error"], "RuntimeError")
        self.assertEqual(ctx["target_count"], 1)

    def test_the_route_lookup_sends_nothing(self):
        # A UDP connect() consults the routing table; it must never transmit.
        sent = []

        class Sock:
            def __init__(self, *a, **k):
                pass

            def connect(self, addr):
                sent.append(("connect", addr))

            def getsockname(self):
                return ("192.0.2.5", 40000)

            def send(self, *a):
                sent.append("send")

            def sendto(self, *a):
                sent.append("sendto")

            def close(self):
                pass

        with mock.patch.object(srv.socket, "socket", Sock), \
                mock.patch.object(srv, "_adapters_psutil", return_value=[]):
            ctx = srv._scan_context(["192.0.2.1"])
        self.assertEqual(ctx["route_source_address"], "192.0.2.5")
        self.assertEqual(sent, [("connect", ("192.0.2.1", 9))])


class EmptyTargetListTests(base.ServerTestBase):
    """Text that expands to no addresses is an error, not a scan of nothing."""

    def test_an_unrecognised_range_is_refused_with_a_reason(self):
        for text in ("192.0.2.1-192.0.2.20", "not an address", "192.0.2.999"):
            with self.subTest(targets=text):
                with mock.patch.object(srv.socket, "create_connection",
                                       side_effect=AssertionError("nothing to probe")):
                    response = self.client.post("/api/scan", json={"targets": text})
                body = response.get_json()
                self.assertEqual(response.status_code, 400, body)
                self.assertIs(body["ok"], False)
                self.assertEqual(body["reason"], "no_valid_targets")
                self.assertEqual(body["tested"], 0)

    def test_a_valid_target_is_unaffected(self):
        with mock.patch.object(srv.socket, "create_connection",
                               side_effect=socket.timeout("timed out")):
            response = self.client.post("/api/scan", json={"targets": "192.0.2.1"})
        self.assertEqual(response.status_code, 200)


class ScanPageReportsRefusalTests(unittest.TestCase):
    """The page must show the server's sentence rather than 'undefined/undefined'.

    This is a source-level check of the handler, bounded at the end of that
    handler; the behaviour itself is exercised on a real Mac by the diagnostic
    build, which is the validation this change is waiting for.
    """

    def test_scan_handler_branches_on_a_failed_response(self):
        text = (base.Path(__file__).resolve().parent.parent / "ui" / "index.html"
                ).read_text(encoding="utf-8")
        start = text.index("$('#scan_btn').addEventListener('click'")
        handler = text[start:text.index("$('#targets_clear')", start)]
        self.assertIn("!res.ok || data.ok === false", handler)
        self.assertLess(handler.index("data.ok === false"),
                        handler.index("found`"),
                        "the failure must be handled before the success line renders")


if __name__ == "__main__":
    unittest.main()
