#!/usr/bin/env python
"""Replay-level assertions for tape overlays (V10 T2).

An overlay that never lands is INVISIBLE to the benchmark: the agent just plays
the baseline tape, and the run looks like an unlucky variant instead of a bug.
The failure mode is the silent no-op - `_noop` (`baseline_v9_1.py:448`) swallows
an invalid action, and an edit aimed at the wrong turn or the wrong order key is
swallowed the same way.

METHOD - why the control runs at the SAME seat. Comparing seat 0 against seat 1
in one game conflates the overlay with the engine's seat-ordered asymmetry,
which T1 measured as real and seed-dependent (up to $696 per game, and it flips
sign with the seat). So this checker runs two matches and compares like with
like:

    run A: variant  on seat 0 vs baseline on seat 1
    run B: baseline on seat 0 vs baseline on seat 1   <- control

Both runs put the agent under test on seat 0 against the SAME opponent, which is
a tape and does not read the opponent, so the seat-1 side behaves identically in
both runs and the engine's seat bias cancels. Any difference in the seat-0 trace
is therefore caused by the overlay.

Assertions:

  1. the variant's season roster must differ from the control's; an identical
     trace means the edit never landed
  2. `--expect TYPE=N` must match the variant's PEAK count of TYPE
  3. no ESCAPES among the expected animal types

Starvation rule (authoritative, `kaggriculture.py:805-832`): at the end of each
day an unfed animal gets `consecutive_unfed += 1` and a fed one resets to 0; at
`>= 2` the animal escapes, the tile reverts to the empty structure, and the
purchase is lost. `fed_today` is reset in that same pass, so reading it on the
LAST turn of a day says whether the animal ate during that day.

Usage:
  .venv/bin/python scripts/assert_overlay.py <variant.py> <baseline.py> \\
      [--seed 0] [--expect GOOSE=6]

Exit code 0 = all assertions passed, 1 = at least one failed.
"""

import argparse
import collections
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from kaggriculture.loading import load_agent  # noqa: E402
from kaggriculture.runner import run_episode_trace  # noqa: E402

TURNS_PER_DAY = 24
ANIMAL_TYPES = ("COW", "SHEEP", "GOOSE")
STRUCTURES = ("PASTURE", "COOP")
ORDER_VERBS = ("BUY_ANIMAL", "BUILD_COOP", "BUILD_PASTURE")


def _farm(step):
    obs = step["observation"]
    return obs["farms"][obs["player"]]


def _census(farm):
    """Animal and structure counts for one farm snapshot."""
    animals = collections.Counter()
    structures = collections.Counter()
    for row in farm.get("tiles", []):
        for tile in row:
            if not isinstance(tile, dict):
                continue
            if tile.get("kind") in STRUCTURES:
                structures[tile["kind"]] += 1
                if tile.get("animal"):
                    animals[tile["animal"]] += 1
    return animals, structures


def _unfed(farm):
    """Animals that have not eaten during the day being observed."""
    return sum(
        1
        for row in farm.get("tiles", [])
        for tile in row
        if isinstance(tile, dict) and tile.get("animal") and not tile.get("fed_today")
    )


def _daily_trace(steps, seat):
    """One row per day: day-start roster plus day-end feeding and money."""
    rows = []
    last = len(steps) - 1
    day = 0
    while day * TURNS_PER_DAY <= last:
        start = min(day * TURNS_PER_DAY, last)
        end = min(day * TURNS_PER_DAY + TURNS_PER_DAY - 1, last)
        animals_start, structures_start = _census(_farm(steps[start][seat]))
        animals_end, structures_end = _census(_farm(steps[end][seat]))
        farm_end = _farm(steps[end][seat])
        rows.append({
            "day": day,
            "money": farm_end.get("money", 0),
            "animals_start": animals_start,
            "animals_end": animals_end,
            "structures_start": structures_start,
            "structures_end": structures_end,
            "unfed_end": _unfed(farm_end),
        })
        day += 1
    return rows


def _roster_signature(trace):
    """The comparable part of a season: roster and structures, day by day."""
    return [
        (
            tuple(sorted(row["animals_start"].items())),
            tuple(sorted(row["structures_start"].items())),
        )
        for row in trace
    ]


def _first_divergence(variant_trace, control_trace):
    """Day where the two seat-0 traces first differ, or None if identical."""
    for v_row, c_row in zip(variant_trace, control_trace, strict=True):
        if (v_row["animals_start"] != c_row["animals_start"]
                or v_row["structures_start"] != c_row["structures_start"]):
            return v_row["day"]
    return None


def _peak(trace, name):
    return max((row["animals_start"].get(name, 0) for row in trace), default=0)


def _peak_structures(trace, name):
    return max((row["structures_start"].get(name, 0) for row in trace), default=0)


def _escapes(trace, name):
    """Census drops for `name` between consecutive day starts = escaped animals."""
    total = 0
    previous = 0
    for row in trace:
        current = row["animals_start"].get(name, 0)
        if current < previous:
            total += previous - current
        previous = current
    return total


def _market_orders(steps, seat):
    counts = collections.Counter()
    for step in steps:
        action = step[seat].get("action")
        orders = action.get("market") if isinstance(action, dict) else action
        for order in orders or []:
            if isinstance(order, (list, tuple)) and order:
                key = str(order[0])
                if key in ORDER_VERBS and len(order) > 1:
                    key = f"{key}:{order[1]}"
                counts[key] += 1
    return counts


def _order_signature(steps, seat):
    """The exact market-order stream, turn by turn, for one seat.

    Needed because a roster signature alone cannot see a market-only overlay
    (for example the JIT melon seed timing in v9/V9.1): the farm looks identical
    while the order stream differs. Both runs must be at the same seat for this
    comparison to be meaningful.
    """
    signature = []
    for step in steps:
        action = step[seat].get("action")
        orders = action.get("market") if isinstance(action, dict) else action
        signature.append(
            tuple(
                tuple(order) if isinstance(order, (list, tuple)) else (order,)
                for order in (orders or [])
            )
        )
    return tuple(signature)


def _first_order_divergence(variant_sig, control_sig):
    """Turn index where the two order streams first differ, or None."""
    for turn, (v_orders, c_orders) in enumerate(zip(variant_sig, control_sig, strict=True)):
        if v_orders != c_orders:
            return turn
    return None


def _fmt_animals(counter):
    shown = " ".join(f"{a}:{counter[a]}" for a in ANIMAL_TYPES if counter.get(a))
    return shown or "-"


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("variant", help="the overlay agent under test (.py)")
    parser.add_argument("baseline", help="the reference tape (.py)")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument(
        "--expect",
        action="append",
        default=[],
        help="TYPE=N peak expectation, e.g. --expect GOOSE=6 or --expect COOP=10",
    )
    args = parser.parse_args()

    expectations = {}
    for item in args.expect:
        if "=" not in item:
            parser.error(f"--expect needs TYPE=N, got {item!r}")
        name, value = item.split("=", 1)
        expectations[name.strip().upper()] = int(value)

    variant_agent = load_agent(args.variant)
    baseline_agent = load_agent(args.baseline)

    run_a = run_episode_trace(variant_agent, baseline_agent, args.seed)  # variant on seat 0
    run_b = run_episode_trace(baseline_agent, baseline_agent, args.seed)  # control, same seat

    v_trace = _daily_trace(run_a["steps"], 0)
    c_trace = _daily_trace(run_b["steps"], 0)
    v_orders = _market_orders(run_a["steps"], 0)
    c_orders = _market_orders(run_b["steps"], 0)
    v_sig = _order_signature(run_a["steps"], 0)
    c_sig = _order_signature(run_b["steps"], 0)

    print(f"seed {args.seed} | seat 0 of both runs compared "
          f"(opponent on seat 1 = {args.baseline})")
    print(f"{'day':>3} | {'VAR animals':<18} {'unf':>3} {'money':>8} | "
          f"{'CTRL animals':<18} {'unf':>3} {'money':>8} | {'':<6}")
    print("-" * 84)
    for v_row, c_row in zip(v_trace, c_trace, strict=True):
        differs = (v_row["animals_start"] != c_row["animals_start"]
                   or v_row["structures_start"] != c_row["structures_start"])
        print(f"{v_row['day']:>3} | {_fmt_animals(v_row['animals_end']):<18} "
              f"{v_row['unfed_end']:>3} {v_row['money']:>8,.0f} | "
              f"{_fmt_animals(c_row['animals_end']):<18} {c_row['unfed_end']:>3} "
              f"{c_row['money']:>8,.0f} | {'DIFF' if differs else '':<6}")

    print("-" * 84)
    for key in sorted(set(v_orders) | set(c_orders)):
        if key.startswith(ORDER_VERBS):
            mark = "" if v_orders[key] == c_orders[key] else "  <-- differs"
            print(f"orders {key:<24} VAR {v_orders[key]:>4} | CTRL {c_orders[key]:>4}{mark}")

    v_peak = {a: _peak(v_trace, a) for a in ANIMAL_TYPES}
    c_peak = {a: _peak(c_trace, a) for a in ANIMAL_TYPES}
    print(f"peak animals    VAR {v_peak} | CTRL {c_peak}")
    print(f"peak structures VAR {_peak_structures(v_trace, 'COOP')} COOP / "
          f"{_peak_structures(v_trace, 'PASTURE')} PASTURE | CTRL "
          f"{_peak_structures(c_trace, 'COOP')} COOP / "
          f"{_peak_structures(c_trace, 'PASTURE')} PASTURE")
    divergence = _first_divergence(v_trace, c_trace)
    order_divergence = _first_order_divergence(v_sig, c_sig)
    print(f"first roster divergence: "
          f"{'day ' + str(divergence) if divergence is not None else 'NONE'}")
    print(f"first order divergence:  "
          f"{'turn ' + str(order_divergence) if order_divergence is not None else 'NONE'}")

    problems = []
    roster_same = _roster_signature(v_trace) == _roster_signature(c_trace)

    # 1. The overlay must actually change SOMETHING observable. Both runs share
    #    the same seat and the same opponent, so a swallowed edit reproduces the
    #    control run exactly - roster AND order stream.
    if roster_same and v_sig == c_sig:
        problems.append(
            "OVERLAY NEVER LANDED: the season roster and the full market-order "
            "stream are identical to the control run, so the edit was swallowed"
        )
    elif roster_same:
        print("NOTE: roster unchanged - this overlay's effect is market/order-side "
              "only, so a roster expectation cannot be met")

    # 2. Explicit expectations.
    for name, want in expectations.items():
        if name in ANIMAL_TYPES:
            got = v_peak.get(name, 0)
        elif name in STRUCTURES:
            got = _peak_structures(v_trace, name)
        else:
            problems.append(f"EXPECT {name}: unknown type (not an animal or structure)")
            continue
        if got != want:
            problems.append(f"EXPECT {name}={want} but the variant's peak was {got}")

    # 3. An escape is a lost purchase: two consecutive unfed days.
    for name in sorted(set(expectations) & set(ANIMAL_TYPES)):
        lost = _escapes(v_trace, name)
        if lost:
            problems.append(f"ESCAPES: the variant lost {lost} {name} to starvation")

    print("-" * 84)
    if problems:
        print(f"FAIL ({len(problems)} problem(s)):")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print("PASS: overlay landed, expectations met, no escapes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
