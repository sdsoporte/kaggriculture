"""Run a single Kaggriculture episode between two agent callables."""

from kaggle_environments import make

ENV_ID = "kaggriculture"


def run_episode(agent_a, agent_b, seed):
    """Run one full season with ``agent_a`` on seat 0 and ``agent_b`` on seat 1.

    Returns a dict: {seed, banks: (a, b), winner: 0|1|None, turns}.
    """
    env = make(ENV_ID, configuration={"seed": seed})
    env.run([agent_a, agent_b])
    banks = []
    for seat in (0, 1):
        obs = env.steps[-1][seat]["observation"]
        banks.append(obs["farms"][obs["player"]]["money"])
    winner = 0 if banks[0] > banks[1] else 1 if banks[1] > banks[0] else None
    return {
        "seed": seed,
        "banks": (banks[0], banks[1]),
        "winner": winner,
        "turns": len(env.steps) - 1,
    }
