# SPDX-License-Identifier: Apache-2.0
"""Optional all-plan consumer for an injected T15 WholePlanSelector class.

Only choose is extended. Action execution, controller ownership, continuation
and observed fills remain with the original consumers. No optimization runs
here: an injected provider supplies exact weights for the complete input table.
"""
from __future__ import annotations

from copy import deepcopy
from fractions import Fraction
from math import lcm
from typing import Any, Callable, Mapping


def _rational(value: Any) -> Fraction:
    # Float weights are not exact declared probabilities. Use strings instead.
    if isinstance(value, bool) or not isinstance(value, (int, str, Fraction)):
        raise ValueError('Use integers, rational strings or Fraction values')
    return Fraction(value)


def make_selector(selector_type: type, solution_provider: Callable, *, rng=None,
                  max_plans: int = 32, max_streams: int = 256,
                  max_number_bits: int = 4096, max_draw_bits: int = 4096):
    """Construct a fresh optional selector using an existing action transform.

    provider(rows) receives immutable exact rows, baseline first. It returns a
    mapping with weights in exactly that order. Other provider fields are kept
    as provenance, not accepted as an optimality certificate. The adapter checks
    the probability simplex and recomputes its included-stream lower bound; it
    never solves the table or infers scenario probabilities. Only a positive
    distribution labeled optimal is used; budget-limited results keep baseline.

    Resource limits bound table size and exact-number/sampling bit lengths;
    they do not establish the total runtime of the injected provider. Prepare
    a solved result before an action call when that provider is expensive.

    The returned object's transform IS selector_type.transform. Wrap this
    object in the existing ContinuationPlanSelector for continuing feasibility
    and skipped-date handling. Use a fresh instance for every actor/match.
    """
    for limit in (max_plans, max_streams, max_number_bits, max_draw_bits):
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError('Resource limits must be positive integers')
    if not callable(solution_provider):
        raise TypeError('solution_provider must be callable')

    def number(value):
        result = _rational(value)
        if max(abs(result.numerator).bit_length(), result.denominator.bit_length()) > max_number_bits:
            raise ValueError('Exact number exceeds the configured bit budget')
        return result

    class WeightedPlanSelector(selector_type):
        def __init__(self):
            super().__init__(rng=rng)
            self.provider_calls = 0

        def _fallback(self, key, reason, **details):
            # Repeating this same lot/window must not obtain another solution
            # or random opportunity. A genuinely new decision uses a new key.
            self.completed.add(key)
            self.last_decision = {'reason': reason, 'key': key,
                                  'fallback_preserved': True, **details}
            return None

        def choose(self, key, plans, deltas, *, feasible, mode='mixed', learned_start=0):
            if self.active is not None:
                if self.active['key'] != key:
                    raise ValueError('Finish the committed window before another lot')
                return deepcopy(self.active)
            if key in self.completed:
                return None
            try:
                frozen_plans = deepcopy(plans)
                n = len(frozen_plans)
                if not 1 <= n <= max_plans or len(deltas) != n:
                    raise ValueError('Plan/table dimensions differ or exceed budget')
                m = len(deltas[0])
                if not 1 <= m <= max_streams or any(len(row) != m for row in deltas):
                    raise ValueError('A nonempty rectangular table is required')
                rows = tuple(tuple(number(v) for v in row) for row in deltas)
                if any(rows[0]):
                    raise ValueError('The first row must be the zero baseline')
                ids = [p['id'] for p in frozen_plans]
                if any(not isinstance(i, str) or not i for i in ids) or len(set(ids)) != n:
                    raise ValueError('Plan IDs must be distinct nonempty strings')
                if not callable(feasible):
                    raise ValueError('A complete-plan feasibility callback is required')
                # Check ALL constituents, including zero-weight and baseline.
                verdicts = [feasible(deepcopy(p)) for p in frozen_plans]
                if any(verdict is not True for verdict in verdicts):
                    return self._fallback(key, 'constituent_not_supported')
                if mode == 'baseline':
                    return self._fallback(key, 'baseline')
                if mode == 'pure':
                    if type(learned_start) is not int or not 0 <= learned_start < m:
                        raise ValueError('Invalid learned column start')
                    eligible = [i for i in range(1, n)
                                if min(rows[i]) >= 0 and min(rows[i][learned_start:]) > 0]
                    chosen = max(eligible, key=lambda i: (min(rows[i][learned_start:]), sum(rows[i])), default=0)
                    raw = {'weights': [str(int(i == chosen)) for i in range(n)],
                           'status': 'pure_mode'}
                elif mode == 'mixed':
                    self.provider_calls += 1
                    raw = solution_provider(rows)
                else:
                    raise ValueError('mode must be baseline, pure or mixed')
                if not isinstance(raw, Mapping):
                    raise ValueError('Provider result must be a mapping')
                raw = deepcopy(dict(raw))
                if mode == 'mixed' and raw.get('status') != 'optimal':
                    return self._fallback(key, 'provider_not_optimal', provider_status=raw.get('status'))
                if len(raw['weights']) != n:
                    raise ValueError('One weight per supplied plan is required')
                weights = tuple(number(v) for v in raw['weights'])
                if any(w < 0 for w in weights) or sum(weights) != 1:
                    raise ValueError('Weights must lie exactly in the probability simplex')
                columns = tuple(sum(weights[i] * rows[i][j] for i in range(n)) for j in range(m))
                floor = min(columns)
                if floor <= 0:
                    return self._fallback(key, 'nonpositive_included_stream_floor', value=str(floor))
                if mode == 'mixed' and number(raw['value']) != floor:
                    return self._fallback(key, 'provider_value_mismatch')
                answer = {'weights': [str(w) for w in weights], 'value': str(floor),
                          'column_expectations': [str(v) for v in columns],
                          'plan_ids': ids, 'provider_result': raw,
                          'optimality': 'not_checked_by_runtime_adapter',
                          'scope': 'expectation over supplied complete streams',
                          'probabilities': None}
                if weights[0] == 1:
                    return self._fallback(key, 'baseline', solution=answer)
                denominator = 1
                for w in weights:
                    denominator = lcm(denominator, w.denominator)
                    if denominator.bit_length() > max_draw_bits:
                        return self._fallback(key, 'sampling_budget_exhausted')
            except Exception:
                # Callback exceptions can contain sensitive runtime context.
                return self._fallback(key, 'solution_or_contract_unavailable')
            # A local RNG is the only draw. No modulo of a smaller random range
            # and no float rounding that silently drops small probabilities.
            try:
                draw = self.rng.randrange(denominator)
                if type(draw) is not int or not 0 <= draw < denominator:
                    raise ValueError('Random generator returned an invalid index')
            except Exception:
                return self._fallback(key, 'sampling_unavailable')
            self.draws += 1
            cumulative = 0
            for index, weight in enumerate(weights):
                cumulative += int(weight * denominator)
                if draw < cumulative:
                    break
            self.active = {'key': key, 'plan': deepcopy(frozen_plans[index]),
                           'plan_index': index, 'weights': answer['weights'],
                           'solution': answer, 'mode': mode}
            self.last_decision = {'reason': 'committed', **deepcopy(self.active)}
            return deepcopy(self.active)

    return WeightedPlanSelector()
