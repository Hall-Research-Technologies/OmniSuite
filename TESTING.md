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
| `tests/test_usb_integration.py` | Discovery, USB, routing, and the long-standing update-check tests. |
| `tests/test_multiview.py` | Multiview planning, transactions, groups and the display output. |
| `tests/test_omni_usb_extender.py` | The standalone USB extender protocol, parsers and captured-hardware fixtures. |
| `tests/test_remote_access.py`, `test_remote_persistence.py`, `test_remote_browser_flow.py` | Remote Access, HTTPS and certificates, restart behaviour, and the sign-in and page flows. |
| `tests/test_remote_authorization.py`, `test_user_routing.py`, `test_user_operations.py`, `test_user_projection.py` | What each account may do and see, enforced per operation, and the restricted User screen's information boundary. |
| `tests/test_direct_access.py` | Direct Access links. |
| `tests/test_account_audit.py`, `test_login_hardening.py`, `test_hardening.py`, `test_security_review.py`, `test_lock_revalidation.py` | Account audit, sign-in hardening, route classification and the security gate, locking and re-checked authorisation. |
| `tests/test_backup.py` | Encrypted backup and restore. |
| `tests/test_local_port.py`, `test_local_port_launcher.py`, `test_local_port_ui.py` | The optional fixed local port: validation, the atomic and quarantining settings file, exact binding and conflicts on real loopback sockets, the supervisor's persist-only-after-bind rule, the local-only endpoints, and the launcher's recovery state driven by `invoke()` under a real withdrawn Tk root in a subprocess. |
| `tests/test_certificate_identity.py`, `test_certificate_address.py`, `test_remote_http_default.py`, `test_remote_certificate_choice.py` | The generated certificate's identity (machine name, organisation, unit, names it is valid for, three calendar years including the 29 February case), existing certificates left unchanged, the first-time HTTP default with its explicit acknowledgement, and the supplied-certificate choice. |
| `tests/test_display_groups.py`, `test_display_group_integrity.py`, `test_user_matrix.py`, `test_multiview_group_identity.py`, `test_inventory_identity_ingest.py` | Display Groups (members by hardware address, one route per display, ambiguous records refused), the User Matrix and who may use which routing interface, and inventory identity. |
| `tests/test_restore_semantics.py`, `test_restore_sessions.py`, `test_restore_atomicity.py`, `test_restore_failures.py`, `test_backup_kdf_bounds.py` | Authoritative restore, which sessions survive it, atomicity and failure injection, and bounded key derivation. |
| `tests/test_firmware_status.py`, `tests/firmware_status_test.js` | Firmware availability: cadence, the last good answer kept, version comparison, and the page that shows it. |
| `tests/test_third_party_notices.py` | The notice file lists every shipped distribution, `third_party_licenses/` holds each licence text, the build bundles the folder and places the same files beside the application, and the Windows version resource. |
| `tests/test_route_diag.py`, `test_attempt_log_bound.py` | The opt-in route diagnostics (off means nothing is wrapped; the file is bounded) and the bound on the read-attempt table. |
| `tests/test_certificate_state.py` | The certificate description the server reports (generated, supplied, missing, expired, unreadable, wrong network, key mismatch), run through the wording code lifted from `ui/settings.js`. |
| `tests/test_device_aliases.py`, `test_device_metadata.py`, `test_state_cache.py`, `test_adapters.py`, `test_clear_protection.py`, `test_certificate_address.py` | Friendly names, device metadata, shared state, adapter selection, protected clearing, certificate addresses. |
| `tests/test_release_isolation.py`, `test_release_compatibility.py`, `test_release_distribution.py` | Release identity, the updater and installed-V1.1.0 compatibility, the workflow guard and publishing. |
| `tests/test_scan_diagnostics.py`, `test_macos_local_network.py`, `test_macos_first_launch.py` | Scan failure classification, macOS Local Network and first launch. |
| `tests/test_fence.py` | The fence itself, including from a fresh interpreter. |

## Local port and certificate wording mutations

`tools/mutations_local_port.py` holds the mutations for the local port and the certificate wording
(ids beginning `localport.` and `certificate.`), appended to the main harness:

    python tools/mutate_security.py localport. certificate.

The launcher suite starts real subprocesses with Tk; it is skipped, loudly, only where Tk cannot
initialise, and then the recovery state has not been exercised.

## Security mutation check

`tools/mutate_security.py` breaks each security predicate on purpose -- listener trust, route
classification, the session requirement, CSRF, role, source, destination, expiry, the plain-HTTP
acknowledgement, the restore and Clear Units locks, and others -- and requires the suites that guard
it to fail. A survivor is a gap in the tests, a badly aimed mutation, or a redundant control; establish
which before dismissing it.

    python tools/mutate_security.py                 # every mutation
    python tools/mutate_security.py scangen http.   # only ids containing these substrings

A mutation reported as `ANCHOR x0` matched nothing: the code it targets has moved, so the mutation is
stale and must be repointed. It is not evidence that the tests are weak. `OMNI_MUTATE_TIMEOUT` raises
the per-run timeout (seconds, default 900) for a loaded machine. The harness restores every file in a
`finally` block, and repairs a file left mutated by an interrupted run at its next start.

The supported test environment is the pinned Python 3.12 stack in `requirements-lock.txt`, with
Node 20 for the page tests. CI runs the whole suite on Windows x86-64, Ubuntu x86-64, macOS arm64 and
macOS x86-64, with a floating Python 3.12 patch release. The lock is installed without PyInstaller;
tests therefore never import it (the Windows version file is written as text for that reason).

## Release identity and compatibility tests

`tests/test_release_isolation.py` runs the identity rules against OmniSuite and against a clearly
synthetic second product, registered only for the duration of that module. The synthetic product
deliberately shares the `OmniSuite-` artifact prefix, because an artifact family that merely starts
the same way is the real hazard exact-name matching exists for. It covers: tag and artifact parsing
(including near-miss tags that must be rejected); selecting the newest release out of a mixed feed;
drafts and prereleases; mixed-asset releases; malformed releases and feeds; missing and wrong-version
assets; version comparison (1.0.10 against 1.0.9, diagnostic builds, builds ahead of the feed); every
refusal in `tools/release_identity.py`, including through its real command line and against a
throwaway git repository for branch ancestry; and the workflow file's structure with comments removed.

`tests/test_release_compatibility.py` runs the **V1.1.0 update check, frozen verbatim** in
`tests/fixtures/legacy_update_check_v1_1_0.py`, against the artifacts the build produces. Installed
V1.1.0 applications cannot be changed, so this is the test that stops a future release from breaking
them. It also covers the first-run notice and its revisions, the macOS bundle identity, and a fence
that fails if customer-facing files name anything other than OmniSuite.

The workflow tests prove the text, not the behaviour. The behaviour is the guard's, tested directly,
and the workflow is exercised end to end by a dispatch run, which cannot publish.

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

`tests/scan_notice_test.js` runs the real `ui/scan-notice.js` against a DOM stub that has no
`innerHTML`, so a regression to markup injection throws. It pins that the notice appears
for a Local Network verdict and nothing else, that Retry Scan runs one scan, and that
Open System Settings is offered only for a Mac server viewed from that Mac.
`tests/test_macos_first_launch.py` holds the first-launch readme inside the zip, the
display name, the product name in the blocked-scan answer, and the page wiring.

## Verifying a release without publishing it

1. `python run_tests.py` on the release branch.
2. Dispatch the workflow on that branch with a suffixed `release_version` such as
   `V1.2.0-diag1`. It runs the identity job, all four test jobs and all four builds with
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
* A mutation harness run owns the source tree while it runs. Never start a second one,
  and never edit a mutated file meanwhile: the second run "repairs" the first run's
  receipt and the first run's restore then overwrites the edit. A test that loops on a
  constant the mutation changes must use a literal count, or the mutation hangs it.

## Not established

Two long-run observations are open, and no test claims either way. Median local route latency rose
with process age in one soak; it has not been reproduced as a defect. A long-running process grows by
about 2.5 MB per hour after a forced collection, for a reason not yet identified. Use the route
diagnostics below, a forced collection through the debugging protocol, and a soak long enough to see a
trend; do not report a heap trend from `performance.memory` without collecting first.

## Route timing diagnostics

`OMNI_ROUTE_DIAG=1` in the environment the application starts with appends one JSON
line per local route call to `route_diag.jsonl` in the data directory (milliseconds,
status, live thread count, call number, and a sampled count of tracked objects). It is
off by default, silent, bounded (the file is moved aside once past 4 MB) and wraps
nothing when off. Use it to see whether latency moves with process age before arguing
about why.
