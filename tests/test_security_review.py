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
import re
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
        # Two added in the remote-browser fix, both deliberate:
        #   /favicon.ico      one file from the build at a fixed path, which
        #                     every browser asks for before it has a session
        #   /access/<token>   a Direct Access link. The token in the path IS
        #                     the credential, so requiring a session to redeem
        #                     it would defeat the feature; the handler refuses
        #                     anything it cannot resolve to an enabled
        #                     restricted account.
        self.assertEqual(counts[Access.PUBLIC], 8,
                         "the anonymous surface changed")
        # /api/user/source_preview added: a still picture of a permitted
        # source, addressed by stable identity so no device address reaches a
        # restricted client, and refused for a source the account may not use.
        # /api/user/unroute added: putting a permitted display into the
        # verified unrouted state. Same feature and the same destination check
        # as routing to it; it names no source because it subscribes to none.
        self.assertEqual(counts[Access.USER], 8,
                         "what a restricted account may call changed")
        # Three moved to ADMIN in the release-candidate audit: the update
        # alert, its acknowledgement and the appearance reset all write or
        # read state belonging to the installation rather than to an account.
        # /api/user/direct_access added: the caller's own link and nobody
        # else's. It takes no user id, so there is no account to aim it at.
        self.assertEqual(counts[Access.AUTHENTICATED], 7,
                         "what any signed-in principal may call changed")
        # Three added for configuration backup and disaster restore. They are
        # local-only because the backup carries the device credentials --
        # which /api/config already refuses to anything but this host -- and
        # because a restore replaces every account on the installation. The
        # machine's own operator is the recovery authority, which is also what
        # lets a technician who never knew the old administrator's password
        # rebuild a deployment.
        self.assertEqual(counts[Access.LOCAL_ONLY], 14,
                         "the local-host-only surface changed")
        # /api/admin/users/<user_id>/direct_access added: turning a Direct
        # Access link on, off, or replacing it. Administrative in both
        # directions -- a restricted account may read its own link and may
        # never create one.
        # /api/unroute added: the engine endpoint behind both the A/V Matrix's
        # and the restricted shell's unroute, so there is one implementation.
        # /api/admin/credentials added: which credential each device is
        # actually answering to, by name -- never a value.
        self.assertEqual(counts[Access.ADMIN], 110,
                         "the administrative surface changed")
        self.assertEqual(sum(counts.values()), len(registered_rules()))
        self.assertEqual(sum(counts.values()), 147)

    def test_the_public_surface_is_still_only_what_signing_in_needs(self):
        public = {rule for rule, (access, _f) in omni_access.ROUTES.items()
                  if access is Access.PUBLIC}
        self.assertEqual(public, {
            "/__health", "/license", "/login", "/api/auth/login",
            "/static/<path:filename>", "/ui/<path:filename>",
            "/favicon.ico", "/access/<token>"})

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
                   "api_user_multiview_show", "api_user_multiview_switch",
                   )

    #: Handlers that translate an identity into an address for a READ.
    #:
    #: They re-check authorisation for the same reason the operations do --
    #: the check has to be on the value the translation will use -- but they
    #: must NOT take a resource lock. Locks serialise operations against
    #: equipment; a read that queued behind a route would make looking at a
    #: source card wait on somebody else's routing.
    TRANSLATING_READS = ("api_user_source_preview",)

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

    def test_every_translating_read_re_checks_too(self):
        """A read reaches a device, so it is authorised at the same boundary."""
        for name in self.TRANSLATING_READS:
            with self.subTest(handler=name):
                self.assertIn(name, self.functions)
                body = ast.dump(self.functions[name])
                self.assertIn("_revalidate", body,
                              f"{name} trusts the account the gate resolved")
                self.assertIn("may_use_source", body,
                              f"{name} does not check the grant it translates")

    def test_a_translating_read_takes_no_lock(self):
        """Observation takes nothing. A preview must never wait on a route."""
        for name in self.TRANSLATING_READS:
            with self.subTest(handler=name):
                body = ast.dump(self.functions[name])
                self.assertNotIn("locks", body,
                                 f"{name} serialises a read against equipment")

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

    #: Disaster recovery. A third reason, kept separate from the other two
    #: because it is a different argument: the backup carries the device
    #: credentials, which /api/config already refuses to anything but this
    #: host, and a restore replaces every account on the installation. The
    #: machine's own operator is the recovery authority -- which is also what
    #: lets a technician who never knew the old administrator's password
    #: rebuild a deployment at all.
    RECOVERY = {"/api/backup/export", "/api/backup/inspect",
                "/api/backup/restore"}

    def test_the_local_only_set_is_exactly_what_is_intended(self):
        """Nothing has quietly joined or left it.

        Three reasons put a route here, and they are worth keeping distinct:
        it makes the host computer do something, it establishes the first
        administrator, or it is how a deployment is rebuilt after the PC it
        lived on failed. Anything else appearing is a decision somebody should
        have to make deliberately.
        """
        local_only = {rule for rule, (access, _f) in omni_access.ROUTES.items()
                      if access is Access.LOCAL_ONLY}
        self.assertEqual(local_only,
                         set(self.HOST_ACTING) | self.BOOTSTRAP | self.RECOVERY)

    def test_a_remote_caller_is_not_told_the_recovery_routes_exist(self):
        """The same treatment the host-acting routes get."""
        store = _one_admin_store()
        _account, session = store.resolve_session(_admin_session(store).token)
        environ = {omni_remote.LISTENER_KEY: omni_remote.REMOTE,
                   "HTTP_COOKIE": "omnisuite_session=" + session.token,
                   "HTTP_X_OMNISUITE_CSRF": session.csrf_token}
        for rule in self.RECOVERY:
            with self.subTest(rule=rule):
                decision = omni_gate.evaluate(rule, "POST", environ, {}, store)
                self.assertFalse(decision.allowed)
                self.assertEqual(decision.status, 404)

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
        omni_access.classify = lambda rule, method=None: (Unknown(), None)
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


class ActivityLabelTests(unittest.TestCase):
    """No internal event name may reach the Activity view.

    Derived from the source rather than from a list kept by hand: a list would
    have been written from the same literals that missed these two.
    """

    #: api_admin_user_action used to turn its `action` into "user.<action>",
    #: producing ids that appeared nowhere as a literal -- which is how two of
    #: them came to have no label. That layer no longer records anything: the
    #: store emits one event per action, saying what changed rather than which
    #: verb was asked for. Nothing is concatenated any more, and this fence
    #: exists to notice if something starts again.
    CONCATENATED = ()

    @staticmethod
    def _emitted() -> set:
        """Every literal event id the application passes to audit()."""
        found = set()
        for path in sorted(Path(ROOT).glob("omni_*.py")) + \
                [Path(ROOT) / "OmniMatrix_upgrade_server_v7_6y.py"]:
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                name = getattr(node.func, "attr", None)
                if name != "audit" or not node.args:
                    continue
                first = node.args[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    found.add(first.value)
                elif isinstance(first, ast.IfExp):
                    for branch in (first.body, first.orelse):
                        if isinstance(branch, ast.Constant):
                            found.add(branch.value)
        return found

    @staticmethod
    def _labelled() -> set:
        source = (Path(ROOT) / "ui" / "admin" / "users.js").read_text(
            encoding="utf-8")
        start = source.index("const EVENT_LABELS = {")
        block = source[start:source.index("};", start)]
        return set(re.findall(r"'([a-z_][a-z_.]*)'\s*:", block))

    def test_every_emitted_event_has_a_label(self):
        labelled = self._labelled()
        self.assertTrue(labelled, "the label map could not be read")
        expected = self._emitted() | {
            "user." + action for action in self.CONCATENATED}
        missing = sorted(event for event in expected if event not in labelled)
        self.assertEqual(missing, [],
                         "these reach the Activity view as internal names")

    def test_no_event_name_is_built_by_concatenation(self):
        """That is how two of them came to have no label.

        A name assembled at the call site appears nowhere as a literal, so
        nobody writing the map from the literals can see it.
        """
        source = (Path(ROOT) / "omni_remote_api.py").read_text(encoding="utf-8")
        self.assertNotIn('audit("user." +', source)
        self.assertNotIn("audit('user.' +", source)

    def test_an_unmapped_event_is_still_rendered_as_english(self):
        """The fallback, for the event somebody adds after this test was written."""
        source = (Path(ROOT) / "ui" / "admin" / "users.js").read_text(
            encoding="utf-8")
        self.assertIn("function eventLabel(", source)
        # The raw id must not be the fallback anywhere it is displayed.
        for line in source.splitlines():
            stripped = line.split("//")[0]
            self.assertNotIn("EVENT_LABELS[entry.event] || entry.event", stripped)

    def test_the_labels_are_not_themselves_internal_names(self):
        source = (Path(ROOT) / "ui" / "admin" / "users.js").read_text(
            encoding="utf-8")
        start = source.index("const EVENT_LABELS = {")
        block = source[start:source.index("};", start)]
        for event, label in re.findall(r"'([a-z_.]+)'\s*:\s*'([^']+)'", block):
            with self.subTest(event=event):
                self.assertNotIn(".", label)
                self.assertNotIn("_", label)
                self.assertNotEqual(label, event)


class InstallationSettingsAreAdministrativeTests(unittest.TestCase):
    """Global files, one per installation, not one per account.

    `ui_preferences.json` and `notice_ack.json` belong to the machine. They
    were classified AUTHENTICATED when remote access was built, which was
    right while the only authenticated caller was the local operator, and
    became wrong the moment restricted Users existed: a signed-in classroom
    account could restyle the administrator's interface and dismiss their
    update alert.

    The read of the theme stays open, because the User Guide is offered at
    /help and reads it to paint itself.
    """

    WRITES = ("/api/ui_preferences/reset", "/api/notices/ack")
    READS = ("/api/notices",)

    def test_writing_an_installation_setting_is_administrative(self):
        for rule in self.WRITES:
            with self.subTest(rule=rule):
                access, _feature = omni_access.classify(rule, "POST")
                self.assertIs(access, omni_access.Access.ADMIN)

    def test_the_update_alert_is_administrative(self):
        for rule in self.READS:
            with self.subTest(rule=rule):
                access, _feature = omni_access.classify(rule, "GET")
                self.assertIs(access, omni_access.Access.ADMIN)

    def test_the_theme_may_be_read_by_anyone_signed_in(self):
        """The User Guide is offered to a restricted account and reads this."""
        access, _feature = omni_access.classify("/api/ui_preferences", "GET")
        self.assertIs(access, omni_access.Access.AUTHENTICATED)

    def test_but_writing_the_theme_is_not(self):
        access, _feature = omni_access.classify("/api/ui_preferences", "POST")
        self.assertIs(access, omni_access.Access.ADMIN)

    def test_a_user_is_refused_the_write_and_allowed_the_read(self):
        store = UserStore(Path(tempfile.mkdtemp(prefix="omni-prefs-")) / "u.json")
        store.create("teacher", "teacher-password-1", omni_users.ROLE_USER)
        teacher = store.find_by_username("teacher")
        session = store.open_session(teacher.id)
        env = {omni_remote.LISTENER_KEY: omni_remote.REMOTE,
               "HTTP_COOKIE": "omnisuite_session=" + session.token,
               "HTTP_X_OMNISUITE_CSRF": session.csrf_token}

        write = omni_gate.evaluate("/api/ui_preferences", "POST", env,
                                   {"template": "compact"}, store)
        self.assertFalse(write.allowed)
        self.assertEqual(write.status, 403)

        read = omni_gate.evaluate("/api/ui_preferences", "GET", env, {}, store)
        self.assertTrue(read.allowed)

    def test_the_local_operator_is_unaffected(self):
        """None of this touches the machine OmniSuite is running on."""
        for rule in self.WRITES + self.READS + ("/api/ui_preferences",):
            with self.subTest(rule=rule):
                decision = omni_gate.evaluate(rule, "POST", {}, {}, None)
                self.assertTrue(decision.allowed)

    def test_method_specific_entries_stay_rare_and_deliberate(self):
        """A rule needing one is usually a rule that should have been two."""
        self.assertLessEqual(len(omni_access.METHOD_ROUTES), 3)
        for (method, rule) in omni_access.METHOD_ROUTES:
            with self.subTest(rule=rule):
                self.assertIn(method, ("GET", "POST", "PUT", "DELETE"))
                self.assertIn(rule, omni_access.ROUTES,
                              "a method override must narrow a classified rule")

    def test_a_method_override_is_never_more_permissive(self):
        order = [omni_access.Access.LOCAL_ONLY, omni_access.Access.ADMIN,
                 omni_access.Access.USER, omni_access.Access.AUTHENTICATED,
                 omni_access.Access.PUBLIC]
        for (method, rule), (access, _f) in omni_access.METHOD_ROUTES.items():
            with self.subTest(rule=rule, method=method):
                base, _bf = omni_access.ROUTES[rule]
                self.assertLessEqual(order.index(access), order.index(base),
                                     "an override must not widen a rule")


if __name__ == "__main__":
    unittest.main()
