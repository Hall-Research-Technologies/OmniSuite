# Architecture

This file describes how the pieces fit together at the level a maintainer needs before changing
release, update or packaging behaviour. The product's runtime design (discovery, routing,
Multiview, USB extenders) is in [PROJECT.md](PROJECT.md) and the files under [docs/](docs/); the
rules that govern changing it are in [AGENTS.md](AGENTS.md). See [DEVELOPMENT.md](DEVELOPMENT.md)
for branches and releasing.

## Release identity

```
omni_product.py        PRODUCT_ID            which product this checkout is
omni_release.py        Product definition    tag, artifact, release repository, macOS identity; pure, no I/O
  Product.parse_tag / parse_asset / tag_for / artifact_name / notes_path
  select_release(feed, product)              the newest release this product should offer
tools/release_identity.py                    the workflow's guard (resolve, verify-assets)
tools/build_release.py                       names artifacts with the product prefix
OmniMatrix_upgrade_server_v7_6y.py           the in-app updater (see below)
.github/workflows/build-cross-platform.yml   identity job -> test -> build -> publish
```

| | |
|---|---|
| Tag | `V1.2.0` |
| Artifact | `OmniSuite-V1.2.0-<platform>` |
| Notes file | `RELEASE_NOTES_V1.2.0.md` |
| Release repository | `Hall-Research-Technologies/OmniSuite` (`release_repo`). The workflow publishes there with a dedicated token and does not run there; installed V1.1.0 applications read its "latest" release |
| macOS bundle identifier | `OmniSuite` (unchanged since the first release) |

`omni_release` is the single definition. The updater, the guard and the tests all read it, so the
rule cannot drift between the three. Everything in it is pure: callers pass in what they have read
from git, files or the network.

What identity is **not**: GitHub's "latest" release, the prerelease flag (a channel, not an
identity), and a release's title or body (free text). Identity is the tag, plus the artifact names
as a cross-check.

## The release workflow

```
trigger (tag V* / v*, or a manual dispatch; nothing else starts it)
   |
   v
identity            tools/release_identity.py resolve
   |                 tag      -> this product's tag? agrees with VERSION? on the release branch?
   |                             release notes exist?  -> publish = true
   |                 dispatch -> safe, suffixed name    -> publish = false
   |                 anything else, or any doubt        -> refuse
   v
test (4 platforms)  python run_tests.py
   v
build (4 platforms) uses the VERIFIED version; verify-assets on the produced file; smoke test
   v
publish-release     only when publish == true AND the ref is a tag;
                    verify-assets --complete; body is the verified version's notes
```

Workflows run only in the repository named by the repository variable `OMNI_CI_REPOSITORY`; elsewhere
the identity job, and every job that needs it, is skipped. Publishing needs `RELEASE_PUBLISH_TOKEN`; a
run without it stops. A dispatch or a push to a branch is a diagnostic build and cannot publish.

Failure modes are all refusals: an unrecognised tag, a tag that disagrees with `VERSION`, a commit
that is not on the release branch, a missing notes file, an unsafe or release-looking dispatch
version, an artifact that is not this product's. None of them degrades into publishing something.

## The in-app update check

The updater is optional and is never a dependency: bounded request, six-hour cache, off every
polling and startup-critical path (one check runs once on a daemon thread after start; nothing schedules another),
and a manual action that always works. It reads the release
**list**, then `omni_release.select_release`:

1. skips anything that is not a dict, has no tag, or has a tag that is not this product's form;
2. skips drafts and prereleases (not offered; they play no part in identity);
3. refuses a release whose assets are another artifact family's;
4. picks the highest version number among what is left.

If nothing is left the answer is UNABLE_TO_CHECK (`NoCompatibleRelease`), never "up to date":
nothing was confirmed. An installed build ahead of the newest release is CURRENT. Within the chosen
release, the platform asset must be this product's exact artifact name for this machine and the same
version as the release; otherwise the update is still reported and no download is offered. Nothing
is downloaded or installed by the check. The releases page address comes from the server, never from
page code.

Installed V1.1.0 applications use an older check that reads the repository's "latest" release. They
cannot be changed, so releases must keep the tag form and artifact names that check understands.

## Runtime structure

Modules sit beside the main server (`OmniMatrix_upgrade_server_v7_6y.py`) and are registered onto its
Flask application rather than reconstructing it.

| Module | Owns |
|---|---|
| `omni_local_port.py` | The optional fixed local port: validation, its own atomically written settings file, the launcher's recovery states |
| `omni_remote.py`, `omni_remote_api.py` | The second (Remote Access) listener and its HTTP surface: sign-in, accounts, direct access, state |
| `omni_tls.py` | Certificates: generating one (identity, names it is valid for, three calendar years), loading a supplied one, describing it without key material |
| `omni_adapters.py` | Network adapters with an identity that survives an address change; the 15-second adapter list |
| `omni_access.py`, `omni_gate.py` | Route classification (an unclassified route is denied) and the single authorization gate in front of every route |
| `omni_users.py`, `omni_secret.py` | Accounts, sessions, the audit trail, direct access links and their protected storage |
| `omni_projection.py`, `omni_destinations.py`, `omni_aliases.py` | What a restricted user may know, where a remote caller may make the server connect, friendly names |
| `omni_groups.py` | Display Groups: members held by hardware address, routed one at a time through the ordinary A/V route |
| `omni_inventory.py` | Which discovered units accounts and groups depend on, and so which Clear Units may remove |
| `omni_locks.py`, `omni_statecache.py`, `omni_liveness.py` | Per-resource serialization, one shared read for every browser, what is believed about whether a unit answers |
| `omni_backup.py` | Encrypted backup and the authoritative, atomic restore |
| `omni_firmware.py` | Firmware availability: reports only, never downloads or selects an upgrade target |
| `omni_routediag.py` | Opt-in route timing diagnostics (`OMNI_ROUTE_DIAG=1`) |

**Transport.** A first-time Remote Access setup starts on HTTP and needs an explicit acknowledgement;
HTTPS is the recommended choice once a certificate exists. A saved choice is honoured. A saved HTTPS
choice that cannot be served refuses to start, never downgrades, and there is no plain-HTTP listener
while HTTPS is active. See [docs/REMOTE_ACCESS_ARCHITECTURE.md](docs/REMOTE_ACCESS_ARCHITECTURE.md).

**Restricted routing.** A User's A/V is Standard routing, the User Matrix (AV, Video, Audio over the
account's permitted sources, displays and groups), or both. Every interface is a projection built on the
server from the account's own permissions; the request is authorised again immediately before the device
write.

## Packaging

The build is `tools/build_release.py` (PyInstaller, one-file on Windows, one-directory elsewhere). It
bundles an allowlist of data (`ui`, `VERSION`, `LICENSE`, the notices, `third_party_licenses`, three
images), reads the package's table of contents back and fails if anything forbidden arrived, and places
`LICENSE`, `THIRD_PARTY_NOTICES.md` and `third_party_licenses/` beside the application in every archive,
because a recipient cannot read the copies inside a one-file executable. `tools/collect_licenses.py` is
the single list of what is considered shipped. The Windows executable carries a version resource
generated from the build version. The platforms are Windows x86-64, macOS arm64, macOS x86-64 and
Ubuntu x86-64 (the runner that was built and tested, not Linux in general).

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
three together.
