#!/usr/bin/env python
"""Run a single Kaggriculture match.

Usage: .venv/bin/python scripts/run_match.py <a.py> <b.py> [--seed 42]
Agent arguments are .py files exposing agent(obs), or builtin names
(pass, random, starter).
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from kaggriculture.loading import load_agent  # noqa: E402
from kaggriculture.runner import run_episode  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("agent_a", help="path to agent A .py file, or builtin name")
    parser.add_argument("agent_b", help="path to agent B .py file, or builtin name")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    result = run_episode(load_agent(args.agent_a), load_agent(args.agent_b), args.seed)
    bank_a, bank_b = result["banks"]
    winner = {0: "A", 1: "B", None: "tie"}[result["winner"]]
    print(f"seed {result['seed']} | {result['turns']} turns")
    print(f"A bank: {bank_a:.0f}   B bank: {bank_b:.0f}   winner: {winner}")


if __name__ == "__main__":
    main()
