#!/usr/bin/env python
"""Express the CURRENT tape as a `kaggriculture-plan/1` vector (W2a gate).

Why this exists, and why it comes before the consumer: the schema in
`docs/planner-parameter-schema.md` was frozen quickly, and the LLM author task was
launched against it. If the schema cannot describe our OWN tape, then an LLM plan
written against that schema is worthless - so the first thing to do is try to
describe the tape with it.

Three outputs, all needed downstream:
  1. The expressiveness verdict: which schema fields cannot capture what the tape
     actually does. Each one is a schema gap to fix before consuming any LLM plan.
  2. The calibration baseline: the tape's own vector, which the consumer (W2b) must
     reproduce to prove it is faithful (W2c).
  3. The search's start point for W2d.

Read-only. Nothing is patched, no agent is built.

Usage:
    .venv/bin/python scripts/tape_to_plan.py --seeds 3
"""

import argparse
import collections
import json
import statistics
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from kaggriculture.loading import load_agent  # noqa: E402
from kaggriculture.runner import run_episode_trace  # noqa: E402

CONTROL = REPO / "src" / "kaggriculture" / "agents" / "baseline_v8.py"
SEASON_DAYS = 30
# `ongoing` from the engine's CROPS table (kaggriculture.py:14-15). Hardcoded so this
# probe does not import the engine just for one boolean.
ONGOING_CROPS = ("TOMATO", "STRAWBERRY")


def _as_int(value, default=0):
    """int() that never raises: a malformed order must not kill the probe."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def market_orders(action):
    if not isinstance(action, dict):
        return []
    return [o for o in (action.get("market") or []) if isinstance(o, (list, tuple))]


def unit_actions(action):
    if not isinstance(action, dict):
        return []
    return [action.get("farmer"), *(action.get("hands") or [])]


def tile_stats(obs, seat):
    """Crop and animal census from the engine's own tile grid."""
    farm = (obs.get("farms") or [{}])[obs.get("player", seat)] or {}
    crops = collections.Counter()
    fertilized = collections.Counter()
    animals = collections.Counter()
    for row in farm.get("tiles") or []:
        for tile in row:
            if not isinstance(tile, dict):
                continue
            if tile.get("animal"):
                animals[tile["animal"]] += 1
            elif tile.get("kind") == "PLANT":
                crop = tile.get("crop")
                crops[crop] += 1
                if tile.get("fertilized_until_day", -1) > tile.get("planted_day", 0):
                    fertilized[crop] += 1
    return crops, fertilized, animals, len(farm.get("unlocked_quadrants") or []), len(farm.get("hands") or [])


def derive(trace, seat):
    """Derive every schema field, plus the raw numbers behind each one."""
    land_days, animal_buys = [], collections.defaultdict(list)
    plant_events = collections.Counter()
    sell_sizes = []
    hands_by_day = {}
    seeds_at_plant = collections.defaultdict(list)
    peak_crops, peak_animals, peak_fert = collections.Counter(), collections.Counter(), collections.Counter()
    first_crop_day, quadrants, hands_max = {}, 1, 0

    for index in range(1, len(trace)):
        entry = trace[index][seat]          # the action that PRODUCED steps[index]
        prev = trace[index - 1][seat]       # ...acting on the pre-state here
        day = (entry.get("observation") or {}).get("day", index // 24)

        action = entry.get("action")
        for order in market_orders(action):
            if order and order[0] == "BUY_LAND":
                land_days.append(day)
            elif len(order) >= 2 and order[0] == "BUY_ANIMAL":
                animal_buys[order[1]].append(day)
            elif len(order) >= 3 and order[0] == "SELL":
                size = _as_int(order[2], default=-1)
                if size >= 0:
                    sell_sizes.append(size)

        planted_this_turn = set()
        for unit in unit_actions(action):
            if isinstance(unit, (list, tuple)) and len(unit) >= 2 and unit[0] == "PLANT":
                plant_events[unit[1]] += 1
                planted_this_turn.add(unit[1])

        crops, fert, animals, quads, hands = tile_stats(entry.get("observation") or {}, seat)
        quadrants = max(quadrants, quads)
        hands_max = max(hands_max, hands)
        hands_by_day[day] = max(hands_by_day.get(day, 0), hands)
        for crop, n in crops.items():
            peak_crops[crop] = max(peak_crops[crop], n)
            first_crop_day.setdefault(crop, day)
        for animal, n in animals.items():
            peak_animals[animal] = max(peak_animals[animal], n)
        for crop, n in fert.items():
            peak_fert[crop] = max(peak_fert[crop], n)

        # Seeds the engine would see for THIS action: the pre-state at index-1.
        # Only the crops actually requested this turn, or the reserve is polluted
        # with crops that were not being planted at all.
        if planted_this_turn:
            seeds = ((prev.get("observation") or {}).get("private") or {}).get("seeds") or {}
            for crop in planted_this_turn:
                seeds_at_plant[crop].append(_as_int(seeds.get(crop, 0)))

    ramp = min((d for d, h in sorted(hands_by_day.items()) if h >= hands_max), default=0)

    crops_vec = []
    for crop in sorted(peak_crops):
        tiles = peak_crops[crop]
        events = plant_events.get(crop, 0)
        ongoing = crop in ONGOING_CROPS
        # Days per tile cycle: the season divided by how many times each tile's
        # crop was (re)planted. Only meaningful for one-shot crops.
        cycle = round(SEASON_DAYS * tiles / events) if events else 0
        crops_vec.append({
            "crop": crop,
            "tiles": tiles,
            "plant_first_day": first_crop_day.get(crop, 0),
            "replant_every": 0 if ongoing else cycle,
            "fertilize": peak_fert.get(crop, 0) > 0,
            "_evidence": {
                "plant_events": events,
                "ongoing": ongoing,
                "peak_fertilized_tiles": peak_fert.get(crop, 0),
                "fertilized_fraction": round(peak_fert.get(crop, 0) / tiles, 3) if tiles else 0.0,
                "seed_reserve_min": min(seeds_at_plant.get(crop, [0])) if seeds_at_plant.get(crop) else None,
            },
        })

    return {
        "schema": "kaggriculture-plan/1",
        "land": {"quadrants": quadrants, "buy_days": sorted(set(land_days))},
        "hands": {"daily_target": hands_max, "ramp_day": ramp},
        "crops": crops_vec,
        "animals": [
            {"animal": a, "count": peak_animals[a], "buy_days": sorted(set(animal_buys.get(a, [])))}
            for a in sorted(peak_animals)
        ],
        "market": {
            "sell_chunk_cap": max(sell_sizes) if sell_sizes else 0,
            "sell_from_day": 0,
            "seed_reserve": min(
                (min(v) for v in seeds_at_plant.values() if v), default=0
            ),
        },
        "_evidence": {
            "hands_distinct_values": sorted(set(hands_by_day.values())),
            "hands_first_and_last_day": [
                min(hands_by_day) if hands_by_day else None,
                max(hands_by_day) if hands_by_day else None,
            ],
            "sell_orders": len(sell_sizes),
            "sell_median": statistics.median(sell_sizes) if sell_sizes else 0,
            "sell_max": max(sell_sizes) if sell_sizes else 0,
            "buy_days_per_animal": {a: len(v) for a, v in sorted(animal_buys.items())},
        },
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seeds", type=int, default=3)
    args = ap.parse_args()

    per_seed = []
    for seed in range(args.seeds):
        trace = run_episode_trace(load_agent(str(CONTROL)), load_agent(str(CONTROL)), seed)
        per_seed.append(derive(trace["steps"], 0))

    first = per_seed[0]
    print("=== vector derivado del tape (seed 0) ===")
    print(json.dumps(first, indent=2, sort_keys=False))
    print()
    print("=== estabilidad entre seeds ===")
    for key in ("land", "hands", "market"):
        values = {json.dumps(p[key], sort_keys=True) for p in per_seed}
        print(f"  {key:<8} {'identico' if len(values) == 1 else 'VARIA: ' + str(len(values)) + ' variantes'}")
    crop_sets = {json.dumps(p["crops"], sort_keys=True) for p in per_seed}
    animal_sets = {json.dumps(p["animals"], sort_keys=True) for p in per_seed}
    print(f"  {'crops':<8} {'identico' if len(crop_sets) == 1 else 'VARIA: ' + str(len(crop_sets)) + ' variantes'}")
    print(f"  {'animals':<8} {'identico' if len(animal_sets) == 1 else 'VARIA: ' + str(len(animal_sets)) + ' variantes'}")

    print()
    print("=== VEREDICTO DE EXPRESIVIDAD (los huecos del schema) ===")
    gaps = []
    hands_values = first["_evidence"]["hands_distinct_values"]
    if len(hands_values) > 2:
        gaps.append(
            f"hands: el tape usa {len(hands_values)} valores distintos {hands_values}; el schema "
            "solo expresa daily_target + un ramp_day (escalon). Una rampa no captura una curva."
        )
    for c in first["crops"]:
        frac = c["_evidence"]["fertilized_fraction"]
        if 0 < frac < 0.99:
            gaps.append(
                f"crops[{c['crop']}].fertilize: el tape fertiliza {c['_evidence']['peak_fertilized_tiles']}"
                f"/{c['tiles']} tiles ({frac:.0%}); el schema es booleano por cultivo."
            )
    gaps.append(
        "market.sell_chunk_cap: el schema asume un tope uniforme; el tape no tiene tope "
        f"(mediana {first['_evidence']['sell_median']}, max {first['_evidence']['sell_max']}). "
        "Expresable solo como cap=100 = sin throttle, que pierde la intencion."
    )
    gaps.append(
        "crops[].replant_every: un unico periodo por cultivo no puede expresar un calendario "
        "no uniforme. Los plant_events por cultivo lo delatan."
    )
    for i, gap in enumerate(gaps, 1):
        print(f"  {i}. {gap}")
    if not gaps:
        print("  ninguno: el schema expresa el tape")
    print()
    print(f"  => {len(gaps)} huecos. Si el schema no describe el tape, un plan del LLM escrito")
    print("     contra ese schema hereda los mismos huecos. Arreglar antes de consumir W1.")


if __name__ == "__main__":
    main()
