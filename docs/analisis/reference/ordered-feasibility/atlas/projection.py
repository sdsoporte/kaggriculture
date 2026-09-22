# SPDX-License-Identifier: Apache-2.0
"""Lossless ordered stock projections for an already-selected action sequence.

Inject the pinned official mechanics module (or the existing standalone
mechanics.py). No controller is called, no future draw or rival state is read,
and no price estimate is used as an operating-cash guarantee.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from typing import Any, Mapping


class ProjectionError(ValueError):
    """The supplied continuation cannot describe a lossless fixed-action plan."""


def _integer(value: Any, name: str, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ProjectionError(f"{name} must be an integer >= {minimum}")
    return value


def _market(action: Mapping[str, Any], maximum: int) -> list:
    orders = action.get("market", [])
    return deepcopy(orders[:maximum]) if isinstance(orders, list) else []


def _order(order: Any) -> tuple | None:
    # Same accepted whole-order shapes as the pinned interpreter's _parse_order.
    if not isinstance(order, list) or not order:
        return None
    op = order[0]
    if op in ("HIRE", "BUY_LAND"):
        return (op, None, 1)
    if op not in ("SELL", "BUY_PRODUCT", "BUY_SEED", "BUY_ANIMAL") or len(order) < 3:
        return None
    try:
        quantity = int(order[2])
    except (TypeError, ValueError, OverflowError):
        return None
    return (op, order[1], quantity) if quantity > 0 else None


def _full_market(mechanics: Any, farm: dict, private: dict, orders: list,
                 board: int, multiplier: int) -> None:
    """Conditional stock/worker effects, NOT a quote, fill or cash forecast.

    The SELL consumer separately certifies funding and capacity for the same
    orders. Simulating a clipped baseline purchase would silently omit an input
    from its continuation. Instead every valid acquisition is assumed filled.
    Future money is intentionally not exposed as a prediction.
    """
    for order in orders:
        parsed = _order(order)
        if parsed is None:
            continue
        op, item, quantity = parsed
        if op in ("HIRE", "BUY_LAND"):
            old_money = farm["money"]
            try:
                if op == "HIRE":
                    farm["money"] = mechanics._hire_cost(farm["hires_today"], multiplier)
                    mechanics._do_hire(farm, private, board, multiplier)
                else:
                    n = len(farm["unlocked_quadrants"]) - 1
                    if n < len(mechanics.LAND_PRICES):
                        farm["money"] = mechanics.LAND_PRICES[n]
                        mechanics._do_buy_land(farm, board)
            finally:
                farm["money"] = old_money
        elif op == "SELL" and item in mechanics.PRODUCTS:
            stock = private["shed"].get(item, 0)
            if stock > 0:
                private["shed"][item] -= min(quantity, stock)
        elif op == "BUY_SEED" and item in mechanics.CROPS:
            private["seeds"][item] = private["seeds"].get(item, 0) + quantity
        elif ((op == "BUY_PRODUCT" and item in ("WHEAT", "FERTILIZER"))
              or (op == "BUY_ANIMAL" and item in mechanics.ANIMALS)):
            private["shed"][item] = private["shed"].get(item, 0) + quantity


def _record(events: list, step: int, phase: str, worker: int, operation: str,
            item: str, delta: int) -> None:
    if delta:
        events.append(dict(step=step, phase=phase, product=item,
                           quantity_delta=delta, worker_index=worker,
                           operation=operation))


def _units(mechanics: Any, farm: dict, private: dict, action: Mapping[str, Any],
           step: int, board: int, day_length: int, capacity: int,
           *, lossless: bool, events: list, excluded: set, omissions: list) -> None:
    hands = action.get("hands", [])
    actions = [action.get("farmer", ["PASS"]), *(hands if isinstance(hands, list) else [])]
    demand = Counter(a[1] for a in actions
                     if isinstance(a, list) and len(a) >= 2 and a[0] == "PLANT")
    blocked = {crop for crop, count in demand.items()
               if count > private.get("seeds", {}).get(crop, 0)}
    for worker, original in enumerate(actions):
        if mechanics._farmer_position(farm, worker) is None:
            continue
        unit = original
        if isinstance(unit, list) and len(unit) >= 2 and unit[0] == "PLANT" and unit[1] in blocked:
            unit = ["PASS"]
        op = unit[0] if isinstance(unit, list) and unit else "PASS"
        before_shed = dict(private["shed"])
        before_inv = dict(mechanics._farmer_inventory(private, worker))
        # Enough room for every currently carried unit, without a fixed fake
        # shed limit. This shadow is admissible only when SELL certifies it.
        shadow_capacity = sum(before_shed.values()) + sum(before_inv.values()) + 1
        mechanics._apply_unit_action(farm, private, worker, unit, board,
                                     step // day_length, day_length,
                                     shadow_capacity if lossless else capacity)
        after_inv = mechanics._farmer_inventory(private, worker)
        if (step, worker) in excluded:
            if op != "HARVEST":
                raise ProjectionError("Contingent exclusion must identify an actual HARVEST action")
            removed = {}
            for item in set(after_inv) | set(before_inv):
                amount = after_inv.get(item, 0) - before_inv.get(item, 0)
                if amount > 0:
                    after_inv[item] -= amount
                    if not after_inv[item]:
                        del after_inv[item]
                    removed[item] = amount
            omissions.append(dict(step=step, worker_index=worker, quantities=removed))
        if lossless and op == "PICKUP" and len(unit) >= 2:
            # A stock-clamped PICKUP can change quantity when a SELL is changed.
            # Shorten before it rather than exporting that accidental baseline fill.
            pos = mechanics._farmer_position(farm, worker)
            requested = int(unit[2]) if len(unit) >= 3 else 1
            if requested > 0 and mechanics._is_shed_adjacent(pos, board):
                actual = after_inv.get(unit[1], 0) - before_inv.get(unit[1], 0)
                if actual != requested:
                    raise ProjectionError("stock-dependent PICKUP")
        if lossless:
            # DROP iterates the worker's inventory in insertion order. Preserve
            # that order; never collapse DROP then PICKUP to one net phase delta.
            keys = list(before_inv) if op == "DROP" else list(before_shed)
            keys += [p for p in private["shed"] if p not in keys]
            for item in keys:
                _record(events, step, "before_market", worker, op, item,
                        private["shed"].get(item, 0) - before_shed.get(item, 0))


def project_selected(mechanics: Any, observation: Mapping[str, Any],
                     configuration: Mapping[str, Any] | None,
                     selected_action: Mapping[str, Any], *,
                     future_actions: Mapping[int, Mapping[str, Any]],
                     end_step: int | None = None,
                     contingent_harvests: tuple[tuple[int, int], ...] = ()) -> dict:
    """Build generic SELL kwargs from current units and an explicit continuation.

    Current units execute once on a copy at real capacity. Future transfers are
    lossless whole requests, in worker/item order. The horizon stops at eight
    future decisions, the first EOD, the final decision, or the first missing or
    stock-dependent action. No missing action is interpreted as PASS.

    Every future acquisition assumes full funding/admission; SELL's independent
    funding/capacity certificate must accept it before use. No cash bounds are
    manufactured here. `contingent_harvests` explicitly names future HARVESTs
    reserved by the caller's separate capacity contract: clear the tile but do
    not credit their yield as guaranteed carried/saleable stock. Already-carried
    goods remain present. The caller owns matching those exclusions to its fresh
    commitment contract; this function neither selects nor invents errands.
    """
    if not isinstance(selected_action, Mapping) or not isinstance(future_actions, Mapping):
        raise ProjectionError("Selected action and future actions must be mappings")
    config = dict(configuration or {})
    tpd = _integer(config.get("turnsPerDay", 24), "turnsPerDay", 1)
    last = _integer(config.get("episodeSteps", 720), "episodeSteps", 2) - 2
    now = observation.get("step")
    if now is None:
        now = _integer(observation["day"], "day") * tpd + _integer(observation["hour"], "hour")
    now = _integer(now, "step")
    if now > last:
        raise ProjectionError("Observation is beyond the last action")
    requested_end = now + 8 if end_step is None else _integer(end_step, "end_step")
    if requested_end < now:
        raise ProjectionError("end_step precedes observation")
    boundary = ((now // tpd) + 1) * tpd - 1
    limit = min(requested_end, now + 8, last, boundary)
    capacity = _integer(config.get("shedCapacity", 100), "shedCapacity")
    board = _integer(config.get("boardSize", 10), "boardSize", 1)
    maximum = _integer(config.get("maxMarketOrdersPerTurn", 10), "maxMarketOrdersPerTurn", 1)
    multiplier = _integer(config.get("farmHandCostMult", 1), "farmHandCostMult")
    player = _integer(observation["player"], "player")
    # Reading the selected farm only is sufficient for this producer.
    farm = deepcopy(observation["farms"][player])
    private = deepcopy(observation["private"])
    if any(q < 0 for q in private["shed"].values()):
        raise ProjectionError("Negative observed shed stock")
    excluded = set()
    for step, worker in contingent_harvests:
        step, worker = _integer(step, "contingent step"), _integer(worker, "worker index")
        if not now < step <= limit:
            raise ProjectionError("Contingent harvest must be inside the future actionable horizon")
        if (step, worker) in excluded:
            raise ProjectionError("Duplicate contingent harvest")
        excluded.add((step, worker))
    events, omissions, future, phases = [], [], {}, []
    post = None
    reached, reason = now, "requested_horizon"
    for step in range(now, limit + 1):
        if step > now and step not in future_actions:
            reason = "missing_selected_action"
            break
        action = selected_action if step == now else future_actions[step]
        if not isinstance(action, Mapping):
            raise ProjectionError("Every supplied action must be a mapping")
        trial_farm, trial_private = deepcopy(farm), deepcopy(private)
        trial_events, trial_omissions = [], []
        try:
            _units(mechanics, trial_farm, trial_private, action, step, board, tpd,
                   capacity, lossless=step > now, events=trial_events,
                   excluded=excluded, omissions=trial_omissions)
        except ProjectionError as error:
            if str(error) != "stock-dependent PICKUP" or step == now:
                raise
            reason = "stock_dependent_pickup"
            break
        farm, private = trial_farm, trial_private
        events.extend(trial_events); omissions.extend(trial_omissions)
        reached = step
        if step == now:
            post = dict(player=player, step=now, day=now // tpd, hour=now % tpd,
                        farm=deepcopy(farm), private=deepcopy(private))
        else:
            future[step] = _market(action, maximum)
        phases.append(dict(step=step, phase="before_market", private=deepcopy(private)))
        _full_market(mechanics, farm, private, _market(action, maximum), board, multiplier)
        mechanics._decay_plants(farm, step)
        if (step + 1) % tpd == 0:
            mechanics._daily_refresh_plants(farm, step // tpd, tpd)
            mechanics._daily_refresh_animals(farm, step // tpd)
            for worker, inventory in enumerate(private["inventories"]):
                for item, quantity in inventory.items():
                    if quantity > 0:
                        _record(events, step, "after_market", worker, "EOD", item, quantity)
            unlimited = sum(private["shed"].values()) + sum(sum(x.values()) for x in private["inventories"]) + 1
            mechanics._drop_inventories_to_shed(private, unlimited)
            farm["farmer"] = list(mechanics._default_spawn(board))
            farm["hands"], farm["hires_today"] = [], 0
            private["inventories"] = [{}]
        phases.append(dict(step=step, phase="after_market", private=deepcopy(private)))
    if reason == "requested_horizon" and reached < requested_end:
        reason = ("final_decision" if reached == last else
                  "daily_boundary" if reached == boundary else "eight_step_bound")
    return dict(post_units=post, post_unit_shed=deepcopy(post["private"]["shed"]),
                projection=dict(observed_step=now, end_step=reached,
                                stock_events=events, future_market=future),
                diagnostics=dict(end_reason=reason, excluded_harvests=omissions,
                                 conditional_full_acquisitions=True,
                                 future_cash_forecast=None,
                                 unknown_future_draws_used=False,
                                 controller_calls=0),
                phases=phases)
