"""Discriminating cases against the preserved official Kaggriculture interpreter.

No game transition implementation is substituted. Small fixtures call the pinned
engine; the final case also executes the existing process-isolated evaluator.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
EVALUATOR = HERE / "reference/evaluator/evaluate.py"
LOADER = HERE / "reference/evaluator/loader.py"
ENGINE_DIR = HERE / "reference/engine"
RECEIPTS = {}


def pass_agent(observation, configuration=None):
    return {"farmer": ["PASS"], "hands": [], "market": []}


def window_agent(observation, configuration=None):
    action = pass_agent(observation, configuration)
    if observation["step"] == 717:
        action["market"] = [["BUY_PRODUCT", "WHEAT", 1]]
    elif observation["step"] == 718:
        action["market"] = [["SELL", "WHEAT", 1]]
    return action


class EngineSemantics(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("sell_semantics_evaluator", EVALUATOR)
        cls.ev = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.ev)
        cls.engine, cls.hashes = cls.ev.get_engine(ENGINE_DIR, LOADER)
        RECEIPTS["engine_sha256"] = cls.hashes
        RECEIPTS["evaluator_sha256"] = hashlib.sha256(EVALUATOR.read_bytes()).hexdigest()

    def fixture(self, item="MILK", stock=(0, 0), inventory=10000, step=1,
                shops=(), cash=0):
        e, S = self.engine, self.ev.Struct
        cfg = S({k: v.get("default") if isinstance(v, dict) else v
                 for k, v in e.specification["configuration"].items()})
        cfg.weedSpawnChance = 0
        farms = [e._new_farm(10, cash), e._new_farm(10, cash)]
        market = e._new_market()
        market["inventory"][item] = inventory
        e._refresh_prices(market)
        town = {"unlocked_shops": list(shops)}
        state = []
        for seat in range(2):
            private = e._new_private()
            private["shed"][item] = stock[seat]
            state.append(S(observation=S(player=seat, step=step, day=step // 24,
                                         hour=step % 24, farms=farms, private=private,
                                         market=market, town=town),
                           action=pass_agent({}), status="ACTIVE", reward=0))
        return state, S(configuration=cfg, done=False, info={"seed": 9600803})

    def market(self, state, env, own=(), rival=()):
        state[0].action["market"] = copy.deepcopy(list(own))
        state[1].action["market"] = copy.deepcopy(list(rival))
        self.engine._process_market(state, env)

    def cash(self, state):
        return [f["money"] for f in state[0].observation.farms]

    def test_01_floor_quote_pays_without_inventory_admission(self):
        state, env = self.fixture(stock=(4, 0), inventory=10075)
        self.assertEqual(self.engine.market_price("MILK", 10075), 3)
        self.assertEqual(self.engine.market_price("MILK", 10076), 1)
        self.market(state, env, [["SELL", "MILK", 4]])
        self.assertEqual(self.cash(state)[0], 6)
        self.assertEqual(state[0].observation.market["inventory"]["MILK"], 10076)
        self.assertEqual(state[0].observation.private["shed"]["MILK"], 0)
        RECEIPTS["floor_admission"] = {"start_inventory": 10075, "sold": 4,
                                       "cash": 6, "end_inventory": 10076}

    def test_02_both_seats_quote_the_same_precommit_inventory(self):
        state, env = self.fixture(stock=(2, 2))
        self.market(state, env, [["SELL", "MILK", 2]], [["SELL", "MILK", 2]])
        self.assertEqual(self.cash(state), [316, 316])
        self.assertEqual(state[0].observation.market["inventory"]["MILK"], 10004)
        # At the floor boundary both seats admit their $3 first unit, even
        # though the first commit alone crosses the admission boundary.
        floor, fenv = self.fixture(stock=(2, 2), inventory=10075)
        self.market(floor, fenv, [["SELL", "MILK", 2]], [["SELL", "MILK", 2]])
        self.assertEqual(self.cash(floor), [4, 4])
        self.assertEqual(floor[0].observation.market["inventory"]["MILK"], 10077)
        RECEIPTS["precommit_pair"] = {"normal_cash": [316, 316],
                                      "floor_boundary_cash": [4, 4],
                                      "floor_boundary_inventory": 10077}

    def test_03_known_town_absorption_between_batches(self):
        kwargs = dict(stock=(12, 0), inventory=10030, step=4,
                      shops=["SMOOTHIE_SHOP"] * 4)
        immediate, ie = self.fixture(**kwargs)
        split, se = self.fixture(**kwargs)
        self.market(immediate, ie, [["SELL", "MILK", 12]])
        self.engine._town_consume(ie, immediate, 4)
        self.market(split, se, [["SELL", "MILK", 6]])
        first_cash = self.cash(split)[0]
        self.engine._town_consume(se, split, 4)
        self.market(split, se, [["SELL", "MILK", 6]])
        self.assertGreater(self.cash(split)[0], self.cash(immediate)[0])
        self.assertEqual(immediate[0].observation.market["inventory"]["MILK"], 10038)
        self.assertEqual(split[0].observation.market["inventory"]["MILK"], 10038)
        # A town tick is after current market: first quote is $97, not the
        # higher price that would follow consumption.
        self.assertEqual(self.engine.market_price("MILK", 10030), 97)
        RECEIPTS["town_between_batches"] = {
            "known_shop_copies": 4, "current_market_price": 97,
            "immediate_cash": self.cash(immediate)[0],
            "first_batch_cash": first_cash, "split_cash": self.cash(split)[0],
            "common_end_inventory": 10038}

    def test_04_rival_supply_can_reverse_a_delay_advantage(self):
        kwargs = dict(stock=(12, 24), inventory=10030, step=4,
                      shops=["SMOOTHIE_SHOP"] * 4)
        no_rival, ne = self.fixture(**kwargs)
        rival, re = self.fixture(**kwargs)
        immediate, ie = self.fixture(**kwargs)
        self.market(no_rival, ne)
        self.engine._town_consume(ne, no_rival, 4)
        self.market(no_rival, ne, [["SELL", "MILK", 12]])
        self.market(rival, re, (), [["SELL", "MILK", 24]])
        self.engine._town_consume(re, rival, 4)
        self.market(rival, re, [["SELL", "MILK", 12]])
        self.market(immediate, ie, [["SELL", "MILK", 12]], [["SELL", "MILK", 24]])
        self.engine._town_consume(ie, immediate, 4)
        self.assertGreater(self.cash(no_rival)[0], self.cash(immediate)[0])
        self.assertLess(self.cash(rival)[0], self.cash(immediate)[0])
        RECEIPTS["observed_rival_scenario"] = {
            "wait_no_rival_cash": self.cash(no_rival)[0],
            "wait_rival_24_cash": self.cash(rival)[0],
            "sell_now_paired_rival_24_cash": self.cash(immediate)[0]}

    def test_05_later_orders_share_remaining_stock(self):
        state, env = self.fixture(stock=(10, 0))
        self.market(state, env, [["SELL", "MILK", 7], ["SELL", "MILK", 7]])
        expected = sum(self.engine.market_price("MILK", 10000 + i) for i in range(10))
        self.assertEqual(self.cash(state)[0], expected)
        self.assertEqual(state[0].observation.market["inventory"]["MILK"], 10010)
        self.assertEqual(state[0].observation.private["shed"]["MILK"], 0)
        RECEIPTS["competing_orders"] = {"requested": [7, 7], "available": 10,
                                         "sold": 10, "cash": expected}

    def test_06_cash_dependencies_follow_market_order_indices(self):
        sale_first, se = self.fixture(stock=(1, 0))
        hire_first, he = self.fixture(stock=(1, 0))
        self.market(sale_first, se, [["SELL", "MILK", 1], ["HIRE"]])
        self.market(hire_first, he, [["HIRE"], ["SELL", "MILK", 1]])
        self.assertEqual(len(sale_first[0].observation.farms[0]["hands"]), 1)
        self.assertEqual(len(hire_first[0].observation.farms[0]["hands"]), 0)
        self.assertEqual(self.cash(sale_first)[0], 159)
        self.assertEqual(self.cash(hire_first)[0], 160)
        RECEIPTS["ordered_cash"] = {"sale_then_hire_cash": 159,
                                     "sale_then_hire_hands": 1,
                                     "hire_then_sale_cash": 160,
                                     "hire_then_sale_hands": 0}

    def test_07_sale_before_eod_frees_shared_depot_space(self):
        cases = {}
        for name, sell in (("hold", 0), ("sell10", 10)):
            state, env = self.fixture(stock=(10, 0), step=23)
            private = state[0].observation.private
            private["shed"]["WHEAT"] = 90
            private["inventories"] = [{"MILK": 20}]
            state[0].action["market"] = [["SELL", "MILK", sell]] if sell else []
            self.engine.interpreter(state, env)
            cases[name] = {"cash": self.cash(state)[0],
                           "milk_in_shed": private["shed"]["MILK"],
                           "shed_total": sum(private["shed"].values()),
                           "carried": copy.deepcopy(private["inventories"]),
                           "lost_milk": 30 - sell - private["shed"]["MILK"]}
        self.assertEqual(cases["hold"]["lost_milk"], 20)
        self.assertEqual(cases["sell10"]["lost_milk"], 10)
        self.assertEqual(cases["sell10"]["shed_total"], 100)
        self.assertEqual(cases["sell10"]["carried"], [{}])
        RECEIPTS["eod_capacity"] = cases

    def test_08_unit_drop_overflow_precedes_and_cannot_use_market_space(self):
        state, env = self.fixture(stock=(10, 0), step=22)
        private = state[0].observation.private
        private["shed"]["WHEAT"] = 90
        private["inventories"] = [{"MILK": 20}]
        state[0].action = {"farmer": ["DROP"], "hands": [],
                           "market": [["SELL", "MILK", 10]]}
        self.engine.interpreter(state, env)
        self.assertEqual(sum(private["shed"].values()), 90)
        self.assertEqual(private["inventories"], [{}])
        self.assertEqual(private["shed"]["MILK"], 0)
        RECEIPTS["pre_market_drop"] = {"lost_milk_before_sale": 20,
                                        "post_sale_shed_total": 90}

    def test_09_final_step_does_not_deposit_carried_inventory(self):
        state, env = self.fixture(stock=(1, 0), step=718)
        private = state[0].observation.private
        private["inventories"] = [{"MILK": 3}]
        state[0].action["market"] = [["SELL", "MILK", 4]]
        self.engine.interpreter(state, env)
        self.assertEqual([s.status for s in state], ["DONE", "DONE"])
        self.assertEqual(state[0].reward, 160)
        self.assertEqual(private["inventories"], [{"MILK": 3}])
        self.assertEqual(private["shed"]["MILK"], 0)
        RECEIPTS["final_carried"] = {"last_decision_step": 718, "hour": 22,
                                     "sold_from_shed": 1, "cash": 160,
                                     "unsold_carried_milk": 3, "salvage": 0}

    def test_10_existing_runner_ends_after_action_718(self):
        observed_steps, original = [], self.engine.interpreter

        def observe(state, env):
            if state[0].observation.get("farms"):
                observed_steps.append(state[0].observation.step)
            return original(state, env)

        self.engine.interpreter = observe
        old_tempdir = tempfile.tempdir
        # Child actor scratch remains under this cloud-lab directory.
        tempfile.tempdir = str(HERE)
        try:
            game = self.ev.play(self.engine,
                                [str(Path(__file__).resolve()) + "::window_agent",
                                 str(Path(__file__).resolve()) + "::pass_agent"],
                                ENGINE_DIR, LOADER, 9600803, 0)
        finally:
            self.engine.interpreter = original
            tempfile.tempdir = old_tempdir
        self.assertEqual(game["status"], "complete", game.get("failure"))
        self.assertEqual(observed_steps, list(range(719)))
        self.assertEqual(game["steps"], 719)
        self.assertEqual(game["actors"][0]["calls"], 719)
        self.assertEqual(game["scores"], [3000, 3000])
        RECEIPTS["existing_runner_terminal"] = {
            "status": game["status"], "actions_per_seat": 719,
            "observed_first_step": min(observed_steps),
            "observed_last_step": max(observed_steps), "cash": game["scores"],
            "final_roundtrip": "BUY_PRODUCT WHEAT at717; SELL WHEAT at718",
            "trace_sha256": game["trace_sha256"],
            "max_action_seconds": max(a["max_call_seconds"] for a in game["actors"])}

    def test_11_runtime_quotes_match_default_and_override_curves(self):
        import mechanics

        checked = 0
        offsets = (-2000, -450, -122, -3, -1, 0, 1, 3, 75, 76, 77,
                   100, 122, 450, 2000)
        parameter_sets = [None]
        for shape in ("linear", "sq", "sqrt", "log", "log10", "hinge"):
            parameter_sets.append(self.engine._resolve_market_params({
                item: {"base": 10, "I0": 20, "T": 2,
                       "below_func": shape, "above_func": shape,
                       "below_target": .1, "above_target": .1}
                for item in self.engine.PRODUCTS}))
        for params in parameter_sets:
            for item in self.engine.PRODUCTS:
                i0 = (params or self.engine.MARKET_PARAMS)[item]["I0"]
                for offset in offsets:
                    self.assertEqual(mechanics.market_price(item, i0 + offset, params),
                                     self.engine.market_price(item, i0 + offset, params))
                    checked += 1
        # Python nearest-even dollar rounding is material at an exact half.
        linear = parameter_sets[1]
        self.assertEqual(mechanics.market_price("MILK", 21, linear), 10)
        self.assertEqual(mechanics.market_price("MILK", 23, linear), 8)
        RECEIPTS["runtime_quote_parity"] = {
            "comparisons": checked, "products": 9,
            "parameter_sets": "default and six legal curve overrides",
            "half_dollar_rounding": {"9.5": 10, "8.5": 8},
            "mechanics_sha256": hashlib.sha256((HERE / "mechanics.py").read_bytes()).hexdigest()}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evaluator", type=Path, default=EVALUATOR)
    parser.add_argument("--loader", type=Path, default=LOADER)
    parser.add_argument("--engine-dir", type=Path, default=ENGINE_DIR)
    args = parser.parse_args()
    EVALUATOR, LOADER, ENGINE_DIR = args.evaluator, args.loader, args.engine_dir
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(EngineSemantics)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    print(json.dumps({"tests": result.testsRun, "successful": result.wasSuccessful(),
                      "receipts": RECEIPTS}, indent=2))
    raise SystemExit(not result.wasSuccessful())
