"""Deliberate-defect run for the Multiview assignment server rules (tests.test_multiview_assignment)."""
import subprocess, sys
import os
from pathlib import Path
ROOT = str(Path(__file__).resolve().parent.parent)
SRV = ROOT + r'\OmniMatrix_upgrade_server_v7_6y.py'
CONF = ROOT + r'\ui\confirm.js'
PY = sys.executable
M = [
 ('clear_cells ignored', SRV, "    for extra in clear_cells:\n        wanted.pop(extra, None)\n", "    pass\n"),
 ('switch outside the lock', SRV, "    with _multiview_apply_lock:\n        response = _multiview_switch_locked(payload)", "    if True:\n        response = _multiview_switch_locked(payload)"),
 ('op_id not echoed', SRV, '            data["op_id"] = op_id\n', "            pass\n"),
 ('emptied window counted as regression', SRV, "and label != changed[\"label\"] and label not in emptied]", "and label != changed[\"label\"]]"),
 ('clear_cells type unchecked', SRV, "    if not isinstance(raw_clear, list):", "    if False:"),
 ('cancel not default (dialog)', CONF, "(options.danger || options.cancelDefault) ? cancelBtn : okBtn", "options.danger ? cancelBtn : okBtn"),
]
def crlf(s): return s.replace('\n', '\r\n')
for name, path, a, b in M:
    orig = open(path, 'rb').read()
    t = orig.decode()
    if t.count(crlf(a)) != 1:
        print(name, 'ANCHOR MISS', t.count(crlf(a))); continue
    try:
        open(path, 'wb').write(t.replace(crlf(a), crlf(b)).encode())
        p = subprocess.run([PY, '-m', 'unittest', 'tests.test_multiview_assignment'], cwd=ROOT, capture_output=True, text=True, timeout=300)
    finally:
        open(path, 'wb').write(orig)
    import re as _re,os
    LOGS = os.path.join(os.environ.get('OMNI_MUTATION_LOGS') or os.path.join(os.path.dirname(ROOT), 'evidence'), 'mutation_py'); os.makedirs(LOGS, exist_ok=True)
    open(os.path.join(LOGS, _re.sub(r'[^A-Za-z0-9._-]', '_', name) + '.txt'), 'w', encoding='utf-8').write('exit %d\n%s%s' % (p.returncode,p.stdout,p.stderr))
    print(name, 'CAUGHT' if p.returncode else 'SURVIVED', flush=True)
