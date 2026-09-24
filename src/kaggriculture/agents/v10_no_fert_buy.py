"""V10 wave 8: play the V8 tape with its fertilizer PURCHASES removed.

The lever, and why it is not the fertilizer lever we already closed:

  * The ABLATION (cancelling the tape's 97 FERTILIZE actions) lost $3,078/game,
    verified. Verdict: the tape's fertilizing is productive. That is closed.
  * This overlay removes the 66 ``BUY_PRODUCT FERTILIZER`` orders per game, which
    no experiment has ever isolated.

Why the buys look like a leak (measured, per game): the tape collects ~382
fertilizer units, applies 97 and sells 333, so 82% of the bought volume is not
needed to cover the applications. The town never consumes fertilizer, our own
sales drive it to the floor, and its measured marginal sale value is ~$5/unit.
Buying a unit near the $100 base and selling it into a glutted market is a
negative round trip; the one thing that can justify the purchase is an extra
APPLICATION, worth ~+$32 measured. The measurement, not this reasoning, decides.

Engine facts the transform relies on (all from kaggriculture.py):

  * ``BUY_PRODUCT`` is accepted for exactly ``WHEAT`` and ``FERTILIZER``
    (:598), and buying quotes at post-buy inventory (:601), so a buy/sell
    round-trip against an unchanged market nets zero.
  * ``maxMarketOrdersPerTurn = 10`` and extras are silently dropped (:551-563).
    Removing orders can only free slots, so the tape's own later orders - the
    wheat buys that follow in the same queue - move UP into slots they previously
    lost. That is a real behavioural channel and the reason the smoke run reports
    the animal and quadrant counts.
  * ``SELL FERTILIZER`` is a different order and is NEVER touched here. Dropping
    the buys is not the same as selling less, and anything unsold is worth zero.

Like every other V10 overlay this is a pure payload transform: nothing is
rescheduled, so hand positions stay valid by construction.
"""

from pathlib import Path

from kaggriculture.loading import current_step as _current_step
from kaggriculture.loading import load_agent

BUY_VERB = "BUY_PRODUCT"
DROPPED_ITEM = "FERTILIZER"
_BASELINE = Path(__file__).with_name("baseline_v8.py")


def _drop_buys(orders):
    """Remove every ``BUY_PRODUCT FERTILIZER`` order, keeping the rest in order."""
    kept = []
    for order in orders or []:
        if (
            isinstance(order, (list, tuple))
            and len(order) > 1
            and order[0] == BUY_VERB
            and order[1] == DROPPED_ITEM
        ):
            continue
        kept.append(list(order) if isinstance(order, (list, tuple)) else order)
    return kept


def make_agent(from_step=0):
    """Build an ``agent(obs)`` that plays the V8 tape without fertilizer buys.

    ``from_step`` defaults to 0 because the change ADDS cash and the tape's
    cash-coupled economy makes an early cash delta the dangerous direction only
    when it REMOVES cash. It is still a parameter: the roster swaps needed
    ``from_step=150`` because extra money can turn a previously failed ``HIRE``
    into a success and desynchronise the hand choreography, so a variant that
    starts late must stay cheap to measure.
    """
    if from_step < 0:
        raise ValueError("from_step cannot be negative")
    # A private copy: the baseline keeps its agent state in a module-level
    # singleton, so sharing one import would let two agents corrupt each other.
    base_agent = load_agent(str(_BASELINE))

    def agent(obs):
        action = base_agent(obs)
        if not isinstance(action, dict) or _current_step(obs) < from_step:
            return action
        return {
            "farmer": action.get("farmer"),
            "hands": action.get("hands"),
            "market": _drop_buys(action.get("market")),
        }

    return agent


agent = make_agent()
