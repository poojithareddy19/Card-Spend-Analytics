"""Small DuckDB helpers shared by the export, the loader and the benchmark.

One function, and it exists because `fetchone()` is typed as returning `tuple | None` and every
caller in this project knows its query returns exactly one row. Indexing the result directly is the
common shortcut and it fails as `'NoneType' object is not subscriptable`, several frames away from
the query that actually returned nothing. Naming the query in the error is worth the wrapper.
"""

from __future__ import annotations

from typing import Any

import duckdb


def scalar(con: duckdb.DuckDBPyConnection, sql: str) -> Any:
    """Run a query that must return exactly one row, and give back its first column."""
    row = con.execute(sql).fetchone()
    if row is None:
        raise RuntimeError(f"expected one row, got none, from: {' '.join(sql.split())[:200]}")
    return row[0]
