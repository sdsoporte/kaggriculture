# SPDX-License-Identifier: Apache-2.0
"""Derived route cache semantics, cancellation, isolation and measured recovery."""
import copy,json,time,unittest
from pathlib import Path
from unittest.mock import patch
import titan_runtime as T
from scheduler import parent
B=T.load('_titan_seed_budget',T.HERE/'reference/integrated-selected/alder/seed_budget.py',cache=True)
ROOT=Path(__file__).resolve().parent
ROWS=[]
def routes():
 return {'a':[{'farmer':['PASS'],'market':[]},{'farmer':['PLANT','CARROT'],'hands':[]}],
         'b':[{'farmer':['PASS'],'market':[]},{'farmer':['PLANT','WHEAT'],'hands':[]}]}
class DerivedTests(unittest.TestCase):
 def setUp(self):B._DERIVED_CACHE.clear()
 def test_content_changes_and_nested_mutation(self):
  r=routes();a=B.SeedBudget(r);b=B.SeedBudget(copy.deepcopy(r))
  self.assertIs(a.suffixes,b.suffixes)
  r['b'][0]['market']=[['SELL','MILK',1]];c=B.SeedBudget(r)
  self.assertEqual(a.remaining('WHEAT',0,'a'),1);self.assertEqual(c.remaining('WHEAT',0,'a'),0)
  r['a'][1]['farmer'][1]='POTATO';d=B.SeedBudget(r)
  self.assertEqual(a.remaining('CARROT',0,'a'),1);self.assertEqual(d.remaining('CARROT',0,'a'),0)
  r['a'].append({'farmer':['PLANT','POTATO']});e=B.SeedBudget(r)
  self.assertEqual(d.remaining('POTATO',0,'a'),1);self.assertEqual(e.remaining('POTATO',0,'a'),2)
  for i in range(8):B.SeedBudget({str(i):r['a']})
  self.assertLessEqual(len(B._DERIVED_CACHE),B._CACHE_LIMIT)
 def test_immutable_tables_fresh_events_and_no_route_alias(self):
  r=routes();a=B.SeedBudget(r);b=B.SeedBudget(copy.deepcopy(r))
  with self.assertRaises(TypeError):a.suffixes['a'][0]['CARROT']=999
  with self.assertRaises(TypeError):a.prefix_lengths['a','b']=999
  a.apply({'market':[['BUY_SEED','CARROT',99]]},{},0,'a')
  self.assertTrue(a.events);self.assertEqual(b.events,[])
  r['a'][1]['farmer'].clear();self.assertEqual(a.remaining('CARROT',0,'a'),1)
 def test_cancelled_derivation_is_not_published(self):
  class Cancel(BaseException):pass
  derive=B._derive;r=routes()
  def interrupted(r):derive(r);raise Cancel()
  with patch.object(B,'_derive',side_effect=interrupted):
   with self.assertRaises(Cancel):B.SeedBudget(r)
  self.assertEqual(B._DERIVED_CACHE,{})
  a=B.SeedBudget(r);self.assertEqual(a.remaining('CARROT',0,'a'),1)
 def test_exact_shipped_route_semantics_against_original(self):
  old=T.load('_titan_seed_before_cache',ROOT/'reference/historical/seed_budget-before-derived-cache.py')
  r=parent.routes();a=old.SeedBudget(r);b=B.SeedBudget(r)
  self.assertEqual(a.prefix_lengths,b.prefix_lengths)
  for name in r:
   self.assertEqual(list(a.suffixes[name]),list(b.suffixes[name]))
   for step in (-1,0,23,100,669,718,720):
    for crop in ('CARROT','WHEAT','STRAWBERRY','MELON'):
     self.assertEqual(a.remaining(crop,step,name),b.remaining(crop,step,name))
     action={'farmer':['PASS'],'market':[['BUY_SEED',crop,99],['HIRE']]}
     self.assertEqual(a.apply(action,{crop:2},step,name),b.apply(action,{crop:2},step,name))
  self.assertEqual(a.events,b.events)
 def test_match_and_recovery_share_only_derived_tables(self):
  a=T.TitanAgent();start=time.perf_counter();a._initialize();cold=time.perf_counter()-start
  old=a.seed_budget;controller=a.controller;old.events.append({'sentinel':True});a.consumer.pending['MILK']=999
  a.ready=False;start=time.perf_counter();a._initialize();recovery=time.perf_counter()-start
  self.assertTrue(a.seed_budget.suffixes is old.suffixes);self.assertIsNot(a.controller,controller)
  self.assertEqual(a.seed_budget.events,[]);self.assertEqual(a.consumer.pending,{})
  b=T.TitanAgent();b._initialize();self.assertTrue(b.seed_budget.suffixes is old.suffixes)
  self.assertIsNot(a.seed_budget.events,b.seed_budget.events)
  r=parent.routes();uncached=[];cached=[]
  for _ in range(5):
   start=time.perf_counter();B._derive(r);uncached.append(time.perf_counter()-start)
   start=time.perf_counter();B.SeedBudget(r);cached.append(time.perf_counter()-start)
  ROWS.append({'first_initialize_in_test_process_seconds':cold,'recovery_initialize_seconds':recovery,
               'uncached_derive_seconds':uncached,'content_keyed_reuse_seconds':cached})
if __name__=='__main__':
 result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(DerivedTests))
 p=ROOT/'runtime/integrated-selected/SEED-DERIVED-TESTS.json';p.parent.mkdir(parents=True,exist_ok=True)
 p.write_text(json.dumps({'tests':result.testsRun,'passed':result.wasSuccessful(),'timing':ROWS,'new_games':0},indent=2)+'\n')
 raise SystemExit(0 if result.wasSuccessful() else 1)
