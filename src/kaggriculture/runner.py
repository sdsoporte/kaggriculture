"""Run a single Kaggriculture episode between two agent callables."""

from kaggle_environments import make

ENV_ID = "kaggriculture"


def _summarise(env, seed):
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


def run_episode(agent_a, agent_b, seed):
    """Run one full season with ``agent_a`` on seat 0 and ``agent_b`` on seat 1.

    Returns a dict: {seed, banks: (a, b), winner: 0|1|None, turns}.
    """
    env = make(ENV_ID, configuration={"seed": seed})
    env.run([agent_a, agent_b])
    return _summarise(env, seed)


def run_episode_trace(agent_a, agent_b, seed):
    """Like :func:`run_episode`, but also returns the raw ``env.steps`` trace.

    Verification tooling needs the per-turn observations to prove that an
    intended action actually landed: the engine silently swallows invalid
    actions, so a broken agent looks merely unlucky instead of broken.

    The trace is ~720 turns of full board observations - never print it, derive
    per-day summaries from it instead. Kept separate from :func:`run_episode`
    so the paired benchmark does not retain a trace for every game.
    """
    env = make(ENV_ID, configuration={"seed": seed})
    env.run([agent_a, agent_b])
    summary = _summarise(env, seed)
    summary["steps"] = env.steps
    return summary
