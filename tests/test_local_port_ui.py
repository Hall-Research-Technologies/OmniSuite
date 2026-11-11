"""Runs the local-port and certificate-wording front-end checks under unittest,
so the mutation harness can see them, and pins the browser's requests to the
server's route table so the two cannot drift apart.

    local_port_ui_test.js          the Settings control and its flows
    local_port_confirm_test.js     the shared dialog's value, hint and validation
    certificate_state_ui_test.js   what Settings says about the certificate

Each is executed rather than read, and a zero status is not a pass on its own:
Node exits 0 when a scenario never settles, so the summary line has to be there.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("OMNI_SMOKE", "1")


def node(script):
    return subprocess.run(["node", str(ROOT / "tests" / script)], cwd=str(ROOT),
                          capture_output=True, encoding="utf-8", timeout=180)


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class LocalPortFrontend(unittest.TestCase):
    def passed(self, script):
        done = node(script)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        match = re.search(r"(\d+)/(\d+) checks passed", done.stdout)
        self.assertIsNotNone(match, "no summary line: " + done.stdout + done.stderr)
        self.assertEqual(match.group(1), match.group(2), done.stdout)
        self.assertGreater(int(match.group(2)), 0)

    def test_the_local_port_control(self):
        self.passed("local_port_ui_test.js")

    def test_the_shared_dialog_additions(self):
        self.passed("local_port_confirm_test.js")

    def test_the_certificate_wording(self):
        self.passed("certificate_state_ui_test.js")


class RequestContractTests(unittest.TestCase):
    """The page's endpoints are the server's endpoints."""

    def setUp(self):
        import OmniMatrix_upgrade_server_v7_6y as srv
        self.srv = srv
        self.source = (ROOT / "ui" / "local-port.js").read_text(encoding="utf-8")

    def test_every_path_the_page_requests_is_a_route_with_that_method(self):
        rules = {}
        for rule in self.srv.app.url_map.iter_rules():
            rules.setdefault(rule.rule, set()).update(rule.methods)
        used = set(re.findall(r"'(/api/local_port[a-z_/]*)'", self.source))
        self.assertTrue(used, "the page requests nothing")
        for path in used:
            with self.subTest(path=path):
                self.assertIn(path, rules, "the page asks for a route that does not exist")
        self.assertIn("GET", rules["/api/local_port"])
        for post in ("/api/local_port/fixed", "/api/local_port/disable",
                     "/api/local_port/retry", "/api/local_port/make_fixed"):
            self.assertIn("POST", rules[post])
            self.assertIn(post, used, "the page never uses " + post)

    def test_the_server_serves_the_module_settings_loads(self):
        client = self.srv.app.test_client()
        response = client.get("/ui/local-port.js")
        self.assertEqual(response.status_code, 200)
        settings = (ROOT / "ui" / "settings.js").read_text(encoding="utf-8")
        self.assertIn("/ui/local-port.js", settings)

    def test_the_settings_page_markup_is_served_to_every_page_with_settings(self):
        # one implementation, injected by settings.js: no page carries a copy
        for page in (ROOT / "ui").rglob("*.html"):
            text = page.read_text(encoding="utf-8")
            self.assertNotIn("cfg_localport_", text, page.name)


if __name__ == "__main__":
    unittest.main()
