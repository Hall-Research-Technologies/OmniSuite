"""One device read serves every browser watching the same thing.

The A/V projection is derived from the host's cache and costs nothing to
serve. USB and Multiview are not: reading USB state queries every endpoint's
`usb_icron` configuration, and reading a decoder's Multiview state reads three
nodes from that decoder. Measured, one client fetching each surface five times
cost 20 and 30 device calls; ten clients cost 200 and 300 -- exactly ten times,
which is the thing the architecture is supposed to prevent.

So observation goes through here: a short time-to-live, and single-flight so
concurrent readers wait for one answer instead of each starting their own.
Ten browsers on one classroom display become one read.

Two rules from the project's own hard-won list are built in:

  * Discarding a stale answer is not the same as not asking. A sequence number
    protects correctness; single-flight is what stops a slow read having
    another stacked on top of it.

  * An operation that already touched the device resets the cadence. After a
    Show or a switch, the next read must see what just happened rather than a
    cached picture of what came before, so writers invalidate the key.

This is observation only. Nothing here is consulted to decide whether an
operation is allowed, and no routing path reads through it.
"""
from __future__ import annotations

import threading
import time

#: How long an observed answer may be reused. Long enough that a room full of
#: phones is one read, short enough that a display someone else changed is
#: right again almost immediately.
DEFAULT_TTL_SECONDS = 2.0


class StateCache:
    """Memoise expensive device reads briefly, with single-flight per key."""

    def __init__(self, ttl=DEFAULT_TTL_SECONDS):
        self._ttl = float(ttl)
        self._lock = threading.Lock()
        self._entries: dict = {}        # key -> (expires_at, value)
        self._inflight: dict = {}       # key -> threading.Event
        self._stats = {"served": 0, "computed": 0, "joined": 0}

    def get(self, key, producer):
        """Return a fresh-enough value for `key`, computing it at most once.

        The first caller computes. Callers that arrive while that is running
        wait for it and share the result rather than starting a second read of
        the same device.
        """
        now = time.time()
        with self._lock:
            entry = self._entries.get(key)
            if entry and entry[0] > now:
                self._stats["served"] += 1
                return entry[1]
            waiting = self._inflight.get(key)
            if waiting is None:
                waiting = threading.Event()
                self._inflight[key] = waiting
                mine = True
            else:
                mine = False

        if not mine:
            # Somebody is already reading this. Wait for them rather than
            # asking the device the same question again.
            self._stats["joined"] += 1
            waiting.wait(timeout=30.0)
            with self._lock:
                entry = self._entries.get(key)
            if entry is not None:
                return entry[1]
            # The reader failed or timed out. Compute rather than return
            # nothing -- but do it without holding anybody else up.
            return producer()

        try:
            value = producer()
        except Exception:
            with self._lock:
                self._inflight.pop(key, None)
            waiting.set()
            raise

        with self._lock:
            self._entries[key] = (time.time() + self._ttl, value)
            self._inflight.pop(key, None)
            self._stats["computed"] += 1
        waiting.set()
        return value

    def invalidate(self, key=None):
        """Drop a cached answer, or all of them.

        Called after an operation that changed the thing being observed, so the
        next read reports what just happened instead of what came before.
        """
        with self._lock:
            if key is None:
                self._entries.clear()
            else:
                self._entries.pop(key, None)

    def stats(self) -> dict:
        with self._lock:
            return dict(self._stats)
