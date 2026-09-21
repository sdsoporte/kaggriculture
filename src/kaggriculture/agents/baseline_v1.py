"""baseline_v1 -- "Walter, fixed" + field capacity cap.

v1a change over baseline_v0: never plant beyond FIELD_CAP live plants. The
phase-1 report showed v0 planting up to 11 tiles while one farmer can only
service ~5-6 (24 actions/day minus travel); surplus plants died unwatered
and the seed budget silently re-funded them. Everything else is v0.

Policy:
- Buy wheat seed in small batches, plant on unlocked tiles nearest the shed
  first, until day 25 -- but only while live plants < FIELD_CAP.
- Water every planted tile every day, including the planting day.
- Harvest only at max yield day (age 4 for wheat), never earlier.
- Carry harvested wheat to the shed and drop it.
- Sell metered (at most 10 units per turn); liquidate everything from day 28.

Movement follows the reference agents' move-or-act contract: one Manhattan
step toward the current target, or the action itself when already standing on
the target tile. The agent is stateless: every decision derives from the
current observation.
"""

# --- Engine constants (mirrored from kaggle_environments/envs/kaggriculture) ---

BOARD = 10
HALF = BOARD // 2
TURNS_PER_DAY = 24
SEASON_DAYS = 30

WHEAT_SEED_PRICE = 10
WHEAT_MAX_DAY = 4  # harvest at max yield day, not at first yield day (2)

PLANT_UNTIL_DAY = 25  # planting after this day never pays back
LIQUIDATE_FROM_DAY = 28  # unsold stock scores zero at the final bell
SELL_CHUNK = 10  # metered selling: at most this many units per turn
SEED_STOCK = 8  # keep at most this many unplanted seeds on hand
SEED_BATCH = 4  # buy at most this many seeds per turn
FIELD_CAP = 5  # never plant beyond this many live plants; sharp optimum (6 loses to Walter)

MOVE_OP = {(0, -1): "NORTH", (0, 1): "SOUTH", (1, 0): "EAST", (-1, 0): "WEST"}
SHED_TILE = (HALF - 1, HALF - 1)  # (4, 4); always unlocked


def _dist(a, b):
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def _step_toward(pos, target):
    """One deterministic Manhattan step. Horizontal first, then vertical."""
    px, py = pos
    tx, ty = target
    if px != tx:
        return [MOVE_OP[(1, 0)] if tx > px else MOVE_OP[(-1, 0)]]
    if py != ty:
        return [MOVE_OP[(0, 1)] if ty > py else MOVE_OP[(0, -1)]]
    return ["PASS"]


def _open_tiles(tiles):
    """Unlocked, empty tiles, nearest the shed first."""
    out = []
    for y in range(BOARD):
        for x in range(BOARD):
            if tiles[y][x] is None:
                out.append((x, y))
    out.sort(key=lambda p: (_dist(p, SHED_TILE), p[1], p[0]))
    return out


def _plants(tiles):
    """All plant tiles as (pos, tile) pairs."""
    out = []
    for y in range(BOARD):
        for x in range(BOARD):
            tile = tiles[y][x]
            if isinstance(tile, dict) and tile.get("kind") == "PLANT":
                out.append(((x, y), tile))
    return out


def _pick_job(jobs, pos):
    """Nearest job to the farmer's current position."""
    jobs = sorted(jobs, key=lambda j: (_dist(pos, j[0]), j[0][1], j[0][0]))
    return jobs[0]


def _field_op(obs, me, priv):
    """Choose the farmer's op for this turn (move-or-act)."""
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
        for p in empties[: min(seeds, room)]:
            plant.append((p, ["PLANT", "WHEAT"]))

    # Urgency (v1b): mature harvest first (yield is perishable only in the
    # sense that the tile frees late), then watering (two missed days in a
    # row kills the plant), then carry produce to the shed, then planting.
    # With FIELD_CAP=5 there is enough daily slack that harvest-first never
    # costs a plant, and it shortens the harvest->sell pipeline.
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
    """Sell metered wheat; keep small seed batches coming while planting pays."""
    day = obs["day"]
    orders = []

    have = priv["shed"].get("WHEAT", 0)
    if have > 0:
        n = have if day >= LIQUIDATE_FROM_DAY else min(have, SELL_CHUNK)
        orders.append(["SELL", "WHEAT", n])

    if day <= PLANT_UNTIL_DAY:
        seeds = priv["seeds"].get("WHEAT", 0)
        room = FIELD_CAP - len(_plants(me["tiles"]))
        gap = min(SEED_STOCK - seeds, SEED_BATCH, room)
        if gap > 0 and me["money"] >= WHEAT_SEED_PRICE * gap:
            orders.append(["BUY_SEED", "WHEAT", gap])

    return orders


def agent(obs):
    """Kaggriculture entry point: one observation in, one action dict out."""
    me = obs["farms"][obs["player"]]
    priv = obs["private"]
    return {
        "farmer": _field_op(obs, me, priv),
        "hands": [],
        "market": _market_orders(obs, me, priv),
    }
