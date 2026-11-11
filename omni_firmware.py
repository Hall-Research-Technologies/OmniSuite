"""Firmware availability: what Hall Research publishes, compared with what is installed.

This module REPORTS. It never downloads a firmware file, never reads a download
link for any purpose, and never makes a device an upgrade target. Availability and
upgrade capability are separate facts: a model OmniSuite cannot upgrade (the
311/324 USB extenders) can still have a published version, and a model with a
published version is not thereby something OmniSuite will install.

Source: the structured JSON endpoint behind hallresearch.com's own product pages,
`https://hallresearch.com/api/products/<slug>`. Nothing is scraped from HTML and no
unofficial site is consulted. The slug comes from a fixed table below, never from
device data, so a device cannot steer a request.

Behaviour, in the order the brief states it:

  * one check shortly after the application launches, then one every seven days;
  * entirely in the background, with a bounded timeout, so a missing Internet
    connection or an unreachable Hall Research costs nothing and blocks nothing;
  * the last good answer per product is kept, with when it was fetched, and a
    failed check marks it stale rather than erasing it -- a failure never turns a
    known status into an unknown one;
  * the answer is persisted, so a restart shows the last known status at once.

A check that fails at the first request stops there: if Hall Research cannot be
reached, asking for the other products as well is only more traffic.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

from omni_users import _atomic_write_json

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1

#: The one host and path prefix a request may go to.
API_HOST = "hallresearch.com"
API_PATH = "/api/products/"
API_BASE = "https://" + API_HOST + API_PATH

#: Seconds. Bounded on every request; a slow server is an unavailable one.
REQUEST_TIMEOUT = 5.0
#: A product page is 20-110 KB. Anything near this is not a product page.
MAX_RESPONSE_BYTES = 5 * 1024 * 1024

#: The cadence the brief asks for.
CHECK_INTERVAL_SECONDS = 7 * 24 * 3600
#: How soon after launch the first check runs, so startup is never what waits.
LAUNCH_DELAY_SECONDS = 20
#: After a failed check, wait this long before trying again -- not every start,
#: not every minute.
RETRY_AFTER_FAILURE_SECONDS = 3600
#: Launching repeatedly (a crash loop, an operator restarting) must not become a
#: request per launch.
MIN_GAP_BETWEEN_ATTEMPTS = 600

STATE_UP_TO_DATE = "up_to_date"
STATE_AVAILABLE = "update_available"
STATE_UNKNOWN = "unknown"

#: What an operator reads. Never the internal name.
LABELS = {STATE_UP_TO_DATE: "Up to Date", STATE_AVAILABLE: "Available",
          STATE_UNKNOWN: "Unknown"}

# ---------------------------------------------------------------------------
# model -> official product page
# ---------------------------------------------------------------------------
#: Matched on the whole normalised model, never as a prefix. A prefix rule would
#: resolve `hw-omni-d4111-wp` -- which has no product page -- to the D4111 page
#: and report another product's firmware against it. Unmapped is Unknown, and
#: says why.
#:
#: The USB extenders appear under their protocol names (AT-OMNI-311/324) and the
#: operator-facing HW-OMNI-311/324; the page slug is the `at-` one for both. Their
#: pages publish no firmware, which is a truthful "Unknown", not an error.
MODEL_SLUGS = {
    "hw-omni-e4521": "hw-omni-e4521",
    "hw-omni-d4511": "hw-omni-d4511",
    "hw-omni-d4111": "hw-omni-d4111",
    "hw-omni-e4111": "hw-omni-e4111",
    "hw-omni-e4111-wp": "hw-omni-e4111-wp",
    "at-omni-111": "at-omni-111",
    "at-omni-111-wp": "at-omni-111-wp",
    "at-omni-112": "at-omni-112",
    "at-omni-121": "at-omni-121",
    "at-omni-311": "at-omni-311",
    "at-omni-324": "at-omni-324",
    "hw-omni-311": "at-omni-311",
    "hw-omni-324": "at-omni-324",
}

#: Slugs whose page exists but publishes no firmware at all.
NO_FIRMWARE_PUBLISHED = frozenset({"at-omni-311", "at-omni-324"})

REASON_NO_PAGE = "Hall Research has no product page for this model."
REASON_NO_FIRMWARE = "Hall Research publishes no firmware for this model."
REASON_NOT_CHECKED = "Not checked yet."
REASON_UNPARSEABLE = "The installed version could not be read."
REASON_UNPARSEABLE_PUBLISHED = "The published version could not be read."
REASON_NO_VERSION = "This device did not report a firmware version."


def normalise_model(model) -> str:
    return " ".join(str(model or "").strip().lower().split())


def slug_for(model):
    """The product page slug for a model, or None when there is no such page."""
    return MODEL_SLUGS.get(normalise_model(model))


# ---------------------------------------------------------------------------
# versions
# ---------------------------------------------------------------------------
_VERSION = re.compile(r"^(\d+(?:\.\d+)*)([A-Za-z]*)$")


def parse_version(text):
    """((int, ...), suffix_rank) or None when the text is not a version.

    Numeric, never string order: 2.0.10 is newer than 2.0.9. A leading `v` is
    dropped (Luma reports `V1.0.2.3`). Trailing letters are kept as a minor rank
    (`2.0.3b`). Anything else is unparseable, and an unparseable version means
    "cannot say", never "update".
    """
    if not isinstance(text, str):
        return None
    cleaned = text.strip()
    if cleaned[:1] in ("v", "V"):
        cleaned = cleaned[1:]
    match = _VERSION.match(cleaned)
    if not match:
        return None
    try:
        numbers = tuple(int(part) for part in match.group(1).split("."))
    except ValueError:
        return None
    suffix = match.group(2).lower()
    rank = 0 if not suffix else 1 + (ord(suffix[0]) - ord("a"))
    return numbers, rank


def _padded(a, b):
    width = max(len(a), len(b))
    return a + (0,) * (width - len(a)), b + (0,) * (width - len(b))


def compare(installed, published):
    """-1, 0 or 1 comparing two version strings, or None if either is unreadable.

    Shorter versions are zero-padded, so 1.0.2 equals 1.0.2.0, and a device
    reporting 1.0.2.3 against a published 1.0.2 is ahead of it, not behind.
    """
    a, b = parse_version(installed), parse_version(published)
    if a is None or b is None:
        return None
    x, y = _padded(a[0], b[0])
    if x != y:
        return -1 if x < y else 1
    if a[1] != b[1]:
        return -1 if a[1] < b[1] else 1
    return 0


def highest_version(candidates):
    """The highest parseable version in a list of strings, or None.

    The published list is not guaranteed to be sorted, so the first entry is not
    the newest.
    """
    best, best_text = None, None
    for text in candidates:
        parsed = parse_version(text)
        if parsed is None:
            continue
        if best is None or compare(text, best_text) == 1:
            best, best_text = parsed, str(text).strip()
    return best_text


# ---------------------------------------------------------------------------
# fetching (the only network code in this module)
# ---------------------------------------------------------------------------
class FirmwareSourceError(Exception):
    """The source could not be read. `transport` marks "could not reach it"."""

    def __init__(self, message, transport=False, missing=False):
        super().__init__(message)
        self.transport = transport
        self.missing = missing


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def valid_product_url(slug) -> str:
    """The request URL for a slug, or "" if it would leave the expected host/path."""
    if not isinstance(slug, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", slug):
        return ""
    url = API_BASE + slug
    parts = urlparse(url)
    if parts.scheme != "https" or parts.hostname != API_HOST \
            or not parts.path.startswith(API_PATH):
        return ""
    return url


def fetch_product(slug, opener=None) -> dict:
    """Read one product's JSON from Hall Research.

    Raises FirmwareSourceError. Bounded in time and size, HTTPS to one host, no
    redirects followed (a redirect off the host is a refusal, not a destination).
    """
    url = valid_product_url(slug)
    if not url:
        raise FirmwareSourceError("not an expected product address")
    opener = opener or urllib.request.build_opener(_NoRedirects)
    request = urllib.request.Request(
        url, headers={"Accept": "application/json",
                      "User-Agent": "OmniSuite-firmware-check"})
    try:
        with opener.open(request, timeout=REQUEST_TIMEOUT) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise FirmwareSourceError("no such product page", missing=True)
        raise FirmwareSourceError("HTTP %d" % exc.code, transport=exc.code >= 500)
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        raise FirmwareSourceError(type(exc).__name__, transport=True)
    if len(raw) > MAX_RESPONSE_BYTES:
        raise FirmwareSourceError("response too large")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise FirmwareSourceError("response was not JSON")
    if not isinstance(payload, dict):
        raise FirmwareSourceError("response was not an object")
    return payload


def latest_from_payload(payload) -> dict:
    """{"latest": str|None, "release_date": str|None} from one product's JSON.

    Reads `data.firmware[].version[].text` and the release date, and nothing
    else: the download link is deliberately never read.
    """
    data = payload.get("data") if isinstance(payload, dict) else None
    entries = data.get("firmware") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        entries = []
    versions = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        parts = entry.get("version")
        if not isinstance(parts, list):
            continue
        text = "".join(str(p.get("text") or "") for p in parts
                       if isinstance(p, dict)).strip()
        if text:
            date = entry.get("release_date")
            versions.append((text, date if isinstance(date, str) else None))
    best = highest_version([v for v, _d in versions])
    if best is None:
        return {"latest": None, "release_date": None}
    date = next((d for v, d in versions if v.strip() == best), None)
    return {"latest": best, "release_date": date}


# ---------------------------------------------------------------------------
# the checker
# ---------------------------------------------------------------------------
class FirmwareChecker:
    """Holds the last known catalogue, refreshes it in the background, persists it.

    `fetch(slug)` is injected: production passes `fetch_product`, a test passes a
    stub, and until a fetch is supplied the checker never touches the network.
    """

    def __init__(self, path, fetch=None, now=time.time, timer_factory=None):
        self._path = Path(path)
        self._fetch = fetch
        self._now = now
        self._timer_factory = timer_factory or threading.Timer
        self._lock = threading.RLock()
        self._running = threading.Lock()          # single-flight
        self._timer = None
        self._threads = []
        self._stopped = False
        self._catalog = {}                        # slug -> record
        self._last_attempt = 0.0
        self._last_success = 0.0
        self._last_error = ""
        self._load()

    # ---- persistence ----------------------------------------------------
    def _load(self) -> None:
        with self._lock:
            self._catalog, self._last_attempt = {}, 0.0
            self._last_success, self._last_error = 0.0, ""
            if not self._path.exists():
                return
            try:
                raw = json.loads(self._path.read_text(encoding="utf-8"))
                if not isinstance(raw, dict) or not isinstance(raw.get("products"), dict):
                    raise ValueError("unexpected shape")
            except Exception as exc:
                log.error("[FIRMWARE] %s is unreadable (%s); starting empty",
                          self._path.name, type(exc).__name__)
                try:
                    self._path.replace(self._path.with_suffix(".corrupt"))
                except OSError:
                    pass
                return
            for slug, record in raw["products"].items():
                if (valid_product_url(slug) and isinstance(record, dict)):
                    latest = record.get("latest")
                    self._catalog[slug] = {
                        "latest": latest if isinstance(latest, str)
                        and parse_version(latest) else None,
                        "release_date": record.get("release_date")
                        if isinstance(record.get("release_date"), str) else None,
                        "published": bool(record.get("published")),
                        "fetched_at": _number(record.get("fetched_at")),
                    }
            self._last_attempt = _number(raw.get("last_attempt"))
            self._last_success = _number(raw.get("last_success"))
            self._last_error = str(raw.get("last_error") or "")[:120]

    def reload(self) -> None:
        self._load()

    def _persist_locked(self) -> None:
        _atomic_write_json(self._path, {
            "version": SCHEMA_VERSION, "last_attempt": self._last_attempt,
            "last_success": self._last_success, "last_error": self._last_error,
            "products": self._catalog})

    # ---- enabling the network ------------------------------------------
    def enable_network(self, fetch) -> None:
        self._fetch = fetch

    @property
    def network_enabled(self) -> bool:
        return self._fetch is not None

    # ---- the check ------------------------------------------------------
    def check(self, slugs=None) -> dict:
        """One sweep. Single-flight, never raises, never blocks a second caller.

        Returns {"ran": bool, "ok": bool, "fetched": n, "failed": n}.
        """
        if self._fetch is None:
            return {"ran": False, "ok": False, "reason": "network_disabled"}
        if not self._running.acquire(blocking=False):
            return {"ran": False, "ok": False, "reason": "already_running"}
        try:
            wanted = list(slugs) if slugs is not None else sorted(set(MODEL_SLUGS.values()))
            with self._lock:
                self._last_attempt = self._now()
            fetched = failed = 0
            error = ""
            for slug in wanted:
                try:
                    payload = self._fetch(slug)
                    found = latest_from_payload(payload)
                    with self._lock:
                        self._catalog[slug] = {
                            "latest": found["latest"],
                            "release_date": found["release_date"],
                            "published": found["latest"] is not None,
                            "fetched_at": self._now()}
                    fetched += 1
                except FirmwareSourceError as exc:
                    if exc.missing:
                        # No page is an answer, not an outage.
                        with self._lock:
                            self._catalog[slug] = {
                                "latest": None, "release_date": None,
                                "published": False, "fetched_at": self._now()}
                        fetched += 1
                        continue
                    failed += 1
                    error = str(exc)[:120]
                    if exc.transport:
                        # Could not reach Hall Research at all: asking for the
                        # rest only adds traffic. Keep what is already known.
                        break
                except Exception as exc:          # a stub or a bug: never fatal
                    failed += 1
                    error = type(exc).__name__
                    break
            ok = fetched > 0 and failed == 0
            with self._lock:
                if fetched:
                    self._last_success = self._now()
                self._last_error = "" if ok else (error or "no answer")
                self._persist_locked()
            if not ok:
                log.info("[FIRMWARE] check did not complete (%s); keeping the last "
                         "known result", self._last_error)
            return {"ran": True, "ok": ok, "fetched": fetched, "failed": failed}
        finally:
            self._running.release()

    def check_async(self) -> dict:
        """Start a sweep in the background for the manual "Check Now".

        Returns at once. The thread is recorded so a caller (a test, shutdown) has
        something to wait on rather than an anonymous daemon.
        """
        if self._fetch is None:
            return {"started": False, "reason": "network_disabled"}
        if self._running.locked():
            return {"started": False, "reason": "already_running"}
        thread = threading.Thread(target=self.check, daemon=True,
                                  name="firmware-check")
        with self._lock:
            self._threads = [t for t in self._threads if t.is_alive()] + [thread]
        thread.start()
        return {"started": True}

    def wait(self, timeout=10.0) -> bool:
        """Wait for any background sweep to finish. True when none is running."""
        with self._lock:
            threads = list(self._threads)
        deadline = self._now() + timeout
        for thread in threads:
            thread.join(max(0.0, deadline - self._now()))
        return not any(t.is_alive() for t in threads)

    # ---- scheduling -------------------------------------------------------
    def seconds_until_next(self, first=False) -> float:
        """How long to wait before the next scheduled check.

        `first` is the launch check: shortly after start, unless an attempt was
        made so recently that a restart loop would become a request per start.
        """
        with self._lock:
            if first:
                since = self._now() - self._last_attempt
                return float(max(LAUNCH_DELAY_SECONDS,
                                 MIN_GAP_BETWEEN_ATTEMPTS - since
                                 if self._last_attempt else 0))
            if self._last_success and self._last_error == "":
                return max(0.0, self._last_success + CHECK_INTERVAL_SECONDS - self._now())
            if self._last_attempt:
                return max(0.0, self._last_attempt + RETRY_AFTER_FAILURE_SECONDS
                           - self._now())
        return float(LAUNCH_DELAY_SECONDS)

    def arm(self, first=False) -> None:
        """Schedule the next check. Replaces any pending timer; does nothing once stopped."""
        with self._lock:
            if self._stopped or self._fetch is None:
                return
            if self._timer is not None:
                self._timer.cancel()
            timer = self._timer_factory(self.seconds_until_next(first), self._fire)
            timer.daemon = True
            self._timer = timer
        timer.start()

    def _fire(self) -> None:
        try:
            self.check()
        finally:
            self.arm()

    def stop(self) -> None:
        with self._lock:
            self._stopped = True
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None

    # ---- reading ----------------------------------------------------------
    def snapshot(self) -> dict:
        with self._lock:
            checked = self._last_success
            state = ("ready" if checked and not self._last_error else
                     ("stale" if checked else
                      ("unavailable" if self._last_attempt else "pending")))
            return {"state": state,
                    "running": self._running.locked(),
                    "last_success": checked or None,
                    "last_attempt": self._last_attempt or None,
                    "stale": bool(checked and self._last_error),
                    "next_check_in": int(self.seconds_until_next())
                    if self._fetch is not None else None,
                    "products": {k: dict(v) for k, v in self._catalog.items()}}

    def describe(self, model, installed) -> dict:
        """The firmware status of one device, with the reason when it is Unknown.

        Independent of whether OmniSuite can upgrade the device.
        """
        slug = slug_for(model)
        base = {"state": STATE_UNKNOWN, "label": LABELS[STATE_UNKNOWN],
                "installed": str(installed or "").strip() or None,
                "latest": None, "reason": ""}
        if slug is None:
            base["reason"] = REASON_NO_PAGE
            return base
        with self._lock:
            record = dict(self._catalog.get(slug) or {})
            stale = bool(self._last_success and self._last_error)
        if not record:
            base["reason"] = REASON_NOT_CHECKED
            return base
        if not record.get("published") or not record.get("latest"):
            base["reason"] = (REASON_NO_FIRMWARE if slug in NO_FIRMWARE_PUBLISHED
                              or record.get("fetched_at") else REASON_NOT_CHECKED)
            return base
        base["latest"] = record["latest"]
        base["release_date"] = record.get("release_date")
        base["stale"] = stale
        if not base["installed"]:
            base["reason"] = REASON_NO_VERSION
            return base
        if parse_version(base["installed"]) is None:
            base["reason"] = REASON_UNPARSEABLE
            return base
        order = compare(base["installed"], record["latest"])
        if order is None:
            base["reason"] = REASON_UNPARSEABLE_PUBLISHED
        elif order < 0:
            base.update(state=STATE_AVAILABLE, label=LABELS[STATE_AVAILABLE])
        else:
            base.update(state=STATE_UP_TO_DATE, label=LABELS[STATE_UP_TO_DATE])
        return base


def _number(value) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    if value != value or value in (float("inf"), float("-inf")) or value < 0:
        return 0.0
    return float(value)
