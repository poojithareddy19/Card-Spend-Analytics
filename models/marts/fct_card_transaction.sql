-- The one fact. Grain: one card transaction, after de-duplication.
--
-- Two design points worth reading the code for:
--
-- 1. The SCD2 keys are resolved **here, at load**, not at query time. A query-time as-of join
--    against two Type 2 dimensions is the second most expensive thing in this schema after the fact
--    scan itself, and every consumer would pay it on every query.
-- 2. `amount_gbp_minor` is stored, not computed. Every report converts; computing it per query means
--    the as-of FX join runs hundreds of times a day instead of once per row per load.
with txns as (
    select * from {{ ref('int_transactions_scored') }}
),
fx as (
    select * from {{ ref('int_fx_rates_asof') }}
),
cust_version as (
    -- The customer as they were on the posting date, not as they are today.
    select customer_key, customer_id, valid_from, valid_to
    from {{ ref('dim_customer') }}
),
acct_version as (
    select account_key, account_id, customer_id, valid_from, valid_to
    from {{ ref('dim_account') }}
)
select
    txns.transaction_id,
    cast(strftime(txns.posted_date, '%Y%m%d') as integer)   as posted_date_key,
    cast(strftime(txns.auth_date,   '%Y%m%d') as integer)   as auth_date_key,
    txns.posted_date,
    txns.auth_date,
    txns.txn_timestamp,

    coalesce(cust_version.customer_key, 'UNKNOWN')          as customer_key,
    coalesce(acct_version.account_key,  'UNKNOWN')          as account_key,
    coalesce(acct_version.customer_id,  'UNKNOWN')          as customer_id,
    case when txns.dq_orphan_card then 'UNKNOWN' else txns.card_id end as card_key,
    coalesce(txns.merchant_id, 'UNKNOWN')                   as merchant_key,
    ctx.txn_context_key,
    txns.currency                                           as currency_key,
    txns.mcc,

    txns.amount_minor,
    -- Rounded to whole pence at load so every downstream sum of this column agrees to the penny.
    cast(round(txns.amount_minor * fx.rate_to_gbp) as bigint) as amount_gbp_minor,
    fx.rate_to_gbp                                           as fx_rate_to_gbp,
    fx.is_carried_forward                                    as fx_rate_carried_forward,
    txns.posting_lag_days,
    txns.dq_score,

    txns.dq_missing_merchant,
    txns.dq_duplicated,
    txns.dq_orphan_card,
    txns.dq_negative_purchase,
    txns.dq_bad_mcc,
    txns.dq_late_posting,
    txns.dq_implausible_amount,
    txns.feed_version
from txns
left join fx
    on fx.rate_date = txns.posted_date
   and fx.currency  = txns.currency
left join acct_version
    on acct_version.account_id = txns.account_id
   and txns.txn_timestamp >= acct_version.valid_from
   and txns.txn_timestamp <  acct_version.valid_to
left join cust_version
    on cust_version.customer_id = acct_version.customer_id
   and txns.txn_timestamp >= cust_version.valid_from
   and txns.txn_timestamp <  cust_version.valid_to
left join {{ ref('dim_txn_context') }} as ctx
    on  ctx.txn_type = txns.txn_type
    and ctx.channel  = txns.channel
    and ctx.status   = txns.status
