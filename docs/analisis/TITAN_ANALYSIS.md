# TITAN Agent Deep Analysis

**Source:** `docs/analisis/` (extracted from `titan-current.tar.gz`, commit `7fcf729c`, SHA256 `e363125093463d1f7a63a01aecb70646344dae5b318952e13a1b1641e2043e58`)

---

## 1. Architecture Overview

TITAN is a **modular, feature-flagged agent** built around a **core runtime** (`TitanAgent`) that composes multiple independent subsystems:

| Subsystem | Module | Purpose |
|---|---|---|
| **Production Planning** | `integrated_selected.py` + `arlene_plan.py` | ALDER seed budget + Arlene (T08 arrival contract) |
| **SELL Scheduling** | `ordered_selected_sell.py`, `selected_action_sell.py` | ATLAS projection + SelectedActionSell |
| **Market Pressure** | `pressure_priority.py`, `mechanics.py` | LARK-responsive sell ordering |
| **Animal Chain** | `integrated_selected.py` + mechanics | Cows/Sheep/Geese with feed/CARE |
| **Land Buying** | `integrated_selected.py` + market | Land purchase timing |
| **Liquidation** | `mechanics.py`, `sell_priority.py` | Market pressure, price floors, town demand |
| **Deadline Management** | `deadline_adapter.py` | 1.0s budget with 0.01s reserve |
| **Fourth Quadrant** | `fourth_quadrant.py`, `terminal_mechanics.py` | ECON market timing |
| **Spatial Routing** | `spatial_tempo.py`, `scheduler.py` | Unit assignment, pathing |

**Architecture Pattern:** Feature-flagged composition. `Features` dataclass controls which subsystems activate. Each subsystem is lazy-loaded and cached.

---

## 2. Core Strategy (from `TITAN-CONFIG.json`)

```json
{
  "consumer": "frozen",           // FrozenSelected route (frozen SELL ledger)
  "seed": true,                   // ALDER seed budget active
  "funding": true,                // Seed funding certificate active
  "terminal_route": false,        // No terminal ECON composition
  "committed": true,              // Committed SELL route
  "budget_seconds": 1.0,          // 1.0s action budget
  "reserve_seconds": 0.01,        // 0.01s deadline reserve
  "redundant_hire": true,         // T10 redundant hire certificate
  "fourth_quadrant": false,       // ECON market timing disabled
  "market_pressure": true,        // LARK-responsive sell ordering
  "committed_seed_retry": false,  // Seed retry certificate disabled
  "operating_stock": true,        // Protect operating stock at market boundary
  "idle_fertilizer": true         // Fertilizer receipt tracking
}
```

**Key insight:** TITAN uses **FrozenSelected** consumer (frozen SELL ledger) with ALDER seed budget, Arlene plan overlay, and LARK market pressure. No fourth quadrant, no terminal route.

---

## 3. Key Strategic Components

### 3.1 Production Planning: ALDER + Arlene

**`integrated_selected.py`** + **`reference/integrated-selected/claude/arlene_plan.py`**

- **ALDER Seed Budget** (`reference/integrated-selected/alder/seed_budget.py`): Bounds seed purchases by counting remaining PLANT requests on the committed route. Uses plant suffix analysis across prefix-compatible routes.
- **Arlene Plan Overlay** (`reference/integrated-selected/claude/arlene_plan.py`): One-way producer with `PlanOverlay` + `RouteAwareCapChooser`. Pinned one-way producer (`one_way=True`).
- **T08 Arrival Contract** (`reference/selected-action/t08/arrival_contract.py`): Conditional short continuation based on current tape and committed one-way cap errands.

**Key insight:** Seed budget is **route-aware** — counts every PLANT request on prefix-compatible routes, not just current seeds.

### 3.2 SELL Scheduling: ATLAS + SelectedActionSell

**`ordered_selected_sell.py`** + **`selected_action_sell.py`** + **`reference/ordered-feasibility/atlas/projection.py`**

- **ATLAS Projection**: Projects unit actions forward (HORIZON=8), handles route boundaries, stock-dependent pickups, market absorption.
- **SelectedActionSell**: Generic SELL transform with horizon, arrival contracts, reservations.
- **Binding**: Exact selected-action binding for prepared packets.

### 3.3 Market Pressure: LARK-Responsive Sell Ordering

**`pressure_priority.py`** + **`sell_priority.py`** + **`mechanics.py`**

- Reorders SELL blocks by public exposure (public exposure = market exposure to opponent)
- `MarketPath` class: exact price path with rival, absorption, joint/single cash
- `optimize_lot`: enumerates legal first-quantity + later-tranche families
- **Alignment**: `paired` (same turn), `after` (rival after), `before` (rival before)

**Key insight:** TITAN uses **exact market simulation** with rival modeling, not heuristics.

### 3.4 Animal Chain (from `titan_runtime.py` + `mechanics.py`)

| Animal | Cost | Structure | First Yield | Interval | Max Held | Product | Feed |
|---|---|---|---|---|---|---|---|
| GOOSE | 300 | COOP | 4 | 1 | 4 | EGG | WHEAT |
| COW | 400 | PASTURE | 8 | 2 | 6 | MILK | WHEAT |
| SHEEP | 500 | PASTURE | 6 | 3 | 6 | WOOL | WHEAT |

**Animal Logic** (from `titan_runtime.py` + `mechanics.py`):
- **FEED**: 1 wheat/animal/day, must feed daily or animal escapes (2 consecutive unfed = escape)
- **CARE**: Banks +1 bonus per fed+cared day, paid on next scheduled production
- **CARE Bonus**: Capped by `max_held` on tile
- **FERTILIZER**: 1/animal/day from `COLLECT_FERTILIZER`
- **FEED Wheat**: Grow don't buy when possible (51% of gap per research)

**Animal Jobs Priority** (from `_collect_jobs`):
1. `feed` + `care` + `harvest_animals` (highest)
2. `water` (crops)
3. `harvest` (crops)
4. `build_structures` (COOP/PASTURE)
5. `place_animals` (PLACE on free structures)
6. `plant` (crops)
7. `dig` (WEED)
8. `collect_fert` (lowest)

**Animal Restocking**: PICKUP at shed (SHED_TILE) → PLACE on structure. Uses routing memory `_ANIMAL_ROUTE_MEMORY` to chain PICKUP → PLACE.

### 3.3 Land Buying (from `titan_runtime.py` + `integrated_selected.py`)

```python
# Land prices: NE=1000, SW=2000, SE=4000
LAND_ORDER = ["NE", "SW", "SE"]
LAND_PRICES = [1000, 2000, 4000]

# V1: Land buying — NE day 7 when tomato established; SW day 10 when NE full.
# Never buy SE (frontier signature: 3 quadrants max).
```

**Land Gating:**
- NE: day ≥ 7, tomato established (age ≥ 2), cash ≥ 1000+500
- SW: NE unlocked, day ≥ 10, cash ≥ 2000+500
- **Never SE** (frontier signature: 3 quadrants max)

**Land Gating by Hands:** `TARGET_HANDS >= 8` required (frontier: 12 hands by day 10)

### 3.5 Hand Scaling (from `titan_runtime.py`)

```python
# Frontier: 4→6 day 3, 8 day 7, 10 day 10, 12 day 12
HANDS_BY_DAY = [(0, 4), (3, 6), (7, 8), (10, 10), (12, 12)]
```

- Hires at hour 0-2 (Fibonacci batch: 1,1,2,3,5,8...)
- Cost reserved before seed purchases (sell-before-buy)
- `TARGET_HANDS = 12` max

### 3.5 Market & Liquidation

**Market Price Function** (`mechanics.py`):
- `I0 = 10,000`, base price at I0
- Shape functions: `linear`, `sq`, `sqrt`, `log`, `log10`, `hinge`
- Floor at $1

**Price Curve Shapes:**
| Product | Base | T | Below Func | Below Target | Above Func | Above Target |
|---|---|---|---|---|---|---|
| WHEAT | 25 | 400 | sqrt | 0.80 | log | 0.20 |
| CARROT | 35 | 450 | hinge | 1.00 | sqrt | 0.70 |
| TOMATO | 60 | 200 | hinge | 0.40 | sqrt | 0.60 |
| STRAWBERRY | 120 | 100 | sqrt | 0.70 | linear | 1.60 |
| MELON | 250 | 300 | log | 0.20 | sq | 3.60 |
| EGG | 50 | 332 | hinge | 0.40 | log | 0.20 |
| MILK | 160 | 122 | sqrt | 0.60 | linear | 1.60 |
| WOOL | 200 | 105 | log | 0.20 | sq | 3.20 |
| FERTILIZER | 100 | 200 | linear | 0.40 | linear | 0.40 |

**Key Insight:** Only **WHEAT (sqrt/log, floor 16)** and **EGG (hinge/log, floor 40)** hold a glut floor. **CARROT, TOMATO, STRAWBERRY, MELON, MILK, WOOL** collapse to $1 under glut.

**Liquidation Rules** (`_sale_quantity` in `titan_runtime.py`):
- Day ≥ 28 (LIQUIDATE_FROM_DAY): sell all
- Shed pressure ≥ 70 (SHED_PRESSURE): sell up to SELL_CHUNK (40)
- Glut signal: price < 90% base (LOW_PRICE_RATIO = 0.90) → sell early
- SELL_CHUNK = 40, SHED_PRESSURE = 70, LIQUIDATE_FROM_DAY = 28

**Town Demand** (from `mechanics.py`):
- Town Center: 1 of each non-fertilizer product every 24 turns (×1 day<10, ×2 day≥10, ×4 day≥20)
- Shops: unlock every 3 days (with replacement), consume every 4 turns
- YARN_STORE: WOOL 2x; PET_CAFE: CARROT 2x

---

## 4. Critical Differences: TITAN vs Our v4

| Feature | TITAN (Tier ~3000) | Our v4 (Tier ~1000) | Gap |
|---|---|---|---|
| **SELL Consumer** | FrozenSelected (frozen ledger) | Custom scheduler | Frozen ledger = replay consistency |
| **Production** | Arlene + ALDER + ATLAS | Custom scheduler | Route-aware planning |
| **Seed Budget** | ALDER (route-aware suffix) | Simple target-based | Route-aware = no overbuy |
| **Animals** | 3 cows + 2 sheep + 2 geese + CARE + feed chain | 3 cows + 2 sheep + 2 geese (but placement broken) | CARE + feed chain = 4.2× milk |
| **Land** | NE day 7, SW day 10, never SE | NE day 7, SW day 10 | Same timing, but TITAN has 12 hands |
| **Hands** | 4→6→8→10→12 (day 3/7/10/12) | 4→6→8 (max 8) | 12 hands = 120 actions/day |
| **Liquidation** | Market pressure, town ×4 day 20 | Reactive (price < 90% base) | TITAN uses LARK market pressure |
| **Animals** | Cows (3) + Sheep (2) + Geese (2) + CARE | Cows 3, Sheep 2, Geese 2 (placement broken) | CARE = 4.2× milk |
| **Fertilizer** | Idle fertilizer tracking | Basic buy | CARE + fertilizer = 2× yield |

---

## 5. Key Gaps in Our v4

| Gap | TITAN | Our v4 | Fix Priority |
|---|---|---|---|
| **Animal Placement** | Routing memory (PICKUP→PLACE chain) | Broken (animals stuck in shed) | **CRITICAL** |
| **Hand Scaling** | 12 hands by day 12 | Max 8 hands | **HIGH** |
| **Frozen SELL Ledger** | FrozenSelected + ALDER | Custom scheduler | **HIGH** |
| **Market Pressure** | LARK exact simulation | Reactive (price < 90%) | **MEDIUM** |
| **Fertilizer/CARE** | Idle fertilizer tracking, CARE banking | Basic buy | **MEDIUM** |
| **Fourth Quadrant** | ECON market timing | Disabled | **LOW** |
| **Terminal Route** | ECON composition | Disabled | **LOW** |

---

## 6. Immediate Fixes for v4

### 1. Fix Animal Placement (CRITICAL)
```python
# Current: animals bought but never placed (stuck in shed)
# Fix: Implement PICKUP→PLACE routing chain
# - PICKUP at SHED_TILE (tag with target structure)
# - PLACE at structure (need=(animal, 1) so only carrier gets it)
# - Routing memory _ANIMAL_ROUTE_MEMORY tracks unit after PICKUP
```

### 2. Increase Hand Cap to 12 (Day 12)
```python
HANDS_BY_DAY = [(0, 4), (3, 6), (7, 8), (10, 10), (12, 12)]
TARGET_HANDS = 12  # for land gating
```

### 3. Land Buying Timing (already correct: NE day 7, SW day 10, never SE)

### 4. CARE + Feed Chain (MEDIUM)
```python
# FEED: 1 wheat/animal/day, must feed daily
# CARE: banks +1 bonus per fed+cared day, paid on next production
# FERTILIZER: 1/animal/day from COLLECT_FERTILIZER
```

### 5. Seed Budget → ALDER (HIGH)
Replace simple `CROP_TARGETS` with ALDER route-aware suffix counting.

### 6. Market Pressure (MEDIUM)
Integrate `pressure_priority.py` + `sell_priority.py` for LARK-responsive sell ordering.

---

## 7. Next Steps Priority

| Priority | Task | Effort | Impact |
|---|---|---|---|
| **1** | Fix animal PICKUP→PLACE chain | Medium | **CRITICAL** (animals = 40% of gap) |
| **2** | Increase hand cap to 12 | Low | **HIGH** (3× action budget) |
| **3** | Frozen SELL ledger + ALDER seed budget | High | **HIGH** (route-aware) |
| **4** | CARE + feed wheat chain | Medium | **HIGH** (4.2× milk) |
| **5** | Market pressure (LARK) | High | **MEDIUM** |
| **6** | Fertilizer/CARE banking | Medium | **MEDIUM** |
| **7** | Fourth quadrant / terminal | High | **LOW** (stretch) |

---

## 8. Files to Study/Modify Next

| Priority | File | Purpose |
|---|---|---|
| **1** | `src/kaggriculture/agents/baseline_v4.py` | Fix animal placement, add hand scaling |
| **2** | `src/kaggriculture/agents/baseline_v4.py` | Add CARE + feed chain |
| **3** | `src/kaggriculture/agents/baseline_v4.py` | Hand scaling 4→6→8→10→12 |
| **4** | New: `src/kaggriculture/agents/seed_budget.py` | ALDER route-aware seed budget |
| **5** | New: `src/kaggriculture/agents/frozen_scheduler.py` | FrozenSelected + ATLAS integration |

---

## 9. Key Learnings from TITAN

1. **Route-aware planning beats greedy heuristics** — ALDER counts PLANT suffixes on prefix-compatible routes
2. **Frozen SELL ledger enables recovery** — FrozenSelected stores completed state, replays fallbacks
3. **Market pressure beats static rules** — LARK exact simulation with rival modeling beats static price thresholds
4. **Animal chain = 40% of gap** — CARE + feed wheat chain = 4.2× milk; requires PICKUP→PLACE routing
5. **Frozen SELL + ALDER + Arlene = Tier 3000** — Our custom scheduler is the bottleneck
5. **Route recovery > production** — T08 arrival contract + one-way producer > generic replanning

---

## 10. Next Actions

1. **Immediate**: Fix animal PICKUP→PLACE chain in `baseline_v4.py` (use `_ANIMAL_ROUTE_MEMORY`)
2. **Today**: Increase `TARGET_HANDS=12`, `HANDS_BY_DAY = [(0,4),(3,6),(7,8),(10,10),(12,12)]`
3. **This week**: Implement ALDER seed budget (port from `seed_budget.py`)
4. **This week**: Add CARE + feed chain to `_collect_jobs` + `_market_orders`
5. **Next week**: Port ALDER seed budget (`seed_budget.py`)
5. **Next week**: Frozen SELL + ATLAS integration (longer term)

---

*Analysis based on TITAN v`7fcf729c` (SHA256 `e363125093463d1f7a63a01aecb70646344dae5b318952e13a1b1641e2043e58`), extracted to `docs/analisis/`*