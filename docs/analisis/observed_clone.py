# SPDX-License-Identifier: Apache-2.0
"""Detached JSON-value copies for observed unit-projection inputs.

Agent IPC deserializes observations as JSON and may wrap mappings in Struct.
The deterministic mechanics consume mapping/list values, not wrapper classes.
This helper copies that JSON value into plain dict/list containers without the
cost of reconstructing every Struct wrapper through Python's pickle protocol.

This is deliberately NOT a generic replacement for copy.deepcopy: JSON does
not preserve Python class identity, mapping attributes, aliases or cycles. Use
it only at the post_units observed-farm/private boundary. Existing route state,
SELL checkpoints and arbitrary Python objects keep their current copy rules.
"""
from __future__ import annotations
from copy import deepcopy
from typing import Any

_ATOMIC = frozenset((str, int, float, bool, type(None)))


def detached_json_value(value: Any) -> Any:
    """Detach JSON containers; retain exact scalar values and mapping order.

    Non-JSON leaf objects are delegated to deepcopy for compatibility with
    diagnostic fixtures. Valid IPC inputs never contain those objects.
    """
    if type(value) in _ATOMIC:
        return value
    if isinstance(value, dict):
        return {key: detached_json_value(child) for key, child in value.items()}
    if isinstance(value, list):
        return [detached_json_value(child) for child in value]
    return deepcopy(value)
