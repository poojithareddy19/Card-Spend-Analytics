-- A GBP transaction must convert to itself. If this fails the as-of FX join picked a wrong row.
select transaction_id, amount_minor, amount_gbp_minor, fx_rate_to_gbp
from {{ ref('fct_card_transaction') }}
where currency_key = 'GBP'
  and amount_minor != amount_gbp_minor
