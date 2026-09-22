# SPDX-License-Identifier: Apache-2.0
"""One persistent complete sale plan over a caller-supplied base action.

All production decisions, projections and feasible-plan checks belong to the
caller. This module constructs no controller and reads no simulator state.
"""
import copy
from fractions import Fraction
from math import lcm
import random

from solver import solve_table


class WholePlanSelector:
    def __init__(self, rng=None):
        self.rng = rng if rng is not None else random
        self.active = None
        self.completed = set()
        self.draws = 0
        self.last_decision = {'reason': 'not_called'}

    def choose(self, key, plans, deltas, *, feasible, mode='mixed', learned_start=0):
        """Commit once; repeat calls with the same key return the same plan.

        Each plan is {'id': str, 'sales': [[absolute_step, quantity], ...]}.
        The baseline must be first. The feasibility callback is evaluated on
        EVERY constituent before any distribution can be selected.
        """
        if self.active is not None:
            if self.active['key'] != key:
                raise ValueError('Finish the committed window before another lot')
            return copy.deepcopy(self.active)
        if key in self.completed:
            return None
        if len(plans) != len(deltas) or not 1 <= len(plans) <= 3:
            raise ValueError('Baseline plus at most two complete alternatives')
        if any(not feasible(copy.deepcopy(plan)) for plan in plans):
            self.last_decision = {'reason':'constituent_infeasible','key':key}
            return None
        answer = solve_table(deltas)
        if mode == 'baseline':
            weights = [Fraction(1)] + [Fraction(0)]*(len(plans)-1)
        elif mode == 'pure':
            eligible = [i for i,row in enumerate(deltas) if i and row[learned_start:]
                        and min(row)>=0 and min(row[learned_start:])>0]
            chosen = max(eligible, key=lambda i:(min(deltas[i][learned_start:]),sum(deltas[i])), default=0)
            weights = [Fraction(int(i==chosen)) for i in range(len(plans))]
        elif mode == 'mixed':
            weights = [Fraction(x) for x in answer['weights']]
        else:
            raise ValueError('mode must be baseline, pure or mixed')
        if weights[0] == 1:
            self.last_decision = {'reason':'baseline','key':key,'solution':answer}
            return None
        denominator = lcm(*(w.denominator for w in weights))
        draw = self.rng.randrange(denominator)
        self.draws += 1
        cumulative=0
        for index,weight in enumerate(weights):
            cumulative += int(weight*denominator)
            if draw < cumulative:
                break
        self.active = {'key':key,'plan':copy.deepcopy(plans[index]),'plan_index':index,
                       'weights':[str(w) for w in weights],'solution':answer,'mode':mode}
        self.last_decision = {'reason':'committed',**copy.deepcopy(self.active)}
        return copy.deepcopy(self.active)

    def transform(self, observation, configuration, base_action, *, window=None,
                  post_unit_shed=None, reservations=None, feasible=None, mode='mixed'):
        """Apply the committed lot's due sale, retaining every other field.

        A new window includes key/item/quantity/now/end/slot/plans/deltas and
        optionally learned_start. The caller supplies post-unit stock and a
        deterministic full-plan feasibility check for projected before/after
        market capacity, reserved production, dated cash and future order slots.
        Current cash separately covers all supplied operating reservations.
        """
        out=copy.deepcopy(base_action)
        now=int(observation.get('step',int(observation.get('day',0))*int((configuration or {}).get('turnsPerDay',24))+int(observation.get('hour',0))))
        cfg=configuration or {}; reserve=reservations or {}
        if self.active and now>self.active.get('completion_step',self.active['end']):
            self.completed.add(self.active['key']);self.active=None
        if self.active is None:
            if window is None or post_unit_shed is None or feasible is None:
                self.last_decision={'reason':'no_window'}
                return out
            item=window['item']; quantity=int(window['quantity'])
            cash=observation['farms'][observation['player']]['money']
            if cash < reserve.get('cash',0):
                self.last_decision={'reason':'operating_cash_reserved'};return out
            available=post_unit_shed.get(item,0)-reserve.get('stock',{}).get(item,0)
            if quantity<=0 or quantity>available:
                self.last_decision={'reason':'operating_stock_reserved'};return out
            end=int(window['end']);last=int(cfg.get('episodeSteps',720))-2
            if window['now']!=now or not now<=end<=last:
                self.last_decision={'reason':'window_dates'};return out
            def complete(plan):
                sales=plan['sales'];dates=[t for t,q in sales]
                return (sum(q for t,q in sales)==quantity and len(set(dates))==len(dates)
                        and all(isinstance(t,int) and isinstance(q,int) and now<=t<=end and q>=0 for t,q in sales)
                        and feasible(plan))
            selected=self.choose(window['key'],window['plans'],window['deltas'],feasible=complete,
                                 mode=mode,learned_start=window.get('learned_start',0))
            if selected is None:return out
            self.active.update(item=item,quantity=quantity,now=now,end=end,slot=int(window['slot']),
                               completion_step=max(t for t,q in selected['plan']['sales'] if q))
        active=self.active
        item,slot=active['item'],active['slot']
        queue=out.setdefault('market',[])
        available=(post_unit_shed or {}).get(item,0)-reserve.get('stock',{}).get(item,0)
        due=dict(active['plan']['sales']).get(now,0)
        limit=int(cfg.get('maxMarketOrdersPerTurn',10))
        # Future caller changes can invalidate the conditional projection. Keep
        # the supplied valid action and record an aborted window, never redraw.
        obstruction=(slot<0 or slot>=limit or due>available
                     or (due and slot<len(queue) and queue[slot] and not
                         (len(queue[slot])>=3 and queue[slot][:2]==['SELL',item])))
        if obstruction:
            self.last_decision={'reason':'commitment_aborted','key':active['key'],'step':now}
            self.completed.add(active['key']);self.active=None
            return out
        for index,order in enumerate(queue):
            if order and len(order)>=3 and order[:2]==['SELL',item]:
                queue[index]=[]
        if due:
            while len(queue)<=slot:queue.append([])
            queue[slot]=['SELL',item,due]
        self.last_decision={'reason':'executed','key':active['key'],'step':now,'due':due,
                            'plan_index':active['plan_index'],'weights':active['weights'],
                            'decision_step':active['now'],'expected_floor_at_commit':active['solution']['value']}
        return out
