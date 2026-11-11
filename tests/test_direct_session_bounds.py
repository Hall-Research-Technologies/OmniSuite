"""Direct Access sessions never expire, so something else has to bound them.

Opening a link mints a session, link sessions are deliberately never timed out (a wall
panel is idle by definition), and nothing removed one short of the account changing. So a
tablet reloading every minute, or anyone holding the URL and repeating the request, grew
the session table -- and wrote an audit entry each time -- without limit.

Two bounds, neither of which changes the "never expires" promise:

  * a browser that already holds a live link session for the same account is the same
    panel reloading, and is given the session it has rather than another;
  * one account's link may hold at most MAX_DIRECT_SESSIONS sessions; past that the one
    that has been quiet longest is dropped.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("OMNI_SMOKE", "1")

import omni_remote                                        # noqa: E402
import omni_users                                         # noqa: E402

#: Written out rather than read from the constant under test.
LIMIT = 256


class StoreBound(unittest.TestCase):

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="omni-dsb-"))
        self.store = omni_users.UserStore(self.dir / "u.json", self.dir / "a.json")
        self.store.create("principal", "principal-pass-1", omni_users.ROLE_ADMIN)
        self.store.create("teacher", "teacher-password-1", omni_users.ROLE_USER)
        self.store.create("janitor", "janitor-password-1", omni_users.ROLE_USER)
        self.teacher = self.store.find_by_username("teacher")
        self.janitor = self.store.find_by_username("janitor")

    def direct(self, account):
        return [s for s in self.store._sessions.values()
                if s.user_id == account.id and s.from_direct_link]

    def test_the_table_stops_growing_at_the_limit(self):
        for _ in range(LIMIT * 2):
            self.store.open_session(self.teacher.id, omni_users.BY_DIRECT_LINK)
        self.assertEqual(len(self.direct(self.teacher)), LIMIT)

    def test_the_session_dropped_is_the_one_quiet_longest(self):
        first = self.store.open_session(self.teacher.id, omni_users.BY_DIRECT_LINK)
        first.last_seen -= 1000
        for _ in range(LIMIT - 1):
            self.store.open_session(self.teacher.id, omni_users.BY_DIRECT_LINK)
        self.assertIn(first.token, self.store._sessions, "the limit was not yet reached")
        newest = self.store.open_session(self.teacher.id, omni_users.BY_DIRECT_LINK)
        self.assertNotIn(first.token, self.store._sessions)
        self.assertIn(newest.token, self.store._sessions)

    def test_one_accounts_flood_does_not_cost_another_its_sessions(self):
        keep = self.store.open_session(self.janitor.id, omni_users.BY_DIRECT_LINK)
        for _ in range(LIMIT * 2):
            self.store.open_session(self.teacher.id, omni_users.BY_DIRECT_LINK)
        self.assertIn(keep.token, self.store._sessions)

    def test_password_sessions_are_not_counted_or_dropped(self):
        # Opening link sessions never touches a password session. (Password sessions
        # have a ceiling of their own now, so this stays under it: what is asserted
        # is that the link ceiling does not reach them.)
        typed = [self.store.open_session(self.teacher.id)
                 for _ in range(omni_users.MAX_PASSWORD_SESSIONS - 1)]
        for _ in range(LIMIT * 2):
            self.store.open_session(self.teacher.id, omni_users.BY_DIRECT_LINK)
        self.assertTrue(all(s.token in self.store._sessions for s in typed))

    def test_a_session_still_never_expires_by_time(self):
        session = self.store.open_session(self.teacher.id, omni_users.BY_DIRECT_LINK)
        real = omni_users._now
        with mock.patch.object(omni_users, "_now", lambda: real() + 400 * 86400):
            account, _ = self.store.resolve_session(session.token)
        self.assertIsNotNone(account)


class EndpointReuse(unittest.TestCase):

    def setUp(self):
        import OmniMatrix_upgrade_server_v7_6y as srv
        self.srv = srv
        self.dir = Path(tempfile.mkdtemp(prefix="omni-dsb-ep-"))
        store = srv._user_store
        saved = (store._path, store._audit_path, dict(store._accounts),
                 dict(store._sessions), list(store._audit))

        def restore():
            store._path, store._audit_path = saved[0], saved[1]
            store._accounts.clear()
            store._accounts.update(saved[2])
            store._sessions.clear()
            store._sessions.update(saved[3])
            store._audit[:] = saved[4]

        self.addCleanup(restore)
        store._path = self.dir / "users.json"
        store._audit_path = self.dir / "audit.json"
        store._accounts.clear()
        store._sessions.clear()
        store._audit.clear()
        store.create("principal", "principal-pass-1", omni_users.ROLE_ADMIN)
        store.create("teacher", "teacher-password-1", omni_users.ROLE_USER,
                     omni_users.Permissions(features=("av",)))
        store.create("janitor", "janitor-password-1", omni_users.ROLE_USER,
                     omni_users.Permissions(features=("av",)))
        self.store = store
        admin = store.find_by_username("principal")
        self.token = store.enable_direct_access(
            store.find_by_username("teacher").id, actor=admin)["token"]
        self.other = store.enable_direct_access(
            store.find_by_username("janitor").id, actor=admin)["token"]
        patcher = mock.patch.object(type(srv._remote_listener), "running",
                                    new_callable=mock.PropertyMock, return_value=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def open(self, client, token):
        return client.get("/access/" + token,
                          environ_base={omni_remote.LISTENER_KEY: omni_remote.REMOTE})

    def live(self):
        return [s for s in self.store._sessions.values() if s.from_direct_link]

    def used_events(self):
        return [e for e in self.store._audit
                if (e.get("event") or e.get("action")) == "direct_access.used"
                and (e.get("detail") or {}).get("result") == "accepted"]

    def test_a_panel_that_reloads_keeps_one_session_and_writes_one_entry(self):
        client = self.srv.app.test_client()
        for _ in range(25):
            self.assertEqual(self.open(client, self.token).status_code, 200)
        self.assertEqual(len(self.live()), 1)
        self.assertEqual(len(self.used_events()), 1)

    def test_a_second_device_gets_its_own_session(self):
        a, b = self.srv.app.test_client(), self.srv.app.test_client()
        self.open(a, self.token)
        self.open(b, self.token)
        self.assertEqual(len(self.live()), 2)

    def test_a_browser_holding_another_accounts_session_gets_a_new_one(self):
        client = self.srv.app.test_client()
        self.open(client, self.token)
        self.open(client, self.other)
        self.assertEqual(len(self.live()), 2)
        owners = {s.user_id for s in self.live()}
        self.assertEqual(len(owners), 2)

    def test_a_revoked_session_is_replaced_not_reused(self):
        client = self.srv.app.test_client()
        self.open(client, self.token)
        self.store._sessions.clear()
        self.assertEqual(self.open(client, self.token).status_code, 200)
        self.assertEqual(len(self.live()), 1)

    def test_a_password_session_in_the_cookie_is_not_adopted_as_a_link_session(self):
        teacher = self.store.find_by_username("teacher")
        typed = self.store.open_session(teacher.id)
        client = self.srv.app.test_client()
        client.set_cookie("omnisuite_session", typed.token, domain="localhost")
        self.open(client, self.token)
        self.assertEqual(len(self.live()), 1, "the link must open a link session")
        self.assertIn(typed.token, self.store._sessions)


if __name__ == "__main__":
    unittest.main()
