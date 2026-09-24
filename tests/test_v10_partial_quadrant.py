"""Unit tests for the bounded 4th-quadrant overlay (v10_partial_quadrant).

The public path needs a full season to exercise, so most tests drive the pure
helpers and the ``QuadrantOverlay`` transform with synthetic observations. The two
season-level tests are the ones that matter most:

  * ``test_zero_extra_hands_is_byte_identical_to_tape`` proves the overlay is a real
    pass-through at ``extra_hands=0``: the tape's action stream, turn for turn, is
    unchanged. That is the evidence the tape is not disturbed.
  * ``test_engine_confirms_hire_appears_next_turn`` pins the alignment the ownership
    bookkeeping depends on: HIRE resolves in the market phase, so the hand is
    visible one turn later. The module docstring says "verified empirically rather
    than assumed"; this test is that verification, kept executable.

The rest pin the two engine traps: atomic PLANT validation (our plants must fit the
spare seed stock) and the SE-only job board (water inside the window AND on the
planting day, harvest at ``max_yield_day``).
"""

from pathlib import Path
from typing import Any

import pytest

from kaggriculture.agents import v10_partial_quadrant as pq
from kaggriculture.loading import load_agent
from kaggriculture.runner import run_episode_trace

BOARD = 10
SE_TILE = (6, 5)  # closest SE non-shed tile: distance 1 from the shed corner


# --------------------------------------------------------------------------- #
# Synthetic observation builders.
# --------------------------------------------------------------------------- #

def _empty_tiles() -> Any:
    return [[None for _ in range(BOARD)] for _ in range(BOARD)]


def make_obs(step=50, day=2, hour=2, hands=(), unlocked=("NW", "NE", "SW", "SE"),
             money=20000.0, tiles=None, seeds=None, hires_today=0, player=0):
    farm = {
        "money": money,
        "hands": [list(p) for p in hands],
        "farmer": [4, 4],
        "unlocked_quadrants": list(unlocked),
        "hires_today": hires_today,
        "tiles": tiles if tiles is not None else _empty_tiles(),
    }
    return {
        "step": step,
        "day": day,
        "hour": hour,
        "player": player,
        "farms": [farm],
        "private": {"seeds": dict(seeds or {}), "shed": {}, "inventories": []},
        "market": {"prices": {}, "inventory": {}},
    }


def _plant(tiles, pos, crop="WHEAT", planted_day=0, watered_today=False,
           consecutive_unwatered=1, yield_units=1):
    x, y = pos
    tiles[y][x] = {
        "kind": "PLANT",
        "crop": crop,
        "planted_day": planted_day,
        "watered_today": watered_today,
        "consecutive_unwatered": consecutive_unwatered,
        "yield_units": yield_units,
    }


def _overlay(extra_hands=1, tiles=None, base_action=None, **kwargs):
    base_action = base_action or {"farmer": ["PASS"], "hands": [], "market": []}
    return pq.QuadrantOverlay(
        extra_hands=extra_hands,
        quadrant_tiles=kwargs.pop("quadrant_tiles", 2),
        base_agent=lambda obs: base_action,
        **kwargs,
    )


# --------------------------------------------------------------------------- #
# Geometry and crop helpers.
# --------------------------------------------------------------------------- #

def test_designated_tiles_are_in_the_se_quadrant_and_skip_the_shed():
    tiles = pq.designated_tiles(6)
    assert len(tiles) == 6
    assert len(set(tiles)) == 6
    shed = set(pq.shed_access_tiles())
    for (x, y) in tiles:
        assert pq.quadrant_of(x, y) == "SE"
        assert (x, y) not in shed
        assert x >= 5 and y >= 5


def test_designated_tiles_is_deterministic_and_prefix_stable():
    first = pq.designated_tiles(4)
    assert pq.designated_tiles(4) == first
    assert pq.designated_tiles(2) == first[:2]  # a prefix: tile identity is stable


def test_water_window_matches_the_engine_table():
    # WHEAT: max_yield_day 4 -> [(4+1)//2, 4] = [2, 4]
    assert pq.water_window("WHEAT") == (2, 4)
    assert pq.water_window("CARROT") == (2, 3)


def test_needs_water_on_the_planting_day_even_outside_the_growth_window():
    # The engine starts consecutive_unwatered at 1 and weeds a plant after two
    # unwatered midnights, so a crop not watered on day 0 is a weed by day 1.
    tile = {"kind": "PLANT", "crop": "WHEAT", "planted_day": 0,
            "watered_today": False, "consecutive_unwatered": 1, "yield_units": 1}
    assert pq.needs_water(tile, 0, "WHEAT") is True


def test_needs_water_inside_the_window_counts_the_bonus():
    tile = {"kind": "PLANT", "crop": "WHEAT", "planted_day": 0,
            "watered_today": False, "consecutive_unwatered": 0, "yield_units": 1}
    assert pq.needs_water(tile, 2, "WHEAT") is True  # age 2 == window start


def test_needs_water_skips_the_middle_day_when_the_weed_clock_is_clear():
    # After a day-0 watering the counter is 0, and age 1 is outside [2, 4]: the
    # minimal schedule is water day 0, skip day 1, water days 2-4.
    tile = {"kind": "PLANT", "crop": "WHEAT", "planted_day": 0,
            "watered_today": False, "consecutive_unwatered": 0, "yield_units": 1}
    assert pq.needs_water(tile, 1, "WHEAT") is False


def test_needs_water_is_false_when_already_watered_today():
    tile = {"kind": "PLANT", "crop": "WHEAT", "planted_day": 0,
            "watered_today": True, "consecutive_unwatered": 1, "yield_units": 1}
    assert pq.needs_water(tile, 2, "WHEAT") is False


def test_harvest_due_only_at_the_maturity_day():
    tile = {"kind": "PLANT", "crop": "WHEAT", "planted_day": 0,
            "yield_units": 2, "watered_today": True}
    assert pq.harvest_due(tile, 2, "WHEAT") is False  # early harvest throws yield away
    assert pq.harvest_due(tile, 4, "WHEAT") is True


def test_harvest_due_fires_early_when_yield_is_capped():
    tile = {"kind": "PLANT", "crop": "WHEAT", "planted_day": 0,
            "yield_units": 6, "watered_today": True}  # max_yield == 6
    assert pq.harvest_due(tile, 3, "WHEAT") is True


def test_tile_job_priorities_order_the_board():
    tiles = _empty_tiles()
    _plant(tiles, (6, 5), planted_day=0, yield_units=4)               # harvest
    # Age 1 at day 4 is outside the window but the weed clock is running: urgent.
    _plant(tiles, (7, 5), planted_day=3, consecutive_unwatered=1,
           yield_units=1)
    tiles[5][8] = {"kind": "WEED"}
    harvest = pq.tile_job(tiles[5][6], 4, "WHEAT", 0)
    urgent = pq.tile_job(tiles[5][7], 4, "WHEAT", 0)
    weed = pq.tile_job(tiles[5][8], 4, "WHEAT", 0)
    plant = pq.tile_job(None, 4, "WHEAT", 3)
    assert harvest is not None and harvest[0] == "HARVEST"
    assert urgent is not None and urgent[0] == "WATER"
    assert weed is not None and weed[0] == "DIG"
    assert plant is not None and plant[0] == "PLANT"
    assert pq.tile_job(None, 4, "WHEAT", 0) is None


def test_step_toward_shortens_the_manhattan_distance():
    assert pq.step_toward((0, 0), (2, 0)) == "EAST"
    assert pq.step_toward((2, 0), (0, 0)) == "WEST"
    assert pq.step_toward((0, 0), (0, 2)) == "SOUTH"
    assert pq.step_toward((0, 2), (0, 0)) == "NORTH"
    assert pq.step_toward((3, 3), (3, 3)) is None


def test_fib_hire_cost_matches_the_engine_indexing():
    # _fib(0)=1, _fib(1)=1, _fib(2)=2, _fib(3)=3, _fib(4)=5, ...
    assert [pq.fib_hire_cost(n) for n in range(5)] == [1, 1, 2, 3, 5]


# --------------------------------------------------------------------------- #
# Atomic PLANT budget.
# --------------------------------------------------------------------------- #

def test_tape_plant_demand_excludes_owned_slots_and_other_crops():
    overlay = _overlay()
    overlay.owned = {1}  # hand index 1 is ours
    action = {
        "farmer": ["PLANT", "WHEAT"],       # counts
        "hands": [["PLANT", "WHEAT"],       # index 0 counts
                  ["PLANT", "WHEAT"],       # index 1 -- ours, excluded
                  ["PLANT", "CARROT"]],     # other crop
        "market": [],
    }
    assert overlay._tape_plant_demand(action) == 2


def test_plant_budget_subtracts_the_tape_demand_and_never_goes_negative():
    overlay = _overlay()
    action = {"farmer": ["PLANT", "WHEAT"], "hands": [], "market": []}
    assert overlay._plant_budget(action, {"WHEAT": 5}) == 4
    assert overlay._plant_budget(action, {"WHEAT": 0}) == 0
    assert overlay._plant_budget(action, {}) == 0


def test_overlay_never_asks_for_more_wheat_plants_than_the_spare_seeds():
    tiles = _empty_tiles()
    action = {"farmer": ["PLANT", "WHEAT"], "hands": [["PASS"]], "market": []}
    overlay = _overlay(extra_hands=1, quadrant_tiles=1, base_action=action)
    overlay.owned = {0}
    obs = make_obs(step=50, day=2, hour=2, hands=[SE_TILE], tiles=tiles,
                   seeds={"WHEAT": 1})  # 1 seed, farmer takes it -> 0 spare
    out = overlay.augment(obs, action)
    assert out["hands"][0] != ["PLANT", "WHEAT"]  # must not trip the atomic void


# --------------------------------------------------------------------------- #
# Ownership bookkeeping.
# --------------------------------------------------------------------------- #

def test_pending_hire_resolves_to_the_last_appended_hand():
    overlay = _overlay()
    overlay.pending = 1
    overlay._resolve_pending(roster_len=5)
    assert overlay.owned == {4}
    assert overlay.pending == 0


def test_failed_hire_allows_a_retry_instead_of_claiming_a_tape_hand():
    overlay = _overlay()
    overlay.pending = 2
    overlay.hired_today = True
    overlay._resolve_pending(roster_len=1)  # fewer hands than we asked for
    assert overlay.owned == set()
    assert overlay.hired_today is False  # retry later today


def test_owned_index_keeps_tracking_after_later_tape_hires():
    # Own hand at index 4; the tape later appended hands 5 and 6. Their indices do
    # not shift, so our hand is still 4 and only 4 is rewritten.
    tiles = _empty_tiles()
    _plant(tiles, SE_TILE, planted_day=0, consecutive_unwatered=1, yield_units=1)
    base = {"farmer": ["PASS"], "hands": [["PASS"]] * 7, "market": []}
    overlay = _overlay(extra_hands=1, quadrant_tiles=1, base_action=base)
    overlay.owned = {4}
    obs = make_obs(step=50, day=2, hour=2,
                   hands=[(0, 0)] * 4 + [SE_TILE, (0, 0), (0, 0)], tiles=tiles)
    out = overlay.augment(obs, base)
    assert out["hands"][4] == ["WATER"]
    assert out["hands"][5] == ["PASS"]
    assert out["hands"][6] == ["PASS"]


def test_only_owned_hand_actions_are_rewritten_and_input_is_not_mutated():
    tiles = _empty_tiles()
    _plant(tiles, SE_TILE, planted_day=0, consecutive_unwatered=1, yield_units=1)
    base = {"farmer": ["PASS"], "hands": [["WATER"], ["HARVEST"], ["PASS"]],
            "market": [["SELL", "WHEAT", 3]]}
    overlay = _overlay(extra_hands=1, quadrant_tiles=1, base_action=base)
    overlay.owned = {2}
    obs = make_obs(step=50, day=2, hour=2, hands=[(0, 0), (0, 0), SE_TILE], tiles=tiles)
    out = overlay.augment(obs, base)
    assert out["farmer"] == base["farmer"]
    assert out["hands"][0] == ["WATER"]      # tape hand, untouched
    assert out["hands"][1] == ["HARVEST"]    # tape hand, untouched
    assert out["hands"][2] == ["WATER"]      # ours
    assert out["market"] == [["SELL", "WHEAT", 3]]  # tape sell preserved in place
    # The overlay must not mutate the dict the tape handed it.
    assert base["hands"][2] == ["PASS"]
    assert base["market"] == [["SELL", "WHEAT", 3]]


def test_hand_away_from_a_plant_job_walks_instead_of_planting_from_a_distance():
    # Regression: emitting PLANT while not on the tile is a silent no-op, so the
    # hand never moves. It must shorten the distance instead.
    tiles = _empty_tiles()
    base = {"farmer": ["PASS"], "hands": [["PASS"]], "market": []}
    overlay = _overlay(extra_hands=1, quadrant_tiles=1, base_action=base)
    overlay.owned = {0}
    obs = make_obs(step=50, day=2, hour=2, hands=[(5, 5)], tiles=tiles,
                   seeds={"WHEAT": 5})
    out = overlay.augment(obs, base)
    assert out["hands"][0] in (["EAST"], ["SOUTH"])  # (6,5) is east/south of (5,5)
    assert out["hands"][0][0] != "PLANT"


def test_extra_hand_plants_wheat_on_an_empty_fourth_quadrant_tile():
    tiles = _empty_tiles()
    base = {"farmer": ["PASS"], "hands": [["PASS"]], "market": []}
    overlay = _overlay(extra_hands=1, quadrant_tiles=1, base_action=base)
    overlay.owned = {0}
    obs = make_obs(step=50, day=2, hour=2, hands=[SE_TILE], tiles=tiles,
                   seeds={"WHEAT": 5})
    out = overlay.augment(obs, base)
    assert out["hands"][0] == ["PLANT", "WHEAT"]


# --------------------------------------------------------------------------- #
# Land purchase gating.
# --------------------------------------------------------------------------- #

def test_land_is_bought_only_with_three_quadrants_and_enough_cash():
    base = {"farmer": ["PASS"], "hands": [], "market": []}
    overlay = _overlay(base_action=base, land_from_day=10)
    obs = make_obs(step=266, day=11, hour=2, unlocked=("NW", "NE", "SW"),
                   money=pq.SE_PRICE + 2000)
    out = overlay.augment(obs, base)
    assert ["BUY_LAND"] in out["market"]


def test_land_is_not_bought_before_the_tape_finishes_its_own_purchases():
    base = {"farmer": ["PASS"], "hands": [], "market": []}
    overlay = _overlay(base_action=base, land_from_day=10)
    obs = make_obs(step=151, day=6, hour=2, unlocked=("NW", "NE"), money=50000)
    out = overlay.augment(obs, base)
    assert ["BUY_LAND"] not in out["market"]


def test_land_is_not_bought_below_the_reserve():
    base = {"farmer": ["PASS"], "hands": [], "market": []}
    overlay = _overlay(base_action=base, land_from_day=10, land_reserve=2000)
    obs = make_obs(step=266, day=11, hour=2, unlocked=("NW", "NE", "SW"),
                   money=pq.SE_PRICE + 1999)
    out = overlay.augment(obs, base)
    assert ["BUY_LAND"] not in out["market"]


def test_land_is_not_bought_before_land_from_day():
    base = {"farmer": ["PASS"], "hands": [], "market": []}
    overlay = _overlay(base_action=base, land_from_day=15)
    obs = make_obs(step=120, day=5, hour=0, unlocked=("NW", "NE", "SW"),
                   money=50000)
    out = overlay.augment(obs, base)
    assert ["BUY_LAND"] not in out["market"]


# --------------------------------------------------------------------------- #
# Order budget.
# --------------------------------------------------------------------------- #

def test_market_stays_within_the_ten_order_cap():
    base = {"farmer": ["PASS"], "hands": [],
            "market": [["SELL", "WHEAT", 1]] * 9}
    overlay = _overlay(base_action=base, land_from_day=10)
    obs = make_obs(step=266, day=11, hour=2, unlocked=("NW", "NE", "SW"),
                   money=50000)
    out = overlay.augment(obs, base)
    assert len(out["market"]) == 10
    assert ["BUY_LAND"] in out["market"]


def test_no_order_is_appended_when_the_tape_turn_is_already_full():
    base = {"farmer": ["PASS"], "hands": [],
            "market": [["SELL", "WHEAT", 1]] * 10}
    overlay = _overlay(base_action=base, land_from_day=10)
    obs = make_obs(step=266, day=11, hour=2, unlocked=("NW", "NE", "SW"),
                   money=50000)
    out = overlay.augment(obs, base)
    assert out["market"] == base["market"]
    assert ["BUY_LAND"] not in out["market"]


# --------------------------------------------------------------------------- #
# Safety and configuration.
# --------------------------------------------------------------------------- #

def test_malformed_observations_never_raise():
    base = {"farmer": ["PASS"], "hands": [], "market": []}
    overlay = _overlay(base_action=base)
    for bad in ({}, None, {"farms": None}, {"farms": [None]}, {"step": "x"},
                {"farms": [{}], "private": None}):
        out = overlay(bad)
        assert isinstance(out, dict)
        assert "farmer" in out and "market" in out


def test_make_agent_rejects_bad_parameters():
    with pytest.raises(ValueError):
        pq.make_agent(extra_hands=-1)
    with pytest.raises(ValueError):
        pq.make_agent(quadrant_crop="UNICORN")
    with pytest.raises(ValueError):
        pq.make_agent(quadrant_tiles=-1)
    with pytest.raises(ValueError):
        pq.make_agent(hire_from_hour=24)
    with pytest.raises(ValueError):
        pq.make_agent(land_from_day=-1)
    with pytest.raises(ValueError):
        pq.make_agent(land_reserve=-1)


def test_zero_extra_hands_returns_the_base_action_untouched():
    base = {"farmer": ["NORTH"], "hands": [["WATER"]], "market": [["HIRE"]]}
    overlay = _overlay(extra_hands=0, base_action=base)
    obs = make_obs(step=266, day=11, hour=2, unlocked=("NW", "NE", "SW"),
                   money=50000)
    assert overlay(obs) == base


# --------------------------------------------------------------------------- #
# Season-level evidence.
# --------------------------------------------------------------------------- #

def test_zero_extra_hands_is_byte_identical_to_tape():
    """The proof the tape is undisturbed: pass-through reproduces it exactly."""
    bare = load_agent(str(pq._BASELINE))
    variant = pq.make_agent(extra_hands=0)
    opponent_a = load_agent(str(pq._BASELINE))
    opponent_b = load_agent(str(pq._BASELINE))

    run_bare = run_episode_trace(bare, opponent_a, seed=0)
    run_variant = run_episode_trace(variant, opponent_b, seed=0)

    bare_actions = [step[0]["action"] for step in run_bare["steps"]]
    variant_actions = [step[0]["action"] for step in run_variant["steps"]]
    assert bare_actions == variant_actions
    # Sanity: the season actually played the tape's land purchases.
    assert any(["BUY_LAND"] in (a.get("market") or []) for a in bare_actions)


def test_engine_confirms_hire_appears_the_turn_after_it_is_ordered():
    """HIRE is a market-phase order, so the hand shows up one turn later."""
    hire_step = 24
    calls = []

    def agent(obs):
        farm = obs["farms"][obs.get("player", 0)]
        calls.append((int(obs.get("step", -1)), len(farm.get("hands") or [])))
        market = [["HIRE"], ["HIRE"]] if obs.get("step") == hire_step else []
        return {"farmer": ["PASS"], "hands": [], "market": market}

    from kaggle_environments import make

    env = make("kaggriculture", configuration={"seed": 0})
    env.run([agent, agent])
    observed = dict(calls)
    assert observed.get(hire_step) == 0            # roster empty when we order
    assert observed.get(hire_step + 1) == 2        # two hands the next turn
