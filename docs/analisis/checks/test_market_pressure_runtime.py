# SPDX-License-Identifier: Apache-2.0
"""Runtime boundary checks for the landed LARK final SELL ordering."""
from __future__ import annotations

import unittest
from copy import deepcopy
from itertools import product

from titan_runtime import Features, TitanAgent, HERE, load
import mechanics


def gap_module():
    source = HERE if (HERE / 'pressure_priority.py').is_file() else HERE.parent / 'cloud-opponent-league/lark-responsive'
    load('sell_priority', source / 'sell_priority.py', cache=True)
    return load('_gap_contract_pressure', source / 'pressure_priority.py', cache=True)


class MarketPressureRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.obs = {
            'player': 0,
            'step': 100,
            'market': {
                # Exact default-curve quotes at the official initial stock.
                'inventory': {'WOOL': 10000, 'MILK': 10000},
                'prices': {'WOOL': 200, 'MILK': 160},
            },
        }
        self.cfg = {'maxMarketOrdersPerTurn': 10}

    def test_disabled_is_identity_without_copy(self):
        action = {'farmer': ['PASS'], 'hands': [],
                  'market': [['SELL', 'WOOL', 14], ['SELL', 'MILK', 15]]}
        agent = TitanAgent(Features(market_pressure=False))
        self.assertIs(agent._market_pressure_selected(self.obs, self.cfg, action), action)

    def test_enabled_uses_public_curve_and_preserves_barriers(self):
        action = {
            'farmer': ['PASS'],
            'hands': [['CARE']],
            'market': [
                ['SELL', 'WOOL', 14], ['SELL', 'MILK', 15],
                ['HIRE'], ['SELL', 'WOOL', 2], ['SELL', 'MILK', 2],
            ],
        }
        agent = TitanAgent(Features(market_pressure=True))
        result = agent._market_pressure_selected(self.obs, self.cfg, action)
        self.assertEqual(result['market'], [
            ['SELL', 'MILK', 15], ['SELL', 'WOOL', 14],
            ['HIRE'], ['SELL', 'MILK', 2], ['SELL', 'WOOL', 2],
        ])
        self.assertEqual(result['farmer'], action['farmer'])
        self.assertEqual(result['hands'], action['hands'])
        self.assertEqual(agent.diagnostics['market_pressure'],
                         {'enabled': True, 'changed': True})
        self.assertEqual(action['market'][0], ['SELL', 'WOOL', 14])


class SaleGapContracts(unittest.TestCase):
    def setUp(self):
        self.pressure = gap_module()
        self.market = {'inventory': {'MILK': 10000, 'WOOL': 10000},
                       'prices': {'MILK': 160, 'WOOL': 200}}
        self.cfg = {'shedCapacity': 100, 'maxMarketOrdersPerTurn': 10}

    def compact(self, orders, *, end=None, market=None, quote=None):
        return self.pressure.compact_sale_only_prefix(
            orders, len(orders) if end is None else end,
            self.market if market is None else market, self.cfg,
            mechanics.market_price if quote is None else quote)

    def test_runtime_enabled_path_moves_only_known_empty_slots(self):
        original = {'farmer': ['FEED'], 'hands': [['PICKUP', 'WHEAT', 1]],
                    'market': [[], ['SELL', 'WOOL', 4], ['SELL', 'WHEAT', 0], ['SELL', 'MILK', 3]]}
        before = deepcopy(original)
        agent = TitanAgent(Features(market_pressure=True))
        out = agent._market_pressure_selected({'player': 0, 'step': 550, 'market': self.market}, self.cfg, original)
        self.assertEqual(out['market'], [['SELL', 'WOOL', 4], ['SELL', 'MILK', 3], [], ['SELL', 'WHEAT', 0]])
        self.assertEqual(out['farmer'], before['farmer']); self.assertEqual(out['hands'], before['hands'])
        self.assertEqual(original, before)

    def test_all_capital_purchase_input_and_unknown_barriers_decline(self):
        barriers = [['HIRE'], ['BUY_LAND'], ['BUY_SEED', 'WHEAT', 1], ['BUY_PRODUCT', 'WHEAT', 1],
                    ['BUY_ANIMAL', 'COW', 1], ['SELL', 'WHEAT', 2], ['SELL', 'FERTILIZER', 2],
                    ['BUY_PRODUCT', 'WHEAT', 0], ['SELL', 'UNKNOWN', 0], ['NOOP'], None]
        for barrier in barriers:
            with self.subTest(barrier=barrier):
                original = [[], ['SELL', 'MILK', 3], barrier]
                self.assertIs(self.compact(original), original)

    def test_preserves_duplicate_lot_order_and_nonexecutable_suffix(self):
        original = [[], ['SELL', 'MILK', 2], ['SELL', 'MILK', 3], ['BUY_PRODUCT', 'WHEAT', 1]]
        out = self.compact(original, end=3)
        self.assertEqual(out, [['SELL', 'MILK', 2], ['SELL', 'MILK', 3], [], ['BUY_PRODUCT', 'WHEAT', 1]])
        self.assertEqual(len(out), len(original))

    def test_unknown_quantities_and_excess_request_bound_decline(self):
        for quantity in (True, -1, '3', 1.5, 101):
            with self.subTest(quantity=quantity):
                original = [[], ['SELL', 'MILK', quantity]]
                self.assertIs(self.compact(original), original)

    def test_nonmonotone_inconsistent_or_nonfinite_curve_declines(self):
        original = [[], ['SELL', 'MILK', 3]]
        quotes = [lambda p, i, params: 160 + (i - 10000),
                  lambda p, i, params: 159,
                  lambda p, i, params: 160 if i == 10000 else float('nan')]
        for quote in quotes:
            self.assertIs(self.compact(original, quote=quote), original)

    def test_floor_flat_price_and_already_compact_rows_are_identity(self):
        original = [[], ['SELL', 'MILK', 3]]
        floor = {'inventory': {'MILK': 11000}, 'prices': {'MILK': 1}}
        self.assertIs(self.compact(original, market=floor), original)
        compact = [['SELL', 'MILK', 3], []]
        self.assertIs(self.compact(compact), compact)

    def test_nondefault_shed_capacity_declines(self):
        self.cfg['shedCapacity'] = 101
        original = [[], ['SELL', 'MILK', 3]]
        self.assertIs(self.compact(original), original)

    def test_paired_sale_arithmetic_has_no_relative_receipt_regression(self):
        # A finite market-arithmetic contract, not a game or agent rollout.
        # Covers rounding, partial pairs, separated duplicate lots and floor.
        def receipts(own, rival, inventory):
            own_cash = rival_cash = 0
            for a, b in zip(own, rival):
                for k in range(max(a, b)):
                    price = mechanics.market_price('MILK', inventory)
                    own_cash += price * (k < a); rival_cash += price * (k < b)
                    if price > 1:
                        inventory += int(k < a) + int(k < b)
            return own_cash, rival_cash
        for inventory in (9990, 10000, 10065, 10075, 10080):
            for first, second in product((1, 3, 5), repeat=2):
                for rival in product((0, 1, 4), repeat=4):
                    before = receipts((0, first, 0, second), rival, inventory)
                    after = receipts((first, second, 0, 0), rival, inventory)
                    self.assertGreaterEqual(after[0], before[0])
                    self.assertGreaterEqual(after[0] - after[1], before[0] - before[1])


if __name__ == '__main__':
    unittest.main(verbosity=2)
