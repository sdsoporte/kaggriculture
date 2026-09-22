# SPDX-License-Identifier: MIT
"""Marginal one-hand selection over caller-scheduled observable-state plans.

Pure calculation, not a scheduler or a game simulator. See README for the paired
counterfactual and inventory contract. No policy tape or hidden state is loaded.
"""
from math import isfinite


def hire_cost(hires_today, multiplier=1):
    if hires_today < 0 or multiplier < 0:
        raise ValueError('Negative hire input')
    a, b = 1, 1
    for _ in range(int(hires_today)):
        a, b = b, a + b
    return a * multiplier


def _number(value):
    value = float(value)
    if not isfinite(value):
        raise ValueError('Non-finite economic input')
    return value


def sale_receipts(lots, inventory_at, quote, last_sale):
    """Reprice a complete paired sale stream against exogenous inventory.

    inventory_at(product, step) excludes ALL sales in lots. Prior sales from this
    stream enter cumulative supply only when their executed price exceeds 1;
    equal-step own lots are processed in list order.
    quote(product, inventory) must use the observation's engine market params.
    Opponent/town trades belong in inventory_at, including chosen scenarios.
    Returns conditional single-stream receipts, not a joint order simulation.
    The engine quotes both seats at the SAME pre-commit inventory per unit;
    this callback model does not imply one seat front-runs the other.
    """
    supplied = {}
    total = 0
    for lot in sorted(lots, key=lambda x: x['step']):
        step, qty, item = lot['step'], lot['quantity'], lot['product']
        if int(step) != step or int(qty) != qty or qty < 0:
            raise ValueError('Sales need integer steps and nonnegative quantities')
        if step > last_sale:
            raise ValueError('Sale after final action')
        inv = _number(inventory_at(item, step)) + supplied.get(item, 0)
        for _ in range(int(qty)):
            price = _number(quote(item, inv))
            total += price
            if price > 1:
                inv += 1
                supplied[item] = supplied.get(item, 0) + 1
    return total


def select_hire(observation, configuration, plans, *, inventory_scenarios,
                quote, funds_before_hire=None, cash_reserve=0, free_order_slots=1,
                objective="worst_case", central_scenario=None, scenario_weights=None):
    """Choose a plan by conditional incremental OWN cash profit, not game margin.

    Plan fields: id, observed_step, jointly_feasible, first_work_step,
    last_work_step, baseline_sales, with_hire_sales, additional_cost.
    additional_cost includes resources, displaced work and downstream upkeep,
    EXCLUDING this hire (computed here). Sales include affected existing-worker
    output, so extra supply's effect on that output is charged to the hire.

    Feasibility is supplied by FLORA's scheduler, not established by this
    calculation. No cash/price result is a guarantee of future execution.
    Worst-case is the compatibility default, not a proven winning objective.
    Central and weighted objectives require explicit caller choices; weights are
    not inferred probabilities. All evaluated plans retain the full profit vector.
    """
    now = int(observation['step'])
    farm = observation['farms'][observation['player']]
    turns = int(configuration.get('turnsPerDay', 24))
    last = int(configuration.get('episodeSteps', 720)) - 2
    if turns <= 0 or not inventory_scenarios:
        raise ValueError('Positive day length and explicit scenarios required')
    names = set(inventory_scenarios)
    if objective == 'worst_case':
        if central_scenario is not None or scenario_weights is not None:
            raise ValueError('Worst-case objective does not use weights or a central scenario')
        score = lambda profits: min(profits.values())
    elif objective == 'central_scenario':
        if central_scenario not in names or scenario_weights is not None:
            raise ValueError('Name one supplied central scenario, without weights')
        score = lambda profits: profits[central_scenario]
    elif objective == 'weighted':
        if central_scenario is not None or scenario_weights is None or set(scenario_weights) != names:
            raise ValueError('Supply weights for exactly the provided scenarios')
        scenario_weights = {k: _number(v) for k, v in scenario_weights.items()}
        if any(v < 0 for v in scenario_weights.values()) or abs(sum(scenario_weights.values()) - 1) > 1e-9:
            raise ValueError('Weights must be nonnegative and sum to one')
        score = lambda profits: sum(scenario_weights[k] * profits[k] for k in profits)
    else:
        raise ValueError('Unknown selection objective')
    cost = hire_cost(int(farm['hires_today']), int(configuration.get('farmHandCostMult', 1)))
    funds = _number(farm['money'] if funds_before_hire is None else funds_before_hire)
    reserve = _number(cash_reserve)
    out = {'decision': 'KEEP', 'plan_id': None, 'market_order': None,
           'hire_cost': cost, 'conditional_own_cash_profit_range': None,
           'scenario_own_cash_profit': {}, 'selection_own_cash_profit': None,
           'objective': objective, 'central_scenario': central_scenario,
           'scenario_weights': scenario_weights,
           # Compatibility only: the old field NEVER denotes own-minus-rival cash.
           'conditional_margin': None, 'evaluations': []}
    if free_order_slots < 1 or funds - reserve < cost:
        out['reason'] = 'no_order_slot' if free_order_slots < 1 else 'cash_shortfall'
        return out
    # Hire occurs after unit actions and lasts only through this day's EOD.
    work_end = min(last, (now // turns + 1) * turns - 1)
    best = 0
    for plan in plans:
        result = {'plan_id': plan['id']}
        out['evaluations'].append(result)
        if plan['observed_step'] != now:
            result['reason'] = 'stale_plan'; continue
        if not plan['jointly_feasible']:
            result['reason'] = 'infeasible_plan'; continue
        if not now < plan['first_work_step'] <= plan['last_work_step'] <= work_end:
            result['reason'] = 'worker_lifetime'; continue
        lots = plan['baseline_sales'] + plan['with_hire_sales']
        if any(l['step'] < now or l['step'] > last for l in lots):
            result['reason'] = 'sale_horizon'; continue
        extra = _number(plan['additional_cost'])
        profits = {}
        for name, inventory_at in inventory_scenarios.items():
            base = sale_receipts(plan['baseline_sales'], inventory_at, quote, last)
            candidate = sale_receipts(plan['with_hire_sales'], inventory_at, quote, last)
            profits[name] = candidate - base - cost - extra
        profit_range = [min(profits.values()), max(profits.values())]
        selected_profit = score(profits)
        result.update(reason='evaluated', scenario_own_cash_profit=profits,
                      conditional_own_cash_profit_range=profit_range,
                      selection_own_cash_profit=selected_profit,
                      # Deprecated aliases retained for existing integration.
                      margins=dict(profits), conditional_margin=list(profit_range))
        if selected_profit > best:
            best = selected_profit
            out.update(decision='HIRE', plan_id=plan['id'], market_order=['HIRE'],
                       scenario_own_cash_profit=dict(profits),
                       conditional_own_cash_profit_range=list(profit_range),
                       selection_own_cash_profit=selected_profit,
                       conditional_margin=list(profit_range))
    out['reason'] = 'positive_incremental_own_cash_profit' if best > 0 else 'no_positive_feasible_plan'
    return out
