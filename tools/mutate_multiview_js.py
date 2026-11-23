"""Deliberate-defect run (stale-read-no-bump is an equivalent mutant: the queue guard on landing covers every window the
sequence bump closes; stale-read-both-guards-off proves the pair. cancel-not-default is held by test_multiview_assignment.)

Deliberate-defect run for the Multiview UI assignment rules.

Each mutation breaks one predicate in ui/matrix/multiview.js (or confirm.js),
runs tests/multiview_ui_test.js, and requires it to fail. The source is restored
in a finally block. Anchors are translated to the file's CRLF.
"""
import subprocess, sys, json
from pathlib import Path
ROOT = str(Path(__file__).resolve().parent.parent)
JS = ROOT + r'\ui\matrix\multiview.js'

import os
SKIP = set(os.environ.get('SKIP', '').split(','))
MUTATIONS = [
 ('size-rule-off', JS, "    return Object.keys(assignments)\n      .filter((other) => other !== cell && assignments[other] === ip\n                         && sizeOf(other) !== wanted)\n      .sort();",
  "    return [];"),
 ('size-rule-by-label', JS, "      .filter((other) => other !== cell && assignments[other] === ip\n                         && sizeOf(other) !== wanted)",
  "      .filter((other) => other !== cell && assignments[other] === ip\n                         && false)"),
 ('cancel-still-replaces', JS, "      if (!agreed) return;                          // Cancel: both stay as they were\n",
  "      // mutated: cancel ignored\n"),
 ('x-visual-only (saved source redrawn)', JS, "    if (!isLive() || live.cleared[cell]) return null;\n    return subframeFor(cell);",
  "    return subframeFor(cell);"),
 ('x-cleared-not-remembered', JS, "        live.cleared[op.cell] = true;\n      }\n    } else if",
  "      }\n    } else if"),
 ('rapid-race: no single-flight', JS, "    if (live.running) return;\n    live.running = true;", "    live.running = true;"),
 ('stale-read-applied', JS, "      if (live.ops.length) return;\n      live.cleared = {};", "      live.cleared = {};"),
 ('stale-read-no-bump', JS, "    stateSeq += 1;\n    render();\n    pumpLive();", "    render();\n    pumpLive();"),
 ('optimistic-accept', JS, "    setStatus((op.source ? 'Switching ' : 'Clearing ') + prettyCell(op.cell) + '…', null);\n    let body;",
  "    if (op.source) state.assignments[op.cell] = op.source; else delete state.assignments[op.cell];\n    setStatus((op.source ? 'Switching ' : 'Clearing ') + prettyCell(op.cell) + '…', null);\n    let body;"),
 ('replace-two-requests', JS, "source: op.source, clear_cells: op.clear, op_id: op.id};\n    setStatus",
  "source: op.source, clear_cells: [], op_id: op.id};\n    setStatus"),
 ('stale-read-both-guards-off', JS, [("      if (live.ops.length) return;\n      live.cleared = {};", "      live.cleared = {};"), ("    stateSeq += 1;\n    render();\n    pumpLive();", "    render();\n    pumpLive();")], None),
 ('layout-change-still-live', JS, "      && !view.pending_show && !layoutChanged(view);", "      && !view.pending_show;"),
 ('show-hidden-for-saved-edit', JS, "(state.mode === MODE.EDIT && view && !isLive())", "(state.mode === MODE.EDIT && view && !view.selected_on_output)"),
 ('drag-render-not-deferred', JS, "    if (dragInProgress()) { state.sourcesDeferred = true; return; }\n", ""),
 ('queue-ignored-in-projection', JS, "    live.ops.forEach((op) => {\n      op.clear.forEach((cell) => { delete out[cell]; });", "    [].forEach((op) => {\n      op.clear.forEach((cell) => { delete out[cell]; });"),
 ('x-not-disabled-while-pending', JS, "        clear.disabled = !!pendingFor(window_.cell);", "        clear.disabled = false;"),
 ('page-actions-not-held', JS, "const busy = mode === MODE.SAVING || live.ops.length > 0;", "const busy = mode === MODE.SAVING;"),
 ('no-edit-state-claim', JS, "    if ((saved[cell] || '') !== (assignedIp || '')) {\n      return {cls: 'unsaved', text: 'Unsaved change'};\n    }", "    if (false) {\n      return {cls: 'unsaved', text: 'Unsaved change'};\n    }"),
 ('cancel-not-default', ROOT + r'\ui\confirm.js', "      else ((options.danger || options.cancelDefault) ? cancelBtn : okBtn).focus();", "      else (options.danger ? cancelBtn : okBtn).focus();"),
]

def crlf(s): return s.replace('\r\n', '\n').replace('\n', '\r\n')

def run_tests():
    try:
        p = subprocess.run(['node', 'tests/multiview_ui_test.js'], cwd=ROOT, capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired:
        return 99, 'HANG: the suite did not finish'
    out = (p.stdout or '') + (p.stderr or '')
    return p.returncode, out

results = []
for name, path, old, new in MUTATIONS:
    if name in SKIP or name.split(' ')[0] in SKIP: continue
    original = open(path, 'rb').read()
    text = original.decode('utf-8')
    pairs = old if isinstance(old, list) else [(old, new)]
    mutated = text
    miss = None
    for a, b in pairs:
        if mutated.count(crlf(a)) != 1:
            miss = mutated.count(crlf(a)); break
        mutated = mutated.replace(crlf(a), crlf(b))
    if miss is not None:
        results.append((name, 'ANCHOR MISS %d' % miss, ''))
        continue
    try:
        open(path, 'wb').write(mutated.encode('utf-8'))
        code, out = run_tests()
    finally:
        open(path, 'wb').write(original)
    import re as _re
    LOGS = os.path.join(os.environ.get('OMNI_MUTATION_LOGS') or os.path.join(os.path.dirname(ROOT), 'evidence'), 'mutation_js'); os.makedirs(LOGS, exist_ok=True)
    open(os.path.join(LOGS, _re.sub(r'[^A-Za-z0-9._-]', '_', name) + '.txt'), 'w', encoding='utf-8').write('exit %d\n%s' % (code,out))
    failed = [l.strip() for l in out.splitlines() if l.strip().startswith('FAIL') or 'not ok' in l]
    results.append((name, 'CAUGHT' if code != 0 else 'SURVIVED', '; '.join(failed)[:160]))
    print(results[-1], flush=True)
print(json.dumps([[r[0], r[1]] for r in results], indent=1))
