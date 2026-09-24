#!/usr/bin/env python
"""Does the 4th quadrant CAUSE wins, or do winners buy land? (timing test)

A within-game analysis of 100 top-team matches found the strongest win-rate signal
measured so far:

    with 3 quadrants: wins  57/139 = 41.0%
    with 4 quadrants: wins  43/61  = 70.5%
    when the seats differ, the one with MORE quadrants wins 38/51 = 74.5%

That is an ASSOCIATION. The obvious objection is reverse causality: a team that is
already ahead can afford the $4,000, so "having 4 quadrants" may be a symptom of
winning rather than a cause. Acting on a symptom would mean spending $4,000 and 25
cells of labour for nothing, so the objection has to be tested before anything is
built on this.

The test uses TIMING, which the association cannot fake:

  * If the 4th quadrant is bought EARLY, while the buyer's bank is still level with
    or behind the opponent's, then the purchase precedes the win and the causal
    reading survives.
  * If it is bought LATE, when the buyer is already far ahead, the association is a
    symptom and the lever is not a lever.

Reports, per seat: the day each quadrant unlocked, the bank gap at the moment of the
4th unlock, and the win rate conditional on the unlock day.

Read-only. One replay in memory at a time.

Usage:
    .venv/bin/python scripts/corpus_quadrant_timing.py --files 100
"""

import argparse
import json
import statistics
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CACHE = REPO / "scratch" / "corpus_full"
TURNS_PER_DAY = 24


def unlock_days(steps, seat):
    """Day each quadrant unlocked, plus the bank gap when the 4th did.

    `farm["unlocked_quadrants"]` starts as ["NW"], so reaching length 2 is the first
    paid quadrant (NE), 3 is SW and 4 is SE.
    """
    days = {}
    gap_at_four = None
    bank = 0.0
    for index in range(1, len(steps)):
        entry = steps[index][seat] if seat < len(steps[index]) else {}
        obs = entry.get("observation") or {}
        farm = (obs.get("farms") or [{}])[obs.get("player", seat)] or {}
        count = len(farm.get("unlocked_quadrants") or [])
        day = obs.get("day", index // TURNS_PER_DAY)
        bank = farm.get("money", bank)
        if count >= 2 and 2 not in days:
            days[2] = day
        if count >= 3 and 3 not in days:
            days[3] = day
        if count >= 4 and 4 not in days:
            days[4] = day
            other = steps[index][1 - seat] if 1 - seat < len(steps[index]) else {}
            oobs = other.get("observation") or {}
            ofarm = (oobs.get("farms") or [{}])[oobs.get("player", 1 - seat)] or {}
            gap_at_four = farm.get("money", 0) - ofarm.get("money", 0)
    return days, gap_at_four, bank


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--files", type=int, default=100)
    args = ap.parse_args()

    files = sorted(CACHE.glob("*.json"))[:args.files]
    games = []
    for path in files:
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        steps = data.get("steps") if isinstance(data, dict) else data
        if not steps or len(steps) < 100:
            continue
        seats = []
        for seat in (0, 1):
            days, gap, bank = unlock_days(steps, seat)
            seats.append({"days": days, "gap_at_four": gap, "bank": bank})
        games.append((path.stem, seats[0], seats[1]))

    print(f"partidas analizadas: {len(games)}")
    print()

    # One flat row per SEAT, so "did this seat win" is never recomputed by hand.
    seats = []
    for _, a, b in games:
        seats.append((a, b, a["bank"] > b["bank"]))
        seats.append((b, a, b["bank"] > a["bank"]))

    for label, quad in (("2do (NE)", 2), ("3ro (SW)", 3), ("4to (SE)", 4)):
        got = [s for s in seats if quad in s[0]["days"]]
        if not got:
            print(f"{label:<12} nadie lo desbloqueo")
            continue
        days = [s[0]["days"][quad] for s in got]
        wins = sum(1 for s in got if s[2])
        print(f"{label:<12} lo desbloquean {len(got):>3} asientos de {len(seats)}"
              f" | dia mediano {statistics.median(days):>4.0f}"
              f" (min {min(days)}, max {max(days)})"
              f" | gana {wins}/{len(got)} = {wins / len(got):.0%}")
    print()

    four = [s for s in seats if 4 in s[0]["days"]]
    if not four:
        print("nadie compro el 4to cuadrante")
        return

    print(f"asientos que SI compraron el 4to cuadrante: {len(four)}")
    gaps = [s[0]["gap_at_four"] for s in four]
    ahead = sum(1 for g in gaps if g > 0)
    print(f"  en el momento de la compra iban ADELANTE: {ahead}/{len(four)} = {ahead / len(four):.0%}")
    print(f"  brecha mediana en ese momento: ${statistics.median(gaps):+,.0f}")
    print(f"  de esos asientos, ganaron: {sum(1 for s in four if s[2])}/{len(four)}")
    print()
    print("lectura: si compran mayormente YENDO ADELANTE, la asociacion puede ser")
    print("sintoma. Si compran con la brecha cerca de cero, la compra precede al")
    print("resultado y vale construir sobre ella.")
    print()

    early = [s[0]["gap_at_four"] for s in four if s[0]["days"][4] <= 15]
    late = [s[0]["gap_at_four"] for s in four if s[0]["days"][4] > 15]
    for label, group in (("hasta el dia 15", early), ("despues del dia 15", late)):
        if group:
            print(f"  compras {label}: {len(group)} asientos, brecha mediana"
                  f" ${statistics.median(group):+,.0f}")
        else:
            print(f"  compras {label}: ninguna")


if __name__ == "__main__":
    main()
