"""How good are the data quality rules, measured rather than asserted.

Most data quality work stops at "the rule fires". This asks the harder question: of the defects that
were actually injected, how many does each rule catch, and how many of the rows it flags were never
defective in the first place. The generator writes a defect ledger of
``(transaction_id, defect_code)``, so recall and precision are computable rather than arguable.

Five of the six rules are deterministic and must be perfect. The sixth is a statistical check on a
defect that is genuinely hard to see in a single row, and it has a floor rather than a target. The
numbers this test produces are the ones quoted in `docs/product/04_incident_rca.md`.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

CONTRACTS = Path(__file__).resolve().parents[2] / "contracts"
pytestmark = pytest.mark.slow

# (defect code, fact column, minimum recall, minimum precision)
RULES = [
    ("D01", "dq_missing_merchant", 1.0, 1.0),
    ("D03", "dq_orphan_card", 1.0, 1.0),
    ("D04", "dq_negative_purchase", 1.0, 1.0),
    ("D05", "dq_bad_mcc", 1.0, 1.0),
    ("D06", "dq_late_posting", 1.0, 1.0),
    # A minor/major unit slip on a £20 basket produces a £2,000 row, which is an ordinary
    # electronics purchase. The category-relative check cannot reach 1.0 and should not pretend to.
    ("D07", "dq_implausible_amount", 0.75, 0.80),
]


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> Path:
    from card_spend.generate.generator import generate
    from card_spend.ingest.lake import ingest
    from card_spend.models.run import run_dbt

    root = tmp_path_factory.mktemp("detect")
    generate("dev", root, seed=41)
    ingest(root / "raw", root / "lake", CONTRACTS)
    assert run_dbt(root) == 0
    return root


def _scores(root: Path, code: str, column: str) -> tuple[float, float, int]:
    lake = (root / "lake").resolve()
    fact = f"read_parquet('{lake}/marts/fct_card_transaction/*/*.parquet', hive_partitioning = 1)"
    ledger = f"read_parquet('{root / 'raw' / '_defects.parquet'}')"
    con = duckdb.connect(":memory:")
    try:
        truth, flagged, hits = con.execute(
            f"""
            with truth as (select distinct transaction_id from {ledger} where defect_code = '{code}'),
                 flagged as (select transaction_id from {fact} where {column})
            select
                (select count(*) from truth),
                (select count(*) from flagged),
                (select count(*) from flagged f join truth t using (transaction_id))
            """
        ).fetchone()
    finally:
        con.close()
    recall = hits / truth if truth else 0.0
    precision = hits / flagged if flagged else 0.0
    return recall, precision, truth


@pytest.mark.parametrize(("code", "column", "min_recall", "min_precision"), RULES)
def test_rule_meets_its_detection_floor(built, code, column, min_recall, min_precision) -> None:
    recall, precision, truth = _scores(built, code, column)
    assert truth > 0, f"the fixture injected no {code} defects to detect"
    assert recall >= min_recall, f"{code} recall {recall:.1%} below the {min_recall:.0%} floor"
    assert precision >= min_precision, f"{code} precision {precision:.1%} below the {min_precision:.0%} floor"


def test_the_unit_slip_residual_is_quantified_in_money(built) -> None:
    """The number the RCA turns on: what the undetected slips are still worth on the ledger.

    A control that catches most of a problem is not the same as a control that protects the ledger,
    and the difference has to be stated in pounds for anyone to act on it.
    """
    lake = (built / "lake").resolve()
    fact = f"read_parquet('{lake}/marts/fct_card_transaction/*/*.parquet', hive_partitioning = 1)"
    ledger = f"read_parquet('{built / 'raw' / '_defects.parquet'}')"
    con = duckdb.connect(":memory:")
    try:
        injected, missed_rows, missed_value = con.execute(
            f"""
            with truth as (select distinct transaction_id from {ledger} where defect_code = 'D07')
            select
                (select count(*) from truth),
                count(*) filter (where not f.dq_implausible_amount),
                coalesce(sum(f.amount_gbp_minor) filter (where not f.dq_implausible_amount), 0) / 100.0
            from {fact} f join truth t using (transaction_id)
            """
        ).fetchone()
    finally:
        con.close()

    assert injected > 0
    # Not a pass/fail threshold so much as a tripwire: if the residual ever grows past a quarter of
    # the injected population, the rule has drifted and the RCA's conclusion needs revisiting.
    assert missed_rows / injected <= 0.25, f"{missed_rows}/{injected} unit slips undetected"
    assert missed_value >= 0
