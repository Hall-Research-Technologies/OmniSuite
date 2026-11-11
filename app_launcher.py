#!/usr/bin/env python3
"""Standalone GUI launcher for OmniSuite."""

from __future__ import annotations

import logging
import errno
import os
import socket
import subprocess
import sys
import threading
import time
import traceback
import webbrowser
from pathlib import Path

try:
    import tkinter as tk
    from tkinter import messagebox, ttk
    from PIL import Image, ImageTk
    if sys.platform == "darwin":
        pystray = None
    else:
        import pystray
except ImportError as exc:
    print(f"Launcher dependency error: {exc}")
    raise


import omni_local_port

IS_FROZEN = getattr(sys, "frozen", False)
EXE_DIR = Path(sys.executable).resolve().parent if IS_FROZEN else Path(__file__).resolve().parent
BUNDLE_DIR = Path(getattr(sys, "_MEIPASS", EXE_DIR)).resolve() if IS_FROZEN else EXE_DIR


def _get_log_path() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "OmniSuite" / "logs"
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Logs" / "OmniSuite"
    else:
        base = Path.home() / ".omnisuite" / "logs"
    base.mkdir(parents=True, exist_ok=True)
    return base / "launcher.log"


def _get_data_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "OmniSuite"
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / "OmniSuite"
    else:
        base = Path.home() / ".omnisuite"
    base.mkdir(parents=True, exist_ok=True)
    return base


LOG_PATH = _get_log_path()
DATA_DIR = _get_data_dir()


def log_message(message: str) -> None:
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {message}"
    print(line)
    try:
        with LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except Exception:
        pass


def _log_unhandled_exception(exc_type, exc_value, exc_traceback) -> None:
    details = "".join(traceback.format_exception(exc_type, exc_value, exc_traceback))
    log_message(f"Unhandled exception:\n{details}")


sys.excepthook = _log_unhandled_exception


def resolve_asset(*relative_parts: str) -> Path | None:
    rel = Path(*relative_parts)
    candidates = [
        BUNDLE_DIR / rel,
        EXE_DIR / rel,
        Path(__file__).resolve().parent / rel,
        BUNDLE_DIR / "ui" / rel.name,
        EXE_DIR / "ui" / rel.name,
    ]
    for path in candidates:
        if path.exists():
            return path
    return None


def resolve_version() -> str:
    env_version = (os.getenv("OMNI_VERSION") or os.getenv("RELEASE_VERSION") or "").strip()
    if env_version:
        return env_version

    for candidate in [EXE_DIR / "VERSION", BUNDLE_DIR / "VERSION", Path(__file__).resolve().parent / "VERSION"]:
        try:
            text = candidate.read_text(encoding="utf-8").strip()
            if text:
                return text
        except Exception:
            continue
    return "V0.0.0"


class AppWindow:
    def __init__(self, root: tk.Tk, host: str = "127.0.0.1", port: int = 8080,
                 server_loader=None):
        self.root = root
        self.host = host
        # The local port is automatic unless the operator chose a fixed one.
        # The stored choice can never stop a start: an unreadable file is
        # AUTOMATIC, and a fixed port that cannot be bound lands in the
        # recovery state below rather than in a failure.
        self.data_dir = Path(os.environ.get("OMNI_DATA_DIR") or DATA_DIR)
        self.supervisor = omni_local_port.PortSupervisor(
            self.data_dir, host, restart=self.request_restart)
        stored = self.supervisor.load()
        self.fixed_mode = stored["mode"] == omni_local_port.MODE_FIXED
        if self.fixed_mode:
            # Exactly the configured port. No search, no drift.
            self.port = stored["port"]
        else:
            self.port = self.find_available_port(port)
        omni_local_port.register_controller(self.supervisor)
        self.server_loader = server_loader
        self.httpd = None
        self._services_started = False
        self._attempt_lock = threading.Lock()
        self._attempting = False
        self.change_dialog = None
        self.header_label = None
        self.footer_label = None
        self.version = resolve_version()
        self.server_module = None
        self.server_thread = None
        self.running = False
        self.tray_icon = None
        self.window_icon_photo = None
        self.header_photo = None
        self.footer_photo = None
        self.window_width = 540
        self.window_height = 440

        # A failure inside a button handler used to go to a console nobody sees
        # (the packaged launcher has none). Put it where the rest of the
        # launcher's problems already go.
        self.root.report_callback_exception = self._log_callback_exception
        self.root.title(f"OmniSuite {self.version}")
        self.root.geometry(f"{self.window_width}x{self.window_height}")
        self.root.resizable(False, False)
        self._apply_window_icon()
        self._center_window()
        self.create_widgets()
        self.setup_tray_icon()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.start_server()

    def _log_callback_exception(self, exc_type, exc_value, exc_traceback) -> None:
        details = "".join(traceback.format_exception(exc_type, exc_value, exc_traceback))
        log_message(f"Launcher callback failed: {details}")

    def _apply_window_icon(self) -> None:
        icon_path = resolve_asset("omnimatrix.ico")
        if icon_path:
            try:
                self.root.iconbitmap(default=str(icon_path))
            except Exception as exc:
                log_message(f"Could not set iconbitmap: {exc}")
            try:
                icon_image = Image.open(icon_path)
                self.window_icon_photo = ImageTk.PhotoImage(icon_image)
                self.root.iconphoto(True, self.window_icon_photo)
            except Exception as exc:
                log_message(f"Could not set iconphoto: {exc}")

    def _center_window(self) -> None:
        self.root.update_idletasks()
        x = (self.root.winfo_screenwidth() // 2) - (self.window_width // 2)
        y = (self.root.winfo_screenheight() // 2) - (self.window_height // 2)
        self.root.geometry(f"+{x}+{y}")

    def find_available_port(self, start_port: int) -> int:
        port = start_port
        while port < start_port + 100:
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                    sock.bind((self.host, port))
                return port
            except OSError:
                port += 1
        return start_port

    def create_widgets(self) -> None:
        self.canvas = tk.Canvas(self.root, width=self.window_width, height=self.window_height, bg="#000000", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)

        frame = tk.Frame(self.canvas, bg="#000000", bd=0)
        frame.place(relx=0.5, rely=0.5, anchor="center", width=500, height=400)

        header_path = resolve_asset("hallway.png")
        if header_path:
            try:
                header = Image.open(header_path)
                width = 300
                height = int(header.height * (width / header.width))
                header = header.resize((width, height), Image.Resampling.LANCZOS)
                self.header_photo = ImageTk.PhotoImage(header)
                self.header_label = tk.Label(frame, image=self.header_photo, bg="#000000", bd=0)
                self.header_label.pack(pady=(8, 8))
            except Exception as exc:
                log_message(f"Could not load hallway.png: {exc}")

        self.title_label = tk.Label(frame, text="OmniSuite", font=("Helvetica", 20, "bold"), fg="#ffffff", bg="#000000")
        self.title_label.pack(pady=(2, 4))
        self.version_label = tk.Label(frame, text=f"OmniSuite {self.version}", font=("Helvetica", 9), fg="#888888", bg="#000000")
        self.version_label.pack(pady=(0, 10))

        self.status_label = tk.Label(frame, text="Starting server...", font=("Helvetica", 12), fg="#4CAF50", bg="#000000")
        self.status_label.pack(pady=6)

        # Shown only in the fixed-port recovery state and while running on a
        # temporary port. Packed on demand, so a normal start looks exactly as
        # it always has.
        self.detail_label = tk.Label(frame, text="", font=("Helvetica", 10), fg="#FFB300", bg="#000000", wraplength=440, justify="center")

        self.url_var = tk.StringVar(value="")
        self.url_label = tk.Label(frame, textvariable=self.url_var, font=("Helvetica", 11, "underline"), fg="#64B5F6", bg="#000000", cursor="hand2")
        self.url_label.pack(pady=4)
        # The lambda discards Tk's event argument deliberately: binding
        # self.open_browser directly would pass the event as `automatic`, which
        # is truthy, and the click would suppress itself under OMNI_SMOKE.
        self.url_label.bind("<Button-1>", lambda _event: self.open_browser())

        controls = tk.Frame(frame, bg="#000000")
        controls.pack(pady=16)
        self.controls = controls
        button_frame = tk.Frame(controls, bg="#000000")
        button_frame.pack()
        self.button_frame = button_frame
        self.recovery_frame = tk.Frame(controls, bg="#000000")

        if sys.platform == "win32":
            self.open_button = tk.Button(button_frame, text="Open Browser", command=self.open_browser, width=14, bg="#2196F3", fg="white", font=("Helvetica", 10, "bold"), relief="flat", padx=10, pady=6, cursor="hand2", state="disabled")
        else:
            self.open_button = ttk.Button(button_frame, text="Open Browser", command=self.open_browser, width=14, state="disabled")
        self.open_button.grid(row=0, column=0, padx=6)

        if sys.platform == "win32":
            exit_button = tk.Button(button_frame, text="Exit", command=self.on_close, width=14, bg="#f44336", fg="white", font=("Helvetica", 10, "bold"), relief="flat", padx=10, pady=6, cursor="hand2")
        else:
            exit_button = ttk.Button(button_frame, text="Exit", command=self.on_close, width=14)
        exit_button.grid(row=0, column=1, padx=6)

        self._build_recovery_buttons()

        self.footer_note = tk.Label(frame, text="The server will run until you click Exit", font=("Helvetica", 9), fg="#999999", bg="#000000")
        self.footer_note.pack(pady=(12, 12))

        footer_path = resolve_asset("atlona.png")
        if footer_path:
            try:
                footer = Image.open(footer_path)
                width = 190
                height = int(footer.height * (width / footer.width))
                footer = footer.resize((width, height), Image.Resampling.LANCZOS)
                self.footer_photo = ImageTk.PhotoImage(footer)
                footer_label = tk.Label(frame, image=self.footer_photo, bg="#000000", bd=0, cursor="hand2")
                self.footer_label = footer_label
                footer_label.pack(pady=(0, 8))
                footer_label.bind("<Button-1>", lambda _event: webbrowser.open("https://www.hallresearch.com"))
            except Exception as exc:
                log_message(f"Could not load atlona.png: {exc}")

    def setup_tray_icon(self) -> None:
        if sys.platform == "darwin":
            log_message("Tray icon disabled on macOS; AppKit tray loops must run on the main thread.")
            return
        if pystray is None:
            log_message("Tray icon unavailable; pystray was not imported")
            return
        icon_path = resolve_asset("omnimatrix.ico")
        if not icon_path:
            log_message("Tray icon asset omnimatrix.ico not found")
            return
        try:
            icon_image = Image.open(icon_path)
            menu = pystray.Menu(
                pystray.MenuItem("Open Browser", lambda: self.root.after(0, self.open_browser)),  # explicit: never suppressed
                pystray.MenuItem("Show Window", lambda: self.root.after(0, self.show_window)),
                pystray.MenuItem("Exit", lambda: self.root.after(0, self.on_close)),
            )
            self.tray_icon = pystray.Icon("OmniSuite", icon_image, "OmniSuite", menu)
            threading.Thread(target=self.tray_icon.run, daemon=True).start()
        except Exception as exc:
            log_message(f"Could not create tray icon: {exc}")

    def show_window(self) -> None:
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def start_server(self) -> None:
        if self.fixed_mode:
            self.begin_attempt(self.port, "startup")
            return
        self.server_thread = threading.Thread(target=self.run_server, daemon=True)
        self.server_thread.start()
        self.wait_for_server()

    def run_server(self) -> None:
        try:
            os.environ["OMNI_HOST"] = self.host
            os.environ["OMNI_PORT"] = str(self.port)
            os.environ.setdefault("OMNI_DATA_DIR", str(DATA_DIR))
            os.environ.setdefault("OMNI_VERSION", self.version)

            sys.path.insert(0, str(EXE_DIR))
            sys.path.insert(0, str(BUNDLE_DIR))

            import OmniMatrix_upgrade_server_v7_6y as server  # noqa: N813

            self.server_module = server
            self.running = True
            logging.getLogger("werkzeug").setLevel(logging.ERROR)
            for candidate_port in range(self.port, self.port + 100):
                try:
                    self.port = candidate_port
                    os.environ["OMNI_PORT"] = str(self.port)
                    log_message(f"Starting server on {self.host}:{self.port}")
                    # The read-only startup refreshes begin here rather than at
                    # import, so simply importing the module never reaches the
                    # network.
                    if hasattr(server, "start_background_startup_tasks"):
                        server.start_background_startup_tasks()
                    # An unattended host should come back the way the
                    # administrator left it. This is the packaged application's
                    # startup path -- the module's own main() is not used here --
                    # so the restore has to be invoked from both or the
                    # behaviour would exist only when run from source.
                    # It restores nothing unless remote access was explicitly
                    # enabled and the saved address is present on this machine,
                    # and it never prevents the local application from starting.
                    if hasattr(server, "restore_remote_access"):
                        try:
                            server.restore_remote_access()
                        except Exception as exc:
                            log_message(
                                f"Remote access restore skipped: {type(exc).__name__}")
                    # Tagged rather than app.run: the authorization gate reads
                    # which listener accepted a connection, and this is the
                    # listener the packaged application serves from. The tag
                    # follows the bound address, so a launcher forced onto a
                    # routable host does not grant local trust.
                    import omni_remote
                    httpd = omni_remote.serve_tagged(
                        self.host, self.port, server.app, threaded=True)
                    # Recorded so Settings can say which port is serving and a
                    # restart can release it. Nothing is persisted: this is the
                    # automatic path and it never was.
                    self.httpd = httpd
                    self.supervisor.bound(self.port)
                    httpd.serve_forever()
                    return
                except OSError as exc:
                    # Windows reports WSAEADDRINUSE with its own wording, so a
                    # substring match never advanced to the next port there.
                    in_use = getattr(exc, "errno", None) in (
                        errno.EADDRINUSE, getattr(errno, "WSAEADDRINUSE", errno.EADDRINUSE))
                    if not in_use and "Address already in use" not in str(exc):
                        raise
                    log_message(f"Port {candidate_port} is in use, trying {candidate_port + 1}")
            raise RuntimeError(f"Unable to bind server on any port starting at {self.port}")
        except Exception as exc:
            log_message(f"Server error: {exc}")
            log_message(traceback.format_exc())
            self.running = False

    # ------------------------------------------------------------------
    # The local port: fixed mode, its recovery state and the restart
    # ------------------------------------------------------------------
    #
    # Everything below runs only for a fixed port or a restart. Automatic mode
    # never reaches it, so the existing start-up is exactly as it was.

    def _make_button(self, parent, text, command, bg):
        if sys.platform == "win32":
            return tk.Button(parent, text=text, command=command, width=22, bg=bg, fg="white", font=("Helvetica", 10, "bold"), relief="flat", padx=10, pady=6, cursor="hand2")
        return ttk.Button(parent, text=text, command=command, width=22)

    def _build_recovery_buttons(self) -> None:
        frame = self.recovery_frame
        self.retry_button = self._make_button(frame, "Retry", self.on_retry_clicked, "#2196F3")
        self.alternate_button = self._make_button(frame, "Start on Alternate Port", self.on_alternate_clicked, "#FB8C00")
        self.change_button = self._make_button(frame, "Change Fixed Port", self.on_change_clicked, "#607D8B")
        self.recovery_exit_button = self._make_button(frame, "Exit", self.on_close, "#f44336")
        self.retry_button.grid(row=0, column=0, padx=6, pady=3)
        self.alternate_button.grid(row=0, column=1, padx=6, pady=3)
        self.change_button.grid(row=1, column=0, padx=6, pady=3)
        self.recovery_exit_button.grid(row=1, column=1, padx=6, pady=3)

    def _set_detail(self, text: str, colour: str = "#FFB300") -> None:
        if text:
            self.detail_label.config(text=text, fg=colour)
            if not self.detail_label.winfo_manager():
                self.detail_label.pack(before=self.url_label, pady=2)
        elif self.detail_label.winfo_manager():
            self.detail_label.pack_forget()
            self.detail_label.config(text="")

    def _show_running_controls(self) -> None:
        """Back to the ordinary Open Browser / Exit row."""
        if self.recovery_frame.winfo_manager():
            self.recovery_frame.pack_forget()
        if not self.button_frame.winfo_manager():
            self.button_frame.pack()
        self.footer_note.config(text="The server will run until you click Exit")
        self._compact(False)
        if self.supervisor.temporary:
            configured = self.supervisor.configured_port
            self._set_detail(
                f"Running temporarily on TCP port {self.port}. "
                f"Configured fixed port: {configured}.")
        else:
            self._set_detail("")

    def _compact(self, on: bool) -> None:
        """Make room for the recovery buttons by setting the artwork aside.

        The window is a fixed size, and the recovery state needs a second row
        of buttons and a sentence the normal screen does not. The artwork
        returns, in its original place, when the server is running.
        """
        if on:
            for widget in (self.header_label, self.footer_label):
                if widget is not None and widget.winfo_manager():
                    widget.pack_forget()
            return
        if self.header_label is not None and not self.header_label.winfo_manager():
            self.header_label.pack(before=self.title_label, pady=(8, 8))
        if self.footer_label is not None and not self.footer_label.winfo_manager():
            self.footer_label.pack(pady=(0, 8))

    def conflict_text(self, failure, port, kind) -> str:
        configured = self.supervisor.configured_port
        if kind == "change":
            reason = (f"TCP port {port} is already in use." if failure.in_use
                      else f"TCP port {port} could not be opened.")
            return f"{reason} The configured fixed port ({configured}) was not changed."
        if kind == "alternate":
            return "No alternate port could be opened."
        if failure.in_use:
            return f"Configured TCP port {port} is already in use."
        return f"Configured TCP port {port} could not be opened ({failure.detail})."

    def show_conflict(self, failure, port, kind) -> None:
        """The recovery state. The launcher stays usable and nothing falls back."""
        self._attempting = False
        configured = self.supervisor.configured_port or port
        text = self.conflict_text(failure, port, kind)
        log_message(f"Server could not start: {text}")
        self.status_label.config(text="Server could not start", fg="#f44336")
        self._set_detail(text, "#FFB300")
        self.url_var.set("")
        self.open_button.config(state="disabled")
        self.retry_button.config(text=f"Retry {configured}", state="normal")
        for button in (self.alternate_button, self.change_button):
            button.config(state="normal")
        if self.button_frame.winfo_manager():
            self.button_frame.pack_forget()
        if not self.recovery_frame.winfo_manager():
            self.recovery_frame.pack()
        self.footer_note.config(text="OmniSuite is not serving until you choose an option above")
        self._compact(True)

    def on_start_failed(self, detail: str) -> None:
        self._attempting = False
        self.status_label.config(text="✗ Server Failed to Start", fg="#f44336")
        self._set_detail(detail, "#FFB300")
        self.open_button.config(state="disabled")

    def _show_attempt(self) -> None:
        self.status_label.config(text="Starting server...", fg="#4CAF50")
        for button in (self.retry_button, self.alternate_button, self.change_button):
            button.config(state="disabled")

    def on_retry_clicked(self) -> None:
        """Try the configured port again. No configuration changes."""
        port = self.supervisor.configured_port
        if port:
            self.begin_attempt(port, "retry")

    def on_alternate_clicked(self) -> None:
        """Explicit emergency: serve on a free port for this session only."""
        self.begin_attempt(None, "alternate")

    def on_change_clicked(self) -> None:
        self.open_change_dialog()

    def begin_attempt(self, port, kind) -> bool:
        """Start (or retry) the complete application on `port`.

        Single flight: a second click while one is running does nothing.
        """
        with self._attempt_lock:
            if self._attempting:
                return False
            self._attempting = True
        if kind != "startup":
            self._show_attempt()
        # A restart released its listener before this process existed; a
        # short grace covers the moment the operating system takes to let go.
        grace = omni_local_port.RESTART_BIND_GRACE if (
            kind == "startup" and os.environ.get("OMNI_RESTARTED")) else 0.0
        self.server_thread = threading.Thread(
            target=self._attempt_worker, args=(port, kind, grace), daemon=True)
        self.server_thread.start()
        return True

    def _load_server(self):
        if self.server_module is not None:
            return self.server_module
        os.environ["OMNI_HOST"] = self.host
        os.environ["OMNI_PORT"] = str(self.port or omni_local_port.DEFAULT_START_PORT)
        os.environ.setdefault("OMNI_DATA_DIR", str(DATA_DIR))
        os.environ.setdefault("OMNI_VERSION", self.version)
        if self.server_loader is not None:
            self.server_module = self.server_loader()
            return self.server_module
        sys.path.insert(0, str(EXE_DIR))
        sys.path.insert(0, str(BUNDLE_DIR))
        import OmniMatrix_upgrade_server_v7_6y as server  # noqa: N813
        logging.getLogger("werkzeug").setLevel(logging.ERROR)
        self.server_module = server
        return server

    def _alternate_candidates(self):
        """Ports for the session-only fallback, using the automatic search.

        The same free-port discovery the automatic mode uses, starting where it
        starts, never the configured port. Several are offered so a port lost to
        a race moves on rather than failing the recovery.
        """
        avoid = self.supervisor.configured_port
        first = self.find_available_port(omni_local_port.DEFAULT_START_PORT)
        seen = []
        for candidate in [first] + list(range(
                omni_local_port.DEFAULT_START_PORT,
                omni_local_port.DEFAULT_START_PORT + 100)):
            if candidate != avoid and candidate not in seen:
                seen.append(candidate)
        return seen

    def _attempt_worker(self, port, kind, grace) -> None:
        failure = None
        try:
            server = self._load_server()
            httpd = None
            if kind == "alternate":
                for candidate in self._alternate_candidates():
                    try:
                        httpd = omni_local_port.bind_listener(server.app, self.host, candidate)
                        port = candidate
                        break
                    except omni_local_port.PortUnavailable as exc:
                        failure = exc
            else:
                deadline = time.monotonic() + grace
                while True:
                    try:
                        httpd = omni_local_port.bind_listener(server.app, self.host, port)
                        break
                    except omni_local_port.PortUnavailable as exc:
                        failure = exc
                        if not exc.in_use or time.monotonic() >= deadline:
                            break
                        time.sleep(0.25)
            if httpd is None:
                shown = port if port else self.supervisor.configured_port
                self.supervisor.conflicted(shown or 0)
                self.root.after(0, lambda: self.show_conflict(failure, shown, kind))
                return

            # The bind is the proof. Only now does anything change.
            self.httpd = httpd
            self.port = port
            os.environ["OMNI_PORT"] = str(port)
            self.supervisor.bound(port, temporary=(kind == "alternate"))
            notice = ""
            if kind == "change":
                if not self.supervisor.persist_after_bind(port):
                    self.supervisor.temporary = True
                    notice = "The new port is serving, but the setting could not be saved."
            self.running = True
            self._start_services_once(server)
            url = f"http://{self.host}:{port}"
            self.root.after(0, lambda: self._after_attempt_ready(url, notice))
            httpd.serve_forever()
        except Exception as exc:
            log_message(f"Server error: {exc}")
            log_message(traceback.format_exc())
            self.running = False
            self.root.after(0, lambda: self.on_start_failed(
                "The server stopped unexpectedly. Check the launcher log."))

    def _after_attempt_ready(self, url, notice) -> None:
        self._attempting = False
        self.on_server_ready(url)
        if notice:
            self._set_detail(notice)

    def _start_services_once(self, server) -> None:
        """The start-up work the automatic path does, once, after the bind."""
        if self._services_started:
            return
        self._services_started = True
        if hasattr(server, "start_background_startup_tasks"):
            server.start_background_startup_tasks()
        if hasattr(server, "restore_remote_access"):
            try:
                server.restore_remote_access()
            except Exception as exc:
                log_message(f"Remote access restore skipped: {type(exc).__name__}")

    # ---- Change Fixed Port, from the launcher: no browser is needed
    def open_change_dialog(self):
        if self.change_dialog is not None:
            try:
                self.change_dialog.lift()
                return self.change_dialog
            except Exception:
                self.change_dialog = None
        dialog = tk.Toplevel(self.root)
        dialog.title("Change fixed TCP port")
        dialog.resizable(False, False)
        dialog.configure(bg="#111111", padx=18, pady=14)
        configured = self.supervisor.configured_port
        tk.Label(dialog, text="Change fixed TCP port", font=("Helvetica", 12, "bold"), fg="#ffffff", bg="#111111").pack(anchor="w")
        tk.Label(dialog, text=f"Configured port {configured} is unavailable.", fg="#cccccc", bg="#111111").pack(anchor="w", pady=(6, 8))
        tk.Label(dialog, text="New TCP port:", fg="#cccccc", bg="#111111").pack(anchor="w")
        entry = tk.Entry(dialog, width=12)
        entry.pack(anchor="w", pady=(2, 4))
        error = tk.Label(dialog, text="", fg="#ff8a80", bg="#111111", wraplength=320, justify="left")
        error.pack(anchor="w")
        row = tk.Frame(dialog, bg="#111111")
        row.pack(anchor="e", pady=(10, 0))
        cancel = tk.Button(row, text="Cancel", width=10, command=self.close_change_dialog)
        start = tk.Button(row, text="Start", width=10, command=self.submit_change_dialog)
        cancel.grid(row=0, column=0, padx=4)
        start.grid(row=0, column=1, padx=4)
        entry.bind("<Return>", lambda _e: self.submit_change_dialog())
        dialog.protocol("WM_DELETE_WINDOW", self.close_change_dialog)
        self.change_dialog = dialog
        self.change_entry, self.change_error = entry, error
        self.change_start_button, self.change_cancel_button = start, cancel
        try:
            # In front of the launcher rather than in the corner of the screen.
            dialog.update_idletasks()
            x = self.root.winfo_rootx() + max(0, (self.root.winfo_width() - dialog.winfo_reqwidth()) // 2)
            y = self.root.winfo_rooty() + max(0, (self.root.winfo_height() - dialog.winfo_reqheight()) // 2)
            dialog.geometry(f"+{x}+{y}")
        except Exception:
            pass
        try:
            dialog.transient(self.root)
            dialog.lift()
            dialog.focus_force()
            entry.focus_set()
        except Exception:
            pass
        return dialog

    def close_change_dialog(self) -> None:
        dialog, self.change_dialog = self.change_dialog, None
        if dialog is not None:
            try:
                dialog.destroy()
            except Exception:
                pass

    def submit_change_dialog(self) -> bool:
        """Validate, then try the new port. Nothing is saved here: the worker
        saves it only after the bind succeeds."""
        try:
            port = omni_local_port.parse_port(self.change_entry.get())
        except omni_local_port.PortError as exc:
            self.change_error.config(text=str(exc))
            return False
        if not self.begin_attempt(port, "change"):
            return False
        self.close_change_dialog()
        return True

    # ---- Save and Restart: a real restart
    def request_restart(self) -> None:
        """Called by the supervisor, from a request thread, after it has saved.

        Deferred a moment so the browser receives the answer that says what is
        about to happen. The work itself runs on the Tk thread.
        """
        self.root.after(700, self.restart_now)

    def release_for_restart(self) -> None:
        """Let go of everything a new process would need, in order.

        The listener first, so the new process cannot find its own port held by
        the process that started it; then the Remote Access listener, which the
        new process restores on its own; then the data folder.
        """
        httpd, self.httpd = self.httpd, None
        if httpd is not None:
            try:
                httpd.shutdown()
            except Exception as exc:
                log_message(f"Listener shutdown raised {type(exc).__name__}")
            try:
                httpd.server_close()
            except Exception as exc:
                log_message(f"Listener close raised {type(exc).__name__}")
        remote = getattr(self.server_module, "_remote_listener", None)
        if remote is not None:
            try:
                remote.stop()
            except Exception as exc:
                log_message(f"Remote listener stop raised {type(exc).__name__}")
        import omni_instance
        omni_instance.release(self.data_dir)

    def replacement_command(self):
        if IS_FROZEN:
            return [sys.executable]
        return [sys.executable, str(Path(__file__).resolve())]

    def spawn_replacement(self) -> bool:
        env = dict(os.environ)
        env["OMNI_RESTARTED"] = "1"
        # A one-file build reuses its parent's extracted files when it sees the
        # parent's variables; this one is about to remove them.
        env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
        env.pop("OMNI_PORT", None)
        kwargs = {}
        if sys.platform == "win32":
            kwargs["creationflags"] = (getattr(subprocess, "DETACHED_PROCESS", 0)
                                       | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
        try:
            subprocess.Popen(self.replacement_command(), env=env, cwd=str(EXE_DIR),
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, close_fds=True, **kwargs)
            return True
        except Exception as exc:
            log_message(f"Could not start the replacement process: {exc}")
            return False

    def restart_now(self) -> None:
        log_message("Restarting OmniSuite: releasing the listener before the new process starts")
        self.release_for_restart()
        if not self.spawn_replacement():
            self.status_label.config(text="✗ Restart failed - start OmniSuite again", fg="#f44336")
            self.url_var.set("")
            self.open_button.config(state="disabled")
            return
        self.running = False
        if self.tray_icon:
            try:
                self.tray_icon.stop()
            except Exception:
                pass
        self.root.destroy()
        raise SystemExit(0)


    def is_server_ready(self) -> bool:
        try:
            with socket.create_connection((self.host, self.port), timeout=1):
                return True
        except (socket.timeout, ConnectionRefusedError, OSError):
            return False

    def wait_for_server(self) -> None:
        def check() -> None:
            for _ in range(30):
                if self.is_server_ready():
                    self.root.after(0, lambda: self.on_server_ready(f"http://{self.host}:{self.port}"))
                    return
                time.sleep(1)
            self.root.after(0, self.on_server_failed)

        threading.Thread(target=check, daemon=True).start()

    def on_server_ready(self, url: str) -> None:
        log_message(f"Server ready at {url}")
        self._show_running_controls()
        self.status_label.config(text="✓ Server Running", fg="#4CAF50")
        self.url_var.set(url)
        self.open_button.config(state="normal")
        # The only automatic open. Everything else is a person clicking.
        self.open_browser(automatic=True)

    def on_server_failed(self) -> None:
        self.status_label.config(text="✗ Server Failed to Start", fg="#f44336")
        self.open_button.config(state="disabled")
        messagebox.showerror("Server Error", "Failed to start the server. Please check the launcher log.")

    def current_url(self) -> str:
        """The address this launcher is actually serving on.

        Built from the port the server bound, which is chosen at runtime and is
        not always 8080 -- so nothing here may assume one.
        """
        return f"http://{self.host}:{self.port}"

    def open_browser(self, automatic: bool = False) -> bool:
        """Open OmniSuite in the operator's default browser.

        The Open Browser button, the clickable URL and the tray menu item all
        call this with no argument; the server-ready handler calls it with
        automatic=True.

        OMNI_SMOKE suppresses only the AUTOMATIC open. A build runner must not
        have a browser window appear on its own, but a click is a person asking
        for one and is always honoured -- the guard used to sit in front of both,
        so with OMNI_SMOKE set the button silently did nothing.
        """
        url = self.current_url()
        if automatic and os.environ.get("OMNI_SMOKE"):
            log_message(f"OMNI_SMOKE set; not opening a browser automatically for {url}")
            return False
        return self.launch_browser(url)

    def launch_browser(self, url: str) -> bool:
        """Hand the URL to the OS default browser. True if it was accepted.

        webbrowser.open() returns False when it cannot launch anything and does
        NOT raise, so the result has to be checked: discarding it turns a failed
        launch into a button that appears to do nothing at all.

        new=2 asks for a new tab in an already-open window, which is what an
        operator expects when their browser is already running. No specific
        browser is named: this is the platform's own default-browser mechanism,
        which is why the same code serves Windows, macOS and Linux.
        """
        try:
            opened = bool(webbrowser.open(url, new=2))
        except Exception as exc:
            log_message(f"Browser launch raised {type(exc).__name__}: {exc}")
            opened = False
        if opened:
            log_message(f"Opened {url} in the default browser")
            self.step_aside()
        else:
            log_message(f"Could not open a browser for {url}")
            self.report_browser_failure(url)
        return opened

    def step_aside(self) -> None:
        """Get out from in front of the browser we just opened.

        Measured on this machine: the console sat at z-order 4 with the two
        OmniSuite browser windows at 7 and 8 -- in front of the thing it had
        just launched, with no topmost flag to explain it. Anything the
        browser then put up, a save-as window in particular, appeared behind
        this one, and the operator had to close a second OmniSuite window to
        find out that the button had worked.

        Lowered rather than hidden or minimised. The console is still there
        with the address on it if somebody goes looking, the tray menu still
        raises it, and nothing about the running server changes. Only when a
        browser actually opened -- if it did not, this window is the only
        place the address appears and must stay where it is.

        Deferred a moment: lowering before the browser's own window exists
        puts this one behind nothing at all.
        """
        try:
            self.root.after(1200, self.root.lower)
        except Exception as exc:
            log_message(f"Could not lower the launcher window: {exc}")

    def report_browser_failure(self, url: str) -> None:
        """Say so without interrupting; the server is still running.

        Reported in the launcher's own status line rather than a modal: the
        address is already on screen and can be copied, and a dialog would have
        to be dismissed before the operator could reach it.
        """
        try:
            self.status_label.config(
                text="Server running - could not open a browser; open the address below",
                fg="#FFB300")
        except Exception:
            pass

    def on_close(self) -> None:
        if messagebox.askokcancel("Exit OmniSuite", "Stop the server and exit the application?"):
            self.running = False
            if self.tray_icon:
                try:
                    self.tray_icon.stop()
                except Exception:
                    pass
            self.root.destroy()
            raise SystemExit(0)


def main() -> int:
    log_message(f"Launcher starting. Frozen={IS_FROZEN} ExeDir={EXE_DIR} BundleDir={BUNDLE_DIR}")
    log_message(f"Log file: {LOG_PATH}")
    log_message(f"Data dir: {DATA_DIR}")
    # One OmniSuite per data folder, decided before the server module is imported:
    # importing it already loads the accounts and settings, and a second copy that
    # got that far would be a second writer of them. It is the folder that is
    # owned, not the port, so a copy started on another port is refused too.
    import omni_instance
    try:
        # A process started by a restart waits briefly for its predecessor to
        # finish letting go of the folder; any other second copy is refused at
        # once, exactly as before.
        deadline = time.monotonic() + (15.0 if os.environ.get("OMNI_RESTARTED") else 0.0)
        while True:
            try:
                omni_instance.acquire(os.environ.get("OMNI_DATA_DIR") or DATA_DIR)
                break
            except omni_instance.AlreadyRunning:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.25)
    except omni_instance.AlreadyRunning as exc:
        log_message(str(exc))
        try:
            notice = tk.Tk()
            notice.withdraw()
            messagebox.showerror("OmniSuite is already running", str(exc))
            notice.destroy()
        except Exception:
            pass
        return 1
    root = tk.Tk()
    AppWindow(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
