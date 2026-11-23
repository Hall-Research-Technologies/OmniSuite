#!/usr/bin/env python3
"""Break each predicate Remote User Multiview depends on, and require the tests to notice.

    python tools/mutate_user_multiview.py [name ...]

An assertion nobody has watched fail is an assertion nobody has verified. Each mutation below edits one
source file in place, runs the named test modules, and restores the file in a `finally` -- so an
interrupted run never leaves a deliberately defective source behind. Anchors are written with newlines
and translated to the file's own line endings (some sources are CRLF, some are LF).

A mutation that SURVIVES is investigated, never waved through: a real gap, a test gap, or a redundant
control, and which one has to be established rather than assumed.

Each mutation's whole output is kept under evidence/final/user_multiview_mutations/.
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
API = "omni_remote_api.py"
USERS = "omni_users.py"
ACCESS = "omni_access.py"
SRV = "OmniMatrix_upgrade_server_v7_6y.py"
AUTH = ["tests.test_user_multiview", "tests.test_user_multiview_legacy"]
IDENT = ["tests.test_multiview.LayoutIdentityTests"]
BOTH = AUTH + IDENT

# (name, file, anchor, replacement, tests, why it matters)
MUTATIONS = [
    ("perm.operate-always-true", USERS,
     '        return bool(key) and key in self.multiviews\n',
     '        return bool(key)\n',
     AUTH, "any well-formed layout would be showable by any account holding the capability"),
    ("perm.id-regex-end-anchor", USERS,
     '_MULTIVIEW_ID = re.compile(r"^mv_[0-9a-f]{16}\\Z")',
     '_MULTIVIEW_ID = re.compile(r"^mv_[0-9a-f]{16}$")',
     AUTH, "a layout id with a trailing newline would be a valid identity"),
    ("perm.name-accepted-as-layout-id", USERS,
     '    if not _MULTIVIEW_MAC.match(identity) or not valid_multiview_id(str(layout_id or "")):\n',
     '    if not _MULTIVIEW_MAC.match(identity):\n',
     AUTH, "a friendly or object name would stand in for an opaque id"),
    ("perm.address-accepted-as-decoder", USERS,
     '    if not _MULTIVIEW_MAC.match(identity) or not valid_multiview_id(str(layout_id or "")):\n',
     '    if not valid_multiview_id(str(layout_id or "")):\n',
     AUTH, "an IP address or a hostname would stand in for the decoder's hardware identity"),
    ("perm.clear-ignores-assigned-decoders", USERS,
     '                for key in permissions.multiviews:\n'
     '                    decoder, _layout = split_multiview_key(key)\n'
     '                    if decoder:\n'
     '                        identities.add(decoder)\n',
     '',
     AUTH, "clearing the inventory would take a decoder away from a user who was given its layouts"),
    ("perm.forget-devices-drops-assignments", USERS,
     '                        p.features, sources, dests, p.groups, p.interfaces,\n'
     '                        p.multiviews)',
     '                        p.features, sources, dests, p.groups, p.interfaces)',
     AUTH, "forgetting a device would silently discard every layout assignment of the account"),
    ("perm.group-removal-drops-assignments", USERS,
     '                        p.groups - {gid}, p.interfaces, p.multiviews)',
     '                        p.groups - {gid}, p.interfaces)',
     AUTH, "deleting a display group would silently discard layout assignments"),
    ("route.show-without-capability", ACCESS,
     '    "/api/user/multiview_show": (Access.USER, FEATURE_MULTIVIEW),',
     '    "/api/user/multiview_show": (Access.USER, None),',
     AUTH, "the capability would not gate showing a layout"),
    ("route.picker-open-to-users", ACCESS,
     '    "/api/admin/multiviews": (Access.ADMIN, None),',
     '    "/api/admin/multiviews": (Access.AUTHENTICATED, None),',
     AUTH, "a restricted account could read every decoder and layout name in the site"),
    ("api.show-skips-assignment-check", API,
     '            if not account.permissions.may_operate_multiview(decoder, layout_id):\n'
     '                return "multiview_not_assigned"\n',
     '',
     AUTH, "any layout on any decoder could be shown by a user with the capability"),
    ("api.state-skips-capability", API,
     '        if not (account.is_admin\n'
     '                or account.permissions.has_feature(omni_access.FEATURE_MULTIVIEW)):\n'
     '            return _denied(account, "missing_feature:multiview",\n'
     '                           "/api/user/multiview_state")\n',
     '',
     AUTH, "an account without the capability could list its assigned layouts"),
    ("api.no-recheck-after-the-lock-wait", API,
     '                refusal = _revalidate(_check, "/api/user/multiview_show")\n'
     '                if refusal is not None:\n'
     '                    return refusal\n'
     '                forget_observation("multiview:" + str(address))\n',
     '                forget_observation("multiview:" + str(address))\n',
     AUTH, "a grant withdrawn during the wait would still reach the decoder"),
    ("api.no-check-before-the-lock-wait", API,
     '        refusal = _revalidate(_check, "/api/user/multiview_show")\n'
     '        if refusal is not None:\n'
     '            return refusal\n'
     '        resolved = _resolve_addresses(decoder)[decoder]\n',
     '        resolved = _resolve_addresses(decoder)[decoder]\n',
     AUTH, "a request that was never allowed would queue for the display's lock"),
    ("api.state-lists-every-layout-on-the-decoder", API,
     '                if entry["id"] not in wanted:\n'
     '                    continue\n',
     '',
     AUTH, "the user would see layouts nobody assigned them"),
    ("api.error-text-not-scrubbed", API,
     '        return _ADDRESS.sub("a unit", str(text or "")).strip()[:240]',
     '        return str(text or "").strip()[:240]',
     AUTH, "an address or MAC inside an engine sentence would reach a restricted account"),
    ("api.status-word-unchecked", API,
     '        label = label if _STATUS_WORDS.match(label or "") else ""\n',
     '        label = label or ""\n',
     AUTH, "free text could ride inside the status word"),
    ("api.unshowable-layout-is-shown", API,
     '            if not entry["showable"]:\n',
     '            if False:\n',
     AUTH, "a layout the decoder cannot show (Video Wall, Fast Switching) would be attempted"),
    ("api.unsupported-decoder-still-shown", API,
     '            if row is None or row["status"] == "unsupported":\n',
     '            if row is None:\n',
     AUTH, "a decoder that lost Multiview would still be commanded"),
    ("api.assignment-validation-skipped", API,
     '        if not added:\n'
     '            return ""\n'
     '        wanted_decoders = set()\n',
     '        if True:\n'
     '            return ""\n'
     '        wanted_decoders = set()\n',
     AUTH, "an administrator could assign a layout the server never checked exists"),
    ("api.ineligible-decoder-assignable", API,
     '            if row is None or not row["eligible"]:\n',
     '            if row is None:\n',
     AUTH, "a decoder without Multiview would accept new assignments"),
    ("api.missing-layout-assignable", API,
     '            if layout_id not in {entry["id"] for entry in row["layouts"]}:\n',
     '            if False:\n',
     AUTH, "a layout id that does not exist could be assigned"),
    ("api.key-repaired-not-refused", API,
     '        key = str(data.get("layout") or "")\n',
     '        key = str(data.get("layout") or "").strip()\n',
     AUTH, "a malformed key would be repaired into a valid one instead of refused"),
    ("api.new-assignments-lose-the-edit-on-save", API,
     '                    interfaces=held.permissions.interfaces,\n'
     '                    multiviews=wanted.multiviews)',
     '                    interfaces=held.permissions.interfaces)',
     AUTH, "saving an account would discard the layouts just assigned"),
    ("ident.new-id-on-every-save", SRV,
     '            record["id"] = (previous.get("id")\n'
     '                            if omni_users.valid_multiview_id(previous.get("id"))\n'
     '                            else _new_multiview_id())\n',
     '            record["id"] = _new_multiview_id()\n',
     IDENT, "every save of a layout would end every assignment to it"),
    ("ident.unsupported-reported-unreachable", SRV,
     '        if body.get("supported") is False:\n'
     '            return "unsupported", [], ""\n',
     '        if body.get("supported") is False:\n'
     '            return "unreachable", [], ""\n',
     IDENT, "a decoder without Multiview would be reported as merely not answering"),
    ("ident.everything-eligible", SRV,
     '                "status": status, "eligible": status == "ok",\n',
     '                "status": status, "eligible": True,\n',
     IDENT, "a decoder that does not answer would be offered for new assignments"),
    ("ident.interlock-ignored", SRV,
     '        showable = bool(view.get("showable")) and not blocked\n',
     '        showable = bool(view.get("showable"))\n',
     IDENT, "Video Wall / Fast Switching would be invisible to the user and to the picker"),
    ("ident.malformed-id-kept", SRV,
     '            if isinstance(record, dict) and not omni_users.valid_multiview_id(record.get("id")):\n',
     '            if False:\n',
     IDENT, "a record saved before ids existed would never become assignable"),
    ("ident.unrecorded-object-given-an-id", SRV,
     '        layout_id = ids.get(object_name)\n        if not layout_id:\n            continue\n',
     '        layout_id = ids.get(object_name) or "mv_0000000000000000"\n        if not layout_id:\n            continue\n',
     IDENT, "an object with no record would be given an identity nothing could verify"),
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
    cmd = [sys.executable, "-m", "unittest", *tests]
    done = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=1500)
    return done.returncode, done.stdout + done.stderr


def main(names):
    wanted = [m for m in MUTATIONS if not names or m[0] in names]
    # A previous run killed mid-mutation leaves a defective source behind; refuse to run over it.
    for path in sorted({m[1] for m in wanted}):
        if subprocess.run(["git", "diff", "--quiet", "--", path], cwd=ROOT).returncode != 0:
            raise SystemExit("%s differs from HEAD; commit or restore it before mutating" % path)
    out = ROOT.parent / "evidence" / "final" / "user_multiview_mutations"
    out.mkdir(parents=True, exist_ok=True)
    # The unmutated tree must pass, or "caught" means nothing.
    code, text = run(BOTH)
    (out / "_baseline.log").write_text(text, encoding="utf-8")
    if code != 0:
        raise SystemExit("the unmutated tree does not pass; see %s" % (out / "_baseline.log"))
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
        print("%-46s %s  (%.0fs)  -- %s" % (name, "caught" if caught else "SURVIVED", time.time() - started, why),
              flush=True)
        if not caught:
            survivors.append(name)
    print("\n%d mutations, %d caught, %d survived" % (len(wanted), len(wanted) - len(survivors), len(survivors)))
    if survivors:
        print("survivors:", ", ".join(survivors))
    return 1 if survivors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
