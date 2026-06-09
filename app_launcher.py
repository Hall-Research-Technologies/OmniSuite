#!/usr/bin/env python3
"""Launcher entrypoint for OmniSuite packaged apps."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _resolve_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def _resolve_version() -> str:
    version_file = _resolve_base_dir() / "VERSION"
    try:
        text = version_file.read_text(encoding="utf-8").strip()
        return text or "V0.0.0"
    except Exception:
        return "V0.0.0"


def main() -> int:
    base_dir = _resolve_base_dir()
    sys.path.insert(0, str(base_dir))

    os.environ.setdefault("OMNI_VERSION", _resolve_version())

    try:
        import OmniMatrix_upgrade_server_v7_6y as server  # noqa: N813
    except Exception as exc:
        print(f"Launcher import error: {exc}")
        return 1

    try:
        server.main()
    except KeyboardInterrupt:
        return 0
    except Exception as exc:
        print(f"Launcher runtime error: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
