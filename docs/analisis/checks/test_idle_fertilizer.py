# SPDX-License-Identifier: Apache-2.0
"""Idle earning jobs: unit primitives and commit contracts, no game panel."""
from copy import deepcopy
import unittest
from unittest.mock import patch
import time
import titan_runtime as T

import mechanics as m
from spatial_tempo import SpatialTempo, unit
from titan_runtime import load
from pathlib import Path

fills=load("_idle_test_fills",T.HERE/"reference/titan-history/observed_fills.py",cache=True)


class Controller:
    def __init__(self,route):
        self.R={'case':deepcopy(route)};self.cur='case';self.calls=0
    def act(self,obs):
        self.calls+=1
        return deepcopy(self.R[self.cur][obs['step']])


class IdleFertilizerTests(unittest.TestCase):
    def fixture(self,step=33):
        tile=m._new_animal('COW',0);tile['fertilizer_available']=True;tile['consecutive_unfed']=1
        board=[[None for _ in range(10)] for _ in range(10)]
        board[3][4]=tile
        farm={'farmer':[4,4],'hands':[[0,0]],'tiles':board,'money':54,'unlocked_quadrants':['NW'],'hires_today':0}
        private={'shed':{'WHEAT':5,'FERTILIZER':1},'inventories':[{},{}],'seeds':{}}
        obs={'step':step,'day':step//24,'hour':step%24,'player':0,
             'farms':[farm,deepcopy(farm)],'private':private,
             'market':{'inventory':{p:10000 for p in m.PRODUCTS},'params':None,
                       'prices':{p:m.market_price(p,10000) for p in m.PRODUCTS}},
             'town':{'unlocked_shops':[]}}
        row={'farmer':['PASS'],'hands':[['PASS']],'market':[]}
        route=[deepcopy(row) for _ in range(720)]
        route[47]['market']=[['SELL','FERTILIZER',1],[],['SELL','WHEAT',0]]
        route[65]['market']=[['SELL','FERTILIZER',4],['BUY_ANIMAL','COW',1]]
        return obs,route

    def install(self,route,enabled=True):
        controller=Controller(route)
        owner=SpatialTempo(m,pathing=False,tempo=False,idle_fertilizer=enabled)
        owner.configure({});owner.install(controller)
        return controller,owner

    def advance(self,obs,action,owner=None):
        before=deepcopy(obs);obs=deepcopy(obs)
        for i,a in enumerate([action['farmer'],*action['hands']]):
            m._apply_unit_action(obs['farms'][0],obs['private'],i,a,10,obs['day'],24,100)
        ledger=fills.ObservedFillLedger() if owner is not None else None
        if ledger is not None:
            ledger.record(before,{},action,post_unit_shed=obs['private']['shed'],
                          post_unit_inventories=obs['private']['inventories'])
        for a in action.get('market',[]):
            if a and a[0]=='SELL':
                for _ in range(a[2]):
                    price=m.market_price(a[1],obs['market']['inventory'][a[1]])
                    m._commit_unit('SELL',a[1],price,obs['farms'][0],obs['private'],obs['market'],100)
        obs['step']+=1;obs['day']=obs['step']//24;obs['hour']=obs['step']%24
        if owner is not None:owner.observe_market_receipt(obs,ledger.observe(obs))
        return obs

    def test_collect_deposit_rejoin_has_only_explicit_one_unit_delta(self):
        obs,route=self.fixture();baseline=deepcopy(obs);original=deepcopy(route)
        p,owner=self.install(route)
        for expected in ('NORTH','COLLECT_FERTILIZER','SOUTH','DROP'):
            now=obs['step'];out=p.act(obs)
            self.assertEqual(out['farmer'],[expected])
            self.assertEqual(out['hands'],original[now]['hands'])
            self.assertEqual(out['market'],[['SELL','FERTILIZER',1]] if expected=='DROP' else original[now]['market'])
            out=owner.guard_returned(obs,out);owner.finish(obs,out);obs=self.advance(obs,out,owner)
            baseline=self.advance(baseline,original[now])
        self.assertEqual(p.calls,4)
        self.assertEqual(obs['private']['shed']['FERTILIZER'],1)
        self.assertEqual(obs['private']['inventories'][0],{})
        self.assertEqual(obs['farms'][0]['farmer'],[4,4])
        self.assertEqual(p.act(obs)['farmer'],['PASS'])
        self.assertEqual(obs['farms'][0]['money']-baseline['farms'][0]['money'],m.market_price('FERTILIZER',10000))
        obs['farms'][0]['money']=baseline['farms'][0]['money']
        obs['market']=deepcopy(baseline['market'])
        obs['farms'][0]['tiles'][3][4]['fertilizer_available']=True
        self.assertEqual(obs,baseline)
        for now in range(720):
            self.assertEqual(p.R['case'][now]['market'],original[now]['market'])
            self.assertEqual(p.R['case'][now]['hands'],original[now]['hands'])
        self.assertTrue(any(e.get('carried_fertilizer_delta')==1 for e in owner.events))

    def test_no_other_worker_same_day_collection_is_displaced(self):
        obs,route=self.fixture();obs['farms'][0]['hands'][0]=[3,3]
        route[34]['hands'][0]=['EAST'];route[35]['hands'][0]=['COLLECT_FERTILIZER']
        p,owner=self.install(route)
        self.assertEqual(p.act(obs),route[33]);self.assertEqual(owner.plans,{})

    def test_carried_operating_stock_is_not_dropped_early(self):
        obs,route=self.fixture();obs['private']['inventories'][0]={'WHEAT':1}
        p,owner=self.install(route)
        self.assertEqual(p.act(obs),route[33]);self.assertEqual(owner.plans,{})

    def test_shared_carry_and_future_buys_need_room_without_sale_credit(self):
        for case in ('carried','purchase','standing'):
            with self.subTest(case=case):
                obs,route=self.fixture();obs['private']['shed']={'WHEAT':99}
                if case=='carried':obs['private']['inventories'][1]={'WHEAT':1}
                if case=='purchase':route[40]['market']=[['SELL','WHEAT',99],['BUY_PRODUCT','WHEAT',1]]
                if case=='standing':obs['farms'][0]['tiles'][3][4]['yield_units']=1
                p,owner=self.install(route)
                self.assertEqual(p.act(obs),route[33]);self.assertEqual(owner.plans,{})

    def test_annual_due_day_water_bonus_is_in_the_stock_ceiling(self):
        obs,route=self.fixture();obs['private']['shed']={'WHEAT':94}
        plant=m._new_plant('WHEAT',-1,24);plant['yield_units']=4
        obs['farms'][0]['tiles'][0][0]=plant
        p,owner=self.install(route)
        self.assertEqual(p.act(obs),route[33])
        plant['watered_today']=True
        p,owner=self.install(route)
        self.assertEqual(p.act(obs)['farmer'],['NORTH'])

    def test_hire_branch_eod_and_productive_actions_are_boundaries(self):
        for case in ('hire','branch','eod','productive'):
            with self.subTest(case=case):
                step=223 if case=='branch' else 45 if case=='eod' else 33
                obs,route=self.fixture(step)
                if case=='hire':route[step+2]['market']=[['HIRE']]
                if case=='productive':route[step+2]['farmer']=['CARE']
                route[min(718,step+10)]['market']=[['SELL','FERTILIZER',2]]
                p,owner=self.install(route)
                self.assertEqual(p.act(obs),route[step]);self.assertEqual(owner.plans,{})

    def test_missing_outlet_and_disabled_or_unsupported_config_are_identity(self):
        for case in ('occupied_delivery','disabled','config'):
            with self.subTest(case=case):
                obs,route=self.fixture()
                if case=='occupied_delivery':
                    for row in route[33:48]:row['market']=[['SELL','WHEAT',0]]
                p,owner=self.install(route,enabled=case!='disabled')
                if case=='config':owner.configure({'maxMarketOrdersPerTurn':5})
                self.assertEqual(p.act(obs),route[33])

    def test_unreturned_departure_never_commits_a_collection(self):
        obs,route=self.fixture();p,owner=self.install(route)
        out=p.act(obs);returned=deepcopy(out);returned['farmer']=['PASS']
        owner.finish(obs,returned);self.assertEqual(owner._committed['plans'],{})
        owner.idle_fertilizer=False
        self.assertEqual(p.act(self.advance(obs,returned))['farmer'],['PASS'])

    def test_changed_target_cancels_collection_but_preserves_return_path(self):
        obs,route=self.fixture();p,owner=self.install(route)
        out=p.act(obs);out=owner.guard_returned(obs,out);owner.finish(obs,out);obs=self.advance(obs,out,owner)
        obs['farms'][0]['tiles'][3][4]['fertilizer_available']=False
        out=p.act(obs);self.assertEqual(out['farmer'],['PASS'])
        out=owner.guard_returned(obs,out);owner.finish(obs,out);obs=self.advance(obs,out,owner)
        out=p.act(obs);self.assertEqual(out['farmer'],['SOUTH'])
        out=owner.guard_returned(obs,out);owner.finish(obs,out);obs=self.advance(obs,out,owner)
        self.assertEqual(obs['farms'][0]['farmer'],[4,4])
        self.assertEqual(obs['private']['inventories'][0],{})

    def test_existing_owner_survives_reconstruction_and_locked_shed_access(self):
        obs,route=self.fixture();obs['farms'][0]['tiles'][4][4]='LOCKED'
        p,owner=self.install(route);out=p.act(obs);out=owner.guard_returned(obs,out);owner.finish(obs,out);obs=self.advance(obs,out,owner)
        p=Controller(route);owner.install(p)
        for expected in ('COLLECT_FERTILIZER','SOUTH','DROP'):
            out=p.act(obs);self.assertEqual(out['farmer'],[expected])
            out=owner.guard_returned(obs,out);owner.finish(obs,out);obs=self.advance(obs,out,owner)
        self.assertEqual(obs['private']['shed']['FERTILIZER'],1)
        obs['step']=0;obs['day']=0;obs['hour']=0;owner.idle_fertilizer=False
        p.act(obs);self.assertEqual(owner.plans,{})

    def test_final_return_move_fallback_keeps_ownership_until_observed_rejoin(self):
        obs,route=self.fixture();obs['farms'][0]['farmer']=[4,3]
        p,owner=self.install(route)
        for expected in ('COLLECT_FERTILIZER','SOUTH','DROP','NORTH'):
            out=p.act(obs);self.assertEqual(out['farmer'],[expected])
            if expected=='NORTH':out['farmer']=['PASS']
            out=owner.guard_returned(obs,out);owner.finish(obs,out);obs=self.advance(obs,out,owner)
        self.assertEqual(obs['step'],37);self.assertEqual(obs['farms'][0]['farmer'],[4,4])
        out=p.act(obs);self.assertEqual(out['farmer'],['NORTH'])
        out=owner.guard_returned(obs,out);owner.finish(obs,out);obs=self.advance(obs,out,owner)
        self.assertEqual(p.act(obs)['farmer'],['PASS']);self.assertEqual(owner.plans,{})
        self.assertEqual(obs['farms'][0]['farmer'],[4,3])

    def test_missed_return_and_drop_recover_in_certified_idle_slack(self):
        obs,route=self.fixture();p,owner=self.install(route)
        for _ in range(4):
            out=p.act(obs)
            if obs['step'] in (35,36):out['farmer']=['PASS']
            out=owner.guard_returned(obs,out);owner.finish(obs,out);obs=self.advance(obs,out,owner)
        self.assertEqual(obs['step'],37);self.assertEqual(obs['farms'][0]['farmer'],[4,3])
        for expected in ('SOUTH','DROP'):
            out=p.act(obs);self.assertEqual(out['farmer'],[expected])
            out=owner.guard_returned(obs,out);owner.finish(obs,out);obs=self.advance(obs,out,owner)
        self.assertEqual(p.act(obs)['farmer'],['PASS'])
        self.assertEqual(obs['private']['shed']['FERTILIZER'],1)

    def test_next_day_purchase_needs_no_future_sale_credit(self):
        obs,route=self.fixture();obs['private']['shed']={'WHEAT':98}
        route[47]['market']=[];route[50]['market']=[['BUY_PRODUCT','WHEAT',2]]
        p,owner=self.install(route)
        for _ in range(4):
            out=p.act(obs);owner.finish(obs,out);obs=self.advance(obs,out,owner)
        self.assertEqual(sum(obs['private']['shed'].values()),98)
        self.assertIsNone(owner.sale_obligation)

    def test_later_sale_requests_are_not_a_stock_clearance_certificate(self):
        obs,route=self.fixture();p,owner=self.install(route)
        p.act(obs)
        self.assertEqual(owner.plans[0]['sale_outlet_step'],36)
        self.assertEqual(owner.plans[0]['outlet_certificate']['future_sale_credit'],0)

    def test_future_feed_or_fertilizer_use_declines_the_salvage_certificate(self):
        for case in ('fed','future_feed','same_day_input'):
            with self.subTest(case=case):
                obs,route=self.fixture()
                if case=='fed':obs['farms'][0]['tiles'][3][4]['fed_today']=True
                if case=='future_feed':
                    obs['farms'][0]['hands'][0]=[4,3];route[40]['hands'][0]=['FEED']
                if case=='same_day_input':route[40]['hands'][0]=['PICKUP','FERTILIZER',1]
                p,owner=self.install(route)
                self.assertEqual(p.act(obs),route[33]);self.assertEqual(owner.plans,{})

    def at_delivery(self):
        obs,route=self.fixture();p,owner=self.install(route)
        for _ in range(3):
            out=p.act(obs);owner.finish(obs,out);obs=self.advance(obs,out,owner)
        return obs,route,p,owner

    def test_moved_sale_binds_final_slot_without_cancelling_deposit(self):
        obs,route,p,owner=self.at_delivery()
        obs['private']['shed']['MILK']=14
        out=p.act(obs);out['market'].append(['SELL','MILK',14])
        pressure=T.TitanAgent(T.Features(market_pressure=True))
        moved=pressure._market_pressure_selected(obs,{},out)
        self.assertEqual(moved['market'],[['SELL','MILK',14],['SELL','FERTILIZER',1]])
        guarded=owner.guard_returned(obs,moved)
        self.assertEqual(guarded,moved)
        owner.finish(obs,guarded)
        self.assertEqual(owner.sale_obligation['slot'],1)
        after=self.advance(obs,guarded,owner)
        self.assertEqual(after['private']['shed']['FERTILIZER'],1)
        self.assertIsNone(owner.sale_obligation)

    def test_partial_pair_cancels_both_parts_and_preserves_unrelated_lot(self):
        for remove in ('deposit','sale'):
            with self.subTest(remove=remove):
                obs,route,p,owner=self.at_delivery();out=p.act(obs)
                out['market']=[['SELL','MILK',2],['SELL','FERTILIZER',1]]
                if remove=='deposit':out['farmer']=['PASS']
                else:out['market'][1]=[]
                guarded=owner.guard_returned(obs,out)
                self.assertEqual(guarded['farmer'],['PASS'])
                self.assertEqual(guarded['market'],[['SELL','MILK',2],[]])
                owner.finish(obs,guarded)
                self.assertEqual(owner.sale_obligation['status'],'carried')

    def test_only_one_idle_job_may_be_in_flight(self):
        obs,route=self.fixture()
        obs['farms'][0]['hands'][0]=[5,4]
        obs['farms'][0]['tiles'][3][5]=deepcopy(obs['farms'][0]['tiles'][3][4])
        p,owner=self.install(route)
        for _ in range(3):
            out=p.act(obs)
            self.assertEqual(len(owner.plans),1)
            self.assertEqual(out['hands'],[['PASS']])
            owner.finish(obs,out);obs=self.advance(obs,out,owner)

    def test_collected_stock_survives_unsent_delivery_and_eod(self):
        obs,route,p,owner=self.at_delivery()
        out=p.act(obs);out['market']=[];out=owner.guard_returned(obs,out)
        owner.finish(obs,out);obs=self.advance(obs,out,owner)
        self.assertEqual(owner.sale_obligation['status'],'carried')
        # Retain an actual carried unit through repeated absent delivery; this
        # boundary check applies only the documented free EOD transfer.
        obs['step']=47;obs['day']=1;obs['hour']=23
        passed={'farmer':['PASS'],'hands':[['PASS']],'market':[]}
        owner.finish(obs,passed)
        self.assertEqual(owner.sale_obligation['eod_transfer_step'],47)
        obs['private']['shed']['FERTILIZER']+=1
        obs['private']['inventories']=[{}]
        obs['farms'][0]['farmer']=[4,4];obs['farms'][0]['hands']=[]
        obs['step']=48;obs['day']=2;obs['hour']=0
        route[48]['hands']=[]
        p=Controller(route);owner.install(p)
        out=p.act(obs)
        self.assertEqual(out['market'],[['SELL','FERTILIZER',1]])
        self.assertEqual(owner.sale_obligation['status'],'observed_deposit')
        out=owner.guard_returned(obs,out);owner.finish(obs,out)
        after=self.advance(obs,out,owner)
        self.assertIsNone(owner.sale_obligation)
        self.assertEqual(after['private']['shed']['FERTILIZER'],1)

    def test_eod_entitlement_does_not_sell_replacement_stock_after_unbound_transfer(self):
        for transfer in ('pickup','sale','purchase'):
            with self.subTest(transfer=transfer):
                obs,route=self.fixture(50);p,owner=self.install(route)
                owner.sale_obligation={'step':34,'player':0,'worker':0,'quantity':1,
                    'target':(4,3),'status':'observed_deposit','eod_transfer_step':47}
                returned=deepcopy(route[50])
                if transfer=='pickup':returned['farmer']=['PICKUP','FERTILIZER',2]
                if transfer=='sale':returned['market']=[['SELL','FERTILIZER',1]]
                if transfer=='purchase':returned['market']=[['BUY_PRODUCT','FERTILIZER',1]]
                owner.finish(obs,returned,post=None)
                self.assertEqual(owner.sale_obligation['status'],'stock_unknown')
                # Present replacement stock is not proof that the old unit
                # survived the unbound consuming action.
                obs['step']=52;obs['hour']=4;obs['private']['shed']['FERTILIZER']=1
                obs['private']['inventories']=[{},{}]
                self.assertEqual(p.act(obs)['market'],[])
                self.assertEqual(owner.plans,{})


class IdleFertilizerRuntimeTests(unittest.TestCase):
    def fixture(self):
        helper=IdleFertilizerTests();obs,route=helper.fixture()
        agent=T.TitanAgent(T.Features(seed=False,funding=False,idle_fertilizer=True,
                                      operating_stock=True,market_pressure=True))
        agent._initialize();agent.controller=Controller(route)
        agent.production=agent.controller;agent.consumer.controller=agent.controller
        agent.spatial.install(agent.controller)
        return helper,obs,agent

    def test_one_projection_and_one_shared_fill_ledger(self):
        helper,obs,agent=self.fixture()
        import frozen_selected
        with patch.object(frozen_selected,'post_units',wraps=frozen_selected.post_units) as units:
            for expected in ('NORTH','COLLECT_FERTILIZER','SOUTH','DROP'):
                out=agent.act(obs,{})
                self.assertEqual(out['farmer'],[expected],agent.diagnostics)
                obs=helper.advance(obs,out)
            self.assertEqual(units.call_count,4)
        self.assertEqual(agent.controller.calls,4)
        ledger=agent.history.bridge.ledger
        with patch.object(ledger,'record',wraps=ledger.record) as record,patch.object(ledger,'observe',wraps=ledger.observe) as observe:
            agent.act(obs,{})
        self.assertEqual((record.call_count,observe.call_count),(1,1))
        self.assertIsNone(agent.spatial.sale_obligation)
        self.assertEqual(agent.spatial.receipt_events[-1]['sold_units'],1)
        self.assertIsNone(agent.history.selector)
        self.assertFalse(agent.history.terminal_enabled)

    def test_prelude_fallback_returns_and_sells_already_collected_unit(self):
        helper,obs,agent=self.fixture()
        for _ in range(2):obs=helper.advance(obs,agent.act(obs,{}))
        for expected in ('SOUTH','DROP','PASS'):
            out=agent.act(obs,{},entry_started=time.perf_counter()-2)
            self.assertEqual(out['farmer'],[expected],agent.diagnostics)
            self.assertEqual(out['market'],[['SELL','FERTILIZER',1]] if expected=='DROP' else [])
            obs=helper.advance(obs,out)
        self.assertEqual(obs['private']['shed']['FERTILIZER'],1)
        self.assertEqual(obs['private']['inventories'][0],{})
        self.assertEqual(agent.spatial.sale_obligation['status'],'awaiting_observed_fill')
        self.assertFalse(any(e.get('sold_units')==1 for e in agent.spatial.receipt_events))

    def test_cancel_after_selected_keeps_joint_pair_without_stale_snapshot(self):
        helper,obs,agent=self.fixture()
        for _ in range(3):obs=helper.advance(obs,agent.act(obs,{}))
        timer_type=T.deadline._DeadlineTimer;timers=[]
        def timer(seconds):
            obj=timer_type(seconds);timers.append(obj);return obj
        def cancel(*args):raise timers[-1].expired
        with patch.object(T.deadline,'_DeadlineTimer',side_effect=timer),patch.object(agent,'transform_selected',side_effect=cancel):
            out=agent.act(obs,{})
        self.assertEqual(out['farmer'],['DROP'])
        self.assertEqual(out['market'],[['SELL','FERTILIZER',1]])
        self.assertIsNone(agent.history.pending)
        obs=helper.advance(obs,out)
        route=deepcopy(agent.controller.R['case'])
        agent._initialize()
        agent.controller=Controller(route);agent.production=agent.controller
        agent.consumer.controller=agent.controller;agent.spatial.install(agent.controller)
        agent.act(obs,{})
        self.assertEqual(agent.spatial.sale_obligation['status'],'fill_unknown')
        self.assertEqual(obs['private']['shed']['FERTILIZER'],1)

    def terminal_fixture(self,step=715):
        """Keep the real initialized producer, its settlement and FrozenSelected."""
        from scheduler import parent
        helper=IdleFertilizerTests();obs,_=helper.fixture(step=step)
        obs['private']['shed'].pop('FERTILIZER')
        agent=T.TitanAgent(T.Features(seed=False,funding=False,idle_fertilizer=True,
                                      operating_stock=True,market_pressure=True))
        # Isolate the fixture's route bytes without replacing the controller or
        # either installed method. All actual market rows stay unmodified.
        with patch.object(parent,'_ROUTES',deepcopy(parent.routes())):
            agent._initialize()
        route=agent.controller.R[agent.controller.cur]
        for now in range(step,719):
            route[now]=deepcopy(route[now])
            route[now]['farmer']=['PASS'];route[now]['hands']=[['PASS']]
        return helper,obs,agent,route

    def test_real_terminal_producer_reuses_one_owned_sale_and_one_receipt(self):
        helper,obs,agent,route=self.terminal_fixture()
        inherited=deepcopy(route[718]['market'])
        self.assertTrue(inherited)
        import frozen_selected
        with patch.object(frozen_selected,'post_units',wraps=frozen_selected.post_units) as projections:
            for expected in ('NORTH','COLLECT_FERTILIZER','SOUTH','DROP'):
                with patch.object(agent.spatial,'_deliver_idle_fertilizer',
                                  wraps=agent.spatial._deliver_idle_fertilizer) as delivery:
                    out=agent.act(obs,{})
                self.assertEqual(out['farmer'],[expected],agent.diagnostics)
                if obs['step']==718:
                    producer_market=delivery.call_args.args[1]['market']
                    self.assertEqual(agent.selected['market'],producer_market)
                    self.assertEqual(agent.selected['farmer'],['DROP'])
                    fertilizer=[(j,a) for j,a in enumerate(out['market'])
                                if a and a[:2]==['SELL','FERTILIZER']]
                    self.assertEqual(len(fertilizer),1)
                    slot,order=fertilizer[0]
                    self.assertEqual(order,['SELL','FERTILIZER',1])
                    self.assertEqual(agent.spatial.sale_obligation['slot'],slot)
                    self.assertEqual(agent.consumer.selected_post_units_binding,
                                     (718,0,out['farmer'],out['hands']))
                obs=helper.advance(obs,out)
            self.assertEqual(projections.call_count,4)
        self.assertEqual(route[718]['market'],inherited)
        self.assertEqual(obs['private']['inventories'][0],{})
        self.assertEqual(obs['private']['shed'].get('FERTILIZER',0),0)
        # Reconcile the final emitted row directly; 719 is not a playable turn.
        agent.history.observe(obs)
        agent.spatial.observe_market_receipt(obs,agent.history.fill_result)
        self.assertIsNone(agent.spatial.sale_obligation)
        self.assertEqual(len(agent.spatial.receipt_events),1)
        self.assertEqual(agent.spatial.receipt_events[0]['sold_units'],1)

    def test_terminal_job_rejects_baseline_fertilizer_or_other_sources_before_departure(self):
        for case in ('shed','other_carry','collection','purchase','input','barrier','placeholder'):
            with self.subTest(case=case):
                helper,obs,agent,route=self.terminal_fixture()
                if case=='shed':obs['private']['shed']['FERTILIZER']=1
                if case=='other_carry':obs['private']['inventories'][1]={'FERTILIZER':1}
                if case=='collection':route[716]['hands'][0]=['COLLECT_FERTILIZER']
                if case=='purchase':route[716]['market'].append(['BUY_PRODUCT','FERTILIZER',1])
                if case=='input':route[716]['hands'][0]=['PICKUP','FERTILIZER',1]
                if case=='barrier':route[718]['market'].append(['BUY_SEED','CARROT',1])
                if case=='placeholder':route[718]['market'].append([])
                out=agent.act(obs,{})
                self.assertEqual(out['farmer'],['PASS'])
                self.assertFalse(any(p.get('kind')=='idle_fertilizer'
                                     for p in agent.spatial.plans.values()))

    def test_terminal_delivery_does_not_relabel_or_duplicate_a_larger_lot(self):
        helper,obs,agent,route=self.terminal_fixture()
        for _ in range(3):obs=helper.advance(obs,agent.act(obs,{}))
        for market in ([['SELL','FERTILIZER',2]],
                       [['SELL','FERTILIZER',1],['SELL','FERTILIZER',1]],
                       [['BUY_PRODUCT','FERTILIZER',1],['SELL','FERTILIZER',1]]):
            selected={'farmer':['DROP'],'hands':[['PASS']],'market':deepcopy(market)}
            out=agent.spatial._deliver_idle_fertilizer(obs,selected,agent.controller)
            self.assertEqual(out['farmer'],['PASS'])
            self.assertEqual(out['market'],market)
        selected={'farmer':['DROP'],'hands':[['PICKUP','FERTILIZER',1]],
                  'market':[['SELL','FERTILIZER',1]]}
        out=agent.spatial._deliver_idle_fertilizer(obs,selected,agent.controller)
        self.assertEqual(out['farmer'],['PASS'])
        self.assertEqual(out['hands'],selected['hands'])

    def test_delayed_owned_delivery_revalidates_the_final_outlet(self):
        helper,obs,agent,route=self.terminal_fixture(step=712)
        route[715]['market']=[]
        for expected in ('NORTH','COLLECT_FERTILIZER','SOUTH'):
            out=agent.act(obs,{})
            self.assertEqual(out['farmer'],[expected],agent.diagnostics)
            obs=helper.advance(obs,out)
        self.assertFalse(agent.spatial.plans[0]['outlet_certificate']['terminal_reuse'])
        # The real producer offers the deposited unit at each intermediate
        # turn. The ordinary empty-market guard keeps the unit carried until
        # a supported outlet; ownership must survive this missed delivery.
        for _ in range(3):
            out=agent.act(obs,{})
            self.assertEqual(out['farmer'],['PASS'])
            obs=helper.advance(obs,out)
        self.assertEqual(obs['step'],718)
        out=agent.act(obs,{})
        self.assertEqual(out['farmer'],['DROP'],agent.diagnostics)
        self.assertEqual(out['market'],[['SELL','FERTILIZER',1]])
        obs=helper.advance(obs,out)
        agent.history.observe(obs)
        agent.spatial.observe_market_receipt(obs,agent.history.fill_result)
        self.assertIsNone(agent.spatial.sale_obligation)
        self.assertEqual(agent.spatial.receipt_events[-1]['sold_units'],1)


if __name__=='__main__':unittest.main()
