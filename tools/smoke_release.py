"""Start the PACKAGED artifact and prove it actually works.

"PyInstaller completed successfully" is not evidence that anything runs. A
missing data file, a hidden import that was not collected, or a path that only
resolves from a source checkout all produce a clean build and a broken binary.
This starts the real executable, waits for its server, and asks it the
questions an operator's first minute would.

The launcher writes the address it bound to into launcher.log, so the port is
read rather than guessed -- it is chosen at runtime and will not be 8080 on a
machine where something else already holds that port.

Usage:
    python tools/smoke_release.py --artifact release/OmniSuite-V1.0.7-....zip
    python tools/smoke_release.py --binary dist/windows/OmniSuite.exe
"""
from __future__ import annotations

import argparse
import os
import platform
import re
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION_FILE = ROOT / "VERSION"

# Everything an operator touches in the first minute, and nothing that needs a
# device: a packaged binary can only fail these by being packaged wrong.
REQUIRED_PATHS = [
    ("/", "text/html"),
    ("/ui/user-guide.html", "text/html"),
    ("/ui/templates.css", None),
    ("/ui/toast.js", None),
    ("/ui/device-log.js", None),
    ("/ui/settings.js", None),
    ("/ui/appearance.js", None),
    ("/ui/companylogo.png", None),
    ("/ui/companylogo-light.png", None),
    ("/matrix/configure", "text/html"),
    ("/matrix", "text/html"),
    ("/matrix/usb", "text/html"),
]


def log(message: str) -> None:
    print(f"[smoke] {message}", flush=True)


def launcher_log_path() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "OmniSuite" / "logs"
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Logs" / "OmniSuite"
    else:
        base = Path.home() / ".omnisuite" / "logs"
    return base / "launcher.log"


def extract(artifact: Path, into: Path) -> Path:
    """Unpack a release archive and return the executable inside it."""
    into.mkdir(parents=True, exist_ok=True)
    if artifact.suffix == ".zip":
        with zipfile.ZipFile(artifact) as zf:
            zf.extractall(into)
    elif artifact.name.endswith((".tar.gz", ".tgz")):
        with tarfile.open(artifact, "r:gz") as tf:
            tf.extractall(into)
    else:
        raise RuntimeError(f"Unsupported archive: {artifact.name}")

    exe = into / "OmniSuite.exe"
    if exe.exists():
        return exe
    app = next(iter(into.glob("*.app")), None)
    if app is not None:
        inner = app / "Contents" / "MacOS" / "OmniSuite"
        if not inner.exists():
            raise RuntimeError(f"No executable inside {app.name}")
        return ensure_executable(inner)
    unix = into / "OmniSuite" / "OmniSuite"
    if unix.exists():
        return ensure_executable(unix)
    raise RuntimeError(f"No OmniSuite executable found under {into}")


def ensure_executable(binary: Path) -> Path:
    """Restore the executable bit if the extraction dropped it.

    zipfile.extractall() does not carry Unix permissions, so a .app unpacked
    with it has a non-executable binary inside and launching it fails with
    EACCES -- which is how both macOS smoke jobs failed. The published archive
    is made with ditto, which does preserve the mode; this is about how the
    smoke test unpacks it, so it applies to every archive type rather than
    only the one where it was first noticed.
    """
    if os.name == "nt" or os.access(binary, os.X_OK):
        return binary
    log(f"executable bit missing after extraction; restoring it on {binary.name}")
    binary.chmod(binary.stat().st_mode | 0o111)
    return binary


# The launcher log is append-only across runs, so "the last port in the file" is
# the previous run's until this one has written its own line. Reading it that way
# probed a port nothing was serving and reported a working binary as broken. Each
# line carries its own timestamp, so only lines from this launch are considered.
LOG_LINE = re.compile(r"^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]\s+(.*)$")
READY = re.compile(r"Server ready at https?://[\d.]+:(\d+)")
STARTING = re.compile(r"Starting server on [\d.]+:(\d+)")


def wait_for_port(log_file: Path, since: float, timeout: float) -> int | None:
    """The port the launcher actually bound, from lines written by THIS launch.

    Prefers "Server ready at", which is only written once a bind succeeded;
    "Starting server on" is an attempt and is logged again for each port the
    launcher steps over when one is in use.
    """
    deadline = time.time() + timeout
    started_at = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(since - 2))
    while time.time() < deadline:
        if log_file.exists():
            try:
                text = log_file.read_text(encoding="utf-8", errors="replace")
            except OSError:
                text = ""
            ready, starting = None, None
            for line in text.splitlines():
                match = LOG_LINE.match(line)
                if not match or match.group(1) < started_at:
                    continue
                body = match.group(2)
                found = READY.search(body)
                if found:
                    ready = int(found.group(1))
                    continue
                found = STARTING.search(body)
                if found:
                    starting = int(found.group(1))
            if ready is not None:
                return ready
            if starting is not None:
                # Bound or not yet, this is the current attempt; keep watching
                # for the ready line rather than committing to it.
                pass
        time.sleep(0.5)
    return None


def get(url: str, timeout: float = 10.0):
    request = urllib.request.Request(url, headers={"User-Agent": "OmniSuite-smoke"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.status, response.headers.get("Content-Type", ""), response.read()


def wait_for_server(port: int, timeout: float) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            status, _, _ = get(f"http://127.0.0.1:{port}/api/config", timeout=3.0)
            if status == 200:
                return True
        except Exception:
            time.sleep(1.0)
    return False


def stop_process_tree(pid: int) -> None:
    """Kill a process and everything it started."""
    try:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)],
                           capture_output=True, check=False)
        else:
            # Safe only because the launcher was started in its own session.
            os.killpg(os.getpgid(pid), signal.SIGTERM)
    except Exception as exc:
        log(f"could not stop the process tree: {type(exc).__name__}")


def surviving_processes(scope: Path) -> list:
    """Processes still running from the copy this run unpacked.

    Scoped to that directory deliberately. Matching on the image name would
    catch an OmniSuite the operator started themselves, and matching the
    command line would catch this script, whose own arguments contain the word
    -- which reported a leak on every Linux and macOS run.
    """
    time.sleep(2.0)
    target = str(Path(scope).resolve()).lower()
    try:
        import psutil
    except ImportError:
        psutil = None

    if psutil is not None:
        try:
            found = []
            # ad_value is required, not cosmetic: without it process_iter raises
            # AccessDenied out of the iterator the moment it meets a process
            # this user cannot read the path of, which is every system process
            # on macOS. The per-process guard below never sees that -- it is
            # raised by the loop itself -- so the smoke test died in its own
            # cleanup on both macOS runners while Linux and Windows passed.
            for proc in psutil.process_iter(["pid", "exe"], ad_value=None):
                executable = (proc.info.get("exe") or "").lower()
                if executable.startswith(target):
                    found.append(str(proc.info["pid"]))
            return found
        except Exception as exc:
            log(f"psutil could not enumerate processes ({type(exc).__name__}); "
                f"falling back to a command-line match")

    # Without psutil, match the extraction path in the command line. Still
    # scoped: this script's arguments name the archive, never the temp copy.
    try:
        if sys.platform == "win32":
            out = subprocess.run(
                ["wmic", "process", "get", "ProcessId,ExecutablePath", "/format:csv"],
                capture_output=True, text=True, check=False).stdout
            return [line.rsplit(",", 1)[-1].strip() for line in out.splitlines()
                    if target in line.lower()]
        out = subprocess.run(["pgrep", "-f", str(Path(scope).resolve())],
                             capture_output=True, text=True, check=False).stdout
        return out.split()
    except Exception:
        return []


def run(binary: Path, expected_version: str, scope: Path | None = None) -> int:
    failures: list[str] = []
    started = time.time()
    log_file = launcher_log_path()
    # Everything this run unpacked lives under here; a process still running
    # from it is this run's leak and nobody else's.
    scope = Path(scope) if scope is not None else binary.parent

    env = dict(os.environ)
    # A smoke test must not open a browser window on a build runner, and must
    # not inherit a port from whatever else the machine is doing.
    env["OMNI_SMOKE"] = "1"
    env.pop("OMNI_PORT", None)

    log(f"launching {binary}")
    # start_new_session puts the launcher in its own process group so the whole
    # tree can be killed later without signalling the runner that started it.
    process = subprocess.Popen([str(binary)], env=env,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               start_new_session=(sys.platform != "win32"))
    port = None
    try:
        port = wait_for_port(log_file, started, timeout=120)
        if port is None:
            failures.append("the launcher never reported a bound port")
            # Read what the child said. It is only safe to read the pipe once
            # the process is gone, and a launcher that never bound is usually
            # still alive -- which is exactly the case the old code skipped,
            # so the one failure that most needed explaining explained nothing.
            still_running = process.poll() is None
            log(f"launcher still running when it should have bound: {still_running}")
            stop_process_tree(process.pid)
            try:
                process.wait(timeout=15)
            except Exception:
                process.kill()
            out = b""
            if process.stdout is not None:
                try:
                    out = process.stdout.read()[:2000]
                except Exception as exc:
                    log(f"could not read the launcher's output: {type(exc).__name__}")
            log(f"launcher exit code: {process.returncode}")
            log(f"launcher output: {out.decode('utf-8', 'replace').strip() or '(nothing)'}")
            if log_file.exists():
                lines = log_file.read_text(encoding="utf-8", errors="replace").splitlines()
                log(f"launcher log {log_file} has {len(lines)} line(s); last 15:")
                for line in lines[-15:]:
                    log(f"  | {line}")
            else:
                log(f"launcher log {log_file} was never created")
            raise SystemExit(report(failures))

        log(f"launcher bound port {port}")
        if not wait_for_server(port, timeout=120):
            failures.append(f"the server never answered on port {port}")
            raise SystemExit(report(failures))
        log("server is answering")

        status, _, body = get(f"http://127.0.0.1:{port}/api/config")
        if status != 200:
            failures.append(f"/api/config returned HTTP {status}")
        else:
            import json
            payload = json.loads(body) or {}
            # The field is `app_version`. Reading `version` produced None and a
            # failure that looked like a packaging fault rather than a typo in
            # this script.
            reported = payload.get("app_version")
            if reported != expected_version:
                failures.append(
                    f"/api/config app_version is {reported!r}, expected {expected_version!r}")
            else:
                log(f"app_version {reported}")

        for path, expect_type in REQUIRED_PATHS:
            try:
                status, content_type, body = get(f"http://127.0.0.1:{port}{path}")
            except Exception as exc:
                failures.append(f"{path} raised {type(exc).__name__}")
                continue
            if status != 200:
                failures.append(f"{path} returned HTTP {status}")
            elif not body:
                failures.append(f"{path} returned an empty body")
            elif expect_type and expect_type not in content_type:
                failures.append(f"{path} content-type {content_type!r}")
            else:
                log(f"  {path} OK ({len(body)} bytes)")
    finally:
        log("stopping")
        # --onefile runs a bootloader that spawns the application as a CHILD, so
        # stopping the handle alone leaves the real process running. The tree has
        # to come down while the bootloader is still alive: once it exits, the
        # child is re-parented and no longer reachable from its pid.
        stop_process_tree(process.pid)
        try:
            process.wait(timeout=20)
            log(f"exited with {process.returncode}")
        except Exception:
            process.kill()
            failures.append("the process did not stop cleanly and had to be killed")
        # A launcher left running from here carries OMNI_SMOKE for its whole
        # life, so every browser click in it is suppressed -- and it looks
        # exactly like a normal install. Make certain nothing survives.
        for _ in range(10):
            if process.poll() is not None:
                break
            time.sleep(0.5)
        survivors = surviving_processes(scope)
        if survivors:
            failures.append(f"smoke-launched process(es) still running: {survivors}")

    return report(failures)


def report(failures: list[str]) -> int:
    if failures:
        log(f"FAIL -- {len(failures)} problem(s):")
        for item in failures:
            log(f"   - {item}")
        return 1
    log("PASS")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact", type=Path, help="release archive to unpack and test")
    parser.add_argument("--binary", type=Path, help="already-built executable to test")
    parser.add_argument("--version", default=None)
    args = parser.parse_args()

    expected = (args.version or VERSION_FILE.read_text(encoding="utf-8").strip())

    if args.binary:
        return run(args.binary.resolve(), expected)
    if not args.artifact:
        parser.error("one of --artifact or --binary is required")

    # Windows keeps a handle on a just-terminated executable for a moment, so
    # removing the directory can fail after a run that was otherwise fine. The
    # smoke result must not depend on how fast the OS releases a file -- but a
    # directory left behind holds a runnable OmniSuite.exe that someone can
    # double-click, so cleanup is retried before giving up on it.
    with tempfile.TemporaryDirectory(prefix="omnisuite-smoke-",
                                     ignore_cleanup_errors=True) as tmp:
        binary = extract(args.artifact.resolve(), Path(tmp))
        log(f"extracted {binary}")
        if platform.system() != "Windows" and shutil.which("file"):
            arch = subprocess.run(["file", str(binary)], capture_output=True, text=True)
            log(f"file: {arch.stdout.strip()}")
        try:
            return run(binary, expected, scope=Path(tmp))
        finally:
            for attempt in range(6):
                try:
                    shutil.rmtree(tmp, ignore_errors=False)
                    break
                except OSError:
                    time.sleep(1.0)
            if Path(tmp).exists():
                log(f"NOTE: could not remove {tmp}; delete it manually")


if __name__ == "__main__":
    raise SystemExit(main())
