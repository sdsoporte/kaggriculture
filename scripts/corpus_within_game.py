#!/usr/bin/env python
"""What does the WINNER do differently in the SAME match? (local, within-game)

The corpus is published as "top episode replays", so it is selection-biased on the
OUTCOME: comparing a top game against a random one is confounded, and this project
was already burned by exactly that (a +$25k "opportunity" that was an artifact).
Inside one match, however, both seats face the same shops, the same town drain and
the same market, so a WITHIN-GAME comparison of winner against loser controls for
almost everything that makes cross-game comparison unsafe. That is the only axis the
Lab's own next-steps list calls safe, and this runs it locally over the replay cache
instead of waiting for the notebook.

Why it matters for the goal: the final ranking is a Bradley-Terry WIN-RATE
tournament and coin margin is irrelevant, so the question is not "how do we earn
more" but "which behaviours separate a winner from a loser in identical
conditions". Those behaviours are the candidate edges, and each one gets measured
afterwards in the arena mirror.

Read-only. One replay is held in memory at a time (each is ~35 MB).

Usage:
    .venv/bin/python scripts/corpus_within_game.py --files 100
"""

import argparse
import collections
import json
import statistics
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CACHE = REPO / "scratch" / "corpus_full"
TURNS_PER_DAY = 24
CROPS = ("WHEAT", "CARROT", "TOMATO", "STRAWBERRY", "MELON")
ANIMALS = ("COW", "SHEEP", "GOOSE")
PRODUCTS = ("WHEAT", "CARROT", "TOMATO", "STRAWBERRY", "MELON", "EGG", "MILK", "WOOL", "FERTILIZER")

# Metrics compared winner against loser within a match. Each is (le, count).
METRICS = (
    "hires", "land", "seed_units", "animals_bought", "plants", "harvests",
    "waters", "feeds", "fertilizes", "collects", "builds",
    "sells", "sell_units", "sell_lot_median", "distinct_sell_days",
    "peak_crop_tiles", "peak_animals", "quadrants",
)


def _sum_orders(action, verb):
    """Units this action asks for under `verb`, summed over its market orders."""
    total = 0
    if isinstance(action, dict):
        for order in action.get("market") or []:
            if (isinstance(order, (list, tuple)) and len(order) >= 3
                    and order[0] == verb):
                try:
                    total += int(order[2])
                except (TypeError, ValueError):
                    continue
    return total


def _verbs(action):
    if not isinstance(action, dict):
        return []
    units = [action.get("farmer"), *(action.get("hands") or [])]
    return [u[0] for u in units if isinstance(u, (list, tuple)) and u]


def profile_seat(steps, seat):
    """Behaviour of one seat over a whole match."""
    m = collections.Counter()
    lots = []
    sell_days = set()
    peak_crops = peak_animals = peak_hands = 0
    quadrants = 1
    bank = 0.0

    for index in range(1, len(steps)):
        entry = steps[index][seat] if seat < len(steps[index]) else {}
        obs = entry.get("observation") or {}
        action = entry.get("action")
        day = obs.get("day", index // TURNS_PER_DAY)

        for verb in _verbs(action):
            if verb == "PLANT":
                m["plants"] += 1
            elif verb == "HARVEST":
                m["harvests"] += 1
            elif verb == "WATER":
                m["waters"] += 1
            elif verb == "FEED":
                m["feeds"] += 1
            elif verb == "FERTILIZE":
                m["fertilizes"] += 1
            elif verb == "COLLECT_FERTILIZER":
                m["collects"] += 1
            elif verb in ("BUILD_COOP", "BUILD_PASTURE"):
                m["builds"] += 1

        if isinstance(action, dict):
            for order in action.get("market") or []:
                if not isinstance(order, (list, tuple)) or not order:
                    continue
                if order[0] == "HIRE":
                    m["hires"] += 1
                elif order[0] == "BUY_LAND":
                    m["land"] += 1
                elif order[0] == "BUY_ANIMAL":
                    m["animals_bought"] += 1
                elif order[0] == "SELL" and len(order) >= 3:
                    m["sells"] += 1
                    sell_days.add(day)
                    try:
                        quantity = int(order[2])
                    except (TypeError, ValueError):
                        continue
                    lots.append(quantity)
                    m["sell_units"] += quantity
            m["seed_units"] += _sum_orders(action, "BUY_SEED")

        crops = animals = hands = 0
        for row in ((obs.get("farms") or [{}])[obs.get("player", seat)] or {}).get("tiles") or []:
            for cell in row:
                if not isinstance(cell, dict):
                    continue
                if cell.get("animal"):
                    animals += 1
                elif cell.get("kind") == "PLANT":
                    crops += 1
        farm = (obs.get("farms") or [{}])[obs.get("player", seat)] or {}
        hands = len(farm.get("hands") or [])
        quadrants = len(farm.get("unlocked_quadrants") or [])
        peak_crops = max(peak_crops, crops)
        peak_animals = max(peak_animals, animals)
        peak_hands = max(peak_hands, hands)
        bank = farm.get("money", bank)

    return {
        **m,
        "sell_lot_median": statistics.median(lots) if lots else 0,
        "distinct_sell_days": len(sell_days),
        "peak_crop_tiles": peak_crops,
        "peak_animals": peak_animals,
        "peak_hands": peak_hands,
        "quadrants": quadrants,
        "bank": bank,
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--files", type=int, default=100)
    args = ap.parse_args()

    files = sorted(CACHE.glob("*.json"))[:args.files]
    if not files:
        raise SystemExit(f"no replays in {CACHE}")

    games, skipped = [], 0
    for path in files:
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            skipped += 1
            continue
        steps = data.get("steps") if isinstance(data, dict) else data
        if not steps or len(steps) < 100 or any(len(s) < 2 for s in steps[:2]):
            skipped += 1
            continue
        statuses = (data.get("statuses") or []) if isinstance(data, dict) else []
        if any(str(s).upper().find("ERROR") >= 0 for s in statuses):
            skipped += 1
            continue
        a, b = profile_seat(steps, 0), profile_seat(steps, 1)
        games.append((path.stem, a, b))

    print(f"partidas analizadas: {len(games)}  (saltadas: {skipped})")
    if not games:
        raise SystemExit("nothing usable")

    margins = [abs(g[1]["bank"] - g[2]["bank"]) for g in games]
    relative = [m / max(1.0, max(g[1]["bank"], g[2]["bank"]))
                for m, g in zip(margins, games, strict=True)]
    print(f"margen absoluto mediano: ${statistics.median(margins):,.0f}")
    print(f"margen relativo mediano: {statistics.median(relative):.2%} del banco del ganador")
    close = sum(1 for r in relative if r < 0.05)
    print(f"partidas decididas por menos del 5% del banco: {close}/{len(games)}"
          f" ({close / len(games):.0%})")
    print()

    # One (winner, loser) pair per game, so every metric is compared within a match.
    pairs = [(a, b) if a["bank"] >= b["bank"] else (b, a) for _, a, b in games]

    print(f"{'metrica':<20}{'ganador':>11}{'perdedor':>11}{'delta':>10}{'gana mas a menudo':>19}{'gana/pierde':>13}")
    print("-" * 84)
    rows = []
    for metric in METRICS:
        wins = sum(1 for winner, loser in pairs
                   if winner.get(metric, 0) > loser.get(metric, 0))
        losses = sum(1 for winner, loser in pairs
                     if winner.get(metric, 0) < loser.get(metric, 0))
        wv = statistics.median([winner.get(metric, 0) for winner, _ in pairs])
        lv = statistics.median([loser.get(metric, 0) for _, loser in pairs])
        share = wins / max(1, wins + losses)
        rows.append((metric, wv, lv, wv - lv, share, wins, losses))
    for metric, wv, lv, delta, share, wins, losses in sorted(rows, key=lambda r: -abs(r[4] - 0.5)):
        flag = "  <-- separa" if abs(share - 0.5) >= 0.15 else ""
        print(f"{metric:<20}{wv:>11.1f}{lv:>11.1f}{delta:>+10.1f}"
              f"{share:>19.1%}{wins:>7}/{losses}{flag}")

    print()
    print("'gana mas a menudo' = la fraccion de partidas en que el GANADOR tiene mas")
    print("de esa metrica que el perdedor. 50% = no separa. Solo cuenta la direccion,")
    print("no el tamano; una metrica con 65% y delta chico separa igual porque el")
    print("ranking final es win rate, no margen.")

    print()
    print("=== CUADRANTES vs TASA DE VICTORIA (el ranking premia win rate, no banco) ===")
    tab = collections.defaultdict(lambda: [0, 0])
    for _, a, b in games:
        for prof, other in ((a, b), (b, a)):
            bucket = tab[prof["quadrants"]]
            bucket[1] += 1
            if prof["bank"] > other["bank"]:
                bucket[0] += 1
    for q in sorted(tab):
        wins, played = tab[q]
        print(f"  con {q} cuadrantes: gana {wins:>3}/{played:<3} = {wins / played:>5.1%}")

    differed = [(a, b) for _, a, b in games if a["quadrants"] != b["quadrants"]]
    if differed:
        more_wins = sum(1 for a, b in differed
                        if (a if a["quadrants"] > b["quadrants"] else b)["bank"]
                        > (b if a["quadrants"] > b["quadrants"] else a)["bank"])
        print()
        print(f"  partidas donde los dos asientos DIFIEREN en cuadrantes: {len(differed)}/{len(games)}")
        print(f"  el que tiene MAS cuadrantes gana: {more_wins}/{len(differed)} = {more_wins / len(differed):.1%}")
        print("  (es asociacion dentro de la partida, no causalidad: mas tierra tambien")
        print("   puede ser consecuencia de ir ganando. Pero la direccion es accionable.)")
    else:
        print()
        print("  ningun cruce: los dos asientos siempre terminan con los mismos cuadrantes.")


if __name__ == "__main__":
    main()
