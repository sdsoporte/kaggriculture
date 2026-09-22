# Feature: v4 — scale: land, hands, animals, liquidation

Goal: move from tier 1 to tier 2+ by adding the structural levers identified in the live analysis:
- 3 quadrants (NE day ~7, SW day ~10, never SE — frontier signature)
- 8–12 hands from day 10 (Fibonacci batch hires, sell-before-buy)
- Animals: cows (milk), sheep (wool), geese (eggs) — with coops/pastures, CARE, feed wheat chain
- Disciplined liquidation: meter premium crops, ride wheat/egg floor, hit town ×4 from day 20
- Optional: add strawberry/melon to rotation when land supports it

Base: `baseline_v3` (harvest-priority, DIG, per-crop plant_until). Each lever verified with paired benchmarks.

## Tasks

- [ ] V1. Land buying logic: buy NE when premium crop ready & cash ≥ price + buffer; SW when melon/strawberry fills NE; never SE (frontier signature).
- [ ] V2. Hand scaling: target 4→8 hands day 7, 12 hands day 10; batch hires at hour 0–2; reserve fib cost in market ordering.
- [ ] V3. Animal chain: build coops/pastures on unlocked land; buy geese→cows→sheep by revenue tier; feed wheat (grow don't buy), CARE daily, collect fertilizer.
- [ ] V4. Liquidation discipline: meter tomato/strawberry/melon (max 12/turn, price floor), ride wheat/egg floor, town center ×4 from day 20, sell early if glut signal.
- [ ] V5. (Optional) Add strawberry/melon to rotation when land ≥ 2 quadrants.
- [ ] V6. Full ladder + report; prepare main_v4.py; explicit upload decision.

## Evidence

- (commits recorded per task)