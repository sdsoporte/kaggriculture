"""Play a recorded Kaggle replay seat back as a live opponent.

Why this module exists
----------------------
Every opponent we own is too weak: the 10 published reference agents lose
10/10 by $100k+, so no variant of our agent is actually challenged. A 21 GB
cache of real top-team replays (``scratch/corpus_full/*.json``) is the only
available source of *real field* opposition. This module turns one recorded
seat into a deterministic ``agent(obs) -> action`` callable so those matches
can be replayed against our agent.

The indexing, established empirically (NOT from documentation)
-------------------------------------------------------------
``data["steps"][i][seat]`` holds ``{"action": ..., "observation": ...}``. Four
separate attempts in this project lost time to guessing the alignment, so it
was measured instead:

* The observation stored at index ``i`` is the state an agent is handed at
  turn ``i`` -- its ``step`` (seat 0) or ``day * 24 + hour`` (both seats)
  equals ``i``. Verified: seat 0's ``step`` runs 0, 1, 2, ...; seat 1 has no
  ``step`` key but its day/hour match.
* The action that must be played **at** that turn is stored one index later, at
  ``steps[i + 1][seat]["action"]``. Equivalently: the action recorded at index
  ``i`` is the one that PRODUCED ``observation[i]``, so it acted on the state at
  index ``i - 1``; a caller at turn ``t`` therefore wants ``action[t + 1]``.

Reproduction proof (engine pin 1.32.7, replay ``112219773``, seed 1761340719),
both seats replayed against each other and compared to the recorded match::

    offset  0 -> final banks (0, 12886)                  (actions never started)
    offset -1 -> final banks (0, 1552)
    offset +1 -> final banks (105960, 103366) == recorded, whole 719-turn
                 trajectory equal money-for-money

Consequences of the alignment:

* ``steps[0].action`` is a placeholder (never used); the action taken at live
  turn 0 is ``steps[1].action``.
* A caller can index purely from the observation, so the opponent is stateless
  across games -- important because the harness pairs it on both seats.

Running past the end of a recording, a malformed entry, a missing seat or a
non-dict action all degrade to a legal PASS action with an empty market. An
exception raised inside an agent makes it inert for the rest of the season
while the opponent keeps playing, which is indistinguishable from a real loss;
returning a fallback keeps the measurement honest.
"""

from __future__ import annotations

import copy
import json

from kaggriculture.loading import current_step

#: Empirically established turn -> recorded-action offset. See module docstring.
ACTION_OFFSET = 1

#: Legal do-nothing action returned when no recorded action is available.
FALLBACK_ACTION = {"farmer": ["PASS"], "hands": [], "market": []}


def load_replay(path):
    """Load one replay JSON and return its ``steps`` list.

    One replay is ~35 MB and parses to a larger Python object, so callers
    should process replays one at a time and release each one before loading
    the next.
    """
    try:
        with open(path) as fh:
            data = json.load(fh)
    except OSError as exc:
        raise FileNotFoundError(f"cannot read replay {path}: {exc}") from exc
    except ValueError as exc:
        raise ValueError(f"replay {path} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"replay {path} is not a JSON object")
    steps = data.get("steps")
    if not isinstance(steps, list):
        raise ValueError(f"replay {path} has no steps list")
    return steps


def _turn_of(obs, fallback):
    """Turn index for an observation, or ``fallback`` when none can be read.

    Prefers ``obs["step"]`` and falls back to ``day * 24 + hour`` exactly like
    :func:`kaggriculture.loading.current_step`; the difference is that a missing
    or unusable observation yields the caller's monotonic ``fallback`` instead
    of silently restarting at turn 0.
    """
    if isinstance(obs, dict) and (obs.get("step") is not None or "day" in obs or "hour" in obs):
        return current_step(obs)
    return fallback


def _entry_action(entry, seat):
    """Action for ``seat`` from one ``steps[i]`` entry, or ``None`` if malformed."""
    if not isinstance(entry, (list, tuple)) or seat >= len(entry):
        return None
    seat_state = entry[seat]
    if not isinstance(seat_state, dict):
        return None
    action = seat_state.get("action")
    return action if isinstance(action, dict) else None


def make_replay_opponent(path=None, seat=0, steps=None, offset=ACTION_OFFSET):
    """Return ``agent(obs) -> action`` replaying ``seat``'s recorded actions.

    Args:
        path: replay file path (used only when ``steps`` is not supplied).
        seat: 0 or 1 -- which recorded seat to replay.
        steps: pre-parsed replay ``steps`` list. Pass it when the caller already
            holds the replay (e.g. the corpus harness uses one load for both
            seats) to avoid a second 35 MB parse.
        offset: action index minus observed turn. The default (:data:`ACTION_OFFSET`)
            is the empirically verified value; the parameter exists so the
            acceptance harness can print the wrong offsets as evidence.

    The returned callable keeps only a monotonic counter for the pathological
    case where an observation carries no usable turn information; normal engine
    observations always carry ``step`` or ``day``/``hour``, so the opponent does
    not depend on call order and can be safely reused across paired games.
    """
    if steps is None:
        if path is None:
            raise ValueError("make_replay_opponent needs either path or steps")
        steps = load_replay(path)
    try:
        seat = int(seat)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"seat must be 0 or 1, got {seat!r}") from exc
    if seat not in (0, 1):
        raise ValueError(f"seat must be 0 or 1, got {seat!r}")
    n = len(steps)
    last_fallback = [-1]

    def agent(obs):
        last_fallback[0] += 1
        turn = _turn_of(obs, last_fallback[0])
        idx = turn + offset
        if 0 <= idx < n:
            action = _entry_action(steps[idx], seat)
            if action is not None:
                # Deep-copy: the engine must never be able to mutate the loaded
                # recording, which is reused for the other seat and the pairing.
                return copy.deepcopy(action)
        return copy.deepcopy(FALLBACK_ACTION)

    return agent
