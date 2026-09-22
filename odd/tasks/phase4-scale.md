# Feature: v4 — scale: land, hands, animals, liquidation

Goal: move from tier 1 to tier 2+ by adding the structural levers identified in the live analysis:
- 3 quadrants (NE day ~7, SW day ~10, never SE — frontier signature)
- 8–12 hands from day 10 (Fibonacci batch hires, sell-before-buy)
- Animals: cows (milk), sheep (wool), geese (eggs) — with coops/pastures, CARE, feed wheat chain
- Disciplined liquidation: meter premium crops, ride wheat/egg floor, hit town ×4 from day 20
- Optional: add strawberry/melon to rotation when land supports it

Base: `baseline_v3` (harvest-priority, DIG, per-crop plant_until). Each lever verified with paired benchmarks.

## Tasks

- [x] V1. Land buying logic: NE day 7 when tomato established, SW day 10, never SE (frontier signature)
- [x] V2. Hand scaling: 4→6 day 3, 8 day 7, 10 day 10, 12 day 12 (fib batch hires)
- [ ] V3. Animal chain: coops/pastures, feed wheat, CARE, fertilizer (implemented but placement needs tuning - animals bought but not placed)
- [ ] V4. Liquidation discipline: meter premium, price floors, town ×4
- [ ] V5. Optional: strawberry/melon when land ≥2 quads
- [ ] V6. Full ladder + main_v4.py + upload decision

## Evidence

- (commits recorded per task)