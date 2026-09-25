# Roster-owner agent: a policy that owns its hands, hires, land and animals

## Goal

Build the agent class the whole investigation points to. Three refutations
converged on the same wall: every overlay on the saturated tape fails because
the tape owns its 12 hands and its schedule (`v10_*`: roster, quadrant, feed,
idle), and the 4th-quadrant winners hold exactly the two levers an overlay
cannot touch - animal structures on new land and driving their own hand
indices. The replicated event study (`197dc0d`) shows what a winning policy
does with that ownership: buy quadrants on funding gates, convert new land to
~16 crop tiles within days, hold ~20 animals and ~12 hands, absorb a ~$5k
mid-game deficit, and collect in the last third.

This feature builds that policy incrementally. NOT a full from-scratch planner:
the farmer's proven core route is inherited first; ownership grows outward.

## Architecture (staged)

Stage 1 - "own the growth" (this feature's first deliverable):
- Farmer: the incumbent tape's farmer actions stay (proven route, proven core
  economy).
- Hands: OURS. Every hand action comes from the `se_loop` job board
  (plant/water/harvest/dig/drop/carry), never from the tape's hand actions.
- Hires: OURS. The daily hand count follows the plan vector (default: the
  tape's own curve, from `scripts/tape_to_plan.py`), hired by our policy with
  the dual-counter ownership claim proven in `se_loop.py`.
- Land: OURS. Quadrant purchases follow the plan's buy days with the
  funding gate proven in `se_loop.py` (net of same-turn spend, reserve = seed
  bill), extending to all quadrants the plan names.
- New land: planted and watered by our hands with the parametrized mix; NO
  animal structures yet (stage 2).
- Byte-identity is NOT expected here (we own hands); the control is instead:
  with the plan frozen to the tape's own curve and the board disabled, the
  agent degrades gracefully to the tape's farmer-only economy, and the test
  suite pins every ownership boundary.

Stage 2 - animals on owned land (only after stage 1 survives):
- The plan's animal roster bought on gated days, structures on the NEW land
  (never displacing crops), daily feed via the proven carry chain
  (`v10_feed_protect` reference), care banking.

Stage 3 - crop mix owned end-to-end (the tape's farmer keeps its tiles; our
mix policy applies to new land and, if benchmarks justify, to replants).

## Instruments this stands on (do not rebuild)

- `se_loop.py`: job board, appended-hand claiming, tape-spend-aware gate,
  watering-on-planting-day, order-cap accounting, byte-identity harness.
- `tape_to_plan.py`: the tape's own season plan as a `kaggriculture-plan`
  vector (the starting plan and the calibration baseline).
- `param_agent.py`: the previous consumer - reference only. Its unresolved
  day-9..14 cash drain is a KNOWN RISK this design inherits if its sell layer
  is reused; stage 1 deliberately keeps the tape's farmer (which sells) to
  avoid that failure mode while the hands are migrated.
- `v10_feed_protect.py`: feeding carry chain (stage 2).
- Engine facts pinned in this repo: PLANT voids when turn demand exceeds held
  seeds; watering on planting day or the crop dies that night; weed RNG is
  shared across seats; the 10-order cap is shared; Fibonacci hire pricing
  punishes late tail hires (~$3,071/season for one appended hand on a
  9-12-hand day) - which is why hires must be OURS from the base of the
  ladder, not appended.

## Acceptance

Stage 1 (this feature's gate):
- A full season runs with: hands hired per plan, every hand action from the
  board, land bought only on gated days, new tiles planted + watered
  (alive next day), zero escapes, zero crashes, order cap respected.
- Paired benchmark vs `submissions/main_v8_1.py`, >= 12 seeds both seats:
  informational, but the margin must be printed with absolute banks and the
  landing evidence must be structural, not narrative.
- The known reference points: the tape's bank (~$80-100k), param_agent's
  failure ($882), the appended-hand tax (-$4,670). Stage 1 wins if the
  roster-owner economy lands and the margin loss vs the tape is small enough
  that stage 2's animal lever has room to flip it.
- A clean structural failure is a recorded result, never massaged.

Stage 2 gate: animals land and are fed daily (zero escapes); the 4q-winner
profile (20-21 animals, 71-72 crop tiles) is reachable; benchmark re-run.

Stage 3 gate: mix policy beats the stage-2 mix on the same seeds.

No submission at any stage without a measured >= 65% win rate and >= +$5,000
margin vs the incumbent bar, and even then the submit stays a user decision.

## Tasks

- [x] R1 - Worker `muhedwai-e-4l30` built `src/kaggriculture/agents/roster_owner.py` (411 lines, RosterOwner subclassing se_loop.SELoopOverlay) + `tests/test_roster_owner.py` (23 tests). Disclosed interpretation deviation: the board covers the WHOLE farm, not only new land, because the literal reading is infeasible - the tape's farmer plants ~0 tiles/season and farmer-only banks exactly $0 (measured seeds 0-3). Structural landing PROVEN: all four quadrants bought gated and in engine order, hands 100% ours per the embedded curve, planting-day watering verified by next-day aliveness, order cap held, zero crashes/escapes, byte-identical pass-through when disabled.
- [x] R2 - Parent re-ran the focused suite (23 passed) and the required benchmark: reproduced exactly (24 games, 0 wins, mean margin -$144,548.1; A mean bank ~$5.6k vs tape ~$150k). **Verdict: stage-1 architecture REFUTED.** The collapse is structural, not tunable: (1) the tape's market layer is a fixed SELL script coupled to its hand-authored agronomy - with our WHEAT/CARROT board it clamps almost every planned sale (tape sells MILK 261/FERTILIZER 373/STRAWBERRY 254/WOOL 131/EGG 79/MELON 72 units vs our WHEAT 326/CARROT 208); (2) se_loop's board is animal-blind while the tape's hands performed ~965 animal ops/season (FEED 310/CARE 328/COLLECT 327) - the animal economy that carries most revenue never starts; (3) the engine commits HIRE/BUY_LAND atomically BEFORE same-turn sells (`kaggriculture.py:571,580,712`), so a weak early economy misses the step-151 land cliff permanently (our NE/SW/SE landed days 15/20/26 and never ripened). Both refuted paths (overlay AND hybrid ownership) point at the same conclusion: a competitive policy must own farmer, hands, hires, land, seeds, animals AND market as one plan-driven whole. Stage 2 does NOT open on this architecture; roster_owner.py is kept as the tested reference for the ownership machinery.
- [ ] R3 - SUPERSEDED as scoped: animals-on-owned-land over this stage-1
      architecture is not openable (the market-script clamp and the missing
      animal economy make the farm fail before animals matter). The remaining
      path is a full plan-driven policy consumer (farmer included), which is
      the planner-large completion - a user-scale decision, not a staged
      increment of this doc.
- [ ] R4 - Superseded together with R3 (same reasoning).

## Constraints

- Small simulations locally (minutes); any corpus-scale sweep on Kaggle.
- No changes to submissions or the active ladder pair.
- Keep the se_loop module untouched (it is the tested reference); the new
  agent imports from it rather than forking the board.
- ~400 authored lines per task is advisory, not a cap to golf toward.
