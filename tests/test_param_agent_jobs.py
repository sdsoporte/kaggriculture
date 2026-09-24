"""Tests for the worker job board: targeting, job derivation, routing, assignment.

These are the rules that decide whether the consumer can plant at all, so they are
tested against a synthetic board rather than an episode - the failure modes here are
silent (the engine no-ops an invalid action) and would otherwise look like bad luck.
"""

from kaggriculture.agents.param_agent import (
    _ACTION,
    GENERIC_JOB_ORDER,
    _action_for,
    assign_targets,
    build_jobs,
    shed_access_tiles,
    step_toward,
    worker_actions,
)

SHED = set(shed_access_tiles())


def plan(animals=None, crops=None, plant_days=None):
    crops = crops or {"WHEAT": 4, "STRAWBERRY": 2, "CARROT": 0, "TOMATO": 0, "MELON": 0}
    plant_days = plant_days or [0]
    return {
        "schema": "kaggriculture-plan/3",
        "land": {"quadrants": 3, "buy_days": [1, 6]},
        "hands": {"start": 4, "ramp_step": 1, "daily_target": 12},
        "crops": [
            {"crop": c, "tiles": crops[c], "plant_days": list(plant_days), "fertilize_tiles": 0}
            for c in ("WHEAT", "CARROT", "TOMATO", "STRAWBERRY", "MELON")
        ],
        "animals": animals or [
            {"animal": "COW", "count": 1, "buy_days": [3]},
            {"animal": "SHEEP", "count": 0, "buy_days": []},
            {"animal": "GOOSE", "count": 0, "buy_days": []},
        ],
        "market": {"sell_chunk_cap": 0, "sell_from_day": 12, "seed_reserve": 4},
    }


def board(open_all=True, placements=()):
    """A 10x10 grid: every tile unlocked unless ``open_all`` is false (NW only)."""
    rows: list[list] = [[None] * 10 for _ in range(10)]
    if not open_all:
        for y in range(10):
            for x in range(10):
                if x >= 5 or y >= 5:
                    rows[y][x] = "LOCKED"
    for x, y, cell in placements:
        rows[y][x] = cell
    return rows


# A plan that reserves no structure tiles: for tests that care only about crops or
# feed jobs and must not also see a BUILD job on a reserved structure slot.
NO_STRUCTURES = [
    {"animal": "COW", "count": 0, "buy_days": []},
    {"animal": "SHEEP", "count": 0, "buy_days": []},
    {"animal": "GOOSE", "count": 0, "buy_days": []},
]


def obs(tiles, *, farmer=(0, 0), hands=(), shed=None, inventories=None, seeds=None, money=3000):
    return {
        "step": 0,
        "player": 0,
        "farms": [{
            "money": money,
            "farmer": list(farmer),
            "hands": [list(h) for h in hands],
            "tiles": tiles,
            "unlocked_quadrants": ["NW"],
            "hires_today": 0,
        }],
        "private": {
            "shed": shed or {},
            "inventories": inventories or [{}],
            "seeds": seeds or {},
        },
    }


# --- routing ----------------------------------------------------------------- #

def test_step_toward_moves_in_x_first_then_y():
    assert step_toward((0, 0), (3, 0)) == "EAST"
    assert step_toward((3, 0), (0, 0)) == "WEST"
    assert step_toward((0, 0), (0, 3)) == "SOUTH"
    assert step_toward((0, 3), (0, 0)) == "NORTH"


def test_step_toward_returns_none_when_already_there():
    assert step_toward((2, 2), (2, 2)) is None


def test_every_generic_job_kind_has_an_action():
    # Totality. A missing key raises inside the agent on the first turn that job
    # exists; the engine isolates the failure and the agent is inert afterwards, so
    # the season looks like "bad luck" rather than a crash. PLANT was missing.
    missing = [kind for kind in GENERIC_JOB_ORDER if kind not in _ACTION]
    assert not missing, f"job kinds with no action: {missing}"


def test_a_plant_action_carries_its_crop():
    # Regression, and the third occurrence of this bug class in the project. The
    # engine reads action[1] as the crop and returns early without it
    # (kaggriculture.py:418-420), so ["PLANT"] alone planted nothing for a whole
    # season while looking like a valid action in every trace.
    assert _action_for("plant", ((2, 2), "WHEAT")) == ["PLANT", "WHEAT"]


def test_a_pickup_action_carries_its_item():
    assert _ACTION["pickup"] == ["PICKUP", "WHEAT"]


def test_planting_emits_the_crop_not_a_bare_verb():
    tiles = board()
    p = plan(plant_days=[0])
    o = obs(tiles, farmer=(0, 0), inventories=[{}], seeds={"WHEAT": 1})
    targets = assign_targets(p, o)
    jobs = build_jobs(p, o, day=0, targets=targets, seeds={"WHEAT": 1})
    wheat_tile = jobs["plant"][0][0]
    farmer, _ = worker_actions(p, obs(tiles, farmer=wheat_tile, inventories=[{}],
                                     seeds={"WHEAT": 1}),
                               0, targets, jobs)
    assert farmer == ["PLANT", "WHEAT"]


# --- targeting --------------------------------------------------------------- #

def test_targets_never_land_on_a_shed_access_tile():
    _, crops = assign_targets(plan(), obs(board()))
    chosen = set().union(*crops.values())
    assert not (chosen & SHED)


def test_targets_never_land_on_a_locked_tile():
    _, crops = assign_targets(plan(), obs(board(open_all=False)))
    assert all(x < 5 and y < 5 for tile in set().union(*crops.values()) for x, y in [tile])


def test_targets_are_deterministic_across_calls():
    first = assign_targets(plan(), obs(board()))
    second = assign_targets(plan(), obs(board()))
    assert first == second


def test_structures_take_the_first_tiles_and_crops_come_after():
    p = plan(animals=[
        {"animal": "COW", "count": 2, "buy_days": [3, 4]},
        {"animal": "SHEEP", "count": 0, "buy_days": []},
        {"animal": "GOOSE", "count": 0, "buy_days": []},
    ])
    structures, crops = assign_targets(p, obs(board()))
    assert len(structures) == 2
    assert not (structures & set().union(*crops.values()))


def test_a_crop_with_zero_tiles_gets_no_targets():
    _, crops = assign_targets(plan(), obs(board()))
    assert crops["CARROT"] == set()
    assert len(crops["WHEAT"]) == 4


# --- job derivation ---------------------------------------------------------- #

def test_unfed_animals_get_feed_jobs_and_fed_ones_do_not():
    tiles = board(placements=[
        (0, 0, {"kind": "PASTURE", "animal": "COW", "fed_today": False}),
        (1, 0, {"kind": "PASTURE", "animal": "COW", "fed_today": True}),
    ])
    p = plan()
    jobs = build_jobs(p, obs(tiles), day=5, targets=assign_targets(p, obs(tiles)), seeds={})
    assert jobs["feed"] == [(0, 0)]


def test_only_ready_crops_get_harvest_jobs():
    tiles = board(placements=[
        (0, 0, {"kind": "PLANT", "crop": "WHEAT", "planted_day": 0, "yield_units": 3}),
        (1, 0, {"kind": "PLANT", "crop": "WHEAT", "planted_day": 0, "yield_units": 0}),
    ])
    p = plan()
    jobs = build_jobs(p, obs(tiles), day=10, targets=assign_targets(p, obs(tiles)), seeds={})
    assert jobs["harvest"] == [(0, 0)]


def test_a_ready_wheat_in_its_window_is_watered_before_it_is_harvested():
    # `harvest_ready` is true on day 2, but the growth window runs to day 4: watering
    # now is worth 1-3 more units. Harvesting at first_yield_day is what kept revenue
    # below spend, so the board takes the window bonus first.
    tiles = board(placements=[
        (0, 0, {"kind": "PLANT", "crop": "WHEAT", "planted_day": 0, "yield_units": 1,
                "watered_today": False, "consecutive_unwatered": 0}),
    ])
    p = plan()
    jobs = build_jobs(p, obs(tiles), day=2, targets=assign_targets(p, obs(tiles)), seeds={})
    assert jobs["water"] == [(0, 0)]
    assert jobs["harvest"] == []


def test_a_wheat_that_already_took_its_last_watering_is_harvested():
    tiles = board(placements=[
        (0, 0, {"kind": "PLANT", "crop": "WHEAT", "planted_day": 0, "yield_units": 4,
                "watered_today": True, "consecutive_unwatered": 0}),
    ])
    p = plan()
    jobs = build_jobs(p, obs(tiles), day=4, targets=assign_targets(p, obs(tiles)), seeds={})
    assert jobs["harvest"] == [(0, 0)]
    assert jobs["water"] == []


def test_an_ongoing_crop_is_harvested_as_soon_as_ready():
    tiles = board(placements=[
        (0, 0, {"kind": "PLANT", "crop": "TOMATO", "planted_day": 0, "yield_units": 1,
                "watered_today": False, "consecutive_unwatered": 0}),
    ])
    p = plan()
    jobs = build_jobs(p, obs(tiles), day=8, targets=assign_targets(p, obs(tiles)), seeds={})
    assert jobs["harvest"] == [(0, 0)]


def test_a_freshly_planted_crop_is_watered_on_its_planting_day():
    # The engine's `_new_plant` starts `consecutive_unwatered` at 1 ("planting day
    # counts as unwatered") and `_daily_refresh_plants` turns the tile into a WEED
    # at >= 2, so a crop that is not watered on its planting day is dug the next
    # morning. This is the drain: the old board only watered inside the growth
    # window (WHEAT days 2-4), so every planting weeded before it could ever be
    # watered and the season produced nothing. Measured on the broken agent: day 0
    # planted 15 with 0 WATER and day 1 dug 15.
    tiles = board(placements=[
        (0, 0, {"kind": "PLANT", "crop": "WHEAT", "planted_day": 0, "yield_units": 1,
                "watered_today": False, "consecutive_unwatered": 1}),
    ])
    p = plan()
    jobs = build_jobs(p, obs(tiles), day=0, targets=assign_targets(p, obs(tiles)), seeds={})
    assert jobs["water"] == [(0, 0)]


def test_watering_outside_the_window_only_when_weeds_threaten():
    # Outside the window WATER has no growth effect, but the engine still weeds a
    # plant after two unwatered midnights. A plant watered today (clock 0) is safe
    # to skip; one that would reach 2 tonight must be watered.
    p = plan()
    safe = board(placements=[
        (0, 0, {"kind": "PLANT", "crop": "WHEAT", "planted_day": 0, "yield_units": 0,
                "watered_today": False, "consecutive_unwatered": 0}),
    ])
    at_risk = board(placements=[
        (0, 0, {"kind": "PLANT", "crop": "WHEAT", "planted_day": 0, "yield_units": 0,
                "watered_today": False, "consecutive_unwatered": 1}),
    ])
    assert build_jobs(p, obs(safe), day=1, targets=assign_targets(p, obs(safe)), seeds={})["water"] == []
    assert build_jobs(p, obs(at_risk), day=1, targets=assign_targets(p, obs(at_risk)), seeds={})["water"] == [(0, 0)]


def test_watering_is_always_asked_inside_the_crops_window():
    # WHEAT benefits on days 2-4; MELON on days 6-12. Inside the window the growth
    # bonus is the reason to water, weed risk or not.
    tiles = board(placements=[
        (0, 0, {"kind": "PLANT", "crop": "WHEAT", "planted_day": 0, "yield_units": 0,
                "watered_today": False, "consecutive_unwatered": 0}),
    ])
    p = plan()
    in_window = build_jobs(p, obs(tiles), day=3, targets=assign_targets(p, obs(tiles)), seeds={})
    assert in_window["water"] == [(0, 0)]


def test_already_watered_crops_are_not_watered_again():
    tiles = board(placements=[
        (0, 0, {"kind": "PLANT", "crop": "WHEAT", "planted_day": 0, "yield_units": 0,
                "watered_today": True}),
    ])
    p = plan()
    jobs = build_jobs(p, obs(tiles), day=3, targets=assign_targets(p, obs(tiles)), seeds={})
    assert jobs["water"] == []


def test_weeds_get_dug():
    tiles = board(placements=[(2, 2, {"kind": "WEED"})])
    p = plan()
    jobs = build_jobs(p, obs(tiles), day=5, targets=assign_targets(p, obs(tiles)), seeds={})
    assert jobs["dig"] == [(2, 2)]


def test_plants_are_only_asked_on_the_crops_plant_days():
    p = plan(plant_days=[0])
    o = obs(board(), seeds={"WHEAT": 4})
    off_day = build_jobs(p, o, day=3, targets=assign_targets(p, o), seeds={"WHEAT": 4})
    assert off_day["plant"] == []
    on_day = build_jobs(p, o, day=0, targets=assign_targets(p, o), seeds={"WHEAT": 4})
    assert len(on_day["plant"]) == 4


def test_the_board_never_asks_for_more_plants_than_the_seeds_held():
    # Invariant 2, and the reason it matters: the engine voids EVERY PLANT of a crop
    # whose turn demand exceeds its stock, so over-asking plants nothing at all.
    p = plan()
    o = obs(board(), seeds={"WHEAT": 1})
    jobs = build_jobs(p, o, day=0, targets=assign_targets(p, o), seeds={"WHEAT": 1})
    wheats = [j for j in jobs["plant"] if j[1] == "WHEAT"]
    assert len(wheats) == 1


def test_occupied_tiles_are_not_planted_on():
    tiles = board()
    p = plan()
    o = obs(tiles, seeds={"WHEAT": 4})
    targets = assign_targets(p, o)
    wheat_tile = sorted(targets[1]["WHEAT"], key=lambda t: (t[1], t[0]))[0]
    tiles[wheat_tile[1]][wheat_tile[0]] = {"kind": "PLANT", "crop": "MELON",
                                           "planted_day": 0, "yield_units": 0}
    jobs = build_jobs(p, o, day=0, targets=targets, seeds={"WHEAT": 4})
    assert wheat_tile not in [j[0] for j in jobs["plant"]]


# --- assignment -------------------------------------------------------------- #

def test_a_worker_carrying_wheat_feeds_the_nearest_unfed_animal():
    tiles = board(placements=[
        (0, 3, {"kind": "PASTURE", "animal": "COW", "fed_today": False}),
    ])
    p = plan()
    o = obs(tiles, farmer=(0, 0), inventories=[{"WHEAT": 1}], shed={"WHEAT": 5})
    farmer, _ = worker_actions(p, o, 5, assign_targets(p, o), build_jobs(
        p, o, 5, assign_targets(p, o), seeds={}))
    # (0,0) -> (0,3) is three SOUTH; the verb is the step, not the target.
    assert farmer == ["SOUTH"]


def test_a_worker_without_wheat_is_sent_to_the_shed_when_animals_are_unfed():
    # FEED consumes an item the worker carries, so the pickup trip is mandatory.
    tiles = board(placements=[
        (0, 3, {"kind": "PASTURE", "animal": "COW", "fed_today": False}),
    ])
    p = plan()
    o = obs(tiles, farmer=(0, 0), inventories=[{}], shed={"WHEAT": 5})
    farmer, _ = worker_actions(p, o, 5, assign_targets(p, o), build_jobs(
        p, o, 5, assign_targets(p, o), seeds={}))
    assert farmer == ["PICKUP"] or farmer[0] in {"EAST", "WEST", "SOUTH", "NORTH"}


def test_a_worker_standing_on_the_shed_picks_wheat_up():
    p = plan()
    tiles = board(placements=[(0, 3, {"kind": "PASTURE", "animal": "COW", "fed_today": False})])
    o = obs(tiles, farmer=(4, 4), inventories=[{}], shed={"WHEAT": 5})
    farmer, _ = worker_actions(p, o, 5, assign_targets(p, o), build_jobs(
        p, o, 5, assign_targets(p, o), seeds={}))
    assert farmer == ["PICKUP", "WHEAT"]


def test_every_unit_gets_exactly_one_action():
    tiles = board(placements=[
        (0, 3, {"kind": "PASTURE", "animal": "COW", "fed_today": False}),
        (2, 2, {"kind": "WEED"}),
    ])
    p = plan()
    o = obs(tiles, farmer=(0, 0), hands=[(1, 1), (2, 1), (9, 9)],
            inventories=[{"WHEAT": 1}, {}, {}, {}], shed={"WHEAT": 5})
    targets, jobs = assign_targets(p, o), build_jobs(p, o, 5, assign_targets(p, o), seeds={})
    farmer, hands = worker_actions(p, o, 5, targets, jobs)
    assert isinstance(farmer, list) and len(farmer) == 1
    assert len(hands) == 3
    assert all(isinstance(a, list) and len(a) == 1 for a in [farmer, *hands])


def test_two_units_never_claim_the_same_target():
    """Two unfed animals and THREE wheat-carrying workers.

    Two of them take the animals; the third must not double up on a claimed tile, so
    it falls through to PASS. Comparing the movement verbs would prove nothing - two
    workers heading for different tiles often move the same way.
    """
    tiles = board(placements=[
        (0, 3, {"kind": "PASTURE", "animal": "COW", "fed_today": False}),
        (1, 3, {"kind": "PASTURE", "animal": "COW", "fed_today": False}),
    ])
    p = plan(animals=NO_STRUCTURES)
    o = obs(tiles, farmer=(0, 0), hands=[(1, 0), (2, 0)],
            inventories=[{"WHEAT": 1}, {"WHEAT": 1}, {"WHEAT": 1}], shed={})
    targets = assign_targets(p, o)
    farmer, hands = worker_actions(p, o, 5, targets, build_jobs(p, o, 5, targets, seeds={}))
    assert [farmer, *hands][2] == ["PASS"]  # only two feed jobs existed


def test_units_with_nothing_to_do_pass():
    p = plan(animals=NO_STRUCTURES)
    o = obs(board(), farmer=(0, 0), inventories=[{}], shed={})
    targets = assign_targets(p, o)
    farmer, _ = worker_actions(p, o, 5, targets, build_jobs(p, o, 5, targets, seeds={}))
    assert farmer == ["PASS"]


# --- animal realization: the plan reserves tiles, the board builds and places --- #

def test_reserved_structure_tiles_get_a_build_job():
    # A bought animal only lands in the shed, so the structure must be BUILT before
    # it can be placed. Without this the consumer spent $1,900 on 5 animals it never
    # placed; the reserved tiles also stayed empty and unusable for crops.
    p = plan()
    o = obs(board())
    targets = assign_targets(p, o)
    structure_tile = sorted(targets[0], key=lambda t: (t[1], t[0]))[0]
    jobs = build_jobs(p, o, day=3, targets=targets, seeds={})
    assert (structure_tile, "BUILD_PASTURE") in jobs["build"]


def test_an_empty_matching_structure_gets_a_place_job():
    p = plan()
    o = obs(board())
    targets = assign_targets(p, o)
    structure_tile = sorted(targets[0], key=lambda t: (t[1], t[0]))[0]
    tiles = board(placements=[(*structure_tile, {"kind": "PASTURE"})])
    jobs = build_jobs(p, obs(tiles), day=3, targets=assign_targets(p, obs(tiles)), seeds={})
    assert (structure_tile, "COW") in jobs["place"]
    assert structure_tile not in [t for t, _ in jobs["build"]]


def test_a_worker_carrying_an_animal_places_it_on_its_structure():
    p = plan()
    o = obs(board())
    structure_tile = sorted(assign_targets(p, o)[0], key=lambda t: (t[1], t[0]))[0]
    tiles = board(placements=[(*structure_tile, {"kind": "PASTURE"})])
    o = obs(tiles, farmer=list(structure_tile), inventories=[{"COW": 1}], shed={})
    farmer, _ = worker_actions(p, o, 5, assign_targets(p, o), build_jobs(
        p, o, 5, assign_targets(p, o), seeds={}))
    assert farmer == ["PLACE", "COW"]


def test_a_worker_without_an_animal_is_sent_to_the_shed_to_pick_one_up():
    p = plan()
    o = obs(board())
    structure_tile = sorted(assign_targets(p, o)[0], key=lambda t: (t[1], t[0]))[0]
    tiles = board(placements=[(*structure_tile, {"kind": "PASTURE"})])
    o = obs(tiles, farmer=(4, 4), inventories=[{}], shed={"COW": 1})
    farmer, _ = worker_actions(p, o, 5, assign_targets(p, o), build_jobs(
        p, o, 5, assign_targets(p, o), seeds={}))
    assert farmer == ["PICKUP", "COW"]
