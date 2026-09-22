"""Kaggriculture baseline_v4: Scale — Land, Hands, Strawberries & Town Shop Reactive Liquidation.

V1: Land expansion — Q2 (days 8-12), Q3 (days 11-14) with strict profitability cutoff.
V2: Labor scaling — 4 -> 6 -> 8 hands aligned with active field quadrants.
V3: High-yield strawberry engine with exact engine ongoing mechanics.
V4: Dynamic town-shop demand weighting and seasonal quota rotation.
V5: Liquidation discipline — glut detection, shed relief, and day 28+ full clearance.

Self-contained and import-free submission agent for Kaggriculture.
"""

# --- Engine constants mirrored from kaggle_environments ---

BOARD = 10
HALF = BOARD // 2
SHED_TILE = (HALF - 1, HALF - 1)  # (4, 4)
LIQUIDATE_FROM_DAY = 28
SELL_CHUNK = 40
LOW_PRICE_RATIO = 0.90
SHED_PRESSURE = 70

LAND_PRICES = [1000, 2000, 4000]

# Planting cutoffs: ensure all planted crops yield before day 30
PLANT_UNTIL_DAY = {
    "STRAWBERRY": 14,  # First yield at day 10; day 14 planting yields on days 24, 26, 28
    "TOMATO": 21,      # First yield at day 8; day 21 planting yields on day 29
    "WHEAT": 25,       # 4-day cycle
    "CARROT": 26,      # 3-day cycle
}

# High-value crops first to prevent room starvation by low-value crops
CROP_ORDER = ("STRAWBERRY", "TOMATO", "CARROT", "WHEAT")
SELL_ORDER = ("STRAWBERRY", "TOMATO", "CARROT", "WHEAT")

CROPS = {
    "WHEAT": {"seed": 10, "first": 2, "max_day": 4, "interval": 0, "ongoing": False},
    "CARROT": {"seed": 20, "first": 2, "max_day": 3, "interval": 0, "ongoing": False},
    "TOMATO": {"seed": 50, "first": 8, "max_day": 8, "interval": 1, "ongoing": True},
    "STRAWBERRY": {"seed": 100, "first": 10, "max_day": 10, "interval": 2, "ongoing": True},
}

MARKET_BASE = {
    "WHEAT": 25,
    "CARROT": 35,
    "TOMATO": 60,
    "STRAWBERRY": 120,
}

SHOP_CONSUMPTION = {
    "PET_CAFE": {"CARROT": 2},
    "FARMERS_MARKET": {"WHEAT": 1, "CARROT": 1, "TOMATO": 1, "STRAWBERRY": 1},
    "SMOOTHIE_SHOP": {"STRAWBERRY": 1},
    "ICE_CREAM_SHOP": {"STRAWBERRY": 1, "WHEAT": 1},
    "BRUNCH_SPOT": {"STRAWBERRY": 1, "WHEAT": 1},
    "PIZZA_SHOP": {"TOMATO": 1, "WHEAT": 1},
    "BAKERY": {"WHEAT": 1},
}

MOVE_OP = {(0, -1): "NORTH", (0, 1): "SOUTH", (1, 0): "EAST", (-1, 0): "WEST"}


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


def _unlocked_quadrants_count(me):
    unlocked = me.get("unlocked_quadrants", ["NW"])
    if isinstance(unlocked, int):
        return unlocked
    return len(unlocked) if isinstance(unlocked, (list, tuple, set)) else 1


def _get_field_cap(me):
    q = _unlocked_quadrants_count(me)
    if q == 1:
        return 20
    elif q == 2:
        return 42
    return 65


def _get_shop_demand(obs):
    demand = {"WHEAT": 0, "CARROT": 0, "TOMATO": 0, "STRAWBERRY": 0}
    for shop in obs.get("town", {}).get("unlocked_shops", []):
        weights = SHOP_CONSUMPTION.get(shop, {})
        for crop, weight in weights.items():
            if crop in demand:
                demand[crop] += weight
    return demand


def _get_crop_targets(me, day, obs):
    q = _unlocked_quadrants_count(me)
    if q == 1:
        base = {"WHEAT": 8, "CARROT": 6, "TOMATO": 4, "STRAWBERRY": 2}
    elif q == 2:
        base = {"WHEAT": 10, "CARROT": 10, "TOMATO": 8, "STRAWBERRY": 10}
    else:
        base = {"WHEAT": 12, "CARROT": 15, "TOMATO": 12, "STRAWBERRY": 16}

    targets = dict(base)
    demand = _get_shop_demand(obs)

    # Shift quotas toward high-demand crops during active planting window
    if day <= PLANT_UNTIL_DAY["STRAWBERRY"] and demand["STRAWBERRY"] >= 1:
        bonus = min(demand["STRAWBERRY"] * 2, 4)
        targets["STRAWBERRY"] += bonus
        targets["WHEAT"] = max(4, targets["WHEAT"] - bonus)

    if day <= PLANT_UNTIL_DAY["CARROT"] and demand["CARROT"] >= 2:
        bonus = min(demand["CARROT"] * 2, 4)
        targets["CARROT"] += bonus
        targets["WHEAT"] = max(4, targets["WHEAT"] - bonus)

    # Seasonal quota rotation: as cutoffs pass, shift quotas to viable crops
    if day > PLANT_UNTIL_DAY["STRAWBERRY"]:
        freed = targets.pop("STRAWBERRY", 0)
        targets["STRAWBERRY"] = 0
        if day <= PLANT_UNTIL_DAY["TOMATO"]:
            targets["TOMATO"] += freed // 2
            targets["CARROT"] += freed - (freed // 2)
        else:
            targets["CARROT"] += (freed * 2) // 3
            targets["WHEAT"] += freed - ((freed * 2) // 3)

    if day > PLANT_UNTIL_DAY["TOMATO"]:
        freed = targets.pop("TOMATO", 0)
        targets["TOMATO"] = 0
        targets["CARROT"] += (freed * 2) // 3
        targets["WHEAT"] += freed - ((freed * 2) // 3)

    if day > PLANT_UNTIL_DAY["WHEAT"]:
        freed = targets.pop("WHEAT", 0)
        targets["WHEAT"] = 0
        targets["CARROT"] += freed

    return targets


def _open_tiles(tiles):
    """Return unlocked empty tiles sorted closest to shed first."""
    out = []
    for y in range(BOARD):
        for x in range(BOARD):
            if tiles[y][x] is None:
                out.append((x, y))
    out.sort(key=lambda p: (_dist(p, SHED_TILE), p[1], p[0]))
    return out


def _plants(tiles):
    out = []
    for y in range(BOARD):
        for x in range(BOARD):
            tile = tiles[y][x]
            if isinstance(tile, dict) and tile.get("kind") == "PLANT":
                out.append(((x, y), tile))
    return out


def _plant_counts(tiles):
    counts = dict.fromkeys(CROP_ORDER, 0)
    for _, tile in _plants(tiles):
        crop = tile.get("crop")
        if crop in counts:
            counts[crop] += 1
    return counts


def _ready_to_harvest(tile, day):
    if not isinstance(tile, dict):
        return False
    info = CROPS.get(tile.get("crop", "WHEAT"))
    if info is None or tile.get("yield_units", 0) <= 0:
        return False
    age = day - tile.get("planted_day", day)
    if age < info["first"]:
        return False
    if info["ongoing"]:
        return True
    return age >= info["max_day"]


def _collect_jobs(obs, me, priv):
    day = obs["day"]
    tiles = me["tiles"]

    water, harvest_ripe, harvest_ongoing, dig = [], [], [], []

    for y in range(BOARD):
        for x in range(BOARD):
            tile = tiles[y][x]
            if not isinstance(tile, dict):
                continue
            kind = tile.get("kind")
            pos = (x, y)

            if kind == "WEED":
                dig.append((pos, ["DIG"]))
                continue

            if kind == "PLANT":
                ready = _ready_to_harvest(tile, day)
                age = day - tile.get("planted_day", day)
                crop = tile.get("crop", "WHEAT")
                info = CROPS.get(crop, CROPS["WHEAT"])
                ripe_one_time = not info["ongoing"] and age >= info["max_day"]

                if ripe_one_time:
                    harvest_ripe.append((pos, ["HARVEST"]))
                elif not tile.get("watered_today", False):
                    water.append((pos, ["WATER"]))
                elif ready:
                    harvest_ongoing.append((pos, ["HARVEST"]))
                continue

    plant = []
    counts = _plant_counts(tiles)
    open_tiles = _open_tiles(tiles)
    reserved = set()
    field_cap = _get_field_cap(me)
    current_plants = len(_plants(tiles))
    targets = _get_crop_targets(me, day, obs)

    for crop in CROP_ORDER:
        target = targets.get(crop, 0)
        need = max(0, target - counts.get(crop, 0))
        seeds = max(0, priv.get("seeds", {}).get(crop, 0) or 0)
        room = max(0, field_cap - current_plants - len(plant))
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

    return harvest_ripe + water + harvest_ongoing + plant + dig


def _assign_jobs(me, priv, jobs):
    positions = [tuple(me["farmer"])] + [tuple(p) for p in me["hands"]]
    inventories = priv.get("inventories", [])
    actions = [["PASS"] for _ in positions]
    busy = [False] * len(positions)

    # Offload harvested crops to shed
    for index, pos in enumerate(positions):
        inv = inventories[index] if index < len(inventories) else {}
        if any(inv.get(item, 0) > 0 for item in CROP_ORDER):
            busy[index] = True
            actions[index] = ["DROP"] if pos == SHED_TILE else _step_toward(pos, SHED_TILE)

    # Greedy Manhattan assignment
    for target, operation in jobs:
        candidates = [i for i, used in enumerate(busy) if not used]
        if not candidates:
            break
        index = min(candidates, key=lambda i: (_dist(positions[i], target), i))
        busy[index] = True
        actions[index] = operation if positions[index] == target else _step_toward(positions[index], target)

    return actions


def _multi_unit_scheduler(obs, me, priv):
    return _assign_jobs(me, priv, _collect_jobs(obs, me, priv))


def _hire_cost(n_already_today):
    prev, curr = 1, 1
    for _ in range(max(0, n_already_today)):
        prev, curr = curr, prev + curr
    return prev


def _sale_quantity(crop, day, shed, prices):
    have = shed.get(crop, 0)
    if have <= 0:
        return 0
    if day >= LIQUIDATE_FROM_DAY:
        return have
    held = sum(shed.get(item, 0) for item in CROP_ORDER)
    base = MARKET_BASE.get(crop, 50)
    price = prices.get(crop, base)
    if crop == "STRAWBERRY" and (price >= 140 or have >= 12):
        return have
    if price < base * LOW_PRICE_RATIO or held >= SHED_PRESSURE:
        return min(have, SELL_CHUNK)
    return 0


def _market_orders(obs, me, priv):
    day = obs["day"]
    orders = []
    shed = priv.get("shed", {})
    prices = obs.get("market", {}).get("prices", {})

    # 1. Sales lead the queue to fund hires and investments
    for item in SELL_ORDER:
        qty = _sale_quantity(item, day, shed, prices)
        if qty > 0:
            orders.append(["SELL", item, qty])

    # 2. Labor scaling: 4 on Q1, 6 on Q2, 8 on Q3
    unlocked_count = _unlocked_quadrants_count(me)
    if unlocked_count == 1:
        target_hands = 4
    elif unlocked_count == 2:
        target_hands = 6
    else:
        target_hands = 8 if day < 26 else 4

    already = max(0, me.get("hires_today", 0) or 0)
    planned_hires = 0
    if obs.get("hour", 0) <= 2:
        missing = max(0, target_hands - already)
        planned_hires = min(missing, target_hands)
        for _ in range(planned_hires):
            orders.append(["HIRE"])

    hire_cost = sum(_hire_cost(already + offset) for offset in range(planned_hires))
    cash = me.get("money", 0) - hire_cost
    for order in orders:
        if order[0] == "SELL":
            quote = prices.get(order[1], MARKET_BASE.get(order[1], 50))
            cash += 0.8 * quote * order[2]

    # 3. Land expansion with strict profit windows (never buy late!)
    if unlocked_count == 1 and (8 <= day <= 12) and cash >= LAND_PRICES[0] + 1200:
        orders.append(["BUY_LAND"])
        cash -= LAND_PRICES[0]
        unlocked_count = 2
    elif unlocked_count == 2 and (11 <= day <= 14) and cash >= LAND_PRICES[1] + 2000:
        orders.append(["BUY_LAND"])
        cash -= LAND_PRICES[1]
        unlocked_count = 3

    # 4. Disciplined seed purchases respecting crop cutoffs
    if day <= max(PLANT_UNTIL_DAY.values()):
        counts = _plant_counts(me["tiles"])
        seeds_by_crop = priv.get("seeds", {})
        targets = _get_crop_targets(me, day, obs)
        stock_cap = 8 if unlocked_count == 1 else (14 if unlocked_count == 2 else 20)
        batch = 4 if unlocked_count == 1 else 6

        for crop in CROP_ORDER:
            if day > PLANT_UNTIL_DAY.get(crop, 25):
                continue
            target = targets.get(crop, 0)
            seeds = max(0, seeds_by_crop.get(crop, 0) or 0)
            gap = max(0, target - counts.get(crop, 0) - seeds)
            gap = min(gap, batch, max(0, stock_cap - seeds))
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
