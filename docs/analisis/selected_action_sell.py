# SPDX-License-Identifier: Apache-2.0
"""Selected-action SELL transform with caller-owned production projections.

No parent controller is imported, constructed, called, or advanced here.
Pending producer lots constrain capacity only; they never become sale stock.
The accepted standalone scheduler.py remains a separate frozen policy.
"""
from __future__ import annotations

import copy
import math
import mechanics as m
from selected_sell_core import absorption, optimize_lot

PRODUCTS = tuple(p for p in m.PRODUCTS if p not in ('WHEAT', 'FERTILIZER'))
PHASES = {'before_market': 0, 'after_market': 1}


def absolute_step(obs, config):
    value = obs.get('step')
    return int(value if value is not None else
               int(obs['day']) * int(config.get('turnsPerDay', 24)) + int(obs['hour']))


def _count(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or int(value) != value:
        raise ValueError('Expected an integer count')
    return int(value)


def _orders(action):
    value = action.get('market', [])
    if not isinstance(value, list):
        raise ValueError('Market orders must be a list')
    return value


def _sell(order, item=None):
    return bool(isinstance(order, list) and len(order) >= 3 and order[0] == 'SELL'
                and (item is None or order[1] == item))


def replace_sales(orders, item, quantity, available, max_orders, reserved=()):
    """Keep economic prefixes and all original positions; append only at end.

    A SELL preceding any non-SELL order remains byte-identical. This preserves
    its actual funding and space contribution to inherited purchases/hiring.
    A reserved SELL position is left to the caller's selected action.
    """
    out = copy.deepcopy(orders)
    last_economic = max((i for i, o in enumerate(orders)
                         if o and not _sell(o)), default=-1)
    if any(i in reserved and _sell(o, item) for i, o in enumerate(orders)):
        return None
    left, stock = int(quantity), int(available)
    for i, order in enumerate(orders):
        if not _sell(order, item):
            continue
        request = max(0, _count(order[2]))
        if i <= last_economic:
            filled = min(stock, request)
            if left < filled:
                return None
        else:
            filled = min(stock, request, left)
            out[i] = ['SELL', item, filled] if filled else []
        stock -= filled
        left -= filled
    if left:
        if left > stock or len(out) >= max_orders or len(out) in reserved:
            return None
        out.append(['SELL', item, left])
    return out


def normalize_selected(action, shed, reserved_stock):
    """Express engine-clamped fills with one shared post-unit stock budget."""
    result = copy.deepcopy(action)
    physical = dict(shed)
    available = {p: max(0, q - reserved_stock.get(p, 0)) for p, q in shed.items()}
    orders = _orders(result)
    last_economic = max((i for i, order in enumerate(orders) if order and not _sell(order)), default=-1)
    for i, order in enumerate(orders):
        if not _sell(order) or order[1] not in PRODUCTS:
            continue
        item, request = order[1], max(0, _count(order[2]))
        baseline_fill = min(request, physical.get(item, 0))
        fill = min(request, available.get(item, 0))
        if i <= last_economic and fill != baseline_fill:
            raise ValueError('Stock reservation conflicts with an inherited economic prefix')
        physical[item] = physical.get(item, 0) - baseline_fill
        available[item] = available.get(item, 0) - fill
        orders[i] = ['SELL', item, fill] if fill else []
    return result


class ProjectionLedger:
    """Conditional lossless stock flows supplied by the selected producer.

    stock_events are signed non-market shed changes, including observed carried
    goods' actual DROP/EOD deposits. capacity_events are separate committed,
    unrealized whole-lot obligations. Neither projection executes farm actions.
    """
    def __init__(self, obs, config, base, shed, projection, contract, reservations, horizon):
        self.obs, self.config, self.base = obs, config, base
        self.now = absolute_step(obs, config)
        self.last = int(config.get('episodeSteps', 720)) - 2
        self.end = _count(projection['end_step'])
        if _count(projection['observed_step']) != self.now:
            raise ValueError('Projection is for a different observation')
        if not self.now <= self.end <= min(self.last, self.now + horizon):
            raise ValueError('Projection must cover a bounded actionable horizon')
        self.shed = {p: _count(q) for p, q in shed.items()}
        if any(q < 0 for q in self.shed.values()):
            raise ValueError('Negative post-unit stock')
        self.capacity = int(config.get('shedCapacity', 100))
        self.max_orders = int(config.get('maxMarketOrdersPerTurn', 10))
        self.future = {int(t): copy.deepcopy(orders) for t, orders in projection['future_market'].items()}
        self.future[self.now] = copy.deepcopy(_orders(base))
        self.events = {}
        for event in projection['stock_events']:
            t, phase = _count(event['step']), event['phase']
            if phase not in PHASES or t < self.now:
                raise ValueError('Invalid stock-event phase or date')
            if t == self.now and phase == 'before_market':
                raise ValueError('Current unit arrivals already belong in post_unit_shed')
            delta = _count(event['quantity_delta'])
            self.events.setdefault((t, phase), []).append((event['product'], delta))
        self.pending = []
        seen = {}
        if contract is not None:
            if int(contract.get('observed_step', self.now)) != self.now:
                raise ValueError('Arrival contract is for a different observation')
            for event in contract.get('capacity_events', []):
                # The T08 contract already subtracts observed carried realization.
                key = (event['owner'], event['errand_id'])
                if key in seen:
                    if seen[key] != event:
                        raise ValueError('Conflicting committed errand rows')
                    continue
                seen[key] = event
                if event.get('guaranteed_stock_units', 0) != 0 or event.get('contingent') is not True:
                    raise ValueError('Expected a contingent committed capacity event')
                if event['phase'] not in PHASES or _count(event['step']) < self.now:
                    raise ValueError('Invalid committed event phase or date')
                units = _count(event['pending_capacity_units'])
                if units < 0 or units > _count(event['units_total']):
                    raise ValueError('Invalid whole-lot pending quantity')
                self.pending.append((_count(event['step']), event['phase'], units))
        reservations = reservations or {}
        self.stock_min = {p: _count(q) for p, q in reservations.get('stock', {}).items()}
        self.cash_min = {}
        for event in reservations.get('cash', []):
            key = (_count(event['step']), event['phase'])
            if key[1] not in PHASES:
                raise ValueError('Invalid cash-reservation phase')
            self.cash_min[key] = max(self.cash_min.get(key, 0), _count(event['minimum']))
        self.reserved_slots = {int(t): tuple(indices) for t, indices in reservations.get('market_slots', {}).items()}
        self.cost_bounds = {(_count(e['step']), _count(e['slot'])): _count(e['max_cash_cost'])
                            for e in reservations.get('order_cost_bounds', [])}
        for t, orders in self.future.items():
            if not self.now <= t <= self.end:
                continue
            for slot, order in enumerate(orders):
                if order and order[0] == 'BUY_PRODUCT' and (t, slot) not in self.cost_bounds:
                    raise ValueError('Caller cash bound required for BUY_PRODUCT, including paired rival buys')

    def pending_units(self, step, phase):
        point = (step, PHASES[phase])
        return sum(q for t, p, q in self.pending if (t, PHASES[p]) <= point)

    def reference(self, item, quantity):
        remaining, result = quantity, []
        for t in range(self.now, self.end + 1):
            request = sum(max(0, _count(o[2])) for o in self.future.get(t, []) if _sell(o, item))
            q = min(remaining, request)
            result.append((t, q)); remaining -= q
        return tuple((t, q) for t, q in result if q or t == self.now)

    def market(self, step, item, quantity, stock):
        return replace_sales(self.future.get(step, []), item, quantity, stock,
                             self.max_orders, self.reserved_slots.get(step, ()))

    def feasible(self, item, plan):
        stock = dict(self.shed)
        farm = self.obs['farms'][int(self.obs['player'])]
        cash = int(farm['money'])
        hires = int(farm.get('hires_today', 0))
        land = len(farm.get('unlocked_quadrants', ['NW'])) - 1
        inv = dict(self.obs['market']['inventory'])
        params = self.obs['market'].get('params')
        tpd = int(self.config.get('turnsPerDay', 24))
        orders = dict(plan)
        consumed = None
        def phase_ok(t, phase):
            for product, delta in self.events.get((t, phase), []):
                stock[product] = stock.get(product, 0) + delta
                # Worker transfers are ordered: a later withdrawal cannot
                # recover an earlier spill or fund an earlier pickup.
                if stock[product] < 0 or sum(stock.values()) > self.capacity:
                    return False
            if any(q < self.stock_min.get(p, 0) for p, q in stock.items()):
                return False
            if any(stock.get(p, 0) < q for p, q in self.stock_min.items()):
                return False
            if sum(stock.values()) + self.pending_units(t, phase) > self.capacity:
                return False
            return cash >= self.cash_min.get((t, phase), 0)
        for t in range(self.now, self.end + 1):
            if t > self.now and t % tpd == 0:
                hires = 0
            if not phase_ok(t, 'before_market'):
                return False
            market = (copy.deepcopy(self.future.get(t, [])) if item is None else
                      self.market(t, item, orders.get(t, 0), stock.get(item, 0)))
            if market is None:
                return False
            for slot, order in enumerate(market[:self.max_orders]):
                if not order:
                    continue
                op = order[0]
                if op == 'HIRE':
                    cost = m._hire_cost(hires, int(self.config.get('farmHandCostMult', 1)))
                    if cash < cost: return False
                    cash -= cost; hires += 1
                elif op == 'BUY_LAND':
                    if land < len(m.LAND_PRICES):
                        cost = m.LAND_PRICES[land]
                        if cash < cost: return False
                        cash -= cost; land += 1
                elif len(order) >= 3:
                    product, q = order[1], max(0, _count(order[2]))
                    if op == 'SELL':
                        sold = min(q, stock.get(product, 0))
                        stock[product] = stock.get(product, 0) - sold
                        # $1 per sold unit is a guaranteed operating-cash lower
                        # bound. Economic selection still uses exact scenario quotes.
                        cash += sold
                    elif op in ('BUY_PRODUCT', 'BUY_SEED', 'BUY_ANIMAL'):
                        if op == 'BUY_PRODUCT':
                            if product not in ('WHEAT', 'FERTILIZER'): return False
                            cost = 0
                            for _ in range(q):
                                inv[product] -= 1
                                cost += m.market_price(product, inv[product], params)
                            bound = self.cost_bounds[(t, slot)]
                            if bound < cost:
                                return False
                            cost = bound
                        else:
                            catalog = m.CROPS if op == 'BUY_SEED' else m.ANIMALS
                            field = 'seed' if op == 'BUY_SEED' else 'cost'
                            if product not in catalog: return False
                            cost = q * catalog[product][field]
                        if cash < cost: return False
                        cash -= cost
                        if op != 'BUY_SEED':
                            stock[product] = stock.get(product, 0) + q
                            if sum(stock.values()) + self.pending_units(t, 'before_market') > self.capacity:
                                return False
            # Actual final EOD has no sale window and imposes no salvage claim.
            if t == self.last:
                return (all(stock.get(p, 0) >= q for p, q in self.stock_min.items())
                        and cash >= self.cash_min.get((t, 'after_market'), 0))
            if not phase_ok(t, 'after_market'):
                return False
            if consumed is None:
                shops = self.obs.get('town', {}).get('unlocked_shops', [])
                # Preserve uncached evaluation for nonstandard caller inputs.
                if not isinstance(shops, (list, tuple)) or not all(isinstance(s, str) for s in shops):
                    for product in inv:
                        inv[product] -= absorption(product, t, shops, self.config)
                    continue
                context = (self.now, self.end, tuple(inv), tuple(shops),
                           self.config.get('townShopSellInterval', 4),
                           self.config.get('townCenterSellInterval', 24), absorption)
                if getattr(self, '_flow_context', None) != context:
                    self._flow_context = context
                    self._flow_consumed = {}
                consumed = self._flow_consumed
            if t not in consumed:
                # Populate only a reached date: early rejection and the final
                # market must not evaluate unused future consumption.
                consumed[t] = tuple((p, absorption(p, t, shops, self.config)) for p in inv)
            for product, quantity in consumed[t]:
                inv[product] -= quantity
        return True


class SelectedActionSell:
    """Replan SELL quantities over one already-selected production action.

    Missing/mismatched projection returns fallback_action, or selected_action
    when no fallback is supplied. Input objects are never mutated.
    """
    def __init__(self, horizon=8):
        self.horizon = max(1, min(8, int(horizon)))
        self.previous = None
        self.observed_harvests = {}
        self.diagnostics = {}

    def _rival(self, obs, item, now):
        rival = obs['farms'][1 - int(obs['player'])]
        visible = 0
        for row in rival['tiles']:
            for tile in row:
                if not isinstance(tile, dict): continue
                product = tile.get('crop') if tile.get('kind') == 'PLANT' else m.ANIMALS.get(tile.get('animal'), {}).get('product')
                if product == item: visible += max(0, int(tile.get('yield_units', 0)))
        recent = sum(q for t, q in self.observed_harvests.get(item, []) if now - t <= 8)
        return min(100, max(visible, recent))

    def _observe(self, obs, now):
        previous = self.previous
        if previous is not None and now > previous[0] and previous[1] == int(obs['player']):
            old = previous[2]; new = obs['farms'][1 - int(obs['player'])]['tiles']
            for y, row in enumerate(old):
                for x, tile in enumerate(row):
                    if not isinstance(tile, dict): continue
                    product = tile.get('crop') if tile.get('kind') == 'PLANT' else m.ANIMALS.get(tile.get('animal'), {}).get('product')
                    later = new[y][x]
                    a = max(0, int(tile.get('yield_units', 0)))
                    b = max(0, int(later.get('yield_units', 0))) if isinstance(later, dict) else 0
                    if product in PRODUCTS and a > b:
                        self.observed_harvests.setdefault(product, []).append((now, a - b))
        else:
            self.observed_harvests = {}
        for product in self.observed_harvests:
            self.observed_harvests[product] = [(t, q) for t, q in self.observed_harvests[product] if now - t <= 8]
        self.previous = (now, int(obs['player']), copy.deepcopy(obs['farms'][1 - int(obs['player'])]['tiles']))

    def transform(self, observation, configuration, selected_action, *, post_unit_shed=None,
                  projection=None, arrival_contract=None, reservations=None, fallback_action=None):
        fallback = selected_action if fallback_action is None else fallback_action
        self.diagnostics = {'status': 'fallback', 'evaluations': []}
        if post_unit_shed is None or projection is None:
            self.diagnostics['reason'] = 'missing_caller_projection'
            return copy.deepcopy(fallback)
        config = dict(configuration or {})
        try:
            ledger = ProjectionLedger(observation, config, selected_action, post_unit_shed,
                                      projection, arrival_contract, reservations, self.horizon)
            selected_action = normalize_selected(selected_action, ledger.shed, ledger.stock_min)
            original_market = _orders(ledger.base)
            for slot in ledger.reserved_slots.get(ledger.now, ()):
                if slot < len(original_market) and _orders(selected_action)[slot] != original_market[slot]:
                    raise ValueError('Normalization would change a caller-reserved order')
            ledger.future[ledger.now] = copy.deepcopy(_orders(selected_action))
            # No optimizable lot means the optimizer will not call feasibility.
            # Validate the literal inherited queue instead of accepting it unchecked.
            if not any(ledger.shed.get(p, 0) > ledger.stock_min.get(p, 0) for p in PRODUCTS):
                if not ledger.feasible(None, ()):
                    raise ValueError('no_certified_feasible_plan')
        except (ValueError, KeyError, TypeError, OverflowError, AttributeError) as error:
            self.diagnostics['reason'] = str(error)
            return copy.deepcopy(fallback)
        now, end = ledger.now, ledger.end
        self._observe(observation, now)
        self.diagnostics.update(status='unchanged', step=now, end_step=end)
        shops = observation.get('town', {}).get('unlocked_shops', [])
        dates = [now] + [t for t in range(now + 1, end + 1)
                         if any(absorption(p, t - 1, shops, config) for p in PRODUCTS)]
        if len(dates) > 3: dates = dates[:2] + dates[-1:]
        dates = sorted(set(dates + [end]))
        best = None
        for item in PRODUCTS:
            quantity = max(0, ledger.shed.get(item, 0) - ledger.stock_min.get(item, 0))
            if not quantity: continue
            reference = ledger.reference(item, quantity)
            # Reserve the actual inherited prefix before any economic operation.
            last_economic = max((i for i, o in enumerate(_orders(selected_action)) if o and not _sell(o)), default=-1)
            locked = min(quantity, sum(max(0, _count(o[2])) for o in _orders(selected_action)[:last_economic + 1] if _sell(o, item)))
            try:
                plan, info = optimize_lot(item=item, quantity=quantity,
                    inventory=int(observation['market']['inventory'][item]), params=observation['market'].get('params'),
                    shops=shops, config=config, now=now, dates=dates, reference=reference,
                    rival_quantity=self._rival(observation, item, now), minimum_now=locked,
                    capacity_ok=lambda plan, item=item: ledger.feasible(item, plan), last=ledger.last)
            except (ValueError, KeyError, TypeError, OverflowError) as error:
                self.diagnostics.update(status='fallback', reason=str(error))
                return copy.deepcopy(fallback)
            self.diagnostics['evaluations'].append(info)
            rank = (info.get('forced_feasibility', False), info['worst_relative_gain'])
            if info['feasible'] and (rank[0] or rank[1] > 0) and (best is None or rank > best[0]):
                best = (rank, item, plan, info)
        if best is None:
            if self.diagnostics['evaluations'] and not any(info['feasible'] for info in self.diagnostics['evaluations']):
                self.diagnostics.update(status='fallback', reason='no_certified_feasible_plan')
                if fallback_action is not None:
                    return copy.deepcopy(fallback_action)
            return copy.deepcopy(selected_action)
        _, item, plan, info = best
        market = ledger.market(now, item, dict(plan).get(now, 0), ledger.shed.get(item, 0))
        if market is None:
            return copy.deepcopy(fallback)
        result = copy.deepcopy(selected_action)
        result['market'] = market
        self.diagnostics.update(status='transformed', chosen=info)
        return result

    act = transform


def transform(observation, configuration, selected_action, **kwargs):
    """Stateless convenience call; retain SelectedActionSell for public history."""
    return SelectedActionSell().transform(observation, configuration, selected_action, **kwargs)
