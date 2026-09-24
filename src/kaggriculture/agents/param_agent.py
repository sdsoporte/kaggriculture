"""Consume a `kaggriculture-plan/2` vector: realise the season against live state.

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

from kaggle_environments.envs.kaggriculture.kaggriculture import (
    ANIMALS,
    CROPS,
    FARM_HAND_COST_MULT,
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
        _require(isinstance(days, list) and days, f"crops[{crop}].plant_days must be a non-empty list")
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
    orders = []

    paid = max(0, ctx["unlocked_quadrants"] - 1)
    buy_days = plan["land"]["buy_days"]
    if (paid < len(buy_days) and ctx["day"] >= buy_days[paid]
            and money >= LAND_PRICES[paid]):
        orders.append(["BUY_LAND"])
        money -= LAND_PRICES[paid]

    for entry in plan["crops"]:
        if entry["tiles"] <= 0:
            continue
        crop = entry["crop"]
        short = plan["market"]["seed_reserve"] - ctx["seeds"].get(crop, 0)
        if short <= 0:
            continue
        # ONE order per crop carrying the whole shortfall. Buying a seed per order
        # burns the 10-order budget on the first crop of the list: the tape emits
        # ["BUY_SEED", crop, n], and a test caught the per-unit version starving
        # every crop after WHEAT.
        cost = CROPS[crop]["seed"] * short
        if money < cost or len(orders) >= MAX_ORDERS - 3:
            continue
        orders.append(["BUY_SEED", crop, short])
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
        if money < cost or len(orders) >= MAX_ORDERS - 2:
            break
        orders.append(["HIRE"])
        money -= cost
        hires_today += 1

    cap = plan["market"]["sell_chunk_cap"]
    before_animals = ctx["day"] < plan["market"]["sell_from_day"]
    for item in PRODUCTS:
        quantity = ctx["held"].get(item, 0)
        if quantity <= 0:
            continue
        chunk = quantity if (cap == 0 or before_animals) else min(cap, quantity)
        orders.append(["SELL", item, chunk])

    return orders[:MAX_ORDERS]


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


# --------------------------------------------------------------------------- #
# The agent: a thin orchestrator over the invariants above.
# --------------------------------------------------------------------------- #

class Agent:
    """Realise a plan against live state. One instance per seat per episode.

    This revision implements the MARKET layer (land, seeds, animals, hires, sells)
    and nothing else: workers PASS. That is deliberate and visible. The worker job
    board - plant, water, harvest, feed, with the pickup trips that FEED requires
    because it consumes an item the worker carries - is the next increment, and it
    is what W2c's consistency check will measure.
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
        farm = farm_of(obs)
        private = obs.get("private") or {}
        held = held_of(obs)
        ctx = {
            "step": step,
            "day": step // TURNS_PER_DAY,
            "money": farm.get("money", 0),
            "hands_now": len(farm.get("hands") or []),
            "hires_today": farm.get("hires_today", 0),
            "unlocked_quadrants": len(farm.get("unlocked_quadrants") or []),
            "seeds": private.get("seeds") or {},
            "shed_free": max(0, SHED_CAPACITY - sum(held.values())),
            "bought": self._bought,
            "held": held,
        }
        orders = plan_market_orders(self.plan, ctx)
        for order in orders:
            if order[0] == "BUY_ANIMAL":
                self._bought[order[1]] = self._bought.get(order[1], 0) + 1
        return {"farmer": ["PASS"], "hands": [], "market": orders}


def make_agent(plan):
    """Return an ``agent(obs)`` closing over one validated plan."""
    agent = Agent(validate_plan(plan))

    def _agent(obs):
        return agent(obs)

    return _agent
