"""Monitor production submission 56471176 (baseline_v7) on Kaggle."""

import json
from pathlib import Path
import subprocess

SUB_ID = 56471176
REPLAY_DIR = Path("scratch/replays_v7")
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
