-- Junk dimension: txn_type x channel x status.
--
-- Roughly 80 rows standing in for three text columns on a fact of hundreds of millions. The width
-- saved on the fact is the entire justification, and day 5 quotes the difference.
select
    row_number() over (order by txn_type, channel, status) as txn_context_key,
    txn_type,
    channel,
    status,
    status = 'settled'                                     as is_settled,
    channel in ('ecommerce', 'mail_order')                 as is_card_not_present,
    txn_type in ('purchase', 'atm_withdrawal')             as counts_towards_spend
from (
    select distinct txn_type, channel, status
    from {{ ref('int_transactions_scored') }}
)
