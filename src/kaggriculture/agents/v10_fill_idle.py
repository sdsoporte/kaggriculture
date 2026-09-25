"""V10 wave: turn the tape's IDLE turns into strictly-safe local work.

The measurement this attacks
----------------------------
``scripts/micro_behaviour.py`` over the corpus and our own tape, median per
seat-season (``odd/tasks`` context, field micro-analysis):

    metric              corpus winners   our tape
    MOVEMENT                      42.4%      44.9%
    PASS (idle)                    3.8%       7.4%
    WORK                          53.0%      47.7%

We idle about twice as often as the field's winners: ~529 idle unit-turns a
season against their ~275. The tape emits ``PASS`` whenever its fixed schedule
has nothing for that unit on that turn. But the unit is standing SOMEWHERE, and
sometimes the tile under it has work the schedule does not cover.

What this overlay does (and only this)
--------------------------------------
For every unit action the tape returns, if the action is ``["PASS"]``, look at
the tile that unit is standing on **in the same observation the tape just used**,
and if that tile offers SAFE WORK, do that instead.

It never moves a unit, never emits a market order, and never modifies a
non-PASS action, so it cannot disrupt a route or defund a buy queue. With
``enabled=False`` it is a complete pass-through and reproduces the bare tape
byte for byte
(``tests/test_v10_fill_idle.py::test_disabled_overlay_is_byte_identical_to_the_tape``).

The SAFE WORK set
-----------------
Chosen so it can never lose a unit or a unit of yield:

1. **Animal, not ``fed_today``, and the unit carries ``WHEAT`` -> FEED.** Two
   consecutive unfed midnights and the animal escapes and is worth zero
   (``kaggriculture.py:_daily_refresh_animals``). This protects value, so it is
   tried before harvest. Without carried wheat it is a silent no-op
   (``:497-504`` returns before any state change), so it is never emitted blind.
2. **Animal with ``yield_units > 0`` -> HARVEST.** Products accumulate to
   ``max_held`` and harvesting sets the tile back to 0 so it can accumulate
   again, so this is never a loss.
3. **Animal with ``fertilizer_available`` -> COLLECT_FERTILIZER.** No downside.
4. **Animal not ``cared_today`` -> CARE.** No downside.
5. **Plant at its yield cap -> HARVEST** (``CROPS[crop]["max_yield"]``). Nothing
   further can be added: the engine clamps every gain with
   ``min(max_yield, ...)`` (``:440-446``, ``_daily_refresh_plants``).
6. **One-shot plant strictly past its window -> HARVEST.** Not ongoing and
   ``day - planted_day > max_yield_day``: the watering bonus window is
   ``[(max_yield_day + 1) // 2, max_yield_day]`` INCLUSIVE, so the first day no
   accumulation is possible is ``max_yield_day + 1`` -- which is also the day
   ``_decay_plants`` starts shaving the tile at every second step
   (``max_lifespan_step = (planted_day + max_yield_day + 1) * turns_per_day``).
   Harvesting from that day on cannot lose anything and stops the decay.

Trap #1: never harvest a plant below its cap inside its window
--------------------------------------------------------------
``yield_units`` keeps growing until ``max_yield_day``, so harvesting at
``first_yield_day`` takes 1-2 units where the window would have delivered 4-6.
A harvest that loses yield is worse than the PASS it replaced, and this project
already made that mistake once: a plan consumer fired ``harvest_ready`` at
``first_yield_day`` and halved every crop
(``v10_partial_quadrant.harvest_due``, ``param_agent.needs_water``).

That is why rule 6 above uses ``>`` and not the brief's literal ``>=``: at
``day - planted_day == max_yield_day`` the tile is still inside the bonus
window, and a ``WATER`` that day can still add units. Harvesting there would
clip the last day of growth.

Trap #2: never harvest before ``first_yield_day``, even at the cap
------------------------------------------------------------------
For a one-shot crop a HARVEST below ``first_yield_day`` is a silent no-op that
only inflates the invisible-waste number (``:451-462``). It is reachable: a
MELON watered inside its window reaches its cap of 6 on day 8, two days before
its ``first_yield_day`` of 10. The cap rule is therefore gated on
``age >= first_yield_day``.

Deliberately out of scope
-------------------------
``DIG`` on a weed is fine but rare on an occupied tile, and ``FERTILIZE``
consumes a carried item, so both are left out of this version.

Safety
------
Every helper degrades instead of raising: a malformed observation must never
take the agent out of the season, because an agent that raises is inert for the
rest of the season while the opponent keeps playing, which looks like a
legitimate loss.

Measurement hooks
-----------------
Pass a ``trace`` list to collect one ``("fill", step, unit_index, verb)`` entry
per converted turn; :func:`tally` counts them by verb.
"""

from __future__ import annotations

import collections
from pathlib import Path

from kaggle_environments.envs.kaggriculture.kaggriculture import CROPS, FARMER_MOVES

from kaggriculture.loading import current_step, load_agent

_BASELINE = Path(__file__).with_name("baseline_v8.py")

BOARD = 10
TURNS_PER_DAY = 24

#: The movement verbs, imported rather than retyped, so "never moves a unit" is
#: checked against the engine's own table.
MOVEMENT_VERBS = frozenset(FARMER_MOVES)

#: The only verbs this overlay may emit. No movement verb and no market verb is
#: in here, and neither DIG nor FERTILIZE (see the module docstring).
SAFE_VERBS = ("FEED", "HARVEST", "COLLECT_FERTILIZER", "CARE")


# --------------------------------------------------------------------------- #
# Non-raising observation helpers. No helper in this module may raise.
# --------------------------------------------------------------------------- #

def _as_int(value, default=0):
    """Non-raising int coercion: a malformed field degrades, never raises."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _farm_of(obs):
    try:
        farms = obs.get("farms") or []
        player = _as_int(obs.get("player", 0))
        farm = farms[player] if 0 <= player < len(farms) else {}
        return farm if isinstance(farm, dict) else {}
    except (AttributeError, TypeError, ValueError, IndexError, KeyError):
        return {}


def _inventories_of(obs):
    try:
        invs = (obs.get("private") or {}).get("inventories") or []
        return invs if isinstance(invs, (list, tuple)) else []
    except (AttributeError, TypeError, ValueError, KeyError):
        return []


def _day_of(obs, step):
    """The engine's ``day`` field, falling back to the turn index."""
    fallback = _as_int(step) // TURNS_PER_DAY
    try:
        raw = obs.get("day")
    except (AttributeError, TypeError, ValueError, KeyError):
        return fallback
    return fallback if raw is None else _as_int(raw, fallback)


def _fallback_action(obs):
    """A legal PASS for every unit the roster shows. Used when the tape raises."""
    try:
        hands = _farm_of(obs).get("hands") or []
    except Exception:
        hands = []
    return {"farmer": ["PASS"], "hands": [["PASS"] for _ in hands], "market": []}


def tile_at(tiles, pos):
    """The tile a unit stands on, or ``None`` when there is no such tile.

    Bounds are checked explicitly: a negative index would otherwise silently
    wrap to the far edge of the board and make the unit act on a tile it is
    nowhere near.
    """
    if not isinstance(pos, (list, tuple)) or len(pos) < 2:
        return None
    if not isinstance(tiles, (list, tuple)):
        return None
    try:
        x, y = _as_int(pos[0], -1), _as_int(pos[1], -1)
        if y < 0 or y >= len(tiles):
            return None
        row = tiles[y]
        if not isinstance(row, (list, tuple)):
            return None
        if x < 0 or x >= len(row):
            return None
        return row[x]
    except (TypeError, ValueError, IndexError, KeyError, AttributeError):
        return None


def carried(inventories, index):
    """The inventory of unit ``index`` (0 = farmer, 1+ = hand index + 1)."""
    try:
        if not (0 <= index < len(inventories)):
            return {}
        inv = inventories[index]
        return inv if isinstance(inv, dict) else {}
    except (TypeError, ValueError, AttributeError):
        return {}


# --------------------------------------------------------------------------- #
# The conservative work set.
# --------------------------------------------------------------------------- #

def safe_work(tile, day, wheat=0):
    """The strictly-safe action a unit standing on ``tile`` can take, or ``None``.

    ``wheat`` is the WHEAT the *unit* carries, not the shed: FEED consumes from
    the unit's own inventory (``_farmer_inventory``), so without carried wheat it
    is a silent no-op. See the module docstring for why each rule cannot lose.
    """
    if not isinstance(tile, dict):
        # ``None`` (empty tile) or the string "LOCKED": no legal tile operation.
        return None

    if tile.get("animal"):
        if not tile.get("fed_today") and _as_int(wheat) > 0:
            return ["FEED"]
        if _as_int(tile.get("yield_units")) > 0:
            return ["HARVEST"]
        if tile.get("fertilizer_available"):
            return ["COLLECT_FERTILIZER"]
        if not tile.get("cared_today"):
            return ["CARE"]
        return None

    if tile.get("kind") != "PLANT":
        return None
    crop = tile.get("crop")
    data = CROPS.get(crop) if isinstance(crop, str) else None
    if not data:
        return None

    units = _as_int(tile.get("yield_units"))
    if units <= 0:
        return None
    age = _as_int(day) - _as_int(tile.get("planted_day"), _as_int(day))
    if age < data["first_yield_day"]:
        # HARVEST below first_yield_day is a silent no-op for a one-shot crop.
        return None
    if units >= data["max_yield"]:
        return ["HARVEST"]  # at the cap: no further accumulation is possible
    if not data["ongoing"] and age > data["max_yield_day"]:
        # Past the inclusive watering window, and decaying from today.
        return ["HARVEST"]
    return None


def tally(trace):
    """Count the conversions in a ``trace`` list by verb."""
    counts = collections.Counter()
    for entry in trace or []:
        if (isinstance(entry, (list, tuple)) and len(entry) >= 4
                and entry[0] == "fill"):
            counts[entry[3]] += 1
    return counts


# --------------------------------------------------------------------------- #
# The overlay.
# --------------------------------------------------------------------------- #

class FillIdleOverlay:
    """Wrap a tape agent and fill its PASS turns with safe work under the unit.

    The class is the testable seam: it accepts an injected ``base_agent`` so
    tests can drive it with synthetic observations, and a ``trace`` list for the
    conversion tally.
    """

    def __init__(self, base_agent=None, enabled=True, trace=None):
        self.enabled = bool(enabled)
        self.trace = trace if trace is not None else []
        # A private copy: the tape keeps season state in a module-level
        # singleton, so sharing one import would let two agents corrupt each
        # other (see the same warning in v10_partial_quadrant).
        self.base = base_agent if base_agent is not None else load_agent(str(_BASELINE))

    def _note(self, *event):
        if self.trace is not None:
            self.trace.append(event)

    def _fill(self, step, index, unit, positions, tiles, inventories, day):
        """One unit's action: the tape's, unless it is a PASS with work under it."""
        if not isinstance(unit, (list, tuple)):
            return unit
        if list(unit) != ["PASS"]:
            return list(unit)
        pos = positions[index] if index < len(positions) else None
        wheat = _as_int(carried(inventories, index).get("WHEAT", 0))
        work = safe_work(tile_at(tiles, pos), day, wheat)
        if work is None:
            return ["PASS"]
        self._note("fill", step, index, work[0])
        return work

    def augment(self, obs, action):
        """The whole transform: only PASS slots change, the market is untouched."""
        step = current_step(obs)
        farm = _farm_of(obs)
        tiles = farm.get("tiles") or []
        day = _day_of(obs, step)
        inventories = _inventories_of(obs)
        positions = [farm.get("farmer")] + list(farm.get("hands") or [])

        units = [action.get("farmer")] + list(action.get("hands") or [])
        filled = [
            self._fill(step, index, unit, positions, tiles, inventories, day)
            for index, unit in enumerate(units)
        ]

        raw_market = action.get("market")
        market = ([list(o) if isinstance(o, (list, tuple)) else o for o in raw_market]
                  if isinstance(raw_market, (list, tuple)) else [])

        return {
            "farmer": filled[0] if filled else ["PASS"],
            "hands": filled[1:],
            "market": market,
        }

    def __call__(self, obs):
        try:
            action = self.base(obs)
        except Exception:
            action = _fallback_action(obs)
        if not isinstance(action, dict):
            return action
        if not self.enabled:
            return action
        try:
            return self.augment(obs, action)
        except Exception:
            # A malformed observation must never take the agent out of the
            # season. The untouched tape action is always legal.
            return action


def make_agent(base_agent=None, enabled=True, trace=None):
    """Build an ``agent(obs)`` that fills the tape's idle turns.

    ``enabled=False`` is a full pass-through, so it reproduces the bare tape
    exactly (the identity check the task requires).
    """
    overlay = FillIdleOverlay(base_agent=base_agent, enabled=enabled, trace=trace)

    def agent(obs):
        return overlay(obs)

    return agent


#: Default agent: the V8 tape with idle filling on.
agent = make_agent()
