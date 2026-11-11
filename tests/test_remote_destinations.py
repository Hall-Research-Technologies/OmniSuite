"""A remote browser must not be able to aim a stored device credential at a host.

The server attaches a device password to nearly every connection it makes, and
about thirty-five handlers take the address from the request. A stolen
administrator session could therefore turn the application into a credential
forwarding proxy: POST /api/login with the address of a machine it owns, and the
configured password arrives there.

Control needs an address the server already holds; discovery may probe an
unseen address only if it is not on the public Internet. These tests drive the
real gate, with the transport replaced by a recorder that is also the assertion:
a refused request must make no connection at all.
"""
from __future__ import annotations

import copy
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

import omni_destinations                                  # noqa: E402
import omni_remote                                        # noqa: E402
import omni_access                                   # noqa: E402
import omni_users                                         # noqa: E402

KNOWN = "198.51.100.7"
OTHER = "198.51.100.8"
UNIT = {"ip": KNOWN, "mac": "B8:98:B0:00:00:01", "model": "AT-OMNI-111",
        "password": "Synthetic-Device-Pw-0002"}


class Base(unittest.TestCase):

    def setUp(self):
        import OmniMatrix_upgrade_server_v7_6y as srv
        self.srv = srv
        self.dir = Path(tempfile.mkdtemp(prefix="omni-dest-"))
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
        self.session = self.store.open_session(
            self.store.find_by_username("principal").id)

        self.inventory = [copy.deepcopy(UNIT)]
        self.calls = []

        def record(url, payload, timeout=0, *a, **k):
            self.calls.append(url)
            return {"config": {}, "error": None}

        def probe(ip, ports, timeout=0.4):
            self.calls.append("tcp:" + str(ip))
            return False

        def outcome(ip, ports, timeout=0.4):
            self.calls.append("tcp:" + str(ip))
            return "closed"

        for patcher in (
                mock.patch.object(srv, "_load_cache",
                                  side_effect=lambda *a, **k: copy.deepcopy(self.inventory)),
                mock.patch.object(srv, "_ws_send_recv", side_effect=record),
                mock.patch.object(srv, "_tcp_probe", side_effect=probe),
                mock.patch.object(srv, "_tcp_probe_outcome", side_effect=outcome),
                mock.patch.object(type(srv._remote_listener), "running",
                                  new_callable=mock.PropertyMock,
                                  return_value=True)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def _client(self, remote):
        client = self.srv.app.test_client()
        env, headers = {}, {}
        if remote:
            env[omni_remote.LISTENER_KEY] = omni_remote.REMOTE
            client.set_cookie("omnisuite_session", self.session.token,
                              domain="localhost")
            headers["X-OmniSuite-CSRF"] = self.session.csrf_token
        return client, headers, env

    def assertNoConnections(self, why):
        """A refusal makes no connection; if one is recorded, say whose it might be.

        The recorder is installed on module globals, so work another test left
        running resolves to it. Naming the live non-test threads turns "two
        attempts to the known address" into a diagnosis instead of a puzzle.
        """
        if self.calls:
            strays = [t.name for t in threading.enumerate()
                      if t is not threading.current_thread()
                      and t is not threading.main_thread() and t.is_alive()]
            self.fail("%s: %r were recorded. Threads still alive that this test did "
                      "not start: %r (a leftover background sweep would explain it)"
                      % (why, self.calls, strays))

    def post(self, path, body, remote=True):
        client, headers, env = self._client(remote)
        return client.post(path, json=body, headers=headers, environ_base=env)

    def get(self, path, remote=True):
        client, headers, env = self._client(remote)
        return client.get(path, headers=headers, environ_base=env)


class ControlNeedsAKnownDeviceTests(Base):

    def test_a_known_device_is_reached(self):
        response = self.post("/api/lldp", {"ip": KNOWN})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertTrue(any(KNOWN in call for call in self.calls), self.calls)

    def test_an_address_the_server_has_never_seen_is_not(self):
        for path, body in (
                ("/api/lldp", {"ip": OTHER}),
                ("/api/login", {"ip": OTHER, "password": "x"}),
                ("/api/login", {"ip": OTHER}),
                ("/api/hostname", {"ip": OTHER, "hostname": "a"}),
                ("/api/codec", {"ip": OTHER, "system_mode": "x"}),
                ("/api/blink", {"ip": OTHER}),
                ("/api/poll", {"ip": OTHER}),
                ("/api/set_thumbnail", {"ip": OTHER, "enable": True}),
                ("/api/ntp_profile", {"ips": [OTHER]}),
                ("/api/set_ntp", {"scope": "selected", "ips": [OTHER], "timezone": "UTC"}),
                ("/api/slate_status", {"ips": [OTHER]}),
                ("/api/poll_encoders", {"encoders": [OTHER]}),
                ("/api/poll_decoders", {"decoders": [OTHER]}),
                ("/api/route", {"decoder": OTHER, "encoder": KNOWN}),
                ("/api/route", {"decoder": KNOWN, "encoder": OTHER}),
                ("/api/unroute", {"decoder": OTHER}),
                ("/api/upgrade", {"file": "f.bin", "targets": [OTHER]}),
                ("/api/get_thumbnail_status", {"ip": OTHER}),
                ("/api/multiview/switch", {"decoder": OTHER}),
        ):
            with self.subTest(path=path, body=body):
                del self.calls[:]
                response = self.post(path, body)
                self.assertEqual(response.status_code, 404, response.get_data(as_text=True))
                self.assertEqual(response.get_json()["reason"], "unknown_device")
                self.assertNoConnections("a refusal must make no connection")

    def test_a_list_with_one_stranger_is_refused_whole(self):
        response = self.post("/api/ntp_profile", {"ips": [KNOWN, OTHER]})
        self.assertEqual(response.status_code, 404)
        self.assertNoConnections("the known address must not be contacted either")

    def test_a_query_string_list_is_judged_the_same_way(self):
        response = self.get("/api/ts_export?devices=%s,%s" % (KNOWN, OTHER))
        self.assertEqual(response.status_code, 404)
        self.assertNoConnections("a refusal must make no connection")

    def test_malformed_and_hostile_addresses_are_400_not_404(self):
        for bad in ("evil.example", "%s@evil.example" % KNOWN, "%s:80/x#" % KNOWN,
                    "[::1]", "::1", "ws://evil.example/", "0x7f000001",
                    "198.51.100.7/24", "198.51.100.256", "198.51.100"):
            with self.subTest(bad=bad):
                response = self.post("/api/login", {"ip": bad, "password": "x"})
                self.assertEqual(response.status_code, 400, bad)
                self.assertEqual(response.get_json()["reason"], "invalid_address")
                self.assertNoConnections("a refusal must make no connection")

    def test_loopback_and_friends_are_refused_unless_actually_recorded(self):
        for odd in ("127.0.0.1", "169.254.1.1", "224.0.0.1", "0.0.0.0",
                    "255.255.255.255", "8.8.8.8"):
            with self.subTest(odd=odd):
                response = self.post("/api/lldp", {"ip": odd})
                self.assertEqual(response.status_code, 404)
                self.assertNoConnections("a refusal must make no connection")

    def test_a_device_that_moved_follows_the_inventory_not_the_old_address(self):
        """Rediscovery updates the record in place; the old address stops working."""
        self.inventory[0]["ip"] = OTHER
        self.assertEqual(self.post("/api/lldp", {"ip": OTHER}).status_code, 200)
        del self.calls[:]
        response = self.post("/api/lldp", {"ip": KNOWN})
        self.assertEqual(response.status_code, 404)
        self.assertNoConnections("a refusal must make no connection")

    def test_an_emptied_inventory_reaches_nothing(self):
        self.inventory[:] = []
        self.assertEqual(self.post("/api/lldp", {"ip": KNOWN}).status_code, 404)
        self.assertNoConnections("a refusal must make no connection")

    def test_an_identity_is_not_an_address_and_is_left_to_the_handler(self):
        response = self.post("/api/route", {"decoder": "B8:98:B0:00:00:09",
                                            "encoder": "B8:98:B0:00:00:01"})
        self.assertNotIn((response.get_json() or {}).get("reason"),
                         ("unknown_device", "invalid_address"))

    def test_the_local_operator_is_unchanged(self):
        response = self.post("/api/lldp", {"ip": OTHER}, remote=False)
        self.assertNotEqual(response.status_code, 404)
        self.assertTrue(any(OTHER in call for call in self.calls), self.calls)

    def test_a_refusal_is_audited(self):
        self.post("/api/login", {"ip": OTHER})
        events = [e for e in self.store._audit
                  if (e.get("event") or e.get("action")) == "access.denied"]
        self.assertTrue(any((e.get("detail") or {}).get("reason") == "unknown_device"
                            for e in events), list(self.store._audit)[-3:])


class DiscoveryMayProbeOnlyNonPublicAddressesTests(Base):

    def test_a_remote_scan_of_the_internet_is_refused_before_any_probe(self):
        for targets in ("8.8.8.8", "1.1.1.0/30", "203.0.113.1,8.8.8.8",
                        "8.8.8.1-3", "127.0.0.1", "169.254.1.1", "224.0.0.1",
                        "0.0.0.0", "255.255.255.255"):
            with self.subTest(targets=targets):
                response = self.post("/api/scan", {"targets": targets})
                self.assertEqual(response.status_code, 400, targets)
                self.assertEqual(response.get_json()["reason"], "address_out_of_scope")
                self.assertNoConnections("a refusal must make no connection")

    def test_a_site_scan_is_still_allowed(self):
        for targets in ("198.51.100.5", "198.51.100.64/30", "203.0.113.1-3", "192.0.2.50"):
            with self.subTest(targets=targets):
                del self.calls[:]
                response = self.post("/api/scan", {"targets": targets})
                self.assertNotIn((response.get_json() or {}).get("reason"),
                                 ("address_out_of_scope", "invalid_address"),
                                 response.get_data(as_text=True))
                self.assertTrue(self.calls, "an allowed scan probes")

    def test_an_oversized_expression_is_refused_without_being_expanded(self):
        response = self.post("/api/scan", {"targets": "10.0.0.0/8"})
        self.assertEqual(response.status_code, 400)
        self.assertNoConnections("a refusal must make no connection")

    def test_unrecognised_text_names_no_address_and_is_the_handlers_to_refuse(self):
        response = self.post("/api/scan", {"targets": "evil.example"})
        self.assertEqual(response.status_code, 400)
        self.assertNoConnections("a refusal must make no connection")

    def test_the_probe_routes_follow_the_same_scope(self):
        for path in ("/api/tcp_probe", "/api/ping", "/api/usb_extenders/discover_ip"):
            with self.subTest(path=path):
                self.assertEqual(self.post(path, {"ip": "8.8.8.8"}).status_code, 400)
                self.assertEqual(self.post(path, {"ip": "127.0.0.1"}).status_code, 400)
        self.assertEqual(self.post("/api/usb_extenders/discover",
                                   {"targets": "8.8.8.8"}).status_code, 400)
        self.assertNoConnections("a refusal must make no connection")


class PredicateTests(unittest.TestCase):
    """The rule on its own, so the gate is not the only thing holding it."""

    @staticmethod
    def expand(text):
        import OmniMatrix_upgrade_server_v7_6y as srv
        return srv._expand_targets(text)

    def run_check(self, rule, payload, known=(KNOWN,)):
        omni_destinations.check(rule, payload, lambda: set(known), self.expand)

    def refused(self, rule, payload, status, reason):
        with self.assertRaises(omni_destinations.Refusal) as caught:
            self.run_check(rule, payload)
        self.assertEqual((caught.exception.status, caught.exception.reason),
                         (status, reason))

    def test_control_known_unknown_malformed(self):
        self.run_check("/api/lldp", {"ip": KNOWN})
        self.refused("/api/lldp", {"ip": OTHER}, 404, "unknown_device")
        self.refused("/api/lldp", {"ip": "evil.example"}, 400, "invalid_address")

    def test_a_request_naming_no_address_does_not_read_the_inventory(self):
        def boom():
            raise AssertionError("inventory read for nothing")
        omni_destinations.check("/api/lldp", {"scope": "all"}, boom, lambda t: [])

    def test_a_known_address_may_be_rescanned_whatever_it_is(self):
        self.run_check("/api/scan", {"targets": "8.8.8.8"}, known=("8.8.8.8",))

    def test_the_scope_rule_by_address_without_any_probe(self):
        """Private and shared ranges are in scope; the public Internet and specials are not."""
        import ipaddress
        scope = omni_destinations.discovery_scope_ok
        for text in ("10.0.0.5", "172.16.4.9", "192.168.1.77", "100.64.0.9", "198.18.0.5",
                     "198.51.100.5", "203.0.113.9", "192.0.2.9"):
            self.assertTrue(scope(ipaddress.IPv4Address(text)), text)
        for text in ("8.8.8.8", "1.1.1.1", "127.0.0.1", "169.254.1.1", "224.0.0.1",
                     "0.0.0.0", "255.255.255.255", "240.0.0.1"):
            self.assertFalse(scope(ipaddress.IPv4Address(text)), text)

    def test_ipv6_is_refused(self):
        self.refused("/api/lldp", {"ip": "2001:db8::1"}, 400, "invalid_address")
        self.refused("/api/ping", {"ip": "fe80::1"}, 400, "invalid_address")


class AddressKeysCoverEveryHandlerTests(unittest.TestCase):
    """An endpoint added tomorrow that takes an address must be covered today."""

    #: Keys that look address-like and are not a destination, with why.
    NOT_A_DESTINATION = {
        "address": "the new address to configure on a device named by MAC",
        "hostname": "a name to set on a device already named by address",
        "interface_ip": "the local adapter to scan from, never contacted",
        "scan_targets": "a remembered expression; scanning it is /api/scan",
        "source": "a routing identity, resolved and authorized server-side",
        "lex": "a USB identity (MAC), resolved against recorded state",
        "rex": "a USB identity (MAC), resolved against recorded state",
        "mac": "an identity, resolved against recorded state",
        "target": "a mode word (all/encoders/decoders), not an address",
        "video_wall_unit": "a position number",
    }

    READ = re.compile(
        r"""(?:data|payload|body|request\.args|args)\.get\(\s*["']([A-Za-z_]+)["']""")

    def keys(self):
        source = (ROOT / "OmniMatrix_upgrade_server_v7_6y.py").read_text(encoding="utf-8")
        return set(self.READ.findall(source))

    def test_every_address_like_body_key_is_judged_or_explained(self):
        looks = {k for k in self.keys() if re.search(
            r"ip|decoder|encoder|target|host|addr|device|unit|source|lex|rex|mac",
            k, re.I)}
        unaccounted = sorted(looks - omni_destinations.ADDRESS_KEYS
                             - set(self.NOT_A_DESTINATION))
        self.assertEqual(unaccounted, [],
                         "a handler reads a body key that may name a device and "
                         "omni_destinations does not know it")


class NoRedirectCarriesACredentialTests(unittest.TestCase):
    """A device that answers with a redirect must not be followed.

    The password travels in the first frame after the handshake, so following a
    Location header sends it wherever the answer pointed.
    """

    def test_a_redirect_is_not_followed(self):
        import OmniMatrix_upgrade_server_v7_6y as srv
        target = socket.socket()
        target.bind(("127.0.0.1", 0))
        target.listen(1)
        target.settimeout(2.0)
        reached = []

        def accept_target():
            try:
                conn, _ = target.accept()
                reached.append(True)
                conn.close()
            except OSError:
                pass

        front = socket.socket()
        front.bind(("127.0.0.1", 0))
        front.listen(1)
        front.settimeout(3)

        def redirect():
            try:
                conn, _ = front.accept()
                conn.recv(4096)
                conn.sendall(("HTTP/1.1 302 Found\r\nLocation: ws://127.0.0.1:%d/\r\n"
                              "Content-Length: 0\r\n\r\n"
                              % target.getsockname()[1]).encode())
                conn.close()
            except OSError:
                pass

        threads = [threading.Thread(target=accept_target, daemon=True),
                   threading.Thread(target=redirect, daemon=True)]
        for t in threads:
            t.start()
        try:
            try:
                srv._ws_send_recv("ws://127.0.0.1:%d/" % front.getsockname()[1],
                                  {"password": "secret"}, 2.0)
            except Exception:
                pass
            for t in threads:
                t.join(3)
        finally:
            target.close()
            front.close()
        self.assertEqual(reached, [], "the redirect target was connected to")

    def test_every_websocket_connection_refuses_redirects(self):
        pattern = re.compile(r"(?<![\w.])(?:websocket\.)?create_connection\([^\n]*")
        for name in ("OmniMatrix_upgrade_server_v7_6y.py", "omni_matrix.py"):
            text = (ROOT / name).read_text(encoding="utf-8")
            calls = [c for c in pattern.findall(text)
                     if not re.match(r"socket\.create_connection", c)]
            self.assertTrue(calls, name)
            for call in calls:
                self.assertIn("redirect_limit=0", call, name + ": " + call)




if __name__ == "__main__":
    unittest.main()
