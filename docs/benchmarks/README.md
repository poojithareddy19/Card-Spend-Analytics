# Benchmarks

Two published runs. Both are machine output from `card-spend bench`; the narrative lives here so the
results files stay regenerable.

| Run | Scale | Stores measured | File |
|---|---:|---|---|
| **dev** | 576,674 transactions, 90 daily partitions, 5,000 customers | Redis, Postgres, DuckDB | [`results.md`](results.md) |
| **bench** | 54,007,505 transactions, 365 daily partitions, 120,000 customers | Redis, DuckDB | [`results-bench-50m.md`](results-bench-50m.md) |

Reference machine for both: **2 cores, 7 GB RAM**, Linux, Python 3.11, DuckDB 1.5.5, PostgreSQL 16,
Redis 7. Every figure is the median of five repeats after one warm-up run.

---

## The three-store result, at dev scale

This is the run that answers the architectural question, because all three stores are loaded.

| Access pattern | Redis | Postgres | DuckDB | Winner |
|---|---:|---:|---:|---|
| Customer profile lookup | **0.42 ms** | 1.37 ms | 2.61 ms | key-value |
| One customer, 12 months by category | not supported | **0.72 ms** | 87.98 ms | relational |
| Spend by category and month, whole book | not supported | 359.95 ms | **109.32 ms** | columnar |
| Top merchants per segment | not supported | 264.65 ms | **181.14 ms** | columnar |
| As-of FX revaluation, full history | not supported | **67.47 ms** | 72.23 ms | relational, narrowly |

**Every store wins something, and that is the finding.** The architecture is three stores rather than
one because the access patterns genuinely want different things:

- Redis answers a single-key deep read in under half a millisecond and **cannot answer the other four
  at all**. A store that does one job three times faster than the alternatives and nothing else is a
  real architectural choice, and a comparison that only included general-purpose stores would hide it.
- Postgres wins the customer hot path by **122x** over the lake, entirely because of the
  pre-aggregate and its index. That is a modelling decision paying off, not an engine difference.
- DuckDB wins both whole-book scans, and the margin grows with scale.
- **q5 is the honest surprise.** At dev scale Postgres edges the as-of FX revaluation, 67 ms against
  72. The query aggregates a few hundred thousand rows after a small join, which is small enough that
  the row store's tighter loop beats the column store's scan. The expectation table in the
  [physical model](../model/04_logical_and_physical.md) predicted columnar, and it was wrong at this
  scale. The point of measuring is to find that out.

### Optimisation round, dev scale

| Change | Before | After | Speedup |
|---|---:|---:|---:|
| Pre-aggregate instead of the raw fact (Postgres) | 2.65 ms | 0.38 ms | **6.9x** |
| Partition pruning, one month of the history (DuckDB) | 26.9 ms | 10.8 ms | **2.5x** |

---

## At 54 million rows

| Access pattern | Redis | DuckDB | vs dev scale |
|---|---:|---:|---|
| Customer profile lookup | **0.53 ms** | 55.5 ms | Redis flat; DuckDB 21x slower |
| One customer, 12 months by category | not supported | 7,322 ms | **83x slower** |
| Spend by category and month, whole book | not supported | 11,282 ms | 103x slower |
| Top merchants per segment | not supported | 17,200 ms | 95x slower |
| As-of FX revaluation, full history | not supported | 8,566 ms | 119x slower |

Data grew 94x. Three of the four analytical patterns grew roughly in line with it, which is what a
full scan should do and is the reassuring result. Two things stand out:

**Redis is flat.** 0.42 ms at half a million rows, 0.53 ms at fifty-four million. A key lookup does
not care how much data exists, which is the entire reason that pattern has its own store.

**The customer hot path degrades worst of all, at 83x.** One customer's year of spend now takes
**seven and a half seconds** from the lake. Nothing about that query got harder, but the lake has no
index on customer, so answering it means touching every partition. This is the single strongest
argument in the project for the relational serving layer: the same question is sub-millisecond from
Postgres and seven seconds from the lake, and the gap widens with every row added.

### Partition pruning improves with scale

| Scale | Full scan | One month | Files opened | Speedup |
|---|---:|---:|---|---:|
| 576,674 rows, 90 partitions | 26.9 ms | 10.8 ms | 30 of 90 | 2.5x |
| 54,007,505 rows, 365 partitions | 4,334 ms | 743 ms | 30 of 365 | **5.8x** |

The speedup rises because the pruned fraction rises: at dev scale a month is a third of the history,
at benchmark scale it is a twelfth. This is why the lake is partitioned by posting date rather than
by anything else, and `tests/performance/test_plan_shape.py` asserts that a single-day filter opens
exactly one file so the property cannot silently regress.

---

## Why Postgres is missing from the 54M run

It is missing because **it did not fit**, and that is worth stating rather than quietly dropping the
column.

The sandbox this was measured in has a fixed writable-disk allowance. Loading 54M rows into a
range-partitioned Postgres table with four indexes exhausted it: the `COPY` failed at 25.1M rows with
`DiskFull`, and a second attempt after freeing the raw layer and the 8 GB DuckDB warehouse failed
again. The measured footprints tell the story:

| | 54M rows |
|---|---:|
| Gold layer, partitioned Parquet with zstd | **3.1 GB** |
| Postgres pre-aggregate alone, 13.6M rows plus its index | **1.24 GB** |
| Postgres fact, projected from the partial load | ~6 GB, plus roughly 4 GB of indexes |

A column store holding 54 million rows in 3.1 GB against a row store needing something like ten is a
real result, not an excuse, and it is one of the reasons the analytical layer is the lake rather than
a bigger relational box.

Two things came out of the attempt and stayed in the code:

1. **The loader reads the gold layer, not the DuckDB warehouse.** Making the published Parquet the
   input to both serving stores means the 8 GB warehouse file can be deleted before the serving
   stores are filled, and it removes any chance of the serving stores drifting from the analytical
   store. See the docstring in `stores/load.py`.
2. **"Available" now means loaded, not reachable.** An empty Postgres schema and an empty Redis
   keyspace both answered instantly and would have published a spectacular, meaningless p50. Both
   stores now report themselves unavailable when they hold nothing, which is why the table above says
   "store not reachable" instead of showing a fast number on zero rows.

Also worth recording: the 54M dbt build only completed after capping DuckDB's `memory_limit` and
turning off `preserve_insertion_order`. At the default 80%-of-RAM setting the de-duplication window
over 54M rows was killed by the OOM killer **with no error message at all** — the process simply
vanished and the next pipeline step failed on a missing table. The reasoning is in `profiles.yml`.

## Reproducing

```bash
make all                                    # dev scale, all three stores, about two minutes
make bench

make bench-dataset                          # 54M rows; roughly an hour and ~15 GB
CSA_BENCH_DATA=data-bench pytest tests/performance -m bench
```

Same seed, same data, byte for byte. The absolute milliseconds will differ on your hardware; the
ratios are the part worth comparing.
