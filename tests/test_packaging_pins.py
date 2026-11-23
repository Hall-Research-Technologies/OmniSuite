"""Dependency pins that exist because a platform's package build depends on them."""
import unittest
from pathlib import Path

REQUIREMENTS = Path(__file__).resolve().parent.parent / "requirements.txt"


def _lines():
    text = REQUIREMENTS.read_text(encoding="utf-8")
    return [ln.strip() for ln in text.splitlines()
            if ln.strip() and not ln.strip().startswith("#")]


class CryptographyPinTests(unittest.TestCase):
    """Supported platforms are Windows x86-64 and macOS Apple Silicon, both of which have a
    prebuilt cryptography 50 wheel, so there is one unconditional requirement and no platform split."""

    def test_cryptography_is_one_unconditional_requirement(self):
        lines = _lines()
        self.assertEqual([ln for ln in lines if ln.startswith("cryptography")], ["cryptography"])


if __name__ == "__main__":
    unittest.main()
