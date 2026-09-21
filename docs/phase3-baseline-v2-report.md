# Phase 3 Baseline v2 Report — Labour Rotation

## Method

Paired benchmark with `scripts/benchmark.py`: 4 seeds × both seats = 8 games per opponent. Margins are from the v2 perspective. The optional `pyspiel` warning is non-blocking.

## Ladder placement

| Manifest tier | Opponent | Record (W-L-T) | Mean margin | Seat 0 | Seat 1 |
| --- | --- | --- | ---: | ---: | ---: |
| 0 | Fallow Finn | 8-0-0 | +6404.0 | +6442.0 | +6366.0 |
| 1 | Wheat Walter | 8-0-0 | +2004.2 | +2108.5 | +1900.0 |
| 2 | Rotation Rosa | 0-8-0 | -2498.1 | -2550.0 | -2446.2 |
| 3 | Homestead Hana | 0-8-0 | -6299.9 | -6439.8 | -6160.0 |
| 4 | Melon Mateo | 0-8-0 | -16660.1 | -19822.0 | -13498.2 |
| 5 | Rancher Rita | 0-8-0 | -25828.9 | -25949.8 | -25708.0 |
| 6 | Broker Bea | 0-8-0 | -135959.5 | -136046.2 | -135872.8 |
| 7 | Ledger Lena | 0-8-0 | -135940.2 | -135933.8 | -135946.8 |
| 8 | Slotter Silas | 0-8-0 | -135204.2 | -135304.5 | -135104.0 |
| 9 | Closer Cleo | 0-8-0 | -140448.6 | -141132.8 | -139764.5 |

## Controls

- `.venv/bin/python -m pytest tests/ -q` → `3 passed in 22.58s`.
- No Kaggle upload was made.

## Interpretation

- v2 owns tier 1.
- C4 improves the Rosa margin by `+2749.1` over v1's `-5247.2`, but still fails the tier-2 full gate.
- Seat asymmetry is small except for Mateo and Cleo.
- These results make no universal or generalization claim.

## Strategy implications

The top gap is scale: three quadrants, approximately 12 hands, animals, and liquidation. Shared-market endgame prices can crash under synchronized tape sales. C4's reactive low-price/shed-pressure sales are directionally correct; Laya/fingerprinting remains post-final/stretch.

## Next

C6 prepares `submissions/main_v2.py`. Before any upload, ask for an explicit user decision. If entry/deadline safety requires a submission before v2 is ready, use the already validated v1b artifact rather than rushing v2.

## Key Learnings

1. v2 clears Fallow Finn and Wheat Walter but remains at tier 1 because it does not clear Rotation Rosa.
2. The next material lever is scale and endgame liquidation, not an unqualified claim of generalization.
3. v1b remains the validated fallback artifact if deadline safety requires an upload before v2 is ready.
