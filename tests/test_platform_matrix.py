"""The distributed platform matrix: Windows x86_64 and macOS Apple Silicon (arm64), and nothing else.

Three different things are kept apart on purpose:

* the TEST RUNNER platform: where the suite runs in CI;
* the DISTRIBUTED platform: what a release carries, names and offers to the updater;
* an unsupported legacy platform (macOS Intel, Linux): built by V1.2.0 and not since. Nothing is
  built, named, published or offered for it, and the updater says so plainly.

The workflow is read as text (comments stripped, so a comment that names the thing under test cannot
satisfy the assertion), the build script's tables are read as data, and the macOS architecture
verifier is exercised on synthetic Mach-O headers.
"""
import importlib.util
import re
import struct
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT))

import build_release as build          # noqa: E402
import omni_release                    # noqa: E402
import verify_macho_arch as macho      # noqa: E402


def _spec(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


identity = _spec("release_identity_for_matrix_tests", "tools/release_identity.py")
inspect = _spec("inspect_release_for_matrix_tests", "tools/inspect_release.py")
OMNI = omni_release.active_product()


def workflow_text():
    raw = (ROOT / ".github" / "workflows" / "build-cross-platform.yml").read_text(encoding="utf-8")
    return "\n".join(l for l in raw.replace("\r\n", "\n").splitlines() if not l.lstrip().startswith("#"))


def job(text, name):
    match = re.search(r"^  %s:\n(.*?)(?=^  [A-Za-z0-9_-]+:\n|\Z)" % re.escape(name), text, re.S | re.M)
    assert match, "no job named " + name
    return match.group(1)


class DistributedSetTests(unittest.TestCase):
    def test_the_distributed_set_is_exactly_two_and_every_table_agrees(self):
        tails = {t.split("{version}-", 1)[1] for t in build.ARTIFACT_NAMES.values()}
        self.assertEqual(tails, {"Windows-x86_64.zip", "macOS-arm64.zip"})
        self.assertEqual(tails, set(omni_release.PLATFORM_TAILS))
        self.assertEqual(set(build.ARTIFACT_NAMES), {"windows", "arm64"})
        self.assertEqual(set(omni_release.SUPPORTED_PLATFORM_KEYS), {"windows-x86_64", "macos-arm64"})

    def test_every_name_states_its_architecture(self):
        for template in build.ARTIFACT_NAMES.values():
            self.assertTrue("x86_64" in template or "arm64" in template, template)

    def test_the_build_script_cannot_be_asked_for_a_retired_platform(self):
        text = (ROOT / "tools" / "build_release.py").read_text(encoding="utf-8")
        choices = re.search(r'"--suffix", choices=\[([^\]]*)\]', text).group(1)
        self.assertEqual(sorted(re.findall(r'"([a-z0-9_]+)"', choices)), ["arm64", "windows"])
        for suffix in ("linux", "x86_64"):
            with self.assertRaises(KeyError):
                build.package_release(Path("."), suffix, "V1.2.1")

    def test_an_intel_mac_or_linux_host_refuses_to_build(self):
        from unittest import mock
        for system, machine in (("Darwin", "x86_64"), ("Linux", "x86_64"), ("Linux", "aarch64")):
            with self.subTest(system=system, machine=machine), \
                    mock.patch.object(build.platform, "system", return_value=system), \
                    mock.patch.object(build.platform, "machine", return_value=machine):
                with self.assertRaises(RuntimeError):
                    build.resolve_suffix(None)
        with mock.patch.object(build.platform, "system", return_value="Darwin"), \
                mock.patch.object(build.platform, "machine", return_value="arm64"):
            self.assertEqual(build.resolve_suffix(None), "arm64")

    def test_the_publish_gate_requires_exactly_the_two_and_refuses_the_retired_names(self):
        names = [OMNI.artifact_name("V1.2.1", t) for t in omni_release.PLATFORM_TAILS]
        names += [n + ".sha256" for n in list(names)]
        identity.verify_asset_names(names, OMNI, "V1.2.1", require_complete=True)
        for retired in ("macOS-x86_64.zip", "Ubuntu-x86_64.tar.gz"):
            with self.subTest(retired=retired):
                with self.assertRaises(omni_release.IdentityError):
                    identity.verify_asset_names(names + [OMNI.artifact_name("V1.2.1", retired)],
                                                OMNI, "V1.2.1", require_complete=True)
        with self.assertRaisesRegex(omni_release.IdentityError, "missing"):
            identity.verify_asset_names(names[:1], OMNI, "V1.2.1", require_complete=True)

    def test_a_retired_artifact_is_still_recognised_as_this_products_so_an_old_release_is_not_foreign(self):
        for tail in ("macOS-x86_64.zip", "Ubuntu-x86_64.tar.gz"):
            parsed = OMNI.parse_asset("OmniSuite-V1.2.0-" + tail)
            self.assertIsNotNone(parsed, tail)
            self.assertIn(parsed["platform"], omni_release.LEGACY_PLATFORM_KEYS)
            self.assertNotIn(parsed["platform"], omni_release.SUPPORTED_PLATFORM_KEYS)


class WorkflowMatrixTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = workflow_text()
        cls.test = job(cls.text, "test")
        cls.build = job(cls.text, "build")
        cls.publish = job(cls.text, "publish-release")

    def test_no_job_builds_or_tests_on_intel_macos_or_linux(self):
        for name, body in (("test", self.test), ("build", self.build)):
            with self.subTest(job=name):
                for forbidden in ("ubuntu", "macos-15-intel", "macos-13", "macos-12", "macos-latest",
                                  "macOS-x86_64", "Ubuntu-x86_64", "suffix: linux", "suffix: x86_64",
                                  "xvfb", "apt-get", "tar.gz"):
                    self.assertNotIn(forbidden, body)

    def test_the_test_runners_are_windows_x86_64_and_a_named_apple_silicon_runner(self):
        runners = re.findall(r"- os: (\S+)", self.test)
        self.assertEqual(runners, ["windows-latest", "macos-15"])
        self.assertEqual(re.findall(r"- os: (\S+)", self.build), ["windows-latest", "macos-15"])

    def test_the_build_rows_name_the_two_distributed_artifacts_and_their_architecture(self):
        self.assertEqual(re.findall(r"artifact_name: (\S+)", self.build), ["Windows-x86_64", "macOS-arm64"])
        self.assertEqual(re.findall(r"suffix: (\S+)", self.build), ["windows", "arm64"])
        self.assertIn("expect_arch: x86-64", self.build)

    def test_the_architecture_is_asserted_rather_than_trusted(self):
        for body in (self.test, self.build):
            self.assertIn("Windows-X64|macOS-ARM64", body)
        self.assertIn('uname -m', self.build)
        self.assertIn("universal2", self.build)
        self.assertIn("lipo -archs", self.build)
        self.assertIn('test "$archs" = "arm64"', self.build)
        self.assertIn("tools/verify_macho_arch.py --path", self.build)
        self.assertIn("tools/verify_macho_arch.py --zip", self.build)
        self.assertIn("tools/verify_macho_arch.py --bundle-identity", self.build)
        self.assertIn("0x8664", self.build)                     # the Windows PE machine type

    def test_the_macos_signature_state_is_asserted_as_documented(self):
        # Ad hoc, not Developer ID, not notarized: what README, the release notes and
        # docs/INSTALL_MACOS.md say. A change to that state has to change both.
        self.assertIn("codesign --verify --deep --strict", self.build)
        self.assertIn("Signature=adhoc", self.build)
        self.assertIn("Authority=", self.build)
        readme = " ".join((ROOT / "README.md").read_text(encoding="utf-8").split())
        self.assertIn("ad hoc signature", readme)
        self.assertIn("not Developer ID signed and not notarised", readme)

    def test_the_package_is_inspected_and_smoke_tested_on_every_distributed_row(self):
        self.assertLess(self.build.index("tools/inspect_release.py"), self.build.index("tools/smoke_release.py"))
        self.assertIn("tools/release_identity.py verify-assets", self.build)
        self.assertIn("--version", self.build[self.build.index("tools/smoke_release.py"):][:400])

    def test_only_zip_archives_are_collected_inspected_and_uploaded(self):
        self.assertNotIn("tar.gz", self.text)
        self.assertIn("release/*.zip", self.build)
        self.assertIn("--complete", self.publish)

    def test_the_orchestration_jobs_say_they_ship_nothing_built_on_their_runner(self):
        raw = (ROOT / ".github" / "workflows" / "build-cross-platform.yml").read_text(encoding="utf-8")
        self.assertIn("SUPPORTED DISTRIBUTED PLATFORMS", raw)
        self.assertIn("TEST RUNNER", raw)
        self.assertIn("DISTRIBUTED", raw)

    def test_the_whole_matrix_still_gates_publication(self):
        self.assertIn("needs: [identity, build]", self.publish)
        self.assertIn("needs: [identity, test]", self.build)
        self.assertIn("fail-fast: false", self.build)            # every row reports; a partial set cannot publish


def _thin(cpu):
    return struct.pack("<Ii", 0xFEEDFACF, cpu) + b"\0" * 24


def _fat(*cpus):
    out = struct.pack(">II", 0xCAFEBABE, len(cpus))
    for cpu in cpus:
        out += struct.pack(">IIIII", cpu, 0, 0x1000, 0x1000, 14)
    return out + b"\0" * 32


ARM, X86 = macho.CPU_ARM64, macho.CPU_X86_64
MAIN = "OmniSuite.app/Contents/MacOS/OmniSuite"


class MachOVerifierTests(unittest.TestCase):
    def problems(self, entries, main="Contents/MacOS/OmniSuite"):
        return macho.problems_for(entries, "arm64", main)

    def test_a_thin_arm64_bundle_passes(self):
        self.assertEqual(self.problems([(MAIN, _thin(ARM)), ("OmniSuite.app/Contents/Frameworks/a.dylib", _thin(ARM)),
                                        ("OmniSuite.app/Contents/Resources/ui/index.html", b"<html>")]), [])

    def test_an_x86_64_launcher_is_refused(self):
        found = self.problems([(MAIN, _thin(X86))])
        self.assertTrue(any("x86_64" in p for p in found), found)

    def test_a_universal_launcher_is_refused_even_though_it_contains_arm64(self):
        found = self.problems([(MAIN, _fat(ARM, X86))])
        self.assertTrue(any("universal" in p for p in found), found)
        self.assertTrue(any("main executable" in p for p in found), found)

    def test_one_intel_library_inside_an_arm64_bundle_is_refused(self):
        found = self.problems([(MAIN, _thin(ARM)), ("OmniSuite.app/Contents/Frameworks/b.so", _thin(X86))])
        self.assertTrue(any("b.so" in p and "x86_64" in p for p in found), found)

    def test_a_universal_library_is_refused(self):
        found = self.problems([(MAIN, _thin(ARM)), ("x/libz.dylib", _fat(X86, ARM))])
        self.assertTrue(any("libz.dylib" in p and "universal" in p for p in found), found)

    def test_a_32_bit_or_unknown_cpu_is_refused_not_ignored(self):
        old = struct.pack("<Ii", 0xFEEDFACE, 7) + b"\0" * 24
        self.assertTrue(self.problems([(MAIN, _thin(ARM)), ("old", old)]))
        self.assertTrue(self.problems([(MAIN, _thin(0x01000099))]))

    def test_an_empty_or_wrong_package_is_not_a_pass(self):
        self.assertTrue(self.problems([]))
        self.assertTrue(self.problems([("a.txt", b"hello"), ("b.py", b"print()")]))
        found = self.problems([("OmniSuite.app/Contents/Frameworks/a.dylib", _thin(ARM))])
        self.assertTrue(any("main executable" in p for p in found), found)

    def test_an_empty_package_fails_on_its_own_even_when_no_main_executable_is_named(self):
        # The "nothing was found" rule is separate from the "main executable missing" rule; with no
        # main named only the first can refuse an empty or non-Mach-O package.
        found = macho.problems_for([], "arm64", None)
        self.assertTrue(any("no Mach-O file" in p for p in found), found)
        found = macho.problems_for([("a.txt", b"hello")], "arm64", None)
        self.assertTrue(any("no Mach-O file" in p for p in found), found)
        self.assertEqual(macho.problems_for([("x", _thin(ARM))], "arm64", None), [])

    def test_a_java_class_file_is_not_mistaken_for_a_universal_binary(self):
        java = struct.pack(">IHH", 0xCAFEBABE, 0, 52) + b"\0" * 32
        self.assertIsNone(macho.classify(java))
        self.assertEqual(self.problems([(MAIN, _thin(ARM)), ("x/Foo.class", java)]), [])

    def test_a_truncated_header_is_never_a_crash(self):
        for head in (b"", b"\xcf\xfa", b"\xca\xfe\xba\xbe", _fat(ARM, X86)[:10]):
            macho.classify(head)

    def test_zip_input_is_read_without_extracting(self):
        with tempfile.TemporaryDirectory() as folder:
            good = Path(folder) / "OmniSuite-V1.2.1-macOS-arm64.zip"
            with zipfile.ZipFile(good, "w") as archive:
                archive.writestr(MAIN, _thin(ARM))
            self.assertEqual(macho.main(["--zip", str(good)]), 0)
            bad = Path(folder) / "bad.zip"
            with zipfile.ZipFile(bad, "w") as archive:
                archive.writestr(MAIN, _fat(ARM, X86))
            self.assertEqual(macho.main(["--zip", str(bad)]), 1)

    def test_directory_input_and_bundle_identity(self):
        with tempfile.TemporaryDirectory() as folder:
            app = Path(folder) / "OmniSuite.app"
            (app / "Contents" / "MacOS").mkdir(parents=True)
            (app / "Contents" / "MacOS" / "OmniSuite").write_bytes(_thin(ARM))
            self.assertEqual(macho.main(["--path", str(app)]), 0)
            self.assertEqual(macho.main(["--bundle-identity", str(app)]), 1)        # no Info.plist yet
            import plistlib
            info = {"CFBundleIdentifier": OMNI.bundle_identifier, "CFBundleExecutable": "OmniSuite",
                    "NSLocalNetworkUsageDescription": OMNI.local_network_usage}
            (app / "Contents" / "Info.plist").write_bytes(plistlib.dumps(info))
            self.assertEqual(macho.main(["--bundle-identity", str(app)]), 0)
            info["CFBundleIdentifier"] = "SomethingElse"
            (app / "Contents" / "Info.plist").write_bytes(plistlib.dumps(info))
            self.assertEqual(macho.main(["--bundle-identity", str(app)]), 1)

    def test_bundle_identity_decisions(self):
        good = {"CFBundleIdentifier": "OmniSuite", "CFBundleExecutable": "OmniSuite",
                "NSLocalNetworkUsageDescription": "x"}
        self.assertEqual(macho.bundle_identity_problems(good, "OmniSuite"), [])
        for key in good:
            broken = dict(good)
            broken.pop(key)
            self.assertTrue(macho.bundle_identity_problems(broken, "OmniSuite"), key)
        self.assertEqual(OMNI.bundle_identifier, "OmniSuite")          # unchanged since the first release


class LicenceMaterialTests(unittest.TestCase):
    """What a distribution carries for the licence and the LGPL component, asserted on the
    packages as the build assembles them, with this product's own inspector as the judge."""

    VERSION = "V1.2.1"

    def assemble(self, folder, platform):
        name = OMNI.artifact_name(self.VERSION, "Windows-x86_64.zip" if platform == "windows" else "macOS-arm64.zip")
        target = Path(folder) / name
        with zipfile.ZipFile(target, "w") as archive:
            if platform == "windows":
                archive.writestr("OmniSuite.exe", b"MZ")
            else:
                archive.writestr("OmniSuite.app/Contents/MacOS/OmniSuite", _thin(ARM))
                archive.writestr("OmniSuite.app/Contents/Info.plist", b"<plist/>")
        if platform == "windows":
            build.add_notices_to_zip(target)
        else:
            build.add_macos_readme(target, OMNI)
            build.add_notices_to_zip(target)
        return target

    def test_both_distributed_packages_pass_the_strict_inspector_with_every_licence_requirement(self):
        for platform in ("windows", "macos"):
            with self.subTest(platform=platform), tempfile.TemporaryDirectory() as folder:
                target = self.assemble(folder, platform)
                self.assertEqual(inspect.problems_for(target, self.VERSION), [])
                names = {n for n, _ in inspect.members(target)}
                for required in inspect.REQUIRED_NAMES:
                    self.assertIn(required, names)
                self.assertTrue({"LICENSE", "THIRD_PARTY_NOTICES.md", "REBUILD.md"} <= names)

    def test_losing_any_licence_requirement_fails_the_inspector(self):
        for missing in inspect.REQUIRED_NAMES + ("LICENSE", "THIRD_PARTY_NOTICES.md"):
            with self.subTest(missing=missing), tempfile.TemporaryDirectory() as folder:
                target = self.assemble(folder, "windows")
                stripped = Path(folder) / "stripped" / target.name
                stripped.parent.mkdir()
                with zipfile.ZipFile(target) as source, zipfile.ZipFile(stripped, "w") as out:
                    for info in source.infolist():
                        if info.filename != missing:
                            out.writestr(info, source.read(info))
                self.assertTrue(inspect.problems_for(stripped, self.VERSION), missing)

    def test_the_licence_is_the_hall_licence_with_the_approved_lgpl_section(self):
        text = (ROOT / "LICENSE").read_text(encoding="utf-8").replace("\r\n", "\n")
        self.assertIn("Hall Research Technologies Source-Available Software License", text)
        self.assertIn("11A. COMPONENTS LICENSED UNDER THE GNU LESSER GENERAL PUBLIC LICENSE", text)
        self.assertIn("you may replace an LGPL Component", text)

    def test_the_gpl_and_lgpl_texts_and_the_exact_pystray_source_travel_with_the_build(self):
        names = {name for _source, name in build.notice_files()}
        for needed in ("third_party_licenses/pystray/COPYING", "third_party_licenses/pystray/COPYING.LGPL",
                       "third_party/pystray-0.19.5/COPYING", "third_party/pystray-0.19.5/COPYING.LGPL",
                       "third_party/pystray-0.19.5/setup.py", "third_party/pystray-0.19.5/lib/pystray/_win32.py",
                       "third_party/pystray-0.19.5/lib/pystray/_darwin.py",
                       "third_party/PYSTRAY_PROVENANCE.md", "REBUILD.md"):
            self.assertIn(needed, names)
        gpl = (ROOT / "third_party_licenses" / "pystray" / "COPYING").read_text(encoding="utf-8")
        lgpl = (ROOT / "third_party_licenses" / "pystray" / "COPYING.LGPL").read_text(encoding="utf-8")
        self.assertIn("GNU GENERAL PUBLIC LICENSE", gpl.upper())
        self.assertIn("GNU LESSER GENERAL PUBLIC LICENSE", lgpl.upper())
        info = (ROOT / "third_party" / "pystray-0.19.5" / "lib" / "pystray" / "_info.py").read_text(encoding="utf-8")
        self.assertIn("__version__ = (0, 19, 5)", info)
        self.assertIn("0.19.5", (ROOT / "third_party" / "PYSTRAY_PROVENANCE.md").read_text(encoding="utf-8"))

    def test_the_embedded_copy_is_required_by_the_package_table_of_contents_check(self):
        every = ["LICENSE", "THIRD_PARTY_NOTICES.md", "third_party_licenses/pystray/COPYING",
                 "third_party_licenses/pystray/COPYING.LGPL"]
        self.assertEqual(build.missing_licence_material(every + ["ui/index.html"]), [])
        for lost in every:
            with self.subTest(lost=lost):
                self.assertEqual(build.missing_licence_material([n for n in every if n != lost]), [lost])
        self.assertEqual(build.missing_licence_material(["ui\\index.html"]), every)
        self.assertEqual(build.missing_licence_material(["third_party_licenses\\pystray\\COPYING"]),
                         [n for n in every if n != "third_party_licenses/pystray/COPYING"])

    def test_the_windows_build_enforces_it_and_a_synthetic_toc_proves_the_enforcement_runs(self):
        text = (ROOT / "tools" / "build_release.py").read_text(encoding="utf-8")
        self.assertIn("assert_package_contents(work_path, require_licence_material=onefile)", text)
        with tempfile.TemporaryDirectory() as folder:
            work = Path(folder)
            rows = [(n, "/src/" + n, "DATA") for n in ("LICENSE", "THIRD_PARTY_NOTICES.md")]
            (work / "PKG-00.toc").write_text(repr(("p", rows, {})), encoding="utf-8")
            build.assert_package_contents(work)                              # forbidden-only: passes
            with self.assertRaises(SystemExit) as caught:
                build.assert_package_contents(work, require_licence_material=True)
            self.assertIn("COPYING.LGPL", str(caught.exception))

    def test_no_ai_is_named_as_a_copyright_holder_anywhere_in_the_distributed_materials(self):
        holder = re.compile(r"copyright[^\n]{0,80}\b(claude|anthropic|openai|chatgpt|copilot|gemini)\b", re.I)
        attribution = re.compile(r"(co-authored-by|generated (with|by))[^\n]{0,60}\b(claude|anthropic|openai|chatgpt)\b",
                                 re.I)
        texts = [ROOT / "LICENSE", ROOT / "THIRD_PARTY_NOTICES.md", ROOT / "README.md", ROOT / "REBUILD.md",
                 ROOT / "ui" / "user-guide.html", ROOT / "third_party" / "PYSTRAY_PROVENANCE.md"]
        texts += sorted((ROOT / "third_party_licenses").rglob("*"))
        for path in texts:
            if not path.is_file():
                continue
            body = path.read_text(encoding="utf-8", errors="ignore")
            self.assertIsNone(holder.search(body), path.name)
            self.assertIsNone(attribution.search(body), path.name)
        licence = (ROOT / "LICENSE").read_text(encoding="utf-8")
        self.assertIn("Copyright \u00a9 2026 Hall Research Technologies LLC", licence)


if __name__ == "__main__":
    unittest.main()
