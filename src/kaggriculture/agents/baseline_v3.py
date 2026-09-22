"""Kaggriculture baseline_v3: harvest-priority, DIG weeds, per-crop plant_until.

C1: ripe one-time crops (age == max_day) outrank watering — fixes rot loss.
C2: DIG weed maintenance recovers tiles lost to rot/lifespan.
C3: per-crop plant_until — wheat ≤25, carrot ≤26, tomato ≤21 (was flat 25).

C2/C3/C4 hiring, multi-unit routing, loaded-unit logistics, reactive sales
stay compact and deterministic. C5 owns the paired controls and ladder evaluation.

The module is self-contained and import-free so it can be submitted directly
as a Kaggriculture agent.
"""

# --- Engine constants mirrored from kaggle_environments ---

BOARD = 10
HALF = BOARD // 2
LIQUIDATE_FROM_DAY = 28
SELL_CHUNK = 40
SEED_STOCK = 8
SEED_BATCH = 4
TARGET_HANDS = 4
FIELD_CAP = 20

# Per-crop planting cutoffs (harvest must land ≤ day 29)
PLANT_UNTIL_DAY = {
    "WHEAT": 25,
    "CARROT": 26,
    "TOMATO": 21,
}

CROP_ORDER = ("WHEAT", "CARROT", "TOMATO")
SELL_ORDER = ("TOMATO", "CARROT", "WHEAT")
CROP_SHARES = {"WHEAT": 0.50, "CARROT": 0.30, "TOMATO": 0.20}
# Keep integer targets explicit: 10/6/4 is the 50/30/20 policy at FIELD_CAP=20.
CROP_TARGETS = {"WHEAT": 10, "CARROT": 6, "TOMATO": 4}
MARKET_BASE = {"WHEAT": 25, "CARROT": 35, "TOMATO": 60}
SHED_PRESSURE = 70
LOW_PRICE_RATIO = 0.90
CROPS = {
    "WHEAT": {"seed": 10, "first": 2, "max_day": 4, "interval": 0, "ongoing": False},
    "CARROT": {"seed": 20, "first": 2, "max_day": 3, "interval": 0, "ongoing": False},
    "TOMATO": {"seed": 50, "first": 8, "max_day": 8, "interval": 1, "ongoing": True},
}

MOVE_OP = {(0, -1): "NORTH", (0, 1): "SOUTH", (1, 0): "EAST", (-1, 0): "WEST"}
SHED_TILE = (HALF - 1, HALF - 1)  # (4, 4)


def _dist(a, b):
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def _step_toward(pos, target):
    """Take one deterministic Manhattan step, horizontal before vertical."""
    px, py = pos
    tx, ty = target
    if px != tx:
        return [MOVE_OP[(1, 0)] if tx > px else MOVE_OP[(-1, 0)]]
    if py != ty:
        return [MOVE_OP[(0, 1)] if ty > py else MOVE_OP[(0, -1)]]
    return ["PASS"]


def _open_tiles(tiles):
    """Return unlocked empty tiles nearest the shed first."""
    out = []
    for y in range(BOARD):
        for x in range(BOARD):
            if tiles[y][x] is None:
                out.append((x, y))
    out.sort(key=lambda p: (_dist(p, SHED_TILE), p[1], p[0]))
    return out


def _plants(tiles):
    """Return all plant tiles as ``(position, tile)`` pairs."""
    out = []
    for y in range(BOARD):
        for x in range(BOARD):
            tile = tiles[y][x]
            if isinstance(tile, dict) and tile.get("kind") == "PLANT":
                out.append(((x, y), tile))
    return out


def _plant_counts(tiles):
    """Count the policy crops currently occupying field tiles."""
    counts = dict.fromkeys(CROP_ORDER, 0)
    for _, tile in _plants(tiles):
        crop = tile.get("crop")
        if crop in counts:
            counts[crop] += 1
    return counts


def _ready_to_harvest(tile, day):
    """Return whether a crop has a legal, worthwhile harvest this turn."""
    if not isinstance(tile, dict):
        return False
    info = CROPS.get(tile.get("crop", "WHEAT"))
    if info is None or tile.get("yield_units", 0) <= 0:
        return False
    age = day - tile.get("planted_day", day)
    if age < info["first"]:
        return False
    if info["ongoing"]:
        # Tomato keeps its tile after each harvest; the engine's interval of
        # one day controls production, while a positive yield controls harvest.
        return True
    # One-time crops wait through the full watering bonus window.
    return age >= info["max_day"]


def _collect_jobs(obs, me, priv):
    """Collect one deterministic crop/logistics job per useful tile.

    C1 fix: ripe one-time crops (age == max_day) outrank watering to prevent rot.
    C2 fix: add DIG jobs for WEED tiles (low priority after field work).
    """
    day = obs["day"]
    tiles = me["tiles"]
    plants = _plants(tiles)

    water, harvest, dig = [], [], []

    for pos, tile in plants:
        ready = _ready_to_harvest(tile, day)
        age = day - tile.get("planted_day", day)
        crop = tile.get("crop", "WHEAT")
        info = CROPS.get(crop, CROPS["WHEAT"])
        at_max = not info["ongoing"] and age == info["max_day"]

        # C1: ripe one-time crops at max_day outrank watering (prevents rot).
        if at_max:
            harvest.append((pos, ["HARVEST"]))
        elif not tile.get("watered_today", False):
            water.append((pos, ["WATER"]))
        elif ready:
            harvest.append((pos, ["HARVEST"]))

    # C2: add DIG jobs for WEED tiles (low priority, after field work).
    for y in range(BOARD):
        for x in range(BOARD):
            tile = tiles[y][x]
            if isinstance(tile, dict) and tile.get("kind") == "WEED":
                dig.append(((x, y), ["DIG"]))

    # Reserve each selected empty tile once while filling the three share
    # targets. Existing seed inventory is consumed by these plant jobs.
    plant = []
    counts = _plant_counts(me["tiles"])
    open_tiles = _open_tiles(me["tiles"])
    reserved = set()
    for crop in CROP_ORDER:
        target = CROP_TARGETS[crop]
        need = max(0, target - counts[crop])
        seeds = max(0, priv.get("seeds", {}).get(crop, 0) or 0)
        room = max(0, FIELD_CAP - len(_plants(me["tiles"])) - len(plant))
        # C3: per-crop planting cutoff
        if day <= PLANT_UNTIL_DAY.get(crop, 25):
            quantity = min(need, seeds, room)
            if quantity <= 0:
                continue
            placed = 0
            for pos in open_tiles:
                if pos in reserved:
                    continue
                reserved.add(pos)
                plant.append((pos, ["PLANT", crop]))
                placed += 1
                if placed >= quantity:
                    break

    # C1: ripe one-time at max_day → HARVEST (highest priority),
    # then WATER, then harvest for ongoing/other ready, then PLANT, then DIG.
    return harvest + water + plant + dig


def _assign_jobs(me, priv, jobs):
    """Assign jobs to nearest free units and return one action per unit."""
    positions = [tuple(me["farmer"])] + [tuple(p) for p in me["hands"]]
    inventories = priv.get("inventories", [])
    actions = [["PASS"] for _ in positions]
    busy = [False] * len(positions)

    # Carrying any policy crop is always higher priority than field work.
    for index, pos in enumerate(positions):
        inventory = inventories[index] if index < len(inventories) else {}
        if not any(inventory.get(crop, 0) > 0 for crop in CROP_ORDER):
            continue
        busy[index] = True
        actions[index] = ["DROP"] if pos == SHED_TILE else _step_toward(pos, SHED_TILE)

    # Each job is consumed once, and each unit receives at most one action.
    for target, operation in jobs:
        candidates = [i for i, used in enumerate(busy) if not used]
        if not candidates:
            break
        index = min(candidates, key=lambda i: (_dist(positions[i], target), i))
        busy[index] = True
        actions[index] = operation if positions[index] == target else _step_toward(positions[index], target)

    return actions


def _multi_unit_scheduler(obs, me, priv):
    """Schedule all visible units for the C4 rotation and logistics."""
    return _assign_jobs(me, priv, _collect_jobs(obs, me, priv))


def _hire_cost(n_already_today):
    """Return the engine's Fibonacci-priced hire cost without imports."""
    previous, current = 1, 1
    for _ in range(max(0, n_already_today)):
        previous, current = current, previous + current
    return previous


def _sale_quantity(crop, day, shed, prices):
    """React to shared-market pressure instead of always waiting for day 28.

    A price below the resource's base price is evidence that opponents are
    liquidating the same product. Sell a bounded batch while the signal is
    visible; otherwise ride a healthy price until the normal liquidation gate.
    Shed pressure remains a safety override. This is market-reactive, not an
    opponent fingerprint; detector-gated switching remains post-final work.
    """
    have = shed.get(crop, 0)
    if have <= 0:
        return 0
    if day >= LIQUIDATE_FROM_DAY:
        return have
    held = sum(shed.get(item, 0) for item in CROP_ORDER)
    base = MARKET_BASE[crop]
    price = prices.get(crop, base)
    glut_signal = price < base * LOW_PRICE_RATIO
    if held >= SHED_PRESSURE or glut_signal:
        return min(have, SELL_CHUNK)
    return 0


def _market_orders(obs, me, priv):
    """Order reactive crop sales, C2 hires, then conservative seed fills."""
    day = obs["day"]
    orders = []
    shed = priv.get("shed", {})
    prices = obs.get("market", {}).get("prices", {})

    # Sales lead the queue so their proceeds can fund later market orders. The
    # quantity/timing is driven by shared prices and shed pressure, not a fixed
    # endgame-only rule.
    for crop in SELL_ORDER:
        quantity = _sale_quantity(crop, day, shed, prices)
        if quantity > 0:
            orders.append(["SELL", crop, quantity])

    # Preserve C2's four-hire opening. Reserve the planned Fibonacci hire cost
    # before considering seed purchases, even though sales are earlier in queue.
    planned_hires = 0
    already = max(0, me.get("hires_today", 0) or 0)
    if obs.get("hour", 0) <= 2:
        missing = max(0, TARGET_HANDS - already)
        planned_hires = min(missing, TARGET_HANDS)
        for _ in range(planned_hires):
            orders.append(["HIRE"])
    hire_cost = sum(_hire_cost(already + offset) for offset in range(planned_hires))
    cash = me.get("money", 0) - hire_cost
    for order in orders:
        if order[0] == "SELL":
            quote = prices.get(order[1], MARKET_BASE[order[1]])
            cash += 0.8 * quote * order[2]

    # Buy only the unheld seeds needed to reach each current share target.
    if day <= max(PLANT_UNTIL_DAY.values()):
        counts = _plant_counts(me["tiles"])
        seeds_by_crop = priv.get("seeds", {})
        for crop in CROP_ORDER:
            target = CROP_TARGETS[crop]
            seeds = max(0, seeds_by_crop.get(crop, 0) or 0)
            gap = max(0, target - counts[crop] - seeds)
            gap = min(gap, SEED_BATCH, max(0, SEED_STOCK - seeds))
            price = CROPS[crop]["seed"]
            if gap > 0 and cash >= price * gap:
                orders.append(["BUY_SEED", crop, gap])
                cash -= price * gap

    return orders[:10]


def agent(obs):
    """Kaggriculture entry point."""
    me = obs["farms"][obs["player"]]
    priv = obs["private"]
    actions = _multi_unit_scheduler(obs, me, priv)
    return {
        "farmer": actions[0],
        "hands": actions[1:],
        "market": _market_orders(obs, me, priv),
    }