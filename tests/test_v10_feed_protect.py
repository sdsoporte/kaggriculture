"""Unit tests for the V10 feed-protection overlay (v10_feed_protect).

The public path needs a full season to exercise, so most tests drive the pure
helpers and the ``FeedProtectOverlay`` transform with synthetic observations.
Two season-level tests carry the load:

  * ``test_zero_extra_hands_is_byte_identical_to_tape`` proves the overlay is a
    real pass-through at ``extra_hands=0``: the tape's action stream, turn for
    turn, is unchanged. That is the evidence the tape is not disturbed.
  * ``test_extra_hand_lands_at_the_index_it_was_ordered_and_acts`` pins the
    ownership contract against the live engine: our HIRE is appended on a turn
    where the tape hires nobody, so the hand appears at exactly the recorded
    index and only that slot is driven.

The rest pin the feeding carry chain (FEED consumes a WHEAT the WORKER carries),
the at-risk hire gate, and the tape-preserving market/hand transform.
"""

from typing import Any

from kaggriculture.agents import v10_feed_protect as fp
from kaggriculture.loading import load_agent
from kaggriculture.runner import run_episode_trace

BOARD = 10


# --------------------------------------------------------------------------- #
# Synthetic observation builders.
# --------------------------------------------------------------------------- #

def _empty_tiles() -> Any:
    return [[None for _ in range(BOARD)] for _ in range(BOARD)]


def make_obs(step=50, day=2, hour=2, hands=(), money=1000.0, tiles=None,
             shed=None, inventories=None, player=0, hires_today=0, seeds=None):
    farm = {
        "money": money,
        "hands": [list(p) for p in hands],
        "farmer": [4, 4],
        "unlocked_quadrants": ["NW", "NE", "SW", "SE"],
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
            "inventories": list(inventories or []),
        },
        "market": {"prices": {}, "inventory": {}},
    }


def _animal(tiles, pos, animal="COW", fed_today=False, consecutive_unfed=0):
    x, y = pos
    tiles[y][x] = {
        "kind": "PASTURE" if animal in ("COW", "SHEEP") else "COOP",
        "animal": animal,
        "placed_day": 0,
        "yield_units": 0,
        "consecutive_unfed": consecutive_unfed,
        "fed_today": fed_today,
        "cared_today": False,
        "fertilizer_available": False,
        "pending_care_bonus": 0,
    }


def _overlay(extra_hands=1, base_action=None, **kwargs):
    base_action = base_action or {"farmer": ["PASS"], "hands": [], "market": []}
    return fp.FeedProtectOverlay(
        extra_hands=extra_hands,
        base_agent=lambda obs: base_action,
        **kwargs,
    )


# --------------------------------------------------------------------------- #
# Pure helpers.
# --------------------------------------------------------------------------- #

def test_shed_access_tiles_are_the_four_inner_corners():
    tiles = fp.shed_access_tiles()
    assert set(tiles) == {(4, 4), (5, 4), (4, 5), (5, 5)}
    assert len(tiles) == 4


def test_step_toward_shortens_the_manhattan_distance():
    assert fp.step_toward((0, 0), (2, 0)) == "EAST"
    assert fp.step_toward((2, 0), (0, 0)) == "WEST"
    assert fp.step_toward((0, 0), (0, 2)) == "SOUTH"
    assert fp.step_toward((0, 2), (0, 0)) == "NORTH"
    assert fp.step_toward((3, 3), (3, 3)) is None


def test_fib_hire_cost_matches_the_engine_indexing():
    # _fib(0)=1, _fib(1)=1, _fib(2)=2, _fib(3)=3, _fib(4)=5, ...
    assert [fp.fib_hire_cost(n) for n in range(6)] == [1, 1, 2, 3, 5, 8]


def test_unfed_animals_counts_only_hungry_animal_tiles():
    tiles = _empty_tiles()
    _animal(tiles, (3, 3), fed_today=True, consecutive_unfed=0)
    _animal(tiles, (4, 3), fed_today=False, consecutive_unfed=1)
    tiles[5][5] = {"kind": "WEED"}
    tiles[5][6] = {"kind": "PASTURE"}  # empty structure, no animal
    assert fp.unfed_animals({"tiles": tiles}) == [((4, 3), 1)]


def test_at_risk_animals_requires_a_missed_day():
    tiles = _empty_tiles()
    _animal(tiles, (3, 3), fed_today=False, consecutive_unfed=0)
    _animal(tiles, (4, 3), fed_today=False, consecutive_unfed=1)
    assert fp.at_risk_animals({"tiles": tiles}) == [((4, 3), 1)]
    assert fp.at_risk_animals({"tiles": tiles}, min_unfed=0) == [
        ((3, 3), 0), ((4, 3), 1),
    ]


def test_choose_feed_target_prefers_an_already_unfed_animal_over_a_closer_fresh_one():
    # The fresh animal at distance 1 is skipped for the day-unfed one at distance 4:
    # only the already-unfed animal escapes tonight.
    targets = [((5, 5), 0), ((0, 5), 1)]
    assert fp.choose_feed_target((4, 5), targets, set()) == (0, 5)


def test_choose_feed_target_uses_distance_within_the_same_risk():
    targets = [((0, 5), 1), ((2, 5), 1)]
    assert fp.choose_feed_target((4, 5), targets, set()) == (2, 5)


def test_choose_feed_target_skips_claimed_tiles():
    targets = [((2, 5), 1), ((0, 5), 1)]
    assert fp.choose_feed_target((4, 5), targets, {(2, 5)}) == (0, 5)
    assert fp.choose_feed_target((4, 5), targets, {(2, 5), (0, 5)}) is None


def test_nearest_shed_tile_skips_claimed_and_is_deterministic():
    assert fp.nearest_shed_tile((4, 4), set()) == (4, 4)
    assert fp.nearest_shed_tile((5, 4), {(4, 4), (5, 4)}) == (5, 5)


# --------------------------------------------------------------------------- #
# Hire gate and ownership bookkeeping.
# --------------------------------------------------------------------------- #

def _pass_action():
    return {"farmer": ["PASS"], "hands": [], "market": []}


def test_hire_gate_needs_an_at_risk_animal():
    tiles = _empty_tiles()
    _animal(tiles, (3, 3), fed_today=False, consecutive_unfed=0)  # fresh: no hire
    overlay = _overlay(base_action=_pass_action())
    obs = make_obs(step=26, day=1, hour=2, tiles=tiles)
    out = overlay.augment(obs, _pass_action())
    assert ["HIRE"] not in out["market"]

    _animal(tiles, (4, 3), fed_today=False, consecutive_unfed=1)  # at risk
    out = overlay.augment(obs, _pass_action())
    assert ["HIRE"] in out["market"]


def test_no_hire_when_the_whole_herd_is_fed():
    tiles = _empty_tiles()
    _animal(tiles, (4, 3), fed_today=True, consecutive_unfed=0)
    overlay = _overlay(base_action=_pass_action())
    out = overlay.augment(make_obs(step=26, day=1, hour=2, tiles=tiles), _pass_action())
    assert out["market"] == []


def test_no_hire_while_the_tape_hires_same_turn():
    # Ownership exactness: a same-turn tape HIRE makes our index ambiguous.
    tiles = _empty_tiles()
    _animal(tiles, (4, 3), fed_today=False, consecutive_unfed=1)
    base = {"farmer": ["PASS"], "hands": [], "market": [["HIRE"], ["HIRE"]]}
    overlay = _overlay(base_action=base)
    out = overlay.augment(make_obs(step=26, day=1, hour=2, tiles=tiles), base)
    assert out["market"] == [["HIRE"], ["HIRE"]]
    assert overlay.pending_index is None


def test_no_hire_when_the_market_budget_is_full():
    tiles = _empty_tiles()
    _animal(tiles, (4, 3), fed_today=False, consecutive_unfed=1)
    base = {"farmer": ["PASS"], "hands": [],
            "market": [["SELL", "WHEAT", 1]] * fp.MAX_ORDERS}
    overlay = _overlay(base_action=base)
    out = overlay.augment(make_obs(step=26, day=1, hour=2, tiles=tiles), base)
    assert len(out["market"]) == fp.MAX_ORDERS
    assert ["HIRE"] not in out["market"]


def test_no_hire_at_the_last_hour_of_the_day():
    # A hand hired at h23 is wiped by the midnight reset before it can act.
    tiles = _empty_tiles()
    _animal(tiles, (4, 3), fed_today=False, consecutive_unfed=1)
    overlay = _overlay(base_action=_pass_action())
    obs = make_obs(step=47, day=1, hour=23, tiles=tiles)
    out = overlay.augment(obs, _pass_action())
    assert ["HIRE"] not in out["market"]


def test_pending_hire_resolves_to_the_index_it_was_ordered_at():
    overlay = _overlay()
    overlay.pending_index = 4
    overlay._resolve_pending(roster_len=5)
    assert overlay.owned == {4}
    assert overlay.pending_index is None
    # hire_events is the EMISSION report, written when we order (see augment), so
    # resolving a pending hire must leave it alone. Ownership confirmation goes to
    # the trace via _note, not into the labour report.
    assert overlay.hire_events == []


def test_failed_hire_allows_a_retry_instead_of_claiming_a_tape_hand():
    overlay = _overlay()
    overlay.pending_index = 5
    overlay._resolve_pending(roster_len=5)  # the roster did not grow
    assert overlay.owned == set()
    assert overlay.pending_index is None


def test_midnight_reset_drops_ownership_so_the_hand_is_rehired():
    overlay = _overlay()
    overlay.owned = {3}
    overlay.pending_index = 4
    overlay.augment(make_obs(step=48, day=2, hour=0), _pass_action())
    assert overlay.owned == set()
    assert overlay.pending_index is None


# --------------------------------------------------------------------------- #
# The tape-preserving transform.
# --------------------------------------------------------------------------- #

def test_zero_extra_hands_returns_the_base_action_untouched():
    base = {"farmer": ["NORTH"], "hands": [["WATER"]], "market": [["HIRE"]]}
    overlay = _overlay(extra_hands=0, base_action=base)
    assert overlay(make_obs(step=266, day=11, hour=2)) == base


def test_only_the_owned_hand_is_rewritten_and_the_market_is_appended_to():
    tiles = _empty_tiles()
    # Unfed but not at risk: the drive acts, the hire gate stays shut.
    _animal(tiles, (6, 4), fed_today=False, consecutive_unfed=0)
    base = {
        "farmer": ["PASS"],
        "hands": [["WATER"], ["HARVEST"], ["PASS"]],
        "market": [["SELL", "WHEAT", 3]],
    }
    overlay = _overlay(base_action=base)
    overlay.owned = {2}
    obs = make_obs(step=50, day=2, hour=2, hands=[(0, 0), (0, 0), (4, 5)],
                   tiles=tiles, shed={"WHEAT": 4},
                   inventories=[{}, {}, {}, {}])
    out = overlay.augment(obs, base)
    assert out["farmer"] == ["PASS"]
    assert out["hands"][0] == ["WATER"]      # tape hand, untouched
    assert out["hands"][1] == ["HARVEST"]    # tape hand, untouched
    assert out["hands"][2] == ["PICKUP", "WHEAT"]  # ours, standing on (4,5)
    assert out["market"] == [["SELL", "WHEAT", 3]]
    # The overlay must not mutate the dict the tape handed it.
    assert base["hands"][2] == ["PASS"]
    assert base["market"] == [["SELL", "WHEAT", 3]]


def test_a_hand_carrying_wheat_on_an_unfed_animal_feeds_it():
    tiles = _empty_tiles()
    _animal(tiles, (6, 4), fed_today=False, consecutive_unfed=1)
    overlay = _overlay()
    action, target = overlay._feed_action(
        (6, 4), {"WHEAT": 2}, fp.unfed_animals({"tiles": tiles}), 0, set()
    )
    assert action == ["FEED"]
    assert target == (6, 4)


def test_a_hand_carrying_wheat_walks_toward_the_animal():
    overlay = _overlay()
    targets = [((6, 4), 1)]
    action, target = overlay._feed_action((0, 4), {"WHEAT": 1}, targets, 0, set())
    assert action == ["EAST"]
    assert target == (6, 4)
    action, target = overlay._feed_action((0, 4), {}, targets, 0, set())
    assert action == ["PASS"]
    assert target is None


def test_a_hand_without_wheat_walks_to_the_shed_then_picks_up():
    overlay = _overlay()
    targets = [((0, 0), 1)]
    action, target = overlay._feed_action((5, 4), {}, targets, 3, set())
    assert action == ["PICKUP", "WHEAT"]
    assert target is None

    action, target = overlay._feed_action((7, 4), {}, targets, 3, set())
    assert action in (["WEST"],)
    assert target == (5, 4)


def test_a_hand_without_wheat_or_shed_stock_passes():
    overlay = _overlay()
    action, target = overlay._feed_action((7, 4), {}, [((0, 0), 1)], 0, set())
    assert action == ["PASS"]
    assert target is None


def test_a_fed_herd_gives_the_extra_hand_nothing_to_do():
    tiles = _empty_tiles()
    _animal(tiles, (6, 4), fed_today=True, consecutive_unfed=0)
    overlay = _overlay()
    overlay.owned = {0}
    base = {"farmer": ["PASS"], "hands": [["PLANT", "WHEAT"]], "market": []}
    obs = make_obs(step=50, day=2, hour=2, hands=[(6, 4)], tiles=tiles,
                   inventories=[{}, {"WHEAT": 2}])
    out = overlay.augment(obs, base)
    assert out["hands"][0] == ["PASS"]


def test_owned_index_past_the_tape_hand_list_is_grown_not_dropped():
    # The tape emits fewer hand actions than the live roster; our appended hand
    # must still get its own slot rather than being silently dropped.
    tiles = _empty_tiles()
    _animal(tiles, (6, 4), fed_today=False, consecutive_unfed=1)
    base = {"farmer": ["PASS"], "hands": [["PASS"]], "market": []}
    overlay = _overlay(base_action=base)
    overlay.owned = {2}
    obs = make_obs(step=50, day=2, hour=2, hands=[(0, 0), (0, 0), (6, 4)],
                   tiles=tiles, shed={"WHEAT": 2},
                   inventories=[{}, {}, {}, {"WHEAT": 1}])
    out = overlay.augment(obs, base)
    assert len(out["hands"]) == 3
    assert out["hands"][0] == ["PASS"]     # tape hand, untouched
    assert out["hands"][1] == ["PASS"]     # grown placeholder
    assert out["hands"][2] == ["FEED"]     # ours


# --------------------------------------------------------------------------- #
# Safety and configuration.
# --------------------------------------------------------------------------- #

def test_malformed_observations_never_raise():
    base = {"farmer": ["PASS"], "hands": [], "market": []}
    overlay = _overlay(base_action=base)
    for bad in ({}, None, {"farms": None}, {"farms": [None]}, {"step": "x"},
                {"farms": [{}], "private": None}, {"farms": [{}], "private": {"shed": None}}):
        out = overlay(bad)
        assert isinstance(out, dict)
        assert "farmer" in out and "market" in out


def test_malformed_observation_reaches_the_tape_only_once():
    seen = []

    def exploding(obs):
        seen.append(obs)
        raise RuntimeError("boom")

    overlay = fp.FeedProtectOverlay(extra_hands=1, base_agent=exploding)
    out = overlay({"not": "an observation"})
    assert isinstance(out, dict)
    assert out["market"] == []
    assert seen == [{"not": "an observation"}]


def test_make_agent_rejects_bad_parameters():
    import pytest

    with pytest.raises(ValueError):
        fp.make_agent(extra_hands=-1)
    with pytest.raises(ValueError):
        fp.make_agent(extra_hands=True)
    with pytest.raises(ValueError):
        fp.make_agent(latest_hire_hour=25)
    with pytest.raises(ValueError):
        fp.make_agent(hire_min_consecutive_unfed=-1)


# --------------------------------------------------------------------------- #
# Season-level evidence.
# --------------------------------------------------------------------------- #

def test_zero_extra_hands_is_byte_identical_to_tape():
    """The proof the tape is undisturbed: pass-through reproduces it exactly."""
    bare = load_agent(str(fp._BASELINE))
    variant = fp.make_agent(extra_hands=0)
    opponent_a = load_agent(str(fp._BASELINE))
    opponent_b = load_agent(str(fp._BASELINE))

    run_bare = run_episode_trace(bare, opponent_a, seed=0)
    run_variant = run_episode_trace(variant, opponent_b, seed=0)

    bare_actions = [step[0]["action"] for step in run_bare["steps"]]
    variant_actions = [step[0]["action"] for step in run_variant["steps"]]
    assert bare_actions == variant_actions
    # Sanity: the season actually played the tape's land purchases and hires.
    assert any(["BUY_LAND"] in (a.get("market") or []) for a in bare_actions)
    assert any(["HIRE"] in (a.get("market") or []) for a in bare_actions)


def test_extra_hand_lands_at_the_index_it_was_ordered_and_acts():
    """Ownership against the live engine: the hand appears where we recorded it."""
    trace = []
    variant = fp.make_agent(extra_hands=1, trace=trace)
    opponent = load_agent(str(fp._BASELINE))
    run = run_episode_trace(variant, opponent, seed=0)
    steps = run["steps"]

    hires = [event for event in trace if event and event[0] == "hire"]
    owned = [event for event in trace if event and event[0] == "own_hand"]
    assert hires, "the overlay never hired an extra hand in a full season"
    assert owned, "the overlay never took ownership of a hand"

    # The reported index is the roster length at the turn we ordered, and the
    # roster grows by exactly that hand the next turn.
    first = hires[0]
    day, hour, index, _cost = first[1], first[2], first[3], first[4]
    order_step = day * 24 + hour
    farm_then = steps[order_step][0]["observation"]["farms"][0]
    assert len(farm_then["hands"]) == index
    farm_next = steps[order_step + 1][0]["observation"]["farms"][0]
    assert len(farm_next["hands"]) > index
    assert index in {event[1] for event in owned}

    # Our hand was actually driven: at least one of its actions is a real verb.
    driven = [
        event for event in trace
        if event and event[0] == "act" and event[2][0] in ("FEED", "PICKUP")
    ]
    assert driven, "the extra hand never carried wheat or fed an animal"
