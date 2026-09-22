# SPDX-License-Identifier: Apache-2.0
"""Relocated offline raw-loader episode, candidate called in one worker thread.

Uses exact upstream build_agent/get_last_callable/read_file functions via AST;
not the hosted HTTP/RPC service. The official interpreter is unchanged.
"""
import ast,copy,hashlib,json,os,sys,time,tarfile,tempfile,subprocess
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from io import StringIO
from typing import Any,Callable,Dict,Tuple
from urllib.parse import urlparse
ROOT=Path(__file__).resolve().parent

def worker_episode(root):
 sys.path.insert(0,str(root));sys.path.insert(1,str(root/'checks'))
 def offline(event,args):
  if event in ('socket.connect','socket.getaddrinfo'):raise RuntimeError('offline episode')
 sys.addaudithook(offline)
 from test_engine_semantics import EngineSemantics
 EngineSemantics.setUpClass();engine=EngineSemantics.engine;ev=EngineSemantics.ev
 ns=dict(globals(),InvalidArgument=ValueError,NotFound=FileNotFoundError)
 for path,names in [(root/'checks/reference/engine/utils.py',{'read_file'}),
                    (root/'checks/reference/evaluator/official_agent.py',{'is_url','get_last_callable','build_agent'})]:
  tree=ast.parse(path.read_text());nodes=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in names]
  assert len(nodes)==len(names)
  exec(compile(ast.Module(body=nodes,type_ignores=[]),str(path),'exec'),ns)
 candidate,_=ns['build_agent'](str(root/'main.py'),{},'kaggriculture')
 cfg=ev.Struct({k:v.get('default') if isinstance(v,dict) else v for k,v in engine.specification['configuration'].items()})
 cfg.seed=9922999
 env=ev.Struct(configuration=cfg,done=False,info={})
 state=[ev.Struct(observation=ev.Struct(),action={},status='ACTIVE',reward=0) for _ in range(2)]
 engine.interpreter(state,env)
 rows=[];trace=hashlib.sha256()
 def call(obs):
  started=time.perf_counter();cpu=time.process_time()
  action=candidate(obs,cfg)
  # Include returned action serialization in the measured worker boundary.
  encoded=json.dumps(action,sort_keys=True,allow_nan=False)
  import titan_runtime
  selected=next(cell.cell_contents for cell in candidate.__closure__ if callable(cell.cell_contents))
  diag=dict(selected.__globals__['_INSTANCE'].diagnostics)
  assert sys.gettrace() is None
  assert titan_runtime.deadline._ACTIVE_TIMER.get() is None
  return json.loads(encoded),time.perf_counter()-started,time.process_time()-cpu,diag
 with ThreadPoolExecutor(1) as pool:
  for step in range(720):
   for seat in (0,1):state[seat].observation.step=step
   started=time.perf_counter()
   action,wall,cpu,diag=pool.submit(call,copy.deepcopy(state[0].observation)).result(timeout=2)
   outer=time.perf_counter()-started
   assert outer<1,(step,outer)
   assert diag['parent_calls']==1 and diag['status']=='completed',(step,diag)
   state[0].action=action
   state[1].action=engine.starter_agent(copy.deepcopy(state[1].observation))
   trace.update(json.dumps(action,sort_keys=True,separators=(',',':')).encode()+b'\n')
   rows.append({'step':step,'worker_wall_seconds':wall,'worker_cpu_seconds':cpu,'outer_thread_seconds':outer,'status':diag['status']})
   engine.interpreter(state,env)
   if any(s.status=='DONE' for s in state):break
 assert len(rows)==719
 for mod in list(sys.modules.values()):
  p=getattr(mod,'__file__',None)
  if p and '/commons-work/' in p:raise AssertionError('original repo import '+p)
 return {'method':'Exact official raw-loader functions via AST and official interpreter; worker thread, offline; not hosted RPC',
         'python':sys.version,'seed':9922999,'seat':0,'opponent':'official_starter','calls':len(rows),'last_step':step,
         'engine_sha256':EngineSemantics.hashes,'raw_loader_sha256':hashlib.sha256((root/'checks/reference/evaluator/official_agent.py').read_bytes()).hexdigest(),
         'source_manifest_sha256':hashlib.sha256((root/'SOURCE.json').read_bytes()).hexdigest(),
         'status':[s.status for s in state],'cash':[s.reward for s in state],
         'action_sha256':trace.hexdigest(),'fallbacks':0,'errors':0,'external_timeouts':0,
         'max_worker_seconds':max(r['worker_wall_seconds'] for r in rows),
         'max_outer_seconds':max(r['outer_thread_seconds'] for r in rows),'rows':rows}

def main():
 if len(sys.argv)>1 and sys.argv[1]=='--worker':
  result=worker_episode(Path(sys.argv[2]));Path(sys.argv[3]).write_text(json.dumps(result,indent=2)+'\n');return
 from build_integrated import verify_current
 verify_current();archive=ROOT/'exports/titan-current.tar.gz'
 with tempfile.TemporaryDirectory(prefix='titan-worker-clean-',dir='/tmp') as folder:
  root=Path(folder)/'candidate';root.mkdir()
  with tarfile.open(archive) as t:t.extractall(root,filter='data')
  output=Path(folder)/'result.json'
  p=subprocess.run([sys.executable,'-I',str(root/'checks/test_worker_episode.py'),'--worker',str(root),str(output)],cwd=folder,capture_output=True,text=True,timeout=180)
  if p.returncode:raise RuntimeError(p.stdout+p.stderr)
  result=json.loads(output.read_text())
 result['archive_sha256']=hashlib.sha256(archive.read_bytes()).hexdigest()
 (ROOT/'runtime/integrated-selected/WORKER-EPISODE.json').write_text(json.dumps(result,indent=2)+'\n')
 print(json.dumps({k:v for k,v in result.items() if k!='rows'},indent=2))
if __name__=='__main__':main()
