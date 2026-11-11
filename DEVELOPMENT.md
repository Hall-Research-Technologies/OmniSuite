# Development workflow

## Branches

| Branch | Rule |
|---|---|
| `omnisuite-unified` | The single OmniSuite development line, and the branch releases are tagged on. |
| `main` | The published release (V1.1.0 until the consolidated product is published). Not moved without approval. |
| `release/omnisuite-1.1.x` | Maintenance of the published V1.1.0, only if a V1.1.x fix is needed. |
| `feature/*` | One feature or fix each, branched from `omnisuite-unified`. |

Do not rename, delete, reset or force-push `main`, `V1.1.0` or a release branch.

Publishing source is a separate decision from building it, because a branch's history is part of what it
publishes. Do not push a development branch to the public repository; a release is published as a reviewed
snapshot commit.

An optional separate working directory per branch keeps switching from disturbing work in progress:

```
git worktree add ../omniSuite-feature <branch>
```

Runtime content (`firmware/`, `dist/`, `build/`, `release/`, virtual environments, configuration with
credentials) is never source: it is ignored, and a test and the release checks keep it that way.

## Dependencies

`requirements-lock.txt` is the exact, resolved dependency set. Builds and CI install from it, with a
pinned PyInstaller, and the supported runtime is the pinned Python 3.12 stack. `cryptography` is held
to an older release on Intel macOS only, because later releases have no prebuilt Intel wheel. The lock
is not hash-pinned. The interpreter is not pinned in CI, which uses the current Python 3.12 the runner
provides, so an artifact built in CI embeds whatever 3.12 patch release that is.

The Intel macOS pin, `cryptography` 48.0.1, has two High advisories (PKCS#7 decryption and the x509
path-building verifier). OmniSuite uses neither: it imports only AES-GCM for backups and the certificate
builder, hashes, serialization and RSA key generation for its own certificate. Revisit the pin as soon as
a later release has an Intel macOS wheel. The lock was resolved on Windows, so the Linux (`python-xlib`) and
macOS (`pyobjc`) requirements of `pystray`, and PyInstaller's own dependencies, are resolved by pip at build
time and are not pinned.

Change a pin only with a reason, then run `python run_tests.py` and the security mutation check
(see [TESTING.md](TESTING.md)).

## Releases and tags

Release identity is defined once, in `omni_release.py`, and enforced by the workflow before
anything is built.

| | |
|---|---|
| Release branch | `omnisuite-unified` |
| Tag | `V1.2.0`, `V1.2.1`, ... |
| Artifact prefix | `OmniSuite-` |
| Workflow trigger | tags `V*` / `v*`, or a manual dispatch (a diagnostic build, which never publishes); no push starts a build |
| Release repository | `release_repo` in `omni_release.py`, which is not the repository the workflow runs in |

A tag must be exactly `V<number>.<number>[.<number>]`. `v1.2.0` and `V1.2.0-rc1` are
refused, not corrected.

To publish a release (with approval):

1. Finish on the release branch. `VERSION` holds the version being released, and it is the one
   place a version is typed: the application, the launcher, Settings, the User Guide, the
   artifact names and the Windows file properties all read it. Do not type a version into a page.
2. Add `RELEASE_NOTES_<version>.md` (for example `RELEASE_NOTES_V1.2.0.md`). The workflow
   refuses a tag whose notes file does not exist.
3. Update the User Guide and `CHANGELOG.md`, and run `python run_tests.py`. If the release
   includes macOS builds, the release notes link to [docs/INSTALL_MACOS.md](docs/INSTALL_MACOS.md)
   and say that macOS will ask for approval under Privacy & Security -> Open Anyway.
4. Rehearse with a **dispatch** of the workflow on the release branch (see below).
5. Create the tag on the release-branch commit. The identity job refuses it unless the tag is
   this product's, agrees with `VERSION`, and the commit is on the release branch; then the
   four platforms test, build, inspect (`tools/inspect_release.py`) and smoke-test, and the publish job
   verifies every artifact against the product and version before creating the release.
   **The release is created as a draft.** Nothing is public, and the updater cannot see a draft.
   Download each archive from the draft, compare it with `SHA256SUMS.txt`, run the manual spot-check
   on those exact files, and only then publish the draft by hand in the GitHub interface.
6. Fast-forward `main` to the published tag.

The public repository receives a release as one reviewed snapshot commit on top of the previously
published `main`, never as the development history. `tools/pre_push_guard.py --install` installs the
pre-push guard that refuses a push carrying private-product names, branches, artefacts or history to the
public repository; it has no bypass.

Installed V1.1.0 applications read the release repository's "latest" release. Keep releasing there,
with the same tag form and artifact names, so they keep being offered updates.

### Where workflows run and what publishes

- Workflows run only in the repository named by the repository variable `OMNI_CI_REPOSITORY` (the
  repository that holds the release workflow). In any other repository the identity job is skipped and every
  later job with it. An unset variable skips everything.
- Publishing goes to the release repository, with a dedicated token (`RELEASE_PUBLISH_TOKEN`). A run
  without the token stops before publishing; nothing publishes by default.
- The publish job collects only this product's artifacts and verifies every file against the product
  and version first. It runs only for an identity-verified tag.
- A release candidate is built the same way as a release: a dispatch with a suffixed name such as
  `V1.2.0-rc1` produces the four artifacts and publishes nothing.

### Diagnostic builds

Run the workflow manually (Run workflow) on a branch and give `release_version` a suffix, for
example `V1.2.0-diag1`. A dispatch never publishes, whatever the version says. A bare release
version (`V1.2.0`) is refused, because the artifact would be named exactly like a release. The
version may contain letters, digits, `.`, `_` and `-` only.

## Third-party licences

The licence texts of everything the package embeds live in `third_party_licenses/`, are bundled into
the application and are placed beside it in every archive with `LICENSE` and
`THIRD_PARTY_NOTICES.md`. When `requirements-lock.txt` changes:

1. Install the lock into a clean Python 3.12 environment (with the pinned PyInstaller).
2. Run `python tools/collect_licenses.py` there. It copies each component's licence files byte for
   byte; a component with none is reported rather than guessed.
3. Update the version table in `THIRD_PARTY_NOTICES.md`.
4. Run `python run_tests.py`. `tests/test_third_party_notices.py` fails if a locked distribution is
   neither shipped nor build-only, if the notice and the lock disagree, or if a licence text is missing.

`tools/collect_licenses.py` is the one list of what counts as shipped. Its `SHIPPED` set was
established from the PyInstaller table of contents of a packaged build, not from the lock alone.

## Every build

- Run `python run_tests.py`. It must pass, including the hardware-isolation check.
- Review the User Guide (`ui/user-guide.html`). Update it in the same build if user-visible
  behaviour changed, or say "User Guide reviewed - no update required."
- Update `CHANGELOG.md`, and `RELEASE_NOTES_<version>.md` when the build is a release candidate.
- Public documents describe what ships. When a policy changes (a default transport, a limit, a
  retention period), change the operator-facing text in the same build.
- Release rules are described in [ARCHITECTURE.md](ARCHITECTURE.md) and tested as described in
  [TESTING.md](TESTING.md).

## Public materials

The README, changelog, release notes, installer documentation, User Guide and workflow text are
customer-facing. They describe OmniSuite only, and `tests/test_release_compatibility.py` fails
if they stop doing so.
