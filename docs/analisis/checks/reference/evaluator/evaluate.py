"""Offline, process-isolated tournament driver for the pinned official interpreter.

Not the hosted Kaggle runner. Only --prepare-engine performs network I/O.
Candidate modules are executable code: run the evaluator in an isolated container.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib.util
import inspect
import json
import math
import os
from pathlib import Path
import random
import resource
import selectors
import signal
import statistics
import subprocess
import sys
import tempfile
import time

HERE = Path(__file__).resolve().parent
LOADER = HERE.parent / "20260907-offline-agent" / "evaluate.py"
ENGINE_REF = "28b6d8af3ce73926b3d0fda1410c1ddd8384ab8c"
ENGINE_BLOBS = {
    "kaggriculture.py": "3c202c7ee921da239356789e266b694635103fc4",
    "kaggriculture.json": "b354d06b742fe48402513792253f1a5c29366b20",
    "utils.py": "91c8822ee6201ba4a5a8416c7dbe34f95dd61c87",
}
MAX_PACKET = 2 * 1024 * 1024


class Struct(dict):
    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError:
            raise AttributeError(key) from None
    def __setattr__(self, key, value):
        self[key] = value


def structify(value):
    if isinstance(value, dict):
        return Struct({k: structify(v) for k, v in value.items()})
    if isinstance(value, list):
        return [structify(v) for v in value]
    return value


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def import_file(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ValueError(f"Cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def verify_sources(directory):
    hashes = {}
    for name, expected in ENGINE_BLOBS.items():
        data = (Path(directory) / name).read_bytes()
        actual = hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()
        if actual != expected:
            raise ValueError(f"Official source mismatch: {name}; expected blob {expected}, got {actual}")
        hashes[name] = hashlib.sha256(data).hexdigest()
    return hashes


def get_engine(directory, loader=LOADER, prepare=False):
    """Compose the existing loader; offline mode never fills a missing cache."""
    if not prepare:
        verify_sources(directory)
    module = import_file(loader, "kag_eval_existing_loader")
    if module.ENGINE_REF != ENGINE_REF:
        raise ValueError("Existing loader's source pin changed; update explicitly, not silently")
    engine, _ = module.get_engine(directory)
    return engine, verify_sources(directory)


def usage():
    value = resource.getrusage(resource.RUSAGE_SELF)
    # Linux returns KiB, macOS bytes. Other POSIX platforms are not validated.
    rss = value.ru_maxrss / 1024 if sys.platform == "darwin" else value.ru_maxrss
    return {"cpu_seconds": value.ru_utime + value.ru_stime, "peak_rss_kib": rss}


def load_callable(agent_spec, cache, loader):
    if agent_spec == "official_starter":
        engine, _ = get_engine(cache, loader)
        return engine.starter_agent
    path, sep, name = agent_spec.partition("::")
    sys.path.insert(0, str(Path(path).resolve().parent))
    function = getattr(import_file(path, "kag_eval_candidate"), name if sep else "agent")
    if not callable(function):
        raise TypeError("Agent entry point is not callable")
    return function


def worker(agent_spec, cache, loader, rng_seed):
    """JSON IPC avoids deserializing agent-produced pickle in the engine process."""
    output = sys.stdout
    def send(message):
        payload = encoded(message)
        if len(payload) + 1 > MAX_PACKET:
            raise ValueError("Worker response exceeds packet limit")
        output.buffer.write(payload + b"\n")
        output.buffer.flush()
    random.seed(rng_seed)  # Independent of the environment seed and player seat.
    with open(os.devnull, "w") as quiet, contextlib.redirect_stdout(quiet), contextlib.redirect_stderr(quiet):
        try:
            function = load_callable(agent_spec, cache, loader)
            try:
                inspect.signature(function).bind({}, {})
                takes_config = True
            except TypeError:
                inspect.signature(function).bind({})
                takes_config = False
            send({"kind": "ready", **usage()})
        except BaseException as exc:
            send({"kind": "load_error", "error": f"{type(exc).__name__}: {exc}"[:1000], **usage()})
            return
        for line in sys.stdin.buffer:
            try:
                request = json.loads(line)
                obs, cfg = structify(request["observation"]), structify(request["configuration"])
                start, cpu = time.perf_counter(), time.process_time()
                action = function(obs, cfg) if takes_config else function(obs)
                seconds, cpu_seconds = time.perf_counter() - start, time.process_time() - cpu
            except BaseException as exc:
                send({"kind": "crash", "error": f"{type(exc).__name__}: {exc}"[:1000], **usage()})
                return
            try:
                if not isinstance(action, dict):
                    raise TypeError("The official action schema requires an object")
                send({"kind": "action", "action": action, "call_seconds": seconds,
                      "call_cpu_seconds": cpu_seconds, **usage()})
            except (TypeError, ValueError, OverflowError) as exc:
                send({"kind": "invalid_action", "error": f"{type(exc).__name__}: {exc}"[:1000], **usage()})
                return


class Actor:
    """One fresh persistent process and working directory per agent per game."""
    def __init__(self, spec, cache, loader, rng_seed, startup_timeout=10.0):
        self.spec, self.buffer = spec, bytearray()
        self.stats = {"calls": 0, "call_seconds": [], "rpc_seconds": [], "call_cpu_seconds": 0.0,
                      "cpu_seconds": 0.0, "peak_rss_kib": 0, "exit_code": None, "resource_sample": "child_rusage"}
        self.directory = tempfile.TemporaryDirectory(prefix="kag-eval-agent-")
        env = {"PATH": os.defpath, "HOME": self.directory.name, "LANG": "C.UTF-8",
               "PYTHONHASHSEED": str(rng_seed % (2**32)), "PYTHONDONTWRITEBYTECODE": "1"}
        self.proc = subprocess.Popen(
            [sys.executable, "-B", "-u", str(Path(__file__).resolve()), "--worker", spec,
             str(Path(cache).resolve()), str(Path(loader).resolve()), str(rng_seed)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            cwd=self.directory.name, env=env, start_new_session=True)
        os.set_blocking(self.proc.stdin.fileno(), False)
        os.set_blocking(self.proc.stdout.fileno(), False)
        started = time.perf_counter()
        self.ready = self.exchange(None, startup_timeout)
        self.stats["startup_seconds"] = time.perf_counter() - started
        self._measure(self.ready)

    def _measure(self, message):
        for key in ("cpu_seconds", "peak_rss_kib"):
            value = message.get(key)
            if isinstance(value, (int, float)) and math.isfinite(value):
                self.stats[key] = max(self.stats[key], value)

    def exchange(self, request, timeout):
        """Deadline covers request writing AND response reading; partial frames cannot hang."""
        started = time.perf_counter()
        try:
            pending = memoryview(encoded(request) + b"\n") if request is not None else memoryview(b"")
            if len(pending) > MAX_PACKET:
                return {"kind": "protocol_error", "error": "Observation exceeds packet limit"}
            with selectors.DefaultSelector() as selector:
                selector.register(self.proc.stdout, selectors.EVENT_READ)
                if pending:
                    selector.register(self.proc.stdin, selectors.EVENT_WRITE)
                while True:
                    if b"\n" in self.buffer:
                        line, _, rest = self.buffer.partition(b"\n")
                        self.buffer = bytearray(rest)
                        result = json.loads(line)
                        if not isinstance(result, dict) or not isinstance(result.get("kind"), str):
                            raise ValueError("Malformed protocol response")
                        return result
                    remaining = timeout - (time.perf_counter() - started)
                    if remaining <= 0:
                        return {"kind": "timeout", "error": "Agent RPC wall-clock deadline exceeded"}
                    for key, _ in selector.select(remaining):
                        if key.fileobj is self.proc.stdin:
                            count = os.write(self.proc.stdin.fileno(), pending)
                            pending = pending[count:]
                            if not pending:
                                selector.unregister(self.proc.stdin)
                        else:
                            chunk = os.read(self.proc.stdout.fileno(), 65536)
                            if not chunk:
                                return {"kind": "process_exit", "error": "Worker exited before completing a response"}
                            self.buffer.extend(chunk)
                            if len(self.buffer) > MAX_PACKET:
                                raise ValueError("Response exceeds packet limit")
        except (OSError, ValueError, TypeError) as exc:
            return {"kind": "protocol_error", "error": f"{type(exc).__name__}: {exc}"[:1000]}

    def act(self, observation, configuration, timeout):
        started = time.perf_counter()
        result = self.exchange({"observation": observation, "configuration": configuration}, timeout)
        self.stats["rpc_seconds"].append(time.perf_counter() - started)
        self.stats["calls"] += 1
        self._measure(result)
        if result.get("kind") == "action":
            duration, cpu = result.get("call_seconds"), result.get("call_cpu_seconds")
            if (not isinstance(result.get("action"), dict) or
                any(not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0 for v in (duration, cpu))):
                return {"kind": "protocol_error", "error": "Invalid action response or timing"}
            self.stats["call_seconds"].append(duration)
            self.stats["call_cpu_seconds"] += cpu
        return result

    def close(self):
        if getattr(self, "closed", False):
            return
        self.closed = True
        # Include resource use inside a call that timed out before it could report.
        if sys.platform.startswith("linux"):
            try:
                status = Path(f"/proc/{self.proc.pid}/status").read_text()
                for line in status.splitlines():
                    if line.startswith("VmHWM:"):
                        self.stats["peak_rss_kib"] = max(self.stats["peak_rss_kib"], int(line.split()[1]))
                fields = Path(f"/proc/{self.proc.pid}/stat").read_text().rsplit(")", 1)[1].split()
                cpu = (int(fields[11]) + int(fields[12])) / os.sysconf("SC_CLK_TCK")
                self.stats["cpu_seconds"] = max(self.stats["cpu_seconds"], cpu)
                self.stats["resource_sample"] = "child_rusage_plus_linux_procfs"
            except (OSError, ValueError, IndexError):
                pass  # Already exited: the last child-reported sample remains available.
        # Kill the process group, including children, before discarding its private directory.
        # Do not wait for a crashed/hung agent to consume another game slot.
        self.stats["exit_code"] = self.proc.poll()
        if self.proc.stdin:
            self.proc.stdin.close()
        try:
            self.proc.wait(timeout=0.1)
        except subprocess.TimeoutExpired:
            pass
        try:
            os.killpg(self.proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        self.proc.wait()
        if self.stats["exit_code"] is None:
            self.stats["exit_code"] = self.proc.returncode
        self.proc.stdout.close()
        self.directory.cleanup()

    def report(self):
        result = {k: v for k, v in self.stats.items() if k not in ("call_seconds", "rpc_seconds")}
        for key in ("call_seconds", "rpc_seconds"):
            values = self.stats[key]
            result["max_" + key] = max(values, default=0.0)
            result["mean_" + key] = statistics.mean(values) if values else 0.0
        return result


def play(engine, specs, cache, loader, seed, candidate_seat, rng_seed=20260907,
         action_timeout=1.0, startup_timeout=10.0, game_timeout=120.0, episode_steps=None):
    cfg = Struct({key: value.get("default") if isinstance(value, dict) else value
                  for key, value in engine.specification["configuration"].items()})
    if episode_steps is not None:
        cfg.episodeSteps = episode_steps
    if not isinstance(cfg.episodeSteps, int) or cfg.episodeSteps < 2:
        raise ValueError("episodeSteps must be at least 2")
    cfg.seed = seed
    env = Struct(configuration=cfg, done=False, info={})
    state = [Struct(observation=Struct(), action={}, status="ACTIVE", reward=0) for _ in range(2)]
    started, initial_cpu = time.perf_counter(), time.process_time()
    actors, trace = [], hashlib.sha256()
    result = {"seed": seed, "candidate_seat": candidate_seat, "status": "failed", "scores": None,
              "failure": None, "steps": 0, "episode_steps": cfg.episodeSteps, "daily_bank": []}
    try:
        engine.interpreter(state, env)
        if cfg.get("seed") is not None:
            raise ValueError("Environment seed must not be exposed to agents")
        # RNG seeds depend on role, NOT the environment seed or player position.
        for seat, spec in enumerate(specs):
            actor = Actor(spec, cache, loader, rng_seed + (seat != candidate_seat), startup_timeout)
            actors.append(actor)
            if actor.ready.get("kind") != "ready":
                result["failure"] = {"seat": seat, "step": 0, "phase": "startup", **actor.ready}
                return result
        for step in range(cfg.episodeSteps):
            actions = []
            for seat, actor in enumerate(actors):
                remaining = game_timeout - (time.perf_counter() - started)
                if remaining <= 0:
                    result["failure"] = {"kind": "game_timeout", "seat": None, "step": step}
                    return result
                state[seat].observation.step = step
                state[seat].observation.remainingOverageTime = 0
                response = actor.act(state[seat].observation, cfg, min(action_timeout, remaining))
                if response.get("kind") != "action":
                    result["failure"] = {"seat": seat, "step": step, "phase": "action", **response}
                    return result
                actions.append(response["action"])
            for seat in range(2):
                state[seat].action = actions[seat]
            engine.interpreter(state, env)
            result["steps"] += 1
            bank = [float(state[0].observation.farms[i]["money"]) for i in range(2)]
            trace.update(encoded({"step": step, "actions": actions, "bank": bank}))
            done = all(s.status == "DONE" for s in state)
            if (step + 1) % cfg.turnsPerDay == 0 or done:
                result["daily_bank"].append({"step": step, "bank": bank})
            if done:
                scores = [s.reward for s in state]
                if not all(isinstance(s, (int, float)) and math.isfinite(s) for s in scores):
                    raise ValueError("Nonfinite or missing terminal score")
                result.update(status="complete", scores=scores)
                env.done = True
                return result
        result["failure"] = {"kind": "incomplete", "seat": None, "step": result["steps"]}
    except Exception as exc:
        result["failure"] = {"kind": "engine_error", "seat": None, "step": result["steps"],
                             "error": f"{type(exc).__name__}: {exc}"[:1000]}
    finally:
        for actor in actors:
            actor.close()
        result["actors"] = [actor.report() for actor in actors]
        result["wall_seconds"] = time.perf_counter() - started
        result["driver_cpu_seconds"] = time.process_time() - initial_cpu
        result["bank_snapshot"] = [float(state[0].observation.get("farms", [{"money": 0}] * 2)[i]["money"])
                                   for i in range(2)]
        trace.update(encoded([s.observation for s in state]))
        result["trace_sha256"] = trace.hexdigest()
    return result


def summarize(games):
    output = {}
    for name in sorted({g["opponent"] for g in games}):
        rows = [g for g in games if g["opponent"] == name]
        valid = [g for g in rows if g["status"] == "complete"]
        margins = [g["scores"][g["candidate_seat"]] - g["scores"][1 - g["candidate_seat"]] for g in valid]
        failures = [g for g in rows if g["status"] != "complete"]
        output[name] = {"scheduled": len(rows), "completed": len(valid), "failed": len(failures),
                       "wins": sum(m > 0 for m in margins), "ties": sum(m == 0 for m in margins),
                       "losses": sum(m < 0 for m in margins),
                       "mean_margin": statistics.mean(margins) if margins else None,
                       "candidate_failures": sum(g["failure"].get("seat") == g["candidate_seat"] for g in failures),
                       "opponent_failures": sum(g["failure"].get("seat") == 1 - g["candidate_seat"] for g in failures)}
    return output


def resolve_spec(value):
    if value == "official_starter":
        return value
    path, sep, name = value.partition("::")
    return str(Path(path).resolve(strict=True)) + ("::" + name if sep else "")


def fingerprint(spec):
    if spec == "official_starter":
        return {"entry": spec, "engine_ref": ENGINE_REF}
    path, _, function = spec.partition("::")
    value = {"entry": Path(path).name, "callable": function or "agent", "sha256": sha256(path)}
    if Path(path).resolve() == HERE / "opponents.py" and function == "compact_no_expansion":
        value["dependency"] = {"entry": "../20260907-offline-agent/main.py", "sha256": sha256(LOADER.with_name("main.py"))}
    return value


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--worker":
        worker(sys.argv[2], sys.argv[3], sys.argv[4], int(sys.argv[5]))
        return 0
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare-engine", type=Path)
    parser.add_argument("--engine-dir", type=Path)
    parser.add_argument("--loader", type=Path, default=LOADER)
    parser.add_argument("--candidate", default=str(LOADER.with_name("main.py")))
    parser.add_argument("--opponent", action="append", default=[], help="Unique label=path.py::function, or label=official_starter")
    parser.add_argument("--seeds", default="2027,6607,104729")
    parser.add_argument("--rng-seed", type=int, default=20260907)
    parser.add_argument("--action-timeout", type=float, default=1.0)
    parser.add_argument("--startup-timeout", type=float, default=10.0)
    parser.add_argument("--game-timeout", type=float, default=120.0)
    parser.add_argument("--episode-steps", type=int)
    parser.add_argument("--recheck-first", action="store_true")
    parser.add_argument("--output", type=Path, default=Path("tournament.json"))
    args = parser.parse_args()
    if args.prepare_engine:
        _, hashes = get_engine(args.prepare_engine, args.loader, prepare=True)
        print(json.dumps({"engine_ref": ENGINE_REF, "sha256": hashes}, indent=2))
        return 0
    if args.engine_dir is None:
        parser.error("--engine-dir is required; prepare its three pinned files before going offline")
    if any(not math.isfinite(t) or t <= 0 for t in (args.action_timeout, args.startup_timeout, args.game_timeout)):
        parser.error("Timeouts must be finite and positive")
    try:
        seeds = [int(s.strip()) for s in args.seeds.split(",")]
        if len(seeds) != len(set(seeds)):
            raise ValueError("Duplicate seeds")
    except ValueError:
        parser.error("--seeds requires distinct comma-separated integers")
    defaults = ["starter=official_starter", f"crop_patrol={HERE / 'opponents.py'}::crop_patrol",
                f"seeded_walk={HERE / 'opponents.py'}::seeded_walk",
                f"compact_no_expansion={HERE / 'opponents.py'}::compact_no_expansion"]
    rivals = {}
    for item in args.opponent or defaults:
        label, sep, spec = item.partition("=")
        if not sep or not label or label in rivals:
            parser.error("Each --opponent needs a unique nonempty label=spec")
        rivals[label] = resolve_spec(spec)
    candidate = resolve_spec(args.candidate)
    engine, hashes = get_engine(args.engine_dir, args.loader)
    games = []
    for name, rival in rivals.items():
        for seed in seeds:
            for seat in (0, 1):
                pair = [candidate, rival] if seat == 0 else [rival, candidate]
                game = play(engine, pair, args.engine_dir, args.loader, seed, seat, args.rng_seed,
                            args.action_timeout, args.startup_timeout, args.game_timeout, args.episode_steps)
                game["opponent"] = name
                games.append(game)
                print(json.dumps({k: game[k] for k in ("opponent", "seed", "candidate_seat", "status", "scores", "failure")}), flush=True)
    reproducibility = None
    if args.recheck_first:
        first = games[0]
        replay = play(engine, [candidate, next(iter(rivals.values()))], args.engine_dir, args.loader,
                      first["seed"], 0, args.rng_seed, args.action_timeout, args.startup_timeout,
                      args.game_timeout, args.episode_steps)
        reproducibility = {"checked": True, "same_trace_and_scores": first["status"] == replay["status"] == "complete" and
                           first["trace_sha256"] == replay["trace_sha256"] and first["scores"] == replay["scores"],
                           "original_trace": first["trace_sha256"], "replay_trace": replay["trace_sha256"]}
    report = {"schema_version": 1, "engine_ref": ENGINE_REF, "engine_sha256": hashes,
              "loader_sha256": sha256(args.loader), "evaluator_sha256": sha256(__file__),
              "candidate": fingerprint(candidate), "opponents": {n: fingerprint(s) for n, s in rivals.items()},
              "seeds": seeds, "agent_rng_seed": args.rng_seed, "python": sys.version,
              "platform": sys.platform, "resource_usage": usage(),
              "limits": {"action_rpc_seconds": args.action_timeout, "startup_seconds": args.startup_timeout,
                         "game_seconds_between_steps": args.game_timeout, "remaining_overage_time": 0},
              "method": "Official interpreter with explicit driver; not hosted Kaggle scoring. Decision timings are child-reported; RPC deadlines are parent-enforced. Resource samples combine child rusage with Linux procfs before cleanup. Entry-file hashes do not cover arbitrary agent dependencies.",
              "summary": summarize(games), "games": games, "reproducibility": reproducibility}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", dir=args.output.parent, delete=False) as file:
        json.dump(report, file, indent=2, allow_nan=False)
        file.write("\n")
        temporary = file.name
    os.replace(temporary, args.output)
    print("SUMMARY " + json.dumps(report["summary"]), flush=True)
    return int(any(g["status"] != "complete" for g in games) or
               (reproducibility is not None and not reproducibility["same_trace_and_scores"]))


if __name__ == "__main__":
    raise SystemExit(main())
