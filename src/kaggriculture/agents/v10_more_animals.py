"""V10 wave 9: MORE animals, paid for with converted crop tiles.

The lever
---------
The loss diagnostic (`scripts/corpus_within_game.py --vs 60`, 120 games, 34
losses) measured that our losses are bimodal: 24% (8/34) are blowouts over
$20,000, and in the five worst the opponent holds **22-26 animals** while ours
reads **16-17 in every single one of them**. Their `hires` is nearly identical
(290 vs our 284) and our `peak_crop_tiles` is sometimes *higher*, so the gap is
neither labour nor crop area: **it is animal count**, and the fact that ours is
the same number in every game is what a structural cap looks like.

The cap is also measured. The tape ends with 17 structures and 58 crop tiles on
75 cells (NW + NE + SW), so it has **zero free structure slots**. A 4th quadrant
would add cells, but the bounded SE overlay was already refuted ($7,130 of land
plus Fibonacci labour for $392 of wheat: 4 of 25 cells amortize nothing). The
remaining route is to make room WITHIN the three quadrants.

This is NOT the earlier roster-mix experiment (cow -$9,072, goose -$7,432,
sheep +$1,933 at a 50% win rate), which swapped the animal MIX at a fixed slot
count and was refuted. This module changes the COUNT: it converts crop tiles
into structures.

What it does (and only this)
----------------------------
1. Designates up to ``convert_tiles`` of the tape's own crop tiles. A tile is
   claimed at the moment the tape tries to ``PLANT`` it (so the tile is empty:
   nothing is dug up and no harvest is thrown away), on or after
   ``convert_from_day`` and only once the tape owns all three quadrants, so the
   overlay never competes with the tape's own land purchases (steps 151 and
   266, both measured). From that turn on, the tape's ``PLANT``/``WATER``/
   ``HARVEST`` at that tile is replaced by ``PASS``: exactly the three verbs the
   task names, exactly the tiles that were converted.
2. Builds the species' structure on each converted tile and buys and places one
   animal per structure. **The engine holds exactly ONE animal per structure
   tile** (``_new_animal`` replaces the tile dict, `kaggriculture.py:229-241`;
   ``PLACE`` requires an unoccupied matching structure, `:377-395`), so N
   converted tiles host N animals, not N/2. See "One animal per tile" below.
3. Feeds them, harvests their product and collects their fertilizer with its own
   hired hands, using the same carry-chain discipline as
   `param_agent`: ``FEED`` consumes a WHEAT the WORKER carries
   (`kaggriculture.py:503-512`), so a worker without wheat is sent to a shed
   access tile for a ``PICKUP`` first. The overlay also tops the shed up with
   ``BUY_PRODUCT WHEAT`` so the extra animals do not eat the tape's own feed
   budget.
4. The species is a parameter and the measurement runs at least COW and GOOSE.

The tape itself is untouched outside the converted tiles and the appended market
orders: the farmer action and every non-owned hand are passed through unchanged,
and the overlay appends its own orders only while the tape's turn has fewer than
the engine's 10 order slots (extras are dropped, `:551-563`). With
``convert_tiles=0`` the overlay is a full pass-through and reproduces the bare
tape byte for byte (``tests/test_v10_more_animals.py``).

One animal per tile (the task's "convert_tiles / 2" is wrong)
-------------------------------------------------------------
The brief says "``convert_tiles / 2`` extra animals if each structure holds one
animal". Those two clauses contradict each other, and the engine decides:
``_new_animal`` stores a single ``"animal"`` field on the tile and ``PLACE``
refuses a tile that already has one, so a structure tile holds **one** animal.
Six converted tiles therefore host six animals. This module implements the
engine's arithmetic (one animal per converted tile = one per structure) and the
report states it explicitly.

Why the overlay hires its own hands instead of reusing the tape's
-----------------------------------------------------------------
The tape is not idle: 7.4-8.2% of worker turns are ``PASS`` (measured over six
seed-0 mirrors), and its routes are hand-index positional. Rewriting tape hand
actions to do animal work would move the tape's own schedule, which is exactly
the perturbation the roster swaps died on. Instead the overlay appends its own
``HIRE`` orders *after* the tape's, at ``hire_from_hour`` (default 2), which is
at or after the tape's last hire of the day on 29 of 30 days (measured: the tape
hires at hour 1-2, with a single hour-4 hire on day 8), so the extra hands land
at the tail of the roster and never take a tape hand index.

Engine facts this module depends on (all verified in the source)
----------------------------------------------------------------
* One animal per structure tile; an animal cannot be removed (``DIG`` returns
  early for a tile with an animal, `:484-490`).
* ``FEED`` consumes a carried WHEAT; two consecutive unfed midnights and the
  animal escapes, leaving the empty structure (`:805-832`).
* Shed capacity is 100 shared across products and animals, overflow at the
  midnight drop is discarded (`:843-857`), and ``BUY_ANIMAL``/``BUY_PRODUCT``
  fail outright when the shed is full (`:652-687`).
* ``COLLECT_FERTILIZER`` sets ``fertilizer_available`` once per night for every
  animal that did not escape, whether or not it was fed (`:831`).
"""

from __future__ import annotations

import os
from pathlib import Path

from kaggle_environments.envs.kaggriculture.kaggriculture import (
    ANIMALS,
    FARMER_MOVES,
)

from kaggriculture.loading import current_step, load_agent

_BASELINE = Path(__file__).with_name("baseline_v8.py")

BOARD = 10
TURNS_PER_DAY = 24
MAX_ORDERS = 10          # engine: maxMarketOrdersPerTurn, extras are dropped
SHED_CAPACITY = 100      # engine: shedCapacity, shared across every item

#: The four inner-corner tiles around the shed (kaggriculture.py:132-135). A
#: converted tile must never be one of them: the shed is used through these
#: tiles, so claiming one would remove a shed access point.
SHED_ACCESS = ((4, 4), (5, 4), (4, 5), (5, 5))

# --------------------------------------------------------------------------- #
# Small coercions. A malformed observation degrades; it never raises.
# --------------------------------------------------------------------------- #

def _as_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


STRUCTURE_OF = {animal: data["structure"] for animal, data in ANIMALS.items()}
COST_OF = {animal: _as_int(data["cost"]) for animal, data in ANIMALS.items()}
PRODUCT_OF = {animal: data["product"] for animal, data in ANIMALS.items()}
SPECIES = tuple(sorted(ANIMALS))

#: Engine movement verbs are the inverse of what routing needs, so the table is
#: inverted rather than retyped (kaggriculture.py:88-93).
_STEP_VERB = {delta: verb for verb, delta in FARMER_MOVES.items()}

#: The tape verbs this overlay removes, and only on a converted tile. DIG and
#: BUILD_* are deliberately absent: a weed on a converted tile can still be
#: cleared by the tape, and the tape's structure build schedule is measured to
#: end on day 10, before the first conversion day.
SUPPRESSED = ("PLANT", "WATER", "HARVEST")

#: Animal products this overlay's work can add to the shed, plus the fertilizer.
ANIMAL_PRODUCTS = ("EGG", "MILK", "WOOL", "FERTILIZER")


# --------------------------------------------------------------------------- #

def structure_of(species):
    """The structure tile an animal needs (``ANIMALS`` is the single source)."""
    return STRUCTURE_OF[species]


def fib_hire_cost(n_already_today, mult=1):
    """The engine's n-th hire of the day: ``mult * fib(n)``, fib = 1, 1, 2, 3..."""
    a, b = 1, 1
    for _ in range(max(0, _as_int(n_already_today))):
        a, b = b, a + b
    return mult * a


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


def shed_access_tiles():
    """The four shed access tiles (kaggriculture.py:132-135)."""
    return list(SHED_ACCESS)


def is_shed_adjacent(pos):
    return tuple(pos) in SHED_ACCESS


def distance(a, b):
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


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


def _fallback_action(obs):
    try:
        hands = _farm_of(obs).get("hands") or []
    except Exception:
        hands = []
    return {"farmer": ["PASS"], "hands": [["PASS"] for _ in hands], "market": []}


def _cell(tiles, pos):
    """The tile at ``pos``, or ``None`` when it is off the board or unreadable."""
    try:
        return tiles[pos[1]][pos[0]]
    except (IndexError, TypeError, KeyError):
        return None


def _worker_positions(farm):
    return [tuple(farm.get("farmer") or (0, 0))] + [
        tuple(p) for p in (farm.get("hands") or [])
    ]


def _normalise_units(action):
    """The tape's action as a mutable ``[farmer, *hands]`` list of lists."""
    farmer = action.get("farmer") if isinstance(action, dict) else None
    hands = action.get("hands") if isinstance(action, dict) else None
    units = [list(farmer) if isinstance(farmer, (list, tuple)) else ["PASS"]]
    for hand in hands or []:
        units.append(list(hand) if isinstance(hand, (list, tuple)) else ["PASS"])
    return units


def count_animals(tiles, positions):
    """Animals standing on ``positions``, and the empty structures among them."""
    animals = empty = 0
    for pos in positions:
        cell = _cell(tiles, pos)
        if isinstance(cell, dict) and cell.get("animal"):
            animals += 1
        elif isinstance(cell, dict) and cell.get("kind") in ("COOP", "PASTURE"):
            empty += 1
    return animals, empty


# --------------------------------------------------------------------------- #
# The overlay.
# --------------------------------------------------------------------------- #

class MoreAnimalsOverlay:
    """Wrap a tape and convert ``convert_tiles`` crop tiles into animal slots.

    The class is the testable seam: it owns the claimed-tile set, the per-day
    ownership bookkeeping and the animal job board, and it accepts an injected
    ``base_agent`` so tests can drive it with synthetic observations.
    """

    def __init__(
        self,
        species="COW",
        convert_tiles=6,
        extra_hands=2,
        convert_from_day=11,
        hire_from_hour=2,
        reserve=500,
        base_agent=None,
        trace=None,
    ):
        if species not in STRUCTURE_OF:
            raise ValueError(f"species must be one of {SPECIES}")
        if not isinstance(convert_tiles, int) or isinstance(convert_tiles, bool) or convert_tiles < 0:
            raise ValueError("convert_tiles must be a non-negative int")
        if not isinstance(extra_hands, int) or isinstance(extra_hands, bool) or extra_hands < 0:
            raise ValueError("extra_hands must be a non-negative int")
        if not isinstance(convert_from_day, int) or convert_from_day < 0:
            raise ValueError("convert_from_day must be a non-negative int")
        if not 0 <= hire_from_hour < TURNS_PER_DAY:
            raise ValueError("hire_from_hour must be in [0, 24)")
        if reserve < 0:
            raise ValueError("reserve must be non-negative")

        self.species = species
        self.structure = STRUCTURE_OF[species]
        self.convert_tiles = convert_tiles
        self.extra_hands = extra_hands
        self.convert_from_day = convert_from_day
        self.hire_from_hour = hire_from_hour
        self.reserve = reserve
        self.trace = trace if trace is not None else []
        # A private copy: the baseline keeps season state in a module-level
        # singleton, so sharing one import would let two agents corrupt each
        # other.
        self.base = base_agent if base_agent is not None else load_agent(str(_BASELINE))
        self._new_season()
        self._new_day()

    # ---- per-season / per-day bookkeeping ----------------------------------

    def _new_season(self):
        """State that lasts the season: the converted tiles are permanent."""
        self.claimed = []      # ordered; a converted tile never reverts
        self.bought = 0        # BUY_ANIMAL orders this overlay issued
        self.placed = 0        # confirmed PLACE actions by our own hands

    def _new_day(self):
        """State the engine clears at midnight (`kaggriculture.py:876-882`)."""
        self.owned = set()     # roster hand indices we control
        self.pending = 0       # HIREs appended last turn, resolved next turn
        self.hired_today = False

    def _note(self, *event):
        if self.trace is not None:
            self.trace.append(event)

    def _resolve_pending(self, roster_len):
        """Ownership of hands hired last turn: they are the last entries appended.

        HIRE resolves in the market phase, so it was not visible when the order
        was issued; by this turn it is the last hand in the roster (our orders
        append after the tape's). Absolute indices are recorded and kept: later
        tape hires append new entries without shifting existing ones.
        """
        if not self.pending:
            return
        if roster_len >= self.pending:
            start = roster_len - self.pending
            self.owned |= set(range(start, roster_len))
            for index in range(start, roster_len):
                self._note("own_hand", index)
        else:
            # The hire did not land (e.g. cash short). Allow a retry today.
            self.hired_today = False
        self.pending = 0

    # ---- claiming and suppressing the tape's crop work ---------------------

    def _claimable(self, pos):
        if pos in self.claimed:
            return False
        if not (0 <= pos[0] < BOARD and 0 <= pos[1] < BOARD):
            return False
        return pos not in SHED_ACCESS

    def _claim_and_suppress(self, units, positions, tiles, day, quadrants):
        """Claim tiles the tape is planting, and suppress tape work on them.

        Claiming happens on the tape's own ``PLANT`` action, which the engine
        only accepts on an empty tile, so a conversion never digs up a standing
        crop and never throws away a harvest. Suppression covers exactly the
        three verbs the task names and exactly the claimed tiles.
        """
        for index in range(min(len(units), len(positions))):
            unit = units[index]
            if not isinstance(unit, list) or not unit:
                continue
            verb = unit[0]
            pos = positions[index]
            if verb == "PLANT":
                if pos in self.claimed:
                    units[index] = ["PASS"]
                elif (len(self.claimed) < self.convert_tiles
                      and day >= self.convert_from_day and quadrants >= 3
                      and self._claimable(pos) and _cell(tiles, pos) is None):
                    self.claimed.append(pos)
                    units[index] = ["PASS"]
                    self._note("claim", pos, day)
            elif verb in SUPPRESSED and pos in self.claimed:
                units[index] = ["PASS"]
                self._note("suppress", pos, verb, day)

    # ---- the animal job board ----------------------------------------------

    def _hungry(self, tiles):
        """Claimed structures whose animal has not been fed today."""
        hungry = 0
        for pos in self.claimed:
            cell = _cell(tiles, pos)
            if isinstance(cell, dict) and cell.get("animal") and not cell.get("fed_today"):
                hungry += 1
        return hungry

    def _empty_structures(self, tiles):
        empty = []
        for pos in self.claimed:
            cell = _cell(tiles, pos)
            if (isinstance(cell, dict) and cell.get("kind") == self.structure
                    and not cell.get("animal")):
                empty.append(pos)
        return empty

    def _choose_job(self, pos, tiles, inventory, shed, busy):
        """Best unclaimed job for one hand: ``(action_at_target, target)``.

        Priority, low number first:
          0  PLACE the animal we carry on an empty structure it fits
          1  FEED (carrying wheat) / PICKUP WHEAT from the shed to enable it
          2  HARVEST an animal product / PICKUP an animal for an empty structure
          3  COLLECT_FERTILIZER
          4  BUILD the structure on a converted tile
          5  DIG a weed that grew on a converted tile
        Harvest and collect are below feeding because an escaped animal makes its
        purchase a pure loss, while a product only waits in ``yield_units``.
        """
        if pos is None:
            return ["PASS"], None
        carrying = inventory if isinstance(inventory, dict) else {}
        carrying_wheat = carrying.get("WHEAT", 0) > 0
        carrying_animal = carrying.get(self.species, 0) > 0
        candidates = []

        for tile_pos in self.claimed:
            if tile_pos in busy:
                continue
            dist = distance(pos, tile_pos)
            cell = _cell(tiles, tile_pos)
            if cell is None:
                candidates.append((4, dist, tile_pos, [f"BUILD_{self.structure}"]))
            elif isinstance(cell, dict):
                if cell.get("kind") == "WEED":
                    candidates.append((5, dist, tile_pos, ["DIG"]))
                elif cell.get("animal"):
                    if carrying_wheat and not cell.get("fed_today"):
                        candidates.append((1, dist, tile_pos, ["FEED"]))
                    if _as_int(cell.get("yield_units", 0)) > 0:
                        candidates.append((2, dist, tile_pos, ["HARVEST"]))
                    if cell.get("fertilizer_available"):
                        candidates.append((3, dist, tile_pos, ["COLLECT_FERTILIZER"]))
                elif cell.get("kind") == self.structure and carrying_animal:
                    candidates.append((0, dist, tile_pos, ["PLACE", self.species]))

        # Shed side of the two carry chains. A hand picks one item up at a time,
        # but a PICKUP may carry a COUNT: grabbing one wheat per feed would make
        # every feed a full shed round trip, which is the throughput bug that
        # left three animals unfed in the first assembly.
        if not carrying_animal and not carrying_wheat:
            hungry = self._hungry(tiles)
            shed_wheat = _as_int(shed.get("WHEAT", 0))
            want_wheat = hungry > 0 and shed_wheat > 0
            want_animal = (self._empty_structures(tiles) and
                           _as_int(shed.get(self.species, 0)) > 0)
            if want_animal or want_wheat:
                available = [t for t in SHED_ACCESS if t not in busy]
                if available:
                    target = min(available, key=lambda t: (distance(pos, t), t))
                    if want_animal:
                        action = ["PICKUP", self.species, 1]
                        priority = 2
                    else:
                        action = ["PICKUP", "WHEAT", min(shed_wheat, hungry)]
                        priority = 1
                    candidates.append((priority, distance(pos, target), target, action))

        if not candidates:
            return ["PASS"], None
        _priority, _dist, target, action = min(
            candidates, key=lambda row: (row[0], row[1], row[2][0], row[2][1])
        )
        if tuple(pos) == target:
            return list(action), target
        return [step_toward(tuple(pos), target) or "PASS"], target

    def _drive_hands(self, units, positions, obs, tiles):
        """The hands list with only our own indices rewritten."""
        hands_out = [list(unit) for unit in units[1:]]
        if self.owned:
            needed = max(self.owned) + 1
            while len(hands_out) < needed:
                hands_out.append(["PASS"])
        private = obs.get("private") or {}
        inventories = private.get("inventories") or []
        shed = private.get("shed") or {}
        busy = set()
        for index in sorted(self.owned):
            roster_index = index + 1  # positions/inventories are farmer-first
            pos = positions[roster_index] if roster_index < len(positions) else None
            inventory = inventories[roster_index] if roster_index < len(inventories) else {}
            action, target = self._choose_job(pos, tiles, inventory, shed, busy)
            if index < len(hands_out):
                hands_out[index] = action
            if target is not None:
                busy.add(target)
            self._note("act", index, tuple(action), target)
            verb = action[0] if action else None
            if verb == "PLACE" and target is not None:
                self.placed += 1
                self._note("place", target)
            elif verb == "FEED":
                self._note("feed", target)
            elif verb == "HARVEST":
                self._note("harvest", target)
            elif verb == "COLLECT_FERTILIZER":
                self._note("collect", target)
            elif verb in ("BUILD_COOP", "BUILD_PASTURE"):
                self._note("build", target)
        return hands_out

    # ---- market side: hires, animals, feed wheat ---------------------------

    def _augment_market(self, market, farm, tiles, day, step, quadrants):
        if quadrants < 3 or day < self.convert_from_day:
            return

        def room():
            return MAX_ORDERS - len(market)

        money = _as_float(farm.get("money", 0))
        shed = (farm.get("_shed") or {})
        shed_total = sum(_as_int(v) for v in shed.values())

        # 1. Hires, appended AFTER the tape's own so they land at the roster tail.
        if (self.extra_hands > 0 and not self.hired_today
                and step % TURNS_PER_DAY >= self.hire_from_hour):
            want = self.extra_hands - (len(self.owned) + self.pending)
            if want > 0 and room() > 0:
                hires_today = _as_int(farm.get("hires_today", 0))
                wallet = money
                affordable = 0
                for slot in range(min(want, room())):
                    cost = fib_hire_cost(hires_today + slot)
                    if wallet < cost:
                        break
                    wallet -= cost
                    affordable += 1
                for _ in range(affordable):
                    market.append(["HIRE"])
                if affordable:
                    self.pending += affordable
                    self.hired_today = True
                    self._note("hire", affordable, day)

        # 2. Buy one animal per empty built structure, never more in flight than
        #    there is room to place. BUY_ANIMAL lands the animal in the shed, so
        #    a full shed makes the buy fail outright (`:652-687`).
        empty = self._empty_structures(tiles)
        in_flight = self.bought - self.placed
        if (self.bought < self.convert_tiles and in_flight < len(empty)
                and room() > 0 and shed_total < SHED_CAPACITY
                and money >= COST_OF[self.species] + self.reserve):
            market.append(["BUY_ANIMAL", self.species, 1])
            self.bought += 1
            self._note("buy_animal", day)

        # 3. Top up the feed wheat. Every animal eats one WHEAT per day; the
        #    tape's own 17-animal herd shares the same shed, so this ADDS wheat
        #    rather than competing for it.
        alive, _ = count_animals(tiles, self.claimed)
        if alive > 0 and room() > 0:
            carried = 0
            for inventory in (farm.get("_inventories") or []):
                carried += _as_int((inventory or {}).get("WHEAT", 0))
            have = _as_int(shed.get("WHEAT", 0)) + carried
            short = alive - have
            if short > 0 and money >= self.reserve:
                market.append(["BUY_PRODUCT", "WHEAT", min(short, 4)])
                self._note("buy_wheat", min(short, 4))

    # ---- tape-preserving transform -----------------------------------------

    def augment(self, obs, action):
        """The tape-preserving transform. Pure with respect to the tape action."""
        step = current_step(obs)
        if step == 0:
            self._new_season()
            self._new_day()
        farm = _farm_of(obs)
        hands = list(farm.get("hands") or [])
        if step % TURNS_PER_DAY == 0:
            self._new_day()
        self._resolve_pending(len(hands))

        day = _as_int(obs.get("day"), step // TURNS_PER_DAY)
        quadrants = len(farm.get("unlocked_quadrants") or [])
        tiles = farm.get("tiles") or []
        private = obs.get("private") or {}
        farm = dict(farm)
        # A private view for the market side: it reads the shed and the carried
        # inventories without threading two more arguments through every helper.
        farm["_shed"] = private.get("shed") or {}
        farm["_inventories"] = private.get("inventories") or []

        units = _normalise_units(action)
        positions = _worker_positions(farm)
        # The tape action's hands may be shorter than the roster (it never
        # addresses our extra hands), so pad before indexing by hand number.
        while len(units) < len(positions):
            units.append(["PASS"])

        self._claim_and_suppress(units, positions, tiles, day, quadrants)

        market = [list(order) for order in (action.get("market") or [])]
        self._augment_market(market, farm, tiles, day, step, quadrants)
        hands_out = self._drive_hands(units, positions, obs, tiles)

        return {
            "farmer": units[0],
            "hands": hands_out,
            "market": market[:MAX_ORDERS],
        }

    def __call__(self, obs):
        try:
            action = self.base(obs)
        except Exception:
            action = _fallback_action(obs)
        if not isinstance(action, dict):
            return action
        if self.convert_tiles <= 0:
            # A full pass-through: this is the byte-identity configuration.
            return action
        try:
            return self.augment(obs, action)
        except Exception:
            # A malformed observation must never take the agent out of the
            # season: the untouched tape action is always legal.
            return action


def make_agent(
    species="COW",
    convert_tiles=6,
    extra_hands=2,
    convert_from_day=11,
    hire_from_hour=2,
    reserve=500,
    base_agent=None,
    trace=None,
):
    """Build an ``agent(obs)`` that converts crop tiles into animal slots.

    ``convert_tiles=0`` is a full pass-through, so it reproduces the bare tape
    exactly (the identity check the task requires).
    """
    overlay = MoreAnimalsOverlay(
        species=species,
        convert_tiles=convert_tiles,
        extra_hands=extra_hands,
        convert_from_day=convert_from_day,
        hire_from_hour=hire_from_hour,
        reserve=reserve,
        base_agent=base_agent,
        trace=trace,
    )

    def agent(obs):
        return overlay(obs)

    return agent


# Default configuration, switched by measurement: COW at 6 converted tiles.
#
# The measurement instruments take a file path (``--agent``), not a species, so
# the species used by ``scripts/corpus_opponents.py`` and
# ``scripts/corpus_within_game.py --vs`` is selected with the
# ``KAGGRICULTURE_ANIMAL_SPECIES`` environment variable. That keeps the whole
# experiment inside this one allowed file instead of adding per-species wrappers.
_DEFAULT_SPECIES = os.environ.get("KAGGRICULTURE_ANIMAL_SPECIES", "COW")
if _DEFAULT_SPECIES not in STRUCTURE_OF:
    _DEFAULT_SPECIES = "COW"

agent = make_agent(species=_DEFAULT_SPECIES)
