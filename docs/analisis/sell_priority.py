"""Production-preserving, observation-only market-order stress transform.

Sort contiguous valid SELL blocks by their currently visible unit quotes. Every
parent order, quantity, non-SELL index and unit action is preserved. This is a
league opponent transform, not a claim that the resulting policy is stronger.
"""
from __future__ import annotations

import copy
import math
import time
from collections.abc import Mapping
from typing import Any

PRODUCTS = frozenset(("WHEAT", "CARROT", "TOMATO", "STRAWBERRY", "MELON",
                      "EGG", "MILK", "WOOL", "FERTILIZER"))


def _quote(order: Any, prices: Mapping) -> float | None:
    # Match the official positive-integer order grammar without changing bytes.
    # Malformed/unknown orders are barriers, not silently repaired or removed.
    if not isinstance(order, list) or len(order) < 3 or order[0] != "SELL":
        return None
    item = order[1]
    if not isinstance(item, str) or item not in PRODUCTS:
        return None
    try:
        quantity = int(order[2])
    except (TypeError, ValueError, OverflowError):
        return None
    if quantity <= 0:
        return None
    price = prices.get(item)
    if isinstance(price, bool) or not isinstance(price, (int, float)):
        return None
    if not math.isfinite(price) or price <= 0:
        return None
    return float(price)


def transform(action: dict, observation: Mapping,
              configuration: Mapping | None = None) -> dict:
    """Return an independent action, prioritizing quotes only within SELL blocks.

    Purchases/HIRE/BUY_LAND and invalid or unquoted orders split blocks. Orders
    beyond the engine's executable prefix are not moved into that prefix. Equal
    quotes retain the parent's ordering. No private state, rival action, history,
    environment seed, future shop schedule, or parent internals are read.
    """
    if not isinstance(action, dict) or not isinstance(action.get("market", []), list):
        raise ValueError("parent policy must return an object with a market list")
    result = copy.deepcopy(action)
    market = observation.get("market", {}) if isinstance(observation, Mapping) else {}
    prices = market.get("prices", {}) if isinstance(market, Mapping) else {}
    if not isinstance(prices, Mapping):
        return result
    cfg = configuration if isinstance(configuration, Mapping) else {}
    try:
        limit = max(1, int(cfg.get("maxMarketOrdersPerTurn", 10)))
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("invalid maxMarketOrdersPerTurn") from exc
    orders = result.get("market", [])
    end = min(len(orders), limit)
    start = 0
    while start < end:
        if _quote(orders[start], prices) is None:
            start += 1
            continue
        stop = start + 1
        while stop < end and _quote(orders[stop], prices) is not None:
            stop += 1
        # Python's stable sort preserves duplicate orders and equal-price ties.
        orders[start:stop] = sorted(orders[start:stop],
                                    key=lambda order: -_quote(order, prices))
        start = stop
    return result


def actor_class(base: type) -> type:
    """Compose with the existing process-isolated cloud-eval Actor.

    Append ``|sell-priority`` to an existing, fully pinned parent specification.
    Counters report actual changed turns and transform wall time separately from
    the parent's subprocess call/RPC timing; no parent-source patch is needed.
    """
    class SellPriorityActor(base):
        def __init__(self, spec: str, *args: Any, **kwargs: Any):
            suffix = "|sell-priority"
            self.sell_priority = spec.endswith(suffix)
            parent = spec[:-len(suffix)] if self.sell_priority else spec
            super().__init__(parent, *args, **kwargs)
            self.stats["sell_priority_enabled"] = self.sell_priority
            self.stats["sell_priority_changed_turns"] = 0
            self.stats["sell_priority_transform_seconds"] = []

        def act(self, observation: dict, configuration: Any, timeout: float):
            entered = time.perf_counter()
            response = super().act(observation, configuration, timeout)
            if not self.sell_priority or response.get("kind") != "action":
                return response
            started = time.perf_counter()
            result = transform(response["action"], observation, configuration)
            duration = time.perf_counter() - started
            self.stats["sell_priority_transform_seconds"].append(duration)
            if result != response["action"]:
                self.stats["sell_priority_changed_turns"] += 1
            total = time.perf_counter() - entered
            self.stats.setdefault("sell_priority_total_seconds", []).append(total)
            if total > timeout:
                return {"kind": "timeout", "phase": "sell_priority",
                        "error": "parent RPC plus transform exceeded the supplied action deadline"}
            return dict(response, action=result)

    return SellPriorityActor
