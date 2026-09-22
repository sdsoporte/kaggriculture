"""Fetch and analyze latest matches from the Top 10 teams on Kaggle Leaderboard."""

import json
from pathlib import Path
import subprocess

REPLAY_DIR = Path("scratch/replays_top10")
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


def get_top10_teams():
    out = run_cmd(
        [
            ".venv/bin/kaggle",
            "competitions",
            "leaderboard",
            "kaggriculture",
            "--show",
            "--format",
            "json",
        ]
    )
    teams = clean_json_output(out)
    return teams[:10]


def get_team_submissions(team_id: int):
    out = run_cmd(
        [
            ".venv/bin/kaggle",
            "competitions",
            "team-submissions",
            str(team_id),
            "--format",
            "json",
        ]
    )
    return clean_json_output(out)


def get_submission_episodes(sub_id: int):
    out = run_cmd(
        [
            ".venv/bin/kaggle",
            "competitions",
            "episodes",
            str(sub_id),
            "--format",
            "json",
        ]
    )
    return clean_json_output(out)


def download_replay(ep_id: int) -> Path:
    target = REPLAY_DIR / f"episode-{ep_id}-replay.json"
    if not target.exists():
        run_cmd(
            [
                ".venv/bin/kaggle",
                "competitions",
                "replay",
                str(ep_id),
                "-p",
                str(REPLAY_DIR),
            ]
        )
    return target


def extract_farm_economy(farm_dict):
    animals = {}
    crops = {}
    structures = {}

    tiles = farm_dict.get("tiles", [])
    for r in range(len(tiles)):
        for c in range(len(tiles[r])):
            t = tiles[r][c]
            if not isinstance(t, dict):
                continue
            kind = t.get("kind")
            if kind in ("PASTURE", "COOP"):
                structures[kind] = structures.get(kind, 0) + 1
                anim = t.get("animal")
                if anim:
                    animals[anim] = animals.get(anim, 0) + 1
            elif kind == "PLANT":
                crop = t.get("crop")
                if crop:
                    crops[crop] = crops.get(crop, 0) + 1
            elif kind == "WEED":
                crops["WEED"] = crops.get("WEED", 0) + 1

    return {
        "money": farm_dict.get("money", 0),
        "hands": len(farm_dict.get("hands", [])),
        "quadrants": farm_dict.get("unlocked_quadrants", []),
        "animals": animals,
        "structures": structures,
        "crops": crops,
    }


def parse_replay(ep_path: Path):
    with open(ep_path) as f:
        data = json.load(f)

    team_names = data.get("info", {}).get("TeamNames", ["Agent 0", "Agent 1"])
    rewards = data.get("rewards", [0, 0])
    steps = data.get("steps", [])

    # Step at ~Day 40 (step 480) for peak economy observation
    mid_step_idx = min(480, len(steps) - 1)
    mid_obs = steps[mid_step_idx][0].get("observation", {})
    mid_farms = mid_obs.get("farms", [])

    economy_stats = []
    for farm in mid_farms:
        economy_stats.append(extract_farm_economy(farm))

    return {
        "team_names": team_names,
        "rewards": rewards,
        "mid_economy": economy_stats,
    }


def main():
    print("=== FETCHING TOP 10 LEADERBOARD TEAMS ===")
    top_teams = get_top10_teams()
    results = []

    for t in top_teams:
        t_id = t["teamId"]
        t_name = t["teamName"]
        score = t["score"]
        print(f"\nTeam: {t_name} (ID: {t_id}) | Score: {score}")

        try:
            subs = get_team_submissions(t_id)
            if not subs:
                print("  No public submissions found.")
                continue

            subs_sorted = sorted(
                subs, key=lambda s: float(s.get("publicScore", 0) or 0), reverse=True
            )
            best_sub = subs_sorted[0]
            sub_id = best_sub["id"]
            sub_score = best_sub.get("publicScore")
            print(f"  Best Sub: {sub_id} (Score: {sub_score})")

            episodes = get_submission_episodes(sub_id)
            completed_eps = [
                e
                for e in episodes
                if "COMPLETED" in str(e.get("state"))
                and "PUBLIC" in str(e.get("type"))
            ]
            print(f"  Found {len(completed_eps)} completed public episodes.")

            if completed_eps:
                latest_ep = completed_eps[0]
                ep_id = latest_ep["id"]
                print(f"  Downloading/Parsing replay ep={ep_id}...")
                rep_path = download_replay(ep_id)
                details = parse_replay(rep_path)

                p0_name = details["team_names"][0]
                p1_name = details["team_names"][1]
                p0_bank = details["rewards"][0]
                p1_bank = details["rewards"][1]

                res_entry = {
                    "rank": len(results) + 1,
                    "team": t_name,
                    "leaderboard_score": score,
                    "sub_id": sub_id,
                    "sub_score": sub_score,
                    "ep_id": ep_id,
                    "p0": {"name": p0_name, "bank": p0_bank},
                    "p1": {"name": p1_name, "bank": p1_bank},
                    "mid_economy": details["mid_economy"],
                }
                results.append(res_entry)
                print(
                    f"  Match: {p0_name} (${p0_bank:,.0f}) vs {p1_name} (${p1_bank:,.0f})"
                )
        except Exception as ex:
            print(f"  Error processing team {t_name}: {ex}")

    out_file = Path("scratch/top10_detailed_analysis.json")
    with open(out_file, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nDetailed analysis saved to {out_file}")


if __name__ == "__main__":
    main()
