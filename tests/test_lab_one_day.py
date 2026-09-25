"""Tests for the Lab builder's one-day guard and deterministic generation.

The Lab kernel is generated, not hand-edited, so these tests guard the generator:
it must refuse metadata that attaches zero or several daily episode datasets, the
generated notebook must be deterministic and parse, and the embedded extractor
must be the library source with no CLI entry point that a notebook cell would run.
"""

import ast
import collections
import contextlib
import importlib.util
import io
import json
import re
import runpy
import statistics
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
BUILDER_PATH = ROOT / "kaggle" / "lab" / "build_notebook.py"
METADATA_PATH = ROOT / "kaggle" / "lab" / "kernel-metadata.json"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None, f"cannot load {path}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


builder = _load("kaggle_lab_build_notebook", BUILDER_PATH)


def _metadata(dataset_sources):
    return {"dataset_sources": dataset_sources}


def test_real_metadata_attaches_exactly_one_daily_source():
    # Date-agnostic on purpose: the tracked metadata names whichever day the next
    # run targets, and the guard must resolve exactly that day. The day itself is
    # not asserted; exactly one RAW daily source is (a duplicate mount fails).
    metadata = json.loads(METADATA_PATH.read_text())
    found = builder.daily_sources_in_metadata(metadata)
    assert len(found) == 1, f"one daily date only, found {sorted(found)}"
    (date,) = found
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", date), date
    assert builder.assert_one_daily_source(metadata) == date


def test_real_metadata_keeps_non_daily_sources():
    metadata = json.loads(METADATA_PATH.read_text())
    sources = metadata["dataset_sources"]
    assert "kaggle/kaggriculture-episodes-index" in sources
    assert "raykkretzschmar/kaggriculture-reference-agents" in sources
    assert "ser8147/kaggriculture-engine-tables" in sources
    # Exactly one daily date survives, or the run would pool two days (or read
    # the same day twice). The attached daily entries must all belong to it.
    found = builder.daily_sources_in_metadata(metadata)
    assert len(found) == 1, f"one daily date only, found {sorted(found)}"
    date = next(iter(found))
    assert [source for source in sources if date in source] == found[date]


def test_builder_rejects_two_daily_sources():
    with pytest.raises(SystemExit):
        builder.assert_one_daily_source(_metadata([
            "kaggle/kaggriculture-episodes-2026-09-23",
            "kaggle/kaggriculture-episodes-2026-09-24",
        ]))


def test_builder_rejects_two_same_date_daily_sources():
    # Two matching mounts of the same day must not collapse to one date.
    with pytest.raises(SystemExit):
        builder.assert_one_daily_source(_metadata([
            "kaggle/kaggriculture-episodes-2026-09-24",
            "kaggle/kaggriculture-episodes-2026-09-24",
        ]))
    with pytest.raises(SystemExit):
        builder.assert_one_daily_source(_metadata([
            "kaggle/kaggriculture-episodes-index",
            "kaggle/kaggriculture-episodes-2026-09-24",
            "ser8147/kaggriculture-episodes-2026-09-24",
        ]))


def test_builder_rejects_zero_daily_sources():
    with pytest.raises(SystemExit):
        builder.assert_one_daily_source(_metadata([
            "kaggle/kaggriculture-episodes-index",
            "ser8147/kaggriculture-engine-tables",
        ]))


def test_builder_ignores_non_daily_sources_when_counting():
    assert builder.assert_one_daily_source(_metadata([
        "kaggle/kaggriculture-episodes-index",
        "kaggle/kaggriculture-episodes-2026-09-24",
        "raykkretzschmar/kaggriculture-reference-agents",
        "ser8147/kaggriculture-engine-tables",
    ])) == "2026-09-24"


def test_econ_source_excludes_the_cli_entry_point():
    source = builder.econ_source()
    assert "def require_exactly_one_day" in source
    assert "def extract_match" in source
    assert "if __name__" not in source
    assert "def main(" not in source


def test_generated_cells_are_deterministic_and_parse():
    first = json.dumps(builder.build_cells(), indent=1)
    second = json.dumps(builder.build_cells(), indent=1)
    assert first == second
    for index, cell in enumerate(builder.build_cells()):
        if cell["cell_type"] != "code":
            continue
        ast.parse("".join(cell["source"]), filename=f"<cell {index}>")


def test_notebook_contains_the_guard_and_the_extractor():
    cells = builder.build_cells()
    code_text = "\n".join("".join(cell["source"]) for cell in cells
                          if cell["cell_type"] == "code")
    assert "require_exactly_one_day" in code_text
    assert "OneDayGuardError" in code_text
    assert "render_report" in code_text
    assert "fold_match" in code_text


def test_extractor_cell_precedes_the_guard_that_uses_it():
    # The guard calls require_exactly_one_day, so the cell that defines it must
    # execute first; otherwise the notebook dies with NameError before Tier 1.
    cells = builder.build_cells()
    def_index = next(index for index, cell in enumerate(cells)
                     if "def require_exactly_one_day" in "".join(cell["source"]))
    guard_index = next(index for index, cell in enumerate(cells)
                       if "require_exactly_one_day(sorted(candidate_dirs))"
                       in "".join(cell["source"]))
    assert def_index < guard_index


def test_tier2_cell_asserts_the_mounted_day_and_refuses_to_truncate():
    # One day mounted means all its replays are the sample: the cell must fail if
    # the full-parse pass discovers another day or if the cap would cut the day.
    assert "require_expected_day(t2_days, DATA_DATE)" in builder.TIER2
    assert "require_within_cap(len(plan), MAX_REPLAYS_T2, DATA_DATE)" in builder.TIER2


def test_tier1_output_has_no_hardcoded_submission_ids():
    # Active submission ids go stale; the Tier-1 closing note must stay
    # time-independent instead of naming a score that no longer holds.
    assert "submissions = {" not in builder.TIER1_INDEX
    assert "our active submissions" not in builder.TIER1_INDEX
    assert "56513137" not in builder.TIER1_INDEX
    assert "56501811" not in builder.TIER1_INDEX


def test_generated_notebook_written_by_main_matches_build_cells(tmp_path, monkeypatch):
    # Point the builder at a temp output so the test does not touch the tracked
    # notebook, then confirm the on-disk JSON is exactly what build_cells returns.
    out = tmp_path / "lab.ipynb"
    monkeypatch.setattr(builder, "OUT", out)
    builder.main()
    written = json.loads(out.read_text())
    assert written["nbformat"] == 4
    assert len(written["cells"]) == len(builder.build_cells())
    assert written["cells"][0]["cell_type"] == "markdown"


# --- Tier-2 report branch behaviour -----------------------------------------
# These execute the real TIER2_REPORT source with synthetic buckets, so the branch
# is exercised, not merely grepped. The report only reads the aggregate scalars the
# processing cell fills, so a hand-built bucket is a faithful stand-in.


def _tier2_bucket():
    return {
        "games": 100,
        "seat_quad": {2: [10, 100], 3: [55, 100], 4: [60, 100]},
        "diff_n": 100,
        "diff_more": 70,
        "unlock": {4: [10, 11, 12]},
        "four_gap": [1000.0, -2000.0],
        "winner_vals": collections.defaultdict(list, {m: [3] * 100
                                                      for m in ("hires", "land")}),
        "loser_vals": collections.defaultdict(list, {m: [1] * 100
                                                     for m in ("hires", "land")}),
        "winner_more": collections.Counter({"hires": 80, "land": 60}),
        "winner_less": collections.Counter({"hires": 20, "land": 40}),
        "margins": [500.0] * 100,
        "relative": [0.1] * 100,
    }


def _run_tier2_report(tmp_path, days, newest):
    # Run the generated report cell as a module file: this executes the real
    # dispatch at the bottom of the cell, so both branches are exercised rather
    # than grepped. The globals the processing cell provides are injected through
    # runpy's init_globals instead of being concatenated into the source.
    namespace = {
        "collections": collections,
        "statistics": statistics,
        "T2_METRICS": ("hires", "land"),
        "t2_days": list(days),
        "t2_newest": newest,
        "t2_day_buckets": {day: _tier2_bucket() for day in days},
        "t2_pool": _tier2_bucket(),
    }
    cell = tmp_path / "tier2_report_cell.py"
    cell.write_text(builder.TIER2_REPORT)
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        runpy.run_path(str(cell), init_globals=namespace)
    return buffer.getvalue()


def test_tier2_same_sample_day_is_labelled_and_gets_no_replication_verdict(tmp_path):
    # The 09-23 run mounts the very corpus LOCAL_0923 summarizes. The branch must
    # say so and must not print an out-of-sample header or any CONFIRMS-family
    # verdict, because comparing the day with itself can confirm nothing.
    out = _run_tier2_report(tmp_path, ["2026-09-23"], "2026-09-23")
    assert "TIER 2.1 - DAY 2026-09-23 ALONE (same-sample calibration" in out
    assert "SAME-SAMPLE calibration, not a replication" in out
    assert "OUT-OF-SAMPLE" not in out
    assert "CONFIRMS" not in out
    assert "SHRINKS" not in out
    assert "CONTRADICTS" not in out
    assert "verdict, this day" not in out
    # No cross-day claim from a single day: the caveat must be present.
    assert "cross-day stability cannot be inferred from one mounted day" in " ".join(
        out.split())


def test_tier2_independent_day_keeps_the_out_of_sample_verdict(tmp_path):
    # 09-24 is a genuinely different day, so the original out-of-sample comparison
    # and its CONFIRMS / SHRINKS / CONTRADICTS verdict must survive unchanged.
    out = _run_tier2_report(tmp_path, ["2026-09-24"], "2026-09-24")
    assert "TIER 2.1 - DAY 2026-09-24 ALONE (out-of-sample replication)" in out
    assert "OUT-OF-SAMPLE" in out
    assert "SAME-SAMPLE" not in out
    assert "verdict, this day against the local 2026-09-23 finding: " in out
    assert any(token in out for token in
               ("CONFIRMS", "SHRINKS", "CONTRADICTS", "INDETERMINATE"))


def test_tier2_single_day_run_skips_the_redundant_pool(tmp_path):
    # One day mounted means the pool IS that day, so it must be skipped, not
    # printed as a second sample.
    out = _run_tier2_report(tmp_path, ["2026-09-23"], "2026-09-23")
    assert "TIER 2.2 - POOL 2026-09-23 .. 2026-09-23 (SKIPPED:" in out
    assert "pool would repeat that same day" in out


def test_tier2_multi_day_run_still_prints_the_pool(tmp_path):
    # With two days the pool is a real wider sample and must be reported again.
    out = _run_tier2_report(tmp_path, ["2026-09-23", "2026-09-24"], "2026-09-24")
    assert "TIER 2.1 - DAY 2026-09-24 ALONE (out-of-sample replication)" in out
    assert "TIER 2.2 - POOL 2026-09-23 .. 2026-09-24" in out
    assert "SKIPPED" not in out
    assert "cross-day stability cannot be inferred" not in out


# --- shop-window extractor embedding -----------------------------------------

def _top_level_names(source):
    """Names a cell defines or assigns at top level, as a set.

    Import binds are excluded on purpose: both embedded sources import the same
    stdlib modules, so an ``import`` name is the very same object in both cells
    and cannot shadow a different implementation. ``from __future__ import
    annotations`` cannot even be aliased. Every other top-level name is a
    distinct binding, and those are the ones that can silently re-shadow.
    """
    names = set()
    for node in ast.parse(source).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            names.update(target.id for target in node.targets
                         if isinstance(target, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return names


def test_embedded_extractors_share_no_top_level_name():
    # Both cells execute in ONE notebook namespace, cell 7 (shop) after cell 5
    # (econ). A shared name means the ECON_REPORT cell calls the SHOP renderer -
    # that is the v11 crash (KeyError 'demand_table', econ_summary has no
    # demand_table) - and shop's helpers silently replace the econ ones during
    # the same Tier-2 fold. So the two embedded sources must define no name twice.
    econ = _top_level_names(builder.econ_source())
    shop = _top_level_names(builder.shop_source())
    assert econ & shop == set(), f"shared top-level names: {sorted(econ & shop)}"


def test_shop_source_excludes_the_cli_entry_point():
    source = builder.shop_source()
    assert "def shop_timeline" in source
    assert "def shop_executed_sales" in source
    assert "def shop_render_report" in source
    assert "if __name__" not in source
    assert "def main(" not in source


def test_shop_cell_precedes_tier2_and_the_report_follows_econ():
    # The shop extractor must be defined before Tier 2 calls it, and its report
    # must be printed after the economic report, both from the SAME pass.
    cells = builder.build_cells()
    shop_def = next(index for index, cell in enumerate(cells)
                    if "def shop_timeline" in "".join(cell["source"]))
    tier2 = next(index for index, cell in enumerate(cells)
                 if "shop_fold_match(shop_summary, _shop)" in "".join(cell["source"]))
    econ_report = next(index for index, cell in enumerate(cells)
                       if 'render_report(econ_summary, DATA_DATE)' in "".join(cell["source"]))
    shop_report = next(index for index, cell in enumerate(cells)
                       if 'shop_render_report(shop_summary, DATA_DATE)'
                       in "".join(cell["source"]))
    assert shop_def < tier2
    assert econ_report < shop_report
    # Only the shop study's markdown sits between the two reports: no other code.
    between = [cell for cell in cells[econ_report + 1:shop_report]
               if cell["cell_type"] == "code"]
    assert between == []


def test_tier2_cell_folds_both_extractors_in_one_pass():
    # One read of a replay feeds the economic AND the shop report: the Tier-2 cell
    # creates both accumulators and folds both inside the same loop.
    assert "shop_new_summary(shop_table)" in builder.TIER2
    assert "shop_extract_match(steps, teams=_teams)" in builder.TIER2
    assert "shop_fold_match(shop_summary, _shop)" in builder.TIER2
    assert "shop_load_demand_table(shop_demand_path)" in builder.TIER2
    assert "shop_render_report(shop_summary, DATA_DATE)" in builder.SHOP_REPORT
    assert "shop_summary[\"skipped\"].update(t2_skipped)" in builder.SHOP_REPORT


# --- shared-kernel-namespace simulation (the v12 SHOP_REPORT crash) ----------
# Every cell of this notebook runs in ONE kernel namespace, so a top-level name a
# later cell binds is visible to the functions an earlier cell defined. The v12 run
# died at the SHOP_REPORT cell with "TypeError: 'str' object is not callable"
# because the Tier-2 loop header
#     for _cut, (_day, _path) in enumerate(sample, 1)
# left _day bound to the day STRING in that shared namespace, overwriting the shop
# module's _day formatter, so shop_render_report eventually called a str. The
# economic report printed fine. The AST guard above cannot see this: it compares
# only the two embedded sources, never the names the other cells leave behind.
#
# This test closes that gap. It executes the two embedded cells in one namespace in
# notebook order, binds every top-level name the OTHER cells bind (harvested from
# their real sources, never hand-listed, so a new cell binding is covered
# automatically) plus the real Tier-2 loop header, and only then calls BOTH
# renderers. A shadowed helper surfaces as the crash it caused in production.


def _top_level_bound_names(source):
    """Top-level names a cell binds to a VALUE: the AST guard's set, plus loops.

    Import binds are excluded for the same reason as in ``_top_level_names``: both
    embedded sources import the same stdlib modules, so that name is the same
    object in every cell and cannot shadow a different implementation. Loop,
    ``with`` and augmented-assignment targets ARE included, because those are the
    bindings that are easy to forget: the v12 crash was a loop target.
    """
    names = set()

    def add_target(target):
        if isinstance(target, ast.Name):
            names.add(target.id)
        elif isinstance(target, (ast.Tuple, ast.List)):
            for element in target.elts:
                add_target(element)

    for node in ast.parse(source).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                add_target(target)
        elif (isinstance(node, (ast.AnnAssign, ast.AugAssign))
              and isinstance(node.target, ast.Name)):
            names.add(node.target.id)
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            add_target(node.target)
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                if item.optional_vars is not None:
                    add_target(item.optional_vars)
    return names


def _synthetic_shop_summary():
    """A minimal shop accumulator holding one BAKERY-seeded match, hand-built.

    Hand-built on purpose: this test must run against both the shadowed and the
    fixed cell, so it cannot depend on the accumulator factory's spelling. The
    shape mirrors the keys the report reads; ``tests/test_corpus_shop_windows.py``
    pins the factory that really produces it.
    """
    return {
        "parsed": 1,
        "matches": 1,
        "seats": 2,
        "ties": 0,
        "skipped": collections.Counter(),
        "demand_table": {"present": False, "path": None, "rates": {},
                         "shop_rows": 0, "town_rows": 0, "mismatches": [],
                         "missing": [], "unknown": []},
        "matched_teams": {"A", "B"},
        "final_instances": [1, 1],
        "shop_instance_counts": collections.Counter({"BAKERY": 1}),
        "shop_seats": collections.Counter({"BAKERY": 2}),
        "shop_unlock_days": collections.defaultdict(list, {"BAKERY": [2, 2]}),
        "duplicate_seats": 0,
        "expected_product": {"WHEAT": [6.0, 1], "EGG": [6.0, 1]},
        "sold_product": {"WHEAT": [10, 10]},
        "window_product": {"WHEAT": {"sold": [10], "share": [0.6]}},
        "shop_units": [10],
        "non_shop_units": [0],
        "window": {"both_defined": 1, "one_defined": 0, "none_defined": 0,
                   "winner_more": 1, "loser_more": 0, "tie": 0,
                   "winner_share": [0.6], "loser_share": [0.2],
                   "winner_sold": [10], "loser_sold": [6]},
        "split": {"winner_shop": [10], "loser_shop": [6], "winner_non": [0],
                  "loser_non": [0], "winner_more_shop": 1, "loser_more_shop": 0,
                  "tie_shop": 0, "winner_more_non": 0, "loser_more_non": 0,
                  "tie_non": 1},
        "yarn": {"seats_with_yarn": 1, "seats_without": 1, "on_units": 5,
                 "off_units": 3, "on_vals": [5], "off_vals": [3]},
    }


def test_renderers_survive_the_shared_notebook_namespace():
    cells = builder.build_cells()
    econ_index = next(index for index, cell in enumerate(cells)
                      if "".join(cell["source"]) == builder.econ_source())
    shop_index = next(index for index, cell in enumerate(cells)
                      if "".join(cell["source"]) == builder.shop_source())
    assert econ_index < shop_index
    # The real loop header this simulation reproduces. If Tier 2 stops using it,
    # this test must be updated rather than silently weakened.
    assert "for _cut, (_day, _path) in enumerate(sample, 1)" in builder.TIER2

    namespace = {"__name__": "notebook-namespace-simulation"}
    for index in (econ_index, shop_index):
        exec(compile("".join(cells[index]["source"]), f"<cell {index}>", "exec"),
             namespace)

    shadows = set()
    for index, cell in enumerate(cells):
        if cell["cell_type"] != "code" or index in (econ_index, shop_index):
            continue
        shadows |= _top_level_bound_names("".join(cell["source"]))
    assert shadows, "the other cells must bind something or this simulates nothing"

    # Look the callables up BEFORE the shadow cell: a shadowed *name* must not stop
    # the test from reaching the failure it is about. Shadowing still reaches the
    # helpers they call, because a function's __globals__ IS the shared namespace.
    render_econ = namespace["render_report"]
    render_shop = namespace["shop_render_report"]
    new_econ_summary = namespace["new_summary"]

    shadow_source = "\n".join(f'{name} = "shadowed-by-another-cell"'
                              for name in sorted(shadows))
    shadow_source += ("\nfor _cut, (_day, _path) in enumerate([(\"2026-09-24\", \"p\")], 1):"
                      "\n    pass\n")
    exec(compile(shadow_source, "<synthetic tier-2 cell>", "exec"), namespace)

    econ_report = render_econ(new_econ_summary(), "2026-09-24")
    shop_report = render_shop(_synthetic_shop_summary(), "2026-09-24")
    assert "DAILY ECONOMIC EVENT STUDY - dataset date 2026-09-24" in econ_report
    assert "SHOP-WINDOW DEMAND ANALYSIS - dataset date 2026-09-24" in shop_report
    assert "BAKERY" in shop_report and "WHEAT" in shop_report
    assert len(econ_report.splitlines()) > 5
    assert len(shop_report.splitlines()) > 5


def test_shop_top_level_names_all_carry_the_shop_prefix():
    # The shop cell shares ONE kernel namespace with every other cell, so every
    # top-level name it defines must be unmistakably shop-owned. `_day`, `_share`,
    # `holdings` or `final_money` are names any other cell can re-bind by accident,
    # which is the whole failure class. ShopSkip is the single exception: it is the
    # exception class the Tier-2 cell catches and is already Shop-prefixed.
    offenders = sorted(
        name for name in _top_level_names(builder.shop_source())
        if name != "ShopSkip" and not name.startswith(("shop_", "SHOP_", "_shop_")))
    assert offenders == [], f"shop cell defines un-namespaced top-level names: {offenders}"
