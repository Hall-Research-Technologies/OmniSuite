"""Route timing diagnostics: off by default, silent, bounded, and never in the way.

The switch (OMNI_ROUTE_DIAG) is read from the environment the process started
with. With it off the route view is not wrapped at all, so the ordinary build
pays nothing; that is asserted on the real application, not on a stand-in.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("OMNI_SMOKE", "1")

import omni_routediag as diag                            # noqa: E402


class FakeApp:
    def __init__(self, view):
        self.view_functions = {"route": view}


def make_view(result=("ok", 200), error=None):
    def route():
        if error:
            raise error
        return result
    return route


class SwitchTests(unittest.TestCase):
    def test_off_unless_asked(self):
        for env in ({}, {"OMNI_ROUTE_DIAG": ""}, {"OMNI_ROUTE_DIAG": "0"},
                    {"OMNI_ROUTE_DIAG": "no"}, {"OMNI_ROUTE_DIAG": "off"},
                    {"OMNI_ROUTE_DIAG": "enable please"}):
            with self.subTest(env=env):
                self.assertFalse(diag.enabled(env))

    def test_on_for_the_documented_values(self):
        for value in ("1", "true", "TRUE", " yes ", "on"):
            with self.subTest(value=value):
                self.assertTrue(diag.enabled({"OMNI_ROUTE_DIAG": value}))

    def test_off_leaves_the_view_untouched(self):
        view = make_view()
        app = FakeApp(view)
        with tempfile.TemporaryDirectory() as d:
            self.assertFalse(diag.install(app, "route", d, environ={}))
            self.assertIs(app.view_functions["route"], view)
            self.assertEqual(os.listdir(d), [])

    def test_a_missing_endpoint_is_not_an_error(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertFalse(diag.install(FakeApp(make_view()), "nope", d,
                                          environ={"OMNI_ROUTE_DIAG": "1"}))

    def test_the_real_application_is_not_wrapped_by_default(self):
        self.assertNotIn("OMNI_ROUTE_DIAG", os.environ,
                         "the test environment must not enable diagnostics")
        import OmniMatrix_upgrade_server_v7_6y as srv
        view = srv.app.view_functions["api_route_matrix"]
        self.assertIsNone(getattr(view, "__wrapped__", None))

    def test_the_server_installs_it_after_the_route_is_defined(self):
        text = (ROOT / "OmniMatrix_upgrade_server_v7_6y.py").read_text(encoding="utf-8")
        code = "\n".join(l for l in text.split("\n") if not l.lstrip().startswith("#"))
        self.assertEqual(code.count('omni_routediag.install(app, "api_route_matrix"'), 1)
        self.assertLess(code.index("def api_route_matrix():"),
                        code.index('omni_routediag.install(app, "api_route_matrix"'))


class RecordingTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="omni-routediag-"))
        self.path = self.dir / diag.FILE_NAME

    def rows(self):
        return [json.loads(l) for l in self.path.read_text(encoding="utf-8").splitlines()]

    def test_one_line_per_call_with_the_documented_fields(self):
        view = diag.wrap(make_view(("ok", 200)), self.path)
        for _ in range(3):
            self.assertEqual(view(), ("ok", 200))
        rows = self.rows()
        self.assertEqual([r["n"] for r in rows], [1, 2, 3])
        for r in rows:
            self.assertEqual(set(r) - {"objects"}, {"at", "ms", "status", "threads", "n"})
            self.assertEqual(r["status"], 200)
            self.assertGreaterEqual(r["ms"], 0)
            self.assertGreaterEqual(r["threads"], 1)

    def test_the_status_is_read_from_a_response_object_too(self):
        class Resp:
            status_code = 409
        view = diag.wrap(make_view(Resp()), self.path)
        view()
        self.assertEqual(self.rows()[0]["status"], 409)

    def test_the_view_still_gets_its_arguments_and_its_name(self):
        def route(a, b=2):
            return (a + b, 200)
        route.__name__ = "api_route_matrix"
        view = diag.wrap(route, self.path)
        self.assertEqual(view(1, b=5), (6, 200))
        self.assertEqual(view.__name__, "api_route_matrix")
        self.assertIs(view.__wrapped__, route)

    def test_objects_are_counted_only_on_every_nth_call(self):
        view = diag.wrap(make_view(), self.path)
        for _ in range(diag.OBJECTS_EVERY * 2):
            view()
        rows = self.rows()
        with_objects = [r["n"] for r in rows if "objects" in r]
        self.assertEqual(with_objects, [diag.OBJECTS_EVERY, diag.OBJECTS_EVERY * 2])
        self.assertTrue(all(isinstance(r["objects"], int) and r["objects"] > 0
                            for r in rows if "objects" in r))

    def test_an_exception_in_the_view_is_recorded_and_still_raised(self):
        view = diag.wrap(make_view(error=RuntimeError("boom")), self.path)
        with self.assertRaises(RuntimeError):
            view()
        self.assertEqual(self.rows()[0]["status"], 500)

    def test_nothing_about_the_request_is_recorded(self):
        view = diag.wrap(make_view(), self.path)
        view()
        self.assertEqual(set(self.rows()[0]), {"at", "ms", "status", "threads", "n"})

    def test_the_file_is_bounded_by_moving_it_aside_once(self):
        view = diag.wrap(make_view(), self.path)
        view()
        with mock.patch.object(diag, "MAX_BYTES", 10):
            view()                       # file now exceeds the limit: moved aside
            view()
        aside = self.path.with_name(self.path.name + ".1")
        self.assertTrue(aside.exists())
        self.assertLessEqual(len(self.rows()), 2)
        with mock.patch.object(diag, "MAX_BYTES", 10):
            for _ in range(4):
                view()
        self.assertEqual(sorted(p.name for p in self.dir.iterdir()),
                         [self.path.name, aside.name])

    def test_a_failing_write_never_breaks_the_route_and_warns_once(self):
        bad = self.dir / "no-such-dir" / "x" / diag.FILE_NAME
        view = diag.wrap(make_view(("fine", 200)), bad)
        with self.assertLogs(diag.log, level=logging.WARNING) as logs:
            self.assertEqual(view(), ("fine", 200))
            self.assertEqual(view(), ("fine", 200))
            self.assertEqual(view(), ("fine", 200))
        self.assertEqual(len(logs.records), 1)
        self.assertNotIn(str(self.dir), logs.output[0], "the path is not logged")

    def test_install_wraps_when_on(self):
        view = make_view()
        app = FakeApp(view)
        self.assertTrue(diag.install(app, "route", self.dir,
                                     environ={"OMNI_ROUTE_DIAG": "1"}))
        self.assertIsNot(app.view_functions["route"], view)
        app.view_functions["route"]()
        self.assertEqual(len(self.rows()), 1)


if __name__ == "__main__":
    unittest.main()
