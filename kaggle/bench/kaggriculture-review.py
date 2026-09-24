# %%
"""Adversarial review of our own analysis, run as a Kaggle Benchmark task.

Why this task exists: this project has repeatedly produced confident conclusions
that measurement later refuted - a flat-price valuation of marginal units, a
sell-layer "opportunity" that was a corpus selection artifact, and a
"fertilize wheat" recommendation that pointed the wrong way. The arithmetic is
code and needs no model; what needs a model is a sceptical reader holding the
numbers, the assumptions and the counter-evidence at once.

Runs server-side against Kaggle's hosted models (billed to the AI quota, no API
key of our own):

    kaggle b t push kaggriculture-review -f kaggle/bench/kaggriculture-review.py --wait
    kaggle b t run kaggriculture-review -m claude-opus-4-5-20251101 --wait
    kaggle b t log kaggriculture-review -m claude-opus-4-5-20251101
"""

import kaggle_benchmarks as kbench  # type: ignore[import-not-found]  # provided by Kaggle's task runtime, deliberately not installed in the project venv

# %%
FINDINGS = """You are reviewing an analysis of a Kaggle farming simulation
("Kaggriculture": 30 days, 720 turns, two players, market price is a
deterministic function of market inventory). Our agent is a FIXED 720-step
action tape. Mean bank on the ladder ~$91k, 55.6% win rate, rating ~1238
(the top of the ladder is ~3000+).

CLAIM UNDER REVIEW: fertilizing our melon crop would add roughly +$10.5k per
game, strawberry about +$3k, and fertilizing wheat would LOSE money. Therefore
the next engineering step is a fertilizer overlay that applies our currently
unsold fertilizer to melon (12 plantings/season) and strawberry.

ENGINE FACTS (verified in the engine source):
- WATER works once per day per tile and adds `yield_units` up to a cap, with
  bonus 2 instead of 1 when the tile is fertilized. The watering bonus applies
  only to non-ongoing crops inside a window: days (max_yield_day+1)//2 through
  max_yield_day after planting.
- FERTILIZE consumes one FERTILIZER unit and sets fertilized_until_day = day+2
  (3 days inclusive). Fertilizer is also buyable and sellable.
- Crops: WHEAT window days 2-4 cap 6; CARROT days 2-3 cap 4; MELON days 6-12 cap
  6; TOMATO and STRAWBERRY are "ongoing" (they accumulate separately).
- End of season: reward is money only, NO liquidation - unsold inventory and
  standing crops are worth zero.
- maxMarketOrdersPerTurn = 10 and extras are silently dropped.

PUBLISHED EVIDENCE USED:
1. crop_growth_trace.csv (published with the engine), accumulated yield summed
   over the tile's life under policies "watered_no_fertilizer" vs
   "watered_fertilized": CARROT 7->9 (+2), WHEAT 11->15 (+4), MELON 38->42 (+4),
   STRAWBERRY 16->24 (+8), TOMATO 10->14 (+4). We treat this delta as extra units
   per fertilizer application.
2. price_curve.csv (published): price at market inventory I0=10000 and above.
   STRAWBERRY 120 at 10000, 82 at 10020, 1 at 10100. MILK 160, 118, 1.
   WOOL 200, 177, 1. MELON 250 at 10000, 246 at 10020, 225 at 10050, 150 at
   10100, 1 at 10200. WHEAT 25, 22, 21, 20 across the same range. EGG 50, 45,
   43, 39. CARROT 35, 30, 27, 23, 9.
3. Observed fertilizer economy over 30 of our own ladder games: collected 382
   units/game, bought 66, SOLD 333, applied 97. Realized fertilizer sale price
   ~$41.8/unit. So we sell 3.43 units for every one we apply.
4. Our tape's planting counts per season: MELON 12, WHEAT ~170, STRAWBERRY 33,
   CARROT 5-31, TOMATO 0.
5. Spatial feasibility: a hand is standing on a non-ongoing crop that is inside
   its window and unfertilized 1079.8 times/game; on 406.7 of those the tape is
   watering that tile, on 60.5 it is idle (PASS), and 612.6 it is doing something
   else. There are 141 tile-days per game where the same tile is watered more
   than once (the second is a no-op).

THE SIZING MODEL: assume the market sits at I0, assume we are the marginal
supplier and the opponent's supply is unchanged. Walk the curve outward from
I0: the k-th extra unit is worth curve(I0+k). Keep going while that price
exceeds ~$42 (the fertilizer unit's opportunity cost = what we forgo by not
selling it). Result:
  crop        per appl   profitable units   applications   revenue   cost   net
  MELON           +4            144              36         25,949   1,512  24,437
  STRAWBERRY      +8             40               5          3,225     210   3,015
  TOMATO          +4             52              13          2,477     546   1,931
  WHEAT           +4              0               0              0       0       0
  CARROT          +2              0               0              0       0       0
Then we cap by plantings: MELON 12 plantings x 4 units = 48 units -> about
+$10.5k; STRAWBERRY capped at 5 applications -> +$3.0k; TOMATO is unreachable
because the tape never plants it. WHEAT and CARROT: zero.

COUNTER-EVIDENCE AND PRIOR FAILURES:
- A previous overlay that chunked sell orders to improve realized prices LOST
  24 of 24 paired games (twice), and the "sell-layer opportunity" it was based on
  turned out to be a selection artifact: the comparison corpus is published as
  "top episode replays" selected by score, its own manifest shows scores around
  3000, and our opponents IN OUR OWN GAMES realize the same prices we do.
- A roster change experiment (more cows/sheep/geese) lost money in every variant.
- The tape's early economy is cash-coupled: sells in a turn fund the buys that
  follow them in the same market queue, so throttling sells defunded the farm.

YOUR TASK: be adversarial and specific. Do NOT summarise or praise. Identify
(a) any logical or arithmetic error in the sizing model above, (b) which of its
assumptions is most likely to break in reality and in which direction that
would bias the estimate, (c) whether the counter-evidence above should make us
expect this lever to fail too, and why or why not, and (d) exactly what
measurement or control would falsify the claim cheaply. If the claim is sound,
say so plainly and say what its weakest link is. Answer in under 500 words."""


# %%
@kbench.task(name="kaggriculture-review")
def kaggriculture_review(llm):
    """Adversarially review our own fertilizer analysis with a hosted model."""
    # Notes from getting this to run (each one cost a failed push):
    #   * the task docstring becomes the task description and must be <= 255 chars;
    #     a longer one fails validation with "Task name and description must each be
    #     at most 255 characters".
    #   * the documented minimal shape is what the runtime accepts: no reasoning=
    #     kwarg and no return value. The response is collected from the run log, so
    #     printing it is enough.
    response = llm.prompt(FINDINGS)
    print("=" * 70)
    print("MODEL REVIEW")
    print("=" * 70)
    print(response)
    print("=" * 70)


# %%
kaggriculture_review.run(kbench.llm)  # type: ignore[attr-defined]  # .run() is added by the @kbench.task decorator at runtime
