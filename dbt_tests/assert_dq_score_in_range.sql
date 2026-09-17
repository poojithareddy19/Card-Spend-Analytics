-- A score outside 0 to 100 means the penalty arithmetic drifted from the rule set.
select transaction_id, dq_score
from {{ ref('fct_card_transaction') }}
where dq_score < 0 or dq_score > 100
