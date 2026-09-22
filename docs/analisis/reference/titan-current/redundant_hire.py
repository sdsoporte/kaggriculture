# SPDX-License-Identifier: Apache-2.0
"""Cheap, conditional redundant-worker proposal over an explicit incumbent route.

This is a selected-action transform, not a producer or a future-price model.
It proves a narrow remaining-shift physical equivalence: removed trailing new
hands only move/pass or duplicate watering, and a retained hand supplies every
needed watering before reset without an intervening crop operation. Immediate
wages are exact. Future economic/controller/opponent responses still require
full-agent evaluation; the report deliberately makes no terminal-profit claim.
"""
from __future__ import annotations

import copy
import math
from collections import Counter
from typing import Any, Mapping, Sequence

NO_ORDER = ["SELL", "WHEAT", 0]
MOVES = {"NORTH": (0, -1), "SOUTH": (0, 1), "WEST": (-1, 0), "EAST": (1, 0)}


def _uint(value: Any, name: str, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}")
    return value


def _units(action: Mapping[str, Any]) -> list:
    return [action.get("farmer", ["PASS"]), *action.get("hands", [])]


def propose_redundant_hires(
    mechanics: Any, observation: Mapping[str, Any], configuration: Mapping[str, Any] | None,
    selected_action: Mapping[str, Any], *, route: Sequence[Mapping[str, Any]],
    route_id: str, route_switch_steps: Sequence[int], max_route_steps: int = 48,
) -> tuple[dict, dict]:
    """Return an optional hire proposal and exact limits; never call a policy.

    The caller supplies the CURRENT complete route after the producer's single
    action call, and all potential route-switch steps, not just triggered ones.
    This contract is for the Arlene-style source whose worker program is the
    supplied tape plus its weed/no-op repair. A different worker controller is
    not made compatible by providing its old tape.
    """
    out = copy.deepcopy(dict(selected_action))
    report = {"changed": False, "reason": "no_hire", "route_id": route_id,
              "removed_order_indices": [], "immediate_wage_saving": 0,
              "terminal_gain": None,
              "scope": "conditional remaining-route physical equivalence; not future cash or win proof"}
    cfg = dict(configuration or {})
    board = _uint(cfg.get("boardSize", 10), "boardSize", 1)
    day_len = _uint(cfg.get("turnsPerDay", 24), "turnsPerDay", 1)
    episode = _uint(cfg.get("episodeSteps", 720), "episodeSteps", 2)
    cap = _uint(cfg.get("shedCapacity", 100), "shedCapacity", 1)
    limit = max(1, _uint(cfg.get("maxMarketOrdersPerTurn", 10), "maxMarketOrdersPerTurn"))
    mult = _uint(cfg.get("farmHandCostMult", 1), "farmHandCostMult")
    _uint(max_route_steps, "max_route_steps", 1)
    step = observation.get("step")
    if step is None:
        step = _uint(observation["day"], "day") * day_len + _uint(observation["hour"], "hour")
    step = _uint(step, "step")
    end = min((step // day_len + 1) * day_len - 1, episode - 2)
    report.update(step=step, end_step=end)
    if step > end or end - step > max_route_steps:
        report["reason"] = "outside_bounded_shift"; return out, report
    queue = out.get("market", [])
    if not isinstance(queue, list):
        report["reason"] = "unsupported_queue"; return out, report
    hires = []
    for j, order in enumerate(queue[:limit]):
        if isinstance(order, list) and order and order[0] == "HIRE":
            hires.append(j)
        elif (isinstance(order, list) and len(order) == 3 and order[0] == "SELL"
              and order[1] in mechanics.PRODUCTS and order[2] == 0):
            continue
        else:
            report["reason"] = "active_or_unknown_nonhire_order"; return out, report
    if not hires or mult == 0:
        report["reason"] = "no_positive_cost_hire"; return out, report
    if not route_id or len(route) <= end:
        report["reason"] = "incomplete_route"; return out, report
    if any(step < _uint(t, "route switch") <= end for t in route_switch_steps):
        report["reason"] = "possible_route_switch"; return out, report
    for t in range(step + 1, end + 1):
        if any(isinstance(o, list) and o and o[0] == "HIRE"
               for o in route[t].get("market", [])[:limit]):
            report["reason"] = "later_hire_changes_cost_or_spawn"; return out, report

    me = _uint(observation["player"], "player")
    farm = copy.deepcopy(observation["farms"][me])
    private = copy.deepcopy(observation["private"])
    if len(farm["tiles"]) != board or any(len(row) != board for row in farm["tiles"]):
        report["reason"] = "board_shape"; return out, report
    current = _units(out)
    demand = Counter(a[1] for a in current if isinstance(a, list) and len(a) > 1 and a[0] == "PLANT")
    blocked = {p for p, n in demand.items() if n > private.get("seeds", {}).get(p, 0)}
    for i, action in enumerate(current):
        if isinstance(action, list) and len(action) > 1 and action[0] == "PLANT" and action[1] in blocked:
            action = ["PASS"]
        mechanics._apply_unit_action(farm, private, i, action, board, step // day_len, day_len, cap)
    existing = len(farm["hands"])
    costs = [mechanics._hire_cost(farm["hires_today"] + j, mult) for j in range(len(hires))]
    cash = farm["money"]
    if isinstance(cash, bool) or not isinstance(cash, (float, int)) or not math.isfinite(cash) or cash < sum(costs):
        report["reason"] = "current_hires_not_all_funded"; return out, report
    for _ in hires:
        mechanics._do_hire(farm, private, board, mult)
    positions = [list(farm["farmer"]), *copy.deepcopy(farm["hands"])]
    events = []
    for t in range(step + 1, end + 1):
        unit_program = _units(route[t])
        for i, pos in enumerate(positions):
            # Missing hand slots are genuinely absent, so the parent weed
            # repair does not process them. Explicit PASS slots do get repaired.
            explicit = i < len(unit_program)
            a = unit_program[i] if explicit else ["PASS"]
            if not isinstance(a, list) or not a or not isinstance(a[0], str):
                report["reason"] = "unsupported_unit_program"; return out, report
            op = a[0]; x, y = pos
            events.append((t, i, op, x, y, explicit))
            if op in MOVES:
                dx, dy = MOVES[op]; nx, ny = x + dx, y + dy
                if 0 <= nx < board and 0 <= ny < board:
                    pos[:] = [nx, ny]

    best = 0; best_witnesses = []; checks = 0
    for remove_count in range(1, len(hires) + 1):
        first_removed = 1 + existing + len(hires) - remove_count
        removed = [e for e in events if e[1] >= first_removed]
        valid = True; witnesses = []
        for t, i, op, x, y, explicit in removed:
            checks += 1
            if op not in (*MOVES, "WATER", "PASS"):
                valid = False; break
            tile = farm["tiles"][y][x]
            # Midday decay may create a weed and turn a nominal PASS/invalid
            # move/WATER into the parent's useful DIG. Do not suppress it.
            if explicit and isinstance(tile, dict):
                if tile.get("kind") == "WEED":
                    valid = False; break
                expiry = tile.get("max_lifespan_step", -1)
                if tile.get("kind") == "PLANT" and expiry >= 0 and expiry <= end:
                    valid = False; break
            if op != "WATER":
                continue
            # A narrow sufficient test; do not guess whether a nonplant tile
            # will be planted/unlocked or whether a later harvest is harmless.
            if not isinstance(tile, dict) or tile.get("kind") != "PLANT":
                valid = False; break
            touching = [e for e in events if e[3:5] == (x, y)]
            if any(e[2] not in (*MOVES, "WATER", "PASS") for e in touching):
                valid = False; break
            kept = [e for e in touching if e[1] < first_removed and e[2] == "WATER"]
            if not tile.get("watered_today") and not kept:
                valid = False; break
            witnesses.append({"worker": i, "step": t, "position": [x, y],
                              "already_watered": bool(tile.get("watered_today")),
                              "retained_watering": [[e[0], e[1]] for e in kept]})
        if not valid:
            break
        best = remove_count; best_witnesses = witnesses
    report["route_events_checked"] = checks
    if not best:
        report["reason"] = "no_redundant_trailing_worker"; return out, report
    omitted = hires[-best:]
    for j in omitted:
        out["market"][j] = list(NO_ORDER)
    report.update(changed=True, reason="redundant_watering_or_empty_tail",
                  removed_order_indices=omitted, removed_workers=best,
                  immediate_wage_saving=sum(costs[-best:]),
                  watering_witnesses=best_witnesses,
                  future_cash_compatibility="not established by the physical certificate")
    return out, report
