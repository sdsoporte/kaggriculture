# SPDX-License-Identifier: Apache-2.0
"""New full dispatch/history/terminal boundaries on constructed official states."""
import copy,json,time,unittest
from unittest.mock import patch
from pathlib import Path
from titan_runtime import TitanAgent,Features
from test_ordered_selected_sell import OrderedSelectedSellTests,action

ROOT=Path(__file__).resolve().parent
RESULTS=[]
HYP={'slot_templates':[{'id':'wheat-first','origin':'explicit order hypothesis','slots':['WHEAT','MILK']}],
     'unobserved_lots':[{'id':'wheat17','origin':'explicit stock hypothesis','stock':{'WHEAT':17,'FERTILIZER':0}}]}

class Joined(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  OrderedSelectedSellTests.setUpClass();cls.h=OrderedSelectedSellTests()
 def actor(self,hyp=HYP):
  obj=TitanAgent(Features(terminal_history=True,history_hypotheses=hyp));obj._initialize();return obj
 def train(self,obj,seat=0):
  # These are actual prior public transitions: no rival private stock is fed
  # to the runtime bridge. Their zero non-operating supply is observed evidence.
  for step in (646,670,694):
   obs,cfg,state,env=self.h.fixture(step)
   obs=state[seat].observation
   final=action(hands=[['PASS']] if seat==0 else [])
   obj.history.remember(obs,cfg,final,copy.deepcopy(obs))
   self.h.advance(state,env,final,step)
   after=copy.deepcopy(state[seat].observation)
   after['step']=step+1  # The official runner supplies the next decision index.
   obj.history.observe(after)
   self.assertEqual(obj.history.diagnostics['observed_fills']['status'],'recorded')
 def test_actual_dispatch_changes_terminal_order_using_completed_history(self):
  for seat in (0,1):
   obj=self.actor();self.train(obj,seat)
   obs,cfg,state,env=self.h.fixture(718,{'WHEAT':2,'MILK':2})
   obs=state[seat].observation
   obs['private']['shed']={'WHEAT':2,'MILK':2}
   obs['farms'][seat]['money']=100030;obs['farms'][1-seat]['money']=100000
   selected=action(hands=[['PASS']] if seat==0 else [],market=[['SELL','MILK',2],['SELL','WHEAT',2]])
   obj.production.act=lambda _:copy.deepcopy(selected)
   import frozen_selected
   original=frozen_selected.post_units
   with patch.object(obj.production,'act',wraps=obj.production.act) as calls,patch.object(frozen_selected,'post_units',wraps=original) as units:
    start=time.perf_counter();out=obj.act(obs,cfg);elapsed=time.perf_counter()-start
   self.assertEqual(calls.call_count,1);self.assertEqual(units.call_count,1)
   diag=obj.history.diagnostics
   self.assertTrue(diag['family']['ready']);self.assertTrue(diag['terminal_inputs']['complete'])
   self.assertTrue(diag['changed'],diag)
   self.assertEqual(out['market'][0],['SELL','WHEAT',2])
   packet=diag['terminal_inputs'];pairs=[]
   for p in packet['plans']:
    sc=packet['scenarios'][0]
    # Independent native interpreter on the complete returned action.
    s,e=copy.deepcopy((state,env));s[1-seat].observation['private']['shed']=copy.deepcopy(sc['shed'])
    s[seat].action=copy.deepcopy(p['action']);s[1-seat].action=action(market=sc['market'])
    self.h.engine.interpreter(s,e)
    actual=[s[0].observation['farms'][i]['money'] for i in (seat,1-seat)]
    receipt=next(r for r in packet['document']['receipts'] if r['plan']==p['id'])
    self.assertEqual(actual,[receipt['own_cash'],receipt['rival_cash']]);pairs.append({'plan':p['id'],'cash':actual})
   self.assertEqual(obj.history.pending[2],out)
   RESULTS.append({'case':'economically_active_single_parent_history_join','seat':seat,'seconds':elapsed,'selected':selected,'output':out,'cash_pairs':pairs,'parent_calls':calls.call_count,'unit_stages':units.call_count})
 def test_unready_history_calls_no_terminal_producer_or_selector(self):
  obj=self.actor();obs,cfg,_,_=self.h.fixture(718,{'WHEAT':2,'MILK':2})
  selected=action(hands=[['PASS']],market=[['SELL','MILK',2],['SELL','WHEAT',2]])
  obj.production.act=lambda _:copy.deepcopy(selected)
  with patch.object(obj.history.inputs,'build_terminal_inputs',side_effect=AssertionError('unready producer')),patch.object(obj.history.selector,'transform_terminal',side_effect=AssertionError('unready selector')):
   out=obj.act(obs,cfg)
  self.assertFalse(obj.history.diagnostics['family']['ready'])
  self.assertEqual(out,selected)
  RESULTS.append({'case':'unready_family_retains_complete_selected_action','passed':True})
 def test_timeout_records_returned_queue_with_completed_snapshot(self):
  obj=self.actor();obs,cfg,_,_=self.h.fixture(100,{'CARROT':1})
  selected=action(hands=[['PASS']],market=[['SELL','CARROT',1]])
  obj.production.act=lambda _:copy.deepcopy(selected)
  def stop(*args,**kw):
   while True:pass
  obj.features=Features(terminal_history=True,history_hypotheses=HYP,budget_seconds=.04,reserve_seconds=.01)
  obj.history.transform=stop
  start=time.perf_counter();out=obj.act(obs,cfg);elapsed=time.perf_counter()-start
  self.assertEqual(out,selected);self.assertEqual(obj.history.pending[2],selected)
  self.assertEqual(obj.diagnostics['fallback_stage'],'terminal_history');self.assertLess(elapsed,1)
  RESULTS.append({'case':'deadline_records_actual_fallback','seconds':elapsed})
 def test_seed_funding_and_history_share_the_selected_unit_snapshot(self):
  obj=self.actor();obs,cfg,state,env=self.h.fixture(100)
  selected=action(['PLANT','WHEAT'],hands=[['PASS']],market=[['BUY_SEED','WHEAT',17],['HIRE']])
  route=[action(hands=[['PASS']]) for _ in range(720)]
  route[100]=selected;route[101]=action(['PLANT','WHEAT'],hands=[['PASS']])
  obj.controller.R={'case':route};obj.controller.cur='case';obj.seed_budget=obj.seed_budget.__class__(obj.controller.R)
  obj.production.act=lambda _:copy.deepcopy(selected)
  obs['private']['seeds']['WHEAT']=1
  x,y=obs['farms'][0]['farmer'];obs['farms'][0]['tiles'][y][x]=None
  import scheduler,frozen_selected
  original=frozen_selected.post_units
  with patch.object(scheduler,'post_units',side_effect=AssertionError('second current unit stage')),patch.object(frozen_selected,'post_units',wraps=original) as units:
   out=obj.act(obs,cfg)
  self.assertEqual(units.call_count,1)
  self.assertEqual(out['market'],[['BUY_SEED','WHEAT',1],['HIRE']])
  self.assertEqual(obj.history.pending[3]['private']['seeds']['WHEAT'],0)
  self.assertEqual(obj.history.pending[2],out)
  self.assertEqual(obj.diagnostics['seed_funding']['status'],'certified')
  self.h.advance(state,env,out,100)
  self.assertEqual(state[0].observation['private']['seeds']['WHEAT'],1)
  self.assertEqual(len(state[0].observation['farms'][0]['hands']),2)
  RESULTS.append({'case':'seed_funding_history_shared_snapshot','unit_stages':1,'seed_buy':1,'official_hands':2})
 def test_partial_receipts_keep_selected_queue(self):
  obj=self.actor();self.train(obj);obs,cfg,_,_=self.h.fixture(718,{'WHEAT':2,'MILK':2})
  selected=action(hands=[['PASS']],market=[['SELL','MILK',2],['SELL','WHEAT',2]])
  obj.production.act=lambda _:copy.deepcopy(selected)
  actual=obj.history.inputs.build_terminal_inputs
  def limited(*args,**kw):kw['max_cells']=1;return actual(*args,**kw)
  with patch.object(obj.history.inputs,'build_terminal_inputs',side_effect=limited),patch.object(obj.history.selector,'transform_terminal',side_effect=AssertionError('partial selector')):
   out=obj.act(obs,cfg)
  self.assertEqual(out,selected);self.assertFalse(obj.history.diagnostics['terminal_inputs']['complete'])
  RESULTS.append({'case':'partial_native_table_retains_selected','passed':True})

if __name__=='__main__':
 suite=unittest.defaultTestLoader.loadTestsFromTestCase(Joined)
 result=unittest.TextTestRunner(verbosity=2).run(suite)
 (ROOT/'runtime/integrated-selected').mkdir(parents=True,exist_ok=True)
 (ROOT/'runtime/integrated-selected/HISTORY-TESTS.json').write_text(json.dumps({'tests':result.testsRun,'passed':result.wasSuccessful(),'cases':RESULTS,'new_games':0},indent=2)+'\n')
 raise SystemExit(0 if result.wasSuccessful() else 1)
