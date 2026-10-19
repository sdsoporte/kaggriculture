# %%
"""An economist, not a reviewer.

The adversarial reviewer task attacks our numbers. This one is a different job: a
quantitative agricultural economist asked to re-derive the whole production plan
under the market's absorption constraints, because our own search has been strictly
incremental (one overlay lever at a time) and has never optimised the plan jointly.

Runs on Kaggle's hosted models through the free AI quota:

    kaggle b t push kaggriculture-economist -f kaggle/bench/kaggriculture-economist.py --wait
    kaggle b t run kaggriculture-economist -m claude-opus-4-5-20251101 --wait
    kaggle b t log kaggriculture-economist -m claude-opus-4-5-20251101
"""

import kaggle_benchmarks as kbench  # type: ignore[import-not-found]  # provided by Kaggle's task runtime, deliberately not installed in the project venv

# %%
BRIEF = """You are a quantitative agricultural economist. A two-player farming
simulation ("Kaggriculture") runs 30 days / 720 turns. You are asked to find the
revenue-maximising production plan, not to praise or summarise anything.

THE MARKET (fully deterministic, verified in the engine source)
- Each product's price is a function of the market INVENTORY only; there is no
  randomness in pricing. Price at inventory I0 = 10,000 and above:
  | product | 9800 | 9950 | 10000 | 10050 | 10100 | 12000 | base price |
  | STRAWBERRY | 239 | 179 | 120 | 24 | 1 | 1 | 120 |
  | MILK | 283 | 221 | 160 | 55 | 1 | 1 | 160 |
  | WOOL | 245 | 234 | 200 | 55 | 1 | 1 | 200 |
  | MELON | 296 | 284 | 250 | 225 | 150 | 1 | 250 |
  | WHEAT | 39 | 32 | 25 | 22 | 21 | 19 | 25 |
  | EGG | 62 | 53 | 50 | 43 | 42 | 39 | 50 |
  | CARROT | 41 | 40 | 35 | 27 | 23 | 9 | 35 |
  | FERTILIZER | 140 | 110 | 100 | 90 | 80 | 1 | 100 |
  So the shop-drained products collapse to $1 within ~100 units of oversupply,
  while WHEAT and EGG are nearly flat.
- Sales are quoted at the PRE-sale inventory and executed unit by unit, so one
  large sell order walks its own price down.
- The town consumes: the town centre takes 1 unit of every non-fertilizer product
  every 24 turns (flat all season); plus shops, which unlock on days 3,6,...,24 and
  each demand one unit per 4 turns (two shops demand 2 units per 4 turns). Eight
  shop instances total, drawn uniformly with replacement from 8 shop types, so a
  given shop type appears with probability 65.6%. Expected daily drain: WHEAT ~22,
  STRAWBERRY ~16, MILK ~11, EGG ~8, CARROT ~10, TOMATO ~6, WOOL ~8, MELON ~1
  (the town centre only, because no shop demands melon).
- The town NEVER consumes fertilizer, and fertilizer is also buyable by players,
  so its market is glutted and its marginal sale value is only about $5/unit.
- END OF SEASON: reward is money only. There is NO liquidation: unsold inventory
  and standing crops are worth zero. Market orders are capped at 10 per turn and
  extras are silently dropped.

PRODUCTION COSTS AND YIELDS
- Seeds: WHEAT 10, CARROT 20, TOMATO 50, STRAWBERRY 100, MELON 80.
- Animals: GOOSE 300 (coop, 1 egg/day from day 4, holds 4), COW 400 (pasture,
  milk every 2 days from day 8, holds 6), SHEEP 500 (pasture, wool every 3 days
  from day 6, holds 6).
- Structures sit on tiles; extra land costs 1000 / 2000 / 4000 (NE / SW / SE).
- Labour: hands are re-hired DAILY at Fibonacci cost; a 12-hand roster costs 376
  per day, 13 hands 609, 14 hands 986.
- Crops: WHEAT yields over days 2-4 after planting (cap 6 units), CARROT days 2-3
  (cap 4), MELON days 6-12 (cap 6), TOMATO and STRAWBERRY are ongoing (they refill
  and are re-harvested, cap 4 held at a time).
- Watering raises yield by 1 per watered day (2 if the tile is fertilized; a
  fertilizer unit lasts 3 days and costs ~$100 bought or ~$5 of forgone sales).

OUR CURRENT PLAN AND MEASURED RESULTS (a fixed 720-step action tape, per game)
- 3 quadrants, 12 hands, 17 animals at peak (9 COW / 5 SHEEP / 3 GOOSE), 14
  pastures and 3-4 coops, 12 MELON plantings, 33 STRAWBERRY, ~170 WHEAT, 0 TOMATO.
- Mean bank ~$91k against a live ladder mean of ~$96k; ~$105k of revenue per game.
- Realised revenue by product: STRAWBERRY ~$26k (249 units at ~$104), MILK ~$16k
  (215 at ~$75), WOOL ~$18k (138 at $134), MELON ~$12.5k (66 at $189), WHEAT
  ~$12k (329 at $36), FERTILIZER ~$10.6k (254 at $42). Total spend ~$14.3k.
- Fertilizer: we COLLECT ~382 units per game, buy 66, apply 97 and sell 333.
  An ablation showed that cancelling all 97 applications costs -$3,078 per game,
  with the loss concentrated on STRAWBERRY (-128 units) and CARROT (-12), and
  EXACTLY ZERO on MELON. Melon tiles reach the 6-unit cap even unfertilized.

WHAT WE ALREADY TRIED AND MEASURED AS FAILING (24 paired games each unless noted)
- Roster swaps at the same slots: cow-heavy -$9,072, sheep-heavy +$1,933 at a 50%
  win rate, goose-heavy -$1,998.
- Sell-lot chunking to improve realised prices: 0 wins in 24 games, twice.
- Buy-timing arbitrage: ceiling of +$81 per game even with perfect foresight.
- Fertilizing melon: exactly $0 (the cap absorbs it; the tape never fertilizes
  melon anyway).
- Adding more animals or a 4th quadrant is not implementable as an overlay: the
  tape ends with 0 free structure slots and only 7.4% idle worker turns, and its
  early economy is cash-coupled (the sells in a turn fund the buys that follow them
  in the same market queue).

YOUR TASK. Answer as an economist, quantitatively, in under 600 words:
1. Given the absorption constraints above, derive the revenue-maximising MIX and
   SCALE. Show your marginal reasoning: for each product, at what cumulative volume
   does its marginal revenue fall below the marginal cost of producing it? Use the
   price table to solve for the volume where the price crosses each product's
   marginal production cost, and state that volume per product.
2. On that basis, say explicitly what our current plan is over- or under-producing
   relative to that optimum, and by roughly how many units.
3. The tape cannot be re-planned cheaply, so name the single change to the existing
   plan that your analysis says has the largest expected value, and give its
   expected dollar magnitude. If your answer is that no affordable change helps,
   say so plainly and say what that implies about where the remaining headroom is.
4. State the assumption in your analysis most likely to be wrong and what it would
   take to check it in one experiment."""


# %%
@kbench.task(name="kaggriculture-economist")
def kaggriculture_economist(llm):
    """Ask a hosted model to re-derive the production plan as an economist."""
    response = llm.prompt(BRIEF)
    print("=" * 72)
    print("ECONOMIST ANALYSIS")
    print("=" * 72)
    print(response)
    print("=" * 72)


# %%
kaggriculture_economist.run(kbench.llm)  # type: ignore[attr-defined]
