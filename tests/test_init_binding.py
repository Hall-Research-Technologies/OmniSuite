"""The Device Info controls that do not need loaded data are bound before the data loads.

Found on hardware: after a server start the first page load waits about ten seconds for the adapter
scan, and Set NTP and Sync all Passwords did nothing for that long because their handlers were
attached after the awaited loads. The assertion reads the init block of the page with comments
stripped and bounded at the end of that block.
"""
import re
import unittest
from pathlib import Path

PAGE = Path(__file__).resolve().parent.parent / "ui" / "index.html"


def _init_block():
    text = PAGE.read_text(encoding="utf-8")
    start = text.index("// ===== Initialize =====")
    block = text[start:]
    block = re.sub(r"/\*.*?\*/", "", block, flags=re.S)
    block = re.sub(r"(?m)^\s*//.*$", "", block)
    end = block.index("initPolling();")          # the first statement after the bound controls
    return block[:end]


class InitBindingTests(unittest.TestCase):
    def test_independent_controls_are_bound_before_the_awaited_loads(self):
        block = _init_block()
        loads = block.index("await Promise.all")
        for name in ("initPasswordSync();", "initSetNtp();", "initInventoryExtraToggle();"):
            self.assertIn(name, block, name + " is no longer called in the init block")
            self.assertLess(block.index(name), loads, name + " is bound only after the data loads finish")

    def test_each_is_bound_exactly_once(self):
        text = PAGE.read_text(encoding="utf-8")
        text = re.sub(r"(?m)^\s*//.*$", "", text)
        for name in ("initPasswordSync();", "initSetNtp();", "initInventoryExtraToggle();"):
            self.assertEqual(text.count("  " + name), 1, name)


if __name__ == "__main__":
    unittest.main()
