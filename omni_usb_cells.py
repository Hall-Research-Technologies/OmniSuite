"""One derivation of every USB Matrix cell, shared by the user projection and the user route.

The Admin USB Matrix decides what each dot may do in two places: the server states what the
two endpoints' families and networks allow (``capabilities``), and the page then applies three
more rules it holds itself -- a management-address check, a peer limit and the routed state. A
second implementation of those rules for Remote Users would drift, so this module restates the
page's rules once, in Python, from the SAME administrative payload, and both user surfaces
(what the projection shows and what the route endpoint will do) consume the result.

``derive`` is pure: it reads a USB state mapping and returns records. It performs no I/O, so the
routing decision can be made on state read during the current operation and tested without a
device.

The page's rules, in the page's order (``ui/matrix/usb.js`` ``render``):

  1. the cell's capability, from the server (family pairing table, ownership, liveness,
     identity, same USB network). A disabled capability disables the cell -- even one that is
     already routed, exactly as the Admin page does;
  2. an integrated cell that is not already routed is refused when the two units' management
     addresses are in different /24 networks (the Admin page's established presentation rule;
     the same-USB-network proof is rule 1 and is enforced again by the pairing path itself);
  3. a cell that is not already routed is refused when the host already holds its peer limit
     (5 for an integrated pairing, 7 for a UDP-controlled one);
  4. routed state: a UDP-controlled cell reads the UDP-observed owner and matches by canonical
     USB MAC; any other cell reads the usb_icron pairing table keyed by control address.

Nothing here invents a rule.
"""
from __future__ import annotations

import re

import omni_users

_IPV4 = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")
_MAC = re.compile(r"\b(?:[0-9a-fA-F]{2}[:-]){5}[0-9a-fA-F]{2}\b")

PEER_LIMIT_INTEGRATED = 5
PEER_LIMIT_STANDALONE = 7


def _identity(row) -> str:
    """The stable identity of a USB endpoint: its own MAC, in one spelling."""
    row = row or {}
    return omni_users._normalise_identity(row.get("mac") or row.get("usb_mac"))


def _subnet24(address) -> str:
    m = re.match(r"^\s*(\d+)\.(\d+)\.(\d+)\.(\d+)\s*$", str(address or ""))
    return "%s.%s.%s" % m.group(1, 2, 3) if m else ""


def _different_management_subnet(a, b) -> bool:
    left, right = _subnet24(a), _subnet24(b)
    return bool(left) and bool(right) and left != right


def clean_text(text) -> str:
    """No address survives into a sentence an operator's browser receives."""
    return _MAC.sub("that device", _IPV4.sub("that device", str(text or ""))).strip()


def _peer_counts(pairings):
    counts = {}
    for pairing in (pairings or {}).values():
        if not pairing:
            continue
        peers = []
        if pairing.get("active"):
            peers.append(pairing["active"])
        peers.extend(pairing.get("available") or [])
        for peer in set(peers):
            counts[peer] = counts.get(peer, 0) + 1
    return counts


def derive(state) -> dict:
    """Every cell of the administrative matrix, keyed ``(rex_identity, lex_identity)``.

    Each record carries the public fields (``public``) and the internal ones the route needs
    (``path``, ``rex_key``, ``lex_key``, ``rex_mac``, ``lex_mac``, ``routed``, ``can_act``).
    The internal ones never leave the server.
    """
    state = state or {}
    lex_rows = state.get("matrix_lex") or state.get("lex") or []
    rex_rows = state.get("matrix_rex") or state.get("rex") or []
    capabilities = state.get("capabilities") or {}
    pairings = state.get("pairings") or {}
    udp_routes = state.get("standalone_routes") or {}

    lex_counts = _peer_counts(pairings)
    udp_counts = {}
    for route in udp_routes.values():
        if route and route.get("active"):
            key = str(route["active"]).upper()
            udp_counts[key] = udp_counts.get(key, 0) + 1

    cells = {}
    for rex in rex_rows:
        rex_key = rex.get("usb_key") or rex.get("ip")
        standalone_rex = rex.get("kind") == "standalone"
        rex_pairing = ((udp_routes.get(rex_key) if standalone_rex else pairings.get(rex.get("ip")))
                       or {"active": None, "available": []})
        active_lex = rex_pairing.get("active")
        available_lex = rex_pairing.get("available") or []
        for lex in lex_rows:
            lex_key = lex.get("usb_key") or lex.get("ip")
            cap = (capabilities.get(rex_key) or {}).get(lex_key) or {
                "state": "UNSUPPORTED_MIXED", "control_path": "", "enabled": False,
                "label": "Not routable", "detail": "This combination has not been validated."}
            path = cap.get("control_path") or ""
            udp_cell = path == "standalone_udp"
            udp_route = udp_routes.get(rex_key) if udp_cell else None
            lex_mac = str(lex.get("usb_mac") or "").upper()
            if udp_route is not None:
                active = bool(udp_route.get("active") and lex_mac
                              and str(udp_route["active"]).upper() == lex_mac)
                available = False
            else:
                active = active_lex == lex_key
                available = lex_key in available_lex
            routed = active or available

            subnet_blocked = (not udp_cell and not routed
                              and _different_management_subnet(rex.get("ip"), lex.get("ip")))
            limit = PEER_LIMIT_STANDALONE if udp_cell else PEER_LIMIT_INTEGRATED
            count = udp_counts.get(lex_mac, 0) if udp_cell else lex_counts.get(lex_key, 0)
            over_limit = not routed and count >= limit
            # Both refusals above are defined as "not already routed and ...", so a routed cell is
            # never refused by them: it stays actionable (it can be removed) while its capability allows.
            can_act = bool(cap.get("enabled")) and not subnet_blocked and not over_limit

            experimental = bool(cap.get("enabled") and cap.get("mixed")
                                and cap.get("data_plane_verified") is False)
            if not cap.get("enabled"):
                label = cap.get("label") or "Not routable"
                detail = cap.get("detail") or ""
                reason = str(cap.get("state") or "unsupported").lower()
            elif subnet_blocked:
                label, detail = "Not routable on current network", "These two devices are on different subnets."
                reason = "management_subnet_mismatch"
            elif over_limit:
                label = "Peer limit reached"
                detail = "That computer already has its maximum of %d peripherals." % limit
                reason = "peer_limit"
            elif routed:
                label = "Connected"
                detail = ("Mixed USB route -- data transport validation pending. Click to remove this route."
                          if experimental else "Click to remove this route.")
                reason = ""
            else:
                label = "Available"
                detail = ("Mixed USB route -- data transport not yet validated. Click to create this route."
                          if experimental else "Click to create this route.")
                reason = ""
            stale = bool(udp_cell and ((udp_route.get("fresh") is False) if udp_route is not None
                                       else rex_pairing.get("fresh") is False))
            if stale:
                detail = (detail + " " if detail else "") + "Pairing state stale; awaiting refresh."

            rex_mac, lex_identity = _identity(rex), _identity(lex)
            cells[(rex_mac, lex_identity)] = {
                "public": {
                    "state": "blocked" if not can_act else ("routed" if routed else "available"),
                    "routed": routed,
                    "active": active,
                    "enabled": can_act,
                    "reason": reason,
                    "label": clean_text(label),
                    "detail": clean_text(detail),
                    "stale": stale,
                },
                "path": path,
                "routed": routed,
                "can_act": can_act,
                "rex_key": rex_key,
                "lex_key": lex_key,
                "rex_mac": rex_mac,
                "lex_mac": lex_identity,
            }
    return cells
