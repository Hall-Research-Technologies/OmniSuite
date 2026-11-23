"""Deliberate-defect run for the Multiview ordered-clear, heal and ownership rules.

Server mutations run tests.test_multiview_assignment; the one UI mutation runs tests/multiview_ui_test.js.
Every mutation must fail its suite. The source is restored in a finally block, anchors are translated to the
file's own line endings, and each mutation's whole output is kept under OMNI_MUTATION_LOGS.
"""
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRV = "OmniMatrix_upgrade_server_v7_6y.py"
JS = "ui/matrix/multiview.js"
PYSUITE = [sys.executable, "-m", "unittest", "tests.test_multiview_assignment"]
JSSUITE = ["node", "tests/multiview_ui_test.js"]

M = [
 ("ordering dropped", SRV, "    if not groups:\n        return mutations\n    index = next(", "    return mutations\n    index = next(", PYSUITE),
 ("pair emptied in one write again", SRV, "            others = list(keep) + [c for c in cleared[position + 1:]]\n", "            others = list(keep)\n", PYSUITE),
 ("sibling bound back before the window is emptied", SRV,
  "                steps.append(step(\"Empty %s\" % short(label), [(label, \"\")]))\n                steps.append(step(\"Bind %s again\" % \", \".join(short(o) for o in others),\n                                  [(o, bound) for o in others]))",
  "                steps.append(step(\"Bind %s again\" % \", \".join(short(o) for o in others),\n                                  [(o, bound) for o in others]))\n                steps.append(step(\"Empty %s\" % short(label), [(label, \"\")]))", PYSUITE),
 ("emptied windows left in the ordinary rebind", SRV, "                 if str(s.get(\"name\") or \"\") not in handled]", "                 if True]", PYSUITE),
 ("ordering not applied", SRV, "        mutations = _ordered_shared_clears(\n            mutations, ip, name, _shared_input_groups(target, after))\n", "", PYSUITE),
 ("a pair emptied together is not recognised", SRV, "        if keep or len(cleared) > 1:", "        if keep:", PYSUITE),
 ("switch leaves the ownership record stale", SRV, "             for w in after[\"windows\"] if w.get(\"ip_input\") and w.get(\"multicast\")},\n            released=release)", "             for w in after[\"windows\"] if False},\n            released=release)", PYSUITE),
 ("heal never attempted", SRV, "    if not unlocked:\n        return report\n    target = next(", "    return report\n    target = next(", PYSUITE),
 ("helper window left bound", SRV, "                if helper and held:\n                    put(helper, \"\")", "                if False:\n                    put(helper, \"\")", PYSUITE),
 ("heal borrows a window that shares the input", SRV, "        if partners:\n            entry[\"result\"] = \"not attempted: the window shares its input\"", "        if False:\n            entry[\"result\"] = \"not attempted: the window shares its input\"", PYSUITE),
 ("heal borrows a window of another size", SRV, "                       and size_of(str(s.get(\"name\") or \"\")) == size_of(label)), None)", "                       ), None)", PYSUITE),
 ("heal result not reported in the switch", SRV, "        \"healing\": healing,\n        \"regressed_windows\": regressions,", "        \"regressed_windows\": regressions,", PYSUITE),
 ("a stale entry of another saved Multiview vetoes ownership", SRV, "            if record_name != shown:\n                continue\n", "", PYSUITE),
 ("a sibling is kept on the input although the plan moves it", SRV, "if other == bound and label not in cleared and planned.get(label) == bound]", "if other == bound and label not in cleared]", PYSUITE),
 ("repaired line never shown to the operator", JS,
  "    ((body.healing || {}).healed || []).forEach((label) => {\n      lines.push('[repaired] '", "    ([]).forEach((label) => {\n      lines.push('[repaired] '", JSSUITE),
 ("repaired line names the whole label", JS, "String(label).replace(/ \\(.*$/, '')", "String(label)", JSSUITE),
]


def crlf(text, eol):
    return text.replace("\n", eol)


def main(argv):
    wanted = argv[1:]
    logs = Path(os.environ.get("OMNI_MUTATION_LOGS") or ROOT.parent / "evidence" / "mutation_mv2")
    logs.mkdir(parents=True, exist_ok=True)
    caught, survived, stale = [], [], []
    for name, rel, old, new, suite in M:
        if wanted and not any(w in name for w in wanted):
            continue
        path = ROOT / rel
        raw = path.read_bytes()
        text = raw.decode("utf-8")
        eol = "\r\n" if "\r\n" in text else "\n"
        a, b = crlf(old, eol), crlf(new, eol)
        if text.count(a) != 1:
            print("%-60s STALE (anchor x%d)" % (name, text.count(a)), flush=True)
            stale.append(name)
            continue
        try:
            path.write_bytes(text.replace(a, b, 1).encode("utf-8"))
            p = subprocess.run(suite, cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=600)
        except subprocess.TimeoutExpired:
            class p:  # noqa: N801
                returncode, stdout, stderr = 99, "HANG", ""
        finally:
            path.write_bytes(raw)
        out = (p.stdout or "") + (p.stderr or "")
        (logs / (re.sub(r"[^A-Za-z0-9._-]", "_", name) + ".txt")).write_text(
            "exit %d\n%s" % (p.returncode, out), encoding="utf-8")
        first = next((l.strip() for l in out.splitlines() if l.startswith(("FAIL", "ERROR")) or "FAIL " in l), "")
        if p.returncode:
            caught.append(name)
            print("%-60s caught  %s" % (name, first[:80]), flush=True)
        else:
            survived.append(name)
            print("%-60s SURVIVED" % name, flush=True)
    print("total %d  caught %d  stale %d  survived %d" % (
        len(caught) + len(stale) + len(survived), len(caught), len(stale), len(survived)))
    for name in survived:
        print("  SURVIVED", name)
    return 1 if survived or stale else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
