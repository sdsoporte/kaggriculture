#!/usr/bin/env python
"""Our REAL ladder record, from our own episodes.

Everything in this project's evaluation so far has been a proxy: the published
reference ladder (too weak - we beat it 10/10 by $100k+), replay opponents from the
daily datasets (open-loop and days old), and the arena mirror (our agent against
itself, which cannot show our standing in the field). This reads the actual episodes
our ladder bots played and computes the real win rate against the live field.

Why it matters now: matchmaking is rating-banded. Our bots sit around 1200 and the
top of the ladder around 3000, and across ~499 of our episodes and 138 concurrent
ones from one top player, the episode-id sets did not intersect ONCE - we never get
matched with that band. So the top 10 is not reachable by beating those teams; the
only route is winning enough in OUR band to climb into theirs. This measures whether
that is happening.

Each replay is ~36 MB, so only a bounded sample is fetched and each file is deleted
after it is read.

Usage:
    .venv/bin/python scripts/ladder_record.py --submission 56513137 --episodes 30
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TMP = REPO / "scratch" / "ladder_tmp"
sys.path.insert(0, str(REPO / "src"))


def _run(args):
    return subprocess.run(args, check=True, capture_output=True, text=True).stdout


def _as_int(value):
    """int() that never raises: a malformed CLI row must not stop the sweep."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def list_episodes(submission_id):
    """Episode ids for a submission, oldest last, as the CLI reports them."""
    out = _run(["kaggle", "competitions", "episodes", str(submission_id), "-v"])
    ids = []
    for line in out.splitlines():
        parsed = _as_int(line.split(",")[0].strip())
        if parsed is not None:
            ids.append(parsed)
    return ids


def fetch(episode_id):
    TMP.mkdir(parents=True, exist_ok=True)
    path = TMP / f"episode-{episode_id}-replay.json"
    if not path.is_file():
        try:
            _run(["kaggle", "competitions", "replay", str(episode_id), "-p", str(TMP), "-q"])
        except (OSError, subprocess.CalledProcessError) as exc:
            detail = getattr(exc, "stderr", "")
            if not detail:
                detail = str(exc)
            print(f"  {episode_id}: no se pudo bajar ({detail.strip()[:200]})")
            return None
    return path if path.is_file() else None


def outcome(path, our_name):
    """(our bank, their bank, their name, seed) or None if we cannot identify our seat."""
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    steps = data.get("steps")
    if not steps or len(steps) < 100:
        return None
    names = list((data.get("info") or {}).get("TeamNames") or [])
    if len(names) < 2:
        return None
    banks = []
    for seat in (0, 1):
        obs = steps[-1][seat].get("observation") or {}
        farm = (obs.get("farms") or [{}])[obs.get("player", seat)] or {}
        banks.append(farm.get("money", 0))
    ours = [i for i, n in enumerate(names) if our_name.lower() in str(n).lower()]
    if len(ours) != 1:
        return None
    seat = ours[0]
    return banks[seat], banks[1 - seat], names[1 - seat], (data.get("info") or {}).get("seed")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--submission", required=True, help="our submission id, e.g. 56513137")
    ap.add_argument("--episodes", type=int, default=30, help="how many to fetch")
    ap.add_argument("--name", default="Sergio", help="our team name, as it appears in replays")
    args = ap.parse_args()

    ids = list_episodes(args.submission)[:args.episodes]
    print(f"submission {args.submission}: {len(ids)} episodios a revisar en esta corrida")
    print()

    rows, skipped = [], 0
    for episode_id in ids:
        path = fetch(episode_id)
        if path is None:
            skipped += 1
            continue
        try:
            result = outcome(path, args.name)
        finally:
            path.unlink(missing_ok=True)  # 36 MB each: never accumulate
        if result is None:
            skipped += 1
            continue
        rows.append((episode_id, *result))

    if not rows:
        print("no se pudo leer ningun resultado; revisar --name")
        return

    wins = sum(1 for _, mine, theirs, _, _ in rows if mine > theirs)
    losses = sum(1 for _, mine, theirs, _, _ in rows if mine < theirs)
    ties = len(rows) - wins - losses
    margins = [mine - theirs for _, mine, theirs, _, _ in rows]
    print(f"{'episodio':>10}{'nuestro':>12}{'rival':>12}{'delta':>10}  rival")
    for episode_id, mine, theirs, name, _ in rows[:12]:
        print(f"{episode_id:>10}{mine:>12,.0f}{theirs:>12,.0f}{mine - theirs:>+10,.0f}  {name}")
    if len(rows) > 12:
        print(f"   ... y {len(rows) - 12} mas")
    print()
    print(f"WIN RATE REAL: {wins}G-{losses}P-{ties}E = {wins / len(rows):.1%}  ({len(rows)} partidas, {skipped} saltadas)")
    print(f"margen medio: {sum(margins) / len(margins):+,.0f}")
    print()
    print("esta es la medicion contra el campo que SI nos empareja (nuestra banda de rating),")
    print("no contra la escalera publicada ni contra grabaciones: los episodios reales del bot.")


if __name__ == "__main__":
    main()
