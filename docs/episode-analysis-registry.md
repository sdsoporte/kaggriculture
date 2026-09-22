# Episode Analysis Registry — Kaggriculture Live Games

**Date:** 2026-09-22
**Purpose:** Permanent record of live episode analysis for v4/v5 planning.

---

## Episode 1: Our v3 (Kakarott 10) vs Anjali Shah (Pass)

**Submission:** `56450455` (main_v3.py)
**Episode:** `111853589`
**Opponent:** Anjali Shah (pass agent, bank 3000)
**Our rating:** 332.9 (rank ~9259)

### Final state (Day 30)

| | v3 (Kakarott 10) | Anjali Shah |
|---|---|---|
| Final bank | **590** | 3,000 |
| Quadrants | 1 (NW only) | 0 |
| Hands | 4 | 0 |
| Animals | 2 cows | 0 |
| Crops | Melon seeds 18, wheat/carrot/tomato rotated | None |
| Shed | 353 | Empty |

### Timeline

| Day | Event |
|---|---|
| 0-2 | Initial wheat/carrot/tomato plant in NW (20 tiles) |
| 7 | ❌ **No NE purchase** (frontier day 7) |
| 10 | ❌ **No SW purchase** (frontier day 10) |
| 10-20 | ~8 hands target but gating inactive |
| 20 | ~4,000 bank (vs frontier 47k) |
| 30 | 590 final (vs frontier 98k) |

### Root cause (confirmed by v4 debugging)

Land buying gated behind `TARGET_HANDS >= 8` but `HANDS_BY_DAY` ramps to 8 only at day 7 → **land buying never triggers on day 7**.

---

## Episode 2: DSM #1 vs KawattaTaido (Top-tier match)

**Submission:** `56427917` (DSM frontier program)
**Episode:** `111864216`
**Opponent:** KawattaTaido (2987.5, rank ~7)
**DSM rating:** 3191.4 (#1)

### Final state (Day 30)

| | DSM #1 | KawattaTaido |
|---|---|---|
| Final bank | **~98,000** | ~82,000 |
| Quadrants | **3** (NW, NE, SW) | 1 |
| Hands | **~12** (badges 2-4 on tiles) | 1-2 |
| Animals | **Cows + sheep + geese** | Few/none |
| Pastures/coops | Middle strips, middle rows | Few/none |
| Day-20 bank | **~47,000** | ~41,000 |
| Final bank | **~98,000** | ~82,000 |
| Waste | Minimal (2 tomato + 4 wheat) | Weeds + unsold items |

### Timeline (DSM)

| Day | Event |
|---|---|
| 0 | 3 quadrants (NW, NE, SW unlocked), 12 hands active |
| 7 | NE purchased (tomato established) |
| 10 | SW purchased (NE full with melon/strawberry) |
| 10+ | 12 hands active, 12+ badges on tiles |
| 20 | **47,000 bank** (doubles to 98k by day 30) |
| 30 | **98,000 final**, near-zero waste (2 tomato + 4 wheat) |

### Layout (Frontier signature confirmed)

- **3 quadrants max** (NW, NE, SW — **never SE**)
- **~12 hands** from day 10 (fib batch hires day 0-2)
- **Cows + sheep + geese** in middle strips
- **Melon/strawberry** on NE/SW, wheat feed chain
- **CARE daily** on animals, feed wheat chain
- **Liquidation discipline**: 2× bank in last 10 days, near-zero waste

---

## Comparative Gap Analysis

| Levantador | DSM (Tier 10) | Our v3/v4 | Factor |
|---|---|---|---|
| **Land** | 3 quadrants (NE day 7, SW day 10, **never SE**) | v4: 1 quadrant (land gating broken) | **3× tiles** |
| **Labor** | 12 hands day 10 (fib batch) | v4: 8 hands max | **3× actions/day** |
| **Animals** | Cows/sheep/geese + CARE + feed wheat | 0 animals (deferred) | **0 recurring revenue** |
| **Crops** | Melon/strawberry premium + wheat feed | Wheat/carrot/tomato only | **5× gross/tile** |
| **Liquidation** | 2× bank last 10 days, waste ~0 | 590-5000 final | **Discipline gap** |
| **Scale** | 3 quadrants × 12 hands = 36 effective units | 1 quadrant × 4-8 hands = 4-8 units | **~9× throughput** |

---

## Frontier Program Signature (Confirmed by both top games)

| Attribute | Value |
|---|---|
| **Max quadrants** | 3 (NW, NE, SW — **never SE**) |
| **Hand scaling** | 4→8 day 7, 12 day 10 (fib batch day 0-2) |
| **Animal mix** | Cows + sheep + geese (pastures/coops middle strips) |
| **Crop mix** | Melon/strawberry premium + wheat feed |
| **Animal care** | CARE daily, feed wheat chain (grow don't buy) |
| **Liquidation** | Meter premium (max 12/turn), ride wheat/egg floor, town ×4 from day 20 |
| **Endgame** | 2× bank last 10 days, waste ~0 |

---

## v4/v5 Action Plan (Ordered by Impact)

### V4 Priority 1: Fix Land Buying (Week 1)
```python
# Fix gating: buy NE day 7 if tomato age>=2 AND cash>=1500
# Buy SW day 10 if NE full AND cash>=2500
# NEVER buy SE (frontier signature)
```

### V4 Priority 2: Hand Scaling (Week 1)
```python
HANDS_BY_DAY = [(0, 4), (3, 6), (7, 8), (10, 12)]
# Batch hires hour 0-2, sell-before-buy in market queue
```

### V4 Priority 3: Animal Chain (Week 2)
```python
# Build pastures on NE/SW (cows=3, sheep=2)
# Feed wheat chain: grow don't buy (51% of gap per research)
# CARE daily → +1 yield per cared day
# Collect fertilizer daily
```

### V4 Priority 4: Liquidation Discipline (Week 2)
```python
# Meter premium: max 12/turn melon/strawberry, refuse <120
# Ride wheat/egg floor (only glut-safe floors)
# Town ×4 from day 20 (melon only town sink)
# Early batch sell if glut signal (price < 90% base)
```

### V5: Full Frontier Clone (Week 3)
- 3 quadrants, 12 hands, melon/strawberry/cow/sheep/goose
- Full CARE + feed wheat chain + fertilizer
- Town ×4 melon absorption from day 20
- Fingerprinting detector for tape matchups (post-final)

---

## Evidence Artifacts

| File | Description |
|---|---|
| `/tmp/pi-playwright/kaggriculture/episode-111853589-start.png` | Our v3 Day 0 |
| `/tmp/pi-playwright/kaggriculture/episode-111853589-mid.png` | Our v3 ~Day 20 |
| `/tmp/pi-playwright/kaggriculture/episode-111853589-end.png` | Our v3 Day 30 |
| `/tmp/pi-playwright/kaggriculture/dsm-ep1-start.png` | DSM Day 0 |
| `/tmp/pi-playwright/kaggriculture/dsm-ep1-mid.png` | DSM ~Day 20 |
| `/tmp/pi-playwright/kaggriculture/dsm-ep1-end.png` | DSM Day 30 |

---

## Next Immediate Action

**Fix v4 land buying gating** → separate land logic from `TARGET_HANDS` gate, buy NE day 7 if tomato age≥2 & cash≥1500, SW day 10 if NE full. Then re-benchmark v4 vs v3.

---

*Generated from live episode analysis via Playwright (Chromium + system Chrome). Screenshots saved in `/tmp/pi-playwright/kaggriculture/`.*