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

PA2 adds the animal economy on top of the same board, and it adds it as ONE
joint capex problem rather than as a second program hung off the side:

  7. **Reserved structure cells.** ``FarmLayout`` takes the structures out of the
     crop block FIRST - the nearest cells of each quadrant, so the feed chain is
     short - and every crop is laid out on what is left. A reserved cell is never
     planted, so ``BUILD_COOP``/``BUILD_PASTURE`` can never displace a crop, which
     matters because structures are irreversible while a crop is not.
  8. **The joint capex order.** The market's slot order is the funding priority:
     sells fund land, land funds seeds, seeds fund animals, and hires are the
     residual claim - except that the animal slot may not touch the WHOLE price of
     the next quadrant while its accrual window is open, nor the hands' bill for
     this turn. An animal is the one purchase that can be postponed (``buy_days``
     is a floor, and a later animal still yields), so it is the purchase that
     yields.
  9. **The feed chain.** ``FEED`` consumes wheat the WORKER carries, so the herd is
     not fed by fiat: units are designated to pick a batch up at the shed and then
     walk it to the unfed tiles. Two consecutive unfed midnights and the animal is
     gone for good, so feeding is top-tier work, and the sell layer reserves the
     day's wheat before it sells any.
 10. **The rest of the animal day.** ``CARE`` banks the care bonus, and
     ``COLLECT_FERTILIZER`` turns the herd's daily exposure into a product - 1 unit
     per surviving animal per day, one action on a tile a unit is already standing
     on. The animal's own product is harvested off its tile, and eggs/milk/wool/
     fertilizer leave through the same metered sell layer as the crops.

Boundary of PA2: no fertilizing (``FERTILIZE`` is PA3 - ``fertilize_tiles`` is 0
in every crop entry), and no purchase of fertilizer. Everything else in the tile
vocabulary is now reachable, so the boundary is asserted as a whitelist of verbs
rather than as a ban on the animal economy.

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

Animal engine facts (verified the same way, and each one shapes a rule above):

  * ``BUY_ANIMAL`` only puts the animal in the shed. ``PLACE`` needs the worker to
    CARRY an animal and stand on an unoccupied COOP/PASTURE of the matching kind,
    so a purchase is worth nothing until a structure exists and a unit has walked
    both legs of the chain.
  * ``FEED`` consumes a WHEAT from the WORKER's inventory (``_inv_take``), never
    from the shed. A board that emits ``["FEED"]`` without the carry chain feeds
    nothing at all, silently.
  * The midnight check is unforgiving: an animal unfed for two consecutive days
    ESCAPES and the structure is left empty. ``consecutive_unfed >= 1`` during the
    day therefore means "feed it today or lose it".
  * ``CARE`` sets ``cared_today``, worth ``+1`` pending care bonus that is popped
    only on a production day the animal was ALSO fed. Production with care is
    2 EGG/day, 3 MILK/2 days, 4 WOOL/3 days; production without care is one unit.
  * ``COLLECT_FERTILIZER`` hands the worker 1 FERTILIZER and needs
    ``fertilizer_available``, which ``_daily_refresh_animals`` sets for every
    surviving animal at midnight. It does not accumulate, so a day not collected is
    a unit of exposure lost.
  * ``BUILD_*`` needs an EMPTY tile (``tile is None``); ``DIG`` removes an empty
    structure but never a placed animal. ``BUILD`` on a WEED is a no-op, so a
    reserved cell has to be dug before it can be built.
  * ``BUY_PRODUCT`` accepts only WHEAT and FERTILIZER, onto the shed, at the curve
    price - the feed shortfall can always be bought instead of grown.
"""

from __future__ import annotations

from collections.abc import Callable

from kaggle_environments.envs.kaggriculture.kaggriculture import (
    ANIMALS,
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
#
# PA2 inserts the animal work into that same chain instead of appending it:
#
#   * FEED runs ABOVE everything (its own tier, -1). The spec puts it "next to
#     water-due", and that is where it started - and one tier lower it lost the herd:
#     at the water-due tier a feed only runs when every water job is farther away, and
#     at the harvest tier it only runs when every harvest is farther away, while a
#     harvester carrying 40 wheat walks past unfed animals all day. Measured: 12 of 18
#     animals unfed after a day, 8 escapes in a single midnight, and half the herd's
#     days fed in a season. The tier is safe to spend because only a unit CARRYING
#     wheat can take a FEED job at all - the crop board sees no change - and the
#     chain is bounded by the herd: once every animal is fed there is no FEED job
#     left, and the carrier goes back to the harvest.
#   * CARE shares water-due's tier, not the bonus tier. Care is the difference
#     between 1 and 2 EGG a day - strictly more than the single unit of crop a bonus
#     water banks - and the unit that just fed the animal is standing ON it, so at
#     equal tier it wins the distance tie-break and the animal's whole day completes
#     in one tour instead of one job per unit.
#   * PLACE shares water-due's tier: an animal in the shed earns nothing at all.
#   * BUILD shares the bonus tier: a structure can wait an hour, and it only has to
#     land before the purchase it was built for.
#   * COLLECT_FERTILIZER runs at the care tier (see below), not at PA1's tier 5.
_P_HARVEST = 0
_P_FEED = -1
_P_WATER_URGENT = 1
_P_CARE = 1
_P_PLACE = 1
#: COLLECT_FERTILIZER runs at the care tier, not PA1's tier 5. The chain is one tour
#: per animal per day, and a unit standing on the animal it just cared for has to be
#: able to finish the tour: at tier 5 any distant PLANT outranked the collect, so the
#: unit walked away and the day's exposure reset at midnight. Measured over 8 geese:
#: 24 collects out of ~136 exposures, $2.1k of a $13k fertilizer bill.
_P_COLLECT = 1
_P_WATER_BONUS = 2
_P_BUILD = 2
_P_WEED = 3
_P_PLANT = 4
_P_DIG = 6
_P_CARRY = 7
_P_DROP = 0
_P_CARRY_LATE = 0  # a carried load is lost at the final step, not dropped at midnight

#: Verbs a unit may be asked to perform. The board is the ONLY place a unit action
#: is chosen, so this is the whole action vocabulary of this agent.
UNIT_VERBS = frozenset({
    "PLANT", "WATER", "HARVEST", "DIG", "DROP", "PICKUP", "PASS",
    "NORTH", "SOUTH", "EAST", "WEST",
    "BUILD_COOP", "BUILD_PASTURE", "PLACE", "FEED", "CARE", "COLLECT_FERTILIZER",
})

#: The engine's animal vocabulary, derived rather than retyped.
SPECIES = tuple(ANIMALS)
STRUCTURE_OF = {animal: data["structure"] for animal, data in ANIMALS.items()}
BUILD_VERB = {animal: ("BUILD_COOP" if data["structure"] == "COOP" else "BUILD_PASTURE")
              for animal, data in ANIMALS.items()}


# --------------------------------------------------------------------------- #
# The plan vector: PA1's tape-derived four-quadrant vector, plus the herd, plus
# PA1's own vector kept verbatim as the control.  (``ANIMAL_FREE_PLAN`` is exactly
# what PA1 shipped: 96 crop tiles, zero animals, no reserved cells.)
# --------------------------------------------------------------------------- #

#: The PA1 gate vector, kept as the CONTROL the PA2 gate is measured against.
#:
#: Its 96 crop tiles and zero animals are exactly what PA1 shipped, so a season of
#: ``DEFAULT_PLAN`` against a season of ``ANIMAL_FREE_PLAN`` is the animal economy's
#: marginal value measured on the same board implementation, with the plan as the
#: only difference.
ANIMAL_FREE_PLAN = {
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
#:   PA2 spends 16 of those cells on structures, so the same mix is scaled by
#:   80/96: WHEAT 28 / STRAWBERRY 23 / CARROT 21 / MELON 8. WHEAT is the one crop
#:   whose job changed rather than shrank: its 28 tiles yield about 22 units a day,
#:   which is the herd's whole feed bill, so the wheat that PA1 sold is now the
#:   wheat the herd eats (and the sell layer reserves it before it sells any).
#: * **animals** - 8 GOOSE, 4 COW, 4 SHEEP = 16 structures, calibrated on paired
#:   seeds (see below): mean margin +$13,122, 12 wins in 12 games, banks
#:   $32.5k-$49.4k against the animal-free plan's $22.4k-$33.8k. Geese take the early
#:   capital because they are cheap, fast (first yield day 4) and deep-market (EGG's
#:   log curve still clears 600 units at $39), while milk and wool saturate on a
#:   linear/quadratic curve - the marginal cow or sheep is worth less than the
#:   marginal goose, so the geese go first and the others take the late capital. The
#:   count is the part that had to be measured: 12 animals are comfortably profitable,
#:   16 is the peak, and 18-20 LOSE money on this board (measured at 18: -$3.6k,
#:   -$2.7k and +$0.3k over three roster splits; at 20: -$1.6k to -$15.6k, and -$22.8k
#:   with a 14-hand roster). More animals saturate the products, eat the wheat the
#:   farm would have sold, and buy labour the crop board needs.
#:   ``buy_days`` is a FLOOR and the pacing comes from the market's own capex order:
#:   the first animals can only be funded after the day-11 quadrant and the day-13
#:   ($4,000) quadrant are covered, which is what a plan cannot see and the runtime
#:   can.  ``calibration`` (from the PA2 gate, paired against ``ANIMAL_FREE_PLAN``,
#:   seeds 0-2 unless noted):
#:
#:   | roster | hands | mean margin | wins |
#:   |---|---|---|---|
#:   | 20 GOOSE | 12 | -$15,628 | 0/6 |
#:   | 12 GOOSE | 12 | -$6,626 | 0/6 |
#:   | 10 GOOSE 4 COW 4 SHEEP | 12 | -$3,558 | 1/6 |
#:   | 8 GOOSE 6 COW 6 SHEEP | 12 | -$1,611 | 4/6 |
#:   | 8 GOOSE 5 COW 5 SHEEP | 12 | +$282 | 3/6 |
#:   | 8 GOOSE 4 COW 4 SHEEP | 13 | +$2,384 | 5/6 |
#:   | 6 GOOSE 2 COW 2 SHEEP | 12 | +$8,000 | 6/6 |
#:   | 8 GOOSE 4 COW 3 SHEEP | 12 | +$11,311 | 6/6 |
#:   | 8 GOOSE 4 COW 4 SHEEP | 12 | +$13,991 | 6/6 |
#:   | 8 GOOSE 4 COW 4 SHEEP | 12 | +$13,122 | 12/12 (seeds 0-5) |
#:   | 8 GOOSE 4 COW 4 SHEEP | 14 | -$22,831 | 0/6 |
#:
#:   The 16-animal row is the default. Two rows above it are the same roster with one
#:   fewer or one more animal, which is why the peak is a plateau and not a spike.
#: * **market** - sell from day 0, no chunk cap (the shed-pressure rule and the
#:   price floor are the throttle), one seed kept per crop as a reserve.
#: * **fertilize_tiles** - 0 everywhere: the herd now produces fertilizer, but
#:   FERTILIZE is PA3's program (and the fertilizer is worth more sold than spread
#:   while no crop is tile-starved for want of a unit).
DEFAULT_PLAN = {
    "schema": "kaggriculture-plan/3",
    "land": {"quadrants": 4, "buy_days": [6, 11, 13]},
    "hands": {"start": 4, "ramp_step": 1, "daily_target": 12},
    "crops": [
        {"crop": "WHEAT", "tiles": 28,
         "plant_days": [0, 4, 8, 12, 16, 20, 24, 28], "fertilize_tiles": 0},
        {"crop": "CARROT", "tiles": 21,
         "plant_days": [0, 3, 6, 9, 12, 15, 18, 21, 24, 27], "fertilize_tiles": 0},
        {"crop": "TOMATO", "tiles": 0, "plant_days": [], "fertilize_tiles": 0},
        {"crop": "STRAWBERRY", "tiles": 23,
         "plant_days": [1, 4, 7, 10], "fertilize_tiles": 0},
        {"crop": "MELON", "tiles": 8, "plant_days": [0, 13], "fertilize_tiles": 0},
    ],
    "animals": [
        {"animal": "GOOSE", "count": 8, "buy_days": [11, 11, 12, 12, 13, 13, 14, 14]},
        {"animal": "COW", "count": 4, "buy_days": [14, 14, 15, 15]},
        {"animal": "SHEEP", "count": 4, "buy_days": [14, 14, 15, 15]},
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

    # -- PA2: the animal economy -------------------------------------------- #

    #: Built-but-empty structures kept ahead of the animals bought. A purchase is
    #: dead capital until a matching structure exists AND a unit has walked both
    #: legs of the pickup/place chain, so the board builds this many ahead of the
    #: buy gate. Calibrated: 0 (build only on demand) delays the first placement by
    #: the walk, 2+ costs two build actions that may never be used if the plan's
    #: cash never arrives.
    structure_lead = 2
    #: Wheat one unit picks up per shed trip. Not a cap of the ENGINE (a worker may
    #: carry any amount); it is the batch that keeps the feed chain from being one
    #: shed round trip per animal. Measured: 1 wheat per trip fed 3-4 animals a day
    #: per unit, 5 feeds 20 animals with 4 units.
    feed_batch = 5
    #: Tier the FEED job runs at, and the reason it is its own tier above the
    #: harvest: only a unit carrying wheat can take a FEED job, so spending the top
    #: tier on it changes nothing for the crop board, while a lower tier measured a
    #: half-fed herd (see the job-priority notes above).
    feed_priority = -1
    #: Units the board will divert into the feed chain at once. The chain is what
    #: starves first (it competes with the harvest tier), so it is capped rather
    #: than open to the whole fleet.
    feed_crew = 4
    #: Units that may run the "animal in the shed -> its structure" chain at once.
    #: The buy gate is paced at ``animals_per_turn``, so this only has to clear the
    #: backlog of a purchase burst.
    place_crew = 2
    #: Days of herd feed the sell layer keeps out of the market. The herd eats 1
    #: wheat per animal per day, so this is the shed reserve that stops a sell from
    #: starving the animals that were just bought (the ``param_agent`` lesson: the
    #: reserve is sized by the HERD, not by the animals unfed right now, because
    #: wheat also has to be in the shed on the day a placement lands).
    feed_days = 1
    #: Animals the market may buy in one turn. Paced, not capped by cash alone:
    #: a burst of five animals is five placements the board has to walk anyway.
    animals_per_turn = 2
    #: Cash the herd may not spend outside a land window: one seed round plus the
    #: day's hands. Inside a land window the WHOLE next quadrant price is protected
    #: instead - see ``animal_floor``.
    animal_buffer = 700.0
    #: Buy the feed shortfall (``BUY_PRODUCT WHEAT``) instead of letting two
    #: midnights pass: 1 wheat at the curve price ($24-30) against an animal that
    #: costs $300-500 and never comes back. Unit actions run BEFORE the market, so
    #: a wheat bought on the turn it is missed is feedable the next turn - inside
    #: the two-midnight window the escape needs.
    buy_feed_when_short = True
    #: Tier the fertilizer collect runs at (see the job-priority notes): the same
    #: tier as care, so the unit that just fed and cared for an animal finishes the
    #: tour instead of being pulled off the tile by a distant planting job.
    collect_priority = 1
    #: Collect the herd's daily fertilizer exposure. On by default: it is 1 unit of
    #: FERTILIZER per surviving animal per day, one action on a tile the feed/care
    #: chain already visits, and FERTILIZER's linear curve is deep enough to take a
    #: herd's whole output.
    collect_fertilizer = True

    FIELDS = ("sell_floor_ratio", "liquidate_day", "shed_pressure", "hires_per_turn",
              "plant_horizon", "plant_grace", "plant_until_hour", "land_margin",
              "land_floor_lead", "land_floor_grace", "land_floor_max_share",
              "seed_budget_share", "carry_from_day", "structure_lead", "feed_batch",
              "feed_priority", "collect_priority", "feed_crew", "place_crew", "feed_days",
              "animals_per_turn", "animal_buffer", "buy_feed_when_short", "collect_fertilizer")

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
    """Which tile grows which crop, and which tile carries which structure.

    ``param_agent.assign_targets`` cannot be reused here: it lays crops out in
    row-major blocks over the tiles unlocked *right now*, so a crop's own tiles move
    when a quadrant unlocks and a plan's per-crop calendar would land on tiles it
    did not plant. This layout is built once from the whole board in quadrant order
    (NW, NE, SW, SE) with ``se_loop.crop_sequence`` round-robining the mix, so every
    crop is spread evenly across all four quadrants and a tile keeps its crop for
    the season, including while its quadrant is still LOCKED.

    PA2 takes the structures out of that block FIRST, and that order is the whole
    point: they come off the *nearest* cells of each quadrant (``designated_tiles``
    is sorted by distance to the shed), which is what makes the feed and care chains
    short enough to run every day, and they leave every remaining cell to the crops.
    A reserved cell is therefore never planted, so a structure can never displace a
    crop even though building one is irreversible. Structures are spread across the
    quadrants in proportion, and each species takes its cells in the plan's own
    animal order - so the geese that are bought first get the NW coops that unlock
    first.
    """

    def __init__(self, plan, policy=None):
        self.policy = policy or DEFAULT_POLICY
        self.plan = plan
        pool = {quadrant: designated_tiles(WORKABLE_PER_QUADRANT, quadrant=quadrant)
                for quadrant in QUADRANTS}
        self.workable = tuple(tile for quadrant in QUADRANTS for tile in pool[quadrant])

        # Structures: the plan's animals, in the plan's order, over the quadrants in
        # board order. The quota is the count split evenly, with the remainder going
        # to the earlier quadrants (a plan with an odd count should still be able to
        # place its first animal in the quadrant that unlocked first).
        species_order = [entry["animal"] for entry in plan["animals"]
                         for _ in range(_as_int(entry["count"]))]
        quota = self._quota(len(species_order))
        structure_tiles = [tile for quadrant in QUADRANTS
                           for tile in pool[quadrant][:quota[quadrant]]]
        self.structure_plan = tuple(zip(structure_tiles, species_order, strict=True))
        self.structure_of = dict(self.structure_plan)
        self.cells_of = {}
        for tile, species in self.structure_plan:
            self.cells_of.setdefault(species, []).append(tile)
        self.cells_of = {species: tuple(tiles) for species, tiles in self.cells_of.items()}
        self.species_order = tuple(
            species for species in SPECIES if self.cells_of.get(species))

        reserved = set(self.structure_of)
        crop_cells = tuple(tile for tile in self.workable if tile not in reserved)
        mix = {entry["crop"]: entry["tiles"] for entry in plan["crops"] if entry["tiles"] > 0}
        wanted = sum(mix.values())
        if wanted > len(crop_cells):
            raise PlanError(
                f"plan asks for {wanted} crop tiles but the board has only "
                f"{len(crop_cells)} workable cells left after "
                f"{len(self.structure_plan)} structures (4 quadrants x "
                f"{WORKABLE_PER_QUADRANT}; the four shed-access tiles are not farmable)"
            )
        sequence = crop_sequence(mix, len(crop_cells))
        #: EVERY workable cell, crop or structure, in board order. The sectors and
        #: the tile budget are read off this, so a reserved cell still has to be
        #: walked past (and weeded) rather than silently dropped from the farm.
        self.plan_tiles = tuple(zip(self.workable, self._crop_or_none(sequence),
                                    strict=True))
        self.crop_of = dict(zip(crop_cells, sequence, strict=True))
        self.tiles_of = {
            entry["crop"]: tuple(t for t in crop_cells
                                 if self.crop_of.get(t) == entry["crop"])
            for entry in plan["crops"]
        }
        self.calendar = {entry["crop"]: tuple(entry["plant_days"]) for entry in plan["crops"]}
        self.quadrant_of = {tile: quadrant_of(*tile) for tile in self.workable}

    @staticmethod
    def _quota(total):
        """Structures per quadrant: even, remainder to the quadrants that unlock first."""
        base, extra = divmod(_as_int(total), len(QUADRANTS))
        return {quadrant: base + (1 if index < extra else 0)
                for index, quadrant in enumerate(QUADRANTS)}

    def _crop_or_none(self, sequence):
        """The crop of every non-reserved cell, in board order, with None reserved."""
        seq = iter(sequence)
        return [None if tile in self.structure_of else next(seq) for tile in self.workable]

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
# The herd: what the structure cells say, from one board.
# --------------------------------------------------------------------------- #

def herd_size(state):
    """Every animal the farm owns: on a tile, in the shed, or in a hand.

    Deliberately layout-free, because the sell layer needs the herd's feed bill
    before it decides what wheat may leave: the reserve has to be sized by the herd
    the farm OWNS (including the animals bought this turn and still in the shed),
    not by the animals unfed right now.
    """
    owned = 0
    for row in state.tiles:
        if not isinstance(row, list):
            continue
        for cell in row:
            if isinstance(cell, dict) and cell.get("animal"):
                owned += 1
    for species in SPECIES:
        owned += max(0, _as_int(state.shed.get(species, 0)))
    for index in range(len(state.positions)):
        carried = state.carried(index)
        for species in SPECIES:
            owned += max(0, _as_int(carried.get(species, 0)))
    return owned


class Herd:
    """The animals, read off the reserved structure cells of one observation.

    Every animal tile appears in exactly ONE of the daily lists, in the chain's own
    order, and that is the point: a unit standing on an animal tile can perform one
    action per turn, so the board has to sequence the animal's day rather than offer
    four jobs that a priority race would reorder. The chain is

      1. ``FEED`` - unfed for two midnights and the animal is gone for good;
      2. the animal's own ``HARVEST`` - its product is capped at ``max_held``, so a
         cycle not collected before the next one is a unit thrown away;
      3. ``CARE`` - banks the +1 the next production day pops;
      4. ``COLLECT_FERTILIZER`` - one unit of exposure per animal per day, and the
         midnight refresh resets it rather than stacking it.

    A fed animal with a product AND no care yet is therefore offered its product
    first and its care on the next turn, from the same unit, with no extra walking.
    """

    __slots__ = ("state", "species_order", "cells_of", "live", "empty", "unbuilt",
                 "weeds", "in_shed", "carried", "unfed", "at_risk", "products",
                 "needs_care", "fertilizer", "placed", "total")

    def __init__(self, layout, state):
        self.state = state
        # ``species_order`` is the PLAN's order (what gets built and placed first);
        # every counter is keyed by the engine's own species so a plan that leaves a
        # species at zero still reads cleanly.
        self.species_order = layout.species_order
        self.cells_of = layout.cells_of
        self.live = dict.fromkeys(SPECIES, 0)
        self.empty = {species: [] for species in SPECIES}
        self.unbuilt = {species: [] for species in SPECIES}
        self.weeds = {species: [] for species in SPECIES}
        self.placed = dict.fromkeys(SPECIES, 0)
        self.in_shed = {species: max(0, _as_int(state.shed.get(species, 0)))
                        for species in SPECIES}
        self.carried = {species: sum(
            max(0, _as_int(state.carried(index).get(species, 0)))
            for index in range(len(state.positions))) for species in SPECIES}
        self.unfed = []
        self.at_risk = 0
        self.products = []
        self.needs_care = []
        self.fertilizer = []
        self.total = 0
        for tile, species in layout.structure_plan:
            cell = state.cell(tile)
            if cell is None:
                self.unbuilt[species].append(tile)
                continue
            if not isinstance(cell, dict):
                continue
            if "animal" in cell:
                if cell.get("animal") != species:
                    continue  # not ours to reason about: never a tile we built
                self.live[species] += 1
                self.placed[species] += 1
                self.total += 1
                if not cell.get("fed_today", False):
                    self.unfed.append(tile)
                    if _as_int(cell.get("consecutive_unfed", 0)) >= 1:
                        self.at_risk += 1
                elif _as_int(cell.get("yield_units", 0)) > 0:
                    self.products.append(tile)
                elif not cell.get("cared_today", False):
                    self.needs_care.append(tile)
                elif cell.get("fertilizer_available", False):
                    self.fertilizer.append(tile)
            elif cell.get("kind") == STRUCTURE_OF[species]:
                self.empty[species].append(tile)
            elif cell.get("kind") == "WEED":
                self.weeds[species].append(tile)

    def bought(self, species):
        """Animals of ``species`` paid for: placed, in a hand, or in the shed."""
        return self.live[species] + self.in_shed[species] + self.carried[species]

    def built(self, species):
        return self.live[species] + len(self.empty[species])

    def placeable(self, species):
        """Animals of ``species`` that are bought, unplaced, and have a place to go."""
        return min(self.in_shed[species], len(self.empty[species]))

    def carried_units(self, species):
        return self.carried[species]


def read_herd(layout, state):
    """``Herd`` from one observation. Never raises."""
    try:
        return Herd(layout, state)
    except Exception:
        return Herd(layout, FarmState({}))


def build_want(herd, policy=DEFAULT_POLICY):
    """Per species, how many more structures to build now.

    A purchase is dead capital until a matching structure exists AND a unit has
    walked the pickup/place chain, so the board keeps ``structure_lead`` built-but-
    empty structures ahead of the animals bought. The count comes from the plan's own
    tile budget, so the lead can never build a structure the plan did not reserve.
    """
    want = {}
    for species in herd.species_order:
        need = policy.structure_lead + herd.bought(species) - herd.built(species)
        want[species] = max(0, min(len(herd.unbuilt[species]), need))
    return want


def feed_shortfall(layout, state, herd, policy=DEFAULT_POLICY):
    """Wheat the farm should buy this turn to keep the herd fed.

    The sell layer already holds ``feed_days`` of the herd back, so a shortfall only
    appears when the shed ran dry anyway (a harvest queued too late, a placement day
    that ate the reserve). It is worth buying: 1 wheat at the curve price is $25 and
    the animal behind it is $300-500 plus every yield it has left in the season.
    """
    if not policy.buy_feed_when_short or herd.total <= 0:
        return 0
    carried = sum(max(0, _as_int(state.carried(index).get("WHEAT", 0)))
                  for index in range(len(state.positions)))
    in_shed = max(0, _as_int(state.shed.get("WHEAT", 0)))
    wanted = max(len(herd.unfed), policy.feed_days * herd.total)
    return max(0, min(wanted - in_shed - carried, policy.feed_batch))


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

    PA2 adds the one product this farm will not sell: WHEAT, minus the herd's feed.
    Every animal eats 1 wheat a day and ``FEED`` reads the WORKER's inventory, so the
    wheat has to be in the shed for a unit to pick it up; selling it was measured to
    starve a whole herd the moment the placement landed. The reserve is
    ``feed_days`` of the herd the farm OWNS (the ``param_agent`` lesson), not of the
    animals unfed right now.
    """
    cap = plan["market"]["sell_chunk_cap"]
    liquidate = (state.day >= policy.liquidate_day
                 or held_units(state) >= policy.shed_pressure)
    reserve = {}
    herd = herd_size(state)
    if herd > 0 and policy.feed_days > 0:
        # Two floors, not one. The herd's day is `feed_days * herd`; the feed chain's
        # day is `feed_crew * feed_batch`, because the chain hands out a batch per
        # unit and every batch has to be IN the shed at the same time. Holding only
        # `herd` units measured a season where four units could not each pick up a
        # batch (the shed held 5 wheat against 8 unfed animals), the harvest that
        # would have refilled it sat in the harvesters' hands until midnight, and the
        # herd starved with 52 wheat on the farm.
        reserve["WHEAT"] = max(policy.feed_days * herd,
                               policy.feed_crew * policy.feed_batch)
    orders = []
    for item in PRODUCTS:
        held = max(0, _as_int(state.shed.get(item, 0))) - reserve.get(item, 0)
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


def animal_floor(plan, state, policy=DEFAULT_POLICY) -> float:
    """Cash the herd may not spend, for the reason the land floor exists.

    ``land_floor`` is capped at half the cash because an unbounded reserve starved
    the seed budget (PA1 measured it). That cap is right for seeds and WRONG here:
    the land buy needs its whole price in hand at once, so an animal bought with the
    money the quadrant needed does not postpone the quadrant, it loses the days the
    quadrant would have produced. Inside the accrual window this floor is the WHOLE
    price plus the margin; outside it, the plain buffer that keeps a seed round and a
    day of hands affordable.

    The one-day slip this accepts is measured, not accidental: against the tape the
    day-13 ($4,000) quadrant lands on day 14, because the geese bought on days 11-12
    are exactly what keeps the cash under the price on day 13. Buying those geese
    four days later instead - its own paired run - turns the +$14.0k margin into
    +$8.2k, so the trade is kept. The plan's buy days are a FLOOR, which is the same
    licence PA1 relies on for its own late buys.
    """
    paid = max(0, len(state.unlocked) - 1)
    buy_days = plan["land"]["buy_days"]
    if paid < len(buy_days) and paid < len(LAND_PRICES):
        target = buy_days[paid]
        if target - policy.land_floor_lead <= state.day <= target + policy.land_floor_grace:
            return float(LAND_PRICES[paid]) + policy.land_margin
    return policy.animal_buffer


def hire_bill(plan, state, policy=DEFAULT_POLICY) -> float:
    """What this turn's hands will cost: reserved before the herd is funded.

    Hires are the residual claim on the cash (PA1's order: land and seeds have hard
    deadlines, labour can come back tomorrow) - but that is an argument about the
    END of the queue, not about the herd eating the hands' money in front of them.
    The ``param_agent`` deadlock was exactly that shape: a farm that could not hire,
    could not earn, and lost the quadrant anyway.
    """
    missing = max(0, hand_target_for_day(plan, state.day) - state.roster)
    slots = min(missing, policy.hires_per_turn)
    return sum(hand_cost(state.hires_today + index) for index in range(slots))


def animal_orders(plan, layout, state, herd, policy=DEFAULT_POLICY, cash=0.0, build=None):
    """This turn's ``BUY_ANIMAL`` orders, and the cash they leave.

    The gates, in the order they are checked, ARE the campaign's funding discipline:

      1. the plan's own ``buy_days`` floor (never earlier), one day per animal;
      2. the animal's cell: an animal whose reserved cell is still LOCKED has nowhere
         to be placed, so buying it would only fill the shed;
      3. a structure: already built and empty, or being built this turn (unit actions
         run before the market, so a structure built this turn is standing when the
         market commits);
      4. the money, net of ``animal_floor`` (the whole next quadrant, inside its
         window) and of this turn's hands (``hire_bill``);
      5. shed room - ``BUY_ANIMAL`` lands in the shed and fails outright when it is
         full, and a full shed also deletes the midnight drop.
    """
    build = build if build is not None else build_want(herd, policy)
    floor = animal_floor(plan, state, policy) + hire_bill(plan, state, policy)
    orders = []
    for entry in plan["animals"]:
        species = entry["animal"]
        cost = ANIMALS[species]["cost"]
        cells = layout.cells_of.get(species, ())
        bought = herd.bought(species)
        while bought < entry["count"] and len(orders) < policy.animals_per_turn:
            if entry["buy_days"][bought] > state.day:
                break
            if bought >= len(cells) or not state.unlocked_at(cells[bought]):
                break
            if not herd.empty[species] and not build.get(species):
                break
            if cash - floor < cost or state.shed_room < 1:
                break
            orders.append(["BUY_ANIMAL", species, 1])
            cash -= cost
            bought += 1
    return orders, cash


def market_orders(plan, layout, state, policy=DEFAULT_POLICY, note=None):
    """Build this turn's market orders. Never more than the engine reads.

    Slot order is processing order, so this is cash order - and in PA2 it is also the
    joint capex order of the whole campaign:

      1. **sells**, because they fund everything that follows them in the queue
         (minus the wheat the herd eats tonight),
      2. **land** once its gated day arrives and the price is in hand,
      3. **seeds**, one order per crop, inside the seed budget,
      4. **the feed shortfall**, when the shed cannot cover the herd - feeding the
         animals already owned outranks buying more of them, and an escaped animal
         never comes back,
      5. **animals**, inside ``animal_floor`` and the hire reserve,
      6. **hires**, to the plan's roster curve, exempt from the land floor.

    Hires come last on purpose: the land and seed slots above them have a hard
    deadline in the season, while labour can make its way back on the next turn -
    so the roster is the residual claim on the cash, not the first claim on it. The
    one thing that is NOT last is the slot budget: this method claims the hire slots
    FIRST and lets the other sections share the rest, because a turn that spends its
    ten orders on sells, land, seeds and animals would otherwise leave the roster
    unfunded without ever looking poor.
    """
    note = note or (lambda *event: None)
    herd = read_herd(layout, state)
    build = build_want(herd, policy)
    orders = []
    cash = state.money

    hire_slots = max(0, min(hand_target_for_day(plan, state.day) - state.roster,
                            policy.hires_per_turn))
    shared = MAX_ORDERS - hire_slots
    for order in sell_orders(plan, state, policy):
        if len(orders) >= shared:
            break
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
    short = feed_shortfall(layout, state, herd, policy)
    animals = min(sum(max(0, entry["count"] - herd.bought(entry["animal"]))
                      for entry in plan["animals"]), policy.animals_per_turn)
    # The seed section stops one slot short of what the feed fallback and the animal
    # slot may claim this turn, so a big planting round cannot spend the herd's
    # orders: seeds are a today problem, the herd is a season-long one.
    seed_ceiling = max(0, shared - animals - (1 if short > 0 else 0))
    for crop, demand in seed_demand(plan, layout, state, policy).items():
        cost = CROPS[crop]["seed"] * demand
        if cost > budget or len(orders) >= seed_ceiling:
            continue
        orders.append(["BUY_SEED", crop, demand])
        budget -= cost
        cash -= cost
        note("seed", state.day, state.step, crop, demand, cost)

    if short > 0 and len(orders) < shared and state.shed_room >= short \
            and cash - policy.animal_buffer >= short * state.price("WHEAT"):
        orders.append(["BUY_PRODUCT", "WHEAT", short])
        cash -= short * state.price("WHEAT")
        note("feed_buy", state.day, state.step, short, state.price("WHEAT"))

    if len(orders) < shared:
        buys, cash = animal_orders(plan, layout, state, herd, policy, cash, build)
        for order in buys:
            orders.append(order)
            note("animal", state.day, state.step, order[1], ANIMALS[order[1]]["cost"])

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

def tile_job(cell, crop, day, seeds, hour, plantable, policy=DEFAULT_POLICY,
             structure=None, build=False):
    """The job this tile offers right now, or None.

    Returns ``(kind, priority, payload, needs)``: ``needs`` is the item the unit has
    to be CARRYING for the job to be its own (``FEED`` needs a WHEAT in the worker's
    hand, ``PLACE`` needs the animal itself), and the crop jobs never need anything.

    ``seeds`` is the *remaining* seed budget for that crop this turn, decremented
    as PLANT actions are emitted. That is not bookkeeping for its own sake: the
    engine voids every PLANT of a crop whose demand exceeds its stock, so two units
    sent to plant two tiles with one seed plant NOTHING.

    ``hour`` gates planting: a plant needs a WATER the same day (``_new_plant``
    starts the weed clock at 1 and the midnight refresh weeds anything it finds
    unwatered), so a planting in the last hour of the day is a seed thrown away.

    ``structure`` is the species a reserved cell belongs to (``None`` for a crop
    cell) and ``build`` says whether this cell is inside the build lead. A reserved
    cell offers, in this order: BUILD if it is inside the lead and empty, DIG if a
    weed sits on it (a build on a weed is a no-op, and the cell is irreversible -
    so it is cleared rather than abandoned), PLACE once the structure stands, and
    then the animal's own day.

    The animal chain is the part that has to be a CHAIN rather than four competing
    jobs: one unit can do one action per turn on the tile it stands on, so the tile
    offers the next thing due, and the unit that just fed the animal is the unit that
    harvests its product and cares for it on the following turns. Feeding comes
    first because it is the only permanent loss (two unfed midnights and the animal
    escapes); the product is capped at ``max_held`` and waits one turn.
    """
    if structure is not None:
        expected = STRUCTURE_OF[structure]
        if cell is None:
            return (BUILD_VERB[structure], _P_BUILD, structure, None) if build else None
        if not isinstance(cell, dict):
            return None  # LOCKED, or a shape we do not understand: never ours
        if "animal" in cell:
            if cell.get("animal") != structure:
                return None
            if not cell.get("fed_today", False):
                # Two consecutive unfed midnights and the animal is gone for good.
                return ("FEED", policy.feed_priority, None, "WHEAT")
            if not cell.get("cared_today", False):
                # Care BEFORE the product on purpose: a tile holding eggs keeps
                # offering its HARVEST (tier 0) for as long as ``yield_units > 0``,
                # so a chain that harvests first never reaches care at all - measured
                # over a season, 2 cares a day for 8 geese the chain could carry, and
                # the care bank was lost on most production days.
                return ("CARE", _P_CARE, None, None)
            if _as_int(cell.get("yield_units", 0)) > 0:
                return ("HARVEST", _P_HARVEST, None, None)
            if cell.get("fertilizer_available", False) and policy.collect_fertilizer:
                return ("COLLECT_FERTILIZER", policy.collect_priority, None, None)
            return None
        if cell.get("kind") == expected:
            return ("PLACE", _P_PLACE, structure, structure)
        if cell.get("kind") == "WEED":
            return ("DIG", _P_WEED, None, None)
        return None
    if cell is None:
        if seeds <= 0 or hour > policy.plant_until_hour:
            return None
        if not plantable:
            return None
        if day + harvest_maturity_day(crop) > SEASON_LAST_DAY:
            return None  # it cannot pay for its seed before the season ends
        return ("PLANT", _P_PLANT, crop, None)
    if not isinstance(cell, dict):
        return None  # LOCKED, or a shape we do not understand: never ours
    kind = cell.get("kind")
    if kind == "WEED":
        return ("DIG", _P_WEED, None, None)
    if kind == "PLANT":
        here = cell.get("crop")
        if here not in CROPS:
            return None
        boost = water_boosts_harvest(cell, day)
        if harvest_due(cell, day, here) and not boost:
            return ("HARVEST", _P_HARVEST, None, None)
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
            return ("WATER", _P_WATER_URGENT if urgent else _P_WATER_BONUS, None, None)
        data = CROPS[here]
        age = day - _as_int(cell.get("planted_day", day), day)
        if not data["ongoing"] and _as_int(cell.get("yield_units", 0)) <= 0 \
                and age > data["max_yield_day"]:
            return ("DIG", _P_DIG, None, None)  # one-shot, spent: reclaim the tile
        return None
    return None  # an empty structure nobody reserved is not ours


def board_jobs(layout, state, seeds, policy=DEFAULT_POLICY, herd=None):
    """Every job on the board this turn: ``[(priority, tile, kind, payload, needs)]``."""
    herd = read_herd(layout, state) if herd is None else herd
    build_tiles = set()
    for species, count in build_want(herd, policy).items():
        build_tiles.update(herd.unbuilt[species][:count])
    jobs = []
    for tile, crop in layout.plan_tiles:
        if not state.unlocked_at(tile):
            continue  # a LOCKED tile silently voided a tile op: never target one
        if crop is None:
            job = tile_job(state.cell(tile), None, state.day, 0, state.hour, False, policy,
                           structure=layout.structure_of[tile], build=tile in build_tiles)
        else:
            job = tile_job(state.cell(tile), crop, state.day, seeds.get(crop, 0),
                           state.hour, layout.plantable(crop, state.day), policy)
        if job is None:
            continue
        kind, priority, payload, needs = job
        jobs.append((priority, tile, kind, payload, needs))
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


def _shed_distance(pos, sheds):
    return min(_distance(pos, tile) for tile in sheds)


def fetch_plan(state, herd, policy=DEFAULT_POLICY):
    """Which units walk to the shed this turn, and what they carry back.

    Two chains need a shed trip and neither of them is a tile job, because the
    engine reads the WORKER's inventory rather than the tile's:

      * ``FEED`` needs wheat in the worker's hand, so the crew is sized from the
        herd's unfed count and the batch size (and shrinks by itself as the day's
        feeding progresses, because a unit holding wheat already counts as a
        carrier);
      * ``PLACE`` needs the animal itself - ``BUY_ANIMAL`` only puts it in the shed -
        so an animal that has a place to stand gets a unit to fetch it.

    Units are chosen nearest-to-the-shed first, in index order for ties, and a unit
    already carrying a fed batch or an animal is not re-tasked.
    """
    positions = state.positions
    sheds = shed_access_tiles()
    order = sorted(range(len(positions)), key=lambda index: (
        -1 if positions[index] is None else _shed_distance(positions[index], sheds), index))
    # A unit already holding wheat or an animal is mid-chain, not free labour: the
    # first version re-tasked the holder with a second batch and the loop never fed
    # anything (the shed tile is distance 0, so the pickup outranked the walk to the
    # animal it was already carrying the wheat for).
    busy = {index for index in range(len(positions))
            if _as_int(state.carried(index).get("WHEAT", 0)) > 0
            or any(_as_int(state.carried(index).get(species, 0)) > 0 for species in SPECIES)}
    plan = {}
    # Size the trips by the wheat the herd still NEEDS, measured in units: a unit
    # holding 2 wheat covers 2 animals, not a batch. Counting carriers instead of
    # units measured a season of dribbling - one carrier with 2 wheat "covered" 3
    # unfed animals, no trip was ever sent, and the herd ate half its days. The
    # wheat in a harvester's hand counts, because a carrier always feeds before it
    # farms again (``feed_priority``); the trip still takes a FULL batch, because
    # the wheat not fed tonight is dropped back in the shed at midnight for free.
    holding = sum(max(0, _as_int(state.carried(index).get("WHEAT", 0)))
                  for index in range(len(positions)))
    carriers = sum(1 for index in range(len(positions))
                   if _as_int(state.carried(index).get("WHEAT", 0)) > 0)
    missing = max(0, len(herd.unfed) - holding)
    trips = min(-(-missing // max(1, policy.feed_batch)),
                max(0, policy.feed_crew - carriers))
    for _ in range(trips):
        index = next((i for i in order if i not in busy), None)
        if index is None:
            break
        plan[index] = ("WHEAT", policy.feed_batch)
        busy.add(index)
        missing = max(0, missing - policy.feed_batch)
    budget = policy.place_crew
    for species in herd.species_order:
        pending = herd.placeable(species)
        while pending > 0 and budget > 0:
            index = next((i for i in order if i not in busy
                          and _as_int(state.carried(i).get(species, 0)) <= 0), None)
            if index is None:
                break
            plan[index] = (species, 1)
            busy.add(index)
            pending -= 1
            budget -= 1
    return plan


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
    herd = read_herd(layout, state)
    jobs = board_jobs(layout, state, seeds, policy, herd)
    #: Items some unclaimed job is waiting for. A load of one of these is on its way
    #: somewhere, not on its way to the market: a goose in a hand is a PLACE in
    #: progress, and wheat in a hand is the herd's dinner. Without this the drop
    #: fallback (priority 0 from the shed tile) unloaded every goose the moment a
    #: unit picked it up - measured, 641 pickups and 1 placement in a season.
    wanted = {needs for *_, needs in jobs if needs is not None}
    sheds = shed_access_tiles()
    claimed = set()
    actions = []
    unit_sectors = sectors(layout, state, state.positions)
    fetches = fetch_plan(state, herd, policy)
    for index, pos in enumerate(state.positions):
        if pos is None:
            actions.append(["PASS"])
            continue
        carrying = state.carried(index)
        mine = set(unit_sectors[index])
        best = _best_job(pos, jobs, claimed, seeds, mine, carrying)
        # The carry chains are GLOBAL, not sector-bound. A unit holding wheat is one
        # step away from feeding an animal in another corner, and holding the job
        # inside its own sector is how the herd went unfed for two thirds of its
        # turns with 4-10 wheat in that unit's hand (measured: 592 of 713 such turns
        # ended without a FEED). Only ``needs`` jobs cross a sector, so the crop
        # board keeps PA1's no-roaming rule exactly as it was.
        chain = _best_job(pos, jobs, claimed, seeds, None, carrying, only_needs=True)
        if chain is not None and (best is None or chain[0] < best[0]):
            best = chain
        if best is None:
            best = _best_job(pos, jobs, claimed, seeds, None, carrying)
        fetch = fetches.get(index)
        if fetch is not None:
            targets = [t for t in sheds if t not in claimed]
            if targets:
                shed = min(targets, key=lambda t: (_distance(pos, t), t[1], t[0]))
                key = _sort_key(pos, _P_FEED, shed)
                if best is None or key < best[0]:
                    best = (key, shed, "PICKUP", fetch)
        carried = state.carried_units(index)
        load = {item for item, count in carrying.items() if _as_int(count) > 0}
        if carried > 0 and not (load & wanted) \
                and (state.shed_room >= carried or state.day >= policy.carry_from_day):
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
            action = _verb_for(kind, payload)
            if kind == "PLANT" and payload in seeds:
                seeds[payload] -= 1
            note("act", index, tuple(action), target, state.day, state.step)
            if kind in ("PLANT", "WATER", "HARVEST", "DIG"):
                note(kind.lower(), target, payload, state.day)
            elif kind in ("FEED", "CARE", "PLACE", "COLLECT_FERTILIZER",
                          "BUILD_COOP", "BUILD_PASTURE"):
                note(kind.lower(), target, state.day)
            elif kind == "PICKUP":
                note("pickup", target, payload, state.day)
            elif kind == "DROP":
                note("drop", target, carried, state.day)
            elif kind == "CARRY":
                note("carry", target, state.day)
        actions.append(action)
    return actions[0], actions[1:]


def _verb_for(kind, payload):
    """The engine action for a job. Payload-less verbs are a silent no-op.

    ``PLANT`` and ``PICKUP`` read ``action[1]`` (the crop / the item) and
    ``PICKUP`` reads ``action[2]`` (the count), and ``PLACE`` reads ``action[1]``
    (the animal). A verb alone plants nothing, places nothing and picks up nothing.
    """
    if kind == "PLANT":
        return ["PLANT", payload]
    if kind == "PLACE":
        return ["PLACE", payload]
    if kind == "PICKUP":
        return ["PICKUP", payload[0], payload[1]]
    return [kind]


def _best_job(pos, jobs, claimed, seeds, tiles, carrying=None, only_needs=False):
    """The cheapest-key unclaimed job for a unit, optionally inside ``tiles``.

    ``carrying`` is the unit's inventory, and a job with a ``needs`` item is only
    this unit's job when the unit holds that item: that is the whole carry chain
    (a FEED without wheat is a silent no-op, and a PLACE of an animal the unit is
    not holding is one). ``only_needs`` restricts the scan to those chained jobs,
    which is how the board lets a carrier cross into another unit's sector without
    reopening the board-wide auction that PA1's sectors exist to stop.
    """
    best = None
    carried = carrying or {}
    for priority, tile, kind, payload, needs in jobs:
        if tile in claimed or (tiles is not None and tile not in tiles):
            continue
        if only_needs and needs is None:
            continue
        if needs is not None and _as_int(carried.get(needs, 0)) <= 0:
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
    ``sell`` / ``land`` / ``seed`` / ``hire`` / ``animal`` / ``feed_buy`` / ``act`` /
    ``plant`` / ``water`` / ``harvest`` / ``dig`` / ``drop`` / ``carry`` / ``pickup`` /
    ``feed`` / ``care`` / ``place`` / ``build_coop`` / ``build_pasture`` /
    ``collect_fertilizer`` / ``crash`` events.
    """
    policy_agent = PolicyAgent(plan=plan, policy=policy, enabled=enabled, trace=trace)

    def agent(obs):
        return policy_agent(obs)

    return agent


#: Default plan, default policy: what the PA1 gate is measured on.
agent = make_agent()
