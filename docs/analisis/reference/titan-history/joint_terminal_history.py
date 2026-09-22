# SPDX-License-Identifier: MIT
"""Join T12 public sale histories into finite terminal-input hypotheses.

Historical exact totals are not current rival stock or observed market order.
This adapter adds no inference model: FlowHistory owns causal sample eligibility;
the caller explicitly supplies unobserved-stock and fixed-slot hypotheses.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from typing import Any, Mapping, Sequence

SCHEMA = "titan.joint-terminal-history.v1"
OPERATING = frozenset(("WHEAT", "FERTILIZER"))


def _int(value: Any, name: str, low: int, high: int) -> int:
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"{name} must be an integer in {low}..{high}")
    return value


def _names(value: Any, name: str) -> list[str]:
    if not isinstance(value, (list, tuple)) or not value or len(value) > 32:
        raise ValueError(f"{name} must contain 1..32 unique product names")
    if any(not isinstance(p, str) or not p for p in value) or len(set(value)) != len(value):
        raise ValueError(f"{name} must contain unique nonempty strings")
    return list(value)


def _entries(value: Any, name: str) -> list[Mapping[str, Any]]:
    if not isinstance(value, (list, tuple)) or not 1 <= len(value) <= 32:
        raise ValueError(f"{name} must contain 1..32 explicit hypotheses")
    ids = []
    for entry in value:
        if not isinstance(entry, Mapping) or not isinstance(entry.get("id"), str) or not entry["id"]:
            raise ValueError(f"{name} entries need a nonempty id")
        if not isinstance(entry.get("origin"), str) or not entry["origin"]:
            raise ValueError(f"{name} entries need an explicit origin")
        ids.append(entry["id"])
    if len(set(ids)) != len(ids):
        raise ValueError(f"{name} ids must be unique")
    return list(value)


def build_joint_terminal_scenarios(
    history: Any,
    products: Sequence[str],
    now: int,
    *,
    slot_templates: Sequence[Mapping[str, Any]] | None = None,
    unobserved_products: Sequence[str] = (),
    unobserved_lots: Sequence[Mapping[str, Any]] | None = None,
    capacity: int = 100,
    max_orders: int = 10,
    max_scenarios: int = 32,
) -> dict[str, Any]:
    """Produce POLY ``terminal_inputs`` rival scenarios or an explicit fallback.

    Pass the full mechanics.PRODUCTS universe and the existing per-match T12
    FlowHistory. Only complete prior same-phase, ONE-TURN windows are joined.
    Products must share the same lag; per-product medians are never combined.
    The history's minimum applies to JOINT support, not individual products.

    A slot template is {id, origin, slots: [product_or_None, ...]}. Each product
    occurs at most once: its entire hypothesized lot is sold in that fixed slot.
    Zero quantities leave an empty slot, never shift later simultaneous orders.
    These templates are finite caller hypotheses, not inferred/exhaustive order.

    WHEAT/FERTILIZER are always unobserved here because T12 cannot separate their
    purchases/sales. Additional unobserved_products may be declared explicitly.
    Each unobserved_lot is {id, origin, stock: {EVERY unobserved_product: quantity}}.
    Explicit zero is a quiet-stock hypothesis, not an inferred zero. No silent
    completion, probability, current rival action or future outcome is consumed.

    On not-ready results, preserve the already-selected complete action. Do not
    pass an empty scenario list to the downstream optimizer, or replace it with
    a successful-looking subset. No upstream actor or observation is modified.
    """
    universe = _names(products, "products")
    now = _int(now, "now", 0, 100000)
    capacity = _int(capacity, "capacity", 1, 1000)
    max_orders = _int(max_orders, "max_orders", 1, 32)
    max_scenarios = _int(max_scenarios, "max_scenarios", 1, 32)
    minimum = _int(history.minimum, "history.minimum", 1, 100000)
    period = _int(history.period, "history.period", 1, 100000)
    if not isinstance(unobserved_products, (list, tuple)) or any(
        p not in universe for p in unobserved_products
    ):
        raise ValueError("unobserved_products must be a subset of products")
    unknown = sorted((set(unobserved_products) | OPERATING) & set(universe))
    observed = [p for p in universe if p not in unknown]
    result: dict[str, Any] = {
        "schema": SCHEMA, "ready": False, "status": "insufficient_joint_history",
        "now": now, "minimum": minimum, "joint_support": 0,
        "observed_products": observed, "unobserved_products": unknown,
        "historical_lags": [], "scenarios": [], "scenario_probabilities": None,
        "interpretation": "Finite historical-quantity and explicit slot/stock hypotheses; not calibrated, exhaustive, or current rival truth.",
    }
    if not observed:
        result["status"] = "no_identified_products"
        return result

    # Consume upstream complete-window eligibility; never reconstruct a missing
    # sample from an absent stream entry until its complete window is present.
    windows: dict[str, dict[int, dict[str, Any]]] = {}
    for product in observed:
        prediction = history.window_prediction(product, now, now)
        windows[product] = {w["lag"]: w for w in prediction["windows"]}
    lags = sorted(set.intersection(*(set(windows[p]) for p in observed)))
    result.update(joint_support=len(lags), historical_lags=lags,
                  product_support={p: len(windows[p]) for p in observed})
    if len(lags) < minimum:
        return result
    if slot_templates is None:
        result["status"] = "slot_order_unidentified"
        return result
    templates = _entries(slot_templates, "slot_templates")
    for template in templates:
        slots = template.get("slots")
        if not isinstance(slots, (list, tuple)) or not 1 <= len(slots) <= max_orders:
            raise ValueError("slot template must fit the admitted market prefix")
        named = [p for p in slots if p is not None]
        if any(p not in universe for p in named) or len(set(named)) != len(named):
            raise ValueError("slot templates contain unique known products or None")
    if unknown and unobserved_lots is None:
        result["status"] = "unobserved_stock_unspecified"
        return result
    if unobserved_lots is None:
        completions = [{"id": "no-unobserved-products", "origin": "all declared products have identified prior totals", "stock": {}}]
    else:
        completions = _entries(unobserved_lots, "unobserved_lots")
    for completion in completions:
        stock = completion.get("stock")
        if not isinstance(stock, Mapping) or set(stock) != set(unknown):
            raise ValueError("each completion must explicitly cover exactly every unobserved product")
        for product, quantity in stock.items():
            _int(quantity, "unobserved stock " + product, 0, capacity)

    scenarios: dict[str, dict[str, Any]] = {}
    for lag in lags:
        vector = {}
        prior = now - lag * period
        for product in observed:
            window = windows[product][lag]
            if not (window["training_start"] == window["training_end"] == prior < now):
                result["status"] = "incompatible_history_window"
                return result
            stream = window["stream"]
            if any(t != now for t, _ in stream) or len(stream) > 1:
                result["status"] = "incompatible_history_window"
                return result
            quantity = stream[0][1] if stream else 0
            vector[product] = _int(quantity, "historical quantity " + product, 0, 1000)
        for completion in completions:
            lot = {**vector, **completion["stock"]}
            if sum(lot.values()) > capacity:
                result["status"] = "joint_capacity_exceeded"
                return result
            shed = {p: lot[p] for p in universe if lot[p]}
            for template in templates:
                slots = template["slots"]
                if not set(shed).issubset(set(slots)):
                    result["status"] = "unrepresented_positive_product"
                    return result
                queue = [["SELL", p, shed[p]] if p in shed else [] for p in slots]
                key = json.dumps([shed, queue], sort_keys=True, separators=(",", ":"))
                witness = {
                    "lag": lag, "training_start": prior, "training_end": prior,
                    "slot_template": template["id"], "slot_origin": template["origin"],
                    "unobserved_lot": completion["id"], "stock_origin": completion["origin"],
                }
                if key not in scenarios:
                    if len(scenarios) >= max_scenarios:
                        result["status"] = "scenario_limit"
                        result["unique_scenarios_at_least"] = len(scenarios) + 1
                        return result
                    scenarios[key] = {
                        "id": "joint-" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:24],
                        "shed": shed.copy(), "market": queue,
                        "origin": {"kind": SCHEMA, "now": now,
                                   "quantity_source": "same-lag exact PRIOR public sales, hypothesized to recur",
                                   "slot_order_identified": False,
                                   "witnesses": []},
                    }
                scenarios[key]["origin"]["witnesses"].append(witness)
    result.update(ready=True, status="ready", scenarios=deepcopy(list(scenarios.values())))
    return result
