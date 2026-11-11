"""First-time Remote Access starts on HTTP; HTTPS stays one choice away.

A certificate generated against a DHCP address stops matching when the address
moves, so HTTPS-by-default produced a browser warning the first time that
happened. HTTP is the starting point now, clearly labelled as unencrypted, and
nothing about it weakens what protects a sign-in: the plaintext listener still
needs an explicit acknowledgement in the request, so a caller that merely omits
a field cannot reach it.

The listener never binds here: start() is replaced with one that records what it
was handed. Addresses are RFC 5737 documentation addresses.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("OMNI_SMOKE", "1")

import omni_tls                                          # noqa: E402
import omni_users                                        # noqa: E402

ADDRESS = "198.51.100.85"
ADAPTER = {
    "id": "guid:bbbb1111-2222-3333-4444-555566667777", "id_from": "guid",
    "name": "Ethernet 3", "address": ADDRESS, "netmask": "255.255.255.0",
    "prefix": 24, "cidr": "198.51.100.0/24", "scan": "", "status": "Up",
    "up": True, "virtual": False, "label": "Ethernet 3",
}


class HttpDefaultTests(unittest.TestCase):

    def setUp(self):
        import OmniMatrix_upgrade_server_v7_6y as srv
        self.srv = srv
        self.dir = Path(tempfile.mkdtemp(prefix="omni-httpdefault-"))
        self.cert = (self.dir / "own_cert.pem", self.dir / "own_key.pem")
        for name, value in (("REMOTE_ACCESS_FILE", self.dir / "remote_access.json"),
                            ("REMOTE_CERT_FILE", self.cert[0]),
                            ("REMOTE_KEY_FILE", self.cert[1])):
            patcher = mock.patch.object(srv, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = mock.patch("omni_adapters.enumerate_adapters",
                             return_value=[dict(ADAPTER)])
        patcher.start()
        self.addCleanup(patcher.stop)

        self.store = srv._user_store
        saved = (self.store._path, self.store._audit_path,
                 dict(self.store._accounts), dict(self.store._sessions),
                 list(self.store._audit))

        def restore():
            self.store._path, self.store._audit_path = saved[0], saved[1]
            self.store._accounts.clear()
            self.store._accounts.update(saved[2])
            self.store._sessions.clear()
            self.store._sessions.update(saved[3])
            self.store._audit[:] = saved[4]

        self.addCleanup(restore)
        self.store._path = self.dir / "users.json"
        self.store._audit_path = self.dir / "audit.json"
        self.store._accounts.clear()
        self.store._sessions.clear()
        self.store._audit.clear()
        self.store.create("principal", "principal-pass-1", omni_users.ROLE_ADMIN)

        self.started = []

        def fake_start(address, port, ssl_context=None):
            self.started.append({"address": address, "context": ssl_context})
            return {"ok": True, "address": address, "port": int(port)}

        patcher = mock.patch.object(srv._remote_listener, "start", fake_start)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = srv.app.test_client()

    def enable(self, **body):
        payload = {"enabled": True, "address": ADDRESS, "port": 8443,
                   "adapter_id": ADAPTER["id"]}
        payload.update(body)
        return self.client.post("/api/remote_access", json=payload)

    def saved(self):
        return self.srv._remote_access_last_choice()

    # ---- what the application starts from -----------------------------
    def test_a_first_time_setup_is_http(self):
        self.assertEqual(self.saved()["transport"], "http")

    def test_the_state_endpoint_reports_it_as_unencrypted(self):
        body = self.client.get("/api/remote_access").get_json()
        self.assertEqual(body["transport"], "http")
        self.assertFalse(body["encrypted"])

    def test_an_explicit_saved_choice_is_honoured(self):
        path = self.srv.REMOTE_ACCESS_FILE
        for stored, expected in (("https", "https"), ("http", "http")):
            with self.subTest(stored=stored):
                path.write_text(json.dumps({"enabled": False, "transport": stored}),
                                encoding="utf-8")
                self.assertEqual(self.saved()["transport"], expected)

    def test_a_record_with_no_transport_keeps_reading_as_https(self):
        """An installation that already has a record is not moved to plaintext."""
        self.srv.REMOTE_ACCESS_FILE.write_text(
            json.dumps({"enabled": False, "address": ADDRESS}), encoding="utf-8")
        self.assertEqual(self.saved()["transport"], "https")

    def test_a_damaged_preference_is_off_and_never_exposes_the_machine(self):
        self.srv.REMOTE_ACCESS_FILE.write_text("{ nope", encoding="utf-8")
        saved = self.saved()
        self.assertFalse(saved["enabled"])

    # ---- plaintext still needs an acknowledgement ----------------------
    def test_omitting_the_transport_does_not_start_plaintext_by_itself(self):
        response = self.enable()
        self.assertEqual(response.status_code, 400)
        self.assertTrue(response.get_json()["needs_confirmation"])
        self.assertEqual(self.started, [], "nothing may listen without consent")

    def test_a_field_with_another_name_is_not_consent(self):
        for body in ({"accept": True}, {"acceptInsecure": True}, {"insecure": True}):
            with self.subTest(body=body):
                self.assertEqual(self.enable(**body).status_code, 400)
        self.assertEqual(self.started, [])

    def test_only_the_boolean_true_is_consent(self):
        # JSON strings and numbers are truthy in Python: "false" and "no" would
        # otherwise switch sign-in details to plaintext.
        for value in ("yes please", "yes", "true", "false", "no", "0", 1, 2, 0, None,
                      [], [True], {}, {"ok": True}, ""):
            with self.subTest(value=value):
                response = self.enable(accept_insecure=value)
                self.assertEqual(response.status_code, 400, repr(value))
                self.assertTrue(response.get_json()["needs_confirmation"], repr(value))
        self.assertEqual(self.started, [], "nothing may listen without consent")

    def test_with_the_acknowledgement_http_starts_and_is_remembered(self):
        response = self.enable(accept_insecure=True)
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertEqual(len(self.started), 1)
        self.assertIsNone(self.started[0]["context"], "no TLS context for http")
        self.assertEqual(self.saved()["transport"], "http")

    def test_http_is_recorded_in_activity(self):
        self.enable(accept_insecure=True)
        entry = [e for e in self.store._audit
                 if e["event"] == "remote_access.enabled"][-1]
        self.assertEqual(entry["detail"]["transport"], "http")

    # ---- HTTPS remains available and never downgrades -------------------
    def test_https_is_still_available_when_asked_for(self):
        if not omni_tls.generation_available():
            self.skipTest("this build cannot generate a certificate")
        omni_tls.generate_self_signed(ADDRESS, *self.cert)
        response = self.enable(transport="https")
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertIsNotNone(self.started[0]["context"])
        self.assertEqual(self.saved()["transport"], "https")

    def test_a_saved_https_choice_is_not_replaced_by_the_default(self):
        if not omni_tls.generation_available():
            self.skipTest("this build cannot generate a certificate")
        omni_tls.generate_self_signed(ADDRESS, *self.cert)
        self.assertEqual(self.enable(transport="https").status_code, 200)
        self.client.post("/api/remote_access", json={"enabled": False})
        # Turning it back on without naming a transport keeps HTTPS.
        again = self.enable()
        self.assertEqual(again.status_code, 200, again.get_data(as_text=True))
        self.assertIsNotNone(self.started[-1]["context"])

    def test_https_with_no_usable_certificate_never_falls_back_to_http(self):
        self.cert[0].unlink(missing_ok=True)
        self.cert[1].unlink(missing_ok=True)
        response = self.enable(transport="https")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.started, [])

    def test_an_unknown_transport_name_is_not_plaintext(self):
        response = self.enable(transport="HTTP2 please", accept_insecure=False)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.started, [])


class SettingsPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.js = (ROOT / "ui" / "settings.js").read_text(encoding="utf-8")

    def test_http_is_the_first_option_and_is_labelled_unencrypted(self):
        start = self.js.index('id="cfg_remote_transport"')
        block = self.js[start:self.js.index("</select>", start)]
        self.assertLess(block.index('value="http"'), block.index('value="https"'))
        self.assertIn("not encrypted", block.split('value="https"')[0].lower())

    def test_the_page_says_why_https_is_not_the_default(self):
        self.assertIn("cfg_remote_transport_hint", self.js)
        self.assertIn("DHCP", self.js)

    def test_the_request_still_carries_the_acknowledgement_for_http(self):
        self.assertIn("accept_insecure: transport === 'http'", self.js)


if __name__ == "__main__":
    unittest.main()
