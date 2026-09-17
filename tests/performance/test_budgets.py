"""Latency budgets at benchmark scale.

Skipped unless `CSA_BENCH_DATA` points at a dataset built with the `bench` profile, because a budget
asserted against half a million rows says nothing about fifty million, and a test that passes for
the wrong reason is worse than one that does not run.

Run it with:

    card-spend generate --profile bench --out data-bench
    card-spend ingest  --data data-bench
    card-spend build   --data data-bench
    card-spend load    --data data-bench
    CSA_BENCH_DATA=data-bench pytest tests/performance -m bench

The budgets live in `config/settings.yaml` so they can be argued about in a pull request rather than
buried in a test file.
"""

from __future__ import annotations

import os
import statistics
import time
from pathlib import Path
from typing import Any

import pytest
import yaml

from card_spend.bench.harness import _pick_params
from card_spend.stores.base import PATTERNS_BY_KEY
from card_spend.stores.duck import DuckStore
from card_spend.stores.kv import KeyValueStore
from card_spend.stores.postgres import PostgresStore

pytestmark = pytest.mark.bench

REPO_ROOT = Path(__file__).resolve().parents[2]
BENCH_DATA = os.environ.get("CSA_BENCH_DATA")

# Which store each budget is asserted against: the one the access pattern is meant to live in.
# Asserting the columnar budget against the row store would only prove the row store is a row store.
BUDGET_STORE = {
    "q1_customer_profile_lookup": "redis",
    "q2_customer_12m_spend_by_category": "postgres",
    "q3_category_month_spend_all_customers": "duckdb",
    "q4_top_merchants_per_segment": "duckdb",
    "q5_asof_fx_revaluation": "duckdb",
}


def _budgets() -> dict[str, int]:
    settings = yaml.safe_load((REPO_ROOT / "config" / "settings.yaml").read_text())
    return settings["benchmark"]["budgets_ms"]


@pytest.fixture(scope="module")
def bench_env() -> dict[str, Any]:
    if not BENCH_DATA:
        pytest.skip("set CSA_BENCH_DATA to a dataset built with the bench profile")
    root = Path(BENCH_DATA)
    if not (root / "lake" / "marts").exists():
        pytest.skip(f"{root} has no gold layer; run generate, ingest, build and load first")
    params = _pick_params(root)
    stores = {"duckdb": DuckStore(root / "lake"), "postgres": PostgresStore(), "redis": KeyValueStore()}
    yield {"root": root, "params": params, "stores": stores}
    for store in stores.values():
        store.close()


@pytest.mark.parametrize("pattern_key", sorted(BUDGET_STORE))
def test_pattern_stays_inside_its_budget(bench_env, pattern_key: str) -> None:
    budgets = _budgets()
    store = bench_env["stores"][BUDGET_STORE[pattern_key]]
    if not store.available():
        pytest.skip(f"{store.name} is not reachable")

    params = {k: v for k, v in bench_env["params"].items() if k in {"customer_id", "from_date", "from_month"}}
    store.run(pattern_key, **params)  # warm up

    samples = []
    for _ in range(5):
        started = time.perf_counter()
        store.run(pattern_key, **params)
        samples.append((time.perf_counter() - started) * 1000)

    p50 = statistics.median(samples)
    budget = budgets[pattern_key]
    pattern = PATTERNS_BY_KEY[pattern_key]
    assert p50 <= budget, (
        f"{pattern.title} on {store.name}: p50 {p50:.0f}ms exceeds the {budget}ms budget "
        f"over {bench_env['params']['transactions']:,} transactions"
    )


def test_the_dataset_is_actually_at_benchmark_scale(bench_env) -> None:
    """Guards against the budgets quietly passing because someone pointed this at the dev dataset."""
    assert bench_env["params"]["transactions"] >= 5_000_000, (
        f"only {bench_env['params']['transactions']:,} transactions; this is not the bench profile"
    )
