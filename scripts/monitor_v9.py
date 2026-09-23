"""Monitor production submission 56496235 (baseline_v9) on Kaggle."""

import json
from pathlib import Path
import subprocess

SUB_ID = 56496235
REPLAY_DIR = Path("scratch/replays_v9")
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
    elif "Sergio D" in p1_name:
        our_bank, opp_bank = p1_bank, p0_bank
        opp_name = p0_name
    else:
        our_bank, opp_bank = p0_bank, p1_bank
        opp_name = f"{p0_name} vs {p1_name}"
    
    margin = (our_bank or 0) - (opp_bank or 0)
    result = "WIN" if margin > 0 else ("LOSS" if margin < 0 else "TIE")
    
    return {
        "id": ep_id,
        "opponent": opp_name,
        "our_bank": our_bank,
        "opp_bank": opp_bank,
        "margin": margin,
        "result": result,
    }


def main():
    print(f"=== MONITORING SUBMISSION {SUB_ID} (baseline_v9) ===")
    status_line = check_submission()
    print(f"Status: {status_line}\n")
    
    try:
        episodes = get_episodes()
    except Exception as e:
        print(f"Error fetching episodes: {e}")
        return

    completed_eps = [ep for ep in episodes if ep.get("type") == "PUBLIC"]
    print(f"Total episodes: {len(episodes)} | Completed public matches: {len(completed_eps)}\n")
    
    if not completed_eps:
        print("No completed public episodes yet.")
        return
        
    results = []
    print(f"{'Ep ID':>10} | {'Opponent':<24} | {'Our Bank':>10} | {'Opp Bank':>10} | {'Margin':>10} | {'Result':<6}")
    print("-" * 78)
    
    for ep in completed_eps:
        ep_id = ep["id"]
        try:
            res = download_and_parse_episode(ep_id)
            results.append(res)
            print(f"{res['id']:>10} | {res['opponent']:<24.24} | {res['our_bank']:>10,.0f} | {res['opp_bank']:>10,.0f} | {res['margin']:>+10,.0f} | {res['result']:<6}")
        except Exception as e:
            print(f"{ep_id:>10} | Error parsing replay: {e}")
            
    if results:
        wins = sum(1 for r in results if r["result"] == "WIN")
        losses = sum(1 for r in results if r["result"] == "LOSS")
        ties = sum(1 for r in results if r["result"] == "TIE")
        total = len(results)
        win_rate = (wins / total) * 100 if total > 0 else 0
        mean_margin = sum(r["margin"] for r in results) / total if total > 0 else 0
        mean_bank = sum(r["our_bank"] for r in results) / total if total > 0 else 0
        
        print("-" * 78)
        print(f"Summary: {wins}/{total} wins ({win_rate:.1f}%) | Mean Bank: ${mean_bank:,.0f} | Mean Margin: {mean_margin:+,.0f}")


if __name__ == "__main__":
    main()
