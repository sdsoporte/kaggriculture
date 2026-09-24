#!/usr/bin/env python
"""Is the tape's FERTILIZER PURCHASE a cash leak? (cheap paired probe)

Companion to `scripts/fert_probe.py`, which answered a different question by
cancelling the tape's 97 FERTILIZE *applications* (-$3,078/game -> the tape's
fertilizing IS productive, verified). This probe removes the 66 `BUY_PRODUCT
FERTILIZER` *orders*, which no experiment had ever isolated.

Measured per game, the tape collects ~382 fertilizer units, applies 97, sells 333
and still buys 66. The town never consumes fertilizer, our own sales drive it to
the floor (marginal sale value ~$5/unit), and buying near the $100 base to sell
into that glut is a negative round trip. The only thing that can justify the
purchase is an extra APPLICATION (~+$32 measured), so the diagnostics below answer
the question that decides it: do the 97 applications survive without the buys?

Everything is read from the paired traces, both seats, same seed and opponent as
the control. Deliberately self-contained so it runs standalone.
"""

import argparse
import collections
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from kaggriculture.loading import load_agent  # noqa: E402
from kaggriculture.runner import run_episode_trace  # noqa: E402

CONTROL = REPO / "src" / "kaggriculture" / "agents" / "baseline_v8.py"
VARIANT = REPO / "src" / "kaggriculture" / "agents" / "v10_no_fert_buy.py"
NEVER_BOUGHT = ("MELON", "STRAWBERRY", "TOMATO", "CARROT")


def holdings(obs):
    private = obs.get("private") or {}
    held = collections.Counter(private.get("shed") or {})
    for inventory in private.get("inventories") or []:
        held.update(inventory)
    return held


def market_units(action, verb, item):
    """Units a market order asks for, matched on verb AND item."""
    total = 0
    if isinstance(action, dict):
        for order in action.get("market") or []:
            if (
                isinstance(order, (list, tuple))
                and len(order) > 2
                and order[0] == verb
                and order[1] == item
            ):
                try:
                    total += int(order[2])
                except (TypeError, ValueError):
                    continue
    return total


def worker_verbs(action):
    if not isinstance(action, dict):
        return []
    workers = [action.get("farmer")] + list(action.get("hands") or [])
    return [
        w[0]
        for w in workers
        if isinstance(w, (list, tuple)) and w
    ]


def farm_summary(obs, seat):
    farm = (obs.get("farms") or [{}])[obs.get("player", seat)] or {}
    tiles = farm.get("tiles") or []
    animals = structures = locked = 0
    for row in tiles:
        for tile in row:
            if tile == "LOCKED":
                locked += 1
            elif isinstance(tile, dict):
                if tile.get("animal"):
                    animals += 1
                elif tile.get("kind") in ("PASTURE", "COOP"):
                    structures += 1
    return {
        "quadrants": len(farm.get("unlocked_quadrants") or []),
        "hands": len(farm.get("hands") or []),
        "animals": animals,
        "structures": structures,
        "locked": locked,
    }


def measure(trace, seat):
    """Everything one seat's trace can tell us about this experiment."""
    harvested = collections.Counter()
    fert_applied = fert_buy_units = fert_buy_orders = fert_sell_units = 0
    revenue = spend = 0
    prev_money = prev_held = None
    last_obs = None
    for turn in trace:
        entry = turn[seat]  # the replay is steps[turn][seat] - not steps[seat]
        obs = entry.get("observation") or {}
        last_obs = obs
        farm = (obs.get("farms") or [{}])[obs.get("player", seat)]
        money = farm.get("money", 0)
        held = holdings(obs)
        action = entry.get("action")

        fert_applied += sum(1 for verb in worker_verbs(action) if verb == "FERTILIZE")
        bought = market_units(action, "BUY_PRODUCT", "FERTILIZER")
        if bought:
            fert_buy_units += bought
            fert_buy_orders += 1
        fert_sell_units += market_units(action, "SELL", "FERTILIZER")

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
        prev_money, prev_held = money, held

    return {
        "harvested": harvested,
        "fert_applied": fert_applied,
        "fert_buy_units": fert_buy_units,
        "fert_buy_orders": fert_buy_orders,
        "fert_sell_units": fert_sell_units,
        "revenue": revenue,
        "spend": spend,
        "farm": farm_summary(last_obs or {}, seat),
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seeds", type=int, default=4)
    args = ap.parse_args()

    variant = load_agent(str(VARIANT))
    rows = []
    for seed in range(args.seeds):
        for seat in (0, 1):
            if seat == 0:
                test_pair = (variant, load_agent(str(CONTROL)))
            else:
                test_pair = (load_agent(str(CONTROL)), variant)
            control = run_episode_trace(
                load_agent(str(CONTROL)), load_agent(str(CONTROL)), seed
            )
            test = run_episode_trace(test_pair[0], test_pair[1], seed)
            rows.append({
                "seed": seed, "seat": seat,
                "bank_control": control["banks"][seat],
                "bank_test": test["banks"][seat],
                "c": measure(control["steps"], seat),
                "t": measure(test["steps"], seat),
            })

    n = len(rows)
    print(f"partidas pareadas: {n} (mismo seed, mismo rival, ambos asientos)")
    print(f"{'seed':>4} {'seat':>4} {'banco ctrl':>12} {'banco test':>12} {'delta':>10}")
    for row in rows:
        print(f"{row['seed']:>4} {row['seat']:>4} {row['bank_control']:>12,.0f} "
              f"{row['bank_test']:>12,.0f} "
              f"{row['bank_test'] - row['bank_control']:>+10,.0f}")

    def mean(key, side):
        return sum(r[side][key] for r in rows) / n

    mean_c = sum(r["bank_control"] for r in rows) / n
    mean_t = sum(r["bank_test"] for r in rows) / n
    print()
    print(f"BANCO medio   control {mean_c:>10,.0f} | sin compras {mean_t:>10,.0f}"
          f"  -> delta {mean_t - mean_c:+,.0f}")
    print()
    print("el diagnostico que decide si la medicion es limpia:")
    print(f"  FERTILIZE aplicados   control {mean('fert_applied', 'c'):>8,.1f} | "
          f"sin compras {mean('fert_applied', 't'):>8,.1f}"
          f"  -> {'SOBREVIVEN' if mean('fert_applied', 't') > 0.9 * mean('fert_applied', 'c') else 'SE PIERDEN'}")
    print(f"  fertilizante comprado control {mean('fert_buy_units', 'c'):>8,.1f} | "
          f"sin compras {mean('fert_buy_units', 't'):>8,.1f}")
    print(f"  ordenes de compra     control {mean('fert_buy_orders', 'c'):>8,.1f} | "
          f"sin compras {mean('fert_buy_orders', 't'):>8,.1f}")
    print(f"  fertilizante vendido  control {mean('fert_sell_units', 'c'):>8,.1f} | "
          f"sin compras {mean('fert_sell_units', 't'):>8,.1f}")
    print()
    print("canal de perturbacion de caja (el riesgo declarado en la tarea):")
    for key, label in (("quadrants", "cuadrantes"), ("hands", "manos"),
                       ("animals", "animales"), ("structures", "estructuras")):
        c = sum(r["c"]["farm"][key] for r in rows) / n
        t = sum(r["t"]["farm"][key] for r in rows) / n
        flag = "" if abs(t - c) < 1e-9 else "   <-- SE MOVIO"
        print(f"  {label:<12} control {c:>7,.1f} | sin compras {t:>7,.1f}{flag}")
    print()
    print("cosecha media por cultivo (los que nunca compramos):")
    for crop in NEVER_BOUGHT:
        c = sum(r["c"]["harvested"][crop] for r in rows) / n
        t = sum(r["t"]["harvested"][crop] for r in rows) / n
        if c or t:
            print(f"  {crop:<12} control {c:>8,.1f} | sin compras {t:>8,.1f}"
                  f" | delta {t - c:>+8,.1f}")


if __name__ == "__main__":
    main()
