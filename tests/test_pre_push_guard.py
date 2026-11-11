"""The pre-push guard blocks evidence of private-product exposure and nothing else.

The earlier guard refused any tree that contained one of thirteen named modules, which are now an
ordinary part of OmniSuite, so it refused the release itself. These tests pin the replacement:
legitimate V1.2.0 content passes; the private product's name, branch, artefacts and history fail.

Every forbidden name is assembled from fragments, so this file does not contain them. Each case
builds a throwaway repository in a temporary directory; nothing touches the network.
"""
import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import pre_push_guard as guard          # noqa: E402

NAME = "Omni" + "Suite " + "Pl" + "us"
BRANCH = "remote-user-" + "routing"
PUBLIC = "https://github.com/Hall-Research-Technologies/OmniSuite.git"
ENV = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.invalid",
           GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.invalid",
           GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull)


def git(repo, *args):
    done = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, env=ENV)
    assert done.returncode == 0, (args, done.stderr)
    return done.stdout.strip()


class Repo:
    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name)
        git(self.path, "init", "-q")
        git(self.path, "config", "commit.gpgsign", "false")

    def commit(self, files, message="change", remove=()):
        for name, text in files.items():
            target = self.path / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
        for name in remove:
            (self.path / name).unlink()
        git(self.path, "add", "-A")
        git(self.path, "commit", "-q", "-m", message)
        return git(self.path, "rev-parse", "HEAD")

    def reasons(self, sha, ref="refs/heads/main", remote_sha=guard.ZERO):
        return guard.check_ref(self.path, ref, sha, remote_sha)


class LegitimateContentPasses(unittest.TestCase):
    def setUp(self):
        self.repo = Repo()
        self.addCleanup(self.repo.tmp.cleanup)

    def test_modules_that_used_to_be_private_are_ordinary_now(self):
        sha = self.repo.commit({
            "omni_users.py": "# accounts\n", "omni_remote.py": "# remote access\n",
            "omni_gate.py": "# gate\n", "omni_tls.py": "# certificates\n",
            "omni_backup.py": "# backup\n", "omni_secret.py": "# secrets\n"}, "Add Remote Access")
        self.assertEqual(self.repo.reasons(sha), [])

    def test_documentation_describing_the_feature_as_omnisuite_passes(self):
        sha = self.repo.commit({"README.md": "OmniSuite adds Remote Access, accounts and Display Groups.\n",
                                "RELEASE_NOTES_V1.2.0.md": "# OmniSuite V1.2.0\nA plus point is speed.\n",
                                "VERSION": "V1.2.0\n"}, "OmniSuite V1.2.0")
        self.assertEqual(self.repo.reasons(sha), [])

    def test_a_public_ca_bundle_and_licence_texts_are_not_key_material(self):
        sha = self.repo.commit({"third_party_licenses/certifi-cacert.pem": "public bundle\n"}, "licences")
        self.assertEqual(self.repo.reasons(sha), [])

    def test_a_deletion_is_always_allowed(self):
        self.assertEqual(guard.check_ref(self.repo.path, "refs/heads/x", guard.ZERO), [])


class PrivateExposureFails(unittest.TestCase):
    def setUp(self):
        self.repo = Repo()
        self.addCleanup(self.repo.tmp.cleanup)

    def assertRefused(self, sha, fragment, **kw):
        reasons = self.repo.reasons(sha, **kw)
        self.assertTrue(any(fragment in r for r in reasons), reasons)

    def test_the_private_product_name_in_a_file(self):
        sha = self.repo.commit({"docs/a.md": "Welcome to %s.\n" % NAME})
        self.assertRefused(sha, "names the private product")

    def test_each_spelling_of_the_name(self):
        for spelling in ("omnisuite-" + "pl" + "us", "OmniSuite" + "Pl" + "us", "omnisuite_" + "pl" + "us", "OmniSuite-" + "Pl" + "us"):
            with self.subTest(spelling):
                repo = Repo()
                self.addCleanup(repo.tmp.cleanup)
                sha = repo.commit({"a.txt": "see " + spelling + "\n"})
                self.assertTrue(repo.reasons(sha))

    def test_the_private_remote_and_the_private_branch_named_in_a_file(self):
        for text in ("git remote add " + "pl" + "us-private x", "written on " + BRANCH, "com.omnisuite" + "plus"):
            with self.subTest(text):
                repo = Repo()
                self.addCleanup(repo.tmp.cleanup)
                self.assertTrue(repo.reasons(repo.commit({"notes.md": text + "\n"})))

    def test_private_branch_and_recovery_refs(self):
        sha = self.repo.commit({"a.txt": "fine\n"})
        for ref in ("refs/heads/omnisuite-" + "pl" + "us", "refs/heads/feature/" + BRANCH,
                    "refs/tags/recovery/pre-consolidation", "refs/heads/clean/omnisuite-1.1.x"):
            with self.subTest(ref):
                self.assertRefused(sha, "private-development ref", ref=ref)

    def test_private_artefacts(self):
        for name in ("docs/PUBLIC_" + "EXPOSURE_AUDIT.md", "firmware/x.bin", "users.json", "config.json",
                     "certs/server.key", "certs/server.pem", "backup.omnibak"):
            with self.subTest(name):
                repo = Repo()
                self.addCleanup(repo.tmp.cleanup)
                reasons = repo.reasons(repo.commit({name: "x\n"}))
                self.assertTrue(any("private artefacts" in r for r in reasons), reasons)

    def test_config_example_is_not_a_configuration_file(self):
        sha = self.repo.commit({"config.example.json": "{}\n"})
        self.assertEqual(self.repo.reasons(sha), [])

    def test_a_commit_message_naming_the_product(self):
        sha = self.repo.commit({"a.txt": "clean\n"}, "Merge %s work" % NAME)
        self.assertRefused(sha, "commit message")

    def test_a_tip_that_deleted_the_text_still_carries_it_in_history(self):
        self.repo.commit({"a.txt": "see %s\n" % NAME}, "add")
        sha = self.repo.commit({"a.txt": "clean\n"}, "tidy")
        self.assertRefused(sha, "history added or removed text")

    def test_history_already_on_the_remote_is_not_blamed_on_the_new_commits(self):
        old = self.repo.commit({"a.txt": "see %s\n" % NAME}, "old")
        new = self.repo.commit({"b.txt": "more\n"}, "new")
        reasons = self.repo.reasons(new, remote_sha=old)
        self.assertFalse(any("history added" in r for r in reasons), reasons)


class SingleSnapshotConstruction(unittest.TestCase):
    """The release is one new commit on the public parent, so the private history is not in it."""

    def test_one_snapshot_commit_over_the_public_parent_passes_though_its_source_history_fails(self):
        repo = Repo()
        self.addCleanup(repo.tmp.cleanup)
        public = repo.commit({"omni_matrix.py": "# V1.1.0\n"}, "V1.1.0")
        repo.commit({"omni_users.py": "# accounts\n", "docs/x.md": "see %s\n" % NAME}, "private work")
        final = repo.commit({"omni_users.py": "# accounts\n", "docs/x.md": "tidy\n"}, "repair")
        self.assertTrue(repo.reasons(final, remote_sha=public), "the development history must be refused")
        tree = git(repo.path, "rev-parse", final + "^{tree}")
        snapshot = git(repo.path, "commit-tree", tree, "-p", public, "-m", "OmniSuite V1.2.0")
        self.assertEqual(repo.reasons(snapshot, remote_sha=public), [])
        self.assertEqual(git(repo.path, "rev-parse", snapshot + "^{tree}"), tree)


class WhichRemotesAreChecked(unittest.TestCase):
    def test_the_public_url_in_every_spelling(self):
        for url in (PUBLIC, PUBLIC.lower(), PUBLIC[:-4], PUBLIC + "/", "git@github.com:Hall-Research-Technologies/OmniSuite.git",
                    "ssh://git@github.com/hall-research-technologies/omnisuite.git",
                    "https://user:token@github.com/Hall-Research-Technologies/OmniSuite"):
            with self.subTest(url):
                self.assertTrue(guard.is_public_url(url))

    def test_other_remotes_are_not_checked(self):
        for url in ("https://github.com/Hall-Research-Technologies/" + "OmniSuite-" + "Pl" + "us.git",
                    "https://github.com/Hall-Research-Technologies/LumaSuite.git", "/tmp/local", ""):
            with self.subTest(url):
                self.assertFalse(guard.is_public_url(url))

    def test_run_refuses_on_the_public_url_and_ignores_a_private_one(self):
        repo = Repo()
        self.addCleanup(repo.tmp.cleanup)
        sha = repo.commit({"a.txt": "see %s\n" % NAME})
        line = "refs/heads/main %s refs/heads/main %s\n" % (sha, guard.ZERO)
        err = io.StringIO()
        self.assertEqual(guard.run(io.StringIO(line), "origin", PUBLIC, repo=repo.path, out=err), 1)
        self.assertIn("REFUSED", err.getvalue())
        self.assertEqual(guard.run(io.StringIO(line), "other", "https://example.invalid/x.git",
                                   repo=repo.path, out=io.StringIO()), 0)

    def test_there_is_no_bypass_option(self):
        source = (ROOT / "tools" / "pre_push_guard.py").read_text(encoding="utf-8")
        self.assertNotIn("--force", source)
        self.assertNotIn("--skip", source)
        self.assertNotIn("environ", source.split("def main", 1)[1].split("if __name__", 1)[0])


class TheShippedTree(unittest.TestCase):
    def test_the_guard_file_itself_does_not_trip_the_content_scan(self):
        text = (ROOT / "tools" / "pre_push_guard.py").read_text(encoding="utf-8")
        self.assertIsNone(guard.CONTENT_RE.search(text))
        self.assertIsNone(guard.CONTENT_RE.search(Path(__file__).read_text(encoding="utf-8")))


if __name__ == "__main__":
    unittest.main()
