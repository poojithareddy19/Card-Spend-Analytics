"""The benchmark harness.

The published numbers in `docs/benchmarks/` come out of this code, which made it the least defensible
untested module in the repo: a benchmark nobody checks is a number nobody should believe.

These tests do not assert timings. Timings flap, and the repo already argues at length that plan
shape is the thing worth gating. What is asserted is the harness's own contract: every pattern is
attempted against every store, a store that cannot answer says so rather than being dropped from the
table, and the scale every figure was measured on is recorded alongside it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from card_spend.bench.harness import _percentile, render_markdown, run_benchmark
from card_spend.stores.base import PATTERNS

pytestmark = pytest.mark.slow


@pytest.fixture(scope="module")
def benched(pipeline, tmp_path_factory: pytest.TempPathFactory) -> dict[str, object]:
    out = tmp_path_factory.mktemp("bench") / "results.md"
    markdown = run_benchmark(Path(str(pipeline["root"])), repeats=1, out_path=out)
    return {"markdown": markdown, "out": out, "report": json.loads(out.with_suffix(".json").read_text("utf-8"))}


def test_every_pattern_is_attempted_against_every_store(benched: dict[str, object]) -> None:
    report = benched["report"]
    assert isinstance(report, dict)
    pairs = {(t["pattern"], t["store"]) for t in report["timings"]}
    expected = {(p.key, store) for p in PATTERNS for store in ("redis", "postgres", "duckdb")}
    assert pairs == expected


def test_a_store_that_cannot_answer_says_so_rather_than_being_omitted(benched: dict[str, object]) -> None:
    """Quietly dropping a row would turn "cannot do this" into "was never asked"."""
    report = benched["report"]
    assert isinstance(report, dict)
    gaps = [t for t in report["timings"] if not t["supported"]]
    assert gaps, "every store answered every pattern, which contradicts the whole premise"
    assert all(t["note"] for t in gaps), "a gap was recorded with no reason given"


def test_the_columnar_store_answers_every_pattern(benched: dict[str, object]) -> None:
    """DuckDB reads the lake directly, so it needs no server and must always be measurable."""
    report = benched["report"]
    assert isinstance(report, dict)
    duck = {t["pattern"]: t for t in report["timings"] if t["store"] == "duckdb"}
    assert set(duck) == {p.key for p in PATTERNS}
    assert all(t["supported"] and t["rows"] > 0 for t in duck.values()), duck


def test_the_scale_every_figure_was_measured_on_is_recorded(benched: dict[str, object]) -> None:
    report = benched["report"]
    assert isinstance(report, dict)
    assert report["transactions"] > 0
    assert report["customers"] > 0
    assert report["partitions"] > 0
    assert report["machine"]["duckdb"], "the engine version is part of the measurement"


def test_the_markdown_names_every_pattern_and_every_store(benched: dict[str, object]) -> None:
    markdown = str(benched["markdown"])
    for pattern in PATTERNS:
        assert pattern.title in markdown, f"{pattern.key} is missing from the published table"
    for store in ("redis", "postgres", "duckdb"):
        assert store in markdown


def test_the_percentile_helper_holds_at_the_edges() -> None:
    """It exists to produce p95, which is the number the results table publishes beside p50.

    Nearest rank, not interpolation: every value it returns is one that was actually measured, which
    is the property that matters when the figure ends up in a document someone argues about.
    """
    assert _percentile([], 95) == 0.0, "no samples must not blow up mid-benchmark"
    assert _percentile([7.0], 95) == 7.0
    assert _percentile([4.0, 1.0, 3.0, 2.0], 100) == 4.0, "the top percentile is the slowest run"
    assert _percentile([4.0, 1.0, 3.0, 2.0], 0) == 1.0, "the bottom percentile is the fastest run"

    sample = [float(n) for n in range(1, 21)]
    assert _percentile(sample, 95) in sample, "p95 must be a measured value, not an interpolated one"
    assert _percentile(sample, 50) <= _percentile(sample, 95)


def test_rendering_is_a_pure_function_of_the_report(benched: dict[str, object]) -> None:
    """The markdown written to disk and the markdown returned must not diverge."""
    assert str(benched["markdown"]) == Path(str(benched["out"])).read_text(encoding="utf-8")


def test_the_report_round_trips_through_json(benched: dict[str, object]) -> None:
    from card_spend.bench.harness import BenchmarkReport

    report = benched["report"]
    assert isinstance(report, dict)
    rebuilt = BenchmarkReport(**report)
    assert render_markdown(rebuilt) == str(benched["markdown"])
