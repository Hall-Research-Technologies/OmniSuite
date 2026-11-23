"""Break each platform-matrix rule on purpose; the focused suites must fail every time.

Usage: python tools/mutate_platform_matrix.py <repository root> [--list]

Each mutation edits one production file, runs the modules that guard it, and requires a failure.
The file is restored in a `finally` block, and anchors are translated to the file's own line endings
(sources here are CRLF; an anchor written with newlines would otherwise match nothing and report a
false "caught"). A mutation whose anchor cannot be found is an ERROR, never a pass. The whole output
of every run is kept in the harness's own log so a survivor can be investigated, not guessed at.
"""
import subprocess
import sys
from pathlib import Path

root = Path(sys.argv[1])
CR, NL = chr(13), chr(10)

PY = ("tests.test_platform_matrix", "tests.test_updater_proof", "tests.test_updater_adversarial",
      "tests.test_release_isolation", "tests.test_release_compatibility", "tests.test_usb_integration.ArtifactNamingTests",
      "tests.test_inspect_release")
WF = ".github/workflows/build-cross-platform.yml"
SRV = "OmniMatrix_upgrade_server_v7_6y.py"

# (name, file, anchor, replacement, runner) where runner is "py" or "js:<file>"
MUTATIONS = [
    ("an Intel macOS build row returns", WF, "          - os: macos-15" + NL + "            suffix: arm64",
     "          - os: macos-15-intel" + NL + "            suffix: x86_64" + NL + "          - os: macos-15" + NL + "            suffix: arm64", "py"),
    ("a Linux test runner returns", WF, "          - os: macos-15" + NL + "            label: macOS arm64",
     "          - os: ubuntu-latest" + NL + "            label: Ubuntu x86_64" + NL + "          - os: macos-15" + NL + "            label: macOS arm64", "py"),
    ("the runner label floats to macos-latest", WF, "          - os: macos-15" + NL + "            suffix: arm64",
     "          - os: macos-latest" + NL + "            suffix: arm64", "py"),
    ("lipo's answer is not required to be exactly arm64", WF, 'test "$archs" = "arm64" ||', "true ||", "py"),
    ("the Mach-O walk over the bundle is dropped", WF, "python tools/verify_macho_arch.py --path", "echo skipped --path", "py"),
    ("the Mach-O walk over the distributed zip is dropped", WF, "python tools/verify_macho_arch.py --zip", "echo skipped --zip", "py"),
    ("the bundle identity check is dropped", WF, "python tools/verify_macho_arch.py --bundle-identity", "echo skipped --bundle-identity", "py"),
    ("the ad hoc signature is no longer asserted", WF, 'grep -q "Signature=adhoc"', 'grep -q "Signature="', "py"),
    ("tar archives are collected again", WF, "release/*.zip" + NL, "release/*.zip" + NL + "            release/*.tar.gz" + NL, "py"),
    ("a retired tail becomes a distributed one", "omni_release.py",
     'PLATFORM_TAILS = ("Windows-x86_64.zip", "macOS-arm64.zip")',
     'PLATFORM_TAILS = ("Windows-x86_64.zip", "macOS-arm64.zip", "macOS-x86_64.zip")', "py"),
    ("macOS Intel is offered the Intel asset again", SRV,
     '        if machine in ("arm64", "aarch64"):' + NL + '            return "macos-arm64"' + NL + "        return None",
     '        if machine in ("arm64", "aarch64"):' + NL + '            return "macos-arm64"' + NL + '        if machine in ("x86_64", "amd64"):' + NL + '            return "macos-x86_64"' + NL + "        return None", "py"),
    ("Linux is offered an asset again", SRV,
     '        return "macos-arm64"' + NL + "        return None" + NL + "    return None",
     '        return "macos-arm64"' + NL + "        return None" + NL + '    if system == "linux":' + NL + '        return "ubuntu-x86_64"' + NL + "    return None", "py"),
    ("an unsupported platform is told nothing", SRV,
     "                    result[" + chr(34) + "package_message" + chr(34) + "] = UPDATE_NO_PACKAGE_UNSUPPORTED",
     "                    result[" + chr(34) + "package_message" + chr(34) + "] = None", "py"),
    ("the build script can be asked for Linux again", "tools/build_release.py",
     'choices=["windows", "arm64"]', 'choices=["windows", "linux", "arm64"]', "py"),
    ("an Intel suffix is a build name again", "tools/build_release.py",
     '    "arm64": "{prefix}{version}-macOS-arm64.zip",',
     '    "arm64": "{prefix}{version}-macOS-arm64.zip",' + NL + '    "x86_64": "{prefix}{version}-macOS-x86_64.zip",', "py"),
    ("an Intel Mac may build as arm64", "tools/build_release.py",
     '        if machine in ("arm64", "aarch64"):' + NL + '            return "arm64"',
     '        if True:' + NL + '            return "arm64"', "py"),
    ("the licence material is not required in the Windows executable", "tools/build_release.py",
     "require_licence_material=onefile)", "require_licence_material=False)", "py"),
    ("a lost licence text is not noticed", "tools/build_release.py",
     "    return [required for required in REQUIRED_IN_PACKAGE if required not in present]", "    return []", "py"),
    ("a universal binary is accepted", "tools/verify_macho_arch.py",
     '        if info["kind"] == "fat":' + NL + '            problems.append("%s is a universal binary (%s), not a thin %s image"',
     '        if False:' + NL + '            problems.append("%s is a universal binary (%s), not a thin %s image"', "py"),
    ("an empty bundle is accepted", "tools/verify_macho_arch.py", "    if found == 0:", "    if False:", "py"),
    ("a missing main executable is accepted", "tools/verify_macho_arch.py", "    if main is not None and not main_seen:", "    if False:", "py"),
    ("a wrong CPU type is accepted", "tools/verify_macho_arch.py",
     '        elif info["arches"] != [expect]:', '        elif False:', "py"),
    ("a fat header is not recognised", "tools/verify_macho_arch.py",
     "    if magic_be in (_FAT, _FAT_64):", "    if False:", "py"),
    ("the settings page drops the no-package sentence", "ui/settings.js",
     "    } else if (data.package_message) {", "    } else if (false) {", "js:version_status_test.js"),
]


def run_py():
    result = subprocess.run([sys.executable, "-m", "unittest", *PY], cwd=root, capture_output=True, text=True)
    return result.returncode, (result.stdout or "") + (result.stderr or "")


def run_js(name):
    result = subprocess.run(["node", "tests/" + name], cwd=root, capture_output=True, text=True)
    return result.returncode, (result.stdout or "") + (result.stderr or "")


def main():
    if "--list" in sys.argv:
        for entry in MUTATIONS:
            print(entry[0])
        return 0
    base_code, base_out = run_py()
    print("baseline (unmutated): exit %d" % base_code)
    if base_code != 0:
        print(base_out[-2000:])
        return 2
    caught, survivors, errors = 0, [], []
    for name, relative, anchor, replacement, runner in MUTATIONS:
        path = root / relative
        original = path.read_bytes()
        text = original.decode("utf-8")
        crlf = CR + NL in text
        a = anchor.replace(NL, CR + NL) if crlf else anchor
        b = replacement.replace(NL, CR + NL) if crlf else replacement
        if text.count(a) != 1:
            errors.append("%s: anchor found %d times" % (name, text.count(a)))
            print("ERROR  %s (anchor found %d times)" % (name, text.count(a)))
            continue
        try:
            path.write_bytes(text.replace(a, b).encode("utf-8"))
            code, output = run_js(runner[3:]) if runner.startswith("js:") else run_py()
        finally:
            path.write_bytes(original)
        failing = [l for l in output.splitlines() if l.startswith(("FAIL:", "ERROR:", "FAILED"))][:3]
        if code != 0:
            caught += 1
            print("caught %s -> %s" % (name, "; ".join(failing)[:160] or "exit %d" % code))
        else:
            survivors.append(name)
            print("SURVIVED %s" % name)
    print("\n%d mutation(s): %d caught, %d survived, %d error(s)" % (len(MUTATIONS), caught, len(survivors), len(errors)))
    for item in survivors + errors:
        print("  - " + item)
    return 1 if survivors or errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
