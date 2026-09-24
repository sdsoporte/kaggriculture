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
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CACHE = REPO / "scratch" / "corpus_full"
sys.path.insert(0, str(REPO / "src"))

from kaggriculture.agents.replay_opponent import make_replay_opponent  # noqa: E402
from kaggriculture.loading import load_agent  # noqa: E402
from kaggriculture.runner import run_episode_trace  # noqa: E402

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


def _ratio(top, bottom):
    """Ratio that never raises, for a report where a zero denominator is meaningful."""
    try:
        return top / bottom
    except ZeroDivisionError:
        return float("inf")


def recipe(games):
    """What ELSE comes with the 4th quadrant?

    The timing test settled that buying it is a lever, not a symptom: buyers take it
    on day 10 while a median $3,495 BEHIND and win 70% against 41% for the seats that
    never buy. But if buyers ALSO hire more, sell more often or plant more, then a
    variant that only adds BUY_LAND would spend $4,000 on 25 idle cells and measure
    the cost of land with none of the recipe.
    """
    groups = {3: [], 4: []}
    for _, a, b in games:
        for prof, other in ((a, b), (b, a)):
            prof["won"] = prof["bank"] > other["bank"]
            if prof["quadrants"] in groups:
                groups[prof["quadrants"]].append(prof)

    three, four = groups[3], groups[4]
    if not three or not four:
        print(f"necesito los dos grupos; tengo 3q={len(three)} 4q={len(four)}")
        return

    for label, group in (("3 cuadrantes", three), ("4 cuadrantes", four)):
        wins = sum(1 for p in group if p["won"])
        print(f"asientos con {label}: {len(group)}  (gana {wins} = {wins / len(group):.1%})")
    print()
    print(f"{'metrica':<20}{'3 cuadrantes':>14}{'4 cuadrantes':>14}{'ratio':>9}{'delta':>12}")
    print("-" * 69)
    for metric in METRICS:
        a = statistics.median([p.get(metric, 0) for p in three])
        b = statistics.median([p.get(metric, 0) for p in four])
        shown = "n/a" if a == 0 else f"{_ratio(b, a):>9.2f}"
        print(f"{metric:<20}{a:>14.1f}{b:>14.1f}{shown:>9}{b - a:>+12.1f}")
    print()
    print("ratio > 1 = los compradores del 4to cuadrante hacen MAS de eso.")
    print("Cerca de 1 = el 4to cuadrante no viene con receta, y un variant de SOLO")
    print("BUY_LAND seria un test limpio de la compra en si.")


def versus_replays(agent_path, count):
    """Run OUR agent against real opponents and separate winner from loser THERE.

    The within-game table above compares two corpus teams against each other. This
    runs the same comparison on the matches WE play, which answers the question our
    own win rate actually depends on: in the games we lose to real top-team
    recordings, what does the opponent do more of than us?

    The opponent is OPEN-LOOP, so its realised profile is its recorded play and not
    an adaptation. That weakens it as an opponent and does not weaken it as a
    measuring stick for our own behaviour.
    """
    ours = []
    failures = []
    for path in sorted(CACHE.glob("*.json"))[:count]:
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        steps = data.get("steps") if isinstance(data, dict) else data
        if not steps or len(steps) < 100:
            continue
        info = data.get("info") if isinstance(data, dict) else None
        seed = (info or {}).get("seed")
        if seed is None and isinstance(data, dict):
            seed = (data.get("configuration") or {}).get("seed")
        for opponent_seat in (0, 1):
            opponent = make_replay_opponent(steps=steps, seat=opponent_seat)
            if opponent_seat == 1:
                pair = (load_agent(str(agent_path)), opponent)
                our_seat = 0
            else:
                pair = (opponent, load_agent(str(agent_path)))
                our_seat = 1
            try:
                trace = run_episode_trace(pair[0], pair[1], int(seed or 0))
            except Exception as exc:  # noqa: BLE001
                # A skipped episode must be VISIBLE. Silently dropping it would shrink
                # the sample while the win rate still looked clean.
                failures.append(f"{path.stem} seat {opponent_seat}: "
                                f"{type(exc).__name__}: {exc}")
                continue
            mine = profile_seat(trace["steps"], our_seat)
            theirs = profile_seat(trace["steps"], opponent_seat)
            mine["bank"] = trace["banks"][our_seat]
            theirs["bank"] = trace["banks"][opponent_seat]
            ours.append((mine, theirs))
    return ours, failures


def report_versus(rows, failures):
    if failures:
        print(f"EPISODIOS SALTADOS: {len(failures)}")
        for line in failures[:10]:
            print(f"   {line}")
    if not rows:
        print("no games")
        return
    wins = sum(1 for mine, _ in rows if mine["bank"] > _["bank"])
    print(f"partidas nuestras contra replay opponents: {len(rows)}")
    print(f"ganamos {wins}/{len(rows)} = {wins / len(rows):.1%}")
    margins = [mine["bank"] - theirs["bank"] for mine, theirs in rows]
    print(f"margen medio ${statistics.mean(margins):+,.0f}"
          f"  |  nuestros bancos medianos ${statistics.median([m['bank'] for m, _ in rows]):,.0f}"
          f" vs ${statistics.median([t['bank'] for _, t in rows]):,.0f}")
    print()
    # Winner minus loser, restricted to OUR games, so the direction says what to copy.
    pairs = [(mine, theirs) if mine["bank"] >= theirs["bank"] else (theirs, mine)
             for mine, theirs in rows]
    print(f"{'metrica':<20}{'ganador':>11}{'perdedor':>11}{'delta':>10}{'gana mas a menudo':>19}")
    print("-" * 71)
    out = []
    for metric in METRICS:
        more = sum(1 for winner, loser in pairs
                   if winner.get(metric, 0) > loser.get(metric, 0))
        less = sum(1 for winner, loser in pairs
                   if winner.get(metric, 0) < loser.get(metric, 0))
        wv = statistics.median([winner.get(metric, 0) for winner, _ in pairs])
        lv = statistics.median([loser.get(metric, 0) for _, loser in pairs])
        out.append((metric, wv, lv, wv - lv, more / max(1, more + less)))
    for metric, wv, lv, delta, share in sorted(out, key=lambda r: -abs(r[4] - 0.5)):
        flag = "  <-- separa" if abs(share - 0.5) >= 0.15 else ""
        print(f"{metric:<20}{wv:>11.1f}{lv:>11.1f}{delta:>+10.1f}{share:>19.1%}{flag}")
    print()
    lost = [(mine, theirs) for mine, theirs in rows if mine["bank"] <= theirs["bank"]]
    print(f"EN LAS {len(lost)} PARTIDAS QUE PERDIMOS, que hicieron ellos mas que nosotros:")
    for metric in METRICS:
        they = statistics.median([t.get(metric, 0) for _, t in lost]) if lost else 0
        we = statistics.median([m.get(metric, 0) for m, _ in lost]) if lost else 0
        if they > we:
            print(f"   {metric:<20} ellos {they:>9.1f} | nosotros {we:>9.1f} | +{they - we:.1f}")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--files", type=int, default=100)
    ap.add_argument("--recipe", action="store_true",
                    help="segment seats by quadrant count instead of comparing winner vs loser")
    ap.add_argument("--vs", type=int, default=0, metavar="N",
                    help="run OUR agent against the first N replay opponents and separate there")
    ap.add_argument("--agent", default="src/kaggriculture/agents/baseline_v8.py",
                    help="agent under test for --vs")
    args = ap.parse_args()

    if args.vs:
        rows, failures = versus_replays(REPO / args.agent, args.vs)
        report_versus(rows, failures)
        return

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

    if args.recipe:
        recipe(games)
        return

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
