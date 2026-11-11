"""Break each security predicate on purpose and require the suite to notice.

A test that cannot fail proves nothing. Every entry below is a deliberate
defect in a decision the remote-access boundary depends on: the listener trust
model, route classification, the role check, the per-device check, Multiview
approval, session revocation, CSRF, the Secure cookie, the HTTPS-required path,
certificate failure, address validation, the resource locks, the state cache
and audit redaction.

For each one the harness edits the file, runs the tests that claim to cover it,
restores the file, and records whether anything failed. A mutation nobody
noticed is a gap to investigate -- a real one, a test one, or a control that
is genuinely redundant -- and never something to wave through.

    python tools/mutate_security.py            # all of them
    python tools/mutate_security.py listener   # only ids containing "listener"

The runner records each suite's whole output, not its last line, because a
runner that reads only the summary reports every mutation as uncaught and looks
exactly like tests that do not work.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Suites, by the thing they are supposed to be watching.
GATE = ["tests.test_remote_authorization", "tests.test_security_review",
        "tests.test_user_operations"]
LISTENER = ["tests.test_remote_access", "tests.test_hardening",
            "tests.test_security_review"]
USERS = ["tests.test_user_operations", "tests.test_login_hardening",
         "tests.test_security_review"]
REMOTE = ["tests.test_remote_access", "tests.test_remote_persistence",
          "tests.test_hardening", "tests.test_login_hardening"]

#: The guarantees this release-candidate pass added.
RELOCK = ["tests.test_lock_revalidation"]
META = ["tests.test_device_metadata", "tests.test_user_projection"]
AUDIT = ["tests.test_account_audit"]
PREFS = ["tests.test_security_review", "tests.test_remote_authorization"]

#: The remote-browser fix and the Direct Access link.
BROWSER = ["tests.test_remote_browser_flow", "tests.test_remote_access"]
CERT = ["tests.test_certificate_address", "tests.test_remote_access"]
CERTCHOICE = ["tests.test_remote_certificate_choice"]
CERTID = ["tests.test_certificate_identity"]
ATTEMPTS = ["tests.test_attempt_log_bound"]
ROUTEDIAG = ["tests.test_route_diag"]
CERTUI = ["tests.test_local_port_ui"]
HOSTH = ["tests.test_host_header", "tests.test_remote_access"]
BACKKDF = ["tests.test_backup_kdf_bounds"]
THROTTLE = ["tests.test_throttle_bounds"]
SINGLE = ["tests.test_single_instance"]
DEST = ["tests.test_remote_destinations"]
ESC = ["tests.test_device_text_escaping"]
DSB = ["tests.test_direct_session_bounds", "tests.test_direct_access"]
RESTORE = ["tests.test_restore_atomicity", "tests.test_backup"]
CREDFILTER = ["tests.test_remote_credential_filter", "tests.test_account_audit"]
CONFIGSAVE = ["tests.test_config_save", "tests.test_account_audit"]
DIRECT = ["tests.test_direct_access", "tests.test_remote_browser_flow",
          "tests.test_security_review"]

#: Restricted-user routing and source previews.
ROUTING = ["tests.test_user_routing", "tests.test_security_review"]

#: Leaving a Multiview for a normal route, and protecting assigned devices
#: from a clear.
EXIT = ["tests.test_multiview"]
CLEAR = ["tests.test_clear_protection"]
LIFECYCLE = ["tests.test_acceptance_lifecycle"]
MATRIX = ["tests.test_route_authorization_matrix"]
BACKUP = ["tests.test_backup"]

#: Unification pass.
GROUPS = ["tests.test_display_groups"]
RETENT = ["tests.test_activity_retention"]
FW = ["tests.test_device_manager", "tests.test_firmware_status"]
ADAPT = ["tests.test_adapter_ranking", "tests.test_adapter_default_js"]
HTTPDEF = ["tests.test_remote_http_default"]
UMV = ["tests.test_user_multiview_removed", "tests.test_user_projection"]
MVPROMPT = ["tests.test_matrix_route_prompt_js"]
STATETEXT = ["tests.test_state_text_contrast_js"]

#: The USB Matrix prepared on inventory change, the User page's thumbnail refresh, offline units and
#: the version status.
USBPREP = ["tests.test_usb_prep", "tests.test_usb_prep_js"]
PREVIEW = ["tests.test_user_preview_refresh_js"]
OFFLINE = ["tests.test_offline_routing"]
OFFLINEUI = ["tests.test_offline_routing", "tests.test_matrix_lifecycle_js"]
USERPAGE = ["tests.test_user_page_js"]
#: Routing | Matrix: who may use which interface, what the Matrix draws, and drag and drop.
MXUI = ["tests.test_user_matrix", "tests.test_user_page_js"]
MXSRV = ["tests.test_user_matrix", "tests.test_security_review"]
VERSION = ["tests.test_version_status_js"]

#: (id, file, anchor, replacement, what the defect would be, suites)
MUTATIONS = [
    # ---- device passwords are for the host, not for an administrator ----
    ("config.password-to-remote-admin", "OmniMatrix_upgrade_server_v7_6y.py",
     '        if include_password and not omni_remote.is_local_request(request.environ):',
     '        if False:',
     "a remote administrator could read the device passwords back",
     ["tests.test_account_audit"]),
    ("config.save-resets-passwords", "OmniMatrix_upgrade_server_v7_6y.py",
     "    app.config['PASSWORD'] = data.get(\"password\") or app.config.get('PASSWORD', 'password')",
     "    app.config['PASSWORD'] = data.get(\"password\", \"password\")",
     "a save that carries no password replaces the device password with the default",
     ["tests.test_account_audit"]),
    ("config.save-resets-fallback", "OmniMatrix_upgrade_server_v7_6y.py",
     "    app.config['FALLBACK_PASSWORD'] = (data.get(\"fallback_password\")\n"
     "                                       or app.config.get('FALLBACK_PASSWORD', 'Atlona'))",
     "    app.config['FALLBACK_PASSWORD'] = data.get(\"fallback_password\", \"Atlona\")",
     "a save that carries no fallback password replaces it with the default",
     ["tests.test_account_audit"]),
    # ---- this pass: the engine result never carries a device address ----
    #
    # The A/V engine answers with the decoder's own record. Forwarding it put
    # an address into a restricted browser on the SUCCESS path, which is the
    # path nobody reads. These break the reducer in the two ways it can be
    # broken: passing the engine's answer straight through, and rebuilding it
    # by removing known fields rather than from a fixed list.
    ("project.engine-answer-forwarded", "omni_remote_api.py",
     '        return _project_engine_result(body, status)',
     '        return result',
     "the engine's decoder record would reach a restricted client", ROUTING),
    ("project.reducer-leaks-input-names", "omni_remote_api.py",
     '    _ENGINE_RESULT_KEYS = ("ok", "status", "reason", "error", "note", "planes")',
     '    _ENGINE_RESULT_KEYS = ("ok", "status", "reason", "error", "note", "planes",\n'
     '                           "cleared", "failed")',
     "the decoder's own input names would reach a restricted client",
     ROUTING),
    ("project.reducer-is-a-denylist", "omni_remote_api.py",
     '        out = {key: body[key] for key in _ENGINE_RESULT_KEYS if key in body}',
     '        out = {k: v for k, v in body.items() if k != "decoder"}',
     "a new engine field would reach a restricted client by default",
     ROUTING),
    ("project.unroute-answer-forwarded", "omni_remote_api.py",
     '                return _project_engine_result(_payload_of(result), status)',
     '                return result',
     "the unroute engine's decoder record would reach a client", ROUTING),

    # ---- this pass: unroute is authorised like a route ------------------
    ("unroute.no-destination-check", "omni_remote_api.py",
     '            if not account.permissions.may_control_destination(destination_id):\n'
     '                return "destination_not_permitted"\n'
     '            return None\n'
     '\n'
     '        # Before the wait, so a request that was never going to be allowed\n'
     '        # does not queue for a lock.\n'
     '        refusal = _revalidate(_check, "/api/user/unroute")',
     '            return None\n'
     '\n'
     '        # Before the wait, so a request that was never going to be allowed\n'
     '        # does not queue for a lock.\n'
     '        refusal = _revalidate(_check, "/api/user/unroute")',
     "any display could be stopped by any account with A/V", ROUTING),

    # ---- this pass: a preset's unknown source fails closed --------------
    # ---- this pass: a refusal names itself ------------------------------
    ("denial.reason-withheld", "omni_remote_api.py",
     '                        "reason": reason}), 403',
     '                        "reason": "denied"}), 403',
     "a 403 would not say which refusal it is", ROUTING),
    # ---- the unit cache is written by two things -----------------------
    ("cache.shared-temp-name", "omni_matrix_logic.py",
     '        tmp_path = _cache_file.with_suffix(\n'
     '            "%s.%d-%d.tmp" % (_cache_file.suffix, os.getpid(),\n'
     '                              threading.get_ident()))',
     '        tmp_path = _cache_file.with_suffix(_cache_file.suffix + ".tmp")',
     "two writers would collide and publish a mixture of both", CLEAR),
    ("cache.read-modify-write-unlocked", "omni_matrix_logic.py",
     "    with _cache_write_lock:\n"
     "        _save_cache_locked()",
     "    _save_cache_locked()",
     "two concurrent writers would each lose the other's change", CLEAR),
    ("cache.malformed-not-quarantined", "omni_matrix_logic.py",
     "        except (ValueError, UnicodeDecodeError) as e:",
     "        except (KeyboardInterrupt,) as e:",
     "a malformed cache would fail on every read for the life of the "
     "process", CLEAR),
    # ---- configuration backup ------------------------------------------
    #
    # A restore replaces every account on the installation and the backup
    # carries the device credentials, so the boundaries worth breaking on
    # purpose are: who may call it, whether the file is really authenticated,
    # whether a device is matched by identity rather than by address, and
    # whether a failure really leaves the installation alone.
    ("backup.export-is-remote", "omni_access.py",
     '    "/api/backup/export": (Access.LOCAL_ONLY, None),',
     '    "/api/backup/export": (Access.ADMIN, None),',
     "the device credentials would be reachable from the network",
     BACKUP),
    ("backup.restore-is-remote", "omni_access.py",
     '    "/api/backup/restore": (Access.LOCAL_ONLY, None),',
     '    "/api/backup/restore": (Access.ADMIN, None),',
     "a stolen administrator session could replace every account",
     BACKUP),
    ("backup.inspect-is-remote", "omni_access.py",
     '    "/api/backup/inspect": (Access.LOCAL_ONLY, None),',
     '    "/api/backup/inspect": (Access.ADMIN, None),',
     "a remote caller could read a deployment out of a backup file",
     BACKUP),

    # The header is the additional authenticated data. Without it the version,
    # the salt and the nonce can all be edited without detection.
    ("backup.header-not-authenticated", "omni_backup.py",
     "    ciphertext = AESGCM(_derive(passphrase, salt)).encrypt(nonce, plaintext, aad)",
     "    ciphertext = AESGCM(_derive(passphrase, salt)).encrypt(nonce, plaintext, None)",
     "a backup's header could be edited without the file being refused",
     BACKUP),
    ("backup.decrypt-ignores-the-tag", "omni_backup.py",
     "        plaintext = AESGCM(key).decrypt(nonce, ciphertext, aad)",
     "        plaintext = AESGCM(key).decrypt(nonce, ciphertext, None)",
     "a tampered header would be accepted", BACKUP),
    ("backup.weak-passphrase-allowed", "omni_backup.py",
     "    if not isinstance(passphrase, str) or len(passphrase) < MIN_PASSPHRASE:",
     "    if False:",
     "a backup of every credential could be sealed with one character",
     BACKUP),
    ("backup.newer-format-accepted", "omni_backup.py",
     "    if fmt > FORMAT_VERSION:",
     "    if False:",
     "a file this build cannot understand would be read anyway", BACKUP),

    # Identity, not address. The whole reason a device record is carried by
    # MAC is that a different unit must never inherit an authorisation by
    # turning up at an old address.
    ("backup.device-matched-by-address", "omni_backup.py",
     '        mac = _normal_mac(unit.get("mac"))\n'
     "        record = remembered.get(mac)",
     '        mac = _normal_mac(unit.get("mac"))\n'
     '        record = remembered.get(mac) or next(iter(remembered.values()), None)',
     "a device could inherit another unit's credential", BACKUP),
    ("backup.identityless-device-carried", "omni_backup.py",
     "        if not mac:\n"
     "            continue",
     "        if not mac:\n"
     '            mac = str(unit.get("ip") or "")',
     "an address would be carried as if it were a hardware identity",
     BACKUP),
    ("backup.stale-data-overwrites-live", "omni_backup.py",
     '            if unit.get(field) in (None, ""):\n'
     "                unit[field] = value",
     "            unit[field] = value",
     "a backup would overwrite what a scan had just learned", BACKUP),

    # Transactional restore.
    ("backup.no-snapshot", "omni_backup.py",
     "    saved = []\n"
     "    for section in SECTIONS:\n"
     "        source = data_dir / section.filename\n"
     "        if source.exists():\n"
     "            shutil.copy2(source, into / section.filename)\n"
     "            saved.append(section.filename)",
     "    saved = []",
     "a failed restore would have nothing to roll back to", BACKUP),
    ("backup.validation-skipped", "omni_backup.py",
     '    if not isinstance(payload.get("sections"), dict):\n'
     '        return ["the backup has no configuration in it"]',
     "    if True:\n"
     "        return []",
     "a damaged backup would be written to disk", BACKUP),

    # What must never travel.
    ("backup.carries-the-bearer-token", "omni_backup.py",
     '        record["blob"] = ""',
     "        pass",
     "a working passwordless link would be written into the backup file",
     BACKUP),

    # ---- the listener trust model -----------------------------------
    ("listener.unknown-is-remote", "omni_remote.py",
     'return REMOTE if value == REMOTE else LOCAL',
     'return LOCAL if value == LOCAL else REMOTE',
     "an in-process request would be treated as remote", LISTENER),
    ("listener.everything-is-local", "omni_remote.py",
     'return REMOTE if value == REMOTE else LOCAL',
     'return LOCAL',
     "the remote listener would hand out local trust", LISTENER),
    ("listener.bind-tag-inverted", "omni_remote.py",
     'return LOCAL if is_loopback_address(host) else REMOTE',
     'return REMOTE if is_loopback_address(host) else LOCAL',
     "a listener on a routable address would be trusted as local", LISTENER),
    ("listener.trust-remote-addr", "omni_remote.py",
     'return listener_of(environ) == LOCAL',
     'return (is_loopback_address(environ.get("REMOTE_ADDR") or "")\n'
     '            or listener_of(environ) == LOCAL)',
     "a client claiming 127.0.0.1 would be trusted", LISTENER),

    # ---- route classification ---------------------------------------
    ("classify.unknown-is-open", "omni_access.py",
     'return ROUTES.get(rule, (Access.LOCAL_ONLY, None))',
     'return ROUTES.get(rule, (Access.AUTHENTICATED, None))',
     "an unclassified route would be reachable remotely", GATE),
    ("classify.unknown-is-public", "omni_access.py",
     'return ROUTES.get(rule, (Access.LOCAL_ONLY, None))',
     'return ROUTES.get(rule, (Access.PUBLIC, None))',
     "an unclassified route would be anonymous", GATE),

    # ---- the gate ----------------------------------------------------
    ("gate.local-only-reachable", "omni_gate.py",
     '    if access is Access.LOCAL_ONLY:\n'
     '        return Decision(False, 404, "local_only")',
     '    if access is Access.LOCAL_ONLY and False:\n'
     '        return Decision(False, 404, "local_only")',
     "diagnostics and bootstrap would answer the network", GATE),
    ("gate.no-session-needed", "omni_gate.py",
     '    if account is None:\n        return Decision(False, 401, "no_session")',
     '    if account is None:\n        return Decision(True, 200, "no_session")',
     "an anonymous remote request would be allowed", GATE),
    ("gate.csrf-off", "omni_gate.py",
     '        if not presented or presented != session.csrf_token:',
     '        if False:',
     "a cross-site POST would be accepted", GATE),
    ("gate.csrf-presence-only", "omni_gate.py",
     '        if not presented or presented != session.csrf_token:',
     '        if not presented:',
     "any CSRF header value would do", GATE),
    ("gate.user-is-admin", "omni_gate.py",
     '    if access is Access.ADMIN:\n        if not account.is_admin:',
     '    if access is Access.ADMIN:\n        if False:',
     "a User would reach the administrative API", GATE),
    ("gate.feature-not-required", "omni_gate.py",
     '        if not feature or not account.permissions.has_feature(feature):',
     '        if False:',
     "a User would reach a feature they were not granted", GATE),
    ("gate.source-not-checked", "omni_gate.py",
     '        if not account.permissions.may_use_source(identity):',
     '        if False:',
     "a User would route a source they may not use", GATE),
    ("gate.destination-not-checked", "omni_gate.py",
     '        if not account.permissions.may_control_destination(identity):',
     '        if False:',
     "a User would control a display they may not", GATE),
    ("gate.fallthrough-allows", "omni_gate.py",
     '    return Decision(False, 403, "unclassified", account)',
     '    return Decision(True, 200, "unclassified", account)',
     "an unknown access class would allow instead of deny", GATE),

    # ---- permissions -------------------------------------------------
    ("perm.source-any", "omni_users.py",
     '        return _normalise_identity(identity) in self.sources',
     '        return True',
     "every source would be permitted", USERS),
    ("perm.destination-any", "omni_users.py",
     '        return _normalise_identity(identity) in self.destinations',
     '        return True',
     "every destination would be permitted", USERS),

    # ---- sessions ----------------------------------------------------
    ("direct.throttle-refuses-a-valid-link", "omni_remote_api.py",
     '        account = store.resolve_direct_token(token)\n        if account is None:\n            # A refused link is throttled, for two reasons.',
     '        account = (None if store.locked_out(_DIRECT_THROTTLE_KEY, source)\n                   else store.resolve_direct_token(token))\n        if account is None:\n            # A refused link is throttled, for two reasons.',
     "a throttled address could not open a link that is valid", DIRECT),
    ("session.password-change-ends-link-sessions", "omni_users.py",
     '                                     except_method=BY_DIRECT_LINK)',
     '                                     except_method=None)',
     "a password change would sign out a Direct Access tablet", LIFECYCLE),
    ("session.password-change-keeps-password-sessions", "omni_users.py",
     '                      if s.user_id == user_id and s.method != except_method]',
     '                      if s.user_id == user_id and s.method == except_method]',
     "a password change would leave the password sessions alive", LIFECYCLE),
    ("account.username-unbounded", "omni_users.py",
     '        if len(username) > MAX_USERNAME or any(ord(c) < 32 or ord(c) == 127 for c in username):',
     '        if False:',
     "any username, however long or malformed, would be accepted", LIFECYCLE),
    ("session.disabled-still-works", "omni_users.py",
     '            if account is None or not account.enabled:',
     '            if account is None:',
     "a disabled account would keep working until its session expired", USERS),
    ("session.never-expires", "omni_users.py",
     '        return (now - self.last_seen > IDLE_TIMEOUT_SECONDS\n'
     '                or now - self.created_at > ABSOLUTE_TIMEOUT_SECONDS)',
     '        return False',
     "a session would live forever", USERS),
    ("session.no-absolute-limit", "omni_users.py",
     '        return (now - self.last_seen > IDLE_TIMEOUT_SECONDS\n'
     '                or now - self.created_at > ABSOLUTE_TIMEOUT_SECONDS)',
     '        return now - self.last_seen > IDLE_TIMEOUT_SECONDS',
     "an active page would never have to sign in again", USERS),

    # ---- transport ---------------------------------------------------
    # Two session cookies are set: one by the sign-in endpoint and one by a
    # Direct Access link. Both must be Secure under TLS, so this takes the
    # first and a second entry takes the other -- an anchor matching both at
    # once would be refused by the harness, and rightly.
    ("tls.cookie-never-secure", "omni_remote_api.py",
     '            httponly=True, samesite="Lax",\n'
     '            secure=request.is_secure, path="/")\n'
     '        return response\n\n    @app.route("/api/auth/logout"',
     '            httponly=True, samesite="Lax",\n'
     '            secure=False, path="/")\n'
     '        return response\n\n    @app.route("/api/auth/logout"',
     "the session cookie would be sent over plaintext", REMOTE),
    # Anchored on the cookie itself plus the comment that follows only the
    # Direct Access one. The previous anchor ran on to the next @app.route,
    # so inserting a header between them made it match nothing -- and an
    # anchor that can never match reads as a gap rather than as a stale
    # harness.
    ("tls.direct-cookie-never-secure", "omni_remote_api.py",
     '            secure=request.is_secure, path="/")\n'
     '        # Belt and braces over the global policy',
     '            secure=False, path="/")\n'
     '        # Belt and braces over the global policy',
     "a Direct Access session cookie would be sent over plaintext",
     REMOTE + ["tests.test_remote_browser_flow"]),
    ("tls.plaintext-without-asking", "omni_remote_api.py",
     'if transport == "http" and data.get("accept_insecure") is not True:',
     'if False:',
     "plaintext would start without the operator saying so", REMOTE),
    # Both clauses, because `is_unspecified` also refuses 0.0.0.0: together
    # they are what keeps the wildcard out, so removing one is a redundant
    # control rather than a defect worth catching.
    ("addr.wildcard-accepted", "omni_remote.py",
     '    if is_loopback_address(text) or is_wildcard_address(text):\n'
     '        return False\n'
     '    if parsed.is_multicast or parsed.is_reserved or parsed.is_unspecified:',
     '    if is_loopback_address(text):\n'
     '        return False\n'
     '    if parsed.is_multicast or parsed.is_reserved:',
     "remote access could bind every interface", REMOTE),
    ("addr.loopback-accepted", "omni_remote.py",
     '    if is_loopback_address(text) or is_wildcard_address(text):',
     '    if is_wildcard_address(text):',
     "remote access could bind loopback and reach nobody", REMOTE),

    # ---- authorisation on the far side of a lock wait ----------------
    ("relock.route-no-recheck", "omni_remote_api.py",
     '                # Again, now that the wait is over and the next statement\n'
     '                # writes to hardware.\n'
     '                refusal = _revalidate(_check, "/api/user/route")\n'
     '                if refusal is not None:\n'
     '                    return refusal\n',
     '',
     "a User revoked while queued would still route", RELOCK),
    # ---- presentation metadata is not identity, and does not widen ----
    ("meta.address-as-distinguisher", "omni_projection.py",
     '    return (device_location(unit) or device_description(unit)\n'
     '            or _device_name(row, unit))',
     '    return (device_location(unit) or device_description(unit)\n'
     '            or str((unit or {}).get("ip") or "")\n'
     '            or _device_name(row, unit))',
     "an address would reach a user-visible name", META),
    ("meta.projection-widens", "omni_projection.py",
     '        "id": identity,\n'
     '        "name": _friendly_name(row, unit, identity, aliases),\n'
     '        "online": _is_online(row, unit),\n'
     '    }\n'
     '\n'
     '\n'
     'def project_destination',
     '        "id": identity,\n'
     '        "name": _friendly_name(row, unit, identity, aliases),\n'
     '        "online": _is_online(row, unit),\n'
     '        "location": device_location(unit),\n'
     '    }\n'
     '\n'
     '\n'
     'def project_destination',
     "systeminfo would widen the restricted projection", META),

    # ---- one action, one entry ---------------------------------------
    ("audit.duplicate-returns", "omni_remote_api.py",
     '        if not result.get("ok"):\n'
     '            return jsonify(result), 400\n'
     '        return jsonify(result)\n\n    # ------------------------------------------------- direct access links',
     '        if not result.get("ok"):\n'
     '            return jsonify(result), 400\n'
     '        store.audit("user." + action, actor=actor,\n'
     '                    detail={"user_id": user_id})\n'
     '        return jsonify(result)\n\n    # ------------------------------------------------- direct access links',
     "one operator action would be recorded twice", AUDIT),

    # ---- installation settings are administrative ---------------------
    ("prefs.global-write-open", "omni_access.py",
     '    ("POST", "/api/ui_preferences"): (Access.ADMIN, None),',
     '    ("POST", "/api/ui_preferences"): (Access.AUTHENTICATED, None),',
     "a restricted User could restyle the operator's interface", PREFS),
    ("prefs.notice-ack-open", "omni_access.py",
     '    "/api/notices/ack": (Access.ADMIN, None),',
     '    "/api/notices/ack": (Access.AUTHENTICATED, None),',
     "a restricted User could dismiss the operator's update alert", PREFS),

    # ---- a person gets a page, a program gets JSON --------------------
    ("browser.api-gets-html", "omni_gate.py",
     '    if str(rule or "").startswith("/api/"):\n        return False',
     '    if False:\n        return False',
     "an API caller would be handed a redirect to a sign-in page", BROWSER),
    ("browser.no-login-redirect", "omni_gate.py",
     '        if wants_a_page(rule, request.method, request.environ):\n'
     '            if decision.status == 401:',
     '        if False and wants_a_page(rule, request.method, request.environ):\n'
     '            if decision.status == 401:',
     "a browser opening the remote URL would see raw JSON again", BROWSER),
    ("browser.disabled-shows-json", "omni_gate.py",
     '            if wants_a_page(rule, request.method, request.environ):\n'
     '                return refused_page(404)',
     '            if False:\n'
     '                return refused_page(404)',
     "a saved link opened after remote access was turned off would show raw "
     "JSON", BROWSER),
    ("browser.writes-redirected", "omni_gate.py",
     '    if str(method or "").upper() not in ("GET", "HEAD"):\n        return False',
     '    if False:\n        return False',
     "an unauthenticated POST would answer 302 instead of 401", BROWSER),

    # ---- the certificate matches the address it is served on ----------
    ("cert.matches-anything", "omni_tls.py",
     '    names = [str(n).strip() for n in (described or {}).get("san") or []]',
     '    return True\n    names = [str(n).strip() for n in (described or {}).get("san") or []]',
     "HTTPS would start with a certificate for a different address", CERT),
    ("cert.unknown-is-fine", "omni_tls.py",
     '    if not names:\n        return False',
     '    if not names:\n        return True',
     "a certificate whose names could not be read would be called a match",
     CERT),

    ("single.refused-copy-exits-zero", "app_launcher.py",
     "            pass\n        return 1\n    root = tk.Tk()",
     "            pass\n        return 0\n    root = tk.Tk()",
     "a refused second copy would report success after its message", SINGLE),
    ("routediag.always-on", "omni_routediag.py",
     "    return str(value).strip().lower() in _TRUE",
     "    return True",
     "every build would time every route and write a file without being asked",
     ROUTEDIAG),
    ("routediag.install-ignores-switch", "omni_routediag.py",
     "    if not enabled(environ):",
     "    if False:",
     "the route view would be wrapped even with the switch off",
     ROUTEDIAG),
    ("routediag.unbounded-file", "omni_routediag.py",
     "                    if path.exists() and path.stat().st_size > MAX_BYTES:",
     "                    if False:",
     "leaving the switch on could fill a disk",
     ROUTEDIAG),
    ("routediag.write-failure-breaks-route", "omni_routediag.py",
     "        except Exception as exc:                # a diagnostic never breaks a route",
     "        except ZeroDivisionError as exc:",
     "an unwritable diagnostic file would turn a route into a server error",
     ROUTEDIAG),
    ("routediag.never-warns", "omni_routediag.py",
     "            if not state[\"warned\"]:",
     "            if False:",
     "a diagnostic that cannot write would fail without a word",
     ROUTEDIAG),
    ("routediag.counts-objects-every-call", "omni_routediag.py",
     "                if state[\"n\"] % OBJECTS_EVERY == 0:",
     "                if True:",
     "counting every tracked object on every route would distort what it measures",
     ROUTEDIAG),
    ("attempts.never-pruned", "OmniMatrix_upgrade_server_v7_6y.py",
     "            _forget_ended_attempt_threads()",
     "            pass",
     "the per-thread read-attempt table would gain an entry for every request thread, forever",
     ATTEMPTS),
    ("attempts.no-cap", "OmniMatrix_upgrade_server_v7_6y.py",
     "_MULTIVIEW_ATTEMPT_THREAD_CAP = 64",
     "_MULTIVIEW_ATTEMPT_THREAD_CAP = 10 ** 9",
     "a burst of live threads could grow the table without limit",
     ATTEMPTS),
    ("attempts.forgets-the-living", "OmniMatrix_upgrade_server_v7_6y.py",
     "    for ident in [i for i in _MULTIVIEW_ATTEMPTS if i not in live]:",
     "    for ident in [i for i in _MULTIVIEW_ATTEMPTS if i in live]:",
     "a running operation would lose its own diagnostic entries",
     ATTEMPTS),
    # ---- the identity and validity of a generated certificate ----------
    ("certid.leap-day-rolls-forward", "omni_tls.py",
     "        return moment.replace(year=target_year, day=28)",
     "        return moment.replace(year=target_year, month=3, day=1)",
     "a certificate made on 29 February would outlive its three years by a day",
     CERTID),
    ("certid.validity-is-a-day-count", "omni_tls.py",
     "add_calendar_years(generated, GENERATED_VALIDITY_YEARS))",
     "generated + datetime.timedelta(days=3 * 365))",
     "expiry would drift off the anniversary whenever a leap day falls inside the period",
     CERTID),
    ("certid.common-name-is-the-address", "omni_tls.py",
     "x509.NameAttribute(NameOID.COMMON_NAME, name),",
     "x509.NameAttribute(NameOID.COMMON_NAME, text),",
     "the certificate would carry the address as its name and not the computer's",
     CERTID),
    ("certid.no-machine-name-san", "omni_tls.py",
     "    alternatives = [x509.DNSName(name)]",
     "    alternatives = []",
     "the computer could not be reached by name over HTTPS",
     CERTID),
    ("certid.no-unit", "omni_tls.py",
     "        x509.NameAttribute(NameOID.ORGANIZATIONAL_UNIT_NAME,\n"
     "                           CERT_ORGANIZATIONAL_UNIT),\n",
     "",
     "an administrator could not tell which product made the certificate",
     CERTID),
    ("certid.name-unbounded", "omni_tls.py",
     '    name = name[:MAX_COMMON_NAME].strip(".-_")\n',
     "",
     "a long machine name would produce a common name no parser has to accept",
     CERTID),
    ("certid.non-ascii-kept", "omni_tls.py",
     'text = re.sub(r"[^A-Za-z0-9._-]", "-", str(raw or "").strip())',
     'text = str(raw or "").strip()',
     "a machine name with other characters would reach the DNS name unchanged",
     CERTID),
    ("certid.address-as-dns", "omni_tls.py",
     "        alternatives.append(x509.IPAddress(ipaddress.ip_address(text)))",
     "        alternatives.append(x509.DNSName(text))",
     "the adapter's address would be a DNS name, which no browser matches to an IP",
     CERTID),
    ("certid.expiry-ignores-clock", "omni_tls.py",
     "            current = now or datetime.datetime.now(datetime.timezone.utc)",
     "            current = datetime.datetime.now(datetime.timezone.utc)",
     "the remaining lifetime could not be measured against a stated moment",
     CERTID),
    ("certid.replace-not-asked", "ui/settings.js",
     "    if (!existing || !(existing.present || existing.path_configured)) return true;",
     "    return true;",
     "an existing certificate would be replaced, and its fingerprint changed, without asking",
     CERTUI),
    ("certid.replace-cancel-ignored", "ui/settings.js",
     "      if (!go) return;\n      certGenerate.disabled = true;",
     "      certGenerate.disabled = true;",
     "Cancel on the replacement dialog would replace the certificate anyway",
     CERTUI),
    ("certid.replace-default-is-yes", "ui/settings.js",
     "      confirmText: 'Replace',\n      danger: true,\n    }));\n  }\n\n  function certificateKind",
     "      confirmText: 'Replace',\n    }));\n  }\n\n  function certificateKind",
     "Enter on the replacement dialog would replace the certificate",
     CERTUI),
    ("certid.sentence-names-the-machine", "ui/settings.js",
     "    return addresses.join(', ') || (cert && cert.subject) || '';",
     "    return (cert && cert.subject) || '';",
     "Settings would say a certificate is for the computer's name instead of its address",
     CERTUI),

    ("cert.saved-choice-ignored", "omni_remote_api.py",
     '        cert_path = str(data.get("cert_path") or "").strip() or chosen.get("cert_path") or ""',
     '        cert_path = str(data.get("cert_path") or "").strip() or ""',
     "turning Remote Access on serves the generated certificate and blanks the supplied one",
     CERTCHOICE),
    ("cert.key-choice-ignored", "omni_remote_api.py",
     '        key_path = str(data.get("key_path") or "").strip() or chosen.get("key_path") or ""',
     '        key_path = str(data.get("key_path") or "").strip() or ""',
     "a supplied certificate is paired with the generated key",
     CERTCHOICE),
    ("cert.half-pair-completed", "OmniMatrix_upgrade_server_v7_6y.py",
     '    if bool(cert) != bool(key):\n        # Half a supplied pair',
     '    if False:\n        # Half a supplied pair',
     "half a supplied pair is completed from OmniSuite's own files",
     CERTCHOICE),

    # ---- device credentials never leave on a network-facing listener ----
    ("credfilter.skipped-remotely", "OmniMatrix_upgrade_server_v7_6y.py",
     '        if (omni_remote.is_local_request(request.environ)',
     '        if (True',
     "a remote administrator is sent every device's stored password",
     CREDFILTER),
    ("credfilter.misses-primary-key", "OmniMatrix_upgrade_server_v7_6y.py",
     '_CREDENTIAL_KEYS = frozenset({"password", "fallback_password"})',
     '_CREDENTIAL_KEYS = frozenset({"fallback_password"})',
     "the per-device password is no longer recognised as a credential",
     CREDFILTER),
    ("config.remote-firmware-folder", "OmniMatrix_upgrade_server_v7_6y.py",
     '            and not omni_remote.is_local_request(request.environ)):',
     '            and False):',
     "a signed-in remote session names any host folder as the firmware folder",
     CONFIGSAVE),
    ("config.timeout-unchecked", "OmniMatrix_upgrade_server_v7_6y.py",
     '    if not (timeout == timeout and 0 < timeout <= 120):',
     '    if False:',
     "a NaN, zero or negative timeout is saved and stops every device read",
     CONFIGSAVE),

    # ---- where a remote caller may make the server connect --------------
    ("dest.gate-not-wired", "OmniMatrix_upgrade_server_v7_6y.py",
     '                  _remote_destination_check)',
     '                  None)',
     "the destination rule is never consulted for a remote request",
     DEST),
    ("dest.control-accepts-unknown", "omni_destinations.py",
     '    if text not in known:',
     '    if False:',
     "a stored credential is sent to an address the server never found",
     DEST),
    ("dest.discovery-allows-public", "omni_destinations.py",
     '                or address.is_reserved or address.is_global',
     '                or address.is_reserved',
     "a remote scan may probe the public Internet with the scan credential",
     DEST),
    ("dest.accepts-ipv6", "omni_destinations.py",
     '        return ipaddress.IPv4Address(text)',
     '        return ipaddress.ip_address(text)',
     "an IPv6 literal is treated as a valid destination",
     DEST),
    ("dest.list-checks-first-only", "omni_destinations.py",
     '        for item in value:',
     '        for item in value[:1]:',
     "only the first address of a list is judged; the rest are contacted",
     DEST),
    ("dest.mac-pattern-matches-anything", "omni_destinations.py",
     '_MAC = re.compile(r"^[0-9A-Fa-f]{2}([:\-]?[0-9A-Fa-f]{2}){5}$")',
     '_MAC = re.compile(r"^.*$")',
     "every value is treated as an identity and skips the address check",
     DEST),
    ("dest.ip-key-forgotten", "omni_destinations.py",
     '    "ip", "ips", "decoder", "decoders", "encoder", "encoders", "targets",',
     '    "ips", "decoder", "decoders", "encoder", "encoders", "targets",',
     "the most common body key is no longer examined",
     DEST),
    ("dest.redirect-followed", "OmniMatrix_upgrade_server_v7_6y.py",
     '        ws = websocket.create_connection(url, timeout=timeout, sslopt=sslopt, redirect_limit=0)',
     '        ws = websocket.create_connection(url, timeout=timeout, sslopt=sslopt)',
     "a device's redirect carries the credential to wherever it points",
     DEST),

    # ---- a restore is the old configuration or the new one ---------------
    ("restore.false-write-ignored", "omni_backup.py",
     '        if write_json(target, content) is False:',
     '        if False:',
     "a write that returns False is counted as written and the restore carries on",
     RESTORE),
    ("restore.created-files-kept", "omni_backup.py",
     '    names = ([section.filename for section in SECTIONS] if present is not None',
     '    names = (list(touched) if present is not None',
     "a file the restore created is left behind by its own rollback",
     RESTORE),
    ("restore.stale-snapshot-kept", "omni_backup.py",
     '    for stale in [section.filename for section in SECTIONS] + [MANIFEST]:',
     '    for stale in []:',
     "an earlier restore's snapshot files are rolled back into place",
     RESTORE),
    ("restore.rollback-in-place", "omni_backup.py",
     '                _atomic_copy(saved, target)',
     '                shutil.copy2(saved, target)',
     "an interrupted rollback truncates the file it was recovering",
     RESTORE),
    ("restore.stores-not-held", "OmniMatrix_upgrade_server_v7_6y.py",
     '        with _user_store._lock, _device_aliases._lock, _display_groups._lock:',
     '        if True:',
     "an account change in the middle writes the old accounts over the restore",
     RESTORE),
    ("restore.group-store-not-held", "OmniMatrix_upgrade_server_v7_6y.py",
     '        with _user_store._lock, _device_aliases._lock, _display_groups._lock:',
     '        with _user_store._lock, _device_aliases._lock:',
     "a group change in the middle writes the old groups over the restore",
     RESTORE + GROUPS),
    ("restore.other-writes-allowed", "OmniMatrix_upgrade_server_v7_6y.py",
     '    if not (_restore_in_progress or _restart_required):',
     '    if True:',
     "configuration is written by anything while a restore runs",
     RESTORE),
    ("restore.restart-not-latched", "OmniMatrix_upgrade_server_v7_6y.py",
     '        _restart_required = True\n        # Nobody can say which table',
     '        _restart_required = False\n        # Nobody can say which table',
     "after a reload failure the next save overwrites the restored files",
     RESTORE),
    ("restore.incomplete-rollback-claimed", "OmniMatrix_upgrade_server_v7_6y.py",
     '        status = ("FAILED — ROLLED BACK" if not failures',
     '        status = ("FAILED — ROLLED BACK" if True',
     "a rollback that failed is reported as having succeeded",
     RESTORE),
    ("restore.concurrent-restore", "OmniMatrix_upgrade_server_v7_6y.py",
     '    if not _restore_lock.acquire(blocking=False):',
     '    if False:',
     "two restores run at once",
     RESTORE),

    # ---- link sessions never expire, so they are bounded another way --------
    ("directsession.no-cap", "omni_users.py",
     '            if excess > 0:',
     '            if False:',
     "anyone holding a Direct Access URL can grow the session table without end",
     DSB),
    ("directsession.reload-mints-new", "omni_remote_api.py",
     '                and held_session.from_direct_link):',
     '                and False):',
     "every reload of a wall tablet adds a session and an audit entry",
     DSB),
    ("directsession.adopts-other-account", "omni_remote_api.py",
     '        if (held is not None and held.id == account.id',
     '        if (held is not None',
     "a browser signed in as one account is handed another account's link session",
     DSB),

    # ---- text a device reports is never markup ---------------------------------
    ("esc.option-list-raw", "ui/matrix/matrix.js",
     "<option value=\"${escAttr(opt)}\" ${opt === currentValue ? 'selected' : ''}>${escAttr(opt)}</option>",
     "<option value=\"${opt}\" ${opt === currentValue ? 'selected' : ''}>${opt}</option>",
     "an EDID or input name a device reports runs script in the administrator's session",
     ESC),
    ("esc.decoder-input-label-raw", "ui/matrix/matrix.js",
     "return `<option value=\"${escAttr(value)}\" ${value === current ? 'selected' : ''}>${escAttr(label)}</option>`;",
     "return `<option value=\"${value}\" ${value === current ? 'selected' : ''}>${label}</option>`;",
     "a decoder-reported input address is written into the page as markup",
     ESC),
    ("esc.linkspeed-raw", "ui/index.html",
     "const shown = Number.isFinite(speed) ? String(speed) : escapeHtml(String(raw).trim());",
     "const shown = Number.isFinite(speed) ? String(speed) : String(raw).trim();",
     "a non-numeric link speed is written into the page as markup",
     ESC),
    ("esc.status-raw", "ui/index.html",
     "statusCell.innerHTML = `<span class=\"status ${escapeAttr(cls)}\">${escapeHtml(text)}</span>`;",
     "statusCell.innerHTML = `<span class=\"status ${cls}\">${text}</span>`;",
     "a device's firmware-upload error body is written into the page as markup",
     ESC),
    ("esc.usb-role-raw", "ui/index.html",
     "const usbHtml = label ? `<span class=\"usb-pill\">${escapeHtml(label)}</span>` : '';",
     "const usbHtml = label ? `<span class=\"usb-pill\">${label}</span>` : '';",
     "a device-reported USB type is written into the page as markup",
     ESC),

    # ---- one copy per data folder ----------------------------------------
    ("instance.lock-ignored", "omni_instance.py",
     '            raise AlreadyRunning(directory, _holder_of(path)) from None',
     '            pass',
     "a second copy is admitted while the first holds the folder",
     SINGLE),
    ("instance.server-main-unlocked", "OmniMatrix_upgrade_server_v7_6y.py",
     '        omni_instance.acquire(DATA_DIR)',
     '        pass',
     "the source entry point starts a second copy on another port",
     SINGLE),
    ("instance.launcher-unlocked", "app_launcher.py",
     '        omni_instance.acquire(os.environ.get("OMNI_DATA_DIR") or DATA_DIR)',
     '        pass',
     "the packaged application starts a second copy",
     SINGLE),

    # ---- the local listener answers only to its own address ----------------
    ("host.guard-off", "omni_remote.py",
     '        if self._listener == LOCAL and not is_loopback_host_header(',
     '        if False and not is_loopback_host_header(',
     "a rebound DNS name reaches the local listener with local trust",
     HOSTH),
    ("host.any-name-is-localhost", "omni_remote.py",
     '    if host == "localhost":',
     '    if True:',
     "any Host header is accepted as this computer",
     HOSTH),
    ("host.missing-host-accepted", "omni_remote.py",
     '    if not host:\n        return False',
     '    if not host:\n        return True',
     "a request with no Host header is trusted",
     HOSTH),

    # ---- a backup header chooses how much work the restore does ----------
    ("backup.kdf-cost-unbounded", "omni_backup.py",
     '            or n * r * p > SCRYPT_N * SCRYPT_R * SCRYPT_P):',
     '            or False):',
     "a hostile header holds a thread for minutes before the passphrase is checked",
     BACKKDF),
    ("backup.nonce-size-unchecked", "omni_backup.py",
     '    if (dklen != KEY_BYTES or len(nonce) != NONCE_BYTES',
     '    if (dklen != KEY_BYTES',
     "a wrong-size nonce escapes as a bare ValueError from the cipher",
     BACKKDF),

    ("backup.salt-size-unchecked", "omni_backup.py",
     '    if len(salt) != SALT_BYTES or not nonce:',
     '    if not salt or not nonce:',
     "a megabyte of salt is hashed before the passphrase is checked",
     BACKKDF),
    ("backup.header-size-unbounded", "omni_backup.py",
     '    if len(parts[1]) > MAX_HEADER_BYTES:',
     '    if False:',
     "an arbitrarily large header is parsed before anything is verified",
     BACKKDF),

    # ---- an unknown username costs what a known one does ------------------
    ("login.unknown-user-double-derivation", "omni_users.py",
     '        stored = account.password_hash if account else dummy',
     '        stored = account.password_hash if account else hash_password(secrets.token_urlsafe(16))',
     "an unknown username costs two key derivations to a known one's one",
     USERS),
    ("login.unknown-user-skips-check", "omni_users.py",
     '        good = verify_password(stored, password)',
     '        good = verify_password(stored, password) if account else False',
     "an unknown username skips the password verification",
     USERS),

    # ---- what a stranger can make the sign-in remember is bounded ----------
    ("throttle.empty-record-kept", "omni_users.py",
     '            if recent:',
     '            if True:',
     "every name asked about leaves a record in the throttle",
     THROTTLE),
    ("throttle.bounds-removed", "omni_users.py",
     'MAX_RECORDED_USERNAME = 128',
     'MAX_RECORDED_USERNAME = 10 ** 9',
     "a typed name is kept whole in the throttle and the audit",
     THROTTLE),
    ("throttle.no-hard-cap", "omni_users.py",
     '            if key not in self._failures and len(self._failures) >= THROTTLE_MAX_KEYS:',
     '            if False:',
     "a flood of distinct names inside the window grows the throttle without limit",
     THROTTLE),
    ("throttle.evicts-lockouts", "omni_users.py",
     '                             if len(times) < MAX_FAILURES]',
     '                             if True]',
     "a flood of junk names releases a real lockout to make room",
     THROTTLE),
    ("throttle.never-swept", "omni_users.py",
     '            if len(self._failures) >= THROTTLE_SWEEP_AT:',
     '            if False:',
     "expired throttle records are never dropped",
     THROTTLE),

    # ---- the Direct Access link ---------------------------------------
    ("direct.link-can-be-admin", "omni_gate.py",
     '        if access is Access.ADMIN:\n            return Decision(False, 403, "direct_link_not_admin", account)',
     '        if False:\n            return Decision(False, 403, "direct_link_not_admin", account)',
     "a passwordless link on a promoted account would reach the "
     "administrative surface", DIRECT),
    ("direct.admin-may-be-issued-one", "omni_users.py",
     '            if account.is_admin:\n'
     '                # Stated as a refusal rather than a filter in the UI, because a',
     '            if False:\n'
     '                # Stated as a refusal rather than a filter in the UI, because a',
     "a passwordless administrator URL could be created", DIRECT),
    ("direct.disabled-account-still-opens", "omni_users.py",
     '                if not account.enabled or account.is_admin:\n                    return None',
     '                if False:\n                    return None',
     "a disabled account's link would still open a session", DIRECT),
    ("direct.regenerate-keeps-old-sessions", "omni_users.py",
     '            self.revoke_direct_sessions(user_id, _locked=True)\n            self.audit(event, actor=actor,',
     '            self.audit(event, actor=actor,',
     "replacing a link would leave the sessions the old one opened running",
     DIRECT),
    ("direct.token-compared-loosely", "omni_users.py",
     '                if not hmac.compare_digest(record.digest, digest):\n                    continue',
     '                if False:\n                    continue',
     "any token at all would resolve to the first account with a link",
     DIRECT),
    # Both of these now name the ROUTE path explicitly. Unroute takes the same
    # lock and reports busy the same way -- deliberately, it is the same
    # contract -- which made the old anchors match twice and report as stale.
    ("route.busy-mislabelled", "omni_remote_api.py",
     '            log.warning("[USER-ROUTE] %s is busy; waited %.1fs for %s",\n'
     '                        destination_id, time.monotonic() - began, busy.resource)\n'
     '            return jsonify({\n'
     '                "ok": False,\n'
     '                "reason": "resource_busy",',
     '            log.warning("[USER-ROUTE] %s is busy; waited %.1fs for %s",\n'
     '                        destination_id, time.monotonic() - began, busy.resource)\n'
     '            return jsonify({\n'
     '                "ok": False,\n'
     '                "reason": "multiview_active",',
     "a busy display would be reported as a composition", ROUTING),
    ("unroute.busy-mislabelled", "omni_remote_api.py",
     '            log.warning("[USER-UNROUTE] %s is busy; waited %.1fs for %s",\n'
     '                        destination_id, time.monotonic() - began, busy.resource)\n'
     '            return jsonify({\n'
     '                "ok": False,\n'
     '                "reason": "resource_busy",',
     '            log.warning("[USER-UNROUTE] %s is busy; waited %.1fs for %s",\n'
     '                        destination_id, time.monotonic() - began, busy.resource)\n'
     '            return jsonify({\n'
     '                "ok": False,\n'
     '                "reason": "multiview_active",',
     "a busy display would be reported as a composition when unrouting",
     ROUTING),
    ("route.display-not-serialised", "omni_remote_api.py",
     "            with locks.hold(locks.decoder(destination_id)):\n"
     "                waited = time.monotonic() - began",
     "            with locks.hold():\n"
     "                waited = time.monotonic() - began",
     "two operators could interleave inside one route transaction", ROUTING),
    ("unroute.display-not-serialised", "omni_remote_api.py",
     "            with locks.hold(locks.decoder(destination_id)):\n"
     "                # Again, now that the wait is over and the next statement\n"
     "                # writes to hardware.\n"
     '                refusal = _revalidate(_check, "/api/user/unroute")',
     "            with locks.hold():\n"
     "                # Again, now that the wait is over and the next statement\n"
     "                # writes to hardware.\n"
     '                refusal = _revalidate(_check, "/api/user/unroute")',
     "two operators could interleave inside one unroute transaction",
     ROUTING),

    # ---- source previews ----------------------------------------------
    #
    # This one survived when it was first written, because the gate refuses an
    # unpermitted `source` before the handler's own check runs. The handler
    # check is a second line of defence, and tests/test_user_routing.py
    # reaches it deliberately by taking the gate's device check out of the way.
    ("preview.not-authorised", "omni_remote_api.py",
     '            if not account.permissions.may_use_source(source_id):\n'
     '                return "source_not_permitted"\n'
     '            return None\n\n'
     '        refusal = _revalidate(_check, "/api/user/source_preview")',
     '            return None\n\n'
     '        refusal = _revalidate(_check, "/api/user/source_preview")',
     "a restricted account could preview any source", ROUTING),
    # Aimed at the path that HAS an address. The first version mutated the
    # branch that fires only when none was found, where the leak was an empty
    # string -- a mutation that proved nothing.
    ("preview.leaks-the-address", "omni_remote_api.py",
     '            return jsonify({"ok": False, "reason": reason}), 200',
     '            return jsonify({"ok": False, "reason": reason, "address": address}), 200',
     "a device address would reach a restricted browser", ROUTING),
    ("preview.disabled-shown-as-a-picture", "OmniMatrix_upgrade_server_v7_6y.py",
     '    if enabled is False:\n        return None, "", "preview_disabled"',
     '    if False:\n        return None, "", "preview_disabled"',
     "the device's placeholder would be passed off as a source picture",
     ROUTING),
    ("preview.cache-removed", "OmniMatrix_upgrade_server_v7_6y.py",
     '    body, kind = _preview_images.get("thumb:" + address, fetch)',
     '    body, kind = fetch()',
     "every browser would fetch from every encoder for itself", ROUTING),

    # ---- a normal route means single view -----------------------------
    ("route.refuses-over-multiview", "OmniMatrix_upgrade_server_v7_6y.py",
     '        log.info("[ROUTE] %s is showing Multiview %s; leaving it for a normal "\n'
     '                 "route", decoder, active_multiview)',
     '        return jsonify({"ok": False, "status": "MULTIVIEW ACTIVE",\n'
     '                        "multiview": active_multiview}), 409',
     "a normal route would be refused over a Multiview again", EXIT),
    # Anchored through each caller's own log line. Unroute leaves a Multiview
    # through the same verified teardown -- that is the point of it -- so the
    # call alone no longer identifies which path is being broken.
    ("route.deletes-the-composition", "OmniMatrix_upgrade_server_v7_6y.py",
     '        log.info("[ROUTE] %s is showing Multiview %s; leaving it for a normal "\n'
     '                 "route", decoder, active_multiview)\n'
     "        ok, steps, released, message = _exit_active_multiview(\n"
     "            decoder, active_multiview, mv_state)",
     '        log.info("[ROUTE] %s is showing Multiview %s; leaving it for a normal "\n'
     '                 "route", decoder, active_multiview)\n'
     "        ok, steps, released, message = (True, [], [], '')",
     "the verified teardown would be skipped, leaving the display on a "
     "Multiview it is no longer showing", EXIT),
    ("unroute.deletes-the-composition", "OmniMatrix_upgrade_server_v7_6y.py",
     '        log.info("[UNROUTE] %s is showing Multiview %s; leaving it first",\n'
     "                 decoder, active_multiview)\n"
     "        ok, steps, released, message = _exit_active_multiview(\n"
     "            decoder, active_multiview, mv_state)",
     '        log.info("[UNROUTE] %s is showing Multiview %s; leaving it first",\n'
     "                 decoder, active_multiview)\n"
     "        ok, steps, released, message = (True, [], [], '')",
     "unrouting would skip the verified teardown and leave the display on a "
     # The teardown itself is covered by test_multiview; that unroute goes
     # THROUGH it rather than around it is covered beside the rest of the
     # unroute contract. A suite list that names only the first reports this
     # as uncaught, which reads as a missing control rather than a misdirected
     # harness.
     "Multiview it is no longer showing", EXIT + ["tests.test_user_routing"]),
    # Aimed at the rollback that actually runs. The first version pointed at a
    # second one further down that `if not ok:` above had already made
    # unreachable -- mutating it changed nothing, which is how the dead code
    # was found and removed.
    ("route.no-rollback", "OmniMatrix_upgrade_server_v7_6y.py",
     '        if multiview_exit:\n'
     '            restored, restore_detail = _restore_multiview_after_failed_route(',
     '        if False:\n'
     '            restored, restore_detail = _restore_multiview_after_failed_route(',
     "a failed route would leave the display blank rather than restoring the "
     "Multiview", EXIT),

    # ---- Clear Units clears the inventory AND every reference to it -----
    ("clear.cascade-skips-accounts", "OmniMatrix_upgrade_server_v7_6y.py",
     "        accounts = _user_store.forget_devices(identities)",
     "        accounts = 0",
     "a cleared device would stay granted to an account", CLEAR + LIFECYCLE),
    ("clear.cascade-skips-groups", "OmniMatrix_upgrade_server_v7_6y.py",
     "        groups = _display_groups.forget_members(identities)",
     "        groups = 0",
     "a Display Group would keep naming a device that is gone",
     CLEAR + LIFECYCLE),
    ("clear.cascade-skips-names", "OmniMatrix_upgrade_server_v7_6y.py",
     "        aliases = _device_aliases.forget_many(identities)",
     "        aliases = 0",
     "a stale friendly name would stay attached to a cleared identity",
     LIFECYCLE),
    ("clear.cascade-never-restores", "OmniMatrix_upgrade_server_v7_6y.py",
     "                    _user_store.restore_grants(grants)",
     "                    pass",
     "a failure part-way would leave accounts stripped but groups intact",
     LIFECYCLE),
    ("clear.refusal-still-clears", "OmniMatrix_upgrade_server_v7_6y.py",
     '                        "User accounts and groups, so nothing was cleared."}), 503',
     '                        "User accounts and groups, so nothing was cleared."}), 200',
     "a store that could not be updated would be reported as success",
     CLEAR + LIFECYCLE),
    ("clear.forget-devices-keeps-sources", "omni_users.py",
     "                sources = [s for s in p.sources if gone is not None and s not in gone]",
     "                sources = list(p.sources)",
     "an account would keep a source that no longer exists", CLEAR + LIFECYCLE),
    ("clear.forget-members-keeps-members", "omni_groups.py",
     '                kept = [m for m in g["members"] if gone is not None and m not in gone]',
     '                kept = list(g["members"])',
     "a group would keep displays that were cleared", CLEAR + LIFECYCLE),
    ("clear.is-not-admin", "omni_access.py",
     '    "/api/clear_units": (Access.ADMIN, None),',
     '    "/api/clear_units": (Access.AUTHENTICATED, None),',
     "a restricted account could clear the inventory", LIFECYCLE),

    # ---- permissions are live, and a page can tell when they changed ----
    ("perm.change-signs-out", "omni_users.py",
     "            # anybody out of a browser or a lectern tablet.\n",
     "            # anybody out of a browser or a lectern tablet.\n"
     "            self.revoke_all_for_user(user_id, _locked=True)\n",
     "a permission change would sign the user out instead of applying live",
     LIFECYCLE),
    ("revision.ignores-destinations", "omni_projection.py",
     '             "d:" + ",".join(sorted(p.destinations))]',
     '             "d:"]',
     "a removed display would not tell an open page to refresh", LIFECYCLE),
    ("revision.ignores-names", "omni_projection.py",
     "    if callable(names):",
     "    if False:",
     "a renamed device would not tell an open page to refresh", LIFECYCLE),
    ("revision.is-public", "omni_access.py",
     '    "/api/user/revision": (Access.AUTHENTICATED, None),',
     '    "/api/user/revision": (Access.PUBLIC, None),',
     "an unauthenticated caller could read the digest", LIFECYCLE),
    ("api.cacheable", "OmniMatrix_upgrade_server_v7_6y.py",
     '    if request.path.startswith(("/api/", "/access/")):',
     '    if False:',
     "an account's view could be answered from a browser cache", LIFECYCLE),
    ("nav.hint-always-off", "OmniMatrix_upgrade_server_v7_6y.py",
     '        flag = "1" if remote_access_enabled() else "0"',
     '        flag = "0"',
     "the Remote Access tab would not be served with the page", LIFECYCLE),
    ("json.body-not-checked", "omni_remote_api.py",
     "    return body if isinstance(body, dict) else {}",
     "    return body or {}",
     "a bare string body would be a 500 instead of a 400", MATRIX),
    ("user.password-change-keeps-sessions", "omni_users.py",
     "            account.password_hash = hash_password(password)\n"
     "            account.password_changed_at = _now()\n"
     "            self._persist()\n"
     "            # Every password-authenticated session for this account ends. A password\n"
     "            # change that left old sessions alive would not be a password change. A\n"
     "            # session a Direct Access link opened was never authenticated by the\n"
     "            # password, so it follows the link's own lifecycle instead.\n"
     "            self.revoke_all_for_user(user_id, _locked=True,\n"
     "                                     except_method=BY_DIRECT_LINK)",
     "            account.password_hash = hash_password(password)\n"
     "            account.password_changed_at = _now()\n"
     "            self._persist()",
     "an old session would survive a password change", LIFECYCLE),


    # ---- a link is indefinite until somebody revokes it -----------------
    #
    # These links are wall panels and lectern tablets: idle by definition, and
    # nobody standing at one has a password. A session that expired put a
    # sign-in page in front of them. Removing the timeout takes nothing away,
    # because whoever holds the URL can mint a fresh session at any moment --
    # so both directions need fencing: the timeout must not come back, and the
    # password limits must not be loosened to match.
    ("direct.sessions-expire-again", "omni_users.py",
     "DIRECT_SESSIONS_EXPIRE = False",
     "DIRECT_SESSIONS_EXPIRE = True",
     "an idle wall panel would be asked for a password it does not have",
     DIRECT + ["tests.test_remote_authorization"]),
    ("direct.password-limits-loosened", "omni_users.py",
     "IDLE_TIMEOUT_SECONDS = 8 * 60 * 60\n"
     "ABSOLUTE_TIMEOUT_SECONDS = 24 * 60 * 60",
     "IDLE_TIMEOUT_SECONDS = 8 * 60 * 60 * 1000\n"
     "ABSOLUTE_TIMEOUT_SECONDS = 24 * 60 * 60 * 1000",
     "a password session would effectively never expire either",
     DIRECT + ["tests.test_remote_authorization"]),
    ("direct.expiry-check-removed", "omni_users.py",
     "            if session.expired():\n"
     "                self._sessions.pop(token, None)\n"
     "                return (None, None)",
     "            if False:\n"
     "                self._sessions.pop(token, None)\n"
     "                return (None, None)",
     "no session would ever expire, password ones included",
     DIRECT + ["tests.test_remote_authorization"]),

    # ---- unification pass: Display Groups -----------------------------
    ("group.no-group-check", "omni_remote_api.py",
     '            if not account.permissions.may_control_group(group_id):\n'
     '                return "group_not_permitted"\n'
     '            return None\n'
     '\n'
     '        refusal = _revalidate(_check, "/api/user/route_group")',
     '            return None\n'
     '\n'
     '        refusal = _revalidate(_check, "/api/user/route_group")',
     "any account with A/V could route to any group", GROUPS),
    ("group.no-source-check", "omni_remote_api.py",
     '            if not account.permissions.may_use_source(source_id):\n'
     '                return "source_not_permitted"\n'
     '            if not account.permissions.may_control_group(group_id):',
     '            if not account.permissions.may_control_group(group_id):',
     "a group route could use a source the account was never given", GROUPS),
    ("group.member-grant-implies-group", "omni_users.py",
     '        return _normalise_group_id(group_id) in self.groups',
     '        return _normalise_group_id(group_id) in self.groups or bool(self.destinations)',
     "holding a display would grant every group", GROUPS),
    ("group.group-grant-implies-members", "omni_users.py",
     '        return _normalise_identity(identity) in self.destinations\n',
     '        return _normalise_identity(identity) in self.destinations or bool(self.groups)\n',
     "holding a group would grant its members individually", GROUPS),
    ("group.stops-after-failure", "omni_groups.py",
     '        results.append(dict(outcome, index=index))\n',
     '        results.append(dict(outcome, index=index))\n'
     '        if not outcome.get("ok"):\n'
     '            break\n',
     "one failed display would leave the rest of the room unrouted", GROUPS),
    ("group.exception-aborts-run", "omni_groups.py",
     "        except Exception as exc:                  # a member's failure is only its own",
     "        except ZeroDivisionError as exc:          # a member's failure is only its own",
     "an exception in one member would abort the others", GROUPS),
    ("group.partial-reported-as-ok", "omni_groups.py",
     '    return {"ok": bool(results) and routed == len(results),',
     '    return {"ok": bool(results) and routed > 0,',
     "a half-routed room would be reported as complete", GROUPS),
    ("group.reply-leaks-members", "omni_remote_api.py",
     '            "total": summary["total"], "group": group["name"],',
     '            "total": summary["total"], "group": group["name"],\n'
     '            "members": group["members"],',
     "a restricted client would learn each display's hardware identity", GROUPS),
    ("group.routes-with-another-mode", "omni_remote_api.py",
     '        mode = str(data.get("mode") or "av").strip().lower()',
     '        mode = str(data.get("mode") or "video").strip().lower()',
     "a route that names no mode would no longer carry audio and video together", GROUPS),
    ("group.no-revalidate-after-lock", "omni_remote_api.py",
     '                with locks.hold(locks.decoder(identity)):\n'
     '                    refusal = _revalidate(_check, "/api/user/route_group")\n'
     '                    if refusal is not None:\n'
     '                        return refused\n',
     '                with locks.hold(locks.decoder(identity)):\n',
     "permission withdrawn while a member waited for its lock would still be used", GROUPS),
    ("group.no-revalidate-before-member", "omni_remote_api.py",
     '            refused = {"ok": False, "result": "refused", "name": name}\n'
     '            refusal = _revalidate(_check, "/api/user/route_group")\n'
     '            if refusal is not None:\n'
     '                return refused\n',
     '            refused = {"ok": False, "result": "refused", "name": name}\n',
     "permission withdrawn between members would not stop the next one", GROUPS),
    ("group.unknown-group-routes", "omni_remote_api.py",
     '        if group is None:\n'
     '            return jsonify({"ok": False, "error": "That group is not available."}), 404',
     '        if group is None:\n'
     '            group = {"id": group_id, "name": "", "members": []}',
     "an unknown group would answer success with nothing routed", GROUPS),

    # ---- unification pass: classification of the new routes -------------
    ("classify.route-group-feature-dropped", "omni_access.py",
     '    "/api/user/route_group": (Access.USER, FEATURE_AV),',
     '    "/api/user/route_group": (Access.USER, None),',
     "an account without A/V could use the group route", GROUPS + GATE),
    ("classify.admin-groups-open", "omni_access.py",
     '    "/api/admin/groups": (Access.ADMIN, None),',
     '    "/api/admin/groups": (Access.USER, None),',
     "a restricted account could create and edit display groups", GROUPS + GATE),
    ("classify.admin-group-item-open", "omni_access.py",
     '    "/api/admin/groups/<group_id>": (Access.ADMIN, None),',
     '    "/api/admin/groups/<group_id>": (Access.USER, None),',
     "a restricted account could rename or delete a display group", GROUPS + GATE),
    ("classify.retention-open", "omni_access.py",
     '    "/api/admin/audit/retention": (Access.ADMIN, None),',
     '    "/api/admin/audit/retention": (Access.USER, None),',
     "a restricted account could shorten the activity record", GROUPS + GATE + RETENT),
    ("classify.firmware-check-open", "omni_access.py",
     '    "/api/firmware_status/check": (Access.ADMIN, None),',
     '    "/api/firmware_status/check": (Access.USER, None),',
     "a restricted account could trigger outbound firmware checks", GROUPS + GATE + FW),

    # ---- unification pass: Multiview is not offered to restricted accounts
    ("umv.feature-reintroduced", "omni_access.py",
     'FEATURES = (FEATURE_AV, FEATURE_USB)',
     'FEATURES = (FEATURE_AV, FEATURE_USB, "multiview")',
     "restricted accounts could be offered Multiview again", UMV),
    ("umv.unknown-feature-accepted", "omni_users.py",
     '        allowed = omni_access.user_features()',
     '        allowed = set(features)',
     "an old stored multiview feature would keep acting after its control was removed", UMV),
    ("umv.user-route-restored", "omni_access.py",
     '    "/api/user/usb_route": (Access.USER, FEATURE_USB),',
     '    "/api/user/usb_route": (Access.USER, FEATURE_USB),\n'
     '    "/api/user/multiview_show": (Access.USER, None),',
     "a restricted Multiview endpoint would be classified for a User again", UMV),

    # ---- unification pass: Activity retention ---------------------------
    ("retention.days-lower-bound", "omni_users.py",
     '                or not ACTIVITY_MIN_DAYS <= int(days) <= ACTIVITY_MAX_DAYS):',
     '                or not int(days) <= ACTIVITY_MAX_DAYS):',
     "zero or negative days would be accepted and wipe the record", RETENT),
    ("retention.days-upper-bound", "omni_users.py",
     '                or not ACTIVITY_MIN_DAYS <= int(days) <= ACTIVITY_MAX_DAYS):',
     '                or not ACTIVITY_MIN_DAYS <= int(days):',
     "an unbounded number of days would be accepted", RETENT),
    ("retention.size-bounds", "omni_users.py",
     '        if not ACTIVITY_MIN_MAX_BYTES <= wanted <= ACTIVITY_MAX_MAX_BYTES:',
     '        if False:',
     "any size limit would be accepted, including one that disables the cap", RETENT),
    ("retention.bool-accepted", "omni_users.py",
     '        if (isinstance(days, bool) or not isinstance(days, (int, float))',
     '        if (not isinstance(days, (int, float))',
     "True would be read as one day", RETENT),
    ("retention.age-prune-off", "omni_users.py",
     '            if isinstance(at, (int, float)) and at < cutoff:',
     '            if False:',
     "entries older than the window would be kept forever", RETENT),
    ("retention.size-prune-off", "omni_users.py",
     '        while self._audit and self._audit_bytes > self._activity_max_bytes:',
     '        while False and self._audit_bytes > self._activity_max_bytes:',
     "the activity log could grow past its disk budget", RETENT),

    # ---- unification pass: firmware is never sent to 311/324 or extenders
    ("firmware.model-not-refused", "OmniMatrix_upgrade_server_v7_6y.py",
     '        if _firmware_unsupported_model((unit or {}).get("model")):',
     '        if False:',
     "a cached 311/324 could be an upgrade target", FW),
    ("firmware.extender-address-not-refused", "OmniMatrix_upgrade_server_v7_6y.py",
     '        elif ip in extender_ips and unit is None:',
     '        elif False:',
     "an extender address could be an upgrade target", FW),
    ("firmware.guard-skipped", "OmniMatrix_upgrade_server_v7_6y.py",
     '        if refused_ips:\n'
     '            log.warning("[UPGRADE] refused',
     '        if False:\n'
     '            log.warning("[UPGRADE] refused',
     "the whole refusal would be bypassed", FW),
    ("firmware.model-pattern-narrowed", "OmniMatrix_upgrade_server_v7_6y.py",
     '(?:311|324)',
     '(?:311)',
     "324 would become an upgrade target", FW),
    ("firmware.targets-not-a-list", "OmniMatrix_upgrade_server_v7_6y.py",
     '        if not isinstance(targets, list):\n'
     '            return jsonify({"ok": False, "error": "targets must be a list"}), 400',
     '        if False:\n'
     '            return jsonify({"ok": False, "error": "targets must be a list"}), 400',
     "a non-list target value would skip the extender refusal", FW),

    # ---- unification pass: adapter default and transport default --------
    ("adapter.virtual-first", "omni_adapters.py",
     '                  key=lambda a: _RANK.get(adapter_kind(a), 3))',
     '                  key=lambda a: -_RANK.get(adapter_kind(a), 3))',
     "a virtual adapter would be preferred over a physical Ethernet port", ADAPT),
    ("adapter.preference-overrides-saved-scan", "ui/index.html",
     '  if (match) {\n    sel.value = match.value;',
     '  if (match && false) {\n    sel.value = match.value;',
     "the default would replace the operator's saved scan adapter", ADAPT),
    ("adapter.preference-overrides-saved-remote", "ui/settings.js",
     '      if (match) { adapter.value = match.value; return; }',
     '      if (match && false) { adapter.value = match.value; return; }',
     "the default would replace the configured Remote Access adapter", ADAPT),
    ("http.acknowledgement-skipped", "omni_remote_api.py",
     '        if transport == "http" and data.get("accept_insecure") is not True:',
     '        if False:',
     "plaintext remote access could start without the explicit acknowledgement", HTTPDEF),
    ("http.default-is-https", "OmniMatrix_upgrade_server_v7_6y.py",
     '             "transport": "http", "cert_path": "", "key_path": ""}',
     '             "transport": "https", "cert_path": "", "key_path": ""}',
     "a first-time setup would no longer default to HTTP", HTTPDEF),
    ("http.damaged-record-reads-http", "OmniMatrix_upgrade_server_v7_6y.py",
     '                    "transport": ("http" if stored.get("transport") == "http"\n'
     '                                  else "https"),',
     '                    "transport": "http",',
     "a record with no readable transport would serve in clear instead of refusing", HTTPDEF),
    ("http.explicit-https-ignored", "omni_remote_api.py",
     '        transport = (requested if requested in ("http", "https")',
     '        transport = ("http" if requested in ("http", "https")',
     "a requested HTTPS would be downgraded to plaintext", HTTPDEF),

    ("direct.session-method-ignored", "omni_users.py",
     '        self.method = method if method in SESSION_METHODS else BY_PASSWORD',
     '        self.method = BY_PASSWORD',
     "a link session would be indistinguishable from a password one, and the "
     "administrative bar would never apply", DIRECT),
    # ---- final pass: a route asks nothing; state text stays readable ------------
    ("matrix.route-asks-again", "ui/matrix/matrix.js",
     "const checkedDecoders = checkedDecoderIps(",
     "await omniConfirm({title: 'Multiview is active on this decoder', message: 'x'}); const checkedDecoders = checkedDecoderIps(",
     "a route to a decoder showing a Multiview would ask a question the operator already answered by choosing the route", MVPROMPT),
    ("text.light-mix-removed", "ui/templates.css",
     "--state-success-text: color-mix(in srgb, var(--state-success) 50%, #000000 50%);",
     "--state-success-text: color-mix(in srgb, var(--state-success) 100%, #000000 0%);",
     "success text on a light page would be the preset's bright fill again, unreadable", STATETEXT),
    ("text.mix-under-100", "ui/templates.css",
     "--state-error-text: color-mix(in srgb, var(--state-error) 55%, #000000 45%);",
     "--state-error-text: color-mix(in srgb, var(--state-error) 55%, #000000 40%);",
     "a mix summing to less than 100 turns the text translucent", STATETEXT),
    ('usbprep.signature-ignores-address', 'OmniMatrix_upgrade_server_v7_6y.py', '            members.append(("I", str(unit.get("ip") or ""), _norm_usb_mac(unit.get("mac")),', '            members.append(("I", "", _norm_usb_mac(unit.get("mac")),', 'a member that moved would still match the prepared matrix, which would paint it at the old address', USBPREP),
    ('usbprep.signature-includes-live-state', 'OmniMatrix_upgrade_server_v7_6y.py', '                            str(unit.get("model") or "").lower()))', '                            str(unit.get("model") or "").lower() + str(unit.get("online"))))', 'every change of reachability would discard the prepared matrix, so it would never be used', USBPREP),
    ('usbprep.signature-after-the-reads', 'OmniMatrix_upgrade_server_v7_6y.py', '            "inventory_signature": inventory_signature,\n            "lex": lex_units,', '            "inventory_signature": _usb_inventory_signature(),\n            "lex": lex_units,', 'a change during the request would be stamped on a snapshot built from the older inventory', USBPREP),
    ('usbprep.standalone-not-counted', 'OmniMatrix_upgrade_server_v7_6y.py', '            members.append(("S", str(device.get("ip") or ""), _norm_usb_mac(device.get("mac")), ""))', '            pass', 'a standalone endpoint arriving or clearing would not invalidate the prepared matrix', USBPREP),
    ('usbprep.route-unclassified-public', 'omni_access.py', '    "/api/usb_state/signature": (Access.ADMIN, None),', '    "/api/usb_state/signature": (Access.PUBLIC, None),', 'the inventory signature would be readable without signing in', ['tests.test_usb_prep', 'tests.test_usb_prep_js', 'tests.test_route_authorization_matrix']),
    ('usbprep.page-overwrites-live-answer', 'ui/matrix/usb.js', 'if (!prepared || lastState || usbStateApplied) return;', 'if (!prepared) return;', 'a prepared copy arriving late would replace a live answer', USBPREP),
    ('usbprep.signature-not-checked', 'ui/usb-prep.js', 'if (j && j.ok && j.signature === snap.inventory_signature) return snap;', 'if (j && j.ok) return snap;', 'a prepared matrix would be painted whatever the inventory had become', USBPREP),
    ('usbprep.stale-read-kept', 'ui/usb-prep.js', 'if (s && s.ok && began === generation) { store(s); return true; }', 'if (s && s.ok) { store(s); return true; }', 'a read in flight when the inventory changed would be stored as current', USBPREP),
    ('preview.no-single-flight', 'ui/user/preview-refresh.js', '      if (current) {\n        stats.joined += 1;', '      if (false) {\n        stats.joined += 1;', 'a slow device would be asked again on every tick and gather a backlog', PREVIEW),
    ('preview.timer-while-hidden', 'ui/user/preview-refresh.js', 'var want = !stopped && !hidden && (eligibleIds(true).size > 0);', 'var want = !stopped && (eligibleIds(true).size > 0);', 'a page nobody is looking at would keep refreshing', PREVIEW),
    ('preview.off-screen-refreshed', 'ui/user/preview-refresh.js', "        if (!entry.visible) return;\n        if (entry.state === 'live'", "        if (entry.state === 'live'", 'a card scrolled out of view would keep being fetched', PREVIEW),
    ('preview.disabled-polled', 'ui/user/preview-refresh.js', "if (entry.state === 'live' || (includeRetries && entry.state === 'unavailable')) ids.add(entry.id);", "if (entry.state === 'live' || entry.state === 'disabled' || (includeRetries && entry.state === 'unavailable')) ids.add(entry.id);", 'a preview switched off at the device would be asked for every five seconds', PREVIEW),
    ('preview.route-does-not-refresh', 'ui/user/preview-refresh.js', '          request(id, {dirty: true});\n        });\n      },', '          void 0;\n        });\n      },', 'pictures would stay old until the next tick after a route', PREVIEW),
    ('preview.post-route-request-repeated', 'ui/user/preview-refresh.js', '          if (current && current.startedAt >= at) return;       // already describes the world after the route\n', '', 'a request begun after the route would be asked for a second time', PREVIEW),
    ('preview.request-never-released', 'ui/user/preview-refresh.js', '          inflight.delete(id);\n          sync();', '          sync();', 'one failed request would leave that source unable to be asked for again', PREVIEW),
    ('preview.stop-does-not-stop', 'ui/user/preview-refresh.js', '      stop: function () {\n        stopped = true;', '      stop: function () {\n        stopped = false;', 'signing out would leave the page refreshing pictures', PREVIEW),
    ('preview.page-never-tells-it-a-route-finished', 'ui/user/user.js', '    await refresh();\n    previews.routeCompleted(at);', '    await refresh();', 'the pictures would wait for the next tick after every route', PREVIEW),
    ('offline.engine-gate-removed', 'OmniMatrix_upgrade_server_v7_6y.py', '    offline = _unit_offline_refusal(source=encoder, display=decoder)\n    if offline is not None:\n        return offline', '    offline = _unit_offline_refusal(source=encoder, display=decoder)\n    if offline is not None:\n        pass', 'a route to a unit that is not answering would go on to spend a timeout per step', OFFLINE),
    ('offline.usb-gate-removed', 'OmniMatrix_upgrade_server_v7_6y.py', '    offline = _unit_offline_refusal(usb_host=lex_ip, usb_device=rex_ip)\n    if offline is not None:\n        return offline', '    offline = _unit_offline_refusal(usb_host=lex_ip, usb_device=rex_ip)\n    if offline is not None:\n        pass', 'a pairing would be attempted against a parent that is not answering', OFFLINE),
    ('offline.address-in-the-sentence', 'OmniMatrix_upgrade_server_v7_6y.py', '"error": subject + ", so nothing was changed.",', '"error": subject + " (" + ", ".join(ip for _r, ip in down) + "), so nothing was changed.",', 'a restricted account would be shown a device address', OFFLINE),
    ('offline.unknown-is-assumed-offline', 'omni_liveness.py', '            reachable = bool(self._probe(ip))', '            reachable = False', 'a unit nothing had looked at would be refused without being asked', OFFLINE),
    ('offline.stale-observation-believed', 'omni_liveness.py', '            window = FRESH_ONLINE_SECONDS if reachable else FRESH_OFFLINE_SECONDS', '            window = 10 ** 9', 'a unit seen once would be believed forever, in either direction', OFFLINE),
    ('offline.probe-error-is-online', 'omni_liveness.py', '        except Exception:\n            reachable = False\n        self.record(ip, reachable, "probe")', '        except Exception:\n            reachable = True\n        self.record(ip, reachable, "probe")', 'a probe that could not run would let a route through to a unit that may be gone', OFFLINE),
    ('offline.state-overlay-removed', 'OmniMatrix_upgrade_server_v7_6y.py', '            unit["online"] = seen', '            pass', 'the screens would never be told a unit was offline', OFFLINE),
    ('offline.refresh-removed', 'OmniMatrix_upgrade_server_v7_6y.py', '    _refresh_offline_in_background()\n    for unit in enc_mapped + dec_mapped:', '    for unit in enc_mapped + dec_mapped:','a unit that came back would stay disabled on screen', OFFLINE),
    ('offline.clear-keeps-the-record', 'OmniMatrix_upgrade_server_v7_6y.py', '        _liveness.forget()\n', '', 'units rediscovered after Clear Units would carry the old record', OFFLINE),
    ('offline.sweep-does-not-record', 'OmniMatrix_upgrade_server_v7_6y.py', '                            answered = True\n                            _liveness.record(ip, True, "verify")', '                            answered = True', 'the sweep would learn nothing about which units answer', OFFLINE),
    ('offline.matrix-click-not-refused', 'ui/matrix/matrix.js', '      if (unitIsOffline(encoderUnit) || unitIsOffline(decoderUnit)) {', '      if (false) {', 'a click that got past the disabled input would still route to a unit that is not answering', OFFLINEUI),
    ('offline.group-selection-includes-offline', 'ui/matrix/matrix.js', 'return routeCodecCompatible(encoderUnit, targetUnit) && !unitIsOffline(targetUnit);', 'return routeCodecCompatible(encoderUnit, targetUnit);', 'a group route would include decoders that are not answering', OFFLINEUI),
    ('offline.offline-is-not-structure', 'ui/matrix/matrix.js', "(unitIsOffline(e) ? 'off' : '')).join('\\u0002') + '\\u0003'", "'').join('\\u0002') + '\\u0003'", 'an encoder going offline would not redraw its column', OFFLINEUI),
    ('offline.user-route-stays-enabled', 'ui/user/user.js', '      if (a && b && quiet) {', '      if (false) {', 'Route would stay enabled for a unit that went offline after it was chosen', USERPAGE),
    ('version.current-not-green', 'ui/settings.js', "    line.className = 'update-status ok';", "    line.className = 'update-status';", 'a current version would not be shown as such', VERSION),
    ('version.update-not-red', 'ui/settings.js', "    line.className = 'update-status bad';", "    line.className = 'update-status warn';", 'an available update would not be red', VERSION),
    ('version.unknown-is-green', 'ui/settings.js', "  line.className = 'update-status warn';", "  line.className = 'update-status ok';", 'a check that could not be made would read as up to date', VERSION),
    ('version.red-uses-raw-fill', 'ui/settings.css', '  color: var(--state-error-text);\n  font-weight: 700; letter-spacing: .02em;', '  color: var(--state-error);\n  font-weight: 700; letter-spacing: .02em;', "red status text would be the preset's raw fill, unreadable in some presets", VERSION),
    ('userpage.route-bar-never-shown', 'ui/user/user.js', "    el('action_bar').hidden = !bar;\n", "    el('action_bar').hidden = true;\n", 'the restricted User page would ship its Route bar hidden and never reveal it, so nobody could route', USERPAGE),
    ('matrix.route-ignores-interface', 'omni_remote_api.py', '            if not account.permissions.has_interface(interface):\n                return "interface_not_permitted"\n            if not account.permissions.may_use_source(source_id):\n                return "source_not_permitted"\n            if not account.permissions.may_control_destination(destination_id):', '            if not account.permissions.may_use_source(source_id):\n                return "source_not_permitted"\n            if not account.permissions.may_control_destination(destination_id):', 'a Standard-only account could route through the Matrix by claiming it', MXSRV),
    ('matrix.group-route-ignores-interface', 'omni_remote_api.py', '            if not account.permissions.has_interface(interface):\n                return "interface_not_permitted"\n            if not account.permissions.may_use_source(source_id):\n                return "source_not_permitted"\n            if not account.permissions.may_control_group(group_id):', '            if not account.permissions.may_use_source(source_id):\n                return "source_not_permitted"\n            if not account.permissions.may_control_group(group_id):', 'a Standard-only account could route a group through the Matrix', MXSRV),
    ('matrix.state-ignores-interface', 'omni_remote_api.py', '        if not account.is_admin and not account.permissions.has_interface(\n                omni_users.INTERFACE_MATRIX):', '        if False:', 'any routing account was handed the Matrix grid', MXSRV),
    ('matrix.state-route-public', 'omni_access.py', '    "/api/user/matrix_state": (Access.USER, FEATURE_AV),', '    "/api/user/matrix_state": (Access.PUBLIC, None),', 'the grid would be served without a session', MXSRV),
    ('matrix.breakaway-allowed-from-standard', 'omni_remote_api.py', '        if mode != "av" and interface != omni_users.INTERFACE_MATRIX:', '        if False:', 'video-only and audio-only routing would be open to the Standard interface', MXSRV),
    ('matrix.mode-not-validated', 'omni_remote_api.py', '        if mode not in _ROUTE_MODES:', '        if False:', 'a mode nobody defined would be handed to the routing engine', MXSRV),
    ('matrix.interface-not-validated', 'omni_remote_api.py', '        if interface not in omni_users.INTERFACES:', '        if False:', 'an unknown interface name would be accepted and compared as text', MXSRV),
    ('matrix.unroute-needs-no-interface', 'omni_remote_api.py', '            if not account.permissions.interfaces:\n                return "interface_not_permitted"\n            if not account.permissions.may_control_destination(destination_id):', '            if not account.permissions.may_control_destination(destination_id):', 'an account holding no routing interface could still unroute a display', MXSRV),
    ('matrix.preview-needs-no-interface', 'omni_remote_api.py', '            if not account.permissions.interfaces:\n                return "interface_not_permitted"\n            if not account.permissions.may_use_source(source_id):\n                return "source_not_permitted"\n            return None\n\n        refusal = _revalidate(_check, "/api/user/source_preview")', '            if not account.permissions.may_use_source(source_id):\n                return "source_not_permitted"\n            return None\n\n        refusal = _revalidate(_check, "/api/user/source_preview")', 'an account holding no routing interface could still fetch source pictures', MXSRV),
    ('matrix.route-audited-as-standard', 'omni_remote_api.py', '                    "matrix.route" if interface == omni_users.INTERFACE_MATRIX\n                    else "route", actor=_current_account(), detail={', '                    "route", actor=_current_account(), detail={', 'a Matrix route would be indistinguishable from a Standard one in the activity log', MXSRV),
    ('matrix.group-route-audited-as-standard', 'omni_remote_api.py', '            "matrix.route_group" if interface == omni_users.INTERFACE_MATRIX\n            else "route_group", actor=_current_account(), detail={', '            "route_group", actor=_current_account(), detail={', 'a Matrix group route would be indistinguishable from a Standard one in the activity log', MXSRV),
    ('matrix.migration-grants-matrix', 'omni_users.py', '            interfaces = (INTERFACE_STANDARD,)', '            interfaces = INTERFACES', 'every account that existed before the choice would gain the Matrix on upgrade', MXSRV),
    ('matrix.migration-removes-standard', 'omni_users.py', '            interfaces = (INTERFACE_STANDARD,)', '            interfaces = ()', 'every account that existed before the choice would lose Standard routing on upgrade', MXSRV),
    ('matrix.empty-choice-repaired', 'omni_users.py', '                   raw["interfaces"] if isinstance(raw.get("interfaces"),\n                                                   (list, tuple)) else None)', '                   raw.get("interfaces") or None)', 'an account deliberately given neither interface would be quietly given Standard', MXSRV),
    ('matrix.no-interface-allowed', 'omni_users.py', '    if role == ROLE_USER and permissions.has_feature("av") \\\n            and not permissions.interfaces:\n        return "choose at least one routing interface"', '    if False:\n        return "choose at least one routing interface"', 'an account could be saved able to route with no way to do it', MXSRV),
    ('matrix.api-resets-interfaces-when-absent', 'omni_remote_api.py', '            if held is not None and not (isinstance(raw, dict)', '            if False and not (isinstance(raw, dict)', 'a client that does not know about interfaces would reset them on every save', MXSRV),
    ('matrix.revision-ignores-interfaces', 'omni_projection.py', '             "i:" + ",".join(sorted(p.interfaces)),\n', '', 'an open page would never notice its Matrix was added or taken away', MXSRV),
    ('matrix.leaks-unpermitted-source-in-other', 'omni_projection.py', '    if identity:\n        # A source this account does not hold.', '    if identity and False:\n        # A source this account does not hold.', 'a display showing a source outside the account would be reported as showing nothing', MXSRV),
    ('matrix.group-all-without-every-member', 'omni_projection.py', '        state = "all" if online == total else "partial"', '        state = "all"', 'a group with a member not answering would be reported as entirely on one source', MXSRV),
    ('matrix.group-partial-called-mixed', 'omni_projection.py', '    elif len(tokens - {"none"}) == 1:\n        state = "partial"', '    elif False:\n        state = "partial"', 'one source on some members and nothing on the rest would read as a mix', MXSRV),
    ('matrix.group-counts-offline-members', 'omni_projection.py', '        live = [by_identity[m] for m in members\n                if m in by_identity and by_identity[m]["online"]]', '        live = [by_identity[m] for m in members if m in by_identity]', 'a member that is not answering would be counted as carrying whatever it last did', MXSRV),
    ('matrix.audio-plane-copies-video', 'omni_projection.py', '            "audio": _plane(audio_identity, permitted),', '            "audio": _plane(routed, permitted),', 'breakaway would be invisible: audio would always be shown following video', MXSRV),
    ('matrix.compositing-keeps-video-crosspoint', 'omni_projection.py', '    if multiview:\n        return {"mode": "multiview", "source_id": None}', '    if False:\n        return {"mode": "multiview", "source_id": None}', 'a compositing display would be drawn with a video route it is not showing', MXSRV),
    ('matrix.ui-partial-drawn-as-all', 'ui/user/matrix.js', "      return n === plane.total ? 'all' : 'partial';", "      return 'all';", 'a group with some members on a source would be drawn as entirely on it', MXUI),
    ('matrix.ui-offline-cell-enabled', 'ui/user/matrix.js', '        cell.button.disabled = gone || busy;', '        cell.button.disabled = busy;', 'a cell for an offline source or display could be pressed', MXUI),
    ('matrix.ui-busy-cell-enabled', 'ui/user/matrix.js', '        cell.button.disabled = gone || busy;', '        cell.button.disabled = gone;', 'a second route could be started while the first is still out', MXUI),
    ('matrix.ui-mode-ignored', 'ui/user/matrix.js', '      deps.route({source: cell.dataset.source, kind: cell.dataset.kind,\n                  id: cell.dataset.row, mode: mode});', "      deps.route({source: cell.dataset.source, kind: cell.dataset.kind,\n                  id: cell.dataset.row, mode: 'av'});", 'Video and Audio would route both, so breakaway would be impossible', MXUI),
    ('matrix.ui-rebuilds-on-every-read', 'ui/user/matrix.js', '      if (key === structureKey) { paint(); return; }', '', 'every read would throw the grid away and rebuild it', MXUI),
    ('matrix.ui-preview-without-delay', 'ui/user/matrix.js', '      }, HOVER_DELAY_MS);', '      }, 0);', 'a pointer passing over a heading would fetch a picture', MXUI),
    ('matrix.ui-preview-survives-leaving', 'ui/user/matrix.js', "      if (event.target.closest('.mx-source-head')) closePreview();", '', 'a picture would stay up and a fetch would stay scheduled after the pointer left', MXUI),
    ('matrix.ui-late-picture-drawn', 'ui/user/matrix.js', '        if (token !== preview.token || !preview.card) return;   // the pointer moved on', '        if (!preview.card) return;', 'a picture that arrived after the pointer left would be drawn', MXUI),
    ('matrix.ui-offline-source-previewed', 'ui/user/matrix.js', '      if (!source || source.online === false || !deps.fetchPreview) return;', '      if (!source || !deps.fetchPreview) return;', 'an offline source would be asked for a picture', MXUI),
    ('matrix.ui-route-claims-standard', 'ui/user/user.js', "      const common = {source: request.source, mode: request.mode, interface: 'matrix'};", "      const common = {source: request.source, mode: request.mode, interface: 'standard'};", 'a Matrix route would claim the Standard interface and be refused for a Matrix-only account', MXUI),
    ('matrix.ui-remembered-tab-is-a-grant', 'ui/user/user.js', "      chosen = allowed.indexOf(saved) !== -1 ? saved : (allowed[0] || 'none');", "      chosen = saved || (allowed[0] || 'none');", 'a remembered Matrix would be shown to an account that no longer holds it', MXUI),
    ('matrix.ui-stale-read-drawn', 'ui/user/user.js', '    if (mine !== matrixSeq) return;\n    if (status === 403) {', '    if (status === 403) {', 'a read that began before a route finished would be drawn over the newer one', MXUI),
    ('matrix.ui-route-does-not-order-reads', 'ui/user/user.js', '    // Anything read before this finishes describes the world before it.\n    matrixSeq += 1;', '', 'a read in flight when a route begins would not be discarded', MXUI),
    ('matrix.ui-tab-for-one-interface', 'ui/user/user.js', '    tabs.hidden = allowed.length < 2;\n    allowed.forEach((id) => {', '    tabs.hidden = false;\n    allowed.forEach((id) => {', 'an account with one interface would be shown a useless second tab', MXUI),
    ('matrix.ui-route-bar-over-matrix', 'ui/user/user.js', "    const bar = !(state.tab === 'av' && (state.surface === 'matrix' || state.surface === 'none'));", '    const bar = true;', 'the Standard Route bar would sit under the Matrix and under the no-routing explanation', MXUI),
    ('matrix.ui-no-routing-shows-standard', 'ui/user/user.js', "      chosen = allowed.indexOf(saved) !== -1 ? saved : (allowed[0] || 'none');", "      chosen = allowed.indexOf(saved) !== -1 ? saved : (allowed[0] || 'standard');", 'an account given neither interface would be shown Standard routing', MXUI),
    ('matrix.ui-fallback-overwrites-preference', 'ui/user/user.js', "      chosen = allowed.indexOf(saved) !== -1 ? saved : (allowed[0] || 'none');", "      chosen = allowed.indexOf(saved) !== -1 ? saved : (allowed[0] || 'none');\n      remember(REMEMBERED_INTERFACE, chosen);", "an automatic fallback would overwrite the operator's own choice", MXUI),
    ('drag.offline-display-accepts', 'ui/user/user.js', '      : state.destinations.some((d) => d.id === id && d.online !== false)),', '      : state.destinations.some((d) => d.id === id)),', 'a source could be dropped on a display that is not answering', MXUI),
    ('drag.offline-source-draggable', 'ui/user/user.js', "    if (kind === 'source' && item.online !== false) button.draggable = true;", "    if (kind === 'source') button.draggable = true;", 'a source that is not answering could be picked up', MXUI),
    ('drag.enabled-in-matrix', 'ui/user/user.js', "    enabled: () => !state.busy && state.tab === 'av' && state.surface === 'standard',", '    enabled: () => true,', 'a drag would start and land while the Matrix, or a route in flight, was the interface', MXUI),
    ('drag.drop-skips-the-route-function', 'ui/user/user.js', '      return routeSelected();\n    },\n  }) : null;', '      return Promise.resolve();\n    },\n  }) : null;', 'a drop would select and then do nothing instead of ending in the Route operation', MXUI),
    ('drag.drop-keeps-the-drag-alive', 'ui/user/dragdrop.js', '        if (event.preventDefault) event.preventDefault();\n        clear();\n        if (!ok) return Promise.resolve(false);', '        if (event.preventDefault) event.preventDefault();\n        if (!ok) return Promise.resolve(false);', 'a finished drop could be repeated onto another display without picking the source up again', MXUI),
    ('drag.payload-names-the-source', 'ui/user/dragdrop.js', "          event.dataTransfer.setData('text/plain', 'omni-source');", "          event.dataTransfer.setData('text/plain', card.dataset.id);", 'the identity of a source would travel in the drag payload', MXUI),
    ('matrix.ui-denied-loops', 'ui/user/user.js', '      if (matrixDenied) return;\n      matrixDenied = true;', '      matrixDenied = false;', 'a grid the server keeps refusing would be re-read without end', MXUI),
    ('drag.target-marker-never-cleared', 'ui/user/dragdrop.js', "      if (marked) { marked.classList.remove('drop-ok'); marked = null; }", '      if (marked) { marked = null; }', 'a display would keep saying it would take a drop after the drag ended', MXUI),
]


#: What is mutated right now, written before the edit and removed after it.
#:
#: `finally` covers an exception and does not cover the process being killed.
#: An interrupted run used to leave a deliberately defective source in the
#: working tree, one `git add -A` away from committing an inverted security
#: predicate. This is the receipt that lets the next run put it back.
#: Assignment-aware Clear Units, restore semantics and the Clear Units dialog.
INV = ["tests.test_clear_inventory"]
INVUI = ["tests.test_clear_units_js"]
RSEM = ["tests.test_restore_semantics"]
CLEARPROT = ["tests.test_clear_protection", "tests.test_acceptance_lifecycle"]

INVENTORY_MUTATIONS = [
    # ---- the decision is the server's, from current state ----
    ("inv.protection-off", "OmniMatrix_upgrade_server_v7_6y.py",
     "    protect = remote_access_enabled()\n    unremoved = []",
     "    protect = False\n    unremoved = []",
     "Clear Units would remove assigned units while Remote Access is on",
     INV),
    ("inv.protection-honours-the-request", "OmniMatrix_upgrade_server_v7_6y.py",
     "    protect = remote_access_enabled()\n    unremoved = []",
     "    protect = remote_access_enabled() and not (request.get_json(silent=True) or {}).get(\"override\")\n    unremoved = []",
     "a request could switch the protection off by saying so",
     INV),
    ("inv.no-store-lock", "OmniMatrix_upgrade_server_v7_6y.py",
     "        with omni_inventory.hold_assignments(_user_store, _display_groups):\n            with _cache_io_lock, _scan_results_lock:",
     "        with omni_inventory.hold_assignments_if(False, _user_store, _display_groups):\n            with _cache_io_lock, _scan_results_lock:",
     "a grant or a group edit could land between the decision and the removal",
     INV),
    ("inv.no-file-lock", "OmniMatrix_upgrade_server_v7_6y.py",
     "            with _cache_io_lock, _scan_results_lock:\n                units = _load_cache() or []\n                assignments = (omni_inventory.collect",
     "            with omni_inventory.hold_assignments_if(False, _user_store, _display_groups):\n                units = _load_cache() or []\n                assignments = (omni_inventory.collect",
     "a scan could write the inventory in the middle of a clear",
     INV),
    ("inv.assigned-not-kept", "omni_inventory.py",
     "        (kept if (protect and assigned) else clearable).append(unit)",
     "        clearable.append(unit)",
     "an assigned unit would be classified clearable",
     INV),
    ("inv.groups-not-counted", "omni_inventory.py",
     "    for group in groups.all():",
     "    for group in []:",
     "a unit in a Display Group would be cleared",
     INV),
    ("inv.users-not-counted", "omni_inventory.py",
     "    for account in store.list_accounts():",
     "    for account in []:",
     "a unit a User was given would be cleared",
     INV),
    ("inv.displays-not-counted", "omni_inventory.py",
     "        for identity in set(permissions.sources) | set(permissions.destinations):",
     "        for identity in set(permissions.sources):",
     "a display a User was given would be cleared",
     INV),
    ("inv.usb-identity-ignored", "omni_inventory.py",
     '    for key in ("mac", "usb_mac"):',
     '    for key in ("mac",):',
     "a grant naming a unit's USB address would not protect its parent",
     INV),
    ("inv.disabled-account-ignored", "omni_inventory.py",
     "        if account.get(\"role\") == omni_users.ROLE_ADMIN:\n            continue\n",
     "        if account.get(\"role\") == omni_users.ROLE_ADMIN or not account.get(\"enabled\", True):\n            continue\n",
     "a unit held by a disabled account would be cleared and lost when it is re-enabled",
     INV),
    ("inv.direct-access-not-marked", "omni_inventory.py",
     "            if (account.get(\"direct_access\") or {}).get(\"enabled\"):",
     "            if False:",
     "the dialog would not say which holders use a Direct Access link",
     INV),
    ("inv.partial-keeps-the-whole-cache", "OmniMatrix_upgrade_server_v7_6y.py",
     "                    _save_cache(kept)\n",
     "                    _save_cache(units)\n",
     "unassigned units would stay in the inventory after being reported cleared",
     INV),
    ("inv.scan-results-not-trimmed", "OmniMatrix_upgrade_server_v7_6y.py",
     "                    if scan_data:\n                        if not _save_scan_results(_scan_rows_without(",
     "                    if False:\n                        if not _save_scan_results(_scan_rows_without(",
     "scan_results would merge the cleared units straight back in",
     INV),
    ("inv.usb-keep-ignored", "OmniMatrix_upgrade_server_v7_6y.py",
     "            usb_cleared = _usb_extenders.clear_devices(keep=set(assignments))\n",
     "            usb_cleared = _usb_extenders.clear_devices()\n",
     "an assigned standalone USB endpoint would be cleared",
     INV),
    ("inv.cascade-removes-assignments", "OmniMatrix_upgrade_server_v7_6y.py",
     "                cascade = _cascade_clear(identities if protect else None)",
     "                cascade = _cascade_clear(None)",
     "protected units would lose their User and group assignments",
     INV),
    ("inv.audit-names-the-wrong-operation", "OmniMatrix_upgrade_server_v7_6y.py",
     '        "inventory.cleared_unassigned" if protect else "inventory.cleared",',
     '        "inventory.cleared",',
     "a Clear Unassigned Units would be recorded as a full clear",
     INV),
    ("inv.remove-selected-unprotected", "OmniMatrix_upgrade_server_v7_6y.py",
     "    protect = remote_access_enabled()\n    hold = omni_inventory",
     "    protect = False\n    hold = omni_inventory",
     "Remove Selected would be the way around the protection",
     INV),
    ("inv.legacy-clear-route-open", "OmniMatrix_upgrade_server_v7_6y.py",
     "    # Clear matrix cached results and in-memory state\n    if remote_access_enabled():",
     "    # Clear matrix cached results and in-memory state\n    if False:",
     "the older clear route would remove scan results while Remote Access is on",
     INV),
    ("inv.assignments-route-open", "omni_access.py",
     '    "/api/admin/inventory/assignments": (Access.ADMIN, None),',
     '    "/api/admin/inventory/assignments": (Access.AUTHENTICATED, None),',
     "a restricted User could read who holds which device",
     INV),
    ("inv.unreadable-store-reads-empty", "OmniMatrix_upgrade_server_v7_6y.py",
     "                assignments = (omni_inventory.collect(_user_store, _display_groups)\n                               if protect else {})",
     "                try:\n                    assignments = (omni_inventory.collect(_user_store, _display_groups)\n                                   if protect else {})\n                except Exception:\n                    assignments = {}",
     "an unreadable account store would be taken to mean nothing is assigned",
     INV),
    ("inv.locks-in-the-other-order", "omni_inventory.py",
     "    with store._lock:\n        with groups._lock:\n            yield",
     "    with groups._lock:\n        with store._lock:\n            yield",
     "the stores would be locked in an order other code could invert",
     INV),
    # ---- restore: the backup is authoritative for what it covers ----
    ("rsem.removal-ignores-coverage", "omni_backup.py",
     "            if (covered is not None and section.name in covered\n                    and section.name in REMOVED_WHEN_ABSENT and target.exists()):",
     "            if (section.name in REMOVED_WHEN_ABSENT and target.exists()):",
     "a backup that never looked at a section would delete the installation's copy",
     RSEM),
    ("rsem.removal-ignores-the-managed-set", "omni_backup.py",
     "                    and section.name in REMOVED_WHEN_ABSENT and target.exists()):",
     "                    and target.exists()):",
     "a restore would delete machine settings the backup did not carry",
     RSEM),
    ("rsem.never-removes", "omni_backup.py",
     "            if (covered is not None and section.name in covered",
     "            if (False and covered is not None and section.name in covered",
     "a managed record absent from the backup would survive the restore",
     RSEM),
    ("rsem.coverage-not-recorded", "omni_backup.py",
     '        "covered": [section.name for section in SECTIONS],',
     '        "covered": [],',
     "no backup could say what it covered, so nothing would ever be removed",
     RSEM),
    ("rsem.coverage-not-validated", "omni_backup.py",
     "    if covered is not None and (\n            not isinstance(covered, list)",
     "    if False and covered is not None and (\n            not isinstance(covered, list)",
     "a damaged coverage list would be read as covering nothing",
     RSEM),
    # ---- the confirmation and View Assignments ----
    ("invui.unreadable-means-nothing-assigned", "ui/clear-units.js",
     "          return res.ok && body && body.ok ? body : null;",
     "          return body && body.ok ? body : { remote_access: false, units: [] };",
     "an unreadable answer would be read as nothing assigned and fall back to the plain confirmation",
     INVUI),
    ("invui.no-single-flight", "ui/clear-units.js",
     "    if (inFlight) return Promise.resolve(false);\n    inFlight = true;",
     "    inFlight = true;",
     "two dialogs could stack on one inventory",
     INVUI),
    ("invui.guard-leaks-on-failure", "ui/clear-units.js",
     "    }).then(release, function (err) { inFlight = false; throw err; });",
     "    }).then(release);",
     "one failed dialog would disable Clear Units until the page was reloaded",
     INVUI),
    ("invui.legacy-when-remote-on", "ui/clear-units.js",
     "      if (!report.remote_access) return deps.legacyConfirm();",
     "      if (true) return deps.legacyConfirm();",
     "the plain wording would be shown while assigned units are kept",
     INVUI),
    ("invui.destructive-button-default", "ui/clear-units.js",
     "      show(deps, parts.shell);\n      cancel.focus();",
     "      show(deps, parts.shell);\n      go.focus();",
     "Enter would clear while the screen offered Cancel",
     INVUI),
    ("invui.disabled-button-answers", "ui/clear-units.js",
     "      go.addEventListener('click', function () { if (!go.disabled) finish(true); });",
     "      go.addEventListener('click', function () { finish(true); });",
     "a disabled Clear button would still clear",
     INVUI),
    ("invui.lists-unassigned-units", "ui/clear-units.js",
     "    var assigned = (report.units || []).filter(function (row) { return row.assigned; });",
     "    var assigned = report.units || [];",
     "View Assignments would list units nothing is holding",
     INVUI),
    ("invui.escape-closes-everything", "ui/clear-units.js",
     "        if (event.key === 'Escape' && isTop(deps, parts.shell)) finish(false);",
     "        if (event.key === 'Escape') finish(false);",
     "Escape on the assignments list would also dismiss the question underneath it",
     INVUI),
    ("invui.markup-from-names", "ui/clear-units.js",
     "    if (text !== undefined && text !== null) node.textContent = String(text);",
     "    if (text !== undefined && text !== null) node.innerHTML = String(text);",
     "a hostile device or account name would be rendered as markup",
     INVUI),
    ("invui.summary-never-expands", "ui/admin/users.js",
     "      if (state.assignmentsExpanded.has(id)) state.assignmentsExpanded.delete(id);\n      else state.assignmentsExpanded.add(id);\n      paintAssignment(cell, id);",
     "      paintAssignment(cell, id);",
     "the assignment summary could not be opened to show the names",
     INVUI),
    ("invui.unknown-reads-unassigned", "ui/admin/users.js",
     "      cell.textContent = '—';\n      return;",
     "      cell.textContent = 'Unassigned';\n      return;",
     "a failed read would say a device has no assignments",
     INVUI),
    ("invui.tab-does-not-reread", "ui/admin/users.js",
     "    if (name === 'names') refreshAssignments().catch(() => {});\n",
     "",
     "an assignment made elsewhere would be shown stale",
     INVUI),
    ("invui.refresh-rebuilds-rows", "ui/admin/users.js",
     "    document.querySelectorAll('[data-assign-id]').forEach((cell) => {\n      paintAssignment(cell, cell.dataset.assignId);\n    });",
     "    renderAliases();",
     "re-reading would discard a name being typed",
     INVUI),
    ("invui.clear-event-unlabelled", "ui/admin/users.js",
     "    'inventory.cleared_unassigned': 'Unassigned units cleared',\n",
     "",
     "the activity list would show an internal event name",
     INVUI),
    ("invui.protect-note-always-shown", "ui/settings.js",
     "    if (note) note.hidden = !(state && state.enabled);",
     "    if (note) note.hidden = false;",
     "Settings would claim protection while Remote Access is off",
     INVUI),
]
MUTATIONS += INVENTORY_MUTATIONS

# ---- a scan that began before a clear cannot hand back what was cleared ----
SCANGEN = ["tests.test_clear_scan_generation"]
SCAN_GENERATION_MUTATIONS = [
    ("scangen.ignores-the-generation", "OmniMatrix_upgrade_server_v7_6y.py",
     "        if _inventory_generation_now() != scan_generation:",
     "        if False:",
     "a scan in flight during Clear Units would write the cleared units straight back",
     SCANGEN),
    ("scangen.clear-never-moves-it-on", "OmniMatrix_upgrade_server_v7_6y.py",
     "                    _bump_inventory_generation()\n                everything = not kept",
     "                    pass\n                everything = not kept",
     "a clear would not mark the scans already running as stale",
     SCANGEN),
    ("scangen.moves-on-without-removing", "OmniMatrix_upgrade_server_v7_6y.py",
     "                if clearable:\n                    # A scan already in flight",
     "                if True:\n                    # A scan already in flight",
     "a clear that removed nothing would throw away a scan that was still valid",
     SCANGEN),
    ("scangen.write-outside-the-locks", "OmniMatrix_upgrade_server_v7_6y.py",
     "    with _cache_io_lock, _scan_results_lock:\n        if _inventory_generation_now() != scan_generation:",
     "    if True:\n        if _inventory_generation_now() != scan_generation:",
     "the check and the scan's write would no longer be ordered against a clear",
     SCANGEN),
    ("scangen.discard-not-flagged", "OmniMatrix_upgrade_server_v7_6y.py",
     '            return jsonify({"ok": True, "discarded": True,',
     '            return jsonify({"ok": True, "discarded": False,',
     "a discarded scan would be indistinguishable from one that was merged",
     SCANGEN),
    ("scangen.discard-reports-additions", "OmniMatrix_upgrade_server_v7_6y.py",
     '                            "omitted": omitted, "added": 0,',
     '                            "omitted": omitted, "added": len(raw_units),',
     "the operator would be told units were added that are not in the inventory",
     SCANGEN),
    ("scangen.page-says-found", "ui/index.html",
     "    $('#scan_status').textContent = data.discarded\n      ? ",
     "    $('#scan_status').textContent = false\n      ? ",
     "the page would report 'found' for a scan whose result was thrown away",
     SCANGEN),
]
MUTATIONS += SCAN_GENERATION_MUTATIONS

SELECTED_STATE_MUTATIONS = [
    ('selected.tab-back-to-tint', 'ui/remote.css',
     '.mode-tabs .tab[aria-selected="true"] {\n  background-color: var(--action-primary);',
     '.mode-tabs .tab[aria-selected="true"] {\n  background-color: var(--selected-fill);',
     'the selected Routing/Matrix tab would be a 12-16% tint again, deltaE 8 from the unselected one',
     MXUI),
    ('selected.mode-back-to-tint', 'ui/remote.css',
     '.mx-modebtn.active {\n  background-color: var(--action-primary);',
     '.mx-modebtn.active {\n  background-color: var(--selected-fill);',
     'the selected AV/Video/Audio button would be a barely visible tint',
     MXUI),
    ('selected.tab-label-in-accent', 'ui/remote.css',
     '  color: var(--on-accent, #101418);\n  border-color: var(--action-primary);\n  box-shadow: inset 0 0 0 1px var(--action-primary);\n}\n/* [hidden]',
     '  color: var(--action-primary);\n  border-color: var(--action-primary);\n  box-shadow: inset 0 0 0 1px var(--action-primary);\n}\n/* [hidden]',
     "the selected tab's label would be the accent on an accent fill, unreadable",
     MXUI),
    ('selected.mode-label-literal-white', 'ui/remote.css',
     '.mx-modebtn.active {\n  background-color: var(--action-primary);\n  color: var(--on-accent, #101418);',
     '.mx-modebtn.active {\n  background-color: var(--action-primary);\n  color: #ffffff;',
     'a fixed white label ignores the readable foreground the palette computed for its accent',
     MXUI),
    ('selected.hover-by-brightness', 'ui/remote.css',
     '.mode-tabs .tab:hover,\n.mx-modebtn:hover { background-image: linear-gradient(var(--control-hover-veil), var(--control-hover-veil)); }',
     '.mode-tabs .tab:hover,\n.mx-modebtn:hover { filter: brightness(1.1); }',
     'hover would lighten a light control and a dark one alike, washing out in one palette',
     MXUI),
    ('selected.pressed-state-removed', 'ui/remote.css',
     '.mode-tabs .tab:active,\n.mx-modebtn:active {\n  background-image:',
     '.mode-tabs .tab:pressed-never,\n.mx-modebtn:pressed-never {\n  background-image:',
     'a pressed tab or mode button would give no feedback',
     MXUI),
]
MUTATIONS += SELECTED_STATE_MUTATIONS

# ---- the adapter list is remembered briefly, and never used to decide --------
ADAPTERCACHE = ["tests.test_adapter_cache"]
ADAPTER_CACHE_MUTATIONS = [
    ("adaptercache.never-expires", "omni_adapters.py",
     '        if found is None or time.monotonic() - _cache["at"] >= ttl:\n            return None',
     '        if found is None:\n            return None',
     "a network change would never be seen: the first answer would be served for the life of the process",
     ADAPTERCACHE),
    ("adaptercache.invalidate-does-nothing", "omni_adapters.py",
     '        _cache["generation"] += 1\n        _cache["found"] = None',
     '        pass',
     "turning Remote Access on would be decided from an adapter list that may be 15 seconds old",
     ADAPTERCACHE),
    ("adaptercache.invalidate-keeps-the-answer", "omni_adapters.py",
     '        _cache["generation"] += 1\n        _cache["found"] = None',
     '        _cache["generation"] += 1',
     "an invalidation would be recorded but the old answer would still be served",
     ADAPTERCACHE),
    ("adaptercache.remembers-an-empty-answer", "omni_adapters.py",
     '            if found and _cache["generation"] == generation:',
     '            if _cache["generation"] == generation:',
     "one PowerShell timeout would leave the machine with no adapters for the whole window",
     ADAPTERCACHE),
    ("adaptercache.keeps-a-scan-that-overlapped-an-invalidation", "omni_adapters.py",
     '            if found and _cache["generation"] == generation:',
     '            if found:',
     "an enumeration that began before an invalidation would be remembered as if it were fresh",
     ADAPTERCACHE),
    ("adaptercache.no-single-flight", "omni_adapters.py",
     '        # Someone may have finished while this caller waited for the lock.\n        hit = _remembered()',
     '        hit = None',
     "a burst of requests would start one PowerShell child each instead of sharing one",
     ADAPTERCACHE),
    ("adaptercache.lifetime-unbounded", "omni_adapters.py",
     'CACHE_TTL = 15.0',
     'CACHE_TTL = 86400.0',
     "an adapter change would take a day to be seen",
     ADAPTERCACHE),
    ("adaptercache.enable-uses-the-memory", "omni_remote_api.py",
     '        omni_adapters.invalidate_cache()\n        if adapter_id:',
     '        if adapter_id:',
     "Remote Access would be turned on against a remembered adapter list",
     ADAPTERCACHE),
    ("adaptercache.restore-uses-the-memory", "OmniMatrix_upgrade_server_v7_6y.py",
     '    omni_adapters.invalidate_cache()\n    try:\n        adapters = omni_adapters.enumerate_adapters()',
     '    try:\n        adapters = omni_adapters.enumerate_adapters()',
     "start-up would choose where to listen from a remembered list",
     ADAPTERCACHE),
    ("adaptercache.remote-state-ignores-refresh", "omni_remote_api.py",
     '        if request.args.get("refresh") in ("1", "true", "yes"):\n            omni_adapters.invalidate_cache()\n        try:\n            enumerated',
     '        try:\n            enumerated',
     "there would be no way to ask Settings to look at the adapters again",
     ADAPTERCACHE),
    ("adaptercache.adapters-ignores-refresh", "OmniMatrix_upgrade_server_v7_6y.py",
     '    if request.args.get("refresh") in ("1", "true", "yes"):\n        omni_adapters.invalidate_cache()\n    try:\n        res = _adapters_windows',
     '    try:\n        res = _adapters_windows',
     "there would be no way to ask the scan page to look at the adapters again",
     ADAPTERCACHE),
    ("adaptercache.suite-leaves-it-on", "tests/__init__.py",
     'omni_adapters.CACHE_TTL = 0.0',
     'omni_adapters.CACHE_TTL = 15.0',
     "one test's adapters would be answered to the next test",
     ADAPTERCACHE),
]
MUTATIONS += ADAPTER_CACHE_MUTATIONS

# ---- looking again at silent units does not hold a Matrix read up -----------
OFFLINEBG = ["tests.test_offline_refresh_background", "tests.test_offline_routing"]
OFFLINE_BACKGROUND_MUTATIONS = [
    ("offlinebg.read-waits-for-the-probes", "OmniMatrix_upgrade_server_v7_6y.py",
     '        _startup_threads.append(thread)\n        thread.start()\n    except Exception as exc:\n        # No thread: do it here',
     '        _run_offline_refresh(work)\n    except Exception as exc:\n        # No thread: do it here',
     "every Matrix read would again wait out the probes of silent units: 1.2 s for sixteen",
     OFFLINEBG),
    ("offlinebg.thread-not-registered", "OmniMatrix_upgrade_server_v7_6y.py",
     '                                  name="offline-refresh", daemon=True)\n        _startup_threads[:] = [t for t in _startup_threads if t.is_alive()]\n        _startup_threads.append(thread)\n        thread.start()',
     '                                  name="offline-refresh", daemon=True)\n        thread.start()',
     "the look-again could land inside a later test, or after shutdown began, with nothing able to wait for it",
     OFFLINEBG),
    ("offlinebg.claim-never-released", "omni_liveness.py",
     '            finally:\n                self._refresh_gate.release()\n            return stale',
     '            finally:\n                pass\n            return stale',
     "after the first batch no silent unit would ever be looked at again",
     OFFLINEBG),
    ("offlinebg.every-reader-starts-a-batch", "omni_liveness.py",
     '        if not self._refresh_gate.acquire(blocking=False):\n            return None\n\n        def work():',
     '        self._refresh_gate.acquire(blocking=False)\n\n        def work():',
     "twelve open pages would issue 192 probes for sixteen units",
     OFFLINEBG),
    ("offlinebg.failure-leaves-the-claim", "OmniMatrix_upgrade_server_v7_6y.py",
     '        log.info("[OFFLINE] background look-again unavailable: %s", type(exc).__name__)\n        _run_offline_refresh(work)',
     '        log.info("[OFFLINE] background look-again unavailable: %s", type(exc).__name__)',
     "a machine that cannot start a thread would hold the claim for ever and look at nothing again",
     OFFLINEBG),
]
MUTATIONS += OFFLINE_BACKGROUND_MUTATIONS

# ---- pictures never take every connection a route or a Matrix read needs ----
PREVIEWCAP = ["tests.test_user_preview_refresh_js"]
PREVIEW_CAP_MUTATIONS = [
    ("previewcap.no-cap", "ui/user/preview-refresh.js",
     "        if (active < MAX_CONCURRENT_FETCHES) run(); else waiting.push(run);",
     "        run();",
     "fourteen pictures would fill every connection and a Matrix read would wait 1.8 s behind them",
     PREVIEWCAP),
    ("previewcap.cap-is-six", "ui/user/preview-refresh.js",
     "  var MAX_CONCURRENT_FETCHES = 3;",
     "  var MAX_CONCURRENT_FETCHES = 6;",
     "the pictures would again be able to take every connection the browser allows",
     PREVIEWCAP),
    ("previewcap.slot-never-released", "ui/user/preview-refresh.js",
     "              active -= 1;\n              var next = waiting.shift();",
     "              var next = waiting.shift();",
     "after three pictures nothing would ever be fetched again",
     PREVIEWCAP),
    ("previewcap.queue-not-in-order", "ui/user/preview-refresh.js",
     "              var next = waiting.shift();",
     "              var next = waiting.pop();",
     "the sources asked for first would be the last to get a picture",
     PREVIEWCAP),
    ("previewcap.stop-strands-the-queue", "ui/user/preview-refresh.js",
     "        queued.forEach(function (run) { run(); });",
     "",
     "leaving the page would leave requests waiting for a turn that never comes",
     PREVIEWCAP),
    ("previewcap.stopped-still-fetches", "ui/user/preview-refresh.js",
     "            .then(function () { return stopped ? NOT_FETCHED : work(); })",
     "            .then(function () { return work(); })",
     "a page that was left, or a session that ended, would still fetch every queued picture",
     PREVIEWCAP),
    ("previewcap.failure-keeps-the-turn", "ui/user/preview-refresh.js",
     "            .catch(function () { return NOT_FETCHED; })\n            .then(function (result) {\n              active -= 1;",
     "            .then(function (result) {\n              active -= 1;",
     "one picture that failed would hold its turn and the rest would never be fetched",
     PREVIEWCAP),
]
MUTATIONS += PREVIEW_CAP_MUTATIONS

IN_FLIGHT = ROOT / ".mutation-in-flight"


def repair_interrupted_run() -> None:
    """Undo whatever a killed run left behind, before doing anything else."""
    if not IN_FLIGHT.exists():
        return
    try:
        record = json.loads(IN_FLIGHT.read_text(encoding="utf-8"))
    except ValueError:
        print("  the in-flight record is unreadable; restore by hand")
        raise SystemExit(2)
    path = ROOT / record["file"]
    with open(path, "r", encoding="utf-8", newline="") as handle:
        current = handle.read()
    if current != record["original"]:
        with open(path, "w", encoding="utf-8", newline="") as handle:
            handle.write(record["original"])
        print("  repaired %s, left mutated by an interrupted run (%s)"
              % (record["file"], record["id"]))
    else:
        print("  %s was already intact" % record["file"])
    IN_FLIGHT.unlink()


#: Whole output of every mutation, kept on disk. A runner that keeps only the
#: summary cannot tell "the tests caught it" from "the tests never ran".
LOG_DIR = os.environ.get("OMNI_MUTATION_LOGS")


def _keep(label, output):
    if not LOG_DIR:
        return
    os.makedirs(LOG_DIR, exist_ok=True)
    safe = "".join(c if c.isalnum() or c in "-._" else "_" for c in label)
    with open(os.path.join(LOG_DIR, safe + ".txt"), "w", encoding="utf-8") as handle:
        handle.write(output)


def run(suites, env):
    """Run these suites and return (failed, full output)."""
    proc = subprocess.run(
        [sys.executable, "-m", "unittest", "-q", *suites],
        cwd=ROOT, env=env, capture_output=True, text=True,
        timeout=int(os.environ.get("OMNI_MUTATE_TIMEOUT", "900")))
    output = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode != 0, output


def main(argv):
    wanted = argv[1:] if len(argv) > 1 else []
    selected = [m for m in MUTATIONS
                if not wanted or any(w in m[0] for w in wanted)]
    # Independent copies of the tree can each take a share: OMNI_MUTATION_SHARD=i/n.
    shard = os.environ.get("OMNI_MUTATION_SHARD")
    if shard:
        index_, total_ = (int(x) for x in shard.split("/"))
        selected = [m for n, m in enumerate(selected) if n % total_ == index_]

    env = dict(os.environ, OMNI_SMOKE="1", PYTHONDONTWRITEBYTECODE="1")

    repair_interrupted_run()
    print("Baseline: the suites must pass before any of this means anything.")
    suites = sorted({s for m in selected for s in m[5]})
    failed, output = run(suites, env)
    _keep("_baseline", output)
    if failed:
        print(output[-3000:])
        print("BASELINE FAILED - nothing below can be interpreted.")
        return 2
    print("  baseline OK (%d suites)\n" % len(suites))

    caught, survived = [], []
    for index, (mid, filename, anchor, replacement, harm, mut_suites) in \
            enumerate(selected, 1):
        path = ROOT / filename
        # newline="" both ways: read_text/write_text would translate line
        # endings and rewrite every line of the file on restore.
        with open(path, "r", encoding="utf-8", newline="") as handle:
            original = handle.read()
        # The sources use CRLF. Anchors here are written with newlines,
        # so translate them to whatever this file uses rather than
        # rewriting every line of it.
        eol = "\r\n" if "\r\n" in original else "\n"
        anchor = anchor.replace("\n", eol)
        replacement = replacement.replace("\n", eol)
        count = original.count(anchor)
        if count != 1:
            print("%2d/%d  %-34s ANCHOR x%d - the code moved; fix the harness"
                  % (index, len(selected), mid, count))
            survived.append((mid, harm, "anchor did not match"))
            continue

        started = time.time()
        # The receipt goes down BEFORE the edit, so a kill between the two
        # leaves a record of a change that was never made -- which repairs to
        # a no-op -- rather than a change with no record.
        IN_FLIGHT.write_text(json.dumps(
            {"id": mid, "file": filename, "original": original}),
            encoding="utf-8")
        try:
            with open(path, "w", encoding="utf-8", newline="") as handle:
                handle.write(original.replace(anchor, replacement, 1))
            failed, output = run(mut_suites, env)
            _keep(mid, output)
        finally:
            # Always, on any exception, so a broken run never leaves a
            # deliberately defective file behind.
            with open(path, "w", encoding="utf-8", newline="") as handle:
                handle.write(original)
            IN_FLIGHT.unlink(missing_ok=True)

        elapsed = time.time() - started
        if failed:
            first = ""
            for line in output.splitlines():
                if line.startswith(("FAIL:", "ERROR:")):
                    first = line.strip()
                    break
            caught.append((mid, first))
            print("%2d/%d  %-34s caught  %5.1fs  %s"
                  % (index, len(selected), mid, elapsed, first[:60]))
        else:
            survived.append((mid, harm, output.strip().splitlines()[-1:]))
            print("%2d/%d  %-34s SURVIVED %4.1fs  %s"
                  % (index, len(selected), mid, elapsed, harm))

    print()
    print("=" * 70)
    print("  caught   : %d" % len(caught))
    print("  survived : %d" % len(survived))
    for mid, harm, _detail in survived:
        print("    %-34s %s" % (mid, harm))
    print("=" * 70)
    return 0 if not survived else 1


# Mutations kept beside the work they belong to (restore and sign-in sessions).
# Found by this file's own location, not by the caller's import path: a test loads
# the harness from its file path, where `tools/` is not on sys.path.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mutations_night_a import NIGHT_A_MUTATIONS      # noqa: E402
MUTATIONS += NIGHT_A_MUTATIONS
from mutations_cache_race import CACHE_RACE_MUTATIONS    # noqa: E402
MUTATIONS += CACHE_RACE_MUTATIONS
from mutations_restore_focus import RESTORE_FOCUS_MUTATIONS    # noqa: E402
MUTATIONS += RESTORE_FOCUS_MUTATIONS
from mutations_sticky_corner import STICKY_CORNER_MUTATIONS    # noqa: E402
MUTATIONS += STICKY_CORNER_MUTATIONS
from mutations_local_port import LOCAL_PORT_MUTATIONS    # noqa: E402
MUTATIONS += LOCAL_PORT_MUTATIONS
from mutations_display_group import DISPLAY_GROUP_MUTATIONS    # noqa: E402
MUTATIONS += DISPLAY_GROUP_MUTATIONS
from mutations_request_bodies import REQUEST_BODY_MUTATIONS    # noqa: E402
MUTATIONS += REQUEST_BODY_MUTATIONS
from mutations_cache_lock import CACHE_LOCK_MUTATIONS    # noqa: E402
MUTATIONS += CACHE_LOCK_MUTATIONS
from mutations_cache_backup import CACHE_BACKUP_MUTATIONS    # noqa: E402
MUTATIONS += CACHE_BACKUP_MUTATIONS
from mutations_restore_cache import RESTORE_CACHE_MUTATIONS    # noqa: E402
MUTATIONS += RESTORE_CACHE_MUTATIONS


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
