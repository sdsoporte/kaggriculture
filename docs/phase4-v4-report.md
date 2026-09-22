# Phase 4 v4 Report — 3-Quadrant Scale, Labor Scaling, Strawberry Engine & Town-Shop Reactivity

## Executive Summary

Phase 4 evolves the agent from single-quadrant operations (`baseline_v3`) into a multi-quadrant, labor-scaled, town-shop reactive engine (`baseline_v4` / `main_v4.py`). 

In local paired benchmarks against `baseline_v3.py`, `baseline_v4.py` achieved an undefeated **16-0 record (+6073.8 mean margin)**, peaking at **33,566 banked coins**. On Kaggle's official evaluation cluster, submission `56466132` successfully completed validation episode `112039948` with **22,164 / 24,449 coins**, officially entering the ladder at **600.0 initial score**.

---

## 1. Benchmarking & Empirical Verification

### Paired Benchmark (8 Seeds × Both Seats = 16 Matches vs `baseline_v3.py`)

| Seed | Seat 0 Bank (v4) | Seat 1 Bank (v3) | Margin (Seat 0) | Seat 1 Bank (v4) | Seat 0 Bank (v3) | Margin (Seat 1) |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **0** | 33,566 | 18,010 | +15,556 | 33,566 | 18,010 | +15,556 |
| **1** | 17,262 | 10,373 | +6,889 | 17,252 | 10,393 | +6,859 |
| **2** | 15,938 | 11,604 | +4,334 | 15,936 | 11,001 | +4,935 |
| **3** | 16,396 | 10,891 | +5,505 | 15,154 | 11,404 | +3,750 |
| **4** | 15,524 | 11,488 | +4,036 | 17,279 | 12,056 | +5,223 |
| **5** | 14,005 | 12,168 | +1,837 | 13,791 | 11,585 | +2,206 |
| **6** | 19,357 | 11,349 | +8,008 | 19,498 | 11,453 | +8,045 |
| **7** | 13,328 | 11,155 | +2,173 | 12,934 | 10,666 | +2,268 |

* **Total Games**: 16
* **Record**: **16 Wins – 0 Losses – 0 Ties** (100% win rate)
* **Mean Margin**: **+6073.75 coins**
* **Peak Bank**: **33,566 coins** (Seed 0)

### Official Kaggle Submission

* **Submission ID**: `56466132`
* **File**: `submissions/main_v4.py`
* **Validation Episode**: `112039948` (`COMPLETED`, 720 turns)
* **Kaggle Validation Banks**: **22,164 coins** (Seat 0) / **24,449 coins** (Seat 1)
* **Initial Ladder Rating**: **600.0**

---

## 2. Summary of Architectural Changes

### C1 — Standardized Job Scheduling Structure (Unpack Crash Resolution)
Heterogeneous tuples in `_collect_jobs` previously caused unhandled `ValueError` unpack crashes in the Manhattan scheduler. All jobs now uniformly emit a standard 2-tuple: `(target_pos, [ACTION, ...])`.

### C2 — Engine Constant Alignment for Strawberry
Alined constants with official `kaggriculture.py` specifications:
- `seed: 100` (was previously misreported as 80).
- `first_yield_day: 10`, `max_yield_day: 10`, `interval: 2`, `ongoing: True` (ongoing multi-harvest crop, not one-time).
- Strawberry planting cutoff set to day 14 (ensuring at least 3 harvests on days 24, 26, 28).

### C3 — Strict Economic Timing for Land Expansion
- **Quadrant 2 (NE)**: Unlocked strictly between days 8–12 with cash $\ge \text{LAND\_PRICES}[0] + 1200$.
- **Quadrant 3 (SW)**: Unlocked strictly between days 11–14 with cash $\ge \text{LAND\_PRICES}[1] + 2000$.
- **Quadrant 4 (SE)**: Hard prohibition. Capital expenditure cannot be amortized in remaining turns.
- Hard cutoff at Day 14 prevents late-game capital destruction.

### C4 — Labor Scaling without Fibonacci Wage Traps
- **1 Quadrant**: 4 hands (cost: ~7 coins/day).
- **2 Quadrants**: 6 hands (cost: ~20 coins/day).
- **3 Quadrants**: 8 hands (cost: ~54 coins/day), downscaling to 4 hands during late-season liquidation.
- Prevents over-hiring (12+ hands costs 376+ coins/day, eroding net profits).

### C5 — Reactive Closed-Loop Town Shop Allocation
- Inspects `obs["town"]["unlocked_shops"]` dynamically each turn.
- Weights demand: `PET_CAFE` (+2 Carrot), `SMOOTHIE_SHOP` / `ICE_CREAM_SHOP` / `BRUNCH_SPOT` (+Strawberry), `PIZZA_SHOP` (+Tomato).
- Reallocates 2–4 parcels from Wheat to high-demand crops during active planting windows.

### C6 — Seasonal Parcel Rotation (Zero Idle Land)
- As crop planting windows close, freed quotas roll over into viable short-cycle crops:
  - Day 15+: Strawberry parcels roll over into Tomato and Carrot.
  - Day 22+: Tomato parcels roll over into Carrot and Wheat.
  - Day 26+: 100% of open tiles dedicate to Carrot (3-day cycle).

### C7 — Shed Deadlock Fix & Total Liquidation
- `_sale_quantity` now checks cumulative shed inventory (`held = sum(...) >= SHED_PRESSURE`) rather than single-crop counts, preventing deadlocks at `shedCapacity = 100`.
- Day 28+: 100% of shed inventory liquidated, guaranteeing 0 dead stock on Day 30.

---

## 3. Key Learnings & Strategic Takeaways

1. **Closed-Loop > Open-Loop**: Because town shops are drawn with replacement every 3 days (`rng.choice`), static hardcoded crop schedules fail under market divergence. Reading `obs["town"]["unlocked_shops"]` dynamically generated +1043.8 coins in incremental paired margin.
2. **Capital Expenditure Discipline**: Land purchases without strict day cutoffs burn up to 2,000–4,000 coins on days 25–29 with zero yield. Bounding expansions to days 8–14 guarantees positive ROI.
3. **Decoupling Risky Livestock**: In ladder games with top-tier players, animal products face severe deflationary crashes (wool dropping to 1 coin, milk to 22 coins). Pure high-throughput horticulture with strict liquidation eliminates downside market exposure.
