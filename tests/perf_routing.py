"""Routing latency harness for the restricted-user routing endpoints.

Not a test (no `test_` prefix; discovery does not collect it). Run it directly:

    python -m tests.perf_routing            # prints a table and JSON

The device is a stub with a fixed, modelled latency (OMNI_PERF_LATENCY seconds,
default 0.5): every route the engine would make takes exactly that long, which is
the part of a real route that cannot be shortened by this application. What the
harness measures is everything the application adds around it -- serialisation,
queueing, and whether one request has to finish before the next may begin. It makes
no claim about hardware time; hardware numbers need a lab.

The client side is what the page does. "Sequential" is the old page: it sent one
request and waited for the answer before the next. "Concurrent" is the new page: it
sends the next request at once. Both run through the same endpoints, the same gate
and the same stub, so the before and after tables come from one harness.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("OMNI_SMOKE", "1")

import omni_gate                                     # noqa: E402
import omni_remote                                   # noqa: E402
from tests import test_display_group_integrity as groups_base   # noqa: E402
from tests import test_user_routing as harness       # noqa: E402

LATENCY = float(os.environ.get("OMNI_PERF_LATENCY", "0.5"))
SRC = harness.TEACHER_PC
mac = groups_base.mac


class Perf(groups_base.GroupLifecycleBase):
    def setUp(self):
        super().setUp()
        self.delay = LATENCY
        inner = self._route_stub()
        self._patch_registered("route_view", inner)

    def _route_stub(self):
        def slow_route():
            from flask import request as flask_request, jsonify
            body = flask_request.get_json(silent=True) or {}
            time.sleep(self.delay)
            self.routes.append(dict(body))
            return jsonify({"ok": True, "status": "VERIFIED"}), 200
        return slow_route

    def _call(self, session, path, body):
        client = self.srv.app.test_client()
        client.set_cookie(omni_gate.SESSION_COOKIE, session.token)
        start = time.perf_counter()
        reply = client.post(
            path, json=body,
            environ_base={omni_remote.LISTENER_KEY: omni_remote.REMOTE},
            headers={"X-OmniSuite-CSRF": session.csrf_token})
        return reply, time.perf_counter() - start

    def run_perf(self):
        a = self.make_group("Room A", [1, 2])
        b = self.make_group("Room B", [3, 4])
        teacher = self.store.find_by_username("teacher")
        from omni_users import Permissions
        self.store.set_permissions(teacher.id, Permissions(
            features=("av",), sources=[SRC], groups=[a, b],
            destinations=[mac(n) for n in range(1, 7)]))
        session = self.session_for(self.store.find_by_username("teacher"))
        many = "/api/user/route_many" in {
            r.rule for r in self.srv.app.url_map.iter_rules()}
        out = {"latency_s": self.delay, "route_many_available": many}

        def single(n):
            return self._call(session, "/api/user/route",
                              {"source": SRC, "destination": mac(n)})

        # 1. single route
        _r, t = single(1)
        out["single_route_s"] = round(t, 3)

        # 2. five consecutive routes to five displays
        start = time.perf_counter()
        for n in (1, 2, 3, 4, 5):
            single(n)
        out["five_consecutive_sequential_s"] = round(time.perf_counter() - start, 3)
        start = time.perf_counter()
        with ThreadPoolExecutor(5) as pool:
            list(pool.map(single, (1, 2, 3, 4, 5)))
        out["five_consecutive_concurrent_s"] = round(time.perf_counter() - start, 3)

        # 3. one source -> three individual displays
        start = time.perf_counter()
        for n in (4, 5, 6):
            single(n)
        out["three_displays_as_three_requests_s"] = round(time.perf_counter() - start, 3)
        if many:
            reply, t = self._call(session, "/api/user/route_many", {
                "source": SRC, "destinations": [mac(4), mac(5), mac(6)]})
            body = reply.get_json()
            out["three_displays_route_many_s"] = round(t, 3)
            out["three_displays_route_many_result"] = [body.get("routed"), body.get("total")]

        # 4. one source -> two groups (2 members each)
        start = time.perf_counter()
        for g in (a, b):
            self._call(session, "/api/user/route_group", {"source": SRC, "group": g})
        out["two_groups_as_two_requests_s"] = round(time.perf_counter() - start, 3)
        if many:
            reply, t = self._call(session, "/api/user/route_many",
                                  {"source": SRC, "groups": [a, b]})
            body = reply.get_json()
            out["two_groups_route_many_s"] = round(t, 3)
            out["two_groups_route_many_result"] = [body.get("routed"), body.get("total")]

        # 5. mixed: two displays outside the group plus one group
        start = time.perf_counter()
        self._call(session, "/api/user/route_group", {"source": SRC, "group": a})
        for n in (5, 6):
            single(n)
        out["mixed_as_three_requests_s"] = round(time.perf_counter() - start, 3)
        if many:
            reply, t = self._call(session, "/api/user/route_many", {
                "source": SRC, "destinations": [mac(5), mac(6)], "groups": [a]})
            body = reply.get_json()
            out["mixed_route_many_s"] = round(t, 3)
            out["mixed_route_many_result"] = [body.get("routed"), body.get("total")]

        # 6. dedupe: a display chosen individually and through its group
        if many:
            self.routes.clear()
            reply, t = self._call(session, "/api/user/route_many", {
                "source": SRC, "destinations": [mac(1), mac(5)], "groups": [a]})
            body = reply.get_json()
            out["dedupe_route_many_result"] = [body.get("routed"), body.get("total")]
            out["dedupe_engine_calls"] = len(self.routes)
        return out


class RunPerf(Perf):
    def test_run(self):
        result = self.run_perf()
        print("PERF " + json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    unittest.main(argv=["perf", "RunPerf"], verbosity=0)
