"""A person opening the remote URL gets a sign-in page; a program gets JSON.

The defect these exist for: opening the advertised HTTPS address in Chrome
answered with

    {"error": "authentication required", "ok": false}

as raw text on a white page. The gate was right about the refusal and wrong
about how to say it -- it had one answer for a browser and a program alike, and
the browser is the one an operator actually uses.

The fix must not be "redirect every refusal", because the API contract is what
every page on the other side of the sign-in depends on. So the discrimination
itself is under test here as much as the redirect is: a `fetch()` of a page path
still has to receive JSON, and every `/api/` route has to receive JSON whatever
headers arrive with it.

Nothing here touches a network. The listener is tagged remote by putting the
listener key in the environ, which is exactly what the real listener does.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("OMNI_SMOKE", "1")

import omni_gate                                        # noqa: E402
import omni_remote                                      # noqa: E402
import omni_users                                       # noqa: E402
from omni_users import Permissions                      # noqa: E402

#: What a browser actually sends when a person types an address and presses
#: enter. Captured from Chrome rather than invented.
NAVIGATION = {
    "HTTP_ACCEPT": ("text/html,application/xhtml+xml,application/xml;q=0.9,"
                    "image/avif,image/webp,image/apng,*/*;q=0.8"),
    "HTTP_SEC_FETCH_MODE": "navigate",
    "HTTP_SEC_FETCH_DEST": "document",
}

#: And what the same browser sends for a fetch() from a page.
XHR = {
    "HTTP_ACCEPT": "*/*",
    "HTTP_SEC_FETCH_MODE": "cors",
    "HTTP_SEC_FETCH_DEST": "empty",
}


class RemoteBrowserTestBase(unittest.TestCase):
    """The real application, reached as though from the network."""

    def setUp(self):
        import OmniMatrix_upgrade_server_v7_6y as srv
        self.srv = srv
        self.dir = Path(tempfile.mkdtemp(prefix="omni-browser-"))

        # The API captured this store object when it registered its routes, so
        # the module attribute cannot be substituted. Redirect what it reads
        # and put back everything it held.
        self.store = srv._user_store
        # `_failures` is in here deliberately. It is the sign-in throttle, it
        # is keyed by (name, source address), and it is as process-global as
        # the accounts are -- so a test that deliberately provokes refusals
        # locks the address out for every test that runs after it, and those
        # tests fail somewhere that has nothing to do with what they broke.
        saved = (self.store._path, self.store._audit_path,
                 dict(self.store._accounts), dict(self.store._sessions),
                 list(self.store._audit), dict(self.store._failures))

        def restore():
            self.store._path, self.store._audit_path = saved[0], saved[1]
            for held, original in ((self.store._accounts, saved[2]),
                                   (self.store._sessions, saved[3]),
                                   (self.store._failures, saved[5])):
                held.clear()
                held.update(original)
            self.store._audit[:] = saved[4]

        self.addCleanup(restore)
        self.store._path = self.dir / "users.json"
        self.store._audit_path = self.dir / "audit.json"
        self.store._accounts.clear()
        self.store._sessions.clear()
        self.store._audit.clear()
        self.store._failures.clear()

        # The gate refuses every remote request while remote access is off,
        # which is right and is not what these tests are about. `running` is
        # "the listener has a server", so a placeholder says it is up without
        # opening a socket.
        listener = srv._remote_listener
        previous = listener._server
        self.addCleanup(lambda: setattr(listener, "_server", previous))
        listener._server = object()

        self.client = srv.app.test_client()
        self.store.create("principal", "principal-pass-1", omni_users.ROLE_ADMIN)
        self.store.create("teacher", "teacher-password-1", omni_users.ROLE_USER,
                          Permissions(features=("av",),
                                      sources=["B8:98:B0:07:85:C6"],
                                      destinations=["B8:98:B0:06:41:B3"]))
        self.admin_account = self.store.find_by_username("principal")
        self.user_account = self.store.find_by_username("teacher")

    # -- helpers -------------------------------------------------------
    def remote(self, path, *, method="GET", headers=None, extra=None,
               json=None, session=None):
        base = {omni_remote.LISTENER_KEY: omni_remote.REMOTE}
        base.update(extra or NAVIGATION)
        if session is not None:
            self.client.set_cookie(omni_gate.SESSION_COOKIE, session.token)
        kwargs = {"environ_base": base, "headers": dict(headers or {})}
        if json is not None:
            kwargs["json"] = json
            if session is not None:
                kwargs["headers"]["X-OmniSuite-CSRF"] = session.csrf_token
        return getattr(self.client, method.lower())(path, **kwargs)

    def sign_in(self, username, password):
        """Through the endpoint the sign-in page actually posts to."""
        return self.client.post(
            "/api/auth/login", json={"username": username, "password": password},
            environ_base={omni_remote.LISTENER_KEY: omni_remote.REMOTE,
                          **XHR})


class UnauthenticatedNavigationTests(RemoteBrowserTestBase):
    """A person is given a page. This is the reported defect."""

    def test_the_root_url_sends_a_browser_to_the_sign_in_page(self):
        response = self.remote("/")
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers["Location"].startswith("/login"))

    def test_the_root_url_does_not_answer_a_browser_with_json(self):
        """Stated separately because this is the exact thing that was seen."""
        response = self.remote("/")
        body = response.get_data(as_text=True)
        self.assertNotIn("authentication required", body)
        self.assertNotIn("application/json", response.headers.get("Content-Type", ""))

    def test_a_protected_page_sends_a_browser_to_the_sign_in_page(self):
        for path in ("/user", "/matrix", "/matrix/usb", "/admin/users"):
            with self.subTest(path=path):
                response = self.remote(path)
                self.assertEqual(response.status_code, 302)
                self.assertTrue(
                    response.headers["Location"].startswith("/login?next="))

    def test_the_sign_in_page_is_told_where_the_browser_was_going(self):
        from urllib.parse import parse_qs, urlparse
        response = self.remote("/matrix/usb?tab=pairs")
        target = urlparse(response.headers["Location"])
        self.assertEqual(target.path, "/login")
        self.assertEqual(parse_qs(target.query)["next"],
                         ["/matrix/usb?tab=pairs"])

    def test_the_sign_in_page_itself_is_reachable_with_no_session(self):
        response = self.remote("/login")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Sign in", response.get_data(as_text=True))


class ApiKeepsItsContractTests(RemoteBrowserTestBase):
    """A program is given a status code and JSON. Nothing here may become HTML."""

    def test_an_unauthenticated_api_call_is_401_json(self):
        for path in ("/api/state", "/api/user/state", "/api/admin/users",
                     "/api/remote_access"):
            with self.subTest(path=path):
                response = self.remote(path, extra=XHR)
                self.assertEqual(response.status_code, 401)
                self.assertEqual(response.get_json()["error"],
                                 "authentication required")

    def test_an_api_call_stays_json_even_when_it_claims_to_want_html(self):
        """The discrimination is on the route, not only on a header."""
        response = self.remote("/api/state", extra={**NAVIGATION})
        self.assertEqual(response.status_code, 401)
        self.assertIsNotNone(response.get_json())

    def test_a_fetch_of_a_page_path_is_still_json(self):
        """A page's own script calling a page URL must not be redirected."""
        response = self.remote("/user", extra=XHR)
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.get_json()["error"], "authentication required")

    def test_a_post_is_never_redirected(self):
        response = self.remote("/api/route", method="POST", extra=NAVIGATION,
                               json={"encoder": "x", "decoder": "y"})
        self.assertEqual(response.status_code, 401)
        self.assertIsNotNone(response.get_json())


class PageVersusApiDiscriminationTests(unittest.TestCase):
    """The rule itself, exercised directly so a failure names the input."""

    def test_a_navigation_to_a_page_wants_a_page(self):
        self.assertTrue(omni_gate.wants_a_page("/", "GET", dict(NAVIGATION)))

    def test_a_navigation_to_an_api_route_does_not(self):
        self.assertFalse(
            omni_gate.wants_a_page("/api/state", "GET", dict(NAVIGATION)))

    def test_a_fetch_does_not(self):
        self.assertFalse(omni_gate.wants_a_page("/user", "GET", dict(XHR)))

    def test_a_write_never_does(self):
        for method in ("POST", "PUT", "PATCH", "DELETE"):
            with self.subTest(method=method):
                self.assertFalse(
                    omni_gate.wants_a_page("/", method, dict(NAVIGATION)))

    def test_an_old_client_is_judged_by_what_it_accepts(self):
        """No Sec-Fetch-Mode: the Accept header is the only evidence there is."""
        self.assertTrue(omni_gate.wants_a_page(
            "/", "GET", {"HTTP_ACCEPT": "text/html,*/*;q=0.8"}))
        self.assertFalse(omni_gate.wants_a_page(
            "/", "GET", {"HTTP_ACCEPT": "application/json"}))
        self.assertFalse(omni_gate.wants_a_page("/", "GET", {}))

    def test_an_accept_naming_both_prefers_what_it_asked_for_first(self):
        self.assertFalse(omni_gate.wants_a_page(
            "/", "GET", {"HTTP_ACCEPT": "application/json, text/html"}))
        self.assertTrue(omni_gate.wants_a_page(
            "/", "GET", {"HTTP_ACCEPT": "text/html, application/json"}))


class LoginFlowTests(RemoteBrowserTestBase):
    """The whole of what a person does, in order."""

    def test_an_administrator_signs_in_and_reaches_the_application(self):
        response = self.sign_in("principal", "principal-pass-1")
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["user"]["role"], "admin")
        self.assertTrue(body["csrf_token"])
        # And a session for that account opens an administrative page.
        page = self.remote("/", session=self._session_of("principal"))
        self.assertEqual(page.status_code, 200)

    def test_a_restricted_user_signs_in_and_reaches_the_shell(self):
        response = self.sign_in("teacher", "teacher-password-1")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["user"]["role"], "user")
        page = self.remote("/user", session=self._session_of("teacher"))
        self.assertEqual(page.status_code, 200)

    def test_a_restricted_user_is_refused_an_administrative_page_readably(self):
        """Not raw JSON: this is a browser, and it is the same class of defect."""
        page = self.remote("/matrix", session=self._session_of("teacher"))
        self.assertEqual(page.status_code, 403)
        body = page.get_data(as_text=True)
        self.assertIn("text/html", page.headers.get("Content-Type", ""))
        self.assertNotIn("not permitted\"", body)
        self.assertIn("<h1>", body)

    def test_a_wrong_password_is_refused_and_says_nothing_useful(self):
        response = self.sign_in("principal", "not-the-password")
        self.assertEqual(response.status_code, 401)
        self.assertFalse(response.get_json()["ok"])

    def test_a_disabled_account_cannot_sign_in(self):
        self.store.set_enabled(self.user_account.id, False)
        response = self.sign_in("teacher", "teacher-password-1")
        self.assertEqual(response.status_code, 401)
        self.assertFalse(response.get_json()["ok"])

    def test_signing_out_ends_the_session(self):
        session = self._session_of("teacher")
        out = self.remote("/api/auth/logout", method="POST", extra=XHR,
                          json={}, session=session)
        self.assertEqual(out.status_code, 200)
        # The page now behaves exactly as it did before signing in.
        page = self.remote("/user", session=session)
        self.assertEqual(page.status_code, 302)
        self.assertTrue(page.headers["Location"].startswith("/login"))

    def test_a_revoked_session_returns_the_browser_to_the_sign_in_page(self):
        session = self._session_of("teacher")
        self.assertEqual(self.remote("/user", session=session).status_code, 200)
        self.store.revoke(session.token)
        page = self.remote("/user", session=session)
        self.assertEqual(page.status_code, 302)
        self.assertTrue(page.headers["Location"].startswith("/login"))
        # And the API it would have called says so in JSON.
        call = self.remote("/api/user/state", extra=XHR, session=session)
        self.assertEqual(call.status_code, 401)

    def _session_of(self, username):
        account = self.store.find_by_username(username)
        return self.store.open_session(account.id)


class SignInPageAssetsTests(RemoteBrowserTestBase):
    """Everything the sign-in page needs, with no session at all."""

    #: Exactly what login.html references, read from the page rather than
    #: listed here, so adding a reference to it fails this until it is
    #: classified.
    def _referenced(self):
        page = (ROOT / "ui" / "login.html").read_text(encoding="utf-8")
        import re
        return sorted(set(re.findall(r'(?:href|src)="(/[^"]+)"', page)))

    def test_every_asset_the_sign_in_page_references_loads_unauthenticated(self):
        referenced = self._referenced()
        self.assertTrue(referenced, "the sign-in page should reference assets")
        for path in referenced:
            with self.subTest(path=path):
                response = self.remote(path, extra=XHR)
                self.assertEqual(response.status_code, 200,
                                 f"{path} is not reachable before signing in")

    def test_the_favicon_is_served(self):
        """A browser asks for this whether or not a page links to it."""
        response = self.remote("/favicon.ico", extra=XHR)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_data())

    def test_the_favicon_is_served_locally_too(self):
        response = self.client.get(
            "/favicon.ico",
            environ_base={omni_remote.LISTENER_KEY: omni_remote.LOCAL})
        self.assertEqual(response.status_code, 200)

    def test_the_public_assets_are_not_a_way_into_anything_else(self):
        """Static assets are not an authorization bypass."""
        for path in ("/ui/settings.js", "/ui/matrix/index.html"):
            with self.subTest(path=path):
                self.assertEqual(self.remote(path, extra=XHR).status_code, 200)
        # But the data those pages read is not public.
        for path in ("/api/state", "/api/config", "/api/admin/users"):
            with self.subTest(path=path):
                self.assertEqual(self.remote(path, extra=XHR).status_code, 401)


class DirectAccessOverHttpTests(RemoteBrowserTestBase):
    """The whole exchange, through the routes a browser actually calls."""

    def setUp(self):
        super().setUp()
        self.admin_session = self.store.open_session(self.admin_account.id)
        self.token = self.store.enable_direct_access(
            self.user_account.id, actor=self.admin_account)["token"]

    def open_link(self, token):
        """As a browser with no cookies at all would."""
        self.client.delete_cookie(omni_gate.SESSION_COOKIE)
        return self.client.get(
            "/access/" + token,
            environ_base={omni_remote.LISTENER_KEY: omni_remote.REMOTE,
                          **NAVIGATION})

    def test_a_valid_link_opens_the_restricted_shell_with_no_password(self):
        response = self.open_link(self.token)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/user")
        self.assertIn(omni_gate.SESSION_COOKIE,
                      response.headers.get("Set-Cookie", ""))
        # And the shell it was sent to opens on that cookie.
        self.assertEqual(self.client.get(
            "/user", environ_base={omni_remote.LISTENER_KEY: omni_remote.REMOTE,
                                   **NAVIGATION}).status_code, 200)

    def test_the_token_is_gone_from_the_address_the_browser_ends_on(self):
        """It is a credential; it must not sit in an address bar or a history."""
        response = self.open_link(self.token)
        self.assertNotIn(self.token, response.headers["Location"])
        self.assertNotIn(self.token, response.get_data(as_text=True))

    def test_the_session_it_opens_knows_how_it_was_established(self):
        self.open_link(self.token)
        state = self.client.get(
            "/api/auth/session",
            environ_base={omni_remote.LISTENER_KEY: omni_remote.REMOTE, **XHR})
        body = state.get_json()
        self.assertEqual(body["method"], omni_users.BY_DIRECT_LINK)
        # And it is handed the CSRF token, because it never saw the sign-in
        # page that would otherwise have given it one.
        self.assertTrue(body["csrf_token"])

    def test_an_invalid_link_says_only_that_it_is_not_valid(self):
        for wrong in ("not-a-token", self.token[:-1], self.token + "x"):
            with self.subTest(token=wrong):
                response = self.open_link(wrong)
                self.assertEqual(response.status_code, 404)
                body = response.get_data(as_text=True)
                self.assertIn("no longer valid", body)
                for leaked in ("teacher", "principal", "disabled", "admin"):
                    self.assertNotIn(leaked, body.lower())

    def test_a_disabled_account_link_is_refused_the_same_way(self):
        self.store.set_enabled(self.user_account.id, False,
                               actor=self.admin_account)
        response = self.open_link(self.token)
        self.assertEqual(response.status_code, 404)
        self.assertNotIn("disabled", response.get_data(as_text=True).lower())

    def test_a_regenerated_link_stops_the_old_url_working(self):
        replacement = self.store.regenerate_direct_access(
            self.user_account.id, actor=self.admin_account)["token"]
        self.assertEqual(self.open_link(self.token).status_code, 404)
        self.assertEqual(self.open_link(replacement).status_code, 302)

    def test_a_direct_session_cannot_reach_an_administrative_endpoint(self):
        self.open_link(self.token)
        environ = {omni_remote.LISTENER_KEY: omni_remote.REMOTE, **XHR}
        for path in ("/api/admin/users", "/api/state", "/api/scan",
                     "/api/config", "/api/remote_access", "/api/admin/audit"):
            with self.subTest(path=path):
                response = self.client.get(path, environ_base=environ)
                self.assertIn(response.status_code, (401, 403, 404), path)
                self.assertNotEqual(response.status_code, 200, path)

    def test_a_direct_session_cannot_reach_an_administrative_page(self):
        self.open_link(self.token)
        environ = {omni_remote.LISTENER_KEY: omni_remote.REMOTE, **NAVIGATION}
        for path in ("/", "/matrix", "/admin/users"):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path, environ_base=environ)
                                 .status_code, 403, path)

    def test_a_direct_session_still_needs_a_csrf_token(self):
        self.open_link(self.token)
        response = self.client.post(
            "/api/user/route", json={"source": "x", "destination": "y"},
            environ_base={omni_remote.LISTENER_KEY: omni_remote.REMOTE, **XHR})
        self.assertEqual(response.status_code, 403)

    def test_the_owner_can_read_their_own_link_after_a_password_login(self):
        session = self.store.open_session(self.user_account.id)
        self.client.set_cookie(omni_gate.SESSION_COOKIE, session.token)
        response = self.client.get(
            "/api/user/direct_access",
            environ_base={omni_remote.LISTENER_KEY: omni_remote.REMOTE, **XHR})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["direct_access"]["enabled"])

    def test_that_endpoint_answers_about_the_caller_and_nobody_else(self):
        """There is no user id to aim it at, and an administrator has none."""
        self.client.set_cookie(omni_gate.SESSION_COOKIE,
                               self.admin_session.token)
        response = self.client.get(
            "/api/user/direct_access",
            environ_base={omni_remote.LISTENER_KEY: omni_remote.REMOTE, **XHR})
        body = response.get_json()
        self.assertFalse(body["direct_access"]["enabled"])
        self.assertEqual(body["link"], "")

    def test_a_restricted_account_cannot_turn_the_feature_on_for_itself(self):
        session = self.store.open_session(self.user_account.id)
        self.client.set_cookie(omni_gate.SESSION_COOKIE, session.token)
        response = self.client.post(
            "/api/admin/users/" + self.user_account.id + "/direct_access",
            json={"action": "enable"},
            environ_base={omni_remote.LISTENER_KEY: omni_remote.REMOTE, **XHR},
            headers={"X-OmniSuite-CSRF": session.csrf_token})
        self.assertEqual(response.status_code, 403)

    def test_an_administrator_account_is_refused_a_link(self):
        self.client.set_cookie(omni_gate.SESSION_COOKIE,
                               self.admin_session.token)
        response = self.client.post(
            "/api/admin/users/" + self.admin_account.id + "/direct_access",
            json={"action": "enable"},
            environ_base={omni_remote.LISTENER_KEY: omni_remote.REMOTE, **XHR},
            headers={"X-OmniSuite-CSRF": self.admin_session.csrf_token})
        self.assertEqual(response.status_code, 400)
        self.assertIn("restricted", response.get_json()["error"])

    def _cookie_over(self, scheme):
        self.client.delete_cookie(omni_gate.SESSION_COOKIE)
        response = self.client.get(
            "/access/" + self.token, base_url=scheme + "://omnisuite.invalid/",
            environ_base={omni_remote.LISTENER_KEY: omni_remote.REMOTE,
                          **NAVIGATION})
        self.assertEqual(response.status_code, 302)
        return " ".join(response.headers.get_all("Set-Cookie"))

    def test_the_cookie_a_link_sets_is_secure_over_tls(self):
        """The link exchanges a bearer credential for this cookie.

        Asserted separately from the sign-in endpoint's cookie because it is a
        second `set_cookie` call: a mutation that removed Secure from this one
        alone was not noticed by anything.
        """
        cookie = self._cookie_over("https")
        self.assertIn("Secure", cookie)
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Lax", cookie)

    def test_that_cookie_is_not_marked_secure_over_plaintext(self):
        """Same reasoning as the sign-in cookie: a Secure cookie over http is
        never sent at all, which would make the link simply not work."""
        cookie = self._cookie_over("http")
        self.assertNotIn("Secure", cookie)
        self.assertIn("HttpOnly", cookie)

    def test_the_flag_follows_the_transport_rather_than_a_setting(self):
        self.assertNotEqual("Secure" in self._cookie_over("https"),
                            "Secure" in self._cookie_over("http"))

    def test_refused_links_are_throttled_so_they_cannot_flush_the_audit(self):
        """The history keeps a fixed amount and discards the oldest.

        Without a limit, requests for `/access/<anything>` write an entry each
        and push out the sign-ins and refusals an administrator needs -- the
        easiest way for somebody to erase what came before them.
        """
        before = len(self.store.audit_entries(500))
        for _ in range(omni_users.MAX_FAILURES * 4):
            self.assertEqual(self.open_link("wrong-token").status_code, 404)
        written = len(self.store.audit_entries(500)) - before
        self.assertLessEqual(written, omni_users.MAX_FAILURES,
                             "a flood of refused links must not be able to "
                             "write an unbounded number of audit entries")

    def test_throttling_does_not_lock_out_a_valid_link(self):
        """Refusals are counted per source; a good link from elsewhere works.

        Driven from a different source address, because the point is that the
        counter is scoped rather than global.
        """
        for _ in range(omni_users.MAX_FAILURES * 2):
            self.open_link("wrong-token")
        self.client.delete_cookie(omni_gate.SESSION_COOKIE)
        response = self.client.get(
            "/access/" + self.token,
            environ_base={omni_remote.LISTENER_KEY: omni_remote.REMOTE,
                          "REMOTE_ADDR": "198.51.100.42", **NAVIGATION})
        self.assertEqual(response.status_code, 302)

    def test_with_remote_access_off_the_link_reaches_nothing(self):
        """Turning remote access off must not leave a passwordless door open."""
        listener = self.srv._remote_listener
        listener._server = None
        try:
            self.assertEqual(self.open_link(self.token).status_code, 404)
        finally:
            listener._server = object()

    def test_a_link_opened_after_remote_access_was_turned_off_gets_a_page(self):
        """A saved home-screen link is exactly how a person reaches this."""
        listener = self.srv._remote_listener
        listener._server = None
        try:
            response = self.open_link(self.token)
            self.assertIn("text/html", response.headers.get("Content-Type", ""))
            body = response.get_data(as_text=True)
            self.assertNotIn("remote access is disabled", body)
            self.assertIn("<h1>", body)
        finally:
            listener._server = object()


class SecurityHeaderTests(RemoteBrowserTestBase):
    """What OmniSuite tells a browser to enforce, and what it does not send.

    Raised by a console full of `Unrecognized feature: 'attribution-reporting'`
    and three more like it. Measured against the running server, OmniSuite was
    sending no Permissions-Policy header at all -- those are Chrome Privacy
    Sandbox features and the header was coming from something else in that
    browser. So the work was not to remove a directive that was never there. It
    was to state OmniSuite's policy deliberately, using only features that have
    been in the specification for years, so the header is correct and any
    remaining console error is demonstrably not ours.
    """

    #: Every feature name OmniSuite is allowed to put in a Permissions-Policy.
    #: Deliberately a tiny list: a directive a browser does not recognise is a
    #: console error, which is the thing that started this.
    RECOGNISED = {"camera", "microphone", "geolocation", "fullscreen",
                  "payment", "usb", "midi", "autoplay", "display-capture",
                  "screen-wake-lock", "xr-spatial-tracking"}

    #: The four from the report. Chrome's Privacy Sandbox proposals, not
    #: universally implemented, and nothing OmniSuite has any use for.
    NEVER = ("attribution-reporting", "private-aggregation",
             "join-ad-interest-group", "run-ad-auction", "browsing-topics",
             "interest-cohort")

    def headers_for(self, path, extra=None):
        return self.remote(path, extra=extra or NAVIGATION).headers

    def test_the_policy_names_only_features_a_browser_will_recognise(self):
        policy = self.headers_for("/login")["Permissions-Policy"]
        names = [part.split("=")[0].strip()
                 for part in policy.split(",") if part.strip()]
        self.assertTrue(names, "the header should say something")
        for name in names:
            with self.subTest(feature=name):
                self.assertIn(name, self.RECOGNISED,
                              "a feature a browser does not recognise is a "
                              "console error on every page load")

    def test_none_of_the_reported_features_is_ever_sent(self):
        policy = self.headers_for("/login")["Permissions-Policy"]
        for feature in self.NEVER:
            with self.subTest(feature=feature):
                self.assertNotIn(feature, policy)

    def test_the_policy_is_a_denial_rather_than_a_grant(self):
        """Every feature named is switched off for every origin."""
        policy = self.headers_for("/login")["Permissions-Policy"]
        for part in policy.split(","):
            if not part.strip():
                continue
            with self.subTest(directive=part.strip()):
                self.assertTrue(part.strip().endswith("=()"), part)

    def test_the_headers_that_actually_prevent_something_are_present(self):
        headers = self.headers_for("/login")
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(headers["X-Frame-Options"], "DENY")
        # A Direct Access link is a bearer credential in a path, so nothing
        # may carry it into a Referer header.
        self.assertEqual(headers["Referrer-Policy"], "no-referrer")

    def test_they_are_on_every_kind_of_response(self):
        for path, extra in (("/login", NAVIGATION), ("/api/state", XHR),
                            ("/ui/templates.css", XHR), ("/favicon.ico", XHR)):
            with self.subTest(path=path):
                headers = self.headers_for(path, extra)
                self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
                self.assertIn("Permissions-Policy", headers)

    def test_they_are_on_local_responses_too(self):
        response = self.client.get(
            "/", environ_base={omni_remote.LISTENER_KEY: omni_remote.LOCAL})
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")

    def test_a_content_type_declares_its_charset_once(self):
        """`text/html; charset=utf-8; charset=utf-8` was going out on every page."""
        for path in ("/login", "/user", "/help"):
            with self.subTest(path=path):
                session = self.store.open_session(self.admin_account.id)
                content_type = self.remote(path, session=session).headers.get(
                    "Content-Type", "")
                self.assertLessEqual(content_type.lower().count("charset"), 1,
                                     content_type)


class LocalOperatorIsUntouchedTests(RemoteBrowserTestBase):
    """None of this may change the experience on the machine itself."""

    def test_a_local_request_still_gets_the_application(self):
        response = self.client.get(
            "/", environ_base={omni_remote.LISTENER_KEY: omni_remote.LOCAL})
        self.assertEqual(response.status_code, 200)

    def test_a_local_request_is_never_redirected_to_sign_in(self):
        for path in ("/", "/matrix", "/api/state"):
            with self.subTest(path=path):
                response = self.client.get(
                    path,
                    environ_base={omni_remote.LISTENER_KEY: omni_remote.LOCAL})
                self.assertNotEqual(response.status_code, 302)


if __name__ == "__main__":
    unittest.main()
