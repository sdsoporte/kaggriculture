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
- [ ] PA2 - Animal economy: roster purchases, structures on owned land, daily
      feed/care/collect, care banking. Gate: winner-profile reachability
      (20-21 animals, 71-72 crop tiles), zero escapes, benchmark delta.
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
