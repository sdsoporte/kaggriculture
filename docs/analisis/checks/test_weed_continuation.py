# SPDX-License-Identifier: Apache-2.0
"""Reached weed obligations, native rejoin, and emitted-action boundaries.

ROOT-SIM-A's reported contract informed this implementation. Its unavailable
candidate bytes and reported test totals are not treated as results of this suite.
"""
from copy import deepcopy
import gzip
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import mechanics as m
from scheduler import parent
from spatial_tempo import SpatialTempo, unit
from titan_runtime import TitanAgent, Features, load
import test_engine_semantics as semantics

HERE=Path(__file__).resolve().parent
ROOT=HERE.parent if HERE.name=='checks' else HERE
BUDGET=load('_weed_test_seed_budget',ROOT/'reference/integrated-selected/alder/seed_budget.py').SeedBudget
PASS={'farmer':['PASS'],'hands':[['PASS'],['PASS']],'market':[]}


class CountingParent(parent.Agent):
    def __init__(self):
        super().__init__();self.calls=0

    def act(self,obs):
        self.calls+=1
        return super().act(obs)


class WeedContinuationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        semantics.EngineSemantics.setUpClass()
        cls.helper=semantics.EngineSemantics()
        cls.engine=cls.helper.engine
        cls.ev=cls.helper.ev

    def fixture(self,obligation=('BUILD_PASTURE',),step=28,seeds=8):
        state,env=self.helper.fixture(step=step,cash=100000)
        obs=deepcopy(state[0].observation)
        farm=obs['farms'][0]
        farm['farmer']=[2,4];farm['hands']=[[4,4],[3,3]]
        obs['private']['inventories']=[{},{},{}]
        obs['private']['seeds']['WHEAT']=seeds
        farm['tiles'][4][2]={'kind':'WEED'}
        farm['tiles'][3][2]=m._new_plant('WHEAT',step//24,24)
        route=[deepcopy(PASS) for _ in range(720)]
        route[step]['farmer']=list(obligation)
        suffix=[['NORTH'],['WATER'],['SOUTH'],['PASS']]
        if obligation[0]=='PLANT':suffix=[['WATER'],['NORTH'],['SOUTH'],['PASS']]
        for t,a in enumerate(suffix,step+1):
            route[t]['farmer']=a
            route[t]['market']=[['SELL','WHEAT',0],['BUY_PRODUCT','WHEAT',1]]
        return obs,dict(env.configuration),route

    def controller(self,route,alternatives=None):
        p=CountingParent();p.R={'case':route,**(alternatives or {})};p.cur='case'
        budget=BUDGET(p.R)
        owner=SpatialTempo(m,pathing=False,tempo=False,seed_reserve=budget.remaining)
        owner.install(p)
        return p,owner,budget

    def advance_units(self,obs,action):
        obs=deepcopy(obs)
        acts=[action['farmer'],*action.get('hands',[])]
        demand={}
        for a in acts:
            if a and a[0]=='PLANT':demand[a[1]]=demand.get(a[1],0)+1
        blocked={c for c,n in demand.items() if n>obs['private']['seeds'].get(c,0)}
        for i,a in enumerate(acts):
            if a and a[0]=='PLANT' and a[1] in blocked:a=['PASS']
            self.engine._apply_unit_action(obs['farms'][obs['player']],obs['private'],i,a,10,obs['day'],24,100)
        self.engine._decay_plants(obs['farms'][obs['player']],obs['step'])
        obs['step']+=1;obs['day']=obs['step']//24;obs['hour']=obs['step']%24
        return obs

    def test_build_rejoins_and_preserves_every_market_and_other_actor(self):
        obs,cfg,route=self.fixture();original=deepcopy(route)
        p,owner,_=self.controller(route)
        first=p.act(obs);self.assertEqual(first['farmer'],['DIG']);owner.finish(obs,first)
        obs=self.advance_units(obs,first)
        second=p.act(obs);self.assertEqual(second['farmer'],['BUILD_PASTURE'])
        self.assertEqual(p.calls,2)
        for t in range(28,34):
            self.assertEqual(p.R['case'][t]['market'],original[t]['market'])
            self.assertEqual(p.R['case'][t]['hands'],original[t]['hands'])
        self.assertEqual(owner.plans[0]['goal'],(2,4))

    def test_plant_uses_only_a_seed_above_later_compatible_branch_demand(self):
        obs,cfg,route=self.fixture(('PLANT','WHEAT'),seeds=3)
        route[622]['farmer']=['PLANT','WHEAT']
        branch=deepcopy(route);branch[623]['farmer']=['PLANT','WHEAT']
        p,owner,budget=self.controller(route,{'branch':branch})
        first=p.act(obs);owner.finish(obs,first)
        self.assertEqual(budget.remaining('WHEAT',28,'case'),2)
        self.assertEqual(owner.future_seed_requests(28),{'WHEAT':1})
        obs=self.advance_units(obs,first)
        out=p.act(obs);self.assertEqual(out['farmer'],['PLANT','WHEAT'])
        obs=self.advance_units(obs,out);owner.finish(dict(obs,step=29),out)
        self.assertEqual(obs['private']['seeds']['WHEAT'],2)

    def test_seed_shortage_later_in_episode_rejects_an_otherwise_legal_retry(self):
        obs,cfg,route=self.fixture(('PLANT','WHEAT'),seeds=2)
        route[622]['farmer']=['PLANT','WHEAT']
        branch=deepcopy(route);branch[623]['farmer']=['PLANT','WHEAT']
        p,owner,_=self.controller(route,{'branch':branch})
        out=p.act(obs);owner.finish(obs,out)
        self.assertEqual(owner.plans,{})
        self.assertEqual(p.R['case'][29]['farmer'],['WATER'])

    def test_same_turn_or_future_seed_purchase_does_not_fund_admission(self):
        for purchase_step in (28,29,620):
            with self.subTest(purchase_step=purchase_step):
                obs,cfg,route=self.fixture(('PLANT','WHEAT'),seeds=1)
                route[622]['farmer']=['PLANT','WHEAT']
                route[purchase_step]['market']=[['BUY_SEED','WHEAT',20]]
                p,owner,_=self.controller(route)
                out=p.act(obs);owner.finish(obs,out)
                self.assertEqual(owner.plans,{})

    def test_no_unwatered_plant_or_shift_across_reset_branch_or_hire(self):
        for case in ('unwatered','reset','branch','hire','no_pass','resource_action'):
            with self.subTest(case=case):
                step=190 if case=='reset' else 224 if case=='branch' else 28
                obs,cfg,route=self.fixture(('PLANT','WHEAT'),step=step)
                if case=='unwatered':route[step+1]['farmer']=['PASS']
                if case=='hire':route[step+2]['market']=[['HIRE']]
                if case=='no_pass':
                    for t in range(step+1,(step//24+1)*24):route[t]['farmer']=['WATER']
                if case=='resource_action':route[step+1]['farmer']=['DROP']
                p,owner,_=self.controller(route)
                out=p.act(obs);owner.finish(obs,out)
                self.assertEqual(owner.plans,{})

    def test_shared_service_is_not_silently_delayed(self):
        obs,cfg,route=self.fixture()
        obs['farms'][0]['hands'][0]=[2,3]
        route[30]['hands'][0]=['HARVEST']
        p,owner,_=self.controller(route)
        out=p.act(obs);owner.finish(obs,out)
        self.assertEqual(owner.plans,{})

    def test_unreturned_dig_does_not_commit_but_unrelated_rewrite_is_independent(self):
        for actor in (0,1):
            with self.subTest(rewritten_actor=actor):
                obs,cfg,route=self.fixture();p,owner,_=self.controller(route)
                out=p.act(obs);returned=deepcopy(out)
                if actor==0:returned['farmer']=['PASS']
                else:returned['hands'][0]=['NORTH']
                owner.finish(obs,returned)
                obs=self.advance_units(obs,returned)
                next_action=p.act(obs)
                self.assertEqual(next_action['farmer'],['NORTH'] if actor==0 else ['BUILD_PASTURE'])

    def test_interrupted_producer_does_not_publish_edited_routes(self):
        obs,cfg,route=self.fixture();p,owner,_=self.controller(route)
        transform=owner.transform
        def interrupted(*args):
            transform(*args)
            raise RuntimeError('interrupted before producer return')
        with patch.object(owner,'transform',side_effect=interrupted):
            with self.assertRaises(RuntimeError):p.act(obs)
        owner.finish(obs,PASS)
        obs['step']=29;obs['hour']=5
        out=p.act(obs)
        self.assertEqual(out['farmer'],['NORTH'])
        self.assertEqual(owner.plans,{})

    def test_completed_obligation_survives_controller_reconstruction_only(self):
        obs,cfg,route=self.fixture();original=deepcopy(route)
        p,owner,_=self.controller(route);out=p.act(obs);owner.finish(obs,out)
        obs=self.advance_units(obs,out)
        fresh=CountingParent();fresh.R={'case':deepcopy(original)};fresh.cur='case'
        owner.install(fresh)
        self.assertEqual(fresh.act(obs)['farmer'],['BUILD_PASTURE'])
        self.assertEqual(fresh.calls,1)
        obs['step']=0;obs['day']=0;obs['hour']=0
        fresh.act(obs)
        self.assertEqual(fresh.R['case'][29]['farmer'],['NORTH'])
        self.assertEqual(owner.plans,{})

    def test_pending_request_augments_existing_seed_purchase_bound(self):
        obs,cfg,route=self.fixture(('PLANT','WHEAT'),seeds=3)
        route[622]['farmer']=['PLANT','WHEAT']
        p,owner,budget=self.controller(route);out=p.act(obs)
        queue={'farmer':['PASS'],'hands':[],'market':[['BUY_SEED','WHEAT',5]]}
        reduced=budget.apply(queue,{'WHEAT':1},28,'case',
                             extra_requests=owner.future_seed_requests(28))
        self.assertEqual(reduced['market'],[['BUY_SEED','WHEAT',1]])
        self.assertEqual(queue['market'],[['BUY_SEED','WHEAT',5]])

    def test_retry_rechecks_actual_actor_tile_and_seed_stock(self):
        for case in ('occupied','position','missing_actor','seed_spent'):
            with self.subTest(case=case):
                obligation=('PLANT','WHEAT') if case=='seed_spent' else ('BUILD_PASTURE',)
                obs,cfg,route=self.fixture(obligation)
                worker=0
                if case=='missing_actor':
                    worker=2;obs['farms'][0]['hands'][1]=[2,4];obs['farms'][0]['farmer']=[0,0]
                    for row in route:
                        row['hands'][1]=row['farmer'];row['farmer']=['PASS']
                original=deepcopy(route)
                p,owner,_=self.controller(route)
                out=p.act(obs);owner.finish(obs,out)
                self.assertIn(worker,owner.plans)
                obs=self.advance_units(obs,out)
                if case=='occupied':obs['farms'][0]['tiles'][4][2]=m._new_plant('WHEAT',1,24)
                elif case=='position':obs['farms'][0]['farmer']=[1,4]
                elif case=='missing_actor':obs['farms'][0]['hands'].pop()
                else:obs['private']['seeds']['WHEAT']=0
                p.act(obs)
                self.assertNotIn(worker,owner.plans)
                self.assertNotIn(worker,owner.active)
                for step in range(29,33):
                    self.assertEqual(unit(p.R['case'][step],worker),unit(original[step],worker))

    def test_delta_recorded_native_controls_and_current_runtime_rejoin(self):
        fixture=HERE/'reference/weed-continuation/delta-native.json.gz'
        data=json.loads(gzip.decompress(fixture.read_bytes()))
        cfg=data['configuration'];states=data['states'];actions=data['actions']
        def restore(raw):
            state=self.ev.structify(deepcopy(raw))
            for key in ('farms','market','town'):
                state[1].observation[key]=state[0].observation[key]
            return state
        def project(state):
            return [{k:v for k,v in s.items() if k!='action'} for s in state]
        # This first arm checks the original fixture, not the current agent.
        control=restore(states['28'])
        env=self.ev.Struct(configuration=self.ev.structify(deepcopy(cfg)),done=False,info={})
        for step in range(28,47):
            for seat in (0,1):
                control[seat].observation.step=step
                control[seat].observation.remainingOverageTime=0
                control[seat].action=deepcopy(actions[str(step)][seat])
            self.engine.interpreter(control,env)
            self.assertEqual(control,states[str(step+1)],step)
        # Candidate chooses from its own live controller, never from future replay.
        current=restore(states['28'])
        agent=TitanAgent(Features(**json.loads((ROOT/'TITAN-CONFIG.json').read_text())))
        for step in range(28,47):
            for seat in (0,1):
                current[seat].observation.step=step
                current[seat].observation.remainingOverageTime=0
            selected=agent.act(deepcopy(current[1].observation),cfg)
            self.assertEqual(agent.diagnostics['parent_calls'],1)
            self.assertEqual(selected['market'],actions[str(step)][1]['market'],step)
            self.assertEqual(selected['farmer'],actions[str(step)][1]['farmer'],step)
            for i in (0,2,3):self.assertEqual(selected['hands'][i],actions[str(step)][1]['hands'][i],(step,i))
            if step==28:self.assertEqual(selected['hands'][1],['DIG'])
            if step==29:self.assertEqual(selected['hands'][1],['BUILD_PASTURE'])
            current[0].action=deepcopy(actions[str(step)][0]);current[1].action=selected
            self.engine.interpreter(current,env)
        self.assertEqual(current[0].observation.farms[1].tiles[4][2],{'kind':'PASTURE'})
        current[0].observation.farms[1].tiles[4][2]=None
        self.assertEqual(project(current),project(control))
        event=agent.diagnostics['route_events'][0]
        self.assertEqual((event['absorbed_step'],event['end']),(46,47))

    def test_exact_ash_observations_reject_later_seed_starvation_and_reset(self):
        data=json.loads(gzip.decompress((HERE/'reference/weed-continuation/ash-native.json.gz').read_bytes()))
        for case in data['cases']:
            for route in parent.routes():
                with self.subTest(seed=case['seed'],route=route):
                    obs=deepcopy(case['players'][0]['observation'])
                    # Retained framework state keeps the previous step field;
                    # the evaluator supplies the actual call index separately.
                    obs.update(step=case['step'],remainingOverageTime=0)
                    agent=TitanAgent(Features(**json.loads((ROOT/'TITAN-CONFIG.json').read_text())))
                    agent._initialize();agent.controller.cur=route
                    out=agent.act(obs,{})
                    self.assertEqual(out['hands'][5],['DIG'])
                    self.assertFalse(any(p.get('obligation')==['PLANT','WHEAT']
                                         for p in agent.spatial.plans.values()))
                    self.assertEqual(agent.controller.R[route][case['step']+1]['hands'][5],['WATER'])
                    if case['step']==304:
                        self.assertGreaterEqual(agent.seed_budget.remaining('WHEAT',304,route),
                                                obs['private']['seeds']['WHEAT'])

    def test_exact_spruce_completed_dig_restores_only_missing_pasture(self):
        data=json.loads(gzip.decompress((HERE/'reference/weed-continuation/spruce-native.json.gz').read_bytes()))
        state29=next(r['state'] for r in data['window'] if r['kind']=='state' and r['index']==29)
        obs=deepcopy(state29[1]['observation']);obs.update(step=29,remainingOverageTime=0)
        agent=TitanAgent(Features(**json.loads((ROOT/'TITAN-CONFIG.json').read_text())))
        first=agent.act(obs,{})
        self.assertEqual(first['hands'][2],['DIG'])
        self.assertEqual(agent.diagnostics['parent_calls'],1)
        obs=deepcopy(data['fixture']['observation'])
        original=data['fixture']['selected_action']
        result=agent.act(obs,{})
        expected=deepcopy(original);expected['hands'][2]=['BUILD_PASTURE']
        self.assertEqual(result,expected)
        self.assertEqual(agent.diagnostics['parent_calls'],1)
        old=self.advance_units(obs,original);new=self.advance_units(obs,result)
        self.assertEqual(new['farms'][1]['tiles'][2][4],{'kind':'PASTURE'})
        new['farms'][1]['tiles'][2][4]=None
        self.assertEqual(old,new)
        self.assertEqual(agent.controller.R[agent.controller.cur][31]['hands'][2],['SOUTH'])


if __name__=='__main__':unittest.main(verbosity=2)
