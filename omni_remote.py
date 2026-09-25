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


def tag_for_bind(host: str) -> str:
    """Which tag a listener bound to `host` deserves.

    Loopback earns local trust; anything else does not, *including* the
    application's own primary listener. That matters because OMNI_HOST can be
    forced to a routable address without remote access being enabled at all,
    and a listener that answered the network while handing out local trust
    would be the whole boundary gone.
    """
    return LOCAL if is_loopback_address(host) else REMOTE


def serve_tagged(host, port, app, **kwargs):
    """Build a WSGI server whose requests carry the right listener tag.

    Every network listener in the product is created through this, so "which
    listener accepted this" is decided once, from the address actually bound,
    rather than at three call sites that could disagree.
    """
    from werkzeug.serving import make_server
    return make_server(host, port, _ListenerTag(app, tag_for_bind(host)),
                       **kwargs)


def listener_of(environ) -> str:
    """Which listener served this request.

    An untagged request is LOCAL, and that is a statement about what can
    actually produce one. Every listener the product opens is tagged by
    `serve_tagged` or by `RemoteListener`, so a request with no tag did not
    arrive over a socket at all -- it came from an in-process caller such as
    Flask's test client, which is as local as anything gets.

    This was the other way round first, on the reasoning that unknown should
    mean untrusted. It made the whole existing test suite fail closed, which
    looked safe and was actually a good way to hide the real hole: the packaged
    launcher was opening a third, unwrapped listener. Tagging every listener at
    the point it is bound fixes the cause; treating unknown as remote only
    concealed it.
    """
    value = environ.get(LISTENER_KEY)
    return REMOTE if value == REMOTE else LOCAL


def is_local_request(environ) -> bool:
    """True for a request that did not arrive on a network-facing listener.

    Deliberately not `remote_addr in 127.0.0.0/8`: that is a statement about an
    address the request claims to come from, and this is a statement about the
    socket the operating system accepted it on.
    """
    return listener_of(environ) == LOCAL


def is_loopback_address(address: str) -> bool:
    """Whether a literal address is loopback. Used for validating a bind choice.

    This is about configuration, never about trusting a request.
    """
    try:
        return ipaddress.ip_address(str(address).strip()) in _LOOPBACK
    except ValueError:
        return False


def is_wildcard_address(address: str) -> bool:
    """Whether an address means "every interface on this machine".

    Its own predicate because `is_loopback_address("0.0.0.0")` is False --
    0.0.0.0 is the unspecified address, not a loopback one -- so a check
    written to refuse loopback silently accepts the one value that exposes the
    host on every network it is attached to. A test that removed the
    availability check found that: the guard had been passing for the wrong
    reason.
    """
    try:
        return ipaddress.ip_address(str(address).strip()).is_unspecified
    except ValueError:
        return False


def is_usable_remote_address(address: str) -> bool:
    """Whether this is a concrete address an administrator may bind remotely.

    A remote listener is always a deliberate choice of one network. Loopback is
    already served, the wildcard is every network at once, and anything that is
    not an address at all cannot be one of them.

    Nor can anything that is not a host: a broadcast or multicast address
    parses perfectly well and cannot be bound to, and this guard used to call
    both usable. Refused as a category rather than as a list of values, so the
    next reserved range does not need remembering. Link-local is kept, because
    a machine really can have one and an administrator on a directly attached
    network may legitimately choose it.
    """
    text = str(address or "").strip()
    if not text:
        return False
    try:
        parsed = ipaddress.ip_address(text)
    except ValueError:
        return False
    if is_loopback_address(text) or is_wildcard_address(text):
        return False
    if parsed.is_multicast or parsed.is_reserved or parsed.is_unspecified:
        # `is_reserved` is 240.0.0.0/4, which includes the limited broadcast
        # address 255.255.255.255 -- checked, because a separate clause for it
        # was carrying a comment claiming otherwise.
        return False
    return True


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
        self._scheme = "http"

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
        """What Settings shows: enabled, where, and the URL that reaches it.

        The advertised URL carries the scheme actually in use. An address given
        as http:// when the listener is serving TLS would simply fail to
        connect, and the other way round would be worse.
        """
        with self._lock:
            if not self._server:
                return {"enabled": False, "host": None, "port": None,
                        "url": None, "scheme": None, "secure": False}
            host, port = self._bound
            return {"enabled": True, "host": host, "port": port,
                    "scheme": self._scheme,
                    "secure": self._scheme == "https",
                    "url": f"{self._scheme}://{host}:{port}/"}

    # -- lifecycle -----------------------------------------------------
    def start(self, host: str, port: int, ssl_context=None) -> dict:
        """Begin answering on `host:port`, over TLS when a context is given.

        Returns a result dict rather than raising for an ordinary operator
        mistake -- an address that is not on this machine, or a port already
        taken -- because those are answers the Settings page has to display, not
        faults.

        There is exactly one remote listener, and it is either TLS or it is
        not. No plaintext listener is opened alongside an HTTPS one, not even
        to redirect: a redirect surface would accept a request -- including,
        eventually, somebody typing a password into a page it served -- before
        it could be protected. An operator who reaches the wrong scheme gets a
        connection failure, which is unambiguous.
        """
        host = str(host or "").strip()
        if not host:
            return {"ok": False, "error": "no address selected"}
        if not is_usable_remote_address(host):
            # Loopback is already served and binding it here would tag remote
            # requests as local; the wildcard exposes every network at once,
            # which is never something to arrive at by default.
            reason = ("loopback is always served locally and cannot be chosen "
                      "as the remote address" if is_loopback_address(host)
                      else ("choose one network rather than all of them"
                            if is_wildcard_address(host)
                            else "that is not a usable address"))
            return {"ok": False, "error": reason}
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
                                     threaded=True, ssl_context=ssl_context)
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
            self._scheme = "https" if ssl_context is not None else "http"
            log.info("[REMOTE] listening on %s://%s:%s", self._scheme, host, port)
            return {"ok": True, **self.describe()}

    def stop(self) -> dict:
        """Stop answering remotely. The local listener is untouched."""
        with self._lock:
            server, thread = self._server, self._thread
            self._server = self._thread = self._bound = None
            self._scheme = "http"
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
        # One concrete network. Loopback is already served, and the wildcard is
        # not a network an administrator can reason about.
        if not is_usable_remote_address(address):
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
