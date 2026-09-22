# SPDX-License-Identifier: Apache-2.0
# Exact official engine 28b6d8af definitions; see reference/engine/LICENSE.
import mechanics as _base
globals().update({k:v for k,v in vars(_base).items() if not k.startswith("__")})

def _refresh_prices(market):
    params = market.get("params")
    for item in PRODUCTS:
        market["prices"][item] = market_price(item, market["inventory"][item], params)

def _process_market(state, env):
    """Per-unit lockstep: at each step, quote both players' current-unit prices, then commit both."""
    obs0 = state[0].observation
    market = obs0.market
    farms = obs0.farms
    privates = [s.observation.private for s in state]
    board_size = int(get(env.configuration, "boardSize", 10))
    max_orders = max(1, int(get(env.configuration, "maxMarketOrdersPerTurn", 10)))
    hire_mult = int(get(env.configuration, "farmHandCostMult", FARM_HAND_COST_MULT))
    shed_capacity = int(get(env.configuration, "shedCapacity", 100))

    queues = []
    for s in state:
        action = s.action if isinstance(s.action, dict) else {}
        m = action.get("market", []) if isinstance(action, dict) else []
        q = list(m) if isinstance(m, list) else []
        queues.append(q[:max_orders])

    max_len = max((len(q) for q in queues), default=0)
    for i in range(max_len):
        order_states = []
        for player_id, q in enumerate(queues):
            ostate = None
            if i < len(q):
                ostate = _parse_order(q[i])
            order_states.append(ostate)

        # Atomic orders (HIRE, BUY_LAND): handle once, in player order.
        for player_id, ostate in enumerate(order_states):
            if ostate is None:
                continue
            op = ostate["type"]
            if op == "HIRE":
                _do_hire(farms[player_id], privates[player_id], board_size, hire_mult)
                order_states[player_id] = None
            elif op == "BUY_LAND":
                _do_buy_land(farms[player_id], board_size)
                order_states[player_id] = None

        # Per-unit lockstep loop for SELL / BUY_*.
        idx_esc = 0
        while True:
            idx_esc += 1
            if idx_esc >= 100_000:
                print("WARNING: kaggriculture market loop exceeded 100k iterations; aborting")
                break
            quoted = [None, None]
            for player_id, ostate in enumerate(order_states):
                if ostate is None or ostate["remaining"] <= 0:
                    continue
                op = ostate["type"]
                item = ostate["item"]
                if op == "SELL" and item in PRODUCTS:
                    quoted[player_id] = ("SELL", item, market_price(item, market["inventory"][item], market.get("params")), ostate)
                elif op == "BUY_PRODUCT" and item in ("WHEAT", "FERTILIZER"):
                    # Quote at post-buy inventory so a buy/sell round-trip
                    # against an unchanged market nets zero.
                    quoted[player_id] = ("BUY_PRODUCT", item, market_price(item, market["inventory"][item] - 1, market.get("params")), ostate)
                elif op == "BUY_SEED" and item in CROPS:
                    quoted[player_id] = ("BUY_SEED", item, CROPS[item]["seed"], ostate)
                elif op == "BUY_ANIMAL" and item in ANIMALS:
                    quoted[player_id] = ("BUY_ANIMAL", item, ANIMALS[item]["cost"], ostate)
                else:
                    order_states[player_id] = None  # malformed sub-op; abort

            if all(q is None for q in quoted):
                break

            # Both players see the same pre-commit inventory for this unit.
            committed_any = False
            for player_id, q in enumerate(quoted):
                if q is None:
                    continue
                op, item, price, ostate = q
                ok = _commit_unit(op, item, price, farms[player_id], privates[player_id], market, shed_capacity)
                if ok:
                    ostate["remaining"] -= 1
                    committed_any = True
                else:
                    order_states[player_id] = None  # can't continue this order

            if not committed_any:
                break

        _refresh_prices(market)

def _parse_order(order):
    if not isinstance(order, list) or not order:
        return None
    op = order[0]
    if op == "HIRE":
        return {"type": "HIRE"}
    if op == "BUY_LAND":
        return {"type": "BUY_LAND"}
    if op in ("BUY_SEED", "BUY_PRODUCT", "BUY_ANIMAL", "SELL"):
        if len(order) < 3:
            return None
        try:
            n = int(order[2])
        except (TypeError, ValueError):
            return None
        if n <= 0:
            return None
        return {"type": op, "item": order[1], "remaining": n}
    return None
