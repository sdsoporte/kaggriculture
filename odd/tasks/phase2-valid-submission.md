# Feature: phase2 — valid submission + baseline_v1

Goal: two tracks, deadline-ordered.

**Track A (blocking, deadline 2026-09-23):** competition joined + one valid
remote submission that validates the packaging/submission pipeline end-to-end
(agente aunque sea `baseline_v0`; el contenido no importa, el pipeline sí).

**Track B:** `baseline_v1` — fix the three defects found in the phase-1
report (over-planting, seed-money bleed, water-first starvation), each fix
verified with paired benchmarks only. No unpaired verdicts.

Decisions:
- Order agreed 2026-09-21: pipeline validation BEFORE policy work. A format
  or sandbox surprise discovered on the 29th is unrecoverable.
- Laya fingerprinting prototype is a separate feature (offline, parallel,
  not on the critical path).
- Every policy change is verified with `scripts/benchmark.py`, paired,
  same seeds both seats.

## Tasks

- [x] A1. Verify Kaggle CLI auth + competition join status (join before 2026-09-23)
- [x] A2. Package single-file submission from `baseline_v0` (stdlib-only, no imports beyond allowed set)
- [x] A3. Submit + verify the remote episode completes (pipeline validated end-to-end)
- [x] B1. v1a: cap planted field to one-farmer service capacity (~5 wheat tiles); paired benchmark vs finn + walter
- [x] B2. v1b: harvest outranks watering for mature tiles + batch harvest/carry trips; paired benchmark vs finn + walter
- [x] B3. Re-run full ladder with v1 + record new placement in `docs/`

## Evidence

- A1: `kaggle competitions list` shows userHasEntered=True (deadline 2026-09-30; entry OK).
- A2: `submissions/main_v0.py` — zero imports, plays locally; commit "feat: submission v0 — packaged baseline_v0 for pipeline validation".
- A3: Kaggle submission 56442827 status COMPLETE, public score 481.0 — remote pipeline validated.
- B1: commit `7098410` feat: baseline_v1a — FIELD_CAP=5 flips Walter to 8-0 (+446); sharp optimum (cap 6: −425, cap 7: −712).
- B2: commit "feat: baseline_v1b — harvest outranks watering, +558 vs Walter"; vs Rosa still −5,247 (labor lever is next feature).
- B3: full v1 ladder recorded in `docs/phase1-baseline-report.md` (Update section); placement: owns tier 1, gate to tier 2 is hired labour.
