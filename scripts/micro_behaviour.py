#!/usr/bin/env python
"""Micro-behaviour: routes, wasted actions, and intra-day sell timing.

Every analysis in this project so far has been at SEASON level - tile counts, animal
counts, revenue by product, win rates. That cannot see the things that decide thin
matches: whether a hand spends its day walking, whether an emitted action was a silent
no-op, and at which hour of the day the agent sells.

THE INVISIBLE WASTE. The engine silently no-ops an invalid action (`_apply_unit_action`:
"Invalid / illegal actions are silent no-ops"). So a `WATER` on a tile that holds no
crop, a `HARVEST` on an immature plant, a `COLLECT_FERTILIZER` where the animal has
none, a `FEED` on an already-fed animal - all of them LOOK like work in a verb count
and cost a worker turn. This measures the no-op fraction per verb, which no previous
analysis could see.

INTRA-DAY TIMING. Price is a function of market inventory, and the town drains once a
day, so the hour at which an agent sells is a choice with a price consequence. This
reports the hour-of-day distribution of sell orders and the units sold per hour.

THE INDEXING, settled empirically elsewhere in this repo: the action recorded at index
`i` is the one that PRODUCED the observation at index `i`, so it acted on the
pre-state at index `i-1`. Positions and tile states therefore come from `i-1`.

Usage:
    # our tape against real opponents, both profiled
    .venv/bin/python scripts/micro_behaviour.py --ours 24
    # the corpus itself: winner vs loser, same match
    .venv/bin/python scripts/micro_behaviour.py --corpus 60
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

MOVES = ("NORTH", "SOUTH", "EAST", "WEST")
WORK = ("PLANT", "WATER", "HARVEST", "FEED", "FERTILIZE", "COLLECT_FERTILIZER",
        "CARE", "DIG", "BUILD_COOP", "BUILD_PASTURE", "PLACE", "PICKUP", "DROP")
TAPE = REPO / "src" / "kaggriculture" / "agents" / "baseline_v8.py"


def _tile_at(obs, seat, pos):
    """The tile one unit stands on, from the state the action acted upon."""
    farms = obs.get("farms") or [{}]
    farm = farms[obs.get("player", seat)] or {}
    tiles = farm.get("tiles") or []
    x, y = pos[0], pos[1]
    if not (0 <= y < len(tiles) and 0 <= x < len(tiles[y])):
        return None
    return tiles[y][x]


def _noop(verb, tile):
    """Whether this action is a silent no-op at this tile. Engine-rule based."""
    if verb == "WATER":
        return not (isinstance(tile, dict) and tile.get("kind") == "PLANT") \
            or bool(tile.get("watered_today"))
    if verb == "HARVEST":
        return not (isinstance(tile, dict) and tile.get("yield_units", 0) > 0)
    if verb == "PLANT":
        return tile is not None
    if verb == "DIG":
        return tile is None or (isinstance(tile, dict) and "animal" in tile)
    if verb == "FEED":
        return not (isinstance(tile, dict) and "animal" in tile) or bool(tile.get("fed_today"))
    if verb == "COLLECT_FERTILIZER":
        return not (isinstance(tile, dict) and "animal" in tile) \
            or not tile.get("fertilizer_available")
    if verb == "FERTILIZE":
        return not (isinstance(tile, dict) and tile.get("kind") == "PLANT")
    if verb == "CARE":
        return not (isinstance(tile, dict) and "animal" in tile) or bool(tile.get("cared_today"))
    return False


def profile(steps, seat):
    """Verb mix, no-op waste, and sell timing for one seat over one match."""
    counts = collections.Counter()
    noops = collections.Counter()
    sell_hours = collections.Counter()
    sell_units_by_hour = collections.Counter()
    for index in range(1, len(steps)):
        entry = steps[index][seat] if seat < len(steps[index]) else {}
        pre = steps[index - 1][seat] if seat < len(steps[index - 1]) else {}
        pre_obs = pre.get("observation") or {}
        action = entry.get("action")
        if not isinstance(action, dict):
            continue

        farms = pre_obs.get("farms") or [{}]
        farm = farms[pre_obs.get("player", seat)] or {}
        positions = [farm.get("farmer"), *(farm.get("hands") or [])]
        units = [action.get("farmer"), *(action.get("hands") or [])]
        for idx, unit in enumerate(units):
            if not isinstance(unit, (list, tuple)) or not unit:
                continue
            verb = unit[0]
            counts[verb] += 1
            if verb in MOVES or verb == "PASS":
                continue
            pos = positions[idx] if idx < len(positions) else None
            if not isinstance(pos, (list, tuple)) or len(pos) < 2:
                continue
            if _noop(verb, _tile_at(pre_obs, seat, pos)):
                noops[verb] += 1

        for order in action.get("market") or []:
            if isinstance(order, (list, tuple)) and len(order) >= 3 and order[0] == "SELL":
                hour = pre_obs.get("hour", index % 24)
                sell_hours[hour] += 1
                try:
                    sell_units_by_hour[hour] += int(order[2])
                except (TypeError, ValueError):
                    continue
    return {"counts": counts, "noops": noops, "sell_hours": sell_hours,
            "sell_units_by_hour": sell_units_by_hour}


def summarise(profile_row):
    counts, noops = profile_row["counts"], profile_row["noops"]
    total = sum(counts.values())
    moves = sum(counts[v] for v in MOVES)
    passes = counts.get("PASS", 0)
    work = sum(counts[v] for v in WORK)
    wasted = sum(noops.values())
    return {
        "actions": total,
        "move_share": moves / total if total else 0,
        "pass_share": passes / total if total else 0,
        "work_share": work / total if total else 0,
        "noop_share": wasted / work if work else 0,
        "noops": dict(noops),
        "sell_hours": profile_row["sell_hours"],
    }


def report(title, rows):
    print(f"=== {title} ({len(rows)} asientos) ===")
    if not rows:
        return
    agg = {}
    for key in ("actions", "move_share", "pass_share", "work_share", "noop_share"):
        agg[key] = statistics.median([r[key] for r in rows])
    print(f"  acciones de unidad por temporada   {agg['actions']:>9,.0f}")
    print(f"  MOVIMIENTO (caminar)               {agg['move_share']:>9.1%}")
    print(f"  PASS (ocioso)                      {agg['pass_share']:>9.1%}")
    print(f"  TRABAJO (verbos utiles)            {agg['work_share']:>9.1%}")
    print(f"  de ese trabajo, NO-OP silencioso    {agg['noop_share']:>9.1%}   <-- desperdicio invisible")
    totals = collections.Counter()
    for row in rows:
        for verb, n in row["noops"].items():
            totals[verb] += n
    if totals:
        print("  no-ops por verbo (total, y por partida/asiento):")
        for verb, n in totals.most_common(6):
            print(f"     {verb:<20}{n:>7}  ({n / len(rows):.1f} por partida)")
    hours = collections.Counter()
    for row in rows:
        hours.update(row["sell_hours"])
    if hours:
        print("  horas del dia en que VENDE (top 6):")
        for hour, n in hours.most_common(6):
            print(f"     hora {hour:>2}: {n:>6} ordenes")


def corpus_rows(count):
    rows = []
    for path in sorted(CACHE.glob("*.json"))[:count]:
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        steps = data.get("steps") if isinstance(data, dict) else data
        if not steps or len(steps) < 100:
            continue
        banks = []
        for seat in (0, 1):
            obs = steps[-1][seat].get("observation") or {}
            farm = (obs.get("farms") or [{}])[obs.get("player", seat)] or {}
            banks.append(farm.get("money", 0))
        for seat in (0, 1):
            rows.append((banks[seat] >= banks[1 - seat], profile(steps, seat), summarise(profile(steps, seat))))
    return rows


def ours_rows(count, agent_path):
    rows = []
    for path in sorted(CACHE.glob("*.json"))[:count]:
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        steps = data.get("steps") if isinstance(data, dict) else data
        if not steps or len(steps) < 100:
            continue
        seed = ((data.get("info") or {}).get("seed") if isinstance(data, dict) else None) or 0
        for opponent_seat in (0, 1):
            opponent = make_replay_opponent(steps=steps, seat=opponent_seat)
            if opponent_seat == 1:
                pair, our_seat = (load_agent(str(agent_path)), opponent), 0
            else:
                pair, our_seat = (opponent, load_agent(str(agent_path))), 1
            try:
                trace = run_episode_trace(pair[0], pair[1], int(seed))
            except Exception as exc:  # noqa: BLE001
                # Printed, never swallowed: a dropped episode would shrink the sample
                # while every reported share still looked clean.
                print(f"  EPISODIO FALLADO {path.stem} asiento {opponent_seat}: "
                      f"{type(exc).__name__}: {exc}")
                continue
            steps_t = trace["steps"]
            ours = profile(steps_t, our_seat)
            theirs = profile(steps_t, opponent_seat)
            rows.append({
                "mine": summarise(ours), "theirs": summarise(theirs),
                "my_profile": ours, "their_profile": theirs,
                "won": trace["banks"][our_seat] > trace["banks"][opponent_seat],
            })
    return rows


def mirror_rows(seeds, agent_path):
    """Our tape against itself: no open-loop divergence, so any no-op is its own fault.

    This is the control for the --ours numbers. Against a replay opponent the state
    diverges (our agent changes the market), and a fixed tape acting on a state it did
    not produce will no-op through no fault of its own - measured: the OPPONENTS waste
    13.4% of their work in our games against 0% in their own.
    """
    rows = []
    for seed in range(seeds):
        trace = run_episode_trace(load_agent(str(agent_path)), load_agent(str(agent_path)), seed)
        p = profile(trace["steps"], 0)
        rows.append({"mine": {**summarise(p), "sell_hours": p["sell_hours"]}})
    return rows


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--corpus", type=int, default=0, help="matches to profile from the corpus")
    ap.add_argument("--ours", type=int, default=0, help="opponents to play our tape against")
    ap.add_argument("--agent", default=str(TAPE), help="agent for --ours")
    ap.add_argument("--mirror", type=int, default=0, help="seasons of our tape against itself")
    args = ap.parse_args()

    if args.mirror:
        report("NOSOTROS EN ESPEJO (sin divergencia)",
               [r["mine"] for r in mirror_rows(args.mirror, REPO / args.agent)])
        return

    if args.ours:
        rows = ours_rows(args.ours, REPO / args.agent)
        wins = sum(1 for r in rows if r["won"])
        print(f"partidas: {len(rows)}, ganamos {wins}/{len(rows)} = {wins / max(1, len(rows)):.1%}")
        report("NOSOTROS", [r["mine"] for r in rows])
        print()
        report("LOS OPONENTES", [r["theirs"] for r in rows])
        return

    rows = corpus_rows(args.corpus or 60)
    report("GANADORES (corpus)", [s for won, _, s in rows if won])
    print()
    report("PERDEDORES (corpus)", [s for won, _, s in rows if not won])


if __name__ == "__main__":
    main()
