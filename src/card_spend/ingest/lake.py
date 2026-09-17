"""Stage 2: land every feed into the Parquet lake under one canonical schema.

Everything after this point reads Parquet and nothing else. That is deliberate: the cost of five
wire formats should be paid exactly once, at the boundary, not by every consumer forever.

Transactions keep their ``posted_date=`` partitioning, because it matches the load pattern, the
retention policy and the most common report filter, and because pruning is the single largest
performance lever in this project. Day 5 measures it.
"""

from __future__ import annotations

import datetime as dt
import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

import pandas as pd
import pyarrow as pa

from card_spend.contracts import ContractCheck, ContractRegistry, Verdict, summarise
from card_spend.generate import writers
from card_spend.ingest import readers

# `posted_date` is deliberately **absent** from the file schema. It is the Hive partition key, and
# under that convention the value lives in the directory name and nowhere else. Storing it in both
# places costs a column on every row, lets the two copies disagree, and makes every engine that
# understands hive partitioning refuse the dataset outright for having the field twice.
TXN_SCHEMA = pa.schema(
    [
        ("transaction_id", pa.string()),
        ("card_id", pa.string()),
        ("account_id", pa.string()),
        ("merchant_id", pa.string()),
        ("txn_timestamp", pa.timestamp("us", tz="UTC")),
        ("amount_minor", pa.int64()),
        ("currency", pa.string()),
        ("mcc", pa.int32()),
        ("auth_code", pa.string()),
        ("txn_type", pa.string()),
        ("channel", pa.string()),
        ("status", pa.string()),
        ("pos_entry_mode", pa.string()),
        ("is_recurring", pa.bool_()),
        ("feed_version", pa.int8()),
    ]
)


class SchemaDriftError(RuntimeError):
    """A feed broke its contract. Nothing is landed."""


@dataclass
class IngestResult:
    contract_checks: list[str] = field(default_factory=list)
    row_counts: dict[str, int] = field(default_factory=dict)
    partitions: int = 0
    bytes_written: int = 0
    seconds: dict[str, float] = field(default_factory=dict)


def check_contracts(raw_dir: Path, contracts_dir: Path) -> list[ContractCheck]:
    registry = ContractRegistry.load(contracts_dir)
    return registry.check_all(readers.observe_all(raw_dir))


def ingest(raw_dir: Path, lake_dir: Path, contracts_dir: Path, *, force: bool = False) -> IngestResult:
    result = IngestResult()
    t_start = time.perf_counter()

    checks = check_contracts(raw_dir, contracts_dir)
    result.contract_checks = [c.message() for c in checks]
    breaking = [c for c in checks if c.verdict is Verdict.BREAKING]
    if breaking and not force:
        # The gate sits outside the landing work on purpose: refusing costs one header read, and the
        # evidence of the refusal survives the abort it causes.
        raise SchemaDriftError("; ".join(c.message() for c in breaking))

    t = time.perf_counter()
    customers = readers.read_customers(raw_dir / "customers" / "customers.jsonl")
    result.row_counts["customers"] = writers.write_parquet(lake_dir / "customers" / "customers.parquet", customers)
    result.seconds["customers"] = round(time.perf_counter() - t, 3)

    t = time.perf_counter()
    accounts = readers.read_accounts(raw_dir / "accounts" / "accounts.csv")
    cards = readers.read_cards(raw_dir / "cards" / "cards.csv")
    result.row_counts["accounts"] = writers.write_parquet(lake_dir / "accounts" / "accounts.parquet", accounts)
    result.row_counts["cards"] = writers.write_parquet(lake_dir / "cards" / "cards.parquet", cards)
    result.seconds["accounts_and_cards"] = round(time.perf_counter() - t, 3)

    t = time.perf_counter()
    merchants = readers.read_merchants(raw_dir / "merchants" / "merchants.parquet")
    result.row_counts["merchants"] = writers.write_parquet(lake_dir / "merchants" / "merchants.parquet", merchants)
    fx = readers.read_fx_rates(raw_dir)
    result.row_counts["fx_rates"] = writers.write_parquet(lake_dir / "fx_rates" / "fx_rates.parquet", fx)
    result.seconds["merchants_and_fx"] = round(time.perf_counter() - t, 3)

    t = time.perf_counter()
    rows, partitions, size = _land_transactions(raw_dir, lake_dir, contracts_dir)
    result.row_counts["card_transactions"] = rows
    result.partitions = partitions
    result.bytes_written = size
    result.seconds["card_transactions"] = round(time.perf_counter() - t, 3)

    result.seconds["total"] = round(time.perf_counter() - t_start, 3)
    (lake_dir / "_ingest.json").parent.mkdir(parents=True, exist_ok=True)
    (lake_dir / "_ingest.json").write_text(
        json.dumps({**asdict(result), "summary": summarise(checks)}, indent=2, default=str), encoding="utf-8"
    )
    return result


def _land_transactions(raw_dir: Path, lake_dir: Path, contracts_dir: Path) -> tuple[int, int, int]:
    # One reader schema for the whole history. v1 partitions come back with the v2 columns null.
    v2 = writers.load_avro_schema(contracts_dir / "card_transactions.v2.avsc")
    v1_fields = {f["name"] for f in json.loads((contracts_dir / "card_transactions.v1.avsc").read_text())["fields"]}

    rows = 0
    partitions = 0
    size = 0
    for stamp, path in readers.transaction_partitions(raw_dir):
        df = readers.read_transaction_partition(path, v2)
        if df.empty:
            continue
        # Which version actually wrote this file is worth keeping. It is the difference between
        # "this field is null because the customer had no value" and "because v1 did not have it".
        written_fields = readers.observe_avro(path)
        df["feed_version"] = 1 if written_fields == v1_fields else 2
        for col in ("merchant_id", "auth_code", "pos_entry_mode"):
            if col not in df:
                df[col] = None
            df[col] = df[col].astype("object")
        if "is_recurring" not in df:
            df["is_recurring"] = None
        df["mcc"] = df["mcc"].astype("int32")
        df["feed_version"] = df["feed_version"].astype("int8")
        df = df[[f.name for f in TXN_SCHEMA]]

        out = lake_dir / "card_transactions" / f"posted_date={stamp.isoformat()}" / "part-00000.parquet"
        rows += writers.write_parquet(out, df, TXN_SCHEMA)
        size += out.stat().st_size
        partitions += 1
    return rows, partitions, size


def lake_dates(lake_dir: Path) -> list[dt.date]:
    return [
        dt.date.fromisoformat(p.name.split("=", 1)[1])
        for p in sorted((lake_dir / "card_transactions").glob("posted_date=*"))
    ]


def read_lake_transactions(lake_dir: Path, columns: list[str] | None = None) -> pd.DataFrame:
    """Read the partitioned fact back with `posted_date` recovered from the partition path."""
    df = pd.read_parquet(lake_dir / "card_transactions", columns=columns)
    if "posted_date" in df.columns:
        df["posted_date"] = pd.to_datetime(df["posted_date"].astype(str)).dt.date
    return df
