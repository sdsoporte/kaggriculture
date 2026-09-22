# SPDX-License-Identifier: Apache-2.0
"""Bounded route improvements over an existing selected production controller.

Uses own observed state and authored commitments. Market valuation is a current
quote screen, not a forecast. No investment, hiring or input orders are added.
An idle fertilizer delivery may append one sale with its own deposit.
"""
from copy import deepcopy
from itertools import permutations

MOVES={'NORTH':(0,-1),'SOUTH':(0,1),'EAST':(1,0),'WEST':(-1,0)}
WORK={'WATER','CARE','FEED','HARVEST','COLLECT_FERTILIZER'}
CHECKPOINTS=(226,360,433)

def unit(row,i):
    return row.get('farmer',['PASS']) if i==0 else (row.get('hands',[])[i-1] if i<=len(row.get('hands',[])) else ['PASS'])

def set_unit(row,i,action):
    if i==0:row['farmer']=action
    else:
        hands=row.setdefault('hands',[])
        while len(hands)<i:hands.append(['PASS'])
        hands[i-1]=action

def move(pos,action,board):
    d=MOVES.get(action[0] if action else '')
    if d:
        q=(pos[0]+d[0],pos[1]+d[1])
        if 0<=q[0]<board and 0<=q[1]<board:return q
    return pos

def path(a,b):
    return [[('EAST' if b[0]>a[0] else 'WEST')]]*abs(b[0]-a[0])+[[('SOUTH' if b[1]>a[1] else 'NORTH')]]*abs(b[1]-a[1])

def distance(a,b):return abs(a[0]-b[0])+abs(a[1]-b[1])

class SpatialTempo:
    def __init__(self,mechanics,pathing=True,tempo=True,seed_reserve=None,idle_fertilizer=False):
        self.m=mechanics;self.pathing=pathing;self.tempo=tempo
        self.events=[];self.edits=[];self.active={};self.reserved=set();self.day=None
        self.plans={};self._committed=None;self._pending=None;self._selected=None
        self.supported=True
        self.idle_supported=True
        self.seed_reserve=seed_reserve
        self.idle_fertilizer=idle_fertilizer
        self.sale_obligation=None
        self.receipt_events=[]
        self._sale_proposal=None

    def configure(self, configuration):
        cfg=configuration or {}
        self.supported=(int(cfg.get('turnsPerDay',24))==24
                        and int(cfg.get('episodeSteps',720))==720
                        and int(cfg.get('shedCapacity',100))==100)
        self.idle_supported=cfg.get('maxMarketOrdersPerTurn',10)==10

    def finish(self, observation, returned_action, post=None):
        """Commit only after TitanAgent returns, including its selected fallback.

        Retain this object across controller reconstruction and install it on the
        replacement controller. A cancelled producer never commits a proposal.
        Market-only fallback changes do not invalidate completed unit actions.
        """
        count=1+len(observation['farms'][observation['player']]['hands'])
        now=int(observation['step'])
        self.observe_owned_stock(observation)
        proposal=self._sale_proposal
        if (proposal is not None and proposal['step']==now
                and returned_action.get('market',[])[proposal['slot']:proposal['slot']+1]
                    ==[['SELL','FERTILIZER',1]]
                and (proposal.get('worker') is None
                     or unit(returned_action,proposal['worker'])==['DROP'])):
            self.sale_obligation=dict(proposal,status='awaiting_observed_fill',
                                      player=int(observation['player']))
        p=self.sale_obligation
        if p is not None and p['status']=='observed_deposit':
            # This retained unit is fungible. Bind potential consumption to the
            # actual return even when no completed post-unit snapshot exists.
            actions=[unit(returned_action,i) for i in range(count)]
            touched=any(a and (a[0]=='FERTILIZE' or a[:2]==['PICKUP','FERTILIZER'])
                        for a in actions)
            touched=touched or any(a and len(a)>1 and a[1]=='FERTILIZER'
                and a[0] in ('SELL','BUY_PRODUCT') for a in returned_action.get('market',[])[:10])
            if touched:self.sale_obligation=dict(p,status='stock_unknown')
            p=self.sale_obligation
        if p is not None and p['status']=='carried' and now%24==23:
            # Own units finish before market; EOD auto-deposits after market.
            # Give no credit to sales when certifying that every carry fits.
            if post is None and all(unit(returned_action,i)==['PASS'] for i in range(count)):
                post=observation
            if post is not None:
                inv=post['private']['inventories'];shed=post['private']['shed']
                buys=sum(max(0,int(a[2])) for a in returned_action.get('market',[])[:10]
                         if a and len(a)>2 and a[0] in ('BUY_PRODUCT','BUY_ANIMAL'))
                if (p['worker']<len(inv) and inv[p['worker']].get('FERTILIZER',0)==1
                        and sum(shed.values())+sum(sum(v.values()) for v in inv)+buys<=100):
                    self.sale_obligation=dict(p,eod_transfer_step=now)
        if self._pending is not None and self._selected is not None:
            prior=self._pending['previous'] or {}
            accepted={i for i in range(count)
                      if unit(returned_action,i)==unit(self._selected,i)}
            old=prior.get('plans',{});proposed=self._pending['plans']
            plans={}
            for i in old.keys() | proposed.keys():
                plan=proposed.get(i) if i in accepted else old.get(i)
                if (plan is not None and i<count
                        and (plan['end']>now or plan.get('kind')=='idle_fertilizer')):
                    if plan.get('kind')=='idle_fertilizer':
                        farm=observation['farms'][observation['player']]
                        plan=dict(plan,last_returned_step=now,
                                  last_returned_action=list(unit(returned_action,i)),
                                  last_position=tuple(self.m._farmer_position(farm,i)),
                                  last_inventory=dict(observation['private']['inventories'][i]))
                    plans[i]=plan
            # Commit each actor's exact stream. Whole rows would accidentally
            # retain an unrelated actor's proposal after its action was replaced.
            patches={}
            for i,plan in plans.items():
                for offset,action in enumerate(plan['replacement']):
                    t=plan['step']+offset
                    if t>=now:
                        patches.setdefault((plan['route'],t),{})[i]=list(action)
            events=list(prior.get('events',[]))
            for event in self._pending['events']:
                if event['worker'] in accepted and event not in events:
                    events.append(event)
            reserved=set(prior.get('reserved',()))
            for i,plan in proposed.items():
                if i in accepted and plan.get('extra'):
                    reserved.add(tuple(plan['extra']['tile']))
            self._committed={'patches':patches,'plans':plans,'events':events,
                'reserved':reserved,'active':{i:p['end'] for i,p in plans.items()},
                'day':now//24,'step':now}
        self._pending=None;self._selected=None
        self._sale_proposal=None
        self.events=[] if self._committed is None else self._committed['events']

    def guard_returned(self, observation, returned, *, repair_fallback=False):
        """A consumer cannot keep the optional deposit while removing its sale."""
        self.observe_owned_stock(observation)
        stock=self.sale_obligation
        now=int(observation['step']);farm=observation['farms'][observation['player']]
        if (repair_fallback and stock is not None and stock['status']=='carried'
                and now//24==stock['step']//24 and stock['worker']<=len(farm['hands'])
                and not returned.get('market')
                and all(unit(returned,i)==['PASS'] for i in range(1+len(farm['hands'])))
                and observation['private']['inventories'][stock['worker']]=={'FERTILIZER':1}):
            # Continue only an already owned idle return, even when the parent
            # misses its deadline. No collection, hire or new job starts here.
            pos=tuple(self.m._farmer_position(farm,stock['worker']))
            shed=min(((4,4),(5,4),(4,5),(5,5)),key=lambda q:distance(pos,q))
            returned=deepcopy(returned)
            if pos!=shed and now%24<23:
                set_unit(returned,stock['worker'],path(pos,shed)[0])
            elif pos==shed and sum(observation['private']['shed'].values())+1<=100:
                set_unit(returned,stock['worker'],['DROP'])
                returned['market']=[['SELL','FERTILIZER',1]]
                self._sale_proposal={'step':now,'slot':0,'worker':stock['worker'],
                                     'quantity':1,'target':stock['target']}
        p=self._sale_proposal
        if p is not None and p['step']==now:
            # The selected producer row was empty. Its unique added FERT lot
            # can move within pressure's SELL block; bind the FINAL slot.
            slots=[i for i,a in enumerate(returned.get('market',[])[:10])
                   if a and len(a)>2 and a[:2]==['SELL','FERTILIZER']]
            sale_ok=(len(slots)==1 and returned['market'][slots[0]]==['SELL','FERTILIZER',1])
            drop_ok=p.get('worker') is None or unit(returned,p['worker'])==['DROP']
            if sale_ok and drop_ok:p['slot']=slots[0]
            else:
                returned=deepcopy(returned)
                if p.get('worker') is not None:set_unit(returned,p['worker'],['PASS'])
                for slot in slots:returned['market'][slot]=[]
        return returned

    def observe_owned_stock(self, obs):
        """Retain the observed collection even if delivery never returns."""
        now=int(obs['step']);player=int(obs['player'])
        p=self.sale_obligation
        if p is not None:
            if player!=p['player'] or now<p['step']:
                self.sale_obligation=None;self.receipt_events=[]
            elif (p['status'] in ('observed_deposit','stock_unknown')
                  and not obs['private']['shed'].get('FERTILIZER',0)
                  and not any(v.get('FERTILIZER',0) for v in obs['private']['inventories'])):
                self.sale_obligation=None
            elif p['status']=='carried' and now//24>p['step']//24:
                confirmed=(now==p.get('eod_transfer_step',-2)+1
                           and obs['private']['shed'].get('FERTILIZER',0)>=1)
                self.sale_obligation=dict(p,status='observed_deposit' if confirmed else 'stock_unknown')
            return
        for i,plan in (self._committed or {}).get('plans',{}).items():
            if (plan.get('kind')=='idle_fertilizer'
                    and plan.get('last_returned_step')==now-1
                    and plan.get('last_returned_action')==['COLLECT_FERTILIZER']
                    and tuple(plan.get('last_position',()))==tuple(plan['extra']['tile'])
                    and i<len(obs['private']['inventories'])
                    and obs['private']['inventories'][i].get('FERTILIZER',0)
                        -plan['last_inventory'].get('FERTILIZER',0)==1):
                self.sale_obligation={'step':now-1,'player':player,'worker':i,'quantity':1,
                                      'target':tuple(plan['extra']['tile']),'status':'carried'}
                return

    def observe_market_receipt(self, obs, result):
        """Reuse the shared own-fill result; an emitted request is not cash.

        The obligation survives rejoin and EOD. Unknown fills never authorize
        another sale of the same unit. A proven zero fill can retry in an empty
        producer market row; a new match discards the old intent.
        """
        p=self.sale_obligation
        if p is None:return
        if p['status'] in ('carried','stock_unknown'):return
        if p['status']=='observed_deposit':
            # A different later sale may have consumed the fungible unit. Do
            # not retry it after such a sale without a distinct fill witness.
            binding=(result or {}).get('binding',{})
            if (binding.get('step',-1)>p.get('eod_transfer_step',-1)
                    and any(o.get('type')=='SELL' and o.get('item')=='FERTILIZER'
                            and (o.get('fill_max') or 0)>0 for o in (result or {}).get('orders',[]))):
                self.sale_obligation=dict(p,status='stock_unknown')
            return
        now=int(obs['step']);player=int(obs['player'])
        if player!=p['player'] or now<=p['step']:
            if player!=p['player'] or now<p['step']:self.sale_obligation=None
            return
        binding=(result or {}).get('binding',{})
        order=next((o for o in (result or {}).get('orders',[])
                    if o.get('slot')==p['slot']),{})
        bound=(binding.get('step')==p['step'] and binding.get('player')==player
               and (result or {}).get('status') in ('reconciled','ambiguous')
               and order.get('type')=='SELL' and order.get('item')=='FERTILIZER'
               and order.get('requested')==1)
        if bound and order.get('fill_min')==1:
            self.receipt_events.append({'kind':'idle_fertilizer_sale_receipt',
                'step':p['step'],'observed_at':now,'sold_units':1,'cash_receipt':None})
            self.sale_obligation=None
        elif bound and order.get('fill_max')==0:
            self.sale_obligation=dict(p,status='observed_zero_fill')
        elif (not obs['private']['shed'].get('FERTILIZER',0)
              and not any(v.get('FERTILIZER',0) for v in obs['private']['inventories'])):
            self.receipt_events.append({'kind':'idle_fertilizer_stock_cleared',
                'step':now,'sold_units':None,'cash_receipt':None})
            self.sale_obligation=None
        else:
            self.sale_obligation=dict(p,status='fill_unknown')
        self.receipt_events=self.receipt_events[-8:]

    def _begin(self, controller, pristine, obs):
        now=int(obs['step']);state=self._committed
        self.observe_owned_stock(obs)
        self._sale_proposal=None
        if self.sale_obligation is not None and now<self.sale_obligation['step']:
            self.sale_obligation=None;self.receipt_events=[]
        if state and (not self.supported or now<=state['step'] or now//24!=state['day']):
            state=None
        self.plans={} if not state else {i:p for i,p in state['plans'].items()
                                        if (p['end']>now or p.get('kind')=='idle_fertilizer')
                                        and p['route']==controller.cur}
        self.events=[] if not state else list(state['events'])
        farm=obs['farms'][obs['player']]
        positions=[tuple(farm['farmer']),*[tuple(p) for p in farm['hands']]]
        for i,plan in list(self.plans.items()):
            if plan.get('kind')!='idle_fertilizer' or i>=len(positions):continue
            if (now>=plan['end'] and positions[i]==tuple(plan['goal'])
                    and not obs['private']['inventories'][i].get('FERTILIZER',0)):
                del self.plans[i]
                self.events.append({'kind':'idle_fertilizer_rejoin','step':now,'worker':i})
                continue
            offset=now-plan['step'];replacement=[list(a) for a in plan['replacement']]
            x,y=plan['extra']['tile'];tile=farm['tiles'][y][x]
            updated=dict(plan)
            if (plan.get('last_returned_step')==now-1
                    and plan.get('last_returned_action')==['COLLECT_FERTILIZER']
                    and tuple(plan.get('last_position',()))==(x,y)):
                gained=(obs['private']['inventories'][i].get('FERTILIZER',0)
                        -plan['last_inventory'].get('FERTILIZER',0))
                updated['collection_observed']=gained==1
                self.events.append({'kind':'idle_fertilizer_receipt','step':now,
                                    'worker':i,'carried_fertilizer_delta':gained})
            if (0<=offset<len(replacement) and replacement[offset]==['COLLECT_FERTILIZER']
                    and (positions[i]!=(x,y) or not isinstance(tile,dict)
                         or 'animal' not in tile or not tile.get('fertilizer_available'))):
                # Keep the owned return path; reverting to idle at this remote
                # position would strand the worker away from its raw route.
                replacement[offset]=['PASS'];updated['replacement']=replacement
                updated['collection_declined_at']=now
            if (0<=offset<len(replacement) and replacement[offset]==['DROP']
                    and sum(obs['private']['shed'].values())+
                        sum(sum(inv.values()) for inv in obs['private']['inventories'])>100):
                replacement[offset]=['PASS'];updated['replacement']=replacement
                updated['deposit_declined_at']=now
            self.plans[i]=updated
        # Returning DIG records intent; the next observation establishes its
        # actual effect. A later consumer can change another actor's actions.
        # Before inserting the retry, require the same actor, empty site, and
        # enough observed stock for every remaining seed obligation again.
        for i,plan in list(self.plans.items()):
            if plan.get('kind')!='weed_continuation' or now!=plan['step']+1:continue
            reason=None;x,y=plan['origin']
            if i>=len(positions) or positions[i]!=tuple(plan['origin']):
                reason='retry_actor_or_position_changed'
            elif farm['tiles'][y][x] is not None:
                reason='retry_site_not_empty'
            elif plan['obligation'][0]=='PLANT':
                crop=plan['obligation'][1]
                reserve=(self.seed_reserve(crop,now-1,controller.cur)
                         +self.future_seed_requests(now-1).get(crop,0))
                if obs['private']['seeds'].get(crop,0)<reserve:
                    reason='retry_seed_reserve_changed'
            if reason:
                del self.plans[i]
                self.events.append({'step':now,'worker':i,'invalidated':reason})
        if state:
            state=dict(state,plans=dict(self.plans),events=list(self.events))
        self._base_state=state
        # Build replacement tables before publishing. Original rows are read-only;
        # every changed row is copied below. Branch-prefix checks see pristine past.
        routes={key:list(rows) for key,rows in pristine.items()}
        if state:
            patches={}
            for i,plan in self.plans.items():
                for offset,a in enumerate(plan['replacement']):
                    t=plan['step']+offset
                    if t>=now:patches.setdefault((plan['route'],t),{})[i]=a
            for (route,t),actions in patches.items():
                if t>=now and route==controller.cur:
                    row=deepcopy(routes[route][t])
                    for i,action in actions.items():
                        if i in self.plans:set_unit(row,i,list(action))
                    routes[route][t]=row
        controller.R=routes
        self.edits=[];self._pending=None;self._selected=None
        self.active={i:p['end'] for i,p in self.plans.items()}
        self.day=now//24
        self.reserved=set() if not state or state['day']!=self.day else set(state['reserved'])
        self._repair_positions(obs,controller)

    def _repair_positions(self, obs, controller):
        """Rejoin a committed route after a missed move using actual own position.

        Preserve remaining task order when the shortened route fits. Otherwise
        abandon that optional segment and return to its exact endpoint. This
        recovery is never used as a forecast of unobserved farm changes.
        """
        now=int(obs['step']);farm=obs['farms'][obs['player']]
        positions=[tuple(farm['farmer'])]+[tuple(p) for p in farm['hands']]
        for i,plan in list(self.plans.items()):
            if i>=len(positions) or plan['route']!=controller.cur:continue
            if plan.get('kind')=='idle_fertilizer' and now>=plan['end']:
                # Admission requires literal idle rows through reset. A missed
                # final move/drop does not silently certify the old endpoint.
                # Repair only in that remaining idle space; reset is the final
                # fallback when repeated cancellations leave no round trip.
                stop=min((now//24+1)*24-1,719)
                left=stop-now;goal=tuple(plan['goal'])
                route=controller.R[plan['route']]
                idle=all(unit(route[t],i)==['PASS'] for t in range(now,stop+1))
                candidates=[path(positions[i],goal)]
                if obs['private']['inventories'][i].get('FERTILIZER',0):
                    candidates=[path(positions[i],shed)+[['DROP']]+path(shed,goal)
                                for shed in ((4,4),(5,4),(4,5),(5,5))]
                trial=min(candidates,key=len)
                if not idle or len(trial)>left:
                    trial=[['PASS']] if idle else [list(unit(route[now],i))]
                    reason='awaiting_day_reset' if idle else 'no_certified_idle_rejoin'
                else:reason='repairing_unobserved_endpoint'
                for k,a in enumerate(trial):
                    row=deepcopy(route[now+k]);set_unit(row,i,list(a));route[now+k]=row
                updated=dict(plan,step=now,origin=positions[i],replacement=trial,end=now+len(trial))
                self.plans[i]=updated;self.active[i]=updated['end']
                self.events.append({'kind':'idle_fertilizer_recovery','step':now,'worker':i,'reason':reason})
                continue
            offset=now-plan['step'];expected=tuple(plan['origin'])
            for a in plan['replacement'][:offset]:expected=move(expected,a,10)
            if positions[i]==expected:continue
            cursor=expected;tasks=[]
            for a in plan['replacement'][offset:]:
                if a[0] not in MOVES and a[0]!='PASS':tasks.append((cursor,a))
                cursor=move(cursor,a,10)
            trial=[];cursor=positions[i]
            for location,action in tasks:
                trial+=path(cursor,location)+[action];cursor=location
            trial+=path(cursor,tuple(plan['goal']))
            left=plan['end']-now;abandoned=len(trial)>left
            if abandoned:trial=path(positions[i],tuple(plan['goal']))
            if len(trial)>left:
                # No feasible rejoin exists; retain the already committed stream.
                continue
            trial+= [['PASS']]*(left-len(trial))
            route=controller.R[plan['route']]
            for k,action in enumerate(trial):
                row=deepcopy(route[now+k]);set_unit(row,i,list(action));route[now+k]=row
                self.edits.append((plan['route'],now+k,None))
            updated=dict(plan,step=now,origin=positions[i],replacement=trial)
            self.plans[i]=updated
            self.events.append({'step':now,'worker':i,'recovery':True,'abandoned':abandoned})

    def install(self,controller):
        """Bind to the same producer; caller must call finish after each return."""
        pristine=controller.R
        original=controller.act
        def act(obs):
            self._begin(controller,pristine,obs)
            selected=original(obs)
            result=self.transform(obs,selected,controller)
            self._pending={'previous':self._base_state,
                'events':list(self.events),'plans':dict(self.plans)}
            self._selected=deepcopy(result)
            return result
        controller.act=act

    def future_seed_requests(self,after_step):
        """Extra requests augment the existing compatible-route seed bound."""
        demand={}
        for plan in self.plans.values():
            if plan.get('obligation',[''])[0]!='PLANT':continue
            for offset,a in enumerate(plan['replacement']):
                if plan['step']+offset>after_step and a[0]=='PLANT':
                    demand[a[1]]=demand.get(a[1],0)+1
        return demand

    def _apply_units(self,farm,private,row,now):
        actions=[row.get('farmer',['PASS']),*row.get('hands',[])]
        demand={}
        for a in actions:
            if a and a[0]=='PLANT' and len(a)>1:
                demand[a[1]]=demand.get(a[1],0)+1
        blocked={crop for crop,n in demand.items() if n>private['seeds'].get(crop,0)}
        for i,a in enumerate(actions):
            if a and a[0]=='PLANT' and a[1] in blocked:a=['PASS']
            self.m._apply_unit_action(farm,private,i,a,10,now//24,24,100)
        self.m._decay_plants(farm,now)

    def _weed_witness(self,obs,selected,route,worker,obligation,stop,reserve):
        """Certify a bounded insertion using observed, pre-market resources.

        Only movement and resource-free service may be delayed. No future
        purchase is credited. Other workers cannot touch a delayed service site,
        and exact joint unit mechanics must rejoin with only the new tile and
        its one seed differing. Markets retain their original times and slots.
        """
        now=int(obs['step']);farm=deepcopy(obs['farms'][obs['player']])
        private=deepcopy(obs['private']);origin=tuple(self.m._farmer_position(farm,worker))
        self._apply_units(farm,private,selected,now)
        x,y=origin
        if farm['tiles'][y][x] is not None:return None
        if obligation[0]=='PLANT' and private['seeds'].get(obligation[1],0)<=reserve:
            return None
        original=[list(unit(route[t],worker)) for t in range(now+1,stop+1)]
        replacement=[list(obligation),*original[:-1]]
        service={origin};cursor=origin
        for a in original:
            if a[0] in ('WATER','CARE'):service.add(cursor)
            tile=farm['tiles'][cursor[1]][cursor[0]]
            if isinstance(tile,dict) and tile.get('kind')=='WEED' and a[0] not in MOVES:
                return None
            cursor=move(cursor,a,10)
        goal=cursor
        positions=[tuple(farm['farmer']),*[tuple(p) for p in farm['hands']]]
        for t in range(now+1,stop+1):
            for i,pos in enumerate(positions):
                if i==worker:continue
                a=unit(route[t],i)
                if a[0] not in (*MOVES,'PASS') and pos in service:return None
                positions[i]=move(pos,a,10)
        results=[]
        for schedule in (original,replacement):
            f=deepcopy(farm);p=deepcopy(private)
            for offset,a in enumerate(schedule):
                t=now+1+offset;row=deepcopy(route[t]);set_unit(row,worker,a)
                self._apply_units(f,p,row,t)
                if offset==0 and schedule is replacement:
                    tile=f['tiles'][y][x]
                    kind='PLANT' if obligation[0]=='PLANT' else obligation[0][6:]
                    if not isinstance(tile,dict) or tile.get('kind')!=kind:return None
                    if kind=='PLANT' and tile.get('crop')!=obligation[1]:return None
            results.append((f,p))
        baseline,bp=results[0];candidate,cp=results[1]
        tile=candidate['tiles'][y][x]
        if baseline['tiles'][y][x] is not None or tile is None:return None
        if obligation[0]=='PLANT' and not tile.get('watered_today'):return None
        candidate['tiles'][y][x]=None
        if obligation[0]=='PLANT':
            crop=obligation[1]
            if cp['seeds'].get(crop,0)!=bp['seeds'].get(crop,0)-1:return None
            cp['seeds'][crop]=bp['seeds'][crop]
        if candidate!=baseline or cp!=bp:return None
        return original,replacement,goal

    def _continue_weed(self,obs,selected,controller,end):
        now=int(obs['step']);route=controller.R[controller.cur]
        farm=obs['farms'][obs['player']]
        positions=[tuple(farm['farmer']),*[tuple(p) for p in farm['hands']]]
        # Finish before the turn which performs reset, not merely before the
        # next day's first call. Branch boundaries are already in end.
        end=min(end,(now//24+1)*24-1)
        for i,origin in enumerate(positions):
            if i in self.active:continue
            obligation=unit(route[now],i)
            if not obligation or obligation[0] not in ('PLANT','BUILD_COOP','BUILD_PASTURE'):continue
            if obligation[0]=='PLANT' and (len(obligation)<2 or obligation[1] not in self.m.CROPS):continue
            tile=farm['tiles'][origin[1]][origin[0]]
            if unit(selected,i)!=['DIG'] or not isinstance(tile,dict) or tile.get('kind')!='WEED':continue
            stop=None
            for t in range(now+1,end):
                a=unit(route[t],i)
                if a==['PASS']:
                    stop=t;break
                if not a or a[0] not in (*MOVES,'WATER','CARE'):break
            if stop is None:continue
            reserve=0
            if obligation[0]=='PLANT':
                # A surviving crop can still starve a much later commitment
                # (ASH step622). Preserve the full incumbent suffix across all
                # prefix-compatible branches, without credit for future buys.
                if self.seed_reserve is None:continue
                crop=obligation[1]
                reserve=(self.seed_reserve(crop,now,controller.cur)
                         +self.future_seed_requests(now).get(crop,0))
            witness=self._weed_witness(obs,selected,route,i,obligation,stop,reserve)
            if witness is None:continue
            original,replacement,goal=witness
            for offset,a in enumerate(replacement):
                t=now+1+offset;row=deepcopy(route[t]);set_unit(row,i,list(a));route[t]=row
                self.edits.append((controller.cur,t,None))
            event={'kind':'weed_continuation','step':now,'worker':i,'end':stop+1,
                'route':controller.cur,'origin':origin,'goal':goal,'saved_travel':0,'extra':None,
                'obligation':list(obligation),'absorbed_step':stop,
                'original':[list(unit(selected,i)),*original],
                'replacement':[['DIG'],*replacement]}
            self.plans[i]=event;self.active[i]=stop+1;self.events.append(event)
            return True
        return False

    def _suffix(self,obs,route,now,end):
        farm=obs['farms'][obs['player']];positions=[tuple(farm['farmer'])]+[tuple(p) for p in farm['hands']]
        touches={};harvests={};board=len(farm['tiles'])
        for t in range(now,end):
            for i,p in enumerate(positions):
                a=unit(route[t],i);op=a[0] if a else 'PASS'
                if op in MOVES:positions[i]=move(p,a,board)
                elif op not in ('PASS','DROP','PICKUP'):
                    touches.setdefault(p,set()).add(i)
                    if op=='HARVEST':harvests.setdefault(p,t)
        return touches,harvests

    def _same_work_state(self,obs,worker,original,replacement):
        # Compare exact own-unit mechanics; no invented future state.
        result=[];n=len(obs['farms'][obs['player']]['tiles'])
        for sequence in (original,replacement):
            farm=deepcopy(obs['farms'][obs['player']]);private=deepcopy(obs['private'])
            for a in sequence:
                x,y=self.m._farmer_position(farm,worker);tile=farm['tiles'][y][x]
                # The producer may replace a no-op on an observed weed with DIG.
                # Do not certify a raw stream which omits that live repair.
                if isinstance(tile,dict) and tile.get('kind')=='WEED' and a[0] not in MOVES:return False
                self.m._apply_unit_action(farm,private,worker,a,n,int(obs['day']),24,100)
            result.append((farm,private))
        return result[0]==result[1]

    def _idle_stock_bound(self,obs,route,end):
        """Bound all current-day physical arrivals without future sale credit.

        A collected unit must not displace a committed deposit or purchase.
        Current shed and every carried item share one bound. Every standing
        asset's remaining yield is counted once, even when not on a harvest
        route. Annual WATER may add its maximum same-day bonus. All requested
        product/animal purchases consume room.
        Worker creation and branch changes decline this narrow certificate.
        """
        now=int(obs['step']);farm=obs['farms'][obs['player']];private=obs['private']
        positions=[tuple(farm['farmer']),*[tuple(p) for p in farm['hands']]]
        stock=sum(private['shed'].values())+sum(sum(inv.values()) for inv in private['inventories'])
        collections=set();feeds=set()
        if any(now<t<end for t in CHECKPOINTS):return None
        for t in range(now,end):
            for a in route[t].get('market',[])[:10]:
                if not a:continue
                if a[0]=='HIRE':return None
                if a[0] in ('BUY_PRODUCT','BUY_ANIMAL'):
                    if len(a)<3 or type(a[2]) is not int or a[2]<0:return None
                    stock+=a[2]
            for i,pos in enumerate(positions):
                a=unit(route[t],i);op=a[0] if a else 'PASS'
                if op=='COLLECT_FERTILIZER':collections.add(pos)
                if op=='FEED':feeds.add(pos)
                positions[i]=move(pos,a,10)
        for row in farm['tiles']:
            for tile in row:
                if not isinstance(tile,dict):continue
                quantity=max(0,int(tile.get('yield_units',0)))
                if tile.get('kind')=='PLANT':
                    crop=self.m.CROPS.get(tile.get('crop'))
                    if crop is None:return None
                    age=now//24-tile['planted_day']
                    if (not crop['ongoing'] and not tile.get('watered_today')
                            and (crop['max_yield_day']+1)//2<=age<=crop['max_yield_day']):
                        quantity=min(crop['max_yield'],quantity+2)
                stock+=quantity
                if 'animal' in tile and tile.get('fertilizer_available'):stock+=1
        return stock,collections,feeds

    def _fertilizer_outlet(self,obs,route,target,deposit,day_bound,*,terminal_worker=None):
        """Certify an empty outlet or the producer's unique terminal product lot.

        The added unit is sold in its own DROP turn, after every inherited row.
        Avoid operating-input obligations throughout this narrow idle window.
        At 718 the real producer already includes the owned DROP in settlement;
        that existing one-unit sale must be reused rather than appended again.
        """
        now=int(obs['step']);close=min((now//24+1)*24,719)
        terminal=deposit==718
        if deposit>=close or (not terminal and route[deposit].get('market')):return None
        if terminal:
            # The pinned settlement fits all nine products in ten slots only
            # with its ordinary product-only tail. Do not remove placeholders
            # or cross economic barriers to manufacture room for this job.
            orders=route[deposit].get('market',[])
            if len(orders)>10 or any(not a or len(a)!=3 or a[0]!='SELL'
                    or a[1] not in self.m.PRODUCTS or type(a[2]) is not int
                    or a[2]<0 for a in orders):return None
            private=obs['private']
            physical=private['shed'].get('FERTILIZER',0)+sum(
                inv.get('FERTILIZER',0) for inv in private['inventories'])
            # Before departure there must be no fungible baseline fertilizer.
            # At delivery only this already observed collected unit may exist.
            if terminal_worker is None:
                if physical:return None
            elif (terminal_worker>=len(private['inventories']) or physical!=1
                  or private['inventories'][terminal_worker]!={'FERTILIZER':1}):return None
        for t in range(now,close):
            for a in [unit(route[t],0),*route[t].get('hands',[])]:
                if a and (a[0]=='FERTILIZE' or a[:2]==['PICKUP','FERTILIZER']):return None
                if terminal and a and a[0]=='COLLECT_FERTILIZER':return None
            if terminal:
                for a in route[t].get('market',[])[:10]:
                    if (a and a[:2]==['BUY_PRODUCT','FERTILIZER']
                            and (len(a)<3 or type(a[2]) is not int or a[2]!=0)):return None
        return {'step':deposit,'slot':0,'shared_stock_upper':day_bound,
                'basis':'same_return_deposit_and_one_unit_sale','future_sale_credit':0,
                'terminal_reuse':terminal}

    def _deliver_idle_fertilizer(self,obs,selected,controller):
        """Bind a proved one-unit pickup to a same-return DROP and surplus sale."""
        now=int(obs['step']);route=controller.R[controller.cur]
        close=min((now//24+1)*24,719)
        for i,plan in self.plans.items():
            if plan.get('kind')!='idle_fertilizer' or unit(selected,i)!=['DROP']:continue
            stock=obs['private']['inventories'][i]
            if not stock.get('FERTILIZER',0):continue
            position=tuple(self.m._farmer_position(obs['farms'][obs['player']],i))
            bound=self._idle_stock_bound(obs,route,close)
            market=selected.get('market',[])
            slots=[j for j,a in enumerate(market[:10])
                   if a and len(a)>1 and a[:2]==['SELL','FERTILIZER']]
            actual_terminal=(len(market)<=10 and all(a and len(a)==3 and a[0]=='SELL'
                and a[1] in self.m.PRODUCTS and type(a[2]) is int and a[2]>=0 for a in market)
                and not any(a and (a[0] in ('FERTILIZE','COLLECT_FERTILIZER')
                                   or a[:2]==['PICKUP','FERTILIZER'])
                            for a in [unit(selected,0),*selected.get('hands',[])]))
            # An earlier owned job may reach this outlet after a cancelled
            # delivery. Revalidate its current stock and route below instead
            # of requiring 718 to have been its originally planned outlet.
            reuse=(now==718 and actual_terminal and len(slots)==1
                   and market[slots[0]]==['SELL','FERTILIZER',1])
            supported=(plan.get('collection_observed') and stock=={'FERTILIZER':1}
                       and position in ((4,4),(5,4),(4,5),(5,5))
                       and (now!=718 or actual_terminal)
                       and (not market or reuse) and bound is not None and bound[0]<=100
                       and self._fertilizer_outlet(obs,route,plan['extra']['tile'],now,bound[0],
                                                  terminal_worker=i if now==718 else None) is not None)
            out=deepcopy(selected)
            if not supported:
                # Keep the fertilizer carried and retry only in owned idle slack.
                # In particular, do not deposit into a market whose sale is absent.
                set_unit(out,i,['PASS'])
                return out
            if not reuse:out['market']=[['SELL','FERTILIZER',1]]
            self._sale_proposal={'step':now,'slot':slots[0] if reuse else 0,'worker':i,'quantity':1,
                                 'target':tuple(plan['extra']['tile'])}
            return out
        p=self.sale_obligation
        if (p is not None and p['status'] in ('observed_zero_fill','observed_deposit')
                and obs['private']['shed'].get('FERTILIZER',0)>=1
                and not selected.get('market')
                and self._fertilizer_outlet(obs,route,p['target'],now,0) is not None):
            out=deepcopy(selected);out['market']=[['SELL','FERTILIZER',1]]
            self._sale_proposal={'step':now,'slot':0,'worker':None,'quantity':1,
                                 'target':p['target']}
            return out
        return None

    def _collect_idle_fertilizer(self,obs,selected,controller,end):
        """Salvage visible unclaimed fertilizer using literal idle actions.

        No input, market, productive service or carried inventory is displaced.
        This buys no animal and promises no future production. The worker must
        collect, deposit and return before hiring/reset/branch boundaries.
        Delivery appends one surplus sale to an empty producer market row, or
        reuses its certified terminal lot. Future requested sales never prove
        that shed room will return.
        """
        if (not self.idle_fertilizer or not self.idle_supported
                or self.sale_obligation is not None
                or any(p.get('kind')=='idle_fertilizer' for p in self.plans.values())):return None
        now=int(obs['step']);farm=obs['farms'][obs['player']];private=obs['private']
        route=controller.R[controller.cur]
        close=min((now//24+1)*24,719)
        stop=min(end,(now//24+1)*24-1,719)
        if stop-now<2:return None
        workers=[]
        positions=[tuple(farm['farmer']),*[tuple(p) for p in farm['hands']]]
        for i,origin in enumerate(positions):
            if (i in self.active or i>=len(private['inventories'])
                    or any(private['inventories'][i].values())
                    or unit(selected,i)!=['PASS'] or unit(route[now],i)!=['PASS']):continue
            length=0
            while now+length<stop and length<6 and unit(route[now+length],i)==['PASS']:
                length+=1
            if length>=2 and all(unit(route[t],i)==['PASS'] for t in range(now,close)):
                workers.append((i,origin,length))
        if not workers:return None
        bound=self._idle_stock_bound(obs,route,close)
        if bound is None or bound[0]>100:return None
        candidates=[]
        for y,row in enumerate(farm['tiles']):
            for x,tile in enumerate(row):
                pos=(x,y)
                if (not isinstance(tile,dict) or 'animal' not in tile
                        or not tile.get('fertilizer_available')
                        or tile.get('fed_today') or tile.get('consecutive_unfed',0)<1
                        or pos in bound[1] or pos in bound[2] or pos in self.reserved):continue
                for i,origin,length in workers:
                    for shed in ((4,4),(5,4),(4,5),(5,5)):
                        trial=path(origin,pos)+[['COLLECT_FERTILIZER']]+path(pos,shed)+[['DROP']]+path(shed,origin)
                        if len(trial)>length:continue
                        deposit=now+distance(origin,pos)+1+distance(pos,shed)
                        outlet=self._fertilizer_outlet(obs,route,pos,deposit,bound[0])
                        if outlet is None:continue
                        candidates.append((len(trial),outlet['step'],i,pos,origin,trial,deposit,outlet))
        if not candidates:return None
        _,sale,i,pos,origin,trial,deposit,outlet=min(candidates,key=lambda c:c[:4])
        out=deepcopy(selected)
        for offset,a in enumerate(trial):
            t=now+offset;row=deepcopy(route[t]);set_unit(row,i,list(a));route[t]=row
            self.edits.append((controller.cur,t,None))
        set_unit(out,i,list(trial[0]));self.reserved.add(pos)
        event={'kind':'idle_fertilizer','step':now,'worker':i,'end':now+len(trial),
               'route':controller.cur,'origin':origin,'goal':origin,'saved_travel':0,
               'extra':{'tile':pos,'item':'FERTILIZER','quantity':1,'deposit_step':deposit},
               'stock_upper_bound':bound[0],'sale_outlet_step':sale,
               'outlet_certificate':outlet,
               'original':[['PASS'] for _ in trial],'replacement':trial}
        self.plans[i]=event;self.active[i]=event['end'];self.events.append(event)
        return out

    def transform(self,obs,selected,controller):
        now=int(obs['step']);board=len(obs['farms'][obs['player']]['tiles'])
        if board!=10 or not self.supported:return selected
        delivery=self._deliver_idle_fertilizer(obs,selected,controller)
        if delivery is not None:return delivery
        end=min((now//24+1)*24,719,*[x for x in CHECKPOINTS if x>now]) if any(x>now for x in CHECKPOINTS) else min((now//24+1)*24,719)
        if end-now<3:return selected
        route=controller.R[controller.cur];farm=obs['farms'][obs['player']];private=obs['private']
        # HIRE happens after unit moves. Existing positions choose the spawn
        # corner, so every worker must rejoin before the next hiring turn.
        if any(a and a[0]=='HIRE' for a in selected.get('market',[])):return selected
        for step in range(now,end):
            if any(a and a[0]=='HIRE' for a in route[step].get('market',[])):
                end=step;break
        if self._continue_weed(obs,selected,controller,end):return selected
        idle=self._collect_idle_fertilizer(obs,selected,controller,end)
        if idle is not None:return idle
        if not self.pathing and not self.tempo:return selected
        if end-now<3:return selected
        positions=[tuple(farm['farmer'])]+[tuple(p) for p in farm['hands']]
        touches,harvests=self._suffix(obs,route,now,end);out=deepcopy(selected);planned=0
        for i,origin in enumerate(positions):
            if i in self.active or planned>=2:continue
            actual=unit(selected,i);baseline=unit(route[now],i)
            if actual!=baseline:continue
            sequence=[];tasks=[];p=origin;t=now
            while t<end:
                a=unit(route[t],i);op=a[0] if a else 'PASS'
                if op not in MOVES and op!='PASS' and op not in WORK:break
                if op in WORK:
                    if touches.get(p,set())-{i}:break
                    tasks.append((p,list(a)))
                sequence.append(list(a));p=move(p,a,board);t+=1
            length=len(sequence);goal=p
            if length<3:continue
            # Only alter a complete segment ending at the exact original place.
            groups=[]
            for pos,a in tasks:
                if groups and groups[-1][0]==pos:groups[-1][1].append(a)
                else:groups.append((pos,[a]))
            best=list(sequence);saved=0
            if self.pathing and 1<=len(groups)<=6 and len({p for p,_ in groups})==len(groups):
                choices=[]
                for order in permutations(range(len(groups))):
                    q=origin;trial=[]
                    for index in order:
                        pos,acts=groups[index];trial+=path(q,pos)+acts;q=pos
                    trial+=path(q,goal)
                    if len(trial)<=length:choices.append(trial)
                if choices:
                    trial=min(choices,key=lambda a:sum(x[0] in MOVES for x in a));trial+= [['PASS']]*(length-len(trial))
                    saved=sum(a[0] in MOVES for a in sequence)-sum(a[0] in MOVES for a in trial)
                    if saved>0 and self._same_work_state(obs,i,sequence,trial):best=trial
                    else:saved=0
            extra=None
            if self.tempo:
                # The slack suffix follows all existing work; service ordering is preserved.
                last=max([j for j,a in enumerate(best) if a[0] not in MOVES and a[0]!='PASS'],default=-1)
                start=origin
                for a in best[:last+1]:start=move(start,a,board)
                available=length-last-1
                candidates=[]
                for y,row in enumerate(farm['tiles']):
                    for x,tile in enumerate(row):
                        pos=(x,y)
                        if not isinstance(tile,dict) or pos in touches or pos in self.reserved:continue
                        quantity=int(tile.get('yield_units',0))
                        if quantity<=0:continue
                        if 'animal' in tile:item=self.m.ANIMALS[tile['animal']]['product']
                        elif tile.get('crop') in self.m.CROPS and self.m.CROPS[tile['crop']]['ongoing']:
                            item=tile['crop']
                            if int(obs['day'])-tile['planted_day']<self.m.CROPS[item]['first_yield_day']:continue
                        else:continue
                        # Retain input stocks; current marginal liquidation must clear the floor.
                        if item in ('WHEAT','FERTILIZER'):continue
                        market=obs['market'];inv=market['inventory'][item]
                        marginal=self.m.market_price(item,inv+quantity-1,market.get('params'))
                        if marginal<=1:continue
                        for shed in ((4,4),(5,4),(4,5),(5,5)):
                            trial=path(start,pos)+[['HARVEST']]+path(pos,shed)+[['DROP']]+path(shed,goal)
                            if len(trial)>available:continue
                            deposit=now+last+1+distance(start,pos)+1+distance(pos,shed)
                            # Conservative visible-stock reservation through arrival; no sale credit.
                            pending=0
                            for cell,when in harvests.items():
                                if when<=deposit:
                                    tile2=farm['tiles'][cell[1]][cell[0]]
                                    if isinstance(tile2,dict):pending+=max(0,int(tile2.get('yield_units',0)))
                            reserved=sum(p['extra']['quantity'] for p in self.plans.values()
                                         if p.get('extra') and p['extra']['deposit_step']>=now)
                            stock=sum(private['shed'].values())+sum(sum(v.values()) for v in private['inventories'])+pending+reserved+quantity
                            if stock>100:continue
                            value=sum(self.m.market_price(item,inv+q,market.get('params')) for q in range(quantity))
                            candidates.append((value,-len(trial),-distance(pos,shed),pos,item,quantity,trial,deposit))
                if candidates:
                    extra=max(candidates,key=lambda z:z[:3]);trial=best[:last+1]+extra[6]
                    best=trial+[['PASS']]*(length-len(trial));self.reserved.add(extra[3])
            if saved<=0 and extra is None:continue
            for offset,a in enumerate(best):
                step=now+offset;old=route[step];new=deepcopy(old);set_unit(new,i,list(a));route[step]=new;self.edits.append((controller.cur,step,old))
            set_unit(out,i,list(best[0]));self.active[i]=t;planned+=1
            event={'step':now,'worker':i,'end':t,'route':controller.cur,'origin':origin,'goal':goal,'saved_travel':saved,'extra':None if extra is None else {'tile':extra[3],'item':extra[4],'quantity':extra[5],'deposit_step':extra[7]},'original':sequence,'replacement':best}
            self.plans[i]=event;self.events.append(event)
        return out
