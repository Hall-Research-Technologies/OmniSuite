"""Deliberate-defect run for the A/V Matrix state-only patch (tests/matrix_lifecycle_test.js). Each mutation must fail it."""
import subprocess, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
F = ROOT / "ui" / "matrix" / "matrix.js"
import os
LOGS = Path(os.environ.get("OMNI_MUTATION_LOGS") or ROOT.parent / "evidence" / "matrix_mutations")
LOGS.mkdir(parents=True, exist_ok=True)
MUT = [
 ("patch.no-snapshot-compare", "      if (known && known[index] === bits) continue;\n", ""),
 ("patch.snapshot-never-stored", "  _matrixPatchSnapshots.set(t, next);\n}", "}"),
 ("patch.offline-bit-dropped", "| (f.offline ? 8 : 0);", "| 0;"),
 ("patch.audio-bit-dropped", "(f.audioMatch ? 4 : 0)", "0"),
 ("patch.video-bit-dropped", "(f.videoMatch ? 2 : 0)", "0"),
 ("patch.codec-bit-dropped", "(f.codecCompatible ? 1 : 0)", "0"),
 ("patch.stale-length-trusted", "previous && previous.length === total ? previous : null", "previous"),
 ("click.no-snapshot-drop", "      _matrixPatchSnapshots.delete(t);\n      for (const targetDec of targets) {", "      for (const targetDec of targets) {"),
 ("rebuild.keeps-old-snapshot", "  _matrixPatchSnapshots.set(t, snapshotBits);\n", ""),
 ("rebuild.snapshot-from-other-flags", "matrixFlagBits({codecCompatible, videoMatch, audioMatch, offline: unitOffline})", "matrixFlagBits({codecCompatible, videoMatch, audioMatch: false, offline: unitOffline})"),
 ("patch.always-query-table", "  let cells = null;\n", "  let cells = t.querySelectorAll('td.cell');\n"),
]
def run():
    d = subprocess.run(["node", "tests/matrix_lifecycle_test.js"], cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
    return d.returncode == 0, d.stdout + d.stderr
ok, out = run(); print("baseline", "PASS" if ok else "FAIL")
(LOGS / "matrix_baseline.txt").write_text(out, encoding="utf-8")
surv = []
for name, old, new in MUT:
    raw = F.read_bytes(); text = raw.decode("utf-8"); crlf = "\r\n" in text
    a = old.replace("\n", "\r\n") if crlf else old; b = new.replace("\n", "\r\n") if crlf else new
    if text.count(a) != 1:
        print("ANCHOR-MISS", name, text.count(a)); surv.append(name + " (anchor)"); continue
    try:
        F.write_bytes(text.replace(a, b).encode("utf-8")); ok, out = run()
    finally:
        F.write_bytes(raw)
    (LOGS / ("matrix_%s.txt" % name)).write_text(out, encoding="utf-8")
    print("%-40s %s" % (name, "SURVIVED" if ok else "caught"))
    if ok: surv.append(name)
print("survivors:", surv)
