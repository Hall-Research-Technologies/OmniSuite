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
VERSION_FILE = ROOT / "VERSION"
RELEASE_DIR = ROOT / "release"


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


def build_binary(dist_path: Path, work_path: Path) -> None:
    data_sep = ";" if os.name == "nt" else ":"
    firmware_dir = ROOT / "firmware"

    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onedir",
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
        str(ENTRY),
    ]

    if firmware_dir.exists():
        cmd[cmd.index(str(ENTRY)):cmd.index(str(ENTRY))] = [
            "--add-data",
            f"{firmware_dir}{data_sep}firmware",
        ]

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
    bundle_dir = dist_path / "OmniSuite"
    if not bundle_dir.exists():
        raise RuntimeError(f"Expected build output not found: {bundle_dir}")

    RELEASE_DIR.mkdir(parents=True, exist_ok=True)
    if suffix == "linux":
        artifact = RELEASE_DIR / f"OmniSuite-{version}-linux.tar.gz"
        tar_path(bundle_dir, artifact)
        return artifact

    if suffix in {"windows", "x86_64", "arm64"}:
        artifact = RELEASE_DIR / f"OmniSuite-{version}-{suffix}.zip"
        zip_path(bundle_dir, artifact)
        return artifact

    raise RuntimeError(f"Unsupported suffix: {suffix}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Build and package OmniSuite release artifacts.")
    parser.add_argument("--suffix", choices=["windows", "linux", "x86_64", "arm64"], default=None)
    parser.add_argument("--version", default=None)
    args = parser.parse_args()

    if not ENTRY.exists():
        raise RuntimeError(f"Entry script not found: {ENTRY}")

    suffix = resolve_suffix(args.suffix)
    version = read_version(args.version)

    dist_path = ROOT / "dist" / suffix
    work_path = ROOT / "build" / suffix

    clean_paths([dist_path, work_path, RELEASE_DIR])
    build_binary(dist_path=dist_path, work_path=work_path)
    artifact = package_release(dist_path=dist_path, suffix=suffix, version=version)

    print(f"[release] Created {artifact}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
