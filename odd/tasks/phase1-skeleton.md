# Feature: phase1 skeleton

Goal: runnable project skeleton — competition kit, local simulator with the real
contract, paired-benchmark harness, and a rule-based baseline agent evaluated
against the reference agents (tiers 0-9).

Decisions:
- Laya deferred to phase 2 (optional typed-decision layer).
- Architecture: Pilkwang-shaped (phase machine + job matcher), scheduler-first focus.
- Methodology: paired benchmarks only (same seed/seat/opponent); no unpaired verdicts.

## Tasks

- [x] Setup Python env + kaggle CLI, verify API access
- [x] Download competition kit + reference agents + engine tables
- [x] Inspect kit: obs/action contract, simulator, submission rules
- [x] Project skeleton: package layout + baseline agent
- [x] Paired-benchmark harness (same seed/seat/opponent)
- [x] Baseline run vs reference agents + report

## Evidence

- b24e022 feat: phase-1 skeleton — paired-benchmark harness, runner, loader, baseline v0
- Report: `docs/phase1-baseline-report.md` — baseline_v0 lands between tier 0
  and tier 1 (beats fallow_finn +2131, loses to wheat_walter −1808, 8 games
  each, paired). Diagnosis: over-planting, seed-money bleed, water-first
  starvation → only ~4 productive plants. Phase 2 levers: cap field to
  service capacity, harvest>water urgency, then hired labour.
