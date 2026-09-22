# SPDX-License-Identifier: Apache-2.0
"""Completed code reuse is separate from mutable cancellation/match state."""
import json,sys,tempfile,time,unittest
from pathlib import Path
from types import ModuleType
from unittest.mock import patch
import titan_runtime as T
from test_ordered_selected_sell import OrderedSelectedSellTests
ROOT=Path(__file__).resolve().parent
ROWS=[]
class ModuleRecovery(unittest.TestCase):
 def test_partial_execution_is_never_reused(self):
  class Cancel(BaseException):pass
  name='_titan_recovery_fixture';old=ModuleType(name);sys.modules[name]=old
  with tempfile.TemporaryDirectory() as folder:
   p=Path(folder)/'module.py';p.write_text('VALUE = 17\n')
   original=T.importlib.util.spec_from_file_location
   def spec(*args):
    s=original(*args)
    def partial(module):module.PARTIAL=True;raise Cancel()
    s.loader.exec_module=partial;return s
   with patch.object(T.importlib.util,'spec_from_file_location',side_effect=spec):
    with self.assertRaises(Cancel):T.load(name,p,cache=True)
   self.assertIs(sys.modules[name],old)
   m=T.load(name,p,cache=True);self.assertEqual(m.VALUE,17);self.assertFalse(hasattr(m,'PARTIAL'))
   self.assertIs(T.load(name,p,cache=True),m)
   q=Path(folder)/'other.py';q.write_text('VALUE = 23\n')
   self.assertEqual(T.load(name,q,cache=True).VALUE,23)
  sys.modules.pop(name,None)
 def test_cancel_reuses_code_but_rebuilds_state(self):
  OrderedSelectedSellTests.setUpClass();h=OrderedSelectedSellTests();obs,cfg,_,_=h.fixture(100)
  agent=T.TitanAgent(T.Features(budget_seconds=.025,reserve_seconds=.01));agent._initialize()
  funding=agent.funding_module;budget_class=type(agent.seed_budget);controller=agent.controller;consumer=agent.consumer;suffixes=agent.seed_budget.suffixes
  agent.seed_budget.events.append({'sentinel':True});consumer.pending['MILK']=999
  def spin(*a):
   while True:pass
  with patch.object(agent,'transform_selected',side_effect=spin):agent.act(obs,cfg)
  self.assertFalse(agent.ready);self.assertEqual(agent.diagnostics['status'],'deadline_fallback')
  started=time.perf_counter();agent._initialize();elapsed=time.perf_counter()-started
  self.assertIs(agent.funding_module,funding);self.assertIs(type(agent.seed_budget),budget_class)
  self.assertIsNot(agent.controller,controller);self.assertIsNot(agent.consumer,consumer)
  self.assertEqual(agent.seed_budget.events,[]);self.assertEqual(agent.consumer.pending,{})
  self.assertTrue(agent.seed_budget.suffixes is suffixes)
  ROWS.append({'case':'recovery_state_reconstruction','seconds':elapsed,'module_identity_reused':True,'mutable_state_rebuilt':True})
 def test_new_match_has_fresh_controller_and_seed_ledger(self):
  a=T.TitanAgent();a._initialize();a.seed_budget.events.append({'sentinel':True});a.consumer.pending['MILK']=999
  b=T.TitanAgent();b._initialize()
  self.assertIs(a.funding_module,b.funding_module);self.assertIsNot(a.controller,b.controller)
  self.assertIsNot(a.seed_budget,b.seed_budget);self.assertEqual(b.seed_budget.events,[]);self.assertEqual(b.consumer.pending,{})
if __name__=='__main__':
 result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(ModuleRecovery))
 p=ROOT/'runtime/integrated-selected/MODULE-RECOVERY-TESTS.json';p.parent.mkdir(parents=True,exist_ok=True)
 p.write_text(json.dumps({'tests':result.testsRun,'passed':result.wasSuccessful(),'cases':ROWS,'new_games':0},indent=2)+'\n')
 raise SystemExit(0 if result.wasSuccessful() else 1)
