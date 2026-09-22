# SPDX-License-Identifier: Apache-2.0
"""Compact, detached public history for the existing frozen SELL observer.

SellScheduler.observe consumes only the previous rival tile grid.  Preserve that
input and the step/player metadata used by the canonical recovery checkpoint,
without copying own private inventory, market data, or other unused observation
fields.  This is the existing TitanAgent._seller_public_observation projection
made reusable at the point where FrozenSelected stores its previous observation.
"""
from copy import deepcopy
from typing import Any, Mapping


def seller_public_observation(
    observation: Mapping[str, Any] | None, *, copy_tiles: bool = True
) -> dict[str, Any] | None:
    """Return the observer's two-farm-shaped public snapshot, or ``None``.

    The default detaches every mutable rival tile.  ``copy_tiles=False`` is for
    retaining an already-private, completed snapshot which will be replaced,
    never mutated; it must not be used on a caller-owned live observation.
    This projection serves a single player's episode, as does the observer.
    """
    if observation is None:
        return None
    player = int(observation['player'])
    rival = 1 - player
    tiles = observation['farms'][rival]['tiles']
    farms = [{'tiles': []}, {'tiles': []}]
    farms[rival] = {'tiles': deepcopy(tiles) if copy_tiles else tiles}
    return {'step': int(observation['step']), 'player': player, 'farms': farms}
