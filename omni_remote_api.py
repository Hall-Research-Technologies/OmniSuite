"""HTTP surface for remote access: auth, bootstrap, user management, user state.

Registered onto the existing application rather than living in it, so the new
trust-sensitive surface is reviewable in one file instead of being scattered
through ten thousand lines of device code.

Every route added here is classified in omni_access.ROUTES; the fence in
tests/test_remote_access.py fails if one is not, so nothing registered here can
reach the network without somebody having decided who may call it.
"""
from __future__ import annotations

import logging
import pathlib
import time

from flask import Response, g, jsonify, redirect, request

import omni_adapters
import omni_access
import omni_gate
import omni_groups
import omni_locks
import omni_projection
import omni_remote
import omni_requests
import omni_users
from omni_gate import CSRF_HEADER, SESSION_COOKIE
from omni_users import Permissions

log = logging.getLogger(__name__)


#: The name refused Direct Access attempts are throttled under.
#:
#: Not a username, and deliberately one that cannot be created: throttling is
#: per name and per source address, and a link refusal has no account to
#: attribute itself to -- that is the whole point of answering the same way
#: whatever the reason.
_DIRECT_THROTTLE_KEY = "\x00direct-access-link"


#: What somebody sees when a Direct Access link no longer works.
#:
#: One sentence, and deliberately the same sentence for every reason: an
#: unknown token, a revoked one, a disabled account, a deleted account, an
#: account promoted to administrator. Whoever is holding an old URL learns only
#: that it does not work, which is all they are entitled to know.
#:
#: Self-contained, so it needs no stylesheet, no script and no image, and
#: renders on a phone with nothing else available.
_LINK_REFUSED = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Link not valid &mdash; OmniSuite</title>
<style>
 :root { color-scheme: dark light; }
 body { font: 15px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif;
        margin: 0; min-height: 100vh; display: flex; align-items: center;
        justify-content: center; background: #16181d; color: #e6e8ee; }
 main { max-width: 28rem; padding: 2rem; text-align: center; }
 h1 { font-size: 1.2rem; margin: 0 0 .6rem; }
 p { margin: 0; color: #a9b0c0; }
</style></head>
<body><main>
<h1>This OmniSuite access link is no longer valid</h1>
<p>Ask whoever set up this device for a new link, or sign in with a username
and password.</p>
</main></body></html>
"""


def _client_address():
    """Best available source address, for throttling and the audit trail only.

    Never used for a trust decision -- that is the listener's job. Recording it
    is still worth doing: "five failures from one address" is the shape of an
    attack, and the audit log is where it becomes visible.
    """
    return request.environ.get("REMOTE_ADDR") or "unknown"


def _json_body() -> dict:
    """The JSON object in the request. Anything else is a 400, never an empty request.

    Silently turning a list or a number into {} made a malformed body act as a
    command with no parameters -- `[]` to the Remote Access endpoint meant "off".
    The rules are shared with every other endpoint (omni_requests).
    """
    return omni_requests.json_object(request)


def _principal():
    return getattr(g, "principal", None)


def _is_local():
    return bool(getattr(g, "is_local", False))


def register(app, *, store, listener, aliases, groups, load_cache, matrix_state,
             route_view, remote_enabled, set_remote_state, remote_config,
             usb_state, usb_pair_view, usb_unpair_view,
             forget_observation, locks, tls, cert_paths, tls_context_for,
             certificate_state, source_preview, credential_report,
             unroute_view, user_shell):
    """Attach the remote-access API.

    Everything the module needs is passed in rather than imported from the
    server, so this file has no opinion about how devices are read and cannot
    accidentally start reading them.
    """

    # ---------------------------------------------------------------- auth
    @app.route("/api/auth/login", methods=["POST"])
    def api_auth_login():
        data = _json_body()
        username = str(data.get("username") or "").strip()
        password = str(data.get("password") or "")
        result = store.authenticate(username, password, source=_client_address())
        if not result["ok"]:
            status = 429 if result.get("throttled") else 401
            return jsonify({"ok": False, "error": result["error"]}), status

        body = {"ok": True, "user": result["user"], "csrf_token": result["csrf_token"]}
        response = jsonify(body)
        # HttpOnly: the session token is never readable by page script, so an
        # injected script cannot exfiltrate it. The CSRF token is returned in
        # the body on purpose -- it has to be readable to be echoed back, and it
        # is useless without the cookie.
        response.set_cookie(
            SESSION_COOKIE, result["token"],
            httponly=True, samesite="Lax",
            secure=request.is_secure, path="/")
        return response

    @app.route("/api/auth/logout", methods=["POST"])
    def api_auth_logout():
        token = request.cookies.get(SESSION_COOKIE)
        if token:
            store.revoke(token)
            store.audit("logout", actor=_principal(), detail={})
        response = jsonify({"ok": True})
        response.delete_cookie(SESSION_COOKIE, path="/")
        return response

    @app.route("/api/auth/session", methods=["GET"])
    def api_auth_session():
        account = _principal()
        if account is None:
            # The local operator has no account and needs none. Saying so lets
            # one page serve both without the browser guessing.
            return jsonify({"ok": True, "local": True, "role": "admin",
                            "username": None,
                            "features": list(omni_access.FEATURES)})
        # The CSRF token for the session this request arrived on.
        #
        # The sign-in page keeps the one it was handed, but a session opened by
        # a Direct Access link never went through that page -- it was redirected
        # straight to the shell -- so without this every action it tried would
        # be refused for a missing token. Returning it is not a weakening: it is
        # meant to be readable by the page, and it is useless without the
        # HttpOnly cookie that is never readable at all.
        _account, session = store.resolve_session(
            request.cookies.get(SESSION_COOKIE))
        return jsonify({**omni_projection.session_summary(account),
                        "local": False,
                        "csrf_token": session.csrf_token if session else "",
                        # So a page can tell the operator how they got here, and
                        # so a test can prove the two are distinguishable.
                        "method": session.method if session else ""})

    # ----------------------------------------------------------- bootstrap
    @app.route("/api/bootstrap/state", methods=["GET"])
    def api_bootstrap_state():
        return jsonify({"ok": True,
                        "needs_admin": not store.has_admin,
                        "accounts": 0 if store.is_empty else len(store.list_accounts())})

    @app.route("/api/bootstrap/admin", methods=["POST"])
    def api_bootstrap_admin():
        """Create the first administrator.

        Classified local-only, so this is reachable from the machine running
        OmniSuite and from nowhere else. That is the whole security property:
        somebody at the host already has full control of the application, and a
        remote client can never reach this route to make itself an
        administrator.

        It also refuses once an administrator exists, so it cannot be used a
        second time to add one.
        """
        if store.has_admin:
            return jsonify({"ok": False,
                            "error": "an administrator already exists"}), 409
        data = _json_body()
        username = str(data.get("username") or "").strip()
        password = str(data.get("password") or "")
        confirm = str(data.get("confirm") or "")
        if password != confirm:
            return jsonify({"ok": False, "error": "passwords do not match"}), 400
        # The generic account-created event is suppressed: creating the
        # first administrator on a machine that had none is a distinct and
        # more meaningful thing, and recording both says one action happened
        # twice.
        result = store.create(username, password, omni_users.ROLE_ADMIN,
                              audit=False)
        if not result["ok"]:
            return jsonify(result), 400
        store.audit("bootstrap.admin_created", actor=None,
                    detail={"username": username})
        return jsonify(result)

    # ------------------------------------------------------ remote access
    @app.route("/api/remote_access", methods=["GET"])
    def api_remote_access_state():
        # Enumerated with a stable identity, so a selection can survive the
        # address changing. `usable_bind_addresses` still has the final say on
        # what may be bound -- this adds identity, it does not widen the gate.
        #
        # Reading the page may reuse an enumeration made moments ago (each one
        # starts a PowerShell child that takes about two seconds); `refresh=1`
        # is the explicit way to ask the system again.
        if request.args.get("refresh") in ("1", "true", "yes"):
            omni_adapters.invalidate_cache()
        try:
            enumerated = omni_adapters.enumerate_adapters()
        except Exception:
            enumerated = []
        adapters = [a for a in enumerated
                    if omni_remote.is_usable_remote_address(a.get("address"))]
        state = listener.describe()
        saved = remote_config()
        # Configured and running are different facts, and an installed system
        # needs to be able to tell them apart: a saved address whose adapter has
        # gone away leaves remote access configured and unavailable, which is
        # neither "on" nor "off".
        configured = bool(saved.get("enabled"))
        # Available means the chosen ADAPTER is here, not that the address it
        # had last time is. That distinction is the defect this fixes.
        chosen = saved.get("adapter_id") or ""
        available = (any(a.get("id") == chosen for a in adapters) if chosen
                     else any(a.get("address") == saved.get("address")
                              for a in adapters))
        # What the certificate is, never what the key is.
        certificate = certificate_state()
        transport = saved.get("transport") or "http"
        _context, tls_error = tls_context_for(saved)

        # Does the certificate actually cover the address being served?
        #
        # A certificate made for one adapter and then used on another is a
        # connection no browser will accept without an override, and Settings
        # previously said "Self-signed certificate in place. For <the other
        # address>" as though that were a satisfactory state of affairs.
        # Decided from the SANs; see omni_tls.covers.
        #
        # The address the certificate has to cover is the one being served if
        # the listener is up, and otherwise the one the chosen adapter carries
        # now. Resolved here from the list already enumerated above rather than
        # through the restore path, because reading a page must not be what
        # re-persists a configuration.
        effective = state.get("host") or ""
        if not effective and chosen:
            effective = omni_adapters.resolve_address(
                chosen, saved.get("address") or "", adapters)[0]
        elif not effective:
            effective = saved.get("address") or ""
        certificate = dict(certificate)
        certificate["for_address"] = effective
        certificate["matches_address"] = bool(
            effective and certificate.get("readable")
            and tls.covers(certificate, effective))
        certificate["generated"] = not bool(saved.get("cert_path"))
        # Whether a TLS context can really be built from the configured pair,
        # asked of the loader the listener itself uses and whatever transport is
        # currently selected. A certificate file that parses is not a certificate
        # that can serve: its key may be missing or belong to another one, and
        # "active" must not be claimed for it.
        _https_context, https_error = tls_context_for(
            dict(saved, transport="https"))
        certificate["usable"] = bool(certificate.get("present")) and not https_error
        certificate["unusable_reason"] = https_error if certificate.get("present") else ""
        return jsonify({
            "ok": True,
            **state,
            "adapters": adapters,
            "configured": configured,
            "configured_address": saved.get("address") or "",
            "configured_adapter": saved.get("adapter_id") or "",
            "configured_adapter_name": saved.get("adapter_name") or "",
            "configured_available": bool(configured and available),
            "restore_error": saved.get("restore_error") or "",
            "has_admin": store.has_admin,
            "sessions": store.active_session_count(),
            # The transport that is configured, whether it can start, and what
            # is actually serving. All three, because they can differ: a
            # configuration asking for HTTPS whose certificate is unusable is
            # neither "secure" nor "insecure", it is "not running".
            "transport": transport,
            "encrypted": bool(state.get("secure")),
            "tls_error": tls_error,
            "certificate": certificate,
        })

    @app.route("/api/remote_access", methods=["POST"])
    def api_remote_access_set():
        data = _json_body()
        # Turning Remote Access on or off is a command, so the request has to say
        # which, in as many words. A body that is not an object, or has no boolean
        # `enabled`, used to read as "off": it ended every session and stopped the
        # listener. And the port is checked before anything is touched.
        enabled = data.get("enabled")
        if not isinstance(enabled, bool):
            return jsonify({"ok": False, "reason": "bad_request_body",
                            "error": "Say whether to turn Remote Access on or off "
                                     "(enabled: true or false). Nothing was changed."}), 400
        if data.get("port") is not None:
            port_error = _remote_port_problem(data.get("port"))
            if port_error:
                return jsonify({"ok": False, "reason": "invalid_port",
                                "error": port_error}), 400
        want = enabled
        if not want:
            listener.stop()
            revoked = store.revoke_every_session()
            store.audit("remote_access.disabled", actor=_principal(),
                        detail={"sessions_revoked": revoked})
            set_remote_state(False, None, None)
            return jsonify({"ok": True, **listener.describe()})

        if not store.has_admin:
            return jsonify({
                "ok": False,
                "error": "create an administrator account before enabling "
                         "remote access"}), 409
        address = str(data.get("address") or "").strip()
        adapter_id = str(data.get("adapter_id") or "").strip()
        port = data.get("port") or request.environ.get("SERVER_PORT") or 8080
        # An explicit choice wins. Otherwise the saved one, which is HTTP for a
        # first-time setup. HTTP still needs the acknowledgement below, so
        # omitting a field never turns plaintext on by itself.
        requested = str(data.get("transport") or "").strip().lower()
        transport = (requested if requested in ("http", "https")
                     else (remote_config().get("transport") or "http"))

        # An adapter chosen by identity decides the address, so a stale one in
        # the request body cannot send the listener somewhere else.
        adapter_name = ""
        # Turning Remote Access on is a DECISION made from this list, so it is
        # never answered from a remembered one.
        omni_adapters.invalidate_cache()
        if adapter_id:
            try:
                enumerated = omni_adapters.enumerate_adapters()
            except Exception:
                enumerated = []
            resolved, why = omni_adapters.resolve_address(adapter_id, address,
                                                          enumerated)
            if not resolved:
                return jsonify({
                    "ok": False,
                    "error": ("That network adapter is no longer available on "
                              "this computer." if why == "adapter missing"
                              else "That adapter has several addresses; "
                                   "choose which one to use.")}), 400
            address = resolved
            adapter_name = next((a["name"] for a in enumerated
                                 if a.get("id") == adapter_id), "")
        else:
            # No identity offered: adopt the one that owns this address, if
            # exactly one does. Anything else is refused rather than guessed.
            try:
                enumerated = omni_adapters.enumerate_adapters()
            except Exception:
                enumerated = []
            owner = omni_adapters.adopt_saved_address(address, enumerated)
            if owner is not None:
                adapter_id, adapter_name = owner["id"], owner["name"]

        # Turning on plaintext remote access is possible, but only when the
        # administrator says so in as many words. It is not a default and it is
        # not reachable by omitting a field.
        if transport == "http" and data.get("accept_insecure") is not True:
            return jsonify({
                "ok": False,
                "error": "Remote access without HTTPS sends sign-in details in "
                         "clear. Confirm you intend that, or configure a "
                         "certificate.",
                "needs_confirmation": True}), 400

        # The certificate the administrator chose ("use these files") is saved
        # state. Settings' toggle sends no paths, so a request that omits them
        # means "the saved choice", never "none": reading the body alone served
        # the generated certificate instead and then blanked the saved pair.
        # Paths in the request still win; "Create one" is what clears them.
        chosen = remote_config()
        cert_path = str(data.get("cert_path") or "").strip() or chosen.get("cert_path") or ""
        key_path = str(data.get("key_path") or "").strip() or chosen.get("key_path") or ""
        if bool(cert_path) != bool(key_path):
            return jsonify({
                "ok": False,
                "error": "A certificate and its key must be chosen together."}), 400
        proposed = {"transport": transport,
                    "cert_path": cert_path, "key_path": key_path}
        context, tls_error = tls_context_for(proposed)
        if tls_error:
            # Never start in the clear because TLS could not be arranged.
            return jsonify({"ok": False, "error": tls_error,
                            "certificate": certificate_state()}), 400

        # A certificate for one address, about to be served on another.
        #
        # This is reachable in two ordinary ways: the administrator changes
        # which adapter Remote Access uses, or the chosen adapter's own address
        # changes and the listener correctly follows it. Either way a
        # certificate generated for the old address is no longer valid for the
        # new one, and starting anyway would advertise HTTPS at an address no
        # browser will accept while Settings reported normal secure operation.
        #
        # OmniSuite's OWN certificate is refused rather than quietly reissued:
        # regenerating changes the fingerprint, and the fingerprint is the one
        # thing an administrator has to go on when their browser warns. So the
        # action is theirs, it is one button, and this says which button.
        #
        # A SUPPLIED certificate is never touched and never blocks. It may
        # legitimately carry a DNS name the site reaches OmniSuite by, which
        # this has no way of knowing about; refusing would break a correct
        # deployment. The mismatch is reported instead, and the status says so.
        if transport == "https":
            described = certificate_state()
            supplied = bool(proposed["cert_path"])
            if (not supplied and described.get("readable")
                    and not tls.covers(described, address)):
                return jsonify({
                    "ok": False,
                    "error": (
                        "The certificate OmniSuite created is for "
                        f"{tls.label_for(described) or 'another address'}, not "
                        f"for {address}. Create one for this network, then "
                        "turn remote access on."),
                    "needs_certificate": True,
                    "certificate": {**described, "for_address": address,
                                    "matches_address": False,
                                    "generated": True}}), 400

        result = listener.start(address, port, ssl_context=context)
        if not result.get("ok"):
            return jsonify(result), 400
        store.audit("remote_access.enabled", actor=_principal(),
                    detail={"address": address, "port": int(port),
                            "transport": transport, "adapter": adapter_name})
        set_remote_state(True, address, int(port), transport,
                         proposed["cert_path"], proposed["key_path"],
                         adapter_id, adapter_name)
        return jsonify({**result, "certificate": certificate_state()})

    @app.route("/api/admin/certificate", methods=["POST"])
    def api_admin_certificate():
        """Create a self-signed certificate, or point at a supplied pair.

        Nothing about a private key comes back out of here. The response
        describes the certificate -- subject, issuer, validity, fingerprint --
        which is what an administrator needs to confirm the thing their browser
        warned about is the one OmniSuite is serving.
        """
        data = _json_body()
        action = str(data.get("action") or "").strip()

        if action == "generate":
            address = str(data.get("address") or "").strip()
            cert_path, key_path = cert_paths
            try:
                described = tls.generate_self_signed(address, cert_path, key_path)
            except tls.TlsError as exc:
                return jsonify({"ok": False, "error": str(exc)}), 400
            store.audit("certificate.generated", actor=_principal(),
                        detail={"address": address,
                                "fingerprint": described.get("fingerprint")})
            # Stored as the configured pair so a restart uses it.
            saved = remote_config()
            set_remote_state(saved.get("enabled"), saved.get("address"),
                             saved.get("port"), "https", "", "",
                             saved.get("adapter_id"), saved.get("adapter_name"))
            return jsonify({"ok": True, "certificate": certificate_state()})

        if action == "use":
            cert_path = str(data.get("cert_path") or "").strip()
            key_path = str(data.get("key_path") or "").strip()
            try:
                tls.load_context(cert_path, key_path)
            except tls.TlsError as exc:
                return jsonify({"ok": False, "error": str(exc)}), 400
            tls.restrict_existing(key_path)
            saved = remote_config()
            set_remote_state(saved.get("enabled"), saved.get("address"),
                             saved.get("port"), "https", cert_path, key_path,
                             saved.get("adapter_id"), saved.get("adapter_name"))
            store.audit("certificate.configured", actor=_principal(),
                        detail={"certificate": pathlib.Path(cert_path).name})
            return jsonify({"ok": True, "certificate": certificate_state()})

        if action == "remove":
            # Not while it is in use: removing the certificate under a running
            # HTTPS listener would drop every signed-in browser and leave the
            # machine unreachable, which is not something to do as a side
            # effect of tidying up.
            if listener.running and listener.describe().get("secure"):
                return jsonify({
                    "ok": False,
                    "error": "Turn remote access off before removing the "
                             "certificate it is using."}), 409

            saved = remote_config()
            cert_path, key_path = cert_paths
            removed = []
            # Only OmniSuite's own pair is deleted. A certificate the site
            # supplied belongs to the site: that selection is cleared so
            # OmniSuite stops using it, and the files are left where they are.
            if not saved.get("cert_path"):
                for path in (pathlib.Path(cert_path), pathlib.Path(key_path)):
                    try:
                        if path.exists():
                            path.unlink()
                            removed.append(path.name)
                    except OSError as exc:
                        return jsonify({
                            "ok": False,
                            "error": f"Could not remove {path.name}: "
                                     f"{exc.strerror or exc}"}), 500
            set_remote_state(saved.get("enabled"), saved.get("address"),
                             saved.get("port"), saved.get("transport"),
                             "", "",
                             saved.get("adapter_id"), saved.get("adapter_name"))
            store.audit("certificate.removed", actor=_principal(),
                        detail={"files": removed,
                                "supplied": bool(saved.get("cert_path"))})
            return jsonify({"ok": True, "removed": removed,
                            "certificate": certificate_state()})

        return jsonify({"ok": False, "error": "unknown action"}), 400

    # -------------------------------------------------- admin: accounts
    @app.route("/api/admin/users", methods=["GET"])
    def api_admin_users():
        return jsonify({"ok": True, "users": store.list_accounts()})

    @app.route("/api/admin/users", methods=["POST"])
    def api_admin_create_user():
        data = _json_body()
        problem = _identity_problem(data.get("permissions"))
        if problem:
            return jsonify({"ok": False, "error": problem}), 400
        # The store records this, with the actor passed down. It is the
        # layer that knows what was actually created -- the request body is
        # what was asked for, which is not the same thing when it was refused
        # or normalised.
        result = store.create(
            str(data.get("username") or "").strip(),
            str(data.get("password") or ""),
            str(data.get("role") or omni_users.ROLE_USER),
            _permissions_from(data.get("permissions")),
            actor=_principal())
        if not result["ok"]:
            return jsonify(result), 400
        return jsonify(result)

    @app.route("/api/admin/users/<user_id>", methods=["POST"])
    def api_admin_update_user(user_id):
        """One endpoint, one explicit action, named in the body.

        `action` rather than a merge of whatever fields arrived: a permission
        change and a password reset need different audit entries and different
        session consequences, and a partial-update endpoint makes it easy to
        perform one while believing you performed the other.
        """
        data = _json_body()
        action = str(data.get("action") or "").strip()
        actor = _principal()

        # Each of these records its own event, in the store, naming the
        # account rather than its id and saying what actually changed --
        # "Account disabled", not "set_enabled". The actor is passed down
        # because it is the one thing only this layer knows.
        if action == "set_enabled":
            result = store.set_enabled(user_id, bool(data.get("enabled")),
                                       actor=actor)
        elif action == "set_role":
            result = store.set_role(user_id, str(data.get("role") or ""),
                                    actor=actor)
        elif action == "set_password":
            result = store.set_password(user_id, str(data.get("password") or ""),
                                        actor=actor)
        elif action == "set_permissions":
            problem = _identity_problem(data.get("permissions"))
            if problem:
                return jsonify({"ok": False, "error": problem}), 400
            wanted = _permissions_from(data.get("permissions"))
            raw = data.get("permissions")
            held = store.get(user_id)
            if held is not None and not (isinstance(raw, dict)
                                         and isinstance(raw.get("interfaces"),
                                                        (list, tuple))):
                # A client that does not know about routing interfaces must
                # not reset them by saying nothing: keep what the account has.
                wanted = Permissions(
                    features=wanted.features, sources=wanted.sources,
                    destinations=wanted.destinations, groups=wanted.groups,
                    interfaces=held.permissions.interfaces)
            result = store.set_permissions(user_id, wanted, actor=actor)
        elif action == "delete":
            result = store.delete(user_id, actor=actor)
        else:
            return jsonify({"ok": False, "error": "unknown action"}), 400

        if not result.get("ok"):
            return jsonify(result), 400
        return jsonify(result)

    # ------------------------------------------------- direct access links
    #
    # A passwordless URL for one restricted account. Off by default, turned on
    # per account by an administrator, and never available on an
    # administrator's own account.
    #
    # The token is a bearer credential, so it appears in exactly two places: in
    # the link handed to whoever asked for it, and hashed in the account
    # record. It is never logged, never audited, and never put in a response
    # that was not a deliberate request for the link itself.

    def _direct_link(token):
        """The URL to hand out, built on the address remote access advertises.

        Never a loopback URL: the whole point is that somebody opens it from a
        phone. If remote access is not running there is no origin to build one
        from, and that is reported rather than papered over with an address
        that would not work.
        """
        state = listener.describe()
        if not state.get("enabled") or not state.get("host"):
            return "", ("Remote access is off, so this link cannot be reached "
                        "from another device yet. It will work once remote "
                        "access is turned on.")
        return (f"{state.get('scheme') or 'https'}://{state['host']}:"
                f"{state['port']}/access/{token}"), ""

    def _direct_payload(account, token):
        link, note = _direct_link(token) if token else ("", "")
        body = {"ok": True,
                "direct_access": account.direct_access.public(),
                "username": account.username,
                # Empty when the stored copy cannot be read on this machine.
                # The page says "regenerate", which is the only thing that
                # helps.
                "link": link,
                "recoverable": bool(token),
                "note": note}
        if token and not link:
            body["note"] = note
        return body

    @app.route("/api/admin/users/<user_id>/direct_access",
               methods=["GET", "POST"])
    def api_admin_direct_access(user_id):
        actor = _principal()
        if request.method == "GET":
            account = store.get(user_id)
            if account is None:
                return jsonify({"ok": False, "error": "no such user"}), 404
            if not account.direct_access.active:
                return jsonify({"ok": True, "username": account.username,
                                "direct_access": account.direct_access.public(),
                                "link": "", "recoverable": False, "note": ""})
            return jsonify(_direct_payload(
                account, store.direct_access_token(user_id)))

        data = _json_body()
        action = str(data.get("action") or "").strip()
        if action == "enable":
            result = store.enable_direct_access(user_id, actor=actor)
        elif action == "regenerate":
            result = store.regenerate_direct_access(user_id, actor=actor)
        elif action == "disable":
            result = store.disable_direct_access(user_id, actor=actor)
        else:
            return jsonify({"ok": False, "error": "unknown action"}), 400
        if not result.get("ok"):
            return jsonify({"ok": False, "error": result["error"]}), 400

        account = store.get(user_id)
        if action == "disable":
            return jsonify({"ok": True, "username": account.username,
                            "direct_access": account.direct_access.public(),
                            "link": "", "recoverable": False, "note": ""})
        # The freshly minted token comes back from the store rather than being
        # read again, so the one moment it exists in memory is this one.
        return jsonify(_direct_payload(account, result["token"]))

    @app.route("/api/user/direct_access", methods=["GET"])
    def api_user_direct_access():
        """The signed-in account's own link, and nobody else's.

        Deliberately takes no user id. An endpoint that accepted one would be
        an endpoint somebody could aim at another account, and the only correct
        answer to "whose link is this" is "the caller's".
        """
        account = _principal()
        if account is None:
            # The local operator has no account, so there is no link to show.
            return jsonify({"ok": True, "direct_access": {"enabled": False},
                            "link": "", "recoverable": False,
                            "note": "Direct access is not enabled for this "
                                    "account."})
        if not account.direct_access.active:
            return jsonify({"ok": True,
                            "direct_access": account.direct_access.public(),
                            "link": "", "recoverable": False,
                            "note": "Direct access is not enabled for this "
                                    "account."})
        return jsonify(_direct_payload(account,
                                       store.direct_access_token(account.id)))

    @app.route("/access/<token>", methods=["GET"])
    def direct_access_entry(token):
        """Open a restricted session from a bearer link and serve the panel.

        The token is exchanged for an ordinary server-side session, and that
        session is what authorises everything afterwards: the token is never
        sent again, never put in an API payload, and never stored by the page.

        The shell is served *at this URL* rather than redirected to /user, so
        the link stays bookmarkable and a refresh works. That is a deliberate
        trade: the credential remains in the address bar, and therefore in a
        history and in anything that screenshots the screen. It is accepted
        because the URL is the credential by design -- there is nothing else
        to present -- and it is bounded by `Referrer-Policy: no-referrer`, by
        the link being revocable and replaceable in one action, and by the
        session it opens being barred from the administrative surface however
        the account's role later changes.
        """
        source = _client_address()
        # The token is resolved FIRST. Refusals from one address are throttled
        # like refused passwords, but the throttle must never be what refuses a
        # link that is valid: an administrator who tries a few old links after
        # regenerating one would otherwise be locked out of the new link too,
        # from that address, for the whole lockout, which looks exactly like
        # "regenerate does not work". The token is long and random, so letting a
        # valid one through a lockout gives a guesser nothing.
        account = store.resolve_direct_token(token)
        if account is None:
            # A refused link is throttled, for two reasons. It bounds the cost
            # of asking; and without it, requests for `/access/<anything>` would
            # write an audit entry each, pushing the sign-ins and refusals an
            # administrator needs out of a history that keeps a fixed amount and
            # discards the oldest.
            if store.locked_out(_DIRECT_THROTTLE_KEY, source):
                return Response(_LINK_REFUSED, status=404, mimetype="text/html")
            # One answer for every reason: unknown, revoked, disabled, deleted,
            # promoted. Saying which would tell whoever has an old URL
            # something about an account they have no business knowing.
            store.record_failure(_DIRECT_THROTTLE_KEY, source)
            store.audit("direct_access.used", actor=None,
                        detail={"result": "refused"})
            return Response(_LINK_REFUSED, status=404, mimetype="text/html")

        # A browser that already holds a live link session for this very account is
        # the same panel reloading. Opening another for it would grow the session
        # table, and one audit entry, on every refresh of a wall tablet -- sessions
        # for these links never expire, so nothing would ever take them out again.
        held, held_session = store.resolve_session(request.cookies.get(SESSION_COOKIE))
        if (held is not None and held.id == account.id
                and held_session.from_direct_link):
            session = held_session
        else:
            session = store.open_session(account.id,
                                         method=omni_users.BY_DIRECT_LINK)
            # The token was good a moment ago. Confirm it still is now that the
            # session exists: a link replaced or switched off between the two would
            # otherwise leave a session that its replacement was meant to end,
            # because the revocation ran before this session was in the table.
            if store.resolve_direct_token(token) is None:
                store.revoke(session.token)
                store.record_failure(_DIRECT_THROTTLE_KEY, source)
                return Response(_LINK_REFUSED, status=404, mimetype="text/html")
            store.audit("direct_access.used", actor=account,
                        detail={"result": "accepted",
                                "username": account.username,
                                "method": omni_users.BY_DIRECT_LINK})
        # The shell itself, at this URL. Served through the same helper the
        # /user route uses, so there is one copy of the page rather than a
        # second one that could drift.
        response = app.make_response(user_shell())
        response.set_cookie(
            SESSION_COOKIE, session.token,
            httponly=True, samesite="Lax",
            secure=request.is_secure, path="/")
        # Belt and braces over the global policy: this response's own URL is a
        # credential, and it must not be cached by a shared proxy or restored
        # into somebody else's tab from a back-forward cache.
        response.headers["Cache-Control"] = "no-store, private"
        return response

    @app.route("/api/admin/devices", methods=["GET"])
    def api_admin_devices():
        """Devices an administrator can assign or name, by stable identity.

        The identity is what a permission stores, so the assignment UI and the
        enforcement agree by construction rather than by both happening to
        normalise the same way.

        An administrator gets the device's own name, model, address,
        location and description alongside the alias: two displays both called
        "Front Display" are only distinguishable by what is underneath them.

        Location and description come from the device's own systeminfo, which
        the cache already holds -- no extra read is issued to obtain them, and
        nothing here writes them back.
        """
        # Who was given each device. Derived once, here, so the page renders
        # it rather than parsing a key format that belongs to the store.
        try:
            assigned = store.assigned_devices()
        except Exception:
            assigned = {}
        devices = []
        for unit in load_cache() or []:
            identity = omni_users._normalise_identity(unit.get("mac"))
            if not identity:
                continue
            role = (unit.get("role") or unit.get("type") or "").strip().lower()
            device_name = unit.get("hostname") or unit.get("name") or identity
            devices.append({
                "id": identity,
                # The device's own name, always, so an administrator can see
                # what they are labelling.
                "device_name": device_name,
                "alias": aliases.alias_for(identity),
                # What to show in a list: the alias where one is set.
                "name": aliases.display_name(identity, device_name),
                "model": unit.get("model") or "",
                "ip": unit.get("ip") or "",
                # Device-provided, from systeminfo the cache already holds.
                # Presented beside the alias so an administrator naming two
                # identical units can tell which is which -- and kept in
                # separate fields from the alias, because one belongs to
                # OmniSuite and these belong to the equipment.
                "location": omni_projection.device_location(unit),
                "description": omni_projection.device_description(unit),
                "role": "encoder" if "enc" in role else
                        ("decoder" if "dec" in role else "other"),
                # Usernames only. An administrator can already list accounts.
                "assigned_to": assigned.get(identity, []),
            })
        devices.sort(key=lambda d: (d["role"], d["name"].lower()))
        return jsonify({"ok": True, "devices": devices,
                        "duplicate_aliases": aliases.duplicates()})

    @app.route("/api/admin/aliases", methods=["GET"])
    def api_admin_aliases():
        return jsonify({"ok": True, "aliases": aliases.all(),
                        "duplicate_aliases": aliases.duplicates()})

    @app.route("/api/admin/aliases", methods=["POST"])
    def api_admin_set_aliases():
        """Name devices, or clear a name with an empty string.

        Takes a mapping so one editing session is one save. Nothing is written
        to any device: this is OmniSuite's own label for a piece of equipment
        and the OmniStream hostname is untouched.
        """
        data = _json_body()
        mapping = data.get("aliases")
        if not isinstance(mapping, dict):
            return jsonify({"ok": False, "error": "aliases must be an object"}), 400
        result = aliases.set_many(mapping)
        if not result.get("ok"):
            return jsonify(result), 400
        store.audit("device.aliases_changed", actor=_principal(),
                    detail={"count": result.get("applied", 0)})
        return jsonify({"ok": True, "applied": result.get("applied", 0),
                        "aliases": aliases.all(),
                        "duplicate_aliases": aliases.duplicates()})

    @app.route("/api/admin/credentials", methods=["GET"])
    def api_admin_credentials():
        """Which credential each device is actually answering to, by name.

        The question this answers is "is that unit still on the vendor
        default?", and it has to be answerable without anybody printing,
        logging or transporting a password. So every value here is one of
        `device`, `configured`, `fallback` or `unknown` -- a name for where the
        working credential came from, never the credential.

        A device appears only once something has successfully talked to it;
        one that has not been reached yet is reported as `unknown` rather than
        assumed good.
        """
        provenance = credential_report() or {}
        rows = []
        for unit in load_cache() or []:
            address = (unit.get("ip") or "").strip()
            if not address:
                continue
            identity = omni_users._normalise_identity(unit.get("mac"))
            source = provenance.get(address, "unknown")
            rows.append({
                "id": identity,
                "name": _label(identity, unit.get("hostname")
                               or unit.get("host") or address),
                "address": address,
                "model": unit.get("model") or "",
                "credential": source,
                # The one that wants attention: a unit answering to the
                # vendor default is a site condition, not a preference.
                "on_fallback": source == "fallback",
            })
        # Numerically, so .9 sorts before .10 rather than after it.
        def _key(row):
            parts = row["address"].split(".")
            try:
                return (0, tuple(int(p) for p in parts))
            except ValueError:
                return (1, row["address"])
        rows.sort(key=_key)
        return jsonify({
            "ok": True,
            "devices": rows,
            "on_fallback": sum(1 for r in rows if r["on_fallback"]),
            "unknown": sum(1 for r in rows if r["credential"] == "unknown"),
        })

    @app.route("/api/admin/audit", methods=["GET"])
    def api_admin_audit():
        """The security log, newest first, filtered server-side.

        Filtering here rather than in the browser so a page never has to hold
        the whole log to show a slice of it. Entries are already redacted when
        they are recorded -- there is no second pass here that could be
        forgotten.
        """
        try:
            limit = max(1, min(500, int(request.args.get("limit") or 200)))
        except (TypeError, ValueError):
            limit = 200
        return jsonify({
            "ok": True,
            "entries": store.audit_entries(
                limit=limit,
                event=request.args.get("event"),
                actor=request.args.get("actor"),
                search=request.args.get("q")),
            "summary": store.audit_summary(),
        })

    @app.route("/api/admin/audit/retention", methods=["POST"])
    def api_admin_audit_retention():
        """How long Activity is kept and how much disk it may use."""
        data = _json_body()
        result = store.set_activity_retention(
            data.get("days"), data.get("max_megabytes"), actor=_principal())
        if not result["ok"]:
            return jsonify(result), 400
        return jsonify(result)

    # ------------------------------------------------------- user surface
    @app.route("/api/user/state", methods=["GET"])
    def api_user_state():
        """Everything a restricted account learns about the deployment."""
        account = _principal()
        if account is None:
            # Local operator: an administrator in every sense, so the same
            # projection with nothing filtered out.
            account = _LocalAdmin()
        return jsonify(omni_projection.user_state(
            matrix_state(), load_cache() or [], account, aliases, groups))

    @app.route("/api/user/revision", methods=["GET"])
    def api_user_revision():
        account = _principal()
        if account is None:
            account = _LocalAdmin()
        return jsonify({"ok": True, "revision": omni_projection.permission_revision(
            account, groups, aliases)})

    @app.route("/api/user/matrix_state", methods=["GET"])
    def api_user_matrix_state():
        """The routable grid for this account, and nothing else.

        Built from the account's own permitted sources, displays and groups --
        never the administrative matrix with rows hidden. The page that draws
        it is not an authorisation boundary: every route it sends is checked
        again, so this endpoint's job is only to say what is on offer, and to
        refuse an account the administrator did not give the Matrix.
        """
        account = _principal()
        if account is None:
            account = _LocalAdmin()
        if not account.is_admin and not account.permissions.has_interface(
                omni_users.INTERFACE_MATRIX):
            return _denied(account, "interface_not_permitted",
                           "/api/user/matrix_state")
        return jsonify(omni_projection.user_matrix(
            matrix_state(), load_cache() or [], account, aliases, groups))

    #: The three things a route can carry. Anything else is a malformed request,
    #: not something to hand to the engine and see what it makes of it.
    _ROUTE_MODES = ("av", "video", "audio")

    def _route_interface(data):
        """The interface a route request says it came from, or an error reply.

        Returns (interface, mode, None) or (None, None, response). Which
        interface asked is stated by the caller and CHECKED against the
        account, so it can only ever narrow what the request is allowed to be:
        claiming one the account does not hold is refused, and claiming one it
        does hold gains nothing it did not already have. Audio-only and
        video-only are Matrix operations -- the Standard page does not offer
        them -- so they are not accepted from it.
        """
        interface = str(data.get("interface")
                        or omni_users.INTERFACE_STANDARD).strip().lower()
        mode = str(data.get("mode") or "av").strip().lower()
        if interface not in omni_users.INTERFACES:
            return None, None, (jsonify({"ok": False,
                                         "error": "unknown routing interface"}), 400)
        if mode not in _ROUTE_MODES:
            return None, None, (jsonify({"ok": False,
                                         "error": "mode must be av, video or audio"}), 400)
        if mode != "av" and interface != omni_users.INTERFACE_MATRIX:
            return None, None, (jsonify({
                "ok": False,
                "error": "video-only and audio-only routing is a Matrix operation"}), 400)
        return interface, mode, None

    @app.route("/api/user/route", methods=["POST"])
    def api_user_route():
        """Route a permitted source to a permitted destination.

        Pulled forward from Phase G, deliberately and minimally: without it the
        restricted shell would be a picture of a product rather than one. It
        adds no routing logic -- it resolves the two identities to the addresses
        the verified route path expects and hands over to that path unchanged.
        """
        data = _json_body()
        source_id = omni_users._normalise_identity(data.get("source"))
        destination_id = omni_users._normalise_identity(data.get("destination"))
        interface, mode, malformed = _route_interface(data)
        if malformed is not None:
            return malformed
        if not source_id or not destination_id:
            return jsonify({"ok": False,
                            "error": "source and destination required"}), 400

        # Re-read the account rather than trusting the one the gate resolved.
        # An administrator may have changed a permission, disabled the account
        # or reset its password in the moments since, and authorisation belongs
        # at the operation boundary rather than at the start of the request.
        # The gate checked these too. Checking again here is not redundancy
        # for its own sake: this endpoint translates identities into addresses,
        # and the check has to be on the same values the translation will use,
        # or the two could drift apart.
        def _check(account):
            if not account.permissions.has_feature(omni_access.FEATURE_AV):
                return "missing_feature:av"
            if not account.permissions.has_interface(interface):
                return "interface_not_permitted"
            if not account.permissions.may_use_source(source_id):
                return "source_not_permitted"
            if not account.permissions.may_control_destination(destination_id):
                return "destination_not_permitted"
            return None

        # Before the wait, so a request that was never going to be allowed
        # does not queue for a lock.
        refusal = _revalidate(_check, "/api/user/route")
        if refusal is not None:
            return refusal
        account = _current_account()

        resolved = _resolve_addresses(source_id, destination_id)
        encoder_ip, encoder_why = resolved[source_id]
        decoder_ip, decoder_why = resolved[destination_id]
        if "ambiguous" in (encoder_why, decoder_why):
            return jsonify({"ok": False, "reason": "ambiguous_device",
                            "error": "that device's address is not certain; "
                                     "scan again"}), 409
        if not encoder_ip or not decoder_ip:
            return jsonify({"ok": False,
                            "error": "that device is not currently known"}), 404

        # The verified path, called rather than reimplemented, with the
        # destination held so two operators cannot interleave inside one
        # transaction. Last successful operation wins; the loser waits for the
        # winner to finish rather than corrupting it.
        # A normal route means single view. If the display is compositing, the
        # engine leaves the Multiview itself as part of the same verified
        # transaction, so there is nothing for this endpoint to ask, to carry,
        # or to forward.
        began = time.monotonic()
        try:
            with locks.hold(locks.decoder(destination_id)):
                waited = time.monotonic() - began
                # Again, now that the wait is over and the next statement
                # writes to hardware.
                refusal = _revalidate(_check, "/api/user/route")
                if refusal is not None:
                    return refusal
                store.audit(
                    "matrix.route" if interface == omni_users.INTERFACE_MATRIX
                    else "route", actor=_current_account(), detail={
                        "source": source_id, "destination": destination_id,
                        "source_label": _label(source_id),
                        "destination_label": _label(destination_id),
                        "mode": mode, "interface": interface})
                with app.test_request_context(
                        "/api/route", method="POST",
                        json={"encoder": encoder_ip, "decoder": decoder_ip,
                              "mode": mode}):
                    result = route_view()
                routed = time.monotonic() - began
                log.info("[USER-ROUTE] %s -> %s waited %.2fs total %.2fs",
                         source_id, destination_id, waited, routed)
                return _classify_route_result(result, destination_id)
        except omni_locks.ResourceBusy as busy:
            # The other 409, and the one everybody assumes. Named so the two
            # can never again be told apart only by how long they took.
            log.warning("[USER-ROUTE] %s is busy; waited %.1fs for %s",
                        destination_id, time.monotonic() - began, busy.resource)
            return jsonify({
                "ok": False,
                "reason": "resource_busy",
                "error": "That display is busy with another change. "
                         "Try again in a moment."}), 409

    # ----------------------------------------------------- display groups
    #
    # Administrative: which displays a group contains. Membership is by stable
    # identity (a canonical MAC); a group has its own opaque id, so renaming or
    # re-populating one never changes what a permission refers to.
    def _group_payload(group):
        """A group as an administrator sees it: members named, never addressed."""
        members = []
        index = {omni_users._normalise_identity(u.get("mac")): u
                 for u in (load_cache() or []) if u.get("mac")}
        for identity in group["members"]:
            unit = index.get(identity)
            members.append({"id": identity,
                            "name": _label(identity, (unit or {}).get("hostname")
                                           or (unit or {}).get("name") or ""),
                            "known": unit is not None})
        return {"id": group["id"], "name": group["name"], "members": members,
                "granted_to": store.accounts_with_group(group["id"])}

    @app.route("/api/admin/groups", methods=["GET"])
    def api_admin_groups():
        return jsonify({"ok": True,
                        "groups": [_group_payload(g) for g in groups.all()]})

    @app.route("/api/admin/groups", methods=["POST"])
    def api_admin_create_group():
        data = _json_body()
        result = groups.create(data.get("name"), data.get("members") or [])
        if not result["ok"]:
            return jsonify(result), 400
        store.audit("group.created", actor=_principal(), detail={
            "name": result["group"]["name"],
            "displays": len(result["group"]["members"])})
        return jsonify({"ok": True, "group": _group_payload(result["group"])})

    @app.route("/api/admin/groups/<group_id>", methods=["POST"])
    def api_admin_update_group(group_id):
        data = _json_body()
        action = str(data.get("action") or "").strip()
        actor = _principal()
        if action == "delete":
            holders = store.accounts_with_group(group_id)
            result = groups.delete(group_id)
            if not result["ok"]:
                return jsonify(result), (404 if result.get("missing") else 400)
            store.forget_group(group_id)
            store.audit("group.deleted", actor=actor, detail={
                "name": result["group"]["name"], "accounts_affected": holders})
            return jsonify({"ok": True})
        if action == "rename":
            result = groups.update(group_id, name=data.get("name"))
        elif action == "set_members":
            result = groups.update(group_id, members=data.get("members") or [])
        else:
            return jsonify({"ok": False, "error": "unknown action"}), 400
        if not result["ok"]:
            return jsonify(result), (404 if result.get("missing") else 400)
        store.audit("group.updated", actor=actor, detail={
            "name": result["group"]["name"],
            "displays": len(result["group"]["members"])})
        return jsonify({"ok": True, "group": _group_payload(result["group"])})

    @app.route("/api/user/route_group", methods=["POST"])
    def api_user_route_group():
        """Route one permitted source to every display in a permitted group.

        One action for the person, strictly sequential underneath: each member
        is resolved, locked, routed and verified by the ordinary A/V route
        before the next one starts. A member that fails does not stop the rest
        and nothing already routed is undone. Membership is read now, so editing
        a group changes what it controls at once. Holding the group is the
        authority -- the members need not be individually granted, and they are
        never named by identity in the reply. Multiview is not involved: a
        display that was compositing leaves it exactly as for any other route.
        """
        data = _json_body()
        source_id = omni_users._normalise_identity(data.get("source"))
        group_id = str(data.get("group") or "").strip().lower()
        interface, mode, malformed = _route_interface(data)
        if malformed is not None:
            return malformed
        if not source_id or not group_id:
            return jsonify({"ok": False, "error": "source and group required"}), 400

        def _check(account):
            if not account.permissions.has_feature(omni_access.FEATURE_AV):
                return "missing_feature:av"
            if not account.permissions.has_interface(interface):
                return "interface_not_permitted"
            if not account.permissions.may_use_source(source_id):
                return "source_not_permitted"
            if not account.permissions.may_control_group(group_id):
                return "group_not_permitted"
            return None

        refusal = _revalidate(_check, "/api/user/route_group")
        if refusal is not None:
            return refusal
        group = groups.get(group_id)
        if group is None:
            return jsonify({"ok": False, "error": "That group is not available."}), 404

        def route_member(index, identity):
            name = _member_label(identity)
            refused = {"ok": False, "result": "refused", "name": name}
            refusal = _revalidate(_check, "/api/user/route_group")
            if refusal is not None:
                return refused
            try:
                with locks.hold(locks.decoder(identity)):
                    refusal = _revalidate(_check, "/api/user/route_group")
                    if refusal is not None:
                        return refused
                    resolved = _resolve_addresses(source_id, identity)
                    encoder_ip, encoder_why = resolved[source_id]
                    decoder_ip, decoder_why = resolved[identity]
                    if "ambiguous" in (encoder_why, decoder_why):
                        return {"ok": False, "result": "ambiguous", "name": name}
                    if not encoder_ip or not decoder_ip:
                        return {"ok": False, "result": "not_found", "name": name}
                    with app.test_request_context(
                            "/api/route", method="POST",
                            json={"encoder": encoder_ip, "decoder": decoder_ip,
                                  "mode": mode}):
                        result = route_view()
                    status = (result[1] if isinstance(result, tuple)
                              and len(result) > 1 else 200)
                    body = _payload_of(result)
                    ok = bool(body.get("ok")) and status < 400
                    return {"ok": ok,
                            "result": str(body.get("status") or
                                          ("verified" if ok else "failed")).lower(),
                            "name": name}
            except omni_locks.ResourceBusy:
                return {"ok": False, "result": "busy", "name": name}

        # Before anything is written: the exact stable identities this request
        # intends to change. Named fields only, never the request body. INFO is
        # right here because a group route is a mutation, not a poll.
        log.info("[GROUP-ROUTE] group=%s source=%s mode=%s interface=%s "
                 "intended=%s", group["id"], source_id, mode, interface,
                 ",".join(group["members"]))
        summary = omni_groups.run_sequential(group["members"], route_member)
        store.audit(
            "matrix.route_group" if interface == omni_users.INTERFACE_MATRIX
            else "route_group", actor=_current_account(), detail={
                "source": source_id, "source_label": _label(source_id),
                "group": group["id"], "group_name": group["name"],
                "mode": mode, "interface": interface,
                "routed": summary["routed"], "total": summary["total"],
                "intended": list(group["members"])})
        # Counts and per-display names and outcomes: never an identity or an
        # address.
        return jsonify({
            "ok": summary["ok"], "routed": summary["routed"],
            "total": summary["total"], "group": group["name"],
            "results": [{"index": r["index"], "name": r.get("name") or "Display",
                         "ok": bool(r.get("ok")),
                         "result": r.get("result") or "failed"}
                        for r in summary["results"]]})

    @app.route("/api/user/unroute", methods=["POST"])
    def api_user_unroute():
        """Stop a permitted display from showing anything.

        Adds no teardown logic: it resolves the identity to an address and
        hands over to the same verified engine the A/V Matrix uses, which
        leaves a Multiview through the established path first and reads the
        device back afterwards.
        """
        data = _json_body()
        destination_id = omni_users._normalise_identity(data.get("destination"))
        if not destination_id:
            return jsonify({"ok": False, "error": "destination required"}), 400

        def _check(account):
            if not account.permissions.has_feature(omni_access.FEATURE_AV):
                return "missing_feature:av"
            if not account.permissions.interfaces:
                return "interface_not_permitted"
            if not account.permissions.may_control_destination(destination_id):
                return "destination_not_permitted"
            return None

        # Before the wait, so a request that was never going to be allowed
        # does not queue for a lock.
        refusal = _revalidate(_check, "/api/user/unroute")
        if refusal is not None:
            return refusal

        decoder_ip, why = _resolve_addresses(destination_id)[destination_id]
        if why == "ambiguous":
            return jsonify({"ok": False, "reason": "ambiguous_device",
                            "error": "that device's address is not certain; "
                                     "scan again"}), 409
        if not decoder_ip:
            return jsonify({"ok": False,
                            "error": "that device is not currently known"}), 404

        began = time.monotonic()
        try:
            with locks.hold(locks.decoder(destination_id)):
                # Again, now that the wait is over and the next statement
                # writes to hardware.
                refusal = _revalidate(_check, "/api/user/unroute")
                if refusal is not None:
                    return refusal
                store.audit("unroute", actor=_current_account(),
                            detail={"destination": destination_id,
                                    "destination_label": _label(destination_id)})
                with app.test_request_context(
                        "/api/unroute", method="POST",
                        json={"decoder": decoder_ip}):
                    result = unroute_view()
                forget_observation("multiview:" + str(decoder_ip))
                log.info("[USER-UNROUTE] %s took %.2fs",
                         destination_id, time.monotonic() - began)
                # Named fields only: the engine answers with the decoder's own
                # record, and a restricted client never receives an address.
                status = (result[1] if isinstance(result, tuple)
                          and len(result) > 1 else 200)
                return _project_engine_result(_payload_of(result), status)
        except omni_locks.ResourceBusy as busy:
            log.warning("[USER-UNROUTE] %s is busy; waited %.1fs for %s",
                        destination_id, time.monotonic() - began, busy.resource)
            return jsonify({
                "ok": False,
                "reason": "resource_busy",
                "error": "That display is busy with another change. "
                         "Try again in a moment."}), 409

    @app.route("/api/user/source_preview", methods=["GET"])
    def api_user_source_preview():
        """A still picture of one permitted source, by identity.

        Optional, lazy and read-only. Preview availability never decides
        whether a source may be routed: a source whose encoder is not
        generating thumbnails is still a perfectly good source, and says so on
        its card rather than disappearing from the list.
        """
        source_id = omni_users._normalise_identity(
            request.args.get("source") or "")
        if not source_id:
            return jsonify({"ok": False, "error": "source required"}), 400

        def _check(account):
            if not account.permissions.has_feature(omni_access.FEATURE_AV):
                return "missing_feature:av"
            if not account.permissions.interfaces:
                return "interface_not_permitted"
            if not account.permissions.may_use_source(source_id):
                return "source_not_permitted"
            return None

        refusal = _revalidate(_check, "/api/user/source_preview")
        if refusal is not None:
            return refusal

        # Identity to address, here and never in a response. This is the whole
        # reason the endpoint is addressed by identity.
        address = ""
        for unit in load_cache() or []:
            if omni_users._normalise_identity(unit.get("mac")) == source_id:
                address = (unit.get("ip") or "").strip()
                break
        if not address:
            return jsonify({"ok": False, "reason": "preview_unavailable",
                            "error": "That source is not available."}), 404

        # A page that has just been opened asks for a fresh read, so a
        # thumbnail switched on at the encoder shows up on reload rather than
        # whenever a five-minute cache happens to lapse. Hover and scroll do
        # not set it, so an idle page still makes no device request.
        fresh = request.args.get("fresh") == "1"
        body, kind, reason = source_preview(address, fresh=fresh)
        if reason:
            # 200 rather than an error status: there is nothing wrong, the card
            # simply has no picture, and a failed request in the console for
            # every source without a thumbnail is noise an operator would have
            # to learn to ignore.
            return jsonify({"ok": False, "reason": reason}), 200

        response = app.response_class(body, mimetype=kind)
        # A picture of a live input, and one that a permission change must not
        # leave cached in a browser.
        response.headers["Cache-Control"] = "no-store, private"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    def _classify_route_result(result, destination_id):
        """Pass the A/V engine's answer through, noticing one thing.

        The engine used to refuse a route to a compositing decoder with
        `MULTIVIEW ACTIVE` and the restricted shell asked the operator about
        it. A normal route now leaves the Multiview itself, so that refusal is
        no longer produced -- and if it ever is again, it means somebody
        restored the requirement without restoring a way to answer it, which
        would look to an operator exactly like the defect this replaced. So it
        is logged rather than silently reported as an unexplained failure.

        Everything else is passed through untouched: this endpoint does not
        reimplement routing and must not reinterpret a result it does not
        recognise.
        """
        body = _payload_of(result)
        status = result[1] if isinstance(result, tuple) and len(result) > 1 else 200
        if status == 409 and body.get("status") == "MULTIVIEW ACTIVE":
            log.error("[USER-ROUTE] %s refused the route because it is showing "
                      "Multiview %s. A normal route is supposed to leave it.",
                      destination_id, body.get("multiview"))
        return _project_engine_result(body, status)

    #: The only keys an engine result may carry out to a restricted client.
    #: Everything else the A/V engine returns describes the device -- its
    #: address, its multicast groups, its input names -- and none of it belongs
    #: in a projection whose whole purpose is that no device address reaches
    #: the browser.
    #
    # `cleared` and `failed` are deliberately NOT here. They name the decoder's
    # own inputs -- ip_input1, ip_input3 -- which is engineering detail this
    # projection exists to keep out of a restricted browser, and the operator
    # is told what happened by `status` and `error` without them. They were in
    # this list briefly, which is the argument for the list being a fixed
    # allowlist: widening it has to be a deliberate act that somebody reads.
    _ENGINE_RESULT_KEYS = ("ok", "status", "reason", "error", "note", "planes")

    def _project_engine_result(body, status):
        """The A/V engine's answer, reduced to named fields.

        The engine is written for the administrative surface and answers with
        the decoder's own record -- `decoder.ip`, `ip1_addr`, the multicast
        addresses it just wrote. Handing that to a restricted client would put
        a device address in a browser that the projection exists to keep them
        out of, so the result is rebuilt from a fixed list rather than filtered
        by removing the fields anybody remembered.

        `multiview_exit` is summarised rather than forwarded: whether the
        display left a Multiview, and whether a rollback was verified, are
        facts about the operation; the released input numbers are not.
        """
        out = {key: body[key] for key in _ENGINE_RESULT_KEYS if key in body}
        out.setdefault("ok", False)
        exit_detail = body.get("multiview_exit") or body.get("multiview")
        if isinstance(exit_detail, dict):
            summary = {"left": bool(exit_detail.get("ok"))}
            rollback = exit_detail.get("rollback")
            if isinstance(rollback, dict):
                summary["restored"] = bool(rollback.get("restored"))
            out["multiview_exit"] = summary
        return jsonify(out), status

    def _payload_of(result):
        """The JSON body of whatever a view returned, without raising.

        A view hands back a Response, or (Response, status). Reading
        `.get_data()` off the tuple raises, and inside a request that surfaces
        as a 500 claiming the application is broken.
        """
        import json as _json
        response = result[0] if isinstance(result, tuple) and result else result
        getter = getattr(response, "get_data", None)
        if getter is None:
            return response if isinstance(response, dict) else {}
        try:
            return _json.loads(getter() or b"{}")
        except ValueError:
            return {}

    def _current_account():
        """The account as it stands right now, not as the gate found it.

        Authorisation belongs at the operation boundary. Between the gate
        admitting a request and the handler acting on it, an administrator may
        have removed a permission, disabled the account or changed its
        password, and a long-running transaction must not proceed on what was
        true when it started.

        Returns the live account, None for the local operator, or _REVOKED if
        the session no longer resolves.
        """
        if getattr(g, "is_local", False):
            return None
        token = request.cookies.get(SESSION_COOKIE)
        account, _session = store.resolve_session(token)
        return account if account is not None else _REVOKED

    def _revalidate(check, rule):
        """Still allowed? Asked again after waiting for a resource lock.

        Returns None when the operation may proceed, or the response to send
        instead. `check` takes the freshly read account and returns a reason
        string, or None if it is satisfied.

        This exists because acquiring a lock is a wait. Everything decided
        before it describes a world that may have moved on: an administrator
        can disable an account, change its permissions or take a display away
        while a request sits in the queue, and the far side of the wait is the
        last moment before a device is written to.
        """
        account = _current_account()
        if account is _REVOKED:
            return jsonify({"ok": False,
                            "error": "your sign-in is no longer valid"}), 401
        if account is not None and not account.is_admin:
            reason = check(account)
            if reason is not None:
                return _denied(account, reason, rule)
        return None

    def _denied(account, reason, rule="/api/user/route"):
        """A refusal that says which refusal it is.

        The sentence comes from the gate's map rather than a second copy here:
        the same cause is caught in one layer or the other depending on which
        check reaches it first, and two maps would eventually disagree about
        what the same refusal means.
        """
        store.audit("access.denied", actor=account,
                    detail={"rule": rule, "reason": reason})
        sentence = omni_gate.DENIAL_SENTENCES.get(reason)
        if sentence is None and reason.startswith("missing_feature:"):
            sentence = "Your account does not have that feature."
        return jsonify({"ok": False,
                        "error": sentence or "not permitted",
                        "reason": reason}), 403

    def _address_index():
        """identity -> current address, from host-authoritative state."""
        index = {}
        for unit in load_cache() or []:
            identity = omni_users._normalise_identity(unit.get("mac"))
            if identity and unit.get("ip"):
                index[identity] = unit["ip"]
        return index

    def _resolve_addresses(*identities):
        """identity -> (address, reason), failing closed on an ambiguous record.

        Discovery keeps one row per hardware identity and one per address, but
        this is the last step before a write reaches a device, so it does not
        assume that. An identity with rows at two addresses, or an address that
        two identities both claim, cannot be resolved from what is recorded: the
        write would go to whichever row happened to be last, and that can be a
        display that was never in the request. `reason` is "" when the address is
        unambiguous, "not_found" when none is recorded, and "ambiguous" otherwise.
        """
        units = load_cache() or []
        owners: dict = {}
        addresses: dict = {}
        for unit in units:
            identity = omni_users._normalise_identity(unit.get("mac"))
            address = str(unit.get("ip") or "")
            if not identity or not address:
                continue
            addresses.setdefault(identity, set()).add(address)
            owners.setdefault(address, set()).add(identity)
        out = {}
        for identity in identities:
            found = addresses.get(identity)
            if not found:
                out[identity] = (None, "not_found")
            elif len(found) > 1 or any(owners[a] != {identity} for a in found):
                log.warning("[USER-ROUTE] refusing an ambiguous record for %s "
                            "(%d address(es), claimed by %s identity(ies))",
                            identity, len(found),
                            max(len(owners[a]) for a in found))
                out[identity] = (None, "ambiguous")
            else:
                out[identity] = (next(iter(found)), "")
        return out

    NO_LONGER_AVAILABLE = "a display that is no longer available"

    def _member_label(identity):
        """What a restricted user is told a group member is called.

        A member the inventory no longer holds (cleared, replaced, never found) has
        no name to give, and the identity is a hardware address: neither the address
        nor anything derived from it goes into a reply to a restricted account.
        """
        for unit in load_cache() or []:
            if omni_users._normalise_identity(unit.get("mac")) == identity:
                return _label(identity)
        return NO_LONGER_AVAILABLE

    def _label(identity, fallback=""):
        """A name safe to put in a message or an audit entry.

        The alias if one is set, else the device's own hostname, and only as a
        last resort the identity itself. Falling straight through to the
        identity put a bare MAC on screen as a display name for any device
        nobody had named -- correct, unique and useless to an operator.
        """
        if fallback:
            return aliases.display_name(identity, fallback)
        for unit in load_cache() or []:
            if omni_users._normalise_identity(unit.get("mac")) == identity:
                return aliases.display_name(
                    identity, unit.get("hostname") or unit.get("name") or identity)
        return aliases.display_name(identity, identity)

    # ------------------------------------------------------------ USB ----
    @app.route("/api/user/usb_state", methods=["GET"])
    def api_user_usb_state():
        """The USB endpoints this account may operate.

        A projection, not the administrative payload: that one carries firmware
        revisions, subnet masks, host ports, filters and protocol provenance
        for every endpoint on the network.
        """
        account = _principal() or _LocalAdmin()
        if not (account.is_admin
                or account.permissions.has_feature(omni_access.FEATURE_USB)):
            return _denied(account, "missing_feature:usb", "/api/user/usb_state")
        return jsonify(omni_projection.project_usb_state(
            usb_state(), load_cache() or [], account, aliases))

    @app.route("/api/user/usb_route", methods=["POST"])
    def api_user_usb_route():
        """Give a permitted computer a permitted set of peripherals, or take
        them away.

        Adds no USB logic. It authorises both endpoints by stable identity,
        then hands the same two MACs to the verified pair/unpair path, which
        keeps its own eligibility, read-back and rollback behaviour.
        """
        data = _json_body()
        computer = omni_users._normalise_identity(data.get("computer"))
        peripherals = omni_users._normalise_identity(data.get("peripherals"))
        detach = bool(data.get("detach"))
        if not computer or not peripherals:
            return jsonify({"ok": False,
                            "error": "computer and peripherals required"}), 400

        def _check(account):
            if not account.permissions.has_feature(omni_access.FEATURE_USB):
                return "missing_feature:usb"
            # Both ends, against the identity the projection authorised them
            # under -- never the endpoint MAC a browser happened to send.
            allowed = _usb_permitted_endpoints(account)
            if computer not in allowed["computers"]:
                return "computer_not_permitted"
            if peripherals not in allowed["peripherals"]:
                return "peripherals_not_permitted"
            return None

        refusal = _revalidate(_check, "/api/user/usb_route")
        if refusal is not None:
            return refusal

        view = usb_unpair_view if detach else usb_pair_view
        try:
            # Both endpoints, sorted by hold(), so an attach and a detach
            # racing over the same pair cannot each hold one of them.
            with locks.hold(*locks.usb_pair(computer, peripherals)):
                # The wait is over and the next statement reaches hardware.
                refusal = _revalidate(_check, "/api/user/usb_route")
                if refusal is not None:
                    return refusal
                store.audit("usb.detach" if detach else "usb.attach",
                            actor=_current_account(),
                            detail={"computer": computer,
                                    "peripherals": peripherals,
                                    "computer_label": _label(computer),
                                    "peripherals_label": _label(peripherals)})
                try:
                    with app.test_request_context(
                            "/api/usb_route", method="POST",
                            json={"lex_mac": computer, "rex_mac": peripherals}):
                        return view()
                finally:
                    # This just changed what a USB read would report.
                    forget_observation("usb")
        except omni_locks.ResourceBusy as busy:
            log.warning("[USER-USB] busy: %s", busy.resource)
            return jsonify({"ok": False,
                            "reason": "resource_busy",
                            "error": "That equipment is busy with another "
                                     "change. Try again in a moment."}), 409

    def _usb_permitted_endpoints(account):
        """The USB endpoint identities this account may act on.

        Derived from the same projection the browser was shown, so a request
        cannot name an endpoint the account was never offered.
        """
        projected = omni_projection.project_usb_state(
            usb_state(), load_cache() or [], account, aliases)
        return {"computers": {c["id"] for c in projected["computers"]},
                "peripherals": {p["id"] for p in projected["peripherals"]}}

    return app

#: Returned by _current_account when a session stopped resolving mid-request.
#: A sentinel rather than None, because None is the local operator and the two
#: must never be confused.
_REVOKED = object()


class _LocalAdmin:
    """The local operator, as the projection expects an account to look.

    Not stored, never persisted, and never authenticated -- it exists so the
    user shell can be opened on the host machine without inventing an account.
    """

    is_admin = True
    username = None
    permissions = Permissions(features=omni_access.FEATURES)


REMOTE_MIN_PORT = 1024
REMOTE_MAX_PORT = 65535


def _remote_port_problem(value) -> str:
    """Why `value` cannot be the Remote Access port, or "" when it can.

    A whole number from 1024 to 65535. Not a boolean (True is 1), not a float,
    not text that merely contains digits with a sign or a space, and nothing below
    1024: those are the ports other services own and need privileges to bind.
    """
    text = "Choose a port from %d to %d." % (REMOTE_MIN_PORT, REMOTE_MAX_PORT)
    if isinstance(value, bool):
        return text
    if isinstance(value, str):
        if not (value.isascii() and value.isdigit() and len(value) <= 6):
            return text
        value = int(value)
    if not isinstance(value, int):
        return text
    return "" if REMOTE_MIN_PORT <= value <= REMOTE_MAX_PORT else text


def _identity_problem(raw) -> str:
    """Why a grant names something that is not a hardware address, or "".

    Sources and displays are granted by MAC. An address or a name is not an
    identity, and the normaliser no longer converts one into a plausible MAC, so
    it would be stored as a grant that can never match anything.
    """
    if not isinstance(raw, dict):
        return ""
    for key in ("sources", "destinations"):
        value = raw.get(key)
        if value is None:
            continue
        if not isinstance(value, (list, tuple)):
            return "%s must be a list of hardware addresses." % key.capitalize()
        for item in value:
            if not (isinstance(item, str) and omni_users.is_mac_identity(item)):
                return ("Sources and displays are chosen by hardware address. "
                        "An IP address or a name cannot be used.")
    return ""


def _permissions_from(raw) -> Permissions:
    """Build a Permissions from request data, ignoring anything unrecognised.

    A malformed payload yields an empty grant rather than an error or a partial
    one: the safe failure for a permission is "none".
    """
    raw = raw if isinstance(raw, dict) else {}

    def _list(key):
        value = raw.get(key)
        if isinstance(value, (list, tuple)):
            return [str(v) for v in value if isinstance(v, (str, int))]
        return []

    # Absent means "not stated", which a caller that predates the routing
    # interfaces never does; a stated list, even an empty one, is believed.
    interfaces = raw.get("interfaces")
    return Permissions(features=_list("features"),
                       sources=_list("sources"),
                       destinations=_list("destinations"),
                       groups=_list("groups"),
                       interfaces=_list("interfaces")
                       if isinstance(interfaces, (list, tuple)) else None)


def _adapters(app):
    """The adapters the product already enumerates, via its own endpoint."""
    try:
        with app.test_request_context("/api/adapters"):
            from flask import current_app
            view = current_app.view_functions.get("api_adapters")
            if view is None:
                return []
            # A view may return (response, status); reading get_data off
            # the tuple raises, and the caller sees a 500 for what is usually
            # an ordinary "nothing to report".
            result = view()
            if isinstance(result, tuple):
                result = result[0] if result else None
            import json
            payload = json.loads(
                (result.get_data() if result is not None else b"{}") or b"{}")
            return payload.get("adapters") or []
    except Exception as exc:
        log.info("[REMOTE] adapter enumeration failed: %s", type(exc).__name__)
        return []
