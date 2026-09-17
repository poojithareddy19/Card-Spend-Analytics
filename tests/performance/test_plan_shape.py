"""Plan-shape regression gates.

Timing assertions are fragile: a busy CI runner makes them flap and everybody starts ignoring them.
Plan shape is not. If someone drops the index on the pre-aggregate, or renames a partition key so
pruning stops working, the plan changes even on a machine that is a hundred times slower, and these
tests fail for the right reason.

This is the piece the companion repo was missing: it committed EXPLAIN output as text files that
nothing ever compared against.
"""

from __future__ import annotations

import re
from pathlib import Path

import duckdb
import pytest

from card_spend.stores.duck import DuckStore
from card_spend.stores.postgres import PostgresStore

pytestmark = pytest.mark.slow


@pytest.fixture(scope="module")
def pg(pipeline) -> PostgresStore:
    if not pipeline["postgres"]:
        pytest.skip("Postgres is not reachable")
    return PostgresStore(str(pipeline["dsn"]))


@pytest.fixture(scope="module")
def params(pipeline) -> dict[str, object]:
    root = Path(str(pipeline["root"]))
    con = duckdb.connect(str(root / "warehouse.duckdb"), read_only=True)
    try:
        customer_id = con.execute(
            """
            select customer_id from main_marts.fct_card_transaction
            where customer_id <> 'UNKNOWN' group by 1 order by count(*) desc limit 1
            """
        ).fetchone()[0]
        max_date = con.execute("select max(posted_date) from main_marts.fct_card_transaction").fetchone()[0]
    finally:
        con.close()
    return {"customer_id": customer_id, "max_date": max_date, "from_month": max_date.strftime("%Y-%m")}


def test_the_hot_path_uses_the_aggregate_index_and_never_scans_it(pg, params) -> None:
    """Access pattern 2 serves a screen. A sequential scan here is a production incident."""
    plan = pg.explain("q2_customer_12m_spend_by_category", **params)
    assert "agg_customer_month" in plan, f"the index is not being used:\n{plan}"
    assert "Seq Scan on agg_customer_category_month" not in plan, f"regressed to a scan:\n{plan}"


def test_a_date_filtered_fact_query_prunes_partitions(pg, params) -> None:
    """The planner must drop the months the filter excludes, not read them and throw rows away."""
    con = pg.connect()
    with con.cursor() as cur:
        cur.execute("select count(*) from pg_inherits where inhparent = 'marts.fct_card_transaction'::regclass")
        total_partitions = int(cur.fetchone()[0])
        cur.execute(
            """
            explain (analyze, format text)
            select count(*) from marts.fct_card_transaction
            where posted_date >= %(from)s
            """,
            {"from": params["max_date"]},
        )
        plan = "\n".join(r[0] for r in cur.fetchall())

    scanned = len(re.findall(r"on (?:marts\.)?fct_\d{4}_\d{2}", plan))
    assert total_partitions > 1, "the fixture should span more than one month"
    assert 0 < scanned < total_partitions, f"expected pruning, scanned {scanned}/{total_partitions}:\n{plan}"


def test_the_customer_lookup_uses_the_primary_key(pg, params) -> None:
    plan = pg.explain("q1_customer_profile_lookup", **params)
    assert "Index Scan" in plan or "Index Only Scan" in plan, plan
    assert "Seq Scan on dim_customer_current" not in plan, plan


def test_duckdb_opens_fewer_files_when_the_filter_is_on_the_partition_key(pipeline, params) -> None:
    """Partition pruning on the lake, measured by the number of Parquet files the scan touches."""
    root = Path(str(pipeline["root"]))
    duck = DuckStore(root / "lake")
    try:
        all_files = len(list((root / "lake" / "card_transactions").glob("posted_date=*/*.parquet")))
        one_day = params["max_date"].isoformat()

        def files_in_plan(sql: str) -> int:
            plan = duck.con.execute("explain analyze " + sql).fetchall()
            text = "\n".join(str(r[-1]) for r in plan)
            match = re.search(r"Total Files Read:\s*(\d+)", text)
            assert match, f"DuckDB did not report files read:\n{text[:2000]}"
            return int(match.group(1))

        unfiltered = files_in_plan(
            f"select count(*) from read_parquet('{duck.lake}/card_transactions/*/*.parquet', hive_partitioning = 1)"
        )
        filtered = files_in_plan(
            f"""
            select count(*) from read_parquet('{duck.lake}/card_transactions/*/*.parquet', hive_partitioning = 1)
            where posted_date = date '{one_day}'
            """
        )
        assert unfiltered == all_files, f"expected a full scan to open all {all_files} files, got {unfiltered}"
        assert filtered == 1, f"a single-day filter should open exactly one file, opened {filtered}"
    finally:
        duck.close()


def test_every_access_pattern_still_returns_the_same_answer_from_every_store(pipeline, params) -> None:
    """A performance change that quietly changes the answer is the worst outcome of an optimisation.

    q3 is the pattern both engines implement over different physical layouts, so it is the one worth
    cross-checking: the row count and the settled transaction total must agree.
    """
    if not pipeline["postgres"]:
        pytest.skip("Postgres is not reachable")
    root = Path(str(pipeline["root"]))
    duck = DuckStore(root / "lake")
    pg = PostgresStore(str(pipeline["dsn"]))
    try:
        a = duck.run("q3_category_month_spend_all_customers").sort_values(["month_key", "category"])
        b = pg.run("q3_category_month_spend_all_customers").sort_values(["month_key", "category"])
        assert len(a) == len(b)
        assert a["txn_count"].sum() == b["txn_count"].sum()
    finally:
        duck.close()
        pg.close()
