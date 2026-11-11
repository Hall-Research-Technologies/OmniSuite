"""Release compatibility: installed V1.1.0 apps, the first-run notice, identity, and what is public.

Installed OmniSuite V1.1.0 applications cannot be changed, and they read the repository's
repository-wide "latest" release. Everything published from here on has to keep working
with them, so the V1.1.0 update check is frozen verbatim in tests/fixtures and executed
here against the artifacts this project actually builds.
"""
import importlib.util
import json
import logging
import platform as real_platform
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

import omni_release

srv = base.srv
ROOT = Path(__file__).resolve().parent.parent
OMNI = omni_release.active_product()
REPO_URL = "https://github.com/Hall-Research-Technologies/omniSuite"


def _load(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


build = _load("build_release_for_compat_tests", "tools/build_release.py")


# ---------------------------------------------------------------- the frozen V1.1.0 client
class LegacyClient:
    """The V1.1.0 update check, executed as published, against a scripted GitHub."""

    def __init__(self, installed, latest_release, system="Windows", machine="AMD64"):
        self.requested = []

        class Response:
            status_code = 200

            def __init__(self, payload):
                self._payload = payload

            def json(self):
                return self._payload

            def raise_for_status(self):
                return None

        client = self

        class Requests:
            @staticmethod
            def get(url, **kwargs):
                client.requested.append(url)
                return Response(latest_release)

        class Platform:
            @staticmethod
            def system():
                return system

            @staticmethod
            def machine():
                return machine

        namespace = {"re": re, "time": time, "threading": threading, "urllib": urllib,
                     "platform": Platform, "requests": Requests,
                     "log": logging.getLogger("legacy-v1.1.0"),
                     "_app_version": lambda: installed}
        source = (ROOT / "tests" / "fixtures" / "legacy_update_check_v1_1_0.py").read_text(encoding="utf-8")
        exec(compile(source, "legacy_update_check_v1_1_0.py", "exec"), namespace)
        self.ns = namespace

    def check(self):
        return self.ns["_update_check"](force=True)


def release_json(tag, names, repo_url=REPO_URL):
    return {"tag_name": tag, "draft": False, "prerelease": False,
            "html_url": "%s/releases/tag/%s" % (repo_url, tag),
            "assets": [{"name": n, "size": 1,
                        "browser_download_url": "%s/releases/download/%s/%s" % (repo_url, tag, n)}
                       for n in names]}


def built_names(version):
    return [template.format(prefix=OMNI.artifact_prefix, version=version)
            for template in build.ARTIFACT_NAMES.values()]


class InstalledV110CompatibilityTests(unittest.TestCase):
    """What an installed V1.1.0 sees when this project publishes a new release."""

    def test_the_frozen_client_really_is_the_published_one(self):
        source = (ROOT / "tests" / "fixtures" / "legacy_update_check_v1_1_0.py").read_text(encoding="utf-8")
        self.assertIn("releases/latest", source)
        self.assertIn('GITHUB_REPO = "omniSuite"', source)
        self.assertIn("def _fetch_latest_release", source)
        self.assertNotIn("omni_release", source)

    def test_it_reads_the_repository_this_project_still_publishes_to(self):
        client = LegacyClient("V1.1.0", release_json("V1.1.0", built_names("V1.1.0")))
        client.check()
        self.assertEqual(client.requested,
                         ["https://api.github.com/repos/Hall-Research-Technologies/omniSuite/releases/latest"])
        legacy_repo = "%s/%s" % (client.ns["GITHUB_OWNER"], client.ns["GITHUB_REPO"])
        self.assertEqual(legacy_repo.lower(), OMNI.release_repo.lower(),
                         "the release channel must not move: installed apps read this repository")

    def test_a_new_release_is_offered_to_an_installed_v110_on_every_platform(self):
        cases = [(("Windows", "AMD64"), "windows-x86_64"), (("Linux", "x86_64"), "ubuntu-x86_64"),
                 (("Darwin", "arm64"), "macos-arm64"), (("Darwin", "x86_64"), "macos-x86_64")]
        for (system, machine), key in cases:
            with self.subTest(platform=system + "/" + machine):
                client = LegacyClient("V1.1.0", release_json("V1.1.1", built_names("V1.1.1")),
                                      system, machine)
                result = client.check()
                self.assertEqual(result["status"], "UPDATE_AVAILABLE")
                self.assertEqual(result["latest_version"], "V1.1.1")
                self.assertEqual(result["asset"]["platform"], key)
                self.assertTrue(result["asset"]["name"].startswith("OmniSuite-V1.1.1-"))
                self.assertTrue(result["release_url"].endswith("/releases/tag/V1.1.1"))

    def test_the_names_this_build_produces_are_matched_by_the_old_matcher(self):
        for name in built_names("V1.1.1"):
            with self.subTest(name=name):
                client = LegacyClient("V1.1.0", release_json("V1.1.1", [name]), "Windows", "AMD64")
                asset = client.ns["_select_platform_asset"]([{"name": name, "browser_download_url":
                                                              REPO_URL + "/releases/download/V1.1.1/" + name}])
                # exactly one platform claims each artifact; never two
                claimed = []
                for system, machine in (("Windows", "AMD64"), ("Linux", "x86_64"),
                                        ("Darwin", "arm64"), ("Darwin", "x86_64")):
                    other = LegacyClient("V1.1.0", {}, system, machine)
                    if other.ns["_select_platform_asset"]([{"name": name, "browser_download_url":
                                                            REPO_URL + "/releases/download/V1.1.1/" + name}]):
                        claimed.append((system, machine))
                self.assertEqual(len(claimed), 1, claimed)

    def test_the_same_or_an_older_release_is_not_an_update(self):
        for installed, tag in (("V1.1.0", "V1.1.0"), ("V1.1.1", "V1.1.0"), ("V1.1.1", "V1.1.1")):
            with self.subTest(installed=installed, latest=tag):
                result = LegacyClient(installed, release_json(tag, built_names(tag))).check()
                self.assertEqual(result["status"], "CURRENT")

    def test_a_tag_outside_the_V_form_is_unreadable_to_the_old_client(self):
        """Why a release family with a different tag form must never share this feed."""
        result = LegacyClient("V1.1.0", release_json("extra-v1.2.0", ["x.zip"])).check()
        self.assertEqual(result["status"], "UNABLE_TO_CHECK")
        self.assertFalse(result["update_available"])

    def test_the_old_client_refuses_assets_outside_this_repository(self):
        elsewhere = "https://github.com/Example-Org/Example"
        client = LegacyClient("V1.1.0", release_json("V1.1.1", built_names("V1.1.1"), elsewhere))
        result = client.check()
        self.assertEqual(result["status"], "UPDATE_AVAILABLE")
        self.assertIsNone(result["asset"], "the old client only trusts this repository's own URLs")

    def test_new_and_old_clients_agree_on_what_a_release_means(self):
        """The same feed, read by both, gives the same version and the same asset."""
        release = release_json("V1.1.1", built_names("V1.1.1"))
        old = LegacyClient("V1.1.0", release).check()
        chosen, core = omni_release.select_release([release], OMNI)
        self.assertEqual("V" + core, old["latest_version"])
        with mock.patch.object(srv.platform, "system", return_value="Windows"), \
                mock.patch.object(srv.platform, "machine", return_value="AMD64"):
            new_asset = srv._select_platform_asset(chosen["assets"], core)
        self.assertEqual(new_asset["name"], old["asset"]["name"])


# ---------------------------------------------------------------- first-run notice
class FirstRunNoticeTextTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        module = (ROOT / "ui" / "settings.js").read_text(encoding="utf-8")
        start = module.index("const NETWORK_NOTICE_HTML = `")
        cls.html = module[start:module.index("`;", start)]
        import html as html_module
        text = re.sub(r"<[^>]+>", " ", cls.html)
        # Read it as a browser would: entities decoded and JavaScript \\uXXXX escapes resolved.
        text = html_module.unescape(text)
        text = re.sub(r"\\u([0-9a-fA-F]{4})", lambda m: chr(int(m.group(1), 16)), text)
        cls.text = re.sub(r"\s+", " ", text)

    def test_the_existing_firewall_guidance_is_kept(self):
        for kept in ("communicates directly with devices on the network",
                     "security software may block discovery",
                     "If prompted by your firewall, allow network access for",
                     "on the networks where you manage AV devices",
                     "application firewall exception",
                     "temporary/system policy exception"):
            self.assertIn(kept, self.text)

    def test_it_now_covers_macos_local_network_access(self):
        for needed in ("On macOS", "Local Network", "Choose Allow", "Don’t Allow",
                       "System Settings", "Privacy & Security"):
            self.assertIn(needed, self.text)

    def test_gatekeeper_is_a_separate_subject_and_is_not_in_this_dialog(self):
        for foreign in ("Gatekeeper", "Open Anyway", "quarantine", "notariz", "unsigned"):
            self.assertNotIn(foreign, self.text)

    def test_the_product_name_is_data_filled_in_from_the_server(self):
        self.assertIn("data-app-name", self.html)
        module = (ROOT / "ui" / "settings.js").read_text(encoding="utf-8")
        body = module[module.index("async function showFirstRunNotice"):]
        body = body[:body.index("\n// Injects the markup")]
        self.assertIn("data.app_name", body)
        self.assertIn("textContent = appName", body, "set as text, never as markup")
        self.assertNotIn("innerHTML", body)

    def test_it_is_still_advisory_not_an_error(self):
        self.assertNotIn("danger", self.html.lower())


class FirstRunNoticeRevisionTests(base.ServerTestBase):
    """New advice reaches the people it applies to once, and nobody else is asked again."""

    def write_ack(self, **entry):
        srv.NOTICE_ACK.write_text(json.dumps({"network_access": dict(acknowledged=True, **entry)}),
                                  encoding="utf-8")

    def notices(self, system):
        with mock.patch.object(srv.platform, "system", return_value=system):
            return self.client.get("/api/notices").get_json()

    def test_a_fresh_install_sees_the_current_notice_on_every_platform(self):
        for system in ("Darwin", "Windows", "Linux"):
            with self.subTest(system=system):
                self.assertFalse(self.notices(system)["notices"]["network_access"]["acknowledged"])

    def test_a_mac_that_read_the_old_text_is_asked_once_more(self):
        self.write_ack(acknowledged_at="2026-09-01 10:00:00")          # no revision: the old text
        body = self.notices("Darwin")
        self.assertFalse(body["notices"]["network_access"]["acknowledged"])
        self.assertEqual(body["notices"]["network_access"]["revision"], srv.NOTICE_REVISIONS["network_access"])

    def test_windows_and_linux_are_not_asked_again_for_advice_that_does_not_apply(self):
        self.write_ack(acknowledged_at="2026-09-01 10:00:00")
        for system in ("Windows", "Linux"):
            with self.subTest(system=system):
                self.assertTrue(self.notices(system)["notices"]["network_access"]["acknowledged"])

    def test_acknowledging_records_the_revision_and_stops_the_question_everywhere(self):
        with mock.patch.object(srv.platform, "system", return_value="Darwin"):
            self.client.post("/api/notices/ack", json={"notice": "network_access"})
        stored = json.loads(srv.NOTICE_ACK.read_text(encoding="utf-8"))["network_access"]
        self.assertEqual(stored["revision"], srv.NOTICE_REVISIONS["network_access"])
        self.assertTrue(self.notices("Darwin")["notices"]["network_access"]["acknowledged"])

    def test_a_malformed_revision_is_treated_as_the_oldest_not_as_a_crash(self):
        for bad in ("two", None, [], {}):
            with self.subTest(revision=bad):
                self.write_ack(revision=bad)
                self.assertFalse(self.notices("Darwin")["notices"]["network_access"]["acknowledged"])
                self.assertTrue(self.notices("Windows")["notices"]["network_access"]["acknowledged"])

    def test_the_response_names_the_product_for_the_dialog(self):
        self.assertEqual(self.notices("Windows")["app_name"], OMNI.name)

    def test_clearing_the_inventory_still_never_resets_it(self):
        with mock.patch.object(srv.platform, "system", return_value="Darwin"):
            self.client.post("/api/notices/ack", json={"notice": "network_access"})
            self.client.post("/api/clear_units", json={})
        self.assertTrue(self.notices("Darwin")["notices"]["network_access"]["acknowledged"])


# ---------------------------------------------------------------- identity
class BundleIdentityTests(unittest.TestCase):
    def test_omnisuite_keeps_the_identifier_it_has_always_had(self):
        self.assertEqual(OMNI.bundle_identifier, "OmniSuite")

    def test_the_identifier_is_written_into_the_bundle_and_nothing_else_is_renamed(self):
        import plistlib
        import tempfile
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        app = Path(folder.name) / "OmniSuite.app"
        (app / "Contents").mkdir(parents=True)
        with (app / "Contents" / "Info.plist").open("wb") as handle:
            plistlib.dump({"CFBundleIdentifier": "OmniSuite", "CFBundleExecutable": "OmniSuite"}, handle)
        with mock.patch.object(build, "run"):
            build.declare_local_network_usage(app, "Finds devices.", "OmniSuite", "com.example.app")
        with (app / "Contents" / "Info.plist").open("rb") as handle:
            info = plistlib.load(handle)
        self.assertEqual(info["CFBundleIdentifier"], "com.example.app")
        self.assertEqual(info["CFBundleExecutable"], "OmniSuite", "the launcher finds the app by this")

    def test_without_an_identifier_the_existing_one_is_left_alone(self):
        import plistlib
        import tempfile
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        app = Path(folder.name) / "OmniSuite.app"
        (app / "Contents").mkdir(parents=True)
        with (app / "Contents" / "Info.plist").open("wb") as handle:
            plistlib.dump({"CFBundleIdentifier": "OmniSuite"}, handle)
        with mock.patch.object(build, "run"):
            build.declare_local_network_usage(app, "Finds devices.")
        with (app / "Contents" / "Info.plist").open("rb") as handle:
            self.assertEqual(plistlib.load(handle)["CFBundleIdentifier"], "OmniSuite")

    def test_the_whole_bundle_check_refuses_the_wrong_identity(self):
        """check_macos_bundle is what runs on the artifact; the helper alone is not enough."""
        import plistlib
        import tempfile
        smoke = _load("smoke_release_for_compat_bundle", "tools/smoke_release.py")
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        app = Path(folder.name) / "OmniSuite.app"
        (app / "Contents" / "MacOS").mkdir(parents=True)
        (app / "Contents" / "MacOS" / "OmniSuite").write_bytes(b"x")
        info = {"CFBundleIdentifier": "com.example.wrong", "CFBundleDisplayName": OMNI.name,
                "CFBundleName": OMNI.name, "NSLocalNetworkUsageDescription": "Finds devices."}
        with (app / "Contents" / "Info.plist").open("wb") as handle:
            plistlib.dump(info, handle)
        with mock.patch.object(smoke.shutil, "which", return_value=None):
            problems = smoke.check_macos_bundle(app / "Contents" / "MacOS" / "OmniSuite")
        self.assertEqual(len(problems), 1)
        self.assertIn("CFBundleIdentifier", problems[0])

    def test_the_smoke_test_refuses_a_bundle_with_the_wrong_identity(self):
        smoke = _load("smoke_release_for_compat_tests", "tools/smoke_release.py")
        good = {"CFBundleIdentifier": OMNI.bundle_identifier, "CFBundleDisplayName": OMNI.name}
        self.assertEqual(smoke.identity_problems(good), [])
        self.assertEqual(len(smoke.identity_problems(dict(good, CFBundleIdentifier="com.example.app"))), 1)
        self.assertEqual(len(smoke.identity_problems(dict(good, CFBundleDisplayName="Something"))), 1)
        self.assertEqual(len(smoke.identity_problems({})), 2)


# ---------------------------------------------------------------- what is public
class NoObsoleteProductNamingTests(unittest.TestCase):
    """OmniSuite is one product. Naming from the retired two-product split must not return.

    The forbidden names are assembled from fragments so this file does not contain them. The scan
    covers every text file in the tree, not a chosen list, so a new file cannot reintroduce them.
    """

    FORBIDDEN = [re.compile("omni" + "suite[ -]" + "pl" + "us", re.I),
                 re.compile("pl" + r"us-v\d", re.I),
                 re.compile("omni" + "suite" + "pl" + "us", re.I),
                 re.compile("PLUS_" + "UPDATE", re.I)]
    SKIP_DIRS = {".git", ".venv", "venv", "build", "dist", "release", "firmware", "node_modules",
                 "__pycache__", ".pytest_cache", "fixtures"}
    TEXT = {".py", ".js", ".html", ".css", ".md", ".yml", ".yaml", ".json", ".txt", ".spec", ".toml",
            ".cfg", ".ini", ".example"}

    def files(self):
        import os
        found = []
        for folder, dirs, names in os.walk(ROOT):
            dirs[:] = [d for d in dirs if d not in self.SKIP_DIRS]
            for name in names:
                path = Path(folder) / name
                if path.suffix.lower() in self.TEXT or name in ("LICENSE", "VERSION"):
                    found.append(path)
        return found

    def test_no_text_file_in_the_tree_uses_the_retired_names(self):
        files = self.files()
        self.assertGreater(len(files), 100, "the scan found suspiciously little")
        for path in files:
            text = path.read_text(encoding="utf-8", errors="replace")
            for pattern in self.FORBIDDEN:
                with self.subTest(file=str(path.relative_to(ROOT)), pattern=pattern.pattern[:12]):
                    self.assertIsNone(pattern.search(text),
                                      "%s uses a retired product name" % path.relative_to(ROOT))

    def test_no_file_is_named_for_the_retired_split(self):
        for path in self.files():
            self.assertNotRegex(path.name.lower(), "pl" + "us_", str(path))

    def test_there_is_a_release_notes_file_for_the_version_being_prepared(self):
        version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
        notes = ROOT / ("RELEASE_NOTES_%s.md" % version)
        self.assertTrue(notes.exists(), "the release workflow refuses a tag without %s" % notes.name)


if __name__ == "__main__":
    unittest.main()
