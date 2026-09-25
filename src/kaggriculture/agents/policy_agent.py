"""PA1: the first TRUE policy agent - one plan, one job board, every worker ours.

Stage PA1 of ``odd/tasks/policy-agent.md``. The three refutations that came before
it (`v10_*` tape overlays, `roster_owner` stage 1 at -$144k, the shop calendar)
all failed the same way: they appended chores to an agent that did not own the
farmer, the hands, the hires, the land, the seeds and the sells. This module stops
appending. It is the whole farm:

  1. **Plan vector.** ``DEFAULT_PLAN`` is a ``kaggriculture-plan/3`` object derived
     from the tape's own behaviour (``scripts/tape_to_plan.py``) and extended to
     four quadrants. It is parsed by ``param_agent.validate_plan``, so an unknown
     field is an error and a plan from another revision is rejected, not misread.
  2. **State model.** ``read_state`` rebuilds everything the board reads from ONE
     observation: own tiles/crops/weeds/shed/seeds/carry, and the public market.
     There is no cross-turn bookkeeping at all, because there is no ownership to
     reconcile: the farmer and every hand belong to this plan.
  3. **ONE job board** driving the farmer AND every hand: harvest-ready > water-due
     > bonus water > weed > plant per the calendar > collect > dig > carry. The
     board is the only place a unit action is chosen; there is no second route.
  4. **Hires and land** per plan, funded the way ``se_loop`` proved: the land gate
     is net of this turn's spending, hires are exempt from the land floor (the
     ``param_agent`` deadlock: below the floor the quadrant is already lost and
     blocking labour only deepens the loss).
  5. **Seed orders** one per crop with a quantity, bounded by the tiles that crop
     can actually plant this round - never a per-unit loop, never a re-buy loop.
  6. **Reactive market.** Metered sells, one order per product, subject to a price
     floor against the engine's own curve, an endgame liquidation from day 28, and
     a shed-pressure override so a full shed is sold rather than deleted.

Boundary of PA1: **no animals**. Zero ``BUY_ANIMAL`` / ``BUILD_*`` / ``PLACE`` /
``FEED`` emissions, no fertilizing, no ``COLLECT_FERTILIZER`` job in practice. The
animal economy is PA2; the board keeps the COLLECT tier so PA2 has a hook, and a
test asserts the boundary holds.

Reuse map (nothing here re-implements a rule that is already tested elsewhere):

  * ``param_agent`` - the plan contract (``validate_plan``/``PlanError``), the daily
    roster curve (``hand_target_for_day``), the hire price (``hand_cost``), and the
    board constants (``MAX_ORDERS``, ``SHED_CAPACITY``, ``TURNS_PER_DAY``).
  * ``se_loop`` - the per-tile crop predicates (``harvest_due``, ``needs_water``,
    ``water_is_urgent``, ``water_boosts_harvest``, ``harvest_maturity_day``), the
    geometry (``designated_tiles``, ``crop_sequence``, ``shed_access_tiles``,
    ``quadrant_of``, ``step_toward``) and the observation coercions.

**What resisted reuse, exactly.** ``se_loop.SELoopOverlay`` could not be reused as a
base class, and not for an aesthetic reason: (a) its ``_choose``/``_tile_job`` plant
on *any* empty tile of the SE plan with no calendar (`plant_days` does not exist in
its plan model), (b) its priority chain is different on purpose (it digs weeds at 2
and waters at 3, because it only owns 1-2 hands and a weed is cheaper to fix than a
bonus water), and (c) it owns a *subset* of the roster and has to prove each hand
index is ours through ``_resolve_pending``, which is exactly the bookkeeping that
disappears once the whole fleet is ours. Forking ``_choose`` silently would have
duplicated the engine traps it encodes; instead this module imports its pure parts
and re-derives only the selection loop, which is 30 lines and fully covered by the
tests in ``tests/test_policy_agent.py``. The private names imported below
(``_as_int``/``_as_float``/``_position``) are borrowed on purpose: if ``se_loop``
renames them this module breaks loudly at import, which is better than a silent
second copy of the same coercion.

Engine facts this module is built on (verified against
``.venv/lib/python3.14/site-packages/kaggle_environments/envs/kaggriculture``):

  * Unit actions run BEFORE the market, so seeds bought this turn are plantable
    next turn and hands hired this turn work from next turn.
  * ``SELL`` is committed ONE UNIT AT A TIME with the price re-quoted against the
    live inventory (``_process_market``), and orders are processed by slot index in
    lockstep across players. Slot order is therefore cash order: the sells in this
    module come first so they fund the buys that follow them in the same queue.
  * ``SELL`` only reads ``private["shed"]``; carried goods reach the shed at
    midnight for free, or through ``DROP`` on a shed-access tile.
  * ``_commit_unit`` prices the market curve per unit, and ``_town_consume`` drains
    the inventory every 4 steps per unlocked shop, so prices RECOVER; that is why a
    price floor is worth having at all.
  * ``DROP`` deletes whatever does not fit in the shed, and the midnight drop does
    the same - a full shed destroys value.
  * ``PLANT`` is atomic per crop and turn: if the total PLANT demand for a crop
    exceeds the seeds held, EVERY PLANT of that crop is voided. The board keeps a
    per-crop budget for exactly this.
"""

from __future__ import annotations

from collections.abc import Callable

from kaggle_environments.envs.kaggriculture.kaggriculture import (
    CROPS,
    LAND_PRICES,
    MARKET_PARAMS,
    PRODUCTS,
)

from kaggriculture.agents.param_agent import (
    MAX_ORDERS,
    SHED_CAPACITY,
    TURNS_PER_DAY,
    PlanError,
    farm_of,
    hand_cost,
    hand_target_for_day,
    validate_plan,
)
from kaggriculture.agents.se_loop import (
    _as_float,  # borrowed internal: see the reuse note in the module docstring
    _as_int,
    _position,
    crop_sequence,
    designated_tiles,
    harvest_due,
    harvest_maturity_day,
    needs_water,
    quadrant_of,
    shed_access_tiles,
    step_toward,
    water_boosts_harvest,
    water_is_urgent,
)

SEASON_LAST_DAY = 29
#: The four tiles around the shed are farmable in no quadrant (the shed is used
#: *through* them), so a full board has 4 x 24 workable cells, not 4 x 25.
QUADRANTS = ("NW", "NE", "SW", "SE")
WORKABLE_PER_QUADRANT = 24
CROP_OF = tuple(CROPS)

# Job priorities. Lower runs first; the spec's chain is
# harvest-ready > water-due > weed > plant > collect > dig > carry, with the
# "one more water still adds a unit" tier inserted right after water-due: skipping
# that water loses a unit, skipping water-due weeds the plant outright.
_P_HARVEST = 0
_P_WATER_URGENT = 1
_P_WATER_BONUS = 2
_P_WEED = 3
_P_PLANT = 4
_P_COLLECT = 5
_P_DIG = 6
_P_CARRY = 7
_P_DROP = 0
_P_CARRY_LATE = 0  # a carried load is lost at the final step, not dropped at midnight


# --------------------------------------------------------------------------- #
# The plan vector: derived from the tape, extended to four quadrants, no animals.
# --------------------------------------------------------------------------- #

#: Default ``kaggriculture-plan/3`` vector.
#:
#: * **land** - the tape unlocks its two paid quadrants on days 6 and 11 on a
#:   three-quadrant board. PA1 wants all four, and the fourth (SE, $4,000) is gated
#:   at day 13 because the tape's own SE buy happens around day 10 and entering
#:   before the price is accrued permanently loses the quadrant. Buy days are a
#:   FLOOR (``day >= buy_day``), never a schedule.
#: * **hands** - the tape's own roster target is 12 (``tape_to_plan``: 7 distinct
#:   values, 12 by day 18). PA1 keeps the target and ramps one hand per day.
#: * **crops** - the tape's peak crop mix on a 75-cell board that also carries 17
#:   animal structures (WHEAT 41 / STRAWBERRY 32 / CARROT 30 / MELON 12 = 115 peak
#:   tiles), scaled by 96/115 onto PA1's 96 workable animal-free cells: WHEAT 34 /
#:   STRAWBERRY 27 / CARROT 25 / MELON 10. TOMATO was 0 in the tape and stays 0.
#:   The calendars are the crops' own cycles: WHEAT matures in 4 days, CARROT in 3,
#:   MELON in 12, STRAWBERRY is ongoing and pays from day 10. They are listed at the
#:   cycle's own cadence (plus the policy's planting grace) rather than as a handful
#:   of round starts, because a tile freed late by the harvest queue must not sit
#:   idle until the next round: measured, a 4-day-round calendar left 40-50 of the
#:   96 tiles empty in mid-season.
#: * **animals** - all zero: PA1's boundary. ``count: 0`` with an empty
#:   ``buy_days`` is how the schema says "never buy this".
#: * **market** - sell from day 0, no chunk cap (the shed-pressure rule and the
#:   price floor are the throttle), one seed kept per crop as a reserve.
#: * **fertilize_tiles** - 0 everywhere: fertilizer only enters the farm through an
#:   animal (``COLLECT_FERTILIZER``) or a purchase, and PA1 has neither.
DEFAULT_PLAN = {
    "schema": "kaggriculture-plan/3",
    "land": {"quadrants": 4, "buy_days": [6, 11, 13]},
    "hands": {"start": 4, "ramp_step": 1, "daily_target": 12},
    "crops": [
        {"crop": "WHEAT", "tiles": 34,
         "plant_days": [0, 4, 8, 12, 16, 20, 24, 28], "fertilize_tiles": 0},
        {"crop": "CARROT", "tiles": 25,
         "plant_days": [0, 3, 6, 9, 12, 15, 18, 21, 24, 27], "fertilize_tiles": 0},
        {"crop": "TOMATO", "tiles": 0, "plant_days": [], "fertilize_tiles": 0},
        {"crop": "STRAWBERRY", "tiles": 27,
         "plant_days": [1, 4, 7, 10], "fertilize_tiles": 0},
        {"crop": "MELON", "tiles": 10, "plant_days": [0, 13], "fertilize_tiles": 0},
    ],
    "animals": [
        {"animal": "GOOSE", "count": 0, "buy_days": []},
        {"animal": "COW", "count": 0, "buy_days": []},
        {"animal": "SHEEP", "count": 0, "buy_days": []},
    ],
    "market": {"sell_chunk_cap": 0, "sell_from_day": 0, "seed_reserve": 1},
}


class Policy:
    """Market and hiring knobs. The defaults are the values the PA1 gate ran at.

    A plain class with class-level defaults, NOT a dataclass: ``load_agent``
    executes an agent file as a module that is never registered in ``sys.modules``,
    and ``dataclasses`` resolves field types through ``sys.modules[cls.__module__]``
    - a dataclass here raises AttributeError on ``'NoneType' object has no attribute
    '__dict__'`` before the season starts. Same constraint as the one-argument
    closure: the runner loads this file, it does not import the package.
    """

    #: Sell below this share of the base price only if forced (endgame / full shed).
    #: Calibrated against the tape on 6 seeds over {0.0, 0.35, 0.5, 0.75, 0.9}: mean
    #: final banks $24.4k / $24.8k / $25.2k / $26.2k / $24.8k. Holding for a drained
    #: market pays because ``_town_consume`` refills demand every 4 steps, and 0.9
    #: gives it back because too much stock then sits in the shed when the price
    #: never comes back (strawberry's glut collapses to $1 by day 20).
    sell_floor_ratio = 0.75
    #: Sell everything, floor and cap ignored, from this day on.
    liquidate_day = 28
    #: Sell everything when this many units are held across shed AND hands. The
    #: midnight drop DELETES what does not fit in the shed, so a load the shed cannot
    #: take is already lost: hoarding below the price floor is only worth it while
    #: there is room for it.
    shed_pressure = 80
    #: Hires ordered per turn (the order cap is per turn, so the roster ramps).
    hires_per_turn = 6
    #: Buy seed for a crop whose planting day is today or within this many days.
    plant_horizon = 1
    #: Keep planting a calendar day this many days after it: a tile freed late by
    #: the harvest queue still gets its round instead of waiting for the next one.
    plant_grace = 2
    #: No planting after this hour: a plant needs a WATER the same day.
    plant_until_hour = 22
    #: Cash held back for the next planned quadrant, inside its accrual window.
    land_margin = 100.0
    land_floor_lead = 4
    land_floor_grace = 1
    #: The reserve may never claim more than this share of the cash in hand. A
    #: farm that cannot buy seed is worth less than the quadrant it hoards for:
    #: measured, an unbounded reserve took the seed budget to $0 for days 6-14 and
    #: the tiles stood empty while the cash sat in the bank.
    land_floor_max_share = 0.5
    #: Share of the spendable cash a turn may put into seed orders. The real cap is
    #: the demand itself (only tiles plantable this round), so this stays at 1.0.
    seed_budget_share = 1.0
    #: Last days: a load in hand outranks farm work (the season ends at step 719).
    carry_from_day = 28

    FIELDS = ("sell_floor_ratio", "liquidate_day", "shed_pressure", "hires_per_turn",
              "plant_horizon", "plant_grace", "plant_until_hour", "land_margin",
              "land_floor_lead", "land_floor_grace", "land_floor_max_share",
              "seed_budget_share", "carry_from_day")

    def __init__(self, **overrides):
        unknown = set(overrides) - set(self.FIELDS)
        if unknown:
            raise ValueError(f"unknown policy fields: {sorted(unknown)}")
        for name in self.FIELDS:
            setattr(self, name, overrides.get(name, getattr(type(self), name)))

    def replace(self, **overrides):
        """A copy with overrides: the calibration seam."""
        merged = {name: getattr(self, name) for name in self.FIELDS}
        merged.update(overrides)
        return Policy(**merged)


#: The policy the PA1 gate is measured on. Overridable per agent for calibration.
DEFAULT_POLICY = Policy()


# --------------------------------------------------------------------------- #
# State model: rebuilt from every observation, never remembered.
# --------------------------------------------------------------------------- #

class FarmState:
    """Everything the board reads, from ONE observation.

    PA1 owns the farmer and the whole roster, so there is nothing to reconcile
    between turns and nothing to remember: ``hires_today`` and the unlocked
    quadrants both come from the observation. That is the payoff of owning the
    farm instead of overlaying it, and it is why this class has no mutators.
    """

    __slots__ = ("step", "day", "hour", "money", "unlocked", "tiles", "seeds",
                 "shed", "inventories", "positions", "prices", "inventory",
                 "hires_today")

    def __init__(self, obs):
        farm = farm_of(obs)
        private = obs.get("private") if isinstance(obs, dict) else None
        private = private if isinstance(private, dict) else {}
        step = _as_int(obs.get("step"), -1) if isinstance(obs, dict) else -1
        if step < 0:
            step = _as_int(obs.get("day"), 0) * TURNS_PER_DAY + _as_int(obs.get("hour"), 0)
        self.step = step
        self.day = _as_int(obs.get("day"), step // TURNS_PER_DAY) if isinstance(obs, dict) \
            else step // TURNS_PER_DAY
        self.hour = _as_int(obs.get("hour"), step % TURNS_PER_DAY) if isinstance(obs, dict) \
            else step % TURNS_PER_DAY
        self.money = _as_float(farm.get("money", 0.0))
        quadrants = farm.get("unlocked_quadrants") or []
        self.unlocked = tuple(q for q in quadrants if q in QUADRANTS) or ("NW",)
        tiles = farm.get("tiles") or []
        self.tiles = tiles if isinstance(tiles, list) else []
        seeds = private.get("seeds") or {}
        self.seeds = seeds if isinstance(seeds, dict) else {}
        shed = private.get("shed") or {}
        self.shed = shed if isinstance(shed, dict) else {}
        inventories = private.get("inventories") or []
        self.inventories = inventories if isinstance(inventories, list) else []
        positions = [farm.get("farmer")] + list(farm.get("hands") or [])
        self.positions = tuple(_position(p) for p in positions)
        market = obs.get("market") if isinstance(obs, dict) else None
        market = market if isinstance(market, dict) else {}
        prices = market.get("prices") or {}
        self.prices = prices if isinstance(prices, dict) else {}
        inventory = market.get("inventory") or {}
        self.inventory = inventory if isinstance(inventory, dict) else {}
        self.hires_today = _as_int(farm.get("hires_today", 0))

    # -- tiles ---------------------------------------------------------------

    def cell(self, pos):
        """The tile, or ``"LOCKED"`` when it cannot be read. Unreadable is not ours."""
        try:
            return self.tiles[pos[1]][pos[0]]
        except (IndexError, KeyError, TypeError):
            return "LOCKED"

    def unlocked_at(self, pos):
        return self.cell(pos) != "LOCKED"

    # -- pockets -------------------------------------------------------------

    def carried(self, index):
        """The inventory of unit ``index`` (0 = farmer) as a dict."""
        if 0 <= index < len(self.inventories):
            inventory = self.inventories[index]
            return inventory if isinstance(inventory, dict) else {}
        return {}

    def carried_units(self, index):
        return sum(max(0, _as_int(n)) for n in self.carried(index).values())

    @property
    def roster(self):
        return sum(1 for pos in self.positions[1:] if pos is not None) \
            if len(self.positions) > 1 else 0

    @property
    def shed_used(self):
        return sum(max(0, _as_int(n)) for n in self.shed.values())

    @property
    def shed_room(self):
        return max(0, SHED_CAPACITY - self.shed_used)

    def price(self, item):
        params = MARKET_PARAMS.get(item) or {}
        base = _as_float(params.get("base", 0.0))
        return _as_float(self.prices.get(item, base), base)


def read_state(obs) -> FarmState:
    """Rebuild the farm state from one observation. Never raises."""
    try:
        return FarmState(obs)
    except Exception:  # a malformed observation degrades to an empty farm
        return FarmState({})


def held_units(state) -> int:
    """Everything this farm holds: the shed plus every worker's inventory."""
    total = state.shed_used
    for index in range(len(state.positions)):
        total += state.carried_units(index)
    return total


# --------------------------------------------------------------------------- #
# Tile assignment.
# --------------------------------------------------------------------------- #

class FarmLayout:
    """Which tile grows which crop. Deterministic and prefix stable.

    ``param_agent.assign_targets`` cannot be reused here: it lays crops out in
    row-major blocks over the tiles unlocked *right now*, so a crop's own tiles move
    when a quadrant unlocks and a plan's per-crop calendar would land on tiles it
    did not plant. This layout is built once from the whole board in quadrant order
    (NW, NE, SW, SE) with ``se_loop.crop_sequence`` round-robining the mix, so every
    crop is spread evenly across all four quadrants and a tile keeps its crop for
    the season, including while its quadrant is still LOCKED.
    """

    def __init__(self, plan, policy=None):
        self.policy = policy or DEFAULT_POLICY
        self.plan = plan
        self.workable = tuple(
            tile
            for quadrant in QUADRANTS
            for tile in designated_tiles(WORKABLE_PER_QUADRANT, quadrant=quadrant)
        )
        mix = {entry["crop"]: entry["tiles"] for entry in plan["crops"] if entry["tiles"] > 0}
        wanted = sum(mix.values())
        if wanted > len(self.workable):
            raise PlanError(
                f"plan asks for {wanted} crop tiles but the board has only "
                f"{len(self.workable)} workable cells (4 quadrants x "
                f"{WORKABLE_PER_QUADRANT}; the four shed-access tiles are not farmable)"
            )
        sequence = crop_sequence(mix, len(self.workable))
        self.plan_tiles = tuple(zip(self.workable, sequence, strict=False))
        self.crop_of = dict(self.plan_tiles)
        self.tiles_of = {
            entry["crop"]: tuple(t for t, c in self.plan_tiles if c == entry["crop"])
            for entry in plan["crops"]
        }
        self.calendar = {entry["crop"]: tuple(entry["plant_days"]) for entry in plan["crops"]}
        self.quadrant_of = {tile: quadrant_of(*tile) for tile in self.workable}

    def plantable(self, crop, day):
        """Whether the crop's calendar window is open (the day itself plus grace)."""
        return self.plant_due(crop, day, lookback=self.policy.plant_grace)

    def tiles_in(self, quadrant):
        return tuple(t for t in self.workable if self.quadrant_of[t] == quadrant)

    def plant_due(self, crop, day, lookback=0, lookahead=0):
        """Whether ``crop``'s calendar asks for a planting inside this window."""
        return any(day - lookback <= planting <= day + lookahead
                   for planting in self.calendar.get(crop, ()))


# --------------------------------------------------------------------------- #
# The invariants, as pure functions.
# --------------------------------------------------------------------------- #

def land_floor(plan, day, unlocked_quadrants, cash=0.0, policy=DEFAULT_POLICY) -> float:
    """Cash this plan may not spend, because the next quadrant is coming up.

    ``se_loop`` gates its land buy on the price plus the seed bill of the tiles it
    is about to work; ``param_agent`` gates it on a hard-coded step window. PA1 does
    neither: the reserve is the *next planned* price plus a margin, it is only
    active inside the buy day's accrual window, and it is capped at
    ``land_floor_max_share`` of the cash in hand.

    Both corrections are measured. Outside the window the buy stays best-effort (the
    engine lets a plan buy a quadrant on any later day), so a missed day cannot
    throttle the farm for a season. And with the reserve unbounded, this agent's
    seed budget went to $0 for days 6-14 while its tiles stood empty: it was
    hoarding $1,100 for a purchase it could already afford, and the resulting dead
    farm could not earn the price of the next quadrant anyway.

    Hires are exempt from this floor (see ``market_orders``): the measured
    ``param_agent`` deadlock was an agent below its floor that could not hire,
    could not earn, and lost the quadrant regardless.
    """
    paid = max(0, _as_int(unlocked_quadrants) - 1)
    buy_days = plan["land"]["buy_days"]
    if paid >= len(buy_days) or paid >= len(LAND_PRICES):
        return 0.0
    target = buy_days[paid]
    if not target - policy.land_floor_lead <= day <= target + policy.land_floor_grace:
        return 0.0
    want = float(LAND_PRICES[paid]) + policy.land_margin
    return min(want, max(0.0, _as_float(cash)) * policy.land_floor_max_share)


def seed_demand(plan, layout, state, policy=DEFAULT_POLICY):
    """Per crop, how many seeds this round still needs. One number per crop.

    The demand is the crop's empty-or-weed tiles that are unlocked AND workable
    now, capped by the crop's planned tile count. It is deliberately not "top the
    stock up to a reserve": that is the loop that drained ``param_agent`` to
    exactly $0 by day 9 while its tiles stood empty.
    """
    demand = {}
    for entry in plan["crops"]:
        crop = entry["crop"]
        if entry["tiles"] <= 0 or not layout.plant_due(
                crop, state.day, lookback=policy.plant_grace, lookahead=policy.plant_horizon):
            continue
        spots = 0
        for tile in layout.tiles_of[crop]:
            if not state.unlocked_at(tile):
                continue
            cell = state.cell(tile)
            if cell is None or (isinstance(cell, dict) and cell.get("kind") == "WEED"):
                spots += 1
        held = _as_int(state.seeds.get(crop, 0))
        # ``seed_reserve`` is the schema's "keep at least this many": it tops the
        # round's shortfall up, it never becomes a second re-buy loop.
        short = max(min(entry["tiles"], spots) - held, plan["market"]["seed_reserve"] - held)
        if short > 0:
            demand[crop] = short
    return demand


def sell_orders(plan, state, policy=DEFAULT_POLICY):
    """Metered sells: at most one order per product, quantity = what is held.

    The engine prices a SELL one unit at a time against the live inventory, so the
    chunk size does not change the revenue of a turn; what the chunk size and the
    floor DO change is when the goods leave. ``_town_consume`` drains the market
    every 4 steps per unlocked shop, so a flooded price recovers and holding can be
    worth it - except that a full shed DELETES its overflow at the midnight drop,
    which is why shed pressure overrides the floor.
    """
    cap = plan["market"]["sell_chunk_cap"]
    liquidate = (state.day >= policy.liquidate_day
                 or held_units(state) >= policy.shed_pressure)
    orders = []
    for item in PRODUCTS:
        held = max(0, _as_int(state.shed.get(item, 0)))
        if held <= 0:
            continue
        if not liquidate:
            params = MARKET_PARAMS.get(item) or {}
            base = _as_float(params.get("base", 0.0))
            if state.price(item) < base * policy.sell_floor_ratio:
                continue
        quantity = held if (cap <= 0 or liquidate) else min(cap, held)
        orders.append(["SELL", item, quantity])
    return orders


def market_orders(plan, layout, state, policy=DEFAULT_POLICY, note=None):
    """Build this turn's market orders. Never more than the engine reads.

    Slot order is processing order, so this is cash order:

      1. **sells**, because they fund everything that follows them in the queue,
      2. **land** once its gated day arrives and the price is in hand,
      3. **seeds**, one order per crop, inside the seed budget,
      4. **hires**, to the plan's roster curve, exempt from the land floor.

    Hires come last on purpose: the land and seed slots above them have a hard
    deadline in the season, while labour can make its way back on the next turn -
    so the roster is the residual claim on the cash, not the first claim on it.
    """
    note = note or (lambda *event: None)
    orders = []
    cash = state.money

    for order in sell_orders(plan, state, policy):
        orders.append(order)
        # Quoted at the pre-sale price: the estimate only over-states income.
        cash += order[2] * state.price(order[1])
        note("sell", state.day, state.step, order[1], order[2], state.price(order[1]))

    paid = max(0, len(state.unlocked) - 1)
    buy_days = plan["land"]["buy_days"]
    if (paid < len(buy_days) and state.day >= buy_days[paid]
            and paid < len(LAND_PRICES) and cash >= LAND_PRICES[paid]):
        orders.append(["BUY_LAND"])
        cash -= LAND_PRICES[paid]
        note("land", state.day, state.step, state.money, LAND_PRICES[paid])

    floor = land_floor(plan, state.day, len(state.unlocked), cash, policy)
    budget = max(0.0, cash - floor) * policy.seed_budget_share
    for crop, short in seed_demand(plan, layout, state, policy).items():
        cost = CROPS[crop]["seed"] * short
        if cost > budget or len(orders) >= MAX_ORDERS - 1:
            continue
        orders.append(["BUY_SEED", crop, short])
        budget -= cost
        cash -= cost
        note("seed", state.day, state.step, crop, short, cost)

    # Hires are exempt from the floor by measurement, not by taste: with the floor
    # applied to labour, an agent whose cash fell below it (day 4: $1,201 against a
    # $2,100 floor) could not hire, could not earn its way back, and deadlocked.
    pending = 0
    hires_today = state.hires_today
    target = hand_target_for_day(plan, state.day)
    while (pending < min(target - state.roster, policy.hires_per_turn)
           and len(orders) < MAX_ORDERS):
        cost = hand_cost(hires_today)
        if cash < cost:
            break
        orders.append(["HIRE"])
        cash -= cost
        hires_today += 1
        pending += 1
        note("hire", state.day, state.step, cost, hires_today)
    return orders[:MAX_ORDERS]


# --------------------------------------------------------------------------- #
# The job board: one board, every unit.
# --------------------------------------------------------------------------- #

def tile_job(cell, crop, day, seeds, hour, plantable, policy=DEFAULT_POLICY):
    """The job this tile offers right now, or None.

    ``seeds`` is the *remaining* seed budget for that crop this turn, decremented
    as PLANT actions are emitted. That is not bookkeeping for its own sake: the
    engine voids every PLANT of a crop whose demand exceeds its stock, so two units
    sent to plant two tiles with one seed plant NOTHING.

    ``hour`` gates planting: a plant needs a WATER the same day (``_new_plant``
    starts the weed clock at 1 and the midnight refresh weeds anything it finds
    unwatered), so a planting in the last hour of the day is a seed thrown away.
    """
    if cell is None:
        if seeds <= 0 or hour > policy.plant_until_hour:
            return None
        if not plantable:
            return None
        if day + harvest_maturity_day(crop) > SEASON_LAST_DAY:
            return None  # it cannot pay for its seed before the season ends
        return ("PLANT", _P_PLANT, crop)
    if not isinstance(cell, dict):
        return None  # LOCKED, or a shape we do not understand: never ours
    kind = cell.get("kind")
    if kind == "WEED":
        return ("DIG", _P_WEED, None)
    if kind == "PLANT":
        here = cell.get("crop")
        if here not in CROPS:
            return None
        boost = water_boosts_harvest(cell, day)
        if harvest_due(cell, day, here) and not boost:
            return ("HARVEST", _P_HARVEST, None)
        if needs_water(cell, day, here):
            # Two different jobs share the WATER verb, and they get the SAME tier.
            # ``water_is_urgent`` is the spec's water-due case: the plant is
            # unwatered and the midnight refresh weeds it tonight. ``boost`` is a
            # plant that survives the night but would bank one more unit. Both are
            # permanent losses, so both are water-due - measured over 5 seeds, giving
            # the boost its own tier below this one cost 17% of the final bank
            # ($24,963 -> $21,242): the units came back to water after the weed pass.
            # The tier below is survival-only: nothing is gained and the plant can
            # still afford to skip a day.
            urgent = water_is_urgent(cell) or boost
            return ("WATER", _P_WATER_URGENT if urgent else _P_WATER_BONUS, None)
        data = CROPS[here]
        age = day - _as_int(cell.get("planted_day", day), day)
        if not data["ongoing"] and _as_int(cell.get("yield_units", 0)) <= 0 \
                and age > data["max_yield_day"]:
            return ("DIG", _P_DIG, None)  # one-shot, spent: reclaim the tile
        return None
    if cell.get("animal"):
        # PA1 buys no animals, so this branch is inert by boundary. It exists so
        # PA2 has a hook and so the COLLECT tier is not an empty promise.
        if _as_int(cell.get("yield_units", 0)) > 0:
            return ("HARVEST", _P_HARVEST, None)
        if cell.get("fertilizer_available"):
            return ("COLLECT_FERTILIZER", _P_COLLECT, None)
        return None
    return None  # an empty structure is not ours: PA1 builds nothing


def board_jobs(layout, state, seeds, policy=DEFAULT_POLICY):
    """Every job on the board this turn: ``[(priority, tile, kind, payload)]``."""
    jobs = []
    for tile, crop in layout.plan_tiles:
        if not state.unlocked_at(tile):
            continue  # a LOCKED tile silently voided a tile op: never target one
        job = tile_job(state.cell(tile), crop, state.day, seeds.get(crop, 0),
                       state.hour, layout.plantable(crop, state.day), policy)
        if job is None:
            continue
        kind, priority, payload = job
        jobs.append((priority, tile, kind, payload))
    return jobs


def _distance(a, b):
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def _sort_key(pos, priority, tile):
    return (priority, _distance(pos, tile), tile[0], tile[1])


def sectors(layout, state, units):
    """One contiguous block of the unlocked plan tiles per unit.

    Without this the board is a per-turn nearest-job auction, and that is what the
    first measured season did: 12 units logged 5,000+ movement steps for 2,200 tile
    actions, criss-crossing the board because a job claimed this turn is free again
    next turn. Sectors are stable while the roster is (they only shift when a
    quadrant unlocks or a hand arrives), which keeps each unit in its own corner:
    the block order is the layout order, which is nearest-first.
    """
    placed = [tile for tile, _crop in layout.plan_tiles if state.unlocked_at(tile)]
    if not placed:
        return [[] for _ in units]
    size = -(-len(placed) // len(units))  # ceil, so every tile is in some sector
    return [placed[index * size:(index + 1) * size] for index in range(len(units))]


def unit_actions(layout, state, policy=DEFAULT_POLICY, note=None):
    """One action per farm unit, farmer first, all from the same board.

    Returns ``(farmer_action, [hand_action, ...])``. A unit that is not standing on
    its target MOVES toward it: every tile operation is a silent no-op from a
    distance, so walking is the only way a job is ever performed. A unit with
    nothing to do in its own sector falls back to the nearest unclaimed job on the
    board, so a lull in one corner is not idle labour.
    """
    note = note or (lambda *event: None)
    seeds = {crop: _as_int(state.seeds.get(crop, 0)) for crop in CROP_OF}
    jobs = board_jobs(layout, state, seeds, policy)
    sheds = shed_access_tiles()
    claimed = set()
    actions = []
    unit_sectors = sectors(layout, state, state.positions)
    for index, pos in enumerate(state.positions):
        if pos is None:
            actions.append(["PASS"])
            continue
        mine = set(unit_sectors[index])
        best = _best_job(pos, jobs, claimed, seeds, mine)
        if best is None:
            best = _best_job(pos, jobs, claimed, seeds, None)
        carried = state.carried_units(index)
        if carried > 0 and (state.shed_room >= carried or state.day >= policy.carry_from_day):
            # A load only earns a trip when the shed can actually take it: a unit
            # walking to a full shed every turn is a unit that never farms again.
            # (Measured: with the price floor holding goods and the shed full, the
            # board idled 50-70 unit-turns a day while its crops died of thirst.)
            targets = [t for t in sheds if t not in claimed]
            if targets:
                shed = min(targets, key=lambda t: (_distance(pos, t), t[1], t[0]))
                if pos == shed and state.shed_room >= carried:
                    key = (_P_DROP, 0, shed[0], shed[1])
                    if best is None or key < best[0]:
                        best = (key, shed, "DROP", None)
                else:
                    priority = (_P_CARRY_LATE if state.day >= policy.carry_from_day
                                else _P_CARRY)
                    key = _sort_key(pos, priority, shed)
                    if best is None or key < best[0]:
                        best = (key, shed, "CARRY", None)
        if best is None:
            actions.append(["PASS"])
            note("act", index, ("PASS",), None, state.day, state.step)
            continue
        _, target, kind, payload = best
        claimed.add(target)
        if pos != target:
            action = [step_toward(pos, target) or "PASS"]
            note("act", index, tuple(action), target, state.day, state.step)
        else:
            action = ["PLANT", payload] if kind == "PLANT" else [kind]
            if kind == "PLANT" and payload in seeds:
                seeds[payload] -= 1
            note("act", index, tuple(action), target, state.day, state.step)
            if kind in ("PLANT", "WATER", "HARVEST", "DIG"):
                note(kind.lower(), target, payload, state.day)
            elif kind == "DROP":
                note("drop", target, carried, state.day)
            elif kind == "CARRY":
                note("carry", target, state.day)
        actions.append(action)
    return actions[0], actions[1:]


def _best_job(pos, jobs, claimed, seeds, tiles):
    """The cheapest-key unclaimed job for a unit, optionally inside ``tiles``."""
    best = None
    for priority, tile, kind, payload in jobs:
        if tile in claimed or (tiles is not None and tile not in tiles):
            continue
        if kind == "PLANT" and seeds.get(payload, 0) <= 0:
            continue  # the atomic PLANT void: our stock ran out this turn
        key = _sort_key(pos, priority, tile)
        if best is None or key < best[0]:
            best = (key, tile, kind, payload)
    return best


# --------------------------------------------------------------------------- #
# The agent: a thin orchestrator over the functions above.
# --------------------------------------------------------------------------- #

def _fallback_action(obs):
    """The do-nothing action: farmer and every visible hand PASS, no orders.

    Also the ``enabled=False`` switch, so the control is byte-clean: the same
    dict, never a partial action and never a market order.
    """
    hands = farm_of(obs).get("hands") or []
    return {"farmer": ["PASS"], "hands": [["PASS"] for _ in hands], "market": []}


class PolicyAgent:
    """Realise one plan with one job board. One instance per seat per episode."""

    def __init__(self, plan=None, policy=None, enabled=True, trace=None):
        self.plan = validate_plan(DEFAULT_PLAN if plan is None else plan)
        self.policy = policy or Policy()
        self.layout = FarmLayout(self.plan, self.policy)
        self.enabled = bool(enabled)
        self.trace = [] if trace is None else trace
        self.crashes = 0

    def _note(self, *event):
        self.trace.append(event)

    def __call__(self, obs):
        if not self.enabled:
            return _fallback_action(obs)
        try:
            return self._decide(obs)
        except Exception:
            # A malformed observation must never take the agent out of the season;
            # the trace entry is here so a crash cannot masquerade as a clean PASS.
            self.crashes += 1
            self._note("crash")
            return _fallback_action(obs)

    def _decide(self, obs):
        state = read_state(obs)
        orders = market_orders(self.plan, self.layout, state, self.policy, self._note)
        farmer, hands = unit_actions(self.layout, state, self.policy, self._note)
        return {"farmer": farmer, "hands": hands, "market": orders}


def make_agent(plan=None, policy=None, enabled=True, trace=None) -> Callable:
    """Build an ``agent(obs)`` closure over one validated plan.

    A plain one-argument function on purpose: the Kaggle runner truncates the
    argument list with ``__code__.co_argcount``, so a callable instance would be
    handed ``(observation, configuration)`` and die on every turn.

    ``enabled=False`` is the byte-clean control (see ``_fallback_action``). Pass a
    list as ``trace=`` to collect the structural evidence the tests assert on:
    ``sell`` / ``land`` / ``seed`` / ``hire`` / ``act`` / ``plant`` / ``water`` /
    ``harvest`` / ``dig`` / ``drop`` / ``carry`` / ``crash`` events.
    """
    policy_agent = PolicyAgent(plan=plan, policy=policy, enabled=enabled, trace=trace)

    def agent(obs):
        return policy_agent(obs)

    return agent


#: Default plan, default policy: what the PA1 gate is measured on.
agent = make_agent()
