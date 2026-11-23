"""Device Manager data semantics: every field a probe records is the field it is named for.

Found by a person on hardware: Model showed the firmware, TimeZone showed the NTP server.
The server was right (see test_server_keeps_model_and_firmware_apart); the page patched cells by
position. These tests give every field a value no other field shares, so an alias between any two
cannot pass, and nothing here touches a device.
"""
import copy
import unittest

try:
    from . import test_usb_integration as base
except ImportError:
    import test_usb_integration as base

srv = base.srv

ADDR = "192.0.2.77"
VALUES = {
    "model": "HW-OMNI-E4511", "firmware": "7.3.5", "host": "synthetic-host-q", "type": "OmniStream Encoder",
    "serial": "SN-0099-ZZ", "tz": "Pacific/Auckland", "ntp": "ntp.synthetic.example",
    "mac_hex": "02005e005377", "linkspeed": 2500, "mode": "av",
}


def fake_device(url, payload, timeout=None, *a, **k):
    node = payload.get("config_get")
    if node == "systeminfo":
        return {"config": {"model": VALUES["model"], "firmwareversion": VALUES["firmware"],
                           "hostname": VALUES["host"], "type": VALUES["type"],
                           "ntpserver": VALUES["ntp"], "system_mode": VALUES["mode"],
                           "board": {"serialnumber": VALUES["serial"]}}}
    if node == "license":
        return {"config": {"device_id": VALUES["mac_hex"]}}
    if node == "timezone":
        return {"config": {"timezone": VALUES["tz"], "active_timezone": VALUES["tz"]}}
    if node == "net":
        return {"config": [{"name": "eth1", "linkspeed": VALUES["linkspeed"]}]}
    if node == "sessions":
        return {"config": []}
    return {"config": {}}


class DeviceColumnSemantics(base.ServerTestBase):
    def setUp(self):
        super().setUp()
        self.addCleanup(lambda s=srv._ws_send_recv: setattr(srv, "_ws_send_recv", s))
        srv._ws_send_recv = fake_device
        self.unit = srv._probe_one(ADDR, "admin", "x", 80, "/wsapp/", 1)

    def test_every_field_holds_its_own_value(self):
        u = self.unit
        self.assertIsNotNone(u)
        self.assertEqual(u["model"], VALUES["model"])
        self.assertEqual(u["version"], VALUES["firmware"])
        self.assertEqual(u["hostname"], VALUES["host"])
        self.assertEqual(u["type"], VALUES["type"])
        self.assertEqual(u["serialnumber"], VALUES["serial"])
        self.assertEqual(u["timezone"], VALUES["tz"])
        self.assertEqual(u["active_timezone"], VALUES["tz"])
        self.assertEqual(u["ntp_server"], VALUES["ntp"])
        self.assertEqual(u["mac"], "02:00:5e:00:53:77")
        self.assertEqual(u["linkspeed"], VALUES["linkspeed"])
        self.assertEqual(u["ip"], ADDR)

    def test_server_keeps_model_and_firmware_apart(self):
        self.assertNotEqual(self.unit["model"], self.unit["version"])
        self.assertNotEqual(self.unit["timezone"], self.unit["ntp_server"])

    def test_the_values_are_all_distinct_so_an_alias_cannot_pass(self):
        shown = [self.unit[k] for k in ("model", "version", "hostname", "type", "serialnumber",
                                        "timezone", "ntp_server", "mac")]
        self.assertEqual(len(set(map(str, shown))), len(shown))

    def test_the_export_columns_follow_the_same_fields(self):
        row = dict(zip(srv.INVENTORY_COLUMNS, srv._inventory_row(copy.deepcopy(self.unit))))
        self.assertEqual(row["Model"], VALUES["model"])
        self.assertEqual(row["Version"], VALUES["firmware"])
        self.assertEqual(row["TimeZone"], VALUES["tz"])
        self.assertEqual(row["NTP Server"], VALUES["ntp"])
        self.assertEqual(row["Hostname"], VALUES["host"])
        self.assertEqual(row["MAC"], "02:00:5e:00:53:77")
        self.assertEqual(row["LinkSpeed"], VALUES["linkspeed"])

    def test_a_unit_with_no_timezone_does_not_borrow_the_ntp_server(self):
        unit = copy.deepcopy(self.unit)
        unit["timezone"] = unit["active_timezone"] = ""
        row = dict(zip(srv.INVENTORY_COLUMNS, srv._inventory_row(unit)))
        self.assertEqual(row["TimeZone"], "")
        self.assertEqual(row["NTP Server"], VALUES["ntp"])

    def test_a_unit_with_no_model_does_not_borrow_the_firmware(self):
        unit = copy.deepcopy(self.unit)
        unit["model"] = ""
        row = dict(zip(srv.INVENTORY_COLUMNS, srv._inventory_row(unit)))
        self.assertEqual(row["Model"], "")
        self.assertEqual(row["Version"], VALUES["firmware"])


if __name__ == "__main__":
    unittest.main()
