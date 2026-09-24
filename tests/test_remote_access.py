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
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("OMNI_SMOKE", "1")

import omni_access                                   # noqa: E402
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
            {"/__health", "/api/login", "/license",
             "/static/<path:filename>", "/ui/<path:filename>"},
            "adding a route to the public surface is a security decision and "
            "has to be made deliberately here")

    def test_no_device_route_is_public(self):
        for rule, (access, _f) in omni_access.ROUTES.items():
            if rule.startswith("/api/") and rule != "/api/login":
                self.assertIsNot(
                    access, Access.PUBLIC,
                    f"{rule} would be callable with no credential")


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

    def test_the_user_surface_is_exactly_the_twelve_routing_routes(self):
        user_routes = {rule for rule, (access, _f) in omni_access.ROUTES.items()
                       if access is Access.USER}
        self.assertEqual(user_routes, {
            "/api/state", "/api/route",
            "/api/usb_state", "/api/usb_route/resolve",
            "/api/usb_route/pair", "/api/usb_route/unpair",
            "/api/multiview/state", "/api/multiview/decoders",
            "/api/multiview/sources", "/api/multiview/preview",
            "/api/multiview/show", "/api/multiview/switch",
        }, "widening what a restricted account may call is a deliberate "
           "security decision, not an implementation detail")

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

    def test_multiview_administration_is_not_granted_by_the_multiview_feature(self):
        """Operating an approved preset is not the same as preparing one.

        Multiview's own architecture already separates a preset from its
        execution; the permission boundary follows that split rather than
        cutting a new one.
        """
        operate = ("/api/multiview/show", "/api/multiview/switch")
        administer = ("/api/multiview/apply", "/api/multiview/copy",
                      "/api/multiview/delete", "/api/multiview/plan",
                      "/api/multiview/layouts/install",
                      "/api/multiview/groups/save",
                      "/api/multiview/groups/delete")
        for rule in operate:
            with self.subTest(operate=rule):
                self.assertIs(omni_access.classify(rule)[0], Access.USER)
        for rule in administer:
            with self.subTest(administer=rule):
                self.assertIs(omni_access.classify(rule)[0], Access.ADMIN)


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

    def test_a_request_with_no_tag_is_treated_as_remote(self):
        """The safe direction. An unwrapped server gets the restricted answer."""
        self.assertEqual(self.omni_remote.listener_of({}), self.omni_remote.REMOTE)
        self.assertFalse(self.omni_remote.is_local_request({}))

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
                         {"enabled": False, "host": None, "port": None, "url": None})

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


if __name__ == "__main__":
    unittest.main()
