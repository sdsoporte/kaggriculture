# V10 wave 8: drop the tape's fertilizer PURCHASES

## Goal

Test the one candidate the seven closed V10 levers never isolated: the V8 tape
**buys** fertilizer while collecting a large surplus. Measured per game, the tape
collects ~382 fertilizer units, applies 97, sells 333 — and still buys 66.

Open question: **is the purchase profitable?** The fertilizer market is glutted
(measured marginal sale value ~$5/unit), the town never consumes fertilizer, and
82% of the bought volume is not even needed to cover the 97 applications. If the
buys are a net cash leak, removing them is free money.

Note the lever boundary carefully, because it was conflated once already:

- The **fertilizer ablation** (commit `f879a4d`, verified) cancelled the 97
  **applications** and lost $3,078/game. Verdict: the tape's fertilizing IS
  productive. Still true.
- This task removes the 66 **purchases**. Different action, different question,
  never measured.

## Origin

`kaggriculture-economist` (Kaggle Benchmarks, gemini-3.7-flash, version 2) named
it "a pure cash leak caused by tape buy/sell sequencing" and sized it at
+$5,500 to +$6,600. That estimate is **the model's arithmetic, not evidence**;
the point of this task is to measure it. The same run (claude-opus-4-5) did NOT
find this leak and instead recommended a sheep tilt that our own 100-seed arena
sweep has already ruled out (margin -$2,533, CI [-5,384, +319] = noise).

## Engine facts the transform depends on

- Market orders are `[OP, item, qty]`; `_process_market` truncates to
  `maxMarketOrdersPerTurn = 10`, extras silently dropped
  (`kaggriculture.py:551-563`).
- `BUY_PRODUCT` is accepted **only** for `WHEAT` and `FERTILIZER`
  (`kaggriculture.py:598`). There is no transfer/gift operation.
- Buying quotes at post-buy inventory (`market_price(item, inventory - 1)`,
  `:601`), i.e. a buy/sell round-trip on an unchanged market nets zero.

## Tasks

- [x] T1 — Overlay `src/kaggriculture/agents/v10_no_fert_buy.py` + unit tests
      `tests/test_v10_no_fert_buy.py`, pytest green, lint clean.
- [x] T2 — Local paired smoke with diagnostics: bank delta, dropped-buy count,
      executed FERTILIZE actions, harvest by crop. Cheap check before any Kaggle
      round trip.
- [ ] T3 — Arena mirror, **cancelled**: see the structural argument in Evidence.
- [x] T4 — Record evidence, commit, close.

## Acceptance criteria

- The measured margin is reported **paired and with the absolute bank beside it**
  (a margin of 0 is ambiguous between "no effect" and "nothing ran").
- If the delta is positive with a CI excluding zero, the fertilizer-buy removal
  becomes the first V10 lever to PASS the bar (win >= 65% AND margin >= $5,000
  AND CI excludes zero); otherwise it closes as the eighth measured verdict.
- The diagnostics must show **whether the 97 applications survived**. If dropping
  the buys starves the applications, the bank delta is not a clean measurement of
  the leak and the finding must be stated that way.

## Non-goals

- Not re-opening the fertilizer application ablation (already verified).
- No new submission: the arena mirror decides first.

## Constraints / risks

- **Cash perturbation.** The tape's early economy is cash-coupled and the
  step-150 `BUY_LAND` cliff (~$1,000) has already destroyed one overlay (tomato,
  -$92k). This change ADDS cash, which is the safe direction, but more cash can
  also make a tape `HIRE` succeed that previously failed — the roster-swap
  overlays had to start at step 150 for exactly that reason. `from_step` is
  therefore a parameter, and the smoke run must report whether the animal count
  and quadrant count moved.
- **Reuse the tested transform pattern.** A fresh module means a fresh test for
  every non-trivial transform: the vowel-less verb bug (`["TOMATO"]`) shipped
  twice because the second overlay was written from scratch.

## Evidence log

### T1 — overlay and tests

`pytest tests/ -q` = **27 passed**. The transform drops `[BUY_PRODUCT,
FERTILIZER, n]` and nothing else; the two boundary tests that matter are that a
`BUY_PRODUCT WHEAT` order survives (a verb-only rule would starve every animal)
and that `SELL FERTILIZER` survives (dropping sells would invert the experiment).

Two cleanups landed with it, both pre-existing and both flagged by the analyzer
as unguarded throwing calls:

1. `_current_step` was **copy-pasted into four overlays** (`v10_mix`,
   `v10_sell_chunks`, `v10_tomato`, this one). It is now one guarded helper,
   `kaggriculture.loading.current_step`, imported by all four. Behaviour on valid
   observations is identical, so every previously measured verdict stands.
2. The unguarded `int(order[2])` parses in `v10_sell_chunks` and `v10_tomato` now
   pass an unparseable order through instead of raising.

### T2 — measured verdict: REFUTED (and it is a design invariant, not a sample)

8 paired games (4 seeds x both seats, same opponent), probe
`scripts/no_fert_buy_probe.py`:

| metric | control | no fertilizer buys |
| --- | --- | --- |
| mean bank | 79,274 | 79,065 (**-$210**) |
| FERTILIZE applied | 97.0 | **97.0 (survive)** |
| fertilizer bought | 66.5 | 0.0 |
| fertilizer sold | 371.2 | 307.2 |
| quadrants / hands / animals | 3 / 8.5 / 17 | 3 / 8.5 / 17 (no move) |
| strawberry / carrot / melon harvest | 258 / 51 / 66 | 255.5 / 47 / 66 |

The economist's hypothesis was **+$5,500 to +$6,600**. Measured: **-$210**, i.e.
0.27% of the bank, with the applications untouched.

**The mechanism, and why the model could not have found it:** the engine quotes
`BUY_PRODUCT` at the POST-buy inventory - `market_price(item, inventory - 1)`
(`kaggriculture.py:601`) - and says so explicitly: *"Quote at post-buy inventory
so a buy/sell round-trip against an unchanged market nets zero"* (`:599-600`).
The tape's buys are therefore a designed wash: it buys 66.5 units and sells 64
fewer, and the round trip nets zero by construction. The model priced the buys at
the $100 base while pricing the sells at the measured ~$5 marginal into a glut,
which is not the price the engine charges.

**Why no arena run.** This is a design invariant, not a sample mean: the only
route to a gain would be buying cheap, and the engine's quote rule removes it. The
measured residual (-$210, and -$2,100 even if the measurement were wrong by 10x)
is an order of magnitude under the $5,000 bar. This is the same reasoning that
closed buy-timing arbitrage - a bound that survives a large error needs no more
statistical power. Spending ~15 minutes of arena compute and a dataset re-version
to tighten a CI around a structurally-zero effect would not change the verdict.

**Verdict: the eighth measured V10 lever, closed.** The fertilizer the tape
APPLIES is profitable (+$3,078, verified); the fertilizer it BUYS is a wash by
engine design. V10 remains closed.

### Follow-up (out of scope, deliberately not in this diff)

`scripts/fert_probe.py` repeats the same trace-reading scaffolding this probe
needed (`holdings`, market-verb scanning, the `steps[turn][seat]` indexing). A
single shared overlay-measurement harness would remove that duplication; it is a
separate work unit and touches a verified script.
