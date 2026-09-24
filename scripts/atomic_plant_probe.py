#!/usr/bin/env python
"""How much does the engine's ATOMIC PLANT rule cost our tape? (read-only)

Found in stage A of `odd/tasks/llm-in-the-loop.md` by handing two models the
complete engine source, then verified by reading it:

    kaggriculture.py:919-931

        # Atomic PLANT validation: if total PLANT requests for a crop this turn
        # exceed available seeds, drop ALL PLANT requests for that crop.
        blocked = {crop for crop, n in plant_demand.items() if n > seeds.get(crop, 0)}

It is NOT a partial plant. If three hands request `PLANT WHEAT` and the farm holds
two wheat seeds, all three actions become `PASS` and the crop is voided for the
turn - silently, with no warning printed.

THE INDEXING TRAP, and it bit the first version of this probe. A replay entry
`steps[i][seat]` holds an observation AND an action, but the action is the one
that PRODUCED that observation: `action[i]` acts on the pre-state at
`steps[i-1]`. The engine reads `s.observation.private` at the moment of the step,
so the seeds to compare against are the ones in `steps[i-1]`, NOT `steps[i]`.
Reading the same index shows seeds already consumed by this turn's planting and
invents blockages that do not exist - the first run of this probe reported 7.91%
of all PLANT actions voided that way, against a tape that measurably lands 239 of
its 240 plantings. This is the fourth time this project has been bitten by
`steps[turn][seat]`; the trace line below is the only place it is settled.

Read-only. Nothing is patched, no overlay is built. If the incidence is zero the
rule is theoretical for our tape and the finding closes here; if it is not, the
count sizes the next experiment.

The loss is reported as an UPPER BOUND on plantings voided: a voided `PLANT` is
converted to `PASS` before any tile check, so we cannot know from one trace how
many of them would have landed on a free tile anyway. Incidence first, value
second.

Usage:
    .venv/bin/python scripts/atomic_plant_probe.py --seeds 8
"""

import argparse
import collections
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from kaggriculture.loading import load_agent  # noqa: E402
from kaggriculture.runner import run_episode_trace  # noqa: E402

CONTROL = REPO / "src" / "kaggriculture" / "agents" / "baseline_v8.py"

# Order-of-magnitude value of one landed planting, from the economic table in
# `odd/tasks/v10-sell-roster-and-peak-count.md`: ~11 wheat units at a ~$24 marginal
# margin, ~8 strawberry units at ~$107. Used only to size the finding.
MARGIN_PER_PLANTING = {"WHEAT": 11 * 24, "STRAWBERRY": 8 * 107, "CARROT": 4 * 32}


def seeds_held(obs):
    """Crop seeds the agent holds: `private["seeds"]`, the table the engine reads."""
    private = obs.get("private")
    if not isinstance(private, dict):
        return {}
    seeds = private.get("seeds")
    return seeds if isinstance(seeds, dict) else {}


def plant_demand(action):
    """How many PLANT actions of each crop this turn's units request."""
    demand = collections.Counter()
    if not isinstance(action, dict):
        return demand
    units = [action.get("farmer")] + list(action.get("hands") or [])
    for unit in units:
        if isinstance(unit, (list, tuple)) and len(unit) >= 2 and unit[0] == "PLANT":
            crop = unit[1]
            if isinstance(crop, str):
                demand[crop] += 1
    return demand


def scan(trace, seat):
    """Every turn where the atomic PLANT rule fires, for one seat.

    `action[i]` acts on the pre-state at `steps[i-1]`, which is where the engine
    reads the seed table. Comparing against `steps[i]` is the trap.
    """
    blocked_turns = []
    total_plants = 0
    for index in range(1, len(trace)):
        entry = trace[index][seat]  # the action that PRODUCED steps[index]
        pre = trace[index - 1][seat]  # ...acting on the pre-state here
        demand = plant_demand(entry.get("action"))
        if not demand:
            continue
        total_plants += sum(demand.values())
        seeds = seeds_held(pre.get("observation") or {})
        blocked = {
            crop: n for crop, n in demand.items() if n > seeds.get(crop, 0)
        }
        if blocked:
            blocked_turns.append({
                "step": index,
                "day": index // 24,
                "blocked": blocked,
                "seeds": {c: seeds.get(c, 0) for c in blocked},
            })
    return {
        "blocked_turns": blocked_turns,
        "voided_plantings": sum(sum(b["blocked"].values()) for b in blocked_turns),
        "total_plants": total_plants,
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seeds", type=int, default=8)
    args = ap.parse_args()

    seats = []
    for seed in range(args.seeds):
        # A mirror match: both seats are our tape, so one episode yields two
        # observations of it. Note the environment is mirror-symmetric across
        # seeds, so seeds - not seat-count - are the effective sample.
        trace = run_episode_trace(load_agent(str(CONTROL)), load_agent(str(CONTROL)), seed)
        for seat in (0, 1):
            result = scan(trace["steps"], seat)
            result["seed"] = seed
            result["seat"] = seat
            seats.append(result)

    games = args.seeds
    print(f"episodios: {games} (espejo) x 2 asientos = {len(seats)} observaciones del tape")
    print()
    header = f"{'seed':>4} {'seat':>4} {'turnos bloqueados':>17} {'plantings anulados':>19} {'PLANT totales':>14}"
    print(header)
    for r in seats:
        print(f"{r['seed']:>4} {r['seat']:>4} {len(r['blocked_turns']):>17} "
              f"{r['voided_plantings']:>19} {r['total_plants']:>14}")

    mean_turns = sum(len(r["blocked_turns"]) for r in seats) / len(seats)
    mean_voided = sum(r["voided_plantings"] for r in seats) / len(seats)
    mean_plants = sum(r["total_plants"] for r in seats) / len(seats)
    print()
    print(f"turnos bloqueados por partida-asiento : {mean_turns:.2f}")
    print(f"plantings anulados por partida-asiento: {mean_voided:.2f}")
    print(f"PLANT totales por partida-asiento     : {mean_plants:.0f}")
    if mean_plants:
        print(f"fraccion de PLANT anulados            : {100 * mean_voided / mean_plants:.2f}%")

    by_crop = collections.Counter()
    for r in seats:
        for b in r["blocked_turns"]:
            for crop, n in b["blocked"].items():
                by_crop[crop] += n
    if by_crop:
        print()
        print("cultivos afectados (total sobre todas las observaciones):")
        for crop, n in by_crop.most_common():
            per_game = n / len(seats)
            margin = MARGIN_PER_PLANTING.get(crop)
            sizing = f"  ~${per_game * margin:,.0f}/partida si todos hubieran prendido" if margin else ""
            print(f"  {crop:<12} {n:>6} anulados = {per_game:.2f}/partida{sizing}")
        print()
        print("OJO: es una COTA SUPERIOR. Un PLANT anulado pasa a PASS antes de mirar el")
        print("tile, asi que no sabemos cuantos habrian caido igual en tile libre.")
        print("Ademas el engine espeja los asientos dentro de un seed, asi que la muestra")
        print("efectiva son los seeds, no las observaciones.")
    else:
        print()
        print("INCIDENCIA CERO: la regla atomica de PLANT no dispara en nuestro tape.")
        print("Es teorica para nosotros y este hallazgo se cierra aca.")


if __name__ == "__main__":
    main()
