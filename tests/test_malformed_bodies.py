"""Every JSON endpoint refuses a body that is not the object it needs, without a 5xx.

Live qualification of the first release candidate found that a list, a string or
a number as a request body gave HTTP 500 with a Python message on several legacy
endpoints, and that an unparseable or null body on Clear Units was read as an
empty request -- which for Clear Units means "clear".

One parser (`_json_dict`) now serves every handler. This module holds it to that:

  * a sweep driven by the application's own route table posts five bodies to
    every POST/PUT/PATCH/DELETE route, so a handler added tomorrow is covered
    without anyone remembering this file, and asserts that none answers 5xx;
  * the named endpoints from the report answer exactly 400, with a sentence and
    no Python text;
  * a destructive endpoint needs a body that says something, and the page's own
    `{}` still works;
  * a wrong-typed value inside an otherwise valid object (`{"targets": 5}`).

The transport is fenced for the whole process, so no request here can reach a
device. Anything that would touch the host (opening a folder) is stubbed.
"""
from __future__ import annotations

import json
import os
import re
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("OMNI_SMOKE", "1")

import OmniMatrix_upgrade_server_v7_6y as srv           # noqa: E402
from tests.test_usb_integration import ServerTestBase   # noqa: E402
from tests.test_clear_protection import ClearTestBase, UNITS   # noqa: E402

# Wrong-typed values, in three separate families so that the check for one cannot hide
# the absence of another: fields that are text, fields that are lists, and the rest.
TEXT_KEYS = ("ip", "mac", "hostname", "name", "username", "password", "file", "address", "action",
             "mode", "transport", "interface_ip", "subnet_mask", "source", "destination", "adapter_id",
             "layout", "decoder", "encoder", "type", "gateway", "passphrase", "target", "filter", "rex",
             "lex", "device")
LIST_KEYS = ("ips", "macs", "members", "ranges", "devices")
OTHER_KEYS = ("targets", "range", "names", "member", "id", "ids", "sources", "destinations",
              "port", "files", "enabled", "windows", "preset", "version", "peers", "host",
              "cell", "cells", "value", "key")
WRONG_TYPES = tuple(json.dumps({k: wrong for k in keys})
                    for keys in (TEXT_KEYS, OTHER_KEYS)
                    for wrong in (5, [], {"a": 1}, None)) + (
                    json.dumps({k: 5 for k in LIST_KEYS}), json.dumps({k: {"a": 1} for k in LIST_KEYS}))
BODIES = ("[]", "null", "5", '"x"', '{"a":[]}') + WRONG_TYPES
WRITES = {"POST", "PUT", "PATCH", "DELETE"}
PYTHON_TEXT = re.compile(r"object has no attribute|Traceback|NoneType|is not subscriptable|"
                         r"not iterable|unhashable|KeyError|TypeError|AttributeError")


def _routes():
    found = []
    for rule in srv.app.url_map.iter_rules():
        methods = sorted(rule.methods & WRITES)
        if methods:
            path = re.sub(r"<[^>]+>", "x", rule.rule)
            found.append((path, methods[0]))
    return sorted(set(found))


class MalformedBodySweepTests(ServerTestBase):
    maxDiff = None

    def setUp(self):
        super().setUp()
        self.client = srv.app.test_client()
        # Opening a folder on the host is the one handler whose effect is not a
        # network call; nothing here may do it.
        for owner, name in (("os", "startfile"), ("subprocess", "Popen"), ("subprocess", "run")):
            module = __import__(owner)
            if hasattr(module, name):
                patcher = mock.patch.object(module, name, side_effect=AssertionError(
                    "a malformed request reached %s.%s" % (owner, name)))
                patcher.start()
                self.addCleanup(patcher.stop)

    def send(self, method, path, raw):
        return self.client.open(path, method=method, data=raw,
                                content_type="application/json")

    def test_the_sweep_sees_the_whole_route_table(self):
        routes = _routes()
        self.assertGreater(len(routes), 80, "the sweep found suspiciously few routes")
        paths = [p for p, _ in routes]
        for needed in ("/api/route", "/api/scan", "/api/remove_units", "/api/clear_units",
                       "/api/usb_extenders/discover_ip", "/api/admin/groups"):
            self.assertIn(needed, paths)

    def test_no_json_endpoint_answers_5xx_to_a_malformed_body(self):
        failures = []
        for path, method in _routes():
            if path == "/api/open_firmware_folder":
                continue
            for raw in BODIES:
                reply = self.send(method, path, raw)
                # 502-504 say a device did not answer (the transport is fenced here);
                # that is an upstream condition, not a fault in the request handling.
                if reply.status_code == 500 or reply.status_code > 504:
                    failures.append("%s %s body=%s -> %s %s" % (
                        method, path, raw, reply.status_code, reply.get_data(as_text=True)[:100]))
        self.assertEqual(failures, [], "\n".join(failures))

    def test_no_refusal_carries_python_text(self):
        leaks = []
        for path, method in _routes():
            if path == "/api/open_firmware_folder":
                continue
            for raw in BODIES:
                reply = self.send(method, path, raw)
                text = reply.get_data(as_text=True)
                if reply.status_code >= 400 and PYTHON_TEXT.search(text):
                    leaks.append("%s %s body=%s -> %s" % (method, path, raw, text[:100]))
        self.assertEqual(leaks, [], "\n".join(leaks))

    def test_the_endpoints_in_the_report_answer_400_with_a_sentence(self):
        for path in ("/api/route", "/api/scan", "/api/remove_units",
                     "/api/usb_extenders/discover_ip", "/api/clear_units"):
            for raw in ("[]", "null", "5", '"x"', "{not json"):
                with self.subTest(path=path, body=raw):
                    reply = self.send("POST", path, raw)
                    self.assertEqual(reply.status_code, 400, reply.get_data(as_text=True)[:120])
                    body = reply.get_json()
                    self.assertFalse(body["ok"])
                    self.assertIn("JSON", body["error"])
                    self.assertEqual(body.get("reason"), "bad_request_body")

    def test_a_wrong_typed_value_in_a_valid_object_is_a_400(self):
        for body in ({"targets": 5}, {"targets": ["192.0.2.1"]}, {"targets": {"a": 1}}):
            with self.subTest(body=body):
                reply = self.client.post("/api/scan", json=body)
                self.assertEqual(reply.status_code, 400, reply.get_data(as_text=True)[:120])
                self.assertIsNone(PYTHON_TEXT.search(reply.get_data(as_text=True)))

    def test_an_unparseable_body_is_refused_on_a_plain_endpoint_too(self):
        reply = self.send("POST", "/api/ping", "{not json")
        self.assertEqual(reply.status_code, 400)


def _fingerprint(test):
    """Everything a mutating request could change: files on disk and the live stores."""
    import hashlib
    seen = {}
    roots = {Path(test.folder.name)}
    for name in ("DATA_DIR", "CWD"):
        value = getattr(srv, name, None)
        if value:
            roots.add(Path(value))
    for root in roots:
        if not root.exists():
            continue
        for path in sorted(root.rglob("*")):
            if path.is_file() and path.suffix not in (".log", ".lock", ".tmp")                     and "logs" not in path.parts and "__pycache__" not in path.parts:
                try:
                    seen[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
                except OSError:
                    pass
    store = srv._user_store
    live = {
        "accounts": json.dumps({k: str(v) for k, v in store._accounts.items()}, sort_keys=True),
        "sessions": sorted(store._sessions),
        "groups": json.dumps(srv._display_groups.all(), sort_keys=True, default=str),
        "listener": srv._remote_listener.running,
        "cache": json.dumps(srv._load_cache() or [], sort_keys=True, default=str),
        "audit": len(store._audit),
    }
    return seen, live


class MalformedBodyNeverActsTests(ServerTestBase):
    """A body that is not the object an endpoint needs changes nothing, on any route.

    `[]` sent to the Remote Access endpoint used to mean "off". This holds every
    mutating route to the same standard, driven by the route table: after a
    non-object body, or an object whose values are the wrong type, the files and
    stores are exactly as they were.
    """
    maxDiff = None
    NON_OBJECTS = ("[]", "null", "5", '"x"', "{oops", "")
    # Routes that legitimately change something when sent garbage, and why.
    EXEMPT = {
        "/api/auth/login": "a refused sign-in is recorded in the audit trail and counted by the throttle",
        "/api/export_csv": "regenerates a derived export file; it carries no configuration",
    }
    # An object body is a well-formed request even when its keys are unknown, so these
    # act on it by design: the page's own request to each is `{}`.
    OBJECT_ACTS = ("/api/clear_units", "/api/clear", "/api/usb_extenders/clear",
                   "/api/ui_preferences/reset")

    def setUp(self):
        super().setUp()
        self.client = srv.app.test_client()
        srv._save_cache([dict(u) for u in DestructiveEndpointsNeedARequestTests.__dict__.get("UNITS", [])
                         ] or [{"ip": "192.0.2.51", "mac": "b8:98:b0:00:00:51", "hostname": "unit-a",
                                "model": "hw-omni-e4521", "role": "encoder", "type": "Encoder"}])
        for owner, name in (("os", "startfile"), ("subprocess", "Popen"), ("subprocess", "run")):
            module = __import__(owner)
            if hasattr(module, name):
                patcher = mock.patch.object(module, name, side_effect=AssertionError(
                    "a malformed request reached %s.%s" % (owner, name)))
                patcher.start()
                self.addCleanup(patcher.stop)

    def test_no_route_acts_on_a_body_that_is_not_an_object(self):
        changed = []
        for path, method in _routes():
            if path == "/api/open_firmware_folder" or path in self.EXEMPT:
                continue
            for raw in self.NON_OBJECTS:
                before = _fingerprint(self)
                reply = self.client.open(path, method=method, data=raw,
                                         content_type="application/json")
                after = _fingerprint(self)
                if before != after:
                    what = sorted(k for k in before[1] if before[1][k] != after[1][k])
                    what += sorted(set(before[0]) ^ set(after[0])
                                   | {k for k in before[0] if k in after[0] and before[0][k] != after[0][k]})
                    changed.append("%s %s body=%r -> %s changed %s" % (
                        method, path, raw, reply.status_code, [Path(w).name for w in what][:4]))
        self.assertEqual(changed, [], chr(10).join(changed))

    def test_no_route_acts_on_values_of_the_wrong_type(self):
        changed = []
        for path, method in _routes():
            if path == "/api/open_firmware_folder" or path in self.EXEMPT or path in self.OBJECT_ACTS:
                continue
            for raw in WRONG_TYPES:
                before = _fingerprint(self)
                reply = self.client.open(path, method=method, data=raw,
                                         content_type="application/json")
                after = _fingerprint(self)
                if before != after:
                    changed.append("%s %s -> %s %s" % (method, path, reply.status_code, raw[:50]))
        self.assertEqual(changed, [], chr(10).join(changed))


class StructureTests(unittest.TestCase):

    def test_no_handler_reads_its_body_inside_a_broad_try(self):
        """A try that catches Exception would absorb the 400 and answer something else."""
        import ast
        tree = ast.parse((ROOT / "OmniMatrix_upgrade_server_v7_6y.py").read_text(encoding="utf-8"))

        def broad(handler):
            kinds = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
            return handler.type is None or any(
                getattr(k, "id", getattr(k, "attr", "")) in ("Exception", "BaseException")
                for k in kinds)

        offenders = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Try) and any(broad(h) for h in node.handlers):
                for child in node.body:
                    for sub in ast.walk(child):
                        if (isinstance(sub, ast.Call) and getattr(sub.func, "id", "") == "_json_dict"):
                            offenders.append(sub.lineno)
        self.assertEqual(offenders, [], "read the body before the try: lines %s" % offenders)

    def test_no_handler_still_calls_the_unchecked_parser(self):
        source = (ROOT / "OmniMatrix_upgrade_server_v7_6y.py").read_text(encoding="utf-8")
        self.assertNotIn("request.get_json(silent=True) or {}", source)


class DestructiveEndpointsNeedARequestTests(ClearTestBase):
    """Clear Units, Clear and the USB inventory clear act only on a body that says something."""

    def cached(self):
        return sorted(u.get("ip") for u in (self.srv._load_cache() or []))

    def full(self):
        return sorted(u["ip"] for u in UNITS)

    def test_refused_bodies_clear_nothing(self):
        for raw in ("", "null", "[]", "5", '"x"', "{oops", "   "):
            with self.subTest(body=raw):
                reply = self.client.post("/api/clear_units", data=raw,
                                         content_type="application/json")
                self.assertEqual(reply.status_code, 400)
                self.assertEqual(self.cached(), self.full())

    def test_no_body_and_no_content_type_clear_nothing(self):
        self.assertEqual(self.client.post("/api/clear_units").status_code, 400)
        self.assertEqual(self.cached(), self.full())

    def test_a_form_body_clears_nothing(self):
        reply = self.client.post("/api/clear_units", data={"x": "1"})
        self.assertEqual(reply.status_code, 400)
        self.assertEqual(self.cached(), self.full())

    def test_the_pages_own_body_still_clears(self):
        # ui/index.html sends exactly '{}' with Content-Type: application/json.
        reply = self.client.post("/api/clear_units", data="{}",
                                 content_type="application/json")
        self.assertEqual(reply.status_code, 200, reply.get_data(as_text=True)[:120])
        self.assertEqual(self.cached(), [])

    def test_the_page_really_sends_an_object(self):
        source = (ROOT / "ui" / "index.html").read_text(encoding="utf-8")
        start = source.index("fetch('/api/clear_units'")
        call = source[start:start + 200]
        self.assertIn("body: '{}'", call)
        self.assertIn("application/json", call)

    def test_clear_and_the_usb_inventory_clear_need_a_body_as_well(self):
        for path in ("/api/clear", "/api/usb_extenders/clear"):
            with self.subTest(path=path):
                self.assertEqual(self.client.post(path).status_code, 400)
                self.assertEqual(self.client.post(path, data="null",
                                                  content_type="application/json").status_code, 400)
                self.assertEqual(self.client.post(path, json={}).status_code, 200)

    def test_remove_units_refuses_a_malformed_body_and_removes_nothing(self):
        for raw in ("null", "[]", "5", "{oops"):
            reply = self.client.post("/api/remove_units", data=raw,
                                     content_type="application/json")
            self.assertEqual(reply.status_code, 400)
        self.assertEqual(self.cached(), self.full())


if __name__ == "__main__":
    unittest.main()
