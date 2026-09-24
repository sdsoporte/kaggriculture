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
FINDINGS = """You are reviewing the RESULTS of an experiment in a Kaggle farming simulation
("Kaggriculture": 30 days, 720 turns, two players, market price is a deterministic
function of market inventory). Our agent is a FIXED 720-step action tape; mean
ladder bank ~$91k, rating ~1238 (the top of the ladder is ~3000+).

WHAT WE BELIEVED (round 1, this same review): a crop-growth trace published with
the engine shows accumulated yield under a "watered" vs a "watered+fertilized"
policy, and the deltas are MELON +4, WHEAT +4, STRAWBERRY +8, CARROT +2 units.
Walked against the published price curve, that projected a fertilizer programme
worth +$24.4k (melon, 36 applications), +$3.0k (strawberry, 5) and $0 (wheat). You
reviewed that plan and raised (a) the trace measures LIFETIME yield under a policy,
not yield per application, so melon would need ~3 applications per planting, (b)
diverting fertilizer from sales risks defunding the same-turn buy queue, the exact
failure that killed an earlier sell-chunk overlay, and (c) late-harvest yield can
go unsold because the market order cap is 10 per turn.

WHAT WE MEASURED INSTEAD. Patched the tape to suppress EVERY FERTILIZE it already
makes (97 per game) and ran 8 paired games (4 seeds x both seats, same opponent).
Results, per game:
| metric            | control | no fertilizer | delta |
| mean bank         |  79,274 |        76,196 | -3,078 |
| crop harvest      |     375 |           236 |   -140 |
| MELON             |      66 |            66 |    +0  |
| STRAWBERRY        |     258 |           130 |   -128 |
| CARROT            |      51 |            40 |    -12 |
| fertilizer sold   |     288 |           372 |    +84 |
| fertilizer applied|      97 |             0 |    -97 |

WHAT WE NOW CONCLUDE:
1. The fertilizer the tape already applies IS productive: cancelling it costs
   -$3,078 per game, about +$32 net per application. Not a dead lever.
2. ALL of the value is on STRAWBERRY (+128 units, ~1.3 units per application).
   MELON GAINS EXACTLY ZERO, which contradicts both round 1's plan and the
   published growth trace's +4 melon delta.
3. The accounting reconciles only at MARGINAL prices: forgoing ~84 fertilizer
   sales costs ~$3.5k, and the lost 128 strawberry units are worth only ~$6.6k
   (~$52/unit, not the ~$104 realised average).
4. Therefore headroom is tiny: the curve allows only ~40 additional profitable
   strawberry units, so at ~$32 per application the whole expansion is worth on
   the order of +$160.
5. We are closing the fertilizer lever and, with it, all four overlay levers we
   have tried: roster mix (lost money in every variant), more animals (the tape is
   saturated: zero free structure slots, 7.4% idle worker turns), sell-lot
   chunking (lost all 24 paired games, twice), and now fertilizer.

YOUR TASK - be adversarial and specific, do NOT summarise or praise:
(A) Do we over-conclude? Is +$3,078 from 8 games distinguishable from noise, and
    what would a proper significance check look like here?
(B) The MELON ZERO is the anomaly worth explaining. Propose the most likely
    mechanism (fertilizer never landing on melon tiles? the window being missed?
    the melon tile being harvested before the bonus pays?) and the single cheapest
    diagnostic that would discriminate between those explanations.
(C) If the tape's fertilizing is worth +$32/application on strawberry while
    melon shows zero, is there a plausible way to REDIRECT the same fertilizer to
    a better target, and what is the smallest experiment that would test it?
(D) Given every overlay lever has now failed, is there any lever left that we have
    not considered? Be concrete about what it is and what would falsify it.
(E) Name the single weakest link in our reasoning above.
Answer in under 500 words."""


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
