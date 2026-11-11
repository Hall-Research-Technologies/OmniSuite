"""The session table is bounded, whoever fills it.

Sessions live in memory. Link sessions already had a ceiling per account; password
sessions did not, and expired ones were only swept when somebody asked how many
there were. Signing in repeatedly with a valid password -- a script, a wall panel
that re-authenticates, a person with a long day -- grew the table for as long as it
went on.

    a ceiling per account, per kind, oldest out first
    one account's ceiling never costs another account a session
    a password session and a link session do not count against each other
    an expired session is dropped when the next one is opened
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import omni_users                                         # noqa: E402
from omni_users import Permissions, UserStore             # noqa: E402


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="omni-bounds-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(self.dir, ignore_errors=True))
        self.store = UserStore(self.dir / "users.json", self.dir / "audit.json")
        for name in ("alice", "bob"):
            self.store.create(name, name + "-password-1", omni_users.ROLE_USER,
                              Permissions(features=("av",)))
        self.alice = self.store.find_by_username("alice")
        self.bob = self.store.find_by_username("bob")


class PasswordSessionsAreBoundedTests(Base):
    def test_the_table_stops_growing_at_the_ceiling(self):
        for _ in range(omni_users.MAX_PASSWORD_SESSIONS * 3):
            self.store.open_session(self.alice.id)
        mine = [s for s in self.store._sessions.values() if s.user_id == self.alice.id]
        self.assertEqual(len(mine), omni_users.MAX_PASSWORD_SESSIONS)

    def test_the_oldest_is_the_one_that_goes(self):
        first = self.store.open_session(self.alice.id)
        first.last_seen -= 100
        others = [self.store.open_session(self.alice.id)
                  for _ in range(omni_users.MAX_PASSWORD_SESSIONS)]
        self.assertIsNone(self.store.resolve_session(first.token)[0],
                          "the newest sign-ins were refused instead of the oldest dropped")
        self.assertTrue(all(self.store.resolve_session(s.token)[0] for s in others))

    def test_one_account_filling_its_ceiling_costs_another_nothing(self):
        bob = self.store.open_session(self.bob.id)
        for _ in range(omni_users.MAX_PASSWORD_SESSIONS * 2):
            self.store.open_session(self.alice.id)
        self.assertIsNotNone(self.store.resolve_session(bob.token)[0])

    def test_a_link_session_and_a_password_session_do_not_count_against_each_other(self):
        link = self.store.open_session(self.alice.id, omni_users.BY_DIRECT_LINK)
        for _ in range(omni_users.MAX_PASSWORD_SESSIONS * 2):
            self.store.open_session(self.alice.id)
        self.assertIsNotNone(self.store.resolve_session(link.token)[0])

    def test_the_link_ceiling_is_unchanged(self):
        for _ in range(omni_users.MAX_DIRECT_SESSIONS + 20):
            self.store.open_session(self.alice.id, omni_users.BY_DIRECT_LINK)
        links = [s for s in self.store._sessions.values()
                 if s.user_id == self.alice.id and s.from_direct_link]
        self.assertEqual(len(links), omni_users.MAX_DIRECT_SESSIONS)

    def test_signing_in_normally_is_never_refused_for_the_ceiling(self):
        for _ in range(omni_users.MAX_PASSWORD_SESSIONS + 5):
            self.assertTrue(self.store.authenticate("alice", "alice-password-1")["ok"])


class ExpiredSessionsAreSweptWhenTheNextOneOpensTests(Base):
    def test_an_expired_session_is_dropped_by_the_next_sign_in(self):
        old = self.store.open_session(self.bob.id)
        old.last_seen -= omni_users.IDLE_TIMEOUT_SECONDS + 5
        self.store.open_session(self.alice.id)
        self.assertNotIn(old.token, self.store._sessions,
                         "an expired session stayed in memory until somebody asked")

    def test_a_live_session_is_not_swept(self):
        live = self.store.open_session(self.bob.id)
        self.store.open_session(self.alice.id)
        self.assertIn(live.token, self.store._sessions)

    def test_a_link_session_is_never_swept_for_age(self):
        link = self.store.open_session(self.bob.id, omni_users.BY_DIRECT_LINK)
        link.last_seen -= omni_users.IDLE_TIMEOUT_SECONDS * 10
        link.created_at -= omni_users.ABSOLUTE_TIMEOUT_SECONDS * 10
        self.store.open_session(self.alice.id)
        self.assertIn(link.token, self.store._sessions)


if __name__ == "__main__":
    unittest.main()
