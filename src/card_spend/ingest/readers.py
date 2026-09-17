"""Format readers, one per feed, plus the cheap field observation the contract check runs on.

`observe_*` opens as little of a feed as it can get away with: a CSV header, a Parquet footer, an
Avro writer schema, one JSON line. That matters because the contract check has to run **before** the
batch is read, so the cost of refusing a bad feed should be close to zero.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any, cast

import fastavro
import pandas as pd
import pyarrow.parquet as pq

from card_spend.generate import writers

# ------------------------------------------------------------------------------------------- observe


def observe_jsonl(path: Path) -> set[str]:
    with path.open(encoding="utf-8") as fh:
        first = fh.readline()
    if not first.strip():
        return set()
    return set(json.loads(first))


def observe_csv(path: Path) -> set[str]:
    return set(pd.read_csv(path, nrows=0).columns)


def observe_parquet(path: Path) -> set[str]:
    return set(pq.read_schema(path).names)


def observe_avro(path: Path) -> set[str]:
    """Read only the container header, which carries the writer schema, not the records."""
    with path.open("rb") as fh:
        reader = fastavro.reader(fh)
        # A writer schema is any Avro schema as far as fastavro's types go; a container written
        # from contracts/ always carries a record, which is the only form with a "fields" list.
        schema = cast(dict[str, Any], reader.writer_schema)
    return {str(f["name"]) for f in schema["fields"]}


def observe_json_array(path: Path) -> set[str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return set(payload[0]) if payload else set()


def observe_all(raw_dir: Path) -> dict[str, set[str]]:
    """What each feed actually delivered, cheaply. Feeds with no files are simply absent."""
    observed: dict[str, set[str]] = {}

    customers = raw_dir / "customers" / "customers.jsonl"
    if customers.exists():
        observed["customers"] = observe_jsonl(customers)

    for feed in ("accounts", "cards"):
        path = raw_dir / feed / f"{feed}.csv"
        if path.exists():
            observed[feed] = observe_csv(path)

    merchants = raw_dir / "merchants" / "merchants.parquet"
    if merchants.exists():
        observed["merchants"] = observe_parquet(merchants)

    # The newest partition is the one that would carry a producer-side change, so it is the one
    # worth checking. Checking the oldest would pass forever after a breaking change landed.
    txn_parts = sorted(raw_dir.glob("card_transactions/posted_date=*/part-*.avro"))
    if txn_parts:
        observed["card_transactions"] = observe_avro(txn_parts[-1])

    fx_parts = sorted(raw_dir.glob("fx_rates/rate_date=*/rates.json"))
    if fx_parts:
        observed["fx_rates"] = observe_json_array(fx_parts[-1])

    return observed


# --------------------------------------------------------------------------------------------- read


def read_customers(path: Path) -> pd.DataFrame:
    """Nested JSON to a flat frame.

    The nested original is kept verbatim in ``document`` because the document store on day 4 serves
    exactly that, and re-nesting a flattened frame is both lossy and pointless.
    """
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    flat = pd.json_normalize(rows, sep="_")
    flat["document"] = [json.dumps(r, separators=(",", ":")) for r in rows]
    flat = flat.rename(
        columns={
            "profile_first_name": "first_name",
            "profile_last_name": "last_name",
            "profile_date_of_birth": "date_of_birth",
            "profile_email": "email",
            "profile_phone": "phone",
            "address_line1": "address_line1",
            "address_line2": "address_line2",
            "address_city": "city",
            "address_postcode": "postcode",
            "address_country": "country",
            "kyc_status": "kyc_status",
            "kyc_verified_at": "kyc_verified_at",
            "kyc_risk_band": "kyc_risk_band",
        }
    )
    flat["created_at"] = pd.to_datetime(flat["created_at"], format="ISO8601", utc=True)
    flat["kyc_verified_at"] = pd.to_datetime(flat["kyc_verified_at"], format="ISO8601", utc=True, errors="coerce")
    flat["date_of_birth"] = pd.to_datetime(flat["date_of_birth"], errors="coerce").dt.date
    return flat


CSV_DTYPES: dict[str, dict[str, str]] = {
    # Declared, never inferred. An inferred dtype changes with the data, which is how an id column
    # of digits silently becomes an int and loses its leading zeros between one load and the next.
    "accounts": {
        "account_id": "string",
        "customer_id": "string",
        "product_code": "string",
        "currency": "string",
        "status": "string",
        "overdraft_limit_minor": "int64",
    },
    "cards": {
        "card_id": "string",
        "account_id": "string",
        "card_type": "string",
        "network": "string",
        "status": "string",
    },
}


def read_accounts(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, dtype=CSV_DTYPES["accounts"], keep_default_na=False, na_values=[""])
    df["opened_date"] = pd.to_datetime(df["opened_date"]).dt.date
    df["closed_date"] = pd.to_datetime(df["closed_date"], errors="coerce").dt.date
    return df


def read_cards(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, dtype=CSV_DTYPES["cards"])
    for col in ("issued_date", "expiry_date"):
        df[col] = pd.to_datetime(df[col]).dt.date
    return df


def read_merchants(path: Path) -> pd.DataFrame:
    return pd.read_parquet(path)


def read_fx_rates(raw_dir: Path) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for path in sorted(raw_dir.glob("fx_rates/rate_date=*/rates.json")):
        rows.extend(json.loads(path.read_text(encoding="utf-8")))
    df = pd.DataFrame(rows)
    df["rate_date"] = pd.to_datetime(df["rate_date"]).dt.date
    return df


def read_transaction_partition(path: Path, reader_schema: dict[str, Any]) -> pd.DataFrame:
    """Read one Avro partition under an explicit reader schema.

    Passing the v2 schema regardless of what wrote the file is the whole payoff of the evolution
    work: a v1 file yields the v2 shape with the new fields null, so one pass over a mixed history
    produces one frame with one set of columns. No version branching anywhere downstream.
    """
    records = writers.read_avro_as(path, reader_schema)
    df = pd.DataFrame.from_records(records)
    if df.empty:
        return df
    df["txn_timestamp"] = pd.to_datetime(df["txn_timestamp"], utc=True)
    df["posted_date"] = pd.to_datetime(df["posted_date"]).dt.date
    return df


def transaction_partitions(raw_dir: Path) -> list[tuple[dt.date, Path]]:
    out: list[tuple[dt.date, Path]] = []
    for path in sorted(raw_dir.glob("card_transactions/posted_date=*/part-*.avro")):
        stamp = dt.date.fromisoformat(path.parent.name.split("=", 1)[1])
        out.append((stamp, path))
    return out
