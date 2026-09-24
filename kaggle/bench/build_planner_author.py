#!/usr/bin/env python
"""Generate the Benchmarks task in which a hosted model AUTHORS a season plan.

Stage W1 of `odd/tasks/planner-large.md`. Three LLM tasks in this project reasoned
from a hand-written ~5 KB prose brief and produced confident claims the engine
contradicts: the economist sized a fertilizer-buy removal at +$5,500..+$6,600 and
it measured **-$210**, because the brief paraphrased the market and dropped the
engine's post-buy quote rule (`kaggriculture.py:599-601`).

This task removes the paraphrase and the free-form answer. The model gets, in one
prompt: the complete engine source, the verified rule sheet, our ten measured
verdicts as hard constraints, and the frozen `docs/planner-parameter-schema.md`
contract. The output is one JSON object conforming to `kaggriculture-plan/1` plus
a <=600-word rationale, format-checked rather than prose-graded, and consumed
downstream by the parameterised agent (W2) instead of being believed.

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
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "kaggle" / "bench" / "kaggriculture-planner-author.py"
ENGINE_GLOB = (
    ".venv/lib/python*/site-packages/kaggle_environments/envs/kaggriculture"
    "/kaggriculture.py"
)
RULE_SHEET_PATH = REPO / "docs" / "engine-rule-sheet.md"
SCHEMA_PATH = REPO / "docs" / "planner-parameter-schema.md"

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

PART_D = '''
PART D - THE FROZEN PLAN SCHEMA (the contract)
==============================================
This document is frozen and authoritative. It defines `kaggriculture-plan/1`: the
exact field set, the exact field names, the bounds, and eight invariants that hold
regardless of the vector. Our parameterised agent consumes a plan against this
schema, so a plan that does not validate is worthless to us. Do not invent fields,
do not rename fields, and do not relax the invariants.

'''

OUTPUT_CONTRACT = '''
OUTPUT FORMAT, EXACTLY
======================
Answer with exactly one JSON object conforming to `kaggriculture-plan/1`, inside a
single fenced ```json block, followed by a rationale of at most 600 words. The
object must contain every field the schema requires, no field the schema does not
define, and no field outside its stated bounds.

'''

ASK = '''THE ASK
=======
You are the planner. Produce the plan you would bet the season on. Obey every
invariant in the schema document. Justify each number from the measured absorption
headroom and the engine rules, not from intuition. If you believe the schema is
missing a decision that matters, say so explicitly in the rationale and score it
zero rather than inventing a field.

You will not be shown any measurement of your plan, and you must not simulate
episodes yourself: this task has no engine, no reward and no grading. Your plan is
a proposal that we implement and measure separately. Its arithmetic is never
evidence.
'''

VERDICTS = '''Everything below was measured by us in this repository, against this exact engine.
The nine verdicts under MEASURED AS FAILING closed nine candidate levers; the tenth
is the most recent measurement. Treat all ten as HARD CONSTRAINTS on the plan you
are about to write: your plan may not do, assume or claim anything that one of
these measurements already refuted. Each one of them was first derived from the
price table by reasoning, and the engine contradicted that reasoning every single
time, so do not re-derive them and conclude otherwise. Where a verdict is a bound,
treat it as a ceiling, not as a prior.

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

MEASURED AS FAILING (24 paired games each unless stated)
V1  Cow-heavy roster swap at the same slots: -$9,072 per game.
    HARD: you may not claim or assume that a COW-heavier roster is free money.
    Cow-heavy was the single worst intervention we have measured. If your plan
    raises the COW count, every extra cow needs a justification other than "cows
    earn the most per structure slot", because that claim is refuted.
V2  Sheep-heavy roster swap at the same slots: +$1,933 per game at a 50% win rate.
    HARD: you may not present a SHEEP tilt as an expected gain. A 50% win rate
    with a +$1,933 mean is noise; treat any sheep rebalance as break-even at best.
V3  Goose-heavy roster swap at the same slots: -$1,998 per game.
    HARD: you may not add GOOSE capacity on the claim that eggs are cheap to
    produce. That swap lost money.
V4  Sell-lot chunking to improve realised prices (first attempt): 0 wins in 24
    games.
V5  Sell-lot chunking to improve realised prices (second attempt): 0 wins in 24
    games.
    HARD (V4 and V5): the schema exposes `market.sell_chunk_cap`, but you may not
    claim that chunking sell lots alone improves realised revenue - two
    independent attempts each won 0 of 24. The recorded cause of death is that
    chunking defunded the buy queue before day 12, which is exactly what
    `market.sell_from_day` controls: if you move `sell_chunk_cap`, you must also
    say how the pre-day-12 buy funding survives.
V6  Buy-timing arbitrage: ceiling of +$81 per game even with perfect foresight.
    HARD: no plan may claim more than +$81 per game from buy timing, and even
    that requires perfect foresight. Do not let expected value depend on it.
V7  Fertilizing melon: exactly $0. Melon tiles reach the 6-unit cap even
    unfertilized, so the cap absorbs the fertilizing.
    HARD: `fertilize` must be false for MELON, and a true value there may not be
    justified by yield. For contrast, the one fertilizing decision we know is
    productive is the tape's existing one: cancelling its 97 FERTILIZE
    applications costs -$3,078 per game, concentrated on STRAWBERRY (-128 units)
    and CARROT (-12), and EXACTLY ZERO on MELON.
V8  Adding more animals is not implementable as an overlay: the tape ends with 0
    free structure slots and only 7.4% idle worker turns.
    HARD: extra animal capacity is not free. If your plan raises an
    `animals[].count` above the measured roster (9 COW / 5 SHEEP / 3 GOOSE), you
    must state where the structure slots and the worker turns come from instead of
    assuming spare capacity exists.
V9  Adding a 4th quadrant is not implementable as an overlay, for the same reason,
    and the tape's early economy is cash-coupled: the sells inside one turn fund
    the buys that follow them in the same market queue.
    HARD: `land.quadrants: 4` is legal in the schema, but you may not assume a
    fourth quadrant is affordable or staffable by default. It needs funded
    `land.buy_days` and a roster that can actually work it, and the earlier tomato
    overlay lost a quadrant permanently by missing the step-150 (~day 6) BUY_LAND
    cliff on cash that was not there.

V10 (most recent) Removing the tape's 66 fertilizer BUYs: -$210 per game, a wash
    at 0.27% of bank, with the 97 applications untouched (97.0 before and after).
    Mechanism: the engine quotes BUY_PRODUCT at the POST-buy inventory
    (`kaggriculture.py:599-601`), so a buy/sell round trip against an unchanged
    market nets zero BY CONSTRUCTION. The tape buys 66.5 units and sells 64 fewer,
    and the round trip is designed to cancel.
    HARD: any plan that claims profit from buying a product in order to resell it
    is refuted by construction, not by sampling. BUY_PRODUCT is also accepted only
    for WHEAT and FERTILIZER, so this is the whole of the room there and it is
    worth zero.
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
    schema = read_contract(SCHEMA_PATH, "planner schema")
    return (
        "# GENERATED by kaggle/bench/build_planner_author.py - do not edit by hand.\n"
        "#\n"
        "# Stage W1 of odd/tasks/planner-large.md: the model AUTHORS a frozen\n"
        f"# kaggriculture-plan/1 vector from the complete engine source ({len(source):,}\n"
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
        '"""Ask a model to author one full-season kaggriculture-plan/1 plan."""\n'
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
        '    """Author one full-season kaggriculture-plan/1 plan."""\n'
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
    print(f"schema:    {SCHEMA_PATH.relative_to(REPO)}")
    print(f"generated: {OUT.relative_to(REPO)}  ({len(generated):,} chars)")


if __name__ == "__main__":
    main()
