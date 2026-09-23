"""Monitor production submission 56476982 (baseline_v8) on Kaggle."""

import json
from pathlib import Path
import subprocess

SUB_ID = 56476982
REPLAY_DIR = Path("scratch/replays_v8")
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
    return clean_json_output(out)


def download_and_parse_episode(ep_id: int) -> dict:
    target = REPLAY_DIR / f"episode-{ep_id}-replay.json"
    if not target.exists():
        run_cmd([".venv/bin/kaggle", "competitions", "replay", str(ep_id), "-p", str(REPLAY_DIR)])
    
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
    print(f"=== MONITORING SUBMISSION {SUB_ID} (baseline_v8) ===")
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
        print(f"{r['ep_id']:10d} | {r['opp_name'][:24]:<24} | {r['our_bank']:9,d} | {r['opp_bank']:9,d} | {r['margin']:+9,d} | {res_str}")
        
    wins = sum(1 for r in results if r["win"] == 1)
    ties = sum(1 for r in results if r["win"] == 0.5)
    mean_bank = sum(r["our_bank"] for r in results) / len(results)
    mean_margin = sum(r["margin"] for r in results) / len(results)
    
    print("-" * 78)
    print(f"Summary: {wins}/{len(results)} wins ({wins/len(results)*100:.1f}%) | Mean Bank: ${mean_bank:,.0f} | Mean Margin: {mean_margin:+,.0f}")


if __name__ == "__main__":
    main()
