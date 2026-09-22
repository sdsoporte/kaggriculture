# Feature: v3 — harvest-priority, DIG weeds, per-crop plant_until

Goal: implement the three corrections from crop-timing-analysis.md to improve over v2.

The three fixes target the largest losses identified in the dynamic trace:
- ripe one-time crops rotting because water outranks harvest,
- 18/25 tiles weed-locked forever without DIG,
- wasted seed on late-planted tomatoes, strawberries, melons.

Each fix is verified with paired benchmarks before proceeding to the next.

## Tasks

- [x] C1. Harvest-priority: ripe one-time crops (age == max_day) outrank watering.
- [x] C2. DIG weed maintenance: add DIG jobs for WEED tiles, low priority.
- [x] C3. Per-crop plant_until: respect wheat≤25, carrot≤26, tomato≤16–21 (start with tomato≤21).
- [x] C4. Run paired benchmarks + full ladder + record

## Evidence

- C1: harvest-priority — ripe one-time crops at max_day outrank watering.
- C2: DIG jobs for WEED tiles added (low priority).
- C3: per-crop plant_until implemented — wheat 25, carrot 26, tomato 21.
- C1–C3 implementation: `src/kaggriculture/agents/baseline_v3.py` (import-free, self-contained).
- C1–C3 verification: pytest `3 passed`; v3 vs v2 `8-0` mean `+3243.9`; v3 vs Walter `8-0` mean `+5399.2`; v3 vs Rosa `0-8` mean `-455.2` (was v2 `-2498.1`, +2042.9 improvement); full ladder: Finn `8-0` `+8712.4`, Walter `8-0` `+5399.2`, Rosa `0-8` `-455.2`, Hana `0-8` `-3689.1`, Mateo `1-7` `-10513.8`.
- C1–C3 commit: parent will record the work-unit commit after this documentation update. 0f34dd6