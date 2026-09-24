#!/usr/bin/env python
"""Sale CONTEXT: at what market inventory do we sell, vs the top teams (V10 B3).

Phase B: product mix matches, realized price does not (strawberry $104 vs $148).
B2 refuted timing: our unit-weighted centre of mass is day 21.5 vs their 21.3.
Remaining variable: the market inventory at the moment of sale, because the
engine's price is a deterministic function of it. This reports, per product, the
inventory level (and quoted price) at the steps each side actually sells, plus
the correlation between the two, which is what a sell policy can act on.

Usage:
  .venv/bin/python scratch/sale_context.py --ours 'scratch/replays_v8/*.json.gz' \\
      --ours-team "Sergio D" --theirs scratch/corpus_full/11264*.json [--limit 30]
"""

import argparse
import collections
import glob
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from income_breakdown import decode, holdings, load, orders_by_verb  # noqa: E402

WATCH = ("STRAWBERRY", "MILK", "WOOL", "MELON", "EGG")


def sale_points(data, seat):
    """[(product, inventory, price, units)] for every executed-sale step."""
    steps = data["steps"]
    points = []
    prev = None
    for entry in steps:
        obs = entry[seat].get("observation") or {}
        held = holdings(obs)
        verbs = orders_by_verb(entry[seat].get("action"))
        if prev is not None and verbs.get("SELL"):
            money = ((obs.get("farms") or [{}])[obs.get("player", seat)]).get("money", 0)
            if money - prev["money"] > 0:
                market = obs.get("market") or {}
                prices = market.get("prices") or {}
                inventory = market.get("inventory") or {}
                for order in verbs["SELL"]:
                    if len(order) < 2:
                        continue
                    item = str(order[1])
                    drop = prev["held"].get(item, 0) - held.get(item, 0)
                    if drop > 0:
                        points.append((item, inventory.get(item, 0), prices.get(item, 0), drop))
        prev = {"money": ((obs.get("farms") or [{}])[obs.get("player", seat)]).get("money", 0),
                "held": held}
    return points


def collect(paths, team=None, limit=0):
    files = []
    for pattern in paths:
        files.extend(sorted(glob.glob(pattern)))
    if limit:
        files = files[:limit]
    points = []
    games = 0
    for path in files:
        try:
            data = load(path)
        except (OSError, ValueError) as exc:  # truncated/corrupt replay: skip, not abort
            print(f"!! {path}: {exc}")
            continue
        names = [decode(n) for n in data["info"]["TeamNames"]]
        for seat in (0, 1):
            if team and team.lower() not in names[seat].lower():
                continue
            points.extend(sale_points(data, seat))
            games += 1
    return points, games


def report(label, points, games):
    print(f"\n=== {label} ({games} partidas) ===")
    print(f"{'producto':<12} {'puntos':>7} {'unid.':>7} {'inventario medio':>17} "
          f"{'p25':>7} {'p75':>7} {'precio medio':>13} {'corr(inv,precio)':>17}")
    by_product = collections.defaultdict(list)
    for item, inventory, price, units in points:
        by_product[item].append((inventory, price, units))
    for item in WATCH:
        rows = by_product.get(item)
        if not rows:
            continue
        invs = [r[0] for r in rows]
        prices = [r[1] for r in rows]
        units = sum(r[2] for r in rows)
        corr = 0.0
        if len(set(invs)) > 1 and len(set(prices)) > 1:
            corr = statistics.correlation(invs, prices)
        print(f"{item:<12} {len(rows):>7} {units / max(1, games):>7,.0f} "
              f"{statistics.fmean(invs):>17,.0f} {statistics.quantiles(invs, n=4)[0]:>7,.0f} "
              f"{statistics.quantiles(invs, n=4)[2]:>7,.0f} "
              f"{statistics.fmean(prices):>13,.1f} {corr:>17.3f}")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ours", nargs="+", required=True)
    ap.add_argument("--theirs", nargs="+", required=True)
    ap.add_argument("--ours-team", default=None)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    our_points, our_games = collect(args.ours, team=args.ours_team, limit=args.limit)
    their_points, their_games = collect(args.theirs)
    report("NOSOTROS", our_points, our_games)
    report("TOP-10", their_points, their_games)


if __name__ == "__main__":
    main()
