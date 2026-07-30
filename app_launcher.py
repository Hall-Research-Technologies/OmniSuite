#!/usr/bin/env python3
"""Standalone GUI launcher for OmniSuite."""

from __future__ import annotations

import logging
import os
import socket
import sys
import threading
import time
import traceback
import webbrowser
from pathlib import Path

try:
    import tkinter as tk
    from tkinter import messagebox
    from PIL import Image, ImageTk
    import pystray
except ImportError as exc:
    print(f"Launcher dependency error: {exc}")
    raise


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


LOG_PATH = _get_log_path()


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
    def __init__(self, root: tk.Tk, host: str = "127.0.0.1", port: int = 8080):
        self.root = root
        self.host = host
        self.port = self.find_available_port(port)
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

        self.root.title(f"OmniSuite {self.version}")
        self.root.geometry(f"{self.window_width}x{self.window_height}")
        self.root.resizable(False, False)
        self._apply_window_icon()
        self._center_window()
        self.create_widgets()
        self.setup_tray_icon()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.start_server()

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
                tk.Label(frame, image=self.header_photo, bg="#000000", bd=0).pack(pady=(8, 8))
            except Exception as exc:
                log_message(f"Could not load hallway.png: {exc}")

        tk.Label(frame, text="OmniSuite", font=("Helvetica", 20, "bold"), fg="#ffffff", bg="#000000").pack(pady=(2, 4))
        tk.Label(frame, text=f"OmniSuite {self.version}", font=("Helvetica", 9), fg="#888888", bg="#000000").pack(pady=(0, 10))

        self.status_label = tk.Label(frame, text="Starting server...", font=("Helvetica", 12), fg="#4CAF50", bg="#000000")
        self.status_label.pack(pady=6)

        self.url_var = tk.StringVar(value="")
        self.url_label = tk.Label(frame, textvariable=self.url_var, font=("Helvetica", 11, "underline"), fg="#64B5F6", bg="#000000", cursor="hand2")
        self.url_label.pack(pady=4)
        self.url_label.bind("<Button-1>", lambda _event: self.open_browser())

        button_frame = tk.Frame(frame, bg="#000000")
        button_frame.pack(pady=16)

        self.open_button = tk.Button(button_frame, text="Open Browser", command=self.open_browser, width=14, bg="#2196F3", fg="white", font=("Helvetica", 10, "bold"), relief="flat", padx=10, pady=6, cursor="hand2", state="disabled")
        self.open_button.grid(row=0, column=0, padx=6)

        tk.Button(button_frame, text="Exit", command=self.on_close, width=14, bg="#f44336", fg="white", font=("Helvetica", 10, "bold"), relief="flat", padx=10, pady=6, cursor="hand2").grid(row=0, column=1, padx=6)

        tk.Label(frame, text="The server will run until you click Exit", font=("Helvetica", 9), fg="#999999", bg="#000000").pack(pady=(12, 12))

        footer_path = resolve_asset("atlona.png")
        if footer_path:
            try:
                footer = Image.open(footer_path)
                width = 190
                height = int(footer.height * (width / footer.width))
                footer = footer.resize((width, height), Image.Resampling.LANCZOS)
                self.footer_photo = ImageTk.PhotoImage(footer)
                footer_label = tk.Label(frame, image=self.footer_photo, bg="#000000", bd=0, cursor="hand2")
                footer_label.pack(pady=(0, 8))
                footer_label.bind("<Button-1>", lambda _event: webbrowser.open("https://www.hallresearch.com"))
            except Exception as exc:
                log_message(f"Could not load atlona.png: {exc}")

    def setup_tray_icon(self) -> None:
        icon_path = resolve_asset("omnimatrix.ico")
        if not icon_path:
            log_message("Tray icon asset omnimatrix.ico not found")
            return
        try:
            icon_image = Image.open(icon_path)
            menu = pystray.Menu(
                pystray.MenuItem("Open Browser", lambda: self.root.after(0, self.open_browser)),
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
        self.server_thread = threading.Thread(target=self.run_server, daemon=True)
        self.server_thread.start()
        self.wait_for_server()

    def run_server(self) -> None:
        try:
            os.environ["OMNI_HOST"] = self.host
            os.environ["OMNI_PORT"] = str(self.port)
            os.environ.setdefault("OMNI_DATA_DIR", str(EXE_DIR))
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
                    server.app.run(host=self.host, port=self.port, debug=False, use_reloader=False, threaded=True)
                    return
                except OSError as exc:
                    if "Address already in use" not in str(exc):
                        raise
                    log_message(f"Port {candidate_port} is in use, trying {candidate_port + 1}")
            raise RuntimeError(f"Unable to bind server on any port starting at {self.port}")
        except Exception as exc:
            log_message(f"Server error: {exc}")
            log_message(traceback.format_exc())
            self.running = False

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
        self.status_label.config(text="✓ Server Running", fg="#4CAF50")
        self.url_var.set(url)
        self.open_button.config(state="normal")
        self.open_browser()

    def on_server_failed(self) -> None:
        self.status_label.config(text="✗ Server Failed to Start", fg="#f44336")
        self.open_button.config(state="disabled")
        messagebox.showerror("Server Error", "Failed to start the server. Please check the launcher log.")

    def open_browser(self) -> None:
        url = f"http://{self.host}:{self.port}"
        try:
            webbrowser.open(url)
        except Exception as exc:
            messagebox.showerror("Browser Error", f"Failed to open browser: {exc}")

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
    root = tk.Tk()
    AppWindow(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
