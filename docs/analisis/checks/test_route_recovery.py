# SPDX-License-Identifier: Apache-2.0
"""Completed route choice survives cancellation, unfinished choice does not."""
import copy,json,unittest
from pathlib import Path
from unittest.mock import patch
import titan_runtime as T
from scheduler import parent
from test_ordered_selected_sell import OrderedSelectedSellTests
ROOT=Path(__file__).resolve().parent
ROWS=[]
class RouteRecovery(unittest.TestCase):
 @classmethod
 def setUpClass(cls):OrderedSelectedSellTests.setUpClass();cls.h=OrderedSelectedSellTests()
 def obs(self,step):
  obs,cfg,_,_=self.h.fixture(step=step);obs['market']['inventory']['MILK']=10068;return obs,cfg
 def interrupt(self,agent,obs,cfg,production=False):
  Timer=T.deadline._DeadlineTimer;timers=[]
  def timer(seconds):
   obj=Timer(seconds);timers.append(obj);return obj
  original=agent.production.act
  def stop(*args):
   if production:original(*args)
   raise timers[-1].expired
  target=agent.production if production else agent
  method='act' if production else 'transform_selected'
  with patch.object(T.deadline,'_DeadlineTimer',side_effect=timer),patch.object(target,method,side_effect=stop):
   return agent.act(obs,cfg)
 def test_completed_route_survives_later_selected_fallback(self):
  a=T.TitanAgent();b=T.TitanAgent();obs,cfg=self.obs(433)
  self.assertEqual(a.act(obs,cfg),b.act(obs,cfg));self.assertEqual(a.controller.cur,parent.MILK_GLUT)
  old=a.controller;obs,cfg=self.obs(434);out=self.interrupt(a,obs,cfg)
  self.assertEqual(out,a.selected);a._initialize()
  self.assertIsNot(a.controller,old);self.assertEqual(a.controller.cur,parent.MILK_GLUT)
  for step in (577,578):
   obs,cfg=self.obs(step);self.assertEqual(a.act(obs,cfg),b.act(obs,cfg))
  ROWS.append({'case':'completed_route_survives_selected_fallback','route':a.controller.cur,'checked_steps':[433,434,577,578]})
 def test_route_selected_on_fallback_turn_is_retained(self):
  a=T.TitanAgent();a._initialize();obs,cfg=self.obs(433)
  self.interrupt(a,obs,cfg);self.assertEqual(a.controller.cur,parent.MILK_GLUT)
  a._initialize();self.assertEqual(a.controller.cur,parent.MILK_GLUT)
 def test_unreturned_producer_route_is_not_committed(self):
  a=T.TitanAgent();obs,cfg=self.obs(432);a.act(obs,cfg)
  obs,cfg=self.obs(433);out=self.interrupt(a,obs,cfg,production=True)
  self.assertEqual(out,T.deadline.legal_pass(obs));self.assertEqual(a.controller.cur,parent.MILK_GLUT)
  a._initialize();self.assertEqual(a.controller.cur,parent.MAIN)
 def test_new_match_does_not_inherit_route(self):
  a=T.TitanAgent();obs,cfg=self.obs(433);a.act(obs,cfg)
  b=T.TitanAgent();b._initialize();self.assertEqual(b.controller.cur,parent.MAIN)
if __name__=='__main__':
 result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(RouteRecovery))
 p=ROOT/'runtime/integrated-selected/ROUTE-RECOVERY-TESTS.json';p.parent.mkdir(parents=True,exist_ok=True)
 p.write_text(json.dumps({'tests':result.testsRun,'passed':result.wasSuccessful(),'cases':ROWS,'new_games':0},indent=2)+'\n')
 raise SystemExit(0 if result.wasSuccessful() else 1)
