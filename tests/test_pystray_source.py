"""The LGPL component travels with the release: licence section 11A, the unmodified pystray source,
its provenance, and what the build copies beside the executable."""
import hashlib
import importlib.util
import re
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import build_release as build          # noqa: E402

SOURCE = ROOT / "third_party" / "pystray-0.19.5"
PROVENANCE = (ROOT / "third_party" / "PYSTRAY_PROVENANCE.md").read_text(encoding="utf-8")

SECTION_11A = """11A. COMPONENTS LICENSED UNDER THE GNU LESSER GENERAL PUBLIC LICENSE

Some Third-Party Components, identified in THIRD_PARTY_NOTICES.md, are
licensed under the GNU Lesser General Public License version 3 (each, an
"LGPL Component"). Each LGPL Component remains governed by its own LGPL
license and not by this license. Notwithstanding any other provision of this
license, but only to the extent necessary to preserve the rights that the LGPL
grants you:

  (a) nothing in this license prohibits you from modifying an LGPL Component
      as the LGPL permits;

  (b) nothing in this license prohibits you from reverse engineering the
      Software to the extent the LGPL requires you to be permitted to do so
      in order to debug such modifications; and

  (c) you may replace an LGPL Component contained in the Software with a
      modified, interface-compatible version, and rebuild or recombine the
      Software with it using the source code and build instructions that Hall
      publishes, for your own permitted use, as necessary to exercise those
      rights.

This section does not relicense, and grants no right in, the code and content
of the Software that Hall owns beyond what sections 3 through 9 grant. Every
other provision of this license, including sections 5, 7, 8 and 10, continues
to apply to the Software as a whole, except to the extent necessary to
preserve the rights described in this section. Nothing in this license
modifies the text or terms of any LGPL."""


def read(path):
    return Path(path).read_text(encoding="utf-8").replace("\r\n", "\n")


class LicenceSection11ATests(unittest.TestCase):
    def test_the_approved_text_is_present_verbatim_after_section_11_and_before_12(self):
        licence = read(ROOT / "LICENSE")
        self.assertIn(SECTION_11A, licence)
        self.assertLess(licence.index("11. THIRD-PARTY COMPONENTS"), licence.index("11A. COMPONENTS"))
        self.assertLess(licence.index("11A. COMPONENTS"), licence.index("12. NO WARRANTY"))

    def test_it_survives_termination_and_the_licence_keeps_its_single_identity(self):
        licence = read(ROOT / "LICENSE")
        self.assertIn("11, 11A, 12, 13 and 15 survive termination", licence)
        self.assertIn("Hall Research Technologies Source-Available Software License", licence)
        self.assertIn("Copyright © 2026 Hall Research Technologies LLC", licence)

    def test_the_notices_point_at_the_source_the_section_and_the_rebuild_steps(self):
        notices = read(ROOT / "THIRD_PARTY_NOTICES.md")
        for needle in ("pystray 0.19.5", "third_party/pystray-0.19.5/", "PYSTRAY_PROVENANCE.md", "REBUILD.md",
                       "Section 11A", "COPYING.LGPL", "not modified"):
            self.assertIn(needle, notices)


class PystraySourceTests(unittest.TestCase):
    def listed(self):
        rows = re.findall(r"^\| `([^`]+)` \| (\d+) \| `([0-9a-f]{64})` \|$", PROVENANCE, re.M)
        return {name: (int(size), digest) for name, size, digest in rows
                if not name.startswith("pystray-")}

    def test_every_upstream_file_matches_its_recorded_hash_and_none_is_extra(self):
        listed = self.listed()
        self.assertGreaterEqual(len(listed), 29)
        on_disk = {p.relative_to(SOURCE).as_posix(): p for p in SOURCE.rglob("*") if p.is_file()}
        self.assertEqual(sorted(on_disk), sorted(listed))
        for name, (size, digest) in listed.items():
            data = on_disk[name].read_bytes()
            self.assertEqual((len(data), hashlib.sha256(data).hexdigest()), (size, digest), name)

    def test_the_licence_texts_beside_the_source_are_the_shipped_licence_folder_texts(self):
        for name in ("COPYING", "COPYING.LGPL"):
            # the licence folder's copies may carry the checkout's line endings; the text is the same
            upstream = (SOURCE / name).read_bytes().replace(bytes([13, 10]), bytes([10]))
            shipped = (ROOT / "third_party_licenses" / "pystray" / name).read_bytes()
            self.assertEqual(upstream, shipped.replace(bytes([13, 10]), bytes([10])), name)

    def test_the_source_is_line_ending_exact_in_git(self):
        attributes = read(ROOT / ".gitattributes")
        self.assertIn("third_party/pystray-0.19.5/** -text", attributes)
        for p in SOURCE.rglob("*.py"):
            self.assertNotIn(b"\r\n", p.read_bytes(), p.name)

    def test_the_provenance_states_the_source_and_what_was_checked(self):
        for needle in ("0.19.5", "github.com/moses-palmer/pystray",
                       "none**: PyPI publishes pystray 0.19.5 only as a wheel",
                       "a0c2229d02cf87207297c22d86ffc57c86c227517b038c0d3c59df79295ac617",
                       "2ab6bd15c14b400c9e8271a82c9de8adc0f1a0cacfc14080bdd4fb42bb88ec92", "Moses Palmer"):
            self.assertIn(needle, PROVENANCE)

    def test_the_included_source_equals_the_pystray_this_project_locks_and_installs(self):
        spec = importlib.util.find_spec("pystray")
        if spec is None or not spec.origin:
            self.skipTest("pystray is not installed here")
        installed = Path(spec.origin).parent
        for p in (SOURCE / "lib" / "pystray").rglob("*.py"):
            twin = installed / p.relative_to(SOURCE / "lib" / "pystray")
            self.assertTrue(twin.is_file(), "%s is not in the installed pystray" % twin.name)
            self.assertEqual(p.read_bytes().replace(b"\r\n", b"\n"), twin.read_bytes().replace(b"\r\n", b"\n"),
                             "installed pystray differs from the included source: %s" % p.name)
        self.assertIn("pystray==0.19.5", read(ROOT / "requirements-lock.txt"))


class PackagingTests(unittest.TestCase):
    def test_the_build_places_source_notices_and_rebuild_steps_beside_the_executable(self):
        names = {name for _source, name in build.notice_files()}
        for needed in ("LICENSE", "THIRD_PARTY_NOTICES.md", "REBUILD.md", "third_party/PYSTRAY_PROVENANCE.md",
                       "third_party/pystray-0.19.5/COPYING", "third_party/pystray-0.19.5/COPYING.LGPL",
                       "third_party/pystray-0.19.5/lib/pystray/__init__.py",
                       "third_party_licenses/pystray/COPYING", "third_party_licenses/pystray/COPYING.LGPL"):
            self.assertIn(needed, names)

    def test_the_archive_carries_them_byte_for_byte(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "OmniSuite-test.zip"
            with zipfile.ZipFile(target, "w") as archive:
                archive.writestr("OmniSuite.exe", b"x")
            build.add_notices_to_zip(target)
            with zipfile.ZipFile(target) as archive:
                self.assertEqual(archive.read("third_party/pystray-0.19.5/lib/pystray/_base.py"),
                                 (SOURCE / "lib" / "pystray" / "_base.py").read_bytes())
                self.assertEqual(archive.read("REBUILD.md"), (ROOT / "REBUILD.md").read_bytes())

    def test_the_rebuild_steps_name_the_pinned_build_and_the_replacement_command(self):
        text = read(ROOT / "REBUILD.md")
        for needle in ("requirements-lock.txt", "pyinstaller==6.22.3", "pyinstaller-hooks-contrib==2026.9",
                       "pip install --no-deps --force-reinstall third_party/pystray-0.19.5",
                       "tools/build_release.py", "section 11A"):
            self.assertIn(needle, text)

    def test_the_smoke_check_requires_the_lgpl_material(self):
        spec = importlib.util.spec_from_file_location("smoke_under_test", ROOT / "tools" / "smoke_release.py")
        smoke = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(smoke)
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for source, name in build.notice_files():
                target = root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(source.read_bytes())
            self.assertEqual(smoke.notice_problems(root), [])
            (root / "REBUILD.md").unlink()
            self.assertTrue(any("REBUILD.md" in p for p in smoke.notice_problems(root)))


if __name__ == "__main__":
    unittest.main()
