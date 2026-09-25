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

from flask import g, jsonify, request

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
    access, feature = omni_access.classify(rule)

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

    # 6. Role.
    if access is Access.AUTHENTICATED:
        return Decision(True, 200, "authenticated", account)
    if access is Access.ADMIN:
        if not account.is_admin:
            return Decision(False, 403, "not_admin", account)
        return Decision(True, 200, "admin", account)
    if access is Access.USER:
        if account.is_admin:
            return Decision(True, 200, "admin", account)
        if not feature or not account.permissions.has_feature(feature):
            return Decision(False, 403, f"missing_feature:{feature}", account)
        # 7. Per-device authorization. A capability is not a device grant.
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
        message = {401: "authentication required",
                   403: "not permitted",
                   404: "not found"}.get(decision.status, "denied")
        return jsonify({"ok": False, "error": message}), decision.status

    return app
