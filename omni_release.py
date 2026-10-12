"""Release identity for the two independently maintained product lines.

OmniSuite and OmniSuite Plus live in one GitHub repository and publish
releases to one releases page. Nothing about a GitHub release says which
product it belongs to, so the product has to be recognised from names this
project controls:

    OmniSuite         tag  V1.1.1            artifact  OmniSuite-V1.1.1-<platform>
    OmniSuite Plus    tag  plus-v1.2.0       artifact  OmniSuite-Plus-V1.2.0-<platform>

This module is the single definition of both identities and is byte-identical
on both lines. The line a checkout belongs to is chosen by `omni_product`,
which is the only file that differs between them. The in-app updater, the
release workflow's identity guard and the tests all read it, so the rule cannot
drift between the three.

Three things are deliberately NOT identity:

* GitHub's own "latest" release. It is repository-wide, so it would offer one
  product the other's release.
* The prerelease flag. That is a channel -- whether anyone should be offered
  the build -- and says nothing about which product it is.
* The release title or body. Free text.

Everything here is pure: no network, no filesystem, no git. Callers pass in
what they have read, which keeps every rule testable offline.
"""
from __future__ import annotations

import re
from typing import Iterable, Optional

# One to three further numeric parts after the major: V1.0, V1.1.0, V0.9.1.1.
_NUMERIC = r"(\d+(?:\.\d+){1,3})"

# Release artifacts. The platform tails are owned by tools/build_release.py and
# pinned against it by tests. Matched case-insensitively on the platform only.
PLATFORM_TAILS = ("Windows-x86_64.zip", "macOS-arm64.zip", "macOS-x86_64.zip",
                  "Ubuntu-x86_64.tar.gz")
_TAIL_PATTERN = r"(?P<key>windows-x86_64|macos-arm64|macos-x86_64|ubuntu-x86_64)\.(?:zip|tar\.gz|tgz)"


class Product:
    """One product line's identity. Immutable by convention."""

    def __init__(self, product_id: str, name: str, tag_prefix: str,
                 artifact_prefix: str, release_branch: str):
        self.id = product_id
        self.name = name
        self.tag_prefix = tag_prefix
        self.artifact_prefix = artifact_prefix
        self.release_branch = release_branch
        # \Z, not $: "$" also matches before a trailing newline, which would let
        # a tag with a newline on the end through as a release tag.
        self._tag = re.compile(r"^" + re.escape(tag_prefix) + _NUMERIC + r"\Z")
        # The version in an artifact name carries an upper-case V. The prefix is
        # matched literally and case-sensitively: "OmniSuite-" is a prefix of
        # "OmniSuite-Plus-", and it is the `V<digit>` that follows which keeps the
        # first pattern from claiming the second product's files.
        self._asset = re.compile(
            r"^" + re.escape(artifact_prefix) + r"V" + _NUMERIC + r"-" + _TAIL_PATTERN + r"\Z",
            re.IGNORECASE)
        self._asset_prefix = re.compile(r"^" + re.escape(artifact_prefix), re.IGNORECASE)

    # -- tags ---------------------------------------------------------------
    def parse_tag(self, tag) -> Optional[str]:
        """The numeric core of a tag ('1.1.1'), or None if it is not this product's."""
        match = self._tag.match(str(tag or ""))
        return match.group(1) if match else None

    def tag_for(self, version: str) -> str:
        return self.tag_prefix + core_of(version)

    # -- artifacts ----------------------------------------------------------
    def parse_asset(self, name) -> Optional[dict]:
        """{'version': '1.1.1', 'platform': 'macos-arm64'} for this product's artifact."""
        match = self._asset.match(str(name or ""))
        if not match:
            return None
        return {"version": match.group(1), "platform": match.group("key").lower()}

    def artifact_name(self, version: str, tail: str) -> str:
        return "%sV%s-%s" % (self.artifact_prefix, core_of(version), tail)

    def release_version(self, core: str) -> str:
        return "V" + core

    def notes_path(self, core: str) -> str:
        return "RELEASE_NOTES_V%s.md" % core


PRODUCTS = {
    "omnisuite": Product("omnisuite", "OmniSuite", "V", "OmniSuite-",
                         "release/omnisuite-1.1.x"),
    "omnisuite-plus": Product("omnisuite-plus", "OmniSuite Plus", "plus-v",
                              "OmniSuite-Plus-", "omnisuite-plus"),
}

# A tag that begins with the first product's prefix in a different case is
# refused rather than normalised: 'v1.1.1' is not 'V1.1.1', and a release that
# had to be repaired to be understood was not understood.


class NoCompatibleRelease(LookupError):
    """The release feed was read, and nothing in it belongs to this product."""


class IdentityError(ValueError):
    """A release identity that is wrong or cannot be established."""


def active_product() -> Product:
    """The product this checkout is. Raises IdentityError when it is unknown."""
    try:
        import omni_product
    except ImportError as exc:                       # pragma: no cover - always shipped
        raise IdentityError("omni_product is missing; the product line is unknown") from exc
    try:
        return PRODUCTS[omni_product.PRODUCT_ID]
    except (AttributeError, KeyError) as exc:
        raise IdentityError("omni_product names no known product line") from exc


def core_of(version) -> str:
    """'1.1.1' from 'V1.1.1', 'v1.1.1', 'plus-v1.2.0' or '1.1.1'. Never guesses."""
    text = str(version or "").strip()
    for prefix in ("plus-v", "plus-V", "V", "v"):
        if text.startswith(prefix):
            text = text[len(prefix):]
            break
    if not re.fullmatch(_NUMERIC, text) or "\n" in text:
        raise IdentityError("%r is not a release version" % (version,))
    return text


def version_tuple(core: str) -> tuple:
    """Comparable numerically, never as a string: 1.0.10 is newer than 1.0.9."""
    parts = [int(piece) for piece in core.split(".")]
    while len(parts) < 3:
        parts.append(0)                              # 1.0 and 1.0.0 compare equal
    return tuple(parts[:4])


def owners_of_tag(tag) -> list:
    """Every product whose tag rule accepts this tag. More than one is ambiguous."""
    return [product for product in PRODUCTS.values() if product.parse_tag(tag)]


def owners_of_asset(name) -> list:
    return [product for product in PRODUCTS.values() if product.parse_asset(name)]


def _names_other_product(assets: Iterable, product: Product) -> Optional[str]:
    """The first asset that belongs to a different product, else None.

    An asset that is named as another product's artifact is enough evidence that
    the release is mixed, and a mixed release is not safe to offer to either
    product.
    """
    if not isinstance(assets, list):
        return None
    for asset in assets:
        if not isinstance(asset, dict):
            continue
        name = str(asset.get("name") or "")
        for other in PRODUCTS.values():
            if other is product:
                continue
            if other.parse_asset(name):
                return name
    return None


def select_release(releases, product: Product):
    """The newest release that belongs to `product`, or raise NoCompatibleRelease.

    Returns (release, core). Draft and prerelease releases are skipped as a
    channel decision -- they are never offered -- and play no part in deciding
    which product a release belongs to. Identity comes from the tag alone, and
    a release whose assets name the other product is refused outright.

    The newest is the highest version number, not the first in the feed: feed
    order is by creation time, which is not release order after a backport.
    """
    if not isinstance(releases, list):
        raise ValueError("the release feed was not a list")
    best = None
    rejected = {"malformed": 0, "other_product": 0, "mixed_assets": 0,
                "not_offered": 0}
    for release in releases:
        if not isinstance(release, dict):
            rejected["malformed"] += 1
            continue
        tag = release.get("tag_name")
        if not isinstance(tag, str) or not tag:
            rejected["malformed"] += 1
            continue
        owners = owners_of_tag(tag)
        if len(owners) > 1:
            rejected["malformed"] += 1               # ambiguous: refuse, do not pick
            continue
        if not owners:
            rejected["malformed"] += 1
            continue
        if owners[0] is not product:
            rejected["other_product"] += 1
            continue
        if release.get("draft") or release.get("prerelease"):
            rejected["not_offered"] += 1
            continue
        if _names_other_product(release.get("assets"), product):
            rejected["mixed_assets"] += 1
            continue
        core = product.parse_tag(tag)
        if best is None or version_tuple(core) > version_tuple(best[1]):
            best = (release, core)
    if best is None:
        error = NoCompatibleRelease("no %s release in the feed" % product.name)
        error.rejected = rejected
        raise error
    return best
