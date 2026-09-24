#!/usr/bin/env python
"""Generate the Benchmarks task in which a hosted model AUTHORS a season plan.

Stage W1 of `odd/tasks/planner-large.md`. Three LLM tasks in this project reasoned
from a hand-written ~5 KB prose brief and produced confident claims the engine
contradicts: the economist sized a fertilizer-buy removal at +$5,500..+$6,600 and
it measured **-$210**, because the brief paraphrased the market and dropped the
engine's post-buy quote rule (`kaggriculture.py:599-601`).

This task removes the paraphrase and the free-form answer. The model gets, in one
prompt: the complete engine source, the verified rule sheet, our ten measured
verdicts as hard constraints, the frozen `docs/planner-parameter-schema.md`
contract, and an explicit tile budget. The output is one JSON object conforming to
`kaggriculture-plan/3` plus a <=600-word rationale, format-checked rather than
prose-graded, and consumed downstream by the parameterised agent (W2) instead of
being believed.

Two rules this file exists to enforce, both learned the hard way:

1. **The version token lives once** (`PLAN_VERSION`), and `build` fails closed when
the schema document declares a different one. A prompt asking for a version the
contract no longer accepts makes every plan worthless downstream.
2. **Every verdict line is a measured result at a stated magnitude, never advice.**
The first version of this task ended its chunking verdict with "chunking defunded
the buy queue before day 12", so BOTH models read it as "do not throttle before
day 12" and proposed `sell_chunk_cap` 4-6 - a lever that measured 0W-24L at -$5,049
even when throttled from day 12. We measured the whole lever as configured, so a
line readable as "this failed for reason R, therefore avoid R" is a defect, not a
constraint.

Run history: version 1 (schema revision 1) produced two plans that passed every
per-field bound and exposed the two gaps now in the contract - the global tile
budget (invariant 9: one plan asked for 142 cells on 75 unlocked cells) and the
explicit `animals[].count == len(buy_days)` rule. Version 2 is re-targeted at
`kaggriculture-plan/3`.

Generated rather than hand-written because the engine is ~40 KB of Python plus two
frozen markdown contracts, and embedding those by hand would rot on the next
engine or schema move. Same shape as `kaggle/bench/build_engine_spec.py`.

Usage:
    .venv/bin/python kaggle/bench/build_planner_author.py
    .venv/bin/python kaggle/bench/build_planner_author.py --check
    kaggle b t push kaggriculture-planner-author \\
        -f kaggle/bench/kaggriculture-planner-author.py --wait --poll-interval 10
    kaggle b t run kaggriculture-planner-author -m claude-opus-4-5-20251101 --wait
    kaggle b t log kaggriculture-planner-author -m gemini-3.7-flash
"""

import argparse
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "kaggle" / "bench" / "kaggriculture-planner-author.py"
ENGINE_GLOB = (
    ".venv/lib/python*/site-packages/kaggle_environments/envs/kaggriculture"
    "/kaggriculture.py"
)
RULE_SHEET_PATH = REPO / "docs" / "engine-rule-sheet.md"
SCHEMA_PATH = REPO / "docs" / "planner-parameter-schema.md"

# The one place the plan version lives. The schema document is the authority; this
# constant is the contract we ask the model to declare, and `build` fails closed if
# the two ever disagree.
PLAN_VERSION = "kaggriculture-plan/3"

INTRO = '''You are the planner for "Kaggriculture", a two-player farming simulation (30 days,
720 turns). Both players share one market and prices are a deterministic function
of market inventory. You are asked to author ONE full-season plan as a small
parameter vector - a "plan" - which our parameterised agent will execute and which
we will then measure against this engine ourselves.

Nothing in this prompt is a paraphrase. Everything you need is one of: the
engine's own source (part A), the rule sheet we extracted from that source and
verified line by line against it (part B), our measured verdicts (part C), or the
frozen schema your answer must satisfy (part D).

PART A - THE COMPLETE ENGINE SOURCE
===================================
This is the environment source, unedited, at the version we play. It is the only
authority on what your plan actually costs and earns. Read it.

'''

PART_B = '''
PART B - THE VERIFIED ENGINE RULE SHEET
=======================================
Extracted from the source above by two hosted models and then verified by us: every
row was checked at the cited line, and only verified rows are here. The models'
own line numbers were discarded, so treat any line number you read there as a hint,
never a citation. Where this sheet and your intuition disagree, the sheet wins.

'''

PART_C = '''
PART C - THE ECONOMIC TABLE AND OUR TEN MEASURED VERDICTS
=========================================================

'''

PART_D = f'''
PART D - THE FROZEN PLAN SCHEMA (the contract)
==============================================
This document is frozen and authoritative. It defines `{PLAN_VERSION}`: the exact
field set, the exact field names, the bounds, and nine invariants that hold
regardless of the vector - including invariant 9, the global tile budget, which is
checked before anything else in your answer is read. Our parameterised agent
consumes a plan against this schema, so a plan that does not validate is worthless
to us. Do not invent fields, do not rename fields, and do not relax the invariants.

'''

OUTPUT_CONTRACT = f'''
OUTPUT FORMAT, EXACTLY
======================
Answer with exactly one JSON object conforming to `{PLAN_VERSION}`, inside a single
fenced ```json block, followed by a rationale of at most 600 words. The object must
contain every field the schema requires, no field the schema does not define, and
no field outside its stated bounds.

'''

ASK = '''THE ASK
=======
You are the planner. Produce the plan you would bet the season on. Obey every
invariant in the schema document. Justify each number from the measured absorption
headroom and the engine rules, not from intuition. If you believe the schema is
missing a decision that matters, say so explicitly in the rationale and score it
zero rather than inventing a field.

CHECK THE TILE BUDGET FIRST, BECAUSE WE DO
==========================================
`sum(crops[].tiles) + sum(animals[].count)` must fit the cells your own
`land.quadrants` unlocks: 25 cells per quadrant on the default 10x10 board, so 25
cells at one quadrant, 50 at two, 75 at three, 100 at four. Count the structures
your `animals[].count` values imply, subtract them from the unlocked cells, and
spend only what remains on crop tiles. Then show that arithmetic in the rationale,
because we check it before we read anything else. A plan that does not fit is
rejected without being read. A per-field bound cannot see a farm that does not fit:
a previous plan asked for 142 cells on 75 unlocked ones and every one of its fields
was in range.

You will not be shown any measurement of your plan, and you must not simulate
episodes yourself: this task has no engine, no reward and no grading. Your plan is
a proposal that we implement and measure separately. Its arithmetic is never
evidence.
'''

VERDICTS = '''Every line below is a lever that we BUILT and MEASURED end to end against this
engine, in this repository. Each one states the lever, its sample, and its result as
a win-loss record and a dollar magnitude. Our acceptance bar for a lever was a >=65%
win rate AND a >=+$5,000 mean margin, and nothing below came within a factor of five
of that bar on margin.

Read every line as a MEASURED RESULT, never as advice, and that distinction is not
rhetorical. Each of these levers was first derived from the price table by reasoning,
and the engine contradicted that reasoning every time. But the opposite misreading
has already happened once in this prompt: a verdict saying "this lever lost money
when configured as Y" was read as "avoid Y", on the theory that the lever must have
failed for a reason a reader could sidestep. We measured the WHOLE lever as
configured. No line below names a cause you may sidestep, so "it failed for reason
R, therefore avoid R" is not supported anywhere here. Where the lever could not be
built at all, the line says NOT IMPLEMENTABLE. Where the measurement is a ceiling,
the line says BOUND.

These are hard constraints in one direction only: your plan may not claim, assume or
inherit as sanctioned anything a measurement below has already priced.

THE ECONOMIC TABLE (measured)
Price is a deterministic function of the market INVENTORY only; there is no
randomness in pricing. Price at inventory I0 = 10,000 and above:

| product    | 9800 | 9950 | 10000 | 10050 | 10100 | 12000 | base price |
|------------|------|------|-------|-------|-------|-------|------------|
| STRAWBERRY |  239 |  179 |   120 |    24 |     1 |     1 |        120 |
| MILK       |  283 |  221 |   160 |    55 |     1 |     1 |        160 |
| WOOL       |  245 |  234 |   200 |    55 |     1 |     1 |        200 |
| MELON      |  296 |  284 |   250 |   225 |   150 |     1 |        250 |
| WHEAT      |   39 |   32 |    25 |    22 |    21 |    19 |         25 |
| EGG        |   62 |   53 |    50 |    43 |    42 |    39 |         50 |
| CARROT     |   41 |   40 |    35 |    27 |    23 |     9 |         35 |
| FERTILIZER |  140 |  110 |   100 |    90 |    80 |     1 |        100 |

So the shop-drained products collapse to $1 within about 100 units of oversupply,
while WHEAT and EGG are nearly flat. SELL is quoted at the PRE-sale inventory and
executed unit by unit, so one large sell order walks its own price down.
BUY_PRODUCT is quoted at the POST-buy inventory, so a buy/sell round trip against
an unchanged market nets zero - which is why verdict V10 below exists.

THE TEN MEASURED LEVERS (24 paired games each unless stated)
V1  LEVER: the tape's roster re-payloaded to cows at the same slots. 24 paired
    games, 12 seeds x both seats.
    MEASURED: 10W-14L, a 41.7% win rate, -$9,072 mean margin per game. The sign
    agreed across the seat swap in all 12 seeds, so it is a systematic effect and
    not noise.
    Scope: that payload swap at this magnitude. It is not a claim that cows are
    unprofitable, and it prices no roster you might design differently.
V2  LEVER: the same roster re-payloaded to sheep at the same slots. 24 paired games.
    MEASURED: 12W-12L, a 50.0% win rate, +$1,933 mean margin. A genuine coin flip
    with a small positive mean, worth about 0 Elo. It failed the bar.
    Scope: that payload swap, and nothing about sheep beyond it.
V3  LEVER: the same roster re-payloaded to geese at the same slots. 24 paired games.
    MEASURED: 10W-14L, a 41.7% win rate, -$1,998 mean margin.
    Scope: as V1, the lever as configured.
V4  LEVER: cap sell lots at 4 units per order (`market.sell_chunk_cap: 4`),
    throttling from step 0. 24 paired games, 12 seeds x both seats.
    MEASURED: 0W-24L, -$61,832 mean margin per game.
    Configuration note, not a cause: in this build the roster also collapsed from 17
    animals to 9 on day 1, so this number prices the cap and that collapse together
    and does not isolate the cap.
V5  LEVER: the same 4-unit cap, throttling only from step 288 (day 12), after the
    animal purchases were complete. 24 paired games.
    MEASURED: 0W-24L, -$5,049 mean margin per game. In this build the roster was
    verified identical to the control, the farm was untouched, and the orders
    diverged only at turn 290. It lost every game anyway.
    HARD (V4 and V5): this refutes the LEVER, not the timing, and it does not
    endorse throttling afterwards. The schema exposes `market.sell_chunk_cap` and it
    is yours to set (`0` means no throttle, which is what the tape does), so a plan
    may still propose a cap. But it must then argue AGAINST a measured 0W-24L at two
    different activation days, and it does not inherit permission from day 12 being
    the smaller of the two losses: moving the throttle later did not turn a loss
    into a win, it only made the loss smaller.
V6  BOUND, not a loss: buy-timing arbitrage, measured with perfect foresight.
    MEASURED CEILING: at most +$81 per game, and that ceiling requires a foresight
    no plan has. HARD: no plan may claim more than +$81 per game from buy timing.
    This bounds the lever, and it endorses no particular buy schedule either.
V7  LEVER: fertilize melon tiles.
    MEASURED: exactly $0, in both directions. Melon tiles reach the 6-unit yield cap
    unfertilized, so the cap absorbs the fertilizing and the delta is zero at this
    magnitude.
    For scale, the fertilizing we know is productive is the tape's existing one:
    cancelling its 97 FERTILIZE applications costs -$3,078 per game, concentrated on
    STRAWBERRY (-128 units) and CARROT (-12), and exactly $0 on MELON. HARD:
    `crops[].fertilize_tiles` is a COUNT of tiles, not a boolean, it may not exceed
    that crop's `tiles`, and the MELON entry may not be justified by yield.
V8  LEVER: add animal capacity as an overlay on the fixed tape.
    MEASURED: NOT IMPLEMENTABLE. The tape ends with 0 free structure slots and only
    7.4% idle worker turns, so the overlay could not be built against that end
    state.
    Scope: the overlay method against a saturated tape. It does NOT price animals in
    a season planned around them, and it is not a reason to avoid animals. What it
    does mean is that animal capacity is not free: each structure occupies a cell
    that your tile budget has to pay for.
V9  LEVER: add a 4th quadrant as an overlay on the fixed tape.
    MEASURED: NOT IMPLEMENTABLE, for the same saturated end state, and that overlay
    was additionally killed by cash coupling - the sells inside one turn fund the
    buys that follow them in the same market queue.
    `land.quadrants: 4` is legal, costs $4,000 and unlocks 25 more cells. A related
    overlay lost a quadrant permanently by missing the step-150 (~day 6) BUY_LAND
    cliff on cash that was not there, which is the measured reason buy timing is a
    field at all.

V10 LEVER: remove the tape's 66 fertilizer BUYs. 8 paired games, 4 seeds x both
    seats - not 24.
    MEASURED: -$210 mean bank margin (79,274 -> 79,065), 0.27% of bank, with the 97
    FERTILIZE applications surviving unchanged (97.0 before and after). A wash.
    Unlike the others this one is structural rather than statistical: the engine
    quotes BUY_PRODUCT at the POST-buy inventory (`kaggriculture.py:599-601`), so a
    buy/sell round trip against an unchanged market nets zero BY CONSTRUCTION, and
    -$210 is the probe's own residual. The tape buys 66.5 units and sells 64 fewer.
    HARD: any plan that claims profit from buying a product in order to resell it is
    refuted by construction, not by sampling. BUY_PRODUCT is accepted only for WHEAT
    and FERTILIZER, so that is the whole of the room there and it is worth zero.
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


def read_schema(path: Path) -> tuple[str, str]:
    """Read the frozen plan schema and the version token it declares."""
    text = read_contract(path, "planner schema")
    declared = sorted(set(re.findall(r"kaggriculture-plan/\d+", text)))
    if declared != [PLAN_VERSION]:
        raise SystemExit(
            f"{path.relative_to(REPO)} declares {declared or 'no plan version'}, "
            f"but the output contract asks for {PLAN_VERSION}; re-target "
            "PLAN_VERSION before generating, or the plans are worthless downstream"
        )
    return text, declared[0]


def read_contract(path: Path, label: str) -> str:
    """Read a frozen markdown contract and make it embeddable in a raw literal."""
    if not path.is_file():
        raise SystemExit(f"{label} not found: {path}")
    text = path.read_text()
    if "'''" in text:
        raise SystemExit(f"{label} contains a triple single quote; embed differently")
    if text.rstrip("\n").endswith("\\"):
        raise SystemExit(f"{label} ends with a backslash; embed differently")
    return text.rstrip("\n") + "\n"


def build(engine_path: Path) -> str:
    source = engine_path.read_text()
    # A docstring is the task description and must stay short; details go in
    # comments above the task that would otherwise fail validation.
    if "'''" in source:
        raise SystemExit("engine source contains a triple single quote; embed differently")
    engine_body = source.rstrip("\n") + "\n"
    rule_sheet = read_contract(RULE_SHEET_PATH, "rule sheet")
    schema, plan_version = read_schema(SCHEMA_PATH)
    return (
        "# GENERATED by kaggle/bench/build_planner_author.py - do not edit by hand.\n"
        "#\n"
        "# Stage W1 of odd/tasks/planner-large.md: the model AUTHORS a frozen\n"
        f"# {plan_version} vector from the complete engine source ({len(source):,}\n"
        f"# chars), the verified rule sheet ({len(rule_sheet):,} chars), ten measured\n"
        f"# verdicts, and the frozen schema ({len(schema):,} chars) - not from a\n"
        "# hand-written prose brief. The plan is consumed by our parameterised agent\n"
        "# (W2) and measured against the engine by us; the model does not grade itself.\n"
        "#\n"
        "#   kaggle b t push kaggriculture-planner-author \\\n"
        "#       -f kaggle/bench/kaggriculture-planner-author.py --wait --poll-interval 10\n"
        "#   kaggle b t run kaggriculture-planner-author \\\n"
        "#       -m claude-opus-4-5-20251101 --wait --poll-interval 10\n"
        "#   kaggle b t log kaggriculture-planner-author -m gemini-3.7-flash\n"
        "\n"
        "# %%\n"
        f'"""Ask a model to author one full-season {plan_version} plan."""\n'
        "\n"
        "import kaggle_benchmarks as kbench  # type: ignore[import-not-found]\n"
        "\n"
        "# %%\n"
        "INTRO = " + repr(INTRO) + "\n"
        "\n"
        "ENGINE_SOURCE = r'''\n" + engine_body + "'''\n"
        "\n"
        "PART_B = " + repr(PART_B) + "\n"
        "\n"
        "RULE_SHEET = r'''\n" + rule_sheet + "'''\n"
        "\n"
        "PART_C = " + repr(PART_C) + "\n"
        "\n"
        "VERDICTS = " + repr(VERDICTS) + "\n"
        "\n"
        "PART_D = " + repr(PART_D) + "\n"
        "\n"
        "PLANNER_SCHEMA = r'''\n" + schema + "'''\n"
        "\n"
        "OUTPUT_CONTRACT = " + repr(OUTPUT_CONTRACT) + "\n"
        "\n"
        "ASK = " + repr(ASK) + "\n"
        "\n"
        "\n"
        "# %%\n"
        '@kbench.task(name="kaggriculture-planner-author")\n'
        "def kaggriculture_planner_author(llm):\n"
        f'    """Author one full-season {plan_version} plan."""\n'
        "    # One prompt, four parts in order: engine source, rule sheet, verdicts,\n"
        "    # schema + output contract, then the ask.\n"
        "    prompt = (\n"
        "        INTRO\n"
        "        + ENGINE_SOURCE\n"
        "        + PART_B\n"
        "        + RULE_SHEET\n"
        "        + PART_C\n"
        "        + VERDICTS\n"
        "        + PART_D\n"
        "        + PLANNER_SCHEMA\n"
        "        + OUTPUT_CONTRACT\n"
        "        + ASK\n"
        "    )\n"
        "    response = llm.prompt(prompt)\n"
        '    print("=" * 72)\n'
        '    print("PLANNER AUTHOR OUTPUT")\n'
        '    print("=" * 72)\n'
        "    print(response)\n"
        '    print("=" * 72)\n'
        "\n"
        "\n"
        "# %%\n"
        "kaggriculture_planner_author.run(kbench.llm)  # type: ignore[attr-defined]\n"
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
    print(f"engine:    {engine_path.relative_to(REPO)}")
    print(f"rules:     {RULE_SHEET_PATH.relative_to(REPO)}")
    print(f"schema:    {SCHEMA_PATH.relative_to(REPO)}  ({PLAN_VERSION})")
    print(f"generated: {OUT.relative_to(REPO)}  ({len(generated):,} chars)")


if __name__ == "__main__":
    main()
