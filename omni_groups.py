"""Display Groups: a named collection of displays that receive the same source.

A Display Group is **not** a Multiview and not a video wall. It has no geometry:
each member shows the whole of whatever source is routed to the group, by the
same ordinary A/V route a single display gets. Multiview is a separate subsystem
and is never used here.

The semantics are the ones LumaSuite proved, so the two products mean the same
thing by the same words:

  * Membership is by stable device identity (a canonical MAC), never by address,
    so a display that changes address stays in its groups.
  * A group has an opaque id of its own, independent of its name and of who is in
    it, so renaming or re-populating a group never changes what a permission
    refers to.
  * A display may belong to any number of groups.
  * Execution is sequential: one member is routed and verified before the next
    starts. A member that fails does not stop the rest, and members already
    routed are not rolled back -- a group makes no atomicity promise, and the
    result says exactly which displays did and did not change.
"""
from __future__ import annotations

import json
import logging
import re
import secrets
import threading
import time
from pathlib import Path

from omni_users import _atomic_write_json

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1
MAX_NAME_LENGTH = 60
MAX_MEMBERS = 256
MAX_GROUPS = 512
_MAC = re.compile(r"^[0-9a-f]{2}(:[0-9a-f]{2}){5}$")


def _clean_name(value) -> str:
    text = " ".join(str(value or "").split())
    return "".join(c for c in text if c.isprintable())[:MAX_NAME_LENGTH].strip()


def _clean_members(values, canonical):
    """(members, rejected): canonical MACs in first-seen order, no duplicates.

    Anything that is not a MAC -- an address, a hostname, a name -- is rejected
    rather than converted: membership is identity, and an address is not one.
    """
    members, rejected, seen = [], [], set()
    if not isinstance(values, (list, tuple)):
        return members, ["members must be a list"]
    for value in values:
        identity = canonical(value) if isinstance(value, str) else ""
        if not identity or not _MAC.match(identity):
            rejected.append(str(value)[:40])
            continue
        if identity not in seen:
            seen.add(identity)
            members.append(identity)
    return members, rejected


class GroupStore:
    """The persisted set of groups, safe to use from many request threads."""

    def __init__(self, path, canonical):
        self._path = Path(path)
        self._canonical = canonical
        self._lock = threading.RLock()
        self._groups: dict = {}
        self._load()

    # ---- persistence ---------------------------------------------------
    def _quarantine(self) -> None:
        try:
            self._path.replace(self._path.with_suffix(".corrupt"))
        except OSError:
            pass

    def _load(self) -> None:
        with self._lock:
            self._groups = {}
            if not self._path.exists():
                return
            try:
                raw = json.loads(self._path.read_text(encoding="utf-8"))
            except Exception as exc:
                log.error("[GROUPS] %s is unreadable (%s); quarantining",
                          self._path.name, type(exc).__name__)
                self._quarantine()
                return
            entries = raw.get("groups") if isinstance(raw, dict) else None
            if not isinstance(entries, list):
                self._quarantine()
                return
            for entry in entries[:MAX_GROUPS]:
                try:
                    gid = str(entry["id"]).strip().lower()
                    name = _clean_name(entry.get("name"))
                    members, _bad = _clean_members(entry.get("members") or [],
                                                   self._canonical)
                    if gid and name:
                        self._groups[gid] = {
                            "id": gid, "name": name, "members": members,
                            "created_at": float(entry.get("created_at") or 0),
                            "updated_at": float(entry.get("updated_at") or 0)}
                except Exception:
                    log.warning("[GROUPS] skipping an unreadable group record")

    def reload(self) -> None:
        self._load()

    def _persist(self) -> bool:
        return _atomic_write_json(self._path, {
            "version": SCHEMA_VERSION,
            "groups": [self._groups[g] for g in sorted(self._groups)]})

    # ---- reads ---------------------------------------------------------
    def all(self) -> list:
        """Every group, in a deterministic order, as independent copies."""
        with self._lock:
            return [dict(g, members=list(g["members"]))
                    for g in sorted(self._groups.values(),
                                    key=lambda g: (g["name"].lower(), g["id"]))]

    def get(self, group_id):
        with self._lock:
            g = self._groups.get(str(group_id or "").strip().lower())
            return None if g is None else dict(g, members=list(g["members"]))

    def groups_containing(self, identity) -> list:
        identity = self._canonical(identity)
        with self._lock:
            return [g["id"] for g in self._groups.values() if identity in g["members"]]

    def referenced_identities(self) -> dict:
        """identity -> sorted group names that contain it (for Clear Units)."""
        out: dict = {}
        with self._lock:
            for g in self._groups.values():
                for member in g["members"]:
                    out.setdefault(member, set()).add(g["name"])
        return {k: sorted(v) for k, v in out.items()}

    def forget_members(self, identities=None) -> int:
        """Remove displays from every group (None = all); returns groups changed.

        The groups themselves stay: an empty group is a name an administrator
        chose, and deleting it is theirs to decide.
        """
        gone = None if identities is None else {
            self._canonical(i) for i in identities}
        changed = 0
        with self._lock:
            for g in self._groups.values():
                kept = [m for m in g["members"] if gone is not None and m not in gone]
                if len(kept) != len(g["members"]):
                    g["members"] = kept
                    g["updated_at"] = time.time()
                    changed += 1
            if changed and not self._persist():
                raise OSError("the group store could not be written")
        return changed

    def members_snapshot(self) -> dict:
        with self._lock:
            return {gid: list(g["members"]) for gid, g in self._groups.items()}

    def restore_members(self, snapshot) -> None:
        with self._lock:
            for gid, members in snapshot.items():
                if gid in self._groups:
                    self._groups[gid]["members"] = list(members)
            self._persist()

    # ---- writes (each one read-modify-write under the lock) -------------
    def create(self, name, members):
        name = _clean_name(name)
        if not name:
            return {"ok": False, "error": "A group needs a name."}
        clean, rejected = _clean_members(members, self._canonical)
        if rejected:
            return {"ok": False, "error": "Some members are not displays that can be "
                                          "identified by hardware address."}
        if len(clean) > MAX_MEMBERS:
            return {"ok": False, "error": "That group has too many displays."}
        with self._lock:
            if len(self._groups) >= MAX_GROUPS:
                return {"ok": False, "error": "There are too many groups."}
            gid = "grp_" + secrets.token_hex(8)
            now = time.time()
            self._groups[gid] = {"id": gid, "name": name, "members": clean,
                                 "created_at": now, "updated_at": now}
            if not self._persist():
                self._groups.pop(gid, None)
                return {"ok": False, "error": "The group could not be saved."}
            return {"ok": True, "group": self.get(gid)}

    def update(self, group_id, name=None, members=None):
        gid = str(group_id or "").strip().lower()
        with self._lock:
            current = self._groups.get(gid)
            if current is None:
                return {"ok": False, "error": "No such group.", "missing": True}
            before = dict(current, members=list(current["members"]))
            if name is not None:
                cleaned = _clean_name(name)
                if not cleaned:
                    return {"ok": False, "error": "A group needs a name."}
                current["name"] = cleaned
            if members is not None:
                clean, rejected = _clean_members(members, self._canonical)
                if rejected:
                    self._groups[gid] = before
                    return {"ok": False,
                            "error": "Some members are not displays that can be "
                                     "identified by hardware address."}
                if len(clean) > MAX_MEMBERS:
                    self._groups[gid] = before
                    return {"ok": False, "error": "That group has too many displays."}
                current["members"] = clean
            current["updated_at"] = time.time()
            if not self._persist():
                self._groups[gid] = before
                return {"ok": False, "error": "The group could not be saved."}
            return {"ok": True, "group": self.get(gid), "before": before}

    def delete(self, group_id):
        gid = str(group_id or "").strip().lower()
        with self._lock:
            removed = self._groups.pop(gid, None)
            if removed is None:
                return {"ok": False, "error": "No such group.", "missing": True}
            if not self._persist():
                self._groups[gid] = removed
                return {"ok": False, "error": "The group could not be removed."}
            return {"ok": True, "group": removed}


def run_sequential(members, route_one) -> dict:
    """Route each member in order, one at a time, and report every outcome.

    `route_one(index, identity)` performs and verifies one display's route and
    returns a dict with at least `ok`. It is called strictly in order; the next
    call does not begin until the previous one has returned. An exception from
    one member is that member's failure and nothing more -- the rest still run,
    and nothing already done is undone.
    """
    results = []
    for index, identity in enumerate(members):
        try:
            outcome = route_one(index, identity)
            if not isinstance(outcome, dict) or "ok" not in outcome:
                outcome = {"ok": False, "reason": "no_result"}
        except Exception as exc:                  # a member's failure is only its own
            log.warning("[GROUPS] member %d raised %s", index, type(exc).__name__)
            outcome = {"ok": False, "reason": "error"}
        results.append(dict(outcome, index=index))
    routed = sum(1 for r in results if r.get("ok"))
    return {"ok": bool(results) and routed == len(results),
            "routed": routed, "total": len(results), "results": results}
