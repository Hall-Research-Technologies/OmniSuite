"""Request-handling hardening found by live qualification of the release candidate.

  * Remote Access's port is an integer from 1024 to 65535, refused with a 400
    BEFORE anything is stopped, started or saved.
  * A body that is not what the endpoint needs never acts as a command: `[]` used
    to mean "turn Remote Access off" and ended every session.
  * Identity spellings: dash- or colon-separated decimals are not MACs either.
  * Both listeners send the same security headers and name no library or version.
  * No log line records a credential or a request body.

Nothing here contacts hardware; the listener tests bind loopback only.
"""
from __future__ import annotations

import http.client
import json
import logging
import os
import re
import socket
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("OMNI_SMOKE", "1")

import omni_groups                                           # noqa: E402
import omni_remote                                           # noqa: E402
import omni_remote_api                                       # noqa: E402
import omni_users                                            # noqa: E402
from tests.test_remote_http_default import HttpDefaultTests  # noqa: E402

BAD_PORTS = (70000, -1, 65536, 99999999999, 0, 1, 80, 1023, "80", "70000", "-1", "+8443", " 8443",
             "8443 ", "8443.0", "0x2000", "８４４３", True, False, 8443.5, [8443], {"a": 1}, "", "abc")


class RemoteAccessCommandTests(unittest.TestCase):
    """Reuses the first-time-setup harness; the listener never really binds."""

    def setUp(self):
        HttpDefaultTests.setUp(self)
        self.stops = mock.Mock()
        self.revokes = mock.Mock(return_value=0)
        for target, replacement in ((self.srv._remote_listener, "stop"),
                                    (self.store, "revoke_every_session")):
            patcher = mock.patch.object(target, replacement,
                                        self.stops if replacement == "stop" else self.revokes)
            patcher.start()
            self.addCleanup(patcher.stop)

    def enable(self, **body):
        return HttpDefaultTests.enable(self, **body)

    def saved(self):
        return HttpDefaultTests.saved(self)

    def untouched(self):
        self.assertEqual(self.started, [], "a listener was started")
        self.stops.assert_not_called()
        self.revokes.assert_not_called()

    def test_an_unusable_port_is_a_400_before_any_change(self):
        before = self.saved()
        for port in BAD_PORTS:
            with self.subTest(port=port):
                reply = self.enable(port=port)
                self.assertEqual(reply.status_code, 400, (port, reply.get_data(as_text=True)[:100]))
                self.assertEqual(reply.get_json().get("reason"), "invalid_port")
                self.assertIn("1024", reply.get_json()["error"])
                self.untouched()
                self.assertEqual(self.saved(), before)
        self.assertFalse(self.srv.REMOTE_ACCESS_FILE.exists(), "nothing was persisted")

    def test_a_bad_port_is_refused_when_turning_it_off_too(self):
        reply = self.client.post("/api/remote_access", json={"enabled": False, "port": 70000})
        self.assertEqual(reply.status_code, 400)
        self.untouched()

    def test_ports_inside_the_range_are_accepted(self):
        for port in (1024, 8443, 65535, "8443", "1024"):
            with self.subTest(port=port):
                self.started.clear()
                reply = self.enable(port=port, accept_insecure=True)
                self.assertEqual(reply.status_code, 200, (port, reply.get_data(as_text=True)[:100]))
                self.assertEqual(self.started[-1]["address"], "198.51.100.85")

    def test_the_port_rule_is_one_function(self):
        problem = omni_remote_api._remote_port_problem
        for port in BAD_PORTS:
            self.assertTrue(problem(port), port)
        for port in (1024, 1025, 8080, 49152, 65535, "1024", "65535"):
            self.assertEqual(problem(port), "", port)

    def test_a_body_that_does_not_say_on_or_off_never_turns_it_off(self):
        for raw in ("[]", "null", "5", '"x"', "{}", '{"a":[]}', '{"enabled":"false"}',
                    '{"enabled":0}', '{"enabled":null}', '{"enabled":[]}', '{"enabled":"yes"}',
                    "{oops", ""):
            with self.subTest(body=raw):
                reply = self.client.post("/api/remote_access", data=raw,
                                         content_type="application/json")
                self.assertEqual(reply.status_code, 400, (raw, reply.get_data(as_text=True)[:100]))
                self.untouched()

    def test_an_explicit_false_still_turns_it_off(self):
        reply = self.client.post("/api/remote_access", json={"enabled": False})
        self.assertEqual(reply.status_code, 200)
        self.stops.assert_called_once()
        self.revokes.assert_called_once()


class IdentitySpellingTests(unittest.TestCase):

    def test_decimal_spellings_with_any_separator_are_not_macs(self):
        for text in ("192-168-100-100", "192:168:100:100", "198.18.100.100", "100.100.100.100",
                     "100-100-100-100", "100:100:100:100", "192_168_100_100", "192 168 100 100",
                     "19.21.68.10.01.52", "255.255.255.255"):
            with self.subTest(text=text):
                self.assertFalse(omni_users.is_mac_identity(text))
                self.assertEqual(omni_users._normalise_identity(text), text)

    def test_a_group_refuses_each_of_them(self):
        with tempfile.TemporaryDirectory() as folder:
            store = omni_groups.GroupStore(Path(folder) / "g.json", omni_users._normalise_identity)
            for text in ("192-168-100-100", "192:168:100:100", "100.100.100.100"):
                with self.subTest(text=text):
                    self.assertFalse(store.create("G", [text])["ok"])
            self.assertEqual(store.all(), [])

    def test_the_accepted_spellings_are_exactly_these(self):
        accepted = ("b8:98:b0:00:00:01", "B8-98-B0-00-00-01", "b898b0000001", "b898.b000.0001")
        for text in accepted:
            self.assertTrue(omni_users.is_mac_identity(text), text)
            self.assertEqual(omni_users._normalise_identity(text), "b8:98:b0:00:00:01")
        for text in ("b8:98-b0:00:00:01", "b8:98:b0:00:00", "b8 98 b0 00 00 01", "b8:98:b0:00:00:0g"):
            self.assertFalse(omni_users.is_mac_identity(text), text)


def _free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class ListenerHeaderTests(unittest.TestCase):
    """Both listeners, real sockets on loopback, the real application behind them."""

    @classmethod
    def setUpClass(cls):
        import OmniMatrix_upgrade_server_v7_6y as srv
        cls.srv = srv
        cls.local_port = _free_port()
        cls.local = omni_remote.serve_tagged("127.0.0.1", cls.local_port, srv.app, threaded=True)
        threading.Thread(target=cls.local.serve_forever, daemon=True).start()
        cls.remote_port = _free_port()
        cls.listener = omni_remote.RemoteListener(srv.app)
        with mock.patch.object(omni_remote, "is_usable_remote_address", return_value=True):
            started = cls.listener.start("127.0.0.1", cls.remote_port)
        assert started.get("ok"), started

    @classmethod
    def tearDownClass(cls):
        cls.local.shutdown()
        cls.local.server_close()
        cls.listener.stop()

    def fetch(self, port, path, host=None):
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        connection.request("GET", path, headers={"Host": host or ("127.0.0.1:%d" % port)})
        reply = connection.getresponse()
        headers = {k.lower(): v for k, v in reply.getheaders()}
        reply.read()
        connection.close()
        return reply.status, headers

    def test_the_security_headers_are_on_both_listeners(self):
        for label, port, path in (("local", self.local_port, "/api/config"),
                                  ("remote", self.remote_port, "/login")):
            with self.subTest(listener=label):
                status, headers = self.fetch(port, path)
                self.assertLess(status, 500)
                self.assertEqual(headers.get("x-content-type-options"), "nosniff")
                self.assertEqual(headers.get("x-frame-options"), "DENY")
                self.assertEqual(headers.get("referrer-policy"), "no-referrer")

    def test_a_refusal_has_them_too(self):
        status, headers = self.fetch(self.local_port, "/api/config", host="attacker.invalid")
        self.assertEqual(status, 421)
        self.assertEqual(headers.get("x-content-type-options"), "nosniff")
        self.assertEqual(headers.get("x-frame-options"), "DENY")
        self.assertEqual(headers.get("referrer-policy"), "no-referrer")

    def test_neither_listener_names_its_software_or_versions(self):
        for label, port, path in (("local", self.local_port, "/api/config"),
                                  ("remote", self.remote_port, "/login"),
                                  ("local-421", self.local_port, "/x")):
            with self.subTest(listener=label):
                host = "attacker.invalid" if label == "local-421" else None
                _, headers = self.fetch(port, path, host=host)
                server = headers.get("server", "")
                self.assertEqual(server, "OmniSuite")
                self.assertIsNone(re.search(r"(?i)werkzeug|python|\d+\.\d+", server))
                for value in headers.values():
                    self.assertIsNone(re.search(r"(?i)werkzeug/|python/", value))

    def test_the_policy_stays_conservative(self):
        _, headers = self.fetch(self.remote_port, "/login")
        self.assertNotIn("content-security-policy", headers)


class LoggingTests(unittest.TestCase):
    """A log line names the fields it records; it never dumps a body or a credential."""

    def source(self):
        return (ROOT / "OmniMatrix_upgrade_server_v7_6y.py").read_text(encoding="utf-8")

    def test_no_log_call_dumps_a_device_command_unredacted(self):
        offenders = []
        for number, line in enumerate(self.source().splitlines(), 1):
            if re.search(r"\blog\.\w+\(", line) and re.search(r"json\.dumps\(\s*\w*payload\w*\s*[,)]", line):
                offenders.append(number)
        self.assertEqual(offenders, [], "unredacted payload in a log call on lines %s" % offenders)

    def test_no_log_call_dumps_a_request_body(self):
        text = self.source()
        self.assertIsNone(re.search(r"log\.\w+\([^)]*request\.get_json", text))
        self.assertIsNone(re.search(r"log\.\w+\([^)]*request\.(data|json|form)\b", text))
        self.assertIsNone(re.search(r"log\.\w+\([^)]*%r[^)]*\btargets\b", text),
                          "a log line prints the targets the caller sent")

    def test_the_redactor_covers_every_credential_name_a_request_can_carry(self):
        for key in ("password", "fallback_password", "new_password", "passphrase", "token",
                    "csrf_token", "authorization", "cookie", "credentials"):
            self.assertEqual(omni_users.redact({key: "hunter2-secret"})[key], "***", key)
        nested = omni_users.redact({"a": [{"password": "x"}], "method": {"b": {"passphrase": "y"}}})
        self.assertEqual(nested["a"][0]["password"], "***")
        self.assertEqual(nested["method"]["b"]["passphrase"], "***")

    def test_the_audit_record_leaves_out_credentials_the_request_carried(self):
        import OmniMatrix_upgrade_server_v7_6y as srv
        marker = "canary-credential-value-93417"
        records = []

        class Capture(logging.Handler):
            def emit(self, record):
                records.append(record.getMessage())

        handler = Capture()
        srv.log.addHandler(handler)
        previous = srv.log.level
        srv.log.setLevel(logging.INFO)
        try:
            @srv._audited("test.operation")
            def view():
                return {"ok": True}

            with srv.app.test_request_context(
                    "/x", method="POST", json={"password": marker, "passphrase": marker,
                                               "fallback_password": marker, "ip": "192.0.2.1"}):
                view()
        finally:
            srv.log.removeHandler(handler)
            srv.log.setLevel(previous)
        self.assertTrue(records, "the audit wrapper logged nothing; the test is not measuring it")
        self.assertFalse([r for r in records if marker in r], records)


if __name__ == "__main__":
    unittest.main()
