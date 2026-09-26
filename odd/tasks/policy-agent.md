# Policy agent: the full plan-driven owner (farmer, hands, hires, land, seeds, animals, market)

## Goal

Build the agent the whole investigation demands. Three refutations converged:
overlays on the tape (`v10_*`, `se_loop`), hybrid ownership (`roster_owner`
stage 1, -$144k), and even the shop calendar (null) all show the same thing -
there is no competitive agent that does not OWN the farmer, the hands, the
hires, the land, the seeds, the animals and the sell calendar as one
plan-driven whole. This feature builds that agent in stages, each with a
structural gate, using the plan vector as the contract and the tape as control.

The user has authorized: build with ODD, then SUBMIT - real field episodes are
the only evaluator left ("a esta altura es la única forma de ver su potencial").
Submission gate: the agent must pass a validation episode without error and be
structurally sound; weakness itself is acceptable field data. Champion-retarder
policy still applies at submit time (the challenger slot rotates; keep the
measured champion the most recent when the challenger is expected weaker).

## Architecture

One agent, `src/kaggriculture/agents/policy_agent.py`, structured as layers
over the proven `se_loop` board (imported, never forked):

- **State model**: from each observation, maintain own farm state (tiles, crop
  ages/water, weeds, shed, seeds, carry) and public opponent/market/shops
  state. This replaces the tape's frozen route with a live model.
- **One job board, all workers**: the farmer AND every hand draw from the same
  board (harvest-ready > water-due incl. planting day > weed > plant-per-plan
  > collect/dig > carry), with the `se_loop` claiming machinery.
- **Hires + land**: per plan vector with the proven funding gates (net of
  same-turn spend, reserve = seed bill; hire at the base of the ladder early
  in the day).
- **Animal economy**: plan roster bought on gated days, structures on owned
  land, daily feed/care/collect via the `feed-protect` carry chain.
- **Market layer**: reactive selling - curve-aware (price_curve tables),
  inventory/shed-pressure aware, cash-floor protected (the land cliff), with
  the param_agent market layer as the tested starting reference and its
  day-9..14 drain as the KNOWN RISK to defeat. Buys: seeds per plan,
  fertilizer per program, wheat feed only if the own-feed plan falls short.
- **Plan vector**: `kaggriculture-plan/3`; the starting vector is the tape's
  own season plan (from `scripts/tape_to_plan.py`) extended to four quadrants
  per the replicated event study (buy gated ~day 10, +16 crop tiles, animals
  held).

## Stages and gates

- [x] PA1 - **PASSED.** Worker `muhjv3yw-n-xgok` built `src/kaggriculture/agents/policy_agent.py` (835 lines: plan parsing, per-observation state model, ONE job board driving farmer AND all hands with sector routing, hires/land/seed gates, basic reactive market with sells-first slot order and windowed land floor) + `tests/test_policy_agent.py` (32 tests, 419 full suite). Structural landing proven (land days 6/11/13 gated, roster exactly on curve, plantings inside calendar, order cap held, zero crashes/escapes, no animal emissions). Gate evidence: 12/12 vs param_agent (mean $35,444 vs $19), bank positive at days 9-14 where param_agent drains to $0-119; vs tape -$106,150 (expected: crop-only boundary, animals are PA2). Calibrations: sell floor 0.75, bonus water own tier (-17% if demoted), 12 hands (14 costs $13.6k in fib), land floor windowed and capped at 50% cash. Benchmark command 3 unrunnable pre-existing (param_agent lacks a module-level `agent`); paired inline measurement substituted; one-line fix deferred to PA2's worker. Status `partial` only for that command.
- [x] PA2 - **PASSED on its improvement gate; the winner-profile target (20-21 animals) is REFUTED by measurement.** Added the animal economy to `src/kaggriculture/agents/policy_agent.py` (structures reserved off the crop block first - nearest cells per quadrant, so the feed chain is short and a build can never displace a crop; the joint capex slot order sells > land > seeds > feed-shortfall > animals > hires with the animal slot floored at the WHOLE next quadrant price inside its window plus the hands' bill; the one-tour-per-day animal chain FEED > CARE > product > COLLECT with the shed carry chain that feeds nothing without a wheat pickup; the wheat reserve sized by the herd the farm OWNS; `ANIMAL_FREE_PLAN` kept as PA1's control) + `tests/test_policy_agent.py` (41 tests; 428 in the full suite). Calibrated roster **8 GOOSE / 4 COW / 4 SHEEP = 16 structures over 80 crop tiles** (WHEAT 28 / STRAWBERRY 23 / CARROT 21 / MELON 8, 12 hands): paired against PA1's own vector (96 crops, 0 animals) **+$13,151 mean margin, 12/12 wins on seeds 0-5**, absolute banks $33.4k-$47.0k vs the control's $22.3k-$33.6k; vs `param_agent` on the same plan **12/12, +$46,702**. Structural landing: 16/16 bought and placed on reserved cells, 216-230 feeds, 192-201 cares, 152-160 fertilizer collects in a season, **zero escapes** (the alive count is monotone across all 30 days), every animal alive by day 22, bank positive through days 9-14, all three non-crop products plus fertilizer sold. The gate's own profile (20-21 animals) is NOT reachable and the tests assert the calibrated profile instead: 18 animals measured -$3.6k / -$2.7k / +$0.3k, 20 measured -$1.6k to -$15.6k, and -$22.8k with a 14-hand roster - the products saturate (WOOL's quadratic curve, MILK's linear one), the herd eats the wheat PA1 sold, and the crop board loses the labour. Calibrations: `collect_priority` 1 (was PA1's 5: 24 collects of ~136 exposures, $2.1k of a $13k fertilizer bill, now $9-11k), `feed_priority` -1 (carry-chain jobs are also GLOBAL, not sector-bound - 592 of 713 unfed turns had wheat in a unit's hand that the sector rule kept off the animal), feed reserve `max(feed_days*herd, feed_crew*feed_batch)`, and the roster/count table in the module docstring. Informational: vs the tape 0/12 and -$124k mean (the tape is $111k-186k), and about $2k BELOW the animal-free control against the tape - the day-13 ($4,000) quadrant slips one day there because the geese bought on days 11-12 are what keeps the cash under the price, and buying them later instead measured +$8.2k vs +$14.0k. Also closed PA1's deferred gap: `param_agent` now exposes a module-level `agent` (its `DEFAULT_PLAN` is the shipped revision-3 vector, read from `plans/` rather than copied), so benchmark command 3 runs for real.
- [ ] PA3 - Market sophistication: curve-aware sell timing, opponent radar
      (delta-inventory inference), fertilizer program economics. Gate: margin
      improvement vs PA2 on the same seeds.
- [ ] PA4 - Offline plan search (engine as simulator, local/coordinate) and
      single-file bake.
- [ ] PA5 - Submission (user-authorized): validation episode must pass clean;
      champion-retarder slot management at submit time; then
      `scripts/ladder_record.py` on the new ref for the real-field read.

## Non-goals and constraints

- Shop-awareness is OUT of the first design (measured null, `shop-demand-analysis`).
- No replay-tape dependency: the agent must not embed or follow any 720-step
  frozen route (patch-fragility lesson; understanding transfers, tapes don't).
- Small simulations local (minutes); corpus-scale sweeps on Kaggle, one day per run.
- The tape (main_v8_1) remains the control and the protected champion until a
  challenger MEASURES stronger.
- ~400 authored lines per task advisory; this feature is expected to run
  multiple sessions - the task file is the recovery artifact.

## Reconciliation

Supersedes `odd/tasks/planner-large.md` (W2b2 job-board drain moves here as
PA1's gate; W2c/W2d/W2e become PA1/PA4/PA4). That doc stays as history.
