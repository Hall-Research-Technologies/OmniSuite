"""The OmniSuite automated test package.

Importing this package installs the hardware fence and points the application
at a scratch data directory. Both happen here, at package import, so that any
runner -- `run_tests.py`, `python -m unittest`, discovery, an IDE, a mutation
harness -- gets them without having to know they exist.
"""
import os
import tempfile

from . import _fence

# Before any test imports the application: a test must never be able to read or
# write the operator's real device cache, and the application resolves this once
# at import.
os.environ.setdefault("OMNI_DATA_DIR",
                      tempfile.mkdtemp(prefix="omnisuite-tests-"))

_fence.install()
