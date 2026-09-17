"""PostgreSQL: the relational serving store.

This is the layer an application queries. It holds the current-state dimensions, the monthly
pre-aggregate, and a range-partitioned fact. It is narrow, indexed and optimised for latency, and
the benchmark exists partly to show where that stops being an advantage.
"""

from __future__ import annotations

import os
import re
from typing import TYPE_CHECKING, Any

import pandas as pd

from card_spend.stores.base import UnsupportedPatternError

if TYPE_CHECKING:
    import psycopg
else:  # pragma: no cover - import guard
    # psycopg is the `serve` extra. Absent, the store reports itself unreachable rather than raising
    # at import, so `make all` still completes without a Postgres server anywhere in sight.
    try:
        import psycopg
    except ImportError:
        psycopg = None

DEFAULT_DSN = os.environ.get("CSA_POSTGRES_DSN", "postgresql://postgres:postgres@127.0.0.1:5432/card_spend")

SQL: dict[str, str] = {
    # The relational cost of a document: four tables reassembled with two aggregations. This is the
    # honest comparison against a key-value store that returns the same thing with one GET.
    "q1_customer_profile_lookup": """
        select
            c.customer_id, c.segment, c.kyc_status, c.kyc_risk_band, c.onboarded_on,
            c.first_name, c.last_name, c.email, c.city, c.postcode,
            coalesce(a.accounts, 0) as account_count,
            coalesce(k.cards, 0)    as card_count
        from marts.dim_customer_current c
        left join (
            select customer_id, count(*) as accounts from marts.dim_account_current group by 1
        ) a on a.customer_id = c.customer_id
        left join (
            select ac.customer_id, count(*) as cards
            from marts.dim_card cd
            join marts.dim_account_current ac on ac.account_id = cd.account_id
            group by 1
        ) k on k.customer_id = c.customer_id
        where c.customer_id = %(customer_id)s
    """,
    # Served from the pre-aggregate, which is the entire reason the pre-aggregate exists.
    "q2_customer_12m_spend_by_category": """
        select month_key, category, txn_count, net_spend_gbp_minor as spend_minor
        from marts.agg_customer_category_month
        where customer_id = %(customer_id)s
          and month_key >= %(from_month)s
        order by month_key, category
    """,
    # Deliberately run against the fact, not the aggregate. A row store scanning every row of a
    # hundred-million-row table is exactly the comparison worth publishing.
    "q3_category_month_spend_all_customers": """
        select
            to_char(f.posted_date, 'YYYY-MM')       as month_key,
            coalesce(m.category, 'unknown')         as category,
            count(*)                                as txn_count,
            sum(f.amount_gbp_minor)                 as spend_minor
        from marts.fct_card_transaction f
        join marts.dim_txn_context c on c.txn_context_key = f.txn_context_key
        left join marts.dim_merchant m on m.merchant_key = f.merchant_key
        where c.is_settled
        group by 1, 2
        order by 1, 2
    """,
    "q4_top_merchants_per_segment": """
        with spend as (
            select cu.segment, m.merchant_name,
                   sum(f.amount_gbp_minor) as spend_minor,
                   count(*)                as txn_count
            from marts.fct_card_transaction f
            join marts.dim_txn_context   c  on c.txn_context_key = f.txn_context_key
            join marts.dim_customer_current cu on cu.customer_id = f.customer_id
            join marts.dim_merchant      m  on m.merchant_key = f.merchant_key
            where c.is_settled and c.counts_towards_spend and m.merchant_key <> 'UNKNOWN'
            group by 1, 2
        ), ranked as (
            select *, row_number() over (partition by segment order by spend_minor desc) as rank
            from spend
        )
        select * from ranked where rank <= 20 order by segment, rank
    """,
    "q5_asof_fx_revaluation": """
        with latest as (
            select distinct on (currency) currency, rate_to_gbp
            from marts.fx_rate order by currency, rate_date desc
        )
        select
            f.currency_key                                                  as currency,
            count(*)                                                        as txn_count,
            sum(f.amount_gbp_minor)                                         as booked_gbp_minor,
            sum(f.amount_minor * l.rate_to_gbp)                             as revalued_gbp_minor,
            sum(f.amount_minor * l.rate_to_gbp) - sum(f.amount_gbp_minor)   as unrealised_gbp_minor
        from marts.fct_card_transaction f
        join latest l on l.currency = f.currency_key
        join marts.dim_txn_context c on c.txn_context_key = f.txn_context_key
        where f.currency_key <> 'GBP' and c.is_settled
        group by 1
        order by 1
    """,
}


class PostgresStore:
    name = "postgres"
    kind = "relational serving marts"

    def __init__(self, dsn: str = DEFAULT_DSN) -> None:
        self.dsn = dsn
        self._con: Any = None

    def connect(self) -> Any:
        if psycopg is None:
            raise UnsupportedPatternError("psycopg is not installed")
        if self._con is None or self._con.closed:
            self._con = psycopg.connect(self.dsn, autocommit=True)
        return self._con

    def reachable(self) -> bool:
        """Can be connected to. Says nothing about whether it holds any data."""
        if psycopg is None:
            return False
        try:
            with psycopg.connect(self.dsn, connect_timeout=3) as con:
                con.execute("select 1")
            return True
        except Exception:
            return False

    def available(self) -> bool:
        """Reachable **and** loaded.

        A connection that answers `select 1` is not the same as a store that can answer the
        benchmark. A half-loaded or empty marts schema returns instantly and would publish a
        spectacular and completely meaningless p50, so an empty fact counts as unavailable.

        The loader deliberately checks `reachable()` instead: a store has to be empty before it can
        be filled, and a loader that refuses to load an empty database is a loader that never runs.
        """
        if not self.reachable():
            return False
        try:
            with psycopg.connect(self.dsn, connect_timeout=3) as con:
                row = con.execute("select 1 from marts.fct_card_transaction limit 1").fetchone()
            return row is not None
        except Exception:
            return False

    def supports(self, pattern_key: str) -> bool:
        return pattern_key in SQL

    def _sql(self, pattern_key: str) -> str:
        if pattern_key not in SQL:
            raise UnsupportedPatternError(f"{self.name} has no implementation of {pattern_key}")
        return SQL[pattern_key]

    @staticmethod
    def _bind(sql: str, params: dict[str, Any]) -> dict[str, Any]:
        """Only the parameters this statement names. See the same helper in the DuckDB store."""
        named = set(re.findall(r"%\(([a-z_][a-z0-9_]*)\)s", sql))
        return {k: v for k, v in params.items() if k in named}

    def run(self, pattern_key: str, **params: Any) -> pd.DataFrame:
        con = self.connect()
        sql = self._sql(pattern_key)
        with con.cursor() as cur:
            cur.execute(sql, self._bind(sql, params))
            cols = [d.name for d in (cur.description or [])]
            return pd.DataFrame(cur.fetchall(), columns=cols)

    def explain(self, pattern_key: str, **params: Any) -> str:
        con = self.connect()
        with con.cursor() as cur:
            sql = self._sql(pattern_key)
            cur.execute("explain (analyze, buffers, format text) " + sql, self._bind(sql, params))
            return "\n".join(row[0] for row in cur.fetchall())

    def close(self) -> None:
        if self._con is not None and not self._con.closed:
            self._con.close()
