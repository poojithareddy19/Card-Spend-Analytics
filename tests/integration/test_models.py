"""Day-3 tests: the dbt layer.

dbt's own tests already assert uniqueness, referential integrity and the reconciliations. These
tests assert the things dbt cannot: that the SCD2 snapshot really opens a second version when a
watched column moves, that the scorecard reconciles against the generator's manifest, and that the
star answers a business question with the right number.
"""

from __future__ import annotations

import json
from pathlib import Path

import duckdb
import pandas as pd
import pytest

from card_spend.generate.generator import generate
from card_spend.ingest.lake import ingest
from card_spend.models.run import dbt, run_dbt

CONTRACTS = Path(__file__).resolve().parents[2] / "contracts"
pytestmark = pytest.mark.slow


@pytest.fixture(scope="module")
def warehouse(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("d3")
    generate("smoke", root, seed=21)
    ingest(root / "raw", root / "lake", CONTRACTS)
    assert run_dbt(root) == 0
    return root


@pytest.fixture(scope="module")
def root(warehouse: Path) -> Path:
    return warehouse


def q(root: Path, sql: str) -> pd.DataFrame:
    """Open, read, close.

    DuckDB takes a file lock even for a read-only connection, so a long-lived handle held by a
    fixture would block the next dbt invocation. Short connections keep the tests independent.
    """
    con = duckdb.connect(str(root / "warehouse.duckdb"), read_only=True)
    try:
        return con.execute(sql).df()
    finally:
        con.close()


def test_the_fact_has_exactly_one_row_per_deduplicated_transaction(root: Path) -> None:
    manifest = json.loads((root / "raw" / "_manifest.json").read_text())
    expected = manifest["row_counts"]["card_transactions"] - manifest["defect_counts"]["D02"]
    actual = q(root, "select count(*) as n from main_marts.fct_card_transaction")["n"][0]
    assert actual == expected


def test_the_scorecard_reconciles_against_the_generator_manifest(root: Path) -> None:
    """The point of declaring defects up front: the scorecard is checkable, not decorative."""
    manifest = json.loads((root / "raw" / "_manifest.json").read_text())
    scored = q(
        root,
        "select defect_code, sum(rows_failed) as failed from main_marts.agg_dq_scorecard group by 1",
    ).set_index("defect_code")["failed"]

    # D01 and D04 are injected on purchases only; the flags mirror that exactly.
    for code in ("D01", "D03", "D04", "D05"):
        assert int(scored[code]) == manifest["defect_counts"][code], code

    # D02's count is the number of *extra* copies, and the flag marks the surviving row of each
    # duplicated pair, so the two agree one-for-one here.
    assert int(scored["D02"]) == manifest["defect_counts"]["D02"]

    # D06 injects a lag of 3 to 20 days, and the rule fires above 2, so every injected row is
    # caught. The natural lag never exceeds 2, so there are no extras.
    assert int(scored["D06"]) == manifest["defect_counts"]["D06"]


def test_gbp_transactions_convert_to_themselves(root: Path) -> None:
    drift = q(
        root,
        """
        select count(*) as n
        from main_marts.fct_card_transaction
        where currency_key = 'GBP' and amount_minor != amount_gbp_minor
        """,
    )["n"][0]
    assert drift == 0


def test_no_transaction_is_left_without_an_fx_rate_despite_the_missing_days(root: Path) -> None:
    """D11 removes whole rate days. The carried-forward spine is what stops those rows vanishing."""
    assert (
        q(root, "select count(*) as n from main_marts.fct_card_transaction where fx_rate_to_gbp is null")["n"][0] == 0
    )
    carried = q(root, "select count(*) as n from main_marts.fct_card_transaction where fx_rate_carried_forward")["n"][0]
    assert carried > 0, "the fixture should exercise at least one carried-forward day"


def test_unmatched_keys_land_on_the_unknown_member_not_on_a_null(root: Path) -> None:
    nulls = q(
        root,
        """
        select
            sum(case when customer_key is null then 1 else 0 end) as c,
            sum(case when merchant_key is null then 1 else 0 end) as m,
            sum(case when card_key     is null then 1 else 0 end) as k
        from main_marts.fct_card_transaction
        """,
    )
    assert int(nulls["c"][0]) == 0 and int(nulls["m"][0]) == 0 and int(nulls["k"][0]) == 0
    unknown_cards = q(root, "select count(*) as n from main_marts.fct_card_transaction where card_key = 'UNKNOWN'")[
        "n"
    ][0]
    assert unknown_cards > 0


def test_the_aggregate_agrees_with_the_fact_it_summarises(root: Path) -> None:
    """A pre-aggregate that disagrees with its source is worse than no pre-aggregate."""
    agg = q(root, "select sum(spend_gbp_minor) as total from main_marts.agg_customer_category_month")["total"][0]
    fact = q(
        root,
        """
        select sum(f.amount_gbp_minor) as total
        from main_marts.fct_card_transaction f
        join main_marts.dim_txn_context c on c.txn_context_key = f.txn_context_key
        where c.is_settled
        """,
    )["total"][0]
    assert int(agg) == int(fact)


def test_scd2_opens_a_new_version_when_a_watched_column_moves(root: Path) -> None:
    """The demonstration that the snapshot is a real Type 2, not a table with two date columns.

    Promote a tenth of the customers to `premier` in the source, snapshot again, and the previous
    version must be closed off rather than overwritten.
    """
    lake_customers = root / "lake" / "customers" / "customers.parquet"
    df = pd.read_parquet(lake_customers)
    # Only customers who are not already premier: promoting a premier customer to premier is not a
    # change, and the snapshot is right to leave them on one version.
    promoted = df.loc[df["segment"] != "premier", "customer_id"].head(20).tolist()
    assert promoted
    df.loc[df["customer_id"].isin(promoted), "segment"] = "premier"
    df.to_parquet(lake_customers, index=False)

    assert dbt(["run", "--select", "staging"], root).returncode == 0
    assert dbt(["snapshot"], root).returncode == 0
    assert dbt(["run", "--select", "dim_customer"], root).returncode == 0

    versions = q(
        root,
        f"""
        select customer_id, count(*) as versions, sum(case when is_current then 1 else 0 end) as current_rows
        from main_marts.dim_customer
        where customer_id in ({",".join(repr(c) for c in promoted)})
        group by 1
        """,
    )
    assert (versions["versions"] == 2).all(), "a watched-column change must open a second version"
    assert (versions["current_rows"] == 1).all(), "exactly one version may be current"

    closed = q(
        root,
        f"""
        select count(*) as n from main_marts.dim_customer
        where customer_id in ({",".join(repr(c) for c in promoted)})
          and not is_current and valid_to < timestamp '9999-12-31 00:00:00'
        """,
    )["n"][0]
    assert closed == len(promoted), "the superseded version must be closed off, not left open"
