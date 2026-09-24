"""Tests for the plan consumer (`v10`-style: the invariants, not the season).

Every test here exists because getting the corresponding rule wrong is either
silent (the engine no-ops invalid actions) or catastrophic (the $1,000 land cliff).
None of them needs an episode to run, which is the point of keeping the invariants
as pure functions.
"""

import pytest

from kaggriculture.agents.param_agent import (
    MAX_ORDERS,
    Agent,
    PlanError,
    cash_floor,
    hand_cost,
    hand_target_for_day,
    harvest_ready,
    held_of,
    make_agent,
    plan_market_orders,
    validate_plan,
    water_window_ok,
)


def plan_v3(**overrides):
    plan = {
        "schema": "kaggriculture-plan/3",
        "land": {"quadrants": 3, "buy_days": [1, 6]},
        "hands": {"start": 4, "ramp_step": 2, "daily_target": 12},
        "crops": [
            {"crop": "WHEAT", "tiles": 30, "plant_days": [0, 8], "fertilize_tiles": 0},
            {"crop": "CARROT", "tiles": 18, "plant_days": [1], "fertilize_tiles": 4},
            {"crop": "TOMATO", "tiles": 0, "plant_days": [4], "fertilize_tiles": 0},
            {"crop": "STRAWBERRY", "tiles": 19, "plant_days": [1], "fertilize_tiles": 12},
            {"crop": "MELON", "tiles": 5, "plant_days": [0], "fertilize_tiles": 0},
        ],
        "animals": [
            {"animal": "COW", "count": 2, "buy_days": [3, 4]},
            {"animal": "SHEEP", "count": 0, "buy_days": []},
            {"animal": "GOOSE", "count": 1, "buy_days": [8]},
        ],
        "market": {"sell_chunk_cap": 0, "sell_from_day": 12, "seed_reserve": 4},
    }
    plan.update(overrides)
    return plan


def ctx(**overrides):
    base = {
        "step": 200, "day": 8, "money": 5000, "hands_now": 0, "hires_today": 0,
        "unlocked_quadrants": 3, "seeds": {}, "shed_free": 100, "bought": {}, "held": {},
    }
    base.update(overrides)
    return base


# --- the version bump is the contract --------------------------------------- #

@pytest.mark.parametrize("old_version", ["kaggriculture-plan/1", "kaggriculture-plan/2"])
def test_an_older_revision_is_rejected_not_misread(old_version):
    # Revision 1 had ramp_day / plant_first_day / replant_every / a boolean fertilize
    # and no sell cap sentinel; revision 2 had all four fixed but no global tile
    # budget, which let an LLM plan ask for 142 cells on 75 unlocked ones. Accepting
    # either silently would misread fields or admit an impossible farm.
    with pytest.raises(PlanError, match="schema must be"):
        validate_plan(plan_v3(schema=old_version))


def test_unknown_fields_are_an_error():
    plan = plan_v3()
    plan["extra_knob"] = 1
    with pytest.raises(PlanError, match="unknown top-level fields"):
        validate_plan(plan)


def test_a_missing_top_level_field_is_an_error():
    plan = plan_v3()
    del plan["market"]
    with pytest.raises(PlanError, match="missing top-level fields"):
        validate_plan(plan)


def test_buy_days_must_hold_one_day_per_paid_quadrant():
    plan = plan_v3(land={"quadrants": 3, "buy_days": [1]})
    with pytest.raises(PlanError, match="one day per paid quadrant"):
        validate_plan(plan)


def test_out_of_range_values_are_rejected():
    plan = plan_v3(market={"sell_chunk_cap": 0, "sell_from_day": 99, "seed_reserve": 4})
    with pytest.raises(PlanError, match="sell_from_day out of range"):
        validate_plan(plan)


def test_fertilize_tiles_cannot_exceed_tiles():
    plan = plan_v3()
    plan["crops"][1]["fertilize_tiles"] = 21  # 21 carrot tiles fertilized, 20 exist
    with pytest.raises(PlanError, match="fertilize_tiles out of range"):
        validate_plan(plan)


def test_a_plan_that_needs_more_cells_than_are_unlocked_is_rejected():
    # Global invariant 9, and it exists because of a real LLM plan: 125 crop tiles
    # plus 17 animal structures = 142 cells on 75 unlocked ones, which passed EVERY
    # per-field bound. Individual ranges cannot see a farm that does not fit.
    plan = plan_v3()
    plan["crops"][0]["tiles"] = 100  # WHEAT 100 + 20 + 0 + 33 + 12 = 165 crops
    with pytest.raises(PlanError, match="unlock only"):
        validate_plan(plan)


def test_the_plan_that_fits_exactly_on_three_quadrants_is_accepted():
    # gemini's plan for the same prompt: 58 crop tiles + 17 structures = 75/75.
    plan = plan_v3(
        crops=[
            {"crop": "WHEAT", "tiles": 24, "plant_days": [0], "fertilize_tiles": 0},
            {"crop": "CARROT", "tiles": 8, "plant_days": [0], "fertilize_tiles": 0},
            {"crop": "TOMATO", "tiles": 0, "plant_days": [4], "fertilize_tiles": 0},
            {"crop": "STRAWBERRY", "tiles": 22, "plant_days": [1], "fertilize_tiles": 22},
            {"crop": "MELON", "tiles": 4, "plant_days": [0], "fertilize_tiles": 0},
        ],
        animals=[
            {"animal": "COW", "count": 9, "buy_days": list(range(3, 12))},
            {"animal": "SHEEP", "count": 5, "buy_days": list(range(12, 17))},
            {"animal": "GOOSE", "count": 3, "buy_days": list(range(17, 20))},
        ],
    )
    assert validate_plan(plan) is not None


def test_a_duplicate_crop_entry_is_rejected():
    plan = plan_v3()
    plan["crops"].append(dict(plan["crops"][0]))
    with pytest.raises(PlanError, match="duplicate crop"):
        validate_plan(plan)


def test_every_crop_must_be_listed_exactly_once():
    plan = plan_v3()
    plan["crops"] = [c for c in plan["crops"] if c["crop"] != "TOMATO"]
    with pytest.raises(PlanError, match="every crop exactly once"):
        validate_plan(plan)


def test_buy_days_must_hold_one_day_per_animal():
    plan = plan_v3()
    plan["animals"][0]["buy_days"] = [3]  # count is 2
    with pytest.raises(PlanError, match="one day per animal"):
        validate_plan(plan)


def test_a_valid_plan_passes_and_make_agent_returns_a_callable():
    assert validate_plan(plan_v3()) is not None
    assert callable(make_agent(plan_v3()))
    assert isinstance(Agent(plan_v3()), Agent)


# --- invariant 1: the land cliff -------------------------------------------- #

def test_cash_floor_holds_the_next_land_price_before_the_cliff():
    # The tomato overlay missed BUY_LAND by $114 and lost a quadrant permanently.
    plan = plan_v3()
    assert cash_floor(plan, step=100, unlocked_quadrants=1, money=9999) == 1000 + 100


def test_cash_floor_is_zero_after_the_cliff_and_when_fully_paid():
    plan = plan_v3()
    assert cash_floor(plan, step=200, unlocked_quadrants=1, money=9999) == 0
    assert cash_floor(plan, step=100, unlocked_quadrants=3, money=9999) == 0


def test_land_is_not_bought_when_the_floor_would_be_broken():
    plan = plan_v3()
    # Before the cliff with one quadrant: $1,500 leaves only $400 spendable, and the
    # needed quadrant costs $1,000. Buying would repeat the tomato failure.
    orders = plan_market_orders(plan, ctx(step=100, day=1, money=1500, unlocked_quadrants=1))
    assert ["BUY_LAND"] not in orders


def test_land_is_bought_once_the_floor_leaves_it_funded():
    plan = plan_v3()
    orders = plan_market_orders(plan, ctx(step=100, day=1, money=2500, unlocked_quadrants=1))
    assert orders[0] == ["BUY_LAND"]


def test_land_is_not_bought_twice_for_the_same_quadrant():
    plan = plan_v3()
    # paid = quadrants - 1 = 2 = len(buy_days): every quadrant the plan wants is owned.
    orders = plan_market_orders(plan, ctx(step=200, day=8, money=9000, unlocked_quadrants=3))
    assert ["BUY_LAND"] not in orders


def test_the_next_pending_quadrant_is_bought_once_its_day_arrives():
    plan = plan_v3()
    # paid = 1: the day-6 quadrant is still pending, so day 8 buys it.
    orders = plan_market_orders(plan, ctx(step=200, day=8, money=9000, unlocked_quadrants=2))
    assert ["BUY_LAND"] in orders


# --- invariant 3: the watering window, and the melon conflation ------------- #

def test_melon_watering_window_is_6_to_12_not_its_harvest_window():
    # The project once called both windows "the melon window". Watering pays on days
    # 6-12; the harvest window is days 10-12.
    assert not water_window_ok("MELON", age_days=5)
    assert water_window_ok("MELON", age_days=6)
    assert water_window_ok("MELON", age_days=12)
    assert not water_window_ok("MELON", age_days=13)


def test_wheat_watering_window_is_days_2_to_4():
    assert not water_window_ok("WHEAT", age_days=1)
    assert water_window_ok("WHEAT", age_days=2)
    assert water_window_ok("WHEAT", age_days=4)
    assert not water_window_ok("WHEAT", age_days=5)


# --- invariant 5: harvest only what is ready -------------------------------- #

def test_harvest_refuses_an_immature_ongoing_crop():
    tile = {"kind": "PLANT", "crop": "TOMATO", "planted_day": 5, "yield_units": 3}
    assert not harvest_ready(tile, day=5)  # tomato first yields on day 8
    assert harvest_ready(tile, day=13)


def test_harvest_refuses_a_tile_with_no_yield():
    tile = {"kind": "PLANT", "crop": "WHEAT", "planted_day": 0, "yield_units": 0}
    assert not harvest_ready(tile, day=10)


def test_harvest_accepts_an_animal_holding_a_product():
    assert harvest_ready({"kind": "COOP", "animal": "GOOSE", "yield_units": 2}, day=9)
    assert not harvest_ready({"kind": "COOP", "animal": "GOOSE", "yield_units": 0}, day=9)


def test_harvest_tolerates_a_malformed_tile():
    assert not harvest_ready(None, day=3)
    assert not harvest_ready({"kind": "PLANT"}, day=3)


# --- invariant 4 and the market ordering ------------------------------------ #

def test_never_more_than_the_engine_reads():
    plan = plan_v3()
    orders = plan_market_orders(plan, ctx(
        money=500000, hands_now=0, seeds={},
        held=dict.fromkeys(
            ("WHEAT", "STRAWBERRY", "MILK", "WOOL", "EGG", "CARROT", "TOMATO", "MELON"),
            400,
        ),
    ))
    assert len(orders) <= MAX_ORDERS


def test_sells_come_last_so_they_do_not_starve_the_buys():
    plan = plan_v3()
    orders = plan_market_orders(plan, ctx(money=500000, held={"WHEAT": 50}))
    verbs = [o[0] for o in orders]
    assert "SELL" in verbs
    assert verbs.index("SELL") > 0
    assert all(v != "SELL" for v in verbs[:verbs.index("SELL")])


def test_no_throttling_before_the_animals_are_bought():
    # The sell-chunk overlay went 0W-24L because it throttled before day 12 and
    # defunded the farm.
    plan = plan_v3(market={"sell_chunk_cap": 5, "sell_from_day": 12, "seed_reserve": 4})
    orders = plan_market_orders(plan, ctx(day=4, held={"WHEAT": 50}))
    assert ["SELL", "WHEAT", 50] in orders


def test_the_cap_applies_after_sell_from_day():
    plan = plan_v3(market={"sell_chunk_cap": 5, "sell_from_day": 12, "seed_reserve": 4})
    orders = plan_market_orders(plan, ctx(day=20, held={"WHEAT": 50}))
    assert ["SELL", "WHEAT", 5] in orders


def test_a_zero_cap_means_no_throttle_at_any_day():
    plan = plan_v3(market={"sell_chunk_cap": 0, "sell_from_day": 12, "seed_reserve": 4})
    orders = plan_market_orders(plan, ctx(day=25, held={"WHEAT": 50}))
    assert ["SELL", "WHEAT", 50] in orders


def test_seeds_are_topped_up_to_the_reserve_only_for_planted_crops():
    plan = plan_v3()
    orders = plan_market_orders(plan, ctx(money=5000, seeds={}, day=0))
    seeded = {o[1] for o in orders if o[0] == "BUY_SEED"}
    assert "TOMATO" not in seeded  # tiles = 0: nothing to plant, nothing to buy
    assert {"WHEAT", "CARROT", "STRAWBERRY", "MELON"} <= seeded


def test_seed_buying_stops_once_the_reserve_is_met():
    plan = plan_v3()
    orders = plan_market_orders(plan, ctx(money=5000, seeds={"WHEAT": 4}, day=0))
    assert not [o for o in orders if o[0] == "BUY_SEED" and o[1] == "WHEAT"]


# --- invariant 8: the shed, and the animal roster ---------------------------- #

def test_animals_are_bought_one_per_listed_day():
    plan = plan_v3()
    early = plan_market_orders(plan, ctx(day=3, money=9000, bought={}))
    assert [o for o in early if o[0] == "BUY_ANIMAL"] == [["BUY_ANIMAL", "COW", 1]]
    later = plan_market_orders(plan, ctx(day=8, money=9000, bought={"COW": 2}))
    assert [o for o in later if o[0] == "BUY_ANIMAL"] == [["BUY_ANIMAL", "GOOSE", 1]]


def test_no_animals_are_bought_without_shed_room():
    plan = plan_v3()
    orders = plan_market_orders(plan, ctx(day=8, money=9000, shed_free=0))
    assert not [o for o in orders if o[0] == "BUY_ANIMAL"]


def test_the_roster_ramps_and_is_capped_by_the_target():
    plan = plan_v3()
    assert hand_target_for_day(plan, 0) == 4
    assert hand_target_for_day(plan, 1) == 6
    assert hand_target_for_day(plan, 4) == 12
    assert hand_target_for_day(plan, 29) == 12  # capped, never 62


def test_hires_are_issued_to_close_the_gap_and_stop_when_funded_out():
    plan = plan_v3()
    orders = plan_market_orders(plan, ctx(day=2, money=100000, hands_now=4))
    assert [o for o in orders if o[0] == "HIRE"]  # gap exists, money is ample
    broke = plan_market_orders(plan, ctx(day=2, money=0, hands_now=0))
    assert not [o for o in broke if o[0] == "HIRE"]


def test_the_hire_price_matches_the_engines_fibonacci_and_the_12_hand_roster():
    assert [hand_cost(n) for n in range(7)] == [1, 1, 2, 3, 5, 8, 13]
    # The engine's own comment: a 12-hand roster costs 376 per day.
    assert sum(hand_cost(n) for n in range(12)) == 376


# --- the orchestrator -------------------------------------------------------- #

def test_held_sums_the_shed_and_every_workers_pockets():
    obs = {
        "player": 0,
        "private": {"shed": {"WHEAT": 10}, "inventories": [{"WHEAT": 2}, {"MILK": 3}]},
    }
    assert held_of(obs) == {"WHEAT": 12, "MILK": 3}


def test_the_agent_resets_its_roster_tracker_at_step_zero():
    agent = Agent(plan_v3())
    agent._bought["COW"] = 2
    obs = {"step": 0, "player": 0, "farms": [{"money": 3000}], "private": {}}
    agent(obs)
    assert agent._bought["COW"] == 0


def test_the_agent_returns_the_three_keys_the_engine_reads():
    agent = Agent(plan_v3())
    obs = {"step": 10, "player": 0, "farms": [{"money": 3000}], "private": {}}
    action = agent(obs)
    assert set(action) == {"farmer", "hands", "market"}
    assert action["farmer"] == ["PASS"]
    assert len(action["market"]) <= MAX_ORDERS
