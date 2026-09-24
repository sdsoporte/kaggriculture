#!/usr/bin/env python
"""Profile a tape season: structure capacity, worker idleness, roster timing.

Exploration tool for V10 T3. It answers the question that decides the design:
does the tape have SLOTS to host more animals (empty structures + free worker
turns), or can it only host a different MIX at the same slots?

Usage:
  .venv/bin/python scratch/tape_profile.py <agent.py> [--seed 0]
"""

import argparse
import collections
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from kaggriculture.loading import load_agent  # noqa: E402
from kaggriculture.runner import run_episode_trace  # noqa: E402

TURNS_PER_DAY = 24
ANIMALS = ("COW", "SHEEP", "GOOSE")
STRUCTURES = ("PASTURE", "COOP")


def _farm(step):
    obs = step["observation"]
    return obs["farms"][obs["player"]]


def _shed(step):
    return (step["observation"].get("private") or {}).get("shed") or {}


def _idle(action):
    if action is None:
        return True
    if isinstance(action, str):
        return action == "PASS"
    return len(action) == 0 or action[0] == "PASS"


def _worker_actions(step):
    action = step.get("action")
    if not isinstance(action, dict):
        return []
    return [action.get("farmer")] + list(action.get("hands") or [])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("agent")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--opponent", default="submissions/main_v8.py")
    args = parser.parse_args()

    episode = run_episode_trace(load_agent(args.agent), load_agent(args.opponent), args.seed)
    steps = episode["steps"]

    buy_orders = []
    build_orders = []
    verbs = collections.Counter()
    market_verbs = collections.Counter()

    for turn, step in enumerate(steps):
        action = step[0].get("action")
        for order in (action.get("market") if isinstance(action, dict) else None) or []:
            if not isinstance(order, (list, tuple)) or not order:
                continue
            verb = str(order[0])
            market_verbs[verb] += 1
            if verb == "BUY_ANIMAL" and len(order) > 1:
                buy_orders.append((turn, order[1]))
            elif verb in ("BUILD_PASTURE", "BUILD_COOP"):
                build_orders.append((turn, verb))
        for worker in _worker_actions(step[0]):
            if isinstance(worker, (list, tuple)) and worker:
                verbs[str(worker[0])] += 1
            elif not _idle(worker):
                verbs[str(worker)] += 1

    print(f"agent {args.agent} seed {args.seed} vs {args.opponent}"
          f" | banks seat0 {episode['banks'][0]:,.0f}")
    print(f"{'day':>3} | {'money':>8} | {'COOP oc/tot':>11} {'PAST oc/tot':>11} "
          f"{'free':>4} | {'animals':<18} | {'idle':>4} {'wheat':>5} {'shed':>4}")
    print("-" * 92)

    idle_by_day = collections.Counter()
    idle_total = 0
    worker_total = 0
    peak_free = 0
    last = len(steps) - 1
    for day in range((last // TURNS_PER_DAY) + 1):
        end = min(day * TURNS_PER_DAY + TURNS_PER_DAY - 1, last)
        farm = _farm(steps[end][0])
        coop = pasture = occupied = coop_occ = pasture_occ = 0
        animals = collections.Counter()
        for row in farm.get("tiles", []):
            for tile in row:
                if not isinstance(tile, dict):
                    continue
                if tile.get("kind") in STRUCTURES:
                    occupied_here = bool(tile.get("animal"))
                    if tile["kind"] == "COOP":
                        coop += 1
                        coop_occ += occupied_here
                    else:
                        pasture += 1
                        pasture_occ += occupied_here
                    if occupied_here:
                        occupied += 1
                        animals[tile["animal"]] += 1
        free = coop + pasture - occupied
        peak_free = max(peak_free, free)

        idle = 0
        workers = 0
        for turn in range(day * TURNS_PER_DAY, end + 1):
            for worker in _worker_actions(steps[turn][0]):
                workers += 1
                if _idle(worker):
                    idle += 1
        idle_by_day[day] = idle
        idle_total += idle
        worker_total += workers

        shed = _shed(steps[end][0])
        shown = " ".join(f"{a}:{animals[a]}" for a in ANIMALS if animals.get(a)) or "-"
        print(f"{day:>3} | {farm.get('money', 0):>8,.0f} | {coop_occ:>4}/{coop:>5} "
              f"{pasture_occ:>4}/{pasture:>5} {free:>4} | {shown:<18} | {idle:>4} "
              f"{shed.get('WHEAT', 0):>5} {sum(shed.values()):>4}")

    print("-" * 92)
    print(f"peak empty structure slots: {peak_free}")
    print(f"worker turns: {worker_total} | idle: {idle_total} "
          f"({100 * idle_total / max(1, worker_total):.1f}%)")
    print(f"busiest idle days: {idle_by_day.most_common(4)}")
    print(f"BUY_ANIMAL orders ({len(buy_orders)}): {buy_orders}")
    print(f"BUILD orders ({len(build_orders)}): {build_orders}")
    print(f"worker verbs: {verbs.most_common(10)}")
    print(f"market verbs: {market_verbs.most_common(10)}")


if __name__ == "__main__":
    main()
