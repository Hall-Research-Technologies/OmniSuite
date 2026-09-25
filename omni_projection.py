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
        destinations.append(project_destination(row, unit_of.get(address),
                                                identity, showing, aliases))

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
