#!/usr/bin/env python
"""Shop-window demand analysis for ONE Kaggle daily episode dataset.

Why this module exists
----------------------
The town shop system is PUBLIC information in every observation
(``obs.town.unlocked_shops``): a shop unlocks every 3 in-game days (with
replacement, capped at 8 live instances) and each instance consumes on its own
4-turn schedule. Before shop-awareness becomes a first-class parameter of the
policy agent, the corpus has to show whether selling into the shop calendar
actually separates winners from losers.

This module extracts that evidence in the SAME Tier-2 full-parse pass as the
daily economic study: one read of the replay feeds both reports. It is stdlib
only and embeddable exactly like ``scripts/corpus_economic_day.py`` - the
notebook builder cuts the source at :data:`SHOP_CLI_MARKER`, so the cell never
defines ``main`` and a notebook running with ``__name__ == "__main__"`` never
executes a CLI.

Namespace rule (why every top-level name is prefixed)
-----------------------------------------------------
The notebook embeds this module AND ``scripts/corpus_economic_day.py`` into the
SAME kernel namespace, the economic cell first, and the other cells keep writing
into that same namespace afterwards. A top-level name is therefore never a
private detail. Two crashes came from that, one per direction:

* v11: the shop module defined ``render_report``, which the later cell silently
  replaced, so the economic report was served the shop renderer's summary and
  raised ``KeyError: 'demand_table'``.
* v12: the Tier-2 loop header ``for _cut, (_day, _path) in enumerate(sample, 1)``
  left ``_day`` bound to the day STRING, overwriting this module's ``_day``
  formatter, so ``shop_render_report`` died calling a ``str``.

So EVERY top-level name this module defines carries a ``shop_``/``SHOP_``(or
``_shop_``) prefix, not only the names it happens to share with the economic
module today: a short helper like ``_day`` or ``holdings`` can be re-bound by any
cell that comes later. ``tests/test_lab_one_day.py`` pins both halves of that:
``test_shop_top_level_names_all_carry_the_shop_prefix`` rejects a new un-prefixed
name, ``test_embedded_extractors_share_no_top_level_name`` rejects a collision
with the economic cell, and
``test_renderers_survive_the_shared_notebook_namespace`` executes both cells with
every other cell's top-level bindings in place and calls both renderers, which is
the regression the v12 crash taught.

Engine semantics pinned here (``kaggriculture`` source + ``docs/engine-rule-sheet.md``)
------------------------------------------------------------------------------------
* ``SHOPS`` maps each shop name to the products it consumes.
* Every ``townShopSellInterval`` = 4 turns, each live instance consumes 1 unit of
  each of its products, DOUBLED (2 units) when the shop has a single product.
  So per instance per product per day: ``24 / 4 * (2 if single else 1)`` = 12 or 6.
* ``townShopUnlockInterval`` = 3 days; ``SHOP_MAX_INSTANCES`` = 8; draws are with
  replacement, so duplicates are legitimate and each copy consumes independently.
* The town centre drains 1 unit of every non-fertilizer product per 24 turns and
  is EXCLUDED here: this module measures SHOP demand only.

Replay semantics pinned here (established in ``kaggriculture.agents.replay_opponent``;
``ACTION_OFFSET == 1``)
------------------------------------------------------------------------------
* ``steps[i][seat]["action"]`` PRODUCED ``observation[i]``; it acted on state
  ``i - 1``. Index 0 is a placeholder action and is never a request.
* Executed sells are holdings DROPS (``private.shed`` + ``private.inventories``)
  on a turn that requests ``SELL`` for that product. The requested quantity is
  NOT a fill: the strong tape submits ``SELL <item> 10000`` as "sell everything",
  so reporting order quantities as sales overstates them by orders of magnitude.
  A requested-but-unfilled ``SELL`` leaves holdings unchanged and therefore
  counts zero units.
"""

from __future__ import annotations

import argparse
import collections
import csv
import glob
import os
import statistics

#: Engine turns per in-game day.
SHOP_TURNS_PER_DAY = 24

#: ``townShopSellInterval``: every instance consumes on this turn cadence.
SHOP_CONSUME_INTERVAL_TURNS = 4

#: ``townShopUnlockInterval``: one new (possibly duplicate) instance every N days.
SHOP_UNLOCK_INTERVAL_DAYS = 3

#: A shop whose product list has a single entry consumes double per tick.
SHOP_SINGLE_PRODUCT_MULTIPLIER = 2

#: ``SHOP_MAX_INSTANCES``: hard cap on live instances, not on variety.
SHOP_MAX_INSTANCES = 8

#: ``SHOPS`` from the engine, verbatim: which products each shop consumes.
SHOP_PRODUCTS = {
    "BAKERY": ("EGG", "WHEAT"),
    "PIZZA_SHOP": ("MILK", "TOMATO", "WHEAT"),
    "BRUNCH_SPOT": ("EGG", "WHEAT", "STRAWBERRY"),
    "YARN_STORE": ("WOOL",),
    "ICE_CREAM_SHOP": ("STRAWBERRY", "MILK", "WHEAT"),
    "PET_CAFE": ("CARROT",),
    "SMOOTHIE_SHOP": ("STRAWBERRY", "MILK"),
    "FARMERS_MARKET": ("WHEAT", "CARROT", "TOMATO", "STRAWBERRY"),
}

#: Products whose demand any shop can draw (union of :data:`SHOP_PRODUCTS`).
SHOP_CONSUMING_PRODUCTS = tuple(sorted(
    {product for products in SHOP_PRODUCTS.values() for product in products}))

#: Products no shop consumes: MELON and FERTILIZER are town-centre / market only.
SHOP_NON_PRODUCTS = ("MELON", "FERTILIZER")

#: The tape's one observed shop gate (route switch at step 226): YARN_STORE/wool.
SHOP_YARN_SHOP = "YARN_STORE"
SHOP_YARN_PRODUCT = "WOOL"

#: Mounted engine table that pins the per-product per-instance rates.
SHOP_DEMAND_FILENAME = "town_shop_demand.csv"

#: Local cache of the engine table (also discovered recursively under /kaggle/input).
SHOP_LOCAL_DEMAND_PATHS = ("data/engine-tables/town_shop_demand.csv",)

#: Boundary between embeddable library code and the local CLI. The notebook
#: builder cuts the source at this line so the cell never defines ``main``.
SHOP_CLI_MARKER = "# --- command-line entry point (not embedded in the notebook) ---"


class ShopSkip(Exception):
    """A replay that cannot supply a shop-window extraction; its reason is kept."""


# --------------------------------------------------------------------------
# Observation readers (observed state only)
# --------------------------------------------------------------------------

def _shop_state(steps, index, seat):
    if index < 0 or index >= len(steps):
        return None
    row = steps[index]
    if not isinstance(row, (list, tuple)) or seat >= len(row):
        return None
    state = row[seat]
    return state if isinstance(state, dict) else None


def _shop_observation(steps, index, seat):
    state = _shop_state(steps, index, seat)
    if state is None:
        return None
    obs = state.get("observation")
    return obs if isinstance(obs, dict) else None


def _shop_day_of(obs, index):
    if isinstance(obs, dict):
        day = obs.get("day")
        if isinstance(day, (int, float)) and not isinstance(day, bool):
            return int(day)
    return index // SHOP_TURNS_PER_DAY


def shop_observed_shops(steps, index, seat):
    """The ``town.unlocked_shops`` list at one turn, or ``None`` if unreadable.

    The list is returned as-is: duplicates are preserved because each copy is an
    independent consumer (the draw is with replacement).
    """
    obs = _shop_observation(steps, index, seat)
    if obs is None:
        return None
    town = obs.get("town")
    if not isinstance(town, dict):
        return None
    shops = town.get("unlocked_shops")
    if not isinstance(shops, (list, tuple)):
        return None
    return [str(shop) for shop in shops]


def shop_observed_last_day(steps, seat):
    """Last in-game day seen for ``seat``, or ``None`` when there is no state."""
    last = None
    for index in range(len(steps)):
        obs = _shop_observation(steps, index, seat)
        if obs is None:
            continue
        last = _shop_day_of(obs, index)
    return last


def shop_final_money(steps, seat):
    """Last observed ``farm.money`` for ``seat`` (0.0 when no observation)."""
    money = 0.0
    for index in range(len(steps)):
        obs = _shop_observation(steps, index, seat)
        if obs is None:
            continue
        farms = obs.get("farms")
        if not isinstance(farms, (list, tuple)):
            continue
        player = obs.get("player")
        if not isinstance(player, int) or not (0 <= player < len(farms)):
            player = seat
        farm = farms[player] if 0 <= player < len(farms) else None
        if isinstance(farm, dict):
            value = farm.get("money")
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                money = float(value)
    return money


# --------------------------------------------------------------------------
# Shop timeline
# --------------------------------------------------------------------------

def shop_timeline(steps, seat):
    """Observed per-instance shop unlock timeline for one seat.

    Iterates the ``town.unlocked_shops`` snapshots turn by turn and emits one
    entry per NEW observed instance: ``{"shop", "turn", "day"}``. Duplicates are
    preserved as separate entries with their own unlock turn, because two BAKERY
    instances unlock at different times and consume independently. A shop is
    never removed by the engine, so the instance count is monotone and a
    decrease is ignored rather than invented as a re-unlock.
    """
    instances = []
    position = collections.Counter()
    for index in range(len(steps)):
        shops = shop_observed_shops(steps, index, seat)
        if shops is None:
            continue
        counts = collections.Counter(shops)
        for shop, count in counts.items():
            while position[shop] < count:
                instances.append({
                    "shop": shop,
                    "turn": index,
                    "day": _shop_day_of(_shop_observation(steps, index, seat), index),
                })
                position[shop] += 1
    return instances


def shop_days_active(timeline, last_day):
    """Map ``product -> set of days`` with at least one consuming shop unlocked.

    A shop unlocked on day D stays live for the rest of the match, so an instance
    contributes its products to every day from D through ``last_day``.
    """
    active = collections.defaultdict(set)
    for instance in timeline:
        products = SHOP_PRODUCTS.get(instance["shop"], ())
        for day in range(instance["day"], last_day + 1):
            for product in products:
                active[product].add(day)
    return dict(active)


# --------------------------------------------------------------------------
# Expected shop demand (mapping joined with town_shop_demand.csv)
# --------------------------------------------------------------------------

def shop_rule_instance_day_units(products, interval=SHOP_CONSUME_INTERVAL_TURNS):
    """Per-instance per-product units per day from the engine rule alone.

    One unit every ``interval`` turns, doubled when the shop has a single
    product: ``24 / 4 * (2 if single else 1)`` -> 12 (YARN_STORE, PET_CAFE) or 6.
    This is the mapping-only fallback when the mounted table is absent, and the
    cross-check for the table's own ``units_per_day_if_unlocked`` column.
    """
    interval = max(1, int(interval))
    multiplier = SHOP_SINGLE_PRODUCT_MULTIPLIER if len(products) == 1 else 1
    return multiplier * (SHOP_TURNS_PER_DAY // interval)


def shop_discover_demand_path(root="/kaggle/input"):
    """First mounted ``town_shop_demand.csv`` (searched recursively), else local.

    Kaggle mounts datasets flat and nested, so the search is recursive. The local
    engine-table cache is only a workstation fallback; ``None`` means neither was
    found and the caller must use the mapping-only fallback, flagged.
    """
    for path in sorted(glob.glob(os.path.join(root, "**", SHOP_DEMAND_FILENAME),
                                 recursive=True)):
        if os.path.isfile(path):
            return path
    for path in SHOP_LOCAL_DEMAND_PATHS:
        if os.path.isfile(path):
            return path
    return None


def _shop_as_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def shop_load_demand_table(path=None):
    """Load the mounted shop demand table, or fall back to the engine rule.

    The table is a join key, not a blind oracle: each ``source == "SHOP"`` row is
    matched to the engine's shop-product mapping and its
    ``units_per_day_if_unlocked`` is compared against :func:`shop_rule_instance_day_units`
    (which applies the single-product doubling). ``TOWN_CENTER`` rows are counted
    and deliberately EXCLUDED from shop demand. A missing table, a missing row or
    an unreadable file all degrade to the mapping-only rule and are flagged in the
    returned dict rather than fabricating a number.
    """
    table = {
        "present": False,
        "path": None,
        "rates": {},
        "shop_rows": 0,
        "town_rows": 0,
        "mismatches": [],
        "missing": [],
        "unknown": [],
    }
    if path is None:
        path = shop_discover_demand_path()
    if path and os.path.isfile(path):
        try:
            with open(path) as handle:
                reader = csv.DictReader(handle)
                for row in reader:
                    source = (row.get("source") or "").strip().upper()
                    if source == "TOWN_CENTER":
                        table["town_rows"] += 1
                        continue
                    if source != "SHOP":
                        continue
                    shop = (row.get("name") or "").strip()
                    product = (row.get("product") or "").strip()
                    if shop not in SHOP_PRODUCTS or product not in SHOP_PRODUCTS[shop]:
                        table["unknown"].append((shop, product))
                        continue
                    table["rates"][(shop, product)] = _shop_as_float(
                        row.get("units_per_day_if_unlocked"))
                    table["shop_rows"] += 1
            table["present"] = True
            table["path"] = path
        except OSError:
            table["path"] = path
    # Fill every mapping entry the table did not supply (or the whole table when
    # it is absent) from the engine rule, and record what was filled.
    for shop, products in SHOP_PRODUCTS.items():
        for product in products:
            if (shop, product) in table["rates"]:
                expected = float(shop_rule_instance_day_units(products))
                actual = table["rates"][(shop, product)]
                if abs(actual - expected) > 1e-9:
                    table["mismatches"].append(
                        (shop, product, actual, expected))
            else:
                table["rates"][(shop, product)] = float(shop_rule_instance_day_units(products))
                if table["present"]:
                    table["missing"].append((shop, product))
    return table


def shop_expected_demand_by_day(timeline, last_day, rates):
    """``day -> Counter(product -> expected shop units/day)``.

    Sums, per day, every live instance's per-product rate: ``rates`` from the
    mounted table (or the mapping-only rule). Only days where a product has at
    least one consuming instance appear, so a mean over these rows is the mean
    demand on ACTIVE days rather than a figure diluted by zero-shop days.
    """
    added = collections.defaultdict(collections.Counter)
    for instance in timeline:
        added[instance["day"]][instance["shop"]] += 1
    demand = {}
    running = collections.Counter()
    for day in range(last_day + 1):
        running.update(added.get(day) or {})
        counts = collections.defaultdict(float)
        for shop, instances in running.items():
            for product in SHOP_PRODUCTS.get(shop, ()):
                rate = rates.get((shop, product))
                if rate is None:
                    rate = float(shop_rule_instance_day_units(SHOP_PRODUCTS[shop]))
                counts[product] += instances * rate
        if counts:
            demand[day] = counts
    return demand


# --------------------------------------------------------------------------
# Executed sold units via holdings-drop accounting
# --------------------------------------------------------------------------

def shop_holdings(obs):
    """Units of every item held: ``private.shed`` + every farmer inventory.

    The sum is the right lens because internal shed<->inventory moves cancel
    inside it; a drop therefore means units LEFT the farm.
    """
    private = obs.get("private") if isinstance(obs, dict) else None
    held = collections.Counter()
    if not isinstance(private, dict):
        return held
    shed = private.get("shed")
    if isinstance(shed, dict):
        for item, units in shed.items():
            if isinstance(units, (int, float)) and not isinstance(units, bool):
                held[str(item)] += int(units)
    inventories = private.get("inventories")
    if isinstance(inventories, (list, tuple)):
        for inv in inventories:
            if isinstance(inv, dict):
                for item, units in inv.items():
                    if isinstance(units, (int, float)) and not isinstance(units, bool):
                        held[str(item)] += int(units)
    return held


def shop_requested_sell_products(action):
    """Products with a ``SELL`` order in one turn's action (the request only)."""
    products = set()
    if not isinstance(action, dict):
        return products
    for order in action.get("market") or []:
        if (isinstance(order, (list, tuple)) and len(order) >= 2
                and order[0] == "SELL"):
            products.add(str(order[1]))
    return products


def shop_executed_sales(steps, seat):
    """Executed sold units per product per day, from holdings drops.

    For each consecutive observable pair ``(i-1, i)`` the holdings of ``seat`` are
    differenced; a POSITIVE drop is executed market volume. The turn's action
    (which produced ``observation[i]``) must request ``SELL`` for that product,
    which is what separates a real fill from two other drops: the town-centre /
    feed consumption and the end-of-day shed-overflow discard. A requested
    ``SELL`` with no drop is a request the market did not fill and counts zero.

    Returns ``{"by_day_product", "by_product", "dropped_unsold"}``. The unsold
    drops are kept as a diagnostic, never as sales.
    """
    by_day = collections.defaultdict(collections.Counter)
    by_product = collections.Counter()
    dropped_unsold = collections.Counter()
    for index in range(1, len(steps)):
        obs = _shop_observation(steps, index, seat)
        prev = _shop_observation(steps, index - 1, seat)
        if obs is None or prev is None:
            continue
        held_now = shop_holdings(obs)
        held_prev = shop_holdings(prev)
        state = _shop_state(steps, index, seat) or {}
        sells = shop_requested_sell_products(state.get("action"))
        day = _shop_day_of(obs, index)
        for product in set(held_prev) | set(held_now):
            drop = held_prev.get(product, 0) - held_now.get(product, 0)
            if drop <= 0:
                continue
            if product in sells:
                by_day[day][product] += drop
                by_product[product] += drop
            else:
                dropped_unsold[product] += drop
    return {
        "by_day_product": {day: collections.Counter(counts)
                           for day, counts in by_day.items()},
        "by_product": by_product,
        "dropped_unsold": dropped_unsold,
    }


# --------------------------------------------------------------------------
# Metrics
# --------------------------------------------------------------------------

def shop_window_summary(executed, active_days):
    """Shop-window share of a seat's executed units.

    For each shop-consuming product: units sold on days with at least one
    unlocked shop consuming it, over total executed units of that product. The
    aggregate is the same ratio over the summed numerator and denominator of all
    shop-consuming products that actually sold. A product with zero executed
    units is excluded from the denominator, not counted as a zero share.
    """
    by_day = executed["by_day_product"]
    per_product = {}
    shop_units = 0
    in_window = 0
    for product in SHOP_CONSUMING_PRODUCTS:
        total = executed["by_product"].get(product, 0)
        if total <= 0:
            continue
        days = set(active_days.get(product, ()))
        window_units = sum(counts.get(product, 0)
                           for day, counts in by_day.items() if day in days)
        per_product[product] = {
            "sold": total,
            "in_window": window_units,
            "share": window_units / total,
        }
        shop_units += total
        in_window += window_units
    non_shop_units = sum(executed["by_product"].get(product, 0)
                         for product in SHOP_NON_PRODUCTS)
    return {
        "per_product": per_product,
        "aggregate": {
            "sold": shop_units,
            "in_window": in_window,
            "share": (in_window / shop_units) if shop_units else None,
        },
        "shop_units": shop_units,
        "non_shop_units": non_shop_units,
    }


def shop_yarn_split(executed, active_days):
    """The tape's YARN gate: wool units on YARN_STORE-active days vs other days."""
    yarn_days = set(active_days.get(SHOP_YARN_PRODUCT, ()))
    on_units = off_units = 0
    for day, counts in executed["by_day_product"].items():
        units = counts.get(SHOP_YARN_PRODUCT, 0)
        if units <= 0:
            continue
        if day in yarn_days:
            on_units += units
        else:
            off_units += units
    total = on_units + off_units
    return {
        "has_yarn": bool(yarn_days),
        "on": on_units,
        "off": off_units,
        "share_on": (on_units / total) if total else None,
    }


def shop_extract_match(steps, teams=None):
    """Per-match shop extraction, both seats of the SAME replay.

    Every match qualifies (unlike the economic study's primary cohort): the
    comparison is winner against loser of one replay. Raises :class:`ShopSkip`
    for a truncated replay it cannot read.
    """
    if not isinstance(steps, (list, tuple)) or len(steps) < 2:
        raise ShopSkip("truncated: fewer than two steps")
    seats = {}
    for seat in (0, 1):
        timeline = shop_timeline(steps, seat)
        executed = shop_executed_sales(steps, seat)
        last_day = shop_observed_last_day(steps, seat)
        if last_day is None:
            last_day = 0
        active = shop_days_active(timeline, last_day)
        seats[seat] = {
            "seat": seat,
            "timeline": timeline,
            "last_day": last_day,
            "executed": executed,
            "active_days": active,
            "window": shop_window_summary(executed, active),
            "yarn": shop_yarn_split(executed, active),
            "money": shop_final_money(steps, seat),
        }
    money0 = seats[0]["money"]
    money1 = seats[1]["money"]
    winner, loser = (0, 1) if money0 >= money1 else (1, 0)
    return {
        "teams": [str(team) for team in (teams or [])],
        "seats": seats,
        "winner": winner,
        "loser": loser,
        "money": [money0, money1],
        "tie": money0 == money1,
    }


# --------------------------------------------------------------------------
# Bounded aggregation
# --------------------------------------------------------------------------

def shop_new_summary(demand_table=None):
    """Accumulator for the shop analysis across one full-parse pass.

    Only scalars and short per-seat lists survive: no replay or timeline object
    is retained, so a 6,000-match pass stays a few MB.
    """
    return {
        "parsed": 0,
        "matches": 0,
        "seats": 0,
        "ties": 0,
        "skipped": collections.Counter(),
        "demand_table": demand_table if demand_table is not None
        else shop_load_demand_table(),
        "matched_teams": set(),
        "final_instances": [],
        "shop_instance_counts": collections.Counter(),
        "shop_seats": collections.Counter(),
        "shop_unlock_days": collections.defaultdict(list),
        "duplicate_seats": 0,
        "expected_product": collections.defaultdict(lambda: [0.0, 0]),
        "sold_product": collections.defaultdict(list),
        "window_product": collections.defaultdict(
            lambda: {"sold": [], "share": []}),
        "shop_units": [],
        "non_shop_units": [],
        "window": {
            "both_defined": 0, "one_defined": 0, "none_defined": 0,
            "winner_more": 0, "loser_more": 0, "tie": 0,
            "winner_share": [], "loser_share": [],
            "winner_sold": [], "loser_sold": [],
        },
        "split": {
            "winner_shop": [], "loser_shop": [],
            "winner_non": [], "loser_non": [],
            "winner_more_shop": 0, "loser_more_shop": 0, "tie_shop": 0,
            "winner_more_non": 0, "loser_more_non": 0, "tie_non": 0,
        },
        "yarn": {
            "seats_with_yarn": 0, "seats_without": 0,
            "on_units": 0, "off_units": 0, "on_vals": [], "off_vals": [],
        },
    }


def _shop_fold_seat(summary, profile):
    summary["seats"] += 1
    timeline = profile["timeline"]
    summary["final_instances"].append(len(timeline))
    per_shop = collections.Counter(instance["shop"] for instance in timeline)
    summary["shop_instance_counts"].update(per_shop)
    if any(count >= 2 for count in per_shop.values()):
        summary["duplicate_seats"] += 1
    for shop in per_shop:
        summary["shop_seats"][shop] += 1
    for instance in timeline:
        summary["shop_unlock_days"][instance["shop"]].append(instance["day"])

    rates = summary["demand_table"]["rates"]
    for counts in shop_expected_demand_by_day(timeline, profile["last_day"], rates).values():
        for product, units in counts.items():
            cell = summary["expected_product"][product]
            cell[0] += units
            cell[1] += 1
    for product, sold in profile["executed"]["by_product"].items():
        summary["sold_product"][product].append(sold)

    window = profile["window"]
    summary["shop_units"].append(window["shop_units"])
    summary["non_shop_units"].append(window["non_shop_units"])
    for product, info in window["per_product"].items():
        summary["window_product"][product]["sold"].append(info["sold"])
        summary["window_product"][product]["share"].append(info["share"])

    yarn = profile["yarn"]
    summary["yarn"]["seats_with_yarn" if yarn["has_yarn"] else "seats_without"] += 1
    summary["yarn"]["on_units"] += yarn["on"]
    summary["yarn"]["off_units"] += yarn["off"]
    if yarn["on"] + yarn["off"] > 0:
        summary["yarn"]["on_vals"].append(yarn["on"])
        summary["yarn"]["off_vals"].append(yarn["off"])


def shop_fold_match(summary, match):
    """Fold one :func:`shop_extract_match` result into ``summary``.

    Both seats are counted in the per-seat tables. The winner/loser rows are
    WITHIN one replay and are skipped on a tied final bank, because a tie has no
    direction to report.
    """
    summary["parsed"] += 1
    seats = match["seats"]
    for team in match.get("teams") or ():
        summary["matched_teams"].add(team)
    for seat in (0, 1):
        profile = seats.get(seat)
        if profile is not None:
            _shop_fold_seat(summary, profile)
    winner = seats.get(match["winner"])
    loser = seats.get(match["loser"])
    if winner is None or loser is None:
        return
    if match.get("tie"):
        summary["ties"] += 1
        return
    summary["matches"] += 1

    window = summary["window"]
    winner_share = winner["window"]["aggregate"]["share"]
    loser_share = loser["window"]["aggregate"]["share"]
    if winner_share is not None and loser_share is not None:
        window["both_defined"] += 1
        window["winner_share"].append(winner_share)
        window["loser_share"].append(loser_share)
        window["winner_sold"].append(winner["window"]["aggregate"]["sold"])
        window["loser_sold"].append(loser["window"]["aggregate"]["sold"])
        if winner_share > loser_share:
            window["winner_more"] += 1
        elif loser_share > winner_share:
            window["loser_more"] += 1
        else:
            window["tie"] += 1
    elif winner_share is None and loser_share is None:
        window["none_defined"] += 1
    else:
        window["one_defined"] += 1

    split = summary["split"]
    winner_shop = winner["window"]["shop_units"]
    loser_shop = loser["window"]["shop_units"]
    winner_non = winner["window"]["non_shop_units"]
    loser_non = loser["window"]["non_shop_units"]
    split["winner_shop"].append(winner_shop)
    split["loser_shop"].append(loser_shop)
    split["winner_non"].append(winner_non)
    split["loser_non"].append(loser_non)
    if winner_shop > loser_shop:
        split["winner_more_shop"] += 1
    elif loser_shop > winner_shop:
        split["loser_more_shop"] += 1
    else:
        split["tie_shop"] += 1
    if winner_non > loser_non:
        split["winner_more_non"] += 1
    elif loser_non > winner_non:
        split["loser_more_non"] += 1
    else:
        split["tie_non"] += 1


# --------------------------------------------------------------------------
# Bounded render
# --------------------------------------------------------------------------

def _shop_median(values):
    return statistics.median(values) if values else None


def _shop_count(value):
    return "-" if value is None else f"{value:,.0f}"


def _shop_share(value):
    return "-" if value is None else f"{value:.1%}"


def _shop_day(value):
    return "-" if value is None else f"{value:.0f}"


def shop_render_report(summary, date):
    """Return the bounded (< 20 kB) text report for one dataset date.

    Every row prints the n behind it, every skip reason is listed, and the
    closing note states the within-game caveat plus the single-dataset-date
    boundary. Descriptive only: no causal claim.
    """
    lines = []
    add = lines.append
    table = summary["demand_table"]
    add("=" * 96)
    add(f"SHOP-WINDOW DEMAND ANALYSIS - dataset date {date} (one day per run)")
    add("=" * 96)
    add(f"replays parsed        : {summary['parsed']:,}")
    add(f"matches (both seats)  : {summary['matches']:,} "
        "(winner = higher final farm.money, same replay)")
    add(f"seats profiled        : {summary['seats']:,}")
    add(f"distinct teams        : {len(summary['matched_teams']):,} "
        "(shared across replays; rows are not independent people)")
    add(f"tied final bank       : {summary['ties']:,} "
        "(no direction; excluded from winner/loser rows)")
    if table.get("present"):
        add(f"shop demand table     : {table['path']} "
            f"({table['shop_rows']} SHOP rows joined)")
    else:
        add("shop demand table     : MISSING - mapping-only fallback (FLAGGED)")
    add(f"town_center rows      : {table['town_rows']:,} seen, "
        "EXCLUDED from shop demand on purpose (1 unit/product/24 turns)")
    if table["mismatches"]:
        add(f"table vs engine rule  : {len(table['mismatches'])} rate mismatch(es) "
            "FLAGGED; the table value is used")
    else:
        add("table vs engine rule  : every joined rate matches the "
            "single-product doubling rule (24/4 * 2 if single -> 12 else 6)")
    if table["missing"]:
        add(f"mapping rows the table omitted: {len(table['missing'])} "
            "filled from the engine rule (flagged)")
    if table["unknown"]:
        add(f"table rows outside the engine mapping: {len(table['unknown'])} ignored")
    if summary["skipped"]:
        add(f"skipped replays       : {sum(summary['skipped'].values()):,}")
        for reason, count in summary["skipped"].most_common():
            add(f"  {count:>7,}  {reason}")
    else:
        add("skipped replays       : 0")

    add("")
    add("--- OBSERVED SHOP TIMELINE (town.unlocked_shops each turn; duplicates kept) ---")
    add(f"{'shop':<18}{'instances':>10}{'seats':>7}{'first day med':>15}{'n':>7}")
    for shop in sorted(SHOP_PRODUCTS):
        days = summary["shop_unlock_days"].get(shop) or []
        add(f"{shop:<18}{summary['shop_instance_counts'].get(shop, 0):>10,}"
            f"{summary['shop_seats'].get(shop, 0):>7,}"
            f"{_shop_day(_shop_median(days)):>15}{len(days):>7,}")
    counts = summary["final_instances"]
    add(f"final live instances per seat: median {_shop_count(_shop_median(counts))}"
        f" (min {_shop_count(min(counts) if counts else None)},"
        f" max {_shop_count(max(counts) if counts else None)},"
        f" cap {SHOP_MAX_INSTANCES}, n={len(counts):,} seats)")
    add(f"seats with a duplicated instance: {summary['duplicate_seats']:,}/"
        f"{summary['seats']:,} (draw is with replacement; copies consume independently)")
    if not counts:
        add("No seat exposed a readable town.unlocked_shops list; nothing to table.")

    add("")
    add("--- EXPECTED SHOP DEMAND PER PRODUCT (mapping x town_shop_demand.csv) ---")
    add(f"{'product':<14}{'active seat-days':>18}{'mean units/day':>16}"
        f"{'instances rx':>14}")
    any_expected = False
    for product in SHOP_CONSUMING_PRODUCTS:
        cell = summary["expected_product"].get(product)
        if not cell or not cell[1]:
            continue
        any_expected = True
        add(f"{product:<14}{cell[1]:>18,}{cell[0] / cell[1]:>16.2f}"
            f"{'':>14}")
    if not any_expected:
        add("no shop-consuming product had an unlocked consumer; row omitted")
    add("'mean units/day' is over seat-days with >=1 consuming shop unlocked; the")
    add("town-centre baseline is excluded, so these are shop-only rates.")

    add("")
    add("--- EXECUTED UNITS AND SHOP-WINDOW SHARE (per seat, within-game) ---")
    add(f"{'product':<14}{'seats sold>0':>13}{'sold med':>10}{'share med':>11}"
        f"{'n share':>9}")
    for product in SHOP_CONSUMING_PRODUCTS + SHOP_NON_PRODUCTS:
        sold = summary["sold_product"].get(product) or []
        if not sold:
            continue
        info = summary["window_product"].get(product) or {"sold": [], "share": []}
        add(f"{product:<14}{len(sold):>13,}{_shop_count(_shop_median(sold)):>10}"
            f"{_shop_share(_shop_median(info['share'])):>11}{len(info['share']):>9,}")
    aggregate_shares = []
    for product in SHOP_CONSUMING_PRODUCTS:
        info = summary["window_product"].get(product)
        if info and info["sold"]:
            aggregate_shares.extend(info["share"])
    window = summary["window"]
    add(f"aggregate shop-consuming share per seat: median "
        f"{_shop_share(_shop_median(aggregate_shares))} (n={len(aggregate_shares):,} seats "
        "with >=1 unit sold of a shop-consuming product)")
    add(f"executed units per seat: shop-consuming products median "
        f"{_shop_count(_shop_median(summary['shop_units']))}, non-shop products median "
        f"{_shop_count(_shop_median(summary['non_shop_units']))} "
        f"(n={len(summary['shop_units']):,} seats)")

    add("")
    add("--- WITHIN-GAME: WINNER vs LOSER OF THE SAME REPLAY ---")
    add(f"matches with both window shares defined: {window['both_defined']:,}"
        f" (one defined {window['one_defined']:,}, none {window['none_defined']:,})")
    if window["both_defined"]:
        add(f"winner has the HIGHER shop-window share: {window['winner_more']:,}/"
            f"{window['both_defined']:,} = "
            f"{window['winner_more'] / window['both_defined']:.1%} "
            f"(loser higher {window['loser_more']:,}, ties {window['tie']:,})")
        add(f"median winner share {_shop_share(_shop_median(window['winner_share']))} vs loser "
            f"{_shop_share(_shop_median(window['loser_share']))}")
    else:
        add("no match has both seats' shop-window share defined; row omitted")
    split = summary["split"]
    if split["winner_shop"]:
        add(f"shop-product units: winner median {_shop_count(_shop_median(split['winner_shop']))}"
            f" vs loser {_shop_count(_shop_median(split['loser_shop']))}; winner has more "
            f"{split['winner_more_shop']:,}/{summary['matches']:,} = "
            f"{split['winner_more_shop'] / max(1, summary['matches']):.1%} "
            f"(n={len(split['winner_shop']):,} matches)")
        add(f"non-shop units   : winner median {_shop_count(_shop_median(split['winner_non']))}"
            f" vs loser {_shop_count(_shop_median(split['loser_non']))}; winner has more "
            f"{split['winner_more_non']:,}/{summary['matches']:,} = "
            f"{split['winner_more_non'] / max(1, summary['matches']):.1%}")
    else:
        add("no non-tied match to compare; row omitted")

    add("")
    add("--- YARN GATE (the tape's one observed shop route switch) ---")
    yarn = summary["yarn"]
    add(f"seats with >=1 YARN_STORE instance: {yarn['seats_with_yarn']:,}; "
        f"without: {yarn['seats_without']:,}")
    total_wool = yarn["on_units"] + yarn["off_units"]
    add(f"wool executed units: on YARN-active days {yarn['on_units']:,}, "
        f"other days {yarn['off_units']:,}, total {total_wool:,}")
    if total_wool:
        add(f"share of wool sold on YARN-active days: "
            f"{yarn['on_units'] / total_wool:.1%} "
            f"(denominator {total_wool:,} units; n="
            f"{len(yarn['on_vals']):,} seats with >0 wool sold)")
        add(f"per-seat median wool units: on-days "
            f"{_shop_count(_shop_median(yarn['on_vals']))}, off-days "
            f"{_shop_count(_shop_median(yarn['off_vals']))}")
    else:
        add("no executed wool sales observed; YARN split not computable")

    add("")
    add("Reading rules: every row prints its own n. Executed units are HOLDINGS DROPS")
    add("on a turn that requested SELL, never the requested quantity (SELL 10000 is a")
    add("request the market may not fill). Expected shop demand comes from the mounted")
    add("engine table joined to the shop-product mapping, with the single-product")
    add("doubling applied; the town-centre baseline is excluded. The within-game")
    add("control removes shared shops and market, not policy, skill or selection, so")
    add("this is DESCRIPTIVE: repeated teams make rows non-independent and one dataset")
    add(f"date ({date}) supports no cross-day claim.")
    return "\n".join(lines)


# --- command-line entry point (not embedded in the notebook) ---

def main(argv=None):
    """Local read-only CLI: resolve and print the shop demand table.

    The full analysis needs a Kaggle full-parse pass, so this stays cheap: it
    reports which table was found, whether the mapping-only fallback is active,
    and the resulting per-instance rates a reviewer can check by hand.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--path", default=None,
                        help="town_shop_demand.csv; default: discover or local cache")
    args = parser.parse_args(argv)
    table = shop_load_demand_table(args.path)
    if table["present"]:
        print(f"shop demand table OK: {table['path']} "
              f"({table['shop_rows']} SHOP rows, {table['town_rows']} TOWN_CENTER rows)")
    else:
        print("shop demand table MISSING - mapping-only fallback (flagged)")
    for (shop, product), rate in sorted(table["rates"].items()):
        print(f"  {shop:<16}{product:<12}{rate:g} units/instance/day")
    for shop, product, actual, expected in table["mismatches"]:
        print(f"  MISMATCH {shop}/{product}: table {actual:g} vs rule {expected:g}")


if __name__ == "__main__":
    main()
