"""Unit tests for the idle-filling overlay (v10_fill_idle).

The overlay answers one measurement: our tape emits PASS on ~7.4% of its unit
turns (529 a season) against 3.8% for the corpus winners, and a unit that is
standing on a tile with available work is idling for no reason. This overlay
converts those PASS turns into local work and nothing else.

Three tests carry the safety claim, and they are the reason the file exists:

  * ``test_disabled_overlay_is_byte_identical_to_the_tape`` -- with the feature
    disabled the action stream is the tape's, turn for turn, verified over a full
    season.
  * ``test_enabled_overlay_only_ever_rewrites_a_pass_turn`` -- over a full season,
    every non-PASS unit action and every market order is bit-for-bit the tape's,
    on the exact observation the tape acted upon. This is the "no turn gains or
    loses a market order" proof, and it also proves no movement verb is emitted.
  * ``test_enabled_overlay_reduces_the_idle_share`` -- the point of the whole
    exercise: PASS turns must actually go down in a real season.

The rest pin the conservative work set, and above all the trap: a plant below its
yield cap INSIDE its watering window is never harvested, because the window would
have delivered more units than the harvest takes.
"""

import copy
from functools import lru_cache
from typing import Any

from kaggriculture.agents import v10_fill_idle as fi
from kaggriculture.loading import load_agent
from kaggriculture.runner import run_episode_trace

BOARD = 10

PASS_ACTION = {"farmer": ["PASS"], "hands": [], "market": []}


# --------------------------------------------------------------------------- #
# Synthetic observations.
# --------------------------------------------------------------------------- #

def _empty_tiles() -> Any:
    return [[None for _ in range(BOARD)] for _ in range(BOARD)]


def make_obs(step=100, day=4, farmer=(0, 0), hands=(), tiles=None,
             inventories=None, player=0):
    farm = {
        "farmer": list(farmer),
        "hands": [list(h) for h in hands],
        "tiles": tiles if tiles is not None else _empty_tiles(),
        "money": 10000.0,
    }
    invs = list(inventories) if inventories is not None else [{} for _ in range(1 + len(hands))]
    while len(invs) < 1 + len(hands):
        invs.append({})
    return {
        "step": step,
        "day": day,
        "hour": step % 24,
        "player": player,
        "farms": [farm],
        "private": {"inventories": invs, "seeds": {}, "shed": {}},
        "market": {"prices": {}, "inventory": {}},
    }


def _plant(tiles, pos, crop="WHEAT", planted_day=0, yield_units=1, **extra):
    x, y = pos
    tile = {"kind": "PLANT", "crop": crop, "planted_day": planted_day,
            "watered_today": False, "consecutive_unwatered": 0,
            "yield_units": yield_units}
    tile.update(extra)
    tiles[y][x] = tile
    return tile


def _animal(tiles, pos, animal="GOOSE", placed_day=0, yield_units=0,
            fed_today=False, cared_today=False, fertilizer_available=False,
            consecutive_unfed=0):
    x, y = pos
    structure = {"GOOSE": "COOP", "COW": "PASTURE", "SHEEP": "PASTURE"}[animal]
    tiles[y][x] = {
        "kind": structure, "animal": animal, "placed_day": placed_day,
        "yield_units": yield_units, "consecutive_unfed": consecutive_unfed,
        "fed_today": fed_today, "cared_today": cared_today,
        "fertilizer_available": fertilizer_available,
    }
    return tiles[y][x]


def _overlay(base_action=None, enabled=True, trace=None):
    base_action = base_action if base_action is not None else copy.deepcopy(PASS_ACTION)
    return fi.FillIdleOverlay(base_agent=lambda obs: base_action, enabled=enabled,
                              trace=trace)


# --------------------------------------------------------------------------- #
# The conservative work set.
# --------------------------------------------------------------------------- #

def test_animal_with_product_is_harvested():
    tile = {"kind": "COOP", "animal": "GOOSE", "yield_units": 2, "fed_today": True,
            "cared_today": True, "fertilizer_available": False}
    assert fi.safe_work(tile, 10, 0) == ["HARVEST"]


def test_animal_fertilizer_is_collected_and_care_is_given():
    fertilizer = {"kind": "PASTURE", "animal": "COW", "yield_units": 0,
                  "fed_today": True, "cared_today": True, "fertilizer_available": True}
    care = {"kind": "PASTURE", "animal": "COW", "yield_units": 0, "fed_today": True,
            "cared_today": False, "fertilizer_available": False}
    assert fi.safe_work(fertilizer, 10, 0) == ["COLLECT_FERTILIZER"]
    assert fi.safe_work(care, 10, 0) == ["CARE"]


def test_nothing_is_left_on_a_fed_cared_animal_with_nothing_to_show():
    tile = {"kind": "COOP", "animal": "GOOSE", "yield_units": 0, "fed_today": True,
            "cared_today": True, "fertilizer_available": False}
    assert fi.safe_work(tile, 10, 0) is None


def test_feed_needs_carried_wheat_or_it_would_be_a_silent_no_op():
    hungry = {"kind": "COOP", "animal": "GOOSE", "yield_units": 0, "fed_today": False,
              "cared_today": True, "fertilizer_available": False}
    assert fi.safe_work(hungry, 10, 0) is None
    assert fi.safe_work(hungry, 10, 1) == ["FEED"]


def test_feeding_precedes_harvesting_so_a_starving_animal_is_not_lost():
    # Two unwatered/fed midnights and the animal escapes and is worth zero; a
    # harvest would leave it starving with the product already safe in inventory.
    tile = {"kind": "PASTURE", "animal": "COW", "yield_units": 5, "fed_today": False,
            "cared_today": False, "fertilizer_available": True, "consecutive_unfed": 1}
    assert fi.safe_work(tile, 10, 1) == ["FEED"]


def test_plant_at_its_yield_cap_is_harvested():
    # max_yield 6 for WHEAT, so the tile cannot accumulate another unit.
    tile = {"kind": "PLANT", "crop": "WHEAT", "planted_day": 0, "yield_units": 6}
    assert fi.safe_work(tile, 3, 0) == ["HARVEST"]


def test_ongoing_plant_at_cap_is_harvested_but_below_cap_is_left_alone():
    at_cap = {"kind": "PLANT", "crop": "TOMATO", "planted_day": 0, "yield_units": 4}
    below = {"kind": "PLANT", "crop": "TOMATO", "planted_day": 0, "yield_units": 2}
    assert fi.safe_work(at_cap, 12, 0) == ["HARVEST"]
    assert fi.safe_work(below, 12, 0) is None


def test_one_shot_plant_past_its_window_is_harvested_even_below_cap():
    # CARROT: first_yield_day 2, max_yield_day 3. At age 4 nothing can be added
    # (the watering bonus window closed at age 3) and decay has begun.
    tile = {"kind": "PLANT", "crop": "CARROT", "planted_day": 0, "yield_units": 2}
    assert fi.safe_work(tile, 4, 0) == ["HARVEST"]


def test_the_last_day_of_the_watering_window_is_not_treated_as_past_it():
    # The engine's bonus window is INCLUSIVE of max_yield_day
    # (kaggriculture.py:440-441), so a WATER today can still add units. This is
    # where the brief's literal "day - planted_day >= max_yield_day" would clip a
    # day of growth. Age 3 for CARROT is still inside the window.
    tile = {"kind": "PLANT", "crop": "CARROT", "planted_day": 0, "yield_units": 2}
    assert fi.safe_work(tile, 3, 0) is None
    # Age 4 is the first day no accumulation is possible.
    assert fi.safe_work(tile, 4, 0) == ["HARVEST"]


def test_crop_below_cap_inside_the_window_is_never_harvested():
    """The trap this overlay exists to avoid: harvesting at first_yield_day."""
    # WHEAT max_yield_day 4, window days 2-4. A day-2 harvest takes 1-2 units
    # where the window would deliver up to 6.
    tile = {"kind": "PLANT", "crop": "WHEAT", "planted_day": 0, "yield_units": 2}
    for day in (2, 3, 4):
        assert fi.safe_work(tile, day, 0) is None


def test_harvest_before_first_yield_day_is_not_emitted_even_at_cap():
    # A MELON watered inside its window reaches the cap of 6 on day 8, before
    # first_yield_day 10. HARVEST then is a silent no-op for a one-shot crop
    # (kaggriculture.py:451-462): emitting it would only add invisible waste.
    tile = {"kind": "PLANT", "crop": "MELON", "planted_day": 0, "yield_units": 6}
    assert fi.safe_work(tile, 8, 0) is None
    assert fi.safe_work(tile, 10, 0) == ["HARVEST"]


def test_empty_weed_locked_and_unknown_tiles_offer_nothing():
    assert fi.safe_work(None, 5, 5) is None
    assert fi.safe_work("LOCKED", 5, 5) is None
    assert fi.safe_work({"kind": "WEED"}, 5, 5) is None
    assert fi.safe_work({"kind": "COOP"}, 5, 5) is None
    assert fi.safe_work({"kind": "PLANT", "crop": "UNICORN", "planted_day": 0,
                         "yield_units": 9}, 5, 0) is None


def test_safe_work_never_returns_a_movement_or_market_verb():
    tiles = [
        {"kind": "COOP", "animal": "GOOSE", "yield_units": 3, "fed_today": False,
         "cared_today": False, "fertilizer_available": True},
        {"kind": "PLANT", "crop": "WHEAT", "planted_day": 0, "yield_units": 6},
        {"kind": "PLANT", "crop": "WHEAT", "planted_day": 0, "yield_units": 1},
        {"kind": "WEED"},
    ]
    for tile in tiles:
        for day in range(20):
            for wheat in (0, 1):
                work = fi.safe_work(tile, day, wheat)
                assert work is None or (isinstance(work, list) and work[0] in fi.SAFE_VERBS)
                assert work is None or work[0] not in fi.MOVEMENT_VERBS


def test_tile_at_refuses_positions_outside_the_board():
    tiles = _empty_tiles()
    tiles[0][9] = {"kind": "WEED"}
    assert fi.tile_at(tiles, [9, 0]) == {"kind": "WEED"}
    # A negative index would silently wrap to the far edge of the board.
    assert fi.tile_at(tiles, [-1, 0]) is None
    assert fi.tile_at(tiles, [0, -1]) is None
    assert fi.tile_at(tiles, [10, 0]) is None
    assert fi.tile_at(tiles, [0, 10]) is None
    assert fi.tile_at(tiles, None) is None
    assert fi.tile_at(tiles, [None, None]) is None
    assert fi.tile_at("not-a-board", [0, 0]) is None


# --------------------------------------------------------------------------- #
# The transform: only PASS, only the tile the unit stands on, never the market.
# --------------------------------------------------------------------------- #

def test_a_pass_is_filled_from_the_tile_the_unit_stands_on():
    tiles = _empty_tiles()
    _animal(tiles, (2, 3), yield_units=2, fed_today=True, cared_today=True)
    base = {"farmer": ["PASS"], "hands": [], "market": []}
    obs = make_obs(step=100, farmer=(2, 3), tiles=tiles)
    assert _overlay(base_action=base)(obs) == {"farmer": ["HARVEST"], "hands": [], "market": []}


def test_a_non_pass_action_is_never_modified():
    tiles = _empty_tiles()
    _animal(tiles, (2, 3), yield_units=2, fed_today=True, cared_today=True)
    base = {"farmer": ["WATER"], "hands": [], "market": []}
    obs = make_obs(step=100, farmer=(2, 3), tiles=tiles)
    assert _overlay(base_action=base)(obs)["farmer"] == ["WATER"]


def test_a_unit_with_nothing_under_it_keeps_passing():
    base = {"farmer": ["PASS"], "hands": [["PASS"], ["PASS"]], "market": []}
    obs = make_obs(step=100, farmer=(0, 0), hands=[(1, 1), (2, 2)])
    out = _overlay(base_action=base)(obs)
    assert out["farmer"] == ["PASS"]
    assert out["hands"] == [["PASS"], ["PASS"]]


def test_each_unit_is_offered_only_its_own_carried_wheat():
    tiles = _empty_tiles()
    _animal(tiles, (1, 1), fed_today=False, cared_today=True)
    _animal(tiles, (2, 2), fed_today=False, cared_today=True)
    base = {"farmer": ["PASS"], "hands": [["PASS"]], "market": []}
    obs = make_obs(step=100, farmer=(1, 1), hands=[(2, 2)], tiles=tiles,
                   inventories=[{}, {"WHEAT": 2}])
    out = _overlay(base_action=base)(obs)
    assert out["farmer"] == ["PASS"]          # the farmer carries no wheat
    assert out["hands"][0] == ["FEED"]        # the hand carries two wheat


def test_the_market_orders_are_returned_untouched_and_not_aliased():
    base = {"farmer": ["PASS"], "hands": [], "market": [["SELL", "WHEAT", 3], ["HIRE"]]}
    obs = make_obs(step=100)
    out = _overlay(base_action=base)(obs)
    assert out["market"] == base["market"]
    out["market"].append(["SELL", "CARROT", 1])
    assert base["market"] == [["SELL", "WHEAT", 3], ["HIRE"]]  # input not mutated


def test_the_tape_action_dict_is_not_mutated():
    tiles = _empty_tiles()
    _animal(tiles, (1, 1), yield_units=1, fed_today=True, cared_today=True)
    base = {"farmer": ["PASS"], "hands": [["PASS"]], "market": []}
    obs = make_obs(step=100, farmer=(1, 1), hands=[(5, 5)], tiles=tiles)
    _overlay(base_action=base)(obs)
    assert base == {"farmer": ["PASS"], "hands": [["PASS"]], "market": []}


def test_hand_actions_are_filled_at_their_own_index():
    tiles = _empty_tiles()
    _plant(tiles, (4, 4), crop="WHEAT", planted_day=0, yield_units=6)
    base = {"farmer": ["NORTH"], "hands": [["PASS"], ["EAST"]], "market": []}
    obs = make_obs(step=100, day=4, farmer=(9, 9), hands=[(4, 4), (0, 0)], tiles=tiles)
    out = _overlay(base_action=base)(obs)
    assert out["farmer"] == ["NORTH"]
    assert out["hands"][0] == ["HARVEST"]
    assert out["hands"][1] == ["EAST"]


def test_trace_tallies_the_conversions_by_verb():
    tiles = _empty_tiles()
    _animal(tiles, (1, 1), yield_units=1, fed_today=True, cared_today=True)
    _plant(tiles, (2, 2), crop="WHEAT", planted_day=0, yield_units=6)
    trace = []
    base = {"farmer": ["PASS"], "hands": [["PASS"]], "market": []}
    obs = make_obs(step=100, day=4, farmer=(1, 1), hands=[(2, 2)], tiles=tiles)
    _overlay(base_action=base, trace=trace)(obs)
    assert fi.tally(trace) == {"HARVEST": 2}
    assert fi.tally([]) == {}


# --------------------------------------------------------------------------- #
# Failure modes.
# --------------------------------------------------------------------------- #

def test_malformed_observations_never_raise_and_keep_the_tape_action():
    base = {"farmer": ["PASS"], "hands": [["PASS"]], "market": [["HIRE"]]}
    overlay = _overlay(base_action=copy.deepcopy(base))
    bad = [
        {}, None, [], {"farms": None}, {"farms": [None]}, {"farms": "x"},
        {"step": "x"}, {"step": None, "day": "x"},
        {"farms": [{}], "private": None}, {"farms": [{}], "private": "x"},
        {"farms": [{"tiles": "no"}]},
        {"farms": [{"tiles": [[]], "farmer": [5, 5], "hands": [None]}],
         "private": {"inventories": "x"}},
        {"farms": [{"tiles": [[None]], "farmer": [0, 9], "hands": [["a"]]}],
         "private": {"inventories": [{"WHEAT": "two"}]}},
    ]
    for obs in bad:
        out = overlay(obs)
        assert isinstance(out, dict)
        assert out["farmer"] == ["PASS"]
        assert out["market"] == [["HIRE"]]


def test_a_raising_tape_still_yields_a_legal_action():
    def broken(obs):
        raise RuntimeError("boom")

    overlay = fi.FillIdleOverlay(base_agent=broken)
    out = overlay({"farms": [{"hands": [[1, 1]]}], "player": 0})
    assert out["farmer"] == ["PASS"]
    assert out["hands"] == [["PASS"]]
    assert out["market"] == []


def test_a_detached_tape_agent_cannot_corrupt_a_second_overlay():
    # The tape keeps season state in a module-level singleton, so two overlays
    # must not share one imported module.
    first = fi.FillIdleOverlay()
    second = fi.FillIdleOverlay()
    assert first.base is not second.base


# --------------------------------------------------------------------------- #
# Season-level evidence.
# --------------------------------------------------------------------------- #

@lru_cache(maxsize=1)
def _behaviour_season(seed=0):
    """One full season of the overlay, logging the tape's raw action per turn.

    The wrapper sees the exact observation the tape saw, so ``raw[k]`` and
    ``final[k]`` are the tape's own action and the overlay's action on the SAME
    observation -- which is what makes the rewrite claim checkable.
    """
    raw, final, trace = [], [], []
    overlay = fi.FillIdleOverlay(trace=trace)
    tape = overlay.base

    def logging_base(obs):
        action = tape(obs)
        raw.append(copy.deepcopy(action))
        return action

    overlay.base = logging_base

    def agent(obs):
        action = overlay(obs)
        final.append(copy.deepcopy(action))
        return action

    opponent = load_agent(str(fi._BASELINE))
    season = run_episode_trace(agent, opponent, seed)
    return raw, final, trace, season


def _units(action):
    return [action.get("farmer") or []] + list(action.get("hands") or [])


def _passes(action):
    return sum(1 for unit in _units(action) if list(unit) == ["PASS"])


def test_enabled_overlay_only_ever_rewrites_a_pass_turn():
    """Season-level proof: no non-PASS action and no market order is touched."""
    raw, final, trace, _season = _behaviour_season(0)
    assert len(raw) == len(final)
    assert len(raw) > 700
    rewrites = 0
    for before, after in zip(raw, final, strict=True):
        # No turn gains or loses a market order.
        assert after["market"] == before["market"]
        assert len(after["market"]) == len(before["market"])
        assert len(after["hands"]) == len(before["hands"])
        before_units, after_units = _units(before), _units(after)
        for before_unit, after_unit in zip(before_units, after_units, strict=True):
            before_unit = list(before_unit)
            if before_unit == ["PASS"]:
                if list(after_unit) != before_unit:
                    rewrites += 1
                    assert after_unit[0] in fi.SAFE_VERBS
                    assert after_unit[0] not in fi.MOVEMENT_VERBS
            else:
                assert list(after_unit) == before_unit
    fills = [entry for entry in trace if entry[0] == "fill"]
    assert rewrites == len(fills)
    assert rewrites > 0


def test_enabled_overlay_reduces_the_idle_share():
    raw, final, _trace, _season = _behaviour_season(0)
    before = sum(_passes(action) for action in raw)
    after = sum(_passes(action) for action in final)
    assert after < before
    # Sanity: the tape really is idle a lot in this match, or the test proves nothing.
    assert before > 200


def test_disabled_overlay_is_byte_identical_to_the_tape():
    """The pass-through proof: with the feature off, the action stream is the tape's."""
    bare = load_agent(str(fi._BASELINE))
    variant = fi.make_agent(enabled=False)
    opponent_a = load_agent(str(fi._BASELINE))
    opponent_b = load_agent(str(fi._BASELINE))

    run_bare = run_episode_trace(bare, opponent_a, seed=0)
    run_variant = run_episode_trace(variant, opponent_b, seed=0)

    bare_actions = [step[0]["action"] for step in run_bare["steps"]]
    variant_actions = [step[0]["action"] for step in run_variant["steps"]]
    assert bare_actions == variant_actions
    assert run_bare["banks"] == run_variant["banks"]
    # Sanity: the season actually played the tape's own land purchases.
    assert any(["BUY_LAND"] in (a.get("market") or []) for a in bare_actions if a)
