# Kaggriculture Operations & Architecture Runbook

This document serves as the single source of truth for repository structure, credentials, workflows, agent architecture, and session onboarding. Any developer or AI pair-programmer entering this project must consult this runbook to ensure continuity.

---

## 1. Mission & System Overview

- **Competition**: [Kaggle Kaggriculture Simulation Challenge](https://www.kaggle.com/competitions/kaggriculture).
- **Goal**: Advance standing into the Top 10 ladder, targeting sustained scores of **$140,000 – $170,000+** per match.
- **Active Git Branch**: `feat/baseline-v9-1-textile-pivot`
- **Active Kaggle Submissions**:
  1. **Primary**: `56501811` (`main_v9_1.py`, `baseline_v9_1`) — Early Textile Pivot + JIT Melon buffer.
  2. **Secondary**: `56496235` (`main_v9.py`, `baseline_v9`) — Hybrid CentralizedDispatcher + JIT Melon buffer.
- **Kaggle Constraint**: Kaggle Simulations permits exactly **2 active submissions per user** on the leaderboard. Older submissions (`baseline_v8_1`, `baseline_v8`, `baseline_v7`) are automatically deactivated and frozen when newer models are submitted.

---

## 2. Access, Credentials & Environment

### Python Environment
- Virtual environment: `.venv/` (Python 3.14).
- Key packages installed: `kaggle`, `kaggle-environments==1.32.7` (installed `--no-deps` to avoid broken pygame display issues in headless linux), `pettingzoo`, `numpy`, `scipy`, `pytest`.

### Kaggle API & Credentials
- Authentication file: `~/.kaggle/access_token` (or `~/.kaggle/kaggle.json`).
- Verify CLI access anytime with:
  ```bash
  .venv/bin/kaggle competitions submissions -c kaggriculture
  ```
- **Daily Quota**: 5 submissions per 24 hours. The quota resets daily at **21:00 ART (00:00 UTC)**.

### Engram Persistent Memory
- CLI binary path: `/home/linuxbrew/.linuxbrew/bin/engram`
- Canonical Project Name: `kaggriculture` (auto-detected from directory or specified via `--project kaggriculture`).
- Key Engram commands:
  ```bash
  # View recent project memory & session history
  engram context --project kaggriculture

  # Save critical findings / decisions / bugfixes
  engram save "<title>" "<content>" --type <type> --project kaggriculture --topic <topic_key>

  # Search memory
  engram search "<query>" --project kaggriculture
  ```

---

## 3. Repository Architecture & Directory Map

```
kaggriculture/
├── docs/                      # Technical analysis, game rules, and architecture specs
│   ├── OPERATIONS.md          # This operational runbook
│   ├── README.md              # Official Kaggriculture game rules & mechanics reference
│   ├── v9_hybrid_architecture_spec.md # V9 CentralizedDispatcher specification
│   ├── v8_vs_v7_benchmark_report.md   # Tape pruning and livestock benchmark report
│   └── analisis/              # Decompiled competitor engines (e.g. Titan, fourth_quadrant)
├── scripts/                   # Operational and telemetry scripts
│   ├── benchmark.py           # Paired benchmark harness across N seeds (both seats)
│   ├── run_match.py           # Single 1v1 match runner with seed control
│   ├── monitor_all.py         # Unified monitor polling V9.1 and V9 telemetry
│   ├── monitor_v9_1.py        # Dedicated parser for submission 56501811 episodes
│   ├── monitor_v9.py          # Dedicated parser for submission 56496235 episodes
│   └── analyze_top10.py       # Replay inspection tool for meta strategies
├── src/kaggriculture/         # Modular Python package
│   ├── runner.py              # Kaggle environment episode execution
│   ├── harness.py             # Paired benchmark engine (eliminates Seat 0 bias)
│   ├── loading.py             # Dynamic module loader for single-file agents
│   └── agents/                # Versioned agent implementations
│       ├── baseline.py (v0)   # Initial wheat baseline
│       ├── baseline_v7.py     # Base compressed tape agent
│       ├── baseline_v8.py     # Tape with pruned toxic routes (MAIN + YARN only)
│       ├── baseline_v8_1.py   # V8 with unified Day 28+ dead stock liquidation
│       ├── baseline_v9.py     # Hybrid architecture with CentralizedDispatcher & JIT Melon
│       ├── baseline_v9_1.py   # Day 6 Early Textile Pivot conditional on Yarn Store
│       └── v9_roles.py        # Worker roles & bipartite task assignment
├── submissions/               # Self-contained, single-file bundles for Kaggle upload
│   ├── main_v0.py ... main_v8_1.py
│   ├── main_v9.py
│   └── main_v9_1.py           # Production bundle deployed to Kaggle
├── scratch/                   # Ephemeral data, downloaded replays, and scratch scripts
│   ├── replays_v9_1/          # Cached replay JSONs for V9.1
│   ├── replays_v9/            # Cached replay JSONs for V9
│   └── test_sheep_swap_150.py # Paired benchmark harness for the Day 6 textile pivot
└── tests/                     # Unit & smoke tests
    └── test_smoke.py          # Smoke tests verifying match runner and paired harness
```

---

## 4. Agent Evolution & Lineage

| Version | Architecture Style | Core Strategy / Breakthrough | Peak Ladder Score / Elo |
| :--- | :--- | :--- | :--- |
| **V0 – V3** | Reactive Heuristic | Single-farmer wheat, carrot/tomato rotation, weed clearing. | $20k – $40k / ~260 Elo |
| **V4 – V6** | Reactive + Multi-Hand | 3-quad expansion, labor hiring curve, melon crop cycles, front-run sell overlay. | $40k – $75k / ~730 Elo |
| **V7** | Compressed Tape (`_BLOB`) | 720-turn pre-computed action tape with conditional runtime branching (`DECISIONS`). | $92k / 1266.3 Elo |
| **V8** | Tape Route Pruning | Removed toxic `YARN_CARROT` and `MILK_GLUT` routes that caused livestock starvation on public ladder. | $98k / 1266.9 Elo |
| **V8.1** | Tape Dead Stock Liquidation | Added unified Day 28+ shed liquidation to clear unused produce into cash before step 720. | $96k / 1257.3 Elo |
| **V9** | Hybrid Dispatcher Engine | Replaced rigid tape replay with autonomous runtime coordinator: **JIT Melon seed buying** (2 seeds step 1, rest JIT), **CentralizedDispatcher** bipartite task matching, and **Feeder isolation**. | $147k peak / 1212.7 Elo |
| **V9.1** | Early Textile Pivot | **Day 6 (Step 150) Sheep Switch**: Checks if `unlocked_shops.count("YARN_STORE") >= 1` upon unlocking NE quadrant. If yes, builds sheep pasture immediately instead of waiting until Day 9. Day 6 sheep shear on Day 9 (+800 early liquidity). Safe fallback to pure dairy if 0 Yarn Stores open. | **$153,624 peak / 1252.3 Elo** |

---

## 5. Standard Operating Procedures (SOPs)

### A. Running Local Matches
Run a fast local head-to-head match:
```bash
.venv/bin/python scripts/run_match.py submissions/main_v9_1.py submissions/main_v9.py --seed 42
```

### B. Running Paired Benchmarks (MANDATORY before submission)
Kaggriculture has significant first-player advantage (Seat 0 market lockstep priority). **Never trust a single-seed or single-seat test.**
Use the paired benchmark harness (plays each seed in both seat configurations: $2 \times N$ games):
```bash
.venv/bin/python scripts/benchmark.py submissions/main_v9_1.py submissions/main_v9.py --seeds 10
```
- **Rule**: Candidate agent must demonstrate non-negative win rate, positive mean margin, and **0 animal starvations** across all tested seeds before deploying to Kaggle.

### C. Creating & Verifying a Submission Bundle
Kaggle requires submissions to be **standalone single files using Python standard library only**.
1. Verify no foreign dependencies or unresolved imports:
   ```bash
   .venv/bin/python -c "import submissions.main_v9_1 as m; print('agent callable:', hasattr(m, 'agent'))"
   ```
2. Run smoke tests:
   ```bash
   .venv/bin/python -m pytest tests/ -q
   ```

### D. Submitting to Kaggle
Submit to the competition ladder using the Kaggle CLI:
```bash
.venv/bin/kaggle competitions submit -c kaggriculture -f submissions/main_v9_1.py -m "baseline_v9_1: early textile pivot with JIT melon liquidity buffer"
```

### E. Telemetry & Ladder Monitoring
Monitor all active models (V9.1 and V9):
```bash
.venv/bin/python scripts/monitor_all.py
```
- A background scheduler is configured to run `scripts/monitor_all.py` on a 15-minute interval (`*/15 * * * *`).
- Monitor logs and downloaded replay files are cached in `scratch/replays_v9_1/` and `scratch/replays_v9/`.

---

## 6. Critical Game Mechanics & Engineering Discoveries

1. **Market Lockstep & Seat Priority**:
   - Quotes for sell orders in slot $i$ are calculated simultaneously, but transactions execute sequentially with Seat 0 taking precedence.
   - Front-running high-value sales (Melons, Wool, Milk) prevents market price decay caused by opponent sales.
2. **The Sheep Glut / Quadratic Price Penalty Trap**:
   - Wool market price drops quadratically when supply exceeds target equilibrium (`above_func: "sq"`, target 3.20).
   - If 0 `YARN_STORE` shops are rolled, heavy wool production crashes market value to $1/unit.
   - Therefore, textile expansion must **always be conditioned on at least 1 `YARN_STORE` being open**.
3. **Finite Crop Lifespans**:
   - Multi-yield crops (Tomato, Strawberry) are **not infinite**. `kaggriculture.py` enforces `max_yield = 4`. After 4 harvest cycles, plants decay into weeds.
4. **Labor Scaling Curve**:
   - Farm hand daily hiring fees scale via Fibonacci sequence:
     - Hand 10: $50/day ($430 cumulative)
     - Hand 11: $90/day ($520 cumulative)
     - Hand 12: $140/day ($660 cumulative)
   - Replay telemetry confirms Top 10 agents cap their workforce at **11 hands**; hiring Hand 12 is ROI negative.
5. **JIT Seed Purchasing**:
   - Never buy 12 Melon seeds ($960) on turn 1. Buying 2 seeds on turn 1 ($160) and the remaining seeds on turns 2..20 maintains a $400-$800 cash cushion to acquire livestock and feed without starvation risk.

---

## 7. Session Onboarding Checklist (For New Sessions)

When resuming or starting a new pair-programming session in this repository, execute these steps in order:

1. **Check Git Status & Branch**:
   ```bash
   git status
   git branch
   ```
   Ensure you are on `feat/baseline-v9-1-textile-pivot` with a clean working tree.
2. **Review Engram Context**:
   ```bash
   engram context --project kaggriculture
   ```
   Inspect recent architectural decisions, ladder milestones, and active constraints.
3. **Verify Kaggle Ladder Submissions**:
   ```bash
   .venv/bin/kaggle competitions submissions -c kaggriculture | head -n 10
   ```
   Confirm the state of active submissions (`56501811` and `56496235`).
4. **Run Telemetry Monitor**:
   ```bash
   .venv/bin/python scripts/monitor_all.py
   ```
   Review win rates, Elo progression, and examine narrow losses.
5. **Run Test Suite**:
   ```bash
   .venv/bin/pytest tests/ -q
   ```
   Ensure all local simulation harnesses pass.
