# Crop Timing & Field Economics Analysis — 2026-09-21

**Status:** investigation record (read-only), input to the v3 planning.
**Sources:** `data/engine-tables/*.csv` (static), instrumented local episode
`baseline_v2` vs `fallow_finn`, seed 0 (dynamic trace, `/tmp` only).
**Season frame:** **30 days × 24 turns/day = 720 turns.** Every deadline below
is expressed in days but enforced in turns; the rot rule ticks every 2 turns.

---

## 1. Static crop economics (engine tables)

| Crop | Seed | First yield | Max day | Corte de siembra | Watered max yield | Base price |
|---|---|---|---|---|---|---|
| WHEAT | 10 | 2 | 4 | **≤ 25** | 4 (cap 6 needs fertilizer) | 25 |
| CARROT | 20 | 2 | 3 | **≤ 26** | 3 (cap 4 needs fertilizer) | 35 |
| TOMATO | 50 | 8 | 8 (+1/turn ongoing) | **≤ 21** (16 in practice, see §3) | 1/turn, cap 4 | 60 |
| STRAWBERRY | 100 | 10 | 10 (+1/2 turns) | **≤ 19** (13 full cycle) | 1/2 turns, cap 4 | 120 |
| MELON | 80 | 10 | 12 | **≤ 17** (15 to pick at cap) | **6 = cap at day 10** (days 10–12 are dead holding time) | 250 |

- Watering window runs from planting through max day; an unwatered crop
  dies the day after planting (every growth trace shows `WEED` at day 1).
- **Melon reaches its 6-unit cap on water alone at day 10** — two days
  before its `max_yield_day` of 12. Harvest at 10.
- Wheat/carrot caps in `crop_constants.csv` overstate watered-only yield
  (+50% / +33%); plan gross on watered yields unless fertilizer is bought
  (~100+/dose; justified only where the per-tile delta pays: wheat +50,
  carrot +35).

## 2. Prices, land, labor

- **Glut floors:** only WHEAT (16) and EGG (32) hold a floor at 50k market
  inventory. Carrot, tomato, strawberry, melon, milk, wool, fertilizer
  collapse to $1 within ~500 units above the 10k anchor. Melon/wool barely
  reward scarcity (<1.4×) — premium goods must be metered, never dumped.
- **Land:** NE 1000 / SW 2000 / SE 4000. Staple tiles cannot repay a second
  quadrant (manifest-confirmed); buy only when a premium crop is ready to
  plant the same day. Melon at ~115–150 gross/tile/day repays any quadrant
  in <2 days.
- **Hands:** daily-reset Fibonacci: 4 = 7/day, 8 = 54/day, 12 = 376/day.
  4 hands from day 1 is the best deal in the game; 12 hands only makes
  sense on premium revenue.

## 3. Dynamic trace — baseline_v2 vs fallow_finn, seed 0

Season: 720 turns. Final bank 10,371; season low 2,130 (day 9).

1. **Rot rule (hard deadline):** one-time crops decay 1 yield every 2 turns
   starting turn 0 of day `max_day + 1` (`max_lifespan_step = (planted_day +
   max_day + 1) × 24`). Harvest-at-max-day is a same-day deadline, not a
   preference. v2's harvest compliance was 48/48 on harvested crops — but
   **12 ripe full-yield crops rotted (~20% of season output)** because WATER
   outranks HARVEST on full-field days (days 4/9/10).
2. **Weed lock is the #1 capacity destroyer:** ripe crops and expired
   tomatoes become WEED; v2 has no DIG. **18 of 25 tiles weed-locked from
   day 12**; field utilization 35% of cap for the entire second half.
3. **Money timeline:** bank never below 2,130 → Q2+Q3 (3,000) affordable
   from day 0; Q4 (4,000) from day 10. v2 never issues BUY_LAND; the
   headroom is real but unexploited (correctly deferred — no premium crop
   to plant on it yet).
4. **4 hands is episodically binding, not structurally:** full-field days
   ran 0% PASS (over capacity exactly at rot deadlines), weed days 66%
   PASS. Season PASS fraction 40%, bimodal. The fix is job priority, then
   DIG, then (later) more hands.
5. **Water discipline perfect:** zero unwatered deaths; all losses were
   ripe-crop rot, a priority failure, not hydration.
6. **Reactive sales worked:** SELL fired days 9/19/26 (shed ≥ 70 and
   glut signal), all 202 units sold by day 28, shed empty at the bell.
7. **Flat `PLANT_UNTIL_DAY=25` wastes seeds:** late tomato plantings can't
   repay; per-crop cutoffs required (wheat 25, carrot 26, tomato 16–21).

## 4. v3 ordered fixes (each verified with paired benchmarks)

1. **Rot-deadline harvest priority:** age == max_day outranks WATER.
2. **DIG weed maintenance:** recover the 18 lost tiles.
3. **Per-crop `plant_until_day`:** wheat 25 / carrot 26 / tomato 16–21.
4. **Land buying** only when a premium crop is ready the same day (with
   melon/strawberry in the policy).
5. **Scale hands 8→12** only when premium revenue pays the Fibonacci curve.

Constraint reminder for all planning: **30 days × 24 turns = 720 turns
total**; every cycle time, travel cost, and deadline above is budgeted in
those turns.
