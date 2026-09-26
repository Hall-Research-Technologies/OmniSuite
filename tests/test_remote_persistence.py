"""Two things this checkpoint changed, and what must remain true of them.

The administrative state is no longer reachable by a restricted account, and
remote access now survives a restart -- which is a convenience that could
quietly expose a machine, so most of these tests are about the cases where it
must refuse to come back.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("OMNI_SMOKE", "1")

import omni_access                                   # noqa: E402
import omni_gate                                     # noqa: E402
import omni_remote                                   # noqa: E402
import omni_users                                    # noqa: E402
from omni_access import Access                       # noqa: E402
from omni_users import Permissions, UserStore        # noqa: E402

SOURCE = "B8:98:B0:00:00:01"
DISPLAY = "B8:98:B0:00:00:11"


def remote_env(session=None, csrf=True):
    env = {omni_remote.LISTENER_KEY: omni_remote.REMOTE}
    if session:
        env["HTTP_COOKIE"] = "omnisuite_session=" + session.token
        if csrf:
            env["HTTP_X_OMNISUITE_CSRF"] = session.csrf_token
    return env


class AdministrativeStateIsClosedTests(unittest.TestCase):
    """Authorised to call is not authorised to see.

    Each of these hands back the administrative object in full. Measured
    before closing them: /api/state 12,087 bytes carrying serials, multicast,
    video-wall geometry, HDCP and firmware; /api/usb_state 12,938 with MACs and
    USB firmware; the Multiview listings 1,350 and 1,757 with MACs.
    """

    #: Every endpoint a restricted account could previously reach that returns
    #: administrative detail.
    ADMINISTRATIVE = (
        "/api/state",
        "/api/usb_state",
        "/api/multiview/state",
        "/api/multiview/decoders",
        "/api/multiview/sources",
        "/api/multiview/preview",
    )

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="omni-close-"))
        self.store = UserStore(self.dir / "users.json")
        # Granted everything a User can be granted, so nothing below passes
        # merely because a capability was missing.
        self.store.create("teacher", "teacher-password-1", omni_users.ROLE_USER,
                          Permissions(features=("av", "usb", "multiview"),
                                      sources=(SOURCE,), destinations=(DISPLAY,)))
        self.teacher = self.store.find_by_username("teacher")
        self.session = self.store.open_session(self.teacher.id)

        self.store.create("admin", "admin-password-1", omni_users.ROLE_ADMIN)
        self.admin = self.store.find_by_username("admin")
        self.admin_session = self.store.open_session(self.admin.id)

    def decide(self, rule, session, method="GET"):
        return omni_gate.evaluate(rule, method, remote_env(session), {},
                                  self.store)

    def test_a_user_with_every_capability_cannot_read_administrative_state(self):
        for rule in self.ADMINISTRATIVE:
            with self.subTest(rule=rule):
                decision = self.decide(rule, self.session)
                self.assertFalse(
                    decision.allowed,
                    f"{rule} returns the administrative object and must not be "
                    "reachable by a restricted account")
                self.assertEqual(decision.reason, "not_admin")

    def test_an_administrator_still_reads_them(self):
        """Closing the hole must not break the application it was in."""
        for rule in self.ADMINISTRATIVE:
            with self.subTest(rule=rule):
                self.assertTrue(self.decide(rule, self.admin_session).allowed)

    def test_the_local_operator_still_reads_them(self):
        local = {omni_remote.LISTENER_KEY: omni_remote.LOCAL}
        for rule in self.ADMINISTRATIVE:
            with self.subTest(rule=rule):
                self.assertTrue(
                    omni_gate.evaluate(rule, "GET", local, {}, self.store).allowed)

    def test_the_legacy_route_endpoint_is_no_longer_user_reachable(self):
        """/api/route takes addresses, which a restricted account never learns.

        It was User-classified and effectively unusable to one -- permissions
        are keyed by identity, so an address never matched and every request
        failed closed. Leaving a route in that state invites somebody to "fix"
        it by loosening the check.
        """
        decision = self.decide("/api/route", self.session, "POST")
        self.assertFalse(decision.allowed)
        self.assertIs(omni_access.classify("/api/route")[0], Access.ADMIN)

    def test_the_user_surface_is_only_endpoints_written_for_a_user(self):
        """Six now, all under /api/user/, all projected.

        Phases G, H and I added USB and Multiview operation the same way the
        A/V route was added: a narrow endpoint that authorises by identity and
        delegates to the verified engine, answering with named fields. The
        administrative endpoints they delegate to stayed administrative.
        """
        user_routes = {rule for rule, (access, _f) in omni_access.ROUTES.items()
                       if access is Access.USER}
        self.assertEqual(user_routes, {
            "/api/user/route",
            "/api/user/usb_state", "/api/user/usb_route",
            "/api/user/multiview_state", "/api/user/multiview_show",
            "/api/user/multiview_switch",
        }, "only endpoints written for a restricted account, which answer "
           "with a projection, belong here")

    def test_no_user_reachable_route_returns_an_unprojected_object(self):
        """The rule, applied to whatever the table says today.

        Every USER or AUTHENTICATED route either is a page, or is served by the
        projection. A future route added to those classes has to be one or the
        other, and this fails if it is neither.
        """
        projected = {"/api/user/state", "/api/user/route",
                     "/api/user/usb_state", "/api/user/usb_route",
                     "/api/user/multiview_state", "/api/user/multiview_show",
                     "/api/user/multiview_switch"}
        pages = {"/user", "/help"}
        session_only = {"/api/auth/logout", "/api/auth/session",
                        "/api/ui_preferences", "/api/ui_preferences/reset",
                        "/api/notices", "/api/notices/ack",
                        # The caller's own Direct Access link. It is about the
                        # account asking rather than about the deployment, it
                        # takes no user id, and it returns named fields only --
                        # whether a link exists, the link itself, and a
                        # sentence. No device state passes through it.
                        "/api/user/direct_access"}
        allowed = projected | pages | session_only
        for rule, (access, _f) in omni_access.ROUTES.items():
            if access in (Access.USER, Access.AUTHENTICATED):
                with self.subTest(rule=rule):
                    self.assertIn(
                        rule, allowed,
                        f"{rule} is reachable by a non-administrator; it must "
                        "answer with a projection, or be reclassified")


class RemoteAccessPersistenceTests(unittest.TestCase):
    """Restoring a listener is a convenience that must refuse to be unsafe."""

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="omni-persist-"))
        self.store = UserStore(self.dir / "users.json")

    def _server(self, saved, adapters, has_admin=True):
        """A restore attempt, with the host conditions stated explicitly.

        Reimplementing the decision would test nothing, so this drives the real
        predicate from the server module with its inputs substituted.
        """
        import OmniMatrix_upgrade_server_v7_6y as srv
        original = {
            "file": srv.REMOTE_ACCESS_FILE,
            "store": srv._user_store,
            "listener": srv._remote_listener,
        }
        path = self.dir / "remote_access.json"
        omni_users._atomic_write_json(path, saved)

        started = {}
        attempted = []

        class FakeListener:
            """A stand-in that refuses what the real listener refuses.

            It records *every* attempt, not only the ones that succeed. The
            distinction matters: the restore guard and the listener's own guard
            are two layers, and a fake that accepted anything would let a
            mutation of either one pass unnoticed because the other still held.
            What the tests assert is that restore never offers an unusable
            address at all.
            """

            def start(self, address, port, ssl_context=None):
                attempted.append((address, port))
                if not omni_remote.is_usable_remote_address(address):
                    return {"ok": False, "error": "that is not a usable address"}
                started["address"] = address
                started["port"] = port
                started["secure"] = ssl_context is not None
                scheme = "https" if ssl_context is not None else "http"
                return {"ok": True, "enabled": True, "host": address,
                        "port": port, "scheme": scheme,
                        "secure": ssl_context is not None,
                        "url": f"{scheme}://{address}:{port}/"}

        if has_admin and not self.store.has_admin:
            self.store.create("admin", "admin-password-1", omni_users.ROLE_ADMIN)

        srv.REMOTE_ACCESS_FILE = path
        srv._user_store = self.store
        srv._remote_listener = FakeListener()
        real_adapters = srv.api_adapters

        def fake_adapters():
            from flask import jsonify
            return jsonify({"ok": True, "adapters": adapters})

        srv.api_adapters = fake_adapters

        # Restore resolves the adapter through omni_adapters, because that is
        # where a stable identity lives. Substituting it here keeps the suite
        # off this machine's real network -- the previous stub reached it.
        import omni_adapters
        real_enumerate = omni_adapters.enumerate_adapters

        def fake_enumerate(include_down=False):
            built = []
            for entry in adapters:
                address = entry.get("address") or entry.get("ip") or ""
                built.append({
                    "id": entry.get("id") or ("name:" + str(
                        entry.get("name", "")).lower()),
                    "id_from": "guid" if entry.get("id") else "name",
                    "name": entry.get("name", ""),
                    "address": address,
                    "netmask": entry.get("netmask", "255.255.255.0"),
                    "prefix": 24,
                    "cidr": entry.get("cidr", ""),
                    "scan": "",
                    "status": "Up",
                    "up": True,
                    "virtual": False,
                    "label": "%s — %s" % (entry.get("name", ""), address),
                })
            return built

        omni_adapters.enumerate_adapters = fake_enumerate
        try:
            result = srv.restore_remote_access()
            return (result, started, srv._remote_access_last_choice(),
                    attempted)
        finally:
            srv.REMOTE_ACCESS_FILE = original["file"]
            srv._user_store = original["store"]
            srv._remote_listener = original["listener"]
            srv.api_adapters = real_adapters
            omni_adapters.enumerate_adapters = real_enumerate

    ADAPTER = [{"id": "guid:aaaa", "address": "10.48.55.10",
                "name": "Ethernet", "cidr": "10.48.55.0/24"}]

    #: The address tests below declare transport explicitly. They were written
    #: before TLS existed and are about which address is bound, not about how
    #: it is secured; leaving transport out would make every one of them a
    #: certificate test by accident.

    def test_a_clean_install_does_not_restore_anything(self):
        result, started, _state, _tried = self._server(
            {"enabled": False, "address": "", "port": None}, self.ADAPTER)
        self.assertFalse(result["restored"])
        self.assertEqual(started, {})

    def test_an_explicit_disable_stays_disabled(self):
        result, started, _state, _tried = self._server(
            {"enabled": False, "address": "10.48.55.10", "port": 8080,
             "transport": "http"},
            self.ADAPTER)
        self.assertFalse(result["restored"],
                         "a disabled preference must not come back because an "
                         "address is remembered")
        self.assertEqual(started, {})

    def test_an_explicit_enable_restores_the_saved_address(self):
        result, started, _state, _tried = self._server(
            {"enabled": True, "adapter_id": "guid:aaaa", "address": "10.48.55.10",
             "port": 8080, "transport": "http"},
            self.ADAPTER)
        self.assertTrue(result["restored"])
        self.assertEqual(started["address"], "10.48.55.10")
        self.assertEqual(started["port"], 8080)

    def test_a_missing_adapter_binds_nothing_at_all(self):
        """The case that matters: the saved network is not here today."""
        result, started, state, _tried = self._server(
            {"enabled": True, "adapter_id": "guid:aaaa", "address": "10.48.55.10",
             "port": 8080, "transport": "http"},
            [{"id": "guid:bbbb", "address": "192.168.9.4", "name": "Other"}])
        self.assertFalse(result["restored"])
        self.assertEqual(started, {}, "nothing may be bound")
        self.assertIn("not available", state["restore_error"])

    def test_a_missing_adapter_does_not_choose_another_one(self):
        _result, started, _state, _tried = self._server(
            {"enabled": True, "address": "10.48.55.10", "port": 8080,
             "transport": "http"},
            [{"address": "192.168.9.4", "name": "Other"},
             {"address": "172.16.0.9", "name": "Another"}])
        self.assertEqual(started, {},
                         "an administrator chose a network; picking a "
                         "different one exposes the host somewhere nobody "
                         "intended")

    def test_it_never_falls_back_to_all_interfaces(self):
        for saved_address in ("0.0.0.0", "", "::"):
            with self.subTest(address=saved_address):
                _r, started, _s, tried = self._server(
                    {"enabled": True, "address": saved_address, "port": 8080,
                     "transport": "http"},
                    self.ADAPTER)
                self.assertEqual(started, {})
                self.assertEqual(tried, [])

    def test_a_saved_loopback_address_is_refused(self):
        _r, started, _s, tried = self._server(
            {"enabled": True, "address": "127.0.0.1", "port": 8080,
             "transport": "http"},
            self.ADAPTER)
        self.assertEqual(started, {},
                         "loopback is already served; binding it remotely "
                         "would tag remote requests as local")
        self.assertEqual(tried, [],
                         "restore must refuse it, not rely on the listener to")

    def test_the_address_guard_holds_on_its_own(self):
        """Tested with the adapter check unable to help.

        The previous test passes for the wrong reason: loopback is not in the
        adapter list, so the availability check refuses it and the address
        check is never reached. A mutation run proved that -- deleting the
        address guard changed nothing. Here the enumerator reports loopback and
        0.0.0.0 as available, so only the address guard can refuse them, which
        is the situation a mis-reporting adapter enumeration would create.
        """
        for address in ("127.0.0.1", "0.0.0.0", ""):
            with self.subTest(address=address):
                _r, started, _s, tried = self._server(
                    {"enabled": True, "address": address, "port": 8080,
                     "transport": "http"},
                    [{"address": "127.0.0.1", "name": "Loopback"},
                     {"address": "0.0.0.0", "name": "All interfaces"},
                     {"address": "", "name": "Nothing"}])
                self.assertEqual(
                    started, {},
                    f"{address!r} must never be bound as the remote listener, "
                    "whatever the adapter enumeration claims")
                self.assertEqual(
                    tried, [],
                    f"restore offered {address!r} to the listener; the two "
                    "guards are layers, and this one has to hold by itself")

    def test_no_administrator_means_no_restore(self):
        """A restored listener nobody can sign in to is worse than none.

        Bootstrap is local-only, so a remote listener with no account would
        answer the network and offer no way in.
        """
        _r, started, state, _tried = self._server(
            {"enabled": True, "address": "10.48.55.10", "port": 8080,
             "transport": "http"},
            self.ADAPTER, has_admin=False)
        self.assertEqual(started, {})
        self.assertIn("administrator", state["restore_error"])

    def test_a_corrupt_preference_reads_as_off(self):
        import OmniMatrix_upgrade_server_v7_6y as srv
        path = self.dir / "broken.json"
        path.write_text("{ not json", encoding="utf-8")
        original = srv.REMOTE_ACCESS_FILE
        srv.REMOTE_ACCESS_FILE = path
        try:
            saved = srv._remote_access_last_choice()
        finally:
            srv.REMOTE_ACCESS_FILE = original
        self.assertFalse(saved["enabled"],
                         "an unreadable preference can never mean 'expose "
                         "this machine'")

    def test_a_failed_restore_is_not_a_startup_failure(self):
        result, _started, _state, _tried = self._server(
            {"enabled": True, "address": "10.48.55.10", "port": 8080,
             "transport": "http"},
            [], has_admin=True)
        self.assertIsInstance(result, dict,
                              "restore reports a result; it must not raise")
        self.assertFalse(result["restored"])


class SessionsDoNotSurviveRestartTests(unittest.TestCase):
    """Accounts persist, permissions persist, sessions do not."""

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="omni-restart-"))
        self.store = UserStore(self.dir / "users.json")
        self.store.create("teacher", "teacher-password-1", omni_users.ROLE_USER,
                          Permissions(features=("av",), sources=(SOURCE,),
                                      destinations=(DISPLAY,)))
        self.teacher = self.store.find_by_username("teacher")
        self.session = self.store.open_session(self.teacher.id)

    def test_a_session_does_not_survive_a_restart(self):
        reopened = UserStore(self.dir / "users.json")
        account, session = reopened.resolve_session(self.session.token)
        self.assertIsNone(account,
                          "sessions are in memory; a restart signs everyone out")

    def test_the_account_and_its_permissions_do_survive(self):
        reopened = UserStore(self.dir / "users.json")
        teacher = reopened.find_by_username("teacher")
        self.assertIsNotNone(teacher)
        self.assertTrue(teacher.permissions.has_feature("av"))
        self.assertTrue(teacher.permissions.may_use_source(SOURCE))
        self.assertTrue(teacher.permissions.may_control_destination(DISPLAY))

    def test_a_restored_listener_still_requires_signing_in(self):
        """The listener may return automatically; the people may not."""
        reopened = UserStore(self.dir / "users.json")
        decision = omni_gate.evaluate("/api/user/state", "GET",
                                      remote_env(self.session), {}, reopened)
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.status, 401)


class AdapterSelectionSurvivesTests(RemoteAccessPersistenceTests):
    """C and D at the restore level, through the real predicate."""

    ETH = "guid:aaaa1111-2222-3333-4444-555566667777"
    WIFI = "guid:bbbb1111-2222-3333-4444-555566667777"

    def test_a_saved_adapter_restores_after_a_restart(self):
        result, started, _state, _tried = self._server(
            {"enabled": True, "adapter_id": self.ETH,
             "adapter_name": "Ethernet 3", "address": "198.51.100.85",
             "port": 8081, "transport": "http"},
            [{"id": self.ETH, "name": "Ethernet 3",
              "address": "198.51.100.85"}])
        self.assertTrue(result["restored"])
        self.assertEqual(started["address"], "198.51.100.85")

    def test_the_same_adapter_at_a_new_address_restores_there(self):
        """The defect, at the level an operator experiences it."""
        result, started, _state, _tried = self._server(
            {"enabled": True, "adapter_id": self.ETH,
             "adapter_name": "Ethernet 3", "address": "198.51.100.80",
             "port": 8081, "transport": "http"},
            [{"id": self.ETH, "name": "Ethernet 3",
              "address": "198.51.100.85"}])
        self.assertTrue(result["restored"])
        self.assertEqual(started["address"], "198.51.100.85",
                         "the chosen adapter moved; the listener follows it")

    def test_another_adapter_holding_the_old_address_inherits_nothing(self):
        """E: Wi-Fi had .180. Ethernet must not answer for that selection."""
        result, started, state, _tried = self._server(
            {"enabled": True, "adapter_id": self.WIFI, "adapter_name": "Wi-Fi",
             "address": "198.51.100.80", "port": 8081, "transport": "http"},
            [{"id": self.ETH, "name": "Ethernet 3",
              "address": "198.51.100.80"}])
        self.assertFalse(result["restored"])
        self.assertEqual(started, {})
        self.assertIn("Wi-Fi", state["restore_error"])
        self.assertIn("not available", state["restore_error"])

    def test_the_message_names_the_adapter_not_the_address(self):
        """F: an operator needs to know which network to plug back in."""
        _result, _started, state, _tried = self._server(
            {"enabled": True, "adapter_id": self.ETH,
             "adapter_name": "Ethernet 3", "address": "198.51.100.85",
             "port": 8081, "transport": "http"},
            [{"id": self.WIFI, "name": "Wi-Fi", "address": "10.0.0.5"}])
        self.assertIn("Ethernet 3", state["restore_error"])

    def test_an_address_only_config_migrates_when_it_is_unambiguous(self):
        """P."""
        result, started, _state, _tried = self._server(
            {"enabled": True, "address": "10.48.55.10", "port": 8080,
             "transport": "http"},
            [{"id": "guid:aaaa", "name": "Ethernet", "address": "10.48.55.10"}])
        self.assertTrue(result["restored"])
        self.assertEqual(started["address"], "10.48.55.10")

    def test_an_address_only_config_refuses_when_it_cannot_be_established(self):
        """Q: the state Wylie's machine was actually in."""
        result, started, state, _tried = self._server(
            {"enabled": True, "address": "198.51.100.80", "port": 8081,
             "transport": "http"},
            [{"id": self.ETH, "name": "Ethernet 3",
              "address": "198.51.100.85"}])
        self.assertFalse(result["restored"])
        self.assertEqual(started, {}, "nothing may be bound on a guess")
        self.assertIn("cannot be established", state["restore_error"])

    def test_remote_access_off_stays_off_whatever_the_adapters_say(self):
        """N."""
        result, started, _state, _tried = self._server(
            {"enabled": False, "adapter_id": self.ETH,
             "address": "198.51.100.85", "port": 8081},
            [{"id": self.ETH, "name": "Ethernet 3",
              "address": "198.51.100.85"}])
        self.assertFalse(result["restored"])
        self.assertEqual(started, {})

    def test_https_behaviour_is_unchanged_by_any_of_this(self):
        """O: a saved HTTPS configuration with no certificate still refuses."""
        result, started, _state, _tried = self._server(
            {"enabled": True, "adapter_id": self.ETH,
             "adapter_name": "Ethernet 3", "address": "198.51.100.85",
             "port": 8081, "transport": "https"},
            [{"id": self.ETH, "name": "Ethernet 3",
              "address": "198.51.100.85"}])
        self.assertFalse(result["restored"])
        self.assertEqual(started, {}, "no plaintext fallback, ever")


if __name__ == "__main__":
    unittest.main()
