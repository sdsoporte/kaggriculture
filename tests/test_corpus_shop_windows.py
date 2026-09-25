"""Synthetic tests for the shop-window demand extractor.

These pin the mechanics the Kaggle run depends on and that are easy to get
silently wrong: the per-instance shop timeline with duplicates, holdings-drop
sell accounting (a requested-but-unfilled SELL is not a sale), the shop-window
share arithmetic, the within-game winner/loser split, the YARN gate split, the
mapping/table join and the bounded report. They run on tiny hand-built replays,
so no Kaggle dataset or engine is needed. Nothing here measures a market; it
pins the contract.
"""

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SHOP_PATH = ROOT / "scripts" / "corpus_shop_windows.py"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None, f"cannot load {path}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


shop = _load("corpus_shop_windows_tests", SHOP_PATH)


# --- synthetic replay builders -------------------------------------------

def _pass():
    return {"farmer": ["PASS"], "hands": [], "market": []}


def _sell(product, units=10000):
    return {"farmer": ["PASS"], "hands": [], "market": [["SELL", product, units]]}


def _obs(seat, day, shops, shed=None, action=None, money=10000):
    farm = {"money": money, "unlocked_quadrants": ["NW"], "hands": [], "tiles": []}
    obs = {
        "player": seat,
        "day": day,
        "farms": [None, None],
        "town": {"unlocked_shops": list(shops)},
        "private": {"shed": dict(shed or {}), "inventories": [{}], "seeds": {}},
    }
    obs["farms"][seat] = farm
    return {"action": action or _pass(), "observation": obs}


def _row(day, shops, shed0=None, act0=None, money0=10000,
         shed1=None, act1=None, money1=10000):
    return [
        _obs(0, day, shops, shed0, act0, money0),
        _obs(1, day, shops, shed1, act1, money1),
    ]


def _rule_table():
    # Mapping-only table: deterministic and independent of the local cache.
    return shop.shop_load_demand_table("/nonexistent/town_shop_demand.csv")


# --- shop timeline --------------------------------------------------------

def test_timeline_preserves_duplicates_with_their_own_unlock_turn():
    steps = [
        _row(0, ["BAKERY", "BAKERY"]),
        _row(0, ["BAKERY", "BAKERY"]),          # same snapshot: no new instance
        _row(1, ["BAKERY", "BAKERY", "YARN_STORE"]),
    ]
    timeline = shop.shop_timeline(steps, 0)
    assert [item["shop"] for item in timeline] == ["BAKERY", "BAKERY", "YARN_STORE"]
    assert [item["day"] for item in timeline] == [0, 0, 1]
    # A repeated snapshot must not invent a third BAKERY.
    assert sum(1 for item in timeline if item["shop"] == "BAKERY") == 2


def test_timeline_tracks_a_later_second_instance_separately():
    steps = [
        _row(1, ["BAKERY"]),
        _row(2, ["BAKERY"]),
        _row(3, ["BAKERY", "BAKERY"]),          # the duplicate unlocks here
    ]
    timeline = shop.shop_timeline(steps, 0)
    assert [(item["shop"], item["day"]) for item in timeline] == [
        ("BAKERY", 1), ("BAKERY", 3)]


def test_shop_days_active_spans_unlock_day_to_last_day():
    steps = [_row(1, ["BAKERY"]), _row(2, ["BAKERY"]), _row(3, ["BAKERY"])]
    timeline = shop.shop_timeline(steps, 0)
    active = shop.shop_days_active(timeline, 4)
    assert active["WHEAT"] == {1, 2, 3, 4}
    assert active["EGG"] == {1, 2, 3, 4}


# --- holdings-drop sell accounting ---------------------------------------

def test_drop_on_a_sell_turn_is_the_executed_units():
    steps = [
        _row(0, [], {"WHEAT": 10}),
        _row(0, [], {"WHEAT": 4}, act0=_sell("WHEAT")),
    ]
    executed = shop.shop_executed_sales(steps, 0)
    assert executed["by_product"]["WHEAT"] == 6
    assert executed["by_day_product"][0]["WHEAT"] == 6


def test_requested_but_unfilled_sell_counts_zero_units():
    # SELL WHEAT 10000 is a REQUEST; the market need not fill it. Holdings did not
    # move, so no sale is reported even though the order is in the action.
    steps = [
        _row(0, [], {"WHEAT": 10}),
        _row(0, [], {"WHEAT": 10}, act0=_sell("WHEAT")),
    ]
    executed = shop.shop_executed_sales(steps, 0)
    assert executed["by_product"].get("WHEAT", 0) == 0
    assert executed["by_day_product"] == {}


def test_non_sell_drop_is_not_a_sale():
    # Farm consumption / the end-of-day shed overflow discard also lower holdings,
    # but with no SELL request they must never be reported as executed sales.
    steps = [
        _row(0, [], {"WHEAT": 10}),
        _row(0, [], {"WHEAT": 9}),
    ]
    executed = shop.shop_executed_sales(steps, 0)
    assert executed["by_product"].get("WHEAT", 0) == 0
    assert executed["dropped_unsold"]["WHEAT"] == 1


def test_shed_and_inventories_both_count_and_internal_moves_cancel():
    first = _obs(0, 0, [])             # shed holds nothing ...
    second = _obs(0, 0, [])            # ... because the units moved to inventory
    first["observation"]["private"]["shed"] = {"WHEAT": 5}
    second["observation"]["private"]["inventories"] = [{"WHEAT": 5}]
    steps = [[first, _obs(1, 0, [])], [second, _obs(1, 0, [])]]
    executed = shop.shop_executed_sales(steps, 0)
    assert executed["by_product"].get("WHEAT", 0) == 0


# --- window share arithmetic ---------------------------------------------

def test_shop_window_share_splits_on_days_the_shop_is_live():
    # BAKERY (WHEAT) unlocks on day 2. 4 units sell before it and 6 after it.
    steps = [
        _row(0, [], {"WHEAT": 4}),
        _row(0, [], {}, act0=_sell("WHEAT")),
        _row(1, [], {"WHEAT": 6}),
        _row(2, ["BAKERY"], {"WHEAT": 6}),
        _row(3, ["BAKERY"], {}, act0=_sell("WHEAT")),
    ]
    match = shop.shop_extract_match(steps)
    window = match["seats"][0]["window"]
    info = window["per_product"]["WHEAT"]
    assert info["sold"] == 10
    assert info["in_window"] == 6
    assert abs(info["share"] - 0.6) < 1e-9
    assert abs(window["aggregate"]["share"] - 0.6) < 1e-9


def test_window_share_is_none_when_the_seat_sold_nothing():
    steps = [_row(0, ["BAKERY"]), _row(1, ["BAKERY"])]
    match = shop.shop_extract_match(steps)
    assert match["seats"][0]["window"]["aggregate"]["share"] is None
    assert match["seats"][0]["window"]["shop_units"] == 0


def test_non_shop_products_are_not_in_the_shop_window():
    steps = [
        _row(0, [], {"MELON": 3}),
        _row(0, [], {}, act0=_sell("MELON")),
    ]
    match = shop.shop_extract_match(steps)
    window = match["seats"][0]["window"]
    assert "MELON" not in window["per_product"]
    assert window["shop_units"] == 0
    assert window["non_shop_units"] == 3


# --- winner vs loser ------------------------------------------------------

def _winner_loser_replay():
    # Seat 0 ends richer and sells 8/12 units in-window; seat 1 sells 2/6.
    # Day 1 has no shop, days 2-3 have BAKERY, so day-1 sales are out of window.
    return [
        _row(0, [], {"WHEAT": 4}, None, 10000, {"WHEAT": 4}, None, 10000),
        _row(1, [], {}, _sell("WHEAT"), 10000, {}, _sell("WHEAT"), 10000),
        _row(2, ["BAKERY"], {"WHEAT": 8}, None, 10000, {"WHEAT": 2}, None, 10000),
        _row(3, ["BAKERY"], {}, _sell("WHEAT"), 12000, {}, _sell("WHEAT"), 9000),
    ]


def test_winner_is_the_higher_final_bank_and_share_direction_is_within_game():
    match = shop.shop_extract_match(_winner_loser_replay(), teams=["WIN", "LOSE"])
    assert match["winner"] == 0
    assert match["loser"] == 1
    summary = shop.shop_new_summary(_rule_table())
    shop.shop_fold_match(summary, match)
    window = summary["window"]
    assert window["both_defined"] == 1
    assert window["winner_more"] == 1
    assert window["loser_more"] == 0
    assert abs(window["winner_share"][0] - 8 / 12) < 1e-9
    assert abs(window["loser_share"][0] - 2 / 6) < 1e-9
    # Shop-product units split: winner 12, loser 6.
    assert summary["split"]["winner_shop"] == [12]
    assert summary["split"]["loser_shop"] == [6]
    assert summary["split"]["winner_more_shop"] == 1


def _sell_many(*products):
    return {"farmer": ["PASS"], "hands": [],
            "market": [["SELL", product, 10000] for product in products]}


def test_shop_vs_non_shop_units_split_counts_melon_as_non_shop():
    steps = [
        _row(0, [], {"WHEAT": 4, "MELON": 2}),
        _row(0, ["BAKERY"], {}, act0=_sell_many("WHEAT", "MELON")),
    ]
    match = shop.shop_extract_match(steps)
    window = match["seats"][0]["window"]
    assert window["shop_units"] == 4
    assert window["non_shop_units"] == 2


def test_tied_bank_is_excluded_from_direction_but_still_counted():
    # Both seats sell 2 units before BAKERY is live, so both shares are defined;
    # a tied bank must still suppress the winner/loser direction entirely.
    steps = [
        _row(0, [], {"WHEAT": 2}, None, 10000, {"WHEAT": 2}, None, 10000),
        _row(0, [], {}, _sell("WHEAT"), 10000, {}, _sell("WHEAT"), 10000),
        _row(1, ["BAKERY"], None, None, 10000, None, None, 10000),
    ]
    match = shop.shop_extract_match(steps)
    assert match["tie"] is True
    summary = shop.shop_new_summary(_rule_table())
    shop.shop_fold_match(summary, match)
    assert summary["ties"] == 1
    assert summary["matches"] == 0
    assert summary["window"]["both_defined"] == 0


# --- YARN gate ------------------------------------------------------------

def test_yarn_split_separates_wool_sold_on_yarn_days():
    steps = [
        _row(0, [], {"WOOL": 3}),
        _row(1, [], {}, act0=_sell("WOOL")),
        _row(2, ["YARN_STORE"], {"WOOL": 5}),
        _row(3, ["YARN_STORE"], {}, act0=_sell("WOOL")),
    ]
    match = shop.shop_extract_match(steps)
    yarn = match["seats"][0]["yarn"]
    assert yarn["has_yarn"] is True
    assert yarn["on"] == 5
    assert yarn["off"] == 3
    assert abs(yarn["share_on"] - 5 / 8) < 1e-9


def test_yarn_absent_when_no_instance_unlocked():
    steps = [_row(0, [], {"WOOL": 2}), _row(1, [], {}, act0=_sell("WOOL"))]
    match = shop.shop_extract_match(steps)
    yarn = match["seats"][0]["yarn"]
    assert yarn["has_yarn"] is False
    assert yarn["on"] == 0
    assert yarn["off"] == 2


# --- expected demand (mapping x table) -----------------------------------

def test_engine_rule_applies_single_product_doubling():
    assert shop.shop_rule_instance_day_units(("WOOL",)) == 12
    assert shop.shop_rule_instance_day_units(("CARROT",)) == 12
    assert shop.shop_rule_instance_day_units(("EGG", "WHEAT")) == 6
    assert shop.shop_rule_instance_day_units(("WHEAT", "CARROT", "TOMATO", "STRAWBERRY")) == 6


def test_mapping_only_fallback_is_flagged_and_matches_the_rule():
    table = shop.shop_load_demand_table("/nonexistent/town_shop_demand.csv")
    assert table["present"] is False
    assert table["rates"][("YARN_STORE", "WOOL")] == 12
    assert table["rates"][("BAKERY", "EGG")] == 6
    assert table["town_rows"] == 0


def test_table_join_excludes_town_center_and_records_missing_rows(tmp_path):
    path = tmp_path / "town_shop_demand.csv"
    path.write_text(
        "source,name,product,units_per_tick,tick_interval_turns,"
        "units_per_day_if_unlocked,note\n"
        "SHOP,YARN_STORE,WOOL,2,4,12.0,\n"
        "SHOP,BAKERY,EGG,1,4,6.0,\n"
        "TOWN_CENTER,TOWN_CENTER,WHEAT,1,12,2.0,stale\n"
    )
    table = shop.shop_load_demand_table(str(path))
    assert table["present"] is True
    assert table["shop_rows"] == 2
    assert table["town_rows"] == 1
    assert table["rates"][("YARN_STORE", "WOOL")] == 12
    # BAKERY/WHEAT was omitted by the table and is filled from the engine rule.
    assert ("BAKERY", "WHEAT") in table["missing"]
    assert table["rates"][("BAKERY", "WHEAT")] == 6
    assert table["mismatches"] == []
    assert "MELON" not in shop.SHOP_CONSUMING_PRODUCTS
    assert "FERTILIZER" not in shop.SHOP_CONSUMING_PRODUCTS


def test_table_rate_disagreement_with_the_rule_is_flagged(tmp_path):
    path = tmp_path / "town_shop_demand.csv"
    path.write_text(
        "source,name,product,units_per_tick,tick_interval_turns,"
        "units_per_day_if_unlocked,note\n"
        "SHOP,BAKERY,WHEAT,1,4,99.0,\n"
    )
    table = shop.shop_load_demand_table(str(path))
    assert table["rates"][("BAKERY", "WHEAT")] == 99.0
    assert any(row[:2] == ("BAKERY", "WHEAT") for row in table["mismatches"])


def test_expected_demand_accumulates_duplicate_instances():
    steps = [
        _row(1, ["BAKERY"]),
        _row(2, ["BAKERY", "BAKERY"]),
        _row(3, ["BAKERY", "BAKERY"]),
    ]
    timeline = shop.shop_timeline(steps, 0)
    demand = shop.shop_expected_demand_by_day(timeline, 3, _rule_table()["rates"])
    assert demand[1]["WHEAT"] == 6
    assert demand[2]["WHEAT"] == 12
    assert demand[3]["EGG"] == 12
    assert "YARN_STORE" not in str(demand)


# --- report ---------------------------------------------------------------

def test_report_is_bounded_labelled_and_repeats_the_caveat():
    summary = shop.shop_new_summary(_rule_table())
    shop.shop_fold_match(summary,
                         shop.shop_extract_match(_winner_loser_replay(),
                                                 teams=["A", "B"]))
    report = shop.shop_render_report(summary, "2026-09-24")
    assert "2026-09-24" in report
    assert "one day per run" in report
    assert "within-game" in report.lower()
    assert "DESCRIPTIVE" in report
    assert "holds" in report.lower() or "holdings drop" in report.lower()
    assert len(report) < 20000


def test_report_prints_n_and_reasons_when_the_cohort_is_empty():
    summary = shop.shop_new_summary(_rule_table())
    summary["skipped"]["truncated: fewer than two steps"] += 3
    report = shop.shop_render_report(summary, "2026-09-24")
    assert "skipped replays" in report
    assert "truncated: fewer than two steps" in report
    assert len(report) < 20000


def test_skip_when_there_is_no_replay_state():
    try:
        shop.shop_extract_match([])
    except shop.ShopSkip as exc:
        assert "fewer than two steps" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("an empty replay must raise ShopSkip")
