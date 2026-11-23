#!/usr/bin/env python3
"""Break each predicate the User USB Matrix depends on, and require the tests to notice.

    python tools/mutate_user_usb.py [name ...]

An assertion nobody has watched fail is an assertion nobody has verified. Each mutation below edits one
source file in place, runs the named test modules, and restores the file in a `finally` -- so an
interrupted run never leaves a deliberately defective source behind. Anchors are written with newlines
and translated to the file's own line endings (the sources are CRLF; an anchor written with newlines
would match nothing, and rewriting a file to fit the anchor would turn every mutation into a whole-file
diff).

A mutation that SURVIVES is investigated, never waved through: a real gap, a test gap, or a redundant
control, and which one has to be established rather than assumed.

Each suite's whole output is kept under evidence/, not just its summary line.
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
API = "omni_remote_api.py"
CELLS = "omni_usb_cells.py"
PROJ = "omni_projection.py"
SERVER_TESTS = ["tests.test_user_usb_matrix"]
PARITY = ["tests.test_user_usb_matrix", "tests.test_usb_admin_user_parity"]
JS = "node tests/user_usb_matrix_test.js"

# (name, file, anchor, replacement, test command(s), why it matters)
MUTATIONS = [
    ("cells.cross-network-ignored", CELLS,
     '            can_act = bool(cap.get("enabled")) and not subnet_blocked and not over_limit\n',
     '            can_act = not subnet_blocked and not over_limit\n',
     SERVER_TESTS, "a cell the server itself disables (different USB network) would be offered"),
    ("cells.peer-limit-ignored", CELLS,
     '            over_limit = not routed and count >= limit\n',
     '            over_limit = False\n',
     SERVER_TESTS, "a sixth device would be offered to a computer that already holds five"),
    ("cells.management-subnet-ignored", CELLS,
     '            subnet_blocked = (not udp_cell and not routed\n'
     '                              and _different_management_subnet(rex.get("ip"), lex.get("ip")))\n',
     '            subnet_blocked = False\n',
     SERVER_TESTS, "the Admin page's management-address rule would be dropped from the user matrix"),
    ("cells.limit-applied-to-routed-cells", CELLS,
     '            over_limit = not routed and count >= limit\n',
     '            over_limit = count >= limit\n',
     SERVER_TESTS, "a connected dot could no longer be removed once its host is at the limit"),
    ("cells.network-rule-applied-to-routed-cells", CELLS,
     '            subnet_blocked = (not udp_cell and not routed\n'
     '                              and _different_management_subnet(rex.get("ip"), lex.get("ip")))\n',
     '            subnet_blocked = (not udp_cell\n'
     '                              and _different_management_subnet(rex.get("ip"), lex.get("ip")))\n',
     SERVER_TESTS, "a connected dot across management subnets could no longer be removed, unlike the Admin page"),
    ("parity.sees-a-divergence-from-the-admin-page", CELLS,
     '            over_limit = not routed and count >= limit\n',
     '            over_limit = not routed and count > limit\n',
     ["tests.test_usb_admin_user_parity.ParityTests.test_the_integrated_peer_limit_boundary_agrees"],
     "the user matrix would drift from the Admin page by one device and only the parity test could say so"),
    ("route.refusal-skipped", API,
     '                if not cell["can_act"]:\n',
     '                if False:\n',
     SERVER_TESTS, "a cross-network route would reach the pair path"),
    ("route.computer-authorisation-dropped", API,
     '            if computer not in allowed["computers"]:\n',
     '            if False:\n',
     SERVER_TESTS, "a user could route a computer they were not given"),
    ("route.peripheral-authorisation-dropped", API,
     '            if peripherals not in allowed["peripherals"]:\n',
     '            if False:\n',
     SERVER_TESTS, "a user could route a device they were not given"),
    ("route.feature-check-dropped", API,
     '            if not account.permissions.has_feature(omni_access.FEATURE_USB):\n                return "missing_feature:usb"\n            # Both ends',
     '            if False:\n                return "missing_feature:usb"\n            # Both ends',
     SERVER_TESTS, "an account without USB could route"),
    ("route.revalidation-after-wait-dropped", API,
     '                # The wait is over and the next statement reaches hardware.\n'
     '                refusal = _revalidate(_check, "/api/user/usb_route")\n'
     '                if refusal is not None:\n'
     '                    return refusal\n',
     '                # The wait is over and the next statement reaches hardware.\n',
     SERVER_TESTS, "a permission withdrawn while the request queued would be honoured"),
    ("route.cache-not-dropped-before-read", API,
     '                forget_observation("usb")\n                state = usb_state()\n',
     '                state = usb_state()\n',
     SERVER_TESTS, "the decision would be made on a cached observation, not state read for this operation"),
    ("route.dispatch-integrated-to-standalone", API,
     '                    view = usb_icron_unpair_view if detach else usb_icron_pair_view\n',
     '                    view = usb_unpair_view if detach else usb_pair_view\n',
     SERVER_TESTS, "an integrated endpoint would again be sent down the standalone path (the original 409)"),
    ("route.replace-existing-dropped", API,
     '                        body.update({"makeActive": False, "replaceExisting": True})\n',
     '                        body.update({"makeActive": False, "replaceExisting": False})\n',
     SERVER_TESTS, "moving a device between computers would stop working, unlike the Admin page"),
    ("route.dispatch-path-from-browser", API,
     '                path = cell["path"]\n',
     '                path = data.get("path") or cell["path"]\n',
     SERVER_TESTS, "the browser could choose the control path"),
    ("route.malformed-identity-accepted", API,
     '        if not (_MAC_IDENTITY.match(computer) and _MAC_IDENTITY.match(peripherals)):\n',
     '        if False:\n',
     SERVER_TESTS, "an address would be accepted where an identity is expected"),
    ("route.already-routed-not-short-circuited", API,
     '                if not detach and cell["routed"]:\n',
     '                if False:\n',
     SERVER_TESTS, "a connected pair would be written again"),
    ("route.unverified-reported-as-success", API,
     '        if code == 200:\n            # Accepted, and a fresh read did not agree.\n',
     '        if False:\n            # Accepted, and a fresh read did not agree.\n',
     SERVER_TESTS, "a command accepted but not confirmed would be reported as a failure of the wrong kind"),
    ("route.error-text-not-scrubbed", API,
     '            sentence = (omni_usb_cells.clean_text(raw.get("error"))\n'
     '                        or "That route is not available right now.")\n',
     '            sentence = raw.get("error") or "That route is not available right now."\n',
     SERVER_TESTS, "a device address in a refusal would reach a restricted account"),
    ("route.audit-event-misnamed", API,
     '                store.audit("usb.detach" if detach else "usb.attach",\n',
     '                store.audit("usb.attempt", \n',
     SERVER_TESTS, "the audit event would not name what happened"),
    ("projection.cells-for-invisible-computers", PROJ,
     '        if lex_id in visible_computers:\n            cells.setdefault(rex_id, {})[lex_id] = record["public"]\n',
     '        cells.setdefault(rex_id, {})[lex_id] = record["public"]\n',
     SERVER_TESTS, "a computer the account was not given would be described to it"),
    ("projection.integrated-attachment-lost", PROJ,
     '            owner = routed_to.get(peripheral["id"])\n',
     '            owner = None\n',
     SERVER_TESTS, "an integrated route would be shown as not connected"),
    ("page.blocked-cell-clickable", "ui/user/usb-matrix.js",
     "      if (classes.indexOf('disabled') !== -1) return;\n",
     "",
     JS, "a blocked dot would send a request"),
    ("page.radio-allowed-to-check-itself", "ui/user/usb-matrix.js",
     "      if (event && typeof event.preventDefault === 'function') event.preventDefault();\n",
     "",
     JS, "a dot would look routed before any read said so"),
    ("page.reload-skipped-after-request", "ui/user/usb-matrix.js",
     "        try { await deps.reload(); } catch (err) { /* the next read repairs it */ }\n",
     "",
     JS, "the grid would be painted from an answer, not from a read"),
    ("page.detach-never-sent", "ui/user/usb-matrix.js",
     "var result = await deps.request(computerId, peripheralId, routed);",
     "var result = await deps.request(computerId, peripheralId, false);",
     JS, "a connected dot would try to connect again instead of disconnecting"),
]


def read_raw(path):
    return (ROOT / path).read_bytes()


def mutate(raw: bytes, anchor: str, replacement: str) -> bytes:
    text = raw.decode("utf-8")
    crlf = "\r\n" in text
    a = anchor.replace("\n", "\r\n") if crlf else anchor
    r = replacement.replace("\n", "\r\n") if crlf else replacement
    if text.count(a) != 1:
        raise SystemExit("anchor matched %d times: %r" % (text.count(a), anchor[:70]))
    return text.replace(a, r).encode("utf-8")


def run(tests):
    if isinstance(tests, str):
        cmd = tests.split()
    else:
        cmd = [sys.executable, "-m", "unittest", *tests]
    done = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=1500)
    return done.returncode, done.stdout + done.stderr


def main(names):
    wanted = [m for m in MUTATIONS if not names or m[0] in names]
    # A previous run killed mid-mutation leaves a defective source behind; refuse to run over it.
    for path in sorted({m[1] for m in wanted}):
        if subprocess.run(["git", "diff", "--quiet", "--", path], cwd=ROOT).returncode != 0:
            raise SystemExit("%s differs from HEAD; restore it (git checkout %s) before mutating" % (path, path))
    out = ROOT.parent / "evidence" / "final" / "usb_mutations"
    out.mkdir(parents=True, exist_ok=True)
    survivors = []
    for name, path, anchor, replacement, tests, why in wanted:
        original = read_raw(path)
        started = time.time()
        try:
            (ROOT / path).write_bytes(mutate(original, anchor, replacement))
            code, text = run(tests)
        finally:
            (ROOT / path).write_bytes(original)
        (out / (name + ".log")).write_text(text, encoding="utf-8")
        caught = code != 0
        print("%-48s %s  (%.0fs)  -- %s" % (name, "caught" if caught else "SURVIVED", time.time() - started, why),
              flush=True)
        if not caught:
            survivors.append(name)
    print("\n%d mutations, %d caught, %d survived" % (len(wanted), len(wanted) - len(survivors), len(survivors)))
    if survivors:
        print("survivors:", ", ".join(survivors))
    return 1 if survivors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
