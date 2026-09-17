-- SCD Type 2, built from the dbt snapshot rather than from the current-state feed.
--
-- Segment and KYC status both change and both matter historically: a report of premier-segment spend
-- in March must use the segment the customer held in March, not the one they hold today. The
-- snapshot is what makes that possible; this model just gives it business-friendly column names.
--
-- One more thing the snapshot cannot know: a version captured today did not *begin* today. dbt
-- stamps the first version's valid_from with the run time, which would leave every transaction
-- older than the first dbt run with no matching version at all. The first version is therefore
-- backdated to the customer's own start date. Later versions keep the snapshot's timestamps,
-- because those are real observations of a change.
with versioned as (
    select
        *,
        row_number() over (partition by customer_id order by dbt_valid_from) as version_number
    from {{ ref('customers_snapshot') }}
)
select
    md5(customer_id || '|' || cast(dbt_valid_from as varchar))        as customer_key,
    customer_id,
    segment,
    kyc_status,
    kyc_risk_band,
    marketing_consent,
    onboarded_on,
    city,
    country,
    -- PII lives here, masked at the serving boundary rather than dropped, so an authorised reader
    -- can still resolve a customer during an investigation.
    first_name,
    last_name,
    email,
    case
        when version_number = 1
        then least(cast(onboarded_at as timestamp), cast(dbt_valid_from as timestamp))
        else cast(dbt_valid_from as timestamp)
    end                                                              as valid_from,
    cast(coalesce(dbt_valid_to, timestamp '9999-12-31 00:00:00') as timestamp) as valid_to,
    dbt_valid_to is null                                             as is_current,
    version_number
from versioned

union all

-- The UNKNOWN member. Standard practice, and the reason the fact can use an inner-join-shaped
-- relationship test: an unmatched key lands on a real dimension row rather than on a null.
select 'UNKNOWN', 'UNKNOWN', 'unknown', 'unknown', 'unknown', false,
       date '1900-01-01', 'unknown', 'unknown', 'unknown', 'unknown', 'unknown',
       timestamp '1900-01-01 00:00:00', timestamp '9999-12-31 00:00:00', true, 1
