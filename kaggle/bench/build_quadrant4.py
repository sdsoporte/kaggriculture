#!/usr/bin/env python
"""Generate the Benchmarks task that investigates the 4th quadrant.

Why this investigation exists: the 4th quadrant is the only structural lever this
project closed by ARGUMENT rather than by measurement. V9 declared it NOT
IMPLEMENTABLE because the tape's early economy is cash-coupled and the step-150
`BUY_LAND` cliff destroyed one overlay by $114. Then `scripts/quadrant4_probe.py`
measured the tape's cash curve and found that reason does not apply:

  * peak cash is a median of **$77,030**;
  * the tape holds **>= $4,000 from day 10 onward in 4/4 episodes** - SE is 5% of
    peak cash and the tape simply never asks for it;
  * the step-150 cliff is a constraint on the EARLY purchases (median $612 in cash at
    that step, for NE and SW), not on the fourth.

So the lever was mis-closed, and the real blockers are the other two: worker turns
are **7.8% idle** (92% saturated, and 25 more cells is ~+33% work) and the market is
a deterministic function of inventory, so extra volume has to land where absorption
allows.

The task therefore asks a model for a plan to FUND, STAFF and MONETISE the quadrant,
with the arithmetic shown, and for the cheapest measurement that would falsify it.

Generated rather than hand-written because the engine source is ~40 KB and the rule
sheet is a live document. Same shape as `build_engine_spec.py`.

Usage:
    .venv/bin/python kaggle/bench/build_quadrant4.py
    kaggle b t push kaggriculture-quadrant-4 -f kaggle/bench/kaggriculture-quadrant-4.py --wait
    kaggle b t run kaggriculture-quadrant-4 -m claude-opus-4-5-20251101 --wait
    kaggle b t log kaggriculture-quadrant-4 -m claude-opus-4-5-20251101
"""

import argparse
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "kaggle" / "bench" / "kaggriculture-quadrant-4.py"
ENGINE_GLOB = (
    ".venv/lib/python*/site-packages/kaggle_environments/envs/kaggriculture"
    "/kaggriculture.py"
)
RULE_SHEET = REPO / "docs" / "engine-rule-sheet.md"

BRIEF = '''You are investigating ONE question about a two-player farming simulation
("Kaggriculture": 30 days, 720 turns, two players sharing one market whose prices are
a deterministic function of market inventory). Do not summarise the game and do not
praise anything. Answer in under 1000 words, with the arithmetic shown.

THE QUESTION. The 4th quadrant of the 10x10 board is unlockable but this agent has
never bought it. Unlocking all four costs 25% more cells (100 vs 75) and the tiles
are purchased IN ORDER at fixed prices: NE 1,000, SW 2,000, **SE 4,000**. So SE is the
last and most expensive purchase. Should the agent buy it, and if so, what exactly
has to change for it to pay?

WHAT WAS ALREADY MEASURED ABOUT THIS AGENT (all from its own traces, 4 mirror
episodes; treat these as facts, not as claims to re-derive):

  * Cash peak, median $77,030 (range 63,629 .. 99,086). Cash at step 150, the step at
    which it issues BUY_LAND, median $612.
  * It holds **>= $4,000 from day 10 onward in 4 of 4 episodes**. The 4th quadrant is
    therefore FUNDABLE, and the earlier conclusion that the cash coupling forbids it
    applies only to the early NE/SW purchases, not to SE.
  * Worker turns: **7.8% are idle** (a bare PASS), 7.4%-8.2% across episodes. So the
    hands are already ~92% saturated.
  * The roster it runs is re-hired DAILY and resets every midnight; it peaks at 12
    hands. Labour is priced by Fibonacci within a day: the n-th hire of a day costs
    fib(n) with fib running 1, 1, 2, 3, 5, 8, 13, so a full 12-hand day costs 376.
  * It currently ends the season with 3 quadrants: **17 animal structures and 58 crop
    tiles standing at once** (per-crop peaks land on different days, so the standing
    figure is the meaningful one).
  * Measured absorption headroom (town drain minus this agent's own volume, in units
    per season): TOMATO +266, STRAWBERRY +253, WHEAT +291, CARROT +246, EGG +217,
    MILK +169, WOOL +10, MELON -36 (overproduced 2.2x, which is why its realised melon
    price is $189 against a $250 base). The town NEVER consumes fertilizer.
  * Ten levers have already been closed by measurement, including: removing the whole
    animal roster's payload swap (cow-heavy -$9,072, goose-heavy -$7,432), sell-lot
    chunking (0W-24L twice), cancelling the fertilizer applications (-$3,078, so the
    existing fertilizing IS productive), buying fertilizer to resell (-$210, a wash by
    the engine's own quote rule), and a tomato crop swap (refuted: the tape's watering
    choreography is crop-specific).
  * The agent is a FIXED 720-step action tape, not a re-planning policy. Any change
    has to be expressed as a change to a schedule that already exists.

YOUR TASK. Answer these five things, quantitatively:

1. PAYBACK. How many additional units of which products can the 25 new cells produce
   in the days that remain after SE becomes affordable, and what are those units worth
   at the MARGINAL price - not the base price - given the absorption headroom above?
   Show the unit count and the marginal price per product. Then state plainly whether
   $4,000 is recovered, and by how much.
2. LABOUR. 25 more cells is roughly +33% work against 7.8% idle turns. How many extra
   hand-days does the plan need, what do they cost in Fibonacci prices, and where
   exactly does that cash come from? If the arithmetic says the labour cannot be
   funded from the quadrant's own output, say so.
3. DEMAND. Name the specific product(s) the new cells must produce so that the extra
   volume lands inside absorption, and name the product(s) it must NOT produce, with
   the number from the absorption list that decides each one.
4. THE DECIDING ENGINE RULE. Which rule in the engine source most directly decides
   whether this is possible - quote the function and approximate line - and what does
   it forbid?
5. FALSIFICATION. The single cheapest measurement on this agent's own traces that
   would settle the question without building anything. Be specific about what to
   count and what result would kill the idea.

Finally: state whether your answer says BUY IT, DON'T BUY IT, or BUY IT ONLY IF, and
name the assumption your answer rests on that is most likely to be wrong.

THE ENGINE SOURCE
=================

'''


def find_engine(explicit: str | None) -> Path:
    if explicit:
        path = Path(explicit)
        if not path.is_file():
            raise SystemExit(f"engine not found: {path}")
        return path
    matches = sorted(REPO.glob(ENGINE_GLOB))
    if len(matches) != 1:
        raise SystemExit(
            f"expected exactly one engine source, found {len(matches)}: "
            f"{[str(m.relative_to(REPO)) for m in matches]}; pass --engine"
        )
    return matches[0]


def build(engine_path: Path) -> str:
    source = engine_path.read_text()
    rules = RULE_SHEET.read_text()
    for label, text in (("engine source", source), ("rule sheet", rules)):
        if "'''" in text:
            raise SystemExit(f"{label} contains a triple single quote; embed differently")
    body = source.rstrip("\n") + "\n"
    return (
        "# GENERATED by kaggle/bench/build_quadrant4.py - do not edit by hand.\n"
        "#\n"
        "# The third Benchmarks task in this project: engine reader (stage A), plan\n"
        "# author (C-Largo W1), and this one, which investigates the single lever that\n"
        "# was closed by argument instead of by measurement.\n"
        "#\n"
        "#   kaggle b t push kaggriculture-quadrant-4 \\\n"
        "#       -f kaggle/bench/kaggriculture-quadrant-4.py --wait\n"
        "#   kaggle b t run kaggriculture-quadrant-4 -m claude-opus-4-5-20251101 --wait\n"
        "#   kaggle b t log kaggriculture-quadrant-4 -m claude-opus-4-5-20251101\n"
        "\n"
        "# %%\n"
        '"""Investigate whether the 4th quadrant pays, with the arithmetic shown."""\n'
        "\n"
        "import kaggle_benchmarks as kbench  # type: ignore[import-not-found]\n"
        "\n"
        "# %%\n"
        "BRIEF = " + repr(BRIEF) + "\n"
        "\n"
        "RULE_SHEET = r'''\n" + rules.rstrip("\n") + "\n'''\n"
        "\n"
        "ENGINE_SOURCE = r'''\n" + body + "'''\n"
        "\n"
        "\n"
        "# %%\n"
        '@kbench.task(name="kaggriculture-quadrant-4")\n'
        "def kaggriculture_quadrant_4(llm):\n"
        '    """Investigate whether the 4th quadrant pays, with the arithmetic shown."""\n'
        "    response = llm.prompt(\n"
        "        BRIEF\n"
        "        + RULE_SHEET\n"
        "        + \"\\n\\nTHE RULE SHEET ALREADY VERIFIED\\n\"\n"
        "        + \"=\" * 36\n"
        "        + \"\\n\\n\"\n"
        "        + ENGINE_SOURCE\n"
        "    )\n"
        '    print("=" * 72)\n'
        '    print("4TH QUADRANT INVESTIGATION")\n'
        '    print("=" * 72)\n'
        "    print(response)\n"
        '    print("=" * 72)\n'
        "\n"
        "\n"
        "# %%\n"
        "kaggriculture_quadrant_4.run(kbench.llm)  # type: ignore[attr-defined]\n"
    )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--engine", help="path to kaggriculture.py (default: the venv copy)")
    ap.add_argument("--check", action="store_true", help="fail if OUT is stale")
    args = ap.parse_args()

    engine_path = find_engine(args.engine)
    generated = build(engine_path)

    if args.check:
        current = OUT.read_text() if OUT.is_file() else ""
        if current != generated:
            raise SystemExit(f"{OUT.relative_to(REPO)} is stale; regenerate it")
        print(f"{OUT.relative_to(REPO)} is up to date")
        return

    OUT.write_text(generated)
    print(f"engine:     {engine_path.relative_to(REPO)}")
    print(f"rule sheet: {RULE_SHEET.relative_to(REPO)}")
    print(f"generated:  {OUT.relative_to(REPO)}  ({len(generated):,} chars)")


if __name__ == "__main__":
    main()
