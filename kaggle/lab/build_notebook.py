#!/usr/bin/env python
"""Generate the Kaggle notebook for corpus ingestion and market analysis.

Why a generator: the .ipynb must be valid nbformat JSON, and hand-editing that
JSON is how you get a broken kernel. Here the cells live as readable Python
strings and the notebook is emitted with json.dump, so it is always well-formed
and reviewable in a diff.

Three hard-won rules are baked in:
  * never assume the input layout - Kaggle mounts other people's datasets under
    /kaggle/input/datasets/<owner>/<slug>/ and one's own flat. Every lookup is
    recursive.
  * ingest in TIERS. Parsing 1.19 TB of replays is the wrong first move: the
    manifests answer population questions in kilobytes, and a prefix read of
    ~8 kB per replay answers team/seed questions for ~300 MB. Full parsing is
    only for the episodes actually selected.
  * price marginal units on the published curve, never at a flat or realised
    average. That confusion produced two wrong conclusions in this project.
No triple quotes inside the cell sources, or they would end the builder strings.

Usage:
    python kaggle/lab/build_notebook.py            # writes kaggriculture-lab.ipynb
"""

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "kaggriculture-lab.ipynb"


def md(text):
    return {"cell_type": "markdown", "metadata": {}, "source": text.splitlines(keepends=True)}


def code(text):
    return {"cell_type": "code", "execution_count": None, "metadata": {},
            "outputs": [], "source": text.splitlines(keepends=True)}


INTRO = """# Kaggriculture Lab - corpus ingestion at scale

Analysis workbench for the Kaggriculture competition, running where the corpus lives.

**The scale argument.** The league is published as one dataset per day, ~21.5 GB
each; the index manifest lists **56 days, 38,544 episodes, 1.19 TB**. A laptop
holds one day. Here the datasets are mounted next to the compute, so a session can
span many days with no download and no local disk pressure.

**How to ingest that much without drowning.** Full-parsing 1.19 TB of replays is
the wrong first move. This notebook works in tiers:

| tier | what it reads | cost | answers |
| --- | --- | --- | --- |
| 0 | every `manifest.csv` | kilobytes | counts, score bands, sizes per day |
| 1 | the first ~8 kB of every replay | ~300 MB total | team names, seeds, per-team history |
| 2 | full replay JSON | ~35 MB per episode | behaviour: roster, market, revenue, timing |

**On the GPU, honestly:** parsing JSON is CPU- and I/O-bound, and Kaggle's own
docs say pandas/sklearn do not benefit from a GPU. Worse, GPU sessions have
historically come with *fewer* cores and *less* RAM than CPU ones, which is the
opposite of what a bulk-ingest job wants. Cell 1 prints the resources the session
actually has, so the choice is made from evidence instead of a habit. The GPU earns
its keep only for a genuinely numeric job (a market surrogate trained on the
trajectories), which is a separate project.

**Which conclusions are safe.** The corpus is published as "top episode replays"
and its own manifest shows the scores clustered around 3000, while our submissions
sit near 1200: it is a different rating band, so it supports WITHIN-GAME
comparisons (both seats of one match) and engine/market mechanics, but not
comparisons of our play against theirs."""

RESOURCES = """import collections
import csv
import glob
import json
import os
import re
import sys
import time

print("python", sys.version.split()[0])
print(f"cpu_count          : {os.cpu_count()}")
try:
    with open("/proc/meminfo") as handle:
        for line in handle:
            if line.startswith(("MemTotal", "MemAvailable")):
                print(f"{line.split(':')[0]:<19}: {int(line.split()[1]) / 1e6:.1f} GB")
except OSError:
    pass
for cmd in ("nvidia-smi --query-gpu=name,memory.total --format=csv,noheader",):
    try:
        import subprocess
        print("gpu                :", subprocess.run(cmd.split(), capture_output=True,
              text=True, timeout=20).stdout.strip() or "none")
    except Exception as exc:
        print("gpu                : unavailable", type(exc).__name__)

# Discover the layout instead of assuming it: the first version of this notebook
# globbed /kaggle/input/<slug>/ and silently found nothing.
def find_all(name):
    return sorted(glob.glob(f"/kaggle/input/**/{name}", recursive=True))


manifests = find_all("manifest.csv")
episode_files = find_all("*.json")
print(f"\\nmanifests found   : {len(manifests)}")
print(f"episode files     : {len(episode_files):,}")
for path in manifests[:6]:
    print(f"  {path}  ({os.path.getsize(path) / 1e3:.0f} kB)")"""

TIER0 = """episodes = []      # per-episode rows, from every daily dataset's manifest
index_rows = []    # the index dataset's list of daily datasets

for manifest in manifests:
    source = os.path.basename(os.path.dirname(manifest))
    try:
        with open(manifest) as handle:
            for row in csv.DictReader(handle):
                if "episode_id" in row:
                    row["_source"] = source
                    episodes.append(row)
                elif "daily_dataset_slug" in row:
                    index_rows.append(row)
    except OSError as exc:
        print("skip", manifest, exc)

print(f"episodes with metadata : {len(episodes):,} across "
      f"{len({e['_source'] for e in episodes})} attached days")
if index_rows:
    print(f"league per the index   : {sum(int(r['episode_count']) for r in index_rows):,} "
          f"episodes, {sum(int(r['total_bytes']) for r in index_rows) / 1e12:.2f} TB, "
          f"{len(index_rows)} days")
    print(f"newest day listed      : {max(r['date'] for r in index_rows)}")

by_day = collections.defaultdict(list)
for row in episodes:
    if row.get("avg_score"):
        by_day[row["_source"]].append(float(row["avg_score"]))
if by_day:
    print(f"\\n{'day dataset':<42}{'n':>6}{'min':>7}{'median':>8}{'max':>7}")
    for day in sorted(by_day)[:12]:
        scores = sorted(by_day[day])
        print(f"{day:<42}{len(scores):>6}{scores[0]:>7.0f}"
              f"{scores[len(scores) // 2]:>8.0f}{scores[-1]:>7.0f}")"""

TIER1 = """import multiprocessing as mp

MAX_EPISODES = 6000   # keep a session comfortable; raise it once the cost is known

def scan_one(path):
    # Tier 1: read only the head and tail of a replay. info.TeamNames and seed sit
    # at one end or the other, so ~12 kB per file answers team questions for the
    # whole league instead of parsing 35 MB.
    try:
        with open(path, "rb") as handle:
            head = handle.read(4096)
            handle.seek(max(0, handle.seek(0, 2) - 8192))
            tail = handle.read(8192)
        blob = (head + b" " + tail).decode("utf-8", "replace")
        match = re.search(r'"TeamNames"\\s*:\\s*\\[(.*?)\\]', blob, re.S)
        if not match:
            return None
        names = json.loads("[" + match.group(1) + "]")   # decodes \\uXXXX escapes
        seed = re.search(r'"seed"\\s*:\\s*(-?\\d+)', blob)
        return (os.path.basename(path), names, int(seed.group(1)) if seed else None)
    except (OSError, ValueError):
        return None

sample = episode_files[:MAX_EPISODES]
started = time.time()
scanned = []
if sample:
    with mp.Pool(min(8, os.cpu_count() or 1)) as pool:
        for result in pool.imap_unordered(scan_one, sample, chunksize=32):
            if result:
                scanned.append(result)
    elapsed = time.time() - started
    rate = len(sample) / max(elapsed, 1e-9)
    print(f"scanned {len(sample):,} replays in {elapsed:.1f}s ({rate:,.0f}/s), "
          f"{len(scanned):,} parsed")
    print(f"projected for the whole league ({38454:,}): "
          f"{38454 / max(rate, 1e-9) / 60:.1f} minutes")
    print(f"bytes read: ~{len(sample) * 12 / 1e6:.0f} MB "
          f"(versus ~{len(sample) * 35 / 1e6:.0f} MB if fully parsed)")"""

TIER1_INDEX = """bands = {}
episode_day = {}
for row in episodes:
    episode_day[row["episode_id"]] = row["_source"]
    if row.get("avg_score"):
        bands[row["episode_id"]] = float(row["avg_score"])

team_days = collections.Counter()
team_games = collections.Counter()
team_scores = collections.defaultdict(list)
for name, names, seed in scanned:
    episode_id = name.split(".")[0]
    score = bands.get(episode_id)
    day = episode_day.get(episode_id, "?")   # dict lookup, not a linear scan per file
    for team in names:
        team_games[team] += 1
        team_days[(team, day)] += 1
        if score:
            team_scores[team].append(score)

team_day_count = collections.Counter(team for team, _ in team_days)

print(f"distinct teams seen: {len(team_games):,}")
print(f"\\n{'team':<30}{'games':>7}{'days':>6}{'mean band':>12}")
for team, count in team_games.most_common(15):
    scores = team_scores.get(team, [])
    print(f"{team[:30]:<30}{count:>7}{team_day_count[team]:>6}"
          f"{(sum(scores) / len(scores) if scores else 0):>12.0f}")

submissions = {"56513137": 1238.2, "56501811": 1194.0}
print(f"\\nour active submissions: {submissions}")
print("=> the corpus band is ~3000: comparing our games against it compares "
      "populations, not strategies. Only within-game comparisons are safe.")"""

CURVE = """curve = {}
curve_path = (find_all("price_curve.csv") or [None])[0]
if curve_path:
    with open(curve_path) as handle:
        for row in csv.DictReader(handle):
            curve.setdefault(row["product"], {})[int(row["inventory"])] = int(row["price"])
    levels = [9800, 9950, 10000, 10050, 10100, 10500, 12000]
    order = ["STRAWBERRY", "MILK", "WOOL", "MELON", "WHEAT", "EGG", "CARROT", "FERTILIZER"]
    print(f"{'product':<12}" + "".join(f"{lv:>9}" for lv in levels))
    for product in order:
        row = curve.get(product, {})
        print(f"{product:<12}" + "".join(f"{row.get(lv, 0):>9}" for lv in levels))
    print("\\nmarginal loss from +100 units of supply at I0:")
    for product in order:
        row = curve.get(product, {})
        base = row.get(10000)
        if base:
            print(f"  {product:<12} {base:>5} -> {row.get(10100, 0):>5}"
                  f"  ({100 * (row.get(10100, 0) - base) / base:+.0f}%)")
else:
    print("price_curve.csv not attached")"""

GROWTH = """growth_path = (find_all("crop_growth_trace.csv") or [None])[0]
if growth_path:
    by_crop = collections.defaultdict(lambda: collections.defaultdict(list))
    with open(growth_path) as handle:
        for row in csv.DictReader(handle):
            by_crop[row["crop"]][row["policy"]].append(float(row["yield_units"] or 0))

    def price_at(product, inventory):
        row = curve.get(product, {})
        return row.get(inventory, row.get(10000, 0))

    print(f"{'crop':<12}{'no fert':>9}{'fert':>7}{'delta':>7}"
          f"{'marginal +delta (I0->I0+delta)':>34}")
    for crop in sorted(by_crop):
        plain = by_crop[crop].get("watered_no_fertilizer")
        fert = by_crop[crop].get("watered_fertilized")
        if not plain or not fert:
            continue
        delta = int(sum(fert) - sum(plain))
        # THE POINT OF THIS CELL: value the extra units at the curve price AFTER
        # the extra supply lands, not at the pre-supply or base price. Valuing a
        # marginal unit at a flat price is the mistake that produced two wrong
        # conclusions in this project (the first version of this very cell made it).
        if delta > 0:
            entry = price_at(crop, 10000)
            exit_ = price_at(crop, 10000 + delta)
            value = sum(price_at(crop, 10000 + step) for step in range(1, delta + 1))
            print(f"{crop:<12}{sum(plain):>9.0f}{sum(fert):>7.0f}{delta:>+7d}"
                  f"   ${entry:>5} -> ${exit_:<5}  revenue ${value:>7,.0f}"
                  f"  vs ~$42 forgone")
        else:
            print(f"{crop:<12}{sum(plain):>9.0f}{sum(fert):>7.0f}{delta:>+7d}"
                  f"   no gain")
else:
    print("crop_growth_trace.csv not attached")"""

REFERENCE = """for name in ("crop_economics.csv", "head_to_head_games.csv",
             "baseline_league.csv", "agents_manifest.csv"):
    path = (find_all(name) or [None])[0]
    if not path:
        print(f"{name:<26} not attached")
        continue
    with open(path) as handle:
        rows = list(csv.DictReader(handle))
    print(f"{name:<26} {len(rows):>5} rows  columns: {list(rows[0].keys())[:8]}")

league_path = (find_all("baseline_league.csv") or [None])[0]
if league_path:
    with open(league_path) as handle:
        league = list(csv.DictReader(handle))
    ranked = collections.Counter()
    played = collections.Counter()
    for row in league:
        try:
            games = int(row["games"] or 0)
        except ValueError:
            continue
        # Count BOTH sides: an agent mostly sits in column b, and reading only the
        # agent_a columns printed five of the ten agents as 0 wins out of 50-odd.
        for wins_key, name_key in (("wins_a", "agent_a"), ("wins_b", "agent_b")):
            try:
                ranked[row[name_key]] += int(row[wins_key] or 0)
                played[row[name_key]] += games
            except ValueError:
                continue
    print("\\nreference league win rate (measured, this is the opponent ladder):")
    table = sorted(
        ((100 * wins / max(played[agent], 1), agent, wins, max(played[agent], 1))
         for agent, wins in ranked.items()),
        reverse=True,
    )
    for rate, agent, wins, total in table:
        print(f"  {agent:<20} {wins:>5}W / {total:>5}  = {rate:>5.1f}%")"""

OPTIMISER = """# How big can a fertilizer programme be before the last unit stops paying?
#
# Assumptions, stated because this is a first-order model: the market sits around
# I0 (our own sale-time inventories were 9,980-10,075, which supports it), we are
# the marginal supplier, and the opponent's supply is unchanged. The benchmark is
# what finally decides; this only sizes the programme.
demand_path = (find_all("town_shop_demand.csv") or [None])[0]
FERT_COST = 42.0        # what we forgo by not selling that fertilizer unit
growth_path = (find_all("crop_growth_trace.csv") or [None])[0]

if demand_path and growth_path and curve:
    P_UNLOCK = 1 - (7 / 8) ** 8     # 8 draws over 8 shop types, with replacement
    SHOP_DAYS = 27                  # shops unlock on days 3, 6, ..., 24
    # The engine's town centre takes ONE unit of each product per 24 turns, flat,
    # all season. town_center_demand_schedule.csv claims interval 12 with x2/x4
    # day multipliers - that table is STALE and would overstate the drain 2-8x.
    CENTRE_SEASON = 1.0 * 30

    shop_rate = collections.Counter()
    with open(demand_path) as handle:
        for row in csv.DictReader(handle):
            if row["source"] == "SHOP":
                shop_rate[row["product"]] += float(row["units_per_day_if_unlocked"]) * P_UNLOCK

    by_crop = collections.defaultdict(lambda: collections.defaultdict(list))
    with open(growth_path) as handle:
        for row in csv.DictReader(handle):
            by_crop[row["crop"]][row["policy"]].append(float(row["yield_units"] or 0))

    print(f"{'crop':<12}{'drain/szn':>10}{'per appl':>9}{'profitable u':>13}"
          f"{'appl':>6}{'revenue':>10}{'cost':>8}{'net':>9}")
    for crop in sorted(by_crop):
        row = curve.get(crop)
        plain = by_crop[crop].get("watered_no_fertilizer")
        fert = by_crop[crop].get("watered_fertilized")
        if not row or not plain or not fert:
            continue
        per_app = int(sum(fert) - sum(plain))
        if per_app <= 0:
            continue
        # The k-th extra unit meets the inventory it helps create, so it is worth
        # curve(I0 + k). Walk outward until a unit no longer beats the fertilizer.
        revenue = 0.0
        units = 0
        for step in range(1, 600):
            price = row.get(10000 + step)
            if price is None or price < FERT_COST:
                break
            revenue += price
            units = step
        apps = units // per_app
        drain = shop_rate[crop] * SHOP_DAYS + CENTRE_SEASON
        print(f"{crop:<12}{drain:>10.0f}{per_app:>9d}{units:>13d}{apps:>6d}"
              f"{revenue:>10,.0f}{apps * FERT_COST:>8,.0f}"
              f"{revenue - apps * FERT_COST:>9,.0f}")
    print()
    print("'profitable u' = extra units whose marginal price still beats the")
    print("fertilizer it costs; beyond that the curve has collapsed and the last")
    print("unit decides the profit, not the average.")
else:
    print("town_shop_demand.csv, crop_growth_trace.csv or price_curve.csv not attached")"""

NEXT = """## Next steps

1. Raise `MAX_EPISODES` once the tier-1 cost is known (the cell prints the rate and
   projects the whole league), and attach more daily datasets to widen the window.
2. Port `income_breakdown.py` / `sale_context.py` / `sell_timing.py` for tier 2, so
   behaviour analyses run over a large sample - always with the within-game control
   (both seats of the same match) beside every cross-team number.
3. Upload our own replay caches as a private dataset so our games live here too.
4. The GPU's only honest use here is a market surrogate trained on trajectories;
   everything above is CPU work."""


CELLS = [
    md(INTRO),
    code(RESOURCES),
    md("""## Tier 0 - the manifests (kilobytes, whole league)

Each daily dataset ships `manifest.csv` with per-episode metadata
(`episode_id, create_time, avg_score, min_score, sum_score, agent_count,
size_bytes`), and the index dataset lists the daily datasets. This answers the
population questions before touching a replay."""),
    code(TIER0),
    md("""## Tier 1 - a prefix scan of every replay (~300 MB, not 1.19 TB)

`info.TeamNames` and `seed` sit at one end or the other of the JSON, so reading
~12 kB per file yields a league-wide team index. This is the part that scales to
many days: cost grows with file count, not with 35 MB per episode."""),
    code(TIER1),
    code(TIER1_INDEX),
    md("""## The published market curve - where marginal value lives

Price is a deterministic function of inventory, and the curve is brutally steep
for shop-drained products while nearly flat for wheat and egg."""),
    code(CURVE),
    md("""## Fertilizer value, priced at the MARGINAL point

The published growth traces give accumulated yield per policy. The extra units must
be valued step by step along the curve, because the last unit of a batch is worth
far less than the first."""),
    code(GROWTH),
    md("""## How large a fertilizer programme can the market absorb?

Extra production only pays while the marginal unit still beats the fertilizer it
costs. The market's absorption comes from the shops (each type present with
probability 65.6%) plus the town centre, and the centre's real rate is one unit per
product per 24 turns - flat - not the x2/x4 schedule the stale CSV claims."""),
    code(OPTIMISER),
    md("""## The measured reference league (a diverse opponent ladder)"""),
    code(REFERENCE),
    md(NEXT),
]


def main():
    # Compile every code cell before writing, so a broken cell fails HERE instead
    # of inside a Kaggle run. A stray escape once produced an unterminated string
    # literal that only surfaced as a PapermillExecutionError after a full push.
    for index, cell in enumerate(CELLS):
        if cell["cell_type"] != "code":
            continue
        source = "".join(cell["source"])
        try:
            compile(source, f"<cell {index}>", "exec")
        except SyntaxError as exc:
            raise SystemExit(f"cell {index} does not compile: {exc}") from exc
        cell["id"] = f"cell-{index}"  # nbformat 5 wants ids; missing ones warn now, error later
    notebook = {
        "cells": CELLS,
        "metadata": {
            "kernelspec": {"display_name": "Python 3", "language": "python",
                           "name": "python3"},
            "language_info": {"name": "python", "version": "3.11"},
        },
        "nbformat": 4,
        "nbformat_minor": 5,
    }
    OUT.write_text(json.dumps(notebook, indent=1))
    print(f"wrote {OUT} ({len(CELLS)} cells)")


if __name__ == "__main__":
    main()
