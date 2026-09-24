"""Optional remote access: a second listener, and the trust signal it carries.

OmniSuite has always served loopback only, and every one of its routes is
anonymous because of it. Remote access cannot simply rebind that socket: the
moment the application answers on a routable address, the one control it had is
gone. So this module does not move the listener. It adds a second one.

    listener LOCAL   127.0.0.1:<port>   always running
    listener REMOTE  <adapter>:<port>   only while remote access is enabled

Which listener accepted a connection is decided by the kernel and recorded by
our own WSGI wrapper before Flask sees the request. That is the whole trust
signal, and it is why it cannot be forged: a client can set any header it likes,
but CGI/WSGI maps header names to `HTTP_UPPER_UNDERSCORE`, so a key containing a
dot -- `omni.listener` -- is not reachable from the wire at all. Comparing
`request.remote_addr` against a loopback set would have been the obvious
alternative and is weaker: it is an inference about an address rather than a
fact about a socket, and it quietly becomes wrong the day somebody puts a proxy
in front.

Nothing here authenticates anybody. It establishes *where a request arrived*;
`omni_access` says who may call what, and the gate joins the two.
"""
from __future__ import annotations

import ipaddress
import logging
import socket
import threading

log = logging.getLogger(__name__)

#: WSGI environ key naming the listener a request arrived on. The dot is
#: load-bearing: HTTP headers become HTTP_X_Y, so nothing a client sends can
#: ever produce this key.
LISTENER_KEY = "omni.listener"

LOCAL = "local"
REMOTE = "remote"

#: Loopback is the only address family that means "this machine".
_LOOPBACK = ipaddress.ip_network("127.0.0.0/8")


class _ListenerTag:
    """WSGI middleware stamping which listener served a request.

    One instance wraps the application per listener, so the value is fixed when
    the server is created rather than computed per request. There is no code
    path that derives it from request content.
    """

    def __init__(self, app, listener: str):
        self._app = app
        self._listener = listener

    def __call__(self, environ, start_response):
        # Overwrite unconditionally. If a key of this name could somehow be
        # present it did not come from us, and ours is the only correct answer.
        environ[LISTENER_KEY] = self._listener
        return self._app(environ, start_response)


def listener_of(environ) -> str:
    """Which listener served this request.

    An unstamped request is treated as REMOTE. That is the safe direction: an
    unwrapped server -- a test client, an embedding nobody anticipated -- gets
    the restricted answer rather than silently being granted local trust.
    """
    value = environ.get(LISTENER_KEY)
    return LOCAL if value == LOCAL else REMOTE


def is_local_request(environ) -> bool:
    """True only for a request the loopback listener accepted.

    Deliberately not `remote_addr in 127.0.0.0/8`: that is a statement about an
    address the request claims to come from, and this is a statement about the
    socket the operating system accepted it on.
    """
    return listener_of(environ) is LOCAL or listener_of(environ) == LOCAL


def is_loopback_address(address: str) -> bool:
    """Whether a literal address is loopback. Used for validating a bind choice.

    This is about configuration, never about trusting a request.
    """
    try:
        return ipaddress.ip_address(str(address).strip()) in _LOOPBACK
    except ValueError:
        return False


class RemoteListener:
    """The optional second listener, and its lifecycle.

    Kept separate from the loopback server so that enabling and disabling remote
    access is starting and stopping *this* object, and never touching the one
    that serves the local operator. Disabling can therefore never break local
    use, which is the property section 4 of the brief asks for.
    """

    def __init__(self, app):
        self._app = app
        self._server = None
        self._thread = None
        self._lock = threading.RLock()
        self._bound = None                      # (host, port) while running

    # -- state ---------------------------------------------------------
    @property
    def running(self) -> bool:
        with self._lock:
            return self._server is not None

    @property
    def bound(self):
        with self._lock:
            return self._bound

    def describe(self) -> dict:
        """What Settings shows: enabled, where, and the URL that reaches it."""
        with self._lock:
            if not self._server:
                return {"enabled": False, "host": None, "port": None, "url": None}
            host, port = self._bound
            return {"enabled": True, "host": host, "port": port,
                    "url": f"http://{host}:{port}/"}

    # -- lifecycle -----------------------------------------------------
    def start(self, host: str, port: int) -> dict:
        """Begin answering on `host:port`.

        Returns a result dict rather than raising for an ordinary operator
        mistake -- an address that is not on this machine, or a port already
        taken -- because those are answers the Settings page has to display, not
        faults.
        """
        host = str(host or "").strip()
        if not host:
            return {"ok": False, "error": "no address selected"}
        if is_loopback_address(host):
            # Loopback is already served, and binding it here would mean remote
            # requests could arrive tagged as local.
            return {"ok": False,
                    "error": "loopback is always served locally and cannot be "
                             "chosen as the remote address"}
        try:
            port = int(port)
        except (TypeError, ValueError):
            return {"ok": False, "error": "invalid port"}

        with self._lock:
            if self._server is not None:
                self.stop()
            try:
                from werkzeug.serving import make_server
                # The remote listener serves the SAME application object, with
                # its own tag. One application, one set of state, two doors.
                server = make_server(host, port, _ListenerTag(self._app, REMOTE),
                                     threaded=True)
            except OSError as exc:
                log.warning("[REMOTE] cannot bind %s:%s: %s", host, port,
                            type(exc).__name__)
                return {"ok": False,
                        "error": f"cannot bind {host}:{port} ({exc.strerror or exc})"}
            except Exception as exc:
                log.warning("[REMOTE] listener failed to start: %s", type(exc).__name__)
                return {"ok": False, "error": f"listener failed: {type(exc).__name__}"}

            thread = threading.Thread(
                target=server.serve_forever,
                name="omni-remote-listener", daemon=True)
            thread.start()
            self._server, self._thread, self._bound = server, thread, (host, port)
            log.info("[REMOTE] listening on %s:%s", host, port)
            return {"ok": True, **self.describe()}

    def stop(self) -> dict:
        """Stop answering remotely. The local listener is untouched."""
        with self._lock:
            server, thread = self._server, self._thread
            self._server = self._thread = self._bound = None
        if server is None:
            return {"ok": True, "enabled": False}
        try:
            server.shutdown()
            server.server_close()
        except Exception as exc:
            log.info("[REMOTE] shutdown raised %s", type(exc).__name__)
        if thread is not None:
            # A listener that outlives the call that stopped it is a socket
            # still accepting connections nobody believes are possible.
            thread.join(timeout=5.0)
            if thread.is_alive():
                log.warning("[REMOTE] listener thread did not stop within 5s")
                return {"ok": False, "enabled": False,
                        "error": "listener did not stop cleanly"}
        log.info("[REMOTE] stopped")
        return {"ok": True, "enabled": False}


def local_app(app):
    """Wrap the application for the loopback listener."""
    return _ListenerTag(app, LOCAL)


def usable_bind_addresses(adapters) -> list:
    """Filter enumerated adapters down to ones that can actually be bound.

    Takes whatever /api/adapters already produces, so there is one adapter
    enumeration in the product rather than a second one written for this
    feature. Loopback is excluded: it is already served.
    """
    usable = []
    for adapter in adapters or []:
        address = (adapter.get("address") or adapter.get("ip") or "").strip()
        if not address or is_loopback_address(address):
            continue
        try:
            ipaddress.ip_address(address)
        except ValueError:
            continue
        usable.append({
            "address": address,
            "name": adapter.get("name") or adapter.get("adapter") or address,
            "cidr": adapter.get("cidr") or "",
        })
    return usable


def can_bind(host: str, port: int) -> bool:
    """Whether this machine can bind that address, without keeping it.

    Used to validate a selection before saving it, so Settings can refuse an
    address that belongs to an adapter that has since gone away.
    """
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind((str(host), int(port)))
            return True
        finally:
            probe.close()
    except (OSError, ValueError):
        return False
