# LLM in the loop: engine reader, adversarial farmer, planner author

## Goal

Put a model inside the loop in the three places where it can actually change an
outcome, instead of only asking it to comment on a prose brief. The first two
attempts (the adversarial review and the economist) both consumed a ~5 KB
hand-written summary of our own numbers; neither ever saw the engine or a match.

Stage A exists because of a measured failure, not a hunch. The economist
(gemini-3.7-flash, `kaggriculture-economist` v2) proposed "eliminate the 66
fertilizer buys" as a **+$5,500 to +$6,600** cash leak. Measured: **-$210**, a
wash. The engine quotes `BUY_PRODUCT` at the POST-buy inventory explicitly so
that a buy/sell round trip nets zero (`kaggriculture.py:599-601`). The model could
not have known: the brief paraphrased the market and omitted that rule. **The
failure was missing mechanics, not missing data.**

## Stages

- **A — engine reader.** CLOSED (A1-A4). See the evidence below.
- **B — adversarial farmer.** A model plays, through the arena, with the goal of
  punishing our tape rather than beating it on average. Our whole evaluation
  problem is that every opponent we have is too weak (10 of 10 published
  reference agents beaten by $100k+), so any variant of our tape also wins; an
  adversary is the only new source of pressure available.
- **C — planner author.** **MIGRATED to `odd/tasks/planner-large.md`** (C-Largo).
  It was scoped here as a stage, but it is a feature of its own: it needed a frozen
  parameter schema, a consumer, an offline search and a bake step, and it is the
  only workstream that can move the score before the 2026-09-30 deadline. Keeping
  it here as one checkbox would have hidden its size. Stage B stays in this
  feature and is now its only open work.

## Tasks

- [x] A1 — `kaggle/bench/build_engine_spec.py` generates a self-contained
      Benchmarks task with the engine source embedded.
- [x] A2 — Run A on `gemini-3.7-flash` and `claude-opus-4-5`; diff the returned
      rule sheet against our own list of engine rules and traps.
- [x] A3 — Fold the deltas into `docs/engine-rule-sheet.md`, a verified rule sheet
      that is now the appendix for every future brief.
- [x] A4 — Measure the atomic-PLANT loss against our own tape: **incidence zero**,
      closed as a null.
- [ ] B1 — Arena plumbing: pass the model-proxy credentials into the notebook and
      prove one LLM call happens inside a match.
- [ ] B2 — Bounded adversarial farmer: daily decisions only (30 per game), tape as
      the executor, against our tape, ~20-30 games.
- [ ] B3 — Harvest the losses: where does our tape get punished, and is it a
      mechanism we have not closed.
- [x] C1, C2 — **migrated** to `planner-large` as W1/W2. Not tracked here.

## Stage A evidence (A1-A4)

### A1 — generator

`kaggle/bench/build_engine_spec.py` embeds the engine source (40,356 chars,
1,086 lines) into a self-contained task (43,716 chars) instead of hand-copying it,
so the rule sheet cannot drift away from the engine version. The source is located
by glob and the script fails closed if the pattern matches zero or several files,
and refuses to generate if the source ever contains a triple single quote (the
embedding delimiter). `--check` makes staleness detectable.

### A2 — the diff (both models completed)

Pushed as `ser8147/kaggriculture-engine-spec` version 1 with the default model
(`gemini-3.7-flash`), then run against `claude-opus-4-5-20251101`. Both returned a
rule sheet; both **independently converged on the same four mechanics we were
missing**, which is the strongest signal available from a two-model design:

1. **Atomic PLANT validation** (`:919-931`) - if a turn's total `PLANT` demand for a
   crop exceeds the seeds held, EVERY `PLANT` of that crop becomes `PASS`. Not a
   partial plant: the crop is voided for the turn.
2. **A sale at the $1 floor does not move the market** (`:659-660`,
   `if price > 1: inventory += 1`). Dumping at the floor is free and cannot crater
   the opponent's price.
3. **Movement onto a LOCKED tile is allowed** (`:328-330`); only tile *operations*
   no-op there.
4. **A placed animal cannot be removed** (`:484-490`) - `DIG` returns early when the
   tile holds an animal. This is the structural reason the roster swaps could only
   ever be payload swaps.

**The models' line numbers are worthless.** `gemini-3.7-flash` cited
`_process_market (L407)` when the real line is **544**, and stated it flatly with
no hedge; `claude-opus-4-5` prefixed every reference with `~`. Every row in
`docs/engine-rule-sheet.md` was therefore re-read by me in the source, and the
models' numbers were discarded. This is the concrete reason stage A's acceptance
criterion was a diff rather than an applause: the output is useful and unreliable
in the same breath.

Both also confirmed the anti-arbitrage rule we had just paid for - `BUY_PRODUCT`
quoted at `inventory - 1` so a round trip nets zero (`:599-601`) - which is the
mechanics the economist's brief had omitted.

### A3 — the artifact

`docs/engine-rule-sheet.md`: the four new rules with verified line numbers, the
full market and physical tables, and the provenance (task, models, generator,
verification method). It also records one conflation this project made: **MELON's
watering window is days 6-12 while its harvest window is days 10-12** (`:16`,
`:440-441`) - both had been called "the melon window".

**The new hypothesis A produced.** The atomic PLANT rule is the only mechanic found
that can silently destroy work the tape believes it did. Our tape plants ~170 WHEAT
and 33 STRAWBERRY with hand-timed `BUY_SEED` orders, so any turn whose PLANT demand
for a crop exceeds stock plants *nothing* for that crop. Measure it read-only from
traces: per game, the turns where `plant_demand[crop] > seeds[crop]` and the
plantings lost. This is a measurement, not an overlay, and it is a candidate for the
next concrete lever.

### A4 — the null, and the indexing bug that produced a false positive first

The atomic PLANT rule was the most promising output of stage A: the only mechanic
found that can silently destroy work the tape believes it did.
`scripts/atomic_plant_probe.py` (8 mirror episodes = 16 observations of our tape)
measures its incidence read-only from traces.

**Result: 0 blocked turns, 0 voided plantings**, against 239-240 `PLANT` actions
per seat-game. Our tape's seed ordering never lets a turn's plant demand exceed its
stock. The rule is real in the engine and **theoretical for our tape**: closed as a
null rather than carried as a lead. The three rule-sheet findings that remain
stand on their own (the $1 floor, walking onto LOCKED, irremovable animals) and
none of them is a lever either.

**The first run reported 7.91% of all PLANT actions voided, and that was a false
positive of the exact class this project keeps hitting.** The probe compared
`action[i]`'s plant demand against the seeds in `steps[i]` - but the action is the
one that PRODUCED that observation, so it acts on the pre-state at `steps[i-1]`,
which is where the engine reads the seed table. Reading the same index shows seeds
already consumed by this turn's own planting and invents blockages. The tell was
arithmetic: the count implied more plantings than the tape has plantings to lose
(it measurably lands 239 of its 240), which is why the contradiction was chased
instead of reported. **This is the fourth time `steps[turn][seat]` has bitten this
project.** The semantics are now settled in the probe's docstring so it does not
happen a fifth time.

## Acceptance criteria

- A: the returned rule sheet is **diffed**, not admired. Success is the model
  naming a rule or trap our own list misses, or naming a wrong one (which is an
  equally useful finding about the model).
- B: success is a **punishing line**, not a better average. If the LLM adversary
  never gets a positive margin, that is evidence our evaluation blind spot is
  smaller than feared.
- C: accepted only if paired measurement beats the tape with a CI excluding zero.

## Non-goals

- Not shipping an LLM as the submitted agent. The submission is a single stdlib
  file with no network at match time, so a model cannot be the player on the
  ladder. The LLM plays only where we control the environment.
- Not a per-turn LLM policy: 720 turns x 1 call per game is not affordable, and
  the proxy rate-limits under load.

## Constraints / risks

- **The model proxy is IP-bound to the Kaggle kernel.** Run logs show
  `{"status":"BOUND","bound_ip":...}`: the credentials do not work from the
  workstation, so every stage runs server-side. B additionally needs the
  credentials handed to a notebook that is not a Benchmarks task.
- **Six days remain** (2026-09-30). Only C can move the score. A is cheap and
  enabling; B is an evaluation improvement and must not starve C.
- **B can produce findings that are interesting and inactionable.** Every overlay
  lever has died on the tape's structural coupling (the step-150 cash cliff, the
  per-crop choreography, the saturated slots). An adversary that exploits a
  structural gap does not imply a fix. The gate after A decides whether B is worth
  its plumbing.

## Evidence log

Stage A: see "Stage A evidence" above. Stages B and C: filled as they close.
