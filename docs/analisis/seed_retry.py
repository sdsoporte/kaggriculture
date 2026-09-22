# SPDX-License-Identifier: Apache-2.0
"""Reconcile seeds for an unchanged next-turn planting commitment.

This is an appendix-only selected-action transform. It neither calls a producer
nor predicts a new route, market, or rival action. The runtime adapter supplies
an existing own-route cash reservation and the authoritative own-unit projection.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
import math
from types import MethodType
from typing import Any, Mapping


def _whole(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(label + ' must be a nonnegative integer')
    return value


def _money(value: Any, label: str) -> int:
    if isinstance(value, float) and math.isfinite(value) and value.is_integer():
        value = int(value)
    return _whole(value, label)


def propose_seed_retry(mechanics: Any, funding: Any, post_unit_observation: Mapping[str, Any],
                       selected: Mapping[str, Any], committed_next: Mapping[str, Any],
                       configuration: Mapping[str, Any] | None = None, *,
                       reserved_cash: int, next_step: int) -> tuple[dict, dict]:
    """Append a funded deficit purchase for physically reachable next-turn PLANTs.

    ``committed_next`` must be the existing, unchanged next-turn unit program,
    not a hypothetical new planting plan. ``reserved_cash`` includes the current
    selected queue and the caller's existing near-term own-route obligations.
    No SELL receipts fund admission. A current BUY_PRODUCT is deliberately left
    to its existing owner rather than supplying a new price model here.
    """
    out = deepcopy(dict(selected))
    report = {'status': 'unchanged', 'reason': 'no_deficit', 'controller_calls': 0,
              'scope': 'committed_next_turn_units_and_reserved_cash_window'}
    try:
        obs, cfg = post_unit_observation, dict(configuration or {})
        now = _whole(obs['step'], 'step')
        next_step = _whole(next_step, 'next_step')
        turns = _whole(cfg.get('turnsPerDay', 24), 'turnsPerDay')
        horizon = _whole(cfg.get('episodeSteps', 720), 'episodeSteps')
        maximum = _whole(cfg.get('maxMarketOrdersPerTurn', 10), 'order limit')
        if not turns or not maximum:
            raise ValueError('positive day length and order limit required')
        if next_step != now + 1 or next_step > horizon - 2:
            report['reason'] = 'not_an_existing_next_action_turn'
            return out, report
        if now // turns != next_step // turns:
            report['reason'] = 'day_boundary'
            return out, report
        seat = _whole(obs['player'], 'player')
        if seat not in (0, 1):
            raise ValueError('player must be zero or one')
        farm, private = obs['farms'][seat], obs['private']
        money = _money(farm['money'], 'observed cash')
        reserve = _money(reserved_cash, 'reserved cash')
        orders = selected['market']
        if not isinstance(orders, list):
            raise ValueError('market must be a list')
        if len(orders) >= maximum:
            report['reason'] = 'no_append_slot'
            return out, report
        hands = committed_next.get('hands', [])
        if not isinstance(hands, list):
            raise ValueError('hands must be a list')
        actions = [committed_next.get('farmer', ['PASS']), *hands]
        positions = [farm['farmer'], *farm['hands']]
        demand = Counter()
        requests = {}
        for index, action in enumerate(actions):
            if isinstance(action, list) and action and action[0] == 'PLANT':
                if len(action) != 2 or action[1] not in mechanics.CROPS:
                    raise ValueError('unsupported committed planting request')
                crop = action[1]
                demand[crop] += 1
                requests.setdefault(crop, []).append(index)
        if not demand:
            return out, report
        current_buys = {o[1] for o in orders if isinstance(o, list) and len(o) >= 3
                        and o[0] == 'BUY_SEED' and isinstance(o[1], str)}
        additions = []
        occupied = Counter(tuple(p) for p in positions)
        tiles = farm['tiles']
        for crop in sorted(demand):
            stock = _whole(private['seeds'].get(crop, 0), 'seed stock')
            deficit = demand[crop] - stock
            if deficit <= 0:
                continue
            # Do not assume a queued purchase failed or add a duplicate purchase
            # before its actual outcome can be observed.
            if crop in current_buys:
                report['reason'] = 'seed_purchase_already_selected'
                return out, report
            # Atomic per-crop demand includes all PLANT requests, even no-ops.
            # Fund only when every request of this crop can actually plant and
            # no co-located unit can displace the committed tile action.
            for index in requests[crop]:
                if index >= len(positions):
                    report['reason'] = 'missing_committed_worker'
                    return out, report
                x, y = positions[index]
                x, y = _whole(x, 'x'), _whole(y, 'y')
                if y >= len(tiles) or x >= len(tiles[y]):
                    raise ValueError('worker outside board')
                if occupied[(x, y)] != 1 or tiles[y][x] is not None:
                    report['reason'] = 'planting_target_not_exclusive_and_empty'
                    return out, report
            additions.append(['BUY_SEED', crop, deficit])
        if not additions:
            return out, report
        if len(orders) + len(additions) > maximum:
            report['reason'] = 'insufficient_append_slots'
            return out, report
        extra = sum(_whole(mechanics.CROPS[o[1]]['seed'], 'seed price') * o[2]
                    for o in additions)
        report.update(next_step=next_step, additions=additions, added_cost=extra,
                      observed_cash=money, reserved_cash=reserve)
        if reserve + extra > money:
            report['reason'] = 'preserve_reserved_obligations'
            return out, report
        candidate = deepcopy(out)
        candidate['market'].extend(deepcopy(additions))
        without_retry = deepcopy(candidate)
        without_retry['market'][len(orders):] = [[] for _ in additions]
        # Reuse the existing certificate in its documented direction: the
        # baseline is the FULL proposed queue, the comparison removes its new
        # seeds. Certification therefore proves the full queue fits cash and
        # its non-seed prefix executes identically. This is NOT a cash-gain claim.
        certificate = funding.certify_seed_funding(
            mechanics, obs, candidate, without_retry, cfg)
        if certificate['status'] != 'certified':
            report.update(reason='full_queue_not_certified',
                          funding_reason=certificate['reason'])
            return out, report
        report.update(status='appended', reason='funded_committed_deficit',
                      certified_queue_cost=certificate['original_fixed_cost_upper_bound'],
                      market_prefix_preserved=True, unit_actions_preserved=True)
        return candidate, report
    except (AttributeError, IndexError, KeyError, TypeError, ValueError, OverflowError) as error:
        report.update(reason='unsupported_input', detail=str(error))
        return out, report


def apply_committed_seed_retry(runtime: Any, obs: Mapping, cfg: Mapping,
                              selected: Mapping) -> tuple[dict, dict]:
    """Use an initialized current TitanAgent's existing route and funding modules.

    The eight-turn reserve is exactly the current seller's fixed-price window.
    Dynamic BUY_PRODUCT obligations, route-switch boundaries, and other unit
    rewriting features retain the selected action unchanged in this first scope.
    """
    unchanged = lambda reason: (selected, {'status': 'unchanged', 'reason': reason,
                                          'controller_calls': 0})
    f = runtime.features
    if f.consumer != 'frozen' or not f.seed or not f.funding:
        return unchanged('existing_seed_funding_lane_not_enabled')
    if any(getattr(f, key, False) for key in
           ('terminal_route', 'spatial_pathing', 'spatial_tempo', 'fourth_quadrant')):
        return unchanged('separate_unit_rewriter_active')
    from scheduler import HORIZON, m, parent, post_units
    now = int(obs['step'])
    turns = int(cfg.get('turnsPerDay', 24))
    if turns != 24:
        return unchanged('existing_reserve_uses_24_turn_days')
    route = runtime.controller.R[runtime.controller.cur]
    next_step = now + 1
    end = min(now + HORIZON, int(cfg.get('episodeSteps', 720)) - 2,
              (now // turns + 1) * turns - 1)
    for checkpoint, *_ in parent.DECISIONS:
        if now < checkpoint <= end:
            end = checkpoint - 1
    if next_step > end or next_step >= len(route):
        return unchanged('route_or_day_boundary')
    next_action = route[next_step]
    requests = [next_action.get('farmer', []), *next_action.get('hands', [])]
    if not any(a and a[0] == 'PLANT' for a in requests):
        return unchanged('no_next_turn_planting')
    # Existing seller reserve is an exact fixed-cost sum in this same-day scope.
    # Do not promote its heuristic product-price quote into a future guarantee.
    queues = [selected.get('market', [])] + [route[t].get('market', [])
              for t in range(next_step, min(end + 1, len(route)))]
    if any(o and o[0] == 'BUY_PRODUCT' for queue in queues for o in queue):
        return unchanged('dynamic_product_obligation_in_reserve_window')
    farm, private = post_units(obs, selected, cfg)
    post = dict(obs)
    post['farms'] = list(obs['farms'])
    post['farms'][int(obs['player'])] = farm
    post['private'] = private
    reserve = runtime.consumer.cash_reserve(obs, cfg, selected, end)
    result, report = propose_seed_retry(m, runtime.funding_module, post, selected,
                                       next_action, cfg, reserved_cash=reserve,
                                       next_step=next_step)
    report.update(route_id=runtime.controller.cur, reservation_end=end)
    return result, report


def install_seed_retry(runtime: Any) -> Any:
    """Compose after this instance's existing seed transform, without another act.

    Installation does not initialize or call the producer. The existing outer
    deadline/recovery mechanism still owns this transform. Repeated installation
    is idempotent. Canonical package owners can call apply_committed_seed_retry
    directly at the same seam instead of using this test/consumer adapter.
    """
    if getattr(runtime, '_committed_seed_retry_installed', False):
        return runtime
    previous = runtime._seed_selected

    def composed(self, obs, cfg, selected):
        baseline = previous(obs, cfg, selected)
        result, report = apply_committed_seed_retry(self, obs, cfg, baseline)
        self.diagnostics['committed_seed_retry'] = report
        return result

    runtime._seed_selected = MethodType(composed, runtime)
    runtime._committed_seed_retry_installed = True
    return runtime
