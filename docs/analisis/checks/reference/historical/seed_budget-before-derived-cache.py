# SPDX-License-Identifier: Apache-2.0
"""Bound purchases by all remaining own-route planting requests.

This component is for the intact frozen Arlene/SELL routes, not arbitrary route
rewriters. Counting every PLANT request (including future no-ops) and taking the
largest suffix count over prefix-compatible routes preserves a conservative stock budget.
It does not read the rival, replay actions, prices, hidden seeds, or future draws.
"""
from collections import Counter
from copy import deepcopy


class SeedBudget:
    def __init__(self, routes):
        self.suffixes = {}
        self.prefix_lengths = {}
        for name, route in routes.items():
            for other, candidate in routes.items():
                common = 0
                for a, b in zip(route, candidate):
                    if a != b:
                        break
                    common += 1
                self.prefix_lengths[name, other] = common
        for name, route in routes.items():
            suffix = [Counter() for _ in range(len(route) + 1)]
            for t in range(len(route) - 1, -1, -1):
                suffix[t] = suffix[t + 1].copy()
                row = route[t]
                for action in [row.get('farmer', []), *row.get('hands', [])]:
                    if len(action) >= 2 and action[0] == 'PLANT':
                        suffix[t][action[1]] += 1
            self.suffixes[name] = suffix
        self.events = []

    def remaining(self, crop, after_step, current):
        return max((s[min(max(0, after_step + 1), len(s) - 1)].get(crop, 0)
                    for name, s in self.suffixes.items()
                    if name == current or self.prefix_lengths[current, name] > after_step), default=0)

    def apply(self, action, post_unit_seeds, step, current, max_orders=10):
        result = deepcopy(action)
        stock = dict(post_unit_seeds)
        for slot, order in enumerate(result.get('market', [])[:max_orders]):
            if len(order) < 3 or order[0] != 'BUY_SEED':
                continue
            crop, requested = order[1], int(order[2])
            if requested <= 0:
                continue
            bound = self.remaining(crop, step, current)
            retained = min(requested, max(0, bound - int(stock.get(crop, 0))))
            if retained != requested:
                result['market'][slot] = ['BUY_SEED', crop, retained] if retained else []
                self.events.append(dict(step=step, slot=slot, crop=crop,
                                        requested=requested, retained=retained,
                                        post_unit_stock=int(stock.get(crop, 0)),
                                        remaining_request_bound=bound))
            # If this buy is cash-limited, later buys at the same fixed price
            # cannot execute either unless intervening cash arrives. Reserving
            # its requested quantity can therefore suppress a later useful buy.
            # Do not assume fulfillment; later slots use observed stock alone.
        return result
