# SPDX-License-Identifier: Apache-2.0
"""Sufficient current-market funding certificate for an existing seed proposal.

This does not derive seed demand, simulate a rival, or value later decisions.
The caller supplies a demand-valid seed reduction and authoritative post-unit
state. Unknown cases preserve the caller's original action.
"""
from __future__ import annotations

from copy import deepcopy
import math
from typing import Any, Mapping


def _whole(value: Any, name: str, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}")
    return value


def _cash(value: Any) -> int:
    """Preserve integral numeric cash from the official float-valued farm state.

    Convert only exact whole floats; never round fractional or nonfinite values.
    Seed quantities, indices and cost metadata keep their integer-only checks.
    """
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return _whole(value, "own cash")


def _quantity(order: Any) -> int:
    if not isinstance(order, list) or len(order) < 3:
        raise ValueError("quantity order must be a list with three fields")
    # Demand proposals emitted by SeedBudget use integers. Do not reinterpret
    # malformed, fractional, or extremely long orders as certified proposals.
    n = _whole(order[2], "quantity")
    if n > 99_998:
        raise ValueError("quantity reaches the interpreter's unit-loop boundary")
    return n


def _public_product_costs(mechanics, observation, orders, config, maximum):
    """Bound product costs without a rival action, private stock or cash input.

    At each slot one actor can buy at most shedCapacity goods: no worker action
    or own sale interleaves that BUY_PRODUCT order. Ignoring all sales gives a
    lower bound on the shared inventory. In particular inventory is NOT clamped
    at zero: the pinned market permits buys below zero.
    """
    capacity = _whole(config.get("shedCapacity", 100), "shed capacity", 1)
    market = observation["market"]
    params = market.get("params") or mechanics.MARKET_PARAMS
    own = {"WHEAT": 0, "FERTILIZER": 0}
    bounds = {}
    shapes = {"linear", "sq", "sqrt", "log", "log10", "hinge"}
    for slot, order in enumerate(orders[:maximum]):
        if not isinstance(order, list) or not order or order[0] != "BUY_PRODUCT":
            continue
        quantity = _quantity(order)
        if not quantity:
            continue
        item = order[1]
        if item not in own:
            raise ValueError("unsupported product purchase")
        inventory = market["inventory"][item]
        if isinstance(inventory, bool) or not isinstance(inventory, int):
            raise ValueError("market inventory must be an integer (may be negative)")
        spec = params[item]
        for key in ("base", "I0", "T", "below_target", "above_target"):
            value = spec[key]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError("market parameters must be finite numbers")
        if spec["base"] < 0 or spec["T"] <= 0 or min(spec["below_target"], spec["above_target"]) < 0:
            raise ValueError("market parameters do not establish a monotone price bound")
        for key in ("below_func", "above_func"):
            if spec[key] not in shapes:
                raise ValueError("unsupported market price shape")
            denominator = mechanics._shape(spec[key], spec["T"], spec["T"])
            if not math.isfinite(denominator) or denominator <= 0:
                raise ValueError("invalid market price normalization")
        quantity_bound = min(quantity, capacity)
        own[item] += quantity_bound
        # This intentionally overbounds the rival: it may buy capacity units
        # in every preceding/current slot, without proving any prior sale.
        # Other products compete for that capacity; treating them independently
        # can only make this sufficient bound more conservative.
        rival_bound = (slot + 1) * capacity
        lower = inventory - own[item] - rival_bound
        price = _whole(mechanics.market_price(item, lower, params), "product price bound")
        bounds[slot] = {
            "slot": slot, "product": item, "visible_inventory": inventory,
            "quantity_upper_bound": quantity_bound,
            "own_purchase_units_bound_through_slot": own[item],
            "rival_purchase_units_bound_through_slot": rival_bound,
            "quote_inventory_lower_bound": lower,
            "quoted_unit_price_upper_bound": price,
            "cost_upper_bound": quantity_bound * price,
        }
    return bounds


def certify_seed_funding(
    mechanics: Any,
    post_unit_observation: Mapping[str, Any],
    baseline_action: Mapping[str, Any],
    proposed_action: Mapping[str, Any],
    configuration: Mapping[str, Any] | None = None,
    *, public_product_bounds: bool = False,
) -> dict[str, Any]:
    """Certify unchanged non-seed execution for the SAME chosen rival queue.

    All original fixed-price requests must fit observed own cash WITHOUT SELL
    revenue. BUY_PRODUCT stays unresolved by default; public_product_bounds=True
    opts into the visible-inventory/ordered-shed-capacity price upper bound.
    Same-slot SELL and capacity-clipped animal purchases remain supported: seed
    purchases neither change shared inventory nor occupy shed capacity.

    The certificate does not prove that the removed seeds are unnecessary. Keep
    the existing route/demand proof. It covers this market phase, not later
    controller choices, terminal cash, or a policy promotion.
    """
    report: dict[str, Any] = {
        "status": "not_certified", "reason": "invalid_input",
        "scope": "current_market_only", "rival_private_used": False,
        "controller_calls": 0,
    }
    try:
        config = dict(configuration or {})
        maximum = max(1, _whole(config.get("maxMarketOrdersPerTurn", 10), "order limit"))
        mult = _whole(config.get("farmHandCostMult", 1), "hire multiplier")
        seat = _whole(post_unit_observation["player"], "player")
        farm = post_unit_observation["farms"][seat]
        money = _cash(farm["money"])
        hires = _whole(farm["hires_today"], "hires_today")
        unlocked = farm["unlocked_quadrants"]
        if not isinstance(unlocked, list) or not unlocked:
            raise ValueError("unlocked_quadrants must include the starting quadrant")
        land_index = len(unlocked) - 1
        if {k: v for k, v in baseline_action.items() if k != "market"} != {
            k: v for k, v in proposed_action.items() if k != "market"
        }:
            raise ValueError("non-market action fields changed")
        original = baseline_action.get("market", [])
        proposed = proposed_action.get("market", [])
        if not isinstance(original, list) or not isinstance(proposed, list):
            raise ValueError("market fields must be lists")
        if len(original) != len(proposed):
            raise ValueError("market slots were inserted or removed")
        edited, savings = [], 0
        for slot, (before, after) in enumerate(zip(original, proposed)):
            if before == after:
                continue
            if slot >= maximum:
                raise ValueError("proposal edits a truncated order")
            if (not isinstance(before, list) or len(before) < 3
                    or before[0] != "BUY_SEED" or before[1] not in mechanics.CROPS):
                raise ValueError("proposal changes a non-seed order")
            old = _quantity(before)
            if after == []:
                new = 0
            elif (isinstance(after, list) and len(after) >= 3
                  and after[:2] == before[:2]):
                new = _quantity(after)
            else:
                raise ValueError("proposal changes seed product or order kind")
            if not 0 <= new < old:
                raise ValueError("proposal is not a strict seed reduction")
            unit_cost = _whole(mechanics.CROPS[before[1]]["seed"], "seed price")
            savings += (old - new) * unit_cost
            edited.append(slot)
        if not edited:
            report.update(status="no_seed_edit", reason="identical_actions")
            return report
        product_bounds = {}
        if public_product_bounds and any(isinstance(o, list) and len(o) >= 3
                and o[0] == "BUY_PRODUCT" and o[2] != 0 for o in original[:maximum]):
            product_bounds = _public_product_costs(mechanics, post_unit_observation,
                                                  original, config, maximum)
        costs, total, product_total = [], 0, 0
        for slot, order in enumerate(original[:maximum]):
            if order == []:
                continue
            if not isinstance(order, list) or not order or not isinstance(order[0], str):
                raise ValueError("unsupported order shape")
            op, cost = order[0], 0
            if op == "HIRE":
                cost = _whole(mechanics._hire_cost(hires, mult), "hire cost")
                hires += 1
            elif op == "BUY_LAND":
                if land_index < len(mechanics.LAND_ORDER):
                    cost = _whole(mechanics.LAND_PRICES[land_index], "land price")
                    land_index += 1
            elif op in ("BUY_SEED", "BUY_ANIMAL", "SELL", "BUY_PRODUCT"):
                quantity = _quantity(order)
                if quantity == 0:
                    continue
                if op == "BUY_PRODUCT":
                    if not public_product_bounds:
                        raise ValueError("product purchase requires paired-flow cash evidence")
                    cost = product_bounds[slot]["cost_upper_bound"]
                    product_total += cost
                elif op == "BUY_SEED":
                    cost = quantity * _whole(mechanics.CROPS[order[1]]["seed"], "seed price")
                elif op == "BUY_ANIMAL":
                    # Requested cost bounds actual cost even when shed admission
                    # clips the purchase. Both arms have identical non-seed stock.
                    cost = quantity * _whole(mechanics.ANIMALS[order[1]]["cost"], "animal price")
                elif order[1] not in mechanics.PRODUCTS:
                    raise ValueError("unknown sale product")
            elif op == "PASS":
                continue
            else:
                raise ValueError("unresolved order cost")
            total += cost
            costs.append({"slot": slot, "operation": op, "cost_upper_bound": cost,
                          "cash_floor_without_sales": money - total})
        report.update(
            observed_cash=money, original_fixed_cost_upper_bound=total - product_total,
            seed_cash_reduction=savings, edited_slots=edited, prefixes=costs,
            minimum_cash_floor=money - total,
        )
        if product_bounds:
            report.update(original_total_cost_upper_bound=total,
                          product_cost_upper_bound=product_total,
                          public_product_bounds=list(product_bounds.values()))
        if total > money:
            report["reason"] = "original_queue_needs_additional_cash"
            return report
        report.update(status="certified", reason="original_fixed_queue_fully_funded",
                      non_seed_execution_preserved=True,
                      paired_current_market_cash_delta=savings)
        if product_bounds:
            report["reason"] = "original_queue_fully_funded_with_public_product_bounds"
    except (AttributeError, IndexError, KeyError, TypeError, ValueError, OverflowError) as error:
        report["reason"] = str(error)
    return report


def select_seed_queue(
    mechanics: Any,
    post_unit_observation: Mapping[str, Any],
    baseline_action: Mapping[str, Any],
    proposed_action: Mapping[str, Any],
    configuration: Mapping[str, Any] | None = None,
    *, fallback_action: Mapping[str, Any] | None = None,
    public_product_bounds: bool = False,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return the supplied proposal only when this certificate is complete.

    Call this inside the existing later-economic-order branch, after its current
    demand check. It does not override ordinary seed decisions outside that branch.
    """
    report = certify_seed_funding(mechanics, post_unit_observation, baseline_action,
                                  proposed_action, configuration,
                                  public_product_bounds=public_product_bounds)
    fallback = baseline_action if fallback_action is None else fallback_action
    chosen = proposed_action if report["status"] == "certified" else fallback
    return deepcopy(dict(chosen)), report


def select_public_seed_queue(
    mechanics: Any,
    post_unit_observation: Mapping[str, Any],
    baseline_action: Mapping[str, Any],
    proposed_action: Mapping[str, Any],
    configuration: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Opt-in callback for the existing integrated seed_queue_selector seam.

    Same demand proof and current-market-only limit as select_seed_queue. This
    adds bounded BUY_PRODUCT support; it does not change any default policy.
    """
    return select_seed_queue(mechanics, post_unit_observation, baseline_action,
                             proposed_action, configuration,
                             public_product_bounds=True)
