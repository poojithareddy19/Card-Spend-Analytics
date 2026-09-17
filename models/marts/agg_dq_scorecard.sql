-- Pass rate per rule per day, rolled up by DAMA dimension.
-- Reconciles directly against the generator's defect manifest, which is what makes it testable.
with flags as (
    select posted_date, 'D01' as defect_code, 'completeness' as dimension, dq_missing_merchant   as failed from {{ ref('fct_card_transaction') }}
    union all
    select posted_date, 'D02', 'uniqueness',   dq_duplicated        from {{ ref('fct_card_transaction') }}
    union all
    select posted_date, 'D03', 'consistency',  dq_orphan_card       from {{ ref('fct_card_transaction') }}
    union all
    select posted_date, 'D04', 'validity',     dq_negative_purchase from {{ ref('fct_card_transaction') }}
    union all
    select posted_date, 'D05', 'validity',     dq_bad_mcc           from {{ ref('fct_card_transaction') }}
    union all
    select posted_date, 'D06', 'timeliness',   dq_late_posting      from {{ ref('fct_card_transaction') }}
    union all
    select posted_date, 'D07', 'accuracy',     dq_implausible_amount from {{ ref('fct_card_transaction') }}
)
select
    posted_date,
    defect_code,
    dimension,
    count(*)                                              as rows_checked,
    sum(cast(failed as int))                              as rows_failed,
    1.0 - sum(cast(failed as int)) / cast(count(*) as double) as pass_rate
from flags
group by 1, 2, 3
