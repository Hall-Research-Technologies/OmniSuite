"""AT-OMNI-311/324 UDP protocol and independent discovery service.

This module intentionally has no Flask dependency.  It implements the binary
protocol described in Atlona's AT-OMNI-311/324 API (UDP/6137), retains standalone
extender state by MAC address, and keeps expensive range scans off the caller's
critical path.

State model
-----------
Two kinds of state are deliberately kept apart:

``cached``
    Whatever the last successful discovery stored in ``usb_extenders.json``.
    Useful for rendering and for remembering identity across IP changes, but it
    is *never* authoritative for a routing decision.  ``last_seen`` and
    ``advanced_query_at`` describe how old it is.

``authoritative``
    A value read from the device during the current operation by
    :meth:`ExtenderDiscoveryService.read_pairing`.  Route transactions consume
    only this.  If a fresh read cannot be completed the operation reports
    ``UNVERIFIABLE`` rather than falling back to the cache.

Hardware validation
-------------------
Pair and Unpair are HARDWARE VALIDATED against a physical AT-OMNI-311/324 pair:
both are *per-endpoint* operations, so a complete route requires the command on
each endpoint (see :meth:`ExtenderDiscoveryService._dual_command`). ACK latency
was 1.5-2.8 ms with no measurable propagation delay.

Items still marked ``HARDWARE VALIDATION REQUIRED`` below remain interpretations
of the API wording that physical testing has not yet settled.
"""
from __future__ import annotations

import atexit
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
import ipaddress
import itertools
import json
import logging
import os
from pathlib import Path
import socket
import struct
import threading
import time
from typing import Callable, Iterable

PREFIX = bytes.fromhex("2F03F4A2")
UDP_PORT = 6137
HEADER_LEN = 10
# Upper bound on an accepted datagram. Raised from 136 once Full Configuration
# was confirmed to answer with 168 bytes; 136 silently rejected it as malformed.
# Still a hard bound, so a hostile or corrupt datagram cannot drive an unbounded
# read: every parser also validates its own minimum length and field bounds.
MAX_PACKET_LEN = 512
MAX_RANGE_HOSTS = 1024
MAX_RANGE_WORKERS = 24
DEFAULT_TIMEOUT = 0.35
DISCOVERY_WINDOW = 0.75

# A record counts as currently online only when it was seen by *this* process
# within this window.  Discovery here is user initiated, so the window is
# generous; nothing polls the network merely to keep the flag set.
ONLINE_TTL = 300.0
PERSIST_DEBOUNCE = 0.5

# Live liveness: a targeted Query that succeeded within this window means the
# endpoint is online right now, independent of which process discovered it.
LIVE_TTL = 45.0
LIVE_TIMEOUT = 0.30
LIVE_WORKERS = 8
# A single dropped UDP datagram must not flip a working endpoint to offline.
LIVE_MISS_TOLERANCE = 2

# Pairing state is refreshed by Advanced Query on its own, slower cadence than
# liveness. It is deliberately a separate concept with a separate timestamp:
# a device answering a Query proves it is online, not that its pairing table was
# re-read. Unlike liveness this is not session scoped -- an Advanced Query that
# actually completed is authoritative regardless of which process ran it.
PAIRING_TTL = 120.0

HOST_MODEL, DEVICE_MODEL = "AT-OMNI-311", "AT-OMNI-324"
# Peer limits come from the API wording. Multi-peer behaviour is exercised in
# tests but has not been driven to the limit on hardware.
HOST_PEER_LIMIT, DEVICE_PEER_LIMIT = 7, 1

# Request/response command pairs documented by Atlona.
QUERY, QUERY_RESPONSE = 0x0000, 0x0001
ACK, NACK = 0x0003, 0x0308
ADVANCED_QUERY, ADVANCED_QUERY_RESPONSE = 0x0300, 0x0301
PAIR, UNPAIR, UNPAIR_ALL = 0x0302, 0x0303, 0x0311
IP_DHCP, IP_STATIC = 0x0306, 0x0307
BLINK_ON, BLINK_OFF, REBOOT = 0x030A, 0x030B, 0x030C

# Read-only queries confirmed against physical hardware on firmware 1.9.4.
FULL_CONFIG, FULL_CONFIG_RESPONSE = 0x030D, 0x030E
LINK_STATUS, LINK_STATUS_RESPONSE = 0x030F, 0x0310
# Topology answers with a payload (0x0305) on one unit and an empty
# acknowledgement (0x0308) on another. Both mean "request processed"; neither
# means the device is offline. It is optional and never gates discovery.
TOPOLOGY = 0x0304
TOPOLOGY_RESPONSES = (0x0305, 0x0308)

# Force Pair is NOT supported.  The value previously used here (0x0301) collides
# with ADVANCED_QUERY_RESPONSE, and no available specification establishes the
# real opcode.  It stays ``None`` so no production path can transmit it;
# build_force_pair() raises until the official document confirms a value.
# HARDWARE VALIDATION REQUIRED: Force Pair opcode and semantics.
FORCE_PAIR = None

# Basic Device Information response, confirmed against firmware 1.9.4 on both a
# physical AT-OMNI-311 and AT-OMNI-324 (88-byte body):
#
#   [0:6]   local MAC          [6:10]  IPv4 address
#   [10]    network mode       [11]    protocol/acquisition byte, meaning unverified
#   [12:44] vendor (32)        [44:76] product id (32)      [76:88] firmware (12)
#
# The string block previously started at offset 11, one byte early. That put the
# protocol byte on the front of the vendor string, shifted every field after it,
# and left the firmware field empty -- which is why no standalone firmware
# version was ever displayed. The trailing field is 12 bytes, not 32.
QUERY_STRING_OFFSET = 12
QUERY_STRING_FIELDS = (("product", 32), ("product_identification", 32), ("product_revision", 12))
QUERY_MIN_LENGTH = QUERY_STRING_OFFSET + sum(width for _name, width in QUERY_STRING_FIELDS)

# Documented Query network-mode byte.  0 means DHCP and 1 means static; an
# earlier build had this inverted, which reported every device backwards.  The
# write side is unaffected because DHCP and static are separate opcodes
# (IP_DHCP / IP_STATIC) rather than a value carried in this field.
NETWORK_MODE_DHCP, NETWORK_MODE_STATIC = 0, 1
NETWORK_MODE_NAMES = {NETWORK_MODE_DHCP: "DHCP", NETWORK_MODE_STATIC: "STATIC"}

# Link Status is its own kind of freshness. Liveness proves the unit answers,
# pairing proves what is configured, and neither implies the extender link is up.
#
# Three separate numbers, and the ordering between them is the whole point:
#
#   LINK_REFRESH_AGE < LINK_TTL < LINK_RETAIN_TTL
#
# A sweep re-reads anything older than LINK_REFRESH_AGE, so a healthy endpoint is
# refreshed well before its state stops counting as current at LINK_TTL. Between
# LINK_TTL and LINK_RETAIN_TTL the last verified state is still shown, marked as
# aging, because one dropped datagram is not evidence that a link went away.
# Past LINK_RETAIN_TTL, or after LINK_MAX_MISSES consecutive failed reads, the
# state becomes genuinely unknown.
LINK_TTL = 60.0
LINK_REFRESH_AGE = 25.0
LINK_RETAIN_TTL = 300.0
LINK_MAX_MISSES = 4
LINK_WORKERS = 6
LINK_TIMEOUT = 0.6

LOG = logging.getLogger("omni_usb_extender")


class ProtocolError(ValueError):
    pass


class ScanBusyError(ProtocolError):
    """A range scan is already queued or running."""


class InterfaceError(OSError):
    """The requested source interface could not be used."""


def normalize_mac(value: str | bytes) -> str:
    """Canonical MAC normalizer for the whole application.

    Accepts bytes, or any text form whose hex digits spell six bytes -- colon,
    hyphen, dotted, or bare, in either case. Every malformed input raises
    ProtocolError, never a bare ValueError: callers catch ProtocolError to fail
    closed, and an odd number of hex digits used to escape that handling and
    surface as an unhandled error.
    """
    if isinstance(value, (bytes, bytearray)):
        raw = bytes(value)
    else:
        text = str(value).strip()
        # Separators are stripped below so dotted MAC notation is accepted, which
        # also means a dotted-quad address would silently become a MAC: an address
        # such as 10.20.30.40 contributes only hex digits, and one whose digits
        # happen to number twelve normalises into a plausible MAC that no device
        # owns. An IPv4 literal is an address, never an identity, so it is
        # rejected outright.
        try:
            ipaddress.IPv4Address(text)
        except ValueError:
            pass
        else:
            raise ProtocolError("an IPv4 address is not a MAC address")
        digits = "".join(c for c in text if c in "0123456789abcdefABCDEF")
        try:
            raw = bytes.fromhex(digits)
        except ValueError as exc:
            raise ProtocolError("MAC address is not valid hexadecimal") from exc
    if len(raw) != 6:
        raise ProtocolError("MAC address must contain six bytes")
    return ":".join(f"{byte:02X}" for byte in raw)


def mac_bytes(value: str | bytes) -> bytes:
    return bytes.fromhex(normalize_mac(value).replace(":", ""))


def _c_string(data: bytes) -> str:
    return data.split(b"\0", 1)[0].decode("utf-8", "replace").strip()


def packet(message_id: int, command: int, data: bytes = b"") -> bytes:
    if command is None:
        raise ProtocolError("command is not supported by this build")
    if not 0 <= int(message_id) <= 0xFFFFFFFF:
        raise ProtocolError("message ID is outside the protocol range")
    if not 0 <= int(command) <= 0xFFFF:
        raise ProtocolError("command is outside the protocol range")
    body = PREFIX + struct.pack(">IH", int(message_id), int(command)) + bytes(data)
    if len(body) > MAX_PACKET_LEN:
        raise ProtocolError("packet exceeds protocol maximum")
    return body


def parse_header(data: bytes, expected_message_id: int | None = None) -> tuple[int, int, bytes]:
    if not isinstance(data, (bytes, bytearray)) or len(data) < HEADER_LEN or len(data) > MAX_PACKET_LEN:
        raise ProtocolError("invalid packet length")
    if bytes(data[:4]) != PREFIX:
        raise ProtocolError("unexpected packet prefix")
    message_id, command = struct.unpack(">IH", bytes(data[4:10]))
    if expected_message_id is not None and message_id != expected_message_id:
        raise ProtocolError("response message ID does not match request")
    return message_id, command, bytes(data[10:])


def build_query(message_id: int) -> bytes: return packet(message_id, QUERY)
def build_advanced_query(message_id: int) -> bytes: return packet(message_id, ADVANCED_QUERY)
def build_ip_dhcp(message_id: int, mac: str | bytes) -> bytes: return packet(message_id, IP_DHCP, mac_bytes(mac))
def build_ip_static(message_id: int, mac: str | bytes, ip: str, mask: str, gateway: str) -> bytes:
    return packet(message_id, IP_STATIC, mac_bytes(mac) + ipaddress.IPv4Address(ip).packed + ipaddress.IPv4Address(mask).packed + ipaddress.IPv4Address(gateway).packed)
def build_blink(message_id: int, enabled: bool) -> bytes: return packet(message_id, BLINK_ON if enabled else BLINK_OFF)
def build_reboot(message_id: int) -> bytes: return packet(message_id, REBOOT)


def build_full_configuration(message_id: int) -> bytes: return packet(message_id, FULL_CONFIG)


def build_link_status(message_id: int) -> bytes: return packet(message_id, LINK_STATUS)


def build_topology(message_id: int) -> bytes: return packet(message_id, TOPOLOGY)


def build_pair(message_id: int, mac: str | bytes) -> bytes:
    """Pair a peer MAC.  Never falls back to Force Pair."""
    return packet(message_id, PAIR, mac_bytes(mac))


def build_force_pair(message_id: int, mac: str | bytes) -> bytes:
    raise ProtocolError("Force Pair is unsupported: opcode not confirmed by the AT-OMNI-311/324 specification")


def build_unpair(message_id: int, mac: str | bytes | None = None) -> bytes:
    """Unpair one peer, or every peer when ``mac`` is None."""
    if mac is None:
        return packet(message_id, UNPAIR_ALL)
    return packet(message_id, UNPAIR, mac_bytes(mac))


def parse_ack(data: bytes, expected_message_id: int | None = None) -> dict:
    message_id, command, payload = parse_header(data, expected_message_id)
    if command == ACK:
        return {"ok": True, "message_id": message_id, "command": command, "data": payload}
    if command == NACK:
        return {"ok": False, "message_id": message_id, "command": command, "error": "device rejected command", "data": payload}
    raise ProtocolError("expected ACK or NACK")


def parse_query_response(data: bytes, expected_message_id: int | None = None) -> dict:
    message_id, command, payload = parse_header(data, expected_message_id)
    if command != QUERY_RESPONSE:
        raise ProtocolError("expected Query response")
    # The documented body carries the identity strings after the fixed header.
    # Accepting anything down to the header alone let a truncated reply parse
    # "successfully" with empty vendor, product and firmware -- which then
    # overwrote good stored values with blanks. A short reply is a bad reply.
    if len(payload) < QUERY_MIN_LENGTH:
        raise ProtocolError("Query response is too short")
    mac = normalize_mac(payload[:6])
    ip = str(ipaddress.IPv4Address(payload[6:10]))
    mode_value = payload[10]
    result = {
        "message_id": message_id, "mac": mac, "ip": ip,
        # Only 0 and 1 are documented. Calling everything else "STATIC" would be
        # naming a value hardware has never shown, which is what the link-state
        # and role-code parsers already refuse to do.
        "network_mode": NETWORK_MODE_NAMES.get(mode_value, "UNKNOWN"),
        "network_mode_code": mode_value,
        # Observed 0x03 on every unit tested. Its meaning is not established, so
        # it is carried as a raw diagnostic and never interpreted.
        "protocol_byte_raw": payload[11] if len(payload) > 11 else None,
    }
    # Each documented field keeps its own fixed slot.  An empty middle field must
    # not shift the fields after it, so padding is trimmed per slot instead of
    # filtering empties out of a positional list.
    start = QUERY_STRING_OFFSET
    for name, width in QUERY_STRING_FIELDS:
        chunk = payload[start:start + width]
        result[name] = _c_string(chunk) if chunk else ""
        start += width
    # Canonical names for what those slots actually contain.
    result["vendor"] = result["product"]
    result["product_id"] = result["product_identification"]
    result["firmware"] = result["product_revision"]
    return result


# Full Configuration response, confirmed against a physical unit (158-byte body).
# Peer slots span [14:56] -- seven six-byte slots, matching HOST_PEER_LIMIT.
#
#   [0:6]    unverified status/flags     [6:12]   local MAC
#   [12:14]  unverified                  [14:56]  seven peer MAC slots
#   [56:58]  unverified                  [58:62]  IPv4    [62:66]  subnet mask
#   [66:70]  gateway                     [70:74]  DHCP server
#   [74:82]  unverified                  [82:114] vendor (32)
#   [114:138] product id (24)            [138:146] firmware (8)
#   [146:154] secondary version (8)      [154:158] unverified
FULL_CONFIG_MIN_LENGTH = 158
FULL_CONFIG_PEER_SLOTS = 7


def parse_full_configuration(data: bytes, expected_message_id: int | None = None) -> dict:
    """Decode Full Configuration.  Unverified regions are kept as raw hex."""
    message_id, command, payload = parse_header(data, expected_message_id)
    if command != FULL_CONFIG_RESPONSE:
        raise ProtocolError("expected Full Configuration response")
    if len(payload) < FULL_CONFIG_MIN_LENGTH:
        raise ProtocolError("Full Configuration response is too short")

    def address(start):
        return str(ipaddress.IPv4Address(payload[start:start + 4]))

    peers = []
    for index in range(FULL_CONFIG_PEER_SLOTS):
        start = 14 + index * 6
        slot = payload[start:start + 6]
        # An all-zero slot is an unused entry, never a device.
        if len(slot) == 6 and any(slot):
            peers.append(normalize_mac(slot))
    strings = {}
    for name, start, width in (("vendor", 82, 32), ("product_id", 114, 24),
                               ("firmware", 138, 8), ("secondary_version", 146, 8)):
        strings[name] = _c_string(payload[start:start + width])
    return {
        "message_id": message_id,
        "mac": normalize_mac(payload[6:12]),
        "paired_macs": peers,
        "ip": address(58),
        "subnet_mask": address(62),
        "gateway": address(66),
        "dhcp_server": address(70),
        **strings,
        # Deliberately not interpreted: no specification establishes these, and a
        # guess here would become a wrong value on a screen.
        "unknown": {
            "head": payload[0:6].hex().upper(),
            "after_mac": payload[12:14].hex().upper(),
            "before_network": payload[56:58].hex().upper(),
            "after_network": payload[74:82].hex().upper(),
            "tail": payload[154:158].hex().upper(),
        },
        "raw": payload.hex().upper(),
    }


# Link Status response, confirmed against physical hardware (50-byte body):
#
#   [0:7]   per-peer link state, one byte per peer slot
#   [7]     zero in every capture, unnamed
#   [8:...] six-byte peer slots, aligned with the states above
#
# The state bytes align with the peer slots. Four captures fix this: a host with
# five configured peers answered 01 01 01 01 01 00 00, a host with two answered
# 01 01 00 00 00 00 00, and a device whose configured host was powered off
# answered 02 followed by zeros. Seven slots matches HOST_PEER_LIMIT and the
# seven peer slots in Full Configuration.
#
# 0x01 and 0x02 are the only values observed. Anything else is UNKNOWN rather
# than folded into "not linked": a value never seen is not evidence.
LINK_STATE_LINKED = 0x01
LINK_STATE_NOT_LINKED = 0x02
LINK_STATUS_SLOTS = 7
LINK_STATUS_MIN_LENGTH = 8
LINK_STATUS_PEER_OFFSET = 8
LINK_STATE_NAMES = {LINK_STATE_LINKED: "LINKED", LINK_STATE_NOT_LINKED: "NOT_LINKED"}


def parse_link_status(data: bytes, expected_message_id: int | None = None) -> dict:
    """Decode Link Status.  Reports the link flag and the MACs the device names.

    Only what the hardware established is named. The peer list is what the device
    reported, which is not the same statement as the configured pairing table.
    """
    message_id, command, payload = parse_header(data, expected_message_id)
    if command != LINK_STATUS_RESPONSE:
        raise ProtocolError("expected Link Status response")
    if len(payload) < LINK_STATUS_MIN_LENGTH:
        raise ProtocolError("Link Status response is too short")
    peers, states = [], []
    # Bounded by the documented slot count, not by however long the datagram
    # happens to be: a longer reply would otherwise be read past the peer region
    # and its trailing bytes emitted as peer MACs.
    last_slot = min(len(payload) - 5, LINK_STATUS_PEER_OFFSET + LINK_STATUS_SLOTS * 6)
    for index, start in enumerate(range(LINK_STATUS_PEER_OFFSET, last_slot, 6)):
        slot = payload[start:start + 6]
        if not any(slot):
            continue
        peers.append(normalize_mac(slot))
        code = payload[index] if index < LINK_STATUS_SLOTS else None
        states.append({"mac": normalize_mac(slot),
                       "state": LINK_STATE_NAMES.get(code, "UNKNOWN"),
                       "code": f"0x{code:02X}" if code is not None else None})
    # The endpoint's overall link: up when any configured peer reports a link.
    # With no configured peer there is nothing to report on.
    if not states:
        link_state = "UNKNOWN"
    elif any(entry["state"] == "LINKED" for entry in states):
        link_state = "LINKED"
    elif all(entry["state"] == "NOT_LINKED" for entry in states):
        link_state = "NOT_LINKED"
    else:
        link_state = "UNKNOWN"
    return {
        "message_id": message_id,
        "linked": link_state == "LINKED",
        "link_state": link_state,
        # Per-peer detail, aligned with the peer slots.
        "link_states": states,
        # These are the peers the device names. That is the configured set: a
        # not-linked unit still listed its configured host.
        "link_peer_macs": peers,
        "unknown": {
            "flag": f"0x{payload[0]:02X}",
            "byte1": f"0x{payload[1]:02X}" if len(payload) > 1 else None,
            "state_bytes": payload[0:LINK_STATUS_SLOTS].hex().upper(),
            "reserved": payload[LINK_STATUS_SLOTS:8].hex().upper(),
        },
        "raw": payload.hex().upper(),
    }


def parse_topology_response(data: bytes, expected_message_id: int | None = None) -> dict:
    """Topology is optional.  An empty acknowledgement is a valid answer."""
    message_id, command, payload = parse_header(data, expected_message_id)
    if command not in TOPOLOGY_RESPONSES:
        raise ProtocolError("expected Topology response")
    return {
        "message_id": message_id,
        "command": command,
        "supported": bool(payload),
        "raw": payload.hex().upper(),
    }


def _peer_slots(payload: bytes) -> list:
    """The occupied peer slots in a peer region, in order.

    An all-zero slot is an empty slot, not a peer at 00:00:00:00:00:00 -- which
    is what this read used to report, and which then became the "previous owner"
    a route move tried and failed to release. Full Configuration already skipped
    those; this makes the authoritative pairing read agree with it.

    A repeated slot is reported twice, deliberately: a parser reports what the
    device said, and deciding what a duplicate means belongs above it.
    """
    peers = []
    for index in range(0, len(payload) - 5, 6):
        slot = payload[index:index + 6]
        if len(slot) == 6 and any(slot):
            peers.append(normalize_mac(slot))
    return peers


def parse_advanced_query_response(data: bytes, expected_message_id: int | None = None) -> dict:
    message_id, command, payload = parse_header(data, expected_message_id)
    if command != ADVANCED_QUERY_RESPONSE or not payload:
        raise ProtocolError("expected Advanced Query response")
    device_code = payload[0]
    if device_code not in (0, 1):
        raise ProtocolError("unknown extender device type")
    paired = payload[1:]
    if len(paired) % 6:
        raise ProtocolError("invalid paired MAC payload")
    limit = HOST_PEER_LIMIT if device_code == 0 else DEVICE_PEER_LIMIT
    if len(paired) // 6 > limit:
        # Surfaced rather than trimmed: a routing decision must never be made
        # from a payload we do not understand.
        raise ProtocolError("paired MAC count exceeds device limit")
    return {
        "message_id": message_id,
        "device_type": HOST_MODEL if device_code == 0 else DEVICE_MODEL,
        "device_type_code": device_code,
        "paired_macs": _peer_slots(paired),
    }


def _range_bounds(text: str) -> tuple[int, int]:
    """Return the inclusive integer bounds of a range without materializing it."""
    if "-" in text and "/" not in text:
        first, last = (part.strip() for part in text.split("-", 1))
        start, end = ipaddress.IPv4Address(first), ipaddress.IPv4Address(last)
        if int(end) < int(start):
            raise ProtocolError("range end precedes range start")
        return int(start), int(end)
    network = ipaddress.IPv4Network(text, strict=False)
    if network.prefixlen >= 31:
        return int(network.network_address), int(network.broadcast_address)
    return int(network.network_address) + 1, int(network.broadcast_address) - 1


def count_range_hosts(values: Iterable[str]) -> int:
    """Upper bound on hosts across ``values``, computed arithmetically."""
    total = 0
    for value in values:
        text = str(value or "").strip()
        if not text:
            continue
        try:
            start, end = _range_bounds(text)
        except (ipaddress.AddressValueError, ipaddress.NetmaskValueError, ValueError) as exc:
            # Malformed input raises ProtocolError like every other parser
            # here; a bare ValueError would escape a caller's fail-closed
            # handling and surface as a 500.
            raise ProtocolError(
                f"{text!r} is not a valid IPv4 address, range or CIDR") from exc
        total += end - start + 1
    return total


def parse_ranges(values: Iterable[str], maximum_hosts: int = MAX_RANGE_HOSTS) -> list[str]:
    """Expand ranges to host addresses, rejecting oversized input before expanding.

    Every range is sized by arithmetic first, so input such as ``10.0.0.0/8`` or
    ``1.0.0.0-254.0.0.0`` is rejected without allocating the address space it
    describes.  The running total bounds the combined set as well.
    """
    plans: list[tuple[int, int]] = []
    total = 0
    for value in values:
        text = str(value or "").strip()
        if not text:
            continue
        try:
            start, end = _range_bounds(text)
        except (ipaddress.AddressValueError, ipaddress.NetmaskValueError, ValueError) as exc:
            # Same contract as every other parser here: malformed input is a
            # ProtocolError, never a bare ValueError escaping as a 500.
            raise ProtocolError(
                f"{text!r} is not a valid IPv4 address, range or CIDR") from exc
        total += end - start + 1
        if total > maximum_hosts:
            raise ProtocolError(f"discovery range exceeds {maximum_hosts} hosts")
        plans.append((start, end))
    hosts: list[str] = []
    for start, end in plans:
        hosts.extend(str(ipaddress.IPv4Address(number)) for number in range(start, end + 1))
    return list(dict.fromkeys(hosts))


def validate_static_network(address: str, mask: str, gateway: str) -> tuple[str, str, str]:
    """Validate a static IPv4 tuple without silently changing caller input."""
    try:
        address_obj = ipaddress.IPv4Address(address)
        mask_obj = ipaddress.IPv4Address(mask)
        gateway_obj = ipaddress.IPv4Address(gateway)
        network = ipaddress.IPv4Network(f"{address_obj}/{mask_obj}", strict=False)
    except (ipaddress.AddressValueError, ipaddress.NetmaskValueError, ValueError) as exc:
        raise ProtocolError("static address, subnet mask, or gateway is invalid") from exc
    if str(network.netmask) != str(mask_obj):
        raise ProtocolError("subnet mask is not a valid IPv4 netmask")
    if address_obj in (network.network_address, network.broadcast_address):
        raise ProtocolError("static address cannot be the network or broadcast address")
    if gateway_obj not in network or gateway_obj in (network.network_address, network.broadcast_address):
        raise ProtocolError("gateway must be a usable address in the static subnet")
    if gateway_obj == address_obj:
        raise ProtocolError("gateway cannot equal the static address")
    return str(address_obj), str(mask_obj), str(gateway_obj)


def network_relation(interface_ip: str, mask: str, device_ip: str) -> str:
    """Classify a device against a real interface.

    Returns ``UNKNOWN`` when the interface is not known.  Callers must treat
    ``UNKNOWN`` as not eligible; a device is never compared against a fabricated
    network derived from its own address.
    """
    try:
        network = ipaddress.IPv4Network(f"{interface_ip}/{mask}", strict=False)
        return "LOCAL" if ipaddress.IPv4Address(device_ip) in network else "OFF_NET"
    except (ipaddress.AddressValueError, ipaddress.NetmaskValueError, ValueError):
        return "UNKNOWN"


def endpoint_network(record: dict) -> tuple[str, str]:
    """The endpoint's own address and mask.

    Prefers the mask the device itself reported through Full Configuration, and
    falls back to the mask of the interface it was discovered from. Returns
    ``("", "")`` when neither is usable.
    """
    ip = str((record or {}).get("ip") or "").strip()
    mask = str((record or {}).get("device_subnet_mask")
               or (record or {}).get("subnet_mask") or "").strip()
    return (ip, mask) if ip and mask else ("", "")


def _is_broadcast_address(destination: str) -> bool:
    """True for the all-ones address or an address ending .255.

    Deliberately conservative: it only has to be right often enough to set the
    socket option, and setting it on a unicast socket changes nothing.
    """
    text = str(destination or "").strip()
    if text == "255.255.255.255":
        return True
    return text.endswith(".255")


def endpoints_same_network(ip_a: str, mask_a: str, ip_b: str, mask_b: str) -> bool | None:
    """Are two USB endpoints on one IP network, by their own masks?

    This is a question about the two endpoints, never about the controller: a
    management host may sit on a third network and still reach both. Returns
    ``None`` when the inputs do not establish an answer, so a caller can tell
    "not compatible" apart from "cannot say".
    """
    if not (ip_a and mask_a and ip_b and mask_b):
        return None
    try:
        network_a = ipaddress.IPv4Network(f"{ip_a}/{mask_a}", strict=False)
        network_b = ipaddress.IPv4Network(f"{ip_b}/{mask_b}", strict=False)
        return (network_a == network_b
                and ipaddress.IPv4Address(ip_b) in network_a
                and ipaddress.IPv4Address(ip_a) in network_b)
    except (ipaddress.AddressValueError, ipaddress.NetmaskValueError, ValueError):
        return None


# Transport error -> route transaction status.
_COMMAND_STATUS = {
    "timeout": "COMMAND_TIMEOUT",
    "device rejected command": "REJECTED",
    "protocol_error": "PROTOCOL_ERROR",
    "not_found": "UNREACHABLE",
    "interface_unavailable": "INTERFACE_UNAVAILABLE",
}


class ExtenderDiscoveryService:
    """Thread-safe MAC-indexed extender state, separate from OmniStream scanning."""

    def __init__(self, state_file: Path, socket_factory: Callable = socket.socket, now: Callable[[], float] = time.time,
                 online_ttl: float = ONLINE_TTL, persist_debounce: float = PERSIST_DEBOUNCE):
        self.state_file, self.socket_factory, self.now = Path(state_file), socket_factory, now
        self.online_ttl, self.persist_debounce = float(online_ttl), float(persist_debounce)
        self._lock = threading.RLock()
        self._route_lock = threading.RLock()
        self._io_lock = threading.Lock()
        self._ids = itertools.count(int(now() * 1000) & 0xFFFFFFFF)
        self._devices: dict[str, dict] = {}
        self._ranges: list[str] = []
        # Traffic tally, so the cost of a cadence change can be measured rather
        # than reasoned about. Diagnostics only; nothing behaves differently.
        self._transmit_counts: dict = {}
        self._transmit_since = now()
        self._identify_timers: dict[str, threading.Timer] = {}
        self._persist_timer: threading.Timer | None = None
        self._dirty = False
        # Enrichment and range scanning use separate pools so a long range scan
        # cannot delay broadcast enrichment (head-of-line blocking).
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="usb-extender")
        self._scan_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="usb-extender-range")
        self._scan_future: Future | None = None
        # Local broadcast discovery gets its own worker so that dispatching it
        # never queues behind a range scan or behind enrichment.
        self._local_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="usb-extender-local")
        self._local_future: Future | None = None
        # Persisted history is never treated as "currently online"; a record must
        # be re-seen by this process to count as active.
        self._session_start = float(now())
        self._load()
        atexit.register(self.close)

    # ---------------- persistence ----------------
    def _load(self):
        try:
            data = json.loads(self.state_file.read_text(encoding="utf-8")) if self.state_file.exists() else {}
            self._ranges = list(data.get("ranges") or [])
            devices = {}
            for key, value in (data.get("devices") or {}).items():
                if not isinstance(value, dict):
                    continue
                record = dict(value)
                # Liveness is derived, never restored from disk.
                record.pop("online", None)
                record.pop("manageable", None)
                record.pop("stale", None)
                try:
                    devices[normalize_mac(key)] = record
                except ProtocolError:
                    # One unreadable key costs one endpoint, not the whole
                    # inventory: this used to unwind and leave the store empty.
                    LOG.info("Skipping unreadable device key in state file")
            self._devices = devices
        except Exception as exc:
            LOG.warning("USB extender state load failed: %s", type(exc).__name__)

    def _mark_dirty(self):
        """Queue a debounced flush.  Must be called with ``self._lock`` held."""
        self._dirty = True
        if self.persist_debounce <= 0:
            self._flush()
            return
        if self._persist_timer is not None and self._persist_timer.is_alive():
            return
        timer = threading.Timer(self.persist_debounce, self._flush)
        timer.daemon = True
        self._persist_timer = timer
        timer.start()

    def _flush(self):
        """Write the state file outside the state lock so readers are not blocked."""
        with self._lock:
            if not self._dirty:
                return
            self._dirty = False
            snapshot = json.dumps({"ranges": list(self._ranges),
                                   "devices": {key: dict(value) for key, value in self._devices.items()}}, indent=2)
        try:
            with self._io_lock:
                self.state_file.parent.mkdir(parents=True, exist_ok=True)
                # Unique temp name and an fsync before the rename, matching every
                # other writer in the project: os.replace is atomic against other
                # processes, but without the fsync the rename can be durable
                # while the contents are not, and a shared temp name collides
                # with a second instance on Windows.
                tmp = self.state_file.with_name(
                    f"{self.state_file.name}.{os.getpid()}-{threading.get_ident()}.tmp")
                with open(tmp, "w", encoding="utf-8") as handle:
                    handle.write(snapshot)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(tmp, self.state_file)
        except OSError as exc:
            with self._lock:
                self._dirty = True
            LOG.warning("USB extender state persist failed: %s", type(exc).__name__)

    def close(self):
        timer = self._persist_timer
        if timer is not None:
            timer.cancel()
        self._flush()

    # ---------------- freshness ----------------
    def _decorate(self, device: dict) -> dict:
        """Attach derived liveness to a copy of a stored record."""
        record = dict(device)
        # Re-derive the network mode from the raw byte so records persisted by an
        # earlier build, which mapped the field the wrong way round, report
        # correctly without waiting for the device to be discovered again.
        code = record.get("network_mode_code")
        if isinstance(code, int) and not isinstance(code, bool):
            record["network_mode"] = NETWORK_MODE_NAMES.get(code, "UNKNOWN")
        now = self.now()
        seen = float(record.get("last_seen") or 0.0)
        # A device that answered a targeted live Query is online regardless of
        # which process first discovered it. Persisted history alone is not
        # enough, but a live response supersedes it.
        live_seen = float(record.get("live_seen") or 0.0)
        live_online = bool(live_seen) and (now - live_seen) <= LIVE_TTL
        record["live_seen"] = live_seen or None
        record["live_online"] = live_online
        record["liveness_source"] = record.get("liveness_source") or ("udp_query" if live_online else "")
        # A live poll that determined the endpoint is unreachable is authoritative
        # and overrides an older sighting from this session.
        determined_offline = bool(record.get("live_offline")) and not live_online
        online = live_online or (not determined_offline
                                 and seen >= self._session_start and (now - seen) <= self.online_ttl)
        record["online"] = online
        record["stale"] = not online
        # Manageable means seen by this process and reachable through a known
        # interface -- not merely present in the persisted file.
        record["manageable"] = bool(online and record.get("interface_ip") and record.get("subnet_mask"))
        # Pairing freshness is independent of liveness and carries its own
        # timestamp; neither one implies the other.
        advanced_at = float(record.get("advanced_query_at") or 0.0)
        record["pairing_last_read"] = advanced_at or None
        record["pairing_state_fresh"] = bool(advanced_at) and (now - advanced_at) <= PAIRING_TTL
        record["pairing_age"] = (now - advanced_at) if advanced_at else None
        # Link status is a third, independent clock. A configured peer is not a
        # live link, and a stale or failed read is UNKNOWN -- never "not linked",
        # because a timeout and a confirmed absence of link are different facts.
        link_at = float(record.get("link_last_read") or 0.0)
        link_age = (now - link_at) if link_at else None
        misses = int(record.get("link_consecutive_failures") or 0)
        link_fresh = bool(link_at) and link_age <= LINK_TTL and misses < LINK_MAX_MISSES
        # Retained means: verified once, not current, but not yet old enough or
        # missed often enough to call unknown. The state is still the truth we
        # last measured, and the caller is told how old it is.
        link_retained = (bool(link_at) and not link_fresh
                         and link_age <= LINK_RETAIN_TTL and misses < LINK_MAX_MISSES)
        record["link_last_read"] = link_at or None
        record["link_last_attempt"] = float(record.get("link_last_attempt") or 0.0) or None
        record["link_consecutive_failures"] = misses
        record["link_state_fresh"] = link_fresh
        record["link_state_retained"] = link_retained
        record["link_known"] = link_fresh or link_retained
        record["link_age"] = link_age
        record["link_source"] = record.get("link_source", "") if (link_fresh or link_retained) else ""
        if link_fresh or link_retained:
            record["link_states"] = list(record.get("link_states") or [])
            record["link_peer_macs"] = list(record.get("link_peer_macs") or [])
        else:
            record["link_state"] = "UNKNOWN"
            record["link_states"] = []
            record["link_peer_macs"] = []
        return record

    def state(self) -> dict:
        with self._lock:
            devices = [self._decorate(device) for device in self._devices.values()]
            ranges = list(self._ranges)
        return {"devices": sorted(devices, key=lambda device: device.get("mac", "")),
                "ranges": ranges, "online_ttl": self.online_ttl}

    def device(self, mac: str) -> dict | None:
        try:
            mac = normalize_mac(mac)
        except ProtocolError:
            return None
        with self._lock:
            current = self._devices.get(mac)
            return self._decorate(current) if current else None

    def clear_devices(self) -> int:
        """Forget the discovered inventory.

        This is inventory only: configured ranges are kept, no packet is sent,
        and no device configuration, pairing, or power state is touched.  Cleared
        devices reappear after the next successful discovery.
        """
        with self._lock:
            removed = len(self._devices)
            self._devices = {}
            self._mark_dirty()
        self._flush()
        LOG.info("Cleared %d standalone USB extender record(s) from inventory", removed)
        return removed

    def set_ranges(self, ranges: Iterable[str]) -> list[str]:
        cleaned = [str(item).strip() for item in ranges if str(item).strip()]
        parse_ranges(cleaned)  # bounded validation before anything is stored
        with self._lock:
            self._ranges = cleaned
            self._mark_dirty()
            saved = list(self._ranges)
        self._flush()  # user-visible configuration is written immediately
        return saved

    def _message_id(self) -> int:
        return next(self._ids) & 0xFFFFFFFF

    def _upsert(self, query: dict, interface_ip: str, mask: str, source: str, advanced: dict | None = None) -> dict:
        mac = query["mac"]
        relation = network_relation(interface_ip, mask, query["ip"])
        now = self.now()
        with self._lock:
            current = dict(self._devices.get(mac) or {})
            current.update(query)
            current.update({
                "mac": mac, "discovered": True, "last_seen": now,
                "first_seen": current.get("first_seen") or now,
                "network_relation": relation,
                # Whether this endpoint can be addressed at all. Unicast UDP is
                # routable, so an endpoint on another subnet is still eligible;
                # whether two endpoints may be paired *with each other* is a
                # separate question answered from their own addresses and masks.
                "pairing_eligible": bool(query.get("ip")),
                "discovery_method": source, "interface_ip": interface_ip, "subnet_mask": mask,
            })
            if advanced:
                current.update(advanced)
                current["advanced_query_at"] = now
            self._devices[mac] = current
            self._mark_dirty()
            return self._decorate(current)

    # ---------------- transport ----------------
    def _exchange(self, destination: str, request: bytes, message_id: int, timeout: float = DEFAULT_TIMEOUT, bind_ip: str | None = None) -> tuple[bytes, tuple]:
        sock = self.socket_factory(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.settimeout(timeout)
            # `configure_network` addresses the interface broadcast, which the
            # OS refuses on a socket that has not asked for it -- the send failed
            # with EACCES and surfaced as "device not known", naming the wrong
            # cause. Discovery already sets this; a single exchange did not.
            if _is_broadcast_address(destination):
                try:
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                except OSError:
                    LOG.info("Could not enable broadcast for %s", destination)
            if bind_ip:
                try:
                    sock.bind((bind_ip, 0))
                except OSError as exc:
                    raise InterfaceError(f"source interface {bind_ip} is unavailable") from exc
            self._count_transmit(request)
            sock.sendto(request, (destination, UDP_PORT))
            data, sender = sock.recvfrom(MAX_PACKET_LEN + 1)
            parse_header(data, message_id)
            return data, sender
        finally:
            sock.close()

    def _count_transmit(self, request: bytes) -> None:
        """Tally what actually leaves, by opcode.

        Cadence and TTL constants describe intent; this is the traffic. Keeping a
        count makes it possible to answer "did that change increase device load"
        with a measurement instead of an argument.
        """
        try:
            command = int.from_bytes(request[8:10], "big")
        except (TypeError, ValueError):
            return
        with self._lock:
            counts = self._transmit_counts
            counts[command] = counts.get(command, 0) + 1
            counts["total"] = counts.get("total", 0) + 1

    def transmit_counts(self) -> dict:
        """A snapshot of the opcode tally, named for readability."""
        names = {QUERY: "query", ADVANCED_QUERY: "advanced_query", FULL_CONFIG: "full_config",
                 LINK_STATUS: "link_status", TOPOLOGY: "topology", PAIR: "pair", UNPAIR: "unpair",
                 IP_DHCP: "ip_dhcp", IP_STATIC: "ip_static", REBOOT: "reboot",
                 BLINK_ON: "blink_on", BLINK_OFF: "blink_off"}
        with self._lock:
            counts = dict(self._transmit_counts)
        out = {"total": counts.pop("total", 0), "since": self._transmit_since, "other": 0}
        for command, value in counts.items():
            name = names.get(command)
            if name:
                out[name] = value
            else:
                out["other"] += value
        return out

    def command(self, ip: str, request: bytes, message_id: int, bind_ip: str | None = None) -> dict:
        try:
            return parse_ack(self._exchange(ip, request, message_id, bind_ip=bind_ip)[0], message_id)
        except socket.timeout:
            return {"ok": False, "error": "timeout"}
        except ProtocolError:
            return {"ok": False, "error": "protocol_error"}
        except InterfaceError:
            LOG.warning("USB extender command could not bind source interface %s", bind_ip)
            return {"ok": False, "error": "interface_unavailable"}
        except OSError:
            return {"ok": False, "error": "not_found"}

    def _command_context(self, mac: str):
        """Resolve ``((record, ip, interface_ip), None)`` or ``(None, error)``.

        Fails closed when the stored interface context is unusable rather than
        letting the OS routing table pick an unrelated NIC.
        """
        device = self.device(mac)
        if not device:
            return None, {"status": "not_found"}
        ip = str(device.get("ip") or "").strip()
        interface_ip = str(device.get("interface_ip") or "").strip()
        mask = str(device.get("subnet_mask") or "").strip()
        if not ip:
            return None, {"status": "not_found"}
        return (device, ip, interface_ip), None

    @staticmethod
    def _bind_source(interface_ip: str, mask: str, destination: str) -> str | None:
        """The source address to bind, or None to let IP routing decide.

        A source is forced only when the discovering interface is on the
        destination's own network, which is what makes the choice meaningful on a
        multi-NIC host. For a routed destination no such local address exists, so
        binding one would make a reachable endpoint unreachable.
        """
        interface_ip = str(interface_ip or "").strip()
        if interface_ip and network_relation(interface_ip, mask, destination) == "LOCAL":
            return interface_ip
        return None

    # ---------------- discovery ----------------
    def discover_ip(self, ip: str, interface_ip: str, mask: str, method: str = "DIRECT_IP", expected_mac: str | None = None) -> dict:
        """Query one address.

        When ``expected_mac`` is given the responding MAC must match it.  A
        different device answering at a remembered address is recorded as
        itself and reported as ``mac_mismatch``; the expected record is left
        untouched and is never marked seen.
        """
        try:
            ip = str(ipaddress.IPv4Address(ip))
        except ValueError:
            return {"status": "invalid_address"}
        if expected_mac is not None:
            try:
                expected_mac = normalize_mac(expected_mac)
            except ProtocolError:
                return {"status": "invalid_address"}
        bind_ip = interface_ip if network_relation(interface_ip, mask, ip) != "UNKNOWN" else None
        mid = self._message_id()
        try:
            query = parse_query_response(self._exchange(ip, build_query(mid), mid, bind_ip=bind_ip)[0], mid)
        except socket.timeout:
            return {"status": "timeout"}
        except ProtocolError:
            return {"status": "protocol_error"}
        except InterfaceError:
            return {"status": "interface_unavailable"}
        except OSError:
            return {"status": "not_found"}
        if expected_mac is not None and query["mac"] != expected_mac:
            other = self._upsert(query, interface_ip, mask, method)
            LOG.info("USB extender at %s reported %s, expected %s", ip, query["mac"], expected_mac)
            return {"status": "mac_mismatch", "expected_mac": expected_mac, "actual_mac": query["mac"], "device": other}
        device = self._upsert(query, interface_ip, mask, method)
        try:
            advanced_mid = self._message_id()
            advanced = parse_advanced_query_response(
                self._exchange(ip, build_advanced_query(advanced_mid), advanced_mid, bind_ip=bind_ip)[0], advanced_mid)
            device = self._upsert(query, interface_ip, mask, method, advanced)
        except (socket.timeout, OSError, ProtocolError) as exc:
            # Best effort only.  Cached pairing data stays as history and is
            # marked non-fresh; routing never consumes it (see read_pairing).
            LOG.info("Advanced Query for %s unavailable: %s", ip, type(exc).__name__)
        return {"status": "found", "device": device}

    def ping(self, mac: str) -> dict:
        """Targeted liveness check for one known endpoint.

        Sends a single Query to the address already on record -- no broadcast, no
        range scan, and deliberately no Advanced Query, because proving the unit
        answers does not require reading its pairing table. The responding MAC
        must match, so another device at the same address never marks this one
        online. A miss is tolerated up to ``LIVE_MISS_TOLERANCE`` times before the
        endpoint is treated as offline, so one dropped datagram does not flap it.
        """
        try:
            mac = normalize_mac(mac)
        except ProtocolError:
            return {"status": "not_found"}
        context, error = self._command_context(mac)
        if error:
            return {"status": error["status"]}
        record, ip, interface_ip = context
        mask = str(record.get("subnet_mask") or "")
        message_id = self._message_id()
        try:
            data, _sender = self._exchange(ip, build_query(message_id), message_id,
                                           timeout=LIVE_TIMEOUT,
                                           bind_ip=self._bind_source(interface_ip, mask, ip))
            query = parse_query_response(data, message_id)
        except (socket.timeout, OSError, ProtocolError) as exc:
            with self._lock:
                current = self._devices.get(mac)
                if current is not None:
                    current["live_missed"] = int(current.get("live_missed") or 0) + 1
                    if current["live_missed"] > LIVE_MISS_TOLERANCE:
                        # Enough consecutive misses to determine it is offline.
                        # This is authoritative and overrides an older sighting.
                        current.pop("live_seen", None)
                        current["liveness_source"] = ""
                        current["live_offline"] = True
                    self._mark_dirty()
            return {"status": "offline", "mac": mac, "reason": type(exc).__name__}
        if query["mac"] != mac:
            # Another device answering is not a response from this one, so it
            # counts as a miss and never marks this endpoint online.
            LOG.warning("Live poll: expected %s at %s but %s answered", mac, ip, query["mac"])
            with self._lock:
                current = self._devices.get(mac)
                if current is not None:
                    current["live_missed"] = int(current.get("live_missed") or 0) + 1
                    current.pop("live_seen", None)
                    current["liveness_source"] = ""
                    current["live_offline"] = True
                    self._mark_dirty()
            return {"status": "mac_mismatch", "mac": mac, "actual_mac": query["mac"]}
        now = self.now()
        with self._lock:
            current = self._devices.get(mac)
            if current is None:
                return {"status": "not_found", "mac": mac}
            current.update(query)
            current["live_seen"] = now
            current["last_seen"] = now
            current["live_missed"] = 0
            current["live_offline"] = False
            current["liveness_source"] = "udp_query"
            current["network_relation"] = network_relation(interface_ip, record.get("subnet_mask", ""), query["ip"])
            current["pairing_eligible"] = bool(query["ip"])
            self._mark_dirty()
            return {"status": "online", "mac": mac, "ip": query["ip"], "device": self._decorate(current)}

    def refresh_liveness(self, macs: Iterable[str]) -> dict:
        """Targeted live poll of several known endpoints, bounded and concurrent.

        One slow or unreachable endpoint cannot hold up the others: each has its
        own short timeout and they run in parallel.
        """
        targets = []
        for mac in macs or []:
            try:
                targets.append(normalize_mac(mac))
            except ProtocolError:
                continue
        if not targets:
            return {"checked": 0, "online": 0, "results": {}}
        results = {}
        with ThreadPoolExecutor(max_workers=min(LIVE_WORKERS, len(targets)),
                                thread_name_prefix="usb-live") as pool:
            for mac, outcome in zip(targets, pool.map(self.ping, targets)):
                results[mac] = outcome.get("status")
        return {"checked": len(targets),
                "online": len([s for s in results.values() if s == "online"]),
                "results": results}

    # ---------------- verified read-only queries ----------------
    def _query(self, mac: str, builder, parser, timeout: float = DEFAULT_TIMEOUT) -> dict:
        """One MAC-targeted read-only query, with its own failure handling.

        Every query is independent: an unsupported or slow command returns its own
        status and never marks the device offline or fails a sibling query.
        """
        resolved, error = self._command_context(mac)
        if error:
            return {"status": error["status"], "mac": mac}
        _device, ip, interface_ip = resolved
        mask = str(_device.get("subnet_mask") or "")
        mid = self._message_id()
        try:
            data, _addr = self._exchange(ip, builder(mid), mid, timeout=timeout,
                                         bind_ip=self._bind_source(interface_ip, mask, ip))
            return {"status": "ok", "mac": mac, "ip": ip, "result": parser(data, mid)}
        except socket.timeout:
            # A command this firmware does not answer is unsupported, not offline.
            return {"status": "timeout", "mac": mac, "ip": ip}
        except ProtocolError as exc:
            LOG.info("USB extender %s protocol error: %s", mac, exc)
            return {"status": "protocol_error", "mac": mac, "ip": ip, "error": str(exc)}
        except InterfaceError:
            return {"status": "interface_unavailable", "mac": mac, "ip": ip}
        except OSError:
            return {"status": "unreachable", "mac": mac, "ip": ip}

    def get_device_info(self, mac: str) -> dict:
        """Basic Device Information: identity, address, firmware."""
        return self._query(mac, build_query, parse_query_response)

    def get_extended_device_info(self, mac: str) -> dict:
        """Extended Device Information: USB role and the configured peer list."""
        return self._query(mac, build_advanced_query, parse_advanced_query_response)

    def get_full_configuration(self, mac: str) -> dict:
        """Full Configuration.  Not answered by every unit; failure is not offline."""
        return self._query(mac, build_full_configuration, parse_full_configuration)

    def get_link_status(self, mac: str) -> dict:
        """Current extender link, which is not the same as the configured pairing."""
        return self._query(mac, build_link_status, parse_link_status, timeout=LINK_TIMEOUT)

    def get_topology(self, mac: str) -> dict:
        """Optional.  An empty acknowledgement means processed-but-unavailable."""
        return self._query(mac, build_topology, parse_topology_response)

    def read_link_status(self, mac: str) -> dict:
        """Read Link Status and record it under its own fields.

        Never merged into pairing: `link_*` describes what the device reports now,
        `paired_macs` describes what is configured.
        """
        try:
            mac = normalize_mac(mac)
        except ProtocolError:
            return {"status": "invalid_mac"}
        outcome = self.get_link_status(mac)
        now = self.now()
        with self._lock:
            current = self._devices.get(mac)
            if current is None:
                return {"status": "not_found", "mac": mac}
            current["link_last_attempt"] = now
            if outcome.get("status") != "ok":
                # A failed read never rewrites the last known link state. It counts
                # against the miss budget; only exhausting that budget, or the
                # retain window expiring, makes the state unknown.
                current["link_read_failed"] = outcome.get("status")
                current["link_consecutive_failures"] = int(current.get("link_consecutive_failures") or 0) + 1
                self._mark_dirty()
                return {**outcome, "mac": mac,
                        "consecutive_failures": current["link_consecutive_failures"]}
            result = outcome["result"]
            current["link_state"] = result["link_state"]
            current["link_states"] = [dict(state) for state in (result.get("link_states") or [])]
            current["link_peer_macs"] = list(result["link_peer_macs"])
            current["link_last_read"] = now
            current["link_unknown"] = result["unknown"]
            current["link_consecutive_failures"] = 0
            current["link_source"] = "udp_link_status"
            current.pop("link_read_failed", None)
            self._mark_dirty()
            return {"status": "ok", "mac": mac, **result}

    def read_full_configuration(self, mac: str) -> dict:
        """Read Full Configuration and merge what it uniquely establishes.

        Basic Device Information remains the identity source. Full Configuration
        adds mask, gateway and DHCP server, and is cross-checked against Basic:
        a disagreement is recorded as an inconsistency and the record is not
        updated from the disagreeing response.
        """
        try:
            mac = normalize_mac(mac)
        except ProtocolError:
            return {"status": "invalid_mac"}
        outcome = self.get_full_configuration(mac)
        if outcome.get("status") != "ok":
            with self._lock:
                current = self._devices.get(mac)
                if current is not None:
                    # Not every unit answers this command; that is a capability
                    # fact, not a fault, and it never marks the device offline.
                    current["full_config_supported"] = outcome.get("status") != "timeout"
                    current["full_config_status"] = outcome.get("status")
                    self._mark_dirty()
            return {**outcome, "mac": mac}
        result = outcome["result"]
        with self._lock:
            current = self._devices.get(mac)
            if current is None:
                return {"status": "not_found", "mac": mac}
            inconsistencies = {}
            if normalize_mac(result["mac"]) != mac:
                # Identity safety outranks richness: a response that names another
                # device is not merged at all.
                inconsistencies["mac"] = {"basic": mac, "full_config": result["mac"]}
                current["full_config_inconsistent"] = inconsistencies
                current["full_config_status"] = "mac_mismatch"
                self._mark_dirty()
                return {"status": "mac_mismatch", "mac": mac, "actual_mac": result["mac"]}
            for field, basic_key in (("ip", "ip"), ("firmware", "product_revision"),
                                     ("vendor", "product"), ("product_id", "product_identification")):
                known = current.get(basic_key)
                fresh = result.get(field)
                if known and fresh and str(known) != str(fresh):
                    inconsistencies[field] = {"basic": known, "full_config": fresh}
            # Fields only Full Configuration establishes.
            # The device's own mask, kept apart from `subnet_mask`, which records
            # the mask of the interface the endpoint was discovered from. They are
            # different facts and only coincide when the two networks match.
            current["device_subnet_mask"] = result["subnet_mask"]
            current["device_gateway"] = result["gateway"]
            current["gateway"] = result["gateway"]
            current["dhcp_server"] = result["dhcp_server"]
            current["secondary_version"] = result["secondary_version"]
            current["full_config_unknown"] = result["unknown"]
            current["full_config_supported"] = True
            current["full_config_status"] = "ok"
            current["full_config_last_read"] = self.now()
            if inconsistencies:
                LOG.info("USB extender %s: Basic and Full Configuration disagree on %s",
                         mac, ", ".join(sorted(inconsistencies)))
            current["full_config_inconsistent"] = inconsistencies or None
            self._mark_dirty()
            return {"status": "ok", "mac": mac, "inconsistencies": inconsistencies, **result}

    def refresh_full_configuration(self, macs: Iterable[str]) -> dict:
        """Bounded Full Configuration sweep.  One unsupported unit affects no other."""
        targets = []
        for mac in macs or []:
            try:
                targets.append(normalize_mac(mac))
            except ProtocolError:
                continue
        if not targets:
            return {"checked": 0, "refreshed": 0, "inconsistent": 0}
        refreshed = inconsistent = 0
        with ThreadPoolExecutor(max_workers=min(LINK_WORKERS, len(targets)),
                                thread_name_prefix="usb-fullcfg") as pool:
            for outcome in pool.map(self.read_full_configuration, targets):
                if outcome.get("status") == "ok":
                    refreshed += 1
                    if outcome.get("inconsistencies"):
                        inconsistent += 1
        return {"checked": len(targets), "refreshed": refreshed, "inconsistent": inconsistent}

    def refresh_link_status(self, macs: Iterable[str], max_age: float = LINK_REFRESH_AGE) -> dict:
        """Bounded Link Status sweep, skipping anything already fresh.

        ``max_age`` is deliberately shorter than :data:`LINK_TTL`. Re-reading only
        at the TTL guarantees a window in which the state has expired and nothing
        has refreshed it yet, which is exactly how a stable link came to flicker
        to Unknown while a page simply sat open.
        """
        due = []
        for mac in macs or []:
            try:
                mac = normalize_mac(mac)
            except ProtocolError:
                continue
            # Through the locking accessor, as every sibling refresh does: the
            # raw dict is mutated under the lock by read_link_status.
            record = self.device(mac)
            if not record:
                continue
            if (self.now() - float(record.get("link_last_read") or 0)) < max_age:
                continue
            due.append(mac)
        if not due:
            return {"checked": 0, "refreshed": 0}
        refreshed = 0
        with ThreadPoolExecutor(max_workers=min(LINK_WORKERS, len(due)),
                                thread_name_prefix="usb-link") as pool:
            for outcome in pool.map(self.read_link_status, due):
                if outcome.get("status") == "ok":
                    refreshed += 1
        return {"checked": len(due), "refreshed": refreshed}

    def refresh_pairing(self, macs: Iterable[str], max_age: float = PAIRING_TTL) -> dict:
        """Advanced Query refresh for endpoints whose pairing state has aged out.

        Deliberately separate from :meth:`refresh_liveness`: this is the slower,
        heavier read and it is skipped for any endpoint whose pairing table was
        already read within ``max_age``, so repeated page polls never repeat the
        physical traffic.
        """
        due = []
        for mac in macs or []:
            try:
                mac = normalize_mac(mac)
            except ProtocolError:
                continue
            record = self.device(mac)
            if not record:
                continue
            age = record.get("pairing_age")
            if age is None or age > max_age:
                due.append(mac)
        if not due:
            return {"checked": 0, "refreshed": 0, "skipped_fresh": True, "results": {}}
        results = {}
        with ThreadPoolExecutor(max_workers=min(LIVE_WORKERS, len(due)),
                                thread_name_prefix="usb-pairing") as pool:
            for mac, outcome in zip(due, pool.map(self.read_pairing, due)):
                results[mac] = outcome.get("status")
        return {"checked": len(due),
                "refreshed": len([s for s in results.values() if s == "OK"]),
                "skipped_fresh": False, "results": results}

    def discover_local(self, interface_ip: str, mask: str) -> dict:
        network = ipaddress.IPv4Network(f"{interface_ip}/{mask}", strict=False)
        broadcast = str(network.broadcast_address)
        mid = self._message_id()
        found = []
        sock = self.socket_factory(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.settimeout(0.1)
        try:
            sock.bind((interface_ip, 0))
            sock.sendto(build_query(mid), (broadcast, UDP_PORT))
            deadline = time.monotonic() + DISCOVERY_WINDOW
            while time.monotonic() < deadline:
                try:
                    data, _sender = sock.recvfrom(MAX_PACKET_LEN + 1)
                    query = parse_query_response(data, mid)
                except socket.timeout:
                    continue
                except (OSError, ProtocolError):
                    continue
                found.append(self._upsert(query, interface_ip, mask, "LOCAL_BROADCAST"))
        finally:
            sock.close()
        # Enrich only after the initial state has been published.
        for device in list(found):
            self._executor.submit(self.discover_ip, device["ip"], interface_ip, mask,
                                  "LOCAL_BROADCAST_ADVANCED", device["mac"])
        return {"status": "completed", "broadcast": broadcast, "found": found}

    def start_local_discovery(self, interface_ip: str, mask: str) -> Future:
        """Dispatch local broadcast discovery in the background and return at once.

        The caller never waits for the UDP window, so a Device Info scan can fire
        this alongside the OmniStream scan without either one delaying the other.
        """
        with self._lock:
            pending = self._local_future
            if pending is not None and not pending.done():
                raise ScanBusyError("local USB discovery is already running")
            future = self._local_executor.submit(self.discover_local, interface_ip, mask)
            self._local_future = future
            return future

    def start_range_scan(self, interface_ip: str, mask: str, ranges: Iterable[str] | None = None,
                         targets: list[str] | None = None) -> Future:
        """Queue one background range scan.

        Rejects overlapping submissions instead of growing an unbounded queue.
        ``targets`` lets a caller that already expanded the ranges avoid parsing
        them a second time.
        """
        if targets is None:
            with self._lock:
                configured = list(self._ranges)
            targets = parse_ranges(ranges if ranges is not None else configured)
        with self._lock:
            pending = self._scan_future
            if pending is not None and not pending.done():
                raise ScanBusyError("a range scan is already running")
            future = self._scan_executor.submit(self._range_scan, targets, interface_ip, mask)
            self._scan_future = future
            return future

    def _range_scan(self, targets: list[str], interface_ip: str, mask: str):
        results = []
        if not targets:
            return results
        with ThreadPoolExecutor(max_workers=min(MAX_RANGE_WORKERS, max(1, len(targets)))) as executor:
            futures = [executor.submit(self.discover_ip, target, interface_ip, mask, "RANGE") for target in targets]
            for future in as_completed(futures):
                results.append(future.result())
        return results

    # ---------------- authoritative pairing read ----------------
    def read_pairing(self, mac: str) -> dict:
        """Read pairing state from the device now.  Never falls back to cache.

        Returns ``status: "OK"`` only when a MAC-verified Query *and* a valid
        Advanced Query both completed during this call.  The returned
        ``paired_macs`` is the value just read, not the stored one.
        """
        try:
            mac = normalize_mac(mac)
        except ProtocolError:
            return {"status": "NOT_FOUND"}
        context, error = self._command_context(mac)
        if error:
            return {"status": "INTERFACE_UNKNOWN" if error["status"] == "interface_unknown" else "NOT_FOUND", "mac": mac}
        record, ip, interface_ip = context
        mask = str(record.get("subnet_mask") or "")
        mid = self._message_id()
        try:
            query = parse_query_response(
                self._exchange(ip, build_query(mid), mid,
                               bind_ip=self._bind_source(interface_ip, mask, ip))[0], mid)
        except socket.timeout:
            return {"status": "OFFLINE", "mac": mac, "ip": ip}
        except InterfaceError:
            return {"status": "INTERFACE_UNAVAILABLE", "mac": mac, "ip": ip}
        except ProtocolError:
            return {"status": "PROTOCOL_ERROR", "mac": mac, "ip": ip}
        except OSError:
            return {"status": "OFFLINE", "mac": mac, "ip": ip}
        if query["mac"] != mac:
            # Record the responder as itself; do not touch or revive the expected
            # record, and never address a command to it under the wrong identity.
            self._upsert(query, interface_ip, mask, "ROUTE_STATE")
            LOG.warning("Expected USB extender %s at %s but %s answered", mac, ip, query["mac"])
            return {"status": "MAC_MISMATCH", "mac": mac, "ip": ip, "actual_mac": query["mac"]}
        advanced_mid = self._message_id()
        try:
            advanced = parse_advanced_query_response(
                self._exchange(ip, build_advanced_query(advanced_mid), advanced_mid,
                               bind_ip=self._bind_source(interface_ip, mask, ip))[0], advanced_mid)
        except socket.timeout:
            return {"status": "ADVANCED_QUERY_FAILED", "mac": mac, "ip": ip, "reason": "timeout"}
        except InterfaceError:
            return {"status": "INTERFACE_UNAVAILABLE", "mac": mac, "ip": ip}
        except ProtocolError as exc:
            LOG.warning("Advanced Query from %s failed validation: %s", ip, exc)
            return {"status": "ADVANCED_QUERY_FAILED", "mac": mac, "ip": ip, "reason": "protocol_error"}
        except OSError:
            return {"status": "ADVANCED_QUERY_FAILED", "mac": mac, "ip": ip, "reason": "unreachable"}
        stored = self._upsert(query, interface_ip, mask, "ROUTE_STATE", advanced)
        return {
            "status": "OK", "mac": mac, "ip": query["ip"], "interface_ip": interface_ip,
            "device_type": advanced["device_type"],
            "paired_macs": list(advanced["paired_macs"]),
            "read_at": self.now(), "authoritative": True,
            "pairing_eligible": bool(stored.get("pairing_eligible")),
            "network_relation": stored.get("network_relation", "UNKNOWN"),
            # The endpoint's own network, so a caller can decide whether two
            # endpoints are compatible without re-reading the record.
            "subnet_mask": stored.get("subnet_mask", ""),
            "device_subnet_mask": stored.get("device_subnet_mask", ""),
            "gateway": stored.get("device_gateway") or stored.get("gateway", ""),
        }

    # ---------------- route transactions (NOT exposed over HTTP) ----------------
    # HARDWARE VALIDATION REQUIRED for every operation below: Pair/Unpair target
    # and direction, bidirectional Advanced Query propagation, propagation
    # timing and retry window, actual peer limits, already-owned AT-OMNI-324
    # behaviour, and pair persistence across reboot and IP change.
    def get_route_state(self, host_mac: str, device_mac: str) -> dict:
        """Fresh both-endpoint route state.

        ``routed`` is authoritative only when both endpoints were read in this
        call and agree.  Anything else reports UNVERIFIABLE or INCONSISTENT.
        """
        host = self.read_pairing(host_mac)
        device = self.read_pairing(device_mac)
        for role, read in (("host", host), ("device", device)):
            if read.get("status") != "OK":
                return {"status": "UNVERIFIABLE", "endpoint": role, "reason": read.get("status"),
                        "host": host, "device": device}
        if host["device_type"] != HOST_MODEL or device["device_type"] != DEVICE_MODEL:
            return {"status": "PROTOCOL_ERROR", "host": host, "device": device}
        # Two endpoints must share an IP network with each other for the extender
        # link to work. This is computed from their own addresses and masks --
        # never from the controller's subnet, which is irrelevant to whether the
        # pair can talk. `None` means the data does not establish an answer, and
        # is not treated as a refusal.
        compatible = endpoints_same_network(*endpoint_network(host), *endpoint_network(device))
        if compatible is False:
            return {"status": "NOT_ELIGIBLE", "reason": "ENDPOINTS_ON_DIFFERENT_NETWORKS",
                    "host": host, "device": device}
        if not host["pairing_eligible"] or not device["pairing_eligible"]:
            return {"status": "NOT_ELIGIBLE", "host": host, "device": device}
        host_lists_device = device["mac"] in host["paired_macs"]
        owner = device["paired_macs"][0] if device["paired_macs"] else None
        device_claims_host = owner == host["mac"]
        if host_lists_device != device_claims_host:
            return {"status": "INCONSISTENT", "host": host, "device": device, "owner": owner,
                    "host_lists_device": host_lists_device, "device_claims_host": device_claims_host}
        return {"status": "OK", "host": host, "device": device, "routed": host_lists_device, "owner": owner}

    def _verify_route(self, host_mac: str, device_mac: str, expect_routed: bool,
                      attempts: int = 1, delay: float = 0.0) -> tuple[dict, int]:
        """Re-read both endpoints until they agree with ``expect_routed``.

        ``attempts=1, delay=0`` reproduces a single immediate read, which is the
        production behaviour.  Bench validation raises these to measure whether
        pairing state propagates immediately or after a delay; the total wait is
        bounded by the caller.  Returns the last state and the attempts used.
        """
        attempts = max(1, int(attempts))
        state = {}
        for attempt in range(1, attempts + 1):
            state = self.get_route_state(host_mac, device_mac)
            if state.get("status") == "OK" and state.get("routed") is expect_routed:
                return state, attempt
            if attempt < attempts and delay > 0:
                time.sleep(delay)
        return state, attempts

    def _dual_command(self, state, builder, opcode, skip_roles=()):
        """Send a per-endpoint pairing command to both endpoints.

        Physical validation showed the AT-OMNI-311/324 Pair and Unpair commands
        update only the table of the device they are addressed to; neither one
        propagates to the peer. A complete route therefore needs the command on
        the host and on the device. Returns (per-endpoint details, first error).

        ``skip_roles`` names endpoints whose table already reflects the intended
        end state. A device rejects a command that re-asserts a relationship it
        already holds, so converging a half-applied route must address only the
        side that is missing it. A skipped endpoint is recorded, never rolled
        back, because this transaction did not change it.
        """
        details, failure = [], None
        for role, peer in (("host", "device"), ("device", "host")):
            endpoint, other = state[role], state[peer]
            if role in skip_roles:
                details.append({"role": role, "target_ip": endpoint["ip"], "target_mac": endpoint["mac"],
                                "peer_mac": other["mac"], "opcode": opcode, "ack": None,
                                "skipped": "already in the intended state"})
                continue
            message_id = self._message_id()
            result = self.command(endpoint["ip"], builder(message_id, other["mac"]), message_id,
                                  bind_ip=self._bind_source(endpoint.get("interface_ip"),
                                                            endpoint.get("subnet_mask", ""),
                                                            endpoint["ip"]))
            details.append({"role": role, "target_ip": endpoint["ip"], "target_mac": endpoint["mac"],
                            "peer_mac": other["mac"], "opcode": opcode,
                            "ack": bool(result.get("ok")), "error": result.get("error")})
            if not result.get("ok"):
                failure = result.get("error")
                break
        return details, failure

    def _rollback(self, state, commands, builder):
        # A skipped endpoint was not changed here, so it is never reversed.
        """Best-effort reversal of endpoints that accepted before a failure."""
        undone = []
        for entry in commands:
            if not entry.get("ack"):
                continue
            endpoint = state[entry["role"]]
            other = state["device" if entry["role"] == "host" else "host"]
            message_id = self._message_id()
            result = self.command(endpoint["ip"], builder(message_id, other["mac"]), message_id,
                                  bind_ip=self._bind_source(endpoint.get("interface_ip"),
                                                            endpoint.get("subnet_mask", ""),
                                                            endpoint["ip"]))
            undone.append({"role": entry["role"], "target_ip": endpoint["ip"], "ack": bool(result.get("ok"))})
        return undone

    @staticmethod
    def _satisfied_roles(state: dict, routed: bool) -> tuple:
        """Endpoints whose table already reflects the intended end state."""
        host, device = state.get("host") or {}, state.get("device") or {}
        host_peers = [normalize_mac(m) for m in host.get("paired_macs") or []]
        device_peers = [normalize_mac(m) for m in device.get("paired_macs") or []]
        try:
            host_has = normalize_mac(device.get("mac", "")) in host_peers
            device_has = normalize_mac(host.get("mac", "")) in device_peers
        except ProtocolError:
            return ()
        satisfied = []
        if host_has == routed:
            satisfied.append("host")
        if device_has == routed:
            satisfied.append("device")
        return tuple(satisfied)

    @staticmethod
    def _is_half_of(state: dict, host_mac: str, device_mac: str, routed: bool) -> bool:
        """Is this INCONSISTENT state simply a half-applied version of the goal?

        Only when the two endpoints disagree *about each other*: the device must
        either be unpaired or already owned by this very host. A device owned by
        someone else is a conflict, never something to converge.
        """
        if state.get("status") != "INCONSISTENT":
            return False
        host, device = state.get("host") or {}, state.get("device") or {}
        if not (host.get("authoritative") and device.get("authoritative")):
            return False
        owner = (device.get("paired_macs") or [None])[0]
        if owner is not None and normalize_mac(owner) != normalize_mac(host_mac):
            return False                       # owned elsewhere: a conflict
        host_has = normalize_mac(device_mac) in [normalize_mac(m) for m in host.get("paired_macs") or []]
        device_has = owner is not None
        # Exactly one side already reflects the goal.
        return (host_has != device_has) if routed else (host_has or device_has)

    def pair_route(self, host_mac: str, device_mac: str, verify_attempts: int = 1, verify_delay: float = 0.0) -> dict:
        with self._route_lock:
            state = self.get_route_state(host_mac, device_mac)
            # A route that exists on one endpoint only is converged toward the
            # requested end state rather than refused; Pair is per-endpoint and
            # idempotent, and both endpoints are verified afterwards.
            if self._is_half_of(state, host_mac, device_mac, routed=True):
                LOG.info("Converging a half-established route %s -> %s", host_mac, device_mac)
                state = {**state, "status": "OK", "routed": False, "owner": None,
                         "converged_from": "INCONSISTENT"}
            if state.get("status") != "OK":
                return state
            if state["routed"]:
                return {"status": "ALREADY_ROUTED", "host": state["host"], "device": state["device"]}
            if len(state["host"]["paired_macs"]) >= HOST_PEER_LIMIT:
                return {"status": "PEER_LIMIT", "peer_limit": HOST_PEER_LIMIT}
            if state["owner"] and normalize_mac(state["owner"]) != normalize_mac(host_mac):
                # The operator asked for this REX to be on this host. Move it.
                return self._reassign_route(host_mac, device_mac, normalize_mac(state["owner"]),
                                            verify_attempts, verify_delay)
            # HARDWARE VALIDATED: Pair is a per-endpoint operation. A command
            # updates only the table of the device it is addressed to, so a
            # complete route requires Pair on both endpoints.
            commands, failure = self._dual_command(state, build_pair, PAIR,
                                                   skip_roles=self._satisfied_roles(state, True))
            command_detail = commands
            if failure:
                # Roll back the endpoint that did accept, so a partial failure
                # does not leave a one-sided route behind.
                rollback = self._rollback(state, commands, build_unpair)
                return {"status": _COMMAND_STATUS.get(failure, "PROTOCOL_ERROR"),
                        "command": commands, "rollback": rollback}
            # ACK means acknowledged, not routed.  Only a fresh both-endpoint
            # read may promote this to VERIFIED_SUCCESS.
            verified, used = self._verify_route(host_mac, device_mac, True, verify_attempts, verify_delay)
            common = {"command": command_detail, "verification_attempts": used, "verification": verified}
            if verified.get("status") != "OK":
                return {"status": "COMMAND_ACCEPTED_UNVERIFIED", "reason": verified.get("status"), **common}
            return {"status": "VERIFIED_SUCCESS" if verified["routed"] else "VERIFICATION_FAILED", **common}

    def _reassign_route(self, host_mac: str, device_mac: str, previous_owner: str,
                        verify_attempts: int, verify_delay: float) -> dict:
        """Move a single-owner REX from its current host to the requested one.

        Runs under the route lock already held by :meth:`pair_route`. The old
        relationship is released with a specific Unpair naming only that peer, so
        the previous host keeps every other REX it owns. If the new pairing then
        fails, the original relationship is restored and the result says whether
        that restoration was verified -- an ACK is never enough.
        """
        LOG.info("Reassigning %s from %s to %s", device_mac, previous_owner, host_mac)
        before = self.read_pairing(previous_owner)
        previous_peers = list(before.get("paired_macs") or []) if before.get("authoritative") else None

        release = self.unpair_route(previous_owner, device_mac, verify_attempts, verify_delay)
        if release.get("status") not in ("VERIFIED_SUCCESS", "ALREADY_UNROUTED"):
            # Nothing was taken from the previous owner, so there is nothing to
            # restore; the requested route simply did not happen.
            return {"status": "REASSIGN_RELEASE_FAILED", "owner_mac": previous_owner,
                    "reason": release.get("status"), "release": release}

        # The REX is free now, so the ordinary path applies: it will converge,
        # skip an endpoint that already holds the peer, and verify both sides.
        outcome = self.pair_route(host_mac, device_mac, verify_attempts, verify_delay)
        outcome = dict(outcome)
        outcome["reassigned_from"] = previous_owner
        outcome["release"] = release
        if outcome.get("status") in ("VERIFIED_SUCCESS", "ALREADY_ROUTED"):
            outcome["previous_owner_peers"] = previous_peers
            return outcome

        # The new pairing failed after the old one was released. Put it back.
        restore = self.pair_route(previous_owner, device_mac, verify_attempts, verify_delay)
        restored = restore.get("status") in ("VERIFIED_SUCCESS", "ALREADY_ROUTED")
        outcome["restore"] = restore
        outcome["status"] = "FAILED_ROLLED_BACK" if restored else "FAILED_ROLLBACK_UNVERIFIED"
        outcome["reason"] = outcome.get("status")
        if previous_peers is not None:
            after = self.read_pairing(previous_owner)
            outcome["previous_owner_peers"] = list(after.get("paired_macs") or [])
            outcome["previous_owner_intact"] = (
                after.get("authoritative") is True
                and {normalize_mac(m) for m in previous_peers}
                == {normalize_mac(m) for m in after.get("paired_macs") or []})
        return outcome

    def unpair_route(self, host_mac: str, device_mac: str, verify_attempts: int = 1, verify_delay: float = 0.0) -> dict:
        with self._route_lock:
            state = self.get_route_state(host_mac, device_mac)
            # Likewise: clearing a route that survives on one endpoint only is
            # exactly what removal means, so it is converged rather than refused.
            if self._is_half_of(state, host_mac, device_mac, routed=False):
                LOG.info("Clearing a half-established route %s -> %s", host_mac, device_mac)
                state = {**state, "status": "OK", "routed": True,
                         "owner": normalize_mac(host_mac), "converged_from": "INCONSISTENT"}
            if state.get("status") != "OK":
                return state
            if not state["routed"]:
                return {"status": "ALREADY_UNROUTED", "host": state["host"], "device": state["device"]}
            # HARDWARE VALIDATED: Unpair is likewise per-endpoint, so both sides
            # must be cleared. Always the specific Unpair; UNPAIR_ALL is never
            # sent from here.
            commands, failure = self._dual_command(state, build_unpair, UNPAIR,
                                                   skip_roles=self._satisfied_roles(state, False))
            command_detail = commands
            if failure:
                rollback = self._rollback(state, commands, build_pair)
                return {"status": _COMMAND_STATUS.get(failure, "PROTOCOL_ERROR"),
                        "command": commands, "rollback": rollback}
            verified, used = self._verify_route(host_mac, device_mac, False, verify_attempts, verify_delay)
            common = {"command": command_detail, "verification_attempts": used, "verification": verified}
            if verified.get("status") != "OK":
                return {"status": "COMMAND_ACCEPTED_UNVERIFIED", "reason": verified.get("status"), **common}
            return {"status": "VERIFIED_SUCCESS" if not verified["routed"] else "VERIFICATION_FAILED", **common}

    # ---------------- device operations ----------------
    def configure_network(self, mac: str, mode: str, interface_ip: str, interface_mask: str, address: str = "", netmask: str = "", gateway: str = "") -> dict:
        device = self.device(mac)
        if not device:
            return {"status": "not_found"}
        message_id = self._message_id()
        try:
            if mode == "dhcp":
                request = build_ip_dhcp(message_id, device["mac"])
                expected_ip = device.get("ip", "")
            elif mode == "static":
                expected_ip, netmask, gateway = validate_static_network(address, netmask, gateway)
                request = build_ip_static(message_id, device["mac"], expected_ip, netmask, gateway)
            else:
                return {"status": "invalid_request"}
        except ProtocolError:
            return {"status": "invalid_request"}
        if network_relation(interface_ip, interface_mask, expected_ip or device.get("ip", "")) == "UNKNOWN":
            return {"status": "invalid_request"}
        broadcast = str(ipaddress.IPv4Network(f"{interface_ip}/{interface_mask}", strict=False).broadcast_address)
        command_result = self.command(broadcast, request, message_id, bind_ip=interface_ip)
        if not command_result.get("ok"):
            return {"status": "command_rejected" if command_result.get("error") == "device rejected command" else command_result.get("error", "protocol_error")}
        verify_ip = expected_ip if mode == "static" else device.get("ip", "")
        verification = self.discover_ip(verify_ip, interface_ip, interface_mask, "NETWORK_CONFIG_VERIFY", device["mac"])
        verified = verification.get("status") == "found"
        return {"status": "rediscovery_verified" if verified else "command_accepted_unverified",
                "command_accepted": True, "rediscovery_verified": verified,
                "verification_status": verification.get("status"),
                "device": verification.get("device") if verified else None}

    def blink(self, mac: str, enabled: bool) -> dict:
        context, error = self._command_context(mac)
        if error:
            return error
        device, ip, interface_ip = context
        message_id = self._message_id()
        result = self.command(ip, build_blink(message_id, enabled), message_id,
                              bind_ip=self._bind_source(interface_ip, device.get('subnet_mask', ''), ip))
        return {"status": "command_accepted" if result.get("ok") else result.get("error", "protocol_error"),
                "command_accepted": bool(result.get("ok"))}

    def identify(self, mac: str, duration: float = 5.0) -> dict:
        try:
            duration = float(duration)
        except (TypeError, ValueError):
            return {"status": "invalid_request"}
        if duration <= 0 or duration > 30:
            return {"status": "invalid_request"}
        result = self.blink(mac, True)
        if not result.get("command_accepted"):
            return result
        normalized = normalize_mac(mac)

        def turn_off():
            try:
                self.blink(normalized, False)
            finally:
                with self._lock:
                    self._identify_timers.pop(normalized, None)

        with self._lock:
            existing = self._identify_timers.pop(normalized, None)
            if existing:
                existing.cancel()
            timer = threading.Timer(duration, turn_off)
            timer.daemon = True
            self._identify_timers[normalized] = timer
            timer.start()
        return {"status": "command_accepted", "command_accepted": True, "auto_off_seconds": duration}

    def reboot(self, mac: str) -> dict:
        context, error = self._command_context(mac)
        if error:
            return error
        device, ip, interface_ip = context
        message_id = self._message_id()
        result = self.command(ip, build_reboot(message_id), message_id,
                              bind_ip=self._bind_source(interface_ip, device.get('subnet_mask', ''), ip))
        return {"status": "command_accepted" if result.get("ok") else result.get("error", "protocol_error"),
                "command_accepted": bool(result.get("ok"))}
