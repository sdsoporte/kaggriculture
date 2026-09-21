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
- TDD mode: **standard checks**, resolved by explicit user choice in this Pi
  session (`standard`); strict TDD is not active. Functional test runner:
  `.venv/bin/python -m pytest tests/ -q`. Agent smoke and paired evidence use
  `scripts/run_match.py` and `scripts/benchmark.py` separately.

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

- [x] C1. Freeze controls, acceptance criteria, and v2 architecture
- [x] C2. Implement four-hand hiring and market budget ordering
- [x] C3. Implement compact multi-unit crop/logistics scheduler
- [ ] C4. Add wheat/carrot/tomato rotation and crop-aware sales
- [ ] C5. Run paired controls + full ladder; record tier placement in `docs/`
- [ ] C6. Prepare `submissions/main_v2.py`; request explicit upload decision

## Non-goals

- No mutation of `baseline_v1.py` or the phase-2 submission.
- No Kaggle upload without a separate explicit user decision.
- No Laya training, runtime inference, opponent fingerprinting, land purchase,
  animals, fertilizer, or market meta beyond the three-crop policy.

## Evidence

- C1: plan committed as `1c074bf`; branch `feat/phase3-labour-rotation`; standard checks selected explicitly by the user, runner `.venv/bin/python -m pytest tests/ -q`.
- C2: `src/kaggriculture/agents/baseline_v2.py` added; import-free, preserves v1 wheat loop, targets four hands, orders `SELL → HIRE → BUY_SEED`, and returns one PASS per visible hand.
- C2 verification: `.venv/bin/python -m pytest tests/ -q` → 3 passed; local seed-0 match completed 719 turns and beat Fallow Finn 6832–3000; trace confirmed four HIRE orders then four visible hands with PASS actions.
- C2 commit: `67c532d` feat: baseline_v2 — four-hand hiring skeleton. Native review lineage `review-9e75ead7349d48b3` approved and acknowledged; R3-001/R3-002 warnings and R3-003 suggestion were non-blocking informational findings.
- C3: `src/kaggriculture/agents/baseline_v2.py` now uses `WHEAT_SERVICE_CAP=20`, deterministic nearest-unit single-use jobs, loaded-unit DROP routing before field work, and one action per farmer/visible hand; C2 hire/order behavior remains intact.
- C3 verification: `.venv/bin/python -m pytest tests/ -q` → `3 passed in 17.40s`.
- C3 smoke: `.venv/bin/python scripts/run_match.py src/kaggriculture/agents/baseline_v2.py data/reference-agents/fallow_finn.py --seed 0` → `719 turns`, A bank `7866`, B bank `3000`.
- C3 paired v1 control: `.venv/bin/python scripts/benchmark.py src/kaggriculture/agents/baseline_v2.py src/kaggriculture/agents/baseline_v1.py --seeds 4` → `7-1`, mean margin `+173.4` (seat0 `+175.5`, seat1 `+171.2`).
- C3 paired Walter control: same benchmark command against `data/reference-agents/wheat_walter.py` → `8-0`, mean margin `+466.6` (seat0 `+385.2`, seat1 `+548.0`).
- C3 trace: four HIREs at turn 0, four visible hands at turn 1, max 20 live plants, loaded units excluded from field assignment; optional `pyspiel` warning was non-blocking.
- C3 commit: parent will record the work-unit commit after this documentation update.

## Key Learnings

1. C3 combines bounded wheat capacity with deterministic nearest-unit single-use scheduling.
2. Loaded units must be dropped before field assignment, while each farmer and visible hand receives one action.
3. The recorded verification, smoke run, and paired controls provide the C3 evidence without closing C4-C6.
