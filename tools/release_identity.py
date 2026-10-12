#!/usr/bin/env python3
"""Decide, and refuse, release identity for the build workflow.

The workflow used to take a tag, a typed version or the VERSION file and trust
whichever applied. With two products in one repository that is not safe: a tag
made on the wrong line would build and publish under the wrong name. This tool
is where the workflow asks "what is this run, and is it allowed to publish?".

    release_identity.py resolve       decide product / version / publish, or refuse
    release_identity.py verify-assets check built artifact names against the product

It fails closed. An ambiguous or unrecognised identity exits non-zero with a
sentence; it never guesses and never repairs. A manually dispatched build is a
diagnostic build: it is always allowed, never publishes, and is named so that it
cannot be mistaken for a release.

All decisions are made by pure functions that are given what they need, so the
tests exercise every rule with no git, network or filesystem.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import omni_release                                               # noqa: E402
from omni_release import IdentityError, PRODUCTS, PLATFORM_TAILS   # noqa: E402

# A version typed into a dispatch is placed in file names and shell commands.
# Anything outside this set is refused rather than escaped.
_SAFE_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def resolve(event, ref_type, ref_name, dispatch_version, version_file_text,
            product, is_ancestor, notes_exist) -> dict:
    """The decision for one workflow run. Raises IdentityError to refuse it.

    is_ancestor(branch) -> bool    is this commit reachable from that release branch?
    notes_exist(path)   -> bool    does the release-notes file exist?
    """
    event = str(event or "")
    if ref_type == "tag":
        if event != "push":
            raise IdentityError("a tag run must be a push event, not %r" % event)
        return _resolve_tag(ref_name, version_file_text, product, is_ancestor, notes_exist)
    if event == "workflow_dispatch":
        return _resolve_diagnostic(dispatch_version, version_file_text, product)
    if event == "push":
        return _resolve_diagnostic("", version_file_text, product)
    raise IdentityError("the event %r is not one this workflow knows how to identify" % event)


def _resolve_tag(tag, version_file_text, product, is_ancestor, notes_exist) -> dict:
    owners = omni_release.owners_of_tag(tag)
    if not owners:
        raise IdentityError(
            "the tag %r is not a release tag of any product. %s tags look like %s, "
            "and nothing else publishes." % (tag, product.name,
                                             product.tag_prefix + "<major>.<minor>[.<patch>]"))
    if len(owners) > 1:
        raise IdentityError("the tag %r matches more than one product; refusing" % tag)
    if owners[0] is not product:
        raise IdentityError(
            "the tag %r belongs to %s but this commit is %s. Tag the release on the "
            "other product's branch." % (tag, owners[0].name, product.name))
    core = product.parse_tag(tag)
    try:
        file_core = omni_release.core_of((version_file_text or "").strip())
    except IdentityError:
        raise IdentityError("VERSION does not hold a release version (%r)"
                            % (version_file_text or "").strip())
    if omni_release.version_tuple(file_core) != omni_release.version_tuple(core):
        raise IdentityError("the tag %r says %s but VERSION says %s; they must agree"
                            % (tag, core, file_core))
    if not is_ancestor(product.release_branch):
        raise IdentityError(
            "this commit is not on %s, so %r cannot be %s's release. Refusing to "
            "publish." % (product.release_branch, tag, product.name))
    notes = product.notes_path(core)
    if not notes_exist(notes):
        raise IdentityError("no release notes for %s (expected %s)" % (core, notes))
    return {"product": product.id, "release_version": product.release_version(core),
            "publish": "true", "notes_path": notes, "tag": tag,
            "artifact_prefix": product.artifact_prefix}


def _resolve_diagnostic(dispatch_version, version_file_text, product) -> dict:
    version = (dispatch_version or "").strip()
    if not version:
        base = (version_file_text or "").strip()
        if not base:
            raise IdentityError("no version was given and VERSION is empty")
        version = base + "-dev"
    if not _SAFE_VERSION.match(version):
        raise IdentityError("the version %r contains characters that are not allowed "
                            "(letters, digits, '.', '_' and '-' only)" % version)
    # A bare release version would produce files named exactly like a published
    # release. A diagnostic build must be distinguishable at a glance.
    try:
        omni_release.core_of(version)
    except IdentityError:
        pass
    else:
        raise IdentityError(
            "%r would be indistinguishable from a published release. Add a suffix, "
            "for example %s-diag1." % (version, version))
    if version.lower().startswith(("plus-", "plus_")):
        raise IdentityError("do not put the product in the version; %s adds its own "
                            "artifact prefix" % product.name)
    return {"product": product.id, "release_version": version, "publish": "false",
            "notes_path": "", "tag": "", "artifact_prefix": product.artifact_prefix}


def verify_asset_names(names, product, release_version, require_complete=False):
    """Refuse any artifact that is not this product's, for this version.

    Returns the list of names checked. Raises IdentityError naming the offender.
    """
    expected = {product.artifact_name(release_version, tail) if _is_release(release_version)
                else product.artifact_prefix + release_version + "-" + tail
                for tail in PLATFORM_TAILS}
    expected |= {name + ".sha256" for name in set(expected)}
    seen = set()
    for name in names:
        if name == "SHA256SUMS.txt":
            continue                      # assembled by the publish job itself
        owners = omni_release.owners_of_asset(name.replace(".sha256", ""))
        foreign = [o for o in owners if o is not product]
        if foreign:
            raise IdentityError("%s belongs to %s, not %s" % (name, foreign[0].name, product.name))
        if name not in expected:
            raise IdentityError("%s is not an artifact of %s %s" % (name, product.name, release_version))
        seen.add(name)
    if require_complete:
        missing = sorted(n for n in expected if not n.endswith(".sha256") and n not in seen)
        if missing:
            raise IdentityError("missing release artifact(s): " + ", ".join(missing))
    return sorted(seen)


def _is_release(version) -> bool:
    try:
        omni_release.core_of(version)
        return True
    except IdentityError:
        return False


# ---- command line ----------------------------------------------------------
def _git_is_ancestor(branch: str, cwd=None) -> bool:
    """HEAD reachable from origin/<branch>. Unknown means no."""
    cwd = cwd or ROOT
    ref = "origin/" + branch
    probe = subprocess.run(["git", "rev-parse", "--verify", "-q", ref],
                           cwd=cwd, capture_output=True, text=True)
    if probe.returncode != 0:
        print("::error::cannot find %s to check ancestry; refusing" % ref)
        return False
    result = subprocess.run(["git", "merge-base", "--is-ancestor", "HEAD", ref], cwd=cwd)
    return result.returncode == 0


def _write_outputs(path, decision: dict):
    lines = ["%s=%s" % (key, value) for key, value in decision.items()]
    if path:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
    print("release identity:")
    for line in lines:
        print("  " + line)


def _cmd_resolve(args) -> int:
    product = omni_release.active_product()
    version_text = (ROOT / "VERSION").read_text(encoding="utf-8") if (ROOT / "VERSION").exists() else ""
    decision = resolve(
        event=os.environ.get("EVENT_NAME", ""), ref_type=os.environ.get("REF_TYPE", ""),
        ref_name=os.environ.get("REF_NAME", ""),
        dispatch_version=os.environ.get("DISPATCH_VERSION", ""),
        version_file_text=version_text, product=product,
        is_ancestor=_git_is_ancestor, notes_exist=lambda p: (ROOT / p).is_file())
    _write_outputs(args.github_output, decision)
    return 0


def _cmd_verify(args) -> int:
    product = omni_release.active_product()
    names = sorted(p.name for p in Path(args.dir).iterdir() if p.is_file())
    if not names:
        raise IdentityError("no artifacts found in %s" % args.dir)
    checked = verify_asset_names(names, product, args.version, args.complete)
    print("verified %d artifact(s) for %s %s" % (len(checked), product.name, args.version))
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    one = sub.add_parser("resolve")
    one.add_argument("--github-output", default=os.environ.get("GITHUB_OUTPUT", ""))
    two = sub.add_parser("verify-assets")
    two.add_argument("--dir", required=True)
    two.add_argument("--version", required=True)
    two.add_argument("--complete", action="store_true")
    args = parser.parse_args(argv)
    try:
        return {"resolve": _cmd_resolve, "verify-assets": _cmd_verify}[args.command](args)
    except IdentityError as exc:
        print("::error::%s" % exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
