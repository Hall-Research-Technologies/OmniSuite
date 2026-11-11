"""A generated certificate says who made it, who it is for, and for how long.

Identity: common name is the Windows machine name, organisation "Hall Research",
unit "OmniSuite". Names: the machine name as DNS and the selected adapter's
address as an IP. Validity: exactly three CALENDAR years from generation.

Every certificate is generated into a temporary directory and read back by
parsing the PEM it wrote -- not by asking the generator what it meant to write.
Addresses are RFC 5737 documentation addresses and machine names are invented.
"""
from __future__ import annotations

import datetime
import ipaddress
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("OMNI_SMOKE", "1")

import omni_tls                                          # noqa: E402

UTC = datetime.timezone.utc
ADDRESS = "198.51.100.40"


def at(year, month, day, hour=12, minute=30, second=15):
    return datetime.datetime(year, month, day, hour, minute, second, tzinfo=UTC)


class CalendarYearTests(unittest.TestCase):
    """Calendar arithmetic, with the leap-day rule stated and held."""

    def test_an_ordinary_date_lands_on_the_same_date_three_years_on(self):
        self.assertEqual(omni_tls.add_calendar_years(at(2026, 11, 6), 3),
                         at(2029, 11, 6))

    def test_the_time_of_day_is_kept(self):
        self.assertEqual(omni_tls.add_calendar_years(at(2026, 7, 1, 23, 59, 59), 3),
                         at(2029, 7, 1, 23, 59, 59))

    def test_month_boundaries(self):
        for start, expected in (
            (at(2026, 1, 31), at(2029, 1, 31)),
            (at(2026, 3, 1), at(2029, 3, 1)),
            (at(2026, 4, 30), at(2029, 4, 30)),
            (at(2026, 12, 31), at(2029, 12, 31)),
            (at(2026, 1, 1, 0, 0, 0), at(2029, 1, 1, 0, 0, 0)),
            (at(2026, 2, 28), at(2029, 2, 28)),
        ):
            with self.subTest(start=start):
                self.assertEqual(omni_tls.add_calendar_years(start, 3), expected)

    def test_it_is_not_a_day_count(self):
        """3 * 365 days is a day short across a leap day; this must not be."""
        start = at(2027, 6, 1)               # 2028-02-29 falls inside the period
        self.assertEqual(omni_tls.add_calendar_years(start, 3), at(2030, 6, 1))
        self.assertNotEqual(omni_tls.add_calendar_years(start, 3),
                            start + datetime.timedelta(days=3 * 365))

    def test_a_leap_day_rolls_back_to_the_28th_when_the_target_has_none(self):
        # The documented rule: never later than the anniversary period.
        self.assertEqual(omni_tls.add_calendar_years(at(2028, 2, 29), 3),
                         at(2031, 2, 28))
        self.assertEqual(omni_tls.add_calendar_years(at(2024, 2, 29), 3),
                         at(2027, 2, 28))

    def test_a_leap_day_stays_a_leap_day_when_the_target_has_one(self):
        self.assertEqual(omni_tls.add_calendar_years(at(2020, 2, 29), 4),
                         at(2024, 2, 29))

    def test_the_day_before_a_leap_day_is_not_disturbed(self):
        self.assertEqual(omni_tls.add_calendar_years(at(2025, 2, 28), 3),
                         at(2028, 2, 28))      # the 29th exists; the 28th is kept
        self.assertEqual(omni_tls.add_calendar_years(at(2025, 3, 1), 3),
                         at(2028, 3, 1))

    def test_the_period_is_three_years(self):
        self.assertEqual(omni_tls.GENERATED_VALIDITY_YEARS, 3)


class MachineNameTests(unittest.TestCase):
    """The common name is the OS's, made safe and bounded, never the caller's."""

    def test_an_ordinary_name_is_unchanged(self):
        self.assertEqual(omni_tls.sanitise_machine_name("CONTROL-ROOM-7"),
                         "CONTROL-ROOM-7")

    def test_characters_a_dns_name_cannot_carry_become_hyphens(self):
        self.assertEqual(omni_tls.sanitise_machine_name("Front Desk PC"),
                         "Front-Desk-PC")
        self.assertEqual(omni_tls.sanitise_machine_name("a/b\\c:d"), "a-b-c-d")

    def test_non_ascii_never_reaches_the_certificate(self):
        name = omni_tls.sanitise_machine_name("Büro-中文-PC")
        self.assertTrue(name.isascii(), name)
        self.assertRegex(name, r"^[A-Za-z0-9._-]+$")

    def test_it_is_bounded_to_the_x509_common_name_limit(self):
        self.assertLessEqual(len(omni_tls.sanitise_machine_name("h" * 300)),
                             omni_tls.MAX_COMMON_NAME)

    def test_dns_labels_are_bounded(self):
        name = omni_tls.sanitise_machine_name("x" * 100 + "." + "y" * 100)
        for label in name.split("."):
            self.assertLessEqual(len(label), omni_tls.MAX_DNS_LABEL)

    def test_the_limit_does_not_leave_a_trailing_separator(self):
        name = omni_tls.sanitise_machine_name("a" * 63 + "." + "b" * 10)
        self.assertLessEqual(len(name), omni_tls.MAX_COMMON_NAME)
        self.assertFalse(name.endswith((".", "-", "_")), name)

    def test_nothing_usable_falls_back_to_the_product_name(self):
        for raw in ("", "   ", None, "...", "---", "中文"):
            with self.subTest(raw=raw):
                self.assertEqual(omni_tls.sanitise_machine_name(raw),
                                 omni_tls.FALLBACK_MACHINE_NAME)

    def test_the_name_comes_from_the_operating_system(self):
        with mock.patch("omni_tls.socket.gethostname", return_value="Rack Nine"):
            self.assertEqual(omni_tls.machine_name(), "Rack-Nine")

    def test_a_failing_hostname_call_uses_the_environment_then_the_fallback(self):
        with mock.patch("omni_tls.socket.gethostname", side_effect=OSError("no")):
            with mock.patch.dict(os.environ, {"COMPUTERNAME": "ENVBOX"}):
                self.assertEqual(omni_tls.machine_name(), "ENVBOX")
            with mock.patch.dict(os.environ, {"COMPUTERNAME": ""}):
                self.assertEqual(omni_tls.machine_name(),
                                 omni_tls.FALLBACK_MACHINE_NAME)


class GeneratedCertificateTests(unittest.TestCase):
    """What is actually in the PEM."""

    @classmethod
    def setUpClass(cls):
        if not omni_tls.generation_available():
            raise unittest.SkipTest("this build cannot generate a certificate")
        from cryptography import x509
        cls.x509 = x509

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="omni-cert-id-"))
        self.cert = self.dir / "cert.pem"
        self.key = self.dir / "key.pem"
        patcher = mock.patch("omni_tls.socket.gethostname",
                             return_value="Studio-B")
        patcher.start()
        self.addCleanup(patcher.stop)

    def make(self, address=ADDRESS, now=None):
        omni_tls.generate_self_signed(address, self.cert, self.key, now=now)
        return self.x509.load_pem_x509_certificate(self.cert.read_bytes())

    def attribute(self, certificate, oid_name):
        from cryptography.x509.oid import NameOID
        values = certificate.subject.get_attributes_for_oid(
            getattr(NameOID, oid_name))
        self.assertEqual(len(values), 1, oid_name)
        return values[0].value

    # -- identity
    def test_subject_is_machine_name_hall_research_omnisuite(self):
        certificate = self.make()
        self.assertEqual(self.attribute(certificate, "COMMON_NAME"), "Studio-B")
        self.assertEqual(self.attribute(certificate, "ORGANIZATION_NAME"),
                         "Hall Research")
        self.assertEqual(self.attribute(certificate, "ORGANIZATIONAL_UNIT_NAME"),
                         "OmniSuite")

    def test_self_signed_issuer_equals_subject(self):
        certificate = self.make()
        self.assertEqual(certificate.issuer, certificate.subject)

    def test_the_address_is_not_the_common_name(self):
        certificate = self.make()
        self.assertNotEqual(self.attribute(certificate, "COMMON_NAME"), ADDRESS)

    # -- names
    def sans(self, certificate):
        extension = certificate.extensions.get_extension_for_class(
            self.x509.SubjectAlternativeName).value
        return (extension.get_values_for_type(self.x509.DNSName),
                [str(a) for a in extension.get_values_for_type(self.x509.IPAddress)])

    def test_san_has_the_machine_name_and_the_adapter_address(self):
        dns, ips = self.sans(self.make())
        self.assertEqual(dns, ["Studio-B"])
        self.assertEqual(ips, [ADDRESS])

    def test_an_address_is_never_a_dns_name(self):
        dns, _ips = self.sans(self.make())
        self.assertNotIn(ADDRESS, dns)

    def test_an_ipv6_adapter_address_is_an_ip_san(self):
        _dns, ips = self.sans(self.make("2001:db8::17"))
        self.assertEqual(ips, ["2001:db8::17"])

    def test_a_name_given_instead_of_an_address_is_kept_as_a_name(self):
        dns, ips = self.sans(self.make("other.example.test"))
        self.assertEqual(sorted(dns), ["Studio-B", "other.example.test"])
        self.assertEqual(ips, [])

    def test_the_certificate_covers_the_address_and_the_name(self):
        self.make()
        described = omni_tls.describe(self.cert)
        self.assertTrue(omni_tls.covers(described, ADDRESS))
        self.assertTrue(omni_tls.covers(described, "Studio-B"))
        self.assertFalse(omni_tls.covers(described, "198.51.100.41"))

    def test_describe_reports_identity_and_addresses(self):
        self.make()
        described = omni_tls.describe(self.cert)
        self.assertEqual(described["subject"], "Studio-B")
        self.assertEqual(described["organization"], "Hall Research")
        self.assertEqual(described["organizational_unit"], "OmniSuite")
        self.assertEqual(described["addresses"], [ADDRESS])
        self.assertTrue(described["self_signed"])

    def test_sentences_say_for_the_address_not_for_the_machine_name(self):
        self.make()
        self.assertEqual(omni_tls.label_for(omni_tls.describe(self.cert)), ADDRESS)

    def test_a_certificate_with_no_addresses_is_labelled_by_its_subject(self):
        self.assertEqual(omni_tls.label_for({"subject": "legacy"}), "legacy")
        self.assertEqual(omni_tls.label_for({}), "")

    def test_the_key_is_never_in_what_is_described(self):
        self.make()
        self.assertNotIn("PRIVATE", repr(omni_tls.describe(self.cert)))

    # -- validity, read from the PEM
    def parsed(self, certificate):
        return (certificate.not_valid_before_utc, certificate.not_valid_after_utc)

    def test_validity_is_three_calendar_years_read_back_from_the_pem(self):
        generated = at(2026, 11, 6, 9, 5, 3)
        before, after = self.parsed(self.make(now=generated))
        self.assertEqual(before, generated)
        self.assertEqual(after, at(2029, 11, 6, 9, 5, 3))

    def test_validity_across_month_boundaries(self):
        for generated in (at(2026, 1, 31), at(2026, 12, 31, 23, 59, 59),
                          at(2026, 3, 1, 0, 0, 0), at(2026, 6, 30)):
            with self.subTest(generated=generated):
                before, after = self.parsed(self.make(now=generated))
                self.assertEqual(before, generated)
                self.assertEqual(after, generated.replace(year=generated.year + 3))

    def test_validity_from_a_leap_day(self):
        before, after = self.parsed(self.make(now=at(2028, 2, 29)))
        self.assertEqual(before, at(2028, 2, 29))
        self.assertEqual(after, at(2031, 2, 28))

    def test_validity_across_a_leap_day_is_a_day_longer_than_365_times_three(self):
        before, after = self.parsed(self.make(now=at(2027, 6, 1)))
        self.assertEqual((after - before).days, 3 * 365 + 1)

    def test_the_unclocked_default_is_now_and_three_years(self):
        lo = datetime.datetime.now(UTC).replace(microsecond=0)
        before, after = self.parsed(self.make())
        hi = datetime.datetime.now(UTC)
        self.assertTrue(lo <= before <= hi, (lo, before, hi))
        self.assertEqual(after, omni_tls.add_calendar_years(before, 3))

    def test_the_certificate_is_not_a_ca(self):
        bc = self.make().extensions.get_extension_for_class(
            self.x509.BasicConstraints).value
        self.assertFalse(bc.ca)

    # -- expiry and remaining lifetime
    def test_remaining_lifetime_is_measured_to_the_parsed_not_after(self):
        generated = at(2026, 11, 6)
        self.make(now=generated)
        expected = (at(2029, 11, 6) - generated).days          # no leap day inside
        described = omni_tls.describe(self.cert, now=generated)
        self.assertEqual(described["days_remaining"], expected)
        self.assertFalse(described["expired"])

    def test_remaining_lifetime_counts_down(self):
        self.make(now=at(2026, 11, 6))
        a = omni_tls.describe(self.cert, now=at(2027, 11, 6))["days_remaining"]
        b = omni_tls.describe(self.cert, now=at(2028, 11, 6))["days_remaining"]
        self.assertEqual(a - b, 366)           # 2028-02-29 lies between

    def test_the_last_day_and_the_day_after(self):
        self.make(now=at(2026, 11, 6))
        last = omni_tls.describe(self.cert, now=at(2029, 11, 5, 12, 30, 15))
        self.assertEqual(last["days_remaining"], 1)
        self.assertFalse(last["expired"])
        over = omni_tls.describe(self.cert, now=at(2029, 11, 7))
        self.assertTrue(over["expired"])
        self.assertLess(over["days_remaining"], 0)

    def test_a_certificate_generated_in_the_past_reads_as_expired_today(self):
        self.make(now=datetime.datetime.now(UTC) - datetime.timedelta(days=4 * 366))
        self.assertTrue(omni_tls.describe(self.cert)["expired"])

    def test_the_old_825_day_period_is_gone(self):
        self.assertFalse(hasattr(omni_tls, "GENERATED_VALIDITY_DAYS"))


class MigrationTests(unittest.TestCase):
    """A certificate that already exists keeps working and is never rewritten."""

    @classmethod
    def setUpClass(cls):
        if not omni_tls.generation_available():
            raise unittest.SkipTest("this build cannot generate a certificate")

    def legacy(self, directory):
        """A certificate in the shape earlier builds created."""
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        subject = x509.Name([
            x509.NameAttribute(NameOID.ORGANIZATION_NAME,
                               "Hall Research Technologies LLC"),
            x509.NameAttribute(NameOID.COMMON_NAME, ADDRESS)])
        now = datetime.datetime.now(UTC)
        certificate = (
            x509.CertificateBuilder().subject_name(subject)
            .issuer_name(subject).public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(minutes=5))
            .not_valid_after(now + datetime.timedelta(days=825))
            .add_extension(x509.SubjectAlternativeName(
                [x509.IPAddress(ipaddress.ip_address(ADDRESS))]), critical=False)
            .sign(key, hashes.SHA256()))
        cert = Path(directory) / "c.pem"
        pkey = Path(directory) / "k.pem"
        cert.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
        pkey.write_bytes(key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption()))
        return cert, pkey

    def test_an_existing_certificate_still_loads_describes_and_covers(self):
        with tempfile.TemporaryDirectory() as d:
            cert, key = self.legacy(d)
            before = cert.read_bytes()
            omni_tls.load_context(cert, key)
            described = omni_tls.describe(cert)
            self.assertEqual(described["subject"], ADDRESS)
            self.assertTrue(omni_tls.covers(described, ADDRESS))
            self.assertFalse(described["expired"])
            self.assertEqual(omni_tls.label_for(described), ADDRESS)
            self.assertEqual(cert.read_bytes(), before,
                             "reading a certificate must never rewrite it")

    def test_describing_never_generates(self):
        with tempfile.TemporaryDirectory() as d:
            cert, key = self.legacy(d)
            with mock.patch.object(omni_tls, "generate_self_signed",
                                   side_effect=AssertionError("regenerated")):
                omni_tls.describe(cert)
                omni_tls.load_context(cert, key)
                omni_tls.covers(omni_tls.describe(cert), ADDRESS)

    def test_generation_is_reachable_from_exactly_one_explicit_action(self):
        """No startup, state read or listener path may create a certificate."""
        callers = []
        for path in sorted(ROOT.glob("*.py")):
            if path.name == "omni_tls.py":
                continue
            text = path.read_text(encoding="utf-8")
            for number, line in enumerate(text.splitlines(), 1):
                if re.search(r"generate_self_signed\s*\(", line) \
                        and not line.lstrip().startswith("#"):
                    callers.append(f"{path.name}:{number}")
        self.assertEqual(len(callers), 1, callers)
        self.assertTrue(callers[0].startswith("omni_remote_api.py"), callers)
        api = (ROOT / "omni_remote_api.py").read_text(encoding="utf-8")
        route = api.split('@app.route("/api/admin/certificate"')[1]
        self.assertLess(route.index('if action == "generate":'),
                        route.index("tls.generate_self_signed("))


if __name__ == "__main__":
    unittest.main()
