"""Runtime state, credentials and keys never enter source control.

The application writes accounts, certificates, private keys, audit history and backups into its data
directory. That is normally outside the source tree, but it falls back to the program directory when
it cannot create the usual one, so `.gitignore` is the safety net. These tests derive the file names
from the code rather than from a list somebody remembered, so a data file added later is caught.
"""
import re
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HAVE_GIT = shutil.which("git") is not None and (ROOT / ".git").exists()

# Files that are inputs to a build or part of the product, never runtime state.
NOT_STATE = {"LICENSE", "VERSION", "manifest.json", "config.example.json"}

# Never source, whatever their names: a key, a certificate, a backup, an operator's firmware.
MUST_BE_IGNORED = ["users.json", "remote_access.json", "remote_cert.pem", "remote_key.pem",
                   "any_other.pem", "any_other.key", "bundle.pfx", "bundle.p12",
                   "OmniSuite-V1.2.0-backup-20261029.omnibak", "restore-snapshot/accounts.json",
                   "security_audit.json", "device_aliases.json", "multiview_groups.json",
                   "multiview_meta.json", "ui_preferences.json", "usb_associations.json",
                   "usb_extenders.json", "config.json", "units_cache.json", "scan_results.json",
                   "notice_ack.json", "scan_preferences.json", "topology_ack.json",
                   "firmware/image.bin", "firmware/sub/image.img", "dist/app.exe", "build/x",
                   "release/OmniSuite-V1.2.0-Windows-x86_64.zip", ".venv/pyvenv.cfg",
                   "launcher.log", "device.vdf"]


def ignored(path: str) -> bool:
    # A directory is ignored by a pattern ending in "/", which git only matches against a path
    # inside it; a name with no extension is a directory here.
    probe = path if "." in path.rsplit("/", 1)[-1] else path + "/x"
    result = subprocess.run(["git", "check-ignore", "-q", "--no-index", probe], cwd=str(ROOT))
    return result.returncode == 0


def data_file_names() -> set:
    """Every file name the code places under the data directory."""
    names = set()
    patterns = [
        # DATA_DIR / "users.json"
        re.compile(r"(?:DATA_DIR|CWD|data_dir)\s*/\s*\"([A-Za-z0-9_.-]+)\""),
        # KEY_FILE = "remote_key.pem": a file name held as a module constant
        re.compile(r"\b[A-Z_]*FILE[A-Z_]*\s*=\s*\"([A-Za-z0-9_.-]+\.(?:json|pem|key|csv|omnibak|log))\""),
        # Section("accounts", "users.json", ...): the backup's table of what it carries
        re.compile(r"Section\(\s*\"[a-z_]+\"\s*,\s*\"([A-Za-z0-9_.-]+)\""),
    ]
    for path in ROOT.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        for pattern in patterns:
            names.update(pattern.findall(text))
    return names - NOT_STATE


@unittest.skipUnless(HAVE_GIT, "needs a git checkout")
class IgnoredStateTests(unittest.TestCase):
    def test_every_file_the_code_writes_to_the_data_directory_is_ignored(self):
        names = data_file_names()
        self.assertGreater(len(names), 17, "the scan found suspiciously few data files: %s" % sorted(names))
        for name in sorted(names):
            with self.subTest(file=name):
                self.assertTrue(ignored(name), "%s is written by the application but not ignored" % name)

    def test_the_credential_and_key_bearing_files_are_named_in_the_scan(self):
        """The derivation must actually find the files that matter, or the test above proves nothing."""
        names = data_file_names()
        for needed in ("users.json", "remote_key.pem", "remote_cert.pem", "remote_access.json",
                       "security_audit.json", "config.json"):
            self.assertIn(needed, names)

    def test_the_sensitive_and_operator_content_is_ignored(self):
        for path in MUST_BE_IGNORED:
            with self.subTest(path=path):
                self.assertTrue(ignored(path), "%s must never be committable" % path)

    def test_the_example_configuration_stays_trackable(self):
        self.assertFalse(ignored("config.example.json"))
        self.assertTrue((ROOT / "config.example.json").exists())


@unittest.skipUnless(HAVE_GIT, "needs a git checkout")
class NothingSensitiveIsTrackedTests(unittest.TestCase):
    def tracked(self):
        out = subprocess.run(["git", "ls-files"], cwd=str(ROOT), capture_output=True, text=True)
        return [l for l in out.stdout.splitlines() if l]

    def test_no_state_secret_key_backup_or_firmware_is_tracked(self):
        bad = re.compile(r"(^|/)(users|remote_access|security_audit|device_aliases|config|units_cache|"
                         r"scan_results|notice_ack)\.json$|\.(pem|key|pfx|p12|omnibak|vdf|env)$|"
                         r"^(firmware|dist|build|release|archive|artifacts)/|(^|/)restore-snapshot/")
        offenders = [p for p in self.tracked() if bad.search(p) and p != "config.example.json"]
        self.assertEqual(offenders, [])

    def test_no_large_binary_blob_is_tracked(self):
        """A firmware image or an installer would show up here long before anyone noticed."""
        for rel in self.tracked():
            path = ROOT / rel
            if path.exists() and path.is_file():
                self.assertLess(path.stat().st_size, 3 * 1024 * 1024,
                                "%s is over 3 MB; source control is not for build output or firmware" % rel)


if __name__ == "__main__":
    unittest.main()
