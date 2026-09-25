# SE closed-loop agent: buy the 4th quadrant the way the winners do

## Goal

Turn the replicated event-study trajectory into an agent and measure it. The
study (`197dc0d`, Kaggle Lab v9/v10) shows what 4th-quadrant buyers do: buy the
SE quadrant around day 10, absorb a ~$5k mid-game cash deficit, convert the new
land into ~16 extra CROP tiles within ~3 days (71-72 vs 55-56 opponent) while
holding animals and hands, and cross into a positive median gap on days 22-25,
winning ~2/3 of 4v3 matches. The failed fixed-tape overlays bought the land but
authored no SE crop choreography and could not fund the deficit.

The hypothesis: a closed-loop overlay on the incumbent tape that
1. buys SE only when money >= $4,000 (land) + a seed reserve, and only from
   day 10, while 3 quadrants are unlocked;
2. immediately plants the new tiles with a parametrized crop mix (default
   biased to the cheap fast tiles so the reserve survives), watering every new
   tile on its planting day;
3. adds appended-only extra hands (never overriding a hand the tape owns);
4. places NO animal structures on the new land;
5. is byte-identical to the tape whenever the gate never fires.

## Non-goals and hard limits

- No submission, no slot eviction, until the benchmark says this is broadly
  superior (champion-retarder policy: measured evidence, never "parece mejor").
- Overlay only: the tape cannot be regenerated; the route and the tape's own
  action priority stay untouched.
- One market-order budget per turn (10) is SHARED with the tape's orders.
- A crop not watered on its planting day dies that night (engine rule sheet).
- Malformed/partial action payloads are silent no-ops - the third-time bug
  class (`["PLANT"]`, `["COOP"]`, `["TOMATO"]`); jobs carry full actions.
- Mirror-signature detection: mean margin ~0 with many ties means the overlay
  never landed; structural assertions are mandatory, not optional.

## Acceptance

Measured with `scripts/benchmark.py` (paired, both seats, >= 12 seeds = >= 24
games) against `submissions/main_v8_1.py` (the incumbent bar):
- win rate >= 65% AND mean margin >= +$5,000 to even discuss a submission
  (same bar the V10 roster experiment used);
- structural landing proof: SE unlocked in the gated runs, new crops planted
  and watered on planting day, zero new animal structures, zero escapes caused
  by the overlay;
- byte-identity when disabled;
- honest reporting: absolute bank printed beside every margin.

A tie or ambiguity is a FAIL and the verdict is recorded either way - a clean
no is a result, not a waste.

## Tasks

- [x] S1 - Worker `muhc90n7-c-x4qh` built `src/kaggriculture/agents/se_loop.py` (parametrized overlay, appended-only hands claimed only when both engine counters agree, tape-spend-aware land gate) plus `tests/test_se_loop.py` (35 tests, including byte-identity when disabled/unfunded and season-level landing evidence). Focused 35 passed, full suite 329 passed. Interpretation call disclosed: the house job board ships with HARVEST + carry-to-shed ON by default (harvest=False measured -$7,661.9 vs -$4,669.6; both branches fail the bar, so the call does not change the verdict).
- [x] S2 - Parent re-ran the required benchmark: reproduced exactly (24 games, 0 wins, mean margin -$4,669.6) and re-ran the focused suite (35 passed). **Verdict: FAIL - clean no.** Structural landing proven (SE unlocks day 11 every seed, ~14 distinct SE tiles planted, zero planting-night deaths, zero SE animal structures, order cap held, zero crashes), so the loss is real economics, not a no-op. Ceiling analysis: fixed spend ~$7.3k (land $4,000 + one appended hand ~$3,071 at fib(9..13) + seeds ~$610) against a ~$4-5k perfect-zero-walk hand revenue ceiling; a second hand is $3.7k worse; the shared market taxes both seats. The winners' advantage needs the two levers this overlay is forbidden: animal structures on the new land or driving the tape's own hand indices. No submission; S3 not reached.
- [ ] S3 - Not reached: the acceptance bar was not met, so no single-file submission candidate is baked. The verdict stands as the recorded result of the hypothesis test.

## Constraints

- Benchmark locally (small, minutes); any corpus-scale sweep goes to Kaggle.
- Animal placement is irreversible within a season; the overlay never places any.
- Deadline pressure is gone as a driver: this is a process/agent-improvement
  experiment for a future tournament, so a negative verdict is acceptable and
  must not be massaged.
