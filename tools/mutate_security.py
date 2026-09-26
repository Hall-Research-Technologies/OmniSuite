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
DIRECT = ["tests.test_direct_access", "tests.test_remote_browser_flow",
          "tests.test_security_review"]

#: (id, file, anchor, replacement, what the defect would be, suites)
MUTATIONS = [
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
    ("perm.multiview-capability-is-enough", "omni_users.py",
     '        key = multiview_key(decoder_identity, name)\n'
     '        return bool(key) and key in self.multiviews',
     '        key = multiview_key(decoder_identity, name)\n'
     '        return True',
     "any saved preset would be operable once the capability is held", USERS),
    ("perm.multiview-decoder-only", "omni_users.py",
     '        key = multiview_key(decoder_identity, name)\n'
     '        return bool(key) and key in self.multiviews',
     '        return any(str(k).split("/")[0] == str(decoder_identity).lower()\n'
     '                   for k in self.multiviews)',
     "approval of one preset would approve every preset on that decoder",
     USERS),
    ("perm.source-any", "omni_users.py",
     '        return _normalise_identity(identity) in self.sources',
     '        return True',
     "every source would be permitted", USERS),
    ("perm.destination-any", "omni_users.py",
     '        return _normalise_identity(identity) in self.destinations',
     '        return True',
     "every destination would be permitted", USERS),

    # ---- sessions ----------------------------------------------------
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
    ("tls.direct-cookie-never-secure", "omni_remote_api.py",
     '            httponly=True, samesite="Lax",\n'
     '            secure=request.is_secure, path="/")\n'
     '        return response\n\n    @app.route("/api/admin/devices"',
     '            httponly=True, samesite="Lax",\n'
     '            secure=False, path="/")\n'
     '        return response\n\n    @app.route("/api/admin/devices"',
     "a Direct Access session cookie would be sent over plaintext",
     REMOTE + ["tests.test_remote_browser_flow"]),
    ("tls.plaintext-without-asking", "omni_remote_api.py",
     'if transport == "http" and not data.get("accept_insecure"):',
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
    ("relock.multiview-no-recheck", "omni_remote_api.py",
     '                refusal = _revalidate_multiview(\n'
     '                    identity, name, "/api/user/multiview_show")\n'
     '                if refusal is not None:\n'
     '                    return refusal\n',
     '',
     "an approval withdrawn while queued would still be shown", RELOCK),

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
    ("direct.session-method-ignored", "omni_users.py",
     '        self.method = method if method in SESSION_METHODS else BY_PASSWORD',
     '        self.method = BY_PASSWORD',
     "a link session would be indistinguishable from a password one, and the "
     "administrative bar would never apply", DIRECT),
]


#: What is mutated right now, written before the edit and removed after it.
#:
#: `finally` covers an exception and does not cover the process being killed.
#: An interrupted run used to leave a deliberately defective source in the
#: working tree, one `git add -A` away from committing an inverted security
#: predicate. This is the receipt that lets the next run put it back.
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


def run(suites, env):
    """Run these suites and return (failed, full output)."""
    proc = subprocess.run(
        [sys.executable, "-m", "unittest", "-q", *suites],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=900)
    output = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode != 0, output


def main(argv):
    wanted = argv[1:] if len(argv) > 1 else []
    selected = [m for m in MUTATIONS
                if not wanted or any(w in m[0] for w in wanted)]

    env = dict(os.environ, OMNI_SMOKE="1", PYTHONDONTWRITEBYTECODE="1")

    repair_interrupted_run()
    print("Baseline: the suites must pass before any of this means anything.")
    suites = sorted({s for m in selected for s in m[5]})
    failed, output = run(suites, env)
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


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
