# Development workflow

OmniSuite is maintained as two product lines. This file explains how they are
kept apart. You are on the **OmniSuite Plus** line. It carries everything developed since the published OmniSuite V1.1.0 and will have its own releases and maintenance lifecycle. Fixes to the published OmniSuite product are made on `release/omnisuite-1.1.x`, not here.

## The two product lines

**OmniSuite** is the published product. Its stable code is `main`, which is
exactly tag `V1.1.0`. Maintenance, bug fixes and future 1.1.x releases happen on
`release/omnisuite-1.1.x`, created from `V1.1.0`.

**OmniSuite Plus** is everything developed since V1.1.0 (remote access, accounts
and per-user routing, among others). It lives on `omnisuite-plus`, created from
`feature/remote-user-routing` at `3cd5742`, and will eventually have its own
releases and maintenance lifecycle. `feature/remote-user-routing` is retained as
a safety reference for now.

```
V1.1.0 (main) ──●── release/omnisuite-1.1.x        OmniSuite maintenance
               └──●──●── ... ── omnisuite-plus      OmniSuite Plus (52 commits at creation)
```

| Branch | Line | Rule |
|---|---|---|
| `main` | OmniSuite | Published stable release only. Advances by fast-forward to a published release tag. |
| `release/omnisuite-1.1.x` | OmniSuite | Fixes and 1.1.x releases. Branch fixes from here, merge them back here. |
| `omnisuite-plus` | OmniSuite Plus | Ongoing development. Features branch from and merge back to here. |
| `feature/*` | OmniSuite Plus | One feature each. `feature/remote-user-routing` is a safety reference. |
| `fix/*` | OmniSuite | Optional short-lived branches off `release/omnisuite-1.1.x`. |

There is no generic `develop` branch, and neither line is ever merged into the
other.

## Moving a change between lines

1. Make and validate the change on the line it was found on.
2. Decide separately whether the other line needs it. Plus has diverged
   substantially, so a fix may not apply, may apply differently, or may already be
   solved there.
3. If it does, `git cherry-pick -x <commit>` onto the other line, resolve conflicts
   against *that* line's code, and run that line's full test suite. The `-x` trailer
   records where it came from.
4. Never `git merge` one product line into the other, and never use a merge to
   "catch up".

## Worktrees

Each line has its own working directory so switching never disturbs the other:

```
git worktree add ../omniSuite-1.1.x  release/omnisuite-1.1.x
git worktree add ../omniSuite-plus   omnisuite-plus
```

A branch can be checked out in only one worktree at a time. Keep tests and
temporary files out of the other line's directory.

## Releases and tags

Each product has its own tag family, artifact prefix and workflow trigger. They are
defined once, in `omni_release.py`, and enforced by the workflow before anything is
built.

| | OmniSuite | OmniSuite Plus |
|---|---|---|
| Release branch | `release/omnisuite-1.1.x` | `omnisuite-plus` |
| Tag | `V1.1.1`, `V1.1.2`, ... | `plus-v1.2.0`, `plus-v1.2.1`, ... |
| Artifact prefix | `OmniSuite-` | `OmniSuite-Plus-` |
| Workflow trigger | tags `V*` / `v*`, push to `main` | tags `plus-*` |

A tag must be exactly this product's form. `v1.1.1`, `V1.1.1-rc1`, `plus-V1.2.0` and
the other product's tag are refused, not corrected.

To publish a release (with approval):

1. Finish on the release branch; `VERSION` holds the version being released.
2. Add `RELEASE_NOTES_<version>.md` (for example `RELEASE_NOTES_V1.2.0.md`). The
   workflow refuses a tag whose notes file does not exist.
3. Update the User Guide and `CHANGELOG.md`, and run `python run_tests.py`.
4. Rehearse with a **dispatch** of the workflow on that branch (see below).
5. Create the tag on the release-branch commit. The identity job refuses it unless the
   tag is this product's, agrees with `VERSION`, and the commit is on the release
   branch; then the four platforms test, build and smoke-test, and the publish job
   verifies every artifact against the product and version before releasing.
6. OmniSuite only: fast-forward `main` to the published tag.

Both lines publish to the same releases page. Do not mark one product's release as a
prerelease to hide it from the other: identity is the tag, and the updater already
ignores the other product's releases.

### Diagnostic builds

Run the workflow manually (Run workflow) on a branch and give `release_version` a
suffix, for example `V1.2.0-diag1`. A dispatch never publishes, whatever the version says.
A bare release version (`V1.2.0`) is refused, because the artifact would be named
exactly like a release. The version may contain letters, digits, `.`, `_` and `-` only
and must not name the product; the artifact prefix is added for you, giving for
example `OmniSuite-Plus-V1.2.0-diag1-macOS-arm64.zip`.

## Every build

- Run `python run_tests.py`. It must pass, including the hardware-isolation check.
- Review the User Guide (`ui/user-guide.html`). Update it in the same build if
  user-visible behaviour changed, or say "User Guide reviewed — no update required."
- Update `CHANGELOG.md` for the line you are on. Each line keeps its own changelog.
- Release rules are described in [ARCHITECTURE.md](ARCHITECTURE.md) and tested as described in
  [TESTING.md](TESTING.md).
