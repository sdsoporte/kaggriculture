"""Phase-1 smoke tests: engine determinism, paired zero-effect control, baseline floor."""

from pathlib import Path

from kaggriculture.agents.baseline import agent as baseline_agent
from kaggriculture.harness import paired_benchmark
from kaggriculture.loading import load_agent
from kaggriculture.runner import run_episode

ROOT = Path(__file__).resolve().parent.parent
FINN_PATH = ROOT / "data" / "reference-agents" / "fallow_finn.py"


def test_engine_determinism_same_seed_same_banks():
    finn = load_agent(str(FINN_PATH))
    first = run_episode(baseline_agent, finn, seed=42)
    second = run_episode(baseline_agent, finn, seed=42)
    assert first["banks"] == second["banks"]


def test_paired_benchmark_self_play_has_zero_net_margin():
    report = paired_benchmark(baseline_agent, baseline_agent, seeds=[3, 17])
    assert report["wins"] == report["losses"]
    assert report["wins"] - report["losses"] == 0
    assert report["mean_margin"] == 0


def test_baseline_beats_fallow_finn_both_seats():
    finn = load_agent(str(FINN_PATH))
    report = paired_benchmark(baseline_agent, finn, seeds=[42, 7, 1, 99])
    for row in report["per_seed"]:
        assert row["ab_banks"][0] > row["ab_banks"][1], row
        assert row["ba_banks"][0] > row["ba_banks"][1], row
