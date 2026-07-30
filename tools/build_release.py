import argparse
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENTRY = ROOT / "OmniMatrix_upgrade_server_v7_6y.py"
LAUNCHER = ROOT / "app_launcher.py"
VERSION_FILE = ROOT / "VERSION"
RELEASE_DIR = ROOT / "release"
APP_ICON = ROOT / "omnimatrix.ico"
HEADER_LOGO = ROOT / "hallway.png"
FOOTER_LOGO = ROOT / "atlona.png"


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


def build_binary(dist_path: Path, work_path: Path, suffix: str) -> None:
    data_sep = ";" if os.name == "nt" else ":"
    firmware_dir = ROOT / "firmware"
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
        "pystray",
        "--hidden-import",
        "psutil",
        "--collect-submodules",
        "PIL",
        "--collect-data",
        "PIL",
    ]

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

    if firmware_dir.exists():
        cmd.extend([
            "--add-data",
            f"{firmware_dir}{data_sep}firmware",
        ])

    if suffix == "windows":
        cmd.extend(["--hidden-import", "pystray._win32"])
    elif suffix in {"x86_64", "arm64"}:
        cmd.extend(["--hidden-import", "pystray._darwin"])
    elif suffix == "linux":
        cmd.extend(["--hidden-import", "pystray._xorg"])

    cmd.append(str(LAUNCHER))

    run(cmd)


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


def package_release(dist_path: Path, suffix: str, version: str) -> Path:
    if suffix == "windows":
        windows_exe = dist_path / "OmniSuite.exe"
        if not windows_exe.exists():
            raise RuntimeError(f"Expected build output not found: {windows_exe}")

        RELEASE_DIR.mkdir(parents=True, exist_ok=True)
        artifact = RELEASE_DIR / f"OmniSuite-{version}-windows.zip"
        zip_path(windows_exe, artifact)
        return artifact

    RELEASE_DIR.mkdir(parents=True, exist_ok=True)

    if suffix in {"x86_64", "arm64"}:
        app_bundle = dist_path / "OmniSuite.app"
        if not app_bundle.exists():
            raise RuntimeError(f"Expected build output not found: {app_bundle}")

        artifact = RELEASE_DIR / f"OmniSuite-{version}-MAC-{suffix}.zip"
        zip_macos_app(app_bundle, artifact)
        return artifact

    bundle_dir = dist_path / "OmniSuite"
    if not bundle_dir.exists():
        raise RuntimeError(f"Expected build output not found: {bundle_dir}")

    if suffix == "linux":
        artifact = RELEASE_DIR / f"OmniSuite-{version}-linux.tar.gz"
        tar_path(bundle_dir, artifact)
        return artifact

    raise RuntimeError(f"Unsupported suffix: {suffix}")


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

    print(f"[release] Created {artifact}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
