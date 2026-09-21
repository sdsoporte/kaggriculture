"""Kaggriculture baseline_v2, C2: four-hand labour ordering.

This slice preserves baseline_v1's validated wheat loop. Hands are
intentionally PASS-only until C3 adds the multi-unit scheduler; the later C4
slice can extend the same compact policy with three-crop rotation.

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
FIELD_CAP = 5
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
    """Return all plant tiles as ``(position, tile)`` pairs."""
    out = []
    for y in range(BOARD):
        for x in range(BOARD):
            tile = tiles[y][x]
            if isinstance(tile, dict) and tile.get("kind") == "PLANT":
                out.append(((x, y), tile))
    return out


def _pick_job(jobs, pos):
    """Select the nearest job, with deterministic board-order tie breaking."""
    jobs = sorted(jobs, key=lambda j: (_dist(pos, j[0]), j[0][1], j[0][0]))
    return jobs[0]


def _field_op(obs, me, priv):
    """Choose the main farmer's baseline_v1 wheat operation."""
    day = obs["day"]
    tiles = me["tiles"]
    pos = tuple(me["farmer"])
    inv = priv["inventories"][0] if priv["inventories"] else {}
    carrying = inv.get("WHEAT", 0)

    water, harvest, plant = [], [], []
    for p, tile in _plants(tiles):
        if not tile["watered_today"]:
            water.append((p, ["WATER"]))
        elif tile["yield_units"] > 0 and day - tile["planted_day"] >= WHEAT_MAX_DAY:
            harvest.append((p, ["HARVEST"]))

    seeds = priv["seeds"].get("WHEAT", 0)
    empties = _open_tiles(tiles)
    room = FIELD_CAP - len(_plants(tiles))
    if day <= PLANT_UNTIL_DAY and seeds > 0 and empties and room > 0:
        for p in empties[:min(seeds, room)]:
            plant.append((p, ["PLANT", "WHEAT"]))

    # Preserve v1's priority: mature harvest, watering, carry to shed, plant.
    if harvest:
        target, op = _pick_job(harvest, pos)
    elif water:
        target, op = _pick_job(water, pos)
    elif carrying > 0:
        target, op = SHED_TILE, ["DROP"]
    elif plant:
        target, op = _pick_job(plant, pos)
    else:
        return ["PASS"]

    return op if pos == target else _step_toward(pos, target)


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

    # Keep v1's seed budget and field-cap policy after the labour orders.
    if day <= PLANT_UNTIL_DAY:
        seeds = priv["seeds"].get("WHEAT", 0)
        room = FIELD_CAP - len(_plants(me["tiles"]))
        gap = min(SEED_STOCK - seeds, SEED_BATCH, room)
        if gap > 0 and me["money"] >= WHEAT_SEED_PRICE * gap:
            orders.append(["BUY_SEED", "WHEAT", gap])

    return orders


def agent(obs):
    """Kaggriculture entry point."""
    me = obs["farms"][obs["player"]]
    priv = obs["private"]
    return {
        "farmer": _field_op(obs, me, priv),
        # C3 extension point: replace these PASS actions with hand jobs.
        "hands": [["PASS"] for _ in me["hands"]],
        "market": _market_orders(obs, me, priv),
    }
