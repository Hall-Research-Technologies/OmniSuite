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

import omni_access
import omni_users
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
    "at-omni-111-00001" -- and a device without one reads exactly as it did
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


def user_state(matrix_state, units, account, aliases=None, groups=None) -> dict:
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

    # ---- display groups ----------------------------------------------
    # A group is its own resource: the account sees the groups it holds, the
    # number of displays in each, and never the members. Holding a group does
    # not make its members individually visible or controllable.
    group_rows = []
    if groups is not None:
        for group in groups.all():
            if not admin and not permissions.may_control_group(group["id"]):
                continue
            group_rows.append({"id": group["id"], "name": group["name"],
                               "displays": len(group["members"])})
    group_rows.sort(key=lambda r: (r["name"].lower(), r["id"]))

    return {
        "ok": True,
        # The account's own grants, so the shell knows which surfaces to offer.
        # Never the policy for anybody else, and never the permission table.
        "features": sorted(permissions.features) if not admin
        else omni_access.user_features(),
        "role": "admin" if admin else "user",
        "username": account.username,
        "sources": sources,
        "destinations": destinations,
        "groups": group_rows,
        "interfaces": routing_interfaces(account),
        "revision": permission_revision(account, groups, aliases),
    }


def routing_interfaces(account) -> list:
    """Which routing interfaces this account may use, as a sorted list.

    An administrator is offered both, as they are offered every feature: the
    grant is about what a restricted account may be given, and an
    administrator's access is not built from grants.
    """
    if bool(getattr(account, "is_admin", False)):
        return sorted(omni_users.INTERFACES)
    return sorted(account.permissions.interfaces)


def permission_revision(account, groups=None, aliases=None) -> str:
    """A short digest of everything that decides what an account may see.

    Grants, role, enabled state, each granted group's current members and the
    friendly names. A page compares it with the one it drew from; a change
    means "read again". It is a hint for rendering and never an authorisation
    input: the server decides every request from the account as it is then.
    """
    import hashlib
    p = account.permissions
    parts = [str(getattr(account, "role", "")),
             str(bool(getattr(account, "enabled", True))),
             "f:" + ",".join(sorted(p.features)),
             "i:" + ",".join(sorted(p.interfaces)),
             "s:" + ",".join(sorted(p.sources)),
             "d:" + ",".join(sorted(p.destinations))]
    if groups is not None:
        for gid in sorted(p.groups):
            g = groups.get(gid)
            parts.append("g:%s:%s" % (gid, ",".join(sorted(g["members"]))
                                      if g else "-"))
    else:
        parts.append("g:" + ",".join(sorted(p.groups)))
    names = getattr(aliases, "all", None)
    if callable(names):
        parts.append("a:" + repr(sorted(names().items())))
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]


def session_summary(account) -> dict:
    """Who the browser is logged in as. No permission detail beyond the grants."""
    admin = bool(getattr(account, "is_admin", False))
    return {
        "ok": True,
        "username": account.username,
        "role": "admin" if admin else "user",
        "features": (omni_access.user_features() if admin
                     else sorted(account.permissions.features)),
        "interfaces": routing_interfaces(account),
    }


# ===========================================================================
# The restricted Matrix
# ===========================================================================
#
# Built from the same permitted lists the Standard page draws -- user_state is
# called, not repeated -- and then given the one thing a grid needs that two
# lists do not: what each row is carrying right now, per plane. Nothing here is
# the administrative matrix with parts hidden. A source or display the account
# does not hold is not a column or a row that is greyed out; it is absent, and
# so is the name of whatever it happens to be showing.

def _unrouted_address(addr) -> bool:
    return not str(addr or "").strip() or str(addr).strip() == "0.0.0.0"


def _plane(identity, permitted, *, multiview=False) -> dict:
    """One plane (video or audio) of one display, in the account's terms."""
    if multiview:
        return {"mode": "multiview", "source_id": None}
    if identity and identity in permitted:
        return {"mode": "source", "source_id": identity}
    if identity:
        # A source this account does not hold. Said plainly, never named --
        # and decided exactly as the Standard page decides it, because the two
        # pages must not disagree about what a display is showing.
        return {"mode": "other", "source_id": None}
    return {"mode": "none", "source_id": None}


def _stream_key(addr, port):
    try:
        return (str(addr or "").strip(), int(port or 0))
    except (TypeError, ValueError):
        return None


def aggregate_plane(planes, total) -> dict:
    """Summarise one plane across every member of a group.

    `planes` holds one entry per ONLINE member; `total` is the member count,
    online or not. The answer is built from every member -- one display's state
    is never reported as the group's -- and says which of four things is true:

      all          every member, all online, is in the same state
      partial      the same state holds for only some of them: the rest are
                   unrouted or not answering
      mixed        members are in different states
      unavailable  no member is answering, so there is nothing true to say
    """
    online = len(planes)
    counts = {"sources": {}, "other": 0, "none": 0, "multiview": 0}
    tokens = set()
    for plane in planes:
        mode = plane["mode"]
        if mode == "source":
            sid = plane["source_id"]
            counts["sources"][sid] = counts["sources"].get(sid, 0) + 1
            tokens.add("s:" + sid)
        else:
            counts[mode] += 1
            tokens.add(mode)
    if online == 0:
        state = "unavailable"
    elif len(tokens) == 1:
        state = "all" if online == total else "partial"
    elif len(tokens - {"none"}) == 1:
        state = "partial"
    else:
        state = "mixed"
    only = None
    if state == "all":
        token = next(iter(tokens))
        only = token[2:] if token.startswith("s:") else None
    return {"state": state, "source_id": only, "counts": counts,
            "online": online, "total": total}


def user_matrix(matrix_state, units, account, aliases=None, groups=None) -> dict:
    """The routable grid for one account: permitted sources by permitted
    displays and display groups, with each row's current video and audio.

    Computed from the same state as user_state, in the same call, so the two
    pages cannot disagree about who is online or what is permitted. No device
    is read here.
    """
    base = user_state(matrix_state, units, account, aliases, groups)
    identity_of = _identity_by_address(units)
    unit_of = {(u.get("ip") or "").strip(): u for u in (units or [])}
    permitted = {s["id"] for s in base["sources"]}

    encoders = matrix_state.get("encoders") or []
    decoders = matrix_state.get("decoders") or []
    routes = matrix_state.get("routes") or {}

    audio_stream = {}
    for row in encoders:
        key = _stream_key(row.get("a_mcast"), row.get("a_port"))
        identity = identity_of.get((row.get("ip") or "").strip())
        if key and identity and not _unrouted_address(key[0]):
            audio_stream.setdefault(key, identity)

    by_identity = {}
    for row in decoders:
        address = (row.get("ip") or "").strip()
        identity = identity_of.get(address)
        if not identity:
            continue
        routed = identity_of.get((routes.get(address) or "").strip())
        audio_key = _stream_key(row.get("ip3_addr"), row.get("ip3_port"))
        audio_identity = audio_stream.get(audio_key) if audio_key else None
        multiview = bool(row.get("multiview_active"))
        by_identity[identity] = {
            "online": _is_online(row, unit_of.get(address)),
            "video": _plane(routed, permitted, multiview=multiview),
            # Multiview carries no audio of its own, so the audio plane of a
            # compositing display is read like any other.
            "audio": _plane(audio_identity, permitted),
        }

    rows = []
    for item in base["destinations"]:
        known = by_identity.get(item["id"]) or {
            "online": item["online"], "video": _plane(None, permitted),
            "audio": _plane(None, permitted)}
        rows.append({"kind": "display", "id": item["id"], "name": item["name"],
                     "online": bool(item["online"]) and known["online"],
                     "video": known["video"], "audio": known["audio"]})

    for item in base["groups"]:
        group = groups.get(item["id"]) if groups is not None else None
        members = list(group["members"]) if group else []
        live = [by_identity[m] for m in members
                if m in by_identity and by_identity[m]["online"]]
        video = aggregate_plane([m["video"] for m in live], len(members))
        audio = aggregate_plane([m["audio"] for m in live], len(members))
        rows.append({"kind": "group", "id": item["id"], "name": item["name"],
                     "displays": len(members), "online": bool(live),
                     "video": video, "audio": audio})

    kind_order = {"display": 0, "group": 1}
    rows.sort(key=lambda r: (kind_order[r["kind"]], r["name"].lower(), r["id"]))
    return {"ok": True, "interfaces": base["interfaces"],
            "revision": base["revision"], "sources": base["sources"],
            "rows": rows}


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
