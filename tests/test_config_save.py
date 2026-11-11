"""What saving Settings may and may not change.

Found by reading /api/config as a remote administrator would use it:

  * a save that omitted a field reset it -- the username to "admin", the firmware
    folder to blank -- and persisted that;
  * nothing was validated, so a timeout of NaN, zero or a negative number was
    saved and could stop every device read, and a non-numeric port raised after
    part of the configuration had already been assigned;
  * a signed-in remote session could name any folder on the host as the firmware
    folder, which the firmware upload then reads from.

Nothing here touches a device; the configuration file is the test run's own.
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

import omni_remote                                       # noqa: E402
import omni_users                                        # noqa: E402

KEYS = ("USERNAME", "PASSWORD", "FALLBACK_PASSWORD", "WS_PORT", "TIMEOUT",
        "UPLOAD_CONCURRENCY", "FIRMWARE_PATH")


class ConfigSaveTests(unittest.TestCase):

    def setUp(self):
        import OmniMatrix_upgrade_server_v7_6y as srv
        self.srv = srv
        self.dir = Path(tempfile.mkdtemp(prefix="omni-cfgsave-"))
        before = {k: srv.app.config.get(k) for k in KEYS}

        def restore():
            for key, value in before.items():
                srv.app.config[key] = value

        self.addCleanup(restore)
        for key, value in (("USERNAME", "operator"), ("PASSWORD", "pw-primary-1"),
                           ("FALLBACK_PASSWORD", "pw-fallback-1"), ("WS_PORT", 81),
                           ("TIMEOUT", 3.0), ("UPLOAD_CONCURRENCY", 5),
                           ("FIRMWARE_PATH", str(self.dir))):
            srv.app.config[key] = value
        for patcher in (mock.patch.object(srv, "_save_config"),
                        mock.patch.object(srv, "_configure_matrix_logic_from_app")):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.store = srv._user_store
        saved = (self.store._path, self.store._audit_path,
                 dict(self.store._accounts), dict(self.store._sessions),
                 list(self.store._audit))

        def restore_store():
            self.store._path, self.store._audit_path = saved[0], saved[1]
            self.store._accounts.clear()
            self.store._accounts.update(saved[2])
            self.store._sessions.clear()
            self.store._sessions.update(saved[3])
            self.store._audit[:] = saved[4]

        self.addCleanup(restore_store)
        self.store._path = self.dir / "users.json"
        self.store._audit_path = self.dir / "audit.json"
        self.store._accounts.clear()
        self.store._sessions.clear()
        self.store._audit.clear()
        self.store.create("principal", "principal-pass-1", omni_users.ROLE_ADMIN)
        self.session = self.store.open_session(
            self.store.find_by_username("principal").id)
        patcher = mock.patch.object(type(srv._remote_listener), "running",
                                    new_callable=mock.PropertyMock,
                                    return_value=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def post(self, body, remote=False):
        client = self.srv.app.test_client()
        env, headers = {}, {}
        if remote:
            env[omni_remote.LISTENER_KEY] = omni_remote.REMOTE
            client.set_cookie("omnisuite_session", self.session.token,
                              domain="localhost")
            headers["X-OmniSuite-CSRF"] = self.session.csrf_token
        return client.post("/api/config", json=body, headers=headers,
                           environ_base=env)

    def config(self):
        return {k: self.srv.app.config.get(k) for k in KEYS}

    def test_a_partial_save_leaves_every_other_setting_alone(self):
        before = self.config()
        response = self.post({"timeout": 6})
        self.assertEqual(response.status_code, 200)
        after = self.config()
        self.assertEqual(after["TIMEOUT"], 6.0)
        after["TIMEOUT"] = before["TIMEOUT"]
        self.assertEqual(after, before)

    def test_a_bad_value_refuses_the_whole_save_and_changes_nothing(self):
        before = self.config()
        for body in ({"timeout": "nan"}, {"timeout": 0}, {"timeout": -1},
                     {"timeout": "inf"}, {"timeout": 1e9}, {"ws_port": 0},
                     {"ws_port": 70000}, {"ws_port": "eighty"},
                     {"concurrency": 0}, {"concurrency": 10 ** 6},
                     {"firmware_path": 5}, {"username": "new", "ws_port": "x"}):
            with self.subTest(body=body):
                response = self.post(body)
                self.assertEqual(response.status_code, 400, body)
                self.assertEqual(self.config(), before,
                                 "a refused save must not change anything")

    def test_a_body_that_is_not_an_object_is_refused_and_saves_nothing(self):
        before = self.config()
        client = self.srv.app.test_client()
        for body in (["a", "list"], "text", 5, None):
            with self.subTest(body=body):
                response = client.post("/api/config", json=body)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(self.config(), before)

    def test_an_object_with_nothing_to_save_is_refused_too(self):
        before = self.config()
        response = self.srv.app.test_client().post("/api/config", json={"unrelated": 1})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.config(), before)

    def test_a_remote_session_cannot_choose_the_firmware_folder(self):
        before = self.config()
        response = self.post({"firmware_path": str(self.dir.parent)}, remote=True)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.config(), before)

    def test_a_remote_save_that_repeats_the_folder_is_an_ordinary_save(self):
        """The page round-trips the folder it was shown."""
        response = self.post({"firmware_path": str(self.dir), "timeout": 7},
                             remote=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.config()["TIMEOUT"], 7.0)

    def test_the_local_operator_still_chooses_the_folder(self):
        elsewhere = str(self.dir.parent)
        self.assertEqual(self.post({"firmware_path": elsewhere}).status_code, 200)
        self.assertEqual(self.config()["FIRMWARE_PATH"], elsewhere)

    def test_a_remote_save_still_preserves_the_device_passwords(self):
        self.assertEqual(self.post({"timeout": 8}, remote=True).status_code, 200)
        cfg = self.config()
        self.assertEqual((cfg["PASSWORD"], cfg["FALLBACK_PASSWORD"]),
                         ("pw-primary-1", "pw-fallback-1"))


if __name__ == "__main__":
    unittest.main()
