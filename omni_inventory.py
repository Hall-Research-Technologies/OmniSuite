"""Which discovered units Remote Access is using, and which may be cleared.

While Remote Access is on, people are working from the accounts, groups and names
an administrator built on top of the discovered inventory. Clearing a unit
somebody was given silently takes their equipment away, so with Remote Access on
Clear Units is assignment aware: units nothing refers to may go, units something
refers to stay.

This module is the one place that decides what "assigned" means. The Clear Units
endpoint, the View Assignments dialog and the Device names page all consume the
same report, so two surfaces cannot disagree about why a unit is protected.

It reads the stores through their public accessors and holds nothing of its own:
the answer is derived from current state on every call, so an assignment made a
moment ago is seen without a rescan or a restart.

Identity is the hardware address. A unit is assigned when its own address, or the
USB address it reports, is named by an account (as a source or a display) or by a
display group. An address, a hostname or a friendly name is never consulted.
"""
from __future__ import annotations

import contextlib

import omni_users

#: The kinds of reference Remote Access keeps to a device. Anything added later
#: that names a device must be added here, and `test_clear_inventory` fails if a
#: store that holds device identities is not accounted for.
REFERENCE_KINDS = ("users", "groups")


def normalise(identity) -> str:
    """The one spelling of a device identity."""
    return omni_users._normalise_identity(identity)


def unit_identities(unit) -> list:
    """Every stable identity one discovered unit answers to, own address first.

    An integrated USB endpoint belongs to its parent: a grant that names the USB
    address is a grant on this unit. Nothing is inferred from an address,
    hostname or similarity -- only exact normalised hardware addresses recorded
    on the unit itself.
    """
    out = []
    for key in ("mac", "usb_mac"):
        value = normalise((unit or {}).get(key))
        if value and value not in out:
            out.append(value)
    return out


def _plural(count, word):
    return "%d %s%s" % (count, word, "" if count == 1 else "s")


def summary_text(users, groups) -> str:
    """The compact summary shown beside a device. "Unassigned" when nothing is."""
    parts = []
    if users:
        parts.append(_plural(len(users), "User"))
    if groups:
        parts.append(_plural(len(groups), "Group"))
    return " · ".join(parts) if parts else "Unassigned"


def collect(store, groups) -> dict:
    """identity -> {"users": [...], "direct": [...], "groups": [...]}.

    Raises if either store cannot be read: the caller must refuse rather than
    treat "could not tell" as "nothing is assigned".

    Disabled accounts count. An account that is off is one an administrator can
    turn back on, and what it was given is still theirs. An administrator holds
    no per-device grants (the role is the grant), so there is nothing to protect.
    """
    found: dict = {}

    def slot(identity):
        return found.setdefault(identity, {"users": set(), "direct": set(),
                                           "groups": set()})

    grants = store.grants_snapshot()
    for account in store.list_accounts():
        if account.get("role") == omni_users.ROLE_ADMIN:
            continue
        permissions = grants.get(account.get("id"))
        if permissions is None:
            continue
        name = account.get("username") or ""
        for identity in set(permissions.sources) | set(permissions.destinations):
            if not identity:
                continue
            entry = slot(normalise(identity))
            entry["users"].add(name)
            if (account.get("direct_access") or {}).get("enabled"):
                entry["direct"].add(name)

    for group in groups.all():
        for member in group.get("members") or []:
            if member:
                slot(normalise(member))["groups"].add(
                    group.get("name") or group.get("id") or "")

    return {identity: {key: sorted(value, key=str.lower)
                       for key, value in entry.items()}
            for identity, entry in found.items()}


def for_unit(assignments, unit) -> dict:
    """The merged references for one unit, across every identity it answers to."""
    users, direct, groups = set(), set(), set()
    for identity in unit_identities(unit):
        entry = assignments.get(identity)
        if entry:
            users.update(entry["users"])
            direct.update(entry["direct"])
            groups.update(entry["groups"])
    return {"users": sorted(users, key=str.lower),
            "direct": sorted(direct, key=str.lower),
            "groups": sorted(groups, key=str.lower)}


def classify(units, assignments, protect) -> tuple:
    """Split a unit list into (kept, clearable), preserving order.

    With `protect` false everything is clearable. Otherwise a unit is kept
    exactly when something refers to it. A unit with no hardware identity cannot
    be referred to by anything, so it is clearable.
    """
    kept, clearable = [], []
    for unit in units:
        refs = for_unit(assignments, unit)
        assigned = bool(refs["users"] or refs["groups"])
        (kept if (protect and assigned) else clearable).append(unit)
    return kept, clearable


def report(units, assignments, *, remote_access, display_name,
           alias_for=None) -> dict:
    """The device list with assignment summaries, derived once.

    `display_name(unit)` supplies the label an operator knows the unit by; the
    hostname is carried separately because two units can share a friendly name.
    Never carries a password, token or session, and no field here is an address
    an operator did not already see in the device list.
    """
    rows = []
    for unit in units:
        identities = unit_identities(unit)
        if not identities:
            continue
        refs = for_unit(assignments, unit)
        role = (unit.get("role") or unit.get("type") or "").strip().lower()
        rows.append({
            "id": identities[0],
            "type": ("Encoder" if "enc" in role else
                     "Decoder" if "dec" in role else "Other"),
            "name": display_name(unit),
            # OmniSuite's own label, kept apart from the equipment's name:
            # two units can share a friendly name.
            "friendly_name": (alias_for(unit) if alias_for else "") or "",
            "device_name": unit.get("hostname") or unit.get("name") or identities[0],
            "model": unit.get("model") or "",
            "users": refs["users"],
            "direct_access": refs["direct"],
            "groups": refs["groups"],
            "count": len(refs["users"]) + len(refs["groups"]),
            "summary": summary_text(refs["users"], refs["groups"]),
            "assigned": bool(refs["users"] or refs["groups"]),
        })
    rows.sort(key=lambda r: (r["type"], r["name"].lower(), r["id"]))
    assigned = sum(1 for r in rows if r["assigned"])
    unidentified = sum(1 for u in units if not unit_identities(u))
    protected = assigned if remote_access else 0
    return {"remote_access": bool(remote_access),
            "units": rows,
            "total": len(rows) + unidentified,
            "assigned": assigned,
            "protected": protected,
            "clearable": len(rows) + unidentified - protected}


@contextlib.contextmanager
def hold_assignments(store, groups):
    """Block assignment edits for the span of a classify-and-remove.

    A unit judged unassigned must not gain an assignment before it is removed,
    or Clear Units takes away what an administrator granted a moment earlier.
    Both stores serialise their writes on one re-entrant lock each; taking them
    in a fixed order (accounts, then groups) for the whole operation means an
    edit either lands before the decision, and is seen, or after the removal.
    No code path takes them in the other order.
    """
    with store._lock:
        with groups._lock:
            yield


def hold_assignments_if(enabled, store, groups):
    """`hold_assignments` when `enabled`, otherwise a context that holds nothing.

    The unprotected path stays exactly as it was: with Remote Access off there is
    nobody to protect, and taking the stores' locks would only add contention.
    """
    return hold_assignments(store, groups) if enabled else contextlib.nullcontext()
