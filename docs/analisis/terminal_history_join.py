# SPDX-License-Identifier: Apache-2.0
"""Bind shipped history and terminal consumers to TITAN's selected-action path."""
from copy import deepcopy
from pathlib import Path
import random
import sys
from titan_runtime import load

HERE=Path(__file__).resolve().parent/'reference/titan-history'


class TerminalHistoryJoin:
    def __init__(self, *, period=24, hypotheses=None, tie_break='baseline', terminal_enabled=True):
        def dependency(name):
            return load('_titan_history_'+name,HERE/(name+'.py'),cache=True)
        self.dependency=dependency
        self.m=dependency('terminal_mechanics')
        bridge=dependency('selected_action_history')
        fills=dependency('observed_fills');flow=dependency('flow')
        scenario=dependency('scenario_adapter')
        self.bridge=bridge.SelectedActionHistory(ledger=fills.ObservedFillLedger(),
            history=flow.FlowHistory(period=period), interval_type=flow.FlowInterval,
            infer=scenario.infer_rival_flow, mechanics=self.m)
        self.hypotheses=deepcopy(hypotheses)
        self.terminal_enabled=terminal_enabled
        self.tie_break=tie_break
        self.selector=None
        self.pending=None
        self.fill_result=None
        self.diagnostics={}
        if terminal_enabled:self._initialize_terminal()

    def _initialize_terminal(self):
        # Receipt collection is useful in season. It does not authorize or load
        # the optional final-action optimizer and its solver dependencies.
        dependency=self.dependency
        self.joint=dependency('joint_terminal_history')
        self.inputs=dependency('terminal_inputs')
        score=dependency('score_endgame');utility=dependency('terminal_utility')
        full=dependency('full_support');weighted=dependency('weighted_selector')
        # The shipped selector has one absolute sibling import. Restore any
        # caller binding after loading; its function reference remains local.
        previous=sys.modules.get('solver')
        sys.modules['solver']=dependency('solver')
        try:
            selector=dependency('selector')
        finally:
            if previous is None:sys.modules.pop('solver',None)
            else:sys.modules['solver']=previous
        self.selector=score.make_score_selector(selector.WholePlanSelector,
            weighted.make_selector,utility.build_table,full.solve_full_table,
            full.verify_certificate,rng=random.Random(0),tie_break=self.tie_break)

    def observe(self, obs):
        """Bind the ACTUALLY returned prior action, then reconcile once."""
        self.diagnostics={}
        self.fill_result=None
        if self.pending is None:return
        before,cfg,final,post=self.pending
        if int(obs['step'])<=int(before['step']):return
        self.pending=None
        self.bridge.record(before,cfg,final,post_unit_shed=post['private']['shed'],
                           post_unit_inventories=post['private']['inventories'])
        self.diagnostics['observed_fills']=self.bridge.observe(obs)
        self.fill_result=deepcopy(self.bridge.ledger.last_result)

    def remember(self, obs, cfg, final, post):
        # A canceled unit stage has no final snapshot. Do not record a requested
        # quantity as a fill or synthesize a second projection for it.
        self.pending=None if post is None else deepcopy((obs,cfg,final,post))

    def transform(self, obs, cfg, selected, post, *, deadline):
        if not self.terminal_enabled:return selected
        if int(obs['step'])!=int(cfg.get('episodeSteps',720))-2:return selected
        if self.selector is None:self._initialize_terminal()
        family=self.joint.build_joint_terminal_scenarios(self.bridge.history,self.m.PRODUCTS,
            int(obs['step']),capacity=int(cfg.get('shedCapacity',100)),
            max_orders=int(cfg.get('maxMarketOrdersPerTurn',10)),**self.hypotheses)
        self.diagnostics['family']=family
        if not family['ready'] or post is None:return selected
        packet=self.inputs.build_terminal_inputs(self.m,obs,cfg,selected,
            post_unit_observation=post,scenarios=family['scenarios'],deadline=deadline)
        self.diagnostics['terminal_inputs']=packet
        if not packet['complete']:return selected
        receipts=packet['document']['receipts']
        baseline={r['scenario']:r for r in receipts if r['plan']=='baseline'}
        def feasible(action):
            # Native receipts already execute every full queue against shared
            # precommit quotes. Seed/hire acquisitions must match the baseline
            # in EVERY included scenario. Other economic fills lack a receipt
            # witness here, so changed mixed queues retain their selected action.
            if action==selected:return True
            if any(o and o[0] in ('BUY_PRODUCT','BUY_ANIMAL','BUY_LAND')
                   for o in selected.get('market',[])):return False
            identity=next((p['id'] for p in packet['plans'] if p['action']==action),None)
            rows=[r for r in receipts if r['plan']==identity]
            return bool(rows) and all(r['done'] and all(r[k]==baseline[r['scenario']][k]
                for k in ('own_seeds_after','own_hands_after')) for r in rows)
        output=self.selector.transform_terminal(obs,cfg,selected,
            document=packet['document'],feasible=feasible)
        self.diagnostics['selection']=deepcopy(self.selector.last_objective)
        self.diagnostics['changed']=output!=selected
        return output
