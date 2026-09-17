# Benchmark results

Measured on **576,674 transactions** across 90 daily partitions, 5,001 customers.

Machine: Linux-6.18.44-fc-v33-x86_64-with-glibc2.39, Python 3.11.15, DuckDB 1.5.5.

Every figure is the median of the stated number of repeats after one warm-up run.

## Stores

| Store | Kind |
|---|---|
| `redis` | key-value document store |
| `postgres` | relational serving marts |
| `duckdb` | columnar over parquet lake |

## Access patterns

### Customer profile lookup

> Show me everything we hold about this one customer, including their nested KYC and address

Expected home: **document**. Latency class: interactive, serves a screen.

| Store | p50 ms | p95 ms | min ms | rows | note |
|---|---:|---:|---:|---:|---|
| `redis` | 0.42 | 0.62 | 0.37 | 1 | |
| `postgres` | 1.37 | 1.48 | 1.17 | 1 | |
| `duckdb` | 2.61 | 3.62 | 2.33 | 1 | |

### One customer's year of spend by category

> What has this customer spent, by category, each month for the last twelve months

Expected home: **relational**. Latency class: interactive, serves a screen.

| Store | p50 ms | p95 ms | min ms | rows | note |
|---|---:|---:|---:|---:|---|
| `postgres` | 0.72 | 0.90 | 0.61 | 50 | |
| `duckdb` | 87.98 | 93.11 | 85.57 | 50 | |
| `redis` | - | - | - | - | not supported |

### Spend by category and month, whole book

> How is spend split across merchant categories by month, across every customer

Expected home: **columnar**. Latency class: analytical, seconds are fine.

| Store | p50 ms | p95 ms | min ms | rows | note |
|---|---:|---:|---:|---:|---|
| `duckdb` | 109.32 | 112.02 | 107.75 | 51 | |
| `postgres` | 359.95 | 407.11 | 349.96 | 51 | |
| `redis` | - | - | - | - | not supported |

### Top merchants per segment

> Which twenty merchants take the most spend in each customer segment

Expected home: **columnar**. Latency class: analytical, seconds are fine.

| Store | p50 ms | p95 ms | min ms | rows | note |
|---|---:|---:|---:|---:|---|
| `duckdb` | 181.14 | 214.31 | 175.89 | 80 | |
| `postgres` | 264.65 | 278.64 | 255.03 | 80 | |
| `redis` | - | - | - | - | not supported |

### As-of FX revaluation over the full history

> Restate every foreign-currency transaction at the latest rate, for the whole history

Expected home: **columnar**. Latency class: batch, runs once per load.

| Store | p50 ms | p95 ms | min ms | rows | note |
|---|---:|---:|---:|---:|---|
| `postgres` | 67.47 | 69.23 | 66.57 | 5 | |
| `duckdb` | 72.23 | 73.45 | 71.96 | 5 | |
| `redis` | - | - | - | - | not supported |

## Optimisation round

| Change | Before | After | Speedup |
|---|---:|---:|---:|
| Pre-aggregate vs the raw fact (Postgres, one customer, 12 months) | 2.6 ms | 0.4 ms | 6.9x |
| Partition pruning (DuckDB, one month out of the whole history) | 26.9 ms | 10.8 ms | 2.5x |

**Pre-aggregate vs the raw fact (Postgres, one customer, 12 months)**

The aggregate collapses a customer's year of rows into one row per month and category. The index on (customer_id, month_key) then answers the question without touching the fact at all.

**Partition pruning (DuckDB, one month out of the whole history)**

The filter is on the partition key, so the engine opens 30 of 90 files. This is the single largest lever in the project and the reason the lake is partitioned by posting date rather than by anything else.
