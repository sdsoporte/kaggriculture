"""Consume a `kaggriculture-plan/3` vector: realise the season against live state.

The contract is `docs/planner-parameter-schema.md`, and it says something easy to
get wrong: **every field is a season-level target or calendar, never a script.**
The check that proved it (`scripts/tape_to_plan.py`, W2a) found that the tape's own
behaviour is not a function of its season-level decisions - `land` is identical
across seeds while `hands`, `market`, `crops` and `animals` all vary - because the
agent is reactive to the engine's state. So this module holds the plan and does the
reacting: it assigns tiles, attempts planting calendars when capacity and cash
allow, and grows the hand roster only while money permits.

Design rules that made this testable:

  * The engine facts live in the ENGINE, not here. `CROPS`, `ANIMALS`,
    `LAND_PRICES` and the movement table are imported, so a duplicated crop window
    cannot drift out of date. The verified rule sheet is
    `docs/engine-rule-sheet.md`.
  * The invariants are PURE FUNCTIONS on explicit inputs (`hand_target_for_day`,
    `cash_floor`, `water_window_ok`, `harvest_ready`, `plan_market_orders`,
    `plantable_jobs`). They are the part that must not be wrong, so they are the
    part that is cheap to test and does not need an episode.
  * `Agent` is a thin orchestrator over those functions plus a job board.

Two engine facts this module depends on, both easy to get backwards:

  * `maxMarketOrdersPerTurn` (10) is **per turn**, not per day, and a day is 24
    turns - so a 12-hand roster is reachable by spreading hires across the day.
    Hands are reset every midnight (`kaggriculture.py:880-882`), so the roster is
    re-hired daily and the Fibonacci cost (`fib(hires_today)`) applies per day.
  * `FEED` and `FERTILIZE` consume an item the WORKER is carrying (`_inv_take`), so
    feeding requires a pickup trip to the shed. A job board without a supply step
    silently feeds nothing.
"""

import json
from pathlib import Path

from kaggle_environments.envs.kaggriculture.kaggriculture import (
    ANIMALS,
    CROPS,
    FARM_HAND_COST_MULT,
    FARMER_MOVES,
    LAND_PRICES,
    PRODUCTS,
)

from kaggriculture.loading import current_step

PLAN_VERSION = "kaggriculture-plan/3"
MAX_ORDERS = 10
SEASON_DAYS = 30
TURNS_PER_DAY = 24
SHED_CAPACITY = 100
BOARD_SIZE = 10
CELLS_PER_QUADRANT = (BOARD_SIZE // 2) ** 2

# The tape is broken by a ~$1,000 early-cash perturbation: at step 150 it issues
# BUY_LAND, and at step 150 missing the purchase by $114 permanently lost a
# quadrant, wasted 102 plantings and starved 17 animals (-$92k). The tomato overlay
# died exactly there. So before that turn the plan may not spend into the land
# purchase it is about to need.
LAND_CLIFF_STEP = 150
LAND_CLIFF_MARGIN = 100

HIRES_PER_TURN = 2  # spread the roster: the order cap is per turn, not per day
ANIMAL_PRODUCT = {a: d["product"] for a, d in ANIMALS.items()}
CROP_OF = tuple(CROPS)


class PlanError(ValueError):
    """A plan that does not conform to the schema. Never silently repaired."""


# --------------------------------------------------------------------------- #
# Plan validation - a plan from revision 1 must be REJECTED, not misread.
# --------------------------------------------------------------------------- #

def _require(condition, message):
    if not condition:
        raise PlanError(message)


def _check_int(value, name, low, high):
    _require(isinstance(value, int) and not isinstance(value, bool), f"{name} must be an int")
    _require(low <= value <= high, f"{name} out of range: {value} not in [{low}, {high}]")
    return value


def validate_plan(plan):
    """Return the plan, or raise `PlanError`. Unknown fields are an error."""
    _require(isinstance(plan, dict), "plan must be an object")
    known = {"schema", "land", "hands", "crops", "animals", "market"}
    unknown = set(plan) - known
    _require(not unknown, f"unknown top-level fields: {sorted(unknown)}")
    _require(plan.get("schema") == PLAN_VERSION,
             f"schema must be {PLAN_VERSION!r}, got {plan.get('schema')!r}")

    _require(set(plan) == known, f"missing top-level fields: {sorted(known - set(plan))}")

    land = plan["land"]
    _require(set(land) == {"quadrants", "buy_days"}, f"land fields: {sorted(land)}")
    _check_int(land["quadrants"], "land.quadrants", 1, 4)
    _require(isinstance(land["buy_days"], list), "land.buy_days must be a list")
    _require(len(land["buy_days"]) == land["quadrants"] - 1,
             "land.buy_days must have exactly one day per paid quadrant")
    for day in land["buy_days"]:
        _check_int(day, "land.buy_days[]", 0, SEASON_DAYS - 1)
    _require(land["buy_days"] == sorted(land["buy_days"]), "land.buy_days must ascend")

    hands = plan["hands"]
    _require(set(hands) == {"start", "ramp_step", "daily_target"}, f"hands fields: {sorted(hands)}")
    _check_int(hands["start"], "hands.start", 0, 14)
    _check_int(hands["ramp_step"], "hands.ramp_step", 0, 14)
    _check_int(hands["daily_target"], "hands.daily_target", 0, 14)
    _require(hands["start"] <= hands["daily_target"], "hands.start exceeds daily_target")

    market = plan["market"]
    _require(set(market) == {"sell_chunk_cap", "sell_from_day", "seed_reserve"},
             f"market fields: {sorted(market)}")
    _check_int(market["sell_chunk_cap"], "market.sell_chunk_cap", 0, 100)
    _check_int(market["sell_from_day"], "market.sell_from_day", 0, SEASON_DAYS - 1)
    _check_int(market["seed_reserve"], "market.seed_reserve", 0, 100)

    seen = set()
    for entry in plan["crops"]:
        _require(set(entry) == {"crop", "tiles", "plant_days", "fertilize_tiles"},
                 f"crops entry fields: {sorted(entry)}")
        crop = entry["crop"]
        _require(crop in CROP_OF, f"unknown crop: {crop!r}")
        _require(crop not in seen, f"duplicate crop entry: {crop}")
        seen.add(crop)
        _check_int(entry["tiles"], f"crops[{crop}].tiles", 0, 100)
        _check_int(entry["fertilize_tiles"], f"crops[{crop}].fertilize_tiles", 0, entry["tiles"])
        days = entry["plant_days"]
        _require(isinstance(days, list), f"crops[{crop}].plant_days must be a list")
        # An EMPTY list is legal only for a crop with no tiles: there is nothing to
        # plant, so there is no planting day. A zero-tile crop is exactly how a plan
        # says "never grow this", and an LLM plan supplied [] for TOMATO where
        # revision 3's own example wrote ["4"] - both mean the same thing.
        if entry["tiles"] > 0:
            _require(days, f"crops[{crop}].plant_days must be non-empty when tiles > 0")
        for day in days:
            _check_int(day, f"crops[{crop}].plant_days[]", 0, SEASON_DAYS - 1)
        _require(days == sorted(days), f"crops[{crop}].plant_days must ascend")
    _require(seen == set(CROP_OF), f"crops must list every crop exactly once, missing {sorted(set(CROP_OF) - seen)}")

    _require(ANIMALS, "engine animal table unavailable")
    for entry in plan["animals"]:
        _require(set(entry) == {"animal", "count", "buy_days"}, f"animals entry fields: {sorted(entry)}")
        animal = entry["animal"]
        _require(animal in ANIMAL_PRODUCT, f"unknown animal: {animal!r}")
        _check_int(entry["count"], f"animals[{animal}].count", 0, 30)
        days = entry["buy_days"]
        _require(len(days) == entry["count"],
                 f"animals[{animal}].buy_days must hold exactly one day per animal")
        for day in days:
            _check_int(day, f"animals[{animal}].buy_days[]", 0, SEASON_DAYS - 1)

    # Invariant 9 - the GLOBAL tile budget. There is no physical room for a plan that
    # is valid field by field. This was found by an LLM plan that passed every
    # per-field bound and asked for 142 cells on 75 unlocked ones, which nothing
    # caught because each field was individually in range.
    unlocked_cells = CELLS_PER_QUADRANT * land["quadrants"]
    structures = sum(entry["count"] for entry in plan["animals"])
    crop_tiles = sum(entry["tiles"] for entry in plan["crops"])
    _require(crop_tiles + structures <= unlocked_cells,
             f"plan needs {crop_tiles} crop tiles + {structures} structures = "
             f"{crop_tiles + structures} cells, but {land['quadrants']} quadrants "
             f"unlock only {unlocked_cells}")
    return plan


# --------------------------------------------------------------------------- #
# Invariants, as pure functions. These are the part that must not be wrong.
# --------------------------------------------------------------------------- #

def hand_target_for_day(plan, day):
    """The roster size the plan wants on ``day``: a ramp, capped by the target."""
    hands = plan["hands"]
    return min(hands["daily_target"], hands["start"] + hands["ramp_step"] * day)


def cash_floor(plan, step, unlocked_quadrants, money):
    """Money this plan may NOT spend, because it is about to be needed.

    Invariant 1 of the schema. Before the land cliff the floor is the price of the
    next quadrant plus a margin; after every quadrant the plan wants is bought, the
    floor is zero. `money` is accepted so callers cannot forget to consider it, and
    returned unchanged for symmetry.
    """
    del money
    paid = max(0, unlocked_quadrants - 1)
    if step >= LAND_CLIFF_STEP or paid >= len(plan["land"]["buy_days"]):
        return 0
    if paid >= len(LAND_PRICES):
        return 0
    return LAND_PRICES[paid] + LAND_CLIFF_MARGIN


def water_window_ok(crop, age_days):
    """Invariant 3: a crop's growth bonus only pays inside its watering window."""
    data = CROPS[crop]
    start = (data["max_yield_day"] + 1) // 2
    return start <= age_days <= data["max_yield_day"]


def needs_water(crop, age_days, watered_today, consecutive_unwatered):
    """Whether a plant must be watered today. Two jobs, only one window-gated.

    Invariant 3 is a statement about the **growth bonus**: the engine only adds
    yield when `WATER` lands inside `[(max_yield_day + 1) // 2, max_yield_day]`
    (`kaggriculture.py:440-441`). It is NOT permission to skip watering outside
    that window, because `WATER` also clears the weed clock: `_daily_refresh_plants`
    turns a plant into a WEED after two consecutive unwatered midnights, and
    `_new_plant` starts `consecutive_unwatered` at 1 with the comment "planting day
    counts as unwatered". So a crop watered only inside its window is already a
    weed before the window opens: measured on the broken agent, day 0 planted 15
    tiles with 0 WATER actions and day 1 dug all 15, so the season produced nothing
    and the seed and hire spend could only drain the bank to $0.

    The tape settles the intent: it plants 19 and waters 19 on day 0, i.e. it
    waters on the planting day, outside every growth window.
    """
    if watered_today:
        return False
    return water_window_ok(crop, age_days) or consecutive_unwatered >= 1


def harvest_ready(tile, day):
    """Invariant 5: harvest only what is actually ready and inside its window."""
    if not isinstance(tile, dict) or tile.get("yield_units", 0) <= 0:
        return False
    if tile.get("kind") != "PLANT":
        return True  # animals: any held product is harvestable
    crop = tile.get("crop")
    if not isinstance(crop, str):
        return False
    data = CROPS.get(crop)
    if data is None:
        return False
    return day - tile.get("planted_day", day) >= data["first_yield_day"]


def harvest_due(tile, day):
    """Whether to spend this turn harvesting rather than still growing the crop.

    `harvest_ready` says the crop CAN be harvested; this says harvesting now is the
    right call. For an animal or an ongoing crop it is the same thing. For a
    one-shot crop the engine pays the growth bonus only inside the watering window
    (`kaggriculture.py:440-441`), so harvesting on `first_yield_day` throws most of
    the yield away: a WHEAT tile watered through days 2-4 fetches 4-6 units while
    harvesting on day 2 fetches 1. Measured on the season, harvesting at
    `first_yield_day` turned ~13 tiles into ~13 units per cycle, so revenue (~$2.5k)
    never covered the seed, hire and animal spend (~$5.5k) and the bank ended at $0.

    The one-turn grace at the cap matters: on the maturity day the tile is still
    unwatered, so this returns False, the board waters it, and only the following
    turn harvests it - the day's bonus is banked, not discarded.
    """
    if not harvest_ready(tile, day):
        return False
    data = CROPS.get(tile.get("crop"))
    if data is None or data["ongoing"]:
        return True
    age = day - tile.get("planted_day", day)
    if (water_window_ok(tile["crop"], age) and not tile.get("watered_today", False)
            and tile.get("yield_units", 0) < data["max_yield"]):
        return False
    return (tile.get("yield_units", 0) >= data["max_yield"]
            or age >= data["max_yield_day"])


def plantable_jobs(plan, day, seeds, empty_tiles, assigned_crops):
    """Invariant 2: never ask for more PLANTs of a crop than the seeds held.

    The engine voids EVERY PLANT of a crop whose turn demand exceeds its stock
    (`kaggriculture.py:919-931`), so a plan that over-asks plants NOTHING. Measured
    as never firing on the tape, but a re-planned calendar can trip it, which is why
    this is enforced here rather than trusted.
    """
    jobs = []
    budget = dict(seeds)
    for entry in plan["crops"]:
        crop = entry["crop"]
        if day not in entry["plant_days"]:
            continue
        want = entry["tiles"] - assigned_crops.get(crop, 0)
        want = min(want, budget.get(crop, 0))
        for _ in range(max(0, want)):
            if not empty_tiles:
                break
            _, tile = empty_tiles.pop(0)
            budget[crop] -= 1
            jobs.append((crop, tile))
    return jobs


def hand_cost(hires_today):
    """The engine's re-hire price: ``fib(hires_today)`` scaled by the configured mult.

    `kaggriculture.py:99-101`: the n-th hire of a day costs `mult * fib(n)` with fib
    running 1, 1, 2, 3, 5, 8, 13. Summed over a day that is exactly why a 12-hand
    roster costs 376. Imported rather than reimplemented so it cannot drift.
    """
    a, b = 1, 1
    for _ in range(hires_today):
        a, b = b, a + b
    return FARM_HAND_COST_MULT * a


def plan_market_orders(plan, ctx):
    """Build this turn's market orders, and never more than the engine reads.

    `ctx` is a plain dict so the invariants can be driven from a test without an
    episode: `step`, `day`, `money`, `hands_now`, `hires_today`,
    `unlocked_quadrants`, `seeds`, `shed_free`, `bought`, `held`.

    Ordering is the engineering, not cosmetics:
      1. land first, and only when the cash floor leaves it funded (invariant 1),
      2. seeds up to the reserve, so the planting calendar never over-asks
         (invariant 2),
      3. animals, one per listed day, and only with shed room (invariant 8),
      4. hires, because the roster is reset every midnight,
      5. sells LAST: the tape's early economy is cash-coupled, the sells in a turn
         fund the buys that follow them in the same queue, and hoisting sells out of
         their slots is what starved the `closer_cleo`-style variants.
    Invariant 4 (<= MAX_ORDERS) is enforced on the way out.
    """
    money = ctx["money"] - cash_floor(
        plan, ctx["step"], ctx["unlocked_quadrants"], ctx["money"]
    )
    # Hires are exempt from the floor, and that exemption is a MEASURED correction.
    # With the floor applied to everything, an agent whose cash fell below it (day 4:
    # $1,201 against a $2,100 floor) could not hire, could not buy seed, and could not
    # earn its way back - it deadlocked with no income and its crops died. The floor
    # protects the land purchase only while the purchase is still reachable; once cash
    # is BELOW it the quadrant is already lost, and blocking labour deepens the loss.
    hire_money = ctx["money"]
    # At most half the spendable cash goes into seed, and only for a crop whose next
    # planting is imminent. Without both gates the agent re-bought every crop up to
    # the reserve EVERY turn and spent itself to zero on days it planted nothing -
    # measured: money reached exactly 0 by day 9 with the farm still standing empty.
    seed_budget = max(0, money) // 2
    orders = []

    paid = max(0, ctx["unlocked_quadrants"] - 1)
    buy_days = plan["land"]["buy_days"]
    if (paid < len(buy_days) and ctx["day"] >= buy_days[paid]
            and money >= LAND_PRICES[paid]):
        orders.append(["BUY_LAND"])
        money -= LAND_PRICES[paid]

    for entry in plan["crops"]:
        if entry["tiles"] <= 0 or not _plant_soon(entry, ctx["day"]):
            continue
        crop = entry["crop"]
        # Capped at the crop's own tile count, not the reserve: buying 24 seeds for a
        # crop with 4 tiles is dead stock that cannot be planted.
        short = min(plan["market"]["seed_reserve"] - ctx["seeds"].get(crop, 0),
                    entry["tiles"])
        if short <= 0:
            continue
        cost = CROPS[crop]["seed"] * short
        if cost > seed_budget or len(orders) >= MAX_ORDERS - 3:
            continue
        orders.append(["BUY_SEED", crop, short])
        seed_budget -= cost
        money -= cost

    shed_room = ctx["shed_free"]
    for entry in plan["animals"]:
        animal = entry["animal"]
        bought = ctx["bought"].get(animal, 0)
        cost = ANIMALS[animal]["cost"]
        while (bought < entry["count"] and shed_room > 0
               and entry["buy_days"][bought] <= ctx["day"]
               and money >= cost and len(orders) < MAX_ORDERS - 4):
            orders.append(["BUY_ANIMAL", animal, 1])
            money -= cost
            bought += 1
            shed_room -= 1

    missing = max(0, hand_target_for_day(plan, ctx["day"]) - ctx["hands_now"])
    hires_today = ctx["hires_today"]
    for _ in range(min(missing, HIRES_PER_TURN)):
        cost = hand_cost(hires_today)
        if hire_money < cost or len(orders) >= MAX_ORDERS - 2:
            break
        orders.append(["HIRE"])
        hire_money -= cost
        money -= cost
        hires_today += 1

    cap = plan["market"]["sell_chunk_cap"]
    before_animals = ctx["day"] < plan["market"]["sell_from_day"]
    feed_reserve = int(ctx.get("feed_reserve", 0))
    for item in PRODUCTS:
        quantity = ctx["held"].get(item, 0)
        if item == "WHEAT" and feed_reserve > 0:
            # Feed every animal every day (invariant 6). Selling the last wheat
            # starves the herd, so the day's feed is taken off the market before the
            # sell queue is built; unit actions run before the market, so a worker
            # can still pick this wheat up and feed.
            quantity = max(0, quantity - feed_reserve)
        if quantity <= 0:
            continue
        chunk = quantity if (cap == 0 or before_animals) else min(cap, quantity)
        orders.append(["SELL", item, chunk])

    return orders[:MAX_ORDERS]


def _plant_soon(entry, day, horizon=2):
    """Whether this crop has a planting day now or within the horizon."""
    return any(day <= planting <= day + horizon for planting in entry["plant_days"])


def farm_of(obs):
    """This seat's farm record from an observation."""
    farms = obs.get("farms") or [{}]
    return farms[obs.get("player", 0)] or {}


def held_of(obs):
    """Everything this seat holds: the shed plus every worker's inventory."""
    private = obs.get("private") or {}
    held = dict(private.get("shed") or {})
    for inventory in private.get("inventories") or []:
        for item, count in (inventory or {}).items():
            held[item] = held.get(item, 0) + count
    return held


def obs_tiles(obs):
    """This seat's tile grid from an observation."""
    return farm_of(obs).get("tiles") or []


# --------------------------------------------------------------------------- #
# The worker job board. This is what makes the consumer able to plant.
# --------------------------------------------------------------------------- #

# Engine movement verbs are the inverse of what routing needs, so the table is
# imported and inverted rather than retyped (kaggriculture.py:88-93).
_STEP = {delta: verb for verb, delta in FARMER_MOVES.items()}


def shed_access_tiles():
    """The four inner-corner tiles around the shed (kaggriculture.py:132-135)."""
    half = BOARD_SIZE // 2
    return [(half - 1, half - 1), (half, half - 1), (half - 1, half), (half, half)]


def structure_slots(plan):
    """Tiles the plan needs for animal structures. Animals are IRREMOVABLE."""
    return sum(entry["count"] for entry in plan["animals"])


def structure_plan(plan, targets):
    """Every planned structure as ``(pos, animal)``, in the plan's own order.

    `assign_targets` decides *which* tiles are reserved for structures but not which
    animal goes where. The mapping has to track the reservation order (row-major) so
    a structure is built where the plan's tile budget already counted it.
    """
    positions = sorted(targets[0], key=lambda p: (p[1], p[0]))
    out = []
    cursor = 0
    for entry in plan["animals"]:
        for _ in range(entry["count"]):
            if cursor >= len(positions):
                return out
            out.append((positions[cursor], entry["animal"]))
            cursor += 1
    return out


def assign_targets(plan, obs, board_size=BOARD_SIZE):
    """Decide WHICH tiles the plan's counts refer to. Deterministic, row-major.

    The plan says how many tiles and how many structures, never where. The choice
    has to be stable across turns or a crop's designated tile would move under it,
    so it is derived from the unlocked tile set in a fixed order: structures first
    (they are irreversible), then each crop's block in the plan's own order.
    Shed-access tiles are excluded because the shed is used through them.
    """
    shed = set(shed_access_tiles())
    free = []
    for y, row in enumerate(obs_tiles(obs)):
        for x, cell in enumerate(row):
            if cell == "LOCKED" or (x, y) in shed:
                continue
            free.append((x, y))
    free.sort(key=lambda pos: (pos[1], pos[0]))

    n_structures = min(structure_slots(plan), len(free))
    structures = set(free[:n_structures])
    rest = free[n_structures:]

    crops = {}
    cursor = 0
    for entry in plan["crops"]:
        count = entry["tiles"]
        crops[entry["crop"]] = set(rest[cursor:cursor + count])
        cursor += count
    return structures, crops


def step_toward(pos, target):
    """The single movement verb that shortens the Manhattan distance, or None."""
    if target[0] > pos[0]:
        return _STEP[(1, 0)]
    if target[0] < pos[0]:
        return _STEP[(-1, 0)]
    if target[1] > pos[1]:
        return _STEP[(0, 1)]
    if target[1] < pos[1]:
        return _STEP[(0, -1)]
    return None


def build_jobs(plan, obs, day, targets, seeds):
    """Every unit of work the current board implies, grouped by kind.

    Invariant 2 lives in the PLANT group: a crop is only ever asked to plant as
    many tiles as there are seeds, because the engine voids EVERY PLANT of a crop
    whose turn demand exceeds its stock (`kaggriculture.py:919-931`).
    """
    _, crops = targets
    tiles = obs_tiles(obs)
    jobs = {"feed": [], "water": [], "harvest": [], "plant": [],
            "collect": [], "build": [], "place": [], "dig": []}

    for y, row in enumerate(tiles):
        for x, cell in enumerate(row):
            pos = (x, y)
            if not isinstance(cell, dict):
                continue
            if cell.get("animal"):
                # Feeding first: two consecutive unfed days and the animal escapes.
                if not cell.get("fed_today", False):
                    jobs["feed"].append(pos)
                if cell.get("yield_units", 0) > 0:
                    jobs["harvest"].append(pos)
                jobs["collect"].append(pos)
            elif cell.get("kind") == "PLANT":
                age = day - cell.get("planted_day", day)
                if harvest_due(cell, day):
                    jobs["harvest"].append(pos)
                elif needs_water(cell.get("crop"), age,
                                 bool(cell.get("watered_today", False)),
                                 int(cell.get("consecutive_unwatered", 0))):
                    jobs["water"].append(pos)
            elif cell.get("kind") == "WEED":
                jobs["dig"].append(pos)

    budget = {entry["crop"]: entry["plant_days"] for entry in plan["crops"]}
    available = dict(seeds)
    for crop, spots in sorted(crops.items()):
        if day not in budget[crop]:
            continue
        for pos in sorted(spots, key=lambda p: (p[1], p[0])):
            if available.get(crop, 0) <= 0:
                break
            if tiles[pos[1]][pos[0]] is None:
                jobs["plant"].append((pos, crop))
                available[crop] -= 1

    # Animal realization. Buying an animal only drops it in the shed, so a planned
    # structure has to be BUILT, then the animal PICKED UP and PLACED on it. Without
    # both steps every BUY_ANIMAL was a guaranteed loss: measured, 5 animals bought
    # in a season, 0 placed, 0 animal products sold, $1,900 spent.
    for pos, animal in structure_plan(plan, targets):
        cell = tiles[pos[1]][pos[0]]
        structure = ANIMALS[animal]["structure"]
        if cell is None:
            jobs["build"].append((pos, "BUILD_COOP" if structure == "COOP" else "BUILD_PASTURE"))
        elif isinstance(cell, dict) and cell.get("kind") == structure and "animal" not in cell:
            jobs["place"].append((pos, animal))

    for kind in jobs:
        jobs[kind].sort()
    return jobs


def worker_actions(plan, obs, day, targets, jobs):
    """One action per farm unit, farmer first. Never two units on one target.

    The feeding chain is the reason this is not a plain nearest-job loop: `FEED`
    consumes an item the WORKER carries (`_inv_take`), so a worker without wheat
    has to make a pickup trip to the shed first, and a board without that step
    silently feeds nothing.
    """
    farm = farm_of(obs)
    private = obs.get("private") or {}
    inventories = private.get("inventories") or []
    shed = private.get("shed") or {}
    positions = [farm.get("farmer")] + list(farm.get("hands") or [])
    positions = [tuple(p) if isinstance(p, (list, tuple)) else (0, 0) for p in positions]

    claimed = set()
    order = GENERIC_JOB_ORDER
    animals = tuple(ANIMAL_PRODUCT)
    actions = []
    for index, pos in enumerate(positions):
        inventory = inventories[index] if index < len(inventories) else {}
        carrying = inventory or {}
        carrying_wheat = carrying.get("WHEAT", 0) > 0
        carried_animal = next((a for a in animals if carrying.get(a, 0) > 0), None)
        available_feed = [p for p in jobs["feed"] if p not in claimed]
        place_jobs = [(p, a) for (p, a) in jobs["place"] if p not in claimed]

        if available_feed and carrying_wheat and day >= 4:
            target = min(available_feed, key=lambda p: (_dist(pos, p), p))
            action = ["FEED"]
        elif carried_animal is not None:
            spots = [p for (p, a) in place_jobs if a == carried_animal]
            if spots:
                target = min(spots, key=lambda p: (_dist(pos, p), p))
                action = ["PLACE", carried_animal]
            else:
                target, action = _generic(pos, jobs, claimed, order)
        elif available_feed and not carrying_wheat and shed.get("WHEAT", 0) > 0:
            sheds = [p for p in shed_access_tiles() if p not in claimed]
            if sheds:
                target = min(sheds, key=lambda p: (_dist(pos, p), p))
                action = ["PICKUP", "WHEAT"]
            else:
                target, action = _generic(pos, jobs, claimed, order)
        else:
            wanted = [a for (p, a) in place_jobs if shed.get(a, 0) > 0]
            sheds = [p for p in shed_access_tiles() if p not in claimed]
            if wanted and sheds:
                target = min(sheds, key=lambda p: (_dist(pos, p), p))
                action = ["PICKUP", wanted[0]]
            else:
                target, action = _generic(pos, jobs, claimed, order)

        if target is None:
            actions.append(["PASS"])
            continue
        claimed.add(target)
        actions.append(_act(pos, target, action))
    return actions[0], actions[1:]


def _dist(a, b):
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def _generic(pos, jobs, claimed, order):
    """Nearest unclaimed job of the highest-priority non-empty kind.

    Feeding is NOT in `order`: it needs the carry chain, so a worker without wheat
    is sent to the shed instead of being told to feed.
    """
    for kind in order:
        for entry in sorted(jobs[kind], key=lambda e: (_dist(pos, _entry_pos(e)), _entry_pos(e))):
            tile = _entry_pos(entry)
            if tile in claimed:
                continue
            return tile, _action_for(kind, entry)
    return None, None


def _action_for(kind, entry):
    """The engine action for a job, payload included.

    PLANT and PICKUP are not actions on their own verb: the engine reads `action[1]`
    as the crop (`kaggriculture.py:418-420`) or the item (`:361-362`) and returns
    early without it. A verb alone is a silent no-op. THIS is how the consumer
    shipped a `["PLANT"]` that planted nothing for a whole season - the same
    payload-less form that shipped twice before as `["COOP"]` and `["TOMATO"]`.
    """
    if kind == "plant":
        return ["PLANT", entry[1]]
    if kind == "build":
        return [entry[1]]  # BUILD_COOP / BUILD_PASTURE is the entry
    return list(_ACTION[kind])


def _entry_pos(entry):
    """A job entry is a tile, or a (tile, crop) pair for planting."""
    return entry[0] if isinstance(entry, tuple) and len(entry) == 2 and isinstance(entry[1], str) else entry


def _act(pos, target, action):
    """Move one step toward the target, or perform the action while standing on it."""
    if pos != target:
        return [step_toward(pos, target) or "PASS"]
    return list(action)


# Every kind of job a generic worker can be sent to do MUST have an action here.
# A missing key is not a silent no-op: `_ACTION[kind]` raises inside the agent on
# the first turn that job exists, the engine isolates the failure, and the agent is
# inert for the rest of the season.
_ACTION = {
    "water": ["WATER"],
    "harvest": ["HARVEST"],
    "plant": ["PLANT"],
    "collect": ["COLLECT_FERTILIZER"],
    "dig": ["DIG"],
    "feed": ["FEED"],
    "build": ["BUILD_PASTURE"],  # payload replaced by the entry in `_action_for`
    "pickup": ["PICKUP", "WHEAT"],
}

# The priority order for a generic worker. Feeding and placing are absent on purpose:
# both need a carry chain (pick the item up first), so they are handled before the
# board is consulted.
GENERIC_JOB_ORDER = ("water", "harvest", "plant", "build", "collect", "dig")


# --------------------------------------------------------------------------- #
# The agent: a thin orchestrator over the invariants above.
# --------------------------------------------------------------------------- #

class Agent:
    """Realise a plan against live state. One instance per seat per episode.

    The market layer (land, seeds, animals, hires, sells) is built on the pure
    invariant functions above, and the worker job board adds plant, water, harvest,
    feed, collect, dig, build and place. Two chains are carried by hand rather than
    read off the board because the engine consumes an item the worker carries: FEED
    needs wheat from the shed, and PLACE needs the animal bought into the shed.
    """

    def __init__(self, plan):
        self.plan = validate_plan(plan)
        self._bought = {a["animal"]: 0 for a in plan["animals"]}

    def _reset(self):
        self._bought = {a["animal"]: 0 for a in self.plan["animals"]}

    def __call__(self, obs):
        step = current_step(obs)
        if step == 0:
            self._reset()
        day = step // TURNS_PER_DAY
        farm = farm_of(obs)
        private = obs.get("private") or {}
        held = held_of(obs)
        # Invariant 6: feed the herd every day. The reserve is the plan's full herd
        # size, not the count of animals unfed right now, because the wheat has to be
        # held BEFORE the animal is placed: measured, reserving only the currently
        # unfed count sold the wheat on the placement turn and every animal escaped
        # unfed (yield stayed 0 for all 9 animals, all season).
        feed_reserve = structure_slots(self.plan)
        ctx = {
            "step": step,
            "day": day,
            "money": farm.get("money", 0),
            "hands_now": len(farm.get("hands") or []),
            "hires_today": farm.get("hires_today", 0),
            "unlocked_quadrants": len(farm.get("unlocked_quadrants") or []),
            "seeds": private.get("seeds") or {},
            "shed_free": max(0, SHED_CAPACITY - sum(held.values())),
            "bought": self._bought,
            "held": held,
            "feed_reserve": feed_reserve,
        }
        orders = plan_market_orders(self.plan, ctx)
        for order in orders:
            if order[0] == "BUY_ANIMAL":
                self._bought[order[1]] = self._bought.get(order[1], 0) + 1

        targets = assign_targets(self.plan, obs)
        jobs = build_jobs(self.plan, obs, day, targets, ctx["seeds"])
        farmer, hands = worker_actions(self.plan, obs, day, targets, jobs)
        n_hands = ctx["hands_now"]
        hands = (hands + [["PASS"]] * n_hands)[:n_hands]
        return {"farmer": farmer, "hands": hands, "market": orders}


def make_agent(plan):
    """Return an ``agent(obs)`` closing over one validated plan."""
    agent = Agent(validate_plan(plan))

    def _agent(obs):
        return agent(obs)

    return _agent


def default_plan():
    """The revision-3 vector this consumer is measured on, read from the repo.

    A missing or malformed file raises ``PlanError`` naming the path instead of
    leaving the module importable without a plan: ``load_agent`` would then report
    the failure as "does not expose a callable agent(obs)", which points at the
    wrong thing entirely.
    """
    path = (Path(__file__).resolve().parents[3] / "plans"
            / "planner-author-rev3-opus.json")
    try:
        plan = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise PlanError(f"cannot read the default plan at {path}: {exc}") from exc
    return validate_plan(plan)


#: The plan the module-level ``agent`` runs. ``kaggriculture.loading`` needs a
#: top-level ``agent(obs)`` to load a file at all (``scripts/benchmark.py``, the
#: Kaggle runner), and this module is the plan CONSUMER: the plan is its input, so
#: the default is the revision-3 vector authored for it, read from the repo instead
#: of copied here. A second copy of a plan vector is a second thing to keep true.
DEFAULT_PLAN = default_plan()

#: Default plan, default board: what this consumer is measured on.
agent = make_agent(DEFAULT_PLAN)
