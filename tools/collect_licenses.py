"""Collect the licence texts of the third-party components OmniSuite ships.

The packaged application embeds these libraries, and most licences require their
text to accompany a distribution. A wheel's own metadata does not travel with a
PyInstaller build (only a few `*.dist-info/METADATA` files do, and not their
licence files), so the texts are gathered here, committed under `third_party_licenses/`, and
bundled by `tools/build_release.py`.

    python tools/collect_licenses.py                      # from the running environment
    python tools/collect_licenses.py --wheel some.whl ... # from downloaded wheels

Run it with the locked environment (requirements-lock.txt) so the texts are the
ones for the pinned versions, then review the diff. It copies files byte for byte
and invents nothing: a component with no licence file is reported, not guessed.

This module is also the single list of what is considered shipped, which
`tests/test_third_party_notices.py` holds the notice file and the bundle to.
"""
from __future__ import annotations

import argparse
import re
import shutil
import sys
import zipfile
from importlib import metadata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LICENSES_DIR = ROOT / "third_party_licenses"

# Distributions whose code is in the packaged application, established from the
# PyInstaller table of contents of a Windows build (the Python modules in the
# archive, plus the extension modules and DLLs beside it). Names are normalised
# the way pip normalises them. `cffi` is here because `_cffi_backend` is bundled;
# `pycparser`, which cffi needs only to build, is not.
SHIPPED = (
    "blinker", "certifi", "cffi", "charset-normalizer", "click", "cryptography",
    "et-xmlfile", "flask", "idna", "itsdangerous", "jinja2", "markdown-it-py",
    "markupsafe", "mdurl", "openpyxl", "packaging", "pillow", "psutil", "pygments",
    "pyinstaller", "pyinstaller-hooks-contrib", "pystray", "requests", "rich",
    "setuptools", "six", "urllib3", "websocket-client", "werkzeug",
)

# Not in the lock because pip resolves them from a platform marker, so they are
# not pinned. pystray needs python-xlib on Linux and pyobjc on macOS. They are
# listed as conditional: whether a given build embeds them depends on what pip
# resolved on that runner, and nothing here claims more than that.
CONDITIONAL = ("python-xlib", "pyobjc-framework-cocoa", "pyobjc-framework-quartz")

# Conditional and declaring a licence in its metadata, but its wheel carries no
# licence file. It is named in the notices with what it declares; its project's
# text is the one in pyobjc-framework-cocoa.
CONDITIONAL_NO_TEXT = ("pyobjc-core",)

# In the lock, but build-time only: nothing of theirs is embedded.
BUILD_ONLY = ("pycparser",)

# The Python runtime PyInstaller embeds is licensed on its own terms, and its text
# is kept beside the others. The folder name is not a distribution.
RUNTIME_FOLDERS = ("python",)

_LICENCE_NAME = re.compile(r"(licen[cs]e|copying|notice|copyright)", re.I)


def normalise(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _wanted(relative: str) -> bool:
    """A licence file at the root of a dist-info, or anything under `third_party_licenses/`."""
    parts = relative.replace("\\", "/").split("/")
    if len(parts) >= 2 and parts[0] == "third_party_licenses":
        return True
    return len(parts) == 1 and bool(_LICENCE_NAME.search(parts[0]))


def _clear(target: Path) -> None:
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)


def collect_installed(name: str, out: Path) -> list[str]:
    dist = metadata.distribution(name)
    target = out / normalise(dist.metadata["Name"])
    copied = []
    for entry in dist.files or []:
        relative = str(entry)
        base = relative.replace("\\", "/")
        if ".dist-info/" not in base:
            continue
        head, inner = base.split(".dist-info/", 1)
        if not _wanted(inner):
            continue
        if "/" in head:
            # A copy vendored inside the package (setuptools carries several). It
            # keeps its own folder rather than overwriting the package's own text.
            inner = "vendored/" + head.rsplit("/", 1)[1] + "/" + inner
        if not copied:
            _clear(target)
        destination = target / inner
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(dist.locate_file(entry), destination)
        copied.append(inner)
    return copied


def collect_wheel(wheel: Path, out: Path) -> tuple[str, list[str]]:
    copied = []
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        info = next((n for n in names if n.split("/")[0].endswith(".dist-info")), None)
        if info is None:
            return wheel.name, []
        distribution = info.split("/")[0][: -len(".dist-info")].rsplit("-", 1)[0]
        target = out / normalise(distribution)
        for entry in names:
            if not entry.startswith(info.split("/")[0] + "/") or entry.endswith("/"):
                continue
            inner = entry.split("/", 1)[1]
            if not _wanted(inner):
                continue
            if not copied:
                _clear(target)
            destination = target / inner
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(archive.read(entry))
            copied.append(inner)
    return normalise(distribution), copied


def collect_python(out: Path) -> list[str]:
    source = Path(sys.base_prefix) / "LICENSE.txt"
    if not source.is_file():
        return []
    target = out / "python"
    _clear(target)
    shutil.copyfile(source, target / "LICENSE.txt")
    return ["LICENSE.txt"]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--wheel", nargs="*", default=[], help="collect from these wheel files")
    parser.add_argument("--out", default=str(LICENSES_DIR))
    parser.add_argument("--no-installed", action="store_true")
    args = parser.parse_args(argv)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    problems = []
    if not args.no_installed:
        for name in SHIPPED:
            try:
                copied = collect_installed(name, out)
            except metadata.PackageNotFoundError:
                problems.append(f"{name}: not installed in this environment")
                continue
            print(f"{name}: {', '.join(copied) or 'NO LICENCE FILE'}")
            if not copied:
                problems.append(f"{name}: no licence file in its metadata")
        print("python: " + (", ".join(collect_python(out)) or "NO LICENCE FILE"))
    for wheel in args.wheel:
        name, copied = collect_wheel(Path(wheel), out)
        print(f"{name}: {', '.join(copied) or 'NO LICENCE FILE'}")
        if not copied:
            problems.append(f"{name}: no licence file in the wheel")
    for line in problems:
        print("PROBLEM", line)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
