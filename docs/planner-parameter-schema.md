# Planner parameter schema (the contract)

The interface between three things that must agree: the **LLM author** (a Kaggle
Benchmarks task that proposes a plan), the **parameterised agent** (ours, which
turns a plan into actions), and the **offline search** (C-Largo, which improves a
plan against the engine).

Without one frozen schema those three drift and nothing is comparable. This file is
the schema. It is deliberately **small**: every field below exists because a
measurement justifies it, and every field we left out is a field the tape already
handles in a way we have not beaten.

## Why a parameter vector and not 720 actions

The tape is ~46 KB of hardcoded actions that sits at a measured local optimum: nine
levers have been closed, and every one died on structural coupling with the fixed
schedule (the step-150 ~$1,000 cash cliff, the per-crop choreography, the saturated
slots). A planner that emits 720 actions from scratch has no way to reuse that
hard-won schedule and no way to be searched cheaply.

So a plan is a **small vector of season-level decisions**. A parameterised agent
consumes it and keeps the proven per-turn machinery (route, priorities, the
cash-coupled buy ordering) intact. That buys three things:

1. The LLM can author it in one prompt (it is ~20 numbers, not 720 actions).
2. It can be **searched** against the engine: a local search over ~20 bounded
   integers is cheap and safe; a search over 720 actions is neither.
3. Any winning vector can be **baked** back into a fixed tape at the end, which is
   what the submission requires.

## The schema

A plan is one JSON object. All fields are required; unknown fields are an error.

```json
{
  "schema": "kaggriculture-plan/3",
  "land": {
    "quadrants": 3,
    "buy_days": [1, 6]
  },
  "hands": {
    "start": 4,
    "ramp_step": 1,
    "daily_target": 12
  },
  "crops": [
    {"crop": "WHEAT",      "tiles": 30, "plant_days": [0, 8, 16, 24], "fertilize_tiles": 0},
    {"crop": "CARROT",     "tiles": 10, "plant_days": [1, 11, 25],    "fertilize_tiles": 3},
    {"crop": "TOMATO",     "tiles": 0,  "plant_days": [4],           "fertilize_tiles": 0},
    {"crop": "STRAWBERRY", "tiles": 20, "plant_days": [1, 3, 5],     "fertilize_tiles": 15},
    {"crop": "MELON",      "tiles": 6,  "plant_days": [0, 12],       "fertilize_tiles": 0}
  ],
  "animals": [
    {"animal": "COW",   "count": 4, "buy_days": [3, 4, 5, 6]},
    {"animal": "SHEEP", "count": 3, "buy_days": [7, 8, 9]},
    {"animal": "GOOSE", "count": 2, "buy_days": [10, 11]}
  ],
  "market": {
    "sell_chunk_cap": 0,
    "sell_from_day": 12,
    "seed_reserve": 24
  }
}
```

Note the arithmetic, because it is now an enforced invariant: 66 crop tiles plus 9
animal structures is exactly the 75 cells three quadrants unlock. **The example
itself was invalid until revision 3** - it asked for 125 crops plus 17 structures,
142 cells, and nothing caught it because each field was individually in range.

### Field reference

| Field | Meaning | Bounds | Why it is here |
|---|---|---|---|
| `land.quadrants` | Quadrants to unlock (NW is free) | 1-4 | Costs 1000/2000/4000 and is the binding physical limit the profiling found. `1` means NW only. |
| `land.buy_days` | Day on which each unlock is attempted, in order | 0-29, ascending | The tomato overlay died at the step-150 (~day 6) `BUY_LAND` cliff: missing it by $114 permanently lost a quadrant. Buy timing is load-bearing and must be a decision. |
| `hands.start` | Farm hands hired on day 0 | 0-14 | Labour is the multiplier on every other decision. Fibonacci cost: 12 hands = $376/day, 13 = $609, 14 = $986. The tape's early cash is on a knife-edge ($24 left on day 0), so the starting count is a decision. |
| `hands.ramp_step` | Hands added each day until the target is reached | 0-14 | **Added in revision 2.** The tape uses seven distinct hand counts `[4,5,8,9,10,11,12]`; revision 1 had `daily_target` + a single `ramp_day`, a step function that cannot describe a curve. |
| `hands.daily_target` | Maximum hands the ramp grows to | 0-14 | The cap on the ramp. |
| `crops[].crop` | One of WHEAT, CARROT, TOMATO, STRAWBERRY, MELON | - | - |
| `crops[].tiles` | Target number of tiles held under this crop | 0-100 | The absorption headroom we measured: TOMATO +266 units unproduced, STRAWBERRY +253, WHEAT +291, against MELON already overproduced 2.2x. |
| `crops[].plant_days` | Explicit days on which to (re)plant this crop, ascending. **Empty is legal only when `tiles == 0`** - there is nothing to plant, so there is no planting day, and that is how a plan says "never grow this". | 0-29 each, 0-30 entries | **Replaced `plant_first_day` + `replant_every` in revision 2.** One period per crop cannot express a non-uniform calendar, and the engine's windows are per-crop (`CROPS`, `:12-16`), so the calendar is the decision. The search moves this field as a SET (add, remove or shift a day), which is a coordinate move, not a continuous one. |
| `crops[].fertilize_tiles` | How many of this crop's tiles to fertilize | 0 to `tiles` | **Replaced the boolean in revision 2.** The tape fertilizes **6 of 30** carrot tiles (20%), which a per-crop boolean cannot express. Verified value: the tape's existing fertilizing is worth +$3,078/game, all of it on STRAWBERRY, ~zero on MELON (cap absorption). |
| `animals[].animal` | One of COW, SHEEP, GOOSE | - | - |
| `animals[].count` | Animals of this species placed | 0-30 | Structures sit on tiles and a placed animal **cannot be removed** (`:484-490`), so this is irreversible within a season. |
| `animals[].buy_days` | Days on which to buy this species (one animal per listed day) | 0-29 | Purchase timing competes with seed cash for the same early money. |
| `market.sell_chunk_cap` | Maximum units per sell order; **`0` means no throttle** | 0-100 | **The `0` sentinel was added in revision 2.** Measured: our lots of 10.6 units over 23.5 sale points fetch less than the top teams' 3.8 units over 52.8 points, so capping is the mechanism - but the tape itself has NO cap (median lot 5, max 50), and revision 1 could only express that as `cap=100`, which loses the intent. The earlier sell-chunk overlay failed only because it defunded the buy queue before day 12. |
| `market.sell_from_day` | Day from which throttling applies | 0-29 | The last animal purchase is turn 266 (day 11), so throttling before day 12 defunds the farm. This field exists so the search can move it safely. |
| `market.seed_reserve` | Seeds of each crop to keep in stock before planting | 0-100 | Seeds are bought into `private["seeds"]`, never the shed. The engine's atomic PLANT rule (`:919-931`) voids EVERY PLANT of a crop whose turn demand exceeds stock - measured as never firing on our tape, but a re-planned crop schedule could change that, so the reserve must be expressible. |

## Revisions 2 and 3 - and why the vector is a set of targets

**Revision 3** added the global tile budget (invariant 9) and states explicitly what
revision 2 left ambiguous: `animals[].count` must EQUAL `len(buy_days)`, one day per
animal. Both gaps were found by the LLM author task rather than by us, which is the
right outcome for a contract: it was exercised by something that did not know our
intentions.

| gap | found by | what made it visible |
|---|---|---|
| no global tile budget | `claude-opus-4-5` | a plan of 125 crop tiles + 17 structures on 75 unlocked cells that passed every per-field bound |
| `count` vs `len(buy_days)` unspecified | both models | COW 8 animals over 3 listed days - a consumer cannot read that unambiguously |

The example JSON above was itself impossible until this revision, which is a fair
comment on how these contracts get written.

**Revision 2** was the first revision written from evidence rather than intent.
Revision 1 was frozen before anyone tried to describe our OWN tape with it, and that
check failed:

The same check produced a second, more important finding: **the vector is not
deterministic.** Across seeds, `land` is identical but `hands`, `market`, `crops`
and `animals` all vary, because the tape is reactive to the engine's state rather
than a function of season-level decisions.

That is not a defect in the schema - it defines what the fields MEAN. Every field
above is a **season-level target or calendar**, and the consumer is responsible for
realising it against the live state each turn: assigning `crops[].tiles` to
specific tiles, attempting `plant_days` when capacity and cash allow, growing hands
by `ramp_step` only while money permits. The schema says what the season is meant
to look like, never which unit does what on which turn.

It also means the W2c calibration gate had to be rewritten: "the tape's own vector
must reproduce the tape's bank" is unachievable by construction, because this schema
deliberately does not describe the tape. W2c is now an internal-consistency check
(the expected plantings, animals and quadrants actually happen), and the comparison
against the tape is the end acceptance test.

## Invariants the consumer must enforce

These are not parameters; the parameterised agent must hold them regardless of the
vector, because each is a measured engine rule or a measured failure:

1. **Cash before step 150.** No plan may spend into the ~$1,000 `BUY_LAND` cliff at
   step 150 (~day 6). A plan that does not leave the land purchase funded loses a
   quadrant permanently - that is the exact mechanism that destroyed the tomato
   overlay (-$92k).
2. **One `PLANT` per tile per turn, and never more than the seeds held for that
   crop.** The atomic rule voids all of them otherwise.
3. **Water inside the crop's window only**: `[(max_yield_day + 1) // 2,
   max_yield_day]`. For MELON that is days 6-12 while its harvest window is days
   10-12; the two are different and were conflated once.
4. **Never exceed 10 market orders per turn** (`:551`); extras are dropped
   silently.
5. **Harvest a one-shot crop inside its window** and let nothing rot: there is no
   liquidation, and an unharvested crop is worth zero.
6. **Feed every animal every day.** Two consecutive unfed days and the animal
   escapes.
7. **Sell at the $1 floor without fear**: a floor sale does not move market
   inventory (`:659-660`), so it cannot depress the price further.
8. **Keep the shed under 100 units**; the overflow is discarded.
9. **The plan must fit the board.** `sum(crops[].tiles) + sum(animals[].count)` must
   not exceed the cells the requested quadrants unlock (25 per quadrant on the
   default 10x10 board). **Added in revision 3**, and it is the only invariant here
   that is not per-field: an LLM plan passed every individual bound while asking for
   142 cells on 75 unlocked ones, and nothing caught it. A per-field range cannot
   see a farm that does not fit.

## What is deliberately NOT a parameter

The per-turn route, the action priority order, and the same-turn sell-then-buy
coupling stay hardcoded. That ordering is what a previous review identified as the
reason `closer_cleo`-style hoisting fails, and three overlays have already died
touching it. Changing it is a separate feature, not a search dimension.
