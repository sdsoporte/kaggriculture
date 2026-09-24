# Feature: V10 phase C — sell-lot chunking (sell-layer overlay)

Goal: raise the REALIZED sell price by splitting sell orders into smaller lots
spread over more turns, without touching production, hands, or structures. This
is the only lever this session found that has a measured mechanism AND a
plausible expected value above the acceptance bar.

## Evidence (measured this session, three passes)

| step | what it showed |
| --- | --- |
| B `scratch/income_breakdown.py` | our product MIX already matches the top teams (~25% of revenue from STRAWBERRY, 14-18% MILK/WOOL, ~12% MELON/WHEAT, ~10% FERTILIZER). MELON, WHEAT, WOOL and FERTILIZER prices are at PARITY. We lose only on the two biggest: STRAWBERRY $104.3 vs $131-197, MILK $75.2 vs $108-153. |
| B2 `scratch/sell_timing.py` | REFUTED timing: unit-weighted centre of mass of sales is day 21.5 vs 21.3 (strawberry), 19.7 vs 20.1 (milk). Also refuted "sell more often": our 185 sell-turns/game are mid-pack (theirs 144-565). |
| B3 `scratch/sale_context.py` | price is a tight function of market inventory (corr -0.72..-0.98), but inventory at sale time is the same for both (~9,98x) while they average $128.8 vs our $109.6. |
| **decisive** | LOT SIZE. They sell **3.8 units per sale point over 52.8 points/game**; we sell **10.6 units over 23.5 points/game** (milk: 3.5 over 49.3 vs 9.0 over 23.8). The engine re-quotes the price PER UNIT as a sale executes (`_commit_unit`), so a big lot walks its own price down. |
| **confirmation, inside our own games** | strawberry lots of <=10 units fetch **$125.3/unit**; lots >10 units fetch **$86.3/unit** (-31%). Milk: $90.5 vs $77.4. Same agent, same opponents, so no cross-team confound. |

Expected value: strawberry 249 u x ($125.3 - $104.3) = +$5.2k, milk 215 u x
($90.5 - $75.2) = +$3.3k => **~+$8.5k per game**, which is above the +$5,000 bar.

## Acceptance criteria

Same bar as the roster experiment (a change must justify evicting an active slot),
plus a mechanism check so a win cannot come from somewhere else:

1. `scripts/benchmark.py`, paired, both seats, >= 12 seeds (24 games) vs
   `submissions/main_v8.py`: mean margin **>= +$5,000** and win rate **>= 65%**.
2. **Mechanism check**: realized price per unit on STRAWBERRY and MILK must
   actually improve versus the same measurement on the baseline. A margin win
   without the price improving means the mechanism is not the one claimed.
3. `scripts/assert_overlay.py` gate: the overlay lands (order stream differs),
   and **zero animal escapes** (the throttling must not starve animals or break
   the buys that follow sells in the same queue).
4. No submission without a new explicit decision.

## Tasks

- [x] C1 Overlay `v10_sell_chunks.py` - DONE (chunk cap + per-product backlog +
      spare-slot emission), twice.
- [x] C2 Landing gate with `assert_overlay.py` - PASS on the day-12 build.
- [x] C3 Paired benchmark, 12 seeds - **0W-24L twice** (-$61,832 and -$5,049).
- [x] C4 Verdict written below: C is CLOSED, and it exposed a corpus selection bias.

## C4 verdict (2026-09-24): C is CLOSED - there is no sell-layer gap

**The overlay failed both ways, and the failure exposed a measurement artifact.**

| build | gate | paired benchmark vs main_v8 (12 seeds) |
| --- | --- | --- |
| chunk=4, from_step=0 | roster collapsed 17 -> 9 animals at day 1 | **0W-24L, -$61,832** |
| chunk=4, from_step=288 (day 12) | PASS, roster identical, order divergence turn 290, 0 escapes | **0W-24L, -$5,049** |

The first build proved the tape's early economy is cash-coupled: the sells in a
turn fund the buys that follow them in the same market queue, so throttling before
the animal purchases are done defunds the farm outright (the `closer_cleo` failure
mode). The second build removed that damage - the gate confirms the farm is
untouched - and the overlay STILL loses every game. Throttling can only hurt.

**Why the premise was wrong - the within-game control.** Phase B compared our
realized price against the top-10's and concluded we lose $25k/game on price. The
decisive control measures the OTHER seat in the SAME games:

| sample | own units | combined units | own price |
| --- | --- | --- | --- |
| our 30 games (us) | 248.7 | 479.8 | $103.5 |
| our 30 games (our OPPONENTS) | 239.9 | 479.8 | **$101.1** |
| corpus top-10 (18 games) | 202.7 | 405.4 | $140.8 |

Inside the same games we price essentially IDENTICALLY to our opponents
($103.5 vs $101.1). There is no seller-skill difference to close. Also note the
correlation between combined volume and price is POSITIVE (+0.26 ours, +0.69
theirs), so volume does not drive it either.

**The real cause: the corpus is SELECTED ON THE OUTCOME.** Kaggle publishes "top
episode replays", i.e. games selected by score, and a favourable shop draw
produces a higher score - so the sample over-represents good markets:

| sample | shops draining STRAWBERRY | draining MILK | P(no ICE_CREAM_SHOP) |
| --- | --- | --- | --- |
| our 30 ladder games | 3.60 | 2.67 | 37% |
| corpus top-10 (18) | **4.11** | **3.39** | **6%** |
| corpus at random (120) | 3.77 | 2.97 | 38% |

ICE_CREAM_SHOP (drains strawberry AND milk; measured at +$15,304) appears in 94%
of the top-10 games against 62-63% of ours and of a random corpus sample - and
65.6% is the theoretical Binomial(8, 1/8) rate. Our games and the random sample
match theory; the top-10 subset does not.

### Consequences
1. C is closed: the sell layer is not where we lose, so there is nothing to fix
   there. No submission.
2. Phase B's headline (+$25k opportunity) was a selection artifact, refuted by
   our own control measurement.
3. **Any comparative conclusion drawn from the Kaggle top-episodes corpus is
   confounded by selection on the outcome** - including earlier ones in this
   project about top-10 scores, roster buckets and peak-animal monotonicity. The
   corpus is usable only for WITHIN-GAME comparisons (both seats of the same
   match), which is exactly what caught this.

## Constraints / non-goals

- **`maxMarketOrdersPerTurn = 10`, extras silently dropped** (engine `:551`,
  `:563`) and our tape already reaches 10 on busy turns. Sells must be MOVED onto
  turns with spare slots, never added to full ones. The overlay must never push a
  turn past the cap, or it would silently drop the tape's own wheat buys.
- **No end-of-season liquidation exists** (`:960-963`): unsold inventory is worth
  zero. The backlog MUST clear by the last effective turn (DONE at step >= 718),
  so the cap self-relaxes as turns run out.
- **Shed capacity 100, overflow discarded** (`:851-863`): holding stock back can
  overflow the shed, so the backlog is bounded and drains faster when large.
- **Intra-turn ordering is preserved**: the tape's own orders keep their
  positions; backlog sells are appended. The `closer_cleo` failure mode was
  hoisting sells out of their slots so later buys failed on cash, which starves
  animals.
- Only the CHUNKING changes, never the total volume: selling less is not a fix,
  because anything unsold is worth zero.
