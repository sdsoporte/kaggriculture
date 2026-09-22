# SPDX-License-Identifier: Apache-2.0
"""Only the new main-to-existing-timer clock boundary; no timer-suite replay."""
import copy,json,time,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import main
import titan_runtime as T
from test_ordered_selected_sell import OrderedSelectedSellTests,action
ROOT=Path(__file__).resolve().parent
ROWS=[]
class EntryClock(unittest.TestCase):
 @classmethod
 def setUpClass(cls):OrderedSelectedSellTests.setUpClass();cls.h=OrderedSelectedSellTests()
 def test_main_prelude_exhaustion_never_starts_parent(self):
  obs,cfg,_,_=self.h.fixture(0);loads=json.loads;calls=[]
  def delayed(s,*a,**k):
   parsed=loads(s,*a,**k)
   if isinstance(parsed,dict) and parsed.get('consumer')=='frozen':
    parsed.update(budget_seconds=.025,reserve_seconds=.01)
    end=time.perf_counter()+.03
    while time.perf_counter()<end:pass
   return parsed
  original_initialize=T.TitanAgent._initialize
  def initialize(obj):
   calls.append(1)
   return original_initialize(obj)
  main._INSTANCE=None
  with patch.object(json,'loads',side_effect=delayed),patch.object(T.TitanAgent,'_initialize',initialize):
   start=time.perf_counter();out=main.agent(obs,cfg);elapsed=time.perf_counter()-start
  self.assertEqual(calls,[]);self.assertEqual(out,T.deadline.legal_pass(obs))
  self.assertEqual(main._INSTANCE.diagnostics['fallback_stage'],'entrypoint_prelude')
  self.assertGreaterEqual(main._INSTANCE.diagnostics['entrypoint_prelude_seconds'],.03)
  ROWS.append({'case':'count_main_prelude_before_parent','wall_seconds':elapsed,'diagnostics':main._INSTANCE.diagnostics})
 def test_elapsed_includes_fallback_copy_after_timer(self):
  obs,cfg,_,_=self.h.fixture(100);obj=T.TitanAgent(T.Features(budget_seconds=.025,reserve_seconds=.01));obj._initialize()
  selected=action(hands=[['PASS']]);obj.production.act=lambda _:copy.deepcopy(selected)
  def spin(*a):
   while True:pass
  obj.transform_selected=spin
  deep=T.deepcopy
  def copy_after_expiry(value):
   if not obj.ready:
    end=time.perf_counter()+.01
    while time.perf_counter()<end:pass
   return deep(value)
  with patch.object(T,'deepcopy',side_effect=copy_after_expiry):
   start=time.perf_counter();out=obj.act(obs,cfg);elapsed=time.perf_counter()-start
  self.assertEqual(out,selected)
  self.assertGreaterEqual(obj.diagnostics['elapsed_seconds'],.025)
  self.assertLess(abs(elapsed-obj.diagnostics['elapsed_seconds']),.005)
  self.assertGreater(obj.diagnostics['act_cpu_seconds'],0)
  ROWS.append({'case':'fallback_copy_is_in_reported_elapsed','wall_seconds':elapsed,'diagnostics':obj.diagnostics})
if __name__=='__main__':
 result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(EntryClock))
 (ROOT/'runtime/integrated-selected').mkdir(parents=True,exist_ok=True)
 (ROOT/'runtime/integrated-selected/ENTRY-CLOCK-TESTS.json').write_text(json.dumps({'tests':result.testsRun,'passed':result.wasSuccessful(),'cases':ROWS,'failed_cell_reproduction':False,'new_games':0},indent=2)+'\n')
 raise SystemExit(0 if result.wasSuccessful() else 1)
