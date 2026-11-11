"""The local application port: an optional fixed port, and how it is held.

OMNI-MA-001. Everything here uses real loopback sockets and a scratch data
folder; nothing touches a device, the Remote Access listener or the operator's
real settings. Every listener a test opens is closed before the test ends.
"""
from __future__ import annotations

import copy
import json
import os
import socket
import sys
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("OMNI_SMOKE", "1")

import omni_access                                      # noqa: E402
import omni_backup                                      # noqa: E402
import omni_gate                                        # noqa: E402
import omni_local_port as lp                            # noqa: E402
import omni_remote                                      # noqa: E402
import omni_users                                       # noqa: E402
import OmniMatrix_upgrade_server_v7_6y as srv           # noqa: E402

LOCAL_PORT_ROUTES = ("/api/local_port", "/api/local_port/fixed",
                     "/api/local_port/disable", "/api/local_port/retry",
                     "/api/local_port/make_fixed")


def free_port():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


class Occupier:
    """A plain listener that holds a port until it is closed."""

    def __init__(self, port=None):
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", port or 0))
        self.sock.listen(1)
        self.port = self.sock.getsockname()[1]

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


class Base(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.folder.cleanup)
        self.data = Path(self.folder.name)
        self.listeners = []
        self.addCleanup(self.close_listeners)

    def close_listeners(self):
        for item in self.listeners:
            try:
                item.close()
            except Exception:
                pass

    def occupy(self, port=None):
        item = Occupier(port)
        self.listeners.append(item)
        return item

    @property
    def file(self):
        return self.data / lp.CONFIG_FILE


class ParsePortTests(unittest.TestCase):
    def test_the_accepted_range(self):
        for value, expected in ((1024, 1024), (65535, 65535), ("8080", 8080),
                                (" 28123 ", 28123), (8080, 8080)):
            self.assertEqual(lp.parse_port(value), expected)

    def test_every_invalid_value_is_refused_with_a_reason(self):
        for value in ("", "   ", None, True, False, "abc", "80", "1023", "0",
                      "65536", "99999", "8080.5", 8080.5, "-1", "+8080",
                      "8 080", "8e3", "0x1F90", "８０８０",
                      [8080], {"port": 8080}):
            with self.subTest(value=value):
                with self.assertRaises(lp.PortError) as caught:
                    lp.parse_port(value)
                self.assertTrue(str(caught.exception))

    def test_it_is_a_value_error_so_callers_that_expect_one_get_one(self):
        self.assertTrue(issubclass(lp.PortError, ValueError))


class ConfigFileTests(Base):
    def test_absent_means_automatic(self):
        self.assertEqual(lp.read_config(self.data),
                         {"mode": "automatic", "port": None, "problem": ""})

    def test_fixed_round_trips(self):
        self.assertTrue(lp.write_config(self.data, lp.MODE_FIXED, 28123))
        stored = json.loads(self.file.read_text("utf-8"))
        self.assertEqual(stored["local_port_mode"], "fixed")
        self.assertEqual(stored["configured_local_fixed_port"], 28123)
        self.assertEqual(lp.read_config(self.data)["port"], 28123)

    def test_a_malformed_file_is_quarantined_and_degrades_to_automatic(self):
        self.file.write_text("{ not json", encoding="utf-8")
        result = lp.read_config(self.data)
        self.assertEqual(result["mode"], "automatic")
        self.assertEqual(result["problem"], "unreadable")
        self.assertFalse(self.file.exists(), "left in place, it would fail again")
        self.assertTrue(list(self.data.glob("local_port.corrupt-*.json")))

    def test_a_file_that_is_not_an_object_is_quarantined(self):
        self.file.write_text("[1, 2]", encoding="utf-8")
        self.assertEqual(lp.read_config(self.data)["mode"], "automatic")
        self.assertTrue(list(self.data.glob("local_port.corrupt-*.json")))

    def test_a_fixed_mode_with_a_bad_port_is_automatic(self):
        for bad in (None, "x", 80, 70000, True):
            with self.subTest(port=bad):
                self.file.write_text(json.dumps(
                    {"local_port_mode": "fixed",
                     "configured_local_fixed_port": bad}), encoding="utf-8")
                result = lp.read_config(self.data)
                self.assertEqual(result["mode"], "automatic")
                self.assertEqual(result["problem"], "invalid_port")

    def test_an_unknown_mode_is_automatic(self):
        self.file.write_text(json.dumps({"local_port_mode": "turbo"}),
                             encoding="utf-8")
        self.assertEqual(lp.read_config(self.data)["problem"], "unknown_mode")

    def test_nothing_in_the_file_can_make_reading_it_raise(self):
        self.file.mkdir()                 # a directory where the file should be
        self.assertEqual(lp.read_config(self.data)["mode"], "automatic")

    def test_writes_are_atomic_and_serialized(self):
        errors = []

        def writer(port):
            try:
                for _ in range(25):
                    self.assertTrue(lp.write_config(self.data, lp.MODE_FIXED, port))
            except Exception as exc:                  # pragma: no cover
                errors.append(exc)

        threads = [threading.Thread(target=writer, args=(28000 + n,))
                   for n in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(30)
        self.assertFalse(errors)
        stored = json.loads(self.file.read_text("utf-8"))          # never torn
        self.assertIn(stored["configured_local_fixed_port"],
                      {28000, 28001, 28002, 28003})
        self.assertEqual(list(self.data.glob("*.tmp")), [])

    def test_a_failed_write_leaves_the_old_file_byte_for_byte(self):
        lp.write_config(self.data, lp.MODE_FIXED, 28200)
        before = self.file.read_bytes()
        import omni_local_port as module
        real = os.replace

        def refuse(*_a, **_k):
            raise OSError("simulated")
        module.os.replace = refuse
        try:
            self.assertFalse(lp.write_config(self.data, lp.MODE_FIXED, 28201))
        finally:
            module.os.replace = real
        self.assertEqual(self.file.read_bytes(), before)
        self.assertEqual(list(self.data.glob("*.tmp")), [])


class BackupScopeTests(Base):
    """Decided on evidence: a port is a fact about the computer it was chosen on.

    The backup model carries deployment decisions and strips machine-bound
    fields; it has no section for anything about the host's own listener, and a
    restored port could collide with whatever the replacement PC already runs.
    """

    def test_it_is_not_a_backup_section(self):
        names = {section.filename for section in omni_backup.SECTIONS}
        self.assertNotIn(lp.CONFIG_FILE, names)

    def test_a_backup_does_not_carry_it_and_a_restore_does_not_remove_it(self):
        lp.write_config(self.data, lp.MODE_FIXED, 28300)
        payload = omni_backup.build_payload(self.data, [], app_version="V0")
        self.assertNotIn("local_port", json.dumps(payload))
        self.assertNotIn(lp.CONFIG_FILE, json.dumps(payload))
        self.assertTrue(self.file.exists())

    def test_the_legacy_removal_list_does_not_name_it(self):
        source = (ROOT / "omni_backup.py").read_text(encoding="utf-8")
        self.assertNotIn("local_port", source)


class BindTests(Base):
    def app(self):
        from flask import Flask
        return Flask("local-port-test")

    def test_a_free_port_binds_exactly(self):
        port = free_port()
        httpd = lp.bind_listener(self.app(), "127.0.0.1", port)
        self.listeners.append(httpd.socket)
        self.assertEqual(httpd.server_port, port)

    def test_an_occupied_port_is_a_conflict_and_no_other_port_is_tried(self):
        held = self.occupy()
        with self.assertRaises(lp.PortUnavailable) as caught:
            lp.bind_listener(self.app(), "127.0.0.1", held.port)
        self.assertTrue(caught.exception.in_use)
        self.assertEqual(caught.exception.port, held.port)
        self.assertIn(str(held.port), str(caught.exception))

    def test_a_listener_that_set_reuse_does_not_defeat_the_conflict(self):
        """Werkzeug's own server holds a port with SO_REUSEADDR; a second bind
        must still be refused rather than quietly sharing it."""
        first = lp.bind_listener(self.app(), "127.0.0.1", free_port())
        self.listeners.append(first.socket)
        with self.assertRaises(lp.PortUnavailable):
            lp.bind_listener(self.app(), "127.0.0.1", first.server_port)

    def test_the_probe_is_advisory_and_the_bind_is_authoritative(self):
        """Free at validation, occupied before the real bind."""
        port = free_port()
        self.assertIsNone(lp.probe("127.0.0.1", port))        # the advisory answer
        self.occupy(port)                                     # then it is taken
        with self.assertRaises(lp.PortUnavailable):
            lp.bind_listener(self.app(), "127.0.0.1", port)

    def test_the_server_exit_werkzeug_raises_is_not_allowed_to_escape(self):
        held = self.occupy()
        try:
            lp.bind_listener(self.app(), "127.0.0.1", held.port)
        except SystemExit:                                    # pragma: no cover
            self.fail("SystemExit escaped; it would end the launcher's thread")
        except lp.PortUnavailable:
            pass


class SupervisorTests(Base):
    def make(self, restart=True):
        self.restarts = []
        supervisor = lp.PortSupervisor(
            self.data, "127.0.0.1",
            restart=(lambda: self.restarts.append(1)) if restart else None)
        supervisor.load()
        return supervisor

    def test_fixed_mode_is_off_by_default(self):
        supervisor = self.make()
        self.assertEqual(supervisor.mode, "automatic")
        self.assertFalse(supervisor.status()["fixed"])
        self.assertFalse(self.file.exists(), "reading must never create state")

    def test_the_status_reports_the_actual_port_never_a_default(self):
        supervisor = self.make()
        supervisor.bound(28777)
        status = supervisor.status()
        self.assertEqual(status["actual_port"], 28777)
        self.assertEqual(status["url"], "http://127.0.0.1:28777/")
        self.assertNotIn("8080", json.dumps(status))

    def test_a_port_is_saved_only_after_it_has_been_bound(self):
        supervisor = self.make()
        with self.assertRaises(RuntimeError):
            supervisor.persist_after_bind(28400)              # nothing bound yet
        self.assertFalse(self.file.exists())
        supervisor.bound(28401)
        with self.assertRaises(RuntimeError):
            supervisor.persist_after_bind(28400)              # not the bound one
        self.assertFalse(self.file.exists())
        self.assertTrue(supervisor.persist_after_bind(28401))
        self.assertEqual(lp.read_config(self.data)["port"], 28401)

    def test_the_temporary_alternate_is_never_persisted(self):
        lp.write_config(self.data, lp.MODE_FIXED, 28500)
        before = self.file.read_bytes()
        supervisor = self.make()
        supervisor.bound(28501, temporary=True)
        status = supervisor.status()
        self.assertTrue(status["temporary"])
        self.assertEqual(status["configured_port"], 28500)
        self.assertEqual(status["actual_port"], 28501)
        self.assertEqual(self.file.read_bytes(), before)

    def test_set_fixed_validates_first(self):
        supervisor = self.make()
        for bad in ("", "abc", "80", "70000", None):
            with self.subTest(value=bad):
                body, status = supervisor.set_fixed(bad)
                self.assertEqual(status, 400)
                self.assertFalse(body["ok"])
        self.assertFalse(self.file.exists())
        self.assertEqual(self.restarts, [])

    def test_set_fixed_refuses_a_port_that_is_taken_and_changes_nothing(self):
        held = self.occupy()
        supervisor = self.make()
        supervisor.bound(free_port())
        body, status = supervisor.set_fixed(held.port)
        self.assertEqual((status, body["reason"]), (409, "port_in_use"))
        self.assertFalse(self.file.exists())
        self.assertEqual(self.restarts, [])

    def test_the_current_port_is_not_refused_for_being_held_by_us(self):
        """The old listener holds it; the restart releases it first."""
        own = self.occupy()
        supervisor = self.make()
        supervisor.bound(own.port)
        body, status = supervisor.set_fixed(own.port)
        self.assertEqual(status, 200, body)
        self.assertEqual(lp.read_config(self.data)["port"], own.port)
        self.assertEqual(self.restarts, [1])

    def test_set_fixed_persists_then_restarts_once(self):
        supervisor = self.make()
        supervisor.bound(28600)
        port = free_port()
        body, status = supervisor.set_fixed(str(port))
        self.assertEqual((status, body["restarting"], body["port"]),
                         (200, True, port))
        self.assertEqual(lp.read_config(self.data)["mode"], "fixed")
        self.assertEqual(self.restarts, [1])
        again, again_status = supervisor.set_fixed(free_port())
        self.assertEqual(again_status, 409, "no second restart while one is under way")
        self.assertEqual(self.restarts, [1])

    def test_without_a_launcher_nothing_can_be_changed(self):
        supervisor = self.make(restart=False)
        for call in (lambda: supervisor.set_fixed(28123), supervisor.disable_fixed,
                     supervisor.retry_fixed, supervisor.make_fixed):
            body, status = call()
            self.assertEqual(status, 409)
            self.assertFalse(self.file.exists())

    def test_disable_returns_to_automatic_and_restarts(self):
        lp.write_config(self.data, lp.MODE_FIXED, 28700)
        supervisor = self.make()
        supervisor.bound(28700)
        body, status = supervisor.disable_fixed()
        self.assertEqual(status, 200)
        self.assertEqual(lp.read_config(self.data)["mode"], "automatic")
        self.assertEqual(self.restarts, [1])

    def test_retry_fixed_needs_a_temporary_port_and_a_free_configured_one(self):
        held = self.occupy()
        lp.write_config(self.data, lp.MODE_FIXED, held.port)
        supervisor = self.make()
        body, status = supervisor.retry_fixed()
        self.assertEqual(body["reason"], "not_temporary")
        supervisor.bound(free_port(), temporary=True)
        body, status = supervisor.retry_fixed()
        self.assertEqual((status, body["reason"]), (409, "port_in_use"))
        self.assertEqual(self.restarts, [])
        held.close()
        body, status = supervisor.retry_fixed()
        self.assertEqual(status, 200)
        self.assertEqual(self.restarts, [1])

    def test_make_fixed_keeps_the_serving_port_without_restarting(self):
        lp.write_config(self.data, lp.MODE_FIXED, 28800)
        supervisor = self.make()
        supervisor.bound(28801, temporary=True)
        body, status = supervisor.make_fixed()
        self.assertEqual((status, body["restarting"], body["port"]),
                         (200, False, 28801))
        self.assertEqual(self.restarts, [])
        self.assertEqual(lp.read_config(self.data)["port"], 28801)
        self.assertFalse(supervisor.status()["temporary"])

    def test_make_fixed_is_refused_when_not_temporary(self):
        supervisor = self.make()
        supervisor.bound(28802)
        body, status = supervisor.make_fixed()
        self.assertEqual(body["reason"], "not_temporary")
        self.assertFalse(self.file.exists())

    def test_none_of_it_touches_remote_access(self):
        remote = self.data / "remote_access.json"
        remote.write_text('{"enabled": false, "port": 9999}', encoding="utf-8")
        before = remote.read_bytes()
        supervisor = self.make()
        supervisor.bound(28900)
        supervisor.set_fixed(free_port())
        supervisor.make_fixed()
        supervisor.disable_fixed()
        self.assertEqual(remote.read_bytes(), before)
        source = (ROOT / "omni_local_port.py").read_text(encoding="utf-8")
        for word in ("remote_access", "REMOTE_ACCESS", "omni_remote_api",
                     "omni_tls"):
            self.assertNotIn(word, source.replace(
                "Remote Access", ""), word)


class EndpointTests(unittest.TestCase):
    """The HTTP surface: local only, and honest when there is no launcher."""

    def setUp(self):
        self.previous = lp.controller()
        self.addCleanup(lp.register_controller, self.previous)
        lp.register_controller(None)
        self.client = srv.app.test_client()

    def test_every_route_is_registered_and_classified_local_only(self):
        rules = {rule.rule for rule in srv.app.url_map.iter_rules()}
        for route in LOCAL_PORT_ROUTES:
            with self.subTest(route=route):
                self.assertIn(route, rules)
                self.assertIs(omni_access.classify(route)[0],
                              omni_access.Access.LOCAL_ONLY)

    def test_the_remote_listener_cannot_reach_them(self):
        """The gate refuses by the socket the request arrived on."""
        directory = Path(tempfile.mkdtemp(prefix="omni-lp-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(directory, True))
        store = omni_users.UserStore(directory / "users.json",
                                     directory / "audit.json")
        store.create("admin1", "admin-password-1", omni_users.ROLE_ADMIN)
        session = store.open_session(store.find_by_username("admin1").id)
        for route in LOCAL_PORT_ROUTES:
            for method in ("GET", "POST"):
                with self.subTest(route=route, method=method):
                    env = {omni_remote.LISTENER_KEY: omni_remote.REMOTE,
                           "REQUEST_METHOD": method,
                           "HTTP_COOKIE": omni_gate.SESSION_COOKIE + "="
                                          + session.token,
                           "HTTP_X_OMNISUITE_CSRF": session.csrf_token}
                    decision = omni_gate.evaluate(route, method, env, {}, store)
                    self.assertFalse(decision.allowed)
                    local = omni_gate.evaluate(
                        route, method, {"REQUEST_METHOD": method}, {}, store)
                    self.assertTrue(local.allowed)

    def test_a_request_from_the_remote_listener_never_reaches_the_controller(self):
        calls = []

        class Spy:
            def status(self):
                calls.append("status")
                return {"ok": True}

            def set_fixed(self, value):
                calls.append("set_fixed")
                return {"ok": True}, 200
        lp.register_controller(Spy())
        env = {omni_remote.LISTENER_KEY: omni_remote.REMOTE}
        for method, route in (("get", "/api/local_port"),
                              ("post", "/api/local_port/fixed")):
            response = getattr(self.client, method)(route, environ_base=env)
            self.assertNotEqual(response.status_code, 200)
        self.assertEqual(calls, [])

    def test_without_a_launcher_the_answer_says_so(self):
        status = self.client.get("/api/local_port").get_json()
        self.assertFalse(status["available"])
        self.assertIn("not available in this run", status["message"])
        for route in LOCAL_PORT_ROUTES[1:]:
            response = self.client.post(route, json={"port": 28123})
            self.assertEqual(response.status_code, 409)
            body = response.get_json()
            self.assertFalse(body["ok"])
            self.assertIn("not available in this run", body["error"])

    def test_with_a_controller_the_calls_are_delegated(self):
        seen = []

        class Fake:
            def status(self):
                return {"ok": True, "available": True, "actual_port": 28999}

            def set_fixed(self, value):
                seen.append(("fixed", value))
                return {"ok": True, "restarting": True}, 200

            def disable_fixed(self):
                seen.append(("disable",))
                return {"ok": True}, 200

            def retry_fixed(self):
                seen.append(("retry",))
                return {"ok": False, "reason": "port_in_use"}, 409

            def make_fixed(self):
                seen.append(("make",))
                return {"ok": True}, 200
        lp.register_controller(Fake())
        self.assertEqual(self.client.get("/api/local_port").get_json()["actual_port"],
                         28999)
        self.client.post("/api/local_port/fixed", json={"port": "28123"})
        self.client.post("/api/local_port/disable")
        self.assertEqual(self.client.post("/api/local_port/retry").status_code, 409)
        self.client.post("/api/local_port/make_fixed")
        self.assertEqual(seen, [("fixed", "28123"), ("disable",), ("retry",),
                                ("make",)])

    def test_a_garbage_body_is_a_refusal_not_a_crash(self):
        class Real:
            def set_fixed(self, value):
                return lp.PortSupervisor(
                    tempfile.gettempdir(), restart=lambda: None).set_fixed(value)
        lp.register_controller(Real())
        for body in (b"not json", b"[]", b"null", b'{"port": {}}'):
            response = self.client.post("/api/local_port/fixed", data=body,
                                        content_type="application/json")
            self.assertEqual(response.status_code, 400)


class RemoteAccessIsNotTouchedTests(unittest.TestCase):
    """The Remote Access listener keeps its own port, binding and transport."""

    def test_the_remote_default_port_still_follows_the_bound_local_port(self):
        source = (ROOT / "OmniMatrix_upgrade_server_v7_6y.py").read_text(
            encoding="utf-8")
        self.assertIn('int(os.getenv("OMNI_PORT", PORT) or PORT)', source)

    def test_the_listener_class_is_not_aware_of_the_local_port(self):
        source = (ROOT / "omni_remote.py").read_text(encoding="utf-8")
        self.assertNotIn("local_port", source)
        self.assertNotIn("omni_local_port", source)

    def test_no_remote_module_imports_the_local_port_module(self):
        for name in ("omni_remote.py", "omni_remote_api.py", "omni_tls.py",
                     "omni_adapters.py", "omni_gate.py"):
            text = (ROOT / name).read_text(encoding="utf-8")
            self.assertNotIn("omni_local_port", text, name)


if __name__ == "__main__":
    unittest.main()
