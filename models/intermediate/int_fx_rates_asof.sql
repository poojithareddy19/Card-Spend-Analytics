-- Defect D11: whole rate days go missing.
--
-- A plain equi-join on (posted_date, currency) drops every transaction on a missing day, which is
-- the kind of loss that reconciles to nothing and gets noticed a quarter later. This builds a dense
-- daily spine per currency and carries the last known rate forward across the gaps, which is what
-- the treasury policy says to do.
with bounds as (
    select min(posted_date) as from_date, max(posted_date) as to_date
    from {{ ref('int_transactions_deduped') }}
),
spine as (
    select cast(unnest(generate_series(from_date, to_date, interval 1 day)) as date) as rate_date
    from bounds
),
currencies as (
    select distinct currency from {{ ref('stg_fx_rates') }}
),
grid as (
    select spine.rate_date, currencies.currency
    from spine cross join currencies
),
joined as (
    select
        grid.rate_date,
        grid.currency,
        raw.rate_to_gbp,
        raw.rate_to_gbp is null as is_carried_forward
    from grid
    left join {{ ref('stg_fx_rates') }} as raw
        on raw.rate_date = grid.rate_date and raw.currency = grid.currency
)
select
    rate_date,
    currency,
    coalesce(
        rate_to_gbp,
        last_value(rate_to_gbp ignore nulls) over (
            partition by currency order by rate_date
            rows between unbounded preceding and current row
        )
    ) as rate_to_gbp,
    is_carried_forward
from joined
