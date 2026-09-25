"""Unit and season tests for the SE closed-loop overlay (``se_loop``).

The public path needs a full season (~3s) to exercise, so most tests drive the
pure helpers and the ``SELoopOverlay`` transform with synthetic observations.
The season-level tests are the ones that matter most:

  * ``test_disabled_overlay_is_byte_identical_to_the_tape`` - the tape-preservation
    proof: with ``enabled=False`` the action stream, turn for turn, is unchanged.
  * ``test_unfunded_overlay_is_byte_identical_to_the_tape`` - the same proof for
    the *gate never fires* state, which is what the doc's hard limit means.
  * ``test_funded_season_lands_...`` - the structural landing evidence: SE is
    unlocked after the gate day, tiles are planted and stay alive through their
    planting day (the engine-level proof that they were watered in time), no
    animal structure ever appears, the 10-order budget holds every turn, and the
    overlay never crashes into a silent pass-through.

The rest pin the engine traps: atomic PLANT validation (our plants must fit the
spare seed stock), the shared order budget, complete action payloads, hires that
land at the roster tail, and a DROP that only happens when the shed can take the
whole load.
"""

import importlib
from collections import Counter
from typing import Any

import pytest

from kaggriculture.loading import load_agent
from kaggriculture.runner import run_episode_trace

# Imported through importlib because this module is created in the same change as
# its test: the static index used by the linter lists the package's modules from
# the session snapshot, and a plain ``from kaggriculture.agents import se_loop``
# is reported as an unknown symbol until that index is rebuilt.
sl: Any = importlib.import_module("kaggriculture.agents.se_loop")

BOARD = 10
# The closest SE non-shed tile (distance 1 from the shed corner) and the second
# one, so a hand can be placed "on" or "one step away from" the work.
SE_TILE = (6, 5)
SE_TILE_2 = (5, 6)
SE_TRACE_OPS = {"PLANT", "WATER", "HARVEST", "DIG", "DROP",
                "NORTH", "SOUTH", "EAST", "WEST", "PASS"}
ALL_SE_TILES = sl.designated_tiles(24)


# --------------------------------------------------------------------------- #
# Synthetic observation builders.
# --------------------------------------------------------------------------- #

def _empty_tiles() -> list[list[Any]]:
    return [[None for _ in range(BOARD)] for _ in range(BOARD)]


def make_obs(step=50, day=2, hour=2, hands=(), unlocked=("NW", "NE", "SW", "SE"),
             money=20000.0, tiles=None, seeds=None, hires_today=0, player=0,
             inventories=None, shed=None):
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
        "private": {
            "seeds": dict(seeds or {}),
            "shed": dict(shed or {}),
            "inventories": inventories if inventories is not None else [{}],
        },
        "market": {"prices": {}, "inventory": {}},
    }


def _plant(tiles, pos, crop="CARROT", planted_day=0, watered_today=False,
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


def _overlay(base_action=None, **kwargs):
    action = base_action or {"farmer": ["PASS"], "hands": [], "market": []}
    kwargs.setdefault("seed_mix", {"CARROT": 2, "WHEAT": 2})
    return sl.SELoopOverlay(base_agent=lambda obs: action, **kwargs)


# --------------------------------------------------------------------------- #
# Pure helpers: geometry, plan, crop rules.
# --------------------------------------------------------------------------- #

def test_default_mix_is_the_cheap_fast_sixteen_tile_plan():
    overlay = sl.SELoopOverlay(base_agent=lambda obs: {})
    assert overlay.mix == sl.DEFAULT_SEED_MIX == {"CARROT": 8, "WHEAT": 8}
    # The reserve is exactly the seed bill of the planned first plantings.
    assert overlay.land_reserve == overlay.seed_cost == 8 * 20 + 8 * 10
    assert len(overlay.plan) == 16
    tiles = [tile for tile, _ in overlay.plan]
    assert len(set(tiles)) == 16
    for (x, y) in tiles:
        assert sl.quadrant_of(x, y) == "SE"
        assert (x, y) not in set(sl.shed_access_tiles())


def test_designated_tiles_are_se_and_skip_the_shed():
    tiles = sl.designated_tiles(6)
    assert len(tiles) == 6
    assert len(set(tiles)) == 6
    assert sl.designated_tiles(2) == tiles[:2]  # prefix stable: identity holds
    for (x, y) in tiles:
        assert sl.quadrant_of(x, y) == "SE"
        assert (x, y) not in set(sl.shed_access_tiles())


def test_crop_sequence_interleaves_the_mix_and_keeps_the_counts():
    sequence = sl.crop_sequence({"CARROT": 10, "WHEAT": 6}, 16)
    counts = Counter(sequence)
    assert counts == {"CARROT": 10, "WHEAT": 6}
    assert sequence[0] == "CARROT" and sequence[1] == "WHEAT"
    assert sl.crop_sequence({"CARROT": 10, "WHEAT": 6}, 16) == sequence


def test_seed_cost_uses_the_engine_prices():
    assert sl.seed_cost({"CARROT": 2, "WHEAT": 3}) == 2 * 20 + 3 * 10


def test_harvest_maturity_day_separates_one_shot_from_ongoing_crops():
    assert sl.harvest_maturity_day("CARROT") == 3
    assert sl.harvest_maturity_day("WHEAT") == 4
    assert sl.harvest_maturity_day("TOMATO") == 8  # ongoing: first_yield_day


def test_water_window_matches_the_engine_table():
    assert sl.water_window("WHEAT") == (2, 4)
    assert sl.water_window("CARROT") == (2, 3)
    assert sl.water_window("MELON") == (6, 12)


def test_needs_water_on_the_planting_day_even_outside_the_window():
    # _new_plant starts consecutive_unwatered at 1 and two unwatered midnights
    # turn the tile into a WEED, so day 0 is urgent even outside [2, 3].
    tile = {"kind": "PLANT", "crop": "CARROT", "planted_day": 0,
            "watered_today": False, "consecutive_unwatered": 1, "yield_units": 1}
    assert sl.needs_water(tile, 0, "CARROT") is True
    assert sl.water_is_urgent(tile) is True


def test_needs_water_is_window_gated_once_the_weed_clock_is_clear():
    tile = {"kind": "PLANT", "crop": "CARROT", "planted_day": 0,
            "watered_today": False, "consecutive_unwatered": 0, "yield_units": 1}
    assert sl.needs_water(tile, 1, "CARROT") is False  # skipped middle day
    assert sl.needs_water(tile, 2, "CARROT") is True
    assert sl.water_boosts_harvest(tile, 3, "CARROT") is True
    assert sl.needs_water({**tile, "watered_today": True}, 2, "CARROT") is False


def test_harvest_due_only_at_the_maturity_day_or_at_max_yield():
    tile = {"kind": "PLANT", "crop": "CARROT", "planted_day": 0,
            "yield_units": 3, "watered_today": True}
    assert sl.harvest_due(tile, 2, "CARROT") is False  # early: throws yield away
    assert sl.harvest_due(tile, 3, "CARROT") is True
    assert sl.harvest_due({**tile, "yield_units": 4}, 2, "CARROT") is True


def test_fib_hire_cost_matches_the_engine_indexing():
    assert [sl.fib_hire_cost(n) for n in range(5)] == [1, 1, 2, 3, 5]


def test_step_toward_shortens_the_manhattan_distance():
    assert sl.step_toward((5, 5), (6, 5)) == "EAST"
    assert sl.step_toward((6, 5), (5, 5)) == "WEST"
    assert sl.step_toward((5, 5), (5, 6)) == "SOUTH"
    assert sl.step_toward((5, 6), (5, 5)) == "NORTH"
    assert sl.step_toward((6, 5), (6, 5)) is None


# --------------------------------------------------------------------------- #
# The land gate.
# --------------------------------------------------------------------------- #

def test_land_is_bought_only_with_three_quadrants_from_buy_day_and_with_reserve():
    base = {"farmer": ["PASS"], "hands": [], "market": []}
    overlay = _overlay(base, buy_day=10)
    ok = make_obs(step=266, day=11, hour=3, unlocked=("NW", "NE", "SW"),
                  money=sl.SE_PRICE + overlay.land_reserve)
    assert ["BUY_LAND"] in overlay.augment(ok, base)["market"]
    assert ["BUY_LAND"] not in overlay.augment(
        make_obs(step=266, day=11, hour=3, unlocked=("NW", "NE"),
                 money=50000), base)["market"]
    assert ["BUY_LAND"] not in overlay.augment(
        make_obs(step=120, day=5, hour=3, unlocked=("NW", "NE", "SW"),
                 money=50000), base)["market"]
    assert ["BUY_LAND"] not in overlay.augment(
        make_obs(step=266, day=11, hour=3, unlocked=("NW", "NE", "SW"),
                 money=sl.SE_PRICE + overlay.land_reserve - 1), base)["market"]


def test_land_gate_is_net_of_the_tape_same_turn_spend():
    """Our order resolves AFTER the tape's, so its spend is already gone."""
    overlay = _overlay(buy_day=10)
    money = sl.SE_PRICE + overlay.land_reserve + 100
    base = {"farmer": ["PASS"], "hands": [],
            "market": [["BUY_SEED", "MELON", 10]]}  # $800 the tape spends first
    obs = make_obs(step=266, day=11, hour=3, unlocked=("NW", "NE", "SW"),
                   money=money, seeds={"MELON": 10})
    assert ["BUY_LAND"] not in overlay.augment(obs, base)["market"]
    # Same money, no tape spend: the gate fires.
    quiet = {"farmer": ["PASS"], "hands": [], "market": []}
    assert ["BUY_LAND"] in overlay.augment(obs, quiet)["market"]


def test_land_is_not_bought_when_the_turn_is_already_full():
    base = {"farmer": ["PASS"], "hands": [], "market": [["SELL", "WHEAT", 1]] * 10}
    overlay = _overlay(base, buy_day=10)
    obs = make_obs(step=266, day=11, hour=3, unlocked=("NW", "NE", "SW"), money=50000)
    out = overlay.augment(obs, base)
    assert out["market"] == base["market"]


# --------------------------------------------------------------------------- #
# Seeds and the atomic PLANT rule.
# --------------------------------------------------------------------------- #

def test_seed_purchase_is_one_order_per_crop_with_a_quantity():
    base = {"farmer": ["PASS"], "hands": [], "market": []}
    overlay = _overlay(base, seed_mix={"CARROT": 3, "WHEAT": 4})
    obs = make_obs(step=268, day=11, hour=4, unlocked=("NW", "NE", "SW", "SE"),
                   money=20000, seeds={})
    market = overlay.augment(obs, base)["market"]
    seed_orders = [o for o in market if o[0] == "BUY_SEED"]
    assert len(seed_orders) == 2  # one per crop, never a per-unit loop
    quantities = {o[1]: o[2] for o in seed_orders}
    assert quantities["CARROT"] >= 2 and quantities["WHEAT"] >= 2
    assert len(seed_orders) < sum(quantities.values()) or sum(quantities.values()) <= 7


def test_no_seed_order_survives_the_order_cap():
    base = {"farmer": ["PASS"], "hands": [], "market": [["SELL", "WHEAT", 1]] * 10}
    overlay = _overlay(base, seed_mix={"CARROT": 3})
    obs = make_obs(step=268, day=11, hour=4, unlocked=("NW", "NE", "SW", "SE"))
    assert len(overlay.augment(obs, base)["market"]) == 10


def test_plant_budget_subtracts_the_tape_demand_and_the_margin():
    overlay = _overlay(seed_margin=2)
    action = {"farmer": ["PLANT", "CARROT"], "hands": [["PLANT", "CARROT"]],
              "market": []}
    assert overlay._spare_seeds(action, {"CARROT": 6}, "CARROT") == 2  # 6-2-2
    assert overlay._spare_seeds(action, {"CARROT": 4}, "CARROT") == 0
    assert overlay._spare_seeds(action, {}, "CARROT") == 0


def test_overlay_never_plants_when_the_spare_stock_is_empty():
    """The tape's own PLANT must never be voided by our demand."""
    tiles = _empty_tiles()
    action = {"farmer": ["PLANT", "CARROT"], "hands": [["PASS"]], "market": []}
    overlay = _overlay(action, seed_mix={"CARROT": 1}, seed_margin=0)
    overlay.owned = {0}
    obs = make_obs(step=50, day=12, hour=6, hands=[SE_TILE], tiles=tiles,
                   seeds={"CARROT": 1})
    out = overlay.augment(obs, action)
    assert out["hands"][0] != ["PLANT", "CARROT"]


def test_plant_action_carries_the_crop_payload_and_never_the_bare_verb():
    tiles = _empty_tiles()
    action = {"farmer": ["PASS"], "hands": [["PASS"]], "market": []}
    overlay = _overlay(action, seed_mix={"CARROT": 2}, seed_margin=0)
    overlay.owned = {0}
    obs = make_obs(step=50, day=12, hour=6, hands=[SE_TILE], tiles=tiles,
                   seeds={"CARROT": 5})
    out = overlay.augment(obs, action)
    assert out["hands"][0] == ["PLANT", "CARROT"]


def test_hand_walks_instead_of_planting_from_a_distance():
    tiles = _empty_tiles()
    action = {"farmer": ["PASS"], "hands": [["PASS"]], "market": []}
    overlay = _overlay(action, seed_mix={"CARROT": 2}, seed_margin=0)
    overlay.owned = {0}
    obs = make_obs(step=50, day=12, hour=6, hands=[(5, 5)], tiles=tiles,
                   seeds={"CARROT": 5})
    out = overlay.augment(obs, action)
    assert out["hands"][0][0] in ("EAST", "SOUTH")


def test_no_plant_is_emitted_when_it_cannot_mature_before_the_season_ends():
    tiles = _empty_tiles()
    action = {"farmer": ["PASS"], "hands": [["PASS"]], "market": []}
    overlay = _overlay(action, seed_mix={"CARROT": 2}, seed_margin=0)
    overlay.owned = {0}
    # step must not be day-aligned: hour 0 is when the engine resets the roster.
    too_late = make_obs(step=28 * 24 + 6, day=28, hour=6, hands=[SE_TILE],
                        tiles=tiles, seeds={"CARROT": 5})
    assert overlay.augment(too_late, action)["hands"][0][0] not in ("PLANT",)
    # One day earlier it fits: 27 + 3 = 30 > 29, 26 + 3 = 29 <= 29.
    obs_ok = make_obs(step=26 * 24 + 6, day=26, hour=6, hands=[SE_TILE],
                      tiles=tiles, seeds={"CARROT": 5})
    assert overlay.augment(obs_ok, action)["hands"][0] == ["PLANT", "CARROT"]


# --------------------------------------------------------------------------- #
# Hand ownership and tape preservation.
# --------------------------------------------------------------------------- #

def test_owned_hand_must_be_verified_by_both_engine_counters():
    overlay = _overlay()
    overlay.pending = 1
    overlay.prev_roster, overlay.prev_hires = 10, 11
    overlay._resolve_pending({"hands": [None] * 11, "hires_today": 12})
    assert overlay.owned == {10}


def test_hire_that_did_not_land_claims_nothing_and_can_be_retried():
    overlay = _overlay()
    overlay.pending = 2
    overlay.hired_today = True
    overlay.prev_roster, overlay.prev_hires = 10, 11
    overlay._resolve_pending({"hands": [None] * 11, "hires_today": 12})  # grew by 1
    assert overlay.owned == set()
    assert overlay.hired_today is False


def test_extra_hand_is_hired_only_after_the_tapes_own_hiring_burst():
    base = {"farmer": ["PASS"], "hands": [], "market": []}
    overlay = _overlay(base, extra_hands_cap=1, hire_from_hour=5)
    early = make_obs(step=2 * 24 + 2, day=2, hour=2, money=50000, hires_today=9,
                     unlocked=("NW", "NE", "SW", "SE"))
    assert ["HIRE"] not in overlay.augment(early, base)["market"]
    late = make_obs(step=2 * 24 + 5, day=2, hour=5, money=50000, hires_today=9,
                    unlocked=("NW", "NE", "SW", "SE"))
    assert ["HIRE"] in overlay.augment(late, base)["market"]


def test_no_hire_when_the_tape_is_hiring_in_the_same_turn():
    base = {"farmer": ["PASS"], "hands": [], "market": [["HIRE"], ["BUY_SEED", "WHEAT", 2]]}
    overlay = _overlay(base, extra_hands_cap=1)
    obs = make_obs(step=2 * 24 + 5, day=2, hour=5, money=50000, hires_today=2,
                   unlocked=("NW", "NE", "SW", "SE"))
    assert overlay.augment(obs, base)["market"].count(["HIRE"]) == 1


def test_only_owned_indices_are_rewritten_and_the_tape_action_is_not_mutated():
    tiles = _empty_tiles()
    _plant(tiles, SE_TILE)
    base = {"farmer": ["NORTH"], "hands": [["WATER"], ["HARVEST"], ["PASS"]],
            "market": [["SELL", "WHEAT", 3]]}
    overlay = _overlay(base, seed_mix={"CARROT": 2})
    overlay.owned = {2}
    obs = make_obs(step=50, day=2, hour=6, hands=[(0, 0), (0, 0), SE_TILE],
                   tiles=tiles, seeds={"CARROT": 4})
    out = overlay.augment(obs, base)
    assert out["farmer"] == base["farmer"]
    assert out["hands"][0] == ["WATER"]     # tape hand, untouched
    assert out["hands"][1] == ["HARVEST"]   # tape hand, untouched
    assert out["hands"][2] == ["WATER"]     # ours
    assert out["market"] == [["SELL", "WHEAT", 3]]
    assert base["hands"][2] == ["PASS"]     # the tape's action was not mutated
    assert base["market"] == [["SELL", "WHEAT", 3]]


def test_no_owned_indices_means_the_hands_list_is_returned_untouched():
    base = {"farmer": ["SOUTH"], "hands": [["PASS"], ["WATER"]], "market": []}
    overlay = _overlay(base)
    obs = make_obs(step=50, day=2, hour=6, hands=[(0, 0), (0, 0)])
    assert overlay.augment(obs, base)["hands"] == base["hands"]


# --------------------------------------------------------------------------- #
# The job board stays inside SE and inside the engine's rules.
# --------------------------------------------------------------------------- #

def test_overlay_hands_never_target_a_tile_outside_the_se_quadrant():
    """A mature WHEAT in NW must be invisible to our job board."""
    tiles = _empty_tiles()
    _plant(tiles, SE_TILE, planted_day=0, yield_units=3)   # inside SE
    _plant(tiles, (1, 1), crop="WHEAT", planted_day=0)    # outside SE
    _plant(tiles, (8, 0), crop="WHEAT", planted_day=0)    # outside SE
    tiles[5][5] = {"kind": "WEED"}                        # a shed-access tile
    trace = []
    base = {"farmer": ["PASS"], "hands": [["PASS"]], "market": []}
    overlay = _overlay(base, seed_mix={"CARROT": 4}, seed_margin=0, trace=trace)
    overlay.owned = {0}
    obs = make_obs(step=12 * 24 + 6, day=12, hour=6, hands=[SE_TILE_2], tiles=tiles,
                   seeds={"CARROT": 4})
    out = overlay.augment(obs, base)
    targets = [e[3] for e in trace if e[0] == "act"]
    assert targets, "the hand produced no job at all"
    assert all(target is None or target in ALL_SE_TILES for target in targets)
    assert out["hands"][0][0] in SE_TRACE_OPS
    # The NE/NW wheat and the shed-access weed are never the chosen target.
    assert (1, 1) not in targets and (8, 0) not in targets and (5, 5) not in targets


def test_harvest_flag_off_never_harvests():
    tiles = _empty_tiles()
    _plant(tiles, SE_TILE, planted_day=0, yield_units=3, watered_today=True)
    base = {"farmer": ["PASS"], "hands": [["PASS"]], "market": []}
    overlay = _overlay(base, seed_mix={"CARROT": 2}, harvest=False)
    overlay.owned = {0}
    obs = make_obs(step=50, day=3, hour=6, hands=[SE_TILE], tiles=tiles,
                   seeds={"CARROT": 4})
    assert overlay.augment(obs, base)["hands"][0][0] != "HARVEST"


def test_drop_only_when_the_shed_can_take_the_whole_load():
    base = {"farmer": ["PASS"], "hands": [["PASS"]], "market": []}
    overlay = _overlay(base)
    overlay.owned = {0}
    load = {"CARROT": 6}
    obs = make_obs(step=50, day=29, hour=6, hands=[(5, 5)],
                   inventories=[{}, load], shed={})
    assert overlay.augment(obs, base)["hands"][0] == ["DROP"]
    # A shed with room for only half the load: DROP would delete the rest.
    obs_full = make_obs(step=50, day=29, hour=6, hands=[(5, 5)],
                        inventories=[{}, load], shed={"WHEAT": 97})
    assert overlay.augment(obs_full, base)["hands"][0] != ["DROP"]


def test_malformed_observations_never_raise():
    base = {"farmer": ["PASS"], "hands": [], "market": []}
    overlay = _overlay(base)
    for bad in ({}, None, {"farms": None}, {"farms": [None]}, {"step": "x"},
                {"farms": [{}], "private": None}, {"farms": [{"tiles": None}]}):
        out = overlay(bad)
        assert isinstance(out, dict)
        assert "farmer" in out and "market" in out


def test_make_agent_rejects_bad_parameters():
    bad_params: list[dict[str, Any]] = [
        {"buy_day": -1}, {"extra_hands_cap": -1}, {"hire_from_hour": 24},
        {"hire_until_hour": 3, "hire_from_hour": 5},
        {"plant_until_hour": 24}, {"seed_margin": -1}, {"cash_floor": -1},
        {"se_tiles": -1}, {"land_reserve": -1}, {"seed_mix": {}},
        {"seed_mix": {"UNICORN": 1}}, {"seed_mix": {"CARROT": 0}},
        {"seed_mix": {"CARROT": -2}},
    ]
    for kwargs in bad_params:
        with pytest.raises(ValueError):
            sl.make_agent(**kwargs)


# --------------------------------------------------------------------------- #
# Season-level evidence.
# --------------------------------------------------------------------------- #

def _actions(run, seat=0):
    return [step[seat].get("action") for step in run["steps"]]


def _planting_events(run, seat=0):
    """Every (tile, day) where a PLANT first shows up on an SE tile.

    The observation is the state at the start of a turn, so a plant with
    ``planted_day == day`` appears on the turn after it was planted.
    """
    seen = {}
    events = []
    for step in run["steps"]:
        obs = step[seat]["observation"]
        tiles = obs["farms"][seat]["tiles"]
        for (x, y) in ALL_SE_TILES:
            tile = tiles[y][x]
            planted_day = (tile.get("planted_day") if isinstance(tile, dict)
                           and tile.get("kind") == "PLANT" else None)
            if planted_day is None:
                seen.pop((x, y), None)
            elif seen.get((x, y)) != planted_day:
                seen[(x, y)] = planted_day
                events.append(((x, y), planted_day))
    return events


def _alive_next_day(run, tile, planted_day, seat=0):
    for step in run["steps"]:
        obs = step[seat]["observation"]
        if int(obs.get("day", 0)) != planted_day + 1:
            continue
        cell = obs["farms"][seat]["tiles"][tile[1]][tile[0]]
        return (isinstance(cell, dict) and cell.get("kind") == "PLANT"
                and cell.get("planted_day") == planted_day)
    return False


def test_disabled_overlay_is_byte_identical_to_the_tape():
    """The tape-preservation proof: pass-through reproduces the tape exactly."""
    bare = load_agent(str(sl._BASELINE))
    disabled = sl.make_agent(enabled=False)
    run_bare = run_episode_trace(bare, load_agent(str(sl._BASELINE)), seed=0)
    run_disabled = run_episode_trace(disabled, load_agent(str(sl._BASELINE)), seed=0)
    assert _actions(run_bare) == _actions(run_disabled)
    assert any(["BUY_LAND"] in (a.get("market") or []) for a in _actions(run_bare))


def test_unfunded_overlay_never_buys_land_and_is_byte_identical_to_the_tape():
    """The gate-never-fires state must be a complete pass-through too."""
    bare = load_agent(str(sl._BASELINE))
    unfunded = sl.make_agent(land_reserve=10 ** 9)
    run_bare = run_episode_trace(bare, load_agent(str(sl._BASELINE)), seed=0)
    run_unfunded = run_episode_trace(unfunded, load_agent(str(sl._BASELINE)), seed=0)
    assert _actions(run_bare) == _actions(run_unfunded)
    final = run_unfunded["steps"][-1][0]["observation"]["farms"][0]
    assert final["unlocked_quadrants"] == ["NW", "NE", "SW"]  # SE never bought
    # The unfunded pass-through must have placed no structure or animal anywhere:
    # every remaining tile stays bare (no COOP/PASTURE/animal dict was ever ours).
    bare_final = run_bare["steps"][-1][0]["observation"]["farms"][0]
    assert final["tiles"] == bare_final["tiles"]


def test_funded_season_lands_the_mechanism_without_placing_animal_structures():
    """Structural landing evidence on a real episode."""
    trace = []
    overlay = sl.make_agent(trace=trace)
    run = run_episode_trace(overlay, load_agent(str(sl._BASELINE)), seed=0)

    # 1. The overlay never crashed into a silent pass-through.
    assert not [e for e in trace if e[0] == "crash"]

    # 2. The gate fired once, after day 10, and SE actually unlocked.
    land = [e for e in trace if e[0] == "land"]
    assert len(land) == 1 and land[0][1] >= 10
    final = run["steps"][-1][0]["observation"]["farms"][0]
    assert final["unlocked_quadrants"] == ["NW", "NE", "SW", "SE"]

    # 3. New crops appeared inside ~3 days of the purchase.
    events = _planting_events(run)
    assert events, "no SE tile was ever planted"
    plant_day = min(day for _, day in events)
    assert plant_day <= land[0][1] + 3
    assert len({tile for tile, day in events if day <= land[0][1] + 3}) >= 4

    # 4. Every new crop was watered in time: the engine keeps an unwatered
    #    planting-day crop alive only by watering it (otherwise it is a WEED).
    water_events = {(e[1], e[2]) for e in trace if e[0] == "water"}
    for tile, day in events:
        assert (tile, day) in water_events, f"{tile} watered by a hand on day {day}"
        assert _alive_next_day(run, tile, day), f"{tile} died the night of day {day}"

    # 5. Zero animal structures on the new land, and no animal action ever.
    for step in run["steps"]:
        tiles = step[0]["observation"]["farms"][0]["tiles"]
        for (x, y) in ALL_SE_TILES:
            cell = tiles[y][x]
            if isinstance(cell, dict):
                assert cell.get("kind") not in ("COOP", "PASTURE")
                assert "animal" not in cell
    for event in trace:
        if event[0] == "act":
            assert event[2][0] in SE_TRACE_OPS

    # 6. The shared 10-order budget held on every single turn.
    for action in _actions(run):
        assert len(action.get("market") or []) <= sl.MAX_ORDERS

    # 7. The overlay really hired: a hand index was claimed by the verified
    #    roster/hires equality, and it acted on many turns.
    assert [e for e in trace if e[0] == "own_hand"]
    assert len([e for e in trace if e[0] == "act"]) > 200
