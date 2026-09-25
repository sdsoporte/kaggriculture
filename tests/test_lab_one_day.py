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
