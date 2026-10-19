# Leaderboard snapshot — 2026-09-21 (~19:15 ART)

Source: live leaderboard via browser (9 days to go at capture time).
Captured for the phase-3 team; complements `docs/kaggriculture-research.md`.

## Field

- **9,783 teams** on the ladder; submissions flowing every 1–2h (very active).
- Rating system: Glicko-2, win-driven; final convergence ~2026-10-01.

## Top of the ladder

| # | Team | Rating | Last active |
| --- | ------ | ------- | ----------- |
| 1 | DSM (denden12, shige, masspeaks) | 3,179.9 | 10h |
| 2 | Majkel1337 | 3,076.2 | 13h |
| 3 | Vadim Vasilenko | 3,069.7 | 1h |
| 4 | Unknown Mother-Goose | 3,065.0 | 16h |
| 5 | Kaggledew Valley | 3,028.7 | 10h |
| 6 | THIRD FARM CLUB | 3,021.7 | 9h |
| 7 | SpaTaro | 3,013.4 | — |
| ~17 | — | 2,948.0 | |
| ~30 | — | ~2,894 | |
| 50 | Ugnė Miklovaitė | 2,852.4 | |

## Our standing

- **Rank 9,259 / 9,783 — "Sergio D" — rating 191.3** (active 2h before capture).
- This is the phase-2 pipeline-validation submission (baseline_v0, wheat-only
  Walter-fixed). The entry system works end-to-end; the rating is the
  expected floor for v0, not a policy signal.

## Analysis

1. **Glicko inflation confirmed.** The August research measured top tapes at
   ~2,500–2,600; the current #1 sits at 3,179.9. Win-driven rating plus
   ~9.8k teams inflated the ladder ~500–600 points, exactly as the research
   predicted (beating the mid-field pays).
2. **Extreme compression at the top.** Rank 1 → rank 50 spans only ~327
   points — the frontier-program clone flood homogenized the summit (the
   single plant-mix fingerprint covering 87.8% of winners in the research).
3. **Strategic read.** Chasing tapes is fragile (one engine patch kills
   them) and the only documented tape-beater was the detector-gated
   parameter switch. The project's current path — own adaptive policy,
   labor scheduler, paired benchmarks — matches the rational play for the
   remaining ~9 days.
4. **Realistic near-term target:** mid-ladder with v2 (rotation +
   scheduler). The qualitative jump (fingerprinting / gated switch) is
   post-final work or a stretch goal if C4–C5 close early.

## Next checkpoints

- Re-capture after the C5 full-ladder run to place v2 on the curve.
- Watch for the leaderboard-freeze / convergence window around 2026-10-01;
  early ratings are noisy (small pairing samples).
- Any engine patch notes from the organizers: tape fragility cuts both
  ways — it would also reshuffle the summit we are climbing into.
