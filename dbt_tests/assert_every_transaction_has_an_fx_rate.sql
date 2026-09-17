-- D11 puts whole rate days missing. A plain equi-join would silently drop those transactions;
-- the carried-forward spine must mean nothing is left without a rate.
select transaction_id, posted_date, currency_key
from {{ ref('fct_card_transaction') }}
where fx_rate_to_gbp is null
