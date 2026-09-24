#!/usr/bin/env python
"""Sell timing: WHEN the volume is sold, and at what price (V10 phase B2).

Phase B showed our product mix already matches the top teams and we lose on the
REALIZED PRICE of the two biggest products (strawberry $104 vs $131-197, milk
$75 vs $108-153), while melon/wheat/wool/fertilizer are at parity. This measures
the remaining candidate mechanism: timing. If the top teams sell steadily from
early season and we dump late into a saturated market, the fix is a sell POLICY
inside the tape - no replanning, no new structures.

Reuses income_breakdown's primitives, including its settled price pairing
(offset 0: the executed units of a step pair with the price quoted at that same
step).

Usage:
  .venv/bin/python scratch/sell_timing.py --ours 'scratch/replays_v8/*.json.gz' \\
      --theirs scratch/corpus_full/11264*.json [--ours-team "Sergio D"] [--limit 30]
"""

import argparse
import collections
import glob
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from income_breakdown import decode, holdings, load, orders_by_verb  # noqa: E402

WATCH = ("STRAWBERRY", "MILK", "WOOL", "MELON")


def game_days(data, seat):
    """Per-day units and price-weighted revenue for every product, one game."""
    steps = data["steps"]
    days = collections.defaultdict(lambda: collections.defaultdict(lambda: [0, 0.0]))
    prev = None
    for t, entry in enumerate(steps):
        obs = entry[seat].get("observation") or {}
        farm = (obs.get("farms") or [{}])[obs.get("player", seat)]
        day = int(obs.get("day", t // 24))
        held = holdings(obs)
        verbs = orders_by_verb(entry[seat].get("action"))
        if prev is not None and verbs.get("SELL"):
            delta = farm.get("money", 0) - prev["money"]
            if delta > 0:
                prices = (obs.get("market") or {}).get("prices") or {}
                priced = {}
                for order in verbs["SELL"]:
                    if len(order) < 2:
                        continue
                    item = str(order[1])
                    drop = prev["held"].get(item, 0) - held.get(item, 0)
                    if drop > 0:
                        priced[item] = drop * prices.get(item, 0)
                total = sum(priced.values())
                for item, weight in priced.items():
                    share = delta * weight / total if total else delta / len(priced)
                    units = prev["held"].get(item, 0) - held.get(item, 0)
                    days[day][item][0] += units
                    days[day][item][1] += share
        prev = {"money": farm.get("money", 0), "held": held}
    return days


def collect(paths, team=None, limit=0):
    files = []
    for pattern in paths:
        files.extend(sorted(glob.glob(pattern)))
    if limit:
        files = files[:limit]
    games = []
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
            games.append(game_days(data, seat))
    return games


def mean_by_day(games, product, key):
    """Average across games of units (key=0) or revenue (key=1) per day."""
    out = collections.defaultdict(float)
    for game in games:
        for day, items in game.items():
            out[day] += items.get(product, [0, 0.0])[key]
    return {day: value / max(1, len(games)) for day, value in out.items()}


def center_of_mass(units_by_day):
    total = sum(units_by_day.values())
    if not total:
        return None
    return sum(day * u for day, u in units_by_day.items()) / total


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ours", nargs="+", required=True)
    ap.add_argument("--theirs", nargs="+", required=True)
    ap.add_argument("--ours-team", default=None)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    ours = collect(args.ours, team=args.ours_team, limit=args.limit)
    theirs = collect(args.theirs)
    print(f"partidas nuestras: {len(ours)} | partidas top-10: {len(theirs)}")

    for product in WATCH:
        ou = mean_by_day(ours, product, 0)
        orr = mean_by_day(ours, product, 1)
        tu = mean_by_day(theirs, product, 0)
        tr = mean_by_day(theirs, product, 1)
        ot, tt = sum(ou.values()), sum(tu.values())
        if not ot and not tt:
            continue
        oprice = sum(orr.values()) / ot if ot else 0
        tprice = sum(tr.values()) / tt if tt else 0
        print(f"\n=== {product} ===  nosotros {ot:,.0f} u/partida @ ${oprice:,.1f} | "
              f"top-10 {tt:,.0f} u/partida @ ${tprice:,.1f}")
        print(f"  centro de masa de las ventas (dia): "
              f"nosotros {center_of_mass(ou) if center_of_mass(ou) is not None else 0:.1f} | "
              f"top-10 {center_of_mass(tu) if center_of_mass(tu) is not None else 0:.1f}")
        for day in range(30):
            if not ou.get(day) and not tu.get(day):
                continue
            o_p = orr.get(day, 0) / ou[day] if ou.get(day) else 0
            t_p = tr.get(day, 0) / tu[day] if tu.get(day) else 0
            print(f"    d{day:>2} | ours {ou.get(day, 0):>6,.0f}u ${o_p:>6,.1f} "
                  f"(${orr.get(day, 0):>8,.0f}) | theirs {tu.get(day, 0):>6,.0f}u "
                  f"${t_p:>6,.1f} (${tr.get(day, 0):>8,.0f})")


if __name__ == "__main__":
    main()
