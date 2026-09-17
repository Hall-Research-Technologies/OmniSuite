"""The hardware fence protects the suite however the suite is started.

Phase 8 found the fence living only in `run_tests.py`, so a plain
`python -m unittest`, an IDE runner or a mutation harness ran the same tests
with nothing between them and the bench. Nothing failed as a result -- measured
at the time, the suite only ever addressed documentation-range addresses -- but
the protection existed by convention, and a convention is not a fence.

These tests are the fence's own regression. The last one is the important one:
it starts a fresh interpreter, runs the others through `python -m unittest`
with no help from `run_tests.py`, and requires them to pass there.
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

import os
import pathlib
import socket
import subprocess
import sys
import unittest

CHILD = "OMNI_FENCE_SELFTEST_CHILD"
ROOT = pathlib.Path(__file__).resolve().parent.parent

# A routable address that is not the bench: RFC 2544 reserves 198.18.0.0/15 for
# benchmarking. The fence refuses it before the socket is used, which is what
# these tests assert -- and no bench address is written into a test file, as a
# target or as anything else.
ROUTABLE = "198.18.0.7"


class FenceTests(unittest.TestCase):
    """What must be true in any process running these tests."""

    def setUp(self):
        """Record this test's deliberate attempts somewhere private.

        These tests provoke refusals on purpose. Counting them in the run's
        tally would make every clean run look as though the application had
        reached for a routable host.
        """
        import collections
        self._run_tally = _fence.attempts
        _fence.attempts = collections.Counter()
        self.addCleanup(self._restore_tally)

    def _restore_tally(self):
        _fence.attempts = self._run_tally

    def test_the_fence_is_installed(self):
        self.assertTrue(_fence.installed(),
                        "the test package did not install the hardware fence")

    def test_a_tcp_connection_to_a_device_is_refused(self):
        # FenceError, not OSError. A socket that really tried and timed out
        # raises OSError too, so the looser assertion passes just as happily
        # with the fence removed -- which is exactly what it must not do.
        sock = socket.socket()
        sock.settimeout(0.5)
        try:
            with self.assertRaises(_fence.FenceError):
                sock.connect((ROUTABLE, 80))
        finally:
            sock.close()
        self.assertEqual(list(_fence.attempts), [("tcp", ROUTABLE)],
                         "the attempt was not recorded as a TCP connection")

    def test_connect_ex_reports_a_refusal_rather_than_succeeding(self):
        """A probe that uses connect_ex must not read a device as present."""
        sock = socket.socket()
        sock.settimeout(0.5)
        try:
            self.assertNotEqual(sock.connect_ex((ROUTABLE, 80)), 0)
        finally:
            sock.close()

    def test_a_udp_datagram_to_a_device_is_refused(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            with self.assertRaises(_fence.FenceError):
                sock.sendto(b"\x00" * 16, (ROUTABLE, 6970))
        finally:
            sock.close()

    def test_a_websocket_to_a_device_is_refused(self):
        import websocket
        with self.assertRaises(_fence.FenceError):
            websocket.create_connection("ws://%s:80/wsapp/" % ROUTABLE)

    def test_an_http_request_to_a_device_is_refused(self):
        """Thumbnails and any other HTTP reach the network through a socket."""
        import urllib.error
        import urllib.request
        with self.assertRaises((OSError, urllib.error.URLError)):
            urllib.request.urlopen(
                "http://%s/thumbnail/thumbnail1.jpg" % ROUTABLE, timeout=1)

    def test_a_documentation_address_is_refused_too(self):
        """Fenced means fenced. The documentation range is merely reportable."""
        sock = socket.socket()
        try:
            with self.assertRaises(_fence.FenceError):
                sock.connect(("192.0.2.10", 80))
        finally:
            sock.close()

    def test_loopback_is_still_allowed(self):
        """A test that talks to a local server is talking to itself."""
        server = socket.socket()
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        try:
            client = socket.socket()
            client.settimeout(2)
            try:
                client.connect(server.getsockname())   # must not raise
            finally:
                client.close()
        finally:
            server.close()

    def test_the_data_directory_is_not_the_operators(self):
        directory = os.environ.get("OMNI_DATA_DIR") or ""
        self.assertTrue(directory, "OMNI_DATA_DIR was not set for the tests")
        self.assertNotIn("OmniSuite", directory,
                         "the tests are pointed at the real data directory")

    # ---- the classifier the isolation report depends on --------------------
    def test_an_address_is_read_out_of_whatever_shape_it_was_recorded_in(self):
        for target in ("192.0.2.10", "ws://192.0.2.10/wsapp/",
                       "192.0.2.1 cmd=0x08"):
            self.assertFalse(_fence.reachable(target), target)

    def test_an_ordinary_address_is_reported_as_routable(self):
        for target in ("198.18.0.7", "ws://198.18.0.7/wsapp/"):
            self.assertTrue(_fence.reachable(target), target)

    def test_an_unrecognisable_target_is_assumed_routable(self):
        self.assertTrue(_fence.reachable("some-device.local"))

    def test_no_private_range_is_ever_excused(self):
        """This is what keeps the bench behind the fence."""
        for prefix in _fence.UNROUTABLE_PREFIXES:
            self.assertFalse(
                prefix.startswith(("10.", "172.16.", "172.17.", "192.168.")),
                "%s excuses a private address" % prefix)


class IsolationReportTests(unittest.TestCase):
    """The report that decides whether a run touched real hardware.

    Attempts are recorded in whatever shape the caller used -- a bare host, a
    URL, a host with the command appended -- so the report has to find the
    address inside the string. Reading the raw string instead once made every
    documentation-range WebSocket look like a routable host and failed a clean
    run; the mistake in the other direction would be far worse, so an
    unrecognisable target counts as routable.
    """

    def _passes(self, target):
        """Does a run that attempted `target` pass the isolation gate?"""
        import collections
        import io
        original = _fence.attempts
        _fence.attempts = collections.Counter({("tcp", target): 1})
        try:
            return _fence.report(stream=io.StringIO())
        finally:
            _fence.attempts = original

    def test_documentation_ranges_pass_the_gate(self):
        for target in ("192.0.2.10", "198.51.100.7", "203.0.113.4",
                       "ws://192.0.2.10/wsapp/", "192.0.2.1 cmd=0x08"):
            self.assertTrue(self._passes(target), target)

    def test_an_ordinary_address_fails_the_gate(self):
        """The bench prefix is deliberately not written here.

        A separate guard forbids any bench address appearing in a test file at
        all, even as something being rejected, so these stand in for one.
        """
        for target in ("198.18.0.7", "ws://198.18.0.7/wsapp/",
                       "198.18.0.7 cmd=0x08"):
            self.assertFalse(self._passes(target),
                             "%s did not fail the isolation gate" % target)

    def test_an_address_that_merely_looks_like_one_is_not_excused(self):
        """1192.0.2.5 is not in the documentation range."""
        self.assertFalse(self._passes("1192.0.2.5"))

    def test_an_unrecognisable_target_fails_the_gate(self):
        self.assertFalse(self._passes("some-device.local"))

    def test_a_clean_run_passes(self):
        import collections
        import io
        original = _fence.attempts
        _fence.attempts = collections.Counter()
        try:
            self.assertTrue(_fence.report(stream=io.StringIO()))
        finally:
            _fence.attempts = original

    def test_the_counter_is_one_counter(self):
        """Loaded as a package module and as a top-level one, it is the same.

        Two module objects meant two counters, and a run that recorded eighty
        attempts reported zero.
        """
        import sys
        self.assertIs(sys.modules.get("tests._fence"), sys.modules.get("_fence"))


class FenceWithoutRunTestsTests(unittest.TestCase):
    """The fence must not depend on being started by `run_tests.py`."""

    @unittest.skipIf(os.environ.get(CHILD), "this process IS the child run")
    def test_a_direct_unittest_run_is_fenced(self):
        environment = dict(os.environ)
        environment[CHILD] = "1"
        environment.pop("OMNI_DATA_DIR", None)   # the package must set its own
        proc = subprocess.run(
            [sys.executable, "-m", "unittest", "-v",
             "tests.test_fence.FenceTests"],
            cwd=str(ROOT), capture_output=True, text=True, timeout=300,
            env=environment)
        self.assertEqual(
            proc.returncode, 0,
            "python -m unittest ran without the fence:\n%s" % proc.stderr[-2000:])
        self.assertIn("OK", proc.stderr,
                      "the child run did not report success:\n%s"
                      % proc.stderr[-2000:])

    @unittest.skipIf(os.environ.get(CHILD), "this process IS the child run")
    def test_a_direct_unittest_run_of_the_application_suite_is_fenced(self):
        """The real suite, started the way a mutation harness starts it."""
        environment = dict(os.environ)
        environment[CHILD] = "1"
        environment.pop("OMNI_DATA_DIR", None)
        proc = subprocess.run(
            [sys.executable, "-c",
             "import tests.test_multiview as m; from tests import _fence;"
             " print('FENCED' if _fence.installed() else 'UNFENCED');"
             " import os; print(os.environ['OMNI_DATA_DIR'])"],
            cwd=str(ROOT), capture_output=True, text=True, timeout=300,
            env=environment)
        self.assertEqual(proc.returncode, 0, proc.stderr[-2000:])
        self.assertIn("FENCED", proc.stdout,
                      "importing the suite directly left it unfenced:\n%s"
                      % proc.stdout)
        self.assertNotIn("OmniSuite", proc.stdout.splitlines()[-1],
                         "the suite was pointed at the real data directory")


if __name__ == "__main__":
    unittest.main()
