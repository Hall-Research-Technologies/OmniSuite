#!/usr/bin/env python3
"""Break each predicate the Remote User's per-window Multiview choice depends on; require the tests to notice.

    python tools/mutate_user_multiview_choice.py [name ...]

An assertion nobody has watched fail is an assertion nobody has verified. Each mutation edits one source file
in place, runs the named suites, and restores the file in a `finally` so an interrupted run never leaves a
deliberately defective source behind. Anchors are written with newlines and translated to the file's own line
endings (sources here are CRLF; an anchor written with newlines matches nothing otherwise).

Before anything is mutated:
  * the files must be identical to HEAD (a previous killed run leaves a defective source behind);
  * the unmutated tree must pass every suite, or "caught" means nothing;
  * one CANARY per suite is broken on purpose and must be noticed -- a harness that cannot see a suite
    reports that suite's tests as worthless, which is indistinguishable from them being worthless.

A mutation that SURVIVES is investigated, never waved through: a real gap, a test gap, or a redundant
control, and which one has to be established rather than assumed. Equivalent mutants are listed
explicitly with the reason, and are still run (so a later change that makes one observable is noticed).

Each mutation's whole output (not its last line) is kept with the run's evidence, not discarded.
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
API = "omni_remote_api.py"
GATE = "omni_gate.py"
ACCESS = "omni_access.py"
SRV = "OmniMatrix_upgrade_server_v7_6y.py"
JS = "ui/user/multiview.js"

PY_CHOICE = ["py:tests.test_user_multiview_choice", "py:tests.test_user_multiview"]
PY_COMPOSE = ["py:tests.test_user_multiview_compose"]
JS_EDITOR = ["js:tests/user_multiview_editor_test.js", "js:tests/user_multiview_test.js"]
ALL_SUITES = PY_CHOICE + PY_COMPOSE + JS_EDITOR

# (name, file, anchor, replacement, suites, why it matters, equivalent-reason or "")
MUTATIONS = [
    # ---- Remote User Multiview authorization --------------------------------------------------------------
    ("auth.apply-skips-layout-assignment", API,
     '            if not account.permissions.may_operate_multiview(decoder, layout_id):\n'
     '                return "multiview_not_assigned"\n'
     '            for identity in wanted_ids.values():\n',
     '            for identity in wanted_ids.values():\n',
     PY_CHOICE, "any layout on any decoder could be composed by an account holding the capability", ""),
    ("auth.apply-skips-feature", API,
     '            if not account.permissions.has_feature(omni_access.FEATURE_MULTIVIEW):\n'
     '                return "missing_feature:multiview"\n'
     '            if not account.permissions.may_operate_multiview(decoder, layout_id):\n'
     '                return "multiview_not_assigned"\n'
     '            for identity in wanted_ids.values():\n',
     '            if not account.permissions.may_operate_multiview(decoder, layout_id):\n'
     '                return "multiview_not_assigned"\n'
     '            for identity in wanted_ids.values():\n',
     PY_CHOICE, "the capability would not be checked by the handler (the gate still would)", ""),
    ("auth.layout-view-skips-assignment", API,
     '            if not account.permissions.may_operate_multiview(decoder, layout_id):\n'
     '                return _denied(account, "multiview_not_assigned",\n'
     '                               "/api/user/multiview_layout")\n',
     '',
     PY_CHOICE, "an account could open and read any layout of any decoder", ""),
    ("auth.layout-view-skips-feature", API,
     '            if not account.permissions.has_feature(omni_access.FEATURE_MULTIVIEW):\n'
     '                return _denied(account, "missing_feature:multiview",\n'
     '                               "/api/user/multiview_layout")\n',
     '',
     PY_CHOICE, "an account without the capability could open a layout", ""),
    ("route.apply-without-capability", ACCESS,
     '    "/api/user/multiview_compose": (Access.USER, FEATURE_MULTIVIEW),',
     '    "/api/user/multiview_compose": (Access.USER, None),',
     PY_CHOICE, "the capability would not gate choosing sources", ""),
    ("route.layout-without-capability", ACCESS,
     '    "/api/user/multiview_layout": (Access.USER, FEATURE_MULTIVIEW),',
     '    "/api/user/multiview_layout": (Access.USER, None),',
     PY_CHOICE, "the capability would not gate opening a layout", ""),
    # ---- source authorization ---------------------------------------------------------------------------
    ("source.apply-skips-grant-check", API,
     '                if identity and not account.permissions.may_use_source(identity):\n'
     '                    return "source_not_permitted"\n',
     '',
     PY_CHOICE, "an unauthorized source could be put in a window (the gate is the second line)", ""),
    ("source.gate-ignores-changes-map", GATE,
     'SOURCE_MAP_KEYS = ("changes",)',
     'SOURCE_MAP_KEYS = ()',
     PY_CHOICE, "the gate would not look at the per-window source map", ""),
    ("source.listing-offers-ungiven-sources", API,
     '            if identity and _may_use_source(account, identity):\n'
     '                rows[identity] = row\n',
     '            if identity:\n'
     '                rows[identity] = row\n',
     PY_CHOICE, "the page would offer every encoder, and name ones the account was not given", ""),
    ("source.window-names-ungiven-source", API,
     '            elif current.get("origin") == "unknown" or mac or current.get("source_ip"):\n',
     '            elif current.get("origin") == "unknown":\n',
     PY_CHOICE, "a window holding a source the account was not given would name it or show it empty", ""),
    ("source.admin-grant-bypass-widened", API,
     '        return account.is_admin or account.permissions.may_use_source(identity)',
     '        return True',
     PY_CHOICE, "every account would be treated as holding every source in the listing", ""),
    ("source.not-ready-accepted", API,
     '                if row.get("status") != "ready":\n',
     '                if False:\n',
     PY_CHOICE, "a source that needs configuration would be sent to the engine", ""),
    ("source.unknown-identity-accepted", API,
     '                if row is None or not address:\n',
     '                if not address:\n',
     PY_CHOICE, "a source missing from the inventory would be passed on", ""),
    ("source.address-accepted-as-identity", API,
     '            if not _SOURCE_MAC.match(identity):\n',
     '            if False:\n',
     PY_CHOICE, "an address or a name could stand in for a source identity", ""),
    ("source.mac-regex-unanchored", API,
     '    _SOURCE_MAC = re.compile(r"^(?:[0-9a-f]{2}:){5}[0-9a-f]{2}\\Z")',
     '    _SOURCE_MAC = re.compile(r"^(?:[0-9a-f]{2}:){5}[0-9a-f]{2}")',
     PY_CHOICE, "a source identity with trailing text would be accepted", ""),
    # ---- decoder / layout authorization --------------------------------------------------------------------
    ("layout.locate-ignores-layout-id", API,
     '        entry = next((e for e in row["layouts"] if e["id"] == layout_id), None)\n'
     '        if entry is None:\n'
     '            return None, (jsonify({"ok": False, "reason": "layout_missing",\n'
     '                                   "error": "That layout is no longer on that "\n'
     '                                            "display."}), 404)\n'
     '        if needs_showable',
     '        entry = next(iter(row["layouts"]), None)\n'
     '        if entry is None:\n'
     '            return None, (jsonify({"ok": False, "reason": "layout_missing",\n'
     '                                   "error": "That layout is no longer on that "\n'
     '                                            "display."}), 404)\n'
     '        if needs_showable',
     PY_CHOICE, "the first layout on a decoder would answer for whichever one was assigned", ""),
    ("layout.unsupported-decoder-still-composed", API,
     '        if row is None or row["status"] == "unsupported":\n'
     '            return None, (jsonify({"ok": False, "reason": "multiview_unavailable",\n'
     '                                   "error": "Multiview is not available on that "\n'
     '                                            "display."}), 409)\n'
     '        if row["status"] == "unreachable":\n'
     '            return None, (jsonify({"ok": False, "reason": "display_unreachable",\n'
     '                                   "error": "That display is not answering."}), 502)\n'
     '        entry =',
     '        if row is None:\n'
     '            return None, (jsonify({"ok": False, "reason": "multiview_unavailable",\n'
     '                                   "error": "Multiview is not available on that "\n'
     '                                            "display."}), 409)\n'
     '        if row["status"] == "unreachable":\n'
     '            return None, (jsonify({"ok": False, "reason": "display_unreachable",\n'
     '                                   "error": "That display is not answering."}), 502)\n'
     '        entry =',
     PY_CHOICE, "a decoder that lost Multiview would still be commanded", ""),
    ("layout.unshowable-composed", API,
     '        if needs_showable and not entry["showable"]:\n',
     '        if False:\n',
     PY_CHOICE, "a layout the decoder cannot show (Video Wall, Fast Switching) would be attempted", ""),
    # ---- geometry / design attempts ------------------------------------------------------------------------
    ("design.fields-allowed", API,
     '        if design:\n'
     '            return _denied(',
     '        if False:\n'
     '            return _denied(',
     PY_CHOICE, "a request carrying design fields would be treated as a plain Show", ""),
    ("design.stray-fields-ignored", API,
     '        if stray:\n',
     '        if False:\n',
     PY_CHOICE, "unknown fields would be silently ignored instead of refused", ""),
    ("design.window-not-in-layout-accepted", API,
     '                if cell not in cells:\n',
     '                if False:\n',
     PY_CHOICE, "a window the layout does not have could be addressed", ""),
    ("design.no-geometry-still-editable", API,
     '            if not cells:\n'
     '                return None, None, None, (jsonify({\n',
     '            if False:\n'
     '                return None, None, None, (jsonify({\n',
     PY_CHOICE, "a layout with no known design would be composed anyway", ""),
    ("design.too-many-changes", API,
     'or not isinstance(changes, dict) or len(changes) > _MAX_CHANGES:',
     'or not isinstance(changes, dict):',
     PY_CHOICE, "an unbounded change map would be accepted", ""),
    # ---- revocation: before the lock wait and after it ---------------------------------------------------
    ("revoke.no-recheck-after-lock-wait", API,
     '                refusal = _revalidate(_check, "/api/user/multiview_compose")\n'
     '                if refusal is not None:\n'
     '                    return _finish(refusal)\n',
     '',
     PY_CHOICE, "a grant withdrawn during the wait would still reach the decoder", ""),
    ("revoke.no-check-before-lock-wait", API,
     '        refusal = _revalidate(_check, "/api/user/multiview_compose")\n'
     '        if refusal is not None:\n'
     '            return refusal\n'
     '        account = _principal() or _LocalAdmin()\n',
     '        account = _principal() or _LocalAdmin()\n',
     PY_CHOICE, "a request that was never allowed would queue for the display's lock", ""),
    ("revoke.layout-view-uses-stale-session", API,
     '        account = _principal() or _LocalAdmin()\n'
     '        key = str(request.args.get("layout") or "")\n',
     '        account = _principal() or _LocalAdmin()\n'
     '        account = account\n'
     '        key = str(request.args.get("layout") or "")\n',
     PY_CHOICE, "(canary for the harness itself: changes nothing)", "EQUIVALENT: a no-op assignment"),
    # ---- stale operations ---------------------------------------------------------------------------------
    ("stale.revision-not-compared", SRV,
     '        if not base or base != current["revision"]:\n',
     '        if False:\n',
     PY_COMPOSE, "a composition planned from an old view would overwrite a newer one", ""),
    ("stale.empty-base-trusted", SRV,
     '        if not base or base != current["revision"]:\n',
     '        if base and base != current["revision"]:\n',
     PY_COMPOSE, "a request carrying no revision would be applied without a comparison", ""),
    ("stale.revision-ignores-window-sources", SRV,
     '                        [[w["cell"], w["source_ip"], w["origin"] == "unknown"]\n'
     '                         for w in windows]])',
     '                        []])',
     PY_COMPOSE, "a change to a window would not change the revision", ""),
    ("stale.revision-ignores-showing", SRV,
     '    token = json.dumps([_USER_MV_SALT, name, bool(showing),\n',
     '    token = json.dumps([_USER_MV_SALT, name, False,\n',
     PY_COMPOSE, "the layout going on or off the display would not change the revision", ""),
    ("stale.compose-outside-the-lock", SRV,
     '    with _multiview_apply_lock:\n'
     '        state, error = _decoder_state(ip)\n'
     '        if state is None:\n'
     '            return {"ok": False, "error": error}, 502\n'
     '        current = _user_mv_windows(ip, name, state)\n',
     '    if True:\n'
     '        state, error = _decoder_state(ip)\n'
     '        if state is None:\n'
     '            return {"ok": False, "error": error}, 502\n'
     '        current = _user_mv_windows(ip, name, state)\n',
     PY_COMPOSE, "an administrator switch could interleave between the read and the write", ""),
    # ---- unassign / baseline / composition ------------------------------------------------------------
    ("unassign.clear-does-not-remove", SRV,
     '            else:\n'
     '                composition.pop(cell, None)\n'
     '        return _show_multiview_on(',
     '            else:\n'
     '                pass\n'
     '        return _show_multiview_on(',
     PY_COMPOSE, "clearing a window would leave its source in place", ""),
    ("unassign.empty-window-falls-back-to-saved", SRV,
     '            if not view.get("ip_input"):\n'
     '                origin = "empty"\n'
     '            elif view.get("source_origin")',
     '            if False:\n'
     '                origin = "empty"\n'
     '            elif view.get("source_origin")',
     PY_COMPOSE, "a window the display holds empty would be shown as holding the saved source", ""),
    ("unassign.api-drops-empty-changes", API,
     '            if value == "":\n'
     '                wanted_ids[cell] = ""\n'
     '                continue\n',
     '            if value == "":\n'
     '                continue\n',
     PY_CHOICE, "an unassign would never reach the engine", ""),
    ("unassign.api-keeps-source-on-clear", API,
     '                if not identity:\n'
     '                    wanted[cell] = ""\n'
     '                    continue\n',
     '                if not identity:\n'
     '                    continue\n',
     PY_CHOICE, "a cleared window would be dropped from the request", ""),
    ("compose.override-ignored", SRV,
     '        assignments = dict(assignments_override)\n',
     '        pass\n',
     PY_COMPOSE, "the person's choices would be replaced by the saved windows", ""),
    ("compose.saved-record-rewritten", SRV,
     '        return _show_multiview_on(ip, name, assignments_override=composition)\n',
     '        _record = dict((_multiview_meta_for({d.get("ip"): d for d in _load_cache()}.get(ip, {})) or {}).get(name) or {})\n'
     '        _record["windows"] = [{"cell": c, "source_ip": s} for c, s in composition.items()]\n'
     '        _record_multiview_meta({d.get("ip"): d for d in _load_cache()}.get(ip, {}), name, _record)\n'
     '        return _show_multiview_on(ip, name, assignments_override=composition)\n',
     PY_COMPOSE, "a person's Show would silently rewrite the administrator's saved layout", ""),
    ("compose.baseline-ignores-display", SRV,
     '    showing = str((hdmi.get("video") or {}).get("input") or "") == name\n'
     '    record = _effective_record(',
     '    showing = False\n'
     '    record = _effective_record(',
     PY_COMPOSE, "the saved record would decide even for the layout that is on the display", ""),
    # ---- Show verification / result mapping ---------------------------------------------------------------
    ("verify.result-trusts-status-200", API,
     '        if body.get("ok"):\n'
     '            dark = body.get("unlocked_windows")',
     '        if True:\n'
     '            dark = body.get("unlocked_windows")',
     PY_CHOICE, "an engine failure would be reported to the person as success", ""),
    ("verify.stale-reported-as-failure", API,
     '        if body.get("reason") == "stale":\n',
     '        if False:\n',
     PY_CHOICE, "a changed layout would be reported as a failed display", ""),
    ("verify.error-not-scrubbed", API,
     '        return _ADDRESS.sub("a unit", str(text or "")).strip()[:240]',
     '        return str(text or "").strip()[:240]',
     PY_CHOICE, "an address or MAC inside an engine sentence would reach a restricted account", ""),
    ("verify.js-success-without-ok", JS,
     "          if (reply && reply.status === 200 && body.ok) {\n"
     "            var note = body.message || '';\n"
     "            results[layoutId]",
     "          if (reply && reply.status === 200) {\n"
     "            var note = body.message || '';\n"
     "            results[layoutId]",
     JS_EDITOR, "(list Show) a refusal delivered with status 200 would read as success", ""),
    ("verify.js-apply-success-without-ok", JS,
     "        var ok = !!(reply && reply.status === 200 && body.ok);\n",
     "        var ok = !!(reply && reply.status === 200);\n",
     JS_EDITOR, "an unverified Show would clear the person's choices as though shown", ""),
    ("verify.js-pending-kept-after-failure-dropped", JS,
     "        return readLayout(layoutId, !ok).then(function (outcome) {\n",
     "        return readLayout(layoutId, false).then(function (outcome) {\n",
     JS_EDITOR, "a failed Show would throw away what the person had chosen", ""),
    ("verify.js-pending-kept-after-success", JS,
     "        return readLayout(layoutId, !ok).then(function (outcome) {\n",
     "        return readLayout(layoutId, true).then(function (outcome) {\n",
     JS_EDITOR, "choices already shown would linger as 'not shown yet'", ""),
    # ---- the page: same-size reuse, incompatible-size replacement, rapid operations ---------------------------
    ("js.same-size-asks", JS,
     "        return other !== cell && effective(other) === id && sizeOf(other) !== wanted;\n",
     "        return other !== cell && effective(other) === id && true;\n",
     JS_EDITOR, "the legal same-size reuse would ask the Replace question", ""),
    ("js.different-size-never-asks", JS,
     "        return other !== cell && effective(other) === id && sizeOf(other) !== wanted;\n",
     "        return other !== cell && effective(other) === id && false;\n",
     JS_EDITOR, "one source could be put in windows of different sizes without asking", ""),
    ("js.replace-keeps-the-other-window", JS,
     "            sizeConflicts(cell, id).forEach(function (other) { setChoice(other, ''); });\n",
     "",
     JS_EDITOR, "Replace would leave the source in both windows", ""),
    ("js.cancel-applies-anyway", JS,
     "          if (agreed && !editor.applying) {\n",
     "          if (!editor.applying) {\n",
     JS_EDITOR, "Cancel would change the windows", ""),
    ("js.replace-focus-not-on-cancel", JS,
     "        cancelDefault: true,\n",
     "        cancelDefault: false,\n",
     JS_EDITOR, "Enter would confirm the costly choice", ""),
    ("js.native-dialog-fallback", JS,
     "        toastIfAny('This source is in a window of a different size, and the confirmation '\n"
     "          + 'dialog is unavailable, so nothing was changed.', 'error');\n",
     "",
     JS_EDITOR, "without the dialog the refusal would be silent", ""),
    ("js.unselectable-source-assignable", JS,
     "      if (!windowByCell(cell) || !selectable(id)) return Promise.resolve(false);\n",
     "      if (!windowByCell(cell)) return Promise.resolve(false);\n",
     JS_EDITOR, "a source that is not ready could be chosen from code", ""),
    ("js.choice-equal-to-display-recorded", JS,
     "      if (value === baselineValue(windowByCell(cell))) delete editor.pending[cell];\n"
     "      else editor.pending[cell] = value;\n",
     "      editor.pending[cell] = value;\n",
     JS_EDITOR, "choosing what the display holds would count as a change", ""),
    ("js.clear-records-nothing", JS,
     "      setChoice(cell, '');\n"
     "      editor.result = null;\n"
     "      paintEditor();\n"
     "      return true;\n",
     "      editor.result = null;\n"
     "      paintEditor();\n"
     "      return true;\n",
     JS_EDITOR, "the X would not unassign", ""),
    ("js.edit-allowed-while-showing", JS,
     "    function clearWindow(cell) {\n"
     "      if (!editor || busy() || editor.confirming) return false;\n",
     "    function clearWindow(cell) {\n"
     "      if (!editor || editor.confirming) return false;\n",
     JS_EDITOR, "windows could be edited while the Show is in flight", ""),
    ("js.assign-allowed-while-showing", JS,
     "    function assignWindow(cell, id) {\n"
     "      if (!editor || busy()) return Promise.resolve(false);\n",
     "    function assignWindow(cell, id) {\n"
     "      if (!editor) return Promise.resolve(false);\n",
     JS_EDITOR, "a source could be assigned while the Show is in flight", ""),
    ("js.show-not-single-flight", JS,
     "    function applyChoices() {\n"
     "      if (!editor || busy() || editor.confirming) return Promise.resolve(false);\n",
     "    function applyChoices() {\n"
     "      if (!editor || editor.confirming) return Promise.resolve(false);\n",
     JS_EDITOR, "three clicks would send three requests", ""),
    ("js.base-revision-not-sent", JS,
     "        return apply({layout: layoutId, base: editor.revision, changes: sent, op_id: op});\n",
     "        return apply({layout: layoutId, base: '', changes: sent, op_id: op});\n",
     JS_EDITOR, "the server could not tell a stale Show from a current one", ""),
    ("js.late-read-applied", JS,
     "        if (mine !== readSeq || !editor || editor.layoutId !== layoutId) return null;   // stale\n",
     "        if (!editor || editor.layoutId !== layoutId) return null;   // stale\n",
     JS_EDITOR, "an answer already in flight when the world changed would be applied", ""),
    ("js.foreign-answer-applied", JS,
     "        if (body.op_id && body.op_id !== op) { editor.applying = false; paintEditor(); return false; }\n",
     "",
     JS_EDITOR, "an answer to another request would be taken as this one's", ""),
    ("js.satisfied-choice-not-dropped-on-reread", JS,
     "        if (editor.pending[cell] === baselineValue(windowByCell(cell))) delete editor.pending[cell];\n"
     "        else if (editor.pending[cell] && !selectable(editor.pending[cell])) {\n",
     "        if (false) delete editor.pending[cell];\n"
     "        else if (editor.pending[cell] && !selectable(editor.pending[cell])) {\n",
     JS_EDITOR, "a choice the display now holds would stay 'not shown yet'", ""),
    ("js.revoked-source-kept-in-choices", JS,
     "          delete editor.pending[cell];                       // no longer a source on offer\n",
     "",
     JS_EDITOR, "a source withdrawn while the page was open would stay chosen", ""),
    ("js.editor-after-close-reopened-by-late-answer", JS,
     "      readSeq += 1;                 // whatever was in flight describes a closed page\n",
     "",
     JS_EDITOR, "a late answer could land on a page that was closed", "EQUIVALENT: the !editor test on the answer already drops it, and re-opening bumps the counter itself; the bump is a second line of defence with no observable difference"),
    ("js.back-allowed-mid-write", JS,
     "      if (!editor || editor.applying) return false;\n"
     "      editor = null;\n",
     "      if (!editor) return false;\n"
     "      editor = null;\n",
     JS_EDITOR, "leaving the editor mid-write would drop its result", ""),
    ("js.question-not-single", JS,
     "      if (editor.confirming) return Promise.resolve(false);   // one question at a time\n",
     "",
     JS_EDITOR, "two Replace dialogs could stack", ""),
    ("js.drag-payload-carries-identity", JS,
     "              event.dataTransfer.setData('text/plain', 'source');\n",
     "              event.dataTransfer.setData('text/plain', s.id);\n",
     JS_EDITOR, "a source identity would travel in the drag payload", ""),
]

# One deliberate defect per suite, which the harness must notice before any other result is believed.
CANARIES = [
    ("canary.choice-suite", API, '                "label": "Window %d" % number,', '                "label": "Wndw %d" % number,', PY_CHOICE),
    ("canary.compose-suite", SRV, '            if address:\n                composition[cell] = address\n',
     '            if address:\n                composition[cell] = ip\n', PY_COMPOSE),
    ("canary.editor-suite", JS, "        clear.setAttribute('aria-label', 'Clear ' + w.label);", "        clear.setAttribute('aria-label', 'Remove ' + w.label);", JS_EDITOR),
]


def read_raw(path):
    return (ROOT / path).read_bytes()


def mutate(raw: bytes, anchor: str, replacement: str) -> bytes:
    text = raw.decode("utf-8")
    crlf = "\r\n" in text
    a = anchor.replace("\n", "\r\n") if crlf else anchor
    r = replacement.replace("\n", "\r\n") if crlf else replacement
    if text.count(a) != 1:
        raise SystemExit("anchor matched %d times: %r" % (text.count(a), anchor[:90]))
    return text.replace(a, r).encode("utf-8")


def run_suite(spec):
    kind, target = spec.split(":", 1)
    cmd = ([sys.executable, "-m", "unittest", target] if kind == "py" else ["node", target])
    done = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=1500)
    return done.returncode, done.stdout + done.stderr


def run(suites):
    """(code, whole output). Non-zero when any suite failed; every suite's output is kept."""
    code, parts = 0, []
    for spec in suites:
        rc, text = run_suite(spec)
        parts.append("=== %s (exit %d) ===\n%s" % (spec, rc, text))
        if rc != 0:
            code = rc
    return code, "\n".join(parts)


def main(names):
    wanted = [m for m in MUTATIONS if not names or m[0] in names]
    files = sorted({m[1] for m in wanted} | {c[1] for c in CANARIES})
    for path in files:
        if subprocess.run(["git", "diff", "--quiet", "--", path], cwd=ROOT).returncode != 0:
            raise SystemExit("%s differs from HEAD; commit or restore it before mutating" % path)
    out = ROOT.parent / "final2" / "omni-ru" / "mutations"
    out.mkdir(parents=True, exist_ok=True)

    code, text = run(ALL_SUITES)
    (out / "_baseline.log").write_text(text, encoding="utf-8")
    if code != 0:
        raise SystemExit("the unmutated tree does not pass; see %s" % (out / "_baseline.log"))
    print("baseline: every suite passes", flush=True)

    for name, path, anchor, replacement, suites in CANARIES:
        original = read_raw(path)
        try:
            (ROOT / path).write_bytes(mutate(original, anchor, replacement))
            code, text = run(suites)
        finally:
            (ROOT / path).write_bytes(original)
        (out / (name + ".log")).write_text(text, encoding="utf-8")
        if code == 0:
            raise SystemExit("CANARY %s was NOT noticed: the harness cannot see that suite" % name)
        print("%-46s noticed" % name, flush=True)

    survivors, equivalents, stale = [], [], []
    for name, path, anchor, replacement, suites, why, equivalent in wanted:
        original = read_raw(path)
        started = time.time()
        try:
            try:
                mutated = mutate(original, anchor, replacement)
            except SystemExit as error:
                stale.append(name)
                print("%-46s STALE ANCHOR (%s)" % (name, error), flush=True)
                continue
            (ROOT / path).write_bytes(mutated)
            code, text = run(suites)
        finally:
            (ROOT / path).write_bytes(original)
        (out / (name + ".log")).write_text(text, encoding="utf-8")
        caught = code != 0
        label = "caught" if caught else ("SURVIVED (%s)" % equivalent if equivalent else "SURVIVED")
        print("%-46s %s  (%.0fs)  -- %s" % (name, label, time.time() - started, why), flush=True)
        if not caught:
            (equivalents if equivalent else survivors).append(name)
    caught_n = len(wanted) - len(survivors) - len(equivalents) - len(stale)
    print("\n%d mutations: %d caught, %d equivalent/uncaught-annotated, %d stale anchors, %d survived"
          % (len(wanted), caught_n, len(equivalents), len(stale), len(survivors)))
    if survivors:
        print("survivors:", ", ".join(survivors))
    if stale:
        print("stale:", ", ".join(stale))
    if equivalents:
        print("annotated:", ", ".join(equivalents))
    return 1 if (survivors or stale) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
