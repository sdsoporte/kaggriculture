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

from __future__ import annotations

from pathlib import Path

from kaggle_environments.envs.kaggriculture.kaggriculture import (
    ANIMALS,
    CROPS,
    FARMER_MOVES,
    LAND_PRICES,
)

from kaggriculture.loading import current_step, load_agent

_BASELINE = Path(__file__).with_name("baseline_v8_1.py")

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
        self.base = base_agent if base_agent is not None else load_agent(str(_BASELINE))
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
