# SPDX-License-Identifier: Apache-2.0
"""One selected producer, ALDER seed budget, T08 arrivals and ordered SELL.

No future producer/controller decisions are invoked. The short continuation is
conditional on the current tape and already committed one-way cap errands.
"""
from copy import deepcopy
from collections import Counter
import importlib.util
from pathlib import Path
import sys

import mechanics as m
from ordered_selected_sell import OrderedSelectedSell, _projection as atlas
from selected_action_sell import absolute_step

HERE = Path(__file__).resolve().parent


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


budget_module = load('_integrated_alder', HERE/'reference/integrated-selected/alder/seed_budget.py')
arrival_module = load('_integrated_t08', HERE/'reference/selected-action/t08/arrival_contract.py')


def make_production():
    """Instantiate precisely one intact Arlene inside the pinned cap producer."""
    vendor = HERE/'reference/integrated-selected/claude'
    sys.path.insert(0, str(vendor))
    import arlene_plan
    import arrival_facts
    import native_motifs
    parent = load('_integrated_arlene', HERE/'reference/next-panel/vendor/arlene.py')
    chooser = arrival_facts.RouteAwareCapChooser(native_motifs.engine())
    production = arlene_plan.PlanOverlay(parent, chooser, one_way=True)
    chooser.agent = production.agent
    return production


def plant_requests(action):
    return Counter(a[1] for a in [action.get('farmer', []), *action.get('hands', [])]
                   if len(a) > 1 and a[0] == 'PLANT')


class IntegratedSelectedAgent:
    def __init__(self, production=None, *, seed=True, committed=True, sell=True, horizon=8,
                 seed_queue_selector=None):
        self.production = make_production() if production is None else production
        self.controller = self.production.agent
        self.execution = OrderedSelectedSell(horizon)
        self.budget = budget_module.SeedBudget(self.controller.R)
        self.seed, self.committed = bool(seed), bool(committed)
        self.sell = bool(sell)
        # Optional current-market selector; no callback preserves the frozen rule.
        self.seed_queue_selector = seed_queue_selector
        self.diagnostics = {}
        self.last_packet = None
        self.last_selected = self.last_seeded = None

    def _projection(self, obs, cfg, selected, farm, private, plans):
        """Continue ATLAS primitives from the already executed current unit stage.

        Seed reduction occurs between units and market. Reusing a projection made
        with the old seed purchases would give future PLANT the wrong stock.
        """
        now = absolute_step(obs, cfg)
        tpd = int(cfg.get('turnsPerDay', 24)); board = int(cfg.get('boardSize', 10))
        cap = int(cfg.get('shedCapacity', 100)); maximum = int(cfg.get('maxMarketOrdersPerTurn', 10))
        last = int(cfg.get('episodeSteps', 720))-2
        end = min(now+self.execution.seller.horizon, last, (now//tpd+1)*tpd-1)
        route = self.controller.R[self.controller.cur]
        switches = {int(row[0]) for row in getattr(self.production.A, 'DECISIONS', ())}
        events, future, omissions = [], {}, []
        reached, reason = now, 'horizon'
        detached_future = False
        for step in range(now, end+1):
            if step == now:
                action = selected
            else:
                if step in switches or step >= len(route):
                    reason = 'route_boundary'; break
                action = deepcopy(route[step])
                if any(o and o[0] == 'BUY_PRODUCT' for o in action.get('market', [])):
                    reason = 'future_product_purchase_needs_cash_bound'; break
                units = [action.get('farmer', ['PASS']), *action.get('hands', [])]
                excluded = set()
                stop = False
                for worker, plan in list(plans.items()):
                    pos = m._farmer_position(farm, worker)
                    if pos is None or worker >= len(units) or units[worker] != ['PASS']:
                        stop = True; break
                    if plan['steps'] >= self.production.max_steps:
                        stop = True; break
                    op = self.production._next_op(plan, pos, m._farmer_inventory(private, worker), board)
                    if op is None:
                        stop = True; break
                    units[worker] = op
                    plan['steps'] += 1
                    if op[0] == 'HARVEST':
                        excluded.add((step, worker))
                        del plans[worker]
                if stop:
                    reason = 'committed_continuation_boundary'; break
                action['farmer'], action['hands'] = units[0], units[1:]
                if not detached_future:
                    # Preserve the caller-visible current-market boundary once.
                    farm, private = deepcopy(farm), deepcopy(private)
                    detached_future = True
                trial_events, trial_omissions = [], []
                try:
                    atlas._units(m, farm, private, action, step, board, tpd, cap,
                                 lossless=True, events=trial_events, excluded=excluded,
                                 omissions=trial_omissions)
                except atlas.ProjectionError:
                    reason = 'stock_dependent_pickup'; break
                # A failed unit stage ends the projection; no rollback copy is
                # needed because its detached state and trial events are discarded.
                events.extend(trial_events); omissions.extend(trial_omissions)
                future[step] = atlas._market(action, maximum)
            reached = step
            atlas._full_market(m, farm, private, atlas._market(action, maximum), board,
                               int(cfg.get('farmHandCostMult', 1)))
            m._decay_plants(farm, step)
            if (step+1) % tpd == 0:
                # Refresh changes tiles, not the carried goods deposited here.
                for worker, inv in enumerate(private['inventories']):
                    for item, q in inv.items():
                        atlas._record(events, step, 'after_market', worker, 'EOD', item, q)
        return {'observed_step':now, 'end_step':reached, 'stock_events':events,
                'future_market':future}, {'end_reason':reason, 'excluded_harvests':omissions}

    def transform(self, obs, cfg, selected, *, fallback_action=None, reservations=None):
        """Consume an action already chosen by this object's production owner.

        For an injected PlanOverlay, call production.act first, then this method.
        act() is the complete callable and performs that single call itself.
        An optional seed_queue_selector receives detached post-unit inputs only
        after demand eligibility and returns (seed-only action, funding report).
        """
        cfg = dict(cfg or {}); obs = dict(obs)
        now = absolute_step(obs, cfg); obs['step'] = now
        fallback = selected if fallback_action is None else fallback_action
        self.last_selected = deepcopy(selected)
        self.diagnostics = {'status':'fallback', 'parent_calls_in_transform':0}
        try:
            if not self.production.one_way:
                raise ValueError('This continuation adapter requires the pinned one-way producer')
            farm = deepcopy(obs['farms'][int(obs['player'])]); private = deepcopy(obs['private'])
            atlas._units(m, farm, private, selected, now, int(cfg.get('boardSize', 10)),
                         int(cfg.get('turnsPerDay',24)), int(cfg.get('shedCapacity',100)),
                         lossless=False, events=[], excluded=set(), omissions=[])
            post = deepcopy(obs)
            post['farms'][int(obs['player'])] = deepcopy(farm); post['private'] = deepcopy(private)
            seeded = deepcopy(selected)
            seed_reason = 'disabled'
            seed_funding = None
            tape = self.controller.R[self.controller.cur]
            if self.seed:
                if now >= len(tape) or plant_requests(selected) - plant_requests(tape[now]):
                    seed_reason = 'selected_plant_requests_exceed_route'
                else:
                    proposed = self.budget.apply(selected, private['seeds'], now, self.controller.cur,
                        int(cfg.get('maxMarketOrdersPerTurn',10)))
                    edits = [i for i,(a,b) in enumerate(zip(selected.get('market',[]), proposed.get('market',[]))) if a != b]
                    # Trimming an earlier buy can activate a later unchanged hire
                    # or acquisition. Keep that queue intact unless the supplied
                    # selector establishes the current-market funding case.
                    dependent = any(o and o[0] in ('HIRE','BUY_LAND','BUY_PRODUCT','BUY_ANIMAL')
                                    for i in edits for o in selected['market'][i+1:])
                    if dependent:
                        seed_reason = 'later_economic_order'; seeded = deepcopy(selected)
                        if self.seed_queue_selector is not None:
                            seeded, seed_funding = self.seed_queue_selector(
                                m, deepcopy(post), deepcopy(selected), deepcopy(proposed), dict(cfg))
                            if not isinstance(seeded, dict) or not isinstance(seed_funding, dict):
                                raise TypeError('seed_queue_selector must return (action dict, report dict)')
                            seeded, seed_funding = deepcopy(seeded), deepcopy(seed_funding)
                            if seed_funding.get('status') == 'certified':
                                seed_reason = 'funded_economic_order'
                    else:
                        seed_reason = 'applied'; seeded = proposed
            self.last_seeded = deepcopy(seeded)
            if seed_funding is not None:
                self.diagnostics['seed_funding'] = seed_funding
            if not self.sell:
                self.diagnostics.update(status='parent_control', seed_reason=seed_reason,
                                        selected_unit_stages=1)
                return seeded
            snapshot = self.production.producer_snapshot(post, seeded)
            contract = arrival_module.build_arrival_contract(post, cfg, seeded, [snapshot])
            projection, details = self._projection(obs, cfg, seeded, deepcopy(farm), deepcopy(private),
                                                    deepcopy(self.production.plans))
            self.last_packet = {'post_unit_observation':post, 'projection':projection,
                                'arrival_contract':contract, 'snapshot':snapshot}
            # Direct generic API consumes the same current unit stage. It must
            # receive the actual reduced seed queue, not the old prepared binding.
            out = self.execution.seller.transform(obs, cfg, seeded,
                post_unit_shed=private['shed'], projection=projection,
                arrival_contract=contract if self.committed else None,
                reservations=reservations, fallback_action=seeded if fallback_action is None else fallback)
            self.diagnostics = dict(self.execution.seller.diagnostics)
            self.diagnostics.update(seed_reason=seed_reason, continuation=details,
                selected_unit_stages=1, parent_calls_in_transform=0,
                committed_capacity=self.committed)
            if seed_funding is not None:
                self.diagnostics['seed_funding'] = seed_funding
            return out
        except (ValueError, TypeError, KeyError, AttributeError, IndexError) as error:
            self.diagnostics['reason'] = str(error)
            return deepcopy(fallback)

    def act(self, observation, configuration=None):
        obs = dict(observation); cfg = dict(configuration or {})
        obs['step'] = absolute_step(obs, cfg)
        selected = self.production.act(obs)
        return self.transform(obs, cfg, selected)

    __call__ = act


def make_agent(production=None, *, seed=True, committed=True, sell=True, horizon=8,
               seed_queue_selector=None):
    return IntegratedSelectedAgent(production, seed=seed, committed=committed, sell=sell,
                                   horizon=horizon, seed_queue_selector=seed_queue_selector)


_INSTANCE = None

def agent(observation, configuration=None):
    global _INSTANCE
    cfg = dict(configuration or {})
    if _INSTANCE is None or absolute_step(observation, cfg) == 0:
        _INSTANCE = make_agent()
    return _INSTANCE.act(observation, cfg)
