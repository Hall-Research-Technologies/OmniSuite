"""Admin USB Matrix eligibility must equal the User USB Matrix's, cell for cell.

The Admin page decides what each dot may do by rendering it; the user matrix and the user route decide
it from omni_usb_cells.derive. A second implementation is only worth having if something proves the two
agree, so this renders the REAL Admin page (ui/matrix/usb.js, through the DOM stub the existing smoke test
uses) from the same administrative payload and compares, for every cell: clickable, routed, active and
control path. The payloads are built with the server's own capability rule (_usb_route_capability), from
synthetic equipment that shares no address, MAC or hostname with the lab.

Where a Remote User's permissions further restrict what they see, the user matrix shows FEWER cells, never
a cell the Admin matrix would refuse; that is asserted separately (user_cells_are_a_subset...).
"""
from __future__ import annotations

import copy
import json
import os
import random
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("OMNI_SMOKE", "1")

import omni_usb_cells                                   # noqa: E402
import omni_users                                       # noqa: E402
from tests import test_user_usb_matrix as fx           # noqa: E402

NODE = "node"


def node_available():
    try:
        return subprocess.run([NODE, "--version"], capture_output=True, timeout=20).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def admin_cells(payload):
    """What the real Admin page draws for this payload: {(rex_mac, lex_mac): record}."""
    with tempfile.TemporaryDirectory(prefix="omni-parity-") as folder:
        path = Path(folder) / "state.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        done = subprocess.run([NODE, str(ROOT / "tests" / "usb_admin_cells_probe.js"), str(path)],
                              capture_output=True, text=True, timeout=120, cwd=str(ROOT))
    if done.returncode != 0:
        raise AssertionError("the Admin render probe failed: " + done.stderr[-800:])
    rows = json.loads(done.stdout)
    return {(omni_users._normalise_identity(r["rexMac"]), omni_users._normalise_identity(r["lexMac"])): r
            for r in rows}


def standalone(role, parent, ip, *, online=True, eligible=True, classification="STANDALONE"):
    row = fx.endpoint("standalone", role, parent, ip, ip, classification=classification, online=online)
    row["usb_key"] = row["usb_mac"]
    row["pairing_eligible"] = eligible
    row["pairing_state_fresh"] = True
    return row


def scenario_matrix(seed):
    """A deterministic random installation: integrated and standalone, several networks, routes, limits."""
    rng = random.Random(seed)
    nets = ["198.51.100", "203.0.113", "192.0.2"]
    lexes, rexes = [], []
    for n in range(rng.randint(1, 3)):
        net = rng.choice(nets)
        if rng.random() < .7:
            lexes.append(fx.lex("0A:1B:2C:A1:%02X:01" % n, "%s.%d" % (net, 20 + n), "%s.%d" % (rng.choice(nets), 120 + n),
                                classification=rng.choice(["INTEGRATED"] * 5 + ["UNCONFIRMED"])))
        else:
            lexes.append(standalone("LEX", "0A:1B:2C:A2:%02X:01" % n, "%s.%d" % (net, 80 + n),
                                    online=rng.random() > .2, eligible=rng.random() > .1))
    for n in range(rng.randint(1, 7)):
        net = rng.choice(nets)
        if rng.random() < .7:
            rexes.append(fx.rex("0A:1B:2C:B1:%02X:01" % n, "%s.%d" % (net, 40 + n), "%s.%d" % (rng.choice(nets), 140 + n)))
        else:
            rexes.append(standalone("REX", "0A:1B:2C:B2:%02X:01" % n, "%s.%d" % (net, 90 + n),
                                    online=rng.random() > .2))
    udp = {}
    routed = []
    for r in rexes:
        pick = rng.choice(lexes) if rng.random() < .5 else None
        if pick is None:
            if r["kind"] == "standalone":
                udp[r["usb_key"]] = {"active": None, "available": [], "fresh": True}
            continue
        if pick["kind"] == "integrated" and r["kind"] == "integrated":
            routed.append((pick["ip"], r["ip"], rng.random() > .3))
        else:
            # A UDP-observed route: keyed by the REX's own key, owner named by canonical USB MAC.
            udp[r["usb_key"]] = {"active": pick["usb_mac"], "available": [],
                                 "fresh": rng.random() > .25}
    state = fx.admin_payload(lexes, rexes, routed, udp)
    # Standalone units are keyed by MAC everywhere the Admin payload keys them.
    return state


class ParityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not node_available():
            raise unittest.SkipTest("node is not available; the Admin render cannot be run")

    def assert_parity(self, state, label):
        admin = admin_cells(state)
        user = omni_usb_cells.derive(state)
        self.assertEqual(set(admin), set(user), label + ": the two draw different cells")
        for key, record in user.items():
            row = admin[key]
            with self.subTest(label=label, cell=key):
                self.assertEqual(not row["disabled"], record["can_act"],
                                 "clickable differs (admin %s, user %s) for %s" % (
                                     not row["disabled"], record["can_act"], record["public"]))
                self.assertEqual(row["paired"], record["routed"], "routed differs")
                self.assertEqual(row["active"], record["public"]["active"], "active differs")
                self.assertEqual(row["path"], record["path"], "control path differs")

    def test_the_standard_installation(self):
        self.assert_parity(fx.standard_state(), "standard")

    def test_with_routes_in_place(self):
        self.assert_parity(fx.standard_state(routed=[(fx.NET_A + ".21", fx.NET_A + ".31", True),
                                                     (fx.NET_A + ".21", fx.NET_A + ".32", False),
                                                     (fx.NET_A + ".22", fx.NET_A + ".34", True)]),
                           "routed")

    def test_a_route_onto_a_blocked_cell_is_removable_in_neither_matrix(self):
        # Admin quirk, restated rather than "fixed": a disabled capability disables the dot even when a
        # route is already there. Parity means the user matrix has the same quirk.
        state = fx.standard_state(routed=[(fx.NET_A + ".21", fx.NET_B + ".33", True)])
        self.assert_parity(state, "routed-but-blocked")

    def test_the_integrated_peer_limit_boundary_agrees(self):
        # Exactly five routed, then the sixth: the boundary a random installation rarely lands on.
        lexes = [fx.lex(fx.PC1_PARENT, fx.NET_A + ".21", fx.NET_A + ".121")]
        rexes = [fx.rex("0A:1B:2C:C0:00:%02X" % n, fx.NET_A + ".%d" % (60 + n), fx.NET_A + ".%d" % (160 + n))
                 for n in range(7)]
        for routed_count in (4, 5, 6):
            routed = [(fx.NET_A + ".21", fx.NET_A + ".%d" % (60 + n), True) for n in range(routed_count)]
            self.assert_parity(fx.admin_payload(lexes, rexes, routed), "integrated limit, %d routed" % routed_count)

    def test_the_standalone_peer_limit_boundary_agrees(self):
        lexes = [standalone("LEX", "0A:1B:2C:A2:00:01", "198.51.100.81")]
        rexes = [standalone("REX", "0A:1B:2C:B2:%02X:01" % n, "198.51.100.%d" % (90 + n)) for n in range(9)]
        for routed_count in (6, 7, 8):
            udp = {r["usb_key"]: {"active": lexes[0]["usb_mac"] if i < routed_count else None,
                                  "available": [], "fresh": True} for i, r in enumerate(rexes)}
            self.assert_parity(fx.admin_payload(lexes, rexes, [], udp), "standalone limit, %d routed" % routed_count)

    def test_randomised_installations_agree_cell_for_cell(self):
        for seed in range(24):
            self.assert_parity(scenario_matrix(seed), "seed %d" % seed)

    def test_the_user_matrix_never_offers_a_cell_the_admin_matrix_refuses(self):
        # With permissions applied the user sees a subset of the Admin's cells, each with the Admin's verdict.
        import omni_projection
        state = scenario_matrix(7)
        admin = admin_cells(state)
        units = []
        for row in state["matrix_lex"] + state["matrix_rex"]:
            units.append({"ip": row["ip"], "mac": row["mac"], "hostname": row["host"]})

        class Account:
            is_admin = False

            def __init__(self, sources, destinations):
                from omni_users import Permissions
                self.permissions = Permissions(features=("usb",), sources=tuple(sources),
                                               destinations=tuple(destinations))

        every_lex = [r["mac"] for r in state["matrix_lex"]]
        every_rex = [r["mac"] for r in state["matrix_rex"]]
        subset = Account(every_lex[:1], every_rex[: max(1, len(every_rex) // 2)])
        projected = omni_projection.project_usb_state(copy.deepcopy(state), units, subset, None)
        shown = 0
        for rex_id, row in projected["cells"].items():
            for lex_id, cell in row.items():
                shown += 1
                self.assertEqual(cell["enabled"], not admin[(rex_id, lex_id)]["disabled"])
        self.assertGreater(shown, 0)
        self.assertLessEqual(shown, len(admin))


if __name__ == "__main__":
    unittest.main()
