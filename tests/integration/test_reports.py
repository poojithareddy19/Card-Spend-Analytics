"""The reports and the dashboard.

Nine reports and a self-contained HTML dashboard shipped with no test of any kind, which made them
the largest untested surface in the repo and the reason the coverage gate had never been met. They
are also the part a stakeholder actually looks at, so an error here is the one that gets seen.

The assertions are about the promises the README makes for this layer rather than about pixels:
every report declares the date basis it uses, the dashboard carries no external reference, and every
chart has a table under it.
"""

from __future__ import annotations

import csv
import re
from pathlib import Path

import pandas as pd
import pytest

from card_spend.reports.build import build_reports, run_reports
from card_spend.reports.queries import REPORTS

pytestmark = pytest.mark.slow


@pytest.fixture(scope="module")
def built(pipeline, tmp_path_factory: pytest.TempPathFactory) -> dict[str, object]:
    out = tmp_path_factory.mktemp("reports")
    return {"root": Path(str(pipeline["root"])), "out": out, "report": build_reports(Path(str(pipeline["root"])), out)}


def test_every_declared_report_runs_and_returns_rows(built: dict[str, object]) -> None:
    frames = run_reports(Path(str(built["root"])))
    assert set(frames) == {r.key for r in REPORTS}
    empty = sorted(key for key, frame in frames.items() if frame.empty)
    assert not empty, f"reports that produced no rows at dev scale: {empty}"


def test_every_report_declares_its_date_basis_and_an_owner() -> None:
    """Mixing posting date with authorisation date is the bug this metadata exists to prevent."""
    for report in REPORTS:
        assert report.date_basis, f"{report.key} does not say which date it counts on"
        assert report.question, f"{report.key} does not say what question it answers"
        assert "@" in report.owner, f"{report.key} has no owner to ask about it"


def test_a_csv_lands_for_every_report(built: dict[str, object]) -> None:
    out = Path(str(built["out"]))
    for report in REPORTS:
        path = out / "csv" / f"{report.key}.csv"
        assert path.exists(), f"no CSV for {report.key}"
        with path.open(encoding="utf-8") as fh:
            rows = list(csv.reader(fh))
        assert len(rows) >= 2, f"{report.key} wrote a header and nothing else"


def test_the_dashboard_is_self_contained(built: dict[str, object]) -> None:
    """No chart library and no CDN. A dashboard that needs the network is not a deliverable."""
    html = (Path(str(built["out"])) / "dashboard.html").read_text(encoding="utf-8")
    external = re.findall(r"""(?:src|href)\s*=\s*["'](https?://[^"']+)""", html)
    assert not external, f"dashboard reaches out to the network: {external}"
    assert "<svg" in html, "the charts are meant to be inline SVG"


def test_every_chart_has_a_table_under_it(built: dict[str, object]) -> None:
    """A chart nobody can read the numbers off is decoration."""
    html = (Path(str(built["out"])) / "dashboard.html").read_text(encoding="utf-8")
    assert html.count("<table") >= html.count("<svg"), "there are more charts than tables"


def test_the_headline_matches_the_spend_report(built: dict[str, object]) -> None:
    """Two reports computed independently must agree, or the dashboard contradicts itself."""
    frames = run_reports(Path(str(built["root"])))
    headline = frames["headline"].iloc[0]
    by_month = frames["spend_by_month"]["spend_gbp"].sum()
    assert headline["spend_gbp"] == pytest.approx(by_month, rel=1e-6)


def test_re_running_the_reports_changes_nothing(built: dict[str, object]) -> None:
    """`out/` is committed, so a number that drifts turns every build into a diff.

    The FX revaluation sums a float product, and DuckDB is configured not to preserve insertion
    order, so it aggregates in parallel and the last digits moved between runs: 12948.66487831 one
    time, 12948.664878309997 the next. The money columns are rounded at the query to stop that, and
    this is the test that notices if a new report reintroduces it.
    """
    first = run_reports(Path(str(built["root"])))
    second = run_reports(Path(str(built["root"])))
    for key in first:
        pd.testing.assert_frame_equal(first[key], second[key], check_exact=True, obj=key)


def test_the_build_report_counts_what_it_wrote(built: dict[str, object]) -> None:
    report = built["report"]
    assert isinstance(report, dict)
    assert set(report["reports"]) == {r.key for r in REPORTS}
    assert all(count > 0 for count in report["reports"].values())
    assert Path(str(report["dashboard"])).exists()
