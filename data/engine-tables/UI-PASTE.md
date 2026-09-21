# Browser descriptor queue — Kaggriculture Engine Reference Tables

`dariushafshar/kaggriculture-engine-reference-tables`

Kaggle's API drops per-file descriptions, so these are pasted into the dataset UI's
"Add file information" fields. One block per published file, in the order the README's
Files section documents them.

## `README.md`

**File description**

```text
Documentation of the dataset: why these tables exist, the source engine module they were read from, the methodology split between pure-function tables and simulated trace tables, the day-indexing convention, the caveats, and how to regenerate everything from a clean install.
```

## `price_curve.csv`

**File description**

```text
The full sale-price curve for all 9 tradeable products: the engine's own market_price(item, inventory) evaluated at every integer inventory level from 0 to 50,000, giving 450,009 rows of product, inventory and price. Price is the integer sale price at that inventory, floored at $1. The shared starting inventory for every product is 10,000, so the range covers both a near-empty and a heavily glutted market.
```

## `crop_constants.csv`

**File description**

```text
The engine's CROPS dictionary as a table, one row per crop, 5 rows, with each product's base market price joined in. Columns: crop, seed_cost, first_yield_day, max_yield_day, interval, max_yield, ongoing, market_base_price. ongoing=False crops yield once and are removed on harvest; ongoing=True crops keep producing on an interval until max_yield cumulative units are reached.
```

## `animal_constants.csv`

**File description**

```text
The engine's ANIMALS dictionary as a table, one row per animal, 3 rows, with the product's base market price joined in. Columns: animal, cost, structure, first_yield_day, interval, max_held, product, market_base_price. Imported directly from the engine module on 2026-08-07 rather than retyped from the rules text.
```

## `crop_growth_trace.csv`

**File description**

```text
Per crop, policy and in-game day snapshot of a single never-harvested plant, 122 rows, produced by actually playing one tiny diagnostic episode per combination through kaggle_environments.make("kaggriculture") with a purpose-built prober agent. Policies are watered_no_fertilizer, watered_fertilized and never_watered. Columns give tile_kind (PLANT or WEED), yield_units accumulated on the tile, fertilized_until_day (-1 when never fertilized) and watered_today; rows stop once the tile reaches WEED.
```

## `animal_production_trace.csv`

**File description**

```text
Per animal, policy and in-game day snapshot of a single never-harvested animal, 366 rows, from the same probe-episode method, with rows stopping once the animal escapes. Policies are fed_daily_cared, fed_daily_no_care, fed_alt_days_no_care and never_fed. Columns are deliberately split across the end-of-day refresh: fed_today, cared_today and fertilizer_available_today are read before that day's refresh tick, while yield_units_after_refresh, consecutive_unfed_after_refresh, pending_care_bonus_after_refresh and escaped are read immediately after it.
```

## `farm_hand_hire_cost.csv`

**File description**

```text
The Fibonacci farm-hand hire-cost sequence through the 20th hire of a single day, 20 rows, evaluated from the engine's own _fib() and _hire_cost() functions. Columns: hire_number_today, fib_index, fib_value, cost, cumulative_cost_today. Cost is farmHandCostMult times fib(hires already made that day), shown at the competition's default multiplier of 1.
```

## `town_shop_demand.csv`

**File description**

```text
Steady-state town demand for each shop and product pair once that source is active, 27 rows. Columns: source, name, product, units_per_tick, tick_interval_turns, units_per_day_if_unlocked, note. Shops consume every 4 turns, 6 ticks per in-game day; which shop unlocks on which day is randomised per episode, so this is the deterministic demand rate once a source is live, not an unlock calendar.
```

## `town_center_demand_schedule.csv`

**File description**

```text
The town centre's demand multiplier by in-game day, 3 rows of day_threshold and multiplier, where the highest threshold at or below the current day applies. The town centre consumes every 12 turns, twice per in-game day.
```

## `misc_game_constants.csv`

**File description**

```text
Eighteen constant and value rows for everything that does not warrant its own table: starting money, board size, turn and day counts, shed capacity, weed spawn chance, market order cap, land prices per quadrant, the shared market's starting inventory level, and the price floor. Read straight out of the engine's constants on 2026-08-07, under the competition's default configuration.
```

## `dataset-cover.png`

**File description**

```text
Cover image for the dataset: a two-tone title card reading "Kaggriculture Engine Reference Tables" over the subtitle "Static lookup tables for the simulation engine". Decorative only; no data is encoded in the image.
```
