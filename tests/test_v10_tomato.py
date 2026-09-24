"""Unit tests for the V10 crop-swap transform (v10_tomato).

These exist because the first version shipped the SAME bug the animal swap shipped
and `tests/test_v10_mix.py` already pins: the transform emitted the payload as the
verb (`["TOMATO"]`) instead of `["PLANT", "TOMATO"]`. The engine ignores an unknown
verb, so 24 plantings silently vanished and the first smoke run collapsed the farm
by $72,251. Writing the module fresh and skipping the test for its one nontrivial
transform is exactly how that recurred.
"""

import pytest

from kaggriculture.agents.v10_tomato import make_agent


def test_make_agent_rejects_a_no_op_swap():
    with pytest.raises(ValueError, match="must differ"):
        make_agent(source="WHEAT", target="WHEAT")


def test_make_agent_rejects_a_negative_budget():
    with pytest.raises(ValueError, match="negative"):
        make_agent(max_swaps=-1)


def _agent_with(obs, **kwargs):
    """Kept for symmetry with the other transform tests; unused."""
    return make_agent(**kwargs)


def test_plant_swap_keeps_the_verb():
    # Regression: ["TOMATO"] is not an action verb; the engine ignores it.
    import kaggriculture.agents.v10_tomato as module

    class Stub:
        def __call__(self, obs):
            return {"farmer": ["PLANT", "WHEAT"], "hands": [["PLANT", "WHEAT"]],
                    "market": [["BUY_SEED", "WHEAT", 10]]}

    original = module.load_agent
    module.load_agent = lambda _path: Stub()
    try:
        # max_swaps=2 because the stub has TWO plant actions (farmer + one hand);
        # with a budget of 1 only the farmer converts, which is what an earlier
        # version of this test got wrong.
        agent = module.make_agent(source="WHEAT", target="TOMATO", max_swaps=2,
                                  from_step=0)
        action = agent({"step": 0})
    finally:
        module.load_agent = original

    assert action["farmer"] == ["PLANT", "TOMATO"], action
    assert action["hands"][0] == ["PLANT", "TOMATO"], action
    # The seed order splits: one unit to the target, the rest stays with the source.
    assert ["BUY_SEED", "TOMATO", 1] in action["market"], action["market"]
    assert ["BUY_SEED", "WHEAT", 9] in action["market"], action["market"]


def test_budget_bounds_the_swaps():
    import kaggriculture.agents.v10_tomato as module

    class Stub:
        def __call__(self, obs):
            return {"farmer": ["PLANT", "WHEAT"], "hands": [], "market": []}

    original = module.load_agent
    module.load_agent = lambda _path: Stub()
    try:
        agent = module.make_agent(max_swaps=1, from_step=0)
        first = agent({"step": 0})
        second = agent({"step": 1})
    finally:
        module.load_agent = original

    assert first["farmer"] == ["PLANT", "TOMATO"]
    assert second["farmer"] == ["PLANT", "WHEAT"], "the budget must stop further swaps"


def test_from_step_gate_leaves_the_opening_alone():
    import kaggriculture.agents.v10_tomato as module

    class Stub:
        def __call__(self, obs):
            return {"farmer": ["PLANT", "WHEAT"], "hands": [], "market": []}

    original = module.load_agent
    module.load_agent = lambda _path: Stub()
    try:
        agent = module.make_agent(max_swaps=99, from_step=100)
        early = agent({"step": 40})
        late = agent({"step": 140})
    finally:
        module.load_agent = original

    assert early["farmer"] == ["PLANT", "WHEAT"]
    assert late["farmer"] == ["PLANT", "TOMATO"]
