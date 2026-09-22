# SPDX-License-Identifier: Apache-2.0
"""Exact runtime/package boundaries for the opt-in ECON admission callback."""
from __future__ import annotations

import copy
import io
import json
from pathlib import Path
import tarfile
import time
import unittest

import build_integrated
import funded_payback_runtime
import main


ROOT = Path(__file__).resolve().parent


class FundedPaybackRuntimeTests(unittest.TestCase):
    def config(self):
        return json.loads((ROOT/'TITAN-CONFIG.json').read_text())

    def test_canonical_config_keeps_investment_opt_in(self):
        config = self.config()
        self.assertIs(config['fourth_quadrant'], False)
        instance = main._new_instance(ROOT, config)
        self.assertIsNone(instance._quadrant_admission)

    def test_enabled_runtime_supplies_econ_callback_and_terminal_mechanics(self):
        config = self.config()
        config['fourth_quadrant'] = True
        config['redundant_hire'] = False
        instance = main._new_instance(ROOT, config)
        self.assertEqual(type(instance._quadrant_admission).__name__,
                         'RuntimeFundedPaybackAdmission')
        self.assertEqual(instance._quadrant_admission.seconds, 1.0)
        self.assertEqual(instance._quadrant_admission.max_proposals, 24)
        instance._initialize()
        self.assertIs(instance.quadrant.admit, instance._quadrant_admission)
        self.assertTrue(callable(instance.quadrant.m._parse_order))
        self.assertTrue(callable(instance.quadrant.m._refresh_prices))
        self.assertEqual(instance.quadrant.m._parse_order(
            ['BUY_SEED', 'CARROT', 3]),
            {'type': 'BUY_SEED', 'item': 'CARROT', 'remaining': 3})

    def test_adapter_preserves_slots_and_explicit_physical_rejoin(self):
        config = self.config()
        config.update(fourth_quadrant=True, redundant_hire=False)
        admission = main._new_instance(ROOT, config)._quadrant_admission
        base = [{'farmer': ['PASS'], 'hands': [], 'market': []}
                for _ in range(8)]
        base[2]['market'] = [['SELL', 'WHEAT', 0], ['HIRE']]
        variant = {'patches': {3: {'farmer': ['PASS'], 'hands': [],
                                   'market': [['BUY_SEED', 'CARROT', 0], ['BUY_LAND']]}},
                   'bundle': {'rejoin_step': 7}}
        candidate, rejoin = admission._candidate(base, variant, 2)
        self.assertEqual(candidate[2]['market'], [['PASS'], ['HIRE']])
        self.assertEqual(candidate[3]['market'], [['PASS'], ['BUY_LAND']])
        self.assertEqual(rejoin, 7)
        self.assertEqual(base[2]['market'], [['SELL', 'WHEAT', 0], ['HIRE']])

    def test_action_deadline_clamps_scan_and_restores_configuration(self):
        seen = []

        class Base:
            def __init__(self, *, seconds=1.0, max_proposals=24):
                self.seconds = seconds
                self.max_proposals = max_proposals
                self.last_report = {}

            def __call__(self, mechanics, observation, configuration, routes, proposals):
                seen.append(self.seconds)
                return None

        admission = funded_payback_runtime.make_admission(Base)()
        admission.begin_action(time.monotonic() + 0.50)
        admission(None, {}, {}, {}, [])
        self.assertGreater(seen[0], 0.0)
        self.assertLess(seen[0], 0.50)
        self.assertEqual(admission.seconds, 1.0)
        self.assertIsNone(admission._action_deadline)

    def test_action_deadline_rejects_nonfinite_values(self):
        config = self.config()
        config.update(fourth_quadrant=True, redundant_hire=False)
        admission = main._new_instance(ROOT, config)._quadrant_admission
        with self.assertRaisesRegex(ValueError, 'finite'):
            admission.begin_action(float('nan'))

    def test_rendered_archive_contains_exact_attributed_callback(self):
        data, manifest_bytes, receipt = build_integrated.render()
        manifest = json.loads(manifest_bytes)
        source = (ROOT/'../cloud-economic-stress/funded_payback/funded_payback.py').read_bytes()
        with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as archive:
            packaged = archive.extractfile('funded_payback.py').read()
            self.assertEqual(archive.extractfile('funded_payback_runtime.py').read(),
                             (ROOT/'funded_payback_runtime.py').read_bytes())
            config = json.load(archive.extractfile('TITAN-CONFIG.json'))
        self.assertEqual(packaged, source)
        self.assertEqual(manifest['runtime']['funded_payback.py']['source_path'],
                         '../cloud-economic-stress/funded_payback/funded_payback.py')
        self.assertIs(config['fourth_quadrant'], False)
        self.assertEqual(receipt['runtime_files'], len(manifest['runtime']))


if __name__ == '__main__':
    unittest.main()
