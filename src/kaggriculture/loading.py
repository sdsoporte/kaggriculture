"""Load a single-file Kaggriculture agent from a .py path (or a builtin name).

A valid agent file exposes a top-level ``agent(obs) -> action dict``.
Builtin engine agents ('pass', 'random', 'starter') resolve by name.

This module is also the shared runtime home for ``current_step``, the observation
helper every tape overlay needs. It lives next to ``load_agent`` because every
overlay already imports this module: the helper had been copy-pasted into four
overlays, so a single fragility (an unguarded ``int()`` that raised on a
malformed observation and would take the agent out of the match) was fixed
nowhere.
"""

import importlib.util
from pathlib import Path

BUILTIN_AGENTS = ("pass", "random", "starter")


def load_agent(source):
    """Return the ``agent(obs)`` callable for a file path or builtin name."""
    if source in BUILTIN_AGENTS:
        from kaggle_environments import make

        return make("kaggriculture").agents[source]

    path = Path(source)
    if not path.is_file():
        raise FileNotFoundError(f"agent file not found: {path}")
    spec = importlib.util.spec_from_file_location(f"kaggriculture_agent_{path.stem}", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot import agent from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    agent = getattr(module, "agent", None)
    if not callable(agent):
        raise AttributeError(f"{path} does not expose a callable agent(obs)")
    return agent


def current_step(obs):
    """Turn index from an observation, the way every tape agent derives it.

    Uses ``step`` when present and falls back to ``day * 24 + hour``. Never
    raises: an observation whose ``step``/``day``/``hour`` is missing, ``None`` or
    unparseable degrades to turn 0 rather than propagating an exception into the
    match loop.
    """
    try:
        raw = obs.get("step")
        if raw is not None:
            return int(raw)
        return int(obs.get("day", 0)) * 24 + int(obs.get("hour", 0))
    except (AttributeError, TypeError, ValueError):
        return 0
