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

# The firmware availability check reads hallresearch.com. No test may reach it,
# so the application is told not to arm it at all -- rather than armed and then
# blocked by the fence, which would still be an attempt.
os.environ["OMNI_FIRMWARE_CHECK"] = "0"

_fence.install()

# The adapter list is remembered for a few seconds in production. In a suite
# that would answer one test with another test's adapters, so it is off here;
# tests of the cache itself turn it on for their own span and put this back.
import omni_adapters  # noqa: E402  (after the fence, like every other import)

omni_adapters.CACHE_TTL = 0.0
