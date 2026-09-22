# SPDX-License-Identifier: Apache-2.0
"""Thread-local cancellation, restoration, and actual entrypoint recovery."""
import copy,json,signal,sys,threading,time,unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
import titan_runtime as T
from test_ordered_selected_sell import OrderedSelectedSellTests
ROOT=Path(__file__).resolve().parent
ROWS=[]

def spin(*args):
 while True:pass

class WorkerDeadline(unittest.TestCase):
 @classmethod
 def setUpClass(cls):OrderedSelectedSellTests.setUpClass();cls.h=OrderedSelectedSellTests()
 def worker(self,fn):
  with ThreadPoolExecutor(1) as pool:return pool.submit(fn).result(timeout=5)
 def test_actual_entrypoint_no_signal_mutation(self):
  import main
  obs,cfg,_,_=self.h.fixture(0)
  handler=signal.getsignal(signal.SIGALRM)
  def run():
   with patch.object(signal,'signal',side_effect=AssertionError('worker signal mutation')),patch.object(signal,'setitimer',side_effect=AssertionError('worker timer mutation')):
    started=time.perf_counter();out=main.agent(obs,cfg);elapsed=time.perf_counter()-started
   self.assertEqual(main._INSTANCE.diagnostics['status'],'completed')
   self.assertEqual(main._INSTANCE.diagnostics['parent_calls'],1)
   self.assertIsNone(sys.gettrace());self.assertIsNone(T.deadline._ACTIVE_TIMER.get())
   self.assertLess(elapsed,1);ROWS.append({'case':'worker_entrypoint','seconds':elapsed})
   return out
  out=self.worker(run);self.assertIsInstance(out,dict)
  self.assertIs(signal.getsignal(signal.SIGALRM),handler)
 def test_trace_restoration_normal_exception_and_expiry(self):
  def run():
   events=[]
   def trace(frame,event,arg):
    if frame.f_code.co_name=='spin':events.append(event)
    return trace
   for mode in ('normal','exception','expiry'):
    sys.settrace(trace);timer=T.deadline._DeadlineTimer(.01)
    sentinel=RuntimeError('caller exception')
    try:
     try:
      with timer:
       if mode=='exception':raise sentinel
       if mode=='expiry':spin()
     except BaseException as error:
      self.assertIs(error,timer.expired if mode=='expiry' else sentinel)
     self.assertIs(sys.gettrace(),trace);self.assertIsNone(T.deadline._ACTIVE_TIMER.get())
    finally:sys.settrace(None)
   self.assertTrue(events)
  self.worker(run)
 def test_inline_loop_and_nested_deadlines(self):
  def run():
   outer=T.deadline._DeadlineTimer(.01);inner=T.deadline._DeadlineTimer(1)
   try:
    with outer:
     with inner:
      while True:
       pass
   except T.deadline.DeadlineExceeded as error:self.assertIs(error,outer.expired)
   else:self.fail('inline loop was not cancelled')
   self.assertIsNone(sys.gettrace());self.assertIsNone(T.deadline._ACTIVE_TIMER.get())
  self.worker(run)
 def test_actual_selected_fallback_and_fresh_recovery(self):
  obs,cfg,_,_=self.h.fixture(1)
  def run():
   a=T.TitanAgent(T.Features(budget_seconds=.05,reserve_seconds=.01));a._initialize()
   old=a.controller
   with patch.object(a,'transform_selected',side_effect=spin):
    out=a.act(obs,cfg)
   self.assertEqual(a.diagnostics['status'],'deadline_fallback')
   self.assertEqual(out,a.selected);self.assertFalse(a.ready)
   a.act(obs,cfg);self.assertIsNot(a.controller,old)
   self.assertEqual(a.diagnostics['status'],'completed')
   self.assertEqual(a.diagnostics['parent_calls'],1)
   self.assertIsNone(sys.gettrace())
  self.worker(run)
 def test_production_cancel_and_foreign_exception(self):
  obs,cfg,_,_=self.h.fixture(1)
  def run():
   a=T.TitanAgent(T.Features(budget_seconds=.03,reserve_seconds=.01));a._initialize()
   with patch.object(a.production,'act',side_effect=spin):out=a.act(obs,cfg)
   self.assertEqual(out,T.deadline.legal_pass(obs));self.assertFalse(a.ready)
   a._initialize();sentinel=T.deadline.DeadlineExceeded('outer')
   with patch.object(a.production,'act',side_effect=sentinel):
    with self.assertRaises(T.deadline.DeadlineExceeded) as caught:a.act(obs,cfg)
   self.assertIs(caught.exception,sentinel);self.assertIsNone(sys.gettrace())
  self.worker(run)
 def test_main_thread_signal_restoration(self):
  previous=signal.getsignal(signal.SIGALRM);prior_timer=signal.getitimer(signal.ITIMER_REAL)
  self.assertEqual(prior_timer,(0.0,0.0))
  events=[]
  def handler(signum,frame):events.append(signum)
  try:
   signal.signal(signal.SIGALRM,handler)
   signal.setitimer(signal.ITIMER_REAL,.2)
   timer=T.deadline._DeadlineTimer(.01)
   with self.assertRaises(T.deadline.DeadlineExceeded) as caught:
    with timer:spin()
   self.assertIs(caught.exception,timer.expired)
   self.assertIs(signal.getsignal(signal.SIGALRM),handler)
   self.assertGreater(signal.getitimer(signal.ITIMER_REAL)[0],0)
   self.assertFalse(events)
   sentinel=ValueError('body')
   with self.assertRaises(ValueError) as caught:
    with T.deadline._DeadlineTimer(.1):raise sentinel
   self.assertIs(caught.exception,sentinel)
   self.assertIs(signal.getsignal(signal.SIGALRM),handler)
  finally:
   signal.setitimer(signal.ITIMER_REAL,0);signal.signal(signal.SIGALRM,previous)
if __name__=='__main__':
 result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(WorkerDeadline))
 p=ROOT/'runtime/integrated-selected/WORKER-DEADLINE-TESTS.json';p.parent.mkdir(parents=True,exist_ok=True)
 p.write_text(json.dumps({'tests':result.testsRun,'passed':result.wasSuccessful(),'cases':ROWS},indent=2)+'\n')
 raise SystemExit(not result.wasSuccessful())
