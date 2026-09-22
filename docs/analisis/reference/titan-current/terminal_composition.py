# SPDX-License-Identifier: MIT
"""T05 committed terminal routes supplied to the unchanged frozen SELL policy.

One Arlene instance, one parent invocation, one terminal decision, one SELL pass.
The forward route is conditional on T05's reference sales, not an oracle for
future deposits or rival actions. Real deposits are readmitted every turn.
"""
from __future__ import annotations

import copy
import hashlib
import importlib.util
from pathlib import Path
import sys
from typing import Any

HERE = Path(__file__).resolve().parent
SELL_SHA = '32c8610c9827d1686a6f831e2c4b6af4c00d32d2aa04dcf25699d976d6d97dd9'
TERMINAL_SHA = '0623cac3515221b874e9bbbeef97afdf2e378489b0b6724fa9df13627a283a64'
PASS = {'farmer': ['PASS'], 'hands': [], 'market': []}


def load(path: Path, name: str, expected: str):
    if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        raise ValueError(f'Pinned dependency changed: {path.name}')
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


SELL_DIR = HERE / 'vendor/sell'
if not SELL_DIR.is_dir():
    SELL_DIR = HERE.parent / 'cloud-titan-composition/vendor/sell'
TERMINAL_FILE = HERE / 'vendor/terminal.py'
if not TERMINAL_FILE.is_file():
    TERMINAL_FILE = HERE.parent / 'cloud-terminal-routing/terminal.py'
sys.path.insert(0, str(SELL_DIR))
seller = load(SELL_DIR / 'scheduler.py', '_osprey_frozen_sell', SELL_SHA)
terminal = load(TERMINAL_FILE, '_osprey_terminal', TERMINAL_SHA)


def terminal_bounds(configuration: dict[str, Any]) -> tuple[int, int]:
    turns = int(configuration.get('turnsPerDay', 24))
    final = int(configuration.get('episodeSteps', 720)) - 2
    return final // turns * turns + 2, final


def forecast_terminal(observation, configuration, selected_action, planner):
    """Return an indexed own-route tape and phase-aware conditional snapshots.

    Clone the already-advanced planner. Never call the parent again. Only the
    observed own farm/private state is advanced, using the pinned unit/decay
    functions. Reference sells release stock, without assuming a rival sale or
    a future quote. No new day, production RNG, hiring or unknown shop is used.

    Every deposit is a before-market obligation. Future admission is conditional
    on reference sales making capacity available; SELL independently checks its
    withholding plan against this route, and actual T05 admission is recalculated
    from observation at the next call. These are NOT guaranteed arrival lots.
    """
    start, final = terminal_bounds(configuration)
    now = int(observation['step'])
    if not start <= now <= final:
        raise ValueError('Terminal forecast is restricted to the final-day window')
    fork = copy.deepcopy(planner)
    future = copy.deepcopy(observation)
    player = int(observation['player'])
    route = [PASS] * (final + 1)
    snapshots = []
    selected = copy.deepcopy(selected_action)
    for step in range(now, final + 1):
        if step != now:
            selected = fork.act(future, PASS, configuration)
        route[step] = copy.deepcopy(selected)
        before = future['private']
        farm, private = seller.post_units(future, selected, configuration)
        arrival = {item: max(0, int(n) - int(before['shed'].get(item, 0)))
                   for item, n in private['shed'].items()
                   if n > before['shed'].get(item, 0)}
        after_sales = copy.deepcopy(private)
        # T05 final-day reference orders contain SELL only. Preserve their order.
        for order in selected.get('market', []):
            if order and order[0] != 'SELL':
                raise ValueError('Unexpected operating order in terminal-only projection')
            if order and len(order) > 2:
                item = order[1]
                after_sales['shed'][item] = max(0, after_sales['shed'].get(item, 0)
                                                - max(0, int(order[2])))
        snapshots.append({
            'step': step, 'phase': 'before-market',
            'selected_action': copy.deepcopy(selected),
            'post_unit_shed': copy.deepcopy(private['shed']),
            'post_unit_inventories': copy.deepcopy(private['inventories']),
            'deposits': arrival,
            'after_reference_sales_shed': copy.deepcopy(after_sales['shed']),
            'admission': 'actual-current' if step == now else 'conditional-reference-sales',
        })
        if step < final:
            seller.m._decay_plants(farm, step)
            future['farms'][player] = farm
            future['private'] = after_sales
            future['step'] = step + 1
    return route, snapshots


class TerminalOwner:
    """Controller contract used by SELL, retaining the single original parent."""
    def __init__(self, parent):
        self.parent = parent
        self.planner = terminal.Planner()
        self.configuration = {}
        self.route = None
        self.snapshots = []
        self.selected_action = None
        self.parent_calls = 0

    @property
    def R(self):
        return self.parent.R if self.route is None else [self.route]

    @property
    def cur(self):
        return self.parent.cur if self.route is None else 0

    def act(self, observation):
        self.parent_calls += 1
        base = self.parent.act(observation)
        step = int(observation['step'])
        start, final = terminal_bounds(self.configuration)
        if not start <= step <= final:
            self.route, self.snapshots = None, []
            self.selected_action = base
            return base
        selected = self.planner.act(
            observation, base, self.configuration,
            own_future_actions=self.parent.R[self.parent.cur][step:final + 1])
        self.route, self.snapshots = forecast_terminal(
            observation, self.configuration, selected, self.planner)
        self.selected_action = selected
        return selected

    def contract(self):
        """Read-only handoff: selected action, committed queues, conditional lots."""
        return copy.deepcopy({
            'selected_action': self.selected_action,
            'remaining_worker_queues': self.planner.queues,
            'snapshots': self.snapshots,
            'semantics': 'before-market deposits conditional on reference sales; no sale deadline',
        })


class TerminalSell:
    def __init__(self):
        self.execution = seller.SellScheduler()
        self.owner = TerminalOwner(self.execution.controller)
        self.execution.controller = self.owner

    def act(self, observation, configuration=None):
        self.owner.configuration = dict(configuration or {})
        return self.execution.act(observation, configuration)


_INSTANCE = None


def agent(observation, configuration=None):
    global _INSTANCE
    if _INSTANCE is None or int(observation.get('step', 0)) == 0:
        _INSTANCE = TerminalSell()
    return _INSTANCE.act(observation, configuration)
