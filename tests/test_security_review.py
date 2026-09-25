"""The final security pass over the whole branch.

Three questions, asked against the application as it now stands rather than
against what anyone remembers writing:

  * Is every route classified, and does the classification still match what the
    handler does?
  * Does the local trust boundary still rest on which listener accepted the
    connection, now that one of those listeners speaks TLS?
  * Can anything that makes the OmniSuite computer itself act be reached from
    the network?
"""
from __future__ import annotations

import ast
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
from omni_users import UserStore                     # noqa: E402
from omni_access import Access                       # noqa: E402
import OmniMatrix_upgrade_server_v7_6y as srv        # noqa: E402


def registered_rules() -> set:
    return {str(rule.rule) for rule in srv.app.url_map.iter_rules()}


class FinalInventoryTests(unittest.TestCase):
    """Every route, classified, with the totals stated."""

    def test_every_registered_route_is_classified(self):
        missing = sorted(registered_rules() - set(omni_access.ROUTES))
        self.assertEqual(missing, [],
                         "unclassified routes:\n  " + "\n  ".join(missing))

    def test_no_classification_describes_a_route_that_is_gone(self):
        extra = sorted(set(omni_access.ROUTES) - registered_rules())
        self.assertEqual(extra, [], "stale entries:\n  " + "\n  ".join(extra))

    def test_the_totals_are_what_the_branch_intends(self):
        """A count that moves without anybody noticing is the thing to catch.

        Not an arbitrary number: each class is a decision, and a route landing
        in a different one is a security change that should be deliberate.
        """
        counts = {}
        for _rule, (access, _feature) in omni_access.ROUTES.items():
            counts[access] = counts.get(access, 0) + 1
        self.assertEqual(counts[Access.PUBLIC], 6,
                         "the anonymous surface changed")
        self.assertEqual(counts[Access.USER], 6,
                         "what a restricted account may call changed")
        self.assertEqual(counts[Access.AUTHENTICATED], 9,
                         "what any signed-in principal may call changed")
        self.assertEqual(counts[Access.LOCAL_ONLY], 11,
                         "the local-host-only surface changed")
        self.assertEqual(counts[Access.ADMIN], 103,
                         "the administrative surface changed")
        self.assertEqual(sum(counts.values()), len(registered_rules()))
        self.assertEqual(sum(counts.values()), 135)

    def test_the_public_surface_is_still_only_what_signing_in_needs(self):
        public = {rule for rule, (access, _f) in omni_access.ROUTES.items()
                  if access is Access.PUBLIC}
        self.assertEqual(public, {
            "/__health", "/license", "/login", "/api/auth/login",
            "/static/<path:filename>", "/ui/<path:filename>"})

    def test_every_user_route_is_projected_and_namespaced(self):
        for rule, (access, feature) in omni_access.ROUTES.items():
            if access is Access.USER:
                with self.subTest(rule=rule):
                    self.assertTrue(rule.startswith("/api/user/"), rule)
                    self.assertIn(feature, omni_access.FEATURES, rule)


class HandlerAgreementTests(unittest.TestCase):
    """The gate is not sufficient on its own for handlers that translate.

    A handler that turns an identity into an address, or that acts on more than
    one resource, must check what it is about to use rather than trusting what
    the gate saw. These assert that each one does.
    """

    TRANSLATING = ("api_user_route", "api_user_usb_route",
                   "api_user_multiview_show", "api_user_multiview_switch")

    def setUp(self):
        self.tree = ast.parse(
            (ROOT / "omni_remote_api.py").read_text(encoding="utf-8"))
        self.functions = {n.name: n for n in ast.walk(self.tree)
                          if isinstance(n, ast.FunctionDef)}

    def test_every_translating_handler_re_reads_the_account(self):
        """Authorisation belongs at the operation boundary.

        Between the gate admitting a request and the handler acting, an
        administrator may have removed a permission or disabled the account.
        """
        for name in self.TRANSLATING:
            with self.subTest(handler=name):
                self.assertIn(name, self.functions)
                body = ast.dump(self.functions[name])
                self.assertIn("_current_account", body,
                              f"{name} trusts the account the gate resolved")

    def test_every_device_operation_holds_the_resource_it_touches(self):
        for name in self.TRANSLATING:
            with self.subTest(handler=name):
                body = ast.dump(self.functions[name])
                self.assertIn("locks", body,
                              f"{name} writes to equipment without serialising")

    def test_the_usb_handler_authorises_against_the_projection(self):
        """USB endpoint MACs are not device identities, so the gate cannot."""
        body = ast.dump(self.functions["api_user_usb_route"])
        self.assertIn("_usb_permitted_endpoints", body)


class LocalTrustAfterTlsTests(unittest.TestCase):
    """The invariant, re-checked now that a listener can speak TLS.

    It has produced two findings already: an untagged listener in the packaged
    launcher, and a guard that accepted 0.0.0.0 because it only refused
    loopback.
    """

    SPOOFABLE = ("HTTP_X_FORWARDED_FOR", "HTTP_X_REAL_IP", "HTTP_HOST",
                 "HTTP_X_FORWARDED_PROTO", "HTTP_FORWARDED", "HTTP_ORIGIN")

    def test_tls_does_not_change_how_local_trust_is_decided(self):
        """A TLS listener is still a remote listener."""
        for tagged, expected in ((omni_remote.LOCAL, True),
                                 (omni_remote.REMOTE, False)):
            with self.subTest(tag=tagged):
                environ = {omni_remote.LISTENER_KEY: tagged,
                           "wsgi.url_scheme": "https",
                           "HTTPS": "on"}
                self.assertEqual(omni_remote.is_local_request(environ), expected)

    def test_no_header_grants_local_trust_over_tls(self):
        environ = {omni_remote.LISTENER_KEY: omni_remote.REMOTE,
                   "wsgi.url_scheme": "https"}
        for header in self.SPOOFABLE:
            environ[header] = "127.0.0.1"
        environ["REMOTE_ADDR"] = "127.0.0.1"
        self.assertFalse(omni_remote.is_local_request(environ))
        decision = omni_gate.evaluate("/api/scan", "POST", environ, {}, None)
        self.assertFalse(decision.allowed)

    def test_every_listener_the_product_opens_is_still_tagged(self):
        """The guarantee untagged-means-local depends on."""
        import re
        for filename in ("OmniMatrix_upgrade_server_v7_6y.py", "app_launcher.py"):
            with self.subTest(filename=filename):
                source = (ROOT / filename).read_text(encoding="utf-8")
                code = "\n".join(line.split("#", 1)[0]
                                 for line in source.splitlines())
                self.assertNotIn("app.run(", code)
                self.assertEqual(re.findall(r"\bmake_server\(", code), [])

    def test_the_tls_module_takes_no_part_in_trust(self):
        tree = ast.parse((ROOT / "omni_tls.py").read_text(encoding="utf-8"))
        names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
        self.assertNotIn("request", names)
        self.assertNotIn("omni_gate", names)

    def test_a_routable_bind_is_still_never_tagged_local(self):
        for address in ("0.0.0.0", "10.48.55.10", "::", ""):
            with self.subTest(address=address):
                self.assertEqual(omni_remote.tag_for_bind(address),
                                 omni_remote.REMOTE)


class HostActingEndpointTests(unittest.TestCase):
    """Things that make the OmniSuite computer itself act.

    An administrator session is necessary for these and deliberately not
    sufficient: browsing the server's filesystem or opening a window on its
    desktop is not something to gain by signing in from a phone.
    """

    HOST_ACTING = {
        "/api/list_dir": "enumerates the host filesystem",
        "/api/files": "reads the firmware directory",
        "/api/open_firmware_folder": "opens a window on the desktop",
        "/api/debug/latest_upload": "reads an uploaded file from disk",
        "/api/cleanup_uploads": "deletes files on the host",
        "/api/diagnostics/usb_endpoints": "bench hardware validation",
        "/api/diagnostics/usb_route/state": "bench hardware validation",
        "/api/diagnostics/usb_route/pair": "bench hardware validation",
        "/api/diagnostics/usb_route/unpair": "bench hardware validation",
    }

    def test_every_host_acting_route_is_local_only(self):
        for rule, why in self.HOST_ACTING.items():
            with self.subTest(rule=rule):
                self.assertIs(omni_access.classify(rule)[0], Access.LOCAL_ONLY,
                              f"{rule} {why}")

    #: Also local-only, for a different reason: creating the first
    #: administrator establishes trust rather than acting on the host, and it
    #: is reachable only from the machine because a remote client must never be
    #: able to make itself one.
    BOOTSTRAP = {"/api/bootstrap/admin", "/api/bootstrap/state"}

    def test_the_local_only_set_is_exactly_what_is_intended(self):
        """Nothing has quietly joined or left it.

        Two reasons put a route here, and they are worth keeping distinct: it
        makes the host computer do something, or it establishes the first
        administrator. Anything else appearing is a decision somebody should
        have to make deliberately.
        """
        local_only = {rule for rule, (access, _f) in omni_access.ROUTES.items()
                      if access is Access.LOCAL_ONLY}
        self.assertEqual(local_only, set(self.HOST_ACTING) | self.BOOTSTRAP)

    def test_a_remote_administrator_is_refused_and_not_told_it_exists(self):
        store = _one_admin_store()
        account, session = store.resolve_session(_admin_session(store).token)
        environ = {omni_remote.LISTENER_KEY: omni_remote.REMOTE,
                   "HTTP_COOKIE": "omnisuite_session=" + session.token,
                   "HTTP_X_OMNISUITE_CSRF": session.csrf_token}
        for rule in self.HOST_ACTING:
            with self.subTest(rule=rule):
                decision = omni_gate.evaluate(rule, "POST", environ, {}, store)
                self.assertFalse(decision.allowed)
                self.assertEqual(decision.status, 404,
                                 "a host-only route should not confirm it exists")

    def test_the_local_operator_still_reaches_them(self):
        store = _one_admin_store()
        environ = {omni_remote.LISTENER_KEY: omni_remote.LOCAL}
        for rule in self.HOST_ACTING:
            with self.subTest(rule=rule):
                self.assertTrue(
                    omni_gate.evaluate(rule, "POST", environ, {}, store).allowed)


class KeyMaterialTests(unittest.TestCase):
    """A private key never leaves the host."""

    def test_no_api_returns_key_material(self):
        source = (ROOT / "omni_remote_api.py").read_text(encoding="utf-8")
        code = "\n".join(line.split("#", 1)[0] for line in source.splitlines())
        for forbidden in ("read_text()", "read_bytes()", "PRIVATE KEY"):
            self.assertNotIn(forbidden, code,
                             "the remote API must not read files back to a caller")

    def test_the_certificate_description_publishes_no_secret(self):
        import omni_tls
        tree = ast.parse((ROOT / "omni_tls.py").read_text(encoding="utf-8"))
        describe = next(n for n in ast.walk(tree)
                        if isinstance(n, ast.FunctionDef) and n.name == "describe")
        returned = ast.dump(describe)
        for forbidden in ("key_path", "private", "KEY_FILE"):
            self.assertNotIn(forbidden, returned,
                             "describe() must only ever see the certificate")

    def test_key_material_is_never_logged(self):
        import omni_tls
        source = (ROOT / "omni_tls.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "log"):
                rendered = ast.dump(node)
                for forbidden in ("key_bytes", "private_bytes", "key_path"):
                    self.assertNotIn(forbidden, rendered,
                                     "a private key reached a log call")


def _one_admin_store():
    import tempfile
    import omni_users
    from omni_users import UserStore
    directory = Path(tempfile.mkdtemp(prefix="omni-sec-"))
    store = UserStore(directory / "users.json")
    store.create("admin", "admin-password-1", omni_users.ROLE_ADMIN)
    return store


def _admin_session(store):
    account = store.find_by_username("admin")
    return store.open_session(account.id)


class DefaultDenyTests(unittest.TestCase):
    """The defaults nothing currently reaches.

    Every route is classified, so `classify()` never falls back and
    `evaluate()` never reaches its final return. That is what makes these
    worth testing directly: an unexercised default is one nobody would notice
    changing, and it is the thing that protects the route somebody adds next
    week.
    """

    def test_an_unclassified_route_is_local_only(self):
        access, feature = omni_access.classify("/api/not-a-real-route")
        self.assertIs(access, omni_access.Access.LOCAL_ONLY)
        self.assertIsNone(feature)

    def test_an_unclassified_route_is_not_reachable_from_the_network(self):
        decision = omni_gate.evaluate(
            "/api/route-added-next-week", "POST",
            {omni_remote.LISTENER_KEY: omni_remote.REMOTE}, {}, None)
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.status, 404)

    def test_an_unknown_access_class_denies_rather_than_falls_through(self):
        """The final return in evaluate(), which classify() cannot produce."""
        class Unknown:
            pass

        store = UserStore(Path(tempfile.mkdtemp(prefix="omni-deny-")) / "u.json")
        store.create("someone", "someone-password-1", omni_users.ROLE_ADMIN)
        result = store.authenticate("someone", "someone-password-1")
        session = store._sessions[result["token"]]

        original = omni_access.classify
        omni_access.classify = lambda rule: (Unknown(), None)
        try:
            decision = omni_gate.evaluate(
                "/api/anything", "GET",
                {omni_remote.LISTENER_KEY: omni_remote.REMOTE,
                 "HTTP_COOKIE": "omnisuite_session=" + session.token},
                {}, store)
        finally:
            omni_access.classify = original

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.status, 403)
        self.assertEqual(decision.reason, "unclassified")


if __name__ == "__main__":
    unittest.main()
