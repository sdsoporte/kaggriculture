"""Kaggriculture baseline_v2, C3: four-hand wheat scheduling.

This slice preserves C2's market ordering while giving the farmer and every
visible hand independent wheat field and drop jobs. C4 will add crop-aware
readiness and the three-crop policy.

The module is self-contained and import-free so it can be submitted directly
as a Kaggriculture agent.
"""

# --- Engine constants mirrored from kaggle_environments ---

BOARD = 10
HALF = BOARD // 2
WHEAT_SEED_PRICE = 10
WHEAT_MAX_DAY = 4
PLANT_UNTIL_DAY = 25
LIQUIDATE_FROM_DAY = 28
SELL_CHUNK = 10
SEED_STOCK = 8
SEED_BATCH = 4
WHEAT_SERVICE_CAP = 20  # C4 can tune this when it adds crop shares.
TARGET_HANDS = 4

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
    """Return wheat plant tiles as ``(position, tile)`` pairs."""
    out = []
    for y in range(BOARD):
        for x in range(BOARD):
            tile = tiles[y][x]
            if (
                isinstance(tile, dict)
                and tile.get("kind") == "PLANT"
                and tile.get("crop", "WHEAT") == "WHEAT"
            ):
                out.append(((x, y), tile))
    return out


def _collect_jobs(obs, me, priv):
    """Collect one deterministic wheat job per useful tile for this turn."""
    day = obs["day"]
    water, harvest = [], []
    for pos, tile in _plants(me["tiles"]):
        # Keep one job per tile: mature wheat is harvested before ordinary
        # watering, while younger unwatered wheat is watered first.
        if (
            tile.get("yield_units", 0) > 0
            and day - tile.get("planted_day", day) >= WHEAT_MAX_DAY
        ):
            harvest.append((pos, ["HARVEST"]))
        elif not tile.get("watered_today", False):
            water.append((pos, ["WATER"]))

    seeds = priv.get("seeds", {}).get("WHEAT", 0)
    room = max(0, WHEAT_SERVICE_CAP - len(_plants(me["tiles"])))
    plant = []
    if day <= PLANT_UNTIL_DAY:
        plant = [
            (pos, ["PLANT", "WHEAT"])
            for pos in _open_tiles(me["tiles"])[: min(seeds, room)]
        ]

    # Preserve the v1 urgency order while C4 adds crop-aware readiness.
    return harvest + water + plant


def _assign_jobs(me, priv, jobs):
    """Assign jobs to nearest free units and return one action per unit."""
    positions = [tuple(me["farmer"])] + [tuple(p) for p in me["hands"]]
    inventories = priv.get("inventories", [])
    actions = [["PASS"] for _ in positions]
    busy = [False] * len(positions)

    # Carrying wheat is always a higher priority than another field operation.
    for index, pos in enumerate(positions):
        inventory = inventories[index] if index < len(inventories) else {}
        if inventory.get("WHEAT", 0) <= 0:
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
    """Schedule the farmer and every visible hand for wheat and logistics."""
    return _assign_jobs(me, priv, _collect_jobs(obs, me, priv))


def _market_orders(obs, me, priv):
    """Order wheat sales, hires, and then only affordable cap-filling seeds."""
    day = obs["day"]
    orders = []

    # Sales must lead the queue so their proceeds can fund subsequent orders.
    have = priv["shed"].get("WHEAT", 0)
    if have > 0:
        quantity = have if day >= LIQUIDATE_FROM_DAY else min(have, SELL_CHUNK)
        orders.append(["SELL", "WHEAT", quantity])

    # C2 hires at the start of the day; engine resolution owns their costs.
    if obs["hour"] <= 2:
        missing = max(0, TARGET_HANDS - me["hires_today"])
        for _ in range(min(missing, TARGET_HANDS)):
            orders.append(["HIRE"])

    # Keep v1's seed budget and planting cutoff after the labour orders.
    if day <= PLANT_UNTIL_DAY:
        seeds = priv["seeds"].get("WHEAT", 0)
        room = WHEAT_SERVICE_CAP - len(_plants(me["tiles"]))
        gap = min(SEED_STOCK - seeds, SEED_BATCH, room)
        if gap > 0 and me["money"] >= WHEAT_SEED_PRICE * gap:
            orders.append(["BUY_SEED", "WHEAT", gap])

    return orders


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
