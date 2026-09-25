"""Roster-owner agent (stage 1): own the hands, the hires and the land.

What this is
------------
`odd/tasks/roster-owner-agent.md` (R1). Three refutations converged on the same
wall: every overlay on the saturated V8.1 tape fails because the tape owns its
12 hands and its schedule. This module keeps the tape's proven FARMER route and
market (the core economy) but takes ownership of everything the tape used to
own *around* the farmer:

* **hands** - every hand action comes from the `se_loop` job board
  (plant/water/harvest/dig/drop/carry), never from the tape's hand actions;
* **hires** - the daily roster follows a plan-vector hand curve (default: the
  tape's own measured curve), hired early in the day at the base of the
  Fibonacci ladder, because the hires are now ours and the tail is expensive;
* **land** - the plan's buy days, with the `se_loop` funding gate (net of the
  tape's same-turn spend, reserve = seed bill), extended to the SE quadrant.

Why the board covers the WHOLE farm, not just the new land
----------------------------------------------------------
The feature doc's stage-1 bullet reads "our hands plant+water the new land with
the `se_loop` mix machinery". Taken literally - all hands ours, the board
restricted to the newly bought quadrant - the tape's original 62-tile farm is
never worked: the tape's farmer plants ~0 tiles a season (measured: 0 PLANT
actions on seed 0; the tape's 235 PLANT actions are all hand actions), and a
farmer-only season banks exactly **$0** (measured, seeds 0-3). So the only
reading under which "the roster-owner economy lands" is the documented one from
the feature doc's own lever: *driving the tape's own hand indices* to service
the farm. The board is therefore the `se_loop` board with its candidate tile set
extended from the SE plan to every workable tile of the unlocked farm, and the
crop mix is the plan's own staple mix. Stage 3 is where the mix stops being
frozen.

The reuse contract (no fork of the board)
-----------------------------------------
`se_loop` is the tested reference and is NOT modified. This module subclasses
`SELoopOverlay` and reuses its board verbatim: `_tile_job`, `_choose`,
`_drive_hands`, `_wanted_seeds`/`_append_seed_orders`, `_spare_seeds`,
`_tape_spend`, plus the pure helpers (`designated_tiles`, `crop_sequence`,
`seed_cost`, `needs_water`, `harvest_due`, `step_toward`, `fib_hire_cost`). The
three couplings the parent warned about are handled by wrapping, not forking:

1. `se_loop`'s plan is SE-only.  ``self.plan`` is rebuilt here over the whole
   board (``_whole_farm_tiles``) with the same ``crop_sequence`` machinery.
2. `se_loop`'s ``_drive_hands`` keeps the tape's hands at indices it does not
   own and rewrites only ``self.owned``. We own every hand index, and hand the
   driver a PASS-filled hand list sized to the real roster so every index is
   rewritten.
3. `se_loop`'s ``_wanted_seeds`` counts LOCKED tiles as plantable (harmless for
   a quadrant the overlay only reaches after unlocking it). For a whole-farm
   plan that would buy seeds for two locked quadrants on day 0, so it is
   overridden to count only unlocked tiles.

Graceful degradation
--------------------
With ``enabled=False`` (the ``se_loop`` convention) the returned action is the
tape's own dict, untouched - byte-identical to the incumbent. This is the useful
degradation: farmer-only is measured at $0, so a board that cannot run must fall
back to the *tape*, not to an idle roster.

Engine facts pinned here (see docs/engine-rule-sheet.md)
--------------------------------------------------------
* PLANT voids for a crop when the turn's total demand exceeds the seeds held, so
  the board only plants on ``_spare_seeds`` budget.
* Watering on the planting day is mandatory or the crop is a WEED at midnight;
  the ``se_loop`` ``needs_water`` rule already encodes that.
* The 10-order cap is shared with the farmer's market orders; our orders are
  appended after the tape's, so budget pressure drops ours, never the tape's.
"""

from __future__ import annotations

from pathlib import Path

from kaggle_environments.envs.kaggriculture.kaggriculture import CROPS, LAND_PRICES

from kaggriculture.agents import se_loop as sl
from kaggriculture.loading import current_step, load_agent

_BASELINE = Path(__file__).with_name("baseline_v8_1.py")

BOARD = sl.BOARD
TURNS_PER_DAY = sl.TURNS_PER_DAY
MAX_ORDERS = sl.MAX_ORDERS
SEASON_DAYS = 30
SE_PRICE = LAND_PRICES[2]  # the third paid quadrant is always SE

#: The tape's own measured daily hand curve (seed 0, ``baseline_v8_1``): the
#: roster the plan vector expresses for ``hands``. The tape reaches 12 hands
#: from day 18 and never more; hiring is a daily cost because the roster resets
#: at midnight. Indices are days, so ``curve[day]`` is that day's target.
TAPE_HAND_CURVE = (
    5, 4, 4, 5, 4, 5, 8, 8, 10, 9,
    11, 11, 9, 10, 10, 11, 11, 11, 12, 12,
    12, 12, 12, 12, 12, 11, 11, 11, 11, 9,
)

#: The plan's crop mix, frozen (stage 3 owns the mix). WHEAT is the tape's feed
#: and volume crop (planted almost every day of the tape's season); CARROT is the
#: cheap fast second staple whose market stays under-supplied all season. The
#: counts are the tile block the board keeps planted across the whole farm.
DEFAULT_MIX = {"WHEAT": 44, "CARROT": 20}

#: Plan land calendar: the tape buys NE on day 6 and SW on day 11; the SE
#: extension is the same day-11 gate, one turn after SW resolves so the "three
#: unlocked quadrants" condition of the proven gate is true.
PLAN_LAND_DAYS = (6, 11, 11)

#: How many hands we hire per turn. The roster resets nightly, so the daily
#: target is reached by hiring a few at a time from hour 0; spreading the
#: orders also keeps room under the shared 10-order cap. Hiring from the base
#: of the ladder (hour 0, ``hires_today`` starting at 0 because the tape's own
#: HIRE orders are removed) is the whole point: the appended-hand tax was
#: fib(10..13), and the base is fib(0..4).
HIRES_PER_TURN = 3


def _whole_farm_tiles(board=BOARD):
    """Every workable tile on the board, nearest-to-shed first per quadrant.

    Reuses ``se_loop.designated_tiles`` for the geometry (distance, then row,
    then column; shed-access tiles excluded) and concatenates the quadrants in
    a fixed order so a tile keeps its crop assignment across turns.
    """
    tiles = []
    for quadrant in ("NW", "NE", "SW", "SE"):
        tiles.extend(sl.designated_tiles(25, board, quadrant))
    return tiles


WHOLE_FARM_TILES = _whole_farm_tiles()


class RosterOwner(sl.SELoopOverlay):
    """Own the farmers' hands, hires and land; keep the tape's farmer and market.

    The base class is the tested `se_loop` board. Everything that differs from
    the append-only SE overlay is a state/ownership decision, so the board
    methods themselves are inherited unchanged.
    """

    def __init__(
        self,
        mix=None,
        hand_curve=None,
        land_days=PLAN_LAND_DAYS,
        land_reserve=None,
        hire_per_turn=HIRES_PER_TURN,
        own_land=True,
        own_seeds=True,
        board=True,
        **kwargs,
    ):
        # Seed mix and hire hours are configured before the base initializes:
        # the base validates the mix and builds its (SE) plan, which we replace.
        kwargs.setdefault("base_agent", load_agent(str(_BASELINE)))
        kwargs.setdefault("enabled", True)
        kwargs.setdefault("seed_mix", DEFAULT_MIX if mix is None else mix)
        kwargs.setdefault("hire_from_hour", 0)
        kwargs.setdefault("hire_until_hour", 20)
        kwargs.setdefault("plant_until_hour", 22)
        kwargs.setdefault("cash_floor", 400)
        super().__init__(**kwargs)

        self.mix = {crop: int(n) for crop, n in (DEFAULT_MIX if mix is None else mix).items()
                    if int(n) > 0}
        self.seed_cost = sl.seed_cost(self.mix)
        self.land_reserve = self.seed_cost if land_reserve is None else land_reserve
        self.hand_curve = tuple(hand_curve if hand_curve is not None else TAPE_HAND_CURVE)
        self.land_days = tuple(land_days)
        self.hire_per_turn = int(hire_per_turn)
        self.own_land = bool(own_land)
        self.own_seeds = bool(own_seeds)
        self.board_enabled = bool(board)

        # The whole-farm plan: the same ``crop_sequence`` machinery over the
        # whole board instead of one quadrant. Static, so a tile keeps its crop.
        sequence = sl.crop_sequence(self.mix, len(WHOLE_FARM_TILES))
        self.plan = list(zip(WHOLE_FARM_TILES, sequence, strict=True))
        self.plan_map = dict(self.plan)
        self.plan_tiles = [tile for tile, _ in self.plan]
        self._unlocked = {"NW"}

    # ---- per-day bookkeeping ----------------------------------------------

    def _target_hands(self, day):
        """The plan's daily roster target, clamped to the curve's last day."""
        if not self.hand_curve:
            return 0
        return self.hand_curve[min(max(day, 0), len(self.hand_curve) - 1)]

    def _reset_day(self):
        # We own the whole roster, so there is no appended-only claiming here;
        # ``owned`` is recomputed from the observed roster every turn.
        self.owned = set()

    # ---- seed demand, restricted to unlocked land --------------------------

    def _wanted_seeds(self, tiles):
        """Per crop: seeds needed to fill unlocked, workable plan tiles only.

        ``se_loop``'s version counts LOCKED tiles (correct for a quadrant the
        overlay cannot reach before unlocking). A whole-farm plan must not buy
        seeds for the two locked quadrants on day 0.
        """
        want = dict.fromkeys(self.mix, 0)
        for tile, crop in self.plan:
            if sl.quadrant_of(tile[0], tile[1]) not in self._unlocked:
                continue
            try:
                cell = tiles[tile[1]][tile[0]]
            except (IndexError, TypeError):
                continue
            if cell is None or (isinstance(cell, dict) and cell.get("kind") == "WEED"):
                want[crop] = want.get(crop, 0) + 1
        return {crop: min(self.mix.get(crop, 0), n) for crop, n in want.items()}

    # ---- hires ------------------------------------------------------------

    def _hire_curve(self, market, farm, day, cash):
        """Append this turn's HIREs, at most ``hire_per_turn``, from the ladder base.

        No pending/resolve dance is needed: we own every hand index, so the
        roster observed next turn already contains our hires and the target is
        recomputed each turn. A hire that did not land (insufficient cash) is
        simply retried, because the target is unchanged.
        """
        target = self._target_hands(day)
        roster = len(farm.get("hands") or [])
        missing = target - roster
        if missing <= 0:
            return
        hires_today = sl._as_int(farm.get("hires_today", 0))
        wallet = cash
        count = 0
        for _ in range(min(missing, self.hire_per_turn)):
            if len(market) >= MAX_ORDERS:
                break
            cost = sl.fib_hire_cost(hires_today + count)
            if wallet - cost < 0:
                break
            wallet -= cost
            count += 1
        for _ in range(count):
            market.append(["HIRE"])
        if count:
            self._note("hire", day, count)

    # ---- land -------------------------------------------------------------

    def _append_land(self, market, day, quads, cash):
        """Append one gated BUY_LAND, following the plan's quadrant buy days."""
        paid = len(quads) - 1  # NW is free; every other quadrant is paid
        if paid >= len(LAND_PRICES) or paid >= len(self.land_days):
            return False
        if day < self.land_days[paid] or len(market) >= MAX_ORDERS:
            return False
        price = LAND_PRICES[paid]
        if cash < price + self.land_reserve:
            return False
        market.append(["BUY_LAND"])
        self._note("land", day, paid, price)
        return True

    def _append_se_extension(self, market, day, quads, cash):
        """The proven SE gate: exactly three quadrants, day >= 11, funded."""
        if len(quads) != 3 or day < self.land_days[2] or len(market) >= MAX_ORDERS:
            return False
        if cash < SE_PRICE + self.land_reserve:
            return False
        market.append(["BUY_LAND"])
        self._note("land", day, 2, SE_PRICE)
        return True

    # ---- the transform ----------------------------------------------------

    def augment(self, obs, action):
        step = current_step(obs)
        farm = sl._farm_of(obs)
        if step % TURNS_PER_DAY == 0:
            self._reset_day()

        day = sl._as_int(obs.get("day"), step // TURNS_PER_DAY)
        hour = sl._as_int(obs.get("hour"), step % TURNS_PER_DAY)
        money = sl._as_float(farm.get("money", 0))
        quads = list(farm.get("unlocked_quadrants") or [])
        self._unlocked = set(quads)
        seeds = dict(sl._private_of(obs).get("seeds") or {})
        tiles = farm.get("tiles") or []
        prices = ((obs.get("market") or {}).get("prices") or {})
        tape_market = action.get("market") or []

        # We own the roster, so the tape's HIRE orders are removed and replaced
        # by ours. We own planting too, so the tape's BUY_SEED orders for crops
        # the board does NOT grow are removed as well: the tape bought
        # MELON/STRAWBERRY seeds for hand plantings that no longer happen, and
        # keeping those burned ~$1k early for zero yield. Seed orders for crops
        # INSIDE the mix are kept - the tape's wheat re-stocking is a useful
        # supply for the board, and `_append_seed_orders` only tops up the gap.
        # When we own land, its BUY_LAND orders are removed and our gated ones
        # take over; otherwise the tape executes the plan's NE/SW buys and we
        # only add the gated SE extension.
        market = []
        for order in tape_market:
            if not isinstance(order, (list, tuple)) or not order:
                continue
            op = order[0]
            if op == "HIRE":
                continue
            if (self.own_seeds and op == "BUY_SEED" and len(order) >= 2
                    and order[1] not in self.mix):
                continue
            if self.own_land and op == "BUY_LAND":
                continue
            market.append(list(order))

        spend = self._tape_spend(market, farm, prices, quads)
        cash = money - spend

        # 1. Land first: it is the lever the tape's own cliff order depends on.
        if self.own_land and self._append_land(market, day, quads, cash):
            cash -= LAND_PRICES[max(0, len(quads) - 1)]
        if self._append_se_extension(market, day, quads, cash):
            cash -= SE_PRICE

        # 2. Seeds for the unlocked part of the plan, one order per crop.
        cash -= self._append_seed_orders(market, tiles, seeds, cash)

        # 3. Our own hires, from the base of the day's Fibonacci ladder.
        self._hire_curve(market, farm, day, cash)

        # 4. Every hand is ours: drive the whole roster from the board.
        roster = list(farm.get("hands") or [])
        self.owned = set(range(len(roster)))
        drive = {"farmer": action.get("farmer"),
                 "hands": [["PASS"] for _ in roster],
                 "market": market}
        hands_out = self._drive_hands(obs, drive, farm, seeds, day, hour)
        return {"farmer": action.get("farmer"), "hands": hands_out,
                "market": market[:MAX_ORDERS]}

    def __call__(self, obs):
        try:
            action = self.base(obs)
        except Exception:
            action = sl._fallback_action(obs)
        if not isinstance(action, dict):
            return action
        if not self.enabled or not self.board_enabled:
            # The useful degradation: the untouched tape (farmer-only banks $0).
            return action
        try:
            return self.augment(obs, action)
        except Exception:
            self.crashes += 1
            self._note("crash")
            return action


def make_agent(
    mix=None,
    hand_curve=None,
    land_days=PLAN_LAND_DAYS,
    land_reserve=None,
    hire_per_turn=HIRES_PER_TURN,
    own_land=True,
    own_seeds=True,
    board=True,
    enabled=True,
    base_agent=None,
    trace=None,
):
    """Build a one-argument ``agent(obs)`` owning hands, hires and land.

    The returned callable is a plain function on purpose: the Kaggle runner
    truncates the argument list with ``__code__.co_argcount``, so a callable
    instance would be handed ``(observation, configuration)``.
    """
    if base_agent is None:
        base_agent = load_agent(str(_BASELINE))
    owner = RosterOwner(
        mix=mix,
        hand_curve=hand_curve,
        land_days=land_days,
        land_reserve=land_reserve,
        hire_per_turn=hire_per_turn,
        own_land=own_land,
        own_seeds=own_seeds,
        board=board,
        enabled=enabled,
        base_agent=base_agent,
        trace=trace,
    )
    return _agent_for(owner)


def _agent_for(owner):
    def agent(obs):
        return owner(obs)

    return agent


#: Default configuration of the R1 experiment.
agent = make_agent()


# ``CROPS`` is imported for the mix validation the base class already performs;
# re-exported so tests can assert the mix names real crops without a second import.
__all__ = ["RosterOwner", "make_agent", "agent", "TAPE_HAND_CURVE", "DEFAULT_MIX",
           "PLAN_LAND_DAYS", "WHOLE_FARM_TILES", "CROPS", "SE_PRICE"]
