#!/usr/bin/env python3
"""Inspect a built release archive before it can be offered to anyone.

    inspect_release.py ARCHIVE [ARCHIVE ...] --version V1.2.0 [--diagnostic]

It fails closed: any problem exits non-zero with a sentence per problem. It checks what is in
the package, not what the build intended to put there:

  * the archive name is this product's, for exactly this version, with no pre-release label
    (the one exception is --diagnostic, below);
  * the licence and the third-party notices are present;
  * nothing that must never ship is present: tests, tools, firmware images, key material, a user or
    configuration file, backups, logs, reports, version-control data, a second release archive;
  * text members do not name the private product, a private branch, a lab-range address, a
    credential, or a private-key block;
  * the executable carries no private product name and no build-machine path.

--diagnostic is for a workflow run that can never publish. It accepts ONE additional identity,
V<release>-diag<N> (for example V1.2.0-diag1), named OmniSuite-V1.2.0-diag1-<platform>, so a
rehearsal can run the same content checks as a release. It relaxes the name and nothing else, and
it changes nothing for any other version: a release version, an rc label or a stray suffix is
judged exactly as before whether or not the flag is given.

It reads archives; it never extracts or runs anything.
"""
from __future__ import annotations

import argparse
import io
import re
import sys
import tarfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import omni_release                                   # noqa: E402
import pre_push_guard                                  # noqa: E402

_FORBIDDEN_NAME = re.compile("|".join([
    r"(^|/)tests?/", r"(^|/)tools/", r"(^|/)\.git(/|$)", r"(^|/)firmware/", r"(^|/)overnight_rc/",
    r"(^|/)(users|config|units_cache|scan_results)\.json$", r"\.(key|omnibak|pfx|p12|log|zip|tar|gz)$",
    r"(^|/)(PROJECT|AGENTS|TESTING|DEVELOPMENT)\.md$", r"(^|/)[^/]*(soak|mutation|qualification)[^/]*$",
    r"(^|/)__pycache__/",
]), re.IGNORECASE)
# A public CA bundle is not key material; these licence texts are the only .pem allowance.
_LABEL_OK_PATH = re.compile(r"^(third_party_licenses/|.*/third_party_licenses/)")
# The corresponding source of the LGPL component ships unmodified, so its own tests/ and tools/
# folders are part of it and are not a leak.
_UPSTREAM_SOURCE = re.compile(r"^(.*/)?third_party/pystray-0\.19\.5/")
# What the LGPL and the approved licensing decision require to travel with every package.
REQUIRED_NAMES = ("REBUILD.md", "third_party/PYSTRAY_PROVENANCE.md",
                  "third_party/pystray-0.19.5/COPYING", "third_party/pystray-0.19.5/COPYING.LGPL",
                  "third_party/pystray-0.19.5/lib/pystray/__init__.py",
                  "third_party_licenses/pystray/COPYING", "third_party_licenses/pystray/COPYING.LGPL")
# The only non-release identity a package may carry: a rehearsal build, which can never publish.
# \Z, not $: a version with a trailing newline is not a diagnostic version.
_DIAGNOSTIC_VERSION = re.compile(r"V\d+(?:\.\d+){1,3}-diag[0-9]{1,3}\Z")
_PEM = re.compile(r"\.pem$")
_SECRET = re.compile(rb"-----BEGIN [A-Z ]*PRIVATE KEY-----|ghp_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}"
                     rb"|xox[baprs]-[A-Za-z0-9-]{10,}|AKIA[0-9A-Z]{16}")
_BENCH = re.compile(rb"192\.168\.(100|150|200|250)\.\d{1,3}")
_BUILD_PATH = re.compile(rb"[A-Za-z]:\\+(softwareDEV|Users\\+Wylie)|overnight_rc|/Users/[A-Za-z]+/softwareDEV",
                         re.IGNORECASE)
_TEXT_SUFFIX = {".md", ".txt", ".html", ".css", ".js", ".json", ".py", ".yml", ".yaml", ".toml", ".cfg"}
_NAME_RE = re.compile(pre_push_guard.CONTENT_PATTERN.encode(), re.IGNORECASE)


def members(path: Path):
    """(name, bytes-or-None-for-directories) for every member, without extracting anything."""
    name = path.name.lower()
    if name.endswith(".zip"):
        with zipfile.ZipFile(path) as archive:
            for info in archive.infolist():
                if not info.is_dir():
                    yield info.filename, archive.read(info)
    elif name.endswith((".tar.gz", ".tgz")):
        with tarfile.open(path, "r:gz") as archive:
            for info in archive:
                if info.isfile():
                    yield info.name, archive.extractfile(info).read()
    else:
        raise ValueError("%s is not a release archive" % path.name)


def _is_diagnostic_archive(name: str, version: str, product) -> bool:
    """A rehearsal archive: a diagnostic version, and exactly this product's name for it."""
    if not _DIAGNOSTIC_VERSION.fullmatch(version or ""):
        return False
    head = product.artifact_prefix + version + "-"
    return name.startswith(head) and name[len(head):] in omni_release.PLATFORM_TAILS


def problems_for(path: Path, version: str, product=None, diagnostic: bool = False) -> list:
    product = product or omni_release.PRODUCTS["omnisuite"]
    found = []
    name = path.name
    parsed = product.parse_asset(name)
    tail = name.split("-", 2)[-1]
    exact = False
    if parsed and product.parse_tag(version.strip()) is not None:
        exact = name == product.artifact_name(version.strip(), tail) and tail in omni_release.PLATFORM_TAILS
    identity_ok = (parsed and exact and "V" + parsed["version"] == version.strip()
                   and product.parse_tag(version.strip()) is not None)
    if diagnostic and _DIAGNOSTIC_VERSION.fullmatch(version or ""):
        identity_ok = _is_diagnostic_archive(name, version, product)
    if not identity_ok:
        found.append("%s is not this product's release archive for exactly %s (a pre-release or "
                     "build label is not an identity)" % (name, version))
    try:
        listing = list(members(path))
    except Exception as error:                         # a corrupt archive is a finding, not a crash
        return found + ["%s cannot be read: %s" % (name, error)]
    names = [n for n, _ in listing]
    if not any(re.search(r"(^|/)LICENSE$", n) for n in names):
        found.append("%s has no LICENSE" % name)
    if not any(re.search(r"(^|/)THIRD_PARTY_NOTICES\.md$", n) for n in names):
        found.append("%s has no THIRD_PARTY_NOTICES.md" % name)
    for required in REQUIRED_NAMES:
        if not any(n == required or n.endswith("/" + required) for n in names):
            found.append("%s has no %s (required by the LGPL component and the licence)" % (name, required))
    licence = [d for n, d in listing if re.search(r"(^|/)LICENSE$", n)]
    marker = b"11A. COMPONENTS LICENSED UNDER THE GNU LESSER GENERAL PUBLIC LICENSE"
    if licence and marker not in licence[0].replace(bytes([13, 10]), bytes([10])):
        found.append("%s: LICENSE does not carry section 11A" % name)
    for member, data in listing:
        if _UPSTREAM_SOURCE.match(member):
            if _SECRET.search(data):
                found.append("%s: %s contains a credential or private-key block" % (name, member))
            continue
        if _FORBIDDEN_NAME.search(member) and not _LABEL_OK_PATH.match(member):
            found.append("%s: %s must not ship" % (name, member))
        if _PEM.search(member) and not _LABEL_OK_PATH.match(member):
            found.append("%s: %s looks like key material" % (name, member))
        is_text = Path(member).suffix.lower() in _TEXT_SUFFIX or Path(member).name in ("LICENSE", "VERSION")
        if is_text:
            if _SECRET.search(data):
                found.append("%s: %s contains a credential or private-key block" % (name, member))
            if _NAME_RE.search(data):
                found.append("%s: %s names the private product" % (name, member))
            if _BENCH.search(data):
                found.append("%s: %s names a lab address" % (name, member))
            if _BUILD_PATH.search(data):
                found.append("%s: %s names a build-machine path" % (name, member))
        elif len(data) > 100_000:                      # an executable or bundle
            if _NAME_RE.search(data):
                found.append("%s: %s contains the private product's name" % (name, member))
            if _BUILD_PATH.search(data):
                found.append("%s: %s contains a build-machine path" % (name, member))
    return found


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archives", nargs="+", type=Path)
    parser.add_argument("--version", required=True)
    parser.add_argument("--diagnostic", action="store_true",
                        help="accept the rehearsal identity V<release>-diag<N>; nothing else is relaxed")
    args = parser.parse_args(argv)
    bad = []
    for path in args.archives:
        found = problems_for(path, args.version, diagnostic=args.diagnostic)
        print("%s: %s" % (path.name, "OK" if not found else "%d problem(s)" % len(found)))
        bad += found
    for line in bad:
        print("::error::" + line if "GITHUB_ACTIONS" in __import__("os").environ else "  " + line)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
