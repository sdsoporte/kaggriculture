# Phase 1 Baseline Report — `baseline_v0` vs the Reference Ladder

**Date:** 2026-09-21
**Method:** paired benchmark (`scripts/benchmark.py`, 4 seeds × both seats = 8
games per pairing), same seed/seat/opponent for every comparison. Margins are
from `baseline_v0`'s perspective (final bank A − final bank B).
**Agent under test:** `src/kaggriculture/agents/baseline.py` ("Walter, fixed":
one farmer, no hires, no land, wheat only, harvest at max-yield day 4).

## Ladder placement

| Tier | Agent | Expected bank | W–L | Mean margin |
| --- | --- | --- | --- | --- |
| 0 | fallow_finn | 3,000 | 8–0 | **+2,131** |
| 1 | wheat_walter | 7,056 | 0–8 | **−1,808** |
| 2 | rotation_rosa | 12,929 | 0–8 | −6,597 |
| 3 | homestead_hana | 16,030 | 0–8 | −10,569 |
| 4 | melon_mateo | 28,349 | 0–8 | −16,177 |
| 5 | rancher_rita | 46,211 | 0–8 | −44,976 |
| 6 | broker_bea | 164,265 | 0–8 | −155,720 |
| 7 | ledger_lena | 164,540 | 0–8 | −155,342 |
| 8 | slotter_silas | 162,999 | 0–8 | −154,990 |
| 9 | closer_cleo | 148,546 | 0–8 | −139,529 |

`baseline_v0` finishes around **5,000** (seed-dependent: 4,527–5,494), so it
sits **between tier 0 and tier 1**. It beats the reward floor comfortably but
loses every game to Wheat Walter — the agent it was meant to improve on.

Seat consistency: per-seat mean margins differ by at most ~260 (finn) and
typically <100, small relative to the margins — the paired harness is doing
its job and first-seat bias is not driving any result above.

## Diagnosis: why "fixed" Walter loses to Walter

Day-by-day trace of one episode (seed 0, baseline on seat 0):

| Day | baseline $ | shed | plants | Walter $ | shed | plants |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | 2,850 | 0 | 7 | 2,860 | 0 | 6 |
| 6 | 2,790 | 0 | 1 | 3,319 | 3 | 6 |
| 13 | 3,177 | 0 | 0 | 3,904 | 2 | 6 |
| 21 | 3,636 | 0 | 4 | 5,160 | 3 | 6 |
| 29 | 4,869 | 0 | 0 | 6,350 | 9 | 0 |

Three compounding defects, all in our scheduler, not in the harvest-timing
"fix" itself:

1. **Over-planting.** `baseline_v0` plants every unlocked tile it has seed
   for (up to 11 tiles by day 3). One farmer has 24 actions/day and can
   sustainably service ~5–6 wheat tiles once travel is counted. The surplus
   plants miss two consecutive waterings and **die**, which is exactly the
   failure Fallow Finn's manifest entry warns about: "buying seeds it then
   fails to water."
2. **Seed-money bleed.** The market layer tops seed stock back up to 8 every
   day while planting "pays", so dead plants are silently re-funded. Our bank
   is flat-to-declining from day 1 to day 18 (2,850 → 3,147, with dips) while
   Walter's compounds.
3. **Water-first starvation of harvest/carry.** Watering outranks harvesting
   unconditionally, and with an oversized field the water queue is never
   empty, so harvest and carry are starved. The agent stabilizes at only
   **4 productive plants** (16 wheat every ~5 days) vs Walter's steady 6–7.

Net effect: the max-yield harvest timing is correct in isolation (4 units vs
Walter's 1–2 per tile), but it operates on a smaller, under-watered field
funded by a leaky seed budget.

## Consequences for phase 2 (baseline_v1)

Ordered by expected value, all verifiable with the paired harness:

1. **Cap the field to service capacity** (~5 wheat tiles for one farmer);
   never plant beyond it. This alone should stop the bleed and flip the
   Walter matchup.
2. **Let harvest outrank watering for mature tiles** (a mature tile about to
   pass max-yield day is worth more than watering a young one), and batch
   harvest+carry trips.
3. Only then re-test the ladder; tier 2 (Rosa) shows the next lever is
   **hired labour** (4 hands ≈ 7 coins/day for 5× the action budget), which
   is the largest single jump on the authored ladder (+87% over Walter).

## Reproduce

```bash
for f in data/reference-agents/*.py; do
  .venv/bin/python scripts/benchmark.py src/kaggriculture/agents/baseline.py "$f" --seeds 4
done
```
