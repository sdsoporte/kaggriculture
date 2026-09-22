# SPDX-License-Identifier: Apache-2.0
"""Observation-only rival-flow identification and contingent SELL scenarios.

No optimizer. Imports the existing source-pinned engine quote/town constants.
Own orders and identified receipts are inputs; rival private data is never read.
"""
from math import ceil

BUYABLE = {'WHEAT', 'FERTILIZER'}


def town_units(observation, configuration, start, end, shops, center_products):
    """Known consumption phases [start,end), before newly drawn shops can be used.

    For multi-turn gaps current shop copies alone are not a complete history if
    new shops were revealed in the gap. infer_rival_flow requires adjacent steps.
    """
    shop_interval=max(1,int(configuration.get('townShopSellInterval',4)))
    center_interval=max(1,int(configuration.get('townCenterSellInterval',24)))
    result={p:0 for p in observation['market']['inventory']}
    for t in range(start,end):
        if t%shop_interval==0:
            for name in observation.get('town',{}).get('unlocked_shops',[]):
                goods=shops[name]
                for p in goods: result[p]=result.get(p,0)+(2 if len(goods)==1 else 1)
        if t%center_interval==0:
            for p in center_products: result[p]=result.get(p,0)+1
    return result


def infer_rival_flow(before, after, own_orders, configuration, *, quote, shops,
                     center_products, own_sale_units=None, own_buy_units=None,
                     own_supply_units=None):
    """Identify net rival market flow, NOT hidden stock or an opponent schedule.

    Receipt maps, when supplied, must be independently identified actual counts.
    Missing map entries stay unknown (bounded by requests), not zero receipts.
    Own supply receipt counts only >1-price admissions, not cash-paying sales.
    For non-buyable products every market change is a SELL admission or town
    consumption, allowing sharper conservation/monotonic-price bounds.
    """
    start=int(before['step']);end=int(after['step'])
    if end!=start+1:
        return {'status':'unknown','reason':'nonconsecutive observations; missing own orders and shop history',
                'start':start,'end':end,'products':{}}
    cap=int(configuration.get('shedCapacity',100));slots=int(configuration.get('maxMarketOrdersPerTurn',10))
    if cap<=0 or slots<=0: raise ValueError('Positive capacity/order slots required')
    demand=town_units(before,configuration,start,end,shops,center_products)
    sells={};buys={}
    for o in own_orders[:slots]:
        if not o or len(o)<3: continue
        if o[0]=='SELL': sells[o[1]]=sells.get(o[1],0)+max(0,int(o[2]))
        if o[0]=='BUY_PRODUCT' and o[1] in BUYABLE: buys[o[1]]=buys.get(o[1],0)+max(0,int(o[2]))
    result={}
    for p,initial in before['market']['inventory'].items():
        end_before_town=after['market']['inventory'][p]+demand.get(p,0)
        net=end_before_town-initial
        gross_bound=cap*slots if p in BUYABLE else cap
        sale_max=min(sells.get(p,0),gross_bound)
        sale_exact=own_sale_units is not None and p in own_sale_units
        if sale_exact:
            sale_max=int(own_sale_units[p])
            if sale_max<0 or sale_max>min(sells.get(p,0),gross_bound): raise ValueError('Sale receipt exceeds request/capacity')
        buy_max=min(buys.get(p,0),cap*slots)
        buy_exact=own_buy_units is not None and p in own_buy_units
        buy_low=buy_max=int(own_buy_units[p]) if buy_exact else buy_max
        if not buy_exact: buy_low=0
        if buy_low<0 or buy_max>min(buys.get(p,0),cap*slots):raise ValueError('Buy receipt exceeds request/capacity')
        # For non-buyable goods inventory cannot fall during the market phase.
        no_floor=(p not in BUYABLE and net>=0 and quote(p,initial)>1 and quote(p,end_before_town)>1)
        supply_low=sale_max if sale_exact and no_floor else 0
        supply_high=sale_max
        if own_supply_units is not None and p in own_supply_units:
            supply_low=supply_high=int(own_supply_units[p])
            if supply_low<0 or supply_high>sale_max: raise ValueError('Supply receipt exceeds sale count')
        rival_net_low=net-supply_high+buy_low
        rival_net_high=net-supply_low+buy_max
        if p not in BUYABLE:
            rival_net_low=max(0,rival_net_low);rival_net_high=min(cap,rival_net_high)
        else:
            rival_net_low=max(-gross_bound,rival_net_low);rival_net_high=min(gross_bound,rival_net_high)
        consistent=rival_net_low<=rival_net_high
        if not consistent:
            result[p]={'status':'inconsistent','market_delta_plus_town':net,
                       'reason':'observations/receipts exceed physical flow bounds'}
            continue
        supply_range=[max(0,rival_net_low),min(gross_bound,rival_net_high+(gross_bound if p in BUYABLE else 0))]
        result[p]={'status':'identified_interval','market_delta_plus_town':net,'known_town_units':demand.get(p,0),
                   'own_market_supply_units_range':[supply_low,supply_high],
                   'own_buy_units_range':[buy_low,buy_max],
                   'rival_net_market_flow_range':[rival_net_low,rival_net_high],
                   'rival_market_supply_units_range':supply_range,
                   'rival_sale_units_range':supply_range if no_floor else [supply_range[0],gross_bound],
                   'floor_nonadmission_possible':not no_floor,
                   'rival_stock':'unknown','order_alignment':'unknown',
                   'gross_buy_sell_ambiguity':p in BUYABLE}
    return {'status':'identified_intervals','start':start,'end':end,'products':result,
            'identification':'conservation bounds; no hidden rival stock, order sequence, or future action inferred'}


def simulate_sell_turn(inventory, own_orders, rival_orders, own_stock, rival_stock,
                       quote, *, max_orders=10):
    """Exact pinned engine SELL-only queue transition, two pre-commit quotes/unit.

    Full queues preserve product slot alignment and duplicate orders. Supports
    empty/PASS slots. Raises for non-SELL economic actions instead of silently
    treating purchases/hiring as empty. Stocks are supplied scenario hypotheses.
    Town consumption is a separate subsequent phase.
    """
    inv=dict(inventory);stock=[dict(own_stock),dict(rival_stock)]
    if max_orders<=0 or any(any(n<0 or int(n)!=n for n in st.values()) for st in stock):
        raise ValueError('Nonnegative integer stock and positive order slots required')
    cash=[0,0];sales=[{},{}];supply=[{},{}]
    qs=[own_orders[:max_orders],rival_orders[:max_orders]]
    for q in qs:
        for o in q:
            if o and o[0] not in ('SELL','PASS'):raise ValueError('SELL-only transition; unsupported economic order '+str(o[0]))
    for slot in range(max(map(len,qs),default=0)):
        active=[]
        for q in qs:
            o=q[slot] if slot<len(q) else None
            active.append([o[1],max(0,int(o[2]))] if o and o[0]=='SELL' and len(o)>=3 and o[1] in inv else None)
        while any(a and a[1]>0 for a in active):
            quotes=[quote(a[0],inv[a[0]]) if a and a[1]>0 else None for a in active]
            for seat,a in enumerate(active):
                if not a or a[1]<=0:continue
                p=a[0]
                if stock[seat].get(p,0)<=0:
                    active[seat]=None;continue
                price=quotes[seat];stock[seat][p]-=1;a[1]-=1
                cash[seat]+=price;sales[seat][p]=sales[seat].get(p,0)+1
                if price>1:
                    inv[p]+=1;supply[seat][p]=supply[seat].get(p,0)+1
    return {'inventory':inv,'stock':stock,'cash':cash,'sale_units':sales,'market_supply_units':supply}


def supply_scenarios(observation, configuration, product, horizon, history=()):
    """Small per-product hypothesis set; no probabilities or guarantee of fills.

    History must contain ONLY completed infer_rival_flow results at/before now.
    Unknown or inconsistent history is not silently converted to measured zero.
    Competitive stock upper scenario assumes the entire current shed could hold
    this product. Per-product envelopes cannot be summed into a feasible multi-
    product rival portfolio without a shared capacity reservation by the caller.
    """
    now=int(observation['step']);last=int(configuration.get('episodeSteps',720))-2
    if horizon<now or horizon>last:raise ValueError('Horizon outside usable market window')
    cap=int(configuration.get('shedCapacity',100))
    if product not in observation['market']['inventory']:raise ValueError('Unknown product')
    rows=[]
    for record in history:
        if record['end']>now:raise ValueError('Future history cannot enter live scenario inputs')
        row=record.get('products',{}).get(product,{})
        if row.get('status')=='identified_interval' and record['end']>now-4:
            rows.append((record['start'],record['end'],row))
    # Deduplicate intervals: repeated receipt objects must not count as more flow.
    unique={}
    for start,end,row in rows:
        if (start,end) in unique and unique[start,end]!=row:raise ValueError('Conflicting history interval')
        unique[start,end]=row
    rows=[(a,b,row) for (a,b),row in sorted(unique.items())]
    base={'product':product,'probability':None,'conditional':True,'initial_rival_stock':{product:0},'orders':{},'arrivals':{}}
    scenarios=[dict(base,id='no_rival_supply',assumption='No rival SELL in this horizon; not a forecast')]
    if rows:
        duration=sum(b-a for a,b,_ in rows)
        # Persistence is of ADMITTED supply interval, used as a contingent future
        # SELL-request rate; future floor admission is recomputed, never copied.
        for index,label in ((0,'lower'),(1,'upper')):
            total=sum(r['rival_market_supply_units_range'][index] for _,_,r in rows)
            qty=min(cap,ceil(total/duration))
            orders={t:[['SELL',product,qty]] for t in range(now,horizon+1) if qty}
            # No invented future harvest: deplete one hypothetical current shed.
            scenarios.append(dict(base,id='recent_'+label,initial_rival_stock={product:cap if qty else 0},orders=orders,
                assumption='Repeat recent admitted-flow '+label+' rate as SELL requests until hypothetical current stock exhausts',
                rate_units_per_turn=qty,history_intervals=[[a,b] for a,b,_ in rows]))
    for delay in (0,1):
        scenarios.append(dict(base,id='competitive_stock_slot_'+str(delay),initial_rival_stock={product:cap},
            orders={now:[['PASS']]*delay+[['SELL',product,cap]]},
            assumption='Entire hidden shed hypothetically holds target product; sell now at slot '+str(delay)+'; no new production assumed'))
    return {'scenarios':scenarios,'history_status':'bounded_recent_flow' if rows else 'unavailable',
            'scope':'per-product conditional scenarios, not calibrated probabilities or guaranteed supply'}


def score_paired_plans(observation, configuration, own_stock, baseline_orders,
                       candidate_orders, scenario_set, horizon, *, quote, shops,
                       center_products, own_arrivals=None):
    """Score two caller-supplied feasible SELL plans against identical hypotheses.

    No plan search, schedule selection, wages, buys or production simulation.
    Dated arrivals are caller-supplied contingent deposits, shared across plans;
    physical overflow is applied. Scenario rival requests/stocks are shared but
    their actual fills/prices/floor admission are recomputed for each own plan.
    Output vector exposes both receipt changes and their difference; no scenario
    is automatically promoted or assigned a probability.
    """
    now=int(observation['step']);last=int(configuration.get('episodeSteps',720))-2
    if not now<=horizon<=last:raise ValueError('Horizon outside usable market window')
    cap=int(configuration.get('shedCapacity',100));slots=int(configuration.get('maxMarketOrdersPerTurn',10))
    def keyed(mapping):return {int(k):v for k,v in (mapping or {}).items()}
    def rollout(plan,scenario):
        plan=keyed(plan);arrivals=keyed(own_arrivals);rivals=keyed(scenario['orders']);rival_arrivals=keyed(scenario.get('arrivals'))
        if any(t<now or t>horizon for m in (plan,arrivals,rivals,rival_arrivals) for t in m):raise ValueError('Dated input outside horizon')
        inv=dict(observation['market']['inventory']);stocks=[dict(own_stock),dict(scenario['initial_rival_stock'])]
        if any(any(n<0 or int(n)!=n for n in st.values()) or sum(st.values())>cap for st in stocks):raise ValueError('Initial stock exceeds physical capacity')
        cash=[0,0];discarded=[0,0];timeline=[]
        for t in range(now,horizon+1):
            for seat,mapping in enumerate((arrivals,rival_arrivals)):
                for p,n in mapping.get(t,{}).items():
                    if n<0 or int(n)!=n:raise ValueError('Nonnegative integer arrivals required')
                    take=min(n,max(0,cap-sum(stocks[seat].values())))
                    stocks[seat][p]=stocks[seat].get(p,0)+take;discarded[seat]+=n-take
            r=simulate_sell_turn(inv,plan.get(t,[]),rivals.get(t,[]),stocks[0],stocks[1],quote,max_orders=slots)
            inv=r['inventory'];stocks=r['stock'];cash=[cash[s]+r['cash'][s] for s in (0,1)]
            demand=town_units(observation,configuration,t,t+1,shops,center_products)
            for p,n in demand.items():inv[p]-=n
            timeline.append({'step':t,'cash':r['cash'],'sale_units':r['sale_units'],'market_supply_units':r['market_supply_units'],'town_units':demand})
        return {'cash':cash,'terminal_stock':stocks,'discarded':discarded,'timeline':timeline}
    vector=[]
    for scenario in scenario_set['scenarios']:
        b=rollout(baseline_orders,scenario);c=rollout(candidate_orders,scenario)
        own=c['cash'][0]-b['cash'][0];rival=c['cash'][1]-b['cash'][1]
        vector.append({'scenario':scenario['id'],'probability':None,'baseline':b,'candidate':c,
                       'own_cash_receipt_delta':own,'rival_cash_receipt_delta':rival,
                       'game_cash_margin_delta':own-rival})
    return {'evaluations':vector,'selection':None,'claim':'conditional receipt differences over supplied horizon; not terminal game forecasts'}
