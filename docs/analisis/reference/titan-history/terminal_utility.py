# SPDX-License-Identifier: Apache-2.0
"""Exact terminal win-points tables from complete paired cash receipts.

No engine, controller, network, scenario probabilities, or continuation model is
constructed here. Callers supply the same complete own plan against each whole
rival scenario. Partial/nonterminal records remain cash-margin diagnostics.
"""
from __future__ import annotations

import argparse
import copy
from fractions import Fraction
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from typing import Any, Callable, Mapping, Sequence

SCHEMA = "titan.terminal-utility.v1"


def _number(value: Any) -> Fraction:
    if isinstance(value, bool) or not isinstance(value, (int, float, str, Fraction)):
        raise ValueError("Cash values must be finite numbers or exact numeric strings.")
    try:
        return Fraction(str(value)) if isinstance(value, float) else Fraction(value)
    except (ValueError, ZeroDivisionError, OverflowError):
        raise ValueError("Cash values must be finite numbers or exact numeric strings.") from None


def _ids(values: Any, name: str) -> list[str]:
    if not isinstance(values, (list, tuple)) or not values:
        raise ValueError(name + " must be a nonempty ordered list.")
    if any(not isinstance(item, str) or not item for item in values):
        raise ValueError(name + " entries must be nonempty strings.")
    if len(set(values)) != len(values):
        raise ValueError(name + " contains duplicate entries.")
    return list(values)


def win_points(own_cash: Any, rival_cash: Any) -> Fraction:
    """Return exact 1/0/1/2 points for a win/loss/tie at a completed endpoint."""
    difference = _number(own_cash) - _number(rival_cash)
    return Fraction(1 if difference > 0 else 0) if difference else Fraction(1, 2)


def _binding(values: dict[str, str], key: str, value: Any, label: str) -> None:
    if value is None:
        return
    if not isinstance(value, str) or not value:
        raise ValueError(label + " must be a nonempty string when supplied.")
    if key in values and values[key] != value:
        raise ValueError(label + " changes across the paired table.")
    values[key] = value


def build_table(document: Mapping[str, Any]) -> dict[str, Any]:
    """Project a rectangular receipt collection without inventing missing cells.

    Minimal document: plan_ids, scenario_ids, baseline, receipts. Each receipt
    has plan, scenario, own_cash, rival_cash and an explicit boolean done.
    Optional plan_sha256/scenario_sha256/public_state_sha256 bindings are
    compared when supplied, not interpreted as a proof of simulator fidelity.
    Extra fields and source provenance are retained as detached input data.
    """
    if not isinstance(document, Mapping):
        raise ValueError("The receipt document must be an object.")
    plans = _ids(document.get("plan_ids"), "plan_ids")
    scenarios = _ids(document.get("scenario_ids"), "scenario_ids")
    baseline = document.get("baseline")
    if baseline not in plans:
        raise ValueError("baseline must name one of plan_ids.")
    # The established T15 solver expects the baseline as the first row.
    plans = [baseline] + [p for p in plans if p != baseline]
    raw_receipts = document.get("receipts")
    if not isinstance(raw_receipts, list):
        raise ValueError("receipts must be a list.")
    cells: dict[tuple[str, str], dict[str, Any]] = {}
    plan_bindings: dict[str, str] = {}
    scenario_bindings: dict[str, str] = {}
    public_binding: dict[str, str] = {}
    binding_omissions: list[dict[str, str]] = []
    for raw in raw_receipts:
        if not isinstance(raw, dict):
            raise ValueError("Each receipt must be an object.")
        plan, scenario = raw.get("plan"), raw.get("scenario")
        if plan not in plans or scenario not in scenarios:
            raise ValueError("Receipt labels must occur in the ordered table labels.")
        key = (plan, scenario)
        if key in cells:
            raise ValueError("A plan/scenario pair has more than one receipt.")
        if "done" in raw and not isinstance(raw["done"], bool):
            raise ValueError("Receipt done must be an explicit boolean.")
        receipt = copy.deepcopy(raw)
        for field in ("own_cash", "rival_cash"):
            if receipt.get(field) is not None:
                receipt[field] = str(_number(receipt[field]))
        _binding(plan_bindings, plan, raw.get("plan_sha256"), "plan_sha256")
        _binding(scenario_bindings, scenario, raw.get("scenario_sha256"), "scenario_sha256")
        _binding(public_binding, "decision", raw.get("public_state_sha256"), "public_state_sha256")
        for field in ("plan_sha256", "scenario_sha256", "public_state_sha256"):
            if raw.get(field) is None:
                binding_omissions.append({"plan": plan, "scenario": scenario, "field": field})
        cells[key] = receipt

    matrix = [[cells.get((p, s)) for s in scenarios] for p in plans]
    incomplete: list[dict[str, str]] = []
    nonterminal: list[dict[str, str]] = []
    margins: list[list[Fraction | None]] = []
    for plan, row in zip(plans, matrix):
        margin_row = []
        for scenario, receipt in zip(scenarios, row):
            if receipt is None or any(receipt.get(f) is None for f in ("own_cash", "rival_cash")):
                incomplete.append({"plan": plan, "scenario": scenario})
                margin_row.append(None)
            else:
                margin_row.append(_number(receipt["own_cash"]) - _number(receipt["rival_cash"]))
            if receipt is None or receipt.get("done") is not True:
                nonterminal.append({"plan": plan, "scenario": scenario})
        margins.append(margin_row)
    terminal = not incomplete and not nonterminal
    cash_deltas = [[None if x is None or b is None else str(x-b)
                    for x, b in zip(row, margins[0])] for row in margins]
    points = [[win_points(r["own_cash"], r["rival_cash"]) for r in row]
              for row in matrix] if terminal else None
    utility_deltas = [[str(2 * (u-b)) for u, b in zip(row, points[0])]
                      for row in points] if terminal else None
    return {
        "schema": SCHEMA,
        "objective": "centered_terminal_win_points" if terminal else "cash_margin_proxy",
        "terminal": terminal,
        "solver_ready": terminal,
        "baseline": baseline,
        "plan_ids": plans,
        "scenario_ids": scenarios,
        "cash_margins": [[None if x is None else str(x) for x in row] for row in margins],
        "cash_margin_deltas": cash_deltas,
        "win_points": [[str(x) for x in row] for row in points] if terminal else None,
        "utility_deltas": utility_deltas,
        "absolute_centered_utilities": [[str(2*x-1) for x in row] for row in points] if terminal else None,
        "baseline_utility_constant": len(set(points[0])) == 1 if terminal else None,
        "incomplete_cells": incomplete,
        "nonterminal_cells": nonterminal,
        "bindings": {"plan_sha256": plan_bindings, "scenario_sha256": scenario_bindings,
                     "public_state_sha256": public_binding.get("decision"),
                     "omissions": binding_omissions},
        "receipts": matrix,
        "source": copy.deepcopy(document.get("source")),
        "scenario_probabilities": None,
        "scope": "Exact arithmetic on supplied paired receipts; not a calibrated win probability or policy promotion.",
    }


def solve_terminal(table: Mapping[str, Any], solver: Callable[[Sequence[Sequence[str]]], Mapping[str, Any]],
                   *, objective: str = "absolute") -> dict[str, Any]:
    """Consume an existing finite-table solver; never call it on a margin proxy.

    The default absolute objective uses T15 only when the baseline utility is
    constant across scenarios. Explicit baseline_relative mode has a different
    objective and is labeled accordingly.

    Returns the solver's weights and expected *terminal win points* for each
    supplied scenario. Expectation is over our complete-plan mixture, not an
    invented rival distribution. No action sampling or live policy is changed.
    """
    if objective not in ("absolute", "baseline_relative"):
        raise ValueError("objective must be absolute or baseline_relative.")
    if table.get("schema") != SCHEMA:
        raise ValueError("Expected a terminal-utility table.")
    if not table.get("solver_ready"):
        return {"status": "terminal_receipts_incomplete", "solver_called": False,
                "objective": table.get("objective"), "weights": None,
                "win_probability": None}
    # Subtracting a different baseline utility from each column changes an
    # absolute maximin problem. The existing zero-baseline T15 solver is an
    # equivalent absolute-utility solver only for a constant baseline row.
    if objective == "absolute" and not table["baseline_utility_constant"]:
        return {"status": "absolute_matrix_solver_needed", "solver_called": False,
                "objective": "absolute_terminal_win_points", "weights": None,
                "win_probability": None,
                "reason": "Scenario-dependent baseline subtraction changes absolute maximin utility.",
                "absolute_centered_utilities": copy.deepcopy(table["absolute_centered_utilities"])}
    raw = copy.deepcopy(dict(solver(copy.deepcopy(table["utility_deltas"]))))
    weights = [_number(w) for w in raw.get("weights", [])]
    if len(weights) != len(table["plan_ids"]) or any(w < 0 for w in weights) or sum(weights) != 1:
        raise ValueError("Solver weights must form a simplex over the complete plans.")
    deltas = [[_number(x) for x in row] for row in table["utility_deltas"]]
    columns = [sum(weights[i] * deltas[i][j] for i in range(len(weights)))
               for j in range(len(table["scenario_ids"]))]
    if _number(raw["value"]) != min(columns):
        raise ValueError("Solver value and weighted terminal table disagree.")
    points = [[_number(x) for x in row] for row in table["win_points"]]
    expected = [sum(weights[i] * points[i][j] for i in range(len(weights)))
                for j in range(len(table["scenario_ids"]))]
    return {"status": "solved", "solver_called": True,
            "objective": objective + "_terminal_win_points",
            "plan_ids": list(table["plan_ids"]), "scenario_ids": list(table["scenario_ids"]),
            "weights": [str(w) for w in weights],
            "worst_expected_win_point_change": str(min(columns) / 2),
            "worst_expected_win_points": str(min(expected)),
            "expected_win_points_by_scenario": [str(x) for x in expected],
            "centered_utility_change_by_scenario": [str(x) for x in columns],
            "win_probability": None, "scenario_probabilities": None,
            "raw_solver": raw,
            "scope": "Included-scenario " + objective + " maximin expectation over complete own plans; not per-outcome protection or a full-game equilibrium."}


def _load_solver(path: Path):
    spec = importlib.util.spec_from_file_location("terminal_utility_external_solver", path)
    if spec is None or spec.loader is None:
        raise ValueError("Cannot load the supplied local solver module.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.solve_table


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--objective", choices=("absolute", "baseline_relative"), default="absolute")
    parser.add_argument("--solver-file", type=Path, help="Existing local T15 solver.py; no download is performed.")
    args = parser.parse_args(argv)
    try:
        source_bytes = args.input.read_bytes()
        result = build_table(json.loads(source_bytes))
        result["input_sha256"] = hashlib.sha256(source_bytes).hexdigest()
        if args.solver_file is not None:
            result["solution"] = solve_terminal(result, _load_solver(args.solver_file), objective=args.objective)
            result["solver_sha256"] = hashlib.sha256(args.solver_file.read_bytes()).hexdigest()
        text = json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
        if args.output is None:
            sys.stdout.write(text)
        else:
            args.output.write_text(text, encoding="utf-8")
        return 0
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        print(json.dumps({"error": type(exc).__name__, "message": str(exc)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
