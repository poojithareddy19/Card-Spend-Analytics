# Dataset SLAs and SLOs

"Maintain data accuracy, integrity and consistency" is only a commitment once it has a number, a
clock and a name attached. This is that.

Two words used precisely:

- **SLA** — what consumers are promised. Breaching it is a conversation with the consumer.
- **SLO** — what the team targets internally. It is tighter, and breaching it is a conversation
  inside the team, before the consumer notices.

---

## Availability and freshness

| Dataset | Available by (SLA) | Target (SLO) | Measured as |
|---|---|---|---|
| `lake/card_transactions` | 05:30 Europe/London, T+1 | 05:00 | Partition for T-1 exists and its row count is within 3 sigma of the trailing 28 days |
| `lake/customers`, `accounts`, `cards` | 04:00, T+1 | 03:30 | File mtime and row count |
| `lake/fx_rates` | 02:00, daily | 01:30 | A rate exists for every active currency, or the spine carries one forward |
| `marts.fct_card_transaction` | **07:00, T+1** | 06:30 | Build completed and every dbt test passed |
| `marts.agg_customer_category_month` | 07:00, T+1 | 06:30 | Built from the fact in the same run |
| Redis customer documents | 07:00, T+1 | 06:30 | Key count within 1% of `dim_customer` current rows |

The 07:00 promise on the marts is the one consumers actually feel: it is the time the app and the
morning reports start reading. Everything upstream is timed backwards from it with roughly ninety
minutes of slack.

## Quality objectives

Measured per build against the rule set. Current pass rates on the reference dev dataset, seed
`20260916`:

| Dimension | Objective | Current | Status |
|---|---|---|---|
| Completeness | >= 99.5% | 99.621% | Meets |
| Consistency | >= 99.5% | 99.801% | Meets |
| Uniqueness | >= 99.9% | 99.852% | **Below.** Duplicate emissions from the processor; see below |
| Validity | >= 99.9% | 99.913% | Meets |
| Accuracy | >= 99.9% | 99.958% | Meets |
| Timeliness | >= 99.0% | 99.402% | Meets |

Uniqueness sits below its objective and that is not a modelling failure: the processor really does
emit some rows twice, the pipeline really does remove them, and the objective is set against the
*source*, deliberately, because raising it would hide a producer problem inside a transformation.
The open action is with the card processor, not with the data team.

## Correctness objectives

These are binary. There is no acceptable rate of failure and a breach blocks the build.

| Objective | How it is enforced |
|---|---|
| Every source transaction appears exactly once in the fact | `dbt_tests/assert_no_transaction_lost_between_lake_and_fact.sql` |
| A GBP transaction converts to itself | `dbt_tests/assert_gbp_conversion_is_identity_for_gbp.sql` |
| No transaction is left without an FX rate | `dbt_tests/assert_every_transaction_has_an_fx_rate.sql` |
| No two versions of a customer are valid at the same instant | `dbt_tests/assert_scd2_versions_do_not_overlap.sql` |
| The pre-aggregate agrees with the fact it summarises | `tests/integration/test_models.py` |
| Every quality score is between 0 and 100 | `dbt_tests/assert_dq_score_in_range.sql` |

## Latency objectives for consumers

| Access pattern | Store | Objective (p50) | Measured |
|---|---|---:|---:|
| Customer profile lookup | Redis | 25 ms | 0.5 ms |
| One customer's twelve months by category | Postgres | 150 ms | 0.7 ms |
| Spend by category and month, whole book | DuckDB over the lake | 15 s | 11.3 s |
| Top merchants per segment | DuckDB over the lake | 22 s | 17.2 s |
| As-of FX revaluation, full history | DuckDB over the lake | 12 s | 8.6 s |

Held in `config/settings.yaml` so they can be argued about in a pull request rather than buried in a
test file, and asserted by `tests/performance/test_budgets.py` at benchmark scale.

Two honest notes. The objectives are **calibrated from the published 54M-row run with about 30%
headroom on a two-core reference machine**, not chosen in advance: a budget nobody measured either
never fires or fires constantly, and either way stops being read. And the analytical budgets are
seconds because the lake answers them by scanning; a production deployment that needed them to be
sub-second would add a pre-aggregate, exactly as the relational hot path already does.

## What happens on a breach

| Breach | Who is told | What consumers should do |
|---|---|---|
| **Freshness**, marts not built by 07:00 | Data platform on-call; a notice on the dashboard | Yesterday's marts remain readable and correct. Use them, and note the as-of date |
| **Contract**, a feed breaks its promise | The producing team by name, from the contract itself | Nothing landed. The previous build is intact. Expect a delay, not wrong numbers |
| **Quality**, a dimension falls below objective | Data platform, and the rule's owner | The rows still load, flagged and scored. Filter on `dq_score` if the analysis is sensitive |
| **Correctness**, any binary objective fails | Data platform, immediately; the build stops | Nothing was published. The previous build is the current one |
| **Latency**, a pattern exceeds its budget | Data platform | The answer is still correct, just slower |

The distinction that matters: a contract or correctness breach means **no new data**, and a quality
or freshness breach means **data that is late or imperfect but still honest**. Consumers are told
which one they are looking at rather than being left to guess from a red icon.

## What is deliberately not promised

- **Intraday freshness.** This is a daily batch product. Nothing here is true within the day.
- **Restatement of history when a rate is corrected.** Today a corrected FX rate applies forward
  only. Restating history is possible and not currently done, and consumers should know that.
- **Sub-second answers on the whole book.** The analytical patterns are seconds, by design.

## Review

Quarterly, with Cards product and Finance. Objectives move when the business need moves, and a
target nobody has looked at in a year is not a target.
