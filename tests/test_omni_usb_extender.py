import json

import logging

import socket

import tempfile

import threading

import time

import unittest

from pathlib import Path



import omni_usb_extender as usb



# Several tests deliberately drive protocol and interface failures; the module

# logs those by design, so keep the expected noise out of the test report.

logging.getLogger("omni_usb_extender").setLevel(logging.CRITICAL)





HOST_MAC = "00:1B:13:02:81:E0"

DEVICE_MAC = "00:1B:13:02:81:E1"

OTHER_HOST_MAC = "00:1B:13:02:81:E2"

IMPOSTOR_MAC = "00:AA:BB:CC:DD:EE"

HOST_IP = "192.168.1.10"

DEVICE_IP = "192.168.1.20"

IFACE_IP = "192.168.1.50"

IFACE_MASK = "255.255.255.0"





def query_payload(mac, ip, mode=1, product="Atlona USB Extender",

                  identification="USB Over Network", revision="1.9.4", protocol_byte=0x03):

    """Basic Device Information exactly as firmware 1.9.4 emits it.



    MAC(6) + IPv4(4) + mode(1) + protocol byte(1) + vendor(32) + product(32)

    + firmware(12). The protocol byte and the narrower firmware slot are what

    the earlier layout got wrong, which is why firmware never appeared.

    """

    payload = usb.mac_bytes(mac) + bytes(map(int, ip.split("."))) + bytes([mode, protocol_byte])

    for value, width in zip((product, identification, revision), (32, 32, 12)):

        payload += value.encode("utf-8").ljust(width, b"\0")

    return payload





def query_response(message_id=7, mac=HOST_MAC, ip="10.0.1.119", mode=usb.NETWORK_MODE_DHCP):

    return usb.packet(message_id, usb.QUERY_RESPONSE, query_payload(mac, ip, mode))





def advanced_response(message_id, device_code, peers=()):

    return usb.packet(message_id, usb.ADVANCED_QUERY_RESPONSE,

                      bytes([device_code]) + b"".join(usb.mac_bytes(peer) for peer in peers))





def make_service(folder, **kwargs):

    kwargs.setdefault("persist_debounce", 0)

    return usb.ExtenderDiscoveryService(Path(folder) / "state.json", **kwargs)





class FakeNetwork:

    """Scriptable UDP peer set used in place of real sockets.



    Every endpoint decides independently whether it answers Query and Advanced

    Query, so the tests can reproduce partial-failure cases exactly.

    """



    def __init__(self):

        self.endpoints = {}

        self.sent = []

        self.binds = []



    def add(self, ip, mac, device_code, peers=(), query=True, advanced=True, ack=True):

        self.endpoints[ip] = {"mac": mac, "code": device_code, "peers": list(peers),

                              "query": query, "advanced": advanced, "ack": ack}

        return self



    def exchange(self, destination, request, message_id, timeout=usb.DEFAULT_TIMEOUT, bind_ip=None):

        self.binds.append(bind_ip)

        command = int.from_bytes(request[8:10], "big")

        self.sent.append((destination, command, request[10:], bind_ip))

        endpoint = self.endpoints.get(destination)

        if endpoint is None:

            raise socket.timeout()

        if command == usb.QUERY:

            if not endpoint["query"]:

                raise socket.timeout()

            return usb.packet(message_id, usb.QUERY_RESPONSE, query_payload(endpoint["mac"], destination)), (destination, usb.UDP_PORT)

        if command == usb.ADVANCED_QUERY:

            if not endpoint["advanced"]:

                raise socket.timeout()

            if endpoint["advanced"] == "malformed":

                return usb.packet(message_id, usb.ADVANCED_QUERY_RESPONSE, bytes([endpoint["code"]]) + b"\x01\x02\x03"), (destination, usb.UDP_PORT)

            return advanced_response(message_id, endpoint["code"], endpoint["peers"]), (destination, usb.UDP_PORT)

        if endpoint["ack"] is None:

            raise socket.timeout()

        return usb.packet(message_id, usb.ACK if endpoint["ack"] else usb.NACK), (destination, usb.UDP_PORT)



    def commands(self):

        return [(destination, command) for destination, command, _payload, _bind in self.sent

                if command not in (usb.QUERY, usb.ADVANCED_QUERY)]





class ProtocolTests(unittest.TestCase):

    def test_query_packet_and_response(self):

        self.assertEqual(usb.build_query(3).hex(), "2f03f4a2000000030000")

        parsed = usb.parse_query_response(query_response())

        self.assertEqual(parsed["mac"], HOST_MAC)

        self.assertEqual(parsed["ip"], "10.0.1.119")

        self.assertEqual(parsed["network_mode"], "DHCP")

        self.assertEqual(parsed["product"], "Atlona USB Extender")

        self.assertEqual(parsed["product_identification"], "USB Over Network")

        self.assertEqual(parsed["product_revision"], "1.9.4")



    def test_advanced_query_311_and_324(self):

        self.assertEqual(usb.build_advanced_query(9).hex(), "2f03f4a2000000090300")

        host = usb.parse_advanced_query_response(advanced_response(9, 0, [DEVICE_MAC]))

        device = usb.parse_advanced_query_response(advanced_response(10, 1, [HOST_MAC]))

        self.assertEqual(host["device_type"], "AT-OMNI-311")

        self.assertEqual(device["device_type"], "AT-OMNI-324")



    def test_mac_range_and_network_validation(self):

        self.assertEqual(usb.normalize_mac("001b.1302.81e0"), HOST_MAC)

        self.assertEqual(usb.parse_ranges(["192.168.1.1-192.168.1.2", "10.1.1.0/30"]),

                         ["192.168.1.1", "192.168.1.2", "10.1.1.1", "10.1.1.2"])

        self.assertEqual(usb.network_relation("192.168.1.50", "255.255.255.0", "192.168.1.40"), "LOCAL")

        self.assertEqual(usb.network_relation("192.168.1.50", "255.255.255.0", "192.168.100.25"), "OFF_NET")



    def test_wrong_id_malformed_and_ack_nack(self):

        with self.assertRaises(usb.ProtocolError): usb.parse_query_response(query_response(2), expected_message_id=3)

        with self.assertRaises(usb.ProtocolError): usb.parse_query_response(b"bad")

        self.assertTrue(usb.parse_ack(usb.packet(3, usb.ACK), 3)["ok"])

        self.assertFalse(usb.parse_ack(usb.packet(3, usb.NACK), 3)["ok"])





class NetworkModeTests(unittest.TestCase):

    """The documented Query network-mode byte: 0 is DHCP, 1 is static."""



    def parse(self, mode_byte):

        return usb.parse_query_response(usb.packet(1, usb.QUERY_RESPONSE, query_payload(HOST_MAC, "10.0.1.119", mode_byte)), 1)



    def test_mode_byte_zero_is_dhcp(self):

        parsed = self.parse(0)

        self.assertEqual(parsed["network_mode"], "DHCP")

        self.assertEqual(parsed["network_mode_code"], 0)



    def test_mode_byte_one_is_static(self):

        parsed = self.parse(1)

        self.assertEqual(parsed["network_mode"], "STATIC")

        self.assertEqual(parsed["network_mode_code"], 1)



    def test_constants_match_documented_values(self):

        self.assertEqual((usb.NETWORK_MODE_DHCP, usb.NETWORK_MODE_STATIC), (0, 1))



    def test_stored_state_carries_the_corrected_mode(self):

        with tempfile.TemporaryDirectory() as folder:

            service = make_service(folder)

            service._upsert(self.parse(0), IFACE_IP, IFACE_MASK, "DIRECT_IP")

            self.assertEqual(service.device(HOST_MAC)["network_mode"], "DHCP")

            service._upsert(self.parse(1), IFACE_IP, IFACE_MASK, "DIRECT_IP")

            self.assertEqual(service.device(HOST_MAC)["network_mode"], "STATIC")



    def test_legacy_persisted_records_are_healed_from_the_raw_byte(self):

        """A record written by the build with the inverted mapping must self-correct."""

        with tempfile.TemporaryDirectory() as folder:

            state = Path(folder) / "state.json"

            state.write_text(json.dumps({"ranges": [], "devices": {

                HOST_MAC: {"mac": HOST_MAC, "ip": "10.0.1.119", "network_mode_code": 0, "network_mode": "STATIC"},

                DEVICE_MAC: {"mac": DEVICE_MAC, "ip": "10.0.1.120", "network_mode_code": 1, "network_mode": "DHCP"},

            }}), encoding="utf-8")

            service = usb.ExtenderDiscoveryService(state, persist_debounce=0)

            self.assertEqual(service.device(HOST_MAC)["network_mode"], "DHCP")

            self.assertEqual(service.device(DEVICE_MAC)["network_mode"], "STATIC")



    def test_record_without_a_raw_byte_is_left_alone(self):

        with tempfile.TemporaryDirectory() as folder:

            state = Path(folder) / "state.json"

            state.write_text(json.dumps({"ranges": [], "devices": {

                HOST_MAC: {"mac": HOST_MAC, "ip": "10.0.1.119", "network_mode": "DHCP"}}}), encoding="utf-8")

            self.assertEqual(usb.ExtenderDiscoveryService(state, persist_debounce=0).device(HOST_MAC)["network_mode"], "DHCP")



    def test_write_commands_use_distinct_opcodes_not_the_mode_byte(self):

        # The read-side mapping was wrong; the write side never used this field,

        # so DHCP and static must remain separate documented opcodes.

        self.assertNotEqual(usb.IP_DHCP, usb.IP_STATIC)

        self.assertEqual(int.from_bytes(usb.build_ip_dhcp(1, HOST_MAC)[8:10], "big"), usb.IP_DHCP)

        self.assertEqual(int.from_bytes(usb.build_ip_static(1, HOST_MAC, "192.168.1.44", "255.255.255.0", "192.168.1.1")[8:10], "big"), usb.IP_STATIC)





class QueryFieldParsingTests(unittest.TestCase):

    """B3: an empty fixed-width field must not shift the fields after it."""



    def parse(self, product, identification, revision):

        payload = query_payload(HOST_MAC, "10.0.1.119", 1, product, identification, revision)

        return usb.parse_query_response(usb.packet(1, usb.QUERY_RESPONSE, payload), 1)



    def test_all_fields_populated(self):

        parsed = self.parse("AT-OMNI-311", "USB Over Network", "1.9.4")

        self.assertEqual((parsed["product"], parsed["product_identification"], parsed["product_revision"]),

                         ("AT-OMNI-311", "USB Over Network", "1.9.4"))



    def test_empty_identification_does_not_shift_revision(self):

        parsed = self.parse("AT-OMNI-311", "", "1.9.4")

        self.assertEqual(parsed["product"], "AT-OMNI-311")

        self.assertEqual(parsed["product_identification"], "")

        self.assertEqual(parsed["product_revision"], "1.9.4")



    def test_empty_product_does_not_shift_identification(self):

        parsed = self.parse("", "USB Over Network", "1.9.4")

        self.assertEqual(parsed["product"], "")

        self.assertEqual(parsed["product_identification"], "USB Over Network")

        self.assertEqual(parsed["product_revision"], "1.9.4")



    def test_empty_revision_keeps_earlier_fields(self):

        parsed = self.parse("AT-OMNI-311", "USB Over Network", "")

        self.assertEqual(parsed["product_identification"], "USB Over Network")

        self.assertEqual(parsed["product_revision"], "")



    def test_padding_is_trimmed_per_slot(self):

        parsed = self.parse("AT-OMNI-311  ", "USB", "1.9.4")

        self.assertEqual(parsed["product"], "AT-OMNI-311")



    def test_a_truncated_response_is_rejected_rather_than_read_as_blanks(self):

        """It used to parse "successfully" with empty vendor, product and

        firmware, and those blanks then overwrote good stored values. A reply

        too short to carry the identity strings is a bad reply."""

        payload = usb.mac_bytes(HOST_MAC) + bytes([10, 0, 1, 119]) + bytes([1, 0x03]) + b"AT-OMNI-311\0".ljust(32, b"\0")

        with self.assertRaises(usb.ProtocolError):

            usb.parse_query_response(usb.packet(1, usb.QUERY_RESPONSE, payload), 1)


    def test_short_response_is_rejected(self):

        with self.assertRaises(usb.ProtocolError):

            usb.parse_query_response(usb.packet(1, usb.QUERY_RESPONSE, b"\x00" * 8), 1)





# ---------------------------------------------------------------------------

# Captured verbatim from physical Atlona hardware on firmware 1.9.4.

#

# Ground truth established from the devices themselves, because Basic Device

# Information reports the local MAC alongside that device's own IP address:

#

#   192.168.100.128  MAC 00:1B:13:04:E9:6E  role byte 0x00  two peers   -> AT-OMNI-311 (LEX/host)

#   192.168.100.127  MAC 00:1B:13:04:6A:EA  role byte 0x01  one peer    -> AT-OMNI-324 (REX/device)

#

# The trailing six-byte groups are PEER MACs: the .128 unit reports two of them,

# and a device cannot have two local MACs. Its peers are exactly the .127 unit

# and the integrated D4511 endpoint B8:98:B0:07:85:C7 it is routed to.

CAPTURED = {
    "basic_324":
        "2F03F4A2000000030001001B13046AEAC0A8647F000341746C6F6E612055534220322E3020457874656E6465"
        "72000000000000000000555342204F766572204E6574776F726B00000000000000000000000000000000312E"
        "392E3400000000000000",
    "basic_311":
        "2F03F4A2000000030001001B1304E96EC0A86480000341746C6F6E612055534220322E3020457874656E6465"
        "72000000000000000000555342204F766572204E6574776F726B00000000000000000000000000000000312E"
        "392E3400000000000000",
    "extended_324":
        "2F03F4A200000003030101001B1304E96E",
    "extended_311":
        "2F03F4A200000003030100001B13046AEAB898B00785C7",
    "full_config_324":
        "2F03F4A200000003030E010001000000001B13046AEA0000001B1304E96E0000000000000000000000000000"
        "0000000000000000000000000000000000000000000017F9C0A8647FFFFFFF00C0A86401C0A864010700089D"
        "0001000541746C6F6E612055534220322E3020457874656E646572000000000000000000555342204F766572"
        "204E6574776F726B0000000000000000312E392E34000000312E392E3400000061000580",
    "link_324":
        "2F03F4A20000000303100100000000000000001B1304E96E0000000000000000000000000000000000000000"
        "00000000000000000000000000000000",
    "link_311":
        "2F03F4A20000000303100101000000000000001B13046AEAB898B00785C70000000000000000000000000000"
        "00000000000000000000000000000000",
    "link_324_unlinked":
        "2F03F4A20000000303100200000000000000001B130550500000000000000000000000000000000000000000"
        "00000000000000000000000000000000",
    "extended_324_b":
        "2F03F4A200000003030101001B13055050",
    "basic_324_b":
        "2F03F4A2000000030001001B130517E9C0A86486000341746C6F6E612055534220322E3020457874656E6465"
        "72000000000000000000555342204F766572204E6574776F726B00000000000000000000000000000000312E"
        "392E3400000000000000",
    "link_311_five_peers":
        "2F03F4A20000000103100101010101000000001B13046AEAB898B00785C7001B130517E9B898B0078587B898"
        "B00785C1000000000000000000000000",
    "topology_empty":
        "2F03F4A2000000030308",
}


def captured(name):

    return bytes.fromhex(CAPTURED[name])





class CapturedHardwareBasicTests(unittest.TestCase):

    """Basic Device Information, byte for byte from the bench."""



    def test_the_324_reports_its_own_identity_and_firmware(self):

        parsed = usb.parse_query_response(captured("basic_324"))

        self.assertEqual(parsed["mac"], "00:1B:13:04:6A:EA")

        self.assertEqual(parsed["ip"], "192.168.100.127")

        self.assertEqual(parsed["vendor"], "Atlona USB 2.0 Extender")

        self.assertEqual(parsed["product_id"], "USB Over Network")

        self.assertEqual(parsed["firmware"], "1.9.4")



    def test_the_311_reports_its_own_identity_and_firmware(self):

        parsed = usb.parse_query_response(captured("basic_311"))

        self.assertEqual(parsed["mac"], "00:1B:13:04:E9:6E")

        self.assertEqual(parsed["ip"], "192.168.100.128")

        self.assertEqual(parsed["firmware"], "1.9.4")



    def test_firmware_is_not_swallowed_by_the_string_offset(self):

        """The regression this fixture exists for: firmware used to come back ''."""

        for name in ("basic_311", "basic_324"):

            self.assertEqual(usb.parse_query_response(captured(name))["product_revision"], "1.9.4", name)



    def test_the_product_string_carries_no_leading_protocol_byte(self):

        parsed = usb.parse_query_response(captured("basic_324"))

        self.assertFalse(parsed["product"].startswith("\x03"))

        self.assertEqual(parsed["protocol_byte_raw"], 0x03, "kept raw, never interpreted")



    def test_product_id_does_not_identify_the_model(self):

        """Both families answer 'USB Over Network'; it can never select a model."""

        self.assertEqual(usb.parse_query_response(captured("basic_311"))["product_id"],

                         usb.parse_query_response(captured("basic_324"))["product_id"])





class CapturedHardwareExtendedTests(unittest.TestCase):

    """The Atlona model discriminator, and what the trailing bytes really are."""



    def test_the_role_byte_zero_is_the_host_and_one_is_the_device(self):

        host = usb.parse_advanced_query_response(captured("extended_311"))

        device = usb.parse_advanced_query_response(captured("extended_324"))

        self.assertEqual(host["device_type_code"], 0x00)

        self.assertEqual(host["device_type"], usb.HOST_MODEL)

        self.assertEqual(device["device_type_code"], 0x01)

        self.assertEqual(device["device_type"], usb.DEVICE_MODEL)



    def test_the_trailing_groups_are_peers_not_the_local_mac(self):

        """The host reports two of them; nothing has two local MACs."""

        host = usb.parse_advanced_query_response(captured("extended_311"))

        self.assertEqual(host["paired_macs"], ["00:1B:13:04:6A:EA", "B8:98:B0:07:85:C7"])

        self.assertNotIn("00:1B:13:04:E9:6E", host["paired_macs"], "that is the host's own MAC")



    def test_the_device_side_reports_its_single_host(self):

        device = usb.parse_advanced_query_response(captured("extended_324"))

        self.assertEqual(device["paired_macs"], ["00:1B:13:04:E9:6E"])



    def test_only_the_host_may_carry_more_than_one_peer(self):

        with self.assertRaises(usb.ProtocolError):

            usb.parse_advanced_query_response(

                usb.packet(1, usb.ADVANCED_QUERY_RESPONSE,

                           bytes([1]) + usb.mac_bytes("00:11:22:33:44:55") * 2), 1)





class CapturedHardwareFullConfigurationTests(unittest.TestCase):

    def setUp(self):

        self.parsed = usb.parse_full_configuration(captured("full_config_324"))



    def test_identity_and_network(self):

        self.assertEqual(self.parsed["mac"], "00:1B:13:04:6A:EA")

        self.assertEqual(self.parsed["ip"], "192.168.100.127")

        self.assertEqual(self.parsed["subnet_mask"], "255.255.255.0")

        self.assertEqual(self.parsed["gateway"], "192.168.100.1")

        self.assertEqual(self.parsed["dhcp_server"], "192.168.100.1")



    def test_strings(self):

        self.assertEqual(self.parsed["vendor"], "Atlona USB 2.0 Extender")

        self.assertEqual(self.parsed["product_id"], "USB Over Network")

        self.assertEqual(self.parsed["firmware"], "1.9.4")

        self.assertEqual(self.parsed["secondary_version"], "1.9.4")



    def test_peer_slots_ignore_the_unused_zero_entries(self):

        self.assertEqual(self.parsed["paired_macs"], ["00:1B:13:04:E9:6E"])

        self.assertNotIn("00:00:00:00:00:00", self.parsed["paired_macs"])



    def test_unverified_regions_are_preserved_and_not_interpreted(self):

        unknown = self.parsed["unknown"]

        self.assertEqual(unknown["head"], "010001000000")

        self.assertEqual(unknown["before_network"], "17F9")

        self.assertEqual(unknown["after_network"], "0700089D00010005")

        self.assertEqual(unknown["tail"], "61000580")



    def test_it_agrees_with_basic_device_information(self):

        basic = usb.parse_query_response(captured("basic_324"))

        for field in ("mac", "ip"):

            self.assertEqual(self.parsed[field], basic[field], field)

        self.assertEqual(self.parsed["firmware"], basic["firmware"])

        self.assertEqual(self.parsed["vendor"], basic["vendor"])





class CapturedHardwareLinkStatusTests(unittest.TestCase):

    def test_the_host_reports_both_of_its_peers(self):

        parsed = usb.parse_link_status(captured("link_311"))

        self.assertTrue(parsed["linked"])

        self.assertEqual(parsed["link_state"], "LINKED")

        self.assertEqual(parsed["link_peer_macs"], ["00:1B:13:04:6A:EA", "B8:98:B0:07:85:C7"])



    def test_the_device_reports_its_single_host(self):

        parsed = usb.parse_link_status(captured("link_324"))

        self.assertEqual(parsed["link_peer_macs"], ["00:1B:13:04:E9:6E"])



    def test_the_state_bytes_are_one_per_peer_slot(self):
        """Four captures line up only when bytes [0:7] are a per-peer array."""
        host = usb.parse_link_status(captured("link_311"))          # two peers
        device = usb.parse_link_status(captured("link_324"))        # one peer
        self.assertEqual([e["state"] for e in host["link_states"]], ["LINKED", "LINKED"])
        self.assertEqual([e["state"] for e in device["link_states"]], ["LINKED"])
        self.assertEqual(host["unknown"]["state_bytes"], "01010000000000")
        self.assertEqual(device["unknown"]["state_bytes"], "01000000000000")

    def test_each_state_is_attached_to_the_peer_it_describes(self):
        host = usb.parse_link_status(captured("link_311"))
        self.assertEqual([e["mac"] for e in host["link_states"]], host["link_peer_macs"])

    def test_five_peers_each_report_their_own_state(self):
        parsed = usb.parse_link_status(captured("link_311_five_peers"))
        self.assertEqual(len(parsed["link_peer_macs"]), 5)
        self.assertEqual([e["state"] for e in parsed["link_states"]], ["LINKED"] * 5)
        self.assertEqual(parsed["unknown"]["state_bytes"], "01010101010000")
        self.assertEqual(parsed["link_state"], "LINKED")

    def test_a_not_linked_peer_is_distinguished_within_the_array(self):
        body = bytearray(captured("link_311_five_peers")[10:])
        body[2] = usb.LINK_STATE_NOT_LINKED           # third peer only
        parsed = usb.parse_link_status(usb.packet(1, usb.LINK_STATUS_RESPONSE, bytes(body)), 1)
        states = [e["state"] for e in parsed["link_states"]]
        self.assertEqual(states, ["LINKED", "LINKED", "NOT_LINKED", "LINKED", "LINKED"])
        self.assertEqual(parsed["link_state"], "LINKED", "the endpoint still has live links")

    def test_every_peer_not_linked_reports_not_linked_overall(self):
        body = bytearray(captured("link_311_five_peers")[10:])
        for index in range(5):
            body[index] = usb.LINK_STATE_NOT_LINKED
        parsed = usb.parse_link_status(usb.packet(1, usb.LINK_STATUS_RESPONSE, bytes(body)), 1)
        self.assertEqual(parsed["link_state"], "NOT_LINKED")
        self.assertFalse(parsed["linked"])

    def test_an_unobserved_state_code_is_unknown(self):
        body = bytearray(captured("link_311_five_peers")[10:])
        body[0] = 0x7F
        parsed = usb.parse_link_status(usb.packet(1, usb.LINK_STATUS_RESPONSE, bytes(body)), 1)
        self.assertEqual(parsed["link_states"][0]["state"], "UNKNOWN")
        self.assertEqual(parsed["link_states"][0]["code"], "0x7F")

    def test_link_status_is_not_the_pairing_table(self):

        """Same MACs here, but the two are read and reported separately."""

        link = usb.parse_link_status(captured("link_311"))

        self.assertNotIn("paired_macs", link)

        self.assertIn("link_peer_macs", link)



    def test_raw_is_retained_for_diagnostics(self):

        self.assertTrue(usb.parse_link_status(captured("link_311"))["raw"].startswith("0101"))





class CapturedHardwareTopologyTests(unittest.TestCase):

    def test_an_empty_acknowledgement_means_unavailable_not_offline(self):

        parsed = usb.parse_topology_response(captured("topology_empty"))

        self.assertEqual(parsed["command"], 0x0308)

        self.assertFalse(parsed["supported"])

        self.assertEqual(parsed["raw"], "")



    def test_a_payload_response_is_also_accepted(self):

        """One unit answers 0x0305 with data; both are valid answers."""

        packet = usb.packet(3, 0x0305, bytes.fromhex("1B1A0100078155811A"))

        parsed = usb.parse_topology_response(packet, 3)

        self.assertEqual(parsed["command"], 0x0305)

        self.assertTrue(parsed["supported"])

        self.assertEqual(parsed["raw"], "1B1A0100078155811A")





class CapturedUnlinkedStateTests(unittest.TestCase):
    """A configured peer with no live link, captured from hardware.

    The AT-OMNI-324 at .134 had its configured host powered off, which produced
    the first observation of a link-state byte other than 0x01.
    """

    def test_the_not_linked_flag_is_decoded(self):
        parsed = usb.parse_link_status(captured("link_324_unlinked"))
        self.assertEqual(parsed["link_state"], "NOT_LINKED")
        self.assertFalse(parsed["linked"])
        self.assertEqual(parsed["unknown"]["flag"], "0x02")

    def test_a_not_linked_device_still_reports_its_configured_peer(self):
        """So the peer list is the configured set, not evidence of a live link."""
        parsed = usb.parse_link_status(captured("link_324_unlinked"))
        self.assertEqual(parsed["link_peer_macs"], ["00:1B:13:05:50:50"])

    def test_an_unobserved_flag_is_unknown_rather_than_not_linked(self):
        body = bytearray(captured("link_324_unlinked")[10:])
        body[0] = 0x7F
        parsed = usb.parse_link_status(usb.packet(1, usb.LINK_STATUS_RESPONSE, bytes(body)), 1)
        self.assertEqual(parsed["link_state"], "UNKNOWN",
                         "a value never seen on hardware is not evidence of no link")
        self.assertFalse(parsed["linked"])
        self.assertEqual(parsed["unknown"]["flag"], "0x7F")

    def test_a_third_unit_confirms_the_model_mapping_again(self):
        """Role byte 0x01 with a single host peer, on a different physical 324."""
        ext = usb.parse_advanced_query_response(captured("extended_324_b"))
        self.assertEqual(ext["device_type_code"], 0x01)
        self.assertEqual(ext["device_type"], usb.DEVICE_MODEL)
        self.assertEqual(ext["paired_macs"], ["00:1B:13:05:50:50"])

    def test_the_third_unit_reports_its_own_identity_and_firmware(self):
        basic = usb.parse_query_response(captured("basic_324_b"))
        self.assertEqual(basic["mac"], "00:1B:13:05:17:E9")
        self.assertEqual(basic["ip"], "192.168.100.134")
        self.assertEqual(basic["firmware"], "1.9.4")

    def test_the_product_id_never_selects_a_model(self):
        """Every unit answers the same product id."""
        ids = {usb.parse_query_response(captured(name))["product_id"]
               for name in ("basic_311", "basic_324", "basic_324_b")}
        self.assertEqual(len(ids), 1)


class MalformedPacketTests(unittest.TestCase):

    """Every parser must reject bad input safely and never read past the end."""



    PARSERS = (

        ("query", usb.parse_query_response, usb.QUERY_RESPONSE),

        ("advanced", usb.parse_advanced_query_response, usb.ADVANCED_QUERY_RESPONSE),

        ("full_config", usb.parse_full_configuration, usb.FULL_CONFIG_RESPONSE),

        ("link", usb.parse_link_status, usb.LINK_STATUS_RESPONSE),

        ("topology", usb.parse_topology_response, 0x0305),

    )



    def bodies(self):

        return {

            "query": captured("basic_324")[10:],

            "advanced": captured("extended_311")[10:],

            "full_config": captured("full_config_324")[10:],

            "link": captured("link_311")[10:],

            "topology": b"",

        }



    def test_wrong_magic_is_rejected(self):

        bodies = self.bodies()

        for name, parser, command in self.PARSERS:

            bad = bytes([0xDE, 0xAD, 0xBE, 0xEF]) + usb.packet(1, command, bodies[name])[4:]

            with self.assertRaises(usb.ProtocolError, msg=name):

                parser(bad, 1)



    def test_wrong_response_command_is_rejected(self):

        bodies = self.bodies()

        for name, parser, _command in self.PARSERS:

            with self.assertRaises(usb.ProtocolError, msg=name):

                parser(usb.packet(1, 0x09FF, bodies[name]), 1)



    def test_message_id_mismatch_is_rejected(self):

        bodies = self.bodies()

        for name, parser, command in self.PARSERS:

            with self.assertRaises(usb.ProtocolError, msg=name):

                parser(usb.packet(99, command, bodies[name]), 1)



    def test_a_short_packet_is_rejected(self):

        for name, parser, _command in self.PARSERS:

            with self.assertRaises(usb.ProtocolError, msg=name):

                parser(bytes([0x2F, 0x03, 0xF4, 0xA2, 0x00]), 1)



    def test_a_truncated_body_is_rejected_rather_than_partly_parsed(self):

        bodies = self.bodies()

        for name in ("full_config", "link"):

            parser = dict((n, p) for n, p, _c in self.PARSERS)[name]

            command = dict((n, c) for n, _p, c in self.PARSERS)[name]

            truncated = bodies[name][:4]

            with self.assertRaises(usb.ProtocolError, msg=name):

                parser(usb.packet(1, command, truncated), 1)



    def test_a_truncated_mac_slot_is_never_half_read(self):

        """A trailing partial slot is ignored, not padded into a MAC."""

        body = bytearray(captured("link_311")[10:])

        parsed = usb.parse_link_status(usb.packet(1, usb.LINK_STATUS_RESPONSE, bytes(body[:17])), 1)

        self.assertEqual(parsed["link_peer_macs"], ["00:1B:13:04:6A:EA"])



    def test_an_odd_length_peer_payload_is_rejected(self):

        with self.assertRaises(usb.ProtocolError):

            usb.parse_advanced_query_response(

                usb.packet(1, usb.ADVANCED_QUERY_RESPONSE, bytes([0]) + b"" * 5), 1)



    def test_an_unknown_role_byte_is_rejected(self):

        with self.assertRaises(usb.ProtocolError):

            usb.parse_advanced_query_response(

                usb.packet(1, usb.ADVANCED_QUERY_RESPONSE, bytes([0x07])), 1)



    def test_a_zero_mac_is_treated_as_an_unused_slot(self):

        body = bytearray(captured("full_config_324")[10:])

        body[14:20] = bytes(6)

        parsed = usb.parse_full_configuration(usb.packet(1, usb.FULL_CONFIG_RESPONSE, bytes(body)), 1)

        self.assertEqual(parsed["paired_macs"], [], "an all-zero slot is not a device")



    def test_unexpected_extra_data_does_not_break_a_valid_parse(self):

        body = captured("full_config_324")[10:] + bytes(8)

        parsed = usb.parse_full_configuration(usb.packet(1, usb.FULL_CONFIG_RESPONSE, body), 1)

        self.assertEqual(parsed["mac"], "00:1B:13:04:6A:EA")



    def test_an_oversized_datagram_is_still_bounded(self):

        with self.assertRaises(usb.ProtocolError):

            usb.parse_full_configuration(

                usb.packet(1, usb.FULL_CONFIG_RESPONSE, bytes(usb.MAX_PACKET_LEN + 1)), 1)



    def test_a_truncated_string_does_not_bleed_into_the_next_field(self):
        body = bytearray(captured("full_config_324")[10:])
        body[138:146] = b"9.9.9".ljust(8, bytes([0]))
        parsed = usb.parse_full_configuration(usb.packet(1, usb.FULL_CONFIG_RESPONSE, bytes(body)), 1)
        self.assertEqual(parsed["firmware"], "9.9.9")
        self.assertEqual(parsed["secondary_version"], "1.9.4", "the next slot is untouched")

    def test_an_unterminated_string_stays_inside_its_slot(self):
        body = bytearray(captured("full_config_324")[10:])
        body[138:146] = b"ABCDEFGH"          # fills the slot, no terminator
        parsed = usb.parse_full_configuration(usb.packet(1, usb.FULL_CONFIG_RESPONSE, bytes(body)), 1)
        self.assertEqual(parsed["firmware"], "ABCDEFGH")
        self.assertEqual(parsed["secondary_version"], "1.9.4")


class RangeValidationPerformanceTests(unittest.TestCase):

    """B1: oversized ranges must be rejected arithmetically, before expansion."""



    def assert_fast_rejection(self, ranges, limit=0.5):

        began = time.monotonic()

        with self.assertRaises(usb.ProtocolError):

            usb.parse_ranges(ranges)

        elapsed = time.monotonic() - began

        self.assertLess(elapsed, limit, f"{ranges} took {elapsed:.3f}s; it was likely expanded before validation")



    def test_class_a_cidr_rejected_without_expansion(self):

        self.assert_fast_rejection(["10.0.0.0/8"])



    def test_enormous_dash_range_rejected_without_expansion(self):

        # ~4.2 billion addresses; expanding this at all would exhaust memory.

        self.assert_fast_rejection(["1.0.0.0-254.0.0.0"])



    def test_entire_address_space_rejected_without_expansion(self):

        self.assert_fast_rejection(["0.0.0.0/0"])



    def test_combined_ranges_bounded_before_expansion(self):

        self.assert_fast_rejection([f"10.{octet}.0.0/16" for octet in range(8)])



    def test_combined_small_ranges_exceeding_limit_are_rejected(self):

        with self.assertRaises(usb.ProtocolError):

            usb.parse_ranges(["192.168.1.0/24", "192.168.2.0/24", "192.168.3.0/24",

                              "192.168.4.0/24", "192.168.5.0/24"])



    def test_counting_is_arithmetic_and_immediate(self):

        began = time.monotonic()

        self.assertEqual(usb.count_range_hosts(["10.0.0.0/8"]), 16777214)

        self.assertLess(time.monotonic() - began, 0.5)



    def test_boundary_sizes(self):

        self.assertEqual(len(usb.parse_ranges(["10.0.0.1-10.0.4.0"])), 1024)

        with self.assertRaises(usb.ProtocolError):

            usb.parse_ranges(["10.0.0.1-10.0.4.1"])

        self.assertEqual(usb.parse_ranges(["10.0.0.7/32"]), ["10.0.0.7"])

        self.assertEqual(usb.parse_ranges(["10.0.0.6/31"]), ["10.0.0.6", "10.0.0.7"])



    def test_reversed_range_is_rejected(self):

        with self.assertRaises(usb.ProtocolError):

            usb.parse_ranges(["10.0.0.9-10.0.0.1"])





class ForcePairTests(unittest.TestCase):

    """B4: the Force Pair opcode is not established and must be untransmittable."""



    def test_force_pair_constant_is_unset(self):

        self.assertIsNone(usb.FORCE_PAIR)



    def test_build_force_pair_raises(self):

        with self.assertRaises(usb.ProtocolError):

            usb.build_force_pair(1, HOST_MAC)



    def test_packet_rejects_unset_command(self):

        with self.assertRaises(usb.ProtocolError):

            usb.packet(1, usb.FORCE_PAIR)



    def test_build_pair_always_uses_pair_opcode(self):

        self.assertEqual(int.from_bytes(usb.build_pair(1, HOST_MAC)[8:10], "big"), usb.PAIR)



    def test_pair_opcode_does_not_collide_with_advanced_query_response(self):

        self.assertNotEqual(usb.PAIR, usb.ADVANCED_QUERY_RESPONSE)

        self.assertNotEqual(usb.UNPAIR, usb.ADVANCED_QUERY_RESPONSE)



    def test_specific_unpair_differs_from_unpair_all(self):

        self.assertEqual(int.from_bytes(usb.build_unpair(1, HOST_MAC)[8:10], "big"), usb.UNPAIR)

        self.assertEqual(int.from_bytes(usb.build_unpair(1, None)[8:10], "big"), usb.UNPAIR_ALL)





class StalenessTests(unittest.TestCase):

    """B5: persisted history must never imply the device is online now."""



    def test_fresh_device_is_online(self):

        with tempfile.TemporaryDirectory() as folder:

            service = make_service(folder)

            service._upsert(usb.parse_query_response(query_response()), IFACE_IP, IFACE_MASK, "DIRECT_IP")

            self.assertTrue(service.state()["devices"][0]["online"])

            self.assertFalse(service.state()["devices"][0]["stale"])



    def test_expired_device_becomes_stale(self):

        with tempfile.TemporaryDirectory() as folder:

            clock = [1000.0]

            service = make_service(folder, now=lambda: clock[0], online_ttl=60)

            service._upsert(usb.parse_query_response(query_response()), IFACE_IP, IFACE_MASK, "DIRECT_IP")

            self.assertTrue(service.state()["devices"][0]["online"])

            clock[0] += 61

            device = service.state()["devices"][0]

            self.assertFalse(device["online"])

            self.assertTrue(device["stale"])

            self.assertFalse(device["manageable"])



    def test_persisted_device_is_not_online_after_restart(self):

        with tempfile.TemporaryDirectory() as folder:

            first = make_service(folder)

            first._upsert(usb.parse_query_response(query_response()), IFACE_IP, IFACE_MASK, "DIRECT_IP")

            self.assertTrue(first.state()["devices"][0]["online"])

            first.close()

            second = make_service(folder)

            devices = second.state()["devices"]

            self.assertEqual(len(devices), 1, "identity must survive a restart")

            self.assertEqual(devices[0]["mac"], HOST_MAC)

            self.assertFalse(devices[0]["online"], "persisted history must not count as online")

            self.assertTrue(devices[0]["stale"])

            self.assertTrue(devices[0]["discovered"])



    def test_rediscovery_after_stale_restores_online(self):

        with tempfile.TemporaryDirectory() as folder:

            first = make_service(folder)

            first._upsert(usb.parse_query_response(query_response()), IFACE_IP, IFACE_MASK, "DIRECT_IP")

            first.close()

            second = make_service(folder)

            self.assertFalse(second.state()["devices"][0]["online"])

            second._upsert(usb.parse_query_response(query_response()), IFACE_IP, IFACE_MASK, "LOCAL_BROADCAST")

            self.assertTrue(second.state()["devices"][0]["online"])



    def test_usb_count_semantics_use_online_not_discovered(self):

        with tempfile.TemporaryDirectory() as folder:

            first = make_service(folder)

            first._upsert(usb.parse_query_response(query_response(mac=HOST_MAC, ip="192.168.1.10")), IFACE_IP, IFACE_MASK, "DIRECT_IP")

            first._upsert(usb.parse_query_response(query_response(mac=DEVICE_MAC, ip="192.168.1.20")), IFACE_IP, IFACE_MASK, "DIRECT_IP")

            first.close()

            second = make_service(folder)

            second._upsert(usb.parse_query_response(query_response(mac=HOST_MAC, ip="192.168.1.10")), IFACE_IP, IFACE_MASK, "DIRECT_IP")

            devices = second.state()["devices"]

            self.assertEqual(len([d for d in devices if d["discovered"]]), 2)

            self.assertEqual(len([d for d in devices if d["online"]]), 1)



    def test_pairing_freshness_flag_tracks_advanced_query(self):

        with tempfile.TemporaryDirectory() as folder:

            service = make_service(folder)

            query = usb.parse_query_response(query_response())

            service._upsert(query, IFACE_IP, IFACE_MASK, "DIRECT_IP")

            self.assertFalse(service.state()["devices"][0]["pairing_state_fresh"])

            service._upsert(query, IFACE_IP, IFACE_MASK, "DIRECT_IP",

                            usb.parse_advanced_query_response(advanced_response(1, 0, [DEVICE_MAC])))

            self.assertTrue(service.state()["devices"][0]["pairing_state_fresh"])





class NetworkEligibilityTests(unittest.TestCase):

    """Fail closed when interface context is unknown; never fabricate a /24."""



    def test_unknown_interface_is_not_local(self):

        self.assertEqual(usb.network_relation("", "", "192.168.1.40"), "UNKNOWN")

        self.assertEqual(usb.network_relation("not-an-ip", "255.255.255.0", "192.168.1.40"), "UNKNOWN")



    def test_an_endpoint_without_interface_context_is_still_addressable(self):
        """Unicast is routable, so a known address is enough to manage it.

        The controller's own subnet is not part of the question; identity is
        still protected by MAC verification on every reply.
        """
        with tempfile.TemporaryDirectory() as folder:
            service = make_service(folder)
            device = service._upsert(usb.parse_query_response(query_response()), "", "", "DIRECT_IP")
            self.assertEqual(device["network_relation"], "UNKNOWN")
            self.assertTrue(device["pairing_eligible"])

    def test_an_endpoint_with_no_address_is_not_eligible(self):
        with tempfile.TemporaryDirectory() as folder:
            service = make_service(folder)
            record = usb.parse_query_response(query_response())
            record["ip"] = ""
            device = service._upsert(record, IFACE_IP, IFACE_MASK, "DIRECT_IP")
            self.assertFalse(device["pairing_eligible"], "nothing to address")


    def test_a_routed_endpoint_is_remote_but_still_eligible(self):
        """OFF_NET describes where it sits, not whether it can be used."""
        with tempfile.TemporaryDirectory() as folder:
            service = make_service(folder)
            device = service._upsert(usb.parse_query_response(query_response(ip="10.9.9.9")),
                                     IFACE_IP, IFACE_MASK, "RANGE")
            self.assertEqual(device["network_relation"], "OFF_NET")
            self.assertTrue(device["pairing_eligible"],
                            "a reachable endpoint on another subnet is manageable")


    def test_a_routed_command_is_attempted_rather_than_refused(self):
        """Without a matching local interface the OS routing table selects the
        source, which is what allows a command to reach another subnet."""
        with tempfile.TemporaryDirectory() as folder:
            service = make_service(folder)
            service._upsert(usb.parse_query_response(query_response()), "", "", "DIRECT_IP")
            bound = []

            def capture(destination, request, message_id, timeout=usb.DEFAULT_TIMEOUT, bind_ip=None):
                bound.append(bind_ip)
                raise usb.socket.timeout()

            service._exchange = capture
            self.assertEqual(service.blink(HOST_MAC, True)["status"], "timeout")
            self.assertEqual(bound, [None], "no source is forced when none would match")

    def test_a_local_endpoint_still_binds_to_its_own_interface(self):
        """Multi-NIC correctness is unchanged where a matching interface exists."""
        with tempfile.TemporaryDirectory() as folder:
            service = make_service(folder)
            service._upsert(usb.parse_query_response(query_response(ip=HOST_IP)),
                            IFACE_IP, IFACE_MASK, "DIRECT_IP")
            bound = []

            def capture(destination, request, message_id, timeout=usb.DEFAULT_TIMEOUT, bind_ip=None):
                bound.append(bind_ip)
                raise usb.socket.timeout()

            service._exchange = capture
            service.blink(HOST_MAC, True)
            self.assertEqual(bound, [IFACE_IP], "a matching interface is still used")


    def test_route_state_without_interface_context_is_judged_by_the_read(self):
        """It is attempted over routing; the outcome comes from the device."""
        with tempfile.TemporaryDirectory() as folder:
            service = make_service(folder)
            # Unreachability is stated, not left to a real timeout: without this
            # the test sent live UDP to whatever holds those addresses and took
            # the socket timeout to decide an outcome it already assumes.
            def _unreachable(*args, **kwargs):
                raise usb.socket.timeout()
            service._exchange = _unreachable
            service._upsert(usb.parse_query_response(query_response(mac=HOST_MAC, ip=HOST_IP)),
                            "", "", "DIRECT_IP")
            service._upsert(usb.parse_query_response(query_response(mac=DEVICE_MAC, ip=DEVICE_IP)),
                            "", "", "DIRECT_IP")
            state = service.get_route_state(HOST_MAC, DEVICE_MAC)
            self.assertEqual(state["status"], "UNVERIFIABLE")
            self.assertEqual(state["reason"], "OFFLINE",
                             "unreachable is reported, not a refusal to try")


class SourceBindingTests(unittest.TestCase):

    """B6: commands must leave through the interface the device was found on."""



    def build(self, folder):

        service = make_service(folder)

        network = FakeNetwork().add(HOST_IP, HOST_MAC, 0)

        service._exchange = network.exchange

        service.discover_ip(HOST_IP, IFACE_IP, IFACE_MASK)

        network.binds.clear()

        return service, network



    def test_discovery_binds_to_the_selected_interface(self):

        with tempfile.TemporaryDirectory() as folder:

            service = make_service(folder)

            network = FakeNetwork().add(HOST_IP, HOST_MAC, 0)

            service._exchange = network.exchange

            service.discover_ip(HOST_IP, IFACE_IP, IFACE_MASK)

            self.assertTrue(network.binds)

            self.assertTrue(all(bind == IFACE_IP for bind in network.binds))



    def test_blink_reboot_and_pairing_reads_bind_to_stored_interface(self):

        with tempfile.TemporaryDirectory() as folder:

            service, network = self.build(folder)

            service.blink(HOST_MAC, True)

            service.reboot(HOST_MAC)

            service.read_pairing(HOST_MAC)

            self.assertTrue(network.binds)

            self.assertTrue(all(bind == IFACE_IP for bind in network.binds))



    def test_route_commands_bind_to_the_host_interface(self):

        with tempfile.TemporaryDirectory() as folder:

            service = make_service(folder)

            network = (FakeNetwork()

                       .add(HOST_IP, HOST_MAC, 0, peers=[])

                       .add(DEVICE_IP, DEVICE_MAC, 1, peers=[]))

            service._exchange = network.exchange

            service.discover_ip(HOST_IP, IFACE_IP, IFACE_MASK)

            service.discover_ip(DEVICE_IP, IFACE_IP, IFACE_MASK)

            network.binds.clear()

            service.pair_route(HOST_MAC, DEVICE_MAC)

            self.assertTrue(network.binds)

            self.assertTrue(all(bind == IFACE_IP for bind in network.binds))



    def test_unavailable_interface_is_reported_not_silently_rerouted(self):

        with tempfile.TemporaryDirectory() as folder:

            service, _network = self.build(folder)

            def refuse(*_args, **_kwargs):

                raise usb.InterfaceError("interface gone")

            service._exchange = refuse

            self.assertEqual(service.blink(HOST_MAC, True)["status"], "interface_unavailable")

            self.assertEqual(service.reboot(HOST_MAC)["status"], "interface_unavailable")

            self.assertEqual(service.read_pairing(HOST_MAC)["status"], "INTERFACE_UNAVAILABLE")

            self.assertEqual(service.discover_ip(HOST_IP, IFACE_IP, IFACE_MASK)["status"], "interface_unavailable")





class WrongMacTests(unittest.TestCase):

    """B2: a different device answering at a remembered address must not be used."""



    def seeded(self, folder):

        service = make_service(folder)

        network = (FakeNetwork()

                   .add(HOST_IP, HOST_MAC, 0, peers=[])

                   .add(DEVICE_IP, DEVICE_MAC, 1, peers=[]))

        service._exchange = network.exchange

        service.discover_ip(HOST_IP, IFACE_IP, IFACE_MASK)

        service.discover_ip(DEVICE_IP, IFACE_IP, IFACE_MASK)

        return service, network



    def test_targeted_refresh_reports_mac_mismatch(self):

        with tempfile.TemporaryDirectory() as folder:

            service, network = self.seeded(folder)

            network.add(HOST_IP, IMPOSTOR_MAC, 0)

            result = service.discover_ip(HOST_IP, IFACE_IP, IFACE_MASK, "DIRECT_IP", HOST_MAC)

            self.assertEqual(result["status"], "mac_mismatch")

            self.assertEqual(result["actual_mac"], IMPOSTOR_MAC)



    def test_expected_record_is_not_revived_by_another_device(self):

        with tempfile.TemporaryDirectory() as folder:

            clock = [1000.0]

            service = make_service(folder, now=lambda: clock[0], online_ttl=60)

            network = FakeNetwork().add(HOST_IP, HOST_MAC, 0, peers=[])

            service._exchange = network.exchange

            service.discover_ip(HOST_IP, IFACE_IP, IFACE_MASK)

            seen_before = service.device(HOST_MAC)["last_seen"]

            clock[0] += 61

            network.add(HOST_IP, IMPOSTOR_MAC, 0)

            service.discover_ip(HOST_IP, IFACE_IP, IFACE_MASK, "DIRECT_IP", HOST_MAC)

            expected = service.device(HOST_MAC)

            self.assertEqual(expected["last_seen"], seen_before, "expected record must not be marked seen")

            self.assertFalse(expected["online"])



    def test_responder_is_still_discovered_as_itself(self):

        with tempfile.TemporaryDirectory() as folder:

            service, network = self.seeded(folder)

            network.add(HOST_IP, IMPOSTOR_MAC, 0)

            service.discover_ip(HOST_IP, IFACE_IP, IFACE_MASK, "DIRECT_IP", HOST_MAC)

            self.assertIsNotNone(service.device(IMPOSTOR_MAC))

            self.assertTrue(service.device(IMPOSTOR_MAC)["online"])



    def test_wrong_mac_at_old_311_ip_blocks_route_state(self):

        with tempfile.TemporaryDirectory() as folder:

            service, network = self.seeded(folder)

            network.add(HOST_IP, IMPOSTOR_MAC, 0, peers=[])

            state = service.get_route_state(HOST_MAC, DEVICE_MAC)

            self.assertEqual(state["status"], "UNVERIFIABLE")

            self.assertEqual(state["endpoint"], "host")

            self.assertEqual(state["reason"], "MAC_MISMATCH")



    def test_wrong_mac_at_old_324_ip_blocks_route_state(self):

        with tempfile.TemporaryDirectory() as folder:

            service, network = self.seeded(folder)

            network.add(DEVICE_IP, IMPOSTOR_MAC, 1, peers=[])

            state = service.get_route_state(HOST_MAC, DEVICE_MAC)

            self.assertEqual(state["status"], "UNVERIFIABLE")

            self.assertEqual(state["endpoint"], "device")

            self.assertEqual(state["reason"], "MAC_MISMATCH")



    def test_no_pair_command_is_sent_to_the_wrong_device(self):

        with tempfile.TemporaryDirectory() as folder:

            service, network = self.seeded(folder)

            network.add(HOST_IP, IMPOSTOR_MAC, 0, peers=[])

            result = service.pair_route(HOST_MAC, DEVICE_MAC)

            self.assertEqual(result["status"], "UNVERIFIABLE")

            self.assertEqual(network.commands(), [], "no command may be addressed to the impostor")



    def test_network_verification_requires_matching_mac(self):

        with tempfile.TemporaryDirectory() as folder:

            service, network = self.seeded(folder)

            network.add("192.168.1.255", HOST_MAC, 0)              # broadcast target ACKs

            network.add("192.168.1.44", IMPOSTOR_MAC, 0)           # but a different unit holds the new address

            result = service.configure_network(HOST_MAC, "static", IFACE_IP, IFACE_MASK,

                                               "192.168.1.44", "255.255.255.0", "192.168.1.1")

            self.assertEqual(result["status"], "command_accepted_unverified")

            self.assertFalse(result["rediscovery_verified"])

            self.assertEqual(result["verification_status"], "mac_mismatch")





class ReadPairingTests(unittest.TestCase):

    """B7 / freshness: pairing state must come from this operation, not the cache."""



    def seeded(self, folder, host_peers=(), device_peers=()):

        service = make_service(folder)

        network = (FakeNetwork()

                   .add(HOST_IP, HOST_MAC, 0, peers=host_peers)

                   .add(DEVICE_IP, DEVICE_MAC, 1, peers=device_peers))

        service._exchange = network.exchange

        service.discover_ip(HOST_IP, IFACE_IP, IFACE_MASK)

        service.discover_ip(DEVICE_IP, IFACE_IP, IFACE_MASK)

        return service, network



    def test_successful_read_is_authoritative(self):

        with tempfile.TemporaryDirectory() as folder:

            service, _network = self.seeded(folder, host_peers=[DEVICE_MAC], device_peers=[HOST_MAC])

            read = service.read_pairing(HOST_MAC)

            self.assertEqual(read["status"], "OK")

            self.assertTrue(read["authoritative"])

            self.assertEqual(read["paired_macs"], [DEVICE_MAC])



    def test_advanced_query_timeout_never_returns_cached_pairing(self):

        with tempfile.TemporaryDirectory() as folder:

            service, network = self.seeded(folder, host_peers=[DEVICE_MAC], device_peers=[HOST_MAC])

            self.assertEqual(service.device(HOST_MAC)["paired_macs"], [DEVICE_MAC])

            network.add(HOST_IP, HOST_MAC, 0, peers=[DEVICE_MAC], advanced=False)

            read = service.read_pairing(HOST_MAC)

            self.assertEqual(read["status"], "ADVANCED_QUERY_FAILED")

            self.assertNotIn("paired_macs", read)



    def test_malformed_advanced_query_fails_closed(self):

        with tempfile.TemporaryDirectory() as folder:

            service, network = self.seeded(folder, host_peers=[DEVICE_MAC], device_peers=[HOST_MAC])

            network.add(HOST_IP, HOST_MAC, 0, advanced="malformed")

            read = service.read_pairing(HOST_MAC)

            self.assertEqual(read["status"], "ADVANCED_QUERY_FAILED")

            self.assertEqual(read["reason"], "protocol_error")



    def test_over_limit_peer_payload_is_surfaced_not_substituted(self):

        with tempfile.TemporaryDirectory() as folder:

            service, network = self.seeded(folder, host_peers=[DEVICE_MAC], device_peers=[HOST_MAC])

            too_many = [f"00:11:22:33:44:{index:02X}" for index in range(usb.HOST_PEER_LIMIT + 1)]

            network.add(HOST_IP, HOST_MAC, 0, peers=too_many)

            read = service.read_pairing(HOST_MAC)

            self.assertEqual(read["status"], "ADVANCED_QUERY_FAILED")

            self.assertEqual(read["reason"], "protocol_error")



    def test_offline_device_reports_offline(self):

        with tempfile.TemporaryDirectory() as folder:

            service, network = self.seeded(folder)

            network.add(HOST_IP, HOST_MAC, 0, query=False)

            self.assertEqual(service.read_pairing(HOST_MAC)["status"], "OFFLINE")



    def test_unknown_mac_is_not_found(self):

        with tempfile.TemporaryDirectory() as folder:

            service, _network = self.seeded(folder)

            self.assertEqual(service.read_pairing("00:00:00:00:00:99")["status"], "NOT_FOUND")

            self.assertEqual(service.read_pairing("nonsense")["status"], "NOT_FOUND")





class RouteStateTests(unittest.TestCase):

    def build(self, folder, host_peers=(), device_peers=(), **overrides):

        service = make_service(folder)

        network = (FakeNetwork()

                   .add(HOST_IP, HOST_MAC, 0, peers=host_peers, **overrides.get("host", {}))

                   .add(DEVICE_IP, DEVICE_MAC, 1, peers=device_peers, **overrides.get("device", {})))

        service._exchange = network.exchange

        service.discover_ip(HOST_IP, IFACE_IP, IFACE_MASK)

        service.discover_ip(DEVICE_IP, IFACE_IP, IFACE_MASK)

        return service, network



    def test_fresh_verified_routed_state(self):

        with tempfile.TemporaryDirectory() as folder:

            service, _network = self.build(folder, [DEVICE_MAC], [HOST_MAC])

            state = service.get_route_state(HOST_MAC, DEVICE_MAC)

            self.assertEqual(state["status"], "OK")

            self.assertTrue(state["routed"])

            self.assertEqual(state["owner"], HOST_MAC)



    def test_fresh_verified_unrouted_state(self):

        with tempfile.TemporaryDirectory() as folder:

            service, _network = self.build(folder, [], [])

            state = service.get_route_state(HOST_MAC, DEVICE_MAC)

            self.assertEqual(state["status"], "OK")

            self.assertFalse(state["routed"])

            self.assertIsNone(state["owner"])



    def test_advanced_query_failure_on_311_is_unverifiable(self):

        with tempfile.TemporaryDirectory() as folder:

            service, network = self.build(folder, [DEVICE_MAC], [HOST_MAC])

            network.add(HOST_IP, HOST_MAC, 0, peers=[DEVICE_MAC], advanced=False)

            state = service.get_route_state(HOST_MAC, DEVICE_MAC)

            self.assertEqual(state["status"], "UNVERIFIABLE")

            self.assertEqual(state["endpoint"], "host")

            self.assertNotIn("routed", state)



    def test_advanced_query_failure_on_324_is_unverifiable(self):

        with tempfile.TemporaryDirectory() as folder:

            service, network = self.build(folder, [DEVICE_MAC], [HOST_MAC])

            network.add(DEVICE_IP, DEVICE_MAC, 1, peers=[HOST_MAC], advanced=False)

            state = service.get_route_state(HOST_MAC, DEVICE_MAC)

            self.assertEqual(state["status"], "UNVERIFIABLE")

            self.assertEqual(state["endpoint"], "device")

            self.assertNotIn("routed", state)



    def test_stale_cached_routed_state_is_never_reported_as_routed(self):

        """The exact regression: cache says routed, hardware can no longer be read."""

        with tempfile.TemporaryDirectory() as folder:

            service, network = self.build(folder, [DEVICE_MAC], [HOST_MAC])

            self.assertEqual(service.device(HOST_MAC)["paired_macs"], [DEVICE_MAC])

            network.add(HOST_IP, HOST_MAC, 0, peers=[DEVICE_MAC], advanced=False)

            network.add(DEVICE_IP, DEVICE_MAC, 1, peers=[HOST_MAC], advanced=False)

            state = service.get_route_state(HOST_MAC, DEVICE_MAC)

            self.assertEqual(state["status"], "UNVERIFIABLE")

            self.assertNotIn("routed", state)



    def test_stale_cached_unrouted_state_is_never_reported_as_unrouted(self):

        with tempfile.TemporaryDirectory() as folder:

            service, network = self.build(folder, [], [])

            network.add(HOST_IP, HOST_MAC, 0, advanced=False)

            network.add(DEVICE_IP, DEVICE_MAC, 1, advanced=False)

            state = service.get_route_state(HOST_MAC, DEVICE_MAC)

            self.assertEqual(state["status"], "UNVERIFIABLE")

            self.assertNotIn("routed", state)



    def test_one_endpoint_paired_other_not_is_inconsistent(self):

        with tempfile.TemporaryDirectory() as folder:

            service, _network = self.build(folder, [DEVICE_MAC], [])

            state = service.get_route_state(HOST_MAC, DEVICE_MAC)

            self.assertEqual(state["status"], "INCONSISTENT")

            self.assertTrue(state["host_lists_device"])

            self.assertFalse(state["device_claims_host"])



    def test_device_claims_host_but_host_does_not_is_inconsistent(self):

        with tempfile.TemporaryDirectory() as folder:

            service, _network = self.build(folder, [], [HOST_MAC])

            self.assertEqual(service.get_route_state(HOST_MAC, DEVICE_MAC)["status"], "INCONSISTENT")



    def test_wrong_device_type_is_protocol_error(self):

        with tempfile.TemporaryDirectory() as folder:

            service, network = self.build(folder, [], [])

            network.add(DEVICE_IP, DEVICE_MAC, 0, peers=[])

            self.assertEqual(service.get_route_state(HOST_MAC, DEVICE_MAC)["status"], "PROTOCOL_ERROR")



    def test_off_net_endpoint_is_not_eligible(self):

        with tempfile.TemporaryDirectory() as folder:

            service = make_service(folder)

            network = (FakeNetwork()

                       .add(HOST_IP, HOST_MAC, 0, peers=[])

                       .add("10.9.9.9", DEVICE_MAC, 1, peers=[]))

            service._exchange = network.exchange

            service.discover_ip(HOST_IP, IFACE_IP, IFACE_MASK)

            service.discover_ip("10.9.9.9", IFACE_IP, IFACE_MASK)

            self.assertEqual(service.get_route_state(HOST_MAC, DEVICE_MAC)["status"], "NOT_ELIGIBLE")





class PairRouteTests(RouteStateTests):

    def test_pair_ack_with_both_endpoints_verifying(self):

        with tempfile.TemporaryDirectory() as folder:

            service, network = self.build(folder, [], [])

            def exchange(destination, request, message_id, timeout=usb.DEFAULT_TIMEOUT, bind_ip=None):

                command = int.from_bytes(request[8:10], "big")

                if command == usb.PAIR:

                    network.add(HOST_IP, HOST_MAC, 0, peers=[DEVICE_MAC])

                    network.add(DEVICE_IP, DEVICE_MAC, 1, peers=[HOST_MAC])

                return network.exchange(destination, request, message_id, timeout, bind_ip)

            service._exchange = exchange

            self.assertEqual(service.pair_route(HOST_MAC, DEVICE_MAC)["status"], "VERIFIED_SUCCESS")



    def test_pair_ack_but_only_311_verifies(self):

        with tempfile.TemporaryDirectory() as folder:

            service, network = self.build(folder, [], [])

            def exchange(destination, request, message_id, timeout=usb.DEFAULT_TIMEOUT, bind_ip=None):

                if int.from_bytes(request[8:10], "big") == usb.PAIR:

                    network.add(HOST_IP, HOST_MAC, 0, peers=[DEVICE_MAC])

                return network.exchange(destination, request, message_id, timeout, bind_ip)

            service._exchange = exchange

            result = service.pair_route(HOST_MAC, DEVICE_MAC)

            self.assertEqual(result["status"], "COMMAND_ACCEPTED_UNVERIFIED")

            self.assertEqual(result["reason"], "INCONSISTENT")



    def test_pair_ack_but_only_324_verifies(self):

        with tempfile.TemporaryDirectory() as folder:

            service, network = self.build(folder, [], [])

            def exchange(destination, request, message_id, timeout=usb.DEFAULT_TIMEOUT, bind_ip=None):

                if int.from_bytes(request[8:10], "big") == usb.PAIR:

                    network.add(DEVICE_IP, DEVICE_MAC, 1, peers=[HOST_MAC])

                return network.exchange(destination, request, message_id, timeout, bind_ip)

            service._exchange = exchange

            result = service.pair_route(HOST_MAC, DEVICE_MAC)

            self.assertEqual(result["status"], "COMMAND_ACCEPTED_UNVERIFIED")

            self.assertEqual(result["reason"], "INCONSISTENT")



    def test_pair_ack_but_neither_endpoint_verifies(self):

        """ACK alone must never be reported as success."""

        with tempfile.TemporaryDirectory() as folder:

            service, _network = self.build(folder, [], [])

            self.assertEqual(service.pair_route(HOST_MAC, DEVICE_MAC)["status"], "VERIFICATION_FAILED")



    def test_pair_ack_but_readback_unavailable_is_unverified(self):

        with tempfile.TemporaryDirectory() as folder:

            service, network = self.build(folder, [], [])

            def exchange(destination, request, message_id, timeout=usb.DEFAULT_TIMEOUT, bind_ip=None):

                if int.from_bytes(request[8:10], "big") == usb.PAIR:

                    network.add(HOST_IP, HOST_MAC, 0, peers=[DEVICE_MAC], advanced=False)

                return network.exchange(destination, request, message_id, timeout, bind_ip)

            service._exchange = exchange

            result = service.pair_route(HOST_MAC, DEVICE_MAC)

            self.assertEqual(result["status"], "COMMAND_ACCEPTED_UNVERIFIED")

            self.assertEqual(result["reason"], "UNVERIFIABLE")



    def test_pair_nack_is_rejected(self):

        with tempfile.TemporaryDirectory() as folder:

            service, network = self.build(folder, [], [])

            network.add(HOST_IP, HOST_MAC, 0, peers=[], ack=False)

            self.assertEqual(service.pair_route(HOST_MAC, DEVICE_MAC)["status"], "REJECTED")



    def test_pair_timeout(self):

        with tempfile.TemporaryDirectory() as folder:

            service, network = self.build(folder, [], [])

            network.add(HOST_IP, HOST_MAC, 0, peers=[], ack=None)

            self.assertEqual(service.pair_route(HOST_MAC, DEVICE_MAC)["status"], "COMMAND_TIMEOUT")



    def test_already_routed_requires_fresh_verification(self):

        with tempfile.TemporaryDirectory() as folder:

            service, network = self.build(folder, [DEVICE_MAC], [HOST_MAC])

            self.assertEqual(service.pair_route(HOST_MAC, DEVICE_MAC)["status"], "ALREADY_ROUTED")

            # Same cache, but the endpoints can no longer be read: no claim allowed.

            network.add(HOST_IP, HOST_MAC, 0, peers=[DEVICE_MAC], advanced=False)

            network.add(DEVICE_IP, DEVICE_MAC, 1, peers=[HOST_MAC], advanced=False)

            self.assertEqual(service.pair_route(HOST_MAC, DEVICE_MAC)["status"], "UNVERIFIABLE")



    def test_peer_limit_uses_fresh_data(self):

        with tempfile.TemporaryDirectory() as folder:

            peers = [f"00:11:22:33:44:{index:02X}" for index in range(usb.HOST_PEER_LIMIT)]

            service, _network = self.build(folder, peers, [])

            result = service.pair_route(HOST_MAC, DEVICE_MAC)

            self.assertEqual(result["status"], "PEER_LIMIT")

            self.assertEqual(result["peer_limit"], usb.HOST_PEER_LIMIT)



    def test_an_unreleasable_owner_is_not_taken_over(self):
        """The owner is not a known endpoint, so it cannot be released and the
        REX is left exactly where it is."""
        with tempfile.TemporaryDirectory() as folder:
            service, _network = self.build(folder, [], [OTHER_HOST_MAC])
            result = service.pair_route(HOST_MAC, DEVICE_MAC)
            self.assertEqual(result["status"], "REASSIGN_RELEASE_FAILED")
            self.assertEqual(result["owner_mac"], usb.normalize_mac(OTHER_HOST_MAC))

    def test_pair_never_transmits_force_pair(self):

        with tempfile.TemporaryDirectory() as folder:

            service, network = self.build(folder, [], [])

            service.pair_route(HOST_MAC, DEVICE_MAC)

            opcodes = [command for _destination, command in network.commands()]

            self.assertIn(usb.PAIR, opcodes)

            self.assertNotIn(usb.ADVANCED_QUERY_RESPONSE, opcodes, "0x0301 must never be transmitted as Force Pair")

            self.assertNotIn(usb.UNPAIR_ALL, opcodes)



    def test_pair_is_sent_to_both_endpoints_with_the_peer_mac(self):

        """HARDWARE VALIDATED: Pair is per-endpoint, so both sides are addressed."""

        with tempfile.TemporaryDirectory() as folder:

            service, network = self.build(folder, [], [])

            service.pair_route(HOST_MAC, DEVICE_MAC)

            pair_frames = [(dest, payload) for dest, command, payload, _bind in network.sent if command == usb.PAIR]

            self.assertEqual(len(pair_frames), 2, "both endpoints must receive Pair")

            by_dest = {dest: usb.normalize_mac(payload) for dest, payload in pair_frames}

            self.assertEqual(by_dest[HOST_IP], DEVICE_MAC, "the 311 is told about the 324")

            self.assertEqual(by_dest[DEVICE_IP], HOST_MAC, "the 324 is told about the 311")





class UnpairRouteTests(RouteStateTests):

    def test_unpair_ack_with_both_endpoints_verifying_removal(self):

        with tempfile.TemporaryDirectory() as folder:

            service, network = self.build(folder, [DEVICE_MAC], [HOST_MAC])

            def exchange(destination, request, message_id, timeout=usb.DEFAULT_TIMEOUT, bind_ip=None):

                if int.from_bytes(request[8:10], "big") == usb.UNPAIR:

                    network.add(HOST_IP, HOST_MAC, 0, peers=[])

                    network.add(DEVICE_IP, DEVICE_MAC, 1, peers=[])

                return network.exchange(destination, request, message_id, timeout, bind_ip)

            service._exchange = exchange

            self.assertEqual(service.unpair_route(HOST_MAC, DEVICE_MAC)["status"], "VERIFIED_SUCCESS")



    def test_unpair_ack_with_inconsistent_readback(self):

        with tempfile.TemporaryDirectory() as folder:

            service, network = self.build(folder, [DEVICE_MAC], [HOST_MAC])

            def exchange(destination, request, message_id, timeout=usb.DEFAULT_TIMEOUT, bind_ip=None):

                if int.from_bytes(request[8:10], "big") == usb.UNPAIR:

                    network.add(HOST_IP, HOST_MAC, 0, peers=[])

                return network.exchange(destination, request, message_id, timeout, bind_ip)

            service._exchange = exchange

            result = service.unpair_route(HOST_MAC, DEVICE_MAC)

            self.assertEqual(result["status"], "COMMAND_ACCEPTED_UNVERIFIED")

            self.assertEqual(result["reason"], "INCONSISTENT")



    def test_unpair_ack_without_removal_is_verification_failed(self):

        with tempfile.TemporaryDirectory() as folder:

            service, _network = self.build(folder, [DEVICE_MAC], [HOST_MAC])

            self.assertEqual(service.unpair_route(HOST_MAC, DEVICE_MAC)["status"], "VERIFICATION_FAILED")



    def test_unpair_ack_but_readback_unavailable_is_unverified(self):

        with tempfile.TemporaryDirectory() as folder:

            service, network = self.build(folder, [DEVICE_MAC], [HOST_MAC])

            def exchange(destination, request, message_id, timeout=usb.DEFAULT_TIMEOUT, bind_ip=None):

                if int.from_bytes(request[8:10], "big") == usb.UNPAIR:

                    network.add(HOST_IP, HOST_MAC, 0, peers=[], advanced=False)

                return network.exchange(destination, request, message_id, timeout, bind_ip)

            service._exchange = exchange

            result = service.unpair_route(HOST_MAC, DEVICE_MAC)

            self.assertEqual(result["status"], "COMMAND_ACCEPTED_UNVERIFIED")

            self.assertEqual(result["reason"], "UNVERIFIABLE")



    def test_unpair_nack_is_rejected(self):

        with tempfile.TemporaryDirectory() as folder:

            service, network = self.build(folder, [DEVICE_MAC], [HOST_MAC])

            network.add(HOST_IP, HOST_MAC, 0, peers=[DEVICE_MAC], ack=False)

            self.assertEqual(service.unpair_route(HOST_MAC, DEVICE_MAC)["status"], "REJECTED")



    def test_unpair_timeout(self):

        with tempfile.TemporaryDirectory() as folder:

            service, network = self.build(folder, [DEVICE_MAC], [HOST_MAC])

            network.add(HOST_IP, HOST_MAC, 0, peers=[DEVICE_MAC], ack=None)

            self.assertEqual(service.unpair_route(HOST_MAC, DEVICE_MAC)["status"], "COMMAND_TIMEOUT")



    def test_already_unrouted_requires_fresh_verification(self):

        with tempfile.TemporaryDirectory() as folder:

            service, network = self.build(folder, [], [])

            self.assertEqual(service.unpair_route(HOST_MAC, DEVICE_MAC)["status"], "ALREADY_UNROUTED")

            network.add(HOST_IP, HOST_MAC, 0, peers=[], advanced=False)

            network.add(DEVICE_IP, DEVICE_MAC, 1, peers=[], advanced=False)

            self.assertEqual(service.unpair_route(HOST_MAC, DEVICE_MAC)["status"], "UNVERIFIABLE")



    def test_unpair_never_transmits_unpair_all(self):

        with tempfile.TemporaryDirectory() as folder:

            service, network = self.build(folder, [DEVICE_MAC], [HOST_MAC])

            service.unpair_route(HOST_MAC, DEVICE_MAC)

            frames = [(dest, command, payload) for dest, command, payload, _bind in network.sent

                      if command in (usb.UNPAIR, usb.UNPAIR_ALL)]

            self.assertEqual(len(frames), 2, "both endpoints must be cleared")

            self.assertTrue(all(command == usb.UNPAIR for _d, command, _p in frames), "never Unpair All")

            by_dest = {dest: usb.normalize_mac(payload) for dest, _c, payload in frames}

            self.assertEqual(by_dest[HOST_IP], DEVICE_MAC)

            self.assertEqual(by_dest[DEVICE_IP], HOST_MAC)





class RouteConcurrencyTests(unittest.TestCase):

    def test_route_transactions_are_serialized(self):

        with tempfile.TemporaryDirectory() as folder:

            service = make_service(folder)

            network = (FakeNetwork()

                       .add(HOST_IP, HOST_MAC, 0, peers=[])

                       .add(DEVICE_IP, DEVICE_MAC, 1, peers=[]))

            service._exchange = network.exchange

            service.discover_ip(HOST_IP, IFACE_IP, IFACE_MASK)

            service.discover_ip(DEVICE_IP, IFACE_IP, IFACE_MASK)

            overlaps, depth, guard = [], [0], threading.Lock()



            original = service.get_route_state

            def traced(*args):

                with guard:

                    depth[0] += 1

                    overlaps.append(depth[0])

                try:

                    time.sleep(0.01)

                    return original(*args)

                finally:

                    with guard:

                        depth[0] -= 1

            service.get_route_state = traced

            threads = [threading.Thread(target=service.pair_route, args=(HOST_MAC, DEVICE_MAC)) for _ in range(4)]

            for thread in threads: thread.start()

            for thread in threads: thread.join()

            self.assertTrue(overlaps)

            self.assertEqual(max(overlaps), 1, "route transactions must not interleave")





class RangeScanTests(unittest.TestCase):

    def test_range_scan_starts_without_waiting_for_slow_work(self):

        with tempfile.TemporaryDirectory() as folder:

            service = make_service(folder)

            started = []

            def slow_scan(*_args):

                started.append(True); time.sleep(0.2); return []

            service._range_scan = slow_scan

            began = time.monotonic()

            future = service.start_range_scan(IFACE_IP, IFACE_MASK, ["192.168.1.1"])

            self.assertLess(time.monotonic() - began, 0.1)

            self.assertEqual(future.result(timeout=2), [])

            self.assertTrue(started)



    def test_overlapping_range_scans_are_rejected_not_queued(self):

        with tempfile.TemporaryDirectory() as folder:

            service = make_service(folder)

            release = threading.Event()

            service._range_scan = lambda *_args: release.wait(2) or []

            first = service.start_range_scan(IFACE_IP, IFACE_MASK, ["192.168.1.1"])

            for _ in range(5):

                with self.assertRaises(usb.ScanBusyError):

                    service.start_range_scan(IFACE_IP, IFACE_MASK, ["192.168.1.2"])

            release.set()

            first.result(timeout=2)

            service.start_range_scan(IFACE_IP, IFACE_MASK, ["192.168.1.3"]).result(timeout=2)



    def test_prepared_targets_are_not_reparsed(self):

        with tempfile.TemporaryDirectory() as folder:

            service = make_service(folder)

            seen = []

            service._range_scan = lambda targets, *_args: seen.append(list(targets)) or []

            service.start_range_scan(IFACE_IP, IFACE_MASK, targets=["10.0.0.1", "10.0.0.2"]).result(timeout=2)

            self.assertEqual(seen, [["10.0.0.1", "10.0.0.2"]])



    def test_range_scan_does_not_block_enrichment_pool(self):

        with tempfile.TemporaryDirectory() as folder:

            service = make_service(folder)

            release, enriched = threading.Event(), threading.Event()

            service._range_scan = lambda *_args: release.wait(2) or []

            service.start_range_scan(IFACE_IP, IFACE_MASK, ["192.168.1.1"])

            service._executor.submit(enriched.set)

            self.assertTrue(enriched.wait(1), "enrichment must not queue behind a range scan")

            release.set()



    def test_set_ranges_validates_before_storing(self):

        with tempfile.TemporaryDirectory() as folder:

            service = make_service(folder)

            with self.assertRaises(usb.ProtocolError):

                service.set_ranges(["10.0.0.0/8"])

            self.assertEqual(service.state()["ranges"], [])

            self.assertEqual(service.set_ranges(["192.168.1.0/24"]), ["192.168.1.0/24"])





class PersistenceTests(unittest.TestCase):

    def test_debounced_persistence_batches_writes(self):

        with tempfile.TemporaryDirectory() as folder:

            service = make_service(folder, persist_debounce=5)

            writes = []

            original = service._flush

            service._flush = lambda: (writes.append(1), original())[1]

            for index in range(20):

                service._upsert(usb.parse_query_response(query_response(mac=f"00:11:22:33:44:{index:02X}", ip="192.168.1.40")),

                                IFACE_IP, IFACE_MASK, "RANGE")

            self.assertEqual(writes, [], "upserts must not each rewrite the state file")

            service.close()



    def test_state_survives_close(self):

        with tempfile.TemporaryDirectory() as folder:

            service = make_service(folder, persist_debounce=5)

            service._upsert(usb.parse_query_response(query_response()), IFACE_IP, IFACE_MASK, "DIRECT_IP")

            service.close()

            self.assertEqual(make_service(folder).state()["devices"][0]["mac"], HOST_MAC)



    def test_same_mac_replaces_current_ip(self):

        with tempfile.TemporaryDirectory() as folder:

            service = make_service(folder)

            service._upsert(usb.parse_query_response(query_response(ip="192.168.1.40")), IFACE_IP, IFACE_MASK, "DIRECT_IP")

            service._upsert(usb.parse_query_response(query_response(ip="192.168.1.83")), IFACE_IP, IFACE_MASK, "DIRECT_IP")

            devices = service.state()["devices"]

            self.assertEqual(len(devices), 1)

            self.assertEqual(devices[0]["ip"], "192.168.1.83")





class DeviceOperationTests(unittest.TestCase):

    def test_dhcp_static_packets_and_static_validation(self):

        self.assertEqual(usb.build_ip_dhcp(3, HOST_MAC).hex(), "2f03f4a2000000030306001b130281e0")

        self.assertEqual(usb.build_ip_static(3, HOST_MAC, "192.168.1.44", "255.255.255.0", "192.168.1.1").hex(),

                         "2f03f4a2000000030307001b130281e0c0a8012cffffff00c0a80101")

        self.assertEqual(usb.validate_static_network("192.168.1.44", "255.255.255.0", "192.168.1.1"),

                         ("192.168.1.44", "255.255.255.0", "192.168.1.1"))

        for values in (("192.168.1.44", "255.0.255.0", "192.168.1.1"), ("192.168.1.0", "255.255.255.0", "192.168.1.1"),

                       ("192.168.1.44", "255.255.255.0", "192.168.2.1")):

            with self.assertRaises(usb.ProtocolError): usb.validate_static_network(*values)



    def test_network_change_uses_ack_and_mac_verified_rediscovery(self):

        with tempfile.TemporaryDirectory() as folder:

            service = make_service(folder)

            network = FakeNetwork().add("192.168.100.25", HOST_MAC, 0)

            service._exchange = network.exchange

            service.discover_ip("192.168.100.25", IFACE_IP, IFACE_MASK)

            network.add("192.168.1.255", HOST_MAC, 0)   # broadcast target ACKs

            network.add("192.168.1.44", HOST_MAC, 0)    # device answers on its new address

            result = service.configure_network(HOST_MAC, "static", IFACE_IP, IFACE_MASK,

                                               "192.168.1.44", "255.255.255.0", "192.168.1.1")

            self.assertEqual(result["status"], "rediscovery_verified")

            self.assertEqual(service.device(HOST_MAC)["ip"], "192.168.1.44")

            self.assertIn(("192.168.1.255", usb.IP_STATIC), network.commands())



    def test_network_command_binds_to_the_selected_interface(self):

        with tempfile.TemporaryDirectory() as folder:

            service = make_service(folder)

            network = FakeNetwork().add("192.168.1.40", HOST_MAC, 0).add("192.168.1.255", HOST_MAC, 0)

            service._exchange = network.exchange

            service.discover_ip("192.168.1.40", IFACE_IP, IFACE_MASK)

            service.configure_network(HOST_MAC, "dhcp", IFACE_IP, IFACE_MASK)

            self.assertTrue(all(bind == IFACE_IP for bind in network.binds))



    def test_timeout_blink_identify_and_reboot(self):

        with tempfile.TemporaryDirectory() as folder:

            service = make_service(folder)

            device = usb.parse_query_response(query_response())

            service._upsert(device, "10.0.1.1", "255.255.255.0", "DIRECT_IP")

            service._exchange = lambda *_args, **_kwargs: (_ for _ in ()).throw(socket.timeout())

            self.assertEqual(service.command("10.0.1.119", usb.build_query(1), 1)["error"], "timeout")

            calls = []

            service.command = lambda _ip, request, _id, bind_ip=None: (calls.append(int.from_bytes(request[8:10], "big")), {"ok": True})[1]

            self.assertTrue(service.blink(device["mac"], True)["command_accepted"])

            self.assertTrue(service.reboot(device["mac"])["command_accepted"])

            self.assertEqual(calls, [usb.BLINK_ON, usb.REBOOT])



    def test_identify_turns_the_led_off_automatically(self):

        with tempfile.TemporaryDirectory() as folder:

            service = make_service(folder)

            device = usb.parse_query_response(query_response())

            service._upsert(device, "10.0.1.1", "255.255.255.0", "DIRECT_IP")

            calls, done = [], threading.Event()

            def fake(_ip, request, _id, bind_ip=None):

                opcode = int.from_bytes(request[8:10], "big")

                calls.append(opcode)

                if opcode == usb.BLINK_OFF: done.set()

                return {"ok": True}

            service.command = fake

            self.assertTrue(service.identify(device["mac"], 0.05)["command_accepted"])

            self.assertTrue(done.wait(2), "identify must schedule an automatic blink off")

            self.assertEqual(calls, [usb.BLINK_ON, usb.BLINK_OFF])



    def test_identify_rejects_out_of_range_durations(self):

        with tempfile.TemporaryDirectory() as folder:

            service = make_service(folder)

            service._upsert(usb.parse_query_response(query_response()), "10.0.1.1", "255.255.255.0", "DIRECT_IP")

            for duration in (0, -1, 31, "abc"):

                self.assertEqual(service.identify(HOST_MAC, duration)["status"], "invalid_request")





if __name__ == "__main__":

    unittest.main()

