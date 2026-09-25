"""Drive Remote Access on the PACKAGED artifact, end to end.

`smoke_release.py` proves the binary serves its pages. This proves the part an
operator sets up afterwards actually works when it is packaged: a clean
installation has no account and no certificate, HTTPS refuses to start without
one, generating one works from inside the executable, the listener really
speaks TLS, the setting survives a restart, and a damaged certificate refuses
rather than quietly falling back to plaintext.

It needs the binary, because every one of those can pass from a source
checkout and fail in a package -- `cryptography`'s Rust extension not being
collected is the obvious one, and it builds cleanly.

    python tools/smoke_remote.py --binary dist/windows/OmniSuite.exe
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# One implementation of "which port did the launcher bind", shared with the
# release smoke test rather than written a second time here.
from smoke_release import launcher_log_path, wait_for_port   # noqa: E402

FAILURES: list[str] = []


def note(label, value, good=True):
    if not good:
        FAILURES.append(label)
    print("  %-52s %-4s %s" % (label, "OK" if good else "FAIL", value), flush=True)


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def local_address():
    """A routable address on this machine, for the certificate and the bind."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.connect(("198.18.0.1", 9))     # RFC 2544, never reached
        return sock.getsockname()[0]


class Packaged:
    """The executable, started against a data directory of our choosing."""

    def __init__(self, binary: Path, data_dir: Path):
        self.binary, self.data_dir = binary, data_dir
        self.port = None
        self.process = None

    def start(self, timeout=180):
        env = dict(os.environ, OMNI_DATA_DIR=str(self.data_dir),
                   OMNI_SMOKE="1")
        env.pop("OMNI_PORT", None)
        log_file = launcher_log_path()
        launched = time.time()
        self.process = subprocess.Popen(
            [str(self.binary)], env=env, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, cwd=str(self.binary.parent))
        self.port = wait_for_port(log_file, launched, timeout=timeout)
        if self.port is None:
            raise RuntimeError("the launcher never recorded a port")
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError("the executable exited during startup")
            try:
                urllib.request.urlopen(self.url("/__health"), timeout=2).read()
                return time.time()
            except Exception:
                time.sleep(0.5)
        raise RuntimeError("the packaged server never answered")

    def stop(self):
        if self.process is None or self.process.poll() is not None:
            return
        if sys.platform == "win32":
            # A onefile build runs a child; killing the parent alone orphans it.
            subprocess.run(["taskkill", "/PID", str(self.process.pid), "/T", "/F"],
                           capture_output=True)
        else:
            self.process.terminate()
        try:
            self.process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            self.process.kill()

    def url(self, path):
        return "http://127.0.0.1:%d%s" % (self.port, path)


INSECURE = ssl.create_default_context()
INSECURE.check_hostname = False
INSECURE.verify_mode = ssl.CERT_NONE


def call(url, method="GET", body=None, cookie=None, context=None, timeout=60):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, method=method)
    if data:
        request.add_header("Content-Type", "application/json")
    if cookie:
        request.add_header("Cookie", "omnisuite_session=" + cookie)
    try:
        with urllib.request.urlopen(request, timeout=timeout,
                                    context=context) as response:
            raw = response.read().decode("utf-8", "replace")
            headers = response.headers.get("Set-Cookie") or ""
            try:
                return response.status, json.loads(raw), headers
            except ValueError:
                return response.status, raw, headers
    except urllib.error.HTTPError as error:
        raw = error.read().decode("utf-8", "replace")
        try:
            return error.code, json.loads(raw), ""
        except ValueError:
            return error.code, raw, ""


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", required=True, type=Path)
    args = parser.parse_args(argv)
    binary = args.binary.resolve()
    if not binary.exists():
        print("No such binary: %s" % binary)
        return 2

    data_dir = Path(tempfile.mkdtemp(prefix="omni-smoke-remote-"))
    remote_port = free_port()
    address = local_address()
    app = Packaged(binary, data_dir)

    try:
        print("=== a clean installation ===", flush=True)
        started = time.time()
        app.start()
        note("the packaged server starts", "%.1f s" % (time.time() - started))
        note("no users.json", not (data_dir / "users.json").exists(),
             not (data_dir / "users.json").exists())
        note("no certificate", not (data_dir / "remote_cert.pem").exists(),
             not (data_dir / "remote_cert.pem").exists())
        note("no private key", not (data_dir / "remote_key.pem").exists(),
             not (data_dir / "remote_key.pem").exists())
        status, state, _ = call(app.url("/api/remote_access"))
        note("remote access is off", not (state or {}).get("enabled"),
             isinstance(state, dict) and not state.get("enabled"))

        print()
        print("=== setting it up ===", flush=True)
        status, _b, _c = call(app.url("/api/bootstrap/admin"), "POST",
                              {"username": "smoke-admin",
                               "password": "smoke-admin-pass-1",
                               "confirm": "smoke-admin-pass-1"})
        note("the first administrator is created locally", status, status == 200)

        status, body, _ = call(app.url("/api/remote_access"), "POST",
                               {"enabled": True, "address": address,
                                "port": remote_port})
        note("HTTPS refuses without a certificate", status, status == 400)

        status, body, _ = call(app.url("/api/admin/certificate"), "POST",
                               {"action": "generate", "address": address})
        # This is the one that a package can fail on its own: generation needs
        # cryptography, whose Rust extension has to have been collected.
        note("a certificate can be generated from the package",
             (body or {}).get("error") or "created", status == 200)
        note("no key material in the response",
             "PRIVATE KEY" not in json.dumps(body),
             "PRIVATE KEY" not in json.dumps(body))

        status, body, _ = call(app.url("/api/remote_access"), "POST",
                               {"enabled": True, "address": address,
                                "port": remote_port})
        note("remote access starts", (body or {}).get("url"), status == 200)
        remote = "https://%s:%d" % (address, remote_port)
        time.sleep(1.5)

        print()
        print("=== it really speaks TLS ===", flush=True)
        status, _b, _c = call(remote + "/__health", context=INSECURE)
        note("an HTTPS request is answered", status, status == 200)
        try:
            call("http://%s:%d/__health" % (address, remote_port), timeout=10)
            note("plaintext on the same port", "ANSWERED", False)
        except Exception as error:
            note("plaintext on the same port is refused", type(error).__name__)

        status, _b, cookie_header = call(
            remote + "/api/auth/login", "POST",
            {"username": "smoke-admin", "password": "smoke-admin-pass-1"},
            context=INSECURE)
        note("sign-in over TLS", status, status == 200)
        for flag in ("HttpOnly", "Secure", "SameSite=Lax"):
            note("the cookie is %s" % flag, flag in cookie_header,
                 flag in cookie_header)

        print()
        print("=== after a restart ===", flush=True)
        app.stop()
        app = Packaged(binary, data_dir)
        app.start()
        time.sleep(1.5)
        status, _b, _c = call(remote + "/__health", context=INSECURE)
        note("remote access came back on its own", status, status == 200)
        status, _b, _c = call(remote + "/api/admin/users", context=INSECURE)
        note("everyone has to sign in again", status, status in (401, 403))

        print()
        print("=== a damaged certificate ===", flush=True)
        app.stop()
        (data_dir / "remote_cert.pem").write_text("not a certificate",
                                                  encoding="utf-8")
        app = Packaged(binary, data_dir)
        app.start()
        time.sleep(1.5)
        try:
            call(remote + "/__health", context=INSECURE, timeout=8)
            note("nothing listens on the remote port", "SOMETHING ANSWERED", False)
        except Exception as error:
            note("nothing listens on the remote port", type(error).__name__)
        try:
            call("http://%s:%d/__health" % (address, remote_port), timeout=8)
            note("and it did not fall back to plaintext", "PLAINTEXT ANSWERED",
                 False)
        except Exception as error:
            note("and it did not fall back to plaintext", type(error).__name__)
        status, state, _ = call(app.url("/api/remote_access"))
        reason = (state or {}).get("restore_error") or ""
        note("Settings says why", reason[:46] or "(nothing)", bool(reason))
        status, _b, _c = call(app.url("/"))
        note("OmniSuite still works on its own computer", status, status == 200)
    finally:
        app.stop()
        shutil.rmtree(data_dir, ignore_errors=True)

    print()
    if FAILURES:
        print("PACKAGED REMOTE ACCESS: FAIL")
        for item in FAILURES:
            print("  - %s" % item)
        return 1
    print("PACKAGED REMOTE ACCESS: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
