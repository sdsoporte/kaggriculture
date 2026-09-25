"""Synthetic tests for the daily/event-time economic extractor.

These tests pin the five mechanics the Kaggle run depends on and that are easy
to get silently wrong: the replay action offset, the day-boundary cash snapshot,
the OBSERVED unlock (not the request), primary pair filtering, and the one-day
guard. They run on tiny hand-built replays, so no Kaggle dataset or engine is
needed. Nothing here measures an economy; it pins the contract.
"""

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
ECON_PATH = ROOT / "scripts" / "corpus_economic_day.py"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None, f"cannot load {path}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


econ = _load("corpus_economic_day_tests", ECON_PATH)


# --- synthetic replay builders -------------------------------------------

def _pass():
    return {"farmer": ["PASS"], "hands": [], "market": []}


def _buy_land():
    return {"farmer": ["PASS"], "hands": [], "market": [["BUY_LAND"]]}


def _sell(units):
    return {"farmer": ["PASS"], "hands": [], "market": [["SELL", "WHEAT", units]]}


def _state(seat, day, hour, money, quads, action=None, crops=0, animals=0,
           hands=0, shed=0, carry=0):
    tiles = [[{"kind": "PLANT"}] for _ in range(crops)]
    tiles += [[{"kind": "PASTURE", "animal": "COW"}] for _ in range(animals)]
    farm = {
        "money": money,
        "unlocked_quadrants": ["NW"] + [f"Q{i}" for i in range(1, quads)],
        "hands": [{} for _ in range(hands)],
        "tiles": tiles,
        "hires_today": 0,
    }
    obs = {
        "player": seat, "day": day, "hour": hour, "farms": [None, None],
        "private": {"shed": {"WHEAT": shed}, "inventories": [{"WHEAT": carry}],
                    "seeds": {}},
    }
    obs["farms"][seat] = farm
    return {"action": action or _pass(), "observation": obs}


def _step(day, hour, m0, q0, m1, q1, a0=None, a1=None, **kw):
    return [
        _state(0, day, hour, m0, q0, action=a0, crops=kw.get("crops0", 0),
               animals=kw.get("animals0", 0), hands=kw.get("hands0", 0),
               shed=kw.get("shed0", 0), carry=kw.get("carry0", 0)),
        _state(1, day, hour, m1, q1, action=a1, crops=kw.get("crops1", 0),
               animals=kw.get("animals1", 0), hands=kw.get("hands1", 0),
               shed=kw.get("shed1", 0), carry=kw.get("carry1", 0)),
    ]


def _quad_replay(q0, q1, turns=2):
    return [_step(i, 0, 10000, q0, 10000, q1) for i in range(turns)]


def _primary_replay(unlock_day=3, last_day=6, buyer=0, per_day=2, recover=False):
    """A replay with exactly one 4q seat and one 3q seat.

    The buyer requests BUY_LAND on the same turn its observation first shows four
    quadrants, which is the offset-+1 encoding of a successful purchase. Cash
    drops $4,000 on the unlock day and is otherwise flat (or recovers +$500/day
    when ``recover`` is set).
    """
    steps = []
    for day in range(last_day + 1):
        for hour in range(per_day):
            quads = [3, 3]
            quads[buyer] = 4 if day >= unlock_day else 3
            actions = [_pass(), _pass()]
            if day == unlock_day and hour == 0:
                actions[buyer] = _buy_land()
            if recover and day >= unlock_day:
                money_buyer = 10000 - 4000 + (day - unlock_day) * 500
            elif day >= unlock_day:
                money_buyer = 10000 - 4000
            else:
                money_buyer = 10000
            monies = [10000, 10000]
            monies[buyer] = money_buyer
            steps.append(_step(day, hour, monies[0], quads[0], monies[1], quads[1],
                               a0=actions[0], a1=actions[1]))
    return steps


# --- action offset --------------------------------------------------------

def test_action_index_matches_the_observation_it_produced():
    steps = [
        _step(0, 0, 10000, 3, 10000, 3),
        _step(1, 0, 10000, 3, 10000, 3),
        _step(2, 0, 6000, 4, 10000, 3, a0=_buy_land()),
        _step(3, 0, 5900, 4, 10000, 3),
    ]
    assert econ.buy_land_indices(steps, 0) == [2]
    # action[2] produced observation[2], so the 3 -> 4 transition is observed at
    # index 2. An obs[i] -> obs[i+1] reading would wrongly expect index 3.
    assert econ.observed_unlock(steps, 0, 4) == (2, 2)
    assert econ.observed_snapshot(steps, 1, 0)["quadrants"] == 3


def test_observed_unlock_ignores_a_silently_failing_request():
    steps = [
        _step(0, 0, 10000, 3, 10000, 3),
        _step(1, 0, 10000, 3, 10000, 3, a0=_buy_land()),
        _step(2, 0, 10000, 3, 10000, 3),
    ]
    assert econ.buy_land_indices(steps, 0) == [1]
    # The request exists but never lands: the treatment is the observed state.
    assert econ.observed_unlock(steps, 0, 4) == (None, None)
    assert econ.final_quadrants(steps, 0) == 3
    assert econ.classify_pair(steps)[0] == "both3"


# --- day boundary ---------------------------------------------------------

def test_day_boundary_snapshot_is_the_last_observation_of_the_day():
    steps = [
        _step(0, 0, 10000, 3, 10000, 3),
        _step(0, 1, 6200, 3, 10000, 3, crops0=4),
        _step(1, 0, 6200, 3, 10000, 3),
        _step(1, 1, 7000, 3, 10000, 3, animals0=2, hands0=3),
    ]
    rows = econ.daily_snapshots(steps, 0)
    assert [row["day"] for row in rows] == [0, 1]
    assert rows[0]["cash"] == 6200          # last turn of day 0, not the opening 10000
    assert rows[1]["cash"] == 7000
    assert rows[0]["cash_change"] is None   # first day has no prior denominator
    assert rows[1]["cash_change"] == 800
    assert rows[1]["animals"] == 2
    assert rows[1]["hands"] == 3


# --- pair filtering -------------------------------------------------------

def test_classify_pair_labels_every_configuration_separately():
    assert econ.classify_pair(_quad_replay(4, 3)) == ("primary_one4_one3", [4, 3])
    assert econ.classify_pair(_quad_replay(3, 4)) == ("primary_one4_one3", [3, 4])
    assert econ.classify_pair(_quad_replay(4, 4))[0] == "both4"
    assert econ.classify_pair(_quad_replay(3, 3))[0] == "both3"
    assert econ.classify_pair(_quad_replay(4, 2))[0] == "one4_other_not3"
    assert econ.classify_pair(_quad_replay(2, 3))[0] == "neither_reached4"


def test_only_the_primary_pair_feeds_the_tables():
    summary = econ.new_summary()
    econ.fold_match(summary, econ.extract_match(_quad_replay(3, 3), teams=["A", "B"]))
    econ.fold_match(summary, econ.extract_match(_quad_replay(4, 4), teams=["C", "D"]))
    assert summary["primary"] == 0
    assert summary["days"] == {}
    assert summary["classes"]["both3"] == 1
    assert summary["classes"]["both4"] == 1
    econ.fold_match(summary, econ.extract_match(_primary_replay(), teams=["E", "F"]))
    assert summary["primary"] == 1
    assert summary["days"]                      # now day rows exist


def test_buyer_is_the_four_quadrant_seat_even_on_seat_one():
    match = econ.extract_match(_primary_replay(buyer=1), teams=["A", "B"])
    assert match["class"] == "primary_one4_one3"
    assert match["buyer"]["seat"] == 1
    assert match["opponent"]["seat"] == 0
    assert match["quadrants"] == [3, 4]


# --- extraction and aggregation ------------------------------------------

def test_primary_match_profiles_both_seats_in_the_same_replay():
    steps = _primary_replay(unlock_day=3, last_day=6)
    match = econ.extract_match(steps, teams=["ALPHA", "BETA"])
    assert match["buyer"]["seat"] == 0
    assert match["buyer"]["unlock_day"] == 3
    assert match["buyer"]["request_gap"] == 0
    rows = econ.pair_rows(match)
    assert [row["day"] for row in rows] == [0, 1, 2, 3, 4, 5, 6]
    assert rows[0]["gap"] == 0
    assert rows[3]["buyer_change"] == -4000     # the mechanical land purchase
    assert rows[3]["gap"] == -4000
    assert rows[4]["gap"] == -4000


def test_mechanical_purchase_dip_lands_at_event_offset_zero():
    summary = econ.new_summary()
    econ.fold_match(summary, econ.extract_match(_primary_replay(), teams=["A", "B"]))
    assert summary["primary_teams"] == {"A", "B"}
    offset_zero = summary["events"][0]
    assert offset_zero
    assert all(row["buyer_change"] == -4000 for row in offset_zero)
    # Offset -1 is pre-purchase and shows no dip.
    assert all(row["buyer_change"] == 0 for row in summary["events"][-1])


def test_distinct_teams_counts_only_the_primary_cohort():
    # A both3 match contributes classes and teams, but NOT the primary headline.
    summary = econ.new_summary()
    econ.fold_match(summary, econ.extract_match(_quad_replay(3, 3), teams=["X", "Y"]))
    econ.fold_match(summary, econ.extract_match(_primary_replay(), teams=["E", "F"]))
    assert summary["primary"] == 1
    assert summary["primary_teams"] == {"E", "F"}
    assert "X" not in summary["primary_teams"]
    report = econ.render_report(summary, "2026-09-24")
    line = next(row for row in report.splitlines() if row.startswith("distinct teams"))
    assert ": 2 " in line                    # E and F only, not the both3 pair
    assert "primary cohort" in line


def test_silent_failure_prose_is_not_inverted():
    summary = econ.new_summary()
    econ.fold_match(summary, econ.extract_match(_primary_replay(), teams=["A", "B"]))
    report = econ.render_report(summary, "2026-09-24").lower()
    assert "silent buy_land failure leaves the request but no 4-quadrant" in report
    assert "silent failure would show 4 quadrants with no request" not in report


def test_report_bounds_and_cash_labelling():
    summary = econ.new_summary()
    econ.fold_match(summary, econ.extract_match(_primary_replay(), teams=["A", "B"]))
    report = econ.render_report(summary, "2026-09-24")
    assert "2026-09-24" in report
    assert "cash" in report
    assert "not revenue" in report.lower()      # explicit non-revenue disclaimer
    assert len(report) < 20000


def test_report_with_no_primary_match_is_honest_and_short():
    summary = econ.new_summary()
    econ.fold_match(summary, econ.extract_match(_quad_replay(3, 3), teams=["A", "B"]))
    report = econ.render_report(summary, "2026-09-24")
    assert "No primary match" in report
    assert len(report) < 20000


def test_requested_sell_units_are_not_reported_as_sales():
    steps = [
        _step(0, 0, 10000, 3, 10000, 3),
        _step(1, 0, 10000, 3, 10000, 3, a0=_sell(999)),
    ]
    snapshot = econ.observed_snapshot(steps, 1, 0)
    assert "sold" not in snapshot
    assert all("sell" not in key for key in snapshot)
    # A requested SELL never moves observed cash inside the snapshot.
    assert snapshot["cash"] == 10000


def test_resources_are_read_from_tiles_and_private_state():
    steps = [
        _step(0, 0, 10000, 3, 10000, 3, crops0=5, animals0=2, hands0=4,
              shed0=7, carry0=3),
    ]
    snapshot = econ.observed_snapshot(steps, 0, 0)
    assert snapshot["crop_tiles"] == 5
    assert snapshot["animals"] == 2
    assert snapshot["hands"] == 4
    assert snapshot["shed_units"] == 7
    assert snapshot["carry_units"] == 3


def test_fully_recovering_buyer_is_detected_within_the_event_window():
    summary = econ.new_summary()
    econ.fold_match(summary, econ.extract_match(
        _primary_replay(unlock_day=2, last_day=11, recover=True), teams=["A", "B"]))
    report = econ.render_report(summary, "2026-09-24")
    assert "offset +8" in report               # gap closes exactly at +8


# --- one-day guard --------------------------------------------------------

def test_require_exactly_one_day_accepts_a_single_daily_source():
    date, source = econ.require_exactly_one_day([
        "/kaggle/input/kaggriculture-episodes-index",
        "/kaggle/input/datasets/kaggle/kaggriculture-episodes-2026-09-24",
        "/kaggle/input/datasets/raykkretzschmar/kaggriculture-reference-agents",
        "/kaggle/input/datasets/ser8147/kaggriculture-engine-tables",
    ])
    assert date == "2026-09-24"
    assert "2026-09-24" in source


def test_require_exactly_one_day_rejects_two_same_date_mounts():
    # Two matching mounts of the SAME day would read the day twice; the guard
    # must reject them, not collapse them to one date.
    with pytest.raises(econ.OneDayGuardError):
        econ.require_exactly_one_day([
            "/kaggle/input/kaggriculture-episodes-2026-09-24",
            "/kaggle/input/datasets/kaggle/kaggriculture-episodes-2026-09-24",
        ])
    with pytest.raises(econ.OneDayGuardError):
        econ.require_exactly_one_day([
            "kaggle/kaggriculture-episodes-2026-09-24",
            "kaggle/kaggriculture-episodes-2026-09-24",  # duplicated declaration
        ])


def test_require_exactly_one_day_rejects_zero_days():
    with pytest.raises(econ.OneDayGuardError):
        econ.require_exactly_one_day([
            "/kaggle/input/kaggriculture-episodes-index",
            "/kaggle/input/datasets/ser8147/kaggriculture-engine-tables",
            "/kaggle/input/datasets/raykkretzschmar/kaggriculture-reference-agents",
        ])


def test_require_exactly_one_day_rejects_two_days():
    with pytest.raises(econ.OneDayGuardError):
        econ.require_exactly_one_day([
            "/kaggle/input/kaggriculture-episodes-2026-09-23",
            "/kaggle/input/kaggriculture-episodes-2026-09-24",
        ])


def test_daily_dataset_dates_ignores_non_daily_names():
    found = econ.daily_dataset_dates([
        "kaggle/kaggriculture-episodes-index",
        "kaggle/kaggriculture-episodes-2026-09-24",
        "kaggle/kaggriculture-episodes-2026-09-24",
        "ser8147/kaggriculture-engine-tables",
    ])
    assert list(found) == ["2026-09-24"]
    assert found["2026-09-24"] == ["kaggle/kaggriculture-episodes-2026-09-24"]


def test_runtime_guard_on_a_synthetic_mounted_tree(tmp_path):
    # discover_daily_dirs + require_exactly_one_day is exactly what the notebook
    # guard cell runs, so this exercises the runtime path, not just the helper.
    (tmp_path / "kaggriculture-episodes-index").mkdir()
    nested = tmp_path / "datasets" / "kaggle" / "kaggriculture-episodes-2026-09-24"
    nested.mkdir(parents=True)
    (tmp_path / "datasets" / "ser8147" / "kaggriculture-engine-tables").mkdir(parents=True)
    found = econ.discover_daily_dirs(str(tmp_path))
    date, source = econ.require_exactly_one_day(found)
    assert date == "2026-09-24"
    assert "2026-09-24" in source          # the index dir was ignored
    # A second daily mount must stop the run before any replay is parsed.
    (tmp_path / "kaggriculture-episodes-2026-09-23").mkdir()
    with pytest.raises(econ.OneDayGuardError):
        econ.require_exactly_one_day(econ.discover_daily_dirs(str(tmp_path)))


def test_require_expected_day_must_match_the_mounted_day():
    assert econ.require_expected_day(["2026-09-24"], "2026-09-24") == "2026-09-24"
    with pytest.raises(econ.OneDayGuardError):
        econ.require_expected_day(["2026-09-23"], "2026-09-24")
    with pytest.raises(econ.OneDayGuardError):
        econ.require_expected_day(["2026-09-23", "2026-09-24"], "2026-09-24")
    with pytest.raises(econ.OneDayGuardError):
        econ.require_expected_day([], "2026-09-24")


def test_require_within_cap_refuses_to_truncate_a_single_day():
    assert econ.require_within_cap(100, 6000, "2026-09-24") == 100
    assert econ.require_within_cap(6000, 6000, "2026-09-24") == 6000
    with pytest.raises(econ.OneDayGuardError):
        econ.require_within_cap(6001, 6000, "2026-09-24")
