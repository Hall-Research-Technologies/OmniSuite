"""Sign-ins that straddle a change to the account they are signing in to.

Verifying a password takes real time (it is deliberately expensive), and it is done
outside the account lock so that one slow sign-in cannot stop every other request.
That leaves a window: the password is checked against the hash that was current when
the check started, and the session is opened afterwards. A change made inside that
window -- the password replaced because it leaked, the account disabled or deleted,
a restore swapping the table -- must win. A session must never be opened by a
credential that was already revoked when it was opened.

    password changed during the check      no session
    account disabled during the check      no session
    account deleted during the check       no session
    restore replaces the account           no session
    nothing changed                        a session (the control)

And a hammer: many threads signing in, signing out, changing passwords and deleting
accounts together, with the invariants that must hold at the end.

No network; the account store only.
"""
from __future__ import annotations

import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import omni_users                                         # noqa: E402
from omni_users import Permissions, UserStore             # noqa: E402
from tests.test_acceptance_lifecycle import LifecycleBase, REMOTE   # noqa: E402


class StraddleBase(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="omni-straddle-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(self.dir, ignore_errors=True))
        self.store = UserStore(self.dir / "users.json", self.dir / "audit.json")
        self.store.create("admin1", "admin-password-1", omni_users.ROLE_ADMIN)
        self.store.create("victim", "victim-password-1", omni_users.ROLE_USER,
                          Permissions(features=("av",)))
        self.admin = self.store.find_by_username("admin1")
        self.victim = self.store.find_by_username("victim")

    def sign_in_during(self, change, password="victim-password-1"):
        """Start a sign-in, hold it inside the password check, make `change`, let it finish."""
        inside, release = threading.Event(), threading.Event()
        real = omni_users.verify_password
        result = {}

        def slow(stored, candidate):
            ok = real(stored, candidate)          # the answer for the hash it was given
            inside.set()
            release.wait(10)
            return ok

        def run():
            with mock.patch.object(omni_users, "verify_password", slow):
                result["answer"] = self.store.authenticate("victim", password,
                                                           source="198.51.100.7")

        worker = threading.Thread(target=run)
        worker.start()
        self.assertTrue(inside.wait(10), "the sign-in never reached the password check")
        change()
        release.set()
        worker.join(10)
        self.assertFalse(worker.is_alive())
        return result["answer"]

    def holds_session(self, answer):
        if not answer.get("ok"):
            return False
        return self.store.resolve_session(answer["token"])[0] is not None


class ASignInThatStraddlesAChangeTests(StraddleBase):
    def test_control_nothing_changes_and_the_sign_in_works(self):
        answer = self.sign_in_during(lambda: None)
        self.assertTrue(answer["ok"])
        self.assertTrue(self.holds_session(answer))

    def test_a_password_replaced_during_the_check_wins(self):
        answer = self.sign_in_during(
            lambda: self.store.set_password(self.victim.id, "replacement-password-9"))
        self.assertFalse(self.holds_session(answer),
                         "a session was opened with a password that had just been replaced")
        self.assertFalse(answer.get("ok"))
        # ...and the new password is the one that works.
        self.assertTrue(self.store.authenticate("victim", "replacement-password-9")["ok"])
        self.assertFalse(self.store.authenticate("victim", "victim-password-1")["ok"])

    def test_an_account_disabled_during_the_check_wins(self):
        answer = self.sign_in_during(
            lambda: self.store.set_enabled(self.victim.id, False, actor=self.admin))
        self.assertFalse(self.holds_session(answer))
        self.assertFalse(answer.get("ok"),
                         "the sign-in reported success for an account that was switched off")

    def test_an_account_deleted_during_the_check_wins(self):
        answer = self.sign_in_during(
            lambda: self.store.delete(self.victim.id, actor=self.admin))
        self.assertFalse(self.holds_session(answer))
        self.assertEqual(self.store.active_session_count(), 0,
                         "a session was left behind for an account that no longer exists")

    def test_an_account_replaced_by_a_restore_during_the_check_wins(self):
        def restore_with_new_password():
            other = UserStore(self.dir / "other.json", self.dir / "other-audit.json")
            other.create("admin1", "admin-password-1", omni_users.ROLE_ADMIN)
            # The same account id, a different credential: what a restore carries.
            other._accounts[self.victim.id] = omni_users.Account.from_json(
                {**self.victim.to_json(),
                 "password_hash": omni_users.hash_password("restored-password-3")})
            other._persist()
            (self.dir / "users.json").write_bytes((self.dir / "other.json").read_bytes())
            self.store.reload_from_disk()

        answer = self.sign_in_during(restore_with_new_password)
        self.assertFalse(self.holds_session(answer))

    def test_a_wrong_password_is_still_just_wrong(self):
        answer = self.sign_in_during(lambda: None, password="not-the-password-1")
        self.assertFalse(answer["ok"])
        self.assertEqual(answer["error"], "invalid username or password")

    def test_the_refusal_says_nothing_about_why(self):
        """Same words as any other failed sign-in: no oracle for what was changed."""
        changed = self.sign_in_during(
            lambda: self.store.set_password(self.victim.id, "replacement-password-9"))
        wrong = self.store.authenticate("victim", "definitely-wrong-1")
        self.assertEqual(changed.get("error"), wrong.get("error"))


class TheSecondLineOfDefenceTests(StraddleBase):
    """The early `enabled` check runs after the slow password check, so a disable made
    during that check is already caught by it. What is left for the check inside the
    lock is the instant between the two -- which no ordinary test reaches, so this one
    lands the change there deliberately: after the early check, before the lock.
    """

    def sign_in_with_a_change_before_the_lock(self, change):
        store = self.store
        inner = store._lock
        armed = {"on": False}

        class HookLock:
            def __enter__(self_):
                if armed["on"]:
                    armed["on"] = False            # disarm first: the change takes the lock too
                    change()
                return inner.__enter__()

            def __exit__(self_, *exc):
                return inner.__exit__(*exc)

            def acquire(self_, *a, **k):
                return inner.acquire(*a, **k)

            def release(self_):
                return inner.release()

        store._lock = HookLock()
        self.addCleanup(setattr, store, "_lock", inner)
        real = omni_users.verify_password

        def arming(stored, candidate):
            ok = real(stored, candidate)
            armed["on"] = True                      # the next lock taken is the one that opens the session
            return ok

        with mock.patch.object(omni_users, "verify_password", arming):
            return store.authenticate("victim", "victim-password-1")

    def test_an_account_disabled_after_the_early_check_still_wins(self):
        answer = self.sign_in_with_a_change_before_the_lock(
            lambda: self.store.set_enabled(self.victim.id, False, actor=self.admin))
        self.assertFalse(answer.get("ok"),
                         "the sign-in reported success for an account switched off a moment before")
        self.assertFalse(self.holds_session(answer))

    def test_a_password_replaced_after_the_early_check_still_wins(self):
        answer = self.sign_in_with_a_change_before_the_lock(
            lambda: self.store.set_password(self.victim.id, "replacement-password-9"))
        self.assertFalse(answer.get("ok"))
        self.assertFalse(self.holds_session(answer))

    def test_control_with_no_change_the_sign_in_works(self):
        answer = self.sign_in_with_a_change_before_the_lock(lambda: None)
        self.assertTrue(answer.get("ok"))
        self.assertTrue(self.holds_session(answer))


class ALinkReplacedWhileItIsBeingOpenedTests(LifecycleBase):
    """The same window for a Direct Access link: resolved, then a session opened.

    A link regenerated or switched off between those two steps ran its revocation
    before the session existed, so the session it was meant to end was opened
    afterwards by the old link.
    """

    def interleaved(self, change):
        lecturer = self.teacher
        token = self.store.enable_direct_access(lecturer.id, actor=self.admin)["token"]
        real = self.store.open_session

        def open_then_change(*args, **kwargs):
            change(lecturer)                     # lands before the session is stored
            return real(*args, **kwargs)

        with mock.patch.object(self.store, "open_session", open_then_change):
            client, reply = self.link_login(token)
        return token, client, reply

    def test_control_an_undisturbed_link_opens(self):
        _t, client, reply = self.interleaved(lambda account: None)
        self.assertEqual(reply.status_code, 200)
        self.assertEqual(client.get("/api/user/state", environ_base=REMOTE).status_code, 200)

    def test_a_link_regenerated_in_the_window_is_refused_and_leaves_no_session(self):
        token, client, reply = self.interleaved(
            lambda a: self.store.regenerate_direct_access(a.id, actor=self.admin))
        self.assertEqual(reply.status_code, 404)
        self.assertEqual(client.get("/api/user/state", environ_base=REMOTE).status_code, 401)
        self.assertEqual(self.store.active_session_count(), 0,
                         "the old link's session outlived the link that replaced it")

    def test_a_link_switched_off_in_the_window_is_refused_and_leaves_no_session(self):
        _t, client, reply = self.interleaved(
            lambda a: self.store.disable_direct_access(a.id, actor=self.admin))
        self.assertEqual(reply.status_code, 404)
        self.assertEqual(self.store.active_session_count(), 0)

    def test_an_account_disabled_in_the_window_is_refused(self):
        _t, client, reply = self.interleaved(
            lambda a: self.store.set_enabled(a.id, False, actor=self.admin))
        self.assertEqual(reply.status_code, 404)
        self.assertEqual(self.store.active_session_count(), 0)

    def test_the_new_link_still_opens_afterwards(self):
        lecturer = self.teacher
        self.store.enable_direct_access(lecturer.id, actor=self.admin)
        self.interleaved(lambda a: self.store.regenerate_direct_access(a.id, actor=self.admin))
        fresh = self.store.direct_access_token(lecturer.id)
        _client, reply = self.link_login(fresh)
        self.assertEqual(reply.status_code, 200)


class AHammerTests(unittest.TestCase):
    """Threads signing in, out, and changing the accounts they sign in to."""

    def test_nothing_survives_that_should_not(self):
        directory = Path(tempfile.mkdtemp(prefix="omni-hammer-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(directory, ignore_errors=True))
        store = UserStore(directory / "users.json", directory / "audit.json")
        admin = store.create("root", "root-password-1", omni_users.ROLE_ADMIN)
        store.create("shared", "shared-password-1", omni_users.ROLE_USER,
                     Permissions(features=("av",)))
        errors, stop = [], threading.Event()
        deleted = []

        def signer(index):
            try:
                while not stop.is_set():
                    answer = store.authenticate("shared", "shared-password-1",
                                                source="198.51.100.%d" % (index + 10))
                    if answer.get("ok"):
                        store.resolve_session(answer["token"])
                        store.revoke(answer["token"])
            except Exception as exc:                      # noqa: BLE001
                errors.append(repr(exc))

        def churn():
            try:
                for n in range(6):
                    account = store.find_by_username("shared")
                    store.set_password(account.id, "shared-password-%d" % (n + 2))
                    store.set_enabled(account.id, n % 2 == 0, actor=None)
                    store.enable_direct_access(account.id, actor=None)
                    store.regenerate_direct_access(account.id, actor=None)
                    store.set_enabled(account.id, True, actor=None)
                    store.set_password(account.id, "shared-password-1")
                victim = store.find_by_username("shared")
                store.delete(victim.id, actor=None)
                deleted.append(victim.id)
            except Exception as exc:                      # noqa: BLE001
                errors.append(repr(exc))

        signers = [threading.Thread(target=signer, args=(i,)) for i in range(6)]
        for thread in signers:
            thread.start()
        churner = threading.Thread(target=churn)
        churner.start()
        churner.join(120)
        stop.set()
        for thread in signers:
            thread.join(30)
        self.assertEqual(errors, [])
        self.assertFalse(churner.is_alive())
        self.assertTrue(deleted, "the churn never finished")
        survivors = [s for s in list(store._sessions.values())
                     if s.user_id == deleted[0]]
        self.assertEqual(survivors, [],
                         "a session outlived the account it was opened for")
        for session in list(store._sessions.values()):
            self.assertIsNone(store.resolve_session(session.token)[0])
        self.assertEqual(store.active_session_count(), 0)


if __name__ == "__main__":
    unittest.main()
