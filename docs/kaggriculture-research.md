# Kaggriculture — Research Report

**Status:** initial deep research, before any agent code.
**Sources:** official competition pages, rules mirror (arena.sonpham.net), Pilkwang Kim's Working Note Part 1 (2026-08-03), Kaggle engine reference datasets, deepeshumrao/kaggriculture-agent.

---

## 1. What the competition is

Kaggle simulation competition (Google × Kaggle "5-Day AI Agents Intensive: Vibe Coding" capstone).
Build an autonomous agent that plays a **two-player farming economy** and out-earns the opponent.

| Fact | Value |
| --- | --- |
| Prize pool | $50,000 |
| Scale | ~20k entrants, ~9.5k teams, ~18k submissions |
| Season | 30 days × 24 turns = **720 turns**, 1 action/sec budget |
| Start / entry deadline | 2026-07-29 / **2026-09-23** |
| Final submission | **2026-09-30** |
| Leaderboard convergence | ~2026-10-01 → 10-15 (Glicko-2, win-driven; margins don't score) |

**Win condition:** more banked coins after turn 720. Unsold inventory, land, animals, seeds — all worth **zero** at scoring.

## 2. Game mechanics (the rulebook)

### Turn structure
Each turn: validate → farm actions (simultaneous) → market queue (orders interleave across players, one unit at a time) → town demand consumption → refresh. Per turn you issue: 1 farmer action + 1 per hired hand + up to **10 ordered market actions**.

Critical: **malformed/impossible actions are silent no-ops.** This is the single most exploitable engine property (see §5).

### Farm
- 10×10 board, four 5×5 quadrants; only NW starts unlocked. Unlock rest for **1k → 2k → 4k** coins. The frontier program unlocks quadrants 2 and 3 on **days 7 and 10**, and *never* buys the 4th.
- Shed at the center (access from (4,4),(5,4),(4,5),(5,5), even on locked cells). Capacity **100 non-seed items; overflow is silently discarded** at day end. Seeds stored separately, uncapped.
- Workers spawn at the shed each morning; carried items auto-drop at day end.
- Locked tiles are walkable but not farmable (shortcut routing).

### Crops
| Crop | Seed | Base | First yield | Schedule | Max yield | Glut behavior |
| --- | --- | --- | --- | --- | --- | --- |
| Wheat | 10 | 25 | day 2 | once, peak day 4 | 6 fert / 4 | log curve — nearly glut-immune |
| Carrot | 20 | 35 | day 2 | once, peak day 3 | 4 / 3 | crashes fast |
| Tomato | 50 | 60 | day 8 | daily ×4 (days 8–11) | 4 yields | sqrt — moderate |
| Strawberry | 100 | 120 | day 10 | every 2 days ×4 | 4 yields | linear steep — crashes |
| Melon | 80 | 250 | day 10 | once | 6 | **sq — crashes hardest** |

- **Water every day.** A fresh seed already has one missed watering counted; skip watering on planting day → weed that night. Two consecutive missed checks → weed.
- **Fertilizer lasts 3 days**; one-time crops gain +1 yield/day in bonus window when watered, +2 fertilized. A serviced melon tile is a 6-unit, ~$1,000-gross tile, not 1 unit.
- Decay: after max lifespan, yield drops 1 every other turn → weed at zero. Waiting for better prices can erase the crop.

### Animals (recurring, wheat-fueled)
| Animal | Cost | Structure | Product | Base | First | Interval | Max held |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Goose | 300 | Coop | Egg | 50 | day 4 | daily | 4 |
| Cow | 400 | Pasture | Milk | 160 | day 8 | every 2d | 6 |
| Sheep | 500 | Pasture | Wool | 200 | day 6 | every 3d | 6 |

- Eat **wheat daily**; two consecutive unfed checks → animal escapes permanently.
- `CARE` banks +1 per fed+cared day, paid at next production, resets after. Unfed production day → base yield only, **entire care bank lost**. (Community analysis prices CARE as a **4.2× multiplier** on animal economics.)
- Each surviving animal exposes 1 fertilizer at day end (doesn't accumulate).
- Held product caps out; neglect wastes future output.

### Labor: the hostile Fibonacci market
The n-th hire of a day costs Fibonacci wage F_n. A 12-hand day costs ~376 total; the 13th hand alone costs 233. Hands vanish nightly. Frontier program: **12 hands from day 10 onward, 5.1% idle**; Pilkwang's 13-hand policy idled 17.9% — labor scheduling is *the* bottleneck lever.

### The shared dynamic market — the actual PvP
You **cannot touch the opponent's farm**. The only coupling is the shared price curve and town demand. ~95% solitaire optimization, ~5% market game — and the 5% decides games between equals.

- `price = base ± amplitude × shape(|inventory − 10,000|)`, per-product shape per side, floor of 1.
- Sells pay **pre-trade** price then raise inventory (large orders walk the price down against themselves). Buys quoted post-trade. Immediate buy-then-sell nets exactly zero — no arbitrage loop.
- Only **wheat and fertilizer** are buyable back.
- Orders interleave across players one unit at a time: **your order sequence changes both your price and the opponent's.**

### Town demand (the tide that lifts prices)
- Town center eats one of every non-fertilizer product per day.
- A random shop unlocks **every 3 days** (with replacement, ≤8 instances), each consuming on its own 4-turn schedule; single-product shops consume double.
- Shops: Bakery (egg+wheat), Pizza (milk+tomato+wheat), Brunch (egg+wheat+strawberry), Yarn (wool×2), Ice Cream (strawberry+milk+wheat), Pet Cafe (carrot×2), Smoothie (strawberry+milk), Farmers Market (4 crops).
- Town appetite ~doubles by day 10, ~quadruples by day 20. Wheat drifts $25 → mid-$50s on town demand alone. **Every seller is front-running the town's growing hunger.**

### Information
Public: both boards, money, worker positions, unlocked land/hires, market inventory/prices, shops, clock.
Private: your shed contents, seed counts, carried inventories.

## 3. The meta (state of the ladder, ~Aug 2026)

From Pilkwang Kim's working note — the most important strategic document available:

1. **The top of the ladder is verbatim replays.** A four-line agent that replays 720 recorded actions of one ~2,600-rated seat scores ~2,500, wrapped in small repair overlays (weed-at-step-636 dig, opponent fingerprinting, frozen sale-sorting modes).
2. **Why tapes work:** (a) near-solitaire game → the production program executes identically regardless of opponent; (b) silent no-ops make divergence harmless; (c) win-driven rating + inflating ladder rewards beating the mid-field; (d) the **clone flood**: one public program forked by ~60 teams — 95/104 fitted parameters near-constant across 781 winning seats, one plant-mix fingerprint (21 melon / 44 strawberry / 66 wheat) covers 87.8% of winners.
3. **The frontier program, decoded:** expand day 7 + day 10 (never Q4), 12 hands from day 10, grow your own feed wheat (66 plantings incl. a **day-25/26 replant wave** sold into the season-end peak at ~$56), roll melon sales mid-game, batch wool/strawberry late. The day-29 wheat decision alone decided a +$7,256 swing in the dissected frontier game.
4. **The $13,400 gap ledger** (Pilkwang vs frontier): wheat cycle 51% (grow feed, don't buy it), strawberry volume 16% (40 tiles by day 15), labor waste 14%, melon cadence 13%. Non-levers: wool realization, land timing, animal costs.
5. **The coupled-margin law:** in a shared market, every sale moves both banks. Deleting your own "waste" can *lose* games (it was propping prices the opponent pays). **Margins don't score; wins do.** Evaluate candidates with **paired benchmarks** (same opponent/seed/seat, 768+ pairs) — per-seed swings of ±20,000 make small samples fiction.
6. **What beat the tapes:** a **detector-gated parameter switch** — fingerprint the opponent in the first 4 days, flip one allocation parameter only when a known lineage is confirmed, provably byte-identical baseline otherwise (240/240 verification).
7. **Patch risk:** the obvious levers (fault invalid actions, nudge market constants, delay replay dataset, add interaction) would kill tapes overnight. Holding an *adaptive* policy that understands the frontier program is a hedge; holding a tape is fragile.

## 4. Available data & tooling

| Resource | What it gives us |
| --- | --- |
| `kaggle/kaggriculture-episodes-YYYY-MM-DD` (daily) | Complete action-level replays of top games (~787/day, ~20 GB expanded, 13M obs-action rows/day). Behavior-cloning goldmine. **Rating-ordered selection, not a census** — archive prominence ≠ ladder strength. |
| `dariushafshar/kaggriculture-engine-reference-tables` | 9 CSVs exposing engine internals: price_curve, crop_constants, animal_constants, farm hands, etc. |
| `dariushafshar/kaggriculture-simulated-episode-corpus` | 4,050 simulated episodes, daily_state + market tables (engine 1.32.6). |
| `dariushafshar/kaggriculture-agent-benchmark-18k` | 18,144 measured episodes. |
| `raykkretzschmar/kaggriculture-reference-agents` | 10 authored agents (tiers 0–9) with manifest: strategy, lesson, measured bank. Built-in sparring partners. |
| Competition Python kit | Official environment + submission contract. |

## 5. Reference architectures

**Pilkwang — Structured Economic Policy** (public notebook): hand-crafted, terminal-money-denominated. 3 invariants (irreversible losses > growth; field > market commitments; terminal feasibility check `t + 24·g_c + harvest + return < T`), 4-phase machine (bootstrap ≤d4 → compounding ≤d21 → realization → liquidation last 22 turns), crisis override. Jobs carry priority + dollar value; worker-job matching `S = b_priority + v − 8·d_manhattan`; inventory shadow prevents double-spending. Public score high-1200s, performance rating ~1550–1600; 1 win in 6 vs top-50.

**deepeshumrao — contract-first agent** (GitHub): rule-based `FarmBrain` (market first → nearest high-value tile: harvest-ready → water → weed → plant), deterministic local simulator reproducing the real obs/action contract, single-file stdlib submission with **parity test** (flattened file ≡ package on every observation), 66 tests. All unconfirmed constants isolated in one `[ASSUMPTION]`-tagged dataclass. Good engineering template, weak strategy (no opponent modeling, no hands).

**Replay tapes** (anonymous, top of ladder): 720-entry action trace + repair overlays. Dominant, fragile, and a fingerprintable target.

## 6. Strategy space for our project

Ordered by expected value per effort:

1. **Baseline ladder:** reference agents (tiers 0–9) as local sparring partners → structured rule-based policy with phase machine + job matcher (Pilkwang shape) → port the decoded frontier program parameters as *defaults*, not a tape.
2. **Non-negotiable infrastructure from day 1:** deterministic local simulator with the real contract, paired-benchmark harness (same seed/seat/opponent), zero-effect control agents, per-dollar revenue attribution ledger reconciled to money conservation. **No unpaired verdicts, ever.**
3. **Labor scheduler as the differentiator:** the frontier idles 5%; everyone else idles 15–18%. Closing that gap cashes every other lever.
4. **Market layer:** sell into opponent buys, never dump into steep curves (melon/wool/strawberry/milk batch discipline), time wheat sales into the day-28/29 peak, keep the milk-dump inefficiency (459 units at half base) as a documented exploit target.
5. **Adaptive edge:** opponent fingerprinting in the first 4 days + gated parameter switches, provably safe fallback (byte-identical baseline vs unrecognized opponents).
6. **RL/imitation later:** behavior-clone the frontier program from the daily episode corpus; learn sale timing; learned value function for job pricing. The scaffolding (trajectories, paired off-policy eval, frozen opponents) is deliberately RL-shaped.
7. **Hedge against patches:** never ship pure replay dependence; understanding transfers, tapes don't.

### Common traps (from the rules, will silently kill a naive bot)
Fresh seed dies unwatered · unsold goods score zero · shed overflow vanishes · >10 market orders dropped · wrong-tile actions no-op · animal escapes · care bank erased · production caps · premium glut to 1 coin · late expansion never repays.

## 7. Proposed next step

Set up the project skeleton: competition kit + local simulator + one baseline agent vs reference agents, with the paired-benchmark harness. Then iterate through the strategy ladder above. Research gate is done; the open product decision is **how closely to follow the Pilkwang architecture vs. building our own scheduler-first design**.
