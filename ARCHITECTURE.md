# Architecture

This file describes how the pieces fit together at the level a maintainer needs
before changing release, update or packaging behaviour. The product's runtime
design (discovery, routing, Multiview, USB extenders, remote access) is in
[PROJECT.md](PROJECT.md) and the files under [docs/](docs/); the rules that
govern changing it are in [AGENTS.md](AGENTS.md). This is OmniSuite, on
`release/omnisuite-1.1.x`; see [DEVELOPMENT.md](DEVELOPMENT.md) for how the two product
lines are kept apart.

## One repository, two products

| | OmniSuite | OmniSuite Plus |
|---|---|---|
| Branch | `release/omnisuite-1.1.x` (and `main` for the published release) | `omnisuite-plus` |
| Tag | `V1.1.1` | `plus-v1.2.0` |
| Artifact | `OmniSuite-V1.1.1-<platform>` | `OmniSuite-Plus-V1.2.0-<platform>` |
| Notes file | `RELEASE_NOTES_V1.1.1.md` | `RELEASE_NOTES_V1.2.0.md` |

They publish to one GitHub releases page. Nothing in a GitHub release says which
product it is, so the project establishes that from names it controls.

## Release identity

```
omni_product.py        PRODUCT_ID            the only file that differs between lines
omni_release.py        Product definitions   identical on both lines; pure, no I/O
  Product.parse_tag / parse_asset / tag_for / artifact_name / notes_path
  select_release(feed, product)              newest release of ONE product
tools/release_identity.py                    the workflow's guard (resolve, verify-assets)
tools/build_release.py                       names artifacts with the product prefix
OmniMatrix_upgrade_server_v7_6y.py           the in-app updater (see below)
.github/workflows/build-cross-platform.yml   identity job -> test -> build -> publish
```

`omni_release` is the single definition. The updater, the guard and the tests all
read it, so the rule cannot drift between the three. Everything in it is pure:
callers pass in what they have read from git, files or the network.

What identity is **not**: GitHub's "latest" release (repository-wide), the
prerelease flag (a channel, not a product), and a release's title or body (free
text). Identity is the tag, plus the artifact names as a cross-check.

## The release workflow

```
trigger (tag V* / v* / dispatch / push to main)
   |
   v
identity            tools/release_identity.py resolve
   |                 tag      -> this product's tag? agrees with VERSION? on release/omnisuite-1.1.x?
   |                             release notes exist?  -> publish = true
   |                 dispatch -> safe, suffixed name    -> publish = false
   |                 anything else, or any doubt        -> refuse
   v
test (4 platforms)  python run_tests.py
   v
build (4 platforms) uses the VERIFIED version; verify-assets on the produced file; smoke test
   v
publish-release     only when publish == true AND the ref is a tag;
                    downloads release-<product>-* only; verify-assets --complete;
                    body is the verified version's notes
```

Failure modes are all refusals: an unrecognised or foreign tag, a tag that
disagrees with `VERSION`, a commit that is not on the release branch, a missing
notes file, an unsafe or release-looking dispatch version, an artifact that is
another product's. None of them degrades into publishing something.

## The in-app update check

The updater is optional and is never a dependency: bounded request, six-hour
cache, off every polling and startup-critical path, and a manual action that
always works. It reads the release **list**, then `omni_release.select_release`:

1. skips anything that is not a dict, has no tag, or has a tag no product recognises;
2. skips releases whose tag belongs to the other product;
3. skips drafts and prereleases (not offered; they play no part in identity);
4. refuses a release whose assets name the other product;
5. picks the highest version number among what is left.

If nothing is left the answer is UNABLE_TO_CHECK (`NoCompatibleRelease`), never
"up to date": nothing was confirmed. An installed build ahead of the newest
release is CURRENT. Within the chosen release, the platform asset must be this
product's exact artifact name for this machine and the same version as the
release; otherwise the update is still reported and no download is offered.
Nothing is downloaded or installed by the check.

## Packaging

On macOS the build adds `NSLocalNetworkUsageDescription` to the built app's
`Info.plist` (the sentence macOS shows when it asks whether the app may reach
devices on the local network), signs the bundle again ad hoc because the edit
invalidates the signature, and verifies it. The smoke test reads the shipped plist
and signature and fails the build if either is wrong. See
[docs/MACOS_LOCAL_NETWORK.md](docs/MACOS_LOCAL_NETWORK.md) for the evidence and what
is still unproven.


`tools/build_release.py` names each artifact `<prefix><version>-<platform tail>`
with the product's prefix. The platform tails are a contract with the updater's
platform keys and with the guard; `tests/test_release_isolation.py` pins all
three together for both products.
