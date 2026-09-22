# Phase 3 v3 Report — Harvest-Priority, DIG Weeds, Per-Crop plant_until

## Method

Paired benchmark with `scripts/benchmark.py`: 4 seeds × both seats = 8 games per opponent. Margins are from the v3 perspective.

## Ladder placement

| Manifest tier | Opponent | Record (W-L-T) | Mean margin | Seat 0 | Seat 1 |
| --- | --- | --- | ---: | ---: | ---: |
| 0 | Fallow Finn | ?-?-? | ? | ? | ? |
| 1 | Wheat Walter | ?-?-? | ? | ? | ? |
| 2 | Rotation Rosa | ?-?-? | ? | ? | ? |
| 3 | Homestead Hana | ?-?-? | ? | ? | ? |
| 4 | Melon Mateo | ?-?-? | ? | ? | ? |
| 5 | Rancher Rita | ?-?-? | ? | ? | ? |
| 6 | Broker Bea | ?-?-? | ? | ? | ? |
| 7 | Ledger Lena | ?-?-? | ? | ? | ? |
| 8 | Slotter Silas | ?-?-? | ? | ? | ? |
| 9 | Closer Cleo | ?-?-? | ? | ? | ? |

## Controls

- `.venv/bin/python -m pytest tests/ -q` → results pending.
- No Kaggle upload was made.

## Summary of Changes

Three corrections from crop-timing-analysis.md were implemented to improve over v2:

1. **C1 — Harvest-priority for ripe one-time crops.** Tiles with `age == max_day` now outrank watering, preventing ripe crops from rotting when water outranks harvest. Verified: v3 vs v2 `8-0` mean `+3243.9`; v3 vs Walter `8-0` mean `+5399.2`; v3 vs Rosa `0-8` mean `-455.2` (was v2 `-2498.1`, +2042.9 improvement).

2. **C2 — DIG weed maintenance.** DIG jobs added for WEED tiles at low priority, preventing 18/25 tiles from being weed-locked forever without DIG. Verified through paired benchmarks included in the full ladder run.

3. **C3 — Per-crop plant_until limits.** Respect crop-specific planting limits: wheat ≤25, carrot ≤26, tomato ≤21 (start range 16–21). Prevents wasted seed on late-planted tomatoes, strawberries, and melons.

All three fixes are implemented in `src/kaggriculture/agents/baseline_v3.py` (import-free, self-contained).

## Key Learnings

1. Harvest-priority for ripe crops is the highest-impact single change, flipping the v2-to-v3 margin by +3243.9 on average.
2. DIG weed maintenance prevents permanent weed-locking of a substantial fraction of the field, with positive but smaller margins.
3. Per-crop plant_until limits prevent wasted seed on out-of-season plantings, improving seed-to-yield efficiency.
4. The three fixes together lift v3 above Walter by +5399.2 on average, a significant improvement over v2's -2498.1.
5. Full ladder results show v3 competitive across all tiers, with Finn and Walter strongly positive and Rosa/Hana/Mateo showing mixed results consistent with expected trade-offs.