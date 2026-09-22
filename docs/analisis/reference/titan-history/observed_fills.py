# SPDX-License-Identifier: Apache-2.0
"""Own, observation-bound market quantity reconciliation (not cash receipts).

Uses only a caller's exact post-unit shed, final submitted queue, and next own
shed. Uncertain buys are enumerated as a bounded superset: no rival-private
state, hypothetical price, or cash-delta attribution is needed. Per-slot ranges
are correlated; their endpoints must not be combined into an invented path.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

PRODUCTS = frozenset(('WHEAT', 'CARROT', 'TOMATO', 'STRAWBERRY', 'MELON',
                      'EGG', 'MILK', 'WOOL', 'FERTILIZER'))
ANIMALS = frozenset(('GOOSE', 'COW', 'SHEEP'))
# The pinned interpreter's per-slot loop stops before iteration 100_000.
MAX_SLOT_UNITS = 99_999


def _unknown(reason: str, **details: Any) -> dict[str, Any]:
    return {'status': 'unknown', 'reason': reason, 'orders': [],
            'cash_receipts': None, **details}


def _count(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError('inventory counts must be nonnegative integers')
    return value


def _stock(value: Any) -> dict[str, int]:
    if not isinstance(value, Mapping) or any(not isinstance(k, str) for k in value):
        raise ValueError('shed must be a string-keyed mapping')
    return {k: _count(v) for k, v in value.items()}


def _config(configuration: Any) -> tuple[int, int, int]:
    cfg = configuration or {}
    if not isinstance(cfg, Mapping):
        raise ValueError('configuration must be a mapping')
    capacity = int(cfg.get('shedCapacity', 100))
    slots = max(1, int(cfg.get('maxMarketOrdersPerTurn', 10)))
    tpd = max(1, int(cfg.get('turnsPerDay', 24)))
    if capacity < 0:
        raise ValueError('negative shed capacity')
    return capacity, slots, tpd


def _parse(order: Any, slot: int, limit: int) -> dict[str, Any]:
    out = {'slot': slot, 'type': None, 'item': None, 'requested': None,
           'kind': 'ignored'}
    if not isinstance(order, list) or not order:
        return out
    op = order[0]
    if not isinstance(op, str):
        return out
    out['type'] = op
    if op in ('HIRE', 'BUY_LAND'):
        out['kind'] = 'not_inferred' if slot < limit else 'ignored'
        return out
    if op not in ('SELL', 'BUY_PRODUCT', 'BUY_ANIMAL', 'BUY_SEED') or len(order) < 3:
        return out
    try:
        n = int(order[2])  # Match the interpreter's order parser, including strings.
    except (ValueError, TypeError, OverflowError):
        return out
    item = order[1]
    if not isinstance(item, str) or n <= 0:
        return out
    out.update(item=item, requested=n)
    if slot >= limit:
        return out
    if op == 'SELL' and item in PRODUCTS:
        out['kind'] = 'sell'
    elif (op == 'BUY_PRODUCT' and item in ('WHEAT', 'FERTILIZER')
          or op == 'BUY_ANIMAL' and item in ANIMALS):
        out['kind'] = 'buy'
    elif op == 'BUY_SEED' and item in ('WHEAT', 'CARROT', 'TOMATO', 'STRAWBERRY', 'MELON'):
        out['kind'] = 'not_inferred'
    return out


def _merge(target: dict, key: tuple, bounds: tuple[tuple, tuple]) -> None:
    previous = target.get(key)
    if previous is None:
        target[key] = bounds
    else:
        target[key] = (tuple(min(a, b) for a, b in zip(previous[0], bounds[0])),
                       tuple(max(a, b) for a, b in zip(previous[1], bounds[1])))


def reconcile_shed_fills(post_unit_shed: Mapping[str, int], submitted_action: Any,
                         next_shed: Mapping[str, int], configuration: Any = None, *,
                         after_market_deposits: Any = (), max_states: int = 4096,
                         max_transitions: int = 100_000) -> dict[str, Any]:
    """Infer per-slot shed-changing fills conditional on exact supplied snapshots.

    ``after_market_deposits`` is the ordered sequence of whole carried-inventory
    mappings automatically deposited AFTER the market. Empty means known none;
    None means unknown. Each mapping's item order matters at the capacity limit.
    The stateful adapter derives whether this evidence is needed from the clock.

    Purchases may fill anywhere from zero to requested/capacity. This deliberately
    ignores cash and rival-dependent prices, retaining a superset of real paths.
    All sell fills follow the pinned market's exact ordered stock semantics.
    A singleton interval is therefore a quantity conclusion, NOT a price or
    payment receipt. Bound exhaustion or contradictory evidence returns unknown.
    """
    try:
        before, after = _stock(post_unit_shed), _stock(next_shed)
        capacity, limit, _ = _config(configuration)
        if not isinstance(max_states, int) or isinstance(max_states, bool) or max_states < 1:
            raise ValueError('invalid state budget')
        if (not isinstance(max_transitions, int) or isinstance(max_transitions, bool)
                or max_transitions < 1):
            raise ValueError('invalid transition budget')
        if after_market_deposits is None:
            return _unknown('after_market_deposits_unknown')
        if not isinstance(after_market_deposits, (list, tuple)):
            raise ValueError('deposits must be an ordered sequence')
        deposits = [_stock(x) for x in after_market_deposits]
        queue = submitted_action.get('market', []) if isinstance(submitted_action, dict) else []
        queue = queue if isinstance(queue, list) else []
        orders = [_parse(o, i, limit) for i, o in enumerate(queue)]
    except (ValueError, TypeError, OverflowError):
        return _unknown('invalid_input')

    keys = sorted(set(before) | set(after) | {k for d in deposits for k in d}
                  | {o['item'] for o in orders if o['kind'] in ('sell', 'buy')})
    index = {k: i for i, k in enumerate(keys)}
    zero = (0,) * len(orders)
    states = {tuple(before.get(k, 0) for k in keys): (zero, zero)}
    peak, transitions = 1, 0
    for order in orders:
        kind, slot = order['kind'], order['slot']
        if kind not in ('sell', 'buy'):
            continue
        target = {}
        item_index = index[order['item']]
        for stock, (lo, hi) in states.items():
            if kind == 'sell':
                quantities = (min(order['requested'], stock[item_index], MAX_SLOT_UNITS),)
            else:
                ceiling = min(order['requested'], max(0, capacity - sum(stock)), MAX_SLOT_UNITS)
                # No sampling/truncation: the complete relaxed range or unknown.
                if transitions + ceiling + 1 > max_transitions:
                    return _unknown('transition_budget_exceeded', peak_states=peak,
                                    transitions=transitions)
                quantities = range(ceiling + 1)
            for quantity in quantities:
                transitions += 1
                if transitions > max_transitions:
                    return _unknown('transition_budget_exceeded', peak_states=peak,
                                    transitions=transitions - 1)
                new_stock = list(stock)
                new_stock[item_index] += -quantity if kind == 'sell' else quantity
                new_lo, new_hi = list(lo), list(hi)
                new_lo[slot] = new_hi[slot] = quantity
                _merge(target, tuple(new_stock), (tuple(new_lo), tuple(new_hi)))
                if len(target) > max_states:
                    return _unknown('state_budget_exceeded', peak_states=len(target),
                                    transitions=transitions)
        states = target
        peak = max(peak, len(states))

    wanted = tuple(after.get(k, 0) for k in keys)
    matched = []
    for stock, bounds in states.items():
        final = list(stock)
        used = sum(final)
        for deposit in deposits:
            for item, quantity in deposit.items():
                admitted = min(quantity, max(0, capacity - used))
                final[index[item]] += admitted
                used += admitted
        if tuple(final) == wanted:
            matched.append(bounds)
    if not matched:
        return _unknown('observed_shed_not_explained', peak_states=peak,
                        transitions=transitions, compatible_states=0)
    lower = [min(b[0][i] for b in matched) for i in range(len(orders))]
    upper = [max(b[1][i] for b in matched) for i in range(len(orders))]
    rows = []
    for order in orders:
        slot, kind = order['slot'], order['kind']
        row = dict(order)
        if kind == 'not_inferred':
            row.update(fill_min=None, fill_max=None)
        else:
            row.update(fill_min=lower[slot], fill_max=upper[slot])
        rows.append(row)
    ambiguous = any(o['kind'] in ('sell', 'buy') and o['fill_min'] != o['fill_max'] for o in rows)
    return {'status': 'ambiguous' if ambiguous else 'reconciled',
            'reason': 'compatible_shed_paths', 'orders': rows,
            'cash_receipts': None, 'compatible_states': len(matched),
            'peak_states': peak, 'transitions': transitions,
            'method': 'bounded_own_shed_superset',
            'non_shed_orders_inferred': False}


def full_sale_verdict(result: Mapping[str, Any], slot: int, quantity: int) -> bool | None:
    """True = quantity proven sold at that slot; False = proven short; None unknown.

    Quantity must equal the final recorded order request. This is intentionally
    not an order-submission check, a cash proof, or a claim about another slot.
    """
    if (result.get('status') not in ('reconciled', 'ambiguous')
            or isinstance(slot, bool) or not isinstance(slot, int)
            or isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0):
        return None
    row = next((r for r in result.get('orders', []) if r.get('slot') == slot), None)
    if (not row or row.get('type') != 'SELL' or row.get('requested') != quantity
            or row.get('fill_min') is None):
        return None
    if row['fill_min'] >= quantity:
        return True
    if row['fill_max'] < quantity:
        return False
    return None


def _clock(observation: Mapping[str, Any], tpd: int) -> int:
    if 'step' in observation:
        step = _count(observation['step'])
        if 'day' in observation and 'hour' in observation:
            if step != _count(observation['day']) * tpd + _count(observation['hour']):
                raise ValueError('inconsistent observation clock')
        return step
    return _count(observation['day']) * tpd + _count(observation['hour'])


class ObservedFillLedger:
    """One actor/match, final-action recorder and next-observation reconciler.

    This does not call a controller or change an action. Record AFTER all action
    transforms. Pass exact post-unit stock/whole carried inventories from that
    same final unit stage, not current pre-unit stock or speculative arrivals.
    """

    def __init__(self, *, max_states: int = 4096, max_transitions: int = 100_000):
        self.max_states, self.max_transitions = max_states, max_transitions
        self.pending: dict[str, Any] | None = None
        self.last_result: dict[str, Any] = _unknown('not_observed')

    def record(self, observation: Mapping[str, Any], configuration: Any,
               submitted_action: Any, *, post_unit_shed: Any,
               post_unit_inventories: Any = None) -> dict[str, Any]:
        capacity, slots, tpd = _config(configuration)
        now = _clock(observation, tpd)
        player = _count(observation['player'])
        if player not in (0, 1):
            raise ValueError('expected player 0 or 1')
        shed = _stock(post_unit_shed)
        inventories = (None if post_unit_inventories is None
                       else [_stock(v) for v in post_unit_inventories])
        # JSON detachment also makes the submitted-action binding portable.
        action = json.loads(json.dumps(submitted_action, allow_nan=False))
        action_hash = hashlib.sha256(json.dumps(action, sort_keys=True,
            separators=(',', ':'), allow_nan=False).encode()).hexdigest()
        self.pending = {'step': now, 'player': player, 'tpd': tpd, 'shed': shed,
                        'inventories': inventories, 'action': action,
                        'configuration': {'shedCapacity': capacity,
                            'maxMarketOrdersPerTurn': slots, 'turnsPerDay': tpd},
                        'action_sha256': action_hash}
        return {'status': 'recorded', 'step': now, 'player': player,
                'action_sha256': action_hash}

    def observe(self, observation: Mapping[str, Any]) -> dict[str, Any]:
        p = self.pending
        if p is None:
            return _unknown('no_pending_action')
        binding = {k: p[k] for k in ('step', 'player', 'action_sha256')}
        try:
            now = _clock(observation, p['tpd'])
            player = _count(observation['player'])
            if player != p['player']:
                return _unknown('different_player', binding=binding)
            if now == p['step']:
                return {'status': 'pending', 'reason': 'same_observation_step',
                        'binding': binding, 'orders': [], 'cash_receipts': None}
            if now != p['step'] + 1:
                result = _unknown('nonadjacent_observation', binding=binding)
            else:
                deposits = p['inventories'] if now % p['tpd'] == 0 else ()
                result = reconcile_shed_fills(p['shed'], p['action'],
                    observation['private']['shed'], p['configuration'],
                    after_market_deposits=deposits, max_states=self.max_states,
                    max_transitions=self.max_transitions)
                result['binding'] = binding
        except (ValueError, TypeError, KeyError, OverflowError):
            result = _unknown('invalid_observation', binding=binding)
        self.pending = None
        self.last_result = deepcopy(result)
        return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', type=Path, help='JSON object with reconcile_shed_fills keyword arguments')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    try:
        data = json.loads(args.input.read_text(encoding='utf-8'))
        result = reconcile_shed_fills(**data)
    except (OSError, ValueError, TypeError):
        result = _unknown('invalid_cli_input')
    text = json.dumps(result, sort_keys=True, indent=2) + '\n'
    if args.output:
        args.output.write_text(text, encoding='utf-8')
    else:
        print(text, end='')
    return 0 if result['status'] in ('reconciled', 'ambiguous') else 2


if __name__ == '__main__':
    raise SystemExit(main())
