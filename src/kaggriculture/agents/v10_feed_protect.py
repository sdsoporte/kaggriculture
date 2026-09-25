"""V10 feed-protection overlay: one appended hand that keeps the herd fed.

The leak this closes
--------------------
Measured over our 40 real ladder episodes (``scratch/ours_new``, the field we are
actually matched with), the win rate splits cleanly on a single variable:

    games where we lost an animal    2/ 7  = ..   (with escapes)
    games with no animal loss       13/33  = ..

An animal that goes two consecutive midnights without eating escapes and the $300
to $500 purchase is gone (``kaggriculture.py:805-833``). The escapes are pure
logistics: at the moment an animal has already missed one day, there is wheat in
the SHED and zero WHEAT in the workers' HANDS, and the tape's open-loop hand
route never carries it to the tile in time.

Why an appended hand, and why the tape stays byte-identical
----------------------------------------------------------
``baseline_v8`` is an open-loop route replay: it emits one action per hand its
route knows about, whatever the live roster size. Overriding one of those slots
would move a tape hand off its route. This overlay instead appends a ``HIRE`` to
the tape's own market list and drives ONLY the hand that order appends, which is
the safe design the module is built around:

* our ``HIRE`` is appended after every tape order, so the engine (which resolves
  HIRE in market order) creates OUR hand after all of that turn's tape hands;
* we only append on a turn where the tape emits no ``HIRE`` at all, so the hand
  we own is at a known absolute index: ``len(roster)`` at the turn we order it.
  Ties are impossible and the ownership proof is exact rather than a heuristic;
* the tape's farmer action, its non-owned hand actions and every market order it
  produced are passed through untouched. With ``extra_hands=0`` the overlay is a
  complete pass-through and reproduces the bare tape action stream byte for byte
  (``tests/test_v10_feed_protect.py::test_zero_extra_hands_is_byte_identical_to_tape``).

The feeding chain
-----------------
``FEED`` consumes a WHEAT the WORKER carries (``_apply_unit_action`` /
``_inv_take``); there is no remote feeding and no shed-side feeding. The extra
hand therefore runs the same carry chain ``param_agent`` documents:

  1. carrying WHEAT and an animal is unfed -> walk to it and ``FEED``;
  2. not carrying WHEAT, an animal is unfed and the shed holds WHEAT ->
     walk to a shed-access tile and ``PICKUP WHEAT``;
  3. otherwise ``PASS``.

The target is the nearest unfed animal, preferring one that has already gone a
day unfed (``consecutive_unfed >= 1``) because that animal escapes TONIGHT if it
is missed, while a freshly placed one still has a day of slack.

What the overlay deliberately does NOT do
-----------------------------------------
It buys no wheat and changes no sell, seed, animal or land order. The tape's
economy is cash-coupled and spending its money on anything but the one extra hand
is how earlier variants (the tomato overlay) missed the step-150 land cliff. The
hand is a pure logistics add-on to the herd the tape already owns.
"""

from __future__ import annotations

from pathlib import Path

from kaggle_environments.envs.kaggriculture.kaggriculture import FARMER_MOVES

from kaggriculture.loading import current_step, load_agent

_BASELINE = Path(__file__).with_name("baseline_v8.py")

BOARD = 10
TURNS_PER_DAY = 24
MAX_ORDERS = 10  # engine: maxMarketOrdersPerTurn, extras silently dropped

# Engine movement verbs are the inverse of what routing needs, so invert the
# imported table rather than retyping it (kaggriculture.py:88-93).
_STEP_VERB = {delta: verb for verb, delta in FARMER_MOVES.items()}


def _as_int(value, default=0):
    """Non-raising int coercion: a malformed field degrades, never raises."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_float(value, default=0.0):
    """Non-raising float coercion for cash/inventory fields."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


# --------------------------------------------------------------------------- #
# Pure geometry / herd helpers. No engine state, cheap to test.
# --------------------------------------------------------------------------- #

def shed_access_tiles(board=BOARD):
    """The four inner-corner tiles around the shed (kaggriculture.py:132-136)."""
    h = board // 2
    return [(h - 1, h - 1), (h, h - 1), (h - 1, h), (h, h)]


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


def unfed_animals(farm):
    """Every animal tile that has not eaten today, as ``(pos, consecutive_unfed)``.

    Sorted deterministically (row, then column) so the choice is stable across a
    turn and readable in a trace. ``fed_today`` is reset at every midnight, so at
    the start of a day this is the whole herd; through the day it shrinks as the
    tape and the extra hand feed it.
    """
    tiles = farm.get("tiles") if isinstance(farm, dict) else None
    if not isinstance(tiles, (list, tuple)):
        return []
    out = []
    for y, row in enumerate(tiles):
        if not isinstance(row, (list, tuple)):
            continue
        for x, cell in enumerate(row):
            if not isinstance(cell, dict) or not cell.get("animal"):
                continue
            if cell.get("fed_today", False):
                continue
            out.append(((x, y), _as_int(cell.get("consecutive_unfed", 0))))
    return out


def at_risk_animals(farm, min_unfed=1):
    """Unfed animals that have already missed at least ``min_unfed`` days.

    These escape at tonight's refresh unless they eat today; a ``min_unfed`` of 0
    would simply mean "any unfed animal".
    """
    return [(pos, n) for pos, n in unfed_animals(farm) if n >= min_unfed]


def choose_feed_target(pos, targets, claimed):
    """Nearest unclaimed animal to feed, preferring one already a day unfed.

    The tie-break order is ``(risk, distance, y, x)``: risk first because feeding
    the already-hungry animal is what prevents tonight's escape, distance second
    because the carry chain is turn-limited, coordinates last for determinism.
    """
    best = None
    for tile, consecutive_unfed in targets:
        if tile in claimed:
            continue
        risk = 0 if consecutive_unfed >= 1 else 1
        key = (risk, abs(pos[0] - tile[0]) + abs(pos[1] - tile[1]), tile[1], tile[0])
        if best is None or key < best[0]:
            best = (key, tile)
    return best[1] if best is not None else None


def nearest_shed_tile(pos, claimed, board=BOARD):
    """Nearest unclaimed shed-access tile, or None when all are claimed."""
    options = [t for t in shed_access_tiles(board) if t not in claimed]
    if not options:
        return None
    return min(options, key=lambda t: (abs(pos[0] - t[0]) + abs(pos[1] - t[1]), t[1], t[0]))


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
        private = obs.get("private")
        return private if isinstance(private, dict) else {}
    except AttributeError:
        return {}


def _hands_of(farm):
    hands = farm.get("hands") if isinstance(farm, dict) else None
    return list(hands) if isinstance(hands, (list, tuple)) else []


def _fallback_action(obs):
    """A legal do-nothing action matching the live roster length."""
    try:
        hands = _hands_of(_farm_of(obs))
    except Exception:
        hands = []
    return {"farmer": ["PASS"], "hands": [["PASS"] for _ in hands], "market": []}


# --------------------------------------------------------------------------- #
# The overlay.
# --------------------------------------------------------------------------- #

class FeedProtectOverlay:
    """Wrap a tape agent and append one hand dedicated to feeding the herd.

    The class is the testable seam: it owns the per-day bookkeeping (which hand
    index is ours, whether a HIRE is in flight) and the feeding carry chain, and
    accepts an injected ``base_agent`` so tests can drive it with synthetic
    observations without a season.
    """

    def __init__(
        self,
        extra_hands=1,
        base_agent=None,
        trace=None,
        latest_hire_hour=22,
        hire_min_consecutive_unfed=1,
    ):
        if not isinstance(extra_hands, int) or isinstance(extra_hands, bool) or extra_hands < 0:
            raise ValueError("extra_hands must be a non-negative int")
        if not 0 <= latest_hire_hour <= TURNS_PER_DAY:
            raise ValueError("latest_hire_hour must be in [0, 24]")
        if not isinstance(hire_min_consecutive_unfed, int) or hire_min_consecutive_unfed < 0:
            raise ValueError("hire_min_consecutive_unfed must be a non-negative int")

        self.extra_hands = extra_hands
        self.latest_hire_hour = latest_hire_hour
        self.hire_min_consecutive_unfed = hire_min_consecutive_unfed
        self.trace = trace if trace is not None else []
        # A private copy: the tape keeps season state in a module-level singleton
        # (`baseline_v8._A`), so sharing one import would let two seats corrupt
        # each other.
        self.base = base_agent if base_agent is not None else load_agent(str(_BASELINE))
        self._reset()

    # ---- per-day bookkeeping ------------------------------------------------

    def _reset(self):
        self.owned = set()       # hand indices we own in the current day
        self.pending_index = None  # absolute index of a HIRE ordered last turn
        self.hire_events = []    # (day, hour, index, cost) for the labour report

    def _note(self, *event):
        if self.trace is not None:
            self.trace.append(event)

    def _resolve_pending(self, roster_len):
        """Confirm the hand our HIRE appended last turn, or allow a retry.

        Our HIRE is only ever appended on a turn where the tape emits no HIRE, so
        it is the sole hire of that turn and the engine appends it at exactly the
        roster length observed then. If the roster did not grow, the order was
        rejected (cash short) and we are free to order again.
        """
        if self.pending_index is None:
            return
        if roster_len > self.pending_index:
            self.owned.add(self.pending_index)
            self._note("own_hand", self.pending_index)
        else:
            self._note("hire_failed", self.pending_index)
        self.pending_index = None

    # ---- market side: append exactly one HIRE, touch nothing else -----------

    def _should_hire(self, farm, hour, market, roster):
        if self.extra_hands <= 0:
            return False
        if self.pending_index is not None:
            return False
        if len(self.owned) >= self.extra_hands:
            return False
        if hour >= self.latest_hire_hour:
            # A hand hired at h23 is appended, then wiped by the midnight roster
            # reset before it can act: pure waste.
            return False
        if len(market) >= MAX_ORDERS:
            return False
        # Ownership exactness: if the tape hires this very turn, the number of
        # hands that actually land is a function of the tape's cash and cannot be
        # predicted, so our hand's index would be ambiguous. Wait for a clean turn.
        for order in market:
            if isinstance(order, (list, tuple)) and order and order[0] == "HIRE":
                return False
        targets = unfed_animals(farm)
        if not targets:
            return False
        return self.hire_min_consecutive_unfed <= 0 or any(
            n >= self.hire_min_consecutive_unfed for _, n in targets
        )

    # ---- worker side: the feeding carry chain for our own hand -------------

    def _feed_action(self, pos, inventory, targets, shed_wheat, claimed):
        """One action plus the tile it commits to, for an unfed-herd hand."""
        if pos is None:
            return ["PASS"], None
        target = choose_feed_target(pos, targets, claimed)
        if target is None:
            return ["PASS"], None
        if _as_int(inventory.get("WHEAT", 0)) > 0:
            if tuple(pos) == target:
                return ["FEED"], target
            return [step_toward(tuple(pos), target) or "PASS"], target
        if shed_wheat > 0:
            spot = nearest_shed_tile(tuple(pos), claimed)
            if spot is None:
                return ["PASS"], None
            if tuple(pos) == spot:
                # Seeds never pass through the shed, but WHEAT does, so PICKUP
                # without an item is a silent no-op; never emit it bare.
                return ["PICKUP", "WHEAT"], None
            return [step_toward(tuple(pos), spot) or "PASS"], spot
        return ["PASS"], None

    def _drive_hands(self, obs, action, farm, roster):
        """Return the hands list with ONLY the indices we own rewritten."""
        hands_out = [list(hand) if isinstance(hand, (list, tuple)) else ["PASS"]
                     for hand in (action.get("hands") or [])]
        if not self.owned:
            return hands_out
        needed = max(self.owned) + 1
        while len(hands_out) < needed:
            hands_out.append(["PASS"])

        private = _private_of(obs)
        inventories = private.get("inventories")
        inventories = inventories if isinstance(inventories, (list, tuple)) else []
        shed = private.get("shed")
        shed = shed if isinstance(shed, dict) else {}
        shed_wheat = _as_int(shed.get("WHEAT", 0))
        targets = unfed_animals(farm)
        claimed = set()

        for index in sorted(self.owned):
            if index >= len(roster):
                continue
            pos = roster[index]
            if isinstance(pos, (list, tuple)) and len(pos) == 2:
                pos = (_as_int(pos[0]), _as_int(pos[1]))
            else:
                pos = None
            inventory = inventories[index + 1] if index + 1 < len(inventories) else {}
            if not isinstance(inventory, dict):
                inventory = {}
            hand_action, target = self._feed_action(
                pos, inventory, targets, shed_wheat, claimed
            )
            hands_out[index] = hand_action
            if target is not None:
                claimed.add(target)
            self._note("act", index, tuple(hand_action),
                       tuple(target) if target is not None else None)
        return hands_out

    # ---- the tape-preserving transform -------------------------------------

    def augment(self, obs, action):
        """The tape-preserving transform. Pure with respect to the tape's action."""
        step = current_step(obs)
        if step == 0 or step % TURNS_PER_DAY == 0:
            # The engine resets the roster every midnight (kaggriculture.py:876-888),
            # so any hand we owned is gone and must be re-hired.
            self._reset()
        farm = _farm_of(obs)
        roster = _hands_of(farm)
        self._resolve_pending(len(roster))
        hour = step % TURNS_PER_DAY
        day = step // TURNS_PER_DAY

        market = [list(order) for order in (action.get("market") or [])]
        if self._should_hire(farm, hour, market, roster):
            market.append(["HIRE"])
            self.pending_index = len(roster)
            cost = fib_hire_cost(_as_int(farm.get("hires_today", 0)))
            self.hire_events.append((day, hour, self.pending_index, cost))
            self._note("hire", day, hour, self.pending_index, cost)

        hands_out = self._drive_hands(obs, action, farm, roster)
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
    base_agent=None,
    trace=None,
    latest_hire_hour=22,
    hire_min_consecutive_unfed=1,
):
    """Build an ``agent(obs)`` adding a feed-protection hand to the V8 tape.

    ``extra_hands=0`` is a full pass-through, so it reproduces the bare tape
    exactly (the identity check the task requires).
    """
    overlay = FeedProtectOverlay(
        extra_hands=extra_hands,
        base_agent=base_agent,
        trace=trace,
        latest_hire_hour=latest_hire_hour,
        hire_min_consecutive_unfed=hire_min_consecutive_unfed,
    )

    def agent(obs):
        return overlay(obs)

    return agent


# Default configuration measured by the task: one extra hand, hired only while an
# animal has already gone a day unfed.
agent = make_agent()
