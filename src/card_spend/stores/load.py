"""Load the gold layer into the serving stores.

DuckDB is the build engine; Postgres and Redis are serving engines. The loader reads the **gold
Parquet layer**, not the DuckDB warehouse the build happened to use, for two reasons:

* The gold layer is the published interface. Every consumer reads the same bytes, so the serving
  stores cannot drift from the analytical store by construction.
* The warehouse file is the largest artefact in the project and is disposable once the gold layer
  exists. At benchmark scale it is roughly 8 GB, and a load that does not need it means it can be
  deleted before the serving stores are filled. That is the difference between the benchmark fitting
  on a laptop and not.

Movement is `COPY` on both sides rather than row-by-row inserts, because a loader that takes an hour
is a loader nobody runs.
"""

from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path
from typing import Any

import duckdb

from card_spend.duck import scalar
from card_spend.stores.kv import KeyValueStore
from card_spend.stores.postgres import DEFAULT_DSN, PostgresStore

DDL = """
create schema if not exists marts;

drop table if exists marts.fct_card_transaction cascade;
drop table if exists marts.agg_customer_category_month cascade;
drop table if exists marts.dim_customer_current cascade;
drop table if exists marts.dim_account_current cascade;
drop table if exists marts.dim_card cascade;
drop table if exists marts.dim_merchant cascade;
drop table if exists marts.dim_txn_context cascade;
drop table if exists marts.fx_rate cascade;

create table marts.dim_customer_current (
    customer_id     text primary key,
    segment         text not null,
    kyc_status      text not null,
    kyc_risk_band   text not null,
    onboarded_on    date,
    first_name      text,
    last_name       text,
    email           text,
    city            text,
    postcode        text
);

create table marts.dim_account_current (
    account_id      text primary key,
    customer_id     text not null,
    product_code    text not null,
    currency        text not null,
    status          text not null,
    opened_on       date
);
create index dim_account_current_customer on marts.dim_account_current (customer_id);

create table marts.dim_card (
    card_key    text primary key,
    account_id  text not null,
    card_type   text,
    network     text,
    status      text
);
create index dim_card_account on marts.dim_card (account_id);

create table marts.dim_merchant (
    merchant_key    text primary key,
    merchant_name   text,
    mcc             integer,
    category        text,
    country         text,
    is_online       boolean
);

create table marts.dim_txn_context (
    txn_context_key         integer primary key,
    txn_type                text,
    channel                 text,
    status                  text,
    is_settled              boolean,
    is_card_not_present     boolean,
    counts_towards_spend    boolean
);

create table marts.fx_rate (
    rate_date   date not null,
    currency    text not null,
    rate_to_gbp double precision not null,
    primary key (rate_date, currency)
);

-- The app's hot path. One row per customer, month and category, indexed on the way it is filtered.
create table marts.agg_customer_category_month (
    customer_id             text not null,
    month_key               text not null,
    category                text not null,
    txn_count               bigint not null,
    spend_gbp_minor         bigint not null,
    net_spend_gbp_minor     bigint not null,
    online_spend_gbp_minor  bigint not null
);

-- Range partitioned by month on the posting date: it matches the load pattern, the retention
-- policy and the most common report filter, and it lets the planner drop whole months.
create table marts.fct_card_transaction (
    transaction_id      text not null,
    posted_date         date not null,
    auth_date           date,
    customer_id         text not null,
    account_key         text,
    card_key            text,
    merchant_key        text,
    txn_context_key     integer,
    currency_key        text,
    mcc                 integer,
    amount_minor        bigint,
    amount_gbp_minor    bigint,
    fx_rate_to_gbp      double precision,
    posting_lag_days    integer,
    dq_score            integer
) partition by range (posted_date);
"""

# Applied after the copy, not before: building an index on an empty table and then filling it is
# several times slower than filling the table and building the index once.
POST_LOAD_SQL = """
create index agg_customer_month on marts.agg_customer_category_month (customer_id, month_key);
create index fct_customer_posted on marts.fct_card_transaction (customer_id, posted_date);
create index fct_posted_brin on marts.fct_card_transaction using brin (posted_date) with (pages_per_range = 32);
create index fct_merchant on marts.fct_card_transaction (merchant_key);
analyze marts.fct_card_transaction;
analyze marts.agg_customer_category_month;
analyze marts.dim_customer_current;
"""

GOLD_FACT = "read_parquet('{gold}/fct_card_transaction/*/*.parquet', hive_partitioning = 1)"

EXPORTS: dict[str, str] = {
    "marts.dim_customer_current": """
        select customer_id, segment, kyc_status, kyc_risk_band, onboarded_on,
               first_name, last_name, email, city, null as postcode
        from read_parquet('{gold}/dim_customer.parquet')
        where is_current and customer_id <> 'UNKNOWN'
    """,
    "marts.dim_account_current": """
        select account_id, customer_id, product_code, currency, status, opened_on
        from read_parquet('{gold}/dim_account.parquet')
        where is_current and account_id <> 'UNKNOWN'
    """,
    "marts.dim_card": """
        select card_key, account_id, card_type, network, status
        from read_parquet('{gold}/dim_card.parquet')
    """,
    "marts.dim_merchant": """
        select merchant_key, merchant_name, mcc, category, country, is_online
        from read_parquet('{gold}/dim_merchant.parquet')
    """,
    "marts.dim_txn_context": """
        select txn_context_key, txn_type, channel, status, is_settled, is_card_not_present,
               counts_towards_spend
        from read_parquet('{gold}/dim_txn_context.parquet')
    """,
    "marts.fx_rate": """
        select rate_date, currency, rate_to_gbp from read_parquet('{gold}/fx_rate.parquet')
    """,
    "marts.agg_customer_category_month": """
        select customer_id, month_key, category,
               cast(txn_count as bigint), cast(spend_gbp_minor as bigint),
               cast(net_spend_gbp_minor as bigint), cast(online_spend_gbp_minor as bigint)
        from read_parquet('{gold}/agg_customer_category_month.parquet')
    """,
    "marts.fct_card_transaction": f"""
        select transaction_id, cast(posted_date as date) as posted_date, auth_date, customer_id,
               account_key, card_key, merchant_key, txn_context_key, currency_key, mcc,
               amount_minor, amount_gbp_minor, fx_rate_to_gbp, posting_lag_days, dq_score
        from {GOLD_FACT}
    """,
}


def _month_partitions(con: duckdb.DuckDBPyConnection, gold: str) -> list[tuple[str, str, str]]:
    """Month ranges taken from the partition directory names, so no rows are read to find them."""
    rows = con.execute(
        f"""
        select distinct strftime(cast(posted_date as date), '%Y-%m-01') as month_start
        from {GOLD_FACT.format(gold=gold)} order by 1
        """
    ).fetchall()
    out = []
    for (start,) in rows:
        name = "fct_" + start[:7].replace("-", "_")
        end = str(scalar(con, f"select cast(date '{start}' + interval 1 month as varchar)"))[:10]
        out.append((name, start, end))
    return out


def load_stores(data_dir: Path, stores: list[str], dsn: str = DEFAULT_DSN) -> dict[str, Any]:
    gold = (data_dir / "lake" / "marts").resolve()
    if not gold.exists():
        raise FileNotFoundError(f"no gold layer at {gold}; run `card-spend build` first")

    report: dict[str, Any] = {"stores": {}, "source": str(gold)}
    con = duckdb.connect(":memory:")
    try:
        if "postgres" in stores:
            report["stores"]["postgres"] = _load_postgres(con, str(gold), dsn)
        if "redis" in stores:
            report["stores"]["redis"] = _load_redis(con, str(gold))
    finally:
        con.close()
    return report


def _load_postgres(con: duckdb.DuckDBPyConnection, gold: str, dsn: str) -> dict[str, Any]:
    store = PostgresStore(dsn)
    if not store.reachable():
        return {"skipped": "postgres not reachable", "dsn": dsn}

    started = time.perf_counter()
    pg = store.connect()
    pg.execute(DDL)
    for name, start, end in _month_partitions(con, gold):
        pg.execute(
            f"create table marts.{name} partition of marts.fct_card_transaction "
            f"for values from ('{start}') to ('{end}')"
        )

    counts: dict[str, int] = {}
    with tempfile.TemporaryDirectory() as tmp:
        for table, sql in EXPORTS.items():
            csv_path = Path(tmp) / f"{table.replace('.', '_')}.csv"
            con.execute(f"copy ({sql.format(gold=gold)}) to '{csv_path}' (format csv, header false, null '')")
            with pg.cursor() as cur, csv_path.open("rb") as fh:
                with cur.copy(f"copy {table} from stdin with (format csv, null '')") as copy:
                    while chunk := fh.read(1 << 20):
                        copy.write(chunk)
                cur.execute(f"select count(*) from {table}")
                counts[table] = int(cur.fetchone()[0])
            csv_path.unlink()

    pg.execute(POST_LOAD_SQL)
    store.close()
    return {"row_counts": counts, "seconds": round(time.perf_counter() - started, 2), "partitions": len(counts)}


def _load_redis(con: duckdb.DuckDBPyConnection, gold: str) -> dict[str, Any]:
    store = KeyValueStore()
    if not store.reachable():
        return {"skipped": "redis not reachable", "url": store.url}

    started = time.perf_counter()
    rows = con.execute(f"select customer_id, document from read_parquet('{gold}/customer_document.parquet')").fetchall()
    written = store.load({cid: doc for cid, doc in rows})
    sample = store.fetch_document(rows[0][0]) if rows else None
    store.close()
    return {
        "documents": written,
        "seconds": round(time.perf_counter() - started, 2),
        "sample_keys": sorted(sample) if sample else [],
        "sample_bytes": len(json.dumps(sample)) if sample else 0,
    }
