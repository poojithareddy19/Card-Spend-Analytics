-- Type 1 with the vendor key and the acquirer key side by side from the first version.
--
-- The acquirer key is null today because only the enrichment vendor supplies merchants. It exists
-- anyway: the bus matrix flags this as the one dimension that would not conform if a second process
-- sourced merchants from the acquirer, and adding the column later would mean a migration.
select
    merchant_id             as merchant_key,
    merchant_id             as vendor_merchant_id,
    cast(null as varchar)   as acquirer_merchant_id,
    merchant_name,
    mcc,
    category,
    country,
    is_online,
    first_seen_on
from {{ ref('stg_merchants') }}

union all

-- ATM withdrawals and fees legitimately have no merchant, and D01 nulls a few that should. Both
-- land here rather than on a null key, which keeps every merchant join an inner join.
select 'UNKNOWN', 'UNKNOWN', cast(null as varchar), 'No merchant', 0, 'unknown', 'ZZ', false,
       date '1900-01-01'
