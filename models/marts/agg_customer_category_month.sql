-- Pre-aggregate for the serving layer.
--
-- Access pattern 2 ("one customer's 12 month spend by category") is the app's hot path and has a
-- latency requirement. Answering it from the fact means scanning a customer's year of rows on every
-- page load. This collapses it to twelve rows per customer per category, which is what makes the
-- Postgres side of the day-5 benchmark sub-100ms rather than sub-second.
select
    f.customer_id,
    d.calendar_month_key                                as month_key,
    coalesce(m.category, 'unknown')                     as category,
    -- Cast back to bigint: DuckDB widens a sum of bigint to hugeint, and Parquet has no hugeint,
    -- so the column would land in the gold layer as a double and every consumer would inherit a
    -- floating-point money column. Pence are integers and should stay integers.
    count(*)                                                    as txn_count,
    cast(sum(f.amount_gbp_minor) as bigint)                     as spend_gbp_minor,
    cast(sum(case when ctx.counts_towards_spend then f.amount_gbp_minor else 0 end) as bigint)
                                                                as net_spend_gbp_minor,
    cast(sum(case when ctx.is_card_not_present then f.amount_gbp_minor else 0 end) as bigint)
                                                                as online_spend_gbp_minor,
    avg(f.dq_score)                                     as avg_dq_score
from {{ ref('fct_card_transaction') }} as f
join {{ ref('dim_date') }}         as d   on d.date_key = f.posted_date_key
join {{ ref('dim_txn_context') }}  as ctx on ctx.txn_context_key = f.txn_context_key
left join {{ ref('dim_merchant') }} as m  on m.merchant_key = f.merchant_key
where ctx.is_settled
group by 1, 2, 3
