"""Monitor the baseline_v8_1 lineage on Kaggle.

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
    SUB_ID = int(os.environ.get("KAG_MONITOR_SUB_ID", "56478415"))
except ValueError as exc:  # an operator typo must not surface as a raw import traceback
    raise SystemExit(f"KAG_MONITOR_SUB_ID must be an integer: {exc}") from exc
REPLAY_DIR = Path(os.environ.get("KAG_MONITOR_REPLAY_DIR", "scratch/replays_v8_1"))
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
    print(f"=== MONITORING SUBMISSION {SUB_ID} (baseline_v8_1) ===")
    sub_line = check_submission()
    print(f"Status: {sub_line.strip()}\n")

    try:
        episodes = get_episodes()
    except Exception as e:
        print(f"No episodes yet: {e}")
        return

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
        print("Waiting for completed public matches...")
        return

    print(f"{'Ep ID':>10} | {'Opponent':<24} | {'Our Bank':>9} | {'Opp Bank':>9} | {'Margin':>9} | Result")
    print("-" * 78)
    for r in results:
        res_str = "WIN " if r["win"] == 1 else ("TIE " if r["win"] == 0.5 else "LOSS")
        our_b = int(r['our_bank'])
        opp_b = int(r['opp_bank'])
        mrg = int(r['margin'])
        print(f"{r['ep_id']:10d} | {r['opp_name'][:24]:<24} | {our_b:9,d} | {opp_b:9,d} | {mrg:+9,d} | {res_str}")

    wins = sum(1 for r in results if r["win"] == 1)
    mean_bank = sum(r["our_bank"] for r in results) / len(results)
    mean_margin = sum(r["margin"] for r in results) / len(results)

    print("-" * 78)
    print(f"Summary: {wins}/{len(results)} wins ({wins/len(results)*100:.1f}%) | Mean Bank: ${mean_bank:,.0f} | Mean Margin: {mean_margin:+,.0f}")


if __name__ == "__main__":
    main()
