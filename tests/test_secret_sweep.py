"""No credential appears anywhere it should not, across a whole working day.

The existing tests check one place at a time (the audit store, one route's reply).
This one plants a distinct canary for every kind of secret the application handles,
runs the operations that touch them -- sign-in, a failed sign-in, a lockout, a
password change, Direct Access (enable, open, regenerate), a backup export and a
restore, an account deleted -- and then looks everywhere a secret could have been
left:

    every log record, with its arguments and any exception text
    the audit record, in memory and on disk
    the files the application writes
    every response body, header and redirect

except the places a secret is *meant* to be: the reply that hands an administrator
a new link, and the cookie that carries a session. A password is never one of them.

Real endpoints, throwaway data directory, network fenced.
"""
from __future__ import annotations

import io
import json
import logging
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import omni_gate                                          # noqa: E402
import omni_remote                                        # noqa: E402
import omni_users                                         # noqa: E402
from omni_users import Permissions                        # noqa: E402
from tests.test_backup import PASSPHRASE as _UNUSED       # noqa: E402,F401
from tests.test_restore_sessions import RestoreSessionBase   # noqa: E402

REMOTE = {omni_remote.LISTENER_KEY: omni_remote.REMOTE}

CANARIES = {
    "admin password": "canary-admin-password-A1",
    "user password": "canary-user-password-B2",
    "new password": "canary-new-password-C3",
    "wrong password": "canary-wrong-password-D4",
    "backup passphrase": "canary-backup-passphrase-E5",
    "device credential": "CANARY-DEVICE-SECRET-F6",
}


class Capture(logging.Handler):
    def __init__(self):
        super().__init__(logging.DEBUG)
        self.lines = []

    def emit(self, record):
        try:
            text = record.getMessage()
        except Exception:
            text = str(record.msg) + " " + str(record.args)
        if record.exc_info:
            text += " " + repr(record.exc_info[1])
        self.lines.append(text)


class SweepBase(RestoreSessionBase):
    def setUp(self):
        super().setUp()
        self.capture = Capture()
        root = logging.getLogger()
        previous = root.level
        root.addHandler(self.capture)
        root.setLevel(logging.DEBUG)
        self.addCleanup(lambda: (root.removeHandler(self.capture),
                                 root.setLevel(previous)))
        self.bodies = []         # (where, text) of everything a client could read
        self.allowed = {}        # where -> the secrets that reply is meant to carry

    def seen(self, where, reply, allow=()):
        parts = [reply.get_data(as_text=True)]
        for name, value in reply.headers:
            if name.lower() == "set-cookie":
                # The session token is the credential being delivered.
                continue
            parts.append("%s: %s" % (name, value))
        self.bodies.append((where, "\n".join(parts), tuple(allow)))
        return reply

    def everything(self):
        self.store.flush_audit()
        files = {}
        for path in self.dir.rglob("*"):
            if path.is_file():
                try:
                    files[str(path.name)] = path.read_bytes().decode("utf-8", "replace")
                except OSError:
                    pass
        return {"logs": "\n".join(self.capture.lines),
                "audit": json.dumps(self.store.audit_entries(1000)),
                **{"file:" + n: t for n, t in files.items()}}

    def assert_clean(self, secrets, *, files_may_hold=()):
        found = self.everything()
        for label, secret in secrets.items():
            for where, text in found.items():
                if where.startswith("file:") and any(
                        where.endswith(f) for f in files_may_hold):
                    continue
                self.assertNotIn(secret, text,
                                 "the %s reached %s" % (label, where))
            for where, text, allow in self.bodies:
                if label in allow:
                    continue
                self.assertNotIn(secret, text,
                                 "the %s reached the reply to %s" % (label, where))


class ADaysSecretsTests(SweepBase):
    def sign_in(self, username, password):
        client = self.srv.app.test_client()
        reply = client.post("/api/auth/login", environ_base=REMOTE,
                            json={"username": username, "password": password})
        return client, self.seen("login " + username, reply)

    def test_nothing_secret_is_left_lying_about(self):
        c = CANARIES
        self.store.create("sweeper", c["user password"], omni_users.ROLE_USER,
                          Permissions(features=("av",)))
        sweeper = self.store.find_by_username("sweeper")
        self.store.set_password(self.admin.id, c["admin password"])

        # sign in, wrongly and then enough times to be throttled, then rightly
        for _ in range(12):
            self.sign_in("sweeper", c["wrong password"])
        _client, ok = self.sign_in("principal", c["admin password"])
        self.assertEqual(ok.status_code, 200)
        self.assertNotIn("password", json.dumps(ok.get_json()).lower().replace(
            "password_changed", ""))

        # administration over the network, as the administrator
        admin = self.session_for(self.store.find_by_username("principal"))
        self.seen("password change", self.post(
            "/api/admin/users/" + sweeper.id,
            {"action": "set_password", "password": c["new password"]}, admin))
        self.seen("direct enable", self.post(
            "/api/admin/users/%s/direct_access" % sweeper.id,
            {"action": "enable"}, admin), allow=("device credential",))
        token = self.store.direct_access_token(sweeper.id)
        self.assertTrue(token)
        # The link is the one secret the administrator is entitled to read, so it
        # is checked on its own below. Everywhere else it must be absent.
        self.seen("link open", self.srv.app.test_client().get(
            "/access/" + token, environ_base=REMOTE))
        self.seen("direct regenerate", self.post(
            "/api/admin/users/%s/direct_access" % sweeper.id,
            {"action": "regenerate"}, admin))
        new_token = self.store.direct_access_token(sweeper.id)
        self.seen("old link", self.srv.app.test_client().get(
            "/access/" + token, environ_base=REMOTE))

        # a backup, made and restored. The deployment holds a device credential, so
        # the backup carries one -- and nothing about the operation may repeat it.
        (self.dir / "config.json").write_text(json.dumps({
            "username": "admin", "password": c["device credential"],
            "fallback_password": c["device credential"] + "-fallback"}),
            encoding="utf-8")
        exported = self.srv.app.test_client().post(
            "/api/backup/export", json={"passphrase": c["backup passphrase"]})
        self.assertEqual(exported.status_code, 200)
        self.srv.app.test_client().post(
            "/api/backup/restore", content_type="multipart/form-data",
            data={"backup": (io.BytesIO(exported.data), "x.omnibak"),
                  "passphrase": c["backup passphrase"]})
        self.srv.app.test_client().post(
            "/api/backup/restore", content_type="multipart/form-data",
            data={"backup": (io.BytesIO(exported.data), "x.omnibak"),
                  "passphrase": c["wrong password"]})

        # an account deleted
        self.store.delete(self.store.find_by_username("sweeper").id, actor=self.admin)

        # The passwords are secrets everywhere, always.
        self.assert_clean({k: v for k, v in c.items()
                           if k in ("admin password", "user password", "new password",
                                    "wrong password", "backup passphrase")})
        # The device credential lives in config.json by design (the application
        # has to present it to the devices); it is not anywhere else.
        self.assert_clean({"device credential": c["device credential"]},
                          files_may_hold=("config.json",))
        # The backup file itself is the one place a passphrase-protected copy of
        # the accounts is meant to live; it is not in the data directory at all.
        # The link: in the one reply that gave it, and nowhere in the application's
        # own records. (It is stored as a digest plus a protected copy.)
        for label, secret in (("old link", token), ("new link", new_token)):
            found = self.everything()
            for where in ("logs", "audit"):
                self.assertNotIn(secret, found[where], "the %s reached %s" % (label, where))
            for where, text in found.items():
                if where.startswith("file:"):
                    self.assertNotIn(secret, text,
                                     "the %s is stored readable in %s" % (label, where))
            for where, text, _allow in self.bodies:
                if where in ("direct enable", "direct regenerate"):
                    continue
                if where == "link open":
                    # Opening a link redirects; the address bar must not keep it.
                    self.assertNotIn(secret, text,
                                     "the %s survives in the redirect" % label)
                    continue
                self.assertNotIn(secret, text,
                                 "the %s reached the reply to %s" % (label, where))

    def test_the_stored_password_is_a_hash_not_the_password(self):
        self.store.create("hashed", CANARIES["user password"], omni_users.ROLE_USER)
        self.store._persist()
        raw = self.store._path.read_text(encoding="utf-8")
        self.assertNotIn(CANARIES["user password"], raw)
        self.assertIn("scrypt", raw)

    def test_a_wrong_sign_in_names_no_secret_and_no_account_state(self):
        self.store.create("target", CANARIES["user password"], omni_users.ROLE_USER)
        _c, wrong = self.sign_in("target", CANARIES["wrong password"])
        _c, unknown = self.sign_in("nobody-at-all", CANARIES["wrong password"])
        self.assertEqual(wrong.status_code, unknown.status_code)
        self.assertEqual(wrong.get_json().get("error"), unknown.get_json().get("error"),
                         "the reply tells an unknown account from a wrong password")
        self.assert_clean({"wrong password": CANARIES["wrong password"]})

    def test_a_failed_restore_does_not_echo_what_was_uploaded(self):
        reply = self.seen("bad restore", self.srv.app.test_client().post(
            "/api/backup/restore", content_type="multipart/form-data",
            data={"backup": (io.BytesIO(b"x" * 64 + CANARIES["device credential"].encode()),
                             "b.omnibak"),
                  "passphrase": CANARIES["backup passphrase"]}))
        self.assertEqual(reply.status_code, 400)
        self.assert_clean({"device credential": CANARIES["device credential"],
                           "backup passphrase": CANARIES["backup passphrase"]})


if __name__ == "__main__":
    unittest.main()
