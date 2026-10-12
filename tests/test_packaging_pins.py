"""Dependency pins that exist because a platform's package build depends on them."""
import re
import unittest
from pathlib import Path

REQUIREMENTS = Path(__file__).resolve().parent.parent / "requirements.txt"


def _lines():
    text = REQUIREMENTS.read_text(encoding="utf-8")
    return [ln.strip() for ln in text.splitlines()
            if ln.strip() and not ln.strip().startswith("#")]


class CryptographyPinTests(unittest.TestCase):
    """cryptography 49+ ships no Intel macOS wheel, so pip builds it from source
    against a different OpenSSL than the one the packaged app bundles, and the
    app fails on `import cryptography`. Only that platform is held back."""

    def test_intel_macos_is_held_below_49_and_nothing_else_is(self):
        lines = _lines()
        self.assertIn("cryptography", lines, "the unconditional requirement must remain")
        pins = [ln for ln in lines if ln.startswith("cryptography") and ln != "cryptography"]
        self.assertEqual(len(pins), 1, pins)
        spec, marker = [part.strip() for part in pins[0].split(";", 1)]
        self.assertEqual(spec, "cryptography<49")
        self.assertIn('sys_platform == "darwin"', marker)
        self.assertIn('platform_machine == "x86_64"', marker)
        self.assertFalse(re.search(r"\bwin32\b|\blinux\b|arm64", marker),
                         "the cap must not reach other platforms: " + marker)


if __name__ == "__main__":
    unittest.main()
