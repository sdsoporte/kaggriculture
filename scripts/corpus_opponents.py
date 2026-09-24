#!/usr/bin/env python
"""Measure our agent's win rate against real top-team replay opponents.

The corpus cache ``scratch/corpus_full/*.json`` holds real ladder matches, i.e.
the only opposition in this project that is not trivially beatable. This script
turns each selected replay seat into a live opponent with
:mod:`kaggriculture.agents.replay_opponent` and plays our agent against it
**paired** (our agent on seat 0, then on seat 1, same opponent, same seed), the
convention documented in :mod:`kaggriculture.harness`.

Two modes:

* default          -- measure win rate over ``--opponents`` corpus replays.
* ``--reproduce N`` -- acceptance test: replay BOTH seats of N records and check
  that the live engine reproduces the recorded final banks. Run this before
  trusting any win-rate number. It also prints an offset sweep for the first
  replay (the actions at index ``i`` vs ``i-1``) so the alignment is visible,
  not asserted.

Usage:
    .venv/bin/python scripts/corpus_opponents.py --opponents 24
    .venv/bin/python scripts/corpus_opponents.py --reproduce 3

Nothing is written to disk: every number goes to stdout, and replays are loaded
one at a time and released before the next.
"""

import argparse
import glob
import json
import math
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from kaggriculture.agents.replay_opponent import (  # noqa: E402
    ACTION_OFFSET,
    make_replay_opponent,
)
from kaggriculture.loading import load_agent  # noqa: E402
from kaggriculture.runner import run_episode_trace  # noqa: E402

#: Engine pin in .venv. A replay recorded with another version cannot be
#: expected to reproduce and is reported, not silently averaged in.
ENGINE_PIN = "1.32.7"

#: Replay headers (configuration/info/rewards) appear before the huge steps
#: array, so this many bytes is enough to read metadata without a 35 MB parse.
META_PREFIX_BYTES = 262144

#: A final bank at or below this means the seat effectively did not play.
NEAR_ZERO_BANK = 1000.0

#: A full season is 719 played turns; fewer means an agent stalled or crashed.
FULL_SEASON_TURNS = 719


def read_metadata(path):
    """Read a replay's header without parsing its steps array.

    Returns a dict with module_version, seed, episode_id, teams and rewards, or
    ``None`` when the header cannot be read/parsed.
    """
    try:
        with open(path, "rb") as fh:
            head = fh.read(META_PREFIX_BYTES)
    except OSError:
        return None
    text = head.decode("utf-8", "replace")
    cut = text.find('"steps": [')
    if cut < 0:
        cut = text.find('"steps":')
    if cut < 0:
        return None
    try:
        meta = json.loads(text[:cut].rstrip().rstrip(",") + "}")
    except ValueError:
        return None
    if not isinstance(meta, dict):
        return None
    info = meta.get("info")
    if not isinstance(info, dict):
        info = {}
    configuration = meta.get("configuration")
    if not isinstance(configuration, dict):
        configuration = {}
    seed = info.get("seed")
    if seed is None:
        seed = configuration.get("seed")
    return {
        "module_version": meta.get("module_version"),
        "seed": seed,
        "episode_id": info.get("EpisodeId"),
        "teams": info.get("TeamNames"),
        "rewards": meta.get("rewards"),
    }


def select_opponents(corpus, count):
    """First ``count`` sorted ``*.json`` filenames whose engine version is pinned.

    Returns ``(selected, skipped, total)`` where ``selected`` is a list of
    ``(path, metadata)``. Selection is deliberately mechanical so the reported
    opponent set is reproducible from the filenames alone.
    """
    files = sorted(glob.glob(os.path.join(corpus, "*.json")))
    selected, skipped = [], []
    for path in files:
        meta = read_metadata(path)
        if meta is None:
            skipped.append((os.path.basename(path), "unreadable header"))
            continue
        if meta["module_version"] != ENGINE_PIN:
            skipped.append((os.path.basename(path), f"engine {meta['module_version']}"))
            continue
        selected.append((path, meta))
        if len(selected) >= count:
            break
    return selected, skipped, len(files)


def _seat_error_counts(steps):
    """Per-seat count of ERROR/INVALID/TIMEOUT statuses across the whole trace."""
    counts = [0, 0]
    for turn in steps:
        for seat, state in enumerate(turn):
            if seat >= len(counts):
                continue
            if state.get("status") in ("ERROR", "INVALID", "TIMEOUT"):
                counts[seat] += 1
    return counts


def play_paired(our_path, steps, seed):
    """Play our agent vs one replay opponent on both seats with the same seed.

    Returns a list of per-game dicts from our agent's perspective. Our agent is
    re-imported for every game because ``load_agent`` reloads the module and the
    baseline agents keep season state in a module-level singleton.
    """
    games = []
    for our_seat in (0, 1):
        opponent_seat = 1 - our_seat
        our_agent = load_agent(our_path)
        opponent = make_replay_opponent(seat=opponent_seat, steps=steps)
        order = [our_agent, opponent] if our_seat == 0 else [opponent, our_agent]
        summary = run_episode_trace(order[0], order[1], seed)

        banks = summary["banks"]
        our_bank = banks[our_seat]
        their_bank = banks[opponent_seat]
        seat_errors = _seat_error_counts(summary["steps"])
        last = summary["steps"][-1]
        final_status = [last[0].get("status"), last[1].get("status")]
        games.append({
            "our_seat": our_seat,
            "opponent_seat": opponent_seat,
            "our_bank": our_bank,
            "their_bank": their_bank,
            "margin": our_bank - their_bank,
            "turns": summary["turns"],
            "our_errors": seat_errors[our_seat],
            "opponent_errors": seat_errors[opponent_seat],
            "our_final_status": final_status[our_seat],
            "opponent_final_status": final_status[opponent_seat],
        })
        del summary
    return games


def wilson_interval(wins, games, z=1.96):
    """95% Wilson score interval for a binomial proportion."""
    if games <= 0:
        return 0.0, 0.0
    p = wins / games
    denom = 1.0 + z * z / games
    center = (p + z * z / (2 * games)) / denom
    half = z * math.sqrt(p * (1 - p) / games + z * z / (4 * games * games)) / denom
    return max(0.0, center - half), min(1.0, center + half)


def _mean(values):
    return sum(values) / len(values) if values else 0.0


def run_measurement(args):
    selected, skipped, total = select_opponents(args.corpus, args.opponents)
    print(f"corpus: {os.path.abspath(args.corpus)}  ({total} *.json files)")
    print(f"engine pin required: {ENGINE_PIN}; selected {len(selected)} opponents "
          f"by first sorted filename, skipped {len(skipped)}")
    for name, reason in skipped[:10]:
        print(f"  skipped {name}: {reason}")
    if len(skipped) > 10:
        print(f"  ... {len(skipped) - 10} more skipped")
    print(f"agent under test: {args.agent}   seeds: each opponent's recorded seed")
    print()

    header = (f"{'episode':>10} {'seed':>11} {'G':>1} {'W':>1} {'win%':>5} "
              f"{'mean_margin':>12} {'our_bank':>10} {'their_bank':>11} {'turns':>7}  flags")
    print(header)
    print("-" * len(header))

    rows = []
    suspicious = []
    all_margins = []
    all_our_banks = []
    all_their_banks = []
    total_games = 0
    total_wins = 0
    seat_games = {0: 0, 1: 0}
    seat_wins = {0: 0, 1: 0}
    seat_margins = {0: [], 1: []}

    for path, meta in selected:
        try:
            with open(path) as fh:
                data = json.load(fh)
            steps = data.get("steps")
            if not isinstance(steps, list):
                raise ValueError("no steps list")
        except (OSError, ValueError) as exc:
            print(f"{str(meta.get('episode_id')):>10}  LOAD FAILED: {exc}")
            suspicious.append((meta.get("episode_id"), f"load failed: {exc}"))
            continue

        seed = args.seed if args.seed is not None else meta["seed"]
        games = play_paired(args.agent, steps, seed)
        del steps, data

        wins = sum(1 for g in games if g["margin"] > 0)
        margins = [g["margin"] for g in games]
        our_banks = [g["our_bank"] for g in games]
        their_banks = [g["their_bank"] for g in games]
        all_margins.extend(margins)
        all_our_banks.extend(our_banks)
        all_their_banks.extend(their_banks)
        total_games += len(games)
        total_wins += wins
        for g in games:
            seat_games[g["our_seat"]] += 1
            if g["margin"] > 0:
                seat_wins[g["our_seat"]] += 1
            seat_margins[g["our_seat"]].append(g["margin"])

        flags = []
        for g in games:
            if g["opponent_errors"] > 0:
                flags.append(f"opp ERRx{g['opponent_errors']}")
            if g["our_errors"] > 0:
                flags.append(f"our ERRx{g['our_errors']}")
            if g["turns"] < FULL_SEASON_TURNS:
                flags.append(f"stall {g['turns']}")
            if g["our_bank"] <= NEAR_ZERO_BANK:
                flags.append(f"our bank~0 seat{g['our_seat']}")
            if g["their_bank"] <= NEAR_ZERO_BANK:
                flags.append(f"their bank~0 seat{g['opponent_seat']}")
        flags = sorted(set(flags))

        row = {
            "episode_id": meta.get("episode_id"),
            "seed": seed,
            "games": len(games),
            "wins": wins,
            "mean_margin": _mean(margins),
            "mean_our_bank": _mean(our_banks),
            "mean_their_bank": _mean(their_banks),
            "flags": flags,
            "games_detail": games,
        }
        rows.append(row)
        if flags:
            suspicious.append((meta.get("episode_id"), "; ".join(flags)))

        print(f"{str(meta.get('episode_id')):>10} {str(seed):>11} {len(games):>1} "
              f"{wins:>1} {wins / len(games):>5.2f} {_mean(margins):>12,.0f} "
              f"{_mean(our_banks):>10,.0f} {_mean(their_banks):>11,.0f} "
              f"{'/'.join(str(g['turns']) for g in games):>7}  {','.join(flags) or '-'}")

    print()
    print("=" * len(header))
    if total_games:
        rate = total_wins / total_games
        lo, hi = wilson_interval(total_wins, total_games)
        print(f"OVERALL: games={total_games} wins={total_wins} "
              f"win_rate={rate:.3f}  95% Wilson CI [{lo:.3f}, {hi:.3f}]")
        print(f"mean margin across all games: {_mean(all_margins):,.1f}")
        print(f"mean our bank: {_mean(all_our_banks):,.1f}   "
              f"mean their bank: {_mean(all_their_banks):,.1f}")
        print("  by seat (our agent on seat 0 / seat 1):")
        for seat in (0, 1):
            if seat_games[seat]:
                print(f"    seat {seat}: games={seat_games[seat]} wins={seat_wins[seat]} "
                      f"rate={seat_wins[seat] / seat_games[seat]:.3f} "
                      f"mean_margin={_mean(seat_margins[seat]):,.1f}")
    else:
        print("OVERALL: no games played")
    print(f"distinct opponents used: {len(rows)} (selected as the first {len(rows)} "
          f"sorted filenames with engine {ENGINE_PIN})")
    if suspicious:
        print("SUSPICIOUS:")
        for episode_id, reason in suspicious:
            print(f"  {episode_id}: {reason}")
    else:
        print("SUSPICIOUS: none -- every game ran a full 719 turns with both "
              "seats above the near-zero bank threshold")


def reproduce_replay(path, meta, offsets=(1,)):
    """Replay both recorded seats and return results per candidate offset."""
    try:
        with open(path) as fh:
            data = json.load(fh)
    except OSError as exc:
        raise FileNotFoundError(f"cannot read replay {path}: {exc}") from exc
    except ValueError as exc:
        raise ValueError(f"replay {path} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"replay {path} is not a JSON object")
    steps = data.get("steps")
    if not isinstance(steps, list):
        raise ValueError(f"replay {path} has no steps list")
    recorded = data.get("rewards")
    seed = meta["seed"]
    results = []
    for offset in offsets:
        agent0 = make_replay_opponent(seat=0, steps=steps, offset=offset)
        agent1 = make_replay_opponent(seat=1, steps=steps, offset=offset)
        summary = run_episode_trace(agent0, agent1, seed)

        banks = list(summary["banks"])
        live_steps = summary["steps"]
        mismatches = 0
        for i in range(min(len(live_steps), len(steps))):
            for seat in (0, 1):
                try:
                    live_money = live_steps[i][seat]["observation"]["farms"][seat]["money"]
                    rec_money = steps[i][seat]["observation"]["farms"][seat]["money"]
                except (KeyError, IndexError, TypeError):
                    mismatches += 1
                    continue
                if live_money != rec_money:
                    mismatches += 1
        results.append({
            "offset": offset,
            "banks": banks,
            "match": recorded is not None and list(recorded) == banks,
            "trajectory_mismatches": mismatches,
            "turns": summary["turns"],
        })
        del summary, live_steps
    del steps, data
    return results


def run_reproduction(args):
    selected, skipped, total = select_opponents(args.corpus, max(args.reproduce, 1))
    print("Acceptance test: reproduce the recorded match by replaying BOTH seats.")
    print(f"corpus: {os.path.abspath(args.corpus)}  ({total} *.json files); "
          f"engine pin {ENGINE_PIN}")
    print(f"offset convention: action index = observed turn + ACTION_OFFSET "
          f"(= {ACTION_OFFSET})\n")

    for n, (path, meta) in enumerate(selected):
        offsets = (-1, 0, 1) if n == 0 else (1,)
        results = reproduce_replay(path, meta, offsets=offsets)
        print(f"{os.path.basename(path)}  episode={meta['episode_id']} "
              f"engine={meta['module_version']} seed={meta['seed']}")
        print(f"  recorded banks: {meta['rewards']}")
        for r in results:
            print(f"  offset {r['offset']:+d}: reproduced banks={r['banks']} "
                  f"match={r['match']} trajectory_mismatches={r['trajectory_mismatches']} "
                  f"turns={r['turns']}")
        canonical = [r for r in results if r["offset"] == 1]
        if canonical and not canonical[0]["match"]:
            print("  *** REPRODUCTION FAILED at the canonical offset +1 ***")
        print()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", default="scratch/corpus_full",
                        help="directory of replay JSON files")
    parser.add_argument("--agent", default="src/kaggriculture/agents/baseline_v8.py",
                        help="our agent file under test")
    parser.add_argument("--opponents", type=int, default=24,
                        help="number of corpus opponents to measure against")
    parser.add_argument("--reproduce", type=int, default=0, metavar="N",
                        help="run the acceptance test on N replays instead of measuring")
    parser.add_argument("--seed", type=int, default=None,
                        help="override every seed (default: each opponent's recorded seed)")
    args = parser.parse_args()

    if args.reproduce:
        run_reproduction(args)
    else:
        run_measurement(args)


if __name__ == "__main__":
    main()
