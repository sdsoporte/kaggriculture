#!/usr/bin/env python
"""Paired benchmark over N seeds: same seeds, both seats.

Usage: .venv/bin/python scripts/benchmark.py <a.py> <b.py> [--seeds 8] [--start 0]
All numbers are from A's perspective. Margins are bankA - bankB.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from kaggriculture.harness import paired_benchmark  # noqa: E402
from kaggriculture.loading import load_agent  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("agent_a", help="path to agent A .py file, or builtin name")
    parser.add_argument("agent_b", help="path to agent B .py file, or builtin name")
    parser.add_argument("--seeds", type=int, default=8)
    parser.add_argument("--start", type=int, default=0, help="first seed")
    args = parser.parse_args()

    agent_a = load_agent(args.agent_a)
    agent_b = load_agent(args.agent_b)
    seeds = list(range(args.start, args.start + args.seeds))
    report = paired_benchmark(agent_a, agent_b, seeds)

    print(f"paired benchmark: A={args.agent_a}  B={args.agent_b}")
    print(f"{'seed':>6} | {'A bank (seat0)':>14} {'B bank':>8} {'margin':>8} | "
          f"{'A bank (seat1)':>14} {'B bank':>8} {'margin':>8}")
    print("-" * 78)
    for row in report["per_seed"]:
        ab, ba = row["ab_banks"], row["ba_banks"]
        print(f"{row['seed']:>6} | {ab[0]:>14.0f} {ab[1]:>8.0f} {row['margins'][0]:>8.0f} | "
              f"{ba[0]:>14.0f} {ba[1]:>8.0f} {row['margins'][1]:>8.0f}")
    print("-" * 78)
    print(f"games {report['games']} | A wins {report['wins']} | "
          f"A losses {report['losses']} | ties {report['ties']}")
    print(f"A mean margin: {report['mean_margin']:.1f} "
          f"(seat0 {report['mean_margin_by_seat']['seat0']:.1f} | "
          f"seat1 {report['mean_margin_by_seat']['seat1']:.1f})")


if __name__ == "__main__":
    main()
