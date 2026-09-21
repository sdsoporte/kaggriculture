# Feature: phase3 — hired labour + crop rotation

Goal: move the agent from tier 1 to the tier-2 gate without destabilizing the
validated `baseline_v1`.

The target is a new, self-contained `baseline_v2` agent that hires four hands
and runs a Wheat/Carrot/Tomato rotation on the unlocked quadrant. It must stay
stdlib-only and emit the Kaggriculture single-file contract. `baseline_v1` is a
frozen control, not a file to mutate.

## Decisions

- New implementation: `src/kaggriculture/agents/baseline_v2.py`; preserve
  `baseline_v1.py` byte-for-byte.
- Build a compact scheduler for crops and logistics rather than copying the
  673-line reference scheduler wholesale. Use `rotation_rosa.py` as a
  read-only behavioral reference for contract details and economic ordering.
- Hires happen at day start, target four hands, and are ordered safely with
  market operations. The scheduler assigns the farmer and hands independently
  using nearest-job routing and explicit carry/drop jobs.
- Policy scope is wheat/carrot/tomato, no land, animals, fertilizer, Laya, or
  opponent fingerprinting in this feature.
- Every policy increment is judged with paired benchmarks (same seeds, both
  seats); no unpaired ladder claims.

## Acceptance criteria

- `baseline_v1.py` remains unchanged and continues to pass its existing
  matchup evidence.
- `baseline_v2.py` is single-file, import-free/stdlib-only, exposes `agent`,
  and runs a complete local 720-turn episode without invalid-contract errors.
- Regression gate: v2 beats `fallow_finn` and `wheat_walter` in both seats on
  the standard four-seed control set.
- Improvement gate: v2's mean margin against `rotation_rosa` is strictly
  better than v1's recorded −5,247.2. The tier-2 claim requires v2 to win the
  full paired gate; otherwise document the remaining gap and next lever.
- Tests and benchmark evidence are recorded before the feature is closed.

## Tasks

- [ ] C1. Freeze controls, acceptance criteria, and v2 architecture
- [ ] C2. Implement four-hand hiring and market budget ordering
- [ ] C3. Implement compact multi-unit crop/logistics scheduler
- [ ] C4. Add wheat/carrot/tomato rotation and crop-aware sales
- [ ] C5. Run paired controls + full ladder; record tier placement in `docs/`
- [ ] C6. Prepare `submissions/main_v2.py`; request explicit upload decision

## Non-goals

- No mutation of `baseline_v1.py` or the phase-2 submission.
- No Kaggle upload without a separate explicit user decision.
- No Laya training, runtime inference, opponent fingerprinting, land purchase,
  animals, fertilizer, or market meta beyond the three-crop policy.

## Evidence

- (commits recorded per task)
