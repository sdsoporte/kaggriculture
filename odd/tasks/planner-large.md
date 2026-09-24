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
- [ ] W2a — **Gate: express the tape itself as a `kaggriculture-plan/1` vector.**
      This step exists because the schema was frozen fast and W1 is already
      authoring against it. If the schema cannot describe our OWN tape, an LLM
      plan written against it is worthless. It also yields the calibration
      baseline and the search's start point.
- [ ] W2b — `src/kaggriculture/agents/param_agent.py`: the vector consumer, with
      the schema's eight invariants each pinned by a unit test.
- [ ] W2c — Calibration: the tape's own vector, consumed, must reproduce the
      tape's bank within noise. If it does not, the consumer is unfaithful and a
      search result is uninterpretable.
- [ ] W2d — Offline search over the vector with the engine as simulator.
- [ ] W2e — Bake the winning vector into a single-file submission candidate.

## Acceptance criteria

- **W2a and W2c are the gates.** If the schema cannot express the tape, fix the
  schema before consuming any LLM plan (W2a). If the tape's own vector consumed
  does not reproduce the tape's bank, the consumer is wrong, not the search (W2c).
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
