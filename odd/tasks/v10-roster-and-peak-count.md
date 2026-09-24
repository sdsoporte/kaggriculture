# Feature: V10 — roster mix and peak animal count (tape overlay)

Goal: settle, with a controlled local experiment, whether V10 should change the
livestock roster (goose vs cow vs sheep) and/or raise the peak animal count from
14-16 toward ~20 — and only build the winner. V10 is an overlay on the existing
720-step tape; it is not a new engine and not a new tape.

## Why this, and why now

- Final Submission Deadline **2026-09-30 23:59 UTC**. After it, the locked latest-2
  bots keep playing and a **Bradley-Terry win-rate tournament** produces the final
  leaderboard. Coin difference does not move the rating: **optimise win rate, not
  bank**. Matches between equals are decided by $2-7k margins.
- **Peak animal count is the only profile variable with a monotone positive
  relationship to score** across 47 corpus episodes: 15-17 -> $101,170 (n=8) |
  18-20 -> $106,243 (n=21) | 21+ -> $108,485 (n=21). Winner mean 20.4; we peak at
  14-16. That gap is real and repeatable.
- The roster MIX recommendation is **contradicted between two evidence sources**,
  so it cannot be decided by argument - it needs a local head-to-head measurement.

## The unresolved conflict (the reason for the experiment)

| Source | Claim |
| --- | --- |
| Marginal model over 40 real observed inventory paths; backed by published `data/reference-agents/crop_economics.csv` (goose never saturates $220,308; cow $6,181; sheep $7,928) | +6 GOOSE = **+$6,189**; +6 COW = -$5,340; +6 SHEEP = -$9,082 |
| 47-episode corpus of top-episode replays | winners are **cow-heavy** on average; goose buckets non-monotonic; NO goose advantage |

The corpus evidence is methodologically weak: selected sample of top episodes,
not independent observations (the same team repeats), and it buckets the
*winner's* roster - i.e. it conditions on the outcome. It is enough to stop us
asserting the goose thesis, not enough to assert the opposite. The marginal model
prices a counterfactual on observed inventory, which is the better method, but
it is still a model.

## Acceptance criteria - what "ampliamente superior" means

User decision (2026-09-24): **no submission until V10 is validated as broadly
superior.** Quantified so it is falsifiable:

1. Measured by `scripts/benchmark.py` (paired, same seeds, **both seats**),
   >= 12 seeds (>= 24 games) per matchup, against the current best tape
   `submissions/main_v8.py` (the 1266.9 Elo bar) - **not** against V9/V9.1.
2. "Broadly superior" requires ALL of:
   - win rate **>= 65%** of paired games (not a coin flip);
   - mean margin **>= +$5,000** (real matches are decided by $2-7k);
   - **no structural regression**: the added animals actually appear, are fed,
     and there are zero animal-starvation events;
   - runtime stays inside the submission budget (actTimeout 1s, 1.6 vCPU).
3. Judgement metric is paired win rate / margin, **never peak bank**. V9 is the
   proof: a $153,624 ceiling ranked below a $98k tape.
4. A tie or an ambiguous result is a FAIL: the incumbent slots (56513137,
   56501811) stay untouched.

## Tasks

- [x] T1 Harness sanity + control - **PASS** (2026-09-24). See the Calibration
      section below for the measured curve.
- [x] T2 Structural assertions - **PASS** (2026-09-24).
      `scripts/assert_overlay.py` + `runner.run_episode_trace()`. Verified on three
      cases: a no-op FAILS, a roster overlay PASSES, a market-only overlay PASSES
      with the right note. See the T2 section below.
- [x] T3 Build the variants - **DONE, but the design changed** (2026-09-24).
      The tape cannot host MORE animals; it can host a different MIX at the same
      slots. The swap also has to start at step 150. See "T3 variants" below.
- [x] T4 Run the paired benchmark for each variant vs the baseline: 12 seeds
      (24 games) per variant - DONE (2026-09-24), 102-108 s each.
- [x] T5 Analysis + decision gate - **VERDICT: FAIL**. See the T5 section below.

## Calibration from T1 (measured, 2026-09-24)

| Control | games | A wins | A losses | ties | A mean margin |
| --- | --- | --- | --- | --- | --- |
| Mirror: main_v8 vs main_v8 | 24 | 2 | 2 | 20 | **0.0** (seat0 -111.7 / seat1 +111.7) |
| Small real difference: main_v9_1 vs main_v8 | 24 | 16 | 6 | 2 | **+922.7** |
| Large difference: main_v8 vs main_v4 | 16 | 16 | 0 | 0 | **+132,193.6** |

Findings:

1. **Deterministic.** 20 of 24 mirror games produced exactly equal banks. The two
   that did not (seeds 0 and 11) are perfectly antisymmetric under the seat swap
   (seat0 -644 / seat1 +644; seat0 -696 / seat1 +696). So the engine has a real,
   seed-dependent, seat-ordered asymmetry - not random noise.
2. **Noise floor ~ +/-$700 per game, mean exactly 0.0.** Any mean margin of a few
   hundred dollars is indistinguishable from seat ordering. That is why the bar is
   +$5,000, roughly 7x the observed floor.
3. **Sensitivity is adequate.** A +$922.7 real difference is clearly resolved
   (16W-6L-2T) over 24 games, so the harness detects effects an order of magnitude
   below the bar. Sample size is not the limiting factor.
4. **Reproducibility across sessions.** This run reproduces the previously recorded
   16W-6L-2T / +$923 for the same matchup exactly.
5. **Timing.** ~4.3 s/game; a 24-game matchup costs ~105 s, so four variants cost
   ~7 minutes total.
6. **The bar deliberately rejects a real, SHIPPED improvement.** main_v9_1 (the
   JIT-melon + textile-pivot overlay, live at 1222.8) reaches only 66.7% of games
   and +$922.7. The acceptance criteria are therefore stricter than "a real
   improvement exists": they demand an edge big enough to justify evicting an
   active slot, not merely one that is statistically visible.

## T2 checker (verified, 2026-09-24)

`scripts/assert_overlay.py` runs two matches and compares LIKE WITH LIKE:

    run A: variant  on seat 0 vs baseline on seat 1
    run B: baseline on seat 0 vs baseline on seat 1   <- control, same seat

The control MUST sit at the same seat. Comparing seat 0 against seat 1 inside a
single game conflates the overlay with the engine's seat-ordered asymmetry, which
T1 measured as real and seed-dependent (up to $696, flipping sign with the seat).
With the same opponent on seat 1 - a tape, which never reads the opponent - the
seat-1 side behaves identically in both runs, the seat bias cancels, and any
difference in the seat-0 trace is attributable to the overlay.

It FAILS when both the season roster and the full per-turn market-order stream
match the control, and it asserts `--expect TYPE=N` peaks plus zero escapes
(escape = the authoritative starvation rule, `kaggriculture.py:805-832`).

Verification - each case exits as required:

| case | exit | roster divergence | order divergence | verdict |
| --- | --- | --- | --- | --- |
| `main_v8` vs `main_v8` (no-op) | 1 | NONE | NONE | FAIL: overlay never landed |
| `main_v8` vs `main_v4` (real roster change) | 0 | day 1 | turn 1 | PASS |
| `main_v9_1` vs `main_v8`, seed 0 | 0 | NONE | **turn 2** | PASS, market-only note |

Blind spot found and closed during T2: a roster signature alone cannot see a
**market-only** overlay. `main_v9_1`'s JIT melon overlay changes the order stream
at turn 2 while the farm stays identical, and its textile pivot never fired at
seed 0 because the pivot is conditioned on a `YARN_STORE` unlock that did not
roll. The checker therefore also compares the full per-turn order stream and
prints "roster unchanged - market/order-side only" instead of passing a roster
expectation it cannot meet.

## T3 variants (built and gated, 2026-09-24)

Design change forced by measurement. The plan was A baseline / B +4 COW / C +4
SHEEP / D +4 GOOSE. Profiling the tape (`scratch/tape_profile.py`) settled it the
other way:

- the season ends with **0 free structure slots** (COOP 3/3, PASTURE 14/14);
- only **7.4% of worker turns are idle** (529 of 7150);
- **shed wheat stays at 0-47**, so the feed budget has no slack;
- structures are built by WORKER actions, not market orders (0 BUILD_* orders).

Adding animals needs new structures, extra placements and ~15 feed actions each -
a rescheduling problem, not a string swap. A cheap version would also be an
UNFAIR test: an animal added late, when cash is plentiful but ~10 days remain,
measures timing rather than animal value.

So the variants change the MIX at the same slots, turns and hand positions:
`v10_mix.py` (transform) plus `v10_cow.py`, `v10_sheep.py`, `v10_goose.py`.

Second finding: **the swap must start at step 150.** A full-roster overhaul from
step 0 STALLS - the early game leaves $24 on day 0 and HIRE success depends on
money, so a cost change shifts the hire pattern and desynchronises the whole hand
choreography. Measured on the cow variant from step 0: peak COW 7 instead of 14,
only 7 of 14 pastures built, 2 of 3 geese lost. From step 150 cash is plentiful
($610 and rising) and the swap lands cleanly - the same threshold the V9.1 textile
pivot uses.

Gate results (`scripts/assert_overlay.py`, seed 0; peak counts derived from the
tape's buy schedule, and the divergence turns confirm it - cow diverges at turn
196, its first post-150 SHEEP buy; sheep and goose at turn 151, the first post-150
COW buy):

| variant | peak roster | peak structures | first divergence | escapes |
| --- | --- | --- | --- | --- |
| control (main_v8) | COW 9 / SHEEP 5 / GOOSE 3 | 4 COOP / 14 PASTURE | - | 0 |
| `v10_cow` | **COW 12** / SHEEP 2 / GOOSE 3 | 4 / 14 | day 9 (turn 196) | 0 |
| `v10_sheep` | COW 4 / **SHEEP 9** / GOOSE 3 | 4 / 14 | day 7 (turn 151) | 0 |
| `v10_goose` | COW 4 / SHEEP 2 / **GOOSE 11** | **12 COOP / 6 PASTURE** | day 7 (turn 151) | 0 |

**The tool itself is verified**: a NULL swap (COW -> COW through the same
machinery) reproduces the control bit-for-bit - identical roster AND identical
full order stream - so the checker reports "overlay never landed". That is what
makes the three PASSes above meaningful.

`tests/test_v10_mix.py` (10 unit tests) pins the transform, including the bug the
first version shipped: the BUILD swap emitted the structure NAME instead of the
build VERB (`["COOP"]` instead of `["BUILD_COOP"]`), which the engine ignores
silently - that is how the first goose variant produced zero structures and zero
animals with no error anywhere.

## T5 verdict (2026-09-24): FAIL - V10 does not ship

| variant | win rate | mean margin | seats agree | verdict |
| --- | --- | --- | --- | --- |
| `v10_cow` | 41.7% (10-14) | **-$9,072** | 12/12 seeds | FAIL |
| `v10_sheep` | 50.0% (12-12) | +$1,933 | 12/12 seeds | FAIL |
| `v10_goose` | 41.7% (10-14) | **-$1,998** | 12/12 seeds | FAIL |

Acceptance was >=65% win rate AND >=+$5,000 mean margin. Nothing came within a
factor of five on margin, and two of the three are actively harmful.

**Robustness.** In all 36 seeds the sign agrees across the seat swap (mixed = 0),
so these are systematic deterministic effects, not noise - the same property T1
found in the mirror. Consequence: the sheep variant's 12-12 with +$1,933 is a
genuine coin flip, not a narrow win, and a 50% win rate is worth ~0 Elo in the
Bradley-Terry final.

### What it settles

1. The corpus finding "winners are cow-heavy" was selection on the outcome: making
   OUR roster cow-heavy costs $9,072. Correlational, now refuted causally.
2. The marginal model's "+6 GOOSE = +$6,189" is refuted head-to-head (-$1,998 for
   geese). That agrees with the measured per-tile revenue (GOOSE $2,228 < COW
   $3,219 < SHEEP $3,506) and with the earlier falsification of the goose thesis.
3. The documented open conflict (marginal model vs corpus) therefore CLOSES as: do
   not tilt the roster either way. The baseline mix wins.

**Decision**: no submission, no slot eviction. The incumbents stay - `56513137`
(the V8 recovery, converging) and `56501811` (V9.1 at 1222.8) - and play out to the
2026-09-30 deadline. V10 as a tape overlay is closed.

**Why this is a result and not a failure to try**: the bar required an edge worth
evicting an active slot, and the experiment was built so that a silent no-op could
not pass it (T2 landing gate, plus the null-swap proof of the wrapper). Three
variants, 72 paired games, zero escapes, zero landing failures - and a clean no.
That is worth more than a hopeful submission.

## Constraints / non-goals

- **The tape cannot be regenerated.** Verified: `_BLOB` is base64+zlib JSON, there
  is **no encoder, no solver and no generator** in the repo (0 matches for
  `b64encode|b85encode|zlib.compress`), and `tails` is reconstruction-only. V10 is
  an overlay or it is nothing. Hand-authoring new routes is out of scope.
- **A goose overlay is not a one-line swap.** The proven COW->SHEEP template
  (`baseline_v9_1.py:557-570`) works because both share the pasture surface. GOOSE
  sits on a COOP, so it needs a coupled `BUILD_PASTURE -> BUILD_COOP` +
  `COW -> GOOSE` + `MILK -> EGG` swap at the same turns/coords. A partial swap is
  swallowed by `_noop` - which is exactly what T2 exists to catch.
- **SE / 4th quadrant is deferred.** `main_v7_1.py:645-650` buys land but authors no
  SE actions, and the tape has no SE movement schedule; idle tiles and desynced
  hands are the risk. Not in V10 unless T1-T5 come back clean and early.
- **No submission, no Kaggle upload, no slot eviction** without a new explicit
  decision. Daily quota is 5; the binding constraint is the 2 active slots.
- Deadline discipline: if T5 does not produce a clear PASS, the correct outcome is
  to stop, not to ship a hopeful variant.

## Evidence log

- 2026-09-24 - feature opened. Evidence base: the verified tape data model
  (decode path, absence of generator, `tails` limits), the falsified goose
  *average*-revenue thesis, the shop-draw distribution, and the 47-episode corpus
  scale findings. All recorded in Engram.
- 2026-09-24 - user constraint recorded: no submission until V10 is broadly
  superior.
- 2026-09-24 - **T1 PASS**. Mirror 24 games 2W-2L-20T, mean margin 0.0, max
  per-game asymmetry $696 (antisymmetric under seat swap). main_v9_1 vs main_v8
  16W-6L-2T / +$922.7, reproducing the historical +$923. main_v8 vs main_v4
  16W-0L-0T / +$132,193.6. ~4.3 s/game. Raw logs: `/tmp/t1_mirror.log`,
  `/tmp/t1_v91.log`, `/tmp/t1_v4.log`.
- 2026-09-24 - **T2 PASS**. `scripts/assert_overlay.py` (new) +
  `runner.run_episode_trace()` (additive; `run_episode` unchanged, mirror benchmark
  reproduces the T1 banks exactly for seeds 0-2, including the -644/+644 seat
  asymmetry). No-op detector FAILS on the mirror, PASSES on a roster change, and
  PASSES with a market-only note on `main_v9_1` (order divergence at turn 2).
  pytest 3 passed. Logs: `/tmp/t2_mirror.log`, `/tmp/t2_v4.log`, `/tmp/t2_v91.log`.
- 2026-09-24 - **T3 DONE** (design changed, see the T3 section). `v10_mix.py` +
  `v10_cow.py` / `v10_sheep.py` / `v10_goose.py` + `tests/test_v10_mix.py`. All
  three variants PASS the landing gate at seed 0 with zero escapes; derived peaks
  match (COW 12 / SHEEP 9 / GOOSE 11). Null swap proves the wrapper is faithful.
  pytest 13 passed. First goose attempt (structure name instead of BUILD_ verb)
  produced 0 structures / 0 animals - caught by the gate, then pinned by a test.
- 2026-09-24 - **T4/T5 DONE. VERDICT: FAIL.** 12 seeds (24 games) per variant vs
  `main_v8`: cow 41.7% / -$9,072, sheep 50.0% / +$1,933, goose 41.7% / -$1,998.
  Seat-sign agreement 12/12 for all three (systematic, not noise). No submission.
  Logs: `/tmp/t4_cow.log`, `/tmp/t4_sheep.log`, `/tmp/t4_goose.log`.
