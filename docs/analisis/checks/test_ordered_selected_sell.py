# SPDX-License-Identifier: Apache-2.0
"""Selected-action wrapper cases, not a rerun of accepted component suites.

The ATLAS transfer witness and WREN terminal reservation are exercised through
the new selected-action wrapper. The official engine is loaded for fixtures and
the few changed transitions only. No parent controller or full game is run.
"""
from __future__ import annotations

import copy
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

import test_engine_semantics as semantics

HERE = Path(__file__).resolve().parent
CASE_RECEIPTS = []


def action(farmer=None, hands=None, market=None):
    return {"farmer": copy.deepcopy(farmer or ["PASS"]),
            "hands": copy.deepcopy(hands or []),
            "market": copy.deepcopy(market or [])}


def sold(selected, product):
    return sum(order[2] for order in selected.get("market", [])
               if order and order[:2] == ["SELL", product])


class OrderedSelectedSellTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Only load the existing exact evaluator/engine, not its test methods.
        semantics.EngineSemantics.setUpClass()
        cls.helper = semantics.EngineSemantics()
        cls.engine = cls.helper.engine
        from ordered_selected_sell import OrderedSelectedSell
        cls.wrapper = OrderedSelectedSell

    def fixture(self, step=100, shed=None, carried=None, capacity=100):
        state, env = self.helper.fixture(step=step, cash=100000,
                                        shops=["FARMERS_MARKET"])
        obs = state[0].observation
        positions = self.engine._shed_access_tiles(10)
        farm = obs["farms"][0]
        farm["farmer"] = list(positions[0])
        farm["hands"] = [list(positions[1])]
        obs["private"]["shed"] = copy.deepcopy(shed or {})
        obs["private"]["inventories"] = copy.deepcopy(carried or [{}, {}])
        env.configuration["shedCapacity"] = capacity
        return obs, env.configuration, state, env

    def advance(self, state, env, selected, step):
        for row in state:
            row.observation["step"] = step
            row.action = action()
        state[0].action = copy.deepcopy(selected)
        self.engine.interpreter(state, env)

    def test_selected_pickup_leaves_empty_lot_and_preserves_cash_fallback(self):
        obs, cfg, state, env = self.fixture(step=17, shed={"CARROT": 1})
        obs['farms'][0]['money'] = 100
        selected = action(['PICKUP', 'CARROT', 1], market=[['HIRE']])
        fallback = action(['PICKUP', 'CARROT', 1], market=[])
        tx = self.wrapper()
        prepared = tx.prepare(obs, cfg, selected, future_actions={}, end_step=17)
        self.assertEqual(sum(prepared['post_unit_shed'].values()), 0)
        out = tx.transform(obs, cfg, selected, prepared=prepared,
            reservations={'cash': [{'step':17, 'phase':'after_market', 'minimum':100}]},
            fallback_action=fallback)
        self.assertEqual(out, fallback)
        self.assertEqual(tx.diagnostics['status'], 'fallback')
        self.advance(state, env, out, 17)
        self.assertEqual(state[0].observation['farms'][0]['money'], 100)
        self.assertEqual(state[0].observation['private']['inventories'][0]['CARROT'], 1)

    def test_current_pickup_before_drop_prepares_once_and_binds_selected_action(self):
        obs, cfg, _, _ = self.fixture(shed={"WHEAT": 10, "MILK": 90},
                                     carried=[{}, {"MILK": 10}])
        selected = action(["PICKUP", "WHEAT", 10], [["DROP"]],
                          [["SELL", "MILK", 100]])
        saved = copy.deepcopy((obs, cfg, selected))
        tx = self.wrapper()
        prepared = tx.prepare(obs, cfg, selected, future_actions={}, end_step=100)
        self.assertEqual(prepared["post_unit_shed"], {"WHEAT": 0, "MILK": 100})
        post = prepared["post_unit_observation"]
        self.assertEqual(post["private"]["inventories"], [{"WHEAT": 10}, {}])
        self.assertEqual(prepared["projection"]["stock_events"], [])
        with patch.object(tx, "prepare", side_effect=AssertionError("Second unit projection")):
            out = tx.transform(obs, cfg, selected, prepared=prepared)
        self.assertEqual(out["farmer"], selected["farmer"])
        self.assertEqual(out["hands"], selected["hands"])
        self.assertEqual(sold(out, "MILK"), 100)
        changed = copy.deepcopy(selected)
        changed["farmer"] = ["PASS"]
        fallback = action(["PASS"], [["PASS"]], [["SELL", "MILK", 90]])
        rejected = tx.transform(obs, cfg, changed, prepared=prepared,
                                fallback_action=fallback)
        self.assertEqual(rejected, fallback)
        changed_obs = copy.deepcopy(obs)
        changed_obs["private"]["shed"]["MILK"] = 89
        self.assertEqual(tx.transform(changed_obs, cfg, selected, prepared=prepared,
                                      fallback_action=fallback), fallback)
        self.assertEqual((obs, cfg, selected), saved)
        CASE_RECEIPTS.append({"case": "selected_pickup_drop", "post_shed": post["private"]["shed"],
                              "sold_milk": sold(out, "MILK"), "prepared_reused": True})

    def test_future_drop_pickup_order_changes_capacity_decision(self):
        quantities = {}
        for reverse in (False, True):
            obs, cfg, state, env = self.fixture(
                shed={"WHEAT": 90, "CARROT": 10},
                carried=[{}, {"MILK": 10}] if reverse else [{"MILK": 10}, {}])
            selected = action(market=[["SELL", "CARROT", 10]])
            following = (action(["PICKUP", "WHEAT", 10], [["DROP"]]) if reverse
                         else action(["DROP"], [["PICKUP", "WHEAT", 10]]))
            tx = self.wrapper()
            prepared = tx.prepare(obs, cfg, selected, future_actions={101: following},
                                  end_step=101)
            events = prepared["projection"]["stock_events"]
            self.assertEqual([event["quantity_delta"] for event in events],
                             [-10, 10] if reverse else [10, -10])
            out = tx.transform(obs, cfg, selected, prepared=prepared)
            quantities[reverse] = sold(out, "CARROT")
            self.advance(state, env, out, 100)
            self.advance(state, env, following, 101)
            self.assertEqual(state[0].observation["private"]["shed"]["MILK"], 10)
        self.assertEqual(quantities[False], 10)
        self.assertLess(quantities[True], quantities[False])
        CASE_RECEIPTS.append({"case": "future_worker_order", "drop_first_sale": quantities[False],
                              "pickup_first_sale": quantities[True], "milk_admitted_both": 10})

    def test_terminal_place_then_reserved_stock_uses_valid_fallback(self):
        obs, cfg, state, env = self.fixture(step=718, shed={"CARROT": 1},
                                          carried=[{"WHEAT": 1}, {}])
        selected = action(["PLACE", "WHEAT", 1], [],
                          [["SELL", "WHEAT", 1], ["SELL", "CARROT", 1]])
        fallback = action(["PLACE", "WHEAT", 1], [], [[], ["SELL", "CARROT", 1]])
        tx = self.wrapper()
        prepared = tx.prepare(obs, cfg, selected, future_actions={}, end_step=718)
        self.assertEqual(prepared["post_unit_shed"].get("WHEAT"), 1)
        out = tx.transform(obs, cfg, selected, prepared=prepared,
                           reservations={"stock": {"WHEAT": 1}}, fallback_action=fallback)
        self.assertEqual(out, fallback)
        self.advance(state, env, out, 718)
        self.assertEqual(state[0].observation["private"]["shed"]["WHEAT"], 1)
        self.assertEqual(state[0].observation["private"]["shed"]["CARROT"], 0)
        CASE_RECEIPTS.append({"case": "terminal_place_reservation", "fallback_used": True,
                              "retained_wheat": 1, "sold_carrot": 1})

    def test_t08_pending_whole_lot_and_observed_carry_have_separate_accounting(self):
        obs, cfg, _, _ = self.fixture(step=21, shed={"WHEAT": 92, "CARROT": 4},
                                     carried=[{"EGG": 2}, {}])
        obs["farms"][0]["farmer"] = [0, 0]
        goose = self.engine._new_animal("GOOSE", 0)
        goose["yield_units"] = 4
        obs["farms"][0]["tiles"][0][0] = goose
        selected = action(market=[["SELL", "CARROT", 4]])
        future = {22: action(["HARVEST"]), 23: action()}
        spec = importlib.util.spec_from_file_location(
            "ordered_example_t08_arrivals", HERE / "reference/selected-action/t08/arrival_contract.py")
        contract_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(contract_module)
        snapshot = {"owner": "ordered-example-producer", "observed_step": 21, "plans": [{
            "errand_id": "committed-goose", "worker_index": 0, "target": [0, 0],
            "product": "EGG", "units_total": 6, "units_incremental": 4,
            "observed_carried_units": 2, "status": "pending", "arrival_step": 23,
            "arrival_kind": "eod_auto", "no_forced_sale_date": True}]}
        contract = contract_module.build_arrival_contract(obs, cfg, selected, [snapshot])
        tx = self.wrapper()
        prepared = tx.prepare(obs, cfg, selected, future_actions=future,
                              end_step=23, contingent_harvests=((22, 0),))
        egg_events = [event for event in prepared["projection"]["stock_events"]
                      if event["product"] == "EGG"]
        self.assertEqual([(event["step"], event["phase"], event["quantity_delta"])
                          for event in egg_events], [(23, "after_market", 2)])
        self.assertEqual(contract["capacity_events"][0]["pending_capacity_units"], 4)
        self.assertEqual(contract["sale_lots"], [])
        fallback = action(market=[["SELL", "CARROT", 4], ["BUY_SEED", "CARROT", 1]])
        out = tx.transform(obs, cfg, selected, prepared=prepared,
                           arrival_contract=contract, fallback_action=fallback)
        self.assertNotEqual(out, fallback)
        self.assertEqual(sold(out, "EGG"), 0)
        self.assertEqual(obs["private"]["inventories"][0], {"EGG": 2})
        CASE_RECEIPTS.append({"case": "t08_whole_lot", "observed_deposit_units": 2,
                              "pending_capacity_units": 4, "current_egg_sale": 0})

    def test_market_sale_before_animal_purchase_preserves_order_and_admission(self):
        outcomes = {}
        for sale_first in (True, False):
            obs, cfg, state, env = self.fixture(step=17, shed={"CARROT": 2}, capacity=2)
            orders = [["SELL", "CARROT", 1], ["BUY_ANIMAL", "GOOSE", 1]]
            fallback = action(market=orders)
            selected = action(market=orders if sale_first else list(reversed(orders)))
            tx = self.wrapper()
            out = tx.transform(obs, cfg, selected, future_actions={}, end_step=17,
                               reservations={"stock": {"CARROT": 1}}, fallback_action=fallback)
            outcomes[sale_first] = copy.deepcopy(tx.diagnostics)
            self.assertEqual(out, fallback)
            if sale_first:
                self.assertEqual(out["market"][1], selected["market"][1])
            self.advance(state, env, out, 17)
            self.assertEqual(state[0].observation["private"]["shed"], {"CARROT": 1, "GOOSE": 1})
        self.assertEqual(outcomes[True]["status"], "unchanged")
        self.assertEqual(outcomes[False]["status"], "fallback")
        CASE_RECEIPTS.append({"case": "market_order", "sale_before_buy": "unchanged",
                              "buy_before_sale": "fallback", "goose_admitted": 1})


if __name__ == "__main__":
    unittest.main()
