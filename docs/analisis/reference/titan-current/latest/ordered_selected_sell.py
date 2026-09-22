# SPDX-License-Identifier: Apache-2.0
"""ATLAS selected-unit projection paired with the generic SELL transform.

The caller owns production and its explicit continuation. This module constructs
no production controller, invokes no parent, and never mutates the live state.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import importlib.util

import mechanics
from selected_action_sell import SelectedActionSell, absolute_step

_spec = importlib.util.spec_from_file_location(
    '_atlas_ordered_projection',
    Path(__file__).resolve().parent / 'reference/ordered-feasibility/atlas/projection.py')
_projection = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_projection)


def _binding(observation, configuration, selected_action):
    """State needed to identify the same selected unit stage, including order."""
    seat = int(observation['player'])
    # repr retains inventory insertion order, which controls DROP admission.
    return (absolute_step(observation, configuration), seat,
            repr(observation['farms'][seat]), repr(observation['private']),
            repr(observation['market']), repr(observation.get('town', {})),
            repr(selected_action), repr(dict(configuration)))


class OrderedSelectedSell:
    """One selected action, one copied unit projection, one SELL decision.

    Use prepare() when producer snapshots need the exact post-unit observation,
    then pass its packet as prepared= to transform(). This avoids reprojecting
    the unit stage. The direct path accepts explicit future_actions instead.
    """
    def __init__(self, horizon=8):
        self.seller = SelectedActionSell(horizon=horizon)
        self.diagnostics = {}

    def prepare(self, observation, configuration, selected_action, *,
                future_actions, end_step=None, contingent_harvests=()):
        config = dict(configuration or {})
        now = absolute_step(observation, config)
        requested_end = now + self.seller.horizon if end_step is None else min(
            int(end_step), now + self.seller.horizon)
        packet = _projection.project_selected(
            mechanics, observation, config, selected_action,
            future_actions=future_actions, end_step=requested_end,
            contingent_harvests=contingent_harvests)
        post = deepcopy(observation)
        seat = int(post['player'])
        post['farms'][seat] = deepcopy(packet['post_units']['farm'])
        post['private'] = deepcopy(packet['post_units']['private'])
        post.update(step=now, day=packet['post_units']['day'], hour=packet['post_units']['hour'])
        packet['post_unit_observation'] = post
        packet['_selected_stage'] = _binding(observation, config, selected_action)
        packet['_contingent_harvests'] = tuple(tuple(x) for x in contingent_harvests)
        return packet

    def transform(self, observation, configuration, selected_action, *,
                  prepared=None, future_actions=None, end_step=None,
                  contingent_harvests=(), arrival_contract=None,
                  reservations=None, fallback_action=None):
        fallback = selected_action if fallback_action is None else fallback_action
        config = dict(configuration or {})
        self.diagnostics = {'status': 'fallback', 'controller_calls': 0}
        if prepared is None and future_actions is None:
            self.diagnostics['reason'] = 'missing_selected_continuation'
            return deepcopy(fallback)
        try:
            if prepared is None:
                packet = self.prepare(observation, config, selected_action,
                    future_actions=future_actions, end_step=end_step,
                    contingent_harvests=contingent_harvests)
                projection_calls = 1
            else:
                packet = prepared
                projection_calls = 0
                if packet['_selected_stage'] != _binding(observation, config, selected_action):
                    raise ValueError('Prepared packet describes a different selected unit stage')
            action = self.seller.transform(observation, config, selected_action,
                post_unit_shed=packet['post_unit_shed'], projection=packet['projection'],
                arrival_contract=arrival_contract, reservations=reservations,
                fallback_action=fallback_action)
        except (ValueError, TypeError, KeyError, AttributeError, IndexError, OverflowError) as error:
            self.diagnostics['reason'] = str(error)
            return deepcopy(fallback)
        self.diagnostics = dict(self.seller.diagnostics)
        self.diagnostics.update(controller_calls=0, unit_projection_calls=projection_calls,
                                projection=deepcopy(packet['diagnostics']))
        return action

    act = transform


def transform(observation, configuration, selected_action, **kwargs):
    """Stateless convenience function; retain the class for public rival history."""
    return OrderedSelectedSell().transform(observation, configuration, selected_action, **kwargs)
