"""Fences around the remote-access trust model.

These are the Phase A tests: they do not test a feature, they constrain one.
The risk this branch introduces is that OmniSuite's only current security
control -- binding 127.0.0.1 -- is removed from 112 anonymous routes at once,
so the tests that matter most are the ones that fail when a route escapes
classification or when the default stops being "deny".

Nothing here contacts hardware; the package's fence is installed on import.
"""
from __future__ import annotations

import os
import socket
import time
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("OMNI_SMOKE", "1")

import omni_access                                   # noqa: E402
import omni_remote                                   # noqa: E402
from omni_access import Access                       # noqa: E402
import OmniMatrix_upgrade_server_v7_6y as srv        # noqa: E402


def registered_rules() -> set[str]:
    """Every rule the application actually serves.

    Taken from Flask's own url_map rather than by grepping for decorators: a
    route registered any other way still has to be classified, and a grep would
    not see it.
    """
    return {str(rule.rule) for rule in srv.app.url_map.iter_rules()}


class RouteClassificationFenceTests(unittest.TestCase):
    """Every route is classified, and the table describes only real routes."""

    def test_every_registered_route_is_classified(self):
        missing = sorted(registered_rules() - set(omni_access.ROUTES))
        self.assertEqual(
            missing, [],
            "these routes have no access classification, so they would be "
            "denied at runtime and nobody has decided who may call them:\n  "
            + "\n  ".join(missing))

    def test_the_table_does_not_describe_routes_that_do_not_exist(self):
        """A stale entry is a rule somebody believes is protected and is not."""
        extra = sorted(set(omni_access.ROUTES) - registered_rules())
        self.assertEqual(
            extra, [],
            "these are classified but not registered -- either the route was "
            "renamed or the entry is stale:\n  " + "\n  ".join(extra))

    def test_an_unclassified_route_denies_by_default(self):
        """The default is the most restrictive answer, not the most convenient."""
        access, feature = omni_access.classify("/api/some/route/added/later")
        self.assertIs(access, Access.LOCAL_ONLY)
        self.assertIsNone(feature)


class PublicSurfaceTests(unittest.TestCase):
    """What may be reached with no session at all is a short, deliberate list."""

    def test_the_public_surface_is_exactly_what_a_login_page_needs(self):
        public = {rule for rule, (access, _f) in omni_access.ROUTES.items()
                  if access is Access.PUBLIC}
        self.assertEqual(
            public,
            {"/__health", "/license",
             "/static/<path:filename>", "/ui/<path:filename>",
             # The sign-in page and the credential check behind it. Nothing
             # else was added: /api/login is the *device* login probe and is
             # administrator-only (see below).
             "/login", "/api/auth/login"},
            "adding a route to the public surface is a security decision and "
            "has to be made deliberately here")

    def test_no_device_route_is_public(self):
        """Only the sign-in check may be called without a credential."""
        for rule, (access, _f) in omni_access.ROUTES.items():
            if rule.startswith("/api/") and rule != "/api/auth/login":
                self.assertIsNot(
                    access, Access.PUBLIC,
                    f"{rule} would be callable with no credential")

    def test_the_device_login_probe_is_not_the_sign_in_endpoint(self):
        """/api/login is not user authentication, despite its name.

        It opens a WebSocket to a caller-supplied address using the stored
        device credentials and reports the result. Phase A classified it public
        on the strength of the name, which would have made it an anonymous
        credential-use oracle and a probe for whatever the host can reach.
        """
        self.assertIs(omni_access.classify("/api/login")[0], Access.ADMIN)
        self.assertIs(omni_access.classify("/api/auth/login")[0], Access.PUBLIC)

        source = (ROOT / "OmniMatrix_upgrade_server_v7_6y.py").read_text(encoding="utf-8")
        body = source[source.index('@app.route("/api/login"'):]
        body = body[:body.index("\n@app.route")]
        self.assertIn("_ws_url(ip", body,
                      "/api/login connects to a caller-supplied address")
        self.assertIn("app.config['PASSWORD']", body,
                      "/api/login uses the stored device credentials")


class HostSurfaceTests(unittest.TestCase):
    """Routes that act on the host computer never become network-reachable."""

    #: Each of these does something to the machine OmniSuite runs on rather
    #: than to an AV device, so an Admin session is necessary but not
    #: sufficient -- the request also has to have arrived on loopback.
    HOST_ROUTES = (
        "/api/list_dir",                    # enumerates the filesystem
        "/api/files",                       # reads the firmware directory
        "/api/open_firmware_folder",        # opens a window on the desktop
        "/api/diagnostics/usb_endpoints",
        "/api/diagnostics/usb_route/state",
        "/api/diagnostics/usb_route/pair",
        "/api/diagnostics/usb_route/unpair",
    )

    def test_host_routes_are_local_only(self):
        for rule in self.HOST_ROUTES:
            with self.subTest(rule=rule):
                access, _feature = omni_access.classify(rule)
                self.assertIs(
                    access, Access.LOCAL_ONLY,
                    f"{rule} acts on the host and must not be reachable from "
                    "the network even with an Admin session")

    def test_list_dir_really_does_read_an_arbitrary_path(self):
        """The reason list_dir is local-only, asserted rather than assumed.

        If this ever stops being true the classification can be revisited; while
        it is true, the route must not be exposed.
        """
        source = (ROOT / "OmniMatrix_upgrade_server_v7_6y.py").read_text(encoding="utf-8")
        body = source[source.index('@app.route("/api/list_dir"'):]
        body = body[:body.index("\n@app.route")]
        self.assertIn('request.args.get("path"', body)
        self.assertIn("Path(path_arg).resolve()", body,
                      "list_dir resolves a caller-supplied absolute path")


class RestrictedUserSurfaceTests(unittest.TestCase):
    """What a restricted routing account can reach, stated exhaustively."""

    def test_the_user_surface_is_exactly_the_projected_endpoints(self):
        """One route, and it answers with a projection.

        This was thirteen. The other twelve were administrative endpoints that
        a restricted account was permitted to *call*, which is not the same as
        being permitted to see what they return -- each handed back the
        administrative object in full. Being on this list now means the
        endpoint was written for a restricted account and answers with named
        fields.
        """
        user_routes = {rule for rule, (access, _f) in omni_access.ROUTES.items()
                       if access is Access.USER}
        self.assertEqual(user_routes, {
            "/api/user/route",
            "/api/user/usb_state", "/api/user/usb_route",
            "/api/user/multiview_state", "/api/user/multiview_show",
            "/api/user/multiview_switch",
        }, "widening what a restricted account may call is a deliberate "
           "security decision, not an implementation detail")

    def test_every_user_endpoint_lives_in_the_user_namespace(self):
        """A restricted account only ever calls /api/user/.

        Which makes the rule reviewable at a glance: anything outside that
        prefix is administrative, and an endpoint that wants to serve a user
        has to be written for one rather than merely opened to one.
        """
        for rule, (access, _f) in omni_access.ROUTES.items():
            if access is Access.USER:
                with self.subTest(rule=rule):
                    self.assertTrue(rule.startswith("/api/user/"), rule)

    def test_the_user_shell_never_receives_the_administrative_state(self):
        """A user's state comes from the projection, not from /api/state.

        Both are User-reachable, because an administrator opens the same shell
        and because /api/state predates this work. What matters is that the
        shell reads the projected one: the projection is built from named
        fields, so an administrative field added later cannot leak through it.
        """
        shell = (ROOT / "ui" / "user" / "user.js").read_text(encoding="utf-8")
        code = "\n".join(line.split("//", 1)[0] for line in shell.splitlines())
        self.assertIn("/api/user/state", code)
        self.assertNotIn("'/api/state'", code)
        self.assertNotIn('"/api/state"', code)

    def test_every_user_route_names_the_capability_that_gates_it(self):
        for rule, (access, feature) in omni_access.ROUTES.items():
            if access is Access.USER:
                with self.subTest(rule=rule):
                    self.assertIn(feature, omni_access.FEATURES,
                                  f"{rule} is User-reachable but names no "
                                  "capability, so granting any feature would "
                                  "grant it")

    def test_a_capability_is_never_claimed_by_a_non_user_route(self):
        """A feature id on an Admin route would suggest a User could reach it."""
        for rule, (access, feature) in omni_access.ROUTES.items():
            if access is not Access.USER:
                with self.subTest(rule=rule):
                    self.assertIsNone(feature, f"{rule} is not a User route")

    def test_a_user_cannot_scan_configure_or_administer(self):
        """The §9 classroom prohibitions, asserted against the table."""
        forbidden = ("/api/scan", "/api/config", "/api/upgrade", "/api/reboot",
                     "/api/sync_passwords", "/api/hostname", "/api/codec",
                     "/api/remove_units", "/api/clear_units",
                     "/api/unit_config/import", "/api/multiview/delete",
                     "/api/multiview/copy", "/api/multiview/layouts/install",
                     "/api/usb_extenders/network", "/api/usb_extenders/reboot")
        for rule in forbidden:
            with self.subTest(rule=rule):
                access, _feature = omni_access.classify(rule)
                self.assertIsNot(access, Access.USER,
                                 f"a restricted user must not reach {rule}")
                self.assertIsNot(access, Access.PUBLIC)

    def test_the_administrative_multiview_endpoints_stay_administrative(self):
        """Phase I gave Multiview a projection; it did not open the admin API.

        A user operates through /api/user/multiview_*, which authorise the
        display, the preset approval and the preset's current content before
        delegating. Everything under /api/multiview remains administrative --
        including show and switch, whose administrative forms take addresses
        and disclose the decoder's whole preset inventory.
        """
        multiview = [r for r in omni_access.ROUTES if r.startswith("/api/multiview")]
        self.assertTrue(multiview)
        for rule in multiview:
            with self.subTest(rule=rule):
                self.assertIs(omni_access.classify(rule)[0], Access.ADMIN)

    def test_multiview_administration_is_never_reachable_by_a_user(self):
        """The operate-versus-administer boundary, stated as a list."""
        administrative = ("/api/multiview/apply", "/api/multiview/copy",
                          "/api/multiview/delete", "/api/multiview/plan",
                          "/api/multiview/layouts", "/api/multiview/layouts/install",
                          "/api/multiview/groups", "/api/multiview/groups/save",
                          "/api/multiview/groups/delete",
                          "/api/multiview/groups/copy",
                          "/api/multiview/groups/show",
                          "/api/multiview/groups/plan",
                          "/api/multiview/groups/state")
        for rule in administrative:
            with self.subTest(rule=rule):
                self.assertIs(omni_access.classify(rule)[0], Access.ADMIN,
                              f"{rule} is administration, not operation")


class PasswordHashingTests(unittest.TestCase):
    """The mechanism chosen for stored login credentials.

    werkzeug.security rather than a new dependency: Flask already brings it, it
    is not custom cryptography, and it produces a salted memory-hard hash that
    survives PyInstaller because it is pure Python over hashlib.
    """

    def test_the_hash_is_salted_and_not_reversible(self):
        from werkzeug.security import generate_password_hash, check_password_hash
        secret = "correct horse battery staple"
        first = generate_password_hash(secret)
        second = generate_password_hash(secret)
        self.assertNotEqual(first, second, "hashes must be individually salted")
        self.assertNotIn(secret, first, "the password must not appear in its hash")
        self.assertTrue(check_password_hash(first, secret))
        self.assertTrue(check_password_hash(second, secret))
        self.assertFalse(check_password_hash(first, "wrong"))

    def test_the_default_is_a_memory_hard_algorithm(self):
        from werkzeug.security import generate_password_hash
        method = generate_password_hash("x").split("$")[0]
        self.assertTrue(method.startswith("scrypt"),
                        f"expected scrypt, got {method!r}; a fast hash is not "
                        "appropriate for stored login credentials")


class TrustSignalTests(unittest.TestCase):
    """The local-versus-remote distinction may not rest on client-supplied data.

    The first version of this searched the source for the header names as bare
    substrings and failed on the word "host" in a sentence about the host
    computer. A test that cannot tell prose from code does not constrain the
    code, so this one parses instead: it asks the AST what the module reads.
    """

    #: Every one of these is set by the client and means nothing about where a
    #: request came from.
    SPOOFABLE = {"x-forwarded-for", "x-real-ip", "x-forwarded-host",
                 "x-forwarded-proto", "forwarded", "origin", "referer", "host"}

    def _policy_module_ast(self):
        import ast
        return ast.parse((ROOT / "omni_access.py").read_text(encoding="utf-8"))

    def test_the_policy_module_reads_no_request_data_at_all(self):
        """It is a table and two lookups; it has no business touching a request.

        Keeping it inert is what makes the trust decision reviewable in one
        place: policy here, enforcement at the gate, and no way for a header to
        influence the former.
        """
        import ast
        tree = self._policy_module_ast()
        names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
        attrs = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
        self.assertNotIn("request", names,
                         "the classification table must not read the request")
        self.assertNotIn("headers", attrs,
                         "no header may influence route classification")

    def test_no_spoofable_header_appears_as_a_string_the_code_could_look_up(self):
        """Header names in *string literals* -- prose in docstrings is fine."""
        import ast
        tree = self._policy_module_ast()
        docstrings = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef)):
                doc = ast.get_docstring(node, clean=False)
                if doc:
                    docstrings.add(doc)
        literals = {node.value for node in ast.walk(tree)
                    if isinstance(node, ast.Constant) and isinstance(node.value, str)
                    and node.value not in docstrings}
        offenders = sorted(s for s in literals if s.strip().lower() in self.SPOOFABLE)
        self.assertEqual(
            offenders, [],
            "these are client-supplied and must never establish that a request "
            f"is local: {offenders}")


class ListenerTagTests(unittest.TestCase):
    """The trust signal itself: which listener accepted the connection.

    Everything about local-versus-remote rests on this one key, so these tests
    attack it rather than demonstrate it.
    """

    def setUp(self):
        import omni_remote
        self.omni_remote = omni_remote

    def test_an_untagged_request_is_local_because_only_in_process_callers_make_one(self):
        """Untagged means "did not arrive over a socket".

        Every listener the product opens is tagged where it is bound, so the
        only thing that can produce an untagged request is an in-process caller
        such as Flask's test client.

        This was the other way round first. Treating unknown as remote looked
        like the cautious choice and instead hid the real defect: the packaged
        launcher was opening a third, unwrapped listener. The fix is to tag
        every listener, which the next test holds in place.
        """
        self.assertEqual(self.omni_remote.listener_of({}), self.omni_remote.LOCAL)
        self.assertTrue(self.omni_remote.is_local_request({}))

    def test_every_listener_the_product_opens_is_tagged(self):
        """The guarantee the previous test depends on, checked against source.

        If a fourth listener is ever added with a bare app.run or make_server,
        untagged stops meaning in-process and this assumption silently breaks.
        """
        import re
        for filename in ("OmniMatrix_upgrade_server_v7_6y.py", "app_launcher.py"):
            with self.subTest(filename=filename):
                source = (ROOT / filename).read_text(encoding="utf-8")
                code = "\n".join(line.split("#", 1)[0] for line in source.splitlines())
                self.assertNotIn(
                    "app.run(", code,
                    f"{filename} opens an untagged listener; use "
                    "omni_remote.serve_tagged so the request carries its origin")
                bare = [m for m in re.findall(r"\bmake_server\(", code)]
                self.assertEqual(
                    bare, [],
                    f"{filename} calls make_server directly; serve_tagged is "
                    "what decides the tag from the bound address")

    def test_the_tag_follows_the_bound_address_not_the_intent(self):
        """A listener on a routable address is never tagged local.

        OMNI_HOST can be forced to a routable address with remote access off.
        If that listener granted local trust, the boundary would be gone
        without anybody enabling anything.
        """
        for address in ("127.0.0.1", "127.5.5.5"):
            self.assertEqual(self.omni_remote.tag_for_bind(address),
                             self.omni_remote.LOCAL, address)
        for address in ("0.0.0.0", "10.48.55.10", "192.168.1.5"):
            self.assertEqual(self.omni_remote.tag_for_bind(address),
                             self.omni_remote.REMOTE, address)

    def test_the_local_wrapper_marks_local_and_the_remote_one_marks_remote(self):
        captured = {}

        def app(environ, start_response):
            captured.update(environ)
            start_response("200 OK", [])
            return [b""]

        for factory, expected in (
                (self.omni_remote.local_app, self.omni_remote.LOCAL),
                (lambda a: self.omni_remote._ListenerTag(a, self.omni_remote.REMOTE),
                 self.omni_remote.REMOTE)):
            captured.clear()
            factory(app)({}, lambda *a, **k: None)
            self.assertEqual(captured[self.omni_remote.LISTENER_KEY], expected)

    def test_no_http_header_can_produce_the_listener_key(self):
        """The core claim, asserted from how WSGI names headers.

        A header is exposed as HTTP_<NAME>, so a bare key containing a dot is
        unreachable from the wire. Measured against a live server as well: a
        request sending `omni.listener: local` arrived as HTTP_OMNI.LISTENER and
        the real tag was untouched.
        """
        key = self.omni_remote.LISTENER_KEY
        self.assertIn(".", key,
                      "the key must contain a character header names cannot "
                      "produce, or a client could set it directly")
        self.assertFalse(key.startswith("HTTP_"),
                         "a HTTP_-prefixed key is exactly what a header becomes")

        # A header of that name, mapped the way a WSGI server maps it.
        as_header = "HTTP_" + key.upper().replace("-", "_")
        self.assertNotEqual(as_header, key)

        captured = {}

        def app(environ, start_response):
            captured.update(environ)
            start_response("200 OK", [])
            return [b""]

        # The forged environ a client's header would produce, plus near misses.
        hostile = {as_header: self.omni_remote.LOCAL,
                   "HTTP_OMNI_LISTENER": self.omni_remote.LOCAL,
                   "HTTP_X_OMNI_LISTENER": self.omni_remote.LOCAL}
        wrapped = self.omni_remote._ListenerTag(app, self.omni_remote.REMOTE)
        wrapped(dict(hostile), lambda *a, **k: None)
        self.assertEqual(captured[self.omni_remote.LISTENER_KEY],
                         self.omni_remote.REMOTE,
                         "a client-supplied header must not influence the tag")

    def test_a_preset_tag_in_the_environ_is_overwritten_not_trusted(self):
        """Defence in depth: if the key is somehow present, ours still wins."""
        captured = {}

        def app(environ, start_response):
            captured.update(environ)
            start_response("200 OK", [])
            return [b""]

        wrapped = self.omni_remote._ListenerTag(app, self.omni_remote.REMOTE)
        wrapped({self.omni_remote.LISTENER_KEY: self.omni_remote.LOCAL},
                lambda *a, **k: None)
        self.assertEqual(captured[self.omni_remote.LISTENER_KEY],
                         self.omni_remote.REMOTE)

    def test_the_remote_listener_tags_its_own_requests_remote(self):
        """The real wrapping, not a hand-built environ.

        Every other test here constructs the environ itself, so none of them
        would notice if RemoteListener tagged its traffic `local` -- which a
        mutation run confirmed, and which would hand full local Admin to every
        remote request. This drives the actual code path by intercepting the
        server it builds, because the listener refuses to bind loopback and the
        suite must not bind anything else.
        """
        import werkzeug.serving

        captured = {}

        class FakeServer:
            def serve_forever(self):
                pass

            def shutdown(self):
                pass

            def server_close(self):
                pass

        def fake_make_server(host, port, app, **kwargs):
            captured["app"] = app
            return FakeServer()

        original = werkzeug.serving.make_server
        werkzeug.serving.make_server = fake_make_server
        try:
            listener = self.omni_remote.RemoteListener(self._probe_app())
            result = listener.start("10.48.55.10", 8099)
            self.assertTrue(result["ok"], result.get("error"))
            self.assertIn("app", captured, "the listener built no server")

            seen = {}

            def start_response(*args, **kwargs):
                return None

            captured["app"]({}, start_response)
            self.assertEqual(
                self._last_environ.get(self.omni_remote.LISTENER_KEY),
                self.omni_remote.REMOTE,
                "the remote listener must tag its traffic remote; tagging it "
                "local would grant every network request local trust")
            listener.stop()
        finally:
            werkzeug.serving.make_server = original

    def _probe_app(self):
        """A WSGI app that records the environ it was called with."""
        self._last_environ = {}

        def app(environ, start_response):
            self._last_environ = dict(environ)
            start_response("200 OK", [])
            return [b""]

        return app

    def test_loopback_cannot_be_chosen_as_the_remote_address(self):
        """Binding loopback remotely would tag remote requests as local."""
        listener = self.omni_remote.RemoteListener(object())
        for address in ("127.0.0.1", "127.1.2.3", " 127.0.0.1 "):
            with self.subTest(address=address):
                result = listener.start(address, 8099)
                self.assertFalse(result["ok"])
                self.assertIn("loopback", result["error"])
        self.assertFalse(listener.running)

    def test_remote_access_is_off_until_something_starts_it(self):
        """Default off, stated as a property of the object (section 28)."""
        listener = self.omni_remote.RemoteListener(object())
        self.assertFalse(listener.running)
        self.assertEqual(listener.describe(),
                         {"enabled": False, "host": None, "port": None,
                          "url": None, "scheme": None, "secure": False})

    def test_a_listener_that_is_not_running_never_claims_to_be_secure(self):
        """`secure` says what the transport is, so off must not read as secure."""
        listener = self.omni_remote.RemoteListener(object())
        self.assertFalse(listener.describe()["secure"])
        self.assertIsNone(listener.describe()["scheme"])

    def test_stopping_a_listener_that_never_ran_is_not_an_error(self):
        listener = self.omni_remote.RemoteListener(object())
        self.assertTrue(listener.stop()["ok"])

    def test_loopback_addresses_are_recognised_and_others_are_not(self):
        for address in ("127.0.0.1", "127.255.255.254"):
            self.assertTrue(self.omni_remote.is_loopback_address(address), address)
        for address in ("10.0.0.5", "192.168.1.10", "", "not-an-address", None):
            self.assertFalse(self.omni_remote.is_loopback_address(address), address)

    def test_bind_candidates_exclude_loopback_and_malformed_addresses(self):
        adapters = [{"address": "127.0.0.1", "name": "Loopback"},
                    {"address": "10.48.55.10", "name": "Ethernet", "cidr": "10.48.55.0/24"},
                    {"address": "", "name": "Empty"},
                    {"address": "not-an-address", "name": "Broken"}]
        usable = self.omni_remote.usable_bind_addresses(adapters)
        self.assertEqual([a["address"] for a in usable], ["10.48.55.10"])


class BindAddressIsAHostTests(unittest.TestCase):
    """A broadcast or multicast address parses and cannot be bound to.

    The guard refused loopback and the wildcard and accepted everything else
    that parsed, so it called 255.255.255.255 usable. Nothing reaches it
    through the product -- enabling also requires the address to be present on
    this machine -- but a guard whose name is a lie gets worked around later.
    """

    NOT_A_HOST = ("255.255.255.255", "224.0.0.1", "239.70.132.254",
                  "240.0.0.1", "0.0.0.0", "::", "127.0.0.1", "127.5.5.5",
                  "not-an-address", "", "   ")

    #: Anything an adapter on a real machine can actually have.
    A_HOST = ("203.0.113.5", "10.0.4.12", "172.29.16.1", "169.254.1.1",
              "198.51.100.7", "2001:db8::1")

    def test_nothing_that_cannot_be_bound_is_called_usable(self):
        for address in self.NOT_A_HOST:
            with self.subTest(address=address):
                self.assertFalse(omni_remote.is_usable_remote_address(address))

    def test_every_real_adapter_address_stays_usable(self):
        for address in self.A_HOST:
            with self.subTest(address=address):
                self.assertTrue(omni_remote.is_usable_remote_address(address))

    def test_the_multicast_range_is_refused_as_a_category(self):
        """Not as a list: the next reserved range must not need remembering."""
        for third in range(0, 255, 37):
            address = "239.70.%d.1" % third
            with self.subTest(address=address):
                self.assertFalse(omni_remote.is_usable_remote_address(address))

    def test_bind_candidates_drop_them_too(self):
        offered = omni_remote.usable_bind_addresses([
            {"ip": "203.0.113.5"}, {"ip": "255.255.255.255"},
            {"ip": "224.0.0.1"}, {"ip": "127.0.0.1"}, {"ip": "0.0.0.0"},
        ])
        self.assertEqual([a["address"] for a in offered], ["203.0.113.5"])


class ReconfiguringDoesNotBreakAWorkingListenerTests(unittest.TestCase):
    """Moving Remote Access to a bad address used to turn it off silently.

    `start()` stopped the running listener and only then asked werkzeug to
    bind. werkzeug answers a bind failure by printing to stderr and calling
    `sys.exit(1)`, so the failure arrived as SystemExit -- a BaseException,
    caught by neither `except OSError` nor `except Exception`. In the packaged
    application that killed the request thread: a dropped connection instead of
    a message, and remote access left off with nothing said.

    Nothing here opens a socket. What is under test is the order of operations,
    so `make_server` is stubbed and told when to fail; a test that bound a real
    interface would be testing this machine.
    """

    GOOD = ("198.51.100.10", 8443)
    BAD = ("198.51.100.99", 8443)

    def setUp(self):
        import werkzeug.serving

        self.listener = omni_remote.RemoteListener(lambda e, s: [b""])
        self.addCleanup(self.listener.stop)
        self.built = []

        class FakeServer:
            def __init__(self, host, port):
                self.host, self.port = host, port
                self.stopped = False

            def serve_forever(self):
                while not self.stopped:
                    time.sleep(0.01)

            def shutdown(self):
                self.stopped = True

            def server_close(self):
                self.stopped = True

        def fake_make_server(host, port, app, **kwargs):
            self.built.append((host, port))
            if (host, port) == self.BAD:
                # Exactly what werkzeug does with a bind it cannot perform.
                raise SystemExit(1)
            return FakeServer(host, port)

        self.real_make_server = werkzeug.serving.make_server
        werkzeug.serving.make_server = fake_make_server
        self.addCleanup(setattr, werkzeug.serving, "make_server",
                        self.real_make_server)

    def test_a_bind_that_cannot_happen_is_an_answer_not_an_exit(self):
        result = self.listener.start(*self.BAD)
        self.assertFalse(result["ok"])
        self.assertIn("cannot bind", result["error"])
        self.assertFalse(self.listener.running)

    def test_a_failed_reconfiguration_leaves_the_listener_running(self):
        self.assertTrue(self.listener.start(*self.GOOD)["ok"])
        self.assertTrue(self.listener.running)

        refused = self.listener.start(*self.BAD)
        self.assertFalse(refused["ok"])
        self.assertTrue(self.listener.running,
                        "a reconfiguration that cannot succeed must not take "
                        "down the listener that works")
        self.assertEqual(self.listener.bound, self.GOOD)

    def test_a_successful_reconfiguration_does_move(self):
        """The control: without it the test above passes by never moving."""
        self.assertTrue(self.listener.start(*self.GOOD)["ok"])
        moved = ("198.51.100.11", 8444)
        self.assertTrue(self.listener.start(*moved)["ok"])
        self.assertEqual(self.listener.bound, moved)

    def test_rebinding_the_same_address_stops_first(self):
        """The one case make-before-break cannot serve."""
        self.assertTrue(self.listener.start(*self.GOOD)["ok"])
        self.assertTrue(self.listener.start(*self.GOOD)["ok"])
        self.assertEqual(self.listener.bound, self.GOOD)
        self.assertEqual(self.built, [self.GOOD, self.GOOD])

    def test_the_new_listener_is_built_before_the_old_one_is_stopped(self):
        """Structural, so the ordering cannot be undone by a later edit."""
        source = (ROOT / "omni_remote.py").read_text(encoding="utf-8")
        body = source[source.index("def start("):]
        body = body[:body.index(chr(10) + "    def ")]
        code = chr(10).join(line.split("#", 1)[0] for line in body.splitlines())
        self.assertIn("except SystemExit:", code)
        built = code.index("server = make_server(")
        stopped = code.index("self.stop()", built)
        self.assertLess(built, stopped,
                        "the new listener must exist before the old one goes")

    def test_the_refusal_says_what_went_wrong(self):
        """An operator needs the reason, not "it did not work"."""
        result = self.listener.start(*self.BAD)
        self.assertGreater(len(result["error"]), len("cannot bind"))
        self.assertIn(str(self.BAD[1]), result["error"])


if __name__ == "__main__":
    unittest.main()
