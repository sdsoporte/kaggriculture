# SPDX-License-Identifier: Apache-2.0
"""Exact maximin for baseline plus at most two complete alternative plans.

The bounded action family is deliberate: this solves that finite table, with
no calibrated model probability and no claim about omitted rival streams.
"""
from fractions import Fraction as F
from functools import lru_cache


def _number(value):
    if isinstance(value, float):
        return F(str(value))
    return F(value)


@lru_cache(maxsize=256)
def _solve(rows):
    n, m = len(rows), len(rows[0])
    weights = [F(1)] + [F(0)] * (n - 1)
    best = F(0)
    # Every pure plan is also an endpoint of the feasible simplex.
    for i, row in enumerate(rows):
        value = min(row)
        if value > best:
            best = value
            weights = [F(int(j == i)) for j in range(n)]
    if n == 3:
        a, b = rows[1:]
        # A positive optimum needs no zero-baseline mass. The lower envelope
        # of these affine columns attains its maximum at an intersection or
        # endpoint. Enumerate all breakpoints with exact rational arithmetic.
        candidates = set()
        for j in range(m):
            for k in range(j):
                denominator = (a[j] - b[j]) - (a[k] - b[k])
                if denominator:
                    p = (b[k] - b[j]) / denominator
                    if 0 < p < 1:
                        candidates.add(p)
        for p in sorted(candidates):
            value = min(p * x + (1-p) * y for x, y in zip(a, b))
            if value > best:
                best, weights = value, [F(0), p, 1-p]
    columns = tuple(sum(weights[i] * rows[i][j] for i in range(n)) for j in range(m))
    return tuple(weights), best, columns


def solve_table(deltas):
    """Return detached exact weights and included-stream expected margins.

    Input rows: a0 (all zero), then zero, one or two alternatives. Each row
    contains own-minus-rival cash changes against that SAME baseline and SAME
    complete rival stream. Up to 32 correlated columns; alpha is always zero.
    Feasibility of every constituent is checked by the caller/plan contract.
    """
    rows = tuple(tuple(_number(x) for x in row) for row in deltas)
    if not 1 <= len(rows) <= 3 or not rows or not 1 <= len(rows[0]) <= 32:
        raise ValueError('Expected 1..3 complete plans and 1..32 streams')
    if any(len(row) != len(rows[0]) for row in rows) or any(rows[0]):
        raise ValueError('Rectangular table with a zero baseline row required')
    weights, value, columns = _solve(rows)
    return {'weights': [str(x) for x in weights], 'value': str(value),
            'column_expectations': [str(x) for x in columns],
            'pure_minima': [str(min(row)) for row in rows],
            'alpha': 0, 'probabilities': None, 'exact': True,
            'scope': 'maximin expectation over supplied complete streams'}
