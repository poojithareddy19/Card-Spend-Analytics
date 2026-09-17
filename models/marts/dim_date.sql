-- Generated, never sourced. A date dimension sourced from a system is a date dimension with gaps.
with bounds as (
    select
        date_trunc('year', min(posted_date))                    as from_date,
        date_trunc('year', max(posted_date)) + interval 1 year  as to_date
    from {{ ref('int_transactions_scored') }}
),
spine as (
    select cast(unnest(generate_series(from_date, to_date, interval 1 day)) as date) as calendar_date
    from bounds
)
select
    cast(strftime(calendar_date, '%Y%m%d') as integer)  as date_key,
    calendar_date,
    extract(year from calendar_date)                    as calendar_year,
    extract(month from calendar_date)                   as calendar_month,
    strftime(calendar_date, '%Y-%m')                    as calendar_month_key,
    extract(quarter from calendar_date)                 as calendar_quarter,
    extract(day from calendar_date)                     as day_of_month,
    extract(isodow from calendar_date)                  as iso_day_of_week,
    strftime(calendar_date, '%A')                       as day_name,
    extract(week from calendar_date)                    as iso_week,
    extract(isodow from calendar_date) in (6, 7)        as is_weekend,
    -- UK fiscal year starts 6 April. Finance closes on this, so it belongs in the dimension.
    case
        when calendar_date >= make_date(cast(extract(year from calendar_date) as int), 4, 6)
        then cast(extract(year from calendar_date) as int)
        else cast(extract(year from calendar_date) as int) - 1
    end                                                 as uk_fiscal_year
from spine
