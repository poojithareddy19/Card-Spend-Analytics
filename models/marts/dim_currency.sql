select
    currency        as currency_key,
    currency        as currency_code,
    currency = 'GBP' as is_reporting_currency
from (select distinct currency from {{ ref('int_fx_rates_asof') }})
