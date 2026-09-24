"""Monitor the baseline_v7 lineage on Kaggle.

Defaults to the original production submission; override SUB_ID and REPLAY_DIR
with KAG_MONITOR_SUB_ID / KAG_MONITOR_REPLAY_DIR (scripts/monitor_all.py does
this automatically for whichever submissions are currently active).
"""

import contextlib
import gzip
import json
import os
import subprocess
from pathlib import Path

try:
    SUB_ID = int(os.environ.get("KAG_MONITOR_SUB_ID", "56471176"))
except ValueError as exc:  # an operator typo must not surface as a raw import traceback
    raise SystemExit(f"KAG_MONITOR_SUB_ID must be an integer: {exc}") from exc
REPLAY_DIR = Path(os.environ.get("KAG_MONITOR_REPLAY_DIR", "scratch/replays_v7"))
REPLAY_DIR.mkdir(parents=True, exist_ok=True)


def run_cmd(cmd: list[str]) -> str:
    res = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return res.stdout


def clean_json_output(raw: str) -> list | dict:
    idx = raw.find("[")
    if idx != -1:
        end_idx = raw.rfind("]")
        if end_idx != -1:
            return json.loads(raw[idx : end_idx + 1])
    idx = raw.find("{")
    if idx != -1:
        end_idx = raw.rfind("}")
        if end_idx != -1:
            return json.loads(raw[idx : end_idx + 1])
    raise ValueError(f"No JSON found in output: {raw[:200]}")


def check_submission():
    out = run_cmd([".venv/bin/kaggle", "competitions", "submissions", "-c", "kaggriculture"])
    for line in out.splitlines():
        if str(SUB_ID) in line:
            return line
    return "Submission not found in recent list"


def get_episodes() -> list[dict]:
    out = run_cmd([".venv/bin/kaggle", "competitions", "episodes", str(SUB_ID), "--format", "json"])
    episodes = clean_json_output(out)
    if not isinstance(episodes, list):
        raise TypeError(
            f"expected an episode list for submission {SUB_ID}, "
            f"got {type(episodes).__name__}"
        )
    return episodes


def compress_cached_replay(path: Path) -> None:
    """Compress a freshly downloaded replay to <path>.gz, then drop the plain file.

    The .gz is read back and compared with the source before anything is unlinked;
    any failure leaves the plain .json in place so the caller still parses it.
    """
    gz_path = Path(f"{path}.gz")
    try:
        original = path.read_bytes()
        with gzip.open(gz_path, "wb", compresslevel=9) as dst:
            dst.write(original)
        with gzip.open(gz_path, "rb") as f:
            restored = f.read()
        if restored != original:
            raise ValueError(f"replay compression mismatch for {path}")
    except (OSError, EOFError, ValueError):
        with contextlib.suppress(OSError):
            gz_path.unlink(missing_ok=True)
        return
    with contextlib.suppress(OSError):
        path.unlink()


def download_and_parse_episode(ep_id: int) -> dict:
    plain = REPLAY_DIR / f"episode-{ep_id}-replay.json"
    compressed = REPLAY_DIR / f"episode-{ep_id}-replay.json.gz"
    if not plain.exists() and not compressed.exists():
        run_cmd([".venv/bin/kaggle", "competitions", "replay", str(ep_id), "-p", str(REPLAY_DIR)])
        compress_cached_replay(plain)

    target = plain if plain.exists() else compressed

    if target.name.endswith(".gz"):
        with gzip.open(target, "rt", encoding="utf-8") as f:
            data = json.load(f)
    else:
        with open(target) as f:
            data = json.load(f)

    names = data.get("info", {}).get("TeamNames", ["Agent 0", "Agent 1"])
    rewards = data.get("rewards", [0, 0])

    # Identify our seat ("Sergio D" or non-opponent)
    p0_name, p1_name = names[0], names[1]
    p0_bank, p1_bank = rewards[0], rewards[1]

    if "Sergio D" in p0_name:
        our_bank, opp_bank = p0_bank, p1_bank
        opp_name = p1_name
        seat = 0
    else:
        our_bank, opp_bank = p1_bank, p0_bank
        opp_name = p0_name
        seat = 1

    margin = our_bank - opp_bank
    win = 1 if margin > 0 else (0.5 if margin == 0 else 0)

    return {
        "ep_id": ep_id,
        "opp_name": opp_name,
        "our_bank": our_bank,
        "opp_bank": opp_bank,
        "margin": margin,
        "win": win,
        "seat": seat,
    }


def main():
    print(f"=== MONITORING SUBMISSION {SUB_ID} (baseline_v7) ===")
    sub_line = check_submission()
    print(f"Status: {sub_line.strip()}\n")

    episodes = get_episodes()
    public_eps = [e for e in episodes if e.get("type") == "EpisodeType.EPISODE_TYPE_PUBLIC" and e.get("state") == "EpisodeState.COMPLETED"]

    print(f"Total episodes: {len(episodes)} | Completed public matches: {len(public_eps)}\n")

    results = []
    for ep in public_eps:
        ep_id = ep["id"]
        try:
            res = download_and_parse_episode(ep_id)
            results.append(res)
        except Exception as e:
            print(f"Error parsing episode {ep_id}: {e}")

    if not results:
        print("No public matches parsed yet.")
        return

    print(f"{'Ep ID':>10} | {'Opponent':<24} | {'Our Bank':>9} | {'Opp Bank':>9} | {'Margin':>9} | {'Result':<6}")
    print("-" * 78)

    wins = 0
    total_margin = 0
    total_our_bank = 0

    for r in results:
        res_str = "WIN" if r["win"] == 1 else ("LOSS" if r["win"] == 0 else "TIE")
        print(f"{r['ep_id']:>10} | {r['opp_name'][:24]:<24} | {r['our_bank']:>9,.0f} | {r['opp_bank']:>9,.0f} | {r['margin']:>+9,.0f} | {res_str:<6}")
        wins += 1 if r["win"] == 1 else 0
        total_margin += r["margin"]
        total_our_bank += r["our_bank"]

    n = len(results)
    print("-" * 78)
    print(f"Summary: {wins}/{n} wins ({wins/n*100:.1f}%) | Mean Bank: ${total_our_bank/n:,.0f} | Mean Margin: {total_margin/n:+,.0f}")


if __name__ == "__main__":
    main()
