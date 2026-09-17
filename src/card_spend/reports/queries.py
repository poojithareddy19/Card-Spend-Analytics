"""The reports.

Each entry carries the business question it answers and which date it uses, because the single most
common reporting bug in a card business is mixing posting date with authorisation date. Finance
closes a month on posting date; product measures behaviour on authorisation date. A transaction
authorised on 31 January and posted on 2 February belongs to January for one and February for the
other, and both are correct.

Every query reads the gold layer, so the reports and the benchmark see identical rows.
"""

from __future__ import annotations

from dataclasses import dataclass

FACT = "read_parquet('{lake}/marts/fct_card_transaction/*/*.parquet', hive_partitioning = 1)"
CTX = "read_parquet('{lake}/marts/dim_txn_context.parquet')"
MERCHANT = "read_parquet('{lake}/marts/dim_merchant.parquet')"
CUSTOMER = "read_parquet('{lake}/marts/dim_customer.parquet')"
SCORECARD = "read_parquet('{lake}/marts/agg_dq_scorecard.parquet')"


@dataclass(frozen=True)
class Report:
    key: str
    title: str
    question: str
    date_basis: str
    owner: str
    sql: str


REPORTS: tuple[Report, ...] = (
    Report(
        "headline",
        "Headline numbers",
        "What are the totals a reader needs before any chart makes sense",
        "posted date",
        "finance@example.com",
        f"""
        select
            sum(f.amount_gbp_minor) / 100.0                                   as spend_gbp,
            count(*)                                                          as txn_count,
            count(distinct f.customer_id)                                     as active_customers,
            sum(f.amount_gbp_minor) / nullif(count(*), 0) / 100.0             as avg_basket_gbp,
            avg(f.dq_score)                                                   as avg_dq_score,
            sum(case when c.is_card_not_present then f.amount_gbp_minor else 0 end)
                / nullif(sum(f.amount_gbp_minor), 0)                          as online_share
        from {FACT} f
        join {CTX} c on c.txn_context_key = f.txn_context_key
        where c.is_settled and c.counts_towards_spend
        """,
    ),
    Report(
        "spend_by_month",
        "Net spend by month",
        "How is total card spend trending month on month",
        "posted date",
        "finance@example.com",
        f"""
        select
            strftime(cast(f.posted_date as date), '%Y-%m')  as month_key,
            sum(f.amount_gbp_minor) / 100.0                 as spend_gbp,
            count(*)                                        as txn_count
        from {FACT} f
        join {CTX} c on c.txn_context_key = f.txn_context_key
        where c.is_settled and c.counts_towards_spend
        group by 1 order by 1
        """,
    ),
    Report(
        "spend_by_category",
        "Spend by merchant category",
        "Where does the money go, by what the merchant sells",
        "posted date",
        "cards-product@example.com",
        f"""
        select
            coalesce(m.category, 'unknown')                 as category,
            sum(f.amount_gbp_minor) / 100.0                 as spend_gbp,
            count(*)                                        as txn_count,
            sum(f.amount_gbp_minor) / nullif(count(*), 0) / 100.0 as avg_basket_gbp
        from {FACT} f
        join {CTX} c on c.txn_context_key = f.txn_context_key
        left join {MERCHANT} m on m.merchant_key = f.merchant_key
        where c.is_settled and c.counts_towards_spend
        group by 1 having sum(f.amount_gbp_minor) > 0
        order by 2 desc
        """,
    ),
    Report(
        "channel_mix_by_month",
        "Card-present against online spend",
        "How much of our spend is moving to card-not-present, and how fast",
        "posted date",
        "cards-product@example.com",
        f"""
        select
            strftime(cast(f.posted_date as date), '%Y-%m')  as month_key,
            sum(case when not c.is_card_not_present then f.amount_gbp_minor else 0 end) / 100.0 as card_present_gbp,
            sum(case when c.is_card_not_present     then f.amount_gbp_minor else 0 end) / 100.0 as online_gbp
        from {FACT} f
        join {CTX} c on c.txn_context_key = f.txn_context_key
        where c.is_settled and c.counts_towards_spend
        group by 1 order by 1
        """,
    ),
    Report(
        "spend_by_segment",
        "Spend and basket by customer segment",
        "Which segments carry the book, and how differently do they behave",
        "posted date",
        "customer-marketing@example.com",
        f"""
        select
            cu.segment,
            count(distinct f.customer_id)                   as customers,
            sum(f.amount_gbp_minor) / 100.0                 as spend_gbp,
            sum(f.amount_gbp_minor) / nullif(count(*), 0) / 100.0 as avg_basket_gbp,
            sum(f.amount_gbp_minor) / nullif(count(distinct f.customer_id), 0) / 100.0 as spend_per_customer_gbp
        from {FACT} f
        join {CTX} c on c.txn_context_key = f.txn_context_key
        join {CUSTOMER} cu on cu.customer_id = f.customer_id and cu.is_current
        where c.is_settled and c.counts_towards_spend and cu.segment <> 'unknown'
        group by 1 order by 3 desc
        """,
    ),
    Report(
        "dq_by_dimension",
        "Data quality pass rate by dimension",
        "Which quality dimension is costing us the most rows, and is it moving",
        "posted date",
        "data-platform@example.com",
        f"""
        select
            dimension,
            sum(rows_checked)                                                  as rows_checked,
            sum(rows_failed)                                                   as rows_failed,
            1.0 - sum(rows_failed) / nullif(cast(sum(rows_checked) as double), 0) as pass_rate
        from {SCORECARD}
        group by 1 order by 4 asc
        """,
    ),
    Report(
        "dq_by_defect",
        "Data quality pass rate by rule",
        "Exactly which rule is firing, so the producer can be told which field to fix",
        "posted date",
        "data-platform@example.com",
        f"""
        select
            defect_code,
            dimension,
            sum(rows_checked)                                                  as rows_checked,
            sum(rows_failed)                                                   as rows_failed,
            1.0 - sum(rows_failed) / nullif(cast(sum(rows_checked) as double), 0) as pass_rate
        from {SCORECARD}
        group by 1, 2 order by 5 asc
        """,
    ),
    Report(
        "fx_exposure",
        "Foreign currency exposure",
        "What would the foreign-currency book be worth if we restated it at today's rate",
        "posted date",
        "treasury@example.com",
        f"""
        with latest as (
            select currency, rate_to_gbp from (
                select currency, rate_to_gbp,
                       row_number() over (partition by currency order by rate_date desc) as rn
                from read_parquet('{{lake}}/marts/fx_rate.parquet')
            ) where rn = 1
        )
        select
            f.currency_key                                          as currency,
            count(*)                                                as txn_count,
            sum(f.amount_gbp_minor) / 100.0                         as booked_gbp,
            sum(f.amount_minor * l.rate_to_gbp) / 100.0             as revalued_gbp,
            (sum(f.amount_minor * l.rate_to_gbp) - sum(f.amount_gbp_minor)) / 100.0 as unrealised_gbp
        from {FACT} f
        join {CTX} c on c.txn_context_key = f.txn_context_key
        join latest l on l.currency = f.currency_key
        where f.currency_key <> 'GBP' and c.is_settled
        group by 1 order by 5 asc
        """,
    ),
    Report(
        "posting_lag",
        "Posting lag against the two-day SLO",
        "Is the gap between authorisation and posting widening",
        "both, and that is the point",
        "data-platform@example.com",
        f"""
        select
            strftime(cast(f.posted_date as date), '%Y-%m')          as month_key,
            avg(f.posting_lag_days)                                 as avg_lag_days,
            quantile_cont(f.posting_lag_days, 0.95)                 as p95_lag_days,
            sum(case when f.posting_lag_days > 2 then 1 else 0 end) as breaches,
            count(*)                                                as txn_count
        from {FACT} f
        group by 1 order by 1
        """,
    ),
)

REPORTS_BY_KEY = {r.key: r for r in REPORTS}
