# SPDX-License-Identifier: Apache-2.0
"""Bounded, exact finite-table maximin with primal/dual certificates.

Rows are complete plans, columns are complete correlated rival streams. Row
zero must be the unchanged zero-delta baseline. No game state or probabilities
are inferred. Runtime uses only the Python standard library.
"""
from __future__ import annotations

from fractions import Fraction as F
from copy import deepcopy
from functools import lru_cache
import hashlib
import json
from typing import Any, Iterable

MAX_PLANS = 9
MAX_STREAMS = 32


def _fraction(value: Any) -> F:
    if isinstance(value, bool):
        raise ValueError('Boolean is not a cash receipt')
    try:
        return F(str(value)) if isinstance(value, float) else F(value)
    except (ValueError, TypeError, OverflowError, ZeroDivisionError) as exc:
        raise ValueError('Finite rational receipts are required') from exc


def _rows(deltas: Iterable[Iterable[Any]]) -> tuple[tuple[F, ...], ...]:
    rows = tuple(tuple(_fraction(x) for x in row) for row in deltas)
    if not 1 <= len(rows) <= MAX_PLANS:
        raise ValueError('Expected 1..9 complete plans, including the baseline')
    width = len(rows[0])
    if not 1 <= width <= MAX_STREAMS or any(len(r) != width for r in rows):
        raise ValueError('Expected a rectangular table with 1..32 complete streams')
    if any(rows[0]):
        raise ValueError('First row must be the unchanged zero-delta baseline')
    return rows


def _digest(rows: tuple[tuple[F, ...], ...]) -> str:
    body = json.dumps([[str(x) for x in r] for r in rows],
                      separators=(',', ':')).encode('ascii')
    return hashlib.sha256(body).hexdigest()


def _expectations(rows, weights, dual):
    n, m = len(rows), len(rows[0])
    columns = [sum((weights[i] * rows[i][j] for i in range(n)), F(0))
               for j in range(m)]
    row_values = [sum((dual[j] * row[j] for j in range(m)), F(0))
                  for row in rows]
    return columns, row_values


def verify_certificate(deltas, result: dict) -> dict:
    """Independently check exact bounds without running the simplex algorithm.

    A normalized row mixture proves the lower bound; a normalized column
    mixture proves the upper bound. Equal bounds certify global finite-table
    optimality. Column weights are an adversarial witness, not fitted beliefs.
    """
    try:
        rows = _rows(deltas)
        if result['status'] not in {'optimal', 'pivot_limit', 'bit_limit'}:
            raise ValueError('Unknown optimization status')
        p = tuple(_fraction(v) for v in result['weights'])
        q = tuple(_fraction(v) for v in result['dual_weights'])
        if len(p) != len(rows) or len(q) != len(rows[0]):
            raise ValueError('Certificate dimensions differ from the table')
        if min(p) < 0 or min(q) < 0 or sum(p) != 1 or sum(q) != 1:
            raise ValueError('Certificate weights must be normalized and nonnegative')
        columns, row_values = _expectations(rows, p, q)
        lower, upper = min(columns), max(row_values)
        if result['table_sha256'] != _digest(rows):
            raise ValueError('Certificate table hash differs')
        if _fraction(result['value']) != lower or _fraction(result['upper_bound']) != upper:
            raise ValueError('Reported bounds differ from exact expectations')
        if _fraction(result['gap']) != upper - lower or lower > upper:
            raise ValueError('Reported bound gap differs')
        if list(map(_fraction, result['column_expectations'])) != columns:
            raise ValueError('Column expectations differ')
        if list(map(_fraction, result['row_expectations_under_dual'])) != row_values:
            raise ValueError('Dual row expectations differ')
        if result['status'] == 'optimal' and lower != upper:
            raise ValueError('An optimal result must close the exact bound gap')
        if result.get('exact') is not (result['status'] == 'optimal' and lower == upper):
            raise ValueError('Exact-optimality flag differs')
        return {'valid': True, 'optimal': lower == upper,
                'lower_bound': str(lower), 'upper_bound': str(upper)}
    except (ValueError, TypeError, KeyError, IndexError) as exc:
        return {'valid': False, 'optimal': False, 'reason': str(exc)}


@lru_cache(maxsize=64)
def _solve_normalized(rows, *, max_pivots: int = 128, max_bits: int = 512) -> dict:
    """Maximize worst expected baseline-relative own-minus-rival cash.

    Solve max 1'y subject to (D + shift)y <= 1, y >= 0 using exact
    primal simplex, feasible slack initialization and Bland pivot ordering.
    Positive shift makes the associated matrix-game reduction bounded. Slack
    reduced costs yield the row strategy; primal variables yield the dual
    column witness. No support-size restriction beyond the supplied table.

    At a pivot/arithmetic limit, return the unchanged baseline and a checked
    upper-bound witness, NEVER an unfinished strategy labeled optimal. Limits
    bound arithmetic work, not wall-clock time. Bad input raises ValueError.
    """
    if type(max_pivots) is not int or not 0 <= max_pivots <= 4096:
        raise ValueError('max_pivots must be an integer in 0..4096')
    if type(max_bits) is not int or not 16 <= max_bits <= 4096:
        raise ValueError('max_bits must be an integer in 16..4096')
    n, m = len(rows), len(rows[0])
    too_big = lambda x: max(x.numerator.bit_length(), x.denominator.bit_length()) > max_bits
    if any(too_big(x) for row in rows for x in row):
        raise ValueError('Input receipts exceed the arithmetic bit budget')
    shift = max(F(1), F(1) - min(min(row) for row in rows))
    table = [[x + shift for x in row] + [F(int(i == k)) for k in range(n)] + [F(1)]
             for i, row in enumerate(rows)]
    table.append([F(-1)] * m + [F(0)] * (n + 1))
    basis = list(range(m, m + n))
    pivots = 0
    status = 'optimal'
    while True:
        entering = next((j for j, cost in enumerate(table[-1][:-1]) if cost < 0), None)
        if entering is None:
            break
        if pivots >= max_pivots:
            status = 'pivot_limit'
            break
        if any(too_big(x) for row in table for x in row):
            status = 'bit_limit'
            break
        eligible = [i for i in range(n) if table[i][entering] > 0]
        if not eligible:
            raise ArithmeticError('Positive shifted game unexpectedly unbounded')
        leaving = min(eligible, key=lambda i: (table[i][-1] / table[i][entering], basis[i]))
        pivot = table[leaving][entering]
        table[leaving] = [x / pivot for x in table[leaving]]
        for i in range(n + 1):
            if i != leaving and table[i][entering]:
                factor = table[i][entering]
                table[i] = [a - factor * b for a, b in zip(table[i], table[leaving])]
        basis[leaving] = entering
        pivots += 1
    y = [F(0)] * m
    for i, var in enumerate(basis):
        if var < m:
            y[var] = table[i][-1]
    total = sum(y)
    dual = [v / total for v in y] if total else [F(1, m)] * m
    weights = [F(1)] + [F(0)] * (n - 1)
    if status == 'optimal':
        x = table[-1][m:m + n]
        if total <= 0 or min(x) < 0 or sum(x) != total:
            raise ArithmeticError('Invalid exact primal/dual simplex extraction')
        weights = [v / total for v in x]
        if min(_expectations(rows, weights, dual)[0]) == 0:
            # Keep the incumbent on an exact tie; do not introduce needless randomness.
            weights = [F(1)] + [F(0)] * (n - 1)
    columns, row_values = _expectations(rows, weights, dual)
    lower, upper = min(columns), max(row_values)
    result = {
        'weights': list(map(str, weights)), 'value': str(lower),
        'column_expectations': list(map(str, columns)),
        'pure_minima': [str(min(row)) for row in rows],
        'dual_weights': list(map(str, dual)),
        'row_expectations_under_dual': list(map(str, row_values)),
        'upper_bound': str(upper), 'gap': str(upper - lower),
        'support': [i for i, w in enumerate(weights) if w],
        'status': status, 'exact': status == 'optimal' and lower == upper,
        'arithmetic_exact': True, 'pivots': pivots, 'max_pivots': max_pivots,
        'max_bits': max_bits, 'alpha': 0, 'probabilities': None,
        'table_sha256': _digest(rows),
        'scope': 'maximin expectation over supplied complete plans and streams',
    }
    certificate = verify_certificate(rows, result)
    if not certificate['valid']:
        raise ArithmeticError('Exact solution certificate failed: ' + certificate['reason'])
    result['certificate_valid'] = True
    return result


def solve_full_table(deltas, *, max_pivots: int = 128, max_bits: int = 512) -> dict:
    """Solve the same exact game, reusing up to 64 identical ordered tables.

    Cache identity includes every canonical rational entry, its row/column
    position, and both computation budgets. No observations or actor state
    are stored. Each call validates inputs and returns a detached result.
    Result ``pivots`` describes the solve that produced the certificate, not
    new work on a cache hit. Cache misses retain the original algorithm.
    """
    rows = _rows(deltas)
    # Validate before lookup: bool and integer budgets can otherwise compare
    # equal as cache keys despite having different public input contracts.
    if type(max_pivots) is not int or not 0 <= max_pivots <= 4096:
        raise ValueError('max_pivots must be an integer in 0..4096')
    if type(max_bits) is not int or not 16 <= max_bits <= 4096:
        raise ValueError('max_bits must be an integer in 16..4096')
    return deepcopy(_solve_normalized(rows, max_pivots=max_pivots, max_bits=max_bits))


def clear_table_cache() -> None:
    """Release retained tables/results and reset cache counters in this process."""
    _solve_normalized.cache_clear()


def table_cache_info() -> dict:
    """Return detached hit/miss/entry counts; these are not game statistics."""
    return dict(_solve_normalized.cache_info()._asdict())



def _main() -> None:
    import argparse
    from pathlib import Path
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', type=Path, help='JSON array, or object with deltas')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--max-pivots', type=int, default=128)
    parser.add_argument('--max-bits', type=int, default=512)
    args = parser.parse_args()
    data = json.loads(args.input.read_text(encoding='utf-8'))
    result = solve_full_table(data['deltas'] if isinstance(data, dict) else data,
                              max_pivots=args.max_pivots, max_bits=args.max_bits)
    text = json.dumps(result, indent=2) + '\n'
    if args.output:
        args.output.write_text(text, encoding='utf-8')
    else:
        print(text, end='')


if __name__ == '__main__':
    _main()
