# SPDX-License-Identifier: Apache-2.0
"""Final-market receipt inputs for the existing PORT/LARCH score consumer.

One supplied selected action and its already computed own-unit snapshot enter.
No controller, unit projection, optimization, sampling, or hidden rival input is
performed here. The optional default scenarios are current-snapshot stress
hypotheses, not an inferred inventory, calibrated distribution, or exhaustive
history-compatible set. Caller-supplied scenario provenance is retained.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
import time
from types import SimpleNamespace as NS
from typing import Any, Mapping

SCHEMA = 'titan.terminal-inputs.v1'
CONFIG_KEYS = ('episodeSteps', 'boardSize', 'turnsPerDay', 'shedCapacity',
               'maxMarketOrdersPerTurn', 'farmHandCostMult')


def fingerprint(value: Any) -> str:
    """Order-preserving JSON identity, including stock/inventory insertion order."""
    return hashlib.sha256(json.dumps(value, separators=(',', ':'), ensure_ascii=True,
                                    allow_nan=False).encode('ascii')).hexdigest()


def _integer(value, name, lower, upper):
    if type(value) is not int or not lower <= value <= upper:
        raise ValueError(f'{name} must be an integer in {lower}..{upper}')
    return value


def _sale(order):
    return isinstance(order, list) and len(order) >= 3 and order[0] == 'SELL'


def _queue_ok(queue, limit):
    if not isinstance(queue, list):
        raise ValueError('A supplied market queue must be a list')
    # Only the engine-admitted prefix is executed. Preserve any inactive tail.
    for order in queue[:limit]:
        if not isinstance(order, list) or not order or order[0] not in {
                'SELL', 'BUY_SEED', 'BUY_PRODUCT', 'BUY_ANIMAL'} or len(order) < 3:
            continue
        try:
            count = int(order[2])
        except (TypeError, ValueError, OverflowError):
            # The native parser has no overflow handler. Do not pass nonfinite
            # orders into it merely to produce a table; preserve caller fallback.
            raise ValueError('Unsupported noninteger native order quantity') from None
        if abs(count) > 1000:
            raise ValueError('Order quantity exceeds this bounded consumer')


def _config(configuration):
    default = dict(episodeSteps=720, boardSize=10, turnsPerDay=24, shedCapacity=100,
                   maxMarketOrdersPerTurn=10, farmHandCostMult=1)
    cfg = {key: configuration.get(key, val) for key, val in default.items()}
    for key, low, high in [('episodeSteps', 2, 100000), ('boardSize', 2, 100),
                           ('turnsPerDay', 1, 10000), ('shedCapacity', 1, 1000),
                           ('maxMarketOrdersPerTurn', 1, 32), ('farmHandCostMult', 0, 10000)]:
        _integer(cfg[key], key, low, high)
    return cfg


def _current(observation, configuration, post_unit_observation):
    cfg = _config(configuration)
    player = _integer(observation.get('player'), 'player', 0, 1)
    step = _integer(observation.get('step'), 'step', 0, 100000)
    if step != cfg['episodeSteps'] - 2:
        raise ValueError('Complete terminal receipts require the final actionable step')
    post = post_unit_observation
    if not isinstance(post, Mapping) or post.get('step') != step or post.get('player') != player:
        raise ValueError('A same-step, same-player selected-unit snapshot is required')
    if len(observation['farms']) != 2 or len(post['farms']) != 2:
        raise ValueError('Expected the two-player public farm state')
    if post['market'] != observation['market']:
        raise ValueError('Selected-unit snapshot must precede the market')
    if post['farms'][player]['money'] != observation['farms'][player]['money']:
        raise ValueError('Native unit actions do not change money')
    farms = deepcopy(observation['farms'])
    farms[player] = deepcopy(post['farms'][player])
    private = deepcopy(post['private'])
    if not isinstance(private['shed'], dict) or not isinstance(private['seeds'], dict):
        raise ValueError('Complete own post-unit shed and seed mappings are required')
    if not isinstance(private['inventories'], list) or len(private['inventories']) != len(farms[player]['hands']) + 1:
        raise ValueError('Post-unit inventories must match the existing workers')
    if len(farms[player]['hands']) > 512 or any(len(farm['hands']) > 512 for farm in farms):
        raise ValueError('Worker count exceeds this bounded consumer')
    for farm in farms:
        if isinstance(farm['money'], bool) or not isinstance(farm['money'], (int, float)) or not math.isfinite(farm['money']):
            raise ValueError('Current cash must be finite')
    for name, count in private['shed'].items():
        _integer(count, 'post-unit stock '+str(name), 0, cfg['shedCapacity'])
    if sum(private['shed'].values()) > cfg['shedCapacity']:
        raise ValueError('Post-unit shed exceeds shared capacity')
    # No seed, record label, opponent action, future record, or outcome accepted.
    visible = {'player': player, 'step': step, 'farms': deepcopy(observation['farms']),
               'market': deepcopy(observation['market']), 'town': deepcopy(observation.get('town', {})),
               'private': deepcopy(observation['private']), 'configuration': cfg}
    bound = {'visible': visible, 'post_unit_farm': farms[player], 'post_unit_private': private}
    return cfg, player, step, farms, private, bound


def sale_plans(mechanics, selected_action, post_unit_private, market, configuration,
               *, max_plans=8):
    """Baseline plus bounded full-stock SELL permutations in replaceable slots.

    Inherited non-SELL positions and every worker field are untouched. Native
    execution, not an assumed nominal saving, determines downstream funding.
    The family is an enumeration, not an exhaustive optimality claim.
    """
    _integer(max_plans, 'max_plans', 1, 8)
    cfg = _config(configuration)
    if not isinstance(selected_action, dict):
        raise ValueError('Supply one complete selected action')
    queue = selected_action.get('market', [])
    limit = cfg['maxMarketOrdersPerTurn']
    _queue_ok(queue, limit)
    plans = [{'id': 'baseline', 'action': deepcopy(selected_action)}]
    available = [p for p in mechanics.PRODUCTS if post_unit_private['shed'].get(p, 0) > 0]
    slots = [i for i in range(limit) if i >= len(queue) or not queue[i] or _sale(queue[i])]
    if not available or not slots or max_plans == 1:
        return plans
    rank = sorted(available, key=lambda p: (-mechanics.market_price(
        p, market['inventory'][p], market.get('params')) * post_unit_private['shed'][p], p))
    orders = [rank, list(reversed(rank)), sorted(available)]
    orders += [[p]+[q for q in rank if q != p] for p in rank]
    seen = {fingerprint(selected_action)}
    for order in orders:
        action = deepcopy(selected_action)
        proposed = action.setdefault('market', [])
        for slot in slots:
            if slot < len(proposed):
                proposed[slot] = []
        for slot, product in zip(slots, order):
            while len(proposed) <= slot:
                proposed.append([])
            proposed[slot] = ['SELL', product, post_unit_private['shed'][product]]
        identity = fingerprint(action)
        if identity not in seen:
            seen.add(identity)
            plans.append({'id': 'sale-'+identity[:16], 'action': action})
        if len(plans) == max_plans:
            break
    return plans


def stress_scenarios(mechanics, observation, configuration):
    """Quiet plus homogeneous full-shed sales early/late and two mixed lots.

    Uses only public current market and configuration. Each is a legal shared-
    capacity post-unit hypothesis, not a reconstruction of hidden rival goods.
    There is no claim that every hypothesis survives earlier public history.
    """
    cfg = _config(configuration)
    cap, limit = cfg['shedCapacity'], cfg['maxMarketOrdersPerTurn']
    items = list(mechanics.PRODUCTS)
    result = [{'id': 'quiet-empty', 'shed': {}, 'market': [], 'origin': 'current-snapshot-stress'}]
    for item in items:
        for slot in sorted({0, limit - 1}):
            result.append({'id': f'all-{item}-slot-{slot}', 'shed': {item: cap},
                           'market': [[] for _ in range(slot)]+[['SELL', item, cap]],
                           'origin': 'current-snapshot-stress'})
    ranked = sorted(items, key=lambda p: (-mechanics.market_price(
        p, observation['market']['inventory'][p], observation['market'].get('params')), p))
    lot = {p: cap // len(items) + int(i < cap % len(items)) for i, p in enumerate(ranked)}
    for name, order in [('ranked', ranked), ('reversed', list(reversed(ranked)))]:
        result.append({'id': 'mixed-'+name, 'shed': lot.copy(),
                       'market': [['SELL', p, lot[p]] for p in order[:limit] if lot[p]],
                       'origin': 'current-snapshot-stress'})
    return result


def _scenarios(mechanics, scenarios, cfg):
    if not isinstance(scenarios, list) or not 1 <= len(scenarios) <= 32:
        raise ValueError('Supply 1..32 complete rival sale hypotheses')
    result = []
    labels = set()
    for value in scenarios:
        name = value.get('id')
        if not isinstance(name, str) or not name or name in labels:
            raise ValueError('Scenario IDs must be unique nonempty strings')
        labels.add(name)
        shed = value.get('shed')
        queue = value.get('market')
        if not isinstance(shed, dict) or any(p not in mechanics.PRODUCTS for p in shed):
            raise ValueError('Rival sale hypotheses contain only known products')
        for p, count in shed.items():
            _integer(count, 'rival stock '+p, 0, cfg['shedCapacity'])
        if sum(shed.values()) > cfg['shedCapacity']:
            raise ValueError('Rival hypothesis violates joint shed capacity')
        _queue_ok(queue, cfg['maxMarketOrdersPerTurn'])
        requested = {}
        for order in queue[:cfg['maxMarketOrdersPerTurn']]:
            if not order:
                continue
            if not _sale(order) or order[1] not in mechanics.PRODUCTS:
                raise ValueError('This scenario producer supports rival SELL slots only')
            q = _integer(order[2], 'rival sale quantity', 1, cfg['shedCapacity'])
            requested[order[1]] = requested.get(order[1], 0) + q
        if any(q > shed.get(p, 0) for p, q in requested.items()):
            raise ValueError('A rival whole sale queue needs its full stock')
        if len(queue) > cfg['maxMarketOrdersPerTurn']:
            raise ValueError('Rival hypothesis includes inactive slots')
        result.append({'id': name, 'shed': deepcopy(shed), 'market': deepcopy(queue),
                       'origin': deepcopy(value.get('origin', 'caller-supplied-hypothesis'))})
    return result


def market_cell(mechanics, farms, own_private, market, configuration, player,
                own_action, scenario):
    """Run the unmodified native market once on detached post-unit facts."""
    other = 1 - player
    current_farms, current_market = deepcopy(farms), deepcopy(market)
    privates = [None, None]
    privates[player] = deepcopy(own_private)
    privates[other] = {'shed': deepcopy(scenario['shed']), 'seeds': {},
                       'inventories': [{} for _ in range(1+len(farms[other]['hands']))]}
    actions = [None, None]
    actions[player] = deepcopy(own_action)
    actions[other] = {'farmer': ['PASS'], 'hands': [], 'market': deepcopy(scenario['market'])}
    state = [NS(observation=NS(farms=current_farms, market=current_market, private=privates[i]),
                action=actions[i]) for i in range(2)]
    mechanics._process_market(state, NS(configuration=NS(**configuration)))
    return {'own_cash': current_farms[player]['money'], 'rival_cash': current_farms[other]['money'],
            'own_private': privates[player], 'rival_private': privates[other],
            'farms': current_farms, 'market': current_market}


def build_terminal_inputs(mechanics, observation, configuration, selected_action, *,
                          post_unit_observation, scenarios=None, max_plans=8,
                          max_cells=256, deadline=None):
    """Build raw PORT receipts using current facts, preserving missing cells.

    Caller supplies the exact own-unit snapshot for this same selected action
    (for example OrderedSelectedSell.prepare(...)[post_unit_observation]). This
    function does not recompute units. It cannot independently establish the
    snapshot's action provenance. Rival scenarios may be caller supplied from
    causal history; otherwise the labeled finite stress family is used.

    deadline is an optional absolute time.perf_counter() value in this process.
    It is checked between native calls, not a preemptive wall-clock guarantee.
    Missing cells retain done=False/cash=None, so PORT/LARCH cannot optimize a
    completed-looking subset. max_cells bounds native market invocations.
    """
    _integer(max_cells, 'max_cells', 0, 256)
    if deadline is not None and (isinstance(deadline, bool) or not isinstance(deadline, (int, float)) or not math.isfinite(deadline)):
        raise ValueError('deadline must be a finite monotonic timestamp')
    cfg, player, step, farms, private, bound = _current(observation, configuration, post_unit_observation)
    plans = sale_plans(mechanics, selected_action, private, observation['market'], cfg, max_plans=max_plans)
    default = scenarios is None
    rivals = _scenarios(mechanics, stress_scenarios(mechanics, observation, cfg) if default else scenarios, cfg)
    public_hash = fingerprint(bound)
    receipt_list = []
    executed = 0
    stopped = None
    for plan in plans:
        for rival in rivals:
            receipt = {'plan': plan['id'], 'scenario': rival['id'], 'step': step,
                       'own_action': deepcopy(plan['action']), 'plan_sha256': fingerprint(plan['action']),
                       'scenario_sha256': fingerprint(rival), 'public_state_sha256': public_hash,
                       'own_cash': None, 'rival_cash': None, 'done': False}
            if stopped is None:
                if executed >= max_cells:
                    stopped = 'cell_limit'
                elif deadline is not None and time.perf_counter() >= deadline:
                    stopped = 'deadline'
            if stopped is None:
                out = market_cell(mechanics, farms, private, observation['market'], cfg,
                                  player, plan['action'], rival)
                executed += 1
                receipt.update(own_cash=out['own_cash'], rival_cash=out['rival_cash'], done=True,
                               own_shed_after=out['own_private']['shed'],
                               own_seeds_after=out['own_private']['seeds'],
                               own_hands_after=len(out['farms'][player]['hands']))
            receipt_list.append(receipt)
    source = {'schema': SCHEMA, 'step': step, 'player': player,
              'selected_action_sha256': fingerprint(selected_action),
              'observation_and_post_units_sha256': public_hash,
              'hypothesis_family': 'finite-current-snapshot-stress' if default else 'caller-supplied-sale-hypotheses',
              'scenario_probabilities': None,
              'limits': 'Conditional final-market cash only. No inferred hidden stock, calibrated win probability, exhaustive scenario coverage, or new game result.'}
    document = {'plan_ids': [p['id'] for p in plans], 'scenario_ids': [r['id'] for r in rivals],
                'baseline': 'baseline', 'receipts': receipt_list, 'source': source}
    return {'schema': SCHEMA, 'status': stopped or 'complete', 'document': document,
            'plans': plans, 'scenarios': rivals, 'native_market_calls': executed,
            'required_cells': len(plans)*len(rivals), 'complete': stopped is None,
            'fallback_action': deepcopy(selected_action), 'source': deepcopy(source)}
