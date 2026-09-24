"""Combined monitor for our ACTIVE Kaggle submissions.

Kaggle keeps only our latest 2 submissions active, and only the best-scoring of
the two is shown on the leaderboard (verified: displayed Score == max(active)).
Both slots therefore change on every submit, so the targets are resolved from
the API at run time.

This is a correction of a real defect: the previous version hardcoded the
V9/V9.1 pair, which went stale the moment V9 was evicted by the V8 recovery
submission (56513137) - telemetry kept watching a dead submission and ignored
the one that mattered. Targets now come from the live submissions list.

Each lineage monitor honours KAG_MONITOR_SUB_ID and KAG_MONITOR_REPLAY_DIR, so
this script only resolves and dispatches.

Usage:
    .venv/bin/python scripts/monitor_all.py
"""

import os
import re
import subprocess
import sys

SUBMISSIONS_CMD = [
    ".venv/bin/kaggle",
    "competitions",
    "submissions",
    "-c",
    "kaggriculture",
]

# fileName -> (lineage monitor, replay cache dir)
LINEAGES = {
    "main_v7.py": ("scripts/monitor_v7.py", "scratch/replays_v7"),
    "main_v8.py": ("scripts/monitor_v8.py", "scratch/replays_v8"),
    "main_v8_1.py": ("scripts/monitor_v8_1.py", "scratch/replays_v8_1"),
    "main_v9.py": ("scripts/monitor_v9.py", "scratch/replays_v9"),
    "main_v9_1.py": ("scripts/monitor_v9_1.py", "scratch/replays_v9_1"),
}

ROW_RE = re.compile(r"^\s*(\d+)\s+(\S+\.py)\s+(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")

ACTIVE_SLOTS = 2


def active_submissions(limit: int = ACTIVE_SLOTS) -> list[tuple[str, str]]:
    """Return [(ref, fileName)] for the most recent submissions, newest first.

    Kaggle only scores the latest 2, so the default limit is 2. Raises on API
    failure rather than silently reporting stale targets.
    """
    proc = subprocess.run(SUBMISSIONS_CMD, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(
            f"could not list submissions (exit {proc.returncode}): "
            f"{proc.stderr.strip()[:200]}"
        )

    rows: list[tuple[str, str, str]] = []
    for line in proc.stdout.splitlines():
        match = ROW_RE.match(line)
        if match:
            ref, file_name, submitted_at = match.groups()
            rows.append((submitted_at, ref, file_name))

    rows.sort(reverse=True)
    return [(ref, file_name) for _date, ref, file_name in rows[:limit]]


def main() -> None:
    targets = active_submissions()
    if not targets:
        sys.exit("monitor_all: no submissions parsed from the Kaggle API")

    summary = ", ".join(f"{ref} ({name})" for ref, name in targets)
    print(f"ACTIVE SUBMISSIONS ({len(targets)} of {ACTIVE_SLOTS} slots): {summary}\n")

    for ref, file_name in targets:
        entry = LINEAGES.get(file_name)
        if entry is None:
            print(f"!! no lineage monitor registered for {file_name} (ref {ref}) - skipping\n")
            continue

        script, replay_dir = entry
        print("=" * 78)
        print(f"RUNNING {script} FOR SUBMISSION {ref} ({file_name})")
        # flush: the child writes straight to our fd, so a buffered header would be
        # printed *after* the child's output whenever stdout is redirected to a file.
        print("=" * 78, flush=True)
        env = dict(os.environ, KAG_MONITOR_SUB_ID=ref, KAG_MONITOR_REPLAY_DIR=replay_dir)
        subprocess.run([sys.executable, script], env=env)
        print()


if __name__ == "__main__":
    main()
