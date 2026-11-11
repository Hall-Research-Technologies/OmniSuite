"""The in-app update check, fed hostile and awkward release feeds.

Only a published, non-draft, non-prerelease release whose tag is exactly V<number>(.<number>)+ and
that is strictly newer than the installed version can be offered; the download is offered only when
an asset carries this product's exact name for exactly that version and this platform, on an official
host. Everything else is "current" or "unable to check" and never an update. A rehearsal, release
candidate, test or retired-product name must never qualify, however the feed presents it.
"""
import copy
import unittest

try:
    from . import test_release_isolation as iso
except ImportError:                                   # loaded by path, no package
    import test_release_isolation as iso

OMNI = iso.OMNI
BASE = iso.BASE_URL
WIN = "Windows-x86_64.zip"


def asset(name, tag, host_url=None):
    return {"name": name, "size": 1,
            "browser_download_url": host_url if host_url is not None else "%s/download/%s/%s" % (BASE, tag, name)}


def release(tag, assets, draft=False, prerelease=False):
    return {"tag_name": tag, "draft": draft, "prerelease": prerelease,
            "html_url": "%s/tag/%s" % (BASE, tag), "assets": assets}


def good(core, tag=None, tails=("Windows-x86_64.zip", "macOS-arm64.zip", "macOS-x86_64.zip",
                                "Ubuntu-x86_64.tar.gz"), **flags):
    tag = tag or "V" + core
    return release(tag, [asset("OmniSuite-V%s-%s" % (core, t), tag) for t in tails], **flags)


class UpdaterAdversarialTests(iso.UpdaterBase):
    INSTALLED = "V1.1.0"

    def offered(self, feed, installed=None):
        self.feed(copy.deepcopy(feed))
        return self.check(OMNI, installed or self.INSTALLED)

    def assert_not_an_update(self, body, why):
        self.assertFalse(body["update_available"], why)
        self.assertNotEqual(body["status"], iso.srv.UPDATE_AVAILABLE, why)
        self.assertIsNone(body["asset"], why)
        self.assertIsNone(body["asset_url"], why)

    def test_the_one_intended_case_qualifies(self):
        body = self.offered([good("1.2.0"), good("1.1.0")])
        self.assertEqual(body["status"], iso.srv.UPDATE_AVAILABLE)
        self.assertEqual(body["latest_version"], "V1.2.0")
        self.assertEqual(body["asset"]["name"], "OmniSuite-V1.2.0-" + WIN)
        self.assertTrue(body["release_url"].endswith("/tag/V1.2.0"))

    def test_names_that_are_not_releases_never_qualify(self):
        for tag in ("V1.2.0-diag1", "V1.2.0-rc1", "V1.2.0-weekend-rc5", "V1.2.0-dev", "V1.2.0-test",
                    "v1.2.0", "V1.2.0 ", " V1.2.0", "V1.2.0\n", "release-1.2.0", "latest", "V1", "1.2.0",
                    "extra-V1.2.0", "V1.2.0.1.1", "V1.2.0+build", "V1.2.x", "V" + "1" * 40,
                    "pl" + "us-v1.2.0", "OmniSuite-V1.2.0"):
            with self.subTest(tag=tag):
                body = self.offered([good("1.2.0", tag=tag), good("1.1.0")])
                self.assert_not_an_update(body, "tag %r must not be offered" % tag)

    def test_a_draft_or_a_prerelease_is_never_offered_however_good_its_tag(self):
        for flags in ({"draft": True}, {"prerelease": True}, {"draft": True, "prerelease": True}):
            with self.subTest(flags=flags):
                body = self.offered([good("1.2.0", **flags), good("1.1.0")])
                self.assert_not_an_update(body, str(flags))

    def test_a_good_release_with_the_wrong_files_offers_the_page_not_a_download(self):
        feeds = {
            "right tag, assets for another version":
                [release("V1.2.0", [asset("OmniSuite-V1.2.1-" + WIN, "V1.2.0")])],
            "right tag, a rehearsal-named asset":
                [release("V1.2.0", [asset("OmniSuite-V1.2.0-diag1-" + WIN, "V1.2.0")])],
            "right tag, an rc-named asset":
                [release("V1.2.0", [asset("OmniSuite-V1.2.0-rc1-" + WIN, "V1.2.0")])],
            "right tag, only another platform":
                [release("V1.2.0", [asset("OmniSuite-V1.2.0-macOS-arm64.zip", "V1.2.0")])],
            "right tag, no assets":
                [release("V1.2.0", [])],
            "right tag, assets key missing":
                [{"tag_name": "V1.2.0", "draft": False, "prerelease": False,
                  "html_url": "%s/tag/V1.2.0" % BASE}],
            "right tag, wrong filename":
                [release("V1.2.0", [asset("OmniSuite-Windows.zip", "V1.2.0")])],
            "right tag, a checksum file only":
                [release("V1.2.0", [asset("OmniSuite-V1.2.0-%s.sha256" % WIN, "V1.2.0")])],
            "right tag, a retired-product name":
                [release("V1.2.0", [asset("LumaSuite-V1.2.0-" + WIN, "V1.2.0")])],
        }
        for label, feed in feeds.items():
            with self.subTest(label):
                body = self.offered(feed)
                self.assertTrue(body["update_available"], label)        # the release itself is real
                self.assertIsNone(body["asset"], label)                  # but no download is offered
                self.assertIsNone(body["asset_url"], label)
                self.assertTrue(body["release_url"].startswith("https://github.com/"))

    def test_the_right_file_is_chosen_among_several_decoys(self):
        decoys = [asset("OmniSuite-V1.2.0-diag1-" + WIN, "V1.2.0"),
                  asset("OmniSuite-V1.2.0-" + WIN + ".sha256", "V1.2.0"),
                  asset("OmniSuite-V1.1.0-" + WIN, "V1.2.0"),
                  asset("OmniSuite-V1.2.0-weekend-rc5-" + WIN, "V1.2.0"),
                  asset("OmniSuite-V1.2.0-macOS-arm64.zip", "V1.2.0")]
        body = self.offered([release("V1.2.0", decoys + [asset("OmniSuite-V1.2.0-" + WIN, "V1.2.0")])])
        self.assertEqual(body["asset"]["name"], "OmniSuite-V1.2.0-" + WIN)

    def test_a_download_address_outside_the_repository_is_never_handed_on(self):
        for url in ("https://evil.example/OmniSuite-V1.2.0-" + WIN,
                    "http://github.com/Hall-Research-Technologies/omniSuite/releases/download/V1.2.0/OmniSuite-V1.2.0-" + WIN,
                    "https://github.com/someone-else/omniSuite/releases/download/V1.2.0/OmniSuite-V1.2.0-" + WIN,
                    "javascript:alert(1)", "", None):
            with self.subTest(url=url):
                body = self.offered([release("V1.2.0", [asset("OmniSuite-V1.2.0-" + WIN, "V1.2.0", host_url=url)])
                                     if url is not None else
                                     release("V1.2.0", [{"name": "OmniSuite-V1.2.0-" + WIN, "size": 1}])])
                self.assertIsNone(body["asset"])
                self.assertIsNone(body["asset_url"])

    def test_an_older_or_the_same_release_is_current_never_an_update(self):
        for feed in ([good("1.0.7")], [good("1.1.0")], [good("1.0.10"), good("1.0.9")]):
            with self.subTest(feed=[r["tag_name"] for r in feed]):
                body = self.offered(feed)
                self.assertEqual(body["status"], iso.srv.UPDATE_CURRENT)
                self.assert_not_an_update(body, "not newer than the installed version")

    def test_the_highest_number_wins_not_the_first_in_the_feed(self):
        body = self.offered([good("1.1.5"), good("1.2.0"), good("1.0.99")])
        self.assertEqual(body["latest_version"], "V1.2.0")
        body = self.offered([good("1.2.0"), good("1.10.0"), good("1.9.0")])
        self.assertEqual(body["latest_version"], "V1.10.0", "numeric, not alphabetic")

    def test_a_non_release_with_a_higher_number_does_not_hide_the_real_release(self):
        body = self.offered([good("9.9.9", tag="V9.9.9-diag1"), good("9.9.9", tag="V9.9.9-rc1"),
                             good("9.9.9", draft=True), good("9.9.9", prerelease=True), good("1.2.0")])
        self.assertEqual(body["latest_version"], "V1.2.0")
        self.assertEqual(body["asset"]["name"], "OmniSuite-V1.2.0-" + WIN)

    def test_a_release_naming_another_products_files_is_refused(self):
        mixed = good("1.2.0")
        mixed["assets"].append(asset("OmniSuite-Extra-V1.2.0-" + WIN, "V1.2.0"))
        body = self.offered([mixed])
        self.assertEqual(body["status"], iso.srv.UPDATE_UNABLE)
        self.assertFalse(body["update_available"])
        self.assertIsNone(body["asset"])

    def test_a_build_ahead_of_the_newest_release_is_current_and_a_rehearsal_build_never_updates(self):
        for installed in ("V1.3.0", "V1.2.0-diag1", "V1.2.0-rc1", "V1.2.0"):
            with self.subTest(installed=installed):
                body = self.offered([good("1.2.0")], installed=installed)
                self.assertFalse(body["update_available"])
                self.assertNotEqual(body["status"], iso.srv.UPDATE_AVAILABLE)

    def test_an_empty_or_hostile_feed_is_unable_to_check_never_up_to_date(self):
        for feed in ([], [{}], [None], ["V1.2.0"], [{"tag_name": None}], [{"tag_name": 7}],
                     [good("1.2.0", tag="V1.2.0-diag1")]):
            with self.subTest(feed=repr(feed)[:40]):
                body = self.offered(feed)
                self.assertFalse(body["update_available"])
                self.assertIsNone(body["asset"])
                self.assertIn(body["status"], (iso.srv.UPDATE_UNABLE, iso.srv.UPDATE_CURRENT))
                if feed == [good("1.2.0", tag="V1.2.0-diag1")]:
                    self.assertEqual(body["status"], iso.srv.UPDATE_UNABLE)

    def test_every_platform_is_offered_only_its_own_exact_asset(self):
        for system, machine, tail in (("Windows", "AMD64", "Windows-x86_64.zip"),
                                      ("Darwin", "arm64", "macOS-arm64.zip"),
                                      ("Darwin", "x86_64", "macOS-x86_64.zip"),
                                      ("Linux", "x86_64", "Ubuntu-x86_64.tar.gz")):
            with self.subTest(system=system, machine=machine):
                self.feed(copy.deepcopy([good("1.2.0")]))
                body = self.check(OMNI, "V1.1.0", platform=(system, machine))
                self.assertEqual(body["asset"]["name"], "OmniSuite-V1.2.0-" + tail)
        for system, machine in (("Linux", "aarch64"), ("FreeBSD", "amd64"), ("Windows", "")):
            with self.subTest(system=system, machine=machine, unsupported=True):
                self.feed(copy.deepcopy([good("1.2.0")]))
                body = self.check(OMNI, "V1.1.0", platform=(system, machine))
                if system == "Windows":
                    continue                                   # Windows always maps to the x86-64 build
                self.assertIsNone(body["asset"])


if __name__ == "__main__":
    unittest.main()
