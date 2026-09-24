#!/usr/bin/env python
"""Profile the LAST 3 episodes of each current top-10 team (V10 evidence).

The daily episode dataset kaggle/kaggriculture-episodes-2026-09-23 is the newest
published (the index manifest ends there) and all 608 replays are already on disk
under scratch/corpus_full/, so nothing needs downloading.

Design note: this takes each team's three MOST RECENT episodes regardless of
result. Earlier corpus work bucketed the WINNER's roster, which conditions on the
outcome; profiling behaviour independent of the result avoids that.

Team names inside the replays are JSON-escaped (\\ud83c\\udfc6), so names must be
decoded with json.loads - a plain regex keeps the escape text and silently drops
those teams from the index.

Usage:
  .venv/bin/python scratch/top10_last3.py <episode_id> [<episode_id> ...]
"""

import collections
import json
import re
import sys
from pathlib import Path

CORPUS = Path("scratch/corpus_full")
ANIMALS = ("COW", "SHEEP", "GOOSE")
STRUCTURES = ("PASTURE", "COOP")
TOP10 = [
    "DSM", "M & M & P & Q", "DECEM", "Vadim Vasilenko", "Majkel1337",
    "Unknown Mother-Goose", "Fourth Quadrant", "Kaggledew Valley \U0001f3c6",
    "mtmr_s1", "\u5403\u767d\u996d\u7684\u5927\u80a5\u9c7c",
]


def norm(name):
    return re.sub(r"[^a-z0-9]", "", name.lower())


TOP10_NORM = {norm(t): t for t in TOP10}


def side_profile(steps, seat):
    """Behaviour profile for one seat over a whole replay."""
    ops = collections.Counter()
    market = collections.Counter()
    peak_animals = collections.Counter()
    peak_struct = collections.Counter()
    peak_hands = 0
    quads = 0
    shops = []
    peak_free = 0

    for step in steps:
        entry = step[seat]
        action = entry.get("action")
        if isinstance(action, dict):
            for worker in [action.get("farmer")] + list(action.get("hands") or []):
                if isinstance(worker, (list, tuple)) and worker:
                    ops[str(worker[0])] += 1
            for order in action.get("market") or []:
                if isinstance(order, (list, tuple)) and order:
                    key = str(order[0])
                    if key in ("BUY_ANIMAL", "BUY_SEED", "BUY_PRODUCT") and len(order) > 1:
                        key = f"{key}:{order[1]}"
                    market[key] += 1

        obs = entry.get("observation") or {}
        farms = obs.get("farms")
        if not farms:
            continue
        farm = farms[obs.get("player", seat)]
        animals = collections.Counter()
        struct = collections.Counter()
        for row in farm.get("tiles", []):
            for tile in row:
                if isinstance(tile, dict) and tile.get("kind") in STRUCTURES:
                    struct[tile["kind"]] += 1
                    if tile.get("animal"):
                        animals[tile["animal"]] += 1
        peak_animals = peak_animals | animals
        peak_struct = peak_struct | struct
        peak_hands = max(peak_hands, len(farm.get("hands") or []))
        quads = max(quads, len(farm.get("unlocked_quadrants") or []))
        peak_free = max(peak_free, sum(struct.values()) - sum(animals.values()))
        unlocked = (obs.get("town") or {}).get("unlocked_shops")
        if unlocked:
            shops = list(unlocked)

    return {
        "ops": ops,
        "market": market,
        "peak_animals": peak_animals,
        "peak_struct": peak_struct,
        "peak_hands": peak_hands,
        "quads": quads,
        "peak_free": peak_free,
        "shops": shops,
    }


def main():
    ids = [int(x) for x in sys.argv[1:]]
    rows = []
    for ep in ids:
        path = CORPUS / f"{ep}.json"
        if not path.is_file():
            print(f"!! missing {path}")
            continue
        data = json.loads(path.read_text())
        names = [json.loads(f'"{n}"') for n in data["info"]["TeamNames"]]
        steps = data["steps"]
        banks = []
        for seat in (0, 1):
            obs = steps[-1][seat]["observation"]
            banks.append(obs["farms"][obs["player"]]["money"])
        for seat, name in enumerate(names):
            if norm(name) not in TOP10_NORM:
                continue
            prof = side_profile(steps, seat)
            margin = banks[seat] - banks[1 - seat]
            rows.append({
                "ep": ep, "team": TOP10_NORM[norm(name)], "seat": seat,
                "bank": banks[seat], "opp": names[1 - seat], "margin": margin,
                "result": "WIN" if margin > 0 else ("LOSS" if margin < 0 else "TIE"),
                **prof,
            })

    print(f"{'ep':>10} {'team':<26} {'res':<5} {'bank':>9} {'margin':>9} "
          f"{'animals':<20} {'q':>2} {'hands':>5} {'free':>4}")
    print("-" * 108)
    for r in sorted(rows, key=lambda r: (r["team"], -r["ep"])):
        animals = " ".join(f"{a}:{r['peak_animals'][a]}" for a in ANIMALS if r["peak_animals"][a])
        print(f"{r['ep']:>10} {r['team'][:26]:<26} {r['result']:<5} {r['bank']:>9,.0f} "
              f"{r['margin']:>+9,.0f} {animals:<20} {r['quads']:>2} {r['peak_hands']:>5} "
              f"{r['peak_free']:>4}")

    print("\n=== agregado por equipo (todas sus ultimas partidas) ===")
    by_team = collections.defaultdict(list)
    for r in rows:
        by_team[r["team"]].append(r)
    for team, rs in sorted(by_team.items(), key=lambda kv: -len(kv[1])):
        wins = sum(1 for r in rs if r["result"] == "WIN")
        mean_bank = sum(r["bank"] for r in rs) / len(rs)
        mean_margin = sum(r["margin"] for r in rs) / len(rs)
        ops = collections.Counter()
        market = collections.Counter()
        animals = collections.Counter()
        struct = collections.Counter()
        for r in rs:
            ops += r["ops"]
            market += r["market"]
            animals = animals | r["peak_animals"]
            struct = struct | r["peak_struct"]
        n = len(rs)
        print(f"\n{team}  ({wins}W-{n - wins}L, bank medio ${mean_bank:,.0f}, "
              f"margen {mean_margin:+,.0f})")
        print(f"  pico animales {dict(animals)} | estructuras {dict(struct)} | "
              f"quads {max(r['quads'] for r in rs)} | manos pico {max(r['peak_hands'] for r in rs)} | "
              f"slots libres pico {max(r['peak_free'] for r in rs)}")
        print("  worker ops/partida: "
              + " ".join(f"{k}:{v // n}" for k, v in ops.most_common(8)))
        print("  market ops/partida: "
              + " ".join(f"{k}:{v // n}" for k, v in market.most_common(8)))
        shops = collections.Counter()
        for r in rs:
            for s in set(r["shops"]):
                shops[s] += 1
        print(f"  tiendas vistas: {dict(shops)}")


if __name__ == "__main__":
    main()
