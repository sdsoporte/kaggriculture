#!/usr/bin/env python
"""What would funding the 4th quadrant actually require? (facts before opinion)

The 4th quadrant is the one structural lever this project closed by ARGUMENT rather
than by measurement: V9 declared it NOT IMPLEMENTABLE because the tape's early
economy is cash-coupled and the step-150 `BUY_LAND` cliff (~$1,000) has already
destroyed one overlay by $114. Land is priced in order - NE 1,000, SW 2,000, SE
**4,000** - so the 4th quadrant is the most expensive purchase in the season and it
lands last.

Before asking a model how to fund it, this measures what the tape's own cash curve
actually looks like, because three separate things have to be true:

  1. FUNDING. Cash must reach the SE price (~$4,000) plus whatever the same-turn buy
     queue needs, without missing the step-150 purchase on the way.
  2. LABOUR. 25 more cells is ~33% more work, and hands are re-hired daily at
     Fibonacci cost. If worker turns are already near-saturated there is nothing to
     absorb the extra cells.
  3. DEMAND. More cells means more units, and the market is a deterministic function
     of inventory. Exceeding absorption collapses the price, so extra volume has to
     land on a product with headroom or it is worth $1.

Read-only. Nothing is patched.

Usage:
    .venv/bin/python scripts/quadrant4_probe.py --seeds 4
"""

import argparse
import statistics
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from kaggriculture.loading import load_agent  # noqa: E402
from kaggriculture.runner import run_episode_trace  # noqa: E402

CONTROL = REPO / "src" / "kaggriculture" / "agents" / "baseline_v8.py"
TURNS_PER_DAY = 24
SE_PRICE = 4000          # LAND_PRICES[2]; the purchase that unlocks the 4th quadrant
LAND_CLIFF_STEP = 150    # the step at which the tape issues its BUY_LAND

MOVE_VERBS = {"NORTH", "SOUTH", "EAST", "WEST"}


def profile(trace, seat):
    """Daily cash, worker saturation and the final layout, for one seat."""
    money_by_day = {}
    unit_turns = idle_turns = 0
    quadrants_seen = set()
    for index in range(1, len(trace)):
        entry = trace[index][seat]
        obs = entry.get("observation") or {}
        day = obs.get("day", index // TURNS_PER_DAY)
        farm = (obs.get("farms") or [{}])[obs.get("player", seat)] or {}

        # End-of-day cash: the last hour of the day overwrites.
        hour = obs.get("hour", index % TURNS_PER_DAY)
        if hour == TURNS_PER_DAY - 1 or day not in money_by_day:
            money_by_day[day] = farm.get("money", 0)
        quadrants_seen.add(len(farm.get("unlocked_quadrants") or []))

        action = entry.get("action")
        if isinstance(action, dict):
            units = [action.get("farmer"), *(action.get("hands") or [])]
            for unit in units:
                if not isinstance(unit, (list, tuple)) or not unit:
                    continue
                unit_turns += 1
                if unit[0] == "PASS":
                    idle_turns += 1

    return {
        "money_by_day": money_by_day,
        "peak_money": max(money_by_day.values()) if money_by_day else 0,
        "peak_day": max(money_by_day, key=lambda d: money_by_day[d]) if money_by_day else 0,
        "money_before_cliff": money_by_day.get(LAND_CLIFF_STEP // TURNS_PER_DAY, 0),
        "idle_fraction": idle_turns / unit_turns if unit_turns else 0.0,
        "quadrants_seen": sorted(quadrants_seen),
        "cash_at_or_above_se": sorted(d for d, m in money_by_day.items() if m >= SE_PRICE),
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seeds", type=int, default=4)
    args = ap.parse_args()

    rows = []
    for seed in range(args.seeds):
        trace = run_episode_trace(load_agent(str(CONTROL)), load_agent(str(CONTROL)), seed)
        rows.append(profile(trace["steps"], 0))

    peaks = [r["peak_money"] for r in rows]
    print(f"el tape, {args.seeds} episodios espejo (asiento 0)")
    print()
    print("1. FUNDING")
    print(f"   efectivo pico            mediana {statistics.median(peaks):>10,.0f}"
          f"   min {min(peaks):>10,.0f}  max {max(peaks):>10,.0f}")
    print(f"   dia del pico             mediana {statistics.median([r['peak_day'] for r in rows]):>10.0f}")
    print(f"   efectivo antes del pico  mediana {statistics.median([r['money_before_cliff'] for r in rows]):>10,.0f}"
          f"   (paso {LAND_CLIFF_STEP}, donde sale el BUY_LAND)")
    for price, label in ((1000, "NE"), (2000, "SW"), (4000, "SE = 4to cuadrante")):
        reachable = [r for r in rows if r["peak_money"] >= price]
        print(f"   alcanza ${price:>5,} ({label:<18}) en {len(reachable)}/{len(rows)} episodios")
    print()
    print("   dias en que el tape YA tiene >= $4,000 (por episodio):")
    for seed, row in enumerate(rows):
        days = row["cash_at_or_above_se"]
        shown = f"{days[:8]}{'...' if len(days) > 8 else ''}" if days else "NINGUNO"
        print(f"     seed {seed}: {shown}")
    print()
    print("2. LABOUR")
    idle = [r["idle_fraction"] for r in rows]
    print(f"   fraccion de turnos de unidad OCIOSOS (PASS)  mediana {statistics.median(idle):.1%}"
          f"   min {min(idle):.1%}  max {max(idle):.1%}")
    print(f"   => margen de manos disponible: {statistics.median(idle):.1%} de los turnos;")
    print("      25 celdas mas son ~33% mas trabajo, asi que este numero decide.")

    print()
    print("3. CUADRANTES EFECTIVAMENTE DESBLOQUEADOS")
    for seed, row in enumerate(rows):
        print(f"     seed {seed}: {row['quadrants_seen']}")
    print()
    print("4. LO QUE COSTARIA EL 4to CUADRANTE")
    print(f"   SE cuesta ${SE_PRICE:,} y es la ULTIMA compra (orden NE/SW/SE),")
    print("   asi que exige sobrevivir el paso 150 primero, y despues juntar $4,000")
    print("   mas lo que la cola de compras del mismo turno necesite.")


if __name__ == "__main__":
    main()
