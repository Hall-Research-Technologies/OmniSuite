"""One OmniSuite per data directory.

Two copies of the application pointed at the same data directory are two writers
of the same files. Every lock in the server is an in-process lock, so nothing
stopped it: a second copy on another port loaded the same accounts, cache and
settings and rewrote them whole, each from its own stale memory. A change made in
one (an account disabled, a permission removed) was overwritten by the other, and
a session revoked in one stayed open in the other.

The guard is an exclusive operating-system lock on a file in the data directory,
held for the life of the process.

  * It is about the directory, not the port. A second copy on a different port is
    exactly the case that matters.
  * The operating system releases it when the process ends, however it ends -- a
    crash, a kill, a power cut -- so there is no stale lock to detect or clean up.
    The file left behind is only the place the lock is taken; its contents are
    never trusted, only shown in the message.
  * It is taken before anything that reads or writes the data directory: before
    the server module is imported (which builds the account store), before the
    background tasks, and before a remote listener is restored.

Importing this module takes no lock. Only the entry points that run the
application call `acquire`, so tests and tools that import the server are
unaffected.
"""
from __future__ import annotations

import os
import sys
import threading
from pathlib import Path

LOCK_FILE = "omnisuite.lock"

#: Where the holder's process id is written. The byte the lock itself covers is
#: offset 0, and on Windows a locked byte cannot be read by anyone else, so the
#: note a refused second copy reads has to sit somewhere else in the file.
_NOTE_OFFSET = 16

_guard = threading.Lock()
_held: dict = {}


class AlreadyRunning(RuntimeError):
    """Another OmniSuite already owns this data directory."""

    def __init__(self, data_dir, pid=None):
        self.data_dir = str(data_dir)
        self.pid = pid
        who = f" (process {pid})" if pid else ""
        super().__init__(
            f"OmniSuite is already running{who} and is using {self.data_dir}. "
            "Close the other copy first: two copies sharing one data folder "
            "overwrite each other's accounts and settings.")


def _try_lock(handle) -> None:
    """Take the lock without waiting. Raises OSError when someone else holds it."""
    if sys.platform == "win32":
        import msvcrt
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock(handle) -> None:
    try:
        if sys.platform == "win32":
            import msvcrt
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass


def _holder_of(path: Path):
    """The process id the current holder wrote, or None. Informational only."""
    try:
        with open(path, "rb") as handle:
            handle.seek(_NOTE_OFFSET)
            text = handle.read(32).split(b"\n", 1)[0].strip()
        return int(text) if text.isdigit() else None
    except OSError:
        return None


def acquire(data_dir) -> Path:
    """Own `data_dir` for this process, or raise `AlreadyRunning`.

    Idempotent within a process: a second call for the same directory returns
    without taking anything, so an entry point that calls it and a library that
    also does cannot lock each other out.
    """
    directory = Path(data_dir).expanduser()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / LOCK_FILE
    key = os.path.normcase(os.path.abspath(path))
    with _guard:
        if key in _held:
            return path
        # "a+b" creates the file if it is missing and never truncates one that
        # another process is holding.
        handle = open(path, "a+b")
        try:
            _try_lock(handle)
        except OSError:
            handle.close()
            raise AlreadyRunning(directory, _holder_of(path)) from None
        try:
            handle.seek(_NOTE_OFFSET)
            handle.truncate(_NOTE_OFFSET)
            handle.write(f"{os.getpid()}\n".encode("ascii"))
            handle.flush()
        except OSError:
            pass                       # the lock is what matters; the note is a courtesy
        _held[key] = handle
    return path


def release(data_dir) -> None:
    """Give the directory up. For tests and orderly shutdown; the OS does it anyway."""
    key = os.path.normcase(os.path.abspath(Path(data_dir).expanduser() / LOCK_FILE))
    with _guard:
        handle = _held.pop(key, None)
    if handle is not None:
        _unlock(handle)
        handle.close()
