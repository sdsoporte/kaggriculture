# SPDX-License-Identifier: Apache-2.0
"""Prospective fourth-quadrant work bundles over authored production routes.

The planner supplies physical schedules and dated costs to an economic admission
callable. It never purchases land without a complete funded work proposal. Known
own route variants are checked separately; no opponent continuation is supplied.
"""
from copy import deepcopy

MOVES = {'NORTH': (0, -1), 'SOUTH': (0, 1), 'EAST': (1, 0), 'WEST': (-1, 0)}
ACCESS = ((4, 4), (5, 4), (4, 5), (5, 5))


def walk(a, b):
    x, y = a
    q = []
    while x != b[0]:
        q.append(['EAST' if x < b[0] else 'WEST']); x += 1 if x < b[0] else -1
    while y != b[1]:
        q.append(['SOUTH' if y < b[1] else 'NORTH']); y += 1 if y < b[1] else -1
    return q


def action(row, i):
    return row.get('farmer', ['PASS']) if i == 0 else (row.get('hands', [])[i-1] if i <= len(row.get('hands', [])) else ['PASS'])


def set_action(row, i, value):
    hands = row.setdefault('hands', [])
    while len(hands) < i:
        hands.append(['PASS'])
    hands[i-1] = list(value)


def calendar(route, first_day):
    """Exact authored movement/spawn calendar, independent of farm production.

    Extra hands are hired strictly after every incumbent HIRE in that day, so
    incumbent spawn selection remains unchanged. An observed runtime mismatch
    cannot be silently treated as this calendar.
    """
    days = {}
    for day in range(first_day, 30):
        start = day * 24
        hires = [t for t in range(start, min(start+24, 719))
                 if any(o and o[0] == 'HIRE' for o in route[t].get('market', []))]
        hire_step = max(hires, default=start-1) + 1
        if hire_step >= min(start+23, 718):
            continue
        positions = [(4, 4)]
        for t in range(start, hire_step+1):
            for i, pos in enumerate(positions):
                a = action(route[t], i)
                delta = MOVES.get(a[0] if a else '')
                if delta:
                    positions[i] = (max(0, min(9, pos[0]+delta[0])), max(0, min(9, pos[1]+delta[1])))
            for order in route[t].get('market', []):
                if order and order[0] == 'HIRE':
                    positions.append(min(ACCESS, key=lambda p: (positions.count(p), ACCESS.index(p))))
        days[day] = {'step': hire_step, 'positions': positions, 'hands': len(positions)-1}
    return days


def crop_days(crop, start):
    if crop == 'MELON':
        if start+12 > 29:
            return None
        return {start+d: ('plant' if d == 0 else 'water') for d in (0, 2, 4, 6, 8, 10)} | {start+12: 'water_harvest'}
    if crop == 'TOMATO':
        if start+11 > 29:
            return None
        return {start+d: ('plant' if d == 0 else 'water') for d in (0, 2, 4, 6, 8, 10)} | {start+11: 'harvest'}
    result = {}
    for day in range(start, 27, 4):
        result[day] = 'plant' if day == start else 'replant'
        result[day+2] = 'water'
        result[day+3] = 'water_harvest'
    return result or None


def tour(origin, tiles, kind, crop):
    """One finite work route, with explicit shed return on every harvest day."""
    remaining = list(tiles); pos = origin; result = []; visits = []
    while remaining:
        target = min(remaining, key=lambda p: (len(walk(pos, p)), p))
        remaining.remove(target)
        result.extend(walk(pos, target)); pos = target
        ops = {'plant': [['PLANT', crop], ['WATER']],
               'replant': [['DIG'], ['PLANT', crop], ['WATER']],
               'water': [['WATER']], 'harvest': [['HARVEST']],
               'water_harvest': [['WATER'], ['HARVEST']]}[kind]
        for op in ops:
            visits.append((len(result), target, list(op)))
            result.append(list(op))
    if 'harvest' in kind:
        result.extend(walk(pos, (5, 5))); result.append(['DROP'])
    return result, visits


def proposals(mechanics, observation, routes, current, configuration):
    """Enumerate bounded complete alternatives; admission owns valuation.

    Called only at a day boundary. Each result includes all compatible authored
    continuations, dated incremental hire/seed/land costs, worker actions and
    exact unfertilized crop receipts. Prices are deliberately not extrapolated.
    """
    now = int(observation['step']); day = now//24
    farm = observation['farms'][int(observation['player'])]
    if (now % 24 or day < 11 or day > 25 or len(farm['tiles']) != 10
            or farm.get('unlocked_quadrants') != ['NW', 'NE', 'SW']
            or farm.get('hands') or tuple(farm['farmer']) != (4, 4)):
        return []
    if any(farm['tiles'][y][x] != 'LOCKED' for y in range(5, 10) for x in range(5, 10)):
        return []
    compatible = {key: rows for key, rows in routes.items()
                  if key == current or rows[:now] == routes[current][:now]}
    calendars = {key: calendar(rows, day) for key, rows in compatible.items()}
    tiles = sorted(((x, y) for y in range(5, 10) for x in range(5, 10)),
                   key=lambda p: (sum(p), p[1], p[0]))
    answer = []
    for crop in ('CARROT', 'TOMATO', 'MELON'):
        schedule = crop_days(crop, day)
        if not schedule:
            continue
        cycles = sum(kind in ('plant', 'replant') for kind in schedule.values())
        for workers in (1, 2, 3):
            for size in range(3, 8):
                if workers*size > 25:
                    continue
                # Round-robin groups keep every worker near the shed; all actual
                # tours, rather than a distance proxy, must fit every route.
                chosen = tiles[:workers*size]
                groups = [chosen[i::workers] for i in range(workers)]
                variants = {}; feasible = True
                for key, rows in compatible.items():
                    patch = {}; receipts = []; costs = []; worker_days = []; lots = []; growing = {}
                    for work_day, kind in schedule.items():
                        entry = calendars[key].get(work_day)
                        if entry is None:
                            feasible = False; break
                        step = entry['step']; positions = list(entry['positions'])
                        first = work_day == day
                        orders = ([['BUY_LAND'], ['BUY_SEED', crop, len(chosen)*cycles]] if first else [])
                        orders += [['HIRE'] for _ in range(workers)]
                        if len(rows[step].get('market', []))+len(orders) > int(configuration.get('maxMarketOrdersPerTurn', 10)):
                            feasible = False; break
                        row = deepcopy(rows[step]); row.setdefault('market', []).extend(orders); patch[step] = row
                        cost = sum(mechanics._hire_cost(entry['hands']+i, int(configuration.get('farmHandCostMult', 1))) for i in range(workers))
                        if first:
                            cost += 4000 + mechanics.CROPS[crop]['seed']*len(chosen)*cycles
                        costs.append({'step': step, 'cash': cost})
                        worker_days.append({'day': work_day, 'kind': kind, 'hire_step': step, 'incumbent_hands': entry['hands'], 'incumbent_positions': entry['positions']})
                        for i, group in enumerate(groups):
                            origin = min(ACCESS, key=lambda p: (positions.count(p), ACCESS.index(p)))
                            positions.append(origin)
                            sequence, visits = tour(origin, group, kind, crop)
                            if step+len(sequence) >= min((work_day+1)*24, 719):
                                feasible = False; break
                            for offset, unit_action in enumerate(sequence, 1):
                                t = step+offset
                                row = deepcopy(patch.get(t, rows[t])); set_action(row, entry['hands']+i+1, unit_action); patch[t] = row
                            harvested = []
                            for offset, tile, op in visits:
                                t = step+1+offset
                                if op[0] == 'PLANT':
                                    growing[tile] = {'tile': list(tile), 'crop': crop,
                                        'plant_step': t, 'water_steps': [],
                                        'worker': entry['hands']+i+1}
                                elif op[0] == 'WATER':
                                    growing[tile]['water_steps'].append(t)
                                elif op[0] == 'HARVEST':
                                    growing[tile]['harvest_step'] = t
                                    harvested.append(growing.pop(tile))
                            if 'harvest' in kind:
                                delivery = step+len(sequence)
                                quantity = len(group)*{'TOMATO': 4, 'CARROT': 3, 'MELON': 5}[crop]
                                row = deepcopy(patch[delivery]); market = row.setdefault('market', [])
                                if len(market) >= int(configuration.get('maxMarketOrdersPerTurn', 10)):
                                    feasible = False; break
                                slot = len(market); market.append(['SELL', crop, quantity]); patch[delivery] = row
                                for lot in harvested:
                                    lot.update(drop_step=delivery, sale_step=delivery, sale_slot=slot)
                                    lots.append(lot)
                                receipts.append({'step': delivery, 'crop': crop, 'units': quantity, 'worker': entry['hands']+i+1})
                        if not feasible:
                            break
                    if not feasible:
                        break
                    first_entry = calendars[key][day]
                    variants[key] = {'patches': patch, 'receipts': receipts, 'costs': costs, 'worker_days': worker_days,
                        'bundle': {'route_id': key, 'base_route_id': key,
                            'target_quadrant': 'SE', 'land': {'step': first_entry['step'],
                                'slot': len(rows[first_entry['step']].get('market', []))},
                            'rejoin_step': min(719, (max(schedule)+1)*24), 'lots': lots}}
                if feasible:
                    answer.append({'crop': crop, 'tiles': chosen, 'workers': workers, 'start': now,
                                   'variants': variants, 'seed_units': len(chosen)*cycles,
                                   'cost': max(sum(x['cash'] for x in v['costs']) for v in variants.values())})
    return answer


def economic_program(proposal, route_id, base_route):
    """Return ECON's concrete full-route and lot contract without valuation."""
    variant = proposal['variants'][route_id]
    candidate = list(base_route)
    for t, row in variant['patches'].items():
        candidate[t] = deepcopy(row)
    bundle = deepcopy(variant['bundle'])
    bundle['candidate_route'] = candidate
    return candidate, bundle


class FourthQuadrant:
    """Install once per controller and retain this object across reconstruction.

    ``admit(m, obs, configuration, routes, proposals)`` returns one supplied
    proposal or None. The admission implementation owns economic estimates.
    ``finish`` must receive the actual action after the canonical deadline guard.
    """
    def __init__(self, mechanics, admit):
        self.m = mechanics; self.admit = admit; self.configuration = {}
        self.plan = None; self.pending = None; self.selected = None
        self.events = []; self.last_scan = None; self.disabled = False
        self.generation = 0

    def configure(self, configuration):
        self.configuration = dict(configuration or {})

    def install(self, controller):
        pristine = controller.R
        original = controller.act
        last_table = None
        def act(obs):
            nonlocal last_table
            now = int(obs['step'])
            self.pending = None; self.selected = None
            # Past rows remain pristine so branch-prefix decisions retain their
            # original meaning. The wrapper outside this one may add only its
            # separately committed spatial suffix before this call.
            # If no outer route owner supplied a fresh table, discard our last
            # table before applying only committed proposals. This also handles
            # a completed producer whose returned fallback rejected admission.
            incoming = pristine if controller.R is last_table else controller.R
            routes = {key: list(rows) for key, rows in incoming.items()}
            for key in routes:
                routes[key][:now] = pristine[key][:now]
            proposal = self.plan
            if (proposal is None and not self.disabled and now % 24 == 0
                    and self.last_scan != now and self.admit is not None
                    and int(self.configuration.get('episodeSteps', 720)) == 720
                    and int(self.configuration.get('turnsPerDay', 24)) == 24
                    and int(self.configuration.get('shedCapacity', 100)) == 100):
                self.last_scan = now
                options = proposals(self.m, obs, pristine, controller.cur, self.configuration)
                proposal = self.admit(self.m, obs, self.configuration, pristine, options)
                if proposal is not None and not any(proposal is p for p in options):
                    raise ValueError('admission must select an evaluated proposal')
                if proposal is not None:
                    self.pending = proposal
            if proposal is not None:
                for key, variant in proposal['variants'].items():
                    for t, row in variant['patches'].items():
                        if t >= now:
                            routes[key][t] = deepcopy(row)
                controller.R = routes
                variant = proposal['variants'].get(controller.cur)
                if variant is None:
                    self._abort(now, 'unplanned_route', controller, pristine)
                elif not self._physical_match(obs, routes[controller.cur], variant):
                    self._abort(now, 'observed_position_or_worker_mismatch', controller, pristine)
            else:
                controller.R = routes
            last_table = controller.R
            result = original(obs)
            self.selected = deepcopy(result)
            return result
        controller.act = act

    def _abort(self, step, reason, controller, pristine):
        # A missed physical commitment is retained as a failure event. Further
        # optional spending stops; it is never relabelled a successful bundle.
        self.events.append({'step': step, 'kind': 'bundle_aborted', 'reason': reason})
        self.plan = None; self.pending = None; self.disabled = True; self.generation += 1
        controller.R = {key: list(rows) for key, rows in pristine.items()}

    def _physical_match(self, obs, route, variant):
        now = int(obs['step']); day = now//24
        farm = obs['farms'][int(obs['player'])]
        entry = next((v for v in variant['worker_days'] if v['day'] == day), None)
        if entry is None:
            return True
        hire_step = entry['hire_step']; count = entry['incumbent_hands']
        positions = [tuple(farm['farmer'])]+[tuple(p) for p in farm['hands']]
        if now == hire_step:
            # Compare after the same unit stage used by the engine, before any
            # appended HIRE. This also detects an unfulfilled incumbent hire.
            if len(positions) != count+1:
                return False
            for i, pos in enumerate(positions):
                a = action(route[now], i); delta = MOVES.get(a[0] if a else '')
                if delta:
                    positions[i] = (max(0, min(9, pos[0]+delta[0])), max(0, min(9, pos[1]+delta[1])))
            return positions == [tuple(p) for p in entry['incumbent_positions']]
        if now < hire_step:
            return True
        proposal = self.pending or self.plan
        if proposal is None or len(positions) != count+proposal['workers']+1:
            return False
        expected = [tuple(p) for p in entry['incumbent_positions']]
        for _ in range(proposal['workers']):
            expected.append(min(ACCESS, key=lambda p: (expected.count(p), ACCESS.index(p))))
        for t in range(hire_step+1, now):
            for i in range(count+1, len(expected)):
                a = action(variant['patches'].get(t, route[t]), i); delta = MOVES.get(a[0] if a else '')
                if delta:
                    expected[i] = (max(0, min(9, expected[i][0]+delta[0])), max(0, min(9, expected[i][1]+delta[1])))
        return positions[count+1:] == expected[count+1:]

    def finish(self, observation, returned_action):
        if self.pending is not None and self.selected is not None:
            # Admission is at the day boundary, before the first appended spend.
            # Only a fully returned producer can publish the future proposal.
            now = int(observation['step'])
            count = 1+len(observation['farms'][int(observation['player'])]['hands'])
            hire_count = lambda row: sum(bool(o) and o[0] == 'HIRE' for o in row.get('market', []))
            if (self.pending['start'] == now
                    and all(action(returned_action, i) == action(self.selected, i) for i in range(count))
                    and hire_count(returned_action) == hire_count(self.selected)):
                self.plan = self.pending; self.generation += 1
                self.events.append({'step': now, 'kind': 'bundle_admitted',
                    'crop': self.plan['crop'], 'tiles': self.plan['tiles'],
                    'workers': self.plan['workers'], 'reserved_cash': self.plan['cost']})
        self.pending = None; self.selected = None
