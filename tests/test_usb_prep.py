"""The USB Matrix is prepared when the inventory changes, and painted only while it still matches.

The page paints a prepared copy and then confirms it with its own request, exactly as the A/V
Matrix does. What makes that safe for a matrix that reads every unit is the inventory signature:
a digest of which USB-capable endpoints are in the inventory, read from memory and disk and from
no device. These tests hold what the signature has to be:

  * it moves when membership or an address changes, and when the inventory is cleared;
  * it does not move for anything live -- reachability, pairing, a unit that is not USB-capable;
  * it costs no device read;
  * /api/usb_state carries the same value, taken before it reads anything;
  * the route that serves it is classified.

Addresses are RFC 5737 documentation space. Nothing here reaches a network.
"""
try:
    from . import test_usb_integration as base
    from ._wsrecord import RecordingWebSocket
except ImportError:                                   # loaded by path, no package
    import test_usb_integration as base
    from _wsrecord import RecordingWebSocket

import omni_access

srv = base.srv


def _unit(mac, ip, model="HW-OMNI-E4521"):
    return {"mac": mac, "ip": ip, "hostname": "unit-" + ip.replace(".", "-"), "model": model, "role": "encoder"}


class InventorySignatureTests(RecordingWebSocket, base.ServerTestBase):

    def setUp(self):
        super().setUp()
        self.install_recording_ws()

    def signature(self):
        response = self.client.get("/api/usb_state/signature")
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertTrue(body["ok"])
        return body["signature"]

    def test_an_empty_inventory_has_a_stable_signature(self):
        self._patch_cache([])
        self.assertEqual(self.signature(), self.signature())

    def test_adding_removing_or_moving_a_usb_unit_moves_it(self):
        self._patch_cache([_unit("AA:BB:CC:00:00:01", "192.0.2.11")])
        one = self.signature()
        self._patch_cache([_unit("AA:BB:CC:00:00:01", "192.0.2.11"), _unit("AA:BB:CC:00:00:02", "192.0.2.12", "HW-OMNI-D4511")])
        two = self.signature()
        self._patch_cache([_unit("AA:BB:CC:00:00:01", "192.0.2.11")])
        back = self.signature()
        self._patch_cache([_unit("AA:BB:CC:00:00:01", "192.0.2.99")])
        moved = self.signature()
        self.assertNotEqual(one, two, "a unit arrived")
        self.assertEqual(one, back, "and leaving again restores it, so a rescan of the same units matches")
        self.assertNotEqual(one, moved, "a member that changed address would otherwise be painted at the old one")

    def test_a_unit_that_is_not_usb_capable_does_not_move_it(self):
        self._patch_cache([_unit("AA:BB:CC:00:00:01", "192.0.2.11")])
        before = self.signature()
        self._patch_cache([_unit("AA:BB:CC:00:00:01", "192.0.2.11"),
                           _unit("AA:BB:CC:00:00:03", "192.0.2.13", "HW-OMNI-E4111")])
        self.assertEqual(before, self.signature())

    def test_mac_formatting_does_not_move_it(self):
        self._patch_cache([_unit("AA:BB:CC:00:00:01", "192.0.2.11")])
        before = self.signature()
        self._patch_cache([_unit("aa-bb-cc-00-00-01", "192.0.2.11")])
        self.assertEqual(before, self.signature())

    def test_a_standalone_endpoint_arriving_and_clearing_moves_it(self):
        self._patch_cache([])
        empty = self.signature()
        self.seed((base.OMNI324_MAC, base.OMNI324_IP, 1))
        with_standalone = self.signature()
        self.assertNotEqual(empty, with_standalone)
        self.service.clear_devices()
        self.assertEqual(empty, self.signature(), "Clear Units leaves the USB Matrix with nothing to paint from before")

    def test_live_state_does_not_move_it(self):
        self._patch_cache([_unit("AA:BB:CC:00:00:01", "192.0.2.11")])
        self.seed((base.OMNI324_MAC, base.OMNI324_IP, 1))
        before = self.signature()
        srv._record_parent_live("192.0.2.11")
        self._patch_cache([dict(_unit("AA:BB:CC:00:00:01", "192.0.2.11"), online=False, liveness_source="probe")])
        self.assertEqual(before, self.signature())

    def test_it_reads_no_device(self):
        self._patch_cache([_unit("AA:BB:CC:00:00:01", "192.0.2.11")])
        self.seed((base.OMNI324_MAC, base.OMNI324_IP, 1))
        self.ws_requested[:] = []
        sent_before = len(self.transmitted)
        for _ in range(5):
            self.signature()
        self.assertEqual(self.ws_requested, [], "no WebSocket read for a signature")
        self.assertEqual(len(self.transmitted), sent_before, "and no datagram")
        self._assert_no_mutation_attempted()


class UsbStateCarriesTheSignatureTests(RecordingWebSocket, base.ServerTestBase):

    def setUp(self):
        super().setUp()
        self.install_recording_ws()

    def test_state_and_endpoint_agree_when_nothing_changed(self):
        self._patch_cache([_unit("AA:BB:CC:00:00:01", "192.0.2.11")])
        state = self.client.get("/api/usb_state").get_json()
        self.assertTrue(state["ok"])
        self.assertEqual(state["inventory_signature"], self.client.get("/api/usb_state/signature").get_json()["signature"])

    def test_the_empty_branch_carries_it_too(self):
        self._patch_cache([])
        state = self.client.get("/api/usb_state").get_json()
        self.assertIn("inventory_signature", state)
        self.assertEqual(state["inventory_signature"], self.client.get("/api/usb_state/signature").get_json()["signature"])

    def test_it_is_taken_before_anything_is_read(self):
        """A change during the request leaves the snapshot with the older signature, which fails the check."""
        self._patch_cache([_unit("AA:BB:CC:00:00:01", "192.0.2.11")])
        old = self.client.get("/api/usb_state/signature").get_json()["signature"]
        real = srv._usb_live_refresh

        def changes_inventory_mid_request(*args, **kwargs):
            self._patch_cache([_unit("AA:BB:CC:00:00:01", "192.0.2.11"), _unit("AA:BB:CC:00:00:02", "192.0.2.12", "HW-OMNI-D4511")])
            return real(*args, **kwargs)

        srv._usb_live_refresh = changes_inventory_mid_request
        self.addCleanup(lambda: setattr(srv, "_usb_live_refresh", real))
        state = self.client.get("/api/usb_state").get_json()
        self.assertEqual(state["inventory_signature"], old)
        self.assertNotEqual(state["inventory_signature"], self.client.get("/api/usb_state/signature").get_json()["signature"])

    def test_opening_the_state_reads_a_unit_once_per_request_not_more(self):
        """The page's confirming request is the only read an open costs; the signature adds none."""
        self._patch_cache([_unit("AA:BB:CC:00:00:01", "192.0.2.11")])
        self.client.get("/api/usb_state/signature")
        self.assertEqual([r for r in self.ws_requested if r[1] == "usb_icron"], [])


class SignatureRouteIsClassifiedTests(base.ServerTestBase):

    def test_it_is_an_administrative_route(self):
        access, feature = omni_access.classify("/api/usb_state/signature", "GET")
        self.assertEqual(access, omni_access.Access.ADMIN)
        self.assertIsNone(feature)

    def test_a_restricted_user_cannot_reach_it(self):
        self.assertNotIn("/api/usb_state/signature", [rule for rule in omni_access.ROUTES
                                                      if omni_access.is_user_route(rule)])


if __name__ == "__main__":
    import unittest
    unittest.main()
