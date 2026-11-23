"""A Direct Access link when the address remote access serves changes.

The URL a person is handed contains the address remote access was serving. When
that address changes -- another adapter is chosen, or DHCP moves the computer --
a copy of the old URL points at nothing, and the NEW link has to be offered and
the old one not left looking current.

What is decided, and fenced here:

  * The token is NOT replaced because the host changed. It names an account and
    works at whatever address the server is listening on, so the new link is the
    same token on the new origin. Exactly one token stays live, and nobody is
    signed out. (Regenerating is still one explicit button, and the advice says
    when to use it: if the old address may now belong to another device.)
  * The change is derived from the current listener on every read, so it is seen
    without a rescan and cannot be missed by opening the page late.
  * The old URL is never rebuilt or returned: it carries the token, and there is
    no reason to produce a second copy of a credential.
  * An address that was never recorded is unknown, and unknown never warns.
  * Acknowledging records the address only; no session ends and no token moves.

The listener test at the end uses two real loopback addresses on one port: the
same token opens the panel at the new origin, and the old origin refuses the
connection because nothing listens there any more.

No address, hostname or token in this file is real.
"""
from __future__ import annotations

import http.client
import os
import socket
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

A = {"enabled": True, "host": "198.51.100.10", "port": 8443, "scheme": "https",
     "secure": True, "url": "https://198.51.100.10:8443/"}
B = {"enabled": True, "host": "198.51.100.77", "port": 8443, "scheme": "https",
     "secure": True, "url": "https://198.51.100.77:8443/"}
OFF = {"enabled": False, "host": None, "port": None, "url": None,
       "scheme": None, "secure": False}


class AddressChangeBase(unittest.TestCase):
    def setUp(self):
        import OmniMatrix_upgrade_server_v7_6y as srv
        self.srv = srv
        self.dir = Path(tempfile.mkdtemp(prefix="omni-directaddr-"))
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
        self.store.create("teacher", "teacher-password-1", omni_users.ROLE_USER,
                          omni_users.Permissions(features=("av",)))
        self.teacher = self.store.find_by_username("teacher")
        self.current = dict(A)
        self.describe_patch = mock.patch.object(
            srv._remote_listener, "describe", lambda: dict(self.current))
        self.describe_patch.start()
        self.addCleanup(self.describe_patch.stop)
        self.client = srv.app.test_client()
        self.url = f"/api/admin/users/{self.teacher.id}/direct_access"

    def act(self, action):
        r = self.client.post(self.url, json={"action": action})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        return r.get_json()

    def read(self):
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 200)
        return r.get_json()

    def token_of(self, link):
        return link.rsplit("/access/", 1)[1]

    def state(self):
        return self.client.get("/api/remote_access").get_json()


class AddressChangeTests(AddressChangeBase):
    def test_a_link_handed_out_at_the_current_address_raises_nothing(self):
        made = self.act("enable")
        self.assertTrue(made["link"].startswith("https://198.51.100.10:8443/access/"))
        self.assertEqual(made["issued_origin"], "https://198.51.100.10:8443")
        self.assertIs(made["address_changed"], False)
        self.assertEqual(self.state()["direct_links_stale"], [])

    def test_a_change_of_address_is_said_and_the_new_link_is_offered(self):
        first = self.act("enable")
        self.current = dict(B)
        seen = self.read()
        self.assertIs(seen["address_changed"], True)
        self.assertEqual(seen["issued_origin"], "https://198.51.100.10:8443")
        self.assertEqual(seen["link_origin"], "https://198.51.100.77:8443")
        self.assertTrue(seen["link"].startswith("https://198.51.100.77:8443/access/"),
                        "the link shown must be for the address now being served")
        self.assertEqual(self.token_of(seen["link"]), self.token_of(first["link"]),
                         "the host changed, not the secret: the same token, a new address")

    def test_the_old_url_is_never_returned(self):
        first = self.act("enable")
        self.current = dict(B)
        text = self.client.get(self.url).get_data(as_text=True)
        self.assertNotIn("198.51.100.10:8443/access", text)
        self.assertEqual(text.count(self.token_of(first["link"])), 1,
                         "exactly one copy of the credential is returned")

    def test_the_notice_is_not_cleared_by_looking_at_it(self):
        self.act("enable")
        self.current = dict(B)
        self.assertIs(self.read()["address_changed"], True)
        self.assertIs(self.read()["address_changed"], True,
                      "reading the link must not be what clears the condition")
        self.assertEqual(self.state()["direct_links_stale"], ["teacher"])

    def test_settings_names_the_accounts_whose_link_is_out_of_date_and_nothing_else(self):
        self.act("enable")
        self.current = dict(B)
        stale = self.state()["direct_links_stale"]
        self.assertEqual(stale, ["teacher"])
        self.assertNotIn("/access/", str(self.state()), "no link or token in the state")

    def test_the_change_is_seen_without_a_rescan_by_deriving_it_every_time(self):
        self.act("enable")
        self.assertEqual(self.state()["direct_links_stale"], [])
        self.current = dict(B)
        self.assertEqual(self.state()["direct_links_stale"], ["teacher"])
        self.current = dict(A)
        self.assertEqual(self.state()["direct_links_stale"], [],
                         "moving back is current again; nothing was written meanwhile")

    def test_a_change_of_scheme_or_port_counts_as_a_change_of_address(self):
        self.act("enable")
        for change in ({"scheme": "http"}, {"port": 8444}):
            with self.subTest(change=change):
                self.current = dict(A, **change)
                self.assertIs(self.read()["address_changed"], True)

    def test_acknowledging_records_the_address_and_moves_nothing_else(self):
        first = self.act("enable")
        self.current = dict(B)
        session = self.store.open_session(self.teacher.id, omni_users.BY_DIRECT_LINK)
        token_before = self.store.direct_access_token(self.teacher.id)
        done = self.act("acknowledge_address")
        self.assertIs(done["address_changed"], False)
        self.assertEqual(done["issued_origin"], "https://198.51.100.77:8443")
        self.assertEqual(self.store.direct_access_token(self.teacher.id), token_before,
                         "the token must not move because the host did")
        self.assertEqual(self.token_of(done["link"]), self.token_of(first["link"]))
        account, _live = self.store.resolve_session(session.token)
        self.assertIsNotNone(account, "acknowledging must not sign anybody out")
        self.assertEqual(self.state()["direct_links_stale"], [])

    def test_acknowledging_is_audited_without_a_token(self):
        self.act("enable")
        self.current = dict(B)
        self.act("acknowledge_address")
        entries = [e for e in self.store.audit_entries(limit=50)
                   if (e.get("event") or e.get("action")) == "direct_access.address_acknowledged"]
        self.assertEqual(len(entries), 1)
        self.assertNotIn(self.teacher.direct_access.digest, str(entries))

    def test_regenerating_after_a_change_replaces_the_token_and_ends_the_old_one(self):
        first = self.act("enable")
        old = self.token_of(first["link"])
        self.current = dict(B)
        fresh = self.act("regenerate")
        self.assertNotEqual(self.token_of(fresh["link"]), old)
        self.assertIs(fresh["address_changed"], False)
        self.assertEqual(fresh["issued_origin"], "https://198.51.100.77:8443")
        self.assertIsNone(self.store.resolve_direct_token(old))

    def test_a_change_of_address_alone_never_revokes_or_regenerates(self):
        first = self.act("enable")
        self.current = dict(B)
        self.read()
        self.state()
        self.assertEqual(self.token_of(self.read()["link"]), self.token_of(first["link"]))
        self.assertIsNotNone(self.store.resolve_direct_token(self.token_of(first["link"])))

    def test_with_remote_access_off_there_is_nothing_to_compare_and_nothing_is_claimed(self):
        self.act("enable")
        self.current = dict(OFF)
        seen = self.read()
        self.assertEqual(seen["link"], "")
        self.assertIs(seen["address_changed"], False)
        self.assertEqual(self.state()["direct_links_stale"], [])
        self.assertIn("Remote access is off", seen["note"])

    def test_turned_back_on_at_another_address_the_difference_appears(self):
        self.act("enable")
        self.current = dict(OFF)
        self.assertIs(self.read()["address_changed"], False)
        self.current = dict(B)
        self.assertIs(self.read()["address_changed"], True)

    def test_an_address_that_was_never_recorded_is_unknown_and_does_not_warn(self):
        self.act("enable")
        self.teacher.direct_access.issued_origin = ""          # a record from before this was kept
        self.current = dict(B)
        seen = self.read()
        self.assertIs(seen["address_changed"], False)
        self.assertEqual(self.state()["direct_links_stale"], [])
        # Looking at it learns the baseline; it does not overwrite a recorded one.
        self.assertEqual(self.teacher.direct_access.issued_origin, "https://198.51.100.77:8443")
        self.current = dict(A)
        self.assertIs(self.read()["address_changed"], True,
                      "once recorded, a later change is a change")

    def test_learning_an_origin_never_overwrites_one_that_was_recorded(self):
        """The API only learns when nothing is recorded, so this second line of defence is
        reached directly: only an acknowledgement may replace a recorded address."""
        self.act("enable")
        elsewhere = "https://198.51.100.77:8443"
        self.assertFalse(self.store.note_direct_origin(self.teacher.id, elsewhere))
        self.assertEqual(self.teacher.direct_access.issued_origin, "https://198.51.100.10:8443")
        self.assertTrue(self.store.note_direct_origin(self.teacher.id, elsewhere, acknowledge=True))
        self.assertEqual(self.teacher.direct_access.issued_origin, elsewhere)
        self.assertFalse(self.store.note_direct_origin(self.teacher.id, ""), "an empty origin records nothing")

    def test_the_origin_survives_a_restart(self):
        self.act("enable")
        reloaded = omni_users.UserStore(self.store._path, self.store._audit_path)
        account = reloaded.find_by_username("teacher")
        self.assertEqual(account.direct_access.issued_origin, "https://198.51.100.10:8443")

    def test_what_the_public_projection_says_is_unchanged(self):
        self.act("enable")
        self.assertEqual(set(self.teacher.public()["direct_access"]),
                         {"enabled", "protection", "created_at"})

    def test_the_users_own_link_endpoint_does_not_grow_address_fields(self):
        """The administrator's view carries the address history; the account's own does not."""
        source = (ROOT / "omni_remote_api.py").read_text(encoding="utf-8")
        body = source[source.index("def api_user_direct_access"):]
        body = body[:body.index("@app.route")]
        self.assertNotIn("_admin_direct_payload", body)
        self.assertNotIn("issued_origin", body)

    def test_an_administrator_still_cannot_be_given_a_link(self):
        admin = self.store.find_by_username("principal")
        r = self.client.post(f"/api/admin/users/{admin.id}/direct_access",
                             json={"action": "enable"})
        self.assertEqual(r.status_code, 400)


ADAPTER_ID = "guid:aaaa1111-2222-3333-4444-555566667777"


def adapter(address):
    return {"id": ADAPTER_ID, "id_from": "guid", "name": "Ethernet 3", "address": address,
            "netmask": "255.255.255.0", "prefix": 24, "cidr": "198.51.100.0/24", "scan": "",
            "status": "Up", "up": True, "virtual": False, "label": "Ethernet 3"}


class TheComputersAddressMovesWhileOnTests(AddressChangeBase):
    """The adapter's CURRENT address versus the one the listener is bound to.

    Found by hand: the computer's address changed during a session and Settings went on saying ON,
    securely, at the old address. It is derived from the live interface list on every state read;
    nothing is polled, nothing is rebound, nothing is reissued, and no interface is touched.
    """

    def setUp(self):
        super().setUp()
        self.live = [adapter("198.51.100.10")]
        for target, value in (("omni_adapters.enumerate_adapters", lambda *a, **k: [dict(x) for x in self.live]),):
            patcher = mock.patch(target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        saved = self.dir / "remote_access.json"
        saved.write_text('{"enabled": true, "adapter_id": "%s", "address": "198.51.100.10", '
                         '"port": 8443, "transport": "https"}' % ADAPTER_ID, encoding="utf-8")
        patcher = mock.patch.object(self.srv, "REMOTE_ACCESS_FILE", saved)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.saved_file = saved

    def moved(self):
        return self.state()["address_moved"]

    def test_the_same_address_says_nothing(self):
        self.assertIsNone(self.moved())

    def test_a_new_address_on_the_chosen_adapter_is_said_in_plain_words(self):
        self.live = [adapter("198.51.100.77")]
        moved = self.moved()
        self.assertEqual((moved["from"], moved["to"]), ("198.51.100.10", "198.51.100.77"))
        self.assertEqual(moved["message"],
                         "This computer's address changed from 198.51.100.10 to 198.51.100.77. "
                         "Remote Access is still listening on 198.51.100.10. Turn Remote Access "
                         "off and on to move it.")

    def test_it_is_derived_on_every_read_so_moving_back_clears_it(self):
        self.live = [adapter("198.51.100.77")]
        self.assertIsNotNone(self.moved())
        self.live = [adapter("198.51.100.10")]
        self.assertIsNone(self.moved())

    def test_an_adapter_that_still_carries_the_bound_address_has_not_moved(self):
        self.live = [adapter("198.51.100.10"), adapter("198.51.100.77")]
        self.assertIsNone(self.moved())

    def test_an_adapter_that_is_gone_is_not_a_move(self):
        self.live = []
        self.assertIsNone(self.moved(), "no claim is made about an adapter that is not there")

    def test_with_remote_access_off_there_is_nothing_to_say(self):
        self.live = [adapter("198.51.100.77")]
        self.current = dict(OFF)
        self.assertIsNone(self.moved())

    def test_saying_so_changes_nothing(self):
        """No rebind, no certificate, no preference rewritten, no token moved."""
        self.act("enable")
        self.live = [adapter("198.51.100.77")]
        before = self.saved_file.read_text(encoding="utf-8")
        token = self.store.direct_access_token(self.teacher.id)
        with mock.patch.object(self.srv._remote_listener, "start") as start, \
                mock.patch.object(self.srv._remote_listener, "stop") as stop, \
                mock.patch("omni_tls.generate_self_signed") as generate:
            self.assertIsNotNone(self.moved())
            self.read()
        start.assert_not_called()
        stop.assert_not_called()
        generate.assert_not_called()
        self.assertEqual(self.saved_file.read_text(encoding="utf-8"), before)
        self.assertEqual(self.store.direct_access_token(self.teacher.id), token)

    def test_the_direct_link_is_not_presented_as_current_at_the_new_address(self):
        first = self.act("enable")
        self.live = [adapter("198.51.100.77")]
        seen = self.read()
        self.assertEqual(seen["address_moved"]["to"], "198.51.100.77")
        self.assertTrue(seen["link"].startswith("https://198.51.100.10:8443/access/"),
                        "the link is where the listener actually is, and says so beside it")
        self.assertNotIn("198.51.100.77", seen["link"])
        self.assertEqual(seen["link_origin"], "https://198.51.100.10:8443")
        self.assertIs(seen["address_changed"], False, "the served address has not changed yet")
        self.assertEqual(self.token_of(seen["link"]), self.token_of(first["link"]))

    def test_turning_it_back_on_meets_the_certificate_mismatch_refusal_not_a_silent_reissue(self):
        """Off, then on at the new address with a certificate for the old one: refused with the
        reason the page turns into Cancel / Change to HTTP / Regenerate. The refusal makes nothing."""
        omni_tls = __import__("omni_tls")
        if not omni_tls.generation_available():
            self.skipTest("this build cannot generate a certificate")
        cert, key = self.dir / "c.pem", self.dir / "k.pem"
        omni_tls.generate_self_signed("198.51.100.10", cert, key)
        before = cert.read_bytes()
        self.live = [adapter("198.51.100.77")]
        with mock.patch.object(self.srv, "REMOTE_CERT_FILE", cert), \
                mock.patch.object(self.srv, "REMOTE_KEY_FILE", key), \
                mock.patch.object(self.srv._remote_listener, "start") as start:
            r = self.client.post("/api/remote_access", json={
                "enabled": True, "address": "198.51.100.77", "port": 8443, "transport": "https",
                "adapter_id": ADAPTER_ID, "accept_insecure": False})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.get_json()["reason"], "certificate_mismatch")
        start.assert_not_called()
        self.assertEqual(cert.read_bytes(), before, "nothing was reissued")


class TheListenerMovesTests(AddressChangeBase):
    """Two real loopback addresses, one port: the token follows, the old address goes dark."""

    OLD, NEW = "127.98.0.21", "127.98.0.22"

    def setUp(self):
        super().setUp()
        patcher = mock.patch.object(omni_remote, "is_usable_remote_address",
                                    lambda host: str(host).startswith("127.98."))
        patcher.start()
        self.addCleanup(patcher.stop)
        probe = socket.socket()
        probe.bind((self.OLD, 0))
        self.port = probe.getsockname()[1]
        probe.close()
        # The application's own remote listener, because the gate asks that one
        # whether remote access is on. This test reads its real state.
        self.describe_patch.stop()
        self.listener = self.srv._remote_listener
        self.addCleanup(self.listener.stop)

    def open_panel(self, host, link_path):
        conn = http.client.HTTPConnection(host, self.port, timeout=5)
        try:
            conn.request("GET", link_path)
            return conn.getresponse().status
        finally:
            conn.close()

    def test_the_same_token_opens_the_panel_at_the_new_origin_and_the_old_one_is_gone(self):
        self.assertTrue(self.listener.start(self.OLD, self.port)["ok"])
        first = self.act("enable")
        self.assertTrue(first["link"].startswith(f"http://{self.OLD}:{self.port}/access/"))
        token = self.token_of(first["link"])
        self.assertEqual(self.open_panel(self.OLD, f"/access/{token}"), 200)

        self.listener.stop()
        self.assertTrue(self.listener.start(self.NEW, self.port)["ok"])
        seen = self.read()
        self.assertIs(seen["address_changed"], True)
        self.assertTrue(seen["link"].startswith(f"http://{self.NEW}:{self.port}/access/"))
        self.assertEqual(self.token_of(seen["link"]), token)
        self.assertEqual(self.open_panel(self.NEW, f"/access/{token}"), 200,
                         "the same token must open the panel at the new address")
        with self.assertRaises(OSError):
            self.open_panel(self.OLD, f"/access/{token}")


if __name__ == "__main__":
    unittest.main()
