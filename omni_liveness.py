"""What the application currently believes about whether a unit answers, and the gate that uses it.

A device that does not answer is an upstream condition, not a fault in this application, and a route
to one should say so at once instead of spending a WebSocket timeout discovering it. This keeps two
things apart:

  * what is *recorded* -- an observation made by something that was already talking to the unit
    (the cache verification sweep, a decoder poll, a route that just succeeded or failed). Recording
    costs nothing and adds no traffic. It is what the screens show.
  * what an *operation* may rely on -- a fresh observation, or, when there is none, one bounded TCP
    connect made for that operation alone. That is the same preflight the Multiview planner already
    takes before spending timeouts on a device, and it is what turns "the unit does not answer" into a
    0.6 second refusal.

Nothing here polls. There is no timer and no thread: an observation is made when an operation or an
existing read makes it.

A gate that cannot tell is not a gate that refuses. Unknown is reported as unknown and the operation
proceeds to the verified path, which fails closed on its own account; only a unit that has been seen
not to answer is refused, and it is refused by name.
"""
from __future__ import annotations

import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor

#: An observation younger than this is believed by a gate without asking the unit again.
FRESH_ONLINE_SECONDS = 20.0
#: A "not answering" observation is believed for a shorter time, so a unit that was just plugged in
#: is not refused on the strength of a reading taken before it booted.
FRESH_OFFLINE_SECONDS = 4.0
#: One bounded connect for an operation that has no fresh observation.
PROBE_TIMEOUT_SECONDS = 0.6
#: Bounded, so an operation that names many units cannot open an unbounded number of sockets.
MAX_PROBE_WORKERS = 8


# The probe is the one place this module reaches a network, so it is the one place a test replaces.
# A suite that routes through the real engine must not open a socket to find out whether a fixture
# address answers; the test package installs an answer here and the transport fence stays exactly
# where it was.
_probe_override = None


def set_probe_override(function):
    """Answer every probe from `function(ip) -> bool` instead of the network. None restores the real one."""
    global _probe_override
    _probe_override = function


def tcp_probe(ip, port=80, timeout=PROBE_TIMEOUT_SECONDS):
    """One bounded TCP connect: does anything answer on the management port."""
    if _probe_override is not None:
        return bool(_probe_override(ip))
    try:
        with socket.create_connection((str(ip), int(port)), timeout=timeout):
            return True
    except OSError:
        return False


class UnitLiveness:
    def __init__(self, probe, now=time.monotonic):
        self._probe = probe
        self._now = now
        self._lock = threading.Lock()
        self._refresh_gate = threading.Lock()   # one look-again batch at a time
        self._seen = {}          # ip -> (reachable, at, source)

    # ---------------------------------------------------------------- recording
    def record(self, ip, reachable, source=""):
        """Note what something already talking to `ip` just found out. Never issues a request."""
        ip = str(ip or "").strip()
        if not ip:
            return
        with self._lock:
            self._seen[ip] = (bool(reachable), self._now(), str(source or ""))

    def forget(self, ip=None):
        """Drop one unit's record, or all of them (the inventory was cleared)."""
        with self._lock:
            if ip is None:
                self._seen.clear()
            else:
                self._seen.pop(str(ip or "").strip(), None)

    # ------------------------------------------------------------------ reading
    def known(self, ip):
        """True, False, or None when nothing has been observed. For display; age does not matter."""
        with self._lock:
            entry = self._seen.get(str(ip or "").strip())
        return None if entry is None else entry[0]

    def snapshot(self):
        with self._lock:
            return {ip: {"reachable": r, "age": round(self._now() - at, 1), "source": src}
                    for ip, (r, at, src) in self._seen.items()}

    def stale_offline(self, min_age=10.0):
        """The units recorded as not answering and not looked at for `min_age` seconds. Reads only."""
        with self._lock:
            now = self._now()
            return [ip for ip, (reachable, at, _source) in self._seen.items()
                    if not reachable and now - at >= min_age]

    def refresh_offline(self, min_age=10.0):
        """Look again, once, at units recorded as not answering and not looked at for `min_age` seconds.

        A unit that came back must not stay disabled on screen for as long as nothing happens to read
        it. This is the whole of it: only units already seen not to answer, at most once per `min_age`
        each, concurrently and bounded -- so with every unit answering it does nothing at all.

        Single-flight: while one batch is being looked at, another caller does not start a second one
        over the same units. Measured before this: twelve pages open at once issued 192 probes for
        sixteen units, and each reader waited out its own batch.
        """
        work = self.begin_refresh(min_age)
        return work() if work is not None else []

    def begin_refresh(self, min_age=10.0):
        """Claim the look-again batch and hand back the work, or None when there is nothing to do.

        None means nothing is stale, or another batch is already out. Otherwise the caller MUST run the
        returned function -- it releases the claim when it finishes, however it finishes -- on whatever
        thread suits it. This module starts no thread of its own, so a request that must not wait for
        the probes can give the work to a registered background thread and return at once.
        """
        stale = self.stale_offline(min_age)
        if not stale:
            return None
        if not self._refresh_gate.acquire(blocking=False):
            return None

        def work():
            try:
                self.check_many(stale)
            finally:
                self._refresh_gate.release()
            return stale
        return work

    # --------------------------------------------------------------------- gate
    def check(self, ip):
        """Whether an operation may rely on `ip` answering right now.

        A fresh observation is believed; otherwise one bounded connect decides, and is recorded so the
        next operation inside the window does not repeat it.
        """
        ip = str(ip or "").strip()
        if not ip:
            return False
        with self._lock:
            entry = self._seen.get(ip)
        if entry is not None:
            reachable, at, _source = entry
            window = FRESH_ONLINE_SECONDS if reachable else FRESH_OFFLINE_SECONDS
            if self._now() - at <= window:
                return reachable
        try:
            reachable = bool(self._probe(ip))
        except Exception:
            reachable = False
        self.record(ip, reachable, "probe")
        return reachable

    def check_many(self, ips):
        """{ip: reachable} for every address, probed concurrently so one dead unit does not delay the rest."""
        ordered = []
        for ip in ips:
            ip = str(ip or "").strip()
            if ip and ip not in ordered:
                ordered.append(ip)
        if not ordered:
            return {}
        if len(ordered) == 1:
            return {ordered[0]: self.check(ordered[0])}
        with ThreadPoolExecutor(max_workers=min(MAX_PROBE_WORKERS, len(ordered))) as pool:
            results = list(pool.map(self.check, ordered))
        return dict(zip(ordered, results))
