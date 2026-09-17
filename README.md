# Card spend analytics

A modelled, multi-store, benchmarked analytics layer over a UK retail bank's card spend.

Five feeds arrive in the format their real producer would use, are validated against contracts before
anything lands, modelled in dbt into a conformed star schema with Type 2 dimensions, exported to a
gold Parquet layer, and served from three different kinds of store. The same five access patterns are
then run against all three, and the numbers are published rather than asserted.

The question this repo answers is not "can a pipeline be built". It is the one a data management role
actually asks: **can you model a domain, choose the right store for each access pattern, make the
queries fast at volume, and explain the result to the product team that asked for it.**

---

## Quickstart

```bash
make setup
make all        # generate → ingest → build → load → report, about two minutes at dev scale
make bench      # benchmark every access pattern across every reachable store
```

`make all` needs nothing but Python. `make load` and the Postgres/Redis half of `make bench` need
those two services; everything else degrades gracefully and the results table records which stores
were reachable.

The Makefile runs on Windows as well as Linux and macOS. Native `make` there drives cmd.exe, which
needs the backslash interpreter path the Makefile picks by default; if you run make from Git Bash,
MSYS2 or WSL instead, pass the POSIX spelling: `make check PY=.venv/Scripts/python`. Every target is
a one-line `card-spend` call, so the CLI is always available if you would rather skip make entirely.

## What is in here

```
contracts/          Avro v1/v2, JSON Schema, and a YAML promise for the feeds with no machine schema
models/             dbt: staging → intermediate → marts, 19 models
snapshots/          dbt snapshots: SCD Type 2 on customer and account
dbt_tests/          5 singular dbt tests, including the lake-to-fact reconciliation
docs/model/         conceptual ERD, bus matrix, business glossary, logical and physical model
docs/product/       data product spec, source-to-target mapping, SLAs, an incident review, a findings one-pager
docs/benchmarks/    published results
src/card_spend/
  generate/         seeded generator: five feeds, five formats, eleven declared defects
  ingest/           contract enforcement and the canonical Parquet lake
  models/           dbt runner and the gold-layer export
  stores/           Postgres, DuckDB-over-Parquet, Redis: the same five patterns on each
  bench/            the benchmark harness
  reports/          nine reports and a self-contained HTML dashboard
tests/              56 tests: unit, integration, detection quality, and plan-shape gates
```

---

## 1. Five feeds, five formats

Every feed uses the format its real producer would use. A consumer that only ever reads CSV has not
met the problems this project is about.

| Feed | Format | Producer | Why that format |
|---|---|---|---|
| `customers` | Nested JSON Lines | Onboarding platform | `profile`, `address` and `kyc` are separate bounded contexts upstream |
| `card_transactions` | **Avro binary**, v1 and v2 in one history | Card processor | High-volume machine feed with a schema registry |
| `merchants` | **Parquet**, typed | Enrichment vendor | Warehouse-to-warehouse weekly full refresh |
| `accounts`, `cards` | CSV | Core banking nightly extract | Legacy producer with no machine schema |
| `fx_rates` | JSON, one document per day | Treasury rates API | Small daily document, with deliberate calendar gaps |

**Schema evolution is exercised, not claimed.** The processor upgrades from v1 to v2 two thirds of
the way through the window, adding two nullable fields with defaults. One reader schema decodes the
whole history in a single pass, and a `feed_version` column records which shape each file had, so a
null in `pos_entry_mode` means "the field did not exist" rather than "no value". Both directions of
compatibility are asserted in `tests/unit/test_generator.py`.

## 2. Contracts refuse bad feeds before anything lands

Three verdicts. `compatible`, `additive` (load it, record it, chase the producer), and `breaking`
(refuse the batch). A rename is breaking, not additive, and the message names the missing field, the
new field, the producing system and the owning team, so it is actionable by the person who caused it.

```
$ card-spend contracts --data data
accounts: BREAKING, missing ['currency'], producer core-banking (legacy nightly extract),
          owner core-banking@example.com
```

The check reads headers only, so refusing a bad batch costs a fraction of a second rather than a full
load. Seven tests cover the refusals, because a gate that has never been shown to refuse anything is
indistinguishable from no gate.

## 3. Eleven declared defects, and rules measured against them

Defects are injected at stated rates and every affected `transaction_id` is written to a defect
ledger. That makes each rule's **recall and precision computable** rather than arguable:

| Rule | Defect | Recall | Precision |
|---|---|---:|---:|
| `dq_missing_merchant` | Purchase with no merchant | 100% | 100% |
| `dq_orphan_card` | Card not in the cards extract | 100% | 100% |
| `dq_negative_purchase` | Purchase with a negative amount | 100% | 100% |
| `dq_bad_mcc` | MCC outside ISO 18245 | 100% | 100% |
| `dq_late_posting` | Posted more than two days after authorisation | 100% | 100% |
| `dq_implausible_amount` | Minor/major unit slip | **84.0%** | **90.6%** |

The last row is the interesting one and it has [its own incident review](docs/product/04_incident_rca.md):
263 rows out of 539,373 moved the headline spend figure by 4.2%, every existing control passed, and
the replacement control is honestly partial. `pytest tests/integration/test_detection_quality.py`
reproduces those numbers and fails if the rule drifts.

## 4. The model

Four documents, written before any table existed:

- **[Conceptual ERD](docs/model/01_conceptual_erd.md)** — entities, relationships, the ten business
  rules the model must enforce, and an explicit list of what it leaves out
- **[Bus matrix](docs/model/02_bus_matrix.md)** — processes against conformed dimensions, with an
  honest note on the one dimension that would not conform
- **[Business glossary](docs/model/03_glossary.md)** — terms with owners and calculations, including
  one still disputed
- **[Logical and physical](docs/model/04_logical_and_physical.md)** — 3NF, then the star, with every
  denormalisation and its cost, measure additivity, and the partitioning plan

Built with **dbt** on DuckDB: 19 models, 2 snapshots, **78 tests** passing. SCD Type 2 on customer and
account with a `check` strategy; a junk dimension collapsing three text columns off a fact that reaches
hundreds of millions of rows; UNKNOWN members so an unmatched key lands on a real dimension row rather
than a null; and the first version of every entity backdated to its own start date, because a snapshot
captured today does not mean the entity began today.

## 5. Three layers, three stores

```
raw/            the five feeds exactly as their producers sent them
lake/           one canonical Parquet schema, partitioned by posted_date
lake/marts/     the gold layer: dbt output written back out as Parquet
```

The gold layer exists for the benchmark. Without it, DuckDB would read unmodelled rows while Postgres
read modelled ones, the two would disagree by exactly the duplicate count, and every cross-store
comparison would be noise.

| Store | Role | Serves |
|---|---|---|
| **Redis** | Key-value document store | The nested customer document, by key |
| **Postgres** | Relational serving marts | The app's hot path: indexed, range-partitioned by month |
| **DuckDB over Parquet** | Columnar analytical layer | Whole-book scans and window functions |

## 6. The benchmark

Five access patterns, defined once in `stores/base.py`, implemented per store. Warm up, then five
repeats, report p50 and p95, and record the scale every figure was measured on. Where a store cannot
answer a pattern, the table says so rather than quietly omitting it.

**Dev profile, 576,674 transactions, all three stores loaded:**

| Access pattern | Redis | Postgres | DuckDB | Winner |
|---|---:|---:|---:|---|
| Customer profile lookup | **0.42 ms** | 1.37 ms | 2.61 ms | key-value |
| One customer, 12 months by category | not supported | **0.72 ms** | 87.98 ms | relational |
| Spend by category and month, whole book | not supported | 359.95 ms | **109.32 ms** | columnar |
| Top merchants per segment | not supported | 264.65 ms | **181.14 ms** | columnar |
| As-of FX revaluation, full history | not supported | **67.47 ms** | 72.23 ms | relational, narrowly |

Every store wins something. That is the result: the architecture is three stores rather than one
because the access patterns genuinely want different things, and Redis earns its place by doing one
job three times faster while being unable to do the other four at all.

The last row is the honest surprise. The physical model predicted columnar for the FX revaluation and
was wrong at this scale: the query aggregates few enough rows after its join that the row store's
tighter loop wins. The point of measuring is to find that out.

**Bench profile, 54,007,505 transactions over 365 partitions:**

| Access pattern | Redis | DuckDB | Change from dev |
|---|---:|---:|---|
| Customer profile lookup | **0.53 ms** | 55.5 ms | Redis flat across 94x the data |
| One customer, 12 months by category | not supported | 7,322 ms | **83x slower** |
| Spend by category and month, whole book | not supported | 11,282 ms | 103x slower |
| Top merchants per segment | not supported | 17,200 ms | 95x slower |
| As-of FX revaluation, full history | not supported | 8,566 ms | 119x slower |

Data grew 94x and the scans grew roughly in line, which is what a full scan should do. Two things
stand out: **Redis does not move**, because a key lookup does not care how much data exists; and the
customer hot path degrades worst of all, taking seven and a half seconds from a lake that has no
index on customer. That gap, sub-millisecond against seven seconds for the same question, is the
strongest argument in the project for keeping a relational serving layer.

**Partition pruning gets better with scale**, because the pruned fraction gets bigger:

| Scale | Full scan | One month | Files opened | Speedup |
|---|---:|---:|---|---:|
| 576,674 rows | 26.9 ms | 10.8 ms | 30 of 90 | 2.5x |
| 54,007,505 rows | 4,334 ms | 743 ms | 30 of 365 | **5.8x** |

Postgres is absent from the 54M run because it did not fit: the same rows are **3.1 GB as
partitioned Parquet** and needed roughly ten in a row store with its indexes, which exhausted the
sandbox's disk allowance. That is recorded rather than quietly dropped, along with the two code
changes it produced and the OOM that only a `memory_limit` fixed:
[`docs/benchmarks/README.md`](docs/benchmarks/README.md).

## 7. Performance is a CI gate, not a claim

Timing assertions flap on a busy runner and everybody starts ignoring them. **Plan shape does not.**

`tests/performance/test_plan_shape.py` asserts that the hot path uses the aggregate index and never
scans it, that a date-filtered fact query prunes Postgres partitions, that a single-day filter opens
exactly one Parquet file out of ninety, and that both engines still return the same answer. Drop an
index or break the partition key and these fail on any machine, however slow.

`tests/performance/test_budgets.py` asserts the latency budgets in `config/settings.yaml` at
benchmark scale, and skips itself unless `CSA_BENCH_DATA` points at a dataset built with the `bench`
profile, so it can never pass for the wrong reason.

## 8. Reports and the dashboard

Nine reports, each carrying the business question it answers and **which date basis it uses**, because
the most common reporting bug in a card business is mixing posting date with authorisation date.
Output as CSV and as a self-contained HTML dashboard: no chart library, no CDN, inline SVG, light and
dark both chosen rather than inverted, a table view under every chart, and a palette validated for
colour-vision deficiency before anything was drawn.

```bash
make report && open out/dashboard.html
```

## 9. The stakeholder artefacts

The half of this role a `src/` directory never demonstrates. See
[`docs/product/`](docs/product/README.md):

1. **[Data product spec](docs/product/01_data_product_spec.md)** — the request as Cards product raised
   it, with acceptance criteria that are all enforced by tests, and the posting-date against
   authorisation-date decision that came out of a conversation rather than a modelling exercise
2. **[Source-to-target mapping](docs/product/02_source_to_target_mapping.md)** — column by column for
   all five feeds
3. **[Dataset SLAs](docs/product/03_dataset_slas.md)** — freshness, quality, correctness and latency,
   what happens on a breach, and what is deliberately not promised
4. **[Incident review](docs/product/04_incident_rca.md)** — the 4.2% overstatement, and why a
   scorecard reading 99.95% failed to show it
5. **[Findings one-pager](docs/product/05_findings_one_pager.md)** — a real finding, for a
   non-technical reader, with its caveats

## Scale profiles

| Profile | Customers | Days | Transactions | Generate | On disk |
|---|---:|---:|---:|---:|---:|
| `smoke` | 200 | 30 | ~7k | 0.2 s | 1 MB |
| `dev` | 5,000 | 90 | ~577k | 8 s | 21 MB |
| `bench` | 120,000 | 365 | ~50M | ~13 min | ~1.5 GB raw |

Generate times are the reference machine, the same 2-core box the benchmark budgets are calibrated
on. Expect roughly double on a laptop that is also running a container runtime: `dev` measured 15 s
there. The row counts do not move, only the clock.

Same seed, same output, byte for byte. `make bench-dataset` builds the benchmark dataset end to end.

## Testing

```bash
make test-fast   # generator, contracts, lake landing: seconds
make test        # everything, including dbt and the multi-store gates: ~2 minutes
make check       # ruff, ruff format, mypy --strict, then the full suite
```

56 tests across four tiers, plus 78 dbt tests inside the build. mypy runs in strict mode.

## Deliberately out of scope

Named here rather than silently omitted.

- **Authorisation and clearing as separate events.** The processor feed is already collapsed to one
  row per transaction. The lifecycle needs two facts and a matching process.
- **Balances, disputes and chargebacks.** Different grains, not variations on this one.
- **Financial crime monitoring and regulatory returns.** Different domains.
- **PII masking and role-based access.** `dim_customer` carries name, email and date of birth in
  clear. Masking them needs a serving boundary that authenticates a reader and a role model to
  authorise one, and neither exists here. The classification is a real piece of work and claiming it
  in a config file without building it would be worse than leaving it out.
- **Real-time.** This is a daily batch product.
- **Cloud deployment.** The stores run locally so anyone who clones the repo can reproduce the
  benchmark. A managed warehouse would make the numbers unrepeatable.

## Related

Companion repo: **Financial Reporting & Data Quality Engine**, which covers the governed pipeline and
data quality side in depth. This repo deliberately reuses that project's declared-defect and contract
patterns, applied to a different domain and a different question.

## Licence

Apache 2.0. See [`LICENSE`](LICENSE).
