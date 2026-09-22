# SPDX-License-Identifier: Apache-2.0
"""Build immutable suffix counts of a controller's own PLANT requests.

Values, insertion order, endpoint length and detached per-step mappings match
Counter-copy construction. Nothing is inferred about execution, affordability,
opponents or future observations. This only changes table construction cost.
"""
from __future__ import annotations
from collections.abc import Mapping, Sequence
from types import MappingProxyType
from typing import Any


def immutable_plant_suffixes(route: Sequence[Mapping[str, Any]]) -> tuple[Mapping, ...]:
    """Return one detached read-only count mapping per suffix plus the endpoint.

    Only nonnegative increments occur, so ordinary dictionary get/set has the
    same counting semantics without Counter's generic update/copy machinery.
    Each published mapping owns a different dictionary, just as before.
    """
    counts: dict[Any, int] = {}
    suffix: list[Mapping] = [MappingProxyType(counts)] * (len(route) + 1)
    for step in range(len(route) - 1, -1, -1):
        counts = counts.copy()
        row = route[step]
        for action in [row.get('farmer', []), *row.get('hands', [])]:
            if len(action) >= 2 and action[0] == 'PLANT':
                crop = action[1]
                counts[crop] = counts.get(crop, 0) + 1
        suffix[step] = MappingProxyType(counts)
    return tuple(suffix)
