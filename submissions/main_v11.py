"""Kaggriculture submission v11: the SE closed-loop overlay on the V8.1 tape.

Single stdlib-only file that bakes two modules together:

* ``src/kaggriculture/agents/baseline_v8_1.py`` -- the incumbent route-replay
  tape (the huge ``_BLOB`` payload decoded by :func:`routes`);
* ``src/kaggriculture/agents/se_loop.py`` -- the SE closed-loop overlay that
  unlocks the fourth quadrant and farms it on top of the untouched tape.

The exposed entry point is ``agent = make_agent()``: a plain one-argument
function (the runner truncates a callable instance's arguments by
``__code__.co_argcount``).  ``make_agent()`` wraps the inlined tape with
:class:`SELoopOverlay`.

Merge notes -- no behavior edits beyond de-colliding names:

* the tape's ``ANIMALS`` mapping is renamed ``TAPE_ANIMALS`` so the engine's
  ``ANIMALS`` table, inlined for the overlay, cannot shadow it at call time;
* the tape's ``agent`` is renamed ``_tape_agent`` so the module-level
  ``agent = make_agent()`` becomes the exposed entry point;
* the engine constants ``CROPS`` / ``ANIMALS`` / ``FARMER_MOVES`` /
  ``LAND_PRICES`` and the ``current_step`` helper are inlined so the file
  imports only the standard library;
* ``se_loop``'s ``load_agent(path)`` tape reference becomes the inlined
  ``_tape_agent``; nothing else in either module changed.
"""

from __future__ import annotations

# ===================================================================== #
# Section 1 -- baseline_v8_1 tape (verbatim; two names de-collided).
# ===================================================================== #

"""Kaggriculture agent baseline_v8: pruned non-starving route replay with shed capacity guard.

Building on baseline_v7, baseline_v8 eliminates the toxic decision switches (YARN_CARROT and
MILK_GLUT) that forensic analysis of 58 public ladder matches showed caused 100% of livestock
starvation (20/20 games) due to premature wheat liquidation. Retains MAIN and YARN, which
achieve 100% livestock survival and positive margin against the entire public field.
"""
import base64
import json
import zlib

TURNS = 720
BOARD = 10
MAX_ORDERS = 10
SHED_CAP = 100
PRODUCTS = ("WHEAT", "CARROT", "TOMATO", "STRAWBERRY", "MELON", "EGG", "MILK", "WOOL",
            "FERTILIZER")
TAPE_ANIMALS = {"GOOSE": "COOP", "COW": "PASTURE", "SHEEP": "PASTURE"}
MOVES = {"NORTH": (0, -1), "SOUTH": (0, 1), "EAST": (1, 0), "WEST": (-1, 0)}
PASS = {"farmer": ["PASS"], "hands": [], "market": []}

MAIN = "7015cc00acfa4922"
YARN = "dc76e4003029ac51"

# (turn, feature, threshold, target tail)
# Forensic replay analysis of 58 public ladder matches showed that 100% of animal
# starvation events (20/20 matches) occurred exclusively when branching into YARN_CARROT
# or MILK_GLUT due to tape-prescribed wheat liquidation. MAIN and YARN maintain a 100%
# animal survival rate, higher mean margins, and complete ladder stability.
DECISIONS = (
    (226, "shop_YARN_STORE", 1, YARN),
)

_BLOB = (
    "eNrtnVtvZNeRrP9LP/cDq3j3myxxbGFkt9CSTmHOgDDausDGmTEG9hgYYOD/fqgmWazaK/LLyFyb3ZLMJ1Ekm7X22uuSGRkZ8b+v"
    "/vPdn//y6levLk82599+e3Ly7tsf3p1db7evXr/64e//8R+vfvXv//vqh3d//c/v/3r35asvP/nqq1e3r1/96d1fvvvb3TfuvvzP"
    "d3/9f9//993X//7q19/82x++fPvms28+/frun+9+e/PJ3X83p7e3/3h9+Ed+/+bt17+N/8pXN1988fTPr29f3//hr25uPnv69uXi"
    "27+7+eLN7+8+bPvj93/7+dubV+4XP/6RT37/+e8++fFDP32ze/V6O3z7q9/e3Hz54w8WT7K7+errwwe5m6DPP/3Xb758+Es//p2H"
    "X3l66oNvHf8yzcJJ/slPf3//Z++HffzhT1/9+pvPv/jsD3dv9OtvfpwK+vjN44yMb3c5sC+/+OT3X+9fyNEIFx94ONL7v3Y8PvEY"
    "d3/80xtvvuwB7z75+ubt4iUefox+h2Lkn36STeO2MKrh/d7/+ddyGhdzfjDi+y+e/90+remHuXt6aY8TPG6BlYYl3uDTJ/3Lj+fD"
    "0ZSM87fqaG4+Gc6E4fXs3yVM2+JfPdvo4tdzNHv3v7XSKB6eX60fMYjFbK2075dXmXpPB/v94WdP2/3w1UUvKt3UT5Mv9rB6MeNr"
    "yF/w/iwQf3D4Cv707IyNv734nWeYuuGr2al7+KXD9/9+Wlrz9jQ3+z8bT9vBd7JPnJ+t+/HUHur+O3Ky9j/rTNbxv4UZWevvH0zW"
    "uuMOvqA/u4wzgqA5jXTzFXKzXNhjnJrv0yG2l6GSOO3z4+fhLjr4y/vbSZ1n9z+EvzdeQhBiPf3dMcxb/t03X3xx8+nXf/iXm7df"
    "f/7F5/93GZvIn2N8ov5F6Rx7jC6ij9yvx6/efLN8C/sr9uAfw10/ZHsPfzK57B9/S491YjxnIogd58J4Pfsz4WmoEI94g9vmQcl+"
    "TRZzj/QPP11kT8f/xJX22ds3Xx6f+fvdOXN2Hjz1Gkfxyif7uIF+QoP7aU/duqHBP9PgOlHE3F8LTjUOVe5O3gThO/xrJxW47hgK"
    "PL9NAwWIbfKcclvIXr3AJkX3shBCJMmVSEoEafgX89RpnHJIMa0xjpM8jKeY3mWTKtIWAqzSgFpdsIXhwioWLysdHy1aNb7072Wz"
    "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"
    "w1eEe0r/fPHmlBeX2LxigyTXixo6NbiAsokaI4gQuFyHSEhhDdYnW327x9fKj8e4TcSVMQsxzEPdP0Lwyx1wWFnOanWpiZ9VNq12"
    "v2ULwRxLVvufrpTa0ygF0Uq091Xm1SVpmeShOAiemFPUVD3GhTbbgiYwKOvV55Y0KbUFSaWmvvM6wds7zn8F2fg5CxHvA3QDV6Fm"
    "PS6Qx5D7MsVAdihaiE328bCfPr449XJjdmUOUs3HVVaTLcuAkY+11P0FlI6xYMLuFPPJ8iZvYHBnsC2nWXwyTO3TBZAV3ie/iLPi"
    "0bkokjBU9fqBARD3YBR/smE1xdixtvhvNjC25JhaNHUcG9Q+9Iuc5LegA7xQ0l046fq6uoQxNMSOOrAp+nREFibagJOqVysxGtg4"
    "1++Q9CmKNWPJw//Lqnkl18TJV5swlEVKqPskHDmJrtQ7+5k0ZrpV2QjSEOFvm7mci21UV+kKnj/cjPL10jNV61H605xRELLa5D1l"
    "WJH8+Ggmq+ucTwY2udipKnnQJtghJCM4bMr2DWOaWMa0e629HGitItpRW9qM2SQoqBzQxHHZ9k4O5qlQ7cXVpMzBsjiFUl5nSaUT"
    "RHSCLJYp6BAR7/+J5Pn2zZepLrbLdFnExte3hRHtB7JPqPbfUft//0NrRA8A8GFkfg+DiVLjUUnyPqQPio8DT/RhUAePNz7V+MX+"
    "d2AK7r+AZzsfnk2VUc8upfGAo7hOqertP+7+77/f/fk/fvzB/77606tfvfru28uL789OTk5Pttfvvj3f3P35/3r31+//cvfrry5P"
    "Nufffnty8u7bH96dXW+3dz97d/f97fbi9au//f2HH/78P+//jKexyuUxk+y3JM0hL25IpPf+3A8/+eT3n//uk/eV7t/e3Hzptnma"
    "7b3okGRnCUtlDORVBqIfBeoisDrCyayIrbryH+P9Mn7hWwD7rerIyMI1iu697ZnD0ErwvcWdMFviFa7pZiNyyiZqYuKwJx7PhwG0"
    "SvYIchCWjK1GLQXebH1PeOuwFZAtO3s2focsbNC6vxE6avkVpxBTZ2LYa28dilEfcXv1ShSAPyZVzsiHDzl+opPayAND8uChzmem"
    "HKM0oq129JVKS3K4M0Jm8GG/37PSAYEj3cSirZjcEGhWwdSWCxmnhaerPEJbPso41aOA8rz1LPWvxhbLitptMTZ3v1Kacmre5eUp"
    "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"
    "lv4afga3//j/YDJN7Q=="
)


_ROUTES = None


FINAL_EXECUTABLE_STEP = TURNS - 2


def _terminal_settlement(projected_shed, prices, market):
    """Sell every projected final product while preserving inherited order slots."""
    result = []
    covered = set()

    # Keep inherited ordering. Duplicate sells are collapsed, and an existing
    # product slot is expanded to the exact post-unit-action shed quantity.
    for raw in market[:MAX_ORDERS]:
        order = list(raw)
        if order and order[0] == "SELL" and len(order) >= 3 and order[1] in PRODUCTS:
            item = order[1]
            if item in covered:
                continue
            quantity = max(0, int(projected_shed.get(item, 0) or 0))
            if quantity <= 0:
                continue
            order[2] = quantity
            covered.add(item)
        result.append(order)

    # Under the supplied routes the final market contains only product sells, so
    # nine product types fit into ten slots. Value ordering is a defensive fallback
    # if a different compatible tail has already occupied some of those slots.
    missing = [
        (max(0, int(projected_shed.get(item, 0) or 0))
         * max(1, int(prices.get(item, 1) or 1)), item)
        for item in PRODUCTS
        if item not in covered and int(projected_shed.get(item, 0) or 0) > 0
    ]
    missing.sort(key=lambda row: (-row[0], row[1]))
    for _value, item in missing:
        if len(result) >= MAX_ORDERS:
            break
        result.append(["SELL", item, max(0, int(projected_shed[item]))])
        covered.add(item)
    return result


def routes():
    """Decode lazily: only MAIN is stored whole, the rest as (parent, turn, suffix)."""
    global _ROUTES
    if _ROUTES is None:
        p = json.loads(zlib.decompress(base64.b64decode(_BLOB)).decode())
        out = {p["main"]: p["full"]}
        for t in p["tails"]:
            out[t["h"]] = out[t["parent"]][:t["at"]] + t["suffix"]
        _ROUTES = out
    return _ROUTES


def _shed_adjacent(x, y, board=BOARD):
    h = board // 2
    return (x, y) in ((h - 1, h - 1), (h, h - 1), (h - 1, h), (h, h))


def _feature(obs, name):
    if name == "shop_YARN_STORE":
        return (obs.get("town", {}).get("unlocked_shops") or []).count("YARN_STORE")
    if name == "px_CARROT":
        return obs["market"]["prices"].get("CARROT", 0)
    if name == "inv_MILK":
        return obs["market"]["inventory"].get("MILK", 0)
    return 0


def _noop(act, tile, inv, seeds, x, y, board=BOARD):
    """True when the engine will certainly ignore this action (kaggriculture.py::
    _apply_unit_action). Only used to decide whether a turn is free to reuse."""
    if not act:
        return True
    op = act[0]
    if op in MOVES:
        dx, dy = MOVES[op]
        return not (0 <= x + dx < board and 0 <= y + dy < board)
    if op == "PASS":
        return True
    if op == "DROP":
        return (not _shed_adjacent(x, y, board)) or (not inv)
    if op == "PICKUP":
        return not _shed_adjacent(x, y, board)
    if op == "PLACE":
        item = act[1] if len(act) > 1 else None
        if (item in TAPE_ANIMALS and isinstance(tile, dict)
                and tile.get("kind") == TAPE_ANIMALS[item] and tile.get("animal") is None):
            return inv.get(item, 0) <= 0
        if _shed_adjacent(x, y, board):
            return inv.get(item, 0) <= 0
        return True
    if tile == "LOCKED":
        return True
    isd = isinstance(tile, dict)
    kind = tile.get("kind") if isd else None
    animal = isd and tile.get("animal") is not None
    if op == "PLANT":
        return tile is not None or seeds.get(act[1] if len(act) > 1 else None, 0) <= 0
    if op == "WATER":
        return kind != "PLANT" or bool(tile.get("watered_today"))
    if op == "HARVEST":
        return (not isd) or tile.get("yield_units", 0) <= 0
    if op == "FERTILIZE":
        return kind != "PLANT" or inv.get("FERTILIZER", 0) <= 0
    if op == "DIG":
        return tile is None or animal
    if op in ("BUILD_COOP", "BUILD_PASTURE"):
        return tile is not None
    if op == "FEED":
        return (not animal) or bool(tile.get("fed_today")) or inv.get("WHEAT", 0) <= 0
    if op == "COLLECT_FERTILIZER":
        return (not animal) or (not tile.get("fertilizer_available"))
    if op == "CARE":
        return (not animal) or bool(tile.get("cared_today"))
    return True


class Agent:
    def __init__(self):
        self.R = routes()
        self.cur = MAIN
        self._fs = None
        self._fs_for = None

    # ---- how much of each product does the rest of the route still intend to sell? ----
    def future_sells(self, item, step):
        if self._fs_for != self.cur:
            r = self.R[self.cur]
            fs = dict((p, [0] * (len(r) + 1)) for p in PRODUCTS)
            for t in range(len(r) - 1, -1, -1):
                add = {}
                for o in (r[t].get("market") or []):
                    if o and o[0] == "SELL" and o[1] in fs:
                        add[o[1]] = add.get(o[1], 0) + int(o[2])
                for p in fs:
                    fs[p][t] = fs[p][t + 1] + add.get(p, 0)
            self._fs = fs
            self._fs_for = self.cur
        a = self._fs.get(item)
        return a[step] if a and step < len(a) else 0

    def _switch_ok(self, target, turn):
        """A switch is legal only onto a tail identical to the current one so far."""
        a, b = self.R[self.cur], self.R[target]
        if a is b:
            return False
        for t in range(turn):
            if a[t] != b[t]:
                return False
        return True

    def act(self, obs):
        s = obs.get("step")
        step = int(s) if s is not None else int(obs.get("day", 0)) * 24 + int(obs.get("hour", 0))
        me = int(obs.get("player", 0))
        farm = obs["farms"][me]
        priv = obs["private"]
        tiles = farm["tiles"]
        seeds = priv.get("seeds") or {}
        invs = priv.get("inventories") or []
        shed = dict(priv.get("shed") or {})
        prices = obs["market"]["prices"]
        day = int(obs.get("day", step // 24))
        board = len(tiles) or BOARD

        for (turn, feat, thr, target) in DECISIONS:
            if turn == step and target != self.cur and self._switch_ok(target, turn):
                if _feature(obs, feat) >= thr:
                    self.cur = target

        route = self.R[self.cur]
        base = route[step] if step < len(route) else PASS
        acts = [list(base.get("farmer") or ["PASS"])] + [list(h) for h in (base.get("hands") or [])]
        market = [list(o) for o in (base.get("market") or [])]
        positions = [tuple(farm["farmer"])] + [tuple(p) for p in farm["hands"]]

        # ---- weed_dig: a wasted turn spent standing on a weed becomes a DIG ----
        for i in range(min(len(acts), len(positions))):
            x, y = positions[i]
            if not (0 <= x < board and 0 <= y < board):
                continue
            tile = tiles[y][x]
            inv = invs[i] if i < len(invs) else {}
            if (isinstance(tile, dict) and tile.get("kind") == "WEED"
                    and _noop(acts[i], tile, inv, seeds, x, y, board)):
                acts[i] = ["DIG"]

        # ---- projected shed: a same-turn DROP/PLACE lands before market processing ----
        proj = dict(shed)
        room = SHED_CAP - sum(proj.values())
        for i in range(min(len(acts), len(positions))):
            if room <= 0:
                break
            x, y = positions[i]
            inv = invs[i] if i < len(invs) else {}
            if not inv or not _shed_adjacent(x, y, board):
                continue
            a = acts[i]
            if a and a[0] == "DROP":
                for it, n in inv.items():
                    take = min(n, room)
                    if take > 0:
                        proj[it] = proj.get(it, 0) + take
                        room -= take
            elif a and a[0] == "PLACE" and len(a) > 1 and a[1] not in TAPE_ANIMALS:
                it = a[1]
                take = min(int(a[2]) if len(a) > 2 else 1, inv.get(it, 0), room)
                if take > 0:
                    proj[it] = proj.get(it, 0) + take
                    room -= take

        # ---- room_guard: at day close, sell enough to avoid shed overflow ----
        if step % 24 == 23:
            carried = sum(max(0, int(n)) for inv in invs for n in inv.values())
            produced = 0
            consumed = 0
            for i in range(min(len(acts), len(positions))):
                x, y = positions[i]
                if not (0 <= x < board and 0 <= y < board):
                    continue
                tile = tiles[y][x]
                a = acts[i]
                if not a:
                    continue
                op = a[0]
                if op == "HARVEST" and isinstance(tile, dict):
                    produced += max(0, int(tile.get("yield_units", 0)))
                elif op == "COLLECT_FERTILIZER" and isinstance(tile, dict) and tile.get("fertilizer_available"):
                    produced += 1
                elif op in ("FEED", "FERTILIZE"):
                    consumed += 1
                elif op == "PLACE" and len(a) > 1 and a[1] in TAPE_ANIMALS:
                    consumed += 1
            planned_sells = {}
            planned_buys = 0
            for o in market:
                if not o:
                    continue
                if o[0] == "SELL":
                    planned_sells[o[1]] = planned_sells.get(o[1], 0) + max(0, int(o[2]))
                elif o[0] in ("BUY_PRODUCT", "BUY_ANIMAL"):
                    planned_buys += max(0, int(o[2]))
            shed_total = sum(max(0, int(n)) for n in shed.values())
            actual_existing_sells = sum(min(max(0, int(shed.get(it, 0))), n) for it, n in planned_sells.items())
            needed = shed_total + carried + produced - consumed + planned_buys - actual_existing_sells - (SHED_CAP - 1)
            if needed > 0:
                priority = sorted(PRODUCTS, key=lambda it: (self.future_sells(it, step + 1) > 0, -prices.get(it, 0), it))
                for it in priority:
                    already = planned_sells.get(it, 0)
                    available = max(0, int(shed.get(it, 0)) - already)
                    qty = min(needed, available)
                    if qty <= 0:
                        continue
                    slot = next((j for j, o in enumerate(market) if o and o[0] == "SELL" and o[1] == it), -1)
                    if slot >= 0:
                        market[slot][2] = max(0, int(market[slot][2])) + qty
                    elif len(market) < MAX_ORDERS:
                        market.append(["SELL", it, qty])
                    else:
                        continue
                    planned_sells[it] = already + qty
                    needed -= qty
                    if needed <= 0:
                        break

        # ---- clamp_sells: a SELL the shed cannot fill burns one of only 10 slots ----
        # SELL draws from the shed alone, and the projection above already credits the
        # goods a same-turn DROP/PLACE will put there, so this only drops orders that
        # really cannot fill.
        avail = dict(proj)
        kept = []
        for o in market:
            if o and o[0] == "SELL":
                have = avail.get(o[1], 0)
                if have <= 0:
                    continue
                n = min(int(o[2]), have)
                if n <= 0:
                    continue
                avail[o[1]] = have - n
                kept.append(["SELL", o[1], n])
            else:
                kept.append(o)
        market = kept

        # ---- dead_stock: sell what the rest of the route will never get to ----
        planned = {}
        for o in market:
            if o and o[0] == "SELL":
                planned[o[1]] = planned.get(o[1], 0) + int(o[2])
        extra = []
        for it in PRODUCTS:
            have = proj.get(it, 0) - planned.get(it, 0)
            if have <= 0:
                continue
            surplus = have if day >= 28 else have - self.future_sells(it, step + 1)
            if surplus > 0 and prices.get(it, 0) > 1:
                extra.append(["SELL", it, surplus])
        extra.sort(key=lambda o: -prices.get(o[1], 0) * int(o[2]))

        market = (market + extra)[:MAX_ORDERS]
        if step == FINAL_EXECUTABLE_STEP:
            market = _terminal_settlement(proj, prices, market)

        return {"farmer": acts[0], "hands": acts[1:], "market": market}


_A = None


def _tape_agent(obs):
    """A crash here forfeits the game, so any failure degrades to a legal PASS."""
    global _A
    try:
        s = obs.get("step")
        step = int(s) if s is not None else int(obs.get("day", 0)) * 24 + int(obs.get("hour", 0))
        if _A is None or step == 0:
            _A = Agent()
        return _A.act(obs)
    except Exception:
        try:
            hands = obs["farms"][int(obs.get("player", 0))].get("hands") or []
        except Exception:
            hands = []
        return {"farmer": ["PASS"], "hands": [["PASS"] for _ in hands], "market": []}

# ===================================================================== #
# Section 2 -- engine constants, inlined.
#
# Copied verbatim from kaggle_environments.envs.kaggriculture.kaggriculture so
# this submission stays a single stdlib-only file.  The tape's own TAPE_ANIMALS
# mapping is a different, unrelated table.
# ===================================================================== #

CROPS = {
    "WHEAT":      {"seed": 10, "first_yield_day": 2, "max_yield_day": 4, "interval": 0, "max_yield": 6, "ongoing": False},
    "CARROT":     {"seed": 20, "first_yield_day": 2, "max_yield_day": 3, "interval": 0, "max_yield": 4, "ongoing": False},
    "TOMATO":     {"seed": 50, "first_yield_day": 8, "max_yield_day": 8, "interval": 1, "max_yield": 4, "ongoing": True},
    "STRAWBERRY": {"seed": 100, "first_yield_day": 10, "max_yield_day": 10, "interval": 2, "max_yield": 4, "ongoing": True},
    "MELON":      {"seed": 80, "first_yield_day": 10, "max_yield_day": 12, "interval": 0, "max_yield": 6, "ongoing": False},
}

ANIMALS = {
    "GOOSE": {"cost": 300, "structure": "COOP",    "first_yield_day": 4, "interval": 1, "max_held": 4, "product": "EGG"},
    "COW":   {"cost": 400, "structure": "PASTURE", "first_yield_day": 8, "interval": 2, "max_held": 6, "product": "MILK"},
    "SHEEP": {"cost": 500, "structure": "PASTURE", "first_yield_day": 6, "interval": 3, "max_held": 6, "product": "WOOL"},
}

# (dx, dy); y grows downward.
FARMER_MOVES = {
    "NORTH": (0, -1),
    "SOUTH": (0, 1),
    "EAST":  (1, 0),
    "WEST":  (-1, 0),
}

LAND_PRICES = [1000, 2000, 4000]


def current_step(obs):
    """Turn index from an observation, the way every tape agent derives it.

    Inlined verbatim from ``kaggriculture.loading.current_step``: uses ``step``
    when present and falls back to ``day * 24 + hour``.  Never raises: an
    observation whose ``step``/``day``/``hour`` is missing, ``None`` or
    unparseable degrades to turn 0 rather than propagating an exception into
    the match loop.
    """
    try:
        raw = obs.get("step")
        if raw is not None:
            return int(raw)
        return int(obs.get("day", 0)) * 24 + int(obs.get("hour", 0))
    except (AttributeError, TypeError, ValueError):
        return 0


# ===================================================================== #
# Section 3 -- se_loop overlay (verbatim; wiring points inlined).
# ===================================================================== #

"""SE closed-loop overlay on the incumbent V8.1 tape.

What this is
------------
`odd/tasks/se-closed-loop-agent.md` (S1) asks for the event-study trajectory as an
agent: the seats that buy the 4th (SE) quadrant around day 10 absorb a ~$5k
mid-game cash deficit, turn the new cells into extra CROP tiles, and cross into a
positive bank gap on days 22-25. The failed fixed-tape overlays bought the land but
"authored no SE crop choreography and could not fund the deficit". This module is
the closed loop: gate, seed purchase, planting, watering, harvesting, and
appended-only extra hands - all inside SE, all on top of the untouched tape.

House pattern: `src/kaggriculture/agents/v10_partial_quadrant.py`. Same factory
shape (`make_agent(...)`), same ownership rules, same "never rewrite a tape slot"
transform. The differences are deliberate and are the point of S1:

* the job board is a closed loop (PLANT -> WATER -> HARVEST -> carry to shed),
  not just the first two days of choreography, because a crop that is never
  harvested cannot "fund the deficit";
* the crop mix is parametrized per crop with quantities (`seed_mix`), and each
  crop gets ONE quantity order per turn, never a per-unit order loop;
* the hires happen after the tape's own daily hiring burst, so the ownership
  arithmetic is a verified equality (`roster delta == hires_today delta ==
  ours`), not a hope;
* the land gate is evaluated NET of the tape's same-turn spending, because the
  tape's orders resolve before ours in the market-phase lockstep.

Design decisions (thresholds, mix, hands)
-----------------------------------------
Day/threshold. `buy_day=10` mirrors the study ("buy around day 10"). The gate
also requires exactly three unlocked quadrants, so the overlay can never compete
with the tape's own land cliff (the tape buys NE at step 150 and SW at step 265;
it never buys SE), and `money - tape_same_turn_spend >= $4,000 + land_reserve`
so the appended BUY_LAND actually commits.

`land_reserve` defaults to the seed cost of the whole mix (the doc's "seed
reserve for the planned first plantings"). With the default mix that is $260.

Mix. Default `{CARROT: 8, WHEAT: 8}` = 16 tiles, the event-study target
("~16 extra CROP tiles", 58 -> 71-72). Both are the cheap fast tiles the doc
asks for (seed $10/$20, 4-5 day cycles) AND both markets are *under-supplied*
all season in a tape-vs-tape game (measured at day 22: WHEAT $41, CARROT $61;
they end at $36/$115 as the CARROT inventory stays under the engine's reference
level), so the added supply is absorbed by the hinge/log shape at little price
cost. The two crops are split evenly to hedge that shared-market hit; MELON was
tried and rejected: its ``sq`` glut shape collapses the price to the $1 floor
after ~150 units, which is ~1.5 hands' worth of crop.

Hands. `extra_hands_cap=1`. The n-th hire of a day costs fib(n) and the tape
hires 9-12 hands/day, so OURS is the 10th-13th of the day: ~$144-377 per hand
per day, ~$3.1k over the season. A second hand adds ~$5k, which is more than the
marginal crops it can tend (measured in the S1 report), so the default stays at
one. They are hired from hour 5, after the tape's own 1am-4am hiring burst, so a
new hand lands at the roster TAIL and never shifts an index the tape's route
uses.

Watering. Same verified lesson as the house pattern: `_new_plant` starts
`consecutive_unwatered=1`, so a crop not watered on its planting day is a WEED at
the next midnight. `needs_water` therefore answers True on the planting day AND
inside the growth window.

The engine traps encoded here
-----------------------------
* **Atomic PLANT validation**: if the turn's total PLANT demand for a crop
  exceeds the seeds held, EVERY plant of that crop is voided, the tape's
  included. Our plants only use seeds left over after the tape's demand plus
  `seed_margin`, so the overlay cannot trip the tape's planting. (Measured: the
  tape's own per-turn demand is 1-3 per crop, but it already self-voids ~6
  crop-turns per season by planting more than it holds - a $92k class of risk we
  refuse to add to.)
* **HIRE/BUY_LAND resolve in the market phase, in order, before unit actions.**
  A hand hired on turn t is visible on t+1. Our appended orders resolve AFTER
  the tape's own, which is why the gate subtracts the tape's same-turn spend.
* **Malformed payloads are silent no-ops** (`["PLANT"]`, `["WATER", "WHEAT"]`,
  bare verbs without their argument). Every action this module emits is complete:
  `["PLANT", crop]`, `["WATER"]`, `["HARVEST"]`, `["DIG"]`, `["DROP"]`,
  `["BUY_SEED", crop, n]`, `["BUY_LAND"]`, `["HIRE"]`.
* **DROP discards the overflow**: dropping into a full shed deletes the
  remainder of the load. Our hands only drop when the shed can take the whole
  load.

Byte-identity
-------------
With `enabled=False`, or whenever the gate never fires (unfunded), the returned
action is the tape's own dict with the same `farmer`, the same hand entries (by
identity) and the same market list prefix. The overlay only ever *appends* market
orders and *replaces* the hand entries at indices it owns.

Known, measured interactions (not hidden)
-----------------------------------------
* Unlocking SE makes its tiles empty rather than LOCKED, so a tape hand whose
  recorded route passes over them can in principle plant there. The overlay
  accepts any PLANT it finds on its own plan tiles (waters/harvests it) instead
  of fighting it.
* The tape's hour-23 room guard counts our hands' carried goods (it sums every
  inventory), so it may sell a little earlier than it would alone. That is the
  tape protecting its shed, and the alternative (goods decaying in a hand) is
  worse.
"""

BOARD = 10
TURNS_PER_DAY = 24
MAX_ORDERS = 10  # engine maxMarketOrdersPerTurn: extras are silently dropped
SHED_CAPACITY = 100  # engine default; DROP deletes whatever does not fit
SE_QUADRANT = "SE"
SE_PRICE = LAND_PRICES[2]  # 4000 -- the third paid quadrant is always SE

#: Cheap fast one-shot crops, split evenly: CARROT (seed 20, 3 units / 4 days,
#: $40-$115 a unit) and WHEAT (seed 10, 4 units / 5 days, $25-$42 a unit). Both
#: markets are *under-supplied* for the whole tape-vs-tape season (measured day
#: 22: WHEAT $41, CARROT $61), so added supply is absorbed at little price cost -
#: unlike MELON, whose quadratic glut shape collapses its price to $1 after ~150
#: units. The split (rather than one crop) hedges the shared-market hit: measured
#: on seed 0, doubling our crop volume through a second hand crashed both players'
#: revenue by ~$12k. 16 tiles is the study's "~16 extra CROP tiles".
DEFAULT_SEED_MIX = {"CARROT": 8, "WHEAT": 8}

#: Engine movement verbs are the inverse of what routing needs.
_STEP_VERB = {delta: verb for verb, delta in FARMER_MOVES.items()}

#: Last day of the season (episodeSteps 720 / 24). A crop planted so late that it
#: cannot reach harvest day before this is a pure loss of seeds and hand turns:
#: 17 of the 38 plants of the first measured draft never matured.
SEASON_LAST_DAY = 29

# Job priorities: lower is done first. Harvest and an urgent water both lose
# value tonight; a weed permanently blocks a tile; a bonus water can wait a day;
# planting is queued ahead of carrying because the shed also gets the load
# automatically at midnight.
_PRIORITY_HARVEST = 0
_PRIORITY_URGENT_WATER = 0
_PRIORITY_DROP = 0
_PRIORITY_DIG = 2
_PRIORITY_WATER = 3
_PRIORITY_PLANT = 4
_PRIORITY_CARRY = 5
_PRIORITY_CARRY_LATE = 0  # last days: a hand's load is lost at the final step


def _as_int(value, default=0):
    """Non-raising int coercion: a malformed field degrades, never raises."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_float(value, default=0.0):
    """Non-raising float coercion for cash fields."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


# --------------------------------------------------------------------------- #
# Pure geometry / crop helpers.
# --------------------------------------------------------------------------- #

def quadrant_of(x, y, board=BOARD):
    """Engine quadrant name for a tile."""
    half = board // 2
    return ("N" if y < half else "S") + ("W" if x < half else "E")


def shed_access_tiles(board=BOARD):
    """The four inner-corner tiles around the shed, in NWSE order."""
    half = board // 2
    return [(half - 1, half - 1), (half, half - 1), (half - 1, half), (half, half)]


def designated_tiles(count, board=BOARD, quadrant=SE_QUADRANT):
    """The first ``count`` workable tiles of ``quadrant``, closest to the shed.

    Deterministic (distance, then row, then column) so a tile keeps its identity
    across turns and the trace can name exactly which cells we worked. The shed
    access tiles are excluded: the shed is used *through* them.
    """
    if count <= 0:
        return []
    shed = set(shed_access_tiles(board))
    candidates = []
    for y in range(board):
        for x in range(board):
            if quadrant_of(x, y, board) != quadrant or (x, y) in shed:
                continue
            distance = min(abs(x - sx) + abs(y - sy) for sx, sy in shed)
            candidates.append((distance, y, x, (x, y)))
    candidates.sort()
    return [entry[3] for entry in candidates[:count]]


def crop_sequence(mix, count):
    """Round-robin the mix so each crop is spread across the tile block.

    Deterministic and prefix stable: the nearest tile gets the first crop of the
    mix, the next gets the second, and so on. This keeps the CARROT share from
    being crowded into the far corner of the quadrant.
    """
    crops = [crop for crop, n in mix.items() if int(n) > 0]
    if not crops or count <= 0:
        return []
    buckets = {crop: int(n) for crop, n in mix.items()}
    sequence = []
    while any(value > 0 for value in buckets.values()):
        for crop in crops:
            if buckets[crop] > 0:
                sequence.append(crop)
                buckets[crop] -= 1
    while len(sequence) < count:  # more tiles than mix units: keep cycling
        sequence.extend(crops)
    return sequence[:count]


def plan_tiles(mix, count):
    """``[(tile, crop), ...]``: the tiles we will work and with what."""
    return list(zip(designated_tiles(count), crop_sequence(mix, count), strict=False))


def seed_cost(mix):
    """Cash needed to buy one planting round of ``mix``."""
    return sum(CROPS[crop]["seed"] * int(n) for crop, n in mix.items() if crop in CROPS)


def harvest_maturity_day(crop):
    """Days from planting to the first harvest worth taking.

    One-shot crops are harvested at ``max_yield_day`` (the last bonus-watering
    day); ongoing crops pay from ``first_yield_day`` on and are harvested as soon
    as they hold a unit.
    """
    data = CROPS[crop]
    return data["first_yield_day"] if data["ongoing"] else data["max_yield_day"]


def water_window(crop):
    """The engine growth-bonus window ``[(max_yield_day+1)//2, max_yield_day]``."""
    data = CROPS[crop]
    return (data["max_yield_day"] + 1) // 2, data["max_yield_day"]


def needs_water(tile, day, crop):
    """True when this plant must be watered today.

    WATER does two jobs: it pays the growth bonus inside the window and it
    resets the weed clock. ``_new_plant`` starts ``consecutive_unwatered`` at 1
    and ``_daily_refresh_plants`` weeds a plant after two unwatered midnights,
    so the planting day counts as urgent even though it is outside the window.
    """
    if not isinstance(tile, dict) or tile.get("kind") != "PLANT":
        return False
    if tile.get("crop") != crop or tile.get("watered_today", False):
        return False
    age = day - _as_int(tile.get("planted_day", day), day)
    start, end = water_window(crop)
    return start <= age <= end or _as_int(tile.get("consecutive_unwatered", 0)) >= 1


def water_is_urgent(tile):
    """True when skipping WATER tonight turns the plant into a weed."""
    return _as_int(tile.get("consecutive_unwatered", 0)) >= 1


def water_boosts_harvest(tile, day, crop=None):
    """True when one more WATER still adds a unit that HARVEST would collect."""
    if not isinstance(tile, dict) or tile.get("kind") != "PLANT":
        return False
    here: object = tile.get("crop")
    if not isinstance(here, str) or here not in CROPS:
        return False
    data = CROPS[here]
    if data["ongoing"] or tile.get("watered_today", False):
        return False
    if _as_int(tile.get("yield_units", 0)) >= data["max_yield"]:
        return False
    age = day - _as_int(tile.get("planted_day", day), day)
    start, end = water_window(here)
    return start <= age <= end


def harvest_due(tile, day, crop):
    """True when a plant should be harvested now.

    HARVEST is only legal at/after ``first_yield_day``. One-shot crops pay their
    bonus inside the watering window and start decaying at
    ``(planted_day + max_yield_day + 1)`` days, so harvest at the maturity day or
    at max yield, whichever comes first. Ongoing crops are harvested whenever
    they hold a unit.
    """
    if not isinstance(tile, dict) or tile.get("kind") != "PLANT":
        return False
    if tile.get("crop") != crop or _as_int(tile.get("yield_units", 0)) <= 0:
        return False
    data = CROPS.get(crop)
    if data is None:
        return False
    age = day - _as_int(tile.get("planted_day", day), day)
    if age < data["first_yield_day"]:
        return False
    if data["ongoing"]:
        return True
    return (_as_int(tile.get("yield_units", 0)) >= data["max_yield"]
            or age >= data["max_yield_day"])


def step_toward(pos, target):
    """The single movement verb shortening the Manhattan distance, or None."""
    if target[0] > pos[0]:
        return _STEP_VERB[(1, 0)]
    if target[0] < pos[0]:
        return _STEP_VERB[(-1, 0)]
    if target[1] > pos[1]:
        return _STEP_VERB[(0, 1)]
    if target[1] < pos[1]:
        return _STEP_VERB[(0, -1)]
    return None


def fib_hire_cost(n_already_today, mult=1):
    """The engine's n-th hire of the day: ``mult * fib(n)``, fib = 1, 1, 2, 3..."""
    a, b = 1, 1
    for _ in range(max(0, _as_int(n_already_today))):
        a, b = b, a + b
    return mult * a


# --------------------------------------------------------------------------- #
# Observation helpers - every one degrades instead of raising.
# --------------------------------------------------------------------------- #

def _farm_of(obs):
    try:
        farms = obs.get("farms") or []
        player = _as_int(obs.get("player", 0))
        farm = farms[player] if 0 <= player < len(farms) else {}
        return farm if isinstance(farm, dict) else {}
    except (AttributeError, TypeError, ValueError, IndexError):
        return {}


def _private_of(obs):
    try:
        private = obs.get("private") or {}
        return private if isinstance(private, dict) else {}
    except (AttributeError, TypeError):
        return {}


def _fallback_action(obs):
    try:
        hands = _farm_of(obs).get("hands") or []
    except Exception:
        hands = []
    return {"farmer": ["PASS"], "hands": [["PASS"] for _ in hands], "market": []}


def _position(value):
    if isinstance(value, (list, tuple)) and len(value) == 2:
        return (_as_int(value[0]), _as_int(value[1]))
    return None


# --------------------------------------------------------------------------- #
# The overlay.
# --------------------------------------------------------------------------- #

class SELoopOverlay:
    """Wrap a tape agent and add a bounded, SE-only closed loop.

    The class is the testable seam: it owns the per-day bookkeeping (which hand
    indices are ours, how many hires are in flight) plus the SE job board, and it
    accepts an injected ``base_agent`` so tests can drive it with synthetic
    observations instead of a season.
    """

    def __init__(
        self,
        buy_day=10,
        land_reserve=None,
        seed_mix=None,
        extra_hands_cap=1,
        hire_from_hour=5,
        hire_until_hour=20,
        plant_until_hour=22,
        seed_margin=2,
        harvest=True,
        carry_from_day=28,
        cash_floor=500,
        last_harvest_day=SEASON_LAST_DAY,
        se_tiles=None,
        enabled=True,
        shed_capacity=SHED_CAPACITY,
        base_agent=None,
        trace=None,
    ):
        if not isinstance(buy_day, int) or isinstance(buy_day, bool) or buy_day < 0:
            raise ValueError("buy_day must be a non-negative int")
        if land_reserve is not None and land_reserve < 0:
            raise ValueError("land_reserve must be non-negative or None")
        if not isinstance(extra_hands_cap, int) or extra_hands_cap < 0:
            raise ValueError("extra_hands_cap must be a non-negative int")
        if not 0 <= hire_from_hour < TURNS_PER_DAY or not 0 <= hire_until_hour < TURNS_PER_DAY:
            raise ValueError("hire hours must be in [0, 24)")
        if hire_until_hour < hire_from_hour:
            raise ValueError("hire_until_hour must be >= hire_from_hour")
        if not 0 <= plant_until_hour < TURNS_PER_DAY:
            raise ValueError("plant_until_hour must be in [0, 24)")
        if seed_margin < 0:
            raise ValueError("seed_margin must be non-negative")
        if cash_floor < 0:
            raise ValueError("cash_floor must be non-negative")
        if se_tiles is not None and (not isinstance(se_tiles, int) or se_tiles < 0):
            raise ValueError("se_tiles must be a non-negative int or None")
        mix = dict(DEFAULT_SEED_MIX if seed_mix is None else seed_mix)
        if not mix:
            raise ValueError("seed_mix must name at least one crop")
        for crop, count in mix.items():
            if crop not in CROPS:
                raise ValueError(f"unknown crop {crop!r}; expected one of {sorted(CROPS)}")
            if not isinstance(count, int) or isinstance(count, bool) or count < 0:
                raise ValueError(f"seed_mix[{crop!r}] must be a non-negative int")
        mix = {crop: int(count) for crop, count in mix.items() if int(count) > 0}
        if not mix:
            raise ValueError("seed_mix must request at least one seed")

        self.buy_day = buy_day
        self.mix = mix
        self.seed_cost = seed_cost(mix)
        self.land_reserve = self.seed_cost if land_reserve is None else land_reserve
        self.extra_hands_cap = extra_hands_cap
        self.hire_from_hour = hire_from_hour
        self.hire_until_hour = hire_until_hour
        self.plant_until_hour = plant_until_hour
        self.seed_margin = seed_margin
        self.harvest = bool(harvest)
        self.carry_from_day = carry_from_day
        self.last_harvest_day = last_harvest_day
        self.cash_floor = cash_floor
        self.enabled = bool(enabled)
        self.shed_capacity = shed_capacity
        self.trace = trace if trace is not None else []
        self.crashes = 0

        tile_count = sum(mix.values()) if se_tiles is None else se_tiles
        self.plan = plan_tiles(mix, tile_count)
        self.plan_map = dict(self.plan)
        self.plan_tiles = [tile for tile, _ in self.plan]

        # A private copy of the tape: it keeps season state in a module-level
        # singleton, so sharing one import would let two agents corrupt each other.
        self.base = base_agent if base_agent is not None else _tape_agent
        self._reset_day()

    # ---- bookkeeping -------------------------------------------------------

    def _reset_day(self):
        """The engine clears the roster and the hire counter at midnight."""
        self.owned = set()      # hand indices we own in the current day
        self.pending = 0        # HIREs appended last turn, resolved this turn
        self.hired_today = False
        self.prev_roster = 0
        self.prev_hires = 0

    def _note(self, *event):
        if self.trace is not None:
            self.trace.append(event)

    def _resolve_pending(self, farm):
        """Claim the hand(s) hired last turn, but only when the arithmetic proves it.

        HIRE resolves in the market phase, after the tape's own orders, so on the
        next observation our new hands are the LAST entries of the roster and both
        the roster and ``hires_today`` grew by exactly the number we bought. If
        either delta disagrees (a hire did not land, or the tape hired in the same
        hour), nothing is claimed: an unowned extra hand is idle, which is safe,
        while a wrongly claimed index would hijack a hand the tape's route drives.
        """
        if not self.pending:
            return
        roster = len(farm.get("hands") or [])
        hires = _as_int(farm.get("hires_today", 0))
        grew_roster = roster - self.prev_roster
        grew_hires = hires - self.prev_hires
        if grew_roster == self.pending and grew_hires == self.pending:
            start = roster - self.pending
            self.owned |= set(range(start, roster))
            for index in range(start, roster):
                self._note("own_hand", index)
        else:
            self.hired_today = False  # the hire did not land: allow a retry
            self._note("hire_missed", grew_roster, grew_hires, self.pending)
        self.pending = 0

    # ---- tape accounting ---------------------------------------------------

    def _tape_spend(self, tape_market, farm, prices, quads):
        """Conservative estimate of what the tape's own orders spend this turn.

        The tape's orders resolve before ours in the market lockstep, so this is
        the cash that is gone by the time an appended BUY_LAND commits. It is an
        upper bound (prices are quoted at the start of the turn), which is the
        safe direction for a gate.
        """
        spend = 0.0
        hires = _as_int(farm.get("hires_today", 0))
        seen_hires = 0
        for order in tape_market:
            if not isinstance(order, (list, tuple)) or not order:
                continue
            op = order[0]
            if op == "HIRE":
                spend += fib_hire_cost(hires + seen_hires)
                seen_hires += 1
            elif op == "BUY_LAND":
                slot = min(max(0, len(quads) - 1), len(LAND_PRICES) - 1)
                spend += LAND_PRICES[slot]
            elif op == "BUY_SEED" and len(order) >= 3 and order[1] in CROPS:
                spend += CROPS[order[1]]["seed"] * max(0, _as_int(order[2]))
            elif op == "BUY_ANIMAL" and len(order) >= 3 and order[1] in ANIMALS:
                spend += ANIMALS[order[1]]["cost"] * max(0, _as_int(order[2]))
            elif op == "BUY_PRODUCT" and len(order) >= 3:
                spend += max(0, _as_int(order[2])) * _as_float(prices.get(order[1], 0))
        return spend

    def _has_order(self, tape_market, op):
        return any(isinstance(o, (list, tuple)) and o and o[0] == op for o in tape_market)

    def _tape_plant_demand(self, action, crop):
        """PLANT demand for ``crop`` in the tape slots we do NOT replace."""
        units = [action.get("farmer")] + list(action.get("hands") or [])
        demand = 0
        for index, unit in enumerate(units):
            if index >= 1 and (index - 1) in self.owned:
                continue
            if (isinstance(unit, (list, tuple)) and len(unit) >= 2
                    and unit[0] == "PLANT" and unit[1] == crop):
                demand += 1
        return demand

    def _spare_seeds(self, action, seeds, crop):
        """Seeds we may plant without ever tripping the tape's atomic void."""
        held = _as_int(seeds.get(crop, 0))
        return max(0, held - self._tape_plant_demand(action, crop) - self.seed_margin)

    # ---- the market side: land, seeds, hands -------------------------------

    def _wanted_seeds(self, tiles):
        """Per crop: how many seeds the plan still needs for its empty tiles."""
        want = {}
        for crop in self.mix:
            want[crop] = 0
        for tile, crop in self.plan:
            try:
                cell = tiles[tile[1]][tile[0]]
            except (IndexError, TypeError):
                continue
            # None = free, "LOCKED" = the land buy in this same turn, WEED = to dig.
            if cell is None or cell == "LOCKED" or (isinstance(cell, dict) and cell.get("kind") == "WEED"):
                want[crop] = want.get(crop, 0) + 1
        return {crop: min(self.mix.get(crop, 0), n) for crop, n in want.items()}

    def _append_seed_orders(self, market, tiles, seeds, cash):
        """One quantity order per crop, never a per-unit loop."""
        spent = 0.0
        for crop, want in self._wanted_seeds(tiles).items():
            short = want - _as_int(seeds.get(crop, 0))
            if short <= 0 or len(market) >= MAX_ORDERS:
                continue
            price = CROPS[crop]["seed"] * short
            if cash - spent - price < self.cash_floor:
                affordable = short
                while affordable > 0 and cash - spent - CROPS[crop]["seed"] * affordable < self.cash_floor:
                    affordable -= 1
                short = affordable
                if short <= 0:
                    continue
                price = CROPS[crop]["seed"] * short
            market.append(["BUY_SEED", crop, short])
            spent += price
            self._note("seed", crop, short, price)
        return spent

    def _append_hires(self, market, farm, hour, cash):
        """Append our daily hands, at most once a day, after the tape's own burst."""
        if (not self.extra_hands_cap or self.hired_today
                or not (self.hire_from_hour <= hour <= self.hire_until_hour)):
            return
        want = self.extra_hands_cap - (len(self.owned) + self.pending)
        room = MAX_ORDERS - len(market)
        if want <= 0 or room <= 0:
            return
        hires_today = _as_int(farm.get("hires_today", 0))
        count = 0
        wallet = cash
        for slot in range(min(want, room)):
            cost = fib_hire_cost(hires_today + count)
            if wallet - cost < self.cash_floor:
                break
            wallet -= cost
            self._note("hire", cost, slot)
            count += 1
        if not count:
            return
        for _ in range(count):
            market.append(["HIRE"])
        self.pending += count
        self.hired_today = True

    # ---- the unit side: the SE job board ----------------------------------

    def _tile_job(self, cell, crop, day, budget, hour):
        """The job this planned tile offers, as ``(kind, priority, crop)`` or None."""
        if cell is None:
            matures_in_time = day + harvest_maturity_day(crop) <= self.last_harvest_day
            if budget > 0 and hour <= self.plant_until_hour and matures_in_time:
                return ("PLANT", _PRIORITY_PLANT, crop)
            return None
        if not isinstance(cell, dict):
            return None  # LOCKED, or something we must not touch
        kind = cell.get("kind")
        if kind == "WEED":
            return ("DIG", _PRIORITY_DIG, crop)
        if kind != "PLANT":
            return None  # COOP / PASTURE / animal: never ours
        here = cell.get("crop")
        if here not in CROPS:
            return None
        boost = water_boosts_harvest(cell, day)
        if self.harvest and harvest_due(cell, day, here) and not boost:
            return ("HARVEST", _PRIORITY_HARVEST, crop)
        if needs_water(cell, day, here):
            urgent = water_is_urgent(cell) or boost
            return ("WATER", _PRIORITY_URGENT_WATER if urgent else _PRIORITY_WATER, crop)
        return None

    def _choose(self, pos, tiles, day, hour, budget, claimed, carried, room):
        """Best unclaimed job for one hand: the action and the tile it commits to."""
        if pos is None:
            return ["PASS"], None
        best = None
        for (x, y), crop in self.plan:
            if (x, y) in claimed:
                continue
            try:
                cell = tiles[y][x]
            except (IndexError, TypeError):
                continue
            job = self._tile_job(cell, crop, day, budget.get(crop, 0), hour)
            if job is None:
                continue
            kind, priority, job_crop = job
            distance = abs(pos[0] - x) + abs(pos[1] - y)
            key = (priority, distance, x, y)
            if best is None or key < best[0]:
                best = (key, (x, y), kind, job_crop)
        # Carrying: a load in hand is dropped for free at midnight, so walking to
        # the shed only outranks farm work on the last days, when the final step
        # arrives before the midnight drop and the load would be lost.
        if carried > 0:
            shed = shed_access_tiles()
            target = min(shed, key=lambda t: (abs(pos[0] - t[0]) + abs(pos[1] - t[1]), t[1], t[0]))
            distance = abs(pos[0] - target[0]) + abs(pos[1] - target[1])
            if distance == 0 and room >= carried:
                key = (_PRIORITY_DROP, 0, target[0], target[1])
                if best is None or key < best[0]:
                    best = (key, target, "DROP", None)
            else:
                priority = (_PRIORITY_CARRY_LATE if day >= self.carry_from_day
                            else _PRIORITY_CARRY)
                key = (priority, distance, target[0], target[1])
                if best is None or key < best[0]:
                    best = (key, target, "WALK", None)
        if best is None:
            return ["PASS"], None
        _, target, kind, job_crop = best
        if tuple(pos) == target:
            if kind == "PLANT":
                # PLANT without its crop is a silent no-op: never emit ["PLANT"].
                return ["PLANT", job_crop], target
            return [kind], target
        # Not standing on it yet: every job, planting included, is reached by
        # walking. Emitting PLANT from a distance is a silent no-op.
        verb = step_toward(tuple(pos), target)
        return [verb or "PASS"], target

    def _drive_hands(self, obs, action, farm, seeds, day, hour):
        """Return the hand list with ONLY our own indices rewritten."""
        tape_hands = action.get("hands") or []
        hands_out = list(tape_hands)  # entries kept by identity unless we own them
        if not self.owned:
            return hands_out
        roster = list(farm.get("hands") or [])
        while len(hands_out) < max(self.owned) + 1:
            hands_out.append(["PASS"])
        tiles = farm.get("tiles") or []
        private = _private_of(obs)
        inventories = private.get("inventories") or []
        shed = private.get("shed") or {}
        room = max(0, self.shed_capacity - sum(max(0, _as_int(v)) for v in shed.values()))
        budget = {crop: self._spare_seeds(action, seeds, crop) for crop in self.mix}
        claimed = set()
        for index in sorted(self.owned):
            pos = _position(roster[index]) if index < len(roster) else None
            carried = 0
            inv = inventories[index + 1] if index + 1 < len(inventories) else {}
            if isinstance(inv, dict):
                carried = sum(max(0, _as_int(n)) for n in inv.values())
            hand_action, target = self._choose(
                pos, tiles, day, hour, budget, claimed, carried, room)
            hands_out[index] = hand_action
            verb = hand_action[0] if hand_action else None
            if verb in ("PLANT", "WATER", "HARVEST", "DIG") and target is not None:
                claimed.add(target)
            if verb == "PLANT" and len(hand_action) >= 2:
                planted = hand_action[1]
                budget[planted] = budget.get(planted, 0) - 1
                self._note("plant", target, planted, day)
            elif verb == "WATER":
                self._note("water", target, day)
            elif verb == "HARVEST":
                self._note("harvest", target, day)
            elif verb == "DIG":
                self._note("dig", target, day)
            elif verb == "DROP":
                self._note("drop", target, carried, day)
            self._note("act", index, tuple(hand_action), target)
        return hands_out

    # ---- the transform -----------------------------------------------------

    def augment(self, obs, action):
        """The tape-preserving transform. Never mutates the tape's action."""
        step = current_step(obs)
        farm = _farm_of(obs)
        if step % TURNS_PER_DAY == 0:
            self._reset_day()
        self._resolve_pending(farm)

        day = _as_int(obs.get("day"), step // TURNS_PER_DAY)
        hour = _as_int(obs.get("hour"), step % TURNS_PER_DAY)
        money = _as_float(farm.get("money", 0))
        quads = list(farm.get("unlocked_quadrants") or [])
        seeds = dict(_private_of(obs).get("seeds") or {})
        tiles = farm.get("tiles") or []
        prices = ((obs.get("market") or {}).get("prices") or {})
        tape_market = action.get("market") or []
        market = list(tape_market)  # appended-to copy; the tape's orders are kept

        spend = self._tape_spend(tape_market, farm, prices, quads)
        cash = money - spend

        # 1. The gate: buy SE once the tape's own land purchases are done, from
        #    buy_day on, with the price plus the seed reserve in hand NET of the
        #    tape's same-turn spending (its orders resolve before ours).
        bought_land = False
        if (len(quads) == 3 and day >= self.buy_day and len(market) < MAX_ORDERS
                and cash >= SE_PRICE + self.land_reserve):
            market.append(["BUY_LAND"])
            bought_land = True
            self._note("land", day, step, money, spend)

        have_se = len(quads) == 4 or bought_land
        if have_se:
            # 2. Seeds for the planned first plantings, one order per crop.
            cash -= self._append_seed_orders(market, tiles, seeds, cash)
            # 3. Our extra hands, after the tape's own daily hiring burst.
            if not self._has_order(tape_market, "HIRE"):
                self._append_hires(market, farm, hour, cash)

        hands_out = self._drive_hands(obs, action, farm, seeds, day, hour)

        self.prev_roster = len(farm.get("hands") or [])
        self.prev_hires = _as_int(farm.get("hires_today", 0))
        return {
            "farmer": action.get("farmer"),
            "hands": hands_out,
            "market": market[:MAX_ORDERS],
        }

    def __call__(self, obs):
        try:
            action = self.base(obs)
        except Exception:
            action = _fallback_action(obs)
        if not self.enabled or not isinstance(action, dict):
            return action
        try:
            return self.augment(obs, action)
        except Exception:
            # A malformed observation must never take the agent out of the season.
            # Returning the untouched tape action is always legal; the trace entry
            # is here so a silent crash cannot masquerade as a clean pass-through.
            self.crashes += 1
            self._note("crash")
            return action


def make_agent(
    buy_day=10,
    land_reserve=None,
    seed_mix=None,
    extra_hands_cap=1,
    hire_from_hour=5,
    hire_until_hour=20,
    plant_until_hour=22,
    seed_margin=2,
    harvest=True,
    carry_from_day=28,
    cash_floor=500,
    last_harvest_day=SEASON_LAST_DAY,
    se_tiles=None,
    enabled=True,
    base_agent=None,
    trace=None,
):
    """Build an ``agent(obs)`` adding the SE closed loop to the V8.1 tape.

    ``enabled=False`` is a full pass-through (the tape's action, untouched), which
    is the byte-identity switch the task requires. The returned callable is a
    one-argument function on purpose: the Kaggle runner truncates the argument
    list with ``__code__.co_argcount``, so a callable *instance* would be handed
    ``(observation, configuration)`` and die on every turn.

    Pass a list as ``trace=`` to collect the structural evidence the tests assert
    on: ``land`` / ``seed`` / ``hire`` / ``own_hand`` / ``plant`` / ``water`` /
    ``harvest`` / ``drop`` / ``crash`` events.
    """
    overlay = SELoopOverlay(
        buy_day=buy_day,
        land_reserve=land_reserve,
        seed_mix=seed_mix,
        extra_hands_cap=extra_hands_cap,
        hire_from_hour=hire_from_hour,
        hire_until_hour=hire_until_hour,
        plant_until_hour=plant_until_hour,
        seed_margin=seed_margin,
        harvest=harvest,
        carry_from_day=carry_from_day,
        cash_floor=cash_floor,
        last_harvest_day=last_harvest_day,
        se_tiles=se_tiles,
        enabled=enabled,
        base_agent=base_agent,
        trace=trace,
    )
    return _agent_for(overlay)


def _agent_for(overlay):
    """A one-argument closure: see the note in :func:`make_agent`."""

    def agent(obs):
        return overlay(obs)

    return agent


#: Default configuration of the S1 experiment: the doc's day-10 gate, the cheap
#: fast 16-tile mix, one appended hand per day, full choreography.
agent = make_agent()
