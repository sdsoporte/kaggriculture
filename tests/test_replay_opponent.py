"""Unit tests for the replay-opponent adapter mechanics.

The contract that matters is the index mapping (see the module docstring): a
call at turn ``t`` must return the recorded action at index ``t + ACTION_OFFSET``,
and every failure mode must degrade to a legal PASS instead of raising. The
offset itself is proven end-to-end by the acceptance test in
``scripts/corpus_opponents.py --reproduce``; these tests pin the mechanics.
"""

import json

import pytest

from kaggriculture.agents.replay_opponent import (
    ACTION_OFFSET,
    FALLBACK_ACTION,
    load_replay,
    make_replay_opponent,
)


def _obs(step=None, day=None, hour=None, player=0):
    obs = {"player": player, "farms": [{"money": 0.0}, {"money": 0.0}]}
    if step is not None:
        obs["step"] = step
    if day is not None:
        obs["day"] = day
    if hour is not None:
        obs["hour"] = hour
    return obs


def _entry(seat, index):
    return {
        "action": {"farmer": [f"seat{seat}-index{index}"], "hands": [], "market": []},
        "observation": {"player": seat, "farms": [{"money": 0.0}, {"money": 0.0}]},
    }


def _steps(n=4):
    return [[_entry(0, i), _entry(1, i)] for i in range(n)]


def test_offset_is_one_as_established_empirically():
    # If this ever changes, the acceptance evidence in the module docstring and
    # in scripts/corpus_opponents.py must be re-derived first.
    assert ACTION_OFFSET == 1


def test_fallback_is_pass_farmer_and_empty_market():
    assert FALLBACK_ACTION == {"farmer": ["PASS"], "hands": [], "market": []}


def test_call_at_turn_t_returns_action_at_t_plus_offset():
    steps = _steps(4)
    agent = make_replay_opponent(seat=0, steps=steps)
    for turn in range(3):
        action = agent(_obs(step=turn))
        assert action["farmer"] == [f"seat0-index{turn + ACTION_OFFSET}"]


def test_each_seat_replays_its_own_recorded_actions():
    steps = _steps(4)
    seat0 = make_replay_opponent(seat=0, steps=steps)
    seat1 = make_replay_opponent(seat=1, steps=steps)
    assert seat0(_obs(step=0))["farmer"] == ["seat0-index1"]
    assert seat1(_obs(step=0))["farmer"] == ["seat1-index1"]
    assert seat0(_obs(step=2))["farmer"] == ["seat0-index3"]
    assert seat1(_obs(step=2))["farmer"] == ["seat1-index3"]


def test_missing_step_falls_back_to_day_times_24_plus_hour():
    steps = _steps(4)
    agent = make_replay_opponent(seat=0, steps=steps)
    action = agent(_obs(day=0, hour=2))  # turn 2 -> index 3
    assert action["farmer"] == ["seat0-index3"]


def test_observation_without_turn_fields_advances_a_monotonic_counter():
    steps = _steps(4)
    agent = make_replay_opponent(seat=0, steps=steps)
    assert agent({})["farmer"] == ["seat0-index1"]
    assert agent({})["farmer"] == ["seat0-index2"]
    assert agent({})["farmer"] == ["seat0-index3"]


def test_running_past_the_recording_returns_fallback_without_raising():
    steps = _steps(2)
    agent = make_replay_opponent(seat=0, steps=steps)
    assert agent(_obs(step=100)) == FALLBACK_ACTION
    assert agent(_obs(step=10_000)) == FALLBACK_ACTION
    # A counter that has run past the end also degrades instead of raising.
    counter_agent = make_replay_opponent(seat=0, steps=_steps(1))
    for _ in range(5):
        assert counter_agent({}) == FALLBACK_ACTION


@pytest.mark.parametrize("bad_entry", [
    {"action": _entry(0, 1)["action"]},                 # entry is not a list
    [],                                                 # too short for the seat
    [None, None],                                       # seat state is not a dict
    [{"observation": {}}, _entry(1, 1)],                # action key missing
    [{"action": "PASS"}, _entry(1, 1)],                 # action is not a dict
])
def test_malformed_entry_degrades_to_fallback(bad_entry):
    steps = [_entry(0, 0), bad_entry]
    agent = make_replay_opponent(seat=0, steps=steps)
    assert agent(_obs(step=0)) == FALLBACK_ACTION


def test_malformed_entry_for_one_seat_does_not_affect_the_other():
    steps = [_entry(0, 0), [{"action": "PASS"}, _entry(1, 1)]]
    seat0 = make_replay_opponent(seat=0, steps=steps)
    seat1 = make_replay_opponent(seat=1, steps=steps)
    assert seat0(_obs(step=0)) == FALLBACK_ACTION
    assert seat1(_obs(step=0))["farmer"] == ["seat1-index1"]


def test_recorded_action_is_replayed_verbatim_including_ragged_hands():
    steps = [
        [_entry(0, 0), _entry(1, 0)],
        [
            {
                "action": {
                    "farmer": ["PASS"],
                    "hands": [["EAST"], ["PASS"], ["PASS"], ["PASS"], ["DROP"]],
                    "market": [["SELL", "WHEAT", 3]],
                },
                "observation": {"player": 0},
            },
            _entry(1, 1),
        ],
    ]
    agent = make_replay_opponent(seat=0, steps=steps)
    action = agent(_obs(step=0))
    assert action["hands"] == [["EAST"], ["PASS"], ["PASS"], ["PASS"], ["DROP"]]
    assert action["market"] == [["SELL", "WHEAT", 3]]


def test_returned_action_is_a_copy_of_the_recording():
    steps = _steps(3)
    agent = make_replay_opponent(seat=0, steps=steps)
    action = agent(_obs(step=0))
    action["farmer"].append("CORRUPT")
    action["market"].append(["SELL", "WHEAT", 99])
    assert steps[1][0]["action"]["farmer"] == ["seat0-index1"]
    assert steps[1][0]["action"]["market"] == []


def test_opponent_is_stateless_across_calls_so_it_can_be_reused():
    steps = _steps(5)
    agent = make_replay_opponent(seat=0, steps=steps)
    # Same observation called twice, interleaved with an off-sequence call.
    assert agent(_obs(step=1))["farmer"] == ["seat0-index2"]
    assert agent(_obs(step=3))["farmer"] == ["seat0-index4"]
    assert agent(_obs(step=1))["farmer"] == ["seat0-index2"]


def test_path_only_construction_loads_the_replay(tmp_path):
    path = tmp_path / "replay.json"
    path.write_text(json.dumps({"steps": _steps(3)}))
    agent = make_replay_opponent(str(path), 0)
    assert agent(_obs(step=0))["farmer"] == ["seat0-index1"]


def test_load_replay_roundtrip(tmp_path):
    path = tmp_path / "replay.json"
    steps = _steps(3)
    path.write_text(json.dumps({"steps": steps}))
    assert load_replay(str(path)) == steps


def test_load_replay_rejects_missing_steps(tmp_path):
    path = tmp_path / "replay.json"
    path.write_text(json.dumps({"name": "kaggriculture"}))
    with pytest.raises(ValueError):
        load_replay(str(path))


def test_load_replay_rejects_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_replay(str(tmp_path / "absent.json"))


@pytest.mark.parametrize("seat", [2, -1, "not-a-seat"])
def test_invalid_seat_raises_at_construction(seat):
    with pytest.raises(ValueError):
        make_replay_opponent(seat=seat, steps=_steps(2))
