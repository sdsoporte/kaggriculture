# SPDX-License-Identifier: MIT
"""Observation-only composition contract; no scheduler, quote or parent call.

Producers supply their complete current errand snapshots. Their executable plans
own arrival dates and attribution of observed carried stock. This module checks
shared reservations and exports contingent capacity obligations, never sale lots.
"""
from collections import defaultdict


class ContractError(ValueError):
    """Caller must retain its safe selected action when contracts conflict."""


def _count(value, label):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ContractError(f'{label} must be a nonnegative integer')
    return value


def build_arrival_contract(observation, configuration, selected_action, snapshots):
    """Return phase-aware pending reservations from current producer snapshots.

    Snapshot: {owner, observed_step, plans:[...]}; omission means no current plans
    for that producer, not permission to reuse an older snapshot.

    Plan: {errand_id, worker_index, target:[x,y], product, units_total,
      units_incremental, arrival_step, arrival_kind, no_forced_sale_date:true,
      status:pending|carried|aborted, observed_carried_units:0,
      hire_order_index?:int, deposit_action?:[DROP]|[PLACE,product,quantity]}.

    observed_carried_units must be attributed by the producer from actual
    execution/observations. Current inventory alone cannot identify its origin.
    We validate that all such allocations fit the current observed inventories.
    A new HIRE is contingent until next observation confirms it; its exact index
    in the selected action is required for a worker that does not exist yet.

    pending_units = units_total - observed_carried_units. Existing carried stock
    is exported separately and must be handled once by the caller's own-state
    projection. Aborted plans reserve nothing. Stale/expired/conflicting plans
    raise ContractError; no guessed arrival or hidden stock is substituted.
    """
    now = _count(observation['step'], 'step')
    tpd = _count(configuration.get('turnsPerDay', 24), 'turnsPerDay')
    if not tpd:
        raise ContractError('turnsPerDay must be positive')
    last = _count(configuration.get('episodeSteps', 720), 'episodeSteps') - 2
    seat = observation['player'];farm = observation['farms'][seat]
    positions = [farm['farmer'], *farm.get('hands', [])]
    inventories = observation['private'].get('inventories', [])
    orders = selected_action.get('market', [])
    hires = [i for i,o in enumerate(orders) if o and o[0] == 'HIRE']
    seen_owners=set();seen_ids=set();workers={};targets={}
    allocations=defaultdict(int);events=[];carried=[];aborted=[]
    for snapshot in snapshots:
        owner=snapshot['owner']
        if not isinstance(owner,str) or not owner or owner in seen_owners:
            raise ContractError('Producer owners must be unique nonempty strings')
        seen_owners.add(owner)
        if snapshot['observed_step'] != now:
            raise ContractError(f'Stale producer snapshot: {owner}')
        for plan in snapshot['plans']:
            eid=plan['errand_id'];key=(owner,eid)
            if not isinstance(eid,str) or not eid or key in seen_ids:
                raise ContractError('Errand IDs must be unique within each producer')
            seen_ids.add(key)
            status=plan['status']
            if status == 'aborted':
                aborted.append({'owner':owner,'errand_id':eid});continue
            if status not in ('pending','carried'):
                raise ContractError('Unrecognized errand lifecycle state')
            worker=_count(plan['worker_index'],'worker_index')
            total=_count(plan['units_total'],'units_total')
            incremental=_count(plan['units_incremental'],'units_incremental')
            realized=_count(plan.get('observed_carried_units',0),'observed_carried_units')
            if incremental>total or realized>total:
                raise ContractError('Incremental/realized units cannot exceed total harvest')
            if status=='carried' and realized!=total:
                raise ContractError('Carried state requires observed realization of the whole lot')
            if plan['no_forced_sale_date'] is not True:
                raise ContractError('A capacity arrival must not impose a sale deadline')
            product=plan['product']
            if not isinstance(product,str) or not product:
                raise ContractError('A product identifier is required')
            pending=total-realized
            if worker>=len(positions):
                slot=plan.get('hire_order_index')
                if slot not in hires or worker!=len(positions)+hires.index(slot) or realized:
                    raise ContractError('Unobserved worker needs its exact contingent HIRE slot')
            allocations[(worker,product)]+=realized
            if realized:
                carried.append({'owner':owner,'errand_id':eid,'worker_index':worker,
                                'product':product,'units':realized,'already_in_observation':True})
            if worker in workers:
                raise ContractError(f'Worker already reserved by {workers[worker]}')
            workers[worker]=key
            if not pending:
                continue
            target=tuple(plan['target'])
            if len(target)!=2 or any(isinstance(v,bool) or not isinstance(v,int) for v in target):
                raise ContractError('Target must be an integer coordinate pair')
            x,y=target
            if not (0<=y<len(farm['tiles']) and 0<=x<len(farm['tiles'][y])):
                raise ContractError('Target outside observed board')
            if target in targets:
                raise ContractError(f'Target already reserved by {targets[target]}')
            targets[target]=key
            arrival=_count(plan['arrival_step'],'arrival_step')
            kind=plan['arrival_kind']
            if arrival<now:
                raise ContractError('Pending arrival is overdue; producer must reconcile it')
            if kind=='eod_auto':
                if arrival!=(now//tpd+1)*tpd-1:
                    raise ContractError('EOD obligation must target this observed day close')
                phase='after_market'
            elif kind=='worker_deposit':
                op=plan.get('deposit_action',[])
                if op!=['DROP'] and not (len(op)==3 and op[0]=='PLACE' and op[1]==product
                                          and _count(op[2],'deposit quantity')>=pending):
                    raise ContractError('Worker deposit requires an explicit DROP/PLACE plan action')
                if arrival==now:
                    issued=[selected_action.get('farmer',['PASS']),*selected_action.get('hands',[])]
                    if worker>=len(issued) or issued[worker]!=op:
                        raise ContractError('Current deposit is absent from selected unit actions')
                phase='before_market'
            else:
                raise ContractError('Unknown arrival kind; do not infer travel-only deposits')
            first_sale=arrival+(phase=='after_market')
            events.append({'owner':owner,'errand_id':eid,'worker_index':worker,
                'target':list(target),'product':product,'step':arrival,'phase':phase,
                'pending_capacity_units':pending,'units_total':total,
                'units_incremental':incremental,'contingent':True,
                'guaranteed_stock_units':0,'no_forced_sale_date':True,
                'first_possible_sale_step':first_sale,'sale_window_available':first_sale<=last})
    for (worker,product),quantity in allocations.items():
        observed=inventories[worker].get(product,0) if worker<len(inventories) else 0
        if quantity>observed:
            raise ContractError('Attributed carried quantities exceed current observed stock')
    return {'version':1,'observed_step':now,'capacity_events':events,
            'realized_carried':carried,'aborted':aborted,
            'worker_reservations':[{'worker_index':w,'owner':key[0],'errand_id':key[1]}
                                   for w,key in workers.items()],
            'sale_lots':[], 'guaranteed_future_output_units':0,
            'boundary':'Producer plan obligations are contingent; observed stock is separate.'}


def pending_capacity(contract, step, phase):
    """Cumulative extra capacity by product; no conversion to guaranteed output."""
    if phase not in ('before_market','after_market'):
        raise ContractError('Unknown market phase')
    out=defaultdict(int)
    for e in contract['capacity_events']:
        if e['step']<step or (e['step']==step and
                (e['phase']=='before_market' or phase=='after_market')):
            out[e['product']]+=e['pending_capacity_units']
    return dict(out)
