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
  "schema": "kaggriculture-plan/1",
  "land": {
    "quadrants": 3,
    "buy_days": [1, 6, 12]
  },
  "hands": {
    "daily_target": 10,
    "ramp_day": 2
  },
  "crops": [
    {"crop": "WHEAT",      "tiles": 60, "plant_first_day": 0,  "replant_every": 8,  "fertilize": false},
    {"crop": "STRAWBERRY", "tiles": 33, "plant_first_day": 1,  "replant_every": 0,  "fertilize": true},
    {"crop": "CARROT",     "tiles": 20, "plant_first_day": 1,  "replant_every": 10, "fertilize": false},
    {"crop": "TOMATO",     "tiles": 0,  "plant_first_day": 4,  "replant_every": 0,  "fertilize": false},
    {"crop": "MELON",      "tiles": 12, "plant_first_day": 0,  "replant_every": 12, "fertilize": false}
  ],
  "animals": [
    {"animal": "COW",   "count": 9, "buy_days": [3, 4, 5]},
    {"animal": "SHEEP", "count": 5, "buy_days": [6, 7]},
    {"animal": "GOOSE", "count": 3, "buy_days": [8]}
  ],
  "market": {
    "sell_chunk_cap": 10,
    "sell_from_day": 12,
    "seed_reserve": 24
  }
}
```

### Field reference

| Field | Meaning | Bounds | Why it is here |
|---|---|---|---|
| `land.quadrants` | Quadrants to unlock (NW is free) | 1-4 | Costs 1000/2000/4000 and is the binding physical limit the profiling found. `1` means NW only. |
| `land.buy_days` | Day on which each unlock is attempted, in order | 0-29, ascending | The tomato overlay died at the step-150 (~day 6) `BUY_LAND` cliff: missing it by $114 permanently lost a quadrant. Buy timing is load-bearing and must be a decision. |
| `hands.daily_target` | Farm hands hired each morning | 0-14 | Labour is the multiplier on every other decision. Fibonacci cost: 12 hands = $376/day, 13 = $609, 14 = $986. |
| `hands.ramp_day` | Day the target becomes active | 0-29 | The tape's early cash is on a knife-edge ($24 left on day 0), so the ramp is a decision, not a constant. |
| `crops[].crop` | One of WHEAT, CARROT, TOMATO, STRAWBERRY, MELON | - | - |
| `crops[].tiles` | Target number of tiles held under this crop | 0-100 | The absorption headroom we measured: TOMATO +266 units unproduced, STRAWBERRY +253, WHEAT +291, against MELON already overproduced 2.2x. |
| `crops[].plant_first_day` | Day of the season's first planting | 0-29 | Crop windows are strict (`CROPS`, `:12-16`) and the tape's choreography is window-specific. |
| `crops[].replant_every` | Replant period in days; `0` = plant once and never replant (ongoing crops) | 0-29 | This is the field that makes TOMATO or STRAWBERRY viable where a payload swap was not: the swap failed because the tape waters on wheat's window, not the new crop's. |
| `crops[].fertilize` | Whether this crop's tiles are fertilized | bool | Verified: the tape's existing fertilizing is worth +$3,078/game, all of it on STRAWBERRY, ~zero on MELON (cap absorption), and MELON tiles cap at 6 unfertilized. |
| `animals[].animal` | One of COW, SHEEP, GOOSE | - | - |
| `animals[].count` | Animals of this species placed | 0-30 | Structures sit on tiles and a placed animal **cannot be removed** (`:484-490`), so this is irreversible within a season. |
| `animals[].buy_days` | Days on which to buy this species (one animal per listed day) | 0-29 | Purchase timing competes with seed cash for the same early money. |
| `market.sell_chunk_cap` | Maximum units per sell order | 1-100 | Measured: our lots of 10.6 units over 23.5 sale points fetch less than the top teams' 3.8 units over 52.8 points. Capping lots is the mechanism; the earlier sell-chunk overlay failed only because it defunded the buy queue before day 12. |
| `market.sell_from_day` | Day from which throttling applies | 0-29 | The last animal purchase is turn 266 (day 11), so throttling before day 12 defunds the farm. This field exists so the search can move it safely. |
| `market.seed_reserve` | Seeds of each crop to keep in stock before planting | 0-100 | Seeds are bought into `private["seeds"]`, never the shed. The engine's atomic PLANT rule (`:919-931`) voids EVERY PLANT of a crop whose turn demand exceeds stock - measured as never firing on our tape, but a re-planned crop schedule could change that, so the reserve must be expressible. |

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

## What is deliberately NOT a parameter

The per-turn route, the action priority order, and the same-turn sell-then-buy
coupling stay hardcoded. That ordering is what a previous review identified as the
reason `closer_cleo`-style hoisting fails, and three overlays have already died
touching it. Changing it is a separate feature, not a search dimension.
