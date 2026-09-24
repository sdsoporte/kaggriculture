#!/usr/bin/env python
"""Income breakdown: where a player's money actually comes from (V10 phase B).

Answers, per player: revenue by product, units sold, REALIZED price per product,
sell frequency, and purchases (wheat / fertilizer / animals / seeds). That
separates "what they produce" from "when and how they sell", which is the
question that decides whether the sell layer (V10 phase C) can close the gap.

Method:
  * revenue/spend are the OBSERVED per-step money deltas (a replay records every
    player's money each turn), so the totals need no price pairing at all;
  * a step's revenue is split across the products whose holdings actually fell
    on that step, weighted by units x price. Executed units are taken from the
    holdings drop, NEVER from the requested quantity: the strong teams submit
    `SELL <item> 10000` (sell everything), so requested quantities overstate
    revenue by orders of magnitude - the first version of this script did that
    and missed the books by ~8x.
  * holdings = private.shed + private.inventories, and internal shed<->inventory
    moves cancel inside that sum.

Validation of the PRICE PAIRING (the off-by-one we had open): on steps that sell
exactly one product, the revenue is fully determined by (observed units x quoted
price), so comparing that prediction against the observed delta says which price
index is right - the same turn (offset 0) or the previous one (offset -1). The
script reports both errors instead of assuming.

Usage:
  .venv/bin/python scratch/income_breakdown.py <path-or-glob...> [--team NAME]
"""

import argparse
import collections
import glob
import gzip
import json
from pathlib import Path

BUY_VERBS = ("BUY_PRODUCT", "BUY_ANIMAL", "BUY_SEED", "HIRE", "BUY_LAND")


def load(path):
    raw = Path(path).read_bytes()
    if path.endswith(".gz"):
        raw = gzip.decompress(raw)
    return json.loads(raw)


def decode(name):
    return json.loads(f'"{name}"')


def holdings(obs):
    """Units of every product held: shed plus every farmer's inventory."""
    private = obs.get("private") or {}
    held = collections.Counter(private.get("shed") or {})
    for inv in private.get("inventories") or []:
        held.update(inv)
    return held


def orders_by_verb(action):
    out = collections.defaultdict(list)
    if isinstance(action, dict):
        for order in action.get("market") or []:
            if isinstance(order, (list, tuple)) and order:
                out[str(order[0])].append(order)
    return out


def analyse(data, seat, price_offset):
    """Breakdown for one seat, anchored to the observed money deltas."""
    steps = data["steps"]
    revenue = spend = 0
    sold = collections.Counter()
    product_rev = collections.Counter()
    sell_turns = 0
    max_orders = 0
    buys = collections.Counter()
    single_obs = single_pred = 0.0

    prev = None
    for t, entry in enumerate(steps):
        obs = entry[seat].get("observation") or {}
        farm = (obs.get("farms") or [{}])[obs.get("player", seat)]
        money = farm.get("money", 0)
        held = holdings(obs)
        verbs = orders_by_verb(entry[seat].get("action"))
        max_orders = max(max_orders, sum(len(v) for v in verbs.values()))
        sells = verbs.get("SELL") or []
        if sells:
            sell_turns += 1

        if prev is not None:
            delta = money - prev["money"]
            if delta > 0:
                revenue += delta
            elif delta < 0:
                spend += -delta

            if sells and delta > 0:
                priced = {}
                for order in sells:
                    if len(order) < 2:
                        continue
                    item = str(order[1])
                    drop = prev["held"].get(item, 0) - held.get(item, 0)
                    if drop <= 0:
                        continue
                    sold[item] += drop
                    price = 0
                    src = t + price_offset
                    if 0 <= src < len(steps):
                        market = (steps[src][seat].get("observation") or {}).get("market") or {}
                        price = (market.get("prices") or {}).get(item, 0)
                    priced[item] = drop * price

                total_weight = sum(priced.values())
                if len(priced) == 1:
                    single_obs += delta
                    single_pred += next(iter(priced.values()))
                if total_weight > 0:
                    for item, value in priced.items():
                        product_rev[item] += delta * value / total_weight
                elif priced:
                    for item in priced:
                        product_rev[item] += delta / len(priced)

        for verb in BUY_VERBS:
            for order in verbs.get(verb, []):
                if verb in ("BUY_PRODUCT", "BUY_SEED") and len(order) > 2:
                    buys[f"{verb}:{order[1]}"] += int(order[2])
                elif verb == "BUY_ANIMAL" and len(order) > 1:
                    buys[f"BUY_ANIMAL:{order[1]}"] += 1
                else:
                    buys[verb] += 1

        prev = {"money": money, "held": held}

    return {
        "revenue": revenue,
        "spend": spend,
        "sold": sold,
        "product_rev": product_rev,
        "sell_turns": sell_turns,
        "max_orders": max_orders,
        "buys": buys,
        "closure": abs(single_pred - single_obs),
        "single_share": single_obs / revenue if revenue else 0.0,
    }


def parse_paths(patterns):
    out = []
    for pattern in patterns:
        out.extend(sorted(glob.glob(pattern)))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="+", help="replay paths or globs (.json / .json.gz)")
    ap.add_argument("--team", default=None, help="only analyse seats whose team name matches")
    ap.add_argument("--limit", type=int, default=0, help="cap the number of files")
    args = ap.parse_args()

    files = parse_paths(args.paths)
    if args.limit:
        files = files[: args.limit]

    rows = []
    closure = collections.Counter()
    for path in files:
        try:
            data = load(path)
        except (OSError, ValueError) as exc:  # truncated/corrupt replay: skip, not abort
            print(f"!! {path}: {exc}")
            continue
        names = [decode(n) for n in data["info"]["TeamNames"]]
        for seat in (0, 1):
            if args.team and args.team.lower() not in names[seat].lower():
                continue
            books = {offset: analyse(data, seat, offset) for offset in (0, -1)}
            for offset, book in books.items():
                closure[offset] += book["closure"]
            best = min(books, key=lambda k: books[k]["closure"] if books[k]["closure"] else 10**18)
            row = books[best]
            rows.append({"path": path, "team": names[seat], "seat": seat,
                         "best": best, **row})

    total_rev = sum(r["revenue"] for r in rows)
    print(f"archivos: {len(files)} | filas de jugador: {len(rows)} | "
          f"ingreso total observado: ${total_rev:,.0f}")
    chosen = collections.Counter(r["best"] for r in rows)
    print(f"offset de precio elegido por fila: {chosen.most_common()}")
    for offset in (0, -1):
        print(f"  offset {offset:>2}: error de cierre ${closure[offset]:,.0f} "
              f"({100 * closure[offset] / max(1, total_rev):.2f}% del ingreso)")

    by_team = collections.defaultdict(list)
    for r in rows:
        by_team[r["team"]].append(r)

    for team, rs in sorted(by_team.items(), key=lambda kv: -len(kv[1]))[:14]:
        n = len(rs)
        rev = sum(r["revenue"] for r in rs) / n
        spend = sum(r["spend"] for r in rs) / n
        sell_turns = sum(r["sell_turns"] for r in rs) / n
        prod = collections.Counter()
        units = collections.Counter()
        buys = collections.Counter()
        for r in rs:
            prod.update(r["product_rev"])
            units.update(r["sold"])
            buys.update(r["buys"])
        total = max(1, sum(prod.values()))
        print(f"\n{team[:30]:<30} n={n} | ingreso ${rev:,.0f} | gasto ${spend:,.0f} | "
              f"turnos con venta {sell_turns:.0f} | max ordenes/turno "
              f"{max(r['max_orders'] for r in rs)}")
        for item, value in prod.most_common(6):
            u = units.get(item, 0)
            price = value / u if u else 0
            print(f"    {item:<12} ${value:>9,.0f} ({100 * value / total:>4.1f}%) "
                  f"u={u:<5} precio medio ${price:>6.1f}")
        print("    compras: " + " ".join(f"{k}:{v}" for k, v in buys.most_common(6)))


if __name__ == "__main__":
    main()
