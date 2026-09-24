"""Unit tests for the V10 roster-swap transform (v10_mix).

The public path (``make_agent``) needs a full season simulation to exercise, so
these test the pure transform directly. They exist because the first version
shipped a real bug: the BUILD swap emitted the STRUCTURE NAME instead of the
build VERB (``["COOP"]`` instead of ``["BUILD_COOP"]``). The engine ignores an
unknown verb, so the goose variant silently produced zero structures and zero
animals - exactly the silent failure `scripts/assert_overlay.py` exists to catch,
and cheap to pin down here.
"""

import pytest

from kaggriculture.agents.v10_mix import (
    ANIMALS,
    BUILDS,
    PASTURE_ANIMALS,
    _swap_market,
    _swap_worker,
    make_agent,
)


def test_place_payload_swaps_to_the_target_species():
    assert _swap_worker(["PLACE", "COW", 1], PASTURE_ANIMALS, "SHEEP", None) == ["PLACE", "SHEEP", 1]


def test_pickup_payload_swaps_too():
    assert _swap_worker(["PICKUP", "SHEEP"], PASTURE_ANIMALS, "COW", None) == ["PICKUP", "COW"]


def test_unsourced_species_is_left_alone():
    # Geese are not pasture animals, so a pasture-only swap must not touch them.
    assert _swap_worker(["PLACE", "GOOSE"], PASTURE_ANIMALS, "COW", None) == ["PLACE", "GOOSE"]


def test_build_swap_emits_the_build_verb_not_the_structure_name():
    # Regression: ["COOP"] is not an action verb, so the engine silently ignores it.
    assert _swap_worker(["BUILD_PASTURE"], ANIMALS, "GOOSE", "COOP") == ["BUILD_COOP"]
    assert _swap_worker(["BUILD_COOP"], PASTURE_ANIMALS, "SHEEP", "PASTURE") == ["BUILD_PASTURE"]


def test_every_build_swap_produces_the_structure_the_target_needs():
    # Both source verbs must end up as the structure the target lives in. Note the
    # swap is idempotent for the verb that already matches: BUILD_COOP stays
    # BUILD_COOP when the target is a goose.
    for verb in BUILDS:
        assert _swap_worker([verb], ANIMALS, "GOOSE", "COOP") == ["BUILD_COOP"]
        assert _swap_worker([verb], PASTURE_ANIMALS, "SHEEP", "PASTURE") == ["BUILD_PASTURE"]


def test_build_left_alone_when_no_structure_is_requested():
    assert _swap_worker(["BUILD_PASTURE"], PASTURE_ANIMALS, "COW", None) == ["BUILD_PASTURE"]


def test_buy_animal_swaps_type_and_keeps_the_rest_of_the_order():
    orders = [["BUY_ANIMAL", "SHEEP", 2], ["BUY_PRODUCT", "WHEAT", 10], ["HIRE"]]
    assert _swap_market(orders, PASTURE_ANIMALS, "COW") == [
        ["BUY_ANIMAL", "COW", 2],
        ["BUY_PRODUCT", "WHEAT", 10],
        ["HIRE"],
    ]


def test_non_animal_actions_pass_through_untouched():
    for action in ([], ["WATER"], ["PASS"], ["HARVEST"], ["CARE"], ["FEED"], ["DIG"]):
        assert _swap_worker(action, ANIMALS, "GOOSE", "COOP") == action


def test_make_agent_rejects_a_structure_the_species_cannot_live_in():
    with pytest.raises(ValueError, match="lives in"):
        make_agent("GOOSE", sources=ANIMALS, structure="PASTURE")


def test_make_agent_rejects_an_unknown_species():
    with pytest.raises(ValueError, match="bad target/sources"):
        make_agent("UNICORN", sources=PASTURE_ANIMALS)
