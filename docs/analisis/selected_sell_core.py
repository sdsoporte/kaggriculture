# SPDX-License-Identifier: Apache-2.0
"""Pure market optimizer extracted from frozen scheduler32c8610c; no parent code.

The selected standalone scheduler is preserved separately.
"""
from functools import lru_cache
from pathlib import Path
import importlib.util
import mechanics as m

_spec = importlib.util.spec_from_file_location("selected_sell_receipts", Path(__file__).resolve().parent / "reference/decision/decision.py")
receipt_math = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(receipt_math)

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
        steps=range(self.now,self.end+1)
        consumed=()
        if steps:
            rival_orders=dict(rival) if isinstance(rival,tuple) else {self.now:rival}
            # A model scores many plans against the same dated town consumption.
            # Key the observable inputs, so edits between calls invalidate the
            # schedule rather than inheriting stale shops or configuration.
            context=(self.item,self.now,self.end,tuple(self.shops),
                     self.config.get('townShopSellInterval',4),
                     self.config.get('townCenterSellInterval',24))
            if getattr(self,'_score_context',None)!=context:
                consumed=tuple(absorption(self.item,t,self.shops,self.config)
                               for t in steps)
                self._score_context=context
                self._score_consumed=consumed
            else:
                consumed=self._score_consumed
        for step,used in zip(steps,consumed):
            q=min(quantity-sold,max(0,orders.get(step,0)))
            r=rival_orders.get(step,0)
            a,b,inv=self.joint(inv,q,r,alignment)
            own_cash+=a;other_cash+=b;sold+=q
            inv-=used
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
