"""A certificate the administrator supplied is the one that gets served.

The defect these exist for: "Use these files" saved the chosen pair, and the
Settings toggle then turned Remote Access on with a request that carried no
certificate paths. The enable handler read the paths from that request alone, so
it served OmniSuite's own generated certificate (or failed, if there was none)
and then wrote blank paths over the saved choice -- the supplied pair was
forgotten the first time Remote Access was switched on.

Every certificate is generated into a temporary directory during the test and the
listener never binds: its start() is replaced with one that records the TLS
context it was handed.
"""
from __future__ import annotations

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
    "id": "guid:aaaa1111-2222-3333-4444-555566667777", "id_from": "guid",
    "name": "Ethernet 3", "address": ADDRESS, "netmask": "255.255.255.0",
    "prefix": 24, "cidr": "198.51.100.0/24", "scan": "", "status": "Up",
    "up": True, "virtual": False, "label": "Ethernet 3",
}


class SuppliedCertificateSurvivesEnableTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        if not omni_tls.generation_available():
            raise unittest.SkipTest("this build cannot generate a certificate")

    def setUp(self):
        import OmniMatrix_upgrade_server_v7_6y as srv
        self.srv = srv
        self.dir = Path(tempfile.mkdtemp(prefix="omni-certchoice-"))
        self.generated = (self.dir / "own_cert.pem", self.dir / "own_key.pem")
        self.supplied = (self.dir / "site_cert.pem", self.dir / "site_key.pem")
        omni_tls.generate_self_signed(ADDRESS, *self.generated)
        omni_tls.generate_self_signed(ADDRESS, *self.supplied)

        for name, value in (("REMOTE_ACCESS_FILE", self.dir / "remote_access.json"),
                            ("REMOTE_CERT_FILE", self.generated[0]),
                            ("REMOTE_KEY_FILE", self.generated[1])):
            patcher = mock.patch.object(srv, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = mock.patch("omni_adapters.enumerate_adapters",
                             return_value=[dict(ADAPTER)])
        patcher.start()
        self.addCleanup(patcher.stop)

        # The API captured the store when it registered, so redirect its files.
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

        self.contexts = []

        def fake_start(address, port, ssl_context=None):
            self.contexts.append(ssl_context)
            return {"ok": True, "address": address, "port": int(port)}

        patcher = mock.patch.object(srv._remote_listener, "start", fake_start)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = srv.app.test_client()

    def saved(self):
        return self.srv._remote_access_last_choice()

    def use_supplied(self):
        r = self.client.post("/api/admin/certificate", json={
            "action": "use", "cert_path": str(self.supplied[0]),
            "key_path": str(self.supplied[1])})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))

    def enable(self, **extra):
        # Exactly what the Settings toggle sends: no certificate paths at all.
        body = {"enabled": True, "address": ADDRESS, "port": 8443,
                "transport": "https", "adapter_id": ADAPTER["id"],
                "accept_insecure": False}
        body.update(extra)
        return self.client.post("/api/remote_access", json=body)

    def test_enabling_keeps_the_supplied_pair_in_the_saved_choice(self):
        self.use_supplied()
        response = self.enable()
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertEqual(self.saved()["cert_path"], str(self.supplied[0]))
        self.assertEqual(self.saved()["key_path"], str(self.supplied[1]))

    def test_enabling_serves_the_supplied_certificate_not_the_generated_one(self):
        self.use_supplied()
        with mock.patch.object(omni_tls, "load_context",
                               wraps=omni_tls.load_context) as load:
            self.assertEqual(self.enable().status_code, 200)
        loaded = [tuple(str(a) for a in c.args[:2]) for c in load.call_args_list]
        self.assertIn((str(self.supplied[0]), str(self.supplied[1])), loaded)
        self.assertNotIn((str(self.generated[0]), str(self.generated[1])), loaded,
                         "the generated pair was loaded although a pair was supplied")

    def test_a_supplied_pair_works_when_no_generated_pair_exists(self):
        self.use_supplied()
        self.generated[0].unlink()
        self.generated[1].unlink()
        response = self.enable()
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))

    def test_the_choice_survives_off_and_on_again(self):
        self.use_supplied()
        self.assertEqual(self.enable().status_code, 200)
        self.client.post("/api/remote_access", json={"enabled": False})
        self.assertEqual(self.enable().status_code, 200)
        self.assertEqual(self.saved()["cert_path"], str(self.supplied[0]))

    def test_a_restart_uses_the_supplied_pair(self):
        self.use_supplied()
        self.enable()
        context, error = self.srv._remote_tls_context(self.saved())
        self.assertEqual(error, "")
        self.assertIsNotNone(context)

    def test_a_bad_supplied_pair_is_refused_and_never_replaced_by_the_generated_one(self):
        self.use_supplied()
        self.supplied[0].write_text("not a certificate")
        response = self.enable()
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.contexts, [], "the listener started anyway")

    def test_a_mismatched_pair_is_refused_without_a_fallback(self):
        other = (self.dir / "other_cert.pem", self.dir / "other_key.pem")
        omni_tls.generate_self_signed(ADDRESS, *other)
        # The saved certificate and a key that belongs to a different one.
        self.srv._remote_access_preference(
            False, ADDRESS, 8443, "https", str(self.supplied[0]), str(other[1]))
        response = self.enable()
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.contexts, [])

    def test_a_half_chosen_pair_is_refused_not_completed_from_the_generated_one(self):
        self.srv._remote_access_preference(
            False, ADDRESS, 8443, "https", str(self.supplied[0]), "")
        response = self.enable()
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.contexts, [])

    def test_a_restart_with_half_a_pair_does_not_complete_it_from_the_generated_files(self):
        """The restart path, reached directly.

        The enable handler refuses a half pair first, so this second line of
        defence is never what answers a request -- which is exactly why it needs
        its own test: a saved file edited or damaged by hand reaches it with no
        handler in front.
        """
        for cert, key in ((str(self.supplied[0]), ""), ("", str(self.supplied[1]))):
            with self.subTest(cert=bool(cert), key=bool(key)):
                context, error = self.srv._remote_tls_context(
                    {"transport": "https", "cert_path": cert, "key_path": key})
                self.assertIsNone(context)
                self.assertIn("together", error)

    def test_generated_mode_still_works_with_no_supplied_pair(self):
        response = self.enable()
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertEqual(len(self.contexts), 1)
        self.assertEqual(self.saved()["cert_path"], "")

    def test_an_explicit_pair_in_the_request_still_wins(self):
        response = self.enable(cert_path=str(self.supplied[0]),
                               key_path=str(self.supplied[1]))
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertEqual(self.saved()["cert_path"], str(self.supplied[0]))


if __name__ == "__main__":
    unittest.main()
