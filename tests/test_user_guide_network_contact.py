"""The User Guide's account of what OmniSuite contacts on the Internet is what the code does.

The guide says what the two optional checks contact, when, and what they send. Each
claim is held to the constant or call that makes it true, so a change to the code
fails here until the guide is changed with it.
"""
from __future__ import annotations

import os
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("OMNI_SMOKE", "1")

import omni_firmware                                       # noqa: E402
import OmniMatrix_upgrade_server_v7_6y as srv              # noqa: E402

GUIDE = (ROOT / "ui" / "user-guide.html").read_text(encoding="utf-8")
SERVER = (ROOT / "OmniMatrix_upgrade_server_v7_6y.py").read_text(encoding="utf-8")


def section():
    start = GUIDE.index('<h3 id="internet-contact">')
    end = GUIDE.index("<h3", start + 10)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", GUIDE[start:end]))


class InternetContactSectionTests(unittest.TestCase):

    def test_the_startup_release_check_is_disclosed_in_the_agreed_words(self):
        text = section()
        self.assertIn("OmniSuite may contact GitHub at startup to check whether a newer "
                      "OmniSuite version is available.", text)
        self.assertIn("No update is downloaded or installed automatically.", text)

    def test_the_hosts_named_are_the_hosts_the_code_uses(self):
        self.assertIn("api.github.com", srv.GITHUB_RELEASES_API)
        self.assertIn("api.github.com", section())
        self.assertEqual(omni_firmware.API_HOST, "hallresearch.com")
        self.assertIn("hallresearch.com", section())

    def test_the_cadences_stated_are_the_cadences_in_the_code(self):
        self.assertEqual(srv.UPDATE_CHECK_TTL, 6 * 3600)
        self.assertIn("more than six hours old", section())
        self.assertEqual(omni_firmware.CHECK_INTERVAL_SECONDS, 7 * 24 * 3600)
        self.assertIn("every seven days", section())
        self.assertEqual(omni_firmware.RETRY_AFTER_FAILURE_SECONDS, 3600)
        self.assertIn("again after an hour", section())

    def test_the_release_check_starts_at_launch_and_nothing_schedules_another(self):
        start = SERVER.index("def start_background_startup_tasks")
        body = SERVER[start:SERVER.index("_startup_tasks_started = False", start)]
        self.assertIn("_update_check()", body)
        # No timer re-arms the release check anywhere: it is called at startup and
        # from the endpoint, and from nowhere else.
        calls = re.findall(r"(?<![\w.])_update_check\(", SERVER)
        self.assertEqual(len(calls), 3, "a new caller of the release check needs the guide changed")

    def test_the_off_switch_is_the_firmware_one_and_the_guide_says_what_it_covers(self):
        self.assertIn('os.environ.get("OMNI_FIRMWARE_CHECK") != "0"', SERVER)
        text = section()
        self.assertIn("OMNI_FIRMWARE_CHECK", text)
        self.assertIn("firmware check only", text)
        self.assertIn("has no switch", text)
        self.assertNotIn("OMNI_UPDATE", SERVER, "the release check gained a switch; say so in the guide")

    def test_what_the_checks_send_is_only_what_the_guide_says(self):
        release = SERVER[SERVER.index("def _fetch_release_feed"):SERVER.index("def _update_check(")]
        self.assertEqual(sorted(re.findall(r'"([A-Za-z-]+)":', release)), ["Accept", "User-Agent"])
        self.assertNotIn("Authorization", release)
        firmware = Path(ROOT / "omni_firmware.py").read_text(encoding="utf-8")
        fetch = firmware[firmware.index("def fetch_product"):firmware.index("except urllib.error.HTTPError")]
        self.assertEqual(sorted(re.findall(r'"([A-Za-z-]+)":', fetch)), ["Accept", "User-Agent"])
        self.assertNotIn("Authorization", fetch)

    def test_no_claim_of_telemetry_and_the_product_name_is_clean(self):
        text = section()
        self.assertIn("no usage statistics", text)
        self.assertNotRegex(GUIDE, r"(?i)omnisuite\s+plus")

    def test_the_firewall_table_lists_the_two_outbound_destinations(self):
        start = GUIDE.index('<h3 id="what-omnisuite-puts-on-the-network">')
        table = GUIDE[start:GUIDE.index("</table>", start)]
        self.assertIn("api.github.com", table)
        self.assertIn("hallresearch.com", table)
        self.assertIn('href="#internet-contact"', table)
        self.assertIn('id="internet-contact"', GUIDE)


if __name__ == "__main__":
    unittest.main()
