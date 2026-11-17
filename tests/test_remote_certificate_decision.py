"""HTTPS without a usable certificate is refused with a reason the page can act on.

The page offers a decision -- Create Certificate, Change to HTTP, Cancel -- and
the server is the enforcement behind it. These pin the server half:

  * nothing starts: no HTTPS listener without a certificate, and no listener at
    all in the clear because the certificate was missing;
  * nothing is made for the operator: no certificate is created by the refusal
    and the saved transport is not rewritten to HTTP;
  * the refusal says which problem it is (`reason`), and only OmniSuite's own
    certificate is offered a replacement -- a supplied one is reported as itself
    and its files are never touched.

The listener's start() is replaced with a recorder, as in
test_remote_certificate_choice, so nothing binds a port.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import omni_tls                                          # noqa: E402
from tests import test_remote_certificate_choice as base  # noqa: E402

ADDRESS = base.ADDRESS
ADAPTER = base.ADAPTER


class CertificateDecisionServerTests(unittest.TestCase):
    # The scaffolding is borrowed by reference, not subclassed: a subclass
    # would run every test of the class it borrows from a second time.
    setUpClass = classmethod(
        base.SuppliedCertificateSurvivesEnableTests.setUpClass.__func__)
    setUp = base.SuppliedCertificateSurvivesEnableTests.setUp
    saved = base.SuppliedCertificateSurvivesEnableTests.saved
    use_supplied = base.SuppliedCertificateSurvivesEnableTests.use_supplied
    enable = base.SuppliedCertificateSurvivesEnableTests.enable

    def generate_via_endpoint(self):
        """POST the real generate action without touching the shared data dir.

        The endpoint writes to the paths it captured at registration, which sit
        in the process-wide test data directory; a certificate left there makes
        every later test that expects "no certificate" see one. The generator is
        redirected to this test's own directory for the span of the call.
        """
        real = omni_tls.generate_self_signed
        seen = []

        def redirected(address, cert_path, key_path, **kwargs):
            seen.append((address, str(cert_path), str(key_path)))
            return real(address, *self.generated, **kwargs)

        with mock.patch.object(omni_tls, "generate_self_signed", redirected):
            response = self.client.post(
                "/api/admin/certificate",
                json={"action": "generate", "address": ADDRESS})
        self.assertEqual(len(seen), 1, "the endpoint did not generate")
        return response

    def remove_generated(self):
        for path in self.generated:
            if path.exists():
                path.unlink()

    def body(self, response):
        return response.get_json()

    def test_no_certificate_at_all_is_refused_with_its_reason(self):
        self.remove_generated()
        response = self.enable()
        self.assertEqual(response.status_code, 400)
        body = self.body(response)
        self.assertEqual(body["reason"], "no_certificate")
        self.assertIs(body["needs_certificate"], True)
        self.assertFalse(body["ok"])

    def test_the_refusal_starts_nothing_and_creates_nothing(self):
        self.remove_generated()
        self.enable()
        self.assertEqual(self.contexts, [], "a listener started without a certificate")
        for path in self.generated:
            self.assertFalse(path.exists(), "the refusal silently created a certificate")

    def test_the_refusal_does_not_fall_back_to_http(self):
        self.remove_generated()
        before = dict(self.saved())
        response = self.enable()
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.contexts, [], "plaintext was started instead")
        self.assertEqual(self.saved().get("transport"), before.get("transport"),
                         "the refusal rewrote the saved transport")

    def test_an_unreadable_own_certificate_is_a_different_reason(self):
        self.generated[0].write_text("not a certificate")
        response = self.enable()
        self.assertEqual(response.status_code, 400)
        body = self.body(response)
        self.assertEqual(body["reason"], "certificate_unusable")
        self.assertIs(body["needs_certificate"], True)
        self.assertEqual(self.contexts, [])

    def test_a_supplied_certificate_is_never_offered_a_replacement(self):
        self.use_supplied()
        before = [p.read_bytes() for p in self.supplied]
        self.supplied[0].write_text("damaged")
        damaged = self.supplied[0].read_bytes()
        response = self.enable()
        self.assertEqual(response.status_code, 400)
        body = self.body(response)
        self.assertEqual(body["reason"], "supplied_certificate_unusable")
        self.assertIs(body["needs_certificate"], False,
                      "a site's own certificate must not be offered a replacement")
        self.assertEqual(self.supplied[0].read_bytes(), damaged)
        self.assertEqual(self.supplied[1].read_bytes(), before[1])
        self.assertEqual(self.contexts, [])

    def test_http_is_still_a_separate_explicit_acknowledgement(self):
        self.remove_generated()
        response = self.enable(transport="http")
        self.assertEqual(response.status_code, 400)
        self.assertIs(self.body(response)["needs_confirmation"], True)
        self.assertEqual(self.contexts, [])
        response = self.enable(transport="http", accept_insecure=True)
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertEqual(self.contexts, [None], "HTTP carries no TLS context")

    def test_a_truthy_field_of_another_name_is_not_the_acknowledgement(self):
        self.remove_generated()
        response = self.enable(transport="http", accept_insecure="yes",
                               acceptInsecure=True, confirmed=True)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.contexts, [])

    def test_creating_the_certificate_then_enabling_succeeds_for_the_adapter_address(self):
        self.remove_generated()
        self.assertEqual(self.enable().status_code, 400)
        made = self.generate_via_endpoint()
        self.assertEqual(made.status_code, 200, made.get_data(as_text=True))
        response = self.enable()
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertEqual(len(self.contexts), 1)
        described = self.body(response)["certificate"]
        self.assertTrue(described["present"])
        self.assertIn(ADDRESS, described.get("addresses") or described.get("san") or [])

    def test_creating_a_certificate_leaves_a_supplied_pair_on_disk_untouched(self):
        self.use_supplied()
        before = [p.read_bytes() for p in self.supplied]
        made = self.generate_via_endpoint()
        self.assertEqual(made.status_code, 200, made.get_data(as_text=True))
        self.assertEqual([p.read_bytes() for p in self.supplied], before)

    def test_a_certificate_for_another_address_keeps_its_own_reason(self):
        other = "198.51.100.77"
        omni_tls.generate_self_signed(other, *self.generated)
        response = self.enable()
        self.assertEqual(response.status_code, 400)
        body = self.body(response)
        self.assertEqual(body["reason"], "certificate_mismatch")
        self.assertIs(body["needs_certificate"], True)
        self.assertEqual(self.contexts, [])


if __name__ == "__main__":
    unittest.main()
