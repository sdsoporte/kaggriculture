#!/usr/bin/env python
"""Does the fertilizer our tape ALREADY applies buy anything? (cheap test)

An adversarial review of our fertilizer plan suggested patching one redundant water
action into a FERTILIZE on one melon planting. That design has a hole: FERTILIZE
requires the worker to be CARRYING fertilizer (`_inv_take` inside the action), so a
patched water action on a hand with empty pockets is a silent no-op - the exact trap
this project keeps hitting.

This probe inverts the test instead. Our tape already applies ~97 fertilizer units
per game, so suppressing every FERTILIZE answers the same question with no
scheduling at all:

  * if cancelling them changes nothing measurable, the fertilizer we already use
    buys nothing, and a bigger programme would buy nothing either;
  * if cancelling them costs money, the value per application is measurable.

Harvest is measured from the player's own holdings: for crops that are never bought
(MELON, STRAWBERRY, TOMATO, CARROT) an increase in `private.shed +
private.inventories` can only be a harvest. Both seats are covered the paired way:
the variant is measured at whichever seat it plays, against the same opponent and
seed as the control.

Deliberately self-contained (no sibling imports) so it runs standalone and does not
need a sys.path hack before its imports.

Usage:
    .venv/bin/python scripts/fert_probe.py --seeds 4
"""

import argparse
import collections
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from kaggriculture.loading import load_agent  # noqa: E402
from kaggriculture.runner import run_episode_trace  # noqa: E402

BASELINE = REPO / "submissions" / "main_v8.py"
NEVER_BOUGHT = ("MELON", "STRAWBERRY", "TOMATO", "CARROT")
WATER_VERBS = ("SELL", "BUY_PRODUCT", "BUY_ANIMAL", "BUY_SEED", "HIRE", "BUY_LAND")


def holdings(obs):
    """Units of every product held: the shed plus every farmer's inventory."""
    private = obs.get("private") or {}
    held = collections.Counter(private.get("shed") or {})
    for inventory in private.get("inventories") or []:
        held.update(inventory)
    return held


def sell_verbs(action):
    """Which market verbs this action carries."""
    verbs = set()
    if isinstance(action, dict):
        for order in action.get("market") or []:
            if isinstance(order, (list, tuple)) and order:
                verbs.add(str(order[0]))
    return verbs


def make_no_fertilize(base_path):
    """Wrap the tape, turning every FERTILIZE into a PASS."""
    base_agent = load_agent(str(base_path))
    cancelled = collections.Counter()

    def agent(obs):
        action = base_agent(obs)
        if not isinstance(action, dict):
            return action
        workers = [action.get("farmer")] + list(action.get("hands") or [])
        patched = []
        for worker in workers:
            if isinstance(worker, (list, tuple)) and worker and worker[0] == "FERTILIZE":
                cancelled["n"] += 1
                patched.append(["PASS"])
            else:
                patched.append(worker)
        return {"farmer": patched[0], "hands": patched[1:],
                "market": action.get("market") or []}

    return agent, cancelled


def measure(trace, seat):
    """Harvest per never-bought crop plus money flows, for one seat."""
    harvested = collections.Counter()
    revenue = spend = fert_sold = fert_applied = 0
    prev_money = None
    prev_held = None
    for turn in trace:
        entry = turn[seat]   # the replay is steps[turn][seat] - not steps[seat].
        # Getting this backwards is the single most repeated bug in this project:
        # it silently reads the wrong player (or crashes on a list), and it has
        # bitten the top10 analysis, the fertilizer-flow measurement and this probe.
        obs = entry.get("observation") or {}
        farm = (obs.get("farms") or [{}])[obs.get("player", seat)]
        money = farm.get("money", 0)
        held = holdings(obs)
        action = entry.get("action")
        workers = []
        if isinstance(action, dict):
            workers = [action.get("farmer")] + list(action.get("hands") or [])
        fert_applied += sum(1 for worker in workers
                            if isinstance(worker, (list, tuple)) and worker
                            and worker[0] == "FERTILIZE")
        if prev_money is not None and prev_held is not None:
            delta = money - prev_money
            if delta > 0:
                revenue += delta
            else:
                spend += -delta
            for crop in NEVER_BOUGHT:
                gain = held.get(crop, 0) - prev_held.get(crop, 0)
                if gain > 0:
                    harvested[crop] += gain
            dropped = prev_held.get("FERTILIZER", 0) - held.get("FERTILIZER", 0)
            if dropped > 0 and "SELL" in sell_verbs(action):
                fert_sold += dropped
        prev_money, prev_held = money, held
    return {"harvested": harvested, "revenue": revenue, "spend": spend,
            "fert_sold": fert_sold, "fert_applied": fert_applied}


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seeds", type=int, default=4)
    args = ap.parse_args()

    variant, cancelled = make_no_fertilize(BASELINE)

    rows = []
    for seed in range(args.seeds):
        for seat in (0, 1):
            # Paired: the variant plays whichever seat is being measured, against
            # the same opponent and seed as the control. An earlier version looped
            # over both seats without using the variable and ran one match twice.
            if seat == 0:
                control_agent_a, control_agent_b = load_agent(str(BASELINE)), load_agent(str(BASELINE))
                test_agent_a, test_agent_b = variant, load_agent(str(BASELINE))
            else:
                control_agent_a, control_agent_b = load_agent(str(BASELINE)), load_agent(str(BASELINE))
                test_agent_a, test_agent_b = load_agent(str(BASELINE)), variant
            control = run_episode_trace(control_agent_a, control_agent_b, seed)
            test = run_episode_trace(test_agent_a, test_agent_b, seed)
            rows.append({
                "seed": seed, "seat": seat,
                "bank_control": control["banks"][seat],
                "bank_test": test["banks"][seat],
                "c": measure(control["steps"], seat),
                "t": measure(test["steps"], seat),
            })

    n = len(rows)
    print(f"partidas pareadas: {n} (mismo seed, mismo rival, ambos asientos)")
    print(f"FERTILIZE cancelados: {cancelled['n']} ({cancelled['n'] / n:.1f} por partida)")
    print()
    print(f"{'seed':>4} {'seat':>4} {'banco ctrl':>12} {'banco test':>12} {'delta':>10} "
          f"{'cosecha ctrl':>13} {'cosecha test':>13}")
    for row in rows:
        c_harvest = sum(row["c"]["harvested"].values())
        t_harvest = sum(row["t"]["harvested"].values())
        print(f"{row['seed']:>4} {row['seat']:>4} {row['bank_control']:>12,.0f} "
              f"{row['bank_test']:>12,.0f} {row['bank_test'] - row['bank_control']:>+10,.0f} "
              f"{c_harvest:>13,.0f} {t_harvest:>13,.0f}")

    mean_bank_c = sum(r["bank_control"] for r in rows) / n
    mean_bank_t = sum(r["bank_test"] for r in rows) / n
    mean_harvest_c = sum(sum(r["c"]["harvested"].values()) for r in rows) / n
    mean_harvest_t = sum(sum(r["t"]["harvested"].values()) for r in rows) / n
    print()
    print(f"banco medio   control {mean_bank_c:,.0f} | sin fertilizar {mean_bank_t:,.0f}"
          f"  -> delta {mean_bank_t - mean_bank_c:+,.0f}")
    print(f"cosecha media control {mean_harvest_c:,.0f} | sin fertilizar {mean_harvest_t:,.0f}"
          f"  -> delta {mean_harvest_t - mean_harvest_c:+,.0f}")
    for crop in NEVER_BOUGHT:
        c_sum = sum(r["c"]["harvested"][crop] for r in rows) / n
        t_sum = sum(r["t"]["harvested"][crop] for r in rows) / n
        if c_sum or t_sum:
            print(f"  {crop:<12} control {c_sum:>8,.0f} | sin fertilizar {t_sum:>8,.0f}"
                  f" | delta {t_sum - c_sum:>+8,.0f}")
    print()
    print(f"fertilizante vendido  control {sum(r['c']['fert_sold'] for r in rows) / n:,.0f}"
          f" | sin fertilizar {sum(r['t']['fert_sold'] for r in rows) / n:,.0f}")
    print(f"fertilizante aplicado control {sum(r['c']['fert_applied'] for r in rows) / n:,.0f}"
          f" | sin fertilizar {sum(r['t']['fert_applied'] for r in rows) / n:,.0f}")


if __name__ == "__main__":
    main()
