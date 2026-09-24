"""Unit tests for the fertilizer-buy removal transform (v10_no_fert_buy).

The public path (``make_agent``) needs a full season simulation to exercise, so
these test the pure transform directly. Two of them exist because the transform's
whole correctness argument is a boundary:

  * ``BUY_PRODUCT`` is only legal for WHEAT and FERTILIZER, so the drop must be
    keyed on BOTH the verb and the item. A verb-only rule would silently delete
    the tape's wheat purchases and starve every animal.
  * ``SELL FERTILIZER`` shares the item name with the dropped order. Confusing
    the two would remove revenue instead of cost, which inverts the experiment.
"""

import pytest

from kaggriculture.agents.v10_no_fert_buy import (
    DROPPED_ITEM,
    _drop_buys,
    make_agent,
)


def test_drops_a_fertilizer_buy():
    assert _drop_buys([["BUY_PRODUCT", "FERTILIZER", 12]]) == []


def test_keeps_the_wheat_buy_that_follows_in_the_same_queue():
    # Regression: a verb-only rule would delete this and starve every animal.
    orders = [["BUY_PRODUCT", "FERTILIZER", 5], ["BUY_PRODUCT", "WHEAT", 30]]
    assert _drop_buys(orders) == [["BUY_PRODUCT", "WHEAT", 30]]


def test_keeps_fertilizer_sells_which_share_the_item_name():
    # Regression: dropping sells instead of buys inverts the experiment.
    orders = [["SELL", "FERTILIZER", 20], ["BUY_PRODUCT", "FERTILIZER", 4]]
    assert _drop_buys(orders) == [["SELL", "FERTILIZER", 20]]


def test_preserves_the_order_of_everything_kept():
    orders = [
        ["HIRE"],
        ["BUY_PRODUCT", "FERTILIZER", 3],
        ["BUY_SEED", "WHEAT", 10],
        ["BUY_ANIMAL", "COW", 1],
        ["BUY_LAND"],
    ]
    assert _drop_buys(orders) == [
        ["HIRE"],
        ["BUY_SEED", "WHEAT", 10],
        ["BUY_ANIMAL", "COW", 1],
        ["BUY_LAND"],
    ]


def test_a_malformed_order_is_left_alone_rather_than_raising():
    # A one-element order cannot carry an item, so it is not ours to drop.
    assert _drop_buys([["BUY_PRODUCT"], []]) == [["BUY_PRODUCT"], []]


def test_accepts_tuples_and_returns_engine_executable_lists():
    result = _drop_buys((["BUY_PRODUCT", "WHEAT", 30], ("SELL", "FERTILIZER", 5)))
    assert result == [["BUY_PRODUCT", "WHEAT", 30], ["SELL", "FERTILIZER", 5]]
    assert all(isinstance(order, list) for order in result)


def test_handles_missing_and_empty_market_queues():
    assert _drop_buys(None) == []
    assert _drop_buys([]) == []


def test_dropped_item_is_the_fertilizer():
    # Pins the constant the two boundary tests above depend on.
    assert DROPPED_ITEM == "FERTILIZER"


def test_make_agent_rejects_a_negative_from_step():
    with pytest.raises(ValueError, match="negative"):
        make_agent(from_step=-1)
