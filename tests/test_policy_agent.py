"""PA1 tests: the plan contract, the one job board, and the season it lands.

Two layers, deliberately:

  * **Invariants with synthetic observations.** The board and the market are pure
    functions over ``FarmState``, so their traps are cheap to pin: the atomic PLANT
    void, the planting-day water, the calendar window, the price floor, the land
    floor that must not starve the seed budget, unlockable-quadrant targeting, and
    the no-animals boundary.
  * **Season-level landing.** A full episode against a real opponent proves what a
    unit test cannot: the board keeps the order cap, never targets a LOCKED tile,
    plants only its own tiles and only inside its calendar, hires to the plan's
    curve, and finishes the season with money in the bank where ``param_agent``
    drains to zero.
"""

from __future__ import annotations

import copy
import importlib
import json
from collections import Counter
from collections.abc import Callable
from pathlib import Path

import pytest

pa = importlib.import_module("kaggriculture.agents.policy_agent")
pm = importlib.import_module("kaggriculture.agents.param_agent")

from kaggriculture.loading import load_agent  # noqa: E402
from kaggriculture.runner import run_episode_trace  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
TAPE = str(ROOT / "src" / "kaggriculture" / "agents" / "baseline_v8_1.py")
FOREIGN_PLAN = str(ROOT / "plans" / "planner-author-rev3-opus.json")
BOARD = 10
LAYOUT = pa.FarmLayout(pa.DEFAULT_PLAN)
PLAN_TILES = {crop: list(tiles) for crop, tiles in LAYOUT.tiles_of.items()}
SHED_TILES = pa.shed_access_tiles()


# --------------------------------------------------------------------------- #
# Synthetic observations.
# --------------------------------------------------------------------------- #

def plant(crop, day, yield_units=1, watered=False, age=0, **extra):
    cell = {
        "kind": "PLANT", "crop": crop, "planted_day": day - age,
        "watered_today": watered, "consecutive_unwatered": 0 if watered else 1,
        "yield_units": yield_units, "max_lifespan_step": -1, "fertilized_until_day": -1,
    }
    cell.update(extra)
    return cell


def grid(cells=None, unlocked=("NW",)):
    """A 10x10 board: NW empty, everything not unlocked is LOCKED."""
    cells = dict(cells or {})
    out = []
    for y in range(BOARD):
        row = []
        for x in range(BOARD):
            pos = (x, y)
            if pos in cells:
                row.append(cells[pos])
            elif pa.quadrant_of(x, y) in unlocked:
                row.append(None)
            else:
                row.append("LOCKED")
        out.append(row)
    return out


def obs(day=0, hour=0, money=3000, seeds=None, shed=None, hands=(), inventories=None,
        prices=None, unlocked=("NW",), cells=None, hires_today=0, player=0, farmer=(4, 4)):
    farm = {
        "money": money, "farmer": list(farmer), "hands": [list(p) for p in hands],
        "unlocked_quadrants": list(unlocked), "hires_today": hires_today,
        "tiles": grid(cells, unlocked),
    }
    return {
        "step": day * 24 + hour, "day": day, "hour": hour, "player": player,
        "farms": [farm, copy.deepcopy(farm)],
        "private": {
            "seeds": dict(seeds or {}),
            "shed": dict(shed or {}),
            "inventories": list(inventories) if inventories is not None
            else [{} for _ in range(len(hands) + 1)],
        },
        "market": {"prices": dict(prices or {}), "inventory": {}},
    }


def state(**kwargs):
    return pa.read_state(obs(**kwargs))


def notes(trace, kind):
    return [e for e in trace if e[0] == kind]


def market_types(orders):
    return {order[0] for order in orders}


def job_of(cell, crop, day, seeds=0, hour=0, plantable=True):
    """``tile_job`` with the None contract asserted, so tests stay readable."""
    job = pa.tile_job(cell, crop, day, seeds, hour, plantable)
    assert job is not None
    return job


# --------------------------------------------------------------------------- #
# The plan vector and the layout.
# --------------------------------------------------------------------------- #

def test_default_plan_is_valid_four_quadrant_and_animal_free():
    plan = pm.validate_plan(pa.DEFAULT_PLAN)
    assert plan["land"]["quadrants"] == 4
    assert plan["land"]["buy_days"] == sorted(plan["land"]["buy_days"])
    assert all(entry["count"] == 0 for entry in plan["animals"])
    assert {entry["crop"] for entry in plan["crops"]} == set(pa.CROP_OF)
    assert sum(entry["tiles"] for entry in plan["crops"]) == len(LAYOUT.workable) <= 96


def test_layout_is_prefix_stable_and_covers_the_board_without_the_shed():
    tiles = [tile for tile, _ in LAYOUT.plan_tiles]
    assert len(tiles) == len(set(tiles)) == 96
    assert not set(tiles) & set(SHED_TILES)
    assert Counter(crop for _, crop in LAYOUT.plan_tiles) == Counter(
        {entry["crop"]: entry["tiles"] for entry in pa.DEFAULT_PLAN["crops"]})
    # Prefix stable: the tile a crop is planted on cannot move under it later.
    again = pa.FarmLayout(pa.DEFAULT_PLAN)
    assert again.crop_of == LAYOUT.crop_of
    assert [t for q in pa.QUADRANTS for t in LAYOUT.tiles_in(q)] == tiles
    for crop in ("WHEAT", "CARROT", "STRAWBERRY"):
        assert len({LAYOUT.quadrant_of[t] for t in LAYOUT.tiles_of[crop]}) == 4


def test_layout_rejects_a_plan_that_overbooks_the_workable_cells():
    plan = copy.deepcopy(pa.DEFAULT_PLAN)
    plan["crops"][0]["tiles"] = 80  # valid per-field and per the 100-cell budget
    with pytest.raises(pm.PlanError):
        pa.FarmLayout(plan)


def test_agent_is_a_plain_one_argument_closure():
    agent: Callable[[dict], dict] = pa.make_agent()
    assert callable(agent)
    assert agent.__code__.co_argcount == 1  # the Kaggle runner truncates on this
    action = agent(obs())
    assert set(action) == {"farmer", "hands", "market"}


def test_disabled_agent_degrades_byte_clean():
    off = pa.make_agent(enabled=False)
    rich = obs(hands=[(1, 1), (2, 2)], seeds={"WHEAT": 9}, shed={"WHEAT": 40},
               unlocked=("NW", "NE", "SW", "SE"),
               prices={"WHEAT": 30}, cells={PLAN_TILES["WHEAT"][0]: plant("WHEAT", 0)})
    expected = {"farmer": ["PASS"], "hands": [["PASS"], ["PASS"]], "market": []}
    assert off(rich) == expected
    assert off({}) == {"farmer": ["PASS"], "hands": [], "market": []}


@pytest.mark.parametrize("bad", [None, {}, {"farms": [None]}, {"farms": [{}], "private": None},
                                 {"step": "x", "farms": [{"tiles": "nope"}]}])
def test_malformed_observation_never_raises(bad):
    action = pa.agent(bad)
    assert isinstance(action, dict) and set(action) == {"farmer", "hands", "market"}


# --------------------------------------------------------------------------- #
# The state model.
# --------------------------------------------------------------------------- #

def test_read_state_rebuilds_every_field_from_one_observation():
    s = state(day=4, hour=7, money=1234.5, hands=[(1, 2)], seeds={"WHEAT": 3},
              shed={"WHEAT": 5, "MELON": 2}, inventories=[{"CARROT": 1}, {"WHEAT": 4}],
              prices={"WHEAT": 31}, unlocked=("NW", "NE"))
    assert (s.step, s.day, s.hour) == (103, 4, 7)
    assert s.money == 1234.5
    assert s.seeds == {"WHEAT": 3} and s.shed_used == 7 and s.shed_room == 93
    assert s.positions == ((4, 4), (1, 2)) and s.roster == 1
    assert s.carried(0) == {"CARROT": 1} and s.carried_units(1) == 4
    assert s.carried(9) == {} and s.carried_units(9) == 0
    assert len(s.unlocked) == 2 and s.unlocked_at(PLAN_TILES["WHEAT"][0]) is True
    assert s.unlocked_at(PLAN_TILES["WHEAT"][-1]) is False  # SE, still LOCKED
    assert s.price("WHEAT") == 31 and s.price("MELON") == 250  # engine base price


def test_read_state_degrades_unreadable_tiles_to_locked():
    broken = obs()
    broken["farms"][0]["tiles"] = [[None]]  # a 1x1 board
    s = pa.read_state(broken)
    assert s.cell((9, 9)) == "LOCKED" and s.unlocked_at((9, 9)) is False


# --------------------------------------------------------------------------- #
# The one job board.
# --------------------------------------------------------------------------- #

def test_job_priorities_follow_the_spec_chain():
    day = 6
    cow = {"kind": "PASTURE", "animal": "COW", "yield_units": 0, "fed_today": True,
           "fertilizer_available": True}
    cells = {
        PLAN_TILES["WHEAT"][0]: plant("WHEAT", day, yield_units=4, watered=True, age=4),
        PLAN_TILES["CARROT"][0]: plant("CARROT", day, yield_units=1, age=2),
        PLAN_TILES["MELON"][0]: {"kind": "WEED"},
        PLAN_TILES["STRAWBERRY"][0]: plant("STRAWBERRY", day, yield_units=0, watered=True,
                                           age=0),
        # Survival-only water: nothing to gain (an ongoing crop only starts paying at
        # day 10) and the plant can afford to skip a day, so it ranks below the weed.
        PLAN_TILES["STRAWBERRY"][1]: plant("STRAWBERRY", day, yield_units=0, age=6,
                                           consecutive_unwatered=0),
        # A bonus water is water-due, at the SAME tier as the fatal one.
        PLAN_TILES["WHEAT"][2]: plant("WHEAT", day, yield_units=1, age=3,
                                      consecutive_unwatered=0),
        PLAN_TILES["CARROT"][1]: cow,
        PLAN_TILES["CARROT"][2]: plant("CARROT", day, yield_units=0, watered=True, age=5),
    }
    harvest = job_of(cells[PLAN_TILES["WHEAT"][0]], "WHEAT", day)
    urgent = job_of(cells[PLAN_TILES["CARROT"][0]], "CARROT", day)
    survival = job_of(cells[PLAN_TILES["STRAWBERRY"][1]], "STRAWBERRY", day)
    bonus = job_of(cells[PLAN_TILES["WHEAT"][2]], "WHEAT", day)
    weed = job_of(cells[PLAN_TILES["MELON"][0]], "MELON", day)
    empty = job_of(None, "STRAWBERRY", day, seeds=4)
    collect = job_of(cow, "CARROT", day)
    dig = job_of(cells[PLAN_TILES["CARROT"][2]], "CARROT", day)
    assert [harvest[0], urgent[0], survival[0], bonus[0], weed[0], empty[0],
            collect[0], dig[0]] == ["HARVEST", "WATER", "WATER", "WATER", "DIG",
                                    "PLANT", "COLLECT_FERTILIZER", "DIG"]
    # harvest < water-due (= bonus) < survival water < weed < plant < collect < dig
    assert harvest[1] == 0
    assert urgent[1] == bonus[1] == 1
    assert survival[1] == 2 and weed[1] == 3 and empty[1] == 4
    assert collect[1] == 5 and dig[1] == 6


def test_no_dig_of_a_live_perennial_but_dead_one_shots_are_reclaimed():
    day = 20
    strawberry = plant("STRAWBERRY", day, yield_units=0, watered=True, age=5)
    wheat = plant("WHEAT", day, yield_units=0, watered=True, age=5)
    assert pa.tile_job(strawberry, "STRAWBERRY", day, 0, 0, True) is None
    assert job_of(wheat, "WHEAT", day)[0] == "DIG"


def test_board_never_asks_for_more_plants_than_the_seeds_held():
    """The engine voids EVERY PLANT of a crop whose demand exceeds its stock."""
    tiles = PLAN_TILES["WHEAT"][:3]
    positions = [tiles[0], tiles[1], tiles[2]]
    s = state(day=0, hour=2, seeds={"WHEAT": 1}, farmer=positions[0],
              hands=positions[1:])
    farmer, hands = pa.unit_actions(LAYOUT, s)
    actions = [farmer, *hands]
    assert sum(1 for a in actions if a[0] == "PLANT") == 1
    assert [a for a in actions if a[0] == "PLANT"] == [["PLANT", "WHEAT"]]
    assert sum(1 for a in actions if a[0] == "PASS") == 2


def test_board_plants_only_inside_the_calendar_and_never_in_the_last_hour():
    tile = PLAN_TILES["WHEAT"][0]
    s = state(day=0, hour=2, seeds={"WHEAT": 50}, farmer=tile)
    assert pa.unit_actions(LAYOUT, s)[0] == ["PLANT", "WHEAT"]
    late = state(day=0, hour=23, seeds={"WHEAT": 50}, farmer=tile)
    assert pa.unit_actions(LAYOUT, late)[0] != ["PLANT", "WHEAT"]
    # WHEAT's calendar is every 4 days with a 2-day grace: day 6 is inside, 7 is not.
    inside = state(day=6, hour=2, seeds={"WHEAT": 50}, farmer=tile)
    assert pa.unit_actions(LAYOUT, inside)[0] == ["PLANT", "WHEAT"]
    outside = state(day=7, hour=2, seeds={"WHEAT": 50}, farmer=tile)
    assert pa.unit_actions(LAYOUT, outside)[0] != ["PLANT", "WHEAT"]


def test_board_never_targets_a_locked_quadrant():
    s = state(day=2, hour=2, seeds={"WHEAT": 9}, farmer=(4, 4), unlocked=("NW",))
    trace: list = []
    farmer = pa.unit_actions(LAYOUT, s, note=lambda *e: trace.append(e))[0]
    assert farmer != ["PASS"]
    moved = [e for e in trace if e[0] == "act"][0][3]
    assert s.unlocked_at(moved) and pa.quadrant_of(*moved) == "NW"


def test_board_sector_keeps_units_in_their_own_corner():
    hands = [PLAN_TILES["MELON"][i % len(PLAN_TILES["MELON"])] for i in range(8)]
    s = state(day=6, hour=3, unlocked=("NW", "NE", "SW", "SE"), cells={
        PLAN_TILES["WHEAT"][0]: plant("WHEAT", 6, yield_units=4, watered=True, age=4),
        PLAN_TILES["WHEAT"][1]: plant("WHEAT", 6, yield_units=4, watered=True, age=4),
    }, farmer=(4, 4), hands=hands)
    sectors = pa.sectors(LAYOUT, s, s.positions)
    assert len(sectors) == len(s.positions)
    covered = [tile for block in sectors for tile in block]
    assert sorted(covered) == sorted(tile for tile, _ in LAYOUT.plan_tiles)
    assert len(set(covered)) == len(covered)  # disjoint: no two units on one tile


def test_loaded_unit_drops_at_the_shed_and_only_carries_when_it_fits():
    tile = PLAN_TILES["WHEAT"][0]
    sprout = plant("WHEAT", 0, yield_units=4, watered=True, age=4)
    drop = state(day=3, hour=3, shed={}, inventories=[{"WHEAT": 5}], farmer=SHED_TILES[0],
                 cells={tile: sprout})
    trace: list = []
    action = pa.unit_actions(LAYOUT, drop, note=lambda *e: trace.append(e))[0]
    assert action == ["DROP"] and notes(trace, "drop")

    full = state(day=3, hour=3, shed={"WHEAT": 100}, inventories=[{"WHEAT": 5}],
                 farmer=SHED_TILES[0], cells={tile: sprout})
    farmer, _ = pa.unit_actions(LAYOUT, full)
    assert farmer != ["DROP"]  # the shed is full: the load is not droppable
    assert farmer[0] in ("NORTH", "SOUTH", "EAST", "WEST")

    late = state(day=28, hour=3, shed={"WHEAT": 100}, inventories=[{"WHEAT": 5}],
                 farmer=SHED_TILES[0], cells={tile: sprout})
    late_trace: list = []
    pa.unit_actions(LAYOUT, late, note=lambda *e: late_trace.append(e))
    assert notes(late_trace, "carry")  # endgame: the load outranks farm work


# --------------------------------------------------------------------------- #
# The market.
# --------------------------------------------------------------------------- #

def test_sells_are_metered_one_order_per_product():
    plan = pm.validate_plan({**copy.deepcopy(pa.DEFAULT_PLAN),
                             "market": {"sell_chunk_cap": 10, "sell_from_day": 0,
                                        "seed_reserve": 1}})
    layout = pa.FarmLayout(plan)
    s = state(money=500, day=5, shed={"WHEAT": 40, "MELON": 3},
              prices={"WHEAT": 30, "MELON": 200, "CARROT": 30, "STRAWBERRY": 120})
    orders = pa.market_orders(plan, layout, s)
    sells = {order[1]: order[2] for order in orders if order[0] == "SELL"}
    assert sells == {"WHEAT": 10, "MELON": 3}
    assert [o[0] for o in orders].count("SELL") == len(sells)


def test_sells_respect_the_price_floor_and_liquidate_at_the_endgame():
    held = {"WHEAT": 20, "STRAWBERRY": 20}
    cheap = {"WHEAT": 2, "STRAWBERRY": 3}   # both under half of their base price
    early = state(day=10, shed=held, prices=cheap)
    assert pa.sell_orders(pa.DEFAULT_PLAN, early) == []
    endgame = state(day=28, shed=held, prices=cheap)
    assert {o[1] for o in pa.sell_orders(pa.DEFAULT_PLAN, endgame)} == {"WHEAT", "STRAWBERRY"}


def test_shed_pressure_liquidates_before_a_full_shed_deletes_it():
    # The midnight drop DELETES what does not fit, so holding is only worth it
    # while the farm has room: 60 in the shed plus 30 carried is over the limit.
    s = state(day=10, shed={"CARROT": 60}, inventories=[{"CARROT": 30}],
              hands=[(1, 1)], prices={"CARROT": 5})
    assert pa.held_units(s) == 90
    assert [o[1] for o in pa.sell_orders(pa.DEFAULT_PLAN, s)] == ["CARROT"]


def test_seed_orders_are_one_per_crop_with_the_demand_quantity():
    plan = pa.DEFAULT_PLAN
    s = state(day=0, hour=0, money=3000, seeds={})
    orders = pa.market_orders(plan, LAYOUT, s)
    buys = [o for o in orders if o[0] == "BUY_SEED"]
    assert {o[1] for o in buys} == {"WHEAT", "CARROT", "STRAWBERRY", "MELON"}
    assert len(buys) == len({o[1] for o in buys})  # never a per-unit loop
    assert all(o[2] >= 1 for o in buys)
    assert max(o[2] for o in buys) <= 8  # bounded by the tiles NW can plant today


def test_land_is_gated_by_its_plan_day_and_bought_in_engine_order():
    # (day, quadrants already unlocked, land orders this turn)
    cases = [(5, 1, 0), (6, 1, 1), (10, 1, 1), (11, 2, 1), (12, 2, 1), (13, 3, 1),
             (20, 4, 0)]
    for day, unlocked_count, expected in cases:
        unlocked = pa.QUADRANTS[:unlocked_count]
        s = state(day=day, money=20000, unlocked=unlocked)
        orders = pa.market_orders(pa.DEFAULT_PLAN, LAYOUT, s)
        assert [o[0] for o in orders].count("BUY_LAND") == expected, (day, unlocked)


def test_land_floor_is_windowed_and_never_claims_more_than_half_the_cash():
    plan = pa.DEFAULT_PLAN
    assert pa.land_floor(plan, 0, 1, 3000) == 0          # outside the NE window
    assert pa.land_floor(plan, 4, 1, 3000) == 1100       # inside: price + margin
    assert pa.land_floor(plan, 4, 1, 400) == 200         # bounded at half the cash
    assert pa.land_floor(plan, 20, 4, 3000) == 0         # every quadrant bought


def test_seed_budget_is_never_starved_by_the_land_floor():
    """The measured regression: a $1,100 reserve against $400 of cash bought nothing
    for nine days and the tiles stood empty. The floor may not zero the seed budget."""
    s = state(day=4, hour=1, money=400, seeds={})
    orders = pa.market_orders(pa.DEFAULT_PLAN, LAYOUT, s)
    assert [o[0] for o in orders].count("BUY_SEED") >= 1


def test_hires_are_exempt_from_the_land_floor_and_respect_the_engine_cap():
    s = state(day=4, hour=1, money=400, unlocked=("NW",), seeds={})
    orders = pa.market_orders(pa.DEFAULT_PLAN, LAYOUT, s)
    assert [o[0] for o in orders].count("HIRE") >= 1
    assert len(orders) <= pm.MAX_ORDERS
    rich = state(day=4, hour=1, money=20000, shed={"WHEAT": 90, "CARROT": 30, "MELON": 20,
                                                   "STRAWBERRY": 40}, seeds={})
    assert len(pa.market_orders(pa.DEFAULT_PLAN, LAYOUT, rich)) <= pm.MAX_ORDERS


def test_roster_curve_and_hire_cost_come_from_the_plan():
    assert [pm.hand_target_for_day(pa.DEFAULT_PLAN, day) for day in (0, 4, 8, 29)] == \
        [4, 8, 12, 12]
    s = state(day=9, hour=0, money=20000, unlocked=pa.QUADRANTS)
    hires = [o[0] for o in pa.market_orders(pa.DEFAULT_PLAN, LAYOUT, s)].count("HIRE")
    assert 1 <= hires <= pa.DEFAULT_POLICY.hires_per_turn


def test_pa1_emits_no_animal_or_structure_order_or_verb():
    banned_orders = {"BUY_ANIMAL", "BUY_PRODUCT", "PLACE"}
    banned_verbs = {"BUILD_COOP", "BUILD_PASTURE", "FEED", "COLLECT_FERTILIZER",
                    "FERTILIZE", "CARE", "PLACE"}
    foreign = pm.validate_plan(json.loads(Path(FOREIGN_PLAN).read_text()))
    for plan in (pa.DEFAULT_PLAN, foreign):
        layout = pa.FarmLayout(plan)
        s = state(day=8, hour=3, money=20000, unlocked=pa.QUADRANTS, seeds={},
                  shed={"WHEAT": 20})
        orders = pa.market_orders(plan, layout, s)
        assert not market_types(orders) & banned_orders
        farmer, hands = pa.unit_actions(layout, s)
        verbs = {a[0] for a in [farmer, *hands] if a}
        assert not verbs & banned_verbs
        assert verbs <= {"PLANT", "WATER", "HARVEST", "DIG", "DROP", "PICKUP",
                         "PASS", "NORTH", "SOUTH", "EAST", "WEST"}


# --------------------------------------------------------------------------- #
# Season-level landing. These are the tests that matter: they are the only ones
# that can prove the board's work reaches the engine on a real board.
# --------------------------------------------------------------------------- #

SEEDS = (0, 1, 2)
TURNS = 24


def season(seed, opponent, trace=None):
    ours = pa.make_agent(trace=trace)
    return ours, run_episode_trace(ours, opponent, seed)


def day_money(steps, seat, day):
    index = min(day * TURNS + TURNS - 1, len(steps) - 1)
    return steps[index][0]["observation"]["farms"][seat]["money"]


def plants_in(trace):
    return [(e[1], e[2], e[3]) for e in trace if e[0] == "plant"]


def test_full_season_survives_on_three_seeds():
    """Zero crashes, zero escapes, and the order cap held on EVERY turn."""
    for seed in SEEDS:
        trace: list = []
        ours, run = season(seed, load_agent(TAPE), trace)
        assert not notes(trace, "crash"), f"seed {seed}: the agent crashed into a pass"
        for step in run["steps"]:
            action = step[0].get("action") or {}
            assert len(action.get("market") or []) <= pm.MAX_ORDERS
        assert run["banks"][0] > 0, f"seed {seed}: the farm drained to {run['banks'][0]}"
        # Zero escapes: PA1 has no animals to lose, and no structure ever appeared.
        final = run["steps"][-1][0]["observation"]["farms"][0]["tiles"]
        for row in final:
            for cell in row:
                if isinstance(cell, dict):
                    assert cell.get("kind") in ("PLANT", "WEED")
                    assert "animal" not in cell
        # Zero escaped WORK: every tile op landed on a tile that was unlocked then.
        for index, step in enumerate(run["steps"]):
            action = step[0].get("action") or {}
            farm = step[0]["observation"]["farms"][0]
            units = [farm.get("farmer")] + list(farm.get("hands") or [])
            for order, unit in zip([action.get("farmer"), *(action.get("hands") or [])],
                                   units, strict=False):
                if not isinstance(order, list) or not order:
                    continue
                if order[0] not in ("PLANT", "WATER", "HARVEST", "DIG"):
                    continue
                assert unit is not None, f"step {index}: a tile op without a unit"
                cell = pa.read_state({"step": index, "farms": [farm]}).cell(tuple(unit))
                assert cell != "LOCKED", f"step {index}: {order} on a LOCKED tile"


def test_full_season_lands_the_plan_inside_the_ownership_boundaries():
    trace: list = []
    ours, run = season(0, load_agent(TAPE), trace)
    layout = pa.FarmLayout(pa.DEFAULT_PLAN)
    steps = run["steps"]

    # 1. Land: bought in engine order, never before the gated day, one per turn.
    buys = []
    for step in steps[1:]:
        action = step[0].get("action") or {}
        if any(o and o[0] == "BUY_LAND" for o in (action.get("market") or [])):
            obs_now = step[0]["observation"]
            buys.append((obs_now["day"], len(obs_now["farms"][0]["unlocked_quadrants"])))
    assert len(buys) == 3, buys
    for index, (day, quadrants) in enumerate(buys):
        assert day >= pa.DEFAULT_PLAN["land"]["buy_days"][index], (index, day)
        assert quadrants == index + 2, (index, quadrants)
    assert steps[-1][0]["observation"]["farms"][0]["unlocked_quadrants"] == \
        list(pa.QUADRANTS)

    # 2. Planting: only our tiles, only inside the calendar window, and never on a
    #    tile another crop owns (the plan is the only thing that decides where).
    events = plants_in(trace)
    assert len(events) > 200, len(events)
    calendar = {e["crop"]: e["plant_days"] for e in pa.DEFAULT_PLAN["crops"]}
    for tile, crop, day in events:
        assert layout.crop_of[tile] == crop, (tile, crop, layout.crop_of[tile])
        assert any(d <= day <= d + pa.DEFAULT_POLICY.plant_grace
                   for d in calendar[crop]), (crop, day)

    # 3. Planting-day water: the engine weeds an unwatered planting day that night,
    #    so the board has to water what it just planted in the same day.
    waters = {(e[1], e[3]) for e in trace if e[0] == "water"}
    same_day = sum(1 for tile, _crop, day in events if (tile, day) in waters)
    assert same_day >= 0.75 * len(events), (same_day, len(events))

    # 4. Hires follow the plan's curve (midday roster vs curve, with tolerance for a
    #    day the wallet or the order cap could not fund the full roster).
    reach = 0
    for day in range(30):
        index = min(day * TURNS + 18, len(steps) - 1)
        roster = len(steps[index][0]["observation"]["farms"][0]["hands"])
        target = pm.hand_target_for_day(pa.DEFAULT_PLAN, day)
        assert roster <= target
        if roster >= target - 2:
            reach += 1
    assert reach >= 24, reach

    # 5. The boundary: no animal work of any kind, anywhere in the season.
    assert not [e for e in trace if e[0] in ("feed", "collect", "place", "care")]
    assert not notes(trace, "crash")


def test_bank_trajectory_beats_param_agent_and_stays_positive_early():
    """The PA1 gate: ``param_agent`` drains to ~$0 by day 9-14; we must not.

    Same plan on both sides, so the difference is the board and the market layer
    rather than the plan vector. Absolute banks are asserted, not just ordering.
    """
    for seed in (0, 1):
        opponent = pm.make_agent(pa.DEFAULT_PLAN)
        ours, run = season(seed, opponent)
        steps = run["steps"]
        trajectory = {day: (day_money(steps, 0, day), day_money(steps, 1, day))
                      for day in (5, 9, 14, 20, 29)}
        for day in (9, 14, 20, 29):
            mine, theirs = trajectory[day]
            assert mine > 0, f"seed {seed} day {day}: drained to {mine}"
        # ``param_agent`` is still solvent at day 9 and dead from day 14 on: the
        # comparison is where it drained, not where it was still spending less.
        for day in (14, 20, 29):
            mine, theirs = trajectory[day]
            assert mine > theirs, f"seed {seed} day {day}: {mine} vs {theirs}"
        assert trajectory[29][0] > 10 * max(1.0, trajectory[29][1]), \
            f"seed {seed}: {trajectory}"
