"""Release isolation: two products, one repository, one releases page.

OmniSuite (tags V1.x.x) and OmniSuite Plus (tags plus-v1.x.x) are maintained
independently but publish to the same GitHub repository. These tests hold the
rules that keep each from offering, building or publishing the other:

* identity is decided from tag and artifact names, never from GitHub's "latest"
  release and never from the prerelease flag;
* the in-app updater only ever offers its own product's releases;
* the workflow's identity guard refuses a tag on the wrong line, a tag that
  disagrees with VERSION, an unverifiable branch, a missing release-notes file
  and an ambiguous identity, and a diagnostic build can never publish.

This file is identical on both product lines. The product a checkout is comes
from `omni_product`; each test that depends on it says so, and the cross-product
tests run both identities regardless of which line they are on.
"""
import importlib.util
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

try:
    from . import test_usb_integration as base
except ImportError:                                   # loaded by path, no package
    import test_usb_integration as base

import omni_product
import omni_release
from omni_release import IdentityError, NoCompatibleRelease, PRODUCTS

srv = base.srv
ROOT = Path(__file__).resolve().parent.parent
OMNI = PRODUCTS["omnisuite"]
PLUS = PRODUCTS["omnisuite-plus"]
BASE_URL = "https://github.com/Hall-Research-Technologies/omniSuite/releases"


def _load_tool():
    spec = importlib.util.spec_from_file_location(
        "release_identity_under_test", ROOT / "tools" / "release_identity.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


tool = _load_tool()


def make_assets(product, core, platforms=("Windows-x86_64.zip", "macOS-arm64.zip",
                                          "macOS-x86_64.zip", "Ubuntu-x86_64.tar.gz")):
    out = []
    for tail in platforms:
        name = product.artifact_name(core, tail)
        out.append({"name": name, "size": 1,
                    "browser_download_url": "%s/download/%s/%s" % (BASE_URL, product.tag_for(core), name)})
    return out


def make_release(product, core, draft=False, prerelease=False, assets=True):
    tag = product.tag_for(core)
    return {"tag_name": tag, "draft": draft, "prerelease": prerelease,
            "html_url": "%s/tag/%s" % (BASE_URL, tag),
            "assets": make_assets(product, core) if assets else []}


class ProductIdentityTests(unittest.TestCase):
    def test_the_checkout_names_a_known_product(self):
        self.assertIn(omni_product.PRODUCT_ID, PRODUCTS)
        self.assertIs(omni_release.active_product(), PRODUCTS[omni_product.PRODUCT_ID])

    def test_each_product_recognises_only_its_own_tags(self):
        good = {OMNI: ["V1.1.0", "V1.1.1", "V1.0", "V0.9.1.1", "V10.20.30"],
                PLUS: ["plus-v1.2.0", "plus-v1.2.1", "plus-v2.0"]}
        for product, tags in good.items():
            for tag in tags:
                with self.subTest(product=product.id, tag=tag):
                    self.assertIsNotNone(product.parse_tag(tag))
                    others = [p for p in PRODUCTS.values() if p is not product]
                    for other in others:
                        self.assertIsNone(other.parse_tag(tag), "%s accepted %s" % (other.id, tag))
                    self.assertEqual([product], omni_release.owners_of_tag(tag))

    def test_a_tag_is_never_repaired_into_a_release(self):
        """A release that had to be repaired to be understood was not understood."""
        for tag in ("v1.1.1", "V1.1.1-rc1", "V1", "1.1.1", "V1.1.1.1.1", "plus-V1.2.0",
                    "Plus-v1.2.0", "plus-1.2.0", "plusv1.2.0", "plus-v1", "release-1.1.1",
                    "", " V1.1.1", "V1.1.1 ", "V1..1", "V1.1.1\n", "main", None, 7):
            with self.subTest(tag=tag):
                self.assertEqual(omni_release.owners_of_tag(tag), [])

    def test_a_tag_cannot_belong_to_two_products(self):
        for tag in ("V1.1.1", "plus-v1.2.0", "plus-v9.9.9", "V9.9.9", "v1.2.0"):
            self.assertLessEqual(len(omni_release.owners_of_tag(tag)), 1, tag)

    def test_artifact_prefixes_are_distinct_even_though_one_contains_the_other(self):
        self.assertTrue(PLUS.artifact_prefix.startswith(OMNI.artifact_prefix.rstrip("-")))
        name_plus = PLUS.artifact_name("1.2.0", "macOS-arm64.zip")
        name_base = OMNI.artifact_name("1.1.1", "macOS-arm64.zip")
        self.assertEqual(name_plus, "OmniSuite-Plus-V1.2.0-macOS-arm64.zip")
        self.assertEqual(name_base, "OmniSuite-V1.1.1-macOS-arm64.zip")
        self.assertIsNone(OMNI.parse_asset(name_plus), "OmniSuite claimed a Plus artifact")
        self.assertIsNone(PLUS.parse_asset(name_base), "Plus claimed an OmniSuite artifact")
        self.assertEqual(PLUS.parse_asset(name_plus), {"version": "1.2.0", "platform": "macos-arm64"})
        self.assertEqual(OMNI.parse_asset(name_base), {"version": "1.1.1", "platform": "macos-arm64"})

    def test_diagnostic_artifact_names_are_not_release_names(self):
        for product in PRODUCTS.values():
            name = product.artifact_prefix + "V1.1.1-diag2-macOS-arm64.zip"
            self.assertIsNone(product.parse_asset(name), name)

    def test_versions_compare_numerically(self):
        self.assertGreater(omni_release.version_tuple("1.0.10"), omni_release.version_tuple("1.0.9"))
        self.assertEqual(omni_release.version_tuple("1.0"), omni_release.version_tuple("1.0.0"))
        self.assertGreater(omni_release.version_tuple("1.10"), omni_release.version_tuple("1.9.9"))

    def test_core_of_refuses_what_is_not_a_version(self):
        self.assertEqual(omni_release.core_of("V1.1.1"), "1.1.1")
        self.assertEqual(omni_release.core_of("plus-v1.2.0"), "1.2.0")
        for bad in ("", None, "latest", "V1", "1.1.1-rc1", "V1.x"):
            with self.subTest(bad=bad), self.assertRaises(IdentityError):
                omni_release.core_of(bad)


class SelectReleaseTests(unittest.TestCase):
    """Choosing this product's newest release out of one shared feed."""

    def mixed_feed(self):
        return [make_release(PLUS, "1.2.1"), make_release(OMNI, "1.1.1"),
                make_release(PLUS, "1.2.0"), make_release(OMNI, "1.1.0")]

    def test_each_product_gets_only_its_own_newest(self):
        feed = self.mixed_feed()
        self.assertEqual(omni_release.select_release(feed, OMNI)[1], "1.1.1")
        self.assertEqual(omni_release.select_release(feed, PLUS)[1], "1.2.1")
        self.assertEqual(omni_release.select_release(feed, OMNI)[0]["tag_name"], "V1.1.1")
        self.assertEqual(omni_release.select_release(feed, PLUS)[0]["tag_name"], "plus-v1.2.1")

    def test_a_numerically_higher_other_product_never_wins(self):
        """OmniSuite 9.0.0 must not outrank Plus 1.2.1 for Plus, nor the reverse."""
        feed = [make_release(OMNI, "9.0.0"), make_release(PLUS, "1.2.1")]
        self.assertEqual(omni_release.select_release(feed, PLUS)[1], "1.2.1")
        feed = [make_release(PLUS, "9.0.0"), make_release(OMNI, "1.1.1")]
        self.assertEqual(omni_release.select_release(feed, OMNI)[1], "1.1.1")

    def test_the_newest_is_the_highest_version_not_the_first_in_the_feed(self):
        feed = [make_release(OMNI, "1.0.9"), make_release(OMNI, "1.0.10"), make_release(OMNI, "1.0.2")]
        self.assertEqual(omni_release.select_release(feed, OMNI)[1], "1.0.10")
        self.assertEqual(omni_release.select_release(list(reversed(feed)), OMNI)[1], "1.0.10")

    def test_drafts_and_prereleases_are_not_offered_and_do_not_decide_identity(self):
        feed = [make_release(OMNI, "9.9.9", prerelease=True), make_release(OMNI, "9.9.8", draft=True),
                make_release(OMNI, "1.1.1")]
        self.assertEqual(omni_release.select_release(feed, OMNI)[1], "1.1.1")
        # A prerelease Plus tag is still Plus: the flag is a channel, not a product.
        only_pre = [make_release(PLUS, "1.2.0", prerelease=True)]
        with self.assertRaises(NoCompatibleRelease) as caught:
            omni_release.select_release(only_pre, PLUS)
        self.assertEqual(caught.exception.rejected["not_offered"], 1)
        self.assertEqual(caught.exception.rejected["other_product"], 0)
        with self.assertRaises(NoCompatibleRelease) as caught:
            omni_release.select_release(only_pre, OMNI)
        self.assertEqual(caught.exception.rejected["other_product"], 1,
                         "a prerelease Plus tag must still be recognised as Plus")

    def test_a_release_that_mixes_products_is_refused_for_both(self):
        mixed = make_release(OMNI, "1.1.1")
        mixed["assets"] = make_assets(OMNI, "1.1.1") + make_assets(PLUS, "1.2.0")[:1]
        with self.assertRaises(NoCompatibleRelease) as caught:
            omni_release.select_release([mixed], OMNI)
        self.assertEqual(caught.exception.rejected["mixed_assets"], 1)
        mixed_plus = make_release(PLUS, "1.2.0")
        mixed_plus["assets"] = make_assets(PLUS, "1.2.0") + make_assets(OMNI, "1.1.1")[:1]
        with self.assertRaises(NoCompatibleRelease):
            omni_release.select_release([mixed_plus], PLUS)

    def test_malformed_releases_are_skipped_not_fatal(self):
        junk = [None, 7, "V1.1.1", [], {}, {"tag_name": None}, {"tag_name": ""},
                {"tag_name": 11}, {"tag_name": "latest"}, {"tag_name": "V1.1.1-rc1"},
                {"tag_name": "v1.1.1"}, {"tag_name": "plus-V1.2.0"}]
        feed = junk + [make_release(OMNI, "1.1.0")]
        self.assertEqual(omni_release.select_release(feed, OMNI)[1], "1.1.0")
        with self.assertRaises(NoCompatibleRelease) as caught:
            omni_release.select_release(junk, OMNI)
        self.assertEqual(caught.exception.rejected["malformed"], len(junk))

    def test_assets_that_are_not_a_list_do_not_break_selection(self):
        for assets in (None, "x", 5, {}, [None, 3, "x"]):
            release = make_release(OMNI, "1.1.1")
            release["assets"] = assets
            with self.subTest(assets=assets):
                self.assertEqual(omni_release.select_release([release], OMNI)[1], "1.1.1")

    def test_an_empty_or_foreign_feed_is_no_compatible_release(self):
        with self.assertRaises(NoCompatibleRelease):
            omni_release.select_release([], OMNI)
        with self.assertRaises(NoCompatibleRelease):
            omni_release.select_release([make_release(PLUS, "1.2.0")], OMNI)
        with self.assertRaises(NoCompatibleRelease):
            omni_release.select_release([make_release(OMNI, "1.1.0")], PLUS)

    def test_a_feed_that_is_not_a_list_is_an_error_not_a_release(self):
        for feed in (None, {}, {"tag_name": "V1.1.1"}, "V1.1.1", 3):
            with self.subTest(feed=feed), self.assertRaises(ValueError):
                omni_release.select_release(feed, OMNI)


class UpdaterBase(base.ServerTestBase):
    """The real /api/update_check, fed a scripted GitHub, as either product."""

    def setUp(self):
        super().setUp()
        self._reset()
        self.addCleanup(self._reset)
        self.fetches = []

    def _reset(self):
        with srv._update_check_lock:
            srv._update_check_cache["checked_at"] = 0.0
            srv._update_check_cache["result"] = None

    def feed(self, releases=None, raises=None):
        def fetch(*args, **kwargs):
            self.fetches.append(1)
            if raises is not None:
                raise raises
            return releases
        self.patch_srv("_fetch_release_feed", fetch)
        self._reset()

    def check(self, product, installed, platform=("Windows", "AMD64")):
        self.patch_srv("_app_version", lambda: installed)
        system, machine = platform
        self._reset()
        with mock.patch.object(omni_product, "PRODUCT_ID", product.id), \
                mock.patch.object(srv.platform, "system", return_value=system), \
                mock.patch.object(srv.platform, "machine", return_value=machine):
            return self.client.get("/api/update_check?force=1").get_json()


class UpdaterFeedTests(UpdaterBase):
    def check_without_reset(self, product, installed):
        self.patch_srv("_app_version", lambda: installed)
        with mock.patch.object(omni_product, "PRODUCT_ID", product.id):
            return self.client.get("/api/update_check?force=1").get_json()

    def feed_both(self):
        self.feed([make_release(PLUS, "1.2.1"), make_release(OMNI, "1.1.1"),
                   make_release(PLUS, "1.2.0"), make_release(OMNI, "1.1.0")])

    def test_omnisuite_is_offered_only_omnisuite_releases(self):
        self.feed_both()
        body = self.check(OMNI, "V1.1.0")
        self.assertEqual(body["status"], srv.UPDATE_AVAILABLE)
        self.assertEqual(body["latest_version"], "V1.1.1")
        self.assertEqual(body["product"], "omnisuite")
        self.assertTrue(body["asset"]["name"].startswith("OmniSuite-V1.1.1-"))
        self.assertNotIn("Plus", body["asset"]["name"])
        self.assertTrue(body["release_url"].endswith("/tag/V1.1.1"))

    def test_plus_is_offered_only_plus_releases(self):
        self.feed_both()
        body = self.check(PLUS, "V1.2.0")
        self.assertEqual(body["status"], srv.UPDATE_AVAILABLE)
        self.assertEqual(body["latest_version"], "V1.2.1")
        self.assertEqual(body["product"], "omnisuite-plus")
        self.assertTrue(body["asset"]["name"].startswith("OmniSuite-Plus-V1.2.1-"))
        self.assertTrue(body["release_url"].endswith("/tag/plus-v1.2.1"))

    def test_a_higher_number_on_the_other_product_is_never_an_update(self):
        """The case the repository-wide 'latest' would have got wrong."""
        self.feed([make_release(PLUS, "1.2.1"), make_release(OMNI, "1.1.0")])
        body = self.check(OMNI, "V1.1.0")
        self.assertEqual(body["status"], srv.UPDATE_CURRENT)
        self.assertFalse(body["update_available"])
        self.feed([make_release(OMNI, "1.9.0"), make_release(PLUS, "1.2.0")])
        body = self.check(PLUS, "V1.2.0")
        self.assertEqual(body["status"], srv.UPDATE_CURRENT)
        self.assertFalse(body["update_available"])

    def test_nothing_compatible_is_unable_to_check_never_up_to_date(self):
        for product, other in ((OMNI, PLUS), (PLUS, OMNI)):
            with self.subTest(product=product.id):
                self.feed([make_release(other, "1.2.0")])
                body = self.check(product, "V1.1.0")
                self.assertEqual(body["status"], srv.UPDATE_UNABLE)
                self.assertFalse(body["ok"])
                self.assertFalse(body["update_available"])
                self.assertEqual(body["error_summary"], "NoCompatibleRelease")
                self.assertEqual(body["release_url"], srv.GITHUB_RELEASES_URL,
                                 "the releases page stays reachable")

    def test_an_empty_feed_is_unable_to_check(self):
        self.feed([])
        self.assertEqual(self.check(OMNI, "V1.1.0")["status"], srv.UPDATE_UNABLE)

    def test_a_feed_that_is_not_a_list_is_unable_to_check(self):
        for payload in (None, {"message": "Not Found"}, "oops", 12):
            with self.subTest(payload=payload):
                self.feed(payload)
                body = self.check(OMNI, "V1.1.0")
                self.assertEqual(body["status"], srv.UPDATE_UNABLE)
                self.assertFalse(body["update_available"])

    def test_malformed_releases_among_good_ones_do_not_stop_the_check(self):
        self.feed([None, {"tag_name": None}, {"tag_name": "v1.1.9"}, 5,
                   make_release(OMNI, "1.1.1")])
        body = self.check(OMNI, "V1.1.0")
        self.assertEqual(body["status"], srv.UPDATE_AVAILABLE)
        self.assertEqual(body["latest_version"], "V1.1.1")

    def test_a_prerelease_is_not_offered_to_either_product(self):
        for product in (OMNI, PLUS):
            with self.subTest(product=product.id):
                self.feed([make_release(product, "9.0.0", prerelease=True),
                           make_release(product, "1.1.1" if product is OMNI else "1.2.1")])
                body = self.check(product, "V1.1.0" if product is OMNI else "V1.2.0")
                self.assertTrue(body["latest_version"] in ("V1.1.1", "V1.2.1"), body["latest_version"])

    def test_a_network_failure_is_not_fatal_for_either_product(self):
        for product in (OMNI, PLUS):
            with self.subTest(product=product.id):
                self.feed(raises=OSError("offline"))
                body = self.check(product, "V1.1.0")
                self.assertEqual(body["status"], srv.UPDATE_UNABLE)
                self.assertFalse(body["ok"])

    def test_the_other_products_cached_answer_is_never_served(self):
        """A last-good answer is only ever this product's own."""
        self.feed_both()
        self.assertEqual(self.check(OMNI, "V1.1.0")["latest_version"], "V1.1.1")

        def offline(*args, **kwargs):
            raise OSError("offline")
        self.patch_srv("_fetch_release_feed", offline)      # without clearing the cache
        stale = self.check_without_reset(OMNI, "V1.1.0")
        self.assertTrue(stale["stale"])
        self.assertEqual(stale["latest_version"], "V1.1.1")
        self.assertEqual(stale["product"], "omnisuite")


class UpdaterAssetTests(UpdaterBase):
    def test_a_release_with_no_assets_still_reports_the_update(self):
        for product, installed, core in ((OMNI, "V1.1.0", "1.1.1"), (PLUS, "V1.2.0", "1.2.1")):
            with self.subTest(product=product.id):
                self.feed([make_release(product, core, assets=False)])
                body = self.check(product, installed)
                self.assertEqual(body["status"], srv.UPDATE_AVAILABLE)
                self.assertIsNone(body["asset"])
                self.assertIsNone(body["asset_url"])

    def test_a_missing_platform_asset_offers_none_rather_than_another_platform(self):
        release = make_release(OMNI, "1.1.1", assets=False)
        release["assets"] = make_assets(OMNI, "1.1.1", platforms=("macOS-arm64.zip",))
        self.feed([release])
        self.assertIsNone(self.check(OMNI, "V1.1.0", ("Windows", "AMD64"))["asset"])
        self.assertEqual(self.check(OMNI, "V1.1.0", ("Darwin", "arm64"))["asset"]["platform"], "macos-arm64")
        self.assertIsNone(self.check(OMNI, "V1.1.0", ("Darwin", "x86_64"))["asset"],
                          "the arm64 build must never be offered to an Intel Mac")

    def test_each_platform_gets_its_own_product_build(self):
        cases = [(("Windows", "AMD64"), "windows-x86_64"), (("Linux", "x86_64"), "ubuntu-x86_64"),
                 (("Darwin", "arm64"), "macos-arm64"), (("Darwin", "x86_64"), "macos-x86_64")]
        for product, installed, core in ((OMNI, "V1.1.0", "1.1.1"), (PLUS, "V1.2.0", "1.2.1")):
            self.feed([make_release(product, core)])
            for platform, key in cases:
                with self.subTest(product=product.id, platform=platform):
                    asset = self.check(product, installed, platform)["asset"]
                    self.assertEqual(asset["platform"], key)
                    self.assertTrue(asset["name"].startswith(product.artifact_prefix + "V" + core))

    def test_the_other_products_asset_is_never_selected(self):
        """Selection is strict: a prefix match alone is not enough."""
        with mock.patch.object(omni_product, "PRODUCT_ID", "omnisuite"), \
                mock.patch.object(srv.platform, "system", return_value="Darwin"), \
                mock.patch.object(srv.platform, "machine", return_value="arm64"):
            plus_assets = make_assets(PLUS, "1.2.0")
            self.assertIsNone(srv._select_platform_asset(plus_assets))
        with mock.patch.object(omni_product, "PRODUCT_ID", "omnisuite-plus"), \
                mock.patch.object(srv.platform, "system", return_value="Darwin"), \
                mock.patch.object(srv.platform, "machine", return_value="arm64"):
            self.assertIsNone(srv._select_platform_asset(make_assets(OMNI, "1.1.1")))

    def test_an_asset_whose_version_disagrees_with_its_release_is_ignored(self):
        release = make_release(OMNI, "1.1.1", assets=False)
        release["assets"] = make_assets(OMNI, "1.0.0")        # a stale file on a new release
        self.feed([release])
        body = self.check(OMNI, "V1.1.0")
        self.assertEqual(body["status"], srv.UPDATE_AVAILABLE)
        self.assertIsNone(body["asset"])

    def test_an_asset_url_outside_the_project_is_still_refused(self):
        release = make_release(PLUS, "1.2.1", assets=False)
        release["assets"] = [{"name": PLUS.artifact_name("1.2.1", "Windows-x86_64.zip"),
                              "browser_download_url": "https://evil.example/x.zip"}]
        self.feed([release])
        self.assertIsNone(self.check(PLUS, "V1.2.0")["asset"])


class UpdaterVersionTests(UpdaterBase):
    def status(self, product, installed, core):
        self.feed([make_release(product, core)])
        return self.check(product, installed)["status"]

    def test_the_comparison_matrix_for_both_products(self):
        cases = [("V1.0.7", "1.0.6", srv.UPDATE_CURRENT), ("V1.0.7", "1.0.7", srv.UPDATE_CURRENT),
                 ("V1.0.7", "1.0.8", srv.UPDATE_AVAILABLE), ("V1.0.9", "1.0.10", srv.UPDATE_AVAILABLE),
                 ("V1.0.10", "1.0.9", srv.UPDATE_CURRENT), ("1.0.7", "1.0.7", srv.UPDATE_CURRENT),
                 ("v1.0.7", "1.0.8", srv.UPDATE_AVAILABLE)]
        for product in (OMNI, PLUS):
            for installed, core, expected in cases:
                with self.subTest(product=product.id, installed=installed, latest=core):
                    self.assertEqual(self.status(product, installed, core), expected)

    def test_a_build_ahead_of_the_newest_release_is_current_not_an_error(self):
        for product in (OMNI, PLUS):
            with self.subTest(product=product.id):
                self.feed([make_release(product, "1.0.0")])
                body = self.check(product, "V1.5.0")
                self.assertEqual(body["status"], srv.UPDATE_CURRENT)
                self.assertTrue(body["ok"])

    def test_a_diagnostic_build_is_not_offered_the_release_it_was_built_for(self):
        for product, installed, core in ((OMNI, "V1.1.1-diag2", "1.1.1"),
                                         (PLUS, "V1.2.0-diag4", "1.2.0")):
            with self.subTest(product=product.id):
                self.assertEqual(self.status(product, installed, core), srv.UPDATE_CURRENT)

    def test_an_unparseable_installed_version_is_never_an_update(self):
        for installed in ("", "unknown", "dev"):
            with self.subTest(installed=installed):
                self.assertNotEqual(self.status(OMNI, installed, "9.9.9"), srv.UPDATE_AVAILABLE)


class UpdaterNeverUsesRepositoryWideLatestTests(unittest.TestCase):
    def source(self):
        text = (ROOT / "OmniMatrix_upgrade_server_v7_6y.py").read_text(encoding="utf-8")
        start = text.index("# ---------------- release update check ----------------")
        block = text[start:text.index('@app.route("/api/config"', start)]
        return "\n".join(line for line in block.splitlines() if not line.lstrip().startswith("#"))

    def test_the_latest_endpoint_is_not_used(self):
        code = self.source()
        self.assertNotIn("releases/latest", code)
        self.assertNotIn("GITHUB_LATEST_API", code)
        self.assertIn("releases?per_page=100", srv.GITHUB_RELEASES_API)
        self.assertNotIn("latest", srv.GITHUB_RELEASES_API)

    def test_identity_comes_from_the_shared_module_not_from_the_prerelease_flag(self):
        code = self.source()
        self.assertIn("omni_release.select_release", code)
        self.assertNotIn('.get("prerelease")', code)
        self.assertNotIn('.get("draft")', code)

    def test_the_update_check_still_downloads_and_installs_nothing(self):
        code = self.source()
        for forbidden in ("urlretrieve", "shutil.unpack", "zipfile.ZipFile", "subprocess"):
            self.assertNotIn(forbidden, code)


class ReleaseIdentityToolTests(unittest.TestCase):
    """The workflow's guard, with git, files and the network replaced."""

    def resolve(self, product, ref_type, ref_name, *, event="push", version="V1.1.1\n",
                dispatch="", ancestor=True, notes=True):
        return tool.resolve(event, ref_type, ref_name, dispatch, version, product,
                            is_ancestor=lambda branch: ancestor if not callable(ancestor) else ancestor(branch),
                            notes_exist=lambda path: notes if not callable(notes) else notes(path))

    # ---- tags -------------------------------------------------------------
    def test_a_correct_tag_on_the_right_line_publishes(self):
        got = self.resolve(OMNI, "tag", "V1.1.1", version="V1.1.1\n")
        self.assertEqual((got["product"], got["release_version"], got["publish"]),
                         ("omnisuite", "V1.1.1", "true"))
        self.assertEqual(got["notes_path"], "RELEASE_NOTES_V1.1.1.md")
        got = self.resolve(PLUS, "tag", "plus-v1.2.0", version="V1.2.0")
        self.assertEqual((got["product"], got["release_version"], got["publish"]),
                         ("omnisuite-plus", "V1.2.0", "true"))
        self.assertEqual(got["artifact_prefix"], "OmniSuite-Plus-")

    def test_the_branch_checked_is_the_products_own(self):
        asked = []
        self.resolve(OMNI, "tag", "V1.1.1", ancestor=lambda b: asked.append(b) or True)
        self.resolve(PLUS, "tag", "plus-v1.2.0", version="V1.2.0", ancestor=lambda b: asked.append(b) or True)
        self.assertEqual(asked, ["release/omnisuite-1.1.x", "omnisuite-plus"])

    def test_a_tag_belonging_to_the_other_product_is_refused(self):
        with self.assertRaisesRegex(IdentityError, "belongs to OmniSuite Plus"):
            self.resolve(OMNI, "tag", "plus-v1.2.0", version="V1.2.0")
        with self.assertRaisesRegex(IdentityError, "belongs to OmniSuite"):
            self.resolve(PLUS, "tag", "V1.1.1", version="V1.1.1")

    def test_unrecognised_ambiguous_or_repaired_tags_are_refused(self):
        for product in (OMNI, PLUS):
            for tag in ("v1.1.1", "V1.1.1-rc1", "latest", "plus-V1.2.0", "Plus-v1.2.0", "release-1",
                        "", "V1"):
                with self.subTest(product=product.id, tag=tag), self.assertRaises(IdentityError):
                    self.resolve(product, "tag", tag)

    def test_a_tag_that_disagrees_with_version_is_refused(self):
        with self.assertRaisesRegex(IdentityError, "VERSION"):
            self.resolve(OMNI, "tag", "V1.1.1", version="V1.1.0")
        with self.assertRaisesRegex(IdentityError, "VERSION"):
            self.resolve(OMNI, "tag", "V1.1.1", version="garbage")
        with self.assertRaisesRegex(IdentityError, "VERSION"):
            self.resolve(PLUS, "tag", "plus-v1.2.0", version="")

    def test_a_commit_not_on_the_release_branch_is_refused(self):
        with self.assertRaisesRegex(IdentityError, "not on release/omnisuite-1.1.x"):
            self.resolve(OMNI, "tag", "V1.1.1", ancestor=False)
        with self.assertRaisesRegex(IdentityError, "not on omnisuite-plus"):
            self.resolve(PLUS, "tag", "plus-v1.2.0", version="V1.2.0", ancestor=False)

    def test_missing_release_notes_are_refused(self):
        with self.assertRaisesRegex(IdentityError, "no release notes"):
            self.resolve(OMNI, "tag", "V1.1.1", notes=False)

    def test_notes_are_looked_up_for_the_selected_version(self):
        asked = []
        self.resolve(OMNI, "tag", "V1.1.1", notes=lambda p: asked.append(p) or True)
        self.assertEqual(asked, ["RELEASE_NOTES_V1.1.1.md"])

    def test_a_tag_run_must_be_a_push(self):
        for event in ("workflow_dispatch", "pull_request", ""):
            with self.subTest(event=event), self.assertRaises(IdentityError):
                self.resolve(OMNI, "tag", "V1.1.1", event=event)

    # ---- diagnostic builds -------------------------------------------------
    def test_a_dispatched_diagnostic_build_is_allowed_and_never_publishes(self):
        for product, name in ((OMNI, "V1.1.1-diag1"), (PLUS, "V1.2.0-diag4"), (OMNI, "macfix.3")):
            with self.subTest(product=product.id, name=name):
                got = self.resolve(product, "branch", "release/x", event="workflow_dispatch", dispatch=name)
                self.assertEqual(got["publish"], "false")
                self.assertEqual(got["release_version"], name)
                self.assertEqual(got["notes_path"], "")
                self.assertEqual(got["tag"], "")

    def test_a_diagnostic_build_cannot_be_named_like_a_release(self):
        for name in ("V1.1.1", "v1.1.1", "1.1.1", "V1.2.0", "plus-v1.2.0"):
            with self.subTest(name=name), self.assertRaises(IdentityError):
                self.resolve(OMNI, "branch", "main", event="workflow_dispatch", dispatch=name)

    def test_the_product_is_not_typed_into_the_version(self):
        with self.assertRaisesRegex(IdentityError, "adds its own"):
            self.resolve(PLUS, "branch", "x", event="workflow_dispatch", dispatch="Plus-V1.2.0-diag1")

    def test_an_unsafe_dispatch_version_is_refused_not_escaped(self):
        for name in ("V1;rm -rf /", "V1 diag", "$(id)", "V1\ndiag", "a" * 65, "-x", "V1/../2", "`x`"):
            with self.subTest(name=name), self.assertRaises(IdentityError):
                self.resolve(OMNI, "branch", "x", event="workflow_dispatch", dispatch=name)

    def test_an_empty_dispatch_uses_version_with_a_dev_suffix(self):
        got = self.resolve(OMNI, "branch", "main", event="workflow_dispatch", version="V1.1.0\n")
        self.assertEqual(got["release_version"], "V1.1.0-dev")
        self.assertEqual(got["publish"], "false")

    def test_a_branch_push_builds_but_never_publishes(self):
        got = self.resolve(OMNI, "branch", "main", event="push", version="V1.1.0")
        self.assertEqual((got["publish"], got["release_version"]), ("false", "V1.1.0-dev"))

    def test_an_unknown_event_is_refused(self):
        for event in ("schedule", "pull_request", "release", "", None):
            with self.subTest(event=event), self.assertRaises(IdentityError):
                self.resolve(OMNI, "branch", "x", event=event)

    # ---- artifact names ------------------------------------------------------
    def names(self, product, version):
        base = [product.artifact_name(version, t) if re.fullmatch(r"V?\d[\d.]*", version) else
                product.artifact_prefix + version + "-" + t for t in omni_release.PLATFORM_TAILS]
        return base + [n + ".sha256" for n in base]

    def test_a_complete_set_of_own_artifacts_verifies(self):
        for product, version in ((OMNI, "V1.1.1"), (PLUS, "V1.2.0"), (OMNI, "V1.1.1-diag2")):
            with self.subTest(product=product.id, version=version):
                got = tool.verify_asset_names(self.names(product, version) + ["SHA256SUMS.txt"],
                                              product, version, require_complete=True)
                self.assertEqual(len(got), 8)

    def test_another_products_artifact_is_refused_by_name(self):
        names = self.names(OMNI, "V1.1.1") + [PLUS.artifact_name("1.2.0", "macOS-arm64.zip")]
        with self.assertRaisesRegex(IdentityError, "belongs to OmniSuite Plus"):
            tool.verify_asset_names(names, OMNI, "V1.1.1")
        names = self.names(PLUS, "V1.2.0") + [OMNI.artifact_name("1.1.1", "Windows-x86_64.zip")]
        with self.assertRaisesRegex(IdentityError, "belongs to OmniSuite,"):
            tool.verify_asset_names(names, PLUS, "V1.2.0")

    def test_a_diagnostic_artifact_of_the_other_product_is_refused(self):
        names = [PLUS.artifact_prefix + "V1.2.0-diag4-macOS-arm64.zip"]
        with self.assertRaisesRegex(IdentityError, "not an artifact of OmniSuite"):
            tool.verify_asset_names(names, OMNI, "V1.1.1-diag2")

    def test_a_wrong_version_or_unknown_file_is_refused(self):
        with self.assertRaises(IdentityError):
            tool.verify_asset_names(self.names(OMNI, "V1.1.0"), OMNI, "V1.1.1")
        with self.assertRaises(IdentityError):
            tool.verify_asset_names(["notes.txt"], OMNI, "V1.1.1")

    def test_a_missing_platform_is_refused_when_completeness_is_required(self):
        names = [n for n in self.names(OMNI, "V1.1.1") if "Ubuntu" not in n]
        with self.assertRaisesRegex(IdentityError, "missing release artifact"):
            tool.verify_asset_names(names, OMNI, "V1.1.1", require_complete=True)
        tool.verify_asset_names(names, OMNI, "V1.1.1", require_complete=False)

    # ---- the real command line ------------------------------------------------
    def run_cli(self, **env):
        out = tempfile.NamedTemporaryFile("w+", suffix=".out", delete=False)
        out.close()
        self.addCleanup(lambda: os.path.exists(out.name) and os.unlink(out.name))
        full = {k: v for k, v in os.environ.items() if k not in ("EVENT_NAME", "REF_TYPE", "REF_NAME",
                                                                  "DISPATCH_VERSION", "GITHUB_OUTPUT")}
        full.update(env)
        proc = subprocess.run([sys.executable, str(ROOT / "tools" / "release_identity.py"),
                               "resolve", "--github-output", out.name],
                              capture_output=True, text=True, env=full, cwd=str(ROOT))
        return proc, Path(out.name).read_text(encoding="utf-8")

    def test_the_command_line_writes_outputs_for_a_dispatch(self):
        proc, written = self.run_cli(EVENT_NAME="workflow_dispatch", REF_TYPE="branch",
                                     REF_NAME="x", DISPATCH_VERSION="V9.9.9-diag1")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("publish=false", written)
        self.assertIn("release_version=V9.9.9-diag1", written)
        self.assertIn("product=" + omni_product.PRODUCT_ID, written)

    def test_the_command_line_refuses_a_malformed_tag_and_publishes_nothing(self):
        proc, written = self.run_cli(EVENT_NAME="push", REF_TYPE="tag", REF_NAME="v1.1.1")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("::error::", proc.stdout)
        self.assertEqual(written, "", "a refused run must write no outputs")

    def test_the_command_line_refuses_the_other_products_tag(self):
        other = "plus-v1.2.0" if omni_product.PRODUCT_ID == "omnisuite" else "V1.1.1"
        proc, written = self.run_cli(EVENT_NAME="push", REF_TYPE="tag", REF_NAME=other)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("belongs to", proc.stdout)
        self.assertNotIn("publish=true", written)

    def test_the_command_line_fails_closed_on_an_unknown_event(self):
        proc, written = self.run_cli(EVENT_NAME="schedule", REF_TYPE="branch", REF_NAME="main")
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(written, "")


class BranchAncestryTests(unittest.TestCase):
    """The one guard a real tag would exercise, run against a throwaway repository.

    A tag made on the wrong line must be refused, and the only way to see that
    without publishing is to build the situation: a release branch, a commit on
    it, and a commit that is not.
    """

    def git(self, *args):
        subprocess.run(["git", *args], cwd=self.repo, check=True, capture_output=True, text=True,
                       env=dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.invalid",
                                GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.invalid"))

    def head(self):
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.repo, check=True,
                              capture_output=True, text=True).stdout.strip()

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.repo = folder.name
        self.git("init", "-q", "-b", "main")
        self.git("commit", "-q", "--allow-empty", "-m", "base")
        self.base = self.head()
        self.git("commit", "-q", "--allow-empty", "-m", "on the release branch")
        self.on_branch = self.head()
        # Stand in for the remote-tracking ref the workflow's full checkout provides.
        self.git("update-ref", "refs/remotes/origin/release/x", self.on_branch)
        self.git("checkout", "-q", "-b", "elsewhere", self.base)
        self.git("commit", "-q", "--allow-empty", "-m", "on another line")

    def test_a_commit_on_the_release_branch_is_accepted(self):
        self.git("checkout", "-q", "--detach", self.on_branch)
        self.assertTrue(tool._git_is_ancestor("release/x", cwd=self.repo))
        self.git("checkout", "-q", "--detach", self.base)
        self.assertTrue(tool._git_is_ancestor("release/x", cwd=self.repo),
                        "history below the branch tip is on the branch")

    def test_a_commit_on_another_line_is_refused(self):
        self.assertFalse(tool._git_is_ancestor("release/x", cwd=self.repo))

    def test_a_branch_that_cannot_be_found_is_refused_not_assumed(self):
        self.git("checkout", "-q", "--detach", self.on_branch)
        self.assertFalse(tool._git_is_ancestor("release/does-not-exist", cwd=self.repo))


class WorkflowStructureTests(unittest.TestCase):
    """Source-level checks of the workflow, with comments removed first.

    These prove the text, not the behaviour; the behaviour is the guard tool's,
    tested above, and the workflow is exercised end to end by a dispatch run.
    """

    @classmethod
    def setUpClass(cls):
        raw = (ROOT / ".github" / "workflows" / "build-cross-platform.yml").read_text(encoding="utf-8")
        cls.text = "\n".join(l for l in raw.replace("\r\n", "\n").splitlines()
                             if not l.lstrip().startswith("#"))

    def job(self, name):
        match = re.search(r"^  %s:\n(.*?)(?=^  [A-Za-z0-9_-]+:\n|\Z)" % re.escape(name),
                          self.text, re.S | re.M)
        self.assertIsNotNone(match, "no job named " + name)
        return match.group(1)

    def triggers(self):
        return self.text[self.text.index("on:\n"):self.text.index("jobs:\n")]

    def test_tag_triggers_belong_to_this_products_line_only(self):
        triggers = self.triggers()
        if omni_product.PRODUCT_ID == "omnisuite":
            self.assertIn('"V*"', triggers)
            self.assertNotIn("plus-", triggers)
        else:
            self.assertIn('"plus-*"', triggers)
            self.assertNotIn('"V*"', triggers)
            self.assertNotIn('"v*"', triggers)
            self.assertNotIn("branches:", triggers, "Plus never builds from main")

    def test_every_job_waits_for_the_identity_decision(self):
        self.assertIn("identity", self.text.split("jobs:\n", 1)[1])
        self.assertIn("tools/release_identity.py resolve", self.job("identity"))
        for name in ("test", "build", "publish-release"):
            with self.subTest(job=name):
                self.assertRegex(self.job(name), r"needs:\s*(\[[^\]]*identity[^\]]*\]|identity)")

    def test_publishing_requires_a_verified_tag_and_nothing_else_does(self):
        publish = self.job("publish-release")
        self.assertIn("needs.identity.outputs.publish == 'true'", publish)
        self.assertIn("github.ref_type == 'tag'", publish)
        for name in ("identity", "test", "build"):
            self.assertNotIn("softprops/action-gh-release", self.job(name))

    def test_artifacts_are_collected_per_product(self):
        self.assertIn("release-${{ needs.identity.outputs.product }}-", self.job("build"))
        publish = self.job("publish-release")
        self.assertIn("pattern: release-${{ needs.identity.outputs.product }}-*", publish)
        self.assertNotIn("pattern: release-*", publish)

    def test_every_published_file_is_verified_against_the_product_and_version(self):
        publish = self.job("publish-release")
        self.assertIn("release_identity.py verify-assets", publish)
        self.assertIn("--complete", publish)
        self.assertIn("release_identity.py verify-assets", self.job("build"))

    def test_release_notes_come_from_the_verified_version(self):
        publish = self.job("publish-release")
        self.assertIn("body_path: ${{ needs.identity.outputs.notes_path }}", publish)
        self.assertNotIn("RELEASE_NOTES_V", self.text)

    def test_the_dispatch_version_never_reaches_a_shell_command(self):
        """It is untrusted text: through the environment, never interpolated."""
        uses = [l.strip() for l in self.text.splitlines() if "github.event.inputs.release_version" in l]
        self.assertEqual(uses, ["DISPATCH_VERSION: ${{ github.event.inputs.release_version }}"])

    def test_the_build_uses_the_verified_version_not_its_own_guess(self):
        build = self.job("build")
        self.assertIn("needs.identity.outputs.release_version", build)
        self.assertNotIn("cat VERSION", build)
        self.assertNotIn("GITHUB_REF_NAME", build)

    def test_the_identity_job_sees_the_history_it_needs_for_ancestry(self):
        self.assertIn("fetch-depth: 0", self.job("identity"))

    def test_nothing_uses_the_repository_wide_latest_release(self):
        self.assertNotIn("releases/latest", self.text)
        self.assertNotIn("make_latest", self.text)


class ArtifactNamingContractTests(unittest.TestCase):
    """build_release.py, the products and the updater's platform keys are one contract."""

    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("build_release_under_test",
                                                      ROOT / "tools" / "build_release.py")
        cls.build = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.build)

    def test_the_build_script_names_files_with_the_active_products_prefix(self):
        for suffix, template in self.build.ARTIFACT_NAMES.items():
            with self.subTest(suffix=suffix):
                self.assertTrue(template.startswith("{prefix}{version}-"), template)
                name = template.format(prefix=omni_release.active_product().artifact_prefix,
                                       version="V1.1.1")
                self.assertTrue(name.startswith(omni_release.active_product().artifact_prefix))

    def test_every_template_round_trips_through_both_products_parsers(self):
        keys = {"windows": "windows-x86_64", "arm64": "macos-arm64", "x86_64": "macos-x86_64",
                "linux": "ubuntu-x86_64"}
        for product in PRODUCTS.values():
            for suffix, template in self.build.ARTIFACT_NAMES.items():
                with self.subTest(product=product.id, suffix=suffix):
                    name = template.format(prefix=product.artifact_prefix, version="V1.2.3")
                    parsed = product.parse_asset(name)
                    self.assertEqual(parsed, {"version": "1.2.3", "platform": keys[suffix]})
                    for other in PRODUCTS.values():
                        if other is not product:
                            self.assertIsNone(other.parse_asset(name))

    def test_the_platform_tails_are_exactly_what_the_build_publishes(self):
        built = {t.split("{version}-", 1)[1] for t in self.build.ARTIFACT_NAMES.values()}
        self.assertEqual(built, set(omni_release.PLATFORM_TAILS))


if __name__ == "__main__":
    unittest.main()
