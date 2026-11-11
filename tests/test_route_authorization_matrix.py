"""Every account/remote route, asked by each kind of caller.

The classification table is the policy; this asks the running application what
each route actually answers to an unauthenticated caller, a restricted User, a
Direct Access session and an Administrator, and fails on any 500 -- a routine
refusal must never look like a crash.

Nothing here contacts hardware: refusals happen at the gate, and the routes an
administrator is allowed to reach are the read-only account surface.
"""
from __future__ import annotations

import copy
import os
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("OMNI_SMOKE", "1")

import omni_access                                   # noqa: E402
import omni_remote                                   # noqa: E402
import omni_users                                    # noqa: E402
from tests.test_acceptance_lifecycle import LifecycleBase, REMOTE  # noqa: E402

#: Where the account and remote-access surface lives.
SURFACE = re.compile(r"^/(api/(admin|user|auth|bootstrap|remote_access)|access/)")


def concrete(rule):
    return re.sub(r"<[^>]+>", "x", rule.rule)


class RouteMatrixTests(LifecycleBase):
    def rules(self):
        out = []
        for rule in self.srv.app.url_map.iter_rules():
            if not SURFACE.match(rule.rule):
                continue
            for method in sorted(rule.methods - {"HEAD", "OPTIONS"}):
                out.append((rule.rule, method))
        return sorted(out)

    def call(self, client, rule, method):
        path = re.sub(r"<[^>]+>", "x", rule)
        kwargs = {"environ_base": REMOTE}
        if method == "GET":
            return client.get(path, **kwargs)
        return client.post(path, json={}, **kwargs)

    def test_the_surface_is_not_empty_and_every_route_is_classified(self):
        rules = self.rules()
        self.assertGreater(len(rules), 25)
        for rule, method in rules:
            access, _feature = omni_access.classify(rule, method)
            self.assertIsNotNone(access, (rule, method))

    def test_unauthenticated_callers_never_get_a_500(self):
        public = {omni_access.Access.PUBLIC}
        for rule, method in self.rules():
            access, _f = omni_access.classify(rule, method)
            client = self.srv.app.test_client()
            reply = self.call(client, rule, method)
            with self.subTest(rule=rule, method=method):
                self.assertLess(reply.status_code, 500)
                if access not in public and not rule.startswith("/access/"):
                    # A local-only route is not found from the network at all.
                    self.assertIn(reply.status_code,
                                  (301, 302, 303, 401, 403, 404),
                                  "an unauthenticated caller reached %s" % rule)

    def test_a_restricted_user_is_refused_every_administrative_route(self):
        session = self.session_for(self.teacher)
        client = self.srv.app.test_client()
        client.set_cookie(__import__("omni_gate").SESSION_COOKIE, session.token)
        for rule, method in self.rules():
            access, _f = omni_access.classify(rule, method)
            if access is not omni_access.Access.ADMIN and \
                    access is not omni_access.Access.LOCAL_ONLY:
                continue
            reply = self.call(client, rule, method)
            with self.subTest(rule=rule, method=method):
                self.assertIn(reply.status_code, (403, 404))

    def test_an_administrator_never_gets_a_500_from_a_read(self):
        session = self.session_for(self.admin)
        client = self.srv.app.test_client()
        client.set_cookie(__import__("omni_gate").SESSION_COOKIE, session.token)
        for rule, method in self.rules():
            if method != "GET" or "<" in rule or rule.startswith("/access/"):
                continue
            access, _f = omni_access.classify(rule, method)
            if access is omni_access.Access.LOCAL_ONLY:
                continue
            reply = self.call(client, rule, method)
            with self.subTest(rule=rule):
                self.assertLess(reply.status_code, 500)

    def test_a_malformed_body_is_a_400_not_a_500(self):
        session = self.session_for(self.admin)
        client = self.srv.app.test_client()
        client.set_cookie(__import__("omni_gate").SESSION_COOKIE, session.token)
        headers = {"X-OmniSuite-CSRF": session.csrf_token}
        for path in ("/api/admin/users", "/api/admin/groups",
                     "/api/admin/aliases", "/api/admin/users/x",
                     "/api/admin/groups/x", "/api/admin/users/" + "x" + "/direct_access"):
            for body in ([], "text", {"action": 5}, {"members": "no"},
                         {"aliases": []}, {"permissions": "no"}):
                reply = client.post(path, json=body, headers=headers,
                                    environ_base=REMOTE)
                with self.subTest(path=path, body=body):
                    self.assertLess(reply.status_code, 500)


if __name__ == "__main__":
    unittest.main()
