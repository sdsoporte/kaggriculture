"""Optional motif proposal layer for the native (hosted) agent.

A motif is ONE worker's op plus the grounded local state that op acted on, taken
from a real turn the model played and the engine executed. It is role-bound in the
sense that matters here: the component is attached to the worker that performed it,
so three WATERs by three different workers compile to one
`worker-on-own-unwatered-plant -> WATER` motif -- not to "a turn with three hands
that also has the farmer picking up a COW".

This layer only ever PROPOSES, and it never claims a cash gain: an effect test and
a legality test cannot measure one. What it can establish is narrower and is what
it is limited to -- that a worker slot the baseline was wasting can be filled with
an op whose local precondition holds, which consumes nothing another slot or the
caller's contract reserved, and which leaves every other worker's exact outcome
unchanged.

Runtime contract (hosted Kaggle process):
  * the transition is the ENGINE'S OWN `_apply_unit_action`, carried in the archive
    as `engine_pin.py` (verbatim, Apache-2.0, pinned sha). The layer does not
    depend on `kaggle_environments` being importable and does not fall back to a
    re-implementation; with no transition available it raises rather than silently
    disabling.
  * no model, no network, no disk read at turn time -- the table is a Python
    literal in `motifs_table.py`.
  * bounded work: MAX_SLOT_CANDIDATES per slot, MAX_ACCEPTED swaps per turn, and a
    template cache that holds COARSE candidate sets only. A cache hit never admits
    a motif: `holds()` is re-run against that slot's fresh facts every time, so a
    key that ignores quantities cannot leak a match across a state where the
    quantity fell below what the op needs.
"""

import copy

MAX_SLOT_CANDIDATES = 8
MAX_ACCEPTED = 2
MOVES = ("NORTH", "SOUTH", "EAST", "WEST")
# An op whose recorded effect was one of these is never compiled and never
# proposed: razing the seat's own empty structure and destroying a live plant are
# the two churn behaviours the segment measurements already charge against cash.
FORBIDDEN_EFFECTS = ("none", "destroyed_plant", "removed_structure")
# Ops that consume nothing shared: they act on an asset the seat already holds and
# take no seed, no shed stock, no cash and no carried item. Only these can be
# proposed without a caller-supplied reservation contract.
FREE_UPKEEP_OPS = ("WATER", "CARE", "HARVEST", "COLLECT_FERTILIZER")


class NoTransition(RuntimeError):
    """No pinned transition available. Raised instead of disabling quietly."""


def engine(prefer_bundle=True):
    """The pinned unit-phase transition.

    The bundle is the hosted path; the installed package is used only when the
    caller explicitly asks (the parity test does). Failure raises.
    """
    mods = ["engine_pin", "kaggle_environments.envs.kaggriculture.kaggriculture"]
    if not prefer_bundle:
        mods.reverse()
    err = []
    for name in mods:
        try:
            mod = __import__(name, fromlist=["_apply_unit_action"])
            if hasattr(mod, "_apply_unit_action"):
                return mod
        except Exception as exc:                       # pragma: no cover - env dep
            err.append(f"{name}: {exc}")
    raise NoTransition("; ".join(err) or "no transition module")


# ---------------------------------------------------------------- grounded facts

def _cfg(config, key, default):
    try:
        v = config[key]
    except Exception:
        v = None
    return default if v is None else v


def _pos(farm, i):
    return farm["farmer"] if i == 0 else farm["hands"][i - 1]


def _shed_access(board):
    half = board // 2
    return {(half - 1, half - 1), (half, half - 1), (half - 1, half), (half, half)}


def unit_facts(farm, priv, config, i):
    """The grounded local situation of ONE worker, read from the state that worker
    will actually act on -- which during proposal is the ordered PREFIX scratch
    state, not the head of the turn. A PLACE proposed for hand2 must see the
    PASTURE hand0 built two slots earlier, or its precondition is fiction.
    """
    board = int(_cfg(config, "boardSize", 10))
    p = _pos(farm, i)
    x, y = int(p[0]), int(p[1])
    tile = farm["tiles"][y][x]
    invs = priv.get("inventories", [])
    carry = dict(invs[i]) if i < len(invs) and isinstance(invs[i], dict) else {}
    f = {
        "tile": "EMPTY" if tile is None else (
            tile if isinstance(tile, str) else str(tile.get("kind"))),
        "at": (x, y),
        "shed_access": (x, y) in _shed_access(board),
        "carry": carry,
        "carry_total": sum(carry.values()),
        "seeds": dict(priv.get("seeds", {})),
        "shed": dict(priv.get("shed", {})),
        "money": float(farm.get("money", 0)),
    }
    if isinstance(tile, dict):
        if "animal" in tile:
            f["tile"] = "ANIMAL"
            f["animal"] = tile["animal"]
            f["structure"] = tile.get("kind")
            f["fed_today"] = bool(tile.get("fed_today"))
            f["cared_today"] = bool(tile.get("cared_today"))
            f["fertilizer_available"] = bool(tile.get("fertilizer_available"))
            f["yield_units"] = int(tile.get("yield_units", 0))
        elif tile.get("kind") == "PLANT":
            f["crop"] = tile.get("crop")
            f["watered_today"] = bool(tile.get("watered_today"))
            f["yield_units"] = int(tile.get("yield_units", 0))
            f["unwatered_run"] = min(2, int(tile.get("consecutive_unwatered", 0)))
        elif tile.get("kind") in ("COOP", "PASTURE"):
            f["structure"] = tile.get("kind")
            f["occupied"] = False
    return f


# Which grounded facts each opcode's precondition is built from. Anything not
# listed here is deliberately NOT part of the motif: a WATER does not depend on
# the worker's cash, so binding it to cash would make the motif unreusable.
PRECOND = {
    "WATER":              ("tile", "watered_today", "unwatered_run"),
    "FEED":               ("tile", "fed_today", "carry.WHEAT>=1"),
    "CARE":               ("tile", "cared_today", "fed_today"),
    "HARVEST":            ("tile", "yield_units>=1"),
    "COLLECT_FERTILIZER": ("tile", "fertilizer_available"),
    "PLANT":              ("tile", "seeds[arg]>=1"),
    "BUILD_COOP":         ("tile",),
    "BUILD_PASTURE":      ("tile",),
    "PLACE":              ("tile", "structure==arg_structure", "carry[arg]>=n"),
    "PICKUP":             ("shed_access", "shed[arg]>=n"),
    "DROP":               ("shed_access", "carry_total>=1"),
}
STRUCTURE_OF = {"GOOSE": "COOP", "COW": "PASTURE", "SHEEP": "PASTURE"}


def precondition(op, facts):
    """The motif's `when` clause: grounded fact -> its ACTUAL observed value.

    Returns None for an opcode this layer does not compile (movement, PASS, DIG).
    DIG is excluded on purpose: it is the op that destroys a live HARVEST target
    and razes an empty structure, so there is no safe context-free motif for it.
    """
    name = op[0]
    if name not in PRECOND:
        return None
    w = {}
    for key in PRECOND[name]:
        if key == "tile":
            w["tile"] = facts["tile"]
        elif key == "shed_access":
            w["shed_access"] = bool(facts["shed_access"])
        elif key == "carry.WHEAT>=1":
            w["carry.WHEAT>=1"] = facts["carry"].get("WHEAT", 0) >= 1
        elif key == "carry_total>=1":
            w["carry_total>=1"] = facts["carry_total"] >= 1
        elif key == "yield_units>=1":
            w["yield_units>=1"] = facts.get("yield_units", 0) >= 1
        elif key == "seeds[arg]>=1":
            if len(op) < 2:
                return None
            w["seeds[arg]>=1"] = facts["seeds"].get(op[1], 0) >= 1
            w["arg"] = op[1]
        elif key == "structure==arg_structure":
            if len(op) < 2 or op[1] not in STRUCTURE_OF:
                return None
            w["structure==arg_structure"] = (
                facts.get("structure") == STRUCTURE_OF[op[1]])
            w["arg"] = op[1]
        elif key == "carry[arg]>=n":
            if len(op) < 2:
                return None
            n = int(op[2]) if len(op) >= 3 else 1
            w["carry[arg]>=n"] = facts["carry"].get(op[1], 0) >= n
        elif key == "shed[arg]>=n":
            if len(op) < 2:
                return None
            n = int(op[2]) if len(op) >= 3 else 1
            w["shed[arg]>=n"] = facts["shed"].get(op[1], 0) >= n
            w["arg"] = op[1]
        else:
            w[key] = facts.get(key)
    return w


def holds(when, op, facts):
    """Does this motif's grounded precondition hold for THIS worker right now?

    Quantities are re-evaluated here against the live facts, which is why the
    lookup cache is not allowed to stand in for this check.
    """
    for k, v in when.items():
        if k == "arg":
            continue
        if k == "tile":
            if facts["tile"] != v:
                return False
        elif k == "shed_access":
            if bool(facts["shed_access"]) != v:
                return False
        elif k == "carry.WHEAT>=1":
            if (facts["carry"].get("WHEAT", 0) >= 1) != v:
                return False
        elif k == "carry_total>=1":
            if (facts["carry_total"] >= 1) != v:
                return False
        elif k == "yield_units>=1":
            if (facts.get("yield_units", 0) >= 1) != v:
                return False
        elif k == "seeds[arg]>=1":
            if (facts["seeds"].get(when.get("arg"), 0) >= 1) != v:
                return False
        elif k == "structure==arg_structure":
            if (facts.get("structure") == STRUCTURE_OF.get(when.get("arg"))) != v:
                return False
        elif k == "carry[arg]>=n":
            n = int(op[2]) if len(op) >= 3 else 1
            item = op[1] if len(op) > 1 else None
            if (facts["carry"].get(item, 0) >= n) != v:
                return False
        elif k == "shed[arg]>=n":
            n = int(op[2]) if len(op) >= 3 else 1
            if (facts["shed"].get(when.get("arg"), 0) >= n) != v:
                return False
        else:
            if facts.get(k) != v:
                return False
    return True


def coarse_key(facts):
    """Cache key for the COARSE candidate set only.

    Deliberately quantity-free and turn-free: what kind of tile this worker stands
    on, whether it can reach the shed, and WHICH items exist in its carry, seeds
    and shed -- never how many, and never anything about the other workers. Every
    candidate it returns is still put through `holds()` against fresh facts, so the
    omitted quantities and flags cannot admit anything.
    """
    return (facts["tile"], facts.get("animal"), facts.get("crop"),
            facts.get("structure"), bool(facts["shed_access"]),
            tuple(sorted(k for k, v in facts["carry"].items() if v)),
            tuple(sorted(k for k, v in facts["seeds"].items() if v)),
            tuple(sorted(k for k, v in facts["shed"].items() if v)))


# ------------------------------------------------------- sequential simulation

def effect_kind(before_f, before_p, after_f, after_p, i, op):
    """The engine-observed effect of one op IN CONTEXT.

    Same classification the cloud lab records in `constraints.unit_effects`;
    `compile_motifs.py` asserts the two agree on every compiled row, so a native
    proposal is judged by the same rule the compiled evidence was judged by.

    Known imprecision, kept deliberately: a shed WITHDRAWAL (PICKUP) lands in
    `tile_state_change` because nothing moved, no tile changed and the shed did
    not grow. The label is stable and shared with every recorded chain, and the
    guards below never rely on it alone -- exact per-worker deltas do that work.
    """
    if (after_f, after_p) == (before_f, before_p):
        return "none"
    bp = _pos(before_f, i)
    ap = _pos(after_f, i)
    bx, by = int(bp[0]), int(bp[1])
    if [int(ap[0]), int(ap[1])] != [bx, by]:
        return "moved"
    t0 = before_f["tiles"][by][bx]
    t1 = after_f["tiles"][by][bx]
    shed_up = sum(after_p["shed"].values()) > sum(before_p["shed"].values())
    if isinstance(t1, dict) and "animal" in t1 and not (
            isinstance(t0, dict) and "animal" in t0):
        return "installed_animal"
    if isinstance(t0, dict) and t0.get("kind") == "PLANT" and not (
            isinstance(t1, dict) and t1.get("kind") == "PLANT"):
        return "harvested" if op[0] == "HARVEST" else "destroyed_plant"
    if isinstance(t0, dict) and t0.get("kind") in ("COOP", "PASTURE") and \
            "animal" not in t0 and t1 is None:
        return "removed_structure"
    if isinstance(t0, dict) and t0.get("kind") == "WEED" and t1 is None:
        return "cleared_weed"
    if shed_up:
        return "stored_in_shed"
    return "tile_state_change"


def seed_blocked(unit_actions, seeds):
    """The interpreter's joint PLANT budget: demand over seeds drops EVERY PLANT
    of that crop, so three PLANTs against two seeds is not two plants, it is none.
    """
    demand = {}
    for a in unit_actions:
        if isinstance(a, (list, tuple)) and len(a) >= 2 and a[0] == "PLANT":
            demand[a[1]] = demand.get(a[1], 0) + 1
    return sorted(c for c, n in demand.items() if n > int(seeds.get(c, 0)))


def simulate(K, obs, config, seat, unit_actions, upto=None):
    """Replay this seat's unit phase sequentially on scratch state.

    One evolving copy, interpreter order, the pinned `_apply_unit_action`, and the
    same atomic PLANT drop -- so BUILD_PASTURE then PLACE COW installs, the reverse
    order does not, and a duplicate CARE on the same animal is seen as the no-op it
    is. `upto` stops after that many slots, which is how a proposal for slot i gets
    the exact prefix state slot i will act on.

    Each slot record carries the EXACT outcome for that worker -- final position,
    the full carried inventory, and the tile under it -- because effect category
    alone cannot tell two PICKUPs apart when the second one receives fewer units.
    """
    farm = copy.deepcopy(obs["farms"][seat])
    priv = copy.deepcopy(obs["private"])
    priv.setdefault("shed", {})
    board = int(_cfg(config, "boardSize", 10))
    tpd = int(_cfg(config, "turnsPerDay", 24))
    cap = int(_cfg(config, "shedCapacity", 100))
    day = int(obs["day"])
    blocked = set(seed_blocked(unit_actions, priv.get("seeds", {})))
    n = len(unit_actions) if upto is None else min(upto, len(unit_actions))
    slots = []
    for i in range(n):
        act = unit_actions[i]
        eff = list(act)
        if len(eff) >= 2 and eff[0] == "PLANT" and eff[1] in blocked:
            eff = ["PASS"]
        b_f, b_p = copy.deepcopy(farm), copy.deepcopy(priv)
        try:
            K._apply_unit_action(farm, priv, i, eff, board, day, tpd, cap)
        except Exception:
            farm, priv = b_f, b_p
        p = _pos(farm, i)
        x, y = int(p[0]), int(p[1])
        invs = priv.get("inventories", [])
        slots.append({
            "i": i, "op": list(act), "applied": eff,
            "kind": effect_kind(b_f, b_p, farm, priv, i, eff),
            "pos": (x, y),
            "carry": dict(invs[i]) if i < len(invs) else {},
            "tile": copy.deepcopy(farm["tiles"][y][x]),
        })
    return {"slots": slots, "kinds": [s["kind"] for s in slots],
            "blocked": sorted(blocked), "farm": farm, "priv": priv}


# --------------------------------------------------------- reservation contract

def consumption(op, facts):
    """What a candidate op would take out of a shared pool.

    Cash is never in the list because the unit phase spends none: BUILD is free and
    an animal is bought in the market phase. Everything here is a real reservation
    question -- a seed another PLANT needs, shed stock another PICKUP or a SELL
    order needs, a carried item another slot's PLACE or DROP needs.
    """
    name = op[0]
    out = {"seeds": {}, "shed": {}, "carry": {}, "tile": None}
    if name == "PLANT" and len(op) > 1:
        out["seeds"][op[1]] = 1
        out["tile"] = facts["at"]
    elif name == "PICKUP" and len(op) > 1:
        out["shed"][op[1]] = int(op[2]) if len(op) >= 3 else 1
    elif name == "PLACE" and len(op) > 1:
        out["carry"][op[1]] = int(op[2]) if len(op) >= 3 else 1
        out["tile"] = facts["at"]
    elif name == "FEED":
        out["carry"]["WHEAT"] = 1
        out["tile"] = facts["at"]
    elif name == "DROP":
        out["carry"] = dict(facts["carry"])
    elif name in ("BUILD_COOP", "BUILD_PASTURE", "DIG", "WATER", "CARE",
                  "HARVEST", "COLLECT_FERTILIZER"):
        out["tile"] = facts["at"]
    return out


def derived_contract(obs, config, seat, baseline):
    """A reservation contract read off THIS turn's baseline.

    It reserves exactly what the baseline's own ops and market orders need: the
    seeds its PLANTs consume, the shed stock its PICKUPs withdraw and its SELL
    orders sell, the carried items its PLACEs, FEEDs and DROPs spend, and every
    tile one of its ops targets.

    It is explicitly NOT a route model: it cannot see a tile the caller's plan
    wants free three turns from now. A caller that has one passes its own contract
    and, if it can price a turn, a `value` callable -- without which a proposal is
    limited to free upkeep on an asset the seat already holds.
    """
    K = engine()
    units = [list(baseline["farmer"])] + [list(h) for h in baseline["hands"]]
    seeds, shed, carry, tiles = {}, {}, {}, set()
    for i, op in enumerate(units):
        pre = simulate(K, obs, config, seat, units, upto=i)
        farm = pre["farm"] if i else obs["farms"][seat]
        priv = pre["priv"] if i else obs["private"]
        c = consumption(op, unit_facts(farm, priv, config, i))
        for k, v in c["seeds"].items():
            seeds[k] = seeds.get(k, 0) + v
        for k, v in c["shed"].items():
            shed[k] = shed.get(k, 0) + v
        for k, v in c["carry"].items():
            carry.setdefault(i, {})
            carry[i][k] = carry[i].get(k, 0) + v
        if c["tile"] is not None:
            tiles.add(tuple(c["tile"]))
    for o in baseline.get("market", []):
        if isinstance(o, (list, tuple)) and len(o) >= 2 and str(o[0]).startswith("SELL"):
            n = int(o[2]) if len(o) >= 3 else 1
            shed[o[1]] = shed.get(o[1], 0) + n
    return {"reserved_seeds": seeds, "reserved_shed": shed,
            "reserved_carry": carry, "reserved_tiles": sorted(tiles),
            "value": None, "source": "derived from this turn's baseline only"}


# ------------------------------------------------------------------- proposing

class Proposer:
    """Bounded, cached, guarded motif proposals over a chosen baseline turn."""

    def __init__(self, table, contract=None, allow_displace_move=False):
        self.motifs = [m for m in table.get("motifs", []) if m.get("proposable")]
        self.meta = dict(table.get("meta", {}))
        self.contract = contract
        # Movement displacement is off unless the caller both asks for it AND
        # supplies a way to price the forgone step. Without a value function there
        # is nothing to weigh the lost movement against, and "the slot did
        # something else instead" is not an improvement.
        self.allow_displace_move = bool(
            allow_displace_move and contract and callable(contract.get("value")))
        self._cache = {}
        self.K = engine()

    def _candidates(self, facts):
        """Coarse cached set, then a fresh `holds()` on every candidate."""
        key = coarse_key(facts)
        coarse = self._cache.get(key)
        if coarse is None:
            coarse = [m for m in self.motifs
                      if m["when"].get("tile", facts["tile"]) == facts["tile"]
                      or "tile" not in m["when"]]
            coarse.sort(key=lambda m: (-m["support"]["total"], m["id"]))
            self._cache[key] = coarse
        out = []
        for m in coarse:
            if holds(m["when"], m["op"], facts):
                out.append(m)
            if len(out) >= MAX_SLOT_CANDIDATES:
                break
        return out

    def _contract_ok(self, op, facts, i, contract):
        """Reserved resources and reserved tiles, checked against what remains free."""
        c = consumption(op, facts)
        for item, n in c["seeds"].items():
            free = facts["seeds"].get(item, 0) - contract["reserved_seeds"].get(item, 0)
            if n > free:
                return f"seed {item} is reserved ({free} free)"
        for item, n in c["shed"].items():
            free = facts["shed"].get(item, 0) - contract["reserved_shed"].get(item, 0)
            if n > free:
                return f"shed {item} is reserved ({free} free)"
        for item, n in c["carry"].items():
            free = facts["carry"].get(item, 0) - \
                contract["reserved_carry"].get(i, {}).get(item, 0)
            if n > free:
                return f"carried {item} is reserved ({free} free)"
        if c["tile"] is not None and tuple(c["tile"]) in {
                tuple(t) for t in contract["reserved_tiles"]}:
            return f"tile {tuple(c['tile'])} is reserved by the baseline"
        return None

    def propose(self, obs, config, seat, baseline):
        """Return (action, notes).

        `action` is the baseline unless a proposal survived every guard; `notes`
        always states what was displaced, what was reserved, and what was and was
        not established.
        """
        notes = []
        if not self.motifs:
            return baseline, [{"skipped": "no proposable motifs in the table"}]
        units = [list(baseline["farmer"])] + [list(h) for h in baseline["hands"]]
        contract = self.contract or derived_contract(obs, config, seat, baseline)
        base = simulate(self.K, obs, config, seat, units)
        accepted = 0
        for i in range(len(units)):
            if accepted >= MAX_ACCEPTED:
                break
            kind = base["kinds"][i]
            # OPPORTUNITY COST, assessed before anything is displaced. A baseline
            # PASS proves the slot produced nothing; it does NOT prove the swap is
            # free, which is what the contract check below is for.
            if kind == "none":
                cost = f"baseline {units[i]} did nothing in context"
            elif kind == "moved" and self.allow_displace_move:
                cost = f"forgoes one step of baseline movement {units[i]}"
            else:
                continue
            # facts from the ORDERED PREFIX, including swaps already accepted
            pre = simulate(self.K, obs, config, seat, units, upto=i)
            farm = pre["farm"] if i else obs["farms"][seat]
            priv = pre["priv"] if i else obs["private"]
            facts = unit_facts(farm, priv, config, i)
            for m in self._candidates(facts):
                op = list(m["op"])
                if op == units[i]:
                    continue
                # Without a value function only free upkeep is testable: an op that
                # consumes a shared pool cannot be called an improvement just
                # because it changed the state.
                if not callable(contract.get("value")) and op[0] not in FREE_UPKEEP_OPS:
                    notes.append({"slot": i, "motif": m["id"], "rejected":
                                  f"{op[0]} consumes a shared pool and the contract "
                                  f"carries no value function to price it"})
                    continue
                bad = self._contract_ok(op, facts, i, contract)
                if bad:
                    notes.append({"slot": i, "motif": m["id"], "rejected": bad})
                    continue
                trial = list(units)
                trial[i] = op
                if seed_blocked(trial, obs["private"].get("seeds", {})):
                    notes.append({"slot": i, "motif": m["id"], "rejected":
                                  "joint seed reservation would drop every PLANT"})
                    continue
                sim = simulate(self.K, obs, config, seat, trial)
                if sim["slots"][i]["kind"] in FORBIDDEN_EFFECTS:
                    notes.append({"slot": i, "motif": m["id"], "rejected":
                                  f"candidate effect {sim['slots'][i]['kind']}"})
                    continue
                # EXACT preservation of every other worker: same effect, same final
                # position, same carried inventory, same tile underneath. Category
                # equality is not enough -- two PICKUPs stay `tile_state_change`
                # while the later one receives fewer units.
                diff = [j for j in range(len(units)) if j != i
                        and (sim["slots"][j]["kind"] != base["slots"][j]["kind"]
                             or sim["slots"][j]["pos"] != base["slots"][j]["pos"]
                             or sim["slots"][j]["carry"] != base["slots"][j]["carry"]
                             or sim["slots"][j]["tile"] != base["slots"][j]["tile"])]
                if diff:
                    notes.append({"slot": i, "motif": m["id"], "rejected":
                                  f"changes the exact outcome of worker(s) {diff}"})
                    continue
                if float(sim["farm"]["money"]) < float(base["farm"]["money"]):
                    notes.append({"slot": i, "motif": m["id"], "rejected":
                                  "spends cash the baseline kept"})
                    continue
                if callable(contract.get("value")):
                    v0 = contract["value"](obs, config, seat, units)
                    v1 = contract["value"](obs, config, seat, trial)
                    if v1 <= v0:
                        notes.append({"slot": i, "motif": m["id"], "rejected":
                                      f"caller value {v1} does not exceed {v0}"})
                        continue
                units[i] = op
                base = sim
                accepted += 1
                notes.append({
                    "slot": i, "motif": m["id"], "accepted": op,
                    "effect": sim["slots"][i]["kind"], "displaced": cost,
                    "support": m["support"], "provenance": m["provenance"],
                    "established": "the slot was idle, the precondition holds on the "
                                   "prefix state, nothing reserved was consumed, and "
                                   "every other worker's exact outcome is unchanged",
                    "not_established": "any cash gain -- no cash claim is made here",
                })
                break
        if accepted == 0:
            return baseline, notes
        out = dict(baseline)
        out["farmer"] = units[0]
        out["hands"] = units[1:]
        return out, notes


def load_table():
    """Import the compiled literal. No disk read, no network, no model."""
    try:
        import motifs_table
        return motifs_table.TABLE
    except Exception:
        return {"motifs": [], "meta": {"loaded": False}}
