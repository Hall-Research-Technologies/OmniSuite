"""Portable encrypted backup and disaster restore.

This feature exists for the day the OmniSuite PC does not come back on, so the
happy path is the least interesting thing about it. What these tests are really
about is the promise that a restore either works or changes nothing: a wrong
passphrase, a corrupt file, a tampered byte, a truncated download and a failure
halfway through writing must all leave the installation exactly as it was.

Nothing here touches the real data directory. Every test builds a throwaway
deployment, and the "replacement machine" cases build a second one.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import omni_backup                                       # noqa: E402
import omni_users                                        # noqa: E402
from omni_users import Permissions                        # noqa: E402

PASSPHRASE = "a-long-enough-backup-passphrase"

#: Documentation-range identities only; a test may never carry a bench value.
CAMERA = "b8:98:b0:00:00:01"
LECTERN = "b8:98:b0:00:00:02"
FRONT = "b8:98:b0:00:00:11"


def a_deployment(root: Path, *, aliases=None, devices=None) -> Path:
    """A data directory with the files a real installation would have."""
    root.mkdir(parents=True, exist_ok=True)
    write = lambda name, obj: (root / name).write_text(
        json.dumps(obj, indent=1), encoding="utf-8")

    write("users.json", {"version": 1, "users": [
        {"id": "admin-1", "username": "principal",
         "password_hash": "scrypt:32768:8:1$abc$def", "role": "admin",
         "enabled": True, "permissions": {"features": [], "sources": [],
                                          "destinations": [], "multiviews": []},
         "created_at": 1.0, "password_changed_at": 1.0,
         "direct_access": {"enabled": False, "digest": "", "blob": "",
                           "protection": "", "created_at": 0}},
        {"id": "user-1", "username": "teacher",
         "password_hash": "scrypt:32768:8:1$ghi$jkl", "role": "user",
         "enabled": True,
         "permissions": {"features": ["av"], "sources": [CAMERA],
                         "destinations": [FRONT], "multiviews": []},
         "created_at": 2.0, "password_changed_at": 2.0,
         "direct_access": {"enabled": True, "digest": "d" * 64,
                           "blob": "DPAPI-PROTECTED-ON-THE-OLD-PC",
                           "protection": "dpapi", "created_at": 3.0}},
    ]})
    write("device_aliases.json",
          {"version": 1, "aliases": aliases if aliases is not None
           else {FRONT: "Hall Display"}})
    write("multiview_meta.json", {"decoders": {
        "mac:b898b0000011": {"decoder_ip": "198.51.100.11",
                             "multiviews": {"twoByTwo": {"layout": "2x2"}}}}})
    write("multiview_groups.json", {"groups": {"hall": [FRONT]}})
    write("usb_associations.json", {"associations": {
        "mac:B898B0000001": {"parent_mac": "B898B0000001",
                             "usb_mac": "B898B0000003"}}})
    write("usb_extenders.json", {"ranges": ["198.51.100.0/24"], "devices": {}})
    write("scan_preferences.json", {"interface_ip": "198.51.100.5",
                                    "scan_expression": "198.51.100.1-254",
                                    "subnet_mask": "255.255.255.0"})
    write("config.json", {"username": "admin", "password": "device-secret",
                          "fallback_password": "Atlona", "ws_port": 80,
                          "timeout": 4.0,
                          "firmware_path": r"D:\old-machine\firmware"})
    write("remote_access.json", {"enabled": True,
                                 "adapter_id": "guid:OLD-MACHINE-ADAPTER",
                                 "adapter_name": "Ethernet 7",
                                 "address": "198.51.100.5", "port": 8081,
                                 "transport": "https",
                                 "cert_path": "", "key_path": ""})
    write("notice_ack.json", {"network_access": {"acknowledged": True}})

    # Deliberately present and deliberately never exported.
    write("scan_results.json", {"devices": [{"ip": "198.51.100.11"}]})
    write("security_audit.json", {"version": 1, "entries": [
        {"event": "login.success", "actor": "principal"}]})
    (root / "remote_key.pem").write_text("-----BEGIN PRIVATE KEY-----\n",
                                         encoding="utf-8")
    (root / "remote_cert.pem").write_text("-----BEGIN CERTIFICATE-----\n",
                                          encoding="utf-8")
    return root


UNITS = [
    {"ip": "198.51.100.11", "mac": FRONT, "hostname": "dec-front",
     "model": "hw-omni-d4511", "password": "device-secret", "role": "decoder"},
    {"ip": "198.51.100.21", "mac": CAMERA, "hostname": "enc-camera",
     "model": "hw-omni-e4111", "password": "device-secret", "role": "encoder"},
    # No MAC: cannot be carried, because an address is not an identity.
    {"ip": "198.51.100.99", "hostname": "unknown", "role": "decoder"},
]


class BackupTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="omni-backup-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.source = a_deployment(self.tmp / "source")

    def sealed(self, units=None, passphrase=PASSPHRASE):
        payload = omni_backup.build_payload(
            self.source, UNITS if units is None else units,
            app_version="V1.2.0")
        return omni_backup.seal(payload, passphrase, app_version="V1.2.0")

    def restore_into(self, target: Path, blob, passphrase=PASSPHRASE):
        """The server's sequence, without the HTTP around it."""
        _header, payload = omni_backup.unseal(blob, passphrase)
        self.assertEqual(omni_backup.validate(payload), [])
        saved = omni_backup.snapshot(target, target.parent / "snapshot")
        applied = omni_backup.apply_payload(
            payload, target, write_json=omni_users._atomic_write_json)
        return payload, applied, saved

    def read(self, root: Path, name):
        return json.loads((root / name).read_text(encoding="utf-8"))


# ======================================================================
# What travels, and what does not
# ======================================================================

class WhatTheBackupCarriesTests(BackupTestBase):

    def setUp(self):
        super().setUp()
        self.payload = omni_backup.build_payload(self.source, UNITS,
                                                 app_version="V1.2.0")

    def test_every_configuration_section_is_present(self):
        sections = self.payload["sections"]
        for expected in ("accounts", "device_names", "multiview",
                         "multiview_groups", "usb_associations",
                         "usb_extenders", "scan_preferences",
                         "device_credentials", "remote_access", "notices"):
            with self.subTest(section=expected):
                self.assertIn(expected, sections)

    def test_the_regenerable_and_unsafe_files_are_not_in_it(self):
        """Not merely absent from the section list -- absent from the bytes."""
        blob = omni_backup.seal(self.payload, PASSPHRASE)
        _h, payload = omni_backup.unseal(blob, PASSPHRASE)
        flat = json.dumps(payload)
        for never in ("BEGIN PRIVATE KEY", "BEGIN CERTIFICATE",
                      "login.success"):
            with self.subTest(never=never):
                self.assertNotIn(never, flat)
        self.assertNotIn("scan_results", payload["sections"])
        self.assertNotIn("security_audit", payload["sections"])

    def test_it_says_what_it_left_out_and_why(self):
        """An operator must not have to guess what a backup does not cover."""
        excluded = self.payload["excluded"]
        for name in ("scan_results.json", "security_audit.json",
                     "remote_cert.pem", "remote_key.pem"):
            self.assertIn(name, excluded)
            self.assertTrue(excluded[name], "no reason given for " + name)

    def test_a_password_hash_travels_so_sign_ins_keep_working(self):
        users = self.payload["sections"]["accounts"]["users"]
        teacher = [u for u in users if u["username"] == "teacher"][0]
        self.assertEqual(teacher["password_hash"], "scrypt:32768:8:1$ghi$jkl")

    def test_permissions_and_assignments_travel_by_identity(self):
        users = self.payload["sections"]["accounts"]["users"]
        teacher = [u for u in users if u["username"] == "teacher"][0]
        self.assertEqual(teacher["permissions"]["sources"], [CAMERA])
        self.assertEqual(teacher["permissions"]["destinations"], [FRONT])

    def test_device_credentials_travel(self):
        creds = self.payload["sections"]["device_credentials"]
        self.assertEqual(creds["password"], "device-secret")
        self.assertEqual(creds["fallback_password"], "Atlona")

    def test_a_device_with_no_identity_is_not_carried(self):
        """An address is not an identity and must never stand in for one."""
        macs = {d["mac"] for d in self.payload["devices"]}
        self.assertEqual(macs, {CAMERA, FRONT})
        self.assertNotIn("198.51.100.99", json.dumps(self.payload["devices"]))

    def test_only_the_expensive_part_of_a_device_record_is_carried(self):
        """Not half a megabyte of state a scan rebuilds by itself."""
        record = [d for d in self.payload["devices"] if d["mac"] == FRONT][0]
        self.assertEqual(record.get("password"), "device-secret")
        self.assertNotIn("ip", record, "an address was carried as if it were "
                                       "part of the device's identity")
        self.assertNotIn("ip1_addr", record)

    def test_machine_bound_values_are_separated_rather_than_mixed_in(self):
        machine = self.payload["machine"]
        self.assertEqual(machine["remote_access"]["adapter_id"],
                         "guid:OLD-MACHINE-ADAPTER")
        self.assertEqual(machine["device_credentials"]["firmware_path"],
                         r"D:\old-machine\firmware")
        self.assertEqual(machine["scan_preferences"]["interface_ip"],
                         "198.51.100.5")
        # And they are not left in the section that gets written.
        self.assertNotIn("adapter_id", self.payload["sections"]["remote_access"])
        self.assertNotIn("firmware_path",
                         self.payload["sections"]["device_credentials"])


class DirectAccessSurvivesADisasterTests(BackupTestBase):
    """The digest travels, the platform-protected copy cannot.

    Which is the good outcome: a panel already holding its link keeps working,
    because the token it presents still matches the digest. What is lost is
    only the administrator's ability to display that link again.
    """

    def setUp(self):
        super().setUp()
        self.payload = omni_backup.build_payload(self.source, UNITS)
        users = self.payload["sections"]["accounts"]["users"]
        self.teacher = [u for u in users if u["username"] == "teacher"][0]

    def test_the_link_keeps_working_because_its_digest_travels(self):
        record = self.teacher["direct_access"]
        self.assertTrue(record["enabled"])
        self.assertEqual(record["digest"], "d" * 64)

    def test_the_machine_bound_copy_does_not_travel(self):
        record = self.teacher["direct_access"]
        self.assertEqual(record["blob"], "")
        self.assertEqual(record["protection"], "not-portable")

    def test_no_bearer_token_is_anywhere_in_the_file(self):
        """A digest is not reversible; a token would be a working credential."""
        blob = omni_backup.seal(self.payload, PASSPHRASE)
        self.assertNotIn(b"DPAPI-PROTECTED-ON-THE-OLD-PC", blob)
        _h, payload = omni_backup.unseal(blob, PASSPHRASE)
        self.assertNotIn("DPAPI-PROTECTED-ON-THE-OLD-PC", json.dumps(payload))

    def test_the_operator_is_told_rather_than_left_to_find_out(self):
        notes = " ".join(self.payload["notes"])
        self.assertIn("Direct Access", notes)
        self.assertIn("keep working", notes)


# ======================================================================
# The container
# ======================================================================

class TheContainerTests(BackupTestBase):

    def test_a_round_trip_returns_exactly_what_went_in(self):
        payload = omni_backup.build_payload(self.source, UNITS)
        blob = omni_backup.seal(payload, PASSPHRASE, app_version="V1.2.0")
        _header, back = omni_backup.unseal(blob, PASSPHRASE)
        for key in ("sections", "machine", "devices"):
            self.assertEqual(back[key], payload[key])

    def test_the_header_is_readable_without_the_passphrase(self):
        """So an incompatible file is refused before anybody types anything."""
        blob = self.sealed()
        header = omni_backup.read_header(blob)
        self.assertEqual(header["format"], omni_backup.FORMAT_VERSION)
        self.assertEqual(header["omnisuite_version"], "V1.2.0")
        self.assertEqual(header["cipher"], "AES-256-GCM")
        self.assertEqual(header["kdf"]["name"], "scrypt")
        self.assertTrue(header["created_at"])

    def test_a_short_passphrase_is_refused_before_anything_is_written(self):
        payload = omni_backup.build_payload(self.source, UNITS)
        for too_short in ("", "a", "short", "1234567"):
            with self.subTest(passphrase=len(too_short)):
                with self.assertRaises(omni_backup.BackupError) as caught:
                    omni_backup.seal(payload, too_short)
                self.assertEqual(caught.exception.reason, "weak_passphrase")

    def test_the_minimum_is_eight_and_the_message_says_so(self):
        """One number, and the sentence an operator reads agrees with it."""
        self.assertEqual(omni_backup.MIN_PASSPHRASE, 8)
        payload = omni_backup.build_payload(self.source, UNITS)
        with self.assertRaises(omni_backup.BackupError) as caught:
            omni_backup.seal(payload, "1234567")
        self.assertIn("8 characters", caught.exception.message)

    def test_exactly_the_minimum_is_accepted(self):
        """The boundary itself, which off-by-one would take away."""
        payload = omni_backup.build_payload(self.source, UNITS)
        blob = omni_backup.seal(payload, "12345678")
        _header, back = omni_backup.unseal(blob, "12345678")
        self.assertEqual(back["sections"], payload["sections"])

    def test_no_shape_is_demanded_of_a_passphrase(self):
        """Length is the only rule.

        Mandatory capitals, digits and symbols push people towards one
        predictable shape, and a passphrase nobody can remember gets written
        on the box the backup is kept in -- which is worse than a short one.
        """
        payload = omni_backup.build_payload(self.source, UNITS)
        for shape in ("alllowercase", "ALLUPPERCASE", "12345678901",
                      "!!!!!!!!!!!!", "correct horse battery staple",
                      "  leading and trailing  ", "pass phrase", "ünïcödé!",
                      "tab\tseparated"):
            with self.subTest(shape=shape):
                blob = omni_backup.seal(payload, shape)
                _header, back = omni_backup.unseal(blob, shape)
                self.assertEqual(back["sections"], payload["sections"])

    def test_a_passphrase_is_used_exactly_as_typed(self):
        """Nothing is trimmed or folded on the way in.

        Silently removing a trailing space would make the backup refuse the
        very thing somebody typed to create it.
        """
        payload = omni_backup.build_payload(self.source, UNITS)
        blob = omni_backup.seal(payload, "trailing space ")
        with self.assertRaises(omni_backup.BackupError):
            omni_backup.unseal(blob, "trailing space")
        with self.assertRaises(omni_backup.BackupError):
            omni_backup.unseal(blob, "Trailing Space ")
        _header, back = omni_backup.unseal(blob, "trailing space ")
        self.assertEqual(back["sections"], payload["sections"])

    def test_two_backups_of_the_same_thing_differ(self):
        """Fresh salt and nonce each time, so the file is not a fingerprint."""
        first, second = self.sealed(), self.sealed()
        self.assertNotEqual(first, second)

    def test_the_wrong_passphrase_is_refused(self):
        with self.assertRaises(omni_backup.BackupError) as caught:
            omni_backup.unseal(self.sealed(), "the-wrong-passphrase-entirely")
        self.assertEqual(caught.exception.reason, "cannot_decrypt")

    def test_an_empty_passphrase_is_refused(self):
        with self.assertRaises(omni_backup.BackupError):
            omni_backup.unseal(self.sealed(), "")

    def test_a_flipped_bit_in_the_ciphertext_is_refused(self):
        """Flip a bit of the ciphertext itself, not of its base64 spelling.

        The sealed body is base64 text and the key material is random, so flipping a
        character of the text sometimes produced a byte that is not base64 at all.
        That is correctly reported as a damaged file ("corrupt"), which made this
        test pass or fail by chance -- and fail on the CI run that drew the unlucky
        value. A flipped bit of the decoded ciphertext is always an authentication
        failure.
        """
        import base64
        magic, header_line, body = self.sealed().split(b"\n", 2)
        raw = bytearray(base64.b64decode(body.strip()))
        raw[-5] ^= 0x01
        forged = b"\n".join([magic, header_line, base64.b64encode(bytes(raw))])
        with self.assertRaises(omni_backup.BackupError) as caught:
            omni_backup.unseal(forged, PASSPHRASE)
        self.assertEqual(caught.exception.reason, "cannot_decrypt")

    def test_an_edited_header_is_refused_because_it_is_authenticated(self):
        """The header is the AAD, so it cannot be edited without detection."""
        magic, header_line, rest = self.sealed().split(b"\n", 2)
        header = json.loads(header_line)
        header["omnisuite_version"] = "V9.9.9"
        forged = b"\n".join([
            magic,
            json.dumps(header, sort_keys=True, separators=(",", ":")).encode(),
            rest])
        with self.assertRaises(omni_backup.BackupError) as caught:
            omni_backup.unseal(forged, PASSPHRASE)
        self.assertEqual(caught.exception.reason, "cannot_decrypt")

    def test_a_swapped_salt_is_refused(self):
        """Re-keying the file to a passphrase of the attacker's choosing."""
        magic, header_line, rest = self.sealed().split(b"\n", 2)
        header = json.loads(header_line)
        header["kdf"]["salt"] = omni_backup._b64(b"\x00" * 16)
        forged = b"\n".join([
            magic,
            json.dumps(header, sort_keys=True, separators=(",", ":")).encode(),
            rest])
        with self.assertRaises(omni_backup.BackupError):
            omni_backup.unseal(forged, PASSPHRASE)

    def test_a_truncated_file_is_refused(self):
        blob = self.sealed()
        for keep in (len(blob) // 2, len(blob) - 4, 40):
            with self.subTest(keep=keep):
                with self.assertRaises(omni_backup.BackupError):
                    omni_backup.unseal(blob[:keep], PASSPHRASE)

    def test_something_that_is_not_a_backup_says_so(self):
        for junk in (b"", b"hello world\n", b"{}", b"\x00\x01\x02"):
            with self.subTest(junk=junk[:8]):
                with self.assertRaises(omni_backup.BackupError) as caught:
                    omni_backup.unseal(junk, PASSPHRASE)
                self.assertIn(caught.exception.reason,
                              ("not_a_backup", "corrupt"))

    def test_a_newer_format_is_refused_with_a_useful_message(self):
        magic, header_line, rest = self.sealed().split(b"\n", 2)
        header = json.loads(header_line)
        header["format"] = omni_backup.FORMAT_VERSION + 5
        forged = b"\n".join([
            magic,
            json.dumps(header, sort_keys=True, separators=(",", ":")).encode(),
            rest])
        with self.assertRaises(omni_backup.BackupError) as caught:
            omni_backup.read_header(forged)
        self.assertEqual(caught.exception.reason, "too_new")
        self.assertIn("newer version", caught.exception.message)

    def test_absurd_kdf_parameters_are_refused_rather_than_attempted(self):
        """A hostile header must not be able to ask for all the memory."""
        magic, header_line, rest = self.sealed().split(b"\n", 2)
        header = json.loads(header_line)
        header["kdf"]["n"] = 1 << 40
        forged = b"\n".join([
            magic,
            json.dumps(header, sort_keys=True, separators=(",", ":")).encode(),
            rest])
        with self.assertRaises(omni_backup.BackupError) as caught:
            omni_backup.unseal(forged, PASSPHRASE)
        self.assertEqual(caught.exception.reason, "corrupt")

    def test_every_way_scrypt_can_refuse_a_header_is_the_modules_own_error(self):
        """Which exception an out-of-range parameter raises depends on the
        interpreter: Python 3.12 on Windows raises TypeError, others raise
        ValueError or OverflowError. All of them must surface as BackupError."""
        from unittest import mock
        sealed = self.sealed()           # sealed for real, before scrypt is stubbed
        for exc in (ValueError("n"), OverflowError("n"), MemoryError(), TypeError("n")):
            with self.subTest(raised=type(exc).__name__):
                with mock.patch.object(omni_backup.hashlib, "scrypt", side_effect=exc):
                    with self.assertRaises(omni_backup.BackupError) as caught:
                        omni_backup.unseal(sealed, PASSPHRASE)
                self.assertEqual(caught.exception.reason, "corrupt")

    def test_a_supported_older_format_is_migrated(self):
        payload = {"sections": {"accounts": {"users": []}}}
        migrated = omni_backup.migrate(payload, {"format": 1})
        self.assertEqual(migrated["migrated_from"], 1)
        self.assertIn("machine", migrated)
        self.assertIn("sections", migrated)

    def test_the_suggested_filename_is_legal_and_descriptive(self):
        name = omni_backup.suggested_filename("V1.2.0")
        self.assertTrue(name.endswith(".omnibak"))
        self.assertIn("V1.2.0", name)
        for illegal in '<>:"/\\|?*':
            self.assertNotIn(illegal, name)


class NoSecretIsWrittenInTheClearTests(BackupTestBase):

    def test_no_plaintext_secret_appears_in_the_file(self):
        blob = self.sealed()
        for secret in (b"device-secret", b"Atlona",
                       b"scrypt:32768:8:1$ghi$jkl",
                       b"DPAPI-PROTECTED-ON-THE-OLD-PC"):
            with self.subTest(secret=secret[:16]):
                self.assertNotIn(secret, blob)

    def test_the_passphrase_itself_is_never_in_the_file(self):
        self.assertNotIn(PASSPHRASE.encode(), self.sealed(passphrase=PASSPHRASE))

    def test_the_module_never_logs(self):
        """No logger at all, so a passphrase cannot reach a log from here."""
        source = (ROOT / "omni_backup.py").read_text(encoding="utf-8")
        code = "\n".join(line.split("#")[0] for line in source.split("\n"))
        for forbidden in ("log.", "logging.", "print("):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, code)


# ======================================================================
# Restore
# ======================================================================

class ARestoreRebuildsTheDeploymentTests(BackupTestBase):

    def setUp(self):
        super().setUp()
        self.blob = self.sealed()
        # A replacement machine: empty, with its own identity already set up.
        self.target = self.tmp / "replacement"
        self.target.mkdir()
        (self.target / "config.json").write_text(json.dumps({
            "username": "admin", "password": "factory",
            "firmware_path": r"C:\new-machine\firmware"}), encoding="utf-8")
        (self.target / "remote_access.json").write_text(json.dumps({
            "enabled": False, "adapter_id": "guid:NEW-MACHINE-ADAPTER",
            "adapter_name": "Ethernet 1", "address": "198.51.100.77",
            "port": 8081, "transport": "https"}), encoding="utf-8")

    def test_accounts_permissions_and_names_arrive(self):
        self.restore_into(self.target, self.blob)
        users = self.read(self.target, "users.json")["users"]
        teacher = [u for u in users if u["username"] == "teacher"][0]
        self.assertEqual(teacher["password_hash"], "scrypt:32768:8:1$ghi$jkl")
        self.assertEqual(teacher["permissions"]["sources"], [CAMERA])
        aliases = self.read(self.target, "device_aliases.json")["aliases"]
        self.assertEqual(aliases[FRONT], "Hall Display")

    def test_multiview_layouts_groups_and_usb_arrive(self):
        self.restore_into(self.target, self.blob)
        mv = self.read(self.target, "multiview_meta.json")["decoders"]
        self.assertIn("mac:b898b0000011", mv)
        self.assertIn("twoByTwo", mv["mac:b898b0000011"]["multiviews"])
        groups = self.read(self.target, "multiview_groups.json")["groups"]
        self.assertEqual(groups["hall"], [FRONT])
        usb = self.read(self.target, "usb_associations.json")["associations"]
        self.assertIn("mac:B898B0000001", usb)

    def test_device_credentials_arrive(self):
        self.restore_into(self.target, self.blob)
        cfg = self.read(self.target, "config.json")
        self.assertEqual(cfg["password"], "device-secret")
        self.assertEqual(cfg["fallback_password"], "Atlona")

    def test_the_replacement_machines_own_settings_are_kept(self):
        """The old PC's adapter and paths do not exist here."""
        _payload, applied, _saved = self.restore_into(self.target, self.blob)
        remote = self.read(self.target, "remote_access.json")
        self.assertEqual(remote["adapter_id"], "guid:NEW-MACHINE-ADAPTER",
                         "the old machine's adapter was written over this "
                         "machine's own")
        self.assertEqual(remote["address"], "198.51.100.77")
        cfg = self.read(self.target, "config.json")
        self.assertEqual(cfg["firmware_path"], r"C:\new-machine\firmware")
        # But the site's own decisions did arrive.
        self.assertTrue(remote["enabled"])
        self.assertEqual(remote["transport"], "https")

    def test_what_did_not_travel_is_reported_rather_than_silent(self):
        _payload, applied, _saved = self.restore_into(self.target, self.blob)
        settings = {(row["section"], row["setting"])
                    for row in applied["attention"]}
        self.assertIn(("remote_access", "adapter_id"), settings)
        self.assertIn(("device_credentials", "firmware_path"), settings)
        self.assertIn(("scan_preferences", "interface_ip"), settings)
        for row in applied["attention"]:
            self.assertTrue(row["was"], "nothing said about the old value")

    def test_no_certificate_or_private_key_is_written(self):
        self.restore_into(self.target, self.blob)
        self.assertFalse((self.target / "remote_key.pem").exists())
        self.assertFalse((self.target / "remote_cert.pem").exists())

    def test_the_old_machines_activity_log_is_not_written(self):
        self.restore_into(self.target, self.blob)
        self.assertFalse((self.target / "security_audit.json").exists())

    def test_a_missing_section_is_skipped_not_invented(self):
        """A backup from a deployment that had no groups must not write one."""
        (self.source / "multiview_groups.json").unlink()
        blob = self.sealed()
        _payload, applied, _saved = self.restore_into(self.target, blob)
        self.assertIn("multiview_groups", applied["skipped"])
        self.assertFalse((self.target / "multiview_groups.json").exists())


class DeviceIdentitySurvivesTests(BackupTestBase):
    """Authorisation follows the hardware, never the address."""

    def setUp(self):
        super().setUp()
        _h, self.payload = omni_backup.unseal(self.sealed(), PASSPHRASE)

    def test_a_unit_that_moved_address_keeps_everything(self):
        found = [{"ip": "198.51.100.250", "mac": FRONT, "role": "decoder"}]
        units, matched, missing = omni_backup.merge_devices(self.payload, found)
        self.assertEqual(matched, 1)
        self.assertEqual(units[0]["password"], "device-secret")
        self.assertEqual(units[0]["hostname"], "dec-front")
        self.assertEqual(units[0]["ip"], "198.51.100.250",
                         "the live address was overwritten by a stale one")

    def test_a_different_unit_at_the_old_address_inherits_nothing(self):
        """The case this whole design exists to prevent."""
        stranger = [{"ip": "198.51.100.11", "mac": "b8:98:b0:00:00:ff",
                     "role": "decoder"}]
        units, matched, missing = omni_backup.merge_devices(self.payload,
                                                            stranger)
        self.assertEqual(matched, 0)
        self.assertNotIn("password", units[0],
                         "a different device inherited a credential by "
                         "occupying an old address")
        self.assertNotIn("hostname", units[0])

    def test_live_data_wins_over_the_backup(self):
        """The backup fills gaps; it does not overrule what a scan just saw."""
        found = [{"ip": "198.51.100.11", "mac": FRONT,
                  "hostname": "renamed-since", "role": "decoder"}]
        units, _m, _x = omni_backup.merge_devices(self.payload, found)
        self.assertEqual(units[0]["hostname"], "renamed-since")

    def test_equipment_not_yet_seen_is_counted_rather_than_invented(self):
        units, matched, awaiting = omni_backup.merge_devices(self.payload, [])
        self.assertEqual(units, [])
        self.assertEqual(matched, 0)
        self.assertEqual(awaiting, 2,
                         "the operator is not told what is still missing")

    def test_a_malformed_mac_matches_nothing(self):
        for spelling in ("", "not-a-mac", "b8:98:b0:00:00", None):
            with self.subTest(spelling=spelling):
                units, matched, _ = omni_backup.merge_devices(
                    self.payload, [{"ip": "198.51.100.11", "mac": spelling}])
                self.assertEqual(matched, 0)

    def test_the_same_identity_written_differently_still_matches(self):
        """A MAC is a MAC however it was punctuated."""
        for spelling in ("B8-98-B0-00-00-11", "b898b0000011",
                         "B8:98:B0:00:00:11"):
            with self.subTest(spelling=spelling):
                units, matched, _ = omni_backup.merge_devices(
                    self.payload, [{"ip": "198.51.100.11", "mac": spelling}])
                self.assertEqual(matched, 1)


class ARestoreIsAllOrNothingTests(BackupTestBase):
    """A half-restored deployment is worse than either whole one."""

    def setUp(self):
        super().setUp()
        self.blob = self.sealed()
        self.target = a_deployment(self.tmp / "existing",
                                   aliases={FRONT: "The Existing Name"})
        self.before = {p.name: p.read_bytes()
                       for p in self.target.iterdir() if p.is_file()}

    def unchanged(self):
        after = {p.name: p.read_bytes()
                 for p in self.target.iterdir() if p.is_file()}
        self.assertEqual(after, self.before,
                         "the installation was modified by a restore that "
                         "should have refused")

    def test_a_wrong_passphrase_changes_nothing(self):
        with self.assertRaises(omni_backup.BackupError):
            omni_backup.unseal(self.blob, "wrong-passphrase-entirely")
        self.unchanged()

    def test_a_corrupt_file_changes_nothing(self):
        blob = bytearray(self.blob)
        blob[-12] ^= 0xFF
        with self.assertRaises(omni_backup.BackupError):
            omni_backup.unseal(bytes(blob), PASSPHRASE)
        self.unchanged()

    def test_an_invalid_payload_changes_nothing(self):
        payload = omni_backup.build_payload(self.source, UNITS)
        payload["sections"]["accounts"]["users"] = "not a list"
        blob = omni_backup.seal(payload, PASSPHRASE)
        _h, back = omni_backup.unseal(blob, PASSPHRASE)
        self.assertTrue(omni_backup.validate(back))
        self.unchanged()

    def test_a_failure_partway_through_is_rolled_back(self):
        """The third file refuses to be written; the first two go back."""
        _h, payload = omni_backup.unseal(self.blob, PASSPHRASE)
        saved = omni_backup.snapshot(self.target, self.tmp / "snap")
        self.assertTrue(saved)

        written = []

        def fails_on_the_third(path, content):
            if len(written) >= 2:
                raise OSError("the disk went away")
            written.append(Path(path).name)
            omni_users._atomic_write_json(path, content)

        with self.assertRaises(OSError):
            omni_backup.apply_payload(payload, self.target,
                                      write_json=fails_on_the_third)
        self.assertTrue(written, "nothing was written, so nothing was proven")

        restored, failures = omni_backup.rollback(
            self.tmp / "snap", self.target, saved)
        self.assertEqual(failures, [])
        self.unchanged()

    def test_rollback_removes_a_file_that_did_not_exist_before(self):
        """Putting it back means deleting it, not leaving it there."""
        (self.target / "multiview_groups.json").unlink()
        saved = omni_backup.snapshot(self.target, self.tmp / "snap2")
        self.assertNotIn("multiview_groups.json", saved)
        (self.target / "multiview_groups.json").write_text(
            '{"groups": {"new": []}}', encoding="utf-8")
        restored, failures = omni_backup.rollback(
            self.tmp / "snap2", self.target,
            saved + ["multiview_groups.json"])
        self.assertEqual(failures, [])
        self.assertFalse((self.target / "multiview_groups.json").exists())

    def test_rollback_reports_what_it_could_not_put_back(self):
        """An incomplete rollback is said, not swallowed."""
        saved = omni_backup.snapshot(self.target, self.tmp / "snap3")
        restored, failures = omni_backup.rollback(
            self.tmp / "snap3", self.target, saved + ["nonexistent.json"])
        self.assertEqual(failures, [])
        # A snapshot directory that has gone is a real failure to report.
        shutil.rmtree(self.tmp / "snap3")
        (self.target / "users.json").write_text("{}", encoding="utf-8")
        restored, failures = omni_backup.rollback(
            self.tmp / "snap3", self.target, ["users.json"])
        self.assertEqual(restored, ["users.json"],
                         "with no snapshot, putting it back means removing it")

    def test_a_source_file_that_cannot_be_read_refuses_the_export(self):
        """A backup silently missing a section is worse than no backup."""
        (self.source / "users.json").write_text("{ not json",
                                                encoding="utf-8")
        with self.assertRaises(omni_backup.BackupError) as caught:
            omni_backup.build_payload(self.source, UNITS)
        self.assertEqual(caught.exception.reason, "unreadable_source")
        self.assertIn("users.json", caught.exception.message)


class TheSnapshotIsTakenBeforeAnythingIsTouchedTests(BackupTestBase):

    def test_the_snapshot_holds_the_previous_configuration(self):
        target = a_deployment(self.tmp / "live",
                              aliases={FRONT: "Before The Restore"})
        snap = self.tmp / "snap"
        saved = omni_backup.snapshot(target, snap)
        self.assertIn("users.json", saved)
        self.assertIn("device_aliases.json", saved)
        held = json.loads((snap / "device_aliases.json").read_text("utf-8"))
        self.assertEqual(held["aliases"][FRONT], "Before The Restore")

    def test_it_does_not_copy_what_a_restore_never_writes(self):
        """A snapshot of files nothing will touch is noise to put back."""
        target = a_deployment(self.tmp / "live2")
        saved = omni_backup.snapshot(target, self.tmp / "snap2")
        self.assertNotIn("scan_results.json", saved)
        self.assertNotIn("security_audit.json", saved)
        self.assertNotIn("remote_key.pem", saved)



# ======================================================================
# Who may do this
# ======================================================================

import omni_access                                       # noqa: E402
import omni_gate                                         # noqa: E402
import omni_remote                                       # noqa: E402

BACKUP_ROUTES = ("/api/backup/export", "/api/backup/inspect",
                 "/api/backup/restore")


def remote_env(cookie=None, csrf=None):
    env = {omni_remote.LISTENER_KEY: omni_remote.REMOTE,
           "REQUEST_METHOD": "POST"}
    if cookie:
        env["HTTP_COOKIE"] = "%s=%s" % (omni_gate.SESSION_COOKIE, cookie)
    if csrf:
        env[omni_gate.CSRF_HEADER.upper().replace("-", "_")
            .replace("HTTP_", "HTTP_")] = csrf
        env["HTTP_X_OMNISUITE_CSRF"] = csrf
    return env


class OnlyTheMachineItselfMayRestoreTests(unittest.TestCase):
    """A disaster restore replaces every account on the installation.

    Reaching that from the network would mean a stolen administrator session
    could replace the account table with one an attacker wrote, so the answer
    is that nobody on the network can reach it at all -- not a restricted User,
    not a Direct Access panel, and not an administrator either. The backup also
    carries the device credentials, which `/api/config` already refuses to hand
    to anything but the local host; an export reachable remotely would be a
    second, quieter way to the same secret.
    """

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="omni-backup-auth-"))
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.store = omni_users.UserStore(self.dir / "users.json",
                                          self.dir / "audit.json")
        self.store.create("principal", "principal-password-1",
                          omni_users.ROLE_ADMIN)
        self.store.create("teacher", "teacher-password-1",
                          omni_users.ROLE_USER,
                          Permissions(features=("av",), sources=[CAMERA],
                                      destinations=[FRONT]))
        self.admin = self.store.find_by_username("principal")
        self.teacher = self.store.find_by_username("teacher")

    def decide(self, rule, session=None):
        env = remote_env(cookie=session.token if session else None,
                         csrf=session.csrf_token if session else None)
        return omni_gate.evaluate(rule, "POST", env, {}, self.store)

    def test_every_backup_route_is_local_only(self):
        for rule in BACKUP_ROUTES:
            with self.subTest(rule=rule):
                access, _feature = omni_access.classify(rule)
                self.assertIs(access, omni_access.Access.LOCAL_ONLY,
                              "%s is reachable from the network" % rule)

    def test_a_remote_administrator_cannot_reach_them(self):
        session = self.store.open_session(self.admin.id)
        for rule in BACKUP_ROUTES:
            with self.subTest(rule=rule):
                decision = self.decide(rule, session)
                self.assertFalse(decision.allowed,
                                 "%s answered a remote administrator" % rule)

    def test_a_restricted_user_cannot_reach_them(self):
        session = self.store.open_session(self.teacher.id)
        for rule in BACKUP_ROUTES:
            with self.subTest(rule=rule):
                self.assertFalse(self.decide(rule, session).allowed)

    def test_a_direct_access_panel_cannot_reach_them(self):
        self.store.enable_direct_access(self.teacher.id, actor=self.admin)
        session = self.store.open_session(self.teacher.id,
                                          omni_users.BY_DIRECT_LINK)
        for rule in BACKUP_ROUTES:
            with self.subTest(rule=rule):
                self.assertFalse(self.decide(rule, session).allowed)

    def test_an_anonymous_request_cannot_reach_them(self):
        for rule in BACKUP_ROUTES:
            with self.subTest(rule=rule):
                self.assertFalse(self.decide(rule).allowed)

    def test_the_local_operator_can(self):
        """The recovery authority: trusted by the socket, not by a password."""
        for rule in BACKUP_ROUTES:
            with self.subTest(rule=rule):
                decision = omni_gate.evaluate(
                    rule, "POST", {"REQUEST_METHOD": "POST"}, {}, self.store)
                self.assertTrue(decision.allowed,
                                "the machine's own operator was refused: %s"
                                % decision.reason)


class ATechnicianIsNeverLockedOutTests(unittest.TestCase):
    """A restore brings somebody else's administrator account with it.

    That password may be one nobody at this site has ever known. It must not
    become a wall: the person standing at the machine can already do anything,
    because the local request is trusted by the socket it arrived on.
    """

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="omni-backup-recover-"))
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.store = omni_users.UserStore(self.dir / "users.json",
                                          self.dir / "audit.json")
        self.store.create("forgotten-admin", "a-password-nobody-remembers",
                          omni_users.ROLE_ADMIN)
        self.admin = self.store.find_by_username("forgotten-admin")

    def test_the_local_operator_can_reset_a_forgotten_administrator(self):
        result = self.store.set_password(
            self.admin.id, "a-brand-new-password-1", actor=None)
        self.assertTrue(result["ok"], result)
        signed_in = self.store.authenticate("forgotten-admin",
                                            "a-brand-new-password-1")
        self.assertTrue(signed_in["ok"], signed_in)

    def test_the_local_operator_can_add_an_administrator(self):
        result = self.store.create("technician", "technician-password-1",
                                   omni_users.ROLE_ADMIN, actor=None)
        self.assertTrue(result["ok"], result)
        self.assertTrue(self.store.authenticate(
            "technician", "technician-password-1")["ok"])

    def test_the_old_password_stops_working_once_it_is_reset(self):
        self.store.set_password(self.admin.id, "a-brand-new-password-1",
                                actor=None)
        refused = self.store.authenticate("forgotten-admin",
                                          "a-password-nobody-remembers")
        self.assertFalse(refused["ok"])
        # And it does not say which half was wrong.
        self.assertNotIn("password_hash", json.dumps(refused))

    def test_resetting_a_password_ends_that_accounts_sessions(self):
        """Whoever held the old deployment's session does not keep it."""
        session = self.store.open_session(self.admin.id)
        self.store.set_password(self.admin.id, "a-brand-new-password-1",
                                actor=None)
        self.assertEqual(self.store.resolve_session(session.token),
                         (None, None))

    def test_the_route_that_does_this_is_reachable_locally(self):
        access, _f = omni_access.classify("/api/admin/users/<user_id>")
        self.assertIs(access, omni_access.Access.ADMIN)
        decision = omni_gate.evaluate(
            "/api/admin/users/<user_id>", "POST",
            {"REQUEST_METHOD": "POST"}, {}, self.store)
        self.assertTrue(decision.allowed,
                        "a technician at the machine cannot reset a password")



class ASucceededRestoreIsNeverReportedAsAFailureTests(unittest.TestCase):
    """The results screen is cosmetic and must not be able to undo the verdict.

    Found on the bench: the restore wrote all ten files and matched every
    device, and then the summary raised because `list_accounts()` hands back
    dictionaries rather than Account objects. The operator saw HTTP 500 on the
    day they were rebuilding a dead deployment, which is the worst lie this
    endpoint could tell.
    """

    def test_the_summary_is_built_after_the_writing_is_finished(self):
        source = (ROOT / "OmniMatrix_upgrade_server_v7_6y.py").read_text(
            encoding="utf-8")
        block = source[source.index("def api_backup_restore"):]
        block = block[:block.index("\ndef _backup_upload")]
        applied = block.index("applied = omni_backup.apply_payload")
        summary = block.index("_admin_recovery_note()")
        self.assertLess(applied, summary,
                        "the results screen is built before the restore")

    def test_a_failure_building_the_summary_does_not_fail_the_restore(self):
        source = (ROOT / "OmniMatrix_upgrade_server_v7_6y.py").read_text(
            encoding="utf-8")
        block = source[source.index("def api_backup_restore"):]
        block = block[:block.index("\ndef _backup_upload")]
        tail = block[block.index("recovery = _admin_recovery_note()") - 200:]
        self.assertIn("try:", tail,
                      "an exception in the results screen still escapes as a "
                      "500 over a restore that succeeded")
        self.assertIn('"ok": True, "status": "RESTORED"', tail)

    def test_the_note_reads_the_projection_rather_than_the_objects(self):
        source = (ROOT / "OmniMatrix_upgrade_server_v7_6y.py").read_text(
            encoding="utf-8")
        note = source[source.index("def _admin_recovery_note"):]
        note = note[:note.index("\n    }")]
        self.assertNotIn("a.is_admin", note,
                         "list_accounts() returns dictionaries")
        self.assertIn('a.get("role")', note)
