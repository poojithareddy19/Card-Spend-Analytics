"""The benchmark.

Rules this harness follows, because a benchmark that does not state them is not a benchmark:

* **Warm up, then measure.** The first run of anything pays for page cache, plan caching and
  connection setup. Those costs are real but they are not what is being compared here.
* **Report a distribution, not a number.** p50 and p95 over N repeats. A single timing is noise.
* **Same question everywhere.** Every store answers the patterns defined in ``stores/base.py``.
  Where a store cannot, the results table says "not supported" rather than quietly omitting it.
* **Record the scale.** Every published figure carries the row count it was measured on, so two
  runs on two machines can be compared honestly or not at all.
"""

from __future__ import annotations

import datetime as dt
import json
import platform
import statistics
import time
from dataclasses import asdict, dataclass, field
from functools import partial
from pathlib import Path
from typing import Any

import duckdb

from card_spend.duck import scalar
from card_spend.stores.base import PATTERNS, UnsupportedPatternError
from card_spend.stores.duck import DuckStore
from card_spend.stores.kv import KeyValueStore
from card_spend.stores.postgres import DEFAULT_DSN, PostgresStore


@dataclass
class Timing:
    pattern: str
    store: str
    supported: bool
    rows: int = 0
    p50_ms: float = 0.0
    p95_ms: float = 0.0
    mean_ms: float = 0.0
    min_ms: float = 0.0
    repeats: int = 0
    note: str = ""


@dataclass
class BenchmarkReport:
    dataset: str
    transactions: int
    customers: int
    partitions: int
    machine: dict[str, Any] = field(default_factory=dict)
    timings: list[dict[str, Any]] = field(default_factory=list)
    optimisations: list[dict[str, Any]] = field(default_factory=list)
    stores: dict[str, str] = field(default_factory=dict)


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return values[0]
    ordered = sorted(values)
    idx = min(len(ordered) - 1, round((pct / 100) * (len(ordered) - 1)))
    return ordered[idx]


def _time_it(fn: Any, repeats: int, warmup: int = 1) -> tuple[list[float], int]:
    rows = 0
    for _ in range(warmup):
        rows = len(fn())
    samples: list[float] = []
    for _ in range(repeats):
        started = time.perf_counter()
        rows = len(fn())
        samples.append((time.perf_counter() - started) * 1000)
    return samples, rows


def _pick_params(data_dir: Path) -> dict[str, Any]:
    """A busy customer and a real date window, taken from the data rather than hard-coded.

    Read from the gold layer rather than the DuckDB warehouse, so the benchmark depends on the
    published interface and not on the engine that happened to build it. At benchmark scale that
    also means the 8 GB warehouse file can be deleted before the benchmark runs.
    """
    gold = (data_dir / "lake" / "marts").resolve()
    fact = f"read_parquet('{gold}/fct_card_transaction/*/*.parquet', hive_partitioning = 1)"
    con = duckdb.connect(":memory:")
    try:
        customer_id = scalar(
            con,
            f"""
            select customer_id from {fact}
            where customer_id <> 'UNKNOWN'
            group by 1 order by count(*) desc limit 1
            """,
        )
        max_date = scalar(con, f"select max(cast(posted_date as date)) from {fact}")
        txns = scalar(con, f"select count(*) from {fact}")
        customers = scalar(con, f"select count(*) from read_parquet('{gold}/dim_customer.parquet') where is_current")
    finally:
        con.close()
    from_date = max_date - dt.timedelta(days=365)
    return {
        "customer_id": customer_id,
        "from_date": from_date,
        "from_month": from_date.strftime("%Y-%m"),
        "max_date": max_date,
        "transactions": int(txns),
        "customers": int(customers),
    }


def run_benchmark(data_dir: Path, repeats: int = 5, out_path: Path | None = None, dsn: str = DEFAULT_DSN) -> str:
    params = _pick_params(data_dir)

    duck = DuckStore(data_dir / "lake")
    pg = PostgresStore(dsn)
    kv = KeyValueStore()
    stores = [s for s in (kv, pg, duck)]

    report = BenchmarkReport(
        dataset=str(data_dir),
        transactions=params["transactions"],
        customers=params["customers"],
        partitions=len(list((data_dir / "lake" / "marts" / "fct_card_transaction").glob("posted_date=*"))),
        machine={
            "python": platform.python_version(),
            "platform": platform.platform(),
            "processor": platform.machine(),
            "duckdb": duckdb.__version__,
        },
        stores={s.name: s.kind for s in stores},
    )

    for pattern in PATTERNS:
        for store in stores:
            if not store.available():
                report.timings.append(asdict(Timing(pattern.key, store.name, False, note="store not reachable")))
                continue
            if not store.supports(pattern.key):
                report.timings.append(asdict(Timing(pattern.key, store.name, False, note="not supported")))
                continue
            call_params = {k: v for k, v in params.items() if k in {"customer_id", "from_date", "from_month"}}
            try:
                run = partial(store.run, pattern.key, **call_params)
                samples, rows = _time_it(run, repeats)
            except UnsupportedPatternError as exc:
                report.timings.append(asdict(Timing(pattern.key, store.name, False, note=str(exc))))
                continue
            report.timings.append(
                asdict(
                    Timing(
                        pattern=pattern.key,
                        store=store.name,
                        supported=True,
                        rows=rows,
                        p50_ms=round(statistics.median(samples), 2),
                        p95_ms=round(_percentile(samples, 95), 2),
                        mean_ms=round(statistics.fmean(samples), 2),
                        min_ms=round(min(samples), 2),
                        repeats=repeats,
                    )
                )
            )

    report.optimisations = _optimisation_round(data_dir, pg, duck, params, repeats)

    for store in stores:
        store.close()

    markdown = render_markdown(report)
    if out_path:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(markdown, encoding="utf-8")
        out_path.with_suffix(".json").write_text(json.dumps(asdict(report), indent=2, default=str), encoding="utf-8")
    return markdown


# ------------------------------------------------------------------------------------------------
# The optimisation round. Each entry is a pair of queries that answer the same question two ways,
# so the number published is a difference rather than an absolute anyone can dispute.
# ------------------------------------------------------------------------------------------------


def _optimisation_round(
    data_dir: Path, pg: PostgresStore, duck: DuckStore, params: dict[str, Any], repeats: int
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []

    if pg.available():
        con = pg.connect()

        def from_aggregate() -> list[Any]:
            with con.cursor() as cur:
                cur.execute(
                    """
                    select month_key, category, sum(net_spend_gbp_minor)
                    from marts.agg_customer_category_month
                    where customer_id = %(customer_id)s and month_key >= %(from_month)s
                    group by 1, 2
                    """,
                    params,
                )
                rows: list[Any] = cur.fetchall()
                return rows

        def from_fact() -> list[Any]:
            with con.cursor() as cur:
                cur.execute(
                    """
                    select to_char(f.posted_date, 'YYYY-MM'), coalesce(m.category, 'unknown'),
                           sum(f.amount_gbp_minor)
                    from marts.fct_card_transaction f
                    join marts.dim_txn_context c on c.txn_context_key = f.txn_context_key
                    left join marts.dim_merchant m on m.merchant_key = f.merchant_key
                    where f.customer_id = %(customer_id)s and f.posted_date >= %(from_date)s and c.is_settled
                    group by 1, 2
                    """,
                    params,
                )
                rows: list[Any] = cur.fetchall()
                return rows

        agg_samples, _ = _time_it(from_aggregate, repeats)
        fact_samples, _ = _time_it(from_fact, repeats)
        out.append(
            {
                "name": "Pre-aggregate vs the raw fact (Postgres, one customer, 12 months)",
                "before_ms": round(statistics.median(fact_samples), 2),
                "after_ms": round(statistics.median(agg_samples), 2),
                "speedup": round(statistics.median(fact_samples) / max(statistics.median(agg_samples), 0.001), 1),
                "why": (
                    "The aggregate collapses a customer's year of rows into one row per month and "
                    "category. The index on (customer_id, month_key) then answers the question "
                    "without touching the fact at all."
                ),
            }
        )

    if duck.available():
        lake = duck.lake
        one_month = params["max_date"].strftime("%Y-%m")

        def full_scan() -> list[Any]:
            rows: list[Any] = duck.con.execute(
                f"""
                select count(*), sum(amount_minor)
                from read_parquet('{lake}/marts/fct_card_transaction/*/*.parquet', hive_partitioning = 1)
                """
            ).fetchall()
            return rows

        def pruned() -> list[Any]:
            rows: list[Any] = duck.con.execute(
                f"""
                select count(*), sum(amount_minor)
                from read_parquet('{lake}/marts/fct_card_transaction/*/*.parquet', hive_partitioning = 1)
                where posted_date >= date '{one_month}-01'
                """
            ).fetchall()
            return rows

        full_samples, _ = _time_it(full_scan, repeats)
        pruned_samples, _ = _time_it(pruned, repeats)
        fact_dir = data_dir / "lake" / "marts" / "fct_card_transaction"
        files_total = len(list(fact_dir.glob("posted_date=*")))
        files_touched = len(list(fact_dir.glob(f"posted_date={one_month}-*")))
        out.append(
            {
                "name": "Partition pruning (DuckDB, one month out of the whole history)",
                "before_ms": round(statistics.median(full_samples), 2),
                "after_ms": round(statistics.median(pruned_samples), 2),
                "speedup": round(statistics.median(full_samples) / max(statistics.median(pruned_samples), 0.001), 1),
                "why": (
                    f"The filter is on the partition key, so the engine opens {files_touched} of "
                    f"{files_total} files. This is the single largest lever in the project and the "
                    "reason the lake is partitioned by posting date rather than by anything else."
                ),
            }
        )

    return out


def render_markdown(report: BenchmarkReport) -> str:
    lines: list[str] = []
    lines.append("# Benchmark results\n")
    lines.append(
        f"Measured on **{report.transactions:,} transactions** across {report.partitions} daily "
        f"partitions, {report.customers:,} customers.\n"
    )
    lines.append(
        f"Machine: {report.machine.get('platform', '?')}, Python {report.machine.get('python', '?')}, "
        f"DuckDB {report.machine.get('duckdb', '?')}.\n"
    )
    lines.append("Every figure is the median of the stated number of repeats after one warm-up run.\n")

    lines.append("## Stores\n")
    lines.append("| Store | Kind |")
    lines.append("|---|---|")
    for name, kind in report.stores.items():
        lines.append(f"| `{name}` | {kind} |")
    lines.append("")

    by_pattern: dict[str, list[dict[str, Any]]] = {}
    for t in report.timings:
        by_pattern.setdefault(t["pattern"], []).append(t)

    lines.append("## Access patterns\n")
    for pattern in PATTERNS:
        rows = by_pattern.get(pattern.key, [])
        lines.append(f"### {pattern.title}\n")
        lines.append(f"> {pattern.question}\n")
        lines.append(f"Expected home: **{pattern.expected_store}**. Latency class: {pattern.latency_class}.\n")
        lines.append("| Store | p50 ms | p95 ms | min ms | rows | note |")
        lines.append("|---|---:|---:|---:|---:|---|")
        for r in sorted(rows, key=lambda x: (not x["supported"], x["p50_ms"])):
            if r["supported"]:
                lines.append(
                    f"| `{r['store']}` | {r['p50_ms']:.2f} | {r['p95_ms']:.2f} | {r['min_ms']:.2f} | {r['rows']:,} | |"
                )
            else:
                lines.append(f"| `{r['store']}` | - | - | - | - | {r['note']} |")
        lines.append("")

    if report.optimisations:
        lines.append("## Optimisation round\n")
        lines.append("| Change | Before | After | Speedup |")
        lines.append("|---|---:|---:|---:|")
        for o in report.optimisations:
            lines.append(f"| {o['name']} | {o['before_ms']:.1f} ms | {o['after_ms']:.1f} ms | {o['speedup']}x |")
        lines.append("")
        for o in report.optimisations:
            lines.append(f"**{o['name']}**\n\n{o['why']}\n")

    return "\n".join(lines)
