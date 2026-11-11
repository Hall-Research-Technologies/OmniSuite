"""The package inspection every release archive must pass before a draft release exists."""
import io
import sys
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import inspect_release as inspect          # noqa: E402

VERSION = "V1.2.0"
GOOD = {"OmniSuite.exe": b"MZ" + b"\0" * 200_000,
        "LICENSE": b"11A. COMPONENTS LICENSED UNDER THE GNU LESSER GENERAL PUBLIC LICENSE\n",
        "THIRD_PARTY_NOTICES.md": b"notices\n", "third_party_licenses/x-LICENSE.txt": b"text\n",
        "third_party_licenses/certifi-cacert.pem": b"public bundle\n",
        "REBUILD.md": b"steps\n", "third_party/PYSTRAY_PROVENANCE.md": b"provenance\n",
        "third_party/pystray-0.19.5/COPYING": b"gpl\n", "third_party/pystray-0.19.5/COPYING.LGPL": b"lgpl\n",
        "third_party/pystray-0.19.5/lib/pystray/__init__.py": b"# pystray\n",
        "third_party/pystray-0.19.5/tests/icon_tests.py": b"# upstream test\n",
        "third_party/pystray-0.19.5/tools/make-release.py": b"# upstream tool\n",
        "third_party_licenses/pystray/COPYING": b"gpl\n", "third_party_licenses/pystray/COPYING.LGPL": b"lgpl\n"}


def make(directory, files, name="OmniSuite-V1.2.0-Windows-x86_64.zip"):
    path = Path(directory) / name
    with zipfile.ZipFile(path, "w") as archive:
        for member, data in files.items():
            archive.writestr(member, data)
    return path


class InspectReleaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def check(self, files, name="OmniSuite-V1.2.0-Windows-x86_64.zip", version=VERSION):
        return inspect.problems_for(make(self.tmp.name, files, name), version)

    def test_a_clean_package_passes(self):
        self.assertEqual(self.check(GOOD), [])

    def test_names_that_are_not_exactly_this_release_fail(self):
        for name in ("OmniSuite-V1.2.0-weekend-rc5-Windows-x86_64.zip", "OmniSuite-V1.2.0-rc1-Windows-x86_64.zip",
                     "OmniSuite-v1.2.0-Windows-x86_64.zip", "OmniSuite-V1.2.1-Windows-x86_64.zip",
                     "LumaSuite-V1.2.0-Windows-x86_64.zip", "OmniSuite-V1.2.0-test-Windows-x86_64.zip"):
            with self.subTest(name):
                self.assertTrue(self.check(GOOD, name=name))

    def test_a_pre_release_version_argument_is_refused(self):
        self.assertTrue(self.check(GOOD, version="V1.2.0-rc1"))

    def test_missing_licence_or_notices(self):
        for drop in ("LICENSE", "THIRD_PARTY_NOTICES.md"):
            with self.subTest(drop):
                files = {k: v for k, v in GOOD.items() if k != drop}
                self.assertTrue(any(drop in p for p in self.check(files)))

    def test_the_lgpl_material_is_required(self):
        for drop in ("REBUILD.md", "third_party/PYSTRAY_PROVENANCE.md", "third_party/pystray-0.19.5/COPYING.LGPL",
                     "third_party/pystray-0.19.5/lib/pystray/__init__.py", "third_party_licenses/pystray/COPYING"):
            with self.subTest(drop):
                files = {k: v for k, v in GOOD.items() if k != drop}
                self.assertTrue(any(drop in p for p in self.check(files)))

    def test_the_licence_must_carry_section_11a(self):
        self.assertTrue(any("11A" in p for p in self.check(dict(GOOD, LICENSE=b"old licence\n"))))

    def test_upstream_tests_and_tools_folders_are_allowed_only_inside_the_pystray_source(self):
        self.assertEqual(self.check(GOOD), [])
        self.assertTrue(self.check(dict(GOOD, **{"tests/test_x.py": b"x"})))
        self.assertTrue(self.check(dict(GOOD, **{"third_party/other/tests/a.py": b"x"})))

    def test_things_that_must_never_ship(self):
        for extra in ("tests/test_x.py", "tools/build_release.py", "firmware/a.bin", "users.json", "config.json",
                      "server.key", "backup.omnibak", "soak-report.md", "PROJECT.md", ".git/config",
                      "OmniSuite-V1.2.0-weekend-rc5-Windows-x86_64.zip", "run.log"):
            with self.subTest(extra):
                self.assertTrue(self.check(dict(GOOD, **{extra: b"x"})))

    def test_a_pem_outside_the_licence_folder_is_key_material(self):
        self.assertTrue(self.check(dict(GOOD, **{"certs/server.pem": b"x"})))

    def test_text_members_are_scanned(self):
        private = b"see Omni" + b"Suite Pl" + b"us"
        for data, label in ((private, "private product"),
                            (b"-----BEGIN RSA " + b"PRIVATE KEY-----\n", "private key"),
                            (b"host 192.168." + b"100.152", "lab address"),
                            (b"C:" + bytes([92]) + b"softwareDEV" + bytes([92]) + b"x", "build path")):
            with self.subTest(label):
                self.assertTrue(self.check(dict(GOOD, **{"README.md": data})), label)

    def test_the_executable_is_scanned_for_the_private_name_and_build_paths(self):
        for blob in (b"Omni" + b"Suite-Pl" + b"us", b"C:" + bytes([92]) + b"softwareDEV" + bytes([92]) + b"omniSuite"):
            with self.subTest(blob):
                files = dict(GOOD, **{"OmniSuite.exe": b"MZ" + b"\0" * 200_000 + blob})
                self.assertTrue(self.check(files))

    def test_a_tar_archive_is_read_the_same_way(self):
        path = Path(self.tmp.name) / "OmniSuite-V1.2.0-Ubuntu-x86_64.tar.gz"
        with tarfile.open(path, "w:gz") as archive:
            for member, data in dict(GOOD, **{"tests/a.py": b"x"}).items():
                info = tarfile.TarInfo(member)
                info.size = len(data)
                archive.addfile(info, io.BytesIO(data))
        self.assertTrue(any("tests/a.py" in p for p in inspect.problems_for(path, VERSION)))

    def test_a_corrupt_archive_is_a_finding_not_a_crash(self):
        path = Path(self.tmp.name) / "OmniSuite-V1.2.0-Windows-x86_64.zip"
        path.write_bytes(b"not a zip")
        self.assertTrue(inspect.problems_for(path, VERSION))

    def test_the_workflow_inspects_before_it_publishes(self):
        text = (ROOT / ".github" / "workflows" / "build-cross-platform.yml").read_text(encoding="utf-8")
        self.assertIn("tools/inspect_release.py", text)
        self.assertLess(text.index("tools/inspect_release.py"), text.index("Publish GitHub Release"))


class WorkflowSafetyTests(unittest.TestCase):
    """The release is created as a draft; nothing in the workflow makes it public."""

    TEXT = (ROOT / ".github" / "workflows" / "build-cross-platform.yml").read_text(encoding="utf-8")
    MARK = "- name: Publish GitHub Release"
    PUBLISH = TEXT[TEXT.index(MARK):]

    def test_the_release_is_a_draft_on_an_explicit_commit(self):
        self.assertRegex(self.PUBLISH, r"(?m)^\s+draft: true\s*$")
        self.assertIn("target_commitish: ${{ github.sha }}", self.PUBLISH)
        self.assertIn("fail_on_unmatched_files: true", self.PUBLISH)

    def test_nothing_marks_it_public(self):
        self.assertNotRegex(self.TEXT, r"(?m)^\s+draft: false")
        self.assertNotRegex(self.TEXT, r"(?m)^\s+make_latest:")
        self.assertNotIn("gh release edit", self.TEXT)

    def test_publication_is_only_for_a_verified_tag_and_needs_the_token_and_the_whole_matrix(self):
        self.assertIn("needs.identity.outputs.publish == 'true' && github.ref_type == 'tag'", self.TEXT)
        self.assertIn("needs: [identity, build]", self.TEXT)
        self.assertIn("RELEASE_PUBLISH_TOKEN is not configured", self.TEXT)
        self.assertLess(self.TEXT.index("RELEASE_PUBLISH_TOKEN is not configured"),
                        self.TEXT.index(self.MARK))

    def test_checksums_are_verified_before_anything_is_published(self):
        self.assertIn("sha256sum -c SHA256SUMS.txt", self.TEXT)
        self.assertLess(self.TEXT.index("sha256sum -c SHA256SUMS.txt"),
                        self.TEXT.index(self.MARK))

    def test_the_text_names_no_private_repository(self):
        self.assertNotIn("private", self.TEXT.lower().replace("private key", ""))


class DiagnosticModeTests(unittest.TestCase):
    """A rehearsal build (V1.2.0-diag1) is inspected for content exactly like a release.

    Only its name is judged differently, and only when the caller says it is a diagnostic run.
    Without this a dispatch rehearsal failed at the inspection step on every platform, before
    the smoke test, so the workflow could never be rehearsed end to end.
    """

    DIAG = "V1.2.0-diag1"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def name(self, version=None, tail="Windows-x86_64.zip"):
        return "OmniSuite-%s-%s" % (version or self.DIAG, tail)

    def check(self, files, name, version, diagnostic=True):
        return inspect.problems_for(make(self.tmp.name, files, name), version, diagnostic=diagnostic)

    def test_a_clean_rehearsal_package_passes_only_when_it_is_inspected_as_a_diagnostic(self):
        self.assertEqual(self.check(GOOD, self.name(), self.DIAG), [])
        self.assertTrue(self.check(GOOD, self.name(), self.DIAG, diagnostic=False))

    def test_every_platform_tail_is_accepted_for_a_rehearsal(self):
        for tail in ("Windows-x86_64.zip", "macOS-arm64.zip", "macOS-x86_64.zip", "Ubuntu-x86_64.tar.gz"):
            with self.subTest(tail):
                if tail.endswith(".tar.gz"):
                    path = Path(self.tmp.name) / self.name(tail=tail)
                    with tarfile.open(path, "w:gz") as archive:
                        for member, data in GOOD.items():
                            info = tarfile.TarInfo(member)
                            info.size = len(data)
                            archive.addfile(info, io.BytesIO(data))
                    self.assertEqual(inspect.problems_for(path, self.DIAG, diagnostic=True), [])
                else:
                    self.assertEqual(self.check(GOOD, self.name(tail=tail), self.DIAG), [])

    def test_the_rehearsal_is_still_inspected_for_content(self):
        for drop in ("LICENSE", "THIRD_PARTY_NOTICES.md", "REBUILD.md", "third_party/PYSTRAY_PROVENANCE.md"):
            with self.subTest(drop):
                files = {k: v for k, v in GOOD.items() if k != drop}
                self.assertTrue(any(drop in p for p in self.check(files, self.name(), self.DIAG)))
        findings = {
            "a test file": dict(GOOD, **{"tests/a.py": b"x"}),
            "no section 11A": dict(GOOD, LICENSE=b"a licence without the section\n"),
            "a private key": dict(GOOD, **{"notes.md": b"-----BEGIN PRIVATE KEY-----\n"}),
            "a lab address": dict(GOOD, **{"notes.md": b"see 192.168." + b"100.7\n"}),
        }
        for label, files in findings.items():
            with self.subTest(label):
                self.assertTrue(self.check(files, self.name(), self.DIAG))

    def test_only_the_diagnostic_identity_is_relaxed(self):
        for version in ("V1.2.0-rc1", "V1.2.0-weekend-rc5", "V1.2.0-dev", "V1.2.0-diag", "V1.2.0-diagX",
                        "V1.2.0-diag1-extra", "V1.2.0-DIAG1", "V1.2.0-diag1234", "v1.2.0-diag1", "1.2.0-diag1",
                        "V1-diag1", "V1.2.0-test"):
            with self.subTest(version):
                self.assertTrue(self.check(GOOD, "OmniSuite-%s-Windows-x86_64.zip" % version, version))

    def test_a_version_with_a_trailing_newline_is_not_a_diagnostic_version(self):
        path = make(self.tmp.name, GOOD, self.name())
        self.assertTrue(inspect.problems_for(path, self.DIAG + chr(10), diagnostic=True))

    def test_a_name_that_disagrees_with_the_diagnostic_version_is_refused(self):
        for name in ("OmniSuite-V1.2.0-diag2-Windows-x86_64.zip", "OmniSuite-V1.2.1-diag1-Windows-x86_64.zip",
                     "OmniSuite-V1.2.0-Windows-x86_64.zip", "LumaSuite-V1.2.0-diag1-Windows-x86_64.zip",
                     "OmniSuite-V1.2.0-diag1-windows-x86_64.zip", "OmniSuite-V1.2.0-diag1-Plan9.zip",
                     "OmniSuite-V1.2.0-diag1-extra-Windows-x86_64.zip"):
            with self.subTest(name):
                self.assertTrue(self.check(GOOD, name, self.DIAG))

    def test_the_flag_changes_nothing_for_a_release_version(self):
        self.assertEqual(self.check(GOOD, "OmniSuite-V1.2.0-Windows-x86_64.zip", "V1.2.0"), [])
        self.assertTrue(self.check(GOOD, self.name(), "V1.2.0"))
        self.assertTrue(self.check(GOOD, "OmniSuite-V1.2.0-rc1-Windows-x86_64.zip", "V1.2.0"))

    def test_the_command_line_default_is_strict_and_the_flag_is_explicit(self):
        path = make(self.tmp.name, GOOD, self.name())
        self.assertEqual(inspect.main([str(path), "--version", self.DIAG, "--diagnostic"]), 0)
        self.assertEqual(inspect.main([str(path), "--version", self.DIAG]), 1)


class DiagnosticWorkflowTests(unittest.TestCase):
    """The workflow passes the flag only where nothing can publish, and holds a read-only token."""

    @classmethod
    def setUpClass(cls):
        raw = (ROOT / ".github" / "workflows" / "build-cross-platform.yml").read_text(encoding="utf-8")
        cls.text = "\n".join(l for l in raw.replace("\r\n", "\n").splitlines() if not l.lstrip().startswith("#"))

    def job(self, name):
        import re
        match = re.search(r"^  %s:\n(.*?)(?=^  [A-Za-z0-9_-]+:\n|\Z)" % re.escape(name), self.text, re.S | re.M)
        self.assertIsNotNone(match, "no job named " + name)
        return match.group(1)

    def test_only_the_build_job_may_ask_for_diagnostic_inspection_and_only_without_publication(self):
        build = self.job("build")
        self.assertIn("CAN_PUBLISH: ${{ needs.identity.outputs.publish }}", build)
        self.assertIn('if [ "$CAN_PUBLISH" != "true" ]; then flag="--diagnostic"; fi', build)
        for name in ("identity", "test", "publish-release"):
            with self.subTest(job=name):
                self.assertNotIn("--diagnostic", self.job(name))

    def test_the_publish_job_inspects_strictly(self):
        publish = self.job("publish-release")
        self.assertIn("tools/inspect_release.py", publish)
        self.assertNotIn("diagnostic", publish.lower())

    def test_the_workflow_token_is_read_only(self):
        head = self.text[:self.text.index("on:\n")]
        self.assertIn("permissions:\n  contents: read\n", head)
        self.assertNotIn("contents: write", self.text)


if __name__ == "__main__":
    unittest.main()
