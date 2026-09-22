# SPDX-License-Identifier: Apache-2.0
"""Bounded, scenario-based finite-horizon SELL execution over unchanged Arlene.

Plans integer tranches, executes the first, then replans from observations.
The production controller and its operating-resource trades are unchanged.
Scenario values include the other seller's cash, not only our own receipts.
"""
from __future__ import annotations
import copy
import importlib.util
import math
from pathlib import Path
from functools import lru_cache
import mechanics as m
from observed_clone import detached_json_value

HERE = Path(__file__).resolve().parent

def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

parent = _load('intact_arlene', HERE/'reference/next-panel/vendor/arlene.py')
receipt_math = _load('pinned_receipt_math', HERE/'reference/decision/decision.py')
PRODUCTS = tuple(p for p in m.PRODUCTS if p not in ('WHEAT','FERTILIZER'))
HORIZON = 8
MAX_PLANS = 700


def post_units(obs, action, config):
    """Exact deterministic engine unit stage on the player's observed farm."""
    farm = detached_json_value(obs['farms'][obs['player']])
    private = detached_json_value(obs['private'])
    acts = [action.get('farmer',['PASS']), *action.get('hands',[])]
    demand = {}
    for a in acts:
        if a and a[0]=='PLANT' and len(a)>1:
            demand[a[1]]=demand.get(a[1],0)+1
    blocked={p for p,n in demand.items() if n>private['seeds'].get(p,0)}
    for i,a in enumerate(acts):
        if a and a[0]=='PLANT' and a[1] in blocked:a=['PASS']
        m._apply_unit_action(farm,private,i,a,len(farm['tiles']),int(obs['step'])//int(config.get('turnsPerDay',24)),int(config.get('turnsPerDay',24)),int(config.get('shedCapacity',100)))
    return farm, private


def absorption(item, step, shops, config):
    n=0
    if step % int(config.get('townShopSellInterval',4))==0:
        for shop in shops:
            products=m.SHOPS.get(shop,())
            if item in products:n+=2 if len(products)==1 else 1
    if item!='FERTILIZER' and step % int(config.get('townCenterSellInterval',24))==0:n+=1
    return n


class MarketPath:
    """Exact rounded, floor-admitting paired units for conditional sale streams."""
    def __init__(self, item, inventory, params, shops, config, now, end):
        self.item,self.inventory,self.params=item,inventory,params
        self.shops,self.config,self.now,self.end=shops,config,now,end
        self.quote=lru_cache(maxsize=2048)(lambda inv:m.market_price(item,inv,params))
        self.single=lru_cache(maxsize=8192)(self._single)
        self.joint=lru_cache(maxsize=8192)(self._joint)

    def _single(self, inv, quantity):
        # Reuse SORREL's repaired receipt interface; admission is derived from
        # the same exact quote condition, not raw quantity added as supply.
        cash=receipt_math.sale_receipts([{'step':0,'quantity':quantity,'product':self.item}],lambda _p,_t:inv,lambda _p,i:self.quote(int(i)),0)
        initial=inv
        for _ in range(quantity):
            if self.quote(inv)>1:inv+=1
            else:break
        return int(cash),inv

    def _joint(self, inv, own, rival, alignment):
        if alignment=='after':
            cash,inv=self.single(inv,own)
            other,inv=self.single(inv,rival)
            return cash,other,inv
        if alignment=='before':
            other,inv=self.single(inv,rival)
            cash,inv=self.single(inv,own)
            return cash,other,inv
        cash=other=0
        for k in range(max(own,rival)):
            price=self.quote(inv)
            a=k<own;b=k<rival
            cash+=price*a;other+=price*b
            if price>1:inv+=int(a)+int(b)
        return cash,other,inv

    def score(self, plan, quantity, rival, alignment, terminal=False):
        inv=self.inventory;own_cash=other_cash=0;sold=0
        orders=dict(plan)
        for step in range(self.now,self.end+1):
            q=min(quantity-sold,max(0,orders.get(step,0)))
            r=(dict(rival).get(step,0) if isinstance(rival,tuple) else rival if step==self.now else 0)
            a,b,inv=self.joint(inv,q,r,alignment)
            own_cash+=a;other_cash+=b;sold+=q
            inv-=absorption(self.item,step,self.shops,self.config)
        remaining=quantity-sold
        # Artificial planning boundaries retain inventory at a conservative
        # continuation value. No mandatory horizon-end liquidation constraint.
        carry=0.0
        if remaining and not terminal:
            carry=float(self.single(inv,remaining)[0])
        return own_cash+carry-other_cash, own_cash,other_cash,remaining


def optimize_lot(*,item,quantity,inventory,params,shops,config,now,dates,
                 reference,rival_quantity,minimum_now=0,capacity_ok=None,last=718):
    end=dates[-1]
    model=MarketPath(item,inventory,params,shops,config,now,end)
    scenarios=[('no_rival',0,'paired'),('observed_paired',rival_quantity,'paired'),('observed_later_order',rival_quantity,'after')]
    if end>now:
        scenarios.append(('observed_next_turn',((now+1,rival_quantity),),'paired'))
    if end>now+2:
        scenarios.append(('observed_before_delayed_batch',((end-1,rival_quantity),),'paired'))
    baseline=[model.score(reference,quantity,r,a,end==last) for _,r,a in scenarios]
    reference_feasible=(capacity_ok(reference) if capacity_ok else True) and dict(reference).get(now,0)>=minimum_now
    best_plan=tuple(reference);best_key=(0.0,0.0,0.0) if reference_feasible else (-float('inf'),-float('inf'),0.0);best_scores=baseline
    found_feasible=reference_feasible
    candidates={tuple(reference)}
    future=dates[1:]
    for first in range(minimum_now,quantity+1):
        remaining=quantity-first
        candidates.add(((now,first),))  # Explicit carry beyond short horizon.
        for date in future:
            candidates.add(((now,first),(date,remaining)))
        if len(future)>=2:
            for share in (1,2,3):
                a=remaining*share//4
                candidates.add(((now,first),(future[0],a),(future[-1],remaining-a)))
    # Enumerates every legal first quantity and a bounded later-tranche family.
    for plan in sorted(candidates):
        if sum(q for _,q in plan)>quantity:continue
        if dict(plan).get(now,0)<minimum_now:continue
        # With a feasible incumbent, a nonpositive no-rival difference cannot
        # meet the existing strict all-scenario improvement rule. Reject that
        # economic loser before the more expensive physical ledger callback.
        # The forced-feasibility path keeps the original evaluation order.
        if reference_feasible:
            first_score=model.score(plan,quantity,0,'paired',end==last)
            if first_score[0]-baseline[0][0] <= 0:
                continue
            if capacity_ok and not capacity_ok(plan):continue
            scores=[first_score]
            competitive=True
            for (_,r,a),b in zip(scenarios[1:],baseline[1:]):
                score=model.score(plan,quantity,r,a,end==last)
                if score[0]-b[0] <= 0:
                    competitive=False
                    break
                scores.append(score)
            if not competitive:continue
        else:
            if capacity_ok and not capacity_ok(plan):continue
            scores=[model.score(plan,quantity,r,a,end==last) for _,r,a in scenarios]
        deltas=[s[0]-b[0] for s,b in zip(scores,baseline)]
        key=(round(min(deltas),8),round(sum(deltas),8),float(dict(plan).get(now,0)))
        # Require improvement in every explicit scenario; ties preserve reference.
        if (key[0]>0 or not reference_feasible) and key>best_key:
            best_key,best_plan,best_scores=key,plan,scores
            found_feasible=True
    return best_plan,{'item':item,'quantity':quantity,'rival_scenario_quantity':rival_quantity,
        'reference':list(reference),'plan':list(best_plan),'minimum_now':minimum_now,
        'scenarios':{name:{'reference_relative_value':b[0],'relative_value':s[0],
                          'own_receipts':s[1],'rival_receipts':s[2],'carry_units':s[3]}
                     for (name,_,_),b,s in zip(scenarios,baseline,best_scores)},
        'worst_relative_gain':best_key[0] if found_feasible else 0.0,'forced_feasibility':not reference_feasible and found_feasible,
        'feasible':found_feasible,'plans_evaluated':len(candidates)}


def _order_spend(order, farm, inventory, params, hires, config):
    if not order:return 0,hires
    op=order[0]
    if op=='HIRE':return m._hire_cost(hires,int(config.get('farmHandCostMult',1))),hires+1
    if op=='BUY_LAND':
        i=len(farm['unlocked_quadrants'])-1
        return (m.LAND_PRICES[i] if i<len(m.LAND_PRICES) else 0),hires
    if len(order)<3:return 0,hires
    item,n=order[1],max(0,int(order[2]))
    if op=='BUY_SEED':return m.CROPS[item]['seed']*n,hires
    if op=='BUY_ANIMAL':return m.ANIMALS[item]['cost']*n,hires
    if op=='BUY_PRODUCT':
        # Conservative quote for all units under known consumption before use.
        return n*m.market_price(item,inventory[item]-n-32,params),hires
    return 0,hires


class SellScheduler:
    def __init__(self, mode='candidate'):
        self.controller=parent.Agent();self.mode=mode;self.pending={};self.planned={}
        self.previous=None;self.observed_harvests={};self.diagnostics={}

    def rival_supply(self, obs, item):
        """Scenario magnitude only, derived from public standing/harvested yield."""
        rival=obs['farms'][1-int(obs['player'])]
        visible=0
        for row in rival['tiles']:
            for tile in row:
                if not isinstance(tile,dict):continue
                product=tile.get('crop') if tile.get('kind')=='PLANT' else m.ANIMALS.get(tile.get('animal'),{}).get('product')
                if product==item:visible+=max(0,int(tile.get('yield_units',0)))
        recent=sum(n for t,n in self.observed_harvests.get(item,[]) if int(obs['step'])-t<=8)
        # Harvested goods may have sold already; this is a named stress scenario,
        # not private stock or a calibrated probability/point forecast.
        return min(100,max(visible,recent))

    def observe(self, obs):
        now=int(obs['step'])
        if self.previous is not None:
            old=self.previous['farms'][1-int(obs['player'])]['tiles']
            new=obs['farms'][1-int(obs['player'])]['tiles']
            for y,row in enumerate(old):
                for x,tile in enumerate(row):
                    if not isinstance(tile,dict):continue
                    product=tile.get('crop') if tile.get('kind')=='PLANT' else m.ANIMALS.get(tile.get('animal'),{}).get('product')
                    if product not in PRODUCTS:continue
                    later=new[y][x]
                    a=max(0,int(tile.get('yield_units',0)))
                    b=max(0,int(later.get('yield_units',0))) if isinstance(later,dict) else 0
                    if a>b:self.observed_harvests.setdefault(product,[]).append((now,a-b))
        for p in self.observed_harvests:
            self.observed_harvests[p]=[(t,n) for t,n in self.observed_harvests[p] if now-t<=8]

    def cash_reserve(self, obs, config, base, end):
        now=int(obs['step']);farm=dict(obs['farms'][obs['player']])
        farm['unlocked_quadrants']=list(farm['unlocked_quadrants'])
        hires=int(farm['hires_today']);cost=0
        route=self.controller.R[self.controller.cur]
        for t in range(now,end+1):
            if t>now and t%24==0:hires=0
            orders=base['market'] if t==now else (route[t].get('market',[]) if t<len(route) else [])
            for order in orders:
                n,hires=_order_spend(order,farm,obs['market']['inventory'],obs['market'].get('params'),hires,config);cost+=n
                if order and order[0]=='BUY_LAND' and len(farm['unlocked_quadrants'])<=len(m.LAND_ORDER):
                    farm['unlocked_quadrants'].append(m.LAND_ORDER[len(farm['unlocked_quadrants'])-1])
        return cost

    def receipt_profile(self, obs, base, farm, private, end, item, config):
        """Conditional exact own-unit deposits, bounded to the current day.

        Follow the unchanged current tape, no RNG or new shops. Enlarging the
        projection shed records requested arrivals so overflow cannot disappear
        from feasibility. Other current sells and later tape sells release room.
        """
        now=int(obs['step']);cap=int(config.get('shedCapacity',100))
        f,p=copy.deepcopy(farm),copy.deepcopy(private)
        for o in base['market']:
            if o and o[0]=='SELL' and len(o)>2 and o[1]!=item:
                p['shed'][o[1]]=max(0,p['shed'].get(o[1],0)-int(o[2]))
        profile=[]
        route=self.controller.R[self.controller.cur]
        for t in range(now,end+1):
            if t>now:
                act=route[t] if t<len(route) else parent.PASS
                acts=[act.get('farmer',['PASS']),*act.get('hands',[])]
                for i,a in enumerate(acts):
                    m._apply_unit_action(f,p,i,a,len(f['tiles']),t//24,24,10**6)
            # Before market: arrivals cannot be rescued by this turn's sale.
            profile.append((t,'before',sum(p['shed'].values())))
            orders=base['market'] if t==now else (route[t].get('market',[]) if t<len(route) else [])
            for o in orders:
                if not o:continue
                if o[0]=='SELL' and t>now and o[1]!=item:
                    p['shed'][o[1]]=max(0,p['shed'].get(o[1],0)-int(o[2]))
                elif o[0] in ('BUY_PRODUCT','BUY_ANIMAL') and len(o)>2:
                    p['shed'][o[1]]=p['shed'].get(o[1],0)+int(o[2])
                elif o[0]=='HIRE':
                    f['hands'].append(m._spawn_hand(f,len(f['tiles'])));p['inventories'].append({})
            if t%24==23:
                m._drop_inventories_to_shed(p,10**6)
                profile.append((t,'after',sum(p['shed'].values())))
                break
            profile.append((t,'after',sum(p['shed'].values())))
        def feasible(plan):
            sold=0;orders=dict(plan)
            for t,phase,total in profile:
                if phase=='after':sold+=orders.get(t,0)
                if t==now and phase=='before':continue
                if total-sold>cap-1:return False
            return True
        return feasible

    def act(self, obs, config=None):
        config=dict(config or {});now=int(obs['step']);last=int(config.get('episodeSteps',720))-2
        self.observe(obs)
        base=self.controller.act(obs)
        farm,private=post_units(obs,base,config)
        shed=private['shed'];self.diagnostics={'step':now,'evaluations':[]}
        # Operating WHEAT/FERTILIZER and animal stock remain baseline-controlled.
        if now==last:
            out=copy.deepcopy(base)
            out['market']=parent._terminal_settlement(shed,obs['market']['prices'],out['market'])
            self.pending={};self.previous=copy.deepcopy(obs);return out
        end=min(now+HORIZON,last,(now//24+1)*24-1)
        # Future controller branch changes are not predicted.
        for checkpoint,*_ in parent.DECISIONS:
            if now<checkpoint<=end:end=checkpoint-1
        shops=obs.get('town',{}).get('unlocked_shops',[])
        dates=[now]+[t for t in range(now+1,end+1) if any(absorption(p,t-1,shops,config) for p in PRODUCTS)]
        if len(dates)>3:dates=dates[:2]+dates[-1:]
        if dates[-1]!=end:dates.append(end)
        dates=sorted(set(dates))
        baseline_q={}
        for o in base['market']:
            if o and o[0]=='SELL' and len(o)>2 and o[1] in PRODUCTS:
                baseline_q[o[1]]=baseline_q.get(o[1],0)+max(0,int(o[2]))
        targets={p:max(0,int(shed.get(p,0))) for p in PRODUCTS if shed.get(p,0)>0}
        current={p:min(targets[p],baseline_q.get(p,0)+sum(q for t,q in self.planned.get(p,[]) if t<=now)) for p in targets}
        budget=self.cash_reserve(obs,config,base,end)
        best=None
        for item,quantity in targets.items():
            if quantity<=0:continue
            reference=[(now,current[item])]
            rem=quantity-current[item]
            pending_future=[(max(now,t),q) for t,q in self.planned.get(item,[]) if t>now]
            for t,q in pending_future:
                q=min(rem,q)
                if q>0:reference.append((min(t,end),q));rem-=q
            route=self.controller.R[self.controller.cur]
            for t in range(now+1,end+1):
                for order in route[t].get('market',[]) if t<len(route) else []:
                    if order and order[0]=='SELL' and order[1]==item and rem>0:
                        q=min(rem,max(0,int(order[2])));reference.append((t,q));rem-=q
            # Remaining stock keeps a continuation value; no artificial liquidation.
            reference=tuple((t,sum(q for d,q in reference if d==t)) for t in sorted({t for t,_ in reference}))
            if self.mode=='naive':
                take=min(quantity,6)
                if farm['money']<budget or now%24==23:take=max(take,current[item])
                if take!=current[item]:
                    info={'item':item,'quantity':quantity,'worst_relative_gain':0,'plan':[(now,take),(min(last,now+1),quantity-take)]}
                    best=(item,tuple(info['plan']),info);break
                continue
            if len(dates)<2:continue
            minimum=current[item] if farm['money']<budget else 0
            receipt_feasible=self.receipt_profile(obs,base,farm,private,end,item,config)
            route=self.controller.R[self.controller.cur]
            def feasible(plan):
                for t,q in plan:
                    if q<=0:continue
                    orders=base['market'] if t==now else route[t].get('market',[]) if t<len(route) else []
                    if len(orders)>=int(config.get('maxMarketOrdersPerTurn',10)):
                        offered=sum(max(0,int(o[2])) for o in orders if o and o[0]=='SELL' and o[1]==item)
                        if q>offered:return False
                return receipt_feasible(plan)
            plan,info=optimize_lot(item=item,quantity=quantity,inventory=int(obs['market']['inventory'][item]),params=obs['market'].get('params'),shops=shops,config=config,now=now,dates=dates,reference=reference,rival_quantity=self.rival_supply(obs,item),minimum_now=minimum,capacity_ok=feasible,last=last)
            self.diagnostics['evaluations'].append(info)
            eligible=info['worst_relative_gain']>0 or info.get('forced_feasibility',False)
            rank=(info.get('forced_feasibility',False),info['worst_relative_gain'])
            if eligible and (best is None or rank>(best[2].get('forced_feasibility',False),best[2]['worst_relative_gain'])):best=(item,plan,info)
        if best:
            item,plan,info=best;current[item]=dict(plan).get(now,0)
            self.planned[item]=[(t,q) for t,q in plan if t>now and q>0]
            self.diagnostics['chosen']=info
        out=copy.deepcopy(base);market=[];remaining=dict(current)
        available=dict(shed)
        # Preserve every original order index, including withheld SELL positions.
        # Extra stock is offered only after inherited orders: never consolidate a
        # later SELL ahead of a cash-dependent purchase or shift its rival pairing.
        for raw in out['market']:
            o=list(raw)
            if o and o[0]=='SELL' and len(o)>2 and o[1] in targets:
                item=o[1]
                q=min(max(0,int(o[2])),remaining.get(item,0),max(0,available.get(item,0)))
                remaining[item]=remaining.get(item,0)-q;available[item]=available.get(item,0)-q
                market.append(['SELL',item,q] if q else [])
            else:market.append(o)
        for item in sorted(targets):
            q=min(remaining.get(item,0),max(0,available.get(item,0)))
            if q>0 and len(market)<int(config.get('maxMarketOrdersPerTurn',10)):
                market.append(['SELL',item,q]);available[item]=available.get(item,0)-q
        out['market']=market
        for item,q in targets.items():
            sold=sum(o[2] for o in out['market'] if o and o[0]=='SELL' and o[1]==item)
            self.pending[item]=max(0,q-sold)
            if not self.pending[item]:self.planned.pop(item,None)
        self.previous=copy.deepcopy(obs)
        return out


_INSTANCE=None

def agent(obs, configuration=None):
    global _INSTANCE
    if _INSTANCE is None or int(obs.get('step',0))==0:_INSTANCE=SellScheduler()
    return _INSTANCE.act(obs,configuration)

_NAIVE=None

def naive_agent(obs, configuration=None):
    global _NAIVE
    if _NAIVE is None or int(obs.get('step',0))==0:_NAIVE=SellScheduler('naive')
    return _NAIVE.act(obs,configuration)
