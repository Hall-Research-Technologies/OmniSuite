"""The certificate state the server reports, and the wording Settings builds from it.

OMNI-MA-002. Real certificates are generated into a temporary directory; the
remote listener never binds. Each case asks the running application's own
/api/remote_access for the certificate it holds, then hands that exact JSON to
the wording code lifted from ui/settings.js, so the server's description and the
browser's reading of it are tested together.
"""
from __future__ import annotations

import datetime
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("OMNI_SMOKE", "1")

import omni_tls                                          # noqa: E402
import omni_users                                        # noqa: E402

ADDRESS = "198.51.100.85"
OTHER = "198.51.100.99"
ADAPTER = {
    "id": "guid:aaaa1111-2222-3333-4444-555566667777", "id_from": "guid",
    "name": "Ethernet 3", "address": ADDRESS, "netmask": "255.255.255.0",
    "prefix": 24, "cidr": "198.51.100.0/24", "scan": "", "status": "Up",
    "up": True, "virtual": False, "label": "Ethernet 3",
}

NODE = r"""
const fs = require('fs'), vm = require('vm');
const src = fs.readFileSync(process.argv[1], 'utf8').split('\r\n').join('\n');
const a = src.indexOf('// BEGIN certificate wording'), b = src.indexOf('// END certificate wording');
const sb = {document: {getElementById: () => null}, Array, console}; vm.createContext(sb);
vm.runInContext(src.slice(a, b) + '\nglobalThis.api = {certificateKind, httpsLabel, certificateWords, certificateDetail};', sb);
const state = JSON.parse(fs.readFileSync(0, 'utf8'));
const kind = sb.api.certificateKind(state.certificate, state);
process.stdout.write(JSON.stringify({kind, label: sb.api.httpsLabel(kind),
  words: sb.api.certificateWords(state.certificate, state),
  detail: sb.api.certificateDetail(state.certificate, kind)}));
"""

HAVE_NODE = shutil.which("node") is not None


@unittest.skipUnless(omni_tls.generation_available(),
                     "this build cannot generate a certificate")
@unittest.skipUnless(HAVE_NODE, "node is not installed")
class CertificateStateTests(unittest.TestCase):

    def setUp(self):
        import OmniMatrix_upgrade_server_v7_6y as srv
        self.srv = srv
        self.dir = Path(tempfile.mkdtemp(prefix="omni-certstate-"))
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.own = (self.dir / "own_cert.pem", self.dir / "own_key.pem")
        self.site = (self.dir / "site_cert.pem", self.dir / "site_key.pem")
        for name, value in (("REMOTE_ACCESS_FILE", self.dir / "remote_access.json"),
                            ("REMOTE_CERT_FILE", self.own[0]),
                            ("REMOTE_KEY_FILE", self.own[1])):
            patcher = mock.patch.object(srv, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.adapters = [dict(ADAPTER)]
        patcher = mock.patch("omni_adapters.enumerate_adapters",
                             side_effect=lambda *a, **k: [dict(x) for x in self.adapters])
        patcher.start()
        self.addCleanup(patcher.stop)
        store = srv._user_store
        saved = (store._path, store._audit_path, dict(store._accounts),
                 dict(store._sessions), list(store._audit))

        def restore():
            store._path, store._audit_path = saved[0], saved[1]
            store._accounts.clear(); store._accounts.update(saved[2])
            store._sessions.clear(); store._sessions.update(saved[3])
            store._audit[:] = saved[4]
        self.addCleanup(restore)
        store._path = self.dir / "users.json"
        store._audit_path = self.dir / "audit.json"
        store._accounts.clear(); store._sessions.clear(); store._audit.clear()
        store.create("principal", "principal-pass-1", omni_users.ROLE_ADMIN)
        self.started = []
        patcher = mock.patch.object(
            srv._remote_listener, "start",
            lambda address, port, ssl_context=None: (self.started.append(address),
                                                    {"ok": True})[1])
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = srv.app.test_client()
        # The adapter the saved configuration names, so the address is known.
        srv._remote_access_preference(False, ADDRESS, 8443, transport="https",
                                      adapter_id=ADAPTER["id"],
                                      adapter_name=ADAPTER["name"])

    # -- helpers
    def state(self):
        response = self.client.get("/api/remote_access")
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        return response.get_json()

    def wording(self, state=None):
        state = state or self.state()
        done = subprocess.run(["node", "-e", NODE, str(ROOT / "ui" / "settings.js")],
                              input=json.dumps(state), capture_output=True,
                              encoding="utf-8", timeout=30)
        self.assertEqual(done.returncode, 0, done.stderr)
        return json.loads(done.stdout)

    def generate(self):
        omni_tls.generate_self_signed(ADDRESS, *self.own)

    def supply(self, address=ADDRESS):
        omni_tls.generate_self_signed(address, *self.site)
        response = self.client.post("/api/admin/certificate", json={
            "action": "use", "cert_path": str(self.site[0]),
            "key_path": str(self.site[1])})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))

    # -- 1 generated active
    def test_a_generated_certificate_is_active_and_self_signed(self):
        self.generate()
        state = self.state()
        cert = state["certificate"]
        self.assertTrue(cert["usable"])
        self.assertTrue(cert["generated"])
        self.assertTrue(cert["matches_address"])
        said = self.wording(state)
        self.assertEqual(said["kind"], "self_signed")
        self.assertEqual(said["label"], "HTTPS — encrypted · certificate active")
        self.assertIn("Self-signed certificate active for " + ADDRESS, said["words"])
        self.assertIn("Your browser may warn because this certificate is self-signed.",
                      said["words"])
        self.assertIn("Fingerprint: " + cert["fingerprint"], said["words"])
        self.assertIn("days left)", said["words"])

    # -- 2 custom active
    def test_a_supplied_certificate_is_custom_active(self):
        self.supply()
        said = self.wording()
        self.assertEqual(said["kind"], "custom")
        self.assertEqual(said["label"],
                         "HTTPS — encrypted · custom certificate active")
        self.assertTrue(said["words"].startswith("Custom certificate active"))

    # -- 3 missing
    def test_a_missing_certificate_is_required(self):
        said = self.wording()
        self.assertEqual(said["kind"], "required")
        self.assertEqual(said["label"], "HTTPS — certificate required")
        self.assertIn("Turn off Remote Access, then create or install a certificate",
                      said["words"])

    # -- 4 expired
    def test_an_expired_certificate_is_required(self):
        # Generated four years ago, so its three calendar years are over.
        omni_tls.generate_self_signed(
            ADDRESS, *self.own,
            now=datetime.datetime.now(datetime.timezone.utc)
            - datetime.timedelta(days=4 * 366))
        state = self.state()
        self.assertTrue(state["certificate"].get("expired"), state["certificate"])
        said = self.wording(state)
        self.assertEqual(said["label"], "HTTPS — certificate required")
        self.assertTrue(said["words"].startswith("The certificate has expired."))

    # -- 5 unreadable
    def test_an_unreadable_certificate_is_required(self):
        self.own[0].write_text("this is not a certificate", encoding="utf-8")
        self.own[1].write_text("nor is this", encoding="utf-8")
        state = self.state()
        self.assertFalse(state["certificate"]["readable"])
        said = self.wording(state)
        self.assertEqual(said["label"], "HTTPS — certificate required")
        self.assertIn("Not a readable PEM certificate", said["words"])

    def test_a_key_that_does_not_belong_is_required_not_active(self):
        self.generate()
        other = (self.dir / "x_cert.pem", self.dir / "x_key.pem")
        omni_tls.generate_self_signed(ADDRESS, *other)
        shutil.copyfile(other[1], self.own[1])               # someone else's key
        state = self.state()
        cert = state["certificate"]
        self.assertTrue(cert["readable"], "the certificate itself parses")
        self.assertFalse(cert["usable"], "but it cannot serve with that key")
        said = self.wording(state)
        self.assertEqual(said["label"], "HTTPS — certificate required")
        self.assertIn("does not match", said["words"])

    def test_a_missing_key_is_required_not_active(self):
        self.generate()
        self.own[1].unlink()
        said = self.wording()
        self.assertEqual(said["label"], "HTTPS — certificate required")

    # -- 6 wrong network
    def test_a_generated_certificate_for_another_network_is_required(self):
        self.generate()
        self.adapters[0]["address"] = OTHER
        state = self.state()
        self.assertFalse(state["certificate"]["matches_address"])
        said = self.wording(state)
        self.assertEqual(said["label"], "HTTPS — certificate required")
        self.assertIn(OTHER, said["words"])
        self.assertIn("Create one for this network", said["detail"])

    def test_a_supplied_certificate_is_not_judged_against_the_adapter(self):
        self.supply()
        self.adapters[0]["address"] = OTHER
        said = self.wording()
        self.assertEqual(said["kind"], "custom")
        self.assertIn("will not alter a certificate you supplied", said["detail"])

    # -- 7, 8, 9, 10 the transport and the switch do not decide it
    def test_the_label_follows_the_certificate_whatever_the_transport_or_the_switch(self):
        self.generate()
        srv = self.srv
        labels = set()
        for transport in ("http", "https"):
            for enabled in (False, True):
                srv._remote_access_preference(enabled, ADDRESS, 8443,
                                              transport=transport,
                                              adapter_id=ADAPTER["id"],
                                              adapter_name=ADAPTER["name"])
                state = self.state()
                self.assertEqual(state["transport"], transport)
                labels.add(self.wording(state)["label"])
        self.assertEqual(labels, {"HTTPS — encrypted · certificate active"})

    def test_choosing_https_with_nothing_usable_never_reads_as_active(self):
        srv = self.srv
        srv._remote_access_preference(False, ADDRESS, 8443, transport="https",
                                      adapter_id=ADAPTER["id"],
                                      adapter_name=ADAPTER["name"])
        self.assertEqual(self.wording()["label"], "HTTPS — certificate required")

    def test_the_state_never_carries_key_material(self):
        self.generate()
        body = json.dumps(self.state())
        self.assertNotIn("PRIVATE KEY", body)
        self.assertNotIn(self.own[1].read_text(encoding="utf-8").strip()[:40], body)

    # -- 12 reading the state changes nothing
    def test_reading_the_state_starts_nothing_and_rewrites_nothing(self):
        self.generate()
        before = (self.dir / "remote_access.json").read_bytes()
        self.state()
        self.state()
        self.assertEqual(self.started, [], "reading a page must not start a listener")
        self.assertEqual((self.dir / "remote_access.json").read_bytes(), before)

    def test_https_that_cannot_start_still_refuses_rather_than_downgrading(self):
        """Unchanged behaviour: no usable certificate, no listener, no plaintext."""
        response = self.client.post("/api/remote_access", json={
            "enabled": True, "address": ADDRESS, "port": 8443,
            "adapter_id": ADAPTER["id"], "transport": "https"})
        self.assertNotEqual(response.status_code, 200)
        self.assertEqual(self.started, [])


if __name__ == "__main__":
    unittest.main()
