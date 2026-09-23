"""Monitor production submission 56501811 (baseline_v9_1) on Kaggle."""

import json
from pathlib import Path
import subprocess

SUB_ID = 56501811
REPLAY_DIR = Path("scratch/replays_v9_1")
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
        opp_name = p1_name
        
    margin = our_bank - opp_bank
    res = "WIN" if margin > 0 else "LOSS" if margin < 0 else "TIE"
    
    return {
        "ep_id": ep_id,
        "opp_name": opp_name,
        "our_bank": our_bank,
        "opp_bank": opp_bank,
        "margin": margin,
        "result": res
    }


def main():
    print(f"=== MONITORING SUBMISSION {SUB_ID} (baseline_v9_1) ===")
    status_line = check_submission()
    print("Status:", status_line)
    print()
    
    try:
        episodes = get_episodes()
    except Exception as e:
        print(f"Could not fetch episodes: {e}")
        return

    # Filter to public matches: exactly 2 players, non-empty rewards
    public_episodes = []
    for ep in episodes:
        agents = ep.get("agents", [])
        if len(agents) == 2 and any(a.get("reward") is not None for a in agents):
            public_episodes.append(ep)

    print(f"Total episodes: {len(episodes)} | Completed public matches: {len(public_episodes)}")
    print()
    
    if not public_episodes:
        print("No completed public matches yet.")
        return

    records = []
    print(f"{'Ep ID':>10} | {'Opponent':<24} | {'Our Bank':>10} | {'Opp Bank':>10} | {'Margin':>10} | Result")
    print("-" * 78)
    
    for ep in public_episodes:
        ep_id = ep["id"]
        try:
            rec = download_and_parse_episode(ep_id)
            records.append(rec)
            print(f"{rec['ep_id']:>10} | {rec['opp_name'][:24]:<24} | {rec['our_bank']:>10,.0f} | {rec['opp_bank']:>10,.0f} | {rec['margin']:>+10,.0f} | {rec['result']}")
        except Exception as e:
            print(f"{ep_id:>10} | Error parsing: {e}")

    if records:
        wins = sum(1 for r in records if r["result"] == "WIN")
        losses = sum(1 for r in records if r["result"] == "LOSS")
        ties = sum(1 for r in records if r["result"] == "TIE")
        tot = len(records)
        mean_bank = sum(r["our_bank"] for r in records) / tot
        mean_margin = sum(r["margin"] for r in records) / tot
        print("-" * 78)
        print(f"Summary: {wins}/{tot} wins ({wins/tot*100:.1f}%) | Mean Bank: ${mean_bank:,.0f} | Mean Margin: {mean_margin:+,.0f}")


if __name__ == "__main__":
    main()
