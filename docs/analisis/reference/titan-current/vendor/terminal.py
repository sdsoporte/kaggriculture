"""Observation-only, joint final-day collection overlay (Apache-2.0).

Transition contracts are from Kaggle engine 28b6d8af; this is a bounded planning
heuristic, not a replacement benchmark engine or a claim of global optimality.
No future rival actions, shop draws, seeds, network, or replay input is used.
"""
from __future__ import annotations
from dataclasses import dataclass
from math import ceil

PRODUCTS = ('WHEAT', 'CARROT', 'TOMATO', 'STRAWBERRY', 'MELON',
            'EGG', 'MILK', 'WOOL', 'FERTILIZER')
# first harvest day, maximal-yield day, yield ceiling, ongoing
CROPS = {'WHEAT': (2, 4, 6, False), 'CARROT': (2, 3, 4, False),
         'TOMATO': (8, 8, 4, True), 'STRAWBERRY': (10, 10, 4, True),
         'MELON': (10, 12, 6, False)}
ANIMALS = {'GOOSE': 'EGG', 'COW': 'MILK', 'SHEEP': 'WOOL'}


def distance(a, b):
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def shed_tiles(board):
    h = board // 2
    return ((h - 1, h - 1), (h, h - 1), (h - 1, h), (h, h))


def toward(a, b):
    if a[0] != b[0]:
        return ['EAST' if a[0] < b[0] else 'WEST']
    if a[1] != b[1]:
        return ['SOUTH' if a[1] < b[1] else 'NORTH']
    return ['PASS']


@dataclass(frozen=True)
class Job:
    pos: tuple[int, int]
    kind: str
    mode: int = 0  # crop: harvest / water+harvest / fertilize+water+harvest

    @property
    def group(self):
        return self.pos, self.kind


def jobs_for(farm, day):
    jobs = []
    for y, row in enumerate(farm['tiles']):
        for x, tile in enumerate(row):
            if not isinstance(tile, dict):
                continue
            pos = (x, y)
            if tile.get('animal') in ANIMALS:
                if tile.get('yield_units', 0) > 0:
                    jobs.append(Job(pos, 'animal'))
                if tile.get('fertilizer_available'):
                    jobs.append(Job(pos, 'fertilizer'))
            elif tile.get('kind') == 'PLANT' and tile.get('crop') in CROPS:
                first, last, ceiling, ongoing = CROPS[tile['crop']]
                age = day - tile['planted_day']
                if age < first:
                    continue
                if tile.get('yield_units', 0) > 0:
                    jobs.append(Job(pos, 'crop'))
                if (not ongoing and not tile.get('watered_today')
                        and (last + 1) // 2 <= age <= last):
                    jobs.extend((Job(pos, 'crop', 1), Job(pos, 'crop', 2)))
    return jobs


def forecast_job(job, tile, now, arrival, day, inventory):
    """Return actual operation sequence and inventory delta, or infeasible.

    Plant decay occurs AFTER each action. A plant already decayed to a weed
    cannot be revived by WATER. Non-ongoing WATER gains can be sold this day.
    """
    if job.kind == 'fertilizer':
        return [['COLLECT_FERTILIZER']], {'FERTILIZER': 1}
    if job.kind == 'animal':
        return [['HARVEST']], {ANIMALS[tile['animal']]: tile['yield_units']}
    crop = tile['crop']
    n = tile['yield_units']
    mls = tile.get('max_lifespan_step', -1)
    def decays(t):
        return mls >= 0 and t >= mls and (t - mls) % 2 == 0
    for t in range(now, arrival):
        if decays(t):
            n -= 1
            if n <= 0:
                return None
    actions = []
    delta = {}
    fertilized = tile.get('fertilized_until_day', -1) >= day
    if job.mode == 2:
        if fertilized or inventory.get('FERTILIZER', 0) <= 0:
            return None
        actions.append(['FERTILIZE'])
        delta['FERTILIZER'] = -1
        fertilized = True
    if job.mode:
        actions.append(['WATER'])
    for t, action in enumerate(actions, arrival):
        if action[0] == 'WATER':
            n = min(CROPS[crop][2], n + (2 if fertilized else 1))
        if decays(t):
            n -= 1
            if n <= 0:
                return None
    if n <= 0:
        return None
    actions.append(['HARVEST'])
    delta[crop] = n
    return actions, delta


def simulate_route(route, pos, inventory, farm, now, final, day, prices, capacity):
    """Feasibility and observed-quote value of one delivery route."""
    inventory = dict(inventory)
    initial = sum(prices.get(k, 0) * n for k, n in inventory.items())
    actions = []
    here = tuple(pos)
    for job in route:
        while here != job.pos:
            a = toward(here, job.pos)
            actions.append(a)
            dx = (a[0] == 'EAST') - (a[0] == 'WEST')
            dy = (a[0] == 'SOUTH') - (a[0] == 'NORTH')
            here = here[0] + dx, here[1] + dy
        tile = farm['tiles'][here[1]][here[0]]
        result = forecast_job(job, tile, now, now + len(actions), day, inventory)
        if result is None:
            return None
        ops, delta = result
        actions.extend(ops)
        for item, amount in delta.items():
            inventory[item] = inventory.get(item, 0) + amount
    goal = min(shed_tiles(len(farm['tiles'])), key=lambda p: (distance(here, p), p))
    while here != goal:
        a = toward(here, goal)
        actions.append(a)
        here = (here[0] + (a[0] == 'EAST') - (a[0] == 'WEST'),
                here[1] + (a[0] == 'SOUTH') - (a[0] == 'NORTH'))
    amount = sum(max(0, n) for n in inventory.values())
    # Large loads need selective deposits across separate market rounds. Actual
    # shared-capacity admissions are selected jointly, never an overflowing DROP.
    deposits = (1 if amount <= capacity else
                sum(ceil(n / capacity) for k, n in inventory.items() if k in PRODUCTS and n > 0))
    cost = len(actions) + (deposits if amount else 0)
    if now + cost - 1 > final:
        return None
    value = sum(prices.get(k, 0) * n for k, n in inventory.items()) - initial
    return value, cost, actions, inventory


def assign_routes(farm, inventories, now, final, day, prices, capacity, seed_routes=None):
    """Greedy joint insertion; one reservation per actual tile resource."""
    positions = [farm['farmer'], *farm['hands']]
    routes = seed_routes if seed_routes is not None else [[] for _ in positions]
    evaluations = [simulate_route(routes[i], p, inventories[i], farm, now, final, day, prices, capacity)
                   for i, p in enumerate(positions)]
    # An inherited route invalidated by actual observed state is not executed.
    for i, row in enumerate(evaluations):
        if row is None:
            routes[i] = []
            evaluations[i] = simulate_route([], positions[i], inventories[i], farm, now, final, day, prices, capacity)
    choices = jobs_for(farm, day)
    reserved = {job.group for route in routes for job in route}
    max_visits = 6 if seed_routes is not None else 4
    for _ in range(min(len(choices), max_visits * len(positions))):
        best = None
        for job in choices:
            if job.group in reserved:
                continue
            for worker, route in enumerate(routes):
                if len(route) >= max_visits or evaluations[worker] is None:
                    continue
                old_value, old_cost = evaluations[worker][:2]
                for slot in range(len(route) + 1):
                    trial = route[:slot] + [job] + route[slot:]
                    evaluation = simulate_route(trial, positions[worker], inventories[worker],
                                                farm, now, final, day, prices, capacity)
                    if evaluation is None:
                        continue
                    gain = evaluation[0] - old_value
                    if gain <= 0:
                        continue
                    extra = max(1, evaluation[1] - old_cost)
                    key = (gain / extra, gain, -evaluation[1], -worker, -slot)
                    if best is None or key > best[0]:
                        best = key, worker, trial, evaluation, job.group
        if best is None:
            break
        _, worker, route, evaluation, group = best
        routes[worker] = route
        evaluations[worker] = evaluation
        reserved.add(group)
    # Efficiency-first insertion can settle for HARVEST before profitable WATER
    # or FERTILIZE. Spend remaining route slack on higher total cash, including
    # trading a low-value fertilizer pickup for a productive crop treatment.
    for worker, route in enumerate(routes):
        if evaluations[worker] is None:
            continue
        for _ in range(12):
            incumbent = evaluations[worker]
            best = (incumbent[0], -incumbent[1]), route, incumbent
            for remove in range(-1, len(route)):
                base = route if remove < 0 else route[:remove] + route[remove + 1:]
                candidates = [base]
                for index, job in enumerate(base):
                    if job.kind == 'crop':
                        for mode in (0, 1, 2):
                            replacement = Job(job.pos, job.kind, mode)
                            if replacement in choices:
                                candidates.append(base[:index] + [replacement] + base[index + 1:])
                for trial in candidates:
                    evaluation = simulate_route(trial, positions[worker], inventories[worker],
                                                farm, now, final, day, prices, capacity)
                    if evaluation is not None and (evaluation[0], -evaluation[1]) > best[0]:
                        best = (evaluation[0], -evaluation[1]), trial, evaluation
            if best[1] == route:
                break
            route, evaluations[worker] = best[1:]
            routes[worker] = route
    return routes, evaluations


def joint_deposits(inventories, eligible, shed, capacity, prices):
    """Capacity DP in real worker order. No destructive overflowing DROP.

    Options are PASS, fitting DROP, or one selective PLACE. Valuation uses
    current quotes (not knowledge of the rival's future market orders).
    Returns chosen actions and exact projected shed, including nonproducts.
    """
    room = max(0, capacity - sum(shed.values()))
    states = {0: (0.0, [])}
    for worker in eligible:
        inv = {k: n for k, n in inventories[worker].items() if n > 0}
        updated = {}
        for used, (score, plan) in states.items():
            options = [(['PASS'], {}, 0)]
            if sum(inv.values()) <= room - used and inv:
                options.append((['DROP'], inv, sum(inv.values())))
            for item, n in inv.items():
                if item in PRODUCTS:
                    for qty in range(1, min(n, room - used) + 1):
                        options.append((['PLACE', item, qty], {item: qty}, qty))
            for action, deposited, qty in options:
                new_score = score + sum(prices.get(k, 0) * n for k, n in deposited.items())
                key = used + qty
                if key not in updated or new_score > updated[key][0]:
                    updated[key] = new_score, plan + [(worker, action, deposited)]
        states = updated
    best = max(states.values(), key=lambda row: row[0])
    projected = dict(shed)
    actions = {}
    for worker, action, deposited in best[1]:
        actions[worker] = action
        for item, n in deposited.items():
            projected[item] = projected.get(item, 0) + n
    return actions, projected


def overlay(observation, parent_action, configuration=None):
    """Compose after the parent. Exact parent through final-day hire/pickup.

    Caller owns parent state. The overlay itself is stateless and replans from
    the current visible observation; safe to compose after other earlier lanes.
    """
    cfg = configuration or {}
    turns = int(cfg.get('turnsPerDay', 24))
    final = int(cfg.get('episodeSteps', 720)) - 2
    now = int(observation.get('step', 0))
    start = (final // turns) * turns + 2
    if now < start or now > final:
        return parent_action
    farm = observation['farms'][int(observation['player'])]
    private = observation['private']
    capacity = max(1, int(cfg.get('shedCapacity', 100)))
    prices = {k: max(1, observation['market']['prices'].get(k, 1)) for k in PRODUCTS}
    positions = [farm['farmer'], *farm['hands']]
    inventories = [dict(private.get('inventories', [])[i])
                   if i < len(private.get('inventories', [])) else {} for i in range(len(positions))]
    _, evaluations = assign_routes(farm, inventories, now, final, now // turns, prices, capacity)
    actions = []
    eligible = []
    for i, pos in enumerate(positions):
        evaluation = evaluations[i]
        if evaluation and evaluation[2]:
            actions.append(evaluation[2][0])
        elif tuple(pos) in shed_tiles(len(farm['tiles'])):
            actions.append(['PASS'])
            eligible.append(i)
        else:
            goal = min(shed_tiles(len(farm['tiles'])), key=lambda p: (distance(pos, p), p))
            actions.append(toward(pos, goal))
    deposits, projected = joint_deposits(inventories, eligible, private['shed'], capacity, prices)
    for i, action in deposits.items():
        actions[i] = action
    market = [['SELL', k, n] for k, n in projected.items() if k in PRODUCTS and n > 0]
    market.sort(key=lambda o: (-prices[o[1]] * o[2], o[1]))
    return {'farmer': actions[0], 'hands': actions[1:],
            'market': market[:int(cfg.get('maxMarketOrdersPerTurn', 10))]}


def inherited_routes(farm, own_future_actions, day):
    """Convert the parent's already-known own route tape to observed resources.

    This is a policy seed, not replay/rival lookahead. Every job must exist in the
    visible farm. Impossible/no-op maintenance and duplicate targets are removed.
    Shortest-path reconstruction may recover wasted travel, while retaining the
    parent's established crop-treatment and collection order.
    """
    positions = [tuple(farm['farmer']), *(tuple(p) for p in farm['hands'])]
    routes = [[] for _ in positions]
    pending = [{} for _ in positions]
    valid = set(jobs_for(farm, day))
    used = set()
    board = len(farm['tiles'])
    moves = {'NORTH': (0, -1), 'SOUTH': (0, 1), 'EAST': (1, 0), 'WEST': (-1, 0)}
    for action in own_future_actions:
        actions = [action.get('farmer', ['PASS']), *action.get('hands', [])]
        for i, raw in enumerate(actions[:len(positions)]):
            if not raw:
                continue
            op = raw[0]
            x, y = positions[i]
            if op in moves:
                dx, dy = moves[op]
                if 0 <= x + dx < board and 0 <= y + dy < board:
                    positions[i] = x + dx, y + dy
                continue
            pos = (x, y)
            tile = farm['tiles'][y][x]
            if op in ('FERTILIZE', 'WATER'):
                pending[i].setdefault(pos, set()).add(op)
                continue
            job = None
            if op == 'COLLECT_FERTILIZER':
                job = Job(pos, 'fertilizer')
            elif op == 'HARVEST' and isinstance(tile, dict):
                if tile.get('animal') in ANIMALS:
                    job = Job(pos, 'animal')
                elif tile.get('kind') == 'PLANT':
                    treatment = pending[i].pop(pos, set())
                    mode = 2 if 'WATER' in treatment and 'FERTILIZE' in treatment else int('WATER' in treatment)
                    job = next((Job(pos, 'crop', m) for m in range(mode, -1, -1)
                                if Job(pos, 'crop', m) in valid), None)
            if job in valid and job.group not in used:
                used.add(job.group)
                routes[i].append(job)
    return routes


class Planner:
    """Commit a jointly planned final-day route; do not swap workers each turn.

    All final-day local production/decay is determined by the observed farm.
    Deposits are still admitted against actual shared capacity on each turn.
    Earlier actions are delegated untouched. Start/restart on a discontinuity.
    """
    def __init__(self):
        self.queues = None
        self.last_step = None
        self.player = None

    def act(self, observation, parent_action, configuration=None, own_future_actions=None):
        cfg = configuration or {}
        turns = int(cfg.get('turnsPerDay', 24))
        final = int(cfg.get('episodeSteps', 720)) - 2
        now = int(observation.get('step', 0))
        start = (final // turns) * turns + 2
        player = int(observation['player'])
        if now < start or now > final:
            self.queues = None
            self.last_step = now
            return parent_action
        farm = observation['farms'][player]
        private = observation['private']
        capacity = max(1, int(cfg.get('shedCapacity', 100)))
        prices = {k: max(1, observation['market']['prices'].get(k, 1)) for k in PRODUCTS}
        positions = [farm['farmer'], *farm['hands']]
        inventories = [dict(private.get('inventories', [])[i])
                       if i < len(private.get('inventories', [])) else {} for i in range(len(positions))]
        if (self.queues is None or self.player != player or self.last_step != now - 1
                or len(self.queues) != len(positions)):
            seeds = inherited_routes(farm, own_future_actions, now // turns) if own_future_actions is not None else None
            _, evaluations = assign_routes(farm, inventories, now, final, now // turns, prices, capacity, seeds)
            self.queues = [list(row[2]) if row is not None else [] for row in evaluations]
        self.last_step, self.player = now, player
        actions, eligible = [], []
        for i, pos in enumerate(positions):
            if self.queues[i]:
                actions.append(self.queues[i].pop(0))
            elif tuple(pos) in shed_tiles(len(farm['tiles'])):
                actions.append(['PASS'])
                eligible.append(i)
            else:
                goal = min(shed_tiles(len(farm['tiles'])), key=lambda p: (distance(pos, p), p))
                actions.append(toward(pos, goal))
        deposits, projected = joint_deposits(inventories, eligible, private['shed'], capacity, prices)
        for i, action in deposits.items():
            actions[i] = action
        market = [['SELL', k, n] for k, n in projected.items() if k in PRODUCTS and n > 0]
        market.sort(key=lambda o: (-prices[o[1]] * o[2], o[1]))
        return {'farmer': actions[0], 'hands': actions[1:],
                'market': market[:int(cfg.get('maxMarketOrdersPerTurn', 10))]}
