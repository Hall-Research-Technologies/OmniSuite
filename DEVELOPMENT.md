# Development workflow

## Branches

| Branch | Rule |
|---|---|
| `main` | The published stable release only. Advances by fast-forward to a published release tag. |
| `release/omnisuite-1.1.x` | Fixes and 1.1.x releases. Branch fixes from here, merge them back here. |
| `feature/*` | One feature or fix each, branched from the release branch. |

`release/omnisuite-1.1.x` was created from tag `V1.1.0`. Do not rename, delete, reset or
force-push `main`, `V1.1.0` or the release branch.

An optional separate working directory per branch keeps switching from disturbing work in
progress:

```
git worktree add ../omniSuite-1.1.x release/omnisuite-1.1.x
```

## Releases and tags

Release identity is defined once, in `omni_release.py`, and enforced by the workflow before
anything is built.

| | |
|---|---|
| Release branch | `release/omnisuite-1.1.x` |
| Tag | `V1.1.1`, `V1.1.2`, ... |
| Artifact prefix | `OmniSuite-` |
| Workflow trigger | tags `V*` / `v*`, push to `main` |

A tag must be exactly `V<number>.<number>[.<number>]`. `v1.1.1` and `V1.1.1-rc1` are
refused, not corrected.

To publish a release (with approval):

1. Finish on the release branch. `VERSION` holds the version being released.
2. Add `RELEASE_NOTES_<version>.md` (for example `RELEASE_NOTES_V1.1.1.md`). The workflow
   refuses a tag whose notes file does not exist.
3. Update the User Guide and `CHANGELOG.md`, and run `python run_tests.py`. If the release
   includes macOS builds, the release notes link to [docs/INSTALL_MACOS.md](docs/INSTALL_MACOS.md)
   and say that macOS will ask for approval under Privacy & Security -> Open Anyway.
4. Rehearse with a **dispatch** of the workflow on the release branch (see below).
5. Create the tag on the release-branch commit. The identity job refuses it unless the tag is
   this product's, agrees with `VERSION`, and the commit is on the release branch; then the
   four platforms test, build and smoke-test, and the publish job verifies every artifact
   against the product and version before releasing.
6. Fast-forward `main` to the published tag.

Installed V1.1.0 applications read this repository's "latest" release. Keep releasing here, with
the same tag form and artifact names, so they keep being offered updates.

### Diagnostic builds

Run the workflow manually (Run workflow) on a branch and give `release_version` a suffix, for
example `V1.1.1-diag1`. A dispatch never publishes, whatever the version says. A bare release
version (`V1.1.1`) is refused, because the artifact would be named exactly like a release. The
version may contain letters, digits, `.`, `_` and `-` only.

## Every build

- Run `python run_tests.py`. It must pass, including the hardware-isolation check.
- Review the User Guide (`ui/user-guide.html`). Update it in the same build if user-visible
  behaviour changed, or say "User Guide reviewed - no update required."
- Update `CHANGELOG.md`.
- Release rules are described in [ARCHITECTURE.md](ARCHITECTURE.md) and tested as described in
  [TESTING.md](TESTING.md).

## Public materials

The README, changelog, release notes, installer documentation, User Guide and workflow text are
customer-facing. They describe OmniSuite only, and `tests/test_release_compatibility.py` fails
if they stop doing so.
