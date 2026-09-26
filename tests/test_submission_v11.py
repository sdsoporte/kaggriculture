"""Guardrails for the baked v11 submission (``submissions/main_v11.py``).

v11 is a single stdlib-only file that inlines the V8.1 tape and the SE
closed-loop overlay. The load-bearing check is parity: the flattened file must
reproduce the package agent's action stream turn for turn, exactly as a Kaggle
validation episode runs the agent against a copy of itself.

The rest pin the submission format the Kaggle runner depends on:

  * stdlib-only imports (the file must not reach for the ``kaggriculture`` or
    ``kaggle_environments`` packages);
  * a malformed observation degrades to a legal action instead of raising;
  * the exposed entry point is a plain one-argument function, because the
    runner truncates a callable instance's arguments by
    ``__code__.co_argcount``.
"""

import ast
import importlib
import inspect
import sys
from pathlib import Path
from typing import Any

import pytest

from kaggriculture.loading import load_agent
from kaggriculture.runner import run_episode_trace

ROOT = Path(__file__).resolve().parent.parent
SUBMISSION = ROOT / "submissions" / "main_v11.py"

# Full-season self-matches, different seeds. The overlay's gate is state-funded,
# so a self-match mostly stays in byte-identical tape mode; that is the point.
PARITY_SEEDS = (0, 7)


def _actions(run, seat):
    return [step[seat].get("action") for step in run["steps"]]


# --------------------------------------------------------------------------- #
# 1. Parity: flattened file vs package make_agent() + baseline.
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("seed", PARITY_SEEDS)
def test_flattened_agent_matches_package_agent_action_stream(seed):
    """A self-match of the flattened file reproduces the package self-match.

    Both seats are independent copies: each :func:`load_agent` call executes a
    fresh module, matching how Kaggle runs the agent against a copy of itself.
    """
    sl: Any = importlib.import_module("kaggriculture.agents.se_loop")
    flat_run = run_episode_trace(
        load_agent(str(SUBMISSION)), load_agent(str(SUBMISSION)), seed=seed
    )
    package_run = run_episode_trace(sl.make_agent(), sl.make_agent(), seed=seed)

    for seat in (0, 1):
        assert _actions(flat_run, seat) == _actions(package_run, seat), (
            f"seat {seat} action stream diverged on seed {seed}"
        )
    assert flat_run["banks"] == package_run["banks"]
    assert flat_run["turns"] == package_run["turns"]


# --------------------------------------------------------------------------- #
# 2. stdlib-only imports.
# --------------------------------------------------------------------------- #

def test_submission_is_a_single_stdlib_only_file():
    source = SUBMISSION.read_text()
    compile(source, str(SUBMISSION), "exec")  # it must at least compile

    imported: set[str] = set()
    for node in ast.walk(ast.parse(source, filename=str(SUBMISSION))):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, "relative imports are not allowed"
            imported.add((node.module or "").split(".")[0])

    non_stdlib = imported - set(sys.stdlib_module_names)
    assert not non_stdlib, f"non-stdlib imports: {sorted(non_stdlib)}"


# --------------------------------------------------------------------------- #
# 3. A malformed observation never raises.
# --------------------------------------------------------------------------- #

def test_malformed_observation_never_raises():
    agent = load_agent(str(SUBMISSION))
    garbage = (
        {},
        None,
        {"farms": None},
        {"farms": [None]},
        {"step": "x"},
        {"farms": [{}], "private": None},
        {"farms": [{"tiles": None}]},
    )
    for obs in garbage:
        out = agent(obs)
        assert isinstance(out, dict), obs
        assert "farmer" in out and "market" in out


# --------------------------------------------------------------------------- #
# 4. Format: plain function entry point and a sane file size.
# --------------------------------------------------------------------------- #

def test_exposed_agent_is_a_plain_one_argument_function():
    agent = load_agent(str(SUBMISSION))
    assert inspect.isfunction(agent)
    assert not inspect.isclass(agent)
    assert agent.__code__.co_argcount == 1


def test_submission_file_is_under_100_mib():
    assert SUBMISSION.stat().st_size < 100 * 1024 * 1024
