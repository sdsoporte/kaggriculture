# SPDX-License-Identifier: Apache-2.0
"""Canonical boundary checks for the attributed committed-seed retry."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
import unittest

from titan_runtime import Features, TitanAgent


ROOT = Path(__file__).resolve().parent
if ROOT.name == 'checks':
    ROOT = ROOT.parent
PASS = {'farmer': ['PASS'], 'hands': [], 'market': []}


class CommittedSeedRetryRuntimeTests(unittest.TestCase):
    def test_default_is_present_and_disabled(self):
        config = json.loads((ROOT/'TITAN-CONFIG.json').read_text())
        self.assertIs(config['committed_seed_retry'], False)
        self.assertIs(Features().committed_seed_retry, False)

    def test_transform_calls_helper_after_existing_seed_boundary(self):
        calls = []
        appended = deepcopy(PASS)
        appended['market'] = [['BUY_SEED', 'STRAWBERRY', 1]]

        agent = TitanAgent(Features(committed_seed_retry=True))
        agent.consumer = SimpleNamespace(
            transform=lambda obs, cfg, selected: calls.append('consumer') or deepcopy(selected))
        agent._redundant_hire_selected = (
            lambda obs, cfg, selected: calls.append('hire') or selected)
        agent._seed_selected = lambda obs, cfg, selected: calls.append('seed') or selected
        agent.committed_seed_retry_module = SimpleNamespace(
            apply_committed_seed_retry=lambda runtime, obs, cfg, selected:
                (calls.append('retry') or deepcopy(appended),
                 {'status': 'appended', 'reason': 'funded_committed_deficit'}))

        result = agent.transform_selected({'step': 10}, {}, PASS)
        self.assertEqual(calls, ['consumer', 'hire', 'seed', 'retry'])
        self.assertEqual(result, appended)
        self.assertEqual(agent.diagnostics['committed_seed_retry']['status'], 'appended')

    def test_disabled_transform_does_not_call_packaged_helper(self):
        agent = TitanAgent(Features())
        agent.committed_seed_retry_module = SimpleNamespace(
            apply_committed_seed_retry=lambda *args: self.fail('disabled helper called'))
        self.assertEqual(agent._committed_seed_retry_selected({}, {}, PASS), PASS)


if __name__ == '__main__':
    unittest.main(verbosity=2)
