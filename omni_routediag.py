"""Opt-in timing diagnostics for the local route endpoint.

Off by default and silent. Switched on by setting OMNI_ROUTE_DIAG=1 in the
environment the application starts with; there is no control for it in the
product and nothing in the user interface mentions it.

Why it exists: a long soak saw the median latency of local route calls grow with
process age. Whatever causes that can only be argued about with numbers taken
from the running process, and instrumentation added afterwards changes the thing
being measured. So the instrument is part of the build, costs nothing while off
(the view is not wrapped at all), and while on appends one small JSON line per
route call to `route_diag.jsonl` in the data directory:

    at       wall-clock time the call finished
    ms       time spent in the route view, in milliseconds
    status   the HTTP status it answered with
    threads  live Python threads at that moment
    n        calls seen since start
    objects  every 50th call only: how many objects the garbage collector tracks
             (counting them is not free, so it is sampled)

The file is bounded: past a few megabytes it is moved aside once and started
again, so leaving the switch on cannot fill a disk. It records no request body,
address, credential or device name.
"""
from __future__ import annotations

import functools
import gc
import json
import logging
import os
import threading
import time
from pathlib import Path

log = logging.getLogger(__name__)

ENV = "OMNI_ROUTE_DIAG"
FILE_NAME = "route_diag.jsonl"
MAX_BYTES = 4 * 1024 * 1024
OBJECTS_EVERY = 50

_TRUE = ("1", "true", "yes", "on")


def enabled(environ=None) -> bool:
    value = (os.environ if environ is None else environ).get(ENV, "")
    return str(value).strip().lower() in _TRUE


def _status_of(response):
    """The HTTP status of whatever a Flask view returned, or None."""
    if isinstance(response, tuple) and len(response) > 1 and isinstance(response[1], int):
        return response[1]
    code = getattr(response, "status_code", None)
    return code if isinstance(code, int) else 200


def wrap(view, path):
    """`view`, timed. Used only when the switch is on."""
    path = Path(path)
    lock = threading.Lock()
    state = {"n": 0, "warned": False}

    def record(started, status):
        row = {"at": round(time.time(), 3),
               "ms": round((time.perf_counter() - started) * 1000, 2),
               "status": status, "threads": threading.active_count()}
        try:
            with lock:
                state["n"] += 1
                row["n"] = state["n"]
                if state["n"] % OBJECTS_EVERY == 0:
                    row["objects"] = len(gc.get_objects())
                try:
                    if path.exists() and path.stat().st_size > MAX_BYTES:
                        os.replace(path, path.with_name(path.name + ".1"))
                except OSError:
                    pass
                with open(path, "a", encoding="utf-8") as handle:
                    handle.write(json.dumps(row) + "\n")
        except Exception as exc:                # a diagnostic never breaks a route
            if not state["warned"]:
                state["warned"] = True
                log.warning("[ROUTE-DIAG] could not write %s: %s",
                            path.name, type(exc).__name__)

    @functools.wraps(view)
    def timed(*args, **kwargs):
        started = time.perf_counter()
        status = None
        try:
            response = view(*args, **kwargs)
            status = _status_of(response)
            return response
        except BaseException:
            status = 500
            raise
        finally:
            record(started, status)

    return timed


def install(app, endpoint, data_dir, environ=None) -> bool:
    """Wrap `endpoint`'s view when the switch is on. True when it did."""
    if not enabled(environ):
        return False
    view = app.view_functions.get(endpoint)
    if view is None:
        return False
    app.view_functions[endpoint] = wrap(view, Path(data_dir) / FILE_NAME)
    return True
