# SPDX-License-Identifier: Apache-2.0
"""Real current-controller tests. Set TITAN_TEST_RUNTIME to an extracted package."""
from copy import deepcopy
import importlib.util
import json
import os
from pathlib import Path
import sys
import unittest

import seed_retry as retry_module
from seed_retry import propose_seed_retry, apply_committed_seed_retry, install_seed_retry

ROOT = Path(os.environ.get('TITAN_TEST_RUNTIME', str(Path(__file__).resolve().parent.parent / 'cloud-execution-lab')))
sys.path.insert(0, str(ROOT))
from titan_runtime import TitanAgent, Features, load
from scheduler import m, post_units, parent

spec = importlib.util.spec_from_file_location('seed_retry_reference_evaluator', ROOT/'checks/reference/evaluator/evaluate.py')
ev = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = ev
spec.loader.exec_module(ev)
engine, ENGINE_HASHES = ev.get_engine(ROOT/'checks/reference/engine', ROOT/'checks/reference/evaluator/loader.py')
funding = load('_test_seed_retry_funding', ROOT/'reference/titan-current/seed_funding.py', cache=True)
CFG = {k: v.get('default') if isinstance(v, dict) else v
       for k, v in engine.specification['configuration'].items()}
CFG.update(turnsPerDay=24, episodeSteps=720, maxMarketOrdersPerTurn=10,
           farmHandCostMult=1, shedCapacity=100, boardSize=10)
PASS = {'farmer': ['PASS'], 'hands': [], 'market': []}
NEXT = {'farmer': ['PLANT', 'STRAWBERRY'], 'hands': [], 'market': []}


def fixture(seat=0):
    cfg = ev.Struct(dict(CFG, seed=4242))
    env = ev.Struct(configuration=cfg, done=False, info={})
    states = [ev.Struct(observation=ev.Struct(), action={}, status='ACTIVE', reward=0) for _ in (0, 1)]
    engine.interpreter(states, env)
    obs = deepcopy(states[seat].observation)
    obs.step = 10
    farm = obs.farms[seat]
    farm['money'] = 1000.0
    farm['farmer'] = [0, 0]
    farm['hands'] = []
    farm['tiles'][0][0] = None
    obs.private['hands'] = []
    obs.private['seeds'] = {crop: 0 for crop in m.CROPS}
    return obs


def propose(obs=None, selected=None, committed=None, reserve=0, next_step=11, cfg=None):
    return propose_seed_retry(m, funding, fixture() if obs is None else obs,
                              deepcopy(PASS) if selected is None else selected,
                              deepcopy(NEXT) if committed is None else committed,
                              CFG if cfg is None else cfg, reserved_cash=reserve,
                              next_step=next_step)


class SeedRetryTests(unittest.TestCase):
    def test_funded_deficit_appended(self):
        action, report = propose()
        self.assertEqual(action['market'], [['BUY_SEED', 'STRAWBERRY', 1]])
        self.assertEqual(report['status'], 'appended')
        self.assertEqual(report['added_cost'], 100)
        self.assertEqual(report['controller_calls'], 0)

    def test_both_seats(self):
        for seat in (0, 1):
            with self.subTest(seat=seat):
                self.assertEqual(propose(obs=fixture(seat))[1]['status'], 'appended')

    def test_observed_stock_means_no_repeat(self):
        obs = fixture(); obs.private['seeds']['STRAWBERRY'] = 1
        self.assertEqual(propose(obs=obs)[0], PASS)

    def test_input_objects_not_modified(self):
        obs, action, nxt = fixture(), deepcopy(PASS), deepcopy(NEXT)
        before = deepcopy((obs, action, nxt))
        result, _ = propose(obs, action, nxt)
        result['market'][0][2] = 99
        self.assertEqual((obs, action, nxt), before)

    def test_entire_market_prefix_and_units_preserved(self):
        action = {'farmer': ['PASS'], 'hands': [['NORTH']],
                  'market': [[], ['SELL', 'WHEAT', 2], ['HIRE']]}
        out, report = propose(selected=action, reserve=100)
        self.assertEqual(report['status'], 'appended')
        self.assertEqual(out['market'][:-1], action['market'])
        self.assertEqual(out['farmer'], action['farmer'])
        self.assertEqual(out['hands'], action['hands'])

    def test_no_sell_receipts_used(self):
        obs = fixture(); obs.farms[0]['money'] = 99.0
        action = deepcopy(PASS); action['market'] = [['SELL', 'WHEAT', 100]]
        self.assertEqual(propose(obs, action)[0], action)

    def test_reserved_obligations_preserved(self):
        self.assertEqual(propose(reserve=901)[1]['reason'], 'preserve_reserved_obligations')
        self.assertEqual(propose(reserve=900)[1]['status'], 'appended')

    def test_full_queue_certificate_not_reserve_alone(self):
        obs = fixture(); obs.farms[0]['money'] = 100
        action = deepcopy(PASS); action['market'] = [['HIRE']]
        self.assertEqual(propose(obs, action, reserve=0)[1]['reason'], 'full_queue_not_certified')

    def test_dynamic_product_keeps_original(self):
        action = deepcopy(PASS); action['market'] = [['BUY_PRODUCT', 'WHEAT', 1]]
        self.assertEqual(propose(selected=action)[0], action)

    def test_current_seed_request_never_duplicated(self):
        action = deepcopy(PASS); action['market'] = [['BUY_SEED', 'STRAWBERRY', 1]]
        self.assertEqual(propose(selected=action)[1]['reason'], 'seed_purchase_already_selected')

    def test_full_market_not_rewritten(self):
        action = deepcopy(PASS); action['market'] = [[] for _ in range(10)]
        self.assertEqual(propose(selected=action)[1]['reason'], 'no_append_slot')

    def test_day_boundary_not_predicted(self):
        obs = fixture(); obs.step = 23
        self.assertEqual(propose(obs=obs, next_step=24)[1]['reason'], 'day_boundary')

    def test_same_turn_or_distant_purchase_not_called_retry(self):
        for step in (10, 12):
            self.assertEqual(propose(next_step=step)[1]['reason'], 'not_an_existing_next_action_turn')

    def test_terminal_boundary(self):
        obs = fixture(); obs.step = 718
        self.assertEqual(propose(obs=obs, next_step=719)[1]['reason'], 'not_an_existing_next_action_turn')

    def test_occupied_or_locked_or_weed_tile(self):
        for tile in ('LOCKED', {'kind': 'WEED'}, {'kind': 'PLANT', 'crop': 'WHEAT'}):
            obs = fixture(); obs.farms[0]['tiles'][0][0] = tile
            self.assertEqual(propose(obs=obs)[1]['reason'], 'planting_target_not_exclusive_and_empty')

    def test_missing_worker_atomic_demand_not_ignored(self):
        action = deepcopy(NEXT); action['hands'] = [['PLANT', 'STRAWBERRY']]
        self.assertEqual(propose(committed=action)[1]['reason'], 'missing_committed_worker')

    def test_colocated_worker_not_assumed_safe(self):
        obs = fixture(); obs.farms[0]['hands'] = [[0, 0]]
        self.assertEqual(propose(obs=obs)[1]['reason'], 'planting_target_not_exclusive_and_empty')

    def test_atomic_two_plants_purchase_entire_deficit(self):
        obs = fixture(); obs.farms[0]['hands'] = [[1, 0]]; obs.farms[0]['tiles'][0][1] = None
        nxt = deepcopy(NEXT); nxt['hands'] = [['PLANT', 'STRAWBERRY']]
        self.assertEqual(propose(obs=obs, committed=nxt)[0]['market'], [['BUY_SEED', 'STRAWBERRY', 2]])
        obs.private['seeds']['STRAWBERRY'] = 1
        self.assertEqual(propose(obs=obs, committed=nxt)[0]['market'], [['BUY_SEED', 'STRAWBERRY', 1]])

    def test_multiple_crops_must_all_fit_slots(self):
        obs = fixture(); obs.farms[0]['hands'] = [[1, 0]]; obs.farms[0]['tiles'][0][1] = None
        nxt = deepcopy(NEXT); nxt['hands'] = [['PLANT', 'WHEAT']]
        action = deepcopy(PASS); action['market'] = [[] for _ in range(9)]
        self.assertEqual(propose(obs, action, nxt)[1]['reason'], 'insufficient_append_slots')

    def test_invalid_cash_retains_action(self):
        for cash in (True, -1, float('nan'), float('inf'), 100.5):
            obs = fixture(); obs.farms[0]['money'] = cash
            self.assertEqual(propose(obs=obs)[1]['reason'], 'unsupported_input')

    def test_no_future_planting_no_purchase(self):
        self.assertEqual(propose(committed=PASS)[0], PASS)

    def test_native_market_then_native_plant(self):
        # A deliberate engine-valid fixture, not a whole-game strength sample.
        for seat in (0, 1):
            own = fixture(seat)
            own.farms[1-seat]['farmer'] = [2, 0]
            states = [ev.Struct(observation=deepcopy(own), action=deepcopy(PASS), status='ACTIVE', reward=0)
                      for _ in (0, 1)]
            for i in (0, 1):
                states[i].observation.player = i
            states[1].observation.farms = states[0].observation.farms
            states[1].observation.market = states[0].observation.market
            states[1].observation.town = states[0].observation.town
            action, report = propose(obs=own)
            self.assertEqual(report['status'], 'appended')
            states[seat].action = ev.structify(action)
            env = ev.Struct(configuration=ev.Struct(deepcopy(CFG)), done=False, info={})
            engine._process_market(states, env)
            self.assertEqual(states[seat].observation.private['seeds']['STRAWBERRY'], 1)
            for i in (0, 1):
                states[i].observation.step = 11
                states[i].action = ev.structify(deepcopy(NEXT if i == seat else PASS))
            engine.interpreter(states, env)
            self.assertEqual(states[0].observation.farms[seat]['tiles'][0][0]['crop'], 'STRAWBERRY')
            self.assertEqual(states[seat].observation.private['seeds']['STRAWBERRY'], 0)
            self.assertEqual(states[0].observation.farms[seat]['money'], 900.0)


class RuntimeAdapterTests(unittest.TestCase):
    def runtime(self):
        r = TitanAgent(Features())
        r._initialize()
        route = [deepcopy(PASS) for _ in range(720)]
        route[11] = deepcopy(NEXT)
        r.controller.R = {'fixture': route}; r.controller.cur = 'fixture'
        return r

    def test_existing_seed_transform_then_retry_no_producer(self):
        r = self.runtime(); install_seed_retry(r)
        r.production.act = lambda *a, **k: (_ for _ in ()).throw(AssertionError('second producer call'))
        action = r._seed_selected(fixture(), CFG, deepcopy(PASS))
        self.assertEqual(action['market'], [['BUY_SEED', 'STRAWBERRY', 1]])

    def test_canonical_runtime_boundary_calls_actual_helper(self):
        r = self.runtime()
        r.features = Features(committed_seed_retry=True)
        r.committed_seed_retry_module = retry_module
        action = r._committed_seed_retry_selected(fixture(), CFG, deepcopy(PASS))
        self.assertEqual(action['market'], [['BUY_SEED', 'STRAWBERRY', 1]])
        self.assertEqual(r.diagnostics['committed_seed_retry']['status'], 'appended')

    def test_installer_idempotent(self):
        r = self.runtime(); install_seed_retry(r); method = r._seed_selected
        self.assertIs(install_seed_retry(r), r)
        self.assertIs(r._seed_selected, method)

    def test_existing_seed_flag_respected(self):
        r = self.runtime(); r.features = Features(seed=False, funding=False)
        self.assertEqual(apply_committed_seed_retry(r, fixture(), CFG, PASS)[1]['reason'], 'existing_seed_funding_lane_not_enabled')

    def test_future_product_reservation_not_repriced(self):
        r = self.runtime(); r.controller.R['fixture'][12]['market'] = [['BUY_PRODUCT', 'WHEAT', 3]]
        self.assertEqual(apply_committed_seed_retry(r, fixture(), CFG, PASS)[1]['reason'], 'dynamic_product_obligation_in_reserve_window')

    def test_existing_future_fixed_obligation_reserved(self):
        r = self.runtime(); r.controller.R['fixture'][12]['market'] = [['BUY_SEED', 'STRAWBERRY', 10]]
        self.assertEqual(apply_committed_seed_retry(r, fixture(), CFG, PASS)[1]['reason'], 'preserve_reserved_obligations')

    def test_next_branch_not_predicted(self):
        r = self.runtime(); point = next(t for t, *_ in parent.DECISIONS if t > 1)
        obs = fixture(); obs.step = point - 1
        r.controller.R['fixture'][point] = deepcopy(NEXT)
        self.assertEqual(apply_committed_seed_retry(r, obs, CFG, PASS)[1]['reason'], 'route_or_day_boundary')

    def test_nonstandard_day_does_not_reuse_wrong_reserve(self):
        r = self.runtime(); cfg = dict(CFG, turnsPerDay=12)
        self.assertEqual(apply_committed_seed_retry(r, fixture(), cfg, PASS)[1]['reason'], 'existing_reserve_uses_24_turn_days')


if __name__ == '__main__':
    unittest.main(verbosity=2)
