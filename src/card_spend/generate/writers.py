"""Format writers.

One function per wire format, because the point of this project is that each feed arrives in the
format its real producer would use. The readers on day 2 mirror these one for one.

Avro is written with `fastavro` against the schema in ``contracts/``, not inferred, so a generator
change that breaks the promise fails here rather than three stages downstream.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any, cast

import fastavro
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

AVRO_CODEC = "snappy"


def load_avro_schema(path: Path) -> dict[str, Any]:
    """Parse an .avsc file into the form fastavro wants.

    The casts here and in the readers below are all the same narrowing. fastavro is typed against
    the whole Avro spec, where a schema may be a bare type name and a datum may be any of eleven
    kinds. Every schema in `contracts/` is a record, so both are always a mapping, and fastavro
    would have raised long before these returns if they were not.
    """
    return cast(dict[str, Any], fastavro.parse_schema(json.loads(path.read_text(encoding="utf-8"))))


def write_avro(path: Path, schema: dict[str, Any], records: Iterable[dict[str, Any]]) -> int:
    """Write an Avro container file. Returns the row count.

    fastavro validates each record against the schema as it writes, which is the behaviour we want:
    an enum symbol or a logical type the contract does not allow should be a generation-time error.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0

    def counted() -> Iterator[dict[str, Any]]:
        nonlocal count
        for rec in records:
            count += 1
            yield rec

    try:
        with path.open("wb") as fh:
            fastavro.writer(fh, schema, counted(), codec=AVRO_CODEC)
    except ValueError:  # snappy not installed in this environment
        count = 0
        with path.open("wb") as fh:
            fastavro.writer(fh, schema, counted(), codec="deflate")
    return count


def read_avro(path: Path) -> list[dict[str, Any]]:
    """Read an Avro container file back with the schema embedded in the file."""
    with path.open("rb") as fh:
        return cast(list[dict[str, Any]], list(fastavro.reader(fh)))


def read_avro_as(path: Path, reader_schema: dict[str, Any]) -> list[dict[str, Any]]:
    """Read with an explicit reader schema, which is how schema evolution is actually exercised."""
    with path.open("rb") as fh:
        return cast(list[dict[str, Any]], list(fastavro.reader(fh, reader_schema=reader_schema)))


def write_jsonl(path: Path, records: Iterable[dict[str, Any]]) -> int:
    """Newline-delimited JSON, one nested object per line."""
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec, separators=(",", ":"), default=str))
            fh.write("\n")
            count += 1
    return count


def write_json(path: Path, payload: Any) -> None:
    """A single JSON document, for feeds that arrive as one array per day."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")


def write_csv(path: Path, df: pd.DataFrame) -> int:
    """CSV with a header row, which is all a legacy nightly extract ever gives you."""
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)
    return len(df)


def write_parquet(path: Path, df: pd.DataFrame, schema: pa.Schema | None = None) -> int:
    """Single-file Parquet with an explicit schema so column types do not drift with the data."""
    path.parent.mkdir(parents=True, exist_ok=True)
    table = pa.Table.from_pandas(df, schema=schema, preserve_index=False)
    pq.write_table(table, path, compression="zstd")
    return int(table.num_rows)
