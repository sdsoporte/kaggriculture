# Kaggriculture Engine Reference Tables

Nine small lookup tables that expose the `kaggriculture` competition engine's internal
economic and biological model as plain CSV, so you can plot the market's glut/scarcity
curve, look up a crop's exact growth schedule, or check the animal CARE-bonus interaction
without re-deriving any of it from the source or from trial and error against the
simulator.

**This is reference data, not competition replay data.** Nothing here is a competitor's
submission, notebook, or downloaded episode. Every number was computed on 2026-08-07 by
either (a) calling the real engine's own functions directly, or (b) running tiny
purpose-built diagnostic episodes through `kaggle_environments.make("kaggriculture")`
using original single-purpose "prober" agents written for this dataset (source included
below) — never a hand re-derivation of the rules. See **Methodology** for exactly which
tables come from which path.

## Why this exists

The competition ships a `README.md`/`AGENTS.md` describing the rules in prose, but if
you're writing or debugging an agent you usually want the *numbers* — "what's the sell
price at inventory 14,300", "on what exact day does an unfertilized strawberry plant
start decaying", "how much does the 11th farm hand cost today" — without re-running the
simulator yourself or mis-transcribing a formula from the rules text. These tables answer
that directly, computed once, checkable, and pinned to the engine version below.

## Source engine

`kaggle_environments/envs/kaggriculture/kaggriculture.py` as installed by
`pip install kaggle-environments` at the time of generation (2026-08-07). All constants
(`CROPS`, `ANIMALS`, `MARKET_PARAMS`, `SHOPS`, `TOWN_CENTER_*`, the Fibonacci hire-cost
sequence) and the `market_price()` function were imported directly from that module —
nothing below was retyped by hand into a spreadsheet.

## Methodology

**Pure-function tables** (`price_curve.csv`, `crop_constants.csv`, `animal_constants.csv`,
`farm_hand_hire_cost.csv`, `town_shop_demand.csv`, `town_center_demand_schedule.csv`,
`misc_game_constants.csv`) were produced by importing the engine's own constants dicts and
its `market_price()` / `_fib()` / `_hire_cost()` functions and evaluating them directly —
zero transcription risk, because the values are read from the same objects the real
simulator uses to score a submission.

**Simulated trace tables** (`crop_growth_trace.csv`, `animal_production_trace.csv`) are
different in kind: the day-to-day growth/production rules are not a pure function you can
just call — they only fire correctly inside the real turn-by-turn interpreter. So instead
of re-deriving the schedule by reading the source (the approach that produced a wrong,
since-corrected conclusion earlier in this campaign — see `CAMPAIGN.md` §9.5, "a PARITY
bug"), this dataset actually **plays** one tiny diagnostic episode per (crop or animal,
policy) combination through `kaggle_environments.make("kaggriculture").run(...)`, using a
purpose-built "prober" agent:

- The prober's farmer stands on tile `(4,4)` for the entire episode — this is both the
  farmer's spawn tile and one of the four shed-access tiles, so it can plant/place, water,
  feed, care, pick up, and fertilize without ever having to path-find or move.
- For crops: buy one seed, plant it, then follow a fixed watering/fertilizing policy.
  **The plant is never harvested**, so `yield_units` accumulates undisturbed up to its
  natural cap and you can see exactly when decay begins. This means these tables show the
  *schedule*, not the cumulative output of a harvest-every-cycle strategy — that number is
  strategy-dependent and is exactly what the companion dataset, **Kaggriculture Simulated
  Episode Corpus**, lets you measure from real agent play instead of a synthetic probe.
- For animals: build the free structure, buy and place one animal, then follow a fixed
  feed/care policy. The animal is never harvested either, for the same reason.
- State is read directly off the recorded `env.steps` trajectory after the run completes
  (never from a hand-rolled step loop — this campaign found that a manual loop desyncs
  from `env.run`'s real bookkeeping; see `CAMPAIGN.md` §9.1.2).
- Every trace was spot-checked against independently-documented facts from this campaign's
  own engine analysis before publishing (e.g. melon capping at 6 units by age 10 under
  watering alone and by age 8 fertilized; strawberry's four scheduled productions landing
  at ages 10/12/14/16; wheat reaching 4 units watered-only vs. 6 fertilized; an animal
  escaping on exactly the second consecutive unfed day; goose CARE bonus paying out on
  schedule). All matched exactly.

The two prober agents are simple reactive state machines (no learning, no search) written
specifically for this dataset. They are original code, not derived from any public
notebook or other competitor's agent.

### A note on "day" indexing

Turns are 0-indexed within a 24-turn day. For the pure per-day tables, `day` = N means the
tile/animal state **at the last turn of in-game day N**, i.e. after every action that day
but before that day's end-of-day refresh tick. For `animal_production_trace.csv` this
wasn't enough on its own — `fed_today`/`cared_today` reset to `False` during the
end-of-day refresh, so the columns are deliberately split: `fed_today`, `cared_today`, and
`fertilizer_available_today` are read from the *pre*-refresh instant (did today's actions
happen), while `yield_units_after_refresh`, `consecutive_unfed_after_refresh`,
`pending_care_bonus_after_refresh`, and `escaped` are read from the instant immediately
after that day's refresh tick (the outcome of today's actions).

## Files

### `price_curve.csv` (450,009 rows)
The full sale-price curve for all 9 products, `market_price(item, inventory)` evaluated at
every integer inventory level from 0 to 50,000 (the shared starting inventory for every
product is 10,000, so this comfortably covers both a near-empty market and a heavily
glutted one). Lets you plot or look up price at any inventory without re-implementing the
piecewise scarcity/glut formula.

| column | meaning |
|---|---|
| `product` | one of the 9 tradeable products |
| `inventory` | market inventory level for that product |
| `price` | integer sale price at that inventory (floor $1) |

### `crop_constants.csv` (5 rows) / `animal_constants.csv` (3 rows)
The raw `CROPS` / `ANIMALS` engine dicts as tables, plus each product's base market price
joined in for convenience.

`crop_constants.csv`: `crop, seed_cost, first_yield_day, max_yield_day, interval,
max_yield, ongoing, market_base_price`

`animal_constants.csv`: `animal, cost, structure, first_yield_day, interval, max_held,
product, market_base_price`

`ongoing=False` crops (wheat, carrot, melon) yield once and are removed on harvest;
`ongoing=True` crops (tomato, strawberry) keep producing on a schedule (`interval` days
apart, starting `first_yield_day`) until `max_yield` cumulative units have been produced,
and stay on the tile across harvests until then.

### `crop_growth_trace.csv` (122 rows) — simulated
Per (crop, policy, day) snapshot from the probe run described above. Policies:
`watered_no_fertilizer`, `watered_fertilized` (fertilizer re-applied opportunistically
whenever coverage has lapsed — this is a reference ceiling, not a cost-optimal schedule),
`never_watered`. Rows stop once the tile reaches `WEED` (nothing more to observe).

| column | meaning |
|---|---|
| `crop`, `policy`, `day` | trace key |
| `tile_kind` | `PLANT` or `WEED` (or `EMPTY`/`LOCKED`, not reached in these traces) |
| `yield_units` | harvestable units currently on the tile (never harvested in this probe) |
| `fertilized_until_day` | `-1` if never fertilized, else the last day fertilizer coverage is active |
| `watered_today` | whether the tile was watered on this day |

### `animal_production_trace.csv` (366 rows) — simulated
Per (animal, policy, day) snapshot. Policies: `fed_daily_cared`, `fed_daily_no_care`,
`fed_alt_days_no_care` (fed only on even in-game days), `never_fed`. Rows stop once the
animal escapes.

| column | meaning |
|---|---|
| `animal`, `policy`, `day` | trace key |
| `fed_today`, `cared_today` | whether FEED / CARE happened that day (pre-refresh) |
| `fertilizer_available_today` | whether a fertilizer was collectible that day (pre-refresh) |
| `yield_units_after_refresh` | held product units after that day's refresh (never harvested) |
| `consecutive_unfed_after_refresh` | 0, 1, or animal has escaped (missing row follows) |
| `pending_care_bonus_after_refresh` | banked CARE bonus to be paid on the next scheduled production |
| `escaped` | `True` the first day the animal is gone (structure remains, animal doesn't) |

### `farm_hand_hire_cost.csv` (20 rows)
The Fibonacci hire-cost sequence (`cost = farmHandCostMult * fib(hires_already_today)`,
default multiplier 1), through the 20th hire of a single day, with a running daily total.

`hire_number_today, fib_index, fib_value, cost, cumulative_cost_today`

### `town_shop_demand.csv` (27 rows) / `town_center_demand_schedule.csv` (3 rows)
Steady-state town demand once each source is active. Shops consume every 4 turns
(6 ticks/day); the town centre consumes every 12 turns (2 ticks/day) at a multiplier that
steps up by day (see the schedule file). **Which shop unlocks on which day is randomised
per episode** (one new shop every 3 days, uniformly among those not yet unlocked, seeded
off the episode seed) — this table gives the deterministic *rate* each source demands at
once it's live, not a fixed unlock calendar.

`town_shop_demand.csv`: `source, name, product, units_per_tick, tick_interval_turns,
units_per_day_if_unlocked, note`

`town_center_demand_schedule.csv`: `day_threshold, multiplier` (highest threshold wins;
e.g. day 23 uses the day_threshold=20 row, multiplier 4).

### `misc_game_constants.csv` (18 rows)
Everything else that doesn't warrant its own table: starting money, board size, turn/day
counts, shed capacity, weed spawn chance, market order cap, land prices per quadrant, the
shared market's starting inventory level, and the price floor. `constant, value`.

## Caveats and limitations

- All configuration-dependent numbers use the competition's **default** configuration
  (`episodeSteps=720`, `turnsPerDay=24`, `farmHandCostMult=1`, `weedSpawnChance=0.005`,
  etc.). A host that overrides these in `configuration` would see different numbers; the
  defaults are what the live competition uses.
- The growth/production traces show a **never-harvest** ceiling so the natural schedule is
  visible without a harvest event resetting `yield_units` to 0. They are not a forecast of
  realised season output under any particular playing strategy — for that, see the
  companion dataset's per-day `shed_*` columns from real agent play.
- `watered_fertilized` reapplies fertilizer greedily (whenever coverage lapses), which
  uses more fertilizer than a cost-minimal schedule would. It demonstrates the mechanic's
  ceiling, not an optimised policy.
- `price_curve.csv` is a pure function of a *synthetic* inventory value — it does not
  reflect how quickly real play moves inventory away from 10,000; pair it with the
  companion corpus's `market.csv` to see realistic inventory trajectories.

## Reproducing this dataset

`scripts/gen_reference_tables.py` in the project repository generates every file here from
a clean `kaggle-environments` install. It takes well under a minute to run.
