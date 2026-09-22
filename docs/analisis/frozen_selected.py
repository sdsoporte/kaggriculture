# SPDX-License-Identifier: Apache-2.0
"""Frozen SELL method with selected base parameter; no second parent call.

Generated mechanically from scheduler.py SHA256 32c8610c9827d1686a6f831e2c4b6af4c00d32d2aa04dcf25699d976d6d97dd9.
The selected-action boundary also optionally exposes its exact unit snapshot.
Original scheduler and sale valuation remain intact.
"""
from scheduler import *
from seller_snapshot import seller_public_observation
from selected_sell_core import optimize_lot

class FrozenSelected(SellScheduler):
    def transform(self, obs, config, base):
        config=dict(config or {});now=int(obs['step']);last=int(config.get('episodeSteps',720))-2
        self.observe(obs)
        farm,private=post_units(obs,base,config)
        if (getattr(self, 'capture_post_units', False)
                or (getattr(self, 'capture_operating_stock', False)
                    and any(o and len(o) > 2 and o[:2] == ['SELL', 'FERTILIZER']
                            for o in base.get('market', [])))):
            self.selected_post_units = (copy.deepcopy(farm), copy.deepcopy(private))
            self.selected_post_units_binding = (
                now, int(obs['player']), copy.deepcopy(base['farmer']),
                copy.deepcopy(base.get('hands', [])))
        shed=private['shed'];self.diagnostics={'step':now,'evaluations':[]}
        # Operating WHEAT/FERTILIZER and animal stock remain baseline-controlled.
        if now==last:
            out=copy.deepcopy(base)
            out['market']=parent._terminal_settlement(shed,obs['market']['prices'],out['market'])
            self.pending={};self.previous=seller_public_observation(obs);return out
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
        self.previous=seller_public_observation(obs)
        return out
