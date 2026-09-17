-- Type 1. Cards are reissued rather than amended, so history arrives as new rows anyway.
select
    card_id                 as card_key,
    card_id,
    account_id,
    card_type,
    network,
    issued_on,
    expires_on,
    status
from {{ ref('stg_cards') }}

union all

-- D03 points a small share of transactions at a card the cards extract has never heard of. Those
-- rows keep their measures and land on the UNKNOWN member, flagged, rather than disappearing into
-- a null key where nobody would ever count them.
select 'UNKNOWN', 'UNKNOWN', 'UNKNOWN', 'unknown', 'unknown',
       date '1900-01-01', date '9999-12-31', 'unknown'
