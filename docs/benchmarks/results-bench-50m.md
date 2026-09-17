# Benchmark results

Measured on **54,007,505 transactions** across 365 daily partitions, 120,001 customers.

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
| `redis` | 0.53 | 0.65 | 0.41 | 1 | |
| `duckdb` | 55.47 | 72.40 | 54.42 | 1 | |
| `postgres` | - | - | - | - | store not reachable |

### One customer's year of spend by category

> What has this customer spent, by category, each month for the last twelve months

Expected home: **relational**. Latency class: interactive, serves a screen.

| Store | p50 ms | p95 ms | min ms | rows | note |
|---|---:|---:|---:|---:|---|
| `duckdb` | 7321.89 | 7367.88 | 7243.35 | 202 | |
| `redis` | - | - | - | - | not supported |
| `postgres` | - | - | - | - | store not reachable |

### Spend by category and month, whole book

> How is spend split across merchant categories by month, across every customer

Expected home: **columnar**. Latency class: analytical, seconds are fine.

| Store | p50 ms | p95 ms | min ms | rows | note |
|---|---:|---:|---:|---:|---|
| `duckdb` | 11281.95 | 11342.88 | 10993.59 | 204 | |
| `redis` | - | - | - | - | not supported |
| `postgres` | - | - | - | - | store not reachable |

### Top merchants per segment

> Which twenty merchants take the most spend in each customer segment

Expected home: **columnar**. Latency class: analytical, seconds are fine.

| Store | p50 ms | p95 ms | min ms | rows | note |
|---|---:|---:|---:|---:|---|
| `duckdb` | 17199.84 | 17432.63 | 17024.17 | 80 | |
| `redis` | - | - | - | - | not supported |
| `postgres` | - | - | - | - | store not reachable |

### As-of FX revaluation over the full history

> Restate every foreign-currency transaction at the latest rate, for the whole history

Expected home: **columnar**. Latency class: batch, runs once per load.

| Store | p50 ms | p95 ms | min ms | rows | note |
|---|---:|---:|---:|---:|---|
| `duckdb` | 8565.77 | 8717.72 | 8242.31 | 5 | |
| `redis` | - | - | - | - | not supported |
| `postgres` | - | - | - | - | store not reachable |

## Optimisation round

| Change | Before | After | Speedup |
|---|---:|---:|---:|
| Partition pruning (DuckDB, one month out of the whole history) | 4334.0 ms | 742.9 ms | 5.8x |

**Partition pruning (DuckDB, one month out of the whole history)**

The filter is on the partition key, so the engine opens 30 of 365 files. This is the single largest lever in the project and the reason the lake is partitioned by posting date rather than by anything else.
