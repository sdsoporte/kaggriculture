# Game analysis: #1 DSM vs Majkel1337 (#2) — and our v0 game

Episodes reviewed live in the Kaggle viewer on 2026-09-21:
- **Top game:** DSM vs Majkel1337, seed 1763904130 (episodeId 111779053)
- **Our game:** Sergio D vs Elahi, seed 820460778 (episodeId 111757775)

## Final states

| | DSM (#1) | Majkel (#2) | Sergio D (v0) | Elahi (pass) |
| --- | --- | --- | --- | --- |
| Banked | **98,233** | 82,404 | **4,786** | 3,000 |
| Rating delta | 3,180 (+3) | 3,080 (−3) | 156 (+35) | 150 (−4) |
| Quads unlocked | 3 (never 4th) | 3 (never 4th) | 1 | 0 |
| Endgame waste | 2 tomato + 4 wheat | minimal | weeds + 7 shed items | — |
| Day-20 bank | 47,026 | 41,361 | — | — |

**Scale gap: 98,233 vs 4,786 ≈ 20.5×.** This is the distance between the
frontier program and a tier-1 reference agent — and it decomposes exactly
along the levers in the research's $13,400 gap ledger.

## How the gap decomposes (visible on the board)

1. **Land leverage (~3×).** Both top agents run three quadrants — and both
   leave the fourth locked, the literal frontier-program signature from the
   research. v0 farms one.
2. **Labor leverage.** Day-20 frame shows action badges 2–4 on many tiles:
   ~12 hired hands working simultaneously (frontier hires 12 from day 10
   through the Fibonacci wage market). v0 hires none.
3. **Animals.** Pens with geese/cattle in the middle strips of both top
   farms — recurring product lines plus care banking (the 4.2× multiplier
   from the community analysis).
4. **Liquidation discipline.** Both top agents roughly **doubled their bank
   in the last 10 days** (47k→98k, 41k→82k) and ended with near-zero unsold
   inventory (2 tomatoes + 4 wheat). v0 ended with weeds and 7 shed items —
   worth zero.
5. **Mirrored layouts.** The two top farms are near-carbon copies — the
   tape-clone flood made visible. Same unlock timing, same labor shape,
   same crop skeleton (research: one plant-mix fingerprint covers 87.8% of
   winners).

## The market insight that changes our pricing model

| Price (final) | Our small game | Top game |
| --- | --- | --- |
| wheat | 35 | 20 |
| carrot | **290** | **22** |
| tomato | 64 | 42 |
| strawberry | **286** | **163** |

In games between small sellers, town hunger explodes endgame prices
(carrot ×8.3). In top games, **both** agents front-run the same hunger and
the shared curve crashes instead (carrot 22, below base 35). Day-20 prices
in the top game were still sane (carrot 37, strawberry 60) — the crash is
the liquidation phase itself.

Implication: sale-timing heuristics cannot be static. "Sell into the
endgame peak" is only true when the opponent does not do the same. This is
the coupled-margin law from the research — and the strongest argument for
the detector-gated parameter switch later: against tapes, batch earlier;
against weak opponents, ride the peak.

## What this means for v2 (C4–C6)

The priority order from the research's gap ledger holds, now with visual
confirmation:

1. Wheat cycle — grow feed wheat, don't buy it (51% of the gap).
2. Strawberry volume — 40 tiles by day 15 (16%).
3. Labor waste — the scheduler C3 is already attacking this (14%).
4. Melon cadence — batch sales (13%).

v2's carrot/tomato rotation is directionally right, but the *scale* comes
from quadrants 2–3 (days 7/10), 12 hands, and animals — the structural
levers. Scheduler-first remains correct: hands pay for everything else.

## Verification artifacts

Screenshots: `/tmp/pi-playwright/s/top1-start.png` (day 1),
`top1-mid.png` (day 20, turn 480), `top1-end.png` (turn 720),
`episode-end.png` (our game's final frame).
