"""Mutation check for the macOS Local Network rules. Usage: python tools/mutate_local_network.py <repository root>"""
import subprocess
import sys
from pathlib import Path

root = Path(sys.argv[1])
CR, NL = chr(13), chr(10)
SERVER = "OmniMatrix_upgrade_server_v7_6y.py"
MUTATIONS = [
    ("the plist is never edited", "tools/build_release.py",
     '    info["NSLocalNetworkUsageDescription"] = usage', "    pass"),
    ("the bundle is not signed again", "tools/build_release.py",
     '    run(["codesign", "--force", "--deep", "--sign", "-", str(app_bundle)])', "    pass"),
    ("the signature is not verified", "tools/build_release.py",
     '    run(["codesign", "--verify", "--deep", "--strict", str(app_bundle)])', "    pass"),
    ("an empty description is accepted", "tools/build_release.py",
     "    if not usage.strip():", "    if False:"),
    ("the build never declares the description", "tools/build_release.py",
     "        declare_local_network_usage(", "        (lambda *a: None)("),
    ("Bonjour services are declared", "tools/build_release.py",
     '    info["NSLocalNetworkUsageDescription"] = usage',
     '    info["NSLocalNetworkUsageDescription"] = usage; info["NSBonjourServices"] = ["_x._tcp"]'),
    ("the smoke check passes a missing description", "tools/smoke_release.py",
     "    if not isinstance(usage, str) or not usage.strip():", "    if False:"),
    ("the smoke check is skipped on macOS", "tools/smoke_release.py",
     '        if platform.system() == "Darwin":', "        if False:"),
    ("a failed signature is not a problem", "tools/smoke_release.py",
     '            if args[0] == "--verify" and result.returncode != 0:', "            if False:"),
    ("every platform is told about macOS", SERVER,
     '    if system == "Darwin" and dominant in _LOCAL_NETWORK_SUSPECTS:',
     "    if dominant in _LOCAL_NETWORK_SUSPECTS:"),
    ("the macOS message loses the settings path", SERVER,
     "Local Network, switch OmniSuite on, and scan again.", "Local Network, and scan again."),
    ("the prompt hint is dropped", SERVER,
     "If a permission prompt is showing, choose ", "If a window is showing, choose "),
    ("the likely cause is not reported", SERVER,
     '                        "likely_cause": _scan_likely_cause(failure["dominant"], platform.system()),', ""),
    ("the platform is not recorded", SERVER,
     '                   "platform": {"system": platform.system(), "machine": platform.machine()},', ""),
    ("the guide loses its section", "ui/user-guide.html",
     'id="macos-local-network"', 'id="x-local-network"'),
    ("the guide loses the do-not-reset promise", "ui/user-guide.html",
     "does not change this setting", "may change this setting"),
    ("the product description is blank", "omni_release.py",
     '"OmniSuite connects directly to Atlona OmniStream encoders, decoders and USB "',
     '"OmniSuite "'),
    ("the first-launch instructions are not added to the zip", "tools/build_release.py",
     "        add_macos_readme(artifact, product)", "        pass"),
    ("the instructions tell people to strip quarantine", "tools/build_release.py",
     "  Do not turn off Gatekeeper, and do not run commands that remove the",
     "  Run xattr -d com.apple.quarantine, and do not run commands that remove the"),
    ("the display name is never set", "tools/build_release.py",
     "    if display_name:", "    if False:"),
    ("the smoke check stops requiring phrases", "tools/smoke_release.py",
     '    for needed in ("Open Anyway", "Privacy & Security", "Local Network", "official"):',
     "    for needed in ():"),
    ("the blocked-scan answer loses the product name", SERVER,
     '                        "app_name": omni_release.active_product().name,', ""),
    ("the notice shows for any blocked cause", "ui/scan-notice.js",
     "    return body.likely_cause === 'local_network_permission' ? 'local_network_permission' : null;",
     "    return body.likely_cause ? 'local_network_permission' : null;"),
    ("Open System Settings is offered to remote browsers", "ui/scan-notice.js",
     "      LOCAL_HOSTS.indexOf(hostname) !== -1 &&", ""),
    ("Open System Settings is offered for any server", "ui/scan-notice.js",
     "    return !!platform && platform.system === 'Darwin';", "    return true;"),
    ("Retry Scan does not run a scan", "ui/scan-notice.js",
     "        state.onRetry();", "        void 0;"),
    ("the diagnostics are dropped from the notice", "ui/scan-notice.js",
     "    box.appendChild(details);", "    void 0;"),
    ("the page forgets to retire the old notice", "ui/index.html",
     "  if (window.OmniScanNotice) OmniScanNotice.hide();", ""),
    ("the page never presents the verdict", "ui/index.html",
     "if (window.OmniScanNotice && OmniScanNotice.show(data)) {", "if (false) {"),
]


def run_suite():
    proc = subprocess.run([sys.executable, "-m", "unittest", "tests.test_macos_local_network",
                           "tests.test_macos_first_launch", "tests.test_scan_diagnostics"],
                          cwd=str(root), capture_output=True, text=True)
    if proc.returncode:
        return proc.returncode
    # The page and the notice module are JavaScript; their tests are run the way run_tests.py runs them.
    node = subprocess.run(["node", "tests/scan_notice_test.js"], cwd=str(root),
                          capture_output=True, text=True)
    return node.returncode


assert run_suite() == 0, "suite must be green first"
survivors = []
for label, filename, old, new in MUTATIONS:
    path = root / filename
    original = path.read_bytes()
    text = original.decode("utf-8")
    crlf = (CR + NL) in text
    work = text.replace(CR + NL, NL)
    if work.count(old) != 1:
        print("ANCHOR-ERROR %-50s matched %d" % (label, work.count(old)))
        survivors.append(label + " (anchor)")
        continue
    try:
        mutated = work.replace(old, new)
        path.write_bytes((mutated.replace(NL, CR + NL) if crlf else mutated).encode("utf-8"))
        code = run_suite()
    finally:
        path.write_bytes(original)
    print("%-8s %s" % ("caught" if code else "SURVIVED", label))
    if not code:
        survivors.append(label)
print()
print("survivors:", survivors or "none")
sys.exit(1 if survivors else 0)
