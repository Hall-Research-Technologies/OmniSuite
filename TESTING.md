# Testing

Run the whole suite, Python and JavaScript, with the hardware fence and the
isolation report:

    python run_tests.py

It exits non-zero if any suite fails **or** any test opened a real socket. The
same command runs on Windows, Ubuntu, macOS arm64 and macOS x86-64 in CI, and a
build that cannot pass its own platform's suite does not produce a download.
Narrower runs: `python run_tests.py --python-only`, `--js-only`, or a single module
with `python -m unittest tests.test_release_isolation`.

## The hardware fence

`tests/_fence.py` is installed when the `tests` package is imported, so it holds
however the suite is entered. It refuses WebSocket, TCP and UDP to anything that is
not loopback. Fixtures use RFC 5737 documentation addresses (192.0.2.0/24,
198.51.100.0/24, 203.0.113.0/24); a test that addresses anything else is reported by
the isolation check as a failure.

## Suites worth knowing

| Suite | Covers |
|---|---|
| `tests/test_release_isolation.py` | Release identity, the updater for both products, the workflow guard, workflow structure, artifact naming. Identical on both lines. |
| `tests/test_scan_diagnostics.py` | Scan failure classification, invalid targets, scan context. |
| `tests/test_usb_integration.py` | Discovery, USB, routing, and the long-standing update-check tests. |
| `tests/test_fence.py` | The fence itself, including from a fresh interpreter. |

## Release isolation tests

They run **both** products' identities on whichever line they are checked out on, so
cross-product rejection is exercised everywhere. They cover: tag and artifact
parsing (including near-miss tags that must be rejected); selecting each product's
newest release out of one mixed feed; drafts and prereleases; mixed-asset
releases; malformed releases and feeds; missing and wrong-version assets; version
comparison (1.0.10 against 1.0.9, diagnostic builds, builds ahead of the feed);
every refusal in `tools/release_identity.py`, including through its real command
line; and the workflow file's structure with comments removed.

The workflow tests prove the text, not the behaviour. The behaviour is the guard's,
tested directly, and the workflow is exercised end to end by a dispatch run, which
cannot publish.

## Mutation check

`tools/mutate_release_isolation.py` breaks each release rule on purpose -- the tag
anchor, product ownership, ancestry, the VERSION check, the notes check, dispatch
safety, the publish condition, the artifact pattern, the latest-release endpoint --
and requires the suite to fail every time. A survivor is a gap in the tests, a
badly aimed mutation, or a genuinely redundant rule; establish which before
dismissing it. It restores every file in a `finally` block and translates its
anchors to each file's own line endings.

    python tools/mutate_release_isolation.py .

## macOS Local Network checks

`tests/test_macos_local_network.py` covers the usage-description edit and the signing
commands the build issues (recorded, never run), the smoke check's reading of a bundle,
the refusal wording on each platform, and allow / deny / revoke transitions. **macOS
itself is simulated** there, by replacing the connection primitive; the real behaviour
is validated on a Mac using the checklist in
[docs/MACOS_LOCAL_NETWORK.md](docs/MACOS_LOCAL_NETWORK.md). `tools/mutate_local_network.py`
breaks each rule on purpose and requires the suite to notice.

## Verifying a release without publishing it

1. `python run_tests.py` on the release branch.
2. Dispatch the workflow on that branch with a suffixed `release_version` such as
   `V1.1.1-diag1`. It runs the identity job, all four test jobs and all four builds with
   smoke tests, and publishes nothing.
3. Check the artifact names carry `OmniSuite-`.
4. Only then create the tag, with approval.

## Testing rules that came from real failures

* A test that replaces a module-level name restores it, and never patches a method on
  its class.
* A fixture handed to server code is deep-copied; background tasks mutate cached
  dicts in place.
* A test owns the background work it starts and waits for it before it ends.
* Assertions sliced from source strip comments first, and bound the slice at the end
  of the thing they describe.
* Make the defect a test names and watch the test fail before trusting it.
