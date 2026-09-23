# Laya Integration Plan for Kaggriculture

**Status:** strategy assessment, pre-implementation.
**Author:** el Gentleman (Pi session), 2026-09-21.
**Depends on:** `docs/kaggriculture-research.md` (competition research), our Laya
fine-tuning pipeline (Kaggle RLCD, checkpoint `~/dev/laya-bakeoff/ko6`, local
server on `127.0.0.1:8377`).

---

## 1. The constraint that shapes everything

Competition agents are **single-file, stdlib-only** submissions.

Evidence: all 10 reference agents in `data/reference-agents/` import only
`base64`, `copy`, `json`, `math`, `zlib`. No numpy, no torch, no network.

Implications:

- **Laya cannot BE the submitted agent.** It is a 421M-param transformer
  (~810MB weights) that needs torch. It will never run inside the
  kaggle-environments sandbox.
- The `base64` + `zlib` imports in the reference agents are the tell: valid
  submissions **embed compressed data** (replay tapes, tables) inside the
  single file. That is the legal vehicle for anything learned offline.
- Per-action budget is ~1s. A small pure-Python forward pass (`math` only)
  comfortably fits; a transformer does not.

## 2. Where Laya fits — three roles, ranked

### Role 1 — Offline opponent fingerprinting (highest value)

The research report (§3.6) shows the only documented strategy that beat the
replay tapes: a **detector-gated parameter switch** — fingerprint the opponent
in the first 4 days, flip one allocation parameter only on a confirmed lineage,
byte-identical baseline otherwise.

Laya is literally a calibrated classifier over natural-language states:

- **Data:** `kaggle/kaggriculture-episodes-*` (~13M obs-action rows/day).
- **Question shape:** `choice` over known lineages (frontier-clone, tape
  replay, Pilkwang-style, random/unknown) given a textual summary of the
  opponent's first-4-day behavior; `noul` for "is this a replay tape?";
  `score` for confidence-graded aggression.
- **Training:** our existing RLCD pipeline on Kaggle (2×T4, ~10 min runs),
  already exercised 3 times for the Pi gate model.
- **What ships in the agent:** NOT Laya — a distilled rule table / tiny
  classifier (see §3) derived from Laya's decisions on labeled episodes.

### Role 2 — Offline market-timing analysis

`choice`/"sell now vs wait" and `score` questions over corpus states to learn
sale-cadence patterns (melon batching, wool/strawberry late batches, wheat
into the day-28/29 peak). Output: documented heuristics and parameters for
the rule-based market layer — the 5% PvP that decides games between equals.

### Role 3 — "Ultra-small Laya" as shipped brain (via distillation only)

If we want a contest-trained model INSIDE the submission, the legal path is:

```text
1. RLCD fine-tune Laya on the episode corpus (behavior cloning of the
   frontier program: state -> choice over candidate actions)
2. Distill to a tiny MLP (100–500K params) trained on Laya's SOFT outputs
3. Embed weights as base64+zlib string in the single-file agent
   (same trick the reference agents use)
4. Pure-Python forward pass with `math` (~10–100ms/action — fits budget)
```

**Honest caveat:** behavior cloning of the frontier program ≈ a *learned
tape* — and the research shows tapes are dominant but fragile (one engine
patch kills them). This role is a hedge and a learning exercise, not the
primary strategy.

## 3. What Laya is NOT good for here

- Whole-policy replacement: the game is ~95% solitaire optimization where
  the labor scheduler (5% vs 18% idle) and phase machine dominate. Those are
  symbolic/numeric problems, not calibrated-judgment problems.
- Runtime inference inside the sandbox (see §1).
- Anything that skips paired benchmarks. "No unpaired verdicts, ever"
  applies to Laya-derived parameters too: every Laya-informed switch gets
  validated through `scripts/benchmark.py` (same seeds, both seats).

## 4. Priority under the real deadlines

Entry deadline **2026-09-23** · final submission **2026-09-30** (~9 days).

| # | Work item | Laya involved? |
| --- | --------- | -------------- |
| 1 | Pilkwang-shaped rule policy (phase machine + job matcher) | No |
| 2 | Labor scheduler closing the 5%-vs-18% idle gap | No |
| 3 | Paired-benchmark discipline on every change | No |
| 4 | Opponent fingerprinting — the cherry | **Yes, offline** |
| 5 | Distilled micro-model — if time remains | **Yes, distilled** |

Roles 1–3 of the table are the base game; Laya is the accelerator, never the
foundation.

## 5. Proposed next steps

1. Measure the starting point: `scripts/benchmark.py baseline_v0` vs the
   tier-ladder reference agents (paired, ≥8 seeds).
2. Build the phase-machine policy and scheduler on that baseline.
3. In parallel (cheap): download one day of `kaggle/kaggriculture-episodes-*`
   and prototype the fingerprinting question set against `127.0.0.1:8377`
   (our local Laya server answers Jev-compatible `POST /v1/systemone`).
   Zero-cost exploration before committing a Kaggle training run.

---

*Cross-reference: the Laya training pipeline, bake-off evidence, and two-tier
gate integration live in `~/dev/laya-bakeoff/` (results.json,
eval_v4_results.json) and the `jev-system-one-pi-decision-making` memory topic.*
