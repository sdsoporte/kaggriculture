"""Unit tests for the crop-tile -> animal-structure conversion overlay (v10).

The season-level test is the one that matters most:
``test_convert_tiles_zero_is_byte_identical_to_tape`` proves that at
``convert_tiles=0`` the overlay is a real pass-through, i.e. the tape's action
stream is unchanged turn for turn. Everything else pins the two halves of the
mechanism with synthetic observations:

  * **Claiming** - the tape's own ``PLANT`` is the only claim trigger, gated on
    ``convert_from_day`` and on all three quadrants being owned, never on a
    shed-access tile, and never duplicated. Claiming at the PLANT means the tile
    was empty, so no standing crop is dug up.
  * **Suppression** - exactly ``PLANT``/``WATER``/``HARVEST`` and exactly the
    claimed tiles.
  * **The job board** - the carry chains `param_agent` documents: ``FEED`` needs
    a carried WHEAT, and ``PLACE`` needs a carried animal, so a worker without
    the item is sent to a shed access tile for a batched ``PICKUP`` first (one
    wheat per feed was the first version's throughput bug and left animals
    unfed).
  * **Bookkeeping** - a hire appended after the tape's lands at the roster tail
    and keeps its absolute index when the tape hires again.
"""

from typing import Any

import pytest

from kaggriculture.agents import v10_more_animals as ma
from kaggriculture.loading import load_agent
from kaggriculture.runner import run_episode_trace

BOARD = 10
COW_TILE = (1, 1)
GOOSE_TILE = (2, 2)
SHED_TILE = (4, 4)


# --------------------------------------------------------------------------- #
# Synthetic observation builders.
# --------------------------------------------------------------------------- #

def _empty_tiles() -> Any:
    return [[None for _ in range(BOARD)] for _ in range(BOARD)]


def _animal(tiles, pos, animal="COW", fed_today=False, yield_units=0,
            fertilizer_available=False):
    tiles[pos[1]][pos[0]] = {
        "kind": ma.STRUCTURE_OF[animal],
        "animal": animal,
        "placed_day": 12,
        "yield_units": yield_units,
        "consecutive_unfed": 0,
        "fed_today": fed_today,
        "cared_today": False,
        "fertilizer_available": fertilizer_available,
        "pending_care_bonus": 0,
    }


def _structure(tiles, pos, animal="COW"):
    tiles[pos[1]][pos[0]] = {"kind": ma.STRUCTURE_OF[animal]}


def make_obs(step=300, day=12, hour=12, hands=(), farmer=(0, 0),
             unlocked=("NW", "NE", "SW"), money=20000.0, tiles=None,
             shed=None, inventories=None, seeds=None, hires_today=0, player=0):
    farm = {
        "money": money,
        "farmer": list(farmer),
        "hands": [list(p) for p in hands],
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
            "inventories": list(inventories or [{}]),
        },
        "market": {"prices": {}, "inventory": {}},
    }


def _overlay(species="COW", convert_tiles=2, extra_hands=0, base_action=None,
             **kwargs):
    base_action = base_action or {"farmer": ["PASS"], "hands": [], "market": []}
    return ma.MoreAnimalsOverlay(
        species=species,
        convert_tiles=convert_tiles,
        extra_hands=extra_hands,
        base_agent=lambda obs: base_action,
        **kwargs,
    )


# --------------------------------------------------------------------------- #
# Pure helpers.
# --------------------------------------------------------------------------- #

def test_structure_of_maps_each_species_to_its_engine_structure():
    assert ma.structure_of("COW") == "PASTURE"
    assert ma.structure_of("SHEEP") == "PASTURE"
    assert ma.structure_of("GOOSE") == "COOP"


def test_fib_hire_cost_matches_the_engine_indexing():
    # _fib(0)=1, _fib(1)=1, _fib(2)=2, _fib(3)=3, _fib(4)=5, ...
    assert [ma.fib_hire_cost(n) for n in range(5)] == [1, 1, 2, 3, 5]


def test_step_toward_shortens_the_manhattan_distance():
    assert ma.step_toward((0, 0), (2, 0)) == "EAST"
    assert ma.step_toward((2, 0), (0, 0)) == "WEST"
    assert ma.step_toward((0, 0), (0, 2)) == "SOUTH"
    assert ma.step_toward((0, 2), (0, 0)) == "NORTH"
    assert ma.step_toward((3, 3), (3, 3)) is None


def test_shed_access_tiles_are_the_four_inner_corners():
    assert set(ma.shed_access_tiles()) == {(4, 4), (5, 4), (4, 5), (5, 5)}
    assert ma.is_shed_adjacent((4, 4)) is True
    assert ma.is_shed_adjacent((0, 0)) is False


# --------------------------------------------------------------------------- #
# Claiming and suppression.
# --------------------------------------------------------------------------- #

def _plant_action(farmer_pos, hands_positions=None):
    return {
        "farmer": ["PLANT", "WHEAT"],
        "hands": [["PLANT", "WHEAT"] for _ in (hands_positions or [])],
        "market": [],
    }


def test_claim_replaces_the_tape_plant_and_records_the_tile():
    overlay = _overlay(convert_tiles=2, convert_from_day=11)
    obs = make_obs(step=300, day=12, farmer=(1, 1), hands=[(2, 2)])
    action = _plant_action((1, 1), [(2, 2)])
    out = overlay.augment(obs, action)
    assert overlay.claimed == [(1, 1), (2, 2)]
    assert out["farmer"] == ["PASS"]           # the seed is not spent
    assert out["hands"] == [["PASS"]]


def test_claim_is_gated_by_the_conversion_day():
    overlay = _overlay(convert_tiles=2, convert_from_day=11)
    obs = make_obs(step=200, day=8, farmer=(1, 1))
    overlay.augment(obs, _plant_action((1, 1)))
    assert overlay.claimed == []


def test_claim_is_gated_by_owning_all_three_quadrants():
    overlay = _overlay(convert_tiles=2, convert_from_day=11)
    obs = make_obs(step=300, day=12, farmer=(1, 1), unlocked=("NW", "NE"))
    overlay.augment(obs, _plant_action((1, 1)))
    assert overlay.claimed == []


def test_claim_respects_the_cap_and_never_duplicates():
    overlay = _overlay(convert_tiles=1, convert_from_day=11)
    action = {
        "farmer": ["PLANT", "WHEAT"],
        "hands": [["PLANT", "WHEAT"], ["PLANT", "WHEAT"]],
        "market": [],
    }
    obs = make_obs(step=300, day=12, farmer=(1, 1), hands=[(2, 2), (3, 3)])
    overlay.augment(obs, action)
    assert overlay.claimed == [(1, 1)]
    # A second turn on the same tile must not duplicate the claim.
    overlay.augment(obs, _plant_action((1, 1)))
    assert overlay.claimed == [(1, 1)]


def test_claim_never_takes_a_shed_access_tile():
    overlay = _overlay(convert_tiles=1, convert_from_day=11)
    obs = make_obs(step=300, day=12, farmer=SHED_TILE)
    overlay.augment(obs, _plant_action(SHED_TILE))
    assert overlay.claimed == []


def test_suppression_removes_water_and_harvest_only_on_claimed_tiles():
    overlay = _overlay(convert_tiles=1, convert_from_day=11)
    overlay.claimed = [(1, 1)]
    action = {
        "farmer": ["WATER"],            # on the claimed tile, suppressed
        "hands": [["WATER"], ["HARVEST"]],  # (2, 2) is not claimed
        "market": [],
    }
    obs = make_obs(step=300, day=12, farmer=(1, 1), hands=[(2, 2), (2, 2)])
    out = overlay.augment(obs, action)
    assert out["farmer"] == ["PASS"]
    assert out["hands"][0] == ["WATER"]
    assert out["hands"][1] == ["HARVEST"]


def test_harvest_on_a_converted_tile_is_suppressed_before_a_structure_exists():
    overlay = _overlay(convert_tiles=1, convert_from_day=11)
    overlay.claimed = [(1, 1)]
    obs = make_obs(step=300, day=12, farmer=(1, 1))
    out = overlay.augment(obs, {"farmer": ["HARVEST"], "hands": [], "market": []})
    assert out["farmer"] == ["PASS"]


# --------------------------------------------------------------------------- #
# Ownership bookkeeping.
# --------------------------------------------------------------------------- #

def test_pending_hire_resolves_to_the_last_appended_hand():
    overlay = _overlay()
    overlay.pending = 2
    overlay._resolve_pending(roster_len=5)
    assert overlay.owned == {3, 4}
    assert overlay.pending == 0


def test_failed_hire_allows_a_retry_instead_of_claiming_a_tape_hand():
    overlay = _overlay()
    overlay.pending = 2
    overlay.hired_today = True
    overlay._resolve_pending(roster_len=1)  # fewer hands than we asked for
    assert overlay.owned == set()
    assert overlay.hired_today is False


def test_owned_index_is_stable_after_a_later_tape_hire():
    # Our hand sits at index 3; the tape then appends hands 4 and 5. Indices do
    # not shift, so only index 3 is ours.
    overlay = _overlay(convert_tiles=1)
    overlay.claimed = [(1, 1)]
    _animal(obs_tiles := _empty_tiles(), (1, 1), fed_today=False)
    overlay.owned = {3}
    base = {"farmer": ["PASS"], "hands": [["PASS"]] * 5, "market": []}
    obs = make_obs(step=300, day=12, farmer=(0, 0),
                   hands=[(0, 0), (0, 0), (0, 0), (1, 1), (0, 0), (0, 0)],
                   tiles=obs_tiles, shed={"WHEAT": 2},
                   inventories=[{}, {}, {}, {}, {"WHEAT": 1}, {}, {}])
    out = overlay.augment(obs, base)
    assert out["hands"][3] == ["FEED"]
    assert out["hands"][4] == ["PASS"]
    assert out["hands"][5] == ["PASS"]


# --------------------------------------------------------------------------- #
# The job board: each job, and the two carry chains.
# --------------------------------------------------------------------------- #

def test_hand_carrying_wheat_feeds_a_hungry_animal():
    tiles = _empty_tiles()
    _animal(tiles, COW_TILE, fed_today=False)
    overlay = _overlay(convert_tiles=1)
    overlay.claimed = [COW_TILE]
    action, target = overlay._choose_job(COW_TILE, tiles, {"WHEAT": 1}, {}, set())
    assert action == ["FEED"]
    assert target == COW_TILE


def test_hand_without_wheat_walks_to_the_shed_and_picks_up_a_batch():
    # Regression: one wheat per feed makes every feed a full shed round trip,
    # which left three of six animals unfed in the first assembly.
    tiles = _empty_tiles()
    for pos in (COW_TILE, (2, 2), (3, 3)):
        _animal(tiles, pos, fed_today=False)
    overlay = _overlay(convert_tiles=3)
    overlay.claimed = [COW_TILE, (2, 2), (3, 3)]
    # Standing on a shed access tile, so this is the action it would execute.
    action, target = overlay._choose_job(SHED_TILE, tiles, {}, {"WHEAT": 5}, set())
    assert target in ma.SHED_ACCESS
    assert action == ["PICKUP", "WHEAT", 3]  # three feeds in one trip
    # From a distance it walks to the shed instead of picking up remotely.
    walk, target = overlay._choose_job((0, 0), tiles, {}, {"WHEAT": 5}, set())
    assert target in ma.SHED_ACCESS
    assert walk[0] in ("EAST", "SOUTH")


def test_hand_carrying_an_animal_places_it_on_the_matching_empty_structure():
    tiles = _empty_tiles()
    _structure(tiles, COW_TILE, "COW")
    overlay = _overlay(convert_tiles=1)
    overlay.claimed = [COW_TILE]
    action, target = overlay._choose_job(COW_TILE, tiles, {"COW": 1}, {}, set())
    assert action == ["PLACE", "COW"]
    assert target == COW_TILE


def test_hand_builds_the_structure_on_an_empty_converted_tile():
    tiles = _empty_tiles()
    overlay = _overlay(convert_tiles=1, species="GOOSE")
    overlay.claimed = [GOOSE_TILE]
    action, target = overlay._choose_job(GOOSE_TILE, tiles, {}, {}, set())
    assert action == ["BUILD_COOP"]
    assert target == GOOSE_TILE


def test_hand_harvests_an_animal_product():
    tiles = _empty_tiles()
    _animal(tiles, COW_TILE, fed_today=True, yield_units=3)
    overlay = _overlay(convert_tiles=1)
    overlay.claimed = [COW_TILE]
    action, target = overlay._choose_job(COW_TILE, tiles, {}, {}, set())
    assert action == ["HARVEST"]
    assert target == COW_TILE


def test_hand_collects_fertilizer():
    tiles = _empty_tiles()
    _animal(tiles, COW_TILE, fed_today=True, yield_units=0,
            fertilizer_available=True)
    overlay = _overlay(convert_tiles=1)
    overlay.claimed = [COW_TILE]
    action, target = overlay._choose_job(COW_TILE, tiles, {}, {}, set())
    assert action == ["COLLECT_FERTILIZER"]
    assert target == COW_TILE


def test_a_weed_on_a_converted_tile_is_dug_out():
    tiles = _empty_tiles()
    tiles[COW_TILE[1]][COW_TILE[0]] = {"kind": "WEED"}
    overlay = _overlay(convert_tiles=1)
    overlay.claimed = [COW_TILE]
    action, target = overlay._choose_job(COW_TILE, tiles, {}, {}, set())
    assert action == ["DIG"]
    assert target == COW_TILE


def test_the_job_board_never_targets_a_non_converted_tile():
    tiles = _empty_tiles()
    _animal(tiles, COW_TILE, fed_today=False)
    overlay = _overlay(convert_tiles=1)
    overlay.claimed = []  # nothing converted
    action, target = overlay._choose_job(COW_TILE, tiles, {"WHEAT": 1}, {}, set())
    assert action == ["PASS"]
    assert target is None


def test_a_hand_walks_toward_its_target_instead_of_acting_from_a_distance():
    tiles = _empty_tiles()
    _animal(tiles, COW_TILE, fed_today=False)
    overlay = _overlay(convert_tiles=1)
    overlay.claimed = [COW_TILE]
    action, target = overlay._choose_job((5, 5), tiles, {"WHEAT": 1}, {}, set())
    assert target == COW_TILE
    assert action[0] in ("NORTH", "WEST")


def test_two_hands_do_not_book_the_same_tile():
    tiles = _empty_tiles()
    _animal(tiles, COW_TILE, fed_today=False, yield_units=2)
    overlay = _overlay(convert_tiles=1)
    overlay.claimed = [COW_TILE]
    _action, target = overlay._choose_job(COW_TILE, tiles, {"WHEAT": 1}, {}, {COW_TILE})
    assert target is None  # the only job is booked


# --------------------------------------------------------------------------- #
# Market side.
# --------------------------------------------------------------------------- #

def test_hire_is_appended_once_the_hour_allows_and_three_quadrants_exist():
    overlay = _overlay(extra_hands=2, convert_from_day=11, hire_from_hour=2)
    obs = make_obs(step=300, day=12, hour=12, farmer=SHED_TILE)
    out = overlay.augment(obs, {"farmer": ["PASS"], "hands": [], "market": []})
    assert out["market"].count(["HIRE"]) == 2
    assert overlay.pending == 2


def test_hire_does_not_start_before_the_conversion_day():
    overlay = _overlay(extra_hands=2, convert_from_day=11, hire_from_hour=2)
    obs = make_obs(step=200, day=8, hour=12, farmer=SHED_TILE, unlocked=("NW", "NE"))
    out = overlay.augment(obs, {"farmer": ["PASS"], "hands": [], "market": []})
    assert ["HIRE"] not in out["market"]


def test_one_animal_is_bought_per_empty_built_structure():
    tiles = _empty_tiles()
    _structure(tiles, COW_TILE, "COW")
    _structure(tiles, GOOSE_TILE, "COW")
    overlay = _overlay(convert_tiles=6, convert_from_day=11)
    overlay.claimed = [COW_TILE, GOOSE_TILE]
    obs = make_obs(step=300, day=12, farmer=SHED_TILE, tiles=tiles)
    out = overlay.augment(obs, {"farmer": ["PASS"], "hands": [], "market": []})
    assert out["market"].count(["BUY_ANIMAL", "COW", 1]) == 1
    assert overlay.bought == 1
    # A second turn while the first animal is still in flight buys no more than
    # there is room to place.
    out2 = overlay.augment(obs, {"farmer": ["PASS"], "hands": [], "market": []})
    assert out2["market"].count(["BUY_ANIMAL", "COW", 1]) == 1
    assert overlay.bought == 2


def test_no_animal_is_bought_when_nothing_was_converted():
    overlay = _overlay(convert_tiles=6, convert_from_day=11)
    obs = make_obs(step=300, day=12, farmer=SHED_TILE)
    out = overlay.augment(obs, {"farmer": ["PASS"], "hands": [], "market": []})
    assert ["BUY_ANIMAL", "COW", 1] not in out["market"]


def test_wheat_is_bought_only_to_cover_our_own_animals():
    tiles = _empty_tiles()
    _animal(tiles, COW_TILE, fed_today=False)
    _animal(tiles, GOOSE_TILE, fed_today=False)
    overlay = _overlay(convert_tiles=2, convert_from_day=11)
    overlay.claimed = [COW_TILE, GOOSE_TILE]
    obs = make_obs(step=300, day=12, farmer=SHED_TILE, tiles=tiles, shed={"WHEAT": 0})
    out = overlay.augment(obs, {"farmer": ["PASS"], "hands": [], "market": []})
    assert [order for order in out["market"] if order[:2] == ["BUY_PRODUCT", "WHEAT"]] == [
        ["BUY_PRODUCT", "WHEAT", 2]
    ]


def test_market_stays_within_the_ten_order_cap():
    base = {"farmer": ["PASS"], "hands": [],
            "market": [["SELL", "WHEAT", 1]] * 10}
    overlay = _overlay(extra_hands=2, convert_tiles=6, convert_from_day=11)
    obs = make_obs(step=300, day=12, farmer=SHED_TILE)
    out = overlay.augment(obs, base)
    assert out["market"] == base["market"]


# --------------------------------------------------------------------------- #
# Safety, configuration and the identity proof.
# --------------------------------------------------------------------------- #

def test_zero_convert_tiles_returns_the_base_action_untouched():
    base = {"farmer": ["NORTH"], "hands": [["WATER"]], "market": [["HIRE"]]}
    overlay = _overlay(convert_tiles=0, extra_hands=2, base_action=base)
    obs = make_obs(step=300, day=12, farmer=SHED_TILE, money=50000)
    assert overlay(obs) == base


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
        ma.make_agent(species="UNICORN")
    with pytest.raises(ValueError):
        ma.make_agent(convert_tiles=-1)
    with pytest.raises(ValueError):
        ma.make_agent(extra_hands=-1)
    with pytest.raises(ValueError):
        ma.make_agent(convert_from_day=-1)
    with pytest.raises(ValueError):
        ma.make_agent(hire_from_hour=24)
    with pytest.raises(ValueError):
        ma.make_agent(reserve=-1)


def test_convert_tiles_zero_is_byte_identical_to_tape():
    """The proof the tape is undisturbed: pass-through reproduces it exactly."""
    bare = load_agent(str(ma._BASELINE))
    variant = ma.make_agent(convert_tiles=0)
    run_bare = run_episode_trace(
        bare, load_agent(str(ma._BASELINE)), seed=0
    )
    run_variant = run_episode_trace(
        variant, load_agent(str(ma._BASELINE)), seed=0
    )
    bare_actions = [step[0]["action"] for step in run_bare["steps"]]
    variant_actions = [step[0]["action"] for step in run_variant["steps"]]
    assert bare_actions == variant_actions
    # Sanity: the season actually played the tape's land purchases.
    assert any(["BUY_LAND"] in (a.get("market") or []) for a in bare_actions)
