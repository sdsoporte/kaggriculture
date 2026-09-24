# C-Largo: the offline planner, authored by an LLM

## Goal

The tape sits at a measured local optimum: nine levers closed, every one of them
killed by structural coupling with the fixed schedule (the step-150 ~$1,000
`BUY_LAND` cash cliff, the per-crop choreography, the saturated slots). The
remaining headroom is real but unreachable from an overlay - the economic table
measures TOMATO +266 unproduced units, STRAWBERRY +253 and WHEAT +291 of
absorption, against a MELON already overproduced 2.2x. Capturing it requires
re-planning the season, not patching it.

This feature is that re-plan, split so the two halves cannot drift.

## The contract

`docs/planner-parameter-schema.md` is frozen and authoritative. A plan is a small
JSON vector of season-level decisions (`kaggriculture-plan/1`): quadrants and
land-buy days, a daily hand target and its ramp, per-crop tile counts with plant
and replant days, an animal roster with purchase days, and three market knobs.
The parameterised agent consumes it and keeps the proven per-turn machinery
intact. Deliberately NOT parameters: the route, the action priority order, and the
same-turn sell-then-buy coupling.

## Two workstreams

- **W1 - LLM author** (delegated): a Kaggle Benchmarks task in which a model
  authors a `kaggriculture-plan/1` vector, given the complete engine source, the
  verified rule sheet, the nine measured verdicts as hard constraints, the
  absorption table, and the schema. Two models, outputs diffed.
- **W2 - consumer and search** (parent): the parameterised agent that consumes the
  vector, plus the offline search over the bounded integer space using the local
  engine as the simulator.

## Tasks

- [ ] W1a — `kaggle/bench/build_planner_author.py` generates the author task with
      the engine source, rule sheet and verdicts embedded. **Delegated** to
      `gentle-ai-worker` as background task `mufxx0i1-1-mnzm`; allowed surfaces
      are only the two `kaggle/bench/` files and the worker must not commit.
- [ ] W1b — Extract both models' plans and diff them against our measured
      headroom (delegated with W1a; the diff and the verdict are mine).
- [x] W2a — **Gate: express the tape itself as a `kaggriculture-plan/1` vector.**
      **FAILED, by design, and that was the point.** The schema could not describe
      the tape: four fields had to change (see W2a2), and the vector turned out not
      to be deterministic across seeds. Evidence: `scripts/tape_to_plan.py`.
- [x] W2a2 — Schema revision 2: `hands.start` + `ramp_step` + `daily_target` (the
      tape uses seven distinct hand counts, a step function cannot describe a
      curve), `crops[].plant_days` (an explicit calendar; one period cannot express
      a non-uniform one), `crops[].fertilize_tiles` (the tape fertilizes 6 of 30
      carrot tiles), and `0` as the no-throttle sentinel on
      `market.sell_chunk_cap`. Version bumped to `kaggriculture-plan/2` so a plan
      written against revision 1 is **rejected rather than silently misread**.
- [ ] W2b — `src/kaggriculture/agents/param_agent.py`: the vector consumer, with
      the schema's eight invariants each pinned by a unit test.
- [ ] W2c — **Rewritten after W2a.** The original form ("the tape's own vector,
      consumed, must reproduce the tape's bank") is unachievable by construction:
      the schema deliberately does not describe the tape, because its fields are
      season-level targets the consumer realises against the live state. It is now
      an **internal-consistency check**: the expected plantings, animals and
      quadrants must actually happen. The comparison against the tape is the end
      acceptance test, not a calibration step.
- [ ] W2d — Offline search over the vector with the engine as simulator.
- [ ] W2e — Bake the winning vector into a single-file submission candidate.

## Acceptance criteria

- **W2a was the gate and it failed on purpose**, which is why the schema is now
  revision 2. W2c is a second gate, rewritten: the consumer must be internally
  consistent, and the tape comparison is the end test.
- The search result is accepted only if a paired benchmark beats the tape with a CI
  excluding zero, measured with the bank printed beside the margin.
- The LLM's plan is compared against our own headroom numbers. Its arithmetic is a
  proposal, never evidence - the economist's +$5,500 fertilizer leak measured
  **-$210**.

## Constraints / risks

- **Six days remain** (deadline 2026-09-30). This is a gamble with a real chance
  of not landing; the fallback is that the tape still stands.
- **Animal placement is irreversible** within a season (`kaggriculture.py:484-490`),
  so the roster is the least searchable dimension.
- **A season is ~3 s to simulate**, so the search budget is thousands of episodes,
  not millions. The search must be a local/coordinate search over the bounded
  integers, not a wide random one.
- **Baking the vector back into a tape is required for submission**, because the
  submitted agent is a single stdlib file with no engine available at match time.

## Evidence log

Filled as tasks close.
