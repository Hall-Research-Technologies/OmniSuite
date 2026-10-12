"""Break each release-isolation rule on purpose; the suite must fail every time.

Usage: python tools/mutate_release_isolation.py <repository root>.  Restores every file in a finally block, and
translates anchors to the file's own line endings.
"""
import subprocess
import sys
from pathlib import Path

root = Path(sys.argv[1])
CR, NL = chr(13), chr(10)
BS = chr(92)

MUTATIONS = [
    ("tag regex accepts a trailing newline", "omni_release.py",
     BS + "Z\")\n        # The version in an artifact", "$\")\n        # The version in an artifact"),
    ("select_release ignores product ownership", "omni_release.py",
     "        if owners[0] is not product:", "        if False:"),
    ("select_release offers prereleases", "omni_release.py",
     '        if release.get("draft") or release.get("prerelease"):', "        if False:"),
    ("select_release picks the first, not the highest", "omni_release.py",
     "if best is None or version_tuple(core) > version_tuple(best[1]):", "if best is None:"),
    ("mixed-asset releases are accepted", "omni_release.py",
     "        if _names_other_product(release.get(\"assets\"), product):", "        if False:"),
    ("asset selection stops checking the product", "OmniMatrix_upgrade_server_v7_6y.py",
     "        parsed = product.parse_asset(name)\n        if not parsed or parsed[\"platform\"] != key:",
     "        parsed = omni_release.PRODUCTS[\"omnisuite\"].parse_asset(name) or omni_release.PRODUCTS[\"omnisuite-plus\"].parse_asset(name)\n        if not parsed or parsed[\"platform\"] != key:"),
    ("asset version disagreement is ignored", "OmniMatrix_upgrade_server_v7_6y.py",
     "        if version is not None and (omni_release.version_tuple(parsed[\"version\"])",
     "        if False and (omni_release.version_tuple(parsed[\"version\"])"),
    ("updater uses the repository-wide latest endpoint", "OmniMatrix_upgrade_server_v7_6y.py",
     "releases?per_page=100\"", "releases/latest\""),
    ("guard skips the branch-ancestry check", "tools/release_identity.py",
     "    if not is_ancestor(product.release_branch):", "    if False:"),
    ("guard accepts the other product's tag", "tools/release_identity.py",
     "    if owners[0] is not product:", "    if False:"),
    ("guard stops comparing the tag with VERSION", "tools/release_identity.py",
     "    if omni_release.version_tuple(file_core) != omni_release.version_tuple(core):", "    if False:"),
    ("guard does not require release notes", "tools/release_identity.py",
     "    if not notes_exist(notes):", "    if False:"),
    ("a dispatch is allowed to publish", "tools/release_identity.py",
     "\"publish\": \"false\",", "\"publish\": \"true\","),
    ("a bare release version is allowed for a dispatch", "tools/release_identity.py",
     "    else:\n        raise IdentityError(\n            \"%r would be indistinguishable", "    else:\n        return {}\n        raise IdentityError(\n            \"%r would be indistinguishable"),
    ("an unsafe dispatch version is allowed", "tools/release_identity.py",
     "    if not _SAFE_VERSION.match(version):", "    if False:"),
    ("a foreign artifact passes verification", "tools/release_identity.py",
     "        if foreign:", "        if False:"),
    ("workflow publishes on any ref", ".github/workflows/build-cross-platform.yml",
     " && github.ref_type == 'tag'", ""),
    ("workflow collects every product's artifacts", ".github/workflows/build-cross-platform.yml",
     "pattern: release-${{ needs.identity.outputs.product }}-*", "pattern: release-*"),
    ("workflow hard-codes release notes", ".github/workflows/build-cross-platform.yml",
     "body_path: ${{ needs.identity.outputs.notes_path }}", "body_path: RELEASE_NOTES_V1.1.0.md"),
    ("workflow interpolates the dispatch version", ".github/workflows/build-cross-platform.yml",
     "          VERIFIED_VERSION: ${{ needs.identity.outputs.release_version }}\n        run: |",
     "          VERIFIED_VERSION: ${{ needs.identity.outputs.release_version }}\n          X: ${{ github.event.inputs.release_version }}\n        run: |"),
]


def run_suite():
    proc = subprocess.run(
        [sys.executable, "-m", "unittest", "tests.test_release_isolation"],
        cwd=str(root), capture_output=True, text=True)
    return proc.returncode, (proc.stderr or "").strip().splitlines()[-1:] or [""]


baseline, last = run_suite()
print("baseline:", "PASS" if baseline == 0 else "FAIL", last)
assert baseline == 0, "the suite must be green before mutating"

survivors = []
for label, filename, old, new in MUTATIONS:
    path = root / filename
    original = path.read_bytes()
    text = original.decode("utf-8")
    crlf = (CR + NL) in text
    work = text.replace(CR + NL, NL)
    if work.count(old) != 1:
        print("ANCHOR-ERROR %-52s matched %d times" % (label, work.count(old)))
        survivors.append(label + " (anchor)")
        continue
    mutated = work.replace(old, new)
    try:
        path.write_bytes((mutated.replace(NL, CR + NL) if crlf else mutated).encode("utf-8"))
        code, last = run_suite()
    finally:
        path.write_bytes(original)
    verdict = "caught" if code != 0 else "SURVIVED"
    print("%-8s %s" % (verdict, label))
    if code == 0:
        survivors.append(label)

print()
print("survivors:", survivors or "none")
sys.exit(1 if survivors else 0)
