"""The launcher's fixed-port behaviour, driven under a REAL Tk root.

OMNI-MA-001. Each scenario runs the real `app_launcher.AppWindow` in a fresh
subprocess with a withdrawn Tk root, a scratch data folder and a stub server
module, and presses the launcher's own buttons with `invoke()`. Nothing is
stubbed in the launcher itself. Skipped, loudly, only if Tk cannot start.

Listeners are on loopback only; every subprocess stops its server and every
occupier before it exits, and the parent kills a subprocess that does not.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import omni_local_port as lp                            # noqa: E402

PRELUDE = r'''
import json, os, socket, sys, threading, time, types, urllib.request
sys.path.insert(0, ROOT)
os.environ["OMNI_SMOKE"] = "1"
import tkinter as tk
import app_launcher
import omni_local_port as lp
from flask import Flask

RESULT = {}
CALLS = []
opened = []
app_launcher.webbrowser.open = lambda url, new=0: (opened.append(url), True)[1]
LISTENERS = []

def occupy(port=0):
    s = socket.socket(); s.bind(("127.0.0.1", port)); s.listen(2)
    LISTENERS.append(s); return s

def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p

def stub():
    flask_app = Flask("stub")
    @flask_app.route("/__health")
    def health(): return "ok"
    mod = types.SimpleNamespace(app=flask_app)
    mod.start_background_startup_tasks = lambda: CALLS.append("startup")
    mod.restore_remote_access = lambda: CALLS.append("restore")
    return mod

root = tk.Tk(); root.withdraw()

def pump(cond, timeout=15.0):
    end = time.time() + timeout
    while time.time() < end:
        root.update()
        if cond(): return True
        time.sleep(0.02)
    return False

def get(port, path="/__health"):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=3) as r:
        return r.status

STUB = stub()
# The automatic path imports the server module by name; give it the stub too.
sys.modules["OmniMatrix_upgrade_server_v7_6y"] = STUB

def window(**kw):
    w = app_launcher.AppWindow(root, server_loader=lambda: STUB, **kw)
    return w

def text(w): return w.status_label.cget("text")
def running(w): return text(w).endswith("Server Running")
def conflict(w): return text(w) == "Server could not start"

def finish(w=None):
    try:
        if w is not None and w.httpd is not None:
            w.httpd.shutdown(); w.httpd.server_close()
    except Exception: pass
    for s in LISTENERS:
        try: s.close()
        except Exception: pass
    print("RESULT " + json.dumps(RESULT)); sys.stdout.flush()
    os._exit(0)
'''


def run_scenario(body, data, extra_env=None, timeout=90):
    # The scenario runs inside Tk's main loop, as the launcher itself does:
    # its server thread schedules UI work with root.after, which Tk refuses
    # from another thread unless the main thread is in the loop.
    body = textwrap.indent(textwrap.dedent(body), "    ")
    script = ("ROOT = " + repr(str(ROOT)) + chr(10) + PRELUDE + chr(10)
              + "def scenario():" + chr(10) + body + chr(10)
              + "def guarded():" + chr(10)
              + "    try:" + chr(10) + "        scenario()" + chr(10)
              + "    except BaseException:" + chr(10)
              + "        import traceback; traceback.print_exc(); sys.stderr.flush(); os._exit(2)" + chr(10)
              + "root.after(0, guarded)" + chr(10) + "root.mainloop()" + chr(10))
    env = dict(os.environ)
    env["OMNI_DATA_DIR"] = str(data)
    env["LOCALAPPDATA"] = str(data / "localappdata")
    env["HOME"] = str(data / "home")
    env["USERPROFILE"] = str(data / "home")
    env.pop("OMNI_RESTARTED", None)
    env.update(extra_env or {})
    try:
        proc = subprocess.run([sys.executable, "-c", script], capture_output=True,
                              text=True, timeout=timeout, env=env, cwd=str(ROOT))
    except subprocess.TimeoutExpired as exc:
        raise AssertionError("scenario timed out: %s %s" % (exc.stdout, exc.stderr))
    for line in (proc.stdout or "").splitlines():
        if line.startswith("RESULT "):
            return json.loads(line[7:]), proc
    raise AssertionError("no RESULT (exit %s)\n%s\n%s" % (proc.returncode, proc.stdout,
                                                         proc.stderr[-3000:]))


def tk_available():
    try:
        done = subprocess.run([sys.executable, "-c",
                               "import tkinter as t; r=t.Tk(); r.withdraw(); r.destroy()"],
                              capture_output=True, timeout=30)
        return done.returncode == 0
    except Exception:
        return False


TK = tk_available()


@unittest.skipUnless(TK, "SKIPPED LOUDLY: Tk cannot initialise here, so the launcher "
                         "recovery state was NOT exercised")
class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.folder.cleanup)
        self.data = Path(self.folder.name)
        (self.data / "localappdata").mkdir()
        (self.data / "home").mkdir()

    @property
    def file(self):
        return self.data / lp.CONFIG_FILE

    def fixed(self, port):
        lp.write_config(self.data, lp.MODE_FIXED, port)

    # -- 1, 2, 3
    def test_default_is_automatic_and_starts_normally(self):
        self.assertFalse(self.file.exists())
        result, _ = run_scenario("""
            w = window(port=free_port())
            RESULT["ok"] = pump(lambda: running(w))
            RESULT["fixed_mode"] = w.fixed_mode
            RESULT["url"] = w.url_var.get()
            RESULT["port"] = w.port
            RESULT["status"] = w.supervisor.status()
            RESULT["detail_shown"] = bool(w.detail_label.winfo_manager())
            RESULT["recovery_shown"] = bool(w.recovery_frame.winfo_manager())
            RESULT["health"] = get(w.port)
            RESULT["services"] = CALLS
            w.open_button.invoke()
            RESULT["opened"] = opened
            finish(w)
        """, self.data)
        self.assertTrue(result["ok"])
        self.assertFalse(result["fixed_mode"])
        self.assertEqual(result["url"], "http://127.0.0.1:%d" % result["port"])
        self.assertEqual(result["status"]["mode"], "automatic")
        self.assertEqual(result["status"]["actual_port"], result["port"])
        self.assertFalse(result["detail_shown"], "a normal start adds no new UI")
        self.assertFalse(result["recovery_shown"])
        self.assertEqual(result["health"], 200)
        self.assertEqual(result["services"], ["startup", "restore"])
        self.assertEqual(result["opened"], [result["url"]])
        self.assertFalse(self.file.exists(), "automatic startup writes nothing")

    def test_automatic_still_drifts_to_the_next_free_port(self):
        result, _ = run_scenario("""
            start = free_port()
            occupy(start)
            w = window(port=start)
            RESULT["ok"] = pump(lambda: running(w))
            RESULT["moved"] = w.port - start
            RESULT["health"] = get(w.port)
            finish(w)
        """, self.data)
        self.assertTrue(result["ok"])
        self.assertGreaterEqual(result["moved"], 1)
        self.assertEqual(result["health"], 200)

    # -- 7
    def test_fixed_startup_binds_exactly_and_shows_the_actual_url(self):
        port = lp.parse_port(free_port_value())
        self.fixed(port)
        result, _ = run_scenario("""
            w = window()
            RESULT["ok"] = pump(lambda: running(w))
            RESULT["url"] = w.url_var.get()
            RESULT["port"] = w.port
            RESULT["health"] = get(w.port)
            RESULT["temporary"] = w.supervisor.temporary
            RESULT["detail_shown"] = bool(w.detail_label.winfo_manager())
            w.open_button.invoke()
            RESULT["opened"] = opened
            finish(w)
        """, self.data)
        self.assertTrue(result["ok"])
        self.assertEqual(result["port"], port)
        self.assertEqual(result["url"], "http://127.0.0.1:%d" % port)
        self.assertEqual(result["opened"], [result["url"]])
        self.assertFalse(result["temporary"])
        self.assertFalse(result["detail_shown"])

    # -- 8, 9, 10
    def test_an_occupied_fixed_port_enters_recovery_with_no_silent_fallback(self):
        port = free_port_value()
        self.fixed(port)
        before = self.file.read_bytes()
        result, _ = run_scenario("""
            occupy(%d)
            w = window()
            RESULT["conflict"] = pump(lambda: conflict(w))
            RESULT["status"] = text(w)
            RESULT["detail"] = w.detail_label.cget("text")
            RESULT["retry"] = w.retry_button.cget("text")
            RESULT["alt"] = w.alternate_button.cget("text")
            RESULT["change"] = w.change_button.cget("text")
            RESULT["exit"] = w.recovery_exit_button.cget("text")
            RESULT["states"] = [str(b.cget("state")) for b in
                (w.retry_button, w.alternate_button, w.change_button, w.recovery_exit_button)]
            RESULT["open_state"] = str(w.open_button.cget("state"))
            RESULT["url"] = w.url_var.get()
            RESULT["httpd"] = w.httpd is None
            RESULT["services"] = CALLS
            RESULT["usable"] = bool(root.winfo_exists())
            # nothing else is serving: the next ports are closed
            others = []
            for p in range(%d + 1, %d + 4):
                try:
                    socket.create_connection(("127.0.0.1", p), timeout=0.3).close(); others.append(p)
                except OSError: pass
            RESULT["others"] = others
            RESULT["fits"] = (w.canvas.winfo_height() >= 0)
            w.root.update_idletasks()
            RESULT["need_h"] = w.canvas.winfo_children()[0].winfo_reqheight()
            finish(w)
        """ % (port, port, port), self.data)
        self.assertTrue(result["conflict"])
        self.assertEqual(result["status"], "Server could not start")
        self.assertEqual(result["detail"],
                         "Configured TCP port %d is already in use." % port)
        self.assertEqual(result["retry"], "Retry %d" % port)
        self.assertEqual(result["alt"], "Start on Alternate Port")
        self.assertEqual(result["change"], "Change Fixed Port")
        self.assertEqual(result["exit"], "Exit")
        self.assertEqual(result["states"], ["normal"] * 4)
        self.assertEqual(result["open_state"], "disabled")
        self.assertEqual(result["url"], "")
        self.assertTrue(result["httpd"])
        self.assertEqual(result["services"], [], "the application did not start")
        self.assertEqual(result["others"], [], "silent fallback to another port")
        self.assertLessEqual(result["need_h"], 440, "the recovery state must fit the window")
        self.assertEqual(self.file.read_bytes(), before)

    # -- 11
    def test_retry_recovers_when_the_port_is_released_and_changes_nothing(self):
        port = free_port_value()
        self.fixed(port)
        before = self.file.read_bytes()
        result, _ = run_scenario("""
            held = occupy(%d)
            w = window()
            pump(lambda: conflict(w))
            w.retry_button.invoke()
            RESULT["still"] = pump(lambda: conflict(w) and not w._attempting, 8)
            time.sleep(0.4)
            RESULT["again"] = conflict(w)
            held.close(); LISTENERS.remove(held)
            w.retry_button.invoke()
            RESULT["ok"] = pump(lambda: running(w))
            RESULT["url"] = w.url_var.get()
            RESULT["recovery_hidden"] = not w.recovery_frame.winfo_manager()
            RESULT["buttons_back"] = bool(w.button_frame.winfo_manager())
            RESULT["detail_cleared"] = not w.detail_label.winfo_manager()
            RESULT["services"] = CALLS
            finish(w)
        """ % port, self.data)
        self.assertTrue(result["again"])
        self.assertTrue(result["ok"])
        self.assertEqual(result["url"], "http://127.0.0.1:%d" % port)
        self.assertTrue(result["recovery_hidden"] and result["buttons_back"])
        self.assertTrue(result["detail_cleared"])
        self.assertEqual(result["services"], ["startup", "restore"])
        self.assertEqual(self.file.read_bytes(), before)

    # -- 12, 13, 14, 15
    def test_alternate_runs_the_complete_application_for_this_session_only(self):
        port = free_port_value()
        self.fixed(port)
        before = self.file.read_bytes()
        result, _ = run_scenario("""
            occupy(%d)
            w = window()
            pump(lambda: conflict(w))
            w.alternate_button.invoke()
            RESULT["ok"] = pump(lambda: running(w))
            RESULT["port"] = w.port
            RESULT["url"] = w.url_var.get()
            RESULT["health"] = get(w.port)
            RESULT["services"] = list(CALLS)
            RESULT["status"] = w.supervisor.status()
            RESULT["detail"] = w.detail_label.cget("text")
            RESULT["detail_shown"] = bool(w.detail_label.winfo_manager())
            w.open_button.invoke()
            RESULT["opened"] = opened
            RESULT["env_port"] = os.environ.get("OMNI_PORT")
            finish(w)
        """ % port, self.data)
        self.assertTrue(result["ok"])
        self.assertNotEqual(result["port"], port)
        self.assertEqual(result["url"], "http://127.0.0.1:%d" % result["port"])
        self.assertEqual(result["health"], 200, "the application is serving")
        self.assertEqual(result["services"], ["startup", "restore"])
        self.assertEqual(result["opened"], [result["url"]])
        self.assertEqual(result["env_port"], str(result["port"]))
        status = result["status"]
        self.assertTrue(status["temporary"])
        self.assertEqual(status["configured_port"], port)
        self.assertEqual(status["actual_port"], result["port"])
        self.assertTrue(result["detail_shown"])
        self.assertIn(str(port), result["detail"])
        self.assertEqual(self.file.read_bytes(), before,
                         "the alternate must never overwrite the fixed port")

    # -- 16
    def test_the_next_normal_start_retries_the_configured_port(self):
        port = free_port_value()
        self.fixed(port)
        first, _ = run_scenario("""
            occupy(%d)
            w = window(); pump(lambda: conflict(w))
            w.alternate_button.invoke(); RESULT["alt"] = pump(lambda: running(w))
            RESULT["port"] = w.port
            finish(w)
        """ % port, self.data)
        self.assertTrue(first["alt"])
        second, _ = run_scenario("""
            w = window()
            RESULT["ok"] = pump(lambda: running(w))
            RESULT["port"] = w.port
            RESULT["temporary"] = w.supervisor.temporary
            finish(w)
        """, self.data)
        self.assertTrue(second["ok"])
        self.assertEqual(second["port"], port)
        self.assertFalse(second["temporary"])

    # -- 17, 18, 19
    def test_change_fixed_port_from_the_launcher(self):
        old = free_port_value()
        self.fixed(old)
        before = self.file.read_bytes()
        result, _ = run_scenario("""
            occupy(%d)
            taken = occupy()
            taken_port = taken.getsockname()[1]
            w = window(); pump(lambda: conflict(w))
            w.change_button.invoke()
            RESULT["dialog_title"] = w.change_dialog.title()
            labels = [c.cget("text") for c in w.change_dialog.winfo_children() if c.winfo_class() == "Label"]
            RESULT["labels"] = labels
            RESULT["buttons"] = [w.change_cancel_button.cget("text"), w.change_start_button.cget("text")]
            # invalid values stay in the dialog and change nothing
            for bad in ("", "abc", "80", "70000"):
                w.change_entry.delete(0, "end"); w.change_entry.insert(0, bad)
                RESULT["bad_" + (bad or "blank")] = (w.change_start_button.invoke(), w.change_error.cget("text"))
            RESULT["dialog_still_open"] = w.change_dialog is not None
            RESULT["file_after_invalid"] = open(%r, "rb").read().decode()
            # a valid but occupied port: a failed bind, config untouched
            w.change_entry.delete(0, "end"); w.change_entry.insert(0, str(taken_port))
            w.change_start_button.invoke()
            pump(lambda: conflict(w) and not w._attempting)
            time.sleep(0.3); root.update()
            RESULT["failed_detail"] = w.detail_label.cget("text")
            RESULT["failed_conflict"] = conflict(w)
            RESULT["file_after_failed"] = open(%r, "rb").read().decode()
            RESULT["retry_label"] = w.retry_button.cget("text")
            # another attempt with a free port succeeds and only then persists
            good = free_port()
            w.change_button.invoke()
            w.change_entry.delete(0, "end"); w.change_entry.insert(0, str(good))
            w.change_start_button.invoke()
            RESULT["ok"] = pump(lambda: running(w))
            RESULT["port"] = w.port; RESULT["good"] = good
            RESULT["file_after_success"] = open(%r, "rb").read().decode()
            RESULT["status"] = w.supervisor.status()
            RESULT["health"] = get(good)
            finish(w)
        """ % (old, str(self.file), str(self.file), str(self.file)), self.data)
        self.assertEqual(result["dialog_title"], "Change fixed TCP port")
        self.assertIn("Change fixed TCP port", result["labels"])
        self.assertIn("Configured port %d is unavailable." % old, result["labels"])
        self.assertIn("New TCP port:", result["labels"])
        self.assertEqual(result["buttons"], ["Cancel", "Start"])
        for key in ("bad_blank", "bad_abc", "bad_80", "bad_70000"):
            self.assertFalse(result[key][0], key)
            self.assertTrue(result[key][1], key)
        self.assertTrue(result["dialog_still_open"])
        self.assertEqual(result["file_after_invalid"].encode(), before)
        self.assertTrue(result["failed_conflict"])
        self.assertIn("was not changed", result["failed_detail"])
        self.assertEqual(result["file_after_failed"].encode(), before,
                         "a failed change must leave the old configuration byte for byte")
        self.assertEqual(result["retry_label"], "Retry %d" % old,
                         "Retry still means the configured port")
        self.assertTrue(result["ok"])
        self.assertEqual(result["port"], result["good"])
        saved = json.loads(result["file_after_success"])
        self.assertEqual(saved["configured_local_fixed_port"], result["good"])
        self.assertEqual(saved["local_port_mode"], "fixed")
        self.assertFalse(result["status"]["temporary"])
        self.assertEqual(result["health"], 200)

    def test_cancel_in_the_change_dialog_changes_nothing(self):
        old = free_port_value()
        self.fixed(old)
        before = self.file.read_bytes()
        result, _ = run_scenario("""
            occupy(%d)
            w = window(); pump(lambda: conflict(w))
            w.change_button.invoke()
            w.change_entry.insert(0, "28444")
            w.change_cancel_button.invoke()
            RESULT["closed"] = w.change_dialog is None
            RESULT["conflict"] = conflict(w)
            finish(w)
        """ % old, self.data)
        self.assertTrue(result["closed"] and result["conflict"])
        self.assertEqual(self.file.read_bytes(), before)

    # -- 20
    def test_make_the_alternate_fixed(self):
        port = free_port_value()
        self.fixed(port)
        result, _ = run_scenario("""
            occupy(%d)
            w = window(); pump(lambda: conflict(w))
            w.alternate_button.invoke(); pump(lambda: running(w))
            actual = w.port
            body, status = w.supervisor.make_fixed()
            RESULT["status_code"] = status
            RESULT["body"] = body
            RESULT["actual"] = actual
            RESULT["after"] = w.supervisor.status()
            RESULT["health"] = get(actual)
            finish(w)
        """ % port, self.data)
        self.assertEqual(result["status_code"], 200)
        self.assertFalse(result["body"]["restarting"])
        self.assertEqual(lp.read_config(self.data)["port"], result["actual"])
        self.assertFalse(result["after"]["temporary"])
        self.assertEqual(result["health"], 200, "no restart: it kept serving")

    # -- corrupt file must not prevent start
    def test_a_corrupt_configuration_never_prevents_the_launcher_starting(self):
        self.file.write_text("\x00\x01 garbage", encoding="utf-8")
        result, _ = run_scenario("""
            w = window(port=free_port())
            RESULT["ok"] = pump(lambda: running(w))
            RESULT["fixed_mode"] = w.fixed_mode
            finish(w)
        """, self.data)
        self.assertTrue(result["ok"])
        self.assertFalse(result["fixed_mode"])
        self.assertTrue(list(self.data.glob("local_port.corrupt-*.json")))

    # -- 21 and the restart helper
    def test_save_and_restart_releases_the_listener_before_the_new_process(self):
        """The old listener, the Remote Access listener and the data folder are
        all released by the time the replacement is started."""
        port = free_port_value()
        self.fixed(port)
        result, _ = run_scenario("""
            import omni_instance
            w = window()
            pump(lambda: running(w))
            omni_instance.acquire(os.environ["OMNI_DATA_DIR"])
            observed = {}
            class Remote:
                stopped = False
                def stop(self): Remote.stopped = True
            w.server_module._remote_listener = Remote()
            def fake_spawn():
                observed["port_free"] = lp.probe("127.0.0.1", w.port) is None
                observed["remote_stopped"] = Remote.stopped
                observed["lock_released"] = not omni_instance._held
                observed["command"] = w.replacement_command()
                return True
            w.spawn_replacement = fake_spawn
            RESULT["held_before"] = lp.probe("127.0.0.1", w.port) is not None
            try:
                w.restart_now()
            except SystemExit:
                observed["exited"] = True
            RESULT["observed"] = observed
            print("RESULT " + json.dumps(RESULT)); sys.stdout.flush(); os._exit(0)
        """, self.data)
        observed = result["observed"]
        self.assertTrue(result["held_before"], "the old listener held the port")
        self.assertTrue(observed["port_free"], "the port must be free when the new process starts")
        self.assertTrue(observed["remote_stopped"])
        self.assertTrue(observed["lock_released"], "the data folder lock must be released")
        self.assertTrue(observed["exited"])
        self.assertTrue(observed["command"][0])

    def test_a_restarted_process_waits_for_the_old_one_to_let_go(self):
        """A restarted process retries the folder lock; a second copy does not."""
        holder = subprocess.Popen(
            [sys.executable, "-c",
             "import sys,time;sys.path.insert(0,%r);import omni_instance as o;"
             "o.acquire(%r);print('H',flush=True);time.sleep(1.5)" % (str(ROOT), str(self.data))],
            stdout=subprocess.PIPE, text=True)
        self.addCleanup(holder.kill)
        self.assertEqual(holder.stdout.readline().strip(), "H")
        env = dict(os.environ, OMNI_DATA_DIR=str(self.data), OMNI_RESTARTED="1",
                   LOCALAPPDATA=str(self.data / "localappdata"), OMNI_SMOKE="1")
        probe = ("import sys;sys.path.insert(0,%r);import time,omni_instance as o;"
                 "d=time.monotonic()+15\n"
                 "while True:\n"
                 "    try: o.acquire(%r);print('GOT',flush=True);break\n"
                 "    except o.AlreadyRunning:\n"
                 "        if time.monotonic()>=d: print('REFUSED');break\n"
                 "        time.sleep(0.25)\n" % (str(ROOT), str(self.data)))
        done = subprocess.run([sys.executable, "-c", probe], capture_output=True,
                              text=True, timeout=30, env=env)
        self.assertIn("GOT", done.stdout)
        source = (ROOT / "app_launcher.py").read_text(encoding="utf-8")
        self.assertIn('os.environ.get("OMNI_RESTARTED") else 0.0', source)

    def test_a_failure_inside_a_button_handler_reaches_the_launcher_log(self):
        """The packaged launcher has no console, so Tk's default report is lost."""
        result, _ = run_scenario("""
            w = window(port=free_port())
            pump(lambda: running(w))
            try:
                raise ValueError("handler-failure-marker")
            except ValueError:
                w.root.report_callback_exception(*sys.exc_info())
            RESULT["log"] = open(app_launcher.LOG_PATH, encoding="utf-8").read()
            finish(w)
        """, self.data)
        self.assertIn("Launcher callback failed", result["log"])
        self.assertIn("handler-failure-marker", result["log"])

    # -- 22
    def test_the_launcher_always_displays_the_actual_listener(self):
        port = free_port_value()
        self.fixed(port)
        result, _ = run_scenario("""
            held = occupy(%d)
            w = window(); pump(lambda: conflict(w))
            RESULT["during_conflict_url"] = w.url_var.get()
            w.alternate_button.invoke(); pump(lambda: running(w))
            RESULT["alt_url"] = w.url_var.get(); RESULT["alt_port"] = w.port
            RESULT["current_url"] = w.current_url()
            finish(w)
        """ % port, self.data)
        self.assertEqual(result["during_conflict_url"], "")
        self.assertEqual(result["alt_url"], "http://127.0.0.1:%d" % result["alt_port"])
        self.assertEqual(result["current_url"], result["alt_url"])

    # -- 23
    def test_free_at_validation_occupied_before_the_real_bind_recovers(self):
        port = free_port_value()
        result, _ = run_scenario("""
            supervisor = lp.PortSupervisor(os.environ["OMNI_DATA_DIR"], restart=lambda: None)
            supervisor.load(); supervisor.bound(free_port())
            body, status = supervisor.set_fixed(%d)
            RESULT["saved"] = status
            occupy(%d)                      # taken between the probe and the bind
            os.environ.pop("OMNI_RESTARTED", None)
            w = window()
            RESULT["conflict"] = pump(lambda: conflict(w))
            RESULT["detail"] = w.detail_label.cget("text")
            finish(w)
        """ % (port, port), self.data)
        self.assertEqual(result["saved"], 200)
        self.assertTrue(result["conflict"])
        self.assertEqual(result["detail"],
                         "Configured TCP port %d is already in use." % port)

    def test_a_restarted_process_tolerates_the_old_listener_letting_go(self):
        """The grace covers the operating system's latency, not a real conflict."""
        port = free_port_value()
        self.fixed(port)
        result, _ = run_scenario("""
            held = occupy(%d)
            threading.Timer(1.0, lambda: held.close()).start()
            w = window()
            RESULT["ok"] = pump(lambda: running(w), 20)
            RESULT["port"] = w.port
            finish(w)
        """ % port, self.data, extra_env={"OMNI_RESTARTED": "1"})
        self.assertTrue(result["ok"])
        self.assertEqual(result["port"], port)


def free_port_value():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


if __name__ == "__main__":
    unittest.main()
