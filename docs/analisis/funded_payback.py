# SPDX-License-Identifier: Apache-2.0
"""Prospective cash admission for a complete paid-land production route.

The caller owns route construction and physical path search.  This module
evaluates two already-authored routes from the current public/own observation.
It never reads a replay, environment seed, future shop draw, opponent private
state, or a future observation.

Future weeds and shop unlocks are intentionally omitted.  A fourth-quadrant
proposal is therefore admissible only when it plants and waters every tile in
the supplied finite work bundle before the first end-of-day boundary after the land purchase.  The
known shops and town-center demand are retained, and every supplied rival flow
is an explicit conditional stress shared by the incumbent and candidate.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
import math
import time
from typing import Any, Mapping, Sequence


class BudgetExceeded(RuntimeError):
    """No incomplete result may admit a route."""


@dataclass(frozen=True)
class MarketScenario:
    name: str
    rival_orders: Mapping[int, Sequence[Sequence[Any]]] = field(default_factory=dict)
    description: str = "Declared conditional flow; no hidden rival state"


def _integer(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")
    return value


def _number(value: Any, name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} cannot be a boolean")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    return result


def _units(action: Mapping[str, Any], count: int) -> list[list[Any]]:
    farmer = action.get("farmer", ["PASS"])
    hands = action.get("hands", [])
    if not isinstance(farmer, list) or not isinstance(hands, list):
        raise ValueError("unit actions must use the official list schema")
    rows = [farmer, *hands]
    return [list(rows[i]) if i < len(rows) and isinstance(rows[i], list) and rows[i]
            else ["PASS"] for i in range(count)]


def _queue(action: Mapping[str, Any], slots: int) -> list[list[Any]]:
    value = action.get("market", [])
    if not isinstance(value, list):
        raise ValueError("market queue must be a list")
    result = []
    for order in value[:slots]:
        if not isinstance(order, list) or not order:
            raise ValueError("market orders must be nonempty lists")
        result.append(list(order))
    return result


def _target_positions(quadrant: str, board: int, mechanics: Any) -> set[tuple[int, int]]:
    return {(x, y) for y in range(board) for x in range(board)
            if mechanics._quadrant_of(x, y, board) == quadrant}


def _deposit_provenance(private: Mapping[str, Any], before_inventories: Sequence[Mapping[str, int]],
                        after_shed: Mapping[str, int], target_inventory: list[dict[str, int]],
                        target_shed: dict[str, int], *, explicit: bool,
                        events: list[dict[str, Any]], step: int,
                        capacity: int, worker_offset: int = 0) -> None:
    """Attribute capacity to non-target stock first; never overclaim target arrival."""
    before_shed = private["shed"]
    shadow = dict(before_shed)
    for worker, inventory in enumerate(before_inventories):
        tags = target_inventory[worker] if worker < len(target_inventory) else {}
        for item, quantity in inventory.items():
            room = max(0, capacity - sum(shadow.values()))
            accepted_total = min(max(0, int(quantity)), room)
            if accepted_total:
                shadow[item] = shadow.get(item, 0) + accepted_total
            tagged = max(0, int(tags.get(item, 0)))
            if tagged <= 0 or accepted_total <= 0:
                continue
            non_target = max(0, int(quantity) - tagged)
            accepted_target = min(tagged, max(0, accepted_total - non_target))
            if accepted_target:
                target_shed[item] = target_shed.get(item, 0) + accepted_target
                events.append({"step": step, "kind": "drop" if explicit else "eod_drop",
                               "worker": worker + worker_offset,
                               "item": item, "quantity": accepted_target})
        if worker < len(target_inventory):
            target_inventory[worker] = {}
    if any(int(shadow.get(item, 0)) != int(after_shed.get(item, 0))
           for item in set(shadow) | set(after_shed)):
        raise ValueError("shed arrival projection disagrees with official unit mechanics")


def _parse_rival(order: Sequence[Any], mechanics: Any):
    parsed = mechanics._parse_order(list(order))
    if parsed is None:
        if list(order) == ["PASS"]:
            return None
        raise ValueError("invalid rival market order")
    if parsed["type"] not in ("SELL", "BUY_PRODUCT"):
        raise ValueError("rival scenario supports SELL/BUY_PRODUCT/PASS only")
    return parsed


def _process_market(action: Mapping[str, Any], rival_queue: Sequence[Sequence[Any]],
                    farm: dict[str, Any], private: dict[str, Any], market: dict[str, Any],
                    configuration: Mapping[str, Any], mechanics: Any,
                    target_shed: dict[str, int], sold_target: dict[str, int],
                    fills: dict[tuple[int, int], int], spend: dict[str, float],
                    receipts: dict[str, float], step: int) -> None:
    slots = max(1, _integer(configuration.get("maxMarketOrdersPerTurn", 10), "max orders"))
    shed_capacity = _integer(configuration.get("shedCapacity", 100), "shed capacity")
    board = _integer(configuration.get("boardSize", 10), "board size")
    hire_mult = _number(configuration.get("farmHandCostMult", 1), "hire multiplier")
    ours = _queue(action, slots)
    if len(rival_queue) > slots:
        raise ValueError("rival queue exceeds market slot limit")
    rivals = [_parse_rival(order, mechanics) for order in rival_queue]
    for slot in range(max(len(ours), len(rivals))):
        own_order = ours[slot] if slot < len(ours) else ["PASS"]
        own = mechanics._parse_order(own_order)
        rival = deepcopy(rivals[slot]) if slot < len(rivals) else None
        key = (step, slot)
        if own is None and own_order != ["PASS"]:
            raise ValueError("invalid own market order")
        if own is not None and own["type"] in ("HIRE", "BUY_LAND"):
            before_cash, before_hands = farm["money"], len(farm["hands"])
            before_land = len(farm["unlocked_quadrants"])
            if own["type"] == "HIRE":
                mechanics._do_hire(farm, private, board, hire_mult)
                fills[key] = int(len(farm["hands"]) > before_hands)
            else:
                mechanics._do_buy_land(farm, board)
                fills[key] = int(len(farm["unlocked_quadrants"]) > before_land)
            spend[own["type"]] = spend.get(own["type"], 0.0) + before_cash - farm["money"]
            own = None
        rounds = max(own.get("remaining", 0) if own else 0,
                     rival.get("remaining", 0) if rival else 0)
        for _ in range(rounds):
            quoted = []
            for actor, parsed in ((0, own), (1, rival)):
                if parsed is None or parsed.get("remaining", 0) <= 0:
                    continue
                op, item = parsed["type"], parsed["item"]
                if op == "SELL" and item in mechanics.PRODUCTS:
                    price = mechanics.market_price(item, market["inventory"][item], market.get("params"))
                elif op == "BUY_PRODUCT" and item in ("WHEAT", "FERTILIZER"):
                    price = mechanics.market_price(item, market["inventory"][item] - 1,
                                                   market.get("params"))
                elif actor == 0 and op == "BUY_SEED" and item in mechanics.CROPS:
                    price = mechanics.CROPS[item]["seed"]
                elif actor == 0 and op == "BUY_ANIMAL" and item in mechanics.ANIMALS:
                    price = mechanics.ANIMALS[item]["cost"]
                else:
                    if actor == 0:
                        own = None
                    else:
                        rival = None
                    continue
                quoted.append((actor, parsed, op, item, price))
            committed = False
            for actor, parsed, op, item, price in quoted:
                if actor == 0:
                    before_cash = farm["money"]
                    before_shed = int(private["shed"].get(item, 0))
                    tagged = int(target_shed.get(item, 0))
                    ok = mechanics._commit_unit(op, item, price, farm, private, market, shed_capacity)
                    if not ok:
                        own = None
                        continue
                    fills[key] = fills.get(key, 0) + 1
                    if op == "SELL":
                        receipts[item] = receipts.get(item, 0.0) + farm["money"] - before_cash
                        non_target = max(0, before_shed - tagged)
                        if non_target == 0 and tagged > 0:
                            target_shed[item] = tagged - 1
                            sold_target[item] = sold_target.get(item, 0) + 1
                    else:
                        spend[op] = spend.get(op, 0.0) + before_cash - farm["money"]
                    parsed["remaining"] -= 1
                else:
                    # Scenario quantities are conditional fills. Rival cash and
                    # private stock are deliberately not invented.
                    if op == "SELL":
                        if price > 1:
                            market["inventory"][item] += 1
                    else:
                        market["inventory"][item] -= 1
                    parsed["remaining"] -= 1
                committed = True
            if not committed:
                break
        mechanics._refresh_prices(market)


def _known_town_demand(observation: Mapping[str, Any], configuration: Mapping[str, Any],
                       mechanics: Any, market: dict[str, Any], step: int) -> None:
    shop_interval = max(1, _integer(configuration.get("townShopSellInterval", 4), "shop interval"))
    center_interval = max(1, _integer(configuration.get("townCenterSellInterval", 24), "center interval"))
    shops = observation["town"].get("unlocked_shops", [])
    if step % shop_interval == 0:
        for shop in shops:
            products = mechanics.SHOPS[shop]
            for item in products:
                market["inventory"][item] -= 2 if len(products) == 1 else 1
    if step % center_interval == 0:
        for item in mechanics.TOWN_CENTER_PRODUCTS:
            market["inventory"][item] -= 1
    mechanics._refresh_prices(market)


def _simulate(route: Sequence[Mapping[str, Any]], observation: Mapping[str, Any],
              configuration: Mapping[str, Any], mechanics: Any, scenario: MarketScenario,
              target: set[tuple[int, int]], deadline: float | None,
              rejoin_step: int) -> dict[str, Any]:
    now = _integer(observation["step"], "step")
    end = _integer(configuration.get("episodeSteps", 720), "episodeSteps") - 2
    turns = max(1, _integer(configuration.get("turnsPerDay", 24), "turns per day"))
    board = _integer(configuration.get("boardSize", 10), "board size")
    capacity = _integer(configuration.get("shedCapacity", 100), "shed capacity")
    player = _integer(observation["player"], "player")
    farm = deepcopy(observation["farms"][player])
    private = deepcopy(observation["private"])
    market = deepcopy(observation["market"])
    target_inventory = [{} for _ in private["inventories"]]
    target_shed: dict[str, int] = {}
    sold_target: dict[str, int] = {}
    fills: dict[tuple[int, int], int] = {}
    spend: dict[str, float] = {}
    receipts: dict[str, float] = {}
    events: list[dict[str, Any]] = []
    cash_trace = []
    rejoin = None
    minimum_cash = float(farm["money"])
    land_step = None
    for step in range(now, end + 1):
        if deadline is not None and time.perf_counter() >= deadline:
            raise BudgetExceeded("prospective route budget exhausted")
        if step == rejoin_step:
            rejoin = deepcopy((farm, private, target_shed))
        action = route[step]
        count = 1 + len(farm["hands"])
        requests = _units(action, count)
        demand: dict[str, int] = {}
        for request in requests:
            if len(request) >= 2 and request[0] == "PLANT":
                demand[request[1]] = demand.get(request[1], 0) + 1
        blocked = {crop for crop, quantity in demand.items()
                   if quantity > private["seeds"].get(crop, 0)}
        for worker, request in enumerate(requests):
            if worker >= len(target_inventory):
                target_inventory.append({})
            actual = (["PASS"] if len(request) >= 2 and request[0] == "PLANT"
                      and request[1] in blocked else request)
            position = tuple(mechanics._farmer_position(farm, worker))
            on_target = position in target
            explicit_drop = bool(actual and actual[0] == "DROP")
            # Provenance needs snapshots only on target tiles or at an explicit
            # deposit. Avoid copying unrelated worker/tile/shed state on every
            # route step; official mechanics still receives the same objects.
            tile_before = (deepcopy(farm["tiles"][position[1]][position[0]])
                           if on_target else None)
            inventory_before = (deepcopy(private["inventories"][worker])
                                if on_target or explicit_drop else None)
            shed_before = deepcopy(private["shed"]) if explicit_drop else None
            mechanics._apply_unit_action(farm, private, worker, actual, board,
                                         step // turns, turns, capacity)
            if on_target:
                tile_after = farm["tiles"][position[1]][position[0]]
                op = actual[0] if actual else "PASS"
                if op == "PLANT" and tile_before is None and isinstance(tile_after, dict):
                    events.append({"step": step, "kind": "plant", "worker": worker,
                                   "tile": list(position), "crop": tile_after.get("crop")})
                elif op == "WATER" and isinstance(tile_after, dict) and tile_after.get("watered_today") \
                        and not (isinstance(tile_before, dict) and tile_before.get("watered_today")):
                    events.append({"step": step, "kind": "water", "worker": worker,
                                   "tile": list(position), "crop": tile_after.get("crop")})
                elif op == "HARVEST":
                    for item, quantity in private["inventories"][worker].items():
                        gained = quantity - inventory_before.get(item, 0)
                        if gained > 0:
                            target_inventory[worker][item] = target_inventory[worker].get(item, 0) + gained
                            events.append({"step": step, "kind": "harvest", "worker": worker,
                                           "tile": list(position), "item": item, "quantity": gained})
            if explicit_drop:
                _deposit_provenance({"shed": shed_before}, [inventory_before], private["shed"],
                                    [target_inventory[worker]], target_shed, explicit=True,
                                    events=events, step=step, capacity=capacity,
                                    worker_offset=worker)
                target_inventory[worker] = {}
        before_land = len(farm["unlocked_quadrants"])
        _process_market(action, scenario.rival_orders.get(step, ()), farm, private, market,
                        configuration, mechanics, target_shed, sold_target, fills,
                        spend, receipts, step)
        if len(farm["unlocked_quadrants"]) > before_land:
            land_step = step if land_step is None else land_step
        _known_town_demand(observation, configuration, mechanics, market, step)
        minimum_cash = min(minimum_cash, float(farm["money"]))
        cash_trace.append({"step": step, "cash": float(farm["money"])})
        mechanics._decay_plants(farm, step)
        if (step + 1) % turns == 0:
            mechanics._daily_refresh_plants(farm, step // turns, turns)
            mechanics._daily_refresh_animals(farm, step // turns)
            if any(target_inventory):
                before_inventories = deepcopy(private["inventories"])
                before_shed = deepcopy(private["shed"])
                mechanics._drop_inventories_to_shed(private, capacity)
                _deposit_provenance({"shed": before_shed}, before_inventories, private["shed"],
                                    target_inventory, target_shed, explicit=False,
                                    events=events, step=step, capacity=capacity)
            else:
                mechanics._drop_inventories_to_shed(private, capacity)
            farm["farmer"] = list(mechanics._default_spawn(board))
            farm["hands"] = []
            farm["hires_today"] = 0
            private["inventories"] = [{}]
            target_inventory = [{}]
    if rejoin is None:
        rejoin = deepcopy((farm, private, target_shed))
    return {"cash": float(farm["money"]), "minimum_cash": minimum_cash,
            "cash_trace": cash_trace, "farm": farm, "private": private,
            "market": market, "fills": fills, "spend": spend,
            "receipts": receipts, "events": events, "sold_target": sold_target,
            "target_shed": target_shed, "land_step": land_step, "rejoin": rejoin}


def default_scenarios(candidate_route: Sequence[Mapping[str, Any]], observation: Mapping[str, Any],
                      configuration: Mapping[str, Any]) -> tuple[MarketScenario, ...]:
    """Known demand plus same-slot matched supply; neither uses hidden inputs."""
    now = _integer(observation["step"], "step")
    end = _integer(configuration.get("episodeSteps", 720), "episodeSteps") - 2
    slots = max(1, _integer(configuration.get("maxMarketOrdersPerTurn", 10), "max orders"))
    pressure = {}
    for step in range(now, end + 1):
        queue = _queue(candidate_route[step], slots)
        if not any(order and order[0] == "SELL" for order in queue):
            continue
        pressure[step] = [list(order) if order and order[0] == "SELL" else ["PASS"]
                          for order in queue]
    return (MarketScenario("known_demand_no_new_rival_flow"),
            MarketScenario("same_slot_matched_sell_pressure", pressure,
                           "Rival conditionally supplies the candidate's scheduled sale quantities"))


def evaluate_bundle(observation: Mapping[str, Any], configuration: Mapping[str, Any],
                    mechanics: Any, base_route: Sequence[Mapping[str, Any]],
                    candidate_route: Sequence[Mapping[str, Any]], bundle: Mapping[str, Any], *,
                    scenarios: Sequence[MarketScenario] | None = None,
                    minimum_gain: float = 0.0, seconds: float | None = 0.20,
                    base_cache: dict[tuple[Any, ...], dict[str, Any]] | None = None) -> dict[str, Any]:
    """Admit only a funded, physically realized, route-rejoining land bundle.

    The returned report is a callback result, not a route mutation.  A caller
    commits the candidate route only when ``complete`` and ``admitted`` are true.
    """
    started = time.perf_counter()
    report: dict[str, Any] = {"complete": False, "admitted": False,
                              "reason": "invalid_input", "rows": []}
    try:
        now = _integer(observation["step"], "step")
        end = _integer(configuration.get("episodeSteps", 720), "episodeSteps") - 2
        if end < now or len(base_route) <= end or len(candidate_route) <= end:
            raise ValueError("routes must cover every remaining executable step")
        gain = _number(minimum_gain, "minimum gain")
        if gain < 0:
            raise ValueError("minimum gain must be nonnegative")
        duration = None if seconds is None else _number(seconds, "seconds")
        if duration is not None and duration < 0:
            raise ValueError("seconds must be nonnegative")
        deadline = None if duration is None else started + duration
        player = _integer(observation["player"], "player")
        farm = observation["farms"][player]
        board = _integer(configuration.get("boardSize", 10), "board size")
        expected_index = len(farm["unlocked_quadrants"]) - 1
        if expected_index >= len(mechanics.LAND_ORDER):
            report.update(complete=True, reason="all_land_already_unlocked")
            return report
        expected = mechanics.LAND_ORDER[expected_index]
        target_name = str(bundle.get("target_quadrant", ""))
        if target_name != expected or target_name in farm["unlocked_quadrants"]:
            raise ValueError("bundle target is not the next locked quadrant")
        rejoin_step = _integer(bundle.get("rejoin_step"), "rejoin step")
        if not now < rejoin_step <= end + 1:
            raise ValueError("rejoin step outside remaining route")
        if tuple(base_route[rejoin_step: end + 1]) != tuple(candidate_route[rejoin_step: end + 1]):
            report.update(complete=True, reason="route_does_not_rejoin")
            return report
        slots = max(1, _integer(configuration.get("maxMarketOrdersPerTurn", 10), "max orders"))
        extras = []
        for step in range(now, rejoin_step):
            base_queue = _queue(base_route[step], slots)
            candidate_queue = _queue(candidate_route[step], slots)
            if candidate_queue[:len(base_queue)] != base_queue:
                report.update(complete=True, reason="baseline_market_prefix_changed")
                return report
            extras.extend((step, slot, order) for slot, order in
                          enumerate(candidate_queue[len(base_queue):], start=len(base_queue)))
        land_orders = [row for row in extras if row[2][0] == "BUY_LAND"]
        if len(land_orders) != 1:
            report.update(complete=True, reason="needs_one_incremental_land_order")
            return report
        allowed_extra = {"BUY_LAND", "BUY_SEED", "BUY_PRODUCT", "BUY_ANIMAL", "HIRE", "SELL", "PASS"}
        if any(row[2][0] not in allowed_extra for row in extras):
            report.update(complete=True, reason="unsupported_incremental_order")
            return report
        quadrant = _target_positions(target_name, board, mechanics)
        raw_required = bundle.get("required_tiles")
        if raw_required is None:
            target = quadrant
        else:
            if not isinstance(raw_required, (list, tuple)) or not raw_required:
                raise ValueError("required tiles must be a nonempty list")
            target = set()
            for raw in raw_required:
                if not isinstance(raw, (list, tuple)) or len(raw) != 2:
                    raise ValueError("required tile must be an x,y pair")
                tile = (_integer(raw[0], "tile x"), _integer(raw[1], "tile y"))
                if tile not in quadrant or tile in target:
                    raise ValueError("required tiles must be distinct target-quadrant positions")
                target.add(tile)
        minimum_tiles = _integer(bundle.get("minimum_planted_tiles", len(target)), "minimum planted tiles")
        if not 1 <= minimum_tiles <= len(target):
            raise ValueError("minimum planted tiles outside target quadrant")
        selected_scenarios = tuple(scenarios or default_scenarios(candidate_route, observation,
                                                                  configuration))
        if not selected_scenarios or len({s.name for s in selected_scenarios}) != len(selected_scenarios):
            raise ValueError("scenarios must be nonempty and uniquely named")
        rows = []
        for scenario in selected_scenarios:
            if deadline is not None and time.perf_counter() >= deadline:
                raise BudgetExceeded("prospective bundle budget exhausted")
            cache_key = ((id(base_route), rejoin_step, scenario.name)
                         if base_cache is not None and not scenario.rival_orders else None)
            base = base_cache.get(cache_key) if cache_key is not None else None
            if base is None:
                base = _simulate(base_route, observation, configuration, mechanics, scenario,
                                 target, deadline, rejoin_step)
                if cache_key is not None:
                    base_cache[cache_key] = base
            candidate = _simulate(candidate_route, observation, configuration, mechanics, scenario,
                                  target, deadline, rejoin_step)
            rows.append({"name": scenario.name, "base": base, "candidate": candidate,
                         "gain": candidate["cash"] - base["cash"]})
            # Admission requires every declared scenario to clear the minimum.
            # Once one complete scenario fails, later stress rows cannot change
            # the rejection and only consume the live action budget.
            if rows[-1]["gain"] <= gain:
                break
        compact_rows = [{"name": row["name"], "base_cash": row["base"]["cash"],
                         "candidate_cash": row["candidate"]["cash"], "gain": row["gain"],
                         "candidate_minimum_cash": row["candidate"]["minimum_cash"],
                         "candidate_spend": row["candidate"]["spend"],
                         "candidate_receipts": row["candidate"]["receipts"]}
                        for row in rows]
        worst = min(row["gain"] for row in rows)
        report.update(rows=compact_rows, worst_gain=worst)
        plants = [e for e in rows[0]["candidate"]["events"] if e["kind"] == "plant"]
        waters = [e for e in rows[0]["candidate"]["events"] if e["kind"] == "water"]
        harvests = [e for e in rows[0]["candidate"]["events"] if e["kind"] == "harvest"]
        drops = [e for e in rows[0]["candidate"]["events"] if e["kind"] == "drop"]
        plant_tiles = {tuple(e["tile"]) for e in plants}
        land_step = rows[0]["candidate"]["land_step"]
        if land_step is None:
            report.update(complete=True, reason="land_purchase_not_funded")
            return report
        extra_keys = {(step, slot): order for step, slot, order in extras}
        for key, order in extra_keys.items():
            requested = 1 if order[0] in ("BUY_LAND", "HIRE") else (int(order[2]) if len(order) > 2 else 0)
            if order[0] not in ("SELL", "PASS") and any(row["candidate"]["fills"].get(key, 0) < requested
                                                         for row in rows):
                report.update(complete=True, reason="incremental_purchase_not_funded")
                return report
        for step in range(now, rejoin_step):
            for slot, order in enumerate(_queue(base_route[step], slots)):
                key = (step, slot)
                if any(row["candidate"]["fills"].get(key, 0) < row["base"]["fills"].get(key, 0)
                       for row in rows):
                    report.update(complete=True, reason="existing_obligation_displaced")
                    return report
        if len(plant_tiles) < minimum_tiles or (raw_required is not None and not target <= plant_tiles):
            report.update(complete=True, reason="target_not_productively_saturated")
            return report
        turns = _integer(configuration.get("turnsPerDay", 24), "turns per day")
        land_day = land_step // turns
        first_plant_by_tile = {}
        for event in plants:
            first_plant_by_tile.setdefault(tuple(event["tile"]), event["step"])
        water_by_tile = {tuple(e["tile"]) for e in waters if e["step"] // turns == land_day}
        # Later cycles may DIG/replant the same paid tile.  Only its first
        # productive commitment belongs to the land-purchase-day certificate.
        if (any(step // turns != land_day for step in first_plant_by_tile.values())
                or not plant_tiles <= water_by_tile):
            report.update(complete=True, reason="target_not_planted_and_watered_before_day_close")
            return report
        harvested = sum(e["quantity"] for e in harvests)
        explicit_drop = sum(e["quantity"] for e in drops)
        sold = sum(rows[0]["candidate"]["sold_target"].values())
        if harvested <= 0:
            report.update(complete=True, reason="no_mature_target_harvest")
            return report
        if explicit_drop < harvested:
            report.update(complete=True, reason="target_harvest_not_explicitly_dropped")
            return report
        if sold < harvested or any(sum(row["candidate"]["sold_target"].values()) < harvested for row in rows):
            report.update(complete=True, reason="target_output_not_realized_in_sale")
            return report
        base_rejoin, candidate_rejoin = rows[0]["base"]["rejoin"], rows[0]["candidate"]["rejoin"]
        if (base_rejoin[0]["farmer"] != candidate_rejoin[0]["farmer"]
                or base_rejoin[0]["hands"] != candidate_rejoin[0]["hands"]
                or base_rejoin[0]["hires_today"] != candidate_rejoin[0]["hires_today"]):
            report.update(complete=True, reason="worker_state_does_not_rejoin")
            return report
        if base_rejoin[1] != candidate_rejoin[1]:
            report.update(complete=True, reason="private_inventory_does_not_rejoin")
            return report
        existing = {(x, y) for y in range(board) for x in range(board)
                    if mechanics._quadrant_of(x, y, board) in farm["unlocked_quadrants"]}
        if any(base_rejoin[0]["tiles"][y][x] != candidate_rejoin[0]["tiles"][y][x]
               for x, y in existing):
            report.update(complete=True, reason="existing_farm_obligation_changed")
            return report
        if any(row["candidate"]["minimum_cash"] < 0 for row in rows):
            report.update(complete=True, reason="candidate_cash_trough_negative")
            return report
        if worst <= gain:
            report.update(complete=True, reason="no_strict_funded_payback")
            return report
        payback_step = None
        for index, item in enumerate(rows[0]["candidate"]["cash_trace"]):
            if item["cash"] - rows[0]["base"]["cash_trace"][index]["cash"] > gain:
                payback_step = item["step"]
                break
        changed_unit_slots = 0
        for step in range(now, rejoin_step):
            count = max(1 + len(farm.get("hands", [])),
                        len(base_route[step].get("hands", [])) + 1,
                        len(candidate_route[step].get("hands", [])) + 1)
            changed_unit_slots += sum(a != b for a, b in zip(_units(base_route[step], count),
                                                              _units(candidate_route[step], count)))
        report.update(complete=True, admitted=True, reason="strict_realized_payback",
                      route_id=str(bundle.get("route_id", "candidate")),
                      base_route_id=str(bundle.get("base_route_id", "base")),
                      target_quadrant=target_name, land_step=land_step,
                      rejoin_step=rejoin_step, minimum_gain=gain, worst_gain=worst,
                      payback_step=payback_step, planted_tiles=len(plant_tiles),
                      harvested_units=harvested, explicitly_dropped_units=explicit_drop,
                      realized_target_units=sold, changed_unit_slots=changed_unit_slots,
                      rows=compact_rows, elapsed_seconds=time.perf_counter() - started,
                      assumptions="known current shops only; declared rival flows; no future weeds/shop draws")
        return report
    except BudgetExceeded:
        report.update(reason="incomplete_budget", rows=[])
        return report
    except (KeyError, ValueError, TypeError, IndexError, OverflowError) as exc:
        report.update(reason="invalid_input", detail=f"{type(exc).__name__}: {exc}", rows=[])
        return report


def _proposal_rank(mechanics: Any, observation: Mapping[str, Any], proposal: Mapping[str, Any]) -> float:
    """Current-public-price ordering only; never an admission estimate."""
    crop = str(proposal.get("crop", ""))
    if crop not in mechanics.CROPS:
        return float("-inf")
    inventory = observation["market"]["inventory"][crop]
    price = mechanics.market_price(crop, inventory, observation["market"].get("params"))
    units = 0
    for variant in proposal.get("variants", {}).values():
        units = max(units, sum(max(0, int(row.get("units", 0)))
                               for row in variant.get("receipts", ())))
    return units * price - float(proposal.get("cost", float("inf")))


class FundedPaybackAdmission:
    """Adapter from WIDEFIELD proposals to the exact prospective evaluator.

    Every compatible authored route variant must pass.  Proposal metadata is
    used only to materialize its sparse route patch and order the bounded scan;
    official mechanics derive all funding, yield, capacity and sale receipts.
    """

    def __init__(self, *, minimum_gain: float = 0.0, seconds: float = 0.35,
                 max_proposals: int = 3):
        self.minimum_gain = _number(minimum_gain, "minimum gain")
        self.seconds = _number(seconds, "seconds")
        self.max_proposals = _integer(max_proposals, "max proposals")
        if self.minimum_gain < 0 or self.seconds < 0 or self.max_proposals < 1:
            raise ValueError("invalid funded-payback admission configuration")
        self.last_report: dict[str, Any] = {}

    @staticmethod
    def _candidate(base: Sequence[Mapping[str, Any]], variant: Mapping[str, Any],
                   now: int) -> tuple[list[Mapping[str, Any]], int]:
        patches = variant.get("patches")
        if not isinstance(patches, Mapping) or not patches:
            raise ValueError("proposal variant needs a nonempty patches mapping")
        candidate = list(base)
        changed = []
        for raw_step, row in patches.items():
            step = _integer(raw_step, "patch step")
            if not now <= step < len(candidate) or not isinstance(row, Mapping):
                raise ValueError("proposal patch lies outside the remaining route")
            candidate[step] = deepcopy(row)
            changed.append(step)
        return candidate, max(changed) + 1

    @staticmethod
    def _compact(report: Mapping[str, Any]) -> dict[str, Any]:
        keys = ("complete", "admitted", "reason", "detail", "route_id", "worst_gain",
                "payback_step", "land_step", "rejoin_step", "planted_tiles",
                "harvested_units", "realized_target_units", "elapsed_seconds")
        return {key: report[key] for key in keys if key in report}

    def __call__(self, mechanics: Any, observation: Mapping[str, Any],
                 configuration: Mapping[str, Any], routes: Mapping[str, Sequence[Mapping[str, Any]]],
                 proposals: Sequence[Mapping[str, Any]]):
        started = time.perf_counter()
        now = _integer(observation["step"], "step")
        diagnostics = []
        base_cache = {}
        selected = None
        selected_gain = float("-inf")
        ordered = sorted(enumerate(proposals),
                         key=lambda row: (-_proposal_rank(mechanics, observation, row[1]), row[0]))
        complete = True
        for _index, proposal in ordered[:self.max_proposals]:
            elapsed = time.perf_counter() - started
            if elapsed >= self.seconds:
                complete = False
                break
            variants = proposal.get("variants")
            tiles = proposal.get("tiles")
            if not isinstance(variants, Mapping) or not variants or not isinstance(tiles, list) or not tiles:
                diagnostics.append({"crop": proposal.get("crop"), "admitted": False,
                                    "reason": "invalid_proposal_shape", "variants": {}})
                continue
            row = {"crop": proposal.get("crop"), "workers": proposal.get("workers"),
                   "tiles": len(tiles), "admitted": True, "variants": {}}
            worst = float("inf")
            for route_id, variant in variants.items():
                remaining = self.seconds - (time.perf_counter() - started)
                if remaining <= 0:
                    row.update(admitted=False, reason="incomplete_budget")
                    complete = False
                    break
                base = routes.get(route_id)
                if base is None:
                    row.update(admitted=False, reason="unknown_route_variant")
                    break
                try:
                    candidate, rejoin = self._candidate(base, variant, now)
                except (TypeError, ValueError, KeyError, IndexError) as exc:
                    row.update(admitted=False, reason="invalid_proposal_shape",
                               detail=f"{type(exc).__name__}: {exc}")
                    break
                report = evaluate_bundle(
                    observation, configuration, mechanics, base, candidate,
                    {"route_id": str(route_id), "base_route_id": str(route_id),
                     "target_quadrant": "SE", "rejoin_step": rejoin,
                     "required_tiles": tiles, "minimum_planted_tiles": len(tiles)},
                    minimum_gain=self.minimum_gain, seconds=remaining,
                    base_cache=base_cache)
                row["variants"][str(route_id)] = self._compact(report)
                if not report.get("complete"):
                    complete = False
                if not report.get("admitted"):
                    row.update(admitted=False, reason=report.get("reason", "rejected"))
                    break
                worst = min(worst, float(report["worst_gain"]))
            if row["admitted"]:
                row.update(reason="all_compatible_routes_pay_back", worst_gain=worst)
                selected = proposal
                selected_gain = worst
            diagnostics.append(row)
            # Proposals are already ranked by current public economics. Execute
            # the first fully funded reachable bundle, retaining enough time for
            # the selected action and avoiding machine-speed-dependent rescans.
            if selected is not None:
                break
        self.last_report = {
            "complete": complete,
            "admitted": selected is not None,
            "reason": "selected_strict_payback" if selected is not None else
                      ("incomplete_budget" if not complete else "no_proposal_payback"),
            "options": len(proposals), "evaluated": len(diagnostics),
            "selected_worst_gain": selected_gain if selected is not None else None,
            "elapsed_seconds": time.perf_counter() - started,
            "proposals": diagnostics,
            "assumptions": "current public/own state; known shops; declared matched-supply stress",
        }
        return selected
