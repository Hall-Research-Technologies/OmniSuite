#!/usr/bin/env python3
"""Run every OmniSuite test suite, and prove none of them touched the bench.

    python run_tests.py

Exits non-zero if any suite fails OR if any test opened a real socket.

The isolation check is part of the run rather than a separate habit because the
suite once reached live hardware for a long time without anyone noticing: the
UDP transport was stubbed, the WebSocket transport was not, and 163 real
connections a run went to the devices on the bench. Three tests passed only
because that hardware answered, so the suite was green on one workstation and
broken everywhere else.

Both transports are replaced here, before the tests are imported, with spies
that record the attempt and raise. A test that needs a device to answer has to
say so through its own fixture; it can no longer borrow the real one.
"""
from __future__ import annotations

import argparse
import collections
import importlib.util
import os
import re
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TESTS = ROOT / "tests"
JS_SUITES = ("usb_render_smoke.js", "usb_filter_test.js", "lldp_topology_test.js",
             "sticky_observers_test.js", "mirror_scroll_test.js", "device_log_test.js",
             "appearance_theme_test.js", "multiview_ui_test.js",
             "matrix_multiview_test.js")

attempts: collections.Counter = collections.Counter()


def install_network_spies() -> None:
    """Replace both hardware transports with recording spies that refuse."""
    import websocket

    def ws_spy(url, *args, **kwargs):
        attempts[("websocket", str(url))] += 1
        raise OSError(f"run_tests.py blocked a WebSocket connection to {url}")

    websocket.create_connection = ws_spy

    real_socket = socket.socket

    class SpySocket(real_socket):
        def sendto(self, data, address, *args, **kwargs):  # type: ignore[override]
            command = int.from_bytes(data[8:10], "big") if len(data) >= 10 else -1
            attempts[("udp", f"{address[0]} cmd={hex(command)}")] += 1
            raise OSError("run_tests.py blocked a UDP datagram")

        def connect(self, address, *args, **kwargs):  # type: ignore[override]
            # Loopback is the test client talking to itself, not a device.
            host = address[0] if isinstance(address, tuple) else ""
            if isinstance(host, str) and not host.startswith("127.") and host != "::1":
                attempts[("tcp", str(host))] += 1
                raise OSError("run_tests.py blocked a TCP connection")
            return super().connect(address, *args, **kwargs)

    socket.socket = SpySocket


def run_python_suite() -> bool:
    # A scratch data directory, so a test can never read or write the operator's
    # real device cache.
    os.environ.setdefault("OMNI_DATA_DIR", tempfile.mkdtemp(prefix="omnisuite-tests-"))
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(TESTS))
    install_network_spies()

    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    for path in sorted(TESTS.glob("test_*.py")):
        spec = importlib.util.spec_from_file_location(path.stem, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        suite.addTests(loader.loadTestsFromModule(module))

    result = unittest.TextTestRunner(verbosity=1).run(suite)
    print(f"\nPython: ran {result.testsRun} tests, "
          f"{len(result.failures)} failures, {len(result.errors)} errors")
    return result.wasSuccessful()


def run_js_suites() -> bool:
    ok = True
    for name in JS_SUITES:
        path = TESTS / name
        if not path.exists():
            print(f"JS: {name} is missing")
            ok = False
            continue
        proc = subprocess.run([node_binary(), str(path)], cwd=str(ROOT),
                              capture_output=True, text=True)
        tail = (proc.stdout or proc.stderr).strip().splitlines()
        print(f"JS: {name} -> exit {proc.returncode}: {tail[-1] if tail else '(no output)'}")
        ok = ok and proc.returncode == 0
    return ok


def node_binary() -> str:
    return os.environ.get("NODE", "node")


# RFC 5737 reserves these for documentation and examples. They are not routed,
# so a datagram addressed to one cannot reach a device. A test that deliberately
# probes an unreachable address is legitimate, and every attempt is still
# blocked at the socket -- it is reported, but it does not fail the gate.
UNROUTABLE_PREFIXES = ("192.0.2.", "198.51.100.", "203.0.113.")

# An attempt is recorded in whatever shape the caller used: a bare host, a URL,
# or a host with the command appended. Classifying the raw string would read
# "ws://192.0.2.10/wsapp/" as a routable host, so pull the address out first.
_ADDRESS = re.compile(r"(?<![0-9.])[0-9]{1,3}(?:[.][0-9]{1,3}){3}(?![0-9.])")


def report_isolation() -> bool:
    def reachable(target: str) -> bool:
        found = _ADDRESS.search(target)
        if found is None:
            return True              # not an address we recognise: assume the worst
        return not any(found.group(0).startswith(prefix)
                       for prefix in UNROUTABLE_PREFIXES)

    blocked = sum(attempts.values())
    live = sum(count for (_kind, target), count in attempts.items() if reachable(target))
    print(f"\nHardware isolation: {blocked} connection attempt(s), all blocked at the socket")
    for (kind, target), count in attempts.most_common(20):
        note = "" if reachable(target) else "   (RFC 5737 documentation range, unroutable)"
        print(f"  {count:5d}  {kind}  {target}{note}")
    if live:
        print(f"  -> {live} of these addressed a routable host, which is a failure")
    return live == 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python-only", action="store_true")
    parser.add_argument("--js-only", action="store_true")
    args = parser.parse_args()

    python_ok = js_ok = True
    isolated = True
    if not args.js_only:
        python_ok = run_python_suite()
        isolated = report_isolation()
    if not args.python_only:
        js_ok = run_js_suites()

    print("\n" + "=" * 60)
    print(f"  Python suite     : {'PASS' if python_ok else 'FAIL'}")
    print(f"  JS suites        : {'PASS' if js_ok else 'FAIL'}")
    print(f"  Hardware isolated: {'PASS' if isolated else 'FAIL'}")
    print("=" * 60)
    return 0 if (python_ok and js_ok and isolated) else 1


if __name__ == "__main__":
    raise SystemExit(main())
