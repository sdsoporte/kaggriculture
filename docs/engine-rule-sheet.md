# Kaggriculture engine rule sheet (verified against the source)

Reference for every plan, overlay and LLM brief in this project. Line numbers are
from `kaggle_environments/envs/kaggriculture/kaggriculture.py`, engine **1.32.7**.

## Why this file exists

Two LLM tasks in this project reasoned from a hand-written ~1 page brief and
produced confident claims the engine contradicts. The economist proposed removing
the tape's fertilizer buys as a **+$5,500..+$6,600** leak; measured, it is a
**-$210** wash, because buying is quoted at the post-buy inventory exactly so the
round trip nets zero. **The failure was missing mechanics, not missing data.**

The fix (`odd/tasks/llm-in-the-loop.md`, stage A) was to hand two models the
complete 1,086-line source and ask for the rule sheet. This file is that output
**after verification**: every row below was checked against the source, and only
verified rows are here.

## How to read the provenance

- **verified** — I read the source at the cited line.
- **NEW** — not present in any of our previous notes or briefs; the models found it.
- **known** — we had it already (kept here so one file is authoritative).
- The models' own line numbers are **not** trustworthy: `gemini-3.7-flash` cited
  `_process_market (L407)` when the real line is **544**, and `claude-opus-4-5`
  hedged every reference with `~`. Treat any LLM-supplied line number as a hint,
  never a citation.

## The four mechanics we were missing (all NEW, all verified)

| # | Rule | Source | Why it matters |
|---|---|---|---|
| 1 | **Atomic PLANT validation.** If the total number of `PLANT` actions for a crop in a single turn exceeds the seeds held, **every** `PLANT` of that crop becomes `PASS`. It is not a partial plant: the crop is voided for that turn. | `:919-931` (comment `:920-921`, `blocked = {crop for crop, n in plant_demand.items() if n > seeds.get(crop, 0)}`) | The tape rides close to its seed stock. Any turn where PLANT demand for a crop exceeds seeds silently plants **nothing** for that crop. It converts a seed shortfall into a total-loss turn rather than a partial one, so seed timing is worth more than it looks. |
| 2 | **A sale at the $1 floor does not move the market.** `if price > 1: market["inventory"][item] += 1`. | `:659-660` | Dumping at the floor is **free**: it cannot depress the price further, and it cannot crater the price for the opponent. It also means the floor is absorbing — once a product is glutted, extra volume is worth $1 and no less, so "sell everything" at the floor has no strategic cost. Both models independently listed this as an anti-strategy ("dump at floor price to crash opponent" is impossible). |
| 3 | **Movement onto a LOCKED tile is allowed.** Only tile *operations* no-op on locked tiles; walking is not an operation. | `:328-330` | Hand routes are not constrained to unlocked quadrants. Any routing model that treats LOCKED as impassable over-constrains itself (and any overlay that assumes hands cannot cross is wrong). |
| 4 | **A placed animal cannot be removed.** `DIG` returns early when the tile holds an animal; it clears plants, weeds and empty structures only. | `:484-490` (comment `:487-488`) | Roster decisions are **irreversible and permanent**. This is the structural reason the V10 roster swaps had to be pure payload swaps on a fixed tape rather than replacements. |

## Market rules (verified)

| Rule | Source |
|---|---|
| Prices are a deterministic function of market **inventory** only. | `market_price`, `:212` |
| `SELL` quotes at the **current** inventory. | `:597` |
| `BUY_PRODUCT` quotes at the **post-buy** inventory (`inventory - 1`) — the explicit anti-arbitrage rule: a buy/sell round trip against an unchanged market nets zero. | `:601`, comment `:599-600` |
| `BUY_PRODUCT` is accepted for **only** `WHEAT` and `FERTILIZER`. | `:598` |
| Market orders are truncated to `maxMarketOrdersPerTurn` (default **10**); the extras are silently dropped. | `:551` |
| Seeds are bought at a fixed price into `private["seeds"]`, never into the shed, and cannot be resold. | `CROPS`, `:417` |

## Physical rules (verified)

| Rule | Source |
|---|---|
| Crop table — **only** `first_yield_day` / `max_yield_day` / `interval` / `max_yield` / `ongoing`: | `CROPS`, `:12-16` |
| WHEAT seed 10, yield days **2-4**, interval 0, cap 6, one-shot | `:12` |
| CARROT seed 20, yield days **2-3**, interval 0, cap 4, one-shot | `:13` |
| TOMATO seed 50, first yield day **8**, interval **1**, cap 4, ongoing | `:14` |
| STRAWBERRY seed 100, first yield day **10**, interval **2**, cap 4, ongoing | `:15` |
| MELON seed 80, first yield day **10**, max yield day **12**, interval 0, cap 6, one-shot | `:16` |
| Watering grants the growth bonus only inside `[(max_yield_day + 1) // 2, max_yield_day]`; outside that window watering only prevents weeds. **For MELON that window is days 6-12 while its harvest window is days 10-12** — the two were conflated once in this project because both were called "the melon window". | `:440-441` |
| `HARVEST` before `first_yield_day` is a no-op (and warns for ongoing crops, which "should never happen"). | `:451-462` |
| A one-shot plant's `max_lifespan_step` is `(day + max_yield_day + 1) * turns_per_day`; ongoing crops are `-1` (no decay). | `:224` |
| Shed capacity defaults to **100** items, shared across products and animals; overflow is discarded. | `:553`, `:866`, `:908` |
| The season ends at `step >= episodeSteps - 2`; the reward is money only and there is **no liquidation** — unsold goods and standing crops are worth zero. | `:959-962` |

## What this changes for our own work

1. **A new, concrete, testable hypothesis about our own tape.** The atomic PLANT
   rule (`:919-931`) is the only mechanic here that can silently destroy work the
   tape believes it did. Our tape plants ~170 WHEAT and 33 STRAWBERRY with
   hand-timed `BUY_SEED` orders; any turn whose PLANT demand exceeds stock voids
   that crop's plantings for the turn. Worth measuring: count, per game, the turns
   where `plant_demand[crop] > seeds[crop]` and how many plantings the tape lost.
   This is a cheap read-only trace measurement, not an overlay.
2. **Rules 2 and 3 retire assumptions.** "Sell into the floor to hurt the
   opponent" is impossible, and hands may cross locked quadrants.
3. **Rule 4 explains a boundary we had already measured.** Roster changes could
   only ever be payload swaps because an animal, once placed, cannot be removed.

## Provenance

| | |
|---|---|
| Task | `ser8147/kaggriculture-engine-spec` (version 1) |
| Prompt | the complete engine source + four extraction questions (verb-level quote rules, silent failures, physical limits, impossible strategies, and the three rules most likely to be missing from a hand-written summary) |
| Models | `gemini-3.7-flash`, `claude-opus-4-5-20251101` — both completed, both converged on rules 1-4 |
| Generator | `kaggle/bench/build_engine_spec.py` (regenerate with `--check` in CI-style) |
| Verification | I read every cited line in the source myself; the models' line numbers were discarded |
