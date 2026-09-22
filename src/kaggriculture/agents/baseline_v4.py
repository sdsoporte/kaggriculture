"""Kaggriculture baseline_v4: scale — land, hands, animals, liquidation.

V1: land buying — NE day 7 when tomato established; SW day 10 when NE full; never SE.
V2: hand scaling 4→6→8→10→12 (fib batch hires, sell-before-buy).
V3: animal chain (coops/pastures, feed wheat, CARE, fertilizer).
V4: liquidation discipline (meter premium, ride wheat/egg floor, town ×4).
V1–V3 preserve v3: harvest-priority, DIG weeds, per-crop plant_until.

The module is self-contained and import-free so it can be submitted directly
as a Kaggriculture agent.
"""

BOARD = 10
HALF = BOARD // 2
LIQUIDATE_FROM_DAY = 28
SELL_CHUNK = 40
SEED_STOCK = 8
SEED_BATCH = 4
FIELD_CAP = 20

LAND_PRICES = [1000, 2000, 4000]

PLANT_UNTIL_DAY = {
    "WHEAT": 25,
    "CARROT": 26,
    "TOMATO": 21,
}

CROP_ORDER = ("WHEAT", "CARROT", "TOMATO")
SELL_ORDER = ("TOMATO", "CARROT", "WHEAT")
CROP_SHARES = {"WHEAT": 0.50, "CARROT": 0.30, "TOMATO": 0.20}
CROP_TARGETS = {"WHEAT": 10, "CARROT": 6, "TOMATO": 4}
MARKET_BASE = {"WHEAT": 25, "CARROT": 35, "TOMATO": 60}
SHED_PRESSURE = 70
LOW_PRICE_RATIO = 0.90

CROPS = {
    "WHEAT": {"seed": 10, "first": 2, "max_day": 4, "interval": 0, "ongoing": False},
    "CARROT": {"seed": 20, "first": 2, "max_day": 3, "interval": 0, "ongoing": False},
    "TOMATO": {"seed": 50, "first": 8, "max_day": 8, "interval": 1, "ongoing": True},
}

ANIMALS = {
    "GOOSE": {"cost": 300, "structure": "COOP", "first": 4, "interval": 1, "cap": 4, "product": "EGG", "feed": "WHEAT"},
    "COW": {"cost": 400, "structure": "PASTURE", "first": 8, "interval": 2, "cap": 6, "product": "MILK", "feed": "WHEAT"},
    "SHEEP": {"cost": 500, "structure": "PASTURE", "first": 6, "interval": 3, "cap": 6, "product": "WOOL", "feed": "WHEAT"},
}

MOVE_OP = {(0, -1): "NORTH", (0, 1): "SOUTH", (1, 0): "EAST", (-1, 0): "WEST"}
SHED_TILE = (HALF - 1, HALF - 1)


def _dist(a, b):
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def _step_toward(pos, target):
    px, py = pos
    tx, ty = target
    if px != tx:
        return [MOVE_OP[(1, 0)] if tx > px else MOVE_OP[(-1, 0)]]
    if py != ty:
        return [MOVE_OP[(0, 1)] if ty > py else MOVE_OP[(0, -1)]]
    return ["PASS"]


def _open_tiles(tiles):
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

    water, harvest, dig = [], [], []
    feed, care, place_animals, build_structures, collect_fert, harvest_animals = [], [], [], [], [], []

    animal_counts = {}
    free_structures = {"COOP": [], "PASTURE": []}
    animal_targets = {"COW": 3, "SHEEP": 2, "GOOSE": 2}

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
                at_max = not info["ongoing"] and age == info["max_day"]

                if at_max:
                    harvest.append((pos, ["HARVEST"]))
                elif not tile.get("watered_today", False):
                    water.append((pos, ["WATER"]))
                elif ready:
                    harvest.append((pos, ["HARVEST"]))
                continue

            if kind in ("COOP", "PASTURE"):
                if "animal" not in tile:
                    free_structures[kind].append(pos)
                    continue
                animal = tile["animal"]
                animal_counts[animal] = animal_counts.get(animal, 0) + 1
                a_info = ANIMALS.get(animal)
                if not a_info:
                    continue
                if not tile.get("fed_today", False):
                    feed.append((pos, ["FEED"]))
                if tile.get("yield_units", 0) >= 1:
                    harvest_animals.append((pos, ["HARVEST"]))
                if not tile.get("cared_today", False):
                    care.append((pos, ["CARE"]))
                if tile.get("fertilizer_available", False):
                    collect_fert.append((pos, ["COLLECT_FERTILIZER"]))
                continue

    open_tiles = _open_tiles(tiles)
    reserved = set()

    for animal, target in animal_targets.items():
        if target <= 0:
            continue
        a_info = ANIMALS[animal]
        kind = a_info["structure"]
        current = animal_counts.get(animal, 0)
        free = len(free_structures[kind])
        need = max(0, target - current - free)
        for pos in open_tiles:
            if pos in reserved or need <= 0:
                break
            reserved.add(pos)
            build_structures.append((pos, ["BUILD_" + kind]))
            need -= 1

    for animal, target in animal_targets.items():
        if target <= 0:
            continue
        a_info = ANIMALS[animal]
        kind = a_info["structure"]
        current = animal_counts.get(animal, 0)
        free = free_structures[kind]
        for pos in free[:max(0, target - current)]:
            place_animals.append((pos, ["PLACE", animal], (animal, 1)))

    # Restock jobs: pick up animals from shed to place on structures
    # PICKUP must happen at shed (center), PLACE at structure
    shed = priv.get("shed", {})
    restock_animals = []
    for animal, target in animal_targets.items():
        if target <= 0:
            continue
        a_info = ANIMALS[animal]
        kind = a_info["structure"]
        current = animal_counts.get(animal, 0)
        free = free_structures[kind]
        in_shed = shed.get(animal, 0)
        # Need to pick up from shed if we have free structures and animals in shed
        need_restock = min(in_shed, len(free), target - current)
        for _ in range(need_restock):
            restock_animals.append((SHED_TILE, ["PICKUP", animal, 1]))

    plant = []
    counts = _plant_counts(tiles)
    for crop in CROP_ORDER:
        target = CROP_TARGETS[crop]
        need = max(0, target - counts[crop])
        seeds = max(0, priv.get("seeds", {}).get(crop, 0) or 0)
        room = max(0, FIELD_CAP - len(_plants(tiles)) - len(plant))
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

    jobs = restock_animals + feed + care + harvest_animals + water + harvest + build_structures + place_animals + plant + dig
    return jobs


def _assign_jobs(me, priv, jobs):
    positions = [tuple(me["farmer"])] + [tuple(p) for p in me["hands"]]
    inventories = priv.get("inventories", [])
    actions = [["PASS"] for _ in positions]
    busy = [False] * len(positions)

    for index, pos in enumerate(positions):
        inventory = inventories[index] if index < len(inventories) else {}
        if not any(inventory.get(crop, 0) > 0 for crop in CROP_ORDER):
            continue
        busy[index] = True
        actions[index] = ["DROP"] if pos == SHED_TILE else _step_toward(pos, SHED_TILE)

    for target, operation in jobs:
        # Jobs may have optional third element: (item, qty) required in inventory
        need = None
        if len(target) > 2:
            target, operation, need = target[0], operation, target[2]
        candidates = [i for i, used in enumerate(busy) if not used]
        if not candidates:
            break
        # Filter candidates that have required item if need is specified
        if need:
            candidates = [i for i in candidates if inventories[i].get(need[0], 0) >= need[1]]
            if not candidates:
                continue
        index = min(candidates, key=lambda i: (_dist(positions[i], target), i))
        busy[index] = True
        actions[index] = operation if positions[index] == target else _step_toward(positions[index], target)

    return actions


def _multi_unit_scheduler(obs, me, priv):
    return _assign_jobs(me, priv, _collect_jobs(obs, me, priv))


def _hire_cost(n_already_today):
    previous, current = 1, 1
    for _ in range(max(0, n_already_today)):
        previous, current = current, previous + current
    return previous


def _sale_quantity(crop, day, shed, prices):
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
    day = obs["day"]
    orders = []
    shed = priv.get("shed", {})
    prices = obs.get("market", {}).get("prices", {})

    for crop in SELL_ORDER:
        quantity = _sale_quantity(crop, day, shed, prices)
        if quantity > 0:
            orders.append(["SELL", crop, quantity])

    target_hands = 4
    if day >= 3:
        target_hands = 6
    if day >= 7:
        target_hands = 8
    if day >= 10:
        target_hands = 10
    if day >= 12:
        target_hands = 12

    planned_hires = 0
    already = max(0, me.get("hires_today", 0) or 0)
    if obs.get("hour", 0) <= 2:
        missing = max(0, target_hands - already)
        planned_hires = min(missing, target_hands)
        for _ in range(planned_hires):
            orders.append(["HIRE"])
    hire_cost = sum(_hire_cost(already + offset) for offset in range(planned_hires))
    cash = me.get("money", 0) - hire_cost
    for order in orders:
        if order[0] == "SELL":
            quote = prices.get(order[1], MARKET_BASE[order[1]])
            cash += 0.8 * quote * order[2]

    unlocked = me.get("unlocked_quadrants", ["NW"])
    tomato_planted = any(tile.get("crop") == "TOMATO" and (day - tile.get("planted_day", 0) >= 2) for _, tile in _plants(me["tiles"]))
    tomato_active = tomato_planted
    in_planting_season = day <= max(PLANT_UNTIL_DAY.values())
    ne_buy = ("NE" not in unlocked and in_planting_season and tomato_active and day >= 7 and cash >= LAND_PRICES[0] + 500)
    sw_buy = ("SW" not in unlocked and "NE" in unlocked and in_planting_season and day >= 10 and cash >= LAND_PRICES[1] + 500)
    if ne_buy:
        orders.append(["BUY_LAND"])
        cash -= LAND_PRICES[0]
    elif sw_buy:
        orders.append(["BUY_LAND"])
        cash -= LAND_PRICES[1]

    # V3: Animal purchases - buy when cash allows, structures will be built in parallel
    animal_counts = {}
    for y in range(BOARD):
        for x in range(BOARD):
            tile = me["tiles"][y][x]
            if isinstance(tile, dict) and tile.get("kind") in ("COOP", "PASTURE") and tile.get("animal"):
                animal = tile["animal"]
                animal_counts[animal] = animal_counts.get(animal, 0) + 1

    animal_targets = {"COW": 3, "SHEEP": 2, "GOOSE": 2}
    for animal, target in animal_targets.items():
        current = animal_counts.get(animal, 0)
        if current >= target:
            continue
        a_info = ANIMALS[animal]
        # Buy if we have enough cash for animal + operations buffer
        # Structures will be built in parallel by field units
        for _ in range(target - animal_counts.get(animal, 0)):
            if cash >= a_info["cost"] + 1000:
                orders.append(["BUY_ANIMAL", animal, 1])
                cash -= a_info["cost"]

    # Feed wheat management - ensure enough wheat for all animals
    total_animals = sum(animal_counts.values()) + sum({"COW": 3, "SHEEP": 2, "GOOSE": 2}.values())
    wheat_in_shed = shed.get("WHEAT", 0)
    wheat_carried = sum(inv.get("WHEAT", 0) for inv in priv.get("inventories", []))
    if wheat_in_shed + wheat_carried < total_animals * 2:
        gap = total_animals * 2 - wheat_in_shed - sum(inv.get("WHEAT", 0) for inv in priv.get("inventories", []))
        wheat_price = prices.get("WHEAT", MARKET_BASE["WHEAT"])
        buy = min(gap, 20)
        if buy > 0 and cash >= wheat_price * buy:
            orders.append(["BUY_PRODUCT", "WHEAT", buy])
            cash -= wheat_price * buy

    if shed.get("FERTILIZER", 0) < 10 and cash >= prices.get("FERTILIZER", 100) * 5:
        orders.append(["BUY_PRODUCT", "FERTILIZER", 5])
        cash -= prices.get("FERTILIZER", 100) * 5

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
    me = obs["farms"][obs["player"]]
    priv = obs["private"]
    actions = _multi_unit_scheduler(obs, me, priv)
    return {
        "farmer": actions[0],
        "hands": actions[1:],
        "market": _market_orders(obs, me, priv),
    }
