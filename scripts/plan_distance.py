#!/usr/bin/env python
"""How far are the LLM's plans from the tape? (D1, the cheap decision point)

The from-scratch consumer (W2b) does not survive a season yet, and building it to
beat a 46 KB tuned tape is a large project with a real chance of not landing before
the 2026-09-30 deadline. Two validated `kaggriculture-plan/3` plans exist, and so does
an extractor for the tape's own season-level numbers. So the question that decides
the next move is cheap: **how far apart are they?**

If the plans differ from the tape in a few fields and by small magnitudes, then a
bounded re-plan of the tape captures most of their value at a fraction of the cost of
a consumer from scratch. If they differ everywhere, the re-plan is not a shortcut and
the consumer is the only path.

This measures the SEASON-LEVEL decisions, which is what a re-plan would change:
quadrants, the labour curve, per-crop tile counts and planting calendars, the animal
roster, and the market knobs. It does not compare 720 actions.

The tape's numbers are derived from mirror episodes and reported as medians across
seeds, because the tape is state-reactive and its own figures vary (see
`scripts/tape_to_plan.py` for why).

Usage:
    .venv/bin/python scripts/plan_distance.py --seeds 4
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
PLANS_DIR = REPO / "plans"
TURNS_PER_DAY = 24
CROP_ORDER = ("WHEAT", "CARROT", "TOMATO", "STRAWBERRY", "MELON")
ANIMAL_ORDER = ("COW", "SHEEP", "GOOSE")


# --------------------------------------------------------------------------- #
# The tape's own season-level numbers.
# --------------------------------------------------------------------------- #

def _as_int(value, default=0):
    """int() that never raises: a malformed order must not stop the measurement."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def tape_profile(trace, seat):
    """Flat numeric profile of one tape episode: the fields a re-plan would move."""
    profile = {
        "quadrants": 1, "hands_peak": 0, "hands_day0": 0,
        "sell_lot_median": 0, "sell_lot_max": 0, "seeds_at_plant_median": 0,
        # Per-crop peaks happen on DIFFERENT days, so summing them does not describe a
        # layout. This is the peak number of crop tiles standing at once, which is the
        # number comparable to a plan's sum of crops[].tiles.
        "crop_tiles_peak": 0, "animal_tiles_peak": 0,
    }
    for crop in CROP_ORDER:
        profile[f"tiles_{crop}"] = 0
        profile[f"plant_days_{crop}"] = 0
    for animal in ANIMAL_ORDER:
        profile[f"count_{animal}"] = 0

    tiles_by_crop = collections.Counter()
    plant_days = collections.defaultdict(set)
    animals = collections.Counter()
    lots, seeds_seen = [], []
    day0_hands = None

    for index in range(1, len(trace)):
        entry = trace[index][seat]        # the action that PRODUCED steps[index]
        pre = trace[index - 1][seat]      # ...acting on the pre-state here
        obs = entry.get("observation") or {}
        day = obs.get("day", index // TURNS_PER_DAY)
        farm = (obs.get("farms") or [{}])[obs.get("player", seat)] or {}

        profile["quadrants"] = max(profile["quadrants"], len(farm.get("unlocked_quadrants") or []))
        hands = len(farm.get("hands") or [])
        profile["hands_peak"] = max(profile["hands_peak"], hands)
        if day == 0 and (day0_hands is None or hands > day0_hands):
            day0_hands = hands

        action = entry.get("action")
        units = []
        if isinstance(action, dict):
            units = [action.get("farmer"), *(action.get("hands") or [])]
            for order in action.get("market") or []:
                if (isinstance(order, (list, tuple)) and len(order) >= 3
                        and order[0] == "SELL" and isinstance(order[1], str)):
                    lots.append(_as_int(order[2]))
        for kind in units:
            if isinstance(kind, (list, tuple)) and len(kind) >= 2 and kind[0] == "PLANT":
                plant_days[kind[1]].add(day)
                seeds = ((pre.get("observation") or {}).get("private") or {}).get("seeds") or {}
                seeds_seen.append(_as_int(seeds.get(kind[1], 0)))

        current = collections.Counter()
        now_animals = collections.Counter()
        for row in farm.get("tiles") or []:
            for cell in row:
                if not isinstance(cell, dict):
                    continue
                if cell.get("animal"):
                    now_animals[cell["animal"]] += 1
                elif cell.get("kind") == "PLANT":
                    current[cell.get("crop")] += 1
        for crop, count in current.items():
            tiles_by_crop[crop] = max(tiles_by_crop[crop], count)
        # Peak PER TURN, not a running sum. Summing across turns produced 5,427 cows.
        for animal, count in now_animals.items():
            animals[animal] = max(animals[animal], count)
        profile["crop_tiles_peak"] = max(profile["crop_tiles_peak"], sum(current.values()))
        profile["animal_tiles_peak"] = max(profile["animal_tiles_peak"], sum(now_animals.values()))

    profile["hands_day0"] = day0_hands or 0
    for crop in CROP_ORDER:
        profile[f"tiles_{crop}"] = tiles_by_crop.get(crop, 0)
        profile[f"plant_days_{crop}"] = len(plant_days.get(crop, ()))
    for animal in ANIMAL_ORDER:
        profile[f"count_{animal}"] = animals.get(animal, 0)
    profile["sell_lot_median"] = statistics.median(lots) if lots else 0
    profile["sell_lot_max"] = max(lots) if lots else 0
    profile["seeds_at_plant_median"] = statistics.median(seeds_seen) if seeds_seen else 0
    return profile


def tape_median(seeds):
    profiles = []
    for seed in range(seeds):
        trace = run_episode_trace(load_agent(str(CONTROL)), load_agent(str(CONTROL)), seed)
        profiles.append(tape_profile(trace["steps"], 0))
    keys = profiles[0]
    return {key: statistics.median([p[key] for p in profiles]) for key in keys}


# --------------------------------------------------------------------------- #
# The same fields, read out of a plan.
# --------------------------------------------------------------------------- #

def plan_profile(plan):
    crops = {entry["crop"]: entry for entry in plan["crops"]}
    animals = {entry["animal"]: entry for entry in plan["animals"]}
    profile = {
        "quadrants": plan["land"]["quadrants"],
        "hands_peak": plan["hands"]["daily_target"],
        "hands_day0": plan["hands"]["start"],
        "sell_lot_median": plan["market"]["sell_chunk_cap"],
        "sell_lot_max": plan["market"]["sell_chunk_cap"] or "unlimited",
        "seeds_at_plant_median": plan["market"]["seed_reserve"],
        "crop_tiles_peak": sum(entry["tiles"] for entry in plan["crops"]),
        "animal_tiles_peak": sum(entry["count"] for entry in plan["animals"]),
    }
    for crop in CROP_ORDER:
        entry = crops[crop]
        profile[f"tiles_{crop}"] = entry["tiles"]
        profile[f"plant_days_{crop}"] = len(entry["plant_days"])
    for animal in ANIMAL_ORDER:
        profile[f"count_{animal}"] = animals[animal]["count"]
    return profile


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seeds", type=int, default=4)
    args = ap.parse_args()

    tape = tape_median(args.seeds)
    plans = {}
    for path in sorted(PLANS_DIR.glob("*.json")):
        # A plan that will not parse must stop the measurement, not be skipped: a
        # silently missing plan would look like "we only had one to compare".
        try:
            plans[path.stem] = plan_profile(json.loads(path.read_text()))
        except (OSError, ValueError) as exc:
            raise SystemExit(f"cannot read {path.name}: {exc}") from exc

    print(f"el tape, mediana de {args.seeds} episodios espejo (asiento 0)")
    print()
    header = f"{'campo':<24}{'tape':>9}" + "".join(f"{name[:22]:>24}" for name in plans)
    print(header)
    print("-" * len(header))
    for key in tape:
        row = f"{key:<24}{_fmt(tape[key]):>9}"
        for plan in plans.values():
            row += f"{_fmt(plan[key]):>24}"
        print(row)

    print()
    print("=== distancia: en cuantos campos el plan difiere del tape ===")
    for name, plan in plans.items():
        differing = [k for k in tape if plan[k] != tape[k]]
        big = [k for k in differing if _relative(tape[k], plan[k]) > 0.25]
        print(f"  {name:<32} {len(differing):>2}/{len(tape)} campos distintos,"
              f" {len(big)} por mas de 25%")
        for key in big:
            print(f"      {key:<22} tape {_fmt(tape[key]):>7} -> plan {_fmt(plan[key]):>7}")


def _fmt(value):
    if isinstance(value, float):
        return f"{value:,.0f}"
    return str(value)


def _relative(tape_value, plan_value):
    if not isinstance(tape_value, (int, float)) or not isinstance(plan_value, (int, float)):
        return 1.0
    if tape_value == 0:
        return 0.0 if plan_value == 0 else 1.0
    return abs(plan_value - tape_value) / abs(tape_value)


if __name__ == "__main__":
    main()
