"""No response to a network-facing listener carries a device password.

Found by asking as a remote administrator: /api/cache and /api/scan_results
returned each unit's stored password in clear (1,124 and 279 bytes of synthetic
state, the secret in both). The AGENTS rule -- no plaintext device password for a
remote caller -- had been applied to /api/config and nowhere else.

The fix is one response filter on the boundary rather than a patch per route, so
these assert the property for the routes that leaked and for the filter itself,
and that the local operator's answers are unchanged.
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

import omni_remote                                       # noqa: E402
import omni_users                                        # noqa: E402

SECRET = "Synthetic-Device-Pw-0001"
UNIT = {"ip": "198.51.100.7", "mac": "B8:98:B0:00:00:01", "password": SECRET,
        "model": "AT-OMNI-111", "hostname": "unit-a",
        "details": {"x": {"password": SECRET, "keep": 1}}}


class RemoteResponsesCarryNoDevicePasswordTests(unittest.TestCase):

    def setUp(self):
        import OmniMatrix_upgrade_server_v7_6y as srv
        self.srv = srv
        self.dir = Path(tempfile.mkdtemp(prefix="omni-credfilter-"))
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
        account = self.store.find_by_username("principal")
        self.session = self.store.open_session(account.id)

        scan = self.dir / "scan_results.json"
        scan.write_text(json.dumps({"devices": [UNIT], "encoders": [UNIT],
                                    "decoders": []}))
        for patcher in (
                mock.patch.object(srv, "SCAN_RESULTS", scan),
                mock.patch.object(srv, "_load_cache", return_value=[dict(UNIT)]),
                mock.patch.object(type(srv._remote_listener), "running",
                                  new_callable=mock.PropertyMock,
                                  return_value=True)):
            patcher.start()
            self.addCleanup(patcher.stop)
        # A GET of /api/cache starts a background verification sweep that reads the
        # transport through module globals when it fires. Wait for it while these
        # stubs are still installed, so it cannot land in another test's recorder.
        self.addCleanup(lambda: self.assertTrue(
            srv.await_startup_tasks(timeout=15.0),
            "a verification sweep started by this test was still running at teardown"))

    def ask(self, path, remote=True):
        client = self.srv.app.test_client()
        env = {}
        headers = {}
        if remote:
            env[omni_remote.LISTENER_KEY] = omni_remote.REMOTE
            client.set_cookie("omnisuite_session", self.session.token,
                              domain="localhost")
            headers["X-OmniSuite-CSRF"] = self.session.csrf_token
        return client.get(path, headers=headers, environ_base=env)

    def test_the_routes_that_leaked_no_longer_do(self):
        for path in ("/api/cache", "/api/scan_results"):
            with self.subTest(path=path):
                response = self.ask(path)
                self.assertEqual(response.status_code, 200, path)
                self.assertNotIn(SECRET, response.get_data(as_text=True))

    def test_the_rest_of_the_record_is_still_there(self):
        body = self.ask("/api/scan_results").get_json()
        unit = body["devices"][0]
        self.assertEqual(unit["mac"], UNIT["mac"])
        self.assertEqual(unit["details"]["x"], {"password": "", "keep": 1},
                         "only the credential is blanked, however deeply nested")
        self.assertEqual(unit["password"], "", "the key stays: the shape is the API")

    def test_the_local_operator_is_unchanged(self):
        """Nothing the application does on its own computer changes."""
        response = self.ask("/api/scan_results", remote=False)
        self.assertIn(SECRET, response.get_data(as_text=True))

    def test_the_filter_removes_a_credential_wherever_it_is(self):
        strip = self.srv._without_credentials
        self.assertEqual(
            strip({"a": [{"password": "x", "b": 1}], "fallback_password": "y",
                   "c": {"d": {"password": "z"}}, "e": "password"}),
            {"a": [{"password": "", "b": 1}], "fallback_password": "",
             "c": {"d": {"password": ""}}, "e": "password"})

    def test_a_response_that_is_not_json_is_left_alone(self):
        response = self.ask("/login")
        self.assertEqual(response.status_code, 200)


if __name__ == "__main__":
    unittest.main()
