#!/usr/bin/env python
"""Test our agent against the recorded play of ONE real episode.

A new capability, and a cheap one: `kaggle competitions replay <episode_id>` fetches a
single match without downloading a daily dataset, and `kaggle competitions episodes
<submission_id>` lists a submission's recent games. So any named opponent's current
play can be turned into an adversary in seconds.

Why this matters for the goal: the ladder is scored by WIN RATE, and our only
non-degenerate opponents used to be a fixed local sample of older episodes. This
reaches the live field - including whichever team a specific episode names.

THE HONEST CAVEATS, printed with every run because they decide how to read it:

  * A replay opponent is OPEN-LOOP. It repeats recorded actions verbatim and cannot
    react to the market our agent creates, so it is a LOWER BOUND on difficulty. The
    same caveat measured elsewhere in this project: the recorded agents waste 13.4% of
    their work in our games against 0% in their own.
  * ONE episode is ONE seed. Treat a single result as an anecdote and a series of
    episodes as evidence.

Usage:
    .venv/bin/python scripts/episode_test.py --episode 113165315
    .venv/bin/python scripts/episode_test.py --episode 113165315 --agent src/kaggriculture/agents/baseline_v8_1.py
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CACHE = REPO / "scratch" / "episodes"
sys.path.insert(0, str(REPO / "src"))

from kaggriculture.agents.replay_opponent import make_replay_opponent  # noqa: E402
from kaggriculture.loading import load_agent  # noqa: E402
from kaggriculture.runner import run_episode_trace  # noqa: E402

DEFAULT_AGENTS = (
    "src/kaggriculture/agents/baseline_v8.py",
    "src/kaggriculture/agents/baseline_v8_1.py",
)


def fetch(episode_id):
    """Return the local path of this episode's replay, downloading it if needed."""
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / f"episode-{episode_id}-replay.json"
    if path.is_file():
        return path
    try:
        subprocess.run(
            ["kaggle", "competitions", "replay", str(episode_id), "-p", str(CACHE)],
            check=True, capture_output=True, text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        detail = getattr(exc, "stderr", "") or str(exc)
        raise SystemExit(f"could not fetch episode {episode_id}: {detail.strip()[:300]}") from None
    if not path.is_file():
        raise SystemExit(f"fetch reported success but {path} is missing")
    return path


def recorded_banks(steps):
    banks = []
    for seat in (0, 1):
        obs = steps[-1][seat].get("observation") or {}
        farm = (obs.get("farms") or [{}])[obs.get("player", seat)] or {}
        banks.append(farm.get("money", 0))
    return banks


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--episode", required=True, help="episode id, e.g. 113165315")
    ap.add_argument("--agent", action="append", help="agent path (repeatable)")
    args = ap.parse_args()

    path = fetch(args.episode)
    data = json.loads(path.read_text())
    steps = data.get("steps")
    if not steps or len(steps) < 100:
        raise SystemExit(f"{path.name} has no usable steps")
    info = data.get("info") or {}
    names = list(info.get("TeamNames") or ["seat0", "seat1"])
    seed = int(info.get("seed") or 0)
    banks = recorded_banks(steps)

    print(f"episodio {args.episode} | engine {data.get('module_version')} | seed {seed}")
    print(f"grabado: {names[0]} ${banks[0]:,.0f}  vs  {names[1]} ${banks[1]:,.0f}"
          f"   -> gano {names[0] if banks[0] > banks[1] else names[1]}")
    print()

    agents = args.agent or list(DEFAULT_AGENTS)
    header = f"{'nuestro agente':<34}{'rival':<20}{'nosotros':>12}{'ellos':>12}{'resultado':>12}"
    print(header)
    print("-" * len(header))
    for agent_path in agents:
        label = Path(agent_path).stem
        for opponent_seat in (0, 1):
            opponent = make_replay_opponent(path=str(path), seat=opponent_seat, steps=steps)
            if opponent_seat == 1:
                trace = run_episode_trace(load_agent(str(REPO / agent_path)), opponent, seed)
                mine, theirs = trace["banks"][0], trace["banks"][1]
            else:
                trace = run_episode_trace(opponent, load_agent(str(REPO / agent_path)), seed)
                mine, theirs = trace["banks"][1], trace["banks"][0]
            verdict = "GANAMOS" if mine > theirs else ("empate" if mine == theirs else "perdemos")
            print(f"{label:<34}{names[opponent_seat]:<20}{mine:>12,.0f}{theirs:>12,.0f}{verdict:>12}")

    print()
    print("caveat 1: oponente OPEN-LOOP - repite lo grabado y no reacciona a nuestro mercado,")
    print("          asi que es una COTA INFERIOR de dificultad, no un win rate de escalera.")
    print("caveat 2: UN episodio es UN seed. Un resultado suelto es anecdota; una serie es evidencia.")
    print("para una serie:  kaggle competitions episodes <submission_id>  y correr varios ids.")


if __name__ == "__main__":
    main()
