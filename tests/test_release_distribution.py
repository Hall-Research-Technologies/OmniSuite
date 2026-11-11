"""How a release leaves the building: where it is published, with what, and from where.

Releases go to a release repository
that installed applications already read. These tests pin the properties that keep that safe:

* a release is published to the release repository, never to the repository the workflow runs in,
  with a dedicated token, and a run without the token stops;
* the identity job runs only in the repository named by a repository variable, so a workflow run in
  any other repository does nothing;
* the guard exports what publishing needs and refuses to publish without a release repository;
* licensing is untouched.
"""
import importlib.util
import re
import shutil
import subprocess
import unittest
from pathlib import Path
from unittest import mock

import omni_release
from omni_release import IdentityError

ROOT = Path(__file__).resolve().parent.parent
OMNI = omni_release.active_product()


def _text(relative):
    return (ROOT / relative).read_text(encoding="utf-8")


def _workflow():
    raw = _text(".github/workflows/build-cross-platform.yml")
    return "\n".join(l for l in raw.replace("\r\n", "\n").splitlines() if not l.lstrip().startswith("#"))


def _job(text, name):
    match = re.search(r"^  %s:\n(.*?)(?=^  [A-Za-z0-9_-]+:\n|\Z)" % re.escape(name), text, re.S | re.M)
    assert match, "no job named " + name
    return match.group(1)


class PublishStepTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = _workflow()
        cls.publish = _job(cls.text, "publish-release")

    def test_the_release_goes_to_the_release_repository_output(self):
        self.assertIn("repository: ${{ needs.identity.outputs.release_repo }}", self.publish)
        self.assertIn("tag_name: ${{ needs.identity.outputs.tag }}", self.publish)

    def test_the_repository_the_workflow_runs_in_is_never_the_target(self):
        self.assertNotIn("github.repository", self.publish)
        self.assertNotIn("GITHUB_REPOSITORY", self.publish)

    def test_a_dedicated_token_is_used_and_required(self):
        self.assertIn("token: ${{ secrets.RELEASE_PUBLISH_TOKEN }}", self.publish)
        self.assertIn("RELEASE_PUBLISH_TOKEN is not configured", self.publish)
        self.assertNotIn("secrets.GITHUB_TOKEN", self.publish)

    def test_the_token_is_checked_before_anything_is_published(self):
        self.assertLess(self.publish.index("Require the release token"),
                        self.publish.index("softprops/action-gh-release"))

    def test_the_identity_job_exports_what_publishing_needs(self):
        identity = _job(self.text, "identity")
        self.assertIn("release_repo: ${{ steps.resolve.outputs.release_repo }}", identity)
        self.assertIn("tag: ${{ steps.resolve.outputs.tag }}", identity)

    def test_publishing_still_needs_a_verified_tag_and_nothing_else_publishes(self):
        self.assertIn("needs.identity.outputs.publish == 'true'", self.publish)
        self.assertIn("github.ref_type == 'tag'", self.publish)
        for name in ("identity", "test", "build"):
            self.assertNotIn("softprops/action-gh-release", _job(self.text, name))

    def test_only_version_tags_start_a_run_and_no_push_builds(self):
        triggers = self.text[self.text.index("on:\n"):self.text.index("jobs:\n")]
        self.assertIn('"V*"', triggers)
        self.assertNotIn("branches:", triggers, "a push must not start a build: private CI minutes are limited")


class SourceRepositoryGuardTests(unittest.TestCase):
    """Workflows run only where the repository variable says they may."""

    @classmethod
    def setUpClass(cls):
        cls.text = _workflow()
        cls.identity = _job(cls.text, "identity")

    def test_the_identity_job_runs_only_in_the_named_repository(self):
        self.assertIn("if: github.repository == vars.OMNI_CI_REPOSITORY", self.identity)

    def test_an_unset_variable_means_nothing_runs(self):
        """github.repository == '' is false, so the guard fails closed."""
        self.assertNotIn("|| vars.OMNI_CI_REPOSITORY == ''", self.identity)
        self.assertNotIn("!vars", self.identity)

    def test_every_later_job_needs_the_identity_job(self):
        for name in ("test", "build", "publish-release"):
            head = _job(self.text, name).split("steps:")[0]
            self.assertRegex(head, r"needs:\s*(\[[^\]]*identity[^\]]*\]|identity)", name)

    def test_no_repository_is_written_into_the_workflow(self):
        self.assertNotRegex(self.text, r"Hall-Research-Technologies/[A-Za-z]")


class GuardPublishingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("release_identity_distribution_tests",
                                                      ROOT / "tools" / "release_identity.py")
        cls.tool = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.tool)

    def resolve(self, event="push", ref_type="tag", ref="V1.2.0", dispatch="", version="V1.2.0"):
        return self.tool.resolve(event, ref_type, ref, dispatch, version, OMNI,
                                 is_ancestor=lambda b: True, notes_exist=lambda p: True)

    def test_a_verified_tag_exports_the_release_repository_and_tag(self):
        got = self.resolve()
        self.assertEqual((got["publish"], got["tag"], got["release_repo"]),
                         ("true", "V1.2.0", OMNI.release_repo))

    def test_the_release_repository_is_where_installed_applications_look(self):
        self.assertEqual(OMNI.release_repo, "Hall-Research-Technologies/OmniSuite")

    def test_publishing_without_a_release_repository_is_refused(self):
        with mock.patch.object(OMNI, "release_repo", ""):
            with self.assertRaisesRegex(IdentityError, "needs a release repository"):
                self.resolve()

    def test_a_diagnostic_build_exports_the_repository_but_never_publishes(self):
        got = self.resolve(event="workflow_dispatch", ref_type="branch", ref="x",
                           dispatch="V1.2.0-diag1")
        self.assertEqual((got["publish"], got["tag"]), ("false", ""))
        self.assertEqual(got["release_repo"], OMNI.release_repo)


class BuiltVersionTests(unittest.TestCase):
    """The version a build is named for is the version the application reports."""

    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("build_release_distribution_tests",
                                                      ROOT / "tools" / "build_release.py")
        cls.build = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.build)

    def embedded_version(self, argument, environment=None):
        import os
        import tempfile
        with tempfile.TemporaryDirectory() as folder:
            work = Path(folder) / "work"
            env = {k: v for k, v in os.environ.items() if k not in ("RELEASE_VERSION", "GITHUB_REF_NAME")}
            env.update(environment or {})
            with mock.patch.dict(os.environ, env, clear=True), \
                    mock.patch.object(self.build, "run"), \
                    mock.patch.object(self.build, "assert_data_allowlist"), \
                    mock.patch.object(self.build, "assert_package_contents"):
                self.build.build_binary(Path(folder) / "dist", work, "windows", argument)
            hook = (work / "omni_version_runtime_hook.py").read_text(encoding="utf-8")
        return re.search(r"OMNI_VERSION', '([^']+)'", hook).group(1)

    def test_the_version_argument_is_what_the_application_will_report(self):
        self.assertEqual(self.embedded_version("V9.8.7-rc1"), "V9.8.7-rc1")

    def test_the_argument_wins_over_the_environment(self):
        self.assertEqual(self.embedded_version("V9.8.7-rc1", {"RELEASE_VERSION": "V1.0.0"}), "V9.8.7-rc1")

    def test_without_an_argument_the_environment_then_the_version_file_decide(self):
        self.assertEqual(self.embedded_version(None, {"RELEASE_VERSION": "V3.2.1"}), "V3.2.1")
        self.assertEqual(self.embedded_version(None), (ROOT / "VERSION").read_text(encoding="utf-8").strip())

    def test_main_passes_the_version_it_names_the_artifact_for(self):
        source = _text("tools/build_release.py")
        main = source[source.index("def main()"):]
        self.assertIn("build_binary(dist_path=dist_path, work_path=work_path, suffix=suffix, version=version)", main)


class ChecksumFileTests(unittest.TestCase):
    """What a customer verifies a download against must work with `sha256sum -c` everywhere."""

    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("build_release_checksum_tests",
                                                      ROOT / "tools" / "build_release.py")
        cls.build = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.build)

    def test_the_checksum_line_ends_with_a_bare_line_feed(self):
        import tempfile
        with tempfile.TemporaryDirectory() as folder:
            artifact = Path(folder) / "OmniSuite-V9.9.9-Windows-x86_64.zip"
            artifact.write_bytes(b"payload")
            digest = self.build.write_checksum(artifact)
            written = Path(str(artifact) + ".sha256").read_bytes()
        self.assertNotIn(bytes([13]), written, "a carriage return becomes part of the file name")
        self.assertEqual(written, (digest + "  " + artifact.name + "\n").encode("ascii"))

    def test_the_published_file_is_assembled_without_carriage_returns(self):
        publish = _job(_workflow(), "publish-release")
        self.assertIn("cat *.sha256 | tr -d '\\r' > SHA256SUMS.txt", publish)

    def test_the_line_verifies_with_the_documented_command(self):
        import hashlib
        import tempfile
        if not shutil.which("sha256sum"):
            self.skipTest("sha256sum is not installed")
        with tempfile.TemporaryDirectory() as folder:
            artifact = Path(folder) / "OmniSuite-V9.9.9-Windows-x86_64.zip"
            artifact.write_bytes(b"payload")
            self.build.write_checksum(artifact)
            result = subprocess.run(["sha256sum", "-c", artifact.name + ".sha256"], cwd=folder,
                                    capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class LicenceUnchangedTests(unittest.TestCase):
    def test_there_is_one_licence_file(self):
        self.assertTrue((ROOT / "LICENSE").exists())
        self.assertEqual(len(list(ROOT.glob("LICENSE*"))), 1, "no second licence was introduced")

    def test_there_is_no_activation_system(self):
        code = _text("omni_release.py") + _text("OmniMatrix_upgrade_server_v7_6y.py")
        for forbidden in ("activation_key", "licence_key", "license_key_check", "activate_license"):
            self.assertNotIn(forbidden, code)

    def test_the_licence_is_the_v1_1_0_text_plus_only_the_approved_section_11a(self):
        shipped = subprocess.run(["git", "show", "V1.1.0:LICENSE"], cwd=str(ROOT), capture_output=True)
        if shipped.returncode != 0:
            self.skipTest("the V1.1.0 tag is not available in this checkout")
        crlf, lf = bytes([13, 10]), bytes([10])
        current = (ROOT / "LICENSE").read_bytes().replace(crlf, lf).decode("utf-8")
        start = current.index("11A. COMPONENTS LICENSED UNDER THE GNU LESSER GENERAL PUBLIC LICENSE")
        end = current.index("12. NO WARRANTY")
        # Remove exactly the added section and the one list it extends; nothing else may differ.
        without = current[:start] + current[end:]
        without = without.replace("11, 11A, 12, 13 and 15 survive termination",
                                  "11, 12, 13 and 15 survive termination")
        self.assertEqual(without, shipped.stdout.replace(crlf, lf).decode("utf-8"))

if __name__ == "__main__":
    unittest.main()
