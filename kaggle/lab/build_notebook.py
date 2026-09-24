#!/usr/bin/env python
"""Generate the Kaggle notebook for corpus/market analysis.

Why a generator: the .ipynb must be valid nbformat JSON, and hand-editing that
JSON is how you get a broken kernel. Here the cells live as readable Python
strings and the notebook is emitted with json.dump, so it is always well-formed
and reviewable in a diff.

Two hard-won rules are baked in:
  * never assume the input layout - the first version globbed
    /kaggle/input/<slug>/ and found nothing, because Kaggle groups attached
    datasets under /kaggle/input/datasets/. Every lookup is recursive.
  * no triple quotes inside the cell sources, or they would terminate the
    builder's own strings.

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


INTRO = """# Kaggriculture Lab

Analysis workbench for the Kaggriculture competition, running where the data lives.

**Why here instead of locally:** the episode corpus is published as daily datasets
(one per day, ~21.5 GB each). The index manifest lists the whole league as
**56 days / 38,544 episodes / 1.19 TB**, against the single day (608 episodes,
1.6%) that fits on a local disk. Attached as notebook inputs they are mounted and
read, so there is no download and no local disk pressure, and a session gets 12 h.

**Honest note about the GPU:** these analyses are JSON/pandas I/O-bound and the GPU
does not accelerate them (Kaggle's own docs say pandas and scikit-learn do not
benefit). It is enabled because it costs nothing here. Its legitimate use in this
project is a different, separate job: fitting a market surrogate from the
trajectories.

**Which conclusions are safe from this corpus:** it is published as "top episode
replays" and its own manifest shows `avg_score` clustered around 3000, while our
own submissions sit near 1200. So the corpus is a different rating band, not a
neutral sample: it is valid for WITHIN-GAME comparisons (both seats of the same
match) and for engine/market mechanics, but NOT for comparing our play against
theirs. That trap already produced one wrong conclusion in this project, so the
controls below are built in."""

DISCOVERY = """import collections
import csv
import glob
import os
import statistics
import sys

print("python", sys.version.split()[0])


def show_tree(root="/kaggle/input", max_depth=3):
    # Discover the layout instead of assuming it: Kaggle has changed where
    # attached datasets are mounted, and the first version of this notebook
    # found nothing because of exactly that.
    for dirpath, dirnames, filenames in os.walk(root):
        rel = os.path.relpath(dirpath, root)
        level = 0 if rel == "." else rel.count(os.sep) + 1
        if level > max_depth:
            dirnames[:] = []
            continue
        print("  " * level + os.path.basename(dirpath) + "/")
        for name in sorted(filenames)[:5]:
            size = os.path.getsize(os.path.join(dirpath, name))
            print("  " * (level + 1) + f"{name}  ({size / 1e6:.1f} MB)")
        if len(filenames) > 5:
            print("  " * (level + 1) + f"... {len(filenames) - 5} more files")


def find(name, limit=1):
    hits = sorted(glob.glob(f"/kaggle/input/**/{name}", recursive=True))
    return hits if limit > 1 else (hits[0] if hits else None)


show_tree()
print()
print("manifest.csv files under /kaggle/input:")
for path in sorted(glob.glob("/kaggle/input/**/manifest.csv", recursive=True)):
    print("  ", path, f"({os.path.getsize(path) / 1e3:.0f} kB)")"""

INDEX = """episodes = []      # per-episode rows from the daily datasets
datasets = []      # the index dataset's list of daily datasets

for manifest in sorted(glob.glob("/kaggle/input/**/manifest.csv", recursive=True)):
    source = os.path.basename(os.path.dirname(manifest))
    try:
        with open(manifest) as handle:
            for row in csv.DictReader(handle):
                if "episode_id" in row:
                    row["_source"] = source
                    episodes.append(row)
                elif "daily_dataset_slug" in row:
                    datasets.append(row)
    except OSError as exc:
        print("skip", manifest, exc)

print(f"episodes with metadata available : {len(episodes)}")
print(f"daily datasets listed in the index: {len(datasets)}")
print(f"daily datasets actually attached  : {len({e['_source'] for e in episodes})}")

if datasets:
    total = sum(int(d["episode_count"]) for d in datasets)
    size = sum(int(d["total_bytes"]) for d in datasets)
    print(f"full league per the index         : {total:,} episodes, {size / 1e12:.2f} TB")
    print(f"newest listed day                 : {max(d['date'] for d in datasets)}")"""

BANDS = """OUR_RATINGS = {"56513137": 1238.2, "56501811": 1194.0}

if episodes:
    scores = sorted(float(e["avg_score"]) for e in episodes if e.get("avg_score"))
    def q(p):
        return scores[int(p * (len(scores) - 1))]
    print("avg_score of the episodes we hold:")
    print(f"  n={len(scores)}  min {scores[0]:.0f}  p25 {q(.25):.0f}  median {q(.5):.0f}"
          f"  p75 {q(.75):.0f}  max {scores[-1]:.0f}")
    print(f"\\nour active submissions: {OUR_RATINGS}")
    lowest = min(OUR_RATINGS.values())
    print(f"corpus episodes at or below our lowest rating: "
          f"{sum(1 for s in scores if s < lowest)} / {len(scores)}")
    print("=> the corpus is a different rating band, so it cannot compare our play"
          " against theirs; only within-game comparisons are safe.")
else:
    print("no episode metadata found - check the attached datasets")"""

CURVE = """curve = {}
curve_path = find("price_curve.csv")
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

    # The decision the curve actually drives: the value of a MARGINAL unit, which
    # is nothing like the realised average (that confusion already produced one
    # wrong conclusion in this project).
    print("\\nmarginal loss from +100 units of supply at I0:")
    for product in order:
        row = curve.get(product, {})
        base = row.get(10000)
        if base:
            print(f"  {product:<12} {base:>5} -> {row.get(10100, 0):>5}"
                  f"  ({100 * (row.get(10100, 0) - base) / base:+.0f}%)")
else:
    print("price_curve.csv not attached - add the engine-tables dataset as an input")"""

GROWTH = """growth_path = find("crop_growth_trace.csv")
if growth_path:
    by_crop = collections.defaultdict(lambda: collections.defaultdict(list))
    with open(growth_path) as handle:
        for row in csv.DictReader(handle):
            by_crop[row["crop"]][row["policy"]].append(float(row["yield_units"] or 0))
    print(f"{'crop':<12} {'no fertilizer':>14} {'fertilized':>11} {'delta':>7}"
          f"  worth at the curve price")
    for crop in sorted(by_crop):
        plain = by_crop[crop].get("watered_no_fertilizer")
        fert = by_crop[crop].get("watered_fertilized")
        if not plain or not fert:
            continue
        delta = sum(fert) - sum(plain)
        price = curve.get(crop, {}).get(10000, 0) if curve else 0
        print(f"{crop:<12} {sum(plain):>14.0f} {sum(fert):>11.0f} {delta:>+7.0f}"
              f"  ${delta * price:>8,.0f} vs the ~$42 forgone by selling that unit")
else:
    print("crop_growth_trace.csv not attached")"""

REFERENCE = """for name in ("crop_economics.csv", "head_to_head_games.csv",
             "baseline_league.csv", "agents_manifest.csv"):
    path = find(name)
    if not path:
        print(f"{name:<26} not attached")
        continue
    with open(path) as handle:
        rows = list(csv.DictReader(handle))
    print(f"{name:<26} {len(rows):>5} rows  columns: {list(rows[0].keys())[:8]}")

h2h = find("head_to_head_games.csv")
if h2h:
    with open(h2h) as handle:
        games = list(csv.DictReader(handle))
    print(f"\\nmeasured reference games: {len(games)}")
    for row in games[:5]:
        print("  ", dict(list(row.items())[:6]))"""

NEXT = """## 4. Next steps this notebook should grow into

1. Port `income_breakdown.py` / `sale_context.py` / `sell_timing.py` here and run
   them over a far larger sample than one local day, always with the within-game
   control (both seats of the same match) beside every cross-team number.
2. Upload our own replay caches as a private dataset so our games live here too.
3. Sweep tape overlays over many seeds: local matches cost ~4.3 s each, so a free
   12 h session covers far more seeds than a laptop.
4. Only after a variant passes the landing gate (`scripts/assert_overlay.py`) -
   that gate exists because a silently ineffective overlay looks exactly like an
   unlucky one."""


CELLS = [
    md(INTRO),
    code(DISCOVERY),
    md("""## 1. The league index (metadata only, so it is cheap)

Every daily dataset ships a `manifest.csv` with per-episode metadata
(`episode_id, create_time, avg_score, min_score, sum_score, agent_count,
size_bytes`), and the index dataset lists the daily datasets. Reading manifests
costs kilobytes and answers population questions before touching a 35 MB replay."""),
    code(INDEX),
    code(BANDS),
    md("""## 2. Engine tables: where marginal value actually lives

The market price is a deterministic function of inventory, and the published curve
is extremely steep for shop-drained products while nearly flat for wheat and egg.
That decides which extra unit is worth producing: **price marginal units on the
curve, never at the realised average**."""),
    code(CURVE),
    code(GROWTH),
    md("""## 3. Reference agents and their measured league

`kaggriculture-reference-agents` carries ten complete agents plus measured
outcomes, a per-day league timeline and crop economics. That is the honest basis
for asking whether a variant is broadly superior: a diverse opponent set rather
than one opponent."""),
    code(REFERENCE),
    md(NEXT),
]


def main():
    for index, cell in enumerate(CELLS):
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
