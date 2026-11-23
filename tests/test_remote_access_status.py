"""`/api/remote_access/status`: the cheap answer the navigation reconciles with.

The full `/api/remote_access` enumerates network adapters, which starts a PowerShell child that
measured 9-11 s on a Windows host. A tab bar that asked for that every time a window came back to
the front would be a self-inflicted stall, so the navigation has its own read: is Remote Access
on, and nothing else.
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("OMNI_SMOKE", "1")

import omni_access                                   # noqa: E402
import omni_adapters                                 # noqa: E402
import OmniMatrix_upgrade_server_v7_6y as srv        # noqa: E402


class StatusEndpointTests(unittest.TestCase):
    def setUp(self):
        self.client = srv.app.test_client()

    def test_it_is_classified_for_administrators(self):
        self.assertEqual(omni_access.ROUTES["/api/remote_access/status"],
                         (omni_access.Access.ADMIN, None))

    def test_it_answers_off_when_the_listener_is_off(self):
        reply = self.client.get("/api/remote_access/status")
        self.assertEqual(reply.status_code, 200)
        self.assertEqual(reply.get_json(), {"ok": True, "enabled": False})

    def test_it_follows_the_listener_and_nothing_else(self):
        listener = srv._remote_listener
        with mock.patch.object(listener, "describe", return_value={"enabled": True}):
            self.assertTrue(self.client.get("/api/remote_access/status").get_json()["enabled"])
        with mock.patch.object(listener, "describe", return_value={"enabled": False}):
            self.assertFalse(self.client.get("/api/remote_access/status").get_json()["enabled"])

    def test_it_never_enumerates_adapters(self):
        with mock.patch.object(omni_adapters, "enumerate_adapters",
                               side_effect=AssertionError("adapters were enumerated")) as spy, \
                mock.patch.object(omni_adapters, "invalidate_cache"):
            reply = self.client.get("/api/remote_access/status")
        self.assertEqual(reply.status_code, 200)
        spy.assert_not_called()

    def test_it_carries_nothing_but_the_flag(self):
        body = self.client.get("/api/remote_access/status").get_json()
        self.assertEqual(sorted(body), ["enabled", "ok"])


if __name__ == "__main__":
    unittest.main()


class PagesAreNeverRevalidatedTests(unittest.TestCase):
    """The stale-tab defect: a cached page with the old Remote Access answer stamped in it."""
    PAGES = ("/", "/matrix", "/matrix/configure", "/matrix/usb", "/matrix/multiview", "/admin/users")

    def setUp(self):
        self.client = srv.app.test_client()

    def test_every_page_is_no_store_and_carries_no_validator(self):
        for page in self.PAGES:
            with self.subTest(page=page):
                reply = self.client.get(page)
                self.assertEqual(reply.status_code, 200)
                self.assertIn("no-store", reply.headers.get("Cache-Control", ""))
                self.assertNotIn("ETag", reply.headers)
                self.assertNotIn("Last-Modified", reply.headers)

    def test_a_conditional_request_is_answered_with_a_fresh_page_not_304(self):
        for page in self.PAGES:
            with self.subTest(page=page):
                # Each validator on its own: when both are sent, a non-matching ETag wins and
                # the date is ignored, which would hide the date path.
                first = self.client.get(page)
                self.assertEqual(first.status_code, 200)
                reply = self.client.get(page, headers={
                    "If-Modified-Since": "Wed, 21 Oct 2037 07:28:00 GMT"})
                self.assertEqual(reply.status_code, 200,
                                 "a 304 lets the browser reuse a body with the old answer in it")
                self.assertIn(b"omni-remote-access", reply.data)

    def test_the_stamped_answer_follows_the_listener_on_every_page(self):
        listener = srv._remote_listener
        for enabled, flag in ((True, b'content="1"'), (False, b'content="0"')):
            with mock.patch.object(listener, "describe", return_value={"enabled": enabled}), \
                    mock.patch.object(type(listener), "running", new_callable=mock.PropertyMock,
                                      return_value=enabled):
                for page in self.PAGES:
                    with self.subTest(page=page, enabled=enabled):
                        self.assertIn(b'name="omni-remote-access" ' + flag,
                                      self.client.get(page).data)
