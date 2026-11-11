"""The local application port: automatic by default, fixed by choice.

OmniSuite's browser address is http://127.0.0.1:<port>/. By default the port is
chosen at startup (8080, or the next free one), so the address can differ from
one run to the next. An operator who has bookmarked it, or documented it, can
ask for a FIXED port instead. This module owns that choice and nothing else.

What it covers, and what it deliberately does not
=================================================

  * The LOCAL application listener only. Remote Access has its own listener,
    its own port, transport and certificate; nothing here reads or changes
    them. (Remote Access's default port still follows OMNI_PORT, which the
    launcher sets to whatever local port was actually bound -- that is
    existing behaviour and is unchanged.)
  * Automatic is the default and is untouched: with no fixed port configured the
    launcher's own free-port search runs exactly as it always did.

The rules this exists to hold
=============================

  * A fixed port is bound EXACTLY. A conflict is never answered by quietly
    picking another port: the launcher says so and the operator chooses.
  * A configuration is persisted only after the port it names has been bound
    (`persist_after_bind` refuses otherwise). A port that merely validated is
    advisory; the bind is authoritative.
  * The temporary alternate port a recovery lands on is NEVER persisted.
  * No state in the file can stop OmniSuite starting. A malformed file is
    quarantined and the application degrades to AUTOMATIC.
  * The file is machine-local. It is not part of a portable backup: a port is a
    fact about the computer it was chosen on (see omni_backup.SECTIONS).

Everything here is plain Python with real sockets, so it can be driven without a
window; the launcher supplies the window and the process restart.
"""
from __future__ import annotations

import errno
import json
import logging
import os
import socket
import sys
import threading
import time
from pathlib import Path

log = logging.getLogger("omni_local_port")

CONFIG_FILE = "local_port.json"

MODE_AUTOMATIC = "automatic"
MODE_FIXED = "fixed"

MIN_PORT = 1024
MAX_PORT = 65535

#: The automatic search starts here, exactly as it always has.
DEFAULT_START_PORT = 8080

#: How long a freshly restarted process keeps retrying its fixed port when it
#: finds it held. The old process releases its listener before the new one is
#: started, so this only covers the operating system's own latency; a port that
#: is genuinely taken is still reported, just a few seconds later.
RESTART_BIND_GRACE = 6.0

#: TCP ports OmniSuite itself reserves on this computer. Measured: the local
#: listener and the optional Remote Access listener are the only TCP listeners
#: the product opens, and Remote Access binds the adapter's own address (it may
#: share a port NUMBER with the loopback listener, as it does by default).
#: Nothing else is reserved, so nothing else is refused beyond the range.
RESERVED_PORTS = frozenset()

_file_lock = threading.RLock()


class PortError(ValueError):
    """A port the operator typed cannot be used. The message is for them."""


class PortUnavailable(OSError):
    """The exact port could not be bound."""

    def __init__(self, port, in_use, detail=""):
        self.port = int(port)
        self.in_use = bool(in_use)
        self.detail = detail
        super().__init__(f"TCP port {self.port} is "
                         + ("already in use" if in_use else "unavailable")
                         + (f" ({detail})" if detail and not in_use else ""))


# ---------------------------------------------------------------- validation

def parse_port(value) -> int:
    """The port a value names, or `PortError` saying why not.

    Strict on purpose: a permissive parser would turn "80 80", "8e3" or a
    float into a port. Digits only, 1024-65535, nothing reserved.
    """
    if isinstance(value, bool) or value is None:
        raise PortError("Enter a TCP port number.")
    if isinstance(value, int):
        text = str(value)
    elif isinstance(value, str):
        text = value.strip()
    else:
        raise PortError("The port must be a whole number.")
    if not text:
        raise PortError("Enter a TCP port number.")
    if not (text.isascii() and text.isdigit()):
        raise PortError("The port must be a whole number, digits only.")
    port = int(text)
    if port < MIN_PORT or port > MAX_PORT:
        raise PortError(f"The port must be between {MIN_PORT} and {MAX_PORT}.")
    if port in RESERVED_PORTS:
        raise PortError(f"TCP port {port} is used by OmniSuite itself.")
    return port


# ------------------------------------------------------------------- the file

def config_path(data_dir) -> Path:
    return Path(data_dir) / CONFIG_FILE


def _quarantine(path: Path, why) -> None:
    try:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        target = path.with_name(f"{path.stem}.corrupt-{stamp}{path.suffix}")
        os.replace(path, target)
        log.warning("[LOCAL-PORT] %s was unusable (%s); quarantined as %s",
                    path.name, why, target.name)
    except OSError as exc:
        log.warning("[LOCAL-PORT] could not quarantine %s: %s", path.name,
                    type(exc).__name__)


def read_config(data_dir) -> dict:
    """The stored choice. Never raises, and never returns anything unusable.

    Returns {"mode", "port", "problem"}. Anything that is not a clean fixed
    configuration is AUTOMATIC: an absent file, a malformed one, an unknown
    mode, a fixed mode whose port is not valid. The file cannot prevent a
    start, and the launcher is where the operator recovers from any of it.
    """
    result = {"mode": MODE_AUTOMATIC, "port": None, "problem": ""}
    path = config_path(data_dir)
    try:
        with _file_lock:
            if not path.exists():
                return result
            try:
                stored = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                _quarantine(path, type(exc).__name__)
                result["problem"] = "unreadable"
                return result
            if not isinstance(stored, dict):
                _quarantine(path, "not an object")
                result["problem"] = "unreadable"
                return result
    except Exception as exc:                     # nothing here may stop a start
        log.warning("[LOCAL-PORT] configuration unreadable: %s", type(exc).__name__)
        result["problem"] = "unreadable"
        return result
    mode = stored.get("local_port_mode")
    if mode == MODE_FIXED:
        try:
            result["port"] = parse_port(stored.get("configured_local_fixed_port"))
            result["mode"] = MODE_FIXED
        except PortError:
            result["problem"] = "invalid_port"
    elif mode not in (None, MODE_AUTOMATIC):
        result["problem"] = "unknown_mode"
    return result


def write_config(data_dir, mode, port=None) -> bool:
    """Atomically replace the file: unique temp, fsync, os.replace, one lock."""
    path = config_path(data_dir)
    payload = {"local_port_mode": mode,
               "configured_local_fixed_port": int(port) if port else None}
    tmp = path.with_name(f"{path.name}.{os.getpid()}-{threading.get_ident()}.tmp")
    try:
        with _file_lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(tmp, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, path)
        return True
    except Exception as exc:
        log.error("[LOCAL-PORT] could not write %s: %s", path.name,
                  type(exc).__name__)
        try:
            tmp.unlink()
        except OSError:
            pass
        return False


# ------------------------------------------------------------------ binding

_IN_USE = {errno.EADDRINUSE, getattr(errno, "WSAEADDRINUSE", errno.EADDRINUSE)}
if sys.platform == "win32":
    # Windows reports a port held by another listener as WSAEACCES when the
    # socket asks for exclusive use, which is what Werkzeug's server does.
    _IN_USE.add(10013)


def _accepting(host, port) -> bool:
    """Whether something is already listening on host:port.

    A plain bind cannot see every holder: a server that asked to share its port
    (Werkzeug's does, and so does another OmniSuite) lets a second bind succeed
    on Windows, and two listeners on one port is exactly the silent conflict a
    fixed port exists to prevent. Connecting asks the question directly.
    """
    try:
        with socket.create_connection((host, int(port)), timeout=0.5):
            return True
    except OSError:
        return False


def probe(host, port):
    """None when the port can be bound right now, else the OSError.

    ADVISORY. It is a separate socket from the one that will serve, so it can be
    wrong in both directions (something takes the port a moment later; a port in
    transition refuses a probe). The real bind decides.
    """
    if _accepting(host, port):
        return OSError(errno.EADDRINUSE, "a listener already holds this port")
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind((host, int(port)))
        return None
    except OSError as exc:
        return exc
    finally:
        try:
            sock.close()
        except OSError:
            pass


def bind_listener(app, host, port, **kwargs):
    """A listening WSGI server on EXACTLY `port`, or `PortUnavailable`.

    Werkzeug turns a failed bind into SystemExit rather than an exception a
    caller can handle, which would end the launcher's server thread silently.
    It is caught here and reported as what it is. No other port is tried.
    """
    import omni_remote
    if _accepting(host, port):
        raise PortUnavailable(port, True)
    try:
        return omni_remote.serve_tagged(host, int(port), app, threaded=True,
                                        **kwargs)
    except (OSError, SystemExit) as exc:
        failure = exc if isinstance(exc, OSError) else probe(host, port)
        number = getattr(failure, "errno", None)
        in_use = (number in _IN_USE) or failure is None or isinstance(exc, SystemExit)
        raise PortUnavailable(port, in_use,
                              "" if in_use else str(failure)) from None


# --------------------------------------------------------------- supervision

class PortSupervisor:
    """What the application knows about its local port, and what may change it.

    One instance per run, created by the launcher and registered here so the
    server's Settings endpoints can reach it. `restart` is supplied by the
    launcher: it must release the listener and relaunch the application for
    real, and it is the only thing in this class that is not plain Python.
    """

    def __init__(self, data_dir, host="127.0.0.1", restart=None,
                 persist=write_config):
        self.data_dir = Path(data_dir)
        self.host = host
        self._restart = restart
        self._persist = persist
        self._lock = threading.RLock()
        self.mode = MODE_AUTOMATIC
        self.configured_port = None
        self.actual_port = None
        self.temporary = False        # running on an alternate for this session
        self.conflict_port = None     # set while the launcher is in recovery
        self.problem = ""
        self.restarting = False

    # ---- reading the choice
    def load(self) -> dict:
        stored = read_config(self.data_dir)
        with self._lock:
            self.mode = stored["mode"]
            self.configured_port = stored["port"]
            self.problem = stored["problem"]
        return stored

    # ---- what the launcher reports as it starts
    def bound(self, port, temporary=False):
        """The port that is actually serving. Never persists anything."""
        with self._lock:
            self.actual_port = int(port)
            self.temporary = bool(temporary)
            self.conflict_port = None

    def conflicted(self, port):
        with self._lock:
            self.conflict_port = int(port)
            self.actual_port = None

    def persist_after_bind(self, port) -> bool:
        """Make `port` the fixed port, provided it is the one now serving.

        The guard is the point: a caller that tries to save a port before it
        has bound it is refused here rather than trusted to have ordered its
        steps correctly.
        """
        with self._lock:
            if self.actual_port is None or int(port) != self.actual_port:
                raise RuntimeError("a port is saved only after it has been bound")
            if not self._persist(self.data_dir, MODE_FIXED, int(port)):
                return False
            self.mode = MODE_FIXED
            self.configured_port = int(port)
            self.temporary = False
            self.problem = ""
            return True

    # ---- what Settings is told
    def status(self) -> dict:
        with self._lock:
            actual = self.actual_port
            return {
                "ok": True, "available": True,
                "mode": self.mode,
                "fixed": self.mode == MODE_FIXED,
                "configured_port": self.configured_port,
                "actual_port": actual,
                "temporary": bool(self.temporary),
                "conflict": self.conflict_port is not None,
                "host": self.host,
                "url": (f"http://{self.host}:{actual}/" if actual else ""),
                "restarting": self.restarting,
                "problem": self.problem,
                "min_port": MIN_PORT, "max_port": MAX_PORT,
            }

    # ---- what Settings may ask for
    def _refuse(self, reason, message, status=409):
        return {"ok": False, "reason": reason, "error": message}, status

    def _begin_restart(self):
        if self._restart is None:
            return False
        self.restarting = True
        self._restart()
        return True

    def set_fixed(self, value):
        """Save a fixed port and restart. Validation first, then the advisory
        probe (skipped for the port already serving: the old listener holds it
        and the restart releases it before the new process binds)."""
        try:
            port = parse_port(value)
        except PortError as exc:
            return self._refuse("invalid_port", str(exc), 400)
        with self._lock:
            if self.restarting:
                return self._refuse("restarting", "OmniSuite is already restarting.")
            if self._restart is None:
                return self._refuse("unavailable", NOT_AVAILABLE)
            holder = self.actual_port
            if port != holder:
                failure = probe(self.host, port)
                if failure is not None:
                    return self._refuse(
                        "port_in_use",
                        f"TCP port {port} is not available right now. "
                        "Enter another port.")
            if not self._persist(self.data_dir, MODE_FIXED, port):
                return self._refuse("write_failed",
                                    "The setting could not be saved.", 500)
            self.mode, self.configured_port = MODE_FIXED, port
            self._begin_restart()
        return {"ok": True, "restarting": True, "port": port}, 200

    def disable_fixed(self):
        with self._lock:
            if self.restarting:
                return self._refuse("restarting", "OmniSuite is already restarting.")
            if self._restart is None:
                return self._refuse("unavailable", NOT_AVAILABLE)
            if not self._persist(self.data_dir, MODE_AUTOMATIC, None):
                return self._refuse("write_failed",
                                    "The setting could not be saved.", 500)
            self.mode, self.configured_port = MODE_AUTOMATIC, None
            self._begin_restart()
        return {"ok": True, "restarting": True}, 200

    def retry_fixed(self):
        """Leave the temporary alternate and try the configured port again."""
        with self._lock:
            if self.restarting:
                return self._refuse("restarting", "OmniSuite is already restarting.")
            if self._restart is None:
                return self._refuse("unavailable", NOT_AVAILABLE)
            if not (self.temporary and self.configured_port):
                return self._refuse("not_temporary",
                                    "OmniSuite is not running on a temporary port.")
            if probe(self.host, self.configured_port) is not None:
                return self._refuse(
                    "port_in_use",
                    f"TCP port {self.configured_port} is still in use.")
            self._begin_restart()
        return {"ok": True, "restarting": True,
                "port": self.configured_port}, 200

    def make_fixed(self):
        """Keep the port OmniSuite is already serving on. No restart: it is bound."""
        with self._lock:
            if not self.temporary:
                return self._refuse("not_temporary",
                                    "OmniSuite is not running on a temporary port.")
            port = self.actual_port
            try:
                saved = self.persist_after_bind(port)
            except RuntimeError:
                return self._refuse("unavailable", NOT_AVAILABLE)
            if not saved:
                return self._refuse("write_failed",
                                    "The setting could not be saved.", 500)
        return {"ok": True, "restarting": False, "port": port}, 200


NOT_AVAILABLE = ("The local port can only be changed when OmniSuite was "
                 "started from its launcher. It is not available in this run.")

# One controller per process: the launcher registers it, the server reads it.
_controller = None


def register_controller(controller) -> None:
    global _controller
    _controller = controller


def controller():
    return _controller


def unavailable_status() -> dict:
    return {"ok": True, "available": False, "message": NOT_AVAILABLE}
