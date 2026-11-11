"""One OmniSuite per data directory.

The defect these exist for: nothing prevented a second copy. Every lock in the
server is in-process, so a second copy on another port loaded the same accounts,
cache and settings, started its own background work and rewrote the same files
from its own stale memory.

The lock is an operating-system lock, so these use real processes: a lock held
by the test's own process proves nothing about another one. No network is
touched; every directory is a fresh temporary one.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import omni_instance                                     # noqa: E402

HOLDER = (
    "import os, sys, omni_instance\n"
    "omni_instance.acquire(sys.argv[1])\n"
    "print('READY', os.getpid(), flush=True)\n"
    "sys.stdin.readline()\n"
)


def _env(**extra):
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    env["OMNI_SMOKE"] = "1"
    env.update(extra)
    return env


class Holder:
    """A separate process that owns a data directory until told to stop."""

    def __init__(self, directory):
        self.proc = subprocess.Popen(
            [sys.executable, "-c", HOLDER, str(directory)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=_env(), cwd=str(ROOT))
        line = self.proc.stdout.readline().decode().strip()
        self.pid = int(line.split()[1]) if line.startswith("READY ") else None
        if self.pid is None:
            self.proc.kill()                  # a live process never closes stderr
            err = self.proc.stderr.read().decode()
            self.stop()
            raise AssertionError(f"holder did not take the lock: {line!r} {err}")

    def stop(self):
        try:
            self.proc.stdin.close()
        except OSError:
            pass
        self.proc.kill()
        self.proc.wait(timeout=10)
        for stream in (self.proc.stdout, self.proc.stderr):
            stream.close()


class SingleInstanceTests(unittest.TestCase):

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="omni-instance-"))
        self.addCleanup(omni_instance.release, self.dir)

    def hold(self, directory=None):
        holder = Holder(directory or self.dir)
        self.addCleanup(holder.stop)
        return holder

    def test_a_second_copy_is_refused_while_the_first_runs(self):
        holder = self.hold()
        with self.assertRaises(omni_instance.AlreadyRunning) as raised:
            omni_instance.acquire(self.dir)
        self.assertEqual(raised.exception.pid, holder.pid,
                         "the message should name the copy that holds the folder")
        self.assertIn("already running", str(raised.exception))

    def test_the_refusal_says_what_to_do(self):
        self.hold()
        with self.assertRaises(omni_instance.AlreadyRunning) as raised:
            omni_instance.acquire(self.dir)
        self.assertIn("Close the other copy", str(raised.exception))
        self.assertIn(str(self.dir), str(raised.exception))

    def test_a_killed_holder_leaves_no_stale_lock(self):
        """Crash recovery: the file stays behind, the lock does not."""
        holder = self.hold()
        holder.proc.kill()
        holder.proc.wait(timeout=10)
        self.assertTrue((self.dir / omni_instance.LOCK_FILE).exists(),
                        "the precondition: a lock file from the dead copy remains")
        deadline = time.monotonic() + 10
        while True:
            try:
                omni_instance.acquire(self.dir)
                break
            except omni_instance.AlreadyRunning:
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.1)

    def test_an_orderly_release_lets_the_next_copy_in(self):
        omni_instance.acquire(self.dir)
        omni_instance.release(self.dir)
        holder = self.hold()
        self.assertIsNone(holder.proc.poll())

    def test_different_data_directories_do_not_exclude_each_other(self):
        other = Path(tempfile.mkdtemp(prefix="omni-instance-other-"))
        self.addCleanup(omni_instance.release, other)
        self.hold(other)
        omni_instance.acquire(self.dir)

    def test_acquiring_twice_in_one_process_is_not_a_conflict(self):
        omni_instance.acquire(self.dir)
        omni_instance.acquire(self.dir)

    def test_a_directory_that_does_not_exist_yet_is_created(self):
        nested = self.dir / "a" / "b"
        self.addCleanup(omni_instance.release, nested)
        omni_instance.acquire(nested)
        self.assertTrue((nested / omni_instance.LOCK_FILE).exists())

    def test_importing_the_module_takes_no_lock(self):
        out = subprocess.run(
            [sys.executable, "-c",
             "import omni_instance, sys; print(len(omni_instance._held))"],
            capture_output=True, text=True, env=_env(), cwd=str(ROOT), timeout=30)
        self.assertEqual(out.stdout.strip(), "0")

    def test_the_server_refuses_to_start_on_another_port(self):
        """The reported case, end to end: same folder, different port.

        Run as a separate process with the real entry point. It must stop before
        choosing a port, starting any background work or serving, and say why.
        """
        self.hold()
        before = sorted(p.name for p in self.dir.iterdir())
        run = subprocess.run(
            # Through the test package, which installs the network fence first: if
            # the guard ever regressed this process would start real background
            # work (an update check, a cache verification), and a test must not be
            # able to reach the Internet or a device because it failed.
            [sys.executable, "-c",
             "import tests, runpy, sys; "
             "runpy.run_path(sys.argv[1], run_name='__main__')",
             str(ROOT / "OmniMatrix_upgrade_server_v7_6y.py")],
            capture_output=True, text=True, timeout=60, cwd=str(ROOT),
            env=_env(OMNI_DATA_DIR=str(self.dir), OMNI_PORT="18999",
                     OMNI_HOST="127.0.0.1"))
        combined = run.stdout + run.stderr
        self.assertEqual(run.returncode, 1, combined[-800:])
        self.assertIn("already running", combined)
        for must_not in ("Serving on", "Port 18999", "synchronous verification"):
            self.assertNotIn(must_not, combined,
                             f"the second copy began work: {must_not!r}")
        created = sorted(set(p.name for p in self.dir.iterdir()) - set(before))
        self.assertEqual(created, [], "the refused copy wrote into the data folder")


class OrderTests(unittest.TestCase):
    """The lock has to come first. Asserted from source, comments removed."""

    @staticmethod
    def code(path, start, end=None):
        text = (ROOT / path).read_text(encoding="utf-8")
        body = text[text.index(start):]
        if end:
            body = body[:body.index(end)]
        return "\n".join(line.split("#")[0] for line in body.split("\n"))

    def test_the_server_takes_it_before_choosing_a_port_or_starting_work(self):
        body = self.code("OmniMatrix_upgrade_server_v7_6y.py", "def main():",
                         "def run_server():")
        lock = body.index("omni_instance.acquire")
        for later in ("_select_bind_port", "start_background_startup_tasks",
                      "restore_remote_access"):
            self.assertLess(lock, body.index(later), later)

    def test_the_launcher_takes_it_before_the_window_and_the_server(self):
        body = self.code("app_launcher.py", "def main()", 'if __name__')
        lock = body.index("omni_instance.acquire")
        self.assertLess(lock, body.index("tk.Tk()"))
        self.assertLess(lock, body.index("AppWindow("))
        text = (ROOT / "app_launcher.py").read_text(encoding="utf-8")
        top = "\n".join(l.split("#")[0] for l in text[:text.index("def main()")].split("\n"))
        self.assertIsNone(re.search(r"^import OmniMatrix_upgrade_server", top, re.M),
                          "the launcher must not import the server at module level")

    def test_the_launcher_uses_the_folder_the_server_will_use(self):
        body = self.code("app_launcher.py", "def main()", 'if __name__')
        self.assertIn('os.environ.get("OMNI_DATA_DIR") or DATA_DIR', body)

    def test_a_refused_second_copy_shows_one_message_then_exits_nonzero(self):
        """The refusal is a deliberate message box, and dismissing it ends the process.

        The window of the refused copy stays up until the operator reads and
        dismisses the message -- that is the notice, not a hang. What must hold
        is that exactly one message is shown, that no application window or
        server is created, and that main() then returns a non-zero status so the
        process (and a packaged build's bootloader and temporary folder) end.
        """
        from unittest import mock
        import app_launcher
        created, shown = [], []
        with mock.patch.object(app_launcher.omni_instance_module(), "acquire",
                               side_effect=omni_instance.AlreadyRunning("d", 4242)) \
                if hasattr(app_launcher, "omni_instance_module") else \
                mock.patch.object(omni_instance, "acquire",
                                  side_effect=omni_instance.AlreadyRunning("d", 4242)):
            class FakeTk:
                def __init__(self):
                    created.append(self)
                def withdraw(self):
                    pass
                def destroy(self):
                    self.destroyed = True
                def mainloop(self):
                    raise AssertionError("a refused copy must not run an event loop")
            with mock.patch.object(app_launcher.tk, "Tk", FakeTk), \
                    mock.patch.object(app_launcher.messagebox, "showerror",
                                      side_effect=lambda *a, **k: shown.append(a)), \
                    mock.patch.object(app_launcher, "log_message"),                     mock.patch.object(app_launcher, "AppWindow",
                                      side_effect=AssertionError("no window")):
                status = app_launcher.main()
        self.assertEqual(status, 1)
        self.assertEqual(len(shown), 1, shown)
        self.assertEqual(shown[0][0], "OmniSuite is already running")
        self.assertEqual(len(created), 1, "only the hidden owner of the message box")
        self.assertTrue(created[0].destroyed)


if __name__ == "__main__":
    unittest.main()
