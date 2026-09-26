"""PA1+PA2 tests: the plan contract, the one job board, and the season it lands.

Two layers, deliberately:

  * **Invariants with synthetic observations.** The board and the market are pure
    functions over ``FarmState``, so their traps are cheap to pin: the atomic PLANT
    void, the planting-day water, the calendar window, the price floor, the land
    floor that must not starve the seed budget, unlockable-quadrant targeting, the
    animal chain (feed before the product, care before the collect, the carry chain
    that feeds nothing without a shed trip), and the structure cells no crop may
    ever be planted on.
  * **Season-level landing.** A full episode against a real opponent proves what a
    unit test cannot: the board keeps the order cap, never targets a LOCKED tile,
    plants only its own tiles and only inside its calendar, hires to the plan's
    curve, feeds the herd every day (two unfed midnights and the animal is gone),
    never loses an animal to starvation, and finishes the season with money in the
    bank where ``param_agent`` drains to zero.
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

def test_default_plan_is_valid_four_quadrant_with_a_calibrated_herd():
    plan = pm.validate_plan(pa.DEFAULT_PLAN)
    assert plan["land"]["quadrants"] == 4
    assert plan["land"]["buy_days"] == sorted(plan["land"]["buy_days"])
    assert {entry["crop"] for entry in plan["crops"]} == set(pa.CROP_OF)
    # The cells are exactly full: every workable cell is a crop or a structure.
    structures = sum(entry["count"] for entry in plan["animals"])
    crops = sum(entry["tiles"] for entry in plan["crops"])
    assert structures == 16 and crops == 80 and crops + structures == len(LAYOUT.workable)
    assert len(LAYOUT.structure_plan) == structures
    # Geese first: the cheapest, fastest, deepest-market animal takes the early
    # capital. The buy days are a floor, never a schedule.
    order = [entry["animal"] for entry in plan["animals"]]
    assert order.index("GOOSE") == 0
    geese = next(e for e in plan["animals"] if e["animal"] == "GOOSE")
    assert geese["buy_days"] == sorted(geese["buy_days"])
    assert min(geese["buy_days"]) >= 1


def test_pa1_control_plan_is_still_a_valid_animal_free_vector():
    """The control the PA2 gate is measured against has to keep validating."""
    plan = pm.validate_plan(pa.ANIMAL_FREE_PLAN)
    assert all(entry["count"] == 0 for entry in plan["animals"])
    assert sum(entry["tiles"] for entry in plan["crops"]) == 96
    control = pa.FarmLayout(plan)
    assert control.structure_plan == () and len(control.tiles_of["WHEAT"]) == 34


def test_layout_reserves_the_nearest_cells_of_every_quadrant_for_structures():
    """Structures come off the crop block FIRST, and a crop never gets one back.

    That ordering is what makes BUILD irreversible and safe at the same time: the
    engine builds only onto an empty tile, so a reserved cell that no crop can be
    planted on is a structure that can never displace one.
    """
    tiles = [tile for tile, _ in LAYOUT.plan_tiles]
    assert len(tiles) == len(set(tiles)) == 96
    assert not set(tiles) & set(SHED_TILES)
    assert set(LAYOUT.crop_of).isdisjoint(LAYOUT.structure_of)
    assert len(LAYOUT.crop_of) == 80 and len(LAYOUT.structure_of) == 16
    counted = Counter(crop for _, crop in LAYOUT.plan_tiles if crop is not None)
    assert counted == Counter({entry["crop"]: entry["tiles"]
                               for entry in pa.DEFAULT_PLAN["crops"]})
    split = Counter(LAYOUT.quadrant_of[tile] for tile in LAYOUT.structure_of)
    assert split == Counter(dict.fromkeys(pa.QUADRANTS, 4))
    # Every structure cell is ONE OF THE NEAREST cells of its quadrant, which is
    # what keeps the feed chain short enough to run every day.
    for quadrant in pa.QUADRANTS:
        near = pa.designated_tiles(4, quadrant=quadrant)
        mine = [t for t in LAYOUT.structure_of if LAYOUT.quadrant_of[t] == quadrant]
        assert sorted(mine) == sorted(near)
    # Prefix stable: the tile a crop is planted on cannot move under it later.
    again = pa.FarmLayout(pa.DEFAULT_PLAN)
    assert again.crop_of == LAYOUT.crop_of and again.structure_of == LAYOUT.structure_of
    assert [t for q in pa.QUADRANTS for t in LAYOUT.tiles_in(q)] == tiles
    # Every crop still has tiles in all four quadrants (the mix is round-robined),
    # and each species' cells follow its own order in the plan.
    for crop in ("WHEAT", "CARROT", "STRAWBERRY"):
        assert len({LAYOUT.quadrant_of[t] for t in LAYOUT.tiles_of[crop]}) == 4
    assert LAYOUT.species_order == ("GOOSE", "COW", "SHEEP")
    assert len(LAYOUT.cells_of["GOOSE"]) == 8
    assert [species for _, species in LAYOUT.structure_plan][:8] == ["GOOSE"] * 8


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
        PLAN_TILES["CARROT"][1]: plant("CARROT", day, yield_units=0, watered=True, age=5),
    }
    harvest = job_of(cells[PLAN_TILES["WHEAT"][0]], "WHEAT", day)
    urgent = job_of(cells[PLAN_TILES["CARROT"][0]], "CARROT", day)
    survival = job_of(cells[PLAN_TILES["STRAWBERRY"][1]], "STRAWBERRY", day)
    bonus = job_of(cells[PLAN_TILES["WHEAT"][2]], "WHEAT", day)
    weed = job_of(cells[PLAN_TILES["MELON"][0]], "MELON", day)
    empty = job_of(None, "STRAWBERRY", day, seeds=4)
    dig = job_of(cells[PLAN_TILES["CARROT"][1]], "CARROT", day)
    assert [harvest[0], urgent[0], survival[0], bonus[0], weed[0], empty[0],
            dig[0]] == ["HARVEST", "WATER", "WATER", "WATER", "DIG", "PLANT", "DIG"]
    # harvest < water-due (= bonus) < survival water < weed < plant < dig
    assert harvest[1] == 0
    assert urgent[1] == bonus[1] == 1
    assert survival[1] == 2 and weed[1] == 3 and empty[1] == 4 and dig[1] == 6


def animal(day=6, species="GOOSE", **extra):
    cell = {"kind": "COOP" if species == "GOOSE" else "PASTURE", "animal": species,
            "placed_day": day - 8, "yield_units": 0, "consecutive_unfed": 0,
            "fed_today": False, "cared_today": False, "fertilizer_available": False,
            "pending_care_bonus": 0}
    cell.update(extra)
    return cell


def test_animal_chain_is_one_tour_per_day_in_the_order_that_keeps_it_alive():
    """Feed, then care, then the product, then the exposure - one job per turn.

    A unit standing on an animal tile performs ONE action per turn, so the tile has
    to offer the next thing due instead of four jobs a priority race would reorder.
    The order is measured, not aesthetic: harvesting the product first starred care
    (2 cares a day for a herd the chain could carry) and collecting last left 24
    collects out of ~136 exposures.
    """
    tile = pa.FarmLayout(pa.DEFAULT_PLAN).cells_of["GOOSE"][0]
    hungry = animal(fed_today=False)
    fed_uncared = animal(fed_today=True)
    cared = animal(fed_today=True, cared_today=True)
    assert pa.tile_job(hungry, None, 6, 0, 0, False, structure="GOOSE") == \
        ("FEED", pa.DEFAULT_POLICY.feed_priority, None, "WHEAT")
    assert pa.tile_job(fed_uncared, None, 6, 0, 0, False, structure="GOOSE")[0] == "CARE"
    assert pa.tile_job(animal(fed_today=True, yield_units=3), None, 6, 0, 0, False,
                       structure="GOOSE")[0] == "CARE"
    assert pa.tile_job(cared, None, 6, 0, 0, False, structure="GOOSE") is None
    assert pa.tile_job(animal(fed_today=True, cared_today=True, fertilizer_available=True),
                       None, 6, 0, 0, False, structure="GOOSE")[0] == "COLLECT_FERTILIZER"
    assert pa.tile_job(animal(fed_today=True, cared_today=True, yield_units=2), None, 6, 0,
                       0, False, structure="GOOSE")[0] == "HARVEST"
    # Tier order: FEED above harvest, and the whole chain above the crop jobs it
    # shares a board with, with care/collect at the water-due tier.
    feed = pa.tile_job(hungry, None, 6, 0, 0, False, structure="GOOSE")
    care = pa.tile_job(fed_uncared, None, 6, 0, 0, False, structure="GOOSE")
    product = pa.tile_job(animal(fed_today=True, cared_today=True, yield_units=2), None, 6,
                          0, 0, False, structure="GOOSE")
    collect = pa.tile_job(animal(fed_today=True, cared_today=True,
                                 fertilizer_available=True), None, 6, 0, 0, False,
                          structure="GOOSE")
    assert feed[1] < product[1] < care[1] == collect[1] == pa.DEFAULT_POLICY.collect_priority
    assert care[1] == pa._P_WATER_URGENT and product[1] == pa._P_HARVEST
    assert feed[3] == "WHEAT"  # the FEED carry chain: no wheat, no job
    assert tile in pa.FarmLayout(pa.DEFAULT_PLAN).cells_of["GOOSE"]


def test_structure_cells_only_ever_offer_build_place_or_clear():
    layout = pa.FarmLayout(pa.DEFAULT_PLAN)
    coop = layout.cells_of["GOOSE"][0]
    pasture = layout.cells_of["COW"][0]
    assert pa.tile_job(None, None, 6, 0, 0, False, structure="GOOSE") is None  # no lead
    assert pa.tile_job(None, None, 6, 0, 0, False, structure="GOOSE", build=True) == \
        ("BUILD_COOP", pa._P_BUILD, "GOOSE", None)
    assert pa.tile_job(None, None, 6, 0, 0, False, structure="COW", build=True)[0] == \
        "BUILD_PASTURE"
    assert pa.tile_job({"kind": "WEED"}, None, 6, 0, 0, False, structure="GOOSE",
                       build=True)[0] == "DIG"
    plain = pa.tile_job({"kind": "COOP"}, None, 6, 0, 0, False, structure="GOOSE")
    assert plain == ("PLACE", pa._P_PLACE, "GOOSE", "GOOSE")
    assert pa.tile_job({"kind": "PASTURE"}, None, 6, 0, 0, False, structure="COW")[3] == "COW"
    # A reserved cell carrying the WRONG structure is not ours to touch.
    assert pa.tile_job({"kind": "PASTURE"}, None, 6, 0, 0, False, structure="GOOSE") is None
    # A crop job never appears on a reserved cell: the layout keeps them separate.
    assert coop not in layout.crop_of and pasture not in layout.crop_of


def test_no_dig_of_a_live_perennial_but_dead_one_shots_are_reclaimed():
    day = 20
    strawberry = plant("STRAWBERRY", day, yield_units=0, watered=True, age=5)
    wheat = plant("WHEAT", day, yield_units=0, watered=True, age=5)
    assert pa.tile_job(strawberry, "STRAWBERRY", day, 0, 0, True) is None
    assert job_of(wheat, "WHEAT", day)[0] == "DIG"


def test_board_never_asks_for_more_plants_than_the_seeds_held():
    """The engine voids EVERY PLANT of a crop whose demand exceeds its stock.

    PA2 changed what the other units do with their turn (the structure lead is
    open from day 0, so a unit with nothing to plant goes to build), but not the
    rule under test: exactly one unit plants, because there is exactly one seed.
    """
    tiles = PLAN_TILES["WHEAT"][:3]
    positions = [tiles[0], tiles[1], tiles[2]]
    s = state(day=0, hour=2, seeds={"WHEAT": 1}, farmer=positions[0],
              hands=positions[1:])
    farmer, hands = pa.unit_actions(LAYOUT, s)
    actions = [farmer, *hands]
    assert sum(1 for a in actions if a[0] == "PLANT") == 1
    assert [a for a in actions if a[0] == "PLANT"] == [["PLANT", "WHEAT"]]
    assert all(a[0] in pa.UNIT_VERBS for a in actions), actions
    assert not [a for a in actions if a[0] == "WATER"]  # nothing is planted yet


def test_board_plants_only_inside_the_calendar_and_never_in_the_last_hour():
    tile = PLAN_TILES["WHEAT"][0]
    # With the structure lead open, a unit standing on an empty crop tile goes to
    # build instead of planting, so this test closes the lead to isolate the calendar.
    policy = pa.DEFAULT_POLICY.replace(structure_lead=0)
    s = state(day=0, hour=2, seeds={"WHEAT": 50}, farmer=tile)
    assert pa.unit_actions(LAYOUT, s, policy)[0] == ["PLANT", "WHEAT"]
    late = state(day=0, hour=23, seeds={"WHEAT": 50}, farmer=tile)
    assert pa.unit_actions(LAYOUT, late, policy)[0] != ["PLANT", "WHEAT"]
    # WHEAT's calendar is every 4 days with a 2-day grace: day 6 is inside, 7 is not.
    inside = state(day=6, hour=2, seeds={"WHEAT": 50}, farmer=tile)
    assert pa.unit_actions(LAYOUT, inside, policy)[0] == ["PLANT", "WHEAT"]
    outside = state(day=7, hour=2, seeds={"WHEAT": 50}, farmer=tile)
    assert pa.unit_actions(LAYOUT, outside, policy)[0] != ["PLANT", "WHEAT"]


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


def test_the_board_owns_its_whole_verb_vocabulary_and_never_fertilizes():
    banned_orders = {"BUY_FERTILIZER"}
    allowed_orders = {"SELL", "BUY_LAND", "BUY_SEED", "BUY_ANIMAL", "BUY_PRODUCT",
                      "HIRE"}
    foreign = pm.validate_plan(json.loads(Path(FOREIGN_PLAN).read_text()))
    for plan in (pa.DEFAULT_PLAN, pa.ANIMAL_FREE_PLAN, foreign):
        layout = pa.FarmLayout(plan)
        s = state(day=8, hour=3, money=20000, unlocked=pa.QUADRANTS, seeds={},
                  shed={"WHEAT": 20})
        orders = pa.market_orders(plan, layout, s)
        assert market_types(orders) <= allowed_orders
        assert not market_types(orders) & banned_orders
        farmer, hands = pa.unit_actions(layout, s)
        verbs = {a[0] for a in [farmer, *hands] if a}
        assert verbs <= pa.UNIT_VERBS, verbs
        assert "FERTILIZE" not in pa.UNIT_VERBS  # PA3's program, not this one
        if not any(entry["count"] for entry in plan["animals"]):
            # An animal-free plan keeps PA1's boundary exactly: no purchases, no
            # structures, no animal verbs, even on a four-quadrant owned board.
            assert "BUY_ANIMAL" not in market_types(orders)
            assert not verbs & {"BUILD_COOP", "BUILD_PASTURE", "PLACE", "FEED",
                                "CARE", "COLLECT_FERTILIZER"}


def test_an_unfed_animal_is_fed_before_any_bonus_job():
    """FEED beats a bonus water, and it crosses the sector line to do it.

    Two measured failures are pinned here. At the water-due tier the crop backlog won
    on distance and the herd went unfed with wheat in the unit's hand; inside its own
    sector the board never even OFFERED the carrier the animal, because the sector
    fallback only ran when the unit's own corner had nothing to do (592 of 713 turns
    with an unfed animal and wheat in hand ended without a feed).
    """
    goose = LAYOUT.cells_of["GOOSE"][0]
    bonus_tile = next(t for crop in ("WHEAT", "CARROT", "STRAWBERRY")
                      for t in LAYOUT.tiles_of[crop]
                      if LAYOUT.quadrant_of[t] in ("SW", "SE"))
    hand_at = (bonus_tile[0], bonus_tile[1] + 1)
    s = state(day=6, hour=3, unlocked=pa.QUADRANTS, seeds={}, shed={"WHEAT": 5},
              farmer=(0, 0), hands=[hand_at],
              inventories=[{}, {"WHEAT": 4}],
              cells={goose: animal(day=6, fed_today=False),
                     bonus_tile: plant("WHEAT", 6, yield_units=1, age=3,
                                       consecutive_unfed=0, consecutive_unwatered=0)})
    trace: list = []
    farmer, hands = pa.unit_actions(LAYOUT, s, note=lambda *e: trace.append(e))
    assert hands[0] != ["WATER"] and hands[0] != ["PASS"]
    target = [e[3] for e in notes(trace, "act") if e[1] == 1][0]
    assert target == goose, target
    assert pa.sectors(LAYOUT, s, s.positions)[1] and goose not in set(
        pa.sectors(LAYOUT, s, s.positions)[1])


def test_a_fed_animal_yields_its_fertilizer_and_the_feed_chain_needs_wheat():
    """The carry chain, unit level: no wheat in hand, no FEED - a pickup instead."""
    goose = LAYOUT.cells_of["GOOSE"][0]
    s = state(day=6, hour=1, unlocked=("NW",), seeds={}, shed={"WHEAT": 9},
              farmer=SHED_TILES[0], cells={goose: animal(day=6, fed_today=False)})
    order = pa.fetch_plan(s, pa.read_herd(LAYOUT, s), pa.DEFAULT_POLICY)
    assert order == {0: ("WHEAT", pa.DEFAULT_POLICY.feed_batch)}
    action = pa.unit_actions(LAYOUT, s)[0]
    assert action == ["PICKUP", "WHEAT", pa.DEFAULT_POLICY.feed_batch]
    # With the wheat already in hand the same unit feeds instead of fetching again.
    carrying = state(day=6, hour=1, unlocked=("NW",), seeds={}, shed={"WHEAT": 9},
                     farmer=(4, 3), inventories=[{"WHEAT": 3}],
                     cells={goose: animal(day=6, fed_today=False)})
    assert pa.unit_actions(LAYOUT, carrying)[0] == ["FEED"]


def test_structures_are_built_only_on_reserved_owned_cells_never_on_a_crop():
    """Placement is irreversible, so the two halves of the claim are pinned here.

    A structure may only go on a cell the layout reserved for it (so it can never
    displace a crop), that cell has to be OWNED (a LOCKED build is a silent no-op),
    and a reserved cell is never planted - the layout's crop map excludes it.
    """
    for plan in (pa.DEFAULT_PLAN, pm.validate_plan(json.loads(Path(FOREIGN_PLAN).read_text()))):
        layout = pa.FarmLayout(plan)
        s = state(day=9, hour=4, money=20000, unlocked=("NW", "NE"), seeds={},
                  shed={})
        jobs = pa.board_jobs(layout, s, {})
        for _priority, tile, kind, _payload, _needs in jobs:
            if kind in ("BUILD_COOP", "BUILD_PASTURE"):
                assert tile in layout.structure_of, tile
                assert kind.startswith("BUILD_")
                assert s.unlocked_at(tile)
                assert layout.structure_of[tile] in pa.SPECIES
            if kind == "PLANT":
                assert tile in layout.crop_of
        # No jobs at all for structure cells in the LOCKED quadrants.
        for _priority, tile, _kind, _payload, _needs in jobs:
            if tile in layout.structure_of:
                assert s.unlocked_at(tile), tile


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
    """Zero crashes, zero escapes, and the order cap held on EVERY turn.

    The escape check is the one that matters for PA2: an animal unfed for two
    midnights leaves and never comes back, and the herd's tiles are the only record
    of it. The alive count is therefore monotone for the whole season.
    """
    for seed in SEEDS:
        trace: list = []
        ours, run = season(seed, load_agent(TAPE), trace)
        assert not notes(trace, "crash"), f"seed {seed}: the agent crashed into a pass"
        for step in run["steps"]:
            action = step[0].get("action") or {}
            assert len(action.get("market") or []) <= pm.MAX_ORDERS
        assert run["banks"][0] > 0, f"seed {seed}: the farm drained to {run['banks'][0]}"
        alive = []
        for step in run["steps"]:
            tiles = step[0]["observation"]["farms"][0]["tiles"]
            alive.append(sum(1 for row in tiles for cell in row
                             if isinstance(cell, dict) and cell.get("animal")))
        assert alive == sorted(alive), f"seed {seed}: an animal escaped"
        assert alive[-1] == 16, f"seed {seed}: {alive[-1]} animals at the end"
        # Zero escaped WORK: every tile op landed on a tile that was unlocked then.
        for index, step in enumerate(run["steps"]):
            action = step[0].get("action") or {}
            farm = step[0]["observation"]["farms"][0]
            units = [farm.get("farmer")] + list(farm.get("hands") or [])
            for order, unit in zip([action.get("farmer"), *(action.get("hands") or [])],
                                   units, strict=False):
                if not isinstance(order, list) or not order:
                    continue
                if order[0] not in ("PLANT", "WATER", "HARVEST", "DIG",
                                    "BUILD_COOP", "BUILD_PASTURE", "PLACE", "FEED",
                                    "CARE", "COLLECT_FERTILIZER"):
                    continue
                assert unit is not None, f"step {index}: a tile op without a unit"
                cell = pa.read_state({"step": index, "farms": [farm]}).cell(tuple(unit))
                assert cell != "LOCKED", f"step {index}: {order} on a LOCKED tile"


def test_the_herd_lands_fed_cared_and_on_its_own_reserved_cells():
    """The whole animal economy, asserted from the trace and the final board.

    The target profile is the CALIBRATED one (16 animals), not the 20-21 of the tape
    corpus: measured, 18 and 20 animals lose money on this board against the
    animal-free plan (-$3.6k / -$16.7k at 18, -$1.6k / -$15.6k at 20), because the
    products saturate, the wheat the farm would have sold feeds them, and the crop
    board loses the labour it needs for its own water and harvest. A test that
    demanded 18 alive would be asserting a configuration the gate measured as worse.
    """
    trace: list = []
    ours, run = season(0, load_agent(TAPE), trace)
    steps = run["steps"]
    layout = pa.FarmLayout(pa.DEFAULT_PLAN)
    reserved = set(layout.structure_of)

    # 1. Every animal the plan pays for is bought once, and placed on a reserved cell.
    buys = notes(trace, "animal")
    assert len(buys) == 16, len(buys)
    assert Counter(e[3] for e in buys) == Counter({"GOOSE": 8, "COW": 4, "SHEEP": 4})
    placements = notes(trace, "place")
    assert len(placements) == 16, len(placements)
    for _kind, tile, _day in placements:
        assert tile in reserved
        assert layout.structure_of[tile] in pa.SPECIES

    # 2. Structures: built only on reserved cells, never displacing a crop.
    built = notes(trace, "build_coop") + notes(trace, "build_pasture")
    assert len(built) == 16, len(built)
    for _kind, tile, _day in built:
        assert tile in reserved
    planted = {tile for tile, _crop, _day in plants_in(trace)}
    assert not planted & reserved

    # 3. The animal day: fed, cared, collected, and its product harvested, with the
    #    feed chain actually walking wheat to the tiles.
    assert len(notes(trace, "feed")) > 150
    assert len(notes(trace, "care")) > 100
    assert len(notes(trace, "pickup")) > 10
    assert len(notes(trace, "collect_fertilizer")) > 40
    products = Counter(e[3] for e in notes(trace, "sell"))
    assert {"EGG", "MILK", "WOOL", "FERTILIZER"} <= set(products)

    # 4. The profile: every animal alive by day 21 and none unfed at the end of a day
    #    after the herd has settled (one unfed day is survivable, two is not).
    alive = []
    for day in range(30):
        tiles = steps[min(day * TURNS + TURNS - 1, len(steps) - 1)][0]["observation"] \
            ["farms"][0]["tiles"]
        here = [cell for row in tiles for cell in row
                if isinstance(cell, dict) and cell.get("animal")]
        alive.append(len(here))
    assert alive[20] >= 10, alive
    assert alive[-1] == 16
    assert alive[25] == 16, alive
    late_unfed = []
    for day in range(21, 30):
        tiles = steps[min(day * TURNS + TURNS - 1, len(steps) - 1)][0]["observation"] \
            ["farms"][0]["tiles"]
        late_unfed.append(sum(1 for row in tiles for cell in row
                              if isinstance(cell, dict) and cell.get("animal")
                              and not cell.get("fed_today")))
    assert sum(late_unfed) <= 4 * len(late_unfed) // 10, late_unfed


def test_full_season_lands_the_plan_inside_the_ownership_boundaries():
    trace: list = []
    ours, run = season(0, load_agent(TAPE), trace)
    layout = pa.FarmLayout(pa.DEFAULT_PLAN)
    steps = run["steps"]

    # 1. Land: bought in engine order, never before the gated day, one per turn.
    #    A retry of a purchase the wallet could not cover is not a second buy: the
    #    plan's day is a FLOOR, and the engine rejects what the cash cannot pay.
    buys = []
    for step in steps[1:]:
        action = step[0].get("action") or {}
        if any(o and o[0] == "BUY_LAND" for o in (action.get("market") or [])):
            obs_now = step[0]["observation"]
            unlocked = len(obs_now["farms"][0]["unlocked_quadrants"])
            if not buys or buys[-1][1] != unlocked:
                buys.append((obs_now["day"], unlocked))
    assert len(buys) == 3, buys
    for index, (day, quadrants) in enumerate(buys):
        assert day >= pa.DEFAULT_PLAN["land"]["buy_days"][index], (index, day)
        assert day <= pa.DEFAULT_PLAN["land"]["buy_days"][index] + 2, (index, day)
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

    # 5. The animal work is real work, and it is the plan's work: the herd is bought,
    #    built for and placed inside the plan's own cells.
    assert len(notes(trace, "animal")) == sum(entry["count"]
                                             for entry in pa.DEFAULT_PLAN["animals"])
    assert len(notes(trace, "place")) == len(notes(trace, "animal"))
    assert not {t[1] for t in notes(trace, "place")} - set(layout.structure_of)
    assert not notes(trace, "crash")


def test_bank_trajectory_beats_param_agent_and_stays_positive_early():
    """The PA1+PA2 gate: ``param_agent`` drains to ~$0 by day 9-14; we must not.

    Same plan on both sides, so the difference is the board and the market layer
    rather than the plan vector - and with PA2 that plan includes the herd, which the
    consumer board feeds from the shed without the carry chain. Absolute banks are
    asserted, not just ordering.
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


def test_the_joint_capex_schedule_keeps_the_bank_positive_through_days_9_to_14():
    """The PA2 trajectory gate: a herd bought INTO the land windows must not break it.

    The animal slot is funded only above the WHOLE price of the next quadrant inside
    its accrual window and above the hands' bill for the turn (``animal_floor`` +
    ``hire_bill``), which is the difference between the animals postponing the
    day-13 quadrant and the animals costing its production. Measured here for every
    day of the window, on the two seeds the PA1 gate asserts, against the tape.
    """
    tape = load_agent(TAPE)
    for seed in (0, 1):
        trace: list = []
        ours, run = season(seed, tape, trace)
        steps = run["steps"]
        floors = {day: min(day_money(steps, 0, hour) for hour in range(24))
                  for day in range(9, 15)}
        for day, cash in floors.items():
            assert cash > 0, f"seed {seed} day {day}: the bank touched {cash}"
        # The land is bought in engine order and never before its plan day: the herd's
        # capex reserves the quadrant's whole price, so the buy is never eaten - only
        # postponed by the cash the same window still had to fund (measured: one day
        # against the tape, and buying the geese later instead costs $5.8k).
        acq = []
        for step in steps[1:]:
            action = step[0].get("action") or {}
            if any(o and o[0] == "BUY_LAND" for o in (action.get("market") or [])):
                obs_now = step[0]["observation"]
                unlocked = len(obs_now["farms"][0]["unlocked_quadrants"])
                if not acq or acq[-1][1] != unlocked:
                    acq.append((obs_now["day"], unlocked))
        assert [q for _day, q in acq] == [2, 3, 4], acq
        for (day, _q), plan_day in zip(acq, pa.DEFAULT_PLAN["land"]["buy_days"], strict=True):
            assert plan_day <= day <= plan_day + 2, (day, plan_day)
        assert notes(trace, "animal"), "the herd was never funded"


def test_the_animal_economy_beats_the_animal_free_plan():
    """The PA2 improvement gate, measured against PA1's own vector on this board.

    ``ANIMAL_FREE_PLAN`` is what PA1 shipped (96 crop tiles, zero animals), so a
    paired season against it is the animal economy's marginal value with the board
    implementation and the policy held constant. The gate wants an IMPROVEMENT, so
    the assertion is on the sign of the margin, and the banks are printed through
    the failure message: an absolute number beats a ratio a reader has to recompute.
    """
    control = pa.make_agent(plan=pa.ANIMAL_FREE_PLAN)
    mines, theirs = [], []
    for seed in (0, 1, 2):
        mine = run_episode_trace(pa.make_agent(), control, seed)["banks"]
        other = run_episode_trace(control, pa.make_agent(), seed)["banks"]
        mines += [mine[0], other[1]]
        theirs += [mine[1], other[0]]
    margins = [a - b for a, b in zip(mines, theirs, strict=True)]
    assert all(margin > 0 for margin in margins), \
        f"lost to the animal-free plan: banks {[round(m) for m in mines]} vs " \
        f"{[round(t) for t in theirs]}"
    assert sum(margins) / len(margins) > 5_000, margins
