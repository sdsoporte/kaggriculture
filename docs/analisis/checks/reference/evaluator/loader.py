"""Benchmark against the pinned unmodified official game interpreter.

Fetches only three public source files to a temporary directory. No Kaggle
login/data downloads/package dependencies; no private opponent information
is given to agents. This driver is not the hosted Kaggle runner.
"""
from __future__ import annotations
import argparse
import ast
import copy
import hashlib
import importlib.util
import json
import random
import statistics
import sys
import tempfile
import time
import types
import urllib.request
from pathlib import Path
from typing import Any, Callable

ENGINE_REF = "28b6d8af3ce73926b3d0fda1410c1ddd8384ab8c"
RAW = "https://raw.githubusercontent.com/Kaggle/kaggle-environments/" + ENGINE_REF
class Struct(dict):
    def __getattr__(self, key):
        try: return self[key]
        except KeyError: raise AttributeError(key) from None
    def __setattr__(self, key, value): self[key] = value


def get_engine(cache):
    root = Path(cache)
    root.mkdir(parents=True, exist_ok=True)
    files = {
        "kaggriculture.py": "kaggle_environments/envs/kaggriculture/kaggriculture.py",
        "kaggriculture.json": "kaggle_environments/envs/kaggriculture/kaggriculture.json",
        "utils.py": "kaggle_environments/utils.py",
    }
    hashes = {}
    for name, path in files.items():
        local = root / name
        if not local.exists():
            with urllib.request.urlopen(RAW + "/" + path, timeout=60) as response:
                local.write_bytes(response.read())
        hashes[name] = hashlib.sha256(local.read_bytes()).hexdigest()
    # Compile the actual upstream seed helper instead of substituting its logic.
    parsed = ast.parse((root/"utils.py").read_text())
    helper = next(n for n in parsed.body if isinstance(n, ast.FunctionDef) and n.name=="resolve_episode_seed")
    namespace = {"Any":Any,"Callable":Callable,"random":random}
    exec(compile(ast.Module(body=[helper],type_ignores=[]),"upstream_seed_helper","exec"),namespace)
    package = types.ModuleType("kaggle_environments")
    utils = types.ModuleType("kaggle_environments.utils")
    utils.resolve_episode_seed = namespace["resolve_episode_seed"]
    sys.modules["kaggle_environments"] = package
    sys.modules["kaggle_environments.utils"] = utils
    spec = importlib.util.spec_from_file_location("official_kaggriculture", root/"kaggriculture.py")
    engine = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(engine)
    return engine, hashes


def load_agent(path, overrides=None):
    name = "candidate_" + str(time.time_ns())
    spec = importlib.util.spec_from_file_location(name,path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if overrides: module.POLICY.update(overrides)
    return module.agent


def play(engine, agents, seed, configuration=None):
    cfg = Struct()
    for key,value in engine.specification["configuration"].items():
        cfg[key] = value.get("default") if isinstance(value,dict) else value
    cfg.update(configuration or {})
    cfg.seed = seed
    env = Struct(configuration=cfg,done=False,info={})
    state = [Struct(observation=Struct(),action={},status="ACTIVE",reward=0) for _ in agents]
    engine.interpreter(state,env)
    timings = [[] for _ in agents]
    actions = [dict() for _ in agents]
    daily = []
    # Kaggle's framework stops after the interpreter sets DONE at episodeSteps-2.
    for step in range(cfg.episodeSteps):
        for i,s in enumerate(state):
            s.observation.step = step
            # Deep copy exposes only this player's own private observation.
            before = time.perf_counter()
            action = agents[i](copy.deepcopy(s.observation),cfg)
            timings[i].append(time.perf_counter()-before)
            assert isinstance(action,dict), "Agent must return an action dict"
            assert isinstance(action.get("farmer",[]),list)
            assert len(action.get("market",[])) <= cfg.maxMarketOrdersPerTurn
            assert len(action.get("hands",[])) <= len(s.observation.farms[i]["hands"])
            s.action = action
            for a in [action.get("farmer",["PASS"]),*action.get("hands",[])]:
                actions[i][a[0]] = actions[i].get(a[0],0)+1
        engine.interpreter(state,env)
        if (step+1)%cfg.turnsPerDay==0 or any(s.status=="DONE" for s in state):
            daily.append({"step":step,"bank":[s.observation.farms[i]["money"] for i,s in enumerate(state)],
                          "animals":[sum(isinstance(t,dict) and "animal" in t for row in s.observation.farms[i]["tiles"] for t in row) for i,s in enumerate(state)],
                          "inventory":[sum(s.observation.private["shed"].values())+
                              sum(sum(v.values()) for v in s.observation.private["inventories"]) for s in state]})
        if any(s.status=="DONE" for s in state):
            env.done=True
            break
    return {"seed":seed,"bank":[s.reward for s in state],
            "steps":step+1,"daily":daily,"actions":actions,
            "max_call_seconds":[max(t) for t in timings],
            "mean_call_seconds":[statistics.mean(t) for t in timings],
            "status":[s.status for s in state]}


def run(output,seeds):
    with tempfile.TemporaryDirectory(prefix="kaggriculture-source-") as cache:
        engine,hashes=get_engine(cache)
        path=Path(__file__).with_name("main.py")
        rival_path=Path(__file__).with_name("incumbent_20260907.py")
        if not rival_path.exists(): rival_path=path
        rivals={
            "official_starter":lambda:lambda obs,cfg:engine.starter_agent(obs),
            "goose_only":lambda:load_agent(rival_path,{"mixed":False}),
            "compact_no_expansion":lambda:load_agent(rival_path,{"animal_cap":22,"expansion":False,"max_hands":9}),
            "no_care":lambda:load_agent(rival_path,{"care":False}),
        }
        results=[]
        for name,factory in rivals.items():
            for seed in seeds:
                for seat in (0,1):
                    candidate=load_agent(path)
                    pair=[candidate,factory()] if seat==0 else [factory(),candidate]
                    result=play(engine,pair,seed)
                    own,other=result["bank"][seat],result["bank"][1-seat]
                    result.update(opponent=name,seat=seat,margin=own-other)
                    results.append(result)
                    print(json.dumps({"opponent":name,"seed":seed,"seat":seat,"bank":result["bank"],
                        "margin":own-other,"max_call_seconds":result["max_call_seconds"]}),flush=True)
        summary={}
        for name in rivals:
            rows=[r for r in results if r["opponent"]==name]
            margins=[r["margin"] for r in rows]
            summary[name]={"games":len(rows),"wins":sum(m>0 for m in margins),
                          "ties":sum(m==0 for m in margins),"losses":sum(m<0 for m in margins),
                          "mean_margin":statistics.mean(margins),
                          "mean_bank":statistics.mean(r["bank"][r["seat"]] for r in rows)}
        report={"engine_ref":ENGINE_REF,"engine_sha256":hashes,
                "agent_sha256":hashlib.sha256(path.read_bytes()).hexdigest(),
                "opponent_source_sha256":hashlib.sha256(rival_path.read_bytes()).hexdigest(),
                "method":"Unmodified official interpreter, explicit driver (not hosted Kaggle runner).",
                "seeds":seeds,"summary":summary,"games":results}
        Path(output).write_text(json.dumps(report,indent=2)+"\n")
        print("SUMMARY "+json.dumps(summary),flush=True)
        # Usability and basic productivity gates; stronger rivals are measured, not guaranteed.
        assert summary["official_starter"]["losses"]==0
        assert all(r["status"]==["DONE","DONE"] for r in results)
        assert max(max(r["max_call_seconds"]) for r in results)<1.0
        return report


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--output",default="evaluation.json")
    parser.add_argument("--seeds",default="1,17,101")
    args=parser.parse_args()
    run(args.output,[int(s) for s in args.seeds.split(",")])
