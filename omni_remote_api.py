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

from flask import g, jsonify, request

import omni_access
import omni_projection
import omni_remote
import omni_users
from omni_gate import CSRF_HEADER, SESSION_COOKIE
from omni_users import Permissions

log = logging.getLogger(__name__)


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
             forget_observation):
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
        return jsonify({**omni_projection.session_summary(account), "local": False})

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
        result = store.create(username, password, omni_users.ROLE_ADMIN)
        if not result["ok"]:
            return jsonify(result), 400
        store.audit("bootstrap.admin_created", actor=None,
                    detail={"username": username})
        return jsonify(result)

    # ------------------------------------------------------ remote access
    @app.route("/api/remote_access", methods=["GET"])
    def api_remote_access_state():
        adapters = omni_remote.usable_bind_addresses(_adapters(app))
        state = listener.describe()
        saved = remote_config()
        # Configured and running are different facts, and an installed system
        # needs to be able to tell them apart: a saved address whose adapter has
        # gone away leaves remote access configured and unavailable, which is
        # neither "on" nor "off".
        configured = bool(saved.get("enabled"))
        available = any(a["address"] == saved.get("address") for a in adapters)
        return jsonify({
            "ok": True,
            **state,
            "adapters": adapters,
            "configured": configured,
            "configured_address": saved.get("address") or "",
            "configured_available": bool(configured and available),
            "restore_error": saved.get("restore_error") or "",
            "has_admin": store.has_admin,
            "sessions": store.active_session_count(),
            # Said plainly so the Settings page cannot imply otherwise.
            "transport": "http",
            "encrypted": False,
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
        port = data.get("port") or request.environ.get("SERVER_PORT") or 8080
        result = listener.start(address, port)
        if not result.get("ok"):
            return jsonify(result), 400
        store.audit("remote_access.enabled", actor=_principal(),
                    detail={"address": address, "port": int(port)})
        set_remote_state(True, address, int(port))
        return jsonify(result)

    # -------------------------------------------------- admin: accounts
    @app.route("/api/admin/users", methods=["GET"])
    def api_admin_users():
        return jsonify({"ok": True, "users": store.list_accounts()})

    @app.route("/api/admin/users", methods=["POST"])
    def api_admin_create_user():
        data = request.get_json(silent=True) or {}
        result = store.create(
            str(data.get("username") or "").strip(),
            str(data.get("password") or ""),
            str(data.get("role") or omni_users.ROLE_USER),
            _permissions_from(data.get("permissions")))
        if not result["ok"]:
            return jsonify(result), 400
        store.audit("user.create", actor=_principal(),
                    detail={"username": data.get("username"),
                            "role": data.get("role")})
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

        if action == "set_enabled":
            result = store.set_enabled(user_id, bool(data.get("enabled")))
        elif action == "set_role":
            result = store.set_role(user_id, str(data.get("role") or ""))
        elif action == "set_password":
            result = store.set_password(user_id, str(data.get("password") or ""))
        elif action == "set_permissions":
            result = store.set_permissions(
                user_id, _permissions_from(data.get("permissions")))
        elif action == "delete":
            result = store.delete(user_id)
        else:
            return jsonify({"ok": False, "error": "unknown action"}), 400

        if not result.get("ok"):
            return jsonify(result), 400
        store.audit("user." + action, actor=actor, detail={"user_id": user_id})
        return jsonify(result)

    @app.route("/api/admin/devices", methods=["GET"])
    def api_admin_devices():
        """Devices an administrator can assign or name, by stable identity.

        The identity is what a permission stores, so the assignment UI and the
        enforcement agree by construction rather than by both happening to
        normalise the same way.

        An administrator gets the device's own name, model and address
        alongside the alias: two displays both called "Front Display" are only
        distinguishable by what is underneath them.
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
        return jsonify({"ok": True, "entries": store.audit_entries(200)})

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

        account = _principal()
        if account is not None and not account.is_admin:
            # The gate checked these too. Checking again here is not
            # redundancy for its own sake: this endpoint translates identities
            # into addresses, and the check has to be on the same values the
            # translation will use, or the two could drift apart.
            if not account.permissions.has_feature(omni_access.FEATURE_AV):
                return _denied(account, "missing_feature:av")
            if not account.permissions.may_use_source(source_id):
                return _denied(account, "source_not_permitted")
            if not account.permissions.may_control_destination(destination_id):
                return _denied(account, "destination_not_permitted")

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

        store.audit("route", actor=account, detail={
            "source": source_id, "destination": destination_id, "mode": mode})

        # The verified path, called rather than reimplemented.
        with app.test_request_context(
                "/api/route", method="POST",
                json={"encoder": encoder_ip, "decoder": decoder_ip, "mode": mode}):
            return route_view()

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

        account = _principal()
        if account is not None and not account.is_admin:
            if not account.permissions.has_feature(omni_access.FEATURE_USB):
                return _denied(account, "missing_feature:usb", "/api/user/usb_route")
            # Both ends, against the identity the projection authorised them
            # under -- never the endpoint MAC a browser happened to send.
            allowed = _usb_permitted_endpoints(account)
            if computer not in allowed["computers"]:
                return _denied(account, "computer_not_permitted",
                               "/api/user/usb_route")
            if peripherals not in allowed["peripherals"]:
                return _denied(account, "peripherals_not_permitted",
                               "/api/user/usb_route")

        store.audit("usb.detach" if detach else "usb.attach", actor=account,
                    detail={"computer": computer, "peripherals": peripherals,
                            "computer_label": _label(computer),
                            "peripherals_label": _label(peripherals)})
        view = usb_unpair_view if detach else usb_pair_view
        try:
            with app.test_request_context(
                    "/api/usb_route", method="POST",
                    json={"lex_mac": computer, "rex_mac": peripherals}):
                return view()
        finally:
            # This just changed what a USB read would report.
            forget_observation("usb")

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
        account = _principal()

        refusal = _multiview_gate(account, identity, name,
                                  "/api/user/multiview_show")
        if refusal:
            return refusal

        address = _address_index().get(identity)
        if not address:
            return jsonify({"ok": False,
                            "error": "that display is not currently known"}), 404

        store.audit("multiview.show", actor=account,
                    detail={"display": identity, "display_label": _label(identity),
                            "preset": name})
        try:
            with app.test_request_context("/api/multiview/show", method="POST",
                                          json={"decoder": address,
                                                "name": name}):
                return multiview_show_view()
        finally:
            forget_observation("multiview:" + str(address))

    @app.route("/api/user/multiview_switch", methods=["POST"])
    def api_user_multiview_switch():
        """Change one window of the Multiview that is on a permitted display."""
        data = request.get_json(silent=True) or {}
        identity = omni_users._normalise_identity(data.get("display"))
        name = str(data.get("preset") or "").strip()
        cell = str(data.get("window") or "").strip()
        source = omni_users._normalise_identity(data.get("source"))
        account = _principal()

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

        store.audit("multiview.switch", actor=account,
                    detail={"display": identity, "display_label": _label(identity),
                            "preset": name, "window": cell,
                            "source": source, "source_label": _label(source)})
        try:
            with app.test_request_context(
                    "/api/multiview/switch", method="POST",
                    json={"decoder": display_address, "name": name,
                          "cell": cell, "source": source_address}):
                return multiview_switch_view()
        finally:
            forget_observation("multiview:" + str(display_address))

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
            import json
            payload = json.loads(view().get_data())
            return payload.get("adapters") or []
    except Exception as exc:
        log.info("[REMOTE] adapter enumeration failed: %s", type(exc).__name__)
        return []
