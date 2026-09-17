-- Defect D02: the processor occasionally emits the same row twice.
--
-- The de-duplication rule is a business decision, not a technical one, so it is written down here:
-- keep exactly one row per transaction_id, and when copies disagree keep the one with the later
-- txn_timestamp, since a re-emission carries the processor's latest view. The duplicates are
-- counted rather than dropped silently, because "we removed some rows" is not an acceptable answer
-- when finance asks why the count moved.
with ranked as (
    select
        *,
        row_number() over (
            partition by transaction_id
            order by txn_timestamp desc, feed_version desc
        ) as _copy_number,
        count(*) over (partition by transaction_id) as _copies
    from {{ ref('stg_card_transactions') }}
)
select
    * exclude (_copy_number, _copies),
    _copies > 1 as was_duplicated
from ranked
where _copy_number = 1
