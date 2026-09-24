"""V10 crop-swap overlay: convert a BOUNDED number of WHEAT plantings to TOMATO.

Why tomato (measurement, not taste). The published traces and curves give:
  * production per wheat planting ~11 units worth a ~+$24 marginal margin at I0;
  * production per tomato planting ~8-13 units worth a ~+$54 marginal margin at I0;
  * the town drains ~266 tomato units a season (no shop demands melon, but four
    shop types and the centre drain tomato) and our tape plants **zero** tomato.
So filling that absorption costs roughly 24 wheat plantings and is worth roughly
+$8k per game. An earlier session had already noticed "our realised TOMATO revenue
is $0 across 310 matches" and never acted on it.

The swap uses the SAME tiles and the SAME hands, so it is overlay-compatible in the
way the animal roster swap was: it rewrites worker `PLANT WHEAT` payloads to
`PLANT TOMATO` and `BUY_SEED WHEAT` market orders to `BUY_SEED TOMATO`.

Two guards, both from measured failure modes:

* `max_swaps` (target ~24 plantings). Wheat seeds cost $10 and tomato $50, so
  converting all ~170 wheat plantings would add about **+$6.8k of seed cash**. The
  tape's early economy is cash-coupled - the sells in a turn fund the buys that
  follow them in the same market queue - and defunding that queue is exactly what
  produced the sell-chunk experiment's 0W-24L. 24 plantings cost only ~$960 more,
  and 24 x ~11 units is about the 266 units of tomato absorption being targeted, so
  the bound is both a cash guard and the economic target.
* `from_step`: leave the opening alone, the protection the animal swaps needed
  (they had to start at step 150, because an early cost change desynchronised the
  whole hand choreography).

Caveat the experiment exists to answer: the swap changes the crop's lifetime cadence
(tomato's first yield is day 8 with interval 1 and 4 held; wheat yields in the window
days 2-4 with a cap of 6), so the tape's watering and harvest rhythm may not
monetise it at all.
"""

from collections.abc import Callable
from pathlib import Path

from kaggriculture.loading import load_agent

_BASELINE = Path(__file__).with_name("baseline_v8.py")


def _current_step(obs):
    raw = obs.get("step")
    if raw is not None:
        return int(raw)
    return int(obs.get("day", 0)) * 24 + int(obs.get("hour", 0))


def make_agent(
    source: str = "WHEAT",
    target: str = "TOMATO",
    max_swaps: int = 24,
    from_step: int = 0,
) -> Callable[[dict], dict]:
    """Play the V8 tape with up to ``max_swaps`` plantings moved to ``target``."""
    if max_swaps < 0:
        raise ValueError("max_swaps cannot be negative")
    if source == target:
        raise ValueError("source and target must differ")
    # A private copy: the baseline keeps its agent state in a module-level singleton,
    # so sharing one import would let two agents in the same process corrupt each other.
    base_agent = load_agent(str(_BASELINE))
    state = {"plantings": 0, "seeds": 0}

    def agent(obs: dict) -> dict:
        action = base_agent(obs)
        if not isinstance(action, dict):
            # The engine expects an action dict. The baseline's own guard already
            # degrades to a PASS, so this is belt-and-braces normalization - and it
            # is what lets this function honestly promise a dict back.
            return {"farmer": ["PASS"], "hands": [], "market": []}
        if _current_step(obs) < from_step:
            return action

        workers = [action.get("farmer")] + list(action.get("hands") or [])
        converted = []
        for worker in workers:
            if (
                state["plantings"] < max_swaps
                and isinstance(worker, (list, tuple))
                and len(worker) > 1
                and worker[0] == "PLANT"
                and worker[1] == source
            ):
                # Keep the VERB. Emitting [target, ...] instead of
                # [worker[0], target, ...] sends an unknown verb that the engine
                # silently ignores: 24 plantings vanished and the farm collapsed by
                # $72,251 in the first smoke run. This is the same bug the animal
                # swap shipped and tests/test_v10_mix.py pins; it recurred here
                # because this module was written fresh instead of reusing that
                # transform, so tests/test_v10_tomato.py now pins it too.
                converted.append([worker[0], target, *worker[2:]])
                state["plantings"] += 1
            else:
                converted.append(worker)

        market = []
        for order in action.get("market") or []:
            if (
                isinstance(order, (list, tuple))
                and len(order) > 2
                and order[0] == "BUY_SEED"
                and order[1] == source
            ):
                want = int(order[2])
                take = max(0, min(want, max_swaps - state["seeds"]))
                if take:
                    # Split the order so the total seed count is unchanged and only
                    # the budgeted part moves to the target crop.
                    market.append(["BUY_SEED", target, take, *list(order[3:])])
                    state["seeds"] += take
                if want - take > 0:
                    market.append(["BUY_SEED", source, want - take, *list(order[3:])])
            else:
                market.append(order)

        return {
            "farmer": converted[0],
            "hands": converted[1:],
            "market": market,
        }

    return agent


agent = make_agent()
