"""V10 wave: a BOUNDED 4th-quadrant (SE) overlay on the V8 tape.

The lever
---------
A within-game analysis of 100 real top-team matches (`scripts/corpus_within_game.py`,
`scripts/corpus_quadrant_timing.py`) measured the 4th quadrant as the strongest
win-rate signal available:

    seats that buy it       43/61  = 70.5% wins
    seats that never do     57/139 = 41.0% wins

The reverse-causality objection was tested with TIMING, which an association cannot
fake: at the moment of purchase only 16% of buyers were ahead on bank, the median
bank gap was -$3,495, and every purchase happened by day 15. So the purchase
PRECEDES the win. A third measurement removed the labour objection: buyers use the
extra cells (peak crop tiles 58 -> 75, exactly the +25 cells) while hiring only +6%
(290 -> 308 hires over 30 days, ~0.6 extra hands/day).

Our tape ends at exactly 58 peak crop tiles with 3 quadrants
(`baseline_v8`: NE at step 151, SW at step 266, SE never), i.e. a 3-quadrant player
in a field of 4-quadrant winners. This module is the bounded test of the lever.

What it does (and only this)
----------------------------
1. Buys the SE quadrant when the tape has already unlocked 3 quadrants, ``day`` is
   at or past ``land_from_day`` (default 10), and cash covers the $4,000 SE price
   plus ``land_reserve``.
2. Hires up to ``extra_hands`` (default 1) EXTRA hands per day beyond the tape's
   own daily hires, subject to the 10-order per-turn budget.
3. Dedicates ONLY those extra hands to a minimal local job board inside SE:
   PLANT/WATER/HARVEST ``quadrant_crop`` (default WHEAT) and walk toward the work.

The tape itself is untouched: the overlay takes the dict the tape returns and
never rewrites its farmer action, its non-owned hand actions, or its market order
slots. It only appends its own market orders (BUY_LAND / HIRE) and replaces the
hand action at an index it owns. With ``extra_hands=0`` the overlay is a complete
pass-through and reproduces the bare tape byte for byte (see
``tests/test_v10_partial_quadrant.py::test_zero_extra_hands_is_byte_identical_to_tape``).

Why the land purchase cannot break the tape's own land cliff
------------------------------------------------------------
``param_agent`` documents the step-150 cliff: the tape's BUY_LAND at step 151 fails
if it is short by even ~$1, and a missed quadrant once cost $92k. This overlay
therefore spends NOTHING extra until the tape's own land purchases are complete:
the SE buy is gated on ``len(unlocked_quadrants) == 3`` (NW + NE + SW already
bought) and on ``day >= land_from_day``, and hires only start once SE exists. The
tape's own purchases are never in competition with our cash.

The two engine traps this module is built around
-----------------------------------------------
* **Atomic PLANT validation** (``kaggriculture.py:919-931``): if the total PLANT
  demand for a crop in one turn exceeds the seeds held, EVERY plant of that crop is
  voided. Our extra hand therefore only plants while the spare seed count allows
  (``_plant_budget`` subtracts the tape's own demand from the seeds held), so the
  overlay can never trip the tape's wheat planting.
* **HIRE resolves in the market phase, after unit actions**: a hand hired on turn
  ``t`` first appears in the observation on turn ``t+1`` (verified empirically
  against the engine, not assumed). Ownership is therefore resolved one turn later,
  when the new hand is the last entry appended by the market.

Watering: a deliberate, documented deviation
--------------------------------------------
The task's brief says "water only inside the window
``[(max_yield_day+1)//2, max_yield_day]``". Taken literally that would weed out
every crop before its window opens: ``_new_plant`` starts
``consecutive_unwatered`` at 1 and ``_daily_refresh_plants`` turns a plant into a
WEED after two consecutive unwatered midnights (``kaggriculture.py:769-800``), so a
crop planted and not watered on its planting day is a weed at the next midnight.
``param_agent.needs_water`` already records this lesson ("a crop watered only inside
its window is already a weed before the window opens: measured on the broken agent,
day 0 planted 15 tiles with 0 WATER actions and day 1 dug all 15"). This module
follows the verified engine rule: water the planting day (to survive) and inside
the window (for the growth bonus). See ``needs_water``.
"""

from __future__ import annotations

from pathlib import Path

from kaggle_environments.envs.kaggriculture.kaggriculture import (
    CROPS,
    FARMER_MOVES,
    LAND_PRICES,
)

from kaggriculture.loading import current_step, load_agent

_BASELINE = Path(__file__).with_name("baseline_v8.py")

BOARD = 10
TURNS_PER_DAY = 24
MAX_ORDERS = 10  # engine: maxMarketOrdersPerTurn, extras silently dropped
SE_QUADRANT = "SE"
SE_PRICE = LAND_PRICES[2]  # 4000 -- the third paid quadrant, always SE

# Engine movement verbs are the inverse of what routing needs, so invert the
# imported table rather than retyping it (kaggriculture.py:88-93).
_STEP_VERB = {delta: verb for verb, delta in FARMER_MOVES.items()}
_ACTION = {"WATER": ["WATER"], "HARVEST": ["HARVEST"], "DIG": ["DIG"]}


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
# Pure geometry / crop helpers. No engine state, cheap to test.
# --------------------------------------------------------------------------- #

def _half(board=BOARD):
    return board // 2


def quadrant_of(x, y, board=BOARD):
    """Engine quadrant name for a tile (kaggriculture.py:127-129)."""
    h = board // 2
    return ("N" if y < h else "S") + ("W" if x < h else "E")


def shed_access_tiles(board=BOARD):
    """The four inner-corner tiles around the shed (kaggriculture.py:132-136)."""
    h = board // 2
    return [(h - 1, h - 1), (h, h - 1), (h - 1, h), (h, h)]


def designated_tiles(count, board=BOARD, quadrant=SE_QUADRANT):
    """The first ``count`` workable tiles of ``quadrant``, closest to the shed.

    Excludes the shed-access tiles because the shed is used through them. The order
    is deterministic (distance, then row, then column) so a tile keeps its identity
    across turns and the mechanism report can name the tiles we worked.
    """
    if count <= 0:
        return []
    shed = shed_access_tiles(board)
    candidates = []
    for y in range(board):
        for x in range(board):
            if quadrant_of(x, y, board) != quadrant:
                continue
            if (x, y) in shed:
                continue
            dist = min(abs(x - sx) + abs(y - sy) for sx, sy in shed)
            candidates.append((dist, y, x, (x, y)))
    candidates.sort()
    return [entry[3] for entry in candidates[:count]]


def water_window(crop):
    """The growth-bonus window ``[(max_yield_day+1)//2, max_yield_day]``."""
    data = CROPS[crop]
    return (data["max_yield_day"] + 1) // 2, data["max_yield_day"]


def needs_water(tile, day, crop):
    """Whether this tile must be watered today. See the module docstring.

    Two jobs, only one window-gated: WATER inside the window grants the growth
    bonus, but WATER also resets the weed clock. A plant with
    ``consecutive_unwatered >= 1`` weeds out tonight if it is not watered, which is
    why the planting day (where the engine starts the counter at 1) is included.
    """
    if not isinstance(tile, dict) or tile.get("kind") != "PLANT":
        return False
    if tile.get("crop") != crop or tile.get("watered_today", False):
        return False
    age = day - _as_int(tile.get("planted_day", day), day)
    start, end = water_window(crop)
    return start <= age <= end or _as_int(tile.get("consecutive_unwatered", 0)) >= 1


def water_is_urgent(tile):
    """True when skipping WATER tonight would turn the plant into a weed."""
    return _as_int(tile.get("consecutive_unwatered", 0)) >= 1


def harvest_due(tile, day, crop):
    """Whether harvesting now is the right call for a one-shot crop.

    Harvest is only legal at/after ``first_yield_day`` (``kaggriculture.py:451``).
    For a one-shot crop the yield bonus is only paid inside the watering window, so
    harvesting early throws it away; harvest once the crop has reached its
    ``max_yield_day`` (or its max yield), before ``_decay_plants`` starts shaving
    it at ``(planted_day + max_yield_day + 1)`` days.
    """
    if not isinstance(tile, dict) or tile.get("kind") != "PLANT":
        return False
    if tile.get("crop") != crop or _as_int(tile.get("yield_units", 0)) <= 0:
        return False
    data = CROPS[crop]
    age = day - _as_int(tile.get("planted_day", day), day)
    if age < data["first_yield_day"]:
        return False
    if data["ongoing"]:
        return True
    return (_as_int(tile.get("yield_units", 0)) >= data["max_yield"]
            or age >= data["max_yield_day"])


def tile_job(tile, day, crop, plant_budget):
    """The job a designated tile offers, as ``(kind, priority)`` or ``None``.

    Lower priority is done first: harvest and an urgent water both lose value
    tonight, a weed blocks the tile permanently, a bonus water can wait a day, and
    planting is last because an unplanted tile is not yet losing anything.
    """
    if tile is None:
        return ("PLANT", 3) if plant_budget > 0 else None
    if not isinstance(tile, dict):
        return None
    if tile.get("kind") == "WEED":
        return ("DIG", 1)
    if tile.get("kind") != "PLANT" or tile.get("crop") != crop:
        return None
    if harvest_due(tile, day, crop):
        return ("HARVEST", 0)
    if needs_water(tile, day, crop):
        return ("WATER", 0 if water_is_urgent(tile) else 2)
    return None


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


def _player_of(obs):
    return _as_int(obs.get("player", 0))


def _fallback_action(obs):
    try:
        hands = _farm_of(obs).get("hands") or []
    except Exception:
        hands = []
    return {"farmer": ["PASS"], "hands": [["PASS"] for _ in hands], "market": []}


# --------------------------------------------------------------------------- #
# The overlay.
# --------------------------------------------------------------------------- #

class QuadrantOverlay:
    """Wrap a tape agent and add a bounded, SE-only extra-hand program.

    The class is the testable seam: it owns the per-day bookkeeping (which hand
    indices are ours, how many hires are in flight) and the SE job board, and it
    accepts an injected ``base_agent`` so tests can drive it with synthetic
    observations without a season.
    """

    def __init__(
        self,
        extra_hands=1,
        land_from_day=10,
        quadrant_crop="WHEAT",
        quadrant_tiles=4,
        land_reserve=2000,
        hire_from_hour=2,
        base_agent=None,
        trace=None,
    ):
        if not isinstance(extra_hands, int) or isinstance(extra_hands, bool) or extra_hands < 0:
            raise ValueError("extra_hands must be a non-negative int")
        if not isinstance(land_from_day, int) or land_from_day < 0:
            raise ValueError("land_from_day must be a non-negative int")
        if quadrant_crop not in CROPS:
            raise ValueError(f"quadrant_crop must be one of {sorted(CROPS)}")
        if not isinstance(quadrant_tiles, int) or quadrant_tiles < 0:
            raise ValueError("quadrant_tiles must be a non-negative int")
        if land_reserve < 0:
            raise ValueError("land_reserve must be non-negative")
        if not 0 <= hire_from_hour < TURNS_PER_DAY:
            raise ValueError("hire_from_hour must be in [0, 24)")

        self.extra_hands = extra_hands
        self.land_from_day = land_from_day
        self.quadrant_crop = quadrant_crop
        self.quadrant_tiles = quadrant_tiles
        self.land_reserve = land_reserve
        self.hire_from_hour = hire_from_hour
        self.trace = trace if trace is not None else []
        self.targets = designated_tiles(quadrant_tiles)
        # A private copy: the tape keeps season state in a module-level singleton,
        # so sharing one import would let two agents corrupt each other.
        self.base = base_agent if base_agent is not None else load_agent(str(_BASELINE))
        self._reset()

    # ---- per-day bookkeeping ------------------------------------------------

    def _reset(self):
        self.owned = set()        # hand indices we own in the current day
        self.pending = 0          # HIREs appended last turn, resolved next turn
        self.hired_today = False

    def _note(self, *event):
        if self.trace is not None:
            self.trace.append(event)

    def _resolve_pending(self, roster_len):
        """Ownership of a hand hired last turn: it is the last entry appended.

        HIRE resolves in the market phase, so it was not visible when the order was
        issued; by this turn it is the last hand in the roster (our orders append
        after the tape's, so ours resolve last). Absolute indices are recorded and
        kept: later tape hires append new entries without shifting existing ones.
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

    # ---- tape-preserving transform -----------------------------------------

    def _tape_plant_demand(self, action):
        """PLANT actions for ``quadrant_crop`` in the tape slots we do NOT replace.

        The engine voids EVERY plant of a crop whose turn demand exceeds the seeds
        held (``kaggriculture.py:919-931``), so our plants must fit in what the tape
        leaves over. Actions at the indices we own are excluded because we replace
        them.
        """
        crop = self.quadrant_crop
        units = [action.get("farmer")] + list(action.get("hands") or [])
        demand = 0
        for index, unit in enumerate(units):
            if index >= 1 and (index - 1) in self.owned:
                continue
            if (isinstance(unit, (list, tuple)) and len(unit) >= 2
                    and unit[0] == "PLANT" and unit[1] == crop):
                demand += 1
        return demand

    def _plant_budget(self, action, seeds):
        return max(0, _as_int(seeds.get(self.quadrant_crop, 0))
                   - self._tape_plant_demand(action))

    def _choose_action(self, pos, tiles, day, budget, claimed):
        """Best unclaimed job for one hand: the action and the tile it commits to."""
        if pos is None:
            return ["PASS"], None
        best = None
        for (x, y) in self.targets:
            if (x, y) in claimed:
                continue
            try:
                tile = tiles[y][x]
            except (IndexError, TypeError):
                continue
            job = tile_job(tile, day, self.quadrant_crop, budget)
            if job is None:
                continue
            kind, priority = job
            distance = abs(pos[0] - x) + abs(pos[1] - y)
            key = (priority, distance, x, y)
            if best is None or key < best[0]:
                best = (key, (x, y), kind)
        if best is None:
            return ["PASS"], None
        target, kind = best[1], best[2]
        if tuple(pos) == target:
            if kind == "PLANT":
                # PLANT without a crop payload is a silent no-op: the engine reads
                # action[1] as the crop and returns early without it
                # (kaggriculture.py:418-420). Never emit ["PLANT"] alone.
                return ["PLANT", self.quadrant_crop], target
            return list(_ACTION.get(kind, [kind])), target
        # Not standing on it yet: every job, planting included, is reached by
        # walking. Emitting PLANT from a distance is a silent no-op, which is how
        # the first version produced 390 plants on one tile and zero harvests.
        verb = step_toward(tuple(pos), target)
        return [verb or "PASS"], target

    def _drive_hands(self, obs, action, farm, seeds):
        """Return the hands list with only our own indices rewritten."""
        hands = list(farm.get("hands") or [])
        hands_out = [list(hand) if isinstance(hand, (list, tuple)) else ["PASS"]
                     for hand in (action.get("hands") or [])]
        if self.owned:
            needed = max(self.owned) + 1
            while len(hands_out) < needed:
                hands_out.append(["PASS"])
        tiles = farm.get("tiles") or []
        step = current_step(obs)
        day = _as_int(obs.get("day"), step // TURNS_PER_DAY)
        budget = self._plant_budget(action, seeds)
        claimed = set()
        for index in sorted(self.owned):
            pos = hands[index] if index < len(hands) else None
            if isinstance(pos, (list, tuple)) and len(pos) == 2:
                pos = (_as_int(pos[0]), _as_int(pos[1]))
            else:
                pos = None
            hand_action, target = self._choose_action(pos, tiles, day, budget, claimed)
            if index < len(hands_out):
                hands_out[index] = hand_action
            if target is not None:
                claimed.add(target)
            self._note("act", index, tuple(hand_action), tuple(target) if target else None)
            verb = hand_action[0] if hand_action else None
            if verb == "PLANT":
                budget -= 1
                self._note("plant", target, day)
            elif verb == "HARVEST" and target is not None:
                try:
                    cell = tiles[target[1]][target[0]]
                except (IndexError, TypeError):
                    cell = None
                yield_units = _as_int(cell.get("yield_units", 0)) if isinstance(cell, dict) else 0
                self._note("harvest", target, yield_units, day)
            elif verb == "WATER":
                self._note("water", target, day)
            elif verb == "DIG":
                self._note("dig", target, day)
        return hands_out

    def augment(self, obs, action):
        """The tape-preserving transform. Pure with respect to the tape's action."""
        step = current_step(obs)
        if step == 0:
            self._reset()
        farm = _farm_of(obs)
        hands = list(farm.get("hands") or [])

        # Midnight: the engine resets the roster (`kaggriculture.py:860-888`), so
        # every hand we owned is gone and must be re-hired.
        if step % TURNS_PER_DAY == 0:
            self._reset()
        self._resolve_pending(len(hands))

        market = [list(order) for order in (action.get("market") or [])]
        quads = list(farm.get("unlocked_quadrants") or [])
        day = _as_int(obs.get("day"), step // TURNS_PER_DAY)
        money = _as_float(farm.get("money", 0))
        seeds = dict((obs.get("private") or {}).get("seeds") or {})

        # 1. Buy the 4th quadrant, but only after the tape's own land buys are done.
        if (len(quads) == 3 and day >= self.land_from_day
                and money >= SE_PRICE + self.land_reserve
                and len(market) < MAX_ORDERS):
            market.append(["BUY_LAND"])
            self._note("land", day, step)

        # 2. Hire our extra hands once the quadrant exists. Appended so they resolve
        #    after the tape's own hires, and only at/after hire_from_hour so they are
        #    inserted at the tail of a completed roster (no tape hand index shifts).
        if len(quads) == 4 and not self.hired_today and step % TURNS_PER_DAY >= self.hire_from_hour:
            want = self.extra_hands - (len(self.owned) + self.pending)
            room = MAX_ORDERS - len(market)
            if want > 0 and room > 0:
                hires_today = _as_int(farm.get("hires_today", 0))
                affordable = 0
                wallet = money
                for slot in range(min(want, room)):
                    cost = fib_hire_cost(hires_today + slot)
                    if wallet < cost:
                        break
                    wallet -= cost
                    affordable += 1
                for slot in range(affordable):
                    market.append(["HIRE"])
                    self._note("hire", fib_hire_cost(hires_today + slot), step)
                if affordable:
                    self.pending += affordable
                    self.hired_today = True

        hands_out = self._drive_hands(obs, action, farm, seeds)
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
        if not isinstance(action, dict) or self.extra_hands <= 0:
            return action
        try:
            return self.augment(obs, action)
        except Exception:
            # A malformed observation must never take the agent out of the season.
            # Returning the untouched tape action is always legal.
            return action


def make_agent(
    extra_hands=1,
    land_from_day=10,
    quadrant_crop="WHEAT",
    quadrant_tiles=4,
    land_reserve=2000,
    hire_from_hour=2,
    base_agent=None,
    trace=None,
):
    """Build an ``agent(obs)`` adding a bounded SE program to the V8 tape.

    ``extra_hands=0`` is a full pass-through, so it reproduces the bare tape
    exactly (the identity check the task requires).
    """
    overlay = QuadrantOverlay(
        extra_hands=extra_hands,
        land_from_day=land_from_day,
        quadrant_crop=quadrant_crop,
        quadrant_tiles=quadrant_tiles,
        land_reserve=land_reserve,
        hire_from_hour=hire_from_hour,
        base_agent=base_agent,
        trace=trace,
    )

    def agent(obs):
        return overlay(obs)

    return agent


# Default configuration measured by the task: 1 extra hand, 4 SE tiles, WHEAT.
agent = make_agent()
