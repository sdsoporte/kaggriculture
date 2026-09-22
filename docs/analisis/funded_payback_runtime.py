# SPDX-License-Identifier: Apache-2.0
"""Canonical-route adapter for ECON's attributed payback admission.

The official engine treats recognized zero-quantity market rows as no-ops while
retaining their queue slots. Historical Arlene routes contain those placeholders.
ECON deliberately rejects malformed inputs, so this adapter translates only that
known engine no-op shape to PASS for prospective evaluation. The executed route
and the peer-owned ECON implementation remain unchanged.
"""
from __future__ import annotations

from copy import deepcopy
import math
import time


_POST_ADMISSION_SECONDS = 0.30


_QUANTITY_ORDERS = frozenset(
    ('BUY_SEED', 'BUY_PRODUCT', 'BUY_ANIMAL', 'SELL')
)


def _engine_noop(order):
    if not isinstance(order, list) or len(order) < 3 or order[0] not in _QUANTITY_ORDERS:
        return False
    try:
        return int(order[2]) <= 0
    except (TypeError, ValueError):
        return False


def _evaluation_row(row):
    market = row.get('market', [])
    if not isinstance(market, list) or not any(_engine_noop(order) for order in market):
        return row
    result = deepcopy(row)
    result['market'] = [(['PASS'] if _engine_noop(order) else order)
                        for order in result['market']]
    return result


def _evaluation_route(route):
    return [_evaluation_row(row) for row in route]


def make_admission(base_class):
    """Bind the adapter to the exact packaged ECON class without forking it."""
    class RuntimeFundedPaybackAdmission(base_class):
        _action_deadline = None

        def begin_action(self, deadline):
            deadline = float(deadline)
            if not math.isfinite(deadline):
                raise ValueError('action deadline must be finite')
            self._action_deadline = deadline

        @staticmethod
        def _candidate(base, variant, now):
            candidate, inferred = base_class._candidate(base, variant, now)
            candidate = _evaluation_route(candidate)
            bundle = variant.get('bundle', {})
            rejoin = bundle.get('rejoin_step', inferred) if isinstance(bundle, dict) else inferred
            if isinstance(rejoin, bool) or not isinstance(rejoin, int):
                raise ValueError('proposal rejoin step must be an integer')
            if not now < rejoin <= len(candidate) or rejoin < inferred:
                raise ValueError('proposal rejoin step lies outside its physical route tail')
            return candidate, rejoin

        def __call__(self, mechanics, observation, configuration, routes, proposals):
            evaluation_routes = {key: _evaluation_route(route)
                                 for key, route in routes.items()}
            configured_seconds = self.seconds
            action_deadline = self._action_deadline
            try:
                if action_deadline is not None:
                    self.seconds = min(
                        configured_seconds,
                        max(0.0, action_deadline-time.monotonic()-_POST_ADMISSION_SECONDS))
                return super().__call__(mechanics, observation, configuration,
                                        evaluation_routes, proposals)
            finally:
                self.seconds = configured_seconds
                self._action_deadline = None

    RuntimeFundedPaybackAdmission.__name__ = 'RuntimeFundedPaybackAdmission'
    RuntimeFundedPaybackAdmission.__qualname__ = 'RuntimeFundedPaybackAdmission'
    return RuntimeFundedPaybackAdmission
