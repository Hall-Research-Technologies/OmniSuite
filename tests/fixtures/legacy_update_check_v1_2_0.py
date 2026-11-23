"""FROZEN, verbatim: the update check exactly as published in the OmniSuite V1.2.0 tag.

Extracted with `git show V1.2.0:OmniMatrix_upgrade_server_v7_6y.py`, lines 3972-4232
(from the GITHUB_OWNER comment through the end of _update_check), and with
`git show V1.2.0:omni_release.py` for legacy_omni_release_v1_2_0.py. It is the logic an
installed V1.2.0 runs. Do NOT edit the body to match current behaviour:
tests/test_updater_proof.py pins it by SHA-256 and asks it, beside the current code, the
same questions of the same scripted GitHub release lists.

The names it needs (omni_release, omni_product, requests, platform, time, threading, re,
urllib, log, _app_version) are supplied by the test, as the V1.2.0 module supplied them.
The body begins at the marker line below.
"""
# ==== BEGIN VERBATIM V1.2.0 BODY ====
# The product reads its OWN release repository (omni_release): the one releases are
# published to, and the one installed V1.1.0 apps already read.
GITHUB_OWNER, GITHUB_REPO = omni_release.active_product().release_repo.split("/", 1)
GITHUB_RELEASES_URL = f"https://github.com/{GITHUB_OWNER}/{GITHUB_REPO}/releases"
# The release LIST rather than the repository-wide "latest" release. Whether a release
# is one this product should offer is decided from its tag and artifact names
# (omni_release), never from anything GitHub says about it, and the newest is chosen
# by version number rather than by creation date.
GITHUB_RELEASES_API = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases?per_page=100"

# Several hours, not minutes. Nothing about a release changes quickly, and this
# must never look like another polling loop.
UPDATE_CHECK_TTL = 6 * 60 * 60
UPDATE_CHECK_TIMEOUT = 4.0

# The three answers a completed check can give. CHECKING exists only in the UI,
# which shows it while a request it started is in flight; the server never
# returns it, because a server that is answering has already finished checking.
UPDATE_AVAILABLE = "UPDATE_AVAILABLE"
UPDATE_CURRENT = "CURRENT"
UPDATE_UNABLE = "UNABLE_TO_CHECK"

_update_check_lock = threading.RLock()
_update_check_cache = {"checked_at": 0.0, "result": None}


def _version_tuple(text):
    """A comparable tuple from a version or tag, or None if it is not one.

    Numeric, never lexical: "1.0.10" is newer than "1.0.9", which a string
    comparison gets backwards. An optional leading V/v is accepted because the
    project's own tags carry one.
    """
    cleaned = str(text or "").strip()
    if cleaned[:1] in ("V", "v"):
        cleaned = cleaned[1:]
    # A trailing suffix such as -rc1 is not a normal release; the numeric core
    # is compared and the suffix is what marks it as a prerelease elsewhere.
    core = re.match(r"^(\d+(?:\.\d+)*)", cleaned)
    if not core:
        return None
    parts = [int(piece) for piece in core.group(1).split(".") if piece != ""]
    if not parts:
        return None
    # Padded so 1.0 and 1.0.0 compare equal.
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:4])


def _version_is_newer(candidate, installed):
    """True when `candidate` is a strictly newer release than `installed`."""
    left, right = _version_tuple(candidate), _version_tuple(installed)
    if left is None or right is None:
        return False
    return left > right


def _is_official_release_url(url):
    """Only this repository's own github.com URLs are ever handed to the UI.

    Without this the endpoint would happily return whatever URL an upstream
    response contained, which is an open redirect wearing a helpful hat.
    """
    text = str(url or "").strip()
    if not text:
        return False
    try:
        parsed = urllib.parse.urlparse(text)
    except Exception:
        return False
    if parsed.scheme != "https":
        return False
    host = (parsed.hostname or "").lower()
    if host not in ("github.com", "objects.githubusercontent.com"):
        return False
    if host == "github.com":
        expected = f"/{GITHUB_OWNER}/{GITHUB_REPO}/".lower()
        if not (parsed.path or "").lower().startswith(expected):
            return False
    return True


def _platform_asset_key():
    """How this machine's release asset is named, or None when unsure.

    Returning None is a real answer: an asset is only offered when the platform
    is identified confidently, because handing someone the wrong build is worse
    than handing them the releases page.
    """
    system = platform.system().lower()
    machine = (platform.machine() or "").lower()
    # These keys are the platform-and-architecture tails of the published
    # artifact names, which tools/build_release.py owns. They carry the
    # architecture because "windows" alone does not say x86-64, and an asset
    # list containing both a Windows x86-64 zip and a macOS x86-64 zip would
    # otherwise be ambiguous.
    if system == "windows":
        # Only an x86-64 build is published. Windows on ARM runs it under
        # emulation, so it is still the right download for that machine.
        return "windows-x86_64"
    if system == "linux":
        # Ubuntu x86-64 is the only Linux artifact built and tested; nothing is
        # offered to an architecture we do not publish.
        if machine in ("x86_64", "amd64"):
            return "ubuntu-x86_64"
        return None
    if system == "darwin":
        if machine in ("arm64", "aarch64"):
            return "macos-arm64"
        if machine in ("x86_64", "amd64"):
            return "macos-x86_64"
        return None
    return None


def _select_platform_asset(assets, version=None):
    """The asset matching this platform and this product, or None.

    The name must be this product's artifact name exactly: the product prefix,
    a version, and the platform tail. A loose suffix match would offer a build
    from some other artifact family that happens to end the same way. When
    `version` is given the version in the name must agree with the release it
    came from.
    """
    key = _platform_asset_key()
    if not key:
        return None
    product = omni_release.active_product()
    if not isinstance(assets, list):
        return None
    for asset in assets:
        if not isinstance(asset, dict):
            continue
        name = str(asset.get("name") or "")
        url = asset.get("browser_download_url")
        if not name or not _is_official_release_url(url):
            continue
        parsed = product.parse_asset(name)
        if not parsed or parsed["platform"] != key:
            continue
        if version is not None and (omni_release.version_tuple(parsed["version"])
                                    != omni_release.version_tuple(version)):
            continue
        return {"name": name, "url": url, "size": asset.get("size"),
                "platform": key}
    return None


def _fetch_release_feed():
    """Ask GitHub for the published releases. Raises on any failure.

    The list is read, not the repository-wide "latest" endpoint (see GITHUB_RELEASES_API), and
    drafts are already absent from an unauthenticated read. Whether a release
    belongs to this product is decided afterwards from its tag and artifact
    names, never from anything GitHub says about it.
    """
    response = requests.get(
        GITHUB_RELEASES_API,
        timeout=UPDATE_CHECK_TIMEOUT,
        headers={"Accept": "application/vnd.github+json",
                 "User-Agent": f"OmniSuite/{_app_version()}"},
    )
    if response.status_code == 404:
        raise LookupError("no releases published")
    if response.status_code in (403, 429):
        raise RuntimeError("rate limited")
    response.raise_for_status()
    return response.json()


def _update_check(force=False):
    """The cached answer, refreshed at most once per TTL unless forced.

    No credential is used or wanted: this reads a public endpoint. A failure is
    logged once at INFO and returned as a status, never raised at the caller.
    """
    now = time.time()
    with _update_check_lock:
        cached = _update_check_cache["result"]
        age = now - _update_check_cache["checked_at"]
        if cached and not force and age < UPDATE_CHECK_TTL:
            fresh = dict(cached)
            fresh["stale"] = False
            fresh["age_seconds"] = int(age)
            return fresh

    installed = _app_version()
    result = {
        "ok": True,
        # The UI switches on `status` and never parses the text. The states are
        # deliberately exhaustive, because the one thing that must never happen
        # is a failed request being presented as "you are up to date".
        "status": UPDATE_UNABLE,
        "installed_version": installed,
        "latest_version": None,
        "update_available": False,
        "release_url": GITHUB_RELEASES_URL,
        "asset": None,
        "asset_url": None,
        "checked_at": None,
        "source": "github",
        "product": omni_product.PRODUCT_ID,
        "stale": False,
        "error_summary": None,
        # Kept so anything still reading the older name keeps working.
        "current_version": installed,
    }
    try:
        feed = _fetch_release_feed()
        # Only this product's releases are ever considered. Nothing is offered
        # when the feed holds none, and that is UNABLE_TO_CHECK rather than
        # "up to date": nothing was confirmed.
        release, core = omni_release.select_release(feed, omni_release.active_product())
        html_url = release.get("html_url")
        result["latest_version"] = "V" + core
        # Strictly newer, and nothing else. A development build legitimately
        # ahead of the newest published release is CURRENT, not a failure and
        # not an update -- which is the whole reason this is a three-way state
        # and not a boolean.
        result["update_available"] = _version_is_newer(core, installed)
        if _is_official_release_url(html_url):
            result["release_url"] = html_url
        if result["update_available"]:
            result["asset"] = _select_platform_asset(release.get("assets"), core)
            result["asset_url"] = (result["asset"] or {}).get("url")
        result["status"] = UPDATE_AVAILABLE if result["update_available"] else UPDATE_CURRENT
    except Exception as exc:
        # Offline is the normal case for a lot of installations; it is not an
        # error worth a stack trace on every check.
        log.info("Update check unavailable: %s", type(exc).__name__)
        result["ok"] = False
        result["status"] = UPDATE_UNABLE
        # A short name, never a stack trace or a response body: this is shown to
        # an operator and travels in support dumps.
        result["error_summary"] = type(exc).__name__
        result["error"] = type(exc).__name__
        with _update_check_lock:
            previous = _update_check_cache["result"]
        if previous and previous.get("latest_version"):
            # A previous good answer is more useful than nothing; it is marked
            # stale so the UI can say so rather than implying it is current.
            # The last good answer is more useful than nothing, but it is
            # marked stale so the UI can say when it was taken rather than imply
            # it has just been confirmed. Its own status is kept: a remembered
            # CURRENT stays CURRENT-but-stale, and never becomes UNABLE.
            stale = dict(previous)
            stale.update({"ok": False, "stale": True,
                          "error": result["error_summary"],
                          "error_summary": result["error_summary"],
                          "installed_version": installed,
                          "current_version": installed})
            return stale
        return result

    result["checked_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    with _update_check_lock:
        _update_check_cache["checked_at"] = time.time()
        _update_check_cache["result"] = dict(result)
    return result

