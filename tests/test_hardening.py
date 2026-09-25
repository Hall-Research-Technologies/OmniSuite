"""Deployment hardening: transport security, and two operators at once.

The concurrency policy this branch takes is the one that suits AV control:
**the last successful authorised operation wins.** Nobody reserves a room and
nobody is locked out because a browser was closed. What that does not permit is
two requests interleaving inside one device transaction, because the
verification and rollback the engines already perform only hold if one
transaction at a time is running against a given piece of equipment.

The TLS tests are about the property that matters most: a configuration that
asks for HTTPS and cannot have it must not quietly serve plaintext.
"""
from __future__ import annotations

import json
import os
import re
import ssl
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("OMNI_SMOKE", "1")

import omni_locks                                   # noqa: E402
import omni_remote                                  # noqa: E402
import omni_tls                                     # noqa: E402
from omni_locks import ResourceBusy, ResourceLocks  # noqa: E402


# =====================================================================  TLS
class CertificateTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="omni-tls-"))
        self.cert = self.dir / "remote_cert.pem"
        self.key = self.dir / "remote_key.pem"

    def generate(self, address="127.0.0.1"):
        return omni_tls.generate_self_signed(address, self.cert, self.key)

    @unittest.skipUnless(omni_tls.generation_available(),
                         "certificate generation needs cryptography")
    def test_a_generated_certificate_can_actually_serve_tls(self):
        """The test that matters: it is not enough that a file appears."""
        self.generate()
        context = omni_tls.load_context(self.cert, self.key)
        self.assertIsInstance(context, ssl.SSLContext)
        self.assertEqual(context.minimum_version, ssl.TLSVersion.TLSv1_2,
                         "TLS 1.0 and 1.1 must not be offered")

    @unittest.skipUnless(omni_tls.generation_available(), "needs cryptography")
    def test_the_description_never_contains_key_material(self):
        described = self.generate()
        blob = repr(described)
        for forbidden in ("PRIVATE KEY", "BEGIN RSA", "-----BEGIN"):
            self.assertNotIn(forbidden, blob,
                             "a private key must never leave this module")
        self.assertIn("fingerprint", described)
        self.assertTrue(described["self_signed"])

    @unittest.skipUnless(omni_tls.generation_available(), "needs cryptography")
    def test_the_key_file_is_not_world_readable_where_the_platform_allows(self):
        self.generate()
        mode = self.key.stat().st_mode & 0o777
        if os.name == "posix":
            self.assertEqual(mode, 0o600)
        else:
            # Windows does not honour POSIX bits; the per-user data directory
            # is the protection there. Asserted so the intent is recorded and
            # a future POSIX build is covered.
            self.assertTrue(True)

    def test_a_missing_certificate_is_refused_with_a_useful_sentence(self):
        with self.assertRaises(omni_tls.TlsError) as caught:
            omni_tls.load_context(self.dir / "absent.pem", self.dir / "absent.key")
        self.assertIn("not found", str(caught.exception))

    @unittest.skipUnless(omni_tls.generation_available(), "needs cryptography")
    def test_a_mismatched_key_is_refused_and_named_as_such(self):
        self.generate()
        other_key = self.dir / "other_key.pem"
        omni_tls.generate_self_signed("127.0.0.1", self.dir / "other.pem",
                                      other_key)
        with self.assertRaises(omni_tls.TlsError) as caught:
            omni_tls.load_context(self.cert, other_key)
        self.assertIn("does not match", str(caught.exception))

    def test_rubbish_in_a_certificate_file_is_refused(self):
        self.cert.write_text("not a certificate", encoding="utf-8")
        self.key.write_text("not a key", encoding="utf-8")
        with self.assertRaises(omni_tls.TlsError):
            omni_tls.load_context(self.cert, self.key)

    def test_describing_an_absent_certificate_says_so_rather_than_raising(self):
        described = omni_tls.describe(self.dir / "absent.pem")
        self.assertFalse(described["present"])

    def test_describing_a_corrupt_certificate_says_so_rather_than_raising(self):
        self.cert.write_text("junk", encoding="utf-8")
        described = omni_tls.describe(self.cert)
        self.assertTrue(described["present"])
        self.assertFalse(described["readable"])


class ListenerTransportTests(unittest.TestCase):
    """A listener says which scheme it is serving, and never overstates it."""

    def test_a_plaintext_listener_does_not_claim_to_be_secure(self):
        listener = omni_remote.RemoteListener(object())
        described = listener.describe()
        self.assertFalse(described["secure"])

    def test_the_advertised_url_carries_the_scheme_in_use(self):
        """An https:// address for an http listener would simply not connect."""
        import werkzeug.serving

        captured = {}

        class FakeServer:
            def serve_forever(self):
                pass

            def shutdown(self):
                pass

            def server_close(self):
                pass

        def fake_make_server(host, port, app, **kwargs):
            captured["ssl"] = kwargs.get("ssl_context")
            return FakeServer()

        original = werkzeug.serving.make_server
        werkzeug.serving.make_server = fake_make_server
        try:
            listener = omni_remote.RemoteListener(object())
            listener.start("10.48.55.10", 8080, ssl_context=None)
            self.assertTrue(listener.describe()["url"].startswith("http://"))
            self.assertFalse(listener.describe()["secure"])
            listener.stop()

            listener.start("10.48.55.10", 8080, ssl_context=object())
            self.assertTrue(listener.describe()["url"].startswith("https://"))
            self.assertTrue(listener.describe()["secure"])
            listener.stop()
        finally:
            werkzeug.serving.make_server = original

    def test_stopping_clears_the_scheme(self):
        listener = omni_remote.RemoteListener(object())
        listener.stop()
        self.assertIsNone(listener.describe()["scheme"])


# ===============================================================  resources
class ResourceLockTests(unittest.TestCase):
    def setUp(self):
        self.locks = ResourceLocks(timeout=2.0)

    def test_two_operations_on_one_display_do_not_interleave(self):
        """The guarantee the engines' rollback depends on."""
        order = []
        started = threading.Event()

        def first():
            with self.locks.hold(self.locks.decoder("aa")):
                order.append("first-in")
                started.set()
                time.sleep(0.3)
                order.append("first-out")

        def second():
            started.wait(timeout=5)
            with self.locks.hold(self.locks.decoder("aa")):
                order.append("second-in")
                order.append("second-out")

        threads = [threading.Thread(target=first), threading.Thread(target=second)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(order,
                         ["first-in", "first-out", "second-in", "second-out"])

    def test_different_displays_run_concurrently(self):
        """Room 101 must never wait on Room 102."""
        overlapped = threading.Event()
        inside = threading.Barrier(2, timeout=5)

        def worker(identity):
            with self.locks.hold(self.locks.decoder(identity)):
                try:
                    inside.wait()
                    overlapped.set()
                except threading.BrokenBarrierError:
                    pass

        threads = [threading.Thread(target=worker, args=("aa",)),
                   threading.Thread(target=worker, args=("bb",))]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertTrue(overlapped.is_set(),
                        "unrelated equipment must not serialise")

    def test_a_pair_is_taken_in_a_deterministic_order(self):
        """USB touches two endpoints; opposite orders would deadlock.

        Both threads name the same pair the opposite way round and must still
        complete. Without sorting this is the textbook deadlock.
        """
        done = []

        def forward():
            for _ in range(30):
                with self.locks.hold(*self.locks.usb_pair("aa", "bb")):
                    time.sleep(0.001)
            done.append("forward")

        def backward():
            for _ in range(30):
                with self.locks.hold(*self.locks.usb_pair("bb", "aa")):
                    time.sleep(0.001)
            done.append("backward")

        threads = [threading.Thread(target=forward),
                   threading.Thread(target=backward)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=20)
        self.assertEqual(sorted(done), ["backward", "forward"],
                         "a deadlock; the pair was not ordered")

    def test_a_lock_is_released_when_the_operation_raises(self):
        """A failed operation must not take the room down with it."""
        with self.assertRaises(RuntimeError):
            with self.locks.hold(self.locks.decoder("aa")):
                raise RuntimeError("the device did not answer")
        # Immediately usable again.
        with self.locks.hold(self.locks.decoder("aa")):
            pass
        self.assertEqual(self.locks.busy(), [])

    def test_waiting_is_bounded_and_reports_which_resource(self):
        holding = threading.Event()
        release = threading.Event()

        def holder():
            with self.locks.hold(self.locks.decoder("aa")):
                holding.set()
                release.wait(timeout=10)

        thread = threading.Thread(target=holder)
        thread.start()
        holding.wait(timeout=5)
        try:
            with self.assertRaises(ResourceBusy) as caught:
                with self.locks.hold(self.locks.decoder("aa"), timeout=0.2):
                    pass
            self.assertIn("decoder:aa", str(caught.exception))
        finally:
            release.set()
            thread.join(timeout=5)

    def test_a_timeout_leaves_nothing_half_held(self):
        """Acquiring a pair must not keep the first when the second times out."""
        holding = threading.Event()
        release = threading.Event()

        def holder():
            with self.locks.hold("usb:bb"):
                holding.set()
                release.wait(timeout=10)

        thread = threading.Thread(target=holder)
        thread.start()
        holding.wait(timeout=5)
        try:
            with self.assertRaises(ResourceBusy):
                with self.locks.hold("usb:aa", "usb:bb", timeout=0.2):
                    pass
            # aa must be free: it was acquired first and has to be given back.
            with self.locks.hold("usb:aa", timeout=1.0):
                pass
        finally:
            release.set()
            thread.join(timeout=5)

    def test_identity_case_does_not_create_a_second_lock(self):
        """The same display named two ways must take the same lock."""
        self.assertEqual(self.locks.decoder("AA:BB"), self.locks.decoder("aa:bb"))

    def test_holding_nothing_is_allowed(self):
        with self.locks.hold():
            pass


class ConcurrentOperationStressTests(unittest.TestCase):
    """Many operators, one room, and nothing corrupted."""

    def test_the_same_display_is_never_held_by_two_at_once(self):
        locks = ResourceLocks(timeout=30)
        concurrent = {"now": 0, "peak": 0}
        guard = threading.Lock()

        def operate():
            for _ in range(25):
                with locks.hold(locks.decoder("front")):
                    with guard:
                        concurrent["now"] += 1
                        concurrent["peak"] = max(concurrent["peak"],
                                                 concurrent["now"])
                    time.sleep(0.001)
                    with guard:
                        concurrent["now"] -= 1

        threads = [threading.Thread(target=operate) for _ in range(12)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)
        self.assertEqual(concurrent["peak"], 1,
                         "two operations were inside one display's transaction")

    def test_unrelated_rooms_stay_parallel_under_load(self):
        locks = ResourceLocks(timeout=30)
        started = time.time()
        threads = []

        def operate(identity):
            for _ in range(20):
                with locks.hold(locks.decoder(identity)):
                    time.sleep(0.002)

        for index in range(10):
            threads.append(threading.Thread(target=operate, args=("room%d" % index,)))
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)
        elapsed = time.time() - started
        # Ten rooms serialised would be ten times one room's work. Generous
        # bound: this asserts they are not serialised, not a specific speed.
        self.assertLess(elapsed, 2.0,
                        "unrelated rooms appear to be serialising")


class LockStatisticsTests(unittest.TestCase):
    """A counter that cannot count is worse than no counter.

    `waited` was declared and never incremented, so it read zero after a
    measurably contended acquisition -- which asserts there was no contention
    rather than saying nothing. Somebody reading that would have drawn the
    wrong conclusion about whether the locks were ever in the way.
    """

    def setUp(self):
        self.locks = omni_locks.ResourceLocks()
        self.resource = self.locks.decoder("0a:1b:2c:00:00:11")

    def test_an_uncontended_acquisition_does_not_count_as_a_wait(self):
        with self.locks.hold(self.resource):
            pass
        self.assertEqual(self.locks.stats()["waited"], 0)
        self.assertEqual(self.locks.stats()["acquired"], 1)

    def test_a_contended_acquisition_counts_as_a_wait(self):
        holding = threading.Event()
        release = threading.Event()

        def squat():
            with self.locks.hold(self.resource):
                holding.set()
                release.wait(10)

        squatter = threading.Thread(target=squat)
        squatter.start()
        self.addCleanup(squatter.join, 10)
        self.assertTrue(holding.wait(5))

        outcome = []

        def contend():
            with self.locks.hold(self.resource):
                outcome.append(True)

        other = threading.Thread(target=contend)
        other.start()
        time.sleep(0.2)
        release.set()
        other.join(10)
        squatter.join(10)
        self.assertEqual(outcome, [True])
        self.assertGreaterEqual(self.locks.stats()["waited"], 1)

    def test_a_refusal_counts_as_a_timeout(self):
        holding = threading.Event()
        release = threading.Event()

        def squat():
            with self.locks.hold(self.resource):
                holding.set()
                release.wait(10)

        squatter = threading.Thread(target=squat)
        squatter.start()
        self.addCleanup(squatter.join, 10)
        self.assertTrue(holding.wait(5))
        with self.assertRaises(omni_locks.ResourceBusy):
            with self.locks.hold(self.resource, timeout=0.3):
                pass
        release.set()
        squatter.join(10)
        self.assertEqual(self.locks.stats()["timed_out"], 1)
        self.assertEqual(self.locks.busy(), [])

    def test_every_declared_statistic_can_actually_be_reached(self):
        """The defect in one sentence: a key nothing increments."""
        source = (ROOT / "omni_locks.py").read_text(encoding="utf-8")
        declared = re.search(r'self\._stats = \{([^}]*)\}', source).group(1)
        for name in re.findall(r'"(\w+)":', declared):
            with self.subTest(statistic=name):
                self.assertIn('_stats["%s"] +=' % name, source,
                              "%s is reported and never counted" % name)


class MutationHarnessSurvivesAKillTests(unittest.TestCase):
    """`finally` covers an exception. It does not cover being killed.

    An interrupted run left a deliberately defective source in the working
    tree, one `git add -A` away from committing an inverted security
    predicate. The harness writes down what it is about to change, before it
    changes it, and repairs that on its next start.
    """

    def test_the_receipt_is_written_before_the_edit(self):
        source = (ROOT / "tools" / "mutate_security.py").read_text(encoding="utf-8")
        code = "\n".join(line.split("#", 1)[0] for line in source.splitlines())
        receipt = code.index("IN_FLIGHT.write_text")
        edit = code.index("handle.write(original.replace(anchor, replacement")
        self.assertLess(receipt, edit,
                        "a kill between the two must leave a record of a "
                        "change that was never made, not the reverse")

    def test_a_run_repairs_what_an_interrupted_one_left(self):
        harness = _load_harness()
        target = ROOT / "omni_access.py"
        with open(target, "r", encoding="utf-8", newline="") as handle:
            original = handle.read()
        self.addCleanup(lambda: open(target, "w", encoding="utf-8",
                                     newline="").write(original))
        receipt = ROOT / ".mutation-in-flight"
        self.addCleanup(lambda: receipt.unlink(missing_ok=True))

        receipt.write_text(json.dumps(
            {"id": "test", "file": "omni_access.py", "original": original}),
            encoding="utf-8")
        with open(target, "w", encoding="utf-8", newline="") as handle:
            handle.write(original.replace("Access.LOCAL_ONLY", "Access.PUBLIC", 1))

        harness.repair_interrupted_run()

        with open(target, "r", encoding="utf-8", newline="") as handle:
            self.assertEqual(handle.read(), original)
        self.assertFalse(receipt.exists())

    def test_repair_is_a_no_op_when_nothing_was_interrupted(self):
        harness = _load_harness()
        receipt = ROOT / ".mutation-in-flight"
        self.assertFalse(receipt.exists())
        harness.repair_interrupted_run()      # must not raise

    def test_the_receipt_is_never_committed(self):
        ignored = (ROOT / ".gitignore").read_text(encoding="utf-8")
        self.assertIn(".mutation-in-flight", ignored)


def _load_harness():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "_mutate_security", ROOT / "tools" / "mutate_security.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


if __name__ == "__main__":
    unittest.main()
