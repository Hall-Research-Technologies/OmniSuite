"""The third-party notice file, the licence texts and the package agree.

The notice used to say the licence text of each component "ships inside its own
package metadata in that bundle". It does not: a PyInstaller build carries a few
`METADATA` files and no licence files. So the texts are collected into
`third_party_licenses/` and bundled, and these tests hold three things together:

  * every distribution the package embeds is named in THIRD_PARTY_NOTICES.md;
  * every one of them has a licence text in `third_party_licenses/`;
  * the build bundles that folder, and puts the same files beside the application
    in the archive, where a recipient can read them.

Nothing here touches the network or a build.
"""
from __future__ import annotations

import importlib.util
import re
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent


def _load(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


collect = _load("collect_licenses_under_test", "tools/collect_licenses.py")
build = _load("build_release_notices_under_test", "tools/build_release.py")
NOTICES = (ROOT / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")


def _lock_lines():
    for line in (ROOT / "requirements-lock.txt").read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            yield line


def _lock_names():
    return {collect.normalise(re.split(r"[=<>!~; ]", line, 1)[0]) for line in _lock_lines()}


def _table_rows():
    """{component: (version, licence text column)} for every table row."""
    rows = {}
    for line in NOTICES.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if (line.startswith("|") and len(cells) >= 4 and cells[0] != "Component"
                and not set(cells[0]) <= {"-"}):
            rows[collect.normalise(cells[0])] = (cells[1], cells[3])
    return rows


class NoticeListsEveryShippedComponentTests(unittest.TestCase):

    def test_every_shipped_distribution_is_named(self):
        rows = _table_rows()
        for name in collect.SHIPPED + collect.CONDITIONAL + collect.CONDITIONAL_NO_TEXT:
            self.assertIn(name, rows, f"{name} is shipped but absent from THIRD_PARTY_NOTICES.md")

    def test_every_locked_distribution_is_classified(self):
        """A new pin must be decided: shipped, or build-only. Neither is a silent default."""
        known = set(collect.SHIPPED) | set(collect.BUILD_ONLY)
        unclassified = sorted(_lock_names() - known)
        self.assertEqual(unclassified, [],
                         "requirements-lock.txt has distributions that are neither in "
                         "tools/collect_licenses.py SHIPPED nor BUILD_ONLY: %s" % unclassified)

    def test_build_only_components_are_not_claimed_as_shipped(self):
        rows = _table_rows()
        for name in collect.BUILD_ONLY:
            self.assertNotIn(name, rows)

    def test_locked_versions_agree_with_the_notice(self):
        pins = {}
        for line in _lock_lines():
            match = re.match(r"([A-Za-z0-9_.\-]+)==([^\s;]+)(?:\s*;\s*(.*))?$", line)
            if match and not match.group(3):
                pins[collect.normalise(match.group(1))] = match.group(2)
        rows = _table_rows()
        checked = 0
        for name, version in pins.items():
            if name in rows:
                checked += 1
                self.assertEqual(rows[name][0], version, f"{name}: notice and lock disagree")
        self.assertGreater(checked, 15, "the comparison matched almost nothing")

    def test_the_false_claim_is_gone(self):
        self.assertNotIn("ships inside its own package metadata", NOTICES)
        self.assertIn("third_party_licenses/", NOTICES)

    def test_notice_keeps_the_single_licence(self):
        self.assertIn("Hall Research Technologies Source-Available Software License", NOTICES)


class LicenceTextsArePresentTests(unittest.TestCase):

    def test_each_shipped_component_has_a_nonempty_licence_file(self):
        for name in collect.SHIPPED + collect.CONDITIONAL:
            folder = ROOT / "third_party_licenses" / name
            files = [f for f in folder.rglob("*") if f.is_file()] if folder.is_dir() else []
            self.assertTrue(files, f"third_party_licenses/{name}/ has no licence text")
            self.assertTrue(any(f.stat().st_size > 100 for f in files),
                            f"third_party_licenses/{name}/ holds only empty or trivial files")

    def test_the_python_runtime_text_is_present(self):
        text = (ROOT / "third_party_licenses" / "python" / "LICENSE.txt").read_text(encoding="utf-8", errors="replace")
        self.assertIn("PYTHON SOFTWARE FOUNDATION LICENSE", text.upper())

    def test_a_licence_file_is_never_key_material(self):
        for item in (ROOT / "third_party_licenses").rglob("*"):
            if item.is_file():
                self.assertNotIn(b"PRIVATE KEY", item.read_bytes()[:4096], str(item))

    def test_no_licence_folder_is_unaccounted_for(self):
        expected = set(collect.SHIPPED) | set(collect.CONDITIONAL) | set(collect.RUNTIME_FOLDERS)
        present = {p.name for p in (ROOT / "third_party_licenses").iterdir() if p.is_dir()}
        self.assertEqual(sorted(present - expected), [],
                         "licences are bundled for components the notice does not list")


class BuildBundlesTheTextsTests(unittest.TestCase):

    def test_the_folder_is_an_allowed_data_directory(self):
        self.assertIn("third_party_licenses", build.ALLOWED_DATA)

    def test_the_pyinstaller_command_adds_the_folder(self):
        commands = []
        with tempfile.TemporaryDirectory() as folder, \
                mock.patch.object(build, "run", side_effect=commands.append), \
                mock.patch.object(build, "assert_package_contents"):
            work = Path(folder) / "work"
            # assert_data_allowlist is NOT stubbed: the real allowlist must accept it.
            build.build_binary(Path(folder) / "dist", work, "windows", "V1.2.0")
        self.assertTrue(commands, "the build never reached PyInstaller")
        command = commands[0]
        adds = [command[i + 1] for i, item in enumerate(command) if item == "--add-data"]
        bundled = [a for a in adds if Path(re.split(r"[;:](?=[^\\/])", a)[0]).name == "third_party_licenses"
                   or a.endswith("third_party_licenses")]
        self.assertTrue(bundled, adds)

    def test_notice_files_cover_the_product_licence_notices_and_every_text(self):
        names = {name for _source, name in build.notice_files()}
        self.assertIn("LICENSE", names)
        self.assertIn("THIRD_PARTY_NOTICES.md", names)
        on_disk = {"third_party_licenses/" + p.relative_to(ROOT / "third_party_licenses").as_posix()
                   for p in (ROOT / "third_party_licenses").rglob("*") if p.is_file()}
        self.assertTrue(on_disk <= names, sorted(on_disk - names)[:5])

    def test_the_archive_carries_the_notices_beside_the_application(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "OmniSuite-test.zip"
            with zipfile.ZipFile(target, "w") as archive:
                archive.writestr("OmniSuite.exe", b"x")
            build.add_notices_to_zip(target)
            with zipfile.ZipFile(target) as archive:
                names = set(archive.namelist())
        self.assertLessEqual({"OmniSuite.exe", "LICENSE", "THIRD_PARTY_NOTICES.md",
                              "third_party_licenses/python/LICENSE.txt"}, names)

    def test_a_linux_folder_receives_the_same_files(self):
        with tempfile.TemporaryDirectory() as folder:
            build.copy_notices_into(Path(folder))
            self.assertTrue((Path(folder) / "LICENSE").is_file())
            self.assertTrue((Path(folder) / "third_party_licenses" / "python" / "LICENSE.txt").is_file())

    def test_adding_the_folder_did_not_open_the_package_gate(self):
        self.assertNotIn("firmware", build.ALLOWED_DATA)
        self.assertIn("docs", build.FORBIDDEN_IN_PACKAGE)


class WindowsVersionResourceTests(unittest.TestCase):
    """OmniSuite.exe's file properties come from the version the build is named for."""

    def test_numeric_version(self):
        self.assertEqual(build.numeric_version("V1.2.0"), (1, 2, 0, 0))
        self.assertEqual(build.numeric_version("V1.2.0-diag1"), (1, 2, 0, 0))
        self.assertEqual(build.numeric_version("V1.10"), (1, 10, 0, 0))
        self.assertEqual(build.numeric_version("garbage"), (0, 0, 0, 0))

    def test_version_file_carries_the_given_version_and_publisher(self):
        with tempfile.TemporaryDirectory() as folder:
            path = build.write_windows_version_file(Path(folder) / "v.txt", "V1.2.0", "OmniSuite")
            text = path.read_text(encoding="utf-8")
        self.assertIn("filevers=(1, 2, 0, 0)", text)
        self.assertIn("StringStruct('ProductVersion', '1.2.0')", text)
        self.assertIn("StringStruct('ProductName', 'OmniSuite')", text)
        self.assertIn("Hall Research Technologies LLC", text)

    def test_the_windows_build_passes_it_to_pyinstaller(self):
        commands = []
        with tempfile.TemporaryDirectory() as folder,                 mock.patch.object(build, "run", side_effect=commands.append),                 mock.patch.object(build, "assert_package_contents"):
            build.build_binary(Path(folder) / "dist", Path(folder) / "work", "windows", "V9.8.7")
            command = commands[0]
            self.assertIn("--version-file", command)
            given = Path(command[command.index("--version-file") + 1])
            self.assertIn("filevers=(9, 8, 7, 0)", given.read_text(encoding="utf-8"))

    def test_no_hand_typed_version_in_the_build_script(self):
        source = (ROOT / "tools" / "build_release.py").read_text(encoding="utf-8")
        self.assertIsNone(re.search(r"V?1\.[0-9]+\.[0-9]+", re.sub(r"#.*", "", source)))


class SmokeChecksTheArchiveCarriesNoticesTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.smoke = _load("smoke_release_notices_under_test", "tools/smoke_release.py")

    def test_a_complete_archive_passes(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for source, name in build.notice_files():
                target = root / "OmniSuite" / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(source.read_bytes())
            self.assertEqual(self.smoke.notice_problems(root), [])

    def test_an_archive_without_them_fails_and_says_which(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertTrue(self.smoke.notice_problems(Path(folder)))
            (Path(folder) / "THIRD_PARTY_NOTICES.md").write_text("x", encoding="utf-8")
            problems = self.smoke.notice_problems(Path(folder))
            self.assertTrue(any("LICENSE" in p for p in problems), problems)
            self.assertTrue(any("third_party_licenses" in p for p in problems), problems)


if __name__ == "__main__":
    unittest.main()
