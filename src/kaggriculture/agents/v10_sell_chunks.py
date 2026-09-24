"""V10 phase C: sell-lot chunking overlay on the V8 tape.

Measured mechanism (see `odd/tasks/v10-sell-policy.md`): the top teams sell
**3.8 units per sale point over 52.8 points/game**; we sell **10.6 units over
23.5 points/game**. The engine re-quotes the price PER UNIT as a sale executes,
so a big lot walks its own price down. Inside our own games, strawberry lots of
<=10 units fetch $125.3/unit against $86.3/unit for bigger lots (-31%).

This overlay throttles every SELL order to a small chunk, banks the remainder in
a per-product backlog, and spends spare market slots on that backlog so the same
volume lands across more turns. Nothing else is touched: production, hands,
structures and the tape's own order positions are unchanged.

Safety rules, each tied to a measured engine fact:
  * `maxMarketOrdersPerTurn = 10` and extras are silently dropped - backlog sells
    are only appended while the turn has a spare slot, so the tape's own orders
    (including the wheat buys) are never pushed out of the list.
  * no end-of-season liquidation exists, so the cap self-relaxes as turns run out
    (`ceil(pending / turns_left)`); by the last turn it dumps whatever is left.
  * the shed holds 100 units and discards overflow, so the backlog is bounded and
    drains faster once it grows past `max_backlog`.
"""

import collections
import math
from pathlib import Path

from kaggriculture.loading import load_agent

MAX_ORDERS = 10  # engine: maxMarketOrdersPerTurn, extras silently dropped
LAST_EFFECTIVE_TURN = 717  # engine: episodeSteps 720, DONE at step >= 718
_BASELINE = Path(__file__).with_name("baseline_v8.py")


def _current_step(obs):
    raw = obs.get("step")
    if raw is not None:
        return int(raw)
    return int(obs.get("day", 0)) * 24 + int(obs.get("hour", 0))


def make_agent(chunk=4, max_backlog=30, from_step=0):
    """Return an ``agent(obs)`` that plays the V8 tape with chunked sells.

    ``from_step`` is NOT optional in practice. The tape's early economy is
    cash-coupled: the sells in a turn fund the buys that follow them in the same
    market queue, so throttling before the animal purchases are done defunds the
    farm outright. Measured with ``from_step=0``: the roster collapsed from 17
    animals to 9 (divergence on day 1) and the paired benchmark went 0W-24L at
    -$61,832. The last animal purchase is at turn 266 (day 11), so throttling only
    safely starts at day 12 - the same early-cash knife-edge that forced the
    roster swaps to start at step 150.
    """
    if chunk < 1:
        raise ValueError("chunk must be at least 1")
    if from_step < 0:
        raise ValueError("from_step cannot be negative")
    # A private copy: the baseline keeps its agent state in a module-level
    # singleton, so sharing one import would let two agents corrupt each other.
    base_agent = load_agent(str(_BASELINE))
    backlog = collections.Counter()

    def agent(obs):
        nonlocal backlog
        step = _current_step(obs)
        if step == 0:
            backlog = collections.Counter()

        action = base_agent(obs)
        if not isinstance(action, dict):
            return action
        market = [list(order) for order in (action.get("market") or [])]
        if step < from_step:
            return {"farmer": action.get("farmer"), "hands": action.get("hands"),
                    "market": market}

        pending = sum(backlog.values())
        turns_left = max(1, LAST_EFFECTIVE_TURN - step)
        # Must clear before the season ends, and drain faster if the backlog could
        # overflow the shed. Both relax the throttle automatically.
        cap = max(chunk, math.ceil(pending / turns_left))
        if pending > max_backlog:
            cap = max(cap, chunk * 3)

        kept = []
        for order in market:
            if len(order) >= 3 and order[0] == "SELL" and isinstance(order[1], str):
                want = int(order[2])
                if want > cap:
                    backlog[order[1]] += want - cap
                    want = cap
                if want > 0:
                    kept.append(["SELL", order[1], want])
            else:
                kept.append(order)

        # Spend spare slots on the banked volume, in the order it was banked.
        for item in list(backlog):
            if len(kept) >= MAX_ORDERS:
                break
            qty = min(backlog[item], cap)
            if qty <= 0:
                continue
            kept.append(["SELL", item, qty])
            backlog[item] -= qty
            if backlog[item] <= 0:
                del backlog[item]

        return {
            "farmer": action.get("farmer"),
            "hands": action.get("hands"),
            "market": kept[:MAX_ORDERS],
        }

    return agent


agent = make_agent(from_step=288)  # day 12: after the last animal purchase (turn 266)
