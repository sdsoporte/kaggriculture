# Marginal service hire

One decision: hire one more hand only when its **incremental, completed sale value** exceeds its Fibonacci cost and displaced work. Compare the jointly feasible existing-worker plan with the extra-hand plan. This is a new importable calculation for FLORA; it neither schedules workers nor changes the submission.

## Why this decision

At source pin [52b71bf](https://github.com/woahwhattheheck/commons/tree/52b71bf15433cf6831e4373d4fd84fb9ffcbcf05/revenue/kaggriculture/cloud-frontier-policy/next-panel), Arlene v14 main requests 12 daily hands on days 18–24 and 481 HARVEST, 1122 WATER, 381 CARE actions overall. Apex's two tapes also request 12 hands on days 18–24; both request 466 HARVEST and 384 CARE actions. `inspect_sources.py` decodes data without importing either policy. Counts are intended work, not successful executions.

Arlene's `act` protects daily storage, liquidates route-dead stock and settles terminal inventory. Apex's native `policy.cpp` invokes its budget guard every **72** turns, despite the header's six-day name; it sells above static route reserves to fund purchases and hires. Its Python market layer adds shop-sensitive sales and observable opponent crop maturity, alongside schedule-specific triggers. None of those tapes or trigger times enter this selector.

LARK's accepted frozen-panel result is 0/16, mean margins −21472 against Arlene and −22334.25 against Apex. These sources point to sustained productive labor and its funding as a concrete mechanism worth transferring. They do not isolate a causal portion of that gap. FLORA already sells stock before purchases; another liquidity overlay would duplicate that behavior. Its current `cloud-production/candidate.py` instead chooses a coarse target number of hands and hires only at hour <3. The selector exposes the economic boundary behind that choice: an additional feasible harvest can justify a costly or later hire, while work already covered by existing hands cannot.

## Callable interface

```python
from decision import select_hire
result = select_hire(obs, config, plans,
    inventory_scenarios={"buffered": inventory_at, "prompt": inventory_at_prompt},
    quote=lambda product, inventory: market_price(product, inventory, obs["market"].get("params")),
    funds_before_hire=confirmed_cash_after_earlier_orders,
    cash_reserve=reserved_cash, free_order_slots=free_slots)
```

`plans` is a list of **alternative single-extra-hand** plans. Each has `id`, `observed_step`, `jointly_feasible`, `first_work_step`, `last_work_step`, `baseline_sales`, `with_hire_sales`, and `additional_cost`. Sale lists contain `{product, quantity, step}`. Include all own sale streams affected by the changed supply, not just the extra harvest. Additional cost includes resources, displaced tasks and downstream upkeep, excluding the hire itself. Values and feasibility must come from the current observation and FLORA's scheduler. Do not mark gross tile yield as incremental output.

The new hand starts at `obs.step+1` and expires at this day's close. The scheduler must reserve its actual spawn/travel, service, return or EOD deposit, shed capacity and sale slot. A deposit at EOD can sell next day, provided the terminal horizon permits it. The selector checks worker lifetime, observation freshness, funding and sale horizon; it trusts the caller's joint route feasibility. It does not certify that feasibility or invent it when absent.

For each scenario, `inventory_at(product, sale_step)` supplies market inventory **excluding every sale modeled in either paired stream**. Include visible town demand, opponent supply and other exogenous trades. The calculation adds only prior own sale units whose executed quote exceeded 1 and reprices every unit, so an early extra sale can reduce receipts from existing later output. `cloud-market-forecast` already includes conditional own crop sales: sale counts are not inventory deltas. Use an exogenous trajectory excluding the paired streams. At the price floor, adding/removing our sales can also change whether rival sales enter inventory: replay the conditional joint sale timeline for a joint counterfactual rather than subtracting raw sale counts or assuming the old admitted rival supply remains exact. This single-stream calculator holds its supplied exogenous trajectory fixed. Do not double-count supply. The quote function can be imported from the existing forecast mechanics module; no engine or package dependency is bundled here.

For each scenario:

`incremental_own_cash_profit = receipts(with_hire) − receipts(existing_workers) − next_hire_cost − additional_cost`

This quantity is incremental **own cash profit**, not own-minus-rival game margin. The calculation does not model a causal change in rival receipts. LARK's game margins cited above are a distinct measured quantity.

`objective="worst_case"` retains the previous largest-minimum-profit policy. An intentionally broad, uncalibrated envelope can make that policy overly conservative; it is one candidate objective, not proven optimal for winning. For ablation, set `objective="central_scenario", central_scenario="buffered"` only with a justified central scenario, or `objective="weighted", scenario_weights={...}` with explicit caller-supplied nonnegative weights summing to one over all supplied scenarios. The selector learns no weights and invents no probabilities. Learned weights need a separate development fit and held-out evaluation; the weighted score is not a calibrated expectation by construction.

Every evaluated plan exposes `scenario_own_cash_profit`, `conditional_own_cash_profit_range` and `selection_own_cash_profit`. The selected plan's values also appear at top level, alongside the objective configuration, `HIRE`/`KEEP`, plan ID, order and cost. The range remains the full scenario envelope even when a central or weighted objective selects a plan with a negative worst case. Deprecated `conditional_margin` and evaluation `margins` are compatibility aliases for OWN cash profit only; new integrations should use the explicit names. Ties retain the first plan. FLORA should compare objectives on paired cloud games using both own cash and own-minus-rival terminal margin, keeping these metrics separate. Current integration need not wait for that experiment. Append HIRE only after its credited funding orders, with no intervening spending; reserve and execute the chosen plan. Recompute from the next observation before another hire. Never select several alternatives as if their same jobs were independent.

Price trajectories and future execution remain conditional. Paired scenarios assume the same exogenous opponent trades in each alternative; behavior changes caused by our intervention require FLORA's ablation. This is not a guaranteed profit bound, a complete scheduler, or a measured terminal-margin improvement.

## Verification and pins

Run `python -m unittest discover -s revenue/kaggriculture/cloud-frontier-decision -v` from Commons root: **10 focused tests passed** in cloud Python 3.12. Cases cover useful late hiring, already-covered output, price impact on existing sales, cash/order capacity, stale/infeasible/terminal plans, nonlinear sequential sale accounting, objective-dependent decisions with unchanged scenario vectors, and rejection of missing or invalid explicit weights. Synthetic economic examples deliberately have no replay labels. No games, rejected studies, or duplicate simulations were run.

Public source originals: [Arlene v14](https://www.kaggle.com/code/lynnsakurai/farming-score-a-mathematical-approach), [Apex v1/script347286121](https://www.kaggle.com/code/avioon/kaggriculture-apex-v7-god-emperor?scriptVersionId=347286121). Both Apache-2.0; retained original notices and exact file hashes are in the pinned next-panel `UPSTREAM.json` and `NOTICE.txt`. Arlene source SHA256 `1dc166ae2bf0c56a44fac4482f469b8812968c4cb32459cb9860f5077897a7d4`; Apex main `1f7cd5fb8a16585936d2562a3667f85bb6661688718ef58f73006de66148354a`; native policy `74b5d7e778c0f4a6e2f0e0725077943dbc319b7d0f5ace4c9070a4ae69db51b3`; budget guard `6835d614131c6ca6c57d86e941891d1fea3fa44237f61fd1ab9ac876dd4ebc25`.

Engine semantics inspected directly at [Kaggle/kaggle-environments 28b6d8af](https://github.com/Kaggle/kaggle-environments/blob/28b6d8af3ce73926b3d0fda1410c1ddd8384ab8c/kaggle_environments/envs/kaggriculture/kaggriculture.py): `_process_market`, `_hire_cost`, `_do_hire`, and existing ROWAN liquidation contracts. Policy source reading covered Arlene's control functions and decoded all route data, Apex's policy/budget guard and Python service/market layer; it does not claim full notebook narrative coverage. New selector code is MIT; no vendor policy code is copied. FLORA owns integration/ablation, LARK owns service repair, root owns Claude and submission reconciliation.

## Price-floor mechanics repair

SELL always pays its quoted price, but only a quote above 1 adds a unit to market inventory. Regression: with quote `max(1, 3-inventory)`, four sales at initial inventory 0 pay 3+2+1+1 and admit two units. After town consumes two, the next sale pays 3: total 10, not the old erroneous 8. The actual engine quotes both seats from the SAME pre-commit inventory per unit and then commits both; the selector's single-stream exogenous callback is not a model of one seat front-running the other.
