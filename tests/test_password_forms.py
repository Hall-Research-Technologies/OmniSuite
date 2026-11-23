"""Every password field the application serves is inside a form, and the forms are inert.

The browser warns "[DOM] Password field is not contained in a form" for each field that is
not. This is the same measurement as password_form_test.js, made on the pages as the server
actually serves them, so a page added later cannot reintroduce the warning unnoticed.
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UI = ROOT / "ui"


def loose_password_fields(markup):
    text = re.sub(r"<!--.*?-->", "", markup, flags=re.S)
    text = re.sub(r"<style\b.*?</style>", "", text, flags=re.S | re.I)
    depth, loose = 0, []
    for m in re.finditer(r"</?(form|input)\b[^>]*>", text, flags=re.I):
        whole, name = m.group(0), m.group(1).lower()
        if name == "form":
            depth += -1 if whole.startswith("</") else 1
        elif re.search(r"type\s*=\s*[\"']?password\b", whole, re.I) and depth <= 0:
            found = re.search(r"id\s*=\s*[\"']([^\"']+)", whole)
            loose.append(found.group(1) if found else whole[:40])
    return loose


class PasswordFieldsAreInForms(unittest.TestCase):
    def test_the_scanner_can_fail(self):
        self.assertEqual(loose_password_fields('<input id="x" type="password">'), ["x"])
        self.assertEqual(loose_password_fields('<form><input id="x" type="password"></form>'), [])
        self.assertEqual(loose_password_fields('<form></form><input id="y" type=password>'), ["y"])

    def test_every_served_file_with_a_password_field_keeps_it_in_a_form(self):
        measured = 0
        for path in sorted(list(UI.rglob("*.html")) + list(UI.rglob("*.js"))):
            source = path.read_text(encoding="utf-8")
            if not re.search(r"<input\b[^>]*type\s*=\s*[\"']?password", source, re.I):
                continue
            measured += 1
            self.assertEqual(loose_password_fields(source), [],
                             "%s has a password field outside a form" % path.relative_to(ROOT))
        self.assertGreaterEqual(measured, 5, "the scan found too few files to be measuring anything")

    def test_no_form_in_the_pages_can_navigate(self):
        for path in sorted(list(UI.rglob("*.html")) + list(UI.rglob("*.js"))):
            source = re.sub(r"<!--.*?-->", "", path.read_text(encoding="utf-8"), flags=re.S)
            for tag in re.findall(r"<form\b[^>]*>", source, flags=re.I):
                self.assertNotRegex(tag, r"\baction\s*=", "%s: %s" % (path.name, tag))
                self.assertNotRegex(tag, r"method\s*=\s*[\"']?get", "%s: %s" % (path.name, tag))


if __name__ == "__main__":
    unittest.main()
