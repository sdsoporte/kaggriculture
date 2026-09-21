"""Load a single-file Kaggriculture agent from a .py path (or a builtin name).

A valid agent file exposes a top-level ``agent(obs) -> action dict``.
Builtin engine agents ('pass', 'random', 'starter') resolve by name.
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
