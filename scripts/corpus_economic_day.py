#!/usr/bin/env python
"""Day-by-day economic extraction for ONE Kaggle daily episode dataset.

Why this module exists
----------------------
The one-day economic event study needs a daily/event-time extractor that can be
unit-tested on the workstation but runs where the daily replay datasets are
mounted. Kaggle is the only place those datasets live, so the notebook builder
embeds this file verbatim as a cell and the tests import it directly: one
implementation of the guard and the extractor, two call sites.

Two hard invariants
-------------------
* EXACTLY ONE daily episode dataset per run. Zero or more than one means the
  run is misconfigured and must fail closed *before* any replay is parsed; the
  index dataset, engine tables and reference agents are not daily sources.
* The treatment timestamp is the OBSERVED ``farm.unlocked_quadrants`` 3 -> 4
  transition, not the ``BUY_LAND`` request, which can silently fail.

Replay semantics pinned here (established in
``kaggriculture.agents.replay_opponent``; ``ACTION_OFFSET == 1``)
------------------------------------------------------------------------
* ``steps[i][seat]["observation"]`` is the state an agent sees at turn ``i``.
* ``steps[i][seat]["action"]`` PRODUCED ``observation[i]``: it acted on the
  state at index ``i - 1``. So the ``BUY_LAND`` request that turns three
  quadrants into four sits at index ``i`` when ``observation[i]`` is the first
  to show four. Index 0 is a placeholder action and is never a request.
* A daily snapshot is the LAST observation of an in-game day, so the $4,000
  purchase that lands within the day is visible in that day's settled cash.

What is deliberately NOT measured
---------------------------------
A ``SELL`` order stores the units an agent *requested*, not the units the market
executed, and the replay does not expose the fills. Sold-unit accounting would
therefore be fabricated, so this module omits it entirely. Only ``farm.money``
is called cash; net cash movement is never called revenue or income.
"""

from __future__ import annotations

import argparse
import collections
import glob
import os
import re
import statistics

#: Engine turns per in-game day, used only when an observation lacks ``day``.
TURNS_PER_DAY = 24

#: Event-time window relative to the buyer's observed 3 -> 4 unlock day.
EVENT_LO, EVENT_HI = -4, 8

#: A mounted daily episode dataset is a directory whose name matches this.
DAILY_DATASET_RE = re.compile(r"^kaggriculture-episodes-(\d{4}-\d{2}-\d{2})$")

#: Boundary between embeddable library code and the local CLI. The notebook
#: builder cuts the source at this line so the cell never defines ``main`` and
#: never runs the CLI when a notebook executes with ``__name__ == "__main__"``.
CLI_MARKER = "# --- command-line entry point (not embedded in the notebook) ---"


class OneDayGuardError(RuntimeError):
    """Raised when a run is not bound to exactly one daily episode dataset."""


class MatchSkip(Exception):
    """A replay that cannot supply a bounded primary pair; its reason is kept."""


# --------------------------------------------------------------------------
# One-day guard
# --------------------------------------------------------------------------

def daily_dataset_date(name):
    """Return the ``YYYY-MM-DD`` of a daily dataset path/name, or ``None``.

    Only the basename is inspected, so both the flat Kaggle mount
    (``/kaggle/input/kaggriculture-episodes-2026-09-24``) and the nested one
    (``/kaggle/input/datasets/kaggle/kaggriculture-episodes-2026-09-24``) map to
    the same date. Index, engine and reference-agent names do not match.
    """
    base = os.path.basename(os.path.normpath(str(name)))
    match = DAILY_DATASET_RE.match(base)
    return match.group(1) if match else None


def daily_dataset_dates(names):
    """Map ``date -> sorted distinct matching source names``."""
    found = {}
    for name in names:
        date = daily_dataset_date(name)
        if date is not None:
            found.setdefault(date, set()).add(str(name))
    return {date: sorted(values) for date, values in found.items()}


def daily_dataset_sources(names):
    """Sorted matching daily source names, duplicates preserved.

    The guard counts RAW matches, not distinct dates: two mounts of the same day
    (or a duplicated ``dataset_sources`` entry) must fail closed, because the run
    would otherwise read the same day twice and call it one.
    """
    return sorted(str(name) for name in names if daily_dataset_date(name) is not None)


def require_exactly_one_day(names):
    """Fail closed unless exactly one daily episode dataset mount is present.

    Returns ``(date, source)``. Raises :class:`OneDayGuardError` for zero daily
    sources, for two or more distinct dates, and for two matching mounts of the
    SAME date. Non-daily sources are ignored on purpose, not treated as error.
    """
    sources = daily_dataset_sources(names)
    if not sources:
        raise OneDayGuardError(
            "no daily episode dataset mounted (expected kaggriculture-episodes-"
            "YYYY-MM-DD); refusing to guess")
    if len(sources) > 1:
        raise OneDayGuardError(
            "more than one daily episode dataset mounted: "
            + ", ".join(sources)
            + "; one run must analyse exactly one mount, even when the duplicate "
            "paths share a date")
    return daily_dataset_date(sources[0]), sources[0]


def discover_daily_dirs(root="/kaggle/input"):
    """Directories under ``root`` whose name looks like a daily dataset.

    Discovery only reads names: it never opens a replay, so it is safe to call
    before the full-parse pass.
    """
    seen = set()
    pattern = os.path.join(root, "**", "kaggriculture-episodes-*")
    for path in glob.glob(pattern, recursive=True):
        if os.path.isdir(path):
            seen.add(path)
    return sorted(seen)


def require_expected_day(discovered, expected):
    """Fail closed when the full-parse pass disagrees with the one-day guard.

    The Tier-2 pass re-derives the day from replay paths; if that set is not the
    single day the guard resolved, the run would analyse a different sample than
    the report claims, so it stops instead of continuing.
    """
    if list(discovered) != [expected]:
        raise OneDayGuardError(
            "tier-2 discovered days " + repr(list(discovered))
            + " but the one-day guard resolved " + repr(expected))
    return expected


def require_within_cap(total, cap, expected):
    """Fail closed rather than truncate the mounted one-day file list.

    With one day mounted, every replay in it is part of the sample; silently
    slicing at ``cap`` would shrink the day while the report still named it.
    """
    if total > cap:
        raise OneDayGuardError(
            f"{total:,} replays mounted for {expected} exceed the cap {cap:,}; "
            "raise the cap instead of truncating a single day")
    return total


# --------------------------------------------------------------------------
# Observation readers (observed state only)
# --------------------------------------------------------------------------

def _seat_state(steps, index, seat):
    if index < 0 or index >= len(steps):
        return None
    row = steps[index]
    if not isinstance(row, (list, tuple)) or seat >= len(row):
        return None
    state = row[seat]
    return state if isinstance(state, dict) else None


def _observation(steps, index, seat):
    state = _seat_state(steps, index, seat)
    if state is None:
        return None
    obs = state.get("observation")
    return obs if isinstance(obs, dict) else None


def _farm(steps, index, seat):
    """The farm of ``seat`` at ``index`` (indexed by ``observation["player"]``)."""
    obs = _observation(steps, index, seat)
    if obs is None:
        return {}
    farms = obs.get("farms")
    if not isinstance(farms, (list, tuple)):
        return {}
    player = obs.get("player")
    if not isinstance(player, int) or not (0 <= player < len(farms)):
        player = seat
    farm = farms[player] if 0 <= player < len(farms) else None
    return farm if isinstance(farm, dict) else {}


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    return float(value)


def _sum_counts(value):
    """Total units across a ``{product: units}`` dict or a list of such dicts."""
    if isinstance(value, dict):
        return sum(int(v) for v in value.values()
                   if isinstance(v, (int, float)) and not isinstance(v, bool))
    if isinstance(value, (list, tuple)):
        return sum(_sum_counts(item) for item in value)
    return 0


def _tile_counts(farm):
    """(planted cells, animal cells) over the farm's tile grid."""
    crops = animals = 0
    tiles = farm.get("tiles")
    if not isinstance(tiles, (list, tuple)):
        return crops, animals
    for row in tiles:
        if not isinstance(row, (list, tuple)):
            continue
        for cell in row:
            if not isinstance(cell, dict):
                continue
            if cell.get("animal"):
                animals += 1
            elif cell.get("kind") == "PLANT":
                crops += 1
    return crops, animals


def _quadrants(farm):
    unlocked = farm.get("unlocked_quadrants")
    return len(unlocked) if isinstance(unlocked, (list, tuple)) else 0


def _day_of(obs, index):
    if isinstance(obs, dict):
        day = obs.get("day")
        if isinstance(day, (int, float)) and not isinstance(day, bool):
            return int(day)
    return index // TURNS_PER_DAY


# --------------------------------------------------------------------------
# Observed snapshots, unlock and pair classification
# --------------------------------------------------------------------------

def observed_snapshot(steps, index, seat):
    """Observable economy of ``seat`` at one turn.

    No requested or executed market data enters here: ``SELL`` quantities are
    fills the replay does not expose, so they are not represented at all.
    """
    obs = _observation(steps, index, seat)
    farm = _farm(steps, index, seat)
    private = obs.get("private") if isinstance(obs, dict) else None
    if not isinstance(private, dict):
        private = {}
    crops, animals = _tile_counts(farm)
    hands = farm.get("hands")
    return {
        "day": _day_of(obs, index),
        "cash": _number(farm.get("money")),
        "quadrants": _quadrants(farm),
        "crop_tiles": crops,
        "animals": animals,
        "hands": len(hands) if isinstance(hands, (list, tuple)) else 0,
        "shed_units": _sum_counts(private.get("shed")),
        "carry_units": _sum_counts(private.get("inventories")),
    }


def daily_snapshots(steps, seat):
    """One settled row per in-game day: the LAST observation of that day.

    The day's final turn already contains any purchase made during the day, so
    a single observation is the honest daily snapshot. Change from the prior day
    is attached (``cash_change``), never averaged or interpolated.
    """
    last_index = {}
    for index in range(len(steps)):
        obs = _observation(steps, index, seat)
        if obs is None:
            continue
        last_index[_day_of(obs, index)] = index
    rows = []
    previous_cash = None
    for day in sorted(last_index):
        row = observed_snapshot(steps, last_index[day], seat)
        row["day"] = day
        row["cash_change"] = None if previous_cash is None else row["cash"] - previous_cash
        previous_cash = row["cash"]
        rows.append(row)
    return rows


def buy_land_indices(steps, seat):
    """Indices ``i`` where ``action[i]`` requests ``BUY_LAND``.

    Because ``action[i]`` produced ``observation[i]``, these are exactly the
    transitions ``observation[i-1] -> observation[i]`` the request could affect.
    Index 0 is the placeholder action and is never a request.
    """
    found = []
    for index in range(len(steps)):
        state = _seat_state(steps, index, seat)
        action = (state or {}).get("action")
        if not isinstance(action, dict):
            continue
        for order in action.get("market") or []:
            if isinstance(order, (list, tuple)) and order and order[0] == "BUY_LAND":
                found.append(index)
                break
    return found


def observed_unlock(steps, seat, target=4):
    """First observed ``(day, index)`` where the farm shows ``target`` quadrants.

    Returns ``(None, None)`` when the state never reaches ``target``. A
    ``BUY_LAND`` request with no such observed transition is deliberately not a
    treatment: the request is allowed to fail silently.
    """
    for index in range(len(steps)):
        if _quadrants(_farm(steps, index, seat)) >= target:
            return _day_of(_observation(steps, index, seat), index), index
    return None, None


def final_quadrants(steps, seat):
    """Last observed quadrant count for a seat (0 when no observation exists)."""
    last = 0
    for index in range(len(steps)):
        count = _quadrants(_farm(steps, index, seat))
        if count:
            last = count
    return last


def classify_pair(steps):
    """Classify one replay by its two final observed quadrant counts.

    The primary cohort is exactly one seat at four quadrants and its opponent at
    three. Everything else is a named, separate configuration.
    """
    quads = [final_quadrants(steps, 0), final_quadrants(steps, 1)]
    counts = sorted(quads)
    if counts == [3, 4]:
        label = "primary_one4_one3"
    elif counts == [4, 4]:
        label = "both4"
    elif counts == [3, 3]:
        label = "both3"
    elif counts[-1] == 4:
        label = "one4_other_not3"
    elif counts[-1] < 3:
        label = "both_under3"
    else:
        label = "neither_reached4"
    return label, quads


def extract_match(steps, teams=None):
    """Bounded per-match extraction, both seats of the SAME replay.

    Returns a dict with the pair class and, for the primary cohort only, the
    buyer's and opponent's daily rows, the observed unlock day and the matching
    ``BUY_LAND`` request. Non-primary matches return without any snapshot work,
    so the fold stays cheap.
    """
    if not isinstance(steps, (list, tuple)) or len(steps) < 2:
        raise MatchSkip("truncated: fewer than two steps")
    label, quads = classify_pair(steps)
    result = {
        "class": label,
        "quadrants": quads,
        "teams": [str(team) for team in (teams or [])],
        "buyer": None,
        "opponent": None,
    }
    if label != "primary_one4_one3":
        return result

    buyer = 0 if quads[0] == 4 else 1
    opponent = 1 - buyer
    buyer_rows = daily_snapshots(steps, buyer)
    opponent_rows = daily_snapshots(steps, opponent)
    if not buyer_rows or not opponent_rows:
        raise MatchSkip("primary pair without daily snapshots")
    unlock_day, unlock_index = observed_unlock(steps, buyer, 4)
    if unlock_day is None:
        raise MatchSkip("four quadrants at the end but no observed 3 -> 4 transition")

    requests = buy_land_indices(steps, buyer)
    prior = [index for index in requests if index <= unlock_index]
    request_gap = None if not prior else unlock_index - max(prior)
    result["buyer"] = {
        "seat": buyer,
        "rows": buyer_rows,
        "unlock_day": unlock_day,
        "unlock_index": unlock_index,
        "buy_land_indices": requests,
        "request_gap": request_gap,
    }
    result["opponent"] = {"seat": opponent, "rows": opponent_rows}
    return result


def pair_rows(match):
    """Day-aligned buyer-versus-opponent rows for one primary match."""
    buyer = match["buyer"]
    opponent = match["opponent"]
    opponent_by_day = {row["day"]: row for row in opponent["rows"]}
    rows = []
    for brow in buyer["rows"]:
        orow = opponent_by_day.get(brow["day"])
        if orow is None:
            continue
        rows.append({
            "day": brow["day"],
            "buyer_cash": brow["cash"],
            "opp_cash": orow["cash"],
            "gap": brow["cash"] - orow["cash"],
            "buyer_change": brow["cash_change"],
            "opp_change": orow["cash_change"],
            "buyer_quadrants": brow["quadrants"],
            "opp_quadrants": orow["quadrants"],
            "buyer_crop": brow["crop_tiles"],
            "opp_crop": orow["crop_tiles"],
            "buyer_animal": brow["animals"],
            "opp_animal": orow["animals"],
            "buyer_hands": brow["hands"],
            "opp_hands": orow["hands"],
            "buyer_shed": brow["shed_units"],
            "opp_shed": orow["shed_units"],
            "buyer_carry": brow["carry_units"],
            "opp_carry": orow["carry_units"],
        })
    return rows


# --------------------------------------------------------------------------
# Bounded aggregation
# --------------------------------------------------------------------------

def new_summary():
    """Accumulator for the primary cohort across one full-parse pass."""
    return {
        "parsed": 0,
        "primary": 0,
        "buyer_wins": 0,
        "classes": collections.Counter(),
        "skipped": collections.Counter(),
        "primary_teams": set(),
        "days": collections.defaultdict(list),
        "events": collections.defaultdict(list),
        "unlock_days": [],
        "final_gap": [],
        "request_ok": 0,
        "request_missing": 0,
    }


def fold_match(summary, match):
    """Fold one :func:`extract_match` result into ``summary``.

    Only the primary cohort feeds the daily/event tables; every class is counted
    so the report can say how many replays were excluded and why.
    """
    summary["parsed"] += 1
    summary["classes"][match["class"]] += 1
    if match["class"] != "primary_one4_one3" or match["buyer"] is None:
        return

    rows = pair_rows(match)
    if not rows:
        summary["skipped"]["primary pair lost day alignment"] += 1
        return

    summary["primary"] += 1
    # Only the primary cohort's teams belong in the headline: teams seen in the
    # excluded classes must not inflate the distinct-team denominator.
    for team in match.get("teams") or ():
        summary["primary_teams"].add(team)
    buyer = match["buyer"]
    unlock_day = buyer["unlock_day"]
    summary["unlock_days"].append(unlock_day)
    if buyer["request_gap"] is not None and buyer["request_gap"] <= 1:
        summary["request_ok"] += 1
    else:
        summary["request_missing"] += 1

    summary["final_gap"].append(rows[-1]["gap"])
    if rows[-1]["gap"] > 0:
        summary["buyer_wins"] += 1
    for row in rows:
        summary["days"][row["day"]].append(row)
        offset = row["day"] - unlock_day
        if EVENT_LO <= offset <= EVENT_HI:
            summary["events"][offset].append(row)


def _median(values):
    return statistics.median(values) if values else None


def _median_of(rows, key):
    return _median([row[key] for row in rows if row.get(key) is not None])


def _money(value):
    return "-" if value is None else f"{value:,.0f}"


def _signed(value):
    return "-" if value is None else f"{value:+,.0f}"


def _count(value):
    return "-" if value is None else f"{value:.0f}"


def _render_table(lines, headers, pairs):
    """Append a fixed-width median table; ``pairs`` is (label, cells) per row."""
    lines.append("".join(f"{head:>{width}}" for head, width in headers))
    for label, cells in pairs:
        body = "".join(f"{text:>{headers[index + 1][1]}}"
                       for index, text in enumerate(cells))
        lines.append(f"{label:>4}" + body)


def render_report(summary, date):
    """Return the bounded (< 20 kB) text report for one dataset date.

    Wording is deliberate: every column is cash or a count, denominators are
    printed, and the closing note states the analysis is descriptive.
    """
    lines = []
    add = lines.append
    add("=" * 100)
    add(f"DAILY ECONOMIC EVENT STUDY - dataset date {date} (one day per run)")
    add("=" * 100)
    add(f"replays parsed        : {summary['parsed']:,}")
    add(f"primary matches       : {summary['primary']:,} "
        "(one seat 4 quadrants, the other 3, same replay)")
    add(f"distinct teams         : {len(summary['primary_teams']):,} "
        "(primary cohort only; shared across replays, rows not independent people)")
    if summary["primary"]:
        share = summary["buyer_wins"] / summary["primary"]
        add(f"buyer win share        : {summary['buyer_wins']:,}/"
            f"{summary['primary']:,} = {share:.1%} "
            f" (denominator: {summary['primary']:,} primary matches)")
    else:
        add("buyer win share        : n/a (no primary match)")
    add("")
    add("classes of the two seats (final observed quadrants, same replay):")
    for label, count in sorted(summary["classes"].items(), key=lambda kv: -kv[1]):
        add(f"  {label:<22} {count:>7,}")
    if summary["skipped"]:
        add(f"skipped replays: {sum(summary['skipped'].values()):,}")
        for reason, count in summary["skipped"].most_common():
            add(f"  {count:>7,}  {reason}")
    else:
        add("skipped replays: 0")

    if not summary["primary"]:
        add("")
        add("No primary match on this day; nothing to table. The class counts above")
        add("are the honest result and no causal reading is possible from an empty cohort.")
        return "\n".join(lines)

    unlock_days = summary["unlock_days"]
    add("")
    add(f"observed unlock day (3 -> 4 quadrants): median {_median(unlock_days):.0f}"
        f" (min {min(unlock_days):.0f}, max {max(unlock_days):.0f},"
        f" n={len(unlock_days):,})")
    total_req = summary["request_ok"] + summary["request_missing"]
    add("  of those, a BUY_LAND request sits at or one step before the observed")
    add(f"  transition: {summary['request_ok']:,}/{total_req:,} = "
        f"{summary['request_ok'] / total_req:.0%}. The request alone is never the")
    add("  treatment; a silent BUY_LAND failure leaves the request but no 4-quadrant")
    add("  transition, so only the observed state counts.")

    cash_headers = [("day", 4), ("n", 6), ("buyer_cash", 12), ("opp_cash", 12),
                    ("gap", 10), ("buyer_chg", 11), ("opp_chg", 10)]
    resource_headers = [("day", 4), ("n", 6), ("b_q", 4), ("o_q", 4),
                        ("b_crop", 7), ("o_crop", 7), ("b_an", 5), ("o_an", 5),
                        ("b_hand", 7), ("o_hand", 7), ("b_shed", 7), ("o_shed", 7),
                        ("b_carry", 8), ("o_carry", 8)]

    def cash_cells(rows):
        return [
            str(len(rows)),
            _money(_median_of(rows, "buyer_cash")),
            _money(_median_of(rows, "opp_cash")),
            _signed(_median_of(rows, "gap")),
            _signed(_median_of(rows, "buyer_change")),
            _signed(_median_of(rows, "opp_change")),
        ]

    def resource_cells(rows):
        return [
            str(len(rows)),
            _count(_median_of(rows, "buyer_quadrants")),
            _count(_median_of(rows, "opp_quadrants")),
            _count(_median_of(rows, "buyer_crop")),
            _count(_median_of(rows, "opp_crop")),
            _count(_median_of(rows, "buyer_animal")),
            _count(_median_of(rows, "opp_animal")),
            _count(_median_of(rows, "buyer_hands")),
            _count(_median_of(rows, "opp_hands")),
            _count(_median_of(rows, "buyer_shed")),
            _count(_median_of(rows, "opp_shed")),
            _count(_median_of(rows, "buyer_carry")),
            _count(_median_of(rows, "opp_carry")),
        ]

    add("")
    add("--- IN-GAME DAY: cash snapshots (median; day = LAST observed turn) ---")
    add("  cash is farm.money. buyer and opponent are both seats of the same replay.")
    _render_table(lines, cash_headers,
                  [(str(day), cash_cells(summary["days"][day]))
                   for day in sorted(summary["days"])])

    add("")
    add("--- IN-GAME DAY: observable resources (median counts) ---")
    _render_table(lines, resource_headers,
                  [(str(day), resource_cells(summary["days"][day]))
                   for day in sorted(summary["days"])])

    add("")
    add("--- EVENT TIME: days relative to the buyer's observed unlock (median) ---")
    _render_table(lines, cash_headers,
                  [(f"{offset:+d}", cash_cells(summary["events"][offset]))
                   for offset in sorted(summary["events"])])

    add("")
    add("--- EVENT TIME: observable resources (median counts) ---")
    _render_table(lines, resource_headers,
                  [(f"{offset:+d}", resource_cells(summary["events"][offset]))
                   for offset in sorted(summary["events"])])

    pretrend_values = [row["gap"] for offset in range(EVENT_LO, 0)
                       for row in summary["events"].get(offset, [])]
    pretrend = _median(pretrend_values)
    dip_rows = summary["events"].get(0, [])
    dip = _median_of(dip_rows, "buyer_change")
    dip_mechanical = sum(1 for row in dip_rows
                         if row["buyer_change"] is not None and row["buyer_change"] <= -3500)
    gap_by_offset = {offset: _median_of(rows, "gap")
                     for offset, rows in summary["events"].items()}
    recovery = None
    for offset in range(1, EVENT_HI + 1):
        value = gap_by_offset.get(offset)
        if pretrend is not None and value is not None and value >= pretrend:
            recovery = offset
            break
    crossover = None
    for offset in range(EVENT_HI + 1):
        value = gap_by_offset.get(offset)
        if value is not None and value >= 0:
            crossover = offset
            break

    add("")
    add("--- HEADLINE (descriptive, not causal) ---")
    add(f"pretrend median gap, offsets {EVENT_LO}..-1 : {_signed(pretrend)}"
        + (f"  (n={len(pretrend_values):,} day-rows)" if pretrend_values else "  (no rows)"))
    add(f"purchase-day (offset 0) buyer cash change  : {_signed(dip)}"
        f"  median over n={len(dip_rows):,}; includes the mechanical -$4,000 land purchase")
    if dip_rows:
        add(f"  purchase-day rows at or below -$3,500    : {dip_mechanical:,}/"
            f"{len(dip_rows):,} = {dip_mechanical / len(dip_rows):.0%}")
    add("recovery (first offset > 0 with median gap >= pretrend): "
        + ("not within +" + str(EVENT_HI) if recovery is None else f"offset +{recovery}"))
    add("crossover (first offset >= 0 with median gap >= 0)     : "
        + ("not within +" + str(EVENT_HI) if crossover is None else f"offset +{crossover}"))
    add("final median gap (last day of each match)             : "
        f"{_signed(_median(summary['final_gap']))}")
    add("")
    add("Denominators: every table row prints its own n; primary matches above are")
    add("the only rows in the day and event tables. Cash is farm.money, not revenue;")
    add("requested SELL units are not executed fills and are omitted on purpose.")
    add("A within-game control removes shared shops and market, not policy, skill or")
    add("selection; repeated teams make rows non-independent. One date per run.")
    return "\n".join(lines)


# --- command-line entry point (not embedded in the notebook) ---

def main(argv=None):
    """Local guard-only CLI: validate a directory tree mounts exactly one day.

    The full analysis needs a Kaggle full-parse pass, so this stays read-only and
    cheap: it lists daily dataset directories and applies the same guard the
    notebook applies first.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dir", default="/kaggle/input",
                        help="root to scan for kaggriculture-episodes-YYYY-MM-DD dirs")
    args = parser.parse_args(argv)
    found = discover_daily_dirs(args.dir)
    date, source = require_exactly_one_day(found)
    print(f"one-day guard OK: date={date} source={source}")


if __name__ == "__main__":
    main()
