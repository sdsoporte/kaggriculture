"""Unit and season tests for the stage-1 roster-owner agent (``roster_owner``).

The public path needs a full season (~4s) to exercise, so most tests drive the
pure helpers and the ``RosterOwner`` transform with synthetic observations. The
season-level tests are the ones that matter most:

  * ``test_disabled_agent_is_byte_identical_to_the_tape`` - the control: with the
    board off, the action stream is turn-for-turn the incumbent.
  * ``test_full_season_lands_the_ownership_boundaries`` - structural landing:
    every hand index is ours, hires follow the plan curve, only gated land is
    bought, our plants are watered on their planting day (next-day aliveness),
    the order cap holds, no animal action, and the agent never crashes.

The rest pin the engine traps and the ownership boundaries: the farmer comes
from the tape while the hands never do, the tape's HIRE/off-mix seed orders are
replaced, the land gate is net of the tape's same-turn spend, and the plan covers
the whole workable board.
"""

import importlib
from collections import Counter
from typing import Any

from kaggriculture.loading import load_agent
from kaggriculture.runner import run_episode_trace

ro: Any = importlib.import_module("kaggriculture.agents.roster_owner")

BOARD = 10
TURNS_PER_DAY = 24
WORKABLE = set(ro.WHOLE_FARM_TILES)
# The verbs our hands may emit: the se_loop crop board plus movement and PASS.
HAND_VERBS = {"PLANT", "WATER", "HARVEST", "DIG", "DROP", "PICKUP",
              "NORTH", "SOUTH", "EAST", "WEST", "PASS"}


# --------------------------------------------------------------------------- #
# Synthetic observation builders.
# --------------------------------------------------------------------------- #

def _locked_outside(unlocked):
    unlocked = set(unlocked)
    tiles: list[list[Any]] = []
    for y in range(BOARD):
        row: list[Any] = []
        for x in range(BOARD):
            row.append(None if ro.sl.quadrant_of(x, y) in unlocked else "LOCKED")
        tiles.append(row)
    return tiles


def make_obs(step=50, day=2, hour=2, hands=(), unlocked=("NW",), money=20000.0,
             tiles=None, seeds=None, hires_today=0, player=0, inventories=None,
             shed=None):
    farm = {
        "money": money,
        "hands": [list(p) for p in hands],
        "farmer": [4, 4],
        "unlocked_quadrants": list(unlocked),
        "hires_today": hires_today,
        "tiles": tiles if tiles is not None else _locked_outside(unlocked),
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


def _owner(action=None, **kwargs):
    action = action if action is not None else {"farmer": ["PASS"], "hands": [],
                                                "market": []}
    kwargs.setdefault("mix", {"WHEAT": 4})
    return ro.RosterOwner(base_agent=lambda obs: action, **kwargs)


def _wheat_tile(owner, unlocked=("NW",)):
    for tile, crop in owner.plan:
        if crop == "WHEAT" and ro.sl.quadrant_of(tile[0], tile[1]) in set(unlocked):
            return tile
    raise AssertionError("no WHEAT plan tile")


# --------------------------------------------------------------------------- #
# The plan covers the whole board.
# --------------------------------------------------------------------------- #

def test_whole_farm_plan_covers_every_workable_tile_once():
    assert len(WORKABLE) == 96  # 4 quadrants of 24, minus the 4 shed-access tiles
    assert len(ro.WHOLE_FARM_TILES) == 96
    assert len(set(ro.WHOLE_FARM_TILES)) == 96
    for tile in WORKABLE:
        assert tile not in set(ro.sl.shed_access_tiles())
    quadrants = {ro.sl.quadrant_of(x, y) for (x, y) in WORKABLE}
    assert quadrants == {"NW", "NE", "SW", "SE"}


def test_plan_crop_counts_match_the_mix():
    owner = _owner(mix={"WHEAT": 40, "CARROT": 20})
    # The plan covers every workable tile; its first sum(mix) entries are the
    # mix's own tile block (the planting budget), and the rest cycle the mix so
    # a newly unlocked quadrant still has tiles assigned to it.
    assert len(owner.plan) == 96
    block = Counter(crop for _, crop in owner.plan[:60])
    assert block == {"WHEAT": 40, "CARROT": 20}
    assert set(owner.plan_map) == WORKABLE
    # A tile keeps its crop across turns: the plan is static.
    assert dict(owner.plan) == owner.plan_map


def test_hand_curve_is_the_tape_curve():
    assert len(ro.TAPE_HAND_CURVE) == 30
    assert max(ro.TAPE_HAND_CURVE) == 12
    assert all(isinstance(n, int) and n >= 0 for n in ro.TAPE_HAND_CURVE)


def test_wanted_seeds_ignores_locked_quadrants():
    owner = _owner(mix={"WHEAT": 4})
    tiles = _locked_outside(("NW",))
    want = owner._wanted_seeds(tiles)
    # NW holds 24 workable tiles; the mix caps the request at 4 seeds.
    assert want == {"WHEAT": 4}
    # A fully locked board wants nothing.
    assert owner._wanted_seeds(_locked_outside(())) == {"WHEAT": 0}


# --------------------------------------------------------------------------- #
# Hires: ours, per plan curve, from the ladder base, under the order cap.
# --------------------------------------------------------------------------- #

def test_hires_follow_the_plan_curve():
    owner = _owner(hand_curve=(5,), hire_per_turn=3)
    action = {"farmer": ["PASS"], "hands": [], "market": []}
    obs = make_obs(step=1, day=0, hour=1, hands=[], money=20000)
    assert owner.augment(obs, action)["market"].count(["HIRE"]) == 3
    obs2 = make_obs(step=1, day=0, hour=1, hands=[(5, 5)] * 3, money=20000)
    assert owner.augment(obs2, action)["market"].count(["HIRE"]) == 2


def test_hires_stop_at_the_daily_target():
    owner = _owner(hand_curve=(4,))
    action = {"farmer": ["PASS"], "hands": [], "market": []}
    obs = make_obs(step=1, day=0, hour=1, hands=[(5, 5)] * 4, money=20000)
    assert ["HIRE"] not in owner.augment(obs, action)["market"]


def test_hire_happens_at_the_base_of_the_ladder():
    """``hires_today`` starts at 0 because the tape's own HIRE orders are gone."""
    owner = _owner(hand_curve=(3,), hire_per_turn=3)
    action = {"farmer": ["PASS"], "hands": [], "market": []}
    obs = make_obs(step=1, day=0, hour=0, hands=[], money=1000, hires_today=0)
    assert owner.augment(obs, action)["market"].count(["HIRE"]) == 3  # fib 1+1+2


def test_no_hires_when_the_wallet_is_empty():
    owner = _owner(hand_curve=(5,), hire_per_turn=3)
    action = {"farmer": ["PASS"], "hands": [], "market": []}
    obs = make_obs(step=1, day=0, hour=0, hands=[], money=0)
    assert ["HIRE"] not in owner.augment(obs, action)["market"]


def test_tape_hire_orders_are_stripped_and_replaced():
    owner = _owner(hand_curve=(0,))  # target 0: we add nothing
    base = {"farmer": ["PASS"], "hands": [],
            "market": [["HIRE"], ["HIRE"], ["SELL", "WHEAT", 1]]}
    obs = make_obs(step=1, day=0, hour=1, hands=[])
    market = owner.augment(obs, base)["market"]
    assert market.count(["HIRE"]) == 0
    assert ["SELL", "WHEAT", 1] in market


def test_hire_orders_never_break_the_order_cap():
    owner = _owner(hand_curve=(12,), hire_per_turn=12)
    base = {"farmer": ["PASS"], "hands": [],
            "market": [["SELL", "WHEAT", 1]] * 8}
    obs = make_obs(step=1, day=0, hour=1, hands=[], money=20000)
    market = owner.augment(obs, base)["market"]
    assert len(market) <= ro.MAX_ORDERS
    assert market[:8] == base["market"][:8]  # budget pressure drops OURS, not the tape's


# --------------------------------------------------------------------------- #
# Land: plan days, funding gate net of the tape's same-turn spend, SE extension.
# --------------------------------------------------------------------------- #

def test_land_gate_requires_the_plan_day_and_the_reserve():
    owner = _owner(land_days=(6, 11, 11), land_reserve=100)
    assert owner._append_land([], 5, ["NW"], 50000) is False     # before day 6
    assert owner._append_land([], 6, ["NW"], 1099) is False      # $1 short of reserve
    assert owner._append_land([], 6, ["NW"], 1100) is True
    assert owner._append_land([["SELL", "WHEAT", 1]] * 10, 6, ["NW"], 50000) is False


def test_land_is_not_bought_when_a_quadrant_is_already_free():
    owner = _owner(land_days=(6,))
    assert owner._append_land([], 6, ["NW"], 50000) is True
    # NE already unlocked: the next paid quadrant is SW, whose day is 11.
    assert owner._append_land([], 6, ["NW", "NE"], 50000) is False


def test_land_gate_is_net_of_the_tape_same_turn_spend():
    owner = _owner(land_days=(6,), land_reserve=0)
    base = {"farmer": ["PASS"], "hands": [],
            "market": [["BUY_SEED", "WHEAT", 10]]}  # the tape spends $100 first
    obs = make_obs(step=6 * 24 + 5, day=6, hour=5, hands=[], money=1099)
    assert ["BUY_LAND"] not in owner.augment(obs, base)["market"]
    quiet = {"farmer": ["PASS"], "hands": [], "market": []}
    assert ["BUY_LAND"] in owner.augment(obs, quiet)["market"]


def test_se_extension_needs_three_quadrants_and_funding():
    owner = _owner(land_days=(6, 11, 11), land_reserve=100)
    assert owner._append_se_extension([], 11, ["NW", "NE"], 50000) is False
    assert owner._append_se_extension([], 10, ["NW", "NE", "SW"], 50000) is False
    assert owner._append_se_extension([], 11, ["NW", "NE", "SW"], 4099) is False
    assert owner._append_se_extension([], 11, ["NW", "NE", "SW"], 4100) is True


def test_tape_buy_land_is_stripped_when_we_own_land():
    owner = _owner(land_days=(6, 11, 11), land_reserve=100)
    base = {"farmer": ["PASS"], "hands": [], "market": [["BUY_LAND"]]}
    obs = make_obs(step=2 * 24 + 5, day=2, hour=5, hands=[], money=20000)
    assert ["BUY_LAND"] not in owner.augment(obs, base)["market"]


# --------------------------------------------------------------------------- #
# Seeds: we own planting, so off-mix seed orders go; the mix's seeds stay.
# --------------------------------------------------------------------------- #

def test_off_mix_seed_orders_are_stripped_and_mix_seeds_kept():
    owner = _owner(mix={"WHEAT": 4})
    base = {"farmer": ["PASS"], "hands": [],
            "market": [["BUY_SEED", "MELON", 5], ["BUY_SEED", "WHEAT", 5]]}
    obs = make_obs(step=50, day=2, hour=6, hands=[], seeds={"WHEAT": 0})
    market = owner.augment(obs, base)["market"]
    assert ["BUY_SEED", "MELON", 5] not in market
    assert any(o[0] == "BUY_SEED" and o[1] == "WHEAT" for o in market)


# --------------------------------------------------------------------------- #
# Ownership boundaries.
# --------------------------------------------------------------------------- #

def test_farmer_comes_from_the_tape_and_hands_never_do():
    tile = (3, 3)
    owner = _owner(mix={"WHEAT": 4})
    owner.plan = [(tile, "WHEAT")]
    owner.plan_map = dict(owner.plan)
    tiles = _locked_outside(("NW",))
    base = {"farmer": ["NORTH"],
            "hands": [["PLANT", "MELON"], ["PLANT", "MELON"]],
            "market": []}
    obs = make_obs(step=50, day=5, hour=6, hands=[tile, tile], tiles=tiles,
                   seeds={"WHEAT": 5}, inventories=[{}, {}, {}])
    out = owner.augment(obs, base)
    assert out["farmer"] == ["NORTH"]                    # the tape's farmer survives
    assert ["PLANT", "MELON"] not in out["hands"]        # the tape's hands do not
    assert out["hands"][0] == ["PLANT", "WHEAT"]         # the board planted its crop


def test_every_hand_index_is_rewritten_from_the_board():
    tile = (3, 3)
    owner = _owner(mix={"WHEAT": 4})
    owner.plan = [(tile, "WHEAT")]
    owner.plan_map = dict(owner.plan)
    tiles = _locked_outside(("NW",))
    base = {"farmer": ["PASS"],
            "hands": [["PLANT", "MELON"]] * 3,
            "market": []}
    obs = make_obs(step=50, day=5, hour=6, hands=[tile, tile, tile], tiles=tiles,
                   seeds={"WHEAT": 5}, inventories=[{}, {}, {}, {}])
    out = owner.augment(obs, base)
    assert len(out["hands"]) == 3
    assert all(h != ["PLANT", "MELON"] for h in out["hands"])
    assert all(h[0] in HAND_VERBS for h in out["hands"])


def test_tape_action_is_never_mutated_and_hands_are_sized_to_the_roster():
    tile = (3, 3)
    owner = _owner(mix={"WHEAT": 4})
    owner.plan = [(tile, "WHEAT")]
    owner.plan_map = dict(owner.plan)
    base = {"farmer": ["SOUTH"], "hands": [["WATER"]], "market": []}
    obs = make_obs(step=50, day=5, hour=6, hands=[tile], tiles=_locked_outside(("NW",)),
                   seeds={"WHEAT": 5}, inventories=[{}, {}])
    out = owner.augment(obs, base)
    assert base["hands"] == [["WATER"]]   # untouched
    assert base["farmer"] == ["SOUTH"]
    assert len(out["hands"]) == 1


def test_malformed_observations_never_raise():
    owner = _owner()
    for bad in ({}, None, {"farms": None}, {"farms": [None]}, {"step": "x"},
                {"farms": [{}], "private": None}, {"farms": [{"tiles": None}]}):
        out = owner(bad)
        assert isinstance(out, dict)
        assert "farmer" in out and "market" in out


# --------------------------------------------------------------------------- #
# Season-level evidence.
# --------------------------------------------------------------------------- #

def _actions(run, seat=0):
    return [step[seat].get("action") for step in run["steps"]]


def _plant_events(trace):
    """Our own PLANT events as ``(tile, crop, day)`` from the board's trace."""
    return [(e[1], e[2], e[3]) for e in trace if e[0] == "plant"]


def _alive_next_day(run, tile, planted_day, seat=0):
    for step in run["steps"]:
        obs = step[seat]["observation"]
        if int(obs.get("day", 0)) != planted_day + 1:
            continue
        cell = obs["farms"][seat]["tiles"][tile[1]][tile[0]]
        return (isinstance(cell, dict) and cell.get("kind") == "PLANT"
                and cell.get("planted_day") == planted_day)
    return False


def test_disabled_agent_is_byte_identical_to_the_tape():
    """The control: with the board off, the incumbent action stream is reproduced."""
    bare = load_agent(str(ro._BASELINE))
    disabled = ro.make_agent(enabled=False)
    run_bare = run_episode_trace(bare, load_agent(str(ro._BASELINE)), seed=0)
    run_disabled = run_episode_trace(disabled, load_agent(str(ro._BASELINE)), seed=0)
    assert _actions(run_bare) == _actions(run_disabled)
    assert any(["BUY_LAND"] in (a.get("market") or []) for a in _actions(run_bare))


def test_board_disabled_is_byte_identical_to_the_tape():
    bare = load_agent(str(ro._BASELINE))
    off = ro.make_agent(board=False)
    run_bare = run_episode_trace(bare, load_agent(str(ro._BASELINE)), seed=0)
    run_off = run_episode_trace(off, load_agent(str(ro._BASELINE)), seed=0)
    assert _actions(run_bare) == _actions(run_off)


def test_full_season_lands_the_ownership_boundaries():
    """Structural landing on a real episode, plus the ownership/trap assertions."""
    trace: list = []
    agent = ro.make_agent(trace=trace)
    run = run_episode_trace(agent, load_agent(str(ro._BASELINE)), seed=0)
    actions = _actions(run)
    steps = run["steps"]

    # 1. Zero crashes: a crash would show as the silent tape pass-through.
    assert not [e for e in trace if e[0] == "crash"], "the agent crashed into a pass-through"

    # 2. The shared 10-order budget held on every single turn.
    for action in actions:
        assert len(action.get("market") or []) <= ro.MAX_ORDERS

    # 3. Every hand action is ours and only the crop board's verbs are used.
    for action in actions:
        for hand in action.get("hands") or []:
            assert hand and hand[0] in HAND_VERBS
    # ... and no animal maintenance: stage 1 places/handles no animals.
    assert not [e for e in trace if e[0] in ("feed", "care", "collect", "place")]

    # 4. Hires follow the plan curve (midday roster vs curve, tolerance for a
    #    day the wallet could not fund the full roster).
    for day in range(30):
        index = day * TURNS_PER_DAY + 12
        if index >= len(steps):
            break
        roster = len(steps[index][0]["observation"]["farms"][0]["hands"])
        assert roster >= ro.TAPE_HAND_CURVE[day] - 6, (day, roster)
    reach = sum(
        1 for day in range(30)
        if day * TURNS_PER_DAY + 12 < len(steps)
        and len(steps[day * TURNS_PER_DAY + 12][0]["observation"]["farms"][0]["hands"])
        >= ro.TAPE_HAND_CURVE[day] - 1
    )
    assert reach >= 20

    # 5. Land: bought in engine order (NE, SW, SE), only on/after the plan's
    #    buy days, and only one quadrant at a time. The gate itself also requires
    #    the price plus reserve net of the tape's same-turn spend; on seed 0 the
    #    weak early economy delays the buys to days 15/20/26 (the recorded stage-1
    #    result), so this asserts the ordered, plan-day-gated structure.
    buys = []
    for i in range(1, len(steps)):
        action = steps[i][0].get("action") or {}
        if ["BUY_LAND"] in (action.get("market") or []):
            obs = steps[i][0]["observation"]
            buys.append((int(obs.get("day", 0)),
                         len(obs["farms"][0]["unlocked_quadrants"])))
    assert len(buys) == 3
    for index, (day, quadrants) in enumerate(buys):
        assert day >= ro.PLAN_LAND_DAYS[index]
        assert quadrants == index + 2  # NW plus one per paid purchase
    assert steps[-1][0]["observation"]["farms"][0]["unlocked_quadrants"] == \
        ["NW", "NE", "SW", "SE"]

    # 6. New tiles planted by our hands were watered on the planting day: the
    #    engine keeps an unwatered planting-day crop alive only if WATER landed.
    events = _plant_events(trace)
    assert events, "the board never planted"
    waters = {(e[1], e[2]) for e in trace if e[0] == "water"}
    for tile, _crop, day in events:
        assert (tile, day) in waters, f"{tile} lacked a planting-day WATER on day {day}"
        assert _alive_next_day(run, tile, day), f"{tile} died the night of day {day}"

    # 7. Zero escapes: no animal tile ever reverts to a bare structure.
    for step in steps:
        tiles = step[0]["observation"]["farms"][0]["tiles"]
        for row in tiles:
            for cell in row:
                if isinstance(cell, dict) and cell.get("kind") in ("COOP", "PASTURE"):
                    assert "animal" in cell  # a bare structure is an escaped animal
