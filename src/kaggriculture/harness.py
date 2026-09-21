"""Paired benchmarking: same seeds, both seats, to cancel first-seat bias.

For every seed we play [A, B] and [B, A]. All aggregates are from A's
perspective: a win means A's bank beat B's bank in that game, and the margin
is A's bank minus B's bank.
"""

from kaggriculture.runner import run_episode


def paired_benchmark(agent_a, agent_b, seeds):
    """Run the paired benchmark and return a report dict.

    Report shape:
        games, wins, losses, ties     -- over all 2 * len(seeds) games, A's perspective
        mean_margin                   -- mean of (bankA - bankB) over all games
        mean_margin_by_seat           -- {"seat0": ..., "seat1": ...} for A
        per_seed                      -- [{seed, ab_banks, ba_banks, margins}]
    """
    per_seed = []
    margins = []
    margins_by_seat = {"seat0": [], "seat1": []}
    for seed in seeds:
        ab = run_episode(agent_a, agent_b, seed)  # A on seat 0
        ba = run_episode(agent_b, agent_a, seed)  # A on seat 1
        ab_margin = ab["banks"][0] - ab["banks"][1]
        ba_margin = ba["banks"][1] - ba["banks"][0]
        margins += [ab_margin, ba_margin]
        margins_by_seat["seat0"].append(ab_margin)
        margins_by_seat["seat1"].append(ba_margin)
        per_seed.append({
            "seed": seed,
            "ab_banks": ab["banks"],               # (A, B) with A on seat 0
            "ba_banks": (ba["banks"][1], ba["banks"][0]),  # (A, B) with A on seat 1
            "margins": (ab_margin, ba_margin),
        })

    wins = sum(1 for m in margins if m > 0)
    losses = sum(1 for m in margins if m < 0)
    ties = len(margins) - wins - losses
    return {
        "games": len(margins),
        "wins": wins,
        "losses": losses,
        "ties": ties,
        "mean_margin": sum(margins) / len(margins),
        "mean_margin_by_seat": {
            seat: sum(ms) / len(ms) for seat, ms in margins_by_seat.items()
        },
        "per_seed": per_seed,
    }
