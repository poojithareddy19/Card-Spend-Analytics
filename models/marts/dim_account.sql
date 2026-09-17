-- First version backdated to the account's own opening date, for the reason set out in
-- dim_customer.sql: a snapshot captured today does not mean the account began today.
with versioned as (
    select
        *,
        row_number() over (partition by account_id order by dbt_valid_from) as version_number
    from {{ ref('accounts_snapshot') }}
)
select
    md5(account_id || '|' || cast(dbt_valid_from as varchar))        as account_key,
    account_id,
    customer_id,
    product_code,
    currency,
    status,
    opened_on,
    closed_on,
    overdraft_limit_minor,
    -- D10: an open account should not carry a closed date. Surfaced, not silently corrected.
    (status = 'open' and closed_on is not null)                      as dq_open_with_closed_date,
    case
        when version_number = 1
        then least(cast(opened_on as timestamp), cast(dbt_valid_from as timestamp))
        else cast(dbt_valid_from as timestamp)
    end                                                              as valid_from,
    cast(coalesce(dbt_valid_to, timestamp '9999-12-31 00:00:00') as timestamp) as valid_to,
    dbt_valid_to is null                                             as is_current,
    version_number
from versioned

union all

select 'UNKNOWN', 'UNKNOWN', 'UNKNOWN', 'unknown', 'unknown', 'unknown',
       date '1900-01-01', cast(null as date), 0, false,
       timestamp '1900-01-01 00:00:00', timestamp '9999-12-31 00:00:00', true, 1
