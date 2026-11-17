"""The credential that authenticated a device is that device's working credential.

Found on hardware: a unit discovered with the fallback password could not have its
preview switched, because that endpoint presented the configured primary and nothing
else. Mixed-password installations have to work, for every authenticated operation.

The device is a fake that accepts exactly one password per address and records what it
was offered. Nothing here reaches a network: the WebSocket library is fenced, and the
transport under test is the real credentialed one, reached under its own name because
the test package replaces the public name with a fenced stand-in.
"""
import copy
import json
import logging
import threading
import unittest

try:
    from . import test_usb_integration as base
except ImportError:                                  # run by path
    import test_usb_integration as base

srv = base.srv

PRIMARY = "primary-secret-A"
FALLBACK = "fallback-secret-B"
THIRD = "changed-secret-C"
ADDR_A = "192.0.2.11"      # accepts the primary
ADDR_B = "192.0.2.12"      # accepts only the fallback
MAC_B = "02:00:5e:00:53:12"
MAC_B2 = "02:00:5e:00:53:99"


class FakeDevices:
    """One accepted password per address; every offer recorded by name, never value."""

    def __init__(self):
        self.accepts = {ADDR_A: PRIMARY, ADDR_B: FALLBACK}
        self.offers = []                 # (address, node, "ok"/"refused", password-class)
        self.writes = []
        self.vc2 = {ADDR_A: [{"name": "vc2_encoder1", "thumbnail": {"enable": False}}],
                    ADDR_B: [{"name": "vc2_encoder1", "thumbnail": {"enable": False}}]}

    @staticmethod
    def cls(pwd):
        return {PRIMARY: "primary", FALLBACK: "fallback", THIRD: "third"}.get(pwd, "other")

    def __call__(self, url, payload, timeout=None, *a, **k):
        address = url.split("//", 1)[1].split(":", 1)[0]
        offered = payload.get("password")
        node = payload.get("config_get") or (payload.get("config_set") or {}).get("name") or "?"
        ok = self.accepts.get(address) == offered
        self.offers.append((address, node, "ok" if ok else "refused", self.cls(offered)))
        if not ok:
            return {"id": payload.get("id"), "error": "Invalid username/password"}
        if "config_set" in payload:
            self.writes.append((address, node, self.cls(offered)))
            if node == "vc2":
                self.vc2[address] = copy.deepcopy(payload["config_set"]["config"])
            return {"id": payload.get("id"), "error": False}
        if node == "vc2":
            return {"id": payload.get("id"), "config": copy.deepcopy(self.vc2[address])}
        return {"id": payload.get("id"), "config": [{"name": node}]}


class WorkingCredentialTests(base.ServerTestBase):
    def setUp(self):
        super().setUp()
        self.devices = FakeDevices()
        # The base class pins the inventory to a fixture; this suite is about the
        # recorded credential in the real one, which lives in the temp folder.
        stubbed = srv._load_cache
        self.addCleanup(lambda: setattr(srv, "_load_cache", stubbed))
        srv._load_cache = self._real_load_cache
        real_raw = srv._ws_send_recv_raw
        self.addCleanup(lambda: setattr(srv, "_ws_send_recv_raw", real_raw))
        srv._ws_send_recv_raw = self.devices
        # The public name is the fence in tests; the real implementation is the subject.
        self.addCleanup(lambda s=srv._ws_send_recv: setattr(srv, "_ws_send_recv", s))
        srv._ws_send_recv = srv._ws_send_recv_credentialed
        previous = {k: srv.app.config.get(k) for k in ("PASSWORD", "FALLBACK_PASSWORD")}
        self.addCleanup(lambda: srv.app.config.update(previous))
        srv.app.config.update(PASSWORD=PRIMARY, FALLBACK_PASSWORD=FALLBACK)
        with srv._working_cred_lock:
            saved = (dict(srv._working_cred_by_identity),
                     dict(srv._working_cred_identity_by_ip),
                     set(srv._working_cred_suspect), srv._working_cred_warmed)
            srv._working_cred_by_identity.clear()
            srv._working_cred_identity_by_ip.clear()
            srv._working_cred_suspect.clear()
        srv._working_cred_warmed = True       # the inventory under test is the temp one

        def restore():
            with srv._working_cred_lock:
                srv._working_cred_by_identity.clear()
                srv._working_cred_by_identity.update(saved[0])
                srv._working_cred_identity_by_ip.clear()
                srv._working_cred_identity_by_ip.update(saved[1])
                srv._working_cred_suspect.clear()
                srv._working_cred_suspect.update(saved[2])
            srv._working_cred_warmed = saved[3]
        self.addCleanup(restore)
        self.client = srv.app.test_client()

    def url(self, address):
        return "ws://%s:80/ws" % address

    def seed_cache(self, address=ADDR_B, mac=MAC_B, password=FALLBACK):
        unit = {"ip": address, "mac": mac, "password": password, "role": "encoder",
                "model": "synthetic-encoder", "hostname": "synthetic"}
        srv._save_cache([unit])
        return unit

    # -- the transport ----------------------------------------------------

    def test_a_learned_credential_replaces_the_configured_one_on_the_next_call(self):
        srv._learn_working_credential(ADDR_B, FALLBACK, MAC_B)
        resp = srv._ws_send_recv(self.url(ADDR_B), {"id": "x", "username": "admin",
                                                    "password": PRIMARY, "config_get": "net"}, 1)
        self.assertFalse(resp.get("error"))
        self.assertEqual([o[2:] for o in self.devices.offers], [("ok", "fallback")],
                         "the unit was offered the configured primary first")

    def test_success_teaches_the_transport(self):
        srv._ws_send_recv(self.url(ADDR_B), {"id": "1", "username": "admin",
                                             "password": FALLBACK, "config_get": "net"}, 1)
        self.devices.offers.clear()
        srv._ws_send_recv(self.url(ADDR_B), {"id": "2", "username": "admin",
                                             "password": PRIMARY, "config_get": "net"}, 1)
        self.assertEqual([o[2:] for o in self.devices.offers], [("ok", "fallback")])

    def test_a_device_on_the_configured_credential_is_untouched(self):
        srv._learn_working_credential(ADDR_A, PRIMARY, "")
        srv._learn_working_credential(ADDR_B, FALLBACK, MAC_B)
        srv._ws_send_recv(self.url(ADDR_A), {"id": "1", "username": "admin",
                                             "password": PRIMARY, "config_get": "net"}, 1)
        self.assertEqual(self.devices.offers[-1][2:], ("ok", "primary"))

    def test_an_explicit_password_that_is_not_a_global_one_is_never_replaced(self):
        srv._learn_working_credential(ADDR_B, FALLBACK, MAC_B)
        srv._ws_send_recv(self.url(ADDR_B), {"id": "1", "username": "admin",
                                             "password": THIRD, "config_get": "net"}, 1)
        self.assertEqual(self.devices.offers[-1][2:], ("refused", "third"))

    def test_credential_follows_the_mac_across_an_address_change(self):
        srv._learn_working_credential(ADDR_B, FALLBACK, MAC_B)
        moved = "192.0.2.77"
        self.devices.accepts[moved] = FALLBACK
        srv._ingest_working_credentials([{"ip": moved, "mac": MAC_B, "password": FALLBACK}])
        srv._ws_send_recv(self.url(moved), {"id": "1", "username": "admin",
                                            "password": PRIMARY, "config_get": "net"}, 1)
        self.assertEqual(self.devices.offers[-1][2:], ("ok", "fallback"))

    def test_a_different_unit_at_a_reused_address_is_not_given_the_credential(self):
        srv._learn_working_credential(ADDR_B, FALLBACK, MAC_B)
        srv._ingest_working_credentials([{"ip": ADDR_B, "mac": MAC_B2, "password": PRIMARY}])
        self.devices.accepts[ADDR_B] = PRIMARY
        srv._ws_send_recv(self.url(ADDR_B), {"id": "1", "username": "admin",
                                             "password": PRIMARY, "config_get": "net"}, 1)
        self.assertEqual(self.devices.offers[-1][2:], ("ok", "primary"))

    def test_a_refused_learned_credential_does_not_lock_out_re_resolution(self):
        srv._learn_working_credential(ADDR_B, FALLBACK, MAC_B)
        self.devices.accepts[ADDR_B] = THIRD            # changed on the device itself
        r1 = srv._ws_send_recv(self.url(ADDR_B), {"id": "1", "username": "admin",
                                                  "password": PRIMARY, "config_get": "net"}, 1)
        self.assertTrue(r1.get("error"))
        # Next attempt presents what the caller offered, not the stale belief.
        self.devices.offers.clear()
        srv._ws_send_recv(self.url(ADDR_B), {"id": "2", "username": "admin",
                                             "password": PRIMARY, "config_get": "net"}, 1)
        self.assertEqual(self.devices.offers[0][3], "primary")
        self.devices.accepts[ADDR_B] = PRIMARY
        r3 = srv._ws_send_recv(self.url(ADDR_B), {"id": "3", "username": "admin",
                                                  "password": PRIMARY, "config_get": "net"}, 1)
        self.assertFalse(r3.get("error"))
        self.devices.offers.clear()
        srv._ws_send_recv(self.url(ADDR_B), {"id": "4", "username": "admin",
                                             "password": FALLBACK, "config_get": "net"}, 1)
        self.assertEqual(self.devices.offers[0][3], "primary", "the newly proven credential wins")

    def test_a_non_authentication_refusal_does_not_discard_the_credential(self):
        srv._learn_working_credential(ADDR_B, FALLBACK, MAC_B)
        original = self.devices.__call__

        def refuse_for_another_reason(url, payload, timeout=None, *a, **k):
            return {"id": payload.get("id"), "error": "Invalid bitrate"}
        srv._ws_send_recv_raw = refuse_for_another_reason
        srv._ws_send_recv(self.url(ADDR_B), {"id": "1", "username": "admin",
                                             "password": PRIMARY, "config_get": "net"}, 1)
        srv._ws_send_recv_raw = self.devices
        srv._ws_send_recv(self.url(ADDR_B), {"id": "2", "username": "admin",
                                             "password": PRIMARY, "config_get": "net"}, 1)
        self.assertEqual(self.devices.offers[-1][2:], ("ok", "fallback"))

    def test_discovery_presents_each_candidate_as_given(self):
        srv._learn_working_credential(ADDR_B, THIRD, MAC_B)      # a stale belief
        with srv._no_credential_substitution():
            r = srv._ws_send_recv(self.url(ADDR_B), {"id": "1", "username": "admin",
                                                     "password": FALLBACK, "config_get": "net"}, 1)
        self.assertFalse(r.get("error"))
        self.assertEqual(self.devices.offers[-1][2:], ("ok", "fallback"))

    def test_the_scan_probe_runs_without_substitution(self):
        """Discovery is what re-resolves a credential, so it is given none."""
        seen = []
        original = srv._probe_one.__wrapped__
        srv._learn_working_credential(ADDR_B, THIRD, MAC_B)       # a stale belief

        def spy(ip, user, pwd, *a, **k):
            seen.append(getattr(srv._cred_resolution, "off", False))
            return {}
        self.addCleanup(lambda f=srv._probe_one: setattr(srv, "_probe_one", f))
        srv._probe_one = srv._resolves_credentials(spy)
        srv._probe_one(ADDR_B, "admin", FALLBACK, 80, "/wsapp/", 1)
        self.assertEqual(seen, [True])
        self.assertFalse(getattr(srv._cred_resolution, "off", False), "the context leaked")

    # -- the recorded credential ----------------------------------------------

    def test_the_recorded_credential_survives_a_restart(self):
        self.seed_cache()
        with srv._working_cred_lock:
            srv._working_cred_by_identity.clear()
            srv._working_cred_identity_by_ip.clear()
        srv._working_cred_warmed = False
        srv._ws_send_recv(self.url(ADDR_B), {"id": "1", "username": "admin",
                                             "password": PRIMARY, "config_get": "net"}, 1)
        self.assertEqual(self.devices.offers[-1][2:], ("ok", "fallback"))

    def test_saving_the_inventory_after_a_credential_change_replaces_the_belief(self):
        srv._learn_working_credential(ADDR_B, FALLBACK, MAC_B)
        srv._save_cache([{"ip": ADDR_B, "mac": MAC_B, "password": THIRD}])
        self.assertEqual(srv._working_credential(ADDR_B), THIRD)

    # -- the endpoints ------------------------------------------------------

    def test_preview_set_uses_the_devices_own_credential(self):
        self.seed_cache()
        r = self.client.post("/api/set_thumbnail", json={"ip": ADDR_B, "enable": True})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assertTrue(r.get_json()["ok"])
        self.assertTrue(self.devices.vc2[ADDR_B][0]["thumbnail"]["enable"])
        self.assertEqual({w[2] for w in self.devices.writes}, {"fallback"})
        self.assertNotIn(("refused", "primary"), [o[2:] for o in self.devices.offers
                                                  if o[0] == ADDR_B and o[1] == "vc2"
                                                  and o[2] == "refused"] or [None])

    def test_preview_set_with_no_recorded_credential_finds_one_and_writes_once(self):
        r = self.client.post("/api/set_thumbnail", json={"ip": ADDR_B, "enable": True})
        self.assertTrue(r.get_json()["ok"], r.get_data(as_text=True))
        writes = [o for o in self.devices.offers if o[1] == "vc2" and o[0] == ADDR_B]
        self.assertEqual(self.devices.writes, [(ADDR_B, "vc2", "fallback")])
        self.assertEqual(writes[-1][2:], ("ok", "fallback"))

    def test_preview_status_uses_the_devices_own_credential(self):
        self.seed_cache()
        r = self.client.post("/api/get_thumbnail_status", json={"ip": ADDR_B})
        self.assertTrue(r.get_json()["ok"])
        self.assertEqual(self.devices.offers[0][2:], ("ok", "fallback"))

    def test_two_devices_with_different_credentials_both_work(self):
        srv._save_cache([
            {"ip": ADDR_A, "mac": "02:00:5e:00:53:11", "password": PRIMARY, "role": "encoder"},
            {"ip": ADDR_B, "mac": MAC_B, "password": FALLBACK, "role": "encoder"}])
        for address in (ADDR_A, ADDR_B):
            r = self.client.post("/api/set_thumbnail", json={"ip": address, "enable": True})
            self.assertTrue(r.get_json()["ok"], (address, r.get_data(as_text=True)))
        self.assertEqual(sorted(self.devices.writes),
                         [(ADDR_A, "vc2", "primary"), (ADDR_B, "vc2", "fallback")])

    def test_multiview_credentials_come_from_the_device_record(self):
        self.seed_cache()
        device, user, pwd, *_ = srv._multiview_credentials(ADDR_B)
        self.assertEqual(pwd, FALLBACK)

    def test_a_global_password_call_site_reaches_a_mixed_unit(self):
        # Many handlers build the request from the configured password. The
        # transport is what makes them work on a unit that has its own.
        self.seed_cache()
        srv._load_cache()
        for node in ("net", "ip_input", "hdmi_output", "multiview", "vc2"):
            self.devices.offers.clear()
            r = srv._ws_send_recv(self.url(ADDR_B), {"id": node, "username": "admin",
                                                     "password": srv.app.config["PASSWORD"],
                                                     "config_get": node}, 1)
            self.assertFalse(r.get("error"), node)
            self.assertEqual(len(self.devices.offers), 1, node)

    # -- nothing is exposed ---------------------------------------------------

    def test_no_password_reaches_a_response_or_a_log(self):
        self.seed_cache()
        handler = logging.Handler()
        records = []
        handler.emit = lambda record: records.append(record.getMessage())
        logging.getLogger().addHandler(handler)
        self.addCleanup(lambda: logging.getLogger().removeHandler(handler))
        old = logging.getLogger().level
        logging.getLogger().setLevel(logging.DEBUG)
        self.addCleanup(lambda: logging.getLogger().setLevel(old))
        bodies = []
        for path, body in (("/api/set_thumbnail", {"ip": ADDR_B, "enable": True}),
                           ("/api/get_thumbnail_status", {"ip": ADDR_B})):
            bodies.append(self.client.post(path, json=body).get_data(as_text=True))
        everything = "\n".join(bodies + records)
        for secret in (PRIMARY, FALLBACK, THIRD):
            self.assertNotIn(secret, everything)

    def test_the_working_credential_is_not_in_the_credential_report(self):
        srv._learn_working_credential(ADDR_B, FALLBACK, MAC_B)
        self.assertNotIn(FALLBACK, json.dumps(srv.credential_report()))

    def test_concurrent_calls_to_different_units_keep_their_own_credentials(self):
        srv._learn_working_credential(ADDR_A, PRIMARY, "02:00:5e:00:53:11")
        srv._learn_working_credential(ADDR_B, FALLBACK, MAC_B)
        failures = []

        def worker(address, expected):
            for _ in range(25):
                r = srv._ws_send_recv(self.url(address), {"id": "1", "username": "admin",
                                                          "password": PRIMARY, "config_get": "net"}, 1)
                if r.get("error"):
                    failures.append((address, expected))
        threads = [threading.Thread(target=worker, args=(ADDR_A, "primary")),
                   threading.Thread(target=worker, args=(ADDR_B, "fallback"))]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(failures, [])


if __name__ == "__main__":
    unittest.main()
