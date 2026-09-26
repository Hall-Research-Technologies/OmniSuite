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

from flask import Response, g, jsonify, redirect, request

import omni_adapters
import omni_access
import omni_locks
import omni_projection
import omni_remote
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


def _principal():
    return getattr(g, "principal", None)


def _is_local():
    return bool(getattr(g, "is_local", False))


def register(app, *, store, listener, aliases, load_cache, matrix_state,
             route_view, remote_enabled, set_remote_state, remote_config,
             usb_state, usb_pair_view, usb_unpair_view,
             multiview_state, multiview_show_view, multiview_switch_view,
             forget_observation, locks, tls, cert_paths, tls_context_for,
             certificate_state):
    """Attach the remote-access API.

    Everything the module needs is passed in rather than imported from the
    server, so this file has no opinion about how devices are read and cannot
    accidentally start reading them.
    """

    # ---------------------------------------------------------------- auth
    @app.route("/api/auth/login", methods=["POST"])
    def api_auth_login():
        data = request.get_json(silent=True) or {}
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
        data = request.get_json(silent=True) or {}
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
        transport = saved.get("transport") or "https"
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
        data = request.get_json(silent=True) or {}
        want = bool(data.get("enabled"))
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
        transport = "http" if data.get("transport") == "http" else "https"

        # An adapter chosen by identity decides the address, so a stale one in
        # the request body cannot send the listener somewhere else.
        adapter_name = ""
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
        if transport == "http" and not data.get("accept_insecure"):
            return jsonify({
                "ok": False,
                "error": "Remote access without HTTPS sends sign-in details in "
                         "clear. Confirm you intend that, or configure a "
                         "certificate.",
                "needs_confirmation": True}), 400

        proposed = {"transport": transport,
                    "cert_path": str(data.get("cert_path") or ""),
                    "key_path": str(data.get("key_path") or "")}
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
                        f"{described.get('subject') or 'another address'}, not "
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
        data = request.get_json(silent=True) or {}
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
        data = request.get_json(silent=True) or {}
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
        data = request.get_json(silent=True) or {}
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
            result = store.set_permissions(
                user_id, _permissions_from(data.get("permissions")), actor=actor)
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

        data = request.get_json(silent=True) or {}
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
        """Open a restricted session from a bearer link, then get rid of the URL.

        The token is exchanged for an ordinary server-side session and the
        browser is redirected to /user, so the credential is not left sitting
        in the address bar to be screenshotted, shoulder-read or copied out of
        a history list by the next person to use the tablet.
        """
        source = _client_address()
        # A refused link is throttled like a refused password, for two
        # reasons. It bounds the cost of asking; and without it, requests for
        # `/access/<anything>` would write an audit entry each, pushing the
        # sign-ins and refusals an administrator needs out of a history that
        # keeps a fixed amount and discards the oldest.
        if store.locked_out(_DIRECT_THROTTLE_KEY, source):
            return Response(_LINK_REFUSED, status=404, mimetype="text/html")

        account = store.resolve_direct_token(token)
        if account is None:
            # One answer for every reason: unknown, revoked, disabled, deleted,
            # promoted. Saying which would tell whoever has an old URL
            # something about an account they have no business knowing.
            store.record_failure(_DIRECT_THROTTLE_KEY, source)
            store.audit("direct_access.used", actor=None,
                        detail={"result": "refused"})
            return Response(_LINK_REFUSED, status=404, mimetype="text/html")

        session = store.open_session(account.id,
                                     method=omni_users.BY_DIRECT_LINK)
        store.audit("direct_access.used", actor=account,
                    detail={"result": "accepted",
                            "username": account.username,
                            "method": omni_users.BY_DIRECT_LINK})
        response = redirect("/user", code=302)
        response.set_cookie(
            SESSION_COOKIE, session.token,
            httponly=True, samesite="Lax",
            secure=request.is_secure, path="/")
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
        data = request.get_json(silent=True) or {}
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

    @app.route("/api/admin/multiviews", methods=["GET"])
    def api_admin_multiviews():
        """The saved Multiviews on one display, for approving them to a user.

        Read on demand for the display an administrator selected, not for every
        decoder in the deployment: a preset list is a device read, and building
        one for the whole site to populate a dropdown would be exactly the kind
        of traffic the rest of this architecture avoids.
        """
        identity = omni_users._normalise_identity(request.args.get("display"))
        if not identity:
            return jsonify({"ok": False, "error": "display required"}), 400
        address = None
        for unit in load_cache() or []:
            if omni_users._normalise_identity(unit.get("mac")) == identity:
                address = unit.get("ip")
                break
        if not address:
            return jsonify({"ok": False, "error": "that display is not known"}), 404
        state = multiview_state(address)
        if not state.get("ok"):
            return jsonify({"ok": False,
                            "error": "that display did not answer"}), 502
        presets = [{
            "name": entry.get("name") or "",
            "label": entry.get("layout_label") or entry.get("name") or "",
            "key": omni_users.multiview_key(identity, entry.get("name") or ""),
            "showing": bool(entry.get("selected_on_output")),
        } for entry in (state.get("multiviews") or []) if entry.get("name")]
        presets.sort(key=lambda entry: entry["label"].lower())
        return jsonify({"ok": True, "display": identity, "presets": presets})

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
            matrix_state(), load_cache() or [], account, aliases))

    @app.route("/api/user/route", methods=["POST"])
    def api_user_route():
        """Route a permitted source to a permitted destination.

        Pulled forward from Phase G, deliberately and minimally: without it the
        restricted shell would be a picture of a product rather than one. It
        adds no routing logic -- it resolves the two identities to the addresses
        the verified route path expects and hands over to that path unchanged.
        """
        data = request.get_json(silent=True) or {}
        source_id = omni_users._normalise_identity(data.get("source"))
        destination_id = omni_users._normalise_identity(data.get("destination"))
        mode = str(data.get("mode") or "av").lower()
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

        address_of = {}
        for unit in load_cache() or []:
            identity = omni_users._normalise_identity(unit.get("mac"))
            if identity and unit.get("ip"):
                address_of[identity] = unit["ip"]
        encoder_ip = address_of.get(source_id)
        decoder_ip = address_of.get(destination_id)
        if not encoder_ip or not decoder_ip:
            return jsonify({"ok": False,
                            "error": "that device is not currently known"}), 404

        # The verified path, called rather than reimplemented, with the
        # destination held so two operators cannot interleave inside one
        # transaction. Last successful operation wins; the loser waits for the
        # winner to finish rather than corrupting it.
        try:
            with locks.hold(locks.decoder(destination_id)):
                # Again, now that the wait is over and the next statement
                # writes to hardware.
                refusal = _revalidate(_check, "/api/user/route")
                if refusal is not None:
                    return refusal
                store.audit("route", actor=_current_account(), detail={
                    "source": source_id, "destination": destination_id,
                    "mode": mode})
                with app.test_request_context(
                        "/api/route", method="POST",
                        json={"encoder": encoder_ip, "decoder": decoder_ip,
                              "mode": mode}):
                    return route_view()
        except omni_locks.ResourceBusy:
            return jsonify({"ok": False,
                            "error": "That display is busy with another "
                                     "change. Try again in a moment."}), 409

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
        store.audit("access.denied", actor=account,
                    detail={"rule": rule, "reason": reason})
        return jsonify({"ok": False, "error": "not permitted"}), 403

    def _address_index():
        """identity -> current address, from host-authoritative state."""
        index = {}
        for unit in load_cache() or []:
            identity = omni_users._normalise_identity(unit.get("mac"))
            if identity and unit.get("ip"):
                index[identity] = unit["ip"]
        return index

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
        data = request.get_json(silent=True) or {}
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
        except omni_locks.ResourceBusy:
            return jsonify({"ok": False,
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

    # ------------------------------------------------------ Multiview ----
    @app.route("/api/user/multiview_state", methods=["GET"])
    def api_user_multiview_state():
        """Approved Multiviews on one permitted display."""
        account = _principal() or _LocalAdmin()
        if not (account.is_admin
                or account.permissions.has_feature(omni_access.FEATURE_MULTIVIEW)):
            return _denied(account, "missing_feature:multiview",
                           "/api/user/multiview_state")
        identity = omni_users._normalise_identity(request.args.get("display"))
        if not identity:
            return jsonify({"ok": False, "error": "display required"}), 400
        if not (account.is_admin
                or account.permissions.may_control_destination(identity)):
            return _denied(account, "destination_not_permitted",
                           "/api/user/multiview_state")

        address = _address_index().get(identity)
        if not address:
            return jsonify({"ok": False,
                            "error": "that display is not currently known"}), 404
        state = multiview_state(address)
        if not state.get("ok"):
            # An unreachable decoder is an upstream condition, not a fault in
            # this application, and the operator only needs to know the display
            # is not answering.
            return jsonify({"ok": False, "error": "that display is unavailable",
                            "display": {"id": identity,
                                        "name": _label(identity)}}), 502
        return jsonify(omni_projection.project_multiview_state(
            identity, _label(identity), state, load_cache() or [],
            account, aliases))

    @app.route("/api/user/multiview_show", methods=["POST"])
    def api_user_multiview_show():
        """Put an approved Multiview on a permitted display."""
        data = request.get_json(silent=True) or {}
        identity = omni_users._normalise_identity(data.get("display"))
        name = str(data.get("preset") or "").strip()
        account = _current_account()
        if account is _REVOKED:
            return jsonify({"ok": False,
                            "error": "your sign-in is no longer valid"}), 401

        refusal = _multiview_gate(account, identity, name,
                                  "/api/user/multiview_show")
        if refusal:
            return refusal

        address = _address_index().get(identity)
        if not address:
            return jsonify({"ok": False,
                            "error": "that display is not currently known"}), 404

        try:
            with locks.hold(locks.decoder(identity)):
                # The wait is over and the next statement reaches hardware.
                refusal = _revalidate_multiview(
                    identity, name, "/api/user/multiview_show")
                if refusal is not None:
                    return refusal
                store.audit("multiview.show", actor=_current_account(),
                            detail={"display": identity,
                                    "display_label": _label(identity),
                                    "preset": name})
                try:
                    with app.test_request_context(
                            "/api/multiview/show", method="POST",
                            json={"decoder": address, "name": name}):
                        return multiview_show_view()
                finally:
                    forget_observation("multiview:" + str(address))
        except omni_locks.ResourceBusy:
            return jsonify({"ok": False,
                            "error": "That display is busy with another "
                                     "change. Try again in a moment."}), 409

    @app.route("/api/user/multiview_switch", methods=["POST"])
    def api_user_multiview_switch():
        """Change one window of the Multiview that is on a permitted display."""
        data = request.get_json(silent=True) or {}
        identity = omni_users._normalise_identity(data.get("display"))
        name = str(data.get("preset") or "").strip()
        cell = str(data.get("window") or "").strip()
        source = omni_users._normalise_identity(data.get("source"))
        account = _current_account()
        if account is _REVOKED:
            return jsonify({"ok": False,
                            "error": "your sign-in is no longer valid"}), 401

        refusal = _multiview_gate(account, identity, name,
                                  "/api/user/multiview_switch")
        if refusal:
            return refusal
        if not cell or not source:
            return jsonify({"ok": False,
                            "error": "window and source required"}), 400
        if account is not None and not account.is_admin:
            if not account.permissions.may_use_source(source):
                return _denied(account, "source_not_permitted",
                               "/api/user/multiview_switch")

        addresses = _address_index()
        display_address = addresses.get(identity)
        source_address = addresses.get(source)
        if not display_address:
            return jsonify({"ok": False,
                            "error": "that display is not currently known"}), 404
        if not source_address:
            return jsonify({"ok": False,
                            "error": "that source is not currently known"}), 404

        try:
            with locks.hold(locks.decoder(identity)):
                # The wait is over and the next statement reaches hardware.
                refusal = _revalidate_multiview(
                    identity, name, "/api/user/multiview_switch", source=source)
                if refusal is not None:
                    return refusal
                store.audit("multiview.switch", actor=_current_account(),
                            detail={"display": identity,
                                    "display_label": _label(identity),
                                    "preset": name, "window": cell,
                                    "source": source,
                                    "source_label": _label(source)})
                try:
                    with app.test_request_context(
                            "/api/multiview/switch", method="POST",
                            json={"decoder": display_address, "name": name,
                                  "cell": cell, "source": source_address}):
                        return multiview_switch_view()
                finally:
                    forget_observation("multiview:" + str(display_address))
        except omni_locks.ResourceBusy:
            return jsonify({"ok": False,
                            "error": "That display is busy with another "
                                     "change. Try again in a moment."}), 409

    def _revalidate_multiview(identity, name, rule, source=None):
        """The Multiview gate, asked again after waiting for the lock.

        Same rule, freshly read account. Acquiring a lock is a wait, and an
        approval can be withdrawn during one.
        """
        account = _current_account()
        if account is _REVOKED:
            return jsonify({"ok": False,
                            "error": "your sign-in is no longer valid"}), 401
        refusal = _multiview_gate(account, identity, name, rule)
        if refusal:
            return refusal
        if source and account is not None and not account.is_admin:
            if not account.permissions.may_use_source(source):
                return _denied(account, "source_not_permitted", rule)
        return None

    def _multiview_gate(account, identity, name, rule):
        """Capability, display and preset approval. None means proceed.

        The preset check is not only "is it approved": the projection is asked
        for it, so a preset whose current content includes a source this
        account may not use is unreachable even when its name is approved. That
        is what keeps a reused preset name from carrying an old approval onto
        new content.
        """
        if not identity or not name:
            return jsonify({"ok": False,
                            "error": "display and preset required"}), 400
        if account is None or account.is_admin:
            return None
        if not account.permissions.has_feature(omni_access.FEATURE_MULTIVIEW):
            return _denied(account, "missing_feature:multiview", rule)
        if not account.permissions.may_control_destination(identity):
            return _denied(account, "destination_not_permitted", rule)
        if not account.permissions.may_operate_multiview(identity, name):
            return _denied(account, "multiview_not_approved", rule)

        address = _address_index().get(identity)
        if not address:
            return jsonify({"ok": False,
                            "error": "that display is not currently known"}), 404
        state = multiview_state(address)
        if not state.get("ok"):
            return jsonify({"ok": False,
                            "error": "that display is unavailable"}), 502
        projected = omni_projection.project_multiview_state(
            identity, _label(identity), state, load_cache() or [],
            account, aliases)
        if not any(p["name"] == name for p in projected["presets"]):
            return _denied(account, "multiview_content_not_permitted", rule)
        return None

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

    return Permissions(features=_list("features"),
                       sources=_list("sources"),
                       destinations=_list("destinations"),
                       # Approved Multiviews. Omitting this quietly dropped
                       # every approval an administrator made: the account was
                       # saved, the UI showed the tick, and the preset was
                       # never reachable. Unit tests built Permissions
                       # directly and could not see it; a live run could.
                       multiviews=_list("multiviews"))


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
