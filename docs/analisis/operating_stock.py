# SPDX-License-Identifier: Apache-2.0
"""Reserve useful fertilizer already owned by the active producer.

This is a bounded market proposal, not another production controller. It keeps
the emitted unit stream and every market slot. All obligations are rebuilt from
the current observation; no requested purchase is treated as a receipt.
"""
from collections import Counter
from copy import deepcopy

MOVES = {'NORTH': (0, -1), 'SOUTH': (0, 1), 'EAST': (1, 0), 'WEST': (-1, 0)}


def _units(row):
    return [row.get('farmer') or ['PASS'], *(row.get('hands') or [])]


def _action(row, actor):
    units = _units(row)
    return units[actor] if actor < len(units) and units[actor] else ['PASS']


def _next_bonus_day(tile, day, crops, last_refresh):
    if not isinstance(tile, dict) or tile.get('kind') != 'PLANT':
        return None
    crop = crops.get(tile.get('crop'))
    # Annual crops need a separate water/harvest certificate. This first consumer
    # covers an existing ongoing crop's production within fertilizer's lifetime.
    if not crop or not crop['ongoing']:
        return None
    first = int(tile['planted_day']) + int(crop['first_yield_day']) - 1
    interval = int(crop['interval'])
    cycle = max(0, (day - first + interval - 1) // interval)
    due = first + cycle * interval
    if (due > min(day + 2, last_refresh) or cycle >= int(crop['max_yield'])
            or int(tile.get('yield_units', 0)) > int(crop['max_yield']) - 2
            or int(tile.get('fertilized_until_day', -1)) >= due):
        return None
    expiry = int(tile.get('max_lifespan_step', -1))
    if expiry >= 0 and expiry <= (due + 1) * 24:
        return None
    return due


def _bonus_water_service(mechanics, observation, configuration, selected,
                         post_farm, route, obligations, checkpoints):
    """Check free movement/watering and paid worker availability through refresh.

    This is a position and cash lower-bound calculation, not game execution.
    Sales give no cash credit. A variable-price purchase reduces the cash lower
    bound to zero; it can never finance a later hire. Already hired workers can
    still move and water for free. Resets discard workers and recompute spawns.
    """
    now = int(observation['step']); cfg = configuration or {}
    finish = (max(o['bonus_day'] for o in obligations) + 1) * 24 - 1
    if finish >= len(route) or any(now < int(t) <= finish for t in checkpoints):
        return None, 'bonus_day_crosses_unresolved_route_boundary'
    positions = [tuple(post_farm['farmer']), *map(tuple, post_farm['hands'])]
    sites = {tuple(o['position']): o for o in obligations}
    watered = {p: bool(post_farm['tiles'][p[1]][p[0]].get('watered_today')) for p in sites}
    dry = {p: int(post_farm['tiles'][p[1]][p[0]].get('consecutive_unwatered', 0)) for p in sites}
    own_services = {(o['step'], tuple(o['position'])) for o in obligations}
    cash = max(0.0, float(post_farm['money']))
    hires = int(post_farm.get('hires_today', len(post_farm['hands'])))
    hire_cost = 0; fixed_spending = 0; water_receipts = []
    for step in range(now, finish + 1):
        row = selected if step == now else route[step]
        if step > now:
            for actor, pos in enumerate(positions):
                action = _action(row, actor); op = action[0]
                if pos in sites and step // 24 <= sites[pos]['bonus_day']:
                    if op in ('DIG', 'PLANT'):
                        return None, 'bonus_target_changes_before_refresh'
                    if op == 'FERTILIZE' and (step, pos) not in own_services:
                        return None, 'bonus_target_has_later_fertilizer'
                    if op == 'WATER':
                        watered[pos] = True
                        water_receipts.append({'step': step, 'actor': actor, 'position': pos})
                if op in MOVES:
                    dx, dy = MOVES[op]; q = (pos[0] + dx, pos[1] + dy)
                    if 0 <= q[0] < 10 and 0 <= q[1] < 10:
                        positions[actor] = q
        for order in (row.get('market') or [])[:int(cfg.get('maxMarketOrdersPerTurn', 10))]:
            if not order:
                continue
            op = order[0]
            if op == 'HIRE':
                cost = mechanics._hire_cost(hires, float(cfg.get('farmHandCostMult', 1)))
                if cash < cost:
                    return None, 'bonus_day_hire_not_funded'
                cash -= cost; hires += 1; hire_cost += cost
                shape = {'farmer': positions[0], 'hands': positions[1:]}
                positions.append(tuple(mechanics._spawn_hand(shape, 10)))
            elif op == 'BUY_PRODUCT':
                cash = 0.0
            elif op == 'BUY_LAND':
                return None, 'bonus_day_has_capital_expansion'
            elif op in ('BUY_SEED', 'BUY_ANIMAL') and len(order) > 2:
                table = mechanics.CROPS if op == 'BUY_SEED' else mechanics.ANIMALS
                price = table[order[1]]['seed' if op == 'BUY_SEED' else 'cost']
                cost = max(0, int(order[2])) * price
                cash = max(0.0, cash - cost); fixed_spending += cost
        if step % 24 == 23:
            day = step // 24
            for pos, obligation in sites.items():
                if day > obligation['bonus_day']:
                    continue
                dry[pos] = 0 if watered[pos] else dry[pos] + 1
                if dry[pos] >= 2:
                    return None, 'bonus_target_dies_before_refresh'
                if day == obligation['bonus_day'] and not watered[pos]:
                    return None, 'missing_bonus_day_water'
                watered[pos] = False
            positions = [tuple(mechanics._default_spawn(10))]; hires = 0
    return {'through_step': finish, 'water_actions': water_receipts,
            'funded_hire_cost': hire_cost, 'fixed_spending_without_sale_credit': fixed_spending,
            'variable_purchase_cash_credit': 0}, None


def protect_operating_stock(mechanics, observation, configuration, selected,
                            post_farm, post_private, route, checkpoints=()):
    """Return a same-slot fertilizer-sale proposal and its explicit limits.

    The supported case contains one actual worker, one upcoming shed pickup, existing
    productive targets, no intervening hiring/branch/reset or input replenishment,
    and room for known deposits without crediting future sales. The price screen
    is a named stress scenario, not a measured or guaranteed future cash gain.
    """
    report = {'changed': False, 'reason': 'no_fertilizer_sale'}
    orders = selected.get('market') or []
    offered = sum(max(0, int(o[2])) for o in orders
                  if o and len(o) > 2 and o[:2] == ['SELL', 'FERTILIZER'])
    if not offered:
        return selected, report
    now = int(observation['step']); day = now // 24
    cfg = configuration or {}
    board = len(post_farm['tiles'])
    if (board != 10 or int(cfg.get('turnsPerDay', 24)) != 24
            or int(cfg.get('episodeSteps', 720)) != 720 or day >= 29):
        report['reason'] = 'outside_supported_production_window'
        return selected, report
    day_end = min((day + 1) * 24, len(route))
    if any(now < int(t) < day_end for t in checkpoints):
        report['reason'] = 'unresolved_same_day_branch'
        return selected, report
    end = min((day + 1) * 24 - 1, 719,
              *[int(t) for t in checkpoints if now < int(t)])
    positions = [tuple(post_farm['farmer']), *map(tuple, post_farm['hands'])]
    if any(o and o[0] == 'HIRE' for o in orders):
        report['reason'] = 'current_hiring_boundary'
        return selected, report
    for step in range(now + 1, min(end, len(route))):
        if any(o and o[0] == 'HIRE' for o in route[step].get('market', [])):
            end = step
            break
    schedule = []
    pickups = []
    deposits = sum(max(0, int(o[2])) for o in orders
                   if o and len(o) > 2 and o[0] in ('BUY_PRODUCT', 'BUY_ANIMAL'))
    for step in range(now + 1, min(end, len(route))):
        row = route[step]
        for order in row.get('market', []):
            if order and order[0] in ('BUY_PRODUCT', 'BUY_ANIMAL'):
                if len(order) > 2:
                    deposits += max(0, int(order[2]))
                if len(order) > 1 and order[1] == 'FERTILIZER':
                    report['reason'] = 'intervening_requested_replenishment'
                    return selected, report
        for actor, pos in enumerate(positions):
            action = _action(row, actor)
            schedule.append((step, actor, pos, action))
            if action[0] == 'DROP':
                report['reason'] = 'unbounded_intervening_drop'
                return selected, report
            if action[0] == 'PLACE' and len(action) > 1 and action[1] not in mechanics.ANIMALS:
                deposits += max(0, int(action[2]) if len(action) > 2 else 1)
            if action[:2] == ['PICKUP', 'FERTILIZER']:
                pickups.append((step, actor, pos, max(0, int(action[2]) if len(action) > 2 else 1)))
            if action[0] in MOVES:
                dx, dy = MOVES[action[0]]
                new = (pos[0] + dx, pos[1] + dy)
                if 0 <= new[0] < board and 0 <= new[1] < board:
                    positions[actor] = new
    if len(pickups) != 1:
        report['reason'] = 'requires_one_unambiguous_pickup'
        return selected, report
    pickup_step, actor, pickup_pos, quantity = pickups[0]
    if pickup_pos not in ((4, 4), (5, 4), (4, 5), (5, 5)) or quantity <= 0:
        report['reason'] = 'pickup_not_reachable_from_shed'
        return selected, report
    carried = max(0, int(post_private['inventories'][actor].get('FERTILIZER', 0)))
    obligations = []
    for step, worker, pos, action in schedule:
        if worker != actor:
            continue
        if action[0] == 'COLLECT_FERTILIZER' or action[:2] == ['PLACE', 'FERTILIZER']:
            report['reason'] = 'actor_has_other_input_transfer'
            return selected, report
        if action[0] != 'FERTILIZE':
            continue
        tile = post_farm['tiles'][pos[1]][pos[0]]
        valid = isinstance(tile, dict) and tile.get('kind') == 'PLANT'
        if step < pickup_step:
            if valid:
                carried = max(0, carried - 1)
            continue
        due = _next_bonus_day(tile, day, mechanics.CROPS, 28)
        if due is None:
            report['reason'] = 'pickup_suffix_contains_nonproductive_consumption'
            return selected, report
        obligations.append({'step': step, 'position': pos, 'crop': tile['crop'], 'bonus_day': due})
    sites = {tuple(o['position']) for o in obligations}
    if not obligations or len(sites) != len(obligations):
        report['reason'] = 'no_distinct_useful_consumption'
        return selected, report
    for step, worker, pos, action in schedule:
        if pos not in sites:
            continue
        if action[0] in ('DIG', 'PLANT') or (action[0] == 'FERTILIZE' and worker != actor):
            report['reason'] = 'target_has_competing_asset_or_input_action'
            return selected, report
    for pos in sites:
        tile = post_farm['tiles'][pos[1]][pos[0]]
        watered = tile.get('watered_today') or any(
            place == pos and action[0] == 'WATER' for _, _, place, action in schedule)
        if int(tile.get('consecutive_unwatered', 0)) >= 1 and not watered:
            report['reason'] = 'target_lacks_water_before_reset'
            return selected, report
    if any(step < pickup_step and action[:2] == ['PLACE', 'FERTILIZER']
           for step, _, _, action in schedule):
        report['reason'] = 'intervening_requested_input_deposit'
        return selected, report
    required = max(0, len(obligations) - carried)
    stock = max(0, int(post_private['shed'].get('FERTILIZER', 0)))
    if not required or required > min(quantity, stock):
        report['reason'] = 'existing_input_covers_use_or_deficit_exceeds_stock'
        return selected, report
    limit = max(0, stock - required)
    withheld = max(0, min(stock, offered) - limit)
    if not withheld:
        report['reason'] = 'sale_already_leaves_required_stock'
        return selected, report
    if withheld > 2:
        report['reason'] = 'reservation_exceeds_bounded_units'
        return selected, report
    # Includes all current stock and requested arrivals; no future purchase is
    # credited as fertilizer and no future sale is credited as capacity relief.
    capacity = int(cfg.get('shedCapacity', 100))
    if sum(max(0, int(n)) for n in post_private['shed'].values()) + deposits >= capacity:
        report['reason'] = 'retained_stock_conflicts_with_arrival_room'
        return selected, report
    market = observation['market']; params = market.get('params')
    input_value = sum(mechanics.market_price('FERTILIZER', market['inventory']['FERTILIZER'] + j, params)
                      for j in range(withheld))
    own_yield = Counter()
    for row in post_farm['tiles']:
        for tile in row:
            if isinstance(tile, dict) and tile.get('kind') == 'PLANT':
                own_yield[tile['crop']] += max(0, int(tile.get('yield_units', 0)))
    # Existing carried and unsold stock already funds the earlier services.
    # Only the final otherwise-unfunded uses are incremental to this sale edit.
    extra = Counter(o['crop'] for o in obligations[-withheld:])
    product_value = sum(mechanics.market_price(crop, market['inventory'][crop] + own_yield[crop] + 100 + j, params)
                        for crop, amount in extra.items() for j in range(amount))
    # The 100-unit rival buffer is a stress scenario, not a bound on multi-day
    # supply. Existing service/harvest actions add no new hiring or route actions.
    if product_value <= 2 * input_value:
        report['reason'] = 'marginal_product_screen_not_favorable'
        return selected, report
    # Use actual saved cash. Reserve current requested spending and a liquidity
    # cushion; neither withheld nor future sale proceeds fund this proposal.
    spending = 0
    # Funding extends through today's reset, even when the useful input segment
    # rejoins before a later capital order. Never hide that order by truncating
    # the service window at its hiring boundary.
    for row in [selected, *route[now + 1:day_end]]:
        for order in row.get('market', []):
            if not order:
                continue
            op = order[0]
            if op in ('HIRE', 'BUY_LAND'):
                report['reason'] = 'intervening_capital_commitment'
                return selected, report
            if len(order) > 2 and op in ('BUY_SEED', 'BUY_ANIMAL'):
                table = mechanics.CROPS if op == 'BUY_SEED' else mechanics.ANIMALS
                spending += max(0, int(order[2])) * table[order[1]]['seed' if op == 'BUY_SEED' else 'cost']
            elif op == 'BUY_PRODUCT':
                report['reason'] = 'intervening_variable_price_purchase'
                return selected, report
    if float(post_farm['money']) < spending + 100 * input_value:
        report['reason'] = 'actual_cash_cushion_insufficient'
        return selected, report
    water_service, reason = _bonus_water_service(
        mechanics, observation, cfg, selected, post_farm, route, obligations, checkpoints)
    if reason:
        report['reason'] = reason
        return selected, report
    out = deepcopy(selected)
    remaining = limit
    for index, order in enumerate(out['market']):
        if order and len(order) > 2 and order[:2] == ['SELL', 'FERTILIZER']:
            take = min(max(0, int(order[2])), remaining)
            out['market'][index] = ['SELL', 'FERTILIZER', take] if take else []
            remaining -= take
    report.update(changed=True, reason='reserve_reachable_fertilizer', actor=actor,
                  pickup_step=pickup_step, end=end, required_stock=required,
                  withheld_units=withheld, obligations=obligations,
                  input_value_scenario=input_value, product_value_scenario=product_value,
                  rival_supply_scenario_units=100, cash_spending_reserve=spending,
                  funding_through_step=day_end - 1, cash_input_cushion_multiple=100,
                  bonus_day_water_service=water_service,
                  future_cash_gain_measured=False)
    return out, report
