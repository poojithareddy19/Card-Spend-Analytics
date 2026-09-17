"""Shared fixtures.

The full pipeline is built once per session at `smoke` scale and shared. It takes about half a
minute, which is too slow for every test but far too useful to skip: the plan-shape tests are the
regression gate for the day-5 numbers, and a gate that only runs on a hand-built dataset is a gate
that stops running.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = REPO_ROOT / "contracts"

TEST_DSN = os.environ.get("CSA_TEST_POSTGRES_DSN", "postgresql://postgres:postgres@127.0.0.1:5432/card_spend_test")


def _ensure_test_database(dsn: str) -> bool:
    """Create the test database if it is missing. Returns False when Postgres is unreachable."""
    try:
        import psycopg
    except ImportError:
        return False

    name = dsn.rsplit("/", 1)[-1]
    admin = dsn.rsplit("/", 1)[0] + "/postgres"
    try:
        with psycopg.connect(admin, connect_timeout=3, autocommit=True) as con:
            exists = con.execute("select 1 from pg_database where datname = %s", (name,)).fetchone()
            if not exists:
                con.execute(f'create database "{name}"')
        return True
    except Exception:
        return False


@pytest.fixture(scope="session")
def pipeline(tmp_path_factory: pytest.TempPathFactory) -> dict[str, object]:
    """Generate, ingest, build and load a dev-scale dataset once for the whole session.

    Dev scale rather than smoke: the plan-shape tests are meaningless on a table small enough that
    the planner rationally prefers a sequential scan to an index, and a fixture that spans one month
    cannot demonstrate partition pruning at all.
    """
    from card_spend.generate.generator import generate
    from card_spend.ingest.lake import ingest
    from card_spend.models.run import run_dbt
    from card_spend.stores.load import load_stores

    root = tmp_path_factory.mktemp("pipeline")
    generate("dev", root, seed=31)
    ingest(root / "raw", root / "lake", CONTRACTS)
    assert run_dbt(root) == 0

    postgres_ready = _ensure_test_database(TEST_DSN)
    stores = ["postgres", "redis"] if postgres_ready else ["redis"]
    loaded = load_stores(root, stores, dsn=TEST_DSN)

    return {"root": root, "dsn": TEST_DSN, "postgres": postgres_ready, "loaded": loaded}
