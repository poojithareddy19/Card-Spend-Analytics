-- Rule evaluation, kept as boolean columns on the row rather than a quarantine table.
--
-- This project is about modelling and query performance, so bad rows stay visible and carry a score
-- instead of being routed elsewhere. Each flag names the defect it detects, so the scorecard can be
-- reconciled against the generator's manifest exactly.
with txns as (
    select * from {{ ref('int_transactions_deduped') }}
),
cards as (
    select card_id from {{ ref('stg_cards') }}
),
-- D07 is a minor/major unit slip: an amount entered in pounds and stored as if it were pence, so
-- the row is exactly 100x too large. A flat threshold cannot catch it, because £20 x 100 is £2,000
-- and £2,000 is an ordinary electronics purchase. The only signal in the row itself is that the
-- amount is wildly out of line *for that merchant category*, so the check is against the category's
-- own median rather than against a number somebody picked.
--
-- This control is deliberately partial and its recall is measured, not assumed:
-- docs/product/04_incident_rca.md carries the recall and false-positive numbers and explains why
-- the control that actually protects the ledger is a settlement reconciliation, not a row check.
mcc_medians as (
    select mcc, median(abs(amount_minor)) as median_amount_minor
    from txns
    where txn_type = 'purchase' and mcc between 1000 and 9999
    group by 1
),
flagged as (
    select
        txns.*,
        -- D01 completeness: a purchase must name a merchant
        (txns.txn_type = 'purchase' and txns.merchant_id is null)            as dq_missing_merchant,
        -- D02 uniqueness: recorded upstream by the de-duplication step
        txns.was_duplicated                                                   as dq_duplicated,
        -- D03 consistency: the card must exist in the cards extract
        (cards.card_id is null)                                               as dq_orphan_card,
        -- D04 validity: a purchase moves money away from the customer
        (txns.txn_type = 'purchase' and txns.amount_minor < 0)                as dq_negative_purchase,
        -- D05 validity: ISO 18245 codes are four digits
        (txns.mcc < 1000 or txns.mcc > 9999)                                  as dq_bad_mcc,
        -- D06 timeliness: posting more than two days after authorisation
        (date_diff('day', txns.auth_date, txns.posted_date) > 2)              as dq_late_posting,
        -- D07 accuracy: 40x the median basket for the merchant's own category
        (abs(txns.amount_minor) > 40 * coalesce(mcc_medians.median_amount_minor, 50000))
                                                                              as dq_implausible_amount
    from txns
    left join cards on cards.card_id = txns.card_id
    left join mcc_medians on mcc_medians.mcc = txns.mcc
)
select
    *,
    date_diff('day', auth_date, posted_date) as posting_lag_days,
    greatest(
        0,
        100
        - 20 * cast(dq_missing_merchant as int)
        - 20 * cast(dq_orphan_card as int)
        - 20 * cast(dq_negative_purchase as int)
        - 20 * cast(dq_implausible_amount as int)
        -  5 * cast(dq_bad_mcc as int)
        -  5 * cast(dq_late_posting as int)
        -  5 * cast(dq_duplicated as int)
    ) as dq_score
from flagged
