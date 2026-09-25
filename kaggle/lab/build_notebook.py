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

import ast
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

TIER2 = """import resource
import statistics

# Tier 2 - full-replay BEHAVIOUR analysis, always with the within-game control.
#
# This is the tier the notebook's own next-steps list was missing: income_breakdown
# / sale_context / sell_timing ported so behaviour analyses run over a large sample,
# always with both seats of the same match beside every cross-team number.
#
# Why the within-game control is NOT a stylistic preference: the corpus is published
# as the day's TOP episodes, so it is selection-biased on the OUTCOME (no episode
# here sits in our rating band). Comparing a top game with a random one is
# confounded, and this project was already burned by exactly that (a +$25k
# "opportunity" that turned out to be an artefact). Inside one match both seats face
# the same shops, the same town drain and the same market, so winner-against-loser
# WITHIN a match is the only safe axis. Every number below is computed that way.
#
# Memory and IO are the whole problem: one replay is ~34 MB, a day is ~21 GB and the
# mounted corpus is ~190 GB. So ONE replay is opened, profiled and released before
# the next is read, and only scalars survive the iteration - nothing here holds a
# day, or a thousand parsed replays, in memory. It stays single process on purpose:
# a 4-way pool would hold four replays and four parsed object graphs at once, and
# the mounted read is the bottleneck anyway.

TURNS_PER_DAY = 24

# Taken verbatim from scripts/corpus_within_game.py, so the numbers stay comparable
# with the local 608-episode result.
T2_METRICS = (
    "hires", "land", "seed_units", "animals_bought", "plants", "harvests",
    "waters", "feeds", "fertilizes", "collects", "builds",
    "sells", "sell_units", "sell_lot_median", "distinct_sell_days",
    "peak_crop_tiles", "peak_animals", "quadrants",
)

MAX_REPLAYS_T2 = 6000   # a bound, so a run that is too slow still prints a report


def t2_day(path):
    match = re.search(r"kaggriculture-episodes-(\\d{4}-\\d{2}-\\d{2})", path)
    if match:
        return match.group(1)
    # Never assume the layout: a dataset mounted under another name still has a
    # parent directory, and a file whose day cannot be recovered is reported under
    # that rather than silently dropped.
    return os.path.basename(os.path.dirname(path)) or "?"


def t2_verbs(action):
    # Farm verbs this agent asks for in one turn. Copied from the local script.
    if not isinstance(action, dict):
        return []
    units = [action.get("farmer"), *(action.get("hands") or [])]
    return [unit[0] for unit in units if isinstance(unit, (list, tuple)) and unit]


def t2_sum_orders(action, verb):
    # Units this action asks for under verb, summed over its market orders.
    total = 0
    if isinstance(action, dict):
        for order in action.get("market") or []:
            if (isinstance(order, (list, tuple)) and len(order) >= 3
                    and order[0] == verb):
                try:
                    total += int(order[2])
                except (TypeError, ValueError):
                    continue
    return total


def t2_profile_seat(steps, seat):
    # Ported from scripts/corpus_within_game.py:profile_seat. Behaviour is identical,
    # including the median sell lot over that seat's SELL orders and the peaks over
    # the whole match; changing it would break comparability with the local result.
    m = collections.Counter()
    lots = []
    sell_days = set()
    peak_crops = peak_animals = peak_hands = 0
    quadrants = 1
    bank = 0.0

    for index in range(1, len(steps)):
        entry = steps[index][seat] if seat < len(steps[index]) else {}
        obs = entry.get("observation") or {}
        action = entry.get("action")
        day = obs.get("day", index // TURNS_PER_DAY)

        for verb in t2_verbs(action):
            if verb == "PLANT":
                m["plants"] += 1
            elif verb == "HARVEST":
                m["harvests"] += 1
            elif verb == "WATER":
                m["waters"] += 1
            elif verb == "FEED":
                m["feeds"] += 1
            elif verb == "FERTILIZE":
                m["fertilizes"] += 1
            elif verb == "COLLECT_FERTILIZER":
                m["collects"] += 1
            elif verb in ("BUILD_COOP", "BUILD_PASTURE"):
                m["builds"] += 1

        if isinstance(action, dict):
            for order in action.get("market") or []:
                if not isinstance(order, (list, tuple)) or not order:
                    continue
                if order[0] == "HIRE":
                    m["hires"] += 1
                elif order[0] == "BUY_LAND":
                    m["land"] += 1
                elif order[0] == "BUY_ANIMAL":
                    m["animals_bought"] += 1
                elif order[0] == "SELL" and len(order) >= 3:
                    m["sells"] += 1
                    sell_days.add(day)
                    try:
                        quantity = int(order[2])
                    except (TypeError, ValueError):
                        continue
                    lots.append(quantity)
                    m["sell_units"] += quantity
            m["seed_units"] += t2_sum_orders(action, "BUY_SEED")

        crops = animals = 0
        farm = (obs.get("farms") or [{}])[obs.get("player", seat)] or {}
        for row in farm.get("tiles") or []:
            for cell in row:
                if not isinstance(cell, dict):
                    continue
                if cell.get("animal"):
                    animals += 1
                elif cell.get("kind") == "PLANT":
                    crops += 1
        peak_crops = max(peak_crops, crops)
        peak_animals = max(peak_animals, animals)
        peak_hands = max(peak_hands, len(farm.get("hands") or []))
        quadrants = len(farm.get("unlocked_quadrants") or [])
        bank = farm.get("money", bank)

    return {
        **m,
        "sell_lot_median": statistics.median(lots) if lots else 0,
        "distinct_sell_days": len(sell_days),
        "peak_crop_tiles": peak_crops,
        "peak_animals": peak_animals,
        "peak_hands": peak_hands,
        "quadrants": quadrants,
        "bank": bank,
    }


def t2_unlock_days(steps, seat):
    # Ported from scripts/corpus_quadrant_timing.py:unlock_days. farm[
    # unlocked_quadrants] starts as ["NW"], so length 2 is the first paid quadrant
    # (NE), 3 is the SW and 4 is the SE. Returns the day each unlocked and the bank
    # gap between the buyer and the OPPONENT at the moment the 4th was bought.
    days = {}
    gap_at_four = None
    bank = 0.0
    for index in range(1, len(steps)):
        entry = steps[index][seat] if seat < len(steps[index]) else {}
        obs = entry.get("observation") or {}
        farm = (obs.get("farms") or [{}])[obs.get("player", seat)] or {}
        count = len(farm.get("unlocked_quadrants") or [])
        day = obs.get("day", index // TURNS_PER_DAY)
        bank = farm.get("money", bank)
        if count >= 2 and 2 not in days:
            days[2] = day
        if count >= 3 and 3 not in days:
            days[3] = day
        if count >= 4 and 4 not in days:
            days[4] = day
            other = steps[index][1 - seat] if 1 - seat < len(steps[index]) else {}
            oobs = other.get("observation") or {}
            ofarm = (oobs.get("farms") or [{}])[oobs.get("player", 1 - seat)] or {}
            gap_at_four = farm.get("money", 0) - ofarm.get("money", 0)
    return days, gap_at_four, bank


def t2_new_bucket():
    # Only scalars and short lists; a bucket for 6,000 matches is a few MB at most.
    return {
        "games": 0,
        "seat_quad": collections.defaultdict(lambda: [0, 0]),   # quadrants -> [wins, played]
        "diff_n": 0, "diff_more": 0,
        "unlock": collections.defaultdict(list),
        "four_gap": [],
        "winner_vals": collections.defaultdict(list),
        "loser_vals": collections.defaultdict(list),
        "winner_more": collections.Counter(),
        "winner_less": collections.Counter(),
        "margins": [], "relative": [],
    }


def t2_fold(bucket, a, b):
    # One fold per MATCH: both seats are profiled, then winner against loser of that
    # same game. No cross-game comparison enters this function.
    bucket["games"] += 1
    if a["bank"] >= b["bank"]:
        winner, loser = a, b
    else:
        winner, loser = b, a

    for prof, other in ((a, b), (b, a)):
        bucket["seat_quad"][prof["quadrants"]][1] += 1
        if prof["bank"] > other["bank"]:
            bucket["seat_quad"][prof["quadrants"]][0] += 1
        for quad, day in prof["days"].items():
            bucket["unlock"][quad].append(day)
        if 4 in prof["days"]:
            bucket["four_gap"].append(prof["gap_at_four"])

    if a["quadrants"] != b["quadrants"]:
        bucket["diff_n"] += 1
        more = a if a["quadrants"] > b["quadrants"] else b
        other = b if more is a else a
        if more["bank"] > other["bank"]:
            bucket["diff_more"] += 1

    for metric in T2_METRICS:
        wv = winner.get(metric, 0) or 0
        lv = loser.get(metric, 0) or 0
        bucket["winner_vals"][metric].append(wv)
        bucket["loser_vals"][metric].append(lv)
        if wv > lv:
            bucket["winner_more"][metric] += 1
        elif wv < lv:
            bucket["winner_less"][metric] += 1

    gap = abs(a["bank"] - b["bank"])
    bucket["margins"].append(gap)
    bucket["relative"].append(gap / max(1.0, max(a["bank"], b["bank"])))


by_day = collections.defaultdict(list)
for path in episode_files:
    if t2_day(path) != "?":
        by_day[t2_day(path)].append(path)
t2_days = sorted(by_day)

# Round-robin across days, so a MAX_REPLAYS_T2 cut still covers every attached day
# instead of exhausting one of them and calling the result representative.
plan = []
cursors = {day: 0 for day in t2_days}
while True:
    progressed = False
    for day in t2_days:
        if cursors[day] < len(by_day[day]):
            plan.append((day, by_day[day][cursors[day]]))
            cursors[day] += 1
            progressed = True
    if not progressed:
        break

sample = plan[:MAX_REPLAYS_T2]
t2_newest = t2_days[-1] if t2_days else "?"
league_total = (sum(int(row["episode_count"]) for row in index_rows)
                if index_rows else 38454)
print(f"tier-2 sample : {len(sample):,} replays "
      f"(mounted {len(plan):,} across {len(t2_days)} days, cap {MAX_REPLAYS_T2:,})")
print(f"tier-2 days   : {', '.join(t2_days)}")
print(f"newest day    : {t2_newest}")


# One bucket per day as well as the pool: the per-day table is what shows whether the
# effect is stable or only tracks the field's strength on one day, and it costs a few
# scalars per match.
t2_pool = t2_new_bucket()
t2_day_buckets = {day: t2_new_bucket() for day in t2_days}
t2_skipped = collections.Counter()
t2_processed = collections.Counter()
t2_done = 0
started = time.time()

for _cut, (_day, _path) in enumerate(sample, 1):
    try:
        with open(_path) as handle:
            data = json.load(handle)
    except (OSError, ValueError) as exc:
        t2_skipped["unreadable / not JSON (" + type(exc).__name__ + ")"] += 1
        continue
    steps = data.get("steps") if isinstance(data, dict) else data
    if not steps or len(steps) < 100 or any(len(s) < 2 for s in steps[:2]):
        t2_skipped["truncated: too few steps or seats"] += 1
        del data, steps
        continue
    statuses = (data.get("statuses") or []) if isinstance(data, dict) else []
    if any(str(s).upper().find("ERROR") >= 0 for s in statuses):
        t2_skipped["engine reported an ERROR status"] += 1
        del data, steps
        continue
    try:
        a = t2_profile_seat(steps, 0)
        b = t2_profile_seat(steps, 1)
        for prof, seat in ((a, 0), (b, 1)):
            prof["days"], prof["gap_at_four"], _ = t2_unlock_days(steps, seat)
    except (AttributeError, IndexError, KeyError, TypeError) as exc:
        t2_skipped["malformed replay (" + type(exc).__name__ + ")"] += 1
        del data, steps
        continue
    del data, steps   # release the ~34 MB replay before opening the next one

    t2_fold(t2_pool, a, b)
    t2_fold(t2_day_buckets[_day], a, b)
    t2_processed[_day] += 1
    t2_done += 1

    if t2_done % 500 == 0:
        _elapsed = time.time() - started
        _rate = t2_done / max(_elapsed, 1e-9)
        print(f"  ... {t2_done:,} replays in {_elapsed:.0f}s ({_rate:.1f}/s), "
              f"{sum(t2_skipped.values()):,} skipped | projection for {len(plan):,}"
              f": {len(plan) / _rate / 60:.0f} min", flush=True)

elapsed = time.time() - started
rate = t2_done / max(elapsed, 1e-9)
peak_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
print()
print(f"TIER 2 done: {t2_done:,} replays in {elapsed:.0f}s ({rate:.2f}/s), "
      f"peak RSS {peak_mb:.0f} MB")
print(f"  skipped: {sum(t2_skipped.values()):,}")
for reason, count in t2_skipped.most_common():
    print(f"    {count:>6,}  {reason}")
print(f"  processed per day: {dict(sorted(t2_processed.items()))}")
print(f"  projection: mounted corpus {len(plan):,} -> {len(plan) / rate / 60:.0f} min"
      f" | whole league {league_total:,} -> {league_total / rate / 3600:.1f} h"
      f" at this rate")
print(f"  bound MAX_REPLAYS_T2={MAX_REPLAYS_T2:,}: "
      + ("all mounted replays taken" if len(sample) == len(plan)
         else "sample CUT by the bound"))
"""

TIER2_REPORT = """def t2_report(bucket, title, full=True):
    print("=" * 84)
    print(title)
    print("=" * 84)
    games = bucket["games"]
    if not games:
        print("no usable matches in this bucket")
        print()
        return
    print(f"matches (both seats of the same game): {games:,}   seats: {2 * games:,}")
    print()

    print("--- QUADRANT DOSE-RESPONSE (the headline) ---")
    print(f"{'quadrants':>10}{'played':>10}{'wins':>8}{'win rate':>10}")
    for quad in sorted(bucket["seat_quad"]):
        wins, played = bucket["seat_quad"][quad]
        print(f"{quad:>10}{played:>10,}{wins:>8,}{wins / played:>10.1%}")
    if bucket["diff_n"]:
        print(f"matches where the two seats DIFFER in quadrant count: "
              f"{bucket['diff_n']:,}")
        print(f"  the seat with MORE quadrants wins: {bucket['diff_more']:,}"
              f"/{bucket['diff_n']:,} = {bucket['diff_more'] / bucket['diff_n']:.1%}")
    print()

    print("--- QUADRANT TIMING (does the purchase PRECEDE the win?) ---")
    for quad, label in ((2, "2nd (NE)"), (3, "3rd (SW)")):
        days = bucket["unlock"].get(quad) or []
        if not days:
            continue
        print(f"  {label:<9} unlocked by {len(days):>5} seats | median day "
              f"{statistics.median(days):>4.0f} (min {min(days):.0f}, max {max(days):.0f})")
    four = bucket["unlock"].get(4) or []
    if four:
        print(f"  {'4th (SE)':<9} bought  by {len(four):>5} seats | median day "
              f"{statistics.median(four):>4.0f} (min {min(four):.0f}, max {max(four):.0f})")
        gaps = [gap for gap in bucket["four_gap"] if gap is not None]
        if gaps:
            ahead = sum(1 for gap in gaps if gap > 0)
            level = sum(1 for gap in gaps if gap == 0)
            print(f"    at the moment of the 4th purchase the buyer was AHEAD: "
                  f"{ahead:,}/{len(gaps):,} = {ahead / len(gaps):.1%} (level: {level:,})")
            print(f"    median bank gap at that moment: ${statistics.median(gaps):+,.0f}"
                  f"   (+ = buyer ahead, - = buyer behind)")
        late = sum(1 for day in four if day > 15)
        print(f"    bought after day 15: {late:,}/{len(four):,} = {late / len(four):.0%}")
    else:
        print("  nobody bought the 4th quadrant in this bucket")
    print()

    if not full:
        return

    print("--- BEHAVIOUR SEPARATORS (winner vs loser WITHIN the same match) ---")
    print(f"{'metric':<20}{'winner':>11}{'loser':>11}{'delta':>11}"
          f"{'winner more':>13}{'wins/losses':>14}")
    rows = []
    for metric in T2_METRICS:
        wvals = bucket["winner_vals"][metric]
        lvals = bucket["loser_vals"][metric]
        wv = statistics.median(wvals) if wvals else 0
        lv = statistics.median(lvals) if lvals else 0
        more = bucket["winner_more"][metric]
        less = bucket["winner_less"][metric]
        share = more / max(1, more + less)
        rows.append((metric, wv, lv, wv - lv, share, more, less))
    for metric, wv, lv, delta, share, more, less in sorted(
            rows, key=lambda row: -abs(row[4] - 0.5)):
        flag = "  <-- separates" if abs(share - 0.5) >= 0.15 else ""
        print(f"{metric:<20}{wv:>11.1f}{lv:>11.1f}{delta:>+11.1f}"
              f"{share:>13.1%}{more:>7}/{less:<7}{flag}")
    print()
    print("'winner more' = share of matches where the WINNER has more of that metric.")
    print("50% = no separation. Only the direction counts: the final ranking is a")
    print("win-rate tournament, not a margin race.")
    print()

    print("--- MARGINS ---")
    print(f"absolute margin, median: ${statistics.median(bucket['margins']):,.0f}")
    print(f"relative margin, median: {statistics.median(bucket['relative']):.2%}"
          f" of the winner's bank")
    close = sum(1 for rel in bucket["relative"] if rel < 0.05)
    print(f"decided by under 5% of the winner's bank: {close:,}/{games:,}"
          f" = {close / games:.0%}")
    print()


# The local 608-episode measurement, taken from the 2026-09-23 dataset (our local
# cache is exactly those files). The newest day was NOT part of it, so comparing
# the newest day against these numbers is an OUT-OF-SAMPLE replication, not a
# re-measurement of the same data.
LOCAL_0923 = {
    "day": "2026-09-23",
    "episodes": 608,
    "seats": 1216,
    "quad_wins": {2: 0, 3: 334, 4: 274},
    "quad_played": {2: 12, 3: 756, 4: 448},
    "differ_share": 0.672,
    # Measured over the same 608 episodes (scripts/corpus_quadrant_timing.py). The
    # 100-file preview read 16% ahead and a $-3,495 median gap; the full 608 read
    # 21% and $-3,083, so the same-sample value is the one used as the baseline.
    "ahead_at_four": 0.21,
    "gap_at_four": -3083.0,
    "day4_median": 10.0,
    "day4_max": 13.0,
}


def t2_compare(bucket, local):
    # CONFIRMS / SHRINKS / CONTRADICTS the local finding, printed next to the raw
    # rates so the label can always be checked against the numbers.
    print("--- vs the LOCAL " + local["day"] + " measurement (OUT-OF-SAMPLE) ---")
    print(f"local: {local['episodes']:,} episodes / {local['seats']:,} seats"
          f"   |   this day: {bucket['games']:,} matches / {2 * bucket['games']:,} seats")
    print(f"{'quadrants':>10}{'local':>12}{'local rate':>12}{'this day':>12}"
          f"{'here':>9}{'delta':>9}")
    here = {}
    deltas = {}
    for quad in (2, 3, 4):
        local_wins = local["quad_wins"].get(quad, 0)
        local_played = local["quad_played"].get(quad, 0)
        local_rate = local_wins / local_played if local_played else 0.0
        wins, played = bucket["seat_quad"].get(quad, [0, 0])
        rate = wins / played if played else None
        here[quad] = rate
        deltas[quad] = None if rate is None else rate - local_rate
        cell = f"{wins}/{played}" if played else "-"
        shown = "n/a" if rate is None else f"{rate:>9.1%}"
        delta = "n/a" if rate is None else f"{rate - local_rate:>+9.1%}"
        print(f"{quad:>10}{local_wins:>5}/{local_played:<6}{local_rate:>12.1%}"
              f"{cell:>12}{shown}{delta}")
    here_differ = bucket["diff_more"] / bucket["diff_n"] if bucket["diff_n"] else None
    shown = "n/a" if here_differ is None else f"{here_differ:>9.1%}"
    delta = ("n/a" if here_differ is None
             else f"{here_differ - local['differ_share']:>+9.1%}")
    print(f"{'differ':>10}{'':>12}{local['differ_share']:>12.1%}"
          f"{(str(bucket['diff_more']) + '/' + str(bucket['diff_n'])):>12}"
          f"{shown}{delta}")

    gaps = [gap for gap in bucket["four_gap"] if gap is not None]
    if gaps:
        ahead = sum(1 for gap in gaps if gap > 0) / len(gaps)
        print(f"4th quadrant, buyer AHEAD at the moment of purchase: local "
              f"{local['ahead_at_four']:.0%} | this day {ahead:.0%}"
              f" ({ahead - local['ahead_at_four']:+.1%})")
        print(f"4th quadrant, median bank gap at that purchase: local "
              f"${local['gap_at_four']:,.0f} | this day "
              f"${statistics.median(gaps):,.0f}")
    four_days = bucket["unlock"].get(4) or []
    if four_days:
        late = sum(1 for day in four_days if day > 15)
        print(f"4th quadrant unlock day: local median {local['day4_median']:.0f}"
              f" (max {local['day4_max']:.0f}; every purchase by day 15) | this day"
              f" median {statistics.median(four_days):.0f}"
              f" (max {max(four_days):.0f}) | after day 15: {late}")

    if here.get(4) is None or here.get(3) is None:
        verdict = "INDETERMINATE (a quadrant group is empty on this day)"
    elif here[4] <= here[3]:
        verdict = "CONTRADICTS (the 4th quadrant no longer beats the 3rd here)"
    elif any(delta is not None and delta > 0.05 for delta in deltas.values()):
        verdict = "CONFIRMS / STRENGTHENS (direction holds, at least one rate higher)"
    elif all(delta is not None and abs(delta) <= 0.05 for delta in deltas.values()):
        verdict = "CONFIRMS (every quadrant rate within 5 points of the local one)"
    else:
        verdict = "SHRINKS (direction holds, effect smaller than local)"
    print("verdict, this day against the local " + local["day"] + " finding: " + verdict)
    print()


def t2_per_day(day_buckets):
    # Cheap by construction: the per-day buckets are a few scalars, not replays.
    print("=" * 84)
    print("TIER 2 - PER-DAY QUADRANT WIN RATES (stable across days, or tracking the field?)")
    print("=" * 84)
    print(f"{'day':<12}{'seats':>7}{'2q':>17}{'3q':>17}{'4q':>17}{'differ->more':>14}")
    for day in sorted(day_buckets):
        bucket = day_buckets[day]
        row = f"{day:<12}{2 * bucket['games']:>7,}"
        for quad in (2, 3, 4):
            wins, played = bucket["seat_quad"].get(quad, [0, 0])
            cell = f"{wins}/{played} = {wins / played:.0%}" if played else "-"
            row += f"{cell:>17}"
        differ = (f"{bucket['diff_more'] / bucket['diff_n']:.0%}"
                  if bucket["diff_n"] else "-")
        print(row + f"{differ:>14}")
    print()
    print("The index manifest shows the field drifting (median_avg_score 3,084 on")
    print("09-18 down to 2,983 on 09-24). A rate that holds across every day is a")
    print("robustness statement; a rate that tracks the day's field is a warning that")
    print("the sample is selected.")
    print()


# Order matters: the newest day leads, alone, because it was never part of the local
# measurement and is therefore the only out-of-sample replication we have.
t2_report(t2_day_buckets[t2_newest],
          "TIER 2.1 - DAY " + t2_newest + " ALONE (out-of-sample replication)")
t2_compare(t2_day_buckets[t2_newest], LOCAL_0923)
t2_report(t2_pool,
          "TIER 2.2 - POOL " + t2_days[0] + " .. " + t2_days[-1]
          + " (INCLUDES the day the local finding came from)")
t2_per_day(t2_day_buckets)
"""

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
2. Tier 2 now runs the within-game behaviour analysis (quadrant dose-response,
   unlock timing, separators, margins) over every mounted day, leading with the
   newest day as an out-of-sample replication. Raise `MAX_REPLAYS_T2` to widen the
   sample, and keep it above the mounted replay count so the bound never bites by
   accident.
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
    md("""## Tier 2 - full-replay BEHAVIOUR, always with the within-game control

The tier the notebook's own next-steps list was missing: `income_breakdown.py` /
`sale_context.py` / `sell_timing.py` ported so behaviour analyses run over a large
sample, always with both seats of the same match beside every cross-team number.

**Why the within-game control is not a stylistic preference.** The corpus is
published as the day's TOP episodes, so it is selection-biased on the OUTCOME: no
episode here sits in our rating band. Comparing a top game with a random one is
confounded, and this project was already burned by exactly that (a +$25k
"opportunity" that turned out to be an artefact). Inside one match both seats face
the same shops, the same town drain and the same market, so winner-against-loser
within a match is the only safe axis. The analysis profiles BOTH seats of every
replay and compares the winner with the loser of that same game - never one corpus
band against another.

**Cost, and how it stays inside memory.** One replay is ~34 MB, a day is ~21 GB and
the mounted corpus is ~190 GB. Each replay is opened, profiled and released before
the next one is read, and only scalars survive an iteration. The cell prints the
measured rate, the number processed, the number skipped **with the reason**, and a
projection for the whole corpus, so a truncated sample can never pass for a clean
result. `MAX_REPLAYS_T2` bounds the work, so a slow run still prints a report
instead of timing out.

**The report leads with the NEWEST day, alone.** Our local 608-episode result came
from the 2026-09-23 dataset, so the newest day is an out-of-sample replication: it
was never seen locally, and pooling it would destroy that property by mixing the
already-used day into the total. The pool follows, clearly labelled as including
that day, and a per-day table checks whether the effect is stable across days or
only tracks the field's strength."""),
    code(TIER2),
    md("""### The report

Newest day first - its quadrant dose-response against the local measurement, with
an explicit CONFIRMS / SHRINKS / CONTRADICTS verdict - then the pool of all attached
days, then a per-day table. Within each block: the dose-response (wins and win rate
for seats ending with 2, 3 and 4 quadrants, and the share when the two seats
differ), the day each quadrant unlocked and the bank gap at the moment the 4th was
bought (that timing is what separates a cause from a symptom), the behaviour
separators with the winner-has-more share, and the margin distribution."""),
    code(TIER2_REPORT),
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
            # Parse, not compile: the same SyntaxError for the same broken cell,
            # without asking the interpreter to build executable code from a string.
            ast.parse(source, filename=f"<cell {index}>")
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
