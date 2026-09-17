"""DuckDB over the Parquet lake: the analytical store.

Every query reads the **gold** layer, `lake/marts/`, with `read_parquet(..., hive_partitioning = 1)`
rather than tables inside a DuckDB file. Two reasons, and both matter:

* The thing being measured is what a column store over partitioned Parquet can do, not what a table
  inside a database file can do.
* The gold layer holds the same modelled rows Postgres was loaded from, so the two engines answer
  each question identically. Pointed at the unmodelled lake instead, DuckDB would double-count the
  duplicated rows the modelling step removes, and every cross-store comparison would be noise.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

from card_spend.stores.base import UnsupportedPatternError

SQL: dict[str, str] = {
    # Answering this from the lake means scanning a string column across every partition. It is here
    # precisely to show a column store losing to a key-value store on a single-key deep read.
    "q1_customer_profile_lookup": """
        select customer_id, document
        from read_parquet('{lake}/marts/customer_document.parquet')
        where customer_id = $customer_id
    """,
    "q2_customer_12m_spend_by_category": """
        select
            strftime(cast(f.posted_date as date), '%Y-%m')      as month_key,
            coalesce(m.category, 'unknown')                     as category,
            count(*)                                            as txn_count,
            sum(f.amount_gbp_minor)                             as spend_minor
        from read_parquet('{lake}/marts/fct_card_transaction/*/*.parquet', hive_partitioning = 1) f
        join read_parquet('{lake}/marts/dim_txn_context.parquet') c on c.txn_context_key = f.txn_context_key
        left join read_parquet('{lake}/marts/dim_merchant.parquet') m on m.merchant_key = f.merchant_key
        where f.customer_id = $customer_id
          and c.is_settled
          and cast(f.posted_date as date) >= $from_date
        group by 1, 2
        order by 1, 2
    """,
    # The pattern a column store is built for: two columns, every row, one grouping.
    "q3_category_month_spend_all_customers": """
        select
            strftime(cast(f.posted_date as date), '%Y-%m')      as month_key,
            coalesce(m.category, 'unknown')                     as category,
            count(*)                                            as txn_count,
            sum(f.amount_gbp_minor)                             as spend_minor
        from read_parquet('{lake}/marts/fct_card_transaction/*/*.parquet', hive_partitioning = 1) f
        join read_parquet('{lake}/marts/dim_txn_context.parquet') c on c.txn_context_key = f.txn_context_key
        left join read_parquet('{lake}/marts/dim_merchant.parquet') m on m.merchant_key = f.merchant_key
        where c.is_settled
        group by 1, 2
        order by 1, 2
    """,
    "q4_top_merchants_per_segment": """
        with spend as (
            select
                cu.segment,
                m.merchant_name,
                sum(f.amount_gbp_minor) as spend_minor,
                count(*)                as txn_count
            from read_parquet('{lake}/marts/fct_card_transaction/*/*.parquet', hive_partitioning = 1) f
            join read_parquet('{lake}/marts/dim_txn_context.parquet') c on c.txn_context_key = f.txn_context_key
            join read_parquet('{lake}/marts/dim_customer.parquet')    cu
                 on cu.customer_id = f.customer_id and cu.is_current and cu.customer_id <> 'UNKNOWN'
            join read_parquet('{lake}/marts/dim_merchant.parquet')    m  on m.merchant_key = f.merchant_key
            where c.is_settled and c.counts_towards_spend and m.merchant_key <> 'UNKNOWN'
            group by 1, 2
        ),
        ranked as (
            select *, row_number() over (partition by segment order by spend_minor desc) as rank
            from spend
        )
        select * from ranked where rank <= 20 order by segment, rank
    """,
    # A range join, and the reason this pattern is computed once at load rather than per query.
    "q5_asof_fx_revaluation": """
        with latest as (
            select currency, rate_to_gbp
            from (
                select
                    currency,
                    rate_to_gbp,
                    row_number() over (partition by currency order by rate_date desc) as rn
                from read_parquet('{lake}/marts/fx_rate.parquet')
            ) where rn = 1
        ),
        priced as (
            select
                t.currency_key as currency,
                cast(t.posted_date as date) as posted_date,
                t.amount_minor,
                (
                    select f.rate_to_gbp
                    from read_parquet('{lake}/marts/fx_rate.parquet') f
                    where f.currency = t.currency_key and f.rate_date <= cast(t.posted_date as date)
                    order by f.rate_date desc
                    limit 1
                ) as rate_at_posting
            from read_parquet('{lake}/marts/fct_card_transaction/*/*.parquet', hive_partitioning = 1) t
            join read_parquet('{lake}/marts/dim_txn_context.parquet') c on c.txn_context_key = t.txn_context_key
            where t.currency_key != 'GBP' and c.is_settled
        )
        select
            priced.currency,
            count(*)                                                       as txn_count,
            sum(priced.amount_minor * priced.rate_at_posting)                as booked_gbp_minor,
            sum(priced.amount_minor * latest.rate_to_gbp)                    as revalued_gbp_minor,
            sum(priced.amount_minor * (latest.rate_to_gbp - priced.rate_at_posting)) as unrealised_gbp_minor
        from priced join latest on latest.currency = priced.currency
        group by 1
        order by 1
    """,
}


class DuckStore:
    name = "duckdb"
    kind = "columnar over parquet lake"

    def __init__(self, lake_path: Path, threads: int | None = None) -> None:
        self.lake = str(Path(lake_path).resolve())
        self.con = duckdb.connect(":memory:")
        if threads:
            self.con.execute(f"pragma threads={threads}")

    def reachable(self) -> bool:
        """Embedded, so reachability is just whether the lake directory is there."""
        return Path(self.lake).exists()

    def available(self) -> bool:
        """The gold layer has to exist, not merely the lake root."""
        return (Path(self.lake) / "marts").exists()

    def supports(self, pattern_key: str) -> bool:
        return pattern_key in SQL

    def _sql(self, pattern_key: str) -> str:
        if pattern_key not in SQL:
            raise UnsupportedPatternError(f"{self.name} has no implementation of {pattern_key}")
        return SQL[pattern_key].format(lake=self.lake)

    @staticmethod
    def _bind(sql: str, params: dict[str, Any]) -> dict[str, Any] | None:
        """Pass only the parameters this statement actually names.

        The harness hands every store the same parameter bag; DuckDB rejects a bag with extras.
        Binding from the placeholders in the SQL keeps the harness generic without every query
        having to accept arguments it does not use.
        """
        named = set(re.findall(r"\$([a-z_][a-z0-9_]*)", sql))
        bound = {k: v for k, v in params.items() if k in named}
        return bound or None

    def run(self, pattern_key: str, **params: Any) -> pd.DataFrame:
        sql = self._sql(pattern_key)
        return self.con.execute(sql, self._bind(sql, params)).df()

    def explain(self, pattern_key: str, **params: Any) -> str:
        sql = self._sql(pattern_key)
        rows = self.con.execute("explain " + sql, self._bind(sql, params)).fetchall()
        return "\n".join(str(r[-1]) for r in rows)

    def scan_stats(self, pattern_key: str, **params: Any) -> dict[str, Any]:
        """How much of the lake the engine actually touched.

        `explain analyze` reports the cardinality each scan produced, which is how partition pruning
        is demonstrated rather than asserted: a date-filtered query should show a scan cardinality
        far below the table's row count.
        """
        self.con.execute("pragma enable_profiling='no_output'")
        try:
            sql = self._sql(pattern_key)
            plan = self.con.execute("explain analyze " + sql, self._bind(sql, params)).fetchall()
        finally:
            self.con.execute("pragma disable_profiling")
        return {"plan": "\n".join(str(r[-1]) for r in plan)}

    def close(self) -> None:
        self.con.close()
