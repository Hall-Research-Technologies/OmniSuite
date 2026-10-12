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

- OmniSuite releases are tagged `V1.1.x` on `release/omnisuite-1.1.x`, and `main`
  is then fast-forwarded to the published tag.
- **Tag naming for OmniSuite Plus is not decided.** The release workflow
  (`.github/workflows/build-cross-platform.yml`) publishes on any tag beginning
  `V` or `v`, and the updater matches downloads by filename. Decide the Plus tag
  and artifact naming, and pin it with a test, before the first Plus tag exists.
- The workflow's push trigger is `main` and tags. A branch build, including a
  diagnostic build, is started with **Run workflow** (`workflow_dispatch`) on that
  branch with a `release_version` such as `V1.1.1-diag1`. Dispatch builds produce
  downloadable run artifacts only; nothing is published without a tag.
- Do not publish a release until its fix has been validated on real hardware and
  approved.

## Every build

- Run `python run_tests.py`. It must pass, including the hardware-isolation check.
- Review the User Guide (`ui/user-guide.html`). Update it in the same build if
  user-visible behaviour changed, or say "User Guide reviewed — no update required."
- Update `CHANGELOG.md` for the line you are on. Each line keeps its own changelog.
