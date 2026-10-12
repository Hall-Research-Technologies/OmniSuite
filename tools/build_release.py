import argparse
import ast
import hashlib
import os
import platform
import shutil
import subprocess
import plistlib
import sys
import tarfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import omni_release  # noqa: E402
ENTRY = ROOT / "OmniMatrix_upgrade_server_v7_6y.py"
LAUNCHER = ROOT / "app_launcher.py"
VERSION_FILE = ROOT / "VERSION"
LICENSE_FILE = ROOT / "LICENSE"
THIRD_PARTY_FILE = ROOT / "THIRD_PARTY_NOTICES.md"
RELEASE_DIR = ROOT / "release"
APP_ICON = ROOT / "omnimatrix.ico"
HEADER_LOGO = ROOT / "hallway.png"
FOOTER_LOGO = ROOT / "atlona.png"


# Only these may be bundled into the application. The runtime needs the UI it
# serves, the version it reports and the three images the launcher window draws;
# nothing else. Anything absent from this list is a packaging defect, not a
# preference -- see the forbidden list below for what that has already cost.
ALLOWED_DATA = {
    "ui",            # pages, scripts, styles and the User Guide the app serves
    "VERSION",       # reported by /api/config and by the updater
    "LICENSE",       # served at /license and linked from Settings
    "THIRD_PARTY_NOTICES.md",  # the bundled libraries and their own licences
    "omnimatrix.ico",
    "hallway.png",
    "atlona.png",
}

# Directories that must never reach the package, with the reason each is here.
FORBIDDEN_IN_PACKAGE = {
    "firmware": "operator-chosen Settings path holding the manufacturer's images (981 MB build)",
    "archive": "superseded development history",
    "docs": "README screenshots; documentation, not runtime",
    "tests": "test suite and fixtures carrying bench addresses",
    "artifacts": "bench QA baselines and third-party device bundles",
    "release": "build output",
    "dist": "build output",
    "build": "build output",
    ".git": "repository metadata",
    ".github": "CI definitions",
    ".venv": "developer virtualenv",
}

FORBIDDEN_FILES = {
    "config.json": "runtime configuration holding device credentials",
    "units_cache.json": "runtime cache holding bench addresses and MACs",
    "scan_results.json": "runtime scan state",
    "units_view.csv": "generated inventory export",
    "README.md": "GitHub documentation, not an operator-facing runtime file",
}


def assert_data_allowlist(cmd: list[str]) -> None:
    """Check what the build is ABOUT to bundle, before PyInstaller starts."""
    bundled = []
    for index, item in enumerate(cmd):
        if item == "--add-data":
            source = cmd[index + 1].rsplit(os.pathsep if os.pathsep in cmd[index + 1] else ":", 1)[0]
            bundled.append(Path(source).name)
    unexpected = sorted(set(bundled) - ALLOWED_DATA)
    if unexpected:
        raise SystemExit(
            f"[build] refusing to package unexpected data: {unexpected}. "
            f"Add it to ALLOWED_DATA only if the running application needs it."
        )
    print(f"[build] data allowlist OK: {sorted(set(bundled))}")


def assert_package_contents(work_path: Path) -> None:
    """Read back what PyInstaller actually collected.

    The allowlist above governs --add-data, but a directory can also arrive by
    being swept up inside an allowed one -- which is exactly how fifteen
    superseded pages shipped inside ui/. This reads the real table of contents.
    """
    names = package_destinations(work_path)
    if not names:
        raise SystemExit("[build] no PyInstaller TOC found; cannot verify package contents")
    problems = []
    for name in names:
        parts = name.replace("\\", "/").split("/")
        for part in parts[:-1]:
            if part in FORBIDDEN_IN_PACKAGE:
                problems.append(f"{part}/ ({FORBIDDEN_IN_PACKAGE[part]}) e.g. {name}")
        if parts[-1] in FORBIDDEN_FILES:
            problems.append(f"{parts[-1]} ({FORBIDDEN_FILES[parts[-1]]})")
    if problems:
        raise SystemExit("[build] forbidden content reached the package: " + "; ".join(sorted(set(problems))))
    print(f"[build] package contents verified: {len(names)} entries, none under "
          f"{len(FORBIDDEN_IN_PACKAGE)} forbidden directories or matching "
          f"{len(FORBIDDEN_FILES)} forbidden files")


def package_destinations(work_path: Path) -> list[str]:
    """The names files will have INSIDE the application.

    Only the destination matters. A TOC entry's second element is the path on
    this build machine, which legitimately contains .venv and build -- matching
    against the raw file text reports those as contraband.
    """
    names: list[str] = []
    # PKG is the archive actually embedded in the executable. Analysis is not
    # read here: it lists the generated runtime hook by its absolute path on
    # this machine, which sits under build/ and is not packaged under that name.
    for toc in sorted(work_path.rglob("PKG-*.toc")):
        try:
            entries = ast.literal_eval(toc.read_text(encoding="utf-8", errors="ignore"))
        except (ValueError, SyntaxError):
            continue
        collect_toc_rows(entries, names)
    return names


# The type codes PyInstaller puts in a TOC row's third field. A row is
# recognised by shape rather than by position, because a PKG TOC nests the
# rows inside an outer tuple alongside the package path and a flags mapping.
TOC_TYPECODES = {"DATA", "BINARY", "EXECUTABLE", "EXTENSION", "PYMODULE",
                 "PYSOURCE", "PYZ", "SPLASH", "SYMLINK", "OPTION"}


def collect_toc_rows(node, names: list[str]) -> None:
    """Gather every packaged destination name from a parsed TOC, at any depth."""
    if isinstance(node, (tuple, list)):
        if (len(node) == 3 and isinstance(node[0], str) and isinstance(node[2], str)
                and node[2] in TOC_TYPECODES):
            # An absolute name is a build-machine source path (the generated
            # runtime hook), not a name anything will have inside the package.
            if not Path(node[0]).is_absolute():
                names.append(node[0])
            return
        for item in node:
            collect_toc_rows(item, names)


def read_version(cli_version: str | None) -> str:
    if cli_version:
        return cli_version.strip()

    env_version = (os.getenv("RELEASE_VERSION") or os.getenv("GITHUB_REF_NAME") or "").strip()
    if env_version:
        return env_version

    if VERSION_FILE.exists():
        text = VERSION_FILE.read_text(encoding="utf-8").strip()
        if text:
            return text

    return "V0.0.0"


def run(cmd: list[str]) -> None:
    print("[build]", " ".join(cmd))
    subprocess.check_call(cmd, cwd=str(ROOT))


def clean_paths(paths: list[Path]) -> None:
    for path in paths:
        if path.exists():
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()


def zip_path(source: Path, target_zip: Path) -> None:
    target_zip.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        if source.is_file():
            zf.write(source, arcname=source.name)
            return

        for item in source.rglob("*"):
            if item.is_file():
                zf.write(item, arcname=item.relative_to(source.parent))


def tar_path(source: Path, target_tgz: Path) -> None:
    target_tgz.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(target_tgz, "w:gz") as tf:
        tf.add(source, arcname=source.name)


def zip_macos_app(app_bundle: Path, target_zip: Path) -> None:
    target_zip.parent.mkdir(parents=True, exist_ok=True)
    if shutil.which("ditto"):
        subprocess.check_call([
            "ditto",
            "-c",
            "-k",
            "--sequesterRsrc",
            "--keepParent",
            str(app_bundle),
            str(target_zip),
        ])
        return

    # Fallback for non-mac environments.
    zip_path(app_bundle, target_zip)


def declare_local_network_usage(app_bundle: Path, usage: str, display_name: str = None) -> None:
    """Add NSLocalNetworkUsageDescription to a built .app, then sign it again.

    macOS asks the person before an app may reach devices on the local network,
    and this is the sentence it shows. PyInstaller writes the bundle's Info.plist
    without it, and its command line has no way to add one.

    Editing Info.plist invalidates the ad-hoc signature PyInstaller applied, and
    Apple Silicon will not run code whose signature does not verify, so the
    bundle is signed again -- ad hoc, as before; no signing identity is involved
    -- and the result is verified. A failure here fails the build: shipping a
    bundle that will not launch is worse than shipping none.

    No Bonjour service types are declared. The application does not use Bonjour
    or mDNS; it opens direct TCP connections and sends UDP broadcasts, and
    declaring services it never browses would describe behaviour it does not have.
    """
    if not usage.strip():
        raise RuntimeError("no Local Network usage description for this product")
    plist_path = app_bundle / "Contents" / "Info.plist"
    with plist_path.open("rb") as handle:
        info = plistlib.load(handle)
    info["NSLocalNetworkUsageDescription"] = usage
    if display_name:
        # What System Settings lists under Local Network. Without this both
        # products appear as "OmniSuite" and cannot be told apart.
        info["CFBundleDisplayName"] = display_name
        info["CFBundleName"] = display_name
    with plist_path.open("wb") as handle:
        plistlib.dump(info, handle)
    run(["codesign", "--force", "--deep", "--sign", "-", str(app_bundle)])
    run(["codesign", "--verify", "--deep", "--strict", str(app_bundle)])
    print(f"[build] Local Network usage description declared in {plist_path}")


MACOS_README_NAME = "READ-ME-FIRST-macOS.txt"

_MACOS_README = """{title}
{rule}

{name} is not signed or notarized by Apple, so macOS blocks it the first time
you open it. That is expected. This page shows how to approve it.

BEFORE YOU APPROVE ANYTHING
  Only approve {name} if you downloaded it from the official project releases
  page:

      {url}

  The file name should start with "{prefix}". If it came from anywhere else,
  delete it. Each release also lists SHA-256 checksums in SHA256SUMS.txt. To
  check yours, open Terminal in the folder with both files and run
  shasum -a 256 followed by your zip file's name, then compare the result with
  the matching line in SHA256SUMS.txt.

FIRST LAUNCH
  1. Double-click the zip in Finder to unpack it. Use Finder: some other unzip
     tools damage the app.
  2. Move OmniSuite.app to your Applications folder if you like.
  3. Open OmniSuite.app. macOS says it cannot verify the app. Click Done. Do not
     click Move to Trash.
  4. Open System Settings, then Privacy & Security. Scroll down to the
     Security section. It says "OmniSuite" was blocked and shows an
     Open Anyway button. Click Open Anyway, enter your password, then
     click Open.
     The button is only there for a while after the blocked attempt. If you do
     not see it, open the app again and look again.
  On macOS 14 (Sonoma) and earlier you can also Control-click the app and
  choose Open.

  You only need to do this once for each copy you download. A new download may
  ask again.

LOCAL NETWORK ACCESS
  The first time {name} looks for devices, macOS asks whether it may find and
  connect to devices on your local network. Choose Allow.

  If you chose Don't Allow, scans will report that they could not reach the
  network. To fix it, open System Settings, then Privacy & Security, then
  Local Network, switch on {name}, and scan again.

WHAT NOT TO DO
  Do not turn off Gatekeeper, and do not run commands that remove the
  quarantine flag. Approve this one app, once, and only if it came from the
  official releases page above.

LATER
  Future releases are intended to be signed with an Apple Developer ID and
  notarized, which will remove the Open Anyway step.

More help is in the User Guide: open Settings, or visit /help while {name} is
running.
"""


def macos_readme_text(product) -> str:
    """The first-launch instructions that travel inside the macOS download."""
    title = f"{product.name} for macOS - read this first"
    return _MACOS_README.format(
        title=title, rule="=" * len(title), name=product.name,
        url=omni_release.RELEASES_URL, prefix=product.artifact_prefix)


def add_macos_readme(target_zip: Path, product) -> None:
    """Add the first-launch instructions beside the app, inside the zip.

    Appended to the archive that already exists rather than staged beside the app,
    so the bundle's symlinks and permissions are never copied or rewritten.
    """
    with zipfile.ZipFile(target_zip, "a", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(MACOS_README_NAME, macos_readme_text(product))


def build_binary(dist_path: Path, work_path: Path, suffix: str) -> None:
    data_sep = ";" if os.name == "nt" else ":"
    onefile = suffix == "windows"
    mac_bundle = suffix in {"x86_64", "arm64"}
    gui_app = True
    version = read_version(None)

    work_path.mkdir(parents=True, exist_ok=True)
    runtime_hook = work_path / "omni_version_runtime_hook.py"
    runtime_hook.write_text(
        "import os\n"
        f"os.environ.setdefault('OMNI_VERSION', {version!r})\n",
        encoding="utf-8",
    )

    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile" if onefile else "--onedir",
        "--windowed" if gui_app else "--console",
        "--name",
        "OmniSuite",
        "--distpath",
        str(dist_path),
        "--workpath",
        str(work_path),
        "--specpath",
        str(work_path),
        "--add-data",
        f"{ROOT / 'ui'}{data_sep}ui",
        "--add-data",
        f"{VERSION_FILE}{data_sep}.",
        "--hidden-import",
        "PIL",
        "--hidden-import",
        "PIL.Image",
        "--hidden-import",
        "PIL.ImageTk",
        "--hidden-import",
        "psutil",
        "--collect-submodules",
        "PIL",
        "--collect-data",
        "PIL",
    ]

    # The application serves its own licence at /license, and the notices for the
    # libraries bundled into this executable have to accompany it.
    for document in (LICENSE_FILE, THIRD_PARTY_FILE):
        if document.exists():
            cmd.extend(["--add-data", f"{document}{data_sep}."])

    if APP_ICON.exists():
        cmd.append(f"--icon={APP_ICON}")
        cmd.extend(["--add-data", f"{APP_ICON}{data_sep}."])

    cmd.extend(["--runtime-hook", str(runtime_hook)])

    if HEADER_LOGO.exists():
        cmd.extend(["--add-data", f"{HEADER_LOGO}{data_sep}."])

    if FOOTER_LOGO.exists():
        cmd.extend(["--add-data", f"{FOOTER_LOGO}{data_sep}."])

    if mac_bundle:
        cmd.extend(["--target-architecture", suffix])

    # The firmware folder is deliberately NOT bundled. It is an operator choice
    # made in Settings at runtime, and the images in it are the manufacturer's.
    # Adding it produced a 981 MB executable on a machine that happened to have
    # firmware present, 908 MB of which was firmware.

    if suffix == "windows":
        cmd.extend(["--hidden-import", "pystray"])
        cmd.extend(["--hidden-import", "pystray._win32"])
    elif suffix == "linux":
        cmd.extend(["--hidden-import", "pystray"])
        cmd.extend(["--hidden-import", "pystray._xorg"])

    cmd.append(str(LAUNCHER))

    assert_data_allowlist(cmd)
    run(cmd)
    assert_package_contents(work_path)


def resolve_suffix(cli_suffix: str | None) -> str:
    if cli_suffix:
        return cli_suffix

    system = platform.system().lower()
    machine = platform.machine().lower()

    if system == "windows":
        return "windows"
    if system == "linux":
        return "linux"
    if system == "darwin":
        return "arm64" if "arm" in machine else "x86_64"
    raise RuntimeError(f"Unsupported platform: {system}")


# The name a download is judged by. Each one states the platform AND the
# architecture, because "windows" alone does not say x86-64 and "linux" claims
# more than Ubuntu is the only environment built and tested.
#
# These strings are matched by _select_platform_asset in the server, which
# offers the running platform its own build from a GitHub release. Changing a
# name here without changing that matcher would offer someone the wrong
# download, so ArtifactNamingTests pins the two together.
ARTIFACT_NAMES = {
    "windows": "{prefix}{version}-Windows-x86_64.zip",
    "arm64": "{prefix}{version}-macOS-arm64.zip",
    "x86_64": "{prefix}{version}-macOS-x86_64.zip",
    "linux": "{prefix}{version}-Ubuntu-x86_64.tar.gz",
}


def package_release(dist_path: Path, suffix: str, version: str) -> Path:
    RELEASE_DIR.mkdir(parents=True, exist_ok=True)
    # The prefix is the product's, so OmniSuite and OmniSuite Plus artifacts can
    # never share a name. See omni_release for the two identities.
    artifact = RELEASE_DIR / ARTIFACT_NAMES[suffix].format(
        prefix=omni_release.active_product().artifact_prefix, version=version)

    if suffix == "windows":
        windows_exe = dist_path / "OmniSuite.exe"
        if not windows_exe.exists():
            raise RuntimeError(f"Expected build output not found: {windows_exe}")
        zip_path(windows_exe, artifact)
        return artifact

    if suffix in {"x86_64", "arm64"}:
        app_bundle = dist_path / "OmniSuite.app"
        if not app_bundle.exists():
            raise RuntimeError(f"Expected build output not found: {app_bundle}")
        product = omni_release.active_product()
        declare_local_network_usage(app_bundle, product.local_network_usage, product.name)
        zip_macos_app(app_bundle, artifact)
        add_macos_readme(artifact, product)
        return artifact

    bundle_dir = dist_path / "OmniSuite"
    if not bundle_dir.exists():
        raise RuntimeError(f"Expected build output not found: {bundle_dir}")

    if suffix == "linux":
        tar_path(bundle_dir, artifact)
        return artifact

    raise RuntimeError(f"Unsupported suffix: {suffix}")


def write_checksum(artifact: Path) -> str:
    """SHA-256 of the archive that is actually distributed.

    Taken from the final archive rather than from the executable inside it, so
    the value a user can verify against their download is the value published.
    Each job writes one line; CI concatenates them into SHA256SUMS.txt.
    """
    digest = hashlib.sha256()
    with open(artifact, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    hexdigest = digest.hexdigest()
    # The `sha256sum -c` format, so verification needs no special tooling.
    artifact.with_suffix(artifact.suffix + ".sha256").write_text(
        f"{hexdigest}  {artifact.name}\n", encoding="utf-8")
    return hexdigest


def main() -> int:
    parser = argparse.ArgumentParser(description="Build and package OmniSuite release artifacts.")
    parser.add_argument("--suffix", choices=["windows", "linux", "x86_64", "arm64"], default=None)
    parser.add_argument("--version", default=None)
    args = parser.parse_args()

    if not ENTRY.exists():
        raise RuntimeError(f"Entry script not found: {ENTRY}")
    if not LAUNCHER.exists():
        raise RuntimeError(f"Launcher script not found: {LAUNCHER}")

    suffix = resolve_suffix(args.suffix)
    version = read_version(args.version)

    dist_path = ROOT / "dist" / suffix
    work_path = ROOT / "build" / suffix

    clean_paths([dist_path, work_path, RELEASE_DIR])
    build_binary(dist_path=dist_path, work_path=work_path, suffix=suffix)
    artifact = package_release(dist_path=dist_path, suffix=suffix, version=version)
    digest = write_checksum(artifact)

    print(f"[release] Created {artifact}")
    print(f"[release] SHA-256 {digest}  {artifact.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
