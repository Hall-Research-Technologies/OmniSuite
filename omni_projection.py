"""What a restricted user is allowed to *know*, as distinct from what they may do.

The Phase D gate answers "may this account perform this action". It does not
answer "may this account see that this device exists", and those are different
questions. A teacher authorised to route one classroom must not receive an
inventory of the building merely because the page needs to draw two lists.

So this is a projection, not a filter, and the distinction is the whole point:

  * A filter takes the administrative payload and removes what the user may not
    see. Anything added to that payload later is exposed by default, and the
    only thing standing between a new field and a remote browser is whoever
    remembers to add it to a deny list.

  * A projection builds a new object out of named fields. A field nobody added
    here cannot appear, however the administrative model grows.

A decoder row in /api/state carries 57 fields -- video wall geometry, HDCP
state, EDID, fast-switching, multicast addresses, serial numbers, firmware. A
user needs a name, whether it is online, and what it is showing. Everything
else is engineering detail they neither need nor should hold.

No device I/O happens here. The projection is computed from host-authoritative
state the application has already gathered, so ten browsers cost the same
device traffic as none.
"""
from __future__ import annotations

from omni_users import _normalise_identity

#: Devices a user may act on are named by the identity the permission model
#: uses, never by address. The projection therefore has to join the matrix view
#: (keyed by IP) to the device cache (which carries the MAC), and it emits the
#: MAC as the id so the browser never learns an address at all.
IDENTITY_FIELD = "mac"


def _identity_by_address(units) -> dict:
    """IP -> normalised hardware identity, from the authoritative cache."""
    index = {}
    for unit in units or []:
        address = (unit.get("ip") or "").strip()
        identity = _normalise_identity(unit.get("mac"))
        if address and identity:
            index[address] = identity
    return index


def disambiguate(items, detail_of) -> None:
    """Make repeated labels distinguishable, and leave unique ones alone.

    Two rooms may each legitimately hold a "Front Display", so duplicates are
    not prevented -- but an operator shown two identical cards cannot choose
    between them. Where a label repeats *within what this account can see*, the
    device's own name is appended:

        Front Display                     (unique -- untouched)
        Front Display - dec-front-101     (ambiguous)
        Front Display - dec-front-102

What gets appended is decided by `distinguisher`: the device's own
    location where it has one, then its description, then its hostname. A site
    that has filled in `location` on its OmniStream units therefore gets

        Front Display — Room 101
        Front Display — Room 102

    and a site that has not still gets something that distinguishes.

    An address or MAC is deliberately not used. Those would disambiguate, and
    they are exactly the engineering detail the whole projection exists to keep
    out of an operator's hands.

    None of this is identity. A device whose location changes is the same
    device, with the same permissions, the same alias and the same routing
    identity; only the words after the dash change.

    Ambiguity is judged over the account's own visible set, so a duplicate in
    another room the account cannot see never changes what it is shown.
    """
    seen: dict = {}
    for item in items:
        seen.setdefault(item["name"].strip().casefold(), []).append(item)
    for group in seen.values():
        if len(group) < 2:
            continue
        for item in group:
            detail = str(detail_of(item) or "").strip()
            if detail and detail.casefold() != item["name"].strip().casefold():
                item["name"] = f"{item['name']} — {detail}"


def _systeminfo(unit) -> dict:
    """The device's own systeminfo configuration, or an empty mapping.

    Every probe already fetches this and every cached unit already carries it,
    so reading it here costs nothing and adds no device round trip.
    """
    details = (unit or {}).get("details") or {}
    return ((details.get("systeminfo") or {}).get("config") or {})


def device_location(unit) -> str:
    """Where the device says it is. Device-provided, never written by us.

    Read from the cached record first and from systeminfo underneath it, so a
    unit discovered before this field was promoted still answers. Empty on a
    device nobody has filled in, which is the common case.
    """
    for candidate in ((unit or {}).get("location"), _systeminfo(unit).get("location")):
        text = str(candidate or "").strip()
        if text:
            return text
    return ""


def device_description(unit) -> str:
    """The device's own free-text description. Same provenance as location."""
    for candidate in ((unit or {}).get("description"),
                      _systeminfo(unit).get("description")):
        text = str(candidate or "").strip()
        if text:
            return text
    return ""


def distinguisher(row, unit) -> str:
    """What to append when two devices share a label.

    Precedence is human-readability, not availability: a room number tells an
    operator which display is in front of them, a description sometimes does,
    and a hostname always distinguishes but says nothing. So location first,
    description second, the device's own name last.

    All three are things the device reports about itself. An address or a MAC
    would also distinguish, and neither is ever used: they are exactly the
    engineering detail the projection exists to keep out of an operator's
    hands.
    """
    return (device_location(unit) or device_description(unit)
            or _device_name(row, unit))


def _device_name(row, unit) -> str:
    """The device's own name: hostname, or the model if it has none.

    This is the fallback, not the label. It is what OmniSuite has always shown
    and what a device with no alias still shows.
    """
    for candidate in (row.get("host"), row.get("hostname"),
                      (unit or {}).get("hostname"), (unit or {}).get("name")):
        text = str(candidate or "").strip()
        if text:
            return text
    model = str(row.get("model") or (unit or {}).get("model") or "").strip()
    return model or "Unnamed device"


def _friendly_name(row, unit, identity, aliases) -> str:
    """What to call a device on screen.

    An administrator's alias wins -- "Document Camera" rather than
    "at-omni-111-04553" -- and a device without one reads exactly as it did
    before aliases existed. The alias is looked up *by identity*; there is no
    path from a name back to a device anywhere in the projection, which is what
    keeps a label from ever behaving like an identifier.
    """
    fallback = _device_name(row, unit)
    if aliases is None:
        return fallback
    return aliases.display_name(identity, fallback)


def _is_online(row, unit) -> bool:
    """Whether the host currently believes the device is reachable.

    Read from state the application already holds. A device the user is
    permitted to use but which is offline is still shown -- hiding it would
    tell the operator their source has been taken away rather than that it is
    not answering.
    """
    for source in (row, unit or {}):
        for key in ("online", "reachable"):
            if key in source:
                return bool(source.get(key))
    # Nothing recorded either way. The matrix lists devices discovery found, so
    # presence in the list is the best evidence available.
    return True


def project_source(row, unit, identity, aliases=None) -> dict:
    """One encoder, as a user may see it. Named fields only."""
    return {
        "id": identity,
        "name": _friendly_name(row, unit, identity, aliases),
        "online": _is_online(row, unit),
    }


def project_destination(row, unit, identity, showing, aliases=None) -> dict:
    """One decoder, as a user may see it.

    `showing` describes what is on that display in the user's own terms -- the
    name of a permitted source, or that it is showing something else -- never a
    multicast address or an input number.
    """
    return {
        "id": identity,
        "name": _friendly_name(row, unit, identity, aliases),
        "online": _is_online(row, unit),
        "showing": showing,
    }


def describe_showing(decoder_row, source_identity, source_names, permitted_sources):
    """What a display is currently showing, for a user.

    Four honest answers, and none of them leaks a device the account may not
    see:

      none          nothing is routed to it
      multiview     it is compositing, which this checkpoint does not let a
                    user operate
      source        it is showing a source the account is permitted to see
      other         it is showing something, but not one of this account's
                    sources -- said plainly rather than named, because naming
                    it would disclose a device outside the account's world
    """
    if decoder_row.get("multiview_active"):
        return {"mode": "multiview", "source_id": None,
                "source_name": decoder_row.get("multiview_name") or "Multiview"}
    if not source_identity:
        return {"mode": "none", "source_id": None, "source_name": None}
    if source_identity in permitted_sources:
        return {"mode": "source", "source_id": source_identity,
                "source_name": source_names.get(source_identity) or "Source"}
    return {"mode": "other", "source_id": None, "source_name": None}


def user_state(matrix_state, units, account, aliases=None) -> dict:
    """The whole of what a restricted account receives about the deployment.

    `matrix_state` is the administrative view the application already computes;
    `units` is the device cache it is derived from. Neither is forwarded. This
    reads them and builds something new.

    An administrator is given the same shape so the user shell can be reviewed
    by an admin without a second code path, but without any permission filter:
    an admin may see everything, and the projection is about visibility, not
    about pretending an admin is restricted.
    """
    permissions = account.permissions
    admin = bool(getattr(account, "is_admin", False))

    identity_of = _identity_by_address(units)
    unit_of = {(u.get("ip") or "").strip(): u for u in (units or [])}
    # How to tell two identical labels apart -- the device's own location
    # where it reports one, otherwise its description, otherwise its name.
    # Kept beside the label so a repeat can be resolved without the projection
    # ever carrying an address, and never sent for a device this account
    # cannot already see: only permitted rows are recorded here.
    device_names = {}

    encoders = matrix_state.get("encoders") or []
    decoders = matrix_state.get("decoders") or []
    routes = matrix_state.get("routes") or {}

    # ---- sources -----------------------------------------------------
    sources = []
    source_names = {}
    permitted_sources = set()
    for row in encoders:
        address = (row.get("ip") or "").strip()
        identity = identity_of.get(address)
        if not identity:
            # A device with no stable identity cannot be permitted, because a
            # permission naming it could not be written. Omitted rather than
            # offered under its address.
            continue
        if not (admin or permissions.may_use_source(identity)):
            continue
        unit = unit_of.get(address)
        projected = project_source(row, unit, identity, aliases)
        device_names[identity] = distinguisher(row, unit)
        sources.append(projected)
        source_names[identity] = projected["name"]
        permitted_sources.add(identity)

    # Names for route resolution have to cover permitted sources only; a
    # display showing somebody else's camera reports "other", not its name.
    # ---- destinations ------------------------------------------------
    destinations = []
    for row in decoders:
        address = (row.get("ip") or "").strip()
        identity = identity_of.get(address)
        if not identity:
            continue
        if not (admin or permissions.may_control_destination(identity)):
            continue
        routed_address = routes.get(address)
        routed_identity = identity_of.get((routed_address or "").strip())
        showing = describe_showing(row, routed_identity, source_names,
                                   permitted_sources)
        device_names[identity] = distinguisher(row, unit_of.get(address))
        destinations.append(project_destination(row, unit_of.get(address),
                                                identity, showing, aliases))

    # Only where a label actually repeats in this account's own view.
    disambiguate(sources, lambda s: device_names.get(s["id"], ""))
    disambiguate(destinations, lambda d: device_names.get(d["id"], ""))

    sources.sort(key=lambda s: s["name"].lower())
    destinations.sort(key=lambda d: d["name"].lower())

    return {
        "ok": True,
        # The account's own grants, so the shell knows which surfaces to offer.
        # Never the policy for anybody else, and never the permission table.
        "features": sorted(permissions.features) if not admin else ["av", "usb",
                                                                    "multiview"],
        "role": "admin" if admin else "user",
        "username": account.username,
        "sources": sources,
        "destinations": destinations,
    }


def session_summary(account) -> dict:
    """Who the browser is logged in as. No permission detail beyond the grants."""
    admin = bool(getattr(account, "is_admin", False))
    return {
        "ok": True,
        "username": account.username,
        "role": "admin" if admin else "user",
        "features": (["av", "usb", "multiview"] if admin
                     else sorted(account.permissions.features)),
    }


# ===========================================================================
# USB
# ===========================================================================
#
# USB extension is not source-to-destination in the A/V sense, and forcing that
# language onto it would misdescribe the hardware. A LEX sits with the host
# computer; a REX sits with the peripherals. A route means "the keyboard, mouse
# and camera plugged into that REX are available to the computer at that LEX".
#
# So the operator-facing words are "computer" and "peripherals", and the
# permission model maps onto grants an administrator has already made:
#
#     integrated LEX  -> lives on an encoder  -> governed by `sources`
#     integrated REX  -> lives on a decoder   -> governed by `destinations`
#     standalone      -> is its own device    -> governed by its own identity
#
# That is not a convenience. Whoever may show the teacher PC on the front
# display is whoever may give that PC the front display's peripherals, and a
# second parallel set of USB grants would be a second thing to keep in step.

def _usb_endpoint_identity(row) -> str:
    """The stable identity of a USB endpoint: its own MAC."""
    return _normalise_identity(row.get("mac") or row.get("usb_mac"))


def _usb_authorising_identity(row, parent_of_address) -> str:
    """Which device's permission governs this endpoint.

    An integrated endpoint is part of its parent OmniStream unit, so the
    parent's grant governs it. A standalone extender is its own device and
    stands on its own identity.
    """
    address = (row.get("ip") or "").strip()
    parent = parent_of_address.get(address)
    return parent or _usb_endpoint_identity(row)


def project_usb_state(usb_state, units, account, aliases=None) -> dict:
    """The USB endpoints an account may operate, and what they are joined to.

    Built from named fields, like every other user projection. The
    administrative payload carries firmware revisions, subnet masks, host port
    numbers, filter settings, protocol revisions and discovery provenance for
    every endpoint on the network. None of that helps somebody give a classroom
    computer its peripherals, and none of it is here.
    """
    permissions = account.permissions
    admin = bool(getattr(account, "is_admin", False))

    identity_of = _identity_by_address(units)
    unit_of = {(u.get("ip") or "").strip(): u for u in (units or [])}

    def visible(row, permitted):
        governing = _usb_authorising_identity(row, identity_of)
        return admin or (governing and governing in permitted)

    def describe(row):
        identity = _usb_endpoint_identity(row)
        governing = _usb_authorising_identity(row, identity_of)
        address = (row.get("ip") or "").strip()
        unit = unit_of.get(address)
        # Named after the device an operator recognises -- the encoder or
        # decoder it belongs to -- rather than the USB endpoint's own MAC.
        fallback = (_device_name({"host": row.get("host")}, unit) if unit
                    else (row.get("host") or "USB device"))
        name = (aliases.display_name(governing, fallback) if aliases
                else fallback)
        return {
            "id": identity,
            "name": name,
            "online": bool(row.get("usb_discovered", True)),
        }

    usb_device_names = {}

    def remember(row):
        identity = _usb_endpoint_identity(row)
        address = (row.get("ip") or "").strip()
        unit = unit_of.get(address)
        usb_device_names[identity] = (
            distinguisher({"host": row.get("host")}, unit)
            if unit else (row.get("host") or ""))

    computers, peripherals = [], []
    for row in (usb_state.get("matrix_lex") or usb_state.get("lex") or []):
        if visible(row, permissions.sources):
            remember(row)
            computers.append(describe(row))
    for row in (usb_state.get("matrix_rex") or usb_state.get("rex") or []):
        if visible(row, permissions.destinations):
            remember(row)
            peripherals.append(describe(row))

    # What is joined to what, expressed only between endpoints this account can
    # already see. Peripherals attached to somebody else's computer say so
    # without naming it, exactly as a display showing an unauthorised source
    # does.
    visible_computers = {c["id"]: c["name"] for c in computers}
    joined = {}
    for key, record in (usb_state.get("standalone_routes") or {}).items():
        target = _normalise_identity(key)
        if not target:
            continue
        active = _normalise_identity((record or {}).get("active"))
        if not active:
            joined[target] = {"mode": "none", "computer_id": None,
                              "computer_name": None}
        elif active in visible_computers:
            joined[target] = {"mode": "computer", "computer_id": active,
                              "computer_name": visible_computers[active]}
        else:
            joined[target] = {"mode": "other", "computer_id": None,
                              "computer_name": None}

    for peripheral in peripherals:
        peripheral["attached_to"] = joined.get(
            peripheral["id"], {"mode": "none", "computer_id": None,
                               "computer_name": None})

    disambiguate(computers, lambda c: usb_device_names.get(c["id"], ""))
    disambiguate(peripherals, lambda p: usb_device_names.get(p["id"], ""))

    computers.sort(key=lambda c: c["name"].lower())
    peripherals.sort(key=lambda p: p["name"].lower())
    return {"ok": True, "computers": computers, "peripherals": peripherals}


# ===========================================================================
# Multiview
# ===========================================================================

#: Window positions in words an operator can act on. The device's own cell
#: names are geometry; these are what somebody looking at a screen would say.
_WINDOW_LABELS = {
    "main": "Main", "top_left": "Top left", "top_right": "Top right",
    "bottom_left": "Bottom left", "bottom_right": "Bottom right",
    "top_middle": "Top middle", "bottom_middle": "Bottom middle",
    "left": "Left", "right": "Right", "top": "Top", "bottom": "Bottom",
    "pip": "Inset",
}


def _window_label(cell) -> str:
    key = str(cell or "").strip().lower()
    if key in _WINDOW_LABELS:
        return _WINDOW_LABELS[key]
    return key.replace("_", " ").capitalize() or "Window"


def project_multiview_state(decoder_identity, decoder_name, state, units,
                            account, aliases=None) -> dict:
    """The saved Multiviews an account may operate on one permitted display.

    Two rules decide what appears, and both have to hold:

      * the preset is approved for this account on this decoder, and
      * every source the preset currently has assigned is one this account is
        already permitted to use.

    The second is what makes the first safe. A saved Multiview has no stable id
    -- its identity is its name on a decoder -- so an administrator who deletes
    one and creates a different preset under the same name would carry the
    approval across. Re-validating the content at operation time means the
    worst that can happen is a preset the account cannot show, rather than one
    it can show containing equipment it was never granted.

    A preset holding an unauthorised source is omitted entirely. Listing it as
    unavailable would still confirm that this account's own display is wired to
    something in another room.
    """
    permissions = account.permissions
    admin = bool(getattr(account, "is_admin", False))
    identity_of = _identity_by_address(units)
    unit_of = {(u.get("ip") or "").strip(): u for u in (units or [])}

    def source_view(subframe):
        source = (subframe or {}).get("source") or {}
        address = (source.get("ip") or "").strip()
        identity = identity_of.get(address)
        if not identity:
            return None, {"id": None, "name": None}
        unit = unit_of.get(address)
        fallback = _device_name({"host": source.get("hostname")}, unit)
        name = aliases.display_name(identity, fallback) if aliases else fallback
        return identity, {"id": identity, "name": name}

    presets = []
    for entry in (state.get("multiviews") or []):
        name = str(entry.get("name") or "")
        if not (admin or permissions.may_operate_multiview(decoder_identity, name)):
            continue

        windows, permitted = [], True
        for subframe in (entry.get("subframes") or []):
            identity, described = source_view(subframe)
            if identity and not (admin or permissions.may_use_source(identity)):
                permitted = False
                break
            windows.append({
                "cell": subframe.get("cell") or "",
                "label": _window_label(subframe.get("cell") or ""),
                "source": described if identity else {"id": None, "name": None},
                "live": (subframe.get("health") or "") == "live",
            })
        if not permitted:
            continue

        presets.append({
            "name": name,
            "label": entry.get("layout_label") or name,
            "showing": bool(entry.get("selected_on_output")),
            "available": bool(entry.get("showable")),
            "windows": windows,
        })

    presets.sort(key=lambda p: p["label"].lower())
    return {"ok": True,
            "display": {"id": decoder_identity, "name": decoder_name},
            "presets": presets}
