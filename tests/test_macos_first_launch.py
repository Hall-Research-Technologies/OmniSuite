"""macOS first launch: Gatekeeper instructions, product name, and the in-app notice.

The app is not signed or notarized, so macOS blocks its first launch. These tests
hold what is shipped to explain that (a plain-text guide inside the download, the
documentation, the README), what the build does to the bundle's name, and how the
page presents a Local Network verdict from the server.

What is NOT tested here, because it cannot be from this machine: how macOS itself
behaves -- whether Open Anyway appears where the instructions say, whether the
System Settings link opens the right pane. The checklist for that is in
docs/MACOS_LOCAL_NETWORK.md and docs/INSTALL_MACOS.md.
"""
import errno
import importlib.util
import os
import re
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

try:
    from . import test_usb_integration as base
except ImportError:                                   # loaded by path, no package
    import test_usb_integration as base

import omni_product
import omni_release
from omni_release import PRODUCTS

srv = base.srv
ROOT = Path(__file__).resolve().parent.parent
OMNI, PLUS = PRODUCTS["omnisuite"], PRODUCTS["omnisuite-plus"]


def _load(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


build = _load("build_release_for_first_launch_tests", "tools/build_release.py")
smoke = _load("smoke_release_for_first_launch_tests", "tools/smoke_release.py")


def _text(relative):
    return (ROOT / relative).read_text(encoding="utf-8")


class ReadmeTextTests(unittest.TestCase):
    def text(self, product):
        return build.macos_readme_text(product)

    def test_each_product_gets_its_own_instructions(self):
        for product in (OMNI, PLUS):
            with self.subTest(product=product.id):
                text = self.text(product)
                self.assertIn(product.name + " for macOS - read this first", text)
                self.assertIn('should start with "%s"' % product.artifact_prefix, text)
        self.assertNotEqual(self.text(OMNI), self.text(PLUS))

    def test_it_explains_open_anyway_step_by_step(self):
        text = self.text(OMNI)
        for needed in ("Done", "Move to Trash", "System Settings", "Privacy & Security",
                       "Security section", "Open Anyway", "enter your password", "Open"):
            self.assertIn(needed, text)
        self.assertIn("Control-click", text)           # the older macOS route, labelled as such
        self.assertIn("macOS 14", text)

    def test_it_says_to_approve_only_the_official_download(self):
        text = self.text(PLUS)
        self.assertIn(omni_release.RELEASES_URL, text)
        self.assertIn("official", text)
        self.assertIn("SHA256SUMS.txt", text)
        self.assertIn("delete it", text)

    def test_it_never_tells_anyone_to_weaken_macos(self):
        """No command, no quarantine removal, nothing that disables Gatekeeper."""
        for product in (OMNI, PLUS):
            text = self.text(product).lower()
            for forbidden in ("xattr", "spctl", "master-disable", "sudo", "chmod", "com.apple.quarantine"):
                self.assertNotIn(forbidden, text)
            self.assertIn("do not turn off gatekeeper", text)
            self.assertIn("do not run commands that remove the", text)

    def test_it_covers_local_network_and_the_future_signing_plan(self):
        text = self.text(OMNI)
        self.assertIn("Local Network", text)
        self.assertIn("Choose Allow", text)
        self.assertIn("Don't Allow", text)
        self.assertIn("Developer ID", text)
        self.assertIn("notarized", text)

    def test_it_makes_no_promise_the_product_cannot_keep(self):
        text = self.text(OMNI).lower()
        self.assertNotIn("safe", text)
        self.assertNotIn("guarantee", text)


class ReadmeInTheZipTests(unittest.TestCase):
    def make_zip(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        target = Path(folder.name) / "OmniSuite-V1.0.0-macOS-arm64.zip"
        with zipfile.ZipFile(target, "w") as archive:
            archive.writestr("OmniSuite.app/Contents/Info.plist", b"plist")
            link = zipfile.ZipInfo("OmniSuite.app/Contents/Frameworks/Python")
            link.external_attr = (0o120755 << 16)             # a symlink entry
            archive.writestr(link, "Python.framework/Versions/3.12/Python")
            exe = zipfile.ZipInfo("OmniSuite.app/Contents/MacOS/OmniSuite")
            exe.external_attr = (0o100755 << 16)
            archive.writestr(exe, b"binary")
        return target

    def test_the_instructions_are_added_beside_the_app(self):
        target = self.make_zip()
        build.add_macos_readme(target, OMNI)
        with zipfile.ZipFile(target) as archive:
            self.assertIn("READ-ME-FIRST-macOS.txt", archive.namelist())
            self.assertEqual(archive.read("READ-ME-FIRST-macOS.txt").decode("utf-8"),
                             build.macos_readme_text(OMNI))

    def test_the_app_inside_is_not_rewritten(self):
        """Appending must keep symlinks, permissions and contents exactly."""
        target = self.make_zip()
        with zipfile.ZipFile(target) as archive:
            before = {i.filename: (archive.read(i), i.external_attr) for i in archive.infolist()}
        build.add_macos_readme(target, PLUS)
        with zipfile.ZipFile(target) as archive:
            after = {i.filename: (archive.read(i), i.external_attr) for i in archive.infolist()}
            self.assertIsNone(archive.testzip())
        for name, kept in before.items():
            self.assertEqual(after[name], kept, name)
        self.assertEqual(set(after) - set(before), {"READ-ME-FIRST-macOS.txt"})

    def test_the_file_name_is_stable_because_the_smoke_test_looks_for_it(self):
        self.assertEqual(build.MACOS_README_NAME, "READ-ME-FIRST-macOS.txt")
        self.assertIn("READ-ME-FIRST-macOS.txt", _text("tools/smoke_release.py"))


class PackagingOrderTests(unittest.TestCase):
    def package(self, suffix):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        dist = Path(folder.name) / "dist"
        dist.mkdir()
        if suffix == "windows":
            (dist / "OmniSuite.exe").write_bytes(b"x")
        elif suffix == "linux":
            (dist / "OmniSuite").mkdir()
        else:
            app = dist / "OmniSuite.app"
            (app / "Contents").mkdir(parents=True)
        events = []
        with mock.patch.object(build, "RELEASE_DIR", Path(folder.name) / "release"), \
                mock.patch.object(build, "declare_local_network_usage",
                                  side_effect=lambda app, usage, name=None: events.append(("declare", usage, name))), \
                mock.patch.object(build, "zip_macos_app", side_effect=lambda *a: events.append(("zip-mac",))), \
                mock.patch.object(build, "add_macos_readme", side_effect=lambda *a: events.append(("readme",))), \
                mock.patch.object(build, "zip_path", side_effect=lambda *a: events.append(("zip",))), \
                mock.patch.object(build, "tar_path", side_effect=lambda *a: events.append(("tar",))):
            build.package_release(dist, suffix, "V1.0.0-test")
        return events

    def test_both_mac_architectures_declare_zip_then_add_the_instructions(self):
        for suffix in ("arm64", "x86_64"):
            with self.subTest(suffix=suffix):
                events = self.package(suffix)
                self.assertEqual([e[0] for e in events], ["declare", "zip-mac", "readme"])
                product = omni_release.active_product()
                self.assertEqual(events[0][1:], (product.local_network_usage, product.name))

    def test_windows_and_linux_get_no_macos_instructions_and_no_plist_edit(self):
        for suffix, expected in (("windows", "zip"), ("linux", "tar")):
            with self.subTest(suffix=suffix):
                self.assertEqual([e[0] for e in self.package(suffix)], [expected])


class DisplayNameTests(unittest.TestCase):
    def declare(self, display_name):
        import plistlib
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        app = Path(folder.name) / "OmniSuite.app"
        (app / "Contents").mkdir(parents=True)
        with (app / "Contents" / "Info.plist").open("wb") as handle:
            plistlib.dump({"CFBundleDisplayName": "OmniSuite", "CFBundleName": "OmniSuite",
                           "CFBundleExecutable": "OmniSuite", "CFBundleIdentifier": "OmniSuite"}, handle)
        with mock.patch.object(build, "run"):
            build.declare_local_network_usage(app, "Finds devices.", display_name)
        with (app / "Contents" / "Info.plist").open("rb") as handle:
            return plistlib.load(handle)

    def test_plus_is_listed_under_its_own_name(self):
        info = self.declare("OmniSuite Plus")
        self.assertEqual(info["CFBundleDisplayName"], "OmniSuite Plus")
        self.assertEqual(info["CFBundleName"], "OmniSuite Plus")

    def test_the_executable_and_identifier_are_left_alone(self):
        """Renaming those would change how the smoke test and the launcher find the app."""
        info = self.declare("OmniSuite Plus")
        self.assertEqual(info["CFBundleExecutable"], "OmniSuite")
        self.assertEqual(info["CFBundleIdentifier"], "OmniSuite")

    def test_without_a_name_nothing_is_renamed(self):
        info = self.declare(None)
        self.assertEqual(info["CFBundleDisplayName"], "OmniSuite")

    def test_the_two_products_have_distinct_names(self):
        self.assertNotEqual(OMNI.name, PLUS.name)


class SmokeReadmeTests(unittest.TestCase):
    def test_a_good_readme_passes(self):
        for product in (OMNI, PLUS):
            self.assertEqual(smoke.macos_readme_problems(build.macos_readme_text(product)), [])

    def test_missing_or_hollow_instructions_fail(self):
        for text in (None, "", "   ", 7):
            self.assertEqual(len(smoke.macos_readme_problems(text)), 1, repr(text))
        problems = smoke.macos_readme_problems("Read this.")
        self.assertEqual(len(problems), 4)

    def test_each_required_phrase_is_individually_required(self):
        good = build.macos_readme_text(OMNI)
        for phrase in ("Open Anyway", "Privacy & Security", "Local Network", "official"):
            with self.subTest(phrase=phrase):
                self.assertEqual(len(smoke.macos_readme_problems(good.replace(phrase, "xxxx"))), 1)

    def test_the_smoke_test_reads_the_shipped_file_on_macos_only(self):
        source = _text("tools/smoke_release.py")
        hook = source[source.index("def main()"):]
        self.assertLess(hook.index('platform.system() == "Darwin"'), hook.index("READ-ME-FIRST-macOS.txt"))
        self.assertLess(hook.index("READ-ME-FIRST-macOS.txt"), hook.index("return run(binary"))


class ServerNamesTheProductTests(base.ServerTestBase):
    def blocked(self, product_id):
        def refuse(address, timeout=None, *a, **k):
            raise OSError(errno.EHOSTUNREACH, "No route to host")
        with mock.patch.object(omni_product, "PRODUCT_ID", product_id), \
                mock.patch.object(srv.platform, "system", return_value="Darwin"), \
                mock.patch.object(srv.socket, "create_connection", side_effect=refuse):
            return self.client.post("/api/scan", json={"targets": "192.0.2.1-20"}).get_json()

    def test_the_blocked_scan_names_the_product_for_the_page(self):
        self.assertEqual(self.blocked("omnisuite")["app_name"], "OmniSuite")
        self.assertEqual(self.blocked("omnisuite-plus")["app_name"], "OmniSuite Plus")

    def test_the_diagnostics_the_notice_needs_are_present(self):
        body = self.blocked("omnisuite")
        self.assertEqual(body["reason"], "scan_blocked")
        self.assertEqual(body["likely_cause"], "local_network_permission")
        self.assertEqual(body["diagnostics"]["platform"]["system"], "Darwin")
        self.assertEqual(body["diagnostics"]["probe_outcomes"], {"EHOSTUNREACH": 20})


class PageWiringTests(unittest.TestCase):
    """index.html presents the server's verdict and decides nothing itself."""

    @classmethod
    def setUpClass(cls):
        cls.html = _text("ui/index.html")
        start = cls.html.index("$('#scan_btn').addEventListener('click'")
        cls.handler = cls.html[start:cls.html.index("$('#targets_clear')", start)]

    def test_the_module_is_loaded_before_the_page_script_uses_it(self):
        self.assertIn('src="/ui/scan-notice.js', self.html)
        self.assertLess(self.html.index('src="/ui/scan-notice.js'),
                        self.html.index("$('#scan_btn').addEventListener('click'"))

    def test_there_is_a_container_for_it_and_it_starts_hidden(self):
        self.assertRegex(self.html, r'<div id="scan_permission_notice"[^>]*\bhidden\b')

    def test_the_page_never_decides_what_a_permission_problem_is(self):
        """Classification is the server's, presented by scan-notice.js."""
        for internal in ("local_network_permission", "scan_blocked", "likely_cause", "EHOSTUNREACH"):
            self.assertNotIn(internal, self.html)

    def test_a_new_scan_retires_the_old_notice_and_failure_presents_the_verdict(self):
        self.assertIn("OmniScanNotice.hide()", self.handler)
        self.assertIn("OmniScanNotice.show(data)", self.handler)
        self.assertLess(self.handler.index("OmniScanNotice.hide()"), self.handler.index("fetch('/api/scan'"))
        self.assertLess(self.handler.index("OmniScanNotice.show(data)"),
                        self.handler.index("toast(data.error"),
                        "the generic toast is the fallback, not the first choice")

    def test_a_successful_scan_never_reaches_the_notice(self):
        success = self.handler[self.handler.index("found`"):]
        self.assertNotIn("OmniScanNotice.show", success)

    def test_retry_runs_the_same_scan_the_button_runs(self):
        self.assertIn("onRetry: () => $('#scan_btn').click()", self.html)

    def test_settings_are_opened_only_through_the_module_which_checks_the_environment(self):
        self.assertIn("navigate: (url) => { window.location.href = url; }", self.html)
        self.assertNotIn("x-apple.systempreferences", self.html)

    def test_the_notice_is_not_styled_as_an_error(self):
        css = self.html[self.html.index(".scan-notice{"):self.html.index(".scan-notice p")]
        self.assertNotIn("danger", css)
        self.assertIn("[hidden]", self.html[self.html.index(".scan-notice[hidden]"):][:40])

    def test_the_javascript_suite_is_part_of_the_run(self):
        self.assertIn("scan_notice_test.js", _text("run_tests.py"))


class DocumentationTests(unittest.TestCase):
    def test_the_install_guide_exists_and_is_linked_from_the_readme(self):
        guide = _text("docs/INSTALL_MACOS.md")
        self.assertIn("docs/INSTALL_MACOS.md", _text("README.md"))
        for needed in ("Open Anyway", "Privacy & Security", "official", "SHA256SUMS.txt",
                       "Developer ID", "notariz", "Local Network", "READ-ME-FIRST-macOS.txt"):
            self.assertIn(needed, guide)

    def test_the_readme_no_longer_offers_right_click_open_as_the_way(self):
        text = _text("README.md")
        macos = text[text.index("- **macOS** —"):]
        macos = macos[:macos.index("- **Ubuntu**")]
        self.assertIn("Open Anyway", macos)
        self.assertIn("INSTALL_MACOS.md", macos)

    def test_nothing_documents_disabling_gatekeeper(self):
        for relative in ("docs/INSTALL_MACOS.md", "README.md", "ui/user-guide.html"):
            text = _text(relative).lower()
            for forbidden in ("xattr -", "spctl --master-disable", "sudo spctl"):
                self.assertNotIn(forbidden, text, relative)

    def test_the_user_guide_has_a_first_launch_section_and_describes_the_buttons(self):
        guide = _text("ui/user-guide.html")
        self.assertIn('id="macos-first-launch"', guide)
        section = guide[guide.index('id="macos-first-launch"'):]
        section = section[:section.index("</section>")]
        for needed in ("Open Anyway", "Privacy &amp; Security", "official"):
            self.assertIn(needed, section)
        local = guide[guide.index('id="macos-local-network"'):]
        local = local[:local.index("</section>")]
        self.assertIn("Retry Scan", local)
        self.assertIn("Open System Settings", local)

    def test_the_release_checklist_asks_for_the_macos_paragraph(self):
        self.assertIn("INSTALL_MACOS.md", _text("DEVELOPMENT.md"))


if __name__ == "__main__":
    unittest.main()
