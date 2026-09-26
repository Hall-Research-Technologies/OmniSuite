"""A certificate is valid for the names it carries, and for no others.

The defect these exist for: a self-signed certificate was created while Remote
Access was on one adapter, the administrator then chose a different adapter, and
nothing compared the two. The listener started HTTPS on the new address,
Settings reported "ON -- securely", and the certificate row said, quite
correctly and quite uselessly, "For <the old address>".

Two rules are held here, and they are deliberately different:

  * a certificate OmniSuite generated is REFUSED rather than reissued. The
    fingerprint is the only thing an administrator can check when their browser
    warns, and silently changing it behind their back removes that. It is one
    button, and the refusal says which.

  * a certificate the site SUPPLIED is never touched and never blocks. It may
    carry a DNS name the site reaches OmniSuite by, which this code has no way
    of knowing about; refusing would break a correct deployment.

Every certificate here is generated into a temporary directory during the test.
None is read from the machine, and no address in this file exists.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("OMNI_SMOKE", "1")

import omni_tls                                          # noqa: E402

#: RFC 5737 documentation addresses. Two on one subnet, which is the shape the
#: defect was found in: an operator sees one network and Windows sees two
#: adapters.
FIRST = "198.51.100.85"
SECOND = "198.51.100.80"


class SubjectAlternativeNameTests(unittest.TestCase):
    """What a certificate says it is for, read the way a browser reads it."""

    @classmethod
    def setUpClass(cls):
        if not omni_tls.generation_available():
            raise unittest.SkipTest("this build cannot generate a certificate")

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="omni-cert-"))
        self.cert = self.dir / "cert.pem"
        self.key = self.dir / "key.pem"

    def generate(self, address):
        omni_tls.generate_self_signed(address, self.cert, self.key)
        return omni_tls.describe(self.cert)

    def test_a_generated_certificate_reports_the_address_in_its_sans(self):
        described = self.generate(FIRST)
        self.assertIn(FIRST, described["san"])

    def test_it_covers_the_address_it_was_made_for(self):
        self.assertTrue(omni_tls.covers(self.generate(FIRST), FIRST))

    def test_it_does_not_cover_another_address_on_the_same_subnet(self):
        """The reported defect, stated as a test."""
        self.assertFalse(omni_tls.covers(self.generate(FIRST), SECOND))

    def test_agreement_is_decided_from_the_sans_and_not_from_the_common_name(self):
        """A browser ignores the common name entirely, so this must too.

        Proved by taking a certificate whose common name says one address and
        asking about a different one: if the common name were consulted, a
        certificate naming FIRST would be reported as covering FIRST even with
        the SANs emptied, and it is not.
        """
        described = self.generate(FIRST)
        self.assertEqual(described["subject"], FIRST)
        without_sans = {**described, "san": []}
        self.assertFalse(omni_tls.covers(without_sans, FIRST),
                         "the common name must not be able to answer this")

    def test_a_certificate_whose_names_cannot_be_read_never_claims_a_match(self):
        """Fail closed: unknown is not the same as fine."""
        self.assertFalse(omni_tls.covers({"present": True}, FIRST))
        self.assertFalse(omni_tls.covers({}, FIRST))

    def test_no_address_at_all_matches_nothing(self):
        described = self.generate(FIRST)
        for address in ("", "   ", None):
            with self.subTest(address=address):
                self.assertFalse(omni_tls.covers(described, address))

    def test_an_address_is_compared_as_an_address(self):
        """One address written two ways is one address.

        String equality alone would report a compressed and an expanded IPv6
        form as different certificates for different machines.
        """
        described = {"san": ["2001:db8:0000:0000:0000:0000:0000:0001"]}
        self.assertTrue(omni_tls.covers(described, "2001:db8::1"))
        self.assertFalse(omni_tls.covers(described, "2001:db8::2"))

    def test_an_address_with_leading_zeros_is_not_quietly_accepted(self):
        """Refused rather than reinterpreted.

        `198.051.100.085` is read as octal by some resolvers and as decimal by
        others, which is why the standard library refuses it outright. A
        certificate check is the last place to start guessing which was meant.
        """
        self.assertFalse(omni_tls.covers({"san": [FIRST]}, "198.051.100.085"))

    def test_a_dns_name_is_matched_too(self):
        described = {"san": ["omnisuite.example.test"]}
        self.assertTrue(omni_tls.covers(described, "OmniSuite.Example.Test"))
        self.assertFalse(omni_tls.covers(described, "other.example.test"))

    def test_nothing_about_the_private_key_is_ever_described(self):
        described = self.generate(FIRST)
        flattened = repr(described).lower()
        for forbidden in ("private", "begin rsa", "key_path", "-----begin"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, flattened)


class WhoseCertificateItIsTests(unittest.TestCase):
    """OmniSuite may replace its own. It may never replace the site's."""

    def test_a_supplied_path_means_it_is_not_ours(self):
        self.assertFalse(omni_tls.is_generated({"present": True}, "a.pem",
                                               configured_cert_path="site.pem"))

    def test_no_supplied_path_means_it_is_ours(self):
        self.assertTrue(omni_tls.is_generated({"present": True}, "a.pem"))

    def test_a_certificate_that_is_not_there_is_not_ours_either(self):
        self.assertFalse(omni_tls.is_generated({"present": False}, "a.pem"))


class RefusalWordingTests(unittest.TestCase):
    """The refusal has to be actionable, and it has to name both addresses."""

    def test_the_server_refuses_https_on_an_address_its_certificate_misses(self):
        source = (ROOT / "omni_remote_api.py").read_text(encoding="utf-8")
        body = source[source.index("def api_remote_access_set"):]
        body = body[:body.index("\n    @app.route")]
        code = "\n".join(line.split("#")[0] for line in body.split("\n"))
        self.assertIn("tls.covers", code,
                      "enabling HTTPS must check the certificate covers the "
                      "address it is about to serve")
        self.assertIn("needs_certificate", code,
                      "the refusal has to tell the page which action fixes it")

    def test_a_supplied_certificate_is_never_regenerated(self):
        source = (ROOT / "omni_remote_api.py").read_text(encoding="utf-8")
        body = source[source.index("def api_remote_access_set"):]
        body = body[:body.index("\n    @app.route")]
        code = "\n".join(line.split("#")[0] for line in body.split("\n"))
        self.assertIn("supplied", code)
        self.assertNotIn("generate_self_signed", code,
                         "enabling remote access must never create a "
                         "certificate on its own")

    def test_the_restore_path_checks_the_same_thing(self):
        """A restart must not start HTTPS the browser will reject either."""
        source = (ROOT / "OmniMatrix_upgrade_server_v7_6y.py").read_text(
            encoding="utf-8")
        body = source[source.index("def restore_remote_access"):]
        body = body[:body.index("\n#: Observation for the restricted surfaces")]
        code = "\n".join(line.split("#")[0] for line in body.split("\n"))
        self.assertIn("omni_tls.covers", code)
        self.assertIn('saved.get("cert_path")', code,
                      "a supplied certificate must be exempt from this check")


if __name__ == "__main__":
    unittest.main()
