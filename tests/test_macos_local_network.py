"""macOS Local Network access: the declaration, the guidance, and permission changes.

What is real here and what is simulated, stated plainly:

* REAL: the Info.plist edit and the signing commands the build issues (the
  commands are recorded, not run), the smoke check's reading of a bundle, the
  wording of every refusal on each platform, and the User Guide text.
* SIMULATED: macOS itself. The operating system's behaviour -- instant
  EHOSTUNREACH or EPERM while a permission prompt is open or after "Don't Allow",
  connections working after Allow -- is reproduced by replacing the connection
  primitive. That behaviour is the leading explanation of the reported defect,
  not a measured fact, and the final validation is on a real Mac. See
  TESTING.md for the checklist.

Nothing here touches the network, a signing identity or any system setting.
"""
import errno
import importlib.util
import os
import plistlib
import socket
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

try:
    from . import test_usb_integration as base
except ImportError:                                   # loaded by path, no package
    import test_usb_integration as base

import omni_release
from omni_release import PRODUCTS

srv = base.srv
ROOT = Path(__file__).resolve().parent.parent
RANGE = "192.0.2.1-20"


def _load(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


build = _load("build_release_for_local_network_tests", "tools/build_release.py")
smoke = _load("smoke_release_for_local_network_tests", "tools/smoke_release.py")


def _refuse(code):
    def connect(address, timeout=None, *args, **kwargs):
        raise OSError(code, os.strerror(code) if code < 10000 else "socket error")
    return connect


def _make_bundle(folder, extra=None):
    app = Path(folder) / "OmniSuite.app"
    (app / "Contents" / "MacOS").mkdir(parents=True)
    info = {"CFBundleIdentifier": "OmniSuite", "CFBundleName": "OmniSuite",
            "CFBundleShortVersionString": "0.0.0", "NSHighResolutionCapable": True}
    info.update(extra or {})
    with (app / "Contents" / "Info.plist").open("wb") as handle:
        plistlib.dump(info, handle)
    (app / "Contents" / "MacOS" / "OmniSuite").write_bytes(b"x")
    return app


class UsageDescriptionTests(unittest.TestCase):
    def test_every_product_has_its_own_non_empty_description(self):
        texts = {p.id: p.local_network_usage for p in PRODUCTS.values()}
        for pid, text in texts.items():
            with self.subTest(product=pid):
                self.assertGreater(len(text.strip()), 40)
                self.assertIn(PRODUCTS[pid].name, text)
        self.assertEqual(len(set(texts.values())), len(texts), "the two must not be identical")

    def test_the_description_says_what_is_done_and_claims_nothing_else(self):
        for product in PRODUCTS.values():
            text = product.local_network_usage.lower()
            self.assertIn("local network", text)
            # It has to say what the app does there, not merely that it is there.
            for what in ("omnistream", "encoders", "decoders", "usb extenders", "find and manage"):
                self.assertIn(what, text)
            for claim in ("bonjour", "mdns", "internet", "cloud", "analytics"):
                self.assertNotIn(claim, text, "the description must not describe %s" % claim)

    def test_no_bonjour_services_are_declared(self):
        """The application uses direct TCP and UDP broadcast, not Bonjour."""
        source = (ROOT / "tools" / "build_release.py").read_text(encoding="utf-8")
        code = "\n".join(l for l in source.splitlines() if not l.lstrip().startswith("#"))
        self.assertNotIn('info["NSBonjourServices"]', code)
        self.assertIn("NSLocalNetworkUsageDescription", code)


class BundleEditTests(unittest.TestCase):
    """declare_local_network_usage edits the plist, then signs, then verifies."""

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = folder.name
        self.app = _make_bundle(self.folder)
        self.commands = []
        self.plist_when_signed = []

    def fake_run(self, cmd, *args, **kwargs):
        self.commands.append(list(cmd))
        with (self.app / "Contents" / "Info.plist").open("rb") as handle:
            self.plist_when_signed.append(plistlib.load(handle))

    def declare(self, usage="OmniSuite finds your devices."):
        with mock.patch.object(build, "run", side_effect=self.fake_run):
            build.declare_local_network_usage(self.app, usage)

    def plist(self):
        with (self.app / "Contents" / "Info.plist").open("rb") as handle:
            return plistlib.load(handle)

    def test_the_description_is_added_and_existing_keys_are_kept(self):
        self.declare("OmniSuite finds your devices.")
        info = self.plist()
        self.assertEqual(info["NSLocalNetworkUsageDescription"], "OmniSuite finds your devices.")
        self.assertEqual(info["CFBundleIdentifier"], "OmniSuite")
        self.assertTrue(info["NSHighResolutionCapable"])
        self.assertNotIn("NSBonjourServices", info)

    def test_the_bundle_is_signed_again_after_the_edit_and_then_verified(self):
        self.declare()
        self.assertEqual(self.commands, [
            ["codesign", "--force", "--deep", "--sign", "-", str(self.app)],
            ["codesign", "--verify", "--deep", "--strict", str(self.app)]])
        for seen in self.plist_when_signed:
            self.assertIn("NSLocalNetworkUsageDescription", seen,
                          "signing before the edit would sign a bundle that is then invalidated")

    def test_no_signing_identity_is_used(self):
        self.declare()
        self.assertEqual(self.commands[0][self.commands[0].index("--sign") + 1], "-",
                         "ad-hoc only: no certificate or identity is involved")

    def test_declaring_twice_is_harmless(self):
        self.declare("first")
        self.declare("second")
        self.assertEqual(self.plist()["NSLocalNetworkUsageDescription"], "second")

    def test_an_empty_description_is_refused_before_anything_is_touched(self):
        before = (self.app / "Contents" / "Info.plist").read_bytes()
        for usage in ("", "   "):
            with self.subTest(usage=usage), self.assertRaises(RuntimeError):
                self.declare(usage)
        self.assertEqual(self.commands, [])
        self.assertEqual((self.app / "Contents" / "Info.plist").read_bytes(), before)

    def test_a_signing_failure_fails_the_build(self):
        def explode(cmd, *a, **k):
            raise subprocess.CalledProcessError(1, cmd)
        with mock.patch.object(build, "run", side_effect=explode):
            with self.assertRaises(subprocess.CalledProcessError):
                build.declare_local_network_usage(self.app, "x")

    def test_a_failed_verification_fails_the_build(self):
        calls = []

        def second_fails(cmd, *a, **k):
            calls.append(cmd)
            if "--verify" in cmd:
                raise subprocess.CalledProcessError(1, cmd)
        with mock.patch.object(build, "run", side_effect=second_fails):
            with self.assertRaises(subprocess.CalledProcessError):
                build.declare_local_network_usage(self.app, "x")
        self.assertEqual(len(calls), 2)


class PackagingHookTests(unittest.TestCase):
    """Only the macOS bundles are edited; Windows and Linux are untouched."""

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
            _make_bundle(dist)
        events = []
        with mock.patch.object(build, "RELEASE_DIR", Path(folder.name) / "release"), \
                mock.patch.object(build, "declare_local_network_usage",
                                  side_effect=lambda app, usage, name=None: events.append(("declare", usage))), \
                mock.patch.object(build, "zip_macos_app",
                                  side_effect=lambda *a: events.append(("zip-mac",))), \
                mock.patch.object(build, "zip_path", side_effect=lambda *a: events.append(("zip",))), \
                mock.patch.object(build, "tar_path", side_effect=lambda *a: events.append(("tar",))):
            build.package_release(dist, suffix, "V1.0.0-test")
        return events

    def test_both_mac_architectures_declare_before_they_are_zipped(self):
        for suffix in ("arm64", "x86_64"):
            with self.subTest(suffix=suffix):
                events = self.package(suffix)
                self.assertEqual([e[0] for e in events], ["declare", "zip-mac"])
                self.assertEqual(events[0][1], omni_release.active_product().local_network_usage)

    def test_windows_and_linux_never_touch_a_macos_plist(self):
        for suffix, expected in (("windows", "zip"), ("linux", "tar")):
            with self.subTest(suffix=suffix):
                self.assertEqual([e[0] for e in self.package(suffix)], [expected])


class SmokeCheckTests(unittest.TestCase):
    def test_a_missing_or_blank_description_is_a_problem(self):
        for info in ({}, {"NSLocalNetworkUsageDescription": ""},
                     {"NSLocalNetworkUsageDescription": "  "},
                     {"NSLocalNetworkUsageDescription": 7},
                     {"NSLocalNetworkUsageDescription": None}):
            with self.subTest(info=info):
                self.assertEqual(len(smoke.local_network_problems(info)), 1)
        self.assertEqual(smoke.local_network_problems(
            {"NSLocalNetworkUsageDescription": "Finds devices."}), [])

    def check(self, extra):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        app = _make_bundle(folder.name, extra)
        with mock.patch.object(smoke.shutil, "which", return_value=None):
            return smoke.check_macos_bundle(app / "Contents" / "MacOS" / "OmniSuite")

    def test_the_shipped_bundle_is_read_not_the_build_intent(self):
        self.assertEqual(self.check({"NSLocalNetworkUsageDescription": "Finds devices."}), [])
        self.assertEqual(len(self.check({})), 1)

    def test_an_unreadable_plist_is_a_problem_not_a_pass(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        app = _make_bundle(folder.name)
        (app / "Contents" / "Info.plist").write_bytes(b"not a plist")
        problems = smoke.check_macos_bundle(app / "Contents" / "MacOS" / "OmniSuite")
        self.assertEqual(len(problems), 1)
        self.assertIn("could not read", problems[0])

    def test_a_signature_that_does_not_verify_is_a_problem(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        app = _make_bundle(folder.name, {"NSLocalNetworkUsageDescription": "Finds devices."})

        def codesign(cmd, **kwargs):
            return subprocess.CompletedProcess(cmd, 1 if "--verify" in cmd else 0, "", "invalid signature")
        with mock.patch.object(smoke.shutil, "which", return_value="/usr/bin/codesign"), \
                mock.patch.object(smoke.subprocess, "run", side_effect=codesign):
            problems = smoke.check_macos_bundle(app / "Contents" / "MacOS" / "OmniSuite")
        self.assertEqual(problems, ["the bundle's signature does not verify"])

    def test_the_check_runs_for_macos_artifacts_only(self):
        source = (ROOT / "tools" / "smoke_release.py").read_text(encoding="utf-8")
        hook = source[source.index("def main()"):]
        self.assertIn('platform.system() == "Darwin"', hook)
        self.assertLess(hook.index("check_macos_bundle"), hook.index("return run(binary"))


class GuidanceByPlatformTests(unittest.TestCase):
    VERDICT = {"dominant": "EHOSTUNREACH", "blocked": 254, "total": 254}

    def message(self, system, dominant="EHOSTUNREACH"):
        return srv._scan_failure_message(dict(self.VERDICT, dominant=dominant), {}, system=system)

    def test_macos_is_told_where_the_setting_is(self):
        for dominant in ("EHOSTUNREACH", "EPERM", "EACCES", "ENETUNREACH"):
            with self.subTest(dominant=dominant):
                text = self.message("Darwin", dominant)
                self.assertIn("System Settings > Privacy & Security > Local Network", text)
                self.assertIn("switch OmniSuite on", text)
                self.assertIn("scan again", text)
                self.assertIn("prompt", text)
                self.assertIn("not the same as finding no devices", text)

    def test_windows_and_linux_never_hear_about_macos(self):
        for system in ("Windows", "Linux"):
            for dominant in ("EHOSTUNREACH", "EPERM", "EACCES", "ENETUNREACH", "EMFILE"):
                with self.subTest(system=system, dominant=dominant):
                    text = self.message(system, dominant)
                    for foreign in ("macOS", "Local Network", "System Settings"):
                        self.assertNotIn(foreign, text)

    def test_permission_errors_off_macos_point_at_firewall_policy(self):
        self.assertIn("firewall", self.message("Windows", "EPERM"))
        self.assertIn("firewall", self.message("Linux", "EACCES"))

    def test_descriptor_exhaustion_is_never_blamed_on_a_permission(self):
        text = srv._scan_failure_message({"dominant": "EMFILE", "blocked": 9, "total": 9},
                                         {"fd_soft_limit": 256}, system="Darwin")
        self.assertIn("file descriptors", text)
        self.assertIn("limit 256", text)
        self.assertNotIn("Local Network", text)

    def test_likely_cause_is_a_machine_readable_attribution(self):
        cases = [("Darwin", "EHOSTUNREACH", "local_network_permission"),
                 ("Darwin", "EPERM", "local_network_permission"),
                 ("Darwin", "EMFILE", "descriptor_exhaustion"),
                 ("Windows", "EPERM", "blocked_by_firewall_or_policy"),
                 ("Linux", "EHOSTUNREACH", "no_route"),
                 ("Windows", "ENETUNREACH", "no_route")]
        for system, dominant, expected in cases:
            with self.subTest(system=system, dominant=dominant):
                self.assertEqual(srv._scan_likely_cause(dominant, system), expected)


class PermissionStateTransitionTests(base.ServerTestBase):
    """Allow, deny and change -- with macOS's behaviour simulated at the socket.

    While a Local Network prompt is open, or after Don't Allow, connections to
    the local subnet fail at once. After Allow they work. Each scan below is a
    separate request, as it would be for an operator who answers a prompt and
    presses Scan again.
    """

    UNIT_IP = "192.0.2.7"

    def setUp(self):
        super().setUp()
        self.unit = {"ip": self.UNIT_IP, "mac": "02:00:00:00:00:07", "role": "encoder",
                     "model": "synthetic", "version": "0", "hostname": "synthetic-1"}

    def scan(self, connector, system="Darwin"):
        with mock.patch.object(srv.socket, "create_connection", side_effect=connector), \
                mock.patch.object(srv.platform, "system", return_value=system), \
                mock.patch.object(srv, "_probe_one", side_effect=lambda ip, *a, **k:
                                  dict(self.unit) if ip == self.UNIT_IP else None):
            return self.client.post("/api/scan", json={"targets": RANGE})

    @staticmethod
    def allowed(address, timeout=None, *a, **k):
        if address[0] == "192.0.2.7":
            return mock.MagicMock()
        raise socket.timeout("timed out")

    def test_denied_then_allowed_then_revoked(self):
        denied = _refuse(errno.EHOSTUNREACH)

        first = self.scan(denied)
        self.assertEqual(first.status_code, 503)
        body = first.get_json()
        self.assertEqual(body["reason"], "scan_blocked")
        self.assertEqual(body["likely_cause"], "local_network_permission")
        self.assertIn("System Settings > Privacy & Security > Local Network", body["error"])
        self.assertFalse(srv.CACHE.exists(), "a refused scan must not create a device list")

        second = self.scan(self.allowed)                      # the operator pressed Allow
        self.assertEqual(second.status_code, 200)
        ok = second.get_json()
        self.assertEqual(ok["reachable"], 1)
        self.assertIn(self.UNIT_IP, [u["ip"] for u in ok["units"]])
        before = srv.CACHE.read_bytes()

        third = self.scan(denied)                             # revoked in System Settings
        self.assertEqual(third.status_code, 503)
        self.assertEqual(third.get_json()["likely_cause"], "local_network_permission")
        self.assertEqual(srv.CACHE.read_bytes(), before,
                         "a blocked scan must leave the devices already found exactly as they were")

        fourth = self.scan(self.allowed)                      # switched back on
        self.assertEqual(fourth.status_code, 200)
        self.assertEqual(fourth.get_json()["reachable"], 1)

    def test_a_scan_that_ran_while_the_prompt_was_open_is_not_reported_as_an_empty_network(self):
        body = self.scan(_refuse(errno.EPERM)).get_json()
        self.assertIs(body["ok"], False)
        self.assertIn("prompt", body["error"])
        self.assertNotIn("units", body)

    def test_a_genuinely_empty_network_is_not_a_permission_failure(self):
        def silent(address, timeout=None, *a, **k):
            raise socket.timeout("timed out")
        response = self.scan(silent)
        body = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(body["reachable"], 0)
        self.assertNotIn("likely_cause", body)
        self.assertNotIn("reason", body)

    def test_hosts_that_answer_with_a_refusal_prove_the_network_is_reachable(self):
        """ECONNREFUSED comes from a host that received the connection."""
        body = self.scan(_refuse(errno.ECONNREFUSED)).get_json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["diagnostics"]["probe_outcomes"], {"refused": 20})

    def test_the_platform_is_recorded_in_the_diagnostics(self):
        for system in ("Darwin", "Windows", "Linux"):
            with self.subTest(system=system):
                body = self.scan(_refuse(errno.EHOSTUNREACH), system=system).get_json()
                self.assertEqual(body["diagnostics"]["platform"]["system"], system)

    def test_windows_and_linux_keep_their_behaviour(self):
        for system in ("Windows", "Linux"):
            with self.subTest(system=system):
                ok = self.scan(self.allowed, system=system)
                self.assertEqual(ok.status_code, 200)
                self.assertEqual(ok.get_json()["reachable"], 1)
                blocked = self.scan(_refuse(errno.EPERM), system=system)
                self.assertEqual(blocked.status_code, 503)
                body = blocked.get_json()
                self.assertEqual(body["likely_cause"], "blocked_by_firewall_or_policy")
                self.assertNotIn("Local Network", body["error"])


class UserGuideTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = (ROOT / "ui" / "user-guide.html").read_text(encoding="utf-8")
        start = cls.html.index('id="macos-local-network"')
        cls.section = cls.html[start:cls.html.index("</section>", start)]

    def test_the_section_exists_and_is_linked_from_the_failure_text(self):
        self.assertIn('href="#macos-local-network"', self.html)

    def test_it_gives_the_path_and_the_three_states(self):
        for needed in ("System Settings", "Privacy &amp; Security", "Local Network",
                       "Choose Allow", "Don&rsquo;t Allow", "scan again"):
            self.assertIn(needed, self.section)

    def test_it_is_honest_about_what_omnisuite_does_not_do(self):
        self.assertIn("does not change this setting", self.section)
        self.assertIn("does not reset it", self.section)
        self.assertIn("not signed", self.section)

    def test_it_says_windows_and_linux_are_different(self):
        self.assertIn("Windows and Linux do not have this permission", self.section)

    def test_it_uses_no_internal_names(self):
        for internal in ("EHOSTUNREACH", "EPERM", "NSLocalNetworkUsageDescription", "TCC", "api/"):
            self.assertNotIn(internal, self.section)


if __name__ == "__main__":
    unittest.main()
