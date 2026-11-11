"""Mutations for what a restore does to sessions, what a sign-in does when the account
changes under it, and the bounds on the session table.

Kept beside the harness rather than inside it: mutate_security.py is long, and a
block that belongs to one piece of work reads better in one place. It is imported by
the harness and appended to MUTATIONS, so `python tools/mutate_security.py nighta.`
runs exactly these.

Each entry is (id, file, anchor, replacement, harm, suites). The anchor must occur
exactly once; the harness reports one that does not, and does not count it caught.
"""
RSESS = ["tests.test_restore_sessions"]
RFAIL = ["tests.test_restore_failures"]
RACES = ["tests.test_session_races"]
BOUNDS = ["tests.test_session_bounds", "tests.test_direct_session_bounds"]
RJS = ["tests.test_restore_js"]
SERVER = "OmniMatrix_upgrade_server_v7_6y.py"

NIGHT_A_MUTATIONS = [
    # ---- a restore ends the sign-ins it replaced, and only those ----
    ("nighta.restore-keeps-replaced-passwords", "omni_users.py",
     '                    elif now[0] != held[0]:\n                        reason = "password"',
     '                    elif False:\n                        reason = "password"',
     "a restore that replaced an account's password would leave its old sessions signed in",
     RSESS),
    ("nighta.restore-keeps-replaced-links", "omni_users.py",
     '                        if now[3] != held[3]:\n                            reason = "link"',
     '                        if False:\n                            reason = "link"',
     "a restore that replaced a Direct Access link would leave the old link's session open",
     RSESS),
    ("nighta.restore-keeps-role-changes", "omni_users.py",
     '                    if now[1] != held[1]:\n                        reason = "role"',
     '                    if False:\n                        reason = "role"',
     "an account promoted by a restore would keep the sessions it had as a user",
     RSESS),
    ("nighta.restore-signs-everybody-out", "omni_users.py",
     '                if reason:\n                    self._sessions.pop(token, None)\n                    reasons[reason] += 1',
     '                if True:\n                    self._sessions.pop(token, None)\n                    reasons[reason or "role"] += 1',
     "a restore that changed nothing about an account would still sign it out",
     RSESS),
    ("nighta.restore-reads-the-table-without-reconciling", SERVER,
     '        session_report = _user_store.reload_from_disk()\n',
     '        _user_store._load()\n',
     "the restore would swap the accounts and leave every session as it was",
     RSESS),
    ("nighta.failed-reload-keeps-sessions", SERVER,
     '        ended = _user_store.revoke_every_session()',
     '        ended = 0',
     "a restore the process could not load would leave people signed in against an unknown table",
     RSESS + RFAIL),
    # ---- the restore, a failed restore and an export are each on the record ----
    ("nighta.restore-not-recorded", SERVER,
     '    _audit_restore("backup.restored", applied, session_report)\n',
     '    pass\n',
     "a restore that replaced every account would leave no trace in Activity",
     RSESS),
    ("nighta.failed-restore-not-recorded", SERVER,
     '            _user_store.audit("backup.restore_failed",',
     '            (lambda *a, **k: None)("backup.restore_failed",',
     "a restore that failed and was rolled back would leave no trace in Activity",
     RFAIL),
    ("nighta.export-not-recorded", SERVER,
     '        _user_store.audit("backup.exported", actor=getattr(g, "principal", None),',
     '        (lambda *a, **k: None)("backup.exported", actor=getattr(g, "principal", None),',
     "making a copy of every credential on the machine would leave no trace in Activity",
     RSESS),
    # ---- the confirmation says who is affected ----
    ("nighta.impact-forgets-the-removed", "omni_backup.py",
     '    removed = sorted(name for uid, (name, _fp) in current.items()\n                     if uid not in incoming)',
     '    removed = sorted(name for uid, (name, _fp) in current.items()\n                     if False)',
     "the confirmation would not name the accounts a restore removes",
     RSESS),
    ("nighta.impact-forgets-the-replaced", "omni_backup.py",
     '                      if uid in incoming and incoming[uid][1] != fp)',
     '                      if False)',
     "the confirmation would not name the accounts whose sign-ins a restore ends",
     RSESS),
    ("nighta.impact-applies-to-everything", "omni_backup.py",
     '    applies = ("accounts" in sections',
     '    applies = (True or "accounts" in sections',
     "a backup that says nothing about accounts would be described as replacing them",
     RSESS),
    ("nighta.inspect-omits-the-impact", SERVER,
     '                    "summary": omni_backup.summarise(payload, header),\n                    "account_impact": _restore_account_impact(payload)})',
     '                    "summary": omni_backup.summarise(payload, header)})',
     "the confirmation would be built without knowing which accounts are affected",
     RSESS),
    ("nighta.confirmation-drops-the-signout-sentence", "ui/settings.js",
     "  if (removed.length) {\n    lines.push('⚠ Anyone signed in as a removed account",
     "  if (false) {\n    lines.push('⚠ Anyone signed in as a removed account",
     "the confirmation would name the removed accounts and not say what happens to anyone signed in as them",
     RJS),
    ("nighta.confirmation-never-shows-the-impact", "ui/settings.js",
     "  accountImpactLines(summary.account_impact).reverse()\n    .forEach((line) => lines.unshift(line));",
     "  void 0;",
     "the dialog would be built and the account warning never put in it",
     RJS),
    ("nighta.result-hides-ended-sign-ins", "ui/settings.js",
     "  if (result.sessions_ended) {",
     "  if (false) {",
     "the result screen would not say how many people the restore signed out",
     RJS),
    # ---- a sign-in that straddles a change to its account ----
    ("nighta.signin-ignores-a-changed-password", "omni_users.py",
     "                if (live is None or not live.enabled\n                        or live.password_hash != stored):",
     "                if (live is None or not live.enabled):",
     "a password replaced during a sign-in's check would not stop the session it opens",
     RACES),
    ("nighta.signin-ignores-a-disabled-account", "omni_users.py",
     "                if (live is None or not live.enabled\n                        or live.password_hash != stored):",
     "                if (live is None\n                        or live.password_hash != stored):",
     "an account switched off during a sign-in's check would be told it had succeeded",
     RACES),
    ("nighta.link-open-skips-the-recheck", "omni_remote_api.py",
     "            if store.resolve_direct_token(token) is None:\n                store.revoke(session.token)",
     "            if False:\n                store.revoke(session.token)",
     "a link replaced while it is being opened would leave the old link's session behind",
     RACES),
    # ---- the session table is bounded ----
    ("nighta.password-sessions-unbounded", "omni_users.py",
     "            cap = MAX_DIRECT_SESSIONS if direct else MAX_PASSWORD_SESSIONS",
     "            cap = MAX_DIRECT_SESSIONS if direct else 10 ** 9",
     "signing in repeatedly with a valid password would grow the session table without limit",
     BOUNDS),
    ("nighta.expired-sessions-linger", "omni_users.py",
     "            for stale in [t for t, s in self._sessions.items() if s.expired()]:\n                self._sessions.pop(stale, None)\n            direct = session.from_direct_link",
     "            direct = session.from_direct_link",
     "expired sessions would sit in memory until somebody happened to count them",
     BOUNDS),
    ("nighta.the-ceiling-counts-both-kinds-together", "omni_users.py",
     "                    if s.user_id == user_id and s.from_direct_link == direct]",
     "                    if s.user_id == user_id]",
     "a wall panel's link sessions would be dropped to make room for a person's browsers",
     BOUNDS),
]


# ---- the closed restore decision: an administrator stays signed in only while the
# account is security-equivalent, and permissions are part of that ----
NIGHT_A_MUTATIONS += [
    ("nighta.restore-keeps-sessions-after-a-permission-change", "omni_users.py",
     '                    elif now[4] != held[4]:\n                        reason = "permissions"',
     '                    elif False:\n                        reason = "permissions"',
     "an account whose authorisation a restore changed would stay signed in on the old grant",
     RSESS),
    ("nighta.fingerprint-forgets-destinations", "omni_users.py",
     "                 tuple(sorted(perms.destinations)), tuple(sorted(perms.groups)),",
     "                 tuple(sorted(perms.groups)),",
     "reducing the displays an account may route would no longer count as a change",
     RSESS),
    ("nighta.restore-signs-out-an-unchanged-administrator", "omni_users.py",
     "                    now = self._fingerprint(account)\n                    if now[1] != held[1]:",
     "                    now = self._fingerprint(account)\n                    if account.is_admin:\n                        reason = \"role\"\n                    if now[1] != held[1]:",
     "a restore would log out an administrator whose account did not change at all",
     RSESS),
]


# ---- plaintext remote access needs the boolean true, not any truthy value ----
NIGHT_A_MUTATIONS += [
    ("nighta.insecure-consent-is-any-truthy-value", "omni_remote_api.py",
     '        if transport == "http" and data.get("accept_insecure") is not True:',
     '        if transport == "http" and not data.get("accept_insecure"):',
     "the string \"false\" would switch sign-in details to plaintext",
     ["tests.test_remote_http_default"]),
]

# ---- Lock Headers on the A/V Matrix pins at the scrollport edge, corner included ----
STICKY = ["tests.test_matrix_sticky_offsets"]
NIGHT_A_MUTATIONS += [
    ("nighta.matrix-corner-does-not-pin-vertically", "ui/matrix/matrix.css",
     "  top:calc(-1 * var(--mx-pad, 8px)); \n  z-index:11;",
     "  z-index:11;",
     "the corner scrolls away while the header row stays and labels overpaint the column headers",
     STICKY),
    ("nighta.matrix-header-pins-at-zero", "ui/matrix/matrix.css",
     "  top:calc(-1 * var(--mx-pad, 8px)); \n  z-index:10; ",
     "  top:0; \n  z-index:10; ",
     "the grid scrolls through the container's padding above the pinned header row",
     STICKY),
]
