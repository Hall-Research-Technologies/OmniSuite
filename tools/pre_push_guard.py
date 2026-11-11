#!/usr/bin/env python3
"""Refuse to push evidence of a private product, or of private development history, to the
public OmniSuite repository.

Git calls a pre-push hook with the remote name and URL as arguments and one line per ref on
standard input:  <local ref> <local sha> <remote ref> <remote sha>.

    python tools/pre_push_guard.py --install      # copy this file into .git/hooks and wire it up

What it blocks is EVIDENCE OF PRIVATE-PRODUCT EXPOSURE, not the names of modules. A module that
is now part of OmniSuite is public whatever product it historically came from; the earlier guard
listed thirteen such modules by name and so refused the V1.2.0 tree itself. This one checks:

  1. the ref is not named for a private branch or a recovery tag;
  2. no text file in the pushed tree names the retired private product, its private repository,
     or a private development branch;
  3. the tree carries no private artefact (an exposure audit document, firmware images, key
     material, a user or configuration file, an encrypted backup);
  4. no commit that would be new to the remote has a message naming the private product;
  5. no commit that would be new to the remote ever ADDED OR REMOVED text naming it. A tip that
     deletes the text still carries it in the history it publishes.

Only pushes to the public OmniSuite URL are checked, in whatever spelling. Deleting a ref is
always allowed. There is deliberately no bypass option: a push that is refused is fixed, not
forced. The forbidden names are assembled from fragments so this file, which ships in the
public tree, does not contain them.
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path

PUBLIC_REPOSITORY = "github.com/hall-research-technologies/omnisuite"
ZERO = "0" * 40

_A = "omni" + "suite"
_B = "pl" + "us"
# Names of the private product, its private repository, its branch and its release metadata.
CONTENT_PATTERN = "|".join([
    _A + r"[ _-]?" + _B,                     # the product's name in any common spelling
    _B + r"-private",                        # the name of its private remote
    _B + r"-v[0-9]",                         # its release tag
    r"com\." + _A + _B,                      # its bundle identifier
    "remote-user-" + "routing",              # the private development branch
])
CONTENT_RE = re.compile(CONTENT_PATTERN, re.IGNORECASE)

# Ref names that are never published, whatever they point at.
_REF_RE = re.compile("|".join([_A + r"[ _-]?" + _B, "remote-user-" + "routing",
                               r"(^|/)recovery/", r"(^|/)clean/"]), re.IGNORECASE)

# Files that must never be in a public tree.
_PRIVATE_FILES = re.compile("|".join([
    r"(^|/)docs/PUBLIC_" + "EXPOSURE_AUDIT\\.md$",
    r"(^|/)firmware/",
    r"(^|/)(users|config)\.json$",
    r"(^|/)\.env$",
    r"\.(pem|key|omnibak|pfx|p12)$",
    r"(^|/)overnight_rc/",
]))
# A public CA bundle or a placeholder is not key material; these are the only exceptions.
_PRIVATE_FILE_ALLOWED = re.compile(r"^(third_party_licenses/.*|tests/fixtures/.*)$")


def normalise_url(url: str) -> str:
    """The host/owner/name of a remote however it is spelled: case, scheme, credentials,
    scp-style, a trailing slash or `.git`."""
    text = (url or "").strip().lower()
    text = re.sub(r"^(ssh://|https?://|git://)", "", text)
    text = re.sub(r"^[^@/]*@", "", text)
    text = re.sub(r"^github\.com:", "github.com/", text)
    text = text.rstrip("/")
    if text.endswith(".git"):
        text = text[:-4]
    return text.rstrip("/")


def is_public_url(url: str) -> bool:
    return normalise_url(url) == PUBLIC_REPOSITORY


def _git(repo, *args):
    done = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    return done.returncode, done.stdout


def check_ref(repo, local_ref: str, local_sha: str, remote_sha: str = ZERO,
              remote_name: str = "") -> list:
    """Every reason this ref may not be pushed to the public repository. Empty means allowed."""
    if local_sha == ZERO:
        return []                                    # a deletion
    found = []
    if _REF_RE.search(local_ref or ""):
        found.append("%s is a private-development ref" % local_ref)

    code, out = _git(repo, "grep", "-I", "-l", "-i", "-E", CONTENT_PATTERN, local_sha, "--")
    if out.strip():
        files = [line.split(":", 1)[-1] for line in out.splitlines()][:5]
        found.append("the tree names the private product or its repository in: " + ", ".join(files))

    code, out = _git(repo, "ls-tree", "-r", "--name-only", local_sha)
    bad = [name for name in out.splitlines()
           if _PRIVATE_FILES.search(name) and not _PRIVATE_FILE_ALLOWED.match(name)]
    if bad:
        found.append("the tree carries private artefacts: " + ", ".join(bad[:5]))

    if remote_sha == ZERO:
        rng = [local_sha, "--not", "--remotes=%s" % remote_name] if remote_name else [local_sha]
    else:
        rng = ["%s..%s" % (remote_sha, local_sha)]
    code, out = _git(repo, "log", "--format=%H%n%an%n%ae%n%B", *rng)
    if CONTENT_RE.search(out):
        found.append("a commit message or author in the pushed history names the private product")
    code, out = _git(repo, "log", "--format=%h", "-i", "-G", CONTENT_PATTERN, *rng)
    if out.strip():
        found.append("the pushed history added or removed text naming the private product (%s)"
                     % ", ".join(out.split()[:5]))
    return found


def run(stdin, remote_name: str, url: str, repo=".", out=sys.stderr) -> int:
    if not is_public_url(url):
        return 0
    failed = False
    for line in stdin:
        parts = line.split()
        if len(parts) != 4:
            continue
        local_ref, local_sha, _remote_ref, remote_sha = parts
        for reason in check_ref(repo, local_ref, local_sha, remote_sha, remote_name):
            failed = True
            print("pre-push: REFUSED. %s" % reason, file=out)
    if failed:
        print("pre-push: nothing was pushed to %s." % url, file=out)
        return 1
    return 0


_HOOK = """#!/bin/sh
# Installed by tools/pre_push_guard.py --install. Runs the guard copied beside it.
exec python "$(dirname "$0")/omni_pre_push_guard.py" "$@"
"""


def install(repo=".") -> Path:
    code, out = _git(repo, "rev-parse", "--git-common-dir")
    hooks = (Path(repo) / out.strip() / "hooks").resolve()
    hooks.mkdir(parents=True, exist_ok=True)
    existing = hooks / "pre-push"
    if existing.exists() and "omni_pre_push_guard" not in existing.read_text(errors="replace"):
        shutil.copy2(existing, hooks / "pre-push.previous")
    shutil.copy2(Path(__file__).resolve(), hooks / "omni_pre_push_guard.py")
    existing.write_text(_HOOK, newline="\n")
    existing.chmod(existing.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return existing


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["--install"]:
        print("installed", install())
        return 0
    if argv[:1] == ["--check"]:                      # --check <ref-or-sha> : a manual dry run
        sha = _git(".", "rev-parse", argv[1])[1].strip()
        reasons = check_ref(".", argv[1], sha)
        for reason in reasons:
            print("REFUSED. %s" % reason)
        print("PASS" if not reasons else "FAIL")
        return 1 if reasons else 0
    if len(argv) < 2:
        print("usage: pre_push_guard.py <remote> <url>   (refs on standard input)", file=sys.stderr)
        return 2
    return run(sys.stdin, argv[0], argv[1], repo=os.getcwd())


if __name__ == "__main__":
    sys.exit(main())
