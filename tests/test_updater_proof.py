"""The update check, proven against the CURRENT code and the code installed copies actually run.

Three implementations are asked the same question of the same scripted GitHub:

* NEW   -- the updater in this tree (V1.2.1).
* V1.2.0 -- the updater exactly as published in the V1.2.0 tag, extracted verbatim into
            tests/fixtures/legacy_update_check_v1_2_0.py (with that tag's omni_release).
* V1.1.0 -- the frozen check in tests/fixtures/legacy_update_check_v1_1_0.py. Installed V1.1.0
            applications read the repository's "latest" release, so they are fed the release
            GitHub would call "latest" (the newest created that is neither a draft nor a
            prerelease), not the list.

Every implementation sees the real `requests.get` call site with `requests.get` replaced by a
scripted transport, so URLs, status handling and JSON handling are exercised, not bypassed.
Nothing here touches a network: the hardware fence refuses it and the transport is scripted.

Platforms are the DISTRIBUTED ones, Windows x86_64 and macOS arm64, plus the platforms that are no
longer distributed (macOS Intel, Linux), which must be told so and offered nothing.
"""
import ast
import hashlib
import importlib.util
import logging
import re
import threading
import time
import unittest
import urllib.parse
from pathlib import Path
from unittest import mock

try:
    from . import test_usb_integration as base
except ImportError:                                   # loaded by path, no package
    import test_usb_integration as base

import omni_product
import omni_release

srv = base.srv
ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures"
REPO = "https://github.com/Hall-Research-Technologies/OmniSuite"
WIN = "Windows-x86_64.zip"
MAC = "macOS-arm64.zip"
PLATFORMS = {"win": ("Windows", "AMD64"), "mac": ("Darwin", "arm64"),
             "win_arm": ("Windows", "ARM64"),
             "intel": ("Darwin", "x86_64"), "linux": ("Linux", "x86_64"),
             "linux_arm": ("Linux", "aarch64")}

# SHA-256 of the verbatim V1.2.0 body (line endings normalised to LF) and of that tag's
# omni_release.py. Editing either fixture changes the hash and fails the first test below.
V120_BODY_SHA256 = "e16539122e142084517b3268c5bb0637adfb376a38e1999cc13fb4929c8d68bd"
V120_RELEASE_SHA256 = "4aed5c67d83f1d37f8c07e7dce9e090cff8f0054175e5d1be20e099df8437eb2"


# ------------------------------------------------------------------ the scripted GitHub
def asset(name, tag, url=None):
    return {"name": name, "size": 1,
            "browser_download_url": url or "%s/releases/download/%s/%s" % (REPO, tag, name)}


def rel(tag, tails=(WIN, MAC), draft=False, prerelease=False, names=None, assets=None):
    if assets is None:
        names = names if names is not None else ["OmniSuite-%s-%s" % (tag, t) for t in tails]
        assets = [asset(n, tag) for n in names]
    return {"tag_name": tag, "draft": draft, "prerelease": prerelease,
            "html_url": "%s/releases/tag/%s" % (REPO, tag), "assets": assets}


class Response:
    def __init__(self, status=200, payload=None, bad_json=False):
        self.status_code = status
        self._payload = payload
        self._bad_json = bad_json

    def json(self):
        if self._bad_json:
            raise ValueError("not json")
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError("HTTP %d" % self.status_code)


class Transport:
    """requests.get, scripted. `feed` answers the list URL; `latest` is derived for V1.1.0."""

    def __init__(self, feed=None, raises=None, status=200, bad_json=False):
        self.feed, self.raises, self.status, self.bad_json = feed, raises, status, bad_json
        self.urls = []

    def get(self, url, **kwargs):
        self.urls.append(url)
        if self.raises is not None:
            raise self.raises
        if url.endswith("/releases/latest"):
            payload = self._latest()
            if payload is None:
                return Response(404, {})
            return Response(self.status, payload, self.bad_json)
        return Response(self.status, self.feed, self.bad_json)

    def _latest(self):
        """What GitHub's own 'latest' endpoint returns: the newest published, non-prerelease."""
        if not isinstance(self.feed, list):
            return self.feed if isinstance(self.feed, dict) else {}
        for item in self.feed:
            if isinstance(item, dict) and not item.get("draft") and not item.get("prerelease"):
                return item
        return None


# ------------------------------------------------------------------ the three implementations
def _load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Platform:
    def __init__(self, system, machine):
        self._system, self._machine = system, machine

    def system(self):
        return self._system

    def machine(self):
        return self._machine


def _normalise(result):
    asset_ = result.get("asset") or {}
    return {"status": result.get("status"), "latest": result.get("latest_version"),
            "asset": asset_.get("name"), "update": bool(result.get("update_available"))}


_FETCH = []


def _real_fetch_release_feed():
    """The server's own _fetch_release_feed, rebuilt from source.

    The test package replaces srv._fetch_release_feed with a function that refuses to run, so no
    test can contact GitHub. To exercise the real call site (URL, headers, status handling, JSON)
    against a scripted `requests.get`, the original function is compiled again from the module's
    source into the module's own namespace. No network is involved: `requests.get` is the script.
    """
    if _FETCH:
        return _FETCH[0]
    source = Path(srv.__file__).read_text(encoding="utf-8")
    for node in ast.parse(source).body:
        if isinstance(node, ast.FunctionDef) and node.name == "_fetch_release_feed":
            module = ast.Module(body=[node], type_ignores=[])
            namespace = dict(srv.__dict__)
            exec(compile(module, srv.__file__, "exec"), namespace)
            _FETCH.append(namespace["_fetch_release_feed"])
            return _FETCH[0]
    raise AssertionError("_fetch_release_feed not found")


def run_new(transport, installed, platform):
    with srv._update_check_lock:
        srv._update_check_cache["checked_at"] = 0.0
        srv._update_check_cache["result"] = None
    system, machine = platform
    with mock.patch.object(srv, "_app_version", lambda: installed), \
            mock.patch.object(srv, "_fetch_release_feed", _real_fetch_release_feed()), \
            mock.patch.object(srv.requests, "get", transport.get), \
            mock.patch.object(srv.platform, "system", return_value=system), \
            mock.patch.object(srv.platform, "machine", return_value=machine):
        return srv._update_check(force=True)


def _legacy_120_namespace(transport, installed, platform):
    legacy_release = _load_module("legacy_omni_release_v1_2_0",
                                  FIXTURES / "legacy_omni_release_v1_2_0.py")
    requests_stub = type("Requests", (), {"get": staticmethod(transport.get)})
    namespace = {"re": re, "time": time, "threading": threading, "urllib": urllib,
                 "platform": _Platform(*platform), "requests": requests_stub,
                 "omni_release": legacy_release, "omni_product": omni_product,
                 "log": logging.getLogger("legacy-v1.2.0"),
                 "_app_version": lambda: installed}
    source = (FIXTURES / "legacy_update_check_v1_2_0.py").read_text(encoding="utf-8")
    exec(compile(source, "legacy_update_check_v1_2_0.py", "exec"), namespace)
    return namespace


def run_v120(transport, installed, platform):
    return _legacy_120_namespace(transport, installed, platform)["_update_check"](force=True)


def run_v110(transport, installed, platform):
    requests_stub = type("Requests", (), {"get": staticmethod(transport.get)})
    namespace = {"re": re, "time": time, "threading": threading, "urllib": urllib,
                 "platform": _Platform(*platform), "requests": requests_stub,
                 "log": logging.getLogger("legacy-v1.1.0"),
                 "_app_version": lambda: installed}
    source = (FIXTURES / "legacy_update_check_v1_1_0.py").read_text(encoding="utf-8")
    exec(compile(source, "legacy_update_check_v1_1_0.py", "exec"), namespace)
    return namespace["_update_check"](force=True)


IMPLEMENTATIONS = {"new": run_new, "v1.2.0": run_v120, "v1.1.0": run_v110}


def ask(implementation, feed=None, installed="V1.2.0", platform="win", **transport):
    return _normalise(IMPLEMENTATIONS[implementation](
        Transport(feed, **transport), installed, PLATFORMS[platform]))


A = "OmniSuite-V1.2.1-" + WIN          # the Windows x86_64 asset of V1.2.1
M = "OmniSuite-V1.2.1-" + MAC          # the macOS arm64 asset of V1.2.1
OFFERED = "UPDATE_AVAILABLE"
CURRENT = "CURRENT"
UNABLE = "UNABLE_TO_CHECK"


def available(latest, name):
    return {"status": OFFERED, "latest": latest, "asset": name, "update": True}


def current(latest):
    return {"status": CURRENT, "latest": latest, "asset": None, "update": False}


UNABLE_RESULT = {"status": UNABLE, "latest": None, "asset": None, "update": False}


class FixtureIntegrityTests(unittest.TestCase):
    """The old code under test is the old code, byte for byte."""

    def test_the_v120_updater_fixture_is_the_published_body(self):
        text = (FIXTURES / "legacy_update_check_v1_2_0.py").read_bytes().replace(b"\r\n", b"\n")
        marker = b"# ==== BEGIN VERBATIM V1.2.0 BODY ====\n"
        self.assertIn(marker, text)
        body = text.split(marker, 1)[1]
        self.assertEqual(hashlib.sha256(body).hexdigest(), V120_BODY_SHA256)
        self.assertIn(b"releases?per_page=100", body)
        self.assertIn(b'"macos-x86_64"', body)             # it still knows the four old platforms

    def test_the_v120_release_module_fixture_is_the_published_one(self):
        raw = (FIXTURES / "legacy_omni_release_v1_2_0.py").read_bytes().replace(b"\r\n", b"\n")
        self.assertEqual(hashlib.sha256(raw).hexdigest(), V120_RELEASE_SHA256)
        self.assertIn(b"Ubuntu-x86_64.tar.gz", raw)

    def test_the_v110_fixture_is_still_the_latest_endpoint_client(self):
        text = (FIXTURES / "legacy_update_check_v1_1_0.py").read_text(encoding="utf-8")
        self.assertIn("releases/latest", text)


class UpdaterProofTests(unittest.TestCase):
    """One scenario, every implementation, the answer each must give."""

    def assert_all(self, feed, installed, platform, new, v120, v110=None, **transport):
        expected = {"new": new, "v1.2.0": v120}
        if v110 is not None:
            expected["v1.1.0"] = v110
        for implementation, want in expected.items():
            with self.subTest(implementation=implementation, platform=platform, installed=installed):
                got = ask(implementation, feed, installed, platform, **transport)
                self.assertEqual(got, want)

    # ---- the case that has to work: V1.2.0 -> V1.2.1
    def test_1_2_0_is_offered_1_2_1_with_its_own_platform_asset(self):
        feed = [rel("V1.2.1"), rel("V1.2.0", tails=(WIN, MAC, "macOS-x86_64.zip", "Ubuntu-x86_64.tar.gz"))]
        self.assert_all(feed, "V1.2.0", "win", available("V1.2.1", A), available("V1.2.1", A),
                        available("V1.2.1", A))
        self.assert_all(feed, "V1.2.0", "mac", available("V1.2.1", M), available("V1.2.1", M),
                        available("V1.2.1", M))
        self.assert_all(feed, "V1.2.0", "win_arm", available("V1.2.1", A), available("V1.2.1", A),
                        available("V1.2.1", A))

    def test_1_1_0_is_offered_1_2_1_too(self):
        feed = [rel("V1.2.1"), rel("V1.1.0")]
        self.assert_all(feed, "V1.1.0", "win", available("V1.2.1", A), available("V1.2.1", A),
                        available("V1.2.1", A))

    def test_the_installed_version_sees_itself_as_current(self):
        feed = [rel("V1.2.1"), rel("V1.2.0")]
        for platform in ("win", "mac"):
            self.assert_all(feed, "V1.2.1", platform, current("V1.2.1"), current("V1.2.1"),
                            current("V1.2.1"))

    def test_a_newer_local_build_is_never_offered_a_downgrade(self):
        feed = [rel("V1.2.1"), rel("V1.2.0")]
        for installed in ("V1.3.0", "V1.2.2", "V2.0.0"):
            self.assert_all(feed, installed, "win", current("V1.2.1"), current("V1.2.1"),
                            current("V1.2.1"))

    # ---- what is ignored
    def test_malformed_versions_in_the_feed_are_ignored(self):
        junk = ["V1.2.x", "1.2.5", "v1.2.9", "V1.2.1-rc1", "V1.2.1\n", "release-9", "latest", "V", "V1.2.1.1.1",
                "V" + "9" * 30 + "x", "other-V9.0.0", ""]
        for tag in junk:
            with self.subTest(tag=tag):
                feed = [rel(tag, names=[A]), rel("V1.2.1"), rel("V1.2.0")]
                for implementation in ("new", "v1.2.0"):
                    got = ask(implementation, feed, "V1.2.0", "win")
                    self.assertEqual(got, available("V1.2.1", A), (implementation, tag))

    def test_a_non_string_or_missing_tag_is_ignored_not_fatal(self):
        feed = [{"tag_name": None}, {"tag_name": 7}, {}, None, "V9.9.9", rel("V1.2.1")]
        for implementation in ("new", "v1.2.0"):
            self.assertEqual(ask(implementation, feed, "V1.2.0", "win"), available("V1.2.1", A))

    def test_a_draft_is_ignored(self):
        feed = [rel("V1.9.0", draft=True), rel("V1.2.1"), rel("V1.2.0")]
        self.assert_all(feed, "V1.2.0", "win", available("V1.2.1", A), available("V1.2.1", A),
                        available("V1.2.1", A))

    def test_a_prerelease_is_never_offered(self):
        """Policy: the prerelease flag is a channel; a prerelease is not offered to anybody."""
        feed = [rel("V1.9.0", prerelease=True), rel("V1.2.1"), rel("V1.2.0")]
        self.assert_all(feed, "V1.2.0", "win", available("V1.2.1", A), available("V1.2.1", A),
                        available("V1.2.1", A))
        only_pre = [rel("V1.9.0", prerelease=True), rel("V1.2.0")]
        self.assert_all(only_pre, "V1.2.0", "win", current("V1.2.0"), current("V1.2.0"),
                        current("V1.2.0"))

    def test_unrelated_assets_are_ignored(self):
        noise = ["notes.txt", "SHA256SUMS.txt", "OmniSuite-V1.2.1-%s.sha256" % WIN, "LumaSuite-V1.2.1-" + WIN,
                 "OmniSuite-V1.2.1-diag1-" + WIN, "OmniSuite-V1.2.0-" + WIN, "OmniSuite-Windows.zip",
                 "OmniSuite-V1.2.1-Windows-x86_64.exe", "OmniSuite-V1.2.1-Windows-x86_64.zip.asc",
                 "Source code (zip)"]
        feed = [rel("V1.2.1", names=noise + [A]), rel("V1.2.0")]
        # The frozen V1.1.0 matcher looks only at the tail of a file name, so it cannot be asked
        # this question fairly: see test_v110_matches_by_tail_only below. NEW and V1.2.0 can.
        self.assert_all(feed, "V1.2.0", "win", available("V1.2.1", A), available("V1.2.1", A))
        only_noise = [rel("V1.2.1", names=noise)]
        self.assert_all(only_noise, "V1.2.0", "win", available("V1.2.1", None),
                        available("V1.2.1", None))

    def test_v110_matches_by_tail_only(self):
        """A recorded property of the frozen V1.1.0 client, not a defect this build can fix: it
        takes the first asset ending in its platform's tail, whoever's name it carries. Nothing
        but this product's artifacts may therefore ever be attached to a release in this
        repository, which is why the publish step collects and verifies only this product's."""
        feed = [rel("V1.2.1", names=["LumaSuite-V1.2.1-" + WIN, A])]
        self.assertEqual(ask("v1.1.0", feed, "V1.1.0", "win")["asset"], "LumaSuite-V1.2.1-" + WIN)
        self.assertEqual(ask("new", feed, "V1.2.0", "win")["asset"], A)
        self.assertEqual(ask("v1.2.0", feed, "V1.2.0", "win")["asset"], A)

    def test_a_download_address_outside_the_repository_is_never_selected(self):
        for url in ("https://evil.example/" + A, "http://github.com/Hall-Research-Technologies/OmniSuite/x/" + A,
                    "https://github.com/someone-else/OmniSuite/releases/download/V1.2.1/" + A):
            feed = [rel("V1.2.1", assets=[asset(A, "V1.2.1", url)])]
            for implementation in ("new", "v1.2.0"):
                with self.subTest(implementation=implementation, url=url):
                    self.assertEqual(ask(implementation, feed, "V1.2.0", "win"),
                                     available("V1.2.1", None))

    # ---- platforms
    def test_wrong_os_and_architecture_assets_are_never_selected(self):
        feed = [rel("V1.2.1", tails=(MAC,)), rel("V1.2.0")]
        self.assert_all(feed, "V1.2.0", "win", available("V1.2.1", None), available("V1.2.1", None),
                        available("V1.2.1", None))
        feed = [rel("V1.2.1", tails=(WIN,)), rel("V1.2.0")]
        self.assert_all(feed, "V1.2.0", "mac", available("V1.2.1", None), available("V1.2.1", None),
                        available("V1.2.1", None))

    def test_platforms_that_are_not_distributed_get_no_package_from_the_new_updater(self):
        legacy_names = ["OmniSuite-V1.2.1-macOS-x86_64.zip", "OmniSuite-V1.2.1-Ubuntu-x86_64.tar.gz"]
        for feed in ([rel("V1.2.1")], [rel("V1.2.1", names=[A, M] + legacy_names)]):
            for platform in ("intel", "linux", "linux_arm"):
                with self.subTest(platform=platform):
                    got = ask("new", feed, "V1.2.0", platform)
                    self.assertEqual(got, available("V1.2.1", None))
                    body = run_new(Transport(feed), "V1.2.0", PLATFORMS[platform])
                    self.assertIn("no supported update package", body["package_message"])
                    self.assertTrue(body["release_url"].startswith("https://github.com/"))

    def test_v120_on_a_platform_that_is_no_longer_built_is_offered_the_release_page_only(self):
        """What an installed V1.2.0 does with a V1.2.1 release on macOS Intel / Linux: it reports
        the update and finds no asset of its own, so it can only show the release page."""
        feed = [rel("V1.2.1")]
        for platform in ("intel", "linux", "linux_arm"):
            with self.subTest(platform=platform):
                self.assertEqual(ask("v1.2.0", feed, "V1.2.0", platform), available("V1.2.1", None))
                self.assertEqual(ask("v1.1.0", feed, "V1.1.0", platform), available("V1.2.1", None))

    def test_the_unsupported_platform_message_is_only_set_when_it_is_true(self):
        body = run_new(Transport([rel("V1.2.1", tails=(MAC,))]), "V1.2.0", PLATFORMS["win"])
        self.assertIn("no download for this computer", body["package_message"])
        body = run_new(Transport([rel("V1.2.1")]), "V1.2.0", PLATFORMS["win"])
        self.assertIsNone(body["package_message"])
        body = run_new(Transport([rel("V1.2.1")]), "V1.2.1", PLATFORMS["linux"])
        self.assertIsNone(body["package_message"], "nothing to download when no update exists")

    # ---- truthfulness under failure
    def test_a_network_failure_is_unable_to_check_never_up_to_date(self):
        for error in (ConnectionError("down"), TimeoutError("slow"), OSError("no route")):
            with self.subTest(error=type(error).__name__):
                for implementation in ("new", "v1.2.0", "v1.1.0"):
                    got = ask(implementation, None, "V1.2.0", "win", raises=error)
                    self.assertEqual(got, UNABLE_RESULT, implementation)
                    self.assertNotEqual(got["status"], CURRENT)

    def test_an_http_error_is_unable_to_check_never_up_to_date(self):
        for status in (403, 429, 500, 502, 503):
            with self.subTest(status=status):
                for implementation in ("new", "v1.2.0", "v1.1.0"):
                    got = ask(implementation, [rel("V1.2.1")], "V1.2.0", "win", status=status)
                    self.assertEqual(got, UNABLE_RESULT, implementation)

    def test_no_releases_published_is_unable_to_check(self):
        for implementation in ("new", "v1.2.0"):
            self.assertEqual(ask(implementation, [], "V1.2.0", "win"), UNABLE_RESULT)

    def test_an_invalid_response_is_safe_and_never_up_to_date(self):
        for payload in ({}, {"message": "Not Found"}, "oops", 7, None, [None], [{}], [[]],
                        [rel("V1.2.1-rc1")], [rel("v1.2.1")]):
            with self.subTest(payload=repr(payload)[:40]):
                for implementation in ("new", "v1.2.0"):
                    got = ask(implementation, payload, "V1.2.0", "win")
                    self.assertEqual(got, UNABLE_RESULT, implementation)
        for implementation in ("new", "v1.2.0", "v1.1.0"):
            self.assertEqual(ask(implementation, [rel("V1.2.1")], "V1.2.0", "win", bad_json=True),
                             UNABLE_RESULT, implementation)

    def test_a_failed_check_after_a_good_one_is_marked_stale_not_current(self):
        with mock.patch.object(srv, "_app_version", lambda: "V1.2.0"), \
                mock.patch.object(srv.platform, "system", return_value="Windows"), \
                mock.patch.object(srv.platform, "machine", return_value="AMD64"):
            with srv._update_check_lock:
                srv._update_check_cache["checked_at"] = 0.0
                srv._update_check_cache["result"] = None
            fetch = _real_fetch_release_feed()
            with mock.patch.object(srv, "_fetch_release_feed", fetch), \
                    mock.patch.object(srv.requests, "get", Transport([rel("V1.2.1")]).get):
                good = srv._update_check(force=True)
            with mock.patch.object(srv, "_fetch_release_feed", fetch), \
                    mock.patch.object(srv.requests, "get", Transport(raises=ConnectionError("x")).get):
                later = srv._update_check(force=True)
        self.assertEqual(good["status"], OFFERED)
        self.assertTrue(later["stale"])
        self.assertEqual(later["status"], OFFERED, "an update that was seen is not forgotten into 'current'")
        self.assertFalse(later["ok"])

    # ---- version handling
    def test_the_V_and_v_prefix_and_none_are_the_same_installed_version(self):
        feed = [rel("V1.2.1"), rel("V1.2.0")]
        for installed in ("V1.2.0", "v1.2.0", "1.2.0", " V1.2.0 "):
            for implementation in ("new", "v1.2.0"):
                with self.subTest(installed=installed, implementation=implementation):
                    self.assertEqual(ask(implementation, feed, installed, "win"), available("V1.2.1", A))
        for installed in ("V1.2.1", "v1.2.1", "1.2.1"):
            for implementation in ("new", "v1.2.0"):
                self.assertEqual(ask(implementation, feed, installed, "win"), current("V1.2.1"))

    def test_a_lower_case_v_tag_in_the_feed_is_not_a_release_of_this_product(self):
        feed = [rel("v1.2.1"), rel("V1.2.0")]
        for implementation in ("new", "v1.2.0"):
            self.assertEqual(ask(implementation, feed, "V1.2.0", "win"), current("V1.2.0"))

    def test_1_2_10_is_newer_than_1_2_9_and_never_the_reverse(self):
        names = lambda core: ["OmniSuite-V%s-%s" % (core, WIN)]
        feed = [rel("V1.2.9", names=names("1.2.9")), rel("V1.2.10", names=names("1.2.10"))]
        for implementation in ("new", "v1.2.0"):
            with self.subTest(implementation=implementation):
                self.assertEqual(ask(implementation, feed, "V1.2.9", "win"),
                                 available("V1.2.10", "OmniSuite-V1.2.10-" + WIN))
                self.assertEqual(ask(implementation, feed, "V1.2.10", "win"), current("V1.2.10"))
        # the older one does not win because it is listed first
        self.assertEqual(ask("new", feed, "V1.2.0", "win")["latest"], "V1.2.10")

    def test_the_newest_release_is_the_highest_number_not_the_first_listed(self):
        feed = [rel("V1.1.5", names=["OmniSuite-V1.1.5-" + WIN]), rel("V1.2.1"), rel("V1.2.0")]
        for implementation in ("new", "v1.2.0"):
            self.assertEqual(ask(implementation, feed, "V1.2.0", "win"), available("V1.2.1", A))

    def test_release_assets_that_name_another_version_are_not_offered(self):
        feed = [rel("V1.2.1", names=["OmniSuite-V1.2.2-" + WIN, "OmniSuite-V1.2.0-" + WIN])]
        for implementation in ("new", "v1.2.0"):
            self.assertEqual(ask(implementation, feed, "V1.2.0", "win"), available("V1.2.1", None))

    # ---- what installed apps do
    def test_installed_v110_reads_latest_and_that_is_what_gets_published(self):
        transport = Transport([rel("V1.2.1"), rel("V1.2.0")])
        run_v110(transport, "V1.1.0", PLATFORMS["win"])
        self.assertEqual(transport.urls,
                         ["https://api.github.com/repos/Hall-Research-Technologies/omniSuite/releases/latest"])
        transport = Transport([rel("V1.2.1")])
        run_v120(transport, "V1.2.0", PLATFORMS["win"])
        self.assertEqual(len(transport.urls), 1)
        self.assertIn("/releases?per_page=100", transport.urls[0])
        transport = Transport([rel("V1.2.1")])
        run_new(transport, "V1.2.0", PLATFORMS["win"])
        self.assertIn("/releases?per_page=100", transport.urls[0])

    def test_a_draft_latest_is_invisible_to_v110_because_github_does_not_serve_drafts(self):
        """A release created as a draft is not 'latest'. Only after it is published by hand does an
        installed V1.1.0 see it, which is the order the release process depends on."""
        feed = [rel("V1.2.1", draft=True), rel("V1.1.0")]
        self.assertEqual(ask("v1.1.0", feed, "V1.1.0", "win"), current("V1.1.0"))
        feed[0]["draft"] = False
        self.assertEqual(ask("v1.1.0", feed, "V1.1.0", "win"), available("V1.2.1", A))


if __name__ == "__main__":
    unittest.main()
