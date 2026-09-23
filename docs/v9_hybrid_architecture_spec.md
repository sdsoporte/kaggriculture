# V9 Hybrid Architecture Specification

## 1. Executive Summary
Baseline V9 combines:
1. **Deterministic Tape Opening (Days 0–6 / Steps 0–150)**: Ultra-optimized opening sequence from the proven tape (Melon planting, Pasture construction, initial 2 Cows + 2 Sheep purchases, first Wool harvest).
2. **Autonomous Reactive Engine (Days 7–29 / Steps 151–720)**: Reactive state machine inspired by DSM's 4-quadrant layout:
   - Full 4-quadrant expansion (NE at Day 6, SW at Day 9, SE at Day 10).
   - Dynamic livestock scaling (adding Cows and Geese).
   - Priority task dispatch for 10–13 farm hands.
   - Guaranteed anti-starvation wheat reserve.

---

## 2. Phase 1: Tape Opening (Steps 0–150)
- **Duration**: Steps 0 to 150 (Day 0, 00:00 through Day 6, 06:00).
- **Execution**: Direct playback from `_BLOB` route (MAIN).
- **Milestone at Step 150**:
  - Sell 6 Wool.
  - Execute `['BUY_LAND']` ($1,000) to unlock Quadrant 2 (NE).
  - Transition control from tape playback to Reactive Engine.

---

## 3. Phase 2: Reactive Engine Architecture (Steps 151–720)

```
[Observation (obs)]
        │
        ▼
[State Tracker] ──> Tile Grid (10x10), Hands & Farmer Locations, Shed, Money
        │
        ▼
[Market & Expansion Brain]
        ├── Land Unlocks: SW ($2,000) at Day 9, SE ($4,000) at Day 10
        ├── Seed Purchases: Balanced Wheat / Carrot / Strawberry pipeline
        ├── Animal Scaling: Geese ($300) and Cows ($400)
        └── Surplus Liquidation: Sell non-feed products, preserve Wheat >= 15
        │
        ▼
[Task Generator & Priority Queue]
        ├── P0: Emergency Feeding (consecutive_unfed >= 1)
        ├── P1: Emergency Watering (consecutive_unwatered >= 1)
        ├── P2: Harvest (crops / animal products with yield_units > 0)
        ├── P3: Weed Digging (adjacent or occupied weeds)
        ├── P4: Routine Care & Feed (animals unfed today)
        ├── P5: Routine Watering (crops unwatered today)
        ├── P6: Fertilizing & Care
        └── P7: Planting empty unlocked tiles
        │
        ▼
[Bipartite Worker Dispatcher (Greedy / Hungarian)]
        └── Map Hands + Farmer to closest reachable high-priority tasks
        │
        ▼
[Action Output (Farmer, Hands, Market)]
```

---

## 4. Key Lessons Integrated from DSM (Episode 112206405)
1. **4th Quadrant (SE) Unlocked Early**: Day 10 unlocked via Melon harvest capital. Adds 25 productive tiles.
2. **Centralized Livestock Cluster**: Animals positioned in center (`x: 1..6, y: 2..6`) so workers from all 4 quadrants can feed and harvest without cross-map travel waste.
3. **Wheat Reserve Floor**: Never allow shed wheat to drop below 15 units.
4. **End-Game Wheat Selloff (Days 27–29)**: Stop feeding animals whose next yield falls after Day 29; sell the conserved wheat at peak prices during terminal settlement.
