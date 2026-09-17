"""Export the built marts back to the lake as Parquet: the gold layer.

Three layers, and the reason for the third one is the benchmark.

* **raw** — the five feeds exactly as their producers sent them, in their native formats
* **lake** — the same data in one canonical Parquet schema, still unmodelled
* **lake/marts** — the dbt output, written back out as Parquet

Without the gold layer, comparing DuckDB against Postgres would compare unmodelled lake data against
modelled warehouse tables, and the DuckDB side would silently double-count the duplicated rows that
the modelling step removes. The numbers would differ and the comparison would be worthless. With it,
both engines read the same modelled rows and the only variables left are the engine and the physical
layout, which is what the benchmark is actually about.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import duckdb

from card_spend.duck import scalar

# Flat exports: small enough that partitioning would only add file-open overhead.
FLAT_TABLES: dict[str, str] = {
    "dim_customer": "select * from main_marts.dim_customer",
    "dim_account": "select * from main_marts.dim_account",
    "dim_card": "select * from main_marts.dim_card",
    "dim_merchant": "select * from main_marts.dim_merchant",
    "dim_txn_context": "select * from main_marts.dim_txn_context",
    "dim_date": "select * from main_marts.dim_date",
    "fx_rate": "select * from main_intermediate.int_fx_rates_asof",
    "agg_customer_category_month": "select * from main_marts.agg_customer_category_month",
    "agg_dq_scorecard": "select * from main_marts.agg_dq_scorecard",
    "customer_document": "select customer_id, document from main_staging.stg_customers",
}


def export_marts(data_dir: Path) -> dict[str, Any]:
    warehouse = data_dir / "warehouse.duckdb"
    if not warehouse.exists():
        raise FileNotFoundError(f"no warehouse at {warehouse}; run the dbt build first")

    out = (data_dir / "lake" / "marts").resolve()
    out.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    report: dict[str, Any] = {"tables": {}}

    con = duckdb.connect(str(warehouse), read_only=True)
    try:
        for name, sql in FLAT_TABLES.items():
            target = out / f"{name}.parquet"
            con.execute(f"copy ({sql}) to '{target}' (format parquet, compression zstd)")
            report["tables"][name] = int(scalar(con, f"select count(*) from ({sql})"))

        # The fact keeps the same partitioning as the raw lake, for the same reasons, and so that a
        # date filter prunes identically on both.
        fact_dir = out / "fct_card_transaction"
        con.execute(
            f"""
            copy (select * from main_marts.fct_card_transaction)
            to '{fact_dir}'
            (format parquet, compression zstd, partition_by (posted_date), overwrite_or_ignore true)
            """
        )
        report["tables"]["fct_card_transaction"] = int(
            scalar(con, "select count(*) from main_marts.fct_card_transaction")
        )
        report["fact_partitions"] = len(list(fact_dir.glob("posted_date=*")))
    finally:
        con.close()

    report["seconds"] = round(time.perf_counter() - started, 2)
    return report
