#!/usr/bin/env python
"""Generate the Kaggle notebook that evaluates the V10 variants at scale.

Why a notebook: our local verdicts rest on 24 paired games per variant, and an
adversarial review flagged the power problem explicitly ("seeds 1-3 give literally
identical deltas, so the effective sample is ~4"). Kaggle gives 4 vCPU and 12 h, so
200+ games per variant turns "measured" into "measured with a confidence interval".

Statistical care taken here, learned the hard way:
  * mirrored seats are DEDUPLICATED before computing n: the engine is symmetric
    under a seat swap, so a seed contributes ONE observation, not two. Averaging
    the two seats per seed and computing the CI over seeds is what an independent
    verification told us to do.
  * win rate and mean margin are reported separately, because the final Kaggle
    ranking is a win-rate tournament while the tactical work is driven by margin.
  * the control is the baseline against itself, run at the same seeds.

Usage:
    python kaggle/arena/build_arena.py
"""

import ast
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "kaggriculture-arena.ipynb"


def md(text):
    return {"cell_type": "markdown", "metadata": {}, "source": text.splitlines(keepends=True)}


def code(text):
    return {"cell_type": "code", "execution_count": None, "metadata": {},
            "outputs": [], "source": text.splitlines(keepends=True)}


INTRO = """# Kaggriculture Arena - V10 variants at scale

Local verdicts for the V10 levers rest on 24 paired games each, and an adversarial
review flagged the power problem: seeds 1-3 produce identical deltas at both seats,
so the effective sample is about 4 and the 95% CI spans 60% of the estimate. This
notebook re-runs the same comparisons with **200 games per variant** on a machine
that is not a laptop.

The agents and the harness come from the private dataset
`ser8147/kaggriculture-agents` (tape agents, the V10 overlays, `loading.py`,
`runner.py`, `harness.py`).

Two statistical rules are enforced deliberately:

1. **Mirrored seats are deduplicated before computing n.** The engine is symmetric
   under a seat swap, so the two games of a seed are not independent observations.
   Each seed contributes one number - the mean of its two seats - and the CI is
   computed over seeds.
2. **Win rate and mean margin are reported separately.** The final ranking is a
   Bradley-Terry win-rate tournament, while the tactical work is driven by margin,
   and the two do not always agree (V9.1 is the standing proof: a higher mean bank
   with a lower win rate)."""

SETUP = """import glob
import json
import multiprocessing as mp
import os
import statistics
import sys
import time

print("cpu_count:", os.cpu_count())

# PIN THE ENGINE **BEFORE ANY IMPORT**. In v2 this was done wrong: importing
# kaggle_environments first to read its version caches the preinstalled module, so
# the later pip install had no effect - pip reported rc 0 and "engine in use" was
# still 1.29.3, whose kaggriculture silently produced $0-5 banks for every variant
# (the tape spends its money and produces nothing, i.e. the action format or the
# mechanics differ). Install first, import after.
WANT = "1.32.7"
import subprocess
subprocess.run([sys.executable, "-m", "pip", "install", "-q",
                f"kaggle-environments=={WANT}"], check=False)
import kaggle_environments
engine_version = getattr(kaggle_environments, "__version__", "?")
print("engine in use:", engine_version, "(want", WANT + ")")
if engine_version != WANT:
    print("WARNING: engine mismatch - the local measurements are NOT comparable")


def find_all(name):
    return sorted(glob.glob(f"/kaggle/input/**/{name}", recursive=True))


agent_root = None
for candidate in sorted(glob.glob("/kaggle/input/**/main_v8.py", recursive=True)):
    root = candidate.rsplit("/submissions/", 1)[0]
    if os.path.isdir(os.path.join(root, "src", "kaggriculture")):
        agent_root = root
        break
print("agent dataset root:", agent_root)

if agent_root is None:
    raise SystemExit("kaggriculture-agents dataset not attached")

sys.path.insert(0, os.path.join(agent_root, "src"))
from kaggriculture.loading import load_agent          # noqa: E402
from kaggriculture.runner import run_episode          # noqa: E402
from kaggriculture.harness import paired_benchmark    # noqa: E402

BASELINE = os.path.join(agent_root, "submissions", "main_v8.py")
AGENTS = os.path.join(agent_root, "src", "kaggriculture", "agents")
VARIANTS = {
    "baseline (control)": BASELINE,
    "roster cow": os.path.join(AGENTS, "v10_cow.py"),
    "roster sheep": os.path.join(AGENTS, "v10_sheep.py"),
    "roster goose": os.path.join(AGENTS, "v10_goose.py"),
    "sell chunks": os.path.join(AGENTS, "v10_sell_chunks.py"),
}
for label, path in VARIANTS.items():
    print(f"  {label:<20} {'OK' if os.path.isfile(path) else 'MISSING'}  {path}")"""

TIMING = """# Measure the cost before committing to a long sweep.
started = time.time()
probe = paired_benchmark(load_agent(BASELINE), load_agent(BASELINE), [0, 1])
elapsed = time.time() - started
per_game = elapsed / probe["games"]
print(f"timing probe: {probe['games']} games in {elapsed:.1f}s = {per_game:.2f} s/game")

N_SEEDS = 100          # SMOKE FIRST proved the pipeline; now the real sweep.
# 100 seeds x 2 seats x 5 variants = 1,000 games, roughly 25 min at 3-8 s/game on
# 4 workers. The engine pin (1.32.7), the bank gate and the reference ladder are all
# in place, and this run is what turns the V10 verdicts from 24-game estimates into
# statements with a seed-level confidence interval.
workers = max(1, min(4, os.cpu_count() or 1))
total_games = N_SEEDS * 2 * len(VARIANTS)
print(f"plan: {len(VARIANTS)} variants x {N_SEEDS} seeds x 2 seats = {total_games:,} games")
print(f"projected wall time with {workers} workers: "
      f"{total_games * per_game / workers / 60:.1f} minutes")"""

SWEEP_HEAD = """# THE DIAGNOSTIC THAT WAS MISSING: absolute banks. A margin of exactly 0 for a
# variant is only interpretable next to the agent's own bank. If both players end
# near the starting money (3000) the tape never advanced - for instance because the
# observation lacks the step/day/hour fields the tape agents use to locate
# themselves, so their wrapper restarts the tape every turn. If the banks are
# 60k-100k, the agents played properly and 0 really means "identical play".
baseline_agent = load_agent(BASELINE)
episode = run_episode(load_agent(BASELINE), load_agent(BASELINE), 0)
print("probe banks:", episode["banks"], "| turns:", episode["turns"])

# HARD GATE. Our tape earns tens of thousands per game in the engine we measured on.
# If the probe comes back near zero, the engine or the action format differs and the
# whole sweep would be void - so abort in 90 seconds instead of burning 20 minutes.
SANE_BANK = 20000
if max(episode["banks"]) < SANE_BANK:
    raise SystemExit(
        f"ABORT: probe banks {episode['banks']} are far below {SANE_BANK}. The engine "
        f"({engine_version}) is not the one the local measurements used, or the action "
        f"format differs. A sweep here would produce a void result."
    )
print("gate passed: the tape is actually playing")

seen = []


def spy(obs):
    if not seen:
        seen.append({"keys": sorted(obs.keys()), "step": obs.get("step"),
                     "day": obs.get("day"), "hour": obs.get("hour")})
    return baseline_agent(obs)


run_episode(spy, load_agent(BASELINE), 0)
print("observation sample:", seen[0] if seen else None)
"""

SWEEP = """def _play(job):
    # Runs in a worker process; fork inherits the imported agents.
    variant_path, seed, seat = job
    if seat == 0:
        agent_a, agent_b = load_agent(variant_path), load_agent(BASELINE)
    else:
        agent_a, agent_b = load_agent(BASELINE), load_agent(variant_path)
    episode = run_episode(agent_a, agent_b, seed)
    return (seed, seat,
            episode["banks"][seat] - episode["banks"][1 - seat],
            episode["banks"][seat], episode["banks"][1 - seat])


def run_variant(label, variant_path, seeds, workers):
    jobs = [(variant_path, seed, seat) for seed in seeds for seat in (0, 1)]
    margins = {}
    banks = []
    started = time.time()
    with mp.Pool(workers) as pool:
        for seed, seat, margin, mine, theirs in pool.imap_unordered(_play, jobs, chunksize=4):
            margins.setdefault(seed, {})[seat] = margin
            banks.append((mine, theirs))
    # One observation per seed: the mean of its two seats (dedup mirrored seats).
    per_seed = [statistics.fmean(pair.values()) for pair in margins.values()]
    per_game = [margin for pair in margins.values() for margin in pair.values()]
    wins = sum(1 for m in per_game if m > 0)
    mean = statistics.fmean(per_seed) if per_seed else 0.0
    sd = statistics.stdev(per_seed) if len(per_seed) > 1 else 0.0
    se = sd / len(per_seed) ** 0.5 if per_seed else 0.0
    return {
        "label": label, "games": len(per_game), "seeds": len(per_seed),
        "win_rate": 100 * wins / max(1, len(per_game)),
        "mean": mean, "sd": sd, "se": se,
        "ci_low": mean - 1.96 * se, "ci_high": mean + 1.96 * se,
        "mean_bank": statistics.fmean(b for b, _ in banks) if banks else 0.0,
        "mean_opp": statistics.fmean(o for _, o in banks) if banks else 0.0,
        "seconds": time.time() - started,
    }


SEEDS = list(range(N_SEEDS))
results = []
for label, path in VARIANTS.items():
    row = run_variant(label, path, SEEDS, workers)
    results.append(row)
    print(f"{label:<20} {row['games']:>4} games  win {row['win_rate']:>5.1f}%  "
          f"margin {row['mean']:>+9,.0f}  CI [{row['ci_low']:>+8,.0f}, {row['ci_high']:>+8,.0f}]  "
          f"bank {row['mean_bank']:>9,.0f} vs {row['mean_opp']:>9,.0f}  "
          f"({row['seconds'] / 60:.1f} min)")"""

REFERENCE = """# The reference ladder: ten complete published agents. Every V10 verdict so far
# compared our tape against a MIRROR of itself, which cannot show whether a change
# generalises. This measures the tape against a diverse field instead - the honest
# test of "broadly better", and the criterion the project kept missing.
from pathlib import Path as _Path

ref_dir = None
for hit in glob.glob("/kaggle/input/**/broker_bea.py", recursive=True):
    ref_dir = _Path(hit).parent
    break
REFERENCE = sorted(p for p in (ref_dir.glob("*.py") if ref_dir else []))
print(f"reference agents found: {len(REFERENCE)}")
for path in REFERENCE:
    print("  ", path.stem)


def _duel(job):
    ours_path, theirs_path, seed, seat = job
    if seat == 0:
        agent_a, agent_b = load_agent(ours_path), load_agent(theirs_path)
    else:
        agent_a, agent_b = load_agent(theirs_path), load_agent(ours_path)
    episode = run_episode(agent_a, agent_b, seed)
    return (seed, seat,
            episode["banks"][seat] - episode["banks"][1 - seat],
            episode["banks"][seat], episode["banks"][1 - seat])


def duel(ours_path, theirs_path, seeds, workers):
    jobs = [(ours_path, theirs_path, seed, seat) for seed in seeds for seat in (0, 1)]
    margins = {}
    banks = []
    with mp.Pool(workers) as pool:
        for seed, seat, margin, mine, theirs in pool.imap_unordered(_duel, jobs, chunksize=2):
            margins.setdefault(seed, {})[seat] = margin
            banks.append((mine, theirs))
    per_game = [m for pair in margins.values() for m in pair.values()]
    per_seed = [statistics.fmean(pair.values()) for pair in margins.values()]
    mean = statistics.fmean(per_seed) if per_seed else 0.0
    sd = statistics.stdev(per_seed) if len(per_seed) > 1 else 0.0
    se = sd / len(per_seed) ** 0.5 if per_seed else 0.0
    return {
        "games": len(per_game),
        "win_rate": 100 * sum(1 for m in per_game if m > 0) / max(1, len(per_game)),
        "mean": mean, "ci_low": mean - 1.96 * se, "ci_high": mean + 1.96 * se,
        "bank": statistics.fmean(b for b, _ in banks) if banks else 0.0,
        "opp": statistics.fmean(o for _, o in banks) if banks else 0.0,
    }


if REFERENCE:
    print(f"{'opponent':<20}{'games':>6}{'win%':>7}{'margin':>11}{'95% CI':>24}"
          f"{'our bank':>11}{'theirs':>10}")
    print("-" * 92)
    ladder = []
    for path in REFERENCE[:12]:
        row = duel(BASELINE, str(path), SEEDS, workers)
        row["name"] = path.stem
        ladder.append(row)
        print(f"{path.stem:<20}{row['games']:>6}{row['win_rate']:>7.1f}{row['mean']:>+11,.0f}"
              f"   [{row['ci_low']:>+8,.0f},{row['ci_high']:>+8,.0f}]"
              f"{row['bank']:>11,.0f}{row['opp']:>10,.0f}")
    beaten = sum(1 for r in ladder if r["mean"] > 0)
    print()
    print(f"our tape beats {beaten} of {len(ladder)} reference agents on margin")
    print("local history: main_v8 beat broker_bea 8-0 by +$41,497 over 4 seeds")
else:
    print("reference-agents dataset not attached")
"""

VERDICT = """BAR_WIN_RATE = 65.0     # a change must justify evicting an active slot
BAR_MARGIN = 5000.0

print(f"{'variant':<20}{'games':>6}{'seeds':>7}{'win%':>7}{'margin':>11}{'95% CI':>24}"
      f"{'bank':>10}{'opp':>10}{'verdict':>10}")
print("-" * 108)
for row in results:
    significant = row["ci_low"] > 0 or row["ci_high"] < 0
    passes = (row["win_rate"] >= BAR_WIN_RATE and row["mean"] >= BAR_MARGIN
              and row["ci_low"] > 0)
    if passes:
        verdict = "PASS"
    elif significant and row["mean"] < 0:
        verdict = "WORSE"
    elif significant and row["mean"] > 0:
        verdict = "better"
    else:
        verdict = "noise"
    print(f"{row['label']:<20}{row['games']:>6}{row['seeds']:>7}{row['win_rate']:>7.1f}"
          f"{row['mean']:>+11,.0f}   [{row['ci_low']:>+8,.0f},{row['ci_high']:>+8,.0f}]"
          f"{row['mean_bank']:>10,.0f}{row['mean_opp']:>10,.0f}{verdict:>10}")
print()
print("ALWAYS read the bank columns before the verdict: a margin of exactly 0 with")
print("banks near the 3,000 starting money means the agents never advanced, not that")
print("the variant is neutral. Six-figure banks mean the tape actually played.")
print()
print("Reading this table:")
print("  WORSE/better = the CI excludes zero, so the effect is real at this sample size.")
print("  noise        = the CI includes zero: with this many games we cannot tell.")
print("  PASS         = the only outcome that would justify evicting an active slot.")
print()
print(f"bar: win rate >= {BAR_WIN_RATE}% AND mean margin >= ${BAR_MARGIN:,.0f} AND CI excludes zero")"""


CELLS = [
    md(INTRO),
    code(SETUP),
    md("""## 1. Cost, measured before committing

A long sweep only makes sense after knowing the per-game cost on this machine."""),
    code(TIMING),
    md("""## 2. The diagnostic that must come before any verdict

A margin of exactly 0 is ambiguous: it is what a true mirror produces AND what an
arena where the agents never advanced produces. The absolute banks tell them apart."""),
    code(SWEEP_HEAD),
    md("""## 3. The sweep"""),
    code(SWEEP),
    md("""## 4. Verdict against the bar"""),
    code(VERDICT),
    md("""## 5. Against a diverse field, not a mirror

Every V10 verdict so far compared the tape with a copy of itself. The published
reference ladder is what turns "better than my own tape" into "better than a field")."""),
    code(REFERENCE),
]


def main():
    # Validate every code cell's SYNTAX before writing, so a broken cell fails HERE
    # instead of after a full push (a stray escape once cost a whole Kaggle run).
    # ast.parse rather than compile(): this only needs to prove the cell parses, and
    # building a code object for text would be unnecessary work on a lint-flagged path.
    for index, cell in enumerate(CELLS):
        if cell["cell_type"] != "code":
            continue
        try:
            ast.parse("".join(cell["source"]))
        except SyntaxError as exc:
            raise SystemExit(f"cell {index} does not parse: {exc}") from exc
        cell["id"] = f"cell-{index}"
    notebook = {
        "cells": CELLS,
        "metadata": {
            "kernelspec": {"display_name": "Python 3", "language": "python",
                           "name": "python3"},
            "language_info": {"name": "python", "version": "3.11"},
        },
        "nbformat": 4,
        "nbformat_minor": 5,
    }
    OUT.write_text(json.dumps(notebook, indent=1))
    print(f"wrote {OUT} ({len(CELLS)} cells)")


if __name__ == "__main__":
    main()
