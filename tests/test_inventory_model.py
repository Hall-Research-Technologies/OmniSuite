"""Clear Units, Remove Selected and the assignment summary against an independent model.

The other inventory suites state what should happen in a scenario somebody thought
of. This one states what must be *true* and lets seeded random sequences of
administrative operations look for the scenario nobody thought of.

The model is kept by the test, from the operations the test itself performed -- it
never reads a store -- so a disagreement is the application's, not a shared bug:

    users     name -> sources, displays, groups it holds
    groups    name -> members
    direct    names with a Direct Access link
    inventory the identities the application currently lists

After every operation, for every seed:

    1  each unit's summary says exactly who holds it, and nobody else
    2  with Remote Access on, a clear or a Remove Selected never takes a held unit
       and never edits an account, group, link or grant
    3  with Remote Access off, a clear takes everything and the cascade is total
    4  the stores hold exactly what the model says (no ghost grant, no lost one)
    5  nothing answers 5xx
    6  a restart (a reload from disk) changes none of it

Real endpoints, throwaway data directory, network fenced.
"""
from __future__ import annotations

import copy
import random
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import omni_users                                         # noqa: E402
from omni_users import Permissions                        # noqa: E402
from tests.test_clear_inventory import (                  # noqa: E402
    InventoryBase, ALL, UNITS, ROOM_A, ROOM_B, ROOM_C, SRC, CAMERA, FRONT)

SEEDS = tuple(range(16))
STEPS = 45
IP_OF = {omni_users._normalise_identity(u["mac"]): u["ip"] for u in UNITS if u.get("mac")}


class Model:
    def __init__(self):
        self.users = {"teacher": {"sources": {SRC, CAMERA}, "dests": {FRONT},
                                  "groups": set()}}
        self.groups = {"Front rooms": {ROOM_A, ROOM_B}}
        self.direct = set()
        self.inventory = set(ALL)

    # ---- the answer the application should give
    def holders(self, identity):
        users = sorted(n for n, u in self.users.items()
                       if identity in u["sources"] or identity in u["dests"])
        groups = sorted(n for n, members in self.groups.items() if identity in members)
        return users, groups

    def held(self):
        return {i for i in self.inventory if any(self.holders(i))}

    def snapshot(self):
        return (copy.deepcopy(self.users), copy.deepcopy(self.groups),
                set(self.direct))


class ModelBase(InventoryBase):
    def setUp(self):
        super().setUp()
        self.model = Model()
        self.statuses = []
        self.gid = {"Front rooms": self.group["id"]}

    # ---- the operations, each performed for real and recorded in the model
    def op_create_user(self, rng):
        name = "u%d" % rng.randrange(10 ** 6)
        if name in self.model.users:
            return
        sources = set(rng.sample(ALL, rng.randrange(0, 3)))
        dests = set(rng.sample(ALL, rng.randrange(0, 3)))
        groups = {g for g in self.model.groups if rng.random() < 0.3}
        result = self.store.create(
            name, name + "-password-1", omni_users.ROLE_USER,
            Permissions(features=("av",), sources=sorted(sources),
                        destinations=sorted(dests),
                        groups=[self.gid[g] for g in sorted(groups)]))
        self.assertTrue(result["ok"], result)
        self.model.users[name] = {"sources": sources, "dests": dests, "groups": groups}

    def op_delete_user(self, rng):
        names = sorted(self.model.users)
        if not names:
            return
        name = rng.choice(names)
        account = self.store.find_by_username(name)
        self.assertTrue(self.store.delete(account.id, actor=self.admin)["ok"])
        del self.model.users[name]
        self.model.direct.discard(name)

    def op_set_permissions(self, rng):
        names = sorted(self.model.users)
        if not names:
            return
        name = rng.choice(names)
        sources = set(rng.sample(ALL, rng.randrange(0, 4)))
        dests = set(rng.sample(ALL, rng.randrange(0, 4)))
        groups = {g for g in self.model.groups if rng.random() < 0.4}
        result = self.store.set_permissions(
            self.store.find_by_username(name).id,
            Permissions(features=("av",), sources=sorted(sources),
                        destinations=sorted(dests),
                        groups=[self.gid[g] for g in sorted(groups)]))
        self.assertTrue(result["ok"], result)
        self.model.users[name] = {"sources": sources, "dests": dests, "groups": groups}

    def op_direct_access(self, rng):
        names = sorted(self.model.users)
        if not names:
            return
        name = rng.choice(names)
        account = self.store.find_by_username(name)
        if name in self.model.direct:
            self.store.disable_direct_access(account.id, actor=self.admin)
            self.model.direct.discard(name)
        else:
            self.store.enable_direct_access(account.id, actor=self.admin)
            self.model.direct.add(name)

    def op_group_create(self, rng):
        name = "G%d" % rng.randrange(10 ** 6)
        members = set(rng.sample(ALL, rng.randrange(0, 4)))
        reply = self.admin_post("/api/admin/groups",
                                {"name": name, "members": sorted(members)})
        self.statuses.append(reply.status_code)
        self.assertEqual(reply.status_code, 200, reply.get_data(as_text=True))
        self.gid[name] = reply.get_json()["group"]["id"]
        self.model.groups[name] = members

    def op_group_members(self, rng):
        names = sorted(self.model.groups)
        if not names:
            return
        name = rng.choice(names)
        members = set(rng.sample(ALL, rng.randrange(0, 4)))
        reply = self.admin_post("/api/admin/groups/" + self.gid[name],
                                {"action": "set_members", "members": sorted(members)})
        self.statuses.append(reply.status_code)
        self.assertEqual(reply.status_code, 200, reply.get_data(as_text=True))
        self.model.groups[name] = members

    def op_group_delete(self, rng):
        names = sorted(self.model.groups)
        if not names:
            return
        name = rng.choice(names)
        reply = self.admin_post("/api/admin/groups/" + self.gid[name],
                                {"action": "delete"})
        self.statuses.append(reply.status_code)
        self.assertEqual(reply.status_code, 200, reply.get_data(as_text=True))
        del self.model.groups[name]
        for user in self.model.users.values():
            user["groups"].discard(name)

    def op_rescan(self, rng):
        self.srv._save_cache(copy.deepcopy(UNITS))
        self.model.inventory = set(ALL)

    def op_clear(self, rng):
        remote_on = rng.random() < 0.7
        self.set_remote(remote_on)
        held_before = self.model.held()
        snapshot = self.model.snapshot()
        reply = self.clear()
        self.statuses.append(reply.status_code)
        self.assertEqual(reply.status_code, 200, reply.get_data(as_text=True))
        if remote_on:
            self.model.inventory = {i for i in self.model.inventory if i in held_before}
            self.assertEqual(self.model.snapshot(), snapshot)
        else:
            self.model.inventory = set()
            for user in self.model.users.values():
                user["sources"], user["dests"] = set(), set()
            for name in self.model.groups:
                self.model.groups[name] = set()

    def op_remove_selected(self, rng):
        remote_on = rng.random() < 0.7
        self.set_remote(remote_on)
        # Only units the application lists: a request that names one it has not
        # found is refused whole (404 unknown_device), which is its own test.
        candidates = sorted(self.model.inventory)
        if not candidates:
            return
        picked = set(rng.sample(candidates, rng.randrange(1, min(3, len(candidates)) + 1)))
        held_before = self.model.held()
        reply = self.admin_post("/api/remove_units",
                                {"ips": [IP_OF[i] for i in sorted(picked)]})
        self.statuses.append(reply.status_code)
        self.assertEqual(reply.status_code, 200, reply.get_data(as_text=True))
        gone = {i for i in picked & self.model.inventory
                if not (remote_on and i in held_before)}
        # Remove Selected takes units out of the inventory and nothing else, with
        # Remote Access off as well: a grant names an identity, and an identity
        # that returns is the same unit. (Clear Units is the cascading action.)
        self.model.inventory -= gone

    def op_restart(self, rng):
        self.store._load()
        self.groups.reload()
        self.aliases._load()

    OPS = ("create_user", "delete_user", "set_permissions", "direct_access",
           "group_create", "group_members", "group_delete", "rescan", "clear",
           "remove_selected", "restart")

    # ---- the checks
    def cached_identities(self):
        return set(self.cached())

    def check(self, label):
        where = "after %s" % label
        # 5: nothing answered with a server fault
        self.assertTrue(all(s < 500 for s in self.statuses), (where, self.statuses))
        # 1: the report says exactly who holds each unit
        report = self.report()
        rows = {r["id"]: r for r in report["units"]}
        self.assertEqual(set(rows), self.model.inventory, where + ": the inventory differs")
        for identity, row in rows.items():
            users, groups = self.model.holders(identity)
            self.assertEqual(row["users"], users, "%s: users of %s" % (where, identity))
            self.assertEqual(row["groups"], groups, "%s: groups of %s" % (where, identity))
            self.assertEqual(row["assigned"], bool(users or groups),
                             "%s: %s says %s" % (where, identity, row["summary"]))
            self.assertEqual(row["summary"] == "Unassigned", not (users or groups),
                             "%s: a false 'Unassigned' for %s" % (where, identity))
        # 4: the stores hold exactly the model
        for name, held in self.model.users.items():
            account = self.store.find_by_username(name)
            self.assertIsNotNone(account, "%s: %s vanished" % (where, name))
            self.assertEqual(set(account.permissions.sources), held["sources"],
                             "%s: %s's sources" % (where, name))
            self.assertEqual(set(account.permissions.destinations), held["dests"],
                             "%s: %s's displays" % (where, name))
            self.assertEqual({g for g, gid in self.gid.items()
                              if gid in account.permissions.groups
                              and g in self.model.groups}, held["groups"],
                             "%s: %s's group grants" % (where, name))
            self.assertEqual(account.direct_access.active, name in self.model.direct,
                             "%s: %s's link" % (where, name))
        stored = {a["username"] for a in self.store.list_accounts()}
        self.assertEqual(stored - {"principal"}, set(self.model.users),
                         "%s: a ghost or missing account" % where)
        for name, members in self.model.groups.items():
            self.assertEqual(set(self.groups.get(self.gid[name])["members"]), members,
                             "%s: members of %s" % (where, name))
        self.assertEqual({g["name"] for g in self.groups.all()}, set(self.model.groups),
                         "%s: a ghost or missing group" % where)
        # What is cached is what the model says, by identity.
        self.assertEqual(self.cached_identities(), self.model.inventory,
                         where + ": the cache differs from the inventory")


class RandomSequencesTests(ModelBase):
    def run_seed(self, seed):
        rng = random.Random(seed)
        self.check("the start")
        history = []
        for step in range(STEPS):
            op = rng.choice(self.OPS)
            history.append(op)
            try:
                getattr(self, "op_" + op)(rng)
                self.check("step %d (%s) of seed %d" % (step, op, seed))
            except AssertionError as error:
                raise AssertionError("seed %d, history %s\n%s"
                                     % (seed, history, error)) from None

    def test_seeded_sequences_never_disagree_with_the_model(self):
        for seed in SEEDS:
            with self.subTest(seed=seed):
                self.setUp_again()
                self.run_seed(seed)

    def setUp_again(self):
        """A fresh world per seed without paying for a fresh application."""
        for account in list(self.store.list_accounts()):
            if account["username"] != "principal":
                self.store.delete(account["id"], actor=self.admin)
        for group in list(self.groups.all()):
            self.groups.delete(group["id"])
        self.model = Model()
        self.statuses = []
        self.gid = {}
        self.set_remote(True)
        self.srv._save_cache(copy.deepcopy(UNITS))
        made = self.groups.create("Front rooms", [ROOM_A, ROOM_B])["group"]
        self.gid["Front rooms"] = made["id"]
        self.store.create("teacher", "teacher-password-1", omni_users.ROLE_USER,
                          Permissions(features=("av",), sources=[SRC, CAMERA],
                                      destinations=[FRONT]))


class AClearNeverTakesAHeldUnitTests(ModelBase):
    """The one property that matters most, stated on its own and hammered."""

    def test_with_remote_access_on_no_sequence_removes_a_held_unit(self):
        for seed in SEEDS:
            with self.subTest(seed=seed):
                rng = random.Random(1000 + seed)
                RandomSequencesTests.setUp_again(self)
                for _ in range(STEPS):
                    op = rng.choice([o for o in self.OPS
                                     if o not in ("restart",)])
                    held = {i for i in self.cached_identities()
                            if any(self.model.holders(i))}
                    self.set_remote(True)
                    if op == "clear":
                        self.assertEqual(self.clear().status_code, 200)
                        self.assertTrue(held <= self.cached_identities(),
                                        "a held unit was cleared")
                        self.model.inventory = {
                            i for i in self.model.inventory if i in held}
                    elif op == "remove_selected":
                        candidates = sorted(self.model.inventory)
                        if not candidates:
                            continue
                        picked = set(rng.sample(candidates, min(3, len(candidates))))
                        reply = self.admin_post(
                            "/api/remove_units",
                            {"ips": [IP_OF[i] for i in sorted(picked)]})
                        self.assertEqual(reply.status_code, 200,
                                         reply.get_data(as_text=True))
                        self.assertTrue(held <= self.cached_identities(),
                                        "a held unit was removed")
                        self.model.inventory -= (picked - held)
                    else:
                        getattr(self, "op_" + op)(rng)
                        # The operation may itself have chosen Remote Access off.
                        self.set_remote(True)


class LivenessIsNotOwnershipTests(ModelBase):
    """Whether a unit answers says nothing about whether somebody holds it."""

    def silent(self, *identities):
        units = copy.deepcopy(UNITS)
        for unit in units:
            if omni_users._normalise_identity(unit.get("mac")) in identities:
                unit.update({"online": False, "reachable": False, "status": "offline"})
        self.srv._save_cache(units)

    def test_a_held_unit_that_has_gone_quiet_is_still_protected(self):
        self.silent(SRC, FRONT, ROOM_A)
        self.assertEqual(self.clear().status_code, 200)
        self.assertTrue({SRC, FRONT, ROOM_A} <= self.cached_identities(),
                        "a held unit was cleared because it was not answering")

    def test_an_unheld_unit_that_has_gone_quiet_is_still_cleared(self):
        self.silent(ROOM_C)
        self.assertEqual(self.clear().status_code, 200)
        self.assertNotIn(ROOM_C, self.cached_identities())

    def test_the_summary_reports_a_quiet_held_unit_as_held(self):
        self.silent(SRC)
        self.assertTrue(self.row(SRC)["assigned"])
        self.assertEqual(self.row(SRC)["users"], ["teacher"])

    def test_a_held_unit_rediscovered_at_another_address_keeps_its_holders(self):
        moved = copy.deepcopy(UNITS)
        for unit in moved:
            if unit["mac"] == SRC:
                unit["ip"] = "192.0.2.222"
        self.srv._save_cache(moved)
        self.assertEqual(self.clear().status_code, 200)
        self.assertEqual(self.row(SRC)["users"], ["teacher"])
        self.assertIn(SRC, self.cached_identities())


if __name__ == "__main__":
    unittest.main()
