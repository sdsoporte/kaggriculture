"""Cards for a bounded worker-reallocation continuation.

The single-slot lane measured out at zero because a free slot only reaches the
tile beneath the worker. `free_runs.py` measured what is actually available
instead: across two full Arlene games, 107 runs of four or more consecutive free
turns for one worker, 104 of which could reach a tile holding collectable yield or
available fertilizer and act on it inside the run, and 56 of which could also
carry the goods back to the depot inside the same run.

A card is one such moment. It carries the exact observation, which worker is free,
and every reachable target enumerated from the observed state with its grounded
economics: what is on the tile, how many units, what those units fetch at the
CURRENT quoted price, how far the worker is, and how far that tile is from the
depot. No future price, no future shop, no seed identity, no RNG.

The route can reclaim the worker on any later turn -- that is not knowable in
advance and is not hidden from the model. A plan that strands goods away from the
depot costs the seat, and that cost shows up in the paired full-game comparison
rather than being gated out beforehand.
"""

import argparse
import copy
import json
import os

# `cards` and `route_cards` are harvesting-side only. They are imported inside the
# functions that need them so that importing this module for `reachable_targets`
# alone -- which is all a packaged agent needs -- pulls in nothing that reaches for
# the evaluator package.

MAX_TARGETS = 6


def _dist(a, b):
    return abs(int(a[0]) - int(b[0])) + abs(int(a[1]) - int(b[1]))


def depot_tiles(board):
    h = board // 2
    return [(h - 1, h - 1), (h, h - 1), (h - 1, h), (h, h)]


def _pricer(K):
    """The engine's own `market_price(item, inventory, params)`.

    Resolved from the caller's module when it provides the API. `engine_pin.py`
    now bundles it verbatim alongside the unit-phase transition, so a hosted
    archive does not need `kaggle_environments` importable at runtime to price a
    candidate; the import below stays as a fallback for a caller that hands in
    some other module. If nothing provides it the import raises, deliberately: an
    unavailable price must stay distinguishable from an absent opportunity, never
    be folded into an empty target list.
    """
    if hasattr(K, "market_price"):
        return K.market_price
    try:
        import engine_pin
        return engine_pin.market_price
    except Exception:
        pass
    from kaggle_environments.envs.kaggriculture import kaggriculture as R
    return R.market_price


def marginal_revenue(K, market, item, units):
    """What `units` of `item` would actually fetch, through the ENGINE's own pricing.

    Not units x the quoted price: the market reprices after every single unit
    (_process_market quotes market_price at the running inventory and commits one
    unit at a time), so a stack of units is worth strictly less than its first one.

    This is still only an upper bound on INCREMENTAL value. The baseline harvests
    and sells most of this stock itself later; what an excursion adds is at most the
    part the route never reaches, and how it prices depends on where it lands in a
    shared book. The paired full-game margin decides, not this number.
    """
    inv = dict(market.get("inventory") or {})
    params = market.get("params")
    price = _pricer(K)
    total = 0.0
    n = int(inv.get(item, 0))
    for _ in range(int(units)):
        p = float(price(item, n, params))
        total += p
        # The engine only credits market supply when the sale cleared above the
        # floor: `_commit_unit` does `if price > 1: market["inventory"][item] += 1`.
        # Advancing the inventory on a floor sale made every further unit look
        # cheaper than the engine will actually quote it.
        if p > 1:
            n += 1
    return round(total, 1)


def reachable_targets(obs, seat, pos, K, board, prices):
    """Every tile this worker could walk to and collect from, priced by the engine."""
    market = obs["market"]
    farm = obs["farms"][seat]
    out = []
    for y, row in enumerate(farm["tiles"]):
        for x, t in enumerate(row):
            if not isinstance(t, dict):
                continue
            d = _dist(pos, (x, y))
            dep = min(_dist((x, y), p) for p in depot_tiles(board))
            if t.get("animal") is not None:
                units = int(t.get("yield_units", 0))
                product = K.ANIMALS[t["animal"]]["product"]
                if units > 0:
                    out.append({
                        "at": [x, y], "op": ["HARVEST"], "dist": d,
                        "depot_dist": dep, "units": units, "product": product,
                        "value_now": marginal_revenue(K, market, product, units),
                        "what": f"{t['animal']} holding {units} {product}",
                        "persists": True,
                        # everything the cap test needs, read straight off the tile
                        "animal_meta": {
                            "animal": t["animal"], "yield_units": units,
                            "placed_day": int(t.get("placed_day", 0)),
                            "fed_today": bool(t.get("fed_today")),
                            "pending_care_bonus": int(
                                t.get("pending_care_bonus", 0) or 0)}})
                if t.get("fertilizer_available"):
                    out.append({
                        "at": [x, y], "op": ["COLLECT_FERTILIZER"], "dist": d,
                        "depot_dist": dep, "units": 1, "product": "FERTILIZER",
                        "value_now": marginal_revenue(K, market, "FERTILIZER", 1),
                        "what": f"{t['animal']} with fertilizer standing available",
                        "persists": True})
            elif t.get("kind") == "PLANT":
                units = int(t.get("yield_units", 0))
                if units > 0:
                    cd = K.CROPS[t["crop"]]
                    out.append({
                        "at": [x, y], "op": ["HARVEST"], "dist": d,
                        "depot_dist": dep, "units": units, "product": t["crop"],
                        "value_now": marginal_revenue(K, market, t["crop"], units),
                        "what": (f"{t['crop']} plant holding {units}/"
                                 f"{cd['max_yield']} units"
                                 + ("; an ongoing crop survives HARVEST"
                                    if cd["ongoing"] else
                                    "; HARVEST DESTROYS a non-ongoing crop and "
                                    "forfeits any growth it had left")),
                        "persists": bool(cd["ongoing"])})
    out.sort(key=lambda r: (-r["value_now"] / max(1, r["dist"] + 1), r["dist"]))
    return out[:MAX_TARGETS]


def harvest(seed, seat, opponent_spec, min_free=1, limit=None):
    import cards as cards_mod
    import route_cards
    from kaggle_environments.envs.kaggriculture import kaggriculture as K
    A, arl_id = route_cards.load_arlene()
    opp, opp_id = route_cards.load_agent(opponent_spec)
    env = cards_mod.make_env(seed)
    env.reset(2)
    mine, theirs = A.Agent(), A.Agent()
    out = []
    run_start = {}
    while not env.done:
        acts = [None, None]
        for i in range(2):
            obs = env.state[i].observation
            if i == seat:
                a = mine.act(obs)
                farm, priv = obs["farms"][seat], obs["private"]
                tiles = farm["tiles"]
                board = len(tiles)
                seeds = priv.get("seeds") or {}
                invs = priv.get("inventories") or []
                prices = obs["market"]["prices"]
                pos = [farm["farmer"]] + list(farm.get("hands", []))
                units = [list(a["farmer"])] + [list(h) for h in a["hands"]]
                step = int(obs["day"]) * 24 + int(obs["hour"])
                for j, op in enumerate(units):
                    if j >= len(pos) or (limit is not None and len(out) >= limit):
                        continue
                    x, y = int(pos[j][0]), int(pos[j][1])
                    if not (0 <= x < board and 0 <= y < board):
                        continue
                    inv = invs[j] if j < len(invs) else {}
                    if not A._noop(op, tiles[y][x], inv, seeds, x, y, board):
                        run_start.pop(j, None)
                        continue
                    if j in run_start:
                        continue          # already carded at this run's start
                    tg = reachable_targets(obs, seat, (x, y), K, board, prices)
                    if not tg:
                        run_start[j] = step
                        continue
                    out.append({
                        "seed": seed, "seat": seat, "step": step,
                        "day": int(obs["day"]), "hour": int(obs["hour"]),
                        "unit": j, "at": [x, y],
                        "carrying": dict(inv),
                        "depot_dist": min(_dist((x, y), p)
                                          for p in depot_tiles(board)),
                        "observation": copy.deepcopy(dict(obs)),
                        "configuration": dict(env.configuration),
                        "arlene_action": copy.deepcopy(a),
                        "targets": tg, "arlene": arl_id,
                    })
                    run_start[j] = step         # exactly one card per idle run
                acts[i] = a
            else:
                acts[i] = (theirs.act(obs) if opponent_spec == "arlene"
                           else route_cards.call(opp, obs, env.configuration))
        env.step(acts)
    return out, {"seed": seed, "seat": seat, "opponent": opp_id, "arlene": arl_id}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, nargs="+", required=True)
    ap.add_argument("--seats", type=int, nargs="+", default=[0])
    ap.add_argument("--opponent", default="arlene")
    ap.add_argument("--limit-per-seed", type=int, default=None)
    ap.add_argument("--out", default="results/run-cards.json")
    a = ap.parse_args()
    import route_cards          # noqa: F401  (CLI path only)
    allc, meta = [], []
    for seed in a.seeds:
        for seat in a.seats:
            got, m = harvest(seed, seat, a.opponent, limit=a.limit_per_seed)
            allc.extend(got)
            meta.append(dict(m, cards=len(got)))
            reach = sum(1 for c in got
                        if any(t["dist"] + 1 <= 6 for t in c["targets"]))
            print(f"seed {seed} seat {seat}: {len(got)} run cards, {reach} with a "
                  f"target reachable within 6 turns", flush=True)
    from collections import Counter
    ops = Counter(t["op"][0] for c in allc for t in c["targets"])
    print("target ops offered:", dict(ops))
    if allc:
        vals = sorted(max((t["value_now"] for t in c["targets"]), default=0)
                      for c in allc)
        print(f"best-target value per card: median {vals[len(vals) // 2]:.0f}, "
              f"max {vals[-1]:.0f}")
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    json.dump({"meta": meta, "cards": allc}, open(a.out, "w"), default=str)
    print(f"wrote {len(allc)} run cards to {a.out}")


if __name__ == "__main__":
    main()
