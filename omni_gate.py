"""The authorization gate: one check, in front of every route.

This is deliberately a `before_request` hook and not a decorator. A decorator
protects the routes somebody remembered to decorate, and the measured problem
here is precisely the routes nobody would think of: `/api/list_dir` enumerates
the host filesystem and `/api/config` hands back the device passwords, and
neither is a route you would decorate while writing a routing UI.

So the gate runs for every request, consults the exhaustive table in
`omni_access`, and denies anything it cannot find an explicit rule for.

    arrived on the loopback listener?      -> local operator, allowed
    public route?                          -> allowed
    valid session?                         -> else 401
    CSRF valid for a state-changing verb?  -> else 403
    established by a Direct Access link,
    and this an administrative route?      -> else 403
    role permitted for this route?         -> else 403
    role=user: every device named in the
    request permitted to this account?     -> else 403

The last step is the one that cannot be skipped. A capability says a user may
route; it does not say *what* they may route. Both the source and the
destination are checked against the account before the routing service is
called, so crafting an HTTP request gets the same answer as pressing a button.
"""
from __future__ import annotations

import logging

from flask import Response, g, jsonify, redirect, request

import omni_access
import omni_remote
from omni_access import Access

log = logging.getLogger(__name__)

#: Verbs that change something and therefore need a CSRF token.
UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

#: Header the browser sends the CSRF token in.
CSRF_HEADER = "X-OmniSuite-CSRF"

#: Cookie carrying the session token.
SESSION_COOKIE = "omnisuite_session"

#: Body keys naming a source device, across the routing endpoints.
SOURCE_KEYS = ("encoder", "encoder_ip", "source", "source_ip", "src",
               "host_mac", "lex", "lex_mac", "encoder_mac", "source_mac")

#: Body keys naming a destination device.
#:
#: `display` is the Multiview endpoints' word for the decoder, and it carries a
#: device identity, so the gate checks it too. That is deliberate redundancy:
#: the handler checks it as well, and the two are independent -- a mutation run
#: showed the gate was not covering this key at all, leaving the handler as the
#: only guard on which display a Multiview may be put on.
DESTINATION_KEYS = ("decoder", "decoder_ip", "destination", "destination_ip",
                    "dst", "device_mac", "rex", "rex_mac", "decoder_mac",
                    "ip", "target", "display")

#: Deliberately NOT here: the USB endpoints' `computer` and `peripherals`.
#:
#: Those carry USB endpoint MACs, which are distinct from the OmniStream device
#: identities a permission is written against -- an endpoint integrated into an
#: encoder has its own MAC. Checking them here would deny every legitimate USB
#: request, because the endpoint MAC is never in the permitted device set. USB
#: authorisation happens in the handler, against the same projection the
#: browser was shown, and is tested there directly rather than through this
#: gate.


#: Where an unauthenticated browser is sent.
LOGIN_PATH = "/login"


def wants_a_page(rule, method, environ) -> bool:
    """Whether this request is a browser navigating, rather than a program calling.

    A person who opens the remote URL in Chrome must be given the sign-in page.
    A program calling an endpoint must be given a status code and JSON it can
    act on. Both were previously answered with `{"error": "authentication
    required"}`, which is correct for the second and useless for the first --
    it is what the operator saw when they opened the advertised address.

    Three conditions, all required, so this can never hand HTML to an API
    caller:

      * a GET or HEAD, because a navigation is never a POST here
      * a rule outside `/api/`, so every endpoint keeps its JSON contract
        whatever headers happen to arrive
      * the client asks for HTML, and asks for it in preference to JSON

    `Sec-Fetch-Mode: navigate` settles it outright where the browser sends it,
    which is every browser this would be opened in. The Accept test is the
    fallback for anything that does not.
    """
    if str(method or "").upper() not in ("GET", "HEAD"):
        return False
    if str(rule or "").startswith("/api/"):
        return False
    mode = (environ.get("HTTP_SEC_FETCH_MODE") or "").lower()
    if mode:
        # An explicit statement from the browser about what this request is.
        # `cors`, `no-cors` and `same-origin` are fetch/XHR; only a navigation
        # puts a page on the screen.
        return mode == "navigate"
    accept = (environ.get("HTTP_ACCEPT") or "").lower()
    if "text/html" not in accept:
        return False
    # An `Accept` naming both is a fetch that would rather have JSON.
    if "application/json" in accept:
        return accept.index("text/html") < accept.index("application/json")
    return True


def login_redirect(environ):
    """Send a browser to the sign-in page, remembering where it was going."""
    wanted = environ.get("PATH_INFO") or "/"
    query = environ.get("QUERY_STRING") or ""
    if query:
        wanted = wanted + "?" + query
    # Only a same-site path, and only characters a path can contain. The login
    # page validates this again before using it -- an open redirect on a sign-in
    # page is how somebody is sent to a convincing copy of it.
    from urllib.parse import quote
    return redirect(LOGIN_PATH + "?next=" + quote(wanted, safe="/"), code=302)


#: A refusal a person can read. Deliberately self-contained: it needs no
#: stylesheet, no script and no image, so it renders whatever else is
#: unreachable, and it cannot become a way to fetch an asset without a session.
_REFUSED_PAGE = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Not available &mdash; OmniSuite</title>
<style>
 :root {{ color-scheme: dark light; }}
 body {{ font: 15px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif;
        margin: 0; min-height: 100vh; display: flex; align-items: center;
        justify-content: center; background: #16181d; color: #e6e8ee; }}
 main {{ max-width: 30rem; padding: 2rem; }}
 h1 {{ font-size: 1.25rem; margin: 0 0 .5rem; }}
 p {{ margin: 0 0 1rem; color: #a9b0c0; }}
 a {{ color: #8ab4ff; }}
</style></head>
<body><main>
<h1>{heading}</h1>
<p>{detail}</p>
<p><a href="{back}">{back_label}</a></p>
</main></body></html>
"""


def refused_page(status, account=None):
    """The human answer to a refusal that is not "sign in"."""
    if status == 404:
        heading = "Not available here"
        detail = ("This part of OmniSuite works only on the computer it is "
                  "running on.")
    else:
        heading = "You do not have access to this"
        detail = ("Your account is not permitted to open this page. If you "
                  "think it should be, ask an administrator.")
    back = "/user" if (account is not None and not account.is_admin) else "/"
    body = _REFUSED_PAGE.format(
        heading=heading, detail=detail, back=back,
        back_label="Back to OmniSuite")
    return Response(body, status=status, mimetype="text/html")


class Decision:
    """Why a request was allowed or refused. Returned rather than raised so the
    caller decides the status code and the audit entry together."""

    __slots__ = ("allowed", "status", "reason", "principal")

    def __init__(self, allowed, status=200, reason="", principal=None):
        self.allowed = allowed
        self.status = status
        self.reason = reason
        self.principal = principal

    def __bool__(self):
        return self.allowed


ALLOW_LOCAL = Decision(True, 200, "local")


def _identities_in(payload, keys) -> list:
    """Every device identity a request body names under `keys`.

    Lists as well as scalars: the group endpoints take several decoders, and a
    check that only looked at scalars would wave those through.
    """
    found = []
    if not isinstance(payload, dict):
        return found
    for key in keys:
        if key not in payload:
            continue
        value = payload[key]
        if isinstance(value, (list, tuple)):
            found.extend(str(v) for v in value if v not in (None, ""))
        elif value not in (None, "", {}):
            found.append(str(value))
    return found


def evaluate(rule, method, environ, payload, store) -> Decision:
    """Decide one request. Pure: no Flask globals, so it is directly testable."""
    # With the method: one rule can classify its read and its write
    # differently, and the gate is the only place that knows which this is.
    access, feature = omni_access.classify(rule, method)

    # 1. The local operator. A fact about which socket accepted the connection,
    #    not an inference from an address a request claims to come from.
    if omni_remote.is_local_request(environ):
        return ALLOW_LOCAL

    # Everything below here arrived from the network.

    # 2. A local-only route is never reachable remotely, whatever the role.
    if access is Access.LOCAL_ONLY:
        return Decision(False, 404, "local_only")

    # 3. The public surface.
    if access is Access.PUBLIC:
        return Decision(True, 200, "public")

    # 4. A session is required for everything else.
    token = (environ.get("HTTP_COOKIE_SESSION_TOKEN")
             or _cookie(environ, SESSION_COOKIE))
    account, session = store.resolve_session(token) if store else (None, None)
    if account is None:
        return Decision(False, 401, "no_session")

    # 5. CSRF on anything that changes state. SameSite alone does not cover
    #    a top-level POST navigation.
    if method in UNSAFE_METHODS:
        presented = environ.get("HTTP_" + CSRF_HEADER.upper().replace("-", "_"))
        if not presented or presented != session.csrf_token:
            return Decision(False, 403, "csrf", account)

    # 6. How the holder proved who they were.
    #
    # A Direct Access link is a URL somebody may have saved on a shared tablet.
    # It is issued only to a restricted account, but an account's role can
    # change after a link is made, and an administrator's session must not be
    # obtainable by opening a bookmark. So a link-established session is barred
    # from the administrative surface here, independently of the account's role
    # -- two controls rather than one, because this is the outcome the whole
    # feature must never produce.
    if session is not None and getattr(session, "from_direct_link", False):
        if access is Access.ADMIN:
            return Decision(False, 403, "direct_link_not_admin", account)

    # 7. Role.
    if access is Access.AUTHENTICATED:
        return Decision(True, 200, "authenticated", account)
    if access is Access.ADMIN:
        if not account.is_admin:
            return Decision(False, 403, "not_admin", account)
        return Decision(True, 200, "admin", account)
    if access is Access.USER:
        # An administrator reviewing a user surface is not held to that user's
        # device grants. A link-established session never takes this path, even
        # on an account that has since been promoted: it would skip the
        # per-device check below, which is the substance of the restriction.
        if account.is_admin and not (
                session is not None
                and getattr(session, "from_direct_link", False)):
            return Decision(True, 200, "admin", account)
        if not feature or not account.permissions.has_feature(feature):
            return Decision(False, 403, f"missing_feature:{feature}", account)
        # 8. Per-device authorization. A capability is not a device grant.
        return _check_devices(payload, account)

    # Unreachable by construction -- classify() never returns anything else --
    # but an unknown access class denies rather than falls through.
    return Decision(False, 403, "unclassified", account)


def _check_devices(payload, account) -> Decision:
    sources = _identities_in(payload, SOURCE_KEYS)
    destinations = _identities_in(payload, DESTINATION_KEYS)

    for identity in sources:
        if not account.permissions.may_use_source(identity):
            return Decision(False, 403, "source_not_permitted", account)
    for identity in destinations:
        if not account.permissions.may_control_destination(identity):
            return Decision(False, 403, "destination_not_permitted", account)
    return Decision(True, 200, "permitted", account)


def _cookie(environ, name):
    """Read one cookie from the raw header, without needing a Flask request."""
    raw = environ.get("HTTP_COOKIE") or ""
    for part in raw.split(";"):
        key, _, value = part.strip().partition("=")
        if key == name:
            return value
    return None


def install(app, store_getter, enabled_getter):
    """Attach the gate to a Flask application.

    `store_getter` returns the UserStore; `enabled_getter` says whether remote
    access is on. Both are callables so the gate holds no state of its own and
    a test can drive it without a server.
    """

    @app.before_request
    def _authorize():                      # pragma: no cover - exercised via client
        rule = str(request.url_rule.rule) if request.url_rule else request.path

        # A request that arrived locally is the existing local experience and
        # is untouched by any of this -- which is what keeps V1.1.0's behaviour
        # intact while remote access is off.
        if omni_remote.is_local_request(request.environ):
            g.principal = None
            g.is_local = True
            return None

        if not enabled_getter():
            # Nothing should be able to reach the application remotely while
            # remote access is disabled; if something did, refuse it.
            #
            # A person can reach this: a Direct Access link saved to a home
            # screen and opened after remote access was turned off is exactly
            # it. So it is said the same two ways as every other refusal --
            # 404 rather than a redirect, because there is no sign-in page to
            # send them to either.
            if wants_a_page(rule, request.method, request.environ):
                return refused_page(404)
            return jsonify({"ok": False, "error": "remote access is disabled"}), 404

        payload = {}
        if request.method in UNSAFE_METHODS:
            payload = request.get_json(silent=True) or {}
            if not payload and request.form:
                payload = request.form.to_dict()
        else:
            payload = request.args.to_dict()

        store = store_getter()
        decision = evaluate(rule, request.method, request.environ, payload, store)
        g.principal = decision.principal
        g.is_local = False

        if decision.allowed:
            return None

        if store is not None and decision.reason not in ("no_session",):
            store.audit("access.denied",
                        actor=decision.principal,
                        detail={"rule": rule, "method": request.method,
                                "reason": decision.reason})

        # A person gets a page; a program gets a status code and JSON. The
        # refusal itself is identical either way -- this decides only how it is
        # said, and never what is allowed.
        if wants_a_page(rule, request.method, request.environ):
            if decision.status == 401:
                return login_redirect(request.environ)
            return refused_page(decision.status, decision.principal)

        message = {401: "authentication required",
                   403: "not permitted",
                   404: "not found"}.get(decision.status, "denied")
        return jsonify({"ok": False, "error": message}), decision.status

    return app
